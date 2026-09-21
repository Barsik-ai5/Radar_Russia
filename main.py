import os
import re
import requests
from dotenv import load_dotenv
from pyrogram import Client, idle

# === ЧИТАЕМ ПЕРЕМЕННЫЕ ИЗ .ENV (ПАНЕЛИ BOTHOST) ===
load_dotenv()

API_ID_RAW = os.getenv("API_ID") or os.getenv("TG_API_ID")
API_HASH = os.getenv("API_HASH") or os.getenv("TG_API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING") or os.getenv("STRING_SESSION")
BOT_TOKEN = os.getenv("BOT_FEDERAL") or os.getenv("BOT_TOKEN") or os.getenv("TOKEN")

missing = []
if not API_ID_RAW: missing.append("API_ID")
if not API_HASH: missing.append("API_HASH")
if not SESSION_STRING: missing.append("SESSION_STRING")
if not BOT_TOKEN: missing.append("BOT_FEDERAL (или BOT_TOKEN)")

if missing:
    print(f"[!] Переменные, которые сейчас видит Питон: {list(os.environ.keys())}")
    raise RuntimeError(f"[-] ОШИБКА: В панели не найдены: {', '.join(missing)}")

API_ID = int(API_ID_RAW)

TARGET_CHANNEL = "@Dozor_Ru_RF"
SOURCE_CHANNEL = "vrv_radar"

FOOTER_SIGNATURE = (
    "\n\n📡 <b>Дозор.ру | Радар по всей России</b> — "
    "<a href='https://t.me/Dozor_Ru_RF'>Подписаться</a>"
)

STOP_WORDS = [
    "erid:", "casino", "казино", "ставка", "ставки", "зарабатывай", 
    "инвестиции", "крипта", "биткоин", "вакансия", "требуется", "розыгрыш",
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
            print(f"[FILTER] Блок по стоп-слову: '{kw}'")
            return True
            
    urls = re.findall(r'https?://[^\s]+|t\.me/[^\s]+', text)
    for url in urls:
        if "vrv_radar" not in url and "vrv_support" not in url and "max.ru" not in url and "Dozor_Ru_RF" not in url:
            print(f"[FILTER] Блок по ссылке: {url}")
            return True
    return False

def send_to_channel(caption: str, photo_file=None):
    base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"
    max_len = 1024 - len(FOOTER_SIGNATURE) if photo_file else 4096 - len(FOOTER_SIGNATURE)
    final_text = (caption[:max_len] if caption else "") + FOOTER_SIGNATURE

    try:
        if photo_file:
            resp = requests.post(
                f"{base_url}/sendPhoto",
                data={"chat_id": TARGET_CHANNEL, "caption": final_text, "parse_mode": "HTML"},
                files={"photo": ("photo.jpg", photo_file)},
                timeout=25
            )
        else:
            resp = requests.post(
                f"{base_url}/sendMessage",
                json={"chat_id": TARGET_CHANNEL, "text": final_text, "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=15
            )
        
        if resp.status_code == 200:
            return True
        print(f"[ERROR] Ошибка Telegram ({resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"[ERROR] Сеть: {e}")
    return False

app = Client("federal_core", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

@app.on_message()
async def handler(client, message):
    if not message.chat:
        return
        
    username = (message.chat.username or "").lower()
    if username != SOURCE_CHANNEL.lower():
        return

    raw_text = message.text or message.caption or ""
    if is_filtered_out(raw_text):
        return

    html_text = ""
    if message.text and message.text.html:
        html_text = message.text.html
    elif message.caption and message.caption.html:
        html_text = message.caption.html

    photo_bytes = None
    if message.photo:
        try:
            f_io = await message.download(in_memory=True)
            photo_bytes = f_io.getvalue()
        except Exception as e:
            print(f"[WARN] Ошибка скачивания фото: {e}")

    if send_to_channel(html_text, photo_bytes):
        print(f"[OK] Пост {message.id} опубликован в {TARGET_CHANNEL}")

async def main():
    print("====================================")
    print("ФЕДЕРАЛЬНЫЙ АГРЕГАТОР ЗАПУЩЕН")
    print("====================================")
    await app.start()
    try:
        await idle()
    finally:
        await app.stop()

if name == "main":
    app.run(main())
