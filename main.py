import asyncio
import logging
import os
import random
import re
import string
import json
import time
from datetime import datetime, timedelta
import aiohttp
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(level=logging.INFO)

# ==================== НАЛАШТУВАННЯ ====================
BOT_TOKEN = "8877190549:AAEoSIj_dOL2hi-PpDrfZFJi6h8x40hJnFQ"
ADMIN_ID = 8138110821

MONGO_URI = "mongodb+srv://kykarikyzerick_db_user:CVz4czwK06sgQlSP@cluster0.xuoxdku.mongodb.net/?appName=Cluster0"

CHECK_INTERVAL = 0.05  # Максимальна швидкість перевірки
ALLOWED_USERS = [8138110821]
EUR_TO_UAH_RATE = 51.0
MAX_ITEM_AGE_MINUTES = 30 

MASTER_KEYS = {
    "VINTED-VIP-2026": 365,
    "VINTED-FREE-TEST": 30,
    "VINTED-KEY-7DAYS": 7
}

SIZES_LIST = [
    "XS", "S", "M", "L", "XL", "XXL",
    "36", "37", "38", "39", "40", "41", "42", "43", "44", "45", "46"
]

NEW_STATUS_IDS = ["6", "1"]

FAKE_KEYWORDS = [
    "fake", "replica", "rep", "1:1", "1v1", "copy", "counterfeit", "knockoff", "bootleg", "not original", "ua pair", "cloned",
    "faux", "fausse", "réplique", "replique", "copie", "contrefaçon", "imitation", "non authentique",
    "gefälscht", "kopia", "fälschung", "plagiat", "unecht",
    "fałszywy", "replika", "podróbka", "podrobka", "padělek",
    "falso", "imittazione", "réplica", "copia no original",
    "1в1", "репліка", "реплика", "копія", "копия", "фейк", "паль", "люкс", "не оригінал", "не оригинал"
]

DOMAINS = {
    "🇵🇱 Польща": {"code": "pl", "currency": "PLN"},
    "🇦Т Австрія": {"code": "at", "currency": "EUR"},
    "🇨🇿 Чехія": {"code": "cz", "currency": "CZK"},
    "🇱Т Литва": {"code": "lt", "currency": "EUR"},
    "🇷🇴 Румунія": {"code": "ro", "currency": "RON"},
    "🇩🇪 Німеччина": {"code": "de", "currency": "EUR"},
    "🇫🇷 Франція": {"code": "fr", "currency": "EUR"},
    "🇬🇧 Великобританія": {"code": "co.uk", "currency": "GBP"}
}

# ==================== ВАЛІДАЦІЯ НАЗВИ БРЕНДУ ====================
def is_valid_brand_name(name: str) -> bool:
    clean_name = name.strip()
    if len(clean_name) < 2:
        return False
    if re.search(r'(.)\1{2,}', clean_name.lower()):
        return False
    keyboard_patterns = ["qwerty", "asdfgh", "zxcvbn", "12345", "123456", "qwer", "asdf", "zxcv"]
    if any(pattern in clean_name.lower() for pattern in keyboard_patterns):
        return False
    if not re.match(r"^[a-zA-Z0-9\s\-\'\&\.а-щьюяа-щьюяіїєґА-ЩЬЮЯА-ЩЬЮЯІЇЄҐ]+$", clean_name):
        return False
    vowels = "aeiouyаеєиіоуюя"
    consonant_count = 0
    for char in clean_name.lower():
        if char.isalpha() and char not in vowels:
            consonant_count += 1
            if consonant_count >= 5:
                return False
        else:
            consonant_count = 0
    return True

# ==================== MONGODB СТАН ТА ЗМІННІ ====================
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["vinted_bot_db"]
keys_collection = db["keys"]
settings_collection = db["user_settings"]
seen_items_collection = db["seen_items"]

user_states = {}
temp_brand_storage = {}
active_users_cache = {}  
seen_items_cache = set()  # КЕШ У ПАМ'ЯТІ ДЛЯ МИТТЄВОЇ ПЕРЕВІРКИ ТОВАРІВ
last_update_id = 0
vinted_cookies = {}
processed_updates = set()

async def init_db_indexes():
    await seen_items_collection.create_index("item_id", unique=True)
    # Завантаження останніх 5000 побачених товарів у оперативку при старті
    async for doc in seen_items_collection.find().sort("_id", -1).limit(5000):
        seen_items_cache.add(str(doc.get("item_id")))

