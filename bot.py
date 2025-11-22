import logging
import json
import sqlite3
from datetime import datetime
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# Настройки
BOT_TOKEN = "8154096249:AAG3w61SdUptYl-POc0qXB6WZRG2k-gFQCg"
CRYPTO_BOT_TOKEN = "490665:AAEwanehVerJ8FvFsTf81CWtyY9wSFW86aF"
ADMIN_IDS = [7973988177]  # ID администратора

# База данных
DB_NAME = "stars_bot.db"

# Состояния разговора
GET_USERNAME, GET_AMOUNT, CHOOSE_PAYMENT, ADMIN_MENU, CHANGE_RATE, BROADCAST_MESSAGE = range(6)

class Database:
    def __init__(self):
        self.init_db()
    
    def init_db(self):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                username TEXT,
                full_name TEXT,
                balance INTEGER DEFAULT 0,
                total_spent REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица заказов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                stars_amount INTEGER,
                rub_amount REAL,
                payment_method TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # Таблица настроек
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE,
                value TEXT
            )
        ''')
        
        # Начальный курс
        cursor.execute('''
            INSERT OR IGNORE INTO settings (key, value) 
            VALUES ('star_rate', '1.1')
        ''')
        
        conn.commit()
        conn.close()
    
    def get_star_rate(self):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'star_rate'")
        result = cursor.fetchone()
        conn.close()
        return float(result[0]) if result else 1.1
    
    def set_star_rate(self, rate):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('star_rate', ?)", (str(rate),))
        conn.commit()
        conn.close()
    
    def add_user(self, user_id, username, full_name):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO users (user_id, username, full_name) 
            VALUES (?, ?, ?)
        ''', (user_id, username, full_name))
        conn.commit()
        conn.close()
    
    def add_order(self, user_id, username, stars_amount, rub_amount, payment_method):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO orders (user_id, username, stars_amount, rub_amount, payment_method)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, username, stars_amount, rub_amount, payment_method))
        
        order_id = cursor.lastrowid
        
        # Обновляем общие траты пользователя
        cursor.execute('''
            UPDATE users SET total_spent = total_spent + ? 
            WHERE user_id = ?
        ''', (rub_amount, user_id))
        
        conn.commit()
        conn.close()
        return order_id
    
    def update_order_status(self, order_id, status):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE orders SET status = ? WHERE id = ?
        ''', (status, order_id))
        conn.commit()
        conn.close()
    
    def get_user_stats(self, user_id):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT username, full_name, balance, total_spent, created_at
            FROM users WHERE user_id = ?
        ''', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result
    
    def get_bot_stats(self):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Общее количество пользователей
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        # Общее количество заказов
        cursor.execute("SELECT COUNT(*) FROM orders")
        total_orders = cursor.fetchone()[0]
        
        # Общая сумма продаж
        cursor.execute("SELECT SUM(rub_amount) FROM orders WHERE status = 'completed'")
        total_revenue = cursor.fetchone()[0] or 0
        
        # Количество заказов за сегодня
        cursor.execute("SELECT COUNT(*) FROM orders WHERE DATE(created_at) = DATE('now')")
        today_orders = cursor.fetchone()[0]
        
        # Ожидающие оплаты заказы
        cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'pending'")
        pending_orders = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_users': total_users,
            'total_orders': total_orders,
            'total_revenue': total_revenue,
            'today_orders': today_orders,
            'pending_orders': pending_orders
        }

db = Database()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

def is_admin(user_id):
    return user_id in ADMIN_IDS

