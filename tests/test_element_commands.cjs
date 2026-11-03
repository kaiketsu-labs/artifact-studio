const {test}=require('node:test');
const assert=require('node:assert/strict');
const {history,shortcut,framePoint}=require('../web/element-commands.js');

test('element history keeps immutable states, branches after undo and has a bounded lifetime',()=>{
  const edits=history(2),before={text:'original'},after={text:'one'};
  edits.record(before,after);after.text='later typing';
  assert.deepEqual(edits.undo(),before);assert.deepEqual(edits.redo(),{text:'one'});
  edits.record({text:'one'},{text:'two'});edits.record({text:'two'},{text:'three'});
  assert.deepEqual(edits.undo(),{text:'two'});assert.deepEqual(edits.undo(),{text:'one'});assert.equal(edits.undo(),undefined);
  edits.record({text:'one'},{text:'new branch'});assert.equal(edits.canRedo(),false);
  edits.clear();assert.equal(edits.canUndo(),false);assert.equal(edits.canRedo(),false);
});
test('canvas commands preserve native typing, composition and reserved modifiers',()=>{
  assert.equal(shortcut({key:'z',ctrlKey:true}),'undo');
  assert.equal(shortcut({key:'z',metaKey:true,shiftKey:true}),'redo');
  assert.equal(shortcut({key:'y',ctrlKey:true}),'redo');
  assert.equal(shortcut({key:'Delete'}),'delete');
  assert.equal(shortcut({key:'Backspace',repeat:true}),'delete');
  assert.deepEqual(shortcut({key:'ArrowRight',shiftKey:true}),{action:'nudge',key:'ArrowRight',step:10});
  assert.equal(shortcut({key:'F10',shiftKey:true}),'menu');
  assert.equal(shortcut({key:'Enter',target:{closest:selector=>selector.includes('button')?{}:null}}),null);
  for(const extra of [{isComposing:true},{keyCode:229},{defaultPrevented:true},{altKey:true},{target:{closest:()=>({})}}])assert.equal(shortcut({key:'z',ctrlKey:true,...extra}),null);
});
test('iframe menu coordinates are scaled and untrusted out-of-frame values fail closed',()=>{
  const frame={getBoundingClientRect:()=>({left:100,top:50,width:400,height:200})};
  assert.deepEqual(framePoint(frame,500,250,{width:1000,height:500}),{x:300,y:150});
  for(const x of [-1,1001,Infinity,NaN,'10'])assert.equal(framePoint(frame,x,250,{width:1000,height:500}),null);
});
