/* Keep workspace actions together while reusing their existing dialogs and handlers. */
((root)=>{
  'use strict';
  /** Match familiar file/show commands without taking over typing or native browser modifiers. */
  function shortcut(event,{mac=false,editing=false,modal=false}={}){
    if(event.defaultPrevented||event.isComposing||event.repeat||modal||event.altKey)return null;
    const primary=mac?event.metaKey&&!event.ctrlKey:event.ctrlKey&&!event.metaKey;
    const key=event.key.toLowerCase();
    if(primary){
      if(key==='s')return event.shiftKey?(editing?null:'files'):'save';
      if(editing)return null;
      if(key==='enter'&&mac)return event.shiftKey?'present-first':'present-current';
      if(event.shiftKey)return null;
      return {n:'new',o:'open'}[key]||null;
    }
    if(editing||event.ctrlKey||event.metaKey)return null;
    if(key==='f5')return event.shiftKey?'present-current':'present-first';
    return key==='f10'&&!event.shiftKey?'menu':null;
  }
  /** Provide an accessible popover, grouped actions and platform-specific shortcut hints. */
  function create({doc,view,projects,present,blocked=()=>false}){
    const $=id=>doc.getElementById(id),button=$('workspace-menu-button'),panel=$('workspace-menu');
    const mac=/Mac|iPhone|iPad/.test(view.navigator.platform),primary=mac?'⌘':'Ctrl+',shift=mac?'⇧':'Shift+';
    const iconButton=view.SlideIcons.button;
    iconButton(button,'menu','Menu');button.title='Menu (F10)';
    iconButton($('menu-open-project'),'folder','Import presentation',true);
    iconButton($('menu-save-project'),'save','Save a copy…',true);
    $('menu-save-project').title='Download an editable .artifact-studio.zip copy, including notes and feedback.';
    for(const [id,key,shifted] of [['new-button','N',false],['menu-open-project','O',false],['menu-save-project','S',false],['project-button','S',true]]){
      const control=$(id);control.dataset.shortcut=(mac?(shifted?shift:'')+primary:primary+(shifted?shift:''))+key;
      control.setAttribute('aria-keyshortcuts',`${mac?'Meta':'Control'}+${shifted?'Shift+':''}${key}`);
    }
    $('present-link').title=mac?'Present (⌘Return)':'Present (Shift+F5)';
    $('present-link').setAttribute('aria-keyshortcuts',mac?'Meta+Enter':'Shift+F5');
    let query='',queryTimer;
    const open=()=>panel.matches(':popover-open');
    const rows=()=>[...panel.querySelectorAll('[role="menuitem"]')].filter(row=>!row.hidden&&!row.disabled&&row.closest('[popover]')===panel);
    function sync(){projects.update();$('menu-save-project').disabled=blocked()||$('save-project').disabled;$('menu-open-project').disabled=blocked()||$('open-project').disabled;}
    function position(){const rect=button.getBoundingClientRect();panel.style.left=`${Math.max(8,Math.min(rect.right-panel.offsetWidth,view.innerWidth-panel.offsetWidth-8))}px`;panel.style.top=`${Math.max(8,Math.min(rect.bottom+8,view.innerHeight-panel.offsetHeight-8))}px`;}
    function close(focus=false){if(open())panel.hidePopover();if(focus)button.focus();}
    function show(last=false){sync();panel.showPopover();position();(last?rows().at(-1):rows()[0])?.focus();}
    button.addEventListener('click',()=>open()?close(true):show());
    button.addEventListener('keydown',event=>{if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();event.stopPropagation();show(event.key==='ArrowUp');}});
    panel.addEventListener('toggle',()=>{button.setAttribute('aria-expanded',String(open()));if(!open()){query='';clearTimeout(queryTimer);}});
    panel.addEventListener('click',event=>{const row=event.target.closest('[role="menuitem"]');if(row&&!row.disabled&&!row.hasAttribute('aria-haspopup')&&row.closest('[popover]')===panel)close(true);},true);
    $('menu-open-project').addEventListener('click',()=>projects.open());
    $('menu-save-project').addEventListener('click',()=>projects.save());
    panel.addEventListener('keydown',event=>{
      event.stopPropagation();
      if(event.key==='Escape'){event.preventDefault();close(true);return;}
      if(event.key==='Tab'){close();return;}
      const items=rows(),current=items.indexOf(doc.activeElement);
      if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();items[event.key==='Home'?0:event.key==='End'?items.length-1:(current+(event.key==='ArrowDown'?1:-1)+items.length)%items.length]?.focus();return;}
      if(event.key==='ArrowRight'&&doc.activeElement===$('appearance-button')){event.preventDefault();$('appearance-button').click();return;}
      if(event.key.length!==1||event.key===' '||event.metaKey||event.ctrlKey||event.altKey)return;
      event.preventDefault();clearTimeout(queryTimer);query+=event.key.toLowerCase();
      const prefix=[...query].every(char=>char===query[0])?query[0]:query;
      [...items.slice(current+1),...items.slice(0,current+1)].find(row=>row.getAttribute('aria-label').toLowerCase().startsWith(prefix))?.focus();
      queryTimer=setTimeout(()=>query='',600);
    });
    function handleShortcut(event){
      const action=shortcut(event,{mac,editing:!!event.target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"])'),modal:!!doc.querySelector('dialog[open]')});
      if(!action||blocked())return;
      sync();
      const control=({'new':$('new-button'),open:$('menu-open-project'),save:$('menu-save-project'),files:$('project-button'),'present-first':$('present-link'),'present-current':$('present-link')})[action];
      if(control&&(control.disabled||control.getAttribute('aria-disabled')==='true'||control.hidden))return;
      event.preventDefault();
      event.stopPropagation();
      if(action==='menu'){open()?close(true):show();return;}
      close(true);
      if(action.startsWith('present-'))present(action==='present-first');else control.click();
    }
    const bound=new WeakSet();
    function bindDocument(target){if(bound.has(target))return;bound.add(target);target.addEventListener('keydown',handleShortcut,true);}
    bindDocument(doc);
    view.addEventListener('resize',()=>{if(open())position();});
    return {show,close,bindDocument};
  }
  const api={create,shortcut};if(typeof module!=='undefined')module.exports=api;else root.SlideWorkspaceMenu=api;
})(typeof window==='undefined'?{}:window);
