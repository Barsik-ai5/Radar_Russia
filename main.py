import os
import asyncio
import re
import requests
import time
from datetime import datetime
from pyrogram import Client, idle

# === ЖЕЛЕЗОБЕТОННОЕ ЧТЕНИЕ ПЕРЕМЕННЫХ НАПРЯМУЮ ИЗ ОС ===
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

API_ID = int(API_ID_RAW)

TARGET_CHANNEL = "@Dozor_Ru_RF"
SOURCE_CHANNELS = ["vrv_radar"] # Сделал списком, чтобы легко добавлять новые каналы

app = Client("federal_aggregator", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

FOOTER_SIGNATURE = (
    "\n\n📡 <b>Дозор.ру | Радар по всей России</b> — "
    "<a href='https://t.me/Dozor_Ru_RF'>Подписаться</a>"
)

# === СТОП-СЛОВА ДЛЯ ФИЛЬТРАЦИИ ===
STOP_WORDS = [
    # Реклама и мусор
    "erid:", "casino", "казино", "ставка", "ставки", "зарабатывай", 
    "инвестиции", "крипта", "биткоин", "вакансия", "требуется", "розыгрыш",
    # Сборы и краудфандинг
    "сбор", "сборы", "собираем", "осталось собрать", "неделя сотки",
    "cloudtips", "реквизиты", "помочь воинам", "помощь фронту",
    "квадрокоптер", "mavic", "сбп", "перевод", "карту", "пожертвован"
]

def log_msg(tag, msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{timestamp} [{tag}] {msg}")

def is_filtered_out(text: str) -> bool:
    if not text:
        return False
    
    text_lower = text.lower()
    for kw in STOP_WORDS:
        if kw in text_lower:
            log_msg("FILTR", f"Заблокировано по стоп-слову: '{kw}'")
            return True
            
    # Проверка на сторонние ссылки (блокируем всё, кроме радара и контакта военных)
    urls = re.findall(r'https?://[^\s]+|t\.me/[^\s]+', text)
    for url in urls:
        if "vrv_radar" not in url and "vrv_support" not in url and "max.ru" not in url:
            log_msg("FILTR", f"Заблокировано из-за чужой ссылки: {url}")
            return True
            
    return False

def sync_send_via_bot_api(chat_id, html_text, photo_id=None, video_id=None):
    """Отправка сообщения с железобетонными ретраями (защита от потери постов)"""
    base_url = f"https://api.telegram.org/bot{BOT_FEDERAL}"
    
    # Собираем финальный текст
    full_text = html_text + FOOTER_SIGNATURE if html_text else FOOTER_SIGNATURE
    
    payload = {
        "chat_id": chat_id,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    if photo_id:
        url = f"{base_url}/sendPhoto"
        payload["photo"] = photo_id
        payload["caption"] = full_text
    elif video_id:
        url = f"{base_url}/sendVideo"
        payload["video"] = video_id
        payload["caption"] = full_text
    else:
        url = f"{base_url}/sendMessage"
        payload["text"] = full_text
        
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                return resp.json().get("result", {}).get("message_id")
                
            elif resp.status_code == 429:
                retry_after = min(resp.json().get("parameters", {}).get("retry_after", 2), 15)
                log_msg("WARN", f"Лимит Telegram. Ждем {retry_after}с...")
                time.sleep(retry_after)
                continue
                
            elif resp.status_code in [400, 401, 403, 404]:
                log_msg("ERROR", f"Фатальная ошибка Telegram API: {resp.text}")
                break 
            else:
                time.sleep(2)
        except requests.exceptions.ReadTimeout:
            log_msg("ERROR", "Таймаут Telegram API. Сообщение могло уйти.")
            return -1
        except Exception as e:
            log_msg("ERROR", f"Сетевая ошибка при отправке: {e}")
            time.sleep(2)
    return None

@app.on_message()
async def aggregator_handler(client, message):
    if not message.chat: return
    username = (message.chat.username or "").lower()
    
    # Слушаем только нужные каналы
    if username not in SOURCE_CHANNELS:
        return

    # Берем чистый текст для проверки фильтрами
    raw_text = message.text or message.caption or ""
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

    # Отправляем через экзекутор, чтобы не тормозить Pyrogram при получении новых постов
    loop = asyncio.get_running_loop()
    msg_id = await loop.run_in_executor(None, sync_send_via_bot_api, TARGET_CHANNEL, html_text, photo_id, video_id)
    
    if msg_id:
        log_msg("OK", f"Опубликован пост ID {message.id} в федеральный канал (TG_ID: {msg_id})")

async def main():
    log_msg("SYSTEM", "=========================================")
    log_msg("SYSTEM", "ФЕДЕРАЛЬНЫЙ АГРЕГАТОР УСПЕШНО ЗАПУЩЕН")
    log_msg("SYSTEM", "=========================================")
    
    await app.start()
    try:
        await idle()
    finally:
        await app.stop()
        log_msg("SYSTEM", "Бот остановлен.")

if __name__ == "__main__":
    app.run(main())
