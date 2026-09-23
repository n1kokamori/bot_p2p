import aiohttp
import sqlite3
from datetime import datetime
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

BOT_TOKEN = "8987123999:AAEhVzV_0qzzyQ6sNr-iv8FYm6VFFceNV70"

# Состояния диалогов
BUY_RUB, BUY_RATE = 1, 2
SELL_RUB, SELL_RATE = 3, 4
CONFIRM_RESET = 5

MAIN_KEYBOARD = [
    ["🛒 Купить", "🏷 Продать"],
    ["📊 Баланс и статистика", "🗑 Сброс статистики"]
]

# ----------------- КУРС ОНЛАЙН ----------------- #
# ----------------- НАДЁЖНОЕ ПОЛУЧЕНИЕ КУРСА ОНЛАЙН ----------------- #
async def get_live_usdt_rate():
    """Получает рыночный курс USDT к RUB через KuCoin или резервный шлюз."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    timeout = aiohttp.ClientTimeout(total=4)

    # 1. Попытка через биржевой API KuCoin (пара USDT-RUB)
    url_kucoin = "https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=USDT-RUB"
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url_kucoin) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price = float(data.get("data", {}).get("price", 0))
                    if price > 0:
                        return price
    except Exception:
        pass

    # 2. Резервный источник: открытый шлюз открытых котировок (USD/RUB ~ USDT/RUB)
    url_backup = "https://open.er-api.com/v6/latest/USD"
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url_backup) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price = float(data.get("rates", {}).get("RUB", 0))
                    if price > 0:
                        return price
    except Exception:
        pass

    return None

# ----------------- БАЗА ДАННЫХ ----------------- #
def init_db():
    conn = sqlite3.connect("p2p_tracker.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            fiat_balance REAL DEFAULT 0,
            crypto_balance REAL DEFAULT 0,
            total_invested_in_crypto REAL DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT,
            rub_amount REAL,
            crypto_amount REAL,
            rate REAL,
            profit REAL,
            date TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_or_create_user(user_id):
    conn = sqlite3.connect("p2p_tracker.db")
    cursor = conn.cursor()
    cursor.execute("SELECT fiat_balance, crypto_balance, total_invested_in_crypto FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute("INSERT INTO users VALUES (?, 0, 0, 0)", (user_id,))
        conn.commit()
        row = (0.0, 0.0, 0.0)
    conn.close()
    return row

def reset_user_data(user_id):
    conn = sqlite3.connect("p2p_tracker.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def process_buy(user_id, spent_rub, rate):
    crypto_bought = spent_rub / rate
    conn = sqlite3.connect("p2p_tracker.db")
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users 
        SET crypto_balance = crypto_balance + ?, 
            total_invested_in_crypto = total_invested_in_crypto + ?
        WHERE user_id = ?
    """, (crypto_bought, spent_rub, user_id))
    
    cursor.execute("""
        INSERT INTO history (user_id, type, rub_amount, crypto_amount, rate, profit, date)
        VALUES (?, 'BUY', ?, ?, ?, 0, ?)
    """, (user_id, spent_rub, crypto_bought, rate, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()
    return crypto_bought

def process_sell(user_id, received_rub, sell_rate):
    crypto_sold = received_rub / sell_rate
    conn = sqlite3.connect("p2p_tracker.db")
    cursor = conn.cursor()
    cursor.execute("SELECT crypto_balance, total_invested_in_crypto FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    current_crypto = row[0] if row else 0
    total_invested = row[1] if row else 0
    
    avg_buy_rate = (total_invested / current_crypto) if current_crypto > 0 else sell_rate
    cost_basis = crypto_sold * avg_buy_rate
    profit = received_rub - cost_basis
    spread = ((sell_rate - avg_buy_rate) / avg_buy_rate) * 100 if avg_buy_rate > 0 else 0

    new_crypto = max(0.0, current_crypto - crypto_sold)
    new_invested = max(0.0, total_invested - cost_basis)

    cursor.execute("""
        UPDATE users 
        SET fiat_balance = fiat_balance + ?, 
            crypto_balance = ?, 
            total_invested_in_crypto = ?
        WHERE user_id = ?
    """, (received_rub, new_crypto, new_invested, user_id))

    cursor.execute("""
        INSERT INTO history (user_id, type, rub_amount, crypto_amount, rate, profit, date)
        VALUES (?, 'SELL', ?, ?, ?, ?, ?)
    """, (user_id, received_rub, crypto_sold, sell_rate, profit, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()
    return crypto_sold, avg_buy_rate, profit, spread

# ----------------- КОМАНДЫ ----------------- #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_or_create_user(update.effective_user.id)
    markup = ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text(
        "👋 Бот для P2P-учёта готов к работе.\n\n"
        "• *«🛒 Купить»* — зафиксировать покупку монет (деньги сразу учтутся)\n"
        "• *«🏷 Продать»* — зафиксировать продажу в рублях и получить спред\n"
        "• *«📊 Баланс и статистика»* — текущие остатки и общая прибыль",
        parse_mode="Markdown",
        reply_markup=markup,
    )

# --- ПОКУПКА ---
async def buy_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛒 *Покупка*\n\nСколько рублей потрачено на покупку?\n(Например: 50000)",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return BUY_RUB

async def buy_get_rub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rub = float(update.message.text.replace(",", ".").replace(" ", ""))
        if rub <= 0:
            raise ValueError
        context.user_data["buy_rub"] = rub
        await update.message.reply_text("По какому курсу купили? (Цена за 1 USDT в рублях, например: 92.50)")
        return BUY_RATE
    except ValueError:
        await update.message.reply_text("⚠️ Введите корректную сумму в рублях.")
        return BUY_RUB

async def buy_get_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rate = float(update.message.text.replace(",", ".").replace(" ", ""))
        if rate <= 0:
            raise ValueError
        rub = context.user_data["buy_rub"]
        bought_crypto = process_buy(update.effective_user.id, rub, rate)
        
        markup = ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        text = (
            "✅ *Покупка зафиксирована!*\n\n"
            f"💵 Потрачено рублей: `{rub:,.2f} ₽`\n"
            f"🛒 Курс закупки: `{rate:.2f} ₽`\n"
            f"🪙 Зачислено на баланс: `+{bought_crypto:.2f} USDT`"
        )
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
        context.user_data.clear()
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("⚠️ Введите корректный курс.")
        return BUY_RATE

# --- ПРОДАЖА ---
async def sell_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_or_create_user(update.effective_user.id)
    crypto_bal = user[1]
    if crypto_bal <= 0:
        await update.message.reply_text(
            "⚠️ На балансе нет доступных монет (`0 USDT`). Сначала добавьте покупку!",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"🏷 *Продажа*\n\nДоступно на балансе: `{crypto_bal:.2f} USDT`.\n"
        "Сколько рублей вы продаете (получаете)?\n(Например: 51500)",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return SELL_RUB

async def sell_get_rub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rub = float(update.message.text.replace(",", ".").replace(" ", ""))
        if rub <= 0:
            raise ValueError
        context.user_data["sell_rub"] = rub
        await update.message.reply_text("По какому курсу продали в рублях? (Например: 94.20)")
        return SELL_RATE
    except ValueError:
        await update.message.reply_text("⚠️ Введите корректную сумму в рублях.")
        return SELL_RUB

async def sell_get_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rate = float(update.message.text.replace(",", ".").replace(" ", ""))
        if rate <= 0:
            raise ValueError
        
        sell_rub = context.user_data["sell_rub"]
        required_crypto = sell_rub / rate
        user = get_or_create_user(update.effective_user.id)
        current_crypto = user[1]

        if required_crypto > current_crypto + 0.001:
            await update.message.reply_text(
                f"⚠️ Для продажи на `{sell_rub:,.2f} ₽` по курсу `{rate:.2f}` нужно `{required_crypto:.2f} USDT`.\n"
                f"У вас на балансе только `{current_crypto:.2f} USDT`.\n\n"
                "Введите курс продажи заново или напишите /cancel:"
            )
            return SELL_RATE

        crypto_sold, avg_buy, profit, spread = process_sell(update.effective_user.id, sell_rub, rate)
        
        markup = ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
        text = (
            "✅ *Продажа зафиксирована!*\n\n"
            f"💳 Выручено рублей: `+{sell_rub:,.2f} ₽`\n"
            f"🏷 Курс продажи: `{rate:.2f} ₽`\n"
            f"🪙 Списано монет: `-{crypto_sold:.2f} USDT`\n"
            f"📊 Средняя цена закупки: `{avg_buy:.2f} ₽`\n"
            f"➖➖➖➖➖➖➖➖\n"
            f"📈 Чистый профит: `{profit:+,.2f} ₽`\n"
            f"🎯 Спред: `{spread:+.2f}%`"
        )
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
        context.user_data.clear()
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("⚠️ Введите корректный курс.")
        return SELL_RATE

# --- БАЛАНС ---
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_or_create_user(update.effective_user.id)
    fiat_bal, crypto_bal, total_invested = user
    
    conn = sqlite3.connect("p2p_tracker.db")
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(profit) FROM history WHERE user_id = ?", (update.effective_user.id,))
    realized_profit = cursor.fetchone()[0] or 0.0
    conn.close()

    live_rate = await get_live_usdt_rate()

    if live_rate and crypto_bal > 0:
        crypto_market_val = crypto_bal * live_rate
        rate_source_text = f"`{live_rate:,.2f} ₽` *(онлайн-курс)*"
    else:
        crypto_market_val = total_invested
        rate_source_text = "*(по себестоимости закупки)*"

    total_assets = fiat_bal + crypto_market_val

    text = (
        "📊 *Текущий баланс и статистика*\n\n"
        f"💵 *Выручка на руках (рубли):* `{fiat_bal:,.2f} ₽`\n"
        f"🪙 *Криптовалюта в наличии:* `{crypto_bal:.2f} USDT`\n"
        f"📈 *Рыночный курс USDT:* {rate_source_text}\n"
        f"💎 *Текущая оценка крипты:* `{crypto_market_val:,.2f} ₽`\n"
        f"➖➖➖➖➖➖➖➖\n"
        f"💼 *Всего активов (рубли + крипта):* `{total_assets:,.2f} ₽`\n"
        f"💰 *Зафиксированная чистая прибыль:* `{realized_profit:+,.2f} ₽`"
    )

    await update.message.reply_text(text, parse_mode="Markdown")

# --- СБРОС ---
async def reset_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    confirm_keyboard = [["⚠️ Да, сбросить всё", "❌ Отмена"]]
    markup = ReplyKeyboardMarkup(confirm_keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "❗ *Сбросить всю статистику и балансы?*",
        parse_mode="Markdown",
        reply_markup=markup,
    )
    return CONFIRM_RESET

async def reset_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text
    markup = ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    if choice == "⚠️ Да, сбросить всё":
        reset_user_data(update.effective_user.id)
        await update.message.reply_text("✅ Балансы и история очищены.", reply_markup=markup)
    else:
        await update.message.reply_text("Действие отменено.", reply_markup=markup)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    markup = ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text("Действие отменено.", reply_markup=markup)
    return ConversationHandler.END

# ----------------- MAIN ----------------- #
def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    buy_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🛒 Купить$"), buy_start)],
        states={
            BUY_RUB: [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_get_rub)],
            BUY_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_get_rate)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    sell_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🏷 Продать$"), sell_start)],
        states={
            SELL_RUB: [MessageHandler(filters.TEXT & ~filters.COMMAND, sell_get_rub)],
            SELL_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, sell_get_rate)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    reset_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🗑 Сброс статистики$"), reset_start)],
        states={CONFIRM_RESET: [MessageHandler(filters.TEXT & ~filters.COMMAND, reset_confirm)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^📊 Баланс и статистика$"), balance))
    app.add_handler(buy_handler)
    app.add_handler(sell_handler)
    app.add_handler(reset_handler)

    print("Бот запущен...")
    app.run_polling()
import threading
from flask import Flask
import os

web_app = Flask('')

@web_app.route('/')
def home():
    return "Бот работает!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host='0.0.0.0', port=port)

# Запуск веб-сервера в фоне перед запуском бота
threading.Thread(target=run_web, daemon=True).start()

if __name__ == "__main__":
    main()