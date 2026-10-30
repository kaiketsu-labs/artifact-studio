const {test}=require('node:test');
const assert=require('node:assert/strict');
const {alignPosition,localDelta,movePosition}=require('../web/slide-edits.js');

test('alignment uses actual slide bounds with a nonzero viewport origin',()=>{
  const canvas={left:100,top:50,width:1000,height:500},rect={left:350,top:175,width:250,height:100};
  assert.deepEqual(movePosition(rect,0,0,canvas),{x:.25,y:.25});
  assert.deepEqual(alignPosition(rect,canvas,'center'),{x:.375,y:.25});
  assert.deepEqual(alignPosition(rect,canvas,'middle'),{x:.25,y:.4});
  assert.deepEqual(alignPosition(rect,canvas,'right'),{x:.75,y:.25});
  assert.deepEqual(alignPosition(rect,canvas,'bottom'),{x:.25,y:.8});
  assert.deepEqual(alignPosition({...rect,width:1200},canvas,'right'),{x:0,y:.25});
});

test('position previews invert scaled and rotated ancestor coordinates',()=>{
  assert.deepEqual(localDelta(20,10,{xx:2,xy:0,yx:0,yy:.5}),{x:10,y:20});
  assert.deepEqual(localDelta(20,10,{xx:0,xy:-2,yx:2,yy:0}),{x:5,y:-10});
  assert.equal(localDelta(20,10,{xx:0,xy:0,yx:0,yy:0}),null);
});
