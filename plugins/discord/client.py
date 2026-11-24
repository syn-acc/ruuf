"""client: the one way in and out of Discord.

Every interface of this plugin (ears, voice, eyes, rooms) talks to Discord through this file and
nowhere else. It knows how to authenticate, how long to wait, and when to give up. It knows nothing
about who the agent is or what it says.

    from client import Discord
    discord = Discord.from_places()          # token from the place called `env`, or the environment
    discord.get("/channels/123/messages", params={"limit": 50})
    discord.post("/channels/123/messages", json={"content": "..."})

Two rules it holds for everyone:
  - A read may be retried when Discord says to slow down: twice at most, and never for longer than
    two seconds a time. A write is never retried, because a second try can deliver twice.
  - An error never carries the token, the request or the reply body: only what kind of failure it was.

Needs `requests`.
"""
import os
import time
from pathlib import Path

API = "https://discord.com/api/v10"
AGENT = "DiscordBot (https://github.com/syn-acc/ruuf, 1.0)"
MAX_WAIT = 2.0
MAX_RETRIES = 2


class DiscordError(RuntimeError):
    """What went wrong, in words safe to log. `status` is the HTTP status, or None for a network failure."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def read_token(env=None):
    """The bot token: from the environment, else from the dotenv file at the place called `env`."""
    env = os.environ if env is None else env
    token = env.get("DISCORD_BOT_TOKEN")
    if token:
        return token
    place = env.get("RUUF_PLACE_ENV")
    if place:
        try:
            for line in Path(place).read_text(encoding="utf-8").splitlines():
                key, sep, value = line.strip().partition("=")
                if sep and key.removeprefix("export ").strip() == "DISCORD_BOT_TOKEN":
                    return value.strip().strip("'\"")
        except OSError:
            pass
    raise DiscordError("the Discord token is not configured")


class Discord:
    def __init__(self, token, session=None, sleep=time.sleep, agent=AGENT):
        if session is None:
            import requests
            session = requests.Session()
        self.session = session
        self.sleep = sleep
        self.headers = {"Authorization": "Bot " + token, "User-Agent": agent}

    @classmethod
    def from_places(cls, env=None, **kwargs):
        return cls(read_token(env), **kwargs)

    def request(self, method, path, *, params=None, json=None, files=None, retry=None):
        """One call. Returns the decoded reply, or None when there is no body."""
        method = method.upper()
        if not path.startswith("/"):
            raise ValueError("path must start with /")
        if retry is None:
            retry = method == "GET"
        if params:
            params = {k: v for k, v in params.items() if v is not None}
        tries = 0
        while True:
            try:
                reply = self.session.request(method, API + path, headers=self.headers, params=params or None,
                                             json=json, files=files, timeout=15)
            except Exception as exc:  # requests.RequestException, without importing requests to say so
                if type(exc).__module__.split(".")[0] not in ("requests", "urllib3") and not isinstance(exc, OSError):
                    raise
                raise DiscordError("Discord could not be reached; a write may or may not have arrived") from None
            if reply.status_code == 429 and retry:
                try:
                    wait = float(reply.headers.get("Retry-After", "1"))
                except (TypeError, ValueError):
                    wait = 1.0
                if wait > MAX_WAIT:
                    raise DiscordError("Discord asked for a longer wait than this client allows", 429)
                if tries >= MAX_RETRIES:
                    raise DiscordError("Discord is still limiting after two retries", 429)
                self.sleep(max(wait, 0.0))
                tries += 1
                continue
            if not 200 <= reply.status_code < 300:
                raise DiscordError(f"Discord answered HTTP {reply.status_code}", reply.status_code)
            if reply.status_code == 204 or not getattr(reply, "content", b"x"):
                return None
            return reply.json()

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def put(self, path, **kwargs):
        return self.request("PUT", path, **kwargs)

    def patch(self, path, **kwargs):
        return self.request("PATCH", path, **kwargs)

    def delete(self, path, **kwargs):
        return self.request("DELETE", path, **kwargs)
