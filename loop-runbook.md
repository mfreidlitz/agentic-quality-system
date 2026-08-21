# Global loop runbook

> The repo-agnostic recipe an autonomous `/loop` session runs to pick up and close
> `owner: Code` work items unattended. Launch with `/loop` (self-paced, no interval)
> and this file plus the repo's adapter as the task.
>
> A repo may keep its own runbook (for example, `docs/autonomous-loop-runbook.md`).
> Where it does, THAT file wins on every specific and this one supplies the shape and
> the parts that are not the repo's to decide: the wave-close Kaizen round, the agent
> contracts, the guardrails. Where it does not, this file is the whole instruction and
> the adapter below is the only thing the repo has to supply.

---

## 1. The repo adapter

A repo is loop-ready when it can answer these seven lines. Put them in the repo's
`CLAUDE.md` (a `## Loop adapter` section) so the loop reads them where it already
looks. Every one of them is a **command or a path**, never a description: a runbook
that says "run the tests" has to be re-interpreted every iteration, and the
interpretation drifts.

| Slot | What it must be | Example |
|---|---|---|
| `verify` | ONE command proving the repo. Exit code AND a `^verify: (PASS\|FAIL)` line | `python scripts/verify.py` |
| `verify --quick` | The seconds-long subset the watch and the post-edit hook run | `python scripts/verify.py --quick` |
| `queue` | The pre-decided action list the loop consumes first | `docs/QUEUE.md` |
| `backlog` | The fallback item source, carrying a `## Kaizen measures` section at its TOP | `docs/BACKLOG.md` |
| `picker` | The script that turns the backlog into one deterministic pick | `scripts/pick_next.py` |
| `ledger` | Append-only JSONL of loop events | `.state/loop-events.jsonl` |
| `channel` | The two-way result channel; a slot that cannot be a command must SAY SO | none, or your channel: a chat webhook, an issue tracker, a paging tool. A push-only channel with no read command still satisfies this slot as long as incoming messages arrive some other way (as ordinary conversation turns, for instance); say so explicitly rather than leaving the row blank |

A repo missing the picker or the ledger can still run the loop: the queue file plus git
history is enough. A repo missing `verify` cannot, and the fix is to build `verify`
first. **The loop's entire safety model is that one command.**

---

## 2. State ownership

The source of truth is **local `main`**: `origin/main` plus the reviewed commits of the
current unpushed batch.

- The **session checkout** is a *synced reader*. Each iteration begins
  `git fetch origin && git merge --ff-only origin/main`. **Never `reset --hard
  origin/main`** - local `main` is ahead by the unpushed batch and the reset silently
  destroys it. `--ff-only` is a no-op when merely ahead and fails loudly on a genuine
  divergence, which is a `blocked`, never a force.
- Every **write** happens on a branch cut from **local `main`**, so item N starts from a
  base carrying 1..N-1's reviewed work. Integration is sequential at the local merge.
- A subagent MUST NOT inherit a base snapshot from when the batch was planned. It
  re-reads local `main` at dispatch.

---

## 3. Per-iteration algorithm

One item per turn, then stop and reschedule.

0. **Session start** (first turn, or after a resume or compaction). First tool call is
   the background watch: a **Monitor** running the repo's `verify --quick`. **Gate it on
   `git status --porcelain` being empty** so it reports on COMMITTED state only, and
   emit only on FAIL. An unguarded watch on a shared checkout fires on every mid-edit
   moment, and an alert indistinguishable from ordinary work is worse than no alert: it
   trains you to ignore the one independent signal you have. Then read the channel, post
   `loop started`, and append `loop_init` BEFORE the first item, or the budget has no
   anchor to measure from.

