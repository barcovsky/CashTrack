import logging
import pytz
import gspread
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message
from aiogram.filters import Command
from aiogram import Router
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Глобальная переменная для хранения начального лимита на месяц
initial_budget = None

fake_date = None  # Переменная для фейковой даты (для тестов)

# Токен бота
BOT_TOKEN = "7607235027:AAGFaSH5YY_t_SIC0hqp9t9MCT8A75EL1MA"

# Настройки Google Sheets
SPREADSHEET_NAME = "1dAazjsbeL49vn0SwME4Avxtm4CjYqCWcc4TKfRmPh9Q"
CREDENTIALS_FILE = "nodal-alcove-351723-22e4e5a3bd10.json"

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

dp.include_router(router)  # Добавляем роутер в диспетчер

# Подключение к Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.file"]
creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SPREADSHEET_NAME).sheet1  # Открываем по ID

# Проверяем, есть ли заголовки, и создаём их, если их нет
headers = sheet.row_values(1)
if not headers or headers[0] != "Статья расходов":
    sheet.insert_row(["Статья расходов", "Стоимость, AMD"], index=1)

cached_budget = None  # Переменная для хранения лимита
last_budget_update = None  # Время последнего обновления

def get_daily_budget_limit():
    global cached_budget, last_budget_update
    try:
        # Используем фейковую дату, если она установлена
        current_date = datetime.strptime(fake_date, "%Y-%m-%d") if fake_date else datetime.now()
        current_date_str = current_date.strftime("%Y-%m-%d")
        current_month = current_date.strftime("%Y-%m")

        # Если лимит уже загружен сегодня, используем кэш
        if cached_budget is not None and last_budget_update == current_date_str:
            return cached_budget

        # Запрашиваем данные из Google Sheets
        values = sheet.get_all_values()
        for row in values:
            if row[0] == "Daily budget limit, AMD":
                raw_value = row[1].strip().replace(" ", "").replace(",", ".")
                budget = float(raw_value)

                # Пересчитываем бюджет с учётом фейковой даты
                new_budget = recalculate_daily_budget(budget)
                cached_budget = new_budget
                last_budget_update = current_date_str

                logging.info(f"Обновлённый дневной лимит: {cached_budget}")
                return cached_budget

    except Exception as e:
        logging.error(f"Ошибка при получении бюджета: {e}")
    return None


def create_new_month_sheet():
    try:
        # Текущая дата или фейковая дата
        today = datetime.strptime(fake_date, "%Y-%m-%d") if fake_date else datetime.now()
        new_sheet_title = today.strftime("%Y-%m")

        # Проверяем, существует ли уже лист на новый месяц
        if new_sheet_title in [sheet.title for sheet in client.open_by_key(SPREADSHEET_NAME).worksheets()]:
            logging.info(f"Лист {new_sheet_title} уже существует.")
            return

        # Создаём новый лист
        new_sheet = client.open_by_key(SPREADSHEET_NAME).add_worksheet(title=new_sheet_title, rows="100", cols="20")

        # Копируем данные из основного листа до раздела "Daily expenses"
        source_data = sheet.get_all_values()
        for i, row in enumerate(source_data):
            if "Daily expenses" in row:
                end_index = i
                break

        # Вставляем скопированные данные в новый лист
        new_sheet.update("A1", source_data[:end_index + 1])
        logging.info(f"Создан новый лист: {new_sheet_title} с копией данных до 'Daily expenses'")

    except Exception as e:
        logging.error(f"Ошибка при создании нового листа: {e}")


