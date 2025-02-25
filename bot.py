import logging
import gspread
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message
from aiogram.filters import Command
from aiogram import Router
from oauth2client.service_account import ServiceAccountCredentials

# Токен бота (получите от @BotFather)
BOT_TOKEN = "7607235027:AAGFaSH5YY_t_SIC0hqp9t9MCT8A75EL1MA"

# Настройки Google Sheets
SPREADSHEET_NAME = "1dAazjsbeL49vn0SwME4Avxtm4CjYqCWcc4TKfRmPh9Q"  # Используем ID таблицы
import json
import os

credentials_json = json.loads(os.getenv("GOOGLE_CREDENTIALS"))

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

dp.include_router(router)  # Добавляем роутер в диспетчер

# Подключение к Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.file"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials_json, scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SPREADSHEET_NAME).sheet1  # Открываем по ID

# Проверяем, есть ли заголовки, и создаём их, если их нет
headers = sheet.row_values(1)
if not headers or headers[0] != "Статья расходов":
    sheet.insert_row(["Статья расходов", "Стоимость, AMD"], index=1)

@router.message()
async def add_expense(message: Message):
    try:
        text = message.text.strip().split(",")
        if len(text) != 2:
            await message.answer("Введи траты в формате: категория, сумма. Например: еда, 1500")
            return

        category = text[0].strip()
        amount = text[1].strip()

        if not amount.replace(".", "").isdigit():
            await message.answer("Сумма должна быть числом. Например: еда, 1500")
            return

        # Запись в Google Таблицу
        sheet.append_row([category, amount])
        await message.answer(f"Записано: {category} - {amount} AMD")
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await message.answer("Произошла ошибка. Проверь формат данных.")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())