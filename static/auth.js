'use strict';
let authMode='login', activeUser=null, registrationEnabled=false;
function showAuth(){
 activeUser=null;token='';result=null;selection.clear();quantities.clear();stocks={};policies={};scenarioVersion++;
 document.querySelectorAll('dialog[open]').forEach(d=>d.close());
 $('orderHistory').innerHTML='<p class="empty">Утверждённых заказов пока нет.</p>';$('notice').hidden=true;
 ['orders','scenario','quality','history','method'].forEach(k=>$(k+'View').hidden=k!=='orders');
 document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view==='orders'));
 document.body.classList.add('signed-out');$('authScreen').hidden=false;$('authPassword').value='';
 $('authSubmit').disabled=false;
}
function setAuthMode(mode){
 if(mode==='register'&&!registrationEnabled)return;
 authMode=mode;const register=mode==='register';$('nameLabel').hidden=!register;$('authName').required=register;
 $('authPassword').autocomplete=register?'new-password':'current-password';
 $('authTitle').textContent=register?'Начнём с знакомства.':'С возвращением.';
 $('authSubtitle').textContent=register?'Создайте аккаунт для планирования закупок.':'Войдите, чтобы продолжить планирование.';
 $('loginTab').classList.toggle('selected',!register);$('registerTab').classList.toggle('selected',register);
 $('authSubmit').innerHTML=register?'Создать аккаунт <span>→</span>':'Войти в пространство <span>→</span>';$('authError').hidden=true;
}
async function enterWorkspace(user,csrf){
 activeUser=user;token=csrf;document.body.classList.remove('signed-out');$('authScreen').hidden=true;
 $('userName').textContent=user.name;$('userAvatar').textContent=user.name.slice(0,1).toUpperCase();$('responsible').value=user.name;
 $('authPassword').value='';
 await recalculate();
}
async function loadOrders(){
 try{const response=await fetch('/api/orders');if(response.status===401){showAuth();return;}const data=await response.json();if(!response.ok)throw new Error(data.error);
 $('orderHistory').innerHTML=data.orders.length?data.orders.map(o=>`<article class="history-order"><div class="history-icon">↗</div><div><h2>${esc(o.suppliers.join(' · '))}</h2><p>${esc(o.created.replace('T',' '))} · ${o.lines} позиций · ${esc(o.responsible)}</p><span class="row-tag">${o.mode==='demo'?'Демо-набор':'Данные партнёра'}</span></div><a class="secondary" href="/api/export?id=${encodeURIComponent(o.id)}" download>Скачать CSV ↓</a></article>`).join(''):'<div class="empty"><h2>Здесь появится ваш первый заказ.</h2><p>Проверьте рекомендации, выберите позиции и утвердите выгрузку.</p><button class="secondary" data-view="orders">К рекомендациям →</button></div>';
 }catch(e){$('orderHistory').innerHTML=`<p class="empty">${esc(e.message)}</p>`;}
}
$('loginTab').onclick=()=>setAuthMode('login');$('registerTab').onclick=()=>setAuthMode('register');
$('authForm').onsubmit=async e=>{
 e.preventDefault();$('authSubmit').disabled=true;$('authError').hidden=true;
 try{const response=await fetch('/api/auth/'+authMode,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:$('authEmail').value,name:$('authName').value,password:$('authPassword').value})});const data=await response.json();if(!response.ok)throw new Error(data.error||'Не удалось войти');await enterWorkspace(data.user,data.token);}
 catch(error){$('authError').textContent=error.message;$('authError').hidden=false;}
 finally{$('authSubmit').disabled=false;}
};
$('logout').onclick=async()=>{try{await api('/api/auth/logout',{});showAuth();setAuthMode('login');}catch(e){notify(e.message,true);}};
$('refreshOrders').onclick=loadOrders;
(async()=>{try{
 const configResponse=await fetch('/api/config');if(!configResponse.ok)throw new Error('Не удалось загрузить настройки входа');
 const config=await configResponse.json();registrationEnabled=config.registration_enabled===true;
 uploadLimit=Number(config.max_upload_bytes)||40_000_000;
 $('registerTab').hidden=!registrationEnabled;
 document.querySelector('.auth-local').textContent=config.hosted?'Закрытое пространство команды 7-Solutions. Доступ выдаёт администратор команды.':'Локальное пространство команды 7-Solutions. Аккаунт создаётся на этом компьютере. Email не проверяется письмом.';
 const response=await fetch('/api/auth/me');if(response.status===401){showAuth();return;}const data=await response.json();if(!response.ok)throw new Error(data.error);await enterWorkspace(data.user,data.token);
 }catch(e){showAuth();$('authError').textContent='Не удалось подключиться: '+e.message;$('authError').hidden=false;}})();
