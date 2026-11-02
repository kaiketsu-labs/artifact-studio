const test=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('../web/workspace-panels.js');

function fixture() {
  const listeners={};
  function element() {
    const handlers={}, styles=new Map(), classes=new Set();
    return {handlers,hidden:false,style:{setProperty:(k,v)=>styles.set(k,v),getPropertyValue:k=>styles.get(k)||'',removeProperty:k=>styles.delete(k)},classList:{add:k=>classes.add(k),remove:k=>classes.delete(k),toggle:(k,v)=>v?classes.add(k):classes.delete(k),contains:k=>classes.has(k)},setAttribute(k,v){this[k]=v},addEventListener(k,v){handlers[k]=v},append(e){this.child=e},getBoundingClientRect(){return {width:200,left:600,right:200}},setPointerCapture(){}};
  }
  const left=element(),right=element(),workspace=element(),body=element(), children=[];
  workspace.querySelector=s=>s.includes('sidebar')?left:right;
  body.append=e=>children.push(e);
  const doc={body,createElement:element,querySelector:()=>workspace,addEventListener:(k,v)=>{listeners[k]=v}};
  const store=new Map(),storage={getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v)};
  const view={addEventListener(){}};
  return {doc,view,storage,store,workspace,body,children,listeners};
}

test('panels toggle, persist and reopen without replacing workspace contents',()=>{
  const f=fixture(), panels=create({...f,kind:'hub'}); panels.refresh();
  const [left,,right]=f.children;
  left.handlers.click(); right.handlers.click();
  assert.equal(f.workspace.classList.contains('panels-left-closed'),true);
  assert.equal(f.workspace.classList.contains('panels-right-closed'),true);
  assert.equal(JSON.parse([...f.store.values()][0]).right,true);
  panels.refresh(); assert.equal(left['aria-expanded'],'false');
  left.handlers.click(); right.handlers.click();
  assert.equal(f.workspace.classList.contains('panels-left-closed'),false);
  assert.equal(f.workspace.classList.contains('panels-right-closed'),false);
});

test('clean view preserves nodes and Escape restores layout',()=>{
  const f=fixture(), panels=create({...f,kind:'hub'}); panels.refresh();
  const workspace=f.workspace; panels.enterView();
  assert.equal(f.body.classList.contains('workspace-clean-view'),true);
  f.listeners.keydown({key:'Escape',preventDefault(){}});
  assert.equal(f.body.classList.contains('workspace-clean-view'),false);
  assert.equal(f.workspace,workspace);
});

test('keyboard resizing and edge drag collapse a panel',()=>{
  const f=fixture(), panels=create({...f,kind:'hub'}); panels.refresh();
  const handle=f.children[1];
  handle.handlers.keydown({key:'ArrowRight',preventDefault(){}});
  assert.equal(f.workspace.style.getPropertyValue('--panel-left-width'),'220px');
  handle.handlers.pointerdown({button:0,pointerId:1,clientX:200,preventDefault(){}});
  handle.handlers.pointermove({pointerId:1,clientX:70});
  handle.handlers.pointerup({pointerId:1,clientX:70});
  assert.equal(f.workspace.classList.contains('panels-left-closed'),true);
});

test('library sidebar remains operable without a chat panel',()=>{
  const f=fixture(), original=f.workspace.querySelector;
  f.workspace.querySelector=s=>s.includes('sidebar') ? original(s) : null;
  const panels=create({...f,kind:'hub'}); panels.refresh();
  assert.equal(f.children[0].hidden,false);
  assert.equal(f.children[2].hidden,true);
  f.children[0].handlers.click();
  assert.equal(f.workspace.classList.contains('panels-left-closed'),true);
  f.children[0].handlers.click();
  assert.equal(f.workspace.classList.contains('panels-left-closed'),false);
});

test('cancelled resize restores previous width and releases capture',()=>{
  const f=fixture(), panels=create({...f,kind:'hub'}); panels.refresh();
  const handle=f.children[1]; let released=false;
  handle.hasPointerCapture=()=>true; handle.releasePointerCapture=()=>{released=true};
  f.workspace.style.setProperty('--panel-left-width','180px');
  handle.handlers.pointerdown({button:0,pointerId:1,clientX:200,preventDefault(){}});
  handle.handlers.pointermove({pointerId:1,clientX:250});
  handle.handlers.pointercancel({pointerId:1,clientX:250});
  assert.equal(f.workspace.style.getPropertyValue('--panel-left-width'),'180px');
  assert.equal(released,true);
});

test('edge collapse restores the previous width for both panels',()=>{
  for (const [index,side,start,end] of [[1,'left',200,70],[3,'right',600,730]]) {
    const f=fixture(), panels=create({...f,kind:'hub'}); panels.refresh();
    f.workspace.style.setProperty(`--panel-${side}-width`,'250px');
    const handle=f.children[index];
    handle.handlers.pointerdown({button:0,pointerId:1,clientX:start,preventDefault(){}});
    handle.handlers.pointermove({pointerId:1,clientX:end});
    handle.handlers.pointerup({pointerId:1,clientX:end});
    assert.equal(f.workspace.style.getPropertyValue(`--panel-${side}-width`),'250px');
    f.children[index-1].handlers.click();
    assert.equal(f.workspace.style.getPropertyValue(`--panel-${side}-width`),'250px');
  }
});

test('view change callback only runs when focus changes',()=>{
  const f=fixture(), changes=[], panels=create({...f,kind:'hub',onViewChange:value=>changes.push(value)});
  panels.refresh(); panels.enterView(); panels.enterView(); panels.refresh(); panels.exitView(); panels.exitView();
  assert.deepEqual(changes,[true,false]);
});


test('clean view exposes a labelled restore control and restores on click',()=>{
  const f=fixture(), panels=create({...f,kind:'hub'}); panels.refresh();
  const area=f.children[4], exit=area.child;
  assert.equal(area.hidden,true);
  panels.enterView();
  assert.equal(area.hidden,false);
  assert.equal(exit['aria-label'],'Exit view');
  exit.handlers.click();
  assert.equal(area.hidden,true);
  assert.equal(f.body.classList.contains('workspace-clean-view'),false);
});


test('slide workspace enters and exits clean view without replacing its preview',()=>{
  const f=fixture(), panels=create({...f,kind:'slides'}); panels.refresh();
  const workspace=f.workspace; panels.enterView();
  assert.equal(f.body.classList.contains('workspace-clean-view'),true);
  panels.exitView();
  assert.equal(f.body.classList.contains('workspace-clean-view'),false);
  assert.equal(f.workspace,workspace);
});
