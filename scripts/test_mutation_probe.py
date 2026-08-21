#!/usr/bin/env python3
"""The probe must never score a run that did not happen.

Two halves, per `CLAUDE.md`'s "A check that reads nothing must not report
clean".

**FLOOR.** `parse_unittest_run` is asserted against a real transcript with a
named known member and a numeric minimum, and the floor is proven by forcing
the input empty and watching the assertion redden.

**KNOWN-BAD, in the form each surface allows.** `parse_unittest_run` is HANDED
its input, so its known-bad is a crafted INPUT: a verbatim
`unittest.loader._FailedTest` transcript, which is the exact text that was
twice scored as a kill. `probe()` executes a real subprocess against a real
suite, so its known-bads are fixture DIRECTORIES driven through the real path,
never hand-built `UnittestRun` objects: a pure-function test cannot notice that
the subprocess invocation itself is wrong, and that blindness is the whole
point of the tool.

The headline case is `test_a_baseline_that_names_a_missing_class_is_NO_RUN`,
which reproduces the original incident literally: a suite whose `defaultTest`
names a class that does not exist runs `Ran 1 test` and reports an ERROR, and
every part of that reads like a red suite from the outside.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mutation_probe as mp  # noqa: E402

_REAL_JOURNAL = mp.JOURNAL_DIR
_JOURNAL_SANDBOX: tempfile.TemporaryDirectory[str] | None = None


def setUpModule() -> None:
    """No test in this file may touch the REAL recovery journal.

    The journal is process-global state shared between the tool and its own
    suite, and it cost a `verify: FAIL`. Killing a verify mid-run killed a probe
    inside THIS file; that left a journal entry naming the killed test's temp
    fixture; the next full run's `recover_abandoned` found it, restored it, and
    printed a REFUSED line into eight unrelated tests. Nothing was wrong with
    the branch under test in any of the eight.

    Module scope rather than per-class, because the hazard is any probe that
    omits `journal_dir=`, and there are more than thirty of those. Isolating at
    the seam is a property of the file; isolating at the call site is thirty
    chances to forget.
    """
    global _JOURNAL_SANDBOX
    _JOURNAL_SANDBOX = tempfile.TemporaryDirectory(prefix="probe-journal-")
    mp.JOURNAL_DIR = Path(_JOURNAL_SANDBOX.name)


def tearDownModule() -> None:
    mp.JOURNAL_DIR = _REAL_JOURNAL
    if _JOURNAL_SANDBOX is not None:
        _JOURNAL_SANDBOX.cleanup()


class ProbeTestCase(unittest.TestCase):
    """Every test in this file starts with an EMPTY journal.

    `setUpModule` alone is not enough and the difference is not pedantic: the
    fault-injection classes below deliberately leave journal entries behind, to
    prove a failed restore is loud. A module-scoped sandbox carries those into
    the next test, which then reports `REFUSED` about damage it did not do. Two
    tests failed exactly that way, which is the ORIGINAL defect one scope
    smaller: shared mutable state between things that should not see each other.

    Per-test, so the isolation cannot depend on execution order.
    """

    def setUp(self) -> None:
        super().setUp()
        sandbox = tempfile.TemporaryDirectory(prefix="probe-journal-case-")
        self.addCleanup(sandbox.cleanup)
        real = mp.JOURNAL_DIR
        mp.JOURNAL_DIR = Path(sandbox.name)
        self.addCleanup(setattr, mp, "JOURNAL_DIR", real)


# The floor for the transcript corpus below: fewer than this and the parser is
# being proven by a sample too small to have shape.
MIN_TRANSCRIPT_CASES = 4

_CARGO = shutil.which("cargo") is not None

# --- the RUST fixture crate --------------------------------------------------
#
# std-only and dependency-free, so `cargo test` takes about a second and both
# halves live in the permanent suite rather than in a one-time transcript.
# Structured as a STANDALONE package rather than a workspace member: a temp
# directory has no Cargo.toml above it, so cargo cannot walk up into this repo's
# own workspace and start building 18.8 GB of it.
_RUST_MANIFEST = '[package]\nname = "probefixture"\nversion = "0.1.0"\nedition = "2021"\n'

# TWO guards, deliberately. `is_allowed` is pinned only on the positive case, so
# widening it to `true` changes nothing any test asserts: a real hollow
# assertion, not a simulated one. `is_bounded` is pinned on BOTH sides, so
# widening it reddens a named test. One crate answering two ways is the healthy
# control: a malformed fixture does not answer two ways.
_RUST_LIB = (
    "pub fn is_allowed(host: &str) -> bool {\n"
    '    host == "ok.example"\n'
    "}\n"
    "\n"
    "pub fn is_bounded(n: u32) -> bool {\n"
    "    n < 10\n"
    "}\n"
    "\n"
    "#[cfg(test)]\n"
    "mod tests {\n"
    "    use super::*;\n"
    "\n"
    "    #[test]\n"
    "    fn allows_the_known_host() {\n"
    '        assert!(is_allowed("ok.example"));\n'
    "    }\n"
    "\n"
    "    #[test]\n"
    "    fn bounds_both_sides() {\n"
    "        assert!(is_bounded(9));\n"
    "        assert!(!is_bounded(10));\n"
    "    }\n"
    "}\n"
)

# A real `cargo test` transcript from a run whose assertion failed.
CARGO_RED_TRANSCRIPT = """
running 2 tests
test tests::allows_the_known_host ... ok
test tests::bounds_both_sides ... FAILED

failures:

---- tests::bounds_both_sides stdout ----
thread 'tests::bounds_both_sides' panicked at src/lib.rs:20:9:
assertion failed: !is_bounded(10)

failures:
    tests::bounds_both_sides

test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
"""

# A real rustc compile error: the mutation was not expressible, so NO test ran.
# This is the Rust twin of `unittest.loader._FailedTest` and it must never be
# read as a kill.
CARGO_COMPILE_ERROR_TRANSCRIPT = """   Compiling probefixture v0.1.0 (/tmp/x)
error[E0308]: mismatched types
  --> src/lib.rs:6:9
   |
6  |     n < "not a number"
   |         ^^^^^^^^^^^^^^ expected `u32`, found `&str`

For more information about this error, try `rustc --explain E0308`.
error: could not compile `probefixture` (lib test) due to 1 previous error
"""

# TWO targets, which is the ordinary cargo shape rather than a nested run.
CARGO_TWO_TARGET_TRANSCRIPT = """
running 2 tests
test tests::a ... ok
test tests::b ... FAILED

test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s

running 3 tests
test integration::x ... ok
test integration::y ... ok
test integration::z ... ok

test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s
"""

CARGO_IGNORED_TRANSCRIPT = """
running 3 tests
test tests::a ... ok
test tests::b ... ok
test tests::slow ... ignored

test result: ok. 2 passed; 0 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.00s
"""

# A real transcript, captured from a passing run rather than invented.
GREEN_TRANSCRIPT = """test_one (test_mod.GuardTests.test_one) ... ok
test_two (test_mod.GuardTests.test_two) ... ok

----------------------------------------------------------------------
Ran 2 tests in 0.001s

OK
"""

# A real transcript from a suite whose assertions failed.
RED_TRANSCRIPT = """test_one (test_mod.GuardTests.test_one) ... FAIL

======================================================================
FAIL: test_one (test_mod.GuardTests.test_one)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "test_mod.py", line 9, in test_one
    self.assertTrue(False)
AssertionError: False is not true

----------------------------------------------------------------------
Ran 1 test in 0.001s

FAILED (failures=1)
"""

# THE KNOWN-BAD, verbatim. This is what `unittest` prints when the name handed
# to it does not resolve. It carries `Ran 1 test`, an `ERROR:` line and a
# `FAILED` verdict, so every surface signal says "the suite noticed something"
# while no test in the suite executed at all.
LOADER_ERROR_TRANSCRIPT = """======================================================================
ERROR: NoSuchClass (unittest.loader._FailedTest.NoSuchClass)
----------------------------------------------------------------------
AttributeError: module '__main__' has no attribute 'NoSuchClass'

----------------------------------------------------------------------
Ran 1 test in 0.000s

FAILED (errors=1)
"""

EMPTY_DISCOVERY_TRANSCRIPT = """
----------------------------------------------------------------------
Ran 0 tests in 0.000s

NO TESTS RAN
"""

ALL_TRANSCRIPTS = (
    GREEN_TRANSCRIPT,
    RED_TRANSCRIPT,
    LOADER_ERROR_TRANSCRIPT,
    EMPTY_DISCOVERY_TRANSCRIPT,
)


# --- fixture builders --------------------------------------------------------
#
# A real module plus a real suite in a real directory, run by a real
# interpreter. Everything below goes through `mp.probe`, never around it.

_MODULE = """LIMIT = 5
CASES = 2


def over(n):
    return n > LIMIT
"""

_SUITE = """import unittest

import mod_x


class GuardTests(unittest.TestCase):
    def test_limit_is_five(self):
        self.assertEqual(mod_x.LIMIT, 5)

    def test_over_uses_the_limit(self):
        self.assertTrue(mod_x.over(6))
        self.assertFalse(mod_x.over(4))


if __name__ == "__main__":
    unittest.main()
"""

# A suite that asserts NOTHING about LIMIT. The guard exists, the test does
# not defend it, and that is precisely a SURVIVED.
_SUITE_UNDEFENDED = """import unittest

import mod_x


class GuardTests(unittest.TestCase):
    def test_the_module_imports(self):
        self.assertTrue(hasattr(mod_x, "LIMIT"))

    def test_over_returns_a_bool(self):
        self.assertIsInstance(mod_x.over(1), bool)


if __name__ == "__main__":
    unittest.main()
"""

# TWO tests, TWO independent constants: `test_other_is_one` cannot see a
# mutation to `LIMIT` at all, which is exactly the "named test does not
# defend this guard" shape `FastPathTests` needs to prove the fast path
# falls back rather than concluding SURVIVED from a narrow miss.
_MODULE_TWO_INDEPENDENT_CONSTANTS = """LIMIT = 5
OTHER = 1


def over(n):
    return n > LIMIT
"""

_SUITE_ONE_TEST_CARES_ABOUT_LIMIT = """import unittest

import mod_x


class GuardTests(unittest.TestCase):
    def test_limit_is_five(self):
        self.assertEqual(mod_x.LIMIT, 5)

    def test_other_is_one(self):
        self.assertEqual(mod_x.OTHER, 1)


if __name__ == "__main__":
    unittest.main()
"""

# The incident, reproduced: `defaultTest` names a class that is not here.
_SUITE_MISSING_CLASS = """import unittest

import mod_x


class GuardTests(unittest.TestCase):
    def test_limit_is_five(self):
        self.assertEqual(mod_x.LIMIT, 5)


if __name__ == "__main__":
    unittest.main(defaultTest="NoSuchClass")
"""

# A suite that is RED before anything is mutated.
_SUITE_ALREADY_RED = """import unittest

import mod_x


class GuardTests(unittest.TestCase):
    def test_limit_is_five(self):
        self.assertEqual(mod_x.LIMIT, 5)

    def test_this_one_is_broken(self):
        self.assertEqual(mod_x.LIMIT, 99)


if __name__ == "__main__":
    unittest.main()
"""

# THE INCIDENT ON THE MUTANT SIDE. The suite asks the loader for a class the
# MODULE names, so the baseline runs normally and the mutation turns that name
# into one that does not resolve. The loader then reports `Ran 1 test` and an
# ERROR, which is the exact transcript that was twice scored as a kill.
_MODULE_NAMES_ITS_SUITE = """LIMIT = 5
CASES = 2
SUITE = "GuardTests"


def over(n):
    return n > LIMIT
"""

_SUITE_FROM_MODULE_NAME = """import unittest

import mod_x


class GuardTests(unittest.TestCase):
    def test_limit_is_five(self):
        self.assertEqual(mod_x.LIMIT, 5)

    def test_over_uses_the_limit(self):
        self.assertTrue(mod_x.over(6))


if __name__ == "__main__":
    unittest.main(defaultTest=mod_x.SUITE)
"""

# A TRUNCATED transcript: real tests ran, the count was emitted, and the
# OK/FAILED line never arrived. That is what a killed process, a full disk or
# an unflushed buffer looks like from the outside, and it is the only shape
# that reaches the `baseline.ok is None` refusal. Without it that branch is
# absorbed by a later one carrying the same verdict and a different message.
_SUITE_TRUNCATED = """import io
import unittest

import mod_x


class GuardTests(unittest.TestCase):
    def test_limit_is_five(self):
        self.assertEqual(mod_x.LIMIT, 5)

    def test_over_uses_the_limit(self):
        self.assertTrue(mod_x.over(6))


if __name__ == "__main__":
    loaded = unittest.TestLoader().loadTestsFromTestCase(GuardTests)
    outcome = unittest.TextTestRunner(stream=io.StringIO()).run(loaded)
    print("Ran %d tests in 0.001s" % outcome.testsRun)
    raise SystemExit(0)
"""

# A suite that dies before the runner starts. No `Ran N tests` line exists at
# all, which is a different fact from a suite that ran and failed, and the
# message has to say which or a reader debugs the wrong thing.
_SUITE_CRASHES = """import mod_x

raise RuntimeError("this suite never reaches unittest")
"""

# A suite that runs NOTHING and exits 0. MEASURED on this interpreter rather
# than assumed, after a step 9 review caught the first version of this comment
# asserting the opposite of what `mutation_probe.py`'s own docstring says:
# CPython 3.13 prints `Ran 0 tests` followed by `NO TESTS RAN`, never `OK`,
# for both `unittest.main()` on an empty module and a runner handed an empty
# TestSuite. So there is no verdict line at all, and what makes this reach the
# `ran == 0` refusal rather than the generic no-verdict one is purely the
# ORDER of the branches in `probe`. The process exit code is still 0, which is
# the genuinely dangerous part and what this repo's "a check that reads
# nothing must not report clean" convention exists about.
_SUITE_ZERO_TESTS = """import unittest

import mod_x

if __name__ == "__main__":
    result = unittest.TextTestRunner().run(unittest.TestSuite())
    raise SystemExit(0 if result.wasSuccessful() else 1)
"""

# The test COUNT is derived from the module, so a mutation to `CASES` changes
# how many tests run. That is the only way to exercise the N-comparison
# through the real path rather than by hand-building an `UnittestRun`.
_SUITE_COUNT_FROM_MODULE = """import unittest

import mod_x


class GuardTests(unittest.TestCase):
    def test_limit_is_five(self):
        self.assertEqual(mod_x.LIMIT, 5)


def _make(i):
    def body(self):
        self.assertLess(i, mod_x.CASES)
    return body


for _i in range(mod_x.CASES):
    setattr(GuardTests, f"test_case_{_i}", _make(_i))


if __name__ == "__main__":
    unittest.main()
"""


# THE FALSE SURVIVED, and it is the worst thing this tool can do. The guard IS
# defended, by `test_limit_is_five`, but that test is gated on the very constant
# being mutated, so the mutation SKIPS it instead of reddening it. `Ran N tests`
# counts the skip, so the population check sees no change and every test that
# did run passed. Before the skip comparison this scored SURVIVED, and CLAUDE.md
# says a branch that survives every mutation is DELETED. The shape is drawn from
# this repo: `@unittest.skipUnless` on a module constant appears eighteen times.
_SUITE_SKIPS_ITS_OWN_GUARD = """import unittest

import mod_x


class GuardTests(unittest.TestCase):
    @unittest.skipUnless(mod_x.LIMIT == 5, "only meaningful at the shipped limit")
    def test_limit_is_five(self):
        self.assertEqual(mod_x.LIMIT, 5)

    def test_cases_is_two(self):
        self.assertEqual(mod_x.CASES, 2)

    def test_over_is_callable(self):
        self.assertIsNotNone(mod_x.over(1))


if __name__ == "__main__":
    unittest.main()
"""

# A suite that reports `Ran 20 tests` while executing two. `--floor 20` was
# satisfied by this, which makes the floor decorative exactly where it matters:
# several suites here skip precisely when the checkout is shallow, and a shallow
# checkout is the environment the report contract mandates probing in.
_SUITE_MOSTLY_SKIPPED = """import unittest

import mod_x


class GuardTests(unittest.TestCase):
    def test_limit_is_five(self):
        self.assertEqual(mod_x.LIMIT, 5)

    def test_cases_is_two(self):
        self.assertEqual(mod_x.CASES, 2)


class SkippedTests(unittest.TestCase):
    pass


for _i in range(18):
    setattr(
        SkippedTests,
        "test_skipped_%d" % _i,
        unittest.skip("not applicable here")(lambda self: None),
    )


if __name__ == "__main__":
    unittest.main()
"""

# A red run that names no failing test. Stock unittest, no custom runner: the
# mutation makes an `expectedFailure` test pass, so the tail reads
# `FAILED (unexpected successes=1)` with no `FAIL:` or `ERROR:` header anywhere.
# This scored `[KILLED] 0 of 3 test(s) reddened: ` and exited 0, which is the
# line a reviewer copies into `defended_by.reddened`, where CLAUDE.md calls a
# zero-reddened claim a BLOCK.
_SUITE_UNEXPECTED_SUCCESS = """import unittest

import mod_x


class GuardTests(unittest.TestCase):
    @unittest.expectedFailure
    def test_limit_is_not_five(self):
        self.assertNotEqual(mod_x.LIMIT, 5)

    def test_cases_is_two(self):
        self.assertEqual(mod_x.CASES, 2)

    def test_over_is_callable(self):
        self.assertIsNotNone(mod_x.over(1))


if __name__ == "__main__":
    unittest.main()
