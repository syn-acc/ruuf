"""Checks for the shipping routine, against throwaway repositories. Run from the repository root:

    python -m unittest discover routines/tests
"""
import getpass
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import ship  # noqa: E402

MANIFEST = """
[agent]
id = "vex"
[[ladder]]
provider = "p"
model = "only"
[providers.p]
[ship]
remote = ["sh", "-c"]
proofs = "{proofs}"
users = ["{user}"]
checks = [["sh", "-c", "test -f works"]]
max_ahead = 2
keep = 2
"""


@unittest.skipUnless(shutil.which("git"), "needs git")
class Case(unittest.TestCase):
    def setUp(self):
        mask = os.umask(0o022)
        self.addCleanup(os.umask, mask)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name).resolve()
        self.proofs = root / "proofs"
        self.proofs.mkdir()
        self.sh("git", "init", "-q", "--bare", "-b", "main", str(root / "origin.git"), cwd=root)
        self.path = root / "clone"
        self.sh("git", "init", "-q", "-b", "main", str(self.path), cwd=root)
        for key, value in (("user.name", "vex"), ("user.email", "vex@example.invalid"), ("commit.gpgsign", "false")):
            self.sh("git", "config", key, value)
        self.sh("git", "remote", "add", "origin", str(root / "origin.git"))
        manifest = MANIFEST.replace("{proofs}", str(self.proofs)).replace("{user}", getpass.getuser())
        self.commit("start", **{"ruuf.toml": manifest})
        self.sh("git", "push", "-q", "origin", "main")
        self.sh("git", "checkout", "-q", "-b", "work")
        self.repo = ship.Repo(self.path)
        self.words("")

    def sh(self, *args, cwd=None, **kwargs):
        return subprocess.run(args, cwd=cwd or self.path, check=True, capture_output=True, text=True, **kwargs).stdout

    def commit(self, message, author=None, **files):
        for name, text in files.items():
            (self.path / name).write_text(text, encoding="utf-8")
        self.sh("git", "add", "-A")
        extra = ["--author", author] if author else []
        self.sh("git", "commit", "-q", "-m", message, *extra)

    def words(self, text):
        folder = self.repo.private()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "private-words").write_text(text, encoding="utf-8")

    def run_ship(self, *argv):
        out = io.StringIO()
        code = ship.main(["--repo", str(self.path), *argv], out=out)
        return code, out.getvalue()

    def origin_main(self):
        return self.sh("git", "ls-remote", "origin", "refs/heads/main").split()[0]


