import asyncio
import json
import logging
import os
import random
import re
import sqlite3
import string
from datetime import datetime, timedelta
import aiohttp
from aiohttp import web

logging.basicConfig(level=logging.INFO)

# ==================== НАЛАШТУВАННЯ ====================
BOT_TOKEN = "8877190549:AAEoSIj_dOL2hi-PpDrfZFJi6h8x40hJnFQ"
ADMIN_ID = 8138110821

# Зменшено для прискорення
CHECK_INTERVAL = 2

ALLOWED_USERS = [8138110821]

MASTER_KEYS = {
    "VINTED-VIP-2026": 365,
    "VINTED-FREE-TEST": 30,
    "VINTED-KEY-7DAYS": 7
}

SIZES_LIST = [
    "XS", "S", "M", "L", "XL", "XXL",
    "36", "37", "38", "39", "40", "41", "42", "43", "44", "45", "46"
]

FAKE_KEYWORDS = [
    "fake", "replica", "rep", "1:1", "1v1", "copy", "counterfeit", "knockoff", "bootleg", "not original", "ua pair",
    "faux", "fausse", "réplique", "replique", "copie", "contrefaçon", "imitation",
    "gefälscht", "kopia", "fałszywy", "replika", "falso",
    "1в1", "репліка", "реплика", "копія", "копия", "фейк", "паль", "люкс"
]

DOMAINS = {
    "🇵🇱 Польща": {"code": "pl", "currency": "PLN"},
    "🇦🇹 Австрія": {"code": "at", "currency": "EUR"},
    "🇨🇿 Чехія": {"code": "cz", "currency": "CZK"},
    "🇱🇹 Литва": {"code": "lt", "currency": "EUR"},
    "🇷🇴 Румунія": {"code": "ro", "currency": "RON"},
    "🇩🇪 Німеччина": {"code": "de", "currency": "EUR"},
    "🇫🇷 Франція": {"code": "fr", "currency": "EUR"},
    "🇬🇧 Великобританія": {"code": "co.uk", "currency": "GBP"}
}

DB_PATH = "licenses.db"

async def health_check(request):
    return web.Response(text="Bot is running 24/7!")

def init_db():
    conn = sqlite3.connect(DB_PATH)
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

def generate_random_key(days):
    rand_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    key_code = f"VINTED-{days}D-{rand_str}"
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO keys (key, duration_days, is_used) VALUES (?, ?, 0)", (key_code, days))
    conn.commit()
    conn.close()
    return key_code

def is_user_active(user_id):
    if user_id in ALLOWED_USERS or user_id == ADMIN_ID:
        return True

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT expires_at FROM keys WHERE used_by = ? AND is_used = 1", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    
    for row in rows:
        if row and row[0]:
            try:
                exp_date = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                if exp_date > datetime.now():
                    return True
            except Exception:
                continue
    return False

def get_key_remaining_time(user_id):
    if user_id in ALLOWED_USERS or user_id == ADMIN_ID:
        return "Безлімітний доступ (Адмін/VIP)"

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT expires_at FROM keys WHERE used_by = ? AND is_used = 1 ORDER BY expires_at DESC", (user_id,))
    row = cursor.fetchone()
    conn.close()

    if row and row[0]:
        try:
            exp_date = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
            now = datetime.now()
            if exp_date > now:
                diff = exp_date - now
                days = diff.days
                hours = diff.seconds // 3600
                return f"{days} днів, {hours} годин"
        except Exception:
            pass
    return None

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

# ==================== КЛАВІАТУРИ НИЖНЬОЇ ПАНЕЛІ ====================
def get_main_keyboard(user_id):
    kb = []
    if not is_user_active(user_id):
        kb.append([{"text": "🔑 Активувати ключ"}, {"text": "🛒 Придбати ключ"}])
        return {"keyboard": kb, "resize_keyboard": True, "persistent": True}

    kb.append([{"text": "➕ Додати бренд (МП)"}, {"text": "🗑 Очистити бренди"}])
    kb.append([{"text": "📏 Налаштувати розміри"}, {"text": "💵 Макс. Ціна"}])
    kb.append([{"text": "🌍 Обрати регіон"}, {"text": "📋 Мої налаштування"}])
    kb.append([{"text": "▶️ Запустити"}, {"text": "⏹ Зупинити"}])
    kb.append([{"text": "🔑 Активація / Стан ключа"}])
    if user_id == ADMIN_ID:
        kb.append([{"text": "👑 Адмін-панель"}])
    return {"keyboard": kb, "resize_keyboard": True, "persistent": True}

