import os
import re
import requests
from dotenv import load_dotenv
from pyrogram import Client, idle

load_dotenv()

# === АВТОМАТИЧЕСКАЯ ОЧИСТКА КЛЮЧЕЙ ОТ НЕВИДИМЫХ СИМВОЛОВ (\u200b) ===
ENV = {k.replace("\u200b", "").strip(): (v.strip() if v else "") for k, v in os.environ.items()}

API_ID_RAW = ENV.get("API_ID") or ENV.get("TG_API_ID")
API_HASH = ENV.get("API_HASH") or ENV.get("TG_API_HASH")
SESSION_STRING = ENV.get("SESSION_STRING") or ENV.get("STRING_SESSION")
BOT_TOKEN = ENV.get("BOT_FEDERAL") or ENV.get("BOT_TOKEN") or ENV.get("TOKEN")

missing = []
if not API_ID_RAW: missing.append("API_ID")
if not API_HASH: missing.append("API_HASH")
if not SESSION_STRING: missing.append("SESSION_STRING")
if not BOT_TOKEN: missing.append("BOT_FEDERAL")

if missing:
    raise RuntimeError(f"[-] ОШИБКА: Не найдены переменные: {', '.join(missing)}")

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

def send_to_channel(text: str):
    base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"
    max_len = 4096 - len(FOOTER_SIGNATURE)
    final_text = (text[:max_len] if text else "") + FOOTER_SIGNATURE

    try:
        resp = requests.post(
            f"{base_url}/sendMessage",
            json={
                "chat_id": TARGET_CHANNEL,
                "text": final_text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
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

    # Забираем текст либо из обычного сообщения, либо из подписи к медиа
    raw_text = message.text or message.caption or ""
    if not raw_text.strip():
        return

    if is_filtered_out(raw_text):
        return

    # Забираем текст с сохранением HTML-тегов (жирный, курсив, ссылки)
    html_text = ""
    if message.text and message.text.html:
        html_text = message.text.html
    elif message.caption and message.caption.html:
        html_text = message.caption.html
    else:
        html_text = raw_text

    if send_to_channel(html_text):
        print(f"[OK] Пост {message.id} опубликован в {TARGET_CHANNEL}")

async def main():
    print("====================================")
    print("ФЕДЕРАЛЬНЫЙ АГРЕГАТОР ЗАПУЩЕН (ТОЛЬКО ТЕКСТ)")
    print("====================================")
    await app.start()
    try:
        await idle()
    finally:
        await app.stop()

if name == "main":
    app.run(main())
