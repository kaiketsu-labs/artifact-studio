const test=require('node:test');
const assert=require('node:assert/strict');
const {placement,projectsIn,markup}=require('../web/project-tree.js');
const {filterArtifacts}=require('../web/hub.js');
const tree={nodes:[
  {id:'allianz',kind:'folder',title:'Allianz',parent_id:null},
  {id:'research',kind:'folder',title:'Research',parent_id:'allianz'},
  {id:'ocr',kind:'project',parent_id:'research'},
  {id:'benchmark',kind:'project',parent_id:'allianz'},
  {id:'travel',kind:'project',parent_id:null},
]};
test('folder filters include nested project artifacts and exclude other work',()=>{
  assert.deepEqual(projectsIn(tree,'allianz'),['ocr','benchmark']);
  assert.deepEqual(filterArtifacts([{project_id:'ocr',title:'OCR'},{project_id:'travel',title:'Trip'}],{projectIds:projectsIn(tree,'allianz')}).map(item=>item.title),['OCR']);
});
test('project and folder placements distinguish nesting from sibling reordering',()=>{
  assert.deepEqual(placement(tree,'travel','allianz'),{action:'move',id:'travel',parent_id:'allianz',before_id:null});
  assert.deepEqual(placement(tree,'travel','allianz','before'),{action:'move',id:'travel',parent_id:null,before_id:'allianz'});
  assert.deepEqual(placement(tree,'ocr',null),{action:'move',id:'ocr',parent_id:null,before_id:null});
  assert.equal(placement(tree,'allianz','research'),null);
  assert.equal(placement(tree,'allianz','ocr','before'),null);
  assert.equal(placement(tree,'research','research'),null);
  assert.equal(placement(tree,'ocr','travel'),null);
});
test('collapsed rows retain folder selection, distinct icons and safe labels',()=>{
  const html=markup({tree,projects:[{id:'ocr',title:'<OCR>',artifact_count:1},{id:'benchmark',title:'Benchmark',artifact_count:2},{id:'travel',title:'Travel',artifact_count:1}],selected:'folder:allianz',collapsed:new Set(['research']),icon:name=>`<svg data-icon="${name}"></svg>`});
  assert.match(html,/data-icon="folderOpen"/);assert.match(html,/data-icon="project"/);
  assert.match(html,/data-project="folder:allianz" aria-current="true"/);
  assert.doesNotMatch(html,/data-project="ocr"/);assert.match(html,/Expand Research/);
});