"""


def _fixture(tmp: Path, suite_src: str, module_src: str = _MODULE) -> tuple[Path, Path]:
    module = tmp / "mod_x.py"
    suite = tmp / "test_mod_x.py"
    module.write_text(module_src, encoding="utf-8")
    suite.write_text(suite_src, encoding="utf-8")
    return module, suite


class TranscriptFloorTests(ProbeTestCase):
    """FLOOR. The corpus is non-empty, holds a named known member, and clears a
    numeric minimum. Proven to redden by forcing the input empty."""

    def test_the_transcript_corpus_is_not_empty(self) -> None:
        self.assertGreaterEqual(len(ALL_TRANSCRIPTS), MIN_TRANSCRIPT_CASES)
        self.assertIn(LOADER_ERROR_TRANSCRIPT, ALL_TRANSCRIPTS, "the known-bad is missing")

    def test_a_green_transcript_parses_to_a_real_run(self) -> None:
        run = mp.parse_unittest_run(GREEN_TRANSCRIPT)
        self.assertEqual(run.ran, 2)
        self.assertTrue(run.ok)
        self.assertTrue(run.is_transcript)
        self.assertFalse(run.loader_error)

    def test_the_floor_reddens_when_the_input_is_forced_empty(self) -> None:
        # The proof that the assertions above are not vacuous: an empty input
        # must NOT satisfy them.
        run = mp.parse_unittest_run("")
        self.assertIsNone(run.ran)
        self.assertFalse(run.is_transcript)

    def test_every_transcript_in_the_corpus_is_distinguishable(self) -> None:
        shapes = {
            (r.ran, r.ok, r.loader_error)
            for r in (mp.parse_unittest_run(t) for t in ALL_TRANSCRIPTS)
        }
        self.assertEqual(len(shapes), len(ALL_TRANSCRIPTS))


class ParserKnownBadTests(ProbeTestCase):
    """KNOWN-BAD as a crafted INPUT, because this function is handed its input."""

    def test_a_loader_error_transcript_is_not_read_as_a_run(self) -> None:
        run = mp.parse_unittest_run(LOADER_ERROR_TRANSCRIPT)
        self.assertTrue(run.loader_error)
        # And crucially: the loader stub is NOT counted as a test that reddened.
        # Reporting `reddened 1` here is the original incident verbatim.
        self.assertEqual(run.failed, [])

    def test_a_transcript_that_merely_MENTIONS_the_marker_is_not_a_loader_error(self) -> None:
        # THE FALSE POSITIVE THE PROBE FOUND IN ITSELF. A suite that tests
        # loader-error handling prints the marker into its own assertion diffs.
        # Read as a raw substring, that made a genuine KILLED read as NO RUN.
        # The marker means something only in an outcome DESCRIPTOR.
        text = (
            "FAIL: test_x (test_mod.GuardTests.test_x)\n"
            "AssertionError: 'unittest.loader._FailedTest' != ''\n"
            "\nRan 3 tests in 0.1s\n\nFAILED (failures=1)\n"
        )
        run = mp.parse_unittest_run(text)
        self.assertFalse(run.loader_error, "a mention is not a loader error")
        self.assertEqual(run.ran, 3)
        self.assertTrue(any("test_x" in f for f in run.failed), run.failed)

    def test_a_red_transcript_names_the_test_that_reddened(self) -> None:
        run = mp.parse_unittest_run(RED_TRANSCRIPT)
        self.assertEqual(run.ran, 1)
        self.assertFalse(run.ok)
        self.assertFalse(run.loader_error)
        self.assertTrue(any("test_one" in f for f in run.failed), run.failed)

    def test_an_empty_discovery_is_zero_and_not_none(self) -> None:
        run = mp.parse_unittest_run(EMPTY_DISCOVERY_TRANSCRIPT)
        self.assertEqual(run.ran, 0)
        # `ran == 0` and `ran is None` are different facts and must not flatten.
        self.assertIsNotNone(run.ran)

    def test_a_run_with_no_verdict_line_is_reported_by_its_own_name(self) -> None:
        # `ok is None` with a real count. Without this the branch is dead:
        # every later branch produces NO RUN too, so only the message
        # distinguishes it.
        run = mp.parse_unittest_run("Ran 7 tests in 0.1s\n")
        self.assertEqual(run.ran, 7)
        self.assertIsNone(run.ok)
        self.assertFalse(run.is_transcript)

    def test_a_nested_transcript_takes_the_OUTER_run(self) -> None:
        # `test_check_red.py` and `test_check_mutants.py` run other test runs,
        # so their output carries an inner transcript. Taking the FIRST `Ran N`
        # and letting any `OK` beat any `FAILED` read a genuinely failed outer
        # run as ok=True, which is a false SURVIVED.
        nested = (
            "Ran 2 tests in 0.0s\n\nOK\n"
            "==========\n"
            "FAIL: test_real (m.C.test_real)\n\n"
            "Ran 34 tests in 9.9s\n\nFAILED (failures=1)\n"
        )
        run = mp.parse_unittest_run(nested)
        self.assertEqual(run.ran, 34, "the OUTER count must win")
        self.assertFalse(run.ok, "the OUTER verdict must win")

    def test_a_stray_line_beginning_OK_does_not_beat_a_later_FAILED(self) -> None:
        run = mp.parse_unittest_run(
            "OK so far\nFAIL: test_x (m.C.test_x)\n\nRan 3 tests in 0.1s\n\nFAILED (failures=1)\n"
        )
        self.assertFalse(run.ok)

    def test_the_loader_marker_is_seen_in_a_descriptorless_header(self) -> None:
        # The `name` half of the discriminator. A header with no parenthesised
        # descriptor puts the marker in the name, and the whole module exists
        # because a loader error was mistaken for a kill, so this half is
        # proven rather than believed.
        run = mp.parse_unittest_run(
            "ERROR: unittest.loader._FailedTest.mod\n\nRan 1 test in 0.0s\n\nFAILED (errors=1)\n"
        )
        self.assertTrue(run.loader_error)
        self.assertEqual(run.failed, [])

    def test_the_singular_ran_one_test_line_parses(self) -> None:
        self.assertEqual(mp.parse_unittest_run("Ran 1 test in 0.1s\n\nOK\n").ran, 1)

    def test_a_bare_traceback_is_not_a_transcript(self) -> None:
        run = mp.parse_unittest_run("Traceback (most recent call last):\n  SyntaxError\n")
        self.assertIsNone(run.ran)
        self.assertFalse(run.is_transcript)


class SkipsAreNotPassesTests(ProbeTestCase):
    """`Ran N tests` COUNTS SKIPS, and discarding that made two guarantees
    hollow at once. Both known-bads here are real fixtures run through the real
    subprocess path, because a hand-built transcript cannot show that the
    population check failed to notice."""

    def test_a_mutation_that_SKIPS_its_own_guard_is_NO_RUN_not_SURVIVED(self) -> None:
        # THE WORST THING THIS TOOL CAN DO. The guard IS defended, by
        # `test_limit_is_five`, but that test is gated on the constant being
        # mutated, so the mutation skips it rather than reddening it. `ran` is
        # unchanged and everything that ran passed. Scoring this SURVIVED would
        # tell a reader, per CLAUDE.md, to DELETE a working guard.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_SKIPS_ITS_OWN_GUARD)
            before = module.read_bytes()
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
            )
            self.assertEqual(result.verdict, mp.NO_RUN, "\n".join(result.lines))
            self.assertNotEqual(result.verdict, mp.SURVIVED)
            self.assertTrue(
                any("SKIPPED" in line for line in result.lines),
                "the refusal must say WHY: " + "\n".join(result.lines),
            )
            self.assertEqual(module.read_bytes(), before, "the restore was not byte-exact")

    def test_the_floor_counts_EXECUTED_tests_not_COLLECTED_ones(self) -> None:
        # `Ran 20 tests ... OK (skipped=18)` cleared `--floor 20` on two real
        # executions. Suites here skip precisely when the checkout is shallow,
        # which is the environment the report contract mandates probing in.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_MOSTLY_SKIPPED)
            before = module.read_bytes()
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=20
            )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertTrue(any("EXECUTED" in line for line in result.lines))
            self.assertEqual(module.read_bytes(), before)

    def test_the_same_suite_is_ACCEPTED_at_a_floor_its_executions_clear(self) -> None:
        # The other direction, so the refusal above is a floor and not a ban.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_MOSTLY_SKIPPED)
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))

    def test_a_RED_run_that_names_no_test_is_NO_RUN_not_KILLED(self) -> None:
        # Stock unittest, no custom runner: the mutation makes an
        # expectedFailure test pass, so the tail is
        # `FAILED (unexpected successes=1)` with no FAIL: header anywhere. This
        # printed `[KILLED] 0 of 3 test(s) reddened: ` and exited 0, and that
        # line is what a reviewer copies into `defended_by.reddened`, where a
        # zero-reddened claim is a BLOCK.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_UNEXPECTED_SUCCESS)
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
            )
            self.assertEqual(result.verdict, mp.NO_RUN, "\n".join(result.lines))
            self.assertNotEqual(result.verdict, mp.KILLED)
            self.assertFalse(
                any("0 of" in line and mp.KILLED in line for line in result.lines),
                "a KILLED naming zero tests must never be printed: " + "\n".join(result.lines),
            )

    def test_the_parser_reads_the_skip_count_off_BOTH_tails(self) -> None:
        ok = mp.parse_unittest_run("Ran 20 tests in 1.0s\n\nOK (skipped=18)\n")
        self.assertEqual((ok.ran, ok.skipped, ok.executed), (20, 18, 2))
        failed = mp.parse_unittest_run(
            "FAIL: test_x (m.C.test_x)\nRan 20 tests in 1.0s\n\nFAILED (failures=1, skipped=17)\n"
        )
        self.assertEqual((failed.ran, failed.skipped, failed.executed), (20, 17, 3))

    def test_a_transcript_with_no_skip_count_reads_as_zero_not_as_missing(self) -> None:
        run = mp.parse_unittest_run("Ran 4 tests in 1.0s\n\nOK\n")
        self.assertEqual((run.skipped, run.executed), (0, 4))


class AbandonedJournalTests(ProbeTestCase):
    """A KILLED probe skips the `finally` entirely, so the byte-exact restore
    has a hole no care inside `probe` can close. It was called undefendable by
    construction; that is true of the `finally` and false of the TOOL, and this
    is the journal that makes the next run repair what the last one abandoned.

    Earned twice in one session: two probes of this repo were killed mid-suite
    and left their mutation in a tracked source file, once found by reading the
    function and once by grepping on suspicion."""

    def test_a_killed_probe_is_RECOVERED_by_the_next_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            jdir = tmp / "journal"
            module, suite = _fixture(tmp, _SUITE)
            pristine = module.read_bytes()
            # Exactly what a kill leaves behind: journal written, file mutated,
            # no `finally`. Built through the module's own journal paths so the
            # test cannot drift from the layout the tool actually writes.
            marker, backup = mp._journal_paths(module, journal_dir=jdir)
            jdir.mkdir(parents=True)
            backup.write_bytes(pristine)
            marker.write_text(str(module.resolve()), encoding="utf-8")
            module.write_bytes(b"LIMIT = 0\nCASES = 2\n\n\ndef over(n):\n    return n > LIMIT\n")
            self.assertNotEqual(module.read_bytes(), pristine, "the fixture must start mutated")

            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2,
                journal_dir=jdir,
            )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertIn("RECOVERED", "\n".join(result.lines))
            self.assertEqual(module.read_bytes(), pristine, "the file was not repaired")
            self.assertFalse(marker.exists(), "the journal must be cleared once used")
            self.assertFalse(backup.exists())

    def test_recovery_REFUSES_rather_than_quietly_fixing_and_carrying_on(self) -> None:
        # The operator has to learn that whatever they measured in between was
        # measured against code nobody wrote. A silent repair would hide that.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            jdir = tmp / "journal"
            module, suite = _fixture(tmp, _SUITE)
            marker, backup = mp._journal_paths(module, journal_dir=jdir)
            jdir.mkdir(parents=True)
            backup.write_bytes(module.read_bytes())
            marker.write_text(str(module.resolve()), encoding="utf-8")
            module.write_bytes(b"LIMIT = 0\n")
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2,
                journal_dir=jdir,
            )
            self.assertNotIn(result.verdict, (mp.KILLED, mp.SURVIVED))
            self.assertIn("void", "\n".join(result.lines))

    def test_a_CLEAN_journal_directory_recovers_nothing_and_says_nothing(self) -> None:
        # The must-not-fire direction: a normal run must not announce a recovery.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            jdir = tmp / "journal"
            module, suite = _fixture(tmp, _SUITE)
            before = module.read_bytes()
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2,
                journal_dir=jdir,
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            self.assertNotIn("RECOVERED", "\n".join(result.lines))
            self.assertEqual(module.read_bytes(), before)

    def test_a_SUCCESSFUL_probe_leaves_no_journal_behind(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            jdir = tmp / "journal"
            module, suite = _fixture(tmp, _SUITE)
            mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2,
                journal_dir=jdir,
            )
            marker, backup = mp._journal_paths(module, journal_dir=jdir)
            self.assertFalse(marker.exists(), "a finished probe must clear its journal")
            self.assertFalse(backup.exists())

    def test_a_journal_whose_backup_is_MISSING_refuses_rather_than_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            jdir = tmp / "journal"
            module, _suite = _fixture(tmp, _SUITE)
            marker, _backup = mp._journal_paths(module, journal_dir=jdir)
            jdir.mkdir(parents=True)
            marker.write_text(str(module.resolve()), encoding="utf-8")
            lines = mp.recover_abandoned(journal_dir=jdir)
            self.assertTrue(lines)
            self.assertIn("backup", " ".join(lines))
            self.assertIn("git checkout", " ".join(lines))

    def test_recovery_over_a_file_that_is_already_correct_is_a_no_op(self) -> None:
        # A journal can survive a run that DID restore, if the clear itself was
        # interrupted. Repairing an already-correct file must not cry wolf.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            jdir = tmp / "journal"
            module, _suite = _fixture(tmp, _SUITE)
            marker, backup = mp._journal_paths(module, journal_dir=jdir)
            jdir.mkdir(parents=True)
            backup.write_bytes(module.read_bytes())
            marker.write_text(str(module.resolve()), encoding="utf-8")
            self.assertEqual(mp.recover_abandoned(journal_dir=jdir), [])
            self.assertFalse(marker.exists(), "a used journal is cleared either way")


class JournalIsolationTests(ProbeTestCase):
    """The suite must be unable to reach the real journal, and that has to be
    checkable rather than a convention in a docstring. The defect it closes was
    not a bug in any branch: eight tests failed because a killed probe in an
    EARLIER run left an entry that this run then recovered."""

    def test_the_suite_is_NOT_pointed_at_the_real_journal_directory(self) -> None:
        self.assertNotEqual(mp.JOURNAL_DIR, _REAL_JOURNAL)
        self.assertIsNotNone(_JOURNAL_SANDBOX, "setUpModule did not run")

    def test_a_probe_that_omits_journal_dir_lands_in_the_SANDBOX(self) -> None:
        # The one that matters. Over thirty probes in this file omit the
        # argument, and before the resolution moved to call time each of them
        # reached the shared directory no matter what `setUpModule` did.
        with tempfile.TemporaryDirectory() as raw:
            module, _suite = _fixture(Path(raw), _SUITE)
            marker, _backup = mp._journal_paths(module)
            self.assertEqual(marker.parent, mp.JOURNAL_DIR)
            self.assertNotEqual(marker.parent, _REAL_JOURNAL)

    def test_NO_journal_parameter_anywhere_binds_the_global_as_a_DEFAULT(self) -> None:
        # The miss that cost a second red run. `_journal_paths` and
        # `recover_abandoned` were converted to call-time resolution and `probe`
        # was not, so the suite's isolation looked correct, read correctly, and
        # still reached the real directory through the one entry point every
        # test actually uses. Asserted over the whole module signature by
        # signature, because "I converted them all" is precisely the claim that
        # was wrong.
        import inspect
        offenders: list[str] = []
        for name, fn in vars(mp).items():
            if not callable(fn) or not hasattr(fn, "__code__"):
                continue
            if getattr(fn, "__module__", None) != mp.__name__:
                continue
            sig = inspect.signature(fn)
            param = sig.parameters.get("journal_dir")
            if param is None:
                continue
            # `inspect.Parameter.empty` is NOT None, and conflating them flags
            # `_resolve_journal` itself, which takes the value positionally with
            # no default precisely because it is the resolver. Only a parameter
            # that HAS a default can bind one at import time.
            if param.default is not param.empty and param.default is not None:
                offenders.append(f"{name}(journal_dir={param.default!r})")
        self.assertEqual(
            offenders, [],
            "a journal_dir default binds at import time and cannot be patched: "
            + ", ".join(offenders),
        )

    def test_the_journal_default_is_WORKTREE_LOCAL_not_machine_global(self) -> None:
        # The hazard this closes is not a test-only one. Under the system temp
        # folder there was ONE journal for every worktree on the host, so five
        # agents probing in parallel shared it: a killed probe in one worktree
        # made the next probe in another REFUSE, and a run in this checkout
        # recovered a mutant sitting in a different agent's tree.
        self.assertEqual(_REAL_JOURNAL.parent, mp.REPO_ROOT)
        self.assertNotEqual(_REAL_JOURNAL.parent, Path(tempfile.gettempdir()))

    def test_the_ENV_VAR_isolates_a_process_a_monkeypatch_cannot_reach(self) -> None:
        # `CommandLineTests` spawns the CLI for real. A child interpreter
        # imports the module afresh, so an in-process patch of `JOURNAL_DIR`
        # never happened as far as it is concerned. Asserted by RUNNING a child
        # and reading back where it resolved the journal to, because the claim
        # is about a process boundary and cannot be proven in this one.
        with tempfile.TemporaryDirectory() as raw:
            elsewhere = Path(raw) / "child-journal"
            done = subprocess.run(
                (
                    sys.executable, "-c",
                    "import sys; sys.path.insert(0, r'"
                    + str(Path(__file__).resolve().parent)
                    + "'); import mutation_probe as m; print(m._resolve_journal(None))",
                ),
                capture_output=True, text=True, timeout=120,
                env={**os.environ, mp.JOURNAL_ENV_VAR: str(elsewhere)},
            )
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(done.stdout.strip(), str(elsewhere))

    def test_an_EXPLICIT_argument_still_beats_an_inherited_env_var(self) -> None:
        # Precedence matters: a caller that names a directory must not be
        # overridden by something it inherited from its parent's environment.
        with tempfile.TemporaryDirectory() as raw:
            explicit = Path(raw) / "explicit"
            os.environ[mp.JOURNAL_ENV_VAR] = str(Path(raw) / "from-env")
            self.addCleanup(os.environ.pop, mp.JOURNAL_ENV_VAR, None)
            self.assertEqual(mp._resolve_journal(explicit), explicit)
            self.assertEqual(mp._resolve_journal(None), Path(raw) / "from-env")

    def test_the_default_is_read_at_CALL_time_not_at_IMPORT_time(self) -> None:
        # A default argument binds once, when the module is imported, so
        # `journal_dir: Path = JOURNAL_DIR` cannot be redirected by patching the
        # global. That is exactly what shipped, and it is why `setUpModule`
        # isolating the suite was not enough on its own.
        with tempfile.TemporaryDirectory() as raw:
            elsewhere = Path(raw) / "elsewhere"
            real = mp.JOURNAL_DIR
            mp.JOURNAL_DIR = elsewhere
            try:
                self.assertEqual(mp._resolve_journal(None), elsewhere)
            finally:
                mp.JOURNAL_DIR = real
            self.assertEqual(mp._resolve_journal(None), real)


class RestoreWriteFailureTests(ProbeTestCase):
    """The restore WRITE was the only unguarded statement in the finally block,
    four lines above a comment saying a failure there must not swallow the
    restore evidence. Reached by fault injection, like its readback sibling:
    there is no honest fixture for a disk that refuses a write, and the host
    this tool is mandated on runs near-full."""

    def test_a_restore_write_that_FAILS_is_a_loud_refusal_not_a_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            real_write = Path.write_bytes
            calls = {"n": 0}

            def flaky(self: Path, data: bytes) -> int:
                # Call 1 writes the mutant; every later write on this file is
                # the restore, and both attempts must fail for the guard to
                # report rather than recover.
                if self == module:
                    calls["n"] += 1
                    if calls["n"] >= 2:
                        raise OSError(28, "No space left on device")
                return real_write(self, data)

            Path.write_bytes = flaky  # type: ignore[assignment,method-assign]
            try:
                result = mp.probe(
                    file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
                )
            finally:
                Path.write_bytes = real_write  # type: ignore[method-assign]
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            joined = "\n".join(result.lines)
            self.assertIn("THE RESTORE WRITE FAILED", joined)
            self.assertIn("git checkout", joined)
            self.assertEqual(calls["n"], 3, "the write must be RETRIED once before refusing")

    def test_a_transient_failure_is_SURVIVED_by_the_retry(self) -> None:
        # A lock from an editor or a scanner is the common cause and costs
        # nothing to survive, so one retry must actually recover.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            before = module.read_bytes()
            real_write = Path.write_bytes
            calls = {"n": 0}

            def flaky(self: Path, data: bytes) -> int:
                if self == module:
                    calls["n"] += 1
                    if calls["n"] == 2:
                        raise OSError(13, "Permission denied")
                return real_write(self, data)

            Path.write_bytes = flaky  # type: ignore[assignment,method-assign]
            try:
                result = mp.probe(
                    file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
                )
            finally:
                Path.write_bytes = real_write  # type: ignore[method-assign]
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            self.assertEqual(module.read_bytes(), before, "the retry did not restore the file")


class ProbeVerdictTests(ProbeTestCase):
    """Every verdict driven through the real subprocess path."""

    def test_a_defended_guard_is_KILLED(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            before = module.read_bytes()
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            self.assertEqual(result.exit_code, 0)
            self.assertTrue(
                any("test_limit_is_five" in line for line in result.lines),
                "the verdict must NAME the tests that reddened: " + "\n".join(result.lines),
            )
            self.assertEqual(module.read_bytes(), before, "the restore was not byte-exact")

    def test_an_undefended_guard_is_SURVIVED(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_UNDEFENDED)
            before = module.read_bytes()
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
            )
            self.assertEqual(result.verdict, mp.SURVIVED, "\n".join(result.lines))
            self.assertEqual(result.exit_code, 1)
            self.assertEqual(module.read_bytes(), before)

    def test_a_baseline_that_names_a_missing_class_is_NO_RUN(self) -> None:
        # THE INCIDENT, REPRODUCED. `Ran 1 test`, an ERROR line and FAILED, and
        # not one test executed. A probe that scored this would report a kill.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_MISSING_CLASS)
            before = module.read_bytes()
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=1
            )
            self.assertEqual(result.verdict, mp.NO_RUN, "\n".join(result.lines))
            self.assertNotEqual(result.verdict, mp.KILLED)
            # Nothing may be written when the baseline never ran.
            self.assertEqual(module.read_bytes(), before)

    def test_a_baseline_that_never_reaches_the_runner_says_so_by_name(self) -> None:
        # NO RUN is the right verdict for several different conditions, so the
        # verdict alone does not prove the right branch produced it. The
        # message is what a reader acts on and is therefore what is asserted.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_CRASHES)
            before = module.read_bytes()
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=1
            )
            self.assertEqual(result.verdict, mp.NO_RUN, "\n".join(result.lines))
            # "test transcript", not "unittest transcript": the message became
            # backend-neutral when the cargo path landed, because a Rust run has
            # no `Ran N tests` line and naming one would send a reader looking
            # for text that cannot be there. Still branch-distinguishing, which
            # is what this test is for.
            self.assertTrue(
                any("not a test transcript" in line for line in result.lines),
                "\n".join(result.lines),
            )
            self.assertEqual(module.read_bytes(), before)

    def test_a_truncated_baseline_transcript_says_so_by_name(self) -> None:
        # Tests ran, the count arrived, the verdict line never did. NO RUN is
        # the right answer and the MESSAGE is what distinguishes this branch
        # from the three others that also produce NO RUN.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_TRUNCATED)
            before = module.read_bytes()
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
            )
            self.assertEqual(result.verdict, mp.NO_RUN, "\n".join(result.lines))
            self.assertTrue(
                any("no OK/FAILED verdict line" in line for line in result.lines),
                "\n".join(result.lines),
            )
            self.assertTrue(
                any("2 test(s) ran but" in line for line in result.lines),
                "the message must carry the real count: " + "\n".join(result.lines),
            )
            self.assertEqual(module.read_bytes(), before)

    def test_a_baseline_that_runs_zero_tests_and_exits_zero_is_NO_RUN(self) -> None:
        # The suite exits 0 having executed nothing. Scoring a mutation against
        # it would report SURVIVED for every guard in the file.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_ZERO_TESTS)
            before = module.read_bytes()
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=0
            )
            self.assertEqual(result.verdict, mp.NO_RUN, "\n".join(result.lines))
            # "ran 0 tests" rather than the literal `Ran 0 tests` line, for the
            # same reason as above: cargo prints `test result: ok. 0 passed`, so
            # the message names the FACT rather than one backend's spelling of
            # it. Still unique to this branch.
            self.assertTrue(
                any("ran 0 tests" in line for line in result.lines),
                "\n".join(result.lines),
            )
            self.assertEqual(module.read_bytes(), before)

    def test_a_MUTATED_run_that_hits_a_loader_error_is_NO_RUN_not_KILLED(self) -> None:
        # THE HEADLINE CASE. The baseline is green, the mutation makes the
        # loader fail to resolve a class, and the run reports `Ran 1 test`,
        # an ERROR and FAILED. Every surface signal says the suite caught the
        # mutation. No test executed. Scoring this as a kill is the incident
        # that this module's tool was built to refuse, twice in one evening.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(
                tmp, _SUITE_FROM_MODULE_NAME, module_src=_MODULE_NAMES_ITS_SUITE
            )
            before = module.read_bytes()
            result = mp.probe(
                file=module,
                old='SUITE = "GuardTests"',
                new='SUITE = "NoSuchClassAnywhere"',
                suite=suite,
                floor=2,
            )
            self.assertEqual(result.verdict, mp.NO_RUN, "\n".join(result.lines))
            self.assertNotEqual(result.verdict, mp.KILLED)
            self.assertTrue(
                any("failed to IMPORT" in line for line in result.lines),
                "the message must name the loader error rather than the count "
                "mismatch: " + "\n".join(result.lines),
            )
            self.assertEqual(module.read_bytes(), before)

    def test_a_red_baseline_is_REFUSED(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_ALREADY_RED)
            before = module.read_bytes()
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
            )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertTrue(any("RED" in line for line in result.lines))
            self.assertEqual(module.read_bytes(), before)

    def test_a_mutation_that_breaks_the_import_is_NO_RUN_not_KILLED(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            before = module.read_bytes()
            # A syntax error at import time. The suite cannot run, and from the
            # outside that is a non-zero exit exactly like a caught mutation.
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 5 +", suite=suite, floor=2
            )
            self.assertEqual(result.verdict, mp.NO_RUN, "\n".join(result.lines))
            self.assertNotEqual(result.verdict, mp.KILLED)
            # THE MESSAGE, mirroring what the baseline side already asserts.
            # Without this the `not mutant.is_transcript` branch is dead: the
            # count-mismatch branch below absorbs the case with the same
            # verdict and prints "executed None test(s)", which sends a reader
            # at the wrong diagnosis.
            self.assertTrue(
                any("no `Ran N tests` line" in line for line in result.lines),
                "\n".join(result.lines),
            )
            self.assertFalse(
                any("None test(s)" in line for line in result.lines),
                "the count-mismatch branch must not absorb a crashed run: "
                + "\n".join(result.lines),
            )
            self.assertEqual(module.read_bytes(), before, "the restore was not byte-exact")

    def test_a_shrinking_population_is_NO_RUN(self) -> None:
        # The mutated run executes FEWER tests than the baseline. That is not a
        # comparison, and it is what a collection error looks like from outside.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_COUNT_FROM_MODULE)
            before = module.read_bytes()
            result = mp.probe(
                file=module, old="CASES = 2", new="CASES = 1", suite=suite, floor=2
            )
            self.assertEqual(result.verdict, mp.NO_RUN, "\n".join(result.lines))
            self.assertEqual(module.read_bytes(), before)

    def test_a_baseline_under_the_floor_is_REFUSED(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            before = module.read_bytes()
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=99
            )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertTrue(any("floor" in line for line in result.lines))
            self.assertEqual(module.read_bytes(), before)


class FastPathTests(ProbeTestCase):
    """`--tests` (here, `probe(tests=...)`) runs the NAMED test(s)
    first, against both the baseline and the mutation, and reports KILLED the
    moment any of them reddens, without ever running the full suite.

    **The fast path can only ever answer KILLED or "keep looking".** It must
    never itself conclude SURVIVED, NO_RUN or REFUSED (beyond the shared,
    already-defended restore-catastrophe case `_mutate_run_restore` covers for
    every caller): a narrow miss (the named test(s) stayed green under the
    mutation) or an unclean narrow baseline (a typo'd test id, a loader error)
    both fall back to running the FULL paired suite, unchanged from the
    `tests=None` path, before any other verdict is possible.
    """

    def test_a_named_reddening_test_is_KILLED_via_the_fast_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            before = module.read_bytes()
            result = mp.probe(
                file=module,
                old="LIMIT = 5",
                new="LIMIT = 0",
                suite=suite,
                floor=2,
                tests=("GuardTests.test_limit_is_five",),
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            self.assertEqual(result.exit_code, 0)
            self.assertTrue(
                any("verdict path: fast" in line for line in result.lines),
                "the transcript must name which path produced the verdict: "
                + "\n".join(result.lines),
            )
            self.assertTrue(
                any("test_limit_is_five" in line for line in result.lines),
                "\n".join(result.lines),
            )
            # The full suite (both tests, `baseline: 2 test(s)`) must never
            # have been reached: the fast path is the whole point.
            self.assertFalse(
                any(line.startswith("baseline: 2 test(s)") for line in result.lines),
                "the fast path must not fall through to the full suite here: "
                + "\n".join(result.lines),
            )
            self.assertEqual(module.read_bytes(), before, "the restore was not byte-exact")

    def test_the_full_suite_is_never_invoked_when_the_fast_path_KILLS(self) -> None:
        # The claim above ("without ever running the full suite"), proven by
        # COUNTING real subprocess invocations rather than by inference from
        # the transcript. Two expected: the narrow baseline, the narrow
        # mutant. A third would mean the full suite ran too.
        seen: list[tuple[str, ...]] = []
        real = mp.subprocess.run

        def spy(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            cmd = args[0] if args else None
            if isinstance(cmd, (tuple, list)) and bool(cmd) and cmd[0] != "git":
                seen.append(tuple(str(c) for c in cmd))
            return real(*args, **kwargs)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            mp.subprocess.run = spy  # type: ignore[assignment]
            try:
                result = mp.probe(
                    file=module,
                    old="LIMIT = 5",
                    new="LIMIT = 0",
                    suite=suite,
                    floor=2,
                    tests=("GuardTests.test_limit_is_five",),
                )
            finally:
                mp.subprocess.run = real  # type: ignore[assignment]
        self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
        self.assertEqual(len(seen), 2, f"expected exactly 2 suite runs, saw {seen}")

    def test_multiple_named_tests_KILL_via_the_fast_path_naming_only_the_one_that_reddened(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(
                tmp, _SUITE_ONE_TEST_CARES_ABOUT_LIMIT, module_src=_MODULE_TWO_INDEPENDENT_CONSTANTS
            )
            result = mp.probe(
                file=module,
                old="LIMIT = 5",
                new="LIMIT = 0",
                suite=suite,
                floor=2,
                tests=("GuardTests.test_other_is_one", "GuardTests.test_limit_is_five"),
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            killed = [line for line in result.lines if line.startswith(f"[{mp.KILLED}]")]
            self.assertEqual(len(killed), 1, "\n".join(result.lines))
            self.assertIn("test_limit_is_five", killed[0])
            self.assertNotIn("test_other_is_one", killed[0])

    def test_a_named_test_that_does_not_catch_it_FALLS_BACK_and_the_full_suite_still_finds_it(
        self,
    ) -> None:
        # THE CRITICAL CASE. `test_other_is_one` cannot see `LIMIT` at all;
        # `test_limit_is_five` does, but it is not named. A fast path that
        # concluded SURVIVED from the narrow miss alone would be wrong: the
        # FULL suite still catches this, and the final verdict must be
        # KILLED, naming the test that actually reddened.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(
                tmp, _SUITE_ONE_TEST_CARES_ABOUT_LIMIT, module_src=_MODULE_TWO_INDEPENDENT_CONSTANTS
            )
            before = module.read_bytes()
            result = mp.probe(
                file=module,
                old="LIMIT = 5",
                new="LIMIT = 0",
                suite=suite,
                floor=2,
                tests=("GuardTests.test_other_is_one",),
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            self.assertTrue(
                any("falling back" in line for line in result.lines),
                "\n".join(result.lines),
            )
            self.assertTrue(
                any("verdict path: full suite" in line for line in result.lines),
                "\n".join(result.lines),
            )
            killed = [line for line in result.lines if line.startswith(f"[{mp.KILLED}]")]
            self.assertEqual(len(killed), 1, "\n".join(result.lines))
            self.assertIn("test_limit_is_five", killed[0])
            self.assertEqual(module.read_bytes(), before, "the restore was not byte-exact")

    def test_an_undefended_named_test_still_ends_in_SURVIVED_after_falling_back(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_UNDEFENDED)
            before = module.read_bytes()
            result = mp.probe(
                file=module,
                old="LIMIT = 5",
                new="LIMIT = 0",
                suite=suite,
                floor=2,
                tests=("GuardTests.test_the_module_imports",),
            )
            self.assertEqual(result.verdict, mp.SURVIVED, "\n".join(result.lines))
            self.assertEqual(result.exit_code, 1)
            self.assertTrue(
                any("falling back" in line for line in result.lines),
                "\n".join(result.lines),
            )
            self.assertFalse(
                any(line.startswith(f"[{mp.KILLED}]") for line in result.lines),
                "\n".join(result.lines),
            )
            self.assertEqual(module.read_bytes(), before)

    def test_an_unknown_named_test_id_falls_back_rather_than_erroring(self) -> None:
        # A typo in `--tests` must not crash and must not be scored: the
        # narrow baseline hits a loader error, which is exactly the "unclean"
        # signal that sends this to the full suite instead.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            result = mp.probe(
                file=module,
                old="LIMIT = 5",
                new="LIMIT = 0",
                suite=suite,
                floor=2,
                tests=("GuardTests.test_does_not_exist_anywhere",),
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            self.assertTrue(
                any("did not run cleanly" in line for line in result.lines),
                "\n".join(result.lines),
            )
            self.assertTrue(
                any("verdict path: full suite" in line for line in result.lines),
                "\n".join(result.lines),
            )

    def test_tests_NONE_behaves_EXACTLY_as_before_and_still_names_its_path(self) -> None:
        # The default (`tests` omitted) must be byte-for-byte the pre-existing
        # behavior, with one addition: the transcript still names the path.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            self.assertTrue(
                any("verdict path: full suite" in line for line in result.lines),
                "\n".join(result.lines),
            )
            self.assertFalse(any("fast path" in line for line in result.lines))

    def test_the_rust_backend_KILLS_via_the_fast_path_too(self) -> None:
        if not _CARGO:
            self.skipTest("cargo not on PATH")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "src").mkdir(parents=True)
            manifest = root / "Cargo.toml"
            manifest.write_text(_RUST_MANIFEST, encoding="utf-8", newline="\n")
            lib = root / "src" / "lib.rs"
            lib.write_text(_RUST_LIB, encoding="utf-8", newline="\n")
            result = mp.probe(
                file=lib,
                old="n < 10",
                new="n < 100",
                suite=manifest,
                floor=2,
                tests=("tests::bounds_both_sides",),
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            killed = [line for line in result.lines if line.startswith(f"[{mp.KILLED}]")]
            self.assertEqual(len(killed), 1, "\n".join(result.lines))
            self.assertIn("tests::bounds_both_sides", killed[0])
            self.assertTrue(
                any("verdict path: fast" in line for line in result.lines),
                "\n".join(result.lines),
            )


def _rust_crate(root: Path) -> tuple[Path, Path]:
    """A standalone crate, matching `RustProbeTests._crate`'s shape but at
    module scope: the classes below prove a later round's mtime fix
    and need the fixture without inheriting from that class.
    """
    (root / "src").mkdir(parents=True)
    manifest = root / "Cargo.toml"
    manifest.write_text(_RUST_MANIFEST, encoding="utf-8", newline="\n")
    lib = root / "src" / "lib.rs"
    lib.write_text(_RUST_LIB, encoding="utf-8", newline="\n")
    return lib, manifest


def _age_file(path: Path, seconds: float) -> None:
    """Set `path`'s mtime `seconds` into the past, deterministically.

    This is the mechanism the step 9 review's own sweep used to reproduce
    findings 1 and 2 without depending on how long any particular build
    happens to take: an ordinary checked-out `.rs` file is routinely hours
    old, and this recreates that fact directly rather than hoping a slow or
    loaded machine reproduces it by accident (which is exactly the shape
    that made the PRE-EXISTING `RustProbeTests` and `FastPathTests` cargo
    tests flake: the review measured `test_A_BEHAVIORAL_TEST_KILLS_ITS_
    GUARD_AND_IS_NAMED` redden in 2 of 3 full-suite runs under concurrent
    load while idle standalone runs stayed green).
    """
    old = time.time() - seconds
    os.utime(path, (old, old))


# Hours, not seconds: strictly larger than `mp.MTIME_SKEW_SECONDS` by a wide
# margin, so the pre-fix defect (a stamp anchored to this stale mtime) is
# unambiguous regardless of clock or filesystem granularity, and so this can
# never be satisfied by accident the way an 8s age marginally was in the
# review's own sweep.
_STALE_SECONDS = 3600.0


@unittest.skipUnless(_CARGO, "cargo is not on PATH")
class CargoMtimeFreshnessTests(ProbeTestCase):
    """A later round, review findings 1 and 2 (both HIGH, one mechanism).

    The cargo backend used to stamp the mutated file's mtime relative to a
    STAT TAKEN ONCE at the top of `probe()`, which lands BEHIND freshly
    built artifacts whenever the source is older than `MTIME_SKEW_SECONDS`
    (an ordinary checked-out file) or whenever a build cycle already ran
    before the write (the fast path's own narrow attempt). Cargo then serves
    the stale HONEST binary against the MUTATED source: a false SURVIVED,
    the one verdict `probe-honesty` exists to make impossible.

    Both tests here age the source file hours into the past with `_age_file`
    rather than racing `MTIME_SKEW_SECONDS` against actual compile duration,
    so correctness is proven independent of how fast or loaded the machine
    running this suite happens to be.
    """

    def test_a_stale_source_mtime_is_still_KILLED_on_the_full_suite_path(self) -> None:
        # Review finding 2's exact reproduction: no `--tests` at all, a
        # single mutation, a source aged an hour. Before the fix this
        # reported SURVIVED with a sub-second mutant run, the tell that
        # cargo served the binary it had just built for the baseline rather
        # than recompiling for the mutant.
        with tempfile.TemporaryDirectory() as raw:
            lib, manifest = _rust_crate(Path(raw))
            _age_file(lib, _STALE_SECONDS)
            result = mp.probe(
                file=lib, old="n < 10", new="n < 100", suite=manifest, floor=2
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            killed = [line for line in result.lines if line.startswith(f"[{mp.KILLED}]")]
            self.assertEqual(len(killed), 1, "\n".join(result.lines))
            self.assertIn("tests::bounds_both_sides", killed[0])

    def test_a_stale_source_mtime_is_still_KILLED_after_the_fast_path_falls_back(
        self,
    ) -> None:
        # Review finding 1's exact reproduction: the fast path's OWN narrow
        # build cycle is what ate the skew even though the narrow attempt
        # itself scored correctly; the FALLBACK's full-suite mutant run is
        # the one the review caught coming back stale.
        # `tests::allows_the_known_host` cannot observe `is_bounded`'s
        # mutation at all, so this is guaranteed to miss narrowly and fall
        # back, exactly the shape the review reproduced.
        with tempfile.TemporaryDirectory() as raw:
            lib, manifest = _rust_crate(Path(raw))
            _age_file(lib, _STALE_SECONDS)
            result = mp.probe(
                file=lib,
                old="n < 10",
                new="n < 100",
                suite=manifest,
                floor=2,
                tests=("tests::allows_the_known_host",),
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            self.assertTrue(
                any("verdict path: full suite" in line for line in result.lines),
                "the fast path must have missed narrowly and fallen back: "
                + "\n".join(result.lines),
            )
            killed = [line for line in result.lines if line.startswith(f"[{mp.KILLED}]")]
            self.assertEqual(len(killed), 1, "\n".join(result.lines))
            self.assertIn("tests::bounds_both_sides", killed[0])


# Independent constants, one suite that is RED before any mutation, for
# `FastPathDisclosureTests`'s finding-4(c) case: a NAMED test that is green
# and does not observe the mutation at all, in a suite an UNRELATED test
# keeps overall red.
_MODULE_FOR_HONESTY_TEST = """LIMIT = 5
OTHER = 1


def over(n):
    return n > LIMIT
"""

_SUITE_RED_WITH_AN_UNRELATED_GREEN_NAMED_TEST = """import unittest

import mod_x


class GuardTests(unittest.TestCase):
    def test_other_is_one(self):
        self.assertEqual(mod_x.OTHER, 1)

    def test_this_one_is_broken(self):
        self.assertEqual(mod_x.LIMIT, 99)


if __name__ == "__main__":
    unittest.main()
"""


class FastPathDisclosureTests(ProbeTestCase):
    """A later round, review findings 4 and 5: the fast path's
    transcript must stay honest about what it did and did not check."""

    def test_a_fast_KILLED_transcript_carries_the_green_baseline_line(self) -> None:
        # Finding 5. Without this line the runbook's step 9 acceptance
        # clause cannot read "the baseline reported green" or compare its N
        # against the mutant's N from a fast-path transcript at all.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            result = mp.probe(
                file=module,
                old="LIMIT = 5",
                new="LIMIT = 0",
                suite=suite,
                floor=2,
                tests=("GuardTests.test_limit_is_five",),
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            baseline_lines = [
                line for line in result.lines if line.startswith("baseline (fast path")
            ]
            self.assertEqual(len(baseline_lines), 1, "\n".join(result.lines))
            self.assertIn("OK", baseline_lines[0])
            self.assertIn("1 test(s)", baseline_lines[0])
            # It must be BEFORE the mutation, matching every other baseline
            # in this file: the promise is "baseline first", not "baseline
            # somewhere".
            mutation_idx = next(
                i for i, ln in enumerate(result.lines) if ln.startswith("mutation applied:")
            )
            baseline_idx = result.lines.index(baseline_lines[0])
            self.assertLess(baseline_idx, mutation_idx, "\n".join(result.lines))

    def test_a_fast_KILLED_transcript_discloses_the_floor_was_not_evaluated(self) -> None:
        # Finding 4(a)/(b). A fast KILLED never ran the full suite, so it
        # never checked --floor or whether the wider suite is red; silence
        # about that was the defect the review measured with `--floor 100`
        # over a 2-test suite.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            result = mp.probe(
                file=module,
                old="LIMIT = 5",
                new="LIMIT = 0",
                suite=suite,
                floor=99,
                tests=("GuardTests.test_limit_is_five",),
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            self.assertTrue(
                any(
                    "--floor 99" in line and "NOT" in line and "evaluated" in line
                    for line in result.lines
                ),
                "a fast KILLED must disclose that --floor was not evaluated: "
                + "\n".join(result.lines),
            )

    def test_a_refusal_reached_via_the_fast_path_never_claims_nothing_was_written(
        self,
    ) -> None:
        # Finding 4(c). The narrow named test is green and does not observe
        # the mutation; the wider suite is red for an unrelated reason. The
        # fast path mutates and byte-exactly restores while proving the
        # narrow miss, then falls back; the full baseline then correctly
        # refuses on the red suite, but must not lie about whether anything
        # was written along the way.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(
                tmp,
                _SUITE_RED_WITH_AN_UNRELATED_GREEN_NAMED_TEST,
                module_src=_MODULE_FOR_HONESTY_TEST,
            )
            result = mp.probe(
                file=module,
                old="LIMIT = 5",
                new="LIMIT = 0",
                suite=suite,
                floor=2,
                tests=("GuardTests.test_other_is_one",),
            )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertTrue(
                any(line.startswith("mutation applied:") for line in result.lines),
                "the fast path's own attempt must have written a mutation: "
                + "\n".join(result.lines),
            )
            self.assertFalse(
                any(
                    line.endswith(f"Nothing was written to {module}.")
                    for line in result.lines
                ),
                "a transcript that already wrote (and restored) a mutation must "
                "not claim nothing was written: " + "\n".join(result.lines),
            )
            self.assertTrue(
                any("already wrote a mutation" in line for line in result.lines),
                "\n".join(result.lines),
            )

    def test_an_ordinary_refusal_with_no_prior_write_still_says_nothing_was_written(
        self,
    ) -> None:
        # The negative of the case above, pinned so the honesty fix cannot
        # simply always print the alternate phrasing: when the fast path
        # never actually wrote anything (a RED suite even on the narrow
        # baseline, so `_fast_path` falls back before ever mutating), the
        # ordinary claim stays exactly as it always was.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_ALREADY_RED)
            result = mp.probe(
                file=module,
                old="LIMIT = 5",
                new="LIMIT = 0",
                suite=suite,
                floor=2,
                tests=("GuardTests.test_this_one_is_broken",),
            )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertFalse(
                any(line.startswith("mutation applied:") for line in result.lines),
                "nothing should have been written on this path: "
                + "\n".join(result.lines),
            )
            self.assertTrue(
                any(
                    line.endswith(f"Nothing was written to {module}.")
                    for line in result.lines
                ),
                "\n".join(result.lines),
            )


class BytecodeCacheTests(ProbeTestCase):
    """A SIZE-PRESERVING mutation must still reach the interpreter.

    This is a regression pin on a defect this module shipped with and its own
    suite caught on the first run. CPython invalidates a `.pyc` on the source's
    mtime and SIZE; `LIMIT = 5` to `LIMIT = 0` changes neither, so the mutated
    run imported the baseline's cached bytecode and every test passed over a
    mutation that never took effect. `SURVIVED` over a run that did not test
    what it claimed is the exact class this file exists to refuse, so the guard
    against it is pinned rather than left to the comment in `run_suite`.
    """

    def test_a_size_preserving_mutation_is_still_detected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            self.assertEqual(
                len("LIMIT = 5"), len("LIMIT = 0"), "this test is pointless unless the "
                "mutation preserves the file size, which is what defeats invalidation"
            )
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))

    def test_each_run_gets_its_own_empty_cache_prefix(self) -> None:
        # The mechanism, asserted directly: two runs must not share a prefix,
        # or the second one can hit the first one's stale entry.
        seen: list[str] = []
        real = mp.subprocess.run

        def spy(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            # The tree-identity git calls go through this same
            # `subprocess.run`. Filtering them out by `"PYTHONPYCACHEPREFIX"
            # not in env` is NOT reliable here: `_run_git`'s env is
            # `dict(os.environ)`, so when THIS suite itself runs nested
            # inside another `run_python_suite` invocation (exactly what
            # happens whenever `mutation_probe.py` probes itself, which is
            # this repo's normal use of the tool), the ambient
            # `PYTHONPYCACHEPREFIX` the OUTER invocation set is still
            # sitting in `os.environ` and leaks into the git call's env
            # unchanged, so an env-presence filter miscounts 3. The COMMAND
            # is what actually distinguishes them: only `_run_git` invokes
            # `git` as argv[0].
            cmd = args[0] if args else None
            is_git = isinstance(cmd, (tuple, list)) and bool(cmd) and cmd[0] == "git"
            if not is_git:
                env = kwargs.get("env")
                assert isinstance(env, dict)
                seen.append(env["PYTHONPYCACHEPREFIX"])
            return real(*args, **kwargs)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            mp.subprocess.run = spy  # type: ignore[assignment]
            try:
                mp.probe(file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2)
            finally:
                mp.subprocess.run = real  # type: ignore[assignment]

        self.assertEqual(len(seen), 2, "baseline and mutant should each have run once")
        self.assertNotEqual(seen[0], seen[1], "the two runs shared a bytecode cache")


class WriteDidNotTakeTests(ProbeTestCase):
    """The edit is ASSERTED to have applied, not assumed.

    This branch guards against the disk disagreeing with the write: a sync
    client, a virus scanner, a read-only overlay. There is no fixture that
    produces that honestly, so it is reached by FAULT INJECTION, which is the
    only way to exercise a guard against the filesystem lying. Left unpinned it
    was the one mutation in this module that SURVIVED its own probe.
    """

    def test_a_readback_that_differs_from_the_write_is_REFUSED(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            true_original = module.read_bytes()
            real_read = Path.read_bytes
            calls = {"n": 0}

            def flaky(self: Path) -> bytes:
                # Call 1 is the original read, call 2 is the readback the guard
                # checks, call 3 is the restore verification. Corrupt only 2.
                if self == module:
                    calls["n"] += 1
                    if calls["n"] == 2:
                        return b"this is not what was written"
                return real_read(self)

            Path.read_bytes = flaky  # type: ignore[assignment,method-assign]
            try:
                result = mp.probe(
                    file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
                )
            finally:
                Path.read_bytes = real_read  # type: ignore[method-assign]

            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertTrue(
                any("did not apply" in line for line in result.lines),
                "\n".join(result.lines),
            )
            # AND the abandoned run still reports the restore, because that is
            # the line telling a reader whether the tree is intact.
            self.assertTrue(
                any(line.startswith("restore:") for line in result.lines),
                "an abandoned run must still show its restore: " + "\n".join(result.lines),
            )
            self.assertEqual(module.read_bytes(), true_original)

    def test_a_restore_that_does_not_match_raises_the_alarm(self) -> None:
        # The loudest branch in the file: the tree is now wrong and the caller
        # has to be told, by name, with the recovery command. Same fault
        # injection, aimed at the restore verification read instead.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            real_read = Path.read_bytes
            calls = {"n": 0}

            def flaky(self: Path) -> bytes:
                if self == module:
                    calls["n"] += 1
                    if calls["n"] == 3:
                        return b"the restore did not take"
                return real_read(self)

            Path.read_bytes = flaky  # type: ignore[assignment,method-assign]
            try:
                result = mp.probe(
                    file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
                )
            finally:
                Path.read_bytes = real_read  # type: ignore[method-assign]

            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertTrue(
                any("THE RESTORE FAILED" in line for line in result.lines),
                "\n".join(result.lines),
            )
            self.assertTrue(
                any("git checkout" in line for line in result.lines),
                "the alarm must carry the recovery command",
            )
            self.assertTrue(
                any("identical=False" in line for line in result.lines),
                "the comparison must be PRINTED, not summarized",
            )


class GrandchildCacheTests(ProbeTestCase):
    """A suite that spawns its OWN child with an explicit `env=` must still see
    the mutation.

    A step 9 review measured this as nondeterministic: six identical probes
    returned KILLED, SURVIVED, SURVIVED, SURVIVED, KILLED, KILLED, because
    `PYTHONPYCACHEPREFIX` reaches the direct child only and the grandchild
    resolved the real `__pycache__`. `test_must_not_fire.py` and
    `test_check_red.py` already spawn with an explicit `env=`, so this is the
    shape of real suites in this repo and not an invented one.

    The invariant that makes it deterministic is the mtime skew, which is a
    property of the FILE and therefore binds a reader at any depth.
    """

    RUNS = 4

    def test_a_grandchild_with_a_clean_env_still_sees_the_mutation(self) -> None:
        suite_src = (
            "import os\n"
            "import subprocess\n"
            "import sys\n"
            "import unittest\n"
            "\n"
            "import mod_x\n"
            "\n"
            "\n"
            "class GuardTests(unittest.TestCase):\n"
            "    def test_limit_via_a_grandchild_with_a_clean_env(self):\n"
            "        env = {k: os.environ[k] for k in ('PATH', 'SYSTEMROOT')\n"
            "               if k in os.environ}\n"
            "        out = subprocess.run(\n"
            "            (sys.executable, '-c', 'import mod_x; print(mod_x.LIMIT)'),\n"
            "            cwd=os.path.dirname(os.path.abspath(__file__)),\n"
            "            capture_output=True, text=True, env=env, timeout=60,\n"
            "        )\n"
            "        self.assertEqual(out.stdout.strip(), '5', out.stderr)\n"
            "\n"
            "    def test_the_module_still_imports(self):\n"
            "        self.assertTrue(hasattr(mod_x, 'LIMIT'))\n"
            "\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    unittest.main()\n"
        )
        verdicts = []
        for _ in range(self.RUNS):
            with tempfile.TemporaryDirectory() as raw:
                tmp = Path(raw)
                module, suite = _fixture(tmp, suite_src)
                before = module.read_bytes()
                result = mp.probe(
                    file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
                )
                verdicts.append(result.verdict)
                self.assertEqual(module.read_bytes(), before)
        self.assertEqual(
            set(verdicts),
            {mp.KILLED},
            f"the verdict must be deterministic and correct, got {verdicts}",
        )

    def test_the_mutated_file_carries_a_skewed_mtime_while_the_suite_runs(self) -> None:
        # The mechanism, asserted directly rather than only through its effect.
        seen: list[float] = []
        real = mp.subprocess.run

        def spy(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            # The tree-identity git calls go through this same
            # `subprocess.run` too, before the mutation. Filtered by COMMAND
            # rather than by `PYTHONPYCACHEPREFIX in env`: that env var can
            # be ambient (inherited from an OUTER `run_python_suite` when
            # this suite runs nested, which is this repo's normal use of the
            # tool on itself), so its mere presence does not distinguish a
            # git call from a Python one the way argv[0] does.
            cmd = args[0] if args else None
            is_git = isinstance(cmd, (tuple, list)) and bool(cmd) and cmd[0] == "git"
            if not is_git:
                seen.append(module.stat().st_mtime)
            return real(*args, **kwargs)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            baseline_mtime = module.stat().st_mtime
            mp.subprocess.run = spy  # type: ignore[assignment]
            try:
                mp.probe(file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2)
            finally:
                mp.subprocess.run = real  # type: ignore[assignment]
            self.assertEqual(len(seen), 2)
            self.assertAlmostEqual(seen[0], baseline_mtime, places=3)
            # A LITERAL, not `mp.MTIME_SKEW_SECONDS - 1`. Reading the constant
            # made this assertion vacuous under the one mutation it exists to
            # catch: with the constant at 0 it read `>= -1` and passed, leaving
            # the whole invariant defended by a single probabilistic test. A
            # step 9 review measured that.
            self.assertGreaterEqual(seen[1] - seen[0], 5)
            self.assertGreaterEqual(mp.MTIME_SKEW_SECONDS, 2, "must clear FAT's 2s granularity")
            # And it is put back, so nothing downstream sees a future stamp.
            self.assertAlmostEqual(module.stat().st_mtime, baseline_mtime, places=3)


class UtimeFailureTests(ProbeTestCase):
    """`os.utime` fails with EPERM on read-only files, on files owned by
    another user, and on some FUSE and network mounts. Unwrapped it escaped as
    a traceback: no verdict, and no `restore:` line, which is the one line this
    repo's convention exists to force. That was the unnamed-fifth-outcome
    defect reintroduced by the fix for the cache defect, caught by a step 9
    review."""

    def _probe_with_failing_utime(self, fail_on: int) -> mp.ProbeResult:
        real = mp.os.utime
        calls = {"n": 0}

        def flaky(path: object, times: object = None) -> None:
            calls["n"] += 1
            if calls["n"] == fail_on:
                raise PermissionError(13, "Operation not permitted")
            real(path, times)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            self.original = module.read_bytes()
            mp.os.utime = flaky  # type: ignore[assignment]
            try:
                result = mp.probe(
                    file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
                )
            finally:
                mp.os.utime = real  # type: ignore[assignment]
            self.restored = module.read_bytes()
        return result

    def test_a_failure_skewing_the_mutant_REFUSES_with_the_restore_shown(self) -> None:
        result = self._probe_with_failing_utime(fail_on=1)
        self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
        self.assertTrue(
            any("could not skew" in line for line in result.lines), "\n".join(result.lines)
        )
        # The evidence line survives the abandonment.
        self.assertTrue(any(line.startswith("restore:") for line in result.lines))
        self.assertEqual(self.restored, self.original)

    def test_a_failure_restoring_the_mtime_still_returns_a_verdict(self) -> None:
        # Call 2 is the restore-side utime. Leaving mtime at "now" is SAFE: it
        # still differs from the `original + skew` any mutant-compiled .pyc
        # recorded, so the cache is still invalidated. What must not happen is
        # a traceback in place of a verdict.
        result = self._probe_with_failing_utime(fail_on=2)
        self.assertIn(result.verdict, (mp.KILLED, mp.SURVIVED), "\n".join(result.lines))
        self.assertTrue(
            any("mtime_restored=False" in line for line in result.lines),
            "the restore line must DISCLOSE that the stamp was not put back: "
            + "\n".join(result.lines),
        )
        self.assertEqual(self.restored, self.original)


class CommandLineTests(ProbeTestCase):
    """The CLI contract, exercised as a REAL child process.

    `main()` and `_resolve()` were undefended: mutating `return
    result.exit_code` to `return 0` SURVIVED, so the module's headline promise
    that only KILLED exits 0 was proven by nothing. The claim is about an exit
    code a caller chains on, so it is proven by running the program.
    """

    def _run(self, tmp: Path, old: str, new: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            (
                sys.executable,
                str(Path(__file__).resolve().parent / "mutation_probe.py"),
                "--file", str(tmp / "mod_x.py"),
                "--old", old,
                "--new", new,
                "--suite", str(tmp / "test_mod_x.py"),
                "--floor", "2",
            ),
            capture_output=True,
            text=True,
            timeout=300,
            # A CHILD process cannot see this test's monkeypatch of
            # `mp.JOURNAL_DIR`: it is a fresh interpreter that imports the
            # module afresh. Without this the child read the shared journal and
            # recovered a DIFFERENT worktree's abandoned probe mid-suite, which
            # is exactly how this test file went red on a tree it had not
            # touched. The env var is the only channel that crosses the boundary.
            env={**os.environ, mp.JOURNAL_ENV_VAR: str(mp.JOURNAL_DIR)},
        )

    def test_a_killed_mutation_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _fixture(tmp, _SUITE)
            done = self._run(tmp, "LIMIT = 5", "LIMIT = 0")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn("[KILLED]", done.stdout)

    def test_a_surviving_mutation_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _fixture(tmp, _SUITE_UNDEFENDED)
            done = self._run(tmp, "LIMIT = 5", "LIMIT = 0")
            self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
            self.assertIn("[SURVIVED]", done.stdout)

    def test_a_refused_probe_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _fixture(tmp, _SUITE)
            done = self._run(tmp, "NOT_PRESENT = 1", "x")
            self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
            self.assertIn("[REFUSED]", done.stdout)

    def test_a_relative_path_resolves_against_the_callers_cwd(self) -> None:
        # `_resolve` was undefended too: returning `candidate` unchanged
        # SURVIVED. This tool is installed once and pointed at many different
        # target repos (`REPO_ROOT = Path.cwd()`, read at import time), so a
        # relative `--file`/`--suite` must resolve against the CALLER's cwd,
        # the target repo, never against wherever this tool itself happens
        # to be installed. Proven by running the CLI with its cwd pointed at
        # a scratch directory holding files at relative names this tool's
        # own install directory does not: if the resolution were still tied
        # to the tool's own location, the run would report "no file at" for
        # a path that does not exist there.
        with tempfile.TemporaryDirectory() as raw:
            target_dir = Path(raw)
            (target_dir / "scripts").mkdir()
            (target_dir / "scripts" / "thing.py").write_text("VALUE = 1\n", encoding="utf-8")
            (target_dir / "scripts" / "test_thing.py").write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_x(self) -> None:\n"
                "        self.assertEqual(1, 1)\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n",
                encoding="utf-8",
            )
            done = subprocess.run(
                (
                    sys.executable,
                    str(Path(__file__).resolve().parent / "mutation_probe.py"),
                    "--file", "scripts/thing.py",
                    "--old", "NOT_PRESENT_ANYWHERE_XYZ",
                    "--new", "x",
                    "--suite", "scripts/test_thing.py",
                ),
                cwd=str(target_dir),
                capture_output=True,
                text=True,
                timeout=300,
                # The same journal isolation `_run` documents above, which this
                # test alone lacked: without it the child resolves the REAL
                # journal, recovers any OUTER probe's live entry mid-flight, and
                # prints RECOVERED instead of `absent from the file`, so this
                # test reddened under EVERY default-journal probe of this repo
                # regardless of what was mutated (a step 9 review of the commit
                # that introduced it measured it 4 for 4). A per-test temp
                # journal removes both the
                # confound and the pollution of the real journal.
                env={**os.environ, mp.JOURNAL_ENV_VAR: str(target_dir / "jrnl")},
            )
            # REFUSED for the absent text, not for a missing file: proof the
            # relative paths were resolved against THIS cwd, not taken
            # literally and not resolved against the tool's own directory.
            self.assertIn("absent from the file", done.stdout, done.stdout + done.stderr)
            self.assertNotIn("no file at", done.stdout)

    def test_the_tests_flag_takes_the_fast_path_end_to_end(self) -> None:
        # `--tests` accepts one or more NAMED test ids and the CLI
        # actually wires them through to `probe(tests=...)`, proven as a real
        # child process rather than by reading the argparse definition.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _fixture(tmp, _SUITE)
            done = subprocess.run(
                (
                    sys.executable,
                    str(Path(__file__).resolve().parent / "mutation_probe.py"),
                    "--file", str(tmp / "mod_x.py"),
                    "--old", "LIMIT = 5",
                    "--new", "LIMIT = 0",
                    "--suite", str(tmp / "test_mod_x.py"),
                    "--floor", "2",
                    "--tests", "GuardTests.test_limit_is_five",
                ),
                capture_output=True,
                text=True,
                timeout=300,
                env={**os.environ, mp.JOURNAL_ENV_VAR: str(mp.JOURNAL_DIR)},
            )
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn("[KILLED]", done.stdout)
            self.assertIn("verdict path: fast", done.stdout)

    def test_the_tests_flag_accepts_MULTIPLE_ids(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _fixture(tmp, _SUITE)
            done = subprocess.run(
                (
                    sys.executable,
                    str(Path(__file__).resolve().parent / "mutation_probe.py"),
                    "--file", str(tmp / "mod_x.py"),
                    "--old", "LIMIT = 5",
                    "--new", "LIMIT = 0",
                    "--suite", str(tmp / "test_mod_x.py"),
                    "--floor", "2",
                    "--tests",
                    "GuardTests.test_over_uses_the_limit",
                    "GuardTests.test_limit_is_five",
                ),
                capture_output=True,
                text=True,
                timeout=300,
                env={**os.environ, mp.JOURNAL_ENV_VAR: str(mp.JOURNAL_DIR)},
            )
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn("[KILLED]", done.stdout)

    def test_an_ambiguous_old_is_refused_over_the_cli(self) -> None:
        # The ambiguous-occurrence refusal, exercised as a real child process the same way the
        # rest of this class proves the CLI contract rather than reading
        # the argparse definition.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _fixture(tmp, _SUITE_DUPLICATE_GUARD, _MODULE_DUPLICATE_GUARD)
            done = subprocess.run(
                (
                    sys.executable,
                    str(Path(__file__).resolve().parent / "mutation_probe.py"),
                    "--file", str(tmp / "mod_x.py"),
                    "--old", "if n > LIMIT:",
                    "--new", "if n < LIMIT:",
                    "--suite", str(tmp / "test_mod_x.py"),
                    "--floor", "4",
                ),
                capture_output=True,
                text=True,
                timeout=300,
                env={**os.environ, mp.JOURNAL_ENV_VAR: str(mp.JOURNAL_DIR)},
            )
            self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
            self.assertIn("[REFUSED]", done.stdout)
            self.assertIn("--nth", done.stdout)

    def test_the_nth_flag_disambiguates_over_the_cli(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _fixture(tmp, _SUITE_DUPLICATE_GUARD, _MODULE_DUPLICATE_GUARD)
            done = subprocess.run(
                (
                    sys.executable,
                    str(Path(__file__).resolve().parent / "mutation_probe.py"),
                    "--file", str(tmp / "mod_x.py"),
                    "--old", "if n > LIMIT:",
                    "--new", "if n < LIMIT:",
                    "--suite", str(tmp / "test_mod_x.py"),
                    "--floor", "4",
                    "--nth", "1",
                ),
                capture_output=True,
                text=True,
                timeout=300,
                env={**os.environ, mp.JOURNAL_ENV_VAR: str(mp.JOURNAL_DIR)},
            )
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn("[KILLED]", done.stdout)
            self.assertIn("test_check_low_high", done.stdout)

    def test_a_zero_timeout_prints_REFUSED_over_the_cli_with_no_traceback(self) -> None:
        # Step 9 finding L1 (a recorded repair): before the repair this raised a bare
        # `ValueError` out of `main()`, a Python traceback on stderr instead
        # of the `[REFUSED] ...` line and clean exit-1 verdict every other
        # refusal in this class prints.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _fixture(tmp, _SUITE)
            done = subprocess.run(
                (
                    sys.executable,
                    str(Path(__file__).resolve().parent / "mutation_probe.py"),
                    "--file", str(tmp / "mod_x.py"),
                    "--old", "LIMIT = 5",
                    "--new", "LIMIT = 0",
                    "--suite", str(tmp / "test_mod_x.py"),
                    "--floor", "2",
                    "--timeout", "0",
                ),
                capture_output=True,
                text=True,
                timeout=300,
                env={**os.environ, mp.JOURNAL_ENV_VAR: str(mp.JOURNAL_DIR)},
            )
            self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
            self.assertIn("[REFUSED]", done.stdout)
            self.assertNotIn("Traceback", done.stdout + done.stderr, done.stdout + done.stderr)
            self.assertNotIn("ValueError", done.stdout + done.stderr, done.stdout + done.stderr)


class InertOccurrenceTests(ProbeTestCase):
    """A mutation that lands only in a comment or a docstring is REFUSED.

    Otherwise the probe prints a confident `SURVIVED ... No test defends this
    behavior` about a run in which nothing was tested, which is this tool's own
    failure mode occurring inside this tool. It is a live trap rather than a
    theoretical one: this module's docstring quotes its own usage verbatim.
    """

    def test_text_only_in_a_comment_is_refused(self) -> None:
        source = "# LIMIT = 5 is the default\nOTHER = 1\n"
        total, in_code = mp.code_occurrences(source, "LIMIT = 5")
        self.assertEqual((total, in_code), (1, 0))

    def test_text_only_in_a_docstring_is_refused(self) -> None:
        source = '"""Usage: LIMIT = 5 becomes LIMIT = 0."""\nOTHER = 1\n'
        total, in_code = mp.code_occurrences(source, "LIMIT = 5")
        self.assertEqual((total, in_code), (1, 0))

    def test_text_in_code_and_in_a_comment_counts_the_code_one(self) -> None:
        source = "# LIMIT = 5 in prose\nLIMIT = 5\n"
        total, in_code = mp.code_occurrences(source, "LIMIT = 5")
        self.assertEqual((total, in_code), (2, 1))

    def test_unparsable_source_counts_every_occurrence_as_code(self) -> None:
        # Erring toward RUNNING the probe rather than refusing a legitimate one.
        source = "def broken(:\nLIMIT = 5\n"
        total, in_code = mp.code_occurrences(source, "LIMIT = 5")
        self.assertEqual((total, in_code), (1, 1))

    def test_unparsable_source_with_PROSE_BEFORE_the_error_still_counts_as_code(self) -> None:
        # The version above cannot tell `return []` from `return spans`,
        # because nothing inert precedes the syntax error, so both produce the
        # same value. With a docstring in front they differ, which is what
        # makes the documented failure behavior defended rather than described.
        source = '"""LIMIT = 5 in prose."""\ndef broken(:\nLIMIT = 5\n'
        total, in_code = mp.code_occurrences(source, "LIMIT = 5")
        self.assertEqual((total, in_code), (2, 2))

    def test_A_STRING_VALUE_IS_NOT_PROSE_and_stays_probeable(self) -> None:
        # THE REGRESSION A STEP 9 REVIEW MEASURED. Marking every STRING token
        # inert made 63 of 109 module-level string constants under scripts/
        # unprobeable by their own value, including this module's own
        # _LOADER_MARKER. A marker, a regex or a message literal is compared
        # against at runtime; mutating it changes behavior.
        source = 'MARKER = "unittest.loader._FailedTest"\n'
        self.assertEqual(mp.code_occurrences(source, "unittest.loader._FailedTest"), (1, 1))

    def test_the_real_module_can_still_probe_its_own_marker(self) -> None:
        # The specific instance, pinned against the live file rather than a
        # fixture, because the fixture is what got this wrong.
        source = (Path(__file__).resolve().parent / "mutation_probe.py").read_text(
            encoding="utf-8"
        )
        total, in_code = mp.code_occurrences(source, "unittest.loader._FailedTest")
        self.assertGreater(total, 0)
        self.assertGreater(in_code, 0, "the module's own loader marker must stay probeable")

    def test_an_f_string_value_is_treated_the_same_as_a_plain_one(self) -> None:
        # These tokenize differently on 3.12+ (FSTRING_START/MIDDLE/END versus
        # STRING), so a rule keyed on token type behaved differently for two
        # spellings of one thing. Keying on ast.Expr makes them agree.
        self.assertEqual(mp.code_occurrences('M = "LIMIT = 5 req"\n', "LIMIT = 5"), (1, 1))
        self.assertEqual(mp.code_occurrences('M = f"LIMIT = 5 req"\n', "LIMIT = 5"), (1, 1))

    def test_a_floating_string_statement_is_prose(self) -> None:
        source = 'def f():\n    x = 1\n    "LIMIT = 5 commentary"\n    return x\n'
        self.assertEqual(mp.code_occurrences(source, "LIMIT = 5"), (1, 0))

    def test_the_probe_refuses_a_docstring_only_mutation_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(
                tmp, _SUITE, module_src='"""Mentions LIMIT = 5."""\nLIMIT = 5\nCASES = 2\n\n\ndef over(n):\n    return n > LIMIT\n'
            )
            result = mp.probe(
                file=module,
                old="Mentions LIMIT = 5",
                new="Mentions LIMIT = 0",
                suite=suite,
                floor=2,
            )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertTrue(
                any("comments or string literals" in line for line in result.lines),
                "\n".join(result.lines),
            )


# Step 9 finding H. The reviewer's own fx2 fixture shape: a module docstring
# that QUOTES the constant under mutation, so `old` occurs once in code and
# once more as prose. `code_occurrences` already reports `(2, 1)` for this
# module; what was undefended is which of the two textual occurrences the
# mutation actually rewrites.
_MODULE_DOCSTRING_PIN = '''"""Module doc: CEILING = 9 is the documented ceiling."""
CEILING = 9


def over(n):
    return n > CEILING
'''

# ONLY a docstring-asserting test: no test here exercises `over` at all. This
# is the exact shape that fabricated a KILLED before the repair, because the
# global replace mutated the PROSE this test actually reads.
_SUITE_DOCSTRING_ONLY = """import unittest

import mod_x


class DocTests(unittest.TestCase):
    def test_the_docstring_quotes_the_ceiling(self):
        self.assertIn("CEILING = 9", mod_x.__doc__)


if __name__ == "__main__":
    unittest.main()
"""

# BOTH a docstring-asserting test AND a test that actually exercises the
# guard `over` defends. Mutating the code span must redden ONLY the guard
# test; the docstring test must stay green, proving the prose copy survived.
_SUITE_DOCSTRING_AND_GUARD = """import unittest

import mod_x


class DocTests(unittest.TestCase):
    def test_the_docstring_quotes_the_ceiling(self):
        self.assertIn("CEILING = 9", mod_x.__doc__)


class GuardTests(unittest.TestCase):
    def test_over_uses_the_ceiling(self):
        self.assertTrue(mod_x.over(10))
        self.assertFalse(mod_x.over(8))


if __name__ == "__main__":
    unittest.main()
"""


class DocstringNotMutatedTests(ProbeTestCase):
    """Step 9 finding H: `_apply`'s `nth is None, in_code == 1` path used a
    GLOBAL `text.replace`, so a pin unique IN CODE but also quoted in a
    module docstring mutated the docstring copy too. A test asserting on
    that prose then reddened for a guard nothing exercised, which is a
    fabricated verdict of exactly the class `probe-honesty` (`CLAUDE.md`)
    exists to prevent, produced by the probe-honesty tool itself. Both
    halves reproduced against the reviewer's own fx2 fixture shape."""

    def test_a_docstring_asserting_test_does_not_redden_under_the_default_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(
                tmp, _SUITE_DOCSTRING_AND_GUARD, _MODULE_DOCSTRING_PIN
            )
            result = mp.probe(
                file=module, old="CEILING = 9", new="CEILING = 0", suite=suite, floor=2
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            killed_line = next(line for line in result.lines if line.startswith("[KILLED]"))
            self.assertIn("test_over_uses_the_ceiling", killed_line)
            self.assertNotIn(
                "test_the_docstring_quotes_the_ceiling",
                killed_line,
                "the docstring-asserting test must NOT redden: " + killed_line,
            )

    def test_the_fabricated_KILLED_reproduction_flips_to_honest_SURVIVED(self) -> None:
        # No test here exercises `over` at all: before the repair, the global
        # `text.replace` mutated the docstring's prose copy too, and the
        # ONLY test in this suite asserts on that prose, so the probe
        # printed KILLED for a guard nothing defends. After the repair only
        # the code span is rewritten, the docstring copy is untouched, the
        # docstring test stays green, and the honest verdict is SURVIVED.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_DOCSTRING_ONLY, _MODULE_DOCSTRING_PIN)
            result = mp.probe(
                file=module, old="CEILING = 9", new="CEILING = 0", suite=suite, floor=1
            )
            self.assertEqual(result.verdict, mp.SURVIVED, "\n".join(result.lines))


class NonUtf8Tests(ProbeTestCase):
    def test_a_file_that_is_not_utf8_is_REFUSED_rather_than_raising(self) -> None:
        # A traceback is a fifth outcome the contract does not name.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _, suite = _fixture(tmp, _SUITE)
            binary = tmp / "binary.py"
            binary.write_bytes(b"x = 1\n# caf\xe9\n")
            result = mp.probe(
                file=binary, old="x = 1", new="x = 0", suite=suite, floor=2
            )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertTrue(
                any("not valid UTF-8" in line for line in result.lines),
                "\n".join(result.lines),
            )
            self.assertEqual(binary.read_bytes(), b"x = 1\n# caf\xe9\n")


# Two functions, deliberately, so the SAME literal text
# ("if n > LIMIT:") occurs twice in code, each guard defended by its own
# pair of tests. This is what makes `--nth` provable rather than merely
# accepted: mutating occurrence 1 only must redden `check_low`'s tests and
# leave `check_also`'s untouched, and vice versa for occurrence 2.
_MODULE_DUPLICATE_GUARD = """LIMIT = 5


def check_low(n):
    if n > LIMIT:
        return "high"
    return "low"


def check_also(n):
    if n > LIMIT:
        return "high-also"
    return "low-also"
"""

_SUITE_DUPLICATE_GUARD = """import unittest

import mod_x


class GuardTests(unittest.TestCase):
    def test_check_low_high(self):
        self.assertEqual(mod_x.check_low(6), "high")

    def test_check_low_low(self):
        self.assertEqual(mod_x.check_low(4), "low")

    def test_check_also_high(self):
        self.assertEqual(mod_x.check_also(6), "high-also")

    def test_check_also_low(self):
        self.assertEqual(mod_x.check_also(4), "low-also")


if __name__ == "__main__":
    unittest.main()
"""


class UniquenessRefusalTests(ProbeTestCase):
    """An `--old` that is not unique in code produces a REFUSAL,
    never a silent mutation of every occurrence at once, and `--nth` (or a
    lengthened `--old`) is the way out."""

    def test_an_ambiguous_old_is_REFUSED_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_DUPLICATE_GUARD, _MODULE_DUPLICATE_GUARD)
            before = module.read_bytes()
            result = mp.probe(
                file=module,
                old="if n > LIMIT:",
                new="if n < LIMIT:",
                suite=suite,
                floor=4,
            )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertTrue(
                any("--nth" in line for line in result.lines), "\n".join(result.lines)
            )
            self.assertTrue(
                any("lengthen --old" in line for line in result.lines),
                "the refusal must point at BOTH escape hatches: "
                + "\n".join(result.lines),
            )
            self.assertEqual(module.read_bytes(), before, "a refused probe must not write")

    def test_nth_1_mutates_ONLY_the_first_occurrence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_DUPLICATE_GUARD, _MODULE_DUPLICATE_GUARD)
            result = mp.probe(
                file=module,
                old="if n > LIMIT:",
                new="if n < LIMIT:",
                suite=suite,
                floor=4,
                nth=1,
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            killed_line = next(line for line in result.lines if line.startswith("[KILLED]"))
            self.assertIn("test_check_low_high", killed_line)
            self.assertIn("test_check_low_low", killed_line)
            self.assertNotIn("test_check_also", killed_line)

    def test_nth_2_mutates_ONLY_the_second_occurrence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_DUPLICATE_GUARD, _MODULE_DUPLICATE_GUARD)
            result = mp.probe(
                file=module,
                old="if n > LIMIT:",
                new="if n < LIMIT:",
                suite=suite,
                floor=4,
                nth=2,
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            killed_line = next(line for line in result.lines if line.startswith("[KILLED]"))
            self.assertIn("test_check_also_high", killed_line)
            self.assertIn("test_check_also_low", killed_line)
            self.assertNotIn("test_check_low", killed_line)

    def test_nth_out_of_range_is_REFUSED_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_DUPLICATE_GUARD, _MODULE_DUPLICATE_GUARD)
            result = mp.probe(
                file=module,
                old="if n > LIMIT:",
                new="if n < LIMIT:",
                suite=suite,
                floor=4,
                nth=3,
            )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertTrue(
                any("--nth 3" in line for line in result.lines), "\n".join(result.lines)
            )

    def test_a_lengthened_old_disambiguates_without_nth(self) -> None:
        # The OTHER escape hatch named in the refusal message: more
        # surrounding text makes `--old` unique on its own, and that idiom
        # (used throughout this repo's real `defended_by` history) must
        # keep working exactly as it did before this item. Written with
        # `newline="\n"` explicitly, the same way this file's own Rust and
        # other multi-line fixtures already do: Windows `Path.write_text`
        # otherwise translates `\n` to `\r\n` on disk, and a multi-line
        # `--old` containing a bare `\n` would then never match.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module = tmp / "mod_x.py"
            suite = tmp / "test_mod_x.py"
            module.write_text(_MODULE_DUPLICATE_GUARD, encoding="utf-8", newline="\n")
            suite.write_text(_SUITE_DUPLICATE_GUARD, encoding="utf-8", newline="\n")
            result = mp.probe(
                file=module,
                old='def check_low(n):\n    if n > LIMIT:',
                new='def check_low(n):\n    if n < LIMIT:',
                suite=suite,
                floor=4,
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            killed_line = next(line for line in result.lines if line.startswith("[KILLED]"))
            self.assertIn("test_check_low_high", killed_line)
            self.assertNotIn("test_check_also", killed_line)

    def test_the_mutation_applied_line_names_the_selected_site_for_nth_1(self) -> None:
        # Step 9 finding L2: before this, `mutation applied:` printed only
        # `old -> new` plus digests, so a reader of a `defended_by` entry
        # could not tell a `--nth 1` run from a `--nth 2` run without
        # re-running the tool or diffing digests by hand. `check_low`'s
        # guard is at line 5 of `_MODULE_DUPLICATE_GUARD`.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_DUPLICATE_GUARD, _MODULE_DUPLICATE_GUARD)
            result = mp.probe(
                file=module,
                old="if n > LIMIT:",
                new="if n < LIMIT:",
                suite=suite,
                floor=4,
                nth=1,
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            applied_line = next(
                line for line in result.lines if line.startswith("mutation applied:")
            )
            self.assertIn("occurrence 1 of 2 in code", applied_line, applied_line)
            self.assertIn("line 5", applied_line, applied_line)

    def test_the_mutation_applied_line_names_the_selected_site_for_nth_2(self) -> None:
        # The OTHER site: `check_also`'s guard is at line 11, so this must
        # read differently from the `--nth 1` line above, proving the text
        # names the SELECTED occurrence rather than a constant.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_DUPLICATE_GUARD, _MODULE_DUPLICATE_GUARD)
            result = mp.probe(
                file=module,
                old="if n > LIMIT:",
                new="if n < LIMIT:",
                suite=suite,
                floor=4,
                nth=2,
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            applied_line = next(
                line for line in result.lines if line.startswith("mutation applied:")
            )
            self.assertIn("occurrence 2 of 2 in code", applied_line, applied_line)
            self.assertIn("line 11", applied_line, applied_line)

    def test_the_mutation_applied_line_names_no_site_when_nth_is_omitted(self) -> None:
        # The disclosure is scoped to an EXPLICIT `--nth`: the ordinary
        # unique-occurrence path (no ambiguity, no `--nth`) must not grow a
        # new clause nothing asked for.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            applied_line = next(
                line for line in result.lines if line.startswith("mutation applied:")
            )
            self.assertNotIn("occurrence", applied_line, applied_line)


class CapabilityWideningTests(ProbeTestCase):
    """The other half of the ambiguity refusal: a `--new` that WIDENS capability instead of
    DISABLING it produces a KILLED-shaped nothing exactly the way an
    ambiguous `--old` does. `capability_widening_match` is a PURE function
    handed its input, so per `CLAUDE.md`'s known-bad rule its known-bads are
    crafted INPUTS; the end-to-end cases below drive the same check through
    `probe()`."""

    def test_if_True_is_matched(self) -> None:
        self.assertEqual(mp.capability_widening_match("if True:"), "if True:")

    def test_if_1_is_matched(self) -> None:
        self.assertEqual(mp.capability_widening_match("if 1:"), "if 1:")

    def test_if_not_False_is_matched(self) -> None:
        self.assertEqual(
            mp.capability_widening_match("if not False:"), "if not False:"
        )

    def test_the_rust_if_true_brace_spelling_is_matched(self) -> None:
        self.assertEqual(mp.capability_widening_match("if true {"), "if true {")

    def test_whitespace_around_the_shape_is_stripped_before_matching(self) -> None:
        self.assertEqual(mp.capability_widening_match("  if True:  \n"), "if True:")

    def test_the_KNOWN_BAD_prescribed_disabling_shape_is_NOT_matched(self) -> None:
        # `trace-the-known-bad`'s own idiom must keep working: this is
        # exactly the mutation the rule prescribes, not the one it warns
        # against.
        self.assertIsNone(mp.capability_widening_match("if False:"))
        self.assertIsNone(mp.capability_widening_match("if false {"))

    def test_an_unrelated_new_is_NOT_matched(self) -> None:
        self.assertIsNone(mp.capability_widening_match("LIMIT = 0"))

    def test_if_True_as_a_MENTION_inside_longer_text_is_NOT_matched(self) -> None:
        # Only the FULL trimmed text of `new` is checked; a `new` that
        # merely CONTAINS the shape inside unrelated surrounding text is a
        # different, legitimate replacement and must not be refused.
        self.assertIsNone(
            mp.capability_widening_match("x = 1  # was: if True: once")
        )

    def test_the_probe_refuses_an_if_True_mutation_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            before = module.read_bytes()
            result = mp.probe(
                file=module, old="LIMIT = 5", new="if True:", suite=suite, floor=2
            )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertTrue(
                any("CAPABILITY-WIDENING" in line for line in result.lines),
                "\n".join(result.lines),
            )
            self.assertTrue(
                any("if False" in line for line in result.lines),
                "the refusal must point at the disabling shape instead: "
                + "\n".join(result.lines),
            )
            self.assertEqual(module.read_bytes(), before, "a refused probe must not write")

    def test_the_prescribed_disabling_mutation_reaches_a_real_verdict(self) -> None:
        # Positive control: the ordinary trace-the-known-bad idiom must
        # reach KILLED, not be caught by this refusal.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))

    def test_a_capability_widening_new_is_refused_over_the_cli(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _fixture(tmp, _SUITE)
            done = subprocess.run(
                (
                    sys.executable,
                    str(Path(__file__).resolve().parent / "mutation_probe.py"),
                    "--file", str(tmp / "mod_x.py"),
                    "--old", "LIMIT = 5",
                    "--new", "if True:",
                    "--suite", str(tmp / "test_mod_x.py"),
                    "--floor", "2",
                ),
                capture_output=True,
                text=True,
                timeout=300,
                env={**os.environ, mp.JOURNAL_ENV_VAR: str(mp.JOURNAL_DIR)},
            )
            self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
            self.assertIn("[REFUSED]", done.stdout)
            self.assertIn("CAPABILITY-WIDENING", done.stdout)


class TimeoutIsPassedThroughTests(ProbeTestCase):
    """`check_hermetic_bounds` accepts any `timeout=` keyword including
    `timeout=None`, so the gate cannot tell a real bound from a fake one. The
    value reaching `subprocess.run` is asserted here instead."""

    def test_run_suite_passes_the_timeout_to_the_child(self) -> None:
        seen: list[object] = []
        real = mp.subprocess.run

        def spy(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            seen.append(kwargs.get("timeout"))
            return real(*args, **kwargs)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _, suite = _fixture(tmp, _SUITE)
            mp.subprocess.run = spy  # type: ignore[assignment]
            try:
                mp.run_suite(suite, timeout=123)
            finally:
                mp.subprocess.run = real  # type: ignore[assignment]

        self.assertEqual(seen, [123])

    def test_a_timed_out_suite_is_no_transcript_and_never_a_result(self) -> None:
        # Unpinned, this returned `UnittestRun(ran=1, ok=True)` under mutation
        # and a timed-out suite would have been scored as a green one-test run.
        # That is the "score a run that did not happen" class by name.
        real = mp.subprocess.run

        def boom(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            raise subprocess.TimeoutExpired(cmd="x", timeout=1)

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _, suite = _fixture(tmp, _SUITE)
            mp.subprocess.run = boom  # type: ignore[assignment]
            try:
                run = mp.run_suite(suite, timeout=1)
            finally:
                mp.subprocess.run = real  # type: ignore[assignment]

        self.assertIsNone(run.ran)
        self.assertIsNone(run.ok)
        self.assertFalse(run.is_transcript)

    def test_a_timed_out_PYTHON_suite_is_flagged_timed_out_with_elapsed(self) -> None:
        # `is_transcript is False` alone cannot distinguish a HANG
        # from a crash or a compile error; `timed_out` is the flag that lets
        # `probe()` tell them apart and report INCONCLUSIVE rather than the
        # generic NO RUN a crash deserves.
        real = mp.subprocess.run

        def boom(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            raise subprocess.TimeoutExpired(cmd="x", timeout=1)

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _, suite = _fixture(tmp, _SUITE)
            mp.subprocess.run = boom  # type: ignore[assignment]
            try:
                run = mp.run_suite(suite, timeout=1)
            finally:
                mp.subprocess.run = real  # type: ignore[assignment]

        self.assertTrue(run.timed_out)
        self.assertIsNotNone(run.elapsed_seconds)
        assert run.elapsed_seconds is not None  # narrows for mypy/the assert below
        self.assertGreaterEqual(run.elapsed_seconds, 0.0)

    def test_a_CARGO_timeout_whose_captured_streams_are_BYTES_still_yields_a_str_tail(self) -> None:
        # A real CI failure, replayed at the handler that actually raised.
        # CPython never decodes a `TimeoutExpired`'s captured streams even
        # under `text=True` (gh-87597), so `exc.output`/`exc.stderr` are
        # BYTES whenever the child spoke before the kill; every local hang
        # fixture captured nothing (`None`), which is why the shape first
        # appeared on the ubuntu runner and never on this box. The python
        # backend's own handler discards the capture and cannot raise.
        real_which = mp.shutil.which
        real_run = mp.subprocess.run

        def fake_which(name: str) -> str | None:
            return "cargo" if name == "cargo" else real_which(name)

        def boom(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            raise subprocess.TimeoutExpired(
                cmd="cargo", timeout=1, output=b"running 1 test\n", stderr=b"warning: unused\n"
            )

        mp.shutil.which = fake_which  # type: ignore[assignment]
        mp.subprocess.run = boom  # type: ignore[assignment]
        try:
            run = mp.run_cargo_suite(Path("Cargo.toml"), timeout=1)
        finally:
            mp.shutil.which = real_which  # type: ignore[assignment]
            mp.subprocess.run = real_run  # type: ignore[assignment]

        self.assertTrue(run.timed_out)
        self.assertIsInstance(run.transcript_tail, str)
        self.assertIn("running 1 test", run.transcript_tail)
        self.assertIn("warning: unused", run.transcript_tail)

    def test_timeout_capture_decodes_bytes_skips_none_and_passes_str_through(self) -> None:
        # Direct pin on the decoder itself: undecodable bytes become U+FFFD
        # rather than raising, `None` streams contribute nothing, and a str
        # stream (the mocked-fixture shape every pre-existing test uses)
        # passes through unchanged.
        exc = subprocess.TimeoutExpired(cmd="x", timeout=1, output=b"a\xffb", stderr=None)
        self.assertEqual(mp._timeout_capture(exc), "a�b")
        mixed = subprocess.TimeoutExpired(cmd="x", timeout=1, output="s1", stderr=b"s2")
        self.assertEqual(mp._timeout_capture(mixed), "s1s2")

    def test_an_ordinary_completed_PYTHON_run_is_NOT_flagged_timed_out(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _, suite = _fixture(tmp, _SUITE)
            run = mp.run_suite(suite, timeout=60)
        self.assertFalse(run.timed_out)
        self.assertIsNone(run.elapsed_seconds)

    def test_a_timed_out_CARGO_suite_is_flagged_timed_out_too(self) -> None:
        # The SAME flag, set by the OTHER backend: `probe()`'s INCONCLUSIVE
        # branch reads `UnittestRun.timed_out` regardless of which parser
        # produced it, so both call sites that catch `TimeoutExpired` must
        # wire the flag, not just the Python one.
        real_which = mp.shutil.which
        real_run = mp.subprocess.run

        def fake_which(name: str) -> str | None:
            return "cargo" if name == "cargo" else real_which(name)

        def boom(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            raise subprocess.TimeoutExpired(cmd="cargo", timeout=1)

        mp.shutil.which = fake_which  # type: ignore[assignment]
        mp.subprocess.run = boom  # type: ignore[assignment]
        try:
            run = mp.run_cargo_suite(Path("Cargo.toml"), timeout=1)
        finally:
            mp.shutil.which = real_which  # type: ignore[assignment]
            mp.subprocess.run = real_run  # type: ignore[assignment]

        self.assertTrue(run.timed_out)
        self.assertIsNotNone(run.elapsed_seconds)
        self.assertFalse(run.is_transcript)

    def test_a_CARGO_OSError_is_NOT_flagged_timed_out(self) -> None:
        # KNOWN-BAD discrimination: `cargo` vanishing mid-run (an `OSError`)
        # is a NO RUN, not a hang. Conflating the two except clauses was the
        # defect: both used to return the identical `UnittestRun(ran=None,
        # ok=None)`, so nothing downstream could tell them apart.
        real_which = mp.shutil.which
        real_run = mp.subprocess.run

        def fake_which(name: str) -> str | None:
            return "cargo" if name == "cargo" else real_which(name)

        def boom(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            raise OSError("cargo vanished")

        mp.shutil.which = fake_which  # type: ignore[assignment]
        mp.subprocess.run = boom  # type: ignore[assignment]
        try:
            run = mp.run_cargo_suite(Path("Cargo.toml"), timeout=1)
        finally:
            mp.shutil.which = real_which  # type: ignore[assignment]
            mp.subprocess.run = real_run  # type: ignore[assignment]

        self.assertFalse(run.timed_out)
        self.assertFalse(run.is_transcript)

    def test_the_default_timeout_is_the_named_constant(self) -> None:
        seen: list[object] = []
        real = mp.subprocess.run

        def spy(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            seen.append(kwargs.get("timeout"))
            return real(*args, **kwargs)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _, suite = _fixture(tmp, _SUITE)
            mp.subprocess.run = spy  # type: ignore[assignment]
            try:
                mp.run_suite(suite)
            finally:
                mp.subprocess.run = real  # type: ignore[assignment]

        self.assertEqual(seen, [mp.SUITE_TIMEOUT_SECONDS])
        self.assertIsNotNone(seen[0], "timeout=None would satisfy the gate and bound nothing")


class RunSuiteTimeoutGuardTests(ProbeTestCase):
    """A `None` or non-positive `timeout` never reaches
    `subprocess.run`; `run_suite` raises before either backend is even
    dispatched, rather than passing it through into an unbounded wait."""

    def test_a_None_timeout_is_refused_not_passed_through(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _, suite = _fixture(tmp, _SUITE)
            with self.assertRaises(ValueError):
                mp.run_suite(suite, timeout=None)  # type: ignore[arg-type]

    def test_a_zero_timeout_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _, suite = _fixture(tmp, _SUITE)
            with self.assertRaises(ValueError):
                mp.run_suite(suite, timeout=0)

    def test_a_negative_timeout_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _, suite = _fixture(tmp, _SUITE)
            with self.assertRaises(ValueError):
                mp.run_suite(suite, timeout=-5)

    def test_the_refusal_never_reaches_subprocess_run(self) -> None:
        # The named failure mode this item exists to close: a bad timeout
        # that DOES reach subprocess.run silently waits forever instead of
        # raising in place, which is the recorded incident verbatim
        # (mutating either clock-resolution fallback left `WiringTests`
        # green while `InconclusiveTimeoutTests` hung past its own bound).
        real = mp.subprocess.run
        called: list[object] = []

        def spy(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            called.append(kwargs.get("timeout"))
            return real(*args, **kwargs)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _, suite = _fixture(tmp, _SUITE)
            mp.subprocess.run = spy  # type: ignore[assignment]
            try:
                with self.assertRaises(ValueError):
                    mp.run_suite(suite, timeout=None)  # type: ignore[arg-type]
            finally:
                mp.subprocess.run = real  # type: ignore[assignment]

        self.assertEqual(
            called, [], "subprocess.run must never be reached with a bad timeout"
        )

    def test_a_positive_timeout_is_unaffected(self) -> None:
        # Positive control: the guard must not refuse ordinary, valid calls.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _, suite = _fixture(tmp, _SUITE)
            run = mp.run_suite(suite, timeout=60)
        self.assertTrue(run.is_transcript)


class ProbeBoundaryTimeoutTests(ProbeTestCase):
    """Step 9 finding L1: `run_suite`'s `ValueError` (the class above pins)
    escaped `probe()` and `main()` uncaught, so a non-positive `--timeout`
    produced a Python traceback instead of the `[REFUSED] ...` line and
    clean exit-1 verdict this module promises everywhere else. `probe()` is
    the ONE boundary that catches it and converts it to `REFUSED`; `run_suite`
    itself keeps raising, which `RunSuiteTimeoutGuardTests` above still pins
    unchanged."""

    def test_probe_with_a_zero_timeout_is_REFUSED_not_raised(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            result = mp.probe(
                file=module,
                old="LIMIT = 5",
                new="LIMIT = 0",
                suite=suite,
                floor=2,
                timeout=0,
            )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertTrue(
                any("timeout must be a positive" in line for line in result.lines),
                "\n".join(result.lines),
            )

    def test_probe_with_a_negative_timeout_is_REFUSED_not_raised(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            result = mp.probe(
                file=module,
                old="LIMIT = 5",
                new="LIMIT = 0",
                suite=suite,
                floor=2,
                timeout=-5,
            )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))

    def test_a_zero_timeout_reaching_run_suite_INSIDE_THE_MUTATED_WINDOW_is_also_REFUSED(
        self,
    ) -> None:
        # The reviewer's own reproduction: `narrow_baseline_timeout=60` gives
        # the fast path's narrow BASELINE a clean, positive clock, so the
        # raise happens only on the MUTATED run inside
        # `_mutate_run_restore`, after the mutation was already written to
        # `module`. This proves the boundary catches it there too, and that
        # the file still comes back byte-exact even though the exception
        # crossed `_mutate_run_restore`'s own frame.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            before = module.read_bytes()
            result = mp.probe(
                file=module,
                old="LIMIT = 5",
                new="LIMIT = 0",
                suite=suite,
                floor=2,
                timeout=0,
                tests=("GuardTests.test_limit_is_five",),
                narrow_baseline_timeout=60,
            )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertEqual(
                module.read_bytes(), before, "a REFUSED-by-exception run must not leave a mutant"
            )


class _FakeCompleted:
    """The two attributes `run_cargo_suite` reads off `subprocess.run`'s
    return value. A real `CompletedProcess` carries more; nothing here reads
    more than this."""

    def __init__(self, stdout: str, stderr: str) -> None:
        self.stdout = stdout
        self.stderr = stderr


_CARGO_OK_TAIL = (
    "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\n"
)


class TailTextTests(ProbeTestCase):
    """`_tail_text` is handed its input, so its known-bad is crafted text."""

    def test_text_shorter_than_the_limit_is_returned_whole(self) -> None:
        self.assertEqual(mp._tail_text("a\nb\nc", lines=20), "a\nb\nc")

    def test_text_longer_than_the_limit_is_truncated_to_the_last_N_lines(self) -> None:
        text = "\n".join(f"L{i}" for i in range(1, 41))
        tail = mp._tail_text(text, lines=5)
        self.assertEqual(tail, "L36\nL37\nL38\nL39\nL40")
        self.assertNotIn("L35", tail)

    def test_empty_text_returns_empty_not_a_lone_newline(self) -> None:
        # KNOWN-BAD: `"\n".join([])` on a naive implementation would still be
        # `""`, but a caller checking truthiness before ever calling this
        # deserves the guarantee stated outright, not inferred.
        self.assertEqual(mp._tail_text(""), "")


class CargoTailClauseTests(ProbeTestCase):
    """`_cargo_tail_clause` is handed its input too."""

    def test_the_python_backend_never_shows_a_tail_even_if_one_is_set(self) -> None:
        # KNOWN-BAD: a `UnittestRun` that happens to carry a `transcript_tail`
        # (which `run_cargo_suite` populates, but nothing stops a caller from
        # constructing one by hand) must still print nothing when `cargo` is
        # False, since the Python backend's own transcript already appears
        # in full via the messages this clause is appended to.
        run = mp.UnittestRun(ran=1, ok=False, transcript_tail="unexpected")
        self.assertEqual(mp._cargo_tail_clause(False, run), "")

    def test_a_cargo_run_with_nothing_captured_prints_nothing(self) -> None:
        run = mp.UnittestRun(ran=None, ok=None)
        self.assertEqual(mp._cargo_tail_clause(True, run), "")

    def test_a_cargo_run_with_a_tail_prints_it_and_names_the_limit(self) -> None:
        run = mp.UnittestRun(ran=None, ok=None, transcript_tail="LNK1104 boom")
        clause = mp._cargo_tail_clause(True, run)
        self.assertIn("LNK1104 boom", clause)
        self.assertIn(str(mp.TRANSCRIPT_TAIL_LINES), clause)


class CargoLNK1104RetryTests(ProbeTestCase):
    """A bounded, ONCE-only retry of the cargo build/run when the
    transcript carries `LNK1104`. Mocked at `subprocess.run`, the same seam
    `TimeoutIsPassedThroughTests` above already uses, because the point under
    test is the RETRY COUNT and which attempt's text wins - not cargo itself,
    which the real-cargo tests elsewhere in this module already exercise."""

    def _patch(self, run_fn: object) -> tuple[object, object]:
        real_which = mp.shutil.which
        real_run = mp.subprocess.run

        def fake_which(name: str) -> str | None:
            return "cargo" if name == "cargo" else real_which(name)

        mp.shutil.which = fake_which  # type: ignore[assignment]
        mp.subprocess.run = run_fn  # type: ignore[assignment]
        return real_which, real_run

    def _unpatch(self, saved: tuple[object, object]) -> None:
        real_which, real_run = saved
        mp.shutil.which = real_which  # type: ignore[assignment]
        mp.subprocess.run = real_run  # type: ignore[assignment]

    def test_an_LNK1104_failure_is_retried_once_and_a_clean_retry_wins(self) -> None:
        calls: list[int] = []

        def fake_run(*args: object, **kwargs: object) -> _FakeCompleted:
            calls.append(1)
            if len(calls) == 1:
                return _FakeCompleted(
                    stdout="",
                    stderr="LINK : fatal error LNK1104: cannot open file 'probefixture.exe'\n",
                )
            return _FakeCompleted(stdout=_CARGO_OK_TAIL, stderr="")

        saved = self._patch(fake_run)
        try:
            run = mp.run_cargo_suite(Path("Cargo.toml"), timeout=60)
        finally:
            self._unpatch(saved)

        self.assertEqual(len(calls), 2, "exactly one retry, not zero and not more")
        self.assertEqual(run.ran, 1)
        self.assertTrue(run.ok)

    def test_a_persistent_LNK1104_failure_still_reports_NO_RUN_after_exactly_one_retry(
        self,
    ) -> None:
        calls: list[int] = []

        def fake_run(*args: object, **kwargs: object) -> _FakeCompleted:
            calls.append(1)
            return _FakeCompleted(
                stdout="",
                stderr=f"LINK : fatal error LNK1104: cannot open file 'probefixture.exe' (attempt {len(calls)})\n",
            )

        saved = self._patch(fake_run)
        try:
            run = mp.run_cargo_suite(Path("Cargo.toml"), timeout=60)
        finally:
            self._unpatch(saved)

        # NOT retried a SECOND time: a build that is genuinely, persistently
        # broken must still report the honest NO RUN, never loop.
        self.assertEqual(len(calls), 2)
        self.assertIsNone(run.ran)
        self.assertFalse(run.is_transcript)
        self.assertIn("LNK1104", run.transcript_tail)
        # The RETRY's own text is what the tail carries, not the first
        # attempt's - the reader wants to see the LATEST evidence.
        self.assertIn("attempt 2", run.transcript_tail)

    def test_an_ordinary_compile_error_is_not_retried_at_all(self) -> None:
        # KNOWN-BAD for the retry's own trigger condition (`trace-the-known-
        # bad`): text with no `LNK1104` anywhere must never cost a second
        # cargo invocation, or a genuine, deterministic compile error would
        # pay double the cost for nothing.
        calls: list[int] = []

        def fake_run(*args: object, **kwargs: object) -> _FakeCompleted:
            calls.append(1)
            return _FakeCompleted(
                stdout="error[E0277]: the trait bound `Widget: Default` is not satisfied\n",
                stderr="",
            )

        saved = self._patch(fake_run)
        try:
            run = mp.run_cargo_suite(Path("Cargo.toml"), timeout=60)
        finally:
            self._unpatch(saved)

        self.assertEqual(len(calls), 1, "an ordinary compile error must not retry")
        self.assertIsNone(run.ran)

    def test_an_ordinary_green_run_carries_its_own_tail_too(self) -> None:
        # The tail is populated on EVERY cargo run, not only failing ones
        # (the "wherever ... conclude NO RUN or Unviable" instruction
        # is about which MESSAGES show it, not about which runs capture it).
        def fake_run(*args: object, **kwargs: object) -> _FakeCompleted:
            return _FakeCompleted(stdout=_CARGO_OK_TAIL, stderr="")

        saved = self._patch(fake_run)
        try:
            run = mp.run_cargo_suite(Path("Cargo.toml"), timeout=60)
        finally:
            self._unpatch(saved)

        self.assertTrue(run.ok)
        self.assertIn("test result: ok", run.transcript_tail)

    def test_a_timeout_on_the_retry_itself_is_still_flagged_timed_out(self) -> None:
        # The retry re-enters the SAME try/except shape as the first
        # attempt; this proves that shape rather than assuming it.
        calls: list[int] = []

        def fake_run(*args: object, **kwargs: object) -> _FakeCompleted:
            calls.append(1)
            if len(calls) == 1:
                return _FakeCompleted(
                    stdout="", stderr="LINK : fatal error LNK1104: cannot open file 'x.exe'\n"
                )
            raise subprocess.TimeoutExpired(cmd="cargo", timeout=60)

        saved = self._patch(fake_run)
        try:
            run = mp.run_cargo_suite(Path("Cargo.toml"), timeout=60)
        finally:
            self._unpatch(saved)

        self.assertEqual(len(calls), 2)
        self.assertTrue(run.timed_out)
        self.assertIsNone(run.ran)

    def test_a_retry_timeout_with_BYTES_streams_still_yields_a_str_tail(self) -> None:
        # A post-push hotfix review (its one MEDIUM): the RETRY
        # handler's `_timeout_capture` wiring SURVIVED mutation because no
        # test drove the LNK1104-then-timeout path with the CI shape, so a
        # revert of that one line would reproduce a real CI run's TypeError
        # and be caught by nothing local. This is that missing test: attempt
        # one completes carrying LNK1104, the retry raises `TimeoutExpired`
        # whose captured streams are BYTES (gh-87597's real-child shape).
        calls: list[int] = []

        def fake_run(*args: object, **kwargs: object) -> _FakeCompleted:
            calls.append(1)
            if len(calls) == 1:
                return _FakeCompleted(
                    stdout="", stderr="LINK : fatal error LNK1104: cannot open file 'x.exe'\n"
                )
            raise subprocess.TimeoutExpired(
                cmd="cargo", timeout=60, output=b"running 9 tests\n", stderr=b"warning: retry\n"
            )

        saved = self._patch(fake_run)
        try:
            run = mp.run_cargo_suite(Path("Cargo.toml"), timeout=60)
        finally:
            self._unpatch(saved)

        self.assertEqual(len(calls), 2)
        self.assertTrue(run.timed_out)
        self.assertIsInstance(run.transcript_tail, str)
        self.assertIn("running 9 tests", run.transcript_tail)
        self.assertIn("warning: retry", run.transcript_tail)

    def test_a_timeout_on_the_retry_reports_elapsed_from_the_FIRST_attempts_clock(
        self,
    ) -> None:
        # step 9 round 1 finding L2 (from a recorded review report): the
        # retry's TimeoutExpired handler used to measure `elapsed_seconds`
        # from a clock started AFTER the LNK1104 retry began, so the NO-RUN
        # line's "took {elapsed}s (bound {timeout}s)" understated the true
        # wall time by up to `timeout` seconds. `time.monotonic` is mocked to
        # a fixed two-call sequence: the first call is `run_cargo_suite`'s own
        # `started`, the second is the elapsed computation in the retry's
        # exception handler. Exactly two calls on this path proves there is
        # no THIRD clock read (a reintroduced `retry_started`) in between.
        def fake_run(*args: object, **kwargs: object) -> _FakeCompleted:
            return _FakeCompleted(
                stdout="", stderr="LINK : fatal error LNK1104: cannot open file 'x.exe'\n"
            )

        def fake_retry_run(*args: object, **kwargs: object) -> _FakeCompleted:
            raise subprocess.TimeoutExpired(cmd="cargo", timeout=60)

        calls: list[int] = []

        def dispatch(*args: object, **kwargs: object) -> _FakeCompleted:
            calls.append(1)
            if len(calls) == 1:
                return fake_run(*args, **kwargs)
            return fake_retry_run(*args, **kwargs)

        saved = self._patch(dispatch)
        try:
            with unittest.mock.patch.object(
                mp.time, "monotonic", side_effect=[1000.0, 1350.0]
            ):
                run = mp.run_cargo_suite(Path("Cargo.toml"), timeout=60)
        finally:
            self._unpatch(saved)

        self.assertEqual(len(calls), 2)
        self.assertTrue(run.timed_out)
        # 1350.0 - 1000.0: the SECOND `monotonic()` call minus the FIRST,
        # i.e. wall time since `run_cargo_suite` began, not since the retry
        # started. A reintroduced `retry_started` clock would need a THIRD
        # `monotonic()` call the mocked two-value sequence does not supply,
        # so that regression fails loudly here rather than merely reporting
        # a smaller (wrong) number silently.
        self.assertEqual(run.elapsed_seconds, 350.0)


class CargoBoundNoteTests(ProbeTestCase):
    """step 9 round 2 finding M (from that same recorded review):
    `_cargo_bound_note` carried no caller-visible test at all. Deleting its
    disclosure text (`return " (a cargo run's true bound is up to 2x this "
    "if an LNK1104 relink retry fired)"` mutated to `return ""`) left the
    whole 168-test suite green [SURVIVED]. These pin the text INSIDE the
    message a real cargo TIMED OUT verdict prints, on both call sites that
    reach it under `cargo=True` (the full-suite mutant TIMED OUT branch and
    the fast-path mutant TIMED OUT branch, finding L's own repair), and its
    ABSENCE on the Python backend, which has no retry to disclose. Mocked at
    `subprocess.run`/`shutil.which`, the same seam `CargoLNK1104RetryTests`
    above uses: the point under test is the MESSAGE TEXT, not cargo itself."""

    def _patch(self, run_fn: object) -> tuple[object, object]:
        real_which = mp.shutil.which
        real_run = mp.subprocess.run

        def fake_which(name: str) -> str | None:
            return "cargo" if name == "cargo" else real_which(name)

        def dispatch(*args: object, **kwargs: object) -> object:
            # `probe()` opens with `tree_identity_line`, which shells out to
            # `git rev-parse HEAD` through this SAME `subprocess.run` seam
            # before the suite it is testing ever runs. Routed to the REAL
            # runner rather than `run_fn`, which knows only cargo/Python
            # suite shapes and would otherwise choke on `.returncode`; the
            # tmp fixture directory is outside any repo, so this reports the
            # honest `UNDETERMINABLE`, exactly as it would for any other
            # probe run from a fresh scratch directory.
            argv = args[0] if args else kwargs.get("args")
            if isinstance(argv, (tuple, list)) and argv and argv[0] == "git":
                return real_run(*args, **kwargs)
            return run_fn(*args, **kwargs)  # type: ignore[operator]

        mp.shutil.which = fake_which  # type: ignore[assignment]
        mp.subprocess.run = dispatch  # type: ignore[assignment]
        return real_which, real_run

    def _unpatch(self, saved: tuple[object, object]) -> None:
        real_which, real_run = saved
        mp.shutil.which = real_which  # type: ignore[assignment]
        mp.subprocess.run = real_run  # type: ignore[assignment]

    def test_a_cargo_mutant_TIMED_OUT_message_carries_the_disclosure(self) -> None:
        calls: list[int] = []

        def fake_run(*args: object, **kwargs: object) -> _FakeCompleted:
            calls.append(1)
            if len(calls) == 1:
                return _FakeCompleted(stdout=_CARGO_OK_TAIL, stderr="")
            raise subprocess.TimeoutExpired(cmd="cargo", timeout=60)

        saved = self._patch(fake_run)
        try:
            with tempfile.TemporaryDirectory() as raw:
                tmp = Path(raw)
                manifest = tmp / "Cargo.toml"
                manifest.write_text(_RUST_MANIFEST, encoding="utf-8", newline="\n")
                lib = tmp / "lib.rs"
                lib.write_text("pub const X: u64 = 0;\n", encoding="utf-8", newline="\n")
                result = mp.probe(
                    file=lib,
                    old="pub const X: u64 = 0;",
                    new="pub const X: u64 = 1;",
                    suite=manifest,
                    floor=1,
                    timeout=60,
                )
        finally:
            self._unpatch(saved)

        self.assertEqual(len(calls), 2, "\n".join(result.lines))
        self.assertEqual(result.verdict, mp.INCONCLUSIVE, "\n".join(result.lines))
        timed_out_lines = [
            line for line in result.lines if "the mutated run TIMED OUT" in line
        ]
        self.assertEqual(len(timed_out_lines), 1, "\n".join(result.lines))
        self.assertIn(
            "a cargo run's true bound is up to 2x this if an LNK1104 relink retry fired",
            timed_out_lines[0],
        )

    def test_a_FAST_PATH_narrow_baseline_TIMED_OUT_message_carries_the_disclosure(
        self,
    ) -> None:
        # Finding L's own repair, the OTHER fast-path site: the narrow
        # BASELINE's own TIMED OUT message (reached before any mutation is
        # even written) used to carry the same bare `(bound {N}s)`. Every
        # call raises, so the narrow baseline times out, falls back, and the
        # full suite's OWN baseline (the same mocked seam) times out too;
        # this test cares only about the FAST-PATH line the fallback leaves
        # behind in the transcript, not which branch decides the final
        # verdict.
        def fake_run(*args: object, **kwargs: object) -> _FakeCompleted:
            raise subprocess.TimeoutExpired(cmd="cargo", timeout=60)

        saved = self._patch(fake_run)
        try:
            with tempfile.TemporaryDirectory() as raw:
                tmp = Path(raw)
                manifest = tmp / "Cargo.toml"
                manifest.write_text(_RUST_MANIFEST, encoding="utf-8", newline="\n")
                lib = tmp / "lib.rs"
                lib.write_text("pub const X: u64 = 0;\n", encoding="utf-8", newline="\n")
                result = mp.probe(
                    file=lib,
                    old="pub const X: u64 = 0;",
                    new="pub const X: u64 = 1;",
                    suite=manifest,
                    floor=1,
                    timeout=60,
                    tests=("bounds_and_does_not_sleep_long",),
                )
        finally:
            self._unpatch(saved)

        fast_baseline_lines = [
            line
            for line in result.lines
            if "fast path" in line and "named-test baseline TIMED OUT" in line
        ]
        self.assertEqual(len(fast_baseline_lines), 1, "\n".join(result.lines))
        self.assertIn(
            "a cargo run's true bound is up to 2x this if an LNK1104 relink retry fired",
            fast_baseline_lines[0],
        )

    def test_a_FAST_PATH_cargo_mutant_TIMED_OUT_message_carries_the_disclosure(
        self,
    ) -> None:
        # Finding L's own repair: the fast path used to print the SAME bare
        # `(bound {N}s)` the full suite used to, over the identical
        # cargo-reachable ceiling. Covered here so the class cannot relocate
        # back to the fast path unnoticed.
        calls: list[int] = []

        def fake_run(*args: object, **kwargs: object) -> _FakeCompleted:
            calls.append(1)
            if len(calls) == 1:
                return _FakeCompleted(stdout=_CARGO_OK_TAIL, stderr="")
            raise subprocess.TimeoutExpired(cmd="cargo", timeout=60)

        saved = self._patch(fake_run)
        try:
            with tempfile.TemporaryDirectory() as raw:
                tmp = Path(raw)
                manifest = tmp / "Cargo.toml"
                manifest.write_text(_RUST_MANIFEST, encoding="utf-8", newline="\n")
                lib = tmp / "lib.rs"
                lib.write_text("pub const X: u64 = 0;\n", encoding="utf-8", newline="\n")
                result = mp.probe(
                    file=lib,
                    old="pub const X: u64 = 0;",
                    new="pub const X: u64 = 1;",
                    suite=manifest,
                    floor=1,
                    timeout=60,
                    tests=("bounds_and_does_not_sleep_long",),
                )
        finally:
            self._unpatch(saved)

        self.assertEqual(len(calls), 2, "\n".join(result.lines))
        self.assertEqual(result.verdict, mp.INCONCLUSIVE, "\n".join(result.lines))
        fast_timed_out_lines = [
            line
            for line in result.lines
            if "fast path" in line and "TIMED OUT" in line and "mutated run" in line
        ]
        self.assertEqual(len(fast_timed_out_lines), 1, "\n".join(result.lines))
        self.assertIn(
            "a cargo run's true bound is up to 2x this if an LNK1104 relink retry fired",
            fast_timed_out_lines[0],
        )
        # The FAST path's own verdict, never a fall-back to the full suite:
        # proves the message pinned above is the one finding L's repair
        # actually touched, not the sibling full-suite branch above.
        self.assertFalse(
            any(line.startswith("verdict path: full suite") for line in result.lines),
            "\n".join(result.lines),
        )

    def test_a_python_mutant_TIMED_OUT_message_carries_no_cargo_disclosure(
        self,
    ) -> None:
        # The OTHER mutation `_cargo_bound_note` needs defended against: `if
        # not cargo:` degraded to `if False:` would leak this same
        # disclosure into a backend that has no LNK1104 retry to disclose.
        calls: list[int] = []

        def fake_run(*args: object, **kwargs: object) -> _FakeCompleted:
            calls.append(1)
            if len(calls) == 1:
                return _FakeCompleted(stdout="Ran 1 test in 0.001s\n\nOK\n", stderr="")
            raise subprocess.TimeoutExpired(cmd=str(sys.executable), timeout=60)

        saved = self._patch(fake_run)
        try:
            with tempfile.TemporaryDirectory() as raw:
                tmp = Path(raw)
                module = tmp / "guard.py"
                module.write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
                suite = tmp / "test_guard.py"
                suite.write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
                result = mp.probe(
                    file=module,
                    old="VALUE = 1",
                    new="VALUE = 2",
                    suite=suite,
                    floor=1,
                    timeout=60,
                )
        finally:
            self._unpatch(saved)

        self.assertEqual(len(calls), 2, "\n".join(result.lines))
        self.assertEqual(result.verdict, mp.INCONCLUSIVE, "\n".join(result.lines))
        timed_out_lines = [
            line for line in result.lines if "the mutated run TIMED OUT" in line
        ]
        self.assertEqual(len(timed_out_lines), 1, "\n".join(result.lines))
        self.assertNotIn(
            "a cargo run's true bound is up to 2x this if an LNK1104 relink retry fired",
            timed_out_lines[0],
        )


class ApplicationTests(ProbeTestCase):
    """A mutation that does not apply must never look like a survival."""

    def test_absent_target_text_is_REFUSED(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            before = module.read_bytes()
            result = mp.probe(
                file=module, old="NOT_IN_THE_FILE = 1", new="x", suite=suite, floor=2
            )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertNotEqual(result.verdict, mp.SURVIVED)
            # The MESSAGE, not just the verdict: "absent" and "no-op" are both
            # REFUSED and they send a reader to different places.
            self.assertTrue(
                any("absent from the file" in line for line in result.lines),
                "\n".join(result.lines),
            )
            self.assertEqual(module.read_bytes(), before)

    def test_the_apply_helper_reports_absence_and_a_no_op_differently(self) -> None:
        original = b"a = 1\n"
        absent, n_absent, note_absent = mp._apply(original, "zzz = 9", "x")
        self.assertIsNone(absent)
        self.assertEqual(n_absent, 0)
        self.assertIn("absent from the file", note_absent)

        noop, n_noop, note_noop = mp._apply(original, "a = 1", "a = 1")
        self.assertIsNone(noop)
        self.assertEqual(n_noop, 1)
        self.assertIn("no-op", note_noop)

    def test_a_no_op_substitution_is_REFUSED(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 5", suite=suite, floor=2
            )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))

    def test_a_missing_file_is_REFUSED(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _, suite = _fixture(tmp, _SUITE)
            result = mp.probe(
                file=tmp / "nope.py", old="a", new="b", suite=suite, floor=1
            )
            self.assertEqual(result.verdict, mp.REFUSED)

    def test_a_missing_suite_is_REFUSED(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, _ = _fixture(tmp, _SUITE)
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0",
                suite=tmp / "test_nope.py", floor=1,
            )
            self.assertEqual(result.verdict, mp.REFUSED)

    def test_the_apply_helper_counts_rather_than_assuming(self) -> None:
        # Two in-code occurrences with no `--nth` is REFUSED
        # rather than silently mutated all at once (the old behavior this
        # test used to pin). `occurrences` is still reported so the refusal
        # message can cite it.
        original = b"a = 1\na = 1\n"
        mutated, occurrences, note = mp._apply(original, "a = 1", "a = 2")
        self.assertIsNone(mutated)
        self.assertEqual(occurrences, 2)
        self.assertIn("--nth", note)

    def test_the_apply_helper_nth_targets_exactly_one_occurrence(self) -> None:
        # `--nth` (1-based) is the disambiguation this item adds: the SAME
        # counted occurrences, but only one of them is actually rewritten.
        original = b"a = 1\na = 1\n"
        first, occ1, note1 = mp._apply(original, "a = 1", "a = 2", nth=1)
        self.assertEqual(occ1, 2)
        self.assertEqual(note1, "")
        self.assertEqual(first, b"a = 2\na = 1\n")

        second, occ2, note2 = mp._apply(original, "a = 1", "a = 2", nth=2)
        self.assertEqual(occ2, 2)
        self.assertEqual(second, b"a = 1\na = 2\n")

    def test_the_apply_helper_nth_out_of_range_is_refused(self) -> None:
        original = b"a = 1\na = 1\n"
        mutated, occurrences, note = mp._apply(original, "a = 1", "a = 2", nth=3)
        self.assertIsNone(mutated)
        self.assertEqual(occurrences, 2)
        self.assertIn("--nth 3", note)

    def test_the_apply_helper_nth_below_one_is_refused(self) -> None:
        original = b"a = 1\n"
        mutated, occurrences, note = mp._apply(original, "a = 1", "a = 2", nth=0)
        self.assertIsNone(mutated)
        self.assertIn("--nth", note)

    def test_nth_is_None_with_ONE_code_occurrence_leaves_a_prose_copy_untouched(
        self,
    ) -> None:
        # Step 9 finding H, at the `_apply` unit level: ONE occurrence in
        # code, ONE more inside a module docstring. `code_occurrences`
        # already reports `in_code == 1` for this shape, and before the
        # repair the `nth is None` branch still called the global
        # `text.replace`, which mutates every TEXTUAL occurrence, prose
        # included. The repair rewrites ONLY the span `_nth_code_span`
        # names, so the docstring copy must survive byte-for-byte.
        original = '"""LIMIT = 5 is documented here."""\nLIMIT = 5\n'.encode("utf-8")
        mutated, occurrences, note = mp._apply(original, "LIMIT = 5", "LIMIT = 0")
        self.assertIsNotNone(mutated)
        assert mutated is not None
        self.assertEqual(occurrences, 2)
        text = mutated.decode("utf-8")
        self.assertIn(
            '"""LIMIT = 5 is documented here."""',
            text,
            "the PROSE copy must be untouched: " + repr(text),
        )
        self.assertIn("\nLIMIT = 0\n", text)
        self.assertNotIn("\nLIMIT = 5\n", text)


class RestoreIsAssertedTests(ProbeTestCase):
    """The restore is compared byte for byte and the comparison is PRINTED."""

    def test_the_restore_line_carries_both_digests_and_the_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
            )
            restore_lines = [line for line in result.lines if line.startswith("restore:")]
            self.assertEqual(len(restore_lines), 1, "\n".join(result.lines))
            self.assertIn("identical=True", restore_lines[0])
            self.assertIn("byte(s)", restore_lines[0])

    def test_a_run_that_never_mutates_prints_no_restore_line(self) -> None:
        # A REFUSED-before-writing path must not claim to have restored
        # something it never touched.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE_ALREADY_RED)
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
            )
            self.assertEqual([line for line in result.lines if line.startswith("restore:")], [])


class CargoTranscriptTests(ProbeTestCase):
    """`parse_cargo_run` is HANDED its input, so per CLAUDE.md its known-bads are
    crafted INPUTS: a real rustc compile-error transcript and a real
    multi-target one."""

    def test_FLOOR_a_real_transcript_parses_with_a_named_member(self) -> None:
        run = mp.parse_cargo_run(CARGO_RED_TRANSCRIPT)
        self.assertEqual(run.ran, 2)
        self.assertIs(run.ok, False)
        self.assertIn("tests::bounds_both_sides", run.failed)
        self.assertGreaterEqual(len(run.failed), 1)

    def test_forcing_the_input_EMPTY_reddens_the_floor(self) -> None:
        # The floor proven rather than asserted: an empty transcript must not
        # satisfy the assertions above.
        run = mp.parse_cargo_run("")
        self.assertIsNone(run.ran)
        self.assertIsNone(run.ok)
        self.assertEqual(run.failed, [])

    def test_A_COMPILE_ERROR_IS_NOT_A_RUN(self) -> None:
        # THE KNOWN-BAD. An unviable mutant is exactly the shape that has twice
        # been scored as a kill in this repo's history, once per language.
        run = mp.parse_cargo_run(CARGO_COMPILE_ERROR_TRANSCRIPT)
        self.assertIsNone(run.ran)
        self.assertFalse(run.is_transcript)

    def test_MULTIPLE_TARGETS_ARE_SUMMED_NOT_LAST_WINS(self) -> None:
        # The opposite of the unittest parser's rule, deliberately. Two
        # `test result:` lines is the ORDINARY cargo case (the lib's unit tests
        # and an integration binary), not a nested run, so taking the last would
        # score against whichever target printed last.
        run = mp.parse_cargo_run(CARGO_TWO_TARGET_TRANSCRIPT)
        self.assertEqual(run.ran, 5)
        self.assertIs(run.ok, False)
        self.assertIn("tests::b", run.failed)

    def test_IGNORED_IS_IN_THE_POPULATION_BUT_NOT_IN_EXECUTED(self) -> None:
        # Cargo's `ignored` is unittest's `skipped`, and conflating them is the
        # false-SURVIVED arithmetic an earlier fix already closed on the Python side: a floor
        # counting collected rather than executed tests was satisfied by two
        # real executions out of twenty. `ran` is the POPULATION, which the
        # same-N rule compares; `executed` is what a floor is asking about.
        run = mp.parse_cargo_run(CARGO_IGNORED_TRANSCRIPT)
        self.assertEqual(run.ran, 3)
        self.assertEqual(run.skipped, 1)
        self.assertEqual(run.executed, 2)
        self.assertIs(run.ok, True)

    def test_a_cargo_floor_counts_EXECUTED_not_collected(self) -> None:
        # The known-bad as a crafted input: three collected, two executed. A
        # floor of 3 must REFUSE, because the third test defends nothing.
        run = mp.parse_cargo_run(CARGO_IGNORED_TRANSCRIPT)
        self.assertLess(run.executed, 3)
        self.assertGreaterEqual(run.ran, 3)

    def test_the_suite_extension_picks_the_backend(self) -> None:
        self.assertTrue(mp.is_cargo_suite(Path("example_crate/Cargo.toml")))
        self.assertFalse(mp.is_cargo_suite(Path("scripts/test_check_thing.py")))


@unittest.skipUnless(_CARGO, "cargo is not on PATH")
class RustProbeTests(ProbeTestCase):
    """The Rust backend, driven through the REAL path: a real crate, a real
    `cargo test`, a real substitution.

    **The fixture carries one HOLLOW guard and one BEHAVIORAL guard in the same
    crate, and that is the healthy control.** Without a case that PASSES, every
    FAIL here could be a malformed fixture rather than the tool working, which is
    `test_check_architecture.py`'s stated reason and it applies verbatim. One
    crate producing SURVIVED for the guard nothing defends and KILLED-with-a-name
    for the guard that is defended cannot be explained by a broken fixture: a
    broken fixture does not answer two ways.

    The crate is std-only and dependency-free, so `cargo test` takes about a
    second and both halves can live in the permanent suite rather than in a
    one-time transcript. It builds into its own temp `target/`, never the
    workspace's.
    """

    def _crate(self, root: Path) -> tuple[Path, Path]:
        """A standalone crate. Returns (lib.rs, Cargo.toml)."""
        (root / "src").mkdir(parents=True)
        manifest = root / "Cargo.toml"
        manifest.write_text(_RUST_MANIFEST, encoding="utf-8", newline="\n")
        lib = root / "src" / "lib.rs"
        lib.write_text(_RUST_LIB, encoding="utf-8", newline="\n")
        return lib, manifest

    def test_A_HOLLOW_TEST_LEAVES_ITS_GUARD_SURVIVING(self) -> None:
        # `allows_the_known_host` pins only the positive case, so widening the
        # guard to `true` changes nothing it asserts. A SURVIVED verdict means
        # the test is MISSING, not that the guard is fine.
        with tempfile.TemporaryDirectory() as raw:
            lib, manifest = self._crate(Path(raw))
            result = mp.probe(
                file=lib,
                old='host == "ok.example"',
                new="true",
                suite=manifest,
                floor=2,
            )
            self.assertEqual(result.verdict, mp.SURVIVED, "\n".join(result.lines))
            self.assertEqual(result.exit_code, 1)

    def test_A_BEHAVIORAL_TEST_KILLS_ITS_GUARD_AND_IS_NAMED(self) -> None:
        # THE HEALTHY CONTROL, and the whole point of the build: the verdict
        # carries the NAME of the test that went red, which is the field
        # `defended_by.reddened` demands and which nothing in this repo could
        # produce for Rust before.
        with tempfile.TemporaryDirectory() as raw:
            lib, manifest = self._crate(Path(raw))
            result = mp.probe(
                file=lib, old="n < 10", new="n < 100", suite=manifest, floor=2
            )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            self.assertEqual(result.exit_code, 0)
            killed = [line for line in result.lines if line.startswith(f"[{mp.KILLED}]")]
            self.assertEqual(len(killed), 1, "\n".join(result.lines))
            self.assertIn("tests::bounds_both_sides", killed[0])
            # And NOT the hollow one, which is untouched by this mutation.
            self.assertNotIn("allows_the_known_host", killed[0])

    def test_AN_UNVIABLE_MUTANT_IS_NO_RUN_NEVER_A_KILL(self) -> None:
        # The substitution does not compile, so no test executes. A run that did
        # not happen is not evidence the guard is defended, and it is not
        # evidence it is undefended either.
        with tempfile.TemporaryDirectory() as raw:
            lib, manifest = self._crate(Path(raw))
            result = mp.probe(
                file=lib, old="n < 10", new='n < "not a number"', suite=manifest, floor=2
            )
            self.assertEqual(result.verdict, mp.NO_RUN, "\n".join(result.lines))
            self.assertTrue(
                any("UNVIABLE" in line for line in result.lines), "\n".join(result.lines)
            )
            # A REAL cargo invocation, not a mocked one: the NO RUN
            # line must carry the actual rustc diagnostic rather than leaving
            # a reader to trust "most often an UNVIABLE mutant" on faith.
            self.assertTrue(
                any("cargo transcript tail" in line for line in result.lines),
                "\n".join(result.lines),
            )
            self.assertTrue(
                any("mismatched types" in line or "expected" in line for line in result.lines),
                "the real rustc error text must reach the transcript:\n" + "\n".join(result.lines),
            )

    def test_a_RED_baseline_refuses_before_writing_anything(self) -> None:
        # The FLOOR, unchanged from the Python path. A mutation scored against
        # an already-red suite proves nothing.
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            lib, manifest = self._crate(root)
            lib.write_text(
                _RUST_LIB.replace("assert!(is_bounded(9));", "assert!(is_bounded(99));"),
                encoding="utf-8",
                newline="\n",
            )
            before = lib.read_bytes()
            result = mp.probe(
                file=lib, old="n < 10", new="n < 100", suite=manifest, floor=2
            )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertTrue(any("baseline is RED" in line for line in result.lines))
            self.assertEqual(lib.read_bytes(), before)

    def test_the_floor_applies_to_the_rust_path_too(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            lib, manifest = self._crate(Path(raw))
            result = mp.probe(
                file=lib, old="n < 10", new="n < 100", suite=manifest, floor=99
            )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertTrue(any("under the declared" in line for line in result.lines))

    def test_THE_RESTORE_IS_BYTE_EXACT_AND_MTIME_IS_ADVANCED(self) -> None:
        # The content promise is unchanged. The mtime direction is INVERTED for
        # cargo on purpose: restoring the stamp backward would leave the honest
        # source looking older than the artifact just built from the mutant, and
        # the next `cargo test` could serve that artifact.
        with tempfile.TemporaryDirectory() as raw:
            lib, manifest = self._crate(Path(raw))
            before = lib.read_bytes()
            before_mtime = lib.stat().st_mtime
            result = mp.probe(
                file=lib, old="n < 10", new="n < 100", suite=manifest, floor=2
            )
            self.assertEqual(lib.read_bytes(), before)
            restore = [line for line in result.lines if line.startswith("restore:")]
            self.assertEqual(len(restore), 1, "\n".join(result.lines))
            self.assertIn("identical=True", restore[0])
            self.assertIn("mtime_advanced=True", restore[0])
            # STRICTLY greater. `assertGreaterEqual` was the first spelling and
            # it is satisfied by an UNCHANGED mtime, which is exactly the
            # behavior this test exists to reject: it would have passed against
            # the Python path's backward restore, so it defended nothing.
            self.assertGreater(lib.stat().st_mtime, before_mtime)

    def test_cargo_missing_REFUSES_rather_than_reporting_a_verdict(self) -> None:
        # A probe that cannot execute its suite says so. Reported as REFUSED
        # before anything is written, so the tree is never touched.
        from unittest import mock

        with tempfile.TemporaryDirectory() as raw:
            lib, manifest = self._crate(Path(raw))
            before = lib.read_bytes()
            with mock.patch.object(mp.shutil, "which", return_value=None):
                result = mp.probe(
                    file=lib, old="n < 10", new="n < 100", suite=manifest, floor=2
                )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertTrue(any("not on PATH" in line for line in result.lines))
            self.assertEqual(lib.read_bytes(), before)

    def test_THE_PROSE_GAP_IS_DISCLOSED_ON_EVERY_RUST_VERDICT(self) -> None:
        # Not hand-rolled, and not silent either. `screenshots.py`'s Rust
        # tokenizer produced seven hollow assertions and three enumerated
        # fail-opens; a printed disclosure a reader can act on beats a partial
        # tokenizer a reader mistakes for a complete one.
        with tempfile.TemporaryDirectory() as raw:
            lib, manifest = self._crate(Path(raw))
            result = mp.probe(
                file=lib, old="n < 10", new="n < 100", suite=manifest, floor=2
            )
            self.assertTrue(
                any(line.startswith("note: prose discrimination is NOT performed")
                    for line in result.lines),
                "\n".join(result.lines),
            )

    def test_A_MUTATION_THAT_IGNORES_A_TEST_IS_NO_RUN_NOT_SURVIVED(self) -> None:
        # THE CARGO SHAPE OF THE SKIP HAZARD, and it is worse here than in
        # Python: a mutation can flip a `#[cfg]` or add `#[ignore]` and move a
        # test OUT of the run while the collected population stays identical.
        # `ran` is 2 both times (1 passed + 1 ignored), so the same-N rule sees
        # nothing; only the skip comparison catches it. Without that, the guard
        # would be reported SURVIVED and `CLAUDE.md` would then instruct its
        # DELETION, which is how a false SURVIVED removes a working guard.
        #
        # A REAL cargo run, not a crafted transcript: this is the one shape
        # where the mutation itself is what changes the skip count, so a
        # hand-built pair would be assuming the answer.
        with tempfile.TemporaryDirectory() as raw:
            lib, manifest = self._crate(Path(raw))
            result = mp.probe(
                file=lib,
                old="    #[test]\n    fn bounds_both_sides() {",
                new="    #[test]\n    #[ignore]\n    fn bounds_both_sides() {",
                suite=manifest,
                floor=2,
            )
            self.assertEqual(result.verdict, mp.NO_RUN, "\n".join(result.lines))
            self.assertTrue(
                any("SKIPPED" in line for line in result.lines), "\n".join(result.lines)
            )
            self.assertEqual(result.exit_code, 1)

    def test_A_RED_CARGO_RUN_NAMING_NO_TEST_IS_NO_RUN_NOT_KILLED(self) -> None:
        # The `[KILLED] 0 of N` shape, in its CARGO form, which is DISTINCT
        # from the Python one rather than covered by it: unittest reaches it
        # through `@unittest.expectedFailure`, cargo through a harness that
        # reports a failure COUNT without per-test lines (a `--format terse`
        # run, or a binary that aborts mid-run and loses them). Given its own
        # test rather than assuming the ported guard covers the new shape.
        #
        # `run_suite` is patched because no mutation reliably produces a
        # truncated harness transcript on demand; the crafted pair is fed
        # through the REAL scoring path, which is what is under test.
        from unittest import mock

        green = mp.UnittestRun(ran=2, ok=True, failed=[], skipped=0)
        red_but_unattributable = mp.UnittestRun(ran=2, ok=False, failed=[], skipped=0)
        runs = [green, red_but_unattributable]

        with tempfile.TemporaryDirectory() as raw:
            lib, manifest = self._crate(Path(raw))
            with mock.patch.object(mp, "run_suite", side_effect=lambda *_a, **_k: runs.pop(0)):
                result = mp.probe(
                    file=lib, old="n < 10", new="n < 100", suite=manifest, floor=2
                )
            self.assertEqual(result.verdict, mp.NO_RUN, "\n".join(result.lines))
            self.assertTrue(
                any("names no failing test" in line for line in result.lines),
                "\n".join(result.lines),
            )
            # And the line that would have been copied into `defended_by` is
            # never printed.
            self.assertEqual([ln for ln in result.lines if ln.startswith(f"[{mp.KILLED}]")], [])

    def test_THE_CRASH_JOURNAL_COVERS_THE_RUST_PATH_TOO(self) -> None:
        # The obligation is to SETTLE this by execution rather than by reading
        # that `probe()` is shared. It is observed MID-RUN: the journal has to
        # exist while the `.rs` file holds the mutant, which is the only moment
        # a kill could strand it, and no after-the-fact check can see that.
        #
        # The Rust window is strictly longer than Python's, which is why this
        # matters more here: a cold `cargo test` is about 7 seconds against a
        # Python suite's fraction of one, so a harness timeout is far likelier
        # to land inside it. My own probe was killed mid-run and left a mutation
        # in a downstream validator module; the same kill during a cargo compile
        # would have stranded a `.rs` file instead.
        seen: list[tuple[bool, bool, bytes]] = []
        real_run = mp.run_suite

        def watch(suite: Path, **kw: object) -> mp.UnittestRun:
            marker, backup = mp._journal_paths(lib, journal_dir=jdir)
            seen.append(
                (marker.exists(), backup.exists(), lib.read_bytes())
            )
            return real_run(suite, **kw)  # type: ignore[arg-type]

        from unittest import mock

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            jdir = root / "journal"
            lib, manifest = self._crate(root)
            original = lib.read_bytes()
            with mock.patch.object(mp, "run_suite", watch):
                result = mp.probe(
                    file=lib, old="n < 10", new="n < 100", suite=manifest,
                    floor=2, journal_dir=jdir,
                )
            self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
            self.assertEqual(len(seen), 2, "expected a baseline run and a mutant run")

            baseline_marker, baseline_backup, baseline_bytes = seen[0]
            mutant_marker, mutant_backup, mutant_bytes = seen[1]

            # BASELINE: nothing written yet, so no journal and the file is honest.
            self.assertFalse(baseline_marker)
            self.assertEqual(baseline_bytes, original)

            # MUTANT: the `.rs` file holds the mutant AND the journal exists,
            # which is the whole claim. Either half alone proves nothing.
            self.assertNotEqual(mutant_bytes, original, "the mutant never reached disk")
            self.assertTrue(mutant_marker, "no journal existed while the .rs held its mutant")
            self.assertTrue(mutant_backup, "the journal named the file but saved no bytes")

            # And the saved bytes are the ORIGINAL, so a recovery would restore
            # the honest source rather than re-writing the mutant.
            _m, b = mp._journal_paths(lib, journal_dir=jdir)
            self.assertFalse(b.exists(), "a finished probe must clear its journal")
            self.assertEqual(lib.read_bytes(), original)

    def test_AN_ABANDONED_RUST_JOURNAL_IS_RECOVERED(self) -> None:
        # The other half: a `.rs` file left holding a mutant by a killed probe
        # is restored by the next run, and the recovery is a REFUSAL that says
        # so rather than a quiet fix.
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            jdir = root / "journal"
            lib, _manifest = self._crate(root)
            original = lib.read_bytes()
            marker, backup = mp._journal_paths(lib, journal_dir=jdir)
            jdir.mkdir(parents=True)
            backup.write_bytes(original)
            marker.write_text(str(lib.resolve()), encoding="utf-8")
            # Simulate the kill: the file on disk holds the mutant.
            lib.write_bytes(original.replace(b"n < 10", b"n < 100"))

            lines = mp.recover_abandoned(journal_dir=jdir)
            self.assertTrue(lines, "an abandoned Rust journal was not recovered")
            self.assertIn("RECOVERED", " ".join(lines))
            self.assertEqual(lib.read_bytes(), original)
            self.assertFalse(marker.exists())

    def test_a_python_probe_prints_NO_such_note(self) -> None:
        # MUST-NOT-FIRE: the Python path does discriminate, so claiming it does
        # not would be a false disclosure.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)
            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
            )
            self.assertEqual(
                [line for line in result.lines if line.startswith("note: prose")], []
            )


class BoundsTests(ProbeTestCase):
    def test_the_suite_timeout_is_a_named_constant(self) -> None:
        self.assertIsInstance(mp.SUITE_TIMEOUT_SECONDS, int)
        self.assertGreater(mp.SUITE_TIMEOUT_SECONDS, 0)

    def test_the_INCONCLUSIVE_constant_is_the_LITERAL_string(self) -> None:
        # `verify`'s own `mutants` gate found this SURVIVED on the
        # first real run against this diff: every other assertion compares
        # `result.verdict` against the SYMBOL `mp.INCONCLUSIVE`, which cannot
        # redden when the STRING it holds changes, because both sides of the
        # comparison move together. Pinning the literal is the only assertion
        # that can notice the constant's VALUE moving underneath the symbol.
        self.assertEqual(mp.INCONCLUSIVE, "INCONCLUSIVE")

    def test_only_KILLED_exits_zero(self) -> None:
        for verdict in (mp.SURVIVED, mp.NO_RUN, mp.REFUSED, mp.INCONCLUSIVE):
            with self.subTest(verdict=verdict):
                self.assertEqual(mp.ProbeResult(verdict, []).exit_code, 1)
        self.assertEqual(mp.ProbeResult(mp.KILLED, []).exit_code, 0)


def _git(args: tuple[str, ...], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Bounded, identity-carrying git helper for the fixtures below.

    Mirrors `mutation_probe._run_git`'s bounds (explicit `timeout=`,
    `GIT_AUTHOR_*`/`GIT_COMMITTER_*`, `GIT_TERMINAL_PROMPT=0`) rather than
    importing the module's private helper, so this file's own fixtures are
    never load-bearing evidence for the thing under test.
    """
    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = "mutation-probe-test"
    env["GIT_AUTHOR_EMAIL"] = "mutation-probe-test@mutation-probe.invalid"
    env["GIT_COMMITTER_NAME"] = "mutation-probe-test"
    env["GIT_COMMITTER_EMAIL"] = "mutation-probe-test@mutation-probe.invalid"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ("git", *args),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        env=env,
        check=True,
    )


def _make_repo(tmp: Path) -> None:
    _git(("init", "-q"), cwd=tmp)
    _git(("config", "user.email", "mutation-probe-test@mutation-probe.invalid"), cwd=tmp)
    _git(("config", "user.name", "mutation-probe-test"), cwd=tmp)


class TreeIdentityTests(ProbeTestCase):
    """`tree: <sha> <clean|dirty>`, derived from the PROBED FILE's
    own repo (never the cwd), on EVERY `probe()` run regardless of verdict."""

    def test_an_ordinary_run_carries_the_real_HEAD_sha_and_clean(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _make_repo(tmp)
            module, suite = _fixture(tmp, _SUITE)
            _git(("add", "-A"), cwd=tmp)
            _git(("commit", "-q", "-m", "init"), cwd=tmp)
            head = _git(("rev-parse", "HEAD"), cwd=tmp).stdout.strip()

            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
            )
            tree_lines = [line for line in result.lines if line.startswith("tree: ")]
            self.assertEqual(len(tree_lines), 1, "\n".join(result.lines))
            self.assertEqual(tree_lines[0], f"tree: {head} clean")
            # It must be EARLY, not merely present.
            self.assertLessEqual(
                result.lines.index(tree_lines[0]), 1, "\n".join(result.lines)
            )

    def test_a_dirty_repo_flips_the_state_to_dirty(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _make_repo(tmp)
            module, suite = _fixture(tmp, _SUITE)
            sentinel = tmp / "sentinel.txt"
            sentinel.write_text("a", encoding="utf-8")
            _git(("add", "-A"), cwd=tmp)
            _git(("commit", "-q", "-m", "init"), cwd=tmp)
            # An UNSTAGED modification to a TRACKED file, unrelated to the
            # module being probed, so the probed content is untouched and the
            # only thing that changed is the tree's clean/dirty state.
            sentinel.write_text("b", encoding="utf-8")

            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
            )
            tree_lines = [line for line in result.lines if line.startswith("tree: ")]
            self.assertEqual(len(tree_lines), 1, "\n".join(result.lines))
            self.assertTrue(tree_lines[0].endswith(" dirty"), tree_lines[0])

    def test_the_line_survives_a_REFUSAL_path_too(self) -> None:
        # "one line in every transcript" has to mean every transcript,
        # including the early-return REFUSED paths, not only a full run.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _make_repo(tmp)
            module, suite = _fixture(tmp, _SUITE)
            _git(("add", "-A"), cwd=tmp)
            _git(("commit", "-q", "-m", "init"), cwd=tmp)
            missing_suite = tmp / "does_not_exist.py"

            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=missing_suite, floor=1
            )
            self.assertEqual(result.verdict, mp.REFUSED, "\n".join(result.lines))
            self.assertTrue(
                any(line.startswith("tree: ") for line in result.lines),
                "the REFUSED transcript dropped the tree line: " + "\n".join(result.lines),
            )

    def test_a_directory_with_no_repo_reports_UNDETERMINABLE_not_a_crash(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(tmp, _SUITE)  # deliberately NOT a git repo

            result = mp.probe(
                file=module, old="LIMIT = 5", new="LIMIT = 0", suite=suite, floor=2
            )
            tree_lines = [line for line in result.lines if line.startswith("tree: ")]
            self.assertEqual(len(tree_lines), 1, "\n".join(result.lines))
            self.assertTrue(tree_lines[0].startswith("tree: UNDETERMINABLE"), tree_lines[0])


class TreeIdentityLineFunctionTests(unittest.TestCase):
    """`tree_identity_line` in isolation, the pure-shape half of the fixtures
    above: exactly one line back, never an exception, whatever git does."""

    def test_a_file_whose_directory_does_not_exist_is_UNDETERMINABLE(self) -> None:
        ghost = Path(tempfile.gettempdir()) / "mutation-probe-ghost-dir-xyz" / "f.py"
        line = mp.tree_identity_line(ghost)
        self.assertTrue(line.startswith("tree: UNDETERMINABLE"), line)

    def test_the_ordinary_shape_matches_the_module_regex_too(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _make_repo(tmp)
            (tmp / "f.py").write_text("x = 1\n", encoding="utf-8")
            _git(("add", "-A"), cwd=tmp)
            _git(("commit", "-q", "-m", "init"), cwd=tmp)
            line = mp.tree_identity_line(tmp / "f.py")
            self.assertRegex(line, r"^tree: [0-9a-f]{7,40} clean$")


class TranscriptCarriesTreeLineTests(unittest.TestCase):
    """The runbook's step 9 acceptance clause names this helper by name.

    KNOWN-BAD, per CLAUDE.md's `empty-discovery-fails`: a real transcript
    shape that carries NO `tree:` line at all, the exact shape every
    transcript had before this guard existed, must be REJECTED, which is the
    direction the acceptance clause enforces.
    """

    def test_an_ordinary_sha_line_is_accepted(self) -> None:
        text = (
            "tree: 1a2b3c4d5e6f7890 clean\n"
            "baseline: 2 test(s), OK, floor 2; 1 occurrence(s) of the target text\n"
        )
        self.assertTrue(mp.transcript_carries_tree_line(text))

    def test_an_UNDETERMINABLE_line_is_accepted_too(self) -> None:
        # probe-honesty: a STATED refusal is the honest outcome, not a gap.
        text = "tree: UNDETERMINABLE git is not on PATH\nbaseline: 2 test(s), OK\n"
        self.assertTrue(mp.transcript_carries_tree_line(text))

    def test_a_transcript_WITHOUT_the_line_is_REJECTED(self) -> None:
        # KNOWN-BAD: this is a REAL pre-tree-identity transcript shape, verbatim
        # in every field except the sha this rule exists to stop trusting
        # unmeasured.
        text = (
            "baseline: 2 test(s), OK, floor 2; 1 occurrence(s) of the target text\n"
            "mutation applied: 'LIMIT = 5' -> 'LIMIT = 0' "
            "(a1b2c3d4e5f6a7b8 -> f6e5d4c3b2a1b8a7)\n"
            "restore: 372 byte(s) 1d9b46be776f65ed -> 372 byte(s) 1d9b46be776f65ed; "
            "identical=True; mtime_advanced=True\n"
            "[KILLED] 1 of 2 test(s) reddened: FAIL: test_limit_is_five\n"
        )
        self.assertFalse(mp.transcript_carries_tree_line(text))

    def test_the_floor_reddens_when_the_input_is_forced_empty(self) -> None:
        # Proof the assertions above are not vacuous: an empty transcript
        # must not read as carrying the line either.
        self.assertFalse(mp.transcript_carries_tree_line(""))


# --- INCONCLUSIVE: a run that TIMED OUT rather than completing --------------
#
# The lineage: mutating a CLI dispatch made `--since` fall through to a live
# poll with no bound the suite itself controlled, and the suite did not fail,
# it HUNG. Folding that into the generic `NO RUN` bucket a compile error or a
# loader failure already uses loses the one fact a reader most needs -- WHICH
# run (baseline or mutant, fast path or full suite) never finished, and after
# how long -- and worse, `probe-honesty` instructs DELETING a guard that
# scores SURVIVED, so a hang that is ever misread as a clean pass argues for
# removing a working guard. `SLEEP` is a module constant the fixture suite
# reads at test time, so mutating it controls whether the MUTATED run merely
# behaves differently or never returns at all, independent of anything the
# BASELINE does.

_MODULE_SLEEPS_BY_ITS_OWN_CONSTANT = "SLEEP = 0\n"

# A later review round's repair (step 9 review finding 3, MEDIUM): the
# original fixtures bounded BOTH halves of every test with the SAME
# `timeout=3` - the run that must COMPLETE (a bare `time.sleep(0)` plus a
# whole CPython interpreter launch and import) and the run that must HANG,
# so that single value was simultaneously a ceiling for one and a floor for
# the other. Under real contention that ceiling breaks first: the review
# measured the COMPLETING side itself timing out ("baseline TIMED OUT after
# 3.2s (bound 3s)... before any mutation was written" inside a test that
# expected the MUTATED run to be the one that hangs), reproduced here again
# at 64 busy processes against these exact pre-fix numbers ("baseline TIMED
# OUT after 4.4s (bound 3s)", and at 64 processes even a first-pass repair
# bound of 15s still broke: "baseline TIMED OUT after 15.6s (bound 15s)").
# The fix decouples rather than merely widening both sides in lockstep: a
# COMPLETING call's ceiling is generous (real headroom for interpreter
# startup under contention), while a HANGING fixture's sleep is VASTLY
# longer than that bound, so the hang side is a hang under any sane choice
# of the first number rather than a value tuned to beat it by a fixed
# multiple. No single timeout value is a ceiling and a floor at once.
#
# A further finding: that last sentence was prose, not code, and three straight
# step 9 rounds relocated the identical defect it describes rather than
# fixing it - round 3 lowered `_HANG_TIMEOUT_SECONDS` 30 -> 8 to
# buy suite-timing headroom, and 8 was again one number serving as a
# ceiling a COMPLETING run must clear and a floor a HANGING run must
# overrun: round 4's own step 9 review reproduced 3 of 8 runs RED under a
# 32-process load (a narrow baseline "TIMED OUT after 8.2s (bound 8s)"),
# against 0 of 3 at the parent. `hang_bounds` makes the relationship
# STRUCTURAL - raising at import rather than relying on a comment nobody
# re-derives - so a future round cannot repeat this by editing one number
# and leaving the other in place.
# That same finding, AMENDED (round 4's review, finding 2):
# the original `hang_bounds` enforced only the FLOOR (`sleep_ms` versus
# `ceiling_s`), the half of the relationship that never once failed in this
# item's four-round history. Every failure was a CEILING overrun - a run
# that had to COMPLETE (a narrow baseline, or a full-suite baseline with no
# hang of its own) overran the same bound a hang was scored against - and
# the old `hang_bounds` had no opinion on `ceiling_s` at all: it accepted
# `(8, 3600000)`, the exact pair round 3 reproduced 3-of-8 red under load,
# and `(3, 3600000)`, the original repair's pair, and even a zero or
# negative ceiling. `_MIN_HANG_CEILING_SECONDS` closes that: a cold
# CPython launch under 32-process contention was measured reaching 30.5s
# for a narrow, do-nothing baseline (round 4's own reproduction), and a
# cold `cargo test` compile-and-run reached 35.73s ambient-only (round 4's
# own ambient batch) - round 3's `8` and the original `3` are nowhere near
# either figure. `hang_bounds` alone still cannot make a COMPLETING call
# safe merely by raising this floor further, though: `paired_hang_bounds`
# below exists for exactly that shape, giving the completing side a
# SEPARATE, larger clock rather than asking one number to clear both a
# floor and this ceiling at once.
_MIN_HANG_CEILING_SECONDS = 30


def hang_bounds(ceiling_s: int, sleep_ms: int) -> tuple[int, int]:
    """Returns `(ceiling_s, sleep_ms)` unchanged, but only once `sleep_ms`
    is proven at least 100x `ceiling_s` in milliseconds - the margin the
    Rust half's pair (30s / 3,600,000ms, 120x) already clears, and that a
    plain `timeout=8` constant carried no way to enforce. Raises
    `ValueError` rather than returning a pair whose ceiling and floor could
    collapse toward each other, since that collapse is exactly what turned
    a provable hang into a flake three times over in this file's history.

    ALSO raises (a later amendment) when `ceiling_s` is non-positive or
    below `_MIN_HANG_CEILING_SECONDS`: a ceiling that small cannot be
    trusted to distinguish ordinary launch contention from a genuine hang
    in ANY call shape, including one this function cannot see reused
    elsewhere as a completion bound - the exact way round 3's `8` reached
    three call sites at once. This is deliberately more conservative than
    the one call shape (`_HANG_ONLY_TIMEOUT_SECONDS`'s two sites) that
    never actually needed a large ceiling to stay correct: refusing a
    ceiling too small for the shapes that DID fail costs a caller that
    never needed it nothing but a slightly longer wait, while blessing it
    unconditionally is what let the ceiling that DID fail through four
    rounds unnoticed.

    ONE check, not two (a later round): a mutation probe of a separate
    `ceiling_s <= 0` branch ahead of this one proved it dead code - with
    `_MIN_HANG_CEILING_SECONDS` a positive constant, every non-positive
    value is ALREADY below it, so a second, earlier branch testing the same
    thing would survive every mutation and is exactly the undefended guard
    `probe-honesty` says to delete rather than keep.
    """
    if ceiling_s < _MIN_HANG_CEILING_SECONDS:
        raise ValueError(
            f"hang_bounds({ceiling_s}, {sleep_ms}): ceiling_s must be at least "
            f"{_MIN_HANG_CEILING_SECONDS} (non-positive included) - round 4's "
            f"own load reproduction measured a completing narrow baseline (a "
            f"bare CPython launch, no sleep) reach 30.5s under 32-process "
            f"contention on a 16-core box, and a cold cargo compile-and-run "
            f"reach 35.73s ambient-only; a ceiling under that observed range "
            f"cannot be trusted to tell ordinary contention from a genuine hang."
        )
    minimum = 100 * ceiling_s * 1000
    if sleep_ms < minimum:
        raise ValueError(
            f"hang_bounds({ceiling_s}, {sleep_ms}): sleep_ms must be at "
            f"least {minimum} (100x the {ceiling_s}s ceiling, in "
            f"milliseconds) - a hang fixture's floor has to be provably "
            f"separated from its own ceiling, not merely documented as such."
        )
    return ceiling_s, sleep_ms


# A later round's repair (step 9 round 4 review finding 1, HIGH): the ONE
# shape `hang_bounds` alone cannot make safe, no matter how its ceiling
# floor is tuned - a call whose BASELINE must genuinely COMPLETE (a real
# CPython launch, import, and named test(s) running to green) while its
# MUTANT must HANG. Every round-3/round-4 regression was exactly this: one
# ceiling asked to bound both a completion and a hang in the SAME call.
# `paired_hang_bounds` gives the completing phase a structurally SEPARATE,
# larger clock - never the same number `hang_bounds` blessed for the hang
# side - so a call site cannot be built with one ceiling wearing both hats
# even by accident.
_BASELINE_COMPLETION_MULTIPLIER = 4


def paired_hang_bounds(hang_ceiling_s: int, sleep_ms: int) -> tuple[int, int, int]:
    """Returns `(baseline_timeout_s, hang_ceiling_s, sleep_ms)` for a call
    whose baseline must COMPLETE while its mutant must HANG. `hang_ceiling_s`
    and `sleep_ms` are validated exactly as `hang_bounds` validates them -
    this calls `hang_bounds` itself rather than re-deriving the check, so
    the two can never drift apart. `baseline_timeout_s` is a SEPARATE,
    `_BASELINE_COMPLETION_MULTIPLIER`x larger number: the clock a completing
    baseline is scored against, structurally distinct from `hang_ceiling_s`,
    the clock a hang is scored against, so no caller can pass this
    function's result into a probe call that scores both phases against the
    same clock - the defect that reached this file three separate rounds.
    """
    hang_ceiling_s, sleep_ms = hang_bounds(hang_ceiling_s, sleep_ms)
    baseline_timeout_s = hang_ceiling_s * _BASELINE_COMPLETION_MULTIPLIER
    return baseline_timeout_s, hang_ceiling_s, sleep_ms


# An earlier round's repair (step 9 round 3 review finding 1, HIGH): back to
# 30, the value round 1's repair already established as
# correctness-clean before round 2 traded it away. It matches
# `_RUST_HANG_TIMEOUT_SECONDS` below, which the reviewer's own 32-process
# recipe proved 0 of 10 RED; the Python side was not independently
# re-measured at 30 under that exact recipe by this repair (see the round 4
# build report's `not_reached`), but it is the shape the review prescribed
# and the Rust half already proves, not a new guess.
#
# That same later round's repair (step 9 round 4 review finding 1, HIGH): a call
# whose fixture is ALREADY sleeping before any mutation is even considered
# (the baseline-hang tests below) never races a real CPython launch against
# the clock: `run_suite`'s `subprocess.run(timeout=...)` reports `timed_out`
# whether the kill lands during interpreter startup or during the sleep -
# either way nothing completed inside the bound, which is the only fact
# those calls test for. Before this round `_HANG_ONLY_TIMEOUT_SECONDS` used
# a genuinely smaller ceiling (5) than `_HANG_TIMEOUT_SECONDS` for exactly
# that reason. `hang_bounds`'s new ceiling floor (`_MIN_HANG_CEILING_SECONDS`,
# above) closes the gap that let round 3's `8` through, but it applies to
# EVERY `hang_bounds` call, including this one: the per-call split by VALUE
# is gone, so both constants now sit at the same floor. The split by ROLE
# survives in the comments and the call sites below (a hang-only call is
# still never asked to also prove a completion), and the three calls that DO
# have a completing side get their own, larger `_BASELINE_COMPLETION_TIMEOUT_SECONDS`
# via `paired_hang_bounds` rather than a bigger shared `timeout=`.
_HANG_TIMEOUT_SECONDS, _HANG_SLEEP_MS = hang_bounds(30, 3600 * 1000)
_HANG_ONLY_TIMEOUT_SECONDS = hang_bounds(_MIN_HANG_CEILING_SECONDS, _HANG_SLEEP_MS)[0]
_HANG_SLEEP_SECONDS = _HANG_SLEEP_MS // 1000

# That same round's repair (finding 1): the SEPARATE, larger clock for the
# three calls below whose baseline (full-suite or fast-path-narrow) must
# actually complete. Never reused as the bound a hang is scored against -
# that stays `_HANG_TIMEOUT_SECONDS`, passed as `timeout=` unchanged.
_BASELINE_COMPLETION_TIMEOUT_SECONDS, _, _ = paired_hang_bounds(
    _HANG_TIMEOUT_SECONDS, _HANG_SLEEP_MS
)

# An earlier round's repair (step 9 round 3 review finding 2, MEDIUM): the
# committed claim this replaced ("227.0s under a fresh 8-process load...
# headroom ~24%") did not reproduce - the round 3 review measured 335.4s,
# 362.5s and 371.4s under the identical 8-process recipe at that repair's
# own commit, and 379.3s/474.8s ambient-only versus loaded for its PARENT
# commit (uniform `_HANG_TIMEOUT_SECONDS = 30`, this file's own state
# before that repair).
# That repair's own replacement number (273.1s) did not reproduce either
# (a later round, round 4 review finding 4, MEDIUM): the round 4
# review measured 380.1s at the MERGED tree and 382.4s at the PARENT, both
# well over `check_mutants.PYTHON_PROBE_TIMEOUT_SECONDS = 300`, and its own
# harness calling the REAL `check_mutants.python_pass` on a quiet box
# (5 concurrent python) returned `[INCONCLUSIVE]` for a diff touching this
# very file, the mutated run TIMING OUT at exactly the 300s bound.
#
# Round 5 measured three ambient (no load-isolation attempted) full
# runs at its own commit of 273.6s/294.6s/286.2s (155 tests; one of the
# three additionally hit an unrelated, unreproduced single-test failure from
# `tree_identity_line` reading `git rev-parse HEAD` against a temp
# directory, gone on the next two runs - a pre-existing environmental
# flake that round did not introduce and did not chase further), and one
# REAL end-to-end `check_mutants.python_pass` call against that round's
# own diff to this file: 547.6s total for
# baseline+mutant (SURVIVED, not INCONCLUSIVE - the suite completed
# within its 300s-per-run bound on this box in this window). The round 4
# review's 380.1s/382.4s pair above was that same commit merged onto the
# next one versus its own parent. None of these five numbers agree, which is
# the point: this figure is a SAMPLE of machine load at measurement time,
# not a property of the suite, exactly as finding 1's own reproduction
# showed for the hang ceiling. What IS a property, not a sample: round
# 5's own fix (finding 2's ceiling floor) FORCED `_HANG_ONLY_TIMEOUT_SECONDS`
# from 5 to `_MIN_HANG_CEILING_SECONDS`, adding a real, deliberate ~75s of
# ceiling-wait time to every full run versus the commit the round 4
# review measured. The round 5 build report counted ~50s from the two
# SOURCE CALL SITES (+25s each); the round 5 review corrected the unit to
# HANGING RUNS, of which there are three, because
# `test_a_FAST_PATH_baseline_hang_still_falls_back_to_the_full_suite`
# hangs its narrow baseline, falls back, and hangs the full-suite
# baseline too, paying the ceiling twice from one call site (measured
# 60s wall for that test, 31s for its BASELINE-hang sibling, at
# that same commit) - a genuine, disclosed regression against this finding's
# own goal, traded for closing finding 2's safety gap. Neither a
# suite-time reduction nor `check_mutants.python_pass` using
# `mutation_probe.py --tests`' fast path for its own probe of
# this suite was implemented this round; see the round 5 build report's
# `not_done` for why `PYTHON_PROBE_TIMEOUT_SECONDS` itself was considered
# and NOT raised as a substitute: the `mutants` gate is a `check`-type gate
# with no enclosing `verify.py` timeout of its own (bounded only by CI's
# `timeout-minutes: 20` job ceiling), and raising a bound that also governs
# every OTHER `scripts/*.py` file's probe would let a genuinely hung file
# elsewhere consume more of that shared budget for a benefit that helps
# only this one self-referential file. And per that same review: `verify`
# runs its gates SEQUENTIALLY, so the real operating condition this suite
# meets inside `verify` is not contending with `verify`'s own other work at
# all, only with whatever else the machine is doing.
#
# A module whose BASELINE already exceeds any sane probe timeout, so the
# hang is provable without ever reaching the mutation-application stage.
# Sleeps for `_HANG_SLEEP_SECONDS` (3600s), not merely a margin over either
# ceiling above: the duration must stay a hang under ANY reasonable timeout
# choice, not just the one a particular call happens to use.
_MODULE_ALWAYS_SLEEPS = f"SLEEP = {_HANG_SLEEP_SECONDS}\n"

_SUITE_SLEEPS_BY_MODULE_CONSTANT = """import time
import unittest

import mod_x


class GuardTests(unittest.TestCase):
    def test_does_not_sleep_long(self):
        time.sleep(mod_x.SLEEP)
        self.assertEqual(mod_x.SLEEP, 0)


if __name__ == "__main__":
    unittest.main()
"""

# Finding 2 (an earlier round's repair, MEDIUM): reproduces the review's own
# repro of the write-status contradiction. A suite with a FAST test that
# neither sleeps nor observes the mutated constant (so the fast path's
# narrow attempt writes-and-restores a mutation, then falls back as a
# "narrow miss" rather than a kill) alongside a SLOW sibling class that
# makes the FULL suite's own baseline hang for an unrelated reason.
_MODULE_SLOW_SIBLING_PLUS_UNRELATED_VALUE = f"SLOW = {_HANG_SLEEP_SECONDS}\nVALUE = 5\n"

_SUITE_FAST_GUARD_PLUS_SLOW_SIBLING = """import time
import unittest

import mod_x


class GuardTests(unittest.TestCase):
    def test_fast(self):
        pass


class SlowTests(unittest.TestCase):
    def test_slow(self):
        time.sleep(mod_x.SLOW)


if __name__ == "__main__":
    unittest.main()
"""


class HangBoundsTests(unittest.TestCase):
    """`hang_bounds` makes a hang fixture's ceiling/sleep pair
    structurally unwritable as one number serving both roles, rather than
    merely documented as such in a comment nobody re-derives."""

    def test_a_pair_at_exactly_the_100x_floor_is_accepted(self) -> None:
        # A later round: the ceiling here is `_MIN_HANG_CEILING_SECONDS`
        # itself, not `1` - a `1`-second ceiling is now rejected outright
        # (see the degenerate-ceiling tests below), so isolating the SLEEP
        # floor from the CEILING floor needs a ceiling both floors clear.
        floor_sleep = _MIN_HANG_CEILING_SECONDS * 100 * 1000
        self.assertEqual(
            hang_bounds(_MIN_HANG_CEILING_SECONDS, floor_sleep),
            (_MIN_HANG_CEILING_SECONDS, floor_sleep),
        )

    def test_a_pair_one_millisecond_under_the_floor_raises(self) -> None:
        with self.assertRaises(ValueError):
            hang_bounds(_MIN_HANG_CEILING_SECONDS, _MIN_HANG_CEILING_SECONDS * 100 * 1000 - 1)

    def test_the_generous_python_pair_clears_its_own_floor(self) -> None:
        self.assertEqual(
            (_HANG_TIMEOUT_SECONDS, _HANG_SLEEP_MS), hang_bounds(30, 3600 * 1000)
        )

    def test_the_generous_python_pair_clears_the_ceiling_and_floor_by_construction(
        self,
    ) -> None:
        # A later round (round 4 review finding 2): the sibling above
        # is a diff-detector that hardcodes both literals and reddens
        # identically on a legitimate raise; this asserts the INVARIANT
        # instead, so a future, larger, review-prescribed value keeps this
        # test green rather than fighting it.
        self.assertGreaterEqual(_HANG_TIMEOUT_SECONDS, _MIN_HANG_CEILING_SECONDS)
        self.assertGreaterEqual(_HANG_SLEEP_MS, 100 * _HANG_TIMEOUT_SECONDS * 1000)

    def test_the_hang_only_pair_shares_the_generous_pairs_sleep(self) -> None:
        # An earlier round (finding 1): the no-completing-side ceiling is
        # validated against the SAME sleep duration as the generous one,
        # never a shorter sleep tuned to fit it. Round 5: the ceiling
        # literal changed from `5` to `_MIN_HANG_CEILING_SECONDS` because
        # `hang_bounds`'s new ceiling floor (finding 2) now rejects `5`
        # outright - the per-call VALUE split is gone, only the per-call
        # ROLE split (this call never proves a completion) remains.
        self.assertEqual(
            _HANG_ONLY_TIMEOUT_SECONDS,
            hang_bounds(_MIN_HANG_CEILING_SECONDS, _HANG_SLEEP_MS)[0],
        )

    def test_the_rust_pair_clears_its_floor_at_120x(self) -> None:
        self.assertEqual(
            (_RUST_HANG_TIMEOUT_SECONDS, _RUST_MUTATED_SLEEP_MS),
            hang_bounds(30, 30 * 1000 * 120),
        )

    # A later round (round 4 review finding 2): the degenerate ceilings
    # the review's own reproduction named as currently (wrongly) accepted.

    def test_a_zero_ceiling_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            hang_bounds(0, _HANG_SLEEP_MS)

    def test_a_negative_ceiling_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            hang_bounds(-5, _HANG_SLEEP_MS)

    def test_a_ceiling_one_second_under_the_minimum_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            hang_bounds(_MIN_HANG_CEILING_SECONDS - 1, _HANG_SLEEP_MS)

    def test_a_hang_only_shaped_call_with_a_tiny_ceiling_is_also_rejected(self) -> None:
        # The review's own words: "the hang-only call form a future agent
        # could write, hang_bounds(1, _HANG_SLEEP_MS)[0] -> 1". `hang_bounds`
        # cannot see how its result will be used, so it refuses this shape
        # regardless of whether the caller's fixture happens to have no
        # completing side.
        with self.assertRaises(ValueError):
            hang_bounds(1, _HANG_SLEEP_MS)

    # The two pairs the round 4 review reproduced as ACCEPTED and named as
    # the shapes that actually failed: round 3's `_HANG_TIMEOUT_SECONDS = 8`
    # (3 of 8 red under load, reused as a completion bound) and the
    # original round-1 pair. `hang_bounds` must refuse both outright now.

    def test_the_round_3_defect_pair_is_now_rejected(self) -> None:
        with self.assertRaises(ValueError):
            hang_bounds(8, 3600000)

    def test_the_original_round_1_defect_pair_is_now_rejected(self) -> None:
        with self.assertRaises(ValueError):
            hang_bounds(3, 3600000)


class PairedHangBoundsTests(unittest.TestCase):
    """A later round (round 4 review finding 1 and 2): `paired_hang_bounds`
    is the one constructor for a call whose baseline must COMPLETE while its
    mutant must HANG - the shape `hang_bounds` alone cannot make safe."""

    def test_the_baseline_timeout_clears_the_hang_ceiling_by_the_named_multiplier(
        self,
    ) -> None:
        baseline_timeout, ceiling, sleep_ms = paired_hang_bounds(
            _HANG_TIMEOUT_SECONDS, _HANG_SLEEP_MS
        )
        self.assertEqual(ceiling, _HANG_TIMEOUT_SECONDS)
        self.assertEqual(sleep_ms, _HANG_SLEEP_MS)
        self.assertEqual(
            baseline_timeout, _HANG_TIMEOUT_SECONDS * _BASELINE_COMPLETION_MULTIPLIER
        )
        self.assertGreater(baseline_timeout, ceiling)

    def test_it_rejects_exactly_what_hang_bounds_rejects(self) -> None:
        # `paired_hang_bounds` calls `hang_bounds` rather than re-deriving
        # its checks, so the round-3 defect pair - the exact shape that
        # actually failed - is refused through this constructor too.
        with self.assertRaises(ValueError):
            paired_hang_bounds(8, 3600000)
        with self.assertRaises(ValueError):
            paired_hang_bounds(0, _HANG_SLEEP_MS)

    def test_the_module_constant_matches_a_fresh_call(self) -> None:
        self.assertEqual(
            _BASELINE_COMPLETION_TIMEOUT_SECONDS,
            paired_hang_bounds(_HANG_TIMEOUT_SECONDS, _HANG_SLEEP_MS)[0],
        )


class WiringTests(ProbeTestCase):
    """A still later round (round 5 review finding 2): the COMMITTED tree must
    be able to tell whether `baseline_timeout` and `narrow_baseline_timeout`
    are wired into `run_suite` at all. Round 5 proved they were defended only
    by a scratch fixture that was never committed: reverting either wiring
    line left every committed test green, which is a recorded
    predicate-vs-mechanism incident's undefended call site verbatim.

    A RECORDING DOUBLE rather than the scratch fixture's subprocess realism,
    deliberately: a subprocess discrimination needs the baseline's real
    elapsed time to sit BETWEEN the two clocks, which either costs the suite
    tens of seconds of genuine sleeping per run or couples the completing
    mutant to a small load-unsafe ceiling, the exact one-number-two-roles
    class this item exists to close. The recorder pins WHICH CLOCK REACHES
    WHICH `run_suite` CALL, deterministically, at zero wall cost; the
    subprocess semantics of each clock (a hang scored against `timeout`, a
    completing baseline against its own bound) are already pinned by
    `InconclusiveTimeoutTests` on real processes."""

    @staticmethod
    def _recorder(scripted: list[mp.UnittestRun], calls: list[dict[str, object]]):
        def fake_run_suite(
            suite: Path,
            *,
            timeout: int = mp.SUITE_TIMEOUT_SECONDS,
            tests: tuple[str, ...] | None = None,
        ) -> mp.UnittestRun:
            calls.append({"timeout": timeout, "tests": tests})
            return scripted[len(calls) - 1]

        return fake_run_suite

    def test_baseline_timeout_reaches_the_full_suite_baseline_call(self) -> None:
        green = mp.UnittestRun(ran=2, ok=True, failed=[], elapsed_seconds=0.1)
        red = mp.UnittestRun(
            ran=2, ok=False, failed=["GuardTests.test_limit"], elapsed_seconds=0.1
        )
        calls: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as raw:
            module, suite = _fixture(Path(raw), _SUITE)
            with unittest.mock.patch.object(
                mp, "run_suite", self._recorder([green, red], calls)
            ):
                result = mp.probe(
                    file=module,
                    old="LIMIT = 5",
                    new="LIMIT = 6",
                    suite=suite,
                    floor=1,
                    timeout=3,
                    baseline_timeout=20,
                )
        self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
        self.assertEqual(
            [c["timeout"] for c in calls],
            [20, 3],
            "the full-suite baseline must run on `baseline_timeout` and the "
            "mutant on `timeout`; a reverted wiring hands the baseline "
            f"`timeout` instead: {calls}",
        )

    def test_narrow_baseline_timeout_reaches_the_fast_path_narrow_baseline(
        self,
    ) -> None:
        green = mp.UnittestRun(ran=1, ok=True, failed=[], elapsed_seconds=0.1)
        red = mp.UnittestRun(
            ran=1, ok=False, failed=["GuardTests.test_limit"], elapsed_seconds=0.1
        )
        calls: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as raw:
            module, suite = _fixture(Path(raw), _SUITE)
            with unittest.mock.patch.object(
                mp, "run_suite", self._recorder([green, red], calls)
            ):
                result = mp.probe(
                    file=module,
                    old="LIMIT = 5",
                    new="LIMIT = 6",
                    suite=suite,
                    floor=1,
                    timeout=3,
                    tests=("GuardTests.test_limit",),
                    baseline_timeout=20,
                    narrow_baseline_timeout=7,
                )
        self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
        self.assertEqual(
            [(c["timeout"], c["tests"]) for c in calls],
            [(7, ("GuardTests.test_limit",)), (3, ("GuardTests.test_limit",))],
            "the fast path's narrow baseline must run on "
            "`narrow_baseline_timeout` and its narrow mutant on `timeout`: "
            f"{calls}",
        )

    def test_baseline_timeout_None_falls_back_to_timeout(self) -> None:
        # The NONE half of the SAME fallback the test above
        # exercises with an override. This is the shape every caller that
        # never passes `baseline_timeout` actually hits, and it is the half
        # a mutation that drops the ternary's `is None` branch breaks
        # silently: `run_suite` would then be handed `None` outright.
        green = mp.UnittestRun(ran=2, ok=True, failed=[], elapsed_seconds=0.1)
        red = mp.UnittestRun(
            ran=2, ok=False, failed=["GuardTests.test_limit"], elapsed_seconds=0.1
        )
        calls: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as raw:
            module, suite = _fixture(Path(raw), _SUITE)
            with unittest.mock.patch.object(
                mp, "run_suite", self._recorder([green, red], calls)
            ):
                result = mp.probe(
                    file=module,
                    old="LIMIT = 5",
                    new="LIMIT = 6",
                    suite=suite,
                    floor=1,
                    timeout=3,
                    baseline_timeout=None,
                )
        self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
        self.assertEqual(
            [c["timeout"] for c in calls],
            [3, 3],
            "baseline_timeout=None must fall back to `timeout`, never reach "
            f"run_suite as None: {calls}",
        )

    def test_narrow_baseline_timeout_None_falls_back_to_timeout(self) -> None:
        # The narrow twin of the fallback test above.
        green = mp.UnittestRun(ran=1, ok=True, failed=[], elapsed_seconds=0.1)
        red = mp.UnittestRun(
            ran=1, ok=False, failed=["GuardTests.test_limit"], elapsed_seconds=0.1
        )
        calls: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as raw:
            module, suite = _fixture(Path(raw), _SUITE)
            with unittest.mock.patch.object(
                mp, "run_suite", self._recorder([green, red], calls)
            ):
                result = mp.probe(
                    file=module,
                    old="LIMIT = 5",
                    new="LIMIT = 6",
                    suite=suite,
                    floor=1,
                    timeout=3,
                    tests=("GuardTests.test_limit",),
                    narrow_baseline_timeout=None,
                )
        self.assertEqual(result.verdict, mp.KILLED, "\n".join(result.lines))
        self.assertEqual(
            [c["timeout"] for c in calls],
            [3, 3],
            "narrow_baseline_timeout=None must fall back to `timeout`, "
            f"never reach run_suite as None: {calls}",
        )


class InconclusiveTimeoutTests(ProbeTestCase):
    """A mutation that makes the process HANG must never score
    SURVIVED (the false-positive `probe-honesty` names), and must be
    distinguishable in the transcript from the generic `NO RUN` a crash or a
    compile error already produces."""

    def test_a_MUTATED_run_that_hangs_is_INCONCLUSIVE_not_SURVIVED_not_NO_RUN(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(
                tmp,
                _SUITE_SLEEPS_BY_MODULE_CONSTANT,
                _MODULE_SLEEPS_BY_ITS_OWN_CONSTANT,
            )
            before = module.read_bytes()
            result = mp.probe(
                file=module,
                old="SLEEP = 0",
                new=f"SLEEP = {_HANG_SLEEP_SECONDS}",
                suite=suite,
                floor=1,
                timeout=_HANG_TIMEOUT_SECONDS,
                # A later round (round 4 review finding 1): this
                # baseline (SLEEP=0) must genuinely COMPLETE - the whole
                # point of this test is a clean baseline beside a hanging
                # mutant - so it gets its own, separate, generous clock
                # rather than racing the same `timeout` the mutant's hang
                # is scored against.
                baseline_timeout=_BASELINE_COMPLETION_TIMEOUT_SECONDS,
            )
            self.assertEqual(result.verdict, mp.INCONCLUSIVE, "\n".join(result.lines))
            self.assertNotEqual(result.verdict, mp.SURVIVED)
            self.assertNotEqual(result.verdict, mp.NO_RUN)
            self.assertEqual(result.exit_code, 1)
            self.assertTrue(
                any(
                    "TIMED OUT" in line and "the mutated run" in line
                    for line in result.lines
                ),
                "\n".join(result.lines),
            )
            # WHICH run and HOW LONG, per the item's own acceptance clause.
            self.assertTrue(
                any(f"bound {_HANG_TIMEOUT_SECONDS}s" in line for line in result.lines),
                "\n".join(result.lines),
            )
            # Restored byte-exactly whatever the mutated process was doing.
            self.assertEqual(module.read_bytes(), before)

    def test_a_BASELINE_that_hangs_is_INCONCLUSIVE_before_anything_is_written(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(
                tmp, _SUITE_SLEEPS_BY_MODULE_CONSTANT, _MODULE_ALWAYS_SLEEPS
            )
            before = module.read_bytes()
            result = mp.probe(
                file=module,
                old=f"SLEEP = {_HANG_SLEEP_SECONDS}",
                new=f"SLEEP = {_HANG_SLEEP_SECONDS + 1}",
                suite=suite,
                floor=1,
                # An earlier round (finding 1): this call's baseline is
                # ALREADY sleeping before any mutation is even considered,
                # so nothing here races a real completion against the
                # clock - `_HANG_ONLY_TIMEOUT_SECONDS` is the smaller,
                # still-safe ceiling for exactly that shape of call.
                timeout=_HANG_ONLY_TIMEOUT_SECONDS,
            )
            self.assertEqual(result.verdict, mp.INCONCLUSIVE, "\n".join(result.lines))
            self.assertTrue(
                any("baseline TIMED OUT" in line for line in result.lines),
                "\n".join(result.lines),
            )
            self.assertEqual(
                module.read_bytes(), before, "the baseline hang wrote nothing"
            )
            # Finding 2: the write-status clause, not a hardcoded claim the
            # sibling repair below proves can go stale.
            self.assertFalse(
                any("before any mutation was written" in line for line in result.lines),
                "the baseline-INCONCLUSIVE line must not hardcode this claim; it "
                "must derive it from _write_status_clause like the sibling NO_RUN "
                "branches do: " + "\n".join(result.lines),
            )
            self.assertTrue(
                any(
                    line.endswith(f"Nothing was written to {module}.")
                    for line in result.lines
                ),
                "\n".join(result.lines),
            )

    def test_a_full_suite_baseline_hang_after_a_fast_path_narrow_miss_does_not_lie_about_writes(
        self,
    ) -> None:
        # Finding 2 (an earlier round's repair, MEDIUM), the other direction
        # of the test above: the FAST PATH's narrow miss already wrote (and
        # byte-exactly restored) a mutation of its own before falling back,
        # so by the time the FULL suite's own baseline hangs, `out` already
        # carries a `mutation applied:` line. The old hardcoded "before any
        # mutation was written" contradicted `_write_status_clause`'s own,
        # correct "The fast path already wrote a mutation..." in the very
        # next sentence; this reproduces the reviewer's exact repro shape
        # (a fast test that neither sleeps nor pins the mutated constant,
        # paired with a slow sibling class).
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(
                tmp,
                _SUITE_FAST_GUARD_PLUS_SLOW_SIBLING,
                _MODULE_SLOW_SIBLING_PLUS_UNRELATED_VALUE,
            )
            result = mp.probe(
                file=module,
                old="VALUE = 5",
                new="VALUE = 6",
                suite=suite,
                floor=1,
                # A still later round (round 5 review finding 1): THREE runs,
                # three roles. The narrow baseline AND the narrow MUTANT
                # (`GuardTests.test_fast` alone, no sleep, and its narrow
                # miss is the premise of this test) must both COMPLETE, so
                # both ride completion clocks: the narrow baseline via
                # `narrow_baseline_timeout`, the narrow mutant via `timeout`
                # itself, which is the clock `_mutate_run_restore` gives
                # every mutant run. Only the FULL suite's baseline must
                # HANG (`SlowTests.test_slow` sleeps unconditionally), and
                # it gets the hang clock through `baseline_timeout`. Round
                # 5 passed `timeout=_HANG_TIMEOUT_SECONDS`, which made 30
                # the ceiling the completing narrow mutant had to beat AND
                # the floor the full baseline had to overrun, the exact
                # one-number-two-roles shape this file's own comment
                # forbids, proven by the round 5 review with `timeout=1`.
                timeout=_BASELINE_COMPLETION_TIMEOUT_SECONDS,
                tests=("GuardTests.test_fast",),
                baseline_timeout=_HANG_TIMEOUT_SECONDS,
                narrow_baseline_timeout=_BASELINE_COMPLETION_TIMEOUT_SECONDS,
            )
            self.assertEqual(result.verdict, mp.INCONCLUSIVE, "\n".join(result.lines))
            self.assertTrue(
                any(line.startswith("mutation applied:") for line in result.lines),
                "the fast path's narrow miss must have written (and restored) a "
                "mutation before the full suite's own baseline hung: "
                + "\n".join(result.lines),
            )
            self.assertTrue(
                any("baseline TIMED OUT" in line for line in result.lines),
                "\n".join(result.lines),
            )
            self.assertFalse(
                any("before any mutation was written" in line for line in result.lines),
                "the baseline-INCONCLUSIVE line must not hardcode this claim when a "
                "prior write actually happened: " + "\n".join(result.lines),
            )
            self.assertTrue(
                any("already wrote a mutation" in line for line in result.lines),
                "the write-status clause must report the fast path's earlier write: "
                + "\n".join(result.lines),
            )
            self.assertFalse(
                any(
                    line.endswith(f"Nothing was written to {module}.")
                    for line in result.lines
                ),
                "a transcript that already wrote (and restored) a mutation must "
                "not claim nothing was written: " + "\n".join(result.lines),
            )

    def test_a_FAST_PATH_mutated_hang_is_INCONCLUSIVE_without_a_full_suite_rerun(
        self,
    ) -> None:
        # The fast path already knows the mutated run does not finish; a
        # silent fall-back would re-run the SAME hang against the full suite
        # for no gain, so this reports INCONCLUSIVE directly.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(
                tmp,
                _SUITE_SLEEPS_BY_MODULE_CONSTANT,
                _MODULE_SLEEPS_BY_ITS_OWN_CONSTANT,
            )
            result = mp.probe(
                file=module,
                old="SLEEP = 0",
                new=f"SLEEP = {_HANG_SLEEP_SECONDS}",
                suite=suite,
                floor=1,
                timeout=_HANG_TIMEOUT_SECONDS,
                tests=("GuardTests.test_does_not_sleep_long",),
                # A later round (round 4 review finding 1): this is the
                # test the round 4 review actually reproduced red (1 of 8
                # under a 32-burner load) - the narrow baseline
                # (SLEEP=0) must genuinely complete before the narrow
                # mutant's hang can even be attempted, so it gets the
                # separate, generous clock rather than racing the same
                # bound the mutant's hang is scored against.
                narrow_baseline_timeout=_BASELINE_COMPLETION_TIMEOUT_SECONDS,
            )
            self.assertEqual(result.verdict, mp.INCONCLUSIVE, "\n".join(result.lines))
            self.assertTrue(
                any(
                    "fast path" in line and "TIMED OUT" in line
                    for line in result.lines
                ),
                "\n".join(result.lines),
            )
            self.assertFalse(
                any(line.startswith("verdict path: full suite") for line in result.lines),
                "the fast path re-ran the full suite over a hang it already "
                "observed: " + "\n".join(result.lines),
            )

    def test_a_FAST_PATH_baseline_hang_still_falls_back_to_the_full_suite(
        self,
    ) -> None:
        # The narrow baseline's own health is not authoritative on its own
        # (a typo'd test id looks identical from here), so a HUNG narrow
        # baseline still defers to the full suite, which then reports its
        # own INCONCLUSIVE rather than the fast path guessing.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            module, suite = _fixture(
                tmp, _SUITE_SLEEPS_BY_MODULE_CONSTANT, _MODULE_ALWAYS_SLEEPS
            )
            result = mp.probe(
                file=module,
                old=f"SLEEP = {_HANG_SLEEP_SECONDS}",
                new=f"SLEEP = {_HANG_SLEEP_SECONDS + 1}",
                suite=suite,
                floor=1,
                # An earlier round (finding 1): neither the narrow nor the
                # full-suite baseline this call falls back to ever has a
                # completing side (both start out already sleeping), so the
                # smaller ceiling is safe here too.
                timeout=_HANG_ONLY_TIMEOUT_SECONDS,
                tests=("GuardTests.test_does_not_sleep_long",),
            )
            self.assertEqual(result.verdict, mp.INCONCLUSIVE, "\n".join(result.lines))
            self.assertTrue(
                any(
                    "fast path" in line and "named-test baseline TIMED OUT" in line
                    for line in result.lines
                ),
                "\n".join(result.lines),
            )
            self.assertTrue(
                any(
                    line.startswith("verdict path: full suite") for line in result.lines
                ),
                "a hung narrow baseline must fall back to the full suite: "
                + "\n".join(result.lines),
            )


_RUST_LIB_SLEEPS = (
    "pub const SLEEP_MS: u64 = 0;\n"
    "\n"
    "pub fn is_bounded(n: u32) -> bool {\n"
    "    n < 10\n"
    "}\n"
    "\n"
    "#[cfg(test)]\n"
    "mod tests {\n"
    "    use super::*;\n"
    "    use std::thread::sleep;\n"
    "    use std::time::Duration;\n"
    "\n"
    "    #[test]\n"
    "    fn bounds_and_does_not_sleep_long() {\n"
    "        sleep(Duration::from_millis(SLEEP_MS));\n"
    "        assert!(is_bounded(9));\n"
    "    }\n"
    "}\n"
)


# An earlier round's repair (step 9 round 2 review finding 1, HIGH): the
# original single `timeout=10` bounded BOTH halves of the call below - the
# ceiling for a COLD `cargo test` compile-and-run that must COMPLETE, and
# the floor for a mutated run that must HANG - with its mutated sleep
# (30000ms) only 3x that bound, against the 120x separation the Python
# half above builds in (3600s sleep vs a 30s bound). The review measured a
# cold cargo baseline reaching 10.3-17.3s under 32-process contention on a
# 16-core box against the old 10s ceiling, and reproduced 7 of 10 runs RED
# under that load. This applies the same rule the Python comment states:
# a generous ceiling the baseline clears by a wide margin even under
# contention, and a mutated sleep vastly larger than that ceiling (120x,
# matching the Python half's own ratio) so the hang is a hang under any
# sane choice of the first number rather than tuned to beat it by a fixed
# multiple. No single timeout value is a ceiling and a floor at once.
#
# Proved, not merely reasoned about, per the review's own load recipe: 10
# runs of this class as FRESH subprocesses under 32 busy processes on the
# same 16-core box the review used gave "SUMMARY: 0 of 10 runs RED" (each
# 43.2-51.6s), against 7 of 10 RED at the old `timeout=10`; 6 quiet control
# runs gave "SUMMARY: 0 of 6 runs RED" at a steady 30.7s each.
#
# A further finding: routed through `hang_bounds` unchanged - this pair already
# satisfies the 100x floor at 120x, the same proven pair, just built through
# the constructor that makes the relationship structural rather than only
# asserted in the comment above.
_RUST_HANG_TIMEOUT_SECONDS, _RUST_MUTATED_SLEEP_MS = hang_bounds(30, 30 * 1000 * 120)

# A later round (round 4 review finding 1's `not_reached`, named but
# not confirmed standalone): the test below has NO `tests=`, so its own
# baseline is a COLD `cargo test` compile-and-run of the unmutated crate -
# the same completing-side shape as the Python tests above, and the review
# measured a cold cargo compile-and-run reach 35.73s ambient-only, i.e.
# with NO synthetic load at all. Decoupled the same way, on the same
# reasoning, even though this file's own reproduction under the 32-process
# recipe read 0 of 10 RED at the shared `timeout=30`.
_RUST_BASELINE_COMPLETION_TIMEOUT_SECONDS, _, _ = paired_hang_bounds(
    _RUST_HANG_TIMEOUT_SECONDS, _RUST_MUTATED_SLEEP_MS
)


@unittest.skipUnless(_CARGO, "cargo is not on PATH")
class RustInconclusiveTests(ProbeTestCase):
    """The same class of hang, on the OTHER backend. The same `UnittestRun.timed_out` flag
    drives both parsers, but this is the only place that proves the CARGO
    call site actually wires it under a REAL `cargo test` that hangs."""

    def _crate(self, root: Path) -> tuple[Path, Path]:
        (root / "src").mkdir(parents=True)
        manifest = root / "Cargo.toml"
        manifest.write_text(_RUST_MANIFEST, encoding="utf-8", newline="\n")
        lib = root / "src" / "lib.rs"
        lib.write_text(_RUST_LIB_SLEEPS, encoding="utf-8", newline="\n")
        return lib, manifest

    def test_a_mutated_cargo_test_that_hangs_is_INCONCLUSIVE_not_NO_RUN(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            lib, manifest = self._crate(Path(raw))
            result = mp.probe(
                file=lib,
                old="pub const SLEEP_MS: u64 = 0;",
                new=f"pub const SLEEP_MS: u64 = {_RUST_MUTATED_SLEEP_MS};",
                suite=manifest,
                floor=1,
                timeout=_RUST_HANG_TIMEOUT_SECONDS,
                # A later round: the unmutated baseline is a COLD
                # `cargo test` compile-and-run that must genuinely complete;
                # see the constant's own comment for why this gets the same
                # decoupling treatment as the Python calls above.
                baseline_timeout=_RUST_BASELINE_COMPLETION_TIMEOUT_SECONDS,
            )
            self.assertEqual(result.verdict, mp.INCONCLUSIVE, "\n".join(result.lines))
            self.assertNotEqual(result.verdict, mp.NO_RUN)
            self.assertTrue(
                any(
                    "TIMED OUT" in line and "the mutated run" in line
                    for line in result.lines
                ),
                "\n".join(result.lines),
            )
            self.assertTrue(
                any(
                    f"bound {_RUST_HANG_TIMEOUT_SECONDS}s" in line
                    for line in result.lines
                ),
                "\n".join(result.lines),
            )


if __name__ == "__main__":
    unittest.main()
