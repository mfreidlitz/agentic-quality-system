#!/usr/bin/env python
"""Guard that no wait under a scripts directory is unbounded.

**The worse failure in this class is a subprocess that BLOCKS rather than
one that fails.** A failure is a verdict a reader can act on; a block is
silence until someone notices the bill. A CI job hung to its ceiling once
burns real minutes for zero signal, and the fix is uniform enforcement: a
policy that exists only as a convention some scripts follow and others do
not is exactly the kind of instruction that gets dropped under pressure.

Two sub-checks:

  subprocess-bounds
      Every `subprocess.run|call|check_call|check_output` and every
      `urlopen` under the scanned directory's `*.py` files passes a
      `timeout=` keyword. `Popen` is detected but judged on its wait
      instead, for the reason recorded at `_POPEN_ATTR`: `Popen.__init__`
      takes no `timeout` and raises `TypeError` if given one, so the bound
      has to live on `communicate()`/`wait()` instead.

  git-identity-env
      Every git-prefixed spawn call (`subprocess.*(["git", ...], ...)`)
      under the scanned directory's `*.py` files passes an `env=` this
      scan can trace to a dict carrying `GIT_AUTHOR_NAME`,
      `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`, `GIT_COMMITTER_EMAIL` and
      `GIT_TERMINAL_PROMPT`. The reason this exists at all: a real `git
      commit` call that inherits the AMBIENT identity in a developer's
      `.git/config` succeeds on a laptop and fails on a runner that has
      none, which is a defect local verification cannot catch by
      construction (there is nothing wrong to reproduce locally).
      Enforced unconditionally, on read-only git calls too (`rev-parse`,
      `show`, `log`, ...): a read-only helper still inherits ambient
      `GIT_TERMINAL_PROMPT` state, so it is not a special case.

      Resolution follows an `env=` argument to an inline dict literal, a
      same-module `NAME = {...}` or `self.NAME = {...}` assignment, a
      `NAME = dict(os.environ)` followed by `NAME["KEY"] = ...` subscript
      assignments, a `{**OTHER, ...}` spread of a name resolved the same
      way, or ONE HOP across a `from <module> import NAME` within the
      scanned directory. Argv recognition accepts a literal `["git",
      ...]`/`("git", ...)`, a `["git"] + args` `BinOp` whose left operand
      is such a literal, and an unaliased `shlex.split("git ...")` call.
      WHAT THIS CANNOT SEE, disclosed rather than left for a reader to
      discover: an argv held ENTIRELY in a variable (`argv = ["git",
      ...]; subprocess.run(argv)`) is not recognized as a git call at all
      (the same "an unverifiable bound is not a bound" trade-off
      `_is_bounded` already makes for `timeout=`), and `shlex.split`
      reached through any import alias other than the bare name `shlex`
      is not traced either; a call routed through a CROSS-MODULE HELPER
      FUNCTION is invisible here because it is not itself a
      `subprocess.*` call -- the helper's own definition is scanned
      instead, at the one place the real `subprocess.run` call lives, so
      the coverage is not lost, only relocated to where the argument
      actually is.

**NOT PORTED: a third sub-check, `listener-locality`, existed in the
source repo this tool was ported from.** It enforced that no test under a
Rust source tree bound its own `std::net::TcpListener` outside one
named, shared, bounded helper module, with an allowlist of exceptions
naming an EXACT bind count per path. That check is inseparable from one
repo's own Rust module layout (a hardcoded path to the one shared
listener helper, a hardcoded allowlist of the files exempt from using
it): a generic tool pointed at an arbitrary repo has no way to know which
file, if any, plays that role. Rather than invent a config surface for a
pattern this specific, it was dropped; a repo that wants the equivalent
protection for its own Rust tree writes its own scoped check.

AST for the Python half, never regex. A real spawn site commonly spans
several lines, so a line-oriented scan sees `subprocess.run(` and
`timeout=` on different lines and can only guess which call the keyword
belongs to; `ast` answers exactly.

`test_*.py` is scanned like everything else, and that is the point rather
than an oversight: a guard that exempts test files exempts exactly the
files most likely to spawn a real subprocess and hang a real CI run.

Run: `python check_hermetic_bounds.py --root <dir>` (exits 0 on PASS, 1 on
FAIL). Wire it into a repo's own verify harness as a subprocess call; it
needs no toolchain beyond Python itself.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

# A positive allowlist of the `subprocess` members that actually START a
# process. Deliberately not a negative list of exclusions: `TimeoutExpired` and
# `CompletedProcess` are ordinary constructors that wait for nothing, and a
# scanner counting them would inflate its own non-vacuity floor with calls it
# can never sensibly demand a bound from.
SPAWN_ATTRS = frozenset({"run", "Popen", "call", "check_call", "check_output"})
_SPAWN_MODULE = "subprocess"
# `urllib.request.urlopen`, `request.urlopen`, or a bare aliased import. An
# unbounded network read is the same class and worse in CI, where a black-holed
# connection never even gets an RST back to end the wait.
_URLOPEN_ATTR = "urlopen"

# A repo may hold no `Popen` call site at all, in which case this branch is
# forward-looking rather than dead: it is kept because `Popen` plus a bare
# `communicate()` is the cheapest way to introduce an unbounded wait, and it
# is what an author reaches for precisely when `run` will not do. The keyword
# rule cannot judge it: `Popen.__init__` accepts no `timeout` and raises
# TypeError if given one, so the bound belongs on the wait.
_POPEN_ATTR = "Popen"
_WAIT_ATTRS = frozenset({"communicate", "wait"})

_TIMEOUT_KEYWORD = "timeout"

_REASON_NO_TIMEOUT = "no timeout= keyword"
_REASON_POPEN_TIMEOUT_KWARG = (
    "Popen() takes no timeout= keyword and raises TypeError if given one; "
    "bound the wait instead"
)
_REASON_POPEN_UNWAITED = "no bounded communicate(timeout=) or wait(timeout=) for the handle"


@dataclass(frozen=True)
class Violation:
    """One spawning call whose wait is not bounded."""

    path: str  # as given to the scanner: repo-relative for a real scan
    line: int
    call: str  # the unparsed callee, e.g. "subprocess.run"
    reason: str  # why it is unbounded


@dataclass(frozen=True)
class ScanResult:
    """What a scan looked at, not only what it objected to.

    `files` and `spawn_calls` exist so a caller can prove the scan was NOT
    vacuous. `violations` alone cannot: an empty list is what both a clean tree
    and a collapsed discovery produce, and telling those two apart is the entire
    lesson of the canary case that first exposed this failure mode.
    """

    files: int
    spawn_calls: int
    violations: tuple[Violation, ...]


def _direct_spawn_aliases(tree: ast.Module) -> dict[str, str]:
    """Local name -> the spawning member `from ... import ...` bound it to.

    Without this, `from subprocess import run` followed by `run([...])` is a
    documented way around a scanner keyed on the dotted `subprocess.` prefix.
    Scoped to the two source modules so an unrelated `from x import run` cannot
    make every `run(...)` in a file a false positive. The VALUE matters as well
    as the key: `from subprocess import Popen as P` must still be recognized as
    a `Popen`, whose bound lives on the wait rather than on the call.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        root = node.module.split(".")[0]
        for alias in node.names:
            if alias.name == "*":
                # A star import binds every public member under its own name,
                # so `from subprocess import *` plus a bare `run(...)` is the
                # same bypass the named form already closes.
                if root == _SPAWN_MODULE:
                    aliases.update({attr: attr for attr in SPAWN_ATTRS})
                elif root == "urllib":
                    aliases[_URLOPEN_ATTR] = _URLOPEN_ATTR
            elif root == _SPAWN_MODULE and alias.name in SPAWN_ATTRS:
                aliases[alias.asname or alias.name] = alias.name
            elif root == "urllib" and alias.name == _URLOPEN_ATTR:
                aliases[alias.asname or alias.name] = _URLOPEN_ATTR
    return aliases


