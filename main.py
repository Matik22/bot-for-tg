import os
import requests
import time
import json
import sqlite3
from datetime import datetime, timedelta

# ==================== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN")
PRIVATE_CHANNEL_ID = os.getenv("PRIVATE_CHANNEL_ID")

BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
CRYPTOBOT_API = "https://pay.crypt.bot/api"


# ==================== БАЗА ДАННЫХ ====================
def init_db():
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, 
                  username TEXT,
                  first_name TEXT,
                  balance INTEGER DEFAULT 0,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS subscriptions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  channel_type TEXT,
                  expires_at TIMESTAMP,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users (user_id))"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS transactions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  type TEXT,
                  amount INTEGER,
                  description TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users (user_id))"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS invite_links
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  invite_link TEXT UNIQUE,
                  expires_at TIMESTAMP,
                  used BOOLEAN DEFAULT FALSE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users (user_id))"""
    )
    conn.commit()
    conn.close()


init_db()

# ==================== КОНФИГУРАЦИЯ КАНАЛОВ ====================
CHANNELS = {
    "free": {
        "name": "🎯 PRO100MILLION",
        "link": "https://t.me/prostavamillion",
        "description": "Бесплатные прогнозы и аналитика",
    },
    "premium": {
        "name": "💎 PRO100MILLION PREMIUM",
        "description": "Эксклюзивные ставки и гарантированные прогнозы",
        "price_rub": 2000,
        "price_stars": 2000,
        "duration_days": 30,
    },
}

STAR_PACKAGES = [
    {"stars": 100, "rub": 50, "bonus": "", "popular": False},
    {"stars": 500, "rub": 250, "bonus": "", "popular": False},
    {"stars": 1000, "rub": 500, "bonus": "", "popular": True},
    {"stars": 2000, "rub": 1000, "bonus": "🔥", "popular": True},
    {"stars": 5000, "rub": 2500, "bonus": "🔥 +5%", "popular": False},
]

# ==================== ХРАНИЛИЩЕ СЕССИЙ ====================
last_update_id = 0
active_crypto_invoices = {}


# ==================== ФУНКЦИИ ДЛЯ БАЛАНСА ====================
def get_user_balance(user_id):
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else 0


def update_user_balance(user_id, amount, username="", first_name=""):
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute(
        """INSERT OR REPLACE INTO users (user_id, username, first_name, balance)
                 VALUES (?, ?, ?, COALESCE((SELECT balance FROM users WHERE user_id = ?), 0) + ?)""",
        (user_id, username, first_name, user_id, amount),
    )
    conn.commit()
    conn.close()
    return True


def add_transaction(user_id, transaction_type, amount, description):
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute(
        """INSERT INTO transactions (user_id, type, amount, description)
                 VALUES (?, ?, ?, ?)""",
        (user_id, transaction_type, amount, description),
    )
    conn.commit()
    conn.close()


# ==================== ФУНКЦИИ ПОДПИСОК ====================
def create_user_subscription(user_id, channel_type, duration_days=30):
    expires_at = datetime.now() + timedelta(days=duration_days)
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute(
        """INSERT INTO subscriptions (user_id, channel_type, expires_at)
                 VALUES (?, ?, ?)""",
        (user_id, channel_type, expires_at),
    )
    conn.commit()
    conn.close()
    return expires_at


def get_user_subscriptions(user_id):
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute(
        """SELECT channel_type, expires_at FROM subscriptions 
                 WHERE user_id = ? AND expires_at > datetime('now') 
                 ORDER BY expires_at DESC""",
        (user_id,),
    )
    result = c.fetchall()
    conn.close()
    return result


def save_invite_link(user_id, invite_link, expires_at):
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute(
        """INSERT INTO invite_links (user_id, invite_link, expires_at)
                 VALUES (?, ?, ?)""",
        (user_id, invite_link, expires_at),
    )
    conn.commit()
    conn.close()


def get_active_invite_link(user_id):
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute(
        """SELECT invite_link, expires_at FROM invite_links 
                 WHERE user_id = ? AND expires_at > datetime('now') AND used = FALSE
                 ORDER BY created_at DESC LIMIT 1""",
        (user_id,),
    )
    result = c.fetchone()
    conn.close()
    return result


def mark_invite_link_used(invite_link):
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute(
        "UPDATE invite_links SET used = TRUE WHERE invite_link = ?", (invite_link,)
    )
    conn.commit()
    conn.close()


# ==================== ФУНКЦИИ СООБЩЕНИЙ ====================
def send_message(chat_id, text, reply_markup=None):
    url = f"{BASE_URL}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        data["reply_markup"] = reply_markup
    try:
        response = requests.post(url, json=data, timeout=10)
        return response.json()
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")
        return None


# ==================== ФУНКЦИИ КЛАВИАТУР ====================
def create_main_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "🎯 Бесплатный канал", "callback_data": "channel_free"}],
            [{"text": "💎 Премиум канал", "callback_data": "channel_premium"}],
            [{"text": "⭐ Купить звёзды", "callback_data": "buy_stars"}],
            [{"text": "💰 Мой баланс", "callback_data": "my_balance"}],
            [{"text": "📊 Мои подписки", "callback_data": "my_subs"}],
        ]
    }


def create_premium_keyboard(user_id):
    user_balance = get_user_balance(user_id)
    channel = CHANNELS["premium"]
    keyboard = []
    if user_balance >= channel["price_stars"]:
        keyboard.append(
            [
                {
                    "text": f"⭐ Оплатить {channel['price_stars']} звёзд с баланса",
                    "callback_data": "pay_from_balance",
                }
            ]
        )
    keyboard.append(
        [
            {
                "text": f"💳 Купить {channel['price_stars']} звёзд",
                "callback_data": "buy_stars_for_sub",
            }
        ]
    )
    keyboard.append(
        [
            {
                "text": f"₿ Оплатить {channel['price_rub']}₽ криптой",
                "callback_data": "pay_crypto_premium",
            }
        ]
    )
    keyboard.append([{"text": "🔙 Назад", "callback_data": "back_main"}])
    return {"inline_keyboard": keyboard}


def create_crypto_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "USDT", "callback_data": "crypto_usdt"}],
            [{"text": "TON", "callback_data": "crypto_ton"}],
            [{"text": "BTC", "callback_data": "crypto_btc"}],
            [{"text": "ETH", "callback_data": "crypto_eth"}],
            [{"text": "🔙 Назад", "callback_data": "channel_premium"}],
        ]
    }


# ==================== ФУНКЦИИ ОПЛАТЫ ====================
def send_stars_invoice(chat_id, stars_amount, description):
    url = f"{BASE_URL}/sendInvoice"
    data = {
        "chat_id": chat_id,
        "title": f"⭐ {stars_amount} Telegram Stars",
        "description": description,
        "payload": f"stars_{stars_amount}",
        "provider_token": os.getenv("STARS_PROVIDER_TOKEN", ""),
        "currency": "XTR",
        "prices": [{"label": "Stars", "amount": stars_amount}],
        "start_parameter": "stars",
        "need_name": False,
        "need_email": False,
        "need_phone_number": False,
        "need_shipping_address": False,
    }
    try:
        response = requests.post(url, json=data, timeout=10)
        result = response.json()
        return result.get("ok", False)
    except Exception as e:
        print(f"❌ Ошибка отправки инвойса: {e}")
        return False


def answer_pre_checkout_query(pre_checkout_query_id):
    url = f"{BASE_URL}/answerPreCheckoutQuery"
    data = {"pre_checkout_query_id": pre_checkout_query_id, "ok": True}
    try:
        response = requests.post(url, json=data, timeout=10)
        return response.json()
    except Exception as e:
        print(f"❌ Ошибка подтверждения платежа: {e}")
        return None


def create_crypto_invoice(amount_rub, currency="USDT", description="Оплата подписки"):
    url = f"{CRYPTOBOT_API}/createInvoice"
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN,
        "Content-Type": "application/json",
    }
    rates = {"USDT": 90, "TON": 180, "BTC": 5800000, "ETH": 300000}
    if currency not in rates:
        return None
    amount_crypto = round(
        amount_rub / rates[currency],
        2 if currency in ["USDT", "TON"] else 6 if currency == "BTC" else 4,
    )
    data = {
        "asset": currency,
        "amount": str(amount_crypto),
        "description": description,
        "payload": str(int(time.time())),
        "allow_comments": False,
        "allow_anonymous": False,
        "expires_in": 3600,
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        result = response.json()
        return result.get("result")
    except Exception as e:
        print(f"❌ Ошибка CryptoBot: {e}")
        return None


def check_crypto_invoice(invoice_id):
    url = f"{CRYPTOBOT_API}/getInvoices"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    params = {"invoice_ids": invoice_id}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        result = response.json()
        if result.get("ok") and result["result"]["items"]:
            return result["result"]["items"][0]
        return None
    except Exception as e:
        print(f"❌ Ошибка проверки инвойса: {e}")
        return None


# ==================== ФУНКЦИИ ПОДПИСОК И ССЫЛОК ====================
def generate_invite_link(chat_id, user_id, duration_days=30):
    url = f"{BASE_URL}/createChatInviteLink"
    data = {
        "chat_id": chat_id,
        "name": f"Premium Access for User {user_id}",
        "expire_date": int(time.time()) + duration_days * 24 * 3600,
        "member_limit": 1,
        "creates_join_request": False,
    }
    try:
        response = requests.post(url, json=data, timeout=10)
        result = response.json()
        if result.get("ok"):
            invite_link = result["result"]["invite_link"]
            expires_at = datetime.now() + timedelta(days=duration_days)
            save_invite_link(user_id, invite_link, expires_at)
            return invite_link
        return None
    except Exception as e:
        print(f"❌ Ошибка создания ссылки: {e}")
        return None


def activate_premium_subscription(user_id, chat_id):
    channel = CHANNELS["premium"]
    create_user_subscription(user_id, "premium", channel["duration_days"])
    invite_link = generate_invite_link(
        PRIVATE_CHANNEL_ID, user_id, channel["duration_days"]
    )
    if invite_link:
        send_message(
            chat_id,
            f"🎉 Подписка активирована!\n🔗 Ваша ссылка: {invite_link}\n⏰ Срок: {channel['duration_days']} дней",
            create_main_keyboard(),
        )
    else:
        send_message(
            chat_id,
            "✅ Подписка активирована, но возникла проблема с ссылкой.\nОбратитесь к администратору.",
            create_main_keyboard(),
        )


# ==================== ФУНКЦИИ GET UPDATES ====================
def get_updates():
    global last_update_id
    url = f"{BASE_URL}/getUpdates"
    params = {"offset": last_update_id + 1, "timeout": 30}
    try:
        response = requests.get(url, params=params, timeout=35)
        data = response.json()
        if data.get("ok"):
            return data["result"]
        return []
    except Exception as e:
        print(f"❌ Ошибка сети: {e}")
        return []


# ==================== ОБРАБОТКА КОЛБЭКОВ ====================
def handle_callback(update):
    global last_update_id
    last_update_id = update["update_id"]
    callback = update["callback_query"]
    chat_id = callback["message"]["chat"]["id"]
    user_id = callback["from"]["id"]
    data = callback["data"]

    if data == "channel_free":
        channel = CHANNELS["free"]
        send_message(
            chat_id,
            f"<b>{channel['name']}</b>\n{channel['description']}\n{channel['link']}",
            create_main_keyboard(),
        )
    elif data == "channel_premium":
        send_message(
            chat_id,
            f"💎 Премиум канал\nСтоимость: {CHANNELS['premium']['price_stars']} ⭐",
            create_premium_keyboard(user_id),
        )
    elif data == "pay_from_balance":
        if get_user_balance(user_id) >= CHANNELS["premium"]["price_stars"]:
            update_user_balance(user_id, -CHANNELS["premium"]["price_stars"])
            add_transaction(
                user_id,
                "subscription",
                -CHANNELS["premium"]["price_stars"],
                "Оплата подписки",
            )
            activate_premium_subscription(user_id, chat_id)
        else:
            send_message(
                chat_id, "❌ Недостаточно средств на балансе", create_main_keyboard()
            )
    elif data.startswith("stars_"):
        stars_amount = int(data.split("_")[1])
        send_stars_invoice(chat_id, stars_amount, f"Пополнение {stars_amount} ⭐")
    elif data.startswith("crypto_"):
        currency = data.split("_")[1].upper()
        invoice = create_crypto_invoice(
            CHANNELS["premium"]["price_rub"], currency, "Подписка премиум"
        )
        if invoice:
            active_crypto_invoices[invoice["invoice_id"]] = {
                "user_id": user_id,
                "chat_id": chat_id,
                "created_at": time.time(),
            }
            send_message(
                chat_id,
                f"Оплатите: {invoice['amount']} {currency}\nСсылка: {invoice['pay_url']}",
                create_main_keyboard(),
            )


# ==================== ОСНОВНОЙ ЦИКЛ ====================
def main():
    global last_update_id
    print("🚀 Бот запущен")
    while True:
        try:
            updates = get_updates()
            for update in updates:
                if "callback_query" in update:
                    handle_callback(update)
            time.sleep(0.5)
        except KeyboardInterrupt:
            print("🛑 Бот остановлен")
            break
        except Exception as e:
            print(f"⚠️ Ошибка: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
