// Script/DOM contract checks; this does not render or measure a real browser.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'..');
const flush=()=>new Promise(resolve=>setImmediate(resolve));

function harness(){
  const templates=['admin-parking.html','portal-ui.html'].map(file=>fs.readFileSync(path.join(root,'templates',file),'utf8')).join('');
  const ids=new Set([...templates.matchAll(/id="([\w-]+)"/g)].map(m=>m[1]));ids.add('detail-duration');
  const nodes=new Map(),requests=[];
  const rows=[
    {id:14,spot_id:2,spot_number:'A02',license_plate:'777ABC02',client_full_name:'Иван Иванов',client_phone:'+77771234567',entry_time:'2026-10-11T05:00:00+00:00',exit_time:null,status:'active',duration_seconds:500,fee:0,eligibility:'not_requested',free_minutes:60,hourly_fee:100},
    {id:25,spot_id:3,spot_number:'A03',license_plate:'123ABC'+'Z'.repeat(80),client_full_name:'Архивный клиент',client_phone:'',entry_time:'2025-01-01T00:00:00+00:00',exit_time:'2024-01-01T00:00:00+00:00',status:'completed',duration_seconds:null,duration_status:'invalid',fee:null,eligibility:'not_requested',is_legacy:true,data_quality:'invalid',data_quality_message:'Некорректные данные',free_minutes:60,hourly_fee:100},
  ];
  let sessions=rows.slice();
  function node(selector){
    const match=selector.match(/^#([\w-]+)/);if(match&&!ids.has(match[1]))throw new Error('Selector missing from template: '+selector);
    if(!nodes.has(selector))nodes.set(selector,{
      dataset:{},value:'',open:false,hidden:false,textContent:'',innerHTML:'',listeners:{},classList:{add(){},remove(){},toggle(){}},
      elements:{confirm:{checked:false},free_minutes:{value:60},hourly_fee:{value:100}},
      addEventListener(type,callback){this.listeners[type]=callback;},
      querySelector(child){return node(selector+' '+child);},
      show(){this.open=true;},showModal(){this.open=true;},close(){this.open=false;if(this.listeners.close)this.listeners.close();},
      reset(){this.elements.confirm.checked=false;},insertAdjacentHTML(position,html){this.innerHTML+=html;},
    });
    return nodes.get(selector);
  }
  const views=['overview','map','active','history','settings'].map(view=>{const value=node('.view-'+view);value.dataset.view=view;return value;});
  const zooms=['in','out','reset'].map(zoom=>{const value=node('.zoom-'+zoom);value.dataset.zoom=zoom;return value;});
  const dialogs=[node('#details-dialog'),node('#spot-dialog')];
  const document={
    body:{classList:{add(){},remove(){}}},activeElement:{closest:()=>null},hidden:false,addEventListener(){},
    querySelector(selector){if(selector==='meta[name="csrf-token"]')return {content:'test-csrf'};if(selector==='.context-panel[open]')return dialogs.find(d=>d.open)||null;if(selector.startsWith('[data-zoom='))return zooms.find(d=>selector.includes('"'+d.dataset.zoom+'"'));return node(selector);},
    querySelectorAll(selector){if(selector==='[data-view]')return views;if(selector==='[data-zoom]')return zooms;if(selector==='.context-panel')return dialogs;if(selector==='.context-panel[open]')return dialogs.filter(d=>d.open);return [];},
  };
  const context=vm.createContext({document,Intl,Date,Error,performance:{now:()=>0},setInterval(){},setTimeout,clearTimeout,
    location:{hash:'#history',assign(){}},history:{replaceState(){}},matchMedia:()=>({matches:false}),
    fetch:async(url,options)=>{
      requests.push({url,options});
      let data;
      if(options.method==='DELETE'){sessions=sessions.filter(s=>s.id!==25);data={message:'Историческая запись удалена.'};}
      else if(url==='/api/spots')data={stats:{total:2,available:1,occupied:1,accessible:0},tariff:{free_minutes:60,hourly_fee:100},lots:[{id:1,name:'Парковка',address:'Адрес'}],spots:[{id:2,parking_lot_id:1,number:'A02',lot_name:'Парковка',is_occupied:true},{id:3,parking_lot_id:1,number:'A03',lot_name:'Парковка',is_occupied:false}]};
      else if(url.endsWith('/messages'))data=[];
      else data={sessions:sessions.slice(),stats:{active:1,total_fees_due:0,pending:0,unverified_fees:0},server_time:'2026-10-11T06:00:00+00:00'};
      return {ok:true,json:async()=>data};
    },
  });context.window=context;
  vm.runInContext(fs.readFileSync(path.join(root,'static/portal-common.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(root,'static/admin-parking.js'),'utf8'),context);
  return {node,requests,parking:context.Parking,click(kind,id){node('#session-rows').listeners.click({target:{closest:selector=>selector===`[data-${kind}]`?{dataset:{[kind]:String(id)}}:null}});}};
}

test('active and completed records have separate actions and safe historical formatting',async()=>{
  const h=harness();await flush();const html=h.node('#session-rows').innerHTML;
  assert.match(html,/data-exit="14"/);assert.doesNotMatch(html,/data-delete="14"/);
  assert.match(html,/data-delete="25"/);assert.doesNotMatch(html,/data-exit="25"/);
  assert.match(html,/Некорректные данные/);assert.match(html,/Требует проверки/);
  assert.match(html,/vehicle-plate/);assert.match(html,/title="123ABCZZZ/);
  h.click('detail',25);await flush();
  assert.match(h.node('#detail-content').innerHTML,/Показать полный государственный номер/);
  assert.equal(h.node('#detail-exit').hidden,true);assert.equal(h.node('#detail-delete').hidden,false);
});

test('delete requires checkbox confirmation and sends authenticated CSRF DELETE',async()=>{
  const h=harness();await flush();h.click('delete',25);
  assert.equal(h.node('#delete-dialog').open,true);
  const form=h.node('#delete-form');
  await form.listeners.submit({target:form,preventDefault(){}});
  assert.equal(h.requests.some(r=>r.options.method==='DELETE'),false);
  assert.match(h.node('#delete-form .form-error').textContent,/Подтвердите/);
  form.elements.confirm.checked=true;
  await form.listeners.submit({target:form,preventDefault(){}});await flush();
  const request=h.requests.find(r=>r.options.method==='DELETE');
  assert.equal(request.url,'/api/admin/sessions/25');
  assert.equal(request.options.headers['X-CSRF-Token'],'test-csrf');
  assert.deepEqual(JSON.parse(request.options.body),{confirm:true});
  assert.equal(h.node('#delete-dialog').open,false);
  assert.doesNotMatch(h.node('#session-rows').innerHTML,/data-delete="25"/);
  assert.match(h.node('#session-rows').innerHTML,/data-exit="14"/);
});

test('formatters never turn invalid dates, unknown fees or invalid durations into zero',async()=>{
  const h=harness();await flush();
  assert.equal(h.parking.duration(-1),'Некорректные данные');
  assert.equal(h.parking.duration(null),'Некорректные данные');
  assert.equal(h.parking.money(null),'Требует проверки');
  assert.equal(h.parking.time('not-a-date'),'Некорректные данные');
  assert.equal(h.parking.sessionDuration({duration_status:'unverified',duration_seconds:null},86400),'Требует проверки');
});
