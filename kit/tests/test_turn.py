import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


RUNNER = Path(__file__).resolve().parents[1] / "turn.py"


class ExternalInstructions(unittest.TestCase):
    def test_exact_prompt_and_non_social_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed = root / "seed"
            seed.mkdir()
            rules = "  Sort numeric samples by μ.\r\nEmit only the sorted array.\n"
            (seed / "task.txt").write_bytes(rules.encode())
            (seed / "notes.txt").write_bytes(b"millimetres\n")
            (seed / "seed.json").write_text(json.dumps({"instruction_file": "task.txt",
                                                       "files": {"unit": "notes.txt"}}))
            command = [sys.executable, "-IB", str(RUNNER), "--seed", str(seed)]
            env = {"PATH": os.defpath, "HOME": str(root), "PRIVATE_EXAMPLE": "local"}
            shown = subprocess.run(command + ["--show-prompt"], capture_output=True, env=env, check=True)
            self.assertEqual(shown.stdout, rules.encode())
            model = root / "adapter.py"
            model.write_text("""import json, os, sys
request = json.load(sys.stdin)
expected = json.loads(sys.argv[1])
assert request == expected
assert 'PRIVATE_EXAMPLE' not in os.environ
sys.stdout.write(json.dumps(sorted(request['input'])) + '\\n')
""")
            expected = {"instructions": rules, "input": [13, -1, 7], "files": {"unit": "millimetres\n"}}
            config = root / "config.json"
            config.write_text(json.dumps({"model_command": [sys.executable, "-IB", str(model), json.dumps(expected)]}))
            result = subprocess.run(command + ["--config", str(config)], input=b"[13,-1,7]", capture_output=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b"[-1, 7, 13]\n")
            # A file escaping the selected bundle cannot become provider input.
            (seed / "notes.txt").unlink()
            (seed / "notes.txt").symlink_to(config)
            escaped = subprocess.run(command + ["--config", str(config)], input=b"null", capture_output=True, env=env)
            self.assertNotEqual(escaped.returncode, 0)
            self.assertFalse(escaped.stdout)

    def test_no_ambient_resident_or_automatic_instruction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = [sys.executable, "-IB", str(RUNNER)]
            missing = subprocess.run(args + ["--show-prompt"], env={"HOME": str(root)}, capture_output=True)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn(b"no seed", missing.stderr)
            (root / "seed.json").write_text('{"instructions":""}')
            empty = subprocess.run(args + ["--seed", directory, "--show-prompt"], capture_output=True, check=True)
            self.assertEqual(empty.stdout, b"")


if __name__ == "__main__":
    unittest.main()
