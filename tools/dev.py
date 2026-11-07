#!/usr/bin/env python3
"""./dev: the one front door for lp2-core. The commands are listed in
AGENTS.md.

Standard library only. Everything this file knows about seams comes from the
two tables in SEAMS.md.

Exit codes: 0 passed, 1 failed, 2 something did not run, 64 usage.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# A folder that holds code checks itself: `<folder>/check` is the fast check
# and `<folder>/qualify` the heavy one. `./dev` only finds and runs them.
CODE_DIRS = ("tools", "calculator", "contracts", "programs", "demo")
QUALIFY_DIRS = ("calculator", "contracts", "programs")

PASSED, FAILED, NOT_RUN, NA = "passed", "failed", "not_run", "n/a"


def git(*args: str, check: bool = True) -> str:
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and done.returncode != 0:
        sys.exit(f"dev: git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


# --- the seam map -----------------------------------------------------------


def table(name: str, text: str | None = None) -> list[dict[str, str]]:
    if text is None:
        text = (ROOT / "SEAMS.md").read_text()
    found = re.search(rf"<!-- BEGIN {name} -->\n(.*?)<!-- END {name} -->", text, re.S)
    if not found:
        sys.exit(f"dev: SEAMS.md has no {name} table")
    lines = [line for line in found.group(1).splitlines() if line.strip().startswith("|")]
    cells = [[c.strip() for c in line.strip().strip("|").split("|")] for line in lines]
    head = [h.lower() for h in cells[0]]
    rows = []
    for row in cells[2:]:
        if len(row) != len(head):
            sys.exit(f"dev: SEAMS.md {name}: a row has {len(row)} cells, expected {len(head)}")
        rows.append(dict(zip(head, row)))
    return rows


def ticks(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


def seams(text: str | None = None) -> list[dict]:
    rows = table("SEAM MAP", text)
    for row in rows:
        row["patterns"] = ticks(row["paths"])
        if row["review"] not in ("owner", "agent") or row["freeze"] not in ("yes", "no"):
            sys.exit(f"dev: SEAMS.md seam '{row['seam']}': review must be owner or agent, freeze yes or no")
        if not row["patterns"]:
            sys.exit(f"dev: SEAMS.md seam '{row['seam']}' names no path")
    return rows


def seams_at(commit: str) -> list[dict]:
    """The seam map as it was at a commit, so a change cannot route itself."""
    text = git("show", f"{commit}:SEAMS.md", check=False)
    return seams(text) if text.strip() else seams()


def seam_of(path: str, rows: list[dict]) -> dict | None:
    """The seam with the longest matching path wins."""
    best, best_len = None, -1
    for row in rows:
        for pattern in row["patterns"]:
            if pattern.endswith("/"):
                hit = path.startswith(pattern)
            else:
                hit = fnmatch.fnmatchcase(path, pattern)
            if hit and len(pattern) > best_len:
                best, best_len = row, len(pattern)
    return best


def files() -> list[str]:
    """Tracked files plus new ones not ignored, that exist on disk."""
    out = git("ls-files", "-co", "--exclude-standard", "-z")
    return sorted({f for f in out.split("\0") if f and (ROOT / f).is_file()})


# --- checks -----------------------------------------------------------------


def research_dir() -> Path | None:
    named = os.environ.get("LP2_RESEARCH")
    if named:
        return Path(named)
    candidates = [ROOT.parent / "lp2-research"]
    link = ROOT / ".agents" / "roster.md"
    if link.exists():  # a worktree reaches the workspace through its links
        candidates.append(link.resolve().parent.parent / "lp2-research")
    for candidate in candidates:
        if (candidate / "AGENTS.md").is_file():
            return candidate
    return None


def check_cover(paths: list[str], rows: list[dict]) -> tuple[str, str, list[str]]:
    name = "every file belongs to a seam"
    lost = [p for p in paths if seam_of(p, rows) is None]
    if lost:
        shown = [f"no seam owns {p}" for p in lost[:10]]
        if len(lost) > 10:
            shown.append(f"and {len(lost) - 10} more")
        shown.append("add a row to SEAMS.md (routed to the owner) or move the file")
        return name, FAILED, shown
    return name, PASSED, []


def door_hits(paths: list[str]) -> list[dict]:
    """For each door rule: matches inside its allowed place, and outside it."""
    results = []
    for rule in table("ONLY-HERE RULES"):
        pattern = re.compile(ticks(rule["pattern"])[0])
        within, allowed = ticks(rule["within"])[0], ticks(rule["only in"])[0]
        inside, outside = 0, []
        for path in paths:
            if not path.startswith(within):
                continue
            text = (ROOT / path).read_text(errors="ignore")
            for number, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    if path.startswith(allowed):
                        inside += 1
                    else:
                        outside.append(f"{path}:{number}")
        results.append({"rule": rule["rule"], "allowed": allowed, "why": rule["why"], "inside": inside, "outside": outside})
    return results


def check_doors(paths: list[str]) -> list[tuple[str, str, list[str]]]:
    out = []
    for hit in door_hits(paths):
        name = f"door rule {hit['rule']}"
        if hit["outside"]:
            detail = [f"{where} is outside {hit['allowed']}" for where in hit["outside"][:10]]
            detail.append(f"{hit['why']}. Move it behind the door, or propose a change to SEAMS.md.")
            out.append((name, FAILED, detail))
        else:
            out.append((name, PASSED, []))
    return out


def check_components(kind: str) -> list[tuple[str, str, list[str]]]:
    wanted = CODE_DIRS if kind == "check" else QUALIFY_DIRS
    out = []
    for folder in wanted:
        name = f"{folder}/{kind}"
        base = ROOT / folder
        if not base.is_dir() or not any(base.iterdir()):
            out.append((name, NA, ["nothing built here yet"]))
            continue
        if folder == "tools" and os.environ.get("LP2_DEV_SELFTEST"):
            out.append((name, NA, ["skipped inside the self-test"]))
            continue
        script = base / kind
        if not script.is_file():
            out.append((name, NOT_RUN, [f"{folder}/ holds code but has no {kind} script"]))
            continue
        if not os.access(script, os.X_OK):
            out.append((name, FAILED, [f"{name} is not executable"]))
            continue
        started = time.time()
        done = subprocess.run([str(script)], cwd=base, capture_output=True, text=True)
        took = f"{time.time() - started:.1f}s"
        if done.returncode == 0:
            out.append((name, PASSED, [took]))
        elif done.returncode == 2:
            out.append((name, NOT_RUN, (done.stdout + done.stderr).strip().splitlines()[-15:]))
        else:
            out.append((name, FAILED, (done.stdout + done.stderr).strip().splitlines()[-40:]))
    return out


def run_verify(components: bool = True) -> list[tuple[str, str, list[str]]]:
    """The structure checks, then each component's own check."""
    paths, rows = files(), seams()
    results = [check_cover(paths, rows), *check_doors(paths)]
    if components:
        results += check_components("check")
    return results