# ==================== РОБОТА З БД ====================
async def get_user_settings(user_id):
    uid_str = str(user_id)
    doc = await settings_collection.find_one({"user_id": uid_str})
    if not doc:
        doc = {
            "user_id": uid_str,
            "brands": [],
            "sizes": [],
            "domain": "at",
            "condition": "all",
            "active": False
        }
        await settings_collection.insert_one(doc)
    
    active_users_cache[int(user_id)] = doc.get("active", False)
    return doc

async def save_user_settings(user_id, settings):
    uid_str = str(user_id)
    settings["user_id"] = uid_str
    
    if "active" in settings:
        active_users_cache[int(user_id)] = settings["active"]

    await settings_collection.update_one(
        {"user_id": uid_str},
        {"$set": settings},
        upsert=True
    )

def is_item_seen_fast(item_id):
    return str(item_id) in seen_items_cache

async def mark_item_seen(item_id):
    item_str = str(item_id)
    seen_items_cache.add(item_str)
    if len(seen_items_cache) > 10000:
        seen_items_cache.clear()
    try:
        await seen_items_collection.update_one(
            {"item_id": item_str},
            {"$set": {"item_id": item_str, "added_at": datetime.now()}},
            upsert=True
        )
    except Exception:
        pass

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
        return f"{diff.days} днів, {diff.seconds // 3600} годин"

    return None

# ==================== КЛАВІАТУРИ ====================
async def get_main_keyboard(user_id):
    kb = []
    if not await is_user_active(user_id):
        kb.append([{"text": "🔑 Активувати ключ"}, {"text": "🛒 Придбати ключ"}])
        return {"keyboard": kb, "resize_keyboard": True, "persistent": True}

    kb.append([{"text": "➕ Додати / Редагувати бренд"}, {"text": "🖼 Пошук за фото"}])
    kb.append([{"text": "📏 Налаштувати розміри"}, {"text": "🌍 Обрати регіон"}])
    kb.append([{"text": "🏷 Стан товару"}, {"text": "🗑 Очистити бренди"}])
    kb.append([{"text": "📋 Мої налаштування"}])
    kb.append([{"text": "▶️ Запустити"}, {"text": "⏹ Зупинити"}])
    kb.append([{"text": "🔑 Активація / Стан ключа"}])
    if user_id == ADMIN_ID:
        kb.append([{"text": "👑 Адмін-панель"}])
    return {"keyboard": kb, "resize_keyboard": True, "persistent": True}

def get_confirm_clear_keyboard():
    return {
        "keyboard": [
            [{"text": "⚠️ Так, видалити всі бренди"}],
            [{"text": "🔙 Скасувати та повернутися"}]
        ],
        "resize_keyboard": True,
        "persistent": True
    }

async def get_brand_management_keyboard(user_id):
    cfg = await get_user_settings(user_id)
    brands = cfg.get("brands", [])
    
    kb = [[{"text": "🆕 Створити новий бренд"}]]
    if brands:
        kb.append([{"text": "💰 Змінити ціну існуючого бренду"}])
        kb.append([{"text": "✏️ Змінити назву бренду (залишити ціну)"}])
    kb.append([{"text": "🔙 Головне меню"}])
    return {"keyboard": kb, "resize_keyboard": True, "persistent": True}

async def get_user_brands_keyboard(user_id):
    cfg = await get_user_settings(user_id)
    brands = cfg.get("brands", [])
    kb = []
    for b in brands:
        name = b.get("name") if isinstance(b, dict) else str(b)
        price = b.get("max_price", "∞") if isinstance(b, dict) else "∞"
        kb.append([{"text": f"{name} ({price} EUR)"}])
    kb.append([{"text": "🔙 Головне меню"}])
    return {"keyboard": kb, "resize_keyboard": True, "persistent": True}

async def get_condition_panel_keyboard(user_id):
    cfg = await get_user_settings(user_id)
    cond = cfg.get("condition", "all")
    
    p_all = "✅ " if cond == "all" else ""
    p_new = "✅ " if cond == "new" else ""
    p_used = "✅ " if cond == "used" else ""

    kb = [
        [{"text": f"{p_all}🌐 Усі речі (Б/У + Нові)"}],
        [{"text": f"{p_new}✨ Тільки Нові"}, {"text": f"{p_used}🔄 Тільки Б/У"}],
        [{"text": "🔙 Головне меню"}]
    ]
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
    try:
        async with session.post(url, json=payload, timeout=2) as resp:
            return await resp.json()
    except Exception as e:
        logging.error(f"Telegram Send Error: {e}")

