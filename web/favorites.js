/* Shared bookmarks and a deliberate, version-bound reference picker. */
((root)=>{
  'use strict';
  /** Keep authored previews live only around the visible part of the picker. */
  function createPreviews({view=root,document=view.document,concurrency=2}={}){
    const records=new Map();let container=null,intersection=null,resize=null,paused=false;
    const scale=record=>{if(record.frame)record.frame.style.transform=`scale(${record.cover.getBoundingClientRect().width/1280})`;};
    function dispose(record){
      view.clearTimeout(record.timer);record.timer=null;record.loading=false;
      if(record.frame){record.frame.onload=null;record.frame.onerror=null;record.frame.remove();record.frame=null;}
      record.cover.dataset.previewState=record.failed?'unavailable':'pending';
    }
    function near(cover){
      const box=cover.getBoundingClientRect(),bounds=container.getBoundingClientRect();
      return box.width>0&&box.height>0&&box.bottom>=bounds.top-80&&box.top<=bounds.bottom+80;
    }
    function load(record){
      const frame=document.createElement('iframe');record.frame=frame;record.loading=true;
      frame.title=record.cover.dataset.previewTitle;frame.setAttribute('aria-hidden','true');frame.tabIndex=-1;frame.setAttribute('sandbox','');
      record.cover.dataset.previewState='loading';
      const finish=failed=>{
        if(record.frame!==frame)return;
        view.clearTimeout(record.timer);record.timer=null;record.loading=false;record.failed=failed;
        if(failed)dispose(record);else record.cover.dataset.previewState='ready';
        pump();
      };
      frame.onload=()=>finish(false);frame.onerror=()=>finish(true);
      record.timer=view.setTimeout(()=>finish(true),45000);
      frame.src=record.url;record.cover.prepend(frame);scale(record);
    }
    function pump(){
      if(paused||!container?.open)return;
      let active=[...records.values()].filter(record=>record.loading).length;
      const bounds=container.getBoundingClientRect();
      const distance=record=>{const box=record.cover.getBoundingClientRect();return Math.max(0,bounds.top-box.bottom,box.top-bounds.bottom);};
      const candidates=[...records.values()].filter(record=>record.visible&&!record.frame&&!record.failed).map(record=>({record,distance:distance(record)}));
      for(const {record} of candidates.sort((a,b)=>a.distance-b.distance)){
        if(active>=concurrency)break;
        load(record);active++;
      }
    }
    function observe(){
      intersection?.disconnect();resize?.disconnect();
      if(paused||!container?.open)return;
      if(view.IntersectionObserver){
        intersection=new view.IntersectionObserver(entries=>{
          for(const entry of entries){const record=records.get(entry.target);if(record){record.visible=entry.isIntersecting;if(!record.visible)dispose(record);}}
          pump();
        },{root:container,rootMargin:'80px 0px',threshold:0});
      }
      if(view.ResizeObserver)resize=new view.ResizeObserver(entries=>{for(const entry of entries){const record=records.get(entry.target);if(record)scale(record);}});
      for(const record of records.values()){
        record.visible=near(record.cover);if(!record.visible)dispose(record);
        intersection?.observe(record.cover);resize?.observe(record.cover);scale(record);
      }
      pump();
    }
    function mount(next){
      container=next;const covers=new Set(next.querySelectorAll('[data-favorite-preview]'));
      for(const [cover,record] of records)if(!covers.has(cover)||record.url!==cover.dataset.favoritePreview){dispose(record);records.delete(cover);}
      for(const cover of covers)if(!records.has(cover))records.set(cover,{cover,url:cover.dataset.favoritePreview,frame:null,loading:false,failed:false,timer:null,visible:false});
      paused=!!document.hidden;observe();
    }
    function clear(){intersection?.disconnect();resize?.disconnect();for(const record of records.values())dispose(record);records.clear();container=null;}
    function visibility(){paused=!!document.hidden;if(paused){intersection?.disconnect();resize?.disconnect();for(const record of records.values())dispose(record);}else observe();}
    const scan=()=>{if(!view.IntersectionObserver&&!paused)observe();};
    document.addEventListener('visibilitychange',visibility);view.addEventListener?.('scroll',scan,true);view.addEventListener?.('resize',scan);
    return {mount,clear,destroy(){clear();document.removeEventListener('visibilitychange',visibility);view.removeEventListener?.('scroll',scan,true);view.removeEventListener?.('resize',scan);}};
  }
  if(typeof module!=='undefined'&&module.exports){module.exports={createPreviews};return;}
  const doc=root.document,escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const icon=()=>root.SlideIcons.svg('star');
  let saved={artifacts:[],slides:[]},epoch=0,picker=null,busy=false,writeQueue=Promise.resolve();
  const pendingStars=new Set();
  const starKey=node=>[node.dataset.starKind,node.dataset.starId,node.dataset.starSlide].join('/');
  const listeners=new Set();
  async function api(path,body){const response=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(typeof data.detail==='string'?data.detail:'Could not load favorites. Try again.');}return response.json();}
  function has(kind,id,slide=''){return saved[kind==='artifact'?'artifacts':'slides'].includes(kind==='artifact'?id:`${id}/${slide}`);}
  const actionLabel=active=>active?'Remove from favorites':'Add to favorites';
  const buttonLabel=(kind,active,title)=>kind==='slide'?`${actionLabel(active)}: ${title}`:`${active?'Unstar':'Star'} ${title}`;
  function sync(notify=true){
    for(const node of doc.querySelectorAll('[data-star-kind]')){
      const active=has(node.dataset.starKind,node.dataset.starId,node.dataset.starSlide),label=buttonLabel(node.dataset.starKind,active,node.dataset.starTitle);
      node.setAttribute('aria-pressed',String(active));node.setAttribute('aria-label',label);node.title=label;node.disabled=pendingStars.has(starKey(node));
    }
    if(notify)for(const listener of listeners)listener();
  }
  async function refresh(){if(pendingStars.size)return saved;const request=++epoch,data=await api('/api/favorites');if(request===epoch && JSON.stringify(saved)!==JSON.stringify(data)){saved=data;sync();}return saved;}
  function button(kind,id,title,slide='',className=''){
    const active=has(kind,id,slide),label=buttonLabel(kind,active,title);
    return `<button type="button" class="favorite-star ${className}" data-star-kind="${kind}" data-star-id="${escape(id)}" data-star-slide="${escape(slide)}" data-star-title="${escape(title)}" aria-label="${escape(label)}" title="${escape(label)}" aria-pressed="${active}" ${pendingStars.has([kind,id,slide].join('/'))?'disabled':''}>${icon()}</button>`;
  }
  /** Serialize favorites from rail buttons and slide-menu commands through one queue. */
  async function toggle(kind,id,slide=''){
    const identity=[kind,id,slide].join('/');if(pendingStars.has(identity))return;
    const body={kind,artifact_id:id,slide_id:slide,starred:!has(kind,id,slide)};
    pendingStars.add(identity);sync(false);
    writeQueue=writeQueue.then(async()=>{
      ++epoch;
      try{saved=await api('/api/favorites',body);}
      catch(error){root.alert(error.message);}
      finally{
        pendingStars.delete(identity);sync();
        if(picker?.open)await load();
      }
    });
    await writeQueue;
  }
  doc.addEventListener('click',async event=>{
    const node=event.target.closest('[data-star-kind]');if(!node)return;
    event.preventDefault();event.stopImmediatePropagation();
    if(node.disabled)return;
    const identity=starKey(node),wasFocused=doc.activeElement===node;
    await toggle(node.dataset.starKind,node.dataset.starId,node.dataset.starSlide);
    if(wasFocused && [doc.body,node].includes(doc.activeElement)){
      [...doc.querySelectorAll('[data-star-kind]')].find(candidate=>starKey(candidate)===identity)?.focus();
    }
  },true);
  let entries=[],chosen=new Set(),attach=null,origin=null,loadEpoch=0;
  const previews=createPreviews();
  let descriptionDialog=null,descriptionSaving=false,describing=null;
  function editDescription(item){
    if(!descriptionDialog){
      descriptionDialog=doc.createElement('dialog');descriptionDialog.className='favorites-dialog favorite-description-dialog';descriptionDialog.setAttribute('aria-labelledby','favorite-description-title');
      descriptionDialog.innerHTML=`<form><div class="favorites-heading"><div><h2 id="favorite-description-title">Slide description</h2><p id="favorite-description-slide"></p></div><button type="button" data-description-close aria-label="Close slide description">${root.SlideIcons.svg('close')}</button></div><label for="favorite-description-text">What makes this slide useful?</label><textarea id="favorite-description-text" rows="3" maxlength="300" placeholder="e.g. A clear layout for comparing two options."></textarea><p class="favorite-description-help">Searchable, and included with this slide when you reference it in chat.</p><p class="favorites-error" role="alert"></p><div class="favorite-description-actions"><button type="button" data-description-cancel>Cancel</button><button type="submit" class="primary">Save</button></div></form>`;
      doc.body.append(descriptionDialog);
      for(const button of descriptionDialog.querySelectorAll('[data-description-close],[data-description-cancel]'))button.addEventListener('click',()=>{if(!descriptionSaving)descriptionDialog.close();});
      descriptionDialog.addEventListener('cancel',event=>{if(descriptionSaving)event.preventDefault();});
      descriptionDialog.addEventListener('close',()=>{
        if(!picker.open)return;
        const key=describing?.deck_id+'/'+describing?.slide_id;
        ([...picker.querySelectorAll('[data-favorite-description]')].find(button=>button.dataset.favoriteDescription===key)||picker.querySelector('input')).focus();
      });
      descriptionDialog.querySelector('form').addEventListener('submit',async event=>{
        event.preventDefault();if(descriptionSaving||!describing)return;
        descriptionSaving=true;const item=describing;
        const controls=[...descriptionDialog.querySelectorAll('button,textarea')];for(const control of controls)control.disabled=true;
        const message=descriptionDialog.querySelector('.favorites-error');message.textContent='';
        try{
          await api(`/api/favorites/slides/${encodeURIComponent(item.deck_id)}/${encodeURIComponent(item.slide_id)}/description`,{description:descriptionDialog.querySelector('textarea').value,expected_description:item.description||''});
          await load();descriptionDialog.close();
        }catch(reason){message.textContent=reason.message;await load();}
        finally{descriptionSaving=false;for(const control of controls)control.disabled=false;}
      });
    }
    describing={...item};descriptionDialog.querySelector('#favorite-description-slide').textContent=item.title;
    descriptionDialog.querySelector('textarea').value=item.description||'';descriptionDialog.querySelector('.favorites-error').textContent='';
    descriptionDialog.showModal();descriptionDialog.querySelector('textarea').focus();
  }
  function ensurePicker(){
    if(picker)return;
    picker=doc.createElement('dialog');picker.className='favorites-dialog';picker.setAttribute('aria-labelledby','favorites-title');
    picker.innerHTML=`<div class="favorites-heading"><div><h2 id="favorites-title">${root.SlideIcons.svg('favoriteSlides')} Favorite slides</h2><p id="favorites-help"></p></div><button type="button" data-favorites-close aria-label="Close favorite slides">${root.SlideIcons.svg('close')}</button></div><label class="favorites-search">${root.SlideIcons.svg('search')}<input type="search" aria-label="Search favorite slides" placeholder="Search titles or descriptions…"></label><div class="favorites-grid"></div><p class="favorites-error" role="alert"></p><div class="favorites-footer"><span role="status"></span><button type="button" class="primary" data-favorites-add>Add references</button></div>`;
    doc.body.append(picker);
    picker.querySelector('[data-favorites-close]').addEventListener('click',()=>{if(!busy)picker.close();});
    picker.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
    picker.addEventListener('close',()=>{loadEpoch++;previews.clear();picker.querySelector('.favorites-grid').replaceChildren();entries=[];chosen.clear();attach=null;origin?.focus();});
    picker.addEventListener('click',event=>{if(event.target===picker&&!busy){const box=picker.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)picker.close();}});
    picker.querySelector('input').addEventListener('input',render);
    picker.querySelector('.favorites-grid').addEventListener('click',event=>{
      const description=event.target.closest('[data-favorite-description]');
      if(description&&!busy){const item=entries.find(item=>item.deck_id+'/'+item.slide_id===description.dataset.favoriteDescription);if(item)editDescription(item);return;}
      const node=event.target.closest('[data-favorite-pick]');if(!node||busy||node.disabled)return;
      const key=node.dataset.favoritePick;
      if(chosen.has(key))chosen.delete(key);else if(chosen.size<5)chosen.add(key);else{error('Choose up to 5 slides.');return;}
      error('');node.setAttribute('aria-pressed',String(chosen.has(key)));footer();
    });
    picker.querySelector('[data-favorites-add]').addEventListener('click',async()=>{
      if(busy||!chosen.size||!attach)return;busy=true;footer();error('');
      try{
        const files=[];
        for(const item of entries.filter(item=>chosen.has(item.deck_id+'/'+item.slide_id))){
          const result=await api(`/api/favorites/slides/${encodeURIComponent(item.deck_id)}/${encodeURIComponent(item.slide_id)}/reference`,{content_hash:item.content_hash});
          const bytes=Uint8Array.from(atob(result.data_base64),char=>char.charCodeAt(0));
          const title=String(item.title).replace(/[/\\:\x00-\x1f]/g,'-').trim().slice(0,160)||'Slide';
          const file=new File([bytes],`${title} - slide reference.html`,{type:'text/html'});
          file.slideReference={title:item.title,deckTitle:item.deck_title,description:item.description||''};files.push(file);
        }
        if(await attach(files)!==false)picker.close();
        else error('Could not attach these slides. Check the message and attachment limits.');
      }catch(reason){error(reason.message);}
      finally{busy=false;footer();}
    });
  }
  function error(message){picker.querySelector('.favorites-error').textContent=message;}
  function footer(){
    const node=picker.querySelector('.favorites-footer');node.hidden=!attach;
    node.querySelector('span').textContent=busy?'Preparing references…':chosen.size?`${chosen.size} selected`:'';
    node.querySelector('button').disabled=busy||!chosen.size;
    picker.querySelector('[data-favorites-close]').disabled=busy;
    for(const button of picker.querySelectorAll('[data-favorite-pick],[data-star-kind],[data-favorite-description]'))button.disabled=busy||!!entries.find(item=>item.deck_id+'/'+item.slide_id===button.dataset.favoritePick)?.reference_error;
  }
  function render(){
    const query=picker.querySelector('input').value.toLocaleLowerCase().trim();
    const matches=entries.filter(item=>`${item.title} ${item.deck_title} ${item.description||''}`.toLocaleLowerCase().includes(query));
    const grid=picker.querySelector('.favorites-grid'),existing=new Map([...grid.querySelectorAll('[data-favorite-card]')].map(card=>[card.dataset.favoriteCard,card]));
    const keys=new Set(matches.map(item=>item.deck_id+'/'+item.slide_id));
    for(const child of [...grid.childNodes])if(!keys.has(child.dataset?.favoriteCard))child.remove();
    matches.forEach((item,index)=>{
      const key=item.deck_id+'/'+item.slide_id,url=`/?deck=${encodeURIComponent(item.deck_id)}#${encodeURIComponent(item.slide_id)}`;
      const signature=JSON.stringify([item,!!attach]),previous=existing.get(key);let card=previous;
      if(!card||card.dataset.signature!==signature){
        const body=`<span class="favorite-cover" data-favorite-preview="/api/decks/${encodeURIComponent(item.deck_id)}/slides/${encodeURIComponent(item.slide_id)}/preview?v=${encodeURIComponent(item.content_hash)}" data-preview-title="Preview of ${escape(item.title)}"><span class="favorite-preview-placeholder" aria-hidden="true">${root.SlideIcons.svg('slides')}</span><span class="favorite-check" aria-hidden="true">${root.SlideIcons.svg('check')}</span></span><span class="favorite-caption"><strong>${escape(item.title)}</strong><small>${escape(item.deck_title)} · ${String(item.number).padStart(2,'0')}</small>${item.reference_error?`<small class="favorites-error">${escape(item.reference_error)}</small>`:''}</span>`;
        card=doc.createElement('article');card.className='favorite-card';card.dataset.favoriteCard=key;card.dataset.signature=signature;
        card.innerHTML=`<div class="favorite-main">${attach?`<button type="button" class="favorite-pick" data-favorite-pick="${escape(key)}" aria-pressed="${chosen.has(key)}" aria-label="Reference ${escape(item.title)} from ${escape(item.deck_title)}">${body}</button>`:`<a class="favorite-pick" href="${url}">${body}</a>`}<div class="favorite-card-tools"><a href="${url}" target="_blank" rel="noopener" title="Open original slide" aria-label="Open ${escape(item.title)} in presentation">${root.SlideIcons.svg('external')}</a>${button('slide',item.deck_id,item.title,item.slide_id)}</div></div><button type="button" class="favorite-description${item.description?' has-description':''}" data-favorite-description="${escape(key)}" aria-label="${item.description?'Edit':'Add'} description for ${escape(item.title)}" title="${item.description?'Edit':'Add'} description">${root.SlideIcons.svg('edit')}<span>${escape(item.description||'Add description')}</span></button>`;
        if(previous)previous.replaceWith(card);
      }
      if(grid.children[index]!==card){if(grid.moveBefore&&card.parentNode===grid)grid.moveBefore(card,grid.children[index]||null);else grid.insertBefore(card,grid.children[index]||null);}
      card.querySelector('[data-favorite-pick]')?.setAttribute('aria-pressed',String(chosen.has(key)));
    });
    if(!matches.length)grid.innerHTML=`<div class="favorites-empty">${icon()}<h3>${query?'No matching slides':'Keep your best slides handy'}</h3><p>${query?'Try another title or presentation.':'Star a slide in any presentation to find it here.'}</p></div>`;
    previews.mount(picker);footer();
  }
  async function load(){const request=++loadEpoch;try{const items=await api('/api/favorites/slides');if(request!==loadEpoch||!picker.open)return;entries=items;const keys=new Set(items.map(item=>item.deck_id+'/'+item.slide_id));chosen=new Set([...chosen].filter(key=>keys.has(key)));render();}catch(reason){if(request===loadEpoch)error(reason.message);}}
  async function open(options={}){
    ensurePicker();origin=doc.activeElement;attach=options.attach||null;chosen.clear();entries=[];
    picker.querySelector('input').value='';picker.querySelector('#favorites-help').textContent=attach?'Choose favorites from any presentation.':'Your starred slides, across presentations.';
    error('');picker.querySelector('.favorites-grid').textContent='Loading…';footer();picker.showModal();picker.querySelector('input').focus();await load();
  }
  root.FavoriteSlides={has,button,refresh,sync,open,toggle,actionLabel,onChange(listener){listeners.add(listener);}};
})(typeof window==='undefined'?{}:window);
