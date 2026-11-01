const {test}=require('node:test');
const assert=require('node:assert/strict');
const {split,remove,moveMembers}=require('../web/slide-sections.js');
const groups=[{id:'intro',title:'Intro',slide_ids:['a','b']},{id:'detail',title:'Detail',slide_ids:['c','d']}];
test('sections split a deck or existing group, preserving empty groups and slide order',()=>{
  assert.deepEqual(split([],['a','b','c'],'b','part','Part','default'),[{id:'default',title:'Default section',slide_ids:['a']},{id:'part',title:'Part',slide_ids:['b','c']}]);
  assert.deepEqual(split(groups,['a','b','c','d'],'b','new','New','default').map(group=>group.slide_ids),[['a'],['b'],['c','d']]);
  assert.throws(()=>split(groups,['a','b','c','d'],'c','empty','Empty','default'),/already starts a section/);
});
test('removing a section retains all slides in the same order',()=>{
  assert.deepEqual(remove(groups,'intro'),[{id:'detail',title:'Detail',slide_ids:['a','b','c','d']}]);
  assert.deepEqual(remove(groups,'detail'),[{id:'intro',title:'Intro',slide_ids:['a','b','c','d']}]);
  assert.deepEqual(remove([groups[0]],'intro'),[]);
});
test('keyboard moves and header drops assign moved slides without moving section names',()=>{
  assert.deepEqual(moveMembers(groups,['a','b','c','d'],['a','c','b','d'],['b']).map(group=>group.slide_ids),[['a'],['c','b','d']]);
  assert.deepEqual(moveMembers(groups,['a','b','c','d'],['a','b','d','c'],['d']).map(group=>group.slide_ids),[['a','b'],['d','c']]);
  assert.deepEqual(moveMembers(groups,['a','b','c','d'],['a','b','c','d'],['b'],'detail').map(group=>group.slide_ids),[['a'],['b','c','d']]);
  assert.deepEqual(groups[0].slide_ids,['a','b']);
});
