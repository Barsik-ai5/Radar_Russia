import os
import asyncio
import re
import requests
import time
import threading
from datetime import datetime, timezone
from html import escape
from pyrogram import Client, idle

# 🔥 ФИКС: Включаем чтение скрытого .env файла, который генерирует BotHost из своей панели!
from dotenv import load_dotenv
load_dotenv()

# === ЖЕЛЕЗОБЕТОННОЕ ЧТЕНИЕ ПЕРЕМЕННЫХ ИЗ BOTHOST ===
API_ID_RAW = os.environ.get("API_ID", "").strip()
API_HASH = os.environ.get("API_HASH", "").strip()
SESSION_STRING = os.environ.get("SESSION_STRING", "").strip()
BOT_FEDERAL = os.environ.get("BOT_FEDERAL", "").strip()

missing = []
if not API_ID_RAW: missing.append("API_ID")
if not API_HASH: missing.append("API_HASH")
if not SESSION_STRING: missing.append("SESSION_STRING")
if not BOT_FEDERAL: missing.append("BOT_FEDERAL")

if missing:
    raise RuntimeError(f"[-] ОШИБКА: BotHost не передал Питону переменные: {', '.join(missing)}")

try:
    API_ID = int(API_ID_RAW)
except ValueError:
    raise RuntimeError("[-] ОШИБКА: API_ID должен быть числом!")

# === НАСТРОЙКИ КАНАЛОВ И ФИЛЬТРОВ ===
TARGET_CHANNEL = "@Dozor_Ru_RF"
SOURCE_CHANNELS = ["vrv_radar"] # Канал источник без @

FOOTER_SIGNATURE = (
    "\n\n📡 <b>Дозор.ру | Радар по всей России</b> — "
    "<a href='https://t.me/Dozor_Ru_RF'>Подписаться</a>"
)

STOP_WORDS = [
    # Реклама и мусор
    "erid:", "casino", "казино", "ставка", "ставки", "зарабатывай", 
    "инвестиции", "крипта", "биткоин", "вакансия", "требуется", "розыгрыш",
    # Сборы и краудфандинг
    "сбор", "сборы", "собираем", "осталось собрать", "неделя сотки",
    "cloudtips", "реквизиты", "помочь воинам", "помощь фронту",
    "квадрокоптер", "mavic", "сбп", "перевод", "карту", "пожертвован"
]

# === НАСЛЕДИЕ V16: МОНИТОРИНГ И ЛОГИРОВАНИЕ ===
STATS = {"processed": 0, "sent": 0, "filtered": 0, "errors": 0}
stats_lock = threading.Lock()

# Админ-айди прочитается, только если ты добавишь его в панель. Если нет — бот просто не будет слать логи в ЛС.
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
ADMIN_BOT_TOKEN = os.environ.get("ADMIN_BOT_TOKEN", BOT_FEDERAL)

MAIN_LOOP = None      
ADMIN_QUEUE = None    

def log_msg(level, tag, msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} [{level}] [{tag}] {msg}"
    print(line)

    if level == "ERROR" or level == "FILTR":
        with stats_lock:
            if level == "ERROR": STATS["errors"] += 1
            if level == "FILTR": STATS["filtered"] += 1
    
    if level == "ERROR":
        _notify_admin_async(line)

def _notify_admin_async(line):
    if not ADMIN_CHAT_ID or MAIN_LOOP is None: return
    try: MAIN_LOOP.call_soon_threadsafe(_enqueue_admin_line, line)
    except RuntimeError: pass  

def _enqueue_admin_line(line):
    if ADMIN_QUEUE is None: return
    try: ADMIN_QUEUE.put_nowait(line)
    except asyncio.QueueFull: print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [WARN] [ADMIN] Очередь переполнена.")

def sync_send_admin_message(text):
    if not ADMIN_CHAT_ID or not ADMIN_BOT_TOKEN: return None
    url = f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": ADMIN_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    for attempt in range(2):
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200: return True
            elif response.status_code == 429:
                time.sleep(min(response.json().get("parameters", {}).get("retry_after", 2), 15))
                continue
            return None
        except Exception: return None
    return None

async def admin_notifier_worker():
    if not ADMIN_CHAT_ID: return
    while True:
        try:
            first_line = await ADMIN_QUEUE.get()
            batch = [first_line]
            deadline = time.monotonic() + 5.0
            while len(batch) < 20:
                remaining = deadline - time.monotonic()
                if remaining <= 0: break
                try: batch.append(await asyncio.wait_for(ADMIN_QUEUE.get(), timeout=remaining))
                except asyncio.TimeoutError: break

            raw_text = "\n\n".join(batch)
            safe_html = escape(raw_text[:600]) 
            text = f"🚨 <b>ФедРадар: {len(batch)} ошиб(ок)</b>\n\n<pre>{safe_html}</pre>"
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, sync_send_admin_message, text)
        except asyncio.CancelledError: break
        except Exception as e: print(f"[ERROR] [ADMIN] Сбой воркера: {e}")

async def heartbeat_worker():
    if not ADMIN_CHAT_ID: return
    while True:
        await asyncio.sleep(6 * 3600) # Сердечный ритм раз в 6 часов
        try:
            with stats_lock:
                proc, sent, filt, errs = STATS['processed'], STATS['sent'], STATS['filtered'], STATS['errors']
                STATS['processed'] = STATS['sent'] = STATS['filtered'] = STATS['errors'] = 0
            
            text = (
                f"✅ <b>ФедРадар жив.</b>\n"
                f"Просмотрено постов: {proc}\n"
                f"Отправлено в РФ: {sent}\n"
                f"Заблокировано фильтром: {filt}\n"
                f"Ошибок: {errs}"
            )
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, sync_send_admin_message, text)
        except asyncio.CancelledError: break
        except Exception: pass

