/* Parallel slide designs: immutable previews, explicit decisions, quiet retained choices. */
((root)=>{
  'use strict';
  const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  function create({api,getContext,activate,beforePreview=async()=>true,canDecide=()=>true,beforeDecide=async()=>true,afterDecision=async()=>{},onPreview=()=>{},onBusy=()=>{},notify=()=>{}}){
    const doc=root.document,icon=name=>root.SlideIcons.svg(name),canvas=doc.querySelector('.canvas-area'),wrap=doc.getElementById('preview-wrap');
    const toggle=doc.createElement('button');toggle.type='button';toggle.id='slide-design-toggle';toggle.className='sd-toggle';toggle.hidden=true;toggle.setAttribute('aria-controls','slide-design-tray');
    doc.querySelector('.preview-controls').prepend(toggle);
    const tray=doc.createElement('section');tray.id='slide-design-tray';tray.className='sd-tray';tray.hidden=true;tray.setAttribute('aria-label','Slide design options');
    tray.innerHTML=`<div class="sd-decision"><span class="sd-current-label"></span><div><button type="button" data-sd-keep>Keep current</button><button type="button" class="primary" data-sd-use>Use this design ${icon('right')}</button></div></div><p class="sd-conflict" role="status" hidden></p><header><h2>Design options <span class="sd-count"></span></h2><button type="button" data-sd-latest hidden>New designs</button><button type="button" data-sd-compare>${icon('batch')}Compare</button></header><div class="sd-options" role="tablist" aria-label="Slide design options"></div><p class="sd-error" role="alert"></p>`;
    wrap.after(tray);
    const overlay=doc.createElement('div');overlay.className='sd-canvas-overlay';overlay.hidden=true;wrap.append(overlay);
    const peek=doc.createElement('section');peek.className='sd-peek';peek.id='slide-design-peek';peek.setAttribute('popover','manual');peek.setAttribute('aria-label','Alternative slide designs');doc.body.append(peek);
    const comparison=doc.createElement('dialog');comparison.className='sd-compare';comparison.setAttribute('aria-labelledby','sd-compare-title');
    comparison.innerHTML=`<header role="presentation"><h2 id="sd-compare-title">Compare designs</h2><button type="button" data-sd-close aria-label="Close design comparison">${icon('close')}</button></header><div class="sd-compare-grid"><section><header><span class="sd-reference-state"></span><label class="sr-only" for="sd-reference">Left design</label><select id="sd-reference"></select></header><div class="sd-compare-left sd-frame-slot"></div></section><section><header><span class="sd-candidate-state">Preview</span><label class="sr-only" for="sd-candidate">Right design</label><select id="sd-candidate"></select></header><div class="sd-compare-right sd-frame-slot"></div></section></div><footer role="presentation"><button type="button" data-sd-keep>Keep current</button><div><button type="button" data-sd-use-reference></button><button type="button" class="primary" data-sd-use-candidate></button></div></footer><p class="sd-compare-error" role="alert"></p>`;
    doc.body.append(comparison);
    const $=selector=>tray.querySelector(selector),cmp=selector=>comparison.querySelector(selector);
    let context=null,page=null,group=null,selected='current',reference='current',trayOpen=false,busy=false,epoch=0,loadKey='',expectedHash='',reviewed=false,peekTrigger=null,closeTimer=null,historyToken=0;
    let scope='',request=null,compareOpener=null,suppressPeek=false,expectedDecision=null,interactionEpoch=0;
    const cache=new Map(),frames=new Set(),pendingPreviews=new Map();
    const resize=new ResizeObserver(entries=>{for(const entry of entries){const frame=entry.target.querySelector('iframe');if(!frame)continue;const factor=entry.target.clientWidth/1600;frame.style.width='1600px';frame.style.height='900px';frame.style.transform=`scale(${factor})`;}});
    function clearFrame(slot){for(const frame of slot.querySelectorAll('iframe')){pendingPreviews.get(frame)?.controller.abort();pendingPreviews.delete(frame);frames.delete(frame);resize.unobserve(frame.parentElement);}slot.replaceChildren();}
    function clearOptions(){for(const slot of $('.sd-options').querySelectorAll('.sd-frame-slot'))clearFrame(slot);delete $('.sd-options').dataset.signature;}
    function frame(slot,choice,{staticOnly=false}={}){
      const url=choice.url+(staticOnly?(choice.url.includes('?')?'&':'?')+'static=true':'');
      if(slot.dataset.designUrl===url&&slot.querySelector('iframe'))return;
      clearFrame(slot);slot.dataset.designUrl=url;
      const iframe=doc.createElement('iframe');iframe.title=`${choice.name} design preview`;iframe.setAttribute('sandbox','allow-scripts');iframe.referrerPolicy='no-referrer';iframe.loading='lazy';iframe.tabIndex=staticOnly?-1:0;
      iframe.addEventListener('load',()=>{if(iframe.dataset.documentUrl===url)iframe.dataset.ready='true';});
      if(url.includes('/current-preview?')){
        const controller=new root.AbortController();
        const pending=root.fetch(root.ArtifactSession?.path?.(url) ?? url,{signal:controller.signal}).then(async response=>{if(!response.ok)throw Error('The current slide changed. Refresh the comparison.');const html=await response.text();if(!iframe.isConnected||slot.dataset.designUrl!==url)return;iframe.dataset.documentUrl=url;iframe.srcdoc=html;}).catch(error=>{if(controller.signal.aborted||!iframe.isConnected||slot.dataset.designUrl!==url)return;iframe.dataset.failed='true';notify(error.message,true);});
        pendingPreviews.set(iframe,{controller,promise:pending});void pending.finally(()=>pendingPreviews.delete(iframe));
      }else{iframe.dataset.documentUrl=url;iframe.src=root.ArtifactSession?.path?.(url) ?? url;}
      slot.append(iframe);frames.add(iframe);resize.observe(slot);
      const factor=slot.clientWidth/1600;iframe.style.cssText=`width:1600px;height:900px;transform:scale(${factor});`;
    }
    /** Finish guarded snapshot reads before replacing their source. */
    async function settlePreviews(){while(pendingPreviews.size)await Promise.all([...pendingPreviews.values()].map(item=>item.promise));}
    function base(value=context){return `/api/decks/${encodeURIComponent(value.deckId)}/slides/${encodeURIComponent(value.slideId)}/designs`;}
    function choices(){
      if(!group||!context)return [];
      const original=group.options.find(option=>option.id===group.current_option_id);
      return [{key:'current',id:null,name:original?.name||'Current',isCurrent:true,source_hash:context.item.content_hash,url:original?.preview_url||`${base()}/current-preview?content_hash=${encodeURIComponent(page.content_hash)}`} ,...group.options.filter(option=>option.id!==group.current_option_id).map(option=>({...option,key:option.id,url:option.preview_url,isCurrent:false}))];
    }
    function choice(key){return choices().find(item=>item.key===key)||choices()[0];}
    function focusControl(){const target=!toggle.hidden?toggle:doc.getElementById('version-history');if(target?.getClientRects().length)target.focus({preventScroll:true});}
    function renderOverlay(){
      const item=choice(selected),preview=!!item&&!item.isCurrent;
      overlay.hidden=!preview;wrap.classList.toggle('sd-previewing',preview);canvas.classList.toggle('sd-preview-mode',preview);
      if(preview)frame(overlay,item);else clearFrame(overlay);
      onPreview(preview?{design_option_id:item.id,design_option_name:item.name,content_hash:item.source_hash}:null);
    }
    function renderOptions(){
      const list=$('.sd-options'),focused=list.contains(doc.activeElement)?doc.activeElement?.dataset.sdOption:null;
      const signature=JSON.stringify([group.id,selected,choices().map(item=>[item.key,item.url])]);
      if(list.dataset.signature!==signature){
        clearOptions();list.dataset.signature=signature;
        list.innerHTML=choices().map(item=>`<button type="button" role="tab" id="sd-option-${escape(item.key)}" aria-selected="${selected===item.key}" aria-controls="preview-wrap" tabindex="${selected===item.key?'0':'-1'}" data-sd-option="${escape(item.key)}" data-sd-chosen="${item.isCurrent}" aria-label="${escape(item.name)}${item.isCurrent?', chosen design':''}"><span class="sd-frame-slot" aria-hidden="true"></span><span class="sd-option-name"><strong>${escape(item.name)}</strong>${item.isCurrent?`<span class="sd-chosen-mark" aria-hidden="true">${icon('check')}</span>`:''}</span></button>`).join('');
        for(const button of list.querySelectorAll('[data-sd-option]'))frame(button.querySelector('.sd-frame-slot'),choice(button.dataset.sdOption),{staticOnly:true});
        if(focused)list.querySelector(`[data-sd-option="${CSS.escape(focused)}"]`)?.focus({preventScroll:true});
      }
    }
    function render(){
      if(!context||!group){clearOptions();toggle.hidden=true;tray.hidden=true;overlay.hidden=true;wrap.classList.remove('sd-previewing');canvas.classList.remove('sd-preview-mode');onPreview(null);return;}
      const item=choice(selected),current=item.isCurrent;
      const visible=trayOpen||!current,newer=!!page?.sets[0]?.pending&&page.sets[0].id!==group.id;
      toggle.hidden=!group.pending&&!visible&&!newer;toggle.innerHTML=icon('slides')+`<span>${newer&&!visible?'New designs':'Designs'}</span> <span class="sd-counter">${newer&&!visible?page.sets[0].options.length:choices().length}</span>`;toggle.setAttribute('aria-expanded',String(visible));toggle.setAttribute('aria-label',`${newer&&!visible?'New designs':'Designs'} ${choices().length}`);toggle.disabled=busy;
      tray.hidden=!visible;$('[data-sd-latest]').hidden=!newer;$('header h2 .sd-count').textContent=String(choices().length);
      $('.sd-current-label').innerHTML=`<span class="sd-preview-state ${current?'in-slide':''}" ${current?'role="img" aria-label="Chosen design"':''}>${current?icon('check'):'Preview'}</span><strong>${escape(item.name)}</strong>`;
      $('[data-sd-use]').hidden=current;$('[data-sd-keep]').textContent=group.pending?'Dismiss suggestions':'Back to current';
      $('.sd-decision').hidden=!canDecide()||!group.pending&&current;
      $('.sd-conflict').hidden=!group.stale||reviewed;$('.sd-conflict').textContent='The slide changed since these designs. Compare with the current slide before replacing it.';
      renderOptions();renderOverlay();mountCards();
      for(const button of tray.querySelectorAll('button'))button.disabled=busy;
      if(!current&&group.stale&&!reviewed)$('[data-sd-use]').disabled=true;
      if(comparison.open)renderComparison();
    }
    async function load({force=false,setId=null,intent=interactionEpoch}={}){
      if(!context)return;const token=++epoch,target={...context},at=scope;
      try{
        const result=await api(base(target));if(token!==epoch||at!==scope||intent!==null&&intent!==interactionEpoch)return;
        if(setId&&setId!=='latest'&&!result.sets.some(item=>item.id===setId)){const retained=await api(base(target)+'?set_id='+encodeURIComponent(setId));if(token!==epoch||at!==scope||intent!==null&&intent!==interactionEpoch)return;result.sets.push(...retained.sets);}
        page=result;cache.set(at,result);
        const previous=group?.id,requested=setId==='latest'?result.sets[0]?.id:(setId||previous);group=result.sets.find(item=>item.id===requested)||(previous===requested?group:null)||result.sets[0]||null;
        if(!group){render();return;}
        if(!previous||previous!==group.id){selected='current';trayOpen=group.pending;expectedHash=result.content_hash;expectedDecision=group.decision_id;reviewed=false;}
        if(!choice(selected)||selected!=='current'&&!group.options.some(item=>item.id===selected))selected='current';
        if(selected==='current'){expectedHash=result.content_hash;expectedDecision=group.decision_id;if(!group.pending&&!force)trayOpen=false;}
        render();
      }catch(error){if(token===epoch&&(intent===null||intent===interactionEpoch)){$('.sd-error').textContent=error.message;notify(error.message,true);}}
    }
    async function preview(key,{focus=false}={}){
      const at=scope,was=group?.id,intent=++interactionEpoch;if(busy||!group||!await beforePreview()||scope!==at||group?.id!==was||intent!==interactionEpoch)return;
      const item=choice(key);if(!item)return;selected=item.key;trayOpen=true;request=null;
      if(selected==='current')expectedHash=page.content_hash;
      render();if(focus)$('.sd-options').querySelector(`[data-sd-option="${CSS.escape(selected)}"]`)?.focus({preventScroll:true});
    }
    async function openOption({slideId=context?.slideId,setId=null,optionId=null}={}){
      const intent=++interactionEpoch;closePeek();if(!slideId||!await activate(slideId)||intent!==interactionEpoch||context?.slideId!==slideId)return;
      const at=scope;await load({force:true,setId:setId||'latest',intent});if(!group||intent!==interactionEpoch||scope!==at)return;
      trayOpen=true;expectedHash=page.content_hash;expectedDecision=group.decision_id;reviewed=false;
      const selectedOption=optionId&&group.options.find(item=>item.id===optionId);
      await preview(selectedOption?(selectedOption.id===group.current_option_id?'current':selectedOption.id):(choices().find(item=>!item.isCurrent)?.key||'current'),{focus:true});
      tray.scrollIntoView({block:'nearest'});
    }
    function closePeek({restoreFocus=false}={}){clearTimeout(closeTimer);if(peek.matches(':popover-open'))peek.hidePopover();peekTrigger?.setAttribute('aria-expanded','false');for(const slot of peek.querySelectorAll('.sd-frame-slot'))clearFrame(slot);if(restoreFocus&&peekTrigger?.isConnected){suppressPeek=true;peekTrigger.focus({preventScroll:true});suppressPeek=false;}}
    function positionPeek(point){const width=peek.offsetWidth,height=peek.offsetHeight;let top=point.y-height-16;if(top<12)top=point.y+18;peek.style.left=`${Math.max(16,Math.min(innerWidth-width-16,point.x-35))}px`;peek.style.top=`${Math.max(16,Math.min(innerHeight-height-16,top))}px`;}
    async function showPeek(trigger,point){
      clearTimeout(closeTimer);peekTrigger=trigger;const target=getContext();if(!target)return;
      const slideId=trigger.dataset.sdSlide,at=`${target.deckId}/${slideId}`;
      let saved=cache.get(at);
      try{if(!saved){saved=await api(base({...target,slideId}));cache.set(at,saved);}}catch(error){notify(error.message,true);return;}
      if(peekTrigger!==trigger||!trigger.isConnected||!trigger.matches(':hover,:focus-visible')||!saved.sets.length)return;
      const latest=saved.sets[0];for(const slot of peek.querySelectorAll('.sd-frame-slot'))clearFrame(slot);peek.innerHTML=`<div class="sd-fan" data-count="${Math.min(3,latest.options.filter(item=>item.id!==latest.current_option_id).length)}">${latest.options.filter(item=>item.id!==latest.current_option_id).slice(0,3).map(item=>`<span class="sd-fan-slot"><button type="button" data-sd-peek="${escape(item.id)}" data-sd-set="${escape(latest.id)}" data-sd-slide="${escape(slideId)}" aria-label="Preview ${escape(item.name)} design"><span class="sd-frame-slot" aria-hidden="true"></span><strong>${escape(item.name)}</strong></button></span>`).join('')}</div><button type="button" class="sd-peek-all" data-sd-show-all data-sd-slide="${escape(slideId)}" data-sd-set="${escape(latest.id)}">View all ${latest.options.length} designs ${icon('right')}</button>`;
      if(!peek.matches(':popover-open'))peek.showPopover();trigger.setAttribute('aria-expanded','true');
      for(const button of peek.querySelectorAll('[data-sd-peek]')){const option=latest.options.find(item=>item.id===button.dataset.sdPeek);frame(button.querySelector('.sd-frame-slot'),{...option,url:option.preview_url},{staticOnly:true});}
      const rect=trigger.getBoundingClientRect();positionPeek(point||{x:rect.left+rect.width/2,y:rect.top});
    }
    function schedulePeekClose(){clearTimeout(closeTimer);closeTimer=setTimeout(()=>{if(!peek.matches(':hover')&&!peek.contains(doc.activeElement)&&!peekTrigger?.matches(':hover,:focus'))closePeek();},260);}
    function badges(snapshot){
      for(const entry of doc.querySelectorAll('#slides .slide-entry')){
        const thumb=entry.querySelector('.slide-thumb'),id=thumb?.dataset.id,summary=snapshot.slide_designs?.[id];let button=entry.querySelector('[data-sd-badge]');
        entry.classList.toggle('has-slide-designs',!!summary);entry.classList.toggle('sd-decided',!!summary&&!summary.pending);
        if(!summary){button?.remove();continue;}
        if(!button){button=doc.createElement('button');button.type='button';button.className='sd-badge';button.dataset.sdBadge='';button.dataset.sdSlide=id;button.setAttribute('aria-controls',peek.id);button.setAttribute('aria-expanded','false');entry.append(button);}
        button.innerHTML=icon('copy')+`<span>${Math.max(1,summary.count-1)}</span>`;button.setAttribute('aria-label',`View ${Math.max(1,summary.count-1)} alternative designs for ${snapshot.slides.find(item=>item.id===id)?.title||'slide'}`);
      }
    }
    function mountCards(){
      if(!context)return;
      for(const element of doc.querySelectorAll('[data-slide-design-set]')){
        const saved=page?.sets.find(item=>item.id===element.dataset.slideDesignSet);if(!saved)continue;
        const signature=JSON.stringify([saved.id,saved.pending,saved.decision_id,saved.current_option_id]);if(element.dataset.signature===signature)continue;
        element.dataset.signature=signature;element.classList.toggle('is-resolved',!saved.pending);for(const slot of element.querySelectorAll('.sd-frame-slot'))clearFrame(slot);
        if(!saved.pending){const named=saved.options.find(item=>item.id===saved.current_option_id)?.name||'Current design';element.innerHTML=icon('check')+`<span>${escape(named)} kept</span>`+icon('right');element.setAttribute('aria-label',`View saved designs, ${named} kept`);continue;}
        element.innerHTML=`<span class="sd-chat-previews">${saved.options.slice(1,4).map(()=>'<span class="sd-frame-slot" aria-hidden="true"></span>').join('')}</span><span>${saved.options.length-1} new designs</span>${icon('right')}`;element.setAttribute('aria-label',`Review ${saved.options.length-1} new designs`);
        [...element.querySelectorAll('.sd-frame-slot')].forEach((slot,index)=>frame(slot,{...saved.options[index+1],url:saved.options[index+1].preview_url},{staticOnly:true}));
      }
    }
    function renderComparison(){
      const options=choices();cmp('#sd-reference').innerHTML=options.map(item=>`<option value="${escape(item.key)}" ${item.key===reference?'selected':''} ${item.key===selected?'disabled':''}>${escape(item.name)}</option>`).join('');
      cmp('#sd-candidate').innerHTML=options.filter(item=>item.key!==reference).map(item=>`<option value="${escape(item.key)}" ${item.key===selected?'selected':''}>${escape(item.name)}</option>`).join('');
      cmp('.sd-reference-state').innerHTML=choice(reference).isCurrent?`<span role="img" aria-label="Chosen design">${icon('check')}</span>`:'Reference';cmp('.sd-candidate-state').innerHTML=choice(selected).isCurrent?`<span role="img" aria-label="Chosen design">${icon('check')}</span>`:'Preview';cmp('.sd-compare-left').classList.toggle('sd-chosen-frame',choice(reference).isCurrent);cmp('.sd-compare-right').classList.toggle('sd-chosen-frame',choice(selected).isCurrent);
      frame(cmp('.sd-compare-left'),choice(reference));frame(cmp('.sd-compare-right'),choice(selected));
      cmp('[data-sd-use-reference]').textContent=`${reference==='current'?'Keep':'Use'} ${choice(reference).name}`;cmp('[data-sd-use-candidate]').textContent=`${selected==='current'?'Keep':'Use'} ${choice(selected).name}`;
      cmp('footer').hidden=!canDecide();cmp('[data-sd-keep]').hidden=reference==='current'||selected==='current';
      for(const button of comparison.querySelectorAll('button'))button.disabled=busy;
    }
    async function openComparison(){
      const at=scope,was=group?.id,intent=++interactionEpoch;if(!group||busy||!await beforePreview()||scope!==at||group?.id!==was||intent!==interactionEpoch)return;
      const latest=await api(base());if(scope!==at||group?.id!==was)return;
      if(intent!==interactionEpoch)return;
      page=latest;group=latest.sets.find(item=>item.id===was)||group;expectedHash=latest.content_hash;expectedDecision=group.decision_id;reference='current';reviewed=false;
      if(selected==='current')selected=choices().find(item=>!item.isCurrent)?.key||'current';
      compareOpener=doc.activeElement;render();renderComparison();comparison.showModal();cmp('[data-sd-close]').focus();
      confirmCurrentLoaded();
    }
    function confirmCurrentLoaded(){
      const currentFrame=cmp('.sd-compare-left iframe');
      const mark=()=>{if(comparison.open&&reference==='current'&&currentFrame?.dataset.ready==='true'&&!currentFrame.dataset.failed){reviewed=true;render();}};
      if(currentFrame?.dataset.ready==='true')mark();else currentFrame?.addEventListener('load',mark,{once:true});
    }
    async function decide(key){
      if(busy||!group||!canDecide())return;
      const target={...context},at=scope,was=group.id,option=choice(key),baseline=expectedHash,decision=expectedDecision,acknowledged=reviewed;
      busy=true;onBusy(true);$('.sd-error').textContent='';cmp('.sd-compare-error').textContent='';render();
      try{
        if(!await beforeDecide())return;
        await settlePreviews();
        if(scope!==at||group?.id!==was)throw Error('The selected slide changed before the design decision.');
        if(!request)request={path:base(target)+'/decide',body:{set_id:was,option_id:option.isCurrent?null:option.id,expected_content_hash:baseline,expected_decision_id:decision,reviewed_changed_slide:acknowledged,request_id:root.crypto.randomUUID()}};
        const receipt=await api(request.path,request.body);request=null;if(at!==scope)return;
        trayOpen=false;selected='current';reviewed=false;if(comparison.open)comparison.close();closePeek();
        await afterDecision(receipt,target);await load({force:true});trayOpen=false;render();
        notify(receipt.action==='keep'?'Current design kept. Alternatives saved.':`${option.name} chosen. Alternatives saved.`,false,{label:'Undo',run:async()=>{
          if(busy)return;busy=true;onBusy(true);
          try{await settlePreviews();const undo=await api(base(target)+'/undo',{decision_id:receipt.id,expected_content_hash:receipt.content_hash,request_id:root.crypto.randomUUID()});
            if(scope!==at)return;await afterDecision(undo,target);await load({force:true});trayOpen=!!group?.pending;render();focusControl();
          }finally{busy=false;onBusy(false);render();}
        }});focusControl();
      }catch(error){if(error.status)request=null;if(at===scope){$('.sd-error').textContent=error.message;cmp('.sd-compare-error').textContent=error.message;notify(error.message,true);}}
      finally{busy=false;onBusy(false);if(at===scope)render();}
    }
    async function history(dialog,target){
      dialog.querySelector('.sd-history')?.remove();if(target.kind!=='slide')return;
      const token=++historyToken,path=base({deckId:target.deckId,slideId:target.slideId});
      try{
        const result=await api(path);if(token!==historyToken||!dialog.open||!result.sets.length)return;
        cache.set(`${target.deckId}/${target.slideId}`,result);
        const details=doc.createElement('details');details.className='sd-history';
        dialog.querySelector('.version-layout').before(details);
        let moreBusy=false;
        const paintFrames=()=>{for(const button of details.querySelectorAll('[data-sd-saved]')){const slot=button.querySelector('.sd-frame-slot');if(!button.checkVisibility()){clearFrame(slot);continue;}const saved=result.sets.find(item=>item.id===button.dataset.sdSet),option=saved.options.find(item=>item.id===button.dataset.sdSaved);frame(slot,{...option,url:option.preview_url},{staticOnly:true});}};
        const draw=()=>{
          const expanded=new Set([...details.querySelectorAll('.sd-saved-group[open]')].map(item=>item.dataset.setId));
          for(const slot of details.querySelectorAll('.sd-frame-slot'))clearFrame(slot);
          details.innerHTML=`<summary>${icon('copy')}Design decisions & saved alternatives</summary><div class="sd-history-decisions">${result.decisions.map(item=>`<p>${icon(item.action==='undo'?'restore':'check')}<span>${escape(item.action==='keep'?'Kept current design':item.action==='undo'?'Design choice undone':item.name+' chosen')}</span><time>${escape(new Date(item.created_at).toLocaleString())}</time></p>`).join('')}</div>${result.sets.map((saved,index)=>`<details class="sd-saved-group" data-set-id="${escape(saved.id)}" ${expanded.has(saved.id)||index===0?'open':''}><summary>${saved.pending?'Unreviewed designs':'Saved designs'} · ${escape(new Date(saved.created_at).toLocaleString())}</summary><div class="sd-saved-options">${saved.options.map(item=>`<button type="button" data-sd-saved="${escape(item.id)}" data-sd-set="${escape(saved.id)}" data-sd-chosen="${item.id===saved.current_option_id}" aria-label="Preview saved ${escape(item.name)} design${item.id===saved.current_option_id?', chosen design':''}"><span class="sd-frame-slot" aria-hidden="true"></span><strong>${escape(item.name)}</strong>${item.id===saved.current_option_id?`<span class="sd-chosen-mark" aria-hidden="true">${icon('check')}</span>`:''}</button>`).join('')}</div></details>`).join('')}${result.next_cursor!==null||result.next_decision_cursor!==null?'<button type="button" class="sd-history-earlier" data-sd-earlier>Load earlier design history</button>':''}`;
          paintFrames();
        };
        draw();details.addEventListener('toggle',paintFrames,{capture:true});
        details.addEventListener('click',async event=>{
          const button=event.target.closest('[data-sd-saved]');
          if(button){if(getContext()?.deckId!==target.deckId){notify('Open saved designs from their presentation.',true);return;}dialog.close();void openOption({slideId:target.slideId,setId:button.dataset.sdSet,optionId:button.dataset.sdSaved});return;}
          if(!event.target.closest('[data-sd-earlier]')||moreBusy)return;
          moreBusy=true;event.target.closest('[data-sd-earlier]').disabled=true;
          try{const previous=await api(path+'?'+new URLSearchParams({cursor:String(result.next_cursor??0),decision_cursor:String(result.next_decision_cursor??0)}));if(token!==historyToken||!dialog.open)return;
            for(const kind of ['sets','decisions']){const seen=new Set(result[kind].map(item=>item.id));result[kind].push(...previous[kind].filter(item=>!seen.has(item.id)));}
            result.next_cursor=previous.next_cursor;result.next_decision_cursor=previous.next_decision_cursor;draw();
            (details.querySelector('[data-sd-earlier]')||details.querySelector('.sd-saved-group:last-of-type>summary')).focus({preventScroll:true});
          }catch(error){notify(error.message,true);}finally{moreBusy=false;const retry=details.querySelector('[data-sd-earlier]');if(retry)retry.disabled=false;}
        });
        dialog.addEventListener('close',()=>{for(const slot of details.querySelectorAll('.sd-frame-slot'))clearFrame(slot);},{once:true});
      }catch(error){notify(error.message,true);}
    }
    function sync(snapshot,slideId){
      badges(snapshot);const item=snapshot.slides.find(value=>value.id===slideId);if(!item)return;
      const nextScope=`${snapshot.id}/${slideId}`,summary=snapshot.slide_designs?.[slideId],signature=JSON.stringify([nextScope,summary,item.content_hash]);
      if(scope!==nextScope){epoch++;closePeek();if(comparison.open)comparison.close();clearFrame(overlay);clearOptions();scope=nextScope;page=null;group=null;selected='current';trayOpen=false;loadKey='';request=null;reviewed=false;}
      context={deckId:snapshot.id,slideId,item};
      if(!summary){group=null;render();return;}
      if(signature!==loadKey){loadKey=signature;void load();}else render();
    }
    toggle.addEventListener('click',()=>{if(page?.sets[0]?.pending&&page.sets[0].id!==group?.id){void openOption();return;}trayOpen=!trayOpen;if(!trayOpen&&selected==='current')render();else render();focusControl();});
    tray.addEventListener('click',event=>{const option=event.target.closest('[data-sd-option]');if(option){void preview(option.dataset.sdOption,{focus:true});return;}if(event.target.closest('[data-sd-latest]'))void openOption();else if(event.target.closest('[data-sd-compare]'))void openComparison();else if(event.target.closest('[data-sd-use]'))void decide(selected);else if(event.target.closest('[data-sd-keep]')){if(group.pending)void decide('current');else{selected='current';trayOpen=false;render();focusControl();}}});
    $('.sd-options').addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const options=choices(),index=options.findIndex(item=>item.key===selected),next=event.key==='Home'?0:event.key==='End'?options.length-1:(index+(event.key==='ArrowRight'?1:-1)+options.length)%options.length;void preview(options[next].key,{focus:true});});
    comparison.addEventListener('click',event=>{if(event.target.closest('[data-sd-close]')){if(!busy)comparison.close();}else if(event.target.closest('[data-sd-keep]'))void decide('current');else if(event.target.closest('[data-sd-use-reference]'))void decide(reference);else if(event.target.closest('[data-sd-use-candidate]'))void decide(selected);});
    cmp('#sd-reference').addEventListener('change',event=>{reference=event.target.value;reviewed=false;renderComparison();confirmCurrentLoaded();});
    cmp('#sd-candidate').addEventListener('change',event=>{selected=event.target.value;renderComparison();});
    comparison.addEventListener('cancel',event=>{if(busy)event.preventDefault();});comparison.addEventListener('close',()=>{for(const slot of comparison.querySelectorAll('.sd-frame-slot'))clearFrame(slot);if(compareOpener?.isConnected&&compareOpener.getClientRects().length)compareOpener.focus({preventScroll:true});else focusControl();});
    doc.addEventListener('click',event=>{const badge=event.target.closest('[data-sd-badge]'),card=event.target.closest('[data-slide-design-set]');if(badge){event.preventDefault();event.stopPropagation();void openOption({slideId:badge.dataset.sdSlide});}else if(card){event.preventDefault();void openOption({setId:card.dataset.slideDesignSet});}});
    doc.addEventListener('pointerover',event=>{const trigger=event.target.closest('[data-sd-badge]');if(trigger&&event.pointerType!=='touch'&&!trigger.contains(event.relatedTarget))void showPeek(trigger,{x:event.clientX,y:event.clientY});});
    doc.addEventListener('pointerout',event=>{const trigger=event.target.closest('[data-sd-badge]');if(trigger&&!trigger.contains(event.relatedTarget))schedulePeekClose();});
    doc.addEventListener('focusin',event=>{const trigger=event.target.closest('[data-sd-badge]');if(trigger&&!suppressPeek&&trigger.matches(':focus-visible'))void showPeek(trigger);});
    doc.addEventListener('focusout',schedulePeekClose);
    peek.addEventListener('pointerenter',()=>clearTimeout(closeTimer));peek.addEventListener('pointerleave',schedulePeekClose);
    peek.addEventListener('click',event=>{const option=event.target.closest('[data-sd-peek]'),all=event.target.closest('[data-sd-show-all]');if(option||all){const target=option||all;void openOption({slideId:target.dataset.sdSlide,setId:target.dataset.sdSet,optionId:option?.dataset.sdPeek});}});
    doc.addEventListener('keydown',event=>{
      if(event.key==='Escape'&&peek.matches(':popover-open')){event.preventDefault();event.stopImmediatePropagation();closePeek({restoreFocus:peek.contains(doc.activeElement)||doc.activeElement===peekTrigger});return;}
      if(event.key==='ArrowDown'&&event.target.closest('[data-sd-badge]')){event.preventDefault();void showPeek(event.target.closest('[data-sd-badge]')).then(()=>peek.querySelector('[data-sd-peek]')?.focus());return;}
      if(event.key==='Escape'&&!comparison.open&&!doc.querySelector('dialog[open]')&&selected!=='current'){event.preventDefault();selected='current';trayOpen=!!group?.pending;render();focusControl();}
    },true);
    doc.addEventListener('pointerdown',event=>{if(!peek.contains(event.target)&&!event.target.closest('[data-sd-badge]'))closePeek();});
    doc.addEventListener('scroll',()=>closePeek(),true);root.addEventListener('resize',()=>closePeek());
    return {sync,history,mountCards,open:openOption,isPreviewing:()=>!!group&&selected!=='current',isBusy:()=>busy,
      dismiss(){if(busy)return false;interactionEpoch++;closePeek();selected='current';trayOpen=false;reviewed=false;request=null;if(comparison.open)comparison.close();render();return true;},
      feedbackContext:()=>{const item=choice(selected);return item&&!item.isCurrent?{design_option_id:item.id,design_option_name:item.name,content_hash:item.source_hash}:null;},
      reset(){epoch++;interactionEpoch++;scope='';context=null;group=null;page=null;selected='current';trayOpen=false;closePeek();if(comparison.open)comparison.close();clearFrame(overlay);render();},
      card(comment){return comment.design_set_id?`<button type="button" class="sd-chat-card" data-slide-design-set="${escape(comment.design_set_id)}">View design options ${icon('right')}</button>`:'';}};
  }
  root.SlideDesignOptions={create};
})(window);
