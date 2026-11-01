/* Answerable questions remain in the slide conversation, including their local drafts. */
((root)=>{
  'use strict';
  const storageKey='slide-studio:question-drafts:v1';
  function answerFor(question,events){return events.find(item=>item.author==='user'&&item.reply_to===question.event_id&&item.slide_id===question.slide_id);}
  function status(question,events){const answer=answerFor(question,events);return answer?(answer.released?'answered':'queued'):question.question?.cancelled?'cancelled':'open';}
  function pending(events){return events.filter(item=>item.author==='agent'&&item.question&&status(item,events)==='open');}
  function create({api,escape,svg,avatar,onRefresh,deliveryMode=()=>'live'}){
    const drafts=new Map(),busy=new Set(),errors=new Map(),handlers=new Map();let storage;
    try{storage=root.localStorage;const saved=JSON.parse(storage.getItem(storageKey)||'{}');for(const [key,value] of Object.entries(saved)){if(value&&typeof value.text==='string'&&value.text.length<=10000&&(value.choice===null||(Number.isInteger(value.choice)&&value.choice>=0&&value.choice<6)))drafts.set(key,{text:value.text,choice:value.choice,request_id:typeof value.request_id==='string'&&/^[a-zA-Z0-9-]{8,100}$/.test(value.request_id)?value.request_id:crypto.randomUUID()});}}catch{}
    const key=(deckId,slideId,id)=>`${deckId}/${slideId}/${id}`;
    function persist(){try{storage?.setItem(storageKey,JSON.stringify(Object.fromEntries(drafts)));}catch{}}
    function draft(id){return drafts.get(id)||{text:'',choice:null,request_id:crypto.randomUUID()};}
    function write(id,value){drafts.set(id,value);errors.delete(id);persist();}
    function render(question,events,deckId,slideId,contentHash){
      const id=key(deckId,slideId,question.event_id),state=status(question,events),open=state==='open';
      let value=draft(id);const answer=answerFor(question,events),pending=value.choice===null?value.text.trim():question.question.options[value.choice];if(answer&&pending&&answer.message===pending){drafts.delete(id);persist();value=draft(id);}
      const batch=deliveryMode()==='batch',hasDraft=!!value.text||value.choice!==null,description=({open:'Awaiting your answer',queued:'Answer queued — not sent to the agent',answered:'Answered',cancelled:'Cancelled'})[state];
      const stateIcon=state==='open'?'question':state==='queued'?'clock':state==='answered'?'check':'close';
      const choices=(question.question.options||[]).map((option,index)=>`<label class="question-option"><input type="radio" name="question-${question.event_id}" value="${index}" ${value.choice===index?'checked':''}><span class="question-choice-mark" aria-hidden="true">${svg('check')}</span><span>${escape(option)}</span></label>`).join('');
      const input=open||hasDraft?`<div class="question-custom">${open&&question.question.options.length?`<input type="radio" name="question-${question.event_id}" value="custom" aria-label="Write your own answer" ${value.choice===null?'checked':''}>`:''}<textarea class="question-answer" maxlength="10000" rows="1" aria-label="${open?'Write your own answer':'Unsent answer'}" placeholder="${question.question.options.length?'Other…':'Your answer…'}" ${open?'':'readonly'}>${escape(open?value.text:value.text||(question.question.options[value.choice]||''))}</textarea></div>`:'';
      return `<article class="comment agent question-card" data-question-id="${question.event_id}" data-question-state="${state}">
        <form class="question-form"><fieldset aria-labelledby="question-prompt-${question.event_id}"><legend class="sr-only">Answer the agent’s question</legend><div class="question-heading">${avatar('agent')}<div class="question-prompt chat-markdown" id="question-prompt-${question.event_id}" tabindex="-1">${root.SlideChatMarkdown.render(question.message)}</div><span class="question-state" role="img" aria-label="${description}" title="${description}">${svg(stateIcon)}${state==='queued'||state==='cancelled'?`<span aria-hidden="true">${state==='queued'?'Queued':'Cancelled'}</span>`:''}</span></div>${open&&question.content_hash!==contentHash?'<span class="old-version question-version">Earlier version</span>':''}
          ${open?`<div class="question-options">${choices}</div>`:''}<div class="question-response" ${!open&&!hasDraft&&!errors.get(id)?'hidden':''}>${input}
          <div class="question-actions" ${!open&&!hasDraft&&!errors.get(id)?'hidden':''}>
            ${open||hasDraft?`<button type="reset" class="question-clear" aria-label="Discard unsent answer" title="Discard unsent answer" ${hasDraft?'':'hidden'}>${svg('close')}</button>`:''}
            ${open?`<button class="question-send" type="submit" aria-label="${batch?'Add answer to batch':'Send answer'}" title="${batch?'Add answer to batch':'Send answer'}" data-delivery-mode="${batch?'batch':'live'}">${svg(batch?'batch':'send')}</button>`:''}
          </div><span class="question-error" role="alert">${escape(errors.get(id)||'')}</span></div></fieldset></form></article>`;
    }
    function updateDelivery(list){const mode=deliveryMode();for(const button of list.querySelectorAll('.question-send')){if(button.dataset.deliveryMode===mode)continue;const batch=mode==='batch',label=batch?'Add answer to batch':'Send answer';button.dataset.deliveryMode=mode;button.setAttribute('aria-label',label);button.title=label;button.innerHTML=svg(batch?'batch':'send');}}
    function mount(list,events,deckId,slideId){
      handlers.clear();
      for(const card of list.querySelectorAll('[data-question-id]')){
        const question=events.find(item=>item.event_id===Number(card.dataset.questionId));if(!question)continue;
        const id=key(deckId,slideId,question.event_id),form=card.querySelector('form'),input=card.querySelector('textarea'),send=card.querySelector('.question-send');
        function sync(){const current=draft(id);if(send)send.disabled=busy.has(id)||(current.choice===null&&!current.text.trim());for(const element of card.querySelectorAll('input,textarea,button'))element.disabled=busy.has(id);if(send&&!busy.has(id))send.disabled=current.choice===null&&!current.text.trim();card.setAttribute('aria-busy',String(busy.has(id)));const clear=card.querySelector('.question-clear');if(clear)clear.hidden=!current.text&&current.choice===null;const error=card.querySelector('.question-error');if(error)error.textContent=errors.get(id)||'';}
        function clear(){drafts.delete(id);errors.delete(id);persist();if(input)input.value='';for(const radio of card.querySelectorAll('input'))radio.checked=radio.value==='custom';card.querySelector('.question-error').textContent='';if(status(question,events)!=='open'){card.querySelector('.question-custom')?.remove();card.querySelector('.question-actions').hidden=true;card.querySelector('.question-response').hidden=true;card.querySelector('.question-prompt').focus({preventScroll:true});}else input?.focus({preventScroll:true});sync();}
        input?.addEventListener('input',()=>{write(id,{text:input.value,choice:null,request_id:crypto.randomUUID()});for(const radio of card.querySelectorAll('input'))radio.checked=radio.value==='custom';sync();});
        input?.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.altKey&&!event.isComposing&&event.keyCode!==229&&send){event.preventDefault();if(!event.repeat&&!send.disabled)form.requestSubmit();}});
        input?.addEventListener('pointerdown',()=>{if(status(question,events)!=='open')return;const current=draft(id);if(current.choice!==null)write(id,{...current,choice:null,request_id:crypto.randomUUID()});for(const radio of card.querySelectorAll('input'))radio.checked=radio.value==='custom';sync();});
        for(const radio of card.querySelectorAll('input'))radio.addEventListener('change',()=>{const current=draft(id);write(id,{...current,choice:radio.value==='custom'?null:Number(radio.value),request_id:crypto.randomUUID()});sync();if(radio.value==='custom')input?.focus({preventScroll:true});});
        form.addEventListener('reset',event=>{event.preventDefault();clear();});
        form.addEventListener('submit',async event=>{
          event.preventDefault();if(busy.has(id)||status(question,events)!=='open')return;
          const current=draft(id),answer=current.choice===null?current.text.trim():question.question.options[current.choice];
          if(!answer)return;write(id,current);busy.add(id);sync();
          try{const answered=await api(`/api/decks/${encodeURIComponent(deckId)}/slides/${encodeURIComponent(slideId)}/questions/${question.event_id}/answer`,{answer,request_id:current.request_id});drafts.delete(id);errors.delete(id);persist();await onRefresh(answered,deckId,slideId);}
          catch(error){errors.set(id,error.message);card.querySelector('.question-error').textContent=error.message;if(error.status===409)await onRefresh();}
          finally{busy.delete(id);handlers.get(id)?.();}
        });
        handlers.set(id,sync);sync();
      }
    }
    function captureFocus(list){const active=list.ownerDocument.activeElement,card=active?.closest('[data-question-id]');if(!card)return null;return {id:card.dataset.questionId,tag:active.tagName,choice:active.value,start:active.selectionStart,end:active.selectionEnd};}
    function restoreFocus(list,saved){if(!saved)return;const card=list.querySelector(`[data-question-id="${saved.id}"]`);if(!card)return;const target=saved.tag==='TEXTAREA'?card.querySelector('textarea'):saved.tag==='INPUT'?card.querySelector(`input[value="${saved.choice}"]`):null;(target||card.querySelector('.question-prompt'))?.focus({preventScroll:true});if(target?.tagName==='TEXTAREA'&&saved.start!==null)target.setSelectionRange(saved.start,saved.end);}
    function jump(list){const card=list.querySelector('[data-question-state="open"]');if(card){card.scrollIntoView({block:'nearest'});card.querySelector('.question-prompt').focus({preventScroll:true});}}
    function exportDrafts(deckId){return Object.fromEntries([...drafts].filter(([name,value])=>name.startsWith(deckId+'/')&&(value.text||value.choice!==null)).map(([name,value])=>[name.split('/')[2],{slide_id:name.split('/')[1],text:value.text,choice:value.choice}]));}
    function importDrafts(deckId,values){for(const [id,value] of Object.entries(values||{}))drafts.set(key(deckId,value.slide_id,id),{text:value.text,choice:value.choice,request_id:crypto.randomUUID()});persist();}
    return {render,mount,captureFocus,restoreFocus,jump,updateDelivery,exportDrafts,importDrafts,hasDrafts:()=>[...drafts.values()].some(value=>!!value.text||value.choice!==null)};
  }
  const api={create,pending,status};if(typeof module!=='undefined')module.exports=api;else root.SlideQuestions=api;
})(typeof window==='undefined'?{}:window);