def _spawn_module_aliases(tree: ast.Module) -> frozenset[str]:
    """Local names that denote the `subprocess` MODULE, via `ast.Import`.

    The gap this closes: the from-import rule above walked `ast.ImportFrom`
    only, so `import subprocess as sp` followed by `sp.run(...)` scored zero
    spawn calls -- and that is the most idiomatic alias shape there is. The
    from-import rule had already anticipated the concept, so this was an
    omission rather than a decision.

    `subprocess` is always in the set, not only when an import was seen: a name
    already spelled `subprocess.run` is a spawn under any import machinery, and
    seeding it keeps the scanner fail-closed. `urllib` needs no equivalent
    because `urlopen` is accepted under any qualifier (see `_is_spawn`).
    """
    names = {_SPAWN_MODULE}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import):
            continue
        for alias in node.names:
            if alias.asname and alias.name.split(".")[0] == _SPAWN_MODULE:
                names.add(alias.asname)
    return frozenset(names)


def _is_spawn(call_name: str, direct_aliases: dict[str, str], modules: frozenset[str]) -> bool:
    """True when `call_name` names a call that starts a process or a request.

    The dotted form must be qualified by a name bound to `subprocess` (so an
    unrelated `self.run(...)` or `parser.call()` is not swept in), while
    `urlopen` is accepted under any qualifier because `urllib.request` is
    routinely bound to a shorter name at the import.
    """
    if call_name in direct_aliases:
        return True
    module, _, attr = call_name.rpartition(".")
    if not module:
        return False
    if attr == _URLOPEN_ATTR:
        return True
    return attr in SPAWN_ATTRS and module.split(".")[-1] in modules


def _is_popen(call_name: str, direct_aliases: dict[str, str]) -> bool:
    """True for a handle-returning spawn, `from subprocess import Popen as P` included."""
    if call_name in direct_aliases:
        return direct_aliases[call_name] == _POPEN_ATTR
    return call_name.rpartition(".")[2] == _POPEN_ATTR


def _is_bounded(node: ast.Call) -> bool:
    """True only when a literal `timeout=` keyword is present.

    A `**kwargs` splat MIGHT carry one; the scanner cannot know, and an
    unverifiable bound is not a bound. Flagged deliberately, so a caller that
    means it writes the keyword where a reader can see it.
    """
    return any(keyword.arg == _TIMEOUT_KEYWORD for keyword in node.keywords)


