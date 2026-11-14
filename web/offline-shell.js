/* This isolated shell never sends queued private content to the network. */
(()=>{
  'use strict';
  const $=id=>document.getElementById(id),INDEX='artifact-studio-offline-index-v1',ACTIVE='artifact-studio-offline-active-v1';
  let entries=[],store=null,record=null,selection=0,conflict=null,syncing=false,deliveryController=null;
  try{const active=localStorage.getItem(ACTIVE);entries=JSON.parse(localStorage.getItem(INDEX)||'[]').filter(item=>item.scope===active&&/^[a-f0-9]{64}$/.test(item.scope)&&typeof item.deck==='string'&&/^\/(?:workspaces\/[a-f0-9]{24}\/)?api\/session$/.test(item.session));}catch{}
  function message(text){$('status').textContent=text;}
  function clearFrame(id){const old=$(id);if(!old)return;const next=old.cloneNode(false);next.removeAttribute('srcdoc');old.replaceWith(next);}
  function renderFrame(id,source){const old=$(id);if(!old)return;const next=old.cloneNode(false);next.srcdoc=previewDocument(source,old);old.replaceWith(next);}
  function closeRecovery(){$('recovery').hidden=true;$('recovery-list').replaceChildren();}
  function recoveryEntries(){
    const scope=localStorage.getItem(ACTIVE);if(!/^[a-f0-9]{64}$/.test(scope||''))return [];
    try{return JSON.parse(localStorage.getItem(INDEX)||'[]').filter(item=>item.scope===scope&&typeof item.deck==='string'&&/^\/(?:workspaces\/[a-f0-9]{24}\/)?api\/session$/.test(item.session)&&(localStorage.getItem('artifact-studio-offline-blocked-v1:'+scope+':*')||localStorage.getItem('artifact-studio-offline-blocked-v1:'+scope+':'+encodeURIComponent(item.deck))));}catch{return [];}
  }
  function offerRecovery(){$('recover-drafts').hidden=!recoveryEntries().length;}
  async function recoverDrafts(){
    const own=selection,scope=localStorage.getItem(ACTIVE),generation=localStorage.getItem('artifact-studio-offline-generation-v1'),candidates=recoveryEntries();
    const check=()=>{if(selection!==own||scope!==localStorage.getItem(ACTIVE)||generation!==localStorage.getItem('artifact-studio-offline-generation-v1'))throw Error('The saved session changed.');};
    $('recover-drafts').disabled=true;closeRecovery();
    try{
      if(!candidates.length)throw Error('No blocked drafts are available.');
      const sessionPath=candidates[0].session;let response;
      try{response=await fetch(sessionPath,{cache:'no-store',signal:AbortSignal.timeout(5000)});}catch{if(navigator.onLine)throw Error('Cannot verify the current session.');}
      check();
      if(response&&response.ok&&!response.redirected&&(response.headers.get('content-type')||'').includes('application/json')){
        const session=await response.json();check();
        if(!/^[a-f0-9]{64}$/.test(session.cache_scope||''))throw Error('Cannot verify the current identity.');
        if(session.cache_scope!==scope){await OfflineClient.bind(session.cache_scope);forget();throw Error('Saved data was cleared after the account changed.');}
      }else if(response&&response.status!==401&&response.status!==403&&!response.redirected)throw Error('Cannot verify the current identity.');
      if(response&&(response.status===401||response.redirected))OfflineClient.blockIdentity();
      const reader=OfflineStore.create({enabled:true,scope});const drafts=[];
      try{for(const item of candidates){if(!recoveryEntries().some(value=>value.deck===item.deck))throw Error('Access changed.');drafts.push(...await reader.drafts(item.deck));check();}}finally{reader.close();}
      check();if(!recoveryEntries().length)throw Error('Access changed.');
      $('recovery-list').replaceChildren(...drafts.map((draft,index)=>{const label=document.createElement('label');label.textContent=(draft.kind==='edit'?'Edit':'Shared comment')+' '+(index+1);const body=document.createElement('textarea');body.readOnly=true;body.value=draft.body;body.rows=4;label.append(body);return label;}));
      $('recovery').hidden=false;message(drafts.length?'Your saved drafts are ready to copy.':'No saved drafts remain on this device.');
    }catch(error){closeRecovery();message(error.message);}finally{$('recover-drafts').disabled=false;}
  }
  function resetConflict(){conflict=null;$('conflict').hidden=true;for(const id of ['conflict-base','conflict-current','conflict-draft'])$(id).textContent='';for(const id of ['conflict-base-preview','conflict-current-preview','conflict-draft-preview'])clearFrame(id);}
  function forget(){selection++;closeRecovery();$('recover-drafts').hidden=true;deliveryController?.abort();store?.close();store=null;entries=[];record=null;$('source').disabled=true;$('comment').disabled=true;$('save-edit').disabled=true;$('save-comment').disabled=true;$('decks').replaceChildren();$('slides').replaceChildren();$('source').value='';$('comment').value='';clearFrame('preview');$('outbox').replaceChildren();resetConflict();message('Saved data cleared.');}
  const purgeChannel=new BroadcastChannel('artifact-studio-offline-purge-v1');
  purgeChannel.onmessage=event=>{if(event.data==='purge')forget();};
  window.addEventListener('storage',event=>{if(event.key==='artifact-studio-offline-generation-v1')forget();else if(event.key?.startsWith('artifact-studio-offline-blocked-v1:')&&event.newValue){forget();offerRecovery();}});
  function option(value,label){const item=document.createElement('option');item.value=value;item.textContent=label;return item;}
  function selectedSlide(){return record?.snapshot.slides.find(item=>item.id===$('slides').value);}
  function previewDocument(source,frame){
    let theme=record?.snapshot.theme||'',content=source||'';
    const assets=record?.snapshot.assets||{};
    for(const [path,data] of Object.entries(assets).sort((a,b)=>b[0].length-a[0].length)){
      if(typeof data!=='string'||!data.startsWith('data:'))continue;
      for(const name of ['./'+path,path]){theme=theme.split(name).join(data);content=content.split(name).join(data);}
    }
    const scale=Math.max(.01,(frame.clientWidth||800)/1600);
    const policy="default-src 'none'; img-src data:; media-src data:; font-src data:; style-src 'unsafe-inline'; form-action 'none'; base-uri 'none'";
    const safeTheme=theme.replace(/<\/style/gi,'<\\/style');
    return '<!doctype html><meta http-equiv="Content-Security-Policy" content="'+policy+'">'+
      '<style>html{color-scheme:light;background:#fff;color:#111}body{margin:0;overflow:hidden}'+
      '.offline-canvas{width:1600px;height:900px;font-size:48px;transform-origin:top left;transform:scale('+scale+');overflow:hidden}'+safeTheme+'</style><div class="offline-canvas">'+content+'</div>';
  }
  function preview(){renderFrame('preview',$('source').value);}
  function effective(item){return record?.outbox.filter(value=>value.kind==='edit'&&value.slide_id===item?.id).at(-1)?.body||item?.html||'';}
  function slide(){const item=selectedSlide();$('source').value=effective(item);$('comment').value='';const disabled=syncing||!item?.receipt;$('source').disabled=disabled;$('comment').disabled=disabled;$('save-edit').disabled=disabled;$('save-comment').disabled=disabled;preview();}
  function renderQueue(){
    $('outbox').replaceChildren(...(record?.outbox||[]).map(item=>{const row=document.createElement('li');const label=document.createElement('strong');label.textContent=(item.kind==='edit'?'Edit':'Shared comment')+' · '+(record.snapshot.slides.find(slide=>slide.id===item.slide_id)?.title||item.slide_id);const text=document.createElement('pre');text.textContent=item.body;const remove=document.createElement('button');remove.type='button';remove.textContent='Remove saved change';remove.disabled=syncing;remove.addEventListener('click',async()=>{const own=selection,generation=localStorage.getItem('artifact-studio-offline-generation-v1'),deck=record.snapshot.id,activeStore=store;try{await activeStore.update(deck,item.id,null);if(own!==selection||generation!==localStorage.getItem('artifact-studio-offline-generation-v1'))return;const updated=await activeStore.read(deck);if(own!==selection||generation!==localStorage.getItem('artifact-studio-offline-generation-v1'))return;record=updated;renderQueue();slide();}catch(error){message(error.message);}});row.append(label,text,remove);return row;}));
  }
  async function select(){
    const selected=++selection;deliveryController?.abort();resetConflict();store?.close();store=null;record=null;$('outbox').replaceChildren();$('source').value='';$('comment').value='';clearFrame('preview');$('source').disabled=true;$('comment').disabled=true;$('save-edit').disabled=true;$('save-comment').disabled=true;const generation=localStorage.getItem('artifact-studio-offline-generation-v1');const entry=entries[Number($('decks').value)];
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
        if(response.status===401||response.status===403||response.redirected||response.ok&&!(response.headers.get('content-type')||'').includes('application/json')){localStorage.setItem(identityBlocked,crypto.randomUUID());forget();offerRecovery();message('Sign in to open saved presentations.');return;}
        if(!response.ok)throw Error('Cannot verify the current session.');
        const session=await response.json();checkBlocks();
        if(!/^[a-f0-9]{64}$/.test(session.cache_scope||''))throw Error('Offline access is unavailable for this session.');
        if(session.cache_scope!==entry.scope){await OfflineClient.clear();forget();message('Saved data was cleared after the account changed.');return;}
        const deckPath=entry.session.replace(/api\/session$/,'api/decks/'+encodeURIComponent(entry.deck));
        const access=await fetch(deckPath,{cache:'no-store',signal:AbortSignal.timeout(5000)});checkBlocks();
        if(!access.ok||access.redirected){if(access.status===401||access.redirected)localStorage.setItem(identityBlocked,crypto.randomUUID());else if(access.status>=400&&access.status<500)localStorage.setItem(blocked,crypto.randomUUID());forget();offerRecovery();message('This presentation is unavailable. Your drafts remain on this device.');return;}
        localStorage.removeItem(identityBlocked);localStorage.removeItem(blocked);priorIdentityBlock=null;priorBlock=null;
      }else if(localStorage.getItem(identityBlocked)||localStorage.getItem(blocked))throw Error('Reconnect and verify access to open this presentation. Your drafts remain on this device.');
      if(localStorage.getItem(ACTIVE)!==entry.scope||localStorage.getItem('artifact-studio-offline-generation-v1')!==generation)throw Error('The saved session changed.');
      const activeStore=OfflineStore.create({enabled:true,scope:entry.scope});store=activeStore;const loaded=await activeStore.read(entry.deck);checkBlocks();if(localStorage.getItem('artifact-studio-offline-generation-v1')!==generation){forget();throw Error('The saved session changed.');}record=loaded;if(!record)throw Error('Saved presentation unavailable.');if(!$('decks').options.length)entries.forEach((item,index)=>$('decks').append(option(String(index),item.title||'Saved presentation')));$('slides').replaceChildren(...record.snapshot.slides.map(item=>option(item.id,item.title)));slide();renderQueue();message('Available on this device.');}catch(error){if(selected!==selection)return;offerRecovery();message(error.message);}
  }
  async function queue(kind){
    const item=selectedSlide(),body=$(kind==='edit'?'source':'comment').value,own=selection,generation=localStorage.getItem('artifact-studio-offline-generation-v1'),deck=record?.snapshot.id,activeStore=store;
    if(!item||!body.trim()||!deck){message('No slide selected.');return;}
    if(!item.receipt){message('Reconnect to refresh this slide before saving another change.');return;}
    message('Saving…');
    try{await activeStore.queue(deck,{id:crypto.randomUUID(),slide_id:item.id,anchor:item.content_hash,expected_hash:item.expected_hash,receipt:item.receipt,kind,body});if(own!==selection||generation!==localStorage.getItem('artifact-studio-offline-generation-v1'))return;const updated=await activeStore.read(deck);if(own!==selection||generation!==localStorage.getItem('artifact-studio-offline-generation-v1'))return;record=updated;renderQueue();message('Saved on this device.');if(kind==='comment')$('comment').value='';}catch(error){if(own===selection&&generation===localStorage.getItem('artifact-studio-offline-generation-v1'))message(error.message);}
  }
  async function deliver(){
    if(syncing||!record||!store)return;if($('source').value!==effective(selectedSlide())||$('comment').value.trim()){message('Save or clear the current edit and comment before reconnecting.');return;}syncing=true;$('source').disabled=true;$('comment').disabled=true;$('save-edit').disabled=true;$('save-comment').disabled=true;$('reconnect').disabled=true;renderQueue();
    const ownSelection=selection,generation=localStorage.getItem('artifact-studio-offline-generation-v1');
    const controller=new AbortController();deliveryController=controller;
    const check=()=>{if(ownSelection!==selection||generation!==localStorage.getItem('artifact-studio-offline-generation-v1'))throw Error('Saved session changed.');};
    const signal=timeout=>AbortSignal.any([controller.signal,AbortSignal.timeout(timeout)]);
    try{
      const entry=entries[Number($('decks').value)],deck=record.snapshot.id,scope=entry.scope,activeStore=store;
      const endpoint='/api/workspaces/'+encodeURIComponent(record.snapshot.workspace)+'/artifacts/'+encodeURIComponent(deck);
      while(true){
        const current=await activeStore.read(deck),item=current?.outbox[0];check();if(!item){
          const freshResponse=await fetch(entry.session.replace(/api\/session$/,'api/decks/'+encodeURIComponent(deck)),{cache:'no-store',signal:signal(5000)});check();
          if(!freshResponse.ok||freshResponse.redirected){if(freshResponse.status===401||freshResponse.redirected)OfflineClient.blockIdentity();else if(freshResponse.status===403||freshResponse.status===404)OfflineClient.blockDeck(deck);if(freshResponse.status===401||freshResponse.status===403||freshResponse.status===404||freshResponse.redirected){forget();offerRecovery();message('Changes delivered; reconnect to refresh the saved presentation.');return;}throw Error('Changes delivered; reconnect to refresh the saved presentation.');}
          const fresh=await freshResponse.json();check();
          await OfflineClient.save(Promise.resolve({cache_scope:scope}),fresh,entry.session,record.snapshot.workspace,{refresh:true});check();
          const updated=await activeStore.read(deck);check();record=updated;renderQueue();slide();message('All saved changes have been delivered.');break;
        }
        const sessionResponse=await fetch(entry.session,{cache:'no-store',signal:signal(5000)});check();
        if(sessionResponse.status===401||sessionResponse.status===403||sessionResponse.redirected||sessionResponse.ok&&!(sessionResponse.headers.get('content-type')||'').includes('application/json')){OfflineClient.blockIdentity();forget();offerRecovery();message('Sign in again. Your drafts remain on this device.');return;}
        if(!sessionResponse.ok)throw Error('Cannot verify the current session. Your drafts remain on this device.');
        const session=await sessionResponse.json();check();if(session.cache_scope!==scope){await OfflineClient.bind(session.cache_scope);forget();message('The signed-in account changed.');return;}
        const access=await fetch(entry.session.replace(/api\/session$/,'api/decks/'+encodeURIComponent(deck)),{cache:'no-store',signal:signal(5000)});check();
        if(!access.ok||access.redirected){if(access.status===401||access.redirected)OfflineClient.blockIdentity();else if(access.status>=400&&access.status<500)OfflineClient.blockDeck(deck);if(access.status>=400&&access.status<500||access.redirected){forget();offerRecovery();message('Access changed. Your drafts remain on this device.');return;}throw Error('Access could not be verified. Your drafts remain on this device.');}
        if(!item.receipt)throw Error('This draft needs an original receipt and remains on this device.');
        const body=item.kind==='edit'?{receipt:item.receipt,request_id:item.id,html:item.body,overwrite_expected:item.overwrite_expected||''}:{receipt:item.receipt,request_id:item.id,message:item.body,channel:'shared'};
        const response=await fetch(endpoint+(item.kind==='edit'?'/offline-edits':'/offline-comments'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:signal(15000)});check();
        if(!response.ok||response.redirected){if(response.status===401||response.redirected)OfflineClient.blockIdentity();else if(response.status===403||response.status===404)OfflineClient.blockDeck(deck);if(response.status===401||response.status===403||response.status===404||response.redirected){forget();offerRecovery();message('Delivery paused. Your drafts remain on this device.');return;}throw Error('Delivery paused. Your drafts remain on this device.');}
        const result=await response.json();check();
        if(result.status==='conflict'){
          conflict={id:item.id,expected:result.current_expected_hash,content:result.current_content_hash,current:result.current};$('conflict-base').textContent=result.base;$('conflict-current').textContent=result.current;$('conflict-draft').textContent=result.draft;$('conflict').hidden=false;for(const [name,source] of [['base',result.base],['current',result.current],['draft',result.draft]]){renderFrame('conflict-'+name+'-preview',source);}message('Review the conflicting versions.');break;
        }
        await activeStore.update(deck,item.id,null,item.kind==='edit'?result:null);check();const updated=await activeStore.read(deck);check();record=updated;renderQueue();
      }
    }catch(error){if(ownSelection!==selection)return;const active=localStorage.getItem(ACTIVE),deck=record?.snapshot.id;if(active&&deck&&(localStorage.getItem('artifact-studio-offline-blocked-v1:'+active+':*')||localStorage.getItem('artifact-studio-offline-blocked-v1:'+active+':'+encodeURIComponent(deck))))forget();offerRecovery();message(error.message||'Delivery paused. Your drafts remain on this device.');}
    finally{if(deliveryController===controller)deliveryController=null;syncing=false;$('reconnect').disabled=false;if(ownSelection===selection){renderQueue();if(record)slide();}else if(record){renderQueue();const disabled=!selectedSlide()?.receipt;$('source').disabled=disabled;$('comment').disabled=disabled;$('save-edit').disabled=disabled;$('save-comment').disabled=disabled;}}
  }
  $('reconnect').addEventListener('click',deliver);
  $('use-current').addEventListener('click',async()=>{if(!conflict||!record)return;const own=selection,generation=localStorage.getItem('artifact-studio-offline-generation-v1'),deck=record.snapshot.id,activeStore=store,choice=conflict;try{await activeStore.update(deck,choice.id,null,{html:choice.current,expected_hash:choice.expected,content_hash:choice.content});if(own!==selection||generation!==localStorage.getItem('artifact-studio-offline-generation-v1'))return;const updated=await activeStore.read(deck);if(own!==selection||generation!==localStorage.getItem('artifact-studio-offline-generation-v1'))return;record=updated;resetConflict();slide();void deliver();}catch(error){message(error.message);}});
  $('use-draft').addEventListener('click',async()=>{if(!conflict||!record)return;const own=selection,generation=localStorage.getItem('artifact-studio-offline-generation-v1'),deck=record.snapshot.id,activeStore=store,choice=conflict;try{await activeStore.update(deck,choice.id,choice.expected);if(own!==selection||generation!==localStorage.getItem('artifact-studio-offline-generation-v1'))return;resetConflict();void deliver();}catch(error){message(error.message);}});
  $('recover-drafts').addEventListener('click',recoverDrafts);$('close-recovery').addEventListener('click',closeRecovery);
  $('source').addEventListener('input',preview);
  window.addEventListener('resize',()=>{
    if(record)preview();
    if(conflict)for(const name of ['base','current','draft']){renderFrame('conflict-'+name+'-preview',$('conflict-'+name).textContent);}
  });
  $('decks').addEventListener('change',select);$('slides').addEventListener('change',slide);
  $('save-edit').addEventListener('click',()=>queue('edit'));$('save-comment').addEventListener('click',()=>queue('comment'));
  $('clear').addEventListener('click',async()=>{try{await OfflineClient.clear();forget();}catch(error){message(error.message);}});
  void select();
})();
