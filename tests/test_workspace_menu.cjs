const {test}=require('node:test');
const assert=require('node:assert/strict');
const {shortcut}=require('../web/workspace-menu.js');

test('file shortcuts follow the platform and keep new-slide shortcuts available',()=>{
  for(const mac of [false,true]){
    const modifier=mac?{metaKey:true}:{ctrlKey:true};
    for(const [key,action] of [['n','new'],['o','open'],['s','save']])assert.equal(shortcut({key,...modifier},{mac}),action);
    assert.equal(shortcut({key:'S',shiftKey:true,...modifier},{mac}),'files');
    assert.equal(shortcut({key:'N',shiftKey:true,...modifier},{mac}),null);
    assert.equal(shortcut({key:'m',...modifier},{mac}),null);
  }
  assert.equal(shortcut({key:'s',ctrlKey:true},{mac:true}),null);
  assert.equal(shortcut({key:'s',metaKey:true}),null);
});
test('slideshow shortcuts distinguish the beginning from the current slide',()=>{
  assert.equal(shortcut({key:'F5'}),'present-first');
  assert.equal(shortcut({key:'F5',shiftKey:true}),'present-current');
  assert.equal(shortcut({key:'Enter',metaKey:true},{mac:true}),'present-current');
  assert.equal(shortcut({key:'Enter',metaKey:true,shiftKey:true},{mac:true}),'present-first');
  assert.equal(shortcut({key:'Enter',ctrlKey:true}),null);
  assert.equal(shortcut({key:'F10'}),'menu');
  assert.equal(shortcut({key:'F10',shiftKey:true}),null);
});
test('typing, modal editors and browser modifiers do not trigger workspace actions',()=>{
  for(const key of ['n','o','Enter','F5','F10'])assert.equal(shortcut({key,metaKey:true},{mac:true,editing:true}),null);
  assert.equal(shortcut({key:'s',metaKey:true},{mac:true,editing:true}),'save');
  assert.equal(shortcut({key:'s',metaKey:true,shiftKey:true},{mac:true,editing:true}),null);
  for(const flag of ['defaultPrevented','isComposing','repeat','altKey'])assert.equal(shortcut({key:'s',metaKey:true,[flag]:true},{mac:true}),null);
  assert.equal(shortcut({key:'s',metaKey:true},{mac:true,modal:true}),null);
  assert.equal(shortcut({key:'s',metaKey:true,ctrlKey:true},{mac:true}),null);
  assert.equal(shortcut({key:'F5',ctrlKey:true}),null);
});
