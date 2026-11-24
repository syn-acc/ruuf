"""Checks for the launcher skeleton. Run from the repository root:

    python -m unittest discover launcher/tests
"""
import io
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
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
        self.assertIn("launcher not running", text)
        self.assertIn("not started", text)
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


RUNNABLE = """
[agent]
id = "vex"
[[ladder]]
provider = "p"
model = "only"
[providers.p]
[places.state]
path = "state"
holds = "what it remembers"
[places.other]
path = "other"
holds = "not this part's business"
[places.logs]
path = "logs"
holds = "output"
[places.launcher]
path = "run"
holds = "launcher state"
[places.jobs]
path = "jobs.sock"
kind = "socket"
holds = "job socket"
"""


class Raw(str):
    """A value written into the manifest as it stands: a table, a list, a boolean."""


def gone(pid):
    """True when a process has ended, including one that ended and has not been collected."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return True


def part(name, code, **fields):
    """A manifest entry for a part that runs a line of Python as the current user's own."""
    lines = [f"[parts.{name}]", f"run = [{sys.executable!r}, '-c', {code!r}]",
             f"user = {fields.pop('user', ruufd.Supervisor._whoami())!r}"]
    fields.setdefault("start", "always")
    for key, value in fields.items():
        lines.append(f"{key} = {value if isinstance(value, (Raw, int)) else repr(value)}")
    return "\n".join(lines) + "\n"


