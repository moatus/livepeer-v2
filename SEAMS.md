# Seams

A seam is a place where something important crosses: a rule becomes code,
money moves, the node meets the contracts, authority is exercised. This file
names each one, says where it lives, and says what makes it hold.

The aim is that nobody has to judge whether a change touches something
important. Each seam is one location, so "was it touched" is answered by git.
Where possible the compiler or the chain refuses to let it be crossed
anywhere else.

`./dev verify` and `./dev review` read the two tables between the markers
below. They are the single source: the report a reviewer reads is generated
from them. Keep the table shape, and do not put a `|` inside a cell.

## The seam map

A file belongs to the seam with the longest matching path. Every file in the
repository must belong to one, so a new top-level folder cannot appear without
a row here.

- **Review** says who must look when the seam changes. `owner` means the
  change report is routed to the owner. `council` means the change proceeds
  on a council ruling that its commits name (`Council: D-03`) and the owner
  reviews the ruling later; with no ruling named, or no record of it, the
  change is the owner's. `agent` means the agent review is enough.
- **Freeze** marks the seams that define what is being built. A change to one
  is a freeze change: it is made on its own, before the code that implements
  it, and it is where the owner or the council decides.
- **Status** is `in force` when the location exists or is settled, and
  `proposed` when it is a starting position that the named spec's freeze will
  confirm or change.

<!-- BEGIN SEAM MAP -->
| Seam | What crosses it | Paths | Review | Freeze | Status |
|---|---|---|---|---|---|
| rules | The rules we build against, and the numbers they must produce | `rulebook/` `vectors/` | council | yes | in force |
| interfaces | The public shape of every contract | `contracts/interfaces/` | owner | yes | proposed until the S-03 freeze |
| money | Minting LPT, and holding or moving USDC | `contracts/src/money/` | owner | no | proposed until the S-03 freeze |
| authority | Who may act for a node, and who may change a parameter | `contracts/src/authority/` | owner | no | proposed until the S-03 freeze |
| maths | Reward calculation inside the contracts | `contracts/src/math/` | agent | no | proposed until the S-05 freeze |
| node | Where the node meets the contracts, and every change we carry against upstream | `nodefork/` | owner | no | in force |
| deployment | What one testnet version contains | `deployments/` | owner | no | in force |
| factory | The checks, this map and the guides agents work from | `dev` `tools/` `SEAMS.md` `AGENTS.md` `CLAUDE.md` `.t3code/` `.gitignore` | owner | no | in force |
| contracts | Contract code and tests outside the seams above | `contracts/` | agent | no | in force |
| calculator | The independent calculator | `calculator/` | agent | no | in force |
| programs | Helper programs that run beside the node | `programs/` | agent | no | in force |
| demo | The one-command demo | `demo/` | agent | no | in force |
| readme | The public description of the project | `README.md` | agent | no | in force |
<!-- END SEAM MAP -->

## Doors

A door rule says that a kind of action may appear in one place only. Each row
is a text search over the files under **Within**. A match outside **Only in**
fails `./dev verify`.

<!-- BEGIN ONLY-HERE RULES -->
| Rule | Pattern | Within | Only in | Why |
|---|---|---|---|---|
| one-mint-path | `[._ ]mint\(` | `contracts/src/` | `contracts/src/money/` | There is one path that mints LPT |
| one-funds-door | `[tT]ransfer(From)?\(` | `contracts/src/` | `contracts/src/money/` | Tokens are held and moved in one place |
<!-- END ONLY-HERE RULES -->

These searches are deliberately crude. They catch a second door being opened
by accident, on every change, in milliseconds. They do not prove the door
itself is correct.

## What makes each seam hold

| Seam | Held by | Strength |
|---|---|---|
| rules | Every example in `vectors/` is run by both the calculator and the contract tests. A rule change without an example change shows in the change report. | Checked on every change, once the calculator and contracts exist |
| interfaces | Each contract's public functions, events and errors are declared in one folder. The change report lists every declaration line added or removed there. Once the contracts exist, `contracts/check` compares the compiler's ABI of each contract against its declared interface, so a public function added to an implementation cannot go unlisted. | Read from the source today; from the compiler once the contracts exist |
| money | The token accepts mint calls from one address fixed at deployment. One contract holds the USDC. The door rules above. | The chain refuses, once the contracts exist. The door rules run today. |
| authority | Authority is checked again at the moment of execution, in one place. | Review against the money-path checklist |
| maths | Reward maths is written as functions the compiler forbids from reading or changing state, so the calculation cannot mint or pay. | The compiler refuses |
| node | The node reads the chain through one narrow interface. Our changes sit in their own directories of the fork, and every changed upstream file has a ledger entry in `nodefork/`. | A check fails when an upstream file differs without an entry, once the fork exists |
| deployment | One manifest per testnet version lists the rulebook version, addresses, node commit and every stand-in in use. | A file, read by people |
| factory | A change to a check, to this map or to a guide is routed to the owner, so a check cannot be weakened quietly. | Read from git on every change |

"Once the contracts exist" is literal. Today the repository holds the factory
and nothing else. The door rules and the coverage rule run now and are shown
to fail on a seeded violation by `tools/check`. The compiler and chain
mechanisms arrive with the code they protect, and each one is shown to fail
once when it does.

## What a seam cannot tell you

- **Whether the change behind it is right.** A payout through the correct
  door with the wrong amount looks fine here. The shared examples catch that,
  and the agent review covers the rest.
- **Whether a new seam should exist.** A new public function appears in the
  change report automatically. Whether it belongs is the owner's call.
- **Anything about a seam nobody declared.** The design has to keep the
  number of doors small. Adding a row here is an owner decision.

## Changing this file

A change to either table is routed to the owner. When a spec's freeze settles
a `proposed` row, the same change updates the row's paths and sets its status
to `in force`.