def _position(node: ast.expr) -> tuple[int, int]:
    """Identity for one call site within one parse. Line alone is not enough:
    two calls can share a line."""
    return (node.lineno, node.col_offset)


def _bounded_wait_names(tree: ast.Module) -> frozenset[str]:
    """Local names on which a bounded `communicate`/`wait` is called somewhere.

    Module-wide rather than flow-sensitive, and deliberately so: the scanner
    answers "is this handle ever waited on with a bound", which is the question
    a reader can also answer by looking. `poll()` is NOT a wait -- it returns
    immediately and blocks nothing, so it can never bound anything.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _WAIT_ATTRS or not _is_bounded(node):
            continue
        if isinstance(node.func.value, ast.Name):
            names.add(node.func.value.id)
    return frozenset(names)


def _inline_bounded_spawns(tree: ast.Module) -> frozenset[tuple[int, int]]:
    """Positions of spawn calls waited on without ever being named, as in
    `Popen(...).communicate(timeout=5)`."""
    positions: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _WAIT_ATTRS or not _is_bounded(node):
            continue
        if isinstance(node.func.value, ast.Call):
            positions.add(_position(node.func.value))
    return frozenset(positions)


def _handle_names(tree: ast.Module) -> dict[tuple[int, int], str]:
    """Spawn-call position -> the local name its result was bound to.

    Covers `p = Popen(...)`, the annotated form, and `with Popen(...) as p`.
    A tuple-unpacking or attribute target binds no simple name and so stays
    unattributed, which the caller reads as unbounded: an unverifiable bound is
    not a bound, the same call the `**kwargs` rule already makes.
    """
    handles: dict[tuple[int, int], str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    handles[_position(node.value)] = target.id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Call):
            if isinstance(node.target, ast.Name):
                handles[_position(node.value)] = node.target.id
        elif isinstance(node, ast.withitem) and isinstance(node.context_expr, ast.Call):
            if isinstance(node.optional_vars, ast.Name):
                handles[_position(node.context_expr)] = node.optional_vars.id
    return handles


def _popen_reason(
    node: ast.Call,
    handles: dict[tuple[int, int], str],
    waited: frozenset[str],
    inline: frozenset[tuple[int, int]],
) -> str | None:
    """Why this `Popen` is unbounded, or None when its wait is bounded.

    A `timeout=` on the constructor is a violation in its own right, checked
    FIRST and regardless of the wait: the line raises `TypeError` the first time
    it runs, so nothing downstream of it can bound anything. `with Popen(...)`
    without an explicit bounded wait is a violation too, because `__exit__`
    calls `wait()` with no bound at all.
    """
    if _is_bounded(node):
        return _REASON_POPEN_TIMEOUT_KWARG
    if _position(node) in inline:
        return None
    handle = handles.get(_position(node))
    if handle is not None and handle in waited:
        return None
    return _REASON_POPEN_UNWAITED


def scan_source(source: str, path: str) -> ScanResult:
    """Pure: scan one module's text. Raises SyntaxError on unparseable input.

    Deliberately propagates rather than returning an empty result: a file the
    scanner cannot parse contributes zero violations, which is byte-identical to
    a clean file. The caller turns that into a named FAIL.
    """
    tree = ast.parse(source, filename=path)
    aliases = _direct_spawn_aliases(tree)
    modules = _spawn_module_aliases(tree)
    handles = _handle_names(tree)
    waited = _bounded_wait_names(tree)
    inline = _inline_bounded_spawns(tree)
    spawn_calls = 0
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        if not _is_spawn(name, aliases, modules):
            continue
        spawn_calls += 1
        if _is_popen(name, aliases):
            reason = _popen_reason(node, handles, waited, inline)
        else:
            reason = None if _is_bounded(node) else _REASON_NO_TIMEOUT
        if reason is not None:
            violations.append(Violation(path=path, line=node.lineno, call=name, reason=reason))
    return ScanResult(files=1, spawn_calls=spawn_calls, violations=tuple(violations))


def scan_targets(directory: Path) -> list[Path]:
    """Every `*.py` directly under `directory`, sorted.

    Not `rglob`: `scripts/` is flat, and a recursive walk would descend into
    `__pycache__` and any future vendored tree, scanning code this repo does not
    own and cannot fix.
    """
    return sorted(p for p in directory.glob("*.py") if p.is_file())


def scan_directory(directory: Path) -> ScanResult:
    """I/O: scan every `*.py` in `directory`. Paths are reported relative to it."""
    files = 0
    spawn_calls = 0
    violations: list[Violation] = []
    for path in scan_targets(directory):
        result = scan_source(path.read_text(encoding="utf-8"), path.name)
        files += result.files
        spawn_calls += result.spawn_calls
        violations.extend(result.violations)
    return ScanResult(files=files, spawn_calls=spawn_calls, violations=tuple(violations))


def check_subprocess_bounds(directory: Path | None = None) -> tuple[str, str]:
    """(status, detail) for the `subprocess-bounds` sub-check.

    Read at CALL time, never bound as a default argument: `directory=None`
    resolves `Path.cwd() / "scripts"` here rather than at import time.

    The invariant is an "objected to nothing" assertion, which an empty scan
    satisfies having compared nothing, so `files == 0` (the *.py glob found
    nothing under `directory`) FAILs unconditionally: an empty `--root` is a
    wrong path far more often than it is a real, empty repo. `spawn_calls ==
    0` (files exist but none of them ever spawn a subprocess or open a URL)
    PASSes instead of FAILing: a repo installed once and pointed at many
    different target repos cannot assume any of them shells out to anything,
    so a scripts directory that genuinely never spawns a process is not a
    broken scan, it is a clean one.
    """
    if directory is None:
        directory = Path.cwd() / "scripts"
    try:
        result = scan_directory(directory)
    except SyntaxError as exc:
        return ("FAIL", f"could not parse {exc.filename}:{exc.lineno}: {exc.msg}")
    if not result.files:
        return ("FAIL", f"scanned 0 file(s) under {directory}; the *.py glob found nothing")
    if not result.spawn_calls:
        return (
            "PASS",
            f"0 spawn/urlopen call(s) across {result.files} file(s) under {directory}; "
            "nothing here spawns a subprocess or opens a URL, so there is nothing for this "
            "sub-check to judge",
        )
    if result.violations:
        listed = "; ".join(f"{v.path}:{v.line} {v.call} ({v.reason})" for v in result.violations)
        return (
            "FAIL",
            f"{len(result.violations)} unbounded call(s) of {result.spawn_calls} scanned: "
            f"{listed} -- pass an explicit timeout= (a named constant, never a literal), "
            "except for Popen, which is bounded by its wait: communicate(timeout=...) "
            "or wait(timeout=...)",
        )
    return (
        "PASS",
        f"{result.spawn_calls} spawn/urlopen call(s) across {result.files} file(s), all bounded",
    )


# --- git-identity-env ---------------------------------------------------------

# CLAUDE.md's bounded-waits rule: every git subprocess under `scripts/` passes
# explicit `GIT_AUTHOR_*`/`GIT_COMMITTER_*` identity plus `GIT_TERMINAL_PROMPT=0`
# rather than inherit whatever `.git/config` or terminal state the process
# happens to run under. A recorded incident is the reason: a test that made real
# `git commit` calls inherited the ambient identity present in a developer's
# `.git/config` and absent on a runner, so the commit failed only there, and
# three separate attempts to reproduce the runner's environment locally all
# still passed. Unconditional on the subcommand: `mutation_probe.py`'s
# `_run_git` sets the full set on its two READ-ONLY calls too, for the reason
# stated in its own docstring -- the rule binds every git subprocess, not only
# the ones that commit.
GIT_IDENTITY_KEYS: frozenset[str] = frozenset(
    {
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "GIT_TERMINAL_PROMPT",
    }
)

_GIT_ARGV_LITERAL = "git"

_REASON_NO_ENV_KWARG_GIT = (
    "no env= keyword; the git subprocess inherits ambient identity/prompt state "
    "(a recorded incident: an ambient GIT_AUTHOR/COMMITTER identity present in a "
    "developer's .git/config and absent on a runner failed 18 consecutive pushes)"
)


@dataclass(frozen=True)
class GitIdentityViolation:
    """One git-prefixed spawn call with no visible identity route."""

    path: str
    line: int
    call: str
    reason: str


@dataclass(frozen=True)
class GitIdentityScanResult:
    """Same non-vacuity contract as [`ScanResult`]: `git_calls` proves the
    scan actually found git call sites rather than comparing nothing."""

    files: int
    git_calls: int
    violations: tuple[GitIdentityViolation, ...]


def _assignable_name(target: ast.expr) -> str | None:
    """`x` for a plain `Name` target, `"self.x"` for `self.x`, else None.

    `self.x` is not a special case invented for this check: it is how
    `test_check_verify_receipt.py`'s `RepoFixture.setUp` builds `self.env`,
    the shared identity dict its `git()` helper passes at every call site.
    """
    if isinstance(target, ast.Name):
        return target.id
    if (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    ):
        return f"self.{target.attr}"
    return None


def _string_key(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


_FUNCTION_KINDS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _build_parents(tree: ast.Module) -> dict[int, ast.AST]:
    """id(child) -> its immediate AST parent, for every node under `tree`."""
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _enclosing_id(node: ast.AST, parents: dict[int, ast.AST], kinds: tuple[type, ...]) -> int | None:
    """id() of the nearest enclosing node of one of `kinds`, walking up
    `parents`, or None when `node` sits outside all of them (module level,
    for `kinds = _FUNCTION_KINDS`; outside any class, for `(ast.ClassDef,)`).
    """
    current = parents.get(id(node))
    while current is not None:
        if isinstance(current, kinds):
            return id(current)
        current = parents.get(id(current))
    return None


def _enclosing_function_chain(node: ast.AST, parents: dict[int, ast.AST]) -> tuple[int, ...]:
    """id()s of every enclosing function, INNERMOST FIRST -- the real lexical
    scope chain a nested function actually reads through.

    A single "nearest enclosing function, else module" (`_enclosing_id`) is
    not enough: a downstream test module's `_repo` methods build a
    complete `env` and pass it to a NESTED closure `run(*args)` that calls
    `subprocess.run(..., env=env)`. The call's nearest enclosing function is
    `run`, which never assigns `env` itself, so a two-tier
    (nearest-function-or-module) lookup missed `_repo`'s own `env` entirely
    and reported it a violation -- a false positive this scan introduced by
    approximating Python's real closure rule. `_resolve_plain_name` walks
    this whole chain before falling back to module level.
    """
    chain: list[int] = []
    current = parents.get(id(node))
    while current is not None:
        if isinstance(current, _FUNCTION_KINDS):
            chain.append(id(current))
        current = parents.get(id(current))
    return tuple(chain)


def _dict_definitions(
    tree: ast.Module, parents: dict[int, ast.AST]
) -> tuple[dict[int | None, dict[str, ast.Dict]], dict[int, dict[str, ast.Dict]]]:
    """(function-scoped plain-name definitions, class-scoped `self.X`
    definitions) -- the LAST `ast.Dict` literal assigned to each name WITHIN
    its own scope.

    **SCOPE-AWARE (a follow-up review round, the step 9 r1 MEDIUM-2).** The first version
    was module-wide last-wins: a name's definition was whichever dict literal
    textually came LAST anywhere in the file, so a git commit whose own
    function bound an identity-less `env` was read as CLEAN the moment an
    unrelated function later in the same module happened to bind the same
    name `env` to a complete dict -- a real violation laundered by a
    coincidence of naming. The origin repo's live shape at porting time held
    three same-named local `env` dicts across different test methods; the
    per-file probe on the first one SURVIVED because resolution used only the
    last.

    A plain NAME is scoped to its nearest enclosing FUNCTION (`None` for
    module level); a name not bound in its own function's scope falls back to
    module level at resolution time (`_resolve_plain_name`), which is what
    keeps a sibling module's `_CHILD_ENV` and every other module-level `_GIT_ENV`
    constant resolving from inside whichever function reads it.

    `self.X` is scoped to its nearest enclosing CLASS instead of its
    enclosing function, because that is where Python's own attribute lookup
    actually shares state: `RepoFixture.setUp` builds `self.env` and
    `RepoFixture.git()` reads it, two different METHODS of the one class, and
    scoping `self.X` to the function would break that real, correct pattern.

    `ast.Assign` only: no real env constant in this repo is annotated
    (`x: dict[str, str] = {...}`), so that form is left unresolved rather than
    adding a branch this file's own tests could not exercise against real
    usage.
    """
    function_scoped: dict[int | None, dict[str, ast.Dict]] = {}
    class_scoped: dict[int, dict[str, ast.Dict]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        for target in node.targets:
            name = _assignable_name(target)
            if name is None:
                continue
            if name.startswith("self."):
                class_id = _enclosing_id(node, parents, (ast.ClassDef,))
                if class_id is not None:
                    class_scoped.setdefault(class_id, {})[name] = node.value
            else:
                func_id = _enclosing_id(node, parents, _FUNCTION_KINDS)
                function_scoped.setdefault(func_id, {})[name] = node.value
    return function_scoped, class_scoped


def _subscript_key_assignments(
    tree: ast.Module, parents: dict[int, ast.AST]
) -> tuple[dict[int | None, dict[str, frozenset[str]]], dict[int, dict[str, frozenset[str]]]]:
    """The subscript-build twin of `_dict_definitions`, same scoping rules
    and the same reason: `env["KEY"] = ...` after `env = dict(os.environ)`
    (`mutation_probe.py`'s `_run_git`, `check_filed_claims.py`'s `_git`).
    Keys accumulate across the whole scope rather than resetting per
    assignment, the same trade-off `_dict_definitions` makes within one scope.
    """
    function_scoped: dict[int | None, dict[str, set[str]]] = {}
    class_scoped: dict[int, dict[str, set[str]]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Subscript):
            continue
        name = _assignable_name(target.value)
        key = _string_key(target.slice)
        if name is None or key is None:
            continue
        if name.startswith("self."):
            class_id = _enclosing_id(node, parents, (ast.ClassDef,))
            if class_id is not None:
                class_scoped.setdefault(class_id, {}).setdefault(name, set()).add(key)
        else:
            func_id = _enclosing_id(node, parents, _FUNCTION_KINDS)
            function_scoped.setdefault(func_id, {}).setdefault(name, set()).add(key)
    return (
        {scope: {n: frozenset(v) for n, v in names.items()} for scope, names in function_scoped.items()},
        {scope: {n: frozenset(v) for n, v in names.items()} for scope, names in class_scoped.items()},
    )


def _imported_names(tree: ast.Module) -> dict[str, str]:
    """local name -> the `scripts/` module it was imported from, via
    `from <module> import NAME` at module level (no relative imports are used
    under `scripts/`). Closes the `_DELTA_ENV` gap: defined in
    a shared test-helpers module, imported and used as `env=_DELTA_ENV` at
    seven real call sites in a sibling test module.
    """
    imports: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                imports[alias.asname or alias.name] = node.module
    return imports


@dataclass(frozen=True)
class _ModuleEnvIndex:
    """One module's env-name resolution surface, computed once per file."""

    parents: dict[int, ast.AST]
    function_scoped_definitions: dict[int | None, dict[str, ast.Dict]]
    function_scoped_subscript_keys: dict[int | None, dict[str, frozenset[str]]]
    class_scoped_definitions: dict[int, dict[str, ast.Dict]]
    class_scoped_subscript_keys: dict[int, dict[str, frozenset[str]]]
    imports: dict[str, str]


