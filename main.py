import os
import uuid
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, JSON
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from datetime import datetime
import yookassa
from telegram import Bot
from dotenv import load_dotenv
from contextlib import asynccontextmanager
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import logging
import ipaddress

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Получение порта из переменной окружения
PORT = int(os.getenv("PORT", 10000))

# Настройка сессии requests для ЮKassa
session = requests.Session()
retry_strategy = Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=[500, 502, 503, 504]
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)

# Настройка ЮKassa
yookassa.Configuration.configure(
    account_id=os.getenv('YOOKASSA_SHOP_ID'),
    secret_key=os.getenv('YOOKASSA_API_KEY'),
    session=session
)

# Настройка Telegram
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# Список разрешенных IP-адресов ЮKassa
YOOKASSA_IPS = [
    ipaddress.ip_network('185.71.76.0/27'),
    ipaddress.ip_network('185.71.77.0/27'),
    ipaddress.ip_network('77.75.153.0/25'),
    ipaddress.ip_network('77.75.156.11/32'),
    ipaddress.ip_network('77.75.156.35/32'),
    ipaddress.ip_network('77.75.154.128/25'),
    ipaddress.ip_network('2a02:5180::/32')
]

# Настройка базы данных
SQLALCHEMY_DATABASE_URL = "sqlite:///./orders.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Модель заказа для базы данных
class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String)
    phone = Column(String)
    address = Column(String)
    delivery_time = Column(String)
    order_time = Column(String)
    items = Column(JSON)
    total_amount = Column(Float)
    status = Column(String, default="created")
    payment_id = Column(String, nullable=True)

Base.metadata.create_all(bind=engine)

# Pydantic модели
class OrderItem(BaseModel):
    name: str
    quantity: int

    def dict(self, *args, **kwargs):
        return {
            "name": self.name,
            "quantity": self.quantity
        }

class OrderCreate(BaseModel):
    email: EmailStr
    phone: str
    address: str
    delivery_time: str
    order_time: str
    items: List[OrderItem]
    total_amount: float

class YooKassaNotification(BaseModel):
    type: str
    event: str
    object: dict

# Dependency для получения сессии БД
async def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Инициализация Telegram бота
telegram_bot = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Инициализация при запуске
    global telegram_bot
    try:
        telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)
        logger.info("Telegram bot initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize Telegram bot: {e}")
        telegram_bot = None
    
    yield
    
    # Очистка при завершении
    if telegram_bot:
        try:
            await telegram_bot.close()
            logger.info("Telegram bot closed successfully")
        except Exception as e:
            logger.error(f"Error closing Telegram bot: {e}")

app = FastAPI(
    title="FastAPI Order API",
    description="API для обработки заказов с интеграцией ЮKassa",
    version="1.0.0",
    lifespan=lifespan
)

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def is_yookassa_ip(ip: str) -> bool:
    """Проверяет, принадлежит ли IP-адрес ЮKassa"""
    try:
        ip_obj = ipaddress.ip_address(ip)
        return any(ip_obj in network for network in YOOKASSA_IPS)
    except ValueError:
        return False

@app.get("/")
async def root():
    return {
        "status": "ok",
        "message": "API is running",
        "docs_url": "/docs",
        "redoc_url": "/redoc",
        "port": PORT
    }

@app.post("/order")
async def create_order(order: OrderCreate, db: Session = Depends(get_db)):
    try:
        # Преобразуем items в список словарей
        items_data = [item.dict() for item in order.items]
        
        # Создание заказа в базе данных
        db_order = Order(
            email=order.email,
            phone=order.phone,
            address=order.address,
            delivery_time=order.delivery_time,
            order_time=order.order_time,
            items=items_data,
            total_amount=order.total_amount
        )
        db.add(db_order)
        db.commit()
        db.refresh(db_order)

        # Создание платежа в ЮKassa
        idempotence_key = str(uuid.uuid4())
        payment_data = {
            "amount": {
                "value": str(order.total_amount),
                "currency": "RUB"
            },
            "confirmation": {
                "type": "embedded"  # Изменено на embedded для виджета
            },
            "capture": True,
            "description": f"Заказ №{db_order.id}"
        }

        payment = yookassa.Payment.create(payment_data, idempotence_key)

        # Обновление заказа с ID платежа
        db_order.payment_id = payment.id
        db.commit()

        return {
            "order_id": db_order.id,
            "confirmation_token": payment.confirmation.confirmation_token
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating order: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/webhook")
async def yookassa_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        # Проверка IP-адреса
        client_ip = request.client.host
        if not is_yookassa_ip(client_ip):
            logger.warning(f"Received webhook from unauthorized IP: {client_ip}")
            raise HTTPException(status_code=403, detail="Unauthorized IP")

        # Получение данных уведомления
        notification_data = await request.json()
        notification = YooKassaNotification(**notification_data)
        
        # Проверка типа уведомления
        if notification.type != "notification":
            raise HTTPException(status_code=400, detail="Invalid notification type")

        # Обработка различных событий
        if notification.event == "payment.succeeded":
            payment = notification.object
            order = db.query(Order).filter(Order.payment_id == payment["id"]).first()
            
            if order:
                # Проверка статуса платежа через API
                yookassa_payment = yookassa.Payment.find_one(payment["id"])
                if yookassa_payment.status == "succeeded":
                    order.status = "paid"
                    db.commit()

                    # Формируем подробное сообщение для Telegram
                    items_text = "\n".join([f"- {item['name']} x{item['quantity']}" for item in order.items])
                    message = (
                        f"✅ Оплачен заказ №{order.id}\n\n"
                        f"💰 Сумма: {order.total_amount} руб.\n"
                        f"📧 Email: {order.email}\n"
                        f"📱 Телефон: {order.phone}\n"
                        f"📍 Адрес: {order.address}\n"
                        f"🕒 Время доставки: {order.delivery_time}\n\n"
                        f"📋 Состав заказа:\n{items_text}"
                    )
                    
                    # Отправка уведомления в Telegram
                    if telegram_bot:
                        try:
                            await telegram_bot.send_message(
                                chat_id=TELEGRAM_CHAT_ID,
                                text=message,
                                parse_mode='HTML'
                            )
                            logger.info(f"Telegram notification sent for order {order.id}")
                        except Exception as e:
                            logger.error(f"Failed to send Telegram notification: {e}")

        elif notification.event == "payment.waiting_for_capture":
            logger.info(f"Payment {notification.object['id']} waiting for capture")
            
        elif notification.event == "payment.canceled":
            payment = notification.object
            order = db.query(Order).filter(Order.payment_id == payment["id"]).first()
            if order:
                order.status = "canceled"
                db.commit()
                logger.info(f"Order {order.id} payment canceled")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/order/{order_id}/status")
async def get_order_status(order_id: int, db: Session = Depends(get_db)):
    try:
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            raise HTTPException(status_code=404, detail="Заказ не найден")
        
        return {
            "order_id": order.id,
            "status": order.status,
            "payment_id": order.payment_id
        }
    except Exception as e:
        logger.error(f"Error getting order status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT) 