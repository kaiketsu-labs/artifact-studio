/* Browser-provided dictation. Each recognition segment retains its original destination. */
((root)=>{
  'use strict';
  const microphone='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/></svg>';
  const stopIcon='<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
  const unsupported='Voice input is unavailable in this browser. You can still type, or use a browser with speech recognition.';
  const clone=value=>value===undefined?undefined:typeof structuredClone==='function'?structuredClone(value):JSON.parse(JSON.stringify(value));
  const errors={
    'not-allowed':'Microphone access was denied. Allow microphone access in your browser settings, then try again.',
    'service-not-allowed':'Your browser does not allow its speech service here. You can still type.',
    'audio-capture':'No microphone is available. Connect a microphone, then try again.',
    'network':'The browser speech service is unavailable. Try again, or type your message.',
    'no-speech':'No speech was heard. Start dictation again when ready.',
    'language-not-supported':'The browser speech service does not support this language. You can still type.',
    'aborted':'Dictation stopped.'
  };

  /** Coordinate one microphone across fields, without treating callbacks as current UI context. */
  function createRecorder({Recognition,language='en-US',onState=()=>{},onIdle=()=>{},drainMs=750,setTimer=setTimeout,clearTimer=clearTimeout}){
    let current=null,pending=null,destroyed=false;
    const isActive=handle=>!!(current&&(!handle||current.handle===handle)||pending&&(!handle||pending.handle===handle));
    const notify=(handle,phase,message='',interim='')=>onState(handle,{phase,message,interim,active:isActive(handle)});
    function capture(handle){
      try{return {handle,sink:{...handle.adapter},context:clone(handle.adapter.capture?.())};}
      catch{notify(handle,'error','This field is not ready for dictation. Try again after editing.');return null;}
    }
    function available(segment){
      try{return !segment.sink.available||segment.sink.available(segment.context)!==false;}
      catch{return false;}
    }
    function retire(segment,abort=false){
      if(segment.timer!==null)clearTimer(segment.timer);
      segment.timer=null;
      const recognition=segment.recognition;
      recognition.onstart=null;recognition.onresult=null;recognition.onerror=null;recognition.onend=null;
      if(abort)try{recognition.abort();}catch{}
      for(const resolve of segment.waiters)resolve();
    }
    function finish(segment,message='Dictation stopped.',abort=false){
      if(current!==segment)return;
      current=null;retire(segment,abort);
      const next=pending;pending=null;
      notify(segment.handle,'idle',message);
      if(next&&!destroyed)launch(next);
      else onIdle(segment.handle);
    }
    function fail(segment,message){
      if(current!==segment)return;
      const queued=pending;pending=null;current=null;retire(segment,true);
      if(queued&&queued.handle!==segment.handle)notify(queued.handle,'idle','Dictation stopped.');
      notify(segment.handle,'error',message);onIdle(segment.handle);
    }
    function launch(destination){
      if(destroyed)return false;
      if(!Recognition){notify(destination.handle,'unavailable',unsupported);return false;}
      if(!available(destination)){notify(destination.handle,'idle','The original field is no longer available.');onIdle(destination.handle);return false;}
      let recognition;
      try{recognition=new Recognition();}
      catch{notify(destination.handle,'error','The browser could not start speech recognition. You can still type.');onIdle(destination.handle);return false;}
      const segment={...destination,recognition,finals:new Set(),closing:false,interim:'',timer:null,waiters:[]};
      current=segment;
      recognition.continuous=true;recognition.interimResults=true;recognition.maxAlternatives=1;recognition.lang=language;
      recognition.onstart=()=>{if(current===segment&&!segment.closing)notify(segment.handle,'listening','Listening…');};
      recognition.onresult=event=>{
        if(current!==segment)return;
        const interim=[];
        for(let index=0;index<event.results.length;index++){
          const result=event.results[index],text=String(result?.[0]?.transcript||'').trim();
          if(!text)continue;
          if(!result.isFinal){interim.push(text);continue;}
          if(segment.finals.has(index))continue;
          segment.finals.add(index);
          if(!available(segment)){
            fail(segment,'The original field changed. Speech was not inserted: '+text);return;
          }
          let inserted=false;
          try{inserted=segment.sink.insert(text,segment.context)!==false;}catch{}
          if(!inserted){fail(segment,'Speech was not inserted because the field changed or reached its text limit: '+text);return;}
        }
        segment.interim=interim.join(' ');
        notify(segment.handle,segment.closing?'finishing':'listening',segment.closing?'Finishing dictation…':'Listening…',segment.interim);
      };
      recognition.onerror=event=>{if(current===segment)fail(segment,errors[event.error]||'Speech recognition stopped. Try again, or type your message.');};
      recognition.onend=()=>finish(segment,segment.interim?'Stopped. Unfinished speech was not inserted.':'Dictation stopped.');
      notify(segment.handle,'starting','Starting microphone…');
      try{recognition.start();}
      catch{fail(segment,'The microphone could not start. Check browser permissions, then try again.');return false;}
      return true;
    }
    function close(){
      const segment=current;if(!segment||segment.closing)return;
      segment.closing=true;
      notify(segment.handle,'finishing',pending?'Switching dictation context…':'Finishing dictation…',segment.interim);
      segment.timer=setTimer(()=>finish(segment,segment.interim?'Stopped. Unfinished speech was not inserted.':'Dictation stopped.',true),drainMs);
      try{segment.recognition.stop();}catch{finish(segment,'Dictation stopped.',true);}
    }
    function start(handle){
      if(destroyed)return false;
      const destination=capture(handle);if(!destination)return false;
      if(current){
        const queued=pending;pending=destination;
        if(queued&&queued.handle!==handle&&queued.handle!==current.handle)notify(queued.handle,'idle','Dictation stopped.');
        if(current.handle!==handle)notify(handle,'waiting','Waiting for the previous microphone segment…');
        close();return true;
      }
      return launch(destination);
    }
    function boundary(handle){
      if(!isActive(handle)||!current)return false;
      const destination=capture(handle);if(!destination)return false;
      pending=destination;close();return true;
    }
    function stop(handle=null){
      if(handle&&!isActive(handle))return Promise.resolve();
      if(pending&&(!handle||pending.handle===handle)){
        const queued=pending;pending=null;
        if(queued.handle!==current?.handle)notify(queued.handle,'idle','Dictation stopped.');
      }
      if(!current||handle&&current.handle!==handle)return Promise.resolve();
      const result=new Promise(resolve=>current.waiters.push(resolve));close();return result;
    }
    function cancel(handle=null){
      if(handle&&!isActive(handle))return;
      if(pending&&(!handle||pending.handle===handle)){const queued=pending;pending=null;if(queued.handle!==current?.handle)notify(queued.handle,'idle','Dictation stopped.');}
      if(current&&(!handle||current.handle===handle))finish(current,'Dictation cancelled. Final text already inserted is kept.',true);
    }
    return {start,boundary,stop,cancel,isActive,destroy(){destroyed=true;cancel();}};
  }

  /** Insert at the retained caret, respecting newer typing and native input/change handlers. */
  function insertText(input,text,context,document){
    if(!input.isConnected||input.disabled||input.readOnly)return false;
    const value=String(input.value??'');
    let start=context.start,end=context.end;
    if(document.activeElement===input){start=input.selectionStart??value.length;end=input.selectionEnd??start;}
    else if(value!==context.value)return false;
    const before=value.slice(0,start),after=value.slice(end);
    const insertion=(before&&!/\s$/.test(before)&&! /^[,.;:!?)]/.test(text)?' ':'')+text+(after&&!/^\s|^[,.;:!?)]/.test(after)?' ':'');
    const updated=before+insertion+after;
    if(input.maxLength>=0&&updated.length>input.maxLength)return false;
    try{input.setRangeText(insertion,start,end,'end');}
    catch{input.value=updated;try{input.setSelectionRange(start+insertion.length,start+insertion.length);}catch{}}
    context.value=updated;context.start=start+insertion.length;context.end=context.start;
    const EventType=input.ownerDocument?.defaultView?.Event||root.Event;
    input.dispatchEvent(new EventType('input',{bubbles:true}));
    return true;
  }

  /** Add microphone controls to human text fields, including fields rendered after startup. */
  function create(options={}){
    const document=options.document||root.document;
    const Recognition=options.Recognition??root.SpeechRecognition??root.webkitSpeechRecognition;
    const handles=new Map();let sequence=0,observer=null,refreshQueued=false,destroyed=false;
    const labelFor=input=>input.getAttribute('aria-label')||input.labels?.[0]?.textContent?.trim()||input.placeholder||input.id||'this field';
    function eligible(input){
      if(!input.isConnected||input.disabled||input.readOnly||input.closest('[hidden],[inert],[data-voice-exclude],dialog:not([open])'))return false;
      return !input.getClientRects||input.getClientRects().length>0;
    }
    function publish(handle,state,emit=true){
      const {button,status,controls,input}=handle;
      controls.dataset.voicePhase=state.phase;handle.phase=state.phase;
      button.setAttribute('aria-pressed',String(state.active));
      const label=typeof handle.adapter.label==='function'?handle.adapter.label():handle.adapter.label||labelFor(input);
      button.setAttribute('aria-label',(state.active?'Stop dictation for ':'Dictate into ')+label);
      button.title=['error','unavailable'].includes(state.phase)?state.message:state.active?'Stop dictation':'Dictate using your browser. Its speech provider may process audio remotely.';
      button.innerHTML=state.active?stopIcon:microphone;
      status.textContent=[state.message,state.interim].filter(Boolean).join(' ');
      status.classList.toggle('has-interim',!!state.interim);
      const EventType=document.defaultView?.CustomEvent||root.CustomEvent;
      const detail={phase:state.phase,active:state.active,input};
      if(emit&&EventType){input.dispatchEvent(new EventType('voice-input-state',{bubbles:true,detail}));if(input.ownerDocument!==document||!input.isConnected)document.dispatchEvent(new EventType('voice-input-state',{detail}));}
      if(emit)options.onState?.(detail);
    }
    const recorder=createRecorder({Recognition,language:options.language||root.navigator?.language||'en-US',drainMs:options.drainMs,
      setTimer:options.setTimer,clearTimer:options.clearTimer,onState:publish,
      onIdle:handle=>options.onIdle?.(handle.input)});
    function defaults(input){return {
      capture:()=>({value:String(input.value??''),start:input.selectionStart??String(input.value??'').length,end:input.selectionEnd??String(input.value??'').length}),
      available:()=>eligible(input),insert:(text,context)=>insertText(input,text,context,input.ownerDocument||document)
    };}
    function register(input,adapter){
      if(!input)return null;
      if(handles.has(input)){
        const existing=handles.get(input);
        if(adapter){
          existing.adapter={...existing.adapter,...adapter};
          if(adapter.controlsHost&&existing.controls.parentElement!==adapter.controlsHost)adapter.controlsHost.append(existing.controls);
          if(!recorder.isActive(existing))publish(existing,{phase:existing.phase,active:false,message:existing.status.textContent,interim:''},false);
        }
        return existing;
      }
      if(input.isContentEditable&&!adapter?.insert)return null;
      // Live speech status inside a wrapping label must not rename its text field.
      if(!input.hasAttribute('aria-label')&&!input.hasAttribute('aria-labelledby')&&input.labels?.length){
        const label=[...input.labels].map(node=>node.textContent.trim()).join(' ').trim();
        if(label)input.setAttribute('aria-label',label);
      }
      const controls=document.createElement('span'),button=document.createElement('button'),status=document.createElement('span');
      const id=input.id||'voice-field-'+(++sequence);if(!input.id&&!input.isContentEditable)input.id=id;
      controls.className='voice-input-controls';controls.dataset.voiceFor=id;controls.dataset.voicePhase=Recognition?'idle':'unavailable';
      button.className='voice-input-button';button.type='button';button.dataset.voiceFor=id;button.setAttribute('aria-pressed','false');
      status.className='voice-input-status';status.id='voice-status-'+(++sequence);status.setAttribute('role','status');status.setAttribute('aria-live','polite');status.setAttribute('aria-atomic','true');
      button.setAttribute('aria-describedby',status.id);controls.append(button,status);
      const handle={input,button,status,controls,rich:!!input.isContentEditable,phase:Recognition?'idle':'unavailable',adapter:{...defaults(input),...adapter},
        start:()=>recorder.start(handle),stop:()=>recorder.stop(handle),cancel:()=>recorder.cancel(handle)};
      handles.set(input,handle);
      if(adapter?.controlsHost)adapter.controlsHost.append(controls);else input.insertAdjacentElement('afterend',controls);
      button.addEventListener('pointerdown',event=>event.preventDefault());
      button.addEventListener('click',event=>{event.preventDefault();if(recorder.isActive(handle))void handle.stop();else handle.start();});
      publish(handle,{phase:handle.phase,active:false,message:Recognition?'':unsupported,interim:''},false);
      button.disabled=!Recognition||!!input.disabled||!!input.readOnly;
      controls.hidden=!eligible(input);
      if(!Recognition)button.title=unsupported;
      return handle;
    }
    function refresh(){
      if(destroyed||!document)return;
      if(options.discover!==false){
        for(const input of document.querySelectorAll('textarea,input:not([type]),input[type="text"],input[type="search"],input[type="url"]')){
          if(input.closest('[data-voice-exclude]'))continue;register(input);
        }
      }
      for(const [input,handle] of handles){
        if(!input.isConnected){if(recorder.isActive(handle))void handle.stop();handle.controls.remove();handles.delete(input);continue;}
        const active=recorder.isActive(handle),disabled=!Recognition||!active&&(!!input.disabled||!!input.readOnly);
        if(handle.button.disabled!==disabled)handle.button.disabled=disabled;
        if(!handle.adapter.controlsHost||handle.adapter.visible){
          const visible=handle.adapter.visible?handle.adapter.visible():!handle.rich||input.isContentEditable;
          const hidden=!active&&(!eligible(input)||!visible);
          if(handle.controls.hidden!==hidden)handle.controls.hidden=hidden;
        }
      }
    }
    const schedule=()=>{if(refreshQueued||destroyed)return;refreshQueued=true;queueMicrotask(()=>{refreshQueued=false;refresh();});};
    const keydown=event=>{if(event.key==='Escape'&&recorder.isActive()){event.preventDefault();void recorder.stop();}};
    const submit=event=>{
      const handle=[...handles.values()].find(handle=>recorder.isActive(handle)&&(handle.input.form===event.target||handle.input.closest('form')===event.target));
      if(!handle)return;
      event.preventDefault();event.stopImmediatePropagation();
      publish(handle,{phase:handle.phase,active:true,message:'Stop dictation before submitting.',interim:''});
    };
    const hide=()=>{if(document.hidden)recorder.cancel();};
    const closeDialog=event=>{if([...handles].some(([input,handle])=>event.target.contains?.(input)&&recorder.isActive(handle)))void recorder.stop();};
    document?.addEventListener('keydown',keydown);document?.addEventListener('submit',submit,true);document?.addEventListener('visibilitychange',hide);document?.addEventListener('close',closeDialog,true);
    const MutationObserver=options.MutationObserver??root.MutationObserver;
    const fieldSelector='textarea,input:not([type]),input[type="text"],input[type="search"],input[type="url"]';
    const containsField=node=>node.nodeType===1&&(handles.has(node)||node.matches?.(fieldSelector)||node.querySelector?.(fieldSelector)||[...handles.keys()].some(input=>node.contains?.(input)));
    const observe=records=>{
      if(records.some(record=>!record.target.closest?.('.voice-input-controls')&&(record.type==='attributes'?containsField(record.target):[...record.addedNodes,...record.removedNodes].some(containsField))))schedule();
    };
    if(document&&MutationObserver){observer=new MutationObserver(observe);observer.observe(document.documentElement,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','readonly','open','hidden','style','class','contenteditable','inert']});}
    refresh();
    return {register,refresh,boundary:input=>{const handle=handles.get(input);return !!handle&&recorder.boundary(handle);},
      stop:()=>recorder.stop(),cancel:()=>recorder.cancel(),isActive:input=>input?recorder.isActive(handles.get(input)||{}):recorder.isActive(),
      destroy(){destroyed=true;recorder.destroy();observer?.disconnect();document?.removeEventListener('keydown',keydown);document?.removeEventListener('submit',submit,true);document?.removeEventListener('visibilitychange',hide);document?.removeEventListener('close',closeDialog,true);for(const handle of handles.values())handle.controls.remove();handles.clear();}
    };
  }
  const api={create,createRecorder,insertText};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.SlideVoice=api;
})(typeof window==='undefined'?globalThis:window);
