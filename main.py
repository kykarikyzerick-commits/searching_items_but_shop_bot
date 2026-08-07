import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
import aiohttp
from aiohttp import web

logging.basicConfig(level=logging.INFO)

# ==================== НАЛАШТУВАННЯ ====================
BOT_TOKEN = "8877190549:AAEoSIj_dOL2hi-PpDrfZFJi6h8x40hJnFQ"
ADMIN_ID = 8138110821
CHECK_INTERVAL = 5

# Список Telegram ID користувачів з постійним доступом
ALLOWED_USERS = [
    8138110821,  # Ваш ID (Адмін)
]

# Постійні ключі (працюють ЗАВЖДИ навіть після перезапуску Render)
MASTER_KEYS = {
    "VINTED-VIP-2026": 365,   # Ключ на 1 рік
    "VINTED-FREE-TEST": 30,   # Ключ на 30 днів
    "VINTED-KEY-7DAYS": 7     # Ключ на 7 днів
}

POPULAR_BRANDS = [
    "Nike", "Adidas", "Stone Island", "Carhartt", 
    "Jordan", "Stussy", "Trapstar", "The North Face"
]

SIZES = ["XS", "S", "M", "L", "XL", "XXL"]

DOMAINS = {
    "🇵🇱 Польща": "pl",
    "🇨🇿 Чехія": "cz",
    "🇱🇹 Литва": "lt",
    "🇷🇴 Румунія": "ro",
    "🇩🇪 Німеччина": "de",
    "🇫🇷 Франція": "fr",
    "🇬🇧 Великобританія": "co.uk"
}

# Сервер для підтримання роботи на Render (24/7)
async def health_check(request):
    return web.Response(text="Bot is running 24/7!")