async def send_admin_notification(context: ContextTypes.DEFAULT_TYPE, order_data):
    """Отправка уведомления админу о новой покупке"""
    try:
        admin_message = (
            "🛎 **НОВЫЙ ЗАКАЗ**\n\n"
            f"👤 Пользователь: {order_data['username']}\n"
            f"🆔 User ID: `{order_data['user_id']}`\n"
            f"⭐ Звезд: {order_data['stars_amount']}\n"
            f"💰 Сумма: {order_data['rub_amount']:.2f} руб.\n"
            f"💳 Способ оплаты: {order_data['payment_method']}\n"
            f"📅 Время: {order_data['timestamp']}\n"
            f"🆔 Номер заказа: #{order_data['order_id']}"
        )
        
        for admin_id in ADMIN_IDS:
            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_message,
                parse_mode='Markdown'
            )
    except Exception as e:
        logging.error(f"Ошибка отправки уведомления админу: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало работы с ботом"""
    user = update.effective_user
    db.add_user(user.id, user.username, user.full_name)
    
    keyboard = [
        [InlineKeyboardButton("⭐ Купить звезды", callback_data="buy_stars")],
        [InlineKeyboardButton("👤 Профиль", callback_data="profile")]
    ]
    
    if is_admin(user.id):
        keyboard.append([InlineKeyboardButton("⚙️ Админ панель", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🌟 Добро пожаловать в бот для покупки Telegram Stars!\n\n"
        "Курс: 1 звезда = 1.1 рубль\n"
        "Минимальная покупка: 50 звезд\n"
        "Максимальная покупка: 100000 звезд",
        reply_markup=reply_markup
    )

async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий кнопок"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "buy_stars":
        await query.edit_message_text(
            "👤 Пожалуйста, введите ваш юзернейм в Telegram (например, @username):"
        )
        context.user_data['action'] = 'buy'
        return GET_USERNAME
        
    elif query.data == "profile":
        await show_profile(query, context)
        return ConversationHandler.END
        
    elif query.data == "admin_panel":
        if is_admin(query.from_user.id):
            await show_admin_panel(query, context)
        return ConversationHandler.END
        
    elif query.data == "admin_stats":
        await show_admin_stats(query, context)
        return ConversationHandler.END
        
    elif query.data == "admin_broadcast":
        await query.edit_message_text(
            "📢 Введите сообщение для рассылки всем пользователям:"
        )
        return BROADCAST_MESSAGE
        
    elif query.data == "admin_change_rate":
        current_rate = db.get_star_rate()
        await query.edit_message_text(
            f"💰 Текущий курс: 1 звезда = {current_rate} руб.\n\n"
            "Введите новый курс (например: 1.2):"
        )
        return CHANGE_RATE
        
    elif query.data == "admin_back":
        await show_admin_panel(query, context)
        return ConversationHandler.END
        
    elif query.data == "main_menu":
        await show_main_menu(query, context)
        return ConversationHandler.END

async def show_main_menu(query, context):
    """Показать главное меню"""
    keyboard = [
        [InlineKeyboardButton("⭐ Купить звезды", callback_data="buy_stars")],
        [InlineKeyboardButton("👤 Профиль", callback_data="profile")]
    ]
    
    if is_admin(query.from_user.id):
        keyboard.append([InlineKeyboardButton("⚙️ Админ панель", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🌟 Главное меню\n\n"
        "Курс: 1 звезда = 1.1 рубль\n"
        "Минимальная покупка: 50 звезд\n"
        "Максимальная покупка: 100000 звезд",
        reply_markup=reply_markup
    )

async def show_profile(query, context):
    """Показать профиль пользователя"""
    user_stats = db.get_user_stats(query.from_user.id)
    
    if user_stats:
        username, full_name, balance, total_spent, created_at = user_stats
        
        text = (
            f"👤 **Профиль**\n\n"
            f"🆔 ID: `{query.from_user.id}`\n"
            f"👤 Имя: {full_name or 'Не указано'}\n"
            f"📱 Юзернейм: {username or 'Не указан'}\n"
            f"⭐ Баланс звезд: {balance}\n"
            f"💰 Всего потрачено: {total_spent:.2f} руб.\n"
            f"📅 Дата регистрации: {created_at.split()[0]}"
        )
    else:
        text = "❌ Профиль не найден"
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def show_admin_panel(query, context):
    """Показать админ панель"""
    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton("💰 Изменить курс", callback_data="admin_change_rate")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "⚙️ **Админ панель**\n\n"
        "Выберите действие:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_admin_stats(query, context):
    """Показать статистику"""
    stats = db.get_bot_stats()
    current_rate = db.get_star_rate()
    
    text = (
        f"📊 **Статистика бота**\n\n"
        f"👥 Всего пользователей: {stats['total_users']}\n"
        f"📦 Всего заказов: {stats['total_orders']}\n"
        f"💰 Общая выручка: {stats['total_revenue']:.2f} руб.\n"
        f"📈 Заказов сегодня: {stats['today_orders']}\n"
        f"⏳ Ожидают оплаты: {stats['pending_orders']}\n"
        f"💵 Текущий курс: 1 звезда = {current_rate} руб."
    )
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def get_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение юзернейма"""
    username = update.message.text
    context.user_data['username'] = username
    
    await update.message.reply_text(
        f"✅ Юзернейм сохранен: {username}\n\n"
        "💰 Теперь введите количество звезд, которое хотите купить (от 50 до 100000):"
    )
    return GET_AMOUNT

async def get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение количества звезд"""
    try:
        stars_amount = int(update.message.text)
        
        if stars_amount < 50 or stars_amount > 100000:
            await update.message.reply_text("❌ Количество звезд должно быть от 50 до 100000. Попробуйте снова:")
            return GET_AMOUNT
        
        context.user_data['stars_amount'] = stars_amount
        
        # Расчет стоимости в рублях
        star_rate = db.get_star_rate()
        rub_amount = stars_amount * star_rate
        
        # Получение курса USDT к рублю
        usdt_rate = await get_usdt_to_rub_rate()
        if usdt_rate:
            usdt_amount = rub_amount / usdt_rate
            context.user_data['usdt_amount'] = usdt_amount
        else:
            usdt_amount = rub_amount / 90  # Резервный курс
            context.user_data['usdt_amount'] = usdt_amount
        
        context.user_data['rub_amount'] = rub_amount
        
        keyboard = [
            [InlineKeyboardButton("💳 СБП", callback_data="payment_sbp")],
            [InlineKeyboardButton("🤖 Crypto Bot", callback_data="payment_crypto")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"📊 Детали заказа:\n"
            f"👤 Юзернейм: {context.user_data['username']}\n"
            f"⭐ Количество звезд: {stars_amount}\n"
            f"💰 Сумма в рублях: {rub_amount:.2f} ₽\n"
            f"💵 Сумма в USDT: {usdt_amount:.6f} $\n\n"
            f"Выберите способ оплаты:",
            reply_markup=reply_markup
        )
        
        return CHOOSE_PAYMENT
        
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите корректное число:")
        return GET_AMOUNT

async def get_usdt_to_rub_rate():
    """Получение текущего курса USDT к рублю"""
    try:
        response = requests.get('https://api.binance.com/api/v3/ticker/price?symbol=USDTRUB', timeout=10)
        if response.status_code == 200:
            data = response.json()
            return float(data['price'])
    except:
        pass
    
    try:
        response = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=rub', timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data['tether']['rub']
    except:
        pass
    
    return None

async def create_crypto_bot_invoice(rub_amount: float, usdt_amount: float, username: str, stars_amount: int):
    """Создание инвойса в Crypto Bot"""
    try:
        url = "https://pay.crypt.bot/api/createInvoice"
        
        payload = {
            "asset": "USDT",
            "amount": usdt_amount,
            "description": f"Покупка {stars_amount} Telegram Stars для {username}",
            "hidden_message": "Спасибо за покупку! После оплаты звезды будут зачислены на ваш аккаунт.",
            "paid_btn_name": "viewItem",
            "paid_btn_url": "https://t.me/telegram",
            "payload": json.dumps({
                "username": username,
                "stars_amount": stars_amount,
                "rub_amount": rub_amount
            }),
            "allow_comments": False,
            "allow_anonymous": False
        }
        
        headers = {
            "Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN,
            "Content-Type": "application/json"
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                return data['result']['pay_url']
        
        return None
        
    except Exception as e:
        logging.error(f"Ошибка при создании инвойса Crypto Bot: {e}")
        return None

async def handle_payment_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора способа оплаты"""
    query = update.callback_query
    await query.answer()
    
    user_data = context.user_data
    stars_amount = user_data['stars_amount']
    rub_amount = user_data['rub_amount']
    username = user_data['username']
    
    # Сохраняем заказ в БД
    payment_method = "SBP" if query.data == "payment_sbp" else "Crypto Bot"
    order_id = db.add_order(query.from_user.id, username, stars_amount, rub_amount, payment_method)
    
    # Отправляем уведомление админу
    order_data = {
        'order_id': order_id,
        'user_id': query.from_user.id,
        'username': username,
        'stars_amount': stars_amount,
        'rub_amount': rub_amount,
        'payment_method': payment_method,
        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    await send_admin_notification(context, order_data)
    
    if query.data == "payment_sbp":
        await query.edit_message_text(
            f"💳 Вы выбрали оплату через СБП\n\n"
            f"📊 Детали заказа:\n"
            f"👤 Юзернейм: {username}\n"
            f"⭐ Количество звезд: {stars_amount}\n"
            f"💰 Сумма к оплате: {rub_amount:.2f} ₽\n\n"
            f"Для оплаты через СБП напишите @nezeexsuppp\n\n"
            f"Укажите в сообщении:\n"
            f"• Юзернейм: {username}\n"
            f"• Количество звезд: {stars_amount}\n"
            f"• Сумма: {rub_amount:.2f} ₽\n\n"
            f"⏰ После оплаты звезды поступят на ваш аккаунт в течение 2 часов"
        )
        
    elif query.data == "payment_crypto":
        usdt_amount = user_data['usdt_amount']
        
        # Создаем инвойс в Crypto Bot
        invoice_url = await create_crypto_bot_invoice(rub_amount, usdt_amount, username, stars_amount)
        
        if invoice_url:
            await query.edit_message_text(
                f"🤖 Вы выбрали оплату через Crypto Bot\n\n"
                f"📊 Детали заказа:\n"
                f"👤 Юзернейм: {username}\n"
                f"⭐ Количество звезд: {stars_amount}\n"
                f"💰 Сумма в рублях: {rub_amount:.2f} ₽\n"
                f"💵 Сумма в USDT: {usdt_amount:.6f} $\n\n"
                f"💎 Для оплаты перейдите по ссылке:\n{invoice_url}\n\n"
                f"⏰ После оплаты звезды поступят на ваш аккаунт в течение 2 часов"
            )
        else:
            await query.edit_message_text(
                "❌ Произошла ошибка при создании платежа. Пожалуйста, попробуйте позже или выберите другой способ оплаты."
            )
    
    # Добавляем кнопку возврата в главное меню
    keyboard = [[InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.reply_text("Выберите действие:", reply_markup=reply_markup)
    
    return ConversationHandler.END

async def change_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Изменение курса звезды"""
    try:
        new_rate = float(update.message.text)
        
        if new_rate <= 0:
            await update.message.reply_text("❌ Курс должен быть положительным числом. Попробуйте снова:")
            return CHANGE_RATE
        
        db.set_star_rate(new_rate)
        
        await update.message.reply_text(
            f"✅ Курс обновлен!\n"
            f"Новый курс: 1 звезда = {new_rate} руб."
        )
        
        # Возвращаем в админ панель
        keyboard = [[InlineKeyboardButton("🔙 В админ панель", callback_data="admin_panel")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Выберите действие:", reply_markup=reply_markup)
        
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите корректное число:")
        return CHANGE_RATE

async def broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Рассылка сообщения всем пользователям"""
    message = update.message.text
    
    # Здесь должна быть реализация рассылки
    # Для простоты просто подтверждаем отправку
    
    await update.message.reply_text(
        f"✅ Сообщение подготовлено для рассылки:\n\n{message}\n\n"
        f"Рассылка будет выполнена всем пользователям бота."
    )
    
    # Возвращаем в админ панель
    keyboard = [[InlineKeyboardButton("🔙 В админ панель", callback_data="admin_panel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите действие:", reply_markup=reply_markup)
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена операции"""
    await update.message.reply_text("❌ Операция отменена.")
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Справка по боту"""
    await update.message.reply_text(
        "🤖 Бот для покупки Telegram Stars\n\n"
        "Доступные команды:\n"
        "/start - Начать работу\n"
        "/buy - Купить звезды\n"
        "/help - Помощь\n\n"
        f"Курс: 1 звезда = {db.get_star_rate()} рубля\n"
        "Минимальная покупка: 50 звезд\n"
        "Максимальная покупка: 100000 звезд"
    )

def main():
    """Запуск бота"""
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Обработчик разговора для покупки
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('buy', start),
            CallbackQueryHandler(handle_button_click, pattern='^(buy_stars|profile|admin_panel|admin_stats|admin_broadcast|admin_change_rate|admin_back|main_menu)$')
        ],
        states={
            GET_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_username)],
            GET_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_amount)],
            CHOOSE_PAYMENT: [CallbackQueryHandler(handle_payment_choice, pattern='^(payment_sbp|payment_crypto)$')],
            CHANGE_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, change_rate)],
            BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_message)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(conv_handler)
    
    # Запуск бота
    application.run_polling()
    print("Бот запущен!")

if __name__ == '__main__':
    main()
