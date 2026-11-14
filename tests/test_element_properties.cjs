const {test}=require('node:test');
const assert=require('node:assert/strict');
const {sizeModel,nextSize,selectionLabel,summary,frameAnchor,toolbarPosition}=require('../web/element-properties.js');

test('document sizes preserve responsive CSS and restore the exact source value',()=>{
  const model=sizeModel({font_size:4.625,font_size_unit:'cqw',font_size_px:59.2});
  assert.equal(model.display,44.4);
  assert.deepEqual(model.patch(48),{font_size:5,font_size_unit:'cqw'});
  assert.deepEqual(model.patch(44.4),{font_size:4.625,font_size_unit:'cqw'});
  const relative=sizeModel({font_size:2,font_size_unit:'rem',font_size_px:32});
  assert.deepEqual(relative.patch(30),{font_size:2.5,font_size_unit:'rem'});
  const tiny=sizeModel({font_size:.00001,font_size_unit:'cqw',font_size_px:16});
  assert.ok(tiny.patch(18).font_size>0);
});

test('size controls use familiar increasing and decreasing document sizes',()=>{
  assert.equal(nextSize(18,1),20);
  assert.equal(nextSize(20,-1),18);
  assert.equal(nextSize(44.4,1),48);
  assert.equal(nextSize(1,-1),1);
  assert.equal(nextSize(720,1),792);
});

test('content labels and chat descriptions contain no HTML tags or CSS units',()=>{
  assert.equal(selectionLabel({label:'h2',canText:true}),'Heading');
  assert.equal(selectionLabel({label:'p',canText:true}),'Text');
  assert.equal(selectionLabel({label:'svg'}),'Graphic');
  assert.equal(selectionLabel({label:'#revenue:nth-child(2)'}),'Selection');
  assert.equal(selectionLabel({label:'Quarterly revenue'}),'Quarterly revenue');
  assert.deepEqual(summary({action:'style',values:{font_size:5,font_size_unit:'cqw',color:'#ff0000'}}),['Text size changed','Text color changed']);
});


test('frame anchor translates and scales iframe coordinates including borders',()=>{
  const frame={getBoundingClientRect:()=>({left:100,top:50,width:400,height:300}),offsetWidth:800,offsetHeight:600,clientLeft:2,clientTop:4};
  assert.deepEqual(frameAnchor({left:20,top:30,width:100,height:40},frame),{
    left:111,top:67,width:50,height:20,bounds:{left:100,top:50,width:400,height:300}
  });
  assert.equal(frameAnchor(null,frame),null);
});

test('toolbar stays within viewport at each edge and flips below near the top',()=>{
  const viewport={width:800,height:600},size={width:220,height:48};
  for(const anchor of [
    {left:0,top:0,width:20,height:20},
    {left:790,top:0,width:10,height:20},
    {left:0,top:590,width:20,height:10},
    {left:790,top:590,width:10,height:10},
  ]){
    const point=toolbarPosition(anchor,size,viewport);
    assert.ok(point.left>=0 && point.left+size.width<=viewport.width);
    assert.ok(point.top>=0 && point.top+size.height<=viewport.height);
  }
  assert.ok(toolbarPosition({left:20,top:5,width:20,height:20},size,viewport).top>=25);
  assert.ok(toolbarPosition({left:20,top:-300,width:20,height:20},size,viewport).top>=0);
});

test('narrow fitted slide keeps formatting outside its selectable objects',()=>{
  const bounds={left:12,top:150,width:366,height:206};
  const anchor={left:30,top:162,width:250,height:24,bounds};
  const size={width:374,height:76};
  const point=toolbarPosition(anchor,size,{width:390,height:844});
  assert.ok(point.top>=bounds.top+bounds.height);
  assert.ok(point.top+size.height<=844-48);
  assert.ok(point.left>=8&&point.left+size.width<=390-8);
  const desktop=toolbarPosition(anchor,size,{width:1000,height:844});
  assert.ok(desktop.top<bounds.top+bounds.height);
});

test('narrow toolbar stays visible when its preview is scrolled offscreen',()=>{
  const size={width:374,height:76},viewport={width:390,height:844};
  for(const frameTop of [-1000,1000]){
    const bounds={left:12,top:frameTop,width:366,height:206};
    const point=toolbarPosition({left:30,top:frameTop+100,width:250,height:24,bounds},size,viewport);
    assert.ok(point.top>=8);
    assert.ok(point.top+size.height<=viewport.height-48);
  }
});