class Running(Case):
    def supervisor(self, parts_text, names=None, **kwargs):
        m = ruufd.load(self.write(RUNNABLE + parts_text), env={})
        s = ruufd.Supervisor(m, names or list(m.parts), **kwargs)
        s.open()
        self.addCleanup(lambda: s.close(grace=2))
        return s

    def until(self, s, done, seconds=10):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            s.step()
            if done():
                return
            time.sleep(0.02)
        self.fail("timed out: " + repr([j.row() for j in s.jobs]))

    def log(self, name):
        return (self.root / "logs" / f"{name}.log").read_text()

    def test_a_part_that_fails_is_started_again_and_its_output_is_kept(self):
        s = self.supervisor(part("mind", "print('thinking'); raise SystemExit(3)", restart="on-failure"))
        job = s.jobs[0]
        self.until(s, lambda: job.runs >= 2)
        text = self.log("mind")
        self.assertIn("thinking", text)
        self.assertIn("exited 3", text)
        self.assertIn("started pid", text)

    def test_a_part_that_ends_cleanly_is_not_restarted_on_failure_only(self):
        s = self.supervisor(part("once", "pass", restart="on-failure"))
        self.until(s, lambda: s.jobs[0].state == "stopped")
        self.assertEqual(s.jobs[0].runs, 1)

    def test_a_part_that_cannot_stay_up_is_left_down(self):
        s = self.supervisor(part("flap", "raise SystemExit(1)", restart="always"))
        self.until(s, lambda: s.jobs[0].state == "failed")
        self.assertEqual(s.jobs[0].runs, ruufd.Supervisor.FLAP[0])
        self.assertIn("left down", self.log("flap"))

    def test_a_timer_runs_again_after_its_gap(self):
        s = self.supervisor(part("tick", "print('tick')", start="timer", every_seconds=1))
        job = s.jobs[0]
        self.until(s, lambda: job.runs == 1 and job.state == "waiting")
        self.assertGreater(job.due, time.time())
        self.until(s, lambda: job.runs >= 2)

    def test_a_part_over_its_time_limit_is_killed(self):
        s = self.supervisor(part("slow", "import time; time.sleep(60)", limits=Raw("{ runtime_seconds = 1 }")))
        self.until(s, lambda: s.jobs[0].last_exit is not None)
        self.assertEqual(s.jobs[0].last_exit, -signal.SIGKILL)
        self.assertIn("over its 1s limit", self.log("slow"))

    def test_a_part_is_told_only_the_places_it_declared(self):
        code = "import os; print(sorted(k for k in os.environ if k.startswith('RUUF_')), os.environ['RUUF_PLACE_STATE'])"
        s = self.supervisor(part("mind", code, writes=Raw("['state']")))
        self.until(s, lambda: s.jobs[0].state == "stopped")
        text = self.log("mind")
        self.assertIn("'RUUF_MANIFEST', 'RUUF_PART', 'RUUF_PLACE_STATE']", text)
        self.assertIn(str(self.root / "state"), text)
        self.assertNotIn("RUUF_PLACE_OTHER", text)

    def test_closing_stops_what_is_running_and_everything_it_spawned(self):
        code = ("import subprocess, sys, time; "
                "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                "print(c.pid, flush=True); time.sleep(60)")
        s = self.supervisor(part("mind", code))
        self.until(s, lambda: "started pid" in self.log("mind") and len(self.log("mind").splitlines()) >= 2)
        child = int(self.log("mind").splitlines()[1])
        state = ruufd.read_state(s.machine)
        self.assertEqual((state["alive"], state["parts"][0]["state"]), (True, "running"))
        s.close(grace=5)
        self.assertEqual(s.jobs[0].state, "stopped")
        self.assertIsNone(ruufd.read_state(s.machine))
        time.sleep(0.2)
        self.assertTrue(gone(child))

    def refusal(self, parts_text, names=None, **kwargs):
        m = ruufd.load(self.write(RUNNABLE + parts_text), env={})
        with self.assertRaises(ruufd.Refused) as caught:
            ruufd.Supervisor(m, names or list(m.parts), **kwargs)
        return str(caught.exception)

    def test_a_part_a_unit_still_runs_is_refused(self):
        self.assertIn("still run by mind.service", self.refusal(part("mind", "pass", unit="mind.service")))

    def test_a_sandbox_is_refused_for_now(self):
        text = part("box", "pass", start="socket", socket="jobs", user="box", sandbox=Raw("true"),
                    limits=Raw("{ network = false }"))
        self.assertIn("cannot yet apply their limits", self.refusal(text))

    @unittest.skipIf(os.geteuid() == 0, "root may start a part as anyone")
    def test_another_users_part_is_refused_unless_as_self(self):
        text = part("mind", "pass", user="somebody-else")
        self.assertIn("only root can start a part as someone else", self.refusal(text))
        m = ruufd.load(self.write(RUNNABLE + text), env={})
        ruufd.Supervisor(m, ["mind"], as_self=True)  # does not raise

    @unittest.skipUnless(os.geteuid() == 0, "only root can start a part as someone else")
    def test_root_starts_a_part_as_its_own_user(self):
        import pwd
        nobody = pwd.getpwnam("nobody")
        # /bin/sh, not this interpreter: the test's own Python may live where `nobody` cannot reach.
        entry = "[parts.low]\nrun = ['/bin/sh', '-c', 'echo uid $(id -u) $USER']\nstart = 'always'\nuser = 'nobody'\n"
        s = self.supervisor(entry)
        self.until(s, lambda: s.jobs[0].state in ("stopped", "failed"))
        text = self.log("low")
        self.assertIn(f"uid {nobody.pw_uid} nobody", text)
        self.assertIn("as nobody", text)

    def test_owned_leaves_out_units_and_sandboxes(self):
        text = (part("mine", "pass") + part("theirs", "pass", unit="x.service")
                + part("box", "pass", start="socket", socket="jobs", user="box", sandbox=Raw("true"),
                       limits=Raw("{ network = false }")))
        m = ruufd.load(self.write(RUNNABLE + text), env={})
        self.assertEqual(ruufd.owned(m), ["mine"])

    def test_next_daily_is_today_if_still_ahead_and_tomorrow_if_not(self):
        noon = 1_800_000_000 - 1_800_000_000 % 86400 + 12 * 3600
        self.assertEqual(ruufd.next_daily("13:30", noon), noon + 5400)
        self.assertEqual(ruufd.next_daily("06:20", noon), noon - 12 * 3600 + 86400 + 6 * 3600 + 1200)

    def test_run_status_and_stop_from_the_command_line(self):
        manifest = self.write(RUNNABLE + part("mind", "import time; time.sleep(60)"))
        launcher = [sys.executable, str(HERE.parent / "ruufd.py"), "--manifest", str(manifest)]
        proc = subprocess.Popen([*launcher, "run", "--all"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.kill)
        m = ruufd.load(manifest, env={})
        end = time.monotonic() + 10
        while time.monotonic() < end:
            state = ruufd.read_state(m)
            if state and state["parts"][0]["state"] == "running":
                break
            time.sleep(0.05)
        else:
            self.fail("the launcher never reported a running part")
        code, text = self.cli("status")
        self.assertIn("running pid", text)
        self.assertIn(f"launcher running, pid {proc.pid}", text)
        again = subprocess.run([*launcher, "run", "--all"], capture_output=True, text=True)
        self.assertIn("already running", again.stdout)
        code, text = self.cli("stop")
        self.assertEqual(code, 0, text)
        self.assertEqual(proc.wait(timeout=10), 0)
        self.assertTrue(gone(state["parts"][0]["pid"]))


class Unseen(Case):
    def test_what_this_user_may_not_look_at_is_not_called_missing(self):
        m = ruufd.load(self.write(GOOD.replace('"python3", "{state}/mind.py"', '"/locked/python", "x"')), env={})
        real = os.stat

        def stat_(path, *a, **k):
            if str(path).startswith("/locked") or str(path).endswith("login"):
                raise PermissionError(13, "Permission denied")
            return real(path, *a, **k)
        ruufd.os.stat = stat_
        self.addCleanup(setattr, ruufd.os, "stat", real)
        unseen = []
        found = ruufd.check_host(m, user_exists=lambda name: True, unseen=unseen)
        self.assertFalse(any("/locked/python" in f or "login" in f for f in found), found)
        self.assertTrue(any("/locked/python (it runs as mind)" in u for u in unseen), unseen)
        self.assertTrue(any(u.startswith("place login") for u in unseen), unseen)


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

    def test_each_part_declares_every_place_its_code_asks_for(self):
        repo = HERE.parent.parent
        code = {"soft-machine": ["ruu-lab/soft_machine.py"],
                "idle-poster": ["plugins/discord-idle/run.py", "plugins/discord-idle/generate.py"],
                "frontier-scan": ["ruu-lab/frontier_scan.py"]}
        for name, files in code.items():
            text = "".join((repo / f).read_text(encoding="utf-8") for f in files)
            asked = {n.lower() for n in re.findall(r'RUUF_PLACE_([A-Z_]+)"', text)}
            asked |= set(re.findall(r'\bplace\("([a-z_]+)"', text))
            self.assertTrue(asked, name)  # the code does take its places from the launcher
            part = self.m.parts[name]
            self.assertLessEqual(asked, {*part.reads, *part.writes}, name)

    def test_nothing_in_it_is_the_launchers_to_start_yet(self):
        self.assertEqual(ruufd.owned(self.m), [])  # every part still names its unit
        with self.assertRaises(ruufd.Refused):
            ruufd.Supervisor(self.m, ["soft-machine"], as_self=True)

    def test_the_server_paths_can_be_moved_with_one_var(self):
        moved = ruufd.load(REPO_MANIFEST, env={"RUUF_VAR_HOME": "/tmp/scratch"})
        self.assertEqual(moved.where("state"), Path("/tmp/scratch/.hermes/ruu-lab"))
        self.assertEqual(moved.where("hands_socket"), Path("/run/ruu-hands.sock"))


if __name__ == "__main__":
    unittest.main()
