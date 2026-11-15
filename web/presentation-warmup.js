/* Warm only small, passive runtime files; never open a second slideshow. */
((root)=>{
  'use strict';
  const QUIET_MS=8000,MAX_BYTES=128*1024,MAX_REQUESTS=3;
  function create({doc=document,view=window,canWarm=()=>true}={}){
    let base='',timer=null,idle=null,active=null,attempts=0;
    const tried=new Set();
    const supported=!!(view.requestIdleCallback&&view.cancelIdleCallback&&view.fetch&&view.AbortController);
    function allowed(){
      const device=view.navigator||{},connection=device.connection;
      return supported&&doc.visibilityState!=='hidden'&&doc.hasFocus()&&device.onLine!==false
        &&!connection?.saveData&&!['slow-2g','2g','3g'].includes(connection?.effectiveType)
        &&!(device.deviceMemory>0&&device.deviceMemory<=2)&&!(device.hardwareConcurrency>0&&device.hardwareConcurrency<=2)
        &&!doc.activeElement?.matches('input,textarea,select,[contenteditable],iframe')&&canWarm();
    }
    function cancel(){
      if(timer!==null)view.clearTimeout(timer);timer=null;
      if(idle!==null)view.cancelIdleCallback(idle);idle=null;
      active?.abort();active=null;
    }
    function schedule(){
      if(!supported||!base||attempts>=MAX_REQUESTS||timer!==null||idle!==null||active||!allowed())return;
      timer=view.setTimeout(()=>{
        timer=null;
        if(!allowed())return;
        // No timeout: a busy main thread must never be forced to do speculative work.
        idle=view.requestIdleCallback(deadline=>{
          idle=null;
          if(!allowed())return;
          if(deadline.timeRemaining()<8){schedule();return;}
          void warm();
        });
      },QUIET_MS);
    }
    async function warm(){
      const url=['presentation.js','icons.js','slideshow.css'].map(name=>base+name).find(url=>!tried.has(url));
      if(!url)return;
      tried.add(url);attempts++;
      const controller=new view.AbortController();active=controller;
      try{
        const response=await view.fetch(url,{credentials:'same-origin',priority:'low',signal:controller.signal});
        const size=Number(response.headers.get('Content-Length'));
        if(!response.ok||!size||size>MAX_BYTES||/no-store/i.test(response.headers.get('Cache-Control')||'')){
          await response.body?.cancel();return;
        }
        // Consume bytes into the HTTP cache without parsing or executing JavaScript/CSS.
        await response.arrayBuffer();
      }catch{/* Speculative work must never interrupt editing or prevent Present. */}
      finally{if(active===controller){active=null;schedule();}}
    }
    function pause(){cancel();schedule();}
    if(supported){
      for(const event of ['pointerdown','keydown','wheel','input','focusin','focusout'])doc.addEventListener(event,pause,{capture:true,passive:true});
      doc.addEventListener('visibilitychange',pause);
      view.addEventListener('blur',cancel);view.addEventListener('focus',schedule);
      view.addEventListener('pagehide',cancel);
      view.addEventListener('offline',cancel);view.addEventListener('online',schedule);
      view.navigator?.connection?.addEventListener?.('change',pause);
    }
    return {cancel,update(href){
      let next='';
      try{const url=new URL(href,view.location.href);if(href&&url.origin===view.location.origin&&/\/decks\/[^/]+\/index\.html$/.test(url.pathname))next=new URL('.',url).href;}catch{}
      if(next===base){schedule();return;}
      cancel();base=next;schedule();
    }};
  }
  const api={create};if(typeof module!=='undefined')module.exports=api;else root.SlidePresentationWarmup=api;
})(typeof window==='undefined'?{}:window);
