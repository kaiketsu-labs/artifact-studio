const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createPreviews}=require('../web/favorites.js');

function cover(id,top=0){
  return {dataset:{favoritePreview:`/api/decks/example/slides/${id}/preview?v=1`,previewTitle:id},top,frames:[],
    getBoundingClientRect(){return {top:this.top,bottom:this.top+200,width:340,height:200};},
    prepend(frame){this.frames.unshift(frame);frame.cover=this;}};
}
function fixture(){
  const frames=[],intersections=[],resizes=[],timers=new Map(),listeners=new Map();let timerId=0;
  const document={hidden:false,addEventListener(name,callback){listeners.set(name,callback);},removeEventListener:name=>listeners.delete(name),
    createElement(tag){assert.equal(tag,'iframe');const frame={style:{},attributes:{},setAttribute(name,value){this.attributes[name]=value;},remove(){this.removed=true;this.cover.frames=this.cover.frames.filter(item=>item!==this);}};frames.push(frame);return frame;}};
  const view={document,setTimeout(callback){const id=++timerId;timers.set(id,callback);return id;},clearTimeout:id=>timers.delete(id),
    addEventListener(name,callback){listeners.set(name,callback);},removeEventListener:name=>listeners.delete(name),
    IntersectionObserver:class{constructor(callback,options){this.callback=callback;this.options=options;this.targets=[];intersections.push(this);}observe(target){this.targets.push(target);}disconnect(){this.targets=[];}},
    ResizeObserver:class{constructor(callback){this.callback=callback;this.targets=[];resizes.push(this);}observe(target){this.targets.push(target);}disconnect(){this.targets=[];}}};
  const controller=createPreviews({view,document}),container={open:true,items:[],getBoundingClientRect:()=>({top:0,bottom:600}),querySelectorAll(){return this.items;}};
  const mount=items=>{container.items=items;controller.mount(container);};
  const intersect=(item,visible)=>{item.top=visible?0:2000;intersections.at(-1).callback([{target:item,isIntersecting:visible}]);};
  return {controller,container,mount,intersect,frames,intersections,resizes,timers,document,listeners};
}

test('favorite documents load two at a time and only around the visible picker',()=>{
  const f=fixture(),first=cover('first'),second=cover('second',220),third=cover('third',440),near=cover('near',670),far=cover('far',1000);
  f.mount([near,first,second,third,far]);
  assert.deepEqual(f.frames.map(frame=>frame.title),['first','second']);
  assert.equal(f.intersections[0].options.root,f.container);assert.equal(f.intersections[0].options.rootMargin,'80px 0px');
  f.frames[0].onload();assert.equal(f.frames[2].title,'third');f.frames[1].onload();assert.equal(f.frames[3].title,'near');
  assert.equal(far.frames.length,0);assert.equal(first.dataset.previewState,'ready');
  assert.equal(first.frames[0].attributes.sandbox,'');assert.equal(first.frames[0].style.transform,'scale(0.265625)');
  f.controller.destroy();
});

test('filtering retains matching loaded documents and disposes hidden in-flight documents',()=>{
  const f=fixture(),first=cover('first'),second=cover('second',220),third=cover('third',440);
  f.mount([first,second,third]);f.frames[0].onload();const survivor=first.frames[0],discarded=second.frames[0];
  f.mount([first]);assert.equal(first.frames[0],survivor);assert.equal(discarded.removed,true);assert.equal(discarded.onload,null);
  const previous=f.frames.length;f.mount([first]);assert.equal(f.frames.length,previous);
  f.controller.destroy();assert.equal(f.timers.size,0);
});

test('scrolling releases documents outside the viewport while admitting newly visible cards',()=>{
  const f=fixture(),first=cover('first'),second=cover('second',220),far=cover('far',2000);
  f.mount([first,second,far]);f.frames[0].onload();const released=first.frames[0];
  f.intersect(first,false);assert.equal(released.removed,true);assert.equal(first.frames.length,0);
  f.intersect(far,true);assert.equal(far.frames.length,1);assert.equal(far.frames[0].title,'far');
  f.controller.destroy();
});

test('closing releases every frame and observer, and stale completion cannot restart work',()=>{
  const f=fixture(),one=cover('one'),two=cover('two'),three=cover('three');f.mount([one,two,three]);
  const stale=f.frames[0].onload;f.container.open=false;f.controller.clear();stale();
  assert.equal(f.frames.length,2);assert.ok(f.frames.every(frame=>frame.removed));assert.equal(f.timers.size,0);
  assert.ok(f.intersections.every(observer=>observer.targets.length===0));assert.ok(f.resizes.every(observer=>observer.targets.length===0));
  f.container.open=true;f.mount([one]);assert.equal(f.frames.length,3);f.controller.destroy();assert.equal(f.listeners.size,0);
});

test('hidden tabs release previews and restore only current demand on return',()=>{
  const f=fixture(),one=cover('one'),far=cover('far',2000);f.mount([one,far]);
  f.document.hidden=true;f.listeners.get('visibilitychange')();assert.equal(one.frames.length,0);
  f.document.hidden=false;f.listeners.get('visibilitychange')();assert.equal(one.frames.length,1);assert.equal(far.frames.length,0);
  f.controller.destroy();
});

test('a changed version replaces its frame while failed previews do not spin or block other cards',()=>{
  const f=fixture(),one=cover('one'),two=cover('two'),three=cover('three');f.mount([one,two,three]);
  f.frames[0].onerror();assert.equal(one.frames.length,0);assert.equal(one.dataset.previewState,'unavailable');assert.equal(three.frames.length,1);
  const count=f.frames.length;f.mount([one,two,three]);assert.equal(f.frames.length,count);
  one.dataset.favoritePreview='/api/decks/example/slides/one/preview?v=2';f.mount([one,two,three]);
  f.frames[1].onload();assert.equal(one.frames.length,1);assert.match(one.frames[0].src,/v=2$/);
  f.controller.destroy();
});