def code_for(results: list[tuple[str, str, list[str]]]) -> int:
    states = {state for _, state, _ in results}
    return 1 if FAILED in states else 2 if NOT_RUN in states else 0


def show(results: list[tuple[str, str, list[str]]]) -> None:
    for name, state, detail in results:
        print(f"{state:<8} {name}")
        if state != PASSED or name.endswith(("/check", "/qualify")):
            for line in detail:
                print(f"         {line}")


# --- commands ---------------------------------------------------------------


def cmd_verify(_args) -> int:
    results = run_verify()
    show(results)
    code = code_for(results)
    print({0: "verify: passed", 1: "verify: FAILED", 2: "verify: not_run (a check did not run)"}[code])
    return code


def qualify_record() -> Path:
    return ROOT / ".cache" / "dev" / "qualify.json"


def cmd_qualify(_args) -> int:
    results = run_verify() + check_components("qualify")
    show(results)
    code = code_for(results)
    clean = not git("status", "--porcelain").strip()
    if clean:
        record = qualify_record()
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({
            "commit": git("rev-parse", "HEAD").strip(),
            "results": [[name, state] for name, state, _ in results],
        }, indent=1))
    else:
        print("qualify: the working tree has uncommitted changes, so no record was kept")
    if code == 0 and all(state == NA for name, state, _ in results if name.endswith("/qualify")):
        print("qualify: not_run (no component has heavy checks yet)")
        return 2
    print({0: "qualify: passed", 1: "qualify: FAILED", 2: "qualify: not_run (a check did not run)"}[code])
    return code


