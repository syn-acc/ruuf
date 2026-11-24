# Find your niche reaction roles

Deterministic self-service for the SCRU guild; no inference or new bot token.
Source: `niche_roles.py`. Reviewed menu copy: `post.txt`. The example JSON is
inactive (`message_id: 0`) and contains unresolved IDs; do not activate it as-is.

Provisioned on 2026-11-16 under the new **Niches** category:

| Group | Emoji | Existing role | Existing channel |
| --- | --- | --- | --- |
| Chinese | 🀄 | Chinese | chinese |
| Rationalism | 🧭 | Rationalism | rationalism |
| Seek Discomfort | 🏔️ | Seek Discomfort | yes-fam |
| Mech Interp | 🔬 | Machine Whisperer | mech |
| Chess | ♟️ | Chess | chess |
| Agent Swarms / Minecraft | 🐝 | Agent Swarms / Minecraft | minecraft |
| League of Legends | ⚔️ | League | league-of-legends |

Do not assume the existing role `成员` is the Chinese group without confirmation.
The server also has member-specific channel overrides. Preserve those and every
unrelated role/overwrite. Removing a niche role revokes access obtained from that
role; administrators and members with another explicit access grant retain it.

## Setup

1. Confirm the channel mappings; create missing permissionless, non-mentionable
   group roles below Sym. Use the existing Seek Discomfort, Machine Whisperer,
   and League roles where appropriate. Inspect where existing roles grant access
   before exposing them to self-assignment.
2. Each target channel needs an explicit `@everyone` deny for View Channel and
   an explicit group-role allow for View Channel, Send Messages, and Read Message
   History. Preserve unrelated overwrites; do not copy the Private Chats category
   wholesale, since it grants access to several different roles.
3. Post the menu yourself in `#find-your-niche`, then put that exact message ID
   in the JSON. Fill every zero role/channel ID. Seed the seven emoji reactions
   on that message with the bot so members can click them.
4. Deploy `niche_roles.py` as `runtime/gateway/niche_roles.py`; store the JSON as
   `$HERMES_HOME/niche-roles.json` with mode 0600. Ensure the service user can
   write the adjacent SQLite database. Configuration contains IDs, not secrets.
5. Apply `deploy/hermes-runtime/patches/discord-niche-roles.patch` after the
   existing colour-role patch. It registers listeners on the existing Bot.
   Default discord.py intents include guild reactions; verify they remain enabled.
6. Compile both files, restart only `bot-syn-gl.service`, inspect connection and
   niche-listener errors. Verify with one human adding/removing a reaction and
   rejoining: only the selected role changes, the correct channel opens/closes,
   and only the first join pings. Do not send test welcomes without authorization.

## Behavior and limits

The configured guild, channel, message, and emoji must all match. Bots are
ignored. Each mutation uses a freshly fetched member, exact role ID, and bot
hierarchy/permission checks. Only permissionless roles can be assigned. Raw
reaction events work without a message cache. The handler checks current
reaction membership before applying a change, so a queued old event does not
blindly undo a later one.

A persistent per-user/per-group welcome marker is claimed before sending. This
prevents repeated welcome attempts across restarts and concurrent events. If
Discord rejects delivery or the connection fails ambiguously, inspect logs and
the channel before manually retrying; the marker deliberately remains claimed.
Only the joining member may be pinged; role/everyone mentions are disabled.

Events occurring while the bot is offline are not automatically replayed. After
an outage, reconcile the menu's reaction users with the configured group roles
before declaring the roster current. Removing a reaction cannot revoke other
roles, administrative access, or explicit member overrides. Bulk clearing all
reactions and deleting the menu are administrative operations, not supported
leave actions; do not use them to remove group memberships.

Activated on 2026-11-16 for Syn’s menu message `1571857178044923976` in
`#find-your-niche`. The seven synthwave icons are uploaded as `scru_*` custom
emojis, and `config.scru.json` records the live role, channel, emoji, and message
IDs. The runtime logged the exact menu and seven groups, then reconnected
successfully after the service restart. All seven reactions were seeded.

Three focused tests passed. An offline check with real discord.py verified both
listeners, all seven custom-ID mappings (including renames), reactions intent,
and service-user configuration access. Live permission checks verified member
menu reactions, assignable permissionless roles, and role access to each group.
No test welcome messages were sent. Human join/leave delivery remains to be
observed. Welcome markers persist in the service-owned mode-0600 SQLite database.
