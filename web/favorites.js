/* Shared bookmarks and a deliberate, version-bound reference picker. */
((root)=>{
  'use strict';
  const doc=root.document,escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const icon=()=>root.SlideIcons.svg('star');
  let saved={artifacts:[],slides:[]},epoch=0,picker=null,busy=false,writeQueue=Promise.resolve();
  const pendingStars=new Set();
  const starKey=node=>[node.dataset.starKind,node.dataset.starId,node.dataset.starSlide].join('/');
  const listeners=new Set();
  async function api(path,body){const response=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(typeof data.detail==='string'?data.detail:'Could not load favorites. Try again.');}return response.json();}
  function has(kind,id,slide=''){return saved[kind==='artifact'?'artifacts':'slides'].includes(kind==='artifact'?id:`${id}/${slide}`);}
  function sync(){
    for(const node of doc.querySelectorAll('[data-star-kind]')){
      const active=has(node.dataset.starKind,node.dataset.starId,node.dataset.starSlide),label=`${active?'Unstar':'Star'} ${node.dataset.starTitle}`;
      node.setAttribute('aria-pressed',String(active));node.setAttribute('aria-label',label);node.title=label;node.disabled=pendingStars.has(starKey(node));
    }
    for(const listener of listeners)listener();
  }
  async function refresh(){if(pendingStars.size)return saved;const request=++epoch,data=await api('/api/favorites');if(request===epoch && JSON.stringify(saved)!==JSON.stringify(data)){saved=data;sync();}return saved;}
  function button(kind,id,title,slide='',className=''){
    const active=has(kind,id,slide),label=`${active?'Unstar':'Star'} ${title}`;
    return `<button type="button" class="favorite-star ${className}" data-star-kind="${kind}" data-star-id="${escape(id)}" data-star-slide="${escape(slide)}" data-star-title="${escape(title)}" aria-label="${escape(label)}" title="${escape(label)}" aria-pressed="${active}" ${pendingStars.has([kind,id,slide].join('/'))?'disabled':''}>${icon()}</button>`;
  }
  doc.addEventListener('click',async event=>{
    const node=event.target.closest('[data-star-kind]');if(!node)return;
    event.preventDefault();event.stopImmediatePropagation();
    const identity=starKey(node);if(node.disabled||pendingStars.has(identity))return;
    const wasFocused=doc.activeElement===node;
    const body={kind:node.dataset.starKind,artifact_id:node.dataset.starId,slide_id:node.dataset.starSlide,starred:!has(node.dataset.starKind,node.dataset.starId,node.dataset.starSlide)};
    pendingStars.add(identity);node.disabled=true;
    writeQueue=writeQueue.then(async()=>{
      ++epoch;
      try{saved=await api('/api/favorites',body);}
      catch(error){root.alert(error.message);}
      finally{
        pendingStars.delete(identity);sync();
        if(picker?.open)await load();
        if(wasFocused && [doc.body,node].includes(doc.activeElement)){
          [...doc.querySelectorAll('[data-star-kind]')].find(candidate=>starKey(candidate)===identity)?.focus();
        }
      }
    });
    await writeQueue;
  },true);
  let entries=[],chosen=new Set(),attach=null,origin=null,loadEpoch=0;
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
    picker.innerHTML=`<div class="favorites-heading"><div><h2 id="favorites-title">${icon()} Favorite slides</h2><p id="favorites-help"></p></div><button type="button" data-favorites-close aria-label="Close favorite slides">${root.SlideIcons.svg('close')}</button></div><label class="favorites-search">${root.SlideIcons.svg('search')}<input type="search" aria-label="Search favorite slides" placeholder="Search titles or descriptions…"></label><div class="favorites-grid"></div><p class="favorites-error" role="alert"></p><div class="favorites-footer"><span role="status"></span><button type="button" class="primary" data-favorites-add>Add references</button></div>`;
    doc.body.append(picker);
    picker.querySelector('[data-favorites-close]').addEventListener('click',()=>{if(!busy)picker.close();});
    picker.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
    picker.addEventListener('close',()=>{loadEpoch++;origin?.focus();});
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
          const file=new File([bytes],result.name,{type:'text/html'});
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
  const observer=new ResizeObserver(records=>{for(const record of records){const frame=record.target.querySelector('iframe');if(frame)frame.style.transform=`scale(${record.contentRect.width/1280})`;}});
  function render(){
    observer.disconnect();const query=picker.querySelector('input').value.toLocaleLowerCase().trim();
    const matches=entries.filter(item=>`${item.title} ${item.deck_title} ${item.description||''}`.toLocaleLowerCase().includes(query));
    picker.querySelector('.favorites-grid').innerHTML=matches.length?matches.map(item=>{
      const key=item.deck_id+'/'+item.slide_id,url=`/?deck=${encodeURIComponent(item.deck_id)}#${encodeURIComponent(item.slide_id)}`;
      const body=`<span class="favorite-cover"><iframe src="/api/decks/${encodeURIComponent(item.deck_id)}/slides/${encodeURIComponent(item.slide_id)}/preview" title="Preview of ${escape(item.title)}" aria-hidden="true" tabindex="-1" loading="lazy" sandbox></iframe><span class="favorite-check" aria-hidden="true">${root.SlideIcons.svg('check')}</span></span><span class="favorite-caption"><strong>${escape(item.title)}</strong><small>${escape(item.deck_title)} · ${String(item.number).padStart(2,'0')}</small>${item.reference_error?`<small class="favorites-error">${escape(item.reference_error)}</small>`:''}</span>`;
      return `<article class="favorite-card"><div class="favorite-main">${attach?`<button type="button" class="favorite-pick" data-favorite-pick="${escape(key)}" aria-pressed="${chosen.has(key)}" aria-label="Reference ${escape(item.title)} from ${escape(item.deck_title)}">${body}</button>`:`<a class="favorite-pick" href="${url}">${body}</a>`}<div class="favorite-card-tools"><a href="${url}" target="_blank" rel="noopener" title="Open original slide" aria-label="Open ${escape(item.title)} in presentation">${root.SlideIcons.svg('external')}</a>${button('slide',item.deck_id,item.title,item.slide_id)}</div></div><button type="button" class="favorite-description${item.description?' has-description':''}" data-favorite-description="${escape(key)}" aria-label="${item.description?'Edit':'Add'} description for ${escape(item.title)}" title="${item.description?'Edit':'Add'} description">${root.SlideIcons.svg('edit')}<span>${escape(item.description||'Add description')}</span></button></article>`;
    }).join(''):`<div class="favorites-empty">${icon()}<h3>${query?'No matching slides':'Keep your best slides handy'}</h3><p>${query?'Try another title or presentation.':'Star a slide in any presentation to find it here.'}</p></div>`;
    for(const cover of picker.querySelectorAll('.favorite-cover'))observer.observe(cover);
    footer();
  }
  async function load(){const request=++loadEpoch;try{const items=await api('/api/favorites/slides');if(request!==loadEpoch||!picker.open)return;entries=items;const keys=new Set(items.map(item=>item.deck_id+'/'+item.slide_id));chosen=new Set([...chosen].filter(key=>keys.has(key)));render();}catch(reason){if(request===loadEpoch)error(reason.message);}}
  async function open(options={}){
    ensurePicker();origin=doc.activeElement;attach=options.attach||null;chosen.clear();entries=[];
    picker.querySelector('input').value='';picker.querySelector('#favorites-help').textContent=attach?'Choose favorites from any presentation.':'Your starred slides, across presentations.';
    error('');picker.querySelector('.favorites-grid').textContent='Loading…';footer();picker.showModal();picker.querySelector('input').focus();await load();
  }
  root.FavoriteSlides={has,button,refresh,sync,open,onChange(listener){listeners.add(listener);}};
})(window);
