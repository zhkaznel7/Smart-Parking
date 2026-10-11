// Frontend logic checks with a minimal DOM harness; these are not browser tests.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'..');
const flush=()=>new Promise(resolve=>setImmediate(resolve));

function harness(initial){
  const nodes=new Map(),intervals=new Map(),requests=[];
  let clock=0,row={...initial};
  function node(selector){
    if(!nodes.has(selector))nodes.set(selector,{textContent:'',innerHTML:'',hidden:false,open:false,content:'csrf',listeners:{},classList:{toggle(){}},addEventListener(type,callback){this.listeners[type]=callback;},showModal(){this.open=true;},close(){this.open=false;}});
    return nodes.get(selector);
  }
  const context=vm.createContext({
    document:{body:{dataset:{mode:'session',token:'a'.repeat(43)}},hidden:false,querySelector:node,querySelectorAll:()=>[],addEventListener(){}},
    location:{origin:'http://localhost',pathname:'/my-parking/'+'a'.repeat(43)},
    localStorage:{getItem:()=>null,setItem(){}},
    navigator:{clipboard:{writeText:async()=>{}}},
    performance:{now:()=>clock},
    setInterval:(callback,ms)=>{intervals.set(ms,callback);},
    setTimeout,clearTimeout,Intl,Date,Error,
    fetch:async(url,options)=>{
      requests.push({url,options});
      if(url.endsWith('/cancel'))return {ok:false,status:409,json:async()=>({error:'Для отмены активной парковки обратитесь к администратору.'})};
      if(url.endsWith('/exit')){
        row={...row,status:'completed',duration_seconds:row.duration_seconds+clock/1000,final_fee:row.fee};
        return {ok:true,json:async()=>({session:{...row},final_fee:row.fee,message:'Парковка завершена'})};
      }
      return {ok:true,json:async()=>url.endsWith('my-session')?{...row}:[{body:'<script>опасно</script>',created_at:'2026-10-10T10:00:00+00:00'}]};
    },
  });
  context.window=context;
  vm.runInContext(fs.readFileSync(path.join(root,'static/portal-common.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(root,'static/client-parking.js'),'utf8'),context);
  return {node,requests,advance(ms){clock+=ms;intervals.get(1000)();},poll(){intervals.get(10000)();},update(next){row={...row,...next};}};
}

const active={id:1,license_plate:'777ABC02',spot_number:'A03',entry_time:'2026-10-10T10:00:00+00:00',status:'active',duration_seconds:90,fee:0,free_minutes:60,hourly_fee:100,eligibility:'not_requested'};

test('timer advances from the server snapshot and survives page reconstruction',async()=>{
  const first=harness(active);await flush();
  assert.equal(first.node('#session-timer').textContent,'00:01:30');
  first.advance(5000);
  assert.equal(first.node('#session-timer').textContent,'00:01:35');
  const refreshed=harness({...active,duration_seconds:110});await flush();
  assert.equal(refreshed.node('#session-timer').textContent,'00:01:50');
  assert.match(refreshed.node('#session-entry').textContent,/10\.10\.2026/);
  assert.equal(first.requests[0].options.headers.Authorization,'Bearer '+'a'.repeat(43));
  assert.equal(first.requests.some(r=>r.url.includes('a'.repeat(43))),false);
});

test('fee comes from the backend and updates at a pricing boundary',async()=>{
  const page=harness({...active,duration_seconds:3599});await flush();
  assert.equal(page.node('#session-fee').textContent,'0 ₸');
  page.update({duration_seconds:3601,fee:100});
  page.advance(2000);await flush();
  assert.equal(page.node('#session-fee').textContent,'100 ₸');
  assert.equal(page.node('#session-timer').textContent,'01:00:01');
  assert.equal(page.requests.filter(r=>r.url.endsWith('my-session')).length,2);
});

test('completed timer stops and administrator messages are escaped',async()=>{
  const page=harness({...active,status:'completed',duration_seconds:5000,fee:100});await flush();
  assert.equal(page.node('#session-timer').textContent,'01:23:20');
  page.advance(100000);
  assert.equal(page.node('#session-timer').textContent,'01:23:20');
  assert.match(page.node('#client-messages').innerHTML,/&lt;script&gt;/);
  assert.doesNotMatch(page.node('#client-messages').innerHTML,/<script>/);
});

test('eligibility confirmation and fee update arrive by polling',async()=>{
  const page=harness({...active,eligibility:'pending',fee:200});await flush();
  assert.match(page.node('#eligibility-note').textContent,/ожидает проверки/);
  page.update({eligibility:'confirmed',fee:0});page.poll();await flush();
  assert.match(page.node('#eligibility-note').textContent,/полностью бесплатна/);
  assert.equal(page.node('#session-fee').textContent,'0 ₸');
});

test('exit confirmation uses server fee, completes once and stops the timer',async()=>{
  const page=harness({...active,fee:100});await flush();page.advance(5000);
  await page.node('#finish-parking').listeners.click();
  assert.equal(page.node('#client-exit-dialog').open,true);
  assert.equal(page.node('#exit-fee').textContent,'100 ₸');
  const submit=()=>page.node('#client-exit-form').listeners.submit({preventDefault(){}});
  await Promise.all([submit(),submit()]);await flush();
  assert.equal(page.requests.filter(r=>r.url.endsWith('/exit')).length,1);
  const request=page.requests.find(r=>r.url.endsWith('/exit'));
  assert.equal(request.options.headers.Authorization,'Bearer '+'a'.repeat(43));
  assert.equal(request.options.headers['X-CSRF-Token'],'csrf');
  assert.equal(request.options.method,'POST');
  assert.equal(page.node('#session-actions').hidden,true);
  assert.equal(page.node('#client-exit-dialog').open,false);
  assert.equal(page.node('#session-status').textContent,'Парковка завершена');
  const stopped=page.node('#session-timer').textContent;
  page.advance(60000);assert.equal(page.node('#session-timer').textContent,stopped);
  assert.match(page.node('#session-action-feedback').textContent,/100 ₸/);
});

test('cancellation shows administrator guidance and leaves the active timer running',async()=>{
  const page=harness(active);await flush();
  await page.node('#cancel-parking').listeners.click();
  assert.equal(page.node('#session-action-feedback').textContent,'Для отмены активной парковки обратитесь к администратору.');
  assert.equal(page.node('#session-actions').hidden,false);
  assert.equal(page.node('#session-status').textContent,'На парковке');
  page.advance(1000);assert.equal(page.node('#session-timer').textContent,'00:01:31');
  assert.equal(page.requests.some(r=>r.url.endsWith('/exit')),false);
});
