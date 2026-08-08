import asyncio
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta
import aiohttp
from aiohttp import web

logging.basicConfig(level=logging.INFO)

# ==================== НАЛАШТУВАННЯ ====================
BOT_TOKEN = "8877190549:AAEoSIj_dOL2hi-PpDrfZFJi6h8x40hJnFQ"
ADMIN_ID = 8138110821
CHECK_INTERVAL = 2

ALLOWED_USERS = [8138110821]

MASTER_KEYS = {
    "VINTED-VIP-2026": 365,
    "VINTED-FREE-TEST": 30,
    "VINTED-KEY-7DAYS": 7
}

POPULAR_BRANDS = [
    "Nike", "Adidas", "Stone Island", "Carhartt", 
    "Jordan", "Stussy", "Trapstar", "The North Face"
]

SIZES = ["XS", "S", "M", "L", "XL", "XXL"]

# Багатомовний словник для блокування фейків і копій
FAKE_KEYWORDS = [
    # Англійська
    "fake", "replica", "rep", "1:1", "1v1", "copy", "counterfeit", "knockoff", "bootleg", "not original", "ua pair", "high quality rep",
    # Французька
    "faux", "fausse", "réplique", "replique", "copie", "contrefaçon", "contrefacon", "pas vrai", "imitation",
    # Німецька / Польська / Чеська / Іспанська
    "gefälscht", "gefaelscht", "kopia", "fałszywy", "replika", "falso", "copia",
    # Українська / Російська
    "1в1", "репліка", "реплика", "копія", "копия", "фейк", "паль", "люкс"
]

DOMAINS = {
    "🇵🇱 Польща": {"code": "pl", "currency": "PLN", "prices": ["До 50 PLN", "До 100 PLN", "До 200 PLN"]},
    "🇦🇹 Австрія": {"code": "at", "currency": "EUR", "prices": ["До 10 €", "До 25 €", "До 50 €"]},
    "🇨🇿 Чехія": {"code": "cz", "currency": "CZK", "prices": ["До 250 CZK", "До 500 CZK", "До 1000 CZK"]},
    "🇱🇹 Литва": {"code": "lt", "currency": "EUR", "prices": ["До 10 €", "До 25 €", "До 50 €"]},
    "🇷🇴 Румунія": {"code": "ro", "currency": "RON", "prices": ["До 50 RON", "До 100 RON", "До 200 RON"]},
    "🇩🇪 Німеччина": {"code": "de", "currency": "EUR", "prices": ["До 10 €", "До 25 €", "До 50 €"]},
    "🇫🇷 Франція": {"code": "fr", "currency": "EUR", "prices": ["До 10 €", "До 25 €", "До 50 €"]},
    "🇬🇧 Великобританія": {"code": "co.uk", "currency": "GBP", "prices": ["До 10 £", "До 25 £", "До 50 £"]}
}

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
vinted_cookies = {}

# ==================== КЛАВІАТУРИ ====================
def get_main_keyboard(user_id):
    kb = []
    if not is_user_active(user_id):
        kb.append([{"text": "🔑 Активувати ключ"}, {"text": "🛒 Придбати ключ"}])
        return {"keyboard": kb, "resize_keyboard": True, "persistent": True}

    kb.append([{"text": "🏷 Обрати бренд"}, {"text": "📏 Обрати розмір"}])
    kb.append([{"text": "💵 Макс. Ціна"}, {"text": "🌍 Обрати регіон"}])
    kb.append([{"text": "📋 Мої налаштування"}, {"text": "▶️ Запустити"}, {"text": "⏹ Зупинити"}])
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

def get_price_keyboard(domain_code):
    price_list = ["До 10 €", "До 25 €", "До 50 €"]
    for reg_data in DOMAINS.values():
        if reg_data["code"] == domain_code:
            price_list = reg_data["prices"]
            break

    buttons = []
    for p in price_list:
        buttons.append([{"text": p, "callback_data": f"set_price:{p}"}])
    buttons.append([{"text": "✏️ Ввести свою ціну", "callback_data": "custom_price"}])
    buttons.append([{"text": "🌐 Будь-яка ціна", "callback_data": "set_price:Будь-яка ціна"}])
    return {"inline_keyboard": buttons}

