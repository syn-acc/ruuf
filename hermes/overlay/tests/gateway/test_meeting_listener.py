"""Tests for Discord's summonable passive meeting listener."""

from __future__ import annotations

import json
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform
from gateway.meeting_listener import MeetingListenerStore, build_notes_prompt
from gateway.platforms.base import MessageEvent, MessageType, SessionSource


def _event(text: str) -> MessageEvent:
    source = SessionSource(
        chat_id="123",
        chat_name="Guild / #meeting-notes",
        chat_type="group",
        user_id="42",
        user_name="Syn",
        platform=Platform.DISCORD,
    )
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=source,
        raw_message=SimpleNamespace(guild_id=111, guild=None),
    )


def _runner(tmp_path):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._voice_mode = {}
    runner._VOICE_MODE_PATH = tmp_path / "gateway_voice_mode.json"
    runner._meeting_listeners = MeetingListenerStore(tmp_path / "listeners")
    runner._recent_voice_transcripts = {}
    runner._is_user_authorized = lambda source: True
    runner._thread_metadata_for_source = lambda source: {}
    return runner


def _adapter():
    adapter = SimpleNamespace(
        platform=Platform.DISCORD,
        get_user_voice_channel=AsyncMock(),
        join_voice_channel=AsyncMock(return_value=True),
        leave_voice_channel=AsyncMock(),
        send=AsyncMock(),
        handle_message=AsyncMock(),
        _voice_text_channels={111: 123},
        _voice_sources={},
        _voice_input_callback=None,
        _voice_listener_active_callback=None,
        _on_voice_disconnect=None,
        _voice_mode_getter=None,
        _auto_tts_default=False,
        _auto_tts_enabled_chats=set(),
        _auto_tts_disabled_chats=set(),
        _client=MagicMock(),
    )
    return adapter


def _start_session(store: MeetingListenerStore):
    return store.start(
        guild_id=111,
        voice_channel_id=999,
        voice_channel_name="Studio",
        text_channel_id=123,
        source=_event("/listen summon").source.to_dict(),
        summoned_by_id="42",
        summoned_by_name="Syn",
    )


def test_store_journals_complete_timestamped_transcript(tmp_path):
    store = MeetingListenerStore(tmp_path / "listeners")
    session = _start_session(store)
    assert store.append(
        111,
        speaker_id="7",
        speaker_name="Alice",
        text="We ship on Tuesday.",
    )
    finished = store.finish(111)

    assert finished is session
    assert finished.status == "dismissed"
    assert finished.participants == {"7": "Alice"}
    assert "Alice: We ship on Tuesday." in finished.transcript_text()
    saved = json.loads(next((tmp_path / "listeners").glob("*.json")).read_text())
    assert saved["segments"][0]["speaker_name"] == "Alice"
    assert saved["segments"][0]["text"] == "We ship on Tuesday."


def test_notes_prompt_is_grounded_and_structured(tmp_path):
    store = MeetingListenerStore(tmp_path / "listeners")
    session = _start_session(store)
    store.append(111, speaker_id="7", speaker_name="Alice", text="Syn owns the launch checklist.")
    session = store.finish(111)

    prompt = build_notes_prompt(session)

    assert "Use only the supplied transcript" in prompt
    assert "## Decisions" in prompt
    assert "## Action items" in prompt
    assert "Alice: Syn owns the launch checklist." in prompt


@pytest.mark.asyncio
async def test_summon_joins_silently_and_enables_capture_all(tmp_path):
    runner = _runner(tmp_path)
    adapter = _adapter()
    voice_channel = SimpleNamespace(id=999, name="Studio")
    adapter.get_user_voice_channel.return_value = voice_channel
    runner.adapters[Platform.DISCORD] = adapter
    event = _event("/listen summon")

    result = await runner._handle_meeting_listener_summon(event)

    assert "Sym is listening" in result
    assert "Raw audio is discarded" in result
    adapter.join_voice_channel.assert_awaited_once_with(
        voice_channel,
        text_channel_id=123,
        source=event.source.to_dict(),
        capture_all=True,
    )
    assert runner._meeting_listeners.active_for(111) is not None
    assert runner._voice_mode["discord:123"] == "off"
    assert adapter._voice_listener_active_callback(111) is True


