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
    raise RuntimeError("BOT_TOKEN is not set")
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
CRYPTOBOT_API = "https://pay.crypt.bot/api"

DB_PATH = "bot_database.db"

# Актуальные цены (обновляются в реальном времени)
crypto_prices = {
    "BTC": 94000,    # Актуальная цена BTC ~$94,000 (декабрь 2024)
    "ETH": 3800,     # ~$3,800 за ETH
    "TON": 15.5,     # ~$15.5 за TON (актуальная цена декабря 2024)
    "USDT": 1.0
}
active_crypto_invoices = {}

# -------------------- Flask --------------------
app = Flask(__name__)

# -------------------- DB --------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, balance INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, channel_type TEXT, expires_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT, amount INTEGER, description TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS invite_links (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, invite_link TEXT UNIQUE, expires_at TIMESTAMP, used BOOLEAN DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    conn.commit()
    conn.close()

init_db()

# -------------------- Channels --------------------
CHANNELS = {
    "free": {
        "name": "🎯 ₽ROstava",
        "link": "https://t.me/prostavamillion",
        "description": "Бесплатные прогнозы и аналитика",
    },
    "premium": {
        "name": "💎 ₽ROstava PREMIUM",
        "description": "Эксклюзивные ставки и гарантированные прогнозы",
        "price_stars": 1000,
        "price_rub": 1649,
        "price_usd": 25,  # $25
        "duration_days": 30,
    },
}

# -------------------- Telegram API --------------------
def tg_post(method, payload):
    try:
        return requests.post(f"{BASE_URL}/{method}", json=payload, timeout=10).json()
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

def send_stars_invoice(chat_id, stars_amount, description):
    data = {
        "chat_id": chat_id,
        "title": f"⭐ {stars_amount} Telegram Stars",
        "description": description,
        "payload": f"stars_{stars_amount}",
        "provider_token": STARS_PROVIDER_TOKEN,
        "currency": "XTR",
        "prices": [{"label": "Stars", "amount": stars_amount}],
        "start_parameter": "stars",
    }
    return tg_post("sendInvoice", data)

def answer_pre_checkout_query(pre_checkout_query_id):
    return tg_post(
        "answerPreCheckoutQuery",
        {"pre_checkout_query_id": pre_checkout_query_id, "ok": True},
    )

# -------------------- DB helpers --------------------
def get_user_balance(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else 0

def update_user_balance(user_id, amount, username="", first_name=""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """INSERT OR REPLACE INTO users (user_id, username, first_name, balance) VALUES (?, ?, ?, COALESCE((SELECT balance FROM users WHERE user_id= ?),0)+?)""",
        (user_id, username, first_name, user_id, amount),
    )
    conn.commit()
    conn.close()

def add_transaction(user_id, ttype, amount, description):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO transactions (user_id,type,amount,description) VALUES (?,?,?,?)",
        (user_id, ttype, amount, description),
    )
    conn.commit()
    conn.close()

def create_user_subscription(user_id, channel_type, duration_days=30):
    expires_at = datetime.now() + timedelta(days=duration_days)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO subscriptions (user_id, channel_type, expires_at) VALUES (?,?,?)",
        (user_id, channel_type, expires_at),
    )
    conn.commit()
    conn.close()
    return expires_at

def save_invite_link(user_id, invite_link, expires_at):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO invite_links (user_id, invite_link, expires_at) VALUES (?,?,?)",
        (user_id, invite_link, expires_at),
    )
    conn.commit()
    conn.close()

def get_user_subscriptions(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT channel_type, expires_at FROM subscriptions WHERE user_id=? AND expires_at>datetime('now') ORDER BY expires_at DESC",
        (user_id,),
    )
    subs = []
    for row in c.fetchall():
        ch_type = row[0]
        ex = row[1]
        if isinstance(ex, str):
            ex = datetime.strptime(ex, "%Y-%m-%d %H:%M:%S")
        formatted = ex.strftime("%d.%m.%Y")
        name = CHANNELS.get(ch_type, {}).get("name", f"Канал({ch_type})")
        subs.append((name, formatted))
    conn.close()
    return subs

# -------------------- Invite link --------------------
def generate_invite_link(user_id, duration_days=30):
    try:
        chat_id = int(PRIVATE_CHANNEL_ID)
        expire_timestamp = int(time.time()) + duration_days * 24 * 3600
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
            save_invite_link(
                user_id, invite, datetime.now() + timedelta(days=duration_days)
            )
            return invite
    except:
        pass
    return None

# -------------------- Keyboards --------------------
def create_main_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "🎯 Бесплатный канал", "callback_data": "channel_free"}],
            [{"text": "💎 Премиум канал", "callback_data": "channel_premium"}],
            [{"text": "📊 Мои подписки", "callback_data": "my_subs"}],
        ]
    }

