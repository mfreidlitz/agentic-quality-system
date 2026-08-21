# CLAUDE.md (example)

This is an example project `CLAUDE.md` for the agentic quality system: a loop
adapter table plus a working-rules section, in the shape a real project's own
`CLAUDE.md` would carry them. Copy the parts you want, fill in your own
commands and paths, delete the rest.

## Loop adapter

The seven slots the runbook (`loop-runbook.md`, section 1) asks a loop-ready
repo to answer. Each is a command or a path, never a description: a runbook
step that says "run the tests" gets re-interpreted every iteration and the
interpretation drifts.

| Slot | This repo |
|---|---|
| `verify` | `python scripts/verify.py` |
| `verify --quick` | `python scripts/verify.py --quick` |
| `queue` | `docs/QUEUE.md` |
| `backlog` | `docs/BACKLOG.md` (its `## Kaizen measures` section is the top of the queue) |
| `picker` | `scripts/pick_next.py` |
| `ledger` | `.state/loop-events.jsonl` |
| `channel` | none, or your channel: a chat webhook, an issue tracker, a paging tool. If it cannot be read back programmatically, say so explicitly rather than leaving the row blank; a slot that cannot be a command must still say so |

## Working rules

These ten rules are distilled from an autonomous-loop project's record across
many waves of real work. Each rule is generic: where a mechanism exists it is
named, otherwise `convention`. The incident in `why` is what earned the rule
its place; a rule that stops earning its place gets deleted.

```
RULE edit-what-you-read: Never write to a tracked source file with `cat >>`,
`cat >`, a heredoc, or scripted string surgery on remembered offsets; use an
editing tool that refuses a stale match.
  enforced_by: convention (the editing tools' stale-match refusal)
  why: the single highest-frequency defect source measured across many waves
    of work; most recently a scripted offset splice duplicated 359 lines and
    only a character-count pin caught it
```

```
RULE no-hard-counts: A figure a command can produce MUST come from the command;
prose, docstrings and tool output MUST NOT carry a maintained total. An
aggregate that exists only as a summary is dropped, not kept current.
  enforced_by: convention
  why: a hand-typed test census went stale twice in two consecutive commits of
    one review chain, each time green under its own pin
```

```
RULE moving-reference-sha: Any number produced by a command run against HEAD, a
branch name, or "now" is quoted with the resolved sha it was measured at, in
the same sentence, or not quoted at all.
  enforced_by: convention
  why: two true observations minutes apart read as a contradiction until the
    sha ends the dispute in one line; separately, a runtime figure cited at a
    commit that did not yet exist entered a rules file and was caught only by
    an adversarial round re-deriving the timestamps
```

```
RULE no-writes-under-read: While any verify or build reads the tree, every
writer (you included) HOLDS writes to tracked paths; state files are committed
before a dispatched build or detached verify starts.
  enforced_by: convention
  why: two incidents in one wave: an agent stashed another writer's uncommitted
    rows to fake a clean tree, and mid-run doc edits voided a full verify as
    three torn-read gate failures
```

```
RULE bounded-waits: Every wait is bounded: every subprocess call and urlopen
passes an explicit timeout; every GitHub Actions job sets timeout-minutes;
every git subprocess in automation passes explicit GIT_AUTHOR_*/GIT_COMMITTER_*
plus GIT_TERMINAL_PROMPT=0.
  enforced_by: `scripts/check_hermetic_bounds.py` from this repo, where
    adopted, for the subprocess-bounds and git-identity clauses; the Actions
    timeout-minutes clause is convention (a repo may enforce it in its own
    CI-parity tooling)
  why: a hang is silence until someone reads the bill; and a git identity
    present in a developer's .git/config and absent on a runner turned a repo's
    main red for eighteen consecutive pushes under truthful local greens
```

```
RULE empty-discovery-fails: A check that reads nothing must not report clean:
discover inputs before judging them and FAIL on empty discovery. Its test suite
carries a FLOOR (non-empty, a named known member, a numeric minimum over a
population that only grows) and a KNOWN-BAD the check rejects, in the form its
signature allows: FILESYSTEM-DISCOVERY checks get a fixture DIRECTORY fed
through the real discovery path (never a hand-built set, since a pure-function
test cannot notice a glob that matched nothing); HANDED-INPUT checks get a
crafted input plus a forced-empty stand-in for whatever discovers upstream.
Prove both halves by forcing discovery empty and watching the suite redden; a
floor that reddens nothing is decoration.
  enforced_by: convention (each check's own suite)
  why: a glob that stopped matching, a moved directory, and a parser a release
    outgrew all look exactly like a clean tree from the outside
```

```
RULE no-state-pins: A test asserting about a real, living dataset binds to a
population that only grows (union archives with the live file) or to a member
the fixture itself appends (inserted into the real text, section-bounded and
last-in-section, never an unbounded insert); a floor over the live set's FILL
STATE reddens exactly when the work succeeds.
  enforced_by: convention
  why: five boundaries of one family inside a single wave of work, four in one
    day, each firing the first time a queue drained or rotated
```

```
RULE definition-of-done: Done means the repo's verify passes AND its output is
shown; "it should work" is never done. Tests are modified or removed only with
a stated reason, and test count and coverage never drop silently.
  enforced_by: the post-edit and Stop hooks where a repo provides verify;
    convention elsewhere
  why: a completion claim without command output is invalid; verification is
    a command with output, never a confidence claim
```

```
RULE ci-only-finding-blocks: A finding naming a CI-only failure class (an
environment dependence local verification cannot detect by construction) is
BLOCKING whatever severity label it carries, and is repaired before push.
  enforced_by: convention (a repo may provide a parity runner)
  why: exactly such a finding was labeled non-blocking once, carried, and
    turned main red on a green local verify
```

```
RULE verify-output-contract: A consumer of a verify harness's output treats the
line matching `^verify: (PASS|FAIL)` as the verdict, never the last line of
output and never a pipeline's exit status.
  enforced_by: convention (the repo's verify defines the line)
  why: `--claim | head -1` laundered an exit 1 into a red push, and a census
    block appended below a verdict was once read as the verdict via tail -1
```

Each rule carries the same two fields for the same reason: `enforced_by` says
whether anything actually checks the rule or whether it rests on convention
alone, and `why` names the incident that earned the rule its place so the
rule can be argued with rather than taken on faith. A rule that stops earning
its place, because the mechanism it depended on changed or the incident class
stopped recurring, gets retired rather than kept as decoration; a backlog of
rules nobody re-derives a reason for is worse than no rules at all.
