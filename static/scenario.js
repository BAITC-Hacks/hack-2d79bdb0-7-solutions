'use strict';
let scenarioVersion=0;
function invalidateScenario(){scenarioVersion++;$('scenarioState').textContent='Пересчитайте рекомендации';$('scenarioOutput').innerHTML='<div class="empty">Параметры изменились. Обновите расчёт на странице пополнения.</div>';$('runScenario').disabled=true;}
function refreshScenario(){
 const previous=$('scenarioItem').value;
 const rows=result.rows.filter(r=>r.daily>0);
 $('scenarioItem').innerHTML=rows.map(r=>`<option value="${esc(r.id)}">${esc(r.sku||r.code)} · ${esc(r.name)}${r.blockers.length?' · уточнить данные':''}</option>`).join('');
 if(rows.some(r=>r.id===previous))$('scenarioItem').value=previous;
 else if(demo&&rows.some(r=>r.id==='demo:0'))$('scenarioItem').value='demo:0';
 $('runScenario').disabled=!rows.length;
 if(rows.length)runScenario();else{scenarioVersion++;$('scenarioOutput').innerHTML='<div class="empty">Нет товаров с прогнозом спроса. Загрузите данные или включите демо.</div>';$('scenarioState').textContent='Нет данных';}
}
function inventoryChart(s){
 const sets=[s.baseline,s.stressed,s.protected], colors=['#8790ac','#c16c38','#087d73'];
 const max=Math.max(1,...sets.flatMap(r=>r.timeline.map(d=>d.stock))),n=s.horizon;
 const x=i=>48+i*660/Math.max(1,n-1),y=v=>204-v/max*164;
 return `<svg class="inventory-chart" viewBox="0 0 750 254" role="img" aria-label="Остаток по дням: обычный план, стресс и план Б"><text x="48" y="18" class="axis-label">Остаток, учётные единицы</text>${[0,.5,1].map(v=>`<line x1="48" x2="708" y1="${y(v*max)}" y2="${y(v*max)}" class="grid-line"/><text x="42" y="${y(v*max)+4}" text-anchor="end" class="axis-label">${fmt(v*max)}</text>`).join('')}${sets.map((r,j)=>`<polyline points="${r.timeline.map((d,i)=>`${x(i)},${y(d.stock)}`).join(' ')}" fill="none" stroke="${colors[j]}" stroke-width="${j===2?3.5:2.5}" ${j===0?'stroke-dasharray="6 5"':''}/>`).join('')}${[0,Math.floor((n-1)/2),n-1].map(i=>`<text x="${x(i)}" y="230" text-anchor="middle" class="axis-label">${s.baseline.timeline[i].date.slice(5).split('-').reverse().join('.')}</text>`).join('')}</svg>`;
}
async function runScenario(){
 if(!result?.calculation_id||!$('scenarioItem').value)return;
 const version=++scenarioVersion, calc=result.calculation_id, id=$('scenarioItem').value;
 $('scenarioState').textContent='Проверяем…';$('runScenario').disabled=true;
 try{
 const s=await api('/api/scenario',{calculation_id:calc,id,shock:{delay_days:Number($('scenarioDelay').value),demand_pct:Number($('scenarioDemand').value)}});
 if(version!==scenarioVersion||result.calculation_id!==calc)return;
 const r=result.rows.find(r=>r.id===id), unit=esc(r.unit||'шт');
 $('scenarioState').textContent=`${result.mode==='demo'?'Демо · ':''}Горизонт: ${s.horizon} дн. · ${r.sku||r.code}`;
 $('scenarioOutput').innerHTML=`<div class="scenario-kpis"><div><span>Первый дефицит</span><strong>${s.stressed.first_date?s.stressed.first_date.split('-').reverse().join('.'):'Не ожидается'}</strong><small>При заданном сценарии</small></div><div><span>Непокрытый спрос</span><strong>${fmt(s.stressed.unmet)} <small>${unit}</small></strong><small>По плану: ${fmt(s.baseline.unmet)} ${unit}</small></div><div><span>Дней с дефицитом</span><strong>${s.stressed.shortage_days}<small> / ${s.horizon}</small></strong><small>После плана Б: ${s.protected.shortage_days}</small></div></div>${inventoryChart(s)}<div class="inventory-legend"><span class="baseline-key">Обычный план</span><span class="stress-key">Стресс-сценарий</span><span class="protected-key">С планом Б</span></div><div class="rescue-card"><span class="eyebrow">03 / ПОДГОТОВЬТЕ ДЕЙСТВИЕ</span><h2>${s.blockers.length?'Сначала подтвердите исходные данные':s.rescue_quantity?`Запросить срочно ${fmt(s.rescue_quantity)} ${unit}`:'Запас выдерживает этот сценарий'}</h2><p>${s.blockers.length?s.blockers.map(esc).join(' · '):s.rescue_quantity?`Дополнительное поступление не позже ${s.rescue_date.split('-').reverse().join('.')}, до дневного расхода. Покрывает ${fmt(s.stressed.unmet-s.protected.unmet)} ${unit} спроса при этих условиях. Остаток в конце: ${fmt(s.protected.end_stock)} ${unit}.`:'На заданном горизонте дополнительное поступление не требуется.'}</p><small>${s.blockers.length?'График предварительный; неизвестный остаток принят равным нулю.':'Срок и доступность нужно согласовать с поставщиком. Это дополнительная партия, не автоматический заказ.'}</small><button class="text-btn" data-detail="${esc(id)}">Открыть расчёт товара →</button></div><details class="scenario-daily"><summary>Таблица по дням · проверить числа</summary><div class="table-wrap"><table><thead><tr><th>Дата</th><th>Спрос в стрессе</th><th>Поступление</th><th>Остаток</th><th>Нехватка</th><th>С планом Б</th></tr></thead><tbody>${s.stressed.timeline.map((d,i)=>`<tr><td>${d.date}</td><td>${fmt(d.demand)}</td><td>${fmt(d.receipt)}</td><td>${fmt(d.stock)}</td><td>${fmt(d.unmet)}</td><td>${fmt(s.protected.timeline[i].stock)}</td></tr>`).join('')}</tbody></table></div></details>`;
 }catch(e){if(version===scenarioVersion){$('scenarioState').textContent='Не удалось рассчитать';$('scenarioOutput').innerHTML=`<div class="empty">${esc(e.message)}</div>`;}}
 finally{if(version===scenarioVersion)$('runScenario').disabled=!result?.calculation_id;}
}
function scenarioInputs(){scenarioVersion++;$('delayLabel').textContent=$('scenarioDelay').value+' дней';$('demandLabel').textContent='+'+$('scenarioDemand').value+'%';$('scenarioState').textContent='Условия изменены — испытайте план';$('scenarioOutput').innerHTML='<div class="empty">Нажмите «Испытать план», чтобы увидеть результат для новых условий.</div>';$('runScenario').disabled=!result?.calculation_id;}
$('scenarioDelay').oninput=scenarioInputs;$('scenarioDemand').oninput=scenarioInputs;$('scenarioItem').onchange=scenarioInputs;
$('runScenario').onclick=runScenario;
document.addEventListener('click',e=>{const p=e.target.closest('[data-shock]');if(p){const [delay,demand]=p.dataset.shock.split(',');$('scenarioDelay').value=delay;$('scenarioDemand').value=demand;scenarioInputs();runScenario();}});
