/* This isolated shell never sends queued private content to the network. */
(()=>{
  'use strict';
  const $=id=>document.getElementById(id),INDEX='artifact-studio-offline-index-v1',ACTIVE='artifact-studio-offline-active-v1';
  let entries=[],store=null,record=null,selection=0,conflict=null,syncing=false,deliveryController=null;
  try{const active=localStorage.getItem(ACTIVE);entries=JSON.parse(localStorage.getItem(INDEX)||'[]').filter(item=>item.scope===active&&/^[a-f0-9]{64}$/.test(item.scope)&&typeof item.deck==='string'&&/^\/(?:workspaces\/[a-f0-9]{24}\/)?api\/session$/.test(item.session));}catch{}
  function message(text){$('status').textContent=text;}
  function forget(){selection++;deliveryController?.abort();store?.close();store=null;entries=[];record=null;$('source').disabled=true;$('comment').disabled=true;$('save-edit').disabled=true;$('save-comment').disabled=true;$('decks').replaceChildren();$('slides').replaceChildren();$('source').value='';$('comment').value='';$('preview').srcdoc='';$('conflict').hidden=true;conflict=null;message('Saved data cleared.');}
  const purgeChannel=new BroadcastChannel('artifact-studio-offline-purge-v1');
  purgeChannel.onmessage=event=>{if(event.data==='purge')forget();};
  window.addEventListener('storage',event=>{if(event.key==='artifact-studio-offline-generation-v1'||event.key?.startsWith('artifact-studio-offline-blocked-v1:')&&event.newValue)forget();});
  function option(value,label){const item=document.createElement('option');item.value=value;item.textContent=label;return item;}
  function selectedSlide(){return record?.snapshot.slides.find(item=>item.id===$('slides').value);}
  function slide(){const item=selectedSlide();$('source').value=item?.html||'';$('comment').value='';$('preview').srcdoc='<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'+(item?.html||'');}
  async function select(){
    const selected=++selection;store?.close();store=null;record=null;$('source').value='';$('comment').value='';$('preview').srcdoc='';$('source').disabled=true;$('comment').disabled=true;$('save-edit').disabled=true;$('save-comment').disabled=true;const generation=localStorage.getItem('artifact-studio-offline-generation-v1');const entry=entries[Number($('decks').value)];
    if(!entry){message('No saved presentations on this device.');return;}
    const blocked='artifact-studio-offline-blocked-v1:'+entry.scope+':'+encodeURIComponent(entry.deck),identityBlocked='artifact-studio-offline-blocked-v1:'+entry.scope+':*';
    let priorBlock=localStorage.getItem(blocked),priorIdentityBlock=localStorage.getItem(identityBlocked);
    const checkBlocks=()=>{if(selection!==selected||localStorage.getItem(blocked)!==priorBlock||localStorage.getItem(identityBlocked)!==priorIdentityBlock)throw Error('Access changed. Reconnect and verify again.');};
    try{
      let response;try{response=await fetch(entry.session,{cache:'no-store',signal:AbortSignal.timeout(5000)});}catch{
        if(navigator.onLine)throw Error('Cannot verify the current session.');
      }
      checkBlocks();
      if(response){
        if(!response.ok||response.redirected||!(response.headers.get('content-type')||'').includes('application/json')){localStorage.setItem(identityBlocked,crypto.randomUUID());throw Error('Sign in to open saved presentations.');}
        const session=await response.json();checkBlocks();
        if(!/^[a-f0-9]{64}$/.test(session.cache_scope||''))throw Error('Offline access is unavailable for this session.');
        if(session.cache_scope!==entry.scope){await OfflineClient.clear();forget();throw Error('Saved data was cleared after the account changed.');}
        const deckPath=entry.session.replace(/api\/session$/,'api/decks/'+encodeURIComponent(entry.deck));
        const access=await fetch(deckPath,{cache:'no-store',signal:AbortSignal.timeout(5000)});checkBlocks();
        if(!access.ok||access.redirected){if(access.status===401||access.redirected)localStorage.setItem(identityBlocked,crypto.randomUUID());else if(access.status>=400&&access.status<500)localStorage.setItem(blocked,crypto.randomUUID());throw Error('This presentation is unavailable. Your drafts remain on this device.');}
        localStorage.removeItem(identityBlocked);localStorage.removeItem(blocked);priorIdentityBlock=null;priorBlock=null;
      }else if(localStorage.getItem(identityBlocked)||localStorage.getItem(blocked))throw Error('Reconnect and verify access to open this presentation. Your drafts remain on this device.');
      if(localStorage.getItem(ACTIVE)!==entry.scope||localStorage.getItem('artifact-studio-offline-generation-v1')!==generation)throw Error('The saved session changed.');
      store=OfflineStore.create({enabled:true,scope:entry.scope});record=await store.read(entry.deck);checkBlocks();if(localStorage.getItem('artifact-studio-offline-generation-v1')!==generation){forget();throw Error('The saved session changed.');}if(!record)throw Error('Saved presentation unavailable.');if(!$('decks').options.length)entries.forEach((item,index)=>$('decks').append(option(String(index),item.title||'Saved presentation')));$('slides').replaceChildren(...record.snapshot.slides.map(item=>option(item.id,item.title)));slide();$('source').disabled=false;$('comment').disabled=false;$('save-edit').disabled=false;$('save-comment').disabled=false;message('Available on this device.');}catch(error){message(error.message);}
  }
  async function queue(kind){const item=selectedSlide(),body=$(kind==='edit'?'source':'comment').value;if(!item||!body.trim()){message('No slide selected.');return;}message('Saving…');try{await store.queue(record.snapshot.id,{id:crypto.randomUUID(),slide_id:item.id,anchor:item.content_hash,expected_hash:item.expected_hash,receipt:item.receipt,kind,body});message('Saved on this device.');if(kind==='comment')$('comment').value='';}catch(error){message(error.message);}}
  async function deliver(){
    if(syncing||!record||!store)return;syncing=true;$('reconnect').disabled=true;
    const ownSelection=selection,generation=localStorage.getItem('artifact-studio-offline-generation-v1');
    const controller=new AbortController();deliveryController=controller;
    const check=()=>{if(ownSelection!==selection||generation!==localStorage.getItem('artifact-studio-offline-generation-v1'))throw Error('Saved session changed.');};
    const signal=timeout=>AbortSignal.any([controller.signal,AbortSignal.timeout(timeout)]);
    try{
      const entry=entries[Number($('decks').value)],deck=record.snapshot.id,scope=entry.scope;
      const endpoint='/api/workspaces/'+encodeURIComponent(record.snapshot.workspace)+'/artifacts/'+encodeURIComponent(deck);
      while(true){
        const current=await store.read(deck),item=current?.outbox[0];check();if(!item){message('All saved changes have been delivered.');break;}
        const sessionResponse=await fetch(entry.session,{cache:'no-store',signal:signal(5000)});check();
        if(!sessionResponse.ok||sessionResponse.redirected){OfflineClient.blockIdentity();forget();throw Error('Sign in again. Your drafts remain on this device.');}
        const session=await sessionResponse.json();check();if(session.cache_scope!==scope){await OfflineClient.bind(session.cache_scope);forget();throw Error('The signed-in account changed.');}
        const access=await fetch(entry.session.replace(/api\/session$/,'api/decks/'+encodeURIComponent(deck)),{cache:'no-store',signal:signal(5000)});check();
        if(!access.ok||access.redirected){if(access.status===401||access.redirected)OfflineClient.blockIdentity();else if(access.status>=400&&access.status<500)OfflineClient.blockDeck(deck);forget();throw Error('Access changed. Your drafts remain on this device.');}
        if(!item.receipt)throw Error('This draft needs an original receipt and remains on this device.');
        const body=item.kind==='edit'?{receipt:item.receipt,request_id:item.id,html:item.body,overwrite_expected:item.overwrite_expected||''}:{receipt:item.receipt,request_id:item.id,message:item.body,channel:'shared'};
        const response=await fetch(endpoint+(item.kind==='edit'?'/offline-edits':'/offline-comments'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:signal(15000)});check();
        if(!response.ok||response.redirected){if(response.status===401||response.redirected)OfflineClient.blockIdentity();else if(response.status===403||response.status===404)OfflineClient.blockDeck(deck);if(response.status===401||response.status===403||response.status===404||response.redirected)forget();throw Error('Delivery paused. Your drafts remain on this device.');}
        const result=await response.json();check();
        if(result.status==='conflict'){
          conflict={id:item.id,expected:result.current_expected_hash};$('conflict-base').textContent=result.base;$('conflict-current').textContent=result.current;$('conflict-draft').textContent=result.draft;$('conflict').hidden=false;message('Review the conflicting versions.');break;
        }
        await store.update(deck,item.id,null);
      }
    }catch(error){message(error.message||'Delivery paused. Your drafts remain on this device.');}
    finally{deliveryController=null;syncing=false;$('reconnect').disabled=false;}
  }
  $('reconnect').addEventListener('click',deliver);
  $('use-current').addEventListener('click',async()=>{if(!conflict||!record)return;try{await store.update(record.snapshot.id,conflict.id,null);conflict=null;$('conflict').hidden=true;void deliver();}catch(error){message(error.message);}});
  $('use-draft').addEventListener('click',async()=>{if(!conflict||!record)return;try{await store.update(record.snapshot.id,conflict.id,conflict.expected);conflict=null;$('conflict').hidden=true;void deliver();}catch(error){message(error.message);}});
  $('decks').addEventListener('change',select);$('slides').addEventListener('change',slide);
  $('save-edit').addEventListener('click',()=>queue('edit'));$('save-comment').addEventListener('click',()=>queue('comment'));
  $('clear').addEventListener('click',async()=>{try{await OfflineClient.clear();forget();}catch(error){message(error.message);}});
  void select();
})();
