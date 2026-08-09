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

CHECK_INTERVAL = 4

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
    kb.append([{"text": "🔑 Активувати новий ключ"}])
    if user_id == ADMIN_ID:
        kb.append([{"text": "👑 Адмін-панель"}])
    return {"keyboard": kb, "resize_keyboard": True, "persistent": True}

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

        if state == "waiting_add_brand":
            brands = user_settings.setdefault(uid_str, {}).get("brands", [])
            if text not in brands:
                brands.append(text)
                user_settings[uid_str]["brands"] = brands
                save_settings(user_settings)
                await send_telegram_message(session, chat_id, f"✅ Бренд *{text}* додано до списку (МП)!", get_main_keyboard(chat_id))
            else:
                await send_telegram_message(session, chat_id, "⚠️ Цей бренд вже є у списку.", get_main_keyboard(chat_id))
            user_states[chat_id] = None
            return

        if state == "waiting_custom_price":
            user_settings.setdefault(uid_str, {})["price"] = text
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, f"✅ Максимальну ціну встановлено: *{text}*", get_main_keyboard(chat_id))
            user_states[chat_id] = None
            return

        if not is_user_active(chat_id):
            if text in ["🔑 Активувати ключ", "🔑 Активувати новий ключ"]:
                user_states[chat_id] = "waiting_for_key"
                await send_telegram_message(session, chat_id, "Надішліть ваш ключ активації у відповідь:")
            else:
                await send_telegram_message(session, chat_id, "🔒 **Доступ обмежено!** Активуйте ключ.", get_main_keyboard(chat_id))
            return

        # Обробка вибору розмірів з панелі
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

        if text == "🧹 Очистити розміри":
            user_settings.setdefault(uid_str, {})["sizes"] = []
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, "🧹 Розміри скинуто.", get_sizes_panel_keyboard(chat_id))
            return

        # Меню команд
        if text == "➕ Додати бренд (МП)":
            user_states[chat_id] = "waiting_add_brand"
            await send_telegram_message(session, chat_id, "Введіть назву бренду для додавання в пошук:")

        elif text == "🗑 Очистити бренди":
            user_settings.setdefault(uid_str, {})["brands"] = []
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, "🗑 Список брендів очищено.", get_main_keyboard(chat_id))

        elif text == "📏 Налаштувати розміри":
            await send_telegram_message(session, chat_id, "Оберіть розміри на нижній панелі:", get_sizes_panel_keyboard(chat_id))

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
            
            info = f"⚙️ **Налаштування (МП):**\n\n🏷 **Бренди:** {brands}\n📏 **Розміри:** {sizes}\n💵 **Макс. ціна:** {price}\n🌍 **Регіон:** {domain}\n📡 **Статус:** {status}"
            await send_telegram_message(session, chat_id, info, get_main_keyboard(chat_id))

        elif text == "▶️ Запустити":
            cfg = user_settings.get(uid_str, {})
            if not cfg.get("brands"):
                await send_telegram_message(session, chat_id, "⚠️ Спочатку додайте хоча б один бренд!", get_main_keyboard(chat_id))
                return
            user_settings.setdefault(uid_str, {})["active"] = True
            save_settings(user_settings)
            await send_telegram_message(session, chat_id, "🚀 **Мультипошук запущено!**", get_main_keyboard(chat_id))

        elif text == "⏹ Зупинити":
            if uid_str in user_settings:
                user_settings[uid_str]["active"] = False
                save_settings(user_settings)
            await send_telegram_message(session, chat_id, "⏹ Пошук зупинено.", get_main_keyboard(chat_id))

# ==================== ОНОВЛЕНИЙ МУЛЬТИПАРСИНГ VINTED ====================
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

async def fetch_vinted(session):
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

        currency_symbol = DOMAINS.get(domain, {}).get("currency", "EUR")
        cookie = await get_vinted_cookie(session, domain)

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"https://www.vinted.{domain}/catalog",
            "Cookie": cookie
        }

        # Цикл по кожному бренду у списку МП
        for target_brand in target_brands:
            api_url = f"https://www.vinted.{domain}/api/v2/catalog/items?search_text={target_brand}&order=newest_first&per_page=15"

            try:
                async with session.get(api_url, headers=headers, timeout=10) as resp:
                    if resp.status in (401, 403, 429):
                        vinted_cookies.pop(domain, None)
                        continue

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

                            # 1. Точна перевірка бренду
                            if target_brand.lower() not in full_text:
                                continue

                            # 2. Перевірка фейків
                            if any(fake_word in full_text for fake_word in FAKE_KEYWORDS):
                                continue

                            # 3. Парсинг ціни
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

                            # Фільтрація за максимальної ціною
                            if "Будь-яка" not in user_price_str:
                                digits = re.findall(r"\d+(?:\.\d+)?", user_price_str.replace(",", "."))
                                if digits:
                                    max_p = float(digits[-1])
                                    if item_price > max_p:
                                        continue

                            # 4. Фільтр розмірів
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
