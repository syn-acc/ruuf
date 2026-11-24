"""Checks for the launcher skeleton. Run from the repository root:

    python -m unittest discover launcher/tests
"""
import io
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import ruufd  # noqa: E402

REPO_MANIFEST = HERE.parent.parent / "ruuf.toml"

GOOD = """
[agent]
id = "vex"
[vars]
home = "{root}/home"
[[ladder]]
provider = "p"
model = "big"
[[ladder]]
provider = "p"
model = "small"
[providers.p]
home = "{login}"
[places.state]
path = "{home}/state"
holds = "what it remembers"
[places.login]
path = "{home}/login"
secret = true
holds = "the login"
[places.jobs]
path = "{home}/jobs.sock"
kind = "socket"
holds = "job socket"
[places.later]
path = "later"
planned = true
holds = "not yet"
[parts.mind]
run = ["python3", "{state}/mind.py"]
start = "always"
user = "mind"
reads = ["login"]
writes = ["state"]
[parts.tick]
run = ["python3", "tick.py"]
start = "timer"
every_seconds = 60
user = "mind"
[parts.box]
run = ["python3", "box.py"]
start = "socket"
socket = "jobs"
user = "box"
sandbox = true
limits = { network = false }
"""


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name).resolve()

    def write(self, text):
        path = self.root / "ruuf.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def problems(self, text):
        with self.assertRaises(ruufd.ManifestError) as caught:
            ruufd.load(self.write(text), env={})
        return caught.exception.problems

    def cli(self, *argv):
        out = io.StringIO()
        code = ruufd.main(["--manifest", str(self.root / "ruuf.toml"), *argv], out=out)
        return code, out.getvalue()


class Loading(Case):
    def test_good_manifest_builds_the_map_and_the_table(self):
        m = ruufd.load(self.write(GOOD), env={})
        self.assertEqual(m.where("state"), self.root / "home" / "state")
        self.assertEqual(m.where("later"), self.root / "later")  # relative paths hang from the manifest
        self.assertEqual([r["model"] for r in m.ladder], ["big", "small"])
        self.assertEqual(m.parts["mind"].run, ("python3", str(self.root / "home" / "state" / "mind.py")))
        self.assertEqual(m.providers["p"]["home"], str(self.root / "home" / "login"))
        self.assertEqual(ruufd.describe_start(m.parts["tick"]), "every 60s")
        self.assertEqual(ruufd.describe_start(m.parts["box"]), "per job on jobs")

    def test_a_var_can_be_overridden_from_the_environment(self):
        m = ruufd.load(self.write(GOOD), env={"RUUF_VAR_HOME": "/elsewhere"})
        self.assertEqual(m.where("state"), Path("/elsewhere/state"))

    def test_unknown_place_is_named_with_the_known_ones(self):
        m = ruufd.load(self.write(GOOD), env={})
        with self.assertRaises(KeyError) as caught:
            m.where("nowhere")
        self.assertIn("state", caught.exception.args[0])

    def test_the_agents_map_leaves_out_secret_places(self):
        safe = ruufd.load(self.write(GOOD), env={}).safe_map()
        self.assertIn("state", safe)
        self.assertNotIn("login", safe)
        self.assertNotIn("path", safe["state"])  # it learns what exists, not where on disk

    def test_every_problem_is_reported_at_once(self):
        found = self.problems("[agent]\nid = 'x'\n[parts.a]\nrun = []\nstart = 'sometimes'\n")
        text = "\n".join(found)
        for expected in ("ladder: required", "parts.a.run", "parts.a.start", "parts.a.user"):
            self.assertIn(expected, text)

    def test_ladder_needs_a_known_provider(self):
        found = self.problems(GOOD.replace('provider = "p"\nmodel = "small"', 'provider = "q"\nmodel = "small"'))
        self.assertTrue(any("no provider called 'q'" in p for p in found), found)

    def test_unknown_slot_is_a_problem(self):
        found = self.problems(GOOD.replace("{state}/mind.py", "{nope}/mind.py"))
        self.assertTrue(any("{nope}" in p for p in found), found)

    def test_timer_needs_exactly_one_schedule(self):
        found = self.problems(GOOD.replace("every_seconds = 60", 'every_seconds = 60\ndaily_at = "06:20"'))
        self.assertTrue(any("parts.tick" in p for p in found), found)
        found = self.problems(GOOD.replace("every_seconds = 60\n", ""))
        self.assertTrue(any("parts.tick" in p for p in found), found)

    def test_socket_part_needs_a_socket_place(self):
        found = self.problems(GOOD.replace('socket = "jobs"', 'socket = "state"'))
        self.assertTrue(any("parts.box.socket" in p for p in found), found)


