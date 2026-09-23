import copy
import math
import unittest
from scenario import simulate
from engine import calculate_item
from demo import dataset


class ScenarioTests(unittest.TestCase):
    def row(self, **changes):
        row=dict(stock=20, quantity=80, pack=10, minimum=0, blockers=[], arrivals=[],
                 id='synthetic', daily_demand=[10]*10)
        row.update(changes)
        return row

    def test_delay_exposes_gap_and_rescue_covers_it(self):
        s=simulate(self.row(), {'lead_days':2}, {'delay_days':3,'demand_pct':0})
        self.assertEqual(s['baseline']['unmet'],0)
        self.assertEqual(s['stressed']['unmet'],30)
        self.assertEqual(s['stressed']['first_date'],'2026-09-25')
        self.assertEqual(s['rescue_quantity'],30)
        self.assertEqual(s['protected']['shortage_days'],0)

    def test_zero_shock_equals_baseline_without_mutation(self):
        row=self.row(); before=copy.deepcopy(row)
        s=simulate(row,{'lead_days':2},{'delay_days':0,'demand_pct':0})
        self.assertEqual(s['baseline'],s['stressed'])
        self.assertEqual(s['rescue_quantity'],0)
        self.assertEqual(row,before)

    def test_unknown_inputs_block_rescue(self):
        s=simulate(self.row(blockers=['Уточните остаток']),{'lead_days':2},{'delay_days':10,'demand_pct':50})
        self.assertGreater(s['stressed']['unmet'],0)
        self.assertEqual(s['rescue_quantity'],0)
        self.assertEqual(s['stressed'],s['protected'])

    def test_pack_minimum_and_lost_sales_conservation(self):
        s=simulate(self.row(stock=95, quantity=0, minimum=21, pack=10),{}, {'delay_days':0,'demand_pct':0})
        self.assertEqual(s['stressed']['unmet'],5)
        self.assertEqual(s['rescue_quantity'],30)
        self.assertEqual(s['protected']['end_stock'],25)
        self.assertEqual(sum(d['unmet'] for d in s['stressed']['timeline']),5)

    def test_overdue_excluded_and_arrival_at_start_of_day(self):
        row=self.row(stock=0,quantity=0,arrivals=[{'eta':'2026-09-22','quantity':999}, {'eta':'2026-09-23','quantity':10}])
        s=simulate(row,{}, {'delay_days':0,'demand_pct':0})
        self.assertEqual(s['stressed']['first_day'],1)
        self.assertEqual(s['stressed']['unmet'],90)
        delayed=simulate(row,{}, {'delay_days':2,'demand_pct':0})
        self.assertEqual(delayed['stressed']['timeline'][1]['receipt'],0)

    def test_receipt_beyond_horizon_does_not_hide_shortage(self):
        s=simulate(self.row(), {'lead_days':2}, {'delay_days':60,'demand_pct':0})
        self.assertEqual(s['stressed']['unmet'],80)
        self.assertEqual(s['protected']['unmet'],0)

    def test_engine_daily_plan_matches_forecast(self):
        row=calculate_item(dataset()['items'][0],{})
        self.assertAlmostEqual(sum(row['daily_demand']),row['forecast'],places=2)
        self.assertEqual(len(row['daily_demand']),44)

    def test_invalid_scenarios_rejected(self):
        for shock in [None, {'delay_days':1.5}, {'delay_days':-1}, {'delay_days':True}, {'demand_pct':math.nan}, {'demand_pct':201}]:
            with self.subTest(shock=shock),self.assertRaises(ValueError):
                simulate(self.row(),{},shock)


if __name__=='__main__': unittest.main()
