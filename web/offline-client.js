/* Account binding and opt-in save orchestration for the experimental offline shell. */
((root)=>{
  'use strict';
  const INDEX='artifact-studio-offline-index-v1',ACTIVE='artifact-studio-offline-active-v1',GEN='artifact-studio-offline-generation-v1';
  const valid=scope=>typeof scope==='string'&&/^[a-f0-9]{64}$/.test(scope);
  function block(deck){try{const scope=localStorage.getItem(ACTIVE);if(valid(scope))localStorage.setItem('artifact-studio-offline-blocked-v1:'+scope+':'+(deck?encodeURIComponent(deck):'*'),crypto.randomUUID());}catch{}}
  function generation(){let value=localStorage.getItem(GEN);if(!value){value=crypto.randomUUID();localStorage.setItem(GEN,value);}return value;}
  function check(value){if(localStorage.getItem(GEN)!==value)throw Error('Saved data was cleared.');}
  async function clear(){
    localStorage.setItem(GEN,crypto.randomUUID());
    const scope=localStorage.getItem(ACTIVE);
    const store=OfflineStore.create({enabled:true,scope:valid(scope)?scope:'0'.repeat(64)});
    await store.purge();
    localStorage.removeItem(INDEX);localStorage.removeItem(ACTIVE);
    for(const key of Object.keys(localStorage))if(key.startsWith('artifact-studio-offline-blocked-v1:'))localStorage.removeItem(key);
    for(const key of await caches.keys())if(key.startsWith('artifact-studio-offline-shell-'))await caches.delete(key);
  }
  async function bind(scope){
    if(!valid(scope))return;
    const previous=localStorage.getItem(ACTIVE);
    if(previous&&previous!==scope)await clear();
    localStorage.setItem(ACTIVE,scope);
  }
  function activation(registration){
    if(registration.active)return Promise.resolve();
    const worker=registration.installing||registration.waiting;
    if(!worker)return Promise.reject(Error('Offline shell is unavailable.'));
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{worker.removeEventListener('statechange',changed);reject(Error('Offline shell did not become ready.'));},15000);
      function changed(){if(worker.state==='activated'||worker.state==='redundant'){clearTimeout(timer);worker.removeEventListener('statechange',changed);if(worker.state==='activated')resolve();else reject(Error('Offline shell is unavailable.'));}}
      worker.addEventListener('statechange',changed);changed();
    });
  }
  async function save(sessionPromise,deck,sessionPath,workspace){
    const started=generation();
    const session=await sessionPromise;check(started);
    if(!valid(session.cache_scope)||localStorage.getItem(ACTIVE)!==session.cache_scope)throw Error('Offline saving is unavailable for this session.');
    const registration=await navigator.serviceWorker.register('/web/offline-worker.js',{scope:'/web/'});check(started);
    await activation(registration);check(started);
    if(!/^(default|[a-f0-9]{24})$/.test(workspace))throw Error('Invalid workspace.');
    const endpoint='/api/workspaces/'+encodeURIComponent(workspace)+'/artifacts/'+encodeURIComponent(deck.id)+'/offline-snapshot';
    const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slides:deck.slides.map(item=>({slide_id:item.id,content_hash:item.content_hash,expected_hash:item.expected_hash,channel:'shared'}))})});check(started);
    if(!response.ok)throw Error('Could not save this presentation for offline use.');
    const receipts=await response.json();check(started);
    if(!receipts||deck.slides.some(item=>typeof receipts[item.id]!=='string'))throw Error('Offline save was incomplete.');
    const snapshot={id:deck.id,title:deck.title,workspace,slides:deck.slides.map(item=>({id:item.id,title:item.title,html:item.html,content_hash:item.content_hash,expected_hash:item.expected_hash,receipt:receipts[item.id]}))};
    const store=OfflineStore.create({enabled:true,scope:session.cache_scope});
    try{await store.save(deck.id,snapshot);}finally{store.close();}
    check(started);
    let entries=[];try{entries=JSON.parse(localStorage.getItem(INDEX)||'[]');if(!Array.isArray(entries))entries=[];}catch{}
    entries=entries.filter(item=>item.scope!==session.cache_scope||item.deck!==deck.id);
    entries.push({scope:session.cache_scope,deck:deck.id,title:deck.title,session:sessionPath});
    check(started);localStorage.setItem(INDEX,JSON.stringify(entries));
  }
  root.OfflineClient={bind,clear,save,blockIdentity:()=>block(null),blockDeck:block};
})(window);
