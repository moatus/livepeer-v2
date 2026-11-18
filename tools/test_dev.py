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
    "tools/versions",
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

    def dev(self, *args, research=True, path=None):
        env = dict(os.environ, LP2_DEV_SELFTEST="1")
        if path:
            env["PATH"] = path
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
    def test_a_rule_change_is_a_freeze_change_that_needs_a_ruling(self):
        self.add("rulebook/R-03.md", "# R-03 Emission\n")
        self.commit("Add R-03, closing D-03\n\nNeeds-owner: Confirm emission follows the main text, not the page 7 diagram.")
        code, out = self.dev("review", "--base", "HEAD~1")
        self.assertEqual(code, 0, out)
        self.assertIn("**Freeze change.**", out)
        self.assertIn("COUNCIL RULING NEEDED", out)
        self.assertNotIn("OWNER REVIEW REQUIRED", out)
        self.assertIn("D-03, R-03", out)
        self.assertIn("1. Confirm emission follows the main text", out)
        self.assertIn("`rulebook/R-03.md` (new, +1 / -0)", out)

    def test_a_rule_change_under_a_council_ruling_does_not_wait_for_the_owner(self):
        (self.research / "council").mkdir()
        (self.research / "council" / "D-03.md").write_text("# D-03\n")
        self.add("rulebook/R-03.md", "# R-03 Emission\n")
        self.commit("Add R-03\n\nCouncil: D-03")
        code, out = self.dev("review", "--base", "HEAD~1")
        self.assertEqual(code, 0, out)
        self.assertIn("Decided by council ruling D-03. The owner keeps or changes it later.", out)
        self.assertIn("**Routing: council ruling D-03, then agent review.**", out)
        self.assertNotIn("COUNCIL RULING NEEDED", out)
        self.assertIn("Nothing now.", out)
        self.assertIn("Not waiting for you: council ruling D-03 decided the change to rules.", out)

    def test_a_rule_change_naming_no_council_ruling_waits_for_one(self):
        self.add("rulebook/R-03.md", "# R-03 Emission\n")
        self.commit("Add R-03")
        _, out = self.dev("review", "--base", "HEAD~1")
        self.assertIn("COUNCIL RULING NEEDED** (rules: no council ruling named by the commits)", out)
        self.assertIn("It needs a council ruling before it lands.", out)

    def test_a_council_ruling_with_no_record_waits_for_one(self):
        self.add("rulebook/R-03.md", "# R-03 Emission\n")
        self.commit("Add R-03\n\nCouncil: D-03")
        _, out = self.dev("review", "--base", "HEAD~1")
        self.assertIn("COUNCIL RULING NEEDED** (rules: no council record found for D-03)", out)
        self.assertIn("It waits for a council ruling, not for you: there is no council record found for D-03.", out)

    def test_an_owner_seam_is_reported_and_does_not_wait(self):
        (self.research / "council").mkdir()
        (self.research / "council" / "D-03.md").write_text("# D-03\n")
        self.add("rulebook/R-03.md", "# R-03 Emission\n")
        self.add("contracts/check", PASS, executable=True)
        self.add("contracts/interfaces/IEmission.sol", "interface IEmission {\n  function emission(uint256 round) external view returns (uint256);\n}\n")
        self.commit("Add R-03 and its interface\n\nCouncil: D-03")
        _, out = self.dev("review", "--base", "HEAD~1")
        self.assertIn("agent review, then reported to the owner** (interfaces)", out)
        self.assertIn("It does not wait for the owner", out)
        self.assertIn("The owner reads it afterwards and keeps or changes it.", out)
        self.assertIn("The change to **interfaces**", out)

    def test_calculator_work_needs_no_owner(self):
        self.add("calculator/check", PASS, executable=True)
        self.add("calculator/calc.py", "print(1)\n")
        self.commit("Start the calculator")
        code, out = self.dev("review", "--base", "HEAD~1")
        self.assertEqual(code, 0, out)
        self.assertIn("**Implements the frozen rules.**", out)
        self.assertIn("Routing: agent review only", out)
        self.assertIn("Nothing. No owner-routed seam changed", out)
        self.assertIn("Routing: agent review only", out)

    def test_a_named_decision_is_reported_to_the_owner_even_on_agent_seams(self):
        self.add("calculator/check", PASS, executable=True)
        self.add("calculator/calc.py", "print(1)\n")
        self.commit("Round down\n\nNeeds-owner: Confirm dust is left unminted (D-07).")
        _, out = self.dev("review", "--base", "HEAD~1")
        self.assertIn("reported to the owner** (a decision named by the commits)", out)
        self.assertIn("1. Confirm dust is left unminted (D-07).", out)

    def test_a_change_cannot_hide_itself_by_editing_the_seam_map(self):
        seams = self.repo / "SEAMS.md"
        row = "| factory | The checks, this map and the guides agents work from |"
        text = seams.read_text()
        start = text.index(row)
        end = text.index("\n", start)
        line = text[start:end]
        self.assertIn("| owner | no |", line)
        seams.write_text(text[:start] + line.replace("| owner | no |", "| agent | no |") + text[end:])
        self.commit("Quietly make the factory agent-reviewed")
        _, out = self.dev("review", "--base", "HEAD~1")
        self.assertIn("reported to the owner** (factory (owner-routed at the base))", out)

    def test_an_owner_seam_change_without_an_explanation_is_asked_for_one(self):
        self.add("deployments/local.json", "{}\n")
        self.commit("Manifest")
        _, out = self.dev("review", "--base", "HEAD~1")
        self.assertIn("The commits do not say what this changes for a participant", out)

    def test_the_report_does_not_run_component_checks_unless_asked(self):
        self.add("calculator/check", FAIL, executable=True)
        self.commit("A calculator whose check fails")
        code, out = self.dev("review", "--base", "HEAD~1")
        self.assertEqual(code, 0, out)
        self.assertIn("| calculator/check | not_run (not asked for", out)
        self.assertNotIn("boom", out)
        _, out = self.dev("review", "--base", "HEAD~1", "--checks")
        self.assertIn("| calculator/check | failed |", out)

    def test_a_freeze_mixed_with_implementation_is_called_out(self):
        self.add("vectors/first.json", "{}\n")
        self.add("calculator/check", PASS, executable=True)
        self.commit("Examples and code together")
        _, out = self.dev("review", "--base", "HEAD~1")
        self.assertIn("should be separate changes", out)

    def test_money_seam_work_is_reported_to_the_owner_without_being_a_freeze(self):
        self.add("contracts/check", PASS, executable=True)
        self.add("contracts/src/money/Minter.sol", "contract Minter {}\n")
        self.commit("Start the minter")
        _, out = self.dev("review", "--base", "HEAD~1")
        self.assertIn("**Implements the frozen rules.**", out)
        self.assertIn("reported to the owner** (money)", out)
        self.assertIn("**Checkpoint: `", out)
        self.assertIn("The change to **money**", out)

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
        self.assertIn("reported to the owner** (deployment)", out)

    def test_heavy_checks_are_listed_as_not_run(self):
        self.add("calculator/check", PASS, executable=True)
        self.add("programs/check", PASS, executable=True)
        self.commit("Calculator and programs checks")
        _, out = self.dev("review", "--base", "HEAD~1")
        self.assertIn("| programs/qualify (heavy checks) | `not_run` |", out)
        self.assertIn("| calculator/qualify | `n/a`: no heavy checks:", out)

    def test_an_unknown_base_is_a_usage_error(self):
        code, _ = self.dev("review", "--base", "no-such-branch")
        self.assertEqual(code, 64)