def _module_env_index(tree: ast.Module) -> _ModuleEnvIndex:
    parents = _build_parents(tree)
    function_defs, class_defs = _dict_definitions(tree, parents)
    function_subs, class_subs = _subscript_key_assignments(tree, parents)
    return _ModuleEnvIndex(
        parents=parents,
        function_scoped_definitions=function_defs,
        function_scoped_subscript_keys=function_subs,
        class_scoped_definitions=class_defs,
        class_scoped_subscript_keys=class_subs,
        imports=_imported_names(tree),
    )


def _lookup_dict_for_spread(
    name: str, function_scope_chain: tuple[int, ...], index: _ModuleEnvIndex
) -> tuple[tuple[int, ...], ast.Dict | None]:
    """(the continuation of the chain FROM where it was found, its Dict
    literal) for a `**name` spread inside another dict, trying the spread's
    OWN enclosing scope chain (innermost first) and falling back to module
    level. Dict-literal lookup only: no real spread in this repo targets a
    subscript-built env, so subscript keys are not merged in here
    (`_resolve_plain_name` is the version that does).

    The CONTINUATION, not just the scope it landed on, matters: a name found
    at chain position `i` was itself WRITTEN at that lexical point, so any
    `**spread` inside ITS OWN dict must search outward from there
    (`chain[i:]`), never from the original call site's innermost scope.
    """
    search = (*function_scope_chain, None)
    for i, scope in enumerate(search):
        defs = index.function_scoped_definitions.get(scope, {})
        if name in defs:
            return (function_scope_chain[i:] if scope is not None else ()), defs[name]
    return (), None


