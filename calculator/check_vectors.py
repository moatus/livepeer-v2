"""Run every shared example, reporting each ID and comparing exact outputs."""

import argparse
import json
from pathlib import Path

from reference import Parameters, calculate


DEFAULT_VECTORS = Path(__file__).resolve().parent.parent / "vectors/october-main-text-v0.json"


def decimal_strings(value):
    if isinstance(value, dict):
        return {key: decimal_strings(item) for key, item in value.items()}
    if type(value) is int:
        return str(value)
    return value


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
        if document["profile"] != "october-main-text-v0":
            raise ValueError("unknown rulebook profile")
        parameters = Parameters.from_strings(document["parameters"])
        cases = document["cases"]
        if not cases:
            raise ValueError("no cases to check")
        ids = [case["id"] for case in cases]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate case ID")
        # Strip expected outputs before constructing any calculator input.
        rounds = {case["id"]: case["inputs"] for case in cases if case["kind"] == "round"}
    except (KeyError, TypeError, ValueError) as error:
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
