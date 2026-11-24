"""Deterministic, reaction-driven access roles for configured Discord groups."""
import asyncio
import json
import logging
import sqlite3
import string
import unicodedata
from pathlib import Path

log = logging.getLogger(__name__)


def _emoji_key(value):
    # Custom emoji identity is its numeric Discord ID; names can be changed.
    ident = getattr(value, "id", None)
    if ident is not None:
        return f"custom:{ident}"
    raw = str(value)
    if raw.startswith("<") and raw.endswith(">"):
        # Accept Discord's <:name:id> and <a:name:id> config spellings.
        parts = raw[1:-1].split(":")
        if len(parts) == 3 and parts[0] in {"", "a"} and parts[2].isdigit():
            return f"custom:{int(parts[2])}"
    # Discord may include presentation selectors in raw payloads inconsistently.
    return "".join(c for c in unicodedata.normalize("NFC", raw)
                   if c not in "\ufe0e\ufe0f")


class NicheRoles:
    def __init__(self, bot, config, state_path):
        self.bot = bot
        self.guild_id = int(config["guild_id"])
        self.message_id = int(config["message_id"])
        self.channel_id = int(config["channel_id"])
        self.groups = {}
        role_ids = set()
        group_keys = set()
        for raw in config["groups"]:
            group = dict(raw)
            group["role_id"] = int(group["role_id"])
            group["channel_id"] = int(group.get("channel_id", self.channel_id))
            welcome = group.get("welcome", "")
            if (group["role_id"] <= 0 or group["channel_id"] <= 0 or not group.get("key")
                    or not group.get("emoji") or not isinstance(welcome, str)
                    or any(field != "mention" for _, field, _, _ in string.Formatter().parse(welcome)
                           if field is not None)):
                raise ValueError("Each niche group needs key, emoji and role_id")
            key = _emoji_key(group["emoji"])
            if key in self.groups or group["role_id"] in role_ids or group["key"] in group_keys:
                raise ValueError("Niche group emojis, keys and role IDs must be unique")
            role_ids.add(group["role_id"])
            group_keys.add(group["key"])
            self.groups[key] = group
        if self.guild_id <= 0 or self.message_id <= 0 or self.channel_id <= 0 or not self.groups:
            raise ValueError("Invalid niche reaction-role configuration")
        self.state_path = Path(state_path)
        self._locks = {}
        self._db = sqlite3.connect(self.state_path)
        self.state_path.chmod(0o600)
        self._db.execute("CREATE TABLE IF NOT EXISTS welcomed (guild_id INTEGER, user_id INTEGER, group_key TEXT, PRIMARY KEY (guild_id,user_id,group_key))")
        self._db.commit()
        bot.add_listener(self.on_raw_reaction_add, "on_raw_reaction_add")
        bot.add_listener(self.on_raw_reaction_remove, "on_raw_reaction_remove")
        log.info("Niche reaction roles enabled: guild=%s channel=%s message=%s groups=%s",
                 self.guild_id, self.channel_id, self.message_id, len(self.groups))

    async def on_raw_reaction_add(self, payload):
        await self._dispatch(payload)

    async def on_raw_reaction_remove(self, payload):
        await self._dispatch(payload)

    async def _dispatch(self, payload):
        try:
            group = self.groups.get(_emoji_key(payload.emoji))
            if (group is None or payload.guild_id != self.guild_id
                    or payload.channel_id != self.channel_id or payload.message_id != self.message_id):
                return
            guild = self.bot.get_guild(self.guild_id)
            if guild is None:
                return
            lock = self._locks.setdefault((payload.user_id, group["key"]), asyncio.Lock())
            async with lock:
                member = await guild.fetch_member(payload.user_id)
                if member.bot:
                    return
                # The current reaction roster wins over callback arrival order.
                channel = self.bot.get_channel(self.channel_id) or await self.bot.fetch_channel(self.channel_id)
                message = await channel.fetch_message(self.message_id)
                reaction = next((r for r in message.reactions
                                 if _emoji_key(r.emoji) == _emoji_key(group["emoji"])), None)
                present = False
                if reaction is not None:
                    async for reactor in reaction.users(limit=None):
                        if reactor.id == member.id:
                            present = True
                            break
                if present:
                    await self._grant(guild, member, group)
                else:
                    await self._revoke(guild, member, group)
        except Exception:
            log.exception("Niche reaction-role event failed")

    async def _safe_role(self, guild, role_id):
        roles = await guild.fetch_roles()
        role = next((r for r in roles if r.id == role_id), None)
        me = await guild.fetch_member(self.bot.user.id)
        if (role is None or role.managed or role.is_default() or role.permissions.value
                or not me.guild_permissions.manage_roles or role >= me.top_role):
            raise ValueError("Configured niche role is missing or unsafe")
        return role

    async def _grant(self, guild, member, group):
        role = await self._safe_role(guild, group["role_id"])
        if role not in member.roles:
            await member.add_roles(role, atomic=True, reason="Niche group reaction opt-in")
            member = await guild.fetch_member(member.id)
        cursor = self._db.execute("INSERT OR IGNORE INTO welcomed VALUES (?,?,?)",
                                  (self.guild_id, member.id, group["key"]))
        self._db.commit()
        if cursor.rowcount:
            channel = self.bot.get_channel(group["channel_id"]) or await self.bot.fetch_channel(group["channel_id"])
            # Recalculate after the role grant; no welcome when the member cannot see the group.
            permissions = channel.permissions_for(member)
            if permissions.view_channel:
                try:
                    import discord
                    await channel.send(group["welcome"].format(mention=member.mention),
                                       allowed_mentions=discord.AllowedMentions(
                                           users=[member], everyone=False, roles=False, replied_user=False))
                except Exception:
                    # Marker is deliberately retained: ambiguous sends must not double-ping.
                    log.exception("Niche welcome delivery failed for group %s", group["key"])

    async def _revoke(self, guild, member, group):
        role = await self._safe_role(guild, group["role_id"])
        if role in member.roles:
            await member.remove_roles(role, atomic=True, reason="Niche group reaction opt-out")


def install_from_file(bot, config_path):
    path = Path(config_path)
    if not path.exists():
        return None
    config = json.loads(path.read_text())
    if int(config.get("message_id", 0)) == 0:
        return None
    return NicheRoles(bot, config, path.with_name("niches.sqlite3"))