def _effective_dict_keys(
    dict_node: ast.Dict, index: _ModuleEnvIndex, function_scope_chain: tuple[int, ...], depth: int = 0
) -> frozenset[str]:
    """Every literal string key `dict_node` carries, following one `**name`
    spread of a name resolved through `function_scope_chain` (falling back to
    module level) as a dict literal (e.g. `{**_GIT_ENV, "PATH": ...}` in
    a shared test-helpers module). Bounded to 4 levels so a self-referential
    spread cannot loop; no real chain in this repo is more than one level
    deep.
    """
    keys: set[str] = set()
    for key, value in zip(dict_node.keys, dict_node.values):
        if key is not None:
            literal = _string_key(key)
            if literal is not None:
                keys.add(literal)
            continue
        if depth >= 4:
            continue
        if not isinstance(value, ast.Name):
            continue
        found_chain, spread_dict = _lookup_dict_for_spread(value.id, function_scope_chain, index)
        if spread_dict is not None:
            keys |= _effective_dict_keys(spread_dict, index, found_chain, depth + 1)
    return frozenset(keys)


def _resolve_plain_name(
    name: str, function_scope_chain: tuple[int, ...], index: _ModuleEnvIndex
) -> frozenset[str]:
    """Keys `name` carries, walking its REAL lexical scope chain (its own
    enclosing function first, then each function enclosing THAT one, then
    module level) rather than only its nearest enclosing function -- a
    two-tier "nearest function or module" lookup missed
    a downstream test module's `_repo() -> def run(*args): ...` shape,
    where `env` is built in `_repo` and read inside the NESTED closure `run`
    (found in a further follow-up while repairing the step 9 r1 MEDIUM-2: the
    scope split that closed the laundering was, on its first cut, too narrow
    by one level and flagged three legitimate closures as violations).

    The two contributions found within ONE chosen scope (a dict literal plus
    any subscript-assigned keys) are merged; two DIFFERENT scopes are never
    mixed with each other -- that mixing is exactly the MEDIUM-2 laundering
    this scoping exists to close.
    """
    search = (*function_scope_chain, None)
    for i, scope in enumerate(search):
        defs = index.function_scoped_definitions.get(scope, {})
        subs = index.function_scoped_subscript_keys.get(scope, {})
        if name not in defs and name not in subs:
            continue
        keys = subs.get(name, frozenset())
        if name in defs:
            continuation = function_scope_chain[i:] if scope is not None else ()
            keys = keys | _effective_dict_keys(defs[name], index, continuation)
        return keys
    return frozenset()


