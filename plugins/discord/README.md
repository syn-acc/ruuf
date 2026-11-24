# The Discord plugin

One plugin for Discord, with one folder for each interface it supplies. An interface is a part of
the body as ruuf names them: what a plugin has to provide for the agent to have that part here.

| Folder | What it supplies | Where the code is today |
| --- | --- | --- |
| `client.py` | The one way in and out of Discord: the token, waiting, giving up. | Here. |
| `ears/` | Hears the rooms: live messages, history, the meeting listener. | Here: `ears/reader.py`, the history reader. The meeting listener is still `hermes/overlay/gateway/meeting_listener.py`. |
| `voice/` | Speaks: send and reply, and the idle timer. It carries words and has none of its own. | Here: `voice/idle/`, the idle poster. The agent's persona stays in `plugins/discord-idle/` until the instance has a folder of its own. |
| `eyes/` | Pictures in and out. | Nothing yet. |
| `rooms/` | Keeps the place: colour roles, group roles, the welcome. | `plugins/discord-niches/`, `hermes/overlay/gateway/colour_roles.py` |

`plugin.toml` says the same thing for a program to read.

The older folders move in here one at a time, behaviour unchanged, each step proven and landed by
`routines/ship.py`. Until a folder has moved, the code that runs is still the old one.

The reader also holds three tools that speak or act: react, reply and pin. They sit in `ears/` for
now and belong to `voice/`.

Nothing of any one agent belongs in this plugin: no persona, no moods, no channel numbers. Those
are the instance's, and reach the plugin as settings. That is not true yet of `voice/idle/`: its
writer's instructions, delivery wording, channel name and news feeds still speak of one agent, and
are to be split out (`voice/idle/README.md` lists them).

    python -m unittest discover plugins/discord/tests
