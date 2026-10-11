// Client script integration in a minimal DOM harness, not a visual browser test.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'..');
const flush=()=>new Promise(resolve=>setImmediate(resolve));

function harness(){
  const nodes=new Map(),requests=[],saved=[];let destination='';
  const data={stats:{total:3,available:2,occupied:1},tariff:{free_minutes:60,hourly_fee:100},lots:[{id:5,name:'Городская парковка'}],spots:[
    {id:28,parking_lot_id:5,number:'E01',lot_name:'Городская парковка',is_occupied:false,is_accessible:false},
    {id:44,parking_lot_id:5,number:'E03',lot_name:'Городская парковка',is_occupied:true,is_accessible:false},
    {id:93,parking_lot_id:5,number:'E09',lot_name:'Городская парковка',is_occupied:false,is_accessible:true},
  ]};
  function node(selector){
    if(!nodes.has(selector))nodes.set(selector,{
      textContent:'',innerHTML:'',value:'',hidden:false,open:false,content:'csrf-token',dataset:{},listeners:{},
      classList:{toggle(){}},elements:{accessibility_request:{checked:false,required:false}},fields:{},
      addEventListener(type,callback){this.listeners[type]=callback;},
      querySelector(child){return node(selector+' '+child);},
      showModal(){this.open=true;},close(){this.open=false;},reset(){this.elements.accessibility_request.checked=false;},
    });
    return nodes.get(selector);
  }
  const context=vm.createContext({
    document:{body:{dataset:{mode:'home'}},hidden:false,querySelector:node,querySelectorAll:()=>[],addEventListener(){}},
    localStorage:{getItem:()=>null,setItem:(key,value)=>saved.push({key,value})},
    location:{assign:value=>{destination=value;}},
    FormData:class{constructor(form){this.fields=form.fields;}get(name){return this.fields[name];}},
    setInterval(){},setTimeout,clearTimeout,Intl,Date,Error,
    fetch:async(url,options)=>{requests.push({url,options});return {ok:true,json:async()=>url==='/api/spots'?data:{private_url:'/my-parking/'+'b'.repeat(43)}};},
  });
  context.window=context;
  vm.runInContext(fs.readFileSync(path.join(root,'static/portal-common.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(root,'static/client-parking.js'),'utf8'),context);
  return {node,requests,saved,get destination(){return destination;},click(id,disabled=false){return node('#spot-grid').listeners.click({target:{closest:()=>({dataset:{spot:String(id)},disabled})}});}};
}

test('client map uses backend IDs and occupied bays cannot open the booking sheet',async()=>{
  const h=harness();await flush();
  assert.match(h.node('#spot-grid').innerHTML,/data-spot="28"/);
  assert.match(h.node('#spot-grid').innerHTML,/data-spot="93"/);
  assert.equal(h.node('#available').textContent,2);
  h.click(44,true);await flush();assert.equal(h.node('#entry-dialog').open,false);
  h.click(28);await flush();assert.equal(h.node('#entry-dialog').open,true);
  assert.equal(h.node('#selected-spot').textContent,'E01');
  assert.equal(h.node('#selected-lot').textContent,'Городская парковка');
  assert.equal(h.node('#accessibility-request').hidden,true);
});

test('accessible booking preserves eligibility request and submits the selected database spot',async()=>{
  const h=harness();await flush();h.click(93);await flush();
  assert.equal(h.node('#accessibility-request').hidden,false);
  const form=h.node('#entry-form');assert.equal(form.elements.accessibility_request.required,true);
  form.elements.accessibility_request.checked=true;
  form.fields={full_name:'Иван Иванов',phone:'+7 777 123 45 67',license_plate:'777 ABC 02'};
  await form.listeners.submit({target:form,preventDefault(){}});
  const sent=h.requests.find(request=>request.url==='/api/parking/enter');
  assert.deepEqual(JSON.parse(sent.options.body),{full_name:'Иван Иванов',phone:'+7 777 123 45 67',license_plate:'777 ABC 02',spot_id:93,accessibility_request:true});
  assert.equal(sent.options.headers['X-CSRF-Token'],'csrf-token');
  assert.equal(h.destination,'/my-parking/'+'b'.repeat(43));
  assert.equal(h.saved[0].value,h.destination);
});
