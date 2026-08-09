import asyncio
import logging
import os
import random
import re
import string
from datetime import datetime, timedelta
import aiohttp
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(level=logging.INFO)

# ==================== НАЛАШТУВАННЯ ====================
BOT_TOKEN = "8877190549:AAEoSIj_dOL2hi-PpDrfZFJi6h8x40hJnFQ"
ADMIN_ID = 8138110821

# Рядок підключення до вашої MongoDB Atlas
MONGO_URI = "mongodb+srv://illya:2010@cluster0.p71v9.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"

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

# ==================== MONGODB СТАН ТА ЗМІННІ ====================
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["vinted_bot_db"]
keys_collection = db["keys"]
settings_collection = db["user_settings"]

user_states = {}
seen_items = set()
last_update_id = 0
vinted_cookies = {}

# ==================== РОБОТА З БД (MONGODB) ====================
async def get_user_settings(user_id):
    uid_str = str(user_id)
    doc = await settings_collection.find_one({"user_id": uid_str})
    if not doc:
        doc = {
            "user_id": uid_str,
            "brands": [],
            "sizes": [],
            "price": "Будь-яка ціна",
            "domain": "at",
            "active": False
        }
        await settings_collection.insert_one(doc)
    return doc

async def save_user_settings(user_id, settings):
    uid_str = str(user_id)
    settings["user_id"] = uid_str
    await settings_collection.update_one(
        {"user_id": uid_str},
        {"$set": settings},
        upsert=True
    )

async def get_key_data(key_code):
    return await keys_collection.find_one({"key": key_code})

async def save_key_data(key_code, data):
    data["key"] = key_code
    await keys_collection.update_one(
        {"key": key_code},
        {"$set": data},
        upsert=True
    )

async def health_check(request):
    return web.Response(text="Bot is running 24/7!")

async def generate_random_key(days):
    rand_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    key_code = f"VINTED-{days}D-{rand_str}"
    
    key_doc = {
        "duration_days": days,
        "is_used": False,
        "used_by": None,
        "expires_at": None
    }
    await save_key_data(key_code, key_doc)
    return key_code

async def is_user_active(user_id):
    if user_id in ALLOWED_USERS or user_id == ADMIN_ID:
        return True

    now = datetime.now()
    cursor = keys_collection.find({"used_by": user_id, "is_used": True})
    async for data in cursor:
        exp_str = data.get("expires_at")
        if exp_str:
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S")
                if exp_date > now:
                    return True
            except Exception:
                continue
    return False

async def get_key_remaining_time(user_id):
    if user_id in ALLOWED_USERS or user_id == ADMIN_ID:
        return "Безлімітний доступ (Адмін/VIP)"

    now = datetime.now()
    latest_exp = None
    cursor = keys_collection.find({"used_by": user_id, "is_used": True})
    async for data in cursor:
        exp_str = data.get("expires_at")
        if exp_str:
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S")
                if exp_date > now:
                    if not latest_exp or exp_date > latest_exp:
                        latest_exp = exp_date
            except Exception:
                pass

    if latest_exp:
        diff = latest_exp - now
        days = diff.days
        hours = diff.seconds // 3600
        return f"{days} днів, {hours} годин"

    return None

# ==================== КЛАВІАТУРИ НИЖНЬОЇ ПАНЕЛІ ====================
async def get_main_keyboard(user_id):
    kb = []
    if not await is_user_active(user_id):
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

async def get_sizes_panel_keyboard(user_id):
    cfg = await get_user_settings(user_id)
    selected = cfg.get("sizes", [])
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

