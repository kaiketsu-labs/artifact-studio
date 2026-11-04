/* PowerPoint-style groups share slide order; folding is a private view preference. */
((root)=>{
  'use strict';
  const copy=value=>JSON.parse(JSON.stringify(value||[])),flat=groups=>groups.flatMap(group=>group.slide_ids);
  function split(groups,ids,slideId,id,title,defaultId){
    const result=copy(groups),position=ids.indexOf(slideId);if(position<0)throw Error('Choose a slide first.');
    if(!result.length){if(position)result.push({id:defaultId,title:'Default section',slide_ids:ids.slice(0,position)});result.push({id,title,slide_ids:ids.slice(position)});return result;}
    const index=result.findIndex(group=>group.slide_ids.includes(slideId)),offset=result[index].slide_ids.indexOf(slideId);
    if(!offset)throw Error('This slide already starts a section. Rename it from the section menu.');
    {const tail=result[index].slide_ids.splice(offset);result.splice(index+1,0,{id,title,slide_ids:tail});}
    return result;
  }
  function remove(groups,id){
    const result=copy(groups),index=result.findIndex(group=>group.id===id);if(index<0)return result;
    const [removed]=result.splice(index,1);if(index)result[index-1].slide_ids.push(...removed.slide_ids);else if(result.length)result[0].slide_ids.unshift(...removed.slide_ids);return result;
  }
  /** Changed neighbours identify the destination of keyboard moves; headers can name it explicitly. */
  function moveMembers(groups,previous,next,moving,target=null){
    const result=copy(groups);if(!result.length)return result;
    const selected=new Set(moving),owners=new Map(groups.flatMap(group=>group.slide_ids.map(id=>[id,group.id]))),before=new Map(previous.map((id,index)=>[id,index])),after=new Map(next.map((id,index)=>[id,index]));
    for(const id of moving){
      const crossed=previous.filter(other=>!selected.has(other)&&(before.get(other)<before.get(id))!==(after.get(other)<after.get(id))).sort((a,b)=>Math.abs(after.get(a)-after.get(id))-Math.abs(after.get(b)-after.get(id)));
      if(target||crossed.length)owners.set(id,target||owners.get(crossed[0]));
    }
    for(const group of result)group.slide_ids=next.filter(id=>owners.get(id)===group.id);
    if(JSON.stringify(flat(result))!==JSON.stringify(next))throw Error('Move a whole section using its menu, or move slides into a section.');
    return result;
  }
  function create({rail,read,active,prepare,blocked,save,onBusy,escape,svg,toast}){
    const doc=rail.ownerDocument,$=id=>doc.getElementById(id),dialog=$('section-dialog'),menu=$('section-menu');
    const folded=new Map();let busy=false,pending=null,menuId=null;
    function collapsed(deckId){if(!folded.has(deckId)){let saved=[];try{saved=JSON.parse(root.localStorage.getItem('artifact-studio:sections:'+deckId)||'[]');}catch{}folded.set(deckId,new Set(Array.isArray(saved)?saved:[]));}return folded.get(deckId);}
    function persist(deckId){try{root.localStorage.setItem('artifact-studio:sections:'+deckId,JSON.stringify([...collapsed(deckId)]));}catch{}}
    function applyFold(){const state=read();if(!state)return;for(const group of rail.querySelectorAll('.slide-section')){const closed=collapsed(state.id).has(group.dataset.sectionId),button=group.querySelector('.section-toggle');group.querySelector('.section-slides').hidden=closed;button.setAttribute('aria-expanded',String(!closed));}}
    function toggle(id,closed){const state=read();if(!state)return;const values=collapsed(state.id);if(closed??!values.has(id))values.add(id);else values.delete(id);persist(state.id);applyFold();}
    function render(state,entries){
      if(!state.sections?.length)return state.slides.map(slide=>entries.get(slide.id)).join('');
      return state.sections.map(group=>`<div class="slide-section" data-section-id="${escape(group.id)}"><div class="section-header"><button class="section-toggle" type="button" aria-expanded="${!collapsed(state.id).has(group.id)}" aria-controls="section-slides-${escape(group.id)}">${svg('right')}<span class="section-title">${escape(group.title)}</span> <span class="section-count" aria-hidden="true">${group.slide_ids.length}</span><span class="section-question" hidden aria-hidden="true">${svg('question')}</span></button><button class="section-options" type="button" aria-label="Section options: ${escape(group.title)}" aria-haspopup="menu" aria-controls="section-menu">${svg('more')}</button></div><div class="section-slides" id="section-slides-${escape(group.id)}" ${collapsed(state.id).has(group.id)?'hidden':''}>${group.slide_ids.map(id=>entries.get(id)).join('')}</div></div>`).join('');
    }
    function sync(state){
      const boundary=state?.sections?.find(group=>group.slide_ids[0]===active()),control=$('add-section'),label=boundary?`Rename section: ${boundary.title}`:'Add section';control.disabled=!state||busy||blocked();control.setAttribute('aria-label',label);control.title=label;control.innerHTML=svg(boundary?'edit':'sectionAdd');
      if(!state)return;const slides=new Map(state.slides.map(slide=>[slide.id,slide]));
      for(const element of rail.querySelectorAll('.slide-section')){const group=state.sections.find(item=>item.id===element.dataset.sectionId);if(!group)continue;element.querySelector('.section-title').textContent=group.title;element.querySelector('.section-count').textContent=group.slide_ids.length;element.querySelector('.section-options').setAttribute('aria-label','Section options: '+group.title);const count=group.slide_ids.reduce((total,id)=>total+root.SlideQuestions.pending(slides.get(id)?.feedback||[]).length,0),button=element.querySelector('.section-toggle');button.setAttribute('aria-label',`${group.title} ${group.slide_ids.length} slides${count?` · ${count} question${count===1?'':'s'} awaiting your answer`:''}`);button.title=group.title;const badge=element.querySelector('.section-question');badge.hidden=!count;for(const control of element.querySelectorAll('.section-header button'))control.disabled=busy||blocked();}
      applyFold();
    }
    async function write(groups,baseline){
      if(busy||blocked()||read()?.id!==baseline.id||!await prepare())return false;
      busy=true;onBusy();sync(read());$('section-submit').disabled=true;
      try{await save(groups.length?flat(groups):baseline.slides.map(slide=>slide.id),groups,baseline);return true;}
      catch(error){if(dialog.open)$('section-error').textContent=error.message;else toast(error.message,true);return false;}
      finally{busy=false;$('section-submit').disabled=false;onBusy();sync(read());}
    }
    async function edit(id=null){
      if(busy||blocked()||!await prepare())return;
      const state=read();if(!state)return;if(!id&&state.sections?.some(group=>group.slide_ids[0]===active()))return;const group=state.sections?.find(item=>item.id===id);
      pending={baseline:{id:state.id,sections:copy(state.sections),sections_hash:state.sections_hash,slides:state.slides.map(slide=>({id:slide.id}))},id,slideId:active()};$('section-dialog-title').textContent=id?'Rename section':'New section';$('section-name').value=group?.title||'Section';$('section-submit').textContent=id?'Save':'Add';$('section-error').textContent='';dialog.showModal();$('section-name').select();
    }
    function focusSection(id){const target=id&&[...rail.querySelectorAll('.slide-section')].find(group=>group.dataset.sectionId===id)?.querySelector('.section-toggle');(target||rail.querySelector('[aria-current="true"]'))?.focus({preventScroll:true});}
    function openMenu(element){const state=read();if(!state||busy||blocked())return;menuId=element.closest('.slide-section').dataset.sectionId;const index=state.sections.findIndex(group=>group.id===menuId);menu.querySelector('[data-section-action="up"]').disabled=index===0;menu.querySelector('[data-section-action="down"]').disabled=index===state.sections.length-1;menu.showPopover();const bounds=element.getBoundingClientRect();menu.style.left=`${Math.min(bounds.left,root.innerWidth-menu.offsetWidth-8)}px`;menu.style.top=`${Math.min(bounds.bottom+3,root.innerHeight-menu.offsetHeight-8)}px`;menu.querySelector('button').focus();}
    rail.addEventListener('click',event=>{const header=event.target.closest('.section-header');if(!header)return;if(event.target.closest('.section-options'))openMenu(event.target.closest('button'));else if(event.target.closest('.section-toggle'))toggle(header.parentElement.dataset.sectionId);});
    rail.addEventListener('contextmenu',event=>{const header=event.target.closest('.section-header');if(header){event.preventDefault();openMenu(header.querySelector('.section-options'));}});
    rail.addEventListener('keydown',event=>{const header=event.target.closest('.section-header');if(!header)return;const id=header.parentElement.dataset.sectionId;if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();toggle(id,event.key==='ArrowLeft');}if(event.key==='F2'){event.preventDefault();void edit(id);}if(event.key==='ContextMenu'||event.key==='F10'&&event.shiftKey){event.preventDefault();openMenu(header.querySelector('.section-options'));}});
    menu.addEventListener('click',async event=>{const action=event.target.closest('[data-section-action]')?.dataset.sectionAction;if(!action)return;menu.hidePopover();if(action==='rename'){void edit(menuId);return;}const state=read();if(!state)return;if(action==='collapse'||action==='expand'){for(const group of state.sections)toggle(group.id,action==='collapse');return;}let groups=copy(state.sections);if(action==='remove')groups=remove(groups,menuId);else{const index=groups.findIndex(group=>group.id===menuId),next=index+(action==='up'?-1:1);if(next<0||next>=groups.length)return;[groups[index],groups[next]]=[groups[next],groups[index]];}if(await write(groups,state))focusSection(action==='remove'?null:menuId);});
    menu.addEventListener('keydown',event=>{const buttons=[...menu.querySelectorAll('button:not(:disabled)')],index=buttons.indexOf(doc.activeElement);if(['ArrowUp','ArrowDown','Home','End'].includes(event.key)){event.preventDefault();buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length]?.focus();}if(event.key==='Escape'){menu.hidePopover();rail.querySelector(`[data-section-id="${CSS.escape(menuId)}"] .section-options`)?.focus();}});
    $('add-section').addEventListener('click',()=>{const boundary=read()?.sections?.find(group=>group.slide_ids[0]===active());void edit(boundary?.id||null);});$('section-cancel').addEventListener('click',()=>dialog.close());
    dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
    $('section-form').addEventListener('submit',async event=>{event.preventDefault();if(!pending||busy)return;const name=$('section-name').value.trim();if(!name){$('section-name').focus();return;}let groups=copy(pending.baseline.sections);if(pending.id)groups.find(group=>group.id===pending.id).title=name;else groups=split(groups,pending.baseline.slides.map(slide=>slide.id),pending.slideId,'section-'+crypto.randomUUID(),name,'section-'+crypto.randomUUID());if(await write(groups,pending.baseline)){const id=pending.id||groups.find(group=>group.slide_ids[0]===pending.slideId)?.id;dialog.close();focusSection(id);}});
    return {render,sync,isBusy:()=>busy,reveal:id=>{const state=read(),group=state?.sections?.find(item=>item.slide_ids.includes(id));if(group)toggle(group.id,false);},capture:deckId=>[...collapsed(deckId)].filter(id=>read()?.sections?.some(group=>group.id===id)),restore:(deckId,values)=>{folded.set(deckId,new Set(values||[]));persist(deckId);applyFold();},drop:({x,y,selected,ids})=>{const element=doc.elementFromPoint(x,y)?.closest('.slide-section');if(!element)return null;const group=read()?.sections.find(item=>item.id===element.dataset.sectionId);if(!group)return null;if(doc.elementFromPoint(x,y)?.closest('.section-header')){const remaining=ids.filter(id=>!selected.includes(id)),following=read().sections.slice(read().sections.indexOf(group)+1).flatMap(item=>item.slide_ids).find(id=>remaining.includes(id));return {sectionId:group.id,position:following?remaining.indexOf(following):remaining.length};}return {sectionId:group.id};}};
  }
  const api={create,split,remove,moveMembers};if(typeof module!=='undefined')module.exports=api;else root.SlideSections=api;
})(typeof window==='undefined'?{}:window);
