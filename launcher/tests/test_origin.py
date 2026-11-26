"""Checks for the origin: what the launcher demands before it starts anything. Run from the root:

    python -m unittest discover launcher/tests
"""
import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import ruufd  # noqa: E402
from ruufd import origins  # noqa: E402

MANIFEST = """
[agent]
id = "vex"
[[ladder]]
provider = "p"
model = "only"
[providers.p]
[places.origin]
path = "host/origin.toml"
kind = "file"
planned = true
holds = "where it comes from"
[places.logs]
path = "logs"
holds = "output"
[places.launcher]
path = "run"
holds = "launcher state"
[parts.mind]
run = ["true"]
start = "always"
user = "mind"
"""

OPERATOR = {"login": "ada", "behind": "", "root": False, "full": "Ada L", "git_name": "countess",
            "owns_manifest": True, "host": "engine"}
ROOT = {**OPERATOR, "login": "root", "behind": "ada", "root": True, "git_name": "", "owns_manifest": False}
PART = {**OPERATOR, "login": "mind", "full": "", "git_name": "", "owns_manifest": False}
VISITOR = {**OPERATOR, "login": "eve", "full": "", "git_name": "", "owns_manifest": False}

NEEDED = ("operator.name", "instance.purpose")
SAID = {"operator.name": "Countess", "instance.purpose": "Build weekly engine reports."}
CUSTOM_FORM = """
[[origin.questions]]
key = "spark.words"
ask = "What reports should the instance prepare?"
required = true

[[origin.questions]]
key = "project.notes"
ask = "Any formatting preferences?"
required = false
"""


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name).resolve()
        self.manifest = self.root / "ruuf.toml"
        self.manifest.write_text(MANIFEST, encoding="utf-8")
        self.m = ruufd.load(self.manifest, env={})

    def cli(self, *argv, typed=None):
        out = io.StringIO()
        lines = iter(typed or [])

        def read_line(prompt=""):
            try:
                return next(lines)
            except StopIteration:
                raise EOFError from None
        code = ruufd.main(["--manifest", str(self.manifest), *argv], out=out,
                          read_line=read_line if typed is not None else None)
        return code, out.getvalue()

    def say(self):
        self.lines = []
        return lambda text="": self.lines.append(text)

    @property
    def text(self):
        return "\n".join(self.lines)


class Demanding(Case):
    def test_with_no_origin_every_question_is_open_and_nothing_starts(self):
        code, text = self.cli("origin")
        self.assertEqual(code, 1)
        self.assertIn("(not written yet)", text)
        for key in NEEDED:
            self.assertIn(key, text)
        self.assertIn("the operator's to answer and nobody else's", text)
        code, text = self.cli("check")
        self.assertEqual(code, 0)  # the manifest is sound; the origin is a separate demand
        self.assertIn(f"origin: {len(NEEDED)} of {len(NEEDED)} question(s) open", text)
        code, text = self.cli("run", "--only", "mind", "--as-self")
        self.assertEqual(code, 1)
        self.assertIn("refused: this agent has no origin yet", text)
        self.assertFalse((self.root / "logs").exists())  # refused before anything was touched

    def test_a_half_answered_origin_still_starts_nothing(self):
        origins.write(self.m, {k: v for k, v in SAID.items() if k != "instance.purpose"}, by="ada")
        self.assertEqual(origins.read(self.m).open, ["instance.purpose"])
        with self.assertRaises(ruufd.Refused) as caught:
            ruufd.Supervisor(self.m, ["mind"], as_self=True).open()
        self.assertIn("1 question(s)", str(caught.exception))

    def test_an_origin_that_cannot_be_read_is_not_taken_as_given(self):
        where = origins.path(self.m)
        where.parent.mkdir(parents=True)
        where.write_text("this is [not toml", encoding="utf-8")
        origin = origins.read(self.m)
        self.assertFalse(origin.complete)
        self.assertIn("cannot be read", origin.problem)
        self.assertEqual(self.cli("origin")[0], 1)

    def test_the_instance_form_controls_prompts_and_required_fields(self):
        self.manifest.write_text(MANIFEST + CUSTOM_FORM)
        self.m = ruufd.load(self.manifest, env={})
        blank = self.cli("origin", "blank")[1]
        self.assertIn("What reports should the instance prepare?", blank)
        self.assertIn("Any formatting preferences? (optional)", blank)
        self.assertNotIn("[operator]", blank)
        self.assertEqual(origins.read(self.m).open, ["spark.words"])
        self.assertEqual(self.cli("origin", "ask", typed=["Weekly reports", ""])[0], 0)
        self.assertTrue(origins.read(self.m).complete)
        self.assertEqual(origins.read(self.m).answers, {"spark.words": "Weekly reports"})
        self.manifest.write_text(MANIFEST + CUSTOM_FORM.replace("required = true", "required = false"))
        self.assertEqual(self.cli("check")[0], 1)

    def test_without_a_place_the_origin_sits_beside_the_manifest(self):
        self.manifest.write_text(MANIFEST.replace("[places.origin]", "[places.elsewhere]"), encoding="utf-8")
        self.assertEqual(origins.path(ruufd.load(self.manifest, env={})), self.root / "origin.toml")


