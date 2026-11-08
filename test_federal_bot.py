"""Offline regression tests. Telegram HTTP and MTProto calls are mocked."""

import asyncio
import io
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import main
from federal_filter import clean_post, format_post, utf16_length


class EntityType(Enum):
    TEXT_LINK = object()  # Pyrogram's value is a raw type. Use the enum name.
    URL = object()


def link(text, label, url=None):
    return SimpleNamespace(type=EntityType.TEXT_LINK if url else EntityType.URL,
                           offset=utf16_length(text[:text.index(label)]),
                           length=utf16_length(label), url=url)


def response(status=200, **payload):
    return SimpleNamespace(status_code=status, json=lambda: payload)


def success(target_id):
    return response(ok=True, result={"message_id": target_id})


def post(source_id, **overrides):
    values = dict(id=source_id, date=datetime.now(timezone.utc), chat=SimpleNamespace(id=-10042),
                  text=None, entities=None, caption=None, caption_entities=None,
                  photo=None, video=None, media_group_id=None, reply_to_message_id=None)
    return SimpleNamespace(**(values | overrides))


class ConfigTests(unittest.TestCase):
    def test_direct_environment_with_dirty_keys_values_and_token_fallback(self):
        with patch.dict(os.environ, {
            "\ufeff API_\u200bID ": " ' 123 ' ", "API_HASH": "\u200b'hash'\ufeff",
            "SESSION_STRING": " “session” ", "BOT_TOKEN": ' "fallback" ',
            "CHANNEL_TITLE": ' "Дозор.ру | Вся Россия" ',
        }, clear=True):
            config = main.load_config()
        self.assertEqual(config, main.Config(123, "hash", "session", "fallback"))

    def test_missing_variables_do_not_load_dotenv(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(main.ConfigError, "API_ID.*API_HASH.*SESSION_STRING.*BOT_FEDERAL"):
                main.load_config()

    def test_config_errors_name_fields_without_echoing_values(self):
        env = dict(API_ID="1", API_HASH="hash", SESSION_STRING="session", BOT_FEDERAL="preferred", BOT_TOKEN="ignored")
        self.assertEqual(main.load_config(env).token, "preferred")
        for field, value in [("API_ID", "secret"), ("HEARTBEAT_INTERVAL_HOURS", "nan"),
                             ("CHANNEL_URL", "javascript:alert(1)"), ("ADMIN_CHAT_ID", "-10042")]:
            with self.subTest(field=field), self.assertRaisesRegex(main.ConfigError, field):
                main.load_config(env | {field: value})


class EditorialTests(unittest.TestCase):
    def test_commercial_and_financial_posts_are_filtered(self):
        for sample in (
            "Сообщение. e\u200brid: ABC123", "Рекламный пост", "Получите фрибет в казино", "Открыт сбор средств",
            "Поддержка: 1234–5678 9012-3456", "Банковская карта 1234567890123456",
            "ИНН: 123", "ООО Пример", "ИП Пример", "Наш партнёр канала",
            "Работа при поддержке банка", "Поддержка через Т–Банк", "Промокод и скидка",
            "Арбитраж и криптовалюта", "Пожертвование на нужды", "Информация\nСбор\nсредств",
        ):
            with self.subTest(sample=sample):
                self.assertEqual(clean_post(sample), "")

    def test_real_alerts_are_not_confused_with_short_stop_words(self):
        text = "Кипит работа ПВО, беспилотник сбит. Около города обошлось без последствий."
        self.assertEqual(clean_post(text), text)

    def test_hidden_body_links_use_original_offsets(self):
        text = "\ufeff🚨 В регионе тревога. Подробнее\r\nПВО работает.\r\n@vrv_radar"
        hidden = link(text, "Подробнее", "https://other.example/offer")
        self.assertEqual(clean_post(text, [hidden]), "")
        inline_url = "⚡ ПВО работает. https://other.example/offer продолжение сводки."
        self.assertEqual(clean_post(inline_url, [link(inline_url, "https://other.example/offer")]), "")
        one_line = "В регионе тревога. Подробнее"
        self.assertEqual(clean_post(one_line, [link(one_line, "Подробнее", "https://t.me/other_bot")]), "")

    def test_strips_trailing_hidden_and_visible_signatures(self):
        body = "⚡️ В области объявлена опасность по БПЛА."
        text = f"{body}\r\n\r\n👉 Наш канал в MAX\r\nСвязь с редакцией\r\n@vrv_radar\r\n🔗 —"
        hidden = link(text, "Связь с редакцией", "https://max.ru/example")
        self.assertEqual(clean_post(text, [hidden]), body)
        self.assertEqual(clean_post("Курс на МАКС\n@vrv_radar\n👇"), "")
        self.assertEqual(clean_post("<3\nПодписаться"), "")

    def test_known_source_urls_and_mentions_are_removed_from_body(self):
        text = "ПВО работает (по данным @vrv_radar) t.me/vrv_radar\nБПЛА сбит."
        result = clean_post(text)
        self.assertIn("БПЛА сбит", result)
        self.assertNotIn("@vrv_radar", result)
        self.assertNotIn("t.me/", result)
        self.assertEqual(clean_post("ПВО работает. https://t.me.example/vrv_radar\nДрон сбит."), "")

    def test_status_and_negative_clear(self):
        cases = {
            "⚪️ 🔴 Текст": "⚪", "🚨📢 Пролёт БПЛА": "🔴", "⚡ ПВО сбила дрон": "🔴",
            "Опасность по обломкам": "🔴", "Авиационная опасность": "🔴", "Атака FPV": "🔴",
            "Сбивают БПЛА": "🔴", "Ракетная угроза": "🔴",
            "Сбито три беспилотника": "🔴", "Объявлена опасность по БПЛА": "🟡",
            "Внимание по БПЛА": "🟡", "Отбой ракетной опасности": "🟢", "Тревога отменена": "🟢",
            "Отбой не объявлен": "⚪", "Отбой пока не объявлен": "⚪", "Без отбоя": "⚪",
            "Опасность по БПЛА сохраняется, отбой не объявлен": "🟡",
            "ПВО работает. Отбой не объявлен": "🔴", "За ночь сбили 10 БПЛА": "⚪",
            "Сводка: работает ПВО": "🔴", "Сводка: опасность по БПЛА": "🟡",
            "СВОДКА: Сбиты три БПЛА": "⚪", "В городе открылась библиотека": "⚪",
        }
        for source, circle in cases.items():
            with self.subTest(source=source):
                result = format_post(source, "Канал", "https://t.me/example", 900)
                self.assertTrue(result.startswith(circle + " "), result)
                self.assertNotIn("\ufe0f", result)
                self.assertEqual(sum(result.count(c) for c in "⚪🔴🟢🟡"), 1)

    def test_html_and_utf16_lengths_with_word_boundaries(self):
        for limit in (main.MAX_CAPTION, main.MAX_TEXT):
            body = 'Проверка <тег> & "цитата". ' + ("⚙ слово " * 1000)
            rendered = format_post(body, "Канал & регион", "https://example.org/?a=1&b=2", limit)
            self.assertLessEqual(utf16_length(rendered), limit)
            self.assertIn("&lt;тег&gt; &amp; &quot;цитата&quot;", rendered)
            self.assertIn("Канал &amp; регион", rendered)
            self.assertIn("?a=1&amp;b=2", rendered)
            self.assertTrue(rendered.split("\n\n")[0].endswith(("слово", "⚙")))


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        sandbox = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(sandbox.cleanup)
        self.path = Path(sandbox.name) / "federal_state.json"
        self.config = main.Config(1, "hash", "session", "test-token", admin_chat_id=123)
        self.bot = self.new_bot()
        self.mtproto = AsyncMock()
        self.http = self.enterContext(patch.object(main.requests, "post", return_value=success(77)))

    def new_bot(self):
        state = main.State(self.path, -10042, self.config.channel_id)
        state.load()
        return main.FederalBot(self.config, main.BotAPI(self.config.token), state)

    async def test_rejects_hidden_caption_ad_without_download_or_send(self):
        caption = "🚨 Сбили дрон. Подробнее\nИнформация уточняется."
        message = post(1, photo=object(), caption=caption,
                       caption_entities=[link(caption, "Подробнее", "https://other.example/promo")])
        await self.bot.process(self.mtproto, message)
        self.mtproto.download_media.assert_not_called()
        self.http.assert_not_called()

    async def test_real_alert_with_max_tail_is_sent_with_our_footer(self):
        message = post(1, text="🚨 Опасность по БПЛА.\n\nНаш канал в MAX\n@vrv_radar\n👇")
        await self.bot.process(self.mtproto, message)
        self.http.assert_called_once()
        payload = self.http.call_args.kwargs["json"]
        self.assertEqual(payload["chat_id"], "@Dozor_Ru_RF")
        self.assertTrue(payload["disable_web_page_preview"])
        self.assertEqual(payload["text"], "🟡 Опасность по БПЛА.\n\n📡 Дозор.ру | Вся Россия - "
                         "<a href='https://t.me/Dozor_Ru_RF'>Подписаться</a>")

    async def test_reply_after_restart_and_duplicate_suppression(self):
        alert = post(10, text="Опасность по БПЛА.")
        await self.bot.process(self.mtproto, alert)
        self.assertTrue(self.path.is_file())
        self.assertFalse(self.path.with_suffix(".tmp").exists())
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["messages"]["10"][1], 77)
        restarted = self.new_bot()
        self.http.reset_mock()
        await restarted.process(self.mtproto, alert)
        self.http.assert_not_called()
        self.http.return_value = success(78)
        await restarted.process(self.mtproto, post(11, text="Отбой опасности", reply_to_message_id=10))
        self.assertEqual(self.http.call_args.kwargs["json"]["reply_parameters"],
                         {"message_id": 77, "allow_sending_without_reply": True})

    async def test_old_posts_and_expired_replies(self):
        self.bot.state.messages[99] = (time.time() - main.STATE_TTL - 1, 555)
        self.bot.state.save()
        self.assertIsNone(self.new_bot().state.reply_to(99))
        old_date = datetime.fromtimestamp(time.time() - 3601)
        await self.bot.process(self.mtproto, post(1, date=old_date, text="Тревога"))
        self.http.assert_not_called()

    async def test_album_uses_earliest_captioned_member_and_maps_other_frames(self):
        empty = post(1, photo=object(), media_group_id="group")
        photo = post(2, photo=object(), caption="ПВО работает", media_group_id="group")
        other = post(3, video=object(), caption="Другой кадр", media_group_id="group")
        self.mtproto.get_media_group.return_value = [other, empty, photo]
        media = io.BytesIO(b"fake-photo")
        self.mtproto.download_media.return_value = media
        for message in (other, empty, photo):
            await self.bot.process(self.mtproto, message)
        await self.new_bot().process(self.mtproto, photo)
        self.mtproto.get_media_group.assert_awaited_once()
        self.mtproto.download_media.assert_awaited_once_with(photo, in_memory=True)
        self.http.assert_called_once()
        self.assertTrue(self.http.call_args.args[0].endswith("/sendPhoto"))
        self.assertIn("photo", self.http.call_args.kwargs["files"])
        self.assertTrue(media.closed)
        self.assertEqual(self.new_bot().state.reply_to(1)["message_id"], 77)

    async def test_video_upload_rewinds_after_429_and_keeps_reply(self):
        self.bot.state.record([10], None, 40)
        self.mtproto.download_media.return_value = io.BytesIO(b"fake-video")
        reads = []

        def upload(_url, **kwargs):
            reads.append(kwargs["files"]["video"][1].read())
            if len(reads) == 1:
                return response(429, error_code=429, parameters={"retry_after": 6})
            return success(79)

        self.http.side_effect = upload
        with patch.object(main.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await self.bot.process(self.mtproto, post(11, video=object(), caption="Сводка", reply_to_message_id=10))
        sleep.assert_awaited_once_with(6)
        self.assertEqual(reads, [b"fake-video", b"fake-video"])
        self.assertTrue(self.http.call_args.args[0].endswith("/sendVideo"))
        self.assertEqual(json.loads(self.http.call_args.kwargs["data"]["reply_parameters"]),
                         {"message_id": 40, "allow_sending_without_reply": True})

    async def test_read_timeout_is_not_retried_or_replayed_after_restart(self):
        self.http.side_effect = main.requests.ReadTimeout("secret URL should not be logged")
        message = post(1, text="Опасность по БПЛА.")
        with self.assertLogs(main.LOG, level="ERROR") as log:
            await self.bot.process(self.mtproto, message)
        await self.new_bot().process(self.mtproto, message)
        self.http.assert_called_once()
        self.assertNotIn("secret URL", " ".join(log.output))
        self.assertIn("ReadTimeout", self.bot.notifications.get_nowait())

    async def test_state_save_failure_prevents_post_and_allows_later_retry(self):
        message = post(1, text="Отбой опасности")
        with patch.object(main.os, "replace", side_effect=PermissionError), self.assertLogs(main.LOG, level="ERROR"):
            await self.bot.process(self.mtproto, message)
        self.http.assert_not_called()
        await self.bot.process(self.mtproto, message)
        self.http.assert_called_once()

    async def test_admin_worker_only_uses_configured_chat(self):
        self.bot.notify("Критическая ошибка")
        with patch.object(self.bot.api, "send", new_callable=AsyncMock) as send:
            worker = asyncio.create_task(self.bot.admin_worker())
            await asyncio.sleep(0)
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        send.assert_awaited_once_with(123, "Критическая ошибка", html=False)
        self.http.assert_not_called()

    async def test_start_warms_source_cache_before_idle_and_stops(self):
        import pyrogram

        client = Mock()
        client.start = AsyncMock()
        client.stop = AsyncMock()
        client.get_chat = AsyncMock(return_value=SimpleNamespace(id=-10042))
        startup_sent = asyncio.Event()

        async def send_startup(_api, chat_id, text, **kwargs):
            self.assertEqual(chat_id, 123)
            self.assertIn("запущен", text)
            startup_sent.set()

        async def idle():
            client.start.assert_awaited_once()
            client.get_chat.assert_awaited_once_with("vrv_radar")
            await asyncio.wait_for(startup_sent.wait(), 2)

        with patch.object(main, "STATE_FILE", self.path), patch.object(pyrogram, "Client", return_value=client) as factory:
            with patch.object(pyrogram, "idle", side_effect=idle), patch.object(main.BotAPI, "send", send_startup):
                await main.run_bot(self.config)
        self.assertTrue(factory.call_args.kwargs["in_memory"])
        client.stop.assert_awaited_once()
        self.http.assert_not_called()


if __name__ == "__main__":
    unittest.main()
