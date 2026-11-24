#!/usr/bin/env python3
"""Trusted host runner; no requester impersonation or reader MCP access."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import client  # noqa: E402  plugins/discord/client.py: the one way in and out of Discord

from idle_questions import checked_history, tick  # noqa: E402
from generate import generate, preview_examples  # noqa: E402


class DiscordAPI:
    """The four calls the idle poster makes, through the plugin's client. Nothing is tried twice:
    not a send, and not a read either, as before."""

    def __init__(self, token, session=None):
        self.discord = client.Discord(token, session=session, agent="DiscordBot (https://syn.gl, 1.0)")

    def request(self, method, path, **kwargs):
        try:
            return self.discord.request(method, path, retry=False, **kwargs)
        except client.DiscordError as error:
            if error.status is None:
                raise RuntimeError("Discord network failure; delivery may be unresolved") from None
            raise RuntimeError(f"Discord HTTP {error.status}; no automatic send retry") from None

    def channel(self, channel_id):
        return self.request("GET", f"/channels/{channel_id}")

    def me(self):
        return self.request("GET", "/users/@me")

    def history(self, channel_id):
        return self.request("GET", f"/channels/{channel_id}/messages", params={"limit": 100})

    def send(self, channel_id, text, anchor):
        return self.request("POST", f"/channels/{channel_id}/messages", json={
            "content": text, "allowed_mentions": {"parse": []},
            "nonce": str(anchor), "enforce_nonce": True,
        })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--send", action="store_true", help="Enable delivery; default is read-only")
    parser.add_argument("--preview", action="store_true", help="One model preview on real history; never sends or changes delivery state")
    parser.add_argument("--variation-seed", help="Reproducible delivery seed for previews only")
    parser.add_argument("--preview-count", type=int, choices=(1, 2, 3, 4), default=1,
                        help="Compare up to four styles in one no-send model call")
    parser.add_argument("--preview-extremes", action="store_true",
                        help="Compare four fixed min/max stress profiles, preview only")
    parser.add_argument("--preview-world-context", action="store_true",
                        help="Hold style fixed and compare four news-influence levels, preview only")
    args = parser.parse_args()
    if args.preview and args.send:
        raise ValueError("Preview cannot send")
    if args.preview_world_context:
        if not args.preview or args.preview_extremes:
            raise ValueError("World-context comparison requires --preview without --preview-extremes")
        args.preview_count = 4
    if args.preview_extremes:
        if not args.preview:
            raise ValueError("Extreme profiles require --preview")
        args.preview_count = 4
    if (args.variation_seed is not None or args.preview_count != 1) and not args.preview:
        raise ValueError("Variation overrides require --preview")
    if args.variation_seed is not None and len(args.variation_seed) > 128:
        raise ValueError("Preview seed is too long")
    config = json.loads(args.config.read_text())
    for key in ("guild_id", "channel_id", "bot_id"):
        if not isinstance(config.get(key), str) or not config[key].isdigit():
            raise ValueError("Config IDs must be digit strings")
    if config.get("enabled") is not True:
        print(json.dumps({"status": "disabled"}))
        return 0
    home = Path(os.environ.get("HERMES_HOME", "/data/syn.gl/hermes/.hermes"))
    from dotenv import dotenv_values
    token = os.environ.get("DISCORD_BOT_TOKEN") or dotenv_values(
        os.environ.get("RUUF_PLACE_ENV") or home / ".env").get("DISCORD_BOT_TOKEN")
    if not token:
        raise ValueError("Discord bot token is not configured")
    # The launcher names these places (RUUF_PLACE_*); without it, where the deployment keeps them.
    state_dir = Path(os.environ.get("RUUF_PLACE_IDLE_STATE") or home / "discord-idle")
    state_dir.mkdir(mode=0o700, exist_ok=True)
    with (state_dir / "state.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "already_running"}))
            return 0
        api = DiscordAPI(token)
        if args.preview:
            from datetime import datetime, timezone
            state_path = state_dir / "state.json"
            state = json.loads(state_path.read_text()) if state_path.exists() else {}
            writing_config = dict(config, seen_news_ids=state.get("recent_news_ids", []))
            if args.variation_seed is not None:
                writing_config["_preview_variation_seed"] = args.variation_seed
            messages = checked_history(api, config)
            now = datetime.now(timezone.utc)
            if args.preview_count == 1:
                result = {"status": "preview", "generation": generate(messages, writing_config, now)}
            else:
                result = {"status": "preview_batch", "generations": preview_examples(
                    messages, writing_config, now, args.preview_count, extremes=args.preview_extremes,
                    world_sweep=args.preview_world_context)}
        else:
            result = tick(api, config, state_dir / "state.json", generate,
                          dry_run=not args.send)
    # Healthy silence produces no journal churn. Dry runs always explain outcome.
    if not args.send or result["status"] == "posted":
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # Do not include request objects, response bodies, credentials or content.
        print(json.dumps({"status": "error", "error_type": type(exc).__name__}), file=sys.stderr)
        sys.exit(1)