class Answering(Case):
    def test_the_operator_answers_and_the_words_are_kept_as_said(self):
        typed = [SAID[key] for key in NEEDED]
        code, text = self.cli("origin", "ask", typed=typed)
        self.assertEqual(code, 0, text)
        self.assertIn("the origin is complete", text)
        origin = origins.read(self.m)
        self.assertTrue(origin.complete)
        self.assertEqual(origin.answers["instance.purpose"], SAID["instance.purpose"])
        self.assertEqual(origin.answers, SAID)
        self.assertTrue(origin.given["by"])
        code, text = self.cli("origin")
        self.assertEqual(code, 0, text)
        self.assertIn(f"{len(NEEDED)} of {len(NEEDED)} answered", text)
        for words in SAID.values():  # what was said is never printed back
            self.assertNotIn(words, text)
        self.assertIn("origin: answered, given by", self.cli("check")[1])

    @unittest.skipIf(os.name == "nt", "file modes are a POSIX matter")
    def test_the_file_is_for_its_owner_only(self):
        where = origins.write(self.m, SAID, by="ada")
        self.assertEqual(stat.S_IMODE(os.stat(where).st_mode), 0o600)

    def test_enter_keeps_an_answer_and_a_later_visit_finishes_the_rest(self):
        first = [SAID["operator.name"]]
        code, text = self.cli("origin", "ask", typed=first)
        self.assertEqual(code, 1)
        self.assertIn("still open: instance.purpose", text)
        rest = ["", SAID["instance.purpose"]]
        code, text = self.cli("origin", "ask", typed=rest)
        self.assertEqual(code, 0, text)
        self.assertEqual(origins.read(self.m).answers, SAID)

    def test_nothing_typed_writes_nothing(self):
        code, text = self.cli("origin", "ask", typed=[])
        self.assertEqual(code, 1)
        self.assertIn("nothing was written", text)
        self.assertFalse(origins.path(self.m).exists())

    def test_with_no_terminal_nobody_is_asked(self):
        code = origins.ask(self.m, say=self.say(), facts=OPERATOR, terminal=False)
        self.assertEqual(code, 1)
        self.assertIn("do not answer for them", self.text)
        self.assertFalse(origins.path(self.m).exists())

    def test_the_blank_file_is_the_same_file_with_nothing_answered(self):
        code, text = self.cli("origin", "blank")
        self.assertEqual(code, 0)
        self.assertIn('[instance]\n# What is the intended purpose of this instance?\npurpose = ""', text)
        self.assertNotIn("spark", text)
        where = origins.path(self.m)
        where.parent.mkdir(parents=True)
        where.write_text(text, encoding="utf-8")
        origin = origins.read(self.m)
        self.assertEqual((origin.problem, origin.answers, origin.open), ("", {}, list(NEEDED)))
        where.write_text(text.replace('name = ""', 'name = "Countess"', 1), encoding="utf-8")
        self.assertEqual(origins.read(self.m).answers, {"operator.name": "Countess"})

    def test_editing_a_new_form_preserves_answers_from_a_legacy_form(self):
        where = origins.path(self.m)
        where.parent.mkdir(parents=True)
        where.write_text('[operator]\nname = "Ada"\nabout = "Existing notes"\n[spark]\nwords = "Original"\nwants = "Existing practice"\n')
        original = where.read_bytes()
        self.assertTrue(origins.read(self.m).complete)
        self.assertEqual(origins.read(self.m).answers["instance.purpose"], "Original")
        self.assertNotIn("spark", self.cli("origin")[1])
        self.assertEqual(where.read_bytes(), original)
        self.assertEqual(self.cli("origin", "ask", typed=["", "Updated purpose"])[0], 0)
        self.assertEqual(origins.read(self.m).answers, {"operator.name": "Ada", "operator.about": "Existing notes",
                                                        "instance.purpose": "Updated purpose", "spark.words": "Original",
                                                        "spark.wants": "Existing practice"})
        self.assertNotIn("Existing notes", self.cli("origin")[1])

    def test_awkward_words_survive_the_file(self):
        said = {**SAID, "instance.purpose": 'she said "build" \\ and then:\ttwo lines\nof it — ünïcode'}
        origins.write(self.m, said, by="ada")
        self.assertEqual(origins.read(self.m).answers["instance.purpose"], said["instance.purpose"])


