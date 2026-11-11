/* Shared, local element commands. Submitted feedback is outside this undo history. */
((root)=>{
  'use strict';
  const copy=value=>JSON.parse(JSON.stringify(value));
  function history(limit=40){
    let past=[],future=[];
    return {
      record(before,after){if(JSON.stringify(before)===JSON.stringify(after))return;past.push({before:copy(before),after:copy(after)});past=past.slice(-limit);future=[];},
      undo(){const item=past.pop();if(!item)return;future.push(item);return copy(item.before);},
      redo(){const item=future.pop();if(!item)return;past.push(item);return copy(item.after);},
      clear(){past=[];future=[];},canUndo:()=>past.length>0,canRedo:()=>future.length>0
    };
  }
  function typing(target){return !!target?.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"]');}
  /** Only called for a focused canvas/selection; native text editing keeps its shortcuts. */
  function shortcut(event){
    if(event.defaultPrevented||event.isComposing||event.keyCode===229||event.altKey||typing(event.target))return null;
    const key=event.key.toLowerCase(),primary=!!(event.ctrlKey||event.metaKey);
    if(key==='enter'&&!primary&&event.target?.closest?.('button,a,[role=button],[role=menuitem]'))return null;
    if(primary){
      if(event.ctrlKey&&event.metaKey)return null;
      if(key==='z')return event.shiftKey?'redo':'undo';
      if(key==='y'&&!event.shiftKey)return 'redo';
      if(key==='enter'&&!event.shiftKey)return 'send';
      return null;
    }
    if(key==='contextmenu'||key==='f10'&&event.shiftKey)return 'menu';
    if(event.shiftKey&&!['arrowleft','arrowright','arrowup','arrowdown'].includes(key))return null;
    if(['delete','backspace'].includes(key))return 'delete';
    if(['arrowleft','arrowright','arrowup','arrowdown'].includes(key))return {action:'nudge',key:event.key,step:event.shiftKey?10:1};
    if(key==='escape')return 'cancel';
    return ['enter','f2'].includes(key)&&!event.repeat?'edit':null;
  }
  /** Render only workspace-owned labels and commands, outside authored content. */
  function menu({doc,id='element-context-menu',onError=()=>{}}){
    const view=doc.defaultView,panel=doc.createElement('div');
    panel.id=id;panel.className='element-context-menu';panel.setAttribute('role','menu');panel.setAttribute('aria-label','Element actions');panel.setAttribute('popover','manual');doc.body.append(panel);
    let returnFocus=null,anchorBounds=null;
    const open=()=>panel.matches(':popover-open');
    const rows=()=>[...panel.querySelectorAll('button:not(:disabled)')];
    function close(focus=false){if(open())panel.hidePopover();if(focus&&returnFocus?.isConnected)returnFocus.focus({preventScroll:true});}
    function show({x,y,commands,focus=doc.activeElement}){
      for(const other of doc.querySelectorAll('.element-context-menu:popover-open'))if(other!==panel)other.hidePopover();
      close();returnFocus=focus;anchorBounds=focus?.getBoundingClientRect?.();panel.replaceChildren();
      for(const item of commands){
        if(item.separator){const divider=doc.createElement('hr');divider.setAttribute('role','separator');panel.append(divider);continue;}
        const button=doc.createElement('button');button.type='button';button.setAttribute('role','menuitem');button.setAttribute('aria-label',item.label);button.disabled=item.disabled===true;
        if(item.icon){const glyph=doc.createElement('span');glyph.className='context-action-icon';glyph.setAttribute('aria-hidden','true');glyph.innerHTML=item.icon;button.append(glyph);}const label=doc.createElement('span');label.className='context-action-label';label.textContent=item.label;button.append(label);
        if(item.keys){const keys=doc.createElement('kbd');keys.textContent=item.keys;button.append(keys);}
        if(item.danger)button.classList.add('is-danger');
        button.addEventListener('click',async()=>{if(button.disabled)return;close();try{await item.run();}catch(error){onError(error.message);}if(returnFocus?.isConnected&&[doc.body,button].includes(doc.activeElement))returnFocus.focus({preventScroll:true});});
        panel.append(button);
      }
      panel.showPopover();
      panel.style.left=`${Math.max(8,Math.min(x,view.innerWidth-panel.offsetWidth-8))}px`;
      panel.style.top=`${Math.max(8,Math.min(y,view.innerHeight-panel.offsetHeight-8))}px`;
      rows()[0]?.focus({preventScroll:true});
    }
    panel.addEventListener('keydown',event=>{
      event.stopPropagation();
      if(event.key==='Escape'){event.preventDefault();close(true);return;}
      if(event.key==='Tab'){close(true);event.preventDefault();return;}
      if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
      event.preventDefault();const buttons=rows(),current=buttons.indexOf(doc.activeElement),index=event.key==='Home'?0:event.key==='End'?buttons.length-1:(current+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;buttons[index]?.focus();
    });
    doc.addEventListener('pointerdown',event=>{if(open()&&!panel.contains(event.target))close();},true);
    view.addEventListener('resize',()=>close());
    doc.addEventListener('scroll',event=>{
      if(!open()||panel.contains(event.target))return;
      const current=returnFocus?.isConnected&&returnFocus.getBoundingClientRect();
      if(!current||!anchorBounds||current.left!==anchorBounds.left||current.top!==anchorBounds.top)close();
    },true);
    return {show,close,isOpen:open,panel};
  }
  function framePoint(frame,x,y,viewport){
    const rect=frame.getBoundingClientRect(),width=viewport?.width,height=viewport?.height;
    if(![x,y,width,height].every(Number.isFinite)||width<=0||height<=0||x<0||y<0||x>width||y>height)return null;
    return {x:rect.left+x*rect.width/width,y:rect.top+y*rect.height/height};
  }
  const api={history,typing,shortcut,menu,framePoint};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  root.ElementCommands=api;
})(typeof window==='undefined'?globalThis:window);
