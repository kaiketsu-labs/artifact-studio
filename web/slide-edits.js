/* Lightweight edit suggestions over the existing version-anchored feedback channel. */
((root)=>{
  'use strict';
  /** Clamp a drag to the slide canvas, independent of preview scale. */
  function movePosition(rect,dx,dy,canvas){
    return {x:Math.max(0,Math.min(Math.max(0,1-rect.width/canvas.width),(rect.left-(canvas.left||0)+dx)/canvas.width)),y:Math.max(0,Math.min(Math.max(0,1-rect.height/canvas.height),(rect.top-(canvas.top||0)+dy)/canvas.height))};
  }
  /** Align a visual bounding box to the actual slide rather than its preview viewport. */
  function alignPosition(rect,canvas,action){
    const position=movePosition(rect,0,0,canvas),width=Math.max(0,1-rect.width/canvas.width),height=Math.max(0,1-rect.height/canvas.height);
    if(['left','center','right'].includes(action))position.x=width*({left:0,center:.5,right:1})[action];
    if(['top','middle','bottom'].includes(action))position.y=height*({top:0,middle:.5,bottom:1})[action];
    return position;
  }
  /** Convert a viewport movement through measured parent rotation, scale and zoom. */
  function localDelta(dx,dy,basis){
    const determinant=basis.xx*basis.yy-basis.xy*basis.yx;
    if(!Number.isFinite(determinant)||Math.abs(determinant)<1e-8)return null;
    return {x:(dx*basis.yy-dy*basis.xy)/determinant,y:(dy*basis.xx-dx*basis.yx)/determinant};
  }
  /** Describe an exact placement while leaving layout implementation to the agent. */
  function moveMessage(position){return `Move this element's top-left corner to ${(position.x*100).toFixed(2)}% from the left and ${(position.y*100).toFixed(2)}% from the top of the slide. Keep its size and content.`;}
  /** Preview edits locally and submit their original element/version as ordinary feedback. */
  function create({tools,editButton,moveButton,alignButton,alignMenu,commitButton,cancelButton,sentBadge,anchor,onSend,onChange,onError,deliveryStatus=()=> 'released',blocked=()=>false}){
    let element=null,original=null,textEditing=false,dragEnabled=true,drag=null,doc=null,busy=false,suppressClick=false,sent=false,keyboardMove=null,submission=null,positionPreview=null;
    const requests=new Map();
    const canText=()=>!!element&&element.namespaceURI==='http://www.w3.org/1999/xhtml'&&/^(H[1-6]|P|SPAN|LI|DIV|LABEL)$/.test(element.tagName)&&!element.querySelector('div,p,h1,h2,h3,h4,h5,h6,li,svg,img,video,button,input,textarea,table,ul,ol')&&!!element.textContent.trim();
    const canMove=()=>!!element&&(element.namespaceURI==='http://www.w3.org/1999/xhtml'||element.tagName.toLowerCase()==='svg');
    function update(){
      tools.hidden=!element;editButton.hidden=textEditing||!!keyboardMove;moveButton.hidden=textEditing;commitButton.hidden=!textEditing&&!keyboardMove;cancelButton.hidden=!textEditing&&!keyboardMove;
      alignButton.hidden=textEditing;alignButton.disabled=busy||blocked()||!canMove();
      if(!element||alignButton.hidden||alignButton.disabled)closeAlign();
      editButton.disabled=busy||blocked()||!canText();moveButton.disabled=busy||blocked()||!canMove();commitButton.disabled=busy||blocked();cancelButton.disabled=busy||blocked();
      moveButton.setAttribute('aria-pressed',String(dragEnabled));moveButton.title=canMove()?'Drag selected element · arrow keys to position':'Select the surrounding group to move it';
      commitButton.setAttribute('aria-label',keyboardMove?'Send position change':'Send text change');commitButton.title=keyboardMove?'Send position change':'Send text change';
      const draft=!!keyboardMove||textEditing&&original&&(element.innerText??element.textContent)!==original.text;
      const status=draft?'draft':deliveryStatus(submission),label=({draft:'Unsent edit',queued:'Queued in Batch',released:'Saved for agent',answered:'Agent replied'})[status]||'Saved for agent';
      sentBadge.hidden=(!sent&&!draft)||busy;
      if(sentBadge.dataset.state!==status){sentBadge.dataset.state=status;sentBadge.title=label;sentBadge.setAttribute('aria-label',label);sentBadge.innerHTML=root.SlideIcons.svg(({draft:'edit',queued:'batch',released:'send',answered:'notes'})[status]||'send');const text=document.createElement('span');text.className='sr-only';text.textContent=label;sentBadge.append(text);}
      tools.classList.toggle('has-suggestion',!!(sent||draft));
      if(element)onChange(element);
    }
    function remember(){
      if(original)return;
      original={html:element.innerHTML,style:element.getAttribute('style'),editable:element.getAttribute('contenteditable'),text:element.innerText??element.textContent,anchor:anchor()};
    }
    function restore(){
      if(!original||!element)return;
      element.innerHTML=original.html;
      for(const [name,value] of [['style',original.style],['contenteditable',original.editable]]){if(value===null)element.removeAttribute(name);else element.setAttribute(name,value);}
      original=null;
      positionPreview=null;
    }
    function select(next){closeAlign();restore();element=next;textEditing=false;drag=null;keyboardMove=null;dragEnabled=true;sent=false;submission=null;update();}
    function cancel(){closeAlign();restore();textEditing=false;drag=null;keyboardMove=null;sent=false;submission=null;update();}
    function startText(){
      if(busy||blocked()||!canText())return;
      restore();remember();sent=false;textEditing=true;element.setAttribute('contenteditable','plaintext-only');element.style.cursor='text';element.style.outline='none';element.focus();update();
    }
    async function send(message,captured){
      const signature=JSON.stringify([captured,message]);
      if(!requests.has(signature))requests.set(signature,crypto.randomUUID());
      busy=true;update();
      try{submission=await onSend({...captured,message,request_id:requests.get(signature)});return true;}
      catch(error){onError(error.message);return false;}
      finally{busy=false;update();}
    }
    async function commitText(){
      if(busy)return false;
      if(keyboardMove){if(await send(moveMessage(keyboardMove.position),original.anchor)){keyboardMove=null;sent=true;update();return true;}return false;}
      if(!textEditing||!original)return true;
      const edited=element,value=element.innerText??element.textContent,captured=original.anchor;
      if(value===original.text){cancel();return true;}
      if(!value.trim()){onError('Use the chat to request removing this element.');return false;}
      if(value.length>9000){onError('Keep the replacement under 9,000 characters.');return false;}
      if(await send(`Replace the text with:\n${value}`,captured)){
        if(element!==edited||!original)return true;
        textEditing=false;sent=true;
        if(original.editable===null)element.removeAttribute('contenteditable');else element.setAttribute('contenteditable',original.editable);
        element.style.cursor='grab';update();return true;
      }
      return false;
    }
    /** Navigation never submits an unfinished edit as an accidental side effect. */
    async function finish(){
      if(busy)return false;
      if(textEditing&&(element.innerText??element.textContent)===original?.text){cancel();return true;}
      if(textEditing||keyboardMove){onError('Send or cancel the edit first.');commitButton.focus({preventScroll:true});return false;}
      return true;
    }
    function closeAlign(){if(alignMenu.matches(':popover-open'))alignMenu.hidePopover();}
    function startPosition(){
      restore();remember();sent=false;
      const view=doc.defaultView;
      for(let node=element;node;node=node.parentElement){const style=view.getComputedStyle(node);if(style.perspective!=='none'||!new view.DOMMatrixReadOnly(style.transform).is2D){restore();onError('Use the chat to position this 3D element.');return null;}}
      const canvas=doc.querySelector('.slide')?.getBoundingClientRect();if(!canvas?.width||!canvas.height){restore();return null;}
      const transform=view.getComputedStyle(element).transform;
      element.style.transition='none';
      const probe=(x,y)=>{element.style.transform=`translate(${x}px,${y}px) ${transform==='none'?'':transform}`;return element.getBoundingClientRect();};
      const rect=probe(0,0),x=probe(10,0),y=probe(0,10);probe(0,0);
      const basis={xx:(x.left-rect.left)/10,yx:(x.top-rect.top)/10,xy:(y.left-rect.left)/10,yy:(y.top-rect.top)/10};
      if(!localDelta(0,0,basis)){restore();onError('Use the chat to position this animated element.');return null;}
      return {rect,canvas,transform,basis,dx:0,dy:0};
    }
    function down(event){
      if(busy||blocked()||textEditing||!dragEnabled||!canMove()||event.button!==0||event.ctrlKey||event.metaKey||event.altKey||event.shiftKey||!element.contains(event.target))return;
      drag={startRect:element.getBoundingClientRect(),x:event.clientX,y:event.clientY,moved:false,pointer:event.pointerId};
      element.setPointerCapture(event.pointerId);
    }
    function previewMove(start,position){
      start.dx=position.x*start.canvas.width+start.canvas.left-start.rect.left;start.dy=position.y*start.canvas.height+start.canvas.top-start.rect.top;
      const delta=localDelta(start.dx,start.dy,start.basis);
      element.style.transform=`translate(${delta.x}px,${delta.y}px) ${start.transform==='none'?'':start.transform}`;
      start.position=position;positionPreview=position;update();
    }
    /** Keep a normalized position preview in place when its slide viewport resizes. */
    function reflow(){
      if(!element||!original||!positionPreview||textEditing||drag||busy)return;
      const position=positionPreview,editing=!!keyboardMove,wasSent=sent;
      const start=startPosition();if(!start){keyboardMove=null;update();return;}
      if(editing)keyboardMove=start;sent=wasSent;
      const dx=position.x*start.canvas.width+start.canvas.left-start.rect.left,dy=position.y*start.canvas.height+start.canvas.top-start.rect.top;
      previewMove(start,movePosition(start.rect,dx,dy,start.canvas));
    }
    function motion(event){
      if(!drag||event.pointerId!==drag.pointer)return;
      const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
      if(!drag.moved&&Math.hypot(dx,dy)<4)return;
      if(!drag.moved){const position=keyboardMove||startPosition();if(!position){drag=null;return;}Object.assign(drag,position);}
      event.preventDefault();drag.moved=true;
      const position=movePosition(drag.startRect,dx,dy,drag.canvas);
      previewMove(drag,position);
    }
    function up(event){
      if(!drag||event.pointerId!==drag.pointer)return;
      const finished=drag;drag=null;
      if(element.hasPointerCapture(event.pointerId))element.releasePointerCapture(event.pointerId);
      if(!finished.moved)return;
      suppressClick=true;setTimeout(()=>{suppressClick=false;},0);
      keyboardMove=finished;sent=false;update();commitButton.focus({preventScroll:true});
    }
    function bind(document){
      if(doc===document)return;select(null);doc=document;
      doc.addEventListener('pointerdown',down);
      doc.addEventListener('pointermove',motion);
      doc.addEventListener('pointerup',up);
      doc.addEventListener('pointercancel',()=>{if(drag)cancel();});
      doc.addEventListener('input',event=>{if(textEditing&&element?.contains(event.target))update();});
      doc.addEventListener('dragstart',event=>{if(element?.contains(event.target)&&dragEnabled)event.preventDefault();});
      doc.addEventListener('keydown',event=>{if(!textEditing)return;if(event.key==='Escape'){event.preventDefault();event.stopPropagation();cancel();}else if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();void commitText();}});
      doc.addEventListener('dblclick',event=>{if(element?.contains(event.target)&&canText()){event.preventDefault();startText();}});
    }
    editButton.addEventListener('click',startText);commitButton.addEventListener('click',()=>void commitText());cancelButton.addEventListener('click',cancel);
    moveButton.addEventListener('click',()=>{dragEnabled=!dragEnabled;update();});
    alignButton.addEventListener('click',()=>{
      if(busy||blocked()||!canMove())return;
      if(alignMenu.matches(':popover-open')){closeAlign();return;}
      alignMenu.showPopover();const rect=alignButton.getBoundingClientRect(),box=alignMenu.getBoundingClientRect();
      alignMenu.style.left=`${Math.max(8,Math.min(rect.left,innerWidth-box.width-8))}px`;alignMenu.style.top=`${Math.max(8,Math.min(rect.bottom+4,innerHeight-box.height-8))}px`;alignMenu.querySelector('button').focus();
    });
    alignMenu.addEventListener('toggle',event=>alignButton.setAttribute('aria-expanded',String(event.newState==='open')));
    alignMenu.addEventListener('click',event=>{
      const button=event.target.closest('[data-align]');if(!button||busy||blocked()||!canMove())return;
      closeAlign();keyboardMove=keyboardMove||startPosition();if(!keyboardMove)return;
      previewMove(keyboardMove,alignPosition(element.getBoundingClientRect(),keyboardMove.canvas,button.dataset.align));commitButton.focus();
    });
    alignMenu.addEventListener('keydown',event=>{
      if(event.key==='Escape'){event.preventDefault();closeAlign();alignButton.focus();return;}
      const offsets={ArrowLeft:-1,ArrowRight:1,ArrowUp:-3,ArrowDown:3};
      if(!['Home','End',...Object.keys(offsets)].includes(event.key))return;
      event.preventDefault();const buttons=[...alignMenu.querySelectorAll('button')],index=buttons.indexOf(document.activeElement);buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+offsets[event.key]+buttons.length)%buttons.length].focus();
    });
    tools.addEventListener('keydown',event=>{if(event.key==='Escape'&&!busy){event.preventDefault();cancel();}});
    moveButton.addEventListener('keydown',event=>{
      if(event.ctrlKey||event.metaKey||event.altKey||busy||blocked()||!canMove())return;
      if(event.key==='Escape'&&keyboardMove){event.preventDefault();cancel();return;}
      if(event.key==='Enter'&&keyboardMove){event.preventDefault();void commitText();return;}
      const directions={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]};
      if(!directions[event.key])return;event.preventDefault();
      if(!keyboardMove)keyboardMove=startPosition();if(!keyboardMove)return;
      const [x,y]=directions[event.key],step=event.shiftKey ? .01 : .001;
      keyboardMove.dx+=x*step*keyboardMove.canvas.width;keyboardMove.dy+=y*step*keyboardMove.canvas.height;
      previewMove(keyboardMove,movePosition(keyboardMove.rect,keyboardMove.dx,keyboardMove.dy,keyboardMove.canvas));
    });
    return {bind,select,cancel,finish,reflow,sync:update,isEditing:()=>textEditing||!!keyboardMove,isBusy:()=>busy,consumeClick:()=>suppressClick};
  }
  const api={create,movePosition,moveMessage,alignPosition,localDelta};
  if(typeof module!=='undefined')module.exports=api;else root.SlideEdits=api;
})(typeof window==='undefined'?{}:window);