def init_db():
    conn = sqlite3.connect("licenses.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            key TEXT PRIMARY KEY,
            duration_days INTEGER,
            is_used INTEGER DEFAULT 0,
            used_by INTEGER,
            expires_at DATETIME
        )
    """)
    conn.commit()
    conn.close()

def is_user_active(user_id):
    if user_id in ALLOWED_USERS or user_id == ADMIN_ID:
        return True

    conn = sqlite3.connect("licenses.db")
    cursor = conn.cursor()
    cursor.execute("SELECT expires_at FROM keys WHERE used_by = ? AND is_used = 1", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0]:
        try:
            exp_date = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
            return exp_date > datetime.now()
        except Exception:
            return False
    return False

def load_settings():
    if os.path.exists("settings.json"):
        try:
            with open("settings.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_settings(settings):
    with open("settings.json", "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=4)

user_settings = load_settings()
user_states = {}
seen_items = set()
last_update_id = 0

# ==================== КЛАВІАТУРИ ====================
def get_main_keyboard(user_id):
    kb = []
    if not is_user_active(user_id):
        kb.append([{"text": "🔑 Активувати ключ"}, {"text": "🛒 Придбати ключ"}])
        return {"keyboard": kb, "resize_keyboard": True, "persistent": True}

    kb.append([{"text": "🏷 Обрати бренд"}, {"text": "📏 Обрати розмір"}])
    kb.append([{"text": "🌍 Обрати регіон"}, {"text": "📋 Мої налаштування"}])
    kb.append([{"text": "▶️ Запустити"}, {"text": "⏹ Зупинити"}])
    kb.append([{"text": "🔑 Активувати новий ключ"}, {"text": "🛒 Придбати ключ"}])
    if user_id == ADMIN_ID:
        kb.append([{"text": "👑 Адмін-панель"}])
    return {"keyboard": kb, "resize_keyboard": True, "persistent": True}

def get_brands_keyboard():
    buttons = []
    row = []
    for brand in POPULAR_BRANDS:
        row.append({"text": brand, "callback_data": f"set_brand:{brand}"})
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([{"text": "✏️ Ввести свій бренд", "callback_data": "custom_brand"}])
    return {"inline_keyboard": buttons}

def get_sizes_keyboard(selected_sizes):
    buttons = []
    row = []
    for size in SIZES:
        prefix = "✅ " if size in selected_sizes else ""
        row.append({"text": f"{prefix}{size}", "callback_data": f"toggle_size:{size}"})
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([{"text": "👌 Готово", "callback_data": "close_size_menu"}])
    return {"inline_keyboard": buttons}

def get_region_keyboard(current_domain):
    buttons = []
    for name, code in DOMAINS.items():
        prefix = "✅ " if code == current_domain else ""
        buttons.append([{"text": f"{prefix}{name}", "callback_data": f"set_reg:{code}"}])
    return {"inline_keyboard": buttons}

def get_item_keyboard(item_url, seller_url):
    return {
        "inline_keyboard": [
            [{"text": "⚡ КУПИТИ НА VINTED", "url": item_url}],
            [{"text": "💬 Профіль продавця", "url": seller_url}]
        ]
    }

# ==================== TELEGRAM API ====================
async def send_telegram_message(session, chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    async with session.post(url, json=payload) as resp:
        return await resp.json()

async def send_telegram_photo(session, chat_id, photo_url, caption, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    payload = {"chat_id": chat_id, "photo": photo_url, "caption": caption, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    async with session.post(url, json=payload) as resp:
        return await resp.json()

# ==================== ОБРОБКА ПОВІДОМЛЕНЬ ====================
async def handle_update(session, update):
    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "").strip()
        uid_str = str(chat_id)

        logging.info(f"Отримано повідомлення від {chat_id}: {text}")

        # Першочергова обробка старт/меню для всіх
        if text in ["/start", "меню", "Start", "start"]:
            await send_telegram_message(
                session, 
                chat_id, 
                "👋 **Ласкаво просимо!** Оберіть дію в меню нижче:", 
                get_main_keyboard(chat_id)
            )
            return

        state = user_states.get(chat_id)

        # Генерація ключа (Адмін)
        if state == "waiting_for_key_gen" and chat_id == ADMIN_ID:
            try:
                days = int(text)
                import uuid
                new_key = f"VINTED-{uuid.uuid4().hex[:8].upper()}"
                conn = sqlite3.connect("licenses.db")
                cursor = conn.cursor()
                cursor.execute("INSERT INTO keys (key, duration_days) VALUES (?, ?)", (new_key, days))
                conn.commit()
                conn.close()
                await send_telegram_message(session, chat_id, f"🔑 **Згенеровано тимчасовий ключ:** `{new_key}` на {days} днів.", get_main_keyboard(chat_id))
            except ValueError:
                await send_telegram_message(session, chat_id, "❌ Введіть число днів цифрою.")
            user_states[chat_id] = None
            return

        # Активація ключа
        if state == "waiting_for_key" or text.startswith("VINTED-"):
            days_to_add = None

            if text in MASTER_KEYS:
                days_to_add = MASTER_KEYS[text]
            else:
                conn = sqlite3.connect("licenses.db")
                cursor = conn.cursor()
                cursor.execute("SELECT duration_days, is_used FROM keys WHERE key = ?", (text,))
                row = cursor.fetchone()
                if row and row[1] == 0:
                    days_to_add = row[0]
                    cursor.execute("UPDATE keys SET is_used = 1 WHERE key = ?", (text,))
                    conn.commit()
                conn.close()

            if days_to_add:
                exp_date = datetime.now() + timedelta(days=days_to_add)
                exp_str = exp_date.strftime("%Y-%m-%d %H:%M:%S")

                conn = sqlite3.connect("licenses.db")
                cursor = conn.cursor()
                cursor.execute("INSERT OR REPLACE INTO keys (key, duration_days, is_used, used_by, expires_at) VALUES (?, ?, 1, ?, ?)",
                               (text, days_to_add, chat_id, exp_str))
                conn.commit()
                conn.close()

                await send_telegram_message(session, chat_id, f"🎉 **Ключ успішно активовано на {days_to_add} днів!**\n\nВам відкрито повне меню бота.", get_main_keyboard(chat_id))
                user_states[chat_id] = None
            else:
                await send_telegram_message(session, chat_id, "❌ **Невірний або вже використаний ключ.**", get_main_keyboard(chat_id))
            return

        # Перевірка наявності підписки
        if not is_user_active(chat_id):
            if text in ["🔑 Активувати ключ", "🔑 Активувати новий ключ"]:
                user_states[chat_id] = "waiting_for_key"
                await send_telegram_message(session, chat_id, "Надішліть ваш ключ у відповідь на це повідомлення:")
            elif text == "🛒 Придбати ключ":
                await send_telegram_message(session, chat_id, "💳 Для купівлі ключа доступу пишіть сюди: @but_sh0ping", get_main_keyboard(chat_id))
            else:
                await send_telegram_message(
                    session, 
                    chat_id, 
                    "🔒 **Доступ обмежено!**\n\nДля роботи з ботом необхідно активувати ключ.\nНатисніть **🔑 Активувати ключ** або напишіть @but_sh0ping для купівлі.", 
                    get_main_keyboard(chat_id)
                )
            return

        # Меню авторизованого користувача
        if state == "waiting_custom_brand":
            user_settings.setdefault(uid_str, {})["brand"] = text
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, f"✅ Бренд встановлено: *{text}*", get_main_keyboard(chat_id))
            user_states[chat_id] = None
            return

        elif "👑 Адмін-панель" in text and chat_id == ADMIN_ID:
            user_states[chat_id] = "waiting_for_key_gen"
            await send_telegram_message(session, chat_id, "Введіть термін дії ключа у днях (наприклад: 1, 3, 7, 30):")

        elif text in ["🔑 Активувати ключ", "🔑 Активувати новий ключ"]:
            user_states[chat_id] = "waiting_for_key"
            await send_telegram_message(session, chat_id, "Надішліть ваш ключ активації:")

        elif text == "🛒 Придбати ключ":
            await send_telegram_message(session, chat_id, "💳 Для купівлі ключа доступу пишіть сюди: @but_sh0ping", get_main_keyboard(chat_id))

        elif "🏷 Обрати бренд" in text:
            await send_telegram_message(session, chat_id, "Оберіть бренд або введіть свій:", get_brands_keyboard())

        elif "📏 Обрати розмір" in text:
            selected = user_settings.get(uid_str, {}).get("sizes", [])
            await send_telegram_message(session, chat_id, "Оберіть розміри:", get_sizes_keyboard(selected))

        elif "🌍 Обрати регіон" in text:
            curr = user_settings.get(uid_str, {}).get("domain", "pl")
            await send_telegram_message(session, chat_id, "Оберіть регіон:", get_region_keyboard(curr))

        elif "📋 Мої налаштування" in text:
            cfg = user_settings.get(uid_str, {})
            brand = cfg.get("brand", "Не обрано")
            sizes = ", ".join(cfg.get("sizes", [])) or "Всі"
            domain = cfg.get("domain", "pl").upper()
            status = "🟢 Активний" if cfg.get("active") else "🔴 Зупинений"
            
            info = f"⚙️ **Налаштування:**\n\n🏷 **Бренд:** {brand}\n📏 **Розміри:** {sizes}\n🌍 **Регіон:** {domain}\n📡 **Статус:** {status}"
            await send_telegram_message(session, chat_id, info, get_main_keyboard(chat_id))

        elif "▶️ Запустити" in text:
            cfg = user_settings.get(uid_str, {})
            if not cfg.get("brand"):
                await send_telegram_message(session, chat_id, "⚠️ Спочатку оберіть бренд!", get_main_keyboard(chat_id))
                return
            user_settings.setdefault(uid_str, {})["active"] = True
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, "🚀 Пошук запущено!", get_main_keyboard(chat_id))

        elif "⏹ Зупинити" in text:
            if uid_str in user_settings:
                user_settings[uid_str]["active"] = False
                save_settings(user_settings)
            await send_telegram_message(session, chat_id, "⏹ Пошук зупинено.", get_main_keyboard(chat_id))

    elif "callback_query" in update:
        cb = update["callback_query"]
        chat_id = cb["message"]["chat"]["id"]
        data = cb.get("data", "")
        uid_str = str(chat_id)

        if not is_user_active(chat_id):
            return

        user_settings.setdefault(uid_str, {})

        if data.startswith("set_brand:"):
            brand = data.split(":")[1]
            user_settings[uid_str]["brand"] = brand
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, f"✅ Обрано бренд: *{brand}*", get_main_keyboard(chat_id))

        elif data == "custom_brand":
            user_states[chat_id] = "waiting_custom_brand"
            await send_telegram_message(session, chat_id, "Напишіть назву бренду у відповідь:")

        elif data.startswith("toggle_size:"):
            size = data.split(":")[1]
            sizes = user_settings[uid_str].get("sizes", [])
            if size in sizes: sizes.remove(size)
            else: sizes.append(size)
            user_settings[uid_str]["sizes"] = sizes
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, "Збережено", get_sizes_keyboard(sizes))

        elif data == "close_size_menu":
            sizes = ", ".join(user_settings[uid_str].get("sizes", [])) or "всі"
            await send_telegram_message(session, chat_id, f"👌 Розміри збережено: *{sizes}*", get_main_keyboard(chat_id))

        elif data.startswith("set_reg:"):
            code = data.split(":")[1]
            user_settings[uid_str]["domain"] = code
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, f"✅ Регіон: *{code.upper()}*", get_main_keyboard(chat_id))

# ==================== ОПИТУВАННЯ TELEGRAM ====================
async def handle_telegram_commands(session):
    global last_update_id
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": last_update_id + 1, "timeout": 2}
    try:
        async with session.get(url, params=params) as resp:
            data = await resp.json()
            if data.get("ok") and data.get("result"):
                for update in data["result"]:
                    last_update_id = update["update_id"]
                    await handle_update(session, update)
    except Exception as e:
        logging.error(f"Помилка Telegram API: {e}")

async def clear_old_updates(session):
    global last_update_id
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    try:
        async with session.get(url, params={"offset": -1}) as resp:
            data = await resp.json()
            if data.get("ok") and data.get("result"):
                last_update_id = data["result"][-1]["update_id"]
    except Exception as e:
        logging.error(f"Очищення: {e}")

# ==================== ПАРСИНГ VINTED ====================
async def fetch_vinted(session):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    for uid_str, config in user_settings.items():
        if not config.get("active") or not config.get("brand"):
            continue

        user_id = int(uid_str)
        if not is_user_active(user_id):
            continue

        domain = config.get("domain", "pl")
        brand = config.get("brand")
        user_sizes = config.get("sizes", [])

        api_url = f"https://www.vinted.{domain}/api/v2/catalog/items?search_text={brand}&order=newest_first"

        try:
            async with session.get(api_url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("items", [])

                    for item in items[:10]:
                        if item.get("promoted") or item.get("is_promoted"):
                            continue

                        item_id = item.get("id")
                        if item_id in seen_items:
                            continue

                        size_title = str(item.get("size_title", "")).upper()
                        if user_sizes:
                            if not any(s.upper() in size_title for s in user_sizes):
                                continue

                        seen_items.add(item_id)

                        title = item.get("title", "Без назви")
                        item_brand = item.get("brand_title", brand)
                        price = item.get("price", "N/A")
                        currency = item.get("currency", "EUR")
                        item_url = item.get("url", f"https://www.vinted.{domain}")

                        photo_data = item.get("photo", {})
                        photo_url = photo_data.get("url") if photo_data else None

                        user_data = item.get("user", {})
                        seller_feedback = user_data.get("feedback_count", 0)
                        seller_status = "⚠️ Новий акаунт / Без відгуків" if seller_feedback == 0 else f"✅ Відгуків: {seller_feedback}"
                        seller_url = user_data.get("profile_url", item_url)

                        caption = (
                            f"⚡️ **НОВА ЗНАХІДКА VINTED** ⚡️\n\n"
                            f"🏷 **Назва:** {title}\n"
                            f"📌 **Бренд:** {item_brand}\n"
                            f"💰 **Ціна:** {price} {currency}\n"
                            f"📏 **Розмір:** {size_title or 'Не вказано'}\n"
                            f"🛡 **Продавець:** {seller_status}"
                        )

                        keyboard = get_item_keyboard(item_url, seller_url)

                        if photo_url:
                            asyncio.create_task(send_telegram_photo(session, user_id, photo_url, caption, keyboard))
                        else:
                            asyncio.create_task(send_telegram_message(session, user_id, caption, keyboard))
        except Exception as e:
            logging.error(f"Помилка парсингу: {e}")

# ==================== ОСНОВНИЙ ЦИКЛ ====================
async def main():
    init_db()

    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    async with aiohttp.ClientSession() as session:
        await clear_old_updates(session)
        while True:
            await handle_telegram_commands(session)
            await fetch_vinted(session)
            await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