1. **Read the channel, then the stop check.** Read FIRST, before the pick. A message
   outranks the queue: a correction, a scope change, an answer, a stop. Reading here is
   the load-bearing part - reading at the report step means every correction arrives one
   full item late and a stop arrives after the work it was meant to prevent.

   End the loop ONLY if ANY of: the owner says stop; the time budget is spent (default
   12h from `loop_init`); the last 3 ledger events are all genuine `blocked` (a real
   breakage you could not fix, not a deliberate `shelved` judgment call); or the queue is
   genuinely empty on BOTH independent signals (every queue ID struck AND the picker
   returns `null` - two files, two checks, never conjoined).

   **This list is exhaustive.** Context size, session length and "this turn is getting
   long" are NOT stop reasons: the harness auto-compacts, `/clear` resumes losslessly,
   and all state is durable. "This item is big / would be cleaner with fresh context" is
   NEVER a reason to stop or defer - segment it (step 6) and keep going.

   **Every stop is a WAVE CLOSE, and a wave does not close until step 13 has run.**

2. **Sync.** `git fetch origin && git merge --ff-only origin/main`.

3. **Baseline.** Run full `verify`. If red, report `BLOCK: baseline red` and stop. Never
   build on a broken tree.

4. **Pick.** The queue file first, top to bottom, first unstruck ID whose dependencies
   are met. Only when it is fully struck, fall back to the picker.

   **The previous wave's `KZ-*` Kaizen measures come first in BOTH paths.** A new queue
   section OPENS with them, and the picker ranks the backlog's `## Kaizen measures`
   section above everything else. Measures are the one class of work that gets more
   expensive the longer it waits: everything built in the meantime is built under the
   defect the measure closes.

5. **Isolate and claim.** Branch `loop/<key>` off local `main`, in a worktree where disk
   allows. Append and commit the `claimed` ledger record inside it, so an aborted
   iteration leaves no claim on `main` and the item returns after its cooldown.

