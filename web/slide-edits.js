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
  /** Normalize a signed rotation without losing precision near a full turn. */
  function rotationDegrees(value){return Math.round(((value%360+540)%360-180)*100)/100;}
  /** Measure pointer rotation in the element parent's coordinate system. */
  function rotationDelta(start,current,center,basis){
    const a=localDelta(start.x-center.x,start.y-center.y,basis),b=localDelta(current.x-center.x,current.y-center.y,basis);
    if(!a||!b||Math.hypot(a.x,a.y)<1||Math.hypot(b.x,b.y)<1)return null;
    return rotationDegrees((Math.atan2(b.y,b.x)-Math.atan2(a.y,a.x))*180/Math.PI);
  }
  /** Keep machine-readable intent compact beside the existing anchored envelope. */
  function editIntent(action,values){return {action,values};}
  /** Recover the fixed pivot from opposite rotations, including SVG viewBox geometry. */
  function rotationGeometry(rect,opposite,basis,matrix){
    const localBasis={xx:basis.xx*matrix.a+basis.xy*matrix.b,xy:basis.xx*matrix.c+basis.xy*matrix.d,yx:basis.yx*matrix.a+basis.yy*matrix.b,yy:basis.yx*matrix.c+basis.yy*matrix.d};
    return {basis:localBasis,center:{x:(rect.left+rect.width/2+opposite.left+opposite.width/2)/2,y:(rect.top+rect.height/2+opposite.top+opposite.height/2)/2}};
  }
  /** Describe an exact placement while leaving layout implementation to the agent. */
  function moveMessage(position){return `Move to ${(position.x*100).toFixed(2)}% from the left and ${(position.y*100).toFixed(2)}% from the top.`;}
  /** Preview edits locally and submit their original element/version as ordinary feedback. */
  function create({tools,editButton,moveButton,alignButton,alignMenu,commitButton,cancelButton,sentBadge,selectionBox=null,deleteButton=null,rotationInput=null,properties=null,anchor,onSend,onChange,onError,getFeedbackMode=()=>undefined,deliveryStatus=()=> 'released',blocked=()=>false}){
    let element=null,original=null,textEditing=false,dragEnabled=true,drag=null,doc=null,busy=false,suppressClick=false,sent=false,keyboardMove=null,submission=null,positionPreview=null,rotationPreview=0,pendingDelete=false,rotationDrag=null,propertyValues={},propertyBaseline={};
    const requests=new Map();
    const history=root.ElementCommands?.history()||{record(){},clear(){},undo(){},redo(){},canUndo:()=>false,canRedo:()=>false};
    const clone=value=>value==null?value:JSON.parse(JSON.stringify(value));
    function snapshot(){
      if(!element)return null;
      const move=keyboardMove?Object.fromEntries(['rect','canvas','transform','basis','dx','dy','rotation','position'].map(key=>[key,keyboardMove[key]])):null;
      return clone({html:element.innerHTML,style:element.getAttribute('style'),editable:element.getAttribute('contenteditable'),original,propertyValues,propertyBaseline,move,positionPreview,rotationPreview,pendingDelete,sent,submission});
    }
    function changed(operation){const before=snapshot(),result=operation();if(before&&element)history.record(before,snapshot());return result;}
    function stepHistory(redo=false){
      if(!element||busy||blocked()||textEditing||drag||rotationDrag)return false;
      if(root.slideVoice?.isActive()){onError('Stop dictation before undoing an element edit.');return false;}
      const state=redo?history.redo():history.undo();if(!state)return false;
      if(element.innerHTML!==state.html)element.innerHTML=state.html;
      for(const [name,value] of [['style',state.style],['contenteditable',state.editable]]){if(value===null)element.removeAttribute(name);else element.setAttribute(name,value);}
      original=state.original;propertyValues=state.propertyValues;keyboardMove=state.move;positionPreview=state.positionPreview;rotationPreview=state.rotationPreview;pendingDelete=state.pendingDelete;sent=state.sent;submission=state.submission;
      inspect();propertyBaseline=state.propertyBaseline;if(positionPreview)reflow();update();return true;
    }

    let textVoice=null,textGeneration=0;
    function cancelTextVoice(){textGeneration++;textVoice?.cancel();if(textVoice)textVoice.controls.hidden=true;}
    const canText=()=>!!element&&element.namespaceURI==='http://www.w3.org/1999/xhtml'&&/^(H[1-6]|P|SPAN|LI|DIV|LABEL)$/.test(element.tagName)&&!element.querySelector('div,p,h1,h2,h3,h4,h5,h6,li,svg,img,video,button,input,textarea,table,ul,ol')&&!!element.textContent.trim();
    const canPropertyText=()=>!!element&&!element.querySelector('div,p,h1,h2,h3,h4,h5,h6,li,svg,img,video,button,input,textarea,table,ul,ol,text,tspan')&&!!element.textContent?.trim();
    const propertyDraft=()=>Object.keys(propertyValues).length>0;
    const canMove=()=>!!element&&(element.namespaceURI==='http://www.w3.org/1999/xhtml'||element.tagName.toLowerCase()==='svg');
    const controls=selectionBox?createSelectionControls(selectionBox):null;
    function update(){
      if(textVoice)textVoice.controls.hidden=!textEditing||busy||blocked();
      const draftGeometry=!!keyboardMove||pendingDelete;
      const editingProperties=propertyDraft();
      tools.hidden=!element;editButton.hidden=textEditing||draftGeometry;moveButton.hidden=textEditing||pendingDelete;commitButton.hidden=!textEditing&&!draftGeometry;cancelButton.hidden=!textEditing&&!draftGeometry;
      alignButton.hidden=textEditing||pendingDelete;alignButton.disabled=busy||blocked()||editingProperties||!canMove();
      if(!element||alignButton.hidden||alignButton.disabled)closeAlign();
      editButton.disabled=busy||blocked()||!canText();moveButton.disabled=busy||blocked()||editingProperties||!canMove();commitButton.disabled=busy||blocked();cancelButton.disabled=busy||blocked();
      moveButton.setAttribute('aria-pressed',String(dragEnabled));moveButton.title=canMove()?'Drag selected element · arrow keys to position':'Select the surrounding group to move it';
      const commitLabel=pendingDelete?'Send deletion request':keyboardMove?'Send element change':'Send text change';
      commitButton.setAttribute('aria-label',commitLabel);commitButton.title=commitLabel;
      if(deleteButton){deleteButton.hidden=textEditing||!!keyboardMove||pendingDelete;deleteButton.disabled=busy||blocked()||editingProperties||!element;}
      if(rotationInput){rotationInput.closest('label').hidden=textEditing||pendingDelete;rotationInput.disabled=busy||blocked()||editingProperties||!canMove();if(rotationInput!==rotationInput.ownerDocument.activeElement)rotationInput.value=String(keyboardMove?.rotation??rotationPreview);}
      if(selectionBox){selectionBox.classList.toggle('deletion-proposal',pendingDelete);selectionBox.classList.toggle('element-manipulation-disabled',busy||blocked()||textEditing||pendingDelete||editingProperties||!canMove()||!dragEnabled);selectionBox.setAttribute('aria-label',pendingDelete?'Selected element · deletion requested':'Selected element · drag border or use arrow keys to move');}

      const draft=draftGeometry||editingProperties||textEditing&&original&&(element.innerText??element.textContent)!==original.text;
      const status=draft?'draft':deliveryStatus(submission),label=({draft:'Unsent edit',queued:'Saved privately · not sent',released:'Saved for agent',answered:'Agent replied'})[status]||'Saved for agent';
      sentBadge.hidden=(!sent&&!draft)||busy;
      if(sentBadge.dataset.state!==status){sentBadge.dataset.state=status;sentBadge.title=label;sentBadge.setAttribute('aria-label',label);sentBadge.innerHTML=root.SlideIcons.svg(({draft:'edit',queued:'batch',released:'send',answered:'notes'})[status]||'send');const text=document.createElement('span');text.className='sr-only';text.textContent=label;sentBadge.append(text);}
      tools.classList.toggle('has-suggestion',!!(sent||draft));
      properties?.setState({dirty:editingProperties,busy,sent,disabled:blocked()||draftGeometry||textEditing,objectDraft:draftGeometry||textEditing});
      if(element)onChange(element);
    }
    function remember(){
      if(original)return;
      original={html:element.innerHTML,style:element.getAttribute('style'),editable:element.getAttribute('contenteditable'),text:element.innerText??element.textContent,anchor:anchor()};
    }
    function restore(){
      cancelTextVoice();
      properties?.cancelVoice?.();
      if(!original||!element)return;
      if(element.innerHTML!==original.html)element.innerHTML=original.html;
      for(const [name,value] of [['style',original.style],['contenteditable',original.editable]]){if(value===null)element.removeAttribute(name);else element.setAttribute(name,value);}
      original=null;
      positionPreview=null;
      rotationPreview=0;
      pendingDelete=false;
    }
    /** Read the authored size unit when possible and computed values otherwise. */
    function inspect(){
      if(!properties||!element){properties?.clear();return;}
      const typography=root.ElementProperties.typographyOf(element,doc.defaultView);
      // Keep viewport-based slide sizes stable while the editing canvas is resized or zoomed.
      const unit=typography.font_size_unit,scale=['cqw','vw'].includes(unit)?1280/doc.documentElement.clientWidth:['cqh','vh'].includes(unit)?720/doc.documentElement.clientHeight:1;
      typography.font_size_px=Math.round(typography.font_size_px*scale*1000)/1000;
      propertyBaseline={text:element.textContent||'',...typography};
      properties.setSelection({key:element,label:element.tagName.toLowerCase(),text:propertyBaseline.text,canText:canPropertyText(),typography});
    }
    function select(next){history.clear();closeAlign();restore();propertyValues={};element=next;textEditing=false;drag=null;keyboardMove=null;pendingDelete=false;rotationDrag=null;dragEnabled=true;sent=false;submission=null;if(rotationInput)rotationInput.value='0';inspect();update();}
    function cancel(){history.clear();closeAlign();restore();propertyValues={};textEditing=false;drag=null;keyboardMove=null;pendingDelete=false;rotationDrag=null;sent=false;submission=null;if(rotationInput)rotationInput.value='0';inspect();update();}
    function startText(){
      if(busy||blocked()||keyboardMove||pendingDelete||propertyDraft()||!canText())return;
      restore();remember();sent=false;textEditing=true;element.setAttribute('contenteditable','plaintext-only');element.style.cursor='text';element.style.outline='none';element.focus();
      bindTextVoice();update();
    }
    /** Keep dictation in the original text edit, with controls outside authored slide markup. */
    function bindTextVoice(){
      if(!root.slideVoice)return;
      const target=element;
      textVoice=root.slideVoice.register(target,{
        label:'selected element text',controlsHost:tools,visible:()=>textEditing&&element===target,
        capture(){
          const selection=doc.getSelection(),range=selection?.rangeCount?selection.getRangeAt(0):null,value=target.textContent;
          let start=value.length,end=start;
          if(range&&target.contains(range.startContainer)&&target.contains(range.endContainer)){
            const prefix=doc.createRange();prefix.selectNodeContents(target);prefix.setEnd(range.startContainer,range.startOffset);start=prefix.toString().length;
            prefix.setEnd(range.endContainer,range.endOffset);end=prefix.toString().length;
          }
          return {generation:textGeneration,value,start,end};
        },
        available:context=>textEditing&&!busy&&!blocked()&&element===target&&target.isConnected&&context.generation===textGeneration,
        insert(text,context){
          if(!textEditing||busy||blocked()||element!==target||context.generation!==textGeneration||target.textContent!==context.value)return false;
          const value=target.textContent,before=value.slice(0,context.start),after=value.slice(context.end);
          const insertion=(before&&!/\s$/.test(before)&&! /^[,.;:!?)]/.test(text)?' ':'')+text+(after&&!/^\s|^[,.;:!?)]/.test(after)?' ':'');
          if(before.length+insertion.length+after.length>9000)return false;
          const nodes=[],walker=doc.createTreeWalker(target,4);while(walker.nextNode())nodes.push(walker.currentNode);
          function point(offset){for(const node of nodes){if(offset<=node.textContent.length)return [node,offset];offset-=node.textContent.length;}return [target,target.childNodes.length];}
          const range=doc.createRange();range.setStart(...point(context.start));range.setEnd(...point(context.end));range.deleteContents();
          const inserted=doc.createTextNode(insertion);range.insertNode(inserted);range.setStartAfter(inserted);range.collapse(true);
          const selection=doc.getSelection();selection.removeAllRanges();selection.addRange(range);
          context.value=target.textContent;context.start=before.length+insertion.length;context.end=context.start;
          target.dispatchEvent(new doc.defaultView.InputEvent('input',{bubbles:true,inputType:'insertText',data:insertion}));return true;
        }
      });

    }
    async function send(message,captured,intent,expectedMode){
      const signature=JSON.stringify([captured,message,intent]);
      if(!requests.has(signature))requests.set(signature,crypto.randomUUID());
      busy=true;update();
      try{submission=await onSend({...captured,message,edit_intent:intent,request_id:requests.get(signature),expected_mode:expectedMode});history.clear();return true;}
      catch(error){onError(error.message);return false;}
      finally{busy=false;update();}
    }
    async function commitText(){
      if(busy)return false;
      const editing=element,expectedMode=getFeedbackMode();
      if(textVoice && root.slideVoice?.isActive(textVoice.input) && !await textVoice.stop()){onError('Recording ended without sending. Review your edit before sending.');return false;}
      if(properties?.stopVoice && !await properties.stopVoice()){onError('Recording ended without sending. Review your edit before sending.');return false;}
      if(busy||element!==editing)return false;
      if(getFeedbackMode()!==expectedMode){onError('Feedback mode changed. Review the current setting before sending.');return false;}
      if(pendingDelete){if(await send('Delete this element.',original.anchor,editIntent('delete',{}),expectedMode)){pendingDelete=false;sent=true;update();return true;}return false;}
      if(keyboardMove){
        const {position,rotation=0}=keyboardMove,values={},parts=[];
        if(position){const rect=element.getBoundingClientRect(),actual={x:(rect.left-keyboardMove.canvas.left)/keyboardMove.canvas.width,y:(rect.top-keyboardMove.canvas.top)/keyboardMove.canvas.height};Object.assign(values,{x:actual.x,y:actual.y,position_reference:'visual_top_left'});parts.push(moveMessage(actual));}
        if(rotation){Object.assign(values,{rotation_degrees:rotation,rotation_reference:'original_element_local',rotation_origin:'existing_transform_origin'});parts.push(`Rotate ${rotation}° clockwise from original.`);}
        if(!parts.length){cancel();return true;}
        const action=position&&rotation?'transform':rotation?'rotate':'move';
        if(await send(parts.join(' '),original.anchor,editIntent(action,values),expectedMode)){keyboardMove=null;sent=true;update();return true;}return false;
      }
      if(propertyDraft()&&original){
        const values={...propertyValues};
        if('text' in values&&(!values.text.trim()||values.text.length>9000)){onError('Keep replacement text between 1 and 9,000 characters.');return false;}
        const hasText='text' in values,hasStyle=Object.keys(values).some(key=>key!=='text'),action=hasText?(hasStyle?'update':'replace_text'):'style';
        if(await send('Update element properties.',original.anchor,editIntent(action,values),expectedMode)){propertyValues={};sent=true;inspect();update();return true;}return false;
      }
      if(!textEditing||!original)return true;
      const edited=element,value=element.innerText??element.textContent,captured=original.anchor;
      if(value===original.text){cancel();return true;}
      if(!value.trim()){onError('Use the chat to request removing this element.');return false;}
      if(value.length>9000){onError('Keep the replacement under 9,000 characters.');return false;}
      if(await send('Replace text.',captured,editIntent('replace_text',{text:value}),expectedMode)){
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
      if(textEditing||keyboardMove||pendingDelete||propertyDraft()){onError('Send or cancel the edit first.');commitButton.focus({preventScroll:true});return false;}
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
      return {rect,canvas,transform,basis,dx:0,dy:0,rotation:0};
    }
    /** Convert an outer selection handle's pointer to iframe viewport coordinates. */
    function pointerPoint(event,outer=false){
      if(!outer)return {x:event.clientX,y:event.clientY};
      const frame=doc.defaultView.frameElement,rect=frame.getBoundingClientRect();
      return {x:(event.clientX-rect.left)*doc.documentElement.clientWidth/rect.width,y:(event.clientY-rect.top)*doc.documentElement.clientHeight/rect.height};
    }
    function beginMove(event,outer=false){
      if(busy||blocked()||textEditing||pendingDelete||propertyDraft()||!dragEnabled||!canMove()||event.button!==0||event.ctrlKey||event.metaKey||event.altKey||event.shiftKey)return;
      const point=pointerPoint(event,outer),capture=outer?event.currentTarget:element;
      drag={before:snapshot(),startRect:element.getBoundingClientRect(),x:point.x,y:point.y,moved:false,pointer:event.pointerId,outer,capture};
      capture.setPointerCapture(event.pointerId);
    }
    function down(event){
      if(element?.contains(event.target))beginMove(event);
    }
    function renderTransform(start){
      const delta=localDelta(start.dx,start.dy,start.basis);
      if(!delta)return;
      element.style.transform=`translate(${delta.x}px,${delta.y}px) ${start.transform==='none'?'':start.transform} rotate(${start.rotation||0}deg)`;
    }
    function previewMove(start,position){
      start.dx=position.x*start.canvas.width+start.canvas.left-start.rect.left;start.dy=position.y*start.canvas.height+start.canvas.top-start.rect.top;
      renderTransform(start);
      const actual=element.getBoundingClientRect();
      start.dx+=position.x*start.canvas.width+start.canvas.left-actual.left;start.dy+=position.y*start.canvas.height+start.canvas.top-actual.top;
      renderTransform(start);
      start.position=position;positionPreview=position;update();
    }
    /** Keep a normalized position preview in place when its slide viewport resizes. */
    function reflow(){
      if(!element||!original||!positionPreview||textEditing||drag||busy)return;
      const position=positionPreview,editing=!!keyboardMove,wasSent=sent,rotation=rotationPreview;
      const start=startPosition();if(!start){keyboardMove=null;update();return;}
      start.rotation=rotation;rotationPreview=rotation;if(editing)keyboardMove=start;sent=wasSent;
      const dx=position.x*start.canvas.width+start.canvas.left-start.rect.left,dy=position.y*start.canvas.height+start.canvas.top-start.rect.top;
      previewMove(start,movePosition(start.rect,dx,dy,start.canvas));
    }
    function motion(event){
      if(!drag||event.pointerId!==drag.pointer)return;
      const point=pointerPoint(event,drag.outer),dx=point.x-drag.x,dy=point.y-drag.y;
      if(!drag.moved&&Math.hypot(dx,dy)<4)return;
      if(!drag.moved){const position=keyboardMove||startPosition();if(!position){drag=null;return;}const {rect,canvas,transform,basis,rotation}=position;Object.assign(drag,{rect,canvas,transform,basis,rotation});}
      event.preventDefault();drag.moved=true;
      const position=movePosition(drag.startRect,dx,dy,drag.canvas);
      previewMove(drag,position);
    }
    function up(event){
      if(!drag||event.pointerId!==drag.pointer)return;
      const finished=drag;drag=null;
      if(finished.capture.hasPointerCapture(event.pointerId))finished.capture.releasePointerCapture(event.pointerId);
      if(!finished.moved)return;
      suppressClick=true;setTimeout(()=>{suppressClick=false;},0);
      keyboardMove=finished;sent=false;update();history.record(finished.before,snapshot());commitButton.focus({preventScroll:true});
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
      if(busy||blocked()||propertyDraft()||!canMove())return;
      if(alignMenu.matches(':popover-open')){closeAlign();return;}
      alignMenu.showPopover();const rect=alignButton.getBoundingClientRect(),box=alignMenu.getBoundingClientRect();
      alignMenu.style.left=`${Math.max(8,Math.min(rect.left,innerWidth-box.width-8))}px`;alignMenu.style.top=`${Math.max(8,Math.min(rect.bottom+4,innerHeight-box.height-8))}px`;alignMenu.querySelector('button').focus();
    });
    alignMenu.addEventListener('toggle',event=>alignButton.setAttribute('aria-expanded',String(event.newState==='open')));
    alignMenu.addEventListener('click',event=>{
      const button=event.target.closest('[data-align]');if(!button||busy||blocked()||propertyDraft()||!canMove())return;
      changed(()=>{closeAlign();keyboardMove=keyboardMove||startPosition();if(!keyboardMove)return;
      previewMove(keyboardMove,alignPosition(element.getBoundingClientRect(),keyboardMove.canvas,button.dataset.align));commitButton.focus();});
    });
    alignMenu.addEventListener('keydown',event=>{
      if(event.key==='Escape'){event.preventDefault();closeAlign();alignButton.focus();return;}
      const offsets={ArrowLeft:-1,ArrowRight:1,ArrowUp:-3,ArrowDown:3};
      if(!['Home','End',...Object.keys(offsets)].includes(event.key))return;
      event.preventDefault();const buttons=[...alignMenu.querySelectorAll('button')],index=buttons.indexOf(document.activeElement);buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+offsets[event.key]+buttons.length)%buttons.length].focus();
    });
    tools.addEventListener('keydown',event=>{if(event.key==='Escape'&&!busy){event.preventDefault();cancel();}});
    function positionKey(event){if(event.defaultPrevented)return;return changed(()=>applyPositionKey(event));}
    function applyPositionKey(event){
      if(event.ctrlKey||event.metaKey||event.altKey||busy||blocked()||textEditing||pendingDelete||propertyDraft()||!canMove())return;
      if(event.key==='Escape'&&keyboardMove){event.preventDefault();cancel();return;}
      if(event.key==='Enter'&&keyboardMove){event.preventDefault();void commitText();return;}
      const directions={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]};
      if(!directions[event.key])return;event.preventDefault();
      if(!keyboardMove)keyboardMove=startPosition();if(!keyboardMove)return;
      const [x,y]=directions[event.key],step=event.shiftKey ? .01 : .001;
      previewMove(keyboardMove,movePosition(element.getBoundingClientRect(),x*step*keyboardMove.canvas.width,y*step*keyboardMove.canvas.height,keyboardMove.canvas));
    }
    moveButton.addEventListener('keydown',positionKey);
    selectionBox?.addEventListener('keydown',event=>{if(event.target===selectionBox)positionKey(event);});
    function stageDelete(){return changed(()=>{
      if(busy||blocked()||!element||textEditing||pendingDelete)return false;
      if(root.slideVoice?.isActive()){onError('Stop dictation before deleting an element.');return false;}
      restore();propertyValues={};keyboardMove=null;remember();pendingDelete=true;sent=false;element.style.visibility='hidden';update();commitButton.focus({preventScroll:true});return true;
    });}
    deleteButton?.addEventListener('click',stageDelete);
    function previewRotation(value){return rotationDrag?applyRotation(value):changed(()=>applyRotation(value));}
    function applyRotation(value){
      if(busy||blocked()||textEditing||pendingDelete||propertyDraft()||!canMove()||!Number.isFinite(value))return;
      keyboardMove=keyboardMove||startPosition();if(!keyboardMove)return;
      keyboardMove.rotation=rotationDegrees(value);rotationPreview=keyboardMove.rotation;sent=false;renderTransform(keyboardMove);update();
    }
    rotationInput?.addEventListener('input',()=>{if(rotationInput.value.trim()&&rotationInput.validity.valid)previewRotation(rotationInput.valueAsNumber);});
    rotationInput?.addEventListener('keydown',event=>{if(event.key==='Enter'&&keyboardMove){event.preventDefault();void commitText();}});
    if(controls){
      for(const edge of controls.edges){edge.addEventListener('pointerdown',event=>{event.preventDefault();beginMove(event,true);});edge.addEventListener('pointermove',motion);edge.addEventListener('pointerup',up);edge.addEventListener('pointercancel',cancel);}
      controls.rotation.addEventListener('pointerdown',event=>{
        if(event.button!==0||busy||blocked()||textEditing||pendingDelete||propertyDraft()||!canMove())return;
        const before=snapshot();event.preventDefault();keyboardMove=keyboardMove||startPosition();if(!keyboardMove)return;
        const view=doc.defaultView,matrix=new view.DOMMatrixReadOnly(view.getComputedStyle(element).transform),rect=element.getBoundingClientRect(),transform=element.style.transform;
        let opposite;
        try{element.style.transform=`${transform} rotate(180deg)`;opposite=element.getBoundingClientRect();}
        finally{element.style.transform=transform;}
        const geometry=rotationGeometry(rect,opposite,keyboardMove.basis,matrix);
        rotationDrag={before,pointer:event.pointerId,start:pointerPoint(event,true),...geometry,rotation:keyboardMove.rotation};
        controls.rotation.setPointerCapture(event.pointerId);
      });
      controls.rotation.addEventListener('pointermove',event=>{
        if(!rotationDrag||event.pointerId!==rotationDrag.pointer)return;
        const delta=rotationDelta(rotationDrag.start,pointerPoint(event,true),rotationDrag.center,rotationDrag.basis);
        if(delta===null)return;event.preventDefault();const value=rotationDrag.rotation+delta;
        previewRotation(event.shiftKey?Math.round(value/15)*15:value);
      });
      controls.rotation.addEventListener('pointerup',event=>{
        if(!rotationDrag||event.pointerId!==rotationDrag.pointer)return;
        const before=rotationDrag.before;rotationDrag=null;history.record(before,snapshot());controls.rotation.releasePointerCapture(event.pointerId);update();commitButton.focus({preventScroll:true});
      });
      controls.rotation.addEventListener('pointercancel',cancel);
      controls.rotation.addEventListener('keydown',event=>{
        if(event.key==='Escape'&&keyboardMove){event.preventDefault();event.stopPropagation();cancel();return;}
        if(event.key==='Enter'&&keyboardMove){event.preventDefault();void commitText();return;}
        if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;
        event.preventDefault();event.stopPropagation();const sign=['ArrowLeft','ArrowDown'].includes(event.key)?-1:1;
        previewRotation((keyboardMove?.rotation||0)+sign*(event.shiftKey?15:1));
      });
    }
    /** Apply only the current property patch to the provisional DOM. */
    function previewProperties(values){return changed(()=>applyProperties(values));}
    function applyProperties(values){
      if(!element||busy||blocked()||textEditing||keyboardMove||pendingDelete)return;
      remember();Object.assign(propertyValues,values);
      for(const key of Object.keys(propertyValues))if(propertyValues[key]===propertyBaseline[key])delete propertyValues[key];
      if(('font_size' in propertyValues||'font_size_unit' in propertyValues)){propertyValues.font_size=values.font_size??propertyValues.font_size??propertyBaseline.font_size;propertyValues.font_size_unit=values.font_size_unit??propertyValues.font_size_unit??propertyBaseline.font_size_unit;}
      if(element.innerHTML!==original.html)element.innerHTML=original.html;
      if(original.style===null)element.removeAttribute('style');else element.setAttribute('style',original.style);
      root.ElementProperties.applyTextPatch(element,propertyValues,propertyBaseline);
      sent=false;update();
    }
    function nudge(key,step=1){return positionKey({key,shiftKey:step===10,preventDefault(){},ctrlKey:false,metaKey:false,altKey:false});}
    return {undo:()=>stepHistory(),redo:()=>stepHistory(true),canUndo:history.canUndo,canRedo:history.canRedo,delete:stageDelete,nudge,edit:startText,canEdit:canPropertyText,hasSelection:()=>!!element,bind,select,cancel,finish,reflow,previewProperties,commit:commitText,sync:update,isEditing:()=>textEditing||propertyDraft()||!!drag||!!rotationDrag||!!keyboardMove||pendingDelete,isBusy:()=>busy,consumeClick:()=>suppressClick};
  }
  /** Add a moveable selection border and an accessible rotation handle. */
  function createSelectionControls(box){
    box.classList.add('element-selection-controls');box.tabIndex=0;box.setAttribute('role','group');
    const document=box.ownerDocument,edges=[];
    for(const side of ['top','right','bottom','left']){const edge=document.createElement('div');edge.className=`element-move-edge element-move-${side}`;edge.setAttribute('aria-hidden','true');box.append(edge);edges.push(edge);}
    for(const corner of ['nw','ne','se','sw']){const handle=document.createElement('i');handle.className=`element-selection-handle element-selection-${corner}`;handle.setAttribute('aria-hidden','true');box.append(handle);}
    const rotation=document.createElement('button');rotation.type='button';rotation.className='element-rotation-handle';rotation.setAttribute('aria-label','Rotate element · arrow keys adjust by 1°, Shift by 15°');rotation.title='Drag to rotate · Shift snaps to 15°';box.append(rotation);
    return {edges,rotation};
  }
  const api={create,movePosition,moveMessage,alignPosition,localDelta,rotationDegrees,rotationDelta,rotationGeometry,editIntent};
  if(typeof module!=='undefined')module.exports=api;else root.SlideEdits=api;
})(typeof window==='undefined'?{}:window);
