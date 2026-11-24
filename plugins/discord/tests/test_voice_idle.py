"""Checks for the idle poster as the plugin's voice, against a pretend Discord. Run from the root:

    python -m unittest discover plugins/discord/tests
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
IDLE = HERE.parent / "voice" / "idle"
OLD = HERE.parents[1] / "discord-idle"
sys.path.insert(0, str(IDLE))
import generate as idle_generate  # noqa: E402
import idle_questions  # noqa: E402

try:
    import run as idle_run  # noqa: E402
except ImportError:  # no fcntl: not a POSIX machine
    idle_run = None

CONFIG = {"enabled": True, "guild_id": "1", "channel_id": "5", "bot_id": "9"}
CHANNEL = {"id": "5", "guild_id": "1", "name": "etc-et-chat", "type": 0}
QUIET = [{"id": "100", "type": 0, "author": {"id": "7"}, "content": "hello",
          "timestamp": "2020-01-01T00:00:00+00:00"}]


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


@unittest.skipUnless(idle_run, "the runner needs fcntl")
class Speaking(unittest.TestCase):
    def api(self, *replies):
        session = Pretend(*replies)
        return idle_run.DiscordAPI("tok-secret", session=session), session

    def test_a_dry_tick_reads_and_sends_nothing(self):
        api, session = self.api(Reply(200, CHANNEL), Reply(200, {"id": "9"}), Reply(200, QUIET))
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state.json"
            result = idle_questions.tick(api, CONFIG, state, mock.Mock(side_effect=AssertionError("no writing")),
                                         dry_run=True)
            self.assertFalse(state.exists())
        self.assertEqual(result, {"status": "would_generate", "human_id": "100"})
        self.assertEqual([method for method, _, _ in session.asked], ["GET", "GET", "GET"])
        self.assertEqual(session.asked[0][2]["headers"]["Authorization"], "Bot tok-secret")
        self.assertEqual(session.asked[2][1], "https://discord.com/api/v10/channels/5/messages")
        self.assertEqual(session.asked[2][2]["params"], {"limit": 100})

    def test_send_posts_once_with_the_nonce_and_no_mentions(self):
        api, session = self.api(Reply(200, {"id": "200"}))
        self.assertEqual(api.send("5", "one line", "100"), {"id": "200"})
        method, url, kwargs = session.asked[0]
        self.assertEqual((method, url), ("POST", "https://discord.com/api/v10/channels/5/messages"))
        self.assertEqual(kwargs["json"], {"content": "one line", "allowed_mentions": {"parse": []},
                                          "nonce": "100", "enforce_nonce": True})

    def test_a_limited_send_is_not_tried_again_and_says_so_as_before(self):
        api, session = self.api(Reply(429, headers={"Retry-After": "0"}), Reply(200, {"id": "200"}))
        with self.assertRaises(RuntimeError) as caught:
            api.send("5", "one line", "100")
        self.assertEqual(str(caught.exception), "Discord HTTP 429; no automatic send retry")
        self.assertEqual(len(session.asked), 1)

    def test_a_limited_read_is_not_tried_again_either(self):
        api, session = self.api(Reply(429, headers={"Retry-After": "0"}), Reply(200, QUIET))
        with self.assertRaises(RuntimeError):
            api.history("5")
        self.assertEqual(len(session.asked), 1)

    def test_a_network_failure_keeps_its_wording_and_carries_nothing(self):
        session = mock.Mock()
        session.request.side_effect = OSError("tok-secret at 10.0.0.1")
        api = idle_run.DiscordAPI("tok-secret", session=session)
        with self.assertRaises(RuntimeError) as caught:
            api.me()
        self.assertEqual(str(caught.exception), "Discord network failure; delivery may be unresolved")
        self.assertIsNone(caught.exception.__cause__)


class TheAgentsOwnFiles(unittest.TestCase):
    """The moved code still finds what is the agent's, in the deployment's layout and in this one."""

    def test_the_launchers_place_comes_first(self):
        with mock.patch.dict(os.environ, {"RUUF_PLACE_PERSONA": "/somewhere/persona.md"}):
            self.assertEqual(idle_generate.agents_file("persona.md", "RUUF_PLACE_PERSONA"), Path("/somewhere/persona.md"))

    def test_on_the_deployment_it_is_beside_the_script(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ):
            os.environ.pop("RUUF_PLACE_PERSONA", None)
            (Path(folder) / "persona.md").write_text("who", encoding="utf-8")
            self.assertEqual(idle_generate.agents_file("persona.md", "RUUF_PLACE_PERSONA", here=folder),
                             Path(folder) / "persona.md")

    def test_in_the_repository_it_is_still_in_the_old_folder(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("RUUF_PLACE_PERSONA", None)
            for name in ("persona.md", "questions.json"):
                found = idle_generate.agents_file(name)
                self.assertEqual(found, OLD / name)
                self.assertTrue(found.is_file(), name)
            self.assertIn(found.parent.joinpath("persona.md").read_text(encoding="utf-8"),
                          idle_generate.writer_instructions())

    def test_nothing_of_the_agent_moved_into_the_plugin(self):
        for name in ("persona.md", "questions.json", "config.example.json"):
            self.assertFalse((IDLE / name).exists(), name)


class TheOldPaths(unittest.TestCase):
    def test_the_old_runner_runs_the_moved_one(self):
        text = (OLD / "run.py").read_text(encoding="utf-8")
        self.assertIn("runpy.run_path", text)
        self.assertTrue(((OLD / "run.py").resolve().parents[1] / "discord" / "voice" / "idle" / "run.py").is_file())

    def test_the_old_generator_hands_on_the_moved_one(self):
        spec = importlib.util.spec_from_file_location("old_idle_generate", OLD / "generate.py")
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        for name in ("generate", "preview_examples", "writer_instructions", "INSTRUCTIONS"):
            self.assertTrue(hasattr(old, name), name)
        self.assertEqual(old.INSTRUCTIONS, idle_generate.INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()