def _resolve_name_keys(
    name: str,
    function_scope_chain: tuple[int, ...],
    class_scope_id: int | None,
    index: _ModuleEnvIndex,
    modules: dict[str, _ModuleEnvIndex],
) -> frozenset[str]:
    """Keys `name` carries at THIS call site's scope: from its own lexical
    function-scope chain (falling back to module level), from its enclosing
    CLASS when it is a `self.X` attribute, or (one hop only) from the
    `scripts/` module it was imported from. One hop is exactly what
    `_DELTA_ENV` needs; a longer re-export chain does not exist in this repo.
    """
    if name.startswith("self."):
        if class_scope_id is None:
            return frozenset()
        defs = index.class_scoped_definitions.get(class_scope_id, {})
        subs = index.class_scoped_subscript_keys.get(class_scope_id, {})
        if name not in defs and name not in subs:
            return frozenset()
        keys = subs.get(name, frozenset())
        if name in defs:
            # `self.X` dicts spread from module level by convention: no real
            # `self.X` definition in this repo spreads another name, so this
            # is a disclosed simplification rather than a third scope kind
            # `_effective_dict_keys` would otherwise need.
            keys = keys | _effective_dict_keys(defs[name], index, ())
        return keys
    direct = _resolve_plain_name(name, function_scope_chain, index)
    if direct:
        return direct
    source = index.imports.get(name)
    other = modules.get(source) if source is not None else None
    if other is None:
        return frozenset()
    # Cross-module: module-level only, matching the one-hop contract above --
    # `_DELTA_ENV` is a module-level constant in a shared test-helpers module,
    # and a function-scoped cross-module import does not exist in this repo.
    return _resolve_plain_name(name, (), other)