@pytest.mark.asyncio
async def test_listener_captures_without_live_caption_or_agent_turn(tmp_path):
    runner = _runner(tmp_path)
    adapter = _adapter()
    member = SimpleNamespace(display_name="Alice")
    guild = MagicMock()
    guild.get_member.return_value = member
    adapter._client.get_guild.return_value = guild
    runner.adapters[Platform.DISCORD] = adapter
    _start_session(runner._meeting_listeners)

    await runner._handle_voice_channel_input(111, 7, "We decided to launch Tuesday.")

    session = runner._meeting_listeners.active_for(111)
    assert len(session.segments) == 1
    assert session.segments[0].speaker_name == "Alice"
    assert session.segments[0].text == "We decided to launch Tuesday."
    adapter.send.assert_not_awaited()
    adapter.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_dismiss_flushes_then_rewrites_turn_into_notes_prompt(tmp_path):
    runner = _runner(tmp_path)
    adapter = _adapter()
    runner.adapters[Platform.DISCORD] = adapter
    _start_session(runner._meeting_listeners)
    runner._meeting_listeners.append(
        111,
        speaker_id="7",
        speaker_name="Alice",
        text="Syn will send the draft tomorrow.",
    )
    event = _event("/listen dismiss")

    result = await runner._handle_meeting_listener_dismiss(event)

    assert result is None
    adapter.leave_voice_channel.assert_awaited_once_with(111)
    assert runner._meeting_listeners.active_for(111) is None
    assert event._meeting_listener_notes_prompt is True
    assert event.message_type == MessageType.TEXT
    assert "Syn will send the draft tomorrow." in event.text
    adapter.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_busy_dismiss_queues_notes_after_current_turn(tmp_path):
    runner = _runner(tmp_path)
    adapter = _adapter()
    runner.adapters[Platform.DISCORD] = adapter
    runner._enqueue_fifo = MagicMock()
    _start_session(runner._meeting_listeners)
    runner._meeting_listeners.append(
        111,
        speaker_id="7",
        speaker_name="Alice",
        text="The decision is to ship Tuesday.",
    )
    event = _event("/dismiss")

    result = await runner._busy_listener_command(event, "discord:123", event.source)

    assert "queued behind the current response" in result
    runner._enqueue_fifo.assert_called_once()
    queued = runner._enqueue_fifo.call_args.args[1]
    assert queued.message_type == MessageType.TEXT
    assert "The decision is to ship Tuesday." in queued.text


@pytest.mark.asyncio
async def test_passive_join_self_mutes_and_does_not_install_mixer():
    from plugins.platforms.discord import adapter as discord_module

    adapter = object.__new__(discord_module.DiscordAdapter)
    adapter._client = MagicMock()
    adapter._voice_locks = {}
    adapter._voice_clients = {}
    adapter._voice_text_channels = {}
    adapter._voice_sources = {}
    adapter._voice_receivers = {}
    adapter._voice_listen_tasks = {}
    adapter._voice_mixers = {}
    adapter._voice_timeout_tasks = {}
    adapter._voice_listener_active_callback = lambda guild_id: True
    adapter._allowed_user_ids = {"42"}
    adapter._voice_fx_cfg = {"enabled": True}
    adapter._reset_voice_timeout = MagicMock()
    adapter._voice_listen_loop = AsyncMock()
    adapter._install_voice_mixer = AsyncMock()

    channel = MagicMock()
    channel.guild.id = 111
    channel.connect = AsyncMock()
    voice_client = MagicMock()
    channel.connect.return_value = voice_client
    receiver = MagicMock()

    with patch.object(discord_module, "VoiceReceiver", return_value=receiver) as receiver_cls:
        assert await adapter.join_voice_channel(
            channel,
            text_channel_id=123,
            source={"chat_id": "123"},
            capture_all=True,
        )

    channel.connect.assert_awaited_once_with(
        cls=discord_module._HermesReceiveVoiceClient,
        timeout=30.0,
        self_mute=True,
        self_deaf=False,
    )
    receiver_cls.assert_called_once_with(voice_client, allowed_user_ids=set())
    receiver.start.assert_called_once()
    adapter._install_voice_mixer.assert_not_awaited()