async def send_telegram_photo(session, chat_id, photo_url, caption, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    payload = {"chat_id": chat_id, "photo": photo_url, "caption": caption, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        async with session.post(url, json=payload, timeout=2) as resp:
            return await resp.json()
    except Exception as e:
        logging.error(f"Telegram Photo Error: {e}")

# ==================== OBSERVE & FETCH VINTED ====================
async def get_vinted_cookie(session, domain="at"):
    if domain in vinted_cookies:
        return vinted_cookies[domain]
    
    url = f"https://www.vinted.{domain}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36"
    }
    try:
        async with session.get(url, headers=headers, timeout=5) as resp:
            cookies = resp.cookies
            vinted_cookies[domain] = cookies
            return cookies
    except Exception as e:
        logging.error(f"Error fetching cookies for domain {domain}: {e}")
        return None

async def fetch_vinted_items(session, domain="at"):
    cookies = await get_vinted_cookie(session, domain)
    url = f"https://www.vinted.{domain}/api/v2/catalog/items?order=newest_first&per_page=30"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*"
    }
    try:
        async with session.get(url, headers=headers, cookies=cookies, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("items", [])
            elif resp.status == 401: # Cookie expired
                vinted_cookies.pop(domain, None)
    except Exception as e:
        logging.error(f"Error fetching items from Vinted ({domain}): {e}")
    return []

# ==================== РОЗСИЛКА ЗНАХІДОК ====================
async def process_and_notify_items(session, items, domain):
    if not items:
        return

    # Отримуємо всіх користувачів БД
    cursor = settings_collection.find({"active": True})
    active_users = await cursor.to_list(length=1000)

    for item in items:
        item_id = item.get("id")
        if not item_id or is_item_seen_fast(item_id):
            continue

        title = item.get("title", "")
        description = item.get("description", "")
        combined_text = f"{title} {description}".lower()

        # Фільтрація фейків
        if any(fake in combined_text for fake in FAKE_KEYWORDS):
            await mark_item_seen(item_id)
            continue

        price_amount = float(item.get("price", 0))
        currency = item.get("currency", "EUR")
        
        # Конвертуємо приблизно в UAH
        price_uah = round(price_amount * EUR_TO_UAH_RATE)
        
        brand_title = item.get("brand_title", "Unbranded")
        size_title = item.get("size_title", "Не вказано")
        item_url = item.get("url", f"https://www.vinted.{domain}/items/{item_id}")
        
        # Стан товару ID
        status_id = str(item.get("status_id", ""))
        is_new = status_id in NEW_STATUS_IDS

        photos = item.get("photos", [])
        photo_url = photos[0].get("url") if photos else "https://via.placeholder.com/400"

        # Сповіщаємо підходящих користувачів
        for user in active_users:
            user_id = int(user.get("user_id"))
            
            # Перевірка чи активована підписка
            if not await is_user_active(user_id):
                continue

            user_domain = user.get("domain", "at")
            if user_domain != domain:
                continue

            # Фільтр стану товару
            user_cond = user.get("condition", "all")
            if user_cond == "new" and not is_new:
                continue
            if user_cond == "used" and is_new:
                continue

            # Фільтр розмірів
            user_sizes = user.get("sizes", [])
            if user_sizes and size_title not in user_sizes:
                continue

            # Фільтр брендів та цін
            user_brands = user.get("brands", [])
            matched = False

            if not user_brands: # Якщо список брендів порожній -> шукаємо все
                matched = True
            else:
                for b in user_brands:
                    b_name = b.get("name", "").lower() if isinstance(b, dict) else str(b).lower()
                    b_max_price = float(b.get("max_price", float("inf"))) if isinstance(b, dict) else float("inf")
                    
                    if b_name in combined_text or b_name in brand_title.lower():
                        if price_amount <= b_max_price:
                            matched = True
                            break

            if matched:
                caption = (
                    f"🔥 **ЗНАЙДЕНО НОВИЙ ТОВАР!** 🔥\n\n"
                    f"📌 **Назва:** {title}\n"
                    f"🏷 **Бренд:** {brand_title}\n"
                    f"📏 **Розмір:** {size_title}\n"
                    f"💰 **Ціна:** {price_amount} {currency} (~{price_uah} UAH)\n"
                    f"✨ **Стан:** {'Новий' if is_new else 'Б/У'}\n"
                )
                await send_telegram_photo(session, user_id, photo_url, caption, get_item_keyboard(item_url))

        await mark_item_seen(item_id)

async def vinted_monitor_loop(session):
    while True:
        try:
            # Отримуємо всі унікальні домени активних користувачів
            domains_to_check = set()
            async for doc in settings_collection.find({"active": True}):
                domains_to_check.add(doc.get("domain", "at"))

            if not domains_to_check:
                domains_to_check.add("at")

            for dom in domains_to_check:
                items = await fetch_vinted_items(session, domain=dom)
                await process_and_notify_items(session, items, dom)
                await asyncio.sleep(0.5)

        except Exception as e:
            logging.error(f"Error in monitor loop: {e}")
        
        await asyncio.sleep(CHECK_INTERVAL)

# ==================== ОБРОБКА ПОВІДОМЛЕНЬ ====================
async def handle_update(session, update):
    update_id = update.get("update_id")
    if update_id in processed_updates:
        return
    processed_updates.add(update_id)
    if len(processed_updates) > 3000:
        processed_updates.clear()

    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "").strip()

        # ГЛОБАЛЬНЕ СКАСУВАННЯ ТА ПОВЕРНЕННЯ В МЕНЮ
        if text in ["/start", "меню", "Start", "start", "🔙 Головне меню", "🔙 Скасувати та повернутися"]:
            user_states[chat_id] = None
            temp_brand_storage.pop(chat_id, None)
            await send_telegram_message(
                session, 
                chat_id, 
                "👋 **Панель керування бота:**", 
                await get_main_keyboard(chat_id)
            )
            return

        if "photo" in msg:
            if not await is_user_active(chat_id):
                await send_telegram_message(session, chat_id, "🔒 **Доступ обмежено!** Натисніть кнопку активації ключа.")
                return

            await send_telegram_message(session, chat_id, "🔍 **Аналізую зображення...** Шукаю схожі речі на Vinted.")
            caption = msg.get("caption", "").strip()
            search_query = caption if caption else "Stone Island"
            
            cfg = await get_user_settings(chat_id)
            brands = cfg.get("brands", [])
            brands.append({"name": search_query, "max_price": float("inf")})
            cfg["brands"] = brands
            await save_user_settings(chat_id, cfg)

            await send_telegram_message(
                session, 
                chat_id, 
                f"✅ **Фото успішно оброблено!**\nДодано фільтр пошуку за фото: *{search_query}*", 
                await get_main_keyboard(chat_id)
            )
            return

        state = user_states.get(chat_id)

        if text == "⏹ Зупинити":
            cfg = await get_user_settings(chat_id)
            cfg["active"] = False
            active_users_cache[chat_id] = False
            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, "⏹ **Пошук повністю зупинено!**", await get_main_keyboard(chat_id))
            return

        if text == "▶️ Запустити":
            cfg = await get_user_settings(chat_id)
            cfg["active"] = True
            active_users_cache[chat_id] = True
            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, "🚀 **Пошук речей запущено!**", await get_main_keyboard(chat_id))
            return

        if text == "🗑 Очистити бренди":
            user_states[chat_id] = "confirm_clear_brands"
            await send_telegram_message(
                session, 
                chat_id, 
                "⚠️ **Ви дійсно хочете очистити весь список брендів?**\n\nЯкщо очистити бренди, бот почне надсилати **ВСІ** нові речі з Vinted підряд!", 
                get_confirm_clear_keyboard()
            )
            return

        if state == "confirm_clear_brands" and text == "⚠️ Так, видалити всі бренди":
            user_states[chat_id] = None
            cfg = await get_user_settings(chat_id)
            cfg["brands"] = []
            await save_user_settings(chat_id, cfg)
            await send_telegram_message(
                session, 
                chat_id, 
                "🗑 **Список брендів очищено.**\nБот тепер шукає всі товари поспіль.", 
                await get_main_keyboard(chat_id)
            )
            return

        if "🌐 Усі речі" in text or "✨ Тільки Нові" in text or "🔄 Тільки Б/У" in text:
            cfg = await get_user_settings(chat_id)
            if "Тільки Нові" in text:
                cfg["condition"] = "new"
                res_text = "✨ Встановлено фільтр: **Тільки нові речі**"
            elif "Тільки Б/У" in text:
                cfg["condition"] = "used"
                res_text = "🔄 Встановлено фільтр: **Тільки Б/У речі**"
            else:
                cfg["condition"] = "all"
                res_text = "🌐 Встановлено фільтр: **Усі речі (Б/У + Нові)**"

            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, res_text, await get_condition_panel_keyboard(chat_id))
            return

        if chat_id == ADMIN_ID:
            if text == "👑 Адмін-панель":
                user_states[chat_id] = None
                await send_telegram_message(session, chat_id, "👑 **Адміністративна панель:**", get_admin_keyboard())
                return

            if text == "➕ Згенерувати ключ":
                user_states[chat_id] = "waiting_gen_days"
                await send_telegram_message(session, chat_id, "Введіть термін дії ключа в днях (число, наприклад: `30`):")
                return

            if state == "waiting_gen_days":
                user_states[chat_id] = None
                if text.isdigit():
                    days = int(text)
                    new_key = await generate_random_key(days)
                    await send_telegram_message(session, chat_id, f"✅ **Ключ успішно створено!**\n\n`{new_key}`\n\nТермін дії: *{days} днів*", get_admin_keyboard())
                else:
                    await send_telegram_message(session, chat_id, "❌ Будь ласка, введіть тільки число цифрами.", get_admin_keyboard())
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

        clean_reg_text = text.replace("✅ ", "")
        if clean_reg_text in DOMAINS:
            cfg = await get_user_settings(chat_id)
            cfg["domain"] = DOMAINS[clean_reg_text]["code"]
            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, f"✅ Регіон змінено на: *{clean_reg_text}*", await get_region_panel_keyboard(chat_id))
            return

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

        # --- КЕРУВАННЯ БРЕНДАМИ ТА ЦІНАМИ ---
        if text in ["➕ Додати бренд", "➕ Додати / Редагувати бренд"]:
            await send_telegram_message(session, chat_id, "⚙️ **Оберіть потрібну дію:**", await get_brand_management_keyboard(chat_id))
            return

        if text == "🆕 Створити новий бренд":
            user_states[chat_id] = "waiting_step1_brand_name"
            await send_telegram_message(session, chat_id, "Напишіть **назву нового бренду** (наприклад: `Nike`):")
            return

        if text == "💰 Змінити ціну існуючого бренду":
            user_states[chat_id] = "select_brand_for_price_change"
            await send_telegram_message(session, chat_id, "Оберіть бренд, для якого бажаєте змінити ціну:", await get_user_brands_keyboard(chat_id))
            return

        if text == "✏️ Змінити назву бренду (залишити ціну)":
            user_states[chat_id] = "select_brand_for_name_change"
            await send_telegram_message(session, chat_id, "Оберіть бренд, який хочете перейменувати:", await get_user_brands_keyboard(chat_id))
            return

        if state == "select_brand_for_price_change":
            raw_selected = text.split(" (")[0]
            cfg = await get_user_settings(chat_id)
            found = False
            for b in cfg.get("brands", []):
                b_name = b.get("name") if isinstance(b, dict) else str(b)
                if b_name.lower() == raw_selected.lower():
                    temp_brand_storage[chat_id] = b_name
                    found = True
                    break
            if found:
                user_states[chat_id] = "waiting_new_price_only"
                await send_telegram_message(session, chat_id, f"Введіть **нову максимальну ціну** для бренду *{temp_brand_storage[chat_id]}* в EUR (наприклад: `50`):")
            else:
                await send_telegram_message(session, chat_id, "❌ Бренд не знайдено в списку.", await get_main_keyboard(chat_id))
            return

        if state == "waiting_new_price_only":
            brand_name = temp_brand_storage.get(chat_id, "")
            price_digits = re.findall(r"\d+(?:\.\d+)?", text.replace(",", "."))
            if not price_digits:
                await send_telegram_message(session, chat_id, "❌ **Введіть суму цифрами!** (наприклад: `50`) ")
                return

            new_price = float(price_digits[0])
            user_states[chat_id] = None
            temp_brand_storage.pop(chat_id, None)

            cfg = await get_user_settings(chat_id)
            for b in cfg.get("brands", []):
                if isinstance(b, dict) and b.get("name", "").lower() == brand_name.lower():
                    b["max_price"] = new_price

            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, f"✅ **Ціну оновлено!**\n🏷 Бренд: *{brand_name}*\n💵 Нова макс. ціна: *{new_price} EUR*", await get_main_keyboard(chat_id))
            return

        if state == "select_brand_for_name_change":
            raw_selected = text.split(" (")[0]
            cfg = await get_user_settings(chat_id)
            found_price = None
            for b in cfg.get("brands", []):
                b_name = b.get("name") if isinstance(b, dict) else str(b)
                if b_name.lower() == raw_selected.lower():
                    found_price = b.get("max_price", float("inf")) if isinstance(b, dict) else float("inf")
                    temp_brand_storage[chat_id] = {"old_name": b_name, "price": found_price}
                    break
            if found_price is not None:
                user_states[chat_id] = "waiting_new_name_only"
                await send_telegram_message(session, chat_id, f"Введіть **нову назву** для бренду *{raw_selected}* (ціна залишиться: *{found_price} EUR*):")
            else:
                await send_telegram_message(session, chat_id, "❌ Бренд не знайдено в списку.", await get_main_keyboard(chat_id))
            return

        if state == "waiting_new_name_only":
            old_data = temp_brand_storage.get(chat_id, {})
            old_name = old_data.get("old_name")
            curr_price = old_data.get("price")
            new_name = text.strip()

            if not is_valid_brand_name(new_name):
                await send_telegram_message(session, chat_id, "❌ **Некоректна назва бренду!** Будь ласка, введіть реальну назву:")
                return

            cfg = await get_user_settings(chat_id)
            for b in cfg.get("brands", []):
                existing_name = b.get("name") if isinstance(b, dict) else str(b)
                if existing_name.lower() == new_name.lower():
                    await send_telegram_message(session, chat_id, f"❌ Бренд *{new_name}* вже є у вашому списку!", await get_main_keyboard(chat_id))
                    user_states[chat_id] = None
                    temp_brand_storage.pop(chat_id, None)
                    return

            user_states[chat_id] = None
            temp_brand_storage.pop(chat_id, None)

            for b in cfg.get("brands", []):
                if isinstance(b, dict) and b.get("name", "").lower() == old_name.lower():
                    b["name"] = new_name

            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, f"✅ **Назву бренду змінено!**\n🏷 Було: *{old_name}*\n🏷 Стало: *{new_name}*\n💵 Ціна збережена: *{curr_price} EUR*", await get_main_keyboard(chat_id))
            return

        if state == "waiting_step1_brand_name":
            brand_name_candidate = text.strip()

            if not is_valid_brand_name(brand_name_candidate):
                await send_telegram_message(
                    session, 
                    chat_id, 
                    "❌ **Некоректна назва бренду!** Напишіть нормальну назву бренду (наприклад: `Stone Island`):"
                )
                return

            cfg = await get_user_settings(chat_id)
            for b in cfg.get("brands", []):
                existing_name = b.get("name") if isinstance(b, dict) else str(b)
                if existing_name.lower() == brand_name_candidate.lower():
                    user_states[chat_id] = None
                    await send_telegram_message(
                        session, 
                        chat_id, 
                        f"⚠️ **Бренд *{existing_name}* вже є у вашому списку!**", 
                        await get_brand_management_keyboard(chat_id)
                    )
                    return

            temp_brand_storage[chat_id] = brand_name_candidate
            user_states[chat_id] = "waiting_step2_brand_price"
            await send_telegram_message(
                session, 
                chat_id, 
                f"🏷 Бренд: *{brand_name_candidate}*\n\nТепер напишіть **максимальну ціну** для цього бренду цифрами (наприклад: `75`):"
            )
            return

        if state == "waiting_step2_brand_price":
            brand_name = temp_brand_storage.get(chat_id, "")
            price_digits = re.findall(r"\d+(?:\.\d+)?", text.replace(",", "."))
            if not price_digits:
                await send_telegram_message(session, chat_id, "❌ **Введіть суму цифрами!** (наприклад: `75`) ")
                return

            max_price = float(price_digits[0])
            user_states[chat_id] = None
            temp_brand_storage.pop(chat_id, None)

            cfg = await get_user_settings(chat_id)
            brands = cfg.get("brands", [])

            updated = False
            for b in brands:
                if isinstance(b, dict) and b.get("name", "").lower() == brand_name.lower():
                    b["max_price"] = max_price
                    updated = True
                    break

            if not updated:
                brands.append({"name": brand_name, "max_price": max_price})

            cfg["brands"] = brands
            await save_user_settings(chat_id, cfg)
            await send_telegram_message(
                session, 
                chat_id, 
                f"✅ **Успішно збережено!**\n\n🏷 Бренд: *{brand_name}*\n💵 Макс. ціна: *{max_price} EUR*", 
                await get_main_keyboard(chat_id)
            )
            return

        if state == "waiting_for_key" or text.startswith("VINTED-"):
            user_states[chat_id] = None
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
                await send_telegram_message(session, chat_id, f"🎉 **Ключ успішно активовано на {days_to_add} днів!**", await get_main_keyboard(chat_id))
            else:
                await send_telegram_message(session, chat_id, "❌ **Невірний або вже використаний ключ.**", await get_main_keyboard(chat_id))
            return

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

        elif text == "🖼 Пошук за фото":
            await send_telegram_message(session, chat_id, "📸 **Просто надішліть фотографію або скріншот речі в цей чат!**")

        elif text == "🏷 Стан товару":
            await send_telegram_message(session, chat_id, "Оберіть стан товарів для пошуку:", await get_condition_panel_keyboard(chat_id))

        elif text == "📏 Налаштувати розміри":
            await send_telegram_message(session, chat_id, "Оберіть розміри на нижній панелі:", await get_sizes_panel_keyboard(chat_id))

        elif text == "🧹 Очистити розміри":
            cfg = await get_user_settings(chat_id)
            cfg["sizes"] = []
            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, "🧹 Розміри скинуто (шукаємо всі розміри).", await get_main_keyboard(chat_id))

        elif text == "🌍 Обрати регіон":
            await send_telegram_message(session, chat_id, "Оберіть країну з панелі нижче:", await get_region_panel_keyboard(chat_id))

        elif text == "📋 Мої налаштування":
            cfg = await get_user_settings(chat_id)
            raw_brands = cfg.get("brands", [])
            
            formatted_brands = []
            for b in raw_brands:
                if isinstance(b, dict):
                    formatted_brands.append(f"{b.get('name')} (до {b.get('max_price')} EUR)")
                else:
                    formatted_brands.append(str(b))

            brands_str = "\n• " + "\n• ".join(formatted_brands) if formatted_brands else "Пошук ВСІХ нових речей поспіль!"
            sizes = ", ".join(cfg.get("sizes", [])) or "Всі розміри"
            domain = cfg.get("domain", "at").upper()
            status = "🟢 Активний" if cfg.get("active") else "🔴 Зупинений"
            
            cond_map = {"all": "🌐 Усі речі", "new": "✨ Тільки Нові", "used": "🔄 Тільки Б/У"}
            condition_str = cond_map.get(cfg.get("condition", "all"), "Усі речі")

            msg_text = (
                f"📋 **Ваші поточні налаштування:**\n\n"
                f"Статус пошуку: *{status}*\n"
                f"Регіон: *{domain}*\n"
                f"Стан: *{condition_str}*\n"
                f"Розміри: *{sizes}*\n\n"
                f"🏷 **Відстежувані бренди:**{brands_str}"
            )
            await send_telegram_message(session, chat_id, msg_text, await get_main_keyboard(chat_id))

        else:
            await send_telegram_message(session, chat_id, "⚙️ Скористайтеся меню нижче:", await get_main_keyboard(chat_id))

# ==================== СЕРВЕР ТА LONG POLLING ====================
async def telegram_polling_loop(session):
    global last_update_id
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    while True:
        try:
            params = {"offset": last_update_id + 1, "timeout": 10}
            async with session.get(url, params=params, timeout=12) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for update in data.get("result", []):
                        last_update_id = update["update_id"]
                        asyncio.create_task(handle_update(session, update))
        except Exception as e:
            logging.error(f"Polling error: {e}")
        await asyncio.sleep(0.5)

async def start_background_tasks(app):
    await init_db_indexes()
    app['http_session'] = aiohttp.ClientSession()
    app['polling_task'] = asyncio.create_task(telegram_polling_loop(app['http_session']))
    app['vinted_task'] = asyncio.create_task(vinted_monitor_loop(app['http_session']))

async def cleanup_background_tasks(app):
    app['polling_task'].cancel()
    app['vinted_task'].cancel()
    await app['http_session'].close()

def main():
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)

    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)

    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
