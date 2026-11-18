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
import json
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
        path.unlink(missing_ok=True)  # never write through a link to a real tool
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
        self.tool("go", 'echo "go $* GOFLAGS=${GOFLAGS-unset} GOENV=${GOENV-unset}" >>"$LOG"\n'
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

    def test_go_saved_settings_cannot_skip_the_tests(self):
        # go env -w GOFLAGS=-run=^$ writes this file; an empty GOFLAGS in
        # the environment falls back to it.
        self.tool("gofmt", "exit 0")
        saved = self.tmp / "goenv"
        saved.write_text("GOFLAGS=-run=^$\n")
        code, out = self.run_script(ROOT / "programs" / "check", GOENV=str(saved), GOFLAGS="")
        self.assertEqual(code, 0, out)
        calls = self.logged().splitlines()
        self.assertTrue(calls, out)
        for call in calls:
            self.assertTrue(call.endswith("GOFLAGS=unset GOENV=off"), call)


class ProgramsVulncheck(Fakes):
    """programs/vulncheck: the scan uses the real Go, at the pinned version."""

    def setUp(self):
        super().setUp()
        self.tool("govulncheck",
                  f'[ "$1" = -version ] && {{ echo "Scanner: govulncheck@v{pinned("GOVULNCHECK_VERSION")}"; exit 0; }}\n'
                  'echo "scan GOVERSION=${GOVERSION-unset} GOTOOLCHAIN=${GOTOOLCHAIN-unset}'
                  ' GOENV=${GOENV-unset} GOFLAGS=${GOFLAGS-unset}" >>"$LOG"\nexit 0')

    def go(self, version):
        self.tool("go", f'[ "$1 $2" = "env GOVERSION" ] && echo go{version}\nexit 0')

    def test_an_older_go_is_not_run_even_when_GOVERSION_names_the_pin(self):
        self.go("1.0.0")
        code, out = self.run_script(ROOT / "programs" / "vulncheck", GOVERSION="go" + pinned("GO_VERSION"))
        self.assertEqual(code, 2, out)
        self.assertNotIn("scan", self.logged())

    def test_the_scan_runs_with_the_local_go_and_nothing_saved(self):
        self.go(pinned("GO_VERSION"))
        saved = self.tmp / "goenv"
        saved.write_text("GOFLAGS=-tags=skip\n")
        code, out = self.run_script(ROOT / "programs" / "vulncheck", GOVERSION="go9.9.9",
                                    GOENV=str(saved), GOFLAGS="-tags=skip")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.logged().strip(), "scan GOVERSION=unset GOTOOLCHAIN=local GOENV=off GOFLAGS=unset")


