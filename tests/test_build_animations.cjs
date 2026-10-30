const {test}=require('node:test');
const assert=require('node:assert/strict');
const {BuildAnimations,counterOptions,countDecimals}=require('../runtime/presentation.js');

/** Model the DOM surface used by builds without coupling the tests to a browser package. */
class Element {
  constructor(attributes={},text='',children=[],tag='div'){
    this.attributes=new Map(Object.entries(attributes));this.textContent=text;this.children=children;this.parentElement=null;this.localName=tag;this.namespaceURI='http://www.w3.org/1999/xhtml';this.listeners=new Map();
    this.childElementCount=children.length;this.ownerDocument={documentElement:{lang:'en'},activeElement:null};
    for(const child of children){child.parentElement=this;child.adopt(this.ownerDocument);}
    const classes=new Set(),styles=new Map();
    this.classList={add:name=>classes.add(name),remove:name=>classes.delete(name),contains:name=>classes.has(name),toggle(name,on){if(on)classes.add(name);else classes.delete(name);}};
    this.style={getPropertyValue:name=>styles.get(name)?.value||'',getPropertyPriority:name=>styles.get(name)?.priority||'',setProperty:(name,value,priority='')=>styles.set(name,{value,priority}),removeProperty:name=>styles.delete(name)};
  }
  hasAttribute(name){return this.attributes.has(name);}
  getAttribute(name){return this.attributes.get(name)??null;}
  setAttribute(name,value){this.attributes.set(name,String(value));}
  removeAttribute(name){this.attributes.delete(name);}
  adopt(document){this.ownerDocument=document;for(const child of this.children)child.adopt(document);}
  matches(selector){
    const tag=selector.match(/^[a-z]+/i)?.[0];
    return (!tag||this.localName===tag)&&[...selector.matchAll(/\[([^\]=]+)(?:="([^"]*)")?\]/g)].every(([,name,value])=>value===undefined?this.hasAttribute(name):this.getAttribute(name)===value)&&[...selector.matchAll(/\.([\w-]+)/g)].every(([,name])=>this.classList.contains(name));
  }
  querySelectorAll(selector){return this.children.flatMap(child=>[...(selector.split(',').some(part=>child.matches(part))?[child]:[]),...child.querySelectorAll(selector)]);}
  closest(selector){for(let part=this;part;part=part.parentElement)if(part.matches(selector))return part;return null;}
  contains(element){return element===this||this.children.some(child=>child.contains(element));}
  addEventListener(name,listener){if(!this.listeners.has(name))this.listeners.set(name,new Set());this.listeners.get(name).add(listener);}
  removeEventListener(name,listener){this.listeners.get(name)?.delete(listener);}
  focus(){this.ownerDocument.activeElement=this;for(let part=this;part;part=part.parentElement)for(const listener of part.listeners.get('focusin')||[])listener({target:this});}
  blur(){if(this.ownerDocument.activeElement===this)this.ownerDocument.activeElement=null;}
}

/** Advance one deterministic frame and expose pending work for cancellation assertions. */
function timeline(slide,options={}){
  let now=0,next=0;const frames=new Map();
  const animation=new BuildAnimations(slide,{now:()=>now,requestFrame:callback=>{frames.set(++next,callback);return next;},cancelFrame:id=>frames.delete(id),computedStyle:element=>({clipPath:element.computedClip||'none'}),...options});
  return {animation,frames,advance(milliseconds){now+=milliseconds;const queued=[...frames.values()];frames.clear();for(const callback of queued)callback(now);}};
}

test('initial hidden builds are committed without transitions and authored transitions return for reveals',()=>{
  const hidden=new Element({'data-build':'1'}),teaser=new Element({'data-build':'2','data-reveal':'blur'}),slide=new Element({},'',[hidden,teaser]);
  hidden.style.setProperty('transition-property','opacity','important');hidden.style.setProperty('transition-duration','1s');
  let commits=0;
  Object.defineProperty(slide,'offsetWidth',{get(){
    commits++;
    for(const element of [hidden,teaser]){assert.equal(element.style.getPropertyValue('transition-property'),'none');assert.equal(element.style.getPropertyPriority('transition-property'),'important');assert.equal(element.classList.contains('unrevealed'),true);}
    assert.equal(teaser.style.getPropertyValue('filter'),'blur(var(--build-blur,10px))');
    return 100;
  }});
  const {animation}=timeline(slide);animation.update(0);
  assert.equal(commits,1);assert.equal(hidden.style.getPropertyValue('transition-property'),'opacity');assert.equal(hidden.style.getPropertyPriority('transition-property'),'important');assert.equal(hidden.style.getPropertyValue('transition-duration'),'1s');assert.equal(teaser.style.getPropertyValue('transition-property'),'');
  animation.update(1);assert.equal(commits,1);assert.equal(hidden.classList.contains('unrevealed'),false);assert.equal(teaser.classList.contains('unrevealed'),true);
});

test('entering live builds from a static preview commits hidden state without transitions',()=>{
  const future=new Element({'data-build':'1'}),slide=new Element({},'',[future]);let commits=0;
  Object.defineProperty(slide,'offsetWidth',{get(){commits++;assert.equal(future.classList.contains('unrevealed'),true);assert.equal(future.style.getPropertyValue('transition-property'),'none');return 100;}});
  const {animation}=timeline(slide);animation.update(0,{build:false});
  assert.equal(commits,0);assert.equal(future.classList.contains('unrevealed'),false);
  animation.update(0);assert.equal(commits,1);assert.equal(future.style.getPropertyValue('transition-property'),'');
  animation.update(1);assert.equal(commits,1);assert.equal(future.classList.contains('unrevealed'),false);
});

test('counters group intermediate values, support signed fractions and preserve final copy',()=>{
  const amount=new Element({'data-count-to':'-9568.25','data-count-from':'-100','data-count-prefix':'€','data-count-suffix':' net'},'−€9,568.25 final');
  const counter=counterOptions(amount);
  assert.equal(counter.from,-100);assert.equal(counter.target,-9568.25);
  assert.equal(counter.format(-1234.5),'€-1,234.50 net');
  assert.equal(counter.final,'−€9,568.25 final');
  assert.equal(counterOptions(amount,'de-DE').format(-1234.5),'€-1.234,50 net');
  assert.equal(counterOptions(amount,'invalid_locale').format(1234),'€1,234.00 net');
  assert.equal(countDecimals('1.25e-3'),5);assert.equal(countDecimals('1.25e2'),0);
  assert.equal(counterOptions(new Element({'data-count-to':'Infinity'})),null);
  assert.equal(counterOptions(new Element({'data-count-to':'4'},'', [new Element()])),null);
});

test('HTML bars and counters share inherited timing without changing authored transforms',()=>{
  const bar=new Element({'data-animate-grow':'x'}),number=new Element({'data-count-to':'9568','data-count-prefix':'$'},'$9,568¹');
  bar.style.setProperty('transform','translateX(30px) rotate(12deg)');
  const group=new Element({'data-build':'1','data-animate-duration':'1000'},'',[bar,number]),slide=new Element({},'',[group]);
  const {animation,advance,frames}=timeline(slide);animation.update(0);
  assert.equal(group.classList.contains('unrevealed'),true);assert.equal(number.textContent,'$9,568¹');assert.equal(frames.size,0);
  animation.update(1);assert.equal(number.textContent,'$0');assert.equal(bar.style.getPropertyValue('clip-path'),'inset(0 100% 0 0)');
  advance(500);assert.equal(number.textContent,'$4,784');assert.equal(bar.style.getPropertyValue('clip-path'),'inset(0 50% 0 0)');
  assert.equal(bar.style.getPropertyValue('transform'),'translateX(30px) rotate(12deg)');
  animation.update(1);advance(500);
  assert.equal(number.textContent,'$9,568¹');assert.equal(bar.style.getPropertyValue('clip-path'),'');assert.equal(frames.size,0);
});

test('SVG growth explicitly clips to the element fill box and retains SVG transforms',()=>{
  const bar=new Element({'data-build':'1','data-animate-grow':'x','transform':'translate(200 50) rotate(15)'});bar.namespaceURI='http://www.w3.org/2000/svg';
  const {animation,advance}=timeline(new Element({},'',[bar]));animation.update(1);advance(400);
  assert.equal(bar.style.getPropertyValue('clip-path'),'inset(0 50% 0 0) fill-box');
  assert.equal(bar.getAttribute('transform'),'translate(200 50) rotate(15)');
  advance(400);assert.equal(bar.style.getPropertyValue('clip-path'),'');
});

test('authored inline, SVG and stylesheet clipping remains intact',()=>{
  const inline=new Element({'data-build':'1','data-animate-grow':'x'});inline.style.setProperty('clip-path','circle(40%)','important');
  const svg=new Element({'data-build':'1','data-animate-grow':'x','clip-path':'url(#mask)'});
  const themed=new Element({'data-build':'1','data-animate-grow':'x','clip-path':'none'});themed.computedClip='inset(5px)';
  const {animation,advance}=timeline(new Element({},'',[inline,svg,themed]));animation.update(1);advance(400);
  assert.equal(inline.style.getPropertyValue('clip-path'),'circle(40%)');assert.equal(inline.style.getPropertyPriority('clip-path'),'important');
  assert.equal(svg.getAttribute('clip-path'),'url(#mask)');assert.equal(svg.style.getPropertyValue('clip-path'),'');assert.equal(themed.style.getPropertyValue('clip-path'),'');
});

test('a delayed result appears when its synchronized bar finishes',()=>{
  const bar=new Element({'data-build':'1','data-animate-grow':'x','data-animate-duration':'800'});
  const result=new Element({'data-build':'1','data-animate-delay':'800'},'Result');
  const {animation,advance,frames}=timeline(new Element({},'',[bar,result]));animation.update(1);advance(799);
  assert.equal(result.classList.contains('unrevealed'),true);assert.notEqual(bar.style.getPropertyValue('clip-path'),'');
  advance(1);assert.equal(result.classList.contains('unrevealed'),false);assert.equal(bar.style.getPropertyValue('clip-path'),'');assert.equal(frames.size,0);
});

test('delayed inner animations wait at zero while blur teasers keep final content until revealed',()=>{
  const bar=new Element({'data-animate-grow':'x','data-animate-delay':'200'});
  const teaser=new Element({'data-build':'2','data-reveal':'blur','data-count-to':'42','data-animate-delay':'200'},'42 final');
  const {animation,advance}=timeline(new Element({},'',[new Element({'data-build':'1'},'',[bar]),teaser]));
  animation.update(1);assert.equal(bar.style.getPropertyValue('clip-path'),'inset(0 100% 0 0)');assert.equal(teaser.classList.contains('unrevealed'),true);assert.equal(teaser.textContent,'42 final');
  animation.update(2);advance(199);assert.equal(teaser.classList.contains('unrevealed'),true);assert.equal(teaser.textContent,'42 final');
  advance(1);assert.equal(teaser.classList.contains('unrevealed'),false);assert.equal(teaser.textContent,'0');
});

test('reversing cancels hidden builds and re-entry replays without restarting retained builds',()=>{
  const count=new Element({'data-build':'1','data-count-to':'-2.50','data-animate-duration':'1000'},'−2.50');
  const {animation,advance,frames}=timeline(new Element({},'',[count]));animation.update(1);advance(250);assert.equal(count.textContent,'-0.63');
  animation.update(1);advance(250);assert.equal(count.textContent,'-1.25');
  animation.update(0);assert.equal(frames.size,0);assert.equal(count.textContent,'−2.50');advance(1000);assert.equal(count.textContent,'−2.50');
  animation.update(1);assert.equal(count.textContent,'0.00');advance(500);assert.equal(count.textContent,'-1.25');advance(500);assert.equal(count.textContent,'−2.50');
});

test('a later outer build keeps nested counters dormant',()=>{
  const count=new Element({'data-build':'1','data-count-to':'10'},'10'),outer=new Element({'data-build':'3'},'',[count]);
  const {animation,frames}=timeline(new Element({},'',[outer]));animation.update(1);
  assert.equal(count.classList.contains('unrevealed'),true);assert.equal(count.textContent,'10');assert.equal(frames.size,0);
  animation.update(3);assert.equal(count.textContent,'0');
});

test('all-visible mode restores exact final text and returning to builds replays',()=>{
  const count=new Element({'data-build':'2','data-count-to':'1234.5','data-count-decimals':'1','data-animate-delay':'99999'},'1,234.5 exact');
  const {animation,frames}=timeline(new Element({},'',[count]));animation.update(2);assert.equal(count.classList.contains('unrevealed'),true);
  animation.update(0,{build:false});assert.equal(count.textContent,'1,234.5 exact');assert.equal(count.classList.contains('unrevealed'),false);assert.equal(frames.size,0);
  animation.update(0);assert.equal(count.classList.contains('unrevealed'),true);
});

test('reduced motion skips delays and motion while retaining future reveal boundaries',()=>{
  const count=new Element({'data-build':'1','data-count-to':'42','data-animate-delay':'99999'},'42'),later=new Element({'data-build':'2'},'Later');
  const {animation,frames}=timeline(new Element({},'',[count,later]),{reducedMotion:()=>true});animation.update(1);
  assert.equal(count.textContent,'42');assert.equal(count.classList.contains('unrevealed'),false);assert.equal(later.classList.contains('unrevealed'),true);assert.equal(frames.size,0);
});

test('printing shows final values and resumes at the same point after the dialog',()=>{
  const count=new Element({'data-build':'1','data-count-to':'100','data-animate-duration':'1000'},'100 final');
  const {animation,advance,frames}=timeline(new Element({},'',[count]));animation.update(1);advance(250);assert.equal(count.textContent,'25');
  animation.beforePrint();assert.equal(count.textContent,'100 final');assert.equal(frames.size,0);advance(10000);
  animation.afterPrint();assert.equal(count.textContent,'25');advance(250);assert.equal(count.textContent,'50');
  animation.dispose();assert.equal(count.textContent,'100 final');assert.equal(frames.size,0);assert.equal(count.classList.contains('unrevealed'),false);
  advance(10000);animation.update(1);assert.equal(count.textContent,'100 final');assert.equal(frames.size,0);
});

test('blur and nested builds stay inaccessible until visible and restore authored ARIA in final views',()=>{
  const count=new Element({'data-count-to':'42'},'42');
  const inner=new Element({'data-build':'1','aria-hidden':'false'},'',[count]);
  const outer=new Element({'data-build':'2','data-reveal':'blur','data-animate-delay':'200'},'',[inner]);
  const authoredHidden=new Element({'data-build':'3','aria-hidden':'true'},'Decorative');
  const authoredEmpty=new Element({'data-build':'1','aria-hidden':''},'Empty attribute');
  const {animation,advance}=timeline(new Element({},'',[outer,authoredHidden,authoredEmpty]));
  animation.update(1);
  for(const part of [outer,inner,count,authoredHidden])assert.equal(part.getAttribute('aria-hidden'),'true');
  assert.equal(authoredEmpty.getAttribute('aria-hidden'),'');
  animation.update(2);advance(199);
  for(const part of [outer,inner,count])assert.equal(part.getAttribute('aria-hidden'),'true');
  advance(1);
  assert.equal(outer.hasAttribute('aria-hidden'),false);assert.equal(inner.getAttribute('aria-hidden'),'false');assert.equal(count.hasAttribute('aria-hidden'),false);
  animation.update(1);
  for(const part of [outer,inner,count])assert.equal(part.getAttribute('aria-hidden'),'true');
  animation.beforePrint();
  assert.equal(outer.hasAttribute('aria-hidden'),false);assert.equal(inner.getAttribute('aria-hidden'),'false');assert.equal(count.hasAttribute('aria-hidden'),false);assert.equal(authoredHidden.getAttribute('aria-hidden'),'true');assert.equal(authoredEmpty.getAttribute('aria-hidden'),'');
  animation.afterPrint();
  for(const part of [outer,inner,count])assert.equal(part.getAttribute('aria-hidden'),'true');
  animation.update(0,{build:false});
  assert.equal(outer.hasAttribute('aria-hidden'),false);assert.equal(inner.getAttribute('aria-hidden'),'false');assert.equal(count.hasAttribute('aria-hidden'),false);assert.equal(authoredHidden.getAttribute('aria-hidden'),'true');
  animation.update(0);assert.equal(inner.getAttribute('aria-hidden'),'true');
  animation.dispose();
  assert.equal(outer.hasAttribute('aria-hidden'),false);assert.equal(inner.getAttribute('aria-hidden'),'false');assert.equal(count.hasAttribute('aria-hidden'),false);assert.equal(authoredHidden.getAttribute('aria-hidden'),'true');assert.equal(authoredEmpty.getAttribute('aria-hidden'),'');
});

test('child delays cannot finish behind a delayed enclosing build and inherited delays are not added',()=>{
  const bar=new Element({'data-animate-grow':'x','data-animate-delay':'0'});
  const explicit=new Element({'data-count-to':'80','data-animate-delay':'0'},'80 exact');
  const inherited=new Element({'data-count-to':'80'},'80 inherited');
  const nestedCount=new Element({'data-count-to':'80','data-animate-delay':'0'},'80 nested');
  const nested=new Element({'data-build':'1','data-animate-delay':'200'},'',[nestedCount]);
  const outer=new Element({'data-build':'1','data-animate-delay':'1000','data-animate-duration':'800'},'',[bar,explicit,inherited,nested]);
  const {animation,advance,frames}=timeline(new Element({},'',[outer]));animation.update(1);advance(800);
  assert.equal(outer.classList.contains('unrevealed'),true);assert.equal(explicit.textContent,'0');assert.equal(inherited.textContent,'0');assert.equal(nestedCount.textContent,'0');
  advance(200);assert.equal(outer.classList.contains('unrevealed'),false);assert.equal(nested.classList.contains('unrevealed'),false);assert.equal(bar.style.getPropertyValue('clip-path'),'inset(0 100% 0 0)');
  advance(400);assert.equal(explicit.textContent,'40');assert.equal(inherited.textContent,'40');assert.equal(nestedCount.textContent,'40');
  advance(400);assert.equal(explicit.textContent,'80 exact');assert.equal(inherited.textContent,'80 inherited');assert.equal(nestedCount.textContent,'80 nested');assert.equal(frames.size,0);
  animation.update(0);animation.update(1);advance(800);assert.equal(explicit.textContent,'0');
  animation.beforePrint();assert.equal(explicit.textContent,'80 exact');animation.afterPrint();assert.equal(explicit.textContent,'0');
  animation.update(0,{build:false});assert.equal(explicit.textContent,'80 exact');assert.equal(frames.size,0);
});

test('hidden HTML and SVG controls lose keyboard and programmatic focus with exact restoration',()=>{
  const link=new Element({'href':'#answer','tabindex':'3'},'Link',[],'a');
  const button=new Element({},'Button',[],'button');
  const emptyTab=new Element({'tabindex':''},'Empty tab stop',[],'span');
  const svgLink=new Element({'xlink:href':'#answer','tabindex':'2','focusable':'true'},'SVG link',[],'a');svgLink.namespaceURI='http://www.w3.org/2000/svg';
  const svgShape=new Element({'focusable':'true'},'',[],'rect');svgShape.namespaceURI='http://www.w3.org/2000/svg';svgShape.blur=undefined;
  const authoredInert=new Element({'data-build':'2','inert':'authored'},'Authored inert');
  const group=new Element({'data-build':'1','data-reveal':'blur','data-animate-delay':'200'},'',[link,button,emptyTab,svgLink,svgShape]);
  const slide=new Element({},'',[group,authoredInert]),host=new Element({'tabindex':'7'},'',[slide]);
  const controls=[link,button,emptyTab,svgLink,svgShape],state=()=>controls.map(element=>[element.getAttribute('tabindex'),element.getAttribute('focusable')]);
  const authored=state(),{animation,advance}=timeline(slide);svgLink.focus();animation.update(0);
  assert.equal(slide.ownerDocument.activeElement,null);assert.equal(group.getAttribute('inert'),'');
  for(const element of controls)assert.equal(element.getAttribute('tabindex'),'-1');
  assert.equal(svgLink.getAttribute('focusable'),'false');assert.equal(svgShape.getAttribute('focusable'),'false');
  svgLink.focus();assert.equal(slide.ownerDocument.activeElement,null);
  svgShape.focus();assert.equal(slide.ownerDocument.activeElement,host);assert.equal(host.getAttribute('tabindex'),'7');
  animation.beforePrint();assert.deepEqual(state(),authored);assert.equal(group.hasAttribute('inert'),false);assert.equal(authoredInert.getAttribute('inert'),'authored');
  svgLink.focus();assert.equal(slide.ownerDocument.activeElement,svgLink);
  animation.afterPrint();assert.equal(slide.ownerDocument.activeElement,null);assert.equal(link.getAttribute('tabindex'),'-1');
  animation.update(1);advance(199);button.focus();assert.equal(slide.ownerDocument.activeElement,null);
  advance(1);assert.deepEqual(state(),authored);assert.equal(group.hasAttribute('inert'),false);assert.equal(authoredInert.getAttribute('inert'),'');
  link.focus();assert.equal(slide.ownerDocument.activeElement,link);
  animation.update(0);assert.equal(slide.ownerDocument.activeElement,null);assert.equal(link.getAttribute('tabindex'),'-1');
  animation.update(0,{build:false});assert.deepEqual(state(),authored);assert.equal(group.hasAttribute('inert'),false);assert.equal(authoredInert.getAttribute('inert'),'authored');
  animation.update(0);animation.dispose();assert.deepEqual(state(),authored);assert.equal(group.hasAttribute('inert'),false);assert.equal(authoredInert.getAttribute('inert'),'authored');assert.equal(slide.listeners.get('focusin').size,0);
});

test('hidden blur overrides inline filter priority and restores it for reveal, print and static views',()=>{
  const plain=new Element({'data-build':'1','data-reveal':'blur'});
  const important=new Element({'data-build':'1','data-reveal':'blur'});important.style.setProperty('filter','none','important');
  const styled=new Element({'data-build':'1','data-reveal':'blur'});styled.style.setProperty('filter','grayscale(.5)','important');
  const teasers=[plain,important,styled],filters=()=>teasers.map(element=>[element.style.getPropertyValue('filter'),element.style.getPropertyPriority('filter')]);
  const authored=filters(),{animation}=timeline(new Element({},'',[...teasers]));animation.update(0);
  assert.deepEqual(filters(),teasers.map(()=>['blur(var(--build-blur,10px))','important']));
  animation.update(1);assert.deepEqual(filters(),authored);
  animation.update(0);animation.beforePrint();assert.deepEqual(filters(),authored);
  animation.afterPrint();assert.equal(important.style.getPropertyValue('filter'),'blur(var(--build-blur,10px))');
  animation.update(0,{build:false});assert.deepEqual(filters(),authored);
  animation.update(0);animation.dispose();assert.deepEqual(filters(),authored);
});

test('later builds wait only for remaining parent delays and replay with their own declared timing',()=>{
  const explicit=new Element({'data-build':'2','data-count-to':'80','data-animate-delay':'0'},'80 exact');
  const inherited=new Element({'data-build':'2','data-count-to':'80'},'80 inherited');
  const outer=new Element({'data-build':'1','data-animate-delay':'1000','data-animate-duration':'800'},'',[explicit,inherited]);
  const {animation,advance}=timeline(new Element({},'',[outer]));animation.update(1);advance(1500);animation.update(2);
  assert.equal(outer.classList.contains('unrevealed'),false);assert.equal(explicit.classList.contains('unrevealed'),false);assert.equal(explicit.textContent,'0');assert.equal(inherited.classList.contains('unrevealed'),true);
  advance(400);assert.equal(explicit.textContent,'40');assert.equal(inherited.textContent,'0');
  animation.update(1);assert.equal(explicit.textContent,'80 exact');animation.update(2);
  assert.equal(explicit.classList.contains('unrevealed'),false);assert.equal(explicit.textContent,'0');advance(400);assert.equal(explicit.textContent,'40');
  animation.update(0);animation.update(1);advance(300);animation.update(2);
  assert.equal(explicit.classList.contains('unrevealed'),true);advance(699);assert.equal(explicit.textContent,'0');assert.equal(explicit.classList.contains('unrevealed'),true);
  advance(1);assert.equal(outer.classList.contains('unrevealed'),false);assert.equal(explicit.classList.contains('unrevealed'),false);assert.equal(explicit.textContent,'0');assert.equal(inherited.classList.contains('unrevealed'),true);
  advance(400);assert.equal(explicit.textContent,'40');assert.equal(inherited.textContent,'10');
});

test('printing removes reveal state so authored filters remain intact and restores the staged view',()=>{
  const count=new Element({'data-build':'1','data-count-to':'100','data-animate-duration':'1000'},'100 exact');
  const stylesheetFilter=new Element({'data-build':'2','data-reveal':'blur'},'Stylesheet filter');stylesheetFilter.classList.add('brand-filter');
  const inlineFilter=new Element({'data-build':'2','data-reveal':'blur'},'Inline filter');inlineFilter.style.setProperty('filter','drop-shadow(2px 3px 4px #0008)');inlineFilter.style.setProperty('opacity','.65');
  const slide=new Element({},'',[count,stylesheetFilter,inlineFilter]),{animation,advance}=timeline(slide);animation.update(1);advance(250);
  assert.equal(slide.classList.contains('build-mode'),true);assert.equal(inlineFilter.classList.contains('unrevealed'),true);
  animation.beforePrint();
  assert.equal(slide.classList.contains('build-mode'),false);assert.equal(count.textContent,'100 exact');
  for(const part of [count,stylesheetFilter,inlineFilter])assert.equal(part.classList.contains('unrevealed'),false);
  assert.equal(stylesheetFilter.style.getPropertyValue('filter'),'');assert.equal(stylesheetFilter.classList.contains('brand-filter'),true);
  assert.equal(inlineFilter.style.getPropertyValue('filter'),'drop-shadow(2px 3px 4px #0008)');assert.equal(inlineFilter.style.getPropertyPriority('filter'),'');assert.equal(inlineFilter.style.getPropertyValue('opacity'),'.65');
  advance(10000);animation.afterPrint();
  assert.equal(slide.classList.contains('build-mode'),true);assert.equal(count.textContent,'25');assert.equal(count.classList.contains('unrevealed'),false);
  for(const part of [stylesheetFilter,inlineFilter]){assert.equal(part.classList.contains('unrevealed'),true);assert.equal(part.style.getPropertyValue('filter'),'blur(var(--build-blur,10px))');}
  assert.equal(inlineFilter.style.getPropertyValue('opacity'),'.65');advance(250);assert.equal(count.textContent,'50');
});