def cmd_demo(args) -> int:
    script = ROOT / "demo" / "run"
    if not script.is_file():
        print("demo: not built yet (roadmap stage 2)")
        return 2
    return subprocess.run([str(script), *args.rest], cwd=ROOT / "demo").returncode


def cmd_doctor(_args) -> int:
    def version(command: list[str]) -> str | None:
        if not shutil.which(command[0]):
            return None
        done = subprocess.run(command, capture_output=True, text=True)
        text = (done.stdout or done.stderr).strip()
        return text.splitlines()[0] if text else "present"

    needs = [
        ("git", ["git", "--version"], True, "install git"),
        ("python3", ["python3", "--version"], True, "install Python 3"),
        ("forge", ["forge", "--version"], (ROOT / "contracts").is_dir(), "install Foundry: https://getfoundry.sh"),
        ("anvil", ["anvil", "--version"], (ROOT / "contracts").is_dir(), "comes with Foundry"),
        ("go", ["go", "version"], (ROOT / "programs").is_dir(), "install Go: https://go.dev/dl"),
    ]
    bad = False
    for name, command, needed_now, fix in needs:
        found = version(command)
        if found:
            print(f"ok       {name}: {found}")
        elif needed_now:
            bad = True
            print(f"MISSING  {name}: needed now. {fix}")
        else:
            print(f"absent   {name}: not needed until its code exists. {fix}")
    research = research_dir()
    print(f"{'ok      ' if research else 'absent  '} lp2-research: {research or 'not beside this checkout (set LP2_RESEARCH)'}")
    roster = ROOT / ".agents" / "roster.md"
    print(f"{'ok      ' if roster.exists() else 'absent  '} roster: .agents/roster.md")
    print("doctor: a fix is needed" if bad else "doctor: ok")
    return 1 if bad else 0


