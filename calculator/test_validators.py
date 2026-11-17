"""Membership continuity at round boundaries (R-11 and R-13)."""
import json
from pathlib import Path
import unittest

from reference import Parameters, validators


class ValidatorContinuity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads((Path(__file__).resolve().parent.parent /
            'vectors/october-main-text-v2.json').read_text())
        cls.parameters = Parameters.from_strings(cls.document['parameters'])

    def test_returns_preserve_votes_and_seating_order(self):
        for case in self.document['cases']:
            if case['id'].startswith('validators-return-'):
                with self.subTest(case=case['id']):
                    result = validators(case['inputs'], self.parameters)
                    for observation in result['observed']:
                        observation['score'] = str(observation['score'])
                    self.assertEqual(result, case['expected'])

    def test_absent_round_resets_seating_order(self):
        steps = []
        def add(round, action, node=None):
            steps.append(dict(step=str(len(steps)+1), round=str(round),
                              action=action, node=node, target='X'))
        for node in 'ABC':
            add(5, 'opt_in', node)
        add(6, 'opt_out', 'A')
        add(7, 'opt_in', 'A')
        add(8, 'opt_in', 'E')
        add(9, 'observe')
        result = validators(dict(stakes=[dict(from_round='5', stake_wei={
            'A': '2000', 'B': '2000', 'C': '3000', 'E': '2001'})],
            steps=steps), self.parameters)
        self.assertEqual(result['observed'][0]['members'], ['B', 'C', 'E'])


if __name__ == '__main__':
    unittest.main()
