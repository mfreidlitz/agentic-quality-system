#!/usr/bin/env python3
"""Tests for `check_no_em_dash.py`.

This check DISCOVERS FROM THE FILESYSTEM, so its known-bad is a fixture
DIRECTORY fed through the real discovery path, never a hand-built set: a
pure-function test cannot notice a glob that matched nothing, and that
blindness is the whole point of the floor.

Pins, exemptions, and branch floors are all read from a per-repo config
file now (`<root>/.claude/em-dash-pins.json`), so most fixtures below
write that file into the temp tree rather than patching a module
constant.
"""

from __future__ import annotations

import contextlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_no_em_dash as ned  # noqa: E402

EM = chr(0x2014)

# A small, easy-to-pad set of floors used by the tests that specifically
# exercise the floor mechanism. Real repos choose their own.
STANDARD_FLOORS: dict[str, int] = {
    "docs/": 3,
    "scripts/": 2,
    "src/": 2,
    "root *.md": 1,
}


@contextlib.contextmanager
def _tree(files: dict[str, str]):
    """A real DIRECTORY, written to disk, read back through the real reader."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for rel, body in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        yield root


def _write_config(
    root: Path,
    *,
    pinned: dict[str, int] | None = None,
    exempt: dict[str, tuple[int, str]] | None = None,
    branch_floors: dict[str, int] | None = None,
    raw_text: str | None = None,
) -> None:
    """Write `<root>/.claude/em-dash-pins.json`.

    `raw_text`, when given, is written VERBATIM instead of building JSON
    from the keyword arguments, for the malformed-config tests.
    """
    config_path = root / ned.CONFIG_RELATIVE_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if raw_text is not None:
        config_path.write_text(raw_text, encoding="utf-8")
        return
    payload: dict[str, object] = {}
    if pinned is not None:
        payload["pinned"] = pinned
    if exempt is not None:
        payload["exempt"] = {k: list(v) for k, v in exempt.items()}
    if branch_floors is not None:
        payload["branch_floors"] = branch_floors
    config_path.write_text(json.dumps(payload), encoding="utf-8")


def _padding_all(floors: dict[str, int] = STANDARD_FLOORS) -> dict[str, str]:
    """Enough clean files in EVERY declared branch to clear `floors`.

    A padding built from only one branch is exactly why mutating the other
    branches away used to survive the suite: pad every branch, by name.
    """
    files: dict[str, str] = {}
    for i in range(floors["docs/"] + 2):
        files[f"docs/pad{i:03d}.md"] = "clean prose.\n"
    for i in range(floors["scripts/"] + 2):
        files[f"scripts/pad{i:03d}.py"] = "# clean\n"
    for i in range(floors["src/"] + 2):
        files[f"src/pad{i:03d}.rs"] = "// clean\n"
    files["CLAUDE.md"] = "clean prose.\n"
    return files


class KnownBadDirectoryTests(unittest.TestCase):
    """No config and no padding needed for most of these: a branch floor
    defaults to 0, so a bare fixture with one offending file is already a
    complete, minimal known-bad."""

    def test_a_fixture_file_with_one_em_dash_is_rejected_BY_NAME(self) -> None:
        files = {"docs/offender.md": f"a sentence {EM} with the character.\n"}
        with _tree(files) as root:
            code, lines = ned.check(root)
        self.assertEqual(code, 1)
        self.assertIn("docs/offender.md", "\n".join(lines))

    def test_a_clean_fixture_tree_passes(self) -> None:
        with _tree({"docs/clean.md": "clean prose.\n"}) as root:
            code, lines = ned.check(root)
        self.assertEqual(code, 0, "\n".join(lines))

    def test_an_out_of_scope_file_is_not_judged(self) -> None:
        # Scope is declared, so this documents it rather than assuming it. A
        # clean in-scope file rides along so the tree is not EMPTY discovery
        # (which now fails in its own right, the test below).
        files = {".state/notes.jsonl": f"{EM}\n", "docs/clean.md": "clean.\n"}
        with _tree(files) as root:
            code, _ = ned.check(root)
        self.assertEqual(code, 0)

    def test_EMPTY_DISCOVERY_fails_rather_than_passing_over_nothing(self) -> None:
        # RULE empty-discovery-fails, the transition review's catch: a tree
        # where every scope branch matches nothing must FAIL naming the globs,
        # never print a PASS over 0 in-scope files. Out-of-scope content only,
        # so every branch census is zero.
        files = {".state/notes.jsonl": "no em dash here either\n"}
        with _tree(files) as root:
            code, lines = ned.check(root)
        self.assertEqual(code, 1, "\n".join(lines))
        joined = "\n".join(lines)
        self.assertIn("ZERO in-scope files", joined)
        self.assertIn("docs/", joined)

    def test_a_source_file_under_src_IS_judged(self) -> None:
        files = {"src/views.rs": f'let s = "unavailable {EM} try later";\n'}
        with _tree(files) as root:
            code, lines = ned.check(root)
        self.assertEqual(code, 1)
        self.assertIn("views.rs", "\n".join(lines))


class FloorTests(unittest.TestCase):
    def test_no_config_means_no_floor_is_enforced(self) -> None:
        # The disclosed default: a tool installed once and pointed at many
        # repos cannot know any repo's population in advance, so a tree that
        # LACKS whole branches passes with no config. One in-scope file keeps
        # this distinct from total-empty discovery, which now fails in its own
        # right (KnownBadDirectoryTests' empty-discovery test).
        with _tree({"docs/only.md": "clean.\n"}) as root:
            code, lines = ned.check(root)
        self.assertEqual(code, 0, "\n".join(lines))

    def test_an_empty_BRANCH_fails_once_its_floor_is_configured(self) -> None:
        # Proven by forcing ONE branch empty against a CONFIGURED floor while
        # another branch is populated, which is the only arrangement where the
        # per-branch floor (rather than the total-empty refusal) does the work.
        files = {"docs/present.md": "clean.\n", "README.txt": "not in scope"}
        with _tree(files) as root:
            _write_config(root, branch_floors=STANDARD_FLOORS)
            code, lines = ned.check(root)
        self.assertEqual(code, 1)
        self.assertIn("below their configured floor", "\n".join(lines))

    def test_a_tree_one_file_under_the_floor_still_fails(self) -> None:
        thin = _padding_all()
        for k in [k for k in thin if k.startswith("scripts/")]:
            del thin[k]
        with _tree(thin) as root:
            _write_config(root, branch_floors=STANDARD_FLOORS)
            code, _ = ned.check(root)
        self.assertEqual(code, 1)


class RatchetTests(unittest.TestCase):
    """The config specifies a TRUE ratchet: a file UNDER its pin must also
    FAIL, so a cleaned file cannot silently regain headroom. `problems()`
    is pure, so these exercise it directly with hand-built dicts rather
    than through a config file."""

    def test_over_the_pin_fails(self) -> None:
        self.assertTrue(ned.problems({"docs/a.md": 3}, {"docs/a.md": 2}, {}))

    def test_under_the_pin_ALSO_fails_and_names_the_new_number(self) -> None:
        out = ned.problems({"docs/a.md": 1}, {"docs/a.md": 2}, {})
        self.assertTrue(out)
        self.assertIn("to 1", out[0])

    def test_exactly_at_the_pin_is_silent(self) -> None:
        self.assertEqual(ned.problems({"docs/a.md": 2}, {"docs/a.md": 2}, {}), [])

    def test_a_pin_over_a_now_clean_file_is_refused(self) -> None:
        out = ned.problems({}, {"docs/a.md": 2}, {})
        self.assertTrue(any("Delete its pin" in p for p in out))

    def test_an_exemption_pins_a_count_not_a_file(self) -> None:
        exempt = {"scripts/x.py": (1, "holds the character on purpose")}
        self.assertEqual(ned.problems({"scripts/x.py": 1}, {}, exempt), [])
        self.assertTrue(ned.problems({"scripts/x.py": 2}, {}, exempt))

    def test_an_exemption_BELOW_its_count_also_fails(self) -> None:
        # The direction that was unpinned: mutating `if n != allowed` to
        # `if n > allowed` SURVIVED, because only n==allowed and n>allowed
        # were ever exercised. "An exemption pins a COUNT" was unproven in
        # the half that makes it a ratchet rather than a ceiling.
        exempt = {"scripts/x.py": (2, "holds two on purpose")}
        self.assertTrue(ned.problems({"scripts/x.py": 1}, {}, exempt))

    def test_an_exempt_file_cleaned_to_zero_must_lower_its_entry(self) -> None:
        # The reverse loop iterated `pinned` only, so an exempt file could
        # be cleaned silently and then re-dirtied back up to its exemption.
        exempt = {"scripts/x.py": (1, "holds one on purpose")}
        out = ned.problems({}, {}, exempt, present={"scripts/x.py"})
        self.assertTrue(any("exempted for 1 and now carries none" in p for p in out), out)


class ScopeBranchTests(unittest.TestCase):
    """Every declared branch, asserted BY NAME with a literal path.

    A mutation dropping the `scripts/` prefix, or the root `*.md` branch,
    must be caught by name: dropping the root branch silently unguards
    `CLAUDE.md`, the file that states the rule this check exists to
    enforce.
    """

    def test_each_declared_branch_is_recognised(self) -> None:
        for path, branch in (
            ("docs/README.md", "docs/"),
            ("scripts/verify.py", "scripts/"),
            ("src/thing.rs", "src/"),
            ("CLAUDE.md", "root *.md"),
        ):
            with self.subTest(path=path):
                self.assertEqual(ned.branch_of(path), branch)
                self.assertTrue(ned.in_scope(path))

    def test_out_of_scope_paths_belong_to_no_branch(self) -> None:
        for path in (".state/loop-events.jsonl", "tests/thing.py", "NOTES.txt"):
            with self.subTest(path=path):
                self.assertIsNone(ned.branch_of(path))

    def test_CLAUDE_md_specifically_is_in_scope(self) -> None:
        # Named rather than covered by a generic case: it is the document
        # the rule lives in.
        self.assertTrue(ned.in_scope("CLAUDE.md"))

    def test_losing_one_branch_FAILS_even_though_the_total_stays_large(self) -> None:
        # The reason the floor is per branch. A tree rich in docs/ and
        # empty of scripts/ clears any total worth setting and must still
        # FAIL, once a floor is configured.
        files = {f"docs/pad{i}.md": "clean.\n" for i in range(80)}
        files["CLAUDE.md"] = "clean.\n"
        with _tree(files) as root:
            _write_config(root, branch_floors=STANDARD_FLOORS)
            code, lines = ned.check(root)
        self.assertEqual(code, 1)
        self.assertIn("scripts/", "\n".join(lines))

    def test_the_default_branch_floors_are_all_zero(self) -> None:
        # Both earlier floor tests were written relative to the constant
        # itself, so mutating it SURVIVED: they passed at any value.
        # Literals cannot do that.
        self.assertEqual(
            ned.DEFAULT_BRANCH_FLOORS,
            {"docs/": 0, "scripts/": 0, "src/": 0, "root *.md": 0},
        )


class HostileFilenameTests(unittest.TestCase):
    """Three ways a reviewer landed a U+2014 in a tracked in-scope file with
    the gate green, all in the discovery rather than the logic."""

    def test_a_path_with_a_space_is_read_whole(self) -> None:
        # `git ls-files` output was `.split()`, which fragmented the path
        # into phantom entries that failed to open and were swallowed,
        # while both fragments still counted toward the floor.
        files = {"docs/a note.md": f"prose {EM} here.\n"}
        with _tree(files) as root:
            code, lines = ned.check(root)
        self.assertEqual(code, 1, "\n".join(lines))
        self.assertIn("a note.md", "\n".join(lines))

    def test_a_non_ascii_path_is_read(self) -> None:
        files = {"docs/cafe-menu.md": f"prose {EM} here.\n"}
        with _tree(files) as root:
            code, lines = ned.check(root)
        self.assertEqual(code, 1, "\n".join(lines))

    def test_a_file_that_cannot_be_decoded_is_not_silently_skipped(self) -> None:
        # A KNOWN LIMIT rather than a fix: a UTF-16 body raises and the
        # file is dropped. Asserted so the limit is visible and so a later
        # change to it is deliberate.
        path_rel = "docs/utf16.md"
        with _tree({}) as root:
            (root / path_rel).parent.mkdir(parents=True, exist_ok=True)
            (root / path_rel).write_bytes(f"prose {EM} here.\n".encode("utf-16"))
            found = ned.scan(root)
        self.assertNotIn(path_rel, found, "if this now passes, update this test to match")


class ConfigLoadingTests(unittest.TestCase):
    """Pins, exemptions, and branch floors all come from
    `<root>/.claude/em-dash-pins.json`, read fresh for whichever root is
    given. No config file means the documented zero-allowance default."""

    def test_a_configured_pin_applies_to_this_root(self) -> None:
        files = {"docs/a.md": f"one {EM} here.\n"}
        with _tree(files) as root:
            _write_config(root, pinned={"docs/a.md": 1})
            code, lines = ned.check(root)
        self.assertEqual(code, 0, "\n".join(lines))

    def test_without_a_config_file_the_default_is_zero_allowance(self) -> None:
        files = {"docs/a.md": f"one {EM} here.\n"}
        with _tree(files) as root:
            code, lines = ned.check(root)
        self.assertEqual(code, 1, "\n".join(lines))
        self.assertIn("carries no pin", "\n".join(lines))

    def test_a_configured_exemption_applies_to_this_root(self) -> None:
        files = {"scripts/x.py": f"one {EM} on purpose.\n"}
        with _tree(files) as root:
            _write_config(root, exempt={"scripts/x.py": (1, "on purpose")})
            code, lines = ned.check(root)
        self.assertEqual(code, 0, "\n".join(lines))

    def test_an_explicit_config_path_overrides_the_default_location(self) -> None:
        # For judging a tree this tool must not write into: the pins live
        # somewhere else entirely, named explicitly rather than searched for.
        files = {"docs/a.md": f"one {EM} here.\n"}
        with _tree(files) as scanned_root, _tree({}) as config_root:
            external_config = config_root / "elsewhere.json"
            external_config.write_text(
                json.dumps({"pinned": {"docs/a.md": 1}}), encoding="utf-8"
            )
            code, lines = ned.check(scanned_root, config_path=external_config)
        self.assertEqual(code, 0, "\n".join(lines))

    def test_an_explicit_config_path_that_is_absent_is_the_zero_allowance_default(self) -> None:
        files = {"docs/a.md": f"one {EM} here.\n"}
        with _tree(files) as scanned_root, _tree({}) as config_root:
            code, lines = ned.check(
                scanned_root, config_path=config_root / "does-not-exist.json"
            )
        self.assertEqual(code, 1, "\n".join(lines))
        self.assertIn("carries no pin", "\n".join(lines))

    def test_a_malformed_config_is_a_loud_FAIL(self) -> None:
        with _tree({"docs/a.md": "clean.\n"}) as root:
            _write_config(root, raw_text="{not valid json")
            code, lines = ned.check(root)
        self.assertEqual(code, 1)
        self.assertIn("invalid JSON", "\n".join(lines))

    def test_an_unknown_branch_floor_key_is_rejected(self) -> None:
        with _tree({"docs/a.md": "clean.\n"}) as root:
            _write_config(root, branch_floors={"vendor/": 5})
            code, lines = ned.check(root)
        self.assertEqual(code, 1)
        self.assertIn("unknown branch", "\n".join(lines))

    def test_a_non_integer_pin_value_is_rejected(self) -> None:
        with _tree({"docs/a.md": "clean.\n"}) as root:
            _write_config(root, raw_text=json.dumps({"pinned": {"docs/a.md": "two"}}))
            code, lines = ned.check(root)
        self.assertEqual(code, 1)
        self.assertIn("integer count", "\n".join(lines))

    def test_load_config_directly_reports_the_absent_default(self) -> None:
        with _tree({}) as root:
            pinned, exempt, branch_floors = ned.load_config(root)
        self.assertEqual((pinned, exempt, branch_floors), ({}, {}, {}))


class SelfCheckTests(unittest.TestCase):
    def test_this_module_holds_none_of_what_it_hunts(self) -> None:
        # The global rule this check enforces applies to this file too: an
        # earlier version of an equivalent comment elsewhere claimed an
        # escape while the file held the literal character, which is why
        # this is a checked count rather than a trusted sentence.
        body = (Path(__file__).resolve().parent / "check_no_em_dash.py").read_text(
            encoding="utf-8"
        )
        self.assertEqual(body.count(EM), 0)

    def test_the_constructed_constant_is_the_real_character(self) -> None:
        self.assertEqual(ned.EM_DASH, EM)


if __name__ == "__main__":
    unittest.main()