def cmd_review(args) -> int:
    if not git("rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}", check=False).strip():
        print(f"review: no commit named {args.base}", file=sys.stderr)
        return 64
    head = git("rev-parse", "HEAD").strip()
    base = git("merge-base", args.base, "HEAD").strip()
    dirty = bool(git("status", "--porcelain").strip())
    rows = seams()
    base_rows = seams_at(base)

    # What changed: committed since the base, plus anything not yet committed.
    counts: dict[str, list[int]] = {}
    for line in git("diff", "--numstat", "--no-renames", base).splitlines():
        added, removed, path = line.split("\t", 2)
        counts[path] = [0 if added == "-" else int(added), 0 if removed == "-" else int(removed)]
    status = {}
    for line in git("diff", "--name-status", "--no-renames", base).splitlines():
        letter, path = line.split("\t", 1)
        status[path] = {"A": "new", "D": "removed"}.get(letter, "changed")
    for path in git("ls-files", "-o", "--exclude-standard", "-z").split("\0"):
        if path and (ROOT / path).is_file():
            counts[path] = [len((ROOT / path).read_text(errors="ignore").splitlines()), 0]
            status[path] = "new"

    messages = git("log", "--format=%B%x01", f"{base}..HEAD")
    rules = sorted(set(re.findall(r"\b[RD]-\d{2}\b", messages)))
    specs = sorted(set(re.findall(r"^Spec:\s*(.+)$", messages, re.M)))
    asks = re.findall(r"^Needs-owner:\s*(.+)$", messages, re.M)

    by_seam: dict[str, list[str]] = {}
    unowned = []
    for path in sorted(counts):
        seam = seam_of(path, rows)
        if seam is None:
            unowned.append(path)
        else:
            by_seam.setdefault(seam["seam"], []).append(path)
    touched = [row for row in rows if row["seam"] in by_seam]
    frozen = [row["seam"] for row in touched if row["freeze"] == "yes"]
    owned = [row["seam"] for row in touched if row["review"] == "owner"]
    other = [row["seam"] for row in touched if row["freeze"] == "no" and row["seam"] != "factory"]
    # Route by the map as it was at the base too, so a change that edits
    # SEAMS.md cannot move itself out of the owner's view.
    base_touched = {seam_of(p, base_rows)["seam"] for p in counts if seam_of(p, base_rows)}
    for row in base_rows:
        if row["seam"] in base_touched and row["review"] == "owner" and row["seam"] not in owned:
            owned.append(f"{row['seam']} (owner-routed at the base)")
        if row["seam"] in base_touched and row["freeze"] == "yes" and row["seam"] not in frozen:
            frozen.append(f"{row['seam']} (a freeze seam at the base)")

    out = print
    out("# Change report")
    out()
    out(f"> `./dev review --base {args.base}` · candidate `{head[:10]}`"
        + (" **plus uncommitted changes**" if dirty else "") + f" · base `{base[:10]}`")
    out(f"> re-run: check out `{head[:10]}` and run `./dev review --base {base[:10]}`")
    out()
    if not counts:
        out("Nothing has changed since the base.")
        return 0

    out("## Classification")
    out()
    if frozen:
        out(f"**Freeze change.** It alters what is being built: {', '.join(frozen)}. This is where the owner decides.")
        if other:
            out(f"It also changes {', '.join(other)}. A freeze and the code that implements it should be separate changes.")
    else:
        out("**Implements the frozen rules.** No rule, shared example or contract interface changed.")
    out(f"Rules and decisions named by the commits: {', '.join(rules) if rules else 'none'}."
        + (f" Spec: {'; '.join(specs)}." if specs else ""))
    if owned or unowned or asks:
        reasons = owned + (["a file no seam owns"] if unowned else []) + (["a decision named by the commits"] if asks else [])
        out(f"**Routing: OWNER REVIEW REQUIRED** ({', '.join(reasons)}).")
    else:
        out("**Routing: agent review only.** No owner-routed seam was touched and no decision was named.")
    out()

    out("## Structure")
    out()
    out("| Seam | What crosses it | Files | Lines | Review |")
    out("|---|---|---|---|---|")
    for row in touched:
        paths = by_seam[row["seam"]]
        added = sum(counts[p][0] for p in paths)
        removed = sum(counts[p][1] for p in paths)
        out(f"| {row['seam']} | {row['what crosses it']} | {len(paths)} | +{added} / -{removed} | {row['review']} |")
    out()
    for row in touched:
        if row["review"] != "owner":
            continue
        out(f"**{row['seam']}**")
        for path in by_seam[row["seam"]][:20]:
            out(f"- `{path}` ({status.get(path, 'changed')}, +{counts[path][0]} / -{counts[path][1]})")
        if len(by_seam[row["seam"]]) > 20:
            out(f"- and {len(by_seam[row['seam']]) - 20} more")
        out()
    if unowned:
        out("**Files no seam owns** (unresolved, so this widens the review):")
        for path in unowned[:20]:
            out(f"- `{path}`")
        out()

    out("## Contract surface")
    out()
    interface_paths = next((row["patterns"] for row in rows if row["seam"] == "interfaces"), [])
    declared = re.compile(r"\b(function|event|error)\s+\w+")
    delta = []
    for path in sorted(counts):
        if not any(path.startswith(p) for p in interface_paths):
            continue
        if status.get(path) == "new" and path not in git("ls-files", "--", path):
            lines = ["+" + l for l in (ROOT / path).read_text(errors="ignore").splitlines()]
        else:
            lines = git("diff", "-U0", "--no-renames", base, "--", path).splitlines()
        for line in lines:
            if line[:1] in "+-" and not line.startswith(("+++", "---")) and declared.search(line):
                delta.append(f"- `{line[0]} {line[1:].strip()}` ({path})")
    if delta:
        out("Declarations added (+) or removed (-) in the declared interface files:")
        for line in delta:
            out(line)
    elif (ROOT / "contracts").is_dir():
        out("No declaration line changed in the declared interface files.")
    else:
        out("No contracts exist yet.")
    out()
    out("This reads the interface files only. A public function added straight to an"
        " implementation contract does not show here until the compiler's ABI is"
        " compared against the declared interfaces, which arrives with the contracts.")
    out()

    out("## Doors")
    out()
    out("| Rule | Result |")
    out("|---|---|")
    for hit in door_hits(files()):
        if hit["outside"]:
            result = f"**BROKEN**: {len(hit['outside'])} outside `{hit['allowed']}` ({', '.join(hit['outside'][:5])})"
        elif hit["inside"]:
            result = f"{hit['inside']} site(s), all inside `{hit['allowed']}`"
        else:
            result = "0 sites (nothing built behind this door yet)"
        out(f"| {hit['rule']} | {result} |")
    out()

    out("## Checks run on this candidate")
    out()
    results = run_verify(components=args.checks)
    if not args.checks:
        results += [(f"{folder}/check", NOT_RUN, ["not asked for: `./dev review --checks` or `./dev verify`"])
                    for folder in CODE_DIRS if (ROOT / folder).is_dir()]
    out("| Check | Result |")
    out("|---|---|")
    for name, state, detail in results:
        note = f" ({detail[0]})" if detail and state in (NA, NOT_RUN) else ""
        out(f"| {name} | {state}{note} |")
    if code_for(results) == 1:
        out()
        out("**A structure check failed.** Run `./dev verify` for the output.")
    out()

    out("## Checks not run")
    out()
    record = qualify_record()
    kept = json.loads(record.read_text()) if record.is_file() else {}
    out("| Check | Status | Where it runs |")
    out("|---|---|---|")
    if kept.get("commit") == head and not dirty:
        for name, state in kept["results"]:
            if name.endswith("/qualify"):
                out(f"| {name} | {state} on this commit | `./dev qualify` |")
    else:
        for folder in QUALIFY_DIRS:
            out(f"| {folder}/qualify (heavy checks) | `not_run` | `./dev qualify`, when the stage closes |")
    out("| Demo reconciled against the calculator | `not_run` by this report | `./dev demo`, at landing when behaviour changed, and at stage close |")
    out()

    out("## Needs you")
    out()
    items = [f"{ask}" for ask in asks]
    for row in touched:
        if row["review"] == "owner":
            items.append(f"Confirm the change to **{row['seam']}** ({row['what crosses it']}): {len(by_seam[row['seam']])} file(s), listed under Structure.")
    if any(row["review"] == "owner" for row in touched) and not asks:
        items.append("The commits do not say what this changes for a participant. Before this goes to the owner,"
                     " the builder adds a `Needs-owner:` line that says so in one sentence, and the reviewer checks it.")
    if unowned:
        items.append("Decide where the files no seam owns belong.")
    if items:
        for number, item in enumerate(items, 1):
            out(f"{number}. {item}")
    else:
        out("Nothing. No owner-routed seam changed and no decision was left open.")
    out()
    out("This report shows what changed and what ran. It does not say the change is right.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="./dev", description="The one front door. Commands: AGENTS.md")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="what this machine has and lacks").set_defaults(run=cmd_doctor)
    commands.add_parser("verify", help="the fast checks, every change").set_defaults(run=cmd_verify)
    review = commands.add_parser("review", help="the change report a reviewer reads")
    review.add_argument("--base", default="main")
    review.add_argument("--checks", action="store_true", help="also run each component's check (build and tests)")
    review.set_defaults(run=cmd_review)
    commands.add_parser("qualify", help="the heavy checks, when a stage closes").set_defaults(run=cmd_qualify)
    demo = commands.add_parser("demo", help="the one-command demo")
    demo.add_argument("rest", nargs=argparse.REMAINDER)
    demo.set_defaults(run=cmd_demo)
    try:
        args = parser.parse_args()
    except SystemExit as stop:
        return 64 if stop.code not in (0, None) else 0
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
