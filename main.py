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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ==================== НАЛАШТУВАННЯ ====================
BOT_TOKEN = "8877190549:AAEoSIj_dOL2hi-PpDrfZFJi6h8x40hJnFQ"
ADMIN_ID = 8138110821

MONGO_URI = "mongodb+srv://kykarikyzerick_db_user:CVz4czwK06sgQlSP@cluster0.xuoxdku.mongodb.net/?appName=Cluster0"

CHECK_INTERVAL = 0.05  
ALLOWED_USERS = [8138110821]
EUR_TO_UAH_RATE = 51.0
MAX_ITEM_AGE_SECONDS = 1800  

MASTER_KEYS = {
    "VINTED-VIP-2026": 365,
    "VINTED-FREE-TEST": 30,
    "VINTED-KEY-7DAYS": 7
}

SIZES_LIST = [
    "XS", "S", "M", "L", "XL", "XXL",
    "36", "37", "38", "39", "40", "41", "42", "43", "44", "45", "46"
]

NEW_STATUS_IDS = ["6", "1", "2", "10"]

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

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
]

# ==================== ВАЛІДАЦІЯ ====================
def is_valid_brand_name(name: str) -> bool:
    clean_name = name.strip()
    if len(clean_name) < 2 or re.search(r'(.)\1{2,}', clean_name.lower()):
        return False
    keyboard_patterns = ["qwerty", "asdfgh", "zxcvbn", "12345", "123456", "qwer", "asdf", "zxcv"]
    if any(pattern in clean_name.lower() for pattern in keyboard_patterns):
        return False
    if not re.match(r"^[a-zA-Z0-9\s\-\'\&\.а-щьюяа-щьюяіїєґА-ЩЬЮЯА-ЩЬЮЯІЇЄҐ]+$", clean_name):
        return False
    return True

# ==================== MONGODB СТАН ====================
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["vinted_bot_db"]
keys_collection = db["keys"]
settings_collection = db["user_settings"]
seen_items_collection = db["seen_items"]

user_states = {}
temp_brand_storage = {}
active_users_cache = {}  
seen_items_cache = set()
vinted_session_data = {} 
processed_updates = set()

async def init_db_indexes():
    try:
        await seen_items_collection.create_index("item_id", unique=True)
        async for doc in seen_items_collection.find().sort("_id", -1).limit(20000):
            seen_items_cache.add(str(doc.get("item_id")))
        logging.info(f"Завантажено {len(seen_items_cache)} товарів у RAM кеш.")
    except Exception as e:
        logging.error(f"Error initializing DB indexes: {e}")

async def get_user_settings(user_id):
    uid_str = str(user_id)
    try:
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
    except Exception as e:
        logging.error(f"DB Error in get_user_settings: {e}")
        return {"user_id": uid_str, "brands": [], "sizes": [], "domain": "at", "condition": "all", "active": False}

async def save_user_settings(user_id, settings):
    uid_str = str(user_id)
    settings["user_id"] = uid_str
    if "active" in settings:
        active_users_cache[int(user_id)] = settings["active"]

    try:
        await settings_collection.update_one(
            {"user_id": uid_str},
            {"$set": settings},
            upsert=True
        )
    except Exception as e:
        logging.error(f"DB Error in save_user_settings: {e}")

# ==================== СИСТЕМА БОРОТЬБИ З ДУБЛІКАТАМИ ====================
def is_item_seen_fast(item_id):
    return str(item_id) in seen_items_cache

async def save_item_to_db(item_str):
    try:
        await seen_items_collection.update_one(
            {"item_id": item_str},
            {"$set": {"item_id": item_str, "added_at": datetime.now()}},
            upsert=True
        )
    except Exception:
        pass

def mark_item_seen_and_save(item_id):
    item_str = str(item_id)
    seen_items_cache.add(item_str)
    
    if len(seen_items_cache) > 50000:
        items_to_remove = list(seen_items_cache)[:10000]
        for k in items_to_remove:
            seen_items_cache.discard(k)

    asyncio.create_task(save_item_to_db(item_str))

