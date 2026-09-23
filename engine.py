"""Explainable replenishment engine; quantities are in stock accounting units."""
from datetime import date, timedelta
from statistics import median
import calendar
import math


def num(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default


def month_shift(key, delta):
    y, m = map(int, key.split('-'))
    ix = y * 12 + m - 1 + delta
    return f'{ix // 12:04d}-{ix % 12 + 1:02d}'


def detect_outliers(transactions):
    """Aggregate fragmented invoice lines before scoring. Customer ID is optional.

    Replace isolated exceptional volume with median ordinary invoice volume.
    Repeated large customer purchases (3+ separate dates) are retained as regular.
    """
    grouped = {}
    for t in transactions:
        qty = num(t.get('quantity'))
        if qty <= 0:
            continue
        key = (t['date'][:10], str(t.get('document', '')), str(t.get('customer_id', '')))
        grouped[key] = grouped.get(key, 0) + qty
    vals = list(grouped.values())
    if len(vals) < 8:
        return []
    base = median(vals)
    mad = median(abs(x - base) for x in vals)
    threshold = max(base * 5, base + 6 * 1.4826 * mad, 1)
    recurring = {}
    for (day, doc, customer), qty in grouped.items():
        if customer and qty > threshold:
            recurring.setdefault(customer, set()).add(day)
    return [dict(date=day, document=doc, customer_id=customer or None,
                 quantity=qty, retained=base, removed=qty-base, threshold=round(threshold, 2))
            for (day, doc, customer), qty in grouped.items()
            if qty > threshold and (not customer or len(recurring.get(customer, [])) < 3)]


def seasonal_factors(history, fallback):
    # Only full historical years. Normalize each year to remove trend first.
    by_year = {}
    for key, value in history.items():
        by_year.setdefault(key[:4], {})[int(key[5:])] = max(0, num(value))
    years = [v for v in by_year.values() if len(v) == 12 and sum(v.values()) > 0]
    factors = []
    for m in range(1, 13):
        own = [v[m] / (sum(v.values()) / 12) for v in years]
        prior = max(.15, num(fallback[m-1], 1)) if len(fallback) == 12 else 1
        factor = .65 * median(own) + .35 * prior if own else prior
        factors.append(max(.15, min(4, factor)))
    avg = sum(factors) / 12
    return [v / avg for v in factors]


def calculate_item(item, options):
    asof = date.fromisoformat(options.get('as_of', '2026-09-23'))
    lead = max(1, min(365, num(options.get('lead_days'), 30)))
    review = max(1, min(180, num(options.get('review_days'), 14)))
    safety = max(0, min(180, num(options.get('safety_days'), 14)))
    uplift = max(-90, min(300, num(options.get('growth_pct')))) / 100
    category = str(item.get('category') or 'Не задана')
    # Explicit user-editable business policy, not an inferred meaning of category codes.
    category_multiplier = num(options.get('category_policy', {}).get(category), 1)
    category_multiplier = max(0, min(5, category_multiplier))
    current_month = asof.strftime('%Y-%m')
    history = {k: max(0, num(v)) for k, v in item.get('sales', {}).items() if k < current_month}
    cleaned = dict(history)
    anomalies = [a for a in item.get('anomalies', []) if a['date'][:7] in history]
    if options.get('exclude_outliers', True):
        for a in anomalies:
            key = a['date'][:7]
            cleaned[key] = max(0, cleaned[key] - a['removed'])
    factors = seasonal_factors(cleaned, item.get('seasonality', [1]*12))
    recent_keys = [month_shift(current_month, -i) for i in range(6, 0, -1)]
    available = [k for k in recent_keys if k in cleaned]
    rates = [cleaned[k] / calendar.monthrange(int(k[:4]), int(k[5:]))[1] / factors[int(k[5:])-1]
             for k in available]
    base = median(rates) if rates else 0
    lost = 0
    imputed = {}
    if options.get('compensate_stockout', True):
        for k in list(cleaned):
            days = calendar.monthrange(int(k[:4]), int(k[5:]))[1]
            exact = num(item.get('stockout_days', {}).get(k))
            if exact > 0:
                exact = min(days, exact)
                # Available-day rate or robust seasonal baseline for fully empty months.
                demand = cleaned[k] * days / (days-exact) if exact < days else base * factors[int(k[5:])-1] * days
                addition = max(0, demand-cleaned[k])
                imputed[k] = addition
                cleaned[k] += addition
                lost += addition
    adjusted = [cleaned[k] / calendar.monthrange(int(k[:4]), int(k[5:]))[1] / factors[int(k[5:])-1]
                for k in available]
    # Mean after invoice cleaning and explicit stockout correction preserves lost-demand effect.
    daily = sum(adjusted)/len(adjusted) if adjusted else 0
    ratios = []
    for k in available:
        prev = month_shift(k, -12)
        if num(cleaned.get(prev)) > 0:
            ratios.append(cleaned[k] / cleaned[prev])
    historical_growth = max(.5, min(2, median(ratios))) if len(ratios) >= 3 else 1
    # Baseline already contains the current level: extrapolate only the future horizon.
    trend = historical_growth ** ((lead+review)/365)
    source_growth = item.get('growth_forecast')
    growth = max(.1, min(4, 1+num(source_growth))) if source_growth is not None else trend
    growth *= 1+uplift
    horizon = int(lead+review)
    forecast = sum(daily * factors[(asof+timedelta(days=d)).month-1] * growth for d in range(horizon))
    daily_now = daily * factors[asof.month-1] * growth
    buffer = daily_now * safety * category_multiplier
    overrides = options.get('stock_overrides', {})
    overridden = item['id'] in overrides
    stock = num(overrides[item['id']]) if overridden else num(item.get('stock'))
    cutoff = (asof+timedelta(days=horizon)).isoformat()
    inbound = sum(max(0, num(p['quantity'])) for p in item.get('inbound', [])
                  if asof.isoformat() <= p['eta'] <= cutoff)
    overdue = sum(max(0, num(p['quantity'])) for p in item.get('inbound', []) if p['eta'] < asof.isoformat())
    raw_order = max(0, forecast+buffer-stock-inbound)
    pack = num(item.get('pack'), 1)
    minimum = max(0, num(item.get('minimum'), 0))
    order = math.ceil(max(raw_order, minimum)/pack)*pack if raw_order > 0 and pack > 0 else 0
    warnings = list(item.get('warnings', []))
    blockers = []
    if not item.get('category'):
        warnings.append('Категория не передана; применяется базовый страховой запас')
    if item.get('stock_date') and not overridden:
        stock_date=date.fromisoformat(item['stock_date'])
        if stock_date>asof:
            blockers.append('Остаток относится к дате позже даты расчёта')
        elif (asof-stock_date).days>7:
            warnings.append('Остаток старше 7 дней; рекомендуется обновить')
    if not overridden and item.get('stock_kind') != 'current':
        blockers.append('Уточните текущий свободный остаток')
    if pack <= 0:
        blockers.append('Не определена кратность заказа')
    if item.get('unit_review'):
        blockers.append('Нужно согласовать единицы закупки и учёта')
    if len(available) < 3:
        blockers.append('Недостаточно истории за последние 6 месяцев')
    if overdue:
        warnings.append('Просроченные поступления исключены: уточните статус')
    if not item.get('stockout_days'):
        warnings.append('Нет точных периодов отсутствия товара; компенсация не применена')
    coverage = stock/daily_now if daily_now > 0 else None
    # Time-phased risk before the next newly placed order can arrive.
    balance = stock
    shortage_day = None
    for d in range(int(lead)):
        day = (asof+timedelta(days=d)).isoformat()
        balance += sum(num(p['quantity']) for p in item.get('inbound', []) if p['eta'] == day)
        balance -= daily * factors[(asof+timedelta(days=d)).month-1] * growth
        if balance < -1e-8 and shortage_day is None:
            shortage_day = d
    urgency = 'Дефицит' if shortage_day is not None else ('Пополнить' if order > 0 else 'Достаточно')
    explain = (f'Спрос на {horizon} дн. {forecast:.1f} + страховой запас {buffer:.1f} '
               f'− остаток {stock:.1f} − поступления в срок {inbound:.1f} = {raw_order:.1f}. '
               f'С учётом минимальной партии {minimum:g} и кратности {pack:g}: {order:g} {item.get("unit", "шт")}.')
    if item.get('stock') is None and not overridden:
        explain+=' Остаток неизвестен: предварительно принят 0, утверждение заблокировано.'
    result = {k:item.get(k) for k in ('id','supplier','code','sku','name','unit','category','stock_kind','stock_date','unit_review')}
    result.update(quantity=order, stock=stock, stock_confirmed=overridden or item.get('stock_kind')=='current',
        forecast=round(forecast,2), safety=round(buffer,2), inbound=round(inbound,2),
        overdue=overdue, pack=pack, minimum=minimum, daily=round(daily_now,3),
        coverage=round(coverage,1) if coverage is not None else None,
        urgency=urgency, shortage_day=shortage_day, blockers=blockers, warnings=warnings,
        explanation=explain, outlier_count=len(anomalies),
        removed=round(sum(a['removed'] for a in anomalies),2) if options.get('exclude_outliers',True) else 0,
        lost=round(lost,2), growth_factor=round(growth,3), category_multiplier=category_multiplier,
        seasonality=[round(v,3) for v in factors], anomalies=anomalies[:30],
        history=[dict(month=k, raw=history[k], clean=round(cleaned[k],2), imputed=round(imputed.get(k,0),2)) for k in sorted(history)[-24:]],
        arrivals=item.get('inbound', []),
        daily_demand=[daily * factors[(asof+timedelta(days=d)).month-1] * growth for d in range(horizon)])
    return result


def calculate(dataset, options):
    rows = [calculate_item(i, options) for i in dataset['items']]
    rank = {'Дефицит':0,'Пополнить':1,'Достаточно':2}
    rows.sort(key=lambda r:(rank[r['urgency']], -r['quantity'], r['id']))
    return dict(rows=rows, meta=dataset.get('meta', {}), options=options,
        summary=dict(items=len(rows), order_lines=sum(r['quantity']>0 for r in rows),
                     shortage=sum(r['urgency']=='Дефицит' for r in rows),
                     needs_review=sum(bool(r['blockers']) for r in rows),
                     anomalies=sum(r['outlier_count'] for r in rows)))
