'use strict';
(() => {
  const {$,escape,icon,money,sessionDuration,time,eligibility,notice,api,renderParkingMap,bindMapControls,openContext}=Parking;
  let mapData,sessions=[],view='overview',filter='all',selectedSession,exitSession,selectedSpot,loading=false,searchTimer;
  let detailSnapshot=null,detailSampledAt=0;
  let deleteSession=null,deleteSubmitting=false;
  const viewTitles={overview:['Обзор парковки','Каждое место. Каждый автомобиль. Одна схема.'],active:['Автомобили','Активные сессии, стоимость и связь с клиентами.'],map:['Парковка','Состояние мест и управление доступной средой.'],history:['История','Все въезды и выезды сохраняются здесь.'],settings:['Тарифы','Условия парковки и управление участками.']};
  async function adminApi(url,options){try{return await api(url,options);}catch(e){if(e.status===401)location.assign('/admin/login');throw e;}}
  function renderMap(){
    if(!mapData)return;
    const lot=$('#lot-filter').value;
    const spots=mapData.spots.filter(s=>!lot||String(s.parking_lot_id)===lot);
    const chosen=sessions.find(s=>s.id===selectedSession);
    renderParkingMap($('#spot-grid'),spots,{admin:true,sessions,filter,selectedSpot:$('#details-dialog').open?chosen?.spot_id:selectedSpot});
    $('#map-count').textContent=`${spots.length} мест · ${spots.filter(s=>!s.is_occupied).length} свободно`;
  }
  function renderTable(){
    const query=$('#search').value.toUpperCase().replace(/[\s-]/g,'');
    const rows=sessions.filter(s=>(view==='history'||s.status==='active')&&s.license_plate.toUpperCase().replace(/[\s-]/g,'').includes(query));
    $('#table-title').textContent=view==='history'?'История парковки':'Автомобили на парковке';
    $('#table-description').textContent=view==='history'?'История въездов и выездов. Завершённые записи сохраняются.':'Въезды, стоимость и связь с клиентами.';
    $('#session-rows').innerHTML=rows.length?rows.map(s=>`<tr><td><strong class="vehicle-plate" title="${escape(s.license_plate)}">${escape(s.license_plate)}</strong><small>${escape(s.client_full_name||'Клиент из старой базы')}</small><small>${escape(s.client_phone||'Телефон не указан')}</small>${s.is_legacy?'<span class="record-label legacy">Импортированная запись</span>':''}${s.data_quality==='invalid'?'<span class="record-label invalid">Некорректные данные</span>':s.data_quality==='suspicious'?'<span class="record-label legacy">Требует проверки</span>':''}</td><td><strong>${escape(s.spot_number)}</strong><span class="pill ${s.status==='active'?'':'done'}">${s.status==='active'?'Активная':'Завершена'}</span></td><td><span class="table-date">${escape(time(s.entry_time))}</span><small>${s.exit_time?'Выезд: '+escape(time(s.exit_time)):'Выезд не зарегистрирован'}</small></td><td>${sessionDuration(s)}</td><td><strong>${money(s.fee)}</strong></td><td><span class="pill ${s.eligibility==='pending'?'pending':s.eligibility==='rejected'?'rejected':''}">${eligibility(s.eligibility)}</span></td><td><div class="table-actions"><button class="table-action" data-detail="${s.id}">Подробнее</button>${s.status==='active'?`<button class="table-action exit-action" data-exit="${s.id}">Завершить парковку</button>`:`<button class="table-action delete-action" data-delete="${s.id}">Удалить запись</button>`}</div></td></tr>`).join(''):'<tr><td colspan="7" class="empty">'+(query?'По этому номеру ничего не найдено.':'Парковочных сессий пока нет.')+'</td></tr>';
  }
  function renderView(){
    $('#admin-title').textContent=viewTitles[view][0];$('#admin-subtitle').textContent=viewTitles[view][1];
    $('#admin-map-section').hidden=!['overview','map'].includes(view);
    $('#admin-sessions-section').hidden=!['active','history'].includes(view);
    $('#attention-section').hidden=view!=='overview';
    $('#admin-settings-section').hidden=view!=='settings';
    document.querySelectorAll('[data-view]').forEach(button=>button.classList.toggle('active',button.dataset.view===view));
    renderTable();
  }
  async function refresh(){
    if(loading)return;loading=true;
    try{
      const [spots,result]=await Promise.all([api('/api/spots'),adminApi('/api/admin/sessions')]);mapData=spots;sessions=result.sessions;
      for(const key of ['total','available','occupied','accessible'])$('#'+key).textContent=mapData.stats[key];
      $('#total-fees').textContent=money(result.stats.total_fees_due);$('#active-summary').textContent=`Активных сессий: ${result.stats.active}`;$('#pending-summary').textContent=`Ожидают проверки: ${result.stats.pending}`;$('#nav-active-count').textContent=result.stats.active;
      $('#fee-summary').textContent=result.stats.unverified_fees?`Суммы требуют проверки: ${result.stats.unverified_fees}`:'По активным сессиям';
      const pending=sessions.filter(s=>s.status==='active'&&s.eligibility==='pending');
      $('#attention-list').innerHTML=pending.length?pending.map(s=>`<button type="button" class="attention-link" data-detail="${s.id}"><strong>${escape(s.spot_number)}</strong> ${escape(s.license_plate)} ${icon('arrow')}</button>`).join(''):'<span class="muted">Все запросы проверены. Новых запросов нет.</span>';
      $('#map-updated').textContent='Обновлено '+new Date(result.server_time).toLocaleTimeString('ru-RU',{timeZone:'Asia/Qyzylorda',hour:'2-digit',minute:'2-digit',second:'2-digit'});
      const chosen=$('#lot-filter').value;$('#lot-filter').innerHTML='<option value="">Все парковки</option>'+mapData.lots.map(l=>`<option value="${l.id}">${escape(l.name)}</option>`).join('');$('#lot-filter').value=chosen;
      if(document.activeElement?.closest('#tariff-form')===null){$('#tariff-form').elements.free_minutes.value=mapData.tariff.free_minutes;$('#tariff-form').elements.hourly_fee.value=mapData.tariff.hourly_fee;}
      $('#lot-list').innerHTML=mapData.lots.map(l=>`<div class="lot-list-item"><strong>${escape(l.name)}</strong><span>${escape(l.address)}</span></div>`).join('');
      renderMap();renderTable();
      if($('#details-dialog').open&&selectedSession){const row=sessions.find(s=>s.id===selectedSession);if(row)renderDetail(row);}
    }catch(e){notice(e.message,true);}finally{loading=false;}
  }
  function renderDetail(row){
    detailSnapshot=row;detailSampledAt=performance.now();
    $('#detail-title').textContent=row.license_plate;
    $('#detail-title').title=row.license_plate;
    $('#detail-content').innerHTML=`<div class="detail-vehicle"><span class="detail-car">${icon('car-top')}</span><div><span class="instrument-label">МЕСТО</span><strong>${escape(row.spot_number)}</strong><span class="pill ${row.status==='completed'?'done':row.eligibility==='pending'?'pending':''}">${row.status==='active'?'На парковке':'Завершена'}</span></div></div><div class="detail-readouts"><div><span>Длительность</span><strong id="detail-duration">${sessionDuration(row)}</strong></div><div><span>К оплате</span><strong>${money(row.fee)}</strong></div></div><dl class="detail-grid"><div><dt>Клиент</dt><dd>${escape(row.client_full_name||'Не указан в старой базе')}</dd></div><div><dt>Телефон</dt><dd>${escape(row.client_phone||'Не указан')}</dd></div><div><dt>Въезд</dt><dd>${escape(time(row.entry_time))}</dd></div>${row.exit_time?`<div><dt>Выезд</dt><dd>${escape(time(row.exit_time))}</dd></div>`:''}<div><dt>Право на бесплатную парковку</dt><dd><span class="pill ${row.eligibility==='pending'?'pending':''}">${eligibility(row.eligibility)}</span></dd></div><div><dt>Тариф при въезде</dt><dd>${row.free_minutes} мин. бесплатно, затем ${money(row.hourly_fee)}/час</dd></div></dl>`;
    $('#verification-actions').hidden=!row.accessibility_requested||row.status!=='active';
    $('#confirm-eligibility').disabled=row.eligibility==='confirmed';$('#reject-eligibility').disabled=row.eligibility==='rejected';
    $('#detail-exit').hidden=row.status!=='active';
    $('#detail-delete').hidden=row.status!=='completed';
    if(row.data_quality_message)$('#detail-content').insertAdjacentHTML('afterbegin',`<p class="eligibility-note quality-note">${escape(row.data_quality_message)}</p>`);
    if(row.license_plate.length>16)$('#detail-content').insertAdjacentHTML('afterbegin',`<details class="full-value"><summary>Показать полный государственный номер</summary><p class="full-plate">${escape(row.license_plate)}</p></details>`);
    $('#detail-duration').textContent=sessionDuration(row);
  }
  async function loadMessages(){
    const id=selectedSession;
    try{const rows=await adminApi(`/api/admin/sessions/${id}/messages`);if(id!==selectedSession)return;$('#admin-messages').innerHTML=rows.map(m=>`<div class="admin-message">${escape(m.body)}<small>${escape(time(m.created_at))}</small></div>`).join('');}
    catch(e){if(id===selectedSession)$('#detail-error').textContent=e.message;}
  }
  function openDetail(id){const row=sessions.find(s=>s.id===id);if(!row)return;selectedSession=id;selectedSpot=null;$('#detail-error').textContent='';$('#message-form').reset();$('#message-form .form-error').textContent='';renderDetail(row);openContext($('#details-dialog'));renderMap();loadMessages();}
  function openExit(id=selectedSession){const row=sessions.find(s=>s.id===(typeof id==='number'?id:selectedSession));if(!row||row.status!=='active')return;exitSession=row.id;$('#exit-description').textContent=`Автомобиль ${row.license_plate}, место ${row.spot_number}. Сейчас к оплате: ${money(row.fee)}.${row.eligibility==='pending'?' Право на бесплатную парковку ещё не проверено.':''}`;$('#details-dialog').close();$('#exit-form .form-error').textContent='';$('#exit-dialog').showModal();}
  function openDelete(id=selectedSession){const row=sessions.find(s=>s.id===(typeof id==='number'?id:selectedSession));if(!row||row.status!=='completed')return;deleteSession=row.id;$('#details-dialog').close();$('#delete-form').reset();$('#delete-form .form-error').textContent='';$('#delete-description').textContent=`Удалить завершённую парковку автомобиля ${row.license_plate} на месте ${row.spot_number}?`;$('#delete-dialog').showModal();}
  document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>{view=button.dataset.view;history.replaceState(null,'','#'+view);renderView();}));
  if(viewTitles[location.hash.slice(1)])view=location.hash.slice(1);renderView();
  $('.segmented').addEventListener('click',event=>{const button=event.target.closest('[data-filter]');if(!button)return;filter=button.dataset.filter;document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('selected',b===button));renderMap();});
  $('#lot-filter').addEventListener('change',renderMap);$('#search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(renderTable,150);});
  $('#session-rows').addEventListener('click',event=>{const detail=event.target.closest('[data-detail]'),exit=event.target.closest('[data-exit]'),remove=event.target.closest('[data-delete]');if(detail)openDetail(Number(detail.dataset.detail));else if(exit)openExit(Number(exit.dataset.exit));else if(remove)openDelete(Number(remove.dataset.delete));});
  $('#attention-list').addEventListener('click',event=>{const button=event.target.closest('[data-detail]');if(button)openDetail(Number(button.dataset.detail));});
  $('#spot-grid').addEventListener('click',event=>{const button=event.target.closest('[data-spot]');if(!button)return;const spot=mapData.spots.find(s=>s.id===Number(button.dataset.spot));if(spot.is_occupied){const row=sessions.find(s=>s.spot_id===spot.id&&s.status==='active');if(row)openDetail(row.id);else notice('Место отмечено занятым в старой базе без активной сессии.',true);}else{selectedSpot=spot.id;selectedSession=null;$('#spot-title').textContent='Место '+spot.number;$('#spot-info').innerHTML=`<div class="spot-status-banner">${icon(spot.is_accessible?'wheelchair':'parking')}<div><strong>Свободно</strong><span>${escape(spot.lot_name)}</span></div></div><dl class="detail-grid"><div><dt>Тип места</dt><dd>${spot.is_accessible?'Место для людей с инвалидностью':'Обычное парковочное место'}</dd></div><div><dt>Назначение</dt><dd>Готово к регистрации въезда на клиентском сайте</dd></div></dl>`;$('#spot-form').elements.is_accessible.checked=spot.is_accessible;$('#spot-form .form-error').textContent='';openContext($('#spot-dialog'));renderMap();}});
  $('#detail-exit').addEventListener('click',openExit);
  $('#detail-delete').addEventListener('click',()=>openDelete());
  $('#delete-form').addEventListener('submit',async event=>{
    event.preventDefault();if(deleteSubmitting)return;
    const form=event.target,button=form.querySelector('[type="submit"]');
    if(!form.elements.confirm.checked){form.querySelector('.form-error').textContent='Подтвердите удаление записи.';return;}
    deleteSubmitting=true;button.disabled=true;form.querySelector('.form-error').textContent='';
    try{const result=await adminApi(`/api/admin/sessions/${deleteSession}`,{method:'DELETE',body:{confirm:true}});$('#delete-dialog').close();selectedSession=null;detailSnapshot=null;notice(result.message);await refresh();}
    catch(e){form.querySelector('.form-error').textContent=e.message;}
    finally{deleteSubmitting=false;button.disabled=false;}
  });
  async function verify(confirmed){
    const button=$(confirmed?'#confirm-eligibility':'#reject-eligibility');button.disabled=true;$('#detail-error').textContent='';
    try{await adminApi(`/api/admin/sessions/${selectedSession}/verify-accessibility`,{body:{confirmed}});await refresh();loadMessages();notice('Результат проверки сохранён. Клиент получил сообщение.');}
    catch(e){$('#detail-error').textContent=e.message;}finally{const row=sessions.find(s=>s.id===selectedSession);if(row)renderDetail(row);}
  }
  $('#confirm-eligibility').addEventListener('click',()=>verify(true));$('#reject-eligibility').addEventListener('click',()=>verify(false));
  $('#logout').addEventListener('click',async()=>{try{await adminApi('/api/admin/logout',{body:{}});location.assign('/admin/login');}catch(e){notice(e.message,true);}});
  $('#add-lot').addEventListener('click',()=>{$('#lot-form').reset();$('#lot-form .form-error').textContent='';$('#lot-dialog').showModal();});
  function formAction(selector,url,payload,success,close=true,after=null){
    $(selector).addEventListener('submit',async event=>{event.preventDefault();const form=event.target;const button=form.querySelector('[type="submit"]');button.disabled=true;form.querySelector('.form-error').textContent='';
      try{await adminApi(typeof url==='function'?url():url,{body:payload(new FormData(form))});if(close)form.closest('dialog').close();notice(success);await refresh();if(after)after();}
      catch(e){form.querySelector('.form-error').textContent=e.message;}finally{button.disabled=false;}
    });
  }
  formAction('#exit-form',()=>`/api/admin/sessions/${exitSession}/exit`,()=>({}),'Выезд зарегистрирован. Место свободно.');
  formAction('#message-form',()=>`/api/admin/sessions/${selectedSession}/message`,f=>({body:f.get('body')}),'Сообщение отправлено клиенту.',false,()=>{$('#message-form').reset();loadMessages();});
  formAction('#tariff-form','/api/admin/tariff',f=>({free_minutes:Number(f.get('free_minutes')),hourly_fee:Number(f.get('hourly_fee'))}),'Новый тариф сохранён для будущих въездов.',false);
  formAction('#spot-form',()=>`/api/admin/spots/${selectedSpot}`,()=>({is_accessible:$('#spot-form').elements.is_accessible.checked}),'Тип парковочного места сохранён.');
  formAction('#lot-form','/api/admin/lots',f=>({name:f.get('name'),address:f.get('address'),total_spots:Number(f.get('total_spots')),accessible_spots:Number(f.get('accessible_spots'))}),'Парковка создана.');
  bindMapControls();
  setInterval(()=>{if(detailSnapshot&&$('#details-dialog').open){const extra=detailSnapshot.status==='active'?(performance.now()-detailSampledAt)/1000:0;$('#detail-duration').textContent=sessionDuration(detailSnapshot,extra);}},1000);
  refresh();setInterval(()=>{if(!document.hidden)refresh();},10000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
})();