def get_admin_keyboard():
    return {
        "keyboard": [
            [{"text": "➕ Згенерувати ключ"}, {"text": "📊 Статистика"}],
            [{"text": "📋 Список ключів"}, {"text": "🔙 Головне меню"}]
        ],
        "resize_keyboard": True,
        "persistent": True
    }

def get_sizes_panel_keyboard(user_id):
    uid_str = str(user_id)
    selected = user_settings.get(uid_str, {}).get("sizes", [])
    kb = []
    row = []
    for sz in SIZES_LIST:
        prefix = "✅ " if sz in selected else ""
        row.append({"text": f"{prefix}{sz}"})
        if len(row) == 3:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([{"text": "🧹 Очистити розміри"}, {"text": "🔙 Головне меню"}])
    return {"keyboard": kb, "resize_keyboard": True, "persistent": True}

def get_region_panel_keyboard(user_id):
    uid_str = str(user_id)
    curr = user_settings.get(uid_str, {}).get("domain", "at")
    kb = []
    row = []
    for name, data in DOMAINS.items():
        prefix = "✅ " if data["code"] == curr else ""
        row.append({"text": f"{prefix}{name}"})
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([{"text": "🔙 Головне меню"}])
    return {"keyboard": kb, "resize_keyboard": True, "persistent": True}

