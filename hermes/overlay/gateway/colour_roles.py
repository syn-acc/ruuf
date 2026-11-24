"""Deterministic Discord colour roles. Importing this module makes no API calls.

Adapter integration is explicit: call install(tree, config) before tree.sync().
The caller owns persistent role-ID storage; role names never authorize mutations.
"""
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path

PALETTE = (
    ("Cherry", 0xE74C3C), ("Crimson", 0xC0392B), ("Rose", 0xFF6B81),
    ("Coral", 0xFF7F50), ("Peach", 0xFFB38A), ("Orange", 0xE67E22),
    ("Amber", 0xF39C12), ("Gold", 0xF1C40F), ("Lemon", 0xFFF176),
    ("Lime", 0xA3CB38), ("Leaf", 0x6AB04C), ("Emerald", 0x2ECC71),
    ("Forest", 0x218C74), ("Mint", 0x7BED9F), ("Seafoam", 0x55EFC4),
    ("Teal", 0x1ABC9C), ("Turquoise", 0x00CEC9), ("Cyan", 0x00D2D3),
    ("Sky", 0x74B9FF), ("Azure", 0x3498DB), ("Blue", 0x3867D6),
    ("Indigo", 0x6C5CE7), ("Violet", 0x8854D0), ("Purple", 0x9B59B6),
    ("Lavender", 0xB8A1E3), ("Magenta", 0xE056FD), ("Pink", 0xFD79A8),
    ("Silver", 0xBDC3C7), ("Slate", 0x95A5A6), ("Ivory", 0xF5F0DC),
)

PALETTE = tuple((name.lower(), value) for name, value in PALETTE)
PALETTE_IMAGE = Path(__file__).with_name("colour_palette.png")
PALETTE_HINT = "Choose with /colour azure; clear with /colour clear."


class ColourError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    guild_id: int
    role_ids: dict  # palette name -> exact Discord role ID
    test_user_ids: frozenset  # empty is invalid while test_only is true
    test_only: bool = True
    channel_id: int = 0


