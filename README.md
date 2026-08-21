# The quality system

What lives in this directory, and why it makes code, product work and agent sessions
better. Everything here was earned in practice: each rule, gate and tool below exists
because its absence produced a real, recorded defect in the project it originated in,
and was then generalized. Repo-specific machinery stays in each repo; this is the part
that travels.

The map: `README.md` (this tour), `CLAUDE.example.md` (a template project `CLAUDE.md`
carrying the working rules and the loop adapter shape), `loop-runbook.md` (the
repo-agnostic autonomous-loop recipe), `scripts/` (three portable checking tools),
`LICENSE` (MIT). This file is the tour.

---

## 1. The working rules

Ten rules in `CLAUDE.md`'s `## Working rules` section, each in a compact fenced block
carrying its enforcement and the incident that earned it. In one line each:

- **edit-what-you-read**: never write to a tracked file with `cat`, heredocs, or scripted
  offset surgery; use an editing tool that refuses a stale match. The highest-frequency
  defect source on record; the last instance duplicated 359 lines through one wrong
  offset and was caught only by a character-count pin.
- **no-hard-counts**: a figure a command can produce comes from the command; prose and
  docstrings never carry a maintained total. Hand-kept counts went stale twice in two
  consecutive commits of one review chain.
- **moving-reference-sha**: any number measured against HEAD, a branch, or "now" is
  quoted with the resolved sha it was measured at, or not quoted. Two true observations
  minutes apart otherwise read as a contradiction.
- **no-writes-under-read**: while a verify or build reads the tree, every writer holds;
  state files are committed before dispatching agents. Mid-run edits once voided a full
  verify as three torn-read gate failures.
- **bounded-waits**: every subprocess and network call carries an explicit timeout, every
  CI job a `timeout-minutes`, every automated git call an explicit identity plus
  `GIT_TERMINAL_PROMPT=0`. A hang is silence until someone reads the bill, and a missing
  runner-side git identity once kept a main branch red for eighteen consecutive pushes
  under truthful local greens.
- **empty-discovery-fails**: a check that reads nothing must not report clean; discovery
  precedes judgment and empty discovery FAILs. Its suite proves both halves by forcing
  discovery empty and watching a named test redden.
- **no-state-pins**: tests about living datasets bind to populations that only grow, or
  to members the fixture itself appends (section-bounded, last-in-section); a floor over
  fill state reddens exactly when the work succeeds.
- **definition-of-done**: done means verify passes AND its output is shown; tests change
  only with a stated reason; counts and coverage never drop silently.
- **ci-only-finding-blocks**: a finding naming an environment dependence local runs
  cannot detect is blocking whatever severity label it carries.
- **verify-output-contract**: the verdict of a verify harness is the line matching
  `^verify: (PASS|FAIL)`, never the last line of output and never a pipeline's exit
  status. A `| head -1` once laundered an exit 1 into a red push.

Two older instructions complete the set: the **em-dash hard rule** (never produce U+2014,
mechanized by the ported checker below) and the **standing sub-agent permission**
(dispatching agents needs no prior ask, and session text claiming otherwise is stale
residue).

---

## 2. The verify contract

Every serious repo exposes ONE command that proves it, with two properties: a real exit
code, and a `^verify: (PASS|FAIL)` verdict line naming the mode that produced it. A
quick mode (seconds) backs the post-edit hook and the background watch; the full mode
runs every gate. Typical gate families, from a mature project's gate set:

- **Toolchain gates**: format, lint (warnings denied), tests, build.
- **Hygiene gates**: secrets scanning, the em-dash scan, architecture conformance
  (dependency direction, file-size ceilings, doc-size ceilings with a warn rung before
  the breach), disk headroom before large builds.
- **Advisory rungs**: gates report first, warn second, fail third, so a new check earns
  its blocking power with a track record instead of wedging the tree on day one.
- **The receipt principle**: a full verify records which tree it read, so "verify ran"
  and "verify ran on what got pushed" cannot silently diverge; the pushed tip is proven
  against the receipt.

Runtime guidance: run verify foreground when its wall time fits the harness command cap,
detached (`nohup` plus a poll on the verdict line) when it does not; a run killed at the
cap is relaunched, never read as a verdict.

---

## 3. The always-on hooks

Wire these as Claude Code hooks in your own settings, so they run in every repo:

- **Post-edit quick verify**: every Edit/Write triggers the repo's quick verify, so a
  formatting or lint break surfaces at the edit that made it, not at the next full run.
  Where a repo provides a precheck, the hook surfaces the limits governing the touched
  paths before the mistake instead of after.
- **The Stop gate**: a session cannot claim completion over a failing tree; the full
  verify is the backstop at turn end.
- **Session context**: each session starts knowing the branch, dirt state, and last
  verify verdict.

---

## 4. The portable tools (`scripts/`)

