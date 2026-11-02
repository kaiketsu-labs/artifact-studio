const {test}=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('../web/preview-updates.js');

test('preview auto-update defaults on and remembers an explicit manual choice',()=>{
  const values=new Map(),changes=[];
  const storage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};
  const preference=create({storage,onChange:value=>changes.push(value)});
  assert.equal(preference.enabled,true);
  preference.set(false);
  assert.equal(create({storage}).enabled,false);
  preference.set(true);
  assert.equal(create({storage}).enabled,true);
  assert.deepEqual(changes,[false,true]);
});

test('other tabs update the choice and unrelated storage events do not affect it',()=>{
  let listener,removed=false;const changes=[];
  const events={addEventListener:(name,fn)=>{listener=fn;},removeEventListener:()=>{removed=true;}};
  const preference=create({storage:{getItem:()=>null},events,onChange:value=>changes.push(value)});
  listener({key:'unrelated',newValue:'false'});assert.equal(preference.enabled,true);
  listener({key:'slide-studio:auto-update-previews',newValue:'false'});assert.equal(preference.enabled,false);
  listener({key:'slide-studio:auto-update-previews',newValue:null});assert.equal(preference.enabled,true);
  assert.deepEqual(changes,[false,true]);preference.stop();assert.equal(removed,true);
});

test('unavailable storage leaves the control usable for the current tab',()=>{
  const preference=create({storage:{getItem(){throw Error('denied');},setItem(){throw Error('denied');}}});
  assert.equal(preference.enabled,true);preference.set(false);assert.equal(preference.enabled,false);
});
