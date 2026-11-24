import importlib.util
import pathlib
import unittest
from unittest import mock


SCRIPT = pathlib.Path(__file__).parents[1] / "scripts" / "discord_mcp.py"
SPEC = importlib.util.spec_from_file_location("discord_mcp", SCRIPT)
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


if __name__ == "__main__":
    unittest.main()
