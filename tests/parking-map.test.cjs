// Map and interaction logic checks. This harness does not render a browser.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.resolve(__dirname,'../static/portal-common.js'),'utf8');

function harness(mobile=false){
  const listeners=new Map(),classes=new Set();
  const buttons=['out','in','reset'].map(action=>({dataset:{zoom:action},disabled:false,addEventListener(name,callback){listeners.set(action,callback);}}));
  const map={dataset:{}},label={textContent:''},panels=[];
  const document={
    body:{classList:{add:c=>classes.add(c),remove:c=>classes.delete(c)}},
    querySelector(selector){
      if(selector==='#spot-grid')return map;
      if(selector==='#map-zoom-label')return label;
      if(selector.startsWith('[data-zoom='))return buttons.find(button=>selector.includes('"'+button.dataset.zoom+'"'));
      if(selector==='.context-panel[open]')return panels.find(panel=>panel.open)||null;
      return null;
    },
    querySelectorAll(selector){
      if(selector==='[data-zoom]')return buttons;
      if(selector==='.context-panel[open]')return panels.filter(panel=>panel.open);
      if(selector==='.context-panel')return panels;
      return [];
    },
  };
  const context=vm.createContext({document,Intl,Date,Error,matchMedia:()=>({matches:mobile})});
  context.window=context;
  vm.runInContext(source,context);
  function panel(){
    const value={open:false,modal:false,show(){this.open=true;this.modal=false;},showModal(){this.open=true;this.modal=true;},close(){this.open=false;}};
    panels.push(value);return value;
  }
  return {parking:context.Parking,map,label,buttons,listeners,classes,panel};
}

const spots=[
  {id:71,parking_lot_id:8,number:'H03',lot_name:'Северная парковка',is_occupied:false,is_accessible:false},
  {id:95,parking_lot_id:8,number:'H07',lot_name:'Северная парковка',is_occupied:true,is_accessible:true},
  {id:103,parking_lot_id:9,number:'I01',lot_name:'Южная парковка',is_occupied:false,is_accessible:true},
];
const sessions=[{id:18,spot_id:95,status:'active',license_plate:'777ABC02',eligibility:'pending'}];
const ids=html=>[...html.matchAll(/data-spot="(\d+)"/g)].map(match=>Number(match[1]));

test('map contains exactly the supplied spaces, real numbers and driving lanes',()=>{
  const {parking}=harness();const html=parking.mapMarkup(spots);
  assert.deepEqual(ids(html),[71,95,103]);
  assert.match(html,/H03/);assert.match(html,/H07/);assert.match(html,/I01/);
  assert.match(html,/driving-lane/);assert.match(html,/upper-row/);assert.match(html,/lower-row/);
  assert.equal((html.match(/class="lot-plan"/g)||[]).length,2);
  assert.match(html,/car-top/);assert.match(html,/wheelchair/);
});

test('filters preserve positions and disable unmatched bays for keyboard navigation',()=>{
  const {parking}=harness();
  const all=parking.mapMarkup(spots,{admin:true,sessions});
  const filtered=parking.mapMarkup(spots,{admin:true,sessions,filter:'pending',selectedSpot:95});
  assert.deepEqual(ids(filtered),ids(all));
  assert.equal((filtered.match(/filtered-out/g)||[]).length,2);
  assert.equal((filtered.match(/ inert/g)||[]).length,2);
  assert.match(filtered,/selected-bay/);
});

test('pending status and plates appear only with administrator session data',()=>{
  const {parking}=harness();
  const admin=parking.mapMarkup(spots,{admin:true,sessions});
  const publicMap=parking.mapMarkup(spots);
  assert.match(admin,/777ABC02/);assert.match(admin,/Ожидает проверки/);
  assert.match(admin,/parking-spot occupied pending accessible/);
  assert.doesNotMatch(publicMap,/777ABC02|Ожидает проверки/);
  assert.match(publicMap,/data-spot="95" disabled/);
  assert.doesNotMatch(admin,/data-spot="95" disabled/);
});

test('many spaces form multiple rows without adding illustrative fake spaces',()=>{
  const {parking}=harness();const many=Array.from({length:25},(_,i)=>({...spots[0],id:300+i,number:'H'+String(i+1).padStart(2,'0')}));
  const html=parking.mapMarkup(many);
  assert.equal(ids(html).length,25);assert.equal(new Set(ids(html)).size,25);
  assert.equal((html.match(/class="map-module"/g)||[]).length,3);
  assert.equal(parking.mapMarkup([]).includes('data-spot'),false);
});

test('lot names and license plates are escaped before insertion',()=>{
  const {parking}=harness();
  const html=parking.mapMarkup([{...spots[1],lot_name:'<script>опасно</script>'}],{admin:true,sessions:[{...sessions[0],license_plate:'<img onerror="опасно">'}]});
  assert.doesNotMatch(html,/<script>|<img/);
  assert.match(html,/&lt;script&gt;/);assert.match(html,/&lt;img/);
});

test('zoom controls change scale, respect limits and restore 100 percent',()=>{
  const h=harness();h.parking.bindMapControls();
  h.listeners.get('in')();assert.equal(h.map.dataset.zoom,'120');
  h.listeners.get('in')();assert.equal(h.map.dataset.zoom,'140');assert.equal(h.buttons[1].disabled,true);
  h.listeners.get('in')();assert.equal(h.map.dataset.zoom,'140');
  h.listeners.get('reset')();assert.equal(h.label.textContent,'100%');
  h.listeners.get('out')();h.listeners.get('out')();assert.equal(h.map.dataset.zoom,'60');assert.equal(h.buttons[0].disabled,true);
});

test('desktop details remain contextual and mobile details use a modal sheet',()=>{
  const desktop=harness();const first=desktop.panel(),second=desktop.panel();
  desktop.parking.openContext(first);assert.equal(first.modal,false);assert.equal(first.open,true);
  desktop.parking.openContext(second);assert.equal(first.open,false);assert.equal(second.open,true);
  assert.equal(desktop.classes.has('details-open'),true);
  const mobile=harness(true);const sheet=mobile.panel();mobile.parking.openContext(sheet);assert.equal(sheet.modal,true);
});
