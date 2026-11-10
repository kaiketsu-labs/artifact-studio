/* Cached image thumbnails; only the selected version owns a live preview frame. */
((root)=>{
  'use strict';
  function create({api,onRestored=()=>{},onOpened=()=>{}}){
    const doc=root.document,escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char])),icon=name=>root.SlideIcons.svg(name);
    const dialog=doc.createElement('dialog');dialog.className='version-history';dialog.setAttribute('aria-labelledby','version-history-title');
    dialog.innerHTML=`<header role="presentation"><div><h2 id="version-history-title">Version history</h2><p class="version-subtitle"></p></div><div class="version-actions"><select aria-label="Version filter"><option value="content">Content changes</option><option value="all">All saves</option></select><button type="button" data-history-close aria-label="Close version history">${icon('close')}</button></div></header><div class="version-layout"><nav class="version-list" aria-label="Saved versions"></nav><section class="version-detail"><div class="version-preview-heading"><strong class="version-name"></strong><span class="version-current"></span></div><p class="version-filter-help"></p><div class="version-preview" aria-label="Saved version preview"></div><div class="version-footer"><details><summary>About this preview</summary><p class="version-fidelity"></p></details><button type="button" class="version-restore">${icon('restore')}<span>Restore version</span></button></div><p class="version-confirm" hidden>Restore as a new version? Current notes stay unchanged. <button type="button" data-confirm-restore>Restore</button> <button type="button" data-cancel-restore>Cancel</button></p><p class="version-error" role="alert"></p><p class="version-status" role="status"></p></section></div>`;
    doc.body.append(dialog);
    const $=selector=>dialog.querySelector(selector);let target=null,items=[],counts=new Map(),next=null,selected=null,expected='',epoch=0,busy=false,opener=null,openedLocation='',restoreCompleted=false,loadBusy=false,restoreRequest=null,includeAll=false;
    const date=value=>value?new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'short'}).format(new Date(value)):'Earlier save';
    const thumbnails=new Map();let thumbnailObserver=null,thumbnailQueue=[],thumbnailBusy=false,thumbnailEpoch=0;
    function clearThumbnails(){thumbnailEpoch++;thumbnailObserver?.disconnect();thumbnailQueue=[];for(const value of thumbnails.values())if(value)root.URL.revokeObjectURL(value);thumbnails.clear();}
    function paintThumbnail(id){
      const image=$('.version-list').querySelector(`[data-history-thumbnail="${CSS.escape(id)}"]`);if(!image||image.dataset.visible!=='true'||!thumbnails.has(id))return;
      const url=thumbnails.get(id),tile=image.parentElement;tile.classList.toggle('is-ready',!!url);tile.classList.toggle('is-unavailable',!url);
      if(url){image.src=url;thumbnails.delete(id);thumbnails.set(id,url);}else tile.querySelector('.version-thumb-state').innerHTML=icon('image')+'<span>Preview unavailable</span>';
    }
    async function loadThumbnails(){
      if(thumbnailBusy)return;thumbnailBusy=true;
      try{while(thumbnailQueue.length&&dialog.open){
        const id=thumbnailQueue.shift();if(thumbnails.has(id)){paintThumbnail(id);continue;}
        const current=thumbnailEpoch,path=target.path;let url=null;
        try{const response=await root.fetch(`${path}/thumbnail?version_id=${encodeURIComponent(id)}`);if(!response.ok)throw Error('Thumbnail unavailable');url=root.URL.createObjectURL(await response.blob());}catch{}
        if(current!==thumbnailEpoch){if(url)root.URL.revokeObjectURL(url);continue;}
        thumbnails.set(id,url);paintThumbnail(id);
        while(thumbnails.size>64){const oldest=thumbnails.keys().next().value,previous=thumbnails.get(oldest);if(previous)root.URL.revokeObjectURL(previous);thumbnails.delete(oldest);}
      }}finally{thumbnailBusy=false;}
    }
    function observeThumbnails(){
      thumbnailObserver?.disconnect();thumbnailQueue=[];
      thumbnailObserver=new IntersectionObserver(entries=>{for(const entry of entries){const image=entry.target,id=image.dataset.historyThumbnail;image.dataset.visible=String(entry.isIntersecting);if(entry.isIntersecting){if(thumbnails.has(id))paintThumbnail(id);else if(!thumbnailQueue.includes(id))thumbnailQueue.push(id);}else{image.removeAttribute('src');image.parentElement.classList.remove('is-ready');thumbnailQueue=thumbnailQueue.filter(value=>value!==id);}}void loadThumbnails();},{root:$('.version-list'),rootMargin:'80px'});
      for(const image of dialog.querySelectorAll('[data-history-thumbnail]'))thumbnailObserver.observe(image);
    }
    function render(){
      const groups=new Map();for(const item of items){if(!groups.has(item.group_id))groups.set(item.group_id,[]);groups.get(item.group_id).push(item);}
      const open=new Set([...dialog.querySelectorAll('details[data-group][open]')].map(element=>element.dataset.group));
      const row=item=>`<button type="button" data-version="${escape(item.version_id)}" aria-label="Version ${item.revision}${item.is_current?' · Current':''}" aria-current="${item.version_id===selected?.version_id}"><span class="version-thumb"><img data-history-thumbnail="${escape(item.version_id)}" alt="" width="400" height="225" decoding="async"><span class="version-thumb-state" aria-hidden="true">${icon('image')}</span></span><span class="version-caption"><span>Version ${item.revision}</span> <small>${item.is_current?'Current':escape(date(item.created_at))}${includeAll&&!item.content_changed?' · Same content':''}${item.author?' · '+escape(item.author):''}</small></span></button>`;
      $('.version-list').innerHTML=[...groups].map(([id,saves])=>{
        const count=counts.get(id)||saves.length, remaining=count-1;
        return `<section class="version-group"><div class="version-group-date">${escape(date(saves[0].created_at))}</div>${row(saves[0])}${remaining?`<details data-group="${escape(id)}" ${open.has(id)?'open':''}><summary>${remaining} earlier ${includeAll?(remaining===1?'save':'saves'):(remaining===1?'version':'versions')}</summary><div>${saves.slice(1).map(row).join('')}</div></details>`:''}</section>`;
      }).join('')+(next?'<button type="button" class="version-earlier">Load earlier</button>':'');
      for(const button of $('.version-list').querySelectorAll('[data-version]'))button.setAttribute('aria-label',button.querySelector('.version-caption').textContent.replace(/\s+/g,' ').trim());
      observeThumbnails();
    }
    function scale(){const container=$('.version-preview'),frame=container.querySelector('iframe');if(!frame||target?.kind!=='slide')return;const factor=Math.min(container.clientWidth/1600,container.clientHeight/900);frame.style.cssText=`width:1600px;height:900px;transform:scale(${factor});left:${(container.clientWidth-1600*factor)/2}px;top:${(container.clientHeight-900*factor)/2}px`;}
    new ResizeObserver(scale).observe($('.version-preview'));
    function select(item){
      selected=item;restoreRequest=null;$('.version-confirm').hidden=true;$('.version-error').textContent='';$('.version-name').textContent=`Version ${item.revision}`;$('.version-current').textContent=item.is_current?'Current':'';$('.version-restore').disabled=item.is_current||busy;render();
      const container=$('.version-preview');container.replaceChildren();const frame=doc.createElement('iframe');frame.title=`Version ${item.revision} preview`;frame.setAttribute('sandbox','allow-scripts allow-downloads');frame.referrerPolicy='no-referrer';
      frame.src=target.kind==='slide'?`${target.path}/preview?version_id=${encodeURIComponent(item.version_id)}`:`/api/artifacts/${encodeURIComponent(target.artifactId)}/preview?revision=${encodeURIComponent(item.version_id.split(':')[1])}`;
      container.append(frame);scale();const row=$('.version-list').querySelector(`[data-version="${CSS.escape(item.version_id)}"]`);row?.focus({preventScroll:true});row?.scrollIntoView({block:'nearest'});
    }
    async function load(){
      if(loadBusy)return;loadBusy=true;const current=epoch;$('.version-status').textContent='Loading versions…';
      try{const query=new URLSearchParams();if(next)query.set('cursor',next);if(includeAll)query.set('include_all','true');const page=await api(target.path+(query.size?'?'+query:''));if(current!==epoch||!dialog.open)return;if(!expected)expected=page.expected_hash;$('option[value=all]').textContent=page.total_saves?`All saves (${page.total_saves})`:'All saves';for(const group of page.groups)counts.set(group.id,group.count);next=page.next_cursor;const seen=new Set(items.map(item=>item.version_id));items.push(...page.items.filter(item=>!seen.has(item.version_id)));$('.version-fidelity').textContent=page.fidelity_warning;$('.version-filter-help').textContent=includeAll?'All retained saves are shown.':'Source or animation changes may look the same.'+(page.hidden_saves?` ${page.hidden_saves} ${page.hidden_saves===1?'save':'saves'} with unchanged content ${page.hidden_saves===1?'is':'are'} hidden; choose All saves to see ${page.hidden_saves===1?'it':'them'}.`:'');$('.version-status').textContent=items.length?'':'No saved versions yet.';if(!selected&&items.length)select(items[0]);else render();}
      catch(error){if(current===epoch){$('.version-error').textContent=error.message;$('.version-status').textContent='';}}
      finally{if(current===epoch)loadBusy=false;}
    }
    function cancelRestore(){ $('.version-confirm').hidden=true;$('.version-restore').focus({preventScroll:true}); }
    function restoreFocus(){
      if(restoreCompleted||root.location.href!==openedLocation)return;
      const active=doc.activeElement;
      if(active&&active!==doc.body&&active!==doc.documentElement&&!dialog.contains(active))return;
      const visible=element=>element?.isConnected&&!element.closest('[inert]')&&element.getClientRects().length&&root.getComputedStyle(element).visibility!=='hidden';
      const trigger=visible(opener)?opener:doc.querySelector(target?.kind==='slide'?'#version-history':'.hub-view-toolbar [data-action="history"]');
      if(visible(trigger))trigger.focus({preventScroll:true});
    }
    dialog.addEventListener('click',async event=>{
      if(event.target.closest('[data-history-close]')){if(!busy)dialog.close();return;}
      const choice=event.target.closest('[data-version]');if(choice&&!busy){select(items.find(item=>item.version_id===choice.dataset.version));return;}
      if(event.target.closest('.version-earlier')){void load();return;}
      if(event.target.closest('.version-restore')&&!target?.readOnly){$('.version-confirm').hidden=false;$('[data-confirm-restore]').focus();return;}
      if(event.target.closest('[data-cancel-restore]')){cancelRestore();return;}
      if(!event.target.closest('[data-confirm-restore]')||busy||target?.readOnly)return;
      busy=true;dialog.setAttribute('aria-busy','true');dialog.querySelectorAll('button,select').forEach(button=>button.disabled=true);$('.version-error').textContent='';
      try{await api(target.path+'/restore',{version_id:selected.version_id,expected_hash:expected,request_id:restoreRequest||(restoreRequest=root.crypto.randomUUID())});await onRestored(target);restoreCompleted=true;dialog.close();}
      catch(error){$('.version-error').textContent=error.message;$('.version-confirm').hidden=true;}
      finally{busy=false;dialog.removeAttribute('aria-busy');dialog.querySelectorAll('button,select').forEach(button=>button.disabled=false);$('.version-restore').disabled=selected?.is_current;}
    });
    $('.version-actions select').addEventListener('change',()=>{if(busy)return;clearThumbnails();includeAll=$('.version-actions select').value==='all';epoch++;items=[];counts=new Map();expected='';next=null;selected=null;loadBusy=false;$('.version-list').replaceChildren();$('.version-preview').replaceChildren();$('.version-filter-help').textContent='';$('.version-restore').disabled=true;$('.version-confirm').hidden=true;void load();});
    $('.version-list').addEventListener('keydown',event=>{if(busy||!event.target.closest('[data-version]')||!['ArrowUp','ArrowDown'].includes(event.key))return;event.preventDefault();const visible=[...dialog.querySelectorAll('.version-list [data-version]')].filter(button=>button.getClientRects().length),index=visible.indexOf(event.target.closest('[data-version]')),next=visible[index+(event.key==='ArrowDown'?1:-1)];if(next)select(items.find(item=>item.version_id===next.dataset.version));});
    dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();else if(!$('.version-confirm').hidden){event.preventDefault();cancelRestore();}});
    dialog.addEventListener('close',()=>{epoch++;loadBusy=false;clearThumbnails();$('.version-preview').replaceChildren();restoreFocus();});
    return {open(value){if(busy)return;epoch++;clearThumbnails();target={...value,path:value.kind==='slide'?`/api/decks/${encodeURIComponent(value.deckId)}/slides/${encodeURIComponent(value.slideId)}/history`:`/api/artifacts/${encodeURIComponent(value.artifactId)}/history`};includeAll=false;$('.version-actions select').value='content';items=[];counts=new Map();expected='';next=null;selected=null;loadBusy=false;$('.version-list').replaceChildren();$('.version-preview').replaceChildren();$('.version-filter-help').textContent='';$('.version-error').textContent='';$('.version-confirm').hidden=true;$('.version-subtitle').textContent=value.title;$('.version-restore').disabled=true;$('.version-restore').hidden=!!value.readOnly;opener=doc.activeElement;openedLocation=root.location.href;restoreCompleted=false;dialog.showModal();onOpened(dialog,target);void load();}};
  }
  root.VersionTimeline={create};
})(window);
