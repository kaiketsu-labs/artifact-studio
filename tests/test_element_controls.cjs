const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const ElementProperties=require('../web/element-properties.js');
const {rotationDegrees,rotationDelta,rotationGeometry,editIntent}=require('../web/slide-edits.js');

test('rotation follows the local plane through anisotropic scale and rotated ancestors',()=>{
  const center={x:100,y:100},basis={xx:0,xy:-.5,yx:2,yy:0};
  assert.equal(rotationDelta({x:100,y:80},{x:105,y:100},center,basis),90);
  assert.equal(rotationDelta(center,{x:105,y:100},center,basis),null);
  assert.equal(rotationDelta({x:100,y:80},{x:105,y:100},center,{xx:0,xy:0,yx:0,yy:0}),null);
  assert.equal(rotationDegrees(359),-1);
  assert.equal(rotationDegrees(-361),-1);
  assert.equal(rotationDegrees(45.1234),45.12);
});

test('rotation pivot respects original rotation and a custom transform origin',()=>{
  const geometry=rotationGeometry({left:200,top:300,width:100,height:50},{left:300,top:250,width:100,height:50},{xx:2,xy:0,yx:0,yy:.5},{a:0,b:1,c:-1,d:0});
  assert.deepEqual(geometry,{basis:{xx:0,xy:-2,yx:.5,yy:0},center:{x:300,y:300}});
});

test('structured intent is compact and does not duplicate anchored HTML',()=>{
  assert.deepEqual(editIntent('delete',{}),{action:'delete',values:{}});
  assert.deepEqual(editIntent('rotate',{rotation_degrees:45}),{action:'rotate',values:{rotation_degrees:45}});
});

/** Supply the DOM surface used by the editor without changing canonical slide content. */
function fixture({sendError=false,properties=null}={}){
  class Control{
    constructor(){this.listeners={};this.dataset={};this.style={};this.attrs=new Map();this.hidden=false;this.children=[];this.classList={toggle(){},add(){}};this.ownerDocument=outer;}
    addEventListener(type,listener){(this.listeners[type]??=[]).push(listener);}
    fire(type,event={}){for(const listener of this.listeners[type]||[])listener({button:0,target:this,currentTarget:this,preventDefault(){},stopPropagation(){},...event});}
    setAttribute(name,value){this.attrs.set(name,value);}
    getAttribute(name){return this.attrs.get(name)??null;}
    removeAttribute(name){this.attrs.delete(name);if(name==='style')this.style={};}
    matches(){return false;}
    append(child){this.children.push(child);}
    focus(){outer.activeElement=this;}
    closest(){return this.label;}
    querySelector(){return null;}
    contains(target){return target===this;}
    getBoundingClientRect(){const match=/translate\(([-.\d]+)px,([-.\d]+)px\)/.exec(this.style.transform||''),rotation=/rotate\(([-.\d]+)deg\)/.exec(this.style.transform||''),angle=Number(rotation?.[1]||0)*Math.PI/180,width=200*Math.abs(Math.cos(angle))+100*Math.abs(Math.sin(angle)),height=200*Math.abs(Math.sin(angle))+100*Math.abs(Math.cos(angle));return {left:100+Number(match?.[1]||0)+(200-width)/2,top:50+Number(match?.[2]||0)+(100-height)/2,width,height};}
  }
  const outer={activeElement:null,createElement:()=>new Control()};
  const doc={addEventListener(){},querySelector:()=>({getBoundingClientRect:()=>({left:0,top:0,width:800,height:450})}),defaultView:{getComputedStyle:element=>({perspective:'none',transform:element.style.transform||'none',fontSize:'16px',fontWeight:'400',fontStyle:'normal',textAlign:'left',color:'rgb(0, 0, 0)'}),DOMMatrixReadOnly:class{constructor(){this.is2D=true;}}}};
  const sandbox={window:{ElementProperties,SlideIcons:{svg:()=>''}},document:outer,crypto:{randomUUID:()=>`request-${Math.random()}`},setTimeout};
  vm.runInNewContext(fs.readFileSync(require.resolve('../web/slide-edits.js'),'utf8'),sandbox);
  const controls=Object.fromEntries(['tools','editButton','moveButton','alignButton','alignMenu','commitButton','cancelButton','sentBadge','deleteButton','rotationInput'].map(key=>[key,new Control()]));
  controls.rotationInput.label=new Control();controls.rotationInput.validity={valid:true};
  const element=new Control();Object.assign(element,{namespaceURI:'http://www.w3.org/1999/xhtml',tagName:'P',textContent:'Original',innerText:'Original',innerHTML:'Original'});
  let currentAnchor={deck_id:'deck',slide_id:'slide',content_hash:'original-hash',target:{selector:'p',html:'<p>Original</p>'}},fail=sendError;
  const submissions=[],errors=[];
  const editor=sandbox.window.SlideEdits.create({...controls,properties,anchor:()=>structuredClone(currentAnchor),onSend:async request=>{submissions.push(request);if(fail){fail=false;throw new Error('offline');}return {event_id:3};},onChange(){},onError:message=>errors.push(message)});
  editor.bind(doc);editor.select(element);
  return {editor,controls,element,submissions,errors,changeAnchor:()=>{currentAnchor={...currentAnchor,content_hash:'new-hash'};}};
}
const settled=()=>new Promise(resolve=>setImmediate(resolve));

