"""Isolated inventory stress test. No mutation, approval or supplier action."""
from datetime import date, timedelta
import math


def simulate(row, options, shock):
    if not isinstance(shock, dict):
        raise ValueError('Сценарий должен быть объектом')
    delay = shock.get('delay_days', 10)
    uplift = shock.get('demand_pct', 30)
    for name, value, maximum in [('Задержка', delay, 60), ('Рост спроса', uplift, 200)]:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= maximum:
            raise ValueError(name + ': недопустимое значение')
    if int(delay) != delay:
        raise ValueError('Задержка задаётся целым числом дней')
    delay = int(delay)
    asof = date.fromisoformat(options.get('as_of', '2026-09-23'))
    demand = row['daily_demand']
    lead = int(options.get('lead_days', 30))
    horizon = len(demand)

    def run(late, multiplier, rescue=0, rescue_day=None):
        receipts = {}
        for arrival in row['arrivals']:
            offset = (date.fromisoformat(arrival['eta'])-asof).days
            # Overdue arrivals are excluded, even if shifting would move them into the future.
            if offset >= 0:
                receipts[offset+late] = receipts.get(offset+late, 0) + max(0, arrival['quantity'])
        receipts[lead+late] = receipts.get(lead+late, 0) + row['quantity']
        stock, unmet, days, first = row['stock'], 0, 0, None
        timeline = []
        for d, base in enumerate(demand):
            arriving = receipts.get(d, 0) + (rescue if d == rescue_day else 0)
            need = base * multiplier
            available = stock + arriving
            missing = max(0, need-available)
            stock = max(0, available-need)
            unmet += missing
            if missing > 1e-8:
                days += 1
                if first is None:
                    first = d
            timeline.append(dict(date=(asof+timedelta(days=d)).isoformat(), stock=round(stock, 2),
                                 demand=round(need, 2), receipt=round(arriving, 2), unmet=round(missing, 2)))
        return dict(timeline=timeline, unmet=round(unmet, 2), shortage_days=days,
                    first_day=first, first_date=(asof+timedelta(days=first)).isoformat() if first is not None else None,
                    end_stock=round(stock, 2)), unmet

    baseline, _ = run(0, 1)
    stressed, missing = run(delay, 1+uplift/100)
    blocked = list(row['blockers'])
    pack = row['pack']
    rescue = math.ceil(max(missing, row['minimum'])/pack)*pack if missing > 1e-8 and pack > 0 and not blocked else 0
    protected, _ = run(delay, 1+uplift/100, rescue, stressed['first_day'])
    return dict(id=row['id'], horizon=horizon, delay_days=delay, demand_pct=uplift,
                baseline=baseline, stressed=stressed, protected=protected,
                rescue_quantity=rescue, rescue_date=stressed['first_date'] if rescue else None,
                blockers=blocked, proposed_order=row['quantity'],
                proposed_eta=(asof+timedelta(days=lead+delay)).isoformat())
