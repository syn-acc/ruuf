#!/usr/bin/env python3
"""Dependency-light Discord MCP server with bounded, session-scoped access."""

import importlib
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import client  # noqa: E402  plugins/discord/client.py: the one way in and out of Discord

AGENT = "DiscordBot (https://syn.gl, 1.0)"
MAX_CONTENT = 4000
MAX_SEARCH_PAGES = 5
MAX_SEARCH_RESULTS = 20
MAX_BATCH_CHANNELS = 10
ID_RE = re.compile(r"^[0-9]+$")


def load_config():
    """Load host runtime configuration only when the process env lacks a token."""
    if os.environ.get("DISCORD_BOT_TOKEN"):
        return
    runtime = "/data/syn.gl/hermes/runtime"
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    try:
        return importlib.import_module("hermes_cli.config").load_env()
    except Exception:
        # The caller receives a generic configuration error; never print config data.
        return {}


def token():
    value = os.environ.get("DISCORD_BOT_TOKEN")
    if not value:
        loaded = load_config() or {}
        value = loaded.get("DISCORD_BOT_TOKEN") or loaded.get("discord_bot_token")
    if not value:
        raise RuntimeError("Discord reader is not configured")
    return value


_discord = None


def discord():
    """The plugin's one client, built once from this reader's token."""
    global _discord
    if _discord is None:
        _discord = client.Discord(token(), agent=AGENT)
    return _discord


def discord_request(method, path, params=None, payload=None, expect_json=True):
    """One call through plugins/discord/client.py. A read is retried on a rate limit (twice, two
    seconds at most); a write never is, so it cannot arrive twice."""
    try:
        reply = discord().request(method, path, params=params, json=payload)
    except client.DiscordError as error:
        if error.status == 429 and "longer wait" in str(error):
            raise RuntimeError("Discord rate limit exceeded; retry delay is over the 2 second limit") from None
        if error.status == 429 and "after two retries" in str(error):
            raise RuntimeError("Discord rate limit exceeded after two retries") from None
        if error.status is None:
            raise RuntimeError("Discord API request failed") from None
        raise RuntimeError(f"Discord API request failed (HTTP {error.status})") from None
    except ValueError:  # a reply that was not JSON
        if expect_json:
            raise RuntimeError("Discord API request failed") from None
        return None
    return reply if expect_json else None


def discord_get(path, params=None):
    return discord_request("GET", path, params=params)


def required(args, name):
    value = args.get(name)
    if value is None or str(value) == "":
        raise ValueError(f"Missing required argument: {name}")
    return valid_id(value, name)


def valid_id(value, name="ID"):
    value = str(value)
    if not ID_RE.fullmatch(value):
        raise ValueError(f"{name} must contain digits only")
    return value


def context():
    platform = os.environ.get("HERMES_SESSION_PLATFORM", "").lower()
    user_id = os.environ.get("HERMES_SESSION_USER_ID", "")
    channel_id = os.environ.get("HERMES_SESSION_CHAT_ID", "")
    thread_id = os.environ.get("HERMES_SESSION_THREAD_ID", "")
    if platform != "discord" or not user_id or not (channel_id or thread_id):
        raise PermissionError("Discord session context is required")
    user_id = valid_id(user_id, "session user ID")
    source_id = valid_id(thread_id or channel_id, "session channel ID")
    owner_config = os.environ.get("DISCORD_ALLOWED_USERS", "")
    if not owner_config:
        loaded = load_config() or {}
        owner_config = loaded.get("DISCORD_ALLOWED_USERS", "") or loaded.get("discord_allowed_users", "")
    owners = {x.strip() for x in owner_config.split(",") if x.strip()}
    return user_id, source_id, user_id in owners


def authorize_channel(channel_id):
    _, source_id, owner = context()
    channel_id = valid_id(channel_id, "channel_id")
    if owner:
        channel = discord_get(f"/channels/{channel_id}")
        if not channel.get("guild_id"):
            raise PermissionError("Direct messages are not available through this reader")
        return channel_id
    if channel_id == source_id:
        return channel_id
    # Resolve a nonowner's source channel to support the actual parent of a thread.
    source = discord_get(f"/channels/{source_id}")
    if not source.get("guild_id"):
        raise PermissionError("Direct messages are not available through this reader")
    parent_id = source.get("parent_id") if source.get("type") in {10, 11, 12} else None
    if parent_id and channel_id == valid_id(parent_id, "parent channel ID"):
        return channel_id
    raise PermissionError("Channel is outside this Discord session's scope")


