const {test}=require('node:test');
const assert=require('node:assert/strict');
const {sizeModel,nextSize,selectionLabel,summary}=require('../web/element-properties.js');

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
