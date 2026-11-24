# The idle poster's generator moved to plugins/discord/voice/idle/generate.py. This hands on
# everything it defines, for anything still importing it from here.
import importlib.util
import sys
from pathlib import Path

_moved = Path(__file__).resolve().parents[1] / "discord" / "voice" / "idle"
sys.path.insert(0, str(_moved))
_spec = importlib.util.spec_from_file_location("discord_voice_idle_generate", _moved / "generate.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
globals().update({name: value for name, value in vars(_module).items() if not name.startswith("__")})