def authorize_message(channel_id, message_id):
    channel_id = authorize_channel(channel_id)
    message_id = valid_id(message_id, "message_id")
    message = discord_get(f"/channels/{channel_id}/messages/{message_id}")
    if valid_id(message.get("channel_id"), "message channel ID") != channel_id:
        raise PermissionError("Message is outside the authorized channel")
    return channel_id, message_id, message


def compact_message(message):
    author = message.get("author") or {}
    channel_id = message.get("channel_id")
    message_id = message.get("id")
    permalink = None
    if channel_id and message_id:
        guild_id = (message.get("guild_id") or "@me")
        permalink = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
    return {
        "id": message_id, "channel_id": channel_id, "timestamp": message.get("timestamp"),
        "edited_timestamp": message.get("edited_timestamp"),
        "author": {"id": author.get("id"), "username": author.get("global_name") or author.get("username"), "bot": author.get("bot", False)},
        "content": (message.get("content", "") or "")[:MAX_CONTENT], "permalink": permalink,
        "attachments": [{"filename": a.get("filename"), "url": a.get("url"), "content_type": a.get("content_type")} for a in message.get("attachments", [])],
        "embeds": [{"title": e.get("title"), "description": (e.get("description") or "")[:MAX_CONTENT], "url": e.get("url")} for e in message.get("embeds", [])],
        "reply_to": ((message.get("message_reference") or {}).get("message_id")), "thread": message.get("thread"),
    }


def list_guilds(_args):
    _, _, owner = context()
    if not owner:
        raise PermissionError("Guild discovery is available to bot owners only")
    return [{"id": g["id"], "name": g["name"], "owner": g.get("owner", False)} for g in discord_get("/users/@me/guilds", {"limit": 200})]


def list_channels(args):
    _, _, owner = context()
    if not owner:
        raise PermissionError("Channel discovery is available to bot owners only")
    guild_id = required(args, "guild_id")
    channels = discord_get(f"/guilds/{guild_id}/channels")
    if args.get("include_active_threads", True):
        channels.extend(discord_get(f"/guilds/{guild_id}/threads/active").get("threads", []))
    allowed_types = {0, 5, 10, 11, 12, 15, 16}
    return [{"id": c.get("id"), "name": c.get("name"), "type": c.get("type"), "parent_id": c.get("parent_id"), "position": c.get("position"), "topic": c.get("topic"), "last_message_id": c.get("last_message_id"), "thread_metadata": c.get("thread_metadata"), "permissions_unverified": True} for c in channels if c.get("type") in allowed_types]


def read_channel(args):
    channel_id = authorize_channel(required(args, "channel_id"))
    limit = min(max(int(args.get("limit", 25)), 1), 100)
    channel = discord_get(f"/channels/{channel_id}")
    if not channel.get("guild_id"):
        raise PermissionError("Direct messages are not available through this reader")
    messages = discord_get(f"/channels/{channel_id}/messages", {"limit": limit, "before": args.get("before"), "after": args.get("after")})
    for message in messages:
        message.setdefault("guild_id", channel.get("guild_id"))
    return {"channel_id": channel_id, "messages": [compact_message(m) for m in messages], "next_before": messages[-1]["id"] if messages else None, "count": len(messages)}


def read_channels(args):
    ids = args.get("channel_ids") or []
    if not ids:
        raise ValueError("channel_ids must contain at least one channel ID")
    if len(ids) > MAX_BATCH_CHANNELS:
        raise ValueError(f"At most {MAX_BATCH_CHANNELS} channels can be read per call")
    limit = min(max(int(args.get("limit_per_channel", 10)), 1), 100)
    output = []
    for cid in ids:
        try:
            output.append(read_channel({"channel_id": valid_id(cid, "channel ID"), "limit": limit, "before": args.get("before")}))
        except Exception as exc:
            output.append({"channel_id": str(cid), "error": str(exc)})
    return output


