#!/usr/bin/env python3
"""Unit tests for check_hermetic_bounds.py.

The worse failure in this class is a subprocess that BLOCKS rather than one
that fails, because a failure is a verdict and a block is silence until
someone notices the bill.

Two layers are tested, deliberately separately:

  * the PURE layer (`scan_source`), fed hand-written fixtures, which is where
    a parser's individual rules are pinned;
  * the DISCOVERY layer (`scan_directory`, `check_subprocess_bounds`), fed
    real fixture DIRECTORIES including a known-bad one it must REJECT. The
    reason this split exists: a check whose discovery collapsed to zero
    reports "clean" in exactly the same words as a working one, and a
    hand-fed fixture can never notice a glob that stopped matching.

This module only tests the Python half (`subprocess-bounds`,
`git-identity-env`). The source repo this tool was ported from also had a
`listener-locality` sub-check over a Rust source tree, tied to one
repo's own Rust module layout; it was not ported (see
`check_hermetic_bounds.py`'s own module docstring), so there is nothing
here to test for it.

Run: python test_check_hermetic_bounds.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import check_hermetic_bounds as guard

THIS_DIR = Path(__file__).resolve().parent


def _write(directory: Path, name: str, source: str) -> None:
    (directory / name).write_text(source, encoding="utf-8")


class SpawnClassificationTests(unittest.TestCase):
    """What counts as a spawn at all. A positive allowlist of call names, not a
    negative list of exclusions: `subprocess.TimeoutExpired` and
    `subprocess.CompletedProcess` are ordinary constructors that wait for
    nothing, and a guard that counted them would inflate its own floor with
    calls it can never sensibly demand a `timeout=` from.
    """

    def test_an_unbounded_subprocess_run_is_a_violation(self) -> None:
        result = guard.scan_source("import subprocess\nsubprocess.run(['git'])\n", "x.py")
        self.assertEqual(result.spawn_calls, 1)
        self.assertEqual([v.line for v in result.violations], [2])
        self.assertEqual(result.violations[0].call, "subprocess.run")
        self.assertEqual(result.violations[0].path, "x.py")

    def test_a_bounded_subprocess_run_is_not_a_violation(self) -> None:
        result = guard.scan_source("import subprocess\nsubprocess.run(['git'], timeout=5)\n", "x.py")
        self.assertEqual(result.spawn_calls, 1)
        self.assertEqual(result.violations, ())

    def test_every_spawning_api_is_recognized(self) -> None:
        # The floor for the classifier itself: each name in the documented set
        # must actually be detected, or a sweep could route around the guard by
        # picking the one API nobody wired up.
        #
        # The REASON is asserted per API, not just the count. Every one of these
        # is a violation, but `Popen` is a violation for a different reason than
        # the rest -- it is unwaited, not un-timed -- and this test read as if
        # the whole set shared one rule while quietly passing under both. See
        # `PopenBoundTests`.
        for attr in sorted(guard.SPAWN_ATTRS):
            with self.subTest(attr=attr):
                result = guard.scan_source(f"import subprocess\nsubprocess.{attr}(['x'])\n", "x.py")
                self.assertEqual(result.spawn_calls, 1, attr)
                self.assertEqual(len(result.violations), 1, attr)
                expected = (
                    guard._REASON_POPEN_UNWAITED
                    if attr == "Popen"
                    else guard._REASON_NO_TIMEOUT
                )
                self.assertEqual(result.violations[0].reason, expected, attr)

    def test_non_spawning_subprocess_members_are_not_counted(self) -> None:
        source = (
            "import subprocess\n"
            "raise subprocess.TimeoutExpired(cmd='x', timeout=1)\n"
            "subprocess.CompletedProcess(args=[], returncode=0)\n"
        )
        result = guard.scan_source(source, "x.py")
        self.assertEqual(result.spawn_calls, 0)
        self.assertEqual(result.violations, ())

    def test_an_unbounded_urlopen_is_a_violation(self) -> None:
        # The site the original item missed: an unbounded NETWORK wait is the
        # same failure class as an unbounded process wait, and worse in CI,
        # where a black-holed connection never even gets an RST back.
        source = "import urllib.request\nurllib.request.urlopen('https://x')\n"
        result = guard.scan_source(source, "x.py")
        self.assertEqual(result.spawn_calls, 1)
        self.assertEqual(result.violations[0].call, "urllib.request.urlopen")

    def test_a_bounded_urlopen_is_not_a_violation(self) -> None:
        source = "import urllib.request\nurllib.request.urlopen('https://x', timeout=30)\n"
        self.assertEqual(guard.scan_source(source, "x.py").violations, ())

    def test_a_multi_line_call_is_handled(self) -> None:
        # The whole reason this is an AST walk and not a regex. A line-oriented
        # scan sees `subprocess.run(` and `timeout=5` on different lines and can
        # only guess which call the keyword belongs to -- and a real spawn site
        # is routinely written across many lines.
        bounded = "import subprocess\nsubprocess.run(\n    ['git'],\n    timeout=5,\n)\n"
        unbounded = "import subprocess\nsubprocess.run(\n    ['git'],\n    check=True,\n)\n"
        self.assertEqual(guard.scan_source(bounded, "x.py").violations, ())
        self.assertEqual(len(guard.scan_source(unbounded, "x.py").violations), 1)

    def test_a_directly_imported_spawn_is_still_seen(self) -> None:
        # `from subprocess import run` would defeat a scan keyed on the dotted
        # `subprocess.` prefix alone. Closed here rather than left as a known
        # way around the guard.
        source = "from subprocess import run\nrun(['git'])\n"
        result = guard.scan_source(source, "x.py")
        self.assertEqual(result.spawn_calls, 1)
        self.assertEqual(result.violations[0].call, "run")

    def test_an_aliased_direct_import_is_still_seen(self) -> None:
        source = "from urllib.request import urlopen as fetch\nfetch('https://x')\n"
        self.assertEqual(len(guard.scan_source(source, "x.py").violations), 1)

    def test_an_aliased_module_import_is_still_seen(self) -> None:
        # `import subprocess as sp` is the most idiomatic alias shape there is,
        # and a scanner walking `ast.ImportFrom` only would miss it -- so
        # `sp.run(...)` would score zero spawn calls and sail through.
        source = "import subprocess as sp\nsp.run(['git'])\n"
        result = guard.scan_source(source, "x.py")
        self.assertEqual(result.spawn_calls, 1)
        self.assertEqual(result.violations[0].call, "sp.run")

    def test_an_aliased_module_import_can_be_bounded(self) -> None:
        # The other half of the same rule: catching the alias must not make it
        # impossible to satisfy.
        source = "import subprocess as sp\nsp.run(['git'], timeout=5)\n"
        result = guard.scan_source(source, "x.py")
        self.assertEqual(result.spawn_calls, 1)
        self.assertEqual(result.violations, ())

    def test_a_dotted_module_alias_is_still_seen(self) -> None:
        # `import urllib.request as ur` binds `ur`, not `urllib`. Already caught
        # by the any-qualifier `urlopen` rule rather than by the alias set;
        # pinned so a future narrowing of that rule cannot silently reopen it.
        source = "import urllib.request as ur\nur.urlopen('https://x')\n"
        self.assertEqual(len(guard.scan_source(source, "x.py").violations), 1)

    def test_a_star_import_of_a_spawn_is_still_seen(self) -> None:
        # A third import form the from-import rule misses on its own: `from
        # subprocess import *` binds every public name, so a bare `run(...)`
        # is the same bypass by a different spelling.
        source = "from subprocess import *\nrun(['git'])\n"
        result = guard.scan_source(source, "x.py")
        self.assertEqual(result.spawn_calls, 1)
        self.assertEqual(result.violations[0].call, "run")

    def test_an_unrelated_module_alias_is_not_swept_in(self) -> None:
        # The cost of the module-alias rule must not be a guard that fires on
        # every `sp.run(...)` in a file that never imported subprocess.
        source = "import shutil as sp\nsp.run(['x'])\n"
        result = guard.scan_source(source, "x.py")
        self.assertEqual(result.spawn_calls, 0)
        self.assertEqual(result.violations, ())

    def test_an_unrelated_method_named_run_is_not_flagged(self) -> None:
        # The cost of the direct-import rule must not be a guard that fires on
        # every `self.run(...)` anywhere.
        source = "import subprocess\nself.run(['x'])\nparser.call()\n"
        result = guard.scan_source(source, "x.py")
        self.assertEqual(result.spawn_calls, 0)
        self.assertEqual(result.violations, ())

    def test_a_kwargs_splat_does_not_count_as_bounded(self) -> None:
        # `**kwargs` MIGHT carry a timeout; the guard cannot know, and an
        # unverifiable bound is not a bound. Flagged deliberately, so a caller
        # that means it writes the keyword explicitly.
        source = "import subprocess\nsubprocess.run(['git'], **kwargs)\n"
        self.assertEqual(len(guard.scan_source(source, "x.py").violations), 1)

    def test_a_syntax_error_is_raised_not_swallowed(self) -> None:
        # A file the scanner cannot parse contributes zero violations, which is
        # indistinguishable from a clean file. Fail loudly instead.
        with self.assertRaises(SyntaxError):
            guard.scan_source("def broken(:\n", "x.py")


class PopenBoundTests(unittest.TestCase):
    """`Popen` is the one spawn whose bound does not live on the call.

    `subprocess.Popen.__init__` accepts no `timeout` keyword at all (it returns
    a handle rather than waiting), so demanding one of it inverted the guard:
    the CORRECT shape, `Popen(...)` plus `communicate(timeout=...)`, was flagged
    as a violation, while `Popen(..., timeout=5)` -- which raises `TypeError` at
    runtime and can never have bounded anything -- passed. A guard that rejects
    working code and accepts broken code is worse than no guard on that path,
    because it teaches the wrong shape.

    These sources are synthetic and exercised only through `scan_source`
    directly: a repo may well have no `Popen` call site at all, so this
    branch has no guaranteed real-tree coverage.
    """

    def test_a_popen_bounded_by_communicate_is_not_a_violation(self) -> None:
        source = "import subprocess\np = subprocess.Popen(['git'])\np.communicate(timeout=5)\n"
        result = guard.scan_source(source, "x.py")
        self.assertEqual(result.spawn_calls, 1)
        self.assertEqual(result.violations, ())

    def test_a_popen_bounded_by_wait_is_not_a_violation(self) -> None:
        source = "import subprocess\np = subprocess.Popen(['git'])\np.wait(timeout=5)\n"
        self.assertEqual(guard.scan_source(source, "x.py").violations, ())

    def test_a_popen_bounded_inline_is_not_a_violation(self) -> None:
        # The handle need not be named: `Popen(...).communicate(timeout=...)`
        # bounds the same wait without ever binding a variable.
        source = "import subprocess\nsubprocess.Popen(['git']).communicate(timeout=5)\n"
        self.assertEqual(guard.scan_source(source, "x.py").violations, ())

    def test_a_popen_with_a_timeout_kwarg_is_a_violation(self) -> None:
        # The inversion, stated as a test: this line raises TypeError the first
        # time it runs, so a guard that passes it is certifying code that cannot
        # execute.
        source = "import subprocess\nsubprocess.Popen(['git'], timeout=5)\n"
        result = guard.scan_source(source, "x.py")
        self.assertEqual(result.spawn_calls, 1)
        self.assertEqual(len(result.violations), 1)
        self.assertIn("takes no timeout", result.violations[0].reason)

    def test_a_popen_with_a_timeout_kwarg_is_a_violation_even_when_waited(self) -> None:
        # A bounded wait does not redeem the broken constructor: the call still
        # raises before any wait can happen.
        source = (
            "import subprocess\n"
            "p = subprocess.Popen(['git'], timeout=5)\n"
            "p.communicate(timeout=5)\n"
        )
        result = guard.scan_source(source, "x.py")
        self.assertEqual(len(result.violations), 1)
        self.assertIn("takes no timeout", result.violations[0].reason)

    def test_a_popen_with_no_wait_at_all_is_a_violation(self) -> None:
        source = "import subprocess\np = subprocess.Popen(['git'])\n"
        result = guard.scan_source(source, "x.py")
        self.assertEqual(result.spawn_calls, 1)
        self.assertIn("communicate", result.violations[0].reason)

    def test_a_popen_with_an_unbounded_communicate_is_a_violation(self) -> None:
        # The shape that actually hangs, and the reason `Popen` cannot simply be
        # dropped from the spawn set: a bare `communicate()` waits forever.
        source = "import subprocess\np = subprocess.Popen(['git'])\np.communicate()\n"
        self.assertEqual(len(guard.scan_source(source, "x.py").violations), 1)

    def test_a_popen_in_a_with_block_still_needs_an_explicit_bound(self) -> None:
        # `with Popen(...) as p:` calls `p.wait()` on exit with NO bound, so the
        # context manager is not itself a bound. Deliberately fail-closed, the
        # same call the `**kwargs` rule already makes.
        unbounded = "import subprocess\nwith subprocess.Popen(['git']) as p:\n    pass\n"
        bounded = (
            "import subprocess\n"
            "with subprocess.Popen(['git']) as p:\n"
            "    p.communicate(timeout=5)\n"
        )
        self.assertEqual(len(guard.scan_source(unbounded, "x.py").violations), 1)
        self.assertEqual(guard.scan_source(bounded, "x.py").violations, ())

    def test_a_wait_bound_does_not_redeem_an_unbounded_run(self) -> None:
        # The wait rule is scoped to `Popen`. A bounded `communicate` elsewhere
        # in the module must not launder an unbounded `subprocess.run`.
        source = (
            "import subprocess\n"
            "p = subprocess.Popen(['git'])\n"
            "p.communicate(timeout=5)\n"
            "subprocess.run(['git'])\n"
        )
        result = guard.scan_source(source, "x.py")
        self.assertEqual(result.spawn_calls, 2)
        self.assertEqual([v.call for v in result.violations], ["subprocess.run"])

    def test_the_wait_rule_is_name_scoped(self) -> None:
        # Bounding one handle must not bound a different one. Attribution is by
        # bound name, not by "a bounded wait exists somewhere".
        source = (
            "import subprocess\n"
            "first = subprocess.Popen(['git'])\n"
            "second = subprocess.Popen(['git'])\n"
            "first.communicate(timeout=5)\n"
        )
        result = guard.scan_source(source, "x.py")
        self.assertEqual(result.spawn_calls, 2)
        self.assertEqual([v.line for v in result.violations], [3])

    def test_the_remedy_text_names_the_wait_for_a_popen(self) -> None:
        # The reported remedy has to be satisfiable. Demanding `timeout=` of a
        # `Popen` was an instruction no author could follow.
        with tempfile.TemporaryDirectory(prefix="hermetic-popen-") as td:
            root = Path(td)
            _write(root, "mod.py", "import subprocess\np = subprocess.Popen(['git'])\n")
            status, detail = guard.check_subprocess_bounds(root)
            self.assertEqual(status, "FAIL")
            self.assertIn("communicate(timeout=", detail)


class DiscoveryLayerTests(unittest.TestCase):
    """The rule applied to the layer that actually collapses. The pure tests
    above feed `scan_source` by hand and so can never notice a `glob` that
    stopped matching, an encoding that stopped decoding, or a directory that
    moved. These drive real directories.
    """

    def test_a_known_bad_fixture_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hermetic-bad-") as td:
            root = Path(td)
            _write(root, "mod.py", "import subprocess\nsubprocess.run(['git'])\n")
            result = guard.scan_directory(root)
            self.assertEqual(result.files, 1)
            self.assertEqual(result.spawn_calls, 1)
            self.assertEqual([v.path for v in result.violations], ["mod.py"])

    def test_test_files_are_scanned_too(self) -> None:
        # A guard that exempted `test_*.py` would exempt exactly the files
        # most likely to spawn a real subprocess.
        with tempfile.TemporaryDirectory(prefix="hermetic-tests-") as td:
            root = Path(td)
            _write(root, "test_thing.py", "import subprocess\nsubprocess.run(['git'])\n")
            result = guard.scan_directory(root)
            self.assertEqual([v.path for v in result.violations], ["test_thing.py"])

    def test_a_clean_fixture_directory_passes_without_being_empty(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hermetic-good-") as td:
            root = Path(td)
            _write(root, "mod.py", "import subprocess\nsubprocess.run(['git'], timeout=5)\n")
            result = guard.scan_directory(root)
            self.assertEqual(result.violations, ())
            self.assertEqual(result.spawn_calls, 1)

    def test_an_empty_directory_fails_rather_than_reporting_clean(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hermetic-empty-") as td:
            status, detail = guard.check_subprocess_bounds(Path(td))
            self.assertEqual(status, "FAIL")
            self.assertIn("0 file", detail)

    def test_a_directory_with_no_spawn_calls_passes_with_an_explanatory_message(self) -> None:
        # DISCLOSED GENERALIZATION (see check_hermetic_bounds.py's own
        # docstring): a tool pointed at many different repos cannot assume any
        # of them spawns a subprocess at all, so files-but-no-spawn-calls is a
        # clean PASS rather than a vacuity FAIL. The one floor that still FAILs
        # unconditionally is zero *.py FILES (see the test above).
        with tempfile.TemporaryDirectory(prefix="hermetic-vacuous-") as td:
            root = Path(td)
            _write(root, "mod.py", "def f() -> None:\n    pass\n")
            status, detail = guard.check_subprocess_bounds(root)
            self.assertEqual(status, "PASS", detail)
            self.assertIn("0 spawn", detail)

    def test_an_unparseable_file_fails_the_check_by_name(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hermetic-broken-") as td:
            root = Path(td)
            _write(root, "mod.py", "import subprocess\nsubprocess.run(['x'], timeout=1)\n")
            _write(root, "broken.py", "def broken(:\n")
            status, detail = guard.check_subprocess_bounds(root)
            self.assertEqual(status, "FAIL")
            self.assertIn("broken.py", detail)


class RealRepoInvariantTests(unittest.TestCase):
    """The invariant itself, against THIS ported `scripts/` directory -- the
    run that makes this guard enforcement rather than documentation. This
    directory is both the tool's home and a legitimate scan target: every
    ported tool here carries real, bounded subprocess/git calls.
    """

    def test_the_scan_finds_a_substantial_number_of_spawn_calls(self) -> None:
        # NON-VACUITY, mandatory. `test_no_unbounded_spawn_remains` below is an
        # `assertEqual(..., ())` over a comprehension, which an empty scan
        # satisfies having compared nothing. Measured at 16 spawn/urlopen sites
        # across the six ported *.py files at the point they were packaged for
        # reuse; floored at 5, well below that, so ordinary future additions
        # cannot trip it while a collapse to near-zero still means discovery broke.
        result = guard.scan_directory(THIS_DIR)
        self.assertGreaterEqual(
            result.spawn_calls, 5, f"spawn discovery collapsed: {result.spawn_calls} found"
        )
        self.assertGreaterEqual(result.files, 3, "the *.py glob found almost nothing")

    def test_known_files_are_actually_scanned(self) -> None:
        # A floor on the count alone would survive a glob that matched only one
        # enormous file. Name the members that must be in the set.
        scanned = guard.scan_targets(THIS_DIR)
        names = {p.name for p in scanned}
        for expected in ("mutation_probe.py", "check_no_em_dash.py", "test_mutation_probe.py"):
            self.assertIn(expected, names)

    def test_no_unbounded_spawn_remains(self) -> None:
        result = guard.scan_directory(THIS_DIR)
        self.assertEqual(
            [f"{v.path}:{v.line} {v.call}" for v in result.violations],
            [],
            "every subprocess/urlopen call under this directory must pass timeout=",
        )

    def test_the_check_reports_pass_on_the_real_tree(self) -> None:
        status, detail = guard.check_subprocess_bounds(THIS_DIR)
        self.assertEqual(status, "PASS", detail)


_FULL_GIT_ENV_LITERAL = (
    'env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", '
    '"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e", '
    '"GIT_TERMINAL_PROMPT": "0"}'
)


class GitIdentityClassificationTests(unittest.TestCase):
    """The PURE layer of `git-identity-env`: what counts as a git-prefixed
    spawn at all, and whether its `env=` resolves to every required key. Fed
    hand-written fixtures via `scan_git_identity_source`, mirroring
    `SpawnClassificationTests`'s split from the discovery layer below.
    """

    def test_a_git_call_with_no_env_is_a_violation(self) -> None:
        result = guard.scan_git_identity_source(
            "import subprocess\nsubprocess.run(['git', 'status'])\n", "x.py"
        )
        self.assertEqual(result.git_calls, 1)
        self.assertEqual([v.line for v in result.violations], [2])
        self.assertEqual(result.violations[0].reason, guard._REASON_NO_ENV_KWARG_GIT)

    def test_a_git_call_with_full_inline_env_is_not_a_violation(self) -> None:
        source = f"import subprocess\nsubprocess.run(['git', 'status'], {_FULL_GIT_ENV_LITERAL})\n"
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.git_calls, 1)
        self.assertEqual(result.violations, ())

    def test_a_non_git_subprocess_call_is_not_judged_by_this_rule(self) -> None:
        # KNOWN-BAD, the other direction: an unbounded-identity `cargo` call is
        # `subprocess-bounds`'s business, not this one's.
        result = guard.scan_git_identity_source(
            "import subprocess\nsubprocess.run(['cargo', 'build'])\n", "x.py"
        )
        self.assertEqual(result.git_calls, 0)
        self.assertEqual(result.violations, ())

    def test_a_variable_argv_is_not_recognized_as_git(self) -> None:
        # The disclosed decidability boundary, stated in the module docstring:
        # an unverifiable argv is not a git call this scanner can judge, the
        # same trade-off `_is_bounded` already makes for `timeout=`.
        source = "import subprocess\nargv = ['git', 'status']\nsubprocess.run(argv)\n"
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.git_calls, 0)
        self.assertEqual(result.violations, ())

    def test_a_tuple_argv_is_recognized_too(self) -> None:
        result = guard.scan_git_identity_source(
            "import subprocess\nsubprocess.run(('git', 'status'))\n", "x.py"
        )
        self.assertEqual(result.git_calls, 1)

    def test_a_binop_prefixed_by_a_git_literal_is_recognized(self) -> None:
        # `["git"] + args` visibly starts with the literal form the module
        # docstring already names as recognized.
        source = "import subprocess\nargs = ['status']\nsubprocess.run(['git'] + args)\n"
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.git_calls, 1)
        self.assertEqual(result.violations[0].reason, guard._REASON_NO_ENV_KWARG_GIT)

    def test_a_binop_bounded_with_full_env_is_not_a_violation(self) -> None:
        source = (
            "import subprocess\n"
            "args = ['status']\n"
            f"subprocess.run(['git'] + args, {_FULL_GIT_ENV_LITERAL})\n"
        )
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.git_calls, 1)
        self.assertEqual(result.violations, ())

    def test_a_binop_not_prefixed_by_git_is_not_recognized(self) -> None:
        # The fail-closed direction: `args + ["git"]` does not START with the
        # literal (git is on the RIGHT), so this scanner correctly stays
        # silent -- it is not a git-prefixed argv by this scanner's own
        # definition, whatever a shell would eventually do with it.
        source = "import subprocess\nargs = ['status']\nsubprocess.run(args + ['git'])\n"
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.git_calls, 0)

    def test_an_unaliased_shlex_split_of_a_git_string_is_recognized(self) -> None:
        source = "import shlex, subprocess\nsubprocess.run(shlex.split('git log -1'))\n"
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.git_calls, 1)
        self.assertEqual(result.violations[0].reason, guard._REASON_NO_ENV_KWARG_GIT)

    def test_a_bare_shlex_split_of_exactly_git_is_recognized(self) -> None:
        source = "import shlex, subprocess\nsubprocess.run(shlex.split('git'))\n"
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.git_calls, 1)

    def test_shlex_split_of_a_non_git_string_is_not_recognized(self) -> None:
        # Fail-closed the other direction: "github-cli ..." starts with
        # "git" as a SUBSTRING but not as the first whitespace-delimited
        # token, which is the question this scanner actually asks.
        source = "import shlex, subprocess\nsubprocess.run(shlex.split('github-cli pr list'))\n"
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.git_calls, 0)

    def test_an_aliased_shlex_split_is_not_recognized(self) -> None:
        # Disclosed narrowing: only the bare name `shlex` is traced.
        source = "import shlex as sh, subprocess\nsubprocess.run(sh.split('git log'))\n"
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.git_calls, 0)

    def test_a_string_split_method_call_is_not_mistaken_for_shlex(self) -> None:
        # The false-positive risk this shape has to avoid: ordinary
        # `str.split(...)` is an extremely common call and has nothing to do
        # with argv construction.
        source = "import subprocess\nsubprocess.run('git log'.split(' '))\n"
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.git_calls, 0)

    def test_a_module_constant_env_is_traced(self) -> None:
        source = (
            "import subprocess\n"
            '_GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", '
            '"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e", '
            '"GIT_TERMINAL_PROMPT": "0"}\n'
            "subprocess.run(['git', 'status'], env=_GIT_ENV)\n"
        )
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.git_calls, 1)
        self.assertEqual(result.violations, ())

    def test_a_self_attr_env_is_traced(self) -> None:
        # The `self.env` shape: a `setUp`-style method builds it, another
        # method of the same class reads it.
        source = (
            "import subprocess\n"
            "class T:\n"
            "    def setUp(self):\n"
            '        self.env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", '
            '"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e", '
            '"GIT_TERMINAL_PROMPT": "0"}\n'
            "    def git(self):\n"
            "        subprocess.run(['git', 'status'], env=self.env)\n"
        )
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.git_calls, 1)
        self.assertEqual(result.violations, ())

    def test_a_dict_built_via_subscript_assignment_is_traced(self) -> None:
        # `mutation_probe.py`'s own `_run_git` shape: `env = dict(os.environ)`
        # followed by subscript assignments.
        source = (
            "import os, subprocess\n"
            "def f():\n"
            "    env = dict(os.environ)\n"
            "    env['GIT_AUTHOR_NAME'] = 't'\n"
            "    env['GIT_AUTHOR_EMAIL'] = 't@e'\n"
            "    env['GIT_COMMITTER_NAME'] = 't'\n"
            "    env['GIT_COMMITTER_EMAIL'] = 't@e'\n"
            "    env['GIT_TERMINAL_PROMPT'] = '0'\n"
            "    subprocess.run(['git', 'status'], env=env)\n"
        )
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.git_calls, 1)
        self.assertEqual(result.violations, ())

    def test_a_spread_of_a_tracked_constant_is_traced(self) -> None:
        source = (
            "import os, subprocess\n"
            '_GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", '
            '"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e", '
            '"GIT_TERMINAL_PROMPT": "0"}\n'
            "subprocess.run(['git', 'status'], env={**_GIT_ENV, 'PATH': os.environ.get('PATH', '')})\n"
        )
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.git_calls, 1)
        self.assertEqual(result.violations, ())

    def test_missing_keys_are_named_in_the_reason(self) -> None:
        source = (
            "import subprocess\n"
            "subprocess.run(['git', 'status'], env={'GIT_TERMINAL_PROMPT': '0'})\n"
        )
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(len(result.violations), 1)
        reason = result.violations[0].reason
        for missing in (
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
        ):
            self.assertIn(missing, reason)
        self.assertNotIn("GIT_TERMINAL_PROMPT,", reason)  # already satisfied

    def test_extra_keys_beyond_the_required_set_do_not_block(self) -> None:
        # A hardening key such as `GIT_CONFIG_NOSYSTEM` may ride alongside the
        # required set; the check asks only whether the required keys are
        # PRESENT, never whether the dict is exactly that set.
        source = (
            "import subprocess\n"
            'env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", '
            '"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e", '
            '"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"}\n'
            "subprocess.run(['git', 'status'], env=env)\n"
        )
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.violations, ())

    def test_a_syntax_error_is_raised_not_swallowed(self) -> None:
        with self.assertRaises(SyntaxError):
            guard.scan_git_identity_source("def broken(:\n", "x.py")


_COMPLETE_ENV_LITERAL = (
    '{"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", '
    '"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e", "GIT_TERMINAL_PROMPT": "0"}'
)


class ScopeAwareResolutionTests(unittest.TestCase):
    """Module-wide last-wins name resolution can launder a real violation:
    two different functions each binding a local `env` under the same name,
    one complete and one not, must not cross-resolve. Pinned here so a
    regression to module-wide resolution fails this suite by name.
    """

    def test_two_functions_with_the_same_local_name_do_not_launder(self) -> None:
        # `first()` binds an INCOMPLETE local `env` and commits with it;
        # `second()`, later in the file, happens to bind the SAME name `env`
        # to a COMPLETE dict. Module-wide last-wins would read `first()`'s
        # call as clean because `second()`'s dict was the last one seen
        # anywhere in the file -- a real violation laundered by a naming
        # coincidence.
        source = (
            "import subprocess\n"
            "def first():\n"
            '    env = {"GIT_TERMINAL_PROMPT": "0"}\n'
            "    subprocess.run(['git', 'commit'], env=env)\n"
            "def second():\n"
            f"    env = {_COMPLETE_ENV_LITERAL}\n"
            "    subprocess.run(['git', 'status'], env=env)\n"
        )
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.git_calls, 2)
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(result.violations[0].line, 4)
        self.assertIn("GIT_AUTHOR_NAME", result.violations[0].reason)

    def test_the_same_call_alone_without_the_laundering_sibling_is_flagged_identically(self) -> None:
        # `first()` in isolation must report the SAME violation whether or
        # not an unrelated `second()` exists elsewhere in the file -- the
        # defining property of correct scoping: an unrelated function must
        # not change the verdict.
        source = (
            "import subprocess\n"
            "def first():\n"
            '    env = {"GIT_TERMINAL_PROMPT": "0"}\n'
            "    subprocess.run(['git', 'commit'], env=env)\n"
        )
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.git_calls, 1)
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(result.violations[0].line, 4)

    def test_a_local_dict_resolves_to_its_own_scope_not_a_later_functions(self) -> None:
        # THREE functions each build their own local `env`. Each call site
        # must resolve to ITS OWN function's dict -- the first is
        # deliberately incomplete so a regression to last-wins (which would
        # resolve every call to the THIRD function's complete dict) fails
        # this test by flipping violations from 1 back to 0.
        source = (
            "import subprocess\n"
            "def alpha():\n"
            '    env = {"GIT_TERMINAL_PROMPT": "0"}\n'  # incomplete, on purpose
            "    subprocess.run(['git', 'a'], env=env)\n"
            "def beta():\n"
            f"    env = {_COMPLETE_ENV_LITERAL}\n"
            "    subprocess.run(['git', 'b'], env=env)\n"
            "def gamma():\n"
            f"    env = {_COMPLETE_ENV_LITERAL}\n"
            "    subprocess.run(['git', 'c'], env=env)\n"
        )
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.git_calls, 3)
        self.assertEqual([v.line for v in result.violations], [4])

    def test_a_nested_closure_reads_its_enclosing_functions_dict(self) -> None:
        # An outer function builds a complete `env` and a NESTED closure
        # reads it. A two-tier (nearest-function-or-module) lookup would miss
        # this because the call's nearest enclosing function never assigns
        # `env` itself; the real fix walks the full lexical chain.
        source = (
            "import subprocess\n"
            "def outer():\n"
            f"    env = {_COMPLETE_ENV_LITERAL}\n"
            "    def inner(*args):\n"
            "        subprocess.run(['git', *args], env=env)\n"
            "    inner('status')\n"
        )
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.git_calls, 1)
        self.assertEqual(result.violations, ())

    def test_a_nested_closure_with_an_incomplete_outer_env_is_still_flagged(self) -> None:
        # The other half: the closure fix must not become a blanket pass for
        # anything inside a nested function.
        source = (
            "import subprocess\n"
            "def outer():\n"
            '    env = {"GIT_TERMINAL_PROMPT": "0"}\n'
            "    def inner(*args):\n"
            "        subprocess.run(['git', *args], env=env)\n"
            "    inner('status')\n"
        )
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(len(result.violations), 1)

    def test_self_attr_still_shares_across_methods_of_one_class(self) -> None:
        # `self.X` must NOT be narrowed to per-method scoping the way plain
        # names are: a `setUp`-style method builds `self.env` and another
        # method of the same class reads it, which is the real, correct
        # pattern this scoping must keep working.
        source = (
            "import subprocess\n"
            "class T:\n"
            "    def setUp(self):\n"
            f"        self.env = {_COMPLETE_ENV_LITERAL}\n"
            "    def git(self):\n"
            "        subprocess.run(['git', 'status'], env=self.env)\n"
        )
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.violations, ())

    def test_self_attr_from_a_different_class_does_not_leak_in(self) -> None:
        # The laundering direction applied to classes: two DIFFERENT classes
        # each with their own `self.env`, one complete and one not, must not
        # cross-resolve.
        source = (
            "import subprocess\n"
            "class Incomplete:\n"
            "    def setUp(self):\n"
            '        self.env = {"GIT_TERMINAL_PROMPT": "0"}\n'
            "    def git(self):\n"
            "        subprocess.run(['git', 'status'], env=self.env)\n"
            "class Complete:\n"
            "    def setUp(self):\n"
            f"        self.env = {_COMPLETE_ENV_LITERAL}\n"
        )
        result = guard.scan_git_identity_source(source, "x.py")
        self.assertEqual(result.git_calls, 1)
        self.assertEqual(len(result.violations), 1)


class GitIdentityCrossModuleTests(unittest.TestCase):
    """The decidability boundary the module docstring discloses: an `env=`
    naming a constant imported from ANOTHER file under the scanned directory.
    Needs the DIRECTORY-level scan (`scan_git_identity_directory`), since
    resolving it is inherently a multi-file question the pure single-file
    scan cannot answer (and does not claim to -- see its own docstring).
    """

    def test_an_import_from_a_sibling_module_is_resolved(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hermetic-cross-") as td:
            root = Path(td)
            _write(
                root,
                "helpers.py",
                '_DELTA_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", '
                '"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e", '
                '"GIT_TERMINAL_PROMPT": "0"}\n',
            )
            _write(
                root,
                "user.py",
                "import subprocess\n"
                "from helpers import _DELTA_ENV\n"
                "subprocess.run(['git', 'status'], env=_DELTA_ENV)\n",
            )
            result = guard.scan_git_identity_directory(root)
            self.assertEqual(result.git_calls, 1)
            self.assertEqual(result.violations, ())

    def test_an_import_from_a_module_missing_a_key_is_still_a_violation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hermetic-cross-bad-") as td:
            root = Path(td)
            _write(root, "helpers.py", '_PARTIAL_ENV = {"GIT_TERMINAL_PROMPT": "0"}\n')
            _write(
                root,
                "user.py",
                "import subprocess\n"
                "from helpers import _PARTIAL_ENV\n"
                "subprocess.run(['git', 'status'], env=_PARTIAL_ENV)\n",
            )
            result = guard.scan_git_identity_directory(root)
            self.assertEqual(len(result.violations), 1)
            self.assertIn("GIT_AUTHOR_NAME", result.violations[0].reason)

    def test_a_cross_module_helper_function_call_is_invisible_here_by_design(self) -> None:
        # Disclosed in the module docstring: a call routed through a helper
        # FUNCTION (not a bare env constant) is not a `subprocess.*` call at
        # its call site, so this scanner does not count it as a git call at
        # all -- coverage lives at the helper's own definition instead.
        with tempfile.TemporaryDirectory(prefix="hermetic-cross-helper-") as td:
            root = Path(td)
            _write(
                root,
                "helpers.py",
                "import subprocess\n"
                "def _git(args):\n"
                "    return subprocess.run(['git', *args])\n",  # helper itself IS a violation
            )
            _write(
                root,
                "user.py",
                "import helpers\nhelpers._git(['status'])\n",  # not a subprocess.* call
            )
            result = guard.scan_git_identity_directory(root)
            # Exactly the ONE real call site (inside helpers.py's own body).
            self.assertEqual(result.git_calls, 1)
            self.assertEqual([v.path for v in result.violations], ["helpers.py"])


class GitIdentityDiscoveryLayerTests(unittest.TestCase):
    """The rule applied to `git-identity-env`'s discovery layer, mirroring
    `DiscoveryLayerTests`."""

    def test_a_known_bad_fixture_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hermetic-gitid-bad-") as td:
            root = Path(td)
            _write(root, "mod.py", "import subprocess\nsubprocess.run(['git', 'status'])\n")
            status, detail = guard.check_git_identity_env(root)
            self.assertEqual(status, "FAIL")
            self.assertIn("mod.py:2", detail)

    def test_a_clean_fixture_directory_passes_without_being_empty(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hermetic-gitid-good-") as td:
            root = Path(td)
            _write(
                root,
                "mod.py",
                f"import subprocess\nsubprocess.run(['git', 'status'], {_FULL_GIT_ENV_LITERAL})\n",
            )
            status, detail = guard.check_git_identity_env(root)
            self.assertEqual(status, "PASS", detail)

    def test_an_empty_directory_fails_rather_than_reporting_clean(self) -> None:
        # Zero *.py files at all: a wrong path far more often than a real,
        # empty repo, so this FAILs unconditionally regardless of `--root`.
        with tempfile.TemporaryDirectory(prefix="hermetic-gitid-empty-") as td:
            status, detail = guard.check_git_identity_env(Path(td))
            self.assertEqual(status, "FAIL")
            self.assertIn("0 file", detail)

    def test_a_directory_with_no_git_calls_passes_with_an_explanatory_message(self) -> None:
        # DISCLOSED GENERALIZATION, mirroring `DiscoveryLayerTests`'s sibling
        # test: most scripts directories never shell out to git at all, so
        # files-but-no-git-calls is a clean PASS, not a vacuity FAIL.
        with tempfile.TemporaryDirectory(prefix="hermetic-gitid-vacuous-") as td:
            root = Path(td)
            _write(root, "mod.py", "import subprocess\nsubprocess.run(['cargo', 'build'])\n")
            status, detail = guard.check_git_identity_env(root)
            self.assertEqual(status, "PASS", detail)
            self.assertIn("0 git-prefixed", detail)

    def test_an_unparseable_file_fails_the_check_by_name(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hermetic-gitid-broken-") as td:
            root = Path(td)
            _write(
                root,
                "mod.py",
                f"import subprocess\nsubprocess.run(['git'], {_FULL_GIT_ENV_LITERAL})\n",
            )
            _write(root, "broken.py", "def broken(:\n")
            status, detail = guard.check_git_identity_env(root)
            self.assertEqual(status, "FAIL")
            self.assertIn("broken.py", detail)


class GitIdentityRealRepoInvariantTests(unittest.TestCase):
    """The invariant itself, against THIS ported `scripts/` directory,
    mirroring `RealRepoInvariantTests`."""

    def test_the_scan_finds_a_substantial_number_of_git_calls(self) -> None:
        # NON-VACUITY, mandatory, the same shape `RealRepoInvariantTests`
        # demands for `subprocess-bounds`. Measured at 3 git-prefixed call
        # sites across the six ported *.py files at the point they were
        # packaged for reuse (mutation_probe.py's own `_run_git`, plus its
        # test fixtures); floored at 1, so a collapse to zero still means discovery broke.
        result = guard.scan_git_identity_directory(THIS_DIR)
        self.assertGreaterEqual(
            result.git_calls, 1, f"git-call discovery collapsed: {result.git_calls} found"
        )
        self.assertGreaterEqual(result.files, 3, "the *.py glob found almost nothing")

    def test_known_files_are_actually_scanned(self) -> None:
        # A floor on the count alone would survive a glob that matched only
        # one enormous file. Name the members that must be in the set,
        # mirroring `RealRepoInvariantTests`'s own version of this test.
        scanned = guard.scan_targets(THIS_DIR)
        names = {p.name for p in scanned}
        for expected in ("mutation_probe.py", "check_hermetic_bounds.py"):
            self.assertIn(expected, names)

    def test_no_git_call_with_missing_identity_remains(self) -> None:
        result = guard.scan_git_identity_directory(THIS_DIR)
        self.assertEqual(
            [f"{v.path}:{v.line} {v.call} ({v.reason})" for v in result.violations],
            [],
            "every git-prefixed subprocess call under this directory must pass env= carrying "
            f"{sorted(guard.GIT_IDENTITY_KEYS)}",
        )

    def test_the_check_reports_pass_on_the_real_tree(self) -> None:
        status, detail = guard.check_git_identity_env(THIS_DIR)
        self.assertEqual(status, "PASS", detail)


class MainWiringTests(unittest.TestCase):
    """`main()`'s own dispatch. Every sub-check above is tested as a
    function, but none of those tests notices a sub-check silently dropped
    from `main()`'s own `results` list -- that line is wiring, not a
    predicate.

    A real subprocess, not an in-process call with `sys.stdout` swapped for
    a `StringIO`: `main()` calls `sys.stdout.reconfigure(...)` as its first
    line, which a `StringIO` does not implement, and this exercises the
    actual CLI entry point besides.

    Each assertion below matches the DETAIL a live scan prints (a count no
    hand-written stub string can produce), not merely the label: a stub
    return value with the right name kept in the list would otherwise make a
    sub-check inert with the suite still green.
    """

    def test_the_real_tree_run_lists_every_sub_check(self) -> None:
        done = subprocess.run(
            [sys.executable, str(THIS_DIR / "check_hermetic_bounds.py"), "--root", str(THIS_DIR)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for name, detail_pattern in (
            ("subprocess-bounds", r"\] subprocess-bounds: \d+ spawn/urlopen call\(s\)"),
            ("git-identity-env", r"\] git-identity-env: \d+ git-prefixed spawn call\(s\)"),
        ):
            self.assertIn(f"] {name}:", done.stdout, f"{name} missing from output entirely")
            self.assertRegex(
                done.stdout,
                detail_pattern,
                f"{name}'s line does not carry a scanned count -- a stub could produce the "
                f"label without ever running the real scan",
            )

    def test_the_default_root_is_scripts_under_cwd(self) -> None:
        # No --root at all: the default must be `scripts/` under the process
        # cwd, never this tool's own install directory. Run from a scratch
        # cwd holding a `scripts/` with one clean file, so a wrong default
        # (this tool's own directory) would report a different count.
        with tempfile.TemporaryDirectory(prefix="hermetic-default-root-") as td:
            root = Path(td)
            (root / "scripts").mkdir()
            _write(root / "scripts", "mod.py", "import subprocess\nsubprocess.run(['x'], timeout=1)\n")
            done = subprocess.run(
                [sys.executable, str(THIS_DIR / "check_hermetic_bounds.py")],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=120,
            )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("1 spawn/urlopen call(s) across 1 file(s)", done.stdout)


if __name__ == "__main__":
    unittest.main()
