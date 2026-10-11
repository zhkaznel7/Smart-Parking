'use strict';
(() => {
  const {$,escape,money,sessionDuration,time,notice,api,tariffText,renderParkingMap,bindMapControls}=Parking;
  const mode=document.body.dataset.mode;
  let data,filter='all',chosenSpot,loading=false;
  const storage={get(){try{return localStorage.getItem('smart-parking-link');}catch{return null;}},set(link){try{localStorage.setItem('smart-parking-link',link);}catch{}}};
  const saved=storage.get();
  if(saved && /^\/my-parking\/[A-Za-z0-9_-]{43}$/.test(saved)){$('#saved-parking').href=saved;$('#saved-parking').hidden=false;}
  function renderMap(){
    if(!data)return;
    const lot=$('#lot-filter').value;
    const spots=data.spots.filter(s=>!lot||String(s.parking_lot_id)===lot);
    renderParkingMap($('#spot-grid'),spots,{filter,selectedSpot:chosenSpot?.id});
    const matched=spots.filter(s=>filter==='all'||filter==='free'&&!s.is_occupied||filter==='accessible'&&s.is_accessible);
    $('#map-count').textContent=`По фильтру: ${matched.length} · свободно ${matched.filter(s=>!s.is_occupied).length}`;
  }
  async function loadMap(){
    if(loading)return;loading=true;
    try{
      data=await api('/api/spots');
      for(const key of ['total','available','occupied'])$('#'+key).textContent=data.stats[key];
      const selected=$('#lot-filter').value;
      $('#lot-filter').innerHTML='<option value="">Все парковки</option>'+data.lots.map(l=>`<option value="${l.id}">${escape(l.name)}</option>`).join('');
      $('#lot-filter').value=selected;
      $('#free-title').textContent=data.tariff.free_minutes===60?'Первый час бесплатно':`Первые ${data.tariff.free_minutes} мин. бесплатно`;
      $('#paid-description').textContent=`Далее ${money(data.tariff.hourly_fee)} за каждый начатый час`;
      renderMap();
    }catch(e){notice(e.message,true);}finally{loading=false;}
  }
  async function openEntry(id){
    await loadMap();
    const spot=data?.spots.find(s=>s.id===id);
    if(!spot||spot.is_occupied){notice('Место уже занято. Выберите другое.',true);return;}
    chosenSpot=spot;const form=$('#entry-form');form.reset();form.querySelector('.form-error').textContent='';
    $('#selected-spot').textContent=spot.number;
    $('#selected-lot').textContent=spot.lot_name;
    renderMap();
    $('#entry-policy').textContent=tariffText(data.tariff);
    $('#accessibility-request').hidden=!spot.is_accessible;
    form.elements.accessibility_request.required=spot.is_accessible;
    $('#entry-dialog').showModal();
  }
  if(mode==='home'){
    bindMapControls();
    $('#lot-filter').addEventListener('change',renderMap);
    $('.segmented').addEventListener('click',event=>{const button=event.target.closest('[data-filter]');if(!button)return;filter=button.dataset.filter;document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('selected',b===button));renderMap();});
    $('#spot-grid').addEventListener('click',event=>{const button=event.target.closest('[data-spot]');if(button&&!button.disabled)openEntry(Number(button.dataset.spot));});
    $('#entry-form').addEventListener('submit',async event=>{
      event.preventDefault();const form=event.target;const button=form.querySelector('[type="submit"]');button.disabled=true;form.querySelector('.form-error').textContent='';
      try{const f=new FormData(form);const result=await api('/api/parking/enter',{body:{full_name:f.get('full_name'),phone:f.get('phone'),license_plate:f.get('license_plate'),spot_id:chosenSpot.id,accessibility_request:form.elements.accessibility_request.checked}});storage.set(result.private_url);location.assign(result.private_url);}
      catch(e){form.querySelector('.form-error').textContent=e.message;loadMap();}finally{button.disabled=false;}
    });
    loadMap();setInterval(()=>{if(!document.hidden)loadMap();},15000);
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)loadMap();});
    return;
  }
  const token=document.body.dataset.token;
  storage.set(location.pathname);
  let snapshot=null,sampledAt=0,sessionLoading=false,messagesLoading=false,lastBoundary=null,exitSubmitting=false,sessionVersion=0;
  function renderSession(row){
    snapshot=row;sampledAt=performance.now();
    $('#session-plate').textContent=row.license_plate;$('#session-spot').textContent='Место '+row.spot_number;
    $('#session-status').textContent=row.status==='active'?'На парковке':'Парковка завершена';$('#session-status').classList.toggle('done',row.status==='completed');
    $('#session-entry').textContent='Время въезда: '+time(row.entry_time);
    $('#session-fee').textContent=money(row.fee);$('#fee-label').textContent=row.status==='active'?'К оплате сейчас':'Итоговая сумма';
    $('#session-actions').hidden=row.status!=='active';
    if($('#client-exit-dialog').open){
      $('#exit-fee').textContent=money(row.fee);
      $('#exit-eligibility').hidden=row.eligibility!=='pending';
      if(row.status!=='active')$('#client-exit-dialog').close();
    }
    $('#session-policy').textContent=tariffText(row);
    const note=$('#eligibility-note');note.hidden=row.eligibility==='not_requested';note.classList.toggle('confirmed',row.eligibility==='confirmed');
    note.textContent=row.eligibility==='confirmed'?'Право подтверждено. Парковка полностью бесплатна.':row.eligibility==='pending'?'Право на бесплатную парковку ожидает проверки. Показана сумма по обычному тарифу. После подтверждения сумма станет 0 ₸.':'Право на бесплатную парковку не подтверждено. Применяется обычный тариф, указанный при въезде.';
    tick();
  }
  async function loadSession(){
    if(sessionLoading||exitSubmitting)return;sessionLoading=true;const version=sessionVersion;
    try{const row=await api('/api/parking/my-session',{token});if(version===sessionVersion)renderSession(row);return row;}
    catch(e){notice(e.message,true);throw e;}finally{sessionLoading=false;}
  }
  async function loadMessages(){
    if(messagesLoading)return;messagesLoading=true;
    try{const messages=await api('/api/parking/my-messages',{token});$('#client-messages').innerHTML=messages.length?messages.map(m=>`<article class="client-message"><p>${escape(m.body)}</p><small>${escape(time(m.created_at))}</small></article>`).join(''):'<p class="muted">Новых сообщений пока нет.</p>';}
    catch(e){notice(e.message,true);}finally{messagesLoading=false;}
  }
  function tick(){
    if(!snapshot)return;
    const extra=snapshot.status==='active'?Math.max(0,(performance.now()-sampledAt)/1000):0;
    $('#session-timer').textContent=sessionDuration(snapshot,extra);
    if($('#client-exit-dialog').open)$('#exit-duration').textContent=sessionDuration(snapshot,extra);
    if(typeof snapshot.duration_seconds!=='number'||snapshot.duration_status==='invalid'||snapshot.duration_status==='unverified')return;
    const elapsed=snapshot.duration_seconds+extra;
    // Request a new authoritative fee whenever a started hour boundary changes.
    const boundary=Math.ceil(Math.max(0,elapsed-snapshot.free_minutes*60)/3600);
    if(snapshot.status==='active'&&lastBoundary!==null&&boundary!==lastBoundary)loadSession().catch(()=>{});
    lastBoundary=boundary;
  }
  $('#copy-link').addEventListener('click',async()=>{
    try{await navigator.clipboard.writeText(location.origin+location.pathname);notice('Личная ссылка скопирована. Сохраните её в безопасном месте.');}
    catch{notice('Скопируйте личную ссылку из адресной строки браузера.');}
  });
  function feedback(text){$('#session-action-feedback').textContent=text;$('#session-action-feedback').hidden=false;}
  $('#finish-parking').addEventListener('click',async()=>{
    const button=$('#finish-parking');button.disabled=true;
    try{
      // A fresh server snapshot supplies the preview; no browser fee arithmetic.
      const version=sessionVersion;
      const row=await api('/api/parking/my-session',{token});
      if(version!==sessionVersion||exitSubmitting)return;
      renderSession(row);
      if(row.status!=='active'){feedback('Парковка уже завершена.');return;}
      $('#client-exit-form .form-error').textContent='';
      $('#exit-fee').textContent=money(row.fee);
      $('#exit-eligibility').hidden=row.eligibility!=='pending';
      $('#client-exit-dialog').showModal();tick();
    }catch(e){feedback(e.message);}finally{button.disabled=false;}
  });
  $('#client-exit-form').addEventListener('submit',async event=>{
    event.preventDefault();if(exitSubmitting)return;
    exitSubmitting=true;sessionVersion++;const button=$('#client-exit-form button[type="submit"]');button.disabled=true;
    $('#client-exit-form .form-error').textContent='';
    try{
      const result=await api('/api/parking/my-session/exit',{token,body:{}});
      $('#client-exit-dialog').close();renderSession(result.session);
      feedback('Парковка завершена. К оплате: '+money(result.final_fee));notice('Парковка завершена. Место освобождено.');loadMessages();
    }catch(e){$('#client-exit-form .form-error').textContent=e.message;}
    finally{exitSubmitting=false;button.disabled=false;loadSession().catch(()=>{});}
  });
  $('#cancel-parking').addEventListener('click',async()=>{
    const button=$('#cancel-parking');button.disabled=true;
    try{await api('/api/parking/my-session/cancel',{token,body:{}});}
    catch(e){feedback(e.message);}finally{button.disabled=false;}
  });
  loadSession().catch(()=>{});loadMessages();setInterval(tick,1000);setInterval(()=>{if(!document.hidden){loadSession().catch(()=>{});loadMessages();}},10000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){loadSession().catch(()=>{});loadMessages();}});
})();
