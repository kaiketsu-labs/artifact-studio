/* One-level slide submenus, with inline expansion when a flyout cannot fit. */
((root)=>{
  'use strict';
  function create({menu,returnFocus}){
    const doc=menu.ownerDocument,view=doc.defaultView;
    const branches=[...menu.querySelectorAll('[data-slide-submenu]')].map(trigger=>({trigger,panel:doc.getElementById(trigger.getAttribute('aria-controls'))}));
    let active=null,hoverTimer=null,closeTimer=null,anchorBounds=null;
    const rows=panel=>[...panel.querySelectorAll('button:not(:disabled)')].filter(button=>button.closest('[role="menu"]')===panel&&button.getClientRects().length);
    const clearTimers=()=>{view.clearTimeout(hoverTimer);view.clearTimeout(closeTimer);};
    function closeBranch(focus=false){
      clearTimers();if(!active)return;
      const {trigger,panel}=active;active=null;
      if(panel.matches(':popover-open'))panel.hidePopover();
      panel.hidden=true;panel.classList.remove('is-inline');panel.setAttribute('popover','manual');trigger.setAttribute('aria-expanded','false');
      if(focus){trigger.focus({preventScroll:true});trigger.scrollIntoView({block:'nearest'});}
    }
    function clamp(x,y){
      menu.style.left=`${Math.max(8,Math.min(x,view.innerWidth-menu.offsetWidth-8))}px`;
      menu.style.top=`${Math.max(8,Math.min(y,view.innerHeight-menu.offsetHeight-8))}px`;
    }
    function canFlyOut(){const rect=menu.getBoundingClientRect();return Math.max(rect.left-8,view.innerWidth-rect.right-8)>=rect.width+4;}
    function showBranch(branch,focus=false){
      clearTimers();if(branch.trigger.disabled||!menu.matches(':popover-open'))return;
      if(active!==branch){
        closeBranch();active=branch;
        const {trigger,panel}=branch,rect=menu.getBoundingClientRect(),row=trigger.getBoundingClientRect();
        panel.hidden=false;
        if(canFlyOut()){
          panel.showPopover({source:trigger});
          const left=rect.right+panel.offsetWidth+4<=view.innerWidth-8?rect.right+4:rect.left-panel.offsetWidth-4;
          panel.style.left=`${Math.max(8,Math.min(left,view.innerWidth-panel.offsetWidth-8))}px`;
          panel.style.top=`${Math.max(8,Math.min(row.top-5,view.innerHeight-panel.offsetHeight-8))}px`;
        }else{
          panel.removeAttribute('popover');panel.classList.add('is-inline');clamp(rect.left,rect.top);
        }
        trigger.setAttribute('aria-expanded','true');
      }
      if(focus){rows(branch.panel)[0]?.focus();doc.activeElement?.scrollIntoView({block:'nearest'});}
    }
    function close(focus=false){closeBranch();if(menu.matches(':popover-open'))menu.hidePopover();if(focus)returnFocus()?.focus({preventScroll:true});}
    function open({x,y,anchor}){
      close();anchorBounds=anchor;menu.showPopover();clamp(x??anchor.right-15,y??anchor.top+12);rows(menu)[0]?.focus({preventScroll:true});
    }
    for(const branch of branches){
      const {trigger,panel}=branch;
      trigger.addEventListener('click',event=>{event.preventDefault();if(active===branch&&branch.panel.classList.contains('is-inline'))closeBranch(true);else showBranch(branch,true);});
      trigger.addEventListener('pointerenter',event=>{clearTimers();if(event.pointerType!=='touch'&&canFlyOut())hoverTimer=view.setTimeout(()=>showBranch(branch),120);});
      panel.addEventListener('pointerenter',clearTimers);
    }
    menu.addEventListener('pointerover',event=>{
      const button=event.target.closest('button');
      if(active&&!active.panel.classList.contains('is-inline')&&button?.closest('[role="menu"]')===menu&&!button.hasAttribute('data-slide-submenu'))closeBranch();
    });
    menu.addEventListener('pointerenter',()=>view.clearTimeout(closeTimer));
    menu.addEventListener('pointerleave',()=>{if(active&&!active.panel.classList.contains('is-inline'))closeTimer=view.setTimeout(()=>closeBranch(),220);});
    menu.addEventListener('beforetoggle',event=>{if(event.target===menu&&event.newState==='closed')closeBranch();});
    menu.addEventListener('keydown',event=>{
      if(event.defaultPrevented)return;
      const level=event.target.closest('[role="menu"]'),branch=branches.find(item=>item.trigger===doc.activeElement);
      if(event.key==='ArrowRight'&&branch){event.preventDefault();event.stopPropagation();showBranch(branch,true);return;}
      if(event.key==='ArrowLeft'&&active){event.preventDefault();event.stopPropagation();closeBranch(true);return;}
      if(event.key==='Escape'){event.preventDefault();event.stopPropagation();if(active)closeBranch(true);else close(true);return;}
      if(event.key==='Tab'){event.preventDefault();event.stopPropagation();close(true);return;}
      if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
      event.preventDefault();event.stopPropagation();
      if(level===menu&&active)closeBranch();
      const buttons=rows(level),index=buttons.indexOf(doc.activeElement),next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;
      buttons[next]?.focus();
    });
    view.addEventListener('resize',()=>close());
    doc.addEventListener('scroll',event=>{
      if(menu.contains(event.target)){if(event.target===menu&&active&&!active.panel.classList.contains('is-inline'))closeBranch();return;}
      if(!menu.matches(':popover-open'))return;
      const anchor=returnFocus()?.getBoundingClientRect();
      if(!anchor||anchor.left!==anchorBounds.left||anchor.top!==anchorBounds.top)close();
    },true);
    return {open,close};
  }
  root.SlideMenu={create};
})(window);