class Qualify(Fixture):
    def test_with_no_heavy_checks_it_reports_not_run(self):
        code, out = self.dev("qualify")
        self.assertEqual(code, 2, out)
        self.assertIn("qualify: not_run", out)

    def test_a_clean_passing_run_is_recorded_and_shown_by_the_report(self):
        self.add("programs/check", PASS, executable=True)
        self.add("programs/qualify", PASS, executable=True)
        self.commit("Programs with heavy checks")
        code, out = self.dev("qualify")
        self.assertEqual(code, 0, out)
        record = json.loads((self.repo / ".cache/dev/qualify.json").read_text())
        self.assertEqual(record["commit"], self.git("rev-parse", "HEAD").strip())
        _, report = self.dev("review", "--base", "HEAD~1")
        self.assertIn("| programs/qualify | `passed` on this commit |", report)

    def test_the_recorded_report_gives_the_reason_a_check_is_n_a(self):
        self.add("calculator/check", PASS, executable=True)
        self.add("programs/check", PASS, executable=True)
        self.add("programs/qualify", PASS, executable=True)
        self.commit("Calculator, and programs with heavy checks")
        code, out = self.dev("qualify")
        self.assertEqual(code, 0, out)
        _, report = self.dev("review", "--base", "HEAD~1")
        self.assertIn("| calculator/qualify | `n/a` on this commit: no heavy checks:", report)

    def test_a_record_from_before_reasons_were_kept_still_reads(self):
        self.add("programs/check", PASS, executable=True)
        self.add("programs/qualify", PASS, executable=True)
        self.commit("Programs with heavy checks")
        record = self.repo / ".cache/dev/qualify.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({"commit": self.git("rev-parse", "HEAD").strip(),
                                      "results": [["programs/qualify", "passed"]]}))
        code, report = self.dev("review", "--base", "HEAD~1")
        self.assertIn("| programs/qualify | `passed` on this commit |", report)

    def test_an_uncommitted_tree_keeps_no_record(self):
        self.add("programs/check", PASS, executable=True)
        self.add("programs/qualify", PASS, executable=True)
        code, out = self.dev("qualify")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.repo / ".cache/dev/qualify.json").exists())

    def test_a_passing_heavy_check_still_shows_the_parts_it_did_not_run(self):
        self.add("programs/check", PASS, executable=True)
        self.add("programs/qualify", "#!/bin/sh\necho 'runs: 3s'\necho 'static analysis: not_run (nothing installed)'\nexit 0\n", executable=True)
        code, out = self.dev("qualify")
        self.assertEqual(code, 0, out)
        self.assertIn("passed   programs/qualify", out)
        self.assertIn("static analysis: not_run (nothing installed)", out)
        self.assertNotIn("runs: 3s", out)

    def test_a_failing_heavy_check_fails(self):
        self.add("programs/check", PASS, executable=True)
        self.add("programs/qualify", FAIL, executable=True)
        code, out = self.dev("qualify")
        self.assertEqual(code, 1, out)

    def test_a_folder_with_no_heavy_checks_is_shown_with_its_reason(self):
        self.add("calculator/check", PASS, executable=True)
        self.add("programs/check", PASS, executable=True)
        self.add("programs/qualify", PASS, executable=True)
        code, out = self.dev("qualify")
        self.assertEqual(code, 0, out)
        self.assertIn("n/a      calculator/qualify\n         no heavy checks: its fast check already runs every shared example", out)
        self.assertNotIn("passed   calculator/qualify", out)

    def test_a_folder_with_code_and_no_qualify_is_not_run(self):
        self.add("calculator/check", PASS, executable=True)
        self.add("programs/check", PASS, executable=True)
        code, out = self.dev("qualify")
        self.assertEqual(code, 2, out)
        self.assertIn("not_run  programs/qualify\n         programs/ holds code but has no qualify script", out)

    def test_a_qualify_script_in_a_folder_said_to_have_none_fails(self):
        self.add("calculator/check", PASS, executable=True)
        self.add("calculator/qualify", PASS, executable=True)
        self.add("programs/check", PASS, executable=True)
        self.add("programs/qualify", PASS, executable=True)
        code, out = self.dev("qualify")
        self.assertEqual(code, 1, out)
        self.assertIn("failed   calculator/qualify", out)