@router.message()
async def add_expense(message: Message):
    global cached_budget
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

        amount = float(amount)
        date_today = datetime.now().strftime("%Y-%m-%d")

        # Запись в Google Таблицу
        sheet.append_row([category, amount, date_today], table_range="A20:C")

        # Сохраняем исходный дневной лимит ДО пересчёта
        original_budget = cached_budget if cached_budget is not None else get_daily_budget_limit()

        # Пересчитываем дневной бюджет после новой траты
        cached_budget = recalculate_daily_budget(get_daily_budget_limit())

        # Считаем траты за сегодня
        total_spent = get_today_expenses()

        # Корректный расчёт процента от ИСХОДНОГО дневного лимита
        percent_spent = (total_spent / original_budget) * 100 if original_budget > 0 else 100

        await message.answer(f"Записано: {category} - {amount} AMD\nПотрачено {percent_spent:.2f}% от суммы сегодняшнего лимита")
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await message.answer("Произошла ошибка. Проверь формат данных.")




# Функция для подсчёта оставшихся дней в месяце
def get_remaining_days():
    today = datetime.strptime(fake_date, "%Y-%m-%d") if fake_date else datetime.now()  # Используем fake_date
    last_day = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return (last_day - today).days + 1  # Включаем сегодняшний день


# Функция для подсчёта трат за сегодня
def get_today_expenses():
    try:
        values = sheet.get_all_values()
        total_spent = 0
        today = fake_date if fake_date else datetime.now().strftime("%Y-%m-%d")  # Используем fake_date

        for row in values:
            if len(row) >= 3:
                expense_date = row[2].strip()
                if expense_date == today:
                    try:
                        expense_amount = float(row[1].strip().replace(",", "").replace(" ", ""))
                        total_spent += expense_amount
                    except ValueError:
                        continue

        return total_spent
    except Exception as e:
        logging.error(f"Ошибка при подсчёте трат: {e}")
    return 0


def recalculate_daily_budget(initial_budget):
    try:
        # Используем фейковую дату, если она установлена
        current_date = datetime.strptime(fake_date, "%Y-%m-%d") if fake_date else datetime.now()
        remaining_days = get_remaining_days()
        
        # Фиксируем общий месячный бюджет из ячейки B17
        fixed_monthly_budget = get_monthly_budget()
        
        # Получаем все траты за текущий месяц
        values = sheet.get_all_values()
        total_budget_spent = 0
        for row in values:
            if len(row) >= 3:
                try:
                    expense_date = row[2].strip()
                    expense_amount = float(row[1].strip().replace(",", "").replace(" ", ""))
                    # Учитываем только траты за текущий месяц на основе фейковой даты
                    if expense_date[:7] == current_date.strftime("%Y-%m"):
                        total_budget_spent += expense_amount
                except ValueError:
                    continue

        # Оставшийся бюджет за месяц
        remaining_budget = fixed_monthly_budget - total_budget_spent

        # Если перерасход, уменьшаем будущие лимиты
        if total_budget_spent > fixed_monthly_budget:
            logging.info(f"Перерасход! Бюджет в минусе: {total_budget_spent - fixed_monthly_budget} AMD")
            remaining_budget = 0

        logging.info(f"=== Перерасчёт дневного лимита ===")
        logging.info(f"Фиксированный месячный бюджет: {fixed_monthly_budget}")
        logging.info(f"Фактически потрачено за месяц: {total_budget_spent}")
        logging.info(f"Оставшийся бюджет: {remaining_budget}")
        logging.info(f"Оставшиеся дни в месяце: {remaining_days}")

        # Новый дневной лимит
        new_budget = max(remaining_budget / remaining_days, 0) if remaining_days > 0 else 0

        logging.info(f"Пересчитанный дневной лимит: {new_budget}")
        logging.info(f"===================================")

        return round(new_budget, 2)

    except Exception as e:
        logging.error(f"Ошибка при перерасчёте бюджета: {e}")
        return initial_budget