def get_region_keyboard(current_domain):
    buttons = []
    for name, data in DOMAINS.items():
        code = data["code"]
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

        if text in ["/start", "меню", "Start", "start"]:
            user_states[chat_id] = None
            await send_telegram_message(
                session, 
                chat_id, 
                "👋 **Ласкаво просимо!** Оберіть налаштування в меню нижче:", 
                get_main_keyboard(chat_id)
            )
            return

        state = user_states.get(chat_id)

        if state == "waiting_custom_brand":
            user_settings.setdefault(uid_str, {})["brand"] = text
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, f"✅ Бренд встановлено: *{text}*", get_main_keyboard(chat_id))
            user_states[chat_id] = None
            return

        if state == "waiting_custom_price":
            user_settings.setdefault(uid_str, {})["price"] = text
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, f"✅ Максимальну ціну встановлено: *{text}*", get_main_keyboard(chat_id))
            user_states[chat_id] = None
            return

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
                await send_telegram_message(session, chat_id, f"🔑 **Згенеровано ключ:** `{new_key}` на {days} днів.", get_main_keyboard(chat_id))
            except ValueError:
                await send_telegram_message(session, chat_id, "❌ Введіть число днів цифрою.")
            user_states[chat_id] = None
            return

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

                user_states[chat_id] = None
                await send_telegram_message(session, chat_id, f"🎉 **Ключ успішно активовано на {days_to_add} днів!**", get_main_keyboard(chat_id))
            else:
                await send_telegram_message(session, chat_id, "❌ **Невірний або вже використаний ключ.**", get_main_keyboard(chat_id))
            return

        if not is_user_active(chat_id):
            if text in ["🔑 Активувати ключ", "🔑 Активувати новий ключ"]:
                user_states[chat_id] = "waiting_for_key"
                await send_telegram_message(session, chat_id, "Надішліть ваш ключ активації у відповідь:")
            elif text == "🛒 Придбати ключ":
                await send_telegram_message(session, chat_id, "💳 Купівля ключа: @but_sh0ping", get_main_keyboard(chat_id))
            else:
                await send_telegram_message(session, chat_id, "🔒 **Доступ обмежено!** Натисніть **🔑 Активувати ключ** або пишіть @but_sh0ping.", get_main_keyboard(chat_id))
            return

        if "👑 Адмін-панель" in text and chat_id == ADMIN_ID:
            user_states[chat_id] = "waiting_for_key_gen"
            await send_telegram_message(session, chat_id, "Введіть термін дії ключа у днях:")

        elif text in ["🔑 Активувати ключ", "🔑 Активувати новий ключ"]:
            user_states[chat_id] = "waiting_for_key"
            await send_telegram_message(session, chat_id, "Надішліть ваш ключ активації:")

        elif text == "🛒 Придбати ключ":
            await send_telegram_message(session, chat_id, "💳 Купівля ключа: @but_sh0ping", get_main_keyboard(chat_id))

        elif "🏷 Обрати бренд" in text:
            await send_telegram_message(session, chat_id, "Оберіть бренд з меню нижче або введіть свій:", get_brands_keyboard())

        elif "📏 Обрати розмір" in text:
            selected = user_settings.get(uid_str, {}).get("sizes", [])
            await send_telegram_message(session, chat_id, "Оберіть розміри:", get_sizes_keyboard(selected))

        elif "💵 Макс. Ціна" in text:
            domain_code = user_settings.get(uid_str, {}).get("domain", "at")
            await send_telegram_message(session, chat_id, "Оберіть або введіть максимальну ціну:", get_price_keyboard(domain_code))

        elif "🌍 Обрати регіон" in text:
            curr = user_settings.get(uid_str, {}).get("domain", "at")
            await send_telegram_message(session, chat_id, "Оберіть регіон з меню:", get_region_keyboard(curr))

        elif "📋 Мої налаштування" in text:
            cfg = user_settings.get(uid_str, {})
            brand = cfg.get("brand", "Не обрано")
            sizes = ", ".join(cfg.get("sizes", [])) or "Всі"
            price = cfg.get("price", "Будь-яка ціна")
            domain = cfg.get("domain", "at").upper()
            status = "🟢 Активний" if cfg.get("active") else "🔴 Зупинений"
            
            info = f"⚙️ **Налаштування:**\n\n🏷 **Бренд:** {brand}\n📏 **Розміри:** {sizes}\n💵 **Макс. ціна:** {price}\n🌍 **Регіон:** {domain}\n📡 **Статус:** {status}"
            await send_telegram_message(session, chat_id, info, get_main_keyboard(chat_id))

        elif "▶️ Запустити" in text:
            cfg = user_settings.get(uid_str, {})
            if not cfg.get("brand"):
                await send_telegram_message(session, chat_id, "⚠️ Спочатку оберіть бренд!", get_main_keyboard(chat_id))
                return
            user_settings.setdefault(uid_str, {})["active"] = True
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, "🚀 **Моніторинг найновіших оригіналів запущено!**", get_main_keyboard(chat_id))

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

        elif data == "custom_price":
            user_states[chat_id] = "waiting_custom_price"
            await send_telegram_message(session, chat_id, "Напишіть максимальну ціну у відповідь (наприклад: `20`):")

        elif data.startswith("toggle_size:"):
            size = data.split(":")[1]
            sizes = user_settings[uid_str].get("sizes", [])
            if size in sizes: sizes.remove(size)
            else: sizes.append(size)
            user_settings[uid_str]["sizes"] = sizes
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, "Оновлено", get_sizes_keyboard(sizes))

        elif data == "close_size_menu":
            sizes = ", ".join(user_settings[uid_str].get("sizes", [])) or "всі"
            await send_telegram_message(session, chat_id, f"👌 Розміри збережено: *{sizes}*", get_main_keyboard(chat_id))

        elif data.startswith("set_price:"):
            pr = data.split(":")[1]
            user_settings[uid_str]["price"] = pr
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, f"✅ Максимальна ціна: *{pr}*", get_main_keyboard(chat_id))

        elif data.startswith("set_reg:"):
            code = data.split(":")[1]
            user_settings[uid_str]["domain"] = code
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, f"✅ Регіон: *{code.upper()}*", get_main_keyboard(chat_id))

