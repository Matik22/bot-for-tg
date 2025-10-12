import os
import requests
import time
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, HTTPServer

# -------------------- Переменные окружения --------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")             # Telegram Bot Token
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))       # ID администратора
CRYPTOBOT_TOKEN = os.environ.get("CRYPTOBOT_TOKEN") # CryptoBot API Token
PRIVATE_CHANNEL_ID = os.environ.get("PRIVATE_CHANNEL_ID")  # ID приватного канала
PORT = int(os.environ.get("PORT", 8080))           # порт для Render Healthcheck

BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
CRYPTOBOT_API = "https://pay.crypt.bot/api"

# -------------------- База данных --------------------
DB_PATH = "bot_database.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Пользователи
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, 
                  username TEXT,
                  first_name TEXT,
                  balance INTEGER DEFAULT 0,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    # Подписки
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  channel_type TEXT,
                  expires_at TIMESTAMP,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users (user_id))''')
    # Транзакции
    c.execute('''CREATE TABLE IF NOT EXISTS transactions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  type TEXT,
                  amount INTEGER,
                  description TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users (user_id))''')
    # Временные ссылки
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

# -------------------- Настройки каналов и пакетов --------------------
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

STAR_PACKAGES = [
    {"stars": 100, "rub": 50, "bonus": "", "popular": False},
    {"stars": 500, "rub": 250, "bonus": "", "popular": False},
    {"stars": 1000, "rub": 500, "bonus": "", "popular": True},
    {"stars": 2000, "rub": 1000, "bonus": "🔥", "popular": True},
    {"stars": 5000, "rub": 2500, "bonus": "🔥 +5%", "popular": False},
]

active_crypto_invoices = {}
last_update_id = 0

# -------------------- Базовые функции --------------------
def get_user_balance(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else 0

def update_user_balance(user_id, amount, username="", first_name=""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO users (user_id, username, first_name, balance)
                 VALUES (?, ?, ?, COALESCE((SELECT balance FROM users WHERE user_id = ?), 0) + ?)''',
              (user_id, username, first_name, user_id, amount))
    conn.commit()
    conn.close()

def add_transaction(user_id, transaction_type, amount, description):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO transactions (user_id, type, amount, description)
                 VALUES (?, ?, ?, ?)''', (user_id, transaction_type, amount, description))
    conn.commit()
    conn.close()

def create_user_subscription(user_id, channel_type, duration_days=30):
    expires_at = datetime.now() + timedelta(days=duration_days)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO subscriptions (user_id, channel_type, expires_at)
                 VALUES (?, ?, ?)''', (user_id, channel_type, expires_at))
    conn.commit()
    conn.close()
    return expires_at

def get_user_subscriptions(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT channel_type, expires_at FROM subscriptions 
                 WHERE user_id = ? AND expires_at > datetime('now') 
                 ORDER BY expires_at DESC''', (user_id,))
    result = c.fetchall()
    conn.close()
    return result

def save_invite_link(user_id, invite_link, expires_at):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO invite_links (user_id, invite_link, expires_at)
                 VALUES (?, ?, ?)''', (user_id, invite_link, expires_at))
    conn.commit()
    conn.close()

def get_active_invite_link(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT invite_link, expires_at FROM invite_links 
                 WHERE user_id = ? AND expires_at > datetime('now') AND used = FALSE
                 ORDER BY created_at DESC LIMIT 1''', (user_id,))
    result = c.fetchone()
    conn.close()
    return result

def mark_invite_link_used(invite_link):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE invite_links SET used = TRUE WHERE invite_link = ?", (invite_link,))
    conn.commit()
    conn.close()

def send_message(chat_id, text, reply_markup=None):
    url = f"{BASE_URL}/sendMessage"
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        data["reply_markup"] = reply_markup
    try:
        response = requests.post(url, json=data, timeout=10)
        return response.json()
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")
        return None

# -------------------- Healthcheck для Render --------------------
def start_http_healthcheck():
    server = HTTPServer(("0.0.0.0", PORT), SimpleHTTPRequestHandler)
    print(f"🔹 Healthcheck server running on port {PORT}")
    server.serve_forever()

# -------------------- Основной цикл бота --------------------
def main():
    global last_update_id
    threading.Thread(target=start_http_healthcheck, daemon=True).start()
    
    print("🚀 Бот запущен, long-polling активен...")
    
    while True:
        try:
            # Здесь вставляется ваша логика get_updates() / handle_callback() / handle_message()
            time.sleep(0.5)
        except KeyboardInterrupt:
            print("🛑 Бот остановлен")
            break
        except Exception as e:
            print(f"⚠️ Ошибка: {e}")
            time.sleep(5)

# -------------------- Запуск --------------------
if __name__ == "__main__":
    main()
