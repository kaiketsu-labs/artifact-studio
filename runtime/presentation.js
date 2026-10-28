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
  /** Format a duration without wrapping after an hour. */
  function duration(milliseconds){const total=Math.floor(Math.max(0,milliseconds)/1000);const seconds=String(total%60).padStart(2,'0');const minutes=String(Math.floor(total/60)%60).padStart(2,'0');return total>=3600?`${Math.floor(total/3600)}:${minutes}:${seconds}`:`${minutes}:${seconds}`;}
  if(typeof module!=='undefined')module.exports={PresentationClock,nextState,duration};
  if(typeof document==='undefined')return;

  const deck=JSON.parse(document.getElementById('deck-data').textContent);
  const $=id=>document.getElementById(id);
  document.title=deck.title;document.querySelector('.deck-title').textContent=deck.title;
  const {button:iconButton}=window.SlideIcons;
  const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const query=new URLSearchParams(location.search),isAudience=query.get('audience')==='1';
  const direct=query.get('present')==='1'||isAudience;
  const channelName=query.get('channel')||`slides-${crypto.randomUUID()}`;
  const channel=typeof BroadcastChannel==='function'?new BroadcastChannel(channelName):null;
  const workspaceDeck=location.pathname.match(/^\/decks\/([^/]+)\//)?.[1];
  const clock=new PresentationClock();
  const expectedNotes=new Map(deck.slides.map(slide=>[slide.id,slide.notes]));
  const noteDrafts=new Map(),saving=new Map();
  const noteKey=`slide-studio:presenter-notes:${deck.id}`;
  let current=Math.max(0,deck.slides.findIndex(slide=>slide.id===decodeURIComponent(location.hash.slice(1))));
  let revealed=false,mode=direct?'single':'preview',active=direct,blackout=false,laser=false;
  let audienceWindow=null,screenDetails=null,selectedScreen=null,noteTimer,lastWheel=0,lastPointer=0;
  let filmSignature='',stageSignature='',notesSlide=null;

  if(direct)document.body.classList.add(isAudience?'audience-only':'single-screen');
  $('presenter-view').innerHTML=`
    <header class="presenter-top"><button id="end-presentation">End</button><div class="display-tools"><select id="screen-choice" aria-label="Audience display" hidden></select><button id="open-audience">Open audience window</button><button id="swap-displays" hidden></button></div><button id="presenter-fullscreen"></button></header>
    <div class="presenter-workspace"><section class="presenter-main"><div class="presenter-time"><div class="timer-controls"><time id="elapsed">00:00</time><button id="pause-timer"></button><button id="reset-timer"></button></div><time id="wall-clock"></time></div><div class="stage-slot" id="presenter-current"></div><footer class="presenter-nav"><div><button id="laser-button"></button><button id="blackout-button"></button><button id="audience-view-button"></button></div><div><button id="presenter-previous"></button><span id="presenter-counter"></span><button id="presenter-next-button"></button></div></footer></section>
    <section class="presenter-side"><h2>Next slide</h2><div class="stage-slot" id="presenter-next"></div><div class="presenter-notes-heading"><h2>Notes</h2><span id="presenter-note-status" role="status"></span><button id="save-presenter-notes"></button></div><textarea id="presenter-notes" aria-label="Speaker notes" placeholder="Add context you want to mention…"></textarea><div class="note-size-controls"><button id="notes-smaller"></button><button id="notes-larger"></button></div></section></div>
    <nav id="presenter-filmstrip" class="presenter-filmstrip" aria-label="All slides"></nav>`;
  const controls=document.createElement('div');controls.className='single-controls';controls.innerHTML='<button id="single-previous"></button><span id="single-counter"></span><button id="single-next"></button><button id="single-blackout"></button><button id="show-presenter"></button><button id="single-fullscreen"></button><button id="exit-slideshow"></button>';document.body.append(controls);
  const pointer=document.createElement('div');pointer.className='laser-pointer';pointer.hidden=true;$('stage').append(pointer);
  for(const [id,icon,label,show] of [
    ['previous','left','Previous slide',false],['next','right','Next slide',false],['notes-button','notes','Notes',false],['sources-button','sources','Sources',false],['present-button','present','Present',true],['audience-fullscreen','fullscreen','Fullscreen',false],
    ['end-presentation','close','End',true],['open-audience','present','Open audience window',true],['swap-displays','swap','Swap displays',false],['presenter-fullscreen','fullscreen','Fullscreen',false],
    ['pause-timer','pause','Pause timer',false],['reset-timer','refresh','Reset timer',false],['laser-button','cursor','Laser pointer',false],['blackout-button','eye','Black screen',false],['audience-view-button','slides','Audience view',false],
    ['presenter-previous','left','Previous slide',false],['presenter-next-button','right','Next slide',false],['save-presenter-notes','save','Save notes',false],['notes-smaller','minus','Smaller notes',false],['notes-larger','plus','Larger notes',false],
    ['single-previous','left','Previous slide',false],['single-next','right','Next slide',false],['single-blackout','eye','Black screen',false],['show-presenter','present','Presenter view',false],['single-fullscreen','fullscreen','Fullscreen',false],['exit-slideshow','close','End presentation',false]
  ])iconButton($(id),icon,label,show);
  document.querySelectorAll('[data-close]').forEach(button=>iconButton(button,'close','Close',false));
  if(!isAudience){try{const saved=JSON.parse(localStorage.getItem(noteKey)||'{}');for(const [id,value] of Object.entries(saved)){if(value&&typeof value.notes==='string'&&typeof value.expected==='string')noteDrafts.set(id,value);}}catch{}}
  let noteFont=22;

  /** Keep unfinished notes attached to their original slide and concurrency baseline. */
  function persistNotes(){try{if(noteDrafts.size)localStorage.setItem(noteKey,JSON.stringify(Object.fromEntries(noteDrafts)));else localStorage.removeItem(noteKey);}catch{}}
  function noteValue(index=current){const slide=deck.slides[index];return noteDrafts.get(slide?.id)?.notes??slide?.notes??'';}
  function noteStatus(message=''){$('presenter-note-status').textContent=message;$('message').textContent=message;}
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
  function broadcast(content=false){if(isAudience)return;channel?.postMessage({kind:'state',current,revealed,blackout,...(content?{slides:deck.slides.map(({id,title,html})=>({id,title,html}))}:{})});}
  function hasBuild(index=current){return /\bdata-build\b/.test(deck.slides[index]?.html||'');}
  function putSlide(element,index,build=true){element.innerHTML=deck.slides[index]?.html||'<article class="slide statement"><div class="slide-body"><h1>End.</h1></div></article>';const slide=element.querySelector('.slide');if(slide){slide.classList.toggle('build-mode',build);slide.querySelectorAll('[data-build]').forEach(part=>part.classList.toggle('unrevealed',build&&!revealed));}}
  /** Preserve note input and thumbnail DOM while changing only slide-dependent content. */
  function render(){
    const signature=`${current}/${revealed}/${deck.slides[current]?.html}`;
    if(signature!==stageSignature){putSlide($('stage'),current);putSlide($('presenter-current'),current);putSlide($('presenter-next'),current+1,false);stageSignature=signature;}
    $('stage').classList.toggle('blacked-out',blackout);document.body.classList.toggle('blackout',blackout);
    const count=`${current+1} / ${deck.slides.length}`;for(const id of ['counter','presenter-counter','single-counter'])$(id).textContent=count;
    const atStart=current===0&&!revealed,atEnd=current===deck.slides.length-1&&(!hasBuild()||revealed);
    for(const id of ['previous','presenter-previous','single-previous'])$(id).disabled=atStart;
    for(const id of ['next','presenter-next-button','single-next'])$(id).disabled=atEnd;
    document.body.classList.toggle('single-screen',mode==='single'&&!isAudience);document.body.classList.toggle('presenter-mode',mode==='presenter');
    $('normal-view').hidden=mode==='presenter';$('presenter-view').hidden=mode!=='presenter';
    if(notesSlide!==deck.slides[current].id){$('presenter-notes').value=noteValue();$('notes-text').value=noteValue();notesSlide=deck.slides[current].id;noteStatus(noteDrafts.has(notesSlide)?'Unsaved':'');}
    for(const id of ['blackout-button','single-blackout'])$(id).setAttribute('aria-pressed',String(blackout));
    $('laser-button').setAttribute('aria-pressed',String(laser));
    const film=JSON.stringify(deck.slides.map(({id,title,html})=>[id,title,html]));
    if(film!==filmSignature){filmSignature=film;$('presenter-filmstrip').innerHTML=deck.slides.map((slide,index)=>`<button data-index="${index}" aria-label="Slide ${index+1}: ${escape(slide.title)}"><div class="film-slide">${slide.html}</div><span>${index+1}</span></button>`).join('');$('dots').innerHTML=deck.slides.length>24?'<span class="progress-count"></span>':deck.slides.map((slide,index)=>`<button aria-label="Slide ${index+1}: ${escape(slide.title)}" title="${index+1} / ${deck.slides.length}" data-index="${index}"></button>`).join('');}
    for(const nav of [$('dots'),$('presenter-filmstrip')])nav.querySelectorAll('[data-index]').forEach(button=>button.setAttribute('aria-current',String(Number(button.dataset.index)===current)));
    const counter=$('dots').querySelector('.progress-count');if(counter)counter.textContent=count;
    history.replaceState(null,'',`${location.pathname}${location.search}#${deck.slides[current].id}`);
    iconButton($('present-button'),'present',active?'End':'Present',true);
  }
  /** Route all audience commands to the presenter to keep one navigation authority. */
  function navigate(action,index){
    if(isAudience){channel?.postMessage({kind:'navigate',action,index});return;}
    const previousId=deck.slides[current].id;if(noteDrafts.has(previousId))void saveNotes(previousId);
    if(action==='next'||action==='previous')({current,revealed}=nextState(current,revealed,action==='next'?1:-1,hasBuild(),deck.slides.length));
    else{current=action==='first'?0:action==='last'?deck.slides.length-1:Math.max(0,Math.min(deck.slides.length-1,Number(index)||0));revealed=false;}
    render();broadcast();$('presenter-filmstrip').querySelector('[aria-current="true"]')?.scrollIntoView({block:'nearest',inline:'nearest'});
  }
  function tick(){$('elapsed').textContent=duration(clock.elapsed());$('wall-clock').textContent=new Intl.DateTimeFormat([],{hour:'2-digit',minute:'2-digit'}).format(new Date());}
  async function fullscreen(){try{if(document.fullscreenElement)await document.exitFullscreen();else await (mode==='presenter'?document.documentElement:$('stage')).requestFullscreen();}catch{}}
  function startClock(){clock.start();iconButton($('pause-timer'),'pause','Pause timer',false);tick();}
  /** Use CSS presentation immediately; native fullscreen remains subject to browser activation. */
  async function startPresentation(){
    if(active){endPresentation();return;}
    active=true;mode='single';clock.pause();clock.reset();startClock();render();void fullscreen();
    if('getScreenDetails' in window){try{screenDetails=await window.getScreenDetails();screenDetails.addEventListener('screenschange',updateScreens);updateScreens();const other=screenDetails.screens.find(screen=>screen!==screenDetails.currentScreen);if(other&&channel){selectedScreen=other;openAudience(other,true);}}catch{}}
  }
  async function endPresentation(){
    if(isAudience){channel?.postMessage({kind:'end'});return;}
    await Promise.all([...noteDrafts.keys()].map(id=>saveNotes(id)));
    active=false;clock.pause();mode='preview';blackout=false;audienceWindow?.close();audienceWindow=null;
    if(document.fullscreenElement)void document.exitFullscreen().catch(()=>{});
    render();
    if(query.get('present')==='1'&&workspaceDeck)location.href=`/?deck=${encodeURIComponent(workspaceDeck)}#${deck.slides[current].id}`;
  }
  function screenKey(screen){return `${screen.left},${screen.top},${screen.width},${screen.height}`;}
  function updateScreens(){
    const screens=screenDetails?.screens||[];$('screen-choice').hidden=screens.length<2;$('swap-displays').hidden=screens.length<2;
    $('screen-choice').innerHTML=screens.map((screen,index)=>`<option value="${index}">${escape(screen.label||`Display ${index+1}`)}${screen===screenDetails.currentScreen?' · this screen':''}</option>`).join('');
    if(selectedScreen){const found=screens.findIndex(screen=>screenKey(screen)===screenKey(selectedScreen));if(found<0&&audienceWindow){audienceWindow.close();audienceWindow=null;mode='single';render();}else $('screen-choice').value=String(found);}
  }
  /** Open the audience window when allowed and expose an explicit retry when popups are blocked. */
  function openAudience(screen=selectedScreen,automatic=false){
    if(!channel)return;
    selectedScreen=screen;
    const url=new URL(location.href);url.searchParams.set('audience','1');url.searchParams.set('channel',channelName);url.searchParams.delete('presenter');
    const geometry=screen?`,left=${screen.availLeft},top=${screen.availTop},width=${screen.availWidth},height=${screen.availHeight}`:'';
    audienceWindow=window.open(url.href,`slide-audience-${channelName}`,`popup${geometry}`);
    if(!audienceWindow&&automatic){$('show-presenter').title='Presenter view · audience window blocked';return;}
    mode='presenter';if(document.fullscreenElement)void document.exitFullscreen().catch(()=>{});active=true;startClock();render();updateScreens();
    $('open-audience').hidden=!!audienceWindow;
    if(!audienceWindow)noteStatus('Allow the audience window, then try again.');
    else{try{audienceWindow.focus();}catch{}broadcast(true);}
  }
  function blackScreen(){if(isAudience){channel?.postMessage({kind:'blackout'});return;}blackout=!blackout;render();broadcast();}
  function showPresenter(){if(isAudience)return;active=true;mode='presenter';if(document.fullscreenElement)void document.exitFullscreen().catch(()=>{});startClock();$('open-audience').hidden=!!audienceWindow;render();}
  function selectIndex(event){const button=event.target.closest('[data-index]');if(button)navigate('goto',Number(button.dataset.index));}
  for(const id of ['previous','presenter-previous','single-previous'])$(id).addEventListener('click',()=>navigate('previous'));
  for(const id of ['next','presenter-next-button','single-next'])$(id).addEventListener('click',()=>navigate('next'));
  $('dots').addEventListener('click',selectIndex);$('presenter-filmstrip').addEventListener('click',selectIndex);
  $('present-button').addEventListener('click',()=>active?endPresentation():startPresentation());
  $('end-presentation').addEventListener('click',endPresentation);$('exit-slideshow').addEventListener('click',endPresentation);
  $('show-presenter').addEventListener('click',showPresenter);$('show-presenter').hidden=isAudience;
  $('audience-view-button').addEventListener('click',()=>{mode='single';render();});
  $('open-audience').addEventListener('click',()=>openAudience());
  $('screen-choice').addEventListener('change',event=>{const screen=screenDetails?.screens[Number(event.target.value)];if(screen){selectedScreen=screen;if(audienceWindow){try{audienceWindow.moveTo(screen.availLeft,screen.availTop);audienceWindow.resizeTo(screen.availWidth,screen.availHeight);}catch{}}else openAudience(screen);}});
  $('swap-displays').addEventListener('click',()=>{if(!audienceWindow||!screenDetails||!selectedScreen)return;const original=screenDetails.currentScreen;try{window.moveTo(selectedScreen.availLeft,selectedScreen.availTop);window.resizeTo(selectedScreen.availWidth,selectedScreen.availHeight);audienceWindow.moveTo(original.availLeft,original.availTop);audienceWindow.resizeTo(original.availWidth,original.availHeight);selectedScreen=original;updateScreens();}catch{}});
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
  document.addEventListener('keydown',event=>{
    if(event.target.closest('textarea,input,select,[contenteditable]')||document.querySelector('dialog[open]'))return;
    const actions={ArrowRight:'next',ArrowDown:'next',PageDown:'next',' ':'next',ArrowLeft:'previous',ArrowUp:'previous',PageUp:'previous',Home:'first',End:'last'};
    if(actions[event.key]){event.preventDefault();navigate(actions[event.key]);}
    if(event.key.toLowerCase()==='b')blackScreen();
    if(!isAudience&&event.key.toLowerCase()==='n')$('notes-button').click();
    if(!isAudience&&event.key.toLowerCase()==='s')$('sources-button').click();
    if(event.key.toLowerCase()==='f')void fullscreen();
    if(event.key==='Escape'&&active)endPresentation();
  });
  $('normal-view').addEventListener('wheel',event=>{if(Math.abs(event.deltaY)<18||performance.now()-lastWheel<650)return;event.preventDefault();lastWheel=performance.now();navigate(event.deltaY>0?'next':'previous');},{passive:false});
  if(!workspaceDeck&&!isAudience){try{const saved=JSON.parse(localStorage.getItem(`slide-notes-${deck.id}`)||'{}');for(const slide of deck.slides)if(typeof saved[slide.id]==='string')slide.notes=saved[slide.id];}catch{}}
  if(channel)channel.onmessage=event=>{
    const data=event.data;if(!data||typeof data!=='object')return;
    if(data.kind==='ready'&&!isAudience)broadcast(true);
    if(data.kind==='navigate'&&!isAudience)navigate(data.action,data.index);
    if(data.kind==='blackout'&&!isAudience)blackScreen();
    if(data.kind==='end'&&!isAudience)endPresentation();
    if(data.kind==='state'&&isAudience){if(Array.isArray(data.slides)&&data.slides.length)deck.slides=data.slides;if(Number.isInteger(data.current)&&data.current>=0&&data.current<deck.slides.length){current=data.current;revealed=!!data.revealed;blackout=!!data.blackout;render();}}
    if(data.kind==='pointer'&&isAudience){let dot=$('stage').querySelector('.laser-pointer');if(!dot){dot=pointer;$('stage').append(dot);}dot.hidden=!data.visible;if(data.visible){dot.style.left=`${Math.max(0,Math.min(1,data.x))*100}%`;dot.style.top=`${Math.max(0,Math.min(1,data.y))*100}%`;}}
  };
  if(isAudience){$('stage').addEventListener('dblclick',fullscreen);channel?.postMessage({kind:'ready'});void fullscreen();}
  const timer=setInterval(()=>{tick();if(audienceWindow?.closed){audienceWindow=null;$('open-audience').hidden=false;if(active&&mode==='presenter'){mode='single';render();}}},500);
  window.addEventListener('beforeunload',()=>{persistNotes();if(!isAudience)audienceWindow?.close();channel?.close();clearInterval(timer);});
  render();tick();
  if(query.get('presenter')==='1'&&!isAudience)showPresenter();
  else if(query.get('present')==='1'&&!isAudience){active=false;void startPresentation();}
})();
