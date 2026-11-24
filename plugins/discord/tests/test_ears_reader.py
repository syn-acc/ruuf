import importlib.util
import pathlib
import unittest
from unittest import mock


SCRIPT = pathlib.Path(__file__).parents[1] / "ears" / "reader.py"
SPEC = importlib.util.spec_from_file_location("ears_reader", SCRIPT)
discord = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(discord)


class DiscordWriteToolTests(unittest.TestCase):
    @mock.patch.object(discord, "discord_request")
    @mock.patch.object(discord, "authorize_message")
    def test_react_encodes_unicode_emoji(self, authorize, request):
        authorize.return_value = ("123", "456", {})

        result = discord.react_message({"channel_id": "123", "message_id": "456", "emoji": "❤️"})

        authorize.assert_called_once_with("123", "456")
        request.assert_called_once_with(
            "PUT", "/channels/123/messages/456/reactions/%E2%9D%A4%EF%B8%8F/@me", expect_json=False
        )
        self.assertTrue(result["reacted"])

    @mock.patch.object(discord, "discord_request")
    @mock.patch.object(discord, "authorize_message")
    def test_reply_disables_mentions_and_references_target(self, authorize, request):
        authorize.return_value = ("123", "456", {})
        request.return_value = {
            "id": "789",
            "channel_id": "123",
            "guild_id": "321",
            "author": {"id": "9", "username": "Sym", "bot": True},
            "content": "hello",
            "attachments": [],
            "embeds": [],
        }

        result = discord.reply_message({"channel_id": "123", "message_id": "456", "content": "hello"})

        request.assert_called_once_with(
            "POST",
            "/channels/123/messages",
            payload={
                "content": "hello",
                "message_reference": {"message_id": "456", "channel_id": "123"},
                "allowed_mentions": {"parse": []},
            },
        )
        self.assertEqual(result["id"], "789")

    @mock.patch.object(discord, "discord_request")
    @mock.patch.object(discord, "authorize_message")
    def test_pin_uses_authorized_message(self, authorize, request):
        authorize.return_value = ("123", "456", {})

        result = discord.pin_message({"channel_id": "123", "message_id": "456"})

        request.assert_called_once_with("PUT", "/channels/123/pins/456", expect_json=False)
        self.assertTrue(result["pinned"])

    @mock.patch.object(discord, "discord_get")
    @mock.patch.object(discord, "authorize_channel", return_value="123")
    def test_authorize_message_rejects_channel_mismatch(self, _authorize_channel, get):
        get.return_value = {"id": "456", "channel_id": "999"}

        with self.assertRaises(PermissionError):
            discord.authorize_message("123", "456")

    def test_write_tools_are_listed(self):
        self.assertTrue({
            "discord_react_message", "discord_reply_message", "discord_pin_message"
        }.issubset(discord.TOOLS))


class Reply:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self.body = body
        self.headers = headers or {}
        self.content = b"" if body is None else b"x"

    def json(self):
        return self.body


class Pretend:
    """Stands in for requests.Session: answers from a list, remembers what it was asked."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.asked = []

    def request(self, method, url, **kwargs):
        self.asked.append((method, url, kwargs))
        return self.replies.pop(0)


REAL_CLIENT = discord.client.Discord


class ThroughTheClient(unittest.TestCase):
    """The reader reaches Discord only through plugins/discord/client.py."""

    def reader_with(self, *replies):
        session, waits, real = Pretend(*replies), [], REAL_CLIENT
        made = []

        def build(token, **kwargs):
            made.append(token)
            return real(token, session=session, sleep=waits.append, **kwargs)
        for patch in (mock.patch.object(discord.client, "Discord", side_effect=build),
                      mock.patch.object(discord, "_discord", None),
                      mock.patch.dict(discord.os.environ, {"DISCORD_BOT_TOKEN": "tok-secret"})):
            patch.start()
            self.addCleanup(patch.stop)
        return session, waits, made

    def test_a_read_goes_through_the_client_with_the_token(self):
        session, _, made = self.reader_with(Reply(200, [{"id": "1"}]), Reply(200, {"id": "2"}))
        self.assertEqual(discord.discord_get("/channels/5/messages", {"limit": 2, "before": None}), [{"id": "1"}])
        self.assertEqual(discord.discord_get("/users/@me"), {"id": "2"})
        self.assertEqual(made, ["tok-secret"])  # one client, built once
        method, url, kwargs = session.asked[0]
        self.assertEqual((method, url), ("GET", "https://discord.com/api/v10/channels/5/messages"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bot tok-secret")
        self.assertEqual(kwargs["headers"]["User-Agent"], "DiscordBot (https://syn.gl, 1.0)")
        self.assertEqual(kwargs["params"], {"limit": 2})

    def test_a_limited_read_is_retried_and_keeps_its_old_words_when_it_gives_up(self):
        session, waits, _ = self.reader_with(Reply(429, headers={"Retry-After": "0.5"}), Reply(200, {"ok": 1}))
        self.assertEqual(discord.discord_get("/users/@me"), {"ok": 1})
        self.assertEqual(waits, [0.5])
        self.reader_with(*[Reply(429, headers={"Retry-After": "0"})] * 3)
        with self.assertRaisesRegex(RuntimeError, "^Discord rate limit exceeded after two retries$"):
            discord.discord_get("/users/@me")
        self.reader_with(Reply(429, headers={"Retry-After": "30"}))
        with self.assertRaisesRegex(RuntimeError, "over the 2 second limit"):
            discord.discord_get("/users/@me")

    def test_a_rate_limited_reply_is_not_sent_twice(self):
        session, waits, _ = self.reader_with(Reply(429, headers={"Retry-After": "0"}), Reply(200, {"id": "9"}))
        with mock.patch.object(discord, "authorize_message", return_value=("123", "456", {})):
            with self.assertRaisesRegex(RuntimeError, r"^Discord API request failed \(HTTP 429\)$"):
                discord.reply_message({"channel_id": "123", "message_id": "456", "content": "hello"})
        self.assertEqual(len(session.asked), 1)
        self.assertEqual(session.asked[0][0], "POST")
        self.assertEqual(waits, [])

    def test_a_failure_says_its_status_and_nothing_of_the_reply(self):
        self.reader_with(Reply(403, {"message": "private detail"}))
        with self.assertRaises(RuntimeError) as caught:
            discord.discord_get("/channels/5")
        self.assertEqual(str(caught.exception), "Discord API request failed (HTTP 403)")

    def test_a_write_with_no_body_returns_none(self):
        self.reader_with(Reply(204))
        self.assertIsNone(discord.discord_request("PUT", "/channels/5/pins/9", expect_json=False))

    def test_the_old_path_still_runs_the_reader(self):
        shim = pathlib.Path(__file__).parents[2] / "discord-reader" / "scripts" / "discord_mcp.py"
        text = shim.read_text(encoding="utf-8")
        self.assertIn("runpy.run_path", text)
        target = shim.resolve().parents[2] / "discord" / "ears" / "reader.py"
        self.assertEqual(target, SCRIPT.resolve())


if __name__ == "__main__":
    unittest.main()
