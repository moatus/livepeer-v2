# lp2-core

The code for a working testnet of the October 2026 Livepeer 2.0 litepaper:
the rules it is built against, the contracts, the independent calculator, the
programs that run beside the node, and the one-command demo.

This repository is written and reviewed by AI agents, directed by its
owner. This file is the guide for any agent working here, the project's own
or a contributor's: the principles the work is held to, the rules for this
code, and the commands. `README.md` says what the project does and how to
run it. `SEAMS.md` names the places where something important crosses.

**The project's own agents** also work from a private repository,
`lp2-research`, which holds the roadmap, the specs and the guide to how a
job is run. When it is on this machine (`./dev doctor` says where), read its
`AGENTS.md` before starting: it governs how a job is run, and this file
governs what goes into this repository. Without it, this file is complete
on its own.

## Principles

- **Authority flows one way.** The litepaper leads, then the project's
  recorded decisions, then the rulebook and the shared examples, then the
  code. A lower layer never silently redefines a higher one.
- **A rule gap is never settled in code.** A question the litepaper and the
  rulebook leave open is raised and decided. The answer is written into the
  rulebook with a worked example, as a change of its own, and the code
  follows.
- **Seams live in the code.** The places where something important crosses
  are named in `SEAMS.md`. Each one is a single location with a mechanism
  that enforces it, so whether a change touches one is a fact read from git
  and the compiler, never an opinion. A change that needs a new seam, or a
  second door through an existing one, says so before it is built.
- **Fast checks always, heavy checks at acceptance.** `./dev verify` runs on
  every change and takes seconds. Long fuzz and invariant runs and the
  other slow searches for rare defects run through `./dev qualify` when a
  stage closes, never per change; static analysis that takes seconds may
  also run on every change. A check that did not run is reported as
  `not_run`, never as passed.
- **Evidence is what ran.** A stage is accepted on what the demo shows,
  reconciled against the independent calculator. Files existing and tests
  passing are not acceptance. Every report separates what was observed, what
  failed, what was not run and what is still assumed.
- **A check is never weakened to pass.** A failing check is fixed in the
  change, or the change does not land. A change to a check is a change of
  its own.
- **Nothing carries forward except the rules and the checks.** Each testnet
  version is deployed fresh. Until a public testnet exists, no stored state,
  address or format needs to survive a change: replace it outright.
- **Each job runs in its own space.** Its own local chain, ports, data
  directory and throwaway keys, so parallel jobs never share state.
- **Secrets and keys.** Never read, print or commit `.env` files,
  credentials, cookies or tokens. Test keys are generated for one job and
  thrown away. A key that has ever held real funds never enters this
  repository or any job.
- **Nothing private enters this repository.** No internal server names or
  addresses, no machine paths, no content of a private repository, no copy
  of someone else's document, no unpublished research text, no keys. This
  holds for every file, commit message and test fixture. The litepaper is
  cited by page and a spec by its ID. Neither is copied.

## How a change is made

Every change takes the same path, at a size that fits it.

1. **The rules come first.** What the code must do is written into the
   rulebook, with worked numbers in the shared examples, as a change of its
   own before any code that implements it.
2. **An agent builds** in its own worktree, against the frozen rules, on
   its own local chain.
3. **It is a pull request.** Every change, whatever its size, is a pull
   request into `main`, and its description is the change summary
   `./dev summary` prints. The host runs `./dev verify` on it.
4. **A different agent reviews.** A change meant to stay is read by a model
   from a different family than the one that built it, with the change
   report `./dev review` generates from git and `SEAMS.md`. Each round of
   the review is posted on the pull request. A real defect it finds is
   closed by a check, so it cannot come back unnoticed.
5. **It lands by fast-forward** once the host's check has passed and, for
   a change that is reviewed, the reviewer has approved that same commit.
   `main` takes no direct push, so the commits on it are exactly the ones
   that were checked.
6. **The owner reads afterwards.** A change to a seam that `SEAMS.md` marks
   `owner` lands as its own commit and is reported with the commit before
   it, so there is always a point to go back to.

A stage closes on the demo's output, reconciled against the independent
calculator, after the heavy checks have run on that commit.

## The change summary

Every change is described by one summary, in one layout, printed by
`./dev summary`. It is the description of the change's pull request. It
fits on a screen, and the detail behind each line opens on a click. It
describes the change. It never tells a reader what to do.

- **The table** is generated: the seams touched, with each sensitive seam
  marked (one whose Review column in `SEAMS.md` says `owner`, such as money,
  authority and the contract interfaces); whether a rule or a contract
  interface changed; what was verified; what was not run; the review.
- **Verified** is a list of claims, each with the evidence that ran for it.
  A claim with no evidence is not in the list.
- **Not run** is every check that exists and did not run on this commit.
- **Design choices and assumptions** is what the checks cannot settle: a
  choice the change makes, or something it takes on trust. The checks can
  show that money moved as specified; they cannot show that the
  specification is the right one. Each item is one line that states a fact
  about the change, with its detail folded below.
- **Review** is one line: how many independent reviews the change had, and
  what they found and had fixed before it landed.

The seams, doors and checks in it come from git, `SEAMS.md` and a fresh run,
so anyone can generate them again from the commit, and a small change
needs nothing more. For a change that was reviewed, the rest is written by
whoever built and reviewed it, in a file passed with `--written`:
a `# ` title, a lead sentence, and the sections `What this changes`,
`Verified`, `Design choices and assumptions` and `Review`. The first plain
line of `Verified` joins its row of the table.

## What is here

