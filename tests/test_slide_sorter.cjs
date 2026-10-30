const {test}=require('node:test');
const assert=require('node:assert/strict');
const {fitZoom,nextIndex}=require('../web/slide-sorter.js');
const {dropPosition,insertSlides}=require('../web/slide-order.js');

test('fitting a deck reduces card size for longer decks and stays in overview mode',()=>{
  assert.equal(fitZoom(3,1200,500),50);
  assert.ok(fitZoom(29,1200,500)<fitZoom(3,1200,500));
  assert.ok(fitZoom(29,600,500)<=fitZoom(29,1200,500));
  for(const count of [1,18,29,100]){
    const zoom=fitZoom(count,1200,500);
    assert.ok(zoom>=10&&zoom<=50);assert.equal(zoom%5,0);
  }
});

test('grid arrows follow rows and columns without losing the last incomplete row',()=>{
  assert.equal(nextIndex(1,'ArrowDown',18,6),7);
  assert.equal(nextIndex(7,'ArrowUp',18,6),1);
  assert.equal(nextIndex(7,'ArrowRight',18,6),8);
  assert.equal(nextIndex(7,'ArrowLeft',18,6),6);
  assert.equal(nextIndex(16,'ArrowDown',18,6),17);
  assert.equal(nextIndex(0,'ArrowLeft',18,6),0);
});

test('drag insertion follows horizontal rows and preserves multi-slide order',()=>{
  const rectangles=Array.from({length:6},(_,index)=>({left:index%3*120,right:index%3*120+100,top:Math.floor(index/3)*100,bottom:Math.floor(index/3)*100+80}));
  assert.equal(dropPosition(rectangles,5,30,true),0);
  assert.equal(dropPosition(rectangles,110,30,true),1);
  assert.equal(dropPosition(rectangles,350,30,true),3);
  assert.equal(dropPosition(rectangles,5,130,true),3);
  assert.equal(dropPosition(rectangles,350,130,true),6);
  assert.equal(dropPosition(rectangles,250,90,true),2);
  assert.equal(dropPosition([],250,90,true),0);
  assert.deepEqual(insertSlides(['a','b','c','d','e'],['b','d'],2),['a','c','b','d','e']);
  assert.equal(dropPosition(rectangles,350,30),0);
});