@pytest.mark.asyncio
async def test_passive_join_replaces_connection_without_running_receiver():
    from plugins.platforms.discord import adapter as discord_module

    adapter = object.__new__(discord_module.DiscordAdapter)
    adapter._client = MagicMock()
    adapter._voice_locks = {}
    adapter._voice_clients = {}
    adapter._voice_text_channels = {}
    adapter._voice_sources = {}
    adapter._voice_receivers = {}
    adapter._voice_listen_tasks = {}
    adapter._voice_mixers = {}
    adapter._voice_timeout_tasks = {}
    adapter._voice_listener_active_callback = lambda guild_id: True
    adapter._allowed_user_ids = {"42"}
    adapter._voice_fx_cfg = {"enabled": True}
    adapter._reset_voice_timeout = MagicMock()
    adapter._voice_listen_loop = AsyncMock()
    adapter._install_voice_mixer = AsyncMock()

    channel = MagicMock()
    channel.guild.id = 111
    channel.connect = AsyncMock()
    old_voice_client = MagicMock()
    old_voice_client.is_connected.return_value = True
    old_voice_client.is_playing.return_value = False
    old_voice_client.disconnect = AsyncMock()
    adapter._voice_clients[111] = old_voice_client
    new_voice_client = MagicMock()
    channel.connect.return_value = new_voice_client
    receiver = MagicMock()

    with patch.object(discord_module, "VoiceReceiver", return_value=receiver):
        assert await adapter.join_voice_channel(channel, capture_all=True)

    old_voice_client.disconnect.assert_awaited_once()
    channel.connect.assert_awaited_once_with(
        cls=discord_module._HermesReceiveVoiceClient,
        timeout=30.0,
        self_mute=True,
        self_deaf=False,
    )
    assert adapter._voice_clients[111] is new_voice_client
    receiver.start.assert_called_once()


def test_receiver_seeds_speakers_captured_during_voice_handshake():
    from plugins.platforms.discord import adapter as discord_module

    conn = SimpleNamespace(
        secret_key=[0] * 32,
        dave_session=None,
        ssrc=999,
        hook=None,
        ws=SimpleNamespace(_hook=None),
        add_socket_listener=MagicMock(),
        remove_socket_listener=MagicMock(),
    )
    vc = SimpleNamespace(
        _connection=conn,
        _hermes_ssrc_to_user={123: 42},
    )
    receiver = discord_module.VoiceReceiver(vc, allowed_user_ids=set())

    receiver.start()

    assert receiver._ssrc_to_user == {123: 42}
    conn.add_socket_listener.assert_called_once()
    receiver.stop()


def test_receiver_refreshes_transport_key_and_bot_ssrc(monkeypatch):
    from plugins.platforms.discord import adapter as discord_module
    import nacl.secret

    conn = SimpleNamespace(
        secret_key=b"n" * 32,
        dave_session=None,
        can_encrypt=False,
        ssrc=777,
    )
    vc = SimpleNamespace(
        _connection=conn,
        channel=SimpleNamespace(members=[]),
        user=SimpleNamespace(id=777),
    )
    receiver = discord_module.VoiceReceiver(vc, allowed_user_ids=set())
    receiver._running = True
    receiver._secret_key = b"o" * 32
    receiver._bot_ssrc = 999

    seen_keys = []
    transport_box = MagicMock()
    transport_box.decrypt.return_value = b"opus-payload"

    def make_box(key):
        seen_keys.append(key)
        return transport_box

    monkeypatch.setattr(nacl.secret, "Aead", make_box)
    decoder = MagicMock()
    decoder.decode.return_value = b"\x00" * 3840
    monkeypatch.setattr(discord_module.discord.opus, "Decoder", lambda: decoder)

    inbound = (
        struct.pack(">BBHII", 0x80, 0x78, 1, 1, 123)
        + b"ciphertext"
        + b"\x00\x00\x00\x01"
    )
    receiver._on_packet(inbound)

    assert seen_keys == [b"n" * 32]
    assert receiver._secret_key == b"n" * 32
    assert receiver._bot_ssrc == 777
    assert receiver._decoded_packet_count == 1

    own_audio = (
        struct.pack(">BBHII", 0x80, 0x78, 2, 2, 777)
        + b"ciphertext"
        + b"\x00\x00\x00\x02"
    )
    receiver._on_packet(own_audio)
    assert receiver._rtp_packet_count == 1


