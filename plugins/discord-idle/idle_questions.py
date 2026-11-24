"""Deterministic idle gating around one bounded contextual generation."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


IDLE_SECONDS = 3 * 60 * 60


def human(message):
    return (message.get("type", 0) in (0, 19)
            and not message["author"].get("bot", False)
            and not message.get("webhook_id"))


def evaluate(messages, bot_id, state, now):
    """Return a reason and human anchor; missing/ambiguous history stays silent."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    ordered = sorted(messages, key=lambda m: int(m["id"]), reverse=True)
    relevant = [m for m in ordered if human(m) or str(m["author"]["id"]) == str(bot_id)]
    if not relevant:
        return "no_human_history", None
    latest = relevant[0]
    if str(latest["author"]["id"]) == str(bot_id):
        return "waiting_for_reply", None
    anchor = str(latest["id"])
    if int(anchor) <= int(state.get("last_prompt_id", "0")):
        return "waiting_for_reply", None
    pending = state.get("pending")
    if pending and int(anchor) <= int(pending["human_id"]):
        return "delivery_unresolved", None
    if anchor == state.get("last_generation_anchor"):
        return "already_considered", None
    timestamp = datetime.fromisoformat(latest["timestamp"].replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("message timestamp must be timezone aware")
    if (now - timestamp).total_seconds() < IDLE_SECONDS:
        return "active", anchor
    return "eligible", anchor


def save_state(path, state):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(state, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def checked_history(api, config):
    channel = api.channel(config["channel_id"])
    if (str(channel.get("guild_id")) != config["guild_id"]
            or channel.get("name") != "etc-et-chat" or channel.get("type") != 0):
        raise ValueError("Idle questions require the configured SCRU etc-et-chat text channel")
    if str(api.me()["id"]) != config["bot_id"]:
        raise ValueError("Unexpected bot identity")
    return api.history(config["channel_id"])


def validate_generation(result):
    if not isinstance(result, dict) or result.get("action") not in ("post", "skip"):
        raise ValueError("Invalid generation decision")
    if result["action"] == "post":
        text = result.get("text", "")
        if (not isinstance(text, str) or not 3 <= len(text.split()) <= 85
                or len(text) > 800 or "@" in text or "http" in text.lower()):
            raise ValueError("Invalid generated contribution")
        if result.get("source_url"):
            parsed = urlsplit(result["source_url"])
            domains = ("bbc.com", "bbc.co.uk", "arstechnica.com", "nasa.gov")
            host = (parsed.hostname or "").lower()
            if (parsed.scheme != "https" or parsed.username or parsed.password
                    or parsed.port not in (None, 443) or len(result["source_url"]) > 1000
                    or any(ord(c) <= 32 for c in result["source_url"])
                    or "<" in result["source_url"] or ">" in result["source_url"]
                    or not any(host == d or host.endswith("." + d) for d in domains)):
                raise ValueError("Invalid news source URL")
    return result


def tick(api, config, state_path, generate, *, dry_run=True, now=None):
    """Caller holds a process lock for this whole read/recheck/send transaction.

    Save intent before HTTP delivery. An ambiguous failure pauses until fresh
    human activity; never blindly retry a possibly successful channel post.
    """
    state_path = Path(state_path)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if state and (state.get("channel_id") != config["channel_id"]
                  or state.get("bot_id") != config["bot_id"]):
        raise ValueError("State belongs to a different channel or bot")
    supplied_now = now
    now = now or datetime.now(timezone.utc)
    messages = checked_history(api, config)
    reason, anchor = evaluate(messages, config["bot_id"], state, now)
    if reason != "eligible":
        return {"status": reason}
    if dry_run:
        return {"status": "would_generate", "human_id": anchor}
    # Persist before inference: no repeated model calls on a skipped, failed or
    # interrupted attempt for this same human message / quiet spell.
    state.update(channel_id=config["channel_id"], bot_id=config["bot_id"],
                 last_generation_anchor=anchor)
    save_state(state_path, state)
    generation_config = dict(config, seen_news_ids=state.get("recent_news_ids", []))
    question = validate_generation(generate(messages, generation_config, now))
    if question["action"] == "skip":
        return {"status": "generation_skipped"}
    # Recheck latest history after preparation; include other bot traffic in the
    # comparison so a busy channel cannot race a stale eligibility decision.
    fresh = api.history(config["channel_id"])
    if ([m["id"] for m in fresh] != [m["id"] for m in messages]
            or evaluate(fresh, config["bot_id"], {k: v for k, v in state.items()
                        if k != "last_generation_anchor"}, supplied_now or datetime.now(timezone.utc))[0] != "eligible"):
        return {"status": "activity_changed"}
    state.update(channel_id=config["channel_id"], bot_id=config["bot_id"],
                 pending={"human_id": anchor})
    if question.get("variation"):
        state["last_variation"] = question["variation"]
    if question.get("news_id"):
        state["recent_news_ids"] = (state.get("recent_news_ids", []) + [question["news_id"]])[-16:]
    save_state(state_path, state)
    outgoing = question["text"]
    if question.get("source_url"):
        outgoing += "\n\n<" + question["source_url"] + ">"
    sent = api.send(config["channel_id"], outgoing, anchor)
    state.update(last_prompt_id=str(sent["id"]))
    state.pop("pending", None)
    save_state(state_path, state)
    return {"status": "posted", "message_id": str(sent["id"])}
