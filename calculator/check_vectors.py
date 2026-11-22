"""Run every shared example, reporting each ID and comparing exact outputs."""

import argparse
import json
import re
from pathlib import Path

from reference import Parameters, calculate


DEFAULT_VECTORS = Path(__file__).resolve().parent.parent / "vectors/october-main-text-v3.json"


def decimal_strings(value):
    if isinstance(value, dict):
        return {key: decimal_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decimal_strings(item) for item in value]
    if type(value) is int:
        return str(value)
    return value


RULEBOOKS = Path(__file__).resolve().parent.parent / "rulebook"


def rulebook_errors(profile: str, ids: set, kinds: set) -> None:
    """The rulebook and its examples hold together: every example the
    rulebook names in backticks is a case here, so removing a worked case
    fails, and every rule the rulebook cites has its own entry."""
    text = (RULEBOOKS / f"{profile}.md").read_text()
    named = set(re.findall(r"`([a-z0-9]+(?:-[a-z0-9]+)+)`", text))
    named -= kinds | {path.stem for path in RULEBOOKS.glob("*.md")}
    missing = sorted(named - ids)
    if missing:
        raise ValueError(f"{profile} names examples the vectors lack: {', '.join(missing)}")
    entries = set(re.findall(r"^## (R-\d+)\b", text, re.M))
    unwritten = sorted(set(re.findall(r"\bR-\d+\b", text)) - entries)
    if unwritten:
        raise ValueError(f"{profile} cites rules with no entry: {', '.join(unwritten)}")


def check(path: Path) -> int:
    try:
        document = json.loads(path.read_text())
    except OSError as error:
        print(f"calculator: not_run: {error}")
        return 2
    except (ValueError, UnicodeError) as error:
        print(f"calculator: FAILED: {error}")
        return 1

    try:
        if document["profile"] not in ("october-main-text-v0", "october-main-text-v1",
                                       "october-main-text-v2", "october-main-text-v3"):
            raise ValueError("unknown rulebook profile")
        parameters = Parameters.from_strings(document["parameters"])
        cases = document["cases"]
        if not cases:
            raise ValueError("no cases to check")
        # Inputs and expected outputs are separate keys of each case; nothing
        # else may ride along where the calculator could read it.
        for case in cases:
            if set(case) != {"id", "kind", "inputs", "expected"}:
                raise ValueError(f"case {case.get('id')!r} must have exactly id, kind, inputs and expected")
        ids = [case["id"] for case in cases]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate case ID")
        rulebook_errors(document["profile"], set(ids), {case["kind"] for case in cases})
        # Strip expected outputs before constructing any calculator input.
        rounds = {case["id"]: case["inputs"] for case in cases if case["kind"] == "round"}
    except (KeyError, OSError, TypeError, ValueError) as error:
        print(f"calculator: FAILED: {error}")
        return 1

    failures = 0
    for case in cases:
        try:
            actual = decimal_strings(calculate(case["kind"], case["inputs"], parameters, rounds))
            expected = case["expected"]
            if case["kind"] == "ceiling":
                # The rulebook gives ceiling cases two output shapes. Always
                # compute both; compare the complete shape the case specifies.
                keys = set(expected)
                if keys not in ({"ceiling_wei"}, {"validators_wei", "treasury_wei"}):
                    raise ValueError("unknown ceiling output shape")
                actual = {key: actual[key] for key in expected}
            if actual != expected:
                raise ValueError(f"expected {json.dumps(expected, sort_keys=True)}; "
                                 f"got {json.dumps(actual, sort_keys=True)}")
        except (KeyError, TypeError, ValueError) as error:
            failures += 1
            print(f"FAIL {case['id']}: {error}")
        else:
            print(f"PASS {case['id']}")

    print(f"calculator: {len(cases) - failures}/{len(cases)} cases passed; {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vectors", type=Path, nargs="?", default=DEFAULT_VECTORS)
    raise SystemExit(check(parser.parse_args().vectors))