def create_premium_keyboard(user_id):
    bal = get_user_balance(user_id)
    ch = CHANNELS["premium"]
    kb = []
    if bal >= ch["price_stars"]:
        kb.append(
            [
                {
                    "text": f"⭐ Оплатить {ch['price_stars']} звёзд с баланса",
                    "callback_data": "pay_from_balance",
                }
            ]
        )
    kb.append(
        [
            {
                "text": f"💳 Купить {ch['price_stars']} звёзд",
                "callback_data": "buy_stars_for_sub",
            }
        ]
    )
    kb.append(
        [
            {
                "text": f"₿ Оплатить криптой",
                "callback_data": "pay_crypto_premium",
            }
        ]
    )
    kb.append([{"text": "🔙 Назад", "callback_data": "back_main"}])
    return {"inline_keyboard": kb}

def create_crypto_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "USDT", "callback_data": "crypto_USDT"}],
            [{"text": "TON", "callback_data": "crypto_TON"}],
            [{"text": "BTC", "callback_data": "crypto_BTC"}],
            [{"text": "ETH", "callback_data": "crypto_ETH"}],
            [{"text": "🔙 Назад", "callback_data": "channel_premium"}],
        ]
    }

# -------------------- CryptoBot --------------------
def update_crypto_prices_loop():
    global crypto_prices
    while True:
        try:
            response = requests.get(
                "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,toncoin,tether&vs_currencies=usd",
                timeout=10,
            )
            if response.status_code == 200:
                data = response.json()
                crypto_prices["BTC"] = data["bitcoin"]["usd"]
                crypto_prices["ETH"] = data["ethereum"]["usd"]
                crypto_prices["TON"] = data["toncoin"]["usd"]
                crypto_prices["USDT"] = 1.0
                print(f"💰 Updated prices: BTC=${crypto_prices['BTC']}, ETH=${crypto_prices['ETH']}, TON=${crypto_prices['TON']}")
            else:
                # Fallback цены (актуальные на декабрь 2024)
                crypto_prices.update({
                    "BTC": 94000,    # Актуальная цена BTC ~$94,000
                    "ETH": 3800,     # ETH ~$3,800
                    "TON": 15.5,     # TON ~$15.5 (актуальная цена)
                    "USDT": 1.0
                })
                print("⚠️ Using fallback prices")
        except Exception as e:
            print("Error updating prices:", e)
            # Актуальные fallback цены
            crypto_prices.update({
                "BTC": 94000,    # $94,000 за BTC (актуально на декабрь 2024)
                "ETH": 3800,     # $3,800 за ETH
                "TON": 15.5,     # $15.5 за TON (актуальная цена)
                "USDT": 1.0
            })
        time.sleep(300)  # Обновляем каждые 5 минут

def get_crypto_amounts(price_usd):
    """Возвращает правильные суммы для всех криптовалют с проверкой"""
    global crypto_prices
    
    # Проверяем, что цены актуальные
    if crypto_prices["BTC"] < 10000:  # Если цена подозрительно низкая
        print("⚠️ Suspicious BTC price, using fallback")
        crypto_prices["BTC"] = 94000
    
    if crypto_prices["TON"] < 5:  # Если цена TON подозрительно низкая
        print("⚠️ Suspicious TON price, using fallback")
        crypto_prices["TON"] = 15.5
    
    amounts = {
        "BTC": round(price_usd / crypto_prices["BTC"], 8),  # Больше знаков для BTC
        "ETH": round(price_usd / crypto_prices["ETH"], 6),
        "TON": round(price_usd / crypto_prices["TON"], 4),  # Увеличили точность для TON
        "USDT": round(price_usd, 2)
    }
    
    print(f"💱 Calculated amounts for ${price_usd}: {amounts}")
    return amounts