def archived_threads(args):
    _, _, owner = context()
    if not owner:
        raise PermissionError("Archived thread discovery is available to bot owners only")
    channel_id = authorize_channel(required(args, "channel_id"))
    before = args.get("before")
    if before and args.get("private", False):
        before = valid_id(before, "before")
    metadata = discord_get(f"/channels/{channel_id}")
    if not metadata.get("guild_id"):
        raise PermissionError("Direct messages are not available through this reader")
    endpoint = f"/channels/{channel_id}/users/@me/threads/archived/private" if args.get("private", False) else f"/channels/{channel_id}/threads/archived/public"
    data = discord_get(endpoint, {"before": before, "limit": 100})
    threads = data.get("threads", [])
    cursor = None
    if threads:
        cursor = threads[-1].get("id") if args.get("private", False) else threads[-1].get("thread_metadata", {}).get("archive_timestamp")
    return {"threads": threads, "has_more": bool(data.get("has_more")), "next_before": cursor}


def search_messages(args):
    channel_id = authorize_channel(required(args, "channel_id"))
    channel = discord_get(f"/channels/{channel_id}")
    if not channel.get("guild_id"):
        raise PermissionError("Direct messages are not available through this reader")
    query = str(args.get("query", "")).strip().lower()
    if not query:
        raise ValueError("query must not be empty")
    before = args.get("before")
    if before:
        before = valid_id(before, "before")
    matches, scanned, pages, exhausted, next_before = [], 0, 0, False, before
    for _ in range(MAX_SEARCH_PAGES):
        page = discord_get(f"/channels/{channel_id}/messages", {"limit": 100, "before": before})
        pages += 1
        for msg in page:
            scanned += 1
            next_before = msg["id"]
            msg.setdefault("guild_id", channel.get("guild_id"))
            content = msg.get("content", "") or ""
            if query in content.lower():
                item = compact_message(msg)
                item["snippet"] = content[:MAX_CONTENT]
                matches.append(item)
                if len(matches) >= MAX_SEARCH_RESULTS:
                    break
        if len(matches) >= MAX_SEARCH_RESULTS:
            break
        if len(page) < 100:
            exhausted = True
            break
        before = page[-1]["id"]
    return {"channel_id": channel_id, "query": query, "matches": matches, "scanned": scanned, "pages_scanned": pages, "max_pages": MAX_SEARCH_PAGES, "exhausted": exhausted, "next_before": next_before, "coverage": "channel history exhausted" if exhausted else f"scanned up to {scanned} messages; older history may remain"}


def react_message(args):
    channel_id, message_id, _ = authorize_message(required(args, "channel_id"), required(args, "message_id"))
    emoji = str(args.get("emoji", "")).strip()
    if not emoji or len(emoji) > 100:
        raise ValueError("emoji must be a Unicode emoji or custom emoji in name:id form")
    encoded_emoji = urllib.parse.quote(emoji, safe="")
    discord_request("PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{encoded_emoji}/@me", expect_json=False)
    return {"channel_id": channel_id, "message_id": message_id, "emoji": emoji, "reacted": True}


def reply_message(args):
    channel_id, message_id, _ = authorize_message(required(args, "channel_id"), required(args, "message_id"))
    content = str(args.get("content", ""))
    if not content.strip():
        raise ValueError("content must not be empty")
    if len(content) > 2000:
        raise ValueError("content must be at most 2000 characters")
    created = discord_request("POST", f"/channels/{channel_id}/messages", payload={
        "content": content,
        "message_reference": {"message_id": message_id, "channel_id": channel_id},
        "allowed_mentions": {"parse": []},
    })
    created.setdefault("guild_id", (created.get("message_reference") or {}).get("guild_id"))
    return compact_message(created)


def pin_message(args):
    channel_id, message_id, _ = authorize_message(required(args, "channel_id"), required(args, "message_id"))
    discord_request("PUT", f"/channels/{channel_id}/pins/{message_id}", expect_json=False)
    return {"channel_id": channel_id, "message_id": message_id, "pinned": True}