def get_daily_budget_limit():
    global cached_budget, last_budget_update
    try:
        current_date = fake_date if fake_date else datetime.now().strftime("%Y-%m-%d")
        current_month = fake_date[:7] if fake_date else datetime.now().strftime("%Y-%m")

        # Проверяем смену месяца и создаём новый лист при необходимости
        if last_budget_update and last_budget_update[:7] != current_month:
            create_new_month_sheet()

        # Если лимит уже загружен сегодня, используем кэш
        if cached_budget is not None and last_budget_update == current_date:
            return cached_budget  

        # Запрашиваем данные из Google Sheets
        values = sheet.get_all_values()
        for row in values:
            if row[0] == "Daily budget limit, AMD":
                raw_value = row[1].strip().replace(" ", "").replace(",", ".")
                budget = float(raw_value)

                # Пересчитываем бюджет только если меняется день
                new_budget = recalculate_daily_budget(budget) if last_budget_update != current_date else budget
                cached_budget = new_budget
                last_budget_update = current_date

                logging.info(f"Обновлённый дневной лимит: {cached_budget}")
                return cached_budget

    except Exception as e:
        logging.error(f"Ошибка при получении бюджета: {e}")
    return None


@router.message(Command("budget_default"))
async def reset_budget(message: Message):
    global cached_budget, last_budget_update
    try:
        # Сбрасываем кэшированный дневной лимит
        cached_budget = None
        last_budget_update = None

        # Получаем новое значение из Google Sheets БЕЗ перерасчёта!
        values = sheet.get_all_values()
        for row in values:
            if row[0] == "Daily budget limit, AMD":
                raw_value = row[1].strip().replace(" ", "").replace(",", ".")
                cached_budget = float(raw_value)  # Просто берём исходный лимит
                last_budget_update = datetime.now()
                break  # Выходим из цикла, чтобы не делать лишние запросы
        
        if cached_budget is None:
            await message.answer("Не удалось сбросить бюджет. Проверь настройки.")
        else:
            await message.answer(f"Бюджет сброшен!\nНовый дневной лимит: {cached_budget:.2f} AMD")

    except Exception as e:
        logging.error(f"Ошибка при сбросе бюджета: {e}")
        await message.answer("Произошла ошибка при сбросе бюджета.")


@router.message(Command("budget_now"))
async def get_current_budget(message: Message):
    try:
        daily_budget = get_daily_budget_limit()
        if daily_budget is None:
            await message.answer("Не удалось получить текущий дневной лимит.")
        else:
            await message.answer(f"Текущий дневной лимит: {daily_budget:.2f} AMD")
    except Exception as e:
        logging.error(f"Ошибка при получении текущего бюджета: {e}")
        await message.answer("Произошла ошибка при получении бюджета.")


@router.message(Command("budget_left"))
async def get_budget_left(message: Message):
    try:
        daily_budget = get_daily_budget_limit()
        total_spent_today = get_today_expenses()

        budget_left = max(daily_budget - total_spent_today, 0)

        await message.answer(f"Оставшийся бюджет на сегодня: {budget_left:.2f} AMD")
    except Exception as e:
        logging.error(f"Ошибка при получении оставшегося бюджета: {e}")
        await message.answer("Произошла ошибка при получении оставшегося бюджета.")


@router.message(Command("set_date"))
async def set_fake_date(message: Message):
    global fake_date, cached_budget, last_budget_update
    try:
        parts = message.text.strip().split()
        if len(parts) != 2:
            await message.answer("Используй формат: /set_date YYYY-MM-DD или /set_date reset")
            return
        
        new_date = parts[1]

        if new_date.lower() == "reset":  # Возвращаем реальную дату
            fake_date = None
            cached_budget = None
            last_budget_update = None
            await message.answer("Дата сброшена! Бот снова использует реальное время.")
            return

        # Проверяем формат даты
        datetime.strptime(new_date, "%Y-%m-%d")
        fake_date = new_date  # Устанавливаем фейковую дату

        # Сбрасываем кэш и пересчитываем лимит
        cached_budget = None
        last_budget_update = None

        # Пересчитываем дневной лимит на основе фейковой даты
        new_budget = get_daily_budget_limit()

        if new_budget is not None:
            await message.answer(f"Дата изменена! Теперь бот считает, что сегодня: {fake_date}\nНовый дневной лимит: {new_budget:.2f} AMD")
        else:
            await message.answer("Не удалось пересчитать бюджет для новой даты.")

    except ValueError:
        await message.answer("Некорректный формат даты. Используй: /set_date YYYY-MM-DD или /set_date reset")
    except Exception as e:
        logging.error(f"Ошибка при установке фейковой даты: {e}")
        await message.answer("Произошла ошибка при смене даты.")