# === ЛОГИКА ФИЛЬТРАЦИИ И ОТПРАВКИ ===

def is_filtered_out(text: str) -> bool:
    if not text:
        return False
    
    text_lower = text.lower()
    for kw in STOP_WORDS:
        if kw in text_lower:
            log_msg("FILTR", "MODERATION", f"Блок по стоп-слову: '{kw}'")
            return True
            
    # Проверка на сторонние ссылки (блокируем всё, кроме разрешенных каналов)
    urls = re.findall(r'https?://[^\s]+|t\.me/[^\s]+', text)
    for url in urls:
        if "vrv_radar" not in url and "vrv_support" not in url and "max.ru" not in url:
            log_msg("FILTR", "MODERATION", f"Блок из-за чужой ссылки: {url}")
            return True
            
    return False

def sync_send_via_bot_api(chat_id, html_text, photo_id=None, video_id=None):
    """Отправка сообщения с железобетонными ретраями из V16"""
    base_url = f"https://api.telegram.org/bot{BOT_FEDERAL}"
    
    full_text = html_text + FOOTER_SIGNATURE if html_text else FOOTER_SIGNATURE
    
    payload = {
        "chat_id": chat_id,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    if photo_id:
        url = f"{base_url}/sendPhoto"
        payload["photo"] = photo_id
        payload["caption"] = full_text[:1024] # Защита от лимитов Telegram (подпись к фото)
    elif video_id:
        url = f"{base_url}/sendVideo"
        payload["video"] = video_id
        payload["caption"] = full_text[:1024]
    else:
        url = f"{base_url}/sendMessage"
        payload["text"] = full_text[:4096] # Защита от лимитов Telegram (чистый текст)
        
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                return resp.json().get("result", {}).get("message_id")
            elif resp.status_code == 429:
                retry_after = min(resp.json().get("parameters", {}).get("retry_after", 2), 15)
                log_msg("WARN", "TG", f"Лимит Telegram. Ждем {retry_after}с...")
                time.sleep(retry_after)
                continue
            elif resp.status_code in [400, 401, 403, 404]:
                log_msg("ERROR", "TG", f"Фатальная ошибка {resp.status_code}: {resp.text[:200]}")
                break 
            else:
                time.sleep(2)
        except requests.exceptions.ReadTimeout:
            log_msg("ERROR", "TG", "Timeout. Сообщение могло уйти.")
            return -1 
        except Exception as e:
            log_msg("ERROR", "TG", f"Сеть: {e}")
            time.sleep(2)
    return None

# Инициализация Pyrogram
app = Client("federal_aggregator", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

@app.on_message()
async def aggregator_handler(client, message):
    if not message.chat: return
    username = (message.chat.username or "").lower()
    
    if username not in SOURCE_CHANNELS:
        return

    raw_text = message.text or message.caption or ""
    
    with stats_lock:
        STATS["processed"] += 1
        
    if is_filtered_out(raw_text):
        return

    # Берем HTML-текст, чтобы сохранить оригинальные жирные шрифты и ссылки источника
    html_text = ""
    if message.text and message.text.html:
        html_text = message.text.html
    elif message.caption and message.caption.html:
        html_text = message.caption.html

    photo_id = message.photo.file_id if message.photo else None
    video_id = message.video.file_id if message.video else None

    # Отправляем через экзекутор, чтобы не тормозить асинхронный цикл Pyrogram
    loop = asyncio.get_running_loop()
    msg_id = await loop.run_in_executor(None, sync_send_via_bot_api, TARGET_CHANNEL, html_text, photo_id, video_id)
    
    if msg_id:
        log_msg("INFO", "SEND", f"Пост ID {message.id} опубликован в ФедРадар (TG_ID: {msg_id})")
        with stats_lock:
            STATS["sent"] += 1

async def main():
    global MAIN_LOOP, ADMIN_QUEUE
    
    MAIN_LOOP = asyncio.get_running_loop()
    ADMIN_QUEUE = asyncio.Queue(maxsize=200)

    log_msg("INFO", "SYSTEM", "=======================================")
    log_msg("INFO", "SYSTEM", "ФЕДЕРАЛЬНЫЙ АГРЕГАТОР ЗАПУЩЕН (V16 Core Edition)")
    log_msg("INFO", "SYSTEM", "=======================================")

    await app.start()
    
    notifier_task = None
    heartbeat_task = None
    
    try:
        notifier_task = asyncio.create_task(admin_notifier_worker())
        heartbeat_task = asyncio.create_task(heartbeat_worker())
        
        # Синк диалогов
        async for _ in app.get_dialogs(limit=20): pass 
        
        if ADMIN_CHAT_ID:
            await MAIN_LOOP.run_in_executor(None, sync_send_admin_message, "✅ <b>Федеральный Агрегатор успешно запущен!</b>")

        await idle()
    finally:
        # V16 Graceful Shutdown
        tasks = [t for t in (notifier_task, heartbeat_task) if t and not t.done()]
        for t in tasks: 
            t.cancel()
            
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=10.0)
            for t in pending:
                t.cancel()
                log_msg("WARN", "SYSTEM", f"Задача {t.get_name() or t} отменена принудительно")
                
        await app.stop()
        log_msg("INFO", "SYSTEM", "Бот безопасно остановлен.")

if __name__ == "__main__":
    if not ADMIN_CHAT_ID:
        print("ВНИМАНИЕ: ADMIN_CHAT_ID не задан. Уведомления отключены.")
    app.run(main())
