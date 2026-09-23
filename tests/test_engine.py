import copy
import unittest
from engine import calculate_item, detect_outliers
from demo import dataset
from importer import validate_normalized


class ReplenishmentTests(unittest.TestCase):
    def setUp(self):
        self.item=dataset()['items'][0]
        self.options=dict(as_of='2026-09-23',lead_days=30,review_days=14,safety_days=14)

    def calc(self,item=None,**options):
        return calculate_item(item or self.item,{**self.options,**options})

    def test_one_off_spike_does_not_inflate_order(self):
        clean=copy.deepcopy(self.item)
        clean['sales']['2026-07']-=10000
        clean['anomalies']=[]
        before=self.calc(clean)['quantity']; after=self.calc()['quantity']
        self.assertLess(abs(after-before)/max(1,before),.05)
        self.assertGreater(self.calc(exclude_outliers=False)['quantity'],after*2)

    def test_inbound_reduces_order(self):
        item=copy.deepcopy(self.item)
        item['inbound']=[dict(eta='2026-10-01',quantity=100)]
        self.assertLess(self.calc(item)['quantity'],self.calc()['quantity'])

    def test_late_and_overdue_arrivals_not_deducted(self):
        item=copy.deepcopy(self.item)
        item['inbound']=[dict(eta='2027-01-01',quantity=99999),dict(eta='2026-09-01',quantity=99999)]
        result=self.calc(item)
        self.assertEqual(result['inbound'],0)
        self.assertEqual(result['quantity'],self.calc()['quantity'])
        self.assertEqual(result['overdue'],99999)

    def test_stockout_compensation(self):
        item=dataset()['items'][1]
        on=self.calc(item,compensate_stockout=True)
        off=self.calc(item,compensate_stockout=False)
        self.assertGreater(on['forecast'],off['forecast'])
        self.assertGreater(on['lost'],0)

    def test_no_fabricated_stockout(self):
        self.assertEqual(self.calc()['lost'],0)

    def test_seasonality_changes_future_demand(self):
        winter=self.calc(as_of='2027-01-01')['daily']
        summer=self.calc(as_of='2026-08-01')['daily']
        self.assertGreater(summer,winter)

    def test_growth_forecast_and_category_are_used(self):
        standard=self.calc()
        self.assertGreater(self.calc(growth_pct=20)['quantity'],standard['quantity'])
        self.assertGreater(self.calc(category_policy={'1':2})['quantity'],standard['quantity'])
        item=copy.deepcopy(self.item);item['growth_forecast']=.4
        self.assertGreater(self.calc(item)['quantity'],standard['quantity'])

    def test_override_stock_reduces_quantity(self):
        self.assertEqual(self.calc(stock_overrides={self.item['id']:99999})['quantity'],0)

    def test_unknown_stock_requires_review(self):
        item=copy.deepcopy(self.item);item.update(stock=None,stock_kind='missing')
        self.assertTrue(self.calc(item)['blockers'])
        self.assertFalse(self.calc(item,stock_overrides={item['id']:0})['blockers'])

    def test_pack_rounding_and_minimum_are_distinct(self):
        item=copy.deepcopy(self.item);item.update(pack=12,minimum=500)
        result=self.calc(item)
        self.assertEqual(result['quantity']%12,0)
        self.assertGreaterEqual(result['quantity'],500)

    def test_split_invoice_detected_and_recurring_customer_kept(self):
        tx=[dict(date=f'2026-06-{d:02d}',document=str(d),quantity=10,customer_id='a') for d in range(1,21)]
        tx.extend([dict(date='2026-07-01',document='large',quantity=100,customer_id='project')]*10)
        anomalies=detect_outliers(tx)
        self.assertEqual(len(anomalies),1)
        self.assertEqual(anomalies[0]['quantity'],1000)
        tx.extend([dict(date=f'2026-07-{d:02d}',document=str(d)+'large',quantity=1000,customer_id='project') for d in (2,3)])
        self.assertEqual(detect_outliers(tx),[])

    def test_immediate_shortage_not_hidden_by_late_receipt(self):
        item=copy.deepcopy(self.item);item['stock']=0
        item['inbound']=[dict(eta='2026-10-15',quantity=10000)]
        result=self.calc(item)
        self.assertEqual(result['quantity'],0)
        self.assertEqual(result['urgency'],'Дефицит')
        self.assertEqual(result['shortage_day'],0)

    def test_unit_uncertainty_blocks_approval(self):
        item=copy.deepcopy(self.item);item['unit_review']=True
        self.assertTrue(self.calc(item)['blockers'])

    def test_invalid_import_rejected(self):
        data=dataset();data['items'].append(copy.deepcopy(data['items'][0]))
        with self.assertRaises(ValueError):validate_normalized(data)


if __name__=='__main__':unittest.main()
