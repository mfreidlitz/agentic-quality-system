#!/usr/bin/env python3
"""No U+2014 in tracked text.

**A hard rule that only ever appears in a document is a rule that is
violated in the one place it matters.** This tool is the mechanism a repo
points at itself so the rule stops being prose alone.

**A TRUE RATCHET, not a ceiling.** Each entry a repo's config names for a
path pins an EXACT count. A file over its pin FAILs, and a file UNDER its
pin also FAILs, naming the new number to write down. So the debt can only
go down, and a file that gets cleaned cannot silently regain headroom to
be dirtied again later.

**Exemptions are granted to an OCCURRENCE with a count and a reason, never
to a file.** A blanket file exemption is indistinguishable from an
unexamined file: a config entry names exactly how many U+2014 an
occurrence is allowed to hold and why, and the ratchet applies to that
count the same way it applies to a pin.

**Per-repo config, never in-file constants.** What used to be hardcoded
`PINNED`/`EXEMPT`/`BRANCH_FLOORS` tables now live in a JSON file at
`<root>/.claude/em-dash-pins.json`, read fresh for whichever `--root` is
given. Absent is a legitimate, expected default: zero allowance (nothing
pinned, nothing exempt, no branch floor enforced) for a repo that has
never needed to record any debt. Schema, every key optional:

    {
      "pinned": {"docs/README.md": 3},
      "exempt": {"scripts/thing.py": [1, "why this one occurrence is allowed"]},
      "branch_floors": {"docs/": 5, "scripts/": 2, "src/": 10, "root *.md": 1}
    }

`pinned` maps a tracked path to the exact U+2014 count it may hold.
`exempt` maps a tracked path to `[count, reason]`, the same ratchet as
`pinned` but stated with its reason inline. `branch_floors` maps a scope
branch name to the minimum number of in-scope files that branch must
discover; a branch not named here defaults to 0, meaning no floor is
enforced for it. A config file that exists but does not parse, or does
not match this shape, is a loud `[FAIL]`, never a silent fallback to the
same defaults: a fallback there would hide a typo in the one file meant
to record real debt.

**Scope, generic and fixed rather than configurable.** Tracked text under
`docs/`, `scripts/`, `src/`, and the root `*.md` files. Everything else is
out of scope by declaration rather than by oversight, and `--root`
retargets all of it so the known-bad can be a real fixture DIRECTORY fed
through the real discovery.

**Disclosed narrowing: a branch floor defaults to 0, not to some measured
population.** The original, single-repo shape of this check set each
branch's floor to a number safely under its own real population, so a
branch whose glob stopped matching was loud by default. A tool installed
once and pointed at many different repos cannot know any repo's
population in advance, and a nonzero default would FAIL by default on any
repo that has, say, no `src/` directory at all. So the floor for every
branch defaults to 0 (no protection) until a repo's own
`branch_floors` config states what it actually expects; the
empty-discovery protection this check exists to provide is then exactly
as strong as the config a repo writes for itself.

FLOOR: the discovered branch census still gates on whatever floors ARE
configured. A glob that has stopped matching reports clean in exactly the
way a clean tree does, which is why a repo that cares about this writes a
floor down once and lets the ratchet defend it.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path

# The same identity discipline applies even though `tracked_files` only reads.
# Every git subprocess here carries an explicit identity and
# GIT_TERMINAL_PROMPT=0 rather than inheriting a developer's config, which
# is present on a laptop and absent on a runner.
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "check-no-em-dash",
    "GIT_AUTHOR_EMAIL": "check-no-em-dash@check-no-em-dash.invalid",
    "GIT_COMMITTER_NAME": "check-no-em-dash",
    "GIT_COMMITTER_EMAIL": "check-no-em-dash@check-no-em-dash.invalid",
    "GIT_TERMINAL_PROMPT": "0",
    "PATH": __import__("os").environ.get("PATH", ""),
}

EM_DASH = chr(0x2014)  # constructed, so this module never contains what it hunts

# Where a repo's own pins/exemptions/floors live, relative to `--root`.
CONFIG_RELATIVE_PATH = Path(".claude") / "em-dash-pins.json"

# The generic default scope branches (see module docstring). Every branch
# name here is also the only valid key in a config's `branch_floors`; an
# unknown key there is a loud config error rather than a silently ignored
# typo.
DEFAULT_BRANCH_FLOORS: dict[str, int] = {
    "docs/": 0,
    "scripts/": 0,
    "src/": 0,
    "root *.md": 0,
}


def branch_of(path: str) -> str | None:
    """Which declared scope branch `path` belongs to, or None if out of scope.

    Named branches rather than a boolean, so a floor can be asserted per
    branch and a branch that stops matching cannot hide inside a total.
    """
    if path.startswith("docs/"):
        return "docs/"
    if path.startswith("scripts/"):
        return "scripts/"
    if path.startswith("src/"):
        return "src/"
    if "/" not in path and path.endswith(".md"):
        return "root *.md"
    return None


def in_scope(path: str) -> bool:
    return branch_of(path) is not None


def tracked_files(root: Path) -> list[str]:
    """Every tracked path under `root`, or every file when it is not a repo.

    A fixture directory is not a git repo, so the known-bad can still be a
    real DIRECTORY fed through this same discovery rather than a hand-built
    set: a check that discovers from the filesystem needs its known-bad to
    exercise that same discovery, or a glob that quietly stopped matching
    would be invisible to it.
    """
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        text=True,
        timeout=60,
        env=_GIT_ENV,
    )
    if proc.returncode == 0 and proc.stdout.strip(chr(0)).strip():
        # NUL-separated, never whitespace-split: `docs/a b.md` fragmented into
        # two phantom entries that failed to open and were swallowed, while
        # both fragments still counted toward the floor. `-z` also stops git
        # quoting non-ASCII paths, which defeated every scope branch.
        return [p for p in proc.stdout.split(chr(0)) if p]
    return sorted(
        str(p.relative_to(root)).replace("\\", "/")
        for p in root.rglob("*")
        if p.is_file()
    )


def scan(root: Path) -> dict[str, int]:
    """`{path: count}` for every in-scope file carrying at least one U+2014."""
    out: dict[str, int] = {}
    for rel in tracked_files(root):
        if not in_scope(rel):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError, OSError):
            continue
        n = text.count(EM_DASH)
        if n:
            out[rel] = n
    return out


def branch_census(root: Path) -> dict[str, int]:
    """How many in-scope files each declared branch discovered.

    Per branch rather than one total, because a total cannot see a whole
    branch disappear while the others stay large.
    """
    out: dict[str, int] = {}
    for rel in tracked_files(root):
        branch = branch_of(rel)
        if branch is not None:
            out[branch] = out.get(branch, 0) + 1
    return out


def problems(
    found: dict[str, int],
    pinned: dict[str, int],
    exempt: dict[str, tuple[int, str]],
    present: set[str] | None = None,
) -> list[str]:
    """Every complaint about `found`, given the pins and the exemptions.

    `present` names the in-scope files that EXIST under the tree being
    judged. Without it the "you cleaned this, lower the pin" rule cannot
    tell a file that was cleaned from one that is simply not in this tree,
    and it would fire on every pin whenever the check ran against a
    fixture root that does not carry the pinned repo's own files.
    """
    out: list[str] = []
    for path, n in sorted(found.items()):
        if path in exempt:
            allowed, reason = exempt[path]
            if n != allowed:
                out.append(
                    f"{path}: {n} U+2014, exempted for exactly {allowed} ({reason}). An "
                    f"exemption pins a COUNT; it is not a licence for the file."
                )
            continue
        cap = pinned.get(path)
        if cap is None:
            out.append(
                f"{path}: {n} U+2014, and this file carries no pin. The default is zero "
                f"allowance. Use a comma, colon, semicolon, parentheses or a sentence break, "
                f"or record a pin in {CONFIG_RELATIVE_PATH} if the debt is staying for now."
            )
        elif n > cap:
            out.append(f"{path}: {n} U+2014, up from its pinned {cap}. The debt only goes down.")
        elif n < cap:
            out.append(
                f"{path}: {n} U+2014, down from its pinned {cap}. Lower the pin in "
                f"{CONFIG_RELATIVE_PATH} to {n} so the file cannot silently regain headroom. "
                f"This is a RATCHET, so cleaning without recording is refused."
            )
    for path, cap in sorted(pinned.items()):
        if present is not None and path not in present:
            continue
        if path not in found and cap:
            out.append(
                f"{path}: pinned at {cap} and now carries none. Delete its pin; a pin over a "
                f"clean file is headroom nobody meant to grant."
            )
    # EXEMPTIONS RATCHET TOO. Iterating `pinned` alone would let an exempt
    # file be cleaned to zero silently and then re-dirtied anywhere back up
    # to its exemption, which contradicts the rule that a cleaned file
    # cannot regain headroom.
    for path, (allowed, _reason) in sorted(exempt.items()):
        if present is not None and path not in present:
            continue
        if path not in found and allowed:
            out.append(
                f"{path}: exempted for {allowed} and now carries none. Lower the exemption "
                f"to 0 or delete it; the ratchet applies to exemptions too."
            )
    return out


def load_config(
    root: Path, config_path: Path | None = None
) -> tuple[dict[str, int], dict[str, tuple[int, str]], dict[str, int]]:
    """`(pinned, exempt, branch_floors)` read from `config_path`, or from
    `<root>/.claude/em-dash-pins.json` when `config_path` is not given.

    `config_path` exists for the case a config cannot live under the
    scanned tree at all: judging a read-only tree (or one this tool must
    not write into) with pins recorded elsewhere. It is an explicit
    override, never a search path, so a caller who names one gets exactly
    that file or a loud error, never a silent fall-through to the default
    location.

    Absent file (in either mode): all three empty, which is the documented
    zero-allowance default. A file that exists but is malformed raises
    `ValueError` with a message naming the path and the problem, which
    `check()` turns into a `[FAIL]` line rather than letting it crash the
    process or silently reverting to the same defaults a typo would then
    hide behind.
    """
    path = config_path if config_path is not None else (root / CONFIG_RELATIVE_PATH)
    if not path.is_file():
        return {}, {}, {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: must be a JSON object at the top level, got {type(raw).__name__}")

    pinned_raw = raw.get("pinned", {})
    if not isinstance(pinned_raw, dict) or not all(
        isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
        for k, v in pinned_raw.items()
    ):
        raise ValueError(f"{path}: 'pinned' must be an object mapping path to integer count")
    pinned: dict[str, int] = dict(pinned_raw)

    exempt_raw = raw.get("exempt", {})
    if not isinstance(exempt_raw, dict) or not all(
        isinstance(k, str)
        and isinstance(v, list)
        and len(v) == 2
        and isinstance(v[0], int)
        and not isinstance(v[0], bool)
        and isinstance(v[1], str)
        for k, v in exempt_raw.items()
    ):
        raise ValueError(
            f"{path}: 'exempt' must be an object mapping path to a [count, reason] pair"
        )
    exempt: dict[str, tuple[int, str]] = {k: (v[0], v[1]) for k, v in exempt_raw.items()}

    floors_raw = raw.get("branch_floors", {})
    if not isinstance(floors_raw, dict) or not all(
        isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
        for k, v in floors_raw.items()
    ):
        raise ValueError(f"{path}: 'branch_floors' must be an object mapping branch to integer")
    unknown = set(floors_raw) - set(DEFAULT_BRANCH_FLOORS)
    if unknown:
        raise ValueError(
            f"{path}: 'branch_floors' names unknown branch(es) {sorted(unknown)}; known "
            f"branches are {sorted(DEFAULT_BRANCH_FLOORS)}"
        )
    branch_floors: dict[str, int] = dict(floors_raw)

    return pinned, exempt, branch_floors


def check(root: Path | None = None, config_path: Path | None = None) -> tuple[int, list[str]]:
    """Read at CALL time, never bound as a default argument: `root=None`
    resolves `Path.cwd()` here rather than at import time, so a caller
    that changed directory after import still gets the right answer.

    `config_path`, when given, overrides where the pins/exempt/floors
    config is read from (see `load_config`); the default remains
    `<root>/.claude/em-dash-pins.json`.
    """
    if root is None:
        root = Path.cwd()

    try:
        pinned, exempt, configured_floors = load_config(root, config_path)
    except ValueError as exc:
        return 1, [f"[FAIL] em-dash: {exc}"]
    branch_floors = {**DEFAULT_BRANCH_FLOORS, **configured_floors}

    census = branch_census(root)
    if sum(census.values()) == 0:
        # RULE empty-discovery-fails: a check that reads nothing must not
        # report clean. Per-branch zero floors stay legal (a repo may lack any
        # one branch), but EVERY branch matching nothing means the scan read
        # nothing at all: a wrong --root, an untracked tree, or globs a layout
        # outgrew, each indistinguishable from a clean repo behind a PASS line.
        # A transition review once caught the ported tool passing over
        # zero files while the rule it ships beside forbids exactly that.
        return 1, [
            f"[FAIL] em-dash: every scope branch matched ZERO in-scope files under "
            f"{root} (branches: {', '.join(sorted(branch_floors))}). A scan that read "
            f"nothing is not a clean tree; check --root, that the tree is a git "
            f"checkout with tracked files, and the branch globs"
        ]
    thin = [
        f"{branch} {census.get(branch, 0)}/{floor}"
        for branch, floor in branch_floors.items()
        if census.get(branch, 0) < floor
    ]
    if thin:
        return 1, [
            f"[FAIL] em-dash: scope branch(es) below their configured floor: {', '.join(thin)}. "
            f"A branch that stops matching reports clean exactly the way a clean tree does, "
            f"and an AGGREGATE floor cannot see it. Census: {dict(sorted(census.items()))}."
        ]

    found = scan(root)
    present = {rel for rel in tracked_files(root) if in_scope(rel)}
    bad = problems(found, pinned, exempt, present)
    if bad:
        return 1, ["[FAIL] em-dash: " + bad[0], *[f"    {b}" for b in bad[1:]]]
    total = sum(found.values())
    return 0, [
        f"[PASS] em-dash: {sum(census.values())} in-scope file(s) "
        f"({', '.join(f'{b} {n}' for b, n in sorted(census.items()))}); "
        f"{total} pinned occurrence(s) across {len(found)} file(s), none over its pin"
    ]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="repo root to scan; reads its .claude/em-dash-pins.json. Default: cwd.",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "explicit path to the pins/exempt/branch_floors JSON config, overriding "
            "<root>/.claude/em-dash-pins.json. For judging a tree this tool must not "
            "write into, or a config that cannot live inside the scanned tree at all."
        ),
    )
    args = p.parse_args()
    code, lines = check(args.root, args.config)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