class Setup(Fakes):
    """tools/setup installs only what tools/versions pins."""

    def test_govulncheck_is_not_built_when_the_pinned_go_cannot_be_installed(self):
        self.tool("curl", "exit 22")  # every download fails
        self.tool("go", 'case "$1" in\n'
                        "  env) echo go1.0.0 ;;\n"
                        '  install) echo install >>"$LOG"\n'
                        f'           printf \'#!/bin/sh\\necho "Scanner: govulncheck@v{pinned("GOVULNCHECK_VERSION")}"\\n\' >"$GOBIN/govulncheck"\n'
                        '           chmod +x "$GOBIN/govulncheck" ;;\n'
                        "esac\nexit 0")
        code, out = self.run_script(ROOT / "tools" / "setup", "govulncheck", LP2_TOOLS=str(self.tmp / "tools"))
        self.assertEqual(code, 2, out)
        self.assertNotIn("install", self.logged())

    def test_a_govulncheck_built_with_another_go_is_not_the_pinned_one(self):
        self.tool("curl", "exit 22")
        self.tool("gofmt", "cat >/dev/null")
        self.tool("govulncheck", f'echo "Scanner: govulncheck@v{pinned("GOVULNCHECK_VERSION")}"')
        for built in ("go1.0.0", f'go{pinned("GO_VERSION")}'):
            self.tool("go", 'case "$1" in\n'
                            f'  env) echo go{pinned("GO_VERSION")} ;;\n'
                            f'  version) echo "$2: {built}" ;;\n'
                            '  install) echo install >>"$LOG"; exit 1 ;;\n'
                            "esac\nexit 0")
            code, out = self.run_script(ROOT / "tools" / "setup", "govulncheck", LP2_TOOLS=str(self.tmp / "tools"))
            if built == "go1.0.0":  # rebuilt with the pinned go (which fails here)
                self.assertEqual(code, 2, out)
                self.assertIn("install", self.logged())
            else:
                self.assertEqual((code, out), (0, ""))

    def foundry(self, forge_exit=0):
        for b in ("forge", "anvil", "cast"):
            code = forge_exit if b == "forge" else 0
            self.tool(b, f'echo "{b} Version: {pinned("FOUNDRY_VERSION")}"\nexit {code}')

    def test_a_version_probe_that_exits_non_zero_is_not_the_pinned_tool(self):
        self.tool("curl", "exit 22")  # so setup cannot install another
        self.foundry(forge_exit=3)
        code, out = self.run_script(ROOT / "tools" / "setup", "foundry", LP2_TOOLS=str(self.tmp / "tools"))
        self.assertEqual(code, 2, out)
        self.foundry()
        code, out = self.run_script(ROOT / "tools" / "setup", "foundry", LP2_TOOLS=str(self.tmp / "tools"))
        self.assertEqual((code, out), (0, ""))

    def test_a_warm_folder_serves_only_the_whole_sha256_it_was_keyed_by(self):
        """A pin that differs from the warm folder's only in its last hex
        digit is a fresh install, which here cannot download."""
        self.tool("curl", "exit 22")
        copy = self.tmp / "tools"
        copy.mkdir()
        shutil.copy(ROOT / "tools" / "setup", copy / "setup")
        sha = pinned("FOUNDRY_SHA256")
        changed = sha[:-1] + ("0" if sha[-1] != "0" else "1")
        versions = (ROOT / "tools" / "versions").read_text()
        (copy / "versions").write_text(versions.replace(sha, changed))
        cache = self.tmp / "cache"
        for key in (sha, sha[:16]):  # the folders this and an older setup made
            warm = cache / f"foundry-{pinned('FOUNDRY_VERSION')}-{key}"
            warm.mkdir(parents=True)
            for b in ("forge", "anvil", "cast"):
                (warm / b).write_text(f'#!/bin/sh\necho "{b} Version: {pinned("FOUNDRY_VERSION")}"\n')
                (warm / b).chmod(0o755)
        code, out = self.run_script(copy / "setup", "foundry", LP2_TOOLS=str(cache))
        self.assertEqual(code, 2, out)

    def test_go_without_its_gofmt_is_not_ready(self):
        self.tool("curl", "exit 22")
        self.tool("go", f'[ "$1 $2" = "env GOVERSION" ] && echo go{pinned("GO_VERSION")}\nexit 0')
        code, out = self.run_script(ROOT / "tools" / "setup", "go", LP2_TOOLS=str(self.tmp / "tools"))
        self.assertEqual(code, 2, out)
        self.tool("gofmt", "cat >/dev/null")
        code, out = self.run_script(ROOT / "tools" / "setup", "go", LP2_TOOLS=str(self.tmp / "tools"))
        self.assertEqual((code, out), (0, ""))

    def test_a_go_whose_version_command_fails_is_not_ready(self):
        self.tool("curl", "exit 22")
        self.tool("gofmt", "cat >/dev/null")
        self.tool("go", f'echo go{pinned("GO_VERSION")}\nexit 1')
        code, out = self.run_script(ROOT / "tools" / "setup", "go", LP2_TOOLS=str(self.tmp / "tools"))
        self.assertEqual(code, 2, out)

    def slither_setup(self, lock):
        """A copy of tools/setup with the given lock file, a warm folder for
        the lock in the checkout, the folder an older setup used, a slither
        on PATH at the pinned version, and a python3 that logs instead of
        making an environment."""
        copy = self.tmp / "tools"
        copy.mkdir()
        for name in ("setup", "versions"):
            shutil.copy(ROOT / "tools" / name, copy / name)
        (copy / "slither-requirements.txt").write_text(lock)
        cache = self.tmp / "cache"
        real = ROOT / "tools" / "slither-requirements.txt"
        digest = subprocess.run(["sha256sum", str(real)], capture_output=True, text=True).stdout.split()[0]
        version = f'#!/bin/sh\necho {pinned("SLITHER_VERSION")}\n'
        warm = cache / f"slither-{pinned('SLITHER_VERSION')}-{digest}" / "path"
        for folder in (warm, cache / f"slither-{pinned('SLITHER_VERSION')}" / "path", self.bin):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "slither").unlink(missing_ok=True)
            (folder / "slither").write_text(version)
            (folder / "slither").chmod(0o755)
        self.tool("python3", 'echo "python3 $*" >>"$LOG"\nexit 1')
        code, out = self.run_script(copy / "setup", "slither", LP2_TOOLS=str(cache))
        return code, out, warm

    def test_a_warm_slither_is_used_only_for_the_lock_it_came_from(self):
        lock = (ROOT / "tools" / "slither-requirements.txt").read_text()
        code, out, warm = self.slither_setup(lock)
        self.assertEqual((code, out.strip()), (0, str(warm)))
        self.assertEqual(self.logged(), "")

    def test_a_changed_lock_is_a_fresh_install(self):
        lock = (ROOT / "tools" / "slither-requirements.txt").read_text()
        changed = lock.replace("--hash=sha256:", "--hash=sha256:0", 1)
        code, out, warm = self.slither_setup(changed)
        self.assertEqual(code, 2, out)
        self.assertIn("python3 -m venv", self.logged())

    def test_slither_is_installed_from_a_lock_at_the_pinned_version(self):
        lock = (ROOT / "tools" / "slither-requirements.txt").read_text()
        self.assertIn(f"\nslither-analyzer=={pinned('SLITHER_VERSION')} \\\n", lock)
        packages = [line for line in lock.splitlines() if line and not line.startswith(("#", " "))]
        self.assertGreater(len(packages), 1)
        for package in packages:  # one version each; pip --require-hashes needs the hashes
            self.assertRegex(package, r"^[A-Za-z0-9_.-]+==[^ ]+ \\$")
        self.assertIn("--require-hashes", (ROOT / "tools" / "setup").read_text())


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


