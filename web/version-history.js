/* Source-free timeline pages; only the selected version owns a preview frame. */
((root)=>{
  'use strict';
  function create({api,onRestored=()=>{}}){
    const doc=root.document,escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char])),icon=name=>root.SlideIcons.svg(name);
    const dialog=doc.createElement('dialog');dialog.className='version-history';dialog.setAttribute('aria-labelledby','version-history-title');
    dialog.innerHTML=`<header><div><h2 id="version-history-title">Version history</h2><p class="version-subtitle"></p></div><button type="button" data-history-close aria-label="Close version history">${icon('close')}</button></header><div class="version-layout"><nav class="version-list" aria-label="Saved versions"></nav><section class="version-detail"><div class="version-preview-heading"><strong class="version-name"></strong><span class="version-current"></span></div><div class="version-preview" aria-label="Saved version preview"></div><div class="version-footer"><details><summary>About this preview</summary><p class="version-fidelity"></p></details><button type="button" class="version-restore">${icon('restore')}<span>Restore version</span></button></div><p class="version-confirm" hidden>Restore as a new version? Current notes stay unchanged. <button type="button" data-confirm-restore>Restore</button> <button type="button" data-cancel-restore>Cancel</button></p><p class="version-error" role="alert"></p><p class="version-status" role="status"></p></section></div>`;
    doc.body.append(dialog);
    const $=selector=>dialog.querySelector(selector);let target=null,items=[],counts=new Map(),next=null,selected=null,expected='',epoch=0,busy=false,opener=null,loadBusy=false,restoreRequest=null;
    const date=value=>value?new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'short'}).format(new Date(value)):'Earlier save';
    function render(){
      const groups=new Map();for(const item of items){if(!groups.has(item.group_id))groups.set(item.group_id,[]);groups.get(item.group_id).push(item);}
      const open=new Set([...dialog.querySelectorAll('details[data-group][open]')].map(element=>element.dataset.group));
      $('.version-list').innerHTML=[...groups].map(([id,saves])=>`<details data-group="${escape(id)}" ${open.has(id)||saves.some(item=>item.version_id===selected?.version_id)?'open':''}><summary><time>${escape(date(saves[0].created_at))}</time><small>${(counts.get(id)||saves.length)===1?'1 save':`${counts.get(id)||saves.length} saves`}</small></summary><div>${saves.map(item=>`<button type="button" data-version="${escape(item.version_id)}" aria-current="${item.version_id===selected?.version_id}"><span class="version-dot"></span><span>Version ${item.revision}<small>${item.is_current?'Current':escape(date(item.created_at))}</small></span></button>`).join('')}</div></details>`).join('')+(next?'<button type="button" class="version-earlier">Load earlier</button>':'');
    }
    function scale(){const container=$('.version-preview'),frame=container.querySelector('iframe');if(!frame||target?.kind!=='slide')return;const factor=Math.min(container.clientWidth/1600,container.clientHeight/900);frame.style.cssText=`width:1600px;height:900px;transform:scale(${factor});left:${(container.clientWidth-1600*factor)/2}px;top:${(container.clientHeight-900*factor)/2}px`;}
    new ResizeObserver(scale).observe($('.version-preview'));
    function select(item){
      selected=item;restoreRequest=null;$('.version-confirm').hidden=true;$('.version-error').textContent='';$('.version-name').textContent=`Version ${item.revision}`;$('.version-current').textContent=item.is_current?'Current':'';$('.version-restore').disabled=item.is_current||busy;render();
      const container=$('.version-preview');container.replaceChildren();const frame=doc.createElement('iframe');frame.title=`Version ${item.revision} preview`;frame.setAttribute('sandbox','allow-scripts allow-downloads');frame.referrerPolicy='no-referrer';
      frame.src=target.kind==='slide'?`${target.path}/preview?version_id=${encodeURIComponent(item.version_id)}`:`/api/artifacts/${encodeURIComponent(target.artifactId)}/preview?revision=${encodeURIComponent(item.version_id.split(':')[1])}`;
      container.append(frame);scale();$('.version-list').querySelector(`[data-version="${CSS.escape(item.version_id)}"]`)?.focus({preventScroll:true});
    }
    async function load(){
      if(loadBusy)return;loadBusy=true;const current=epoch;$('.version-status').textContent='Loading versions…';
      try{const page=await api(target.path+(next?'?cursor='+encodeURIComponent(next):''));if(current!==epoch||!dialog.open)return;if(!expected)expected=page.expected_hash;for(const group of page.groups)counts.set(group.id,group.count);next=page.next_cursor;const seen=new Set(items.map(item=>item.version_id));items.push(...page.items.filter(item=>!seen.has(item.version_id)));$('.version-fidelity').textContent=page.fidelity_warning;$('.version-status').textContent=items.length?'':'No saved versions yet.';if(!selected&&items.length)select(items[0]);else render();}
      catch(error){if(current===epoch){$('.version-error').textContent=error.message;$('.version-status').textContent='';}}
      finally{if(current===epoch)loadBusy=false;}
    }
    dialog.addEventListener('click',async event=>{
      if(event.target.closest('[data-history-close]')){if(!busy)dialog.close();return;}
      const choice=event.target.closest('[data-version]');if(choice&&!busy){select(items.find(item=>item.version_id===choice.dataset.version));return;}
      if(event.target.closest('.version-earlier')){void load();return;}
      if(event.target.closest('.version-restore')){$('.version-confirm').hidden=false;$('[data-confirm-restore]').focus();return;}
      if(event.target.closest('[data-cancel-restore]')){$('.version-confirm').hidden=true;return;}
      if(!event.target.closest('[data-confirm-restore]')||busy)return;
      busy=true;dialog.setAttribute('aria-busy','true');dialog.querySelectorAll('button').forEach(button=>button.disabled=true);$('.version-error').textContent='';
      try{await api(target.path+'/restore',{version_id:selected.version_id,expected_hash:expected,request_id:restoreRequest||(restoreRequest=root.crypto.randomUUID())});await onRestored(target);dialog.close();}
      catch(error){$('.version-error').textContent=error.message;$('.version-confirm').hidden=true;}
      finally{busy=false;dialog.removeAttribute('aria-busy');dialog.querySelectorAll('button').forEach(button=>button.disabled=false);$('.version-restore').disabled=selected?.is_current;}
    });
    $('.version-list').addEventListener('keydown',event=>{if(busy||!event.target.closest('[data-version]')||!['ArrowUp','ArrowDown'].includes(event.key))return;event.preventDefault();const index=items.findIndex(item=>item.version_id===selected?.version_id),next=items[index+(event.key==='ArrowDown'?1:-1)];if(next)select(next);});
    dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
    dialog.addEventListener('close',()=>{epoch++;loadBusy=false;$('.version-preview').replaceChildren();opener?.focus();});
    return {open(value){if(busy)return;epoch++;target={...value,path:value.kind==='slide'?`/api/decks/${encodeURIComponent(value.deckId)}/slides/${encodeURIComponent(value.slideId)}/history`:`/api/artifacts/${encodeURIComponent(value.artifactId)}/history`};items=[];counts=new Map();expected='';next=null;selected=null;loadBusy=false;$('.version-list').replaceChildren();$('.version-preview').replaceChildren();$('.version-error').textContent='';$('.version-confirm').hidden=true;$('.version-subtitle').textContent=value.title;$('.version-restore').disabled=true;opener=doc.activeElement;dialog.showModal();void load();}};
  }
  root.VersionTimeline={create};
})(window);