class Sandboxes(Case):
    def test_a_sandbox_must_have_no_network(self):
        found = self.problems(GOOD.replace("limits = { network = false }", "limits = { network = true }"))
        self.assertTrue(any("limits.network = false" in p for p in found), found)

    def test_a_sandbox_may_not_touch_a_secret_place(self):
        found = self.problems(GOOD.replace("sandbox = true", 'sandbox = true\nreads = ["login"]'))
        self.assertTrue(any("secret place 'login'" in p for p in found), found)

    def test_a_sandbox_may_not_share_a_user_with_a_part_that_reads_secrets(self):
        found = self.problems(GOOD.replace('user = "box"', 'user = "mind"'))
        self.assertTrue(any("same user as a part that reads secrets" in p for p in found), found)


class Host(Case):
    def test_host_check_reports_what_is_missing_and_skips_planned_places(self):
        m = ruufd.load(self.write(GOOD), env={})
        (self.root / "home" / "state").mkdir(parents=True)
        (self.root / "home" / "login").write_text("x")  # a file where a folder should be
        found = ruufd.check_host(m, user_exists=lambda name: name == "mind")
        text = "\n".join(found)
        self.assertNotIn("place state", text)
        self.assertNotIn("later", text)
        self.assertIn("place login", text)
        self.assertIn("is not a dir", text)
        self.assertIn("place jobs: nothing at", text)
        self.assertIn("part box: no user called box", text)
        self.assertNotIn("part mind: no user", text)


class CommandLine(Case):
    def test_check_where_status_and_map(self):
        self.write(GOOD)
        code, text = self.cli("check")
        self.assertEqual(code, 0)
        self.assertIn("agent vex, 2 rung(s), 4 places, 3 parts", text)
        code, text = self.cli("where", "state")
        self.assertEqual((code, text.strip()), (0, str(self.root / "home" / "state")))
        code, text = self.cli("where", "nowhere")
        self.assertEqual(code, 1)
        code, text = self.cli("status")
        self.assertIn("does not start processes yet", text)
        self.assertIn("box (sandbox)", text)
        code, text = self.cli("map")
        self.assertNotIn("login", text)

    def test_a_broken_manifest_exits_1_and_lists_the_problems(self):
        self.write("[agent]\nid = 'x'\n")
        code, text = self.cli("check")
        self.assertEqual(code, 1)
        self.assertIn("ladder: required", text)

    def test_no_manifest_exits_2(self):
        code, text = self.cli("check")
        self.assertEqual(code, 2)


class TheRepositoryManifest(unittest.TestCase):
    """ruuf.toml describes the deployment. It has to stay sound and keep saying what the units say."""

    def setUp(self):
        self.m = ruufd.load(REPO_MANIFEST, env={})

    def test_it_loads_with_a_ladder_best_first(self):
        self.assertEqual(self.m.agent["id"], "ruu")
        self.assertGreaterEqual(len(self.m.ladder), 1)

    def test_every_part_names_a_unit_that_is_in_the_repository(self):
        units = HERE.parent.parent / "deploy" / "units"
        for part in self.m.parts.values():
            self.assertTrue((units / part.unit).is_file(), f"{part.name}: {part.unit}")

    def test_commands_match_the_units(self):
        units = HERE.parent.parent / "deploy" / "units"
        service = {"soft-machine": "ruu-soft-machine.service", "idle-poster": "discord-idle.service",
                   "frontier-scan": "ruu-frontier-scan.service", "hands": "ruu-hands@.service",
                   "score": "ruu-score@.service", "child-reap": "ruu-child-reap.service",
                   "gateway": "bot-syn-gl.service"}
        self.assertEqual(set(service), set(self.m.parts))
        for name, unit in service.items():
            lines = (units / unit).read_text(encoding="utf-8").splitlines()
            command = next(line.split("=", 1)[1] for line in lines if line.startswith("ExecStart="))
            user = next(line.split("=", 1)[1] for line in lines if line.startswith("User="))
            self.assertEqual(" ".join(self.m.parts[name].run), command, name)
            self.assertEqual(self.m.parts[name].user, user, name)

    def test_the_server_paths_can_be_moved_with_one_var(self):
        moved = ruufd.load(REPO_MANIFEST, env={"RUUF_VAR_HOME": "/tmp/scratch"})
        self.assertEqual(moved.where("state"), Path("/tmp/scratch/.hermes/ruu-lab"))
        self.assertEqual(moved.where("hands_socket"), Path("/run/ruu-hands.sock"))


if __name__ == "__main__":
    unittest.main()
