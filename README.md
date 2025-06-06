# FastAPI Order API с ЮKassa

API для обработки заказов с интеграцией ЮKassa и уведомлениями в Telegram.

## Установка

1. Клонируйте репозиторий
2. Создайте виртуальное окружение:
```bash
python -m venv venv
source venv/bin/activate  # для Linux/Mac
# или
venv\Scripts\activate  # для Windows
```

3. Установите зависимости:
```bash
pip install -r requirements.txt
```

4. Создайте файл .env со следующими переменными:
```
YOOKASSA_SHOP_ID=ваш_shop_id
YOOKASSA_API_KEY=ваш_api_key
TELEGRAM_BOT_TOKEN=токен_бота
TELEGRAM_CHAT_ID=id_чата
```

## Запуск

```bash
uvicorn main:app --reload
```

## API Endpoints

### POST /order

Создание нового заказа.

Пример запроса:
```json
{
    "email": "user@example.com",
    "phone": "+79001234567",
    "address": "ул. Примерная, д. 1",
    "delivery_time": "2024-03-20T15:00:00",
    "order_time": "2024-03-20T14:00:00",
    "items": [
        {
            "name": "Пицца Маргарита",
            "quantity": 2
        },
        {
            "name": "Кока-Кола",
            "quantity": 1
        }
    ],
    "total_amount": 1500.00
}
```

Ответ:
```json
{
    "order_id": 1,
    "payment_url": "https://yookassa.ru/payment/..."
}
```

## Деплой на Render

1. Создайте новый Web Service на Render
2. Подключите ваш GitHub репозиторий
3. Добавьте переменные окружения в настройках сервиса
4. Render автоматически определит Procfile и запустит приложение 