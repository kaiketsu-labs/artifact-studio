/* Local playback over a disposable copy; the editable preview remains untouched. */
((root)=>{
  'use strict';
  /** Replay numbered builds in order, respecting their actual delays and durations. */
  class Playback {
    constructor(motion,steps,{now=()=>performance.now(),requestFrame=callback=>requestAnimationFrame(callback),cancelFrame=id=>cancelAnimationFrame(id),cssDeadline=()=>0,onFinish=()=>{}}={}){
      this.motion=motion;this.steps=steps;this.now=now;this.requestFrame=requestFrame;this.cancelFrame=cancelFrame;this.cssDeadline=cssDeadline;this.onFinish=onFinish;this.frame=null;this.stopped=false;this.step=0;
    }
    start(){this.advance();}
    advance(){
      this.motion.update(this.step);
      const now=this.now(),parts=this.motion.parts.filter(part=>part.active&&!part.done);
      this.until=Math.max(now+900,...parts.map(part=>this.motion.deadline(part)+part.duration+350),this.cssDeadline(now));
      this.frame=this.requestFrame(()=>this.tick());
    }
    tick(){
      if(this.stopped)return;
      this.frame=null;
      this.until=Math.max(this.until,this.cssDeadline(this.now()));
      if(this.now()>=this.until){
        if(this.step===this.steps){this.stop();this.onFinish();return;}
        this.step++;this.advance();return;
      }
      this.frame=this.requestFrame(()=>this.tick());
    }
    stop(){if(this.stopped)return;this.stopped=true;if(this.frame!==null)this.cancelFrame(this.frame);this.frame=null;this.motion.dispose();}
  }
  /** Start CSS entrances when their reveal becomes visible, including delayed groups. */
  class CSSPlayback {
    constructor(doc){this.doc=doc;this.states=new Map();}
    sync(now){
      for(const animation of this.doc.getAnimations()){
        const hidden=!!animation.effect?.target?.closest?.('[data-build].unrevealed');
        let state=this.states.get(animation);
        if(!state){
          state={started:false,end:0};this.states.set(animation,state);
          if('transitionProperty' in animation){
            if(hidden)animation.finish();
            else{const end=animation.effect?.getComputedTiming().endTime;state.end=now+(Number.isFinite(end)?Math.max(0,end-(Number(animation.currentTime)||0)):6000)+350;}
            state.started=true;
          }
          else{animation.pause();animation.currentTime=0;}
        }
        if(hidden||state.started)continue;
        animation.currentTime=0;animation.play();state.started=true;
        const end=animation.effect?.getComputedTiming().endTime;
        state.end=now+(Number.isFinite(end)?end:6000)+350;
      }
      return Math.max(0,...[...this.states.values()].map(state=>state.end));
    }
  }
  /** Detect authored motion after styles load, including CSS keyframes without builds. */
  function hasAnimations(doc){
    const slide=doc?.querySelector('.slide');if(!slide)return false;
    if(doc.getAnimations().length)return true;
    if(slide.querySelector('[data-build],[data-animate-grow="x"]'))return true;
    if([...slide.querySelectorAll('[data-count-to]')].some(element=>!element.childElementCount&&element.getAttribute('data-count-to').trim()!==''&&Number.isFinite(Number(element.getAttribute('data-count-to')))))return true;
    return [slide,...slide.querySelectorAll('*')].some(element=>doc.defaultView.getComputedStyle(element).animationName.split(',').some(name=>name.trim()!=='none'));
  }
  /** Keep ordinary previews complete and still; authored CSS remains unchanged. */
  function freeze(doc){for(const animation of doc.getAnimations()){animation.pause();const end=animation.effect?.getComputedTiming().endTime;animation.currentTime=Number.isFinite(end)?end:0;}}
  /** Keep playback, selection and unsent edits isolated from each other. */
  function create({button,frame,wrap,documentFor,onState=()=>{},onError=()=>{}}){
    let layer=null,playback=null,epoch=0,timeout=null,available=false;
    function render(){root.SlideIcons.button(button,layer?'stop':'play',layer?'Stop animation preview':available?'Play animations':'No animations on this slide');button.disabled=!available;button.dataset.playing=String(!!layer);onState(!!layer);}
    function stop(){epoch++;clearTimeout(timeout);playback?.stop();playback=null;layer?.remove();layer=null;wrap.classList.remove('is-playing');render();}
    function reset(){stop();available=false;render();}
    function bind(){available=hasAnimations(frame.contentDocument);render();}
    function play(){
      if(layer){stop();return;}
      if(!available)return;
      const slide=frame.contentDocument?.querySelector('.slide');if(!slide)return;
      const token=++epoch,overlay=document.createElement('div'),copy=document.createElement('iframe');
      overlay.className='animation-preview';overlay.inert=true;overlay.setAttribute('aria-hidden','true');overlay.hidden=true;
      copy.setAttribute('sandbox','allow-same-origin');copy.tabIndex=-1;copy.title='Animation preview';overlay.append(copy);layer=overlay;wrap.append(layer);render();
      const fail=()=>{if(epoch!==token)return;stop();onError('Animation preview couldn’t load. Try again.');};
      timeout=setTimeout(fail,10000);copy.onerror=fail;
      copy.onload=async()=>{
        try{
          const doc=copy.contentDocument;await doc.fonts.ready;if(epoch!==token)return;
          if(!doc.querySelector('link[rel=stylesheet]')?.sheet?.cssRules.length){fail();return;}
          clearTimeout(timeout);
          const target=doc.querySelector('.slide'),{BuildAnimations,buildCount}=root.SlideStudioBuilds;
          const motion=new BuildAnimations(target,{reducedMotion:()=>root.matchMedia('(prefers-reduced-motion: reduce)').matches});
          const css=new CSSPlayback(doc);
          overlay.hidden=false;wrap.classList.add('is-playing');
          playback=new Playback(motion,buildCount(target.outerHTML),{cssDeadline:now=>css.sync(now),onFinish:stop});
          playback.start();
        }catch{fail();}
      };
      copy.srcdoc=documentFor(slide.outerHTML);
    }
    button.addEventListener('click',play);
    button.ownerDocument.addEventListener('keydown',event=>{if(layer&&event.key==='Escape'){event.preventDefault();event.stopPropagation();stop();}},true);
    root.addEventListener('pagehide',stop);
    render();return {bind,reset,stop,isPlaying:()=>!!layer};
  }
  const api={Playback,CSSPlayback,hasAnimations,freeze,create};
  if(typeof module!=='undefined')module.exports=api;else root.SlideAnimationPreview=api;
})(typeof window==='undefined'?{}:window);
