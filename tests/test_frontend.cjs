const {test}=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('../web/drafts.js');
const {PresentationClock,nextState,nextBuildState,buildCount,shownIndices,startIndex,duration,keyAction}=require('../runtime/presentation.js');
const {forSlide,migrateDraft}=require('../web/conversations.js');
const {movePosition,moveMessage}=require('../web/slide-edits.js');
const {needsSource}=require('../web/rules-editor.js');
const {lexer}=require('../web/vendor/marked-18.1.2.js');

test('rules with unsupported Markdown retain source editing instead of lossy rich conversion',()=>{
  assert.equal(needsSource(lexer('# Rules\n\n- **Verify** every chart.\n- [Source](https://example.com)\n\n### Notes\n\n> Keep it brief.')),false);
  for(const markdown of ['| A | B |\n| --- | --- |\n| 1 | 2 |','![Chart](chart.png)','---','~~Removed~~','<div>Custom content</div>','- [x] Reviewed','```python\nprint(1)\n```','- Rule\n  - ![Nested image](chart.png)'])assert.equal(needsSource(lexer(markdown)),true,markdown);
});

const storage=()=>{const values=new Map();return {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};};

test('a reload restores targeted feedback with the original slide and revision',()=>{
  const local=storage(),first=create(local);
  first.feedback.set('deck-a',{feedback:'Make this shorter',slide_id:'cover',context_slide_id:'cover',content_hash:'original',request_id:'retry-same-request',target:{selector:'h1',label:'Heading'}});
  first.feedback.set('deck-b',{feedback:'Add a conclusion',target:null});
  first.notes.set('deck-a/cover',{notes:'A brief off-slide example',expected_notes:'Original notes'});
  assert.equal(first.persist(),true);
  const restored=create(local);
  assert.equal(restored.feedback.get('deck-a').content_hash,'original');
  assert.equal(restored.feedback.get('deck-a').request_id,'retry-same-request');
  assert.equal(restored.feedback.get('deck-b').feedback,'Add a conclusion');
  assert.equal(restored.notes.get('deck-a/cover').expected_notes,'Original notes');
  restored.feedback.clear();restored.notes.clear();restored.persist();
  assert.equal(create(local).feedback.size,0);
});

test('corrupt or unavailable storage cannot prevent the workspace from opening',()=>{
  const local=storage();local.setItem('slide-studio:drafts:v1','invalid JSON');
  assert.equal(create(local).feedback.size,0);
  local.setItem('slide-studio:drafts:v1',JSON.stringify({version:1,notes:[['x',{notes:4}]],feedback:[['x',{feedback:7}]]}));
  assert.equal(create(local).notes.size,0);assert.equal(create(local).feedback.size,0);
  const unavailable=create({getItem(){throw new Error('Denied');},setItem(){throw new Error('Denied');},removeItem(){throw new Error('Denied');}});
  assert.equal(unavailable.persist(),false);
});

test('presentation time pauses, resumes, resets and passes an hour correctly',()=>{
  let now=0;const clock=new PresentationClock(()=>now);clock.start();now=65000;
  assert.equal(duration(clock.elapsed()),'01:05');clock.pause();now=95000;
  assert.equal(duration(clock.elapsed()),'01:05');clock.start();now=100000;
  assert.equal(duration(clock.elapsed()),'01:10');clock.reset();assert.equal(clock.elapsed(),0);
  now=3701000;assert.equal(duration(clock.elapsed()),'1:00:01');
});

test('navigation reveals before advancing and keeps both ends stable',()=>{
  assert.deepEqual(nextState(0,false,-1,true,3),{current:0,revealed:false});
  assert.deepEqual(nextState(0,false,1,true,3),{current:0,revealed:true});
  assert.deepEqual(nextState(0,true,1,true,3),{current:1,revealed:false});
  assert.deepEqual(nextState(1,false,-1,false,3),{current:0,revealed:true});
  assert.deepEqual(nextState(0,true,-1,true,3),{current:0,revealed:false});
  assert.deepEqual(nextState(2,true,1,true,3),{current:2,revealed:true});
});

test('slideshow keys advance without hijacking editing, controls or browser shortcuts',()=>{
  const active={active:true};
  for(const key of ['Enter',' ','n','s','f','a','9'])assert.equal(keyAction({key},active),'next');
  for(const key of ['ArrowLeft','ArrowUp','PageUp','Backspace'])assert.equal(keyAction({key},active),'previous');
  assert.equal(keyAction({key:'Home'},active),'first');
  assert.equal(keyAction({key:'End'},active),'last');
  assert.equal(keyAction({key:'Escape'},{...active,editing:true}),'end');
  assert.equal(keyAction({key:'Escape'},{...active,dialog:true}),null);
  for(const key of ['Tab','Shift','Control','Meta','Alt','F1'])assert.equal(keyAction({key},active),null);
  for(const flag of ['ctrlKey','metaKey','altKey','isComposing','repeat','defaultPrevented'])assert.equal(keyAction({key:'n',[flag]:true},active),null);
  assert.equal(keyAction({key:'n'},{...active,editing:true}),null);
  assert.equal(keyAction({key:'ArrowLeft'},{...active,editing:true}),null);
  assert.equal(keyAction({key:'Enter'},{...active,control:true}),null);
  assert.equal(keyAction({key:' '},{...active,control:true}),null);
  assert.equal(keyAction({key:'ArrowRight'},{...active,control:true}),'next');
  assert.equal(keyAction({key:'n'}),null);
});