def test_voice_identity_parser_accepts_speaking_and_video_frames():
    from plugins.platforms.discord import adapter as discord_module

    assert discord_module._discord_speaking_pair(
        {"op": 5, "d": {"ssrc": 123, "user_id": "42"}}
    ) == (123, 42)
    assert discord_module._discord_speaking_pair(
        {"op": 12, "d": {"audio_ssrc": 456, "user_id": "43"}}
    ) == (456, 43)


def test_receiver_infers_single_speaker_before_dave_decrypt(monkeypatch):
    from plugins.platforms.discord import adapter as discord_module
    import nacl.secret

    bot = SimpleNamespace(id=999)
    human = SimpleNamespace(id=42)
    dave_session = MagicMock()
    dave_session.decrypt.return_value = b"opus-payload"
    vc = SimpleNamespace(
        channel=SimpleNamespace(members=[human, bot]),
        user=bot,
        _connection=SimpleNamespace(
            dave_session=dave_session,
            can_encrypt=True,
        ),
    )
    receiver = discord_module.VoiceReceiver(vc, allowed_user_ids=set())
    receiver._running = True
    receiver._secret_key = b"x" * 32
    receiver._bot_ssrc = 999

    transport_box = MagicMock()
    transport_box.decrypt.return_value = b"dave-payload"
    monkeypatch.setattr(nacl.secret, "Aead", lambda _key: transport_box)

    decoder = MagicMock()
    decoder.decode.return_value = b"\x00" * 3840
    monkeypatch.setattr(
        discord_module.discord.opus,
        "Decoder",
        lambda: decoder,
    )

    packet = (
        struct.pack(">BBHII", 0x80, 0x78, 1, 1, 123)
        + b"ciphertext"
        + b"\x00\x00\x00\x01"
    )
    receiver._on_packet(packet)

    assert receiver._ssrc_to_user[123] == 42
    assert receiver._rtp_packet_count == 1
    assert receiver._decoded_packet_count == 1
    assert len(receiver._buffers[123]) == 3840
    dave_session.decrypt.assert_called_once()
    assert dave_session.decrypt.call_args.args[0] == 42


def test_receiver_drops_unmapped_dave_ciphertext_before_opus(monkeypatch):
    from plugins.platforms.discord import adapter as discord_module
    import nacl.secret

    bot = SimpleNamespace(id=999)
    humans = [SimpleNamespace(id=42), SimpleNamespace(id=43)]
    dave_session = MagicMock()
    vc = SimpleNamespace(
        channel=SimpleNamespace(members=[*humans, bot]),
        user=bot,
        _connection=SimpleNamespace(
            dave_session=dave_session,
            can_encrypt=True,
        ),
    )
    receiver = discord_module.VoiceReceiver(vc, allowed_user_ids=set())
    receiver._running = True
    receiver._secret_key = b"x" * 32
    receiver._bot_ssrc = 999

    transport_box = MagicMock()
    transport_box.decrypt.return_value = b"dave-ciphertext"
    monkeypatch.setattr(nacl.secret, "Aead", lambda _key: transport_box)

    decoder = MagicMock()
    monkeypatch.setattr(
        discord_module.discord.opus,
        "Decoder",
        lambda: decoder,
    )

    packet = (
        struct.pack(">BBHII", 0x80, 0x78, 1, 1, 123)
        + b"ciphertext"
        + b"\x00\x00\x00\x01"
    )
    receiver._on_packet(packet)

    assert receiver._unmapped_dave_packet_count == 1
    assert receiver._decoded_packet_count == 0
    assert 123 not in receiver._buffers
    dave_session.decrypt.assert_not_called()
    decoder.decode.assert_not_called()


def test_command_registry_exposes_listener_and_scribe_alias():
    from hermes_cli.commands import resolve_command

    assert resolve_command("listen").name == "listen"
    assert resolve_command("scribe").name == "listen"
    assert resolve_command("listening").name == "listen"
    assert resolve_command("summon").name == "summon"
    assert resolve_command("dismiss").name == "dismiss"