| Path | Holds |
|---|---|
| `dev`, `tools/` | The checks, the change report and the change summary |
| `SEAMS.md` | The seam map the checks and the report read |
| `rulebook/` | The rules, one entry per rule, each citing the litepaper page |
| `vectors/` | The shared examples: inputs and the exact numbers they must produce |
| `calculator/` | The independent integer calculator |
| `contracts/` | Foundry project: `interfaces/`, `src/money/`, `src/authority/`, `src/math/`, tests |
| `programs/` | Helper programs that run beside the node |
| `demo/` | The one-command demo |
| `deployments/` | One manifest per testnet version |
| `nodefork/` | The ledger of every change carried against the upstream node. It arrives with the first such change |

A folder is not created empty. It arrives with its first real file, and
`SEAMS.md` already says which seam it belongs to.

## Working agreement

For work that changes code, or a document someone will build from.

- Make the smallest coherent change that delivers the asked-for result, and
  finish it before widening.
- Get one working path through the real thing first: a local chain, the real
  contracts, the real node where the stage needs it. Harden only what that
  path showed to matter.
- Verifiable means a relevant wrong behaviour would be caught by a check
  someone will run. It does not mean every uncertainty is removed.
- Build no test harness, evidence system, verifier or procedure unless the
  task asks for one. Say which cheaper check you used instead.
- Reuse what exists before adding, and delete what has stopped earning its
  cost. When a correction changes the facts behind a number or a default,
  re-derive the dependent value in the same job.
- Stop when the behaviour works, and report plainly.

## Rules for this code

- **The rulebook is what the code is tested against.** A contract or
  calculator change that needs a rule to mean something new changes the
  rulebook and the shared examples first, on its own.
- **The calculator is independent.** Whoever writes or changes
  `calculator/` works from `rulebook/` and `vectors/` only and is never
  shown contract code. Whoever writes the contracts is never shown the
  calculator. Two implementations that agree are evidence only while
  neither was copied from the other. The brief for either job says so.
- **Money and authority go through their doors.** Minting, and holding or
  moving tokens, happen in `contracts/src/money/`. Authority is checked in
  `contracts/src/authority/`, again at the moment of execution. A change
  that needs a second door says so before it is built.
- **Reward maths is pure.** It is written as functions the compiler forbids
  from reading or changing state, and takes everything it needs as
  arguments.
- **Integer arithmetic only**, with the rounding direction stated in the
  rulebook entry and shown in a shared example.
- **A stand-in is named.** Anything simplified for the testnet (a price
  source, a token, a bridge) is listed in the deployment manifest as a
  stand-in, never left to be discovered.
- **Each component checks itself.** A folder that holds code has an
  executable `check` (fast, seconds) and, where it has slow checks, a
  `qualify`; a folder with none is named, with the reason, in `NO_QUALIFY`
  in `tools/dev.py`, and `./dev qualify` shows it as `n/a`. `./dev` finds
  and runs them. Exit 0 passed, 1 failed, 2 could not run.

## Commands

| Command | Does | Exit |
|---|---|---|
| `./dev doctor` | Says what this machine has and lacks, and holds each tool to the version `tools/versions` pins | 0 ok; 1 a fix is needed |
| `./dev verify` | The fast checks, on every change: every file belongs to a seam, the door rules, and each component's `check` | 0 passed; 1 failed; 2 `not_run` |
| `./dev review [--base <ref>] [--checks]` | Prints the change report a reviewer reads, generated from git and `SEAMS.md`. Runs the structure checks only; `--checks` adds each component's `check` | 0 printed; 64 usage |
| `./dev summary [--base <ref>] [--checks] [--written <file>]` | Prints the change summary. Runs the structure checks only; `--checks` adds each component's `check`; `--written` adds the written half | 0 printed; 64 usage |
| `./dev qualify` | The heavy checks, when a stage closes (and on the host every night): `verify` plus each component's `qualify` | 0 passed; 1 failed; 2 `not_run` |
| `./dev demo` | The one-command demo | The demo's own; 2 while it is not built |

## Commits

Every change lands through a pull request into `main`. The host runs
`./dev verify` on it (`.github/workflows/verify.yml`), and `main` accepts
the merge only when that check has passed on the commit being merged.
`main` only moves forward: a merge is a fast-forward, and it is never
pushed to directly or force-pushed.

A commit message says what changed and why, and ends with these trailers
where they apply. Together they are the public record of how the change
was made.

- `Built-by: <provider>/<model>` names what actually wrote it.
- `Lane: <word>` names the route it took: `direct` for a small change with
  an obvious result, `reviewed` for a change read by a second agent before
  it landed.
- `Spec: <ID>@<commit>` when the change implements a spec. Specs are kept
  in the private repository, and the ID is the reference.
- `Council: <D-xx>` when the change writes a ruling into the rulebook or
  the shared examples. Where the litepaper is silent or can be read two
  ways, a council of agents from different model families rules on what is
  built, and the owner keeps or changes each ruling later.
- `Needs-owner: <one sentence>` for each decision in the change that the
  owner should read, and, for any change to an owner-routed seam, one
  sentence saying what changes for a participant. The change report lists
  these under "For you to read".

The main repository is hosted privately, and the public copy receives its
history from there. Nothing is committed on the public copy directly. A
pull request opened there is brought to the main repository, reviewed and
landed like any other change, and appears with the next push.

## Safety

- Test keys are generated inside a job and never committed. Well-known
  local chain development keys are the one exception, and only in files
  that say what they are.
- Nothing here talks to a public network or a hosted service without an
  instruction from the owner.
