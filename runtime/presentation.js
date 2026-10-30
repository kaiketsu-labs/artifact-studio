(() => {
  'use strict';
  /** Count elapsed presentation time independently of pauses and wall-clock changes. */
  class PresentationClock {
    constructor(now=()=>performance.now()){this.now=now;this.accumulated=0;this.since=null;}
    start(){if(this.since===null)this.since=this.now();}
    pause(){if(this.since!==null){this.accumulated+=this.now()-this.since;this.since=null;}}
    reset(){this.accumulated=0;if(this.since!==null)this.since=this.now();}
    elapsed(){return this.accumulated+(this.since===null?0:this.now()-this.since);}
    get running(){return this.since!==null;}
  }
  /** Respect reveal boundaries and never invent a build before the first slide. */
  function nextState(current,revealed,delta,hasBuild,count){
    if(delta>0&&hasBuild&&!revealed)return {current,revealed:true};
    if(delta<0&&hasBuild&&revealed)return {current,revealed:false};
    const next=Math.max(0,Math.min(count-1,current+delta));
    return {current:next,revealed:next===current?revealed:delta<0};
  }
  /** Count numbered reveal groups, retaining unnumbered legacy reveals as step one. */
  function buildCount(html){
    const groups=[...html.matchAll(/\bdata-build(?=\s|=|>)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)];
    return groups.reduce((count,group)=>Math.max(count,Math.min(50,Math.max(1,Math.floor(Number(group[1]||group[2]||group[3]))||1))),0);
  }
  /** Advance one reveal at a time and restore all reveals when navigating back a slide. */
  function nextBuildState(current,revealed,delta,counts,hidden=[]){
    const limit=counts[current]||0,step=Math.max(0,Math.min(limit,Number(revealed)||0));
    if(delta>0&&step<limit)return {current,revealed:step+1};
    if(delta<0&&step>0)return {current,revealed:step-1};
    const candidates=counts.map((_,index)=>index).filter(index=>!hidden[index]);
    const next=(delta>0?candidates.find(index=>index>current):candidates.findLast(index=>index<current))??current;
    return {current:next,revealed:next===current?step:delta<0?counts[next]||0:0};
  }
  /** Keep original slide numbers while filtering the normal slideshow route. */
  function shownIndices(slides){return slides.flatMap((slide,index)=>slide.hidden?[]:[index]);}
  /** Start on the requested visible slide or the next visible slide, with a safe empty result. */
  function startIndex(slides,requested=0){const shown=shownIndices(slides);return shown.find(index=>index>=requested)??shown[0]??-1;}
  /** Format a duration without wrapping after an hour. */
  function duration(milliseconds){const total=Math.floor(Math.max(0,milliseconds)/1000);const seconds=String(total%60).padStart(2,'0');const minutes=String(Math.floor(total/60)%60).padStart(2,'0');return total>=3600?`${Math.floor(total/3600)}:${minutes}:${seconds}`:`${minutes}:${seconds}`;}
  /** Preserve editing, focused controls and system shortcuts while accepting slide-advance keys. */
  function keyAction(event,{active=false,editing=false,control=false,dialog=false}={}){
    if(dialog||event.defaultPrevented||event.isComposing||event.repeat||event.ctrlKey||event.metaKey||event.altKey)return null;
    if(event.key==='Escape')return active?'end':null;
    if(editing||(control&&['Enter',' '].includes(event.key)))return null;
    const actions={ArrowRight:'next',ArrowDown:'next',PageDown:'next',ArrowLeft:'previous',ArrowUp:'previous',PageUp:'previous',Backspace:'previous',Delete:'previous',Home:'first',End:'last'};
    return actions[event.key]||(active&&(event.key==='Enter'||event.key.length===1)?'next':null);
  }
  /** Read a finite, nonnegative animation time, inheriting the nearest explicit value. */
  function animationTime(element,name,fallback,slide){
    for(let part=element;part;part=part.parentElement){
      if(part.hasAttribute(name)){const value=Number(part.getAttribute(name));return Number.isFinite(value)&&value>=0?value:fallback;}
      if(part===slide)break;
    }
    return fallback;
  }
  /** Restore the exact authored value, including an originally absent or empty attribute. */
  function restoreAttribute(element,name,value){if(value===null)element.removeAttribute(name);else element.setAttribute(name,value);}
  /** Respect every enclosing reveal, including nested builds with later outer steps. */
  function animationStep(element,slide){
    let step=0;
    for(let part=element;part;part=part.parentElement){
      if(part.hasAttribute('data-build'))step=Math.max(step,Math.min(50,Math.max(1,Math.floor(Number(part.getAttribute('data-build')))||1)));
      if(part===slide)break;
    }
    return step;
  }
  /** Infer decimal precision without losing fractional targets written in exponent notation. */
  function countDecimals(value){
    const [mantissa,exponent='0']=String(value).toLowerCase().split('e');
    return Math.max(0,Math.min(20,(mantissa.split('.')[1]?.length||0)-(Number(exponent)||0)));
  }
  /** Keep counters text-only and restore the author's exact final typography. */
  function counterOptions(element,locale='en'){
    const raw=element.getAttribute('data-count-to'),target=Number(raw);
    if(raw===null||raw.trim()===''||!Number.isFinite(target)||element.childElementCount)return null;
    const rawFrom=element.getAttribute('data-count-from'),from=rawFrom===null?0:Number(rawFrom);
    const rawDecimals=element.getAttribute('data-count-decimals'),decimals=rawDecimals===null?countDecimals(raw):Math.max(0,Math.min(20,Math.floor(Number(rawDecimals))||0));
    const options={minimumFractionDigits:decimals,maximumFractionDigits:decimals};
    const prefix=element.getAttribute('data-count-prefix')||'',suffix=element.getAttribute('data-count-suffix')||'';
    let formatter;try{formatter=new Intl.NumberFormat(locale||'en',options);}catch{formatter=new Intl.NumberFormat('en',options);}
    return {target,from:Number.isFinite(from)?from:0,final:element.textContent,format:value=>`${prefix}${formatter.format(Object.is(value,-0)?0:value)}${suffix}`};
  }
  /** Reveal each bar in its own geometry box without changing author transforms or layout. */
  function growthClip(progress,svg=false){return `inset(0 ${(1-Math.max(0,Math.min(1,progress)))*100}% 0 0)${svg?' fill-box':''}`;}
  /** Retain one cancellable animation loop per slide and replay only newly activated builds. */
  class BuildAnimations {
    constructor(slide,{now=()=>performance.now(),requestFrame=callback=>requestAnimationFrame(callback),cancelFrame=id=>cancelAnimationFrame(id),reducedMotion=()=>false,computedStyle=element=>element.ownerDocument?.defaultView?.getComputedStyle(element),locale=slide.ownerDocument?.documentElement?.lang||'en'}={}){
      this.slide=slide;this.now=now;this.requestFrame=requestFrame;this.cancelFrame=cancelFrame;this.reducedMotion=reducedMotion;this.frame=null;this.build=true;this.disposed=false;this.printing=null;
      this.parts=[...slide.querySelectorAll('[data-build],[data-animate-grow="x"],[data-count-to]')].map(element=>{
        const clip=element.style.getPropertyValue('clip-path'),priority=element.style.getPropertyPriority('clip-path');
        const authoredClip=[clip,element.getAttribute('clip-path'),computedStyle(element)?.clipPath].some(value=>value&&value!=='none');
        const grow=element.getAttribute('data-animate-grow')==='x'&&!authoredClip;
        const counter=counterOptions(element,locale);
        return {element,step:animationStep(element,slide),build:element.hasAttribute('data-build'),ariaHidden:element.getAttribute('aria-hidden'),inert:element.getAttribute('inert'),blur:element.getAttribute('data-reveal')==='blur',filter:element.style.getPropertyValue('filter'),filterPriority:element.style.getPropertyPriority('filter'),grow,counter,clip,priority,svg:element.namespaceURI==='http://www.w3.org/2000/svg',delay:animationTime(element,'data-animate-delay',0,slide),duration:grow||counter?animationTime(element,'data-animate-duration',800,slide):0,active:false,started:0,done:false};
      });
      this.buildParts=new Map(this.parts.filter(part=>part.build).map(part=>[part.element,part]));
      this.focusParts=[...slide.querySelectorAll('a,area,button,input,select,textarea,iframe,object,embed,audio[controls],video[controls],summary,[tabindex],[contenteditable],[focusable]')].filter(element=>element.closest('[data-build]')).map(element=>({element,tabindex:element.getAttribute('tabindex'),focusable:element.getAttribute('focusable'),svg:element.namespaceURI==='http://www.w3.org/2000/svg'}));
      this.focusGuard=event=>{if(this.build&&this.printing===null&&event.target?.closest?.('[data-build].unrevealed'))this.releaseFocus(event.target);};
      slide.addEventListener('focusin',this.focusGuard,true);
    }
    /** Restore author styles instead of leaving an inline clipping override in final views. */
    restore(part){
      if(part.grow){if(part.clip)part.element.style.setProperty('clip-path',part.clip,part.priority);else part.element.style.removeProperty('clip-path');}
      if(part.counter)part.element.textContent=part.counter.final;
    }
    /** Keep future content inaccessible and restore each exact authored accessibility value. */
    accessibility(final=false){
      const live=!final&&this.build;
      const focused=this.slide.ownerDocument?.activeElement;
      if(live&&focused&&this.slide.contains(focused)&&focused.closest('[data-build].unrevealed'))this.releaseFocus(focused);
      for(const part of this.parts){
        const hidden=live&&!!part.element.closest('[data-build].unrevealed');
        if(hidden)part.element.setAttribute('aria-hidden','true');else restoreAttribute(part.element,'aria-hidden',part.ariaHidden);
        if(part.build){if(hidden)part.element.setAttribute('inert','');else restoreAttribute(part.element,'inert',part.inert);}
        if(part.blur){
          if(hidden)part.element.style.setProperty('filter','blur(var(--build-blur,10px))','important');
          else if(part.filter)part.element.style.setProperty('filter',part.filter,part.filterPriority);
          else part.element.style.removeProperty('filter');
        }
      }
      for(const part of this.focusParts){
        const hidden=live&&!!part.element.closest('[data-build].unrevealed');
        if(hidden){part.element.setAttribute('tabindex','-1');if(part.svg)part.element.setAttribute('focusable','false');}
        else{restoreAttribute(part.element,'tabindex',part.tabindex);if(part.svg)restoreAttribute(part.element,'focusable',part.focusable);}
      }
    }
    /** Remove existing hidden focus, including SVG focus when an inert implementation is HTML-only. */
    releaseFocus(element){
      element.blur?.();
      if(this.slide.ownerDocument?.activeElement!==element)return;
      const host=this.slide.parentElement;if(!host)return;
      const tabindex=host.getAttribute('tabindex');host.setAttribute('tabindex','-1');host.focus?.({preventScroll:true});restoreAttribute(host,'tabindex',tabindex);
    }
    /** Draw an intermediate value, keeping layout and author transforms untouched. */
    paint(part,progress){
      if(part.grow)part.element.style.setProperty('clip-path',growthClip(progress,part.svg),'important');
      if(part.counter)part.element.textContent=part.counter.format(part.counter.from*(1-progress)+part.counter.target*progress);
    }
    /** Wait only for enclosing builds' remaining delays, measured from their own activation. */
    deadline(part){
      let deadline=part.started+part.delay;
      for(let element=part.element.parentElement;element;element=element.parentElement){
        const ancestor=this.buildParts.get(element);
        if(ancestor?.active)deadline=Math.max(deadline,ancestor.started+ancestor.delay);
        if(element===this.slide)break;
      }
      return deadline;
    }
    /** Cancel stale work before replacing or leaving a retained slide. */
    cancel(){if(this.frame!==null){this.cancelFrame(this.frame);this.frame=null;}}
    /** Dispose to a complete static state suitable for previews and printing. */
    dispose(){this.cancel();this.disposed=true;this.slide.removeEventListener('focusin',this.focusGuard,true);for(const part of this.parts){this.restore(part);part.element.classList.remove('unrevealed');}this.slide.classList.remove('build-mode');this.accessibility(true);}
    /** Print final values while preserving the live reveal and elapsed animation state. */
    beforePrint(){
      if(this.disposed||this.printing!==null)return;
      this.printing={started:this.now(),buildMode:this.slide.classList.contains('build-mode'),hidden:[...this.buildParts.values()].map(part=>[part,part.element.classList.contains('unrevealed')])};
      this.cancel();this.slide.classList.remove('build-mode');
      for(const part of this.parts){this.restore(part);if(part.build)part.element.classList.remove('unrevealed');}
      this.accessibility(true);
    }
    /** Resume in-flight animation after the print dialog without counting time spent there. */
    afterPrint(){
      if(this.disposed||this.printing===null)return;
      const snapshot=this.printing,now=this.now(),paused=now-snapshot.started;this.printing=null;
      this.slide.classList.toggle('build-mode',snapshot.buildMode);
      for(const [part,hidden] of snapshot.hidden)part.element.classList.toggle('unrevealed',hidden);
      for(const part of this.parts)if(part.active&&!part.done)part.started+=paused;
      this.draw(now);
    }
    /** Advance eligibility without restarting animations that are already running or complete. */
    update(revealed,{build=true}={}){
      if(this.disposed)return;
      const changedMode=this.build!==build;this.build=build;this.slide.classList.toggle('build-mode',build);
      const now=this.now();
      for(const part of this.parts){
        if(changedMode){part.active=false;part.done=false;}
        const eligible=!build||part.step<=revealed;
        if(!eligible){part.active=false;part.done=false;this.restore(part);if(part.build)part.element.classList.add('unrevealed');continue;}
        if(!part.active){part.active=true;part.done=false;part.started=now;}
      }
      this.draw(now);
    }
    /** Use one timestamp for bar growth, counters and delayed results within a reveal. */
    draw(now){
      if(this.disposed||this.printing!==null)return;
      this.cancel();let pending=false;
      const immediate=!this.build||this.reducedMotion();
      for(const part of this.parts){
        if(!part.active||part.done)continue;
        const deadline=this.deadline(part),waiting=!immediate&&now<deadline;
        if(part.build)part.element.classList.toggle('unrevealed',waiting);
        if(waiting){if(part.element.closest('[data-reveal="blur"].unrevealed'))this.restore(part);else this.paint(part,0);pending=true;continue;}
        const progress=immediate||part.duration===0?1:Math.max(0,Math.min(1,(now-deadline)/part.duration));
        if(progress===1){this.restore(part);part.done=true;continue;}
        this.paint(part,progress);
        pending=true;
      }
      this.accessibility();
      if(pending)this.frame=this.requestFrame(()=>{this.frame=null;this.draw(this.now());});
    }
  }
  if(typeof module!=='undefined')module.exports={PresentationClock,nextState,nextBuildState,buildCount,shownIndices,startIndex,duration,keyAction,animationTime,animationStep,countDecimals,counterOptions,growthClip,BuildAnimations};
  if(typeof document==='undefined')return;
  if(document.documentElement.dataset.capture==='slide'){
    window.SlideStudioBuilds={BuildAnimations,buildCount};
    return;
  }

  if(!document.getElementById('slideshow-styles')){const runtimeStyles=document.createElement('link');runtimeStyles.rel='stylesheet';runtimeStyles.href=new URL('slideshow.css',document.currentScript?.src||location.href).href;document.head.append(runtimeStyles);}

  const deck=JSON.parse(document.getElementById('deck-data').textContent);
  const $=id=>document.getElementById(id);
  document.title=deck.title;document.querySelector('.deck-title').textContent=deck.title;
  const {button:iconButton}=window.SlideIcons;
  const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const query=new URLSearchParams(location.search),isAudience=query.get('audience')==='1';
  const direct=query.get('present')==='1'||isAudience||document.documentElement.dataset.export==='audience';
  const channelName=query.get('channel')||`slides-${crypto.randomUUID()}`;
  const channel=typeof BroadcastChannel==='function'?new BroadcastChannel(channelName):null;
  const workspaceDeck=location.pathname.match(/^\/decks\/([^/]+)\//)?.[1];
  const clock=new PresentationClock();
  const expectedNotes=new Map(deck.slides.map(slide=>[slide.id,slide.notes]));
  const noteDrafts=new Map(),saving=new Map();
  const noteKey=`slide-studio:presenter-notes:${deck.id}`;
  const requested=deck.slides.findIndex(slide=>slide.id===decodeURIComponent(location.hash.slice(1)));
  const initial=startIndex(deck.slides,Math.max(0,requested));
  let current=isAudience&&requested>=0?requested:direct?Math.max(0,initial):Math.max(0,requested);
  let emptyShow=direct&&initial<0&&!(isAudience&&requested>=0);
  let revealed=0,mode=direct?'single':'preview',active=direct,blackout=false,laser=false;
  let audienceWindow=null,screenDetails=null,selectedScreen=null,noteTimer,lastWheel=0,lastPointer=0;
  let filmSignature='',stageSignature='',notesSlide=null;
  const slideSources=new WeakMap();
  const slideAnimations=new Map(),motionPreference=window.matchMedia?.('(prefers-reduced-motion: reduce)');
  let ending=false,leavingFullscreen=false;

  if(direct)document.body.classList.add(isAudience?'audience-only':'single-screen');
  $('presenter-view').innerHTML=`
    <header class="presenter-top"><button id="end-presentation">End</button><span id="display-status" role="status" hidden></span><div class="display-tools"><select id="screen-choice" aria-label="Audience display" hidden></select><button id="open-audience">Open audience window</button><button id="swap-displays" hidden></button></div><button id="presenter-fullscreen"></button></header><div id="slide-announcement" class="presenter-announcement" aria-live="polite" aria-atomic="true"></div>
    <div class="presenter-workspace"><section class="presenter-main"><div class="presenter-time"><div class="timer-controls"><time id="elapsed">00:00</time><button id="pause-timer"></button><button id="reset-timer"></button></div><time id="wall-clock"></time></div><div class="stage-slot" id="presenter-current"></div><footer class="presenter-nav"><div><button id="laser-button"></button><button id="blackout-button"></button><button id="audience-view-button"></button></div><div><button id="presenter-previous"></button><span id="presenter-counter"></span><button id="presenter-next-button"></button></div></footer></section>
    <section class="presenter-side"><h2>Next slide</h2><div class="stage-slot" id="presenter-next"></div><div class="presenter-notes-heading"><h2>Notes</h2><span id="presenter-note-status" role="status"></span><button id="save-presenter-notes"></button></div><textarea id="presenter-notes" aria-label="Speaker notes" placeholder="Add context you want to mention…"></textarea><div class="note-size-controls"><button id="notes-smaller"></button><button id="notes-larger"></button></div></section></div>
    <nav id="presenter-filmstrip" class="presenter-filmstrip" aria-label="All slides"></nav>`;
  const controls=document.createElement('div');controls.className='single-controls';controls.innerHTML='<button id="single-previous"></button><span id="single-counter"></span><button id="single-next"></button><button id="all-slides-button"></button><button id="single-blackout"></button><button id="show-presenter"></button><button id="single-fullscreen"></button><button id="exit-slideshow"></button>';document.body.append(controls);
  const overview=document.createElement('dialog');overview.id='all-slides-dialog';overview.innerHTML='<header><h2>All slides</h2><button id="close-all-slides"></button></header><div id="all-slides-grid"></div>';document.body.append(overview);
  const pointer=document.createElement('div');pointer.className='laser-pointer';pointer.hidden=true;$('stage').append(pointer);
  for(const [id,icon,label,show] of [
    ['previous','left','Previous slide',false],['next','right','Next slide',false],['notes-button','notes','Notes',false],['sources-button','sources','Sources',false],['present-button','present','Present',true],['audience-fullscreen','fullscreen','Fullscreen',false],
    ['end-presentation','close','End',true],['open-audience','present','Open audience window',true],['swap-displays','swap','Swap displays',false],['presenter-fullscreen','fullscreen','Fullscreen',false],
    ['pause-timer','pause','Pause timer',false],['reset-timer','refresh','Reset timer',false],['laser-button','cursor','Laser pointer',false],['blackout-button','eye','Black screen',false],['audience-view-button','slides','Audience view',false],
    ['presenter-previous','left','Previous slide',false],['presenter-next-button','right','Next slide',false],['save-presenter-notes','save','Save notes',false],['notes-smaller','minus','Smaller notes',false],['notes-larger','plus','Larger notes',false],
    ['single-previous','left','Previous slide',false],['single-next','right','Next slide',false],['single-blackout','eye','Black screen',false],['show-presenter','present','Presenter view',false],['single-fullscreen','fullscreen','Fullscreen',false],['exit-slideshow','close','End presentation',false],
    ['all-slides-button','grid','See all slides',false],['close-all-slides','close','Close',false]
  ])iconButton($(id),icon,label,show);
  document.querySelectorAll('[data-close]').forEach(button=>iconButton(button,'close','Close',false));
  if(!isAudience){try{const saved=JSON.parse(localStorage.getItem(noteKey)||'{}');for(const [id,value] of Object.entries(saved)){if(value&&typeof value.notes==='string'&&typeof value.expected==='string')noteDrafts.set(id,value);}}catch{}}
  let noteFont=22;

  /** Keep unfinished notes attached to their original slide and concurrency baseline. */
  function persistNotes(){try{if(noteDrafts.size)localStorage.setItem(noteKey,JSON.stringify(Object.fromEntries(noteDrafts)));else localStorage.removeItem(noteKey);}catch{}}
  function noteValue(index=current){const slide=deck.slides[index];return noteDrafts.get(slide?.id)?.notes??slide?.notes??'';}
  function noteStatus(message=''){$('presenter-note-status').textContent=message;$('message').textContent=message;}
  function displayStatus(message=''){if(isAudience)return;const status=$('display-status');status.textContent=message;status.hidden=!message;}
  function editNotes(value){if(isAudience)return;const slide=deck.slides[current];noteDrafts.set(slide.id,{notes:value,expected:noteDrafts.get(slide.id)?.expected??expectedNotes.get(slide.id)});persistNotes();noteStatus('Unsaved');clearTimeout(noteTimer);noteTimer=setTimeout(()=>saveNotes(slide.id),700);}
  /** Save a captured slide ID, retaining failed or concurrently edited drafts. */
  function saveNotes(id=deck.slides[current].id){
    if(isAudience||!noteDrafts.has(id))return Promise.resolve();
    if(saving.has(id))return saving.get(id);
    const task=Promise.resolve().then(async()=>{
      const draft={...noteDrafts.get(id)};let saved=false;
      if(deck.slides[current].id===id)noteStatus('Saving…');
      try{
        if(workspaceDeck){const response=await fetch(`/api/decks/${workspaceDeck}/slides/${id}/notes`,{method:'POST',keepalive:true,headers:{'Content-Type':'application/json'},body:JSON.stringify({notes:draft.notes,expected_notes:draft.expected})});if(!response.ok){const error=await response.json();throw new Error(error.detail||'Notes could not be saved.');}}
        else{const stored=JSON.parse(localStorage.getItem(`slide-notes-${deck.id}`)||'{}');stored[id]=draft.notes;localStorage.setItem(`slide-notes-${deck.id}`,JSON.stringify(stored));}
        deck.slides.find(slide=>slide.id===id).notes=draft.notes;expectedNotes.set(id,draft.notes);saved=true;
        if(noteDrafts.get(id)?.notes===draft.notes)noteDrafts.delete(id);else if(noteDrafts.has(id))noteDrafts.get(id).expected=draft.notes;
        persistNotes();if(deck.slides[current].id===id)noteStatus(noteDrafts.has(id)?'Unsaved':'Saved');
      }catch(error){if(deck.slides[current].id===id)noteStatus(`Not saved: ${error.message}`);}
      finally{saving.delete(id);}
      if(saved&&noteDrafts.has(id))await saveNotes(id);
    });
    saving.set(id,task);return task;
  }

  /** Update the audience from the presenter's frozen slide snapshot, excluding private notes. */
  function broadcast(content=false){if(isAudience)return;channel?.postMessage({kind:'state',current,revealed,blackout,emptyShow,...(content?{slides:deck.slides.map(({id,title,html,hidden})=>({id,title,html,hidden}))}:{})});}
  function hasBuild(index=current){return buildCount(deck.slides[index]?.html||'');}
  /** Keep the same slide DOM during builds so previously revealed chart lines stay still. */
  function putSlide(element,index,build=true){
    const html=deck.slides[index]?.html||`<article class="slide runtime-empty"><h1>${emptyShow?'No slides to present.':'End.'}</h1></article>`;
    const source=slideSources.get(element),changed=source?.html!==html||source?.index!==index;
    if(changed){slideAnimations.get(element)?.dispose();slideAnimations.delete(element);element.innerHTML=html;slideSources.set(element,{html,index});}
    const slide=element.querySelector('.slide');if(!slide)return;
    if(!slideAnimations.has(element))slideAnimations.set(element,new BuildAnimations(slide,{reducedMotion:()=>!!motionPreference?.matches}));
    slideAnimations.get(element).update(revealed,{build});
  }
  /** Preserve note input and thumbnail DOM while changing only slide-dependent content. */
  function render(){
    const hidden=active?deck.slides.map(slide=>slide.hidden):[],counts=deck.slides.map(slide=>buildCount(slide.html));
    const ahead=nextBuildState(current,counts[current],1,counts,hidden).current;
    const signature=`${current}/${revealed}/${emptyShow}/${ahead}/${deck.slides[current]?.html}`;
    if(signature!==stageSignature){putSlide($('stage'),emptyShow?-1:current);putSlide($('presenter-current'),emptyShow?-1:current);putSlide($('presenter-next'),!emptyShow&&ahead!==current?ahead:-1,false);stageSignature=signature;}
    $('stage').classList.toggle('blacked-out',blackout);document.body.classList.toggle('blackout',blackout);
    const count=emptyShow?`0 / ${shownIndices(deck.slides).length}`:`${current+1} / ${deck.slides.length}`;for(const id of ['counter','presenter-counter','single-counter'])$(id).textContent=count;
    if(!isAudience){const announcement=emptyShow?'No slides to present.':`Slide ${current+1} of ${deck.slides.length}: ${deck.slides[current].title}`;if($('slide-announcement').textContent!==announcement)$('slide-announcement').textContent=announcement;}
    const before=nextBuildState(current,revealed,-1,counts,hidden),after=nextBuildState(current,revealed,1,counts,hidden);
    const atStart=emptyShow||before.current===current&&before.revealed===revealed,atEnd=emptyShow||after.current===current&&after.revealed===revealed;
    for(const id of ['previous','presenter-previous','single-previous'])$(id).disabled=atStart;
    for(const id of ['next','presenter-next-button','single-next'])$(id).disabled=atEnd;
    document.body.classList.toggle('single-screen',mode==='single'&&!isAudience);document.body.classList.toggle('presenter-mode',mode==='presenter');
    $('normal-view').hidden=mode==='presenter';$('presenter-view').hidden=mode!=='presenter';
    $('presenter-notes').disabled=emptyShow;
    if(notesSlide!==deck.slides[current].id||emptyShow){$('presenter-notes').value=emptyShow?'':noteValue();$('notes-text').value=emptyShow?'':noteValue();notesSlide=emptyShow?null:deck.slides[current].id;noteStatus(noteDrafts.has(notesSlide)?'Unsaved':'');}
    for(const id of ['blackout-button','single-blackout'])$(id).setAttribute('aria-pressed',String(blackout));
    $('laser-button').setAttribute('aria-pressed',String(laser));
    const film=JSON.stringify(deck.slides.map(({id,title,html,hidden})=>[id,title,html,hidden]));
    if(film!==filmSignature){filmSignature=film;const thumbs=deck.slides.map((slide,index)=>`<button data-index="${index}" class="${slide.hidden?'is-hidden':''}" aria-label="Slide ${index+1}: ${escape(slide.title)}${slide.hidden?' · Hidden':''}"><div class="film-slide">${slide.html}</div><span>${index+1}</span></button>`).join('');$('presenter-filmstrip').innerHTML=thumbs;$('all-slides-grid').innerHTML=thumbs;const shown=shownIndices(deck.slides);$('dots').style.setProperty('--slide-count',Math.max(1,shown.length));$('dots').innerHTML=shown.map((index,position)=>`<button aria-label="Slide ${index+1}: ${escape(deck.slides[index].title)}" title="${position+1} / ${shown.length} · ${escape(deck.slides[index].title)}" data-index="${index}"></button>`).join('');}
    for(const nav of [$('dots'),$('presenter-filmstrip')])nav.querySelectorAll('[data-index]').forEach(button=>button.setAttribute('aria-current',String(Number(button.dataset.index)===current)));
    const railFocused=$('dots').contains(document.activeElement),points=[...$('dots').querySelectorAll('[data-index]')],focusPoint=points.find(button=>Number(button.dataset.index)===current)||points.find(button=>Number(button.dataset.index)>current)||points.at(-1);
    for(const button of points){button.tabIndex=button===focusPoint?0:-1;button.classList.toggle('is-past',Number(button.dataset.index)<current);}
    if(railFocused&&focusPoint!==document.activeElement)focusPoint?.focus({preventScroll:true});
    focusPoint?.scrollIntoView({block:'nearest',inline:'nearest'});
    history.replaceState(null,'',`${location.pathname}${location.search}${emptyShow?'':`#${deck.slides[current].id}`}`);
    iconButton($('present-button'),'present',active?'End':'Present',true);
  }
  /** Route all audience commands to the presenter to keep one navigation authority. */
  function navigate(action,index){
    if(isAudience){channel?.postMessage({kind:'navigate',action,index});return;}
    const previousId=deck.slides[current].id;if(noteDrafts.has(previousId))void saveNotes(previousId);
    if(action==='next'||action==='previous'){if(!emptyShow)({current,revealed}=nextBuildState(current,revealed,action==='next'?1:-1,deck.slides.map(slide=>buildCount(slide.html)),active?deck.slides.map(slide=>slide.hidden):[]));}
    else{const route=active?shownIndices(deck.slides):deck.slides.map((_,index)=>index);emptyShow=action!=='goto'&&!route.length;if(!emptyShow){current=action==='first'?route[0]:action==='last'?route.at(-1):Math.max(0,Math.min(deck.slides.length-1,Number(index)||0));revealed=0;}}
    render();broadcast();$('presenter-filmstrip').querySelector('[aria-current="true"]')?.scrollIntoView({block:'nearest',inline:'nearest'});
  }
  function tick(){$('elapsed').textContent=duration(clock.elapsed());$('wall-clock').textContent=new Intl.DateTimeFormat([],{hour:'2-digit',minute:'2-digit'}).format(new Date());}
  /** A deliberate display change leaves native fullscreen without ending the slideshow. */
  async function leaveFullscreen(){if(!document.fullscreenElement)return;leavingFullscreen=true;try{await document.exitFullscreen();}catch{leavingFullscreen=false;}}
  async function fullscreen(){try{if(document.fullscreenElement)await leaveFullscreen();else await (mode==='presenter'?document.documentElement:$('stage')).requestFullscreen();displayStatus();}catch{displayStatus('Fullscreen unavailable. Try Fullscreen again.');}}
  function startClock(){clock.start();iconButton($('pause-timer'),'pause','Pause timer',false);tick();}
  /** Use CSS presentation immediately; native fullscreen remains subject to browser activation. */
  async function startPresentation(){
    if(active){endPresentation();return;}
    const initial=startIndex(deck.slides,current);emptyShow=initial<0;if(!emptyShow)current=initial;revealed=0;
    active=true;mode='single';clock.pause();clock.reset();startClock();render();void fullscreen();
    if('getScreenDetails' in window){try{screenDetails=await window.getScreenDetails();if(!active||ending)return;screenDetails.addEventListener('screenschange',updateScreens);updateScreens();const other=screenDetails.screens.find(screen=>screen!==screenDetails.currentScreen);if(other&&channel){selectedScreen=other;openAudience(other,true);}}catch{displayStatus('Automatic display selection unavailable.');}}
  }
  async function endPresentation(){
    if(isAudience){channel?.postMessage({kind:'end'});return;}
    if(ending)return;ending=true;
    active=false;clock.pause();audienceWindow?.close();audienceWindow=null;
    await Promise.all([...noteDrafts.keys()].map(id=>saveNotes(id)));
    await leaveFullscreen();
    if(workspaceDeck){location.replace(`/?deck=${encodeURIComponent(workspaceDeck)}#${encodeURIComponent(deck.slides[current].id)}`);return;}
    mode='preview';blackout=false;emptyShow=false;render();ending=false;
  }
  function screenKey(screen){return `${screen.left},${screen.top},${screen.width},${screen.height}`;}
  function updateScreens(){
    const screens=screenDetails?.screens||[];$('screen-choice').hidden=screens.length<2;$('swap-displays').hidden=screens.length<2;
    $('screen-choice').innerHTML=screens.map((screen,index)=>`<option value="${index}">${escape(screen.label||`Display ${index+1}`)}${screen===screenDetails.currentScreen?' · this screen':''}</option>`).join('');
    if(selectedScreen){const found=screens.findIndex(screen=>screenKey(screen)===screenKey(selectedScreen));if(found<0&&audienceWindow){audienceWindow.close();audienceWindow=null;mode='single';render();}else $('screen-choice').value=String(found);}
  }
  /** Open the audience window when allowed and expose an explicit retry when popups are blocked. */
  function openAudience(screen=selectedScreen,automatic=false){
    if(!channel){displayStatus('Audience windows unavailable. Continue on this screen.');return;}
    selectedScreen=screen;
    const url=new URL(location.href);url.searchParams.set('audience','1');url.searchParams.set('channel',channelName);url.searchParams.delete('presenter');
    const geometry=screen?`,left=${screen.availLeft},top=${screen.availTop},width=${screen.availWidth},height=${screen.availHeight}`:'';
    audienceWindow=window.open(url.href,`slide-audience-${channelName}`,`popup${geometry}`);
    if(!audienceWindow&&automatic){$('show-presenter').title='Presenter view · pop-up blocked';displayStatus('Pop-up blocked. Open Presenter view to retry.');return;}
    mode='presenter';void leaveFullscreen();active=true;startClock();render();updateScreens();
    $('open-audience').hidden=!!audienceWindow;
    if(!audienceWindow)displayStatus('Pop-up blocked. Allow pop-ups, then retry.');
    else{displayStatus('Audience window open. Check its display.');try{audienceWindow.focus();}catch{}broadcast(true);}
  }
  function blackScreen(){if(isAudience){channel?.postMessage({kind:'blackout'});return;}blackout=!blackout;render();broadcast();}
  function showPresenter(){if(isAudience)return;if(!active){const initial=startIndex(deck.slides,current);emptyShow=initial<0;if(!emptyShow)current=initial;revealed=0;}active=true;mode='presenter';void leaveFullscreen();startClock();$('open-audience').hidden=!!audienceWindow;render();}
  function selectIndex(event){const button=event.target.closest('[data-index]');if(button)navigate('goto',Number(button.dataset.index));}
  let overviewReturnFocus=null;
  function showAllSlides(){if(isAudience){channel?.postMessage({kind:'overview'});return;}overviewReturnFocus=document.activeElement;overview.showModal();overview.querySelector(`[data-index="${current}"]`)?.focus();}
  overview.addEventListener('close',()=>{if(overviewReturnFocus?.isConnected&&overviewReturnFocus.getClientRects().length)overviewReturnFocus.focus();overviewReturnFocus=null;});
  $('all-slides-button').addEventListener('click',showAllSlides);$('close-all-slides').addEventListener('click',()=>overview.close());
  $('all-slides-grid').addEventListener('click',event=>{if(!event.target.closest('[data-index]'))return;selectIndex(event);overview.close();});
  for(const id of ['stage','presenter-current'])$(id).addEventListener('contextmenu',event=>{if(!active)return;event.preventDefault();showAllSlides();});
  for(const id of ['previous','presenter-previous','single-previous'])$(id).addEventListener('click',()=>navigate('previous'));
  for(const id of ['next','presenter-next-button','single-next'])$(id).addEventListener('click',()=>navigate('next'));
  $('dots').addEventListener('click',selectIndex);$('presenter-filmstrip').addEventListener('click',selectIndex);
  $('present-button').addEventListener('click',()=>active?endPresentation():startPresentation());
  $('end-presentation').addEventListener('click',endPresentation);$('exit-slideshow').addEventListener('click',endPresentation);
  $('show-presenter').addEventListener('click',showPresenter);$('show-presenter').hidden=isAudience;
  $('audience-view-button').addEventListener('click',()=>{mode='single';render();});
  $('open-audience').addEventListener('click',()=>openAudience());
  $('screen-choice').addEventListener('change',event=>{const screen=screenDetails?.screens[Number(event.target.value)];if(screen){selectedScreen=screen;if(audienceWindow){try{audienceWindow.moveTo(screen.availLeft,screen.availTop);audienceWindow.resizeTo(screen.availWidth,screen.availHeight);displayStatus('Audience window open. Check its display.');}catch{displayStatus('Could not change displays. Move the audience window manually.');}}else openAudience(screen);}});
  $('swap-displays').addEventListener('click',()=>{if(!audienceWindow||!screenDetails||!selectedScreen)return;const original=screenDetails.currentScreen;try{window.moveTo(selectedScreen.availLeft,selectedScreen.availTop);window.resizeTo(selectedScreen.availWidth,selectedScreen.availHeight);audienceWindow.moveTo(original.availLeft,original.availTop);audienceWindow.resizeTo(original.availWidth,original.availHeight);selectedScreen=original;updateScreens();displayStatus('Displays changed. Check both windows.');}catch{displayStatus('Could not swap displays. Move the windows manually.');}});
  for(const id of ['single-fullscreen','presenter-fullscreen'])$(id).addEventListener('click',fullscreen);
  for(const id of ['blackout-button','single-blackout'])$(id).addEventListener('click',blackScreen);
  $('pause-timer').addEventListener('click',()=>{if(clock.running)clock.pause();else clock.start();iconButton($('pause-timer'),clock.running?'pause':'play',clock.running?'Pause timer':'Resume timer',false);tick();});
  $('reset-timer').addEventListener('click',()=>{clock.reset();tick();});
  $('laser-button').addEventListener('click',()=>{laser=!laser;$('laser-button').setAttribute('aria-pressed',String(laser));if(!laser)channel?.postMessage({kind:'pointer',visible:false});});
  $('presenter-current').addEventListener('pointermove',event=>{if(!laser||performance.now()-lastPointer<30)return;lastPointer=performance.now();const rect=event.currentTarget.getBoundingClientRect();channel?.postMessage({kind:'pointer',visible:true,x:(event.clientX-rect.left)/rect.width,y:(event.clientY-rect.top)/rect.height});});
  $('presenter-current').addEventListener('pointerleave',()=>channel?.postMessage({kind:'pointer',visible:false}));
  $('presenter-notes').addEventListener('input',event=>editNotes(event.target.value));$('notes-text').addEventListener('input',event=>editNotes(event.target.value));
  $('presenter-notes').addEventListener('blur',()=>saveNotes());$('notes-text').addEventListener('blur',()=>saveNotes());$('save-presenter-notes').addEventListener('click',()=>saveNotes());
  for(const [id,delta] of [['notes-smaller',-2],['notes-larger',2]])$(id).addEventListener('click',()=>{noteFont=Math.max(14,Math.min(40,noteFont+delta));$('presenter-notes').style.fontSize=`${noteFont}px`;});
  $('notes-button').addEventListener('click',()=>{$('notes-text').value=noteValue();$('notes-hint').textContent='';$('notes-dialog').showModal();});
  $('sources-button').addEventListener('click',()=>{$('sources-list').innerHTML=(deck.slides[current].sources||[]).map(source=>`<div class="source">${source.url?`<a href="${escape(source.url)}" target="_blank" rel="noopener">${escape(source.label)}</a>`:escape(source.label)}</div>`).join('')||'No sources.';$('sources-dialog').showModal();});
  document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>$(button.dataset.close).close()));
  for(const id of ['stage','presenter-current'])$(id).addEventListener('click',event=>{
    if(!active||laser||event.defaultPrevented||event.button!==0||event.ctrlKey||event.metaKey||event.altKey||event.shiftKey||event.target.closest('a,button,input,textarea,select,video,audio,[contenteditable],[role="button"],[data-index]'))return;
    navigate('next');
  });
  document.addEventListener('keydown',event=>{
    const editing=!!event.target.closest('textarea,input,select,[contenteditable]'),control=!!event.target.closest('button,a,[role="button"]'),dialog=!!document.querySelector('dialog[open]');
    const action=keyAction(event,{active,editing,control,dialog});
    if(action){event.preventDefault();if(action==='end')void endPresentation();else navigate(action);return;}
    if(active||editing||control||dialog||event.ctrlKey||event.metaKey||event.altKey)return;
    if(!isAudience&&event.key.toLowerCase()==='n')$('notes-button').click();
    if(!isAudience&&event.key.toLowerCase()==='s')$('sources-button').click();
    if(event.key.toLowerCase()==='f')void fullscreen();
  });
  document.addEventListener('fullscreenchange',()=>{if(document.fullscreenElement)return;if(leavingFullscreen){leavingFullscreen=false;return;}if(active&&!ending&&!isAudience)void endPresentation();});
  $('normal-view').addEventListener('wheel',event=>{if(Math.abs(event.deltaY)<18||performance.now()-lastWheel<650)return;event.preventDefault();lastWheel=performance.now();navigate(event.deltaY>0?'next':'previous');},{passive:false});
  if(!workspaceDeck&&!isAudience){try{const saved=JSON.parse(localStorage.getItem(`slide-notes-${deck.id}`)||'{}');for(const slide of deck.slides)if(typeof saved[slide.id]==='string')slide.notes=saved[slide.id];}catch{}}
  if(channel)channel.onmessage=event=>{
    const data=event.data;if(!data||typeof data!=='object')return;
    if(data.kind==='ready'&&!isAudience)broadcast(true);
    if(data.kind==='navigate'&&!isAudience)navigate(data.action,data.index);
    if(data.kind==='blackout'&&!isAudience)blackScreen();
    if(data.kind==='end'&&!isAudience)endPresentation();
    if(data.kind==='overview'&&!isAudience)showAllSlides();
    if(data.kind==='state'&&isAudience){if(Array.isArray(data.slides)&&data.slides.length)deck.slides=data.slides;if(Number.isInteger(data.current)&&data.current>=0&&data.current<deck.slides.length){current=data.current;revealed=Math.max(0,Math.min(hasBuild(current),Number(data.revealed)||0));blackout=!!data.blackout;emptyShow=!!data.emptyShow;render();}}
    if(data.kind==='pointer'&&isAudience){let dot=$('stage').querySelector('.laser-pointer');if(!dot){dot=pointer;$('stage').append(dot);}dot.hidden=!data.visible;if(data.visible){dot.style.left=`${Math.max(0,Math.min(1,data.x))*100}%`;dot.style.top=`${Math.max(0,Math.min(1,data.y))*100}%`;}}
  };
  if(isAudience){channel?.postMessage({kind:'ready'});void fullscreen();}
  const timer=setInterval(()=>{tick();if(audienceWindow?.closed){audienceWindow=null;$('open-audience').hidden=false;if(active&&mode==='presenter'){mode='single';render();}}},500);
  motionPreference?.addEventListener('change',()=>{for(const animation of slideAnimations.values())animation.update(revealed,{build:animation.build});});
  window.addEventListener('beforeprint',()=>{for(const animation of slideAnimations.values())animation.beforePrint();});
  window.addEventListener('afterprint',()=>{for(const animation of slideAnimations.values())animation.afterPrint();});
  window.addEventListener('beforeunload',()=>{for(const animation of slideAnimations.values())animation.dispose();persistNotes();if(!isAudience)audienceWindow?.close();channel?.close();clearInterval(timer);});
  render();tick();
  if(query.get('presenter')==='1'&&!isAudience)showPresenter();
  else if(query.get('present')==='1'&&!isAudience){active=false;void startPresentation();}
})();
