import os
import asyncio
import re
import requests
from pyrogram import Client, idle

# === ЖЕЛЕЗОБЕТОННОЕ ЧТЕНИЕ ПЕРЕМЕННЫХ НАПРЯМУЮ ИЗ BOTHOST ===
# Никаких load_dotenv() и файлов .env, берем только из панели хостинга!

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
SOURCE_CHANNEL = "vrv_radar"

app = Client("federal_aggregator", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

# ... дальше идет остальной код (FOOTER_SIGNATURE, STOP_WORDS, функции и т.д.) ...

FOOTER_SIGNATURE = (
    "\n\n📡 <b>Дозор.ру | Радар по всей России</b> — "
    "<a href='https://t.me/Dozor_Ru_RF'>Подписаться</a>"
)

# Стоп-слова для фильтрации (реклама + любые сборы)
STOP_WORDS = [
    # Реклама и мусор
    "erid:", "casino", "казино", "ставка", "ставки", "зарабатывай", 
    "инвестиции", "крипта", "биткоин", "вакансия", "требуется", "розыгрыш",
    # Сборы и краудфандинг
    "сбор", "сборы", "собираем", "осталось собрать", "неделя сотки",
    "cloudtips", "реквизиты", "помочь воинам", "помощь фронту",
    "квадрокоптер", "mavic", "сбп", "перевод", "карту", "пожертвован"
]

def is_filtered_out(text: str) -> bool:
    if not text:
        return False
    
    text_lower = text.lower()
    for kw in STOP_WORDS:
        if kw in text_lower:
            print(f"[FILTR] Заблокировано по стоп-слову: '{kw}'")
            return True
            
    # Проверка на сторонние ссылки (блокируем всё, кроме радара и контакта военных)
    urls = re.findall(r'https?://[^\s]+|t\.me/[^\s]+', text)
    for url in urls:
        if "vrv_radar" not in url and "vrv_support" not in url and "max.ru" not in url:
            print(f"[FILTR] Заблокировано из-за чужой ссылки: {url}")
            return True
            
    return False

def send_via_bot_api(chat_id, html_text, photo_id=None, video_id=None):
    base_url = f"https://api.telegram.org/bot{BOT_FEDERAL}"
    
    # Собираем финальный текст (оригинал + наша подпись)
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
        
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code != 200:
            print(f"[ERROR] Ошибка Telegram API: {resp.text}")
    except Exception as e:
        print(f"[ERROR] Сетевая ошибка при отправке: {e}")

@app.on_message()
async def aggregator_handler(client, message):
    # Слушаем только нужный канал
    if not message.chat or message.chat.username != SOURCE_CHANNEL:
        return

    # Берем чистый текст для проверки фильтрами
    raw_text = message.text or message.caption or ""
    if is_filtered_out(raw_text):
        return

    print(f"[OK] Публикуем пост ID {message.id} в федеральный канал...")

    # Берем HTML-текст, чтобы сохранить оригинальные жирные шрифты и ссылки источника
    html_text = ""
    if message.text and message.text.html:
        html_text = message.text.html
    elif message.caption and message.caption.html:
        html_text = message.caption.html

    photo_id = message.photo.file_id if message.photo else None
    video_id = message.video.file_id if message.video else None

    # Отправляем через экзекутор, чтобы не тормозить Pyrogram
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, send_via_bot_api, TARGET_CHANNEL, html_text, photo_id, video_id)

async def main():
    print("=========================================")
    print("ФЕДЕРАЛЬНЫЙ АГРЕГАТОР УСПЕШНО ЗАПУЩЕН")
    print("=========================================")
    await app.start()
    try:
        await idle()
    finally:
        await app.stop()
        print("Бот остановлен.")

if __name__ == "__main__":
    app.run(main())
