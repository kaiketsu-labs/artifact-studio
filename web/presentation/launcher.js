/* Keep the browser activation from Present while loading the audience document. */
((root)=>{
  'use strict';
  /** Own native fullscreen and return to the unchanged editor after the show. */
  function create({button,additionalButtons=[],onReturn=()=>{},onUnavailable=()=>{},prepare=null,onError=()=>{},doc=document,view=window}){
    let session=null;
    const currentFullscreen=()=>doc.fullscreenElement||doc.webkitFullscreenElement;
    const supported=()=>!!(doc.documentElement.requestFullscreen||doc.documentElement.webkitRequestFullscreen);
    function notify(){session?.frame.contentWindow?.postMessage({type:'artifact-studio:host',action:'fullscreen-state',available:supported(),active:!!currentFullscreen()},view.location.origin);}
    /** Make the first rendered slide usable without waiting for every document asset. */
    function show(){
      const current=session;if(!current||current.visible)return;
      current.visible=true;current.frame.style.visibility='';current.frame.removeAttribute('aria-hidden');
      current.loading.remove();current.frame.focus();notify();
    }
    async function fullscreen(action='enter'){
      const current=session;if(!current)return;
      const active=currentFullscreen();
      if(action==='leave'||action==='toggle'&&active){
        if(!active)return;
        current.deliberate=true;current.native=false;
        try{await (doc.exitFullscreen||doc.webkitExitFullscreen).call(doc);}catch{current.deliberate=false;current.native=!!currentFullscreen();}
      }else if(!active){
        current.deliberate=false;
        const request=current.layer.requestFullscreen||current.layer.webkitRequestFullscreen;
        if(!request){onUnavailable();notify();return;}
        try{await request.call(current.layer);}catch{if(session===current)onUnavailable();}
      }
      notify();
    }
    async function close(){
      const current=session;if(!current)return;session=null;
      try{current.slideId=decodeURIComponent(current.frame.contentWindow.location.hash.slice(1))||current.slideId;}catch{}
      if(currentFullscreen()===current.layer){try{await (doc.exitFullscreen||doc.webkitExitFullscreen).call(doc);}catch{}}
      current.layer.remove();doc.body.classList.remove('is-presenting');
      for(const [element,inert] of current.siblings)element.inert=inert;
      current.trigger.focus();onReturn(current.slideId);
    }
    function end(){if(!session)return;if(session.ready)session.frame.contentWindow?.postMessage({type:'artifact-studio:host',action:'end'},view.location.origin);else void close();}
    function changed(){
      if(!session)return;
      if(currentFullscreen()===session.layer)session.native=true;
      else if(session.deliberate){session.deliberate=false;session.native=false;}
      else if(session.native){session.native=false;end();}
      notify();
    }
    /** Request fullscreen before navigation or awaiting any asynchronous preparation. */
    function open(event,trigger,href=button.getAttribute('href')){
      if(event.button>0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
      if(!href)return;
      event.preventDefault();if(session)return;
      const url=new URL(href,view.location.href);if(url.origin!==view.location.origin)return;
      const layer=doc.createElement('div'),frame=doc.createElement('iframe'),loading=doc.createElement('div'),status=doc.createElement('p'),cancel=doc.createElement('button');
      layer.className='workspace-presentation';frame.title='Presentation';frame.allow='fullscreen; window-management';frame.allowFullscreen=true;
      frame.style.visibility='hidden';frame.setAttribute('aria-hidden','true');
      loading.className='presentation-loading';status.setAttribute('role','status');status.textContent='Starting presentation…';
      cancel.type='button';cancel.textContent='Cancel';cancel.addEventListener('click',()=>void close());loading.append(status,cancel);
      const siblings=[...doc.body.children].map(element=>[element,element.inert]);
      for(const [element] of siblings)element.inert=true;
      layer.append(frame,loading);doc.body.append(layer);doc.body.classList.add('is-presenting');
      session={layer,frame,trigger,siblings,loading,slideId:decodeURIComponent(url.hash.slice(1)),native:false,deliberate:false,ready:false,visible:false};
      void fullscreen();
      cancel.focus();
      url.searchParams.set('workspace','1');
      const current=session,load=()=>{if(session===current){status.textContent='Loading slides…';frame.src=url.href;}};
      frame.addEventListener('artifact-studio:display-change',event=>{if(session===current)current.deliberate=event.detail!==false&&(current.native||currentFullscreen()===current.layer);});
      if(prepare)Promise.resolve().then(()=>prepare()).then(load,error=>{if(session===current)void close().then(()=>onError(error.message));});
      else load();
      frame.addEventListener('load',()=>{if(session?.frame!==frame)return;try{if(frame.contentWindow.location.href==='about:blank')return;if(frame.contentWindow.location.pathname===(view.ArtifactSession?.home?.() || '/')&&new URL(frame.contentWindow.location.href).searchParams.has('deck')){void close();return;}}catch{}show();});
    }
    view.addEventListener('message',event=>{
      if(!session||event.source!==session.frame.contentWindow||event.origin!==view.location.origin||event.data?.type!=='artifact-studio:presentation')return;
      const data=event.data;
      if(typeof data.slide_id==='string')session.slideId=data.slide_id;
      if(data.action==='end')void close();
      else if(data.action==='ready'){session.ready=true;show();notify();}
      else if(data.action==='fullscreen'&&['enter','leave','toggle'].includes(data.mode))void fullscreen(data.mode);
    });
    doc.addEventListener('fullscreenchange',changed);doc.addEventListener('webkitfullscreenchange',changed);
    doc.addEventListener('keydown',event=>{if(session&&!session.visible&&event.key==='Escape'){event.preventDefault();void close();}});
    for(const trigger of [button,...additionalButtons])trigger.addEventListener('click',event=>open(event,trigger));
    return {isOpen:()=>!!session,start({fromBeginning=false}={}){const href=button.getAttribute('href');if(!href)return;const url=new URL(href,view.location.href);if(fromBeginning)url.hash='';open({button:0,preventDefault(){}},button,url.href);}};
  }
  const api={create};if(typeof module!=='undefined')module.exports=api;else root.SlidePresentationLauncher=api;
})(typeof window==='undefined'?{}:window);
