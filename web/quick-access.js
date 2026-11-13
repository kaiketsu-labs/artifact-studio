/* Shared human recents and verified connections; no artifact bodies are fetched here. */
((root)=>{
  'use strict';
  function create({menu,button,keyboard=false,onProject=null,trackProjects=false}){
    const doc=menu.ownerDocument,host=doc.createElement('div');host.className='project-quick-access';menu.prepend(host);
    let snapshot={connected:[],recent:[]},signature='',pending=null;
    const harnessName=value=>value==='claude-code'?'Claude Code':value==='codex'?'Codex':value;
    const connectionLabel=id=>[...new Set(snapshot.connected.filter(entry=>entry.project_id===id).map(entry=>harnessName(entry.harness)))].join(', ');
    function connectionBadge(label){const badge=doc.createElement('span');badge.className='project-connected-badge';badge.title=label+' connected';badge.setAttribute('role','img');badge.setAttribute('aria-label',badge.title);badge.innerHTML=root.SlideIcons.svg('plug');return badge;}
    function decorateProjects(){
      if(!trackProjects)return;
      for(const row of doc.querySelectorAll('[data-project]')){
        const label=connectionLabel(row.dataset.project),old=row.querySelector('.project-connected-badge');
        if(label){if(!old)row.append(connectionBadge(label));else{old.title=label+' connected';old.setAttribute('aria-label',old.title);}row.setAttribute('aria-description',label+' connected');}
        else{old?.remove();row.removeAttribute('aria-description');}
      }
    }
    const rows=()=>[...menu.querySelectorAll('a,button')].filter(row=>!row.hidden&&!row.disabled&&row.closest('[popover]')===menu);
    let recentButton,recentMenu,hoverOpened=false;
    function closeRecent(focus=false){if(recentMenu?.matches(':popover-open'))recentMenu.hidePopover();recentButton?.setAttribute('aria-expanded','false');hoverOpened=false;if(focus)recentButton?.focus();}
    function showRecent(focus=false){
      if(!recentMenu)return;
      recentMenu.showPopover({source:recentButton});recentButton.setAttribute('aria-expanded','true');
      const rect=recentButton.getBoundingClientRect(),parent=menu.getBoundingClientRect();
      const rightFits=parent.right+recentMenu.offsetWidth<root.innerWidth-8,leftFits=parent.left-recentMenu.offsetWidth>8;
      const left=rightFits?parent.right:leftFits?parent.left-recentMenu.offsetWidth:rect.right-recentMenu.offsetWidth;
      const top=rightFits||leftFits?rect.top:rect.bottom+recentMenu.offsetHeight<root.innerHeight-8?rect.bottom:rect.top-recentMenu.offsetHeight;
      recentMenu.style.left=`${Math.max(8,Math.min(left,root.innerWidth-recentMenu.offsetWidth-8))}px`;
      recentMenu.style.top=`${Math.max(8,Math.min(top,root.innerHeight-recentMenu.offsetHeight-8))}px`;
      if(focus)recentMenu.querySelector('a')?.focus();
    }
    function render(){
      const next=JSON.stringify(snapshot);if(next===signature)return;signature=next;
      const active=doc.activeElement?.dataset.quickId,wasRecentOpen=recentMenu?.matches(':popover-open');closeRecent();host.replaceChildren();recentButton=null;recentMenu=null;
      for(const [name,items] of [['Connected',snapshot.connected],['Recent projects',snapshot.recent]]){
        if(!items.length)continue;
        const group=doc.createElement('div');group.setAttribute('role','group');group.setAttribute('aria-label',name);
        let destination=group;
        if(name==='Recent projects'){
          recentButton=doc.createElement('button');recentButton.type='button';recentButton.className='project-recent-button';recentButton.dataset.quickId='recent-projects';recentButton.setAttribute('role','menuitem');recentButton.setAttribute('aria-label',name);recentButton.setAttribute('aria-haspopup','menu');recentButton.setAttribute('aria-expanded','false');recentButton.tabIndex=-1;
          recentButton.innerHTML=root.SlideIcons.svg('project');const label=doc.createElement('span');label.textContent=name;recentButton.append(label);const arrow=doc.createElement('span');arrow.className='project-submenu-arrow';arrow.textContent='›';arrow.setAttribute('aria-hidden','true');recentButton.append(arrow);
          recentMenu=doc.createElement('div');recentMenu.id=menu.id+'-recents';recentMenu.className='project-recent-menu';recentMenu.setAttribute('popover','manual');recentMenu.setAttribute('role','menu');recentMenu.setAttribute('aria-label',name);recentButton.setAttribute('aria-controls',recentMenu.id);
          recentButton.addEventListener('pointerenter',event=>{if(event.pointerType!=='touch'&&!recentMenu.matches(':popover-open')){showRecent();hoverOpened=true;}});
          recentButton.addEventListener('click',()=>{if(recentMenu.matches(':popover-open')&&!hoverOpened)closeRecent(true);else{hoverOpened=false;showRecent(true);}});
          recentMenu.addEventListener('toggle',()=>recentButton.setAttribute('aria-expanded',String(recentMenu.matches(':popover-open'))));
          recentMenu.addEventListener('keydown',event=>{
            event.stopPropagation();const items=[...recentMenu.querySelectorAll('a')],index=items.indexOf(doc.activeElement);
            if(['ArrowLeft','Escape'].includes(event.key)){event.preventDefault();closeRecent(true);}
            else if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();items[event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length]?.focus();}
            else if(event.key===' '){event.preventDefault();doc.activeElement.click();}
            else if(event.key==='Tab')menu.hidePopover();
          });
          group.append(recentButton,recentMenu);destination=recentMenu;
        }else{
          const heading=doc.createElement('p');heading.className='project-quick-label';heading.textContent=name;heading.setAttribute('aria-hidden','true');group.append(heading);
        }
        for(const entry of items){
          const connected=!!entry.harness,row=doc.createElement('a'),glyph=doc.createElement('span'),label=doc.createElement('span'),title=doc.createElement('span');
          const harness=harnessName(entry.harness),projectAgent=!connected&&connectionLabel(entry.project_id);
          row.href=entry.url;row.className='project-shortcut';row.dataset.quickId=entry.id;row.setAttribute('role','menuitem');row.setAttribute('aria-label',entry.title+(connected?`, ${entry.project_title}, ${harness} connected`:''));
          if(!keyboard)row.tabIndex=-1;
          glyph.className=connected?'project-connection-dot':'project-shortcut-icon';glyph.setAttribute('aria-hidden','true');if(!connected)glyph.innerHTML=root.SlideIcons.svg('project');
          label.className='project-shortcut-label';title.textContent=entry.title;label.append(title);
          if(connected&&entry.project_title!==entry.title){const subtitle=doc.createElement('small');subtitle.textContent=entry.project_title;label.append(subtitle);}
          row.title=connected?`${entry.project_title} · ${harness} connected`:entry.title;
          row.append(glyph,label);destination.append(row);
          if(projectAgent){row.append(connectionBadge(projectAgent));row.setAttribute('aria-description',projectAgent+' connected');}
          row.addEventListener('click',event=>{if(!connected&&onProject&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();onProject(entry.project_id);}menu.hidePopover();});
        }
        host.append(group);const separator=doc.createElement('div');separator.className='project-quick-separator';separator.setAttribute('role','separator');host.append(separator);
      }
      if(menu.matches(':popover-open')){if(wasRecentOpen)showRecent();if(active)[...host.querySelectorAll('a,button')].find(row=>row.dataset.quickId===active)?.focus();}
    }
    async function request(body){
      try{
        const path='/api/navigation'+(body?'/visit':'');
        const response=await root.fetch(root.ArtifactSession?.path?.(path) ?? path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{cache:'no-store'});
        if(!response.ok)throw Error('Navigation unavailable');snapshot=await response.json();render();decorateProjects();
      }catch{snapshot={...snapshot,connected:[]};render();decorateProjects();}
    }
    function refresh(){if(!pending)pending=request().finally(()=>pending=null);return pending;}
    menu.addEventListener('beforetoggle',event=>{if(event.target!==menu)return;if(event.newState==='open')void refresh();else closeRecent();});
    menu.addEventListener('pointerover',event=>{if(event.target.closest('[role="menuitem"]')&&!event.target.closest('.project-recent-button,.project-recent-menu'))closeRecent();});
    menu.addEventListener('keydown',event=>{if(event.target===recentButton&&['ArrowRight','Enter',' '].includes(event.key)){event.preventDefault();event.stopImmediatePropagation();showRecent(true);}},true);
    menu.addEventListener('keydown',event=>{
      if(event.defaultPrevented)return;
      if(event.key===' '&&event.target.matches('a[data-quick-id]')){event.preventDefault();event.target.click();return;}
      if(!keyboard)return;
      if(event.key==='Escape'){event.preventDefault();menu.hidePopover();button.focus();return;}
      if(event.key==='Tab'){menu.hidePopover();return;}
      const items=rows(),index=items.indexOf(doc.activeElement);
      if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();items[event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length]?.focus();}
    });
    if(keyboard){
      menu.setAttribute('role','menu');
      for(const row of rows())row.setAttribute('role','menuitem');
      button.addEventListener('keydown',event=>{
        if(!['ArrowDown','ArrowUp'].includes(event.key))return;
        event.preventDefault();
        const index=event.key==='ArrowUp'?-1:0;
        menu.showPopover();
        const initial=rows().at(index);
        initial?.focus();
        void refresh().then(()=>{
          if(menu.matches(':popover-open')&&doc.activeElement===initial)rows().at(index)?.focus();
        });
      });
      menu.addEventListener('toggle',()=>button.setAttribute('aria-expanded',String(menu.matches(':popover-open'))));
    }
    root.addEventListener('focus',()=>void refresh());
    const timer=root.setInterval(()=>{if(!doc.hidden&&(trackProjects||menu.matches(':popover-open')))void refresh();},10000);
    root.addEventListener('pagehide',()=>root.clearInterval(timer),{once:true});
    void refresh();return {refresh,visit:body=>request(body),decorateProjects};
  }
  root.ProjectQuickAccess={create};
})(window);
