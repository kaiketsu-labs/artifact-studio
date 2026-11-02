const {test}=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('../web/appearance.js');

/** Exercise first-paint preferences without needing the later menu DOM. */
function fixture(saved=null,{dark=false,unavailable=false}={}){
  const events={},writes=[],doc={documentElement:{dataset:{}},readyState:'loading',addEventListener(){}};
  const media={matches:dark,addEventListener(type,listener){events.media=listener;}};
  const storage={getItem(){if(unavailable)throw Error('Blocked');return saved;},setItem(key,value){if(unavailable)throw Error('Blocked');writes.push([key,value]);}};
  const view={addEventListener(type,listener){events[type]=listener;}};
  const appearance=create({doc,view,media,storage});
  return {doc,media,events,writes,appearance};
}
test('system appearance applies before paint and follows operating-system changes',()=>{
  const check=fixture(null,{dark:true});assert.equal(check.doc.documentElement.dataset.theme,'dark');
  check.media.matches=false;check.events.media();assert.equal(check.doc.documentElement.dataset.theme,'light');
});
test('an explicit choice persists and wins over system changes; other tabs stay in sync',()=>{
  const check=fixture('light',{dark:true});assert.equal(check.doc.documentElement.dataset.theme,'light');
  check.appearance.choose('dark');assert.deepEqual(check.writes,[['artifact-studio:appearance','dark']]);
  check.media.matches=false;check.events.media();assert.equal(check.doc.documentElement.dataset.theme,'dark');
  check.events.storage({key:'artifact-studio:appearance',newValue:'light'});assert.equal(check.doc.documentElement.dataset.theme,'light');
  check.events.storage({key:'unrelated',newValue:'dark'});assert.equal(check.doc.documentElement.dataset.theme,'light');
  check.events.storage({key:null,newValue:null});assert.equal(check.appearance.preference(),'system');
});
test('unavailable storage and invalid preferences cannot break startup or toggling',()=>{
  const check=fixture(null,{unavailable:true});check.appearance.choose('dark');assert.equal(check.doc.documentElement.dataset.theme,'dark');
  assert.equal(fixture('invalid',{dark:true}).doc.documentElement.dataset.theme,'dark');
});