class ColourRoles:
    def __init__(self, config):
        names = {name for name, _ in PALETTE}
        if (config.guild_id <= 0 or set(config.role_ids) != names
                or len(set(config.role_ids.values())) != 30
                or any(type(i) is not int or i <= 0 for i in config.role_ids.values())):
            raise ColourError("Configure exactly 30 distinct role IDs for this guild.")
        if config.test_only and not config.test_user_ids:
            raise ColourError("Test mode requires explicit test users.")
        self.config = config
        self._lock = asyncio.Lock()  # serialize switches; no per-user lock growth

    def authorize(self, member):
        guild = getattr(member, "guild", None)
        if guild is None or guild.id != self.config.guild_id:
            raise ColourError("Use this command in the configured server.")
        if getattr(member, "bot", False):
            raise ColourError("Only human members can choose a colour.")
        if self.config.test_only and member.id not in self.config.test_user_ids:
            raise ColourError("Colour roles are currently restricted to test users.")
        return guild

    def listing(self, member):
        self.authorize(member)
        return PALETTE_HINT

    def check_channel(self, channel_id):
        if self.config.channel_id and channel_id != self.config.channel_id:
            raise ColourError("Colour testing is only enabled in the sbt test channel.")

    async def handle_message(self, message):
        if message.author.bot:
            return False
        parts = (message.content or "").strip().split(maxsplit=1)
        if not parts or parts[0] not in {"/colour", "/colours"}:
            return False
        import discord
        show_palette = False
        try:
            self.check_channel(message.channel.id)
            result = (self.listing(message.author) if len(parts) == 1 or parts[1] == "list"
                      else await self.choose(message.author, parts[1]))
            show_palette = len(parts) == 1 or parts[1] == "list"
        except ColourError as exc:
            result = str(exc)
        except discord.HTTPException:
            result = "Discord could not finish the change. Retry shortly."
        await send_response(message.reply, result, palette=show_palette,
                            mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        return True

    async def choose(self, member, requested):
        guild = self.authorize(member)
        canonical = {name.casefold(): name for name, _ in PALETTE}
        key = requested.strip().casefold()
        if key != "clear" and key not in canonical:
            raise ColourError("Unknown colour. Use /colours to see the palette.")
        async with self._lock:
            # Fetch current state, not stale interaction caches.
            roles = {role.id: role for role in await guild.fetch_roles()}
            me = await guild.fetch_member(guild.me.id)
            member = await guild.fetch_member(member.id)
            if not me.guild_permissions.manage_roles:
                raise ColourError("The bot needs Manage Roles before this can run.")
            selected = [roles.get(i) for i in self.config.role_ids.values()]
            if any(r is None or r.managed or r.is_default() or r.permissions.value
                   or r >= me.top_role for r in selected):
                raise ColourError("Colour roles must exist, have no permissions, and sit below the bot.")
            # A zero-permission role can still grant access through an overwrite.
            channels = await guild._state.http.get_all_guild_channels(guild.id)
            role_ids = {str(role.id) for role in selected}
            for channel in channels:
                if any(str(overwrite["id"]) in role_ids and int(overwrite["type"]) == 0
                       for overwrite in channel.get("permission_overwrites", [])):
                    raise ColourError("Colour roles must have no channel permission overrides.")
            target = None if key == "clear" else roles[self.config.role_ids[canonical[key]]]
            owned = [r for r in member.roles if r.id in self.config.role_ids.values()]
            old = [r for r in owned if target is None or r.id != target.id]
            # Individual role endpoints preserve all unrelated roles. Add first so
            # an add failure retains the old colour. Report partial remove failures.
            if target is not None and target not in member.roles:
                await member.add_roles(target, reason="Self-service colour", atomic=True)
            try:
                for role in old:
                    await member.remove_roles(role, reason="Self-service colour", atomic=True)
            except Exception as exc:
                raise ColourError("Colour cleanup failed; retry the same choice to finish.") from exc
            return "Colour cleared." if target is None else f"Colour set to {canonical[key]}."


async def provision_test_palette(guild, *, confirmed_guild_id, bot_member):
    """Explicit setup helper; never called by a user command or on import.

    Returns exact IDs to persist. Refuses existing names to prevent adopting
    unrelated roles. If creation fails, stop and retain Discord's audit trail;
    inspect partial roles before retrying. No automatic deletion.
    """
    if guild.id != confirmed_guild_id or not bot_member.guild_permissions.manage_roles:
        raise ColourError("Wrong guild or missing Manage Roles.")
    roles = await guild.fetch_roles()
    if any(r.name in {f"colour · {n}" for n, _ in PALETTE} for r in roles):
        raise ColourError("Palette names already exist; inspect before provisioning.")
    import discord
    result = {}
    for name, value in PALETTE:
        role = await guild.create_role(
            name=f"colour · {name}", colour=discord.Colour(value),
            permissions=discord.Permissions.none(), hoist=False, mentionable=False,
            reason="Colour-role test setup",
        )
        result[name] = role.id
    return result



async def send_response(send, content, *, palette=False, **kwargs):
    if not palette:
        return await send(content, **kwargs)
    import discord
    try:
        attachment = discord.File(PALETTE_IMAGE, filename="colours.png",
                                  description="30 named nickname colour swatches; choose with /colour followed by a name.")
        try:
            return await send(content, file=attachment, **kwargs)
        finally:
            attachment.close()
    except (OSError, discord.HTTPException):
        # Still usable if the attachment is unavailable or Attach Files is denied.
        names = ", ".join(name for name, _ in PALETTE)
        return await send(f"{content}\nPalette image unavailable. Colours: {names}", **kwargs)


def install(tree, config):
    """Register two guild-scoped interactions, without enabling general AI access.

    Requires discord.py in the Hermes runtime. Caller must sync this guild.
    """
    import discord
    from discord import app_commands
    service = ColourRoles(config)
    guild = discord.Object(id=config.guild_id)

    async def respond(interaction, requested=None):
        await interaction.response.defer(ephemeral=True)
        show_palette = False
        try:
            service.check_channel(interaction.channel_id)
            content = (service.listing(interaction.user) if requested is None
                       else await service.choose(interaction.user, requested))
            show_palette = requested is None
        except ColourError as exc:
            content = str(exc)
        except discord.HTTPException:
            content = "Discord could not finish the change. Retry your choice shortly."
        await send_response(interaction.followup.send, content, palette=show_palette,
                            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="colours", description="List the 30 nickname colours")
    async def colours(interaction: discord.Interaction):
        await respond(interaction)

    @app_commands.command(name="colour", description="Choose your nickname colour, or clear it")
    async def colour(interaction: discord.Interaction, colour: str = ""):
        await respond(interaction, colour or None)

    @colour.autocomplete("colour")
    async def autocomplete(interaction: discord.Interaction, current: str):
        try:
            service.check_channel(interaction.channel_id)
            service.authorize(interaction.user)
        except ColourError:
            return []
        names = [n for n, _ in PALETTE] + ["clear"]
        # Discord autocomplete accepts at most 25 choices; typing searches all 30.
        return [app_commands.Choice(name=n, value=n) for n in names
                if current.casefold() in n.casefold()][:25]

    tree.add_command(colours, guild=guild)
    tree.add_command(colour, guild=guild)
    return service


def install_from_file(tree, path):
    path = Path(path)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return install(tree, Config(
        guild_id=int(data["guild_id"]), role_ids=data["role_ids"],
        test_user_ids=frozenset(int(i) for i in data["test_user_ids"]),
        test_only=data.get("test_only", True), channel_id=int(data["channel_id"]),
    ))