class SlitherRun(Fakes):
    """contracts/slither: an analyzer that could not finish is not_run."""

    def setUp(self):
        super().setUp()
        self.tool("forge", "exit 0")

    def slither(self, body, version_exit=0):
        self.tool("slither", f'[ "$1" = --version ] && {{ echo {pinned("SLITHER_VERSION")}; exit {version_exit}; }}\n' + body)

    def test_an_analyzer_that_stops_before_its_report_is_not_run(self):
        self.slither('echo "Traceback: something went wrong" >&2\nexit 1')
        code, out = self.run_script(ROOT / "contracts" / "slither")
        self.assertEqual(code, 2, out)
        self.assertIn("not_run", out)

    def test_a_report_that_says_it_did_not_succeed_is_not_run(self):
        self.slither('while [ "$1" != --json ]; do shift; done\n'
                     'echo \'{"success": false, "error": "compilation failed"}\' >"$2"\nexit 1')
        code, out = self.run_script(ROOT / "contracts" / "slither")
        self.assertEqual(code, 2, out)

    def test_a_version_command_that_fails_is_not_run(self):
        self.slither("exit 0", version_exit=1)
        code, out = self.run_script(ROOT / "contracts" / "slither")
        self.assertEqual(code, 2, out)

    def test_a_finished_report_with_a_new_finding_fails(self):
        report = {"success": True, "results": {"detectors": [
            finding("calls-loop", "C.f() has external calls inside a loop: x = l.v(i)")]}}
        (self.tmp / "report.json").write_text(json.dumps(report))
        self.slither('while [ "$1" != --json ]; do shift; done\n'
                     f'cat "{self.tmp / "report.json"}" >"$2"\nexit 0')
        code, out = self.run_script(ROOT / "contracts" / "slither")
        self.assertEqual(code, 1, out)
        self.assertIn("NEW", out)


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

    def forge(self, fmt=0, check=0):
        """A fake forge: builds, lays out with the exit code fmt, checks
        the layout with the exit code check, and fails every test it is
        asked to run."""
        self.tool("forge", 'case "$1" in\n'
                           f'  fmt) [ "$2" = --check ] && exit {check}; exit {fmt} ;;\n'
                           '  test) echo "[FAIL: seeded] test_x() (gas: 1)"; exit 1 ;;\n'
                           "esac\nexit 0")

    def fast_check(self, code):
        self.check.write_text(f"#!/bin/sh\necho 'the fast check, exit {code}'\nexit {code}\n")

    def main(self, *names):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"PATH": str(self.bin), "LOG": str(self.log)}), \
                contextlib.redirect_stdout(out):
            code = self.m.main(list(names))
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
        self.assertEqual(code, 2, out)
        self.assertIn("NOTRUN", out)

    def test_a_layout_check_that_could_not_run_is_not_run_not_caught(self):
        # The real contracts/check, with a forge fmt --check that stops.
        self.forge(check=9)
        shutil.copy(ROOT / "contracts" / "check", self.check)
        code, out = self.main("abi: extra public function on Broker")
        self.assertEqual(code, 2, out)
        self.assertIn("NOTRUN", out)
        self.assertNotIn("RED", out)

    def test_a_check_that_could_not_run_is_not_run_not_caught(self):
        self.forge()
        self.fast_check(2)
        code, out = self.main("abi: extra public function on Broker")
        self.assertEqual(code, 2, out)
        self.assertIn("NOTRUN", out)

    def test_a_heavy_only_mutant_needs_a_fast_check_that_ran_green(self):
        self.forge()
        self.fast_check(2)
        code, out = self.main("partly paid ticket not marked used")
        self.assertEqual(code, 2, out)
        self.assertNotIn("fast check: green", out)

    def test_a_mutant_a_check_ran_and_missed_fails_the_run_beside_one_not_run(self):
        self.forge()
        # The first check could not run; the second ran and stayed green.
        self.check.write_text('#!/bin/sh\n[ -e "$LOG" ] && exit 0\necho >"$LOG"\nexit 2\n')
        code, out = self.main("abi: extra public function on Broker", "abi: extra public function on Claims")
        self.assertEqual(code, 1, out)
        self.assertIn("NOTRUN", out)
        self.assertIn("GREEN", out)

    def test_a_name_that_matches_no_mutant_is_not_run(self):
        self.forge()
        self.fast_check(1)
        code, out = self.main("no mutant has this name")
        self.assertEqual(code, 2, out)


