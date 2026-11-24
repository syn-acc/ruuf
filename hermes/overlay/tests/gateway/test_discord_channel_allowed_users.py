"""Security tests for per-channel Discord collaborator grants."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.session import SessionSource
from plugins.platforms.discord.adapter import DiscordAdapter


OWNER = "111111111111111111"
COLLABORATOR = "222222222222222222"
MECH_CHANNEL = "333333333333333333"
OTHER_CHANNEL = "444444444444444444"


@pytest.fixture
def adapter():
    config = PlatformConfig(
        enabled=True,
        token="***",
        extra={
            "channel_allowed_users": {
                MECH_CHANNEL: [COLLABORATOR],
            },
        },
    )
    value = DiscordAdapter(config)
    value._allowed_user_ids = {OWNER}
    value._allowed_role_ids = set()
    return value


def test_adapter_allows_collaborator_only_in_configured_channel(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "_is_pairing_approved_user", lambda _uid: False)

    assert adapter._is_allowed_user(
        COLLABORATOR,
        is_dm=False,
        channel_ids={MECH_CHANNEL},
    ) is True
    assert adapter._is_allowed_user(
        COLLABORATOR,
        is_dm=False,
        channel_ids={OTHER_CHANNEL},
    ) is False
    assert adapter._is_allowed_user(
        COLLABORATOR,
        is_dm=True,
        channel_ids=None,
    ) is False
    assert adapter._is_allowed_user(
        COLLABORATOR,
        is_dm=False,
        channel_ids=None,
    ) is False


def test_adapter_inherits_parent_channel_grant_for_threads(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "_is_pairing_approved_user", lambda _uid: False)
    assert adapter._is_allowed_user(
        COLLABORATOR,
        is_dm=False,
        channel_ids={"thread-id", MECH_CHANNEL},
    ) is True


def _runner(adapter):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.DISCORD: adapter}
    runner.pairing_store = MagicMock()
    runner.pairing_store.is_approved.return_value = False
    return runner


def _source(*, chat_id=MECH_CHANNEL, chat_type="group", parent_chat_id=None):
    return SessionSource(
        platform=Platform.DISCORD,
        user_id=COLLABORATOR,
        chat_id=chat_id,
        chat_type=chat_type,
        parent_chat_id=parent_chat_id,
        user_name="collaborator",
    )


def test_gateway_rechecks_scoped_grant(adapter, monkeypatch):
    for key in (
        "DISCORD_ALLOWED_USERS",
        "GATEWAY_ALLOWED_USERS",
        "DISCORD_ALLOW_ALL_USERS",
        "GATEWAY_ALLOW_ALL_USERS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(adapter, "_is_pairing_approved_user", lambda _uid: False)
    runner = _runner(adapter)

    assert runner._is_user_authorized(_source()) is True
    assert runner._is_user_authorized(_source(chat_id=OTHER_CHANNEL)) is False
    assert runner._is_user_authorized(
        _source(chat_id="thread-id", chat_type="thread", parent_chat_id=MECH_CHANNEL),
    ) is True
    assert runner._is_user_authorized(
        _source(chat_id=MECH_CHANNEL, chat_type="dm"),
    ) is False


def test_scoped_helper_failure_is_fail_closed(adapter, monkeypatch):
    monkeypatch.delenv("DISCORD_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("GATEWAY_ALLOWED_USERS", raising=False)
    monkeypatch.setattr(
        adapter,
        "_is_channel_allowed_user",
        MagicMock(side_effect=RuntimeError("broken scoped policy")),
    )
    runner = _runner(adapter)

    assert runner._is_user_authorized(_source()) is False


def test_global_owner_access_is_unchanged(adapter, monkeypatch):
    monkeypatch.setenv("DISCORD_ALLOWED_USERS", OWNER)
    runner = _runner(adapter)
    source = SimpleNamespace(
        platform=Platform.DISCORD,
        user_id=OWNER,
        chat_id=OTHER_CHANNEL,
        chat_type="group",
        parent_chat_id=None,
        is_bot=False,
        delivered_via_upstream_relay=False,
        role_authorized=False,
        profile=None,
    )

    assert runner._is_user_authorized(source) is True
