const {test}=require('node:test');
const assert=require('node:assert/strict');
const {shownIndices,updateProgressRail,keyAction}=require('../runtime/presentation.js');

/** Model the rail's native buttons without opening any presentation or browser. */
function rail(slides){
  const document={activeElement:null},focuses=[],scrolls=[];
  const buttons=shownIndices(slides).map(index=>{
    const attributes=new Map(),classes=new Set();
    return {
      dataset:{index:String(index)},attributes,classes,tabIndex:0,
      setAttribute:(name,value)=>attributes.set(name,value),
      classList:{toggle(name,enabled){if(enabled)classes.add(name);else classes.delete(name);}},
      focus(options){document.activeElement=this;focuses.push({index,options});},
      scrollIntoView(options){scrolls.push({index,options});}
    };
  });
  const nav={ownerDocument:document,contains:element=>buttons.includes(element),querySelectorAll:()=>buttons};
  return {nav,document,buttons,focuses,scrolls};
}

test('visible ticks preserve original slide indices and distinguish current, past and future',()=>{
  const run=rail([{hidden:true},{},{hidden:true},{},{}]);
  updateProgressRail(run.nav,3);
  assert.deepEqual(run.buttons.map(button=>button.dataset.index),['1','3','4']);
  assert.deepEqual(run.buttons.map(button=>button.attributes.get('aria-current')),['false','true','false']);
  assert.deepEqual(run.buttons.map(button=>button.classes.has('is-past')),[true,false,false]);
  assert.deepEqual(run.buttons.map(button=>button.tabIndex),[-1,0,-1]);
  assert.deepEqual(run.focuses,[]);
});

test('slide jumps move the focused rail entry while builds keep that focus stable',()=>{
  const run=rail([{}, {}, {}, {}]);
  run.document.activeElement=run.buttons[0];
  updateProgressRail(run.nav,3);
  assert.equal(run.document.activeElement,run.buttons[3]);
  assert.deepEqual(run.focuses,[{index:3,options:{preventScroll:true}}]);
  updateProgressRail(run.nav,3);
  assert.equal(run.focuses.length,1);
  updateProgressRail(run.nav,1);
  assert.equal(run.document.activeElement,run.buttons[1]);
  assert.deepEqual(run.buttons.map(button=>button.tabIndex),[-1,0,-1,-1]);
  assert.deepEqual(run.buttons.map(button=>button.classes.has('is-past')),[true,false,false,false]);
});

test('rail updates never steal focus from presentation controls or notes',()=>{
  const run=rail([{}, {}, {}]),control={};
  run.document.activeElement=control;
  updateProgressRail(run.nav,2);
  assert.equal(run.document.activeElement,control);
  assert.deepEqual(run.focuses,[]);
  assert.deepEqual(run.scrolls,[{index:2,options:{block:'nearest',inline:'nearest'}}]);
});

test('hidden overview slides retain an accessible next visible tick and empty rails are safe',()=>{
  const run=rail([{}, {hidden:true}, {}, {hidden:true}]);
  updateProgressRail(run.nav,1);
  assert.deepEqual(run.buttons.map(button=>button.tabIndex),[-1,0]);
  assert.equal(run.buttons.some(button=>button.attributes.get('aria-current')==='true'),false);
  updateProgressRail(run.nav,3);
  assert.deepEqual(run.buttons.map(button=>button.tabIndex),[-1,0]);
  const empty=rail([{hidden:true}]);
  assert.doesNotThrow(()=>updateProgressRail(empty.nav,0));
  assert.deepEqual(empty.scrolls,[]);
});

test('long decks retain every visible jump target and only one keyboard tab stop',()=>{
  const slides=Array.from({length:501},(_,index)=>({hidden:index%7===0})),run=rail(slides);
  updateProgressRail(run.nav,499);
  assert.equal(run.buttons.length,429);
  assert.equal(run.buttons.filter(button=>button.tabIndex===0).length,1);
  assert.equal(run.buttons.find(button=>button.tabIndex===0).dataset.index,'499');
  assert.equal(run.buttons.at(-1).dataset.index,'500');
});

test('native tick activation stays available to Enter and Space while navigation keys work',()=>{
  const context={active:true,control:true};
  assert.equal(keyAction({key:'Enter'},context),null);
  assert.equal(keyAction({key:' '},context),null);
  assert.equal(keyAction({key:'Tab'},context),null);
  assert.equal(keyAction({key:'ArrowDown'},context),'next');
  assert.equal(keyAction({key:'ArrowUp'},context),'previous');
  assert.equal(keyAction({key:'Home'},context),'first');
  assert.equal(keyAction({key:'End'},context),'last');
});