class Shipping(Case):
    def test_clone_policy_proves_a_tree_without_an_instance_and_binds_the_receipt(self):
        config = self.repo.private() / "ship.toml"
        policy = "[ship]" + (self.path / "ruuf.toml").read_text().split("[ship]", 1)[1]
        config.write_text(policy)
        config.chmod(0o600)
        (self.path / "ruuf.toml").unlink()
        self.commit("generic framework", works="yes")
        self.assertEqual(self.run_ship("prove", "work")[0], 0)
        config.write_text(policy.replace("test -f works", "test -s works"))
        code, why = self.run_ship("land", "work")
        self.assertEqual(code, 1)
        self.assertIn("proof settings changed", why)
        self.assertEqual(self.run_ship("prove", "work")[0], 0)
        self.assertEqual(self.run_ship("land", "work")[0], 0)

    def test_explicit_config_runs_its_own_checks_and_cannot_point_into_a_worktree(self):
        config = self.proofs.parent / "operator.toml"
        manifest = (self.path / "ruuf.toml").read_text()
        config.write_text("[ship]" + manifest.split("[ship]", 1)[1].replace("test -f works", "test -f independent"))
        config.chmod(0o600)
        self.commit("candidate", works="yes")
        code, why = self.run_ship("--ship-config", str(config), "prove", "work")
        self.assertEqual(code, 1)
        self.assertIn("test -f independent", why)
        code, why = self.run_ship("--ship-config", str(self.path / "ruuf.toml"), "prove", "work")
        self.assertEqual(code, 1)
        self.assertIn("outside candidate worktrees", why)
        linked = self.proofs.parent / 'linked "tree"\nsecond line'
        self.sh("git", "worktree", "add", "--detach", str(linked), "work")
        code, why = self.run_ship("--ship-config", str(linked / "ruuf.toml"), "prove", "work")
        self.assertEqual(code, 1)
        self.assertIn("outside candidate worktrees", why)

    def test_a_proven_branch_lands_as_a_fast_forward(self):
        self.commit("make it work", works="yes")
        code, text = self.run_ship("prove", "work")
        self.assertEqual(code, 0, text)
        self.assertIn("land it now", text)
        tip = self.repo.tip("work")
        self.assertTrue((self.proofs / self.repo.tree(tip) / "works").is_file())
        code, text = self.run_ship("land", "work")
        self.assertEqual(code, 0, text)
        self.assertEqual(self.origin_main(), tip)
        self.assertEqual(self.repo.tip("main"), tip)
        self.assertEqual(self.run_ship("status")[0], 0)

    def test_an_unproven_branch_is_refused(self):
        self.commit("make it work", works="yes")
        before = self.origin_main()
        code, text = self.run_ship("land", "work")
        self.assertEqual(code, 1)
        self.assertIn("no receipt", text)
        self.assertEqual(self.origin_main(), before)

    def test_a_failed_proof_is_refused_and_says_which_check(self):
        self.commit("does not work", other="x")
        code, text = self.run_ship("prove", "work")
        self.assertEqual(code, 1)
        self.assertIn("FAIL", text)
        code, text = self.run_ship("land", "work")
        self.assertEqual(code, 1)
        self.assertIn("failed", text)
        self.assertIn("test -f works", text)

    def test_a_proof_is_for_one_tree_only(self):
        self.commit("make it work", works="yes")
        self.run_ship("prove", "work")
        self.commit("one more thing", more="x")
        code, text = self.run_ship("land", "work")
        self.assertEqual(code, 1)
        self.assertIn("no receipt", text)

    def test_every_reason_is_given_at_once(self):
        self.commit("by someone else\n\nCo-Authored-By: Other <o@example.invalid>", author="Other <o@example.invalid>",
                    works="the secretname was here")
        self.words("SecretName\n")
        self.run_ship("prove", "work")
        code, text = self.run_ship("land", "work")
        self.assertEqual(code, 1)
        for expected in ("not authored and committed by vex", "carries a trailer", "1 word(s) from the private list"):
            self.assertIn(expected, text)
        self.assertNotIn("secretname", text.lower())  # the refusal does not repeat the word

    def test_no_private_list_is_a_refusal_not_a_pass(self):
        self.commit("make it work", works="yes")
        self.run_ship("prove", "work")
        (self.repo.private() / "private-words").unlink()
        code, text = self.run_ship("land", "work")
        self.assertEqual(code, 1)
        self.assertIn("no private list", text)

    def test_main_that_moved_on_is_refused(self):
        self.commit("make it work", works="yes")
        self.run_ship("prove", "work")
        self.sh("git", "checkout", "-q", "main")
        self.commit("meanwhile", meanwhile="x")
        self.sh("git", "checkout", "-q", "work")
        code, text = self.run_ship("land", "work")
        self.assertEqual(code, 1)
        self.assertIn("not be a fast-forward", text)

    def test_main_itself_cannot_be_shipped(self):
        self.assertEqual(self.run_ship("land", "main")[0], 1)

    def test_status_fails_while_a_proven_branch_waits_or_a_branch_drifts(self):
        self.assertEqual(self.run_ship("status")[0], 0)
        self.commit("one", works="1")
        code, text = self.run_ship("status")
        self.assertEqual(code, 0, text)
        self.assertIn("not proven", text)
        self.run_ship("prove", "work")
        code, text = self.run_ship("status")
        self.assertEqual(code, 1)
        self.assertIn("land it now", text)
        self.commit("two", works="2")
        self.commit("three", works="3")
        code, text = self.run_ship("status")
        self.assertEqual(code, 1)
        self.assertIn("more than 2 ahead", text)

    def test_old_proofs_are_cleared_from_the_machine(self):
        for n in range(4):
            self.commit(f"step {n}", works=str(n))
            self.run_ship("prove", "work")
        self.assertEqual(len(list(self.proofs.iterdir())), 2)


if __name__ == "__main__":
    unittest.main()
