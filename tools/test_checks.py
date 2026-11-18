"""Self-tests for the scripts the component checks run: each case seeds a
missing or misbehaving tool, or a finding, and shows the script reports it
as failed or not_run, never as passed.

The tools are fakes, written into a folder that is the whole PATH, so a
real tool installed on the machine never answers in their place. Nothing
here changes the checkout. Run with tools/check, or through ./dev verify.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
# What the scripts under test need from the machine besides the faked tools.
UTILS = ("sh", "env", "dirname", "cat", "grep", "sed", "mkdir", "rm", "mv", "ln",
         "uname", "sha256sum", "tar", "python3")


def pinned(name):
    for line in (ROOT / "tools" / "versions").read_text().splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1]
    raise AssertionError(f"tools/versions pins no {name}")


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class Fakes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lp2-checks-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "log"
        for util in UTILS:
            found = shutil.which(util)
            if found:
                (self.bin / util).symlink_to(found)

    def tool(self, name, body):
        """A fake tool: a shell script. $LOG is a file it may write to."""
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def run_script(self, script, *args, **env):
        base = {"PATH": str(self.bin), "HOME": str(self.tmp), "LOG": str(self.log)}
        done = subprocess.run([str(script), *args], env={**base, **env},
                              capture_output=True, text=True, timeout=120)
        return done.returncode, done.stdout + done.stderr

    def logged(self):
        return self.log.read_text() if self.log.exists() else ""


class ProgramsCheck(Fakes):
    """programs/check: layout, vet, then the tests under the race detector."""

    def setUp(self):
        super().setUp()
        self.tool("go", 'echo "go $* GOFLAGS=${GOFLAGS-unset}" >>"$LOG"\n'
                        '[ "$1 $2" = "env CGO_ENABLED" ] && echo 1\nexit 0')

    def test_a_missing_gofmt_is_not_run(self):
        code, out = self.run_script(ROOT / "programs" / "check")
        self.assertEqual(code, 2, out)
        self.assertIn("gofmt", out)
        self.assertNotIn("go test", self.logged())

    def test_a_failing_gofmt_is_not_run(self):
        self.tool("gofmt", 'echo "gofmt: something went wrong" >&2\nexit 2')
        code, out = self.run_script(ROOT / "programs" / "check")
        self.assertEqual(code, 2, out)
        self.assertNotIn("go test", self.logged())

    def test_inherited_goflags_cannot_skip_the_tests(self):
        self.tool("gofmt", "exit 0")
        code, out = self.run_script(ROOT / "programs" / "check", GOFLAGS="-run=^$")
        self.assertEqual(code, 0, out)
        tests = [line for line in self.logged().splitlines() if line.startswith("go test")]
        self.assertEqual(len(tests), 1, self.logged())
        self.assertNotIn("-run", tests[0])


class ProgramsVulncheck(Fakes):
    """programs/vulncheck: the scan uses the real Go, at the pinned version."""

    def setUp(self):
        super().setUp()
        self.tool("govulncheck",
                  f'[ "$1" = -version ] && {{ echo "Scanner: govulncheck@v{pinned("GOVULNCHECK_VERSION")}"; exit 0; }}\n'
                  'echo "scan GOVERSION=${GOVERSION-unset} GOTOOLCHAIN=${GOTOOLCHAIN-unset}" >>"$LOG"\nexit 0')

    def go(self, version):
        self.tool("go", f'[ "$1 $2" = "env GOVERSION" ] && echo go{version}\nexit 0')

    def test_an_older_go_is_not_run_even_when_GOVERSION_names_the_pin(self):
        self.go("1.0.0")
        code, out = self.run_script(ROOT / "programs" / "vulncheck", GOVERSION="go" + pinned("GO_VERSION"))
        self.assertEqual(code, 2, out)
        self.assertNotIn("scan", self.logged())

    def test_the_scan_runs_with_the_local_go_and_no_GOVERSION(self):
        self.go(pinned("GO_VERSION"))
        code, out = self.run_script(ROOT / "programs" / "vulncheck", GOVERSION="go9.9.9")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.logged().strip(), "scan GOVERSION=unset GOTOOLCHAIN=local")


def finding(check, first, *more, line=10):
    """A slither finding as its JSON report gives it."""
    text = f"{first} (src/money/Claims.sol#{line}-{line + 4})\n" + "".join(f"\t{m}\n" for m in more)
    return {"check": check, "impact": "Low", "description": text}


class Slither(unittest.TestCase):
    """contracts/slither: each finding is accepted by name, as often as it
    occurs."""

    @classmethod
    def setUpClass(cls):
        cls.s = load(ROOT / "contracts" / "slither", "slither_check")

    def accepted(self, *keys):
        return self.s.parse_accepted("".join(f"{k}\n    the reason\n" for k in keys))

    def test_line_numbers_do_not_change_a_finding(self):
        a = finding("calls-loop", "C.f() has external calls inside a loop: x = l.v(i)", line=10)
        b = finding("calls-loop", "C.f() has external calls inside a loop: x = l.v(i)", line=99)
        self.assertEqual(self.s.key(a), self.s.key(b))

    def test_the_call_stack_tells_findings_apart(self):
        a = finding("calls-loop", "C._p() has external calls inside a loop: l.v()",
                    "Calls stack containing the loop:", "\tC.withdraw()")
        b = finding("calls-loop", "C._p() has external calls inside a loop: l.v()",
                    "Calls stack containing the loop:", "\tC.withdrawOther()")
        self.assertNotEqual(self.s.key(a), self.s.key(b))
        new, stale = self.s.compare([a, b], self.accepted(self.s.key(a)))
        self.assertEqual([k for k, _, _ in new], [self.s.key(b)])
        self.assertEqual(stale, [])

    def test_a_second_finding_with_an_accepted_key_is_new(self):
        a = finding("calls-loop", "C.f() has external calls inside a loop: x = l.v(i)", line=10)
        b = finding("calls-loop", "C.f() has external calls inside a loop: x = l.v(i)", line=12)
        new, stale = self.s.compare([a, b], self.accepted(self.s.key(a)))
        self.assertEqual(new, [(self.s.key(a), 2, 1)])
        self.assertEqual(stale, [])

    def test_a_finding_listed_twice_accepts_two(self):
        a = finding("calls-loop", "C.f() has external calls inside a loop: x = l.v(i)", line=10)
        b = finding("calls-loop", "C.f() has external calls inside a loop: x = l.v(i)", line=12)
        self.assertEqual(self.s.compare([a, b], self.accepted(self.s.key(a), self.s.key(a))), ([], []))

    def test_a_listed_finding_no_longer_reported_is_stale(self):
        a = finding("calls-loop", "C.f() has external calls inside a loop: x = l.v(i)")
        new, stale = self.s.compare([], self.accepted(self.s.key(a)))
        self.assertEqual((new, stale), ([], [(self.s.key(a), 0, 1)]))

    def test_a_finding_without_a_reason_is_refused(self):
        with self.assertRaises(SystemExit):
            self.s.parse_accepted("calls-loop: C.f() has external calls inside a loop: x\n")


class Mutations(Fakes):
    """contracts/mutations.py: a mutant counts as caught only when its check
    ran and turned red."""

    def setUp(self):
        super().setUp()
        self.m = load(ROOT / "contracts" / "mutations.py", "mutations_check")
        work = self.tmp / "copy" / "contracts"
        shutil.copytree(ROOT / "contracts" / "src", work / "src")
        shutil.copytree(ROOT / "vectors", self.tmp / "copy" / "vectors")
        self.check = work / "check"
        self.patch = mock.patch.object(self.m, "ROOT", work)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def forge(self, fmt=0):
        """A fake forge: builds, lays out with the given exit code, and
        fails every test it is asked to run."""
        self.tool("forge", 'case "$1" in\n'
                           f"  fmt) exit {fmt} ;;\n"
                           '  test) echo "[FAIL: seeded] test_x() (gas: 1)"; exit 1 ;;\n'
                           "esac\nexit 0")

    def fast_check(self, code):
        self.check.write_text(f"#!/bin/sh\necho 'the fast check, exit {code}'\nexit {code}\n")

    def main(self, name):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"PATH": str(self.bin)}), contextlib.redirect_stdout(out):
            code = self.m.main([name])
        return code, out.getvalue()

    def test_a_check_that_turns_red_catches_the_mutant(self):
        self.forge()
        self.fast_check(1)
        code, out = self.main("abi: extra public function on Broker")
        self.assertEqual(code, 0, out)
        self.assertIn("RED", out)

    def test_a_formatter_that_fails_is_not_run_not_caught(self):
        self.forge(fmt=1)
        self.fast_check(1)
        code, out = self.main("abi: extra public function on Broker")
        self.assertEqual(code, 1, out)
        self.assertIn("NOTRUN", out)

    def test_a_check_that_could_not_run_is_not_run_not_caught(self):
        self.forge()
        self.fast_check(2)
        code, out = self.main("abi: extra public function on Broker")
        self.assertEqual(code, 1, out)
        self.assertIn("NOTRUN", out)

    def test_a_heavy_only_mutant_needs_a_fast_check_that_ran_green(self):
        self.forge()
        self.fast_check(2)
        code, out = self.main("partly paid ticket not marked used")
        self.assertEqual(code, 1, out)
        self.assertNotIn("fast check: green", out)


if __name__ == "__main__":
    unittest.main()
