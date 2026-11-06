"""Self-tests for ./dev.

Every mechanical rule is shown to fail on a seeded violation and to pass
without it. Each test builds a throwaway copy of the factory files in a
temporary git repository, so nothing here touches the real checkout.

Run with tools/check, or through ./dev verify.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FACTORY_FILES = (
    "dev", "tools/dev.py", "SEAMS.md", "AGENTS.md", "README.md",
    "CLAUDE.md", ".gitignore", ".t3code/worktree-links.json",
)
PASS = "#!/bin/sh\nexit 0\n"
FAIL = "#!/bin/sh\necho 'boom: 2 + 2 was 5'\nexit 1\n"
CANNOT = "#!/bin/sh\necho 'forge is not installed'\nexit 2\n"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lp2-dev-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "lp2-core"
        self.research = self.tmp / "lp2-research"
        for name in FACTORY_FILES:
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        self.research.mkdir()
        self.git("init", "-q", "-b", "main")
        self.commit("founding")

    def git(self, *args):
        done = subprocess.run(
            ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
            cwd=self.repo, capture_output=True, text=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def add(self, path, text, executable=False):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        if executable:
            target.chmod(0o755)

    def dev(self, *args, research=True):
        env = dict(os.environ, LP2_DEV_SELFTEST="1")
        env.pop("LP2_RESEARCH", None)
        if research:
            env["LP2_RESEARCH"] = str(self.research)
        done = subprocess.run(
            [str(self.repo / "dev"), *args], cwd=self.repo, capture_output=True, text=True, env=env,
        )
        return done.returncode, done.stdout + done.stderr


class Verify(Fixture):
    def test_the_factory_alone_passes(self):
        code, out = self.dev("verify")
        self.assertEqual(code, 0, out)
        self.assertIn("verify: passed", out)

    def test_a_file_no_seam_owns_fails(self):
        self.add("stray/notes.txt", "hello\n")
        code, out = self.dev("verify")
        self.assertEqual(code, 1, out)
        self.assertIn("no seam owns stray/notes.txt", out)

    def test_a_mint_call_outside_the_money_seam_fails(self):
        self.add("contracts/check", PASS, executable=True)
        self.add("contracts/src/rewards/Claims.sol", "contract Claims {\n  function pay() external { token.mint(msg.sender, 1); }\n}\n")
        code, out = self.dev("verify")
        self.assertEqual(code, 1, out)
        self.assertIn("failed   door rule one-mint-path", out)
        self.assertIn("contracts/src/rewards/Claims.sol:2", out)

    def test_a_mint_call_inside_the_money_seam_passes(self):
        self.add("contracts/check", PASS, executable=True)
        self.add("contracts/src/money/Minter.sol", "contract Minter {\n  function pay() external { token.mint(msg.sender, 1); }\n}\n")
        code, out = self.dev("verify")
        self.assertEqual(code, 0, out)
        self.assertIn("passed   door rule one-mint-path", out)

    def test_a_token_transfer_outside_the_money_seam_fails(self):
        self.add("contracts/check", PASS, executable=True)
        self.add("contracts/src/authority/Admin.sol", "contract Admin {\n  function sweep() external { usdc.safeTransfer(owner, 1); }\n}\n")
        code, out = self.dev("verify")
        self.assertEqual(code, 1, out)
        self.assertIn("failed   door rule one-funds-door", out)

    def test_verify_needs_no_research_repository(self):
        shutil.rmtree(self.research)
        code, out = self.dev("verify", research=False)
        self.assertEqual(code, 0, out)
        self.assertIn("verify: passed", out)

    def test_code_without_a_check_is_not_run_never_passed(self):
        self.add("calculator/calc.py", "print(1)\n")
        code, out = self.dev("verify")
        self.assertEqual(code, 2, out)
        self.assertIn("not_run  calculator/check", out)
        self.assertNotIn("verify: passed", out)

    def test_a_failing_component_check_fails_and_shows_its_output(self):
        self.add("calculator/check", FAIL, executable=True)
        code, out = self.dev("verify")
        self.assertEqual(code, 1, out)
        self.assertIn("boom: 2 + 2 was 5", out)

    def test_a_component_check_that_could_not_run_is_not_run(self):
        self.add("contracts/check", CANNOT, executable=True)
        code, out = self.dev("verify")
        self.assertEqual(code, 2, out)
        self.assertIn("forge is not installed", out)

    def test_a_passing_component_check_passes(self):
        self.add("calculator/check", PASS, executable=True)
        code, out = self.dev("verify")
        self.assertEqual(code, 0, out)
        self.assertIn("passed   calculator/check", out)


class Review(Fixture):
    def test_a_rule_change_is_a_freeze_change_for_the_owner(self):
        self.add("rulebook/R-03.md", "# R-03 Emission\n")
        self.commit("Add R-03, closing D-03\n\nNeeds-owner: Confirm emission follows the main text, not the page 7 diagram.")
        code, out = self.dev("review", "--base", "HEAD~1")
        self.assertEqual(code, 0, out)
        self.assertIn("**Freeze change.**", out)
        self.assertIn("OWNER REVIEW REQUIRED", out)
        self.assertIn("D-03, R-03", out)
        self.assertIn("1. Confirm emission follows the main text", out)
        self.assertIn("`rulebook/R-03.md` (new, +1 / -0)", out)

    def test_calculator_work_needs_no_owner(self):
        self.add("calculator/check", PASS, executable=True)
        self.add("calculator/calc.py", "print(1)\n")
        self.commit("Start the calculator")
        code, out = self.dev("review", "--base", "HEAD~1")
        self.assertEqual(code, 0, out)
        self.assertIn("**Implements the frozen rules.**", out)
        self.assertIn("Routing: agent review only", out)
        self.assertIn("Nothing. No owner-routed seam changed", out)

    def test_a_freeze_mixed_with_implementation_is_called_out(self):
        self.add("vectors/first.json", "{}\n")
        self.add("calculator/check", PASS, executable=True)
        self.commit("Examples and code together")
        _, out = self.dev("review", "--base", "HEAD~1")
        self.assertIn("should be separate changes", out)

    def test_money_seam_work_is_routed_to_the_owner_without_being_a_freeze(self):
        self.add("contracts/check", PASS, executable=True)
        self.add("contracts/src/money/Minter.sol", "contract Minter {}\n")
        self.commit("Start the minter")
        _, out = self.dev("review", "--base", "HEAD~1")
        self.assertIn("**Implements the frozen rules.**", out)
        self.assertIn("OWNER REVIEW REQUIRED** (money)", out)
        self.assertIn("Confirm the change to **money**", out)

    def test_interface_declarations_added_and_removed_are_listed(self):
        self.add("contracts/check", PASS, executable=True)
        self.add("contracts/interfaces/IRegistry.sol", "interface IRegistry {\n  function register(address node) external;\n}\n")
        self.commit("Registry interface")
        self.add("contracts/interfaces/IRegistry.sol", "interface IRegistry {\n  function enrol(address node) external;\n  event Enrolled(address node);\n}\n")
        self.commit("Rename register to enrol")
        _, out = self.dev("review", "--base", "HEAD~1")
        self.assertIn("`- function register(address node) external;`", out)
        self.assertIn("`+ function enrol(address node) external;`", out)
        self.assertIn("`+ event Enrolled(address node);`", out)

    def test_uncommitted_work_is_reported_and_marked(self):
        self.add("deployments/local.json", "{}\n")
        _, out = self.dev("review")
        self.assertIn("plus uncommitted changes", out)
        self.assertIn("OWNER REVIEW REQUIRED** (deployment)", out)

    def test_heavy_checks_are_listed_as_not_run(self):
        self.add("calculator/check", PASS, executable=True)
        self.commit("Calculator check")
        _, out = self.dev("review", "--base", "HEAD~1")
        self.assertIn("| calculator/qualify (heavy checks) | `not_run` |", out)

    def test_an_unknown_base_is_a_usage_error(self):
        code, _ = self.dev("review", "--base", "no-such-branch")
        self.assertEqual(code, 64)


class Qualify(Fixture):
    def test_with_no_heavy_checks_it_reports_not_run(self):
        code, out = self.dev("qualify")
        self.assertEqual(code, 2, out)
        self.assertIn("qualify: not_run", out)

    def test_a_clean_passing_run_is_recorded_and_shown_by_the_report(self):
        self.add("calculator/check", PASS, executable=True)
        self.add("calculator/qualify", PASS, executable=True)
        self.commit("Calculator with heavy checks")
        code, out = self.dev("qualify")
        self.assertEqual(code, 0, out)
        record = json.loads((self.repo / ".cache/dev/qualify.json").read_text())
        self.assertEqual(record["commit"], self.git("rev-parse", "HEAD").strip())
        _, report = self.dev("review", "--base", "HEAD~1")
        self.assertIn("| calculator/qualify | passed on this commit |", report)

    def test_an_uncommitted_tree_keeps_no_record(self):
        self.add("calculator/check", PASS, executable=True)
        self.add("calculator/qualify", PASS, executable=True)
        code, out = self.dev("qualify")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.repo / ".cache/dev/qualify.json").exists())

    def test_a_failing_heavy_check_fails(self):
        self.add("calculator/check", PASS, executable=True)
        self.add("calculator/qualify", FAIL, executable=True)
        code, out = self.dev("qualify")
        self.assertEqual(code, 1, out)


class Demo(Fixture):
    def test_before_it_is_built_the_demo_says_so(self):
        code, out = self.dev("demo")
        self.assertEqual(code, 2, out)
        self.assertIn("not built yet", out)


if __name__ == "__main__":
    unittest.main()