def _env_keys_for(
    value: ast.expr,
    function_scope_chain: tuple[int, ...],
    class_scope_id: int | None,
    index: _ModuleEnvIndex,
    modules: dict[str, _ModuleEnvIndex],
) -> frozenset[str]:
    """Keys the `env=` argument's VALUE resolves to, or an empty set when it
    is not a form this scanner can trace (an unverifiable route is not a
    route, the same call `_is_bounded` already makes for `timeout=`)."""
    if isinstance(value, ast.Dict):
        return _effective_dict_keys(value, index, function_scope_chain)
    name = _assignable_name(value)
    if name is not None:
        return _resolve_name_keys(name, function_scope_chain, class_scope_id, index, modules)
    return frozenset()


def _is_git_argv(node: ast.Call) -> bool:
    """True when the call's first positional argument is an argv EXPRESSION
    this scanner can confidently say starts with the literal string `"git"`.

    Three shapes (from that same review round, the step 9 r1 LOW widened the first version's
    single shape, which required the whole argv expression to BE a literal
    rather than merely begin with one):

      - `["git", *args]` / `("git", *args)`: the literal itself.
      - `["git"] + more`: an `ast.BinOp` with `Add` whose LEFT operand is,
        recursively, one of these three shapes -- `subprocess.run(["git"] +
        args, ...)` visibly starts with the recognized literal form and a
        reader would reasonably expect it caught.
      - `shlex.split("git ...")`: a literal string argument equal to `"git"`
        or starting with `"git "`, called through the UNALIASED name
        `shlex` only -- `import shlex as sh` or `from shlex import split`
        are not traced, the same "an unverifiable route is not a route"
        trade-off `_is_bounded` already makes for `timeout=`.

    Still NOT recognized, disclosed in the module docstring: an argv held
    entirely in a variable (`argv = ["git", ...]; subprocess.run(argv)`).
    """
    if not node.args:
        return False
    return _argv_is_git_prefixed(node.args[0])


def _argv_is_git_prefixed(expr: ast.expr) -> bool:
    """The recursive predicate `_is_git_argv` applies to the argv expression
    itself, so `["git"] + args`'s LEFT operand can be checked the same way
    the whole expression would be."""
    if isinstance(expr, (ast.List, ast.Tuple)) and expr.elts:
        return _string_key(expr.elts[0]) == _GIT_ARGV_LITERAL
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        return _argv_is_git_prefixed(expr.left)
    if (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Attribute)
        and expr.func.attr == "split"
        and isinstance(expr.func.value, ast.Name)
        and expr.func.value.id == "shlex"
        and expr.args
    ):
        literal = _string_key(expr.args[0])
        if literal is not None:
            return literal == _GIT_ARGV_LITERAL or literal.startswith(f"{_GIT_ARGV_LITERAL} ")
    return False


def _git_call_sites(tree: ast.Module) -> list[ast.Call]:
    """Every spawn call in `tree` whose argv is git-prefixed, reusing the
    same alias detection `scan_source` uses for the timeout half of this
    file so a `from subprocess import run` or `import subprocess as sp`
    bypass cannot open a gap here that the sibling check already closed."""
    aliases = _direct_spawn_aliases(tree)
    modules = _spawn_module_aliases(tree)
    sites: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        if not _is_spawn(name, aliases, modules):
            continue
        if _is_git_argv(node):
            sites.append(node)
    return sites