def create_crypto_invoice(price_usd, currency="USDT", description="Подписка"):
    amounts = get_crypto_amounts(price_usd)
    amount = amounts.get(currency)
    
    if amount is None:
        print(f"❌ Cannot calculate amount for {currency}")
        return None

    print(f"💱 Creating {currency} invoice: {amount} {currency} for ${price_usd}")

    url = f"{CRYPTOBOT_API}/createInvoice"
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN,
        "Content-Type": "application/json",
    }

    payload = {
        "asset": currency,
        "amount": str(amount),
        "description": description,
        "payload": str(int(time.time())),
        "allow_comments": False,
        "allow_anonymous": False,
        "expires_in": 3600,
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        result = response.json()
        print(f"📄 CryptoBot {currency} response: {result}")
        
        if result.get("ok"):
            return result["result"]
        else:
            error_msg = result.get("error", "Unknown error")
            print(f"❌ CryptoBot error for {currency}: {error_msg}")
            return None
    except Exception as e:
        print(f"❌ CryptoBot API error for {currency}: {e}")
        return None

def check_crypto_invoice(invoice_id):
    url = f"{CRYPTOBOT_API}/getInvoices"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    try:
        r = requests.get(
            url, headers=headers, params={"invoice_ids": invoice_id}, timeout=10
        )
        res = r.json()
        if res.get("ok") and res["result"]["items"]:
            return res["result"]["items"][0]
    except Exception as e:
        print(f"Error checking invoice: {e}")
    return None

def crypto_checker_loop():
    while True:
        now = time.time()
        to_remove = []
        for inv_id, info in list(active_crypto_invoices.items()):
            if now - info["created_at"] > 2 * 3600:
                to_remove.append(inv_id)
                continue
            inv_info = check_crypto_invoice(inv_id)
            if inv_info and inv_info.get("status") == "paid":
                user_id = info["user_id"]
                chat_id = info["chat_id"]
                dur = info.get("duration_days", 30)
                create_user_subscription(user_id, "premium", dur)
                invite = generate_invite_link(user_id, dur)
                msg = f"🎉 <b>Оплата подтверждена!</b>\n💎 Подписка {dur} дней\n📅 До {(datetime.now()+timedelta(days=dur)).strftime('%d.%m.%Y')}"
                if invite:
                    msg += f"\n🔗 Ваша ссылка: {invite}\n⚠️ Ссылка действительна только для одного использования!"
                send_message(chat_id, msg)
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
        answer_pre_checkout_query(update["pre_checkout_query"]["id"])
    elif "successful_payment" in update:
        handle_successful_payment(update)

def handle_successful_payment(update):
    msg = update.get("message", {})
    user = msg.get("from", {})
    chat_id = msg.get("chat", {}).get("id")
    user_id = user.get("id")
    payment_info = update.get("successful_payment", {})
    if not payment_info:
        return
    total_amount = payment_info.get("total_amount", 0)
    update_user_balance(user_id, total_amount)
    add_transaction(user_id, "deposit", total_amount, "Пополнение через Telegram Stars")
    send_message(
        chat_id,
        f"✅ Баланс пополнен на {total_amount} ⭐\n💰 На балансе: {get_user_balance(user_id)} ⭐",
        create_main_keyboard(),
    )

def handle_message(message):
    chat_id = message["chat"]["id"]
    user = message.get("from", {})
    user_id = user.get("id")
    text = message.get("text", "")
    update_user_balance(
        user_id, 0, user.get("username", ""), user.get("first_name", "")
    )
    if text == "/start":
        send_message(
            chat_id,
            f"👋 Привет, {user.get('first_name','')}!\nВыберите действие:",
            create_main_keyboard(),
        )
    elif text == "/mysub":
        subs = get_user_subscriptions(user_id)
        if subs:
            reply = "📋 <b>Ваши подписки:</b>\n\n"
            for ch, ex in subs:
                reply += f"• {ch}\n   └─ до <b>{ex}</b>\n"
            send_message(chat_id, reply)
        else:
            send_message(chat_id, "❌ У вас нет активных подписок.")
    else:
        send_message(
            chat_id, "🤖 Не распознана команда. Нажмите /start.", create_main_keyboard()
        )

def handle_callback(callback):
    data = callback.get("data")
    user_id = callback.get("from", {}).get("id")
    chat_id = callback.get("message", {}).get("chat", {}).get("id")
    cb_id = callback.get("id")
    ch = CHANNELS["premium"]

    if data == "channel_free":
        chf = CHANNELS["free"]
        send_message(
            chat_id, f"<b>{chf['name']}</b>\n\n{chf['description']}\n\n{chf['link']}"
        )
    elif data == "channel_premium":
        bal = get_user_balance(user_id)
        amounts = get_crypto_amounts(ch["price_usd"])
        txt = (f"<b>{ch['name']}</b>\n\n{ch['description']}\n\n"
               f"💎 Стоимость: {ch['price_stars']} ⭐ (~{ch['price_rub']} ₽)\n"
               f"💰 На балансе: {bal} ⭐\n\n"
               f"<b>Криптовалюты ($25):</b>\n"
               f"• BTC: {amounts['BTC']:.8f}\n"
               f"• ETH: {amounts['ETH']:.6f}\n" 
               f"• TON: {amounts['TON']:.4f}\n"
               f"• USDT: {amounts['USDT']:.2f}")
        send_message(chat_id, txt, create_premium_keyboard(user_id))
    elif data == "pay_from_balance":
        bal = get_user_balance(user_id)
        if bal >= ch["price_stars"]:
            update_user_balance(user_id, -ch["price_stars"])
            add_transaction(
                user_id, "subscription", -ch["price_stars"], "Оплата подписки со счета"
            )
            expires_at = create_user_subscription(
                user_id, "premium", ch["duration_days"]
            )
            invite = generate_invite_link(user_id, ch["duration_days"])
            msg = f"✅ <b>Подписка активирована!</b>\n💎 Канал: {ch['name']}\n📅 Действует до: {expires_at.strftime('%d.%m.%Y')}"
            if invite:
                msg += f"\n🔗 Ваша ссылка: {invite}\n⚠️ Ссылка действительна только для одного использования!"
            send_message(chat_id, msg)
        else:
            send_message(
                chat_id,
                f"❌ Недостаточно звёзд. Нужно {ch['price_stars']} ⭐, у вас {bal} ⭐",
            )
    elif data == "buy_stars_for_sub":
        stars = ch["price_stars"]
        inv = send_stars_invoice(chat_id, stars, f"Покупка {stars} звёзд для подписки")
        if inv and inv.get("ok"):
            send_message(
                chat_id, "📋 Инвойс отправлен. Следуйте инструкциям Telegram оплаты."
            )
        else:
            send_message(
                chat_id,
                "❌ Не удалось создать инвойс (проверьте STARS_PROVIDER_TOKEN).",
            )
    elif data == "pay_crypto_premium":
        send_message(chat_id, "Выберите валюту:", create_crypto_keyboard())
    elif data.startswith("crypto_"):
        cur = data.split("_")[1].upper()
        amounts = get_crypto_amounts(ch["price_usd"])
        amount = amounts.get(cur)
        
        if amount is None:
            send_message(chat_id, f"❌ Не удалось рассчитать сумму для {cur}")
            answer_callback_query(cb_id)
            return

        invoice = create_crypto_invoice(
            ch["price_usd"], cur, f"Подписка {ch['name']} на {ch['duration_days']} дней"
        )
        if invoice:
            inv_id = invoice.get("invoice_id") or invoice.get("id")
            active_crypto_invoices[inv_id] = {
                "user_id": user_id,
                "chat_id": chat_id,
                "created_at": time.time(),
                "duration_days": ch["duration_days"],
            }

            # Форматируем сумму в зависимости от валюты
            if cur == "BTC":
                amount_str = f"{amount:.8f}"
            elif cur == "ETH":
                amount_str = f"{amount:.6f}"
            elif cur == "TON":
                amount_str = f"{amount:.4f}"
            else:
                amount_str = f"{amount:.2f}"

            send_message(
                chat_id,
                f"💎 <b>Оплата подписки</b>\n\n"
                f"💰 Сумма: {amount_str} {cur}\n"
                f"💵 Примерно: {ch['price_usd']} USD\n"
                f"🔗 Ссылка для оплаты: {invoice.get('pay_url')}\n\n"
                f"После оплаты подписка активируется автоматически в течение 1-2 минут.",
            )
        else:
            send_message(
                chat_id,
                f"❌ Ошибка создания инвойса для {cur}. Попробуйте другую валюту.",
            )
    elif data == "my_subs":
        subs = get_user_subscriptions(user_id)
        if not subs:
            send_message(
                chat_id, "❌ У вас нет активных подписок.", create_main_keyboard()
            )
        else:
            txt = "📋 <b>Ваши активные подписки:</b>\n\n"
            for chn, ex in subs:
                txt += f"• {chn}\n   └─ до <b>{ex}</b>\n"
            txt += "\nДля продления выберите канал в главном меню."
            send_message(chat_id, txt, create_main_keyboard())
    elif data == "back_main":
        send_message(chat_id, "Главное меню", create_main_keyboard())
    answer_callback_query(cb_id)

# -------------------- Webhook --------------------
@app.route("/", methods=["GET"])
def index():
    return "OK"

@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json(force=True)
        threading.Thread(target=handle_update, args=(update,), daemon=True).start()
    except Exception as e:
        print("webhook exception", e)
    return jsonify({"ok": True})

def set_webhook():
    if not SELF_URL:
        return
    webhook_url = f"{SELF_URL}/webhook/{BOT_TOKEN}"
    res = tg_post(
        "setWebhook",
        {
            "url": webhook_url,
            "allowed_updates": [
                "message",
                "callback_query",
                "pre_checkout_query",
                "successful_payment",
            ],
        },
    )
    print("setWebhook response:", res)

# -------------------- Startup --------------------
if __name__ == "__main__":
    set_webhook()
    threading.Thread(target=crypto_checker_loop, daemon=True).start()
    threading.Thread(target=update_crypto_prices_loop, daemon=True).start()
    print(f"Starting Flask on 0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT)