6. **Choose the slice.** For a pre-decided queue item the action IS the slice and the
   judgment is already made: implement it, do not re-litigate.

   **Large items SEGMENT, never bail.** One coherent verify-green segment per iteration,
   merged, ledger row keyed `<ID>-<segment>`, ID left unstruck until the last segment
   lands. A big item is a sequence of small merges.

   **The inverse also holds: closely related rows BATCH.** Rows from one file family or
   one mechanism family may ride a single build-plus-review chain, each row still getting
   its own strike and its own terminal ledger record under the picker's key scheme.
   Measured (one project's own record, wave over wave): batched rows cost ~4.5x less per
   row than singleton chains, because the review chain amortizes.

   **Deviation is allowed in exactly two cases**, both of which CONTINUE the loop: the
   item needs an owner-provisioned secret (`shelved` + reason + next), or implementing it
   reveals it is genuinely net-harmful (`shelved` + the file:line rationale + next).
   "Too big", "low value", "better fresh" are not deviations.

6b. **STAGE ONLY WHAT YOU TOUCHED.** Never `git add -A`, never `commit -a`. Name the
    paths. Run `git status` before staging and STOP if a path outside your scope is
    dirty: it is not yours. This binds at EVERY commit in the iteration, including the
    `claimed` row, and inside a worktree too. The loop routinely shares one checkout with
    the owner's parallel session; a sweep will as happily commit a half-finished edit or
    a pasted secret and attribute it to your segment, and it corrupts the review record
    both ways.

7. **Implement via TDD.** Failing test first. Stay inside the item's scope. Respect the
   repo's ADRs.

8. **Gate.** Run full `verify` and SHOW the output. Done = verify passes AND its output
   is shown. A completion claim without it is invalid. The verdict is the line matching
   `^verify: (PASS|FAIL)`, never `tail -1` and never a pipeline's exit status. Run
   foreground when the expected wall time fits the harness's command cap, go DETACHED
   (`nohup ... > <log> 2>&1 &` plus a poll on the verdict line) when it does not, and a
   run killed at the cap is relaunched, never read as a verdict.

9. **Self-review gate. NOT OPTIONAL, AND NOT SUBSTITUTABLE BY THE CADENCE REVIEW.**
   Verify green and no high-severity correctness finding, then merge. Otherwise push the
   branch, open a PR, record `ambiguous`, and move on.

   **The implementing agent MUST NOT push.** Review before merge is only possible while
   the work is unmerged, so a segment commits, reports its SHA, and stops; a SEPARATE
   reviewer sees the diff and only then does the orchestrator fast-forward. Record it as
   a `reviewed` ledger event keyed `<item-key>-review`, so "was it reviewed" is
   answerable from the ledger rather than from prose.

   **The record-review discipline** (a zero-transcription-defects practice from one
   project's own record): the reviewer WRITES its own report file at a path the DISPATCH
   names (the orchestrator chose the path, so it knows it; the return channel cannot
   carry one back), the orchestrator never transcribes a verdict, and the `reviewed` row
   is built by the repo's validating writer from that file, never composed by hand.

   **Verify-green is necessary and NOT sufficient.** It proves the toolchain is happy,
   never that a check the segment wrote checks anything. The standing hunt list:

   - Can this gate PASS or SKIP without checking? Force its inputs empty and look.
   - Does each new test FAIL when the behavior it claims to pin is broken? Mutate the
     SHIPPED value, assert the edit APPLIED, watch a named test redden. A test that
     survives mutation pins nothing.
   - Is every discovered set asserted non-empty, with a known member present?
   - **Does the guard stay SILENT on the ordinary inputs it will actually meet?** A guard
     that fires on routine work gets switched off exactly like one that never fires.
   - Does every "filed" claim carry an issue id that actually exists?
   - Is every quoted metric reproducible from a command in the transcript?
   - Does a gate SKIP in an environment that could have run it? That is a failure.
   - Does a durable record pin a LINE NUMBER in a file the same commit edits? Anchor to
     the SYMBOL instead. A symbol survives every edit that does not delete it.

9b. **Push cadence: batch, and push only AFTER a review and its corrections.** A segment
    merges LOCALLY and does not push. The push happens at the review boundary, so
    `origin` only ever receives work that passed both gates. Consequence that will
    silently corrupt the run if missed: **segments branch from LOCAL `main`**, because
    `origin/main` is deliberately stale by the whole batch.

    **The push itself goes through ONE gate-to-push command** where the repo provides one
    (`scripts/push_gate.py` as a pattern): the gates and `git push` inside one argv-list
    process, so no shell chain can sit between a gate and the push. The class this
    closes: `gate | head -1` laundered an exit 1 into a red push twice in one day.
    Verify runs LAST before the push, and where the repo mints a verify receipt binding
    the run to a tree, the pushed tip is proven against it.

10. **Merge and sync the specs.** Strike the completed action, update the matching spec
    level (requirements doc / roadmap / backlog) so the docs mirror the code, commit code
    and docs together, fast-forward into local `main`, and STOP THERE. If the boundary
    push is ever rejected non-fast-forward, do NOT force: fetch, rebase, re-verify, push,
    repeat.

11. **Report.** Read the channel again (a message may have landed while this item was
    building), append the terminal ledger row with the SHA, post the verdict.

11b. **CONFIRM CI BEFORE CLAIMING ANYTHING SHIPPED, AND IT IS A COMMAND.** After every
     push, resolve the run for the pushed TIP, wait for a conclusion under a bounded
     deadline, and treat anything but a genuine green as a BLOCK. Unknown is not green:
     "no run found" and "the CLI is absent or unauthenticated" both fail.

     **A local `verify` PASS is NOT evidence that a push shipped.** It runs on a machine
     whose ambient state the runner does not share: git identity, history depth,
     installed packages, `node_modules`, network. That class is undetectable locally by
     construction. `main` has stayed red for eighteen consecutive pushes while every
     segment truthfully reported a green verify and nobody looked.

     Reading API status costs no CI minutes. There is no cost argument against this.

12. **Clean up.** Remove the worktree, reschedule, stop.

---

## 4. Step 13: the wave-close KAIZEN round

**Runs once per wave, at whichever stop condition fired, and the wave is not closed
until it passes.** This is the only point in the loop where the loop changes itself.

**Why it is shaped this way.** Three consecutive waves ended with a written lesson.
Each lesson was already true when the next wave repeated it, with the file loaded in
context from the first turn. A lesson in a document has no route into the next wave's
queue unless someone with filing authority puts it there. The original answer was
self-filing queue rows; that regime ran many waves and grew a ceremony that cost more
than it returned, so the owner reset it. The current answer:

> **The deliverable is a set of PROPOSED rows in the close summary, each changing a
> named surface, ready for the owner to paste into the queue.** The owner alone files;
> a proposal he never sees has not been made, so the summary is where the round ends.

**a. HARVEST from commands, never from memory.** The ledger slice for the wave, the
elapsed time recomputed from the `loop_init` timestamp, the review and CI results, every
finding. Paste the transcript beside each figure. Recompute at the current tree state; a
"before" figure comes from the before-state, not from today minus your changes. A wave
summary carrying an inherited number is the defect being catalogued, committed in the act
of cataloguing it.

**b. CLASSIFY, then ask the one question that matters.** Give each defect a class. One
fitting no class is itself a finding and the taxonomy grows. Then, per defect: *which
forcing check would have caught this, and was it run?* Record "the check exists and I
skipped it" where that is true; it points at a different measure than "no check existed".

**c. CONVERT EACH LEARNING INTO AN ACTIVE MEASURE.** The step the round exists for, and
the one that will degrade into note-taking. A measure names a SURFACE and a MECHANISM:

| Surface | A measure here changes | Lands in |
|---|---|---|
| `loop` | the algorithm: a step, its ordering, a precondition, a command a step must run and quote | this file, the repo runbook, the queue file |
| `way-of-working` | a convention binding every change, not just loop work | `CLAUDE.md`, global or repo |
| `agent` | what a dispatched agent is TOLD: required inputs, required OUTPUT FIELDS, refusal conditions | section 5 below, the repo's dispatch playbook |
| `tooling` | a check, a gate, a floor, a known-bad fixture, a must-not-fire case | the repo's scripts and gate list |

**The test a measure has to pass: could someone build it without asking what you meant?**
"Be more careful with figures" fails. "The register check prints the caught-by histogram
and the round quotes it" passes. Name the file, command, flag or symbol in backticks.

Where a learning genuinely cannot be mechanized, say which class, why not, and what
partial rule would cover part of it. That is an honest entry. A note dressed as a measure
is not.

**Every surface is accounted for, every wave** - it carries a measure, or the section
carries an explicit `not this wave: <surface>` with a written reason. A wave that
discharges all its learning into `tooling` leaves the loop, the conventions and the
agents exactly as it found them, which is how three waves in a row inherited the defect
that produced them.

**d. PROPOSE, NEVER FILE.** Each measure goes into the close summary as a PROPOSED row
in the backlog section's own shape, ready to paste: `wave:` · `surface:` · `mechanism:`
· the lineage. **Only the owner files rows into a backlog or queue**; the next wave
opens on whatever he filed, and zero-measure closes are legal. (This reverses an earlier
file-directly step that stood through many waves; the self-filing Kaizen loop grew a
ceremony that cost more than it returned and was carved out at the owner's direction.)

**e. REPORT.** Post the wave summary with the proposed measures and the harvest figures,
append the `loop_closed` ledger row, then stop.

**Do not shorten this round because the budget ran out.** That is the highest-risk moment
in the loop and it has its own history: every defect of the worst wave was made under
pressure. If there is genuinely no runway, write the PROPOSED rows FIRST (step d is
load-bearing, the write-up is not) and say in the summary that the harvest was partial
and which part is missing.

---

## 5. Agents: what each is TOLD, and what it must RETURN

The `agent` Kaizen surface. These are contracts, not descriptions. A wave-close measure
on this surface edits this table, and the change binds from the next dispatch on.

Every agent gets: the working path, its item ID, this runbook, and the moment it is about
to stand at. Every agent returns a compact report. The fields below are REQUIRED; a
report missing one is incomplete, not terse.

**The contract every dispatch carries** (the surviving core of one project's own
agent-report contract): an agent's final text is DISCARDED and reaches nobody, so the
report is delivered by the forced tool call as the agent's FINAL action or it was not
delivered; an implementing agent never pushes its own unreviewed work; mutation testing
runs against a COPY (a scratch worktree), never the shared working tree, and the scratch
copy is removed whatever the verdict; every figure in a report comes from a command the
agent ran in that same session.

**The orchestration model** (low-token): the orchestrator dispatches each item's
implementation to a fresh subagent on a cheap model and keeps only a short summary per
iteration so its own context stays flat; reviews run on the strong model; folds run
sequentially into local `main`.

| Agent | Model | Told | MUST return | Refuses to |
|---|---|---|---|---|
| **Orchestrator** | Opus | the whole runbook; owns sequencing, the cadence review, the channel, and the wave close | per iteration a ~5-line summary; per wave, step 13's measures | close a wave without step 13; push unreviewed work |
| **Implementing subagent** | Sonnet | one item, the playbook, "you MUST NOT push" | branch + SHA, the `verify` verdict, and for any claim that something is pinned: the SHIPPED symbol mutated, that the edit APPLIED, and the test that reddened BY NAME | push its own work; `git add -A`; claim a pin it did not mutate; write an issue id it did not file |
| **Reviewer (per item)** | Opus | the diff, the step 9 hunt list, "you did not write this" | a severity per finding, and per check the diff adds: what happened with its inputs forced empty AND when it met an ordinary input it must stay silent on | pass a diff on a reading; treat verify-green as evidence |
| **Cadence reviewer** | Opus | the range since the last review; drift and cross-segment pattern | the `reviewed` ledger row naming the tip, plus the corrections it demands | substitute for the per-item gate |

**Why the "must return" column is where measures usually land.** An agent instructed to
*be careful* behaves identically to one that was not. An agent that must return the name
of a test that reddened has to run something to answer. Every defect that has been caught
at all was caught by an agent RUNNING something, never by one recalling a rule. A measure
that changes what an agent must return is worth more than one that changes what it is
warned about.

---

## 6. Guardrails

- Only unblocked `owner: Code` work, no owner-provisioned secret required.
- Verify green is a hard precondition for commit and merge, and is NOT sufficient for
  merge on its own. Step 9 closes that gap.
- The per-item review runs before the push, by an agent that did not write the code. The
  cadence review does not substitute for it.
- Circuit breaker: 3 consecutive genuine blocks ends the loop.
- Time budget from `loop_init`; empty queue ends the loop.
- One item per turn; never a half-merge to `main`.
- Fresh base per unit of work, cut from LOCAL `main`. Never `reset --hard origin/main`.
  A non-fast-forward push is reconciled by fetch-rebase-verify-push, never by force.
- **Every stop runs step 13 before `ScheduleWakeup stop`.**

---

## 7. Adopting this in a new repo

1. Build `verify` first: one command, exit code plus a `^verify: (PASS|FAIL)` line naming
   the mode that produced it. Everything else depends on it.
2. Add the `## Loop adapter` table (section 1) to the repo's `CLAUDE.md`.
3. Add a `## Kaizen measures` section at the TOP of the backlog, with the surface table,
   and teach the picker to read it first and rank it above everything.
4. Create the queue file with the wave's pre-decided actions.
5. Launch `/loop`, self-paced, with the repo runbook (or this file) as the task.

Steps 1 and 3 are the ones that matter. A repo can run this loop with no picker and no
ledger; it cannot run it without `verify`, and without step 3 the owner's filed measures
have no section the picker ranks first, so a filed measure stops being a scheduled one.
