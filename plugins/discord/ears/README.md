# Sym's native Discord reader

The host-side MCP integration reuses the running Hermes bot's credentials. It
needs no second bot, new token, browser login or marketplace registration.
`codex-mcp.toml` registers the tools directly with the Codex app-server that drives
Sym; `native-context.md` teaches that runtime when and how to retrieve discussion.

Tools discover guilds/channels/active threads, page history, read bounded channel
batches, discover archived threads, scan bounded history for keyword matches, and
perform explicitly requested reactions, replies, and pins.
Messages preserve author, timestamps, replies and source links. History and search
are on demand; there is no continuous model invocation or full-server prompt dump.
The reader does not create a permanent message archive or embedding service.

## Runtime integration

Install this directory at `/data/syn.gl/hermes/plugins/discord-reader`. The
runtime venv supplies `requests`; `hermes_cli.config.load_env()` loads the existing
token and owner allowlist inside the reader process, without forwarding the token
through Codex or writing it to MCP configuration.

Append `codex-mcp.toml` outside the generated section of
`/data/syn.gl/hermes/.codex/config.toml`; preserve all existing configuration.
Include `native-context.md` in `/data/syn.gl/hermes/.codex/AGENTS.md` and install
`skills/read-discord` under `.codex/skills/read-discord`. Apply the runtime overlay
`deploy/hermes-runtime/patches/discord-reader-session-scope.patch`: it retires a
Codex session when the trusted gateway platform/user/channel/thread changes, so
a shared conversation cannot retain a previous requester's reader privileges.
Also apply `discord-reader-tool-trust.patch`, which accepts tool confirmation only for this host-installed scoped server alongside Hermes's existing trusted server. Restart the gateway after validating the configuration. Preserve existing auth,
conversation databases, plugin state and unrelated runtime changes.

Write actions re-fetch the target message and apply the same authenticated session
scope as reads. Replies disable automatic mention parsing. Reactions accept Unicode
emoji or Discord custom emoji in `name:id` form. Pins still require the bot's
Discord `Manage Messages` permission.

The MCP config forwards only the gateway's session identity variables. Calls
without authenticated Discord context fail closed. Owners may read all guild
channels the bot can access; other requesters are restricted to their source
channel and its parent. This protects the existing channel-scoped collaborator
grant. Tools accept IDs for targets, never an asserted requester identity.

## Coverage and cost

Discord's bot membership, View Channel, Read Message History, private-thread
membership and message-content intent still apply. Channel discovery may list a
channel whose history returns 403. Active-thread discovery excludes archives;
archived-thread discovery is separately paginated. Forum containers need their
threads to be read. Search reports its bounded scan coverage, not a full-server
index. Attachment metadata is available; this does not transcribe voice calls or
read every attachment automatically. Private-channel material must stay within
its audience when Sym composes a reply.

Use `python3 -m unittest discover -s tests -p test_discord_reader.py -q` for the
focused offline checks. For deployment, additionally check real history and MCP
tool discovery as the service user; perform one bounded real adapter turn to
verify tool invocation by the model. Do not post a test message to Discord.
