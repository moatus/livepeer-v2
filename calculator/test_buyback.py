"""A buyback case is any run of its five actions, not only the four frozen ones."""
import copy
import json
from pathlib import Path
import unittest

from check_vectors import decimal_strings
from dataclasses import replace

from reference import Parameters, amount_out, buyback, calculate


class BuybackCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        document = json.loads((Path(__file__).resolve().parent.parent /
                               'vectors/october-main-text-v4.json').read_text())
        cls.parameters = Parameters.from_strings(document['parameters'])
        cls.cases = {c['id']: c for c in document['cases'] if c['kind'] == 'buyback'}
        cls.walk = cls.cases['buyback-walk']

    def run_case(self, inputs):
        return decimal_strings(buyback(inputs, self.parameters))

    def test_block_times_shift_nothing(self):
        # A local chain runs at its own clock: only differences in time
        # count, and NOT_DUE names a time, which moves with the clock.
        for shift, case in ((shift, case) for shift in (1_763_800_000,)
                            for case in self.cases.values()):
            with self.subTest(case=case['id'], shift=shift):
                expected = self.run_case(case['inputs'])
                for refusal in expected['refused']:
                    if refusal['code'] == 'NOT_DUE':
                        refusal['args'] = [str(int(refusal['args'][0]) + shift)]
                inputs = copy.deepcopy(case['inputs'])
                for step in inputs['steps']:
                    step['time'] = str(int(step['time']) + shift)
                self.assertEqual(self.run_case(inputs), expected)

    def walk_until(self, last_step, *extra):
        inputs = copy.deepcopy(self.walk['inputs'])
        index = next(i for i, s in enumerate(inputs['steps']) if s['step'] == last_step)
        inputs['steps'] = inputs['steps'][:index + 1] + [dict(s) for s in extra]
        return inputs

    def test_refused_buy_records_nothing(self):
        # Rule 7: step 13's buy is due an observation and refused, so it
        # records none, and a poke at the same time is still due.
        step13 = next(s for s in self.walk['inputs']['steps'] if s['step'] == '13')
        poke = dict(step='13b', round=step13['round'], time=step13['time'], action='poke')
        result = self.run_case(self.walk_until('13', poke))
        self.assertEqual([r['step'] for r in result['refused']][-1], '13')

    def test_not_due_names_the_next_observation(self):
        # Rule 8: NOT_DUE gives O1's time plus the interval, not now's.
        step14 = next(s for s in self.walk['inputs']['steps'] if s['step'] == '14')
        later = int(step14['time']) + 1
        poke = dict(step='14b', round=step14['round'], time=str(later), action='poke')
        result = self.run_case(self.walk_until('14', poke))
        due = int(step14['time']) + self.parameters.buyback_twap_seconds
        self.assertEqual(result['refused'][-1], {'step': '14b', 'code': 'NOT_DUE', 'args': [str(due)]})

    def test_refusal_order(self):
        # Rule 11: a buy refuses ALREADY_BOUGHT before NOTHING_HELD, and
        # NOTHING_HELD before NO_LIQUIDITY; a poke on an empty pool refuses
        # NO_LIQUIDITY.
        first = next(s for s in self.walk['inputs']['steps'] if s['step'] == '6')
        again = dict(first, step='6b')
        result = self.run_case(self.walk_until('6', again))
        self.assertEqual(result['held_micro_usdc'], '0')
        self.assertEqual(result['refused'][-1]['code'], 'ALREADY_BOUGHT')
        empty = {'lpt_supply_wei': '1', 'steps': [
            dict(step='1', round='1', time='0', action='buy'),
            dict(step='2', round='1', time='0', action='poke')]}
        self.assertEqual([r['code'] for r in self.run_case(empty)['refused']],
                         ['NOTHING_HELD', 'NO_LIQUIDITY'])

    def test_buy_before_any_observation(self):
        # Rule 3: with nothing observed a buy refuses TWAP_NOT_READY with
        # elapsed 0, however long the pool has held, and changes nothing.
        interval = self.parameters.buyback_twap_seconds
        steps = [dict(step='1', round='1', time='0', action='seed',
                      usdc_micro_usdc='1000000', lpt_wei='1000000'),
                 dict(step='2', round='1', time='0', action='job', face_micro_usdc='20000')]
        buy = dict(step='3', round='1', time=str(2 * interval), action='buy')
        refused = self.run_case({'lpt_supply_wei': '1', 'steps': steps + [buy]})
        self.assertEqual(refused.pop('refused'),
                         [{'step': '3', 'code': 'TWAP_NOT_READY', 'args': ['0', str(interval)]}])
        untouched = self.run_case({'lpt_supply_wei': '1', 'steps': steps})
        self.assertEqual(untouched.pop('refused'), [])
        self.assertEqual(refused, untouched)

    def pool_walk(self, buy_time):
        # Seed 1,000,000 a side, poke at 0, hold a network share, buy later.
        return {'lpt_supply_wei': str(10 ** 6), 'steps': [
            dict(step='1', round='1', time='0', action='seed',
                 usdc_micro_usdc='1000000', lpt_wei='1000000'),
            dict(step='2', round='1', time='0', action='poke'),
            dict(step='3', round='1', time='0', action='job', face_micro_usdc='20000'),
            dict(step='4', round='1', time=str(buy_time), action='buy')]}

    def test_job_adds_the_network_share(self):
        # R-03: an odd face leaves the floored node cut's remainder to the
        # network, as a settlement case computes it.
        for face in ('1', '3', '1001'):
            with self.subTest(face=face):
                inputs = {'lpt_supply_wei': '1', 'steps': [
                    dict(step='1', round='1', time='0', action='job', face_micro_usdc=face)]}
                settled = calculate('settlement', {'gross_micro_usdc': face, 'payee_registered': True},
                                    self.parameters, {})
                self.assertEqual(buyback(inputs, self.parameters)['held_micro_usdc'],
                                 settled['network_micro_usdc'])

    def test_least_input_and_guarantee(self):
        # Rules 5 and 6 over small pools. 997 is prime, so the division in
        # usdcIn is exact only when it divides ru x lptOut: USDC reserves in
        # multiples of 997 reach that case, where v2's getAmountIn (floor
        # plus 1) would overpay by one. usdcIn is the least input whose
        # output is lptOut, within cap, and the price stays within rule 6.
        s = self.parameters.buyback_max_slippage_ppm
        exact = 0
        for usdc in range(997, 997 * 41, 997):
            for lpt in range(1, 200, 3):
                for face in (2, 50, 400, 4000):
                    inputs = {'lpt_supply_wei': str(10 ** 6), 'steps': [
                        dict(step='1', round='1', time='0', action='seed',
                             usdc_micro_usdc=str(usdc), lpt_wei=str(lpt)),
                        dict(step='2', round='1', time='0', action='poke'),
                        dict(step='3', round='1', time='0', action='job', face_micro_usdc=str(face)),
                        dict(step='4', round='1', time='3600', action='buy')]}
                    result = buyback(inputs, self.parameters)
                    for b in result['bought']:
                        ru, rl, paid, got = usdc, lpt, b['usdc_in_micro_usdc'], b['lpt_out_wei']
                        cap = min(face - face * self.parameters.node_cut_millionths // 10 ** 6,
                                  b['max_in_micro_usdc'])
                        self.assertLessEqual(paid, cap)
                        self.assertEqual(amount_out(paid, ru, rl), got)
                        self.assertLess(amount_out(paid - 1, ru, rl), got)
                        self.assertLess((paid * 10 ** 6) << 112, (got + 1) * b['twap_x112'] * (10 ** 6 + s))
                        exact += 1000 * ru * got % (997 * (rl - got)) == 0
        self.assertGreater(exact, 0, 'no buy met an exact division')

    def test_burn_share(self):
        # Rule 7 at a burn share below 10^6: the rest is not burned.
        half = replace(self.parameters, buyback_burn_ppm=500_000)
        result = buyback(self.walk['inputs'], half)
        burned = [b['burned_wei'] for b in result['bought']]
        self.assertEqual(burned, [b['lpt_out_wei'] * 500_000 // 10 ** 6 for b in result['bought']])
        supply = int(self.walk['inputs']['lpt_supply_wei'])
        self.assertEqual(result['lpt_supply_wei'], supply - sum(burned))

    def reject(self, inputs, message):
        with self.assertRaisesRegex(ValueError, 'buyback input error.*' + message):
            buyback(inputs, self.parameters)

    def test_malformed_steps_fail(self):
        def changed(index, **fields):
            inputs = copy.deepcopy(self.walk['inputs'])
            inputs['steps'][index].update(fields)
            return inputs
        self.reject(changed(6, time='3599'), 'round or time decreases')
        self.reject(changed(8, round='1', time='7199'), 'round or time decreases')
        self.reject(changed(0, round='0'), 'rounds are numbered from 1')
        self.reject(changed(1, action='sell'), 'unknown action')
        self.reject(changed(1, usdc_micro_usdc='1'), 'buy takes no usdc_micro_usdc')
        self.reject(changed(1, step='1'), 'step repeated')
        self.reject(changed(11, step='12b', usdc_micro_usdc='0'), 'must receive some LPT')
        empty = copy.deepcopy(self.walk['inputs'])
        empty['steps'] = [dict(step='1', round='1', time='0', action='swap', usdc_micro_usdc='1')]
        self.reject(empty, 'needs both reserves')


if __name__ == '__main__':
    unittest.main()