async def get_key_data(key_code):
    try:
        return await keys_collection.find_one({"key": key_code})
    except Exception as e:
        logging.error(f"DB Error in get_key_data: {e}")
        return None

async def save_key_data(key_code, data):
    data["key"] = key_code
    try:
        await keys_collection.update_one(
            {"key": key_code},
            {"$set": data},
            upsert=True
        )
    except Exception as e:
        logging.error(f"DB Error in save_key_data: {e}")

async def health_check(request):
    return web.Response(text="Bot is active")

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
    try:
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
    except Exception as e:
        logging.error(f"DB Error in is_user_active: {e}")

    return False

async def get_key_remaining_time(user_id):
    if user_id in ALLOWED_USERS or user_id == ADMIN_ID:
        return "Безлімітний доступ (Адмін/VIP)"

    now = datetime.now()
    latest_exp = None
    try:
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
    except Exception as e:
        logging.error(f"DB Error in get_key_remaining_time: {e}")

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
        kb.append([{"text": "💰 Змінити ціну існуючного бренду"}])
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
        async with session.post(url, json=payload, timeout=5) as resp:
            return await resp.json()
    except Exception as e:
        logging.error(f"Telegram Send Error [{chat_id}]: {e}")

async def send_telegram_photo(session, chat_id, photo_url, caption, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    payload = {"chat_id": chat_id, "photo": photo_url, "caption": caption, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        async with session.post(url, json=payload, timeout=5) as resp:
            return await resp.json()
    except Exception as e:
        logging.error(f"Telegram Photo Error [{chat_id}]: {e}")

# ==================== VINTED API ====================
async def refresh_vinted_session(session, domain="at"):
    url = f"https://www.vinted.{domain}"
    user_agent = random.choice(USER_AGENTS)
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive"
    }
    try:
        async with session.get(url, headers=headers, timeout=5) as resp:
            if resp.status == 200:
                cookies = resp.cookies
                text = await resp.text()
                token_match = re.search(r'"accessToken":"([^"]+)"', text)
                access_token = token_match.group(1) if token_match else None
                
                vinted_session_data[domain] = {
                    "cookies": cookies,
                    "token": access_token,
                    "user_agent": user_agent,
                    "updated_at": time.time()
                }
                return vinted_session_data[domain]
    except Exception as e:
        logging.error(f"Failed session refresh [{domain}]: {e}")
    return None

async def fetch_vinted_brand_items(session, domain="at", brand_query=None):
    sess = vinted_session_data.get(domain)
    if not sess or (time.time() - sess.get("updated_at", 0) > 600):
        sess = await refresh_vinted_session(session, domain)

    if not sess:
        return []

    if brand_query:
        url = f"https://www.vinted.{domain}/api/v2/catalog/items?search_text={brand_query}&order=newest_first&per_page=15"
    else:
        url = f"https://www.vinted.{domain}/api/v2/catalog/items?order=newest_first&per_page=15"

    headers = {
        "User-Agent": sess["user_agent"],
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://www.vinted.{domain}/catalog"
    }
    
    if sess.get("token"):
        headers["Authorization"] = f"Bearer {sess['token']}"

    try:
        async with session.get(url, headers=headers, cookies=sess["cookies"], timeout=4) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("items", [])
            elif resp.status in [401, 403, 429]:
                vinted_session_data.pop(domain, None)
    except Exception:
        pass
    return []

# ==================== ШВИДКА ОБРОБКА БЕЗ ДУБЛІВ ====================
async def process_and_notify_items(session, items, domain, active_users):
    if not items or not active_users:
        return

    now_ts = time.time()

    for item in items:
        item_id = item.get("id")
        
        if not item_id or is_item_seen_fast(item_id):
            continue

        mark_item_seen_and_save(item_id)

        created_at_ts = item.get("created_at_ts")
        if created_at_ts:
            if created_at_ts > 1000000000000:  
                created_at_ts /= 1000.0

            age = now_ts - created_at_ts
            if age > MAX_ITEM_AGE_SECONDS:  
                continue

        title = item.get("title", "")
        description = item.get("description", "") or ""
        combined_text = f"{title} {description}".lower()

        if any(fake in combined_text for fake in FAKE_KEYWORDS):
            continue

        raw_price = item.get("price")
        if isinstance(raw_price, dict):
            price_amount = float(raw_price.get("amount") or raw_price.get("price") or 0)
        else:
            try:
                price_amount = float(raw_price) if raw_price is not None else 0.0
            except (ValueError, TypeError):
                price_amount = 0.0

        currency = item.get("currency", "EUR")
        if isinstance(currency, dict):
            currency = currency.get("code", "EUR")

        price_uah = round(price_amount * EUR_TO_UAH_RATE)
        
        brand_title = item.get("brand_title", "Unbranded")
        size_title = item.get("size_title", "Не вказано")
        item_url = item.get("url", f"https://www.vinted.{domain}/items/{item_id}")
        
        status_id = str(item.get("status_id", ""))
        is_new = status_id in NEW_STATUS_IDS

        photos = item.get("photos", [])
        photo_url = photos[0].get("url") if photos else "https://via.placeholder.com/400"

        for user in active_users:
            user_id = int(user.get("user_id"))

            if not active_users_cache.get(user_id, False) or not await is_user_active(user_id):
                continue

            user_domain = user.get("domain", "at")
            if user_domain != domain:
                continue

            user_cond = user.get("condition", "all")
            if user_cond == "new" and not is_new:
                continue
            if user_cond == "used" and is_new:
                continue

            user_sizes = user.get("sizes", [])
            if user_sizes and size_title not in user_sizes:
                continue

            user_brands = user.get("brands", [])
            matched = False

            if not user_brands:
                matched = True
            else:
                for b in user_brands:
                    b_name = b.get("name", "").lower() if isinstance(b, dict) else str(b).lower()
                    b_max_price_raw = b.get("max_price", float("inf")) if isinstance(b, dict) else float("inf")
                    try:
                        b_max_price = float(b_max_price_raw)
                    except (ValueError, TypeError):
                        b_max_price = float("inf")
                    
                    if b_name in combined_text or b_name in brand_title.lower():
                        if price_amount <= b_max_price:
                            matched = True
                            break

            if matched:
                caption = (
                    f"⚡️ **НОВИЙ ТОВАР!** ⚡️\n\n"
                    f"📌 **Назва:** {title}\n"
                    f"🏷 **Бренд:** {brand_title}\n"
                    f"📏 **Розмір:** {size_title}\n"
                    f"💰 **Ціна:** {price_amount} {currency} (~{price_uah} UAH)\n"
                    f"✨ **Стан:** {'Новий' if is_new else 'Б/У'}\n"
                )
                asyncio.create_task(send_telegram_photo(session, user_id, photo_url, caption, get_item_keyboard(item_url)))

async def fetch_and_notify(session, dom, b_name, active_users):
    items = await fetch_vinted_brand_items(session, domain=dom, brand_query=b_name)
    if items:
        await process_and_notify_items(session, items, dom, active_users)

async def vinted_monitor_loop(session):
    while True:
        try:
            cursor = settings_collection.find({"active": True})
            active_users = await cursor.to_list(length=1000)

            if any(active_users_cache.get(int(u.get("user_id")), False) for u in active_users):
                tasks = []
                for user in active_users:
                    u_id = int(user.get("user_id"))
                    if not active_users_cache.get(u_id, False):
                        continue

                    dom = user.get("domain", "at")
                    user_brands = user.get("brands", [])
                    
                    if user_brands:
                        for b in user_brands:
                            b_name = b.get("name") if isinstance(b, dict) else str(b)
                            tasks.append(fetch_and_notify(session, dom, b_name, active_users))
                    else:
                        tasks.append(fetch_and_notify(session, dom, None, active_users))

                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            logging.error(f"Error in monitor loop: {e}")
        
        await asyncio.sleep(CHECK_INTERVAL)

# ==================== ОБРОБКА ПОВІДОМЛЕНЬ ====================
async def safe_handle_update(session, update):
    try:
        await handle_update(session, update)
    except Exception as e:
        logging.error(f"Error handling update {update.get('update_id')}: {e}")

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
                await send_telegram_message(session, chat_id, "🔒 **Доступ обмежено!** Активуйте ключ.")
                return

            await send_telegram_message(session, chat_id, "🔍 **Аналізую зображення...**")
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
                f"✅ **Фото оброблено!**\nДодано фільтр: *{search_query}*", 
                await get_main_keyboard(chat_id)
            )
            return

        state = user_states.get(chat_id)

        if text == "⏹ Зупинити":
            active_users_cache[chat_id] = False
            cfg = await get_user_settings(chat_id)
            cfg["active"] = False
            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, "⏹ **Пошук повністю зупинено!**", await get_main_keyboard(chat_id))
            return

        if text == "▶️ Запустити":
            active_users_cache[chat_id] = True
            cfg = await get_user_settings(chat_id)
            cfg["active"] = True
            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, "🚀 **Пошук речей запущено!**", await get_main_keyboard(chat_id))
            return

        if text == "🗑 Очистити бренди":
            user_states[chat_id] = "confirm_clear_brands"
            await send_telegram_message(
                session, 
                chat_id, 
                "⚠️ **Ви дійсно хочете очистити весь список брендів?**", 
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
                "🗑 **Список брендів очищено.** Бот шукає всі речі підряд.", 
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
                await send_telegram_message(session, chat_id, "Введіть термін дії ключа в днях:")
                return

            if state == "waiting_gen_days":
                user_states[chat_id] = None
                if text.isdigit():
                    days = int(text)
                    new_key = await generate_random_key(days)
                    await send_telegram_message(session, chat_id, f"✅ **Ключ успішно створено!**\n\n`{new_key}`\n\nТермін дій: *{days} днів*", get_admin_keyboard())
                else:
                    await send_telegram_message(session, chat_id, "❌ Введіть число цифрами.", get_admin_keyboard())
                return

            if text == "📊 Статистика":
                total_keys = await keys_collection.count_documents({})
                used_keys = await keys_collection.count_documents({"is_used": True})
                total_users = await settings_collection.count_documents({})

                stat_msg = f"📊 **Статистика бота:**\n\n🔑 Всього ключів: *{total_keys}*\n✅ Активовано ключів: *{used_keys}*\n👥 Користувачів: *{total_users}*"
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
            await send_telegram_message(session, chat_id, "📏 Оновлено розміри:", await get_sizes_panel_keyboard(chat_id))
            return

        if text in ["➕ Додати бренд", "➕ Додати / Редагувати бренд"]:
            await send_telegram_message(session, chat_id, "⚙️ **Оберіть дію:**", await get_brand_management_keyboard(chat_id))
            return

        if text == "🆕 Створити новий бренд":
            user_states[chat_id] = "waiting_step1_brand_name"
            await send_telegram_message(session, chat_id, "Напишіть **назву бренду**:")
            return

        if text == "💰 Змінити ціну існуючного бренду":
            user_states[chat_id] = "select_brand_for_price_change"
            await send_telegram_message(session, chat_id, "Оберіть бренд для зміни ціни:", await get_user_brands_keyboard(chat_id))
            return

        if text == "✏️ Змінити назву бренду (залишити ціну)":
            user_states[chat_id] = "select_brand_for_name_change"
            await send_telegram_message(session, chat_id, "Оберіть бренд для перейменування:", await get_user_brands_keyboard(chat_id))
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
                await send_telegram_message(session, chat_id, f"Введіть **нову ціну** в EUR для *{temp_brand_storage[chat_id]}*:")
            else:
                await send_telegram_message(session, chat_id, "❌ Бренд не знайдено.", await get_main_keyboard(chat_id))
            return

        if state == "waiting_new_price_only":
            brand_name = temp_brand_storage.get(chat_id, "")
            price_digits = re.findall(r"\d+(?:\.\d+)?", text.replace(",", "."))
            if not price_digits:
                await send_telegram_message(session, chat_id, "❌ Введіть суму цифрами!")
                return

            new_price = float(price_digits[0])
            user_states[chat_id] = None
            temp_brand_storage.pop(chat_id, None)

            cfg = await get_user_settings(chat_id)
            for b in cfg.get("brands", []):
                if isinstance(b, dict) and b.get("name", "").lower() == brand_name.lower():
                    b["max_price"] = new_price

            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, f"✅ **Ціну оновлено!**\n🏷 Бренд: *{brand_name}*\n💵 Макс. ціна: *{new_price} EUR*", await get_main_keyboard(chat_id))
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
                await send_telegram_message(session, chat_id, f"Введіть **нову назву** для *{raw_selected}*:")
            else:
                await send_telegram_message(session, chat_id, "❌ Бренд не знайдено.", await get_main_keyboard(chat_id))
            return

        if state == "waiting_new_name_only":
            old_data = temp_brand_storage.get(chat_id, {})
            old_name = old_data.get("old_name")
            new_name = text.strip()

            if not is_valid_brand_name(new_name):
                await send_telegram_message(session, chat_id, "❌ **Некоректна назва бренду!**")
                return

            user_states[chat_id] = None
            temp_brand_storage.pop(chat_id, None)

            cfg = await get_user_settings(chat_id)
            for b in cfg.get("brands", []):
                if isinstance(b, dict) and b.get("name", "").lower() == old_name.lower():
                    b["name"] = new_name

            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, f"✅ **Назву змінено!**\n🏷 Стало: *{new_name}*", await get_main_keyboard(chat_id))
            return

        if state == "waiting_step1_brand_name":
            brand_name_candidate = text.strip()
            if not is_valid_brand_name(brand_name_candidate):
                await send_telegram_message(session, chat_id, "❌ Некоректна назва бренду!")
                return

            temp_brand_storage[chat_id] = brand_name_candidate
            user_states[chat_id] = "waiting_step2_brand_price"
            await send_telegram_message(session, chat_id, f"Введіть **максимальну ціну** для *{brand_name_candidate}* (або `безліміт`):")
            return

        if state == "waiting_step2_brand_price":
            brand_name = temp_brand_storage.get(chat_id, "")
            price_val = float("inf")

            if not any(kw in text.lower() for kw in ["безліміт", "безлимит", "0", "no"]):
                price_digits = re.findall(r"\d+(?:\.\d+)?", text.replace(",", "."))
                if price_digits:
                    price_val = float(price_digits[0])
                else:
                    await send_telegram_message(session, chat_id, "❌ Напишіть число або `безліміт`:")
                    return

            user_states[chat_id] = None
            temp_brand_storage.pop(chat_id, None)

            cfg = await get_user_settings(chat_id)
            brands = cfg.get("brands", [])
            brands.append({"name": brand_name, "max_price": price_val})
            cfg["brands"] = brands
            await save_user_settings(chat_id, cfg)

            p_str = f"{price_val} EUR" if price_val != float("inf") else "Безліміт"
            await send_telegram_message(session, chat_id, f"✅ **Бренд додано!**\n🏷 Назва: *{brand_name}*\n💵 Макс. ціна: *{p_str}*", await get_main_keyboard(chat_id))
            return

        if text in ["🔑 Активувати ключ", "🔑 Активація / Стан ключа"]:
            time_left = await get_key_remaining_time(chat_id)
            status_str = f"⏳ **Залишок підписки:** {time_left}\n\n" if time_left else "❌ **Немає активної підписки.**\n\n"
            user_states[chat_id] = "waiting_key_input"
            await send_telegram_message(session, chat_id, f"{status_str}Введіть ваш ключ:")
            return

        if state == "waiting_key_input":
            user_states[chat_id] = None
            key_code = text.strip()

            if key_code in MASTER_KEYS:
                days = MASTER_KEYS[key_code]
                exp_date = datetime.now() + timedelta(days=days)
                await save_key_data(key_code, {
                    "duration_days": days,
                    "is_used": True,
                    "used_by": chat_id,
                    "expires_at": exp_date.strftime("%Y-%m-%d %H:%M:%S")
                })
                await send_telegram_message(session, chat_id, f"🎉 **Ключ активовано!** Доступ на *{days} днів*.", await get_main_keyboard(chat_id))
                return

            key_data = await get_key_data(key_code)
            if key_data and not key_data.get("is_used"):
                days = key_data.get("duration_days", 30)
                exp_date = datetime.now() + timedelta(days=days)
                key_data["is_used"] = True
                key_data["used_by"] = chat_id
                key_data["expires_at"] = exp_date.strftime("%Y-%m-%d %H:%M:%S")
                await save_key_data(key_code, key_data)
                await send_telegram_message(session, chat_id, f"🎉 **Ключ активовано!** Доступ на *{days} днів*.", await get_main_keyboard(chat_id))
            else:
                await send_telegram_message(session, chat_id, "❌ **Недійсний ключ!**", await get_main_keyboard(chat_id))
            return

        if text == "📏 Налаштувати розміри":
            await send_telegram_message(session, chat_id, "📏 **Оберіть розміри:**", await get_sizes_panel_keyboard(chat_id))
            return

        if text == "🧹 Очистити розміри":
            cfg = await get_user_settings(chat_id)
            cfg["sizes"] = []
            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, "🧹 **Розміри очищено!**", await get_sizes_panel_keyboard(chat_id))
            return

        if text == "🏷 Стан товару":
            await send_telegram_message(session, chat_id, "🏷 **Оберіть стан:**", await get_condition_panel_keyboard(chat_id))
            return

        if text == "🌍 Обрати регіон":
            await send_telegram_message(session, chat_id, "🌍 **Оберіть країну:**", await get_region_panel_keyboard(chat_id))
            return

        if text == "📋 Мої налаштування":
            cfg = await get_user_settings(chat_id)
            b_list = cfg.get("brands", [])
            brands_fmt = []
            for b in b_list:
                b_n = b.get("name") if isinstance(b, dict) else str(b)
                b_p = f"{b.get('max_price')} EUR" if isinstance(b, dict) and b.get("max_price") != float("inf") else "Безліміт"
                brands_fmt.append(f"• {b_n} (до {b_p})")

            b_str = "\n".join(brands_fmt) if brands_fmt else "Усі бренди"
            s_str = ", ".join(cfg.get("sizes", [])) if cfg.get("sizes") else "Усі розміри"
            reg = cfg.get("domain", "at").upper()
            cond_map = {"all": "Усі (Б/У + Нові)", "new": "Тільки Нові", "used": "Тільки Б/У"}
            cond_str = cond_map.get(cfg.get("condition", "all"), "Усі")
            st_str = "🟢 Активний" if cfg.get("active") else "🔴 Зупинений"

            info_msg = (
                f"⚙️ **Поточні налаштування:**\n\n"
                f"📊 **Статус:** {st_str}\n"
                f"🌍 **Регіон:** {reg}\n"
                f"🏷 **Стан:** {cond_str}\n"
                f"📏 **Розміри:** {s_str}\n\n"
                f"📋 **Бренди:**\n{b_str}"
            )
            await send_telegram_message(session, chat_id, info_msg, await get_main_keyboard(chat_id))
            return

        if text == "🛒 Придбати ключ":
            await send_telegram_message(session, chat_id, f"🛒 Зверніться до адміна: tg://user?id={ADMIN_ID}")
            return

# ==================== MAIN LOOP ====================
async def telegram_polling_loop(session):
    last_update_id = 0
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    while True:
        try:
            params = {"offset": last_update_id + 1, "timeout": 10}
            async with session.get(url, params=params, timeout=12) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for update in data.get("result", []):
                        last_update_id = max(last_update_id, update["update_id"])
                        asyncio.create_task(safe_handle_update(session, update))
        except Exception as e:
            logging.error(f"Error in polling loop: {e}")
        await asyncio.sleep(0.1)

async def main():
    await init_db_indexes()
    async with aiohttp.ClientSession() as session:
        app = web.Application()
        app.router.add_get('/', health_check)
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.environ.get("PORT", 8080))
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        logging.info(f"Server started on port {port}")

        await asyncio.gather(
            telegram_polling_loop(session),
            vinted_monitor_loop(session)
        )

if __name__ == "__main__":
    asyncio.run(main())
