const {test}=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('../web/presentation-launcher.js');

/** Model fullscreen events and frame messages without granting synthetic activation. */
function fixture({unsupported=false,reject=false,prepare=null,onError=()=>{}}={}){
  const order=[],messages=[],handlers={},classes=new Set();let returned=null,unavailable=0;
  const doc={fullscreenElement:null,documentElement:{},body:{children:[],classList:{add:value=>classes.add(value),remove:value=>classes.delete(value)},append(element){this.children.push(element);}},addEventListener(type,listener){handlers[type]=listener;}};
  const view={location:{origin:'http://localhost',href:'http://localhost/?deck=test'},addEventListener(type,listener){handlers[type]=listener;}};
  class Element{
    constructor(tag){this.tag=tag;this.handlers={};this.inert=false;this.contentWindow={postMessage:data=>messages.push(data)};}
    addEventListener(type,listener){this.handlers[type]=listener;}
    append(child){this.child=child;}
    focus(){this.focused=true;}
    remove(){this.removed=true;}
    getAttribute(){return '/decks/test/index.html?present=1#cover';}
    set src(value){order.push('navigate');this.url=value;}
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
  const message=(data,extra={})=>handlers.message({source:frame().contentWindow,origin:view.location.origin,data:{type:'artifact-studio:presentation',...data},...extra});
  return {doc,handlers,launcher,button,fullscreenButton,sibling,order,messages,classes,click,layer,frame,message,returned:()=>returned,unavailable:()=>unavailable};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('pending notes save after fullscreen activation and before the presentation loads',async()=>{
  let finish;const saving=new Promise(resolve=>{finish=resolve;});
  const check=fixture({prepare:()=>saving});check.click();
  assert.deepEqual(check.order,['fullscreen']);
  await settle();assert.deepEqual(check.order,['fullscreen']);
  finish();await settle();assert.deepEqual(check.order,['fullscreen','navigate']);
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
