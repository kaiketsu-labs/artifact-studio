const {test}=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('../web/presentation-launcher.js');

/** Model fullscreen events and frame messages without granting synthetic activation. */
function fixture({unsupported=false,reject=false,prepare=null,onError=()=>{}}={}){
  const order=[],messages=[],handlers={},classes=new Set();let returned=null,unavailable=0;
  const doc={fullscreenElement:null,documentElement:{},body:{children:[],classList:{add:value=>classes.add(value),remove:value=>classes.delete(value)},append(element){this.children.push(element);}},addEventListener(type,listener){handlers[type]=listener;}};
  const view={location:{origin:'http://localhost',href:'http://localhost/?deck=test'},addEventListener(type,listener){handlers[type]=listener;}};
  class Element{
    constructor(tag){this.tag=tag;this.handlers={};this.inert=false;this.style={};this.attributes={};this.contentWindow={location:{href:'about:blank',pathname:'blank'},postMessage:data=>messages.push(data)};}
    addEventListener(type,listener){this.handlers[type]=listener;}
    append(...children){this.children=children;this.child=children[0];}
    focus(){this.focused=true;this.focusCount=(this.focusCount||0)+1;}
    remove(){this.removed=true;}
    getAttribute(name){return name==='href'?'/decks/test/index.html?present=1#cover':this.attributes[name];}
    setAttribute(name,value){this.attributes[name]=value;}
    removeAttribute(name){delete this.attributes[name];}
    set src(value){order.push('navigate');this.url=value;this.contentWindow.location=new URL(value);}
  }
  doc.createElement=tag=>{
    const element=new Element(tag);
    if(!unsupported)element.requestFullscreen=async()=>{order.push('fullscreen');if(reject)throw Error('Denied');doc.fullscreenElement=element;handlers.fullscreenchange();};
    return element;
  };
  if(!unsupported)doc.documentElement.requestFullscreen=()=>{};
  doc.exitFullscreen=async()=>{doc.fullscreenElement=null;handlers.fullscreenchange();};
  const button=new Element('a'),fullscreenButton=new Element('button'),sibling=new Element('main');sibling.inert=true;doc.body.children.push(sibling);
  const launcher=create({button,additionalButtons:[fullscreenButton],doc,view,prepare,onError,onReturn:id=>{returned=id;},onUnavailable:()=>unavailable++});
  const click=(extra={})=>button.handlers.click({button:0,preventDefault(){},...extra});
  const layer=()=>doc.body.children.at(-1),frame=()=>layer().child;
  const loading=()=>layer().children[1];
  const message=(data,extra={})=>handlers.message({source:frame().contentWindow,origin:view.location.origin,data:{type:'artifact-studio:presentation',...data},...extra});
  return {doc,handlers,launcher,button,fullscreenButton,sibling,order,messages,classes,click,layer,frame,loading,message,returned:()=>returned,unavailable:()=>unavailable};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('pending notes save after fullscreen activation and before the presentation loads',async()=>{
  let finish;const saving=new Promise(resolve=>{finish=resolve;});
  const check=fixture({prepare:()=>saving});check.click();
  assert.deepEqual(check.order,['fullscreen']);
  assert.equal(check.loading().children[0].textContent,'Starting presentation…');
  assert.equal(check.loading().children[1].focused,true);
  assert.equal(check.frame().style.visibility,'hidden');
  await settle();assert.deepEqual(check.order,['fullscreen']);
  finish();await settle();assert.deepEqual(check.order,['fullscreen','navigate']);
  assert.equal(check.loading().children[0].textContent,'Loading slides…');
});

test('runtime readiness reveals and focuses the slide before slower assets finish loading',()=>{
  const check=fixture();check.click();
  check.message({action:'ready'});
  assert.equal(check.loading().removed,true);
  assert.equal(check.frame().style.visibility,'');
  assert.equal(check.frame().getAttribute('aria-hidden'),undefined);
  assert.equal(check.frame().focusCount,1);
  check.frame().handlers.load();
  assert.equal(check.frame().focusCount,1,'late document load must not steal focus from presenter controls');
});

test('the initial blank document cannot dismiss loading or take focus',async()=>{
  let finish;const check=fixture({prepare:()=>new Promise(resolve=>{finish=resolve;})});
  check.click();await settle();check.frame().handlers.load();
  assert.equal(check.loading().removed,undefined);assert.equal(check.frame().focused,undefined);
  check.loading().children[1].handlers.click();await settle();finish();await settle();
  assert.equal(check.launcher.isOpen(),false);assert.deepEqual(check.order,['fullscreen']);
  assert.equal(check.button.focused,true);
});

test('Escape can cancel loading even when native fullscreen is unavailable',async()=>{
  const check=fixture({unsupported:true});check.click();
  check.handlers.keydown({key:'Escape',preventDefault(){}});await settle();
  assert.equal(check.launcher.isOpen(),false);assert.equal(check.button.focused,true);
});

test('a failed note save returns to editing with an actionable error',async()=>{
  const errors=[],check=fixture({prepare:async()=>{throw Error('Review notes');},onError:message=>errors.push(message)});
  check.click();await settle();
  assert.equal(check.launcher.isOpen(),false);assert.deepEqual(errors,['Review notes']);
  assert.deepEqual(check.order,['fullscreen']);assert.equal(check.button.focused,true);
});

test('Present requests fullscreen synchronously before loading the slideshow',async()=>{
  const check=fixture();check.click();
  assert.deepEqual(check.order,['fullscreen','navigate']);
  assert.equal(check.doc.fullscreenElement,check.layer());
  assert.match(check.frame().url,/workspace=1/);
  check.frame().contentWindow.location.hash='#second';
  check.message({action:'state',slide_id:'second'});
  check.message({action:'end'});await settle();
  assert.equal(check.returned(),'second');assert.equal(check.launcher.isOpen(),false);
  assert.equal(check.sibling.inert,true);assert.equal(check.button.focused,true);
});
test('native Escape requests a graceful end, while deliberate display changes keep the show',async()=>{
  const check=fixture();check.click();await settle();
  check.message({action:'ready'});
  check.message({action:'fullscreen',mode:'leave'});await settle();
  assert.equal(check.messages.some(message=>message.action==='end'),false);
  check.message({action:'fullscreen',mode:'enter'});await settle();
  await check.doc.exitFullscreen();
  assert.equal(check.messages.at(-2).action,'end');assert.equal(check.launcher.isOpen(),true);
  check.message({action:'end'});await settle();assert.equal(check.launcher.isOpen(),false);
});
test('Escape still exits while the viewer is loading or uses an older bundled runtime',async()=>{
  const check=fixture();check.click();await settle();
  await check.doc.exitFullscreen();await settle();
  assert.equal(check.launcher.isOpen(),false);assert.equal(check.returned(),'cover');
});
test('opening another display can leave fullscreen before its asynchronous host message',async()=>{
  const check=fixture();check.click();await settle();check.message({action:'ready'});
  check.frame().handlers['artifact-studio:display-change']({detail:true});
  check.handlers.fullscreenchange();
  check.doc.fullscreenElement=null;
  check.frame().handlers['artifact-studio:display-change']({detail:true});
  await check.doc.exitFullscreen();await settle();
  assert.equal(check.launcher.isOpen(),true);
  assert.equal(check.messages.some(message=>message.action==='end'),false);
  check.message({action:'end'});await settle();
  assert.equal(check.launcher.isOpen(),false);
});
test('a blocked automatic popup does not suppress Escape from fullscreen',async()=>{
  const check=fixture();check.click();await settle();check.message({action:'ready'});
  check.frame().handlers['artifact-studio:display-change']({detail:true});
  check.frame().handlers['artifact-studio:display-change']({detail:false});
  await check.doc.exitFullscreen();await settle();
  assert.equal(check.messages.some(message=>message.action==='end'),true);
});
test('unavailable fullscreen keeps the show usable and reports the limitation',async()=>{
  for(const options of [{unsupported:true},{reject:true}]){
    const check=fixture(options);check.click();await settle();
    assert.equal(check.unavailable(),1);assert.equal(check.launcher.isOpen(),true);
    check.frame().handlers.load();assert.equal(check.frame().focused,true);
    check.message({action:'end'});await settle();assert.equal(check.returned(),'cover');
  }
});
test('foreign windows cannot close the show and modified clicks retain normal link behavior',()=>{
  const check=fixture();check.click({metaKey:true});assert.equal(check.launcher.isOpen(),false);
  check.click();check.message({action:'end'},{origin:'https://other.example'});
  check.message({action:'end'},{source:{}});assert.equal(check.launcher.isOpen(),true);
});
test('the bottom-right fullscreen trigger shares the launch path and receives return focus',async()=>{
  const check=fixture();check.fullscreenButton.handlers.click({button:0,preventDefault(){}});
  assert.deepEqual(check.order,['fullscreen','navigate']);
  check.message({action:'end'});await settle();
  assert.equal(check.fullscreenButton.focused,true);assert.equal(check.button.focused,undefined);
});
test('keyboard starts preserve fullscreen activation and choose beginning or current slide',()=>{
  for(const fromBeginning of [false,true]){
    const check=fixture();check.launcher.start({fromBeginning});
    assert.deepEqual(check.order,['fullscreen','navigate']);
    assert.equal(new URL(check.frame().url).hash,fromBeginning?'':'#cover');
    assert.equal(check.launcher.isOpen(),true);
  }
});