test('numbered chart reveals advance independently and restore when navigating backward',()=>{
  assert.equal(buildCount('<div data-build="1"></div><g data-build="3"></g>'),3);
  assert.equal(buildCount('<div data-builder><div data-build-mode>'),0);
  assert.equal(buildCount('<div data-build>'),1);
  assert.equal(buildCount('<div data-build="invalid">'),1);
  const counts=[0,3,1,0];let state={current:0,revealed:0};
  for(const [current,revealed] of [[1,0],[1,1],[1,2],[1,3],[2,0],[2,1],[3,0],[3,0]]){
    state=nextBuildState(state.current,state.revealed,1,counts);assert.deepEqual(state,{current,revealed});
  }
  for(const [current,revealed] of [[2,1],[2,0],[1,3],[1,2],[1,1],[1,0],[0,0],[0,0]]){
    state=nextBuildState(state.current,state.revealed,-1,counts);assert.deepEqual(state,{current,revealed});
  }
});

test('slideshow navigation skips hidden runs while retaining visible reveals and original indices',()=>{
  const counts=[0,2,3,0,1,0],hidden=[true,false,true,true,false,true];
  assert.deepEqual(shownIndices(hidden.map(hidden=>({hidden}))),[1,4]);
  assert.deepEqual(shownIndices([{},{hidden:false}]),[0,1]);
  assert.deepEqual(shownIndices([{hidden:true}]),[]);
  assert.deepEqual(nextBuildState(1,0,-1,counts,hidden),{current:1,revealed:0});
  let state={current:1,revealed:0};
  for(const [current,revealed] of [[1,1],[1,2],[4,0],[4,1],[4,1]]){
    state=nextBuildState(state.current,state.revealed,1,counts,hidden);assert.deepEqual(state,{current,revealed});
  }
  assert.deepEqual(nextBuildState(4,0,-1,counts,hidden),{current:1,revealed:2});
  assert.deepEqual(nextBuildState(2,3,1,counts,hidden),{current:4,revealed:0});
  assert.deepEqual(nextBuildState(2,0,-1,counts,hidden),{current:1,revealed:2});
  assert.deepEqual(nextBuildState(0,0,1,[0,0],[true,true]),{current:0,revealed:0});
});

test('starting a show skips the selected hidden slide and handles an entirely hidden deck',()=>{
  const slides=[{hidden:true},{hidden:false},{hidden:true},{hidden:false},{hidden:true}];
  assert.equal(startIndex(slides),1);
  assert.equal(startIndex(slides,1),1);
  assert.equal(startIndex(slides,2),3);
  assert.equal(startIndex(slides,4),1);
  assert.equal(startIndex([{hidden:true},{hidden:true}],1),-1);
  assert.equal(startIndex([{},{}],1),1);
});

test('slide chats keep legacy requests and replies together without repeating global history',()=>{
  const deck={id:'deck',slides:[{id:'cover',feedback:[{event_id:4,slide_id:'cover'}]},{id:'story',feedback:[{event_id:5,slide_id:'story'}]}],feedback:[{event_id:1},{event_id:2,context_slide_id:'story'},{event_id:3,reply_to:2},{event_id:6,reply_to:1}]};
  assert.deepEqual(forSlide(deck,'cover').map(item=>item.event_id),[1,4,6]);
  assert.deepEqual(forSlide(deck,'story').map(item=>item.event_id),[2,3,5]);
});

test('per-slide drafts restore independently and legacy anchors keep their original revision',()=>{
  const local=storage(),store=create(local),deck={id:'deck',slides:[{id:'cover'},{id:'story'}]};
  store.feedback.set('deck',{feedback:'Keep my original target',slide_id:'story',context_slide_id:'story',content_hash:'old-hash',target:{selector:'h2',label:'Heading'}});
  assert.equal(migrateDraft(store.feedback,deck),true);
  store.feedback.set('deck/cover',{feedback:'Add a second slide',slide_id:'cover'});store.persist();
  const restored=create(local);
  assert.equal(restored.feedback.get('deck/story').content_hash,'old-hash');
  assert.equal(restored.feedback.get('deck/story').target.selector,'h2');
  assert.equal(restored.feedback.get('deck/cover').feedback,'Add a second slide');
  assert.equal(restored.feedback.has('deck'),false);
});

test('drag proposals use slide coordinates and stay within the canvas at any preview scale',()=>{
  const small=movePosition({left:100,top:50,width:200,height:100},80,40,{width:800,height:450});
  const large=movePosition({left:200,top:100,width:400,height:200},160,80,{width:1600,height:900});
  assert.deepEqual(small,large);assert.deepEqual(small,{x:.225,y:.2});
  assert.deepEqual(movePosition({left:100,top:50,width:200,height:100},999,-999,{width:800,height:450}),{x:.75,y:0});
  assert.match(moveMessage(small),/22.5% from the left and 20.0% from the top/);
});
