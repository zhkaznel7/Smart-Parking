'use strict';
window.Parking = (() => {
  const $ = selector => document.querySelector(selector);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icon = name => `<svg class="icon" aria-hidden="true"><use href="/static/parking-icons.svg#${name}"></use></svg>`;
  const money = value => typeof value==='number'&&Number.isFinite(value) ? new Intl.NumberFormat('ru-RU').format(value) + ' ₸' : 'Требует проверки';
  const duration = seconds => {if(typeof seconds!=='number'||!Number.isFinite(seconds)||seconds<0)return 'Некорректные данные';seconds=Math.floor(seconds); return [Math.floor(seconds/3600),Math.floor(seconds/60)%60,seconds%60].map(n=>String(n).padStart(2,'0')).join(':');};
  const sessionDuration = (row,extra=0) => row.duration_status==='unverified' ? 'Требует проверки' : duration(typeof row.duration_seconds==='number'?row.duration_seconds+extra:null);
  const time = value => !value ? '—' : Number.isNaN(new Date(value).getTime()) ? 'Некорректные данные' : new Date(value).toLocaleString('ru-RU',{timeZone:'Asia/Qyzylorda',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
  const eligibility = value => ({not_requested:'Обычный тариф',pending:'Ожидает проверки',confirmed:'Право подтверждено',rejected:'Не подтверждено'}[value] || '—');
  function notice(message,error=false) {const node=$('#notice');if(!node)return;node.textContent=message;node.classList.toggle('error',error);node.hidden=false;}
  async function api(url,{body,token,method}={}) {
    const headers = {};
    if(token)headers.Authorization='Bearer '+token;
    method=method||(body===undefined?'GET':'POST');
    if(body!==undefined)headers['Content-Type']='application/json';
    if(!['GET','HEAD'].includes(method))headers['X-CSRF-Token']=$('meta[name="csrf-token"]')?.content || '';
    let response;
    try {response=await fetch(url,{method,headers,body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',credentials:'same-origin'});}catch{throw new Error('Нет связи с сервером. Повторите попытку.');}
    let value;
    try{value=await response.json();}catch{throw new Error('Не удалось получить ответ. Обновите страницу.');}
    if(!response.ok){const error=new Error(value.error||'Не удалось выполнить действие.');error.status=response.status;throw error;}
    return value;
  }
  function tariffText(policy){return `${policy.free_minutes===60?'Первый час бесплатно':`Первые ${policy.free_minutes} мин. бесплатно`}. Далее ${money(policy.hourly_fee)} за каждый начатый час.`;}
  function spotMarkup(spot,admin=false,session=null){
    const occupied=spot.is_occupied;
    const pending=admin&&occupied&&session?.eligibility==='pending';
    const status=pending?'Ожидает проверки':occupied?'Занято':'Свободно';
    const fullPlate=session?' · '+escape(session.license_plate):'';
    return `<button type="button" class="parking-spot ${occupied?'occupied':''} ${pending?'pending':''} ${spot.is_accessible?'accessible':''}" data-spot="${spot.id}" ${occupied&&!admin?'disabled':''} aria-label="${escape(spot.number)}: ${status}${spot.is_accessible?', Место для людей с инвалидностью':''}${fullPlate}" title="${escape(spot.number)} · ${status}${spot.is_accessible?' · Место для людей с инвалидностью':''}${fullPlate}"><span class="bay-number">${escape(spot.number)}</span><span class="bay-center">${occupied?icon('car-top'):spot.is_accessible?icon('wheelchair'):'<span class="vacant-mark" aria-hidden="true">＋</span>'}</span><span class="bay-status">${status}</span>${session?'<span class="bay-plate">'+escape(session.license_plate)+'</span>':''}${spot.is_accessible?'<span class="access-mark">'+icon('wheelchair')+'</span>':''}</button>`;
  }
  function mapMarkup(spots,{admin=false,sessions=[],filter='all',selectedSpot=null}={}){
    if(!spots.length)return '<p class="empty map-empty">Парковочных мест пока нет.</p>';
    const lots=new Map();
    for(const spot of spots){if(!lots.has(spot.parking_lot_id))lots.set(spot.parking_lot_id,[]);lots.get(spot.parking_lot_id).push(spot);}
    const active=new Map(sessions.filter(row=>row.status==='active').map(row=>[row.spot_id,row]));
    function bay(spot){
      const row=active.get(spot.id);
      const matches=filter==='all'||filter==='free'&&!spot.is_occupied||filter==='accessible'&&spot.is_accessible||filter==='pending'&&row?.eligibility==='pending';
      const classes=[!matches?'filtered-out':'',spot.id===selectedSpot?'selected-bay':''].filter(Boolean).join(' ');
      return `<div class="bay-wrap ${classes}" ${!matches?'inert':''}>${spotMarkup(spot,admin,row)}</div>`;
    }
    return [...lots.entries()].map(([id,lotSpots])=>{
      const modules=[];
      for(let start=0;start<lotSpots.length;start+=12){
        const slice=lotSpots.slice(start,start+12),columns=Math.ceil(slice.length/2);
        modules.push(`<div class="map-module" data-columns="${columns}"><div class="parking-row upper-row">${slice.slice(0,columns).map(bay).join('')}</div><div class="driving-lane" aria-hidden="true"><span class="lane-arrow">${icon('arrow')}</span><span class="lane-dashes"></span><span class="lane-label">ПРОЕЗД · РЯД ${Math.floor(start/12)+1}</span><span class="lane-dashes"></span><span class="lane-arrow">${icon('arrow')}</span></div><div class="parking-row lower-row">${slice.slice(columns).map(bay).join('')}</div></div>`);
      }
      return `<section class="lot-plan" aria-label="${escape(lotSpots[0].lot_name)}"><div class="lot-plan-header"><span class="lot-marker">${icon('parking')}</span><h3>${escape(lotSpots[0].lot_name)}</h3><span>${lotSpots.filter(s=>!s.is_occupied).length} / ${lotSpots.length} свободно</span></div>${modules.join('')}<div class="perimeter-lane"><span>${icon('arrow')} ВЪЕЗД</span><span class="lane-dashes"></span><span>ВЫЕЗД ${icon('arrow')}</span></div></section>`;
    }).join('');
  }
  function renderParkingMap(node,spots,options={}){
    const focused=document.activeElement?.closest?.('[data-spot]')?.dataset.spot;
    node.innerHTML=mapMarkup(spots,options);
    if(focused)node.querySelector(`[data-spot="${focused}"]`)?.focus({preventScroll:true});
  }
  function bindMapControls(){
    const map=$('#spot-grid');if(!map)return;
    const levels=[60,80,100,120,140];let index=2;
    document.querySelectorAll('[data-zoom]').forEach(button=>button.addEventListener('click',()=>{
      index=button.dataset.zoom==='reset'?2:Math.max(0,Math.min(levels.length-1,index+(button.dataset.zoom==='in'?1:-1)));
      map.dataset.zoom=String(levels[index]);$('#map-zoom-label').textContent=levels[index]+'%';
      document.querySelector('[data-zoom="out"]').disabled=index===0;document.querySelector('[data-zoom="in"]').disabled=index===levels.length-1;
    }));
  }
  function openContext(dialog){
    document.querySelectorAll('.context-panel[open]').forEach(other=>{if(other!==dialog)other.close();});
    document.body.classList.add('details-open');
    if(!dialog.open){if(window.matchMedia('(max-width: 900px)').matches)dialog.showModal();else dialog.show();}
  }
  document.querySelectorAll('.context-panel').forEach(dialog=>dialog.addEventListener('close',()=>{
    if(!document.querySelector('.context-panel[open]'))document.body.classList.remove('details-open');
  }));
  document.querySelectorAll('.close-dialog').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
  return {$,escape,icon,money,duration,sessionDuration,time,eligibility,notice,api,tariffText,spotMarkup,mapMarkup,renderParkingMap,bindMapControls,openContext};
})();
