"""Forward posts from a Telegram source to the federal Dozor.ru channel."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

import requests

from federal_filter import INVISIBLE, clean_post, format_post

LOG = logging.getLogger("federal-dozor")
STATE_FILE = Path(__file__).with_name("federal_state.json")
STATE_TTL = 48 * 60 * 60
MAX_POST_AGE = 60 * 60
MAX_TEXT = 3800
MAX_CAPTION = 900
MEDIA_LIMITS = {"photo": 10_000_000, "video": 50_000_000}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    session_string: str
    token: str
    channel_id: str = "@Dozor_Ru_RF"
    channel_title: str = "Дозор.ру | Вся Россия"
    channel_url: str = "https://t.me/Dozor_Ru_RF"
    source_channel: str = "vrv_radar"
    admin_chat_id: int | None = None
    heartbeat_hours: float = 12.0


def clean_env(value: str) -> str:
    value = value.translate(INVISIBLE).strip()
    quotes = {'"': '"', "'": "'", "«": "»", "“": "”", "‘": "’"}
    while len(value) >= 2 and quotes.get(value[0]) == value[-1]:
        value = value[1:-1].strip()
    return value


def load_config(environ: Mapping[str, str] | None = None) -> Config:
    env = {clean_env(k): clean_env(v) for k, v in (os.environ if environ is None else environ).items()}
    token = env.get("BOT_FEDERAL") or env.get("BOT_TOKEN")
    missing = [name for name in ("API_ID", "API_HASH", "SESSION_STRING") if not env.get(name)]
    if not token:
        missing.append("BOT_FEDERAL (или BOT_TOKEN)")
    if missing:
        raise ConfigError("Не заданы переменные окружения: " + ", ".join(missing))

    def positive_int(name: str) -> int:
        try:
            value = int(env[name])
            if value > 0:
                return value
        except ValueError:
            pass
        raise ConfigError(f"{name} должен быть положительным целым числом")

    try:
        interval = float(env.get("HEARTBEAT_INTERVAL_HOURS") or Config.heartbeat_hours)
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError
    except ValueError:
        raise ConfigError("HEARTBEAT_INTERVAL_HOURS должен быть положительным числом") from None

    url = env.get("CHANNEL_URL") or Config.channel_url
    try:
        parsed = urlsplit(url)
        valid_url = (
            parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
            and not parsed.password and not any(char.isspace() for char in url)
        )
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ConfigError("CHANNEL_URL должен быть полной ссылкой http:// или https://")

    return Config(
        api_id=positive_int("API_ID"), api_hash=env["API_HASH"], session_string=env["SESSION_STRING"],
        token=token, channel_id=env.get("CHANNEL_ID") or Config.channel_id,
        channel_title=env.get("CHANNEL_TITLE") or Config.channel_title, channel_url=url,
        source_channel=env.get("SOURCE_CHANNEL") or Config.source_channel,
        admin_chat_id=positive_int("ADMIN_CHAT_ID") if env.get("ADMIN_CHAT_ID") else None,
        heartbeat_hours=interval,
    )


class State:
    """Keep delivery IDs and reservations for a single source and destination."""

    def __init__(self, path: Path, source_id: int, target: str):
        self.path = path
        self.route = [source_id, target.casefold()]
        self.messages: dict[int, tuple[float, int | None]] = {}
        self.albums: dict[str, float] = {}
        self.dirty = False

    def load(self) -> None:
        try:
            with self.path.open(encoding="utf-8") as stream:
                state = json.load(stream)
        except FileNotFoundError:
            return
        if state["version"] != 1:
            raise ValueError("Неизвестная версия federal_state.json")
        if state["route"] == self.route:
            self.messages = {
                int(key): (float(value[0]), None if value[1] is None else int(value[1]))
                for key, value in state["messages"].items()
            }
            self.albums = {key: float(value) for key, value in state["albums"].items()}
        self.prune()

    def prune(self) -> None:
        cutoff = time.time() - STATE_TTL
        expired_messages = [key for key, (timestamp, _) in self.messages.items() if timestamp < cutoff]
        expired_albums = [key for key, timestamp in self.albums.items() if timestamp < cutoff]
        for key in expired_messages:
            del self.messages[key]
        for key in expired_albums:
            del self.albums[key]
        if expired_messages or expired_albums:
            self.dirty = True

    def save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump({"version": 1, "route": self.route, "messages": self.messages, "albums": self.albums}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        self.dirty = False

    def record(self, source_ids: list[int], group: str | None, target_id: int | None = None) -> None:
        now = time.time()
        for source_id in source_ids:
            self.messages[source_id] = (now, target_id)
        if group:
            self.albums[group] = now
        self.dirty = True

    def reserve(self, source_ids: list[int], group: str | None) -> None:
        old_messages = {source_id: self.messages.get(source_id) for source_id in source_ids}
        self.record(source_ids, group)
        try:
            self.save()
        except OSError:
            for source_id, previous in old_messages.items():
                if previous is None:
                    self.messages.pop(source_id, None)
                else:
                    self.messages[source_id] = previous
            if group:
                self.albums.pop(group, None)
            raise

    def reply_to(self, source_id: int | None) -> dict | None:
        if source_id in self.messages:
            target_id = self.messages[source_id][1]
            if target_id is not None:
                return {"message_id": target_id, "allow_sending_without_reply": True}
        return None


class DeliveryError(RuntimeError):
    pass


class BotAPI:
    def __init__(self, token: str):
        self.url = f"https://api.telegram.org/bot{token}"

    async def send(self, chat_id: str | int, text: str, *, reply: dict | None = None,
                   media: str | None = None, attachment=None, html: bool = True) -> int:
        payload = {"chat_id": chat_id}
        if html:
            payload["parse_mode"] = "HTML"
        if reply:
            payload["reply_parameters"] = reply
        method = "sendMessage"
        if media:
            payload["caption"] = text
            if reply:
                payload["reply_parameters"] = json.dumps(reply)
            method = "sendPhoto" if media == "photo" else "sendVideo"
        else:
            payload.update(text=text, disable_web_page_preview=True)

        for attempt in range(3):
            if media:
                attachment.seek(0)
                name = "photo.jpg" if media == "photo" else "video.mp4"
                kwargs = {"data": payload, "files": {media: (name, attachment)}}
            else:
                kwargs = {"json": payload}
            try:
                response = await asyncio.to_thread(
                    requests.post, f"{self.url}/{method}", timeout=(5, 45), allow_redirects=False, **kwargs
                )
            except requests.ConnectTimeout:
                if attempt == 2:
                    raise DeliveryError("не удалось подключиться к Telegram") from None
                await asyncio.sleep(2 ** attempt)
                continue
            except requests.ReadTimeout:
                raise DeliveryError("Telegram не подтвердил отправку (ReadTimeout)") from None
            except requests.RequestException as exc:
                raise DeliveryError(f"сетевая ошибка Telegram ({type(exc).__name__})") from None

            try:
                data = response.json()
            except ValueError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            if response.status_code == 429 or data.get("error_code") == 429:
                if attempt == 2:
                    raise DeliveryError("Telegram вернул 429 после повторных попыток")
                parameters = data.get("parameters") or {}
                retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
                if not isinstance(retry_after, int) or retry_after < 1:
                    retry_after = 2 ** (attempt + 1)
                await asyncio.sleep(retry_after)
                continue
            if response.status_code == 200 and data.get("ok") is True:
                result = data.get("result")
                message_id = result.get("message_id") if isinstance(result, dict) else None
                if type(message_id) is int and message_id > 0:
                    return message_id
            raise DeliveryError(f"Telegram не подтвердил отправку (HTTP {response.status_code})")
        raise AssertionError("Недостижимый исход отправки")


class FederalBot:
    def __init__(self, config: Config, api: BotAPI, state: State):
        self.config = config
        self.api = api
        self.state = state
        self.notifications: asyncio.Queue[str] = asyncio.Queue(maxsize=20)
        self.published = self.filtered = self.errors = 0

    def notify(self, text: str) -> None:
        if self.config.admin_chat_id and not self.notifications.full():
            self.notifications.put_nowait(text)

    def report_error(self, source_id: int, error: Exception) -> None:
        self.errors += 1
        detail = str(error) if isinstance(error, DeliveryError) else type(error).__name__
        text = f"Дозор.ру РФ · ошибка обработки поста {source_id}: {detail}"
        LOG.error("%s", text)
        self.notify(text)

    async def admin_worker(self) -> None:
        while True:
            text = await self.notifications.get()
            await send_admin(self.config, self.api, text)

    async def heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.config.heartbeat_hours * 3600)
            try:
                self.state.prune()
                if self.state.dirty:
                    self.state.save()
            except OSError as error:
                self.report_error(0, error)
            self.notify(f"Дозор.ру РФ работает. С запуска: опубликовано {self.published}, "
                        f"отфильтровано {self.filtered}, ошибок {self.errors}.")

    async def process(self, client, message) -> None:
        # The client has one handler worker so replies see the result of earlier posts.
        source_id = message.id
        try:
            await self.publish(client, message)
        except Exception as error:
            self.report_error(source_id, error)

    async def publish(self, client, message) -> None:
        now = time.time()
        if not message.date:
            return
        if now - message.date.timestamp() > MAX_POST_AGE:
            return

        self.state.prune()
        group = str(message.media_group_id) if message.media_group_id else None
        if message.id in self.state.messages or group in self.state.albums:
            return
        source_ids = [message.id]
        if group:
            members = await client.get_media_group(message.chat.id, message.id)
            eligible = [item for item in members if item.caption and (item.photo or item.video)]
            if not eligible:
                self.state.record(source_ids, group)
                return
            source_ids = list({message.id, *(item.id for item in members)})
            message = min(eligible, key=lambda item: item.id)

        media = "photo" if message.photo else "video" if message.video else None
        raw = message.caption if media else message.text
        entities = message.caption_entities if media else message.entities
        body = clean_post(raw, entities, self.config.source_channel)
        rendered = format_post(body, self.config.channel_title, self.config.channel_url,
                               MAX_CAPTION if media else MAX_TEXT) if body else ""
        if not rendered:
            self.filtered += 1
            self.state.record(source_ids, group)
            return

        reply = self.state.reply_to(message.reply_to_message_id)
        attachment = None
        try:
            if media:
                size = getattr(getattr(message, media), "file_size", None)
                if size and size > MEDIA_LIMITS[media]:
                    raise DeliveryError("размер медиа превышает лимит Telegram Bot API")
                attachment = await client.download_media(message, in_memory=True)
                if attachment is None:
                    raise DeliveryError("Pyrogram не скачал медиа")
            # Persist before sending. If the outcome is unknown, a replay must not post twice.
            self.state.reserve(source_ids, group)
            target_id = await self.api.send(self.config.channel_id, rendered, reply=reply,
                                            media=media, attachment=attachment)
            self.state.record(source_ids, group, target_id)
            self.state.save()
            self.published += 1
            LOG.info("Опубликован пост %s → %s", message.id, target_id)
        finally:
            if attachment is not None:
                attachment.close()


async def send_admin(config: Config, api: BotAPI, text: str) -> None:
    if config.admin_chat_id is None:
        return
    try:
        await api.send(config.admin_chat_id, text, html=False)
    except DeliveryError as error:
        LOG.warning("Уведомление администратору: %s", error)


async def run_bot(config: Config) -> None:
    from pyrogram import Client, filters, idle
    from pyrogram.handlers import MessageHandler

    app = Client("federal_core", api_id=config.api_id, api_hash=config.api_hash,
                 session_string=config.session_string, in_memory=True, workers=1)
    api = BotAPI(config.token)
    ready = asyncio.Event()
    bot = None
    source_id = None

    async def handle(client, message):
        await ready.wait()
        if bot is not None and message.chat and message.chat.id == source_id:
            await bot.process(client, message)

    app.add_handler(MessageHandler(handle, filters.channel))
    started = False
    tasks = []
    try:
        await app.start()
        started = True
        source = config.source_channel
        peer = int(source) if source.lstrip("-").isdigit() else source
        if isinstance(peer, int):
            async for dialog in app.get_dialogs():
                if dialog.chat.id == peer:
                    break
        chat = await app.get_chat(peer)
        source_id = chat.id
        state = State(STATE_FILE, source_id, config.channel_id)
        state.load()
        bot = FederalBot(config, api, state)
        ready.set()
        tasks.append(asyncio.create_task(bot.heartbeat()))
        if config.admin_chat_id:
            tasks.append(asyncio.create_task(bot.admin_worker()))
        text = f"Дозор.ру РФ запущен. Источник {source}, канал {config.channel_id}."
        LOG.info("%s", text)
        bot.notify(text)
        await idle()
    except Exception as error:
        await send_admin(config, api, f"Дозор.ру РФ · критическая ошибка: {type(error).__name__}")
        raise
    finally:
        ready.set()
        try:
            if started:
                await app.stop()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        config = load_config()
    except ConfigError as error:
        LOG.error("%s", error)
        return 1
    try:
        asyncio.run(run_bot(config))
    except Exception as error:
        LOG.error("Федеральный радар остановлен (%s). Проверьте сессию, источник и federal_state.json.",
                  type(error).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
