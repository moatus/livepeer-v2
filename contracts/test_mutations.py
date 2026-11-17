"""contracts/mutations.py counts a mutant as caught only on a test that ran."""
import importlib.util, pathlib, unittest

spec = importlib.util.spec_from_file_location("mutations", pathlib.Path(__file__).with_name("mutations.py"))
mutations = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mutations)

FAILED = """Ran 1 test for test/Ledger.t.sol:LedgerTest
[FAIL: assertion failed: 1 != 2] test_ledger_release_x() (gas: 1234)
Suite result: FAILED. 0 passed; 1 failed; 0 skipped
"""
PASSED = """Ran 1 test for test/Ledger.t.sol:LedgerTest
[PASS] test_ledger_release_x() (gas: 1234)
Suite result: ok. 1 passed; 0 failed; 0 skipped
"""


class TestOutcome(unittest.TestCase):
    def test_a_failing_test_that_ran_is_a_catch(self):
        self.assertEqual(mutations.test_outcome(FAILED), (True, True))

    def test_a_passing_test_is_ran_and_not_a_catch(self):
        self.assertEqual(mutations.test_outcome(PASSED), (False, True))

    def test_a_mutant_that_does_not_compile_is_not_ran(self):
        out = "Compiling 40 files\nError: Compiler run failed:\nError (2314): Expected expression.\n"
        self.assertEqual(mutations.test_outcome(out), (False, False))

    def test_a_tool_error_before_any_test_is_not_ran(self):
        out = "Error: failed to decode solc output: EOF while parsing a value\n"
        self.assertEqual(mutations.test_outcome(out), (False, False))

    def test_a_pattern_that_matches_no_test_is_not_ran(self):
        out = "Warning: no tests match the provided pattern:\n\tmatch-test: `nothing`\n"
        self.assertEqual(mutations.test_outcome(out), (False, False))


if __name__ == "__main__":
    unittest.main()