def get_item_keyboard(item_url):
    return {
        "inline_keyboard": [
            [{"text": "⚡ КУПИТИ НА VINTED", "url": item_url}]
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

# ==================== ВАЛІДАЦІЯ БРЕНДІВ ====================
async def is_valid_brand(session, brand_name, domain="at"):
    # Перевірка формату та символів
    if len(brand_name) < 2 or len(brand_name) > 30:
        return False, "❌ Назва бренду повинна бути від 2 до 30 символів."
    
    if re.search(r"http[s]?://|www\.|@|\.com|\.net", brand_name, re.IGNORECASE):
        return False, "❌ Назва не повинна містити посилання або спецсимволи."

    if not re.match(r"^[a-zA-Z0-9\s\-\&\.\'\+]+$", brand_name):
        return False, "❌ Введіть коректну назву бренду (букви, цифри, пробіли, дефіси)."

    # Перевірка реального існування бренду через API Vinted
    cookie = await get_vinted_cookie(session, domain)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie
    }
    api_url = f"https://www.vinted.{domain}/api/v2/catalog/items?search_text={brand_name}&per_page=5"
    try:
        async with session.get(api_url, headers=headers, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                items = data.get("items", [])
                if not items:
                    return False, f"❌ Бренд або товари з назвою *'{brand_name}'* не знайдені на Vinted."
            else:
                return True, ""
    except Exception:
        pass

    return True, ""

# ==================== ОБРОБКА ПОВІДОМЛЕНЬ ====================
async def handle_update(session, update):
    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "").strip()
        uid_str = str(chat_id)

        if text in ["/start", "меню", "Start", "start", "🔙 Головне меню"]:
            user_states[chat_id] = None
            await send_telegram_message(
                session, 
                chat_id, 
                "👋 **Панель керування бота:**", 
                get_main_keyboard(chat_id)
            )
            return

        state = user_states.get(chat_id)

        # Адмін-панель
        if chat_id == ADMIN_ID:
            if text == "👑 Адмін-панель":
                await send_telegram_message(session, chat_id, "👑 **Адміністративна панель:**", get_admin_keyboard())
                return

            if text == "➕ Згенерувати ключ":
                user_states[chat_id] = "waiting_gen_days"
                await send_telegram_message(session, chat_id, "Введіть термін дії ключа в днях (число, наприклад: `30`):")
                return

            if state == "waiting_gen_days":
                if text.isdigit():
                    days = int(text)
                    new_key = generate_random_key(days)
                    await send_telegram_message(session, chat_id, f"✅ **Ключ успішно створено!**\n\n`{new_key}`\n\nТермін дії: *{days} днів*", get_admin_keyboard())
                else:
                    await send_telegram_message(session, chat_id, "❌ Будь ласка, введіть тільки число цифрами.", get_admin_keyboard())
                user_states[chat_id] = None
                return

            if text == "📊 Статистика":
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM keys")
                total_keys = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM keys WHERE is_used = 1")
                used_keys = cursor.fetchone()[0]
                conn.close()

                stat_msg = f"📊 **Статистика бота:**\n\n🔑 Всього ключів: *{total_keys}*\n✅ Активовано ключів: *{used_keys}*\n👥 Активних користувачів у файлі: *{len(user_settings)}*"
                await send_telegram_message(session, chat_id, stat_msg, get_admin_keyboard())
                return

            if text == "📋 Список ключів":
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("SELECT key, duration_days, is_used, expires_at FROM keys ORDER BY is_used ASC LIMIT 20")
                rows = cursor.fetchall()
                conn.close()

                if not rows:
                    await send_telegram_message(session, chat_id, "📋 Список ключів порожній.", get_admin_keyboard())
                else:
                    msg_list = "📋 **Останні ключі:**\n\n"
                    for r in rows:
                        st = "❌ Використаний" if r[2] == 1 else "🟢 Вільний"
                        msg_list += f"`{r[0]}` | {r[1]} дн. | {st}\n"
                    await send_telegram_message(session, chat_id, msg_list, get_admin_keyboard())
                return

        # Перевірка регіону
        clean_reg_text = text.replace("✅ ", "")
        if clean_reg_text in DOMAINS:
            code = DOMAINS[clean_reg_text]["code"]
            user_settings.setdefault(uid_str, {})["domain"] = code
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, f"✅ Регіон змінено на: *{clean_reg_text}*", get_region_panel_keyboard(chat_id))
            return

        # Обробка розмірів
        clean_size = text.replace("✅ ", "")
        if clean_size in SIZES_LIST:
            sizes = user_settings.setdefault(uid_str, {}).get("sizes", [])
            if clean_size in sizes:
                sizes.remove(clean_size)
            else:
                sizes.append(clean_size)
            user_settings[uid_str]["sizes"] = sizes
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, "📏 Оновлено розміри на панелі:", get_sizes_panel_keyboard(chat_id))
            return

        # Додавання бренду з додатковими перевірками
        if state == "waiting_add_brand":
            user_states[chat_id] = None
            brands = user_settings.setdefault(uid_str, {}).get("brands", [])

            # Перевірка на дублікати (без урахування регістру)
            if any(b.lower() == text.lower() for b in brands):
                await send_telegram_message(session, chat_id, "⚠️ Цей бренд вже є у вашому списку!", get_main_keyboard(chat_id))
                return

            # Валідація назви бренду
            domain = user_settings.get(uid_str, {}).get("domain", "at")
            is_valid, err_msg = await is_valid_brand(session, text, domain)
            if not is_valid:
                await send_telegram_message(session, chat_id, err_msg, get_main_keyboard(chat_id))
                return

            brands.append(text)
            user_settings[uid_str]["brands"] = brands
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, f"✅ Бренд *{text}* успішно додано!", get_main_keyboard(chat_id))
            return

        if state == "waiting_custom_price":
            user_settings.setdefault(uid_str, {})["price"] = text
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, f"✅ Максимальну ціну встановлено: *{text}*", get_main_keyboard(chat_id))
            user_states[chat_id] = None
            return

        if state == "waiting_for_key" or text.startswith("VINTED-"):
            days_to_add = None
            if text in MASTER_KEYS:
                days_to_add = MASTER_KEYS[text]
            else:
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("SELECT duration_days, is_used FROM keys WHERE key = ?", (text,))
                row = cursor.fetchone()
                if row and row[1] == 0:
                    days_to_add = row[0]
                    cursor.execute("UPDATE keys SET is_used = 1, used_by = ? WHERE key = ?", (chat_id, text))
                    conn.commit()
                conn.close()

            if days_to_add:
                exp_date = datetime.now() + timedelta(days=days_to_add)
                exp_str = exp_date.strftime("%Y-%m-%d %H:%M:%S")

                conn = sqlite3.connect(DB_PATH)
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

        # Перевірка ключа
        if text in ["🔑 Активувати ключ", "🔑 Активація / Стан ключа", "🔑 Активувати новий ключ"]:
            time_left = get_key_remaining_time(chat_id)
            if time_left:
                await send_telegram_message(
                    session, 
                    chat_id, 
                    f"✅ **Ваш ключ активний!**\n⏱ Залишилося: *{time_left}*\n\nЯкщо ви хочете ввести новий ключ, надішліть його у відповідь:", 
                    get_main_keyboard(chat_id)
                )
            else:
                user_states[chat_id] = "waiting_for_key"
                await send_telegram_message(session, chat_id, "Надішліть ваш ключ активації у відповідь:")
            return

        if not is_user_active(chat_id):
            await send_telegram_message(session, chat_id, "🔒 **Доступ обмежено!** Натисніть кнопку активації ключа.", get_main_keyboard(chat_id))
            return

        # Команди меню
        if text == "➕ Додати бренд (МП)":
            user_states[chat_id] = "waiting_add_brand"
            await send_telegram_message(session, chat_id, "Введіть назву бренду для додавання в пошук:")

        elif text == "🗑 Очистити бренди":
            user_settings.setdefault(uid_str, {})["brands"] = []
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, "🗑 Список брендів очищено.", get_main_keyboard(chat_id))

        elif text == "📏 Налаштувати розміри":
            await send_telegram_message(session, chat_id, "Оберіть розміри на нижній панелі:", get_sizes_panel_keyboard(chat_id))

        elif text == "🧹 Очистити розміри":
            user_settings.setdefault(uid_str, {})["sizes"] = []
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, "🧹 Розміри скинуто.", get_sizes_panel_keyboard(chat_id))

        elif text == "🌍 Обрати регіон":
            await send_telegram_message(session, chat_id, "Оберіть країну з панелі нижче:", get_region_panel_keyboard(chat_id))

        elif text == "💵 Макс. Ціна":
            user_states[chat_id] = "waiting_custom_price"
            await send_telegram_message(session, chat_id, "Введіть максимальну ціну цифрами (наприклад: `30`):")

        elif text == "📋 Мої налаштування":
            cfg = user_settings.get(uid_str, {})
            brands = ", ".join(cfg.get("brands", [])) or "Не обрано"
            sizes = ", ".join(cfg.get("sizes", [])) or "Всі"
            price = cfg.get("price", "Будь-яка ціна")
            domain = cfg.get("domain", "at").upper()
            status = "🟢 Активний" if cfg.get("active") else "🔴 Зупинений"
            
            info = f"⚙️ **Налаштування:**\n\n🏷 **Бренди:** {brands}\n📏 **Розміри:** {sizes}\n💵 **Макс. ціна:** {price}\n🌍 **Регіон:** {domain}\n📡 **Статус:** {status}"
            await send_telegram_message(session, chat_id, info, get_main_keyboard(chat_id))

        elif text == "▶️ Запустити":
            cfg = user_settings.get(uid_str, {})
            if not cfg.get("brands"):
                await send_telegram_message(session, chat_id, "⚠️ Спочатку додайте хоча б один бренд!", get_main_keyboard(chat_id))
                return
            user_settings.setdefault(uid_str, {})["active"] = True
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, "Пошук запущено", get_main_keyboard(chat_id))

        elif text == "⏹ Зупинити":
            if uid_str in user_settings:
                user_settings[uid_str]["active"] = False
                save_settings(user_settings)
            await send_telegram_message(session, chat_id, "⏹ Пошук зупинено.", get_main_keyboard(chat_id))

