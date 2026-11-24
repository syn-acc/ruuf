"""Checks for the Discord client, against a pretend Discord. Run from the repository root:

    python -m unittest discover plugins/discord/tests
"""
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import client  # noqa: E402


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
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def discord(*replies):
    session = Pretend(*replies)
    waits = []
    return client.Discord("tok-secret", session=session, sleep=waits.append), session, waits


class Client(unittest.TestCase):
    def test_a_read_goes_out_with_the_token_and_comes_back_decoded(self):
        d, session, _ = discord(Reply(200, [{"id": "1"}]))
        self.assertEqual(d.get("/channels/5/messages", params={"limit": 2, "before": None}), [{"id": "1"}])
        method, url, kwargs = session.asked[0]
        self.assertEqual((method, url), ("GET", "https://discord.com/api/v10/channels/5/messages"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bot tok-secret")
        self.assertEqual(kwargs["params"], {"limit": 2})

    def test_a_limited_read_waits_and_tries_again(self):
        d, session, waits = discord(Reply(429, headers={"Retry-After": "0.5"}), Reply(200, {"ok": 1}))
        self.assertEqual(d.get("/users/@me"), {"ok": 1})
        self.assertEqual(waits, [0.5])
        self.assertEqual(len(session.asked), 2)

    def test_a_read_gives_up_after_two_retries_or_a_long_wait(self):
        d, session, _ = discord(*[Reply(429, headers={"Retry-After": "0"})] * 3)
        with self.assertRaises(client.DiscordError) as caught:
            d.get("/users/@me")
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(len(session.asked), 3)
        d, session, waits = discord(Reply(429, headers={"Retry-After": "30"}))
        with self.assertRaises(client.DiscordError):
            d.get("/users/@me")
        self.assertEqual(waits, [])

    def test_a_write_is_never_sent_twice(self):
        d, session, waits = discord(Reply(429, headers={"Retry-After": "0"}), Reply(200, {}))
        with self.assertRaises(client.DiscordError):
            d.post("/channels/5/messages", json={"content": "hello"})
        self.assertEqual(len(session.asked), 1)
        self.assertEqual(waits, [])

    def test_an_error_says_what_kind_and_nothing_else(self):
        d, _, _ = discord(Reply(403, {"message": "private detail"}))
        with self.assertRaises(client.DiscordError) as caught:
            d.post("/channels/5/messages", json={"content": "the words"})
        text = str(caught.exception)
        self.assertEqual(caught.exception.status, 403)
        for leak in ("tok-secret", "private detail", "the words"):
            self.assertNotIn(leak, text)

    def test_a_network_failure_is_reported_without_its_details(self):
        d, _, _ = discord(OSError("connect to 10.0.0.1 with tok-secret failed"))
        with self.assertRaises(client.DiscordError) as caught:
            d.get("/users/@me")
        self.assertIsNone(caught.exception.status)
        self.assertNotIn("tok-secret", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_no_body_is_none(self):
        d, _, _ = discord(Reply(204))
        self.assertIsNone(d.put("/channels/5/pins/9"))

    def test_the_token_comes_from_the_environment_or_the_env_place(self):
        self.assertEqual(client.read_token({"DISCORD_BOT_TOKEN": "a"}), "a")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("OTHER=1\nDISCORD_BOT_TOKEN='b'\n", encoding="utf-8")
            self.assertEqual(client.read_token({"RUUF_PLACE_ENV": str(path)}), "b")
        with self.assertRaises(client.DiscordError):
            client.read_token({})


if __name__ == "__main__":
    unittest.main()
