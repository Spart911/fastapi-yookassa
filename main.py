import os
import uuid
from typing import List
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime
import yookassa
from telegram import Bot
from dotenv import load_dotenv
from contextlib import asynccontextmanager

# Загрузка переменных окружения
load_dotenv()

# Настройка ЮKassa
yookassa.Configuration.account_id = os.getenv('YOOKASSA_SHOP_ID')
yookassa.Configuration.secret_key = os.getenv('YOOKASSA_API_KEY')

# Настройка Telegram
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

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

class OrderCreate(BaseModel):
    email: EmailStr
    phone: str
    address: str
    delivery_time: str
    order_time: str
    items: List[OrderItem]
    total_amount: float

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
    telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)
    yield
    # Очистка при завершении
    if telegram_bot:
        await telegram_bot.close()

app = FastAPI(lifespan=lifespan)

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/order")
async def create_order(order: OrderCreate, db: Session = Depends(get_db)):
    # Создание заказа в базе данных
    db_order = Order(
        email=order.email,
        phone=order.phone,
        address=order.address,
        delivery_time=order.delivery_time,
        order_time=order.order_time,
        items=order.items,
        total_amount=order.total_amount
    )
    db.add(db_order)
    db.commit()
    db.refresh(db_order)

    # Создание платежа в ЮKassa
    idempotence_key = str(uuid.uuid4())
    payment = yookassa.Payment.create({
        "amount": {
            "value": str(order.total_amount),
            "currency": "RUB"
        },
        "confirmation": {
            "type": "redirect",
            "return_url": "https://myshop.com/payment_success"
        },
        "capture": True,
        "description": f"Заказ №{db_order.id}"
    }, idempotence_key)

    # Обновление заказа с ID платежа
    db_order.payment_id = payment.id
    db.commit()

    return {
        "order_id": db_order.id,
        "payment_url": payment.confirmation.confirmation_url
    }

@app.post("/payment_success")
async def payment_success(payment_id: str, db: Session = Depends(get_db)):
    # Получение информации о платеже
    payment = yookassa.Payment.find_one(payment_id)
    
    if payment.status == "succeeded":
        # Обновление статуса заказа
        order = db.query(Order).filter(Order.payment_id == payment_id).first()
        if order:
            order.status = "paid"
            db.commit()

            # Отправка уведомления в Telegram
            message = f"Оплачен заказ №{order.id}, сумма {order.total_amount} руб."
            await telegram_bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)

            return {"status": "success"}
    
    raise HTTPException(status_code=400, detail="Payment not successful") 