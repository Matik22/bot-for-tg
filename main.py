import os
import threading
import time
import sqlite3
from datetime import datetime, timedelta
import requests
from flask import Flask, request, jsonify

# -------------------- Настройки --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN")
STARS_PROVIDER_TOKEN = os.getenv("STARS_PROVIDER_TOKEN", "")
PRIVATE_CHANNEL_ID = "-1003176208290"
SELF_URL = os.getenv("SELF_URL")
PORT = int(os.getenv("PORT", "8080"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in env")

BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
CRYPTOBOT_API = "https://pay.crypt.bot/api"
DB_PATH = "bot_database.db"

# -------------------- Flask app --------------------
app = Flask(__name__)

# -------------------- DB init --------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS transactions
           (id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT,
            amount INTEGER,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS invite_links
           (id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            invite_link TEXT UNIQUE,
            expires_at TIMESTAMP,
            used BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    conn.commit()
    conn.close()

init_db()

# -------------------- Configs --------------------
CHANNELS = {
    "free": {
        "name": "🎯 ₽ROstava",
        "link": "https://t.me/prostavamillion",
        "description": "Бесплатные прогнозы и аналитика",
    },
    "premium": {
        "name": "💎 ₽ROstava PREMIUM",
        "description": "Эксклюзивные ставки и гарантированные прогнозы",
        "price_stars": 1000,  # списание со звёзд
        "price_rub": 1649,    # отображение при оплате звёздами
        "price_usd": 25,      # крипта/фиат
        "duration_days": 30,
    },
}

active_crypto_invoices = {}

# -------------------- Telegram API --------------------
def tg_post(method, payload):
    url = f"{BASE_URL}/{method}"
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.json()
    except Exception as e:
        print("tg_post error", e)
        return None

def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        data["reply_markup"] = reply_markup
    return tg_post("sendMessage", data)

def answer_callback_query(callback_id, text=None, show_alert=False):
    data = {"callback_query_id": callback_id, "show_alert": show_alert}
    if text:
        data["text"] = text
    return tg_post("answerCallbackQuery", data)

# -------------------- DB helpers --------------------
def get_user_balance(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else 0

def update_user_balance(user_id, amount, username="", first_name=""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """INSERT OR REPLACE INTO users (user_id, username, first_name, balance)
           VALUES (?, ?, ?, COALESCE((SELECT balance FROM users WHERE user_id = ?), 0) + ?)""",
        (user_id, username, first_name, user_id, amount),
    )
    conn.commit()
    conn.close()

def add_transaction(user_id, ttype, amount, description):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO transactions (user_id, type, amount, description) VALUES (?, ?, ?, ?)",
        (user_id, ttype, amount, description),
    )
    conn.commit()
    conn.close()

def create_user_subscription(user_id, channel_type, duration_days=30):
    expires_at = datetime.now() + timedelta(days=duration_days)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO subscriptions (user_id, channel_type, expires_at) VALUES (?, ?, ?)",
        (user_id, channel_type, expires_at),
    )
    conn.commit()
    conn.close()
    return expires_at

def save_invite_link(user_id, invite_link, expires_at):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO invite_links (user_id, invite_link, expires_at) VALUES (?, ?, ?)",
        (user_id, invite_link, expires_at),
    )
    conn.commit()
    conn.close()

def get_user_subscriptions(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT channel_type, expires_at FROM subscriptions WHERE user_id = ? AND expires_at > datetime('now') ORDER BY expires_at DESC",
        (user_id,),
    )
    subscriptions = []
    for row in c.fetchall():
        channel_type, expires_at = row
        try:
            expires_date = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
        except:
            expires_date = expires_at
        formatted_date = expires_date.strftime("%d.%m.%Y") if isinstance(expires_date, datetime) else str(expires_date)
        if channel_type == "premium":
            channel_name = CHANNELS["premium"]["name"]
        elif channel_type == "free":
            channel_name = CHANNELS["free"]["name"]
        else:
            channel_name = f"Канал ({channel_type})"
        subscriptions.append((channel_name, formatted_date))
    conn.close()
    return subscriptions

# -------------------- Invite link --------------------
def generate_invite_link(user_id, duration_days=30):
    try:
        chat_id = int(PRIVATE_CHANNEL_ID)
        expire_timestamp = int(time.time()) + duration_days*24*3600
        data = {
            "chat_id": chat_id,
            "name": f"Premium for user_{user_id}",
            "expire_date": expire_timestamp,
            "member_limit": 1,
            "creates_join_request": False,
        }
        res = tg_post("createChatInviteLink", data)
        if res and res.get("ok"):
            invite = res["result"]["invite_link"]
            expires_at = datetime.now() + timedelta(days=duration_days)
            save_invite_link(user_id, invite, expires_at)
            return invite
    except Exception as e:
        print("Invite error:", e)
    return None

# -------------------- Keyboards --------------------
def create_main_keyboard():
    return {"inline_keyboard":[
        [{"text":"🎯 Бесплатный канал","callback_data":"channel_free"}],
        [{"text":"💎 Премиум канал","callback_data":"channel_premium"}],
        [{"text":"📊 Мои подписки","callback_data":"my_subs"}],
    ]}

def create_premium_keyboard(user_id):
    user_balance = get_user_balance(user_id)
    ch = CHANNELS["premium"]
    kb = []
    if user_balance >= ch["price_stars"]:
        kb.append([{"text": f"⭐ Оплатить {ch['price_stars']} ⭐ (≈{ch['price_rub']}₽)", "callback_data":"pay_from_balance"}])
    kb.append([{"text": f"💳 Купить {ch['price_stars']} ⭐", "callback_data":"buy_stars_for_sub"}])
    kb.append([{"text": f"₿ Оплатить ${ch['price_usd']} криптой/фиатом", "callback_data":"pay_crypto_premium"}])
    kb.append([{"text":"🔙 Назад","callback_data":"back_main"}])
    return {"inline_keyboard": kb}

def create_crypto_keyboard():
    return {"inline_keyboard":[
        [{"text":"USDT","callback_data":"crypto_USDT"}],
        [{"text":"TON","callback_data":"crypto_TON"}],
        [{"text":"BTC","callback_data":"crypto_BTC"}],
        [{"text":"ETH","callback_data":"crypto_ETH"}],
        [{"text":"🔙 Назад","callback_data":"channel_premium"}],
    ]}

# -------------------- Payments --------------------
def send_stars_invoice(chat_id, stars_amount, description):
    data = {
        "chat_id": chat_id,
        "title": f"⭐ {stars_amount} Telegram Stars",
        "description": description,
        "payload": f"stars_{stars_amount}",
        "provider_token": STARS_PROVIDER_TOKEN,
        "currency": "XTR",
        "prices": [{"label":"Stars","amount":stars_amount}],
        "start_parameter":"stars",
    }
    return tg_post("sendInvoice", data)

def answer_pre_checkout_query(pre_checkout_query_id):
    return tg_post("answerPreCheckoutQuery", {"pre_checkout_query_id": pre_checkout_query_id, "ok": True})

def create_crypto_invoice(amount_usd, currency="USDT", description="Подписка"):
    url = f"{CRYPTOBOT_API}/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN, "Content-Type":"application/json"}
    # курсы для расчёта суммы
    rates = {"USDT":1.0,"TON":1.8,"BTC":58000.0,"ETH":3000.0}
    if currency not in rates:
        return None
    amt = round(amount_usd / rates[currency], 6 if currency in ["BTC"] else 4)
    payload = {"asset":currency,"amount":str(amt),"description":description,"payload":str(int(time.time())),"allow_comments":False,"allow_anonymous":False,"expires_in":3600}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        res = r.json()
        if res.get("ok"):
            return res["result"]
    except Exception as e:
        print("CryptoBot create exception:", e)
    return None

def check_crypto_invoice(invoice_id):
    url = f"{CRYPTOBOT_API}/getInvoices"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    params = {"invoice_ids": invoice_id}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        res = r.json()
        if res.get("ok") and res["result"]["items"]:
            return res["result"]["items"][0]
    except Exception as e:
        print("CryptoBot check exception:", e)
    return None

# -------------------- Crypto checker --------------------
def crypto_checker_loop():
    while True:
        now = time.time()
        to_remove = []
        for inv_id, info in list(active_crypto_invoices.items()):
            if now - info["created_at"] > 2*3600:
                to_remove.append(inv_id)
                continue
            inv_info = check_crypto_invoice(inv_id)
            if inv_info and inv_info.get("status") == "paid":
                user_id = info["user_id"]
                chat_id = info["chat_id"]
                ch_type = info.get("channel_type","premium")
                days = info.get("duration_days",30)
                expires_at = create_user_subscription(user_id, ch_type, days)
                formatted_date = expires_at.strftime("%d.%m.%Y")
                invite = generate_invite_link(user_id, days)
                msg = f"🎉 <b>Оплата подтверждена!</b>\n💎 Подписка активирована на {days} дней\n📅 Действует до: {formatted_date}"
                if invite:
                    msg += f"\n🔗 <b>Ваша ссылка:</b>\n{invite}\n⚠️ Ссылка одноразовая"
                else:
                    msg += "\n❌ Не удалось создать ссылку"
                send_message(chat_id,msg)
                to_remove.append(inv_id)
        for rid in to_remove:
            active_crypto_invoices.pop(rid, None)
        time.sleep(30)

# -------------------- Handlers --------------------
def handle_update(update):
    if "message" in update:
        handle_message(update["message"])
    elif "callback_query" in update:
        handle_callback(update["callback_query"])
    elif "pre_checkout_query" in update:
        pq = update["pre_checkout_query"]
        answer_pre_checkout_query(pq["id"])
    elif "successful_payment" in update:
        msg = update.get("message",{})
        chat_id = msg.get("chat",{}).get("id")
        user_id = msg.get("from",{}).get("id")
        info = update.get("successful_payment",{})
        total_amount = info.get("total_amount",0)
        update_user_balance(user_id,total_amount)
        add_transaction(user_id,"deposit",total_amount,"Пополнение через Telegram Stars")
        send_message(chat_id,f"✅ Баланс пополнен на {total_amount} ⭐\n💰 Теперь ваш баланс: {get_user_balance(user_id)} ⭐",create_main_keyboard())

# -------------------- Webhook --------------------
@app.route("/", methods=["GET"])
def index(): return "OK"

@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json(force=True)
        threading.Thread(target=handle_update,args=(update,),daemon=True).start()
    except Exception as e:
        print("Webhook error:", e)
    return jsonify({"ok": True})

def set_webhook():
    if not SELF_URL:
        print("SELF_URL not set — webhook not registered.")
        return
    webhook_url = f"{SELF_URL}/webhook/{BOT_TOKEN}"
    res = tg_post("setWebhook",{"url":webhook_url,"allowed_updates":["message","callback_query","pre_checkout_query","successful_payment"]})
    print("setWebhook response:",res)

# -------------------- Startup --------------------
if __name__=="__main__":
    set_webhook()
    threading.Thread(target=crypto_checker_loop,daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
