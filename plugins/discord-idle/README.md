# What is left here

The idle poster's code moved to `plugins/discord/voice/idle/`: it is the voice of the one Discord
plugin. `run.py` and `generate.py` here are a few lines each that run or hand on the moved ones.

The other files are the agent's own, not the plugin's, and wait here for the instance folder:

| File | What it is |
| --- | --- |
| `persona.md` | Who the agent is. The moved code finds it here, unless the launcher names another place. |
| `questions.json` | An older bank of questions in the agent's voice. Not used at runtime. |
| `config.example.json` | Example settings for one deployment: its server, channel and bot. |
