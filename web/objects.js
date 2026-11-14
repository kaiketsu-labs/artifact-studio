/* Selection-only navigation of meaningful objects in the slide preview. */
((root)=>{
  'use strict';
  const selector='[data-element-id],h1,h2,h3,h4,h5,h6,p,li,img,svg,span,div';
  const clean=value=>(value||'').replace(/\s+/g,' ').trim();
  /** Describe an object without using its text as its identity. */
  function objectLabel(type,name,position){
    const text=clean(name).slice(0,70);
    return `${type}${text?` · ${text}`:''}${position?` · ${position}`:''}`;
  }
  /** Describe slide-relative placement for otherwise indistinguishable objects. */
  function location(x,y){
    const vertical=y<1/3?'top':y>2/3?'bottom':'middle';
    const horizontal=x<1/3?'left':x>2/3?'right':'center';
    return `${vertical} ${horizontal}`;
  }
  /** Collect visible, meaningful elements while retaining explicit groups. */
  function collect(doc){
    const slide=doc?.querySelector('.slide');
    if(!slide)return [];
    const view=doc.defaultView,canvas=slide.getBoundingClientRect();
    const entries=[];
    for(const element of slide.querySelectorAll(selector)){
      if(element.closest('[aria-hidden="true"], [hidden], [data-review-chrome]'))continue;
      const tag=element.tagName.toLowerCase(),explicit=element.hasAttribute('data-element-id');
      if(element.closest('svg')&&tag!=='svg'&&!explicit)continue;
      if(!explicit&&['span','div'].includes(tag)&&element.parentElement?.closest('h1,h2,h3,h4,h5,h6,p,li'))continue;
      const style=view.getComputedStyle(element),rect=element.getBoundingClientRect();
      if(style.display==='none'||style.visibility==='hidden'||!rect.width||!rect.height)continue;
      const name=clean(element.getAttribute('aria-label')||element.getAttribute('alt')||element.innerText||element.textContent);
      let type='';
      if(/^h[1-6]$/.test(tag))type='Heading';
      else if(tag==='img')type='Image';
      else if(tag==='svg')type='Diagram';
      else if(explicit&&(element.querySelector(selector)||element.namespaceURI==='http://www.w3.org/2000/svg'))type='Group';
      else if(['p','li','span','div'].includes(tag)&&name&&!element.querySelector('h1,h2,h3,h4,h5,h6,p,li,img,svg,div,[data-element-id]'))type='Text';
      else if(explicit&&name)type='Group';
      if(!type)continue;
      const parent=entries.findLast(entry=>entry.element.contains(element));
      const x=canvas.width?(rect.left+rect.width/2-canvas.left)/canvas.width:.5;
      const y=canvas.height?(rect.top+rect.height/2-canvas.top)/canvas.height:.5;
      entries.push({element,type,name:type==='Group'?clean(element.getAttribute('aria-label')||element.getAttribute('data-element-id')):name,position:location(x,y),depth:parent?parent.depth+1:0});
    }
    const counts=new Map();
    for(const entry of entries){const base=objectLabel(entry.type,entry.name);counts.set(base,(counts.get(base)||0)+1);}
    const seen=new Map();
    for(const entry of entries){
      const base=objectLabel(entry.type,entry.name),duplicate=counts.get(base)>1;
      const label=objectLabel(entry.type,entry.name,duplicate?entry.position:'');
      const number=(seen.get(label)||0)+1;seen.set(label,number);
      entry.label=label;
      if(duplicate&&entries.filter(item=>objectLabel(item.type,item.name,item.position)===label).length>1)entry.label+=` · ${number}`;
    }
    return entries;
  }
  /** Create a keyboard-accessible object picker for the current preview frame. */
  function create({button,panel,frame,onSelect,onHover}){
    let entries=[],selected=[];
    panel.classList.add('object-picker');
    panel.setAttribute('role','menu');panel.setAttribute('aria-label','Slide objects');
    button.setAttribute('aria-controls',panel.id);button.setAttribute('aria-haspopup','menu');button.setAttribute('aria-expanded','false');
    const close=()=>{if(panel.matches(':popover-open'))panel.hidePopover();onHover(null);};
    const rows=()=>[...panel.querySelectorAll('button[data-object-index]')];
    function refresh(){
      const hadFocus=panel.contains(document.activeElement);
      close();entries=frame.style.visibility==='hidden'?[]:collect(frame.contentDocument);selected=selected.filter(element=>element.isConnected&&element.ownerDocument===frame.contentDocument);panel.replaceChildren();
      entries.forEach((entry,index)=>{
        const row=document.createElement('button');row.type='button';row.dataset.objectIndex=String(index);row.setAttribute('role','menuitemcheckbox');row.setAttribute('aria-checked','false');row.title='Select object · Cmd/Ctrl/Shift-click to add or remove';row.style.setProperty('--object-depth',Math.min(entry.depth,5));
        const icon=document.createElement('span');icon.className='object-picker-icon';icon.innerHTML=root.SlideIcons?.svg(entry.type==='Group'?'group':entry.type==='Image'||entry.type==='Diagram'?'slides':'edit')||'';
        const label=document.createElement('span');label.className='object-picker-label';label.textContent=entry.label;row.append(icon,label);panel.append(row);
      });
      button.disabled=!entries.length;
      sync(selected);
      if(hadFocus)(button.disabled?frame:button).focus();
    }
    function sync(elements){selected=Array.isArray(elements)?elements:elements?[elements]:[];for(const row of rows()){const active=selected.includes(entries[Number(row.dataset.objectIndex)]?.element);row.classList.toggle('is-selected',active);row.setAttribute('aria-checked',String(active));}}
    button.addEventListener('click',()=>{
      if(panel.matches(':popover-open')){close();return;}
      if(!entries.length)return;
      panel.showPopover();
      const anchor=button.getBoundingClientRect(),box=panel.getBoundingClientRect(),margin=8;
      const left=Math.max(margin,Math.min(anchor.left,window.innerWidth-box.width-margin));
      const below=anchor.bottom+4,top=below+box.height<=window.innerHeight-margin?below:Math.max(margin,anchor.top-box.height-4);
      panel.style.left=`${left}px`;panel.style.top=`${top}px`;
      sync(selected);(rows().find(row=>row.classList.contains('is-selected'))||rows()[0])?.focus();
    });
    panel.addEventListener('click',async event=>{
      const row=event.target.closest('button[data-object-index]'),entry=row&&entries[Number(row.dataset.objectIndex)];
      if(!entry||!entry.element.isConnected||entry.element.ownerDocument!==frame.contentDocument){refresh();return;}
      const additive=!!(event.metaKey||event.ctrlKey||event.shiftKey);
      if(!additive)close();
      try{await onSelect(entry.element,additive);}
      catch{ /* Selection remains unchanged when the editor rejects the request. */ }
      finally{if(additive&&panel.matches(':popover-open'))row.focus();else if(!button.disabled)button.focus();else frame.focus();}
    });
    const highlight=event=>{const row=event.target.closest('button[data-object-index]');onHover(row?entries[Number(row.dataset.objectIndex)]?.element||null:null);};
    panel.addEventListener('pointerover',highlight);panel.addEventListener('pointerleave',()=>onHover(null));panel.addEventListener('focusin',highlight);
    panel.addEventListener('keydown',event=>{
      if(event.key==='Escape'){event.preventDefault();close();button.focus();return;}
      if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
      event.preventDefault();const items=rows(),index=items.indexOf(document.activeElement);
      const next=event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;
      items[next]?.focus();
    });
    panel.addEventListener('toggle',event=>{button.setAttribute('aria-expanded',String(event.newState==='open'));if(event.newState==='closed')onHover(null);});
    frame.addEventListener('load',refresh);
    refresh();
    return {refresh,sync,setFrame(next){frame.removeEventListener('load',refresh);frame=next;frame.addEventListener('load',refresh);refresh();}};
  }
  const api={create,collect,objectLabel,location};
  if(typeof module!=='undefined')module.exports=api;else root.SlideObjects=api;
})(typeof window==='undefined'?{}:window);
