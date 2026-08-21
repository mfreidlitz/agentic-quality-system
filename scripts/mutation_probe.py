#!/usr/bin/env python3
"""Apply a NAMED substitution to a file, run a suite, and report whether the
suite noticed.

**This is a TOOL, not a check.** It is deliberately named `mutation_probe.py`
rather than `check_mutation_probe.py`: it answers a question an author or a
reviewer asks about one specific guard, on demand, and there is no tree state
it could gate on. A repo may run its own population of `check_*.py` gates,
discovered and enforced by that repo's own harness; this tool owns no paths
and judges no tree, so it never joins that population, whatever a given repo
calls it.

**THE TOOL'S OWN CONTRACT (`probe-honesty`), stated once so nothing below has
to be taken on faith.** Refuse a verdict, never report KILLED or SURVIVED,
when the run being scored did not actually happen: an import error, a skip
masquerading as a pass, or a run that went red but names no failing test are
all refusals, never kills. A branch that survives every mutation this tool
throws at it must be deleted rather than kept as an undefended guard, because
a single SURVIVED verdict means the test is missing, not that the guard is
fine. Every `probe-honesty` reference in this module points back to this
paragraph.

**Why it exists, and the lineage is two failures in one evening.** A mutation
proof reported `reddened 1` where the 1 was `unittest.loader._FailedTest` for a
class name that does not exist: nothing ran, and the run that did not happen
was scored as a kill. A second reported `reddened 0` across three mutants when
the isolated copy was missing the ledger, so again no test ran, and the absence
was scored as three survivals. Both were caught only because the number looked
wrong to a person, which is not a mechanism. Hand-rolled twice in one evening
is the argument for a file.

**The whole design follows from one observation: a unittest run that fails to
IMPORT is textually almost identical to one whose assertions failed.** Both
print `ERROR:` lines, both exit non-zero, both end in `FAILED`. The difference
is that the loader error runs `Ran 1 test` regardless of how many the suite
declares, and names `unittest.loader._FailedTest`. So the verdict is refused
unless all of the following hold at once:

1. a `Ran N tests` line PARSED, with N at or above a declared floor;
2. the UNMUTATED baseline ran GREEN over that same N;
3. the mutated run reported the SAME N;
4. the mutated run SKIPPED no more tests than the baseline. `Ran N` counts
   skips, so a mutation that turns the defending test into a skip (a
   `@unittest.skipUnless` on the very constant under mutation) leaves N
   identical while the guard's own test never executes; scoring that would
   print SURVIVED over the one test most likely to be the guard's. The
   floor in condition 1 is therefore on tests EXECUTED, not collected:
   `Ran N` alone cannot tell the two apart, which is exactly what condition
   4 exists to catch;
5. the run FINISHED at all. Both the baseline and the mutated
   attempt can HANG past `--timeout` instead of exiting, which leaves `ran`
   `None` exactly like a crash or a compile error does -- but a hang is a
   different fact, worth a different verdict: `INCONCLUSIVE`, never folded
   into the generic `NO RUN` a run that actually EXITED without a
   transcript produces. The lineage: mutating a CLI dispatch made `--since`
   fall through to a live poll with no suite-controlled bound, the suite
   did not fail, it hung, and under load that once scored a false
   `SURVIVED`.

Any of them missing is `NO RUN`, `INCONCLUSIVE` or `REFUSED`, never `KILLED`
and never `SURVIVED`. Condition 3 is the one that catches the real incident: a
loader error collapses N to 1, so it can never be mistaken for a kill.
Condition 5 is what makes a HANG its own named outcome rather than silently
scoring whatever the process happened to print, or not print, before this
tool's own timeout ended it.

**FLOOR: a probe whose baseline reddens anything refuses to proceed.** A
mutation scored against an already-red suite proves nothing, because the
failure it "caused" was there before it arrived.

**The restore of the MUTATED FILE is ASSERTED, not claimed.** The original
bytes are read before anything is written and put back in a `finally`, then
re-read and compared byte for byte, and the comparison is PRINTED with both
digests rather than summarized as "restored". A tool that edits a tracked file
and reports its own tidiness in prose is the class this repo keeps paying for.

**The scope of that promise is exactly one file, stated because the first
version implied more.** Anything the SUITE itself writes is outside it, and so
is compiled bytecode a child may leave in a `__pycache__` (which is gitignored,
so `git status` cannot see it either). The mtime restore above is what keeps
such bytecode from being believed; it is not a claim that none was written.

**IT SPEAKS RUST TOO, and the reason is that a `defended_by`-style report was
otherwise unfillable for a Rust-based repo.** A reviewer's report demands the
NAMED tests that reddened. This module shelled to `sys.executable` and parsed a
unittest transcript; `check_mutants` mutates Rust but reports MUTANT names,
because cargo-mutants does not record the failing test in `outcomes.json`. So a
segment touching Rust source met a required field no tool could fill. Point
`--suite` at a `Cargo.toml` and the same probe runs `cargo test` instead, parses
which tests FAILED, and returns them in the same shape. **Every refusal above
applies unchanged** - the baseline must be green, the floor holds, the
population must match, the restore is asserted - because those refusals are the
whole value and a second backend that relaxed one would be worse than no second
backend.

Two Rust-specific facts, disclosed rather than discovered:

- **Prose discrimination is not performed for Rust.** `_inert_spans` is
  `tokenize` plus `ast`, both Python's, so a `.rs` file yields no spans and every
  occurrence counts as code. A substitution landing only inside a `//` comment
  would score as SURVIVED over a no-op. The probe PRINTS this on every Rust run
  rather than hand-rolling a Rust tokenizer, a lesson learned the hard way:
  a hand-rolled tokenizer elsewhere in this project's history produced seven
  hollow assertions and three enumerated fail-opens, each fix followed by
  another.
- **A compile error is NO RUN, never a kill.** An unviable mutant produces no
  `test result:` line, which the parser reports as "no run happened" and the
  scoring refuses. That is the same shape as a Python loader error and it is
  refused for the same reason.

Usage:

    python mutation_probe.py \\
        --file scripts/check_thing.py \\
        --old 'MIN_ROWS = 5' --new 'MIN_ROWS = 0' \\
        --suite scripts/test_check_thing.py --floor 5

    python mutation_probe.py \\
        --file example_crate/src/lib.rs \\
        --old 'if items.is_empty() {' --new 'if false {' \\
        --suite example_crate/Cargo.toml --floor 10

**`--tests` is the fast path**, for the ordinary case where a
reviewer already believes they know which test(s) defend a guard:

    python mutation_probe.py \\
        --file scripts/check_thing.py \\
        --old 'MIN_ROWS = 5' --new 'MIN_ROWS = 0' \\
        --suite scripts/test_check_thing.py --floor 5 \\
        --tests FloorTests.test_MIN_ROWS_is_five

The named test(s) run first, against baseline and mutant alike, without
touching the rest of the suite; a KILLED there is seconds rather than the
minutes a suite of a hundred-plus tests costs twice. When none of them
redden this falls back AUTOMATICALLY to the full paired suite before any
verdict, so a wrong guess at which test defends a guard costs nothing but
that one fast attempt, never a manufactured SURVIVED.

**`--nth` disambiguates an `--old` that occurs more than once
in code.** A plain `--old`/`--new` pair mutates EVERY in-code occurrence at
once, which is refused the moment there is more than one: name which
occurrence (1-based, in document order) with `--nth`, or lengthen `--old`
(more surrounding text, e.g. indentation) until it is unique on its own --
both idioms keep working, this only refuses the silent third option of
mutating all of them and calling it one substitution. The same refusal
guards the other direction too: a `--new` matching the `if True:`
CAPABILITY-WIDENING shape (`trace-the-known-bad`'s prescribed idiom in
reverse) is refused outright, never scored.

Exit 0 only on `KILLED`. `SURVIVED`, `NO RUN`, `INCONCLUSIVE` and `REFUSED`
all exit 1, so the tool can be chained: the thing a caller wants to assert is
that the guard is defended, and every other outcome is a reason to look.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tokenize
from dataclasses import dataclass, field, replace
from pathlib import Path

# The CALLER's working directory, read once at import time, NEVER derived
# from where this module itself is installed. This tool is meant to be
# installed once and pointed at many different target repos (`--file`,
# `--suite`, and the journal below all resolve relative to this), so tying
# it to `__file__`'s own location would make every relative path resolve
# against the TOOL's install directory instead of whichever repo it is
# actually probing. The documented usage is `cd <target repo> && python
# <path-to-this-tool>/mutation_probe.py --file <relative path> ...`, which is
# exactly the convention that makes `Path.cwd()` the target repo's root.
REPO_ROOT = Path.cwd()

# Bounded per the caller's own bounded-waits convention, as a named constant
# rather than a literal. Sized above an ordinary suite that shells to a
# compiler or another slow subprocess, so a genuine slow suite is not
# reported as a hang; a caller with something slower still passes its own
# `--timeout`.
SUITE_TIMEOUT_SECONDS = 900

# How many trailing lines of a CARGO invocation's own
# combined stdout+stderr are kept as a self-diagnosing tail on a NO RUN or
# INCONCLUSIVE verdict. Twenty is enough to carry a rustc error block or an
# MSVC `LNK1104` linker line without turning every refusal into a wall of
# text; the row this exists for names "the last ~20 lines" directly.
TRANSCRIPT_TAIL_LINES = 20

# The verdicts. `NO RUN` and `REFUSED` are distinct on purpose: `NO RUN` means
# the suite did not execute a comparable population (a loader error, an import
# failure, an empty discovery), while `REFUSED` means the probe declined to
# start or to score because a precondition of its own was not met.
#
# `INCONCLUSIVE` is a FOURTH way this tool can lie, distinct from
# both. A `NO RUN` means the process EXITED without producing a comparable
# transcript; `INCONCLUSIVE` means it never exited at all -- it HUNG until the
# probe's own `--timeout` killed it. Folding the two together loses the one
# fact a reader most needs (the run was still going when this tool gave up on
# it, not that it finished and produced garbage), and `probe-honesty`
# instructs DELETING a guard that scores SURVIVED, so a hang that is ever
# misread as a clean pass argues for removing a working guard. The lineage:
# mutating a CLI dispatch made `--since` fall through to a live poll with no
# suite-controlled bound, and under load that once scored a false SURVIVED.
KILLED = "KILLED"
SURVIVED = "SURVIVED"
NO_RUN = "NO RUN"
REFUSED = "REFUSED"
INCONCLUSIVE = "INCONCLUSIVE"

# The default floor. A suite running fewer tests than this is either a stub or
# a collection that silently stopped matching, and either way a verdict over it
# is not worth the bytes it is printed on. Callers raise it per suite.
DEFAULT_FLOOR = 1

# How far the mutated file's mtime is pushed ahead of the original's.
#
# THE ENV VARIABLE IS NOT ENOUGH, and a step 9 review proved it by measurement
# rather than by argument. `PYTHONPYCACHEPREFIX` reaches the DIRECT child only;
# a suite that spawns its own grandchild with an explicit `env=` (the shape
# `test_must_not_fire.py` and `test_check_red.py` already use) hands it a clean
# environment, and that grandchild resolves the real `__pycache__`. Measured:
# six identical probes returned KILLED, SURVIVED, SURVIVED, SURVIVED, KILLED,
# KILLED. A nondeterministic mutation probe is worse than none.
#
# mtime is the fix because it is a property of the FILE rather than of a
# process tree: CPython invalidates a `.pyc` whose recorded source mtime does
# not match, so bumping it invalidates the cache for every reader at any depth,
# including one that inherits nothing from us. The skew has to exceed the
# filesystem's mtime granularity, which is 2s on FAT and 1s on some network
# mounts, so 10s is chosen with room rather than 1.
MTIME_SKEW_SECONDS = 10

# --- the RUST half -------------------------------------------------------
#
# `defended_by.reddened` demands the NAMED tests that went red, and until this
# existed no tool in the repo could produce that for a Rust guard: this module
# shelled to `sys.executable` and parsed a unittest transcript, while
# `check_mutants` mutates Rust but reports MUTANT names (`<file>:<line>
# <function> -> <replacement>`) because cargo-mutants does not record the failing
# test in `outcomes.json`. So in the origin repo, every segment touching its
# Rust product met a required field nothing could fill.
#
# The scoring logic is NOT duplicated. `probe()` is unchanged in structure: a
# second parser produces the same `SuiteRun`, and every refusal the Python path
# already had (baseline green, the floor, the same N before and after, the
# byte-exact restore, no verdict over a run that did not happen) applies to Rust
# unaltered. Those refusals are the best-designed thing here and weakening one
# for a new backend would be the whole point thrown away.
CARGO_MANIFEST = "Cargo.toml"

# `--lib --bins --tests` and NOT a bare `cargo test`, which also runs DOC-TESTS.
# A doc-test is reported as `src/lib.rs - foo (line 42)`, a name that moves the
# moment anything above it changes, and editing that file is precisely this
# tool's job. A name that cannot survive the edit being measured is not a name a
# `defended_by` entry can carry.
#
# `--no-fail-fast` because cargo otherwise stops at the first failing TARGET, so
# the mutated run would report a smaller population than the baseline and trip
# the same-N rule for a reason that is not a defect.
CARGO_TEST_ARGS = ("test", "--no-fail-fast", "--lib", "--bins", "--tests")

# `test tests::allows_the_known_host ... FAILED`. The trailing token is the
# outcome; the middle is the name a reader pastes back into `cargo test <name>`.
_CARGO_TEST_LINE_RE = re.compile(
    r"^test (\S+) \.\.\. (ok|FAILED|ignored)", re.MULTILINE
)
# `test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out`
_CARGO_RESULT_RE = re.compile(
    r"^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored",
    re.MULTILINE,
)

_RAN_RE = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
# `Ran N tests` COUNTS SKIPS, so N alone cannot tell an executed test from one
# unittest walked past. The count is already in the transcript, on both the
# `OK (skipped=3)` and `FAILED (failures=1, skipped=3)` tails, and discarding it
# made two separate guarantees hollow. See `UnittestRun.skipped`.
_SKIPPED_RE = re.compile(r"^(?:OK|FAILED)\b.*?\bskipped=(\d+)", re.MULTILINE)
_OK_RE = re.compile(r"^OK\b", re.MULTILINE)
_FAILED_RE = re.compile(r"^FAILED\b", re.MULTILINE)
# `FAIL: test_x (mod.Class.test_x)` and `ERROR: test_x (mod.Class.test_x)`.
# The descriptor in parentheses is what distinguishes a real failure from a
# loader stub, so it is captured rather than discarded.
_OUTCOME_RE = re.compile(r"^(FAIL|ERROR): (\S+)(?: \(([^)]*)\))?", re.MULTILINE)
# The signature of a unittest run that never reached the tests. Matched against
# the descriptor AND the raw text, because the two Python versions in play
# spell the descriptor differently.
_LOADER_MARKER = "unittest.loader._FailedTest"


@dataclass(frozen=True)
class UnittestRun:
    """What one `python test_x.py` or `cargo test` invocation actually did.

    Shared by BOTH backends on purpose. The scoring in `probe()` asks four
    questions (did a run happen, was it green, how many, which names) and none
    of them is language-specific, so a second dataclass would be a second place
    for the contract to drift.

    `ran is None` means no `Ran N tests` line was present at all, which is a
    different fact from `ran == 0` and must never be flattened into it: the
    first is "the output is not a unittest transcript" (a traceback before the
    runner started, a crash, a timeout) and the second is "the runner found
    nothing to run". Both refuse a verdict; they refuse it for different
    reasons and the message says which.
    """

    ran: int | None
    ok: bool | None
    failed: list[str] = field(default_factory=list)
    loader_error: bool = False
    # SKIPS ARE COUNTED IN `ran`, which made two of this tool's three
    # guarantees hollow at once. A mutation that turns the DEFENDING test into
    # a skip (a `@unittest.skipUnless` on the very constant being mutated, a
    # shape common in floor/ceiling checks) leaves `ran` identical and every
    # remaining test green, so the population check sees no change and the run
    # is scored SURVIVED. `CLAUDE.md` then says a branch that survives every
    # mutation is DELETED, so a false SURVIVED does not merely fail to prove a
    # guard: it instructs the removal of a working one. The same arithmetic
    # made `--floor` satisfiable by two real executions out of twenty.
    skipped: int = 0

    # Set ONLY by `run_python_suite`/`run_cargo_suite`'s own
    # `except subprocess.TimeoutExpired:` clause, never by either PARSER:
    # a transcript never carries "I hung", because a process the probe's own
    # timeout killed produces no transcript at all. This is what lets
    # `probe()` tell a HANG apart from a crash or a compile error, both of
    # which also leave `ran is None`. `elapsed_seconds` is the wall clock
    # measured around that same subprocess call, read fresh at the moment the
    # `TimeoutExpired` is caught, so the transcript can say how long the run
    # was allowed before this tool gave up on it.
    timed_out: bool = False
    elapsed_seconds: float | None = None

    # The last `TRANSCRIPT_TAIL_LINES` lines of the CARGO
    # invocation's own combined stdout+stderr, populated ONLY by
    # `run_cargo_suite` (the Python backend's own transcript is already
    # printed in full by the messages that build on `UnittestRun`, so a tail
    # there would just repeat text the reader already has). Existing on
    # every cargo run regardless of verdict, not only NO RUN ones, so a
    # caller never has to special-case "was this captured": read it, it is
    # either the real tail or the empty string. This is what lets a NO RUN
    # or an Unviable verdict on a cargo invocation be self-diagnosing
    # instead of pointing every reader at "most often an UNVIABLE mutant"
    # regardless of the actual cause.
    transcript_tail: str = ""

    @property
    def is_transcript(self) -> bool:
        return self.ran is not None and self.ok is not None

    @property
    def executed(self) -> int:
        """Tests that actually RAN. This is what a floor is asking about."""
        return max(0, (self.ran or 0) - self.skipped)


def parse_unittest_run(text: str) -> UnittestRun:
    """Read a unittest transcript. PURE: handed a string, touches nothing.

    Per `CLAUDE.md`'s known-bad rule this function is handed its input, so its
    known-bad is a crafted INPUT rather than a fixture directory: see
    `test_mutation_probe.py`, which feeds it a real `_FailedTest` transcript
    captured from an actual loader error and asserts it is NOT read as a run.
    """
    # LAST WINS, for the count and for the verdict alike. The first spelling
    # took the FIRST `Ran N` and let any `^OK` beat any `^FAILED`, so a
    # transcript containing a NESTED run (which is exactly what
    # `test_check_red.py` and `test_check_mutants.py` produce, since their job
    # is running other test runs) parsed the inner run's numbers and the inner
    # run's verdict. A genuinely FAILED outer run then read as `ok=True`, which
    # is a false SURVIVED. Found by a step 9 review, reproduced on a crafted
    # nested transcript.
    ran_matches = _RAN_RE.findall(text or "")
    ran = int(ran_matches[-1]) if ran_matches else None

    ok: bool | None = None
    last_ok = None
    for match in _OK_RE.finditer(text or ""):
        last_ok = match.start()
    last_failed = None
    for match in _FAILED_RE.finditer(text or ""):
        last_failed = match.start()
    if last_ok is not None and last_failed is not None:
        ok = last_ok > last_failed
    elif last_ok is not None:
        ok = True
    elif last_failed is not None:
        ok = False

    failed: list[str] = []
    # DELIBERATELY NOT `_LOADER_MARKER in text`. That was the first spelling and
    # the probe caught it on ITSELF: a bare scan over the whole transcript reads
    # any suite that merely MENTIONS the marker as one that failed to import,
    # and the suite most likely to mention it is the one testing loader-error
    # handling. Mutating `_LOADER_MARKER` reported `NO RUN` where the honest
    # answer was `KILLED`, because unittest echoed this module's own fixture
    # text into an assertion diff. The marker only means something in the
    # DESCRIPTOR of a reported outcome, so that is the only place it is read.
    loader_error = False
    for kind, name, descriptor in _OUTCOME_RE.findall(text or ""):
        descriptor = descriptor or ""
        if _LOADER_MARKER in descriptor or _LOADER_MARKER in name:
            loader_error = True
            continue
        failed.append(f"{kind}: {descriptor or name}")

    # LAST WINS here too, for the same nested-transcript reason as `ran`.
    skip_matches = _SKIPPED_RE.findall(text or "")
    skipped = int(skip_matches[-1]) if skip_matches else 0

    return UnittestRun(
        ran=ran, ok=ok, failed=failed, loader_error=loader_error, skipped=skipped
    )


def parse_cargo_run(text: str) -> UnittestRun:
    """Read a `cargo test` transcript. PURE: handed a string, touches nothing.

    Handed its input, so per `CLAUDE.md` its known-bad is a crafted INPUT: see
    `test_mutation_probe.py`, which feeds it a real rustc compile-error
    transcript and asserts it is NOT read as a run.

    **SUMMED, not last-wins, and that is the opposite of the unittest parser's
    rule for a real reason.** A unittest transcript containing two `Ran N` lines
    is a NESTED run and the inner one must not decide; a cargo transcript
    containing two `test result:` lines is the ORDINARY case, one per target
    (the lib's unit tests and each integration test binary), and every one of
    them is part of the population. Taking the last would silently score against
    whichever target happened to print last.

    **`ignored` IS CARGO'S `skipped`, and it is reported as both.** It counts
    toward `ran`, the collected POPULATION, because the same-N rule compares
    populations and a `#[ignore]` test is in the population whether or not it
    executed. It counts toward `skipped` as well, so `executed` excludes it,
    because a floor is asking how many tests actually RAN and an ignored test
    defends nothing. That is exactly the unittest side's `Ran N` versus
    `skipped=N` split, and getting it wrong there is what let `--floor 20` pass
    on two real executions.

    A compile failure produces no `test result:` line at all, so `ran is None`
    and `probe()` reports NO RUN. **That is the correct reading of an unviable
    mutant**: the mutation was not expressible, which is information, and it is
    emphatically not a kill. Scoring a compile error as a kill is the same
    defect as scoring a Python loader error as one, which this module was
    written after doing twice in one evening.
    """
    results = _CARGO_RESULT_RE.findall(text or "")
    if not results:
        return UnittestRun(ran=None, ok=None)

    ran = 0
    skipped = 0
    ok = True
    for verdict, passed, failed, ignored in results:
        ran += int(passed) + int(failed) + int(ignored)
        skipped += int(ignored)
        if verdict != "ok":
            ok = False

    named: list[str] = []
    for name, outcome in _CARGO_TEST_LINE_RE.findall(text or ""):
        if outcome == "FAILED":
            named.append(name)
    return UnittestRun(ran=ran, ok=ok, failed=named, skipped=skipped)


def _tail_text(text: str, lines: int = TRANSCRIPT_TAIL_LINES) -> str:
    """The last `lines` lines of `text`, joined back with newlines.

    PURE: handed a string, touches nothing, so it is tested directly on a
    crafted input per `CLAUDE.md`'s known-bad rule for a function handed its
    input. `""` for empty text rather than a lone newline, so a caller with
    nothing to show prints nothing rather than a misleading blank tail.
    """
    if not text:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def _timeout_capture(exc: subprocess.TimeoutExpired) -> str:
    """The streams a `TimeoutExpired` captured, decoded to `str`.

    CPython never decodes a timeout's captured output, even when the run was
    started with `text=True` (cpython gh-87597): `exc.output`/`exc.stderr`
    arrive as BYTES whenever the child emitted anything before the kill, and
    as `None` when it emitted nothing. Every local hang fixture captured
    nothing, so the bytes shape stayed invisible on this box while CI's real
    cargo child fed `str.join` a bytes line and a real CI run went red on it.
    Decode, never assume.
    """
    parts: list[str] = []
    for chunk in (exc.output, exc.stderr):
        if chunk is None:
            continue
        parts.append(chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else chunk)
    return "".join(parts)


def run_cargo_suite(
    manifest: Path,
    *,
    timeout: int = SUITE_TIMEOUT_SECONDS,
    tests: tuple[str, ...] | None = None,
) -> UnittestRun:
    """Execute the crate `manifest` describes and read what its tests did.

    **`CARGO_TERM_COLOR=never` is load-bearing, not cosmetic.** cargo colorizes
    when it believes it has a terminal, and an ANSI-wrapped `FAILED` does not
    match a parser anchored on the literal. That would report a red run as
    green, which is a false SURVIVED, which is the one direction this whole
    module exists to make impossible.

    **`CARGO_TARGET_DIR` is deliberately NOT set here.** cargo's own resolution
    applies, so a caller can redirect the build and a fixture crate in a temp
    directory naturally builds into its own `target/`. Stated because the
    hazard is real and belongs with the caller: pointing it at a shared
    workspace makes two concurrent probes contend on cargo's file lock, a
    familiar class of failure one level down, and this repo's workspace
    `target/` is 18.8 GB against 12.8 GB free, so a second cold one does not
    fit at all.

    **`tests` is appended after a literal `--`,** which hands the
    name(s) straight to the compiled test BINARY rather than to cargo's own
    argument parser: cargo itself accepts only one bare filter positional
    before `--`, so more than one name can only reach the harness this way.
    The harness ORs multiple filters together (a test runs if its name
    contains ANY of them), which is exactly what a caller checking several
    candidate `defended_by` tests wants.

    **`timeout` bounds ONE attempt, not this call.** The LNK1104
    retry below re-issues the same `subprocess.run(..., timeout=timeout)`
    once more when the first attempt's own combined output carries
    `LNK1104`, so a caller whose first attempt completes (rather than itself
    timing out) with that text can wait up to `2*timeout` in total before
    this function returns - never more, since the retry is bounded by the
    identical explicit `timeout` (`bounded-waits`) and is never itself
    retried. `elapsed_seconds` on a retry timeout is measured from THIS
    call's own start, so it reports that true combined wall time rather than
    only the retry's own slice.
    """
    if shutil.which("cargo") is None:
        return UnittestRun(ran=None, ok=None)
    env = dict(os.environ)
    env["CARGO_TERM_COLOR"] = "never"
    args: tuple[str, ...] = ("cargo", *CARGO_TEST_ARGS, "--manifest-path", str(manifest))
    if tests:
        args = args + ("--", *tests)
    # The two exceptions used to share ONE `except` clause and one
    # identical `UnittestRun(ran=None, ok=None)` return, which meant `cargo`
    # vanishing mid-run (an `OSError`) and `cargo test` HANGING past
    # `timeout` were indistinguishable from the outside. Split so only the
    # timeout carries `timed_out=True`; an `OSError` remains the plain
    # NO-RUN shape, because the process never started running at all.
    started = time.monotonic()
    try:
        completed = subprocess.run(
            args,
            cwd=manifest.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return UnittestRun(
            ran=None,
            ok=None,
            timed_out=True,
            elapsed_seconds=time.monotonic() - started,
            transcript_tail=_tail_text(_timeout_capture(exc)),
        )
    except OSError:
        return UnittestRun(ran=None, ok=None)
    text = completed.stdout + completed.stderr

    # MSVC `LNK1104` ("cannot open file '...probefixture-....exe'")
    # fires when an external handle (an antivirus scanner, an indexer) is
    # still holding the fresh test .exe a moment after cargo produced it, a
    # window that closes on its own within seconds. Retried ONCE, bounded by
    # the same explicit `timeout` this call already carries (`bounded-waits`),
    # rather than failing open on a purely environmental, momentary lock; NOT
    # retried a second time, so a build that is genuinely, persistently
    # broken still reports the honest NO RUN rather than looping. This does
    # NOT touch `_cargo_freshness_stamp`: nothing here rewrites the mutated
    # file, so cargo sees byte-identical source on both attempts, and a real
    # compile error reproduces identically on the retry rather than being
    # laundered into a pass.
    if "LNK1104" in text:
        try:
            completed = subprocess.run(
                args,
                cwd=manifest.parent,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            return UnittestRun(
                ran=None,
                ok=None,
                timed_out=True,
                # `started`, not `retry_started`: this is the SAME NO-RUN
                # line's `elapsed` figure the caller prints against `bound
                # {timeout}s`, and the retry can only fire after the first
                # attempt already ran to completion (an LNK1104 text match
                # requires a finished `completed`), so the true wall time a
                # caller waited is the first attempt PLUS this one - up to
                # `2*timeout`, never just this attempt's own clock.
                elapsed_seconds=time.monotonic() - started,
                transcript_tail=_tail_text(_timeout_capture(exc)),
            )
        except OSError:
            return UnittestRun(ran=None, ok=None, transcript_tail=_tail_text(text))
        text = completed.stdout + completed.stderr

    parsed = parse_cargo_run(text)
    return replace(parsed, transcript_tail=_tail_text(text))


def _cargo_freshness_stamp() -> float:
    """The mtime to stamp on a CARGO-mutated source file (from a later review
    round, findings 1 and 2).

    **THE BUG THIS REPLACES.** The mutated file used to be stamped
    `original_stat.st_mtime + MTIME_SKEW_SECONDS`, where `original_stat` was
    read ONCE at the top of `probe()`. Cargo's freshness check is not "this
    source differs from before", it is "this source is NEWER than the
    artifact already on disk": an ordinary checked-out `.rs` file is
    routinely older than `MTIME_SKEW_SECONDS` (10s) all by itself, and even a
    freshly-written one loses the race once a build cycle (the fast path's
    own narrow attempt, the full baseline, or both) has elapsed between the
    stat and this write. Either way the mutated source ends up OLDER than
    the artifact cargo just built from the UNMUTATED source, cargo serves
    that artifact unchanged, and the honest binary runs against the mutated
    text: a false SURVIVED, the one verdict `probe-honesty` exists to make
    impossible. Reproduced live: a source aged 8s already loses this race
    across the fast path's extra build cycle; a source aged 3600s (an
    ordinary checked-out file) loses it even with no fast path involved.

    **THE FIX.** Stamp forward from `time.time()`, read FRESH right here
    rather than inherited from a stat taken earlier, so the margin is
    measured from "now" and can never be eaten by a build that already
    finished by the time this call happens (sequential by construction: the
    baseline that produced any existing artifact always completes before
    this write does). Every artifact currently on disk finished building
    strictly before this call runs, so the wall-clock term alone dominates.

    A round 3 repair DELETED the `max(time.time(), newest)` insurance term
    this function briefly carried, plus the `_cargo_target_dir` helper that
    fed it (step 9 round 2 of that same review): removing the term reddened
    nothing (SURVIVED, 129/129 green), and the sampled value was a profile
    DIRECTORY mtime, which a rebuild does not move, so the term was both
    undefended and inoperative for the network-mount case its docstring
    claimed. `probe-honesty` (CLAUDE.md) requires exactly this deletion: a
    branch that survives every mutation is removed, never kept as an
    undefended guard. The `manifest` parameter left with the term: nothing
    remaining here reads it, and a future defended insurance term brings it
    back alongside the test that reddens when the term is removed.

    **WHY THE RESTORE STEP DOES NOT NEED THIS.** `_mutate_run_restore`'s
    `finally` block already stamps the RESTORED (honest-content) file with
    `os.utime(file, None)`, which is `time.time()` read fresh at that later
    point, after the mutant's own build finished; it was never anchored to
    `original_stat` and so never had this defect. Restoring the ORIGINAL
    mtime instead (mirroring the Python path, for byte-for-byte cosmetic
    fidelity) would put the honest source BEHIND the mutant-compiled
    artifact that build just produced, and the next `cargo test` could then
    serve that artifact against the honest text: the same false-result shape
    this whole function exists to close, just relocated to the restore.
    Digest fidelity is a promise about CONTENT; cargo's own staleness model
    makes "restore the original mtime too" unsound for this backend, which is
    why only the byte comparison, never the mtime, is asserted at restore.
    """
    return time.time() + MTIME_SKEW_SECONDS


def is_cargo_suite(suite: Path) -> bool:
    """Whether `suite` names a Cargo manifest rather than a Python module.

    The SUITE picks the backend, not the mutated file's extension. A `.rs` guard
    is always proven by a cargo run, but the reverse is not true: a Rust test can
    be defeated by an edit to a `.toml` or a fixture, and refusing that on an
    extension check would be a rule about filenames rather than about what runs.
    """
    return suite.name == CARGO_MANIFEST


def run_suite(
    suite: Path,
    *,
    timeout: int = SUITE_TIMEOUT_SECONDS,
    tests: tuple[str, ...] | None = None,
) -> UnittestRun:
    """Execute `suite` with whichever backend it names, and read what it did.

    **THE DISPATCH LIVES HERE RATHER THAN IN `probe()`, and the first spelling
    put it nowhere at all.** `is_cargo_suite` and `run_cargo_suite` were written
    and `probe()` went on calling the Python runner unconditionally, so a Cargo
    manifest was handed to `sys.executable` and every Rust probe came back
    `NO RUN`. Caught by the fixture crate on its first execution, which is the
    argument for the fixture. Both call sites in `probe()` (baseline and mutant)
    go through this one function, so they cannot disagree about the backend.

    `tests`, when given, restricts the run to those NAMED test
    id(s) instead of the whole suite; `None` (the default) is the unrestricted
    run this function has always performed.

    **Refuses a `None` or non-positive `timeout` OUTRIGHT, by
    RAISING, rather than reaching `subprocess.run(timeout=...)` with it.**
    Every production caller resolves a fallback first
    (`resolved_baseline_timeout = timeout if baseline_timeout is None else
    baseline_timeout` in `probe()`, and the narrow twin in `_fast_path`), so
    the only way `None` reaches here in the shipped tree is one of those two
    fallback lines being disabled by a mutation -- which is exactly what a
    round 6 review found: mutating either fallback to drop its `None` half
    left `WiringTests` green (it passes both clocks explicitly, exercising
    only the `else` branch) while `InconclusiveTimeoutTests` HUNG past its
    own 240s bound, because `subprocess.run(timeout=None)` waits forever
    rather than bounding anything. `bounded-waits` (`CLAUDE.md`) binds every
    wait under `scripts/`; a `ValueError` here is a red test in seconds, in
    place of a hang nothing catches.
    """
    if timeout is None or timeout <= 0:
        raise ValueError(
            f"run_suite: timeout must be a positive number of seconds, got "
            f"{timeout!r}. Passing this through to subprocess.run(timeout=...) "
            f"would wait unboundedly instead of bounding the run."
        )
    if is_cargo_suite(suite):
        return run_cargo_suite(suite, timeout=timeout, tests=tests)
    return run_python_suite(suite, timeout=timeout, tests=tests)


def run_python_suite(
    suite: Path,
    *,
    timeout: int = SUITE_TIMEOUT_SECONDS,
    tests: tuple[str, ...] | None = None,
) -> UnittestRun:
    """Execute one suite the way `verify.py`'s `loop-tools` gate does.

    Same cwd and same invocation shape deliberately: a probe that ran the suite
    differently from the gate would be answering a question about a
    configuration nobody ships. A timeout is reported as "no transcript"
    (`ran=None`) rather than as a failure, because a suite that was killed
    proves nothing about the mutation.

    **THE DIRECT CHILD GETS A PRIVATE, EMPTY BYTECODE CACHE, and this is not a
    tidiness measure.** It is deliberately stated as the direct child and not
    as "every run": an earlier version of this paragraph claimed the broader
    thing, and a step 9 review measured that a grandchild spawned with an
    explicit `env=` inherits none of it. The invariant that actually holds for
    readers at any depth is the mtime skew in `probe`; this env var is the
    cheap half that also keeps the repository's real `scripts/__pycache__`
    clean. CPython invalidates a `.pyc` on the source's mtime AND
    SIZE, and the most useful mutations this tool applies are size-preserving:
    `LIMIT = 5` to `LIMIT = 0`, `>` to `>=`, `9` to `99`. Written inside the
    same mtime tick as the baseline's own import, such a mutation leaves both
    invalidation inputs unchanged, so the mutated run imports the BASELINE's
    cached bytecode and reports every test passing over a mutation that never
    reached the interpreter. That is a SURVIVED verdict on a run that did not
    test what it claimed, which is the precise class this whole file exists to
    refuse, and it reproduced on the first execution of this module's own
    suite: `test_a_defended_guard_is_KILLED` and
    `test_a_shrinking_population_is_NO_RUN` both failed for exactly this reason
    before `PYTHONPYCACHEPREFIX` was set.

    A fresh prefix per invocation guarantees a cache MISS, so the source is
    always recompiled, and it redirects the lookup away from the repository's
    real `scripts/__pycache__` rather than deleting anything a developer owns.

    **`tests` is appended as trailing positional arguments,**
    which is the ordinary way to restrict a `unittest.main()`-driven script:
    called with no `argv`, it parses `sys.argv`, and each fixture suite here
    ends `if __name__ == "__main__": unittest.main()`, so a dotted id like
    `GuardTests.test_x` resolves against the running script exactly the way it
    would against any other `__main__` module. `None` (the default) is the
    unrestricted invocation this function has always made.
    """
    with tempfile.TemporaryDirectory(prefix="mutation-probe-pyc-") as cache:
        env = dict(os.environ)
        env["PYTHONPYCACHEPREFIX"] = cache
        args: tuple[str, ...] = (sys.executable, suite.name, *tests) if tests else (
            sys.executable,
            suite.name,
        )
        # `time.monotonic()` read fresh around THIS call, never
        # derived from anything computed earlier, so the elapsed figure
        # reflects what THIS subprocess actually waited rather than
        # accumulated overhead from a caller that already ran a fast-path
        # attempt first.
        started = time.monotonic()
        try:
            completed = subprocess.run(
                args,
                cwd=suite.parent,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return UnittestRun(
                ran=None, ok=None, timed_out=True, elapsed_seconds=time.monotonic() - started
            )
    return parse_unittest_run(completed.stdout + completed.stderr)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _inert_spans(text: str) -> list[tuple[int, int]]:
    """Absolute character spans of PROSE: comments, and string literals used as
    statements (docstrings and floating strings).

    **A STRING VALUE IS NOT PROSE, and the first version got this exactly
    backwards.** It marked every STRING token inert, which meant a guard whose
    value IS a string, and most of this repo's checks are built from those,
    could no longer be probed by its own value. A step 9 review measured the
    breadth: 63 of 109 module-level string constants under `scripts/` became
    unprobeable, including this module's own `_LOADER_MARKER`, whose history
    says in as many words that it must stay probeable. Worse than the lost
    capability, the refusal asserted something false: a marker string, a regex
    pattern or a message literal is compared against at runtime, and mutating
    it changes behavior exactly the way mutating an integer does.

    So the question is not "is this token a string" but "is this string being
    used as prose". A string literal that stands alone as a STATEMENT is prose;
    one that appears in an assignment, a call or a comparison is a value. `ast`
    answers that and `tokenize` cannot, which is why both are used here.

    Returns `[]` when the source will not parse, which is honest rather than
    convenient: an unparsable file gets no discrimination and the caller treats
    every occurrence as code, erring toward running the probe.
    """
    line_starts = [0]
    for line in text.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))

    def offset(row: int, col: int) -> int:
        # rows are 1-based; a row past the end means a truncated file.
        if row - 1 >= len(line_starts):
            return len(text)
        return line_starts[row - 1] + col

    spans: list[tuple[int, int]] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                spans.append((offset(*token.start), offset(*token.end)))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []

    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    for node in ast.walk(tree):
        # A string literal standing alone as a statement: a module, class or
        # function docstring, or a "floating" string used as commentary. This
        # covers the real trap (this module's own docstring quotes its usage,
        # so `MIN_ROWS = 5` appears in it as prose) and nothing else.
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and node.end_lineno is not None
            and node.end_col_offset is not None
        ):
            spans.append(
                (
                    offset(node.lineno, node.col_offset),
                    offset(node.end_lineno, node.end_col_offset),
                )
            )
    return spans


def code_occurrences(text: str, old: str) -> tuple[int, int]:
    """`(total, in_code)` occurrences of `old`.

    **Why this exists.** A mutation that lands only inside a comment or a
    docstring changes nothing the interpreter executes, so the suite passes and
    the probe prints `SURVIVED ... No test defends this behavior` about a run
    in which nothing was tested. That is this tool's own failure mode occurring
    inside this tool, and it is a live trap rather than a theoretical one: this
    module's docstring quotes its own usage verbatim, so `MIN_ROWS = 5` appears
    in it as prose. A step 9 review reproduced exactly that.

    `in_code == 0` with `total > 0` is the refusal condition. When the source
    does not tokenize, `_inert_spans` returns `[]` and every occurrence counts
    as code, which errs toward running the probe rather than toward refusing a
    legitimate one.
    """
    spans = _inert_spans(text)
    total = 0
    in_code = 0
    start = text.find(old)
    while start != -1:
        total += 1
        end = start + len(old)
        if not any(lo <= start and end <= hi for lo, hi in spans):
            in_code += 1
        start = text.find(old, start + 1)
    return total, in_code


def _nth_code_span(text: str, old: str, nth: int) -> tuple[int, int] | None:
    """The `(start, end)` span of the `nth` (1-based) IN-CODE occurrence of
    `old` in `text`, or `None` when fewer than `nth` in-code occurrences exist.

    Shares `_inert_spans` with `code_occurrences` so the count
    that GATES the ambiguity refusal below and the span `--nth` SELECTS can
    never disagree about which occurrences are code and which are prose: a
    prose occurrence is never counted here, exactly as it is never counted
    by `code_occurrences`'s `in_code`.
    """
    spans = _inert_spans(text)
    seen = 0
    start = text.find(old)
    while start != -1:
        end = start + len(old)
        if not any(lo <= start and end <= hi for lo, hi in spans):
            seen += 1
            if seen == nth:
                return start, end
        start = text.find(old, start + 1)
    return None


# The literal shapes this heuristic refuses in `--new`, named
# directly from a recorded incident: a builder once probed a guard with
# `if True:`, the exact ENABLING inverse of `trace-the-known-bad`'s
# prescribed `if False:` DISABLING idiom, and the widened branch's own
# hollow ACCEPTED verdict stayed invisible through review.
#
# NECESSARILY HEURISTIC, and deliberately narrow rather than clever: this is
# a regex over the LITERAL, fully-stripped text of `new`, never semantic
# analysis of what `old` did or whether the two are related. It recognizes
# only the named tautological literal after `if`, for both spellings this
# module already speaks (Python's colon, Rust's brace), and nothing wider.
# A `--new` that widens capability through `>` becoming `>=`, `and`
# becoming `or`, or a raised constant is NOT caught here and is not
# claimed to be; the refusal message says so rather than implying a
# completeness this check does not have.
_WIDENING_RE = re.compile(
    r"if\s*\(?\s*(?:True|1|not\s+False)\s*\)?\s*:\s*(?:#.*)?"  # Python
    r"|if\s*\(?\s*true\s*\)?\s*\{\s*"  # Rust
)


def capability_widening_match(new: str) -> str | None:
    """The literal WIDENING text `new` (stripped) matches, or `None`.

    PURE: handed a string, touches nothing, so per `CLAUDE.md`'s known-bad
    rule its known-bad is a crafted INPUT (see `test_mutation_probe.py`).
    Matches only the FULL trimmed text of `new`, never a substring: this
    module's own `Usage` docstring shows `--new` as one standalone
    statement, and matching a substring would refuse a `new` that merely
    MENTIONS `if True:` inside a longer, unrelated, legitimate replacement.
    """
    stripped = new.strip()
    return stripped if _WIDENING_RE.fullmatch(stripped) else None


def _apply(
    original: bytes, old: str, new: str, *, nth: int | None = None
) -> tuple[bytes | None, int, str]:
    """`(mutated_bytes, occurrences, note)`. None when the edit cannot apply.

    Counted rather than blindly replaced. Zero occurrences means the probe was
    aimed at text that is not there, which is the silent way a mutation proof
    becomes a proof of nothing: the file is untouched, the suite stays green,
    and the transcript reads exactly like a survival.

    **MORE THAN ONE in-code occurrence refuses too, unless `nth`
    (1-based, counting IN-CODE occurrences only) names exactly one.** A
    plain `text.replace` mutates every in-code occurrence of `old` at once,
    which is not the single, nameable substitution a `defended_by` entry
    claims: a recorded review round anchored a non-unique literal and its
    call site shipped SURVIVED undisclosed, because the reader had no way to tell that
    "the" occurrence was actually several.

    **`nth is None` and `in_code == 1` rewrites ONLY that one code span, via
    `_nth_code_span(text, old, 1)`, never a global `text.replace`.** A step 9
    review measured that the first version of this item still called
    `text.replace` for this exact branch, which mutates every TEXTUAL
    occurrence of `old`, code or prose, the moment `old` also happens to
    appear in a docstring or a comment: this module's own docstring quoting
    its own marker is precisely that shape, and a test asserting on the
    prose copy then reddens, so the probe reports KILLED for a guard no test
    actually defends. `code_occurrences`/`_nth_code_span` already agree on
    which occurrence is "the" code one; rewriting through the same span they
    name is what makes "one occurrence" and "the occurrence actually
    mutated" the same claim.
    """
    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A fifth outcome the contract does not name is not an outcome, it is a
        # traceback. Named as a refusal instead, which is what the docstring
        # promises: NO RUN or REFUSED, never a crash.
        return None, 0, f"the file is not valid UTF-8 and cannot be mutated as text: {exc}"

    if nth is not None and nth < 1:
        return None, 0, f"--nth must be a positive 1-based index, got {nth}"

    occurrences, in_code = code_occurrences(text, old)
    if occurrences == 0:
        return None, 0, f"the target text is absent from the file: {old!r}"
    if in_code == 0:
        return None, occurrences, (
            f"all {occurrences} occurrence(s) of {old!r} lie inside comments or string "
            f"literals, so the mutation changes nothing the interpreter executes. A "
            f"suite passing over that is not evidence the behavior is undefended."
        )

    if nth is None:
        if in_code > 1:
            return None, occurrences, (
                f"{in_code} of {occurrences} occurrence(s) of {old!r} are in code, so a "
                f"plain replace would mutate all {in_code} of them at once, which is not "
                f"the single, nameable substitution a defended_by entry claims. Point "
                f"--nth <k> (1-based, counting IN-CODE occurrences only, in document "
                f"order) at exactly one, or lengthen --old (more surrounding text, e.g. "
                f"indentation) until it is unique."
            )
        # `in_code == 1` here, guaranteed by the two returns above. Rewrite
        # ONLY that one code span via `_nth_code_span`, never a global
        # `text.replace`: a plain replace mutates every TEXTUAL occurrence of
        # `old`, so a PROSE copy sitting in a docstring or comment alongside
        # the one code occurrence gets mutated too, and a test asserting on
        # that prose then reddens for a guard nothing actually defends.
        span = _nth_code_span(text, old, 1)
    else:
        span = _nth_code_span(text, old, nth)
        if span is None:
            return None, occurrences, (
                f"--nth {nth} does not name an in-code occurrence of {old!r}: only "
                f"{in_code} in-code occurrence(s) exist (1-based)."
            )

    assert span is not None, (
        f"in_code == {in_code} for {old!r} but _nth_code_span found no matching span; "
        f"code_occurrences and _nth_code_span disagree"
    )
    start, end = span
    mutated = (text[:start] + new + text[end:]).encode("utf-8")

    if mutated == original:
        return None, occurrences, (
            f"the substitution is a no-op: {old!r} and {new!r} produce identical bytes"
        )
    # The note carries the code/prose split on the SUCCESS path too. Reporting
    # only the total left a reader unable to tell that two of three occurrences
    # were commentary, which is the ambiguity the classifier was added to
    # remove and which the first version removed only from the refusal path.
    detail = "" if in_code == occurrences else f"{in_code} of them in code, the rest prose"
    return mutated, occurrences, detail


@dataclass(frozen=True)
class ProbeResult:
    verdict: str
    lines: list[str]

    @property
    def exit_code(self) -> int:
        return 0 if self.verdict == KILLED else 1


# WORKTREE-LOCAL, not one directory per machine and not one directory per
# TOOL INSTALL either. The journal protects files in the TARGET repo, so it
# belongs beside them, derived from `REPO_ROOT` (the caller's cwd) rather
# than from where this module itself lives: a single shared install (this
# tool run from one place against many different repos) must not funnel
# every repo's abandoned mutants into one directory, for the same reason the
# system temp folder was wrong. Under the system temp folder it was a single
# directory shared by every worktree on the host, and with several agents
# probing in parallel that is not a corner case: multiple agents hit it
# within one evening, and a probe of one repo recovered a mutant sitting in a
# different agent's worktree, which is a cross-process write nobody asked
# for. This constant is a default only: the env var below or an explicit
# `journal_dir` argument both override it per invocation.
JOURNAL_DIR = REPO_ROOT / ".mutation-probe-journal"

# The escape hatch a monkeypatch cannot provide. `CommandLineTests` spawns the
# CLI as a real subprocess, a fresh interpreter where an in-process patch of
# `JOURNAL_DIR` never happened, so the child read the shared directory and
# recovered a foreign worktree's abandoned probe mid-suite. An env var is the
# only channel that crosses that boundary.
JOURNAL_ENV_VAR = "MUTATION_PROBE_JOURNAL"


def _resolve_journal(journal_dir: Path | None) -> Path:
    """`JOURNAL_DIR` read at CALL time, never bound as a default argument.

    A default is evaluated once, when the module is imported, so
    `journal_dir: Path = JOURNAL_DIR` cannot be redirected by patching the
    global and every caller that omitted the argument reached the one real
    directory in the system temp folder. That is shared mutable state between
    this tool and its own test suite, and it cost a `verify: FAIL`: killing a
    verify mid-run killed a probe inside `test_mutation_probe.py`, which left a
    journal entry naming that test's temp fixture, which the NEXT full run's
    suite then recovered, printing a REFUSED line into eight unrelated tests.

    The suite's isolation now works by pointing `JOURNAL_DIR` at a scratch
    directory, which only works if the read happens here.

    Precedence: an explicit argument, then `MUTATION_PROBE_JOURNAL`, then the
    worktree-local default. The env var sits in the middle because it is how a
    parent isolates a CHILD process it cannot patch, and an explicit argument
    from a caller that knows what it wants must still win over an inherited one.
    """
    if journal_dir is not None:
        return journal_dir
    from_env = os.environ.get(JOURNAL_ENV_VAR)
    return Path(from_env) if from_env else JOURNAL_DIR


def _journal_paths(file: Path, *, journal_dir: Path | None = None) -> tuple[Path, Path]:
    root = _resolve_journal(journal_dir)
    key = hashlib.sha256(str(file.resolve()).encode("utf-8")).hexdigest()[:16]
    return root / f"{key}.path", root / f"{key}.bin"


def recover_abandoned(*, journal_dir: Path | None = None) -> list[str]:
    """Restore any file a KILLED probe left holding its mutant, and say so.

    The `finally` block cannot run when the process is SIGKILLed or hits a
    harness timeout, so the byte-exact restore has a hole that no amount of
    care inside `probe` can close. It was called undefendable by construction,
    and that is true of the `finally` and false of the TOOL: a journal written
    BEFORE the mutation lets the next run repair what the last one abandoned.

    Twice in one session a probe of this repo was killed mid-suite and left its
    mutation in a tracked source file. The first was found by reading the
    function; the second by grepping for it on suspicion. Neither was found by
    anything that would have caught it on its own, and a mutated source file
    silently in the tree is the worst outcome this tool has, because everything
    downstream is then measured against code nobody wrote.

    Returns one line per file recovered, empty when there was nothing to do.
    """
    root = _resolve_journal(journal_dir)
    if not root.is_dir():
        return []
    lines: list[str] = []
    for marker in sorted(root.glob("*.path")):
        backup = marker.with_suffix(".bin")
        try:
            target = Path(marker.read_text(encoding="utf-8").strip())
            # A target whose DIRECTORY is gone cannot be holding a mutant, and
            # trying to recreate it would resurrect a file into a tree that no
            # longer has a place for it. This is the ordinary case for a probe
            # over a temporary fixture, so it is silent rather than an alarm.
            if not target.parent.is_dir():
                marker.unlink(missing_ok=True)
                backup.unlink(missing_ok=True)
                continue
            if not backup.is_file():
                lines.append(
                    f"[{REFUSED}] an abandoned probe journal names {target} but its backup "
                    f"is missing, so this cannot repair it. Run `git status` and "
                    f"`git checkout -- {target}` before trusting the tree."
                )
                marker.unlink(missing_ok=True)
                continue
            saved = backup.read_bytes()
            current = target.read_bytes() if target.is_file() else b""
            if current != saved:
                target.write_bytes(saved)
                lines.append(
                    f"[{REFUSED}] RECOVERED {target}: a previous probe was killed before it "
                    f"could restore, and the file on disk held its MUTANT. Restored from the "
                    f"journal ({len(saved)} byte(s), {_digest(saved)}). Re-run whatever you "
                    f"measured since, and treat any verdict from that run as void."
                )
        except OSError as exc:
            lines.append(f"[{REFUSED}] could not process the abandoned journal {marker}: {exc}")
            continue
        marker.unlink(missing_ok=True)
        backup.unlink(missing_ok=True)
    return lines


# --- tree identity --------------------------------------------------------
#
# The step 9 acceptance clause for an expensive probe transcript names a sha
# and the clean/dirty tree state it ran at, next to the transcript, but
# nothing here printed either: the sha beside an accepted transcript was
# always author-typed, and a wrong one read exactly like a measured one. This
# section makes the TOOL the source of that claim instead of a reviewer's
# annotation.

GIT_TREE_TIMEOUT_SECONDS = 15
# Bounded per CLAUDE.md's every-wait-is-bounded rule. `rev-parse` and
# `status --porcelain` against a local repo answer in well under a second;
# 15s is headroom for a network mount or an antivirus scanner mid-scan, not a
# number chosen to make the identity line itself the reason a caller waits.

_TREE_LINE_RE = re.compile(
    r"^tree: (?:[0-9a-f]{7,40} (?:clean|dirty)|UNDETERMINABLE .+)$", re.MULTILINE
)


def _run_git(
    args: tuple[str, ...], *, cwd: Path, timeout: int = GIT_TREE_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    """One bounded git subprocess, with an explicit identity, never inherited.

    `GIT_AUTHOR_*`/`GIT_COMMITTER_*` and `GIT_TERMINAL_PROMPT=0` are set on
    the two READ-ONLY calls this makes (`rev-parse`, `status`) exactly as a
    write would need them, because CLAUDE.md's bounded-waits rule binds
    every git subprocess under `scripts/`, not only the ones that commit: an
    identity read from a developer's `.git/config` is present on a laptop
    and absent on a runner, which is the asymmetry that rule exists to close.
    """
    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = "mutation-probe"
    env["GIT_AUTHOR_EMAIL"] = "mutation-probe@mutation-probe.invalid"
    env["GIT_COMMITTER_NAME"] = "mutation-probe"
    env["GIT_COMMITTER_EMAIL"] = "mutation-probe@mutation-probe.invalid"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ("git", *args),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )


def tree_identity_line(file: Path) -> str:
    """`tree: <sha> <clean|dirty>`, or `tree: UNDETERMINABLE <reason>`.

    **Derived from the PROBED FILE's own repo, never the cwd.** `--file` can
    point into any worktree: a fixture crate, an isolated scratch copy, a
    sibling checkout. `git -C <file's directory>` answers "what tree is THIS
    file part of" regardless of where the process happened to be launched
    from, which is the question the runbook's acceptance clause is actually
    asking. `git status --porcelain` reports the state of the WHOLE working
    tree even when run from a subdirectory, with no pathspec narrowing it, so
    "clean"/"dirty" here describes the tree, never the one folder `file`
    happens to sit in.

    **Never blocks a verdict, never silently omits (`probe-honesty`).** A git
    failure (no git on PATH, the directory is not inside a repo, a fresh
    worktree with no commit yet, a timeout) produces `UNDETERMINABLE <reason>`
    rather than raising or returning nothing, so `probe()` can call this
    unconditionally and every transcript carries exactly one `tree:` line: a
    reader sees WHY the identity is missing rather than a gap that reads as
    an oversight. `-uno` matches what "dirty" means here: an untracked
    scratch file (a stray `__pycache__`, a probe's own journal) does not turn
    an otherwise-clean tree dirty.
    """
    directory = file.parent
    if not directory.is_dir():
        return f"tree: UNDETERMINABLE {directory} is not a directory"
    if shutil.which("git") is None:
        return "tree: UNDETERMINABLE git is not on PATH"
    try:
        rev = _run_git(("rev-parse", "HEAD"), cwd=directory)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"tree: UNDETERMINABLE git rev-parse HEAD: {type(exc).__name__}: {exc}"
    if rev.returncode != 0:
        detail = (rev.stderr or rev.stdout or "").strip().splitlines()
        return (
            f"tree: UNDETERMINABLE git rev-parse HEAD exited {rev.returncode}: "
            f"{detail[0] if detail else '(no output)'}"
        )
    sha = rev.stdout.strip()
    if not sha:
        return "tree: UNDETERMINABLE git rev-parse HEAD printed no sha"
    try:
        status = _run_git(("status", "--porcelain", "-uno"), cwd=directory)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"tree: UNDETERMINABLE git status --porcelain -uno: {type(exc).__name__}: {exc}"
    if status.returncode != 0:
        detail = (status.stderr or status.stdout or "").strip().splitlines()
        return (
            f"tree: UNDETERMINABLE git status --porcelain -uno exited {status.returncode}: "
            f"{detail[0] if detail else '(no output)'}"
        )
    state = "dirty" if status.stdout.strip() else "clean"
    return f"tree: {sha} {state}"


def transcript_carries_tree_line(text: str) -> bool:
    """Whether `text` carries this module's `tree: <sha|UNDETERMINABLE> ...` line.

    The runbook's step 9 acceptance clause names THIS function by name rather
    than restating the pattern in prose, so the check a reviewer runs by hand
    and the shape this module actually emits cannot drift apart the way the
    hand-typed sha-and-tree-state ANNOTATION this replaces always could. An
    `UNDETERMINABLE` line passes too: `probe-honesty` treats a stated refusal
    as the honest outcome here, not as a gap to reject alongside a transcript
    that truly carries no line at all.
    """
    return bool(_TREE_LINE_RE.search(text))


# --- the fast path ---------------------------------------------------------
#
# A review's `defended_by` names one to four EXPECTED reddening tests out of a
# suite of over a hundred, and until this existed every probe ran the WHOLE
# suite twice (baseline, mutant) regardless: measured, three step 9 reviews in
# one wave cost 17, 18 and 28 minutes wall-clock, almost entirely suite
# execution. `_fast_path` tries the named tests first; when they redden, the
# full suite is never touched at all.


def _site_descriptor(original: bytes, old: str, nth: int) -> str:
    """`'occurrence {nth} of {n} in code, line {L}'` for an explicit `--nth`,
    or `''` when the site cannot be resolved.

    A later repair, step 9 finding L2: the `mutation applied:` line named
    only `old -> new` plus digests, so a reader of a `defended_by` entry
    could not tell a `--nth 1` run from a `--nth 2` run without re-running
    the tool or diffing digests by hand. Re-decodes `original` and re-derives
    the span through `_nth_code_span` rather than threading the already-
    decoded text through every caller: this is a cheap, pure computation off
    bytes `_mutate_run_restore` already holds, kept out of the mutate/run/
    restore path itself so a failure here can never affect whether the
    mutation applies. `''` (never raising) on any failure to resolve, since
    this is disclosure ON TOP OF a mutation `_apply` already accepted for
    this same `(original, old, nth)`, not a second gate on it.
    """
    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    _, in_code = code_occurrences(text, old)
    span = _nth_code_span(text, old, nth)
    if span is None:
        return ""
    line = text.count("\n", 0, span[0]) + 1
    return f"occurrence {nth} of {in_code} in code, line {line}"


def _mutate_run_restore(
    *,
    file: Path,
    old: str,
    new: str,
    mutated: bytes,
    original: bytes,
    before_digest: str,
    original_stat: os.stat_result,
    cargo: bool,
    suite: Path,
    timeout: int,
    journal_dir: Path | None,
    tests: tuple[str, ...] = (),
    nth: int | None = None,
) -> tuple[UnittestRun | None, list[str], str | None]:
    """Write `mutated` to `file`, run `suite` (restricted to `tests` when
    non-empty), and restore `file` byte-exactly whatever happens next.

    Returns `(mutant_run, lines, abandoned)`. `abandoned` is `REFUSED` when
    the write, the mtime skew, or the restore itself failed; `mutant_run` is
    then `None` and the caller MUST return `ProbeResult(abandoned, ...)`
    without inspecting `mutant_run`. Otherwise `abandoned` is `None` and
    `mutant_run` is a real transcript.

    **`nth` (a later repair, step 9 finding L2) is the caller's ORIGINAL
    `--nth`, never the internal `1` `_apply` substitutes when `nth is None`
    and only one in-code occurrence exists.** When not `None`, the
    `mutation applied:` line below names WHICH site it selected (occurrence
    and line number): before this, a reader of a `defended_by` entry could
    not tell a `--nth 1` run from a `--nth 2` run without re-running the
    tool or diffing digests by hand, which matters exactly when `--nth` is
    the prescribed escape hatch for a non-unique pin.

    **THE ONLY PLACE THAT WRITES A MUTATION TO A TRACKED FILE.** `probe()`'s
    ordinary full run and `_fast_path`'s narrow `--tests` attempt both call
    this, so the crash-safety machinery (the journal written before the
    write, the mtime skew, the verified write, the retried restore, the
    verified restore) exists in exactly one place rather than twice with two
    chances to drift. This module's own docstring names the alternative:
    "hand-rolled twice in one evening is the argument for a file". Hand-
    rolling it a third time, for the fast path, would be the same mistake
    with a new name.
    """
    out: list[str] = []
    # Set when the run is abandoned before the mutant executes. NOT an early
    # `return` from inside the `try`: the restore evidence is produced by the
    # `finally` below, and a result returned before it ran would omit the one
    # line that says whether the tree is intact. The first spelling did exactly
    # that, and the omission is invisible precisely when it matters most.
    abandoned: str | None = None
    mutant: UnittestRun | None = None
    # THE JOURNAL, written BEFORE the mutation and only then. Everything else in
    # this function protects against the process CONTINUING and getting it
    # wrong; this protects against the process STOPPING. A kill or a harness
    # timeout skips the `finally` entirely, and twice in one session that left a
    # mutation sitting in a tracked source file of this repo.
    marker, backup = _journal_paths(file, journal_dir=journal_dir)
    journalled = False
    try:
        _resolve_journal(journal_dir).mkdir(parents=True, exist_ok=True)
        backup.write_bytes(original)
        marker.write_text(str(file.resolve()), encoding="utf-8")
        journalled = True
    except OSError as exc:
        # Stated, not silent. A probe without a journal is the old behavior, and
        # the old behavior is what this is here to end, so the operator gets to
        # see that the net is missing rather than assume it is there.
        out.append(
            f"[WARN] could not write the recovery journal to "
            f"{_resolve_journal(journal_dir)} ({exc}). "
            f"If this run is killed mid-suite, {file} will be left holding the mutant "
            f"and nothing will repair it."
        )
    try:
        file.write_bytes(mutated)
        # Push mtime clear of the original's so EVERY reader invalidates its
        # cached bytecode, not just the child that inherited our
        # PYTHONPYCACHEPREFIX. See MTIME_SKEW_SECONDS: a grandchild spawned
        # with an explicit `env=` gets none of our environment, and that made
        # the verdict nondeterministic.
        #
        # **CARGO GETS A DIFFERENT STAMP THAN PYTHON (a later review round,
        # review findings 1 and 2), and sharing one stamp between both was
        # the defect.** CPython invalidates a `.pyc` on an EXACT mtime
        # mismatch, so any value that DIFFERS from the original works
        # regardless of when it is computed, which is why the Python branch
        # below is untouched: `original_stat.st_mtime + MTIME_SKEW_SECONDS`
        # still differs from `original_stat.st_mtime` no matter how much wall
        # clock has passed. Cargo's freshness check asks a different
        # question, "is this source NEWER than the artifact already on
        # disk", and `original_stat` was a stat taken once at the very top
        # of `probe()`, before any build this run performs. See
        # `_cargo_freshness_stamp`'s own docstring for the full mechanism and
        # the reproduced failure it replaces.
        #
        # Wrapped, because `os.utime` fails with EPERM on a read-only file, on
        # a file owned by another user, and on some FUSE and network mounts. An
        # uncaught OSError here escapes as a traceback: no verdict, and no
        # `restore:` line, which is the one line this repo's convention exists
        # to force. That is the "unnamed fifth outcome" defect reintroduced by
        # the fix for the previous one, and a step 9 review caught it.
        try:
            stamp = (
                _cargo_freshness_stamp()
                if cargo
                else original_stat.st_mtime + MTIME_SKEW_SECONDS
            )
            os.utime(file, (original_stat.st_atime, stamp))
        except OSError as exc:
            out.append(
                f"[{REFUSED}] could not skew the mutated file's mtime ({exc}), so a "
                f"cached .pyc or build artifact could mask the mutation and any "
                f"verdict would be unsound. Refusing rather than guessing."
            )
            abandoned = REFUSED
        # ASSERT the edit APPLIED rather than assuming write_bytes did what it
        # was told. This is cheap and it is the half that the two hand-rolled
        # proofs both skipped.
        readback = b"" if abandoned else file.read_bytes()
        if abandoned:
            pass
        elif readback != mutated:
            out.append(
                f"[{REFUSED}] the mutation did not apply: the file on disk differs "
                f"from what was written. Digest wanted {_digest(mutated)}, "
                f"got {_digest(readback)}."
            )
            abandoned = REFUSED
        else:
            descriptor = _site_descriptor(original, old, nth) if nth is not None else ""
            site = f" ({descriptor})" if descriptor else ""
            out.append(
                f"mutation applied: {old!r} -> {new!r} "
                f"({before_digest} -> {_digest(mutated)}){site}"
            )
            mutant = run_suite(suite, timeout=timeout, tests=tests or None)
    finally:
        # THE RESTORE WRITE IS THE ONE THING HERE THAT MUST NOT RAISE, and it
        # was the only unguarded statement in this block. The comment below
        # already says a failure "MUST NOT SWALLOW THE RESTORE EVIDENCE", and
        # said it about `os.utime` while the write four lines above it went
        # bare. If this raised, the tracked source file kept the MUTANT, the
        # `restore:` line was never appended, the loud alarm never fired, and
        # the caller got a traceback where a verdict belongs. `write_bytes`
        # truncates before writing, so a partial failure leaves the file
        # truncated rather than merely wrong. The host this tool is mandated on
        # runs near-full on disk, so ENOSPC is the live case, not a thought
        # experiment. Retried once, because a transient lock (an editor, an
        # indexer, a virus scanner on Windows) is the common cause and costs
        # nothing to survive.
        restore_failure: str | None = None
        for attempt in (1, 2):
            try:
                file.write_bytes(original)
                restore_failure = None
                break
            except OSError as exc:
                restore_failure = f"attempt {attempt}: {type(exc).__name__}: {exc}"
        if restore_failure:
            out.append(
                f"[{REFUSED}] THE RESTORE WRITE FAILED ({restore_failure}). "
                f"{file} may still hold the MUTANT or be truncated. Run "
                f"`git checkout -- {file}` before doing anything else, and treat "
                f"any verdict below as void."
            )
            abandoned = REFUSED
        # Put mtime back too. Any bytecode compiled from the MUTANT recorded
        # `original + MTIME_SKEW_SECONDS`, so restoring the original stamp
        # invalidates it for every future reader, while bytecode compiled from
        # the genuine original stays valid because the content matches.
        #
        # A FAILURE HERE MUST NOT SWALLOW THE RESTORE EVIDENCE. If this raised,
        # the `restore:` line below would never be appended and the caller
        # would get a traceback instead of a verdict, which is the same defect
        # the skew's own wrapper exists to prevent. Leaving mtime at "now" is
        # safe: it still differs from the `original + skew` any mutant-compiled
        # .pyc recorded, so the cache is still invalidated.
        #
        # **THE RUST PATH RESTORES MTIME FORWARD, NOT BACK, and the inversion is
        # required rather than sloppy.** cargo's staleness model is mtime against
        # `target/.fingerprint`, so putting the stamp BACK would leave the
        # restored source looking OLDER than the artifact just compiled from the
        # MUTANT, and the next `cargo test` could serve that artifact against the
        # honest source. That is a stale-cache false result, which is the exact
        # hazard `MTIME_SKEW_SECONDS` exists to close on the Python side; the two
        # toolchains simply need opposite directions to close it. The CONTENT is
        # restored byte-exactly either way, which is what the promise is about.
        mtime_restored = True
        try:
            if cargo:
                os.utime(file, None)
            else:
                os.utime(file, (original_stat.st_atime, original_stat.st_mtime))
        except OSError:
            mtime_restored = False
        # Guarded for the same reason the write above now is: this is the
        # statement that PRODUCES the restore evidence, so it must not be the
        # statement that destroys it. An unreadable file here is itself the
        # alarm, and `b""` makes the comparison below fail loudly rather than
        # raise silently past it.
        try:
            restored = file.read_bytes()
        except OSError as exc:
            out.append(
                f"[{REFUSED}] the restored file could not be READ BACK ({exc}), so "
                f"whether {file} holds the original is unknown. Run "
                f"`git checkout -- {file}`."
            )
            restored = b""
            abandoned = REFUSED
        after_digest = _digest(restored)
        # PRINTED, not claimed. Both digests and both lengths, so a reader can
        # see the comparison rather than take the word "restored" for it.
        out.append(
            f"restore: {len(original)} byte(s) {before_digest} -> "
            f"{len(restored)} byte(s) {after_digest}; "
            f"identical={restored == original}; "
            f"mtime_{'advanced' if cargo else 'restored'}={mtime_restored}"
        )
        if restored != original:
            # Nothing below matters next to this. The tree is now wrong.
            out.append(
                f"[{REFUSED}] THE RESTORE FAILED and {file} does not match what it "
                f"held before this ran. Recover it with `git checkout -- {file}` "
                f"before doing anything else."
            )
            return None, out, REFUSED
        # ONLY once the restore is VERIFIED byte-exact. Clearing the journal any
        # earlier would discard the backup while the file might still be wrong,
        # which is the one state the journal exists to survive.
        if journalled:
            marker.unlink(missing_ok=True)
            backup.unlink(missing_ok=True)

    return mutant, out, abandoned


def _fast_path(
    *,
    file: Path,
    old: str,
    new: str,
    mutated: bytes,
    original: bytes,
    before_digest: str,
    original_stat: os.stat_result,
    cargo: bool,
    suite: Path,
    tests: tuple[str, ...],
    floor: int,
    timeout: int,
    journal_dir: Path | None,
    narrow_baseline_timeout: int | None = None,
    nth: int | None = None,
) -> tuple[str | None, list[str]]:
    """Try to confirm KILLED using ONLY `tests`. Returns `(verdict, lines)`.

    **`narrow_baseline_timeout` (from a later review round, finding 1) is a
    SEPARATE clock for the narrow baseline below, never the same one the
    mutated narrow run is scored against.** Defaults to `timeout` when
    `None`, which is exactly today's behavior for every caller that never
    passes it. The narrow baseline is the one call in this function with a
    COMPLETING side (a real CPython launch, import, and the named test(s)
    running to green); the narrow mutated run below it is the HANG side.
    Four rounds of this item's history relocated the same defect by reusing
    one number for both roles - a completing run that occasionally overran
    the bound under load, misread as a hang. This parameter lets a caller
    give the completing phase its own, more generous clock without moving
    the hang-detection clock the mutated run is scored against.

    `verdict` is `KILLED` when the named test(s) reddened cleanly, `REFUSED`
    on the same restore catastrophe `_mutate_run_restore` can raise for any
    caller, `INCONCLUSIVE` when the MUTATED narrow run itself
    HUNG past `timeout`, or `None` meaning "inconclusive [lowercase; not the
    verdict], fall back to the full paired suite" -- the caller MUST treat
    `None` as exactly that and nothing else.

    **NEVER returns SURVIVED or NO_RUN.** Those verdicts depend on the FULL
    suite's population and floor to mean anything, and a narrow run of one to
    four tests has neither. A narrow miss -- the named test(s) stayed green
    under the mutation -- says only that THESE tests do not catch THIS
    mutation; it says nothing about the rest of the suite, so it can only
    ever mean "keep looking", never "undefended". Same for an unclean narrow
    baseline (a typo'd test id, a loader error, OR A HANG): the full suite's
    OWN red-baseline, floor and INCONCLUSIVE checks below will diagnose it
    properly if it is a real problem, so this refuses to guess and falls
    back instead.

    **THE MUTATED NARROW RUN IS THE ONE EXCEPTION, and reports `INCONCLUSIVE`
    directly rather than falling back.** A hung BASELINE might be a typo'd
    test id colliding with something slow elsewhere; a hung MUTANT, run
    against the SAME named test(s) a baseline moments ago just proved clean
    and fast, has no such innocent explanation, and falling back would only
    re-run the identical hang against the full suite at ten times the cost
    for no new information.

    **`floor` is accepted but never ENFORCED here (review finding 4).** The
    only way to know whether the WIDER suite clears `--floor` or is red
    elsewhere is to run it, which is precisely the cost this path exists to
    avoid. Enforcing it would defeat the fast path; silently ignoring it was
    the defect. The middle path is disclosure: once the narrow baseline is
    clean enough to proceed, the transcript states in as many words that the
    floor and the wider suite's health were not evaluated on this attempt,
    so a reader (or a downstream validator) never mistakes a fast KILLED for a run
    that checked either.
    """
    lines: list[str] = [
        f"fast path: probing {len(tests)} named test(s) first: {', '.join(tests)}"
    ]
    resolved_narrow_baseline_timeout = (
        timeout if narrow_baseline_timeout is None else narrow_baseline_timeout
    )
    baseline_narrow = run_suite(suite, timeout=resolved_narrow_baseline_timeout, tests=tests)
    if not (
        baseline_narrow.is_transcript
        and not baseline_narrow.loader_error
        and baseline_narrow.ok
        and baseline_narrow.executed >= 1
    ):
        if baseline_narrow.timed_out:
            # NAMED explicitly rather than folded into the
            # generic "did not run cleanly" message below: a typo'd test id
            # is a different fact from a HANG, even though both fall back to
            # the full suite the same way here. The full suite's own
            # INCONCLUSIVE branch is authoritative on whether this is a real
            # problem; this narrow attempt only refuses to guess.
            lines.append(
                "fast path: the named-test baseline TIMED OUT after "
                f"{baseline_narrow.elapsed_seconds:.1f}s (bound "
                # step 9 round 2 finding L (from a recorded review report):
                # this bound is a cargo-reachable ceiling too (`run_suite`
                # dispatches a Cargo manifest straight into `run_cargo_suite`),
                # so it gets the same disclosure the two full-suite sites
                # already carry, never a bare number that reads as a hard cap.
                f"{resolved_narrow_baseline_timeout}s{_cargo_bound_note(cargo)}) rather "
                "than completing; falling back to the full suite, which applies "
                "its own INCONCLUSIVE handling if it hangs too."
            )
        else:
            lines.append(
                "fast path: the named-test baseline did not run cleanly "
                f"(ran={baseline_narrow.ran}, ok={baseline_narrow.ok}, "
                f"loader_error={baseline_narrow.loader_error}); falling back to the "
                "full suite rather than trusting a narrow run that is itself in question."
            )
        return None, lines

    # UNCONDITIONAL, the moment the narrow baseline is clean enough to act
    # on, before the mutation is even written (review finding 5: a fast
    # KILLED transcript must carry a green baseline line so the runbook's
    # step 9 acceptance clause can read it back). `baseline (fast path,
    # named only):` is deliberately NOT the bare `baseline:` prefix the full
    # suite uses a few lines below in `probe()`: the two report different
    # populations and must never be mistaken for one another by a reader
    # grepping for the word.
    lines.append(
        f"baseline (fast path, named only): {baseline_narrow.ran} test(s), OK "
        f"({baseline_narrow.executed} executed, {baseline_narrow.skipped} skipped)"
    )
    lines.append(
        f"note: --floor {floor} and the wider suite's own health are NOT "
        f"evaluated on the fast path; only the {len(tests)} named test(s) above "
        f"ran. A narrow miss falls back to the full suite, which checks both "
        f"before scoring; a fast KILLED verdict does not."
    )

    mutant_narrow, restore_lines, abandoned = _mutate_run_restore(
        file=file,
        old=old,
        new=new,
        mutated=mutated,
        original=original,
        before_digest=before_digest,
        original_stat=original_stat,
        cargo=cargo,
        suite=suite,
        timeout=timeout,
        journal_dir=journal_dir,
        tests=tests,
        nth=nth,
    )
    lines.extend(restore_lines)
    if abandoned is not None:
        return abandoned, lines
    assert mutant_narrow is not None

    if mutant_narrow.timed_out:
        # Reported HERE, directly, rather than falling back: see
        # the docstring's "THE MUTATED NARROW RUN IS THE ONE EXCEPTION"
        # paragraph for why a hung mutant does not get the baseline's
        # benefit of the doubt.
        lines.append(
            f"[{INCONCLUSIVE}] the mutated run (fast path, named tests) TIMED "
            # step 9 round 2 finding L: same disclosure, same reason as the
            # narrow baseline's bound just above.
            f"OUT after {mutant_narrow.elapsed_seconds:.1f}s (bound {timeout}s"
            f"{_cargo_bound_note(cargo)}) "
            f"rather than completing red or green. A run that never finished "
            f"is neither a kill nor a survival; refusing a verdict rather than "
            f"re-running the same hang against the full suite."
        )
        return INCONCLUSIVE, lines

    if (
        mutant_narrow.is_transcript
        and not mutant_narrow.loader_error
        and mutant_narrow.ran == baseline_narrow.ran
        and mutant_narrow.skipped <= baseline_narrow.skipped
        and not mutant_narrow.ok
        and mutant_narrow.failed
    ):
        lines.append(
            f"[{KILLED}] verdict path: fast (named tests); "
            f"{len(mutant_narrow.failed)} of {mutant_narrow.ran} test(s) "
            f"reddened: {'; '.join(mutant_narrow.failed)}"
        )
        return KILLED, lines

    lines.append(
        "fast path: the named test(s) did not redden under the mutation "
        f"(ran={mutant_narrow.ran}, ok={mutant_narrow.ok}); falling back to the "
        "full suite before any verdict, since a narrow miss is not a SURVIVED."
    )
    return None, lines


def _write_status_clause(out: list[str], file: Path) -> str:
    """The honest tail for a REFUSED/NO_RUN line that would otherwise claim
    nothing was written to `file` (review finding 4(c)).

    The FULL-SUITE baseline checks below in `probe()` can be reached AFTER
    the fast path already wrote a mutation of its own (a narrow miss that
    fell back to here): `_fast_path` mutates and byte-exactly restores
    `file` as part of proving a narrow miss is not a SURVIVED, so by the time
    the full baseline runs, something WAS written and then undone. Claiming
    "Nothing was written to `file`" in that transcript is false, and it is
    the exact line a reader consults to answer a reviewer's question (did a
    kill mid-run leave a mutant behind). Read from `out` itself, which
    already carries `_mutate_run_restore`'s own `mutation applied: ...`
    line on success, rather than threading a second boolean through every
    caller: this can never disagree with what the transcript already says,
    because it IS what the transcript already says.
    """
    if any(line.startswith("mutation applied:") for line in out):
        return (
            f"The fast path already wrote a mutation to {file} earlier in this "
            f"run and restored it byte-exactly (see the mutation/restore lines "
            f"above); nothing further was written here."
        )
    return f"Nothing was written to {file}."


def _cargo_tail_clause(cargo: bool, run: UnittestRun) -> str:
    """The captured cargo transcript tail, appended to a NO RUN or
    INCONCLUSIVE message so a reader is not left trusting a guess about the
    cause.

    `""` for the Python backend (`cargo` False): its own transcript never
    reaches this function's caller in the first place, since `UnittestRun`
    only ever carries `transcript_tail` when `run_cargo_suite` produced it.
    Also `""` whenever nothing was captured (an `OSError` before the
    subprocess even started), so an empty clause never prints a misleading
    empty header.
    """
    if not cargo or not run.transcript_tail:
        return ""
    return (
        f" cargo transcript tail (last {TRANSCRIPT_TAIL_LINES} lines):\n"
        f"{run.transcript_tail}"
    )


def _cargo_bound_note(cargo: bool) -> str:
    """The disclosure appended to a TIMED OUT message's own `(bound {N}s)`
    clause, so that clause never reads as a hard ceiling it is not.

    step 9 round 1 finding L2 (from a recorded review report):
    `run_cargo_suite`'s ONCE-only LNK1104 retry can carry a cargo run's true
    wall time to `2*timeout`, not `timeout`, whenever the FIRST attempt
    completes (rather than itself timing out) with an `LNK1104` transcript
    and the retry then also runs to (or past) the bound. `""` for the Python
    backend (`cargo` False), which has no retry to disclose.

    Reaches all FOUR `(bound {N}s)` sites, not only the two full-suite ones
    (step 9 round 2 finding L, from a recorded review report):
    `probe()`'s baseline-TIMED-OUT and mutant-TIMED-OUT messages, and
    `_fast_path`'s narrow-baseline-TIMED-OUT and narrow-mutant-TIMED-OUT
    messages. `_fast_path` is reachable under `cargo=True` exactly like the
    full suite is: `run_suite` dispatches on the SUITE's manifest, not on
    whether a caller went through the fast path, so a caller budgeting off a
    fast-path bound was wrong by the same factor of two the full-suite sites
    were until this note reached them too. `_cargo_bound_note(True)`'s own
    text is pinned inside a real TIMED OUT message on THREE of the four
    sites in `CargoBoundNoteTests` (`test_mutation_probe.py`): the
    full-suite mutant site, and both fast-path sites (narrow baseline and
    narrow mutant); its absence for the Python backend is pinned there too.
    """
    if not cargo:
        return ""
    return " (a cargo run's true bound is up to 2x this if an LNK1104 relink retry fired)"


def probe(
    *,
    file: Path,
    old: str,
    new: str,
    suite: Path,
    floor: int = DEFAULT_FLOOR,
    timeout: int = SUITE_TIMEOUT_SECONDS,
    journal_dir: Path | None = None,
    tests: tuple[str, ...] | None = None,
    baseline_timeout: int | None = None,
    narrow_baseline_timeout: int | None = None,
    nth: int | None = None,
) -> ProbeResult:
    """The public entry point. See `_probe_body` for the whole contract; this
    is a thin boundary around it.

    **A later repair, step 9 finding L1: converts `run_suite`'s `ValueError`
    (a `None` or non-positive EFFECTIVE timeout, for whichever phase of the
    call reaches it) into a `REFUSED` `ProbeResult`, never a traceback.**
    `run_suite` itself keeps raising -- that stays the internal contract a
    caller who bypasses `probe()` still gets loudly and immediately, per
    `bounded-waits` (`CLAUDE.md`) -- but a non-positive `--timeout` reaching
    the CLI produced a bare Python traceback instead of the `[REFUSED] ...`
    line and clean exit-1 verdict this module promises everywhere else. This
    is the ONE place that catches it: every `run_suite` call in this module
    is reached only through `_probe_body`, so catching here covers all of
    them, at whichever phase (the full-suite baseline, the fast path's
    narrow baseline, or either mutated run) the bad value reached. Composition
    stays safe regardless of WHERE inside `_probe_body` the raise happens:
    `_mutate_run_restore`'s own `finally` already restores the file byte-
    exactly and clears the journal before the exception ever reaches this
    boundary, which is `_mutate_run_restore`'s contract independent of what a
    caller does with what it raises.
    """
    try:
        return _probe_body(
            file=file,
            old=old,
            new=new,
            suite=suite,
            floor=floor,
            timeout=timeout,
            journal_dir=journal_dir,
            tests=tests,
            baseline_timeout=baseline_timeout,
            narrow_baseline_timeout=narrow_baseline_timeout,
            nth=nth,
        )
    except ValueError as exc:
        return ProbeResult(REFUSED, [tree_identity_line(file), f"[{REFUSED}] {exc}"])


def _probe_body(
    *,
    file: Path,
    old: str,
    new: str,
    suite: Path,
    floor: int = DEFAULT_FLOOR,
    timeout: int = SUITE_TIMEOUT_SECONDS,
    journal_dir: Path | None = None,
    tests: tuple[str, ...] | None = None,
    baseline_timeout: int | None = None,
    narrow_baseline_timeout: int | None = None,
    nth: int | None = None,
) -> ProbeResult:
    """The whole contract. Restores `file` byte-exactly whatever happens.

    "Whatever happens" now includes being KILLED mid-suite, which the `finally`
    cannot cover: a journal written before the mutation lets the NEXT run repair
    it. See `recover_abandoned`.

    **`nth` names WHICH in-code occurrence of `old` to mutate,
    1-based, when more than one exists.** `None` (the default) is refused
    the moment `_apply` finds more than one in-code occurrence; see
    `_apply`'s own docstring for the full refusal and `_nth_code_span` for
    how a given `nth` is resolved to a span.

    **`baseline_timeout` and `narrow_baseline_timeout` (from a later review
    round, finding 1: "a separate ceiling per PHASE of the call rather than
    per call") give the COMPLETING side of a call its own clock, separate
    from `timeout`, which remains the clock a HANG is scored against.**
    Both default to `timeout` when `None`, which is every caller before this
    round and every caller that never passes them. `baseline_timeout`
    overrides the full-suite baseline run below (reached directly when
    `tests` is empty, or via the fast path's fallback); `narrow_baseline_timeout`
    overrides `_fast_path`'s own narrow baseline. Four review rounds
    relocated the same defect by scoring a completing baseline against the
    identical bound a hang is scored against: raising that ONE shared
    number only moves the load at which it overruns, since nothing bounds a
    process launch from above under contention. Decoupling the two removes
    the race rather than widening it.

    **`tests` is the fast path.** When given, the NAMED test
    id(s) are tried FIRST, both baseline and mutant, entirely without touching
    the full suite: a review's `defended_by` is usually one to four tests
    against a suite of over a hundred, and running the whole thing twice per
    mutation is most of a review's 17-to-28-minute cost. The fast path can
    only ever answer KILLED or "keep looking" (see `_fast_path` below); a
    narrow miss, an unclean narrow baseline, or any ambiguity ALWAYS falls
    back to running the full paired suite exactly as if `tests` were `None`,
    so the fast path can never manufacture a SURVIVED, a NO_RUN or a REFUSED.
    Every probe-honesty refusal condition below applies unchanged to that
    fallback run. The transcript names which path produced the verdict
    (`verdict path: fast` or `verdict path: full suite`).
    """
    # ONE LINE, EVERY RUN, BEFORE ANYTHING ELSE CAN RETURN EARLY.
    # Every return statement below this point threads `out` through, or this
    # line is silently dropped on exactly the REFUSED paths a reviewer is
    # most likely to be reading a transcript from.
    out: list[str] = [tree_identity_line(file)]

    # FIRST, before anything else can be measured against a tree a previous run
    # may have left mutated. Recovery is a REFUSAL, not a quiet fix: the operator
    # has to know that whatever they measured in between was measured against
    # code nobody wrote.
    recovered = recover_abandoned(journal_dir=journal_dir)
    if recovered:
        return ProbeResult(REFUSED, out + recovered)

    if not file.exists():
        return ProbeResult(REFUSED, out + [f"[{REFUSED}] no file at {file}"])
    if not suite.exists():
        return ProbeResult(REFUSED, out + [f"[{REFUSED}] no suite at {suite}"])

    cargo = is_cargo_suite(suite)
    if cargo and shutil.which("cargo") is None:
        return ProbeResult(
            REFUSED,
            out
            + [
                f"[{REFUSED}] {suite} is a Cargo manifest but `cargo` is not on PATH, so "
                f"nothing could be run. A probe that cannot execute its suite reports "
                f"that, never a verdict."
            ],
        )

    # Checked before anything is read or written: this REFUSAL
    # depends only on the literal text of `new`, never on the file or the
    # suite, so there is nothing to gain by deferring it past the checks
    # above and every reason not to mutate a tracked file first.
    widening = capability_widening_match(new)
    if widening is not None:
        return ProbeResult(
            REFUSED,
            out
            + [
                f"[{REFUSED}] --new {widening!r} is the CAPABILITY-WIDENING shape "
                f"trace-the-known-bad names by exclusion: its prescribed idiom DISABLES "
                f"a branch (--new 'if False:', or the Rust spelling that opens with "
                f"'if false'), and this unconditionally ENABLES one instead, which "
                f"proves nothing about whether the branch's own logic is defended -- it "
                f"is a different mutation, in the opposite direction, wearing the same "
                f"idiom's clothes. This is a HEURISTIC over the literal text of --new "
                f"only ('if True:' / 'if 1:' / 'if not False:' / the Rust spelling that "
                f"opens with 'if true', and nothing broader): a --new that widens "
                f"capability through '>' becoming '>=', 'and' becoming 'or', or a "
                f"raised constant is NOT caught here. Point --new at the disabling "
                f"shape instead."
            ],
        )

    original = file.read_bytes()
    before_digest = _digest(original)
    original_stat = file.stat()

    mutated, occurrences, note = _apply(original, old, new, nth=nth)
    if mutated is None:
        return ProbeResult(REFUSED, out + [f"[{REFUSED}] {note}"])

    if cargo:
        # DISCLOSED, on every Rust verdict, rather than left for a reader to
        # infer from a missing feature. `code_occurrences` discriminates code
        # from prose with `tokenize` plus `ast`, which are Python's. Handed a
        # `.rs` file both fail, `_inert_spans` returns [] by its own documented
        # contract, and every occurrence is counted as code.
        #
        # It is NOT hand-rolled for Rust, and that is a decision with a scar
        # rather than laziness: `screenshots.py`'s hand-rolled Rust tokenizer
        # produced seven hollow assertions inside one segment and its docstring
        # still enumerates three fail-opens (a nested block comment, a `*/`
        # inside a string inside a block comment, a non-literal path). Each fix
        # added a comment form and each was followed by another. A printed
        # disclosure a reader can act on beats a partial tokenizer a reader
        # mistakes for a complete one.
        out.append(
            "note: prose discrimination is NOT performed for Rust. A substitution landing "
            "only inside a `//` or `/* */` comment would compile to identical behavior and "
            "score as SURVIVED over a no-op. Check the target is executable code."
        )

    tests_tuple: tuple[str, ...] = tuple(tests) if tests else ()
    if tests_tuple:
        fast_verdict, fast_lines = _fast_path(
            file=file,
            old=old,
            new=new,
            mutated=mutated,
            original=original,
            before_digest=before_digest,
            original_stat=original_stat,
            cargo=cargo,
            suite=suite,
            tests=tests_tuple,
            floor=floor,
            timeout=timeout,
            journal_dir=journal_dir,
            narrow_baseline_timeout=narrow_baseline_timeout,
            nth=nth,
        )
        out.extend(fast_lines)
        if fast_verdict is not None:
            # Only KILLED, REFUSED (a genuine restore catastrophe inside the
            # fast attempt itself) or INCONCLUSIVE (the MUTATED
            # narrow run hung) ever come back here; a narrow miss, or a hung
            # narrow BASELINE, returns `None` above and this branch is not
            # taken. See `_fast_path`'s own docstring for why a SURVIVED can
            # never originate here.
            return ProbeResult(fast_verdict, out)

    # BASELINE FIRST, before anything is written. A red baseline is the FLOOR
    # this refuses on, and finding out afterwards would mean having mutated a
    # tracked file to learn something that made the mutation meaningless.
    out.append(
        "verdict path: full suite"
        + (" (the fast path found no reddening among the named tests)" if tests_tuple else "")
    )
    resolved_baseline_timeout = timeout if baseline_timeout is None else baseline_timeout
    baseline = run_suite(suite, timeout=resolved_baseline_timeout)
    # The nouns the messages below use. A cargo run has no `Ran N tests` line
    # and no loader error, so a message naming those would send a reader looking
    # for text that cannot be there.
    ran_line = "test result:" if cargo else "`Ran N tests`"
    verdict_line = "test result:" if cargo else "OK/FAILED"
    # ORDER MATTERS AND IS NOT ARBITRARY. Each branch below must be reachable,
    # or it is decoration that no test can defend. `timed_out` has to be asked
    # BEFORE the generic "no transcript" question: both leave
    # `ran is None`, and a HANG the probe's own timeout killed is a different
    # fact from a crash or a compile error that exited on its own, so folding
    # it into the generic NO_RUN message would say "a timeout" while never
    # naming which run or how long. The empty discovery in particular has to
    # be asked BEFORE the generic "no verdict line" question: CPython prints
    # `NO TESTS RAN` rather than `OK` over zero tests, so an empty suite
    # carries no OK/FAILED line at all and a generic check would swallow it
    # under a message naming the wrong condition. Measured on this
    # interpreter rather than assumed, after the probe reported that branch
    # SURVIVED its own mutation.
    if baseline.timed_out:
        # A later review round's repair (finding 2). NOT "before any
        # mutation was written": the FULL-SUITE baseline runs before ITS OWN
        # mutation, but can be reached AFTER the fast path already wrote and
        # byte-exactly restored one of its own (a narrow miss that fell
        # back to here, see `_write_status_clause`'s docstring). Deriving
        # this from `_write_status_clause` - exactly like every sibling
        # NO_RUN branch below already does - means this line can never
        # again disagree with what the transcript actually says.
        return ProbeResult(
            INCONCLUSIVE,
            out
            + [
                f"[{INCONCLUSIVE}] baseline TIMED OUT after "
                f"{baseline.elapsed_seconds:.1f}s (bound {resolved_baseline_timeout}s"
                f"{_cargo_bound_note(cargo)}) rather than "
                f"completing red or green. A baseline that never finished "
                f"proves nothing about the mutation; refusing a verdict "
                f"rather than guessing. {_write_status_clause(out, file)}"
                f"{_cargo_tail_clause(cargo, baseline)}"
            ],
        )
    if baseline.ran is None:
        return ProbeResult(
            NO_RUN,
            out
            + [
                f"[{NO_RUN}] baseline: no {ran_line} line at all, so this is not a "
                f"test transcript (a crash, a compile error, or output from "
                f"something else). {_write_status_clause(out, file)}"
                f"{_cargo_tail_clause(cargo, baseline)}"
            ],
        )
    if baseline.ran == 0:
        return ProbeResult(
            NO_RUN,
            out
            + [
                f"[{NO_RUN}] baseline ran 0 tests. The discovery is empty, and an "
                f"empty discovery reports clean in the same words a healthy suite "
                f"uses. {_write_status_clause(out, file)}"
            ],
        )
    if baseline.loader_error:
        return ProbeResult(
            NO_RUN,
            out
            + [
                f"[{NO_RUN}] baseline: the run hit `{_LOADER_MARKER}`, so the suite "
                f"failed to IMPORT and no test executed. {_write_status_clause(out, file)}"
            ],
        )
    if baseline.ok is None:
        return ProbeResult(
            NO_RUN,
            out
            + [
                f"[{NO_RUN}] baseline: {baseline.ran} test(s) ran but the output "
                f"carries no {verdict_line} verdict line. {_write_status_clause(out, file)}"
                f"{_cargo_tail_clause(cargo, baseline)}"
            ],
        )
    # EXECUTED, not collected. `Ran N tests` counts skips, so a suite reporting
    # `Ran 20 tests ... OK (skipped=18)` cleared `--floor 20` on two real
    # executions. Several suites here skip precisely when the checkout is
    # shallow, which is the environment the contract mandates probing in.
    if baseline.executed < floor:
        return ProbeResult(
            REFUSED,
            out
            + [
                f"[{REFUSED}] baseline EXECUTED {baseline.executed} test(s) "
                f"({baseline.ran} collected, {baseline.skipped} skipped), under the "
                f"declared floor of {floor}. A skipped test defends nothing. "
                f"{_write_status_clause(out, file)}"
            ],
        )
    if not baseline.ok:
        return ProbeResult(
            REFUSED,
            out
            + [
                f"[{REFUSED}] FLOOR: the unmutated baseline is RED "
                f"({len(baseline.failed)} failing: {', '.join(baseline.failed[:5]) or 'unnamed'}). "
                f"A mutation scored against an already-red suite proves nothing. "
                f"{_write_status_clause(out, file)}"
            ],
        )

    out.append(
        f"baseline: {baseline.ran} test(s), OK, floor {floor}; "
        f"{occurrences} occurrence(s) of the target text in {file}"
        + (f" ({note})" if note else "")
    )

    mutant, restore_lines, abandoned = _mutate_run_restore(
        file=file,
        old=old,
        new=new,
        mutated=mutated,
        original=original,
        before_digest=before_digest,
        original_stat=original_stat,
        cargo=cargo,
        suite=suite,
        timeout=timeout,
        journal_dir=journal_dir,
        nth=nth,
    )
    out.extend(restore_lines)
    if abandoned is not None:
        return ProbeResult(abandoned, out)
    assert mutant is not None

    if mutant.timed_out:
        # Checked BEFORE `is_transcript` below for the same
        # reason the baseline's own check above is ordered first: a HANG the
        # probe's own timeout killed and a crash/compile error that exited
        # on its own both leave `ran is None`, and only this flag tells them
        # apart. This is the incident the item names directly: the mutated
        # run never fails, it just never returns, and scoring that as a
        # clean SURVIVED (or silently as a generic NO_RUN with no mention of
        # WHICH run hung or for how long) is exactly the false-positive
        # `probe-honesty` warns would argue for deleting a working guard.
        return ProbeResult(
            INCONCLUSIVE,
            out
            + [
                f"[{INCONCLUSIVE}] the mutated run TIMED OUT after "
                f"{mutant.elapsed_seconds:.1f}s (bound {timeout}s{_cargo_bound_note(cargo)}) "
                f"rather than completing red or green. A run that never finished is "
                f"neither a kill nor a survival; refusing a verdict rather than "
                f"guessing which.{_cargo_tail_clause(cargo, mutant)}"
            ],
        )

    if not mutant.is_transcript:
        reason = (
            f"no {ran_line} line at all"
            if mutant.ran is None
            else f"no {verdict_line} verdict line"
        )
        unviable = (
            " For a Rust guard this is most often an UNVIABLE mutant: the substitution did "
            "not compile, so no test ran. That is information, not a kill."
            if cargo
            else ""
        )
        return ProbeResult(
            NO_RUN,
            out
            + [
                f"[{NO_RUN}] mutated run: {reason}, so nothing was scored.{unviable}"
                f"{_cargo_tail_clause(cargo, mutant)}"
            ],
        )
    if mutant.loader_error:
        return ProbeResult(
            NO_RUN,
            out
            + [
                f"[{NO_RUN}] mutated run: `{_LOADER_MARKER}`. The suite failed to "
                f"IMPORT under the mutation, so no test executed. This is the exact "
                f"shape that has twice been reported as a kill: the run that never "
                f"happened is not evidence the guard is defended."
            ],
        )
    if mutant.ran != baseline.ran:
        return ProbeResult(
            NO_RUN,
            out
            + [
                f"[{NO_RUN}] mutated run executed {mutant.ran} test(s) against the "
                f"baseline's {baseline.ran}. A different population is not a "
                f"comparison, and a SHRINKING one is what a collection error looks "
                f"like from the outside.{_cargo_tail_clause(cargo, mutant)}"
            ],
        )

    # THE POPULATION CHECK ABOVE CANNOT SEE THIS. `ran` counts skips, so a
    # mutation that turns the DEFENDING test into a skip leaves `ran` identical
    # and every remaining test green: a textbook false SURVIVED, and the tool
    # would then be telling a reader to DELETE a guard that is in fact defended.
    # The shape is not hypothetical: `@unittest.skipUnless(mod.LIMIT == 5, ...)`
    # skips exactly when the constant under mutation changes, and a repo's own
    # floor/ceiling checks commonly use that form.
    if mutant.skipped > baseline.skipped:
        return ProbeResult(
            NO_RUN,
            out
            + [
                f"[{NO_RUN}] the mutation SKIPPED {mutant.skipped - baseline.skipped} "
                f"more test(s) than the baseline ({mutant.skipped} vs "
                f"{baseline.skipped}). A test unittest walked past is not a test that "
                f"passed, and scoring this would report SURVIVED over the very tests "
                f"most likely to be the guard's own."
            ],
        )

    if mutant.ok:
        return ProbeResult(
            SURVIVED,
            out
            + [
                f"[{SURVIVED}] {mutant.ran} test(s) ran and ALL PASSED under the "
                f"mutation. No test in {suite.name} defends this behavior."
            ],
        )
    # A KILLED THAT NAMES NO TEST IS THE LOADER ERROR IN A DIFFERENT COSTUME:
    # the run went red and no outcome can be attributed to anything. Stock
    # unittest produces it from an `@unittest.expectedFailure` test the mutation
    # makes pass (`FAILED (unexpected successes=1)`, with no `FAIL:` header
    # anywhere), and from a nested runner writing into a suppressed stream. The
    # asymmetry was the defect: the tool refused the import-time version and
    # scored this one, printing `[KILLED] 0 of 2 test(s) reddened: ` and exiting
    # 0. That line is what a reviewer copies into `defended_by.reddened`, and
    # `CLAUDE.md` calls an entry claiming zero reddened tests a BLOCK, so the
    # tool's own success output produced a report its own schema rejects.
    if not mutant.failed:
        return ProbeResult(
            NO_RUN,
            out
            + [
                f"[{NO_RUN}] the mutated run went RED but names no failing test "
                f"({mutant.ran} ran). An unattributable failure is not evidence a "
                f"guard is defended, which is the same reason the loader error above "
                f"is refused rather than scored.{_cargo_tail_clause(cargo, mutant)}"
            ],
        )
    return ProbeResult(
        KILLED,
        out
        + [
            f"[{KILLED}] {len(mutant.failed)} of {mutant.ran} test(s) reddened: "
            f"{'; '.join(mutant.failed)}"
        ],
    )


def _resolve(raw: str) -> Path:
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else (REPO_ROOT / candidate)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(
        description=(
            "Mutate a file, run a suite, and report "
            "KILLED/SURVIVED/NO RUN/INCONCLUSIVE/REFUSED. Exits 0 only on KILLED."
        )
    )
    parser.add_argument("--file", required=True, help="the file to mutate")
    parser.add_argument("--old", required=True, help="exact text to replace")
    parser.add_argument("--new", required=True, help="text to replace it with")
    parser.add_argument(
        "--suite",
        required=True,
        help=(
            "the test suite to run: a Python module, or a Cargo.toml to run "
            "`cargo test --lib --bins --tests` against that crate"
        ),
    )
    parser.add_argument(
        "--floor",
        type=int,
        default=DEFAULT_FLOOR,
        help="refuse a verdict when the baseline runs fewer tests than this",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=SUITE_TIMEOUT_SECONDS,
        help="per-suite bound in seconds",
    )
    parser.add_argument(
        "--tests",
        nargs="+",
        default=None,
        metavar="TEST_ID",
        help=(
            "run only these NAMED test id(s) against the mutation first "
            "(unittest dotted names, e.g. GuardTests.test_x; or Rust "
            "mod::test names for a Cargo suite) and report KILLED the moment "
            "any of them reddens, without running the full suite. When none "
            "of them redden this falls back AUTOMATICALLY to the full paired "
            "suite before any verdict, so this can never manufacture a "
            "SURVIVED"
        ),
    )
    parser.add_argument(
        "--nth",
        type=int,
        default=None,
        metavar="K",
        help=(
            "when --old occurs more than once in code, name WHICH "
            "in-code occurrence (1-based, in document order) to mutate. "
            "Omitting --nth for an ambiguous --old is REFUSED rather than "
            "mutating every occurrence at once; the alternative is "
            "lengthening --old (more surrounding text) until it is unique."
        ),
    )
    args = parser.parse_args(argv)

    result = probe(
        file=_resolve(args.file),
        old=args.old,
        new=args.new,
        suite=_resolve(args.suite),
        floor=args.floor,
        timeout=args.timeout,
        tests=tuple(args.tests) if args.tests else None,
        nth=args.nth,
    )
    for line in result.lines:
        print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
