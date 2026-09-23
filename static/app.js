'use strict';
const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=v=>Number(v).toLocaleString('ru-RU',{maximumFractionDigits:1});
let token='',result=null,demo=false,busy=false,page=0,aiConfigured=false;
const PAGE_SIZE=25;
function notify(text,error=false){$('notice').textContent=text;$('notice').hidden=false;$('notice').classList.toggle('error',error);}
function lock(value){busy=value;document.querySelectorAll('button,input,select').forEach(e=>e.disabled=value);if(!value)render();}
async function request(path,payload,headers={}){
 const r=await fetch(path,{method:'POST',headers:{'X-App-Token':token,'Content-Type':'application/json',...headers},body:payload instanceof ArrayBuffer?payload:JSON.stringify(payload)});
 if(!r.ok){const e=await r.json();throw new Error(e.error||'Не удалось выполнить запрос');}return r;
}
function options(){return {as_of:$('asOf').value,lead_days:Number($('lead').value),review_days:Number($('review').value),safety_days:Number($('safety').value),exclude_outliers:true,compensate_stockout:true};}
function filtered(){const term=$('search').value.toLowerCase();return (result?.rows||[]).filter(r=>r.quantity>0&&(!$('supplier').value||r.supplier===$('supplier').value)&&(!term||[r.sku,r.code,r.name].join(' ').toLowerCase().includes(term))&&(!$('urgency').value||($('urgency').value==='urgent')===(r.shortage_day!==null)));}
function currentPage(){const rows=filtered();page=Math.min(page,Math.max(0,Math.ceil(rows.length/PAGE_SIZE)-1));return rows.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE);}
function reason(r){return [r.ai_explanation,r.explanation.split(' Остаток неизвестен:')[0]].filter(Boolean).join(' ');}
function render(){
 const all=filtered(),rows=currentPage(),orders=(result?.rows||[]).filter(r=>r.quantity>0);
 $('statOrders').textContent=fmt(orders.length);$('statRisk').textContent=fmt(orders.filter(r=>r.shortage_day!==null).length);$('statSuppliers').textContent=fmt(new Set(orders.map(r=>r.supplier)).size);
 $('realMode').classList.toggle('selected',!demo);$('demoMode').classList.toggle('selected',demo);$('demoBanner').hidden=result?.mode!=='demo';$('filteredCount').textContent=fmt(all.length);
 $('rows').innerHTML=rows.map(r=>'<tr><td><strong>'+esc(r.sku||r.code)+'</strong><span class="product-label">'+esc(r.name)+'</span></td><td>'+esc(r.supplier)+'</td><td><span class="qty">'+fmt(r.quantity)+'</span> '+esc(r.unit)+'</td><td class="reason">'+(r.ai_explanation?'<span class="ai-label">OpenAI + расчёт</span>':'')+esc(reason(r))+'</td><td><span class="row-tag '+(r.shortage_day!==null?'risk':'')+'">'+(r.shortage_day!==null?'Срочно':'Планово')+'</span>'+(r.shortage_day!==null?'<span class="product-label">Дефицит через '+fmt(r.shortage_day)+' дн.</span>':'')+'</td></tr>').join('')||'<tr><td colspan="5" class="empty">Нет рекомендаций. Загрузите данные, откройте демо или измените фильтры.</td></tr>';
 $('pageLabel').textContent=all.length?(page*PAGE_SIZE+1)+'–'+Math.min((page+1)*PAGE_SIZE,all.length)+' из '+fmt(all.length):'0 позиций';
 $('prev').disabled=busy||page===0;$('next').disabled=busy||(page+1)*PAGE_SIZE>=all.length;
 $('export').disabled=busy||!all.length||!result?.calculation_id;$('explain').disabled=busy||!rows.length||!result?.calculation_id||!aiConfigured;
 $('explain').title=aiConfigured?'Краткие пояснения для текущей страницы':'Настройте OPENAI_API_KEY в .env и перезапустите сервер';
}
async function recalculate(){if(busy)return;lock(true);$('notice').hidden=true;result=null;try{result=await (await request('/api/calculate',{demo,options:options()})).json();page=0;const supplier=$('supplier').value;$('supplier').innerHTML='<option value="">Все поставщики</option>'+[...new Set(result.rows.map(r=>r.supplier))].sort().map(s=>'<option value="'+esc(s)+'">'+esc(s)+'</option>').join('');$('supplier').value=supplier;demo=result.mode==='demo';}catch(e){notify(e.message,true);}finally{lock(false);}}
for(const id of ['supplier','urgency','search'])$(id).addEventListener(id==='search'?'input':'change',()=>{page=0;render();});
for(const id of ['asOf','lead','review','safety'])$(id).onchange=()=>{if(result)result.calculation_id=null;notify('Нажмите «Рассчитать», чтобы применить параметры.');render();};
$('calculate').onclick=recalculate;$('prev').onclick=()=>{page--;render();};$('next').onclick=()=>{page++;render();};
$('realMode').onclick=()=>{demo=false;recalculate();};$('demoMode').onclick=()=>{demo=true;recalculate();};
$('explain').onclick=async()=>{if(busy)return;const calculation=result.calculation_id;const ids=currentPage().map(r=>r.id);lock(true);notify('OpenAI формирует обоснования…');try{const answer=await(await request('/api/explain',{calculation_id:calculation,ids})).json();for(const r of result.rows)if(answer.explanations[r.id])r.ai_explanation=answer.explanations[r.id];notify(answer.status==='ready'?'Обоснования готовы.':'OpenAI недоступен. Сохранены расчётные обоснования.');}catch(e){notify(e.message,true);}finally{lock(false);}};
$('export').onclick=async()=>{if(busy)return;const ids=filtered().map(r=>r.id);lock(true);try{const r=await request('/api/recommendations/export',{calculation_id:result.calculation_id,ids});const url=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=url;a.download='7-solutions-recommendations.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);notify('Экспортировано рекомендаций: '+ids.length);}catch(e){notify(e.message,true);}finally{lock(false);}};
$('upload').onchange=async e=>{const files=[...e.target.files];if(!files.length)return;if(files.length>1&&files.some(f=>f.name.toLowerCase().endsWith('.json'))){notify('JSON загружайте отдельно.',true);e.target.value='';return;}lock(true);result=null;try{for(const f of files){notify('Загрузка '+f.name+'…');await request('/api/import',await f.arrayBuffer(),{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(f.name)});}demo=false;}catch(e){notify(e.message,true);lock(false);e.target.value='';return;}lock(false);e.target.value='';await recalculate();};
(async()=>{try{const response=await fetch('/api/status');if(!response.ok)throw new Error('Сервер недоступен');const status=await response.json();token=status.token;aiConfigured=status.ai_configured;demo=status.items===0;await recalculate();}catch(e){notify(e.message,true);}})();
