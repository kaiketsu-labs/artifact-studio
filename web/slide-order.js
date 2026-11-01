/* Thumbnail selection and slide movement, independent of slide content. */
(() => {
  'use strict';
  /** Insert selected slides together, retaining their original relative order. */
  function insertSlides(ids,selected,position){
    const picked=new Set(selected),moving=ids.filter(id=>picked.has(id)),rest=ids.filter(id=>!picked.has(id));
    rest.splice(Math.max(0,Math.min(rest.length,position)),0,...moving);return rest;
  }
  /** Move selected blocks one place, or collect them at either end. */
  function moveSlides(ids,selected,action){
    if(action==='first'||action==='last')return insertSlides(ids,selected,action==='first'?0:ids.length);
    const picked=new Set(selected),result=[...ids],up=action==='up';
    for(let index=up?1:result.length-2;up?index<result.length:index>=0;index+=up?1:-1){
      const other=index+(up?-1:1);
      if(picked.has(result[index])&&!picked.has(result[other]))[result[index],result[other]]=[result[other],result[index]];
    }
    return result;
  }
  /** Select a contiguous range using stable IDs. */
  function slideRange(ids,anchor,target){const first=ids.indexOf(anchor),last=ids.indexOf(target);return first<0||last<0?[target]:ids.slice(Math.min(first,last),Math.max(first,last)+1);}
  const equal=(left,right)=>left.length===right.length&&left.every((id,index)=>id===right[index]);

  /** Find a row-aware insertion point without confusing the next row with the next column. */
  function dropPosition(rectangles,x,y,grid=false){
    if(!rectangles.length)return 0;
    if(!grid){const index=rectangles.findIndex(rect=>y<(rect.top+rect.bottom)/2);return index<0?rectangles.length:index;}
    const rows=[];
    rectangles.forEach((rect,index)=>{let row=rows.at(-1);if(!row||Math.abs(row.top-rect.top)>2){row={top:rect.top,bottom:rect.bottom,items:[]};rows.push(row);}row.items.push({rect,index});});
    const distance=row=>Math.max(row.top-y,0,y-row.bottom);
    const row=rows.reduce((closest,item)=>distance(item)<distance(closest)?item:closest);
    const next=row.items.find(item=>x<(item.rect.left+item.rect.right)/2);
    return next?next.index:row.items.at(-1).index+1;
  }

  /** Bind pointer and keyboard movement to an existing thumbnail rail. */
  function create({rail,read,open,save,contextMenu,notify,command=()=>{},undoAction=async()=>false,blocked=()=>false,layout=()=> 'list',activate=async()=>{},onBusy=()=>{},drop=()=>null}){
    let deckId=null,selected=new Set(),anchor=null,busy=false,gesture=null,suppressClick=false,frame=0,history=[],future=[],selectionEpoch=0;
    const line=document.createElement('div');line.className='slide-drop-line';line.hidden=true;document.body.append(line);
    const ghost=document.createElement('div');ghost.className='slide-drag-count';ghost.hidden=true;ghost.setAttribute('aria-hidden','true');document.body.append(ghost);
    const status=document.createElement('span');status.className='sr-only';status.setAttribute('role','status');rail.after(status);
    const buttonFor=id=>[...rail.querySelectorAll('[data-id]')].find(button=>button.dataset.id===id);
    function sync(){
      const state=read();if(!state)return;
      if(deckId!==state.deckId){cancel();deckId=state.deckId;selected.clear();anchor=null;history=[];future=[];}
      selected=new Set(state.ids.filter(id=>selected.has(id)));
      if(!selected.has(state.activeId)){selected=new Set([state.activeId]);anchor=state.activeId;}
      for(const button of rail.querySelectorAll('[data-id]')){button.classList.toggle('is-selected',selected.has(button.dataset.id));button.setAttribute('aria-pressed',String(selected.has(button.dataset.id)));button.setAttribute('aria-keyshortcuts','Control+ArrowUp Control+ArrowDown Meta+ArrowUp Meta+ArrowDown');}
    }
    async function select(id,event={}){
      if(busy||blocked())return false;const state=read();if(!state)return false;
      const request=++selectionEpoch;let next,nextAnchor=anchor;
      if(event.shiftKey)next=new Set(slideRange(state.ids,anchor||state.activeId,id));
      else if(event.metaKey||event.ctrlKey){next=new Set(selected);if(next.has(id)&&next.size>1)next.delete(id);else next.add(id);nextAnchor=id;}
      else{next=new Set([id]);nextAnchor=id;}
      const active=next.has(id)?id:state.ids.findLast(item=>next.has(item));
      if(!await open(active)||request!==selectionEpoch||read()?.deckId!==state.deckId)return false;
      selected=next;anchor=nextAnchor;sync();buttonFor(active)?.focus({preventScroll:true});
      return true;
    }
    async function commit(next,previous,{record=true,message='Slides moved.',sections=null,targetSection=null}={}){
      if(busy||blocked()||equal(next,previous)&&!sections&&!targetSection)return false;
      const state=read(),selection=[...selected];if(!state)return false;
      busy=true;rail.setAttribute('aria-busy','true');onBusy(true);
      try{
        if(!await save(next,previous,{moving:selection,sections,targetSection,expectedSections:state.sections||[],expectedHash:state.sectionsHash}))return false;
        if(read()?.deckId!==state.deckId)return false;
        if(record){history.push({before:previous,after:next,selection,beforeSections:state.sections||[],afterSections:read().sections||[]});history=history.slice(-30);future=[];}
        sync();buttonFor(read().activeId)?.focus({preventScroll:true});buttonFor(read().activeId)?.scrollIntoView({block:'nearest'});
        status.textContent=message;notify(message,()=>undo());return true;
      }finally{busy=false;rail.removeAttribute('aria-busy');onBusy(false);}
    }
    async function move(action){const state=read();if(state)await commit(moveSlides(state.ids,selected,action),state.ids);}
    async function undo(redo=false){
      const from=redo?future:history,to=redo?history:future,item=from.at(-1),state=read();if(!item||!state||busy)return;
      const expected=redo?item.before:item.after,next=redo?item.after:item.before;
      const expectedSections=redo?item.beforeSections:item.afterSections;
      if(!equal(state.ids,expected)||JSON.stringify(state.sections||[])!==JSON.stringify(expectedSections)){history=[];future=[];notify('Slide order or sections changed. Undo is no longer available.');return;}
      if(await commit(next,expected,{record:false,sections:redo?item.afterSections:item.beforeSections,message:redo?'Move restored.':'Move undone.'})){from.pop();to.push(item);}
    }
    function canMove(action){const state=read();return !!state&&!busy&&!equal(state.ids,moveSlides(state.ids,selected,action));}
    function cancel(){
      cancelAnimationFrame(frame);frame=0;
      if(gesture?.dragging&&rail.hasPointerCapture(gesture.pointerId))rail.releasePointerCapture(gesture.pointerId);
      gesture=null;line.hidden=true;ghost.hidden=true;rail.classList.remove('is-dragging');
      rail.querySelectorAll('.is-moving').forEach(button=>button.classList.remove('is-moving'));
    }
    function updateDrag(){
      const drag=gesture;if(!drag?.dragging)return;
      const bounds=rail.getBoundingClientRect(),inside=drag.x>=bounds.left&&drag.x<=bounds.right&&drag.y>=bounds.top&&drag.y<=bounds.bottom;
      drag.inside=inside;line.hidden=!inside;
      ghost.style.left=`${drag.x+15}px`;ghost.style.top=`${drag.y+12}px`;
      const entries=[...rail.querySelectorAll('[data-id]')].filter(button=>!drag.selected.includes(button.dataset.id)&&button.getClientRects().length);
      const grid=layout()==='grid';
      const position=dropPosition(entries.map(button=>button.getBoundingClientRect()),drag.x,drag.y,grid),remaining=drag.previous.filter(id=>!drag.selected.includes(id));
      drag.position=position<entries.length?remaining.indexOf(entries[position].dataset.id):remaining.length;
      const destination=drop({x:drag.x,y:drag.y,selected:drag.selected,ids:drag.previous,position:drag.position});drag.targetSection=destination?.sectionId||null;if(destination?.position!==undefined)drag.position=destination.position;
      const target=entries[position]||entries.at(-1),rect=target?.getBoundingClientRect();
      const y=rect?(position<entries.length?rect.top-4:rect.bottom+4):bounds.top+8;
      line.classList.toggle('is-grid',grid);
      line.style.left=`${grid?Math.max(bounds.left+2,Math.min(bounds.right-3,rect?(position<entries.length?rect.left-10:rect.right+10):bounds.left+8)):bounds.left+5}px`;
      line.style.width=`${grid?3:bounds.width-10}px`;line.style.height=`${grid?(rect?.height||100)+8:3}px`;
      line.style.top=`${Math.max(bounds.top+2,Math.min(bounds.bottom-2,grid?(rect?.top||bounds.top)-4:y))}px`;
      if(inside){const edge=48,speed=drag.y<bounds.top+edge?-Math.ceil((bounds.top+edge-drag.y)/4):drag.y>bounds.bottom-edge?Math.ceil((drag.y-bounds.bottom+edge)/4):0;rail.scrollTop+=speed;}
      frame=requestAnimationFrame(updateDrag);
    }
    rail.addEventListener('pointerdown',event=>{
      const button=event.target.closest('[data-id]');if(!button||event.button!==0||event.pointerType==='touch'||busy||blocked()||event.shiftKey||event.metaKey||event.ctrlKey)return;
      const state=read();if(!state)return;
      gesture={pointerId:event.pointerId,id:button.dataset.id,x:event.clientX,y:event.clientY,startX:event.clientX,startY:event.clientY,previous:state.ids,selected:selected.has(button.dataset.id)?[...selected]:[button.dataset.id],dragging:false};
    });
    rail.addEventListener('pointermove',event=>{
      const drag=gesture;if(!drag||drag.pointerId!==event.pointerId)return;
      drag.x=event.clientX;drag.y=event.clientY;
      if(!drag.dragging&&Math.hypot(drag.x-drag.startX,drag.y-drag.startY)>7){
        drag.dragging=true;suppressClick=true;rail.setPointerCapture(event.pointerId);rail.classList.add('is-dragging');
        for(const id of drag.selected)buttonFor(id)?.classList.add('is-moving');
        ghost.innerHTML=window.SlideIcons.svg('slides')+`<span>${drag.selected.length}</span>`;ghost.hidden=false;updateDrag();
      }
      if(drag.dragging)event.preventDefault();
    });
    rail.addEventListener('pointerup',async event=>{
      const drag=gesture;if(!drag||drag.pointerId!==event.pointerId)return;
      if(drag.dragging){event.preventDefault();const next=insertSlides(drag.previous,drag.selected,drag.position);cancel();setTimeout(()=>{suppressClick=false;},0);if(drag.inside&&await open(drag.id)){selected=new Set(drag.selected);void commit(next,drag.previous,{targetSection:drag.targetSection});}}
      else cancel();
    });
    rail.addEventListener('pointercancel',()=>{cancel();suppressClick=false;});
    rail.addEventListener('lostpointercapture',()=>{if(gesture)cancel();});
    rail.addEventListener('dragstart',event=>event.preventDefault());
    rail.addEventListener('click',event=>{if(suppressClick){event.preventDefault();return;}const button=event.target.closest('[data-id]');if(button)void select(button.dataset.id,event);});
    rail.addEventListener('contextmenu',async event=>{
      const button=event.target.closest('[data-id],[data-visibility-id]');if(!button)return;event.preventDefault();if(busy)return;
      const id=button.dataset.id||button.dataset.visibilityId;
      if(!selected.has(id)){if(!await select(id))return;}else if(!await open(id))return;
      contextMenu(id,{x:event.clientX,y:event.clientY});
    });
    rail.addEventListener('keydown',async event=>{
      const button=event.target.closest('[data-id]');if(!button||busy||blocked())return;
      if(event.key==='ContextMenu'||event.key==='F10'&&event.shiftKey){event.preventDefault();const id=button.dataset.id;if(!selected.has(id)){if(!await select(id))return;}else if(!await open(id))return;contextMenu(id);return;}
      if(event.altKey||event.isComposing)return;
      if(event.metaKey||event.ctrlKey){
        if(event.key.toLowerCase()==='a'){event.preventDefault();selected=new Set(read().ids);sync();return;}
        if(event.key.toLowerCase()==='z'){event.preventDefault();if(!event.repeat&&!(event.shiftKey?false:await undoAction()))void undo(event.shiftKey);return;}
        if(event.key.toLowerCase()==='n'&&event.metaKey&&event.shiftKey){event.preventDefault();if(!event.repeat)void command('add');return;}
        if(event.key.toLowerCase()==='y'){event.preventDefault();if(!event.repeat)void undo(true);return;}
        if(['d','m'].includes(event.key.toLowerCase())){event.preventDefault();if(!event.repeat)void command(event.key.toLowerCase()==='d'?'duplicate':'add');return;}
        if(['ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();if(!event.repeat)void move(event.shiftKey?(event.key==='ArrowUp'?'first':'last'):(event.key==='ArrowUp'?'up':'down'));}return;
      }
      if(['Delete','Backspace'].includes(event.key)){event.preventDefault();if(!event.repeat)void command('delete');return;}
      const grid=layout()==='grid';
      if(grid&&event.key==='Enter'){event.preventDefault();if(!event.repeat)void activate(button.dataset.id);return;}
      if(!['ArrowUp','ArrowDown','Home','End',...(grid?['ArrowLeft','ArrowRight']:[])].includes(event.key))return;event.preventDefault();
      const buttons=[...rail.querySelectorAll('[data-id]')].filter(item=>item.getClientRects().length),top=buttons[0].getBoundingClientRect().top;
      const columns=grid?buttons.filter(item=>Math.abs(item.getBoundingClientRect().top-top)<2).length:1;
      const ids=buttons.map(item=>item.dataset.id),index=ids.indexOf(button.dataset.id),offset={ArrowUp:-columns,ArrowDown:columns,ArrowLeft:-1,ArrowRight:1}[event.key]||0,next=event.key==='Home'?0:event.key==='End'?ids.length-1:Math.max(0,Math.min(ids.length-1,index+offset));
      await select(ids[next],event);buttonFor(ids[next])?.scrollIntoView({block:'nearest'});
    });
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&gesture){event.preventDefault();event.stopPropagation();cancel();suppressClick=false;}},true);
    return {sync,move,canMove,undo,selection:()=>[...selected],setSelection:ids=>{selected=new Set(ids);anchor=ids[0];sync();},resetHistory:()=>{history=[];future=[];},isBusy:()=>busy,isDragging:()=>!!gesture?.dragging};
  }
  const api={insertSlides,moveSlides,slideRange,dropPosition,create};
  if(typeof module!=='undefined')module.exports=api;
  if(typeof window!=='undefined')window.SlideOrder=api;
})();
