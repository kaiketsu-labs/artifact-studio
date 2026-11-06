/* Typed @ paths resolve to stable workspace IDs and frozen feedback attachments. */
((root)=>{
  'use strict';
  const doc=root.document, bindings=new Map();
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
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
    menu.querySelector('[role=status]').textContent=matches.length?'↑ ↓ select · Enter attach · Tab browse':'No matching references. Try another name or path.';
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
    const token='@'+JSON.stringify(item.path)+' ';
    if(input.maxLength>0&&original.length-(part.end-part.start)+token.length>input.maxLength){menu.querySelector('[role=status]').textContent='Shorten the message before adding a reference.';return;}
    busy=true;pending=current;hidePreview();input.readOnly=true;menu.querySelector('[role=status]').textContent='Adding reference…';
    try{
      const result=await api({kind:item.kind,project_id:item.project_id,artifact_id:item.artifact_id,slide_id:item.slide_id,token:item.token});
      if(active!==current||!input.isConnected||binding.key()!==context||input.value!==original)return;
      const bytes=Uint8Array.from(atob(result.file.data_base64),c=>c.charCodeAt(0));
      const file=new File([bytes],result.file.name,{type:result.file.name.endsWith('.html')?'text/html':'application/json'});file.workspaceReference=result.entry;
      if(binding.attach([file])===false){menu.querySelector('[role=status]').textContent='Could not attach this reference. Check the attachment limits.';return;}
      input.setRangeText(token,part.start,part.end,'end');close();input.readOnly=false;input.focus({preventScroll:true});input.dispatchEvent(new Event('input',{bubbles:true}));
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
    if(event.key==='Tab'&&!event.shiftKey&&matches.length&&!busy){if(browse()){event.preventDefault();event.stopImmediatePropagation();}else close();return;}
    if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();event.stopImmediatePropagation();if(matches.length)void choose(selected);return;}
    if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key))close();
  },true);
  doc.addEventListener('submit',event=>{if(busy&&pending?.input.form===event.target&&pending.binding.key()===pending.context){event.preventDefault();event.stopImmediatePropagation();}},true);
  doc.addEventListener('pointerdown',event=>{if(active&&event.target!==active.input&&!menu.contains(event.target))close();},true);
  doc.addEventListener('focusin',event=>{if(active&&event.target!==active.input&&!menu.contains(event.target))close();});
  root.addEventListener('resize',()=>{hidePreview();position();});
  function chip(file,remove,className='chat-file-chip'){
    const item=file.workspaceReference;if(!item)return '';
    return `<span class="${className} is-path-reference"><span class="reference-symbol" aria-hidden="true">@</span><span class="chat-file-description"><span class="chat-file-name">${escape(item.title)}</span><small class="favorite-reference-label" title="${escape(item.path)}">${escape(item.path)}</small>${item.description?`<small class="favorite-reference-description">${escape(item.description)}</small>`:''}</span>${remove}</span>`;
  }
  function register(options){bindings.set(options.inputId,options);}
  doc.addEventListener('focusin',event=>{if(bindings.has(event.target.id)){event.target.setAttribute('aria-autocomplete','list');event.target.setAttribute('aria-controls','chat-reference-options');event.target.setAttribute('aria-haspopup','listbox');ensure();void update(event.target);}});
  root.ChatReferences={register,chip};
})(window);
