(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const draftStore=window.SlideDrafts.create({keys:()=>Object.keys(localStorage),getItem:key=>localStorage.getItem(key),setItem:(key,value)=>localStorage.setItem(key,value),removeItem:key=>localStorage.removeItem(key)});
  const drafts=draftStore.notes;
  const feedbackDrafts=draftStore.feedback;
  const feedbackDraft=()=>feedbackDrafts.get(key());
  const {svg,brand,button:iconButton}=window.SlideIcons;
  for(const [id,icon,label,show] of [
    ['instructions-button','rules','Instructions',true],['reload-instructions','refresh','Reload saved instructions',false],['close-instructions','close','Close',false],['save-instructions','save','Save',true],
    ['hide-slide','eye','Hide slide',false],['objects-button','group','Slide objects',false],
    ['delete-element','trash','Remove element',false],['attach-feedback','attach','Attach files',false],
    ['edit-element-text','edit','Edit text',false],['move-element','move','Move element',false],['align-element','align','Align to slide',false],['commit-element-text','check','Send text change',false],['cancel-element-edit','close','Cancel edit',false],
    ['retry-preview','refresh','Retry loading design',false],['copy-start','copy','Copy agent prompt',true],['copy-agent-prompt','copy','Copy agent prompt',true],
    ['attach-brief','attach','Attach brief files',false],['toggle-design-chat','plus','Add a design',false],['attach-design','attach','Attach reference files',false],['send-design','send','Send design request',false],
    ['new-button','plus','New presentation',true],['connect-button','plug','Connect an agent',true],['deck-design-references','attach','Design references',true],
    ['present-link','present','Present',true],['fullscreen-button','fullscreen','Present fullscreen',false],['prev-slide','left','Previous slide',false],['next-slide','right','Next slide',false],
    ['save-notes','save','Save',true],['clear-target','close','Clear element selection',false],
    ['select-parent','group','Select surrounding group',false],['load-update','refresh','Show update',true],
    ['close-connect','close','Close',false],['copy-url','copy','Copy server URL',false],['close-new','close','Close',false],
    ['copy-live-setup','copy','Copy setup prompt',true],['end-review','close','End review',true],
    ['create-button','plus','Create presentation',true],['mode-live','live','Live',true],['mode-batch','batch','Batch',true]
  ])iconButton($(id),icon,label,show);
  $('hover-hint').innerHTML=svg('cursor');$('hover-hint').title='Select an element to edit, move or comment';$('element-proposal-sent').innerHTML=svg('send');
  let deck=null, selectedId=null, pendingDeck=null, selectedElement=null, hoverElement=null, toastTimer;
  let visibilityBusy=false,menuSlide=null;
  let orderEpoch=0, selectionEpoch=0;
  let currentTarget=null, noteBaseline='', previewKey='', railSignature='', agentNotesKey='', commentsKey='', commentsSignature='', isLoading=false, feedbackSending=false, draftWarning=false, switchingDeck=false;
  let refreshQueued=false;
  let deckPicker=null, workspaceMenu=null, previewStatusTimer=null;
  const boundPreviewDocuments=new WeakSet();
  const key=()=>`${deck?.id}/${selectedId}`;
  const slide=()=>deck?.slides.find(item=>item.id===selectedId);
  const presentationLauncher=window.SlidePresentationLauncher.create({button:$('present-link'),additionalButtons:[$('fullscreen-button')],onReturn:id=>{if(deck?.slides.some(item=>item.id===id))void choose(id);},onUnavailable:()=>toast('Fullscreen unavailable in this browser. Open Slide Studio in Chrome or Firefox.',true)});
  const starting=()=>deck?.slides.length===1&&slide()?.id==='cover'&&slide()?.revision===1&&['Template draft. Replace with your presentation content.','Your brief is saved. Ready for the first draft.','Tell your agent who this is for and what it should say.'].includes(slide()?.agent.summary);
  const needsBrief=()=>starting()&&!deck.feedback?.length&&!slide()?.feedback?.length;
  const base=()=>`/api/decks/${encodeURIComponent(deck.id)}/slides/${encodeURIComponent(selectedId)}`;
  const elementEditor=window.SlideEdits.create({
    tools:$('element-tools'),selectionBox:$('selection-box'),deleteButton:$('delete-element'),rotationInput:$('rotate-element'),editButton:$('edit-element-text'),moveButton:$('move-element'),alignButton:$('align-element'),alignMenu:$('element-align-menu'),commitButton:$('commit-element-text'),cancelButton:$('cancel-element-edit'),sentBadge:$('element-proposal-sent'),
    anchor:()=>({deck_id:deck.id,slide_id:selectedId,content_hash:slide().content_hash,target:structuredClone(currentTarget)}),
    onSend:async({deck_id,slide_id,content_hash,target,message,request_id,edit_intent})=>{
      const result=await api(`/api/decks/${encodeURIComponent(deck_id)}/slides/${encodeURIComponent(slide_id)}/feedback`,{message,content_hash,target,request_id,edit_intent});
      const released=deck.workflow?.mode!=='batch';
      await refresh();toast(deck?.workflow?.mode==='batch'?'Added to batch.':deck?.workflow?.agent_waiting?'Sent to agent.':'Saved for agent.');
      return {deck_id,slide_id,event_id:Number(result.event_id),released};
    },
    deliveryStatus:submission=>{if(!submission)return 'released';const comments=deck?.id===submission.deck_id?deck.slides.find(item=>item.id===submission.slide_id)?.feedback||[]:[];if(comments.some(item=>item.author==='agent'&&item.reply_to===submission.event_id))return 'answered';const event=comments.find(item=>item.event_id===submission.event_id);return (event?.released??submission.released)?'released':'queued';},
    onChange:element=>{placeBox($('selection-box'),element);if(!element)return;const rect=element.getBoundingClientRect(),frame=$('preview').contentDocument.documentElement,tools=$('element-tools');tools.style.left=`${Math.max(6,Math.min(rect.left,frame.clientWidth-tools.offsetWidth-6))}px`;tools.style.top=`${rect.top-tools.offsetHeight-36>=6?rect.top-tools.offsetHeight-36:Math.min(frame.clientHeight-tools.offsetHeight-6,rect.bottom+10)}px`;},
    onError:message=>toast(message,true),blocked:()=>feedbackSending||switchingDeck||visibilityBusy||slideActions.isBusy()
  });
  for(const button of $('element-align-menu').querySelectorAll('[data-align]'))iconButton(button,`align${button.dataset.align[0].toUpperCase()+button.dataset.align.slice(1)}`,button.getAttribute('aria-label'));
  const animationPreview=window.SlideAnimationPreview.create({
    button:$('play-animations'),frame:$('preview'),wrap:$('preview-wrap'),
    documentFor:html=>previewDocument({...slide(),html},deck.id),
    interactiveContent:()=>slide()?.interactive_html||'',
    onState:playing=>{$('objects-button').disabled=playing||!$('preview').dataset.loadToken?.endsWith('-ready');$('element-tools').inert=playing;if(playing&&$('object-panel').matches(':popover-open'))$('object-panel').hidePopover();},
    onError:message=>toast(message,true)
  });

  const chatComposer=window.SlideChatComposer.create({
    form:$('feedback-form'),input:$('feedback-text'),attachButton:$('attach-feedback'),fileInput:$('feedback-file-input'),filesList:$('feedback-files'),error:$('feedback-error'),status:$('feedback-upload-status'),key,escape,svg,
    onChange:()=>{if(deck&&selectedId){retainDraft();const draft=feedbackDraft();if(draft)draft.request_id=crypto.randomUUID();draftStore.persist();}updateComposer();}
  });

  const slideQuestions=window.SlideQuestions.create({api,escape,svg,avatar:chatAvatar,deliveryMode:()=>deck?.workflow?.mode||'live',onRefresh:async(answer,deckId,slideId)=>{if(answer&&deck?.id===deckId){const target=deck.slides.find(item=>item.id===slideId);if(target&&!target.feedback.some(item=>item.event_id===answer.event_id))target.feedback.push(answer);renderRail();renderComments(slide());}await refresh();}});
  $('chat-question-status').addEventListener('click',()=>slideQuestions.jump($('feedback-list')));

  /** Report save and conflict results without interrupting the review flow. */
  function toast(message,error=false,action=null){clearTimeout(toastTimer);$('toast').textContent=message;if(action){const button=document.createElement('button');button.type='button';button.textContent=action.label;button.addEventListener('click',async()=>{button.disabled=true;try{await action.run();}catch(error){toast(error.message,true);}});$('toast').append(button);}$('toast').classList.toggle('error',error);$('toast').hidden=false;toastTimer=setTimeout(()=>$('toast').hidden=true,error||action?8000:3500);}
  /** Exchange JSON with the local server and surface actionable error messages. */
  async function api(url,body){const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const result=await response.json();if(!response.ok){const error=new Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail));error.status=response.status;throw error;}return result;}
  /** Retain unsent per-slide feedback and notes when switching between slides. */
  function retainDraft(){
    if(!deck||!selectedId)return;
    const dirty=$('notes').value!==noteBaseline;
    drafts.set(key(),{notes:dirty?$('notes').value:undefined,expected_notes:dirty?noteBaseline:undefined});
    const previous=feedbackDraft(),value=$('feedback-text').value;
    const hasContext=previous?.feedback?.trim()||previous?.target||(chatComposer.hasFiles()&&previous?.content_hash);
    const unchanged=previous?.feedback===value&&JSON.stringify(previous?.target)===JSON.stringify(currentTarget);
    feedbackDrafts.set(key(),{feedback:value,target:currentTarget,slide_id:previous?.slide_id||selectedId,context_slide_id:hasContext?previous.context_slide_id:selectedId,content_hash:hasContext?previous.content_hash:slide().content_hash,request_id:unchanged&&previous?.request_id?previous.request_id:crypto.randomUUID()});
    if(!draftStore.persist()&&!draftWarning){draftWarning=true;toast('Draft recovery is unavailable in this browser.',true);}
  }
  function updateComposer(){$('fullscreen-button').disabled=!deck;const mutating=visibilityBusy||slideActions.isBusy();elementEditor.sync();$('clear-target').disabled=feedbackSending||mutating;$('select-parent').disabled=feedbackSending||mutating;$('send-feedback').disabled=!deck||feedbackSending||mutating||(!$('feedback-text').value.trim()&&!chatComposer.hasFiles());chatComposer.sync({disabled:!deck||feedbackSending||mutating});for(const id of ['project-button','new-button'])$(id).disabled=mutating;presentationActions.update(feedbackSending||switchingDeck||mutating);deckPicker?.sync();sorter?.sync();}


  /** Resolve a source relative to the canonical presentation folder. */
  function sourceURL(url){return new URL(url,`${location.origin}/decks/${deck.id}/`).href;}
  /** Render approval and unread revisions independently, without reloading stable thumbnails. */
  function renderRail(snapshot=pendingDeck||deck){
    const approved=snapshot.slides.filter(item=>item.approved).length;
    const progress=$('progress-text'),label=`${approved} of ${snapshot.slides.length} slide${snapshot.slides.length===1?'':'s'} approved`;
    progress.innerHTML=svg('check')+`<span>${approved} / ${snapshot.slides.length}</span>`;progress.title=label;progress.setAttribute('aria-label',label);$('progress-bar').style.width=`${approved/snapshot.slides.length*100}%`;
    const signature=JSON.stringify([snapshot.id,snapshot.slides.map(item=>item.id)]);
    if(signature!==railSignature){
      railSignature=signature;
      $('slides').innerHTML=snapshot.slides.map((item,index)=>`<div class="slide-entry"><button class="slide-thumb" data-id="${escape(item.id)}"><div class="thumb-preview"><iframe title="Thumbnail ${index+1}" tabindex="-1" loading="lazy" sandbox="allow-same-origin"></iframe></div><span class="thumb-question" hidden aria-hidden="true"></span><div class="thumb-caption"><span class="thumb-number">${String(index+1).padStart(2,'0')}</span><span class="thumb-title">${escape(item.title)}</span><span class="dot ${item.approved?'green':'red'}" aria-hidden="true"></span></div></button><button class="slide-visibility" data-visibility-id="${escape(item.id)}" aria-label="Hide slide ${index+1}"></button></div>`).join('');
    }
    $('slides').querySelectorAll('iframe').forEach((frame,index)=>loadPreview(frame,snapshot.slides[index],snapshot.id,snapshot.design_hash));
    $('slides').querySelectorAll('[data-id]').forEach((button,index)=>{
      const item=snapshot.slides[index],pendingQuestions=window.SlideQuestions.pending(item.feedback||[]).length,label=`Slide ${index+1}: ${item.title}${pendingQuestions?` · ${pendingQuestions} question${pendingQuestions===1?'':'s'} awaiting your answer`:''} — ${item.approved?'Approved':'Needs review'}${item.hidden?' · Hidden from slideshow':''}${item.has_unseen_changes?' · Updated since last opened':''}`;
      const caption=button.querySelector('.thumb-title');if(caption.textContent!==item.title)caption.textContent=item.title;button.querySelector('.dot').className='dot '+(item.approved?'green':'red');
      button.setAttribute('aria-current',String(item.id===selectedId));button.setAttribute('aria-label',label);button.title=label;button.classList.toggle('has-unseen-changes',!!item.has_unseen_changes);
      button.classList.toggle('is-hidden',!!item.hidden);
      const questionBadge=button.querySelector('.thumb-question');questionBadge.hidden=!pendingQuestions;questionBadge.innerHTML=svg('question');button.classList.toggle('has-open-question',!!pendingQuestions);
      const visibility=button.parentElement.querySelector('[data-visibility-id]');iconButton(visibility,item.hidden?'eyeOff':'eye',`${item.hidden?'Unhide':'Hide'} slide ${index+1}`);visibility.disabled=visibilityBusy||slideActions.isBusy();
    });
    slideOrder.sync();slideActions.update();sorter.sync();
  }
  function renderChangeStatus(){document.querySelector('.preview-wrap').classList.toggle('has-unseen-changes',!!(pendingDeck||deck)?.slides.find(item=>item.id===selectedId)?.has_unseen_changes);}
  /** Acknowledge only a version explicitly opened, never a background refresh. */
  async function markOpened(){
    const item=slide(),deckId=deck?.id;if(!item?.has_unseen_changes)return;
    const {id,content_hash}=item;
    try{
      const result=await api(`/api/decks/${encodeURIComponent(deckId)}/slides/${encodeURIComponent(id)}/seen`,{content_hash});
      if(!result.seen||deck?.id!==deckId)return;
      for(const snapshot of [deck,pendingDeck]){const opened=snapshot?.slides.find(item=>item.id===id);if(opened?.content_hash===content_hash)opened.has_unseen_changes=false;}
      renderRail();renderChangeStatus();
    }catch(error){toast(error.message,true);}
  }
  const updates=window.SlideUpdates.create({refresh,workflow:refreshWorkflow,onError:error=>toast(error.message,true)});
  function watchDeck(id){updates.watch(id);}
  /** Refresh time-limited agent availability without reloading slides or discussion history. */
  async function refreshWorkflow(id){
    if(deck?.id!==id||switchingDeck||isLoading||presentationActions.isBusy())return;
    const snapshot=deck,baseline=deck.workflow,state=await api(`/api/decks/${encodeURIComponent(id)}/workflow`);
    if(deck!==snapshot||deck.workflow!==baseline)return;
    deck.workflow=state;if(pendingDeck?.id===id)pendingDeck.workflow=state;
    renderWorkflow();renderStart();
  }
  /** Reflect the selected element in the comment composer. */
  function renderTarget(){const targeted=!!currentTarget,context=feedbackDraft();const original=deck.slides.find(item=>item.id===context?.slide_id);const otherSlide=targeted&&context?.slide_id!==selectedId;$('target-label').textContent=targeted?`${otherSlide?'Another slide · ':''}${currentTarget.label}${original&&context.content_hash!==original.content_hash?' · earlier version':''}`:'';$('target-chip').classList.toggle('has-target',targeted);$('clear-target').hidden=!targeted;$('select-parent').hidden=!selectedElement?.parentElement||selectedElement.parentElement.matches('body,html,.slide');$('feedback-text').placeholder=targeted?'What should change here?':needsBrief()?'Audience, takeaway, key points or links…':'Message your agent…';}

  /** Place a highlight in slide-relative coordinates so it survives viewport resizing. */
  function placeBox(box,element){if(!element){box.hidden=true;return;}const rect=element.getBoundingClientRect(),frame=$('preview').contentDocument.documentElement;const width=frame.clientWidth,height=frame.clientHeight;box.hidden=false;box.style.left=`${rect.left/width*100}%`;box.style.top=`${rect.top/height*100}%`;box.style.width=`${rect.width/width*100}%`;box.style.height=`${rect.height/height*100}%`;}
  /** Use a stable element ID when present, with a structural selector as a fallback. */
  function selectorFor(element){if(element.id)return `#${CSS.escape(element.id)}`;if(element.dataset.elementId)return `[data-element-id="${CSS.escape(element.dataset.elementId)}"]`;const parts=[];let node=element;while(node&&node.tagName.toLowerCase()!=='body'){const tag=node.tagName.toLowerCase();const siblings=[...(node.parentElement?.children||[])].filter(sibling=>sibling.tagName===node.tagName);parts.unshift(`${tag}:nth-of-type(${siblings.indexOf(node)+1})`);node=node.parentElement;}return parts.join(' > ');}
  /** Give the highlighted item a short human-readable name. */
  function labelFor(element){const tag=element.tagName.toLowerCase();const types={h1:'Heading',h2:'Heading',h3:'Heading',p:'Text',span:'Text',img:'Image',svg:'Diagram',rect:'Box',circle:'Circle',path:'Shape',line:'Line',text:'Chart label',div:'Group',g:'Group'};const name=element.getAttribute('aria-label')||element.getAttribute('alt')||(element.innerText||element.textContent).trim().replace(/\s+/g,' ');return `${types[tag]||'Element'}${name?` · ${name.slice(0,95)}`:''}`.slice(0,200);}
  /** Remove editor-only focus attributes from the immutable source snapshot. */
  function elementSnapshot(element){const clone=element.cloneNode(true);for(const node of [clone,...clone.querySelectorAll('[data-review-focus]')]){if(node.hasAttribute('data-review-focus')){node.removeAttribute('data-review-focus');node.removeAttribute('tabindex');}}return clone.outerHTML.slice(0,15000);}
  /** Make major slide elements selectable without a pointing device. */
  function bindKeyboardSelection(doc,bindEvents=true){
    for(const element of doc.querySelectorAll('h1,h2,h3,h4,h5,h6,p,img,svg,[data-element-id]')){if(element.hasAttribute('tabindex')||element.closest('svg')!==null&&element.tagName.toLowerCase()!=='svg')continue;element.tabIndex=0;element.dataset.reviewFocus='';}
    if(!bindEvents)return;
    workspaceMenu?.bindDocument(doc);
    doc.addEventListener('keydown',async event=>{if(!['Enter',' '].includes(event.key)||event.defaultPrevented||event.ctrlKey||event.metaKey||event.altKey||elementEditor.isEditing()||elementEditor.isBusy()||feedbackSending)return;const element=event.target.closest('[data-review-focus]');if(!element)return;event.preventDefault();await selectElement(element,false);($('edit-element-text').disabled?$('move-element'):$('edit-element-text')).focus();});
  }
  /** Capture the element and original geometry, never just an unstable screen pixel. */
  async function selectElement(element,focus=true){if(elementEditor.isBusy()||!await elementEditor.finish())return false;if(element&&element===selectedElement&&element.isConnected){if(focus)$('feedback-text').focus();return true;}const previousElement=selectedElement;elementEditor.select(null);if(element&&!element.isConnected)element=previousElement?.isConnected?previousElement:null;selectedElement=element;hoverElement=null;$('hover-box').hidden=true;if(!element||element.matches('body,html,.slide')){currentTarget=null;selectedElement=null;$('selection-box').hidden=true;}else{const rect=element.getBoundingClientRect(),canvas=$('preview').contentDocument.querySelector('.slide').getBoundingClientRect();const clamp=value=>Math.max(0,Math.min(1,value));currentTarget={selector:selectorFor(element),label:labelFor(element),quote:(element.innerText||element.textContent).trim().slice(0,2000),html:elementSnapshot(element),x:clamp((rect.left-canvas.left)/canvas.width),y:clamp((rect.top-canvas.top)/canvas.height),width:clamp(rect.width/canvas.width),height:clamp(rect.height/canvas.height)};placeBox($('selection-box'),element);elementEditor.select(element);}
    const previous=feedbackDraft()||{};feedbackDrafts.set(key(),{...previous,target:currentTarget,slide_id:selectedId,context_slide_id:selectedId,content_hash:slide().content_hash});retainDraft();renderTarget();objectPicker.sync(selectedElement);if(focus)$('feedback-text').focus();return true;
  }
  /** Install immediate hover and click targeting inside the script-free slide preview. */
  function bindPreview(){const doc=$('preview').contentDocument;if(!doc)return;elementEditor.bind(doc);const fresh=!boundPreviewDocuments.has(doc);bindKeyboardSelection(doc,fresh);doc.documentElement.style.cursor='crosshair';if(fresh){boundPreviewDocuments.add(doc);doc.addEventListener('wheel',event=>sorter.zoomWheel(event),{passive:false});doc.addEventListener('pointermove',event=>{if(elementEditor.isEditing()||elementEditor.isBusy())return;let target=event.target;if(!(target instanceof doc.defaultView.Element)||target.matches('html,body,.slide')){hoverElement=null;$('hover-box').hidden=true;return;}if(target.closest('svg')?.classList.contains('icon'))target=target.closest('svg');if(target===selectedElement){$('hover-box').hidden=true;return;}if(hoverElement===target)return;hoverElement=target;placeBox($('hover-box'),target);$('hover-box').querySelector('span').textContent=labelFor(target);});doc.documentElement.addEventListener('pointerleave',()=>{$('hover-box').hidden=true;hoverElement=null;});doc.addEventListener('click',event=>{if(elementEditor.isEditing()||elementEditor.isBusy()||feedbackSending)return;event.preventDefault();event.stopPropagation();if(elementEditor.consumeClick())return;let target=event.target;if(target.closest('svg')?.classList.contains('icon'))target=target.closest('svg');selectElement(target,false);});}if(currentTarget&&feedbackDraft()?.slide_id===selectedId&&feedbackDraft()?.content_hash===slide().content_hash){try{selectedElement=doc.querySelector(currentTarget.selector);placeBox($('selection-box'),selectedElement);elementEditor.select(selectedElement);}catch{selectedElement=null;}renderTarget();}}
  /** Render discussion as text, including element anchors and original-version labels. */
  function renderWorkflow(){
    const state=deck.workflow||{mode:'live',pending:0},live=state.mode==='live',available=state.agent_waiting;
    const harness=({'codex':'Codex','claude-code':'Claude Code'})[state.review_harness];
    const session=state.review_session?' · session '+state.review_session.slice(0,8):'';
    $('mode-live').setAttribute('aria-pressed',String(live));$('mode-batch').setAttribute('aria-pressed',String(!live));
    $('mode-live').innerHTML=(brand(state.review_harness)||svg('live'))+'<span>Live <small class="live-availability '+(available?'available':'')+'">'+(available?(harness||'Available'):'Unavailable')+'</small></span>';
    $('mode-live').title='Live feedback · '+(available?(harness||'agent')+session+' available':'agent unavailable');
    $('mode-live').setAttribute('aria-label','Live feedback: '+(available?(harness||'agent')+' available':'agent unavailable'));
    $('end-review').hidden=!state.review_connected;
    $('send-batch').hidden=!state.pending;iconButton($('send-batch'),'send',(live?'Send queued comments':'Send batch')+' ('+state.pending+')',true);
    $('batch-delivery-help').hidden=live;$('send-feedback').classList.toggle('is-batch',!live);
    if(live)$('feedback-text').removeAttribute('aria-describedby');else $('feedback-text').setAttribute('aria-describedby','batch-delivery-help');
    iconButton($('send-feedback'),live?'send':'plus',live?'Send feedback':'Add to batch',!live);$('send-feedback').title+=' (Enter · Shift+Enter for a new line)';
    slideQuestions.updateDelivery($('feedback-list'));
  }

  /** Keep each preview tied to its exact content snapshot while an agent revises the file. */
  function previewDocument(item,deckId,designHash=deck?.design_hash||item.content_hash,attempt=0){const origin=location.origin;return '<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src '+origin+' \'unsafe-inline\'; img-src '+origin+'; font-src '+origin+'; script-src \'none\'; base-uri '+origin+';"><base href="'+origin+'/decks/'+deckId+'/"><link rel="stylesheet" href="'+origin+'/decks/'+deckId+'/theme.css?v='+designHash+'&retry='+attempt+'"><link rel="stylesheet" href="'+origin+'/runtime/slideshow.css"><style>html,body{width:100%;height:100%;overflow:hidden}.slide,.slide *,.slide::before,.slide::after,.slide *::before,.slide *::after{animation-play-state:paused!important}[data-review-focus]:focus-visible{outline:2px solid #386df0;outline-offset:3px}</style></head><body data-preview-key="'+item.content_hash+'">'+item.html+'</body></html>';}
  const previewLoader=window.SlidePreviewLoader.create({
    documentFor:previewDocument,
    onStart:(frame,reused)=>{
      const main=frame.id==='preview';
      if(main){
        clearTimeout(previewStatusTimer);$('preview-status').hidden=true;
        $('objects-button').disabled=true;$('approve-button').disabled=true;$('retry-preview').hidden=true;
        previewStatusTimer=setTimeout(()=>{$('preview-message').textContent=reused?'Loading slide…':'Loading design…';$('preview-status').hidden=false;},180);
      }else frame.parentElement.dataset.previewStatus='';
    },
    onReady:(frame,doc)=>{
      window.SlideAnimationPreview.freeze(doc);delete frame.parentElement.dataset.previewStatus;
      if(frame.id==='preview'){clearTimeout(previewStatusTimer);$('preview-status').hidden=true;bindPreview();objectPicker.refresh();objectPicker.sync(selectedElement);animationPreview.bind();$('approve-button').disabled=false;}
    },
    onFailure:frame=>{
      frame.parentElement.dataset.previewStatus='Design unavailable';
      if(frame.id==='preview'){clearTimeout(previewStatusTimer);$('preview-status').hidden=false;$('preview-message').textContent='Slide couldn’t load.';$('retry-preview').hidden=false;}
    }
  });
  /** Keep the current document and loaded fonts when only slide content changes. */
  function loadPreview(frame,item,deckId,designHash=deck?.design_hash||item.content_hash){
    if(frame.id==='preview')animationPreview.reset();
    previewLoader.load(frame,item,deckId,designHash||item.content_hash);
  }
  /** Offer one concrete handoff when the first draft still needs an agent. */
  function renderStart(){
    const fresh=starting(),noBrief=needsBrief();
    $('start-help').hidden=!fresh||deck.workflow?.agent_waiting||[...(deck.feedback||[]),...(slide()?.feedback||[])].some(item=>item.author==='agent');
    $('start-help-text').textContent=noBrief?'Add a brief below.':'Ask your agent to draft the slides.';
    if(noBrief){$('agent-summary').hidden=true;$('agent-panel').hidden=!slide().agent.alternatives.length&&!deck.brief_files?.length;}
    else if(fresh&&slide().agent.summary==='Template draft. Replace with your presentation content.')$('agent-summary').textContent='Brief saved.';
  }

  /** Keep questions visible and supporting explanations behind one disclosure. */
  function renderAgentContext(item){
    const context=item.agent;
    $('agent-panel').hidden=!context.summary&&!context.alternatives.length&&!context.questions.length&&!deck.brief_files?.length;$('brief-attachments').innerHTML=(deck.brief_files||[]).map((file,index)=>`<a class="file-chip" href="/api/decks/${deck.id}/brief-files/${index}" download>${svg('attach')}${escape(file.name)}</a>`).join('');
    $('questions').hidden=!context.questions.length;
    $('agent-summary').textContent=context.summary;
    $('agent-summary').hidden=!context.summary;
    $('questions').innerHTML=context.questions.map(question=>`<p class="question">${escape(question)}</p>`).join('');
    $('alternatives-panel').hidden=!context.alternatives.length;
    $('alternative-count').textContent=context.alternatives.length?`(${context.alternatives.length})`:'';
    $('alternatives').innerHTML=context.alternatives.map(value=>`<p class="alternative">${escape(value)}</p>`).join('');
    if(agentNotesKey!==key()){$('agent-panel').open=false;agentNotesKey=key();}
  }
  /** Identify chat senders visually and retain their names for assistive technology. */
  function chatAvatar(author,isReply=false){
    const user=author==='user',label=(user?'You':'Agent')+(isReply?' reply':'');
    return `<span class="chat-sender"><span class="chat-avatar ${user?'user':'agent'}" role="img" aria-label="${label}" title="${label}">${svg(user?'user':'robot')}</span>${isReply?`<span class="chat-reply" aria-hidden="true" title="Reply">${svg('reply')}</span>`:''}</span>`;
  }
  /** Preserve reading position while new messages arrive. */
  function renderComments(item){
    const comments=window.SlideConversations.forSlide(deck,item.id);
    $('feedback-count').textContent=comments.length?String(comments.length):'';
    const signature=JSON.stringify([item.content_hash,comments]),list=$('feedback-list'),changedContext=commentsKey!==key();
    if(!changedContext&&signature===commentsSignature)return;
    const pendingQuestions=window.SlideQuestions.pending(comments).length,questionStatus=$('chat-question-status');questionStatus.hidden=!pendingQuestions;iconButton(questionStatus,'question',`${pendingQuestions} question${pendingQuestions===1?'':'s'} awaiting your answer`,false);
    const focused=slideQuestions.captureFocus(list);
    const atEnd=list.scrollHeight-list.scrollTop-list.clientHeight<35;
    commentsKey=key();commentsSignature=signature;
    list.innerHTML=comments.map(comment=>comment.question&&comment.author==='agent'?slideQuestions.render(comment,comments,deck.id,item.id,item.content_hash):`<article class="comment ${comment.author}"><div class="comment-header">${chatAvatar(comment.author,!!comment.reply_to)}<time>${escape(new Date(comment.created_at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}))}</time></div>${comment.target?`<button type="button" class="comment-target" data-event="${comment.event_id}" title="${escape(comment.target.quote)}">↖ ${escape(comment.target.label)}</button>`:''}<div class="chat-markdown">${window.SlideChatMarkdown.render(comment.message)}</div>${chatComposer.sentFiles(comment)}${!comment.released?'<span class="comment-pending">Not sent</span>':''}${comment.scope!=='presentation'&&comment.content_hash!==item.content_hash?'<span class="old-version">Earlier version</span>':''}</article>`).join('')||'';
    slideQuestions.mount(list,comments,deck.id,item.id);
    if(changedContext&&pendingQuestions){list.querySelector('[data-question-state="open"]')?.scrollIntoView({block:'nearest'});}else if(changedContext||atEnd)list.scrollTop=list.scrollHeight;
    slideQuestions.restoreFocus(list,focused);
  }

  /** Display one selected slide without losing unsaved per-slide work. */
  function render(){const item=slide();if(!item)return;$('instructions-button').hidden=deck.authoring_instructions===undefined;if(window.SlideConversations.migrateDraft(feedbackDrafts,deck))draftStore.persist();watchDeck(deck.id);renderChangeStatus();document.querySelector('.workspace').classList.remove('is-empty');for(const id of ['mode-live','mode-batch'])$(id).disabled=false;const index=deck.slides.indexOf(item),draft=drafts.get(key()),commentDraft=feedbackDraft();$('chat-slide-number').textContent=String(index+1).padStart(2,'0');$('chat-slide-title').textContent=item.title;$('chat-slide-title').title=item.title;$('feedback-slide-context').innerHTML=svg('slides')+`<span>${String(index+1).padStart(2,'0')}</span>`;$('feedback-slide-context').title=`Slide ${index+1}: ${item.title} · Included with every message`;$('feedback-slide-context').setAttribute('aria-label',`Context: slide ${index+1}, ${item.title}`);$('slide-eyebrow').textContent=`${String(index+1).padStart(2,'0')} / ${String(deck.slides.length).padStart(2,'0')}`;$('slide-eyebrow').title=`Version ${item.revision}`;$('slide-title').textContent=item.title;iconButton($('approve-button'),'check',item.approved?'Approved':'Approve',true);$('approve-button').classList.toggle('is-approved',item.approved);$('approve-button').setAttribute('aria-pressed',String(item.approved));$('approve-button').title=item.approved?'Click to return this slide to needs review':'Approve this exact slide version';$('prev-slide').disabled=index===0;$('next-slide').disabled=index===deck.slides.length-1;$('present-link').href=`/decks/${deck.id}/index.html?present=1#${item.id}`;$('present-link').removeAttribute('aria-disabled');$('present-link').removeAttribute('tabindex');
    const nextKey=`${deck.id}/${item.id}/${item.content_hash}/${deck.design_hash||item.content_hash}`;currentTarget=commentDraft?.target||null;if(previewKey!==nextKey){elementEditor.select(null);if(draft?.notes!==undefined)$('notes-panel').open=true;selectedElement=null;$('selection-box').hidden=true;$('hover-box').hidden=true;if(!sorter.isOpen()){previewKey=nextKey;loadPreview($('preview'),item,deck.id);}}
    noteBaseline=draft?.expected_notes??item.notes;if($('notes').value!==(draft?.notes??item.notes))$('notes').value=draft?.notes??item.notes;if($('feedback-text').value!==(commentDraft?.feedback||''))$('feedback-text').value=commentDraft?.feedback||'';notesDirty();renderVisibility();renderTarget();renderAgentContext(item);$('source-count').textContent=item.sources.length;$('sources').innerHTML=item.sources.map(source=>source.url?`<a href="${escape(sourceURL(source.url))}" target="_blank" rel="noopener">${escape(source.label)} ↗</a>`:`<p>${escape(source.label)}</p>`).join('')||'<p>No sources.</p>';renderComments(item);renderWorkflow();renderStart();updateComposer();document.title=`${deck.title} · Slide Studio`;history.replaceState(null,'',`?deck=${encodeURIComponent(deck.id)}#${encodeURIComponent(selectedId)}`);}
  /** Keep visibility separate from review state and content drafts. */
  function renderVisibility(){const item=slide();iconButton($('hide-slide'),item?.hidden?'eyeOff':'eye',item?.hidden?'Unhide slide':'Hide slide');$('hide-slide').classList.toggle('is-hidden',!!item?.hidden);$('hide-slide').disabled=visibilityBusy||slideActions.isBusy()||!item;}
  async function setHidden(id,ids=[id]){
    if(visibilityBusy||slideActions.isBusy()||!deck)return;
    const deckId=deck.id,items=(pendingDeck||deck).slides.filter(item=>ids.includes(item.id));if(!items.length)return;
    const hidden=items.some(item=>!item.hidden);
    visibilityBusy=true;orderEpoch++;updateComposer();renderVisibility();renderRail();$('toggle-slide-hidden').disabled=true;
    try{await api(`/api/decks/${deckId}/slides-visibility`,{slide_ids:items.map(item=>item.id),hidden,expected_hidden:Object.fromEntries(items.map(item=>[item.id,!!item.hidden]))});const latest=await api(`/api/decks/${deckId}`);if(deck?.id!==deckId)return;for(const snapshot of [deck,pendingDeck]){for(const target of snapshot?.slides||[]){if(!ids.includes(target.id))continue;target.hidden=hidden;const saved=latest.slides.find(item=>item.id===target.id);if(saved&&['id','title','html','notes','sources','agent','hidden','revision'].every(name=>JSON.stringify(target[name])===JSON.stringify(saved[name])))target.expected_hash=saved.expected_hash;}}renderRail();}
    catch(error){toast(error.message,true);}
    finally{visibilityBusy=false;orderEpoch++;updateComposer();renderVisibility();renderRail();$('toggle-slide-hidden').disabled=false;void refresh();}
  }
  async function openSlideMenu(id,point){
    if(visibilityBusy||slideOrder.isBusy()||slideActions.isBusy())return;
    const menu=$('slide-menu'),button=$('toggle-slide-hidden'),count=slideOrder.selection().length,selection=$('slide-menu-selection');selection.hidden=count<2;selection.innerHTML=svg('slides')+`<span>${count} selected</span>`;selection.setAttribute('aria-label',`${count} slides selected`);menuSlide={deckId:deck.id,id};const hide=(pendingDeck||deck).slides.some(item=>slideOrder.selection().includes(item.id)&&!item.hidden);iconButton(button,hide?'eye':'eyeOff',`${hide?'Hide':'Unhide'} ${count>1?'selected slides':'slide'}`,true);for(const control of menu.querySelectorAll('[data-slide-action]')){const action=control.dataset.slideAction;iconButton(control,{add:'slideAdd',duplicate:'copy',delete:'trash'}[action],{add:'New slide',duplicate:count>1?'Duplicate slides':'Duplicate slide',delete:count>1?'Delete slides':'Delete slide'}[action],true);control.disabled=action==='delete'?count===(pendingDeck||deck).slides.length:action==='add'?(pendingDeck||deck).slides.length>=100:(pendingDeck||deck).slides.length+count>100;}menu.showPopover();
    for(const control of menu.querySelectorAll('[data-slide-move]')){const action=control.dataset.slideMove;iconButton(control,action,{up:'Move up',down:'Move down',first:'Move to beginning',last:'Move to end'}[action],true);control.disabled=!slideOrder.canMove(action);}
    const anchor=$('slides').querySelector(`[data-id="${CSS.escape(id)}"]`).getBoundingClientRect(),rect=menu.getBoundingClientRect();menu.style.left=`${Math.max(8,Math.min(point?.x??anchor.right-15,innerWidth-rect.width-8))}px`;menu.style.top=`${Math.max(8,Math.min(point?.y??anchor.top+12,innerHeight-rect.height-8))}px`;menu.querySelector('button:not(:disabled)')?.focus();
  }
  $('hide-slide').addEventListener('click',()=>setHidden(selectedId));
  $('toggle-slide-hidden').addEventListener('click',()=>{const target=menuSlide;$('slide-menu').hidePopover();if(target?.deckId===deck?.id)void setHidden(target.id,slideOrder.selection());});
  $('slide-menu').addEventListener('click',event=>{const control=event.target.closest('[data-slide-move],[data-slide-action]');if(!control||control.disabled)return;$('slide-menu').hidePopover();if(control.dataset.slideAction)void slideActions.run(control.dataset.slideAction);else void slideOrder.move(control.dataset.slideMove);});
  $('slide-menu').addEventListener('keydown',event=>{if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();const buttons=[...$('slide-menu').querySelectorAll('button:not(:disabled)')],index=buttons.indexOf(document.activeElement),next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;buttons[next]?.focus();}if(event.key==='Escape'){event.preventDefault();$('slide-menu').hidePopover();if(menuSlide?.deckId===deck?.id)$('slides').querySelector(`[data-id="${CSS.escape(menuSlide.id)}"]`)?.focus();}});
  /** Choose a slide and restore any draft feedback attached to it. */
  async function choose(id){const request=++selectionEpoch,deckId=deck?.id;if(visibilityBusy||slideActions.isBusy()||!await elementEditor.finish()||request!==selectionEpoch||deck?.id!==deckId||!deck.slides.some(item=>item.id===id))return false;retainDraft();if(pendingDeck){deck=pendingDeck;pendingDeck=null;$('update-banner').hidden=true;}selectedId=id;renderRail();render();if(!sorter.isOpen())void markOpened();$('slides').querySelector('[aria-current="true"]')?.scrollIntoView({block:'nearest'});return true;}
  const slideOrder=window.SlideOrder.create({
    rail:$('slides'),read:()=>deck?{deckId:deck.id,ids:(pendingDeck||deck).slides.map(item=>item.id),activeId:selectedId}:null,
    layout:()=>sorter.isOpen()?'grid':'list',activate:async id=>{if(await choose(id))await sorter.edit();},onBusy:()=>sorter.sync(),
    open:choose,contextMenu:openSlideMenu,command:action=>slideActions.run(action),undoAction:()=>slideActions.undo(),blocked:()=>slideActions.isBusy()||visibilityBusy,
    notify:(message,undo)=>toast(message,false,undo?{label:'Undo',run:undo}:null),
    save:async(ids,expected)=>{
      const deckId=deck.id;retainDraft();orderEpoch++;
      try{
        const saved=await api(`/api/decks/${deckId}/slide-order`,{slide_ids:ids,expected_slide_ids:expected});
        if(deck?.id!==deckId)return false;
        for(const snapshot of [deck,pendingDeck]){if(!snapshot)continue;const lookup=new Map(snapshot.slides.map(item=>[item.id,item]));if(lookup.size===saved.slide_ids.length&&saved.slide_ids.every(id=>lookup.has(id)))snapshot.slides=saved.slide_ids.map(id=>lookup.get(id));}
        slideActions.clearUndo();renderRail();render();return true;
      }catch(error){toast(error.message,true);return false;}
      finally{orderEpoch++;setTimeout(()=>void refresh(),0);}
    }
  });
  const sorter=window.SlideSorter.create({
    workspace:document.querySelector('.workspace'),rail:$('slides'),preview:$('preview-wrap'),
    controls:{editButton:$('edit-view'),gridButton:$('sorter-view'),minus:$('zoom-out'),plus:$('zoom-in'),range:$('view-zoom'),value:$('zoom-value'),fit:$('fit-view'),status:$('view-status')},
    read:()=>deck?{count:deck.slides.length,index:deck.slides.findIndex(item=>item.id===selectedId)}:null,
    blocked:()=>visibilityBusy||slideOrder.isBusy()||slideOrder.isDragging()||slideActions.isBusy()||switchingDeck||feedbackSending,
    prepare:async()=>{if(!await elementEditor.finish())return false;if(animationPreview.isPlaying())animationPreview.stop();if($('object-panel').matches(':popover-open'))$('object-panel').hidePopover();return true;},
    edit:async id=>{if(await choose(id))await sorter.edit();},
    onChange:()=>{if(!deck)return;retainDraft();renderRail();render();if(!sorter.isOpen()){if($('preview').contentDocument?.body?.dataset.previewKey===slide().content_hash){bindPreview();objectPicker.refresh();}void markOpened();}}
  });
  const objectPicker=window.SlideObjects.create({
    button:$('objects-button'),panel:$('object-panel'),frame:$('preview'),
    onSelect:element=>selectElement(element,false),
    onHover:element=>{hoverElement=element;placeBox($('hover-box'),element);if(element)$('hover-box').querySelector('span').textContent=labelFor(element);}
  });
  const slideActions=window.SlideActions.create({
    read:()=>deck?{...(pendingDeck||deck),activeId:selectedId}:null,
    selection:()=>slideOrder.selection(),request:api,
    prepare:async()=>{if(slideOrder.isBusy()||visibilityBusy||feedbackSending||switchingDeck||!await elementEditor.finish())return false;retainDraft();return true;},
    onBusy:busy=>{orderEpoch++;$('slides').setAttribute('aria-busy',String(busy));updateComposer();renderVisibility();if(deck)renderRail();},
    notify:(message,undo,error=false)=>toast(message,error,undo?{label:error?'Retry':'Undo',run:undo}:null),
    apply:async(result,command)=>{
      if(deck?.id!==command.deckId)return false;
      const previous=pendingDeck||deck,snapshot=await api(`/api/decks/${encodeURIComponent(command.deckId)}`);
      if(deck?.id!==command.deckId)return false;
      if(result.action==='duplicate')result.selected_ids.forEach((source,index)=>{const note=drafts.get(`${command.deckId}/${source}`);if(note?.notes!==undefined){const copy=snapshot.slides.find(item=>item.id===result.added_ids[index]);drafts.set(`${command.deckId}/${copy.id}`,{...note,expected_notes:copy.notes});}});
      const added=result.added_ids.filter(id=>snapshot.slides.some(item=>item.id===id));
      const priorIndex=previous.slides.findIndex(item=>item.id===selectedId);
      const survivors=new Set(snapshot.slides.map(item=>item.id));
      const neighbour=previous.slides.slice(priorIndex+1).find(item=>survivors.has(item.id))||previous.slides.slice(0,priorIndex).findLast(item=>survivors.has(item.id));
      deck=snapshot;pendingDeck=null;$('update-banner').hidden=true;
      selectedId=added[0]||(survivors.has(selectedId)?selectedId:neighbour?.id||snapshot.slides[0].id);
      draftStore.persist();slideOrder.resetHistory();renderRail();render();slideOrder.setSelection(added.length?added:[selectedId]);
      $('slides').querySelector(`[data-id="${CSS.escape(selectedId)}"]`)?.focus({preventScroll:true});$('slides').querySelector('[aria-current="true"]')?.scrollIntoView({block:'nearest'});
      return true;
    }
  });
  const projectFiles=window.SlideProjects.init({
    toast,read:()=>deck,
    capture:()=>{
      if(elementEditor.isEditing())throw new Error('Finish or cancel the element edit before saving or opening a project.');
      retainDraft();if(!deck)return {};
      const prefix=deck.id+'/';
      let presenter_notes={};try{presenter_notes=JSON.parse(localStorage.getItem(`slide-studio:presenter-notes:${deck.id}`)||'{}');}catch{}
      return {active_slide_id:selectedId,...sorter.snapshot(),notes:Object.fromEntries([...drafts].filter(([name,value])=>name.startsWith(prefix)&&value.notes!==undefined).map(([name,value])=>[name.slice(prefix.length),value])),feedback:Object.fromEntries([...feedbackDrafts].filter(([name,value])=>name.startsWith(prefix)&&(value.feedback.trim()||value.target)).map(([name,value])=>[name.slice(prefix.length),value])),presenter_notes,question_answers:slideQuestions.exportDrafts(deck.id),open_panels:[['notes','notes-panel'],['sources',null],['agent','agent-panel']].filter(([name,id])=>(id?$(id):document.querySelector('.sources-panel')).open).map(([name])=>name)};
    },
    restore:async opened=>{
      const state=opened.client,prefix=opened.deck_id+'/';
      slideQuestions.importDrafts(opened.deck_id,state.question_answers);
      for(const [id,value] of Object.entries(state.notes||{}))drafts.set(prefix+id,value);
      for(const [id,value] of Object.entries(state.feedback||{}))feedbackDrafts.set(prefix+id,value);
      if(Object.keys(state.presenter_notes||{}).length)localStorage.setItem(`slide-studio:presenter-notes:${opened.deck_id}`,JSON.stringify(state.presenter_notes));
      draftStore.persist();history.replaceState(null,'',`?deck=${encodeURIComponent(opened.deck_id)}#${encodeURIComponent(state.active_slide_id||'')}`);await start(opened.deck_id,{acknowledge:false});
      for(const [name,id] of [['notes','notes-panel'],['sources',null],['agent','agent-panel']])(id?$(id):document.querySelector('.sources-panel')).open=(state.open_panels||[]).includes(name);
      await sorter.restore(state);
    }
  });
  /** Poll durable state while preserving a user's in-progress review of an older version. */
  async function refresh(force=false){
    if(!deck||switchingDeck||presentationActions.isBusy()||slideOrder.isBusy()||slideOrder.isDragging()||slideActions.isBusy())return;
    if(isLoading){refreshQueued=true;return;}
    isLoading=true;
    try{
      const deckId=deck.id,epoch=orderEpoch,snapshot=await api(`/api/decks/${deckId}`);
      if(deck?.id!==deckId||switchingDeck||presentationActions.isBusy())return;
      if(epoch!==orderEpoch||slideOrder.isBusy()||slideOrder.isDragging()||slideActions.isBusy()){refreshQueued=true;return;}
      if(JSON.stringify(snapshot)===JSON.stringify(deck)){renderWorkflow();renderStart();return;}
      const contentChanged=snapshot.slides.find(item=>item.id===selectedId)?.content_hash!==slide()?.content_hash;
      const editing=currentTarget||$('notes').value!==noteBaseline;
      if(contentChanged&&editing&&!force){pendingDeck=snapshot;deck.workflow=snapshot.workflow;renderWorkflow();renderRail(snapshot);renderChangeStatus();$('update-banner').hidden=false;return;}
      retainDraft();deck=snapshot;pendingDeck=null;$('update-banner').hidden=true;
      if(!deck.slides.some(item=>item.id===selectedId))selectedId=deck.slides[0].id;
      renderRail();render();
    }catch(error){toast(error.message,true);}
    finally{isLoading=false;if(refreshQueued){refreshQueued=false;void refresh();}}
  }
  /** Save notes explicitly, detecting another editor's changes. */
  async function saveNotes(){const value=$('notes').value,deckId=deck.id,id=selectedId;try{await api(`${base()}/notes`,{notes:value,expected_notes:noteBaseline});if(deck.id===deckId&&selectedId===id){noteBaseline=value;slide().notes=value;retainDraft();notesDirty();}toast('Notes saved.');}catch(error){toast(error.message,true);}}
  function notesDirty(){$('save-notes').disabled=$('notes').value===noteBaseline;$('notes-state').textContent=$('notes').value===noteBaseline?'':'Unsaved';}
  $('retry-preview').addEventListener('click',async()=>{if(!await elementEditor.finish())return;loadPreview($('preview'),slide(),deck.id);railSignature='';renderRail();});
  async function copyAgentPrompt(){
    const target=deck?`deck_id="${deck.id}"`:'';
    const task=deck?`Work on presentation "${deck.title}" (deck ID: ${deck.id}). Call get_working_context(${target}, include_discovery=False), get_presentation_outline(${target}, include_context=True) and read_feedback(${target}). Read the returned full brief; inspect relevant brief_files attachments and design-reference source files with file tools. Report inaccessible or missing sources. Read get_slide_questions for pending decisions and use ask_slide_question for missing slide context. Follow the quickstart, preserve my notes and address released feedback. Use concise text and useful visuals.`:`Help me add a design. Call get_working_context() and read_design_requests(). Inspect my reference using file tools and report if unavailable, then register the design and create_presentation with the chosen design_id and a short brief. For the new deck, read get_working_context(deck_id=...) and get_presentation_outline(deck_id=..., include_context=True) before drafting.`;
    const prompt=`${task} For automatic feedback, call get_authoring_guide(topic="live_review"), follow native hook setup/trust/reload, then start_live_review(${target}) and get_live_review_status(${target}). Compare review_session with this host session before claiming connection. Keep the review open. Workspace: ${location.origin}/${deck?`?deck=${encodeURIComponent(deck.id)}`:''} MCP: ${location.origin}/mcp`;
    try{await navigator.clipboard.writeText(prompt);toast('Prompt copied.');}catch{toast('Copy is unavailable. Ask your agent to open Slide Studio and call get_working_context.',true);}
  }
  $('copy-start').addEventListener('click',copyAgentPrompt);
  $('copy-agent-prompt').addEventListener('click',copyAgentPrompt);
  $('copy-live-setup').addEventListener('click',async()=>{
    const target=deck?`deck_id="${deck.id}"`:'';
    const prompt=`Set up automatic Slide Studio feedback. Call get_working_context(${target}${deck?', include_discovery=False':''}) and get_authoring_guide(topic="live_review"). Use app_path and the actual MCP server name with studio.hook_client configure in your working project. Preserve settings and follow native hook trust/reload requirements. Then start_live_review(${target}) and get_live_review_status(${target}); compare review_session with this host session before claiming connection. ${deck?`Read get_presentation_outline(${target}, include_context=True), the returned full brief and read_feedback(${target}). Inspect relevant brief_files attachments and design-reference sources with file tools; report inaccessible or missing sources and use ask_slide_question for missing slide context.`:'Read read_design_requests().'} Keep review open, preserve my notes, address released feedback and visually check changes. Workspace: ${location.origin}/${deck?`?deck=${encodeURIComponent(deck.id)}`:''} MCP: ${location.origin}/mcp`;
    try{await navigator.clipboard.writeText(prompt);toast('Setup prompt copied.');}catch{toast('Copy is unavailable.',true);}
  });
  $('end-review').addEventListener('click',async()=>{if(!deck)return;const selected=deck.id;$('end-review').disabled=true;try{const state=await api(`/api/decks/${selected}/review/disconnect`,{});if(deck?.id===selected){deck.workflow=state;renderWorkflow();}toast('Review ended.');}catch(error){toast(error.message,true);}finally{$('end-review').disabled=false;}});
  $('slides').addEventListener('click',event=>{const visibility=event.target.closest('[data-visibility-id]');if(visibility)void setHidden(visibility.dataset.visibilityId);});
  $('prev-slide').addEventListener('click',()=>choose(deck.slides[Math.max(0,deck.slides.findIndex(item=>item.id===selectedId)-1)].id));$('next-slide').addEventListener('click',()=>choose(deck.slides[Math.min(deck.slides.length-1,deck.slides.findIndex(item=>item.id===selectedId)+1)].id));
  $('clear-target').addEventListener('click',()=>selectElement(null));$('select-parent').addEventListener('click',()=>selectElement(selectedElement?.parentElement));
  $('notes').addEventListener('input',()=>{notesDirty();retainDraft();});$('save-notes').addEventListener('click',saveNotes);$('feedback-text').addEventListener('input',()=>{retainDraft();updateComposer();});
  /** Send chat messages with Enter while preserving multiline and composition input. */
  function bindMessageEnter(inputId,buttonId,send){$(inputId).addEventListener('keydown',event=>{if(event.key!=='Enter'||event.shiftKey||event.altKey||event.isComposing||event.keyCode===229)return;event.preventDefault();if(!event.repeat&&!$(buttonId).disabled)send();});}
  bindMessageEnter('feedback-text','send-feedback',()=>$('feedback-form').requestSubmit());
  $('feedback-form').addEventListener('submit',async event=>{event.preventDefault();if(!deck||feedbackSending||(!$('feedback-text').value.trim()&&!chatComposer.hasFiles()))return;if(!await elementEditor.finish()||feedbackSending)return;retainDraft();const button=$('send-feedback'),sendingDeck=deck.id,sendingKey=key(),context=feedbackDraft(),message=$('feedback-text').value;feedbackSending=true;updateComposer();$('feedback-text').disabled=true;try{const endpoint=`/api/decks/${sendingDeck}/slides/${context.slide_id}/feedback`;const files=await chatComposer.payload(sendingKey);const body={message,...(files.length?{files}:{}),content_hash:context.content_hash,target:context.target,request_id:context.request_id};await api(endpoint,body);chatComposer.clear(sendingKey);feedbackDrafts.delete(sendingKey);draftStore.persist();if(key()===sendingKey){$('feedback-text').value='';selectElement(null,false);}await refresh();toast(deck.workflow?.mode==='batch'?'Added to batch.':deck.workflow?.agent_waiting?'Sent to agent.':'Saved for agent.');}catch(error){chatComposer.error(error.message,sendingKey);}finally{chatComposer.idle(sendingKey);feedbackSending=false;$('feedback-text').disabled=false;updateComposer();if(key()===sendingKey&&!document.querySelector('dialog[open]')&&[document.body,button].includes(document.activeElement))$('feedback-text').focus();}});

  $('approve-button').addEventListener('click',async()=>{try{await api(`${base()}/approve`,{content_hash:slide().content_hash,approved:!slide().approved});await refresh();}catch(error){toast(error.message,true);await refresh();}});
  $('feedback-list').addEventListener('click',event=>{if(feedbackSending)return;const button=event.target.closest('[data-event]');if(!button)return;const comment=window.SlideConversations.forSlide(deck,selectedId).find(item=>item.event_id===Number(button.dataset.event));if(!comment?.target)return;if(comment.content_hash!==slide().content_hash){toast(`This comment targets an earlier version: ${comment.target.quote||comment.target.label}`);return;}const element=$('preview').contentDocument.querySelector(comment.target.selector);if(element)selectElement(element);else toast('The original element is no longer present.');});
  $('load-update').addEventListener('click',async()=>{if(!pendingDeck||!await elementEditor.finish())return;retainDraft();const draft=feedbackDraft();if(draft&&draft.target){toast('Draft kept on its original version. Select an element again to retarget it.');}deck=pendingDeck;pendingDeck=null;$('update-banner').hidden=true;if(!slide())selectedId=deck.slides[0].id;renderRail();render();void markOpened();});
  $('deck-picker').addEventListener('change',async event=>{retainDraft();try{await start(event.target.value);}catch(error){if(deck)$('deck-picker').value=deck.id;toast(error.message,true);}finally{deckPicker?.sync();if(!$('deck-switcher').disabled&&[document.body,$('deck-switcher')].includes(document.activeElement))$('deck-switcher').focus({preventScroll:true});}});
  for(const mode of ['live','batch'])$('mode-'+mode).addEventListener('click',async()=>{try{deck.workflow=await api('/api/decks/'+deck.id+'/feedback-mode',{mode});renderWorkflow();}catch(error){toast(error.message,true);}});
  $('send-batch').addEventListener('click',async()=>{try{deck.workflow=await api('/api/decks/'+deck.id+'/send-batch',{});renderWorkflow();await refresh();toast('Batch released.');}catch(error){toast(error.message,true);}});
  const attachmentFiles={design:[],brief:[]};
  const designReferences=window.SlideDesignReferences.create({api,button:iconButton});
  $('selected-design-references').addEventListener('click',()=>{const selected=document.querySelector('input[name="design"]:checked');if(selected)void designReferences.open(selected.value,selected.closest('.design-card')?.querySelector('strong')?.textContent);});
  $('deck-design-references').addEventListener('click',()=>{if(deck?.design_id)void designReferences.open(deck.design_id,deck.design_id);else toast('Select a presentation with a design first.',true);});
  let designSignature='',inboxSignature='',designTimer=null,designRequestId=null;
  /** Refresh the library without resetting the user's selected brand or draft message. */
  async function loadDesigns(reset=false){const [designs,preferences]=await Promise.all([api('/api/designs'),api('/api/preferences')]);$('selected-design-references').disabled=!designs.length;const defaultId=preferences.default_design_id;const signature=JSON.stringify([designs,defaultId]);if(!reset&&signature===designSignature)return;const selected=reset?defaultId:document.querySelector('input[name="design"]:checked')?.value||defaultId;designSignature=signature;designs.sort((a,b)=>(a.id===defaultId?-1:b.id===defaultId?1:a.name.localeCompare(b.name)));$('design-options').innerHTML=designs.length?designs.map(design=>`<label class="design-card"><input type="radio" name="design" value="${escape(design.id)}" ${design.id===selected?'checked':''} required><span class="design-preview"><iframe title="${escape(design.name)} design example" src="/api/designs/${encodeURIComponent(design.id)}/preview" tabindex="-1" sandbox></iframe></span><strong title="${escape(design.description)}">${escape(design.name)}</strong></label>`).join(''):'<p class="library-help">Send a PowerPoint, image or link below to add a design.</p>';if(!document.querySelector('input[name="design"]:checked'))document.querySelector('input[name="design"]')?.click();}
  /** Display durable agent replies and attachment links beside the design choices. */
  async function loadDesignChat(){const inbox=await api('/api/design-requests');$('design-availability').textContent=inbox.agent_waiting?'Available':'Unavailable';$('design-availability').setAttribute('aria-label',inbox.agent_waiting?'Agent available':'Agent unavailable');$('design-availability').classList.toggle('available',inbox.agent_waiting);const signature=JSON.stringify(inbox.messages);if(signature!==inboxSignature){inboxSignature=signature;const list=$('design-messages');const atBottom=list.scrollHeight-list.scrollTop-list.clientHeight<35;list.innerHTML=inbox.messages.map(item=>`<article class="design-message ${item.author}"><div class="design-message-header">${chatAvatar(item.author,!!item.reply_to)}</div><p>${escape(item.message)}</p>${item.files.map((file,index)=>`<a class="file-chip" href="/api/design-requests/${item.event_id}/files/${index}" download>${svg('attach')}${escape(file.name)}</a>`).join('')}${item.author==='user'?`<span class="request-status">${({queued:'Queued',working:'Working',needs_input:'Needs your input',ready:'Ready'})[item.status]}</span>`:''}${item.design_id&&item.author==='agent'?`<button class="use-design" type="button" data-use-design="${escape(item.design_id)}">${svg('check')}Use this design</button>`:''}</article>`).join('');if(atBottom)list.scrollTop=list.scrollHeight;}await loadDesigns();}
  /** Render removable files identically for brief and design references. */
  function renderFiles(kind){$(kind+'-files').innerHTML=attachmentFiles[kind].map((file,index)=>`<span class="file-chip">${svg('attach')}${escape(file.name)}<button type="button" data-remove-file="${index}" aria-label="Remove ${escape(file.name)}" title="Remove ${escape(file.name)}">${svg('close')}</button></span>`).join('');}
  /** Encode selected local references for the shared attachment API. */
  function readFiles(files){return Promise.all(files.map(file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve({name:file.name,data_base64:String(reader.result).split(',')[1]});reader.onerror=()=>reject(new Error('Could not read '+file.name));reader.readAsDataURL(file);})) );}
  function toggleDesignChat(open){$('design-chat').hidden=!open;$('toggle-design-chat').setAttribute('aria-expanded',String(open));iconButton($('toggle-design-chat'),open?'close':'plus',open?'Close design chat':'Add a design',false);if(open)$('design-message').focus();}
  $('toggle-design-chat').addEventListener('click',()=>toggleDesignChat($('design-chat').hidden));
  $('new-button').addEventListener('click',async()=>{try{if(!await elementEditor.finish())return;await loadDesigns(true);await loadDesignChat();$('create-error').textContent='';toggleDesignChat(false);$('new-dialog').showModal();$('new-title').focus();clearInterval(designTimer);designTimer=setInterval(()=>{if($('new-dialog').open)loadDesignChat().catch(error=>$('design-error').textContent=error.message);},2000);}catch(error){toast(error.message,true);}});
  $('new-dialog').addEventListener('close',()=>clearInterval(designTimer));
  for(const kind of ['design','brief']){
    $('attach-'+kind).addEventListener('click',()=>$(kind+'-file-input').click());
    $(kind+'-file-input').addEventListener('change',()=>{const input=$(kind+'-file-input'),incoming=[...input.files],files=[...attachmentFiles[kind],...incoming];if(files.length>5||files.some(file=>file.size>25000000)||files.reduce((sum,file)=>sum+file.size,0)>50000000){$(kind+'-error').textContent='Up to 5 files, 25 MB each and 50 MB total.';}else{attachmentFiles[kind]=files;if(kind==='design')designRequestId=null;renderFiles(kind);$(kind+'-error').textContent='';}input.value='';});
    $(kind+'-files').addEventListener('click',event=>{const button=event.target.closest('[data-remove-file]');if(button){attachmentFiles[kind].splice(Number(button.dataset.removeFile),1);if(kind==='design')designRequestId=null;renderFiles(kind);$(kind+'-error').textContent='';}});
  }
  $('design-message').addEventListener('input',()=>{designRequestId=null;});
  bindMessageEnter('design-message','send-design',()=>$('send-design').click());
  $('design-messages').addEventListener('click',event=>{const button=event.target.closest('[data-use-design]');if(button){const input=[...document.querySelectorAll('input[name="design"]')].find(item=>item.value===button.dataset.useDesign);if(input){input.checked=true;$('new-title').focus();}}});
  $('send-design').addEventListener('click',async()=>{const message=$('design-message').value.trim();if(!message&&!attachmentFiles.design.length){$('design-message').focus();return;}const button=$('send-design');button.disabled=true;$('design-message').disabled=true;$('attach-design').disabled=true;$('design-error').textContent='';designRequestId??=crypto.randomUUID();try{const files=await readFiles(attachmentFiles.design);await api('/api/design-requests',{message,files,request_id:designRequestId});$('design-message').value='';attachmentFiles.design=[];designRequestId=null;renderFiles('design');await loadDesignChat();$('design-messages').scrollTop=$('design-messages').scrollHeight;}catch(error){$('design-error').textContent=error.message;}finally{button.disabled=false;$('design-message').disabled=false;$('attach-design').disabled=false;if($('new-dialog').open&&[document.body,button,$('new-dialog')].includes(document.activeElement))$('design-message').focus();}});
  $('close-new').addEventListener('click',()=>$('new-dialog').close());
  $('new-form').addEventListener('submit',async event=>{event.preventDefault();const button=$('create-button');button.disabled=true;try{const title=$('new-title').value.trim(),brief=$('new-brief').value.trim();if(!brief){$('new-brief').focus();throw new Error('Add a brief.');}const date=new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Berlin',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());const slug=title.normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'').slice(0,60)||'presentation';const created=await api('/api/decks',{deck_id:date+'-'+slug,title,brief,files:await readFiles(attachmentFiles.brief),design_id:new FormData($('new-form')).get('design')||''});retainDraft();const option=new Option(created.title,created.id);$('deck-picker').append(option);$('deck-picker').value=created.id;deck=await api('/api/decks/'+created.id);selectedId=deck.slides[0].id;pendingDeck=null;$('update-banner').hidden=true;renderRail();render();$('new-dialog').close();$('new-form').reset();attachmentFiles.brief=[];renderFiles('brief');$('brief-error').textContent='';toast('Presentation created.');}catch(error){$('create-error').textContent=error.message;}finally{button.disabled=false;}});
  $('connect-button').addEventListener('click',()=>{$('mcp-url').value=`${location.origin}/mcp`;$('connect-dialog').showModal();});$('close-connect').addEventListener('click',()=>$('connect-dialog').close());$('copy-url').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('mcp-url').value);toast('URL copied.');}catch{$('mcp-url').select();}});
  new ResizeObserver(()=>{if(sorter.isOpen())return;if(selectedElement){elementEditor.reflow();elementEditor.sync();}if(hoverElement)placeBox($('hover-box'),hoverElement);}).observe($('preview'));
  window.addEventListener('beforeunload',event=>{retainDraft();if(instructionsDraft!==null||learnedDraft!==null||chatComposer.hasDrafts()||slideQuestions.hasDrafts()||designReferences.hasDrafts()||elementEditor.isEditing()||$('new-title').value.trim()||$('new-brief').value.trim()||$('design-message').value.trim()||attachmentFiles.design.length||attachmentFiles.brief.length||[...feedbackDrafts.values()].some(draft=>draft.feedback.trim())||[...drafts.values()].some(draft=>draft.notes!==undefined&&draft.notes!==draft.expected_notes)){event.preventDefault();event.returnValue='';}});
  document.addEventListener('keydown',event=>{if(!deck||event.defaultPrevented||event.isComposing||event.ctrlKey||event.metaKey||event.altKey||event.target.closest('textarea,input,select,dialog,#element-tools,#slide-menu,#object-panel,#element-align-menu'))return;if(event.key==='ArrowLeft'){event.preventDefault();$('prev-slide').click();}if(event.key==='ArrowRight'){event.preventDefault();$('next-slide').click();}if(event.key==='Escape'){event.preventDefault();if(elementEditor.isEditing())elementEditor.cancel();else void selectElement(null,false);}});
  /** Clear stale slide content when the last selector entry is removed. */
  function renderEmpty(){sorter.reset();previewLoader.cancel($('preview'));clearTimeout(previewStatusTimer);$('preview-status').hidden=true;animationPreview.reset();elementEditor.select(null);watchDeck(null);document.querySelector('.preview-wrap').classList.remove('has-unseen-changes');$('deck-picker').selectedIndex=-1;deck=null;selectedId=null;pendingDeck=null;currentTarget=null;selectedElement=null;hoverElement=null;previewKey='';railSignature='';noteBaseline='';document.querySelector('.workspace').classList.add('is-empty');$('slides').replaceChildren();$('chat-question-status').hidden=true;$('progress-text').replaceChildren();$('progress-text').removeAttribute('aria-label');$('progress-text').removeAttribute('title');$('progress-bar').style.width='0%';$('preview').onload=null;$('preview').dataset.loadToken='';$('preview').srcdoc='';$('notes').value='';$('feedback-text').value='';$('update-banner').hidden=true;$('slide-title').textContent=$('deck-picker').options.length?'Choose a presentation':'No presentations';$('slide-eyebrow').textContent=$('deck-picker').options.length?'':'Create a presentation to begin.';$('present-link').removeAttribute('href');$('present-link').setAttribute('aria-disabled','true');$('present-link').tabIndex=-1;for(const id of ['approve-button','prev-slide','next-slide','mode-live','mode-batch'])$(id).disabled=true;slideActions.update();updateComposer();document.title='Slide Studio';history.replaceState(null,'','/');}
  /** Rebuild the selector while keeping a requested or still-visible presentation selected. */
  async function start(preferred=null,{acknowledge=true}={}){if(slideActions.isBusy())throw new Error('Wait for the slide action to finish.');if(!await elementEditor.finish())throw new Error('Finish or cancel the text edit.');switchingDeck=true;updateComposer();try{const items=await api('/api/decks');$('deck-picker').innerHTML=items.map(item=>`<option value="${escape(item.id)}">${escape(item.title)}</option>`).join('');if(!deck)$('deck-picker').selectedIndex=-1;if(!items.length){renderEmpty();return;}const requested=preferred||deck?.id||new URLSearchParams(location.search).get('deck');const id=items.some(item=>item.id===requested)?requested:items[0].id;const snapshot=await api(`/api/decks/${id}`);$('deck-picker').value=id;deck=snapshot;selectedId=deck.slides.some(item=>item.id===decodeURIComponent(location.hash.slice(1)))?decodeURIComponent(location.hash.slice(1)):deck.slides[0].id;pendingDeck=null;$('update-banner').hidden=true;renderRail();render();if(acknowledge&&!sorter.isOpen())void markOpened();}finally{switchingDeck=false;updateComposer();}}
  const presentationActions=window.SlidePresentations.init({api,toast,escape,currentDeck:()=>deck,retainDraft,reload:start,forget:id=>{$('deck-picker').querySelector(`option[value="${CSS.escape(id)}"]`)?.remove();if(deck?.id===id)renderEmpty();},recover:item=>{if(![...$('deck-picker').options].some(option=>option.value===item.id))$('deck-picker').add(new Option(item.title,item.id));if(!deck)renderEmpty();}});
  deckPicker=window.SlideDeckPicker.create({select:$('deck-picker'),button:$('deck-switcher'),panel:$('deck-menu'),list:$('deck-menu-options'),createButton:$('new-project-option'),onCreate:()=>$('new-button').click(),onOpen:()=>animationPreview.stop(),blocked:()=>feedbackSending||switchingDeck||visibilityBusy||slideActions.isBusy()||presentationActions.isBusy()});
  window.SlideLibraries.init({api,toast,escape,currentDeck:()=>deck});
  let instructionsBaseline='',instructionsDraft=null,learnedBaseline='',learnedDraft=null,instructionsSaving=false,rulesEditor=null,rulesEditorPromise=null,learnedEditor=null,learnedEditorPromise=null;
  function getRulesEditor(){
    if(!rulesEditorPromise)rulesEditorPromise=window.SlideRulesEditor.create($('authoring-instructions'),{onChange:()=>{instructionsDraft=rulesEditor.getMarkdown();}}).then(editor=>{rulesEditor=editor;return editor;}).catch(error=>{rulesEditorPromise=null;throw error;});
    return rulesEditorPromise;
  }
  function getLearnedEditor(){
    if(!learnedEditorPromise)learnedEditorPromise=window.SlideRulesEditor.create($('learned-rules'),{label:'Learned preferences',onChange:()=>{learnedDraft=learnedEditor.getMarkdown();}}).then(editor=>{learnedEditor=editor;return editor;}).catch(error=>{learnedEditorPromise=null;throw error;});
    return learnedEditorPromise;
  }
  async function loadInstructionEditors(saved){
    const [editor,learned]=await Promise.all([getRulesEditor(),getLearnedEditor()]);
    if(instructionsDraft===null)instructionsBaseline=saved.expected_hash;
    if(learnedDraft===null)learnedBaseline=saved.learned_preferences_hash;
    editor.setMarkdown(instructionsDraft??saved.markdown);
    learned.setMarkdown(learnedDraft??saved.learned_preferences);
  }
  $('instructions-button').addEventListener('click',async()=>{
    if(instructionsSaving)return;
    $('instructions-button').disabled=true;$('instructions-button').setAttribute('aria-busy','true');
    try{await loadInstructionEditors(await api('/api/instructions'));$('instructions-error').textContent='';$('instructions-dialog').showModal();}
    catch(error){toast(error.message,true);}
    finally{$('instructions-button').disabled=false;$('instructions-button').removeAttribute('aria-busy');}
  });
  function setInstructionsBusy(value){instructionsSaving=value;rulesEditor?.enable(!value);learnedEditor?.enable(!value);for(const id of ['save-instructions','close-instructions','reload-instructions','instructions-button'])$(id).disabled=value;}
  $('close-instructions').addEventListener('click',()=>$('instructions-dialog').close());
  $('reload-instructions').addEventListener('click',async()=>{if(instructionsSaving)return;setInstructionsBusy(true);try{const saved=await api('/api/instructions');instructionsDraft=null;learnedDraft=null;await loadInstructionEditors(saved);$('instructions-error').textContent='';}catch(error){$('instructions-error').textContent=error.message;}finally{setInstructionsBusy(false);}});
  $('instructions-form').addEventListener('submit',async event=>{
    event.preventDefault();if(instructionsSaving)return;
    const [editor,learned]=await Promise.all([getRulesEditor(),getLearnedEditor()]);if(instructionsSaving)return;const markdown=editor.getMarkdown(),preferences=learned.getMarkdown();
    if(markdown.length>20000||preferences.length>20000){$('instructions-error').textContent='Keep each document under 20,000 characters.';return;}
    setInstructionsBusy(true);
    try{const saved=await api('/api/instructions',{markdown,expected_hash:instructionsBaseline,learned_preferences:{markdown:preferences,expected_hash:learnedBaseline}});instructionsBaseline=saved.expected_hash;learnedBaseline=saved.learned_preferences_hash;instructionsDraft=null;learnedDraft=null;$('instructions-dialog').close();toast('Instructions saved.');await refresh();}
    catch(error){$('instructions-error').textContent=error.message;}
    finally{setInstructionsBusy(false);}
  });
  workspaceMenu=window.SlideWorkspaceMenu.create({doc:document,view:window,projects:projectFiles,present:fromBeginning=>presentationLauncher.start({fromBeginning}),blocked:()=>presentationLauncher.isOpen()||elementEditor.isEditing()||elementEditor.isBusy()||feedbackSending||switchingDeck||visibilityBusy||slideActions.isBusy()||presentationActions.isBusy()});
  api('/api/instructions').then(()=>$('instructions-button').hidden=false).catch(()=>{});
  start().catch(error=>{toast(error.message,true);$('slide-title').textContent='Could not open the local presentation.';});presentationActions.refresh().catch(error=>toast(error.message,true));
})();
