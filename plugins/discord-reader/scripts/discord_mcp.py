#!/usr/bin/env python3
# The reader moved to plugins/discord/ears/reader.py. This runs it, for anything still pointing here.
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parents[2] / "discord" / "ears" / "reader.py"), run_name="__main__")
