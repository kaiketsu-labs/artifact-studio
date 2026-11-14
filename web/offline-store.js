/* Experimental offline storage. Callers must explicitly opt in for each deck. */
((root)=>{
  'use strict';
  const NAME='artifact-studio-offline-v1',CHANNEL='artifact-studio-offline-purge-v1',EPOCH='@epoch';
  const validScope=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  const validId=value=>typeof value==='string'&&value.length>0&&value.length<=200;
  function create({enabled=false,scope,indexedDB=root.indexedDB,BroadcastChannel=root.BroadcastChannel,storage=root.localStorage}={}){
    if(!enabled||!validScope(scope)||!BroadcastChannel||!storage)throw new Error('Offline storage is not enabled for this identity.');
    const marker='artifact-studio-offline-generation-v1';
    if(!storage.getItem(marker))storage.setItem(marker,crypto.randomUUID());
    const initial=storage.getItem(marker);
    let generation=0,closed=false;
    const channel=new BroadcastChannel(CHANNEL),pending=new Set();
    const active=()=>!closed&&storage.getItem(marker)===initial;
    function fence(){generation++;for(const tx of pending){try{tx.abort();}catch{}}}
    if(channel)channel.onmessage=event=>{if(event.data==='purge'){fence();closed=true;}};
    function open(){return new Promise((resolve,reject)=>{
      const request=indexedDB.open(NAME,1);
      request.onupgradeneeded=()=>{const store=request.result.createObjectStore('decks',{keyPath:'key'});store.add({key:EPOCH,value:crypto.randomUUID()});};
      request.onerror=()=>reject(request.error);
      request.onsuccess=()=>resolve(request.result);
    });}
    const ready=(async()=>{
      const observed=generation,db=await open();
      if(!active()||generation!==observed){db.close();throw new Error('Offline storage was cleared.');}
      return new Promise((resolve,reject)=>{
        const tx=db.transaction('decks','readonly');pending.add(tx);const get=tx.objectStore('decks').get(EPOCH);
        tx.oncomplete=()=>{pending.delete(tx);db.close();resolve(get.result.value);};
        tx.onabort=tx.onerror=()=>{pending.delete(tx);db.close();reject(tx.error||new Error('Offline storage was cleared.'));};
      });
    })();
    async function transaction(mode,operation){
      const observed=generation,epoch=await ready,db=await open();
      if(!active()||generation!==observed){db.close();throw new Error('Offline storage was cleared.');}
      return new Promise((resolve,reject)=>{
        const tx=db.transaction('decks',mode);pending.add(tx);const store=tx.objectStore('decks'),check=store.get(EPOCH);let result;
        check.onsuccess=()=>{if(!active()||check.result?.value!==epoch){tx.abort();return;}try{result=operation(store,tx);}catch{tx.abort();}};
        tx.oncomplete=()=>{pending.delete(tx);db.close();resolve(result?.result);};
        tx.onabort=tx.onerror=()=>{pending.delete(tx);db.close();reject(tx.error||new Error('Offline operation could not be saved.'));};
      });
    }
    const key=deck=>{if(!validId(deck))throw new Error('Invalid deck ID.');return scope+':'+deck;};
    async function save(deck,snapshot){
      if(!snapshot||typeof snapshot!=='object'||Array.isArray(snapshot))throw new Error('Invalid snapshot.');
      const deckKey=key(deck);
      await transaction('readwrite',(store,tx)=>{const get=store.get(deckKey);get.onsuccess=()=>{if(get.result){tx.abort();return;}store.add({key:deckKey,snapshot:structuredClone(snapshot),outbox:[]});};return get;});
    }
    async function read(deck){return transaction('readonly',store=>store.get(key(deck)));}
    async function drafts(deck){
      const record=await read(deck);
      return (record?.outbox||[]).filter(item=>['edit','comment'].includes(item.kind)&&typeof item.body==='string').map(item=>({kind:item.kind,body:item.body}));
    }
    async function queue(deck,operation){
      if(!operation||!validId(operation.id)||!validId(operation.slide_id)||!validId(operation.anchor)||!['edit','comment'].includes(operation.kind)||typeof operation.body!=='string')throw new Error('Invalid offline operation.');
      // Never store caller objects, which may contain authentication material.
      const entry={id:operation.id,slide_id:operation.slide_id,anchor:operation.anchor,kind:operation.kind,body:operation.body};
      if(validId(operation.expected_hash))entry.expected_hash=operation.expected_hash;
      if(validId(operation.receipt))entry.receipt=operation.receipt;
      await transaction('readwrite',(store,tx)=>{const get=store.get(key(deck));get.onsuccess=()=>{const record=get.result;if(!record){tx.abort();return;}const prior=record.outbox.find(item=>item.id===entry.id);if(prior&&JSON.stringify(prior)!==JSON.stringify(entry)){tx.abort();return;}if(!prior)record.outbox.push(entry);store.put(record);};return get;});
    }
    async function refresh(deck,snapshot){
      await transaction('readwrite',(store,tx)=>{const get=store.get(key(deck));get.onsuccess=()=>{
        const record=get.result;if(!record||record.outbox.length){tx.abort();return;}
        record.snapshot=structuredClone(snapshot);store.put(record);
      };return get;});
    }
    async function update(deck,id,choice,confirmed=null){
      await transaction('readwrite',(store,tx)=>{const get=store.get(key(deck));get.onsuccess=()=>{
        const record=get.result,item=record?.outbox.find(value=>value.id===id);
        if(!item){tx.abort();return;}
        if(choice===null){
          if(confirmed&&item.kind==='edit'){
            const slide=record.snapshot.slides.find(value=>value.id===item.slide_id);
            if(slide){slide.html=typeof confirmed.html==='string'?confirmed.html:item.body;slide.expected_hash=confirmed.expected_hash;slide.content_hash=confirmed.content_hash;slide.receipt=null;record.snapshot.needs_refresh=true;}
          }
          record.outbox=record.outbox.filter(value=>value.id!==id);
        }
        else if(validId(choice))item.overwrite_expected=choice;else{tx.abort();return;}
        store.put(record);
      };return get;});
    }
    async function purge(){
      await ready;storage.setItem(marker,crypto.randomUUID());fence();channel.postMessage('purge');
      const db=await open();
      try{await new Promise((resolve,reject)=>{const tx=db.transaction('decks','readwrite'),store=tx.objectStore('decks');store.clear();store.put({key:EPOCH,value:crypto.randomUUID()});tx.oncomplete=resolve;tx.onabort=tx.onerror=()=>reject(tx.error||new Error('Offline data could not be cleared.'));});}
      finally{db.close();closed=true;channel?.close();}
    }
    function close(){fence();closed=true;channel?.close();}
    return {save,read,drafts,queue,update,refresh,purge,close};
  }
  if(typeof module!=='undefined')module.exports={create};else root.OfflineStore={create};
})(typeof window==='undefined'?globalThis:window);
