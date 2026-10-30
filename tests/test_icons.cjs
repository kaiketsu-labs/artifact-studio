const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');

function fixture(){
  const window={},document={createElement:()=>({textContent:''})};
  vm.runInNewContext(fs.readFileSync(require.resolve('../web/icons.js'),'utf8'),{window,document});
  let html='',writes=0;const attributes=new Map();
  const element={title:'',disabled:true,classList:{add(){},toggle(){}},get innerHTML(){return html;},set innerHTML(value){writes++;html=value;},append(node){html+='<span>'+node.textContent+'</span>';},getAttribute:name=>attributes.get(name),setAttribute:(name,value)=>attributes.set(name,value)};
  return {element,button:window.SlideIcons.button,writes:()=>writes};
}

test('unchanged utility icons retain their markup across frequent workspace renders',()=>{
  const f=fixture();
  for(let i=0;i<100;i++)f.button(f.element,'eye','Hide slide 1');
  assert.equal(f.writes(),1);assert.equal(f.element.disabled,true);
  f.element.title='Custom tooltip suffix';f.button(f.element,'eye','Hide slide 1');
  assert.equal(f.element.title,'Hide slide 1');assert.equal(f.writes(),1);
  f.button(f.element,'eyeOff','Unhide slide 1');assert.equal(f.writes(),2);
  assert.equal(f.element.getAttribute('aria-label'),'Unhide slide 1');
});

test('label changes and externally replaced button contents remain recoverable',()=>{
  const f=fixture();f.button(f.element,'send','Send batch (1)',true);
  const first=f.element.innerHTML;f.button(f.element,'send','Send batch (1)',true);
  assert.equal(f.element.innerHTML,first);assert.equal(f.writes(),1);
  f.button(f.element,'send','Send batch (2)',true);assert.match(f.element.innerHTML,/Send batch \(2\)/);
  f.element.innerHTML='Temporary spinner';f.button(f.element,'send','Send batch (2)',true);
  assert.match(f.element.innerHTML,/<svg/);assert.equal(f.writes(),4);
  f.button(f.element,'send','Send batch (2)',false);assert.doesNotMatch(f.element.innerHTML,/<span/);
});