TOOLS = {
    "discord_list_guilds": {"description": "List servers visible to the configured bot (owners only).", "schema": {"type": "object", "properties": {}, "additionalProperties": False}, "handler": list_guilds},
    "discord_list_channels": {"description": "List channels visible in a server; permissions are not verified.", "schema": {"type": "object", "properties": {"guild_id": {"type": "string"}, "include_active_threads": {"type": "boolean", "default": True}}, "required": ["guild_id"], "additionalProperties": False}, "handler": list_channels},
    "discord_read_channel": {"description": "Read a bounded page of messages from the scoped channel.", "schema": {"type": "object", "properties": {"channel_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25}, "before": {"type": "string"}, "after": {"type": "string"}}, "required": ["channel_id"], "additionalProperties": False}, "handler": read_channel},
    "discord_read_channels": {"description": "Read recent messages from up to 10 authorized channels.", "schema": {"type": "object", "properties": {"channel_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10}, "limit_per_channel": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10}, "before": {"type": "string"}}, "required": ["channel_ids"], "additionalProperties": False}, "handler": read_channels},
    "discord_archived_threads": {"description": "List one page of archived public or private threads for an authorized channel.", "schema": {"type": "object", "properties": {"channel_id": {"type": "string"}, "private": {"type": "boolean", "default": False}, "before": {"type": "string"}}, "required": ["channel_id"], "additionalProperties": False}, "handler": archived_threads},
    "discord_search_messages": {"description": "Search authorized channel history for a keyword, scanning at most five pages of 100 messages.", "schema": {"type": "object", "properties": {"channel_id": {"type": "string"}, "query": {"type": "string"}, "before": {"type": "string"}}, "required": ["channel_id", "query"], "additionalProperties": False}, "handler": search_messages},
    "discord_react_message": {"description": "Add the bot's reaction to a message in an authorized Discord channel.", "schema": {"type": "object", "properties": {"channel_id": {"type": "string"}, "message_id": {"type": "string"}, "emoji": {"type": "string", "description": "A Unicode emoji or custom emoji in name:id form."}}, "required": ["channel_id", "message_id", "emoji"], "additionalProperties": False}, "handler": react_message},
    "discord_reply_message": {"description": "Reply to a message in an authorized Discord channel. User mentions are not expanded automatically.", "schema": {"type": "object", "properties": {"channel_id": {"type": "string"}, "message_id": {"type": "string"}, "content": {"type": "string", "minLength": 1, "maxLength": 2000}}, "required": ["channel_id", "message_id", "content"], "additionalProperties": False}, "handler": reply_message},
    "discord_pin_message": {"description": "Pin a message in an authorized Discord channel when the bot has Manage Messages permission.", "schema": {"type": "object", "properties": {"channel_id": {"type": "string"}, "message_id": {"type": "string"}}, "required": ["channel_id", "message_id"], "additionalProperties": False}, "handler": pin_message},
}


def respond(request_id, result=None, error=None):
    message = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        message["error"] = {"code": -32603, "message": str(error)}
    else:
        message["result"] = result
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        try:
            request = json.loads(line)
            method, request_id = request.get("method"), request.get("id")
            if method == "initialize":
                respond(request_id, {"protocolVersion": request.get("params", {}).get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}}, "serverInfo": {"name": "discord-reader", "version": "0.3.0"}})
            elif method == "notifications/initialized":
                continue
            elif method == "ping":
                respond(request_id, {})
            elif method == "tools/list":
                respond(request_id, {"tools": [{"name": n, "description": t["description"], "inputSchema": t["schema"]} for n, t in TOOLS.items()]})
            elif method == "tools/call":
                params = request.get("params") or {}
                tool = TOOLS.get(params.get("name"))
                if not tool:
                    raise ValueError("Unknown tool")
                try:
                    value = tool["handler"](params.get("arguments") or {})
                    respond(request_id, {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]})
                except Exception as exc:
                    respond(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
            elif request_id is not None:
                respond(request_id, error=f"Unsupported method: {method}")
        except Exception as exc:
            if "request_id" in locals() and request_id is not None:
                respond(request_id, error=exc)


if __name__ == "__main__":
    main()
