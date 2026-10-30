/* Project selection reuses the canonical selector and existing creation flow. */
window.SlideDeckPicker={
  create({select,button,panel,list,createButton,onCreate,onOpen=()=>{},blocked=()=>false}){
    const {svg,button:iconButton}=window.SlideIcons;
    let signature='',query='',queryTimer=null;
    const rows=()=>[...panel.querySelectorAll('[role^=menuitem]')].filter(row=>!row.disabled);
    const open=()=>panel.matches(':popover-open');
    iconButton(createButton,'plus','New project',true);
    function sync(){
      const items=[...select.options],selected=items[select.selectedIndex],busy=blocked()||select.disabled&&items.length>0;
      button.disabled=!!busy;createButton.disabled=!!busy;
      const title=selected?.textContent||'Projects';
      button.querySelector('span').textContent=title;button.title=title;button.setAttribute('aria-label',selected?`Choose project: ${title}`:'Choose project');
      const next=JSON.stringify(items.map(item=>[item.value,item.textContent]));
      if(signature!==next){
        const focused=panel.contains(document.activeElement)?document.activeElement.dataset.projectId:null;
        signature=next;
        list.replaceChildren(...items.map(item=>{
          const row=document.createElement('button');row.type='button';row.setAttribute('role','menuitemradio');row.tabIndex=-1;row.dataset.projectId=item.value;row.title=item.textContent;
          row.innerHTML=svg('slides');
          const name=document.createElement('span');name.className='deck-menu-name';name.textContent=item.textContent;
          const check=document.createElement('span');check.className='deck-menu-check';check.setAttribute('aria-hidden','true');check.innerHTML=svg('check');
          row.append(name,check);return row;
        }));
        if(focused&&open())[...list.children].find(row=>row.dataset.projectId===focused)?.focus();
      }
      for(const row of list.children){row.disabled=!!busy;const checked=row.dataset.projectId===selected?.value;row.setAttribute('aria-checked',String(checked));row.querySelector('.deck-menu-check').style.visibility=checked?'visible':'hidden';}
      if(busy&&open())panel.hidePopover();
    }
    function position(){
      const rect=button.getBoundingClientRect();
      panel.style.left=`${Math.max(8,Math.min(rect.left,window.innerWidth-panel.offsetWidth-8))}px`;
      panel.style.top=`${Math.max(8,Math.min(rect.bottom+7,window.innerHeight-panel.offsetHeight-8))}px`;
    }
    function close(focus=false){if(open())panel.hidePopover();if(focus&&!button.disabled)button.focus();}
    function show(last=false){sync();if(button.disabled)return;onOpen();panel.showPopover();position();const items=rows();(last?items.at(-1):items.find(row=>row.getAttribute('aria-checked')==='true')||items[0])?.focus();}
    button.append(document.createRange().createContextualFragment(svg('down')));
    button.addEventListener('click',()=>open()?close():show());
    button.addEventListener('keydown',event=>{if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();show(event.key==='ArrowUp');}});
    panel.addEventListener('toggle',()=>{button.setAttribute('aria-expanded',String(open()));if(!open()){query='';clearTimeout(queryTimer);}});
    panel.addEventListener('click',event=>{
      const row=event.target.closest('[data-project-id]');if(!row||row.disabled)return;
      const id=row.dataset.projectId;close(true);if(select.value===id)return;
      select.value=id;select.dispatchEvent(new Event('change',{bubbles:true}));
    });
    createButton.addEventListener('click',()=>{if(createButton.disabled)return;close();onCreate();});
    panel.addEventListener('keydown',event=>{
      event.stopPropagation();
      if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close(true);return;}
      if(event.key==='Tab'){close();return;}
      const items=rows(),current=items.indexOf(document.activeElement);
      if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
        event.preventDefault();event.stopPropagation();
        const index=event.key==='Home'?0:event.key==='End'?items.length-1:(current+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;
        items[index]?.focus();return;
      }
      if(event.key.length!==1||event.key===' '||event.ctrlKey||event.metaKey||event.altKey)return;
      event.preventDefault();event.stopPropagation();clearTimeout(queryTimer);
      query+=event.key.toLocaleLowerCase();const prefix=[...query].every(char=>char===query[0])?query[0]:query;
      const ordered=[...items.slice(current+1),...items.slice(0,current+1)];
      ordered.find(row=>row.textContent.trim().toLocaleLowerCase().startsWith(prefix))?.focus();queryTimer=setTimeout(()=>query='',600);
    });
    window.addEventListener('resize',()=>{if(open())position();});
    new MutationObserver(sync).observe(select,{childList:true,subtree:true,attributes:true,characterData:true});
    sync();return {sync};
  }
};
