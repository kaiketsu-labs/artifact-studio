/* Typed @ paths resolve to stable workspace IDs and frozen feedback attachments. */
((root)=>{
  'use strict';
  const doc=root.document, bindings=new Map();
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const draftKeys=new WeakMap(),pillSources=new Map(),pillModels=new Map(),previewDownloads=new Map();
  let nextPill=0,pillPreview=null,pillAnchor=null,pillTimer=null,pillCloseTimer=null,pillRequest=0,renderedPill='',touchFocus=false,pillBox=null,pillOpen=false,framePointerExit=false,pillBridgeActive=false;
  const PREVIEW_SOURCE_LIMIT=8000000,PREVIEW_HTML_LIMIT=12000000,PREVIEW_CACHE_LIMIT=24000000;
  let active=null,pending=null,entries=[],matches=[],selected=0,epoch=0,busy=false,loading=false,loaded=false,menu=null,preview=null,hoverTimer=null,coverLoader=null;
  function fragment(input){
    if(input.selectionStart!==input.selectionEnd)return null;
    const end=input.selectionStart,line=input.value.slice(0,end);
    let start=line.lastIndexOf('@');
    if(active?.input===input&&line[active.part.start]==='@'&&!line.slice(active.part.start).includes('\n'))start=active.part.start;
    if(start<0 || (start && !/[\s([{]/.test(line[start-1])))return null;
    const query=line.slice(start+1);
    if(query.includes('\n') || query.length>500 || query.startsWith('"'))return null;
    for(const token of input.value.matchAll(/@"(?:\\.|[^"\\])*"/g))if(start>token.index&&start<token.index+token[0].length)return null;
    return {start,end,query};
  }
  async function api(body){
    const response=await fetch('/api/chat-references',body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
    if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(typeof data.detail==='string'?data.detail:'Could not load references. Type @ to retry.');}
    return response.json();
  }
  function ensure(){
    if(menu)return;
    menu=doc.createElement('div');menu.className='chat-reference-menu';menu.hidden=true;menu.setAttribute('role','region');menu.setAttribute('aria-label','Reference suggestions');
    menu.innerHTML='<div id="chat-reference-options" role="listbox" aria-label="Workspace references"></div><div class="chat-reference-status" role="status"></div>';
    preview=doc.createElement('div');preview.className='chat-reference-preview';preview.hidden=true;preview.setAttribute('role','region');preview.setAttribute('aria-label','Reference preview');
    doc.body.append(menu,preview);
    menu.addEventListener('pointerdown',event=>event.preventDefault());
    menu.addEventListener('click',event=>{const row=event.target.closest('[data-reference-index]');if(row)void choose(Number(row.dataset.referenceIndex));});
    menu.addEventListener('pointermove',event=>{
      const row=event.target.closest('[data-reference-index]');if(!row || busy || event.pointerType==='touch')return;
      const index=Number(row.dataset.referenceIndex);
      selected=index;highlight();
      if(preview.dataset.key===String(index)&&!preview.hidden){placePreview(event.clientX,event.clientY);return;}
      clearTimeout(hoverTimer);hoverTimer=setTimeout(()=>showPreview(index,event.clientX,event.clientY),180);
    });
    menu.addEventListener('pointerleave',hidePreview);
  }
  function hidePreview(){clearTimeout(hoverTimer);coverLoader?.clear();if(preview){preview.hidden=true;preview.dataset.key='';preview.replaceChildren();}}
  function close(){
    epoch++;hidePreview();if(menu)menu.hidden=true;
    if(active)active.input.removeAttribute('aria-activedescendant');
    active=null;
  }
  function position(){
    if(!active||menu.hidden)return;
    const rect=active.input.getBoundingClientRect(),width=Math.min(520,Math.max(300,rect.width),root.innerWidth-24);
    menu.style.width=width+'px';menu.style.left=Math.max(12,Math.min(rect.left,root.innerWidth-width-12))+'px';
    const height=Math.min(menu.offsetHeight,root.innerHeight-24);
    menu.style.top=Math.max(12,rect.top-height-8)+'px';
  }
  function placePreview(x,y){
    const width=preview.offsetWidth,height=preview.offsetHeight;
    preview.style.left=Math.max(12,Math.min(x+16+width>root.innerWidth?x-width-16:x+16,root.innerWidth-width-12))+'px';
    preview.style.top=Math.max(12,Math.min(y+18+height>root.innerHeight?y-height-18:y+18,root.innerHeight-height-12))+'px';
  }
  function showPreview(index,x,y){
    if(!active || busy || !matches[index] || root.innerWidth<640)return;
    const item=matches[index];hidePreview();preview.dataset.key=String(index);
    const visual=item.preview_url?(item.preview_kind==='slide'?`<div class="chat-reference-frame"><iframe sandbox tabindex="-1" aria-hidden="true" src="${escape(item.preview_url)}"></iframe></div>`:`<div class="chat-reference-cover" data-cover-container><img hidden data-cover-url="${escape(item.preview_url)}" data-cover-fallback="${escape(item.preview_fallback_url)}" data-cover-cached="${item.preview_cached===true}" data-cover-renderer="${item.preview_renderer!==false}" alt="Preview of ${escape(item.title)}"></div>`):'';
    preview.innerHTML=`${visual}<div class="chat-reference-preview-caption"><strong>${escape(item.title)}</strong><small>${escape(item.detail)}</small>${item.description?`<p>${escape(item.description)}</p>`:''}</div>`;
    preview.hidden=false;placePreview(x,y);
    const img=preview.querySelector('img');
    if(img){coverLoader ||= root.HubCovers.create();img.addEventListener('error',()=>coverLoader.imageFailed(img));coverLoader.mount(preview);}
  }
  function highlight(){
    for(const row of menu.querySelectorAll('[data-reference-index]'))row.setAttribute('aria-selected',String(Number(row.dataset.referenceIndex)===selected));
    if(active&&matches.length)active.input.setAttribute('aria-activedescendant',`chat-reference-option-${selected}`);
    else active?.input.removeAttribute('aria-activedescendant');
  }
  function render(query){
    const normalized=query.trim().toLocaleLowerCase(),parts=normalized.split(/[\s/]+/).filter(Boolean);
    matches=entries.filter(item=>normalized.endsWith('/')?item.path.toLocaleLowerCase().startsWith(normalized.startsWith('/')?normalized:'/'+normalized):parts.every(part=>`${item.path} ${item.description}`.toLocaleLowerCase().includes(part)))
      .sort((a,b)=>(b.starred-a.starred)||((a.kind==='project'?0:1)-(b.kind==='project'?0:1))||a.path.localeCompare(b.path)).slice(0,30);
    selected=0;hidePreview();
    menu.querySelector('[role=listbox]').innerHTML=matches.map((item,index)=>{
      const duplicate=matches.some(other=>other!==item&&other.path===item.path),identity=[item.project_id,item.artifact_id,item.slide_id].filter(Boolean).join('/');
      return `<button type="button" role="option" tabindex="-1" id="chat-reference-option-${index}" data-reference-index="${index}" aria-selected="${index===0}" aria-label="${escape(item.path)}${duplicate?' · '+escape(identity):''}"><span class="chat-reference-kind" aria-hidden="true">${item.starred?'★':item.kind==='project'?'▸':'@'}</span><span class="chat-reference-row-text"><strong>${escape(item.title)}</strong><small>${escape(item.path)}</small></span><span class="chat-reference-type">${escape(item.kind)}${duplicate?'<br>'+escape(identity):''}</span></button>`;
    }).join('');
    menu.querySelector('[role=status]').textContent=matches.length?'↑ ↓ select · Enter / Tab attach · → browse':'No matching references. Try another name or path.';
    highlight();position();
  }
  async function update(input){
    if(busy)return;
    const binding=bindings.get(input.id),part=fragment(input);
    if(!binding||!part||input.disabled||input.readOnly){if(active?.input===input)close();return;}
    ensure();
    if(active?.input===input && (loaded||loading)){active.part=part;if(loaded)render(part.query);return;}
    close();const request=++epoch;active={input,binding,part,context:binding.key()};entries=[];matches=[];loaded=false;loading=true;
    menu.hidden=false;menu.querySelector('[role=listbox]').replaceChildren();menu.querySelector('[role=status]').textContent='Loading references…';position();
    try{const data=await api();if(request!==epoch||!input.isConnected||active?.input!==input)return;entries=data;loaded=true;const current=fragment(input);if(!current){close();return;}active.part=current;render(current.query);}
    catch(error){if(request===epoch){menu.querySelector('[role=status]').textContent=error.message;position();}}
    finally{if(request===epoch)loading=false;}
  }
  async function choose(index){
    if(!active||busy||!matches[index])return;
    const current=active,{input,binding,part,context}=current,item=matches[index],original=input.value;
    if(item.reference_error){menu.querySelector('[role=status]').textContent=item.reference_error;return;}
    busy=true;pending=current;hidePreview();input.readOnly=true;menu.querySelector('[role=status]').textContent='Adding reference…';
    try{
      const result=await api({kind:item.kind,project_id:item.project_id,artifact_id:item.artifact_id,slide_id:item.slide_id,token:item.token});
      if(active!==current||!input.isConnected||binding.key()!==context||input.value!==original)return;
      const bytes=Uint8Array.from(atob(result.file.data_base64),c=>c.charCodeAt(0));
      const file=new File([bytes],result.file.name,{type:result.file.name.endsWith('.html')?'text/html':'application/json'});file.workspaceReference=result.entry;
      if(binding.attach([file])===false){menu.querySelector('[role=status]').textContent='Could not attach this reference. Check the attachment limits.';return;}
      input.setRangeText('',part.start,part.end,'end');close();input.readOnly=false;input.focus({preventScroll:true});input.dispatchEvent(new Event('input',{bubbles:true}));
    }catch(error){if(active===current){entries=[];matches=[];loaded=false;menu.querySelector('[role=listbox]').replaceChildren();menu.querySelector('[role=status]').textContent=error.message;position();}}
    finally{input.readOnly=false;busy=false;pending=null;}
  }
  function browse(){
    const item=matches[selected];if(!active||!item||!['project','presentation'].includes(item.kind))return false;
    const {input,part}=active;input.setRangeText('@'+item.path+'/',part.start,part.end,'end');input.dispatchEvent(new Event('input',{bubbles:true}));return true;
  }
  doc.addEventListener('input',event=>{if(bindings.has(event.target.id)&&!event.isComposing)void update(event.target);});
  doc.addEventListener('click',event=>{if(bindings.has(event.target.id))void update(event.target);});
  doc.addEventListener('compositionend',event=>{if(bindings.has(event.target.id))void update(event.target);});
  doc.addEventListener('keydown',event=>{
    if(!active||event.target!==active.input||event.isComposing||event.keyCode===229)return;
    if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();close();return;}
    if(busy&&event.key==='Enter'){event.preventDefault();event.stopImmediatePropagation();return;}
    if(event.key==='ArrowDown'||event.key==='ArrowUp'){
      if(!matches.length)return;event.preventDefault();event.stopImmediatePropagation();
      selected=(selected+(event.key==='ArrowDown'?1:-1)+matches.length)%matches.length;highlight();
      const row=doc.getElementById(`chat-reference-option-${selected}`);row.scrollIntoView({block:'nearest'});const box=menu.getBoundingClientRect();showPreview(selected,box.right,box.top+20);return;
    }
    if(event.key==='ArrowRight'&&!busy&&active.input.selectionStart===active.part.end&&browse()){event.preventDefault();event.stopImmediatePropagation();return;}
    if(event.key==='Tab'&&!event.shiftKey&&matches.length){event.preventDefault();event.stopImmediatePropagation();if(!busy)void choose(selected);return;}
    if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();event.stopImmediatePropagation();if(matches.length)void choose(selected);return;}
    if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key))close();
  },true);
  doc.addEventListener('submit',event=>{if(busy&&pending?.input.form===event.target&&pending.binding.key()===pending.context){event.preventDefault();event.stopImmediatePropagation();}},true);
  doc.addEventListener('pointerdown',event=>{if(active&&event.target!==active.input&&!menu.contains(event.target))close();},true);
  doc.addEventListener('focusin',event=>{if(active&&event.target!==active.input&&!menu.contains(event.target))close();});
  root.addEventListener('resize',()=>{hidePreview();position();});
  function chip(file,remove,className='chat-file-chip'){
    const item=file.workspaceReference||file.slideReference;if(!item)return '';
    if(!draftKeys.has(file))draftKeys.set(file,'draft-'+(++nextPill));
    const key=draftKeys.get(file);pillSources.set(key,pillSources.get(key)||{file,title:item.title});
    return `<span class="chat-reference-slot"><span class="${className} is-path-reference${file.slideReference?' is-slide-reference':''}" data-reference-preview="${key}" tabindex="0" aria-expanded="false" aria-label="Preview saved reference: ${escape(item.title)}"><span class="chat-reference-bar"><span class="reference-symbol" aria-hidden="true">@</span><span class="chat-file-name">${escape(item.title)}</span>${remove}</span></span></span>`;
  }
  function sent(file,className='chat-file-chip'){
    const match=/^(.+) - (project|artifact|presentation|slide) reference\.(json|html)$/.exec(file.name||'');
    const url=file.download_url;if(!match||!root.ChatImages.downloadURL(url))return '';
    pillSources.set(url,pillSources.get(url)||{url,title:match[1]});
    return `<span class="chat-reference-slot"><a class="${className} is-path-reference" data-reference-preview="${escape(url)}" href="${escape(url)}" download aria-expanded="false" aria-label="Download saved ${escape(match[2])} reference: ${escape(match[1])}"><span class="chat-reference-bar"><span class="reference-symbol" aria-hidden="true">@</span><span class="chat-file-name">${escape(match[1])}</span></span></a></span>`;
  }
  // Render only the selected attachment's frozen bytes, never a mutable workspace URL.
  function frozenDocument(source,files=[],entrypoint=''){
    const assets=new Map(files.map(file=>[file.path,file])),types={png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',gif:'image/gif',webp:'image/webp',svg:'image/svg+xml',avif:'image/avif',woff:'font/woff',woff2:'font/woff2'};
    let expanded=0;
    const encode=text=>{let binary='';for(const byte of new TextEncoder().encode(text))binary+=String.fromCharCode(byte);return btoa(binary);};
    function resolve(value,base){
      try{const url=new URL(value,'https://saved.invalid/'+base);return url.origin==='https://saved.invalid'?decodeURIComponent(url.pathname.slice(1)):'';}catch{return '';}
    }
    function asset(value,base,seen=new Set()){
      if(value.startsWith('data:'))return value;
      const path=resolve(value,base),file=assets.get(path);if(!file||seen.has(path))return '';
      expanded+=(file.data_base64?.length||file.text?.length||0);if(expanded>PREVIEW_SOURCE_LIMIT)return '';
      const extension=path.split('.').pop().toLowerCase();
      if(extension==='css')return 'data:text/css;base64,'+encode(css(file.text||'',path,new Set([...seen,path])));
      return 'data:'+(types[extension]||'application/octet-stream')+';base64,'+(file.data_base64||encode(file.text||''));
    }
    function css(text,base,seen=new Set()){
      return text.replace(/url\(\s*(['"]?)(.*?)\1\s*\)/gi,(_all,_quote,value)=>`url("${asset(value,base,seen)}")`)
        .replace(/@import\s+(?:url\([^)]*\)|['"][^'"]*['"])\s*[^;]*;/gi,'');
    }
    function rootAttributes(tag){
      const match=source.match(new RegExp('<'+tag+'\\b([^>]*)>','i'));if(!match)return '';
      const holder=doc.createElement('template');holder.innerHTML='<span '+match[1]+'></span>';
      return [...(holder.content.firstElementChild?.attributes||[])].filter(attribute=>['class','style','lang','dir'].includes(attribute.name)||attribute.name.startsWith('data-'))
        .map(attribute=>' '+attribute.name+'="'+escape(attribute.name==='style'?css(attribute.value,entrypoint):attribute.value)+'"').join('');
    }
    const htmlAttributes=rootAttributes('html'),bodyAttributes=rootAttributes('body');
    const template=doc.createElement('template');template.innerHTML=source;
    const deckData=template.content.querySelector('script#deck-data');
    if(deckData){
      let deck;try{deck=JSON.parse(deckData.textContent);}catch{throw new Error('Saved presentation metadata is invalid.');}
      if(!Array.isArray(deck?.slides)||typeof deck.slides[0]?.html!=='string')throw new Error('Saved presentation has no slide preview.');
      const styles=[...template.content.querySelectorAll('style')].map(node=>node.outerHTML).join('');
      template.innerHTML=styles+'<main id="capture-stage" class="stage-slot">'+deck.slides[0].html+'</main><style>html,body{width:1600px;height:900px;margin:0;overflow:hidden}#capture-stage{position:relative;width:1600px;height:900px;container-type:inline-size;box-shadow:none}#capture-stage>.slide{position:relative;width:100%;height:100%;box-sizing:border-box;overflow:hidden}</style>';
    }
    template.content.querySelectorAll('script,iframe,object,embed,base,meta,form').forEach(node=>node.remove());
    for(const node of template.content.querySelectorAll('*')){
      for(const attribute of [...node.attributes])if(/^on/i.test(attribute.name)||['srcset','action','formaction'].includes(attribute.name)||['href','xlink:href'].includes(attribute.name)&&node.tagName!=='LINK'&&!attribute.value.startsWith('#'))node.removeAttribute(attribute.name);
      if(node.hasAttribute('src'))node.setAttribute('src',asset(node.getAttribute('src'),entrypoint));
      if(node.hasAttribute('poster'))node.setAttribute('poster',asset(node.getAttribute('poster'),entrypoint));
      if(node.hasAttribute('style'))node.setAttribute('style',css(node.getAttribute('style'),entrypoint));
      if(node.tagName==='STYLE')node.textContent=css(node.textContent,entrypoint).replace(/</g,'\\3c ');
      if(node.tagName==='LINK'){
        const file=assets.get(resolve(node.getAttribute('href')||'',entrypoint));
        if(node.rel==='stylesheet'&&file?.text){const style=doc.createElement('style');style.textContent=css(file.text,file.path).replace(/</g,'\\3c ');node.replaceWith(style);}else node.remove();
      }
    }
    return '<!doctype html><html'+htmlAttributes+'><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; media-src data:; font-src data:; style-src \'unsafe-inline\' data:; script-src \'none\'; base-uri \'none\'; form-action \'none\'"><style>html,body{margin:0}body{overflow:hidden}.slide~.slide{display:none!important}</style></head><body'+bodyAttributes+'>'+template.innerHTML+'</body></html>';
  }
  async function frozenPreview(record){
    const key=record.file||record.url;
    if(pillModels.has(key)){const cached=pillModels.get(key);pillModels.delete(key);pillModels.set(key,cached);return cached.promise;}
    const cached={bytes:0,promise:null};
    cached.promise=(async()=>{
      let text;
      if(record.file){if(record.file.size>PREVIEW_SOURCE_LIMIT)throw new Error('Preview size limit.');text=await record.file.text();}
      else{
        while(previewDownloads.size>=2){const oldest=previewDownloads.keys().next().value;previewDownloads.get(oldest).abort();previewDownloads.delete(oldest);}
        const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);previewDownloads.set(key,controller);
        try{
          const response=await fetch(record.url,{signal:controller.signal});if(!response.ok)throw new Error('Saved reference preview unavailable.');
          if(Number(response.headers.get('Content-Length'))>PREVIEW_SOURCE_LIMIT){await response.body?.cancel();throw new Error('Preview size limit.');}
          const reader=response.body.getReader(),chunks=[];let length=0;
          try{while(true){const {done,value}=await reader.read();if(done)break;length+=value.byteLength;if(length>PREVIEW_SOURCE_LIMIT){await reader.cancel();throw new Error('Preview size limit.');}chunks.push(value);}}
          finally{reader.releaseLock();}
          text=await new Blob(chunks).text();
        }finally{clearTimeout(timeout);if(previewDownloads.get(key)===controller)previewDownloads.delete(key);}
      }
      let data;try{data=JSON.parse(text);}catch{return {html:frozenDocument(text),caption:text.includes('id="deck-data"')?'Saved presentation · First slide · Static preview':'Saved reference · Static preview',width:1600,height:900};}
      if(!data||typeof data!=='object'||Array.isArray(data)||data.format!=='artifact-studio-reference/v1')return {summary:'Saved source available to download.',caption:'Saved reference'};
      const context=typeof data.context==='string'?data.context:'';
      if(data.kind==='project')return {summary:(context||'Project brief and artifact index.').slice(0,2000),items:(Array.isArray(data.artifacts)?data.artifacts:[]).filter(item=>typeof item?.title==='string').map(item=>item.title).slice(0,8),caption:'Saved project brief'};
      const files=(Array.isArray(data.files)?data.files:[]).filter(file=>file&&typeof file.path==='string'&&(typeof file.text==='string'||typeof file.data_base64==='string')),entry=files.find(file=>file.path===data.entrypoint);
      if(entry&&/\.html?$/i.test(entry.path)&&typeof entry.text==='string')return {html:frozenDocument(entry.text,files,entry.path),caption:'Saved artifact · Static preview',width:1280,height:800};
      if(entry&&(entry.data_base64||/\.svg$/i.test(entry.path)&&entry.text)&&/\.(png|jpe?g|gif|webp|svg|avif)$/i.test(entry.path))return {html:frozenDocument('<img style="width:100vw;height:100vh;object-fit:contain" src="'+escape(entry.path)+'">',files,''),caption:'Saved image',width:1280,height:800};
      return {summary:(entry?.text||context||'Saved source available to download.').slice(0,2000),caption:'Saved artifact source'};
    })().then(model=>{
      if((model.html?.length||0)>PREVIEW_HTML_LIMIT)model={summary:'Saved source available to download.',caption:'Saved reference · Thumbnail unavailable'};
      cached.bytes=2*((model.html?.length||0)+(model.summary?.length||0));
      while(pillModels.size>1&&[...pillModels.values()].reduce((sum,item)=>sum+item.bytes,0)>PREVIEW_CACHE_LIMIT)pillModels.delete(pillModels.keys().next().value);
      return model;
    }).catch(error=>{if(pillModels.get(key)===cached)pillModels.delete(key);throw error;});
    pillModels.set(key,cached);while(pillModels.size>4)pillModels.delete(pillModels.keys().next().value);
    return cached.promise;
  }
  // The original pill enters the top layer while its slot preserves chat layout.
  // Keeping the real title/remove/download controls avoids cloned interactive UI.
  function setReferencePointer(active){
    if(pillBridgeActive===active)return;
    pillBridgeActive=active;doc.dispatchEvent(new CustomEvent('studio:reference-preview',{detail:active}));
  }
  function releasePill(clear=false){
    clearTimeout(pillCloseTimer);pillCloseTimer=null;
    if(pillAnchor){
      const anchor=pillAnchor,slot=anchor.parentElement;
      anchor.removeAttribute('aria-describedby');anchor.setAttribute('aria-expanded','false');
      if(anchor.matches(':popover-open'))anchor.hidePopover();
      anchor.removeAttribute('popover');anchor.classList.remove('is-reference-unfolding');
      anchor.removeAttribute('data-open');anchor.removeAttribute('data-direction');anchor.removeAttribute('style');
      if(slot?.classList.contains('chat-reference-slot'))slot.removeAttribute('style');
    }
    if(pillPreview){pillPreview.hidden=true;doc.body.append(pillPreview);}
    pillAnchor=null;pillBox=null;pillOpen=false;
    if(clear&&pillPreview){pillPreview.replaceChildren();renderedPill='';}
  }
  function hidePillPreview(clear=false,immediate=false){
    clearTimeout(pillTimer);pillTimer=null;pillRequest++;pillOpen=false;
    setReferencePointer(false);
    if(!pillAnchor){if(clear)releasePill(true);return;}
    pillAnchor.setAttribute('aria-expanded','false');pillAnchor.dataset.open='false';
    if(clear||immediate||root.matchMedia('(prefers-reduced-motion: reduce)').matches){releasePill(clear);return;}
    pillAnchor.style.width=pillBox.width+'px';pillAnchor.style.height=pillBox.height+'px';
    clearTimeout(pillCloseTimer);pillCloseTimer=setTimeout(()=>releasePill(),300);
  }
  function mountPill(anchor){
    clearTimeout(pillCloseTimer);pillCloseTimer=null;
    if(pillAnchor===anchor&&pillBox)return;
    releasePill();pillAnchor=anchor;pillBox=anchor.getBoundingClientRect();
    const slot=anchor.parentElement;
    slot.style.width=pillBox.width+'px';slot.style.height=pillBox.height+'px';
    anchor.classList.add('is-reference-unfolding');anchor.dataset.open='false';
    anchor.style.width=pillBox.width+'px';anchor.style.height=pillBox.height+'px';
    anchor.style.setProperty('--reference-bar-height',pillBox.height+'px');
    anchor.style.left=pillBox.left+'px';
    // Prefer unfolding upward. Near the top edge, unfold into the available space.
    const down=pillBox.top<90&&root.innerHeight-pillBox.bottom>pillBox.top;
    anchor.dataset.direction=down?'down':'up';
    if(down)anchor.style.top=pillBox.top+'px';else anchor.style.bottom=(root.innerHeight-pillBox.bottom)+'px';
    if(anchor.showPopover){anchor.setAttribute('popover','manual');anchor.showPopover();}
  }
  function expandPill(model){
    const anchor=pillAnchor,box=pillBox;if(!anchor||!box)return;
    const width=Math.max(box.width,Math.min(320,root.innerWidth-box.left-12));
    const available=anchor.dataset.direction==='down'?root.innerHeight-box.bottom-12:box.top-12;
    const height=Math.max(0,Math.min(model.html?model.height*width/model.width:220,available));
    if(model.html){
      const frame=pillPreview.querySelector('iframe'),scale=Math.min(width/model.width,height/model.height);
      frame.style.transform=`scale(${scale})`;frame.style.left=(width-model.width*scale)/2+'px';frame.style.top=(height-model.height*scale)/2+'px';
    }
    anchor.style.setProperty('--reference-preview-height',height+'px');
    void anchor.offsetHeight;
    anchor.style.width=width+'px';anchor.style.height=(height+box.height)+'px';
    anchor.dataset.open='true';anchor.setAttribute('aria-expanded','true');pillOpen=true;
  }
  async function showPillPreview(anchor){
    const key=anchor.dataset.referencePreview,record=pillSources.get(key);if(!record||!anchor.isConnected)return;
    framePointerExit=false;
    setReferencePointer(true);
    clearTimeout(pillTimer);clearTimeout(pillCloseTimer);hidePreview();
    if(pillAnchor&&pillAnchor!==anchor)releasePill();
    const request=++pillRequest;
    if(!pillPreview){pillPreview=doc.createElement('div');pillPreview.id='chat-saved-reference-preview';pillPreview.className='chat-reference-unfold';pillPreview.setAttribute('role','tooltip');pillPreview.hidden=true;doc.body.append(pillPreview);}
    try{
      const model=await frozenPreview(record);if(request!==pillRequest||!anchor.isConnected)return;
      if(renderedPill!==key){
        pillPreview.replaceChildren();
        if(model.html){
          const frame=doc.createElement('iframe');frame.setAttribute('sandbox','');frame.tabIndex=-1;frame.title='Saved reference thumbnail: '+record.title;frame.style.width=model.width+'px';frame.style.height=model.height+'px';frame.srcdoc=model.html;
          const wrap=doc.createElement('div');wrap.className='chat-reference-frame';wrap.append(frame);pillPreview.append(wrap);
        }
        const caption=doc.createElement('div');caption.className='chat-reference-preview-caption';
        if(model.html)caption.classList.add('reference-preview-description');
        const detail=doc.createElement('small');detail.textContent=model.caption;caption.append(detail);
        if(model.summary){const summary=doc.createElement('p');summary.textContent=model.summary.slice(0,2000);caption.append(summary);}
        if(model.items){const list=doc.createElement('p');list.textContent=model.items.join(' · ');caption.append(list);}
        pillPreview.append(caption);renderedPill=key;
      }
      mountPill(anchor);anchor.prepend(pillPreview);pillPreview.hidden=false;anchor.setAttribute('aria-describedby',pillPreview.id);expandPill(model);
    }catch{
      if(request!==pillRequest||!anchor.isConnected)return;
      mountPill(anchor);pillPreview.textContent='Saved reference preview unavailable. Download the reference to inspect it.';renderedPill='';anchor.prepend(pillPreview);pillPreview.hidden=false;anchor.setAttribute('aria-describedby',pillPreview.id);expandPill({});
    }
  }
  function resumePillPreview(anchor){
    if(anchor===pillAnchor){
      // Hold the current hit area while a closing preview reverses or retries.
      // Otherwise it can shrink out from under the pointer before loading ends.
      const box=anchor.getBoundingClientRect();anchor.style.width=box.width+'px';anchor.style.height=box.height+'px';anchor.dataset.open='true';
    }
    void showPillPreview(anchor);
  }
  doc.addEventListener('pointerover',event=>{
    const anchor=event.target.closest?.('[data-reference-preview]');if(!anchor||anchor.contains(event.relatedTarget)||event.pointerType==='touch')return;
    clearTimeout(pillTimer);clearTimeout(pillCloseTimer);
    setReferencePointer(true);
    if(anchor===pillAnchor)resumePillPreview(anchor);else pillTimer=setTimeout(()=>void showPillPreview(anchor),70);
  });
  doc.addEventListener('pointermove',event=>{
    // Chromium can retain the parent's hover target across an opaque iframe.
    // On return, pointermove still arrives even when pointerover is omitted.
    if(!framePointerExit||event.pointerType==='touch')return;
    const anchor=event.target.closest?.('[data-reference-preview]');if(!anchor)return;
    clearTimeout(pillTimer);clearTimeout(pillCloseTimer);resumePillPreview(anchor);
  });
  doc.addEventListener('pointerout',event=>{
    const anchor=event.target.closest?.('[data-reference-preview]');if(!anchor||anchor.contains(event.relatedTarget)||event.pointerType==='touch')return;
    clearTimeout(pillTimer);pillRequest++;
    pillTimer=setTimeout(()=>hidePillPreview(),80);
  });
  doc.addEventListener('pointerdown',event=>{
    touchFocus=event.pointerType==='touch';
    if(pillAnchor&&!pillAnchor.contains(event.target))hidePillPreview();
  });
  doc.addEventListener('click',event=>{
    const anchor=event.target.closest?.('[data-reference-preview]');if(!anchor||event.target.closest('button'))return;
    if(touchFocus){
      if(anchor===pillAnchor&&pillOpen&&anchor.tagName==='A')return; // A second tap downloads the saved source.
      event.preventDefault();if(anchor===pillAnchor&&pillOpen)hidePillPreview();else void showPillPreview(anchor);
    }
  });
  doc.addEventListener('focusin',event=>{const anchor=event.target.closest?.('[data-reference-preview]');if(anchor&&!touchFocus)void showPillPreview(anchor);});
  doc.addEventListener('focusout',event=>{const anchor=event.target.closest?.('[data-reference-preview]');if(anchor&&!anchor.contains(event.relatedTarget))hidePillPreview();});
  doc.addEventListener('keydown',event=>{
    touchFocus=false;
    if(event.key==='Escape'&&pillAnchor){event.preventDefault();event.stopImmediatePropagation();hidePillPreview();return;}
    const anchor=event.target.closest?.('[data-reference-preview]');
    if(anchor&&event.target===anchor&&anchor.tagName!=='A'&&['Enter',' '].includes(event.key)){event.preventDefault();if(pillOpen&&pillAnchor===anchor)hidePillPreview();else void showPillPreview(anchor);}
  },true);
  root.addEventListener('resize',()=>hidePillPreview(false,true));
  doc.addEventListener('studio:preview-pointer-enter',()=>{framePointerExit=true;hidePillPreview();});
  root.addEventListener('scroll',event=>{if(!pillPreview?.contains(event.target))hidePillPreview(false,true);},true);
  doc.addEventListener('visibilitychange',()=>{if(doc.hidden)hidePillPreview(false,true);});
  new MutationObserver(()=>{
    if(pillAnchor&&!pillAnchor.isConnected)hidePillPreview(true,true);
    const live=new Set([...doc.querySelectorAll('[data-reference-preview]')].map(node=>node.dataset.referencePreview));
    if(renderedPill&&!live.has(renderedPill))hidePillPreview(true,true);
    for(const [key,record] of pillSources)if(!live.has(key)){pillSources.delete(key);pillModels.delete(record.file||record.url);}
  }).observe(doc.documentElement,{childList:true,subtree:true});
  function register(options){bindings.set(options.inputId,options);}
  doc.addEventListener('focusin',event=>{if(bindings.has(event.target.id)){event.target.setAttribute('aria-autocomplete','list');event.target.setAttribute('aria-controls','chat-reference-options');event.target.setAttribute('aria-haspopup','listbox');ensure();void update(event.target);}});
  root.ChatReferences={register,chip,sent};
})(window);