test('trash stages a cancellable proposal and navigation cannot silently send it',async()=>{
  const {editor,controls,element,submissions,errors}=fixture();
  controls.deleteButton.fire('click');
  assert.equal(editor.isEditing(),true);
  assert.equal(element.innerHTML,'Original');
  assert.equal(submissions.length,0);
  assert.equal(await editor.finish(),false);
  assert.deepEqual(errors,['Send or cancel the edit first.']);
  controls.cancelButton.fire('click');
  assert.equal(editor.isEditing(),false);
  assert.equal(submissions.length,0);
});

test('explicit Send retains the original deletion anchor and retry identifier',async()=>{
  const {editor,controls,element,submissions,changeAnchor}=fixture({sendError:true});
  controls.deleteButton.fire('click');changeAnchor();
  controls.commitButton.fire('click');await settled();
  assert.equal(editor.isEditing(),true);
  controls.commitButton.fire('click');await settled();
  assert.equal(editor.isEditing(),false);
  assert.equal(submissions.length,2);
  assert.equal(submissions[0].request_id,submissions[1].request_id);
  assert.equal(submissions[1].content_hash,'original-hash');
  assert.equal(submissions[1].target.selector,'p');
  assert.equal(JSON.stringify(submissions[1].edit_intent),JSON.stringify({action:'delete',values:{}}));
  assert.equal(element.innerHTML,'Original');
});

test('numeric rotation and fine movement share one reviewable transform request',async()=>{
  const {editor,controls,submissions,element}=fixture();
  Object.assign(controls.rotationInput,{value:'30',valueAsNumber:30});controls.rotationInput.fire('input');
  assert.equal(editor.isEditing(),true);
  assert.match(element.style.transform,/rotate\(30deg\)/);
  assert.equal(submissions.length,0);
  controls.moveButton.fire('keydown',{key:'ArrowRight'});
  controls.commitButton.fire('click');await settled();
  assert.equal(submissions[0].edit_intent.action,'transform');
  assert.equal(submissions[0].edit_intent.values.rotation_degrees,30);
  const rect=element.getBoundingClientRect();
  assert.equal(submissions[0].edit_intent.values.x,rect.left/800);
  assert.equal(submissions[0].edit_intent.values.y,rect.top/450);
  assert.equal(submissions[0].edit_intent.values.position_reference,'visual_top_left');
  assert.equal(await editor.finish(),true);
  editor.reflow();
  assert.match(element.style.transform,/rotate\(30deg\)/);
});

test('cancelling a rotation restores the element and numeric control',()=>{
  const {editor,controls,element,submissions}=fixture();
  controls.rotationInput.ownerDocument.activeElement=controls.rotationInput;
  Object.assign(controls.rotationInput,{value:'-45',valueAsNumber:-45});controls.rotationInput.fire('input');
  assert.match(element.style.transform,/rotate\(-45deg\)/);
  editor.cancel();
  assert.equal(element.style.transform,undefined);
  assert.equal(controls.rotationInput.value,'0');
  assert.equal(editor.isEditing(),false);
  assert.equal(submissions.length,0);
});

test('property patches stay provisional, retain the original anchor and guard geometry',async()=>{
  const {editor,controls,element,submissions,changeAnchor}=fixture();
  editor.previewProperties({text:'Changed',color:'#ff0000',font_size:24,font_size_unit:'px'});
  assert.equal(editor.isEditing(),true);
  assert.equal(element.textContent,'Changed');
  assert.equal(element.style.color,'#ff0000');
  assert.equal(element.style.fontSize,'24px');
  controls.moveButton.fire('keydown',{key:'ArrowRight'});
  assert.equal(element.style.transform,undefined);
  assert.equal(await editor.finish(),false);
  changeAnchor();
  assert.equal(await editor.commit(),true);
  assert.equal(submissions[0].content_hash,'original-hash');
  assert.equal(submissions[0].edit_intent.action,'update');
  assert.equal(JSON.stringify(submissions[0].edit_intent.values),JSON.stringify({text:'Changed',color:'#ff0000',font_size:24,font_size_unit:'px'}));
});

test('property cancellation restores the original and style-only requests are distinct',async()=>{
  const {editor,element,submissions}=fixture();
  editor.previewProperties({color:'#ff0000'});
  editor.cancel();
  assert.equal(element.innerHTML,'Original');
  assert.equal(element.style.color,undefined);
  editor.previewProperties({font_weight:'bold'});
  await editor.commit();
  assert.equal(submissions[0].edit_intent.action,'style');
});


test('reverting property patches restores the original preview and clears the draft',()=>{
  const properties={setSelection(){},setState(){},clear(){}};
  const {editor,element}=fixture({properties});
  editor.previewProperties({text:'Changed'});
  editor.previewProperties({color:'#ff0000'});
  editor.previewProperties({font_size:24,font_size_unit:'px'});
  editor.previewProperties({text:'Original'});
  editor.previewProperties({color:'#000000'});
  editor.previewProperties({font_size:16,font_size_unit:'px'});
  assert.equal(element.innerHTML,'Original');
  assert.equal(element.style.color,undefined);
  assert.equal(element.style.fontSize,undefined);
  assert.equal(editor.isEditing(),false);
});
