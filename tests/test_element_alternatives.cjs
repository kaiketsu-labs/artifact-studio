const {test}=require('node:test');
const assert=require('node:assert/strict');
const {revealOffset,panelPosition}=require('../web/element-alternatives.js');

test('a partly visible selected card aligns to its snap point instead of snapping back',()=>{
  const strip={left:20,right:300};
  assert.equal(revealOffset({left:209,right:389},strip,2),189);
  assert.equal(revealOffset({left:22,right:202},strip,189),189);
});

test('headline options leave their hover badge clickable and stay within the slide',()=>{
  const anchor={left:305,top:236,right:889,bottom:272};
  const size={width:640,height:144};
  const position=panelPosition(anchor,size,{width:1600,height:1000},{left:244,right:1205,width:961},{alignEnd:true});
  assert.deepEqual(position,{left:249,top:66});
  assert.ok(position.top+size.height<=anchor.top-26);
});

test('text options move below their target instead of covering the headline',()=>{
  const position=panelPosition({left:305,top:354,right:618,bottom:404},{width:640,height:167},{width:1600,height:1000},{left:244,right:1205,width:961},{avoid:{top:236,bottom:272}});
  assert.equal(position.top,416);
});

test('narrow options keep the selected element visible and remain in the viewport',()=>{
  const size={width:296,height:200};
  const position=panelPosition({left:38,top:194,right:285,bottom:210},size,{width:320,height:700},{left:16,right:304,width:288});
  assert.deepEqual(position,{left:12,top:222});
  assert.ok(position.left+size.width<=320);
  assert.ok(position.top+size.height<=700);
});