def get_monthly_budget():
    try:
        # Получаем значение из ячейки B17 ("Balance, AMD")
        value = sheet.acell("B17").value
        return float(value.strip().replace(",", "").replace(" ", ""))
    except Exception as e:
        logging.error(f"Ошибка при получении месячного бюджета: {e}")
        return 0  # Если ошибка, возвращаем 0

@router.message(Command("stats"))
async def get_monthly_stats(message: Message):
    try:
        # Используем фейковую дату, если она установлена
        current_date = datetime.strptime(fake_date, "%Y-%m-%d") if fake_date else datetime.now()
        current_month = current_date.strftime("%Y-%m")

        # Получаем данные из Google Sheets начиная с A21
        values = sheet.get("A21:C1000")  # Смотрим до тысячи строк на всякий случай

        # Считаем траты по категориям
        category_totals = {}
        total_spent = 0

        for row in values:
            # Пропускаем пустые строки
            if len(row) < 3 or not row[1].strip().replace(",", "").replace(" ", "").isdigit():
                continue

            try:
                category = row[0].strip()
                amount = float(row[1].strip().replace(",", "").replace(" ", ""))
                date = row[2].strip()

                # Учитываем только траты за текущий месяц
                if date.startswith(current_month):
                    if category in category_totals:
                        category_totals[category] += amount
                    else:
                        category_totals[category] = amount
                    total_spent += amount
            except ValueError:
                continue

        # Формируем сообщение со статистикой
        if category_totals:
            stats_message = "📊 Статистика за месяц:\n"
            for category, total in sorted(category_totals.items(), key=lambda x: x[1], reverse=True):
                stats_message += f"- {category}: {total:.2f} AMD\n"
            stats_message += f"Всего: {total_spent:.2f} AMD"
        else:
            stats_message = "📊 За этот месяц пока нет трат."

        await message.answer(stats_message)
    except Exception as e:
        logging.error(f"Ошибка при получении статистики: {e}")
        await message.answer("Произошла ошибка при получении статистики.")


import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram.types import Message

# 🟢 Устанавливаем часовой пояс для Еревана через pytz
timezone = pytz.timezone('Asia/Yerevan')

# 🟢 Создаём планировщик задач с использованием pytz
scheduler = AsyncIOScheduler(timezone=timezone)

# 🔄 Укажите здесь ваш реальный chat_id
YOUR_CHAT_ID = 151719897  # Ваш реальный chat_id

# 🟢 Функция для отправки статистики автоматически
async def send_weekly_stats():
    try:
        # Используем фиктивное сообщение для вызова get_monthly_stats
        message = Message(chat={'id': YOUR_CHAT_ID})
        await get_monthly_stats(message)
    except Exception as e:
        logging.error(f"Ошибка при отправке статистики: {e}")

# 🟢 Планируем выполнение задачи каждый понедельник в 14:00
scheduler.add_job(send_weekly_stats, CronTrigger(day_of_week='mon', hour=14, minute=0))


async def main():

    dp.message.register(get_monthly_stats, Command("stats"))

    dp.message.register(set_fake_date, Command("set_date"))
    dp.message.register(get_budget_left, Command("budget_left"))
    dp.message.register(reset_budget, Command("budget_default"))  # Сброс бюджета
    dp.message.register(get_current_budget, Command("budget_now"))  # Просмотр текущего бюджета
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


   # 🟢 Добавляем планировщик задач в main
    scheduler.start()



if __name__ == "__main__":
    asyncio.run(main())