class Context(Case):
    """A possible operator name is offered for confirmation."""

    def test_the_one_who_set_it_up_is_recognised_and_their_public_name_guessed(self):
        seen, guesses = origins.context(self.m, OPERATOR)
        self.assertIn("you are ada on engine", seen[0])
        self.assertEqual(guesses, {"operator.name": "countess"})

    def test_someone_acting_as_root_runs_the_machine(self):
        seen, guesses = origins.context(self.m, ROOT)
        self.assertIn("you are ada on engine, acting as root", seen[0])
        self.assertEqual(set(guesses), {"operator.name"})
        self.assertEqual(guesses["operator.name"], "Ada L")

    def test_bare_root_is_not_given_a_name(self):
        _, guesses = origins.context(self.m, {**ROOT, "behind": "", "full": "root"})
        self.assertNotIn("operator.name", guesses)

    def test_the_agents_own_account_is_not_a_person(self):
        seen, guesses = origins.context(self.m, PART)
        self.assertEqual(guesses, {})
        self.assertIn("a configured part uses this account", "\n".join(seen))

    def test_a_visitor_is_offered_nothing(self):
        seen, guesses = origins.context(self.m, VISITOR)
        self.assertEqual(guesses, {})
        self.assertEqual(seen, ["you are eve on engine"])

    def test_a_guess_is_shown_as_a_guess_and_is_not_an_answer(self):
        code = origins.show(self.m, self.say(), facts=OPERATOR)
        self.assertEqual(code, 1)
        self.assertIn("guess: 'countess', not confirmed", self.text)
        self.assertEqual(origins.read(self.m).answers, {})

    def test_a_guess_becomes_an_answer_only_when_the_operator_accepts_it(self):
        lines = iter(["", *[SAID[key] for key in NEEDED[1:]], ""])
        code = origins.ask(self.m, lambda prompt="": next(lines), self.say(), facts=OPERATOR)
        self.assertEqual(code, 0, self.text)
        origin = origins.read(self.m)
        self.assertEqual(origin.answers["operator.name"], "countess")       # Enter accepted the guess
        self.assertEqual(origin.answers["instance.purpose"], SAID["instance.purpose"])
        self.assertEqual(origin.given["by"], "ada")

    def test_the_real_caller_can_be_looked_at_without_error(self):
        facts = origins.who_is_asking(self.m, env={})
        self.assertEqual(set(facts), set(OPERATOR))
        self.assertEqual(facts["git_name"], "")  # not a clone: the account's own git name is not borrowed
        origins.context(self.m, facts)


if __name__ == "__main__":
    unittest.main()
