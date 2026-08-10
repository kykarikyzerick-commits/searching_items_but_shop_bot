import asyncio
import logging
import os
import random
import re
import string
import json
from datetime import datetime, timedelta
import aiohttp
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(level=logging.INFO)

# ==================== НАЛАШТУВАННЯ ====================
BOT_TOKEN = "8877190549:AAEoSIj_dOL2hi-PpDrfZFJi6h8x40hJnFQ"
ADMIN_ID = 8138110821

MONGO_URI = "mongodb+srv://kykarikyzerick_db_user:CVz4czwK06sgQlSP@cluster0.xuoxdku.mongodb.net/?appName=Cluster0"

CHECK_INTERVAL = 1.0
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
seen_items_collection = db["seen_items"]

user_states = {}
temp_brand_storage = {}
last_update_id = 0
vinted_cookies = {}
processed_updates = set()

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

async def is_item_seen(item_id):
    doc = await seen_items_collection.find_one({"item_id": str(item_id)})
    return doc is not None

async def mark_item_seen(item_id):
    await seen_items_collection.update_one(
        {"item_id": str(item_id)},
        {"$set": {"item_id": str(item_id), "added_at": datetime.now()}},
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
        kb.append([{"text": "🆘 Підтримка / Помилка"}])
        return {"keyboard": kb, "resize_keyboard": True, "persistent": True}

    kb.append([{"text": "➕ Додати бренд"}, {"text": "🗑 Очистити бренди"}])
    kb.append([{"text": "📏 Налаштувати розміри"}, {"text": "🌍 Обрати регіон"}])
    kb.append([{"text": "📋 Мої налаштування"}])
    kb.append([{"text": "▶️ Запустити"}, {"text": "⏹ Зупинити"}])
    kb.append([{"text": "🔑 Активація / Стан ключа"}, {"text": "🆘 Підтримка / Помилка"}])
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
    try:
        async with session.post(url, json=payload, timeout=5) as resp:
            return await resp.json()
    except Exception as e:
        logging.error(f"Telegram Send Error: {e}")

async def send_telegram_photo(session, chat_id, photo_url, caption, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    payload = {"chat_id": chat_id, "photo": photo_url, "caption": caption, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        async with session.post(url, json=payload, timeout=5) as resp:
            return await resp.json()
    except Exception as e:
        logging.error(f"Telegram Photo Error: {e}")

# ==================== ОБРОБКА ПОВІДОМЛЕНЬ ====================
async def handle_update(session, update):
    update_id = update.get("update_id")
    if update_id in processed_updates:
        return
    processed_updates.add(update_id)
    if len(processed_updates) > 2000:
        processed_updates.clear()

    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "").strip()

        if text in ["/start", "меню", "Start", "start", "🔙 Головне меню"]:
            user_states[chat_id] = None
            temp_brand_storage.pop(chat_id, None)
            await send_telegram_message(
                session, 
                chat_id, 
                "👋 **Панель керування бота:**", 
                await get_main_keyboard(chat_id)
            )
            return

        if text == "🆘 Підтримка / Помилка":
            support_text = "🆘 **Служба підтримки:**\n\nЯкщо ви знайшли помилку або маєте запитання, звертайтесь сюди: @but_sh0ping"
            await send_telegram_message(session, chat_id, support_text, await get_main_keyboard(chat_id))
            return

        state = user_states.get(chat_id)

        # Адмін-панель
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

        # КРОК 1: Введення назви бренду
        if state == "waiting_step1_brand_name":
            temp_brand_storage[chat_id] = text
            user_states[chat_id] = "waiting_step2_brand_price"
            await send_telegram_message(
                session, 
                chat_id, 
                f"🏷 Бренд: *{text}*\n\nТепер напишіть **максимальну ціну** для цього бренду цифрами (наприклад: `75` або `120`):"
            )
            return

        # КРОК 2: Введення ціни для бренду
        if state == "waiting_step2_brand_price":
            brand_name = temp_brand_storage.get(chat_id, "")
            
            price_digits = re.findall(r"\d+(?:\.\d+)?", text.replace(",", "."))
            if not price_digits:
                await send_telegram_message(session, chat_id, "❌ **Будь ласка, введіть суму тільки цифрами!** (наприклад: `75`)")
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
                f"✅ **Успішно збережено!**\n\n🏷 Бренд: *{brand_name}*\n💵 Макс. ціна: *{max_price}*", 
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
        if text in ["➕ Додати бренд", "➕ Додати бренд з ціною", "➕ Додати бренд (МП)"]:
            user_states[chat_id] = "waiting_step1_brand_name"
            await send_telegram_message(session, chat_id, "Напишіть **назву бренду** (наприклад: `Stone Island` або `Off White`):")

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

        elif text == "📋 Мої налаштування":
            cfg = await get_user_settings(chat_id)
            raw_brands = cfg.get("brands", [])
            
            formatted_brands = []
            for b in raw_brands:
                if isinstance(b, dict):
                    formatted_brands.append(f"{b.get('name')} (до {b.get('max_price')})")
                else:
                    formatted_brands.append(str(b))

            brands_str = "\n• ".join(formatted_brands) if formatted_brands else "Не обрано"
            sizes = ", ".join(cfg.get("sizes", [])) or "Всі"
            domain = cfg.get("domain", "at").upper()
            status = "🟢 Активний" if cfg.get("active") else "🔴 Зупинений"
            
            info = f"⚙️ **Налаштування:**\n\n🏷 **Бренди та ліміти ціни:**\n• {brands_str}\n\n📏 **Розміри:** {sizes}\n🌍 **Регіон:** {domain}\n📡 **Статус:** {status}"
            await send_telegram_message(session, chat_id, info, await get_main_keyboard(chat_id))

        elif text == "▶️ Запустити":
            cfg = await get_user_settings(chat_id)
            if not cfg.get("brands"):
                await send_telegram_message(session, chat_id, "⚠️ Спочатку додайте хоча б один бренд з ціною!", await get_main_keyboard(chat_id))
                return
            cfg["active"] = True
            await save_user_settings(chat_id, cfg)
            await send_telegram_message(session, chat_id, "🚀 Пошук запущено!", await get_main_keyboard(chat_id))

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
        async with session.get(url, headers=headers, timeout=5) as resp:
            cookies = resp.cookies
            cookie_str = "; ".join([f"{k}={v.value}" for k, v in cookies.items()])
            if cookie_str:
                vinted_cookies[domain] = cookie_str
            return cookie_str
    except Exception:
        return ""

async def process_brand_search(session, user_id, brand_obj, domain, user_sizes, currency_symbol, headers):
    if isinstance(brand_obj, dict):
        target_brand = brand_obj.get("name", "")
        max_price = float(brand_obj.get("max_price", float("inf")))
    else:
        target_brand = str(brand_obj)
        max_price = float("inf")

    price_param = f"&price_to={max_price}" if max_price < float("inf") else ""
    api_url = f"https://www.vinted.{domain}/api/v2/catalog/items?search_text={target_brand}&order=newest_first&per_page=30{price_param}"
    
    try:
        async with session.get(api_url, headers=headers, timeout=5) as resp:
            if resp.status in (401, 403, 429):
                vinted_cookies.pop(domain, None)
                return

            if resp.status == 200:
                data = await resp.json()
                items = data.get("items", [])

                brand_words = [w.lower() for w in target_brand.split() if len(w) > 1]

                for item in items:
                    if item.get("promoted") or item.get("is_promoted"):
                        continue

                    item_id = str(item.get("id", ""))
                    if not item_id:
                        continue

                    # Перевіряємо в MongoDB чи цей товар уже відправляли раніше
                    if await is_item_seen(item_id):
                        continue

                    title = str(item.get("title", ""))
                    description = str(item.get("description", ""))
                    item_brand = str(item.get("brand_title", ""))
                    full_text = f"{title} {description} {item_brand}".lower()

                    if not all(word in full_text for word in brand_words):
                        continue

                    if any(fake_word in full_text for fake_word in FAKE_KEYWORDS):
                        continue

                    item_price = 0.0
                    
                    if "price_numeric" in item and item["price_numeric"] is not None:
                        try: item_price = float(item["price_numeric"])
                        except ValueError: pass
                    
                    if item_price == 0.0:
                        raw_price = item.get("price")
                        if isinstance(raw_price, (int, float, str)):
                            try: item_price = float(str(raw_price).replace(",", "."))
                            except ValueError: pass
                        elif isinstance(raw_price, dict):
                            try: item_price = float(str(raw_price.get("amount", 0)).replace(",", "."))
                            except ValueError: pass

                    if item_price > (max_price + 0.01):
                        continue

                    size_title = str(item.get("size_title", "")).upper()
                    if user_sizes:
                        if not any(s.upper() in size_title for s in user_sizes):
                            continue

                    # Записуємо ID знахідки у базу даних, щоб не повторювати
                    await mark_item_seen(item_id)

                    item_brand_display = item_brand if item_brand else target_brand
                    item_url = item.get("url", f"https://www.vinted.{domain}")
                    photo_data = item.get("photo", {})
                    photo_url = photo_data.get("url") if photo_data else None

                    price_display = f"{item_price:.2f}" if item_price > 0 else "За запитом"

                    caption = (
                        f"⚡️ **НОВА ЗНАХІДКА VINTED** ⚡️\n\n"
                        f"🏷 **Назва:** {title}\n"
                        f"💰 **Ціна:** {price_display} {currency_symbol} (Ліміт: {max_price} {currency_symbol})\n"
                        f"📌 **Бренд:** {item_brand_display}\n"
                        f"📏 **Розмір:** {size_title or 'Не вказано'}"
                    )

                    keyboard = get_item_keyboard(item_url)

                    if photo_url:
                        asyncio.create_task(send_telegram_photo(session, user_id, photo_url, caption, keyboard))
                    else:
                        asyncio.create_task(send_telegram_message(session, user_id, caption, keyboard))
    except Exception:
        pass

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

        for brand_obj in target_brands:
            tasks.append(
                process_brand_search(
                    session, user_id, brand_obj, domain, user_sizes, currency_symbol, headers
                )
            )

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

# ==================== ОСНОВНИЙ ЦИКЛ ====================
async def handle_telegram_commands(session):
    global last_update_id
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": last_update_id + 1, "timeout": 3}
    try:
        async with session.get(url, params=params, timeout=5) as resp:
            data = await resp.json()
            if data.get("ok") and data.get("result"):
                for update in data["result"]:
                    last_update_id = update["update_id"]
                    await handle_update(session, update)
    except Exception:
        pass

async def main():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    connector = aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        while True:
            await handle_telegram_commands(session)
            await fetch_vinted(session)
            await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