def _evaluate_git_call(
    node: ast.Call, path: str, index: _ModuleEnvIndex, modules: dict[str, _ModuleEnvIndex]
) -> GitIdentityViolation | None:
    """None when the call has a traceable route to every required key."""
    name = ast.unparse(node.func)
    env_kw = next((kw for kw in node.keywords if kw.arg == "env"), None)
    if env_kw is None:
        return GitIdentityViolation(path, node.lineno, name, _REASON_NO_ENV_KWARG_GIT)
    function_scope_chain = _enclosing_function_chain(node, index.parents)
    class_scope_id = _enclosing_id(node, index.parents, (ast.ClassDef,))
    resolved = _env_keys_for(env_kw.value, function_scope_chain, class_scope_id, index, modules)
    missing = sorted(GIT_IDENTITY_KEYS - resolved)
    if not missing:
        return None
    return GitIdentityViolation(
        path,
        node.lineno,
        name,
        f"env= resolved via `{ast.unparse(env_kw.value)}` is missing {', '.join(missing)}",
    )


def scan_git_identity_source(source: str, path: str) -> GitIdentityScanResult:
    """Pure: one module in isolation, no cross-module import resolution --
    there is nothing else in scope to resolve against. Raises SyntaxError on
    unparseable input, exactly like `scan_source`.
    """
    tree = ast.parse(source, filename=path)
    index = _module_env_index(tree)
    modules = {Path(path).stem: index}
    violations: list[GitIdentityViolation] = []
    sites = _git_call_sites(tree)
    for node in sites:
        violation = _evaluate_git_call(node, path, index, modules)
        if violation is not None:
            violations.append(violation)
    return GitIdentityScanResult(files=1, git_calls=len(sites), violations=tuple(violations))


def scan_git_identity_directory(directory: Path) -> GitIdentityScanResult:
    """I/O: scan every `*.py` in `directory` together, so an `env=` naming a
    name imported from a SIBLING file in the same directory resolves rather
    than reading as unresolvable (the `_DELTA_ENV` gap named in the module
    docstring)."""
    trees: dict[str, ast.Module] = {}
    paths: dict[str, Path] = {}
    for path in scan_targets(directory):
        trees[path.stem] = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        paths[path.stem] = path
    modules = {stem: _module_env_index(tree) for stem, tree in trees.items()}
    files = 0
    git_calls = 0
    violations: list[GitIdentityViolation] = []
    for stem in sorted(trees):
        files += 1
        index = modules[stem]
        for node in _git_call_sites(trees[stem]):
            git_calls += 1
            violation = _evaluate_git_call(node, paths[stem].name, index, modules)
            if violation is not None:
                violations.append(violation)
    return GitIdentityScanResult(files=files, git_calls=git_calls, violations=tuple(violations))


def check_git_identity_env(directory: Path | None = None) -> tuple[str, str]:
    """(status, detail) for the `git-identity-env` sub-check.

    Read at CALL time, never bound as a default argument: `directory=None`
    resolves `Path.cwd() / "scripts"` here rather than at import time.

    **DISCLOSED GENERALIZATION.** The single-repo shape of this check FAILed
    when it found `*.py` files but zero git-prefixed spawn calls among them,
    on the theory that its own scripts directory was known in advance to
    contain some. A tool installed once and pointed at many different repos
    cannot know that in advance: most scripts directories never shell out to
    git at all, and treating that as a broken scan would make the check
    permanently, uselessly red for them. So a directory with `*.py` files
    but no git-prefixed call PASSes here, with a message that says exactly
    that rather than staying silent about the difference. The one floor that
    still FAILs unconditionally is finding NO `*.py` files at all: an empty
    `--root` is a wrong path far more often than it is a real, empty repo.
    """
    if directory is None:
        directory = Path.cwd() / "scripts"
    try:
        result = scan_git_identity_directory(directory)
    except SyntaxError as exc:
        return ("FAIL", f"could not parse {exc.filename}:{exc.lineno}: {exc.msg}")
    if not result.files:
        return ("FAIL", f"scanned 0 file(s) under {directory}; the *.py glob found nothing")
    if not result.git_calls:
        return (
            "PASS",
            f"0 git-prefixed spawn call(s) across {result.files} file(s) under {directory}; "
            "nothing here shells out to git, so there is nothing for this sub-check to judge",
        )
    if result.violations:
        listed = "; ".join(f"{v.path}:{v.line} {v.call} ({v.reason})" for v in result.violations)
        return (
            "FAIL",
            f"{len(result.violations)} git call(s) of {result.git_calls} scanned with no "
            f"visible identity route: {listed} -- pass env= carrying "
            f"{', '.join(sorted(GIT_IDENTITY_KEYS))} (an ambient identity read from a "
            "developer's .git/config is present on a laptop and absent on a runner, so "
            "this fails only there)",
        )
    return (
        "PASS",
        f"{result.git_calls} git-prefixed spawn call(s) across {result.files} file(s), "
        "every one with a traceable identity route",
    )


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            "the directory of *.py files to scan directly (never a repo root that "
            "gets /scripts appended). Both sub-checks scan it. Default: scripts/ "
            "under cwd."
        ),
    )
    args = parser.parse_args(argv)
    root = args.root if args.root is not None else (Path.cwd() / "scripts")
    results = [
        ("subprocess-bounds", check_subprocess_bounds(root)),
        ("git-identity-env", check_git_identity_env(root)),
    ]
    for name, (status, detail) in results:
        print(f"[{status}] {name}: {detail}", flush=True)
    return 0 if all(status == "PASS" for _, (status, _) in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
