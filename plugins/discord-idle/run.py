#!/usr/bin/env python3
# The idle poster moved to plugins/discord/voice/idle/. This runs it, for anything still pointing here.
import runpy
import sys
from pathlib import Path

moved = Path(__file__).resolve().parents[1] / "discord" / "voice" / "idle"
sys.path.insert(0, str(moved))
runpy.run_path(str(moved / "run.py"), run_name="__main__")
