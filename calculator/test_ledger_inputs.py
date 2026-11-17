"""Malformed demo ledgers must fail before they can be reproduced."""
import copy
import json
from pathlib import Path
import unittest

from reference import Parameters, ledger


class LedgerInputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        document = json.loads((Path(__file__).resolve().parent.parent /
                               'vectors/october-main-text-v1.json').read_text())
        cls.parameters = Parameters.from_strings(document['parameters'])
        cls.inputs = next(c['inputs'] for c in document['cases']
                          if c['id'] == 'ledger-replay')

    def reject(self, inputs, message):
        with self.assertRaisesRegex(ValueError, 'ledger input error.*' + message):
            ledger(inputs, self.parameters)

    def test_round_decreases(self):
        inputs = copy.deepcopy(self.inputs)
        inputs['steps'][1]['round'] = '1'
        self.reject(inputs, 'round decreases')

    def test_ticket_details_change(self):
        for field, value in [('payer', 'Q'), ('node', 'M'),
                             ('face_micro_usdc', '10002'), ('created_round', '1')]:
            with self.subTest(field=field):
                inputs = copy.deepcopy(self.inputs)
                inputs['steps'][-1][field] = value
                self.reject(inputs, 'ticket details changed')

    def test_refused_ticket_details_change(self):
        inputs = copy.deepcopy(self.inputs)
        inputs['steps'][1]['deposit_micro_usdc'] = '0'
        inputs['steps'][-1]['face_micro_usdc'] = '10002'
        self.reject(inputs, 'ticket details changed')

    def test_observe_requires_node(self):
        for node in [{}, {'node': None}, {'node': ''}]:
            with self.subTest(node=node):
                inputs = copy.deepcopy(self.inputs)
                inputs['steps'].append(dict(step='5', round='2', action='observe',
                                            payer='P', **node))
                self.reject(inputs, 'observe requires a node')

    def test_bond_requires_owned_lpt(self):
        with self.assertRaisesRegex(ValueError, 'bond exceeds holder LPT'):
            ledger({'steps': [
                dict(step='1', round='1', action='register', node='N'),
                dict(step='2', round='1', action='bond', node='N', holder='H', amount_wei='1')
            ]}, self.parameters)

    def test_earning_round_starts_at_one(self):
        with self.assertRaisesRegex(ValueError, 'earning rounds start at one'):
            ledger({'steps': [dict(step='1', round='1', action='mint_shares', caller='K', earned_round='0')]}, self.parameters)


if __name__ == '__main__':
    unittest.main()