# ==================== ОПТИМІЗОВАНИЙ ПАРСИНГ VINTED ====================
async def get_vinted_cookie(session, domain):
    if domain in vinted_cookies and vinted_cookies[domain]:
        return vinted_cookies[domain]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    url = f"https://www.vinted.{domain}"
    try:
        async with session.get(url, headers=headers, timeout=10) as resp:
            cookies = resp.cookies
            cookie_str = "; ".join([f"{k}={v.value}" for k, v in cookies.items()])
            if cookie_str:
                vinted_cookies[domain] = cookie_str
            return cookie_str
    except Exception:
        return ""

async def process_brand_search(session, user_id, target_brand, domain, user_sizes, user_price_str, currency_symbol, headers):
    api_url = f"https://www.vinted.{domain}/api/v2/catalog/items?search_text={target_brand}&order=newest_first&per_page=15"
    try:
        async with session.get(api_url, headers=headers, timeout=8) as resp:
            if resp.status in (401, 403, 429):
                vinted_cookies.pop(domain, None)
                return

            if resp.status == 200:
                data = await resp.json()
                items = data.get("items", [])

                for item in items:
                    if item.get("promoted") or item.get("is_promoted"):
                        continue

                    item_id = item.get("id")
                    if not item_id or item_id in seen_items:
                        continue

                    title = str(item.get("title", ""))
                    description = str(item.get("description", ""))
                    item_brand = str(item.get("brand_title", ""))
                    full_text = f"{title} {description} {item_brand}".lower()

                    if target_brand.lower() not in full_text:
                        continue

                    if any(fake_word in full_text for fake_word in FAKE_KEYWORDS):
                        continue

                    item_price = 0.0
                    raw_price = item.get("price")
                    if isinstance(raw_price, (int, float, str)):
                        try: item_price = float(raw_price)
                        except ValueError: pass
                    elif isinstance(raw_price, dict):
                        try: item_price = float(raw_price.get("amount", 0))
                        except ValueError: pass

                    if item_price == 0.0 and item.get("price_numeric"):
                        try: item_price = float(item.get("price_numeric"))
                        except ValueError: pass

                    if "Будь-яка" not in user_price_str:
                        digits = re.findall(r"\d+(?:\.\d+)?", user_price_str.replace(",", "."))
                        if digits:
                            max_p = float(digits[-1])
                            if item_price > max_p:
                                continue

                    size_title = str(item.get("size_title", "")).upper()
                    if user_sizes:
                        if not any(s.upper() in size_title for s in user_sizes):
                            continue

                    seen_items.add(item_id)

                    item_brand_display = item_brand if item_brand else target_brand
                    item_url = item.get("url", f"https://www.vinted.{domain}")
                    photo_data = item.get("photo", {})
                    photo_url = photo_data.get("url") if photo_data else None

                    price_display = f"{item_price:.2f}" if item_price > 0 else "За запитом"

                    caption = (
                        f"⚡️ **НОВА ЗНАХІДКА VINTED** ⚡️\n\n"
                        f"🏷 **Назва:** {title}\n"
                        f"💰 **Ціна:** {price_display} {currency_symbol}\n"
                        f"📌 **Бренд:** {item_brand_display}\n"
                        f"📏 **Розмір:** {size_title or 'Не вказано'}"
                    )

                    keyboard = get_item_keyboard(item_url)

                    if photo_url:
                        asyncio.create_task(send_telegram_photo(session, user_id, photo_url, caption, keyboard))
                    else:
                        asyncio.create_task(send_telegram_message(session, user_id, caption, keyboard))
    except Exception as e:
        logging.error(f"Помилка МП парсингу: {e}")

async def fetch_vinted(session):
    tasks = []
    for uid_str, config in list(user_settings.items()):
        if not config.get("active") or not config.get("brands"):
            continue

        user_id = int(uid_str)
        if not is_user_active(user_id):
            continue

        domain = config.get("domain", "at")
        target_brands = config.get("brands", [])
        user_sizes = config.get("sizes", [])
        user_price_str = str(config.get("price", "Будь-яка ціна"))

        currency_symbol = "EUR"
        for reg in DOMAINS.values():
            if reg["code"] == domain:
                currency_symbol = reg["currency"]
                break

        cookie = await get_vinted_cookie(session, domain)

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"https://www.vinted.{domain}/catalog",
            "Cookie": cookie
        }

        # Асинхронний паралельний запуск перевірки для кожного бренду
        for target_brand in target_brands:
            tasks.append(
                process_brand_search(
                    session, user_id, target_brand, domain, user_sizes, user_price_str, currency_symbol, headers
                )
            )

    if tasks:
        await asyncio.gather(*tasks)

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
