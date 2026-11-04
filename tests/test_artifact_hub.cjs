const {test}=require('node:test');
const assert=require('node:assert/strict');
const marked=require('../web/vendor/marked-18.1.2.js');
const {filterArtifacts,groupTargets,selectTargets,bundlePath,renderDocument,feedbackTarget,filePath,exportFile}=require('../web/hub.js');
const {sortArtifacts,artifactType,validSort}=require('../web/hub.js');

test('library sorts natural names, distinct dates and missing timestamps without mutating input',()=>{
  const items=[
    {id:'ten',title:'Draft 10',created_at:'2026-01-01T10:00:00Z',updated_at:'2026-01-03T10:00:00Z'},
    {id:'two',title:'draft 2',created_at:'2026-01-02T10:00:00Z',updated_at:'2026-01-02T10:00:00Z'},
    {id:'unknown',title:'Archive',created_at:'',updated_at:'invalid'},
  ];
  const ids=options=>sortArtifacts(items,options).map(item=>item.id);
  assert.deepEqual(ids({by:'name',direction:'asc'}),['unknown','two','ten']);
  assert.deepEqual(ids({by:'created',direction:'desc'}),['two','ten','unknown']);
  assert.deepEqual(ids({by:'updated',direction:'desc'}),['ten','two','unknown']);
  assert.deepEqual(ids({by:'updated',direction:'asc'}),['two','ten','unknown']);
  assert.deepEqual(items.map(item=>item.id),['ten','two','unknown']);
});

test('file-type sorting groups actual formats and keeps names natural inside each group',()=>{
  const items=[
    {id:'a',title:'Diagram 10',kind:'svg',entrypoint:'cover.SVG'},
    {id:'b',title:'Website',kind:'html',entrypoint:'index.htm'},
    {id:'c',title:'Diagram 2',kind:'image',entrypoint:'diagram.svg'},
    {id:'d',title:'Deck',kind:'presentation',entrypoint:'index.html'},
    {id:'e',title:'Photo',kind:'image',entrypoint:'photo.png'},
  ];
  assert.deepEqual(sortArtifacts(items,{by:'type',direction:'asc'}).map(item=>item.id),['b','e','d','c','a']);
  assert.equal(artifactType(items[0]).label,'SVG');
  assert.equal(artifactType(items[3]).key,'presentation');
  assert.equal(artifactType({kind:'markdown',entrypoint:'guide.markdown'}).key,'md');
  assert.equal(artifactType({kind:'other',entrypoint:'README'}).label,'Files');
  assert.equal(artifactType({kind:'file',entrypoint:'README.constructor'}).label,'CONSTRUCTOR');
  assert.equal(artifactType({kind:'markdown',entrypoint:'README'}).key,'md');
});

test('unusable stored sort preferences fall back to newest changes',()=>{
  for(const value of [null,{},'name',{by:'__proto__',direction:'asc'},{by:'name',direction:'sideways'}])assert.deepEqual(validSort(value),{by:'updated',direction:'desc'});
  assert.deepEqual(validSort({by:'type',direction:'asc'}),{by:'type',direction:'asc'});
});

test('generated exports accept portable text and reject unsafe paths, formats and payloads',()=>{
  for(const [name,type] of [['benchmark.html','text/html'],['data.json','application/json'],['results.csv','text/csv']]){
    assert.deepEqual(exportFile({name,type:type+';charset=utf-8',content:'<literal text>'}),{name,type:type+';charset=utf-8',content:'<literal text>'});
  }
  for(const name of ['../data.json','folder/data.json','folder\\data.json','.hidden.json','bad\nname.json','run.js','run.exe','constructor','x.constructor','html']){
    assert.equal(exportFile({name,type:'application/json',content:'{}'}),null,name);
  }
  for(const value of [null,'text',{}, {name:'data.json',type:'text/html',content:'{}'}, {name:'data.json',type:'application/json',content:{secret:true}}, {name:'data.json',type:'application/json',content:'x'.repeat(20*1024*1024+1)}])assert.equal(exportFile(value),null);
});

