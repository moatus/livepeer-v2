# lp2-core

A working testnet of the October 2026 Livepeer 2.0 litepaper, built in thin
end-to-end slices.

This is an independent build for learning what the paper's rules do when
they run. It is not an official Livepeer release, and nothing here approves
a rule, a parameter or a mechanism for the Livepeer protocol. Where the
paper is silent or unclear, the choice we build against is recorded in the
rulebook and labelled as ours.

## Status

Nothing of the protocol is built yet. The repository currently holds the
checks and guides the code will be built with.

| Part | State |
|---|---|
| Rulebook and shared examples | Not started |
| Independent calculator | Not started |
| Contracts | Not started |
| Node integration | Not started |
| One-command demo | Not started |

## What you can run today

```sh
./dev doctor    # what this machine has and lacks
./dev verify    # the fast checks
./dev review    # the change report for the current work
```

`./dev demo` will run the whole path on a local chain once the first slice
exists. Today it says it is not built.

## How the code is arranged

- `SEAMS.md` names the places where something important crosses (rules,
  contract interfaces, money, authority, the node) and what enforces each.
- `AGENTS.md` is the guide for agents working here.

Requirements: `git` and Python 3 for the checks. Foundry and Go are needed
once the contracts and programs exist.