class ContractsCheck(Fakes):
    """contracts/check: a finding is exit 1; a forge that could not check is
    exit 2."""

    def setUp(self):
        super().setUp()
        self.dir = self.tmp / "contracts"
        self.dir.mkdir()
        shutil.copy(ROOT / "contracts" / "check", self.dir / "check")

    def forge(self, check, test="exit 0"):
        self.tool("forge", 'case "$1" in\n'
                           f"  fmt) {check} ;;\n"
                           f"  test) {test} ;;\n"
                           "esac\nexit 0")

    def test_a_layout_that_differs_fails(self):
        self.forge('echo "Diff in src/A.sol:"; exit 1')
        code, out = self.run_script(self.dir / "check")
        self.assertEqual(code, 1, out)

    def test_a_file_the_formatter_cannot_read_is_not_run(self):
        self.forge('echo "Error: solar reported errors:" >&2; exit 1')
        code, out = self.run_script(self.dir / "check")
        self.assertEqual(code, 2, out)

    def test_a_formatter_that_stops_is_not_run(self):
        self.forge("exit 9")
        code, out = self.run_script(self.dir / "check")
        self.assertEqual(code, 2, out)

    def test_a_forge_test_that_stops_is_not_run(self):
        self.forge("exit 0", test="exit 101")
        code, out = self.run_script(self.dir / "check")
        self.assertEqual(code, 2, out)

    def test_a_failing_test_fails(self):
        self.forge("exit 0", test='echo "[FAIL: seeded] test_a() (gas: 1)"; exit 1')
        code, out = self.run_script(self.dir / "check")
        self.assertEqual(code, 1, out)

    def test_code_that_does_not_compile_fails(self):
        self.forge("exit 0", test='echo "Error: Compiler run failed:" >&2; exit 1')
        code, out = self.run_script(self.dir / "check")
        self.assertEqual(code, 1, out)

    # Any Foundry setting from the caller's environment reaches forge: here,
    # one that runs nothing. Only qualify's own profile is allowed.
    FILTERED = ('if env | grep -E "^(FOUNDRY|DAPP)_" | grep -vqx FOUNDRY_PROFILE=qualify; '
                'then echo filtered; exit 0; fi; ')
    # Test filters, a skipped file, and a setting no list here names.
    INHERITED = {"FOUNDRY_MATCH_TEST": "^$", "DAPP_NO_MATCH_PATH": "test/*",
                 "FOUNDRY_SKIP": '["test/ReviewSkip.t.sol"]', "FOUNDRY_FUZZ_RUNS": "0"}

    def test_inherited_test_filters_are_cleared(self):
        self.forge("exit 0", test=self.FILTERED + 'echo "[FAIL: seeded] test_a() (gas: 1)"; exit 1')
        code, out = self.run_script(self.dir / "check", **self.INHERITED)
        self.assertEqual(code, 1, out)
        self.assertIn("[FAIL: seeded]", out)

    def test_no_test_run_is_not_run(self):
        self.forge("exit 0", test='echo "No tests match the provided pattern"; exit 0')
        code, out = self.run_script(self.dir / "check")
        self.assertEqual(code, 2, out)

    def test_qualify_clears_inherited_test_filters(self):
        shutil.copy(ROOT / "contracts" / "qualify", self.dir / "qualify")
        for name in ("date", "tee"):
            (self.bin / name).symlink_to(shutil.which(name))
        self.forge("exit 0", test=self.FILTERED + 'echo "MoneyInvariants invariants (runs: 5)"; exit 0')
        code, out = self.run_script(self.dir / "qualify", **self.INHERITED)
        self.assertNotIn("filtered", out)
        self.assertIn("MoneyInvariants invariants (runs: 5)", out)

    def test_forge_stopping_with_exit_1_is_not_run(self):
        # FOUNDRY_OUT=/dev/null: forge exits 1 with no test run.
        self.forge("exit 0", test='echo "Error: File exists (os error 17)" >&2; exit 1')
        code, out = self.run_script(self.dir / "check")
        self.assertEqual(code, 2, out)


if __name__ == "__main__":
    unittest.main()
