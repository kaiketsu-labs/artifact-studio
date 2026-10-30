/* Keep the browser activation from Present while loading the audience document. */
((root)=>{
  'use strict';
  /** Own native fullscreen and return to the unchanged editor after the show. */
  function create({button,onReturn=()=>{},onUnavailable=()=>{},doc=document,view=window}){
    let session=null;
    const currentFullscreen=()=>doc.fullscreenElement||doc.webkitFullscreenElement;
    const supported=()=>!!(doc.documentElement.requestFullscreen||doc.documentElement.webkitRequestFullscreen);
    function notify(){session?.frame.contentWindow?.postMessage({type:'slide-studio:host',action:'fullscreen-state',available:supported(),active:!!currentFullscreen()},view.location.origin);}
    async function fullscreen(action='enter'){
      const current=session;if(!current)return;
      const active=currentFullscreen();
      if(action==='leave'||action==='toggle'&&active){
        if(!active)return;
        current.deliberate=true;current.native=false;
        try{await (doc.exitFullscreen||doc.webkitExitFullscreen).call(doc);}catch{current.deliberate=false;current.native=!!currentFullscreen();}
      }else if(!active){
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
      button.focus();onReturn(current.slideId);
    }
    function end(){if(!session)return;if(session.ready)session.frame.contentWindow?.postMessage({type:'slide-studio:host',action:'end'},view.location.origin);else void close();}
    function changed(){
      if(!session)return;
      if(currentFullscreen()===session.layer)session.native=true;
      else if(session.deliberate)session.deliberate=false;
      else if(session.native){session.native=false;end();}
      notify();
    }
    /** Request fullscreen before navigation or awaiting any asynchronous preparation. */
    function open(event){
      if(event.button>0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
      const href=button.getAttribute('href');if(!href)return;
      event.preventDefault();if(session)return;
      const url=new URL(href,view.location.href);if(url.origin!==view.location.origin)return;
      const layer=doc.createElement('div'),frame=doc.createElement('iframe');
      layer.className='workspace-presentation';frame.title='Presentation';frame.allow='fullscreen; window-management';frame.allowFullscreen=true;
      const siblings=[...doc.body.children].map(element=>[element,element.inert]);
      for(const [element] of siblings)element.inert=true;
      layer.append(frame);doc.body.append(layer);doc.body.classList.add('is-presenting');
      session={layer,frame,siblings,slideId:decodeURIComponent(url.hash.slice(1)),native:false,deliberate:false,ready:false};
      void fullscreen();
      url.searchParams.set('workspace','1');frame.src=url.href;
      frame.addEventListener('load',()=>{if(session?.frame!==frame)return;try{if(frame.contentWindow.location.pathname==='/'&&new URL(frame.contentWindow.location.href).searchParams.has('deck')){void close();return;}}catch{}frame.focus();notify();});
    }
    view.addEventListener('message',event=>{
      if(!session||event.source!==session.frame.contentWindow||event.origin!==view.location.origin||event.data?.type!=='slide-studio:presentation')return;
      const data=event.data;
      if(typeof data.slide_id==='string')session.slideId=data.slide_id;
      if(data.action==='end')void close();
      else if(data.action==='ready'){session.ready=true;notify();}
      else if(data.action==='fullscreen'&&['enter','leave','toggle'].includes(data.mode))void fullscreen(data.mode);
    });
    doc.addEventListener('fullscreenchange',changed);doc.addEventListener('webkitfullscreenchange',changed);
    button.addEventListener('click',open);
    return {isOpen:()=>!!session};
  }
  const api={create};if(typeof module!=='undefined')module.exports=api;else root.SlidePresentationLauncher=api;
})(typeof window==='undefined'?{}:window);