# ==================== ПАРСИНГ З СУВОРОЮ ПЕРЕВІРКОЮ БРЕНДУ ТА МУЛЬТИМОВНИМ АНТИФЕЙКОМ ====================
async def get_vinted_cookie(session, domain):
    if domain in vinted_cookies:
        return vinted_cookies[domain]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    }
    url = f"https://www.vinted.{domain}"
    try:
        async with session.get(url, headers=headers, timeout=5) as resp:
            cookies = resp.cookies
            cookie_str = "; ".join([f"{k}={v.value}" for k, v in cookies.items()])
            vinted_cookies[domain] = cookie_str
            return cookie_str
    except Exception:
        return ""

async def fetch_vinted(session):
    for uid_str, config in list(user_settings.items()):
        if not config.get("active") or not config.get("brand"):
            continue

        user_id = int(uid_str)
        if not is_user_active(user_id):
            continue

        domain = config.get("domain", "at")
        target_brand = str(config.get("brand", "")).strip().lower()
        user_sizes = config.get("sizes", [])
        user_price_str = config.get("price", "Будь-яка ціна")

        cookie = await get_vinted_cookie(session, domain)

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Cookie": cookie
        }

        api_url = f"https://www.vinted.{domain}/api/v2/catalog/items?search_text={target_brand}&order=newest_first&per_page=20"

        try:
            async with session.get(api_url, headers=headers, timeout=6) as resp:
                if resp.status in (401, 403):
                    vinted_cookies.pop(domain, None)
                    continue

                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("items", [])

                    for item in items:
                        if item.get("promoted") or item.get("is_promoted"):
                            continue

                        item_id = item.get("id")
                        if item_id in seen_items:
                            continue

                        # 1. Сувора перевірка бренду
                        item_brand_raw = str(item.get("brand_title", "")).strip().lower()
                        if target_brand not in item_brand_raw:
                            continue

                        # 2. Перевірка на фейки та копії (багатомовна)
                        title = str(item.get("title", ""))
                        description = str(item.get("description", ""))
                        full_text = f"{title} {description}".lower()

                        if any(fake_word in full_text for fake_word in FAKE_KEYWORDS):
                            continue

                        # 3. Перевірка цінового ліміту
                        try:
                            item_price = float(item.get("price", 0))
                        except (ValueError, TypeError):
                            item_price = 0.0

                        if "Будь-яка" not in user_price_str:
                            digits = re.findall(r"\d+", user_price_str)
                            if digits:
                                max_p = float(digits[-1])
                                if item_price > max_p:
                                    continue

                        # 4. Перевірка розміру
                        size_title = str(item.get("size_title", "")).upper()
                        if user_sizes:
                            if not any(s.upper() in size_title for s in user_sizes):
                                continue

                        seen_items.add(item_id)

                        item_brand_display = item.get("brand_title", config.get("brand"))
                        item_url = item.get("url", f"https://www.vinted.{domain}")

                        photo_data = item.get("photo", {})
                        photo_url = photo_data.get("url") if photo_data else None

                        user_data = item.get("user", {})
                        seller_feedback = user_data.get("feedback_count", 0)
                        seller_status = "⚠️ Новий акаунт / Без відгуків" if seller_feedback == 0 else f"✅ Відгуків: {seller_feedback}"
                        seller_url = user_data.get("profile_url", item_url)

                        # Опис картки без рядка з ціною
                        caption = (
                            f"⚡️ **НОВА ЗНАХІДКА VINTED** ⚡️\n\n"
                            f"🏷 **Назва:** {title}\n"
                            f"📌 **Бренд:** {item_brand_display}\n"
                            f"📏 **Розмір:** {size_title or 'Не вказано'}\n"
                            f"🛡 **Продавець:** {seller_status}"
                        )

                        keyboard = get_item_keyboard(item_url, seller_url)

                        if photo_url:
                            asyncio.create_task(send_telegram_photo(session, user_id, photo_url, caption, keyboard))
                        else:
                            asyncio.create_task(send_telegram_message(session, user_id, caption, keyboard))
        except Exception as e:
            logging.error(f"Помилка парсингу Vinted: {e}")

# ==================== ОСНОВНИЙ ЦИКЛ ====================
async def handle_telegram_commands(session):
    global last_update_id
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": last_update_id + 1, "timeout": 1}
    try:
        async with session.get(url, params=params) as resp:
            data = await resp.json()
            if data.get("ok") and data.get("result"):
                for update in data["result"]:
                    last_update_id = update["update_id"]
                    asyncio.create_task(handle_update(session, update))
    except Exception as e:
        logging.error(f"Помилка Telegram API: {e}")

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
        while True:
            await handle_telegram_commands(session)
            await fetch_vinted(session)
            await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