test('library combines project, format and case-insensitive search',()=>{
  const items=[
    {id:'a',project_id:'launch',project_title:'Launch',title:'Speaker deck',kind:'presentation',entrypoint:'deck.html'},
    {id:'b',project_id:'launch',project_title:'Launch',title:'Handout',kind:'markdown',entrypoint:'notes.md'},
    {id:'c',project_id:'other',project_title:'Other',title:'Cover',kind:'svg',entrypoint:'cover.svg'},
  ];
  assert.deepEqual(filterArtifacts(items,{project:'launch',kind:'markdown',search:'HAND'}).map(item=>item.id),['b']);
  assert.deepEqual(filterArtifacts(items,{kind:'image'}).map(item=>item.id),['c']);
  assert.deepEqual(filterArtifacts(items,{search:'notes.md'}).map(item=>item.id),['b']);
  assert.equal(filterArtifacts(items,{project:'missing'}).length,0);
});

test('additive selection toggles each anchor and creates a flat group',()=>{
  const first={kind:'element',selector:'#title',label:'Title'},second={kind:'element',selector:'#body',label:'Body'};
  const selected=selectTargets([first],second,true);
  assert.deepEqual(groupTargets(selected),{kind:'group',elements:[first,second]});
  assert.deepEqual(selectTargets(selected,first,true),[second]);
  assert.deepEqual(selectTargets(selected,first),[first]);
  assert.equal(groupTargets([]),null);
  assert.deepEqual(groupTargets([first]),first);
  const many=Array.from({length:100},(_,index)=>({kind:'element',selector:`#element-${index}`}));
  assert.equal(selectTargets(many,second,true).length,100);
});

test('strict feedback payload removes UI-only Markdown positions without mutating drafts',()=>{
  const text={kind:'text',quote:'Review this',block_index:2,start:12,end:23};
  const group={kind:'group',elements:[text,{kind:'region',region:{x:.1,y:.2,width:.3,height:.4,image_width:1000,image_height:500}}]};
  assert.deepEqual(feedbackTarget(group).elements[0],{kind:'text',quote:'Review this',start:12,end:23});
  assert.equal(text.block_index,2);
  assert.equal(feedbackTarget(null),null);
});

test('bundle links cannot escape the artifact or request arbitrary protocols',()=>{
  assert.equal(bundlePath('docs/guide.md','../images/cover.svg'),'images/cover.svg');
  assert.equal(bundlePath('docs/guide.md','./chart.png?size=2'),'docs/chart.png');
  for(const url of ['../../secret','%2e%2e/%2e%2e/private','https://external.test/x','//external.test/x','javascript:alert(1)','/api/hub','folder%2fsecret','..\\secret'])assert.equal(bundlePath('docs/guide.md',url),null,url);
});

test('Markdown keeps authored HTML literal and uses immutable owned image resources',()=>{
  const item={id:'guide',entrypoint:'docs/guide.md',revision:3};
  const html=renderDocument(marked,item,'# Guide\n\n<script>secret()</script>\n\n![Cover](../cover.svg)\n\n![Remote](https://remote.test/pixel.png)\n\n[Unsafe](javascript:alert(1))\n\n[Docs](https://example.com)');
  assert.match(html,/<h1 id="hub-md-guide" tabindex="-1">Guide<\/h1>/);
  assert.ok(!html.includes('<script>'));
  assert.match(html,/src="\/api\/artifacts\/guide\/files\/cover.svg\?revision=3"/);
  assert.ok(!html.includes('src="https://'));
  assert.ok(!html.includes('href="javascript:'));
  assert.match(html,/rel="noopener noreferrer"/);
  assert.equal(filePath(item,'images/space name.png'),'/api/artifacts/guide/files/images/space%20name.png?revision=3');
});

test('Markdown headings and table of contents retain safe in-document links',()=>{
  const item={id:'guide',entrypoint:'guide.md',revision:1};
  const html=renderDocument(marked,item,'[Jump](#next-steps)\n\n## Next steps\n\n## Next steps');
  assert.match(html,/<a href="#next-steps" data-hub-fragment="next-steps">Jump<\/a>/);
  assert.match(html,/id="hub-md-next-steps"/);
  assert.match(html,/id="hub-md-next-steps-1"/);
});
