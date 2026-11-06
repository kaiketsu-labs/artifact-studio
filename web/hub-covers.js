/* Visible-first cover requests; source files and the full artifact viewer stay untouched. */
((root)=>{
  'use strict';
  const MiB=1024*1024;
  const coverURL=value=>typeof value==='string' && /^\/api\/hub\/previews\/[^/?#]+\?v=[^#]+$/.test(value)?value:'';
  function create({view=root,document=view.document,concurrency=2,maxEntries=32,maxBytes=24*MiB,maxItemBytes=8*MiB,retryLimit=6,onChanged=()=>{},now=()=>Date.now()}={}){
    const cache=new Map(),failures=new Map(),active=new Map(),changed=new Set();
    let records=new Map(),observed=new Map(),observer=null,container=null,bytes=0,wakeTimer=null,paused=false,destroyed=false,blockedUntil=0;
    const setTimer=(callback,delay)=>view.setTimeout(callback,delay),clearTimer=id=>view.clearTimeout(id);
    const connected=record=>record.image.isConnected!==false;
    const rendererBlocked=record=>blockedUntil>now() && record.renderer && !record.cached;
    const desired=key=>!paused && !destroyed && [...records.values()].some(record=>(record.key===key || record.fallback===key && record.loadedKey!==record.key) && record.visible && connected(record));
    function visible(image,margin=120){const box=(image.closest('.hub-cover') || image).getBoundingClientRect();return box.width>0 && box.height>0 && box.bottom>=-margin && box.top<=view.innerHeight+margin && box.right>=0 && box.left<=view.innerWidth;}
    function distance(record){const box=(record.image.closest('.hub-cover') || record.image).getBoundingClientRect();return Math.max(0,-box.bottom,box.top-view.innerHeight);}
    function remember(key,value){failures.delete(key);failures.set(key,value);while(failures.size>256)failures.delete(failures.keys().next().value);}
    function touch(key){const entry=cache.get(key);if(entry){cache.delete(key);cache.set(key,entry);}return entry;}
    function drop(key){const entry=cache.get(key);if(entry){bytes-=entry.size;view.URL.revokeObjectURL(entry.url);cache.delete(key);}}
    function trim(){
      while(cache.size>maxEntries || bytes>maxBytes){
        // Prefer a cover outside the viewport; visible decoded images remain painted
        // after their object URL is released and disappear only when scrolled away.
        const key=[...cache.keys()].find(key=>!desired(key)) || cache.keys().next().value;
        drop(key);
      }
    }
    function paint(record){
      if(!connected(record))return;
      const image=record.image,card=image.closest('.hub-card'),button=card?.querySelector('[data-cover-retry]'),label=card?.querySelector('[data-cover-status]'),open=card?.querySelector('.hub-card-open');
      const entry=cache.get(record.key),fallback=cache.get(record.fallback),failure=failures.get(record.key);
      if(record.visible && entry){image.src=entry.url;record.loadedKey=record.key;}
      else if(record.visible && record.loadedKey!==record.key && fallback){image.src=fallback.url;record.loadedKey=record.fallback;}
      const painted=!!record.loadedKey,current=record.loadedKey===record.key;
      let state=current?'ready':'pending';
      if(!current){
        if(failure && !failure.transient || rendererBlocked(record))state='unavailable';
        else if(painted)state='updating';
        else if(active.has(record.key) || failure?.transient)state='loading';
      }
      image.hidden=!painted || !record.visible;
      if(!record.visible){image.removeAttribute('src');record.loadedKey=null;}
      if(card)card.dataset.previewState=state;
      if(label){label.hidden=!painted || current;label.textContent=state==='unavailable'?'Earlier preview':'Updating preview';}
      if(open){
        const description=state==='unavailable'?(painted?'Showing an earlier saved preview.':'Preview temporarily unavailable.')+' You can still open this artifact.':painted&&!current?'Showing an earlier saved preview while the current version updates.':'';
        description?open.setAttribute('aria-description',description):open.removeAttribute('aria-description');
      }
      if(button){
        button.hidden=state!=='unavailable';
        button.disabled=rendererBlocked(record) || (failure?.retryAt || 0)>now();
        button.title=button.disabled?'Preview temporarily unavailable. You can still open this artifact.':'Retry preview';
      }
    }
    function paintAll(){for(const record of records.values())paint(record);}
    function cancelUnwanted(){for(const [key,job] of active)if(!desired(key))job.controller.abort();}
    function nextCandidates(){
      const unique=new Map();
      for(const record of records.values()){
        if(!record.visible || !connected(record))continue;
        for(const [key,isFallback] of [[record.fallback,true],[record.key,false]]){
          if(!key || record.loadedKey===key || isFallback && record.loadedKey===record.key || cache.has(key) || active.has(key))continue;
          const failure=failures.get(key);
          if(failure && (!failure.transient || failure.retryAt>now()))continue;
          if(!isFallback && rendererBlocked(record))continue;
          const candidate={key,record,priority:distance(record)+(isFallback?-1:0)},existing=unique.get(key);
          if(!existing || candidate.priority<existing.priority)unique.set(key,candidate);
        }
      }
      return [...unique.values()].sort((a,b)=>a.priority-b.priority || a.record.order-b.record.order);
    }
    function schedule(){
      if(wakeTimer!==null){clearTimer(wakeTimer);wakeTimer=null;}
      if(paused || destroyed || !records.size)return;
      const deadlines=[];
      if(blockedUntil>now())deadlines.push(blockedUntil);
      for(const [key,failure] of failures)if(desired(key) && failure.retryAt>now())deadlines.push(failure.retryAt);
      if(deadlines.length)wakeTimer=setTimer(()=>{wakeTimer=null;paintAll();pump();},Math.max(10,Math.min(...deadlines)-now()));
    }
    function pump(){
      if(paused || destroyed)return;
      cancelUnwanted();
      for(const candidate of nextCandidates()){
        if(active.size>=concurrency)break;
        void load(candidate.key);
      }
      paintAll();schedule();
    }
    function retryAfter(response,fallback){
      const raw=response.headers.get('Retry-After'),seconds=Number(raw);
      const delay=raw && Number.isFinite(seconds)?seconds*1000:raw?Date.parse(raw)-now():fallback;
      return Math.max(250,Math.min(Number.isFinite(delay)?delay:fallback,300000));
    }
    async function imageBlob(response){
      const type=(response.headers.get('Content-Type') || '').split(';')[0];
      if(!['image/png','image/jpeg','image/webp','image/svg+xml'].includes(type))throw Error('Invalid cover format');
      if(Number(response.headers.get('Content-Length'))>maxItemBytes)throw Error('Cover too large');
      if(!response.body?.getReader){const blob=await response.blob();if(blob.size>maxItemBytes)throw Error('Cover too large');return blob;}
      const reader=response.body.getReader(),chunks=[];let total=0;
      try{
        while(true){const {value,done}=await reader.read();if(done)break;total+=value.byteLength;if(total>maxItemBytes){await reader.cancel();throw Error('Cover too large');}chunks.push(value);}
        return new view.Blob(chunks,{type});
      }finally{reader.releaseLock();}
    }
    async function decode(url,signal){
      if(!view.Image)return;
      const image=new view.Image();image.src=url;
      await new Promise((resolve,reject)=>{
        const finish=(error)=>{signal.removeEventListener('abort',abort);error?reject(error):resolve();};
        const abort=()=>{image.src='';finish(Error('Cover request cancelled'));};
        if(signal.aborted){abort();return;}
        signal.addEventListener('abort',abort,{once:true});
        image.decode().then(()=>finish(),finish);
      });
    }
    async function load(key){
      const controller=new view.AbortController(),job={controller,timedOut:false};active.set(key,job);
      const timeout=setTimer(()=>{job.timedOut=true;controller.abort();},45000);let objectURL=null;
      try{
        const response=await view.fetch(key,{signal:controller.signal,credentials:'same-origin',headers:{Accept:'image/png'}});
        if(controller.signal.aborted)return;
        if(response.status===202 || !response.ok){
          let code='';try{code=(await response.json()).code || '';}catch{}
          if(controller.signal.aborted)return;
          const attempts=(failures.get(key)?.attempts || 0)+1;
          const pendingSince=failures.get(key)?.pendingSince ?? now();
          if(response.status===202 && ((code==='cover_pending' && now()-pendingSince<120000) || (code==='cover_busy' && attempts<=retryLimit))){
            const delay=Math.max(retryAfter(response,1000),Math.min(code==='cover_pending'?5000:15000,1000*2**(attempts-1)));
            remember(key,{transient:true,retryAt:now()+delay,attempts,pendingSince});
          }else{
            const delay=response.status>=500?retryAfter(response,30000):0;
            remember(key,{transient:false,retryAt:now()+delay,attempts,code});
            if(code==='renderer_unavailable')blockedUntil=Math.max(blockedUntil,now()+retryAfter(response,30000));
            if(code==='cover_changed' && !changed.has(key) && [...records.values()].some(record=>record.key===key)){changed.add(key);while(changed.size>256)changed.delete(changed.values().next().value);void Promise.resolve().then(onChanged).catch(()=>{});}
          }
          return;
        }
        const blob=await imageBlob(response);
        if(controller.signal.aborted || !desired(key))return;
        objectURL=view.URL.createObjectURL(blob);
        await decode(objectURL,controller.signal);
        if(controller.signal.aborted || !desired(key))return;
        drop(key);cache.set(key,{url:objectURL,size:blob.size});bytes+=blob.size;objectURL=null;failures.delete(key);
        for(const record of records.values())if(record.key===key || record.fallback===key)paint(record);
        trim();
      }catch{
        if(!controller.signal.aborted || job.timedOut)remember(key,{transient:false,retryAt:now()+30000,attempts:1});
      }finally{
        if(objectURL)view.URL.revokeObjectURL(objectURL);
        clearTimer(timeout);
        if(active.get(key)===job)active.delete(key);
        pump();
      }
    }
    function scan(){for(const record of records.values())record.visible=visible(record.image);pump();}
    function mount(next){
      observer?.disconnect();observer=null;container=next;
      for(const record of records.values())if(!next?.contains(record.image)){record.image.removeAttribute('src');record.image.hidden=true;}
      records=new Map();observed=new Map();
      for(const [order,image] of [...(next?.querySelectorAll('[data-cover-url]') || [])].entries()){
        const key=coverURL(image.dataset.coverUrl);if(!key)continue;
        const record={image,key,fallback:coverURL(image.dataset.coverFallback),cached:image.dataset.coverCached==='true',renderer:image.dataset.coverRenderer!=='false',order,visible:visible(image),loadedKey:null};records.set(image,record);observed.set(image.closest('.hub-cover') || image,record);if(record.visible)touch(key);if(record.cached && failures.get(key)?.code==='renderer_unavailable')failures.delete(key);
      }
      paused=!!document?.hidden;
      if(view.IntersectionObserver){
        observer=new view.IntersectionObserver(entries=>{
          for(const entry of entries){const record=observed.get(entry.target);if(record){record.visible=entry.isIntersecting;if(record.visible)touch(record.key);paint(record);}}
          pump();
        },{root:null,rootMargin:'120px 0px',threshold:0});
        for(const target of observed.keys())observer.observe(target);
      }
      pump();
    }
    function clear(){observer?.disconnect();observer=null;container=null;records.clear();observed.clear();for(const job of active.values())job.controller.abort();if(wakeTimer!==null)clearTimer(wakeTimer);wakeTimer=null;}
    function pause(){paused=true;for(const job of active.values())job.controller.abort();if(wakeTimer!==null)clearTimer(wakeTimer);wakeTimer=null;}
    function resume(){paused=false;if(container)scan();}
    function retry(key){
      const failure=failures.get(key);
      if([...records.values()].some(record=>record.key===key && rendererBlocked(record)) || (failure?.retryAt || 0)>now() || active.has(key))return;
      const focused=document?.activeElement;
      if(focused?.dataset?.coverRetry===key)focused.closest('.hub-card')?.querySelector('.hub-card-open')?.focus({preventScroll:true});
      failures.delete(key);pump();
    }
    function imageFailed(image){
      const record=records.get(image);if(!record || !record.loadedKey)return;
      const failedKey=record.loadedKey;record.loadedKey=null;drop(failedKey);remember(failedKey,{transient:false,retryAt:now()+30000,attempts:1});paintAll();schedule();
    }
    const visibility=()=>document.hidden?pause():resume(),fallbackScan=()=>{if(!view.IntersectionObserver && !paused)scan();};
    document?.addEventListener('visibilitychange',visibility);
    view.addEventListener?.('pagehide',pause);view.addEventListener?.('pageshow',resume);
    view.addEventListener?.('scroll',fallbackScan,{passive:true});view.addEventListener?.('resize',fallbackScan,{passive:true});
    return {mount,clear,pause,resume,retry,imageFailed,destroy(){
      destroyed=true;clear();for(const key of [...cache.keys()])drop(key);failures.clear();changed.clear();
      document?.removeEventListener('visibilitychange',visibility);
      view.removeEventListener?.('pagehide',pause);view.removeEventListener?.('pageshow',resume);
      view.removeEventListener?.('scroll',fallbackScan);view.removeEventListener?.('resize',fallbackScan);
    }};
  }
  const api={create,coverURL};
  if(typeof module!=='undefined' && module.exports)module.exports=api;else root.HubCovers=api;
})(typeof window==='undefined'?{}:window);
