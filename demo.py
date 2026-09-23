"""Clearly marked synthetic acceptance dataset, isolated from partner records."""
import calendar
from engine import detect_outliers


def dataset():
    items=[]
    names=['Автоматический выключатель 16А','Кабель силовой 3×2,5','Розетка с заземлением','Светильник LED 36Вт','Контактор модульный','Коробка монтажная']
    season=[.7,.75,.9,1,1.05,1.1,1.2,1.25,1.3,1.15,.9,.7]
    for idx,name in enumerate(names):
        daily=3+idx*2
        sales={}
        tx=[]
        for year in (2024,2025,2026):
            for month in range(1,13 if year<2026 else 10):
                days=calendar.monthrange(year,month)[1]
                qty=round(daily*days*season[month-1]*(1+.12*(year-2024)))
                key=f'{year}-{month:02d}'
                sales[key]=qty
                for d in (2,5,9,13,17,21,25,28):
                    tx.append(dict(date=f'{key}-{d:02d}',document=f'{key}-{d}',customer_id=f'client-{d%3}',quantity=qty/8))
        if idx==0:
            sales['2026-07']+=10000
            tx.append(dict(date='2026-07-15',document='PROJECT-ONE-OFF',customer_id='one-off',quantity=10000))
        stockout={}
        if idx==1:
            for k in ['2026-04','2026-05','2026-06']:
                sales[k]/=2
                stockout[k]=15
        supplier='Systeme Electric' if idx%2==0 else 'IEK'
        it=dict(id=f'demo:{idx}',supplier=supplier,code=f'DEMO-{idx+1:03d}',sku=f'DEMO-{idx+1:03d}',
            name=name,unit='м' if idx==1 else 'шт',category=str(idx%3+1),sales=sales,
            stock=20 if idx<3 else 1000,stock_kind='current',stock_date='2026-09-23',pack=10,
            minimum=10,stockout_days=stockout,seasonality=season,anomalies=detect_outliers(tx),
            inbound=[dict(eta='2026-10-01',quantity=120)] if idx==2 else [],warnings=[])
        items.append(it)
    return dict(items=items,meta=dict(demo=True,as_of='2026-09-23',sources=[],transactions=0,
                notes=['Синтетический набор для проверки сценариев. Не является данными Электрокомплект.']))