class Doctor(Fixture):
    """./dev doctor holds each tool to the version tools/versions pins."""

    def setUp(self):
        super().setUp()
        # Foundry is needed now where contracts/ exists, Go where programs/ does.
        (self.repo / "contracts").mkdir()
        (self.repo / "programs").mkdir()

    def pinned(self, name):
        for line in (ROOT / "tools" / "versions").read_text().splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1]
        self.fail(f"tools/versions pins no {name}")

    def tools(self, **says):
        """A PATH that is one folder: the real git, python3 and dirname
        ./dev needs, and fake tools that print what they are told to. A
        value of the form "exit N" makes the fake fail with that code."""
        bin_ = self.tmp / "bin"
        shutil.rmtree(bin_, ignore_errors=True)
        bin_.mkdir()
        for real in ("git", "python3", "dirname"):
            (bin_ / real).symlink_to(shutil.which(real))
        for name, text in says.items():
            if text is None:
                continue
            body = f"echo 'it broke' >&2\n{text}" if text.startswith("exit ") else f"echo '{text}'"
            (bin_ / name).write_text(f"#!/bin/sh\n{body}\n")
            (bin_ / name).chmod(0o755)
        return str(bin_)

    def fine(self, **change):
        """The tools ./dev verify needs, each at its pinned version."""
        foundry = self.pinned("FOUNDRY_VERSION")
        tools = {"go": f"go{self.pinned('GO_VERSION')}", "gofmt": "package p",
                 **{t: f"{t} Version: {foundry}" for t in ("forge", "anvil", "cast")}}
        return self.tools(**{**tools, **change})

    def test_the_pinned_versions_pass(self):
        code, out = self.dev("doctor", path=self.fine())
        self.assertEqual(code, 0, out)
        self.assertIn(f"ok       forge: {self.pinned('FOUNDRY_VERSION')} (pinned)", out)
        self.assertIn(f"ok       cast: {self.pinned('FOUNDRY_VERSION')} (pinned)", out)
        self.assertIn("ok       gofmt: runs", out)

    def test_another_foundry_version_fails(self):
        for tool in ("forge", "anvil", "cast"):
            code, out = self.dev("doctor", path=self.fine(**{tool: f"{tool} Version: 0.0.1"}))
            self.assertEqual(code, 1, out)
            self.assertIn(f"WRONG    {tool}: 0.0.1, but tools/versions pins {self.pinned('FOUNDRY_VERSION')}", out)

    def test_another_go_version_fails(self):
        code, out = self.dev("doctor", path=self.fine(go="go1.0.0"))
        self.assertEqual(code, 1, out)
        self.assertIn("WRONG    go: 1.0.0", out)

    def test_saved_go_settings_do_not_break_the_go_probe(self):
        # A go env -w GOROOT=<a removed folder> breaks any go that reads it.
        path = self.fine()
        go = Path(path) / "go"
        go.write_text("#!/bin/sh\n"
                      '[ "$GOENV" = off ] || { echo "go: cannot find GOROOT directory: /gone" >&2; exit 2; }\n'
                      f"echo go{self.pinned('GO_VERSION')}\n")
        code, out = self.dev("doctor", path=path)
        self.assertEqual(code, 0, out)
        self.assertIn(f"ok       go: {self.pinned('GO_VERSION')} (pinned)", out)

    def test_a_missing_tool_that_is_needed_now_fails(self):
        for tool in ("forge", "anvil", "cast", "go", "gofmt"):
            code, out = self.dev("doctor", path=self.fine(**{tool: None}))
            self.assertEqual(code, 1, out)
            self.assertIn(f"MISSING  {tool}: needed now.", out)

    def test_a_version_command_that_fails_fails(self):
        for tool in ("forge", "cast", "go", "gofmt", "slither"):
            code, out = self.dev("doctor", path=self.fine(**{tool: "exit 3"}))
            self.assertEqual(code, 1, out)
            self.assertIn(f"BROKEN   {tool}: `", out)
            self.assertIn("exited 3: it broke", out)

    def test_a_missing_slither_is_noted_and_another_version_fails(self):
        code, out = self.dev("doctor", path=self.fine())
        self.assertEqual(code, 0, out)
        self.assertIn("absent   slither", out)
        code, out = self.dev("doctor", path=self.fine(slither="0.0.1"))
        self.assertEqual(code, 1, out)
        self.assertIn("WRONG    slither: 0.0.1", out)


class Demo(Fixture):
    def test_before_it_is_built_the_demo_says_so(self):
        code, out = self.dev("demo")
        self.assertEqual(code, 2, out)
        self.assertIn("not built yet", out)


if __name__ == "__main__":
    unittest.main()