async def get_region_panel_keyboard(user_id):
    cfg = await get_user_settings(user_id)
    curr = cfg.get("domain", "at")
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
    if len(brand_name) < 2 or len(brand_name) > 30:
        return False, "❌ Назва бренду повинна бути від 2 до 30 символів."
    
    if re.search(r"http[s]?://|www\.|@|\.com|\.net", brand_name, re.IGNORECASE):
        return False, "❌ Назва не повинна містити посилання або спецсимволи."

    if not re.match(r"^[a-zA-Z0-9\s\-\&\.\'\+]+$", brand_name):
        return False, "❌ Введіть коректну назву бренду (букви, цифри, пробіли, дефіси)."

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

        if text in ["/start", "меню", "Start", "start", "🔙 Головне меню"]:
            user_states[chat_id] = None
            await send_telegram_message(
                session, 
                chat_id, 
                "👋 **Панель керування бота:**", 
                await get_main_keyboard(chat_id)
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
                    new_key = await generate_random_key(days)
                    await send_telegram_message(session, chat_id, f"✅ **Ключ успішно створено!**\n\n`{new_key}`\n\nТермін дії: *{days} днів*", get_admin_keyboard())
                else:
                    await send_telegram_message(session, chat_id, "❌ Будь ласка, введіть тільки число цифрами.", get_admin_keyboard())
                user_states[chat_id] = None
                return

            if text == "📊 Статистика":
                total_keys = await keys_collection.count_documents({})
                used_keys = await keys_collection.count_documents({"is_used": True})
                total_users = await settings_collection.count_documents({})

                stat_msg = f"📊 **Статистика бота:**\n\n🔑 Всього ключів: *{total_keys}*\n✅ Активовано ключів: *{used_keys}*\n👥 Активних користувачів: *{total_users}*"
                await send_telegram_message(session, chat_id, stat_msg, get_admin_keyboard())
                return

            if text == "📋 Список ключів":
                keys_cursor = keys_collection.find().limit(20)
                keys_list = await keys_cursor.to_list(length=20)
                if not keys_list:
                    await send_telegram_message(session, chat_id, "📋 Список ключів порожній.", get_admin_keyboard())
                else:
                    msg_list = "📋 **Останні ключі:**\n\n"
                    for val in keys_list:
                        st = "❌ Використаний" if val.get("is_used") else "🟢 Вільний"
                        msg_list += f"`{val.get('key')}` | {val.get('duration_days')} дн. | {st}\n"
                    await send_telegram_message(session, chat_id, msg_list, get_admin_keyboard())
                return

        # Перевірка регіону
        clean_reg_text = text.replace("✅ ", "")
        if clean_reg_text in DOMAINS:
            cfg = await get_user_settings(chat_id)
            cfg["domain"] = DOMAINS[clean_reg_text]["code"]
            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, f"✅ Регіон змінено на: *{clean_reg_text}*", await get_region_panel_keyboard(chat_id))
            return

        # Обробка розмірів
        clean_size = text.replace("✅ ", "")
        if clean_size in SIZES_LIST:
            cfg = await get_user_settings(chat_id)
            sizes = cfg.get("sizes", [])
            if clean_size in sizes:
                sizes.remove(clean_size)
            else:
                sizes.append(clean_size)
            cfg["sizes"] = sizes
            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, "📏 Оновлено розміри на панелі:", await get_sizes_panel_keyboard(chat_id))
            return

        # Додавання бренду
        if state == "waiting_add_brand":
            user_states[chat_id] = None
            cfg = await get_user_settings(chat_id)
            brands = cfg.get("brands", [])

            if any(b.lower() == text.lower() for b in brands):
                await send_telegram_message(session, chat_id, "⚠️ Цей бренд вже є у вашому списку!", await get_main_keyboard(chat_id))
                return

            domain = cfg.get("domain", "at")
            is_valid, err_msg = await is_valid_brand(session, text, domain)
            if not is_valid:
                await send_telegram_message(session, chat_id, err_msg, await get_main_keyboard(chat_id))
                return

            brands.append(text)
            cfg["brands"] = brands
            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, f"✅ Бренд *{text}* успішно додано!", await get_main_keyboard(chat_id))
            return

        if state == "waiting_custom_price":
            cfg = await get_user_settings(chat_id)
            cfg["price"] = text
            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, f"✅ Максимальну ціну встановлено: *{text}*", await get_main_keyboard(chat_id))
            user_states[chat_id] = None
            return

        if state == "waiting_for_key" or text.startswith("VINTED-"):
            days_to_add = None
            key_doc = await get_key_data(text)

            if text in MASTER_KEYS:
                days_to_add = MASTER_KEYS[text]
            elif key_doc and not key_doc.get("is_used"):
                days_to_add = key_doc.get("duration_days")

            if days_to_add:
                exp_date = datetime.now() + timedelta(days=days_to_add)
                exp_str = exp_date.strftime("%Y-%m-%d %H:%M:%S")

                updated_key_data = {
                    "duration_days": days_to_add,
                    "is_used": True,
                    "used_by": chat_id,
                    "expires_at": exp_str
                }
                await save_key_data(text, updated_key_data)

                user_states[chat_id] = None
                await send_telegram_message(session, chat_id, f"🎉 **Ключ успішно активовано на {days_to_add} днів!**", await get_main_keyboard(chat_id))
            else:
                await send_telegram_message(session, chat_id, "❌ **Невірний або вже використаний ключ.**", await get_main_keyboard(chat_id))
            return

        # Перевірка ключа
        if text in ["🔑 Активувати ключ", "🔑 Активація / Стан ключа", "🔑 Активувати новий ключ"]:
            time_left = await get_key_remaining_time(chat_id)
            if time_left:
                await send_telegram_message(
                    session, 
                    chat_id, 
                    f"✅ **Ваш ключ активний!**\n⏱ Залишилося: *{time_left}*\n\nЯкщо ви хочете ввести новий ключ, надішліть його у відповідь:", 
                    await get_main_keyboard(chat_id)
                )
            else:
                user_states[chat_id] = "waiting_for_key"
                await send_telegram_message(session, chat_id, "Надішліть ваш ключ активації у відповідь:")
            return

        if not await is_user_active(chat_id):
            await send_telegram_message(session, chat_id, "🔒 **Доступ обмежено!** Натисніть кнопку активації ключа.", await get_main_keyboard(chat_id))
            return

        # Команди меню
        if text == "➕ Додати бренд (МП)":
            user_states[chat_id] = "waiting_add_brand"
            await send_telegram_message(session, chat_id, "Введіть назву бренду для додавання в пошук:")

        elif text == "🗑 Очистити бренди":
            cfg = await get_user_settings(chat_id)
            cfg["brands"] = []
            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, "🗑 Список брендів очищено.", await get_main_keyboard(chat_id))

        elif text == "📏 Налаштувати розміри":
            await send_telegram_message(session, chat_id, "Оберіть розміри на нижній панелі:", await get_sizes_panel_keyboard(chat_id))

        elif text == "🧹 Очистити розміри":
            cfg = await get_user_settings(chat_id)
            cfg["sizes"] = []
            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, "🧹 Розміри скинуто.", await get_main_keyboard(chat_id))

        elif text == "🌍 Обрати регіон":
            await send_telegram_message(session, chat_id, "Оберіть країну з панелі нижче:", await get_region_panel_keyboard(chat_id))

        elif text == "💵 Макс. Ціна":
            user_states[chat_id] = "waiting_custom_price"
            await send_telegram_message(session, chat_id, "Введіть максимальну ціну цифрами (наприклад: `30`):")

        elif text == "📋 Мої налаштування":
            cfg = await get_user_settings(chat_id)
            brands = ", ".join(cfg.get("brands", [])) or "Не обрано"
            sizes = ", ".join(cfg.get("sizes", [])) or "Всі"
            price = cfg.get("price", "Будь-яка ціна")
            domain = cfg.get("domain", "at").upper()
            status = "🟢 Активний" if cfg.get("active") else "🔴 Зупинений"
            
            info = f"⚙️ **Налаштування:**\n\n🏷 **Бренди:** {brands}\n📏 **Розміри:** {sizes}\n💵 **Макс. ціна:** {price}\n🌍 **Регіон:** {domain}\n📡 **Статус:** {status}"
            await send_telegram_message(session, chat_id, info, await get_main_keyboard(chat_id))

        elif text == "▶️ Запустити":
            cfg = await get_user_settings(chat_id)
            if not cfg.get("brands"):
                await send_telegram_message(session, chat_id, "⚠️ Спочатку додайте хоча б один бренд!", await get_main_keyboard(chat_id))
                return
            cfg["active"] = True
            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, "Пошук запущено", await get_main_keyboard(chat_id))

        elif text == "⏹ Зупинити":
            cfg = await get_user_settings(chat_id)
            cfg["active"] = False
            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, "⏹ Пошук зупинено.", await get_main_keyboard(chat_id))

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
    cursor = settings_collection.find({"active": True})
    async for config in cursor:
        user_id = int(config.get("user_id"))
        if not await is_user_active(user_id):
            continue

        domain = config.get("domain", "at")
        target_brands = config.get("brands", [])
        if not target_brands:
            continue

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