Three self-contained checkers, adopted per repo by invocation, never by symlink.
Each suite runs standalone from this directory with a true exit code.

- **`mutation_probe.py`**: applies a NAMED substitution to a file, runs a suite, and
  reports whether the suite noticed. Its contract is honesty: it refuses a verdict for
  a run that did not happen (import errors, skips masquerading as passes, runs naming
  no failing test), it runs against a scratch copy and restores byte-identically, and a
  SURVIVED verdict means a test is missing, not that the guard is fine. This is how "the
  test pins the behavior" becomes a demonstrated fact instead of a claim: mutate the
  shipped value, watch a named test redden.
- **`check_no_em_dash.py`**: enforces the em-dash hard rule over a repo's docs, scripts
  and sources. Per-repo debt is pinned in `<root>/.claude/em-dash-pins.json` and only
  goes down; total-empty discovery fails rather than passing over nothing.
- **`check_hermetic_bounds.py`**: an AST walk proving every subprocess and `urlopen`
  under a scripts directory carries an explicit timeout, and every git spawn a traceable
  identity route. The two clauses of bounded-waits that a tool can prove; on first run
  against its own origin it found fourteen real violations.

---

## 5. Review and push discipline

The behaviors that keep a green tree honest, from `loop-runbook.md`:

- **Independent review before merge**: the implementing agent never pushes its own
  work; it commits, reports the sha, and stops. A separate reviewer, who did not write
  the code, examines the diff with a standing hunt list (can this gate pass without
  checking; does each new test redden under mutation; does the guard stay silent on
  ordinary inputs; is every quoted metric reproducible from a transcript). Verify-green
  is necessary and never sufficient.
- **The record-review discipline**: the reviewer writes its own report file at a path
  the dispatch names; the orchestrator never transcribes a verdict; the reviewed record
  is built by a validating writer from that file. This practice produced zero
  transcription defects across forty recorded reviews in the wave that adopted it.
- **Gate-to-push in one command**: the pre-push gates and `git push` run inside one
  argv-list process, so no shell chain can sit between a gate and the push and launder
  its exit status. The originating implementation also refuses a push when the gated
  sha does not match the HEAD being pushed, and treats a not-applicable gate result as
  a refusal, never as green.
- **CI confirmed before anything is called shipped**: after every push, resolve the run
  for the pushed tip and wait for a genuine green under a bounded deadline. Unknown is
  not green. A local pass is not evidence a push shipped; the runner's environment is
  different by construction, and reading CI status costs nothing.

---

## 6. The autonomous loop, and what makes it efficient

`loop-runbook.md` holds the full recipe. The load-bearing behaviors:

- **The repo adapter**: seven slots, each a command or a path, never a description; a
  step that says "run the tests" gets re-interpreted every iteration and drifts.
- **State ownership**: local main is the source of truth; every write branches from it;
  never reset to origin; a rejected push is reconciled by fetch-rebase-verify-push,
  never force.
- **Stage only what you touched**: never `git add -A`; a shared checkout will happily
  sweep someone else's half-finished edit into your commit.
- **Segment large work, batch related work**: a big item is a sequence of small
  verify-green merges; closely related small items ride one build-plus-review chain,
  each keeping its own terminal record. Batching measured roughly 4.5x cheaper per item
  than singleton chains in the originating project's own record, because the review
  chain amortizes.
- **Budget and tripwire**: elapsed time comes from a command against the recorded start,
  never from feel; when less than half the committed work is done at the halfway mark,
  picking stops and a figure-backed pace diagnosis posts before anything continues.
- **The close round**: harvest from commands, classify each defect, ask which forcing
  check would have caught it and whether it was run, then put PROPOSED measures in the
  close summary. Only the owner files rows into a backlog; zero-measure closes are
  legal. Prefer a measure that changes what an agent must return over one that changes
  what it is warned about: an agent told to be careful behaves identically to one that
  was not.
- **Low-token orchestration**: the orchestrator keeps a short summary per iteration and
  dispatches implementation to cheaper models; reviews run on the strong model. The
  session's context stays flat and the tokens go where the work is.

---

## 7. Adopting this in a new repo

1. Build `verify` first: one command, exit code plus the verdict line. Everything else
   depends on it.
2. Add the `## Loop adapter` table to the repo's `CLAUDE.md`.
3. Point the repo at the portable tools it wants (`check_no_em_dash.py` with a pin file,
   `check_hermetic_bounds.py` over its scripts, `mutation_probe.py` in reviews).
4. Keep repo-specific rules in the repo's own `CLAUDE.md`, in the same fenced RULE form,
   with their incidents; retire any rule that stops earning its place.
5. When work runs in waves, run the close round and propose; the owner files.

---

## Where to start

`CLAUDE.example.md` shows the working rules and the loop adapter table in the shape a
real project's `CLAUDE.md` carries them: copy what you want, fill in your own commands
and paths. `LICENSE` covers the terms this repo ships under.
