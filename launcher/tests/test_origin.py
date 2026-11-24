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

SAID = {"operator.name": "Countess", "operator.about": "I build engines and will not have guesswork.",
        "relation.you_are": "the one who made it", "relation.your_word": "an order; everyone else is advice",
        "world.name": "the workshop", "world.about": "people who build engines",
        "spark.words": "i wanted something in the room that keeps building when we stop",
        "spark.wants": "building the next small thing"}


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
        for key in origins.NEEDED:
            self.assertIn(key, text)
        self.assertIn("the operator's to answer and nobody else's", text)
        code, text = self.cli("check")
        self.assertEqual(code, 0)  # the manifest is sound; the origin is a separate demand
        self.assertIn(f"origin: {len(origins.NEEDED)} of {len(origins.NEEDED)} question(s) open", text)
        code, text = self.cli("run", "--only", "mind", "--as-self")
        self.assertEqual(code, 1)
        self.assertIn("refused: this agent has no origin yet", text)
        self.assertFalse((self.root / "logs").exists())  # refused before anything was touched

    def test_a_half_answered_origin_still_starts_nothing(self):
        origins.write(self.m, {k: v for k, v in SAID.items() if not k.startswith("spark.")}, by="ada")
        self.assertEqual(origins.read(self.m).open, ["spark.words", "spark.wants"])
        with self.assertRaises(ruufd.Refused) as caught:
            ruufd.Supervisor(self.m, ["mind"], as_self=True).open()
        self.assertIn("2 question(s)", str(caught.exception))

    def test_an_origin_that_cannot_be_read_is_not_taken_as_given(self):
        where = origins.path(self.m)
        where.parent.mkdir(parents=True)
        where.write_text("this is [not toml", encoding="utf-8")
        origin = origins.read(self.m)
        self.assertFalse(origin.complete)
        self.assertIn("cannot be read", origin.problem)
        self.assertEqual(self.cli("origin")[0], 1)

    def test_the_optional_question_is_not_demanded(self):
        origins.write(self.m, SAID, by="ada")
        self.assertTrue(origins.read(self.m).complete)
        self.assertNotIn("spark.sources", origins.read(self.m).answers)

    def test_without_a_place_the_origin_sits_beside_the_manifest(self):
        self.manifest.write_text(MANIFEST.replace("[places.origin]", "[places.elsewhere]"), encoding="utf-8")
        self.assertEqual(origins.path(ruufd.load(self.manifest, env={})), self.root / "origin.toml")


class Answering(Case):
    def test_the_operator_answers_and_the_words_are_kept_as_said(self):
        typed = [SAID[key] for key in origins.NEEDED] + ['old manuals; "the difference engine"; a cold room']
        code, text = self.cli("origin", "ask", typed=typed)
        self.assertEqual(code, 0, text)
        self.assertIn("the origin is complete", text)
        origin = origins.read(self.m)
        self.assertTrue(origin.complete)
        self.assertEqual(origin.answers["spark.words"], SAID["spark.words"])
        self.assertEqual(origin.answers["spark.sources"], typed[-1])
        self.assertTrue(origin.given["by"])
        code, text = self.cli("origin")
        self.assertEqual(code, 0, text)
        self.assertIn(f"{len(origins.NEEDED)} of {len(origins.NEEDED)} answered", text)
        for words in SAID.values():  # what was said is never printed back
            self.assertNotIn(words, text)
        self.assertIn("origin: answered, given by", self.cli("check")[1])

    @unittest.skipIf(os.name == "nt", "file modes are a POSIX matter")
    def test_the_file_is_for_its_owner_only(self):
        where = origins.write(self.m, SAID, by="ada")
        self.assertEqual(stat.S_IMODE(os.stat(where).st_mode), 0o600)

    def test_enter_keeps_an_answer_and_a_later_visit_finishes_the_rest(self):
        first = [SAID[key] for key in origins.NEEDED[:3]]
        code, text = self.cli("origin", "ask", typed=first)
        self.assertEqual(code, 1)
        self.assertIn("still open: relation.your_word", text)
        rest = ["", "", ""] + [SAID[key] for key in origins.NEEDED[3:]] + [""]
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
        where = origins.path(self.m)
        where.parent.mkdir(parents=True)
        where.write_text(text, encoding="utf-8")
        origin = origins.read(self.m)
        self.assertEqual((origin.problem, origin.answers, origin.open), ("", {}, list(origins.NEEDED)))
        where.write_text(text.replace('name = ""', 'name = "Countess"', 1), encoding="utf-8")
        self.assertEqual(origins.read(self.m).answers, {"operator.name": "Countess"})

    def test_awkward_words_survive_the_file(self):
        said = {**SAID, "spark.words": 'she said "build" \\ and then:\ttwo lines\nof it — ünïcode'}
        origins.write(self.m, said, by="ada")
        self.assertEqual(origins.read(self.m).answers["spark.words"], said["spark.words"])


