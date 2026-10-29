/* Lightweight edit suggestions over the existing version-anchored feedback channel. */
((root)=>{
  'use strict';
  /** Clamp a drag to the slide canvas, independent of preview scale. */
  function movePosition(rect,dx,dy,canvas){
    return {x:Math.max(0,Math.min(Math.max(0,1-rect.width/canvas.width),(rect.left+dx)/canvas.width)),y:Math.max(0,Math.min(Math.max(0,1-rect.height/canvas.height),(rect.top+dy)/canvas.height))};
  }
  /** Describe an exact placement while leaving layout implementation to the agent. */
  function moveMessage(position){return `Move this element's top-left corner to ${(position.x*100).toFixed(1)}% from the left and ${(position.y*100).toFixed(1)}% from the top of the slide. Keep its size and content.`;}
  /** Preview edits locally and submit their original element/version as ordinary feedback. */
  function create({tools,editButton,moveButton,commitButton,cancelButton,sentBadge,anchor,onSend,onChange,onError,blocked=()=>false}){
    let element=null,original=null,textEditing=false,dragEnabled=true,drag=null,doc=null,busy=false,suppressClick=false,sent=false,keyboardMove=null;
    const requests=new Map();
    const canText=()=>!!element&&element.namespaceURI==='http://www.w3.org/1999/xhtml'&&/^(H[1-6]|P|SPAN|LI|DIV|LABEL)$/.test(element.tagName)&&!element.querySelector('div,p,h1,h2,h3,h4,h5,h6,li,svg,img,video,button,input,textarea,table,ul,ol')&&!!element.textContent.trim();
    const canMove=()=>!!element&&(element.namespaceURI==='http://www.w3.org/1999/xhtml'||element.tagName.toLowerCase()==='svg');
    function update(){
      tools.hidden=!element;editButton.hidden=textEditing||!!keyboardMove;moveButton.hidden=textEditing;commitButton.hidden=!textEditing&&!keyboardMove;cancelButton.hidden=!textEditing&&!keyboardMove;
      editButton.disabled=busy||blocked()||!canText();moveButton.disabled=busy||blocked()||!canMove();commitButton.disabled=busy||blocked();cancelButton.disabled=busy||blocked();
      moveButton.setAttribute('aria-pressed',String(dragEnabled));moveButton.title=canMove()?'Drag selected element · arrow keys to position':'Select the surrounding group to move it';
      commitButton.setAttribute('aria-label',keyboardMove?'Send position change':'Send text change');commitButton.title=keyboardMove?'Send position change':'Send text change';
      sentBadge.hidden=!sent||textEditing||busy;tools.classList.toggle('has-suggestion',sent&&!textEditing&&!busy);
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
    }
    function select(next){restore();element=next;textEditing=false;drag=null;keyboardMove=null;dragEnabled=true;sent=false;update();}
    function cancel(){restore();textEditing=false;drag=null;keyboardMove=null;sent=false;update();}
    function startText(){
      if(busy||blocked()||!canText())return;
      restore();remember();sent=false;textEditing=true;element.setAttribute('contenteditable','plaintext-only');element.style.cursor='text';element.style.outline='none';element.focus();update();
    }
    async function send(message,captured){
      const signature=JSON.stringify([captured,message]);
      if(!requests.has(signature))requests.set(signature,crypto.randomUUID());
      busy=true;update();
      try{await onSend({...captured,message,request_id:requests.get(signature)});return true;}
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
    function down(event){
      if(busy||blocked()||textEditing||keyboardMove||!dragEnabled||!canMove()||event.button!==0||event.ctrlKey||event.metaKey||event.altKey||event.shiftKey||!element.contains(event.target))return;
      restore();remember();sent=false;
      const rect=element.getBoundingClientRect();
      drag={x:event.clientX,y:event.clientY,rect,canvas:{width:doc.documentElement.clientWidth,height:doc.documentElement.clientHeight},transform:doc.defaultView.getComputedStyle(element).transform,moved:false,pointer:event.pointerId};
      element.setPointerCapture(event.pointerId);
    }
    function previewMove(start,position){
      element.style.transform=`translate(${position.x*start.canvas.width-start.rect.left}px,${position.y*start.canvas.height-start.rect.top}px) ${start.transform==='none'?'':start.transform}`;
      start.position=position;update();
    }
    function motion(event){
      if(!drag||event.pointerId!==drag.pointer)return;
      const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
      if(!drag.moved&&Math.hypot(dx,dy)<4)return;
      event.preventDefault();drag.moved=true;
      const position=movePosition(drag.rect,dx,dy,drag.canvas);
      previewMove(drag,position);
    }
    async function up(event){
      if(!drag||event.pointerId!==drag.pointer)return;
      const finished=drag,edited=element,captured=original.anchor;drag=null;
      if(element.hasPointerCapture(event.pointerId))element.releasePointerCapture(event.pointerId);
      if(!finished.moved){restore();return;}
      suppressClick=true;setTimeout(()=>{suppressClick=false;},0);
      if(await send(moveMessage(finished.position),captured)){if(element===edited){sent=true;update();}}
      else if(element===edited)cancel();
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
    moveButton.addEventListener('keydown',event=>{
      if(event.ctrlKey||event.metaKey||event.altKey||busy||blocked()||!canMove())return;
      if(event.key==='Escape'&&keyboardMove){event.preventDefault();cancel();return;}
      if(event.key==='Enter'&&keyboardMove){event.preventDefault();void commitText();return;}
      const directions={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]};
      if(!directions[event.key])return;event.preventDefault();
      if(!keyboardMove){restore();remember();sent=false;const rect=element.getBoundingClientRect();keyboardMove={rect,canvas:{width:doc.documentElement.clientWidth,height:doc.documentElement.clientHeight},transform:doc.defaultView.getComputedStyle(element).transform,dx:0,dy:0};}
      const [x,y]=directions[event.key],step=event.shiftKey ? .05 : .01;
      keyboardMove.dx+=x*step*keyboardMove.canvas.width;keyboardMove.dy+=y*step*keyboardMove.canvas.height;
      previewMove(keyboardMove,movePosition(keyboardMove.rect,keyboardMove.dx,keyboardMove.dy,keyboardMove.canvas));
    });
    return {bind,select,cancel,finish:commitText,sync:update,isEditing:()=>textEditing||!!keyboardMove,isBusy:()=>busy,consumeClick:()=>suppressClick};
  }
  const api={create,movePosition,moveMessage};
  if(typeof module!=='undefined')module.exports=api;else root.SlideEdits=api;
})(typeof window==='undefined'?{}:window);
