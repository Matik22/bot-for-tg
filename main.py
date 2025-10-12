import os
import requests
import time
import json
import sqlite3
from datetime import datetime, timedelta

# ==================== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CRYPTOBOT_TOKEN = os.environ.get("CRYPTOBOT_TOKEN")
PRIVATE_CHANNEL_ID = os.environ.get("PRIVATE_CHANNEL_ID")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

if not BOT_TOKEN or not CRYPTOBOT_TOKEN or not PRIVATE_CHANNEL_ID or not ADMIN_ID:
    raise Exception("❌ Не заданы все необходимые переменные окружения: BOT_TOKEN, CRYPTOBOT_TOKEN, PRIVATE_CHANNEL_ID, ADMIN_ID")

print(f"🔑 Бот запущен с токеном: {BOT_TOKEN}")

BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
CRYPTOBOT_API = "https://pay.crypt.bot/api"

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, 
                  username TEXT,
                  first_name TEXT,
                  balance INTEGER DEFAULT 0,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  channel_type TEXT,
                  expires_at TIMESTAMP,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users (user_id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS transactions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  type TEXT,
                  amount INTEGER,
                  description TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users (user_id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS invite_links
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  invite_link TEXT UNIQUE,
                  expires_at TIMESTAMP,
                  used BOOLEAN DEFAULT FALSE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users (user_id))''')
    
    conn.commit()
    conn.close()

init_db()

# ==================== КАНАЛЫ ====================
CHANNELS = {
    "free": {
        "name": "🎯 PRO100MILLION",
        "link": "https://t.me/prostavamillion",
        "description": "Бесплатные прогнозы и аналитика"
    },
    "premium": {
        "name": "💎 PRO100MILLION PREMIUM", 
        "description": "Эксклюзивные ставки и гарантированные прогнозы",
        "price_rub": 2000,
        "price_stars": 2000,
        "duration_days": 30
    }
}

# ==================== ПАКЕТЫ ЗВЁЗД ====================
STAR_PACKAGES = [
    {"stars": 100, "rub": 50, "bonus": "", "popular": False},
    {"stars": 500, "rub": 250, "bonus": "", "popular": False},
    {"stars": 1000, "rub": 500, "bonus": "", "popular": True},
    {"stars": 2000, "rub": 1000, "bonus": "🔥", "popular": True},
    {"stars": 5000, "rub": 2500, "bonus": "🔥 +5%", "popular": False},
]

# ==================== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ====================
last_update_id = 0
active_crypto_invoices = {}

# ==================== ФУНКЦИИ РАБОТЫ С БД ====================
def get_user_balance(user_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else 0

def update_user_balance(user_id, amount, username="", first_name=""):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO users (user_id, username, first_name, balance)
                 VALUES (?, ?, ?, COALESCE((SELECT balance FROM users WHERE user_id = ?), 0) + ?)''',
              (user_id, username, first_name, user_id, amount))
    conn.commit()
    conn.close()
    return True

def add_transaction(user_id, transaction_type, amount, description):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('''INSERT INTO transactions (user_id, type, amount, description)
                 VALUES (?, ?, ?, ?)''', (user_id, transaction_type, amount, description))
    conn.commit()
    conn.close()

def create_user_subscription(user_id, channel_type, duration_days=30):
    expires_at = datetime.now() + timedelta(days=duration_days)
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('''INSERT INTO subscriptions (user_id, channel_type, expires_at)
                 VALUES (?, ?, ?)''', (user_id, channel_type, expires_at))
    conn.commit()
    conn.close()
    return expires_at

def get_user_subscriptions(user_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('''SELECT channel_type, expires_at FROM subscriptions 
                 WHERE user_id = ? AND expires_at > datetime('now') 
                 ORDER BY expires_at DESC''', (user_id,))
    result = c.fetchall()
    conn.close()
    return result

def save_invite_link(user_id, invite_link, expires_at):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('''INSERT INTO invite_links (user_id, invite_link, expires_at)
                 VALUES (?, ?, ?)''', (user_id, invite_link, expires_at))
    conn.commit()
    conn.close()

def get_active_invite_link(user_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('''SELECT invite_link, expires_at FROM invite_links 
                 WHERE user_id = ? AND expires_at > datetime('now') AND used = FALSE
                 ORDER BY created_at DESC LIMIT 1''', (user_id,))
    result = c.fetchone()
    conn.close()
    return result

def mark_invite_link_used(invite_link):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("UPDATE invite_links SET used = TRUE WHERE invite_link = ?", (invite_link,))
    conn.commit()
    conn.close()

# ==================== ФУНКЦИИ TELEGRAM API ====================
def send_message(chat_id, text, reply_markup=None):
    url = f"{BASE_URL}/sendMessage"
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if reply_markup:
        data["reply_markup"] = reply_markup
    try:
        response = requests.post(url, json=data, timeout=10)
        return response.json()
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")
        return None

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

# ==================== ЗАПУСК БОТА ====================
def main():
    global last_update_id
    print("🚀 Запуск бота на Render Background Worker...")
    
    while True:
        try:
            updates = get_updates()
            for update in updates:
                last_update_id = update["update_id"]
                # обработка сообщений и callback'ов
                # здесь можно вставить все функции handle_message, handle_callback и handle_successful_payment
            time.sleep(0.5)
        except KeyboardInterrupt:
            print("🛑 Бот остановлен")
            break
        except Exception as e:
            print(f"⚠️ Ошибка: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