class Context(Case):
    """The launcher works out who is asking and what they are likely to be to the agent."""

    def test_the_one_who_set_it_up_is_recognised_and_their_public_name_guessed(self):
        seen, guesses = origins.context(self.m, OPERATOR)
        self.assertIn("you are ada on engine", seen[0])
        self.assertEqual(guesses, {"relation.you_are": "the one who set it up", "operator.name": "countess"})

    def test_someone_acting_as_root_runs_the_machine(self):
        seen, guesses = origins.context(self.m, ROOT)
        self.assertIn("you are ada on engine, acting as root", seen[0])
        self.assertEqual(guesses["relation.you_are"], "the one who runs it")
        self.assertEqual(guesses["operator.name"], "Ada L")

    def test_bare_root_is_not_given_a_name(self):
        _, guesses = origins.context(self.m, {**ROOT, "behind": "", "full": ""})
        self.assertNotIn("operator.name", guesses)

    def test_the_agents_own_account_is_not_a_person(self):
        seen, guesses = origins.context(self.m, PART)
        self.assertEqual(guesses, {})
        self.assertIn("the agent's own account, not a person", "\n".join(seen))

    def test_a_visitor_is_offered_nothing(self):
        seen, guesses = origins.context(self.m, VISITOR)
        self.assertEqual(guesses, {})
        self.assertIn("you look like a visitor", "\n".join(seen))

    def test_a_guess_is_shown_as_a_guess_and_is_not_an_answer(self):
        code = origins.show(self.m, self.say(), facts=OPERATOR)
        self.assertEqual(code, 1)
        self.assertIn("guess: 'countess', not confirmed", self.text)
        self.assertEqual(origins.read(self.m).answers, {})

    def test_a_guess_becomes_an_answer_only_when_the_operator_accepts_it(self):
        lines = iter(["", *[SAID[key] for key in origins.NEEDED[1:]], ""])
        code = origins.ask(self.m, lambda prompt="": next(lines), self.say(), facts=OPERATOR)
        self.assertEqual(code, 0, self.text)
        origin = origins.read(self.m)
        self.assertEqual(origin.answers["operator.name"], "countess")       # Enter accepted the guess
        self.assertEqual(origin.answers["relation.you_are"], SAID["relation.you_are"])  # typed over the guess
        self.assertEqual(origin.given["by"], "ada")

    def test_the_real_caller_can_be_looked_at_without_error(self):
        facts = origins.who_is_asking(self.m, env={})
        self.assertEqual(set(facts), set(OPERATOR))
        self.assertEqual(facts["git_name"], "")  # not a clone: the account's own git name is not borrowed
        origins.context(self.m, facts)


class TheRepositoryManifest(unittest.TestCase):
    def setUp(self):
        self.m = ruufd.load(HERE.parent.parent / "ruuf.toml", env={})

    def test_it_names_where_the_origin_lives_on_the_host_not_in_the_repository(self):
        where = origins.path(self.m)
        self.assertEqual(where, self.m.where("origin"))
        self.assertNotIn(HERE.parent.parent, where.parents)

    def test_no_part_reads_the_origin_yet(self):
        for part in self.m.parts.values():
            self.assertNotIn("origin", (*part.reads, *part.writes), part.name)


if __name__ == "__main__":
    unittest.main()
