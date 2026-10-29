(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const draftStore=window.SlideDrafts.create({getItem:key=>localStorage.getItem(key),setItem:(key,value)=>localStorage.setItem(key,value),removeItem:key=>localStorage.removeItem(key)});
  const drafts=draftStore.notes;
  const feedbackDrafts=draftStore.feedback;
  const feedbackDraft=()=>feedbackDrafts.get(deck?.id);
  const {svg,button:iconButton}=window.SlideIcons;
  for(const [id,icon,label,show] of [
    ['retry-preview','refresh','Retry loading design',false],['copy-start','copy','Copy agent prompt',true],['copy-agent-prompt','copy','Copy agent prompt',true],
    ['attach-brief','attach','Attach brief files',false],['toggle-design-chat','plus','Add a design',false],['attach-design','attach','Attach reference files',false],['send-design','send','Send design request',false],
    ['new-button','plus','New presentation',false],['connect-button','plug','Connect an agent',false],
    ['present-link','present','Present',true],['prev-slide','left','Previous slide',false],['next-slide','right','Next slide',false],
    ['save-notes','save','Save',true],['clear-target','close','Clear element selection',false],
    ['select-parent','group','Select surrounding group',false],['load-update','refresh','Show update',true],
    ['close-connect','close','Close',false],['copy-url','copy','Copy server URL',false],['close-new','close','Close',false],
    ['copy-live-setup','copy','Copy setup prompt',true],['end-review','close','End review',true],
    ['create-button','plus','Create presentation',true],['mode-live','live','Live',true],['mode-batch','batch','Batch',true]
  ])iconButton($(id),icon,label,show);
  $('hover-hint').innerHTML=svg('cursor')+' Click to comment';
  let deck=null, selectedId=null, pendingDeck=null, selectedElement=null, hoverElement=null, toastTimer;
  let currentTarget=null, noteBaseline='', previewKey='', railSignature='', agentNotesKey='', commentsKey='', commentsSignature='', isLoading=false, feedbackSending=false, draftWarning=false, switchingDeck=false;
  const key=()=>`${deck?.id}/${selectedId}`;
  const slide=()=>deck?.slides.find(item=>item.id===selectedId);
  const starting=()=>deck?.slides.length===1&&slide()?.id==='cover'&&slide()?.revision===1&&['Template draft. Replace with your presentation content.','Your brief is saved. Ready for the first draft.','Tell your agent who this is for and what it should say.'].includes(slide()?.agent.summary);
  const needsBrief=()=>starting()&&!deck.feedback?.length;
  const base=()=>`/api/decks/${encodeURIComponent(deck.id)}/slides/${encodeURIComponent(selectedId)}`;

  /** Report save and conflict results without interrupting the review flow. */
  function toast(message,error=false,action=null){clearTimeout(toastTimer);$('toast').textContent=message;if(action){const button=document.createElement('button');button.type='button';button.textContent=action.label;button.addEventListener('click',async()=>{button.disabled=true;try{await action.run();}catch(error){toast(error.message,true);}});$('toast').append(button);}$('toast').classList.toggle('error',error);$('toast').hidden=false;toastTimer=setTimeout(()=>$('toast').hidden=true,error||action?8000:3500);}
  /** Exchange JSON with the local server and surface actionable error messages. */
  async function api(url,body){const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const result=await response.json();if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail));return result;}
  /** Retain unsent per-slide feedback and notes when switching between slides. */
  function retainDraft(){
    if(!deck||!selectedId)return;
    const dirty=$('notes').value!==noteBaseline;
    drafts.set(key(),{notes:dirty?$('notes').value:undefined,expected_notes:dirty?noteBaseline:undefined});
    const previous=feedbackDraft(),value=$('feedback-text').value;
    const hasContext=previous?.feedback?.trim()||previous?.target;
    const unchanged=previous?.feedback===value&&JSON.stringify(previous?.target)===JSON.stringify(currentTarget);
    feedbackDrafts.set(deck.id,{feedback:value,target:currentTarget,slide_id:currentTarget?(previous?.slide_id||selectedId):null,context_slide_id:hasContext?previous.context_slide_id:selectedId,content_hash:hasContext?previous.content_hash:slide().content_hash,request_id:unchanged&&previous?.request_id?previous.request_id:crypto.randomUUID()});
    if(!draftStore.persist()&&!draftWarning){draftWarning=true;toast('Draft recovery is unavailable in this browser.',true);}
  }
  function updateComposer(){$('send-feedback').disabled=!deck||feedbackSending||!$('feedback-text').value.trim();presentationActions.update(feedbackSending||switchingDeck);}


  /** Resolve a source relative to the canonical presentation folder. */
  function sourceURL(url){return new URL(url,`${location.origin}/decks/${deck.id}/`).href;}
  /** Render the stable slide overview and its red/green review markers. */
  function renderRail(){const approved=deck.slides.filter(item=>item.approved).length;const progress=$('progress-text'),label=`${approved} of ${deck.slides.length} slide${deck.slides.length===1?'':'s'} approved`;progress.innerHTML=svg('check')+`<span>${approved} / ${deck.slides.length}</span>`;progress.title=label;progress.setAttribute('aria-label',label);$('progress-bar').style.width=`${approved/deck.slides.length*100}%`;
    const signature=JSON.stringify([deck.id,deck.slides.map(item=>[item.id,item.title,item.content_hash,item.approved])]);if(signature===railSignature){$('slides').querySelectorAll('[data-id]').forEach(button=>button.setAttribute('aria-current',String(button.dataset.id===selectedId)));return;}railSignature=signature;
    $('slides').innerHTML=deck.slides.map((item,index)=>`<button class="slide-thumb" data-id="${escape(item.id)}" aria-current="${item.id===selectedId}" aria-label="Slide ${index+1}: ${escape(item.title)} — ${item.approved?'Approved':'Needs review'}"><div class="thumb-preview"><iframe title="Thumbnail ${index+1}" tabindex="-1" loading="lazy" sandbox="allow-same-origin"></iframe></div><div class="thumb-caption"><span class="thumb-number">${String(index+1).padStart(2,'0')}</span><span class="thumb-title">${escape(item.title)}</span><span class="dot ${item.approved?'green':'red'}" aria-hidden="true"></span></div></button>`).join('');$('slides').querySelectorAll('iframe').forEach((frame,index)=>{loadPreview(frame,deck.slides[index],deck.id);});
  }
  /** Reflect the selected element in the comment composer. */
  function renderTarget(){const targeted=!!currentTarget,context=feedbackDraft();const original=deck.slides.find(item=>item.id===context?.slide_id);const otherSlide=targeted&&context?.slide_id!==selectedId;$('target-label').textContent=targeted?`${otherSlide?'Another slide · ':''}${currentTarget.label}${original&&context.content_hash!==original.content_hash?' · earlier version':''}`:'Presentation';$('target-chip').classList.toggle('has-target',targeted);$('clear-target').hidden=!targeted;$('select-parent').hidden=!selectedElement?.parentElement||selectedElement.parentElement.matches('body,html,.slide');$('feedback-text').placeholder=targeted?'What should change here?':needsBrief()?'Audience, takeaway, key points or links…':'Ask for a change…';}

  /** Place a highlight in slide-relative coordinates so it survives viewport resizing. */
  function placeBox(box,element){if(!element){box.hidden=true;return;}const rect=element.getBoundingClientRect(),frame=$('preview').contentDocument.documentElement;const width=frame.clientWidth,height=frame.clientHeight;box.hidden=false;box.style.left=`${rect.left/width*100}%`;box.style.top=`${rect.top/height*100}%`;box.style.width=`${rect.width/width*100}%`;box.style.height=`${rect.height/height*100}%`;}
  /** Use a stable element ID when present, with a structural selector as a fallback. */
  function selectorFor(element){if(element.id)return `#${CSS.escape(element.id)}`;if(element.dataset.elementId)return `[data-element-id="${CSS.escape(element.dataset.elementId)}"]`;const parts=[];let node=element;while(node&&node.tagName.toLowerCase()!=='body'){const tag=node.tagName.toLowerCase();const siblings=[...(node.parentElement?.children||[])].filter(sibling=>sibling.tagName===node.tagName);parts.unshift(`${tag}:nth-of-type(${siblings.indexOf(node)+1})`);node=node.parentElement;}return parts.join(' > ');}
  /** Give the highlighted item a short human-readable name. */
  function labelFor(element){const tag=element.tagName.toLowerCase();const types={h1:'Heading',h2:'Heading',h3:'Heading',p:'Text',span:'Text',img:'Image',svg:'Diagram',rect:'Box',circle:'Circle',path:'Shape',line:'Line',text:'Chart label',div:'Group',g:'Group'};const name=element.getAttribute('aria-label')||element.getAttribute('alt')||(element.innerText||element.textContent).trim().replace(/\s+/g,' ');return `${types[tag]||'Element'}${name?` · ${name.slice(0,95)}`:''}`.slice(0,200);}
  /** Capture the element and original geometry, never just an unstable screen pixel. */
  function selectElement(element,focus=true){selectedElement=element;hoverElement=null;$('hover-box').hidden=true;if(!element||element.matches('body,html,.slide')){currentTarget=null;selectedElement=null;$('selection-box').hidden=true;}else{const rect=element.getBoundingClientRect(),doc=$('preview').contentDocument.documentElement;const clamp=value=>Math.max(0,Math.min(1,value));currentTarget={selector:selectorFor(element),label:labelFor(element),quote:(element.innerText||element.textContent).trim().slice(0,2000),html:element.outerHTML.slice(0,15000),x:clamp(rect.left/doc.clientWidth),y:clamp(rect.top/doc.clientHeight),width:clamp(rect.width/doc.clientWidth),height:clamp(rect.height/doc.clientHeight)};placeBox($('selection-box'),element);}
    const previous=feedbackDraft()||{};feedbackDrafts.set(deck.id,{...previous,target:currentTarget,slide_id:currentTarget?selectedId:null,context_slide_id:selectedId,content_hash:slide().content_hash});retainDraft();renderTarget();if(focus)$('feedback-text').focus();
  }
  /** Install immediate hover and click targeting inside the script-free slide preview. */
  function bindPreview(){const doc=$('preview').contentDocument;if(!doc)return;doc.documentElement.style.cursor='crosshair';doc.addEventListener('pointermove',event=>{let target=event.target;if(!(target instanceof doc.defaultView.Element)||target.matches('html,body,.slide')){hoverElement=null;$('hover-box').hidden=true;return;}if(target.closest('svg')?.classList.contains('icon'))target=target.closest('svg');if(target===selectedElement){$('hover-box').hidden=true;return;}if(hoverElement===target)return;hoverElement=target;placeBox($('hover-box'),target);$('hover-box').querySelector('span').textContent=labelFor(target);});doc.documentElement.addEventListener('pointerleave',()=>{$('hover-box').hidden=true;hoverElement=null;});doc.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();let target=event.target;if(target.closest('svg')?.classList.contains('icon'))target=target.closest('svg');selectElement(target);});if(currentTarget&&feedbackDraft()?.slide_id===selectedId&&feedbackDraft()?.content_hash===slide().content_hash){try{selectedElement=doc.querySelector(currentTarget.selector);placeBox($('selection-box'),selectedElement);}catch{selectedElement=null;}renderTarget();}}
  /** Render discussion as text, including element anchors and original-version labels. */
  function renderWorkflow(){
    const state=deck.workflow||{mode:'live',pending:0},live=state.mode==='live',available=state.agent_waiting;
    const harness=({'codex':'Codex','claude-code':'Claude Code'})[state.review_harness];
    $('mode-live').setAttribute('aria-pressed',String(live));$('mode-batch').setAttribute('aria-pressed',String(!live));
    $('mode-live').innerHTML=svg('live')+'<span>Live <small class="live-availability '+(available?'available':'')+'">'+(available?'Available':'Unavailable')+'</small></span>';
    $('mode-live').title='Live feedback · '+(available?(harness||'agent')+' available':'agent unavailable');
    $('mode-live').setAttribute('aria-label','Live feedback: '+(available?(harness||'agent')+' available':'agent unavailable'));
    $('end-review').hidden=!state.review_connected;
    $('send-batch').hidden=!state.pending;iconButton($('send-batch'),'send',(live?'Send queued comments':'Send batch')+' ('+state.pending+')',true);
    iconButton($('send-feedback'),live?'send':'plus',live?'Send feedback':'Add to batch',false);$('send-feedback').title+=' (Enter · Shift+Enter for a new line)';
  }

  /** Keep each preview tied to its exact content snapshot while an agent revises the file. */
  function previewDocument(item,deckId,attempt=0){const origin=location.origin;return '<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src '+origin+' \'unsafe-inline\'; img-src '+origin+'; font-src '+origin+'; script-src \'none\'; base-uri '+origin+';"><base href="'+origin+'/decks/'+deckId+'/"><link rel="stylesheet" href="'+origin+'/decks/'+deckId+'/theme.css?v='+item.content_hash+'&retry='+attempt+'"><style>html,body{width:100%;height:100%;overflow:hidden}</style></head><body data-preview-key="'+item.content_hash+'">'+item.html+'</body></html>';}
  /** Reveal branded previews only after their design loads; retry transient failures once. */
  function loadPreview(frame,item,deckId,attempt=0){
    const token=crypto.randomUUID(),main=frame.id==='preview',parent=frame.parentElement;
    frame.dataset.loadToken=token;frame.style.visibility='hidden';parent.dataset.previewStatus='Loading design…';
    if(main){$('preview-status').hidden=false;$('preview-message').textContent='Loading design…';$('retry-preview').hidden=true;$('approve-button').disabled=true;}
    const fail=()=>{if(!frame.isConnected||frame.dataset.loadToken!==token)return;
      if(attempt===0){setTimeout(()=>{if(frame.isConnected&&frame.dataset.loadToken===token)loadPreview(frame,item,deckId,1);},500);return;}
      parent.dataset.previewStatus='Design unavailable';
      if(main){$('preview-message').textContent='Design couldn’t load.';$('retry-preview').hidden=false;}
    };
    const timeout=main?setTimeout(fail,10000):null;
    frame.onload=async()=>{
      const doc=frame.contentDocument;if(!doc||doc.body?.dataset.previewKey!==item.content_hash)return;
      await doc.fonts.ready;
      if(frame.dataset.loadToken!==token)return;
      clearTimeout(timeout);
      const stylesheet=doc.querySelector('link[rel="stylesheet"]');
      const failed=!stylesheet?.sheet?.cssRules.length||[...doc.images].some(image=>!image.naturalWidth)||[...doc.fonts].some(font=>font.status==='error');
      if(failed){fail();return;}
      frame.dataset.loadToken=token+'-ready';frame.style.visibility='visible';delete parent.dataset.previewStatus;
      if(main){$('preview-status').hidden=true;$('approve-button').disabled=false;bindPreview();}
    };
    frame.srcdoc=previewDocument(item,deckId,attempt);
  }
  /** Offer one concrete handoff when the first draft still needs an agent. */
  function renderStart(){
    const fresh=starting(),noBrief=needsBrief();
    $('start-help').hidden=!fresh||deck.workflow?.agent_waiting||deck.feedback?.some(item=>item.author==='agent');
    $('start-help-text').textContent=noBrief?'Add a brief below.':'Ask your agent to draft the slides.';
    if(noBrief){$('agent-summary').hidden=true;$('agent-panel').hidden=!slide().agent.alternatives.length&&!deck.brief_files?.length;}
    else if(fresh&&slide().agent.summary==='Template draft. Replace with your presentation content.')$('agent-summary').textContent='Brief saved.';
  }

  /** Keep questions visible and supporting explanations behind one disclosure. */
  function renderAgentContext(item){
    const context=item.agent;
    $('agent-panel').hidden=!context.summary&&!context.alternatives.length&&!deck.brief_files?.length;$('brief-attachments').innerHTML=(deck.brief_files||[]).map((file,index)=>`<a class="file-chip" href="/api/decks/${deck.id}/brief-files/${index}" download>${svg('attach')}${escape(file.name)}</a>`).join('');
    $('questions-panel').hidden=!context.questions.length;
    $('agent-summary').textContent=context.summary;
    $('agent-summary').hidden=!context.summary;
    $('questions').innerHTML=context.questions.map(question=>`<p class="question">${escape(question)}</p>`).join('');
    $('alternatives-panel').hidden=!context.alternatives.length;
    $('alternative-count').textContent=context.alternatives.length?`(${context.alternatives.length})`:'';
    $('alternatives').innerHTML=context.alternatives.map(value=>`<p class="alternative">${escape(value)}</p>`).join('');
    if(agentNotesKey!==key()){$('agent-panel').open=false;agentNotesKey=key();}
  }
  /** Preserve reading position while new messages arrive. */
  function renderComments(item){
    const comments=[...item.feedback,...(deck.feedback||[])].sort((a,b)=>a.event_id-b.event_id);
    $('feedback-count').textContent=comments.length?String(comments.length):'';
    const signature=JSON.stringify(comments),list=$('feedback-list'),changedContext=commentsKey!==key();
    if(!changedContext&&signature===commentsSignature)return;
    const atEnd=list.scrollHeight-list.scrollTop-list.clientHeight<35;
    commentsKey=key();commentsSignature=signature;
    list.innerHTML=comments.map(comment=>`<article class="comment ${comment.author}"><div class="comment-header"><strong>${comment.author==='user'?'You':'Agent'}${comment.reply_to?' · reply':''}${comment.scope==='presentation'?' · Presentation':''}</strong><time>${escape(new Date(comment.created_at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}))}</time></div>${comment.target?`<button type="button" class="comment-target" data-event="${comment.event_id}" title="${escape(comment.target.quote)}">↖ ${escape(comment.target.label)}</button>`:''}<p>${escape(comment.message)}</p>${!comment.released?'<span class="comment-pending">Not sent</span>':''}${comment.scope!=='presentation'&&comment.content_hash!==item.content_hash?'<span class="old-version">On an earlier version</span>':''}</article>`).join('')||'';
    if(changedContext||atEnd)list.scrollTop=list.scrollHeight;
  }

  /** Display one selected slide without losing unsaved per-slide work. */
  function render(){const item=slide();if(!item)return;document.querySelector('.workspace').classList.remove('is-empty');for(const id of ['mode-live','mode-batch'])$(id).disabled=false;const index=deck.slides.indexOf(item),draft=drafts.get(key()),commentDraft=feedbackDraft();$('slide-eyebrow').textContent=`${String(index+1).padStart(2,'0')} / ${String(deck.slides.length).padStart(2,'0')}`;$('slide-eyebrow').title=`Version ${item.revision}`;$('slide-title').textContent=item.title;iconButton($('approve-button'),'check',item.approved?'Approved':'Approve',true);$('approve-button').classList.toggle('is-approved',item.approved);$('approve-button').setAttribute('aria-pressed',String(item.approved));$('approve-button').title=item.approved?'Click to return this slide to needs review':'Approve this exact slide version';$('prev-slide').disabled=index===0;$('next-slide').disabled=index===deck.slides.length-1;$('present-link').href=`/decks/${deck.id}/index.html?present=1#${item.id}`;$('present-link').removeAttribute('aria-disabled');$('present-link').removeAttribute('tabindex');
    const nextKey=`${deck.id}/${item.id}/${item.content_hash}`;currentTarget=commentDraft?.target||null;if(previewKey!==nextKey){previewKey=nextKey;if(draft?.notes!==undefined)$('notes-panel').open=true;selectedElement=null;$('selection-box').hidden=true;$('hover-box').hidden=true;loadPreview($('preview'),item,deck.id);}
    noteBaseline=draft?.expected_notes??item.notes;if($('notes').value!==(draft?.notes??item.notes))$('notes').value=draft?.notes??item.notes;if($('feedback-text').value!==(commentDraft?.feedback||''))$('feedback-text').value=commentDraft?.feedback||'';notesDirty();renderTarget();renderAgentContext(item);$('source-count').textContent=item.sources.length;$('sources').innerHTML=item.sources.map(source=>source.url?`<a href="${escape(sourceURL(source.url))}" target="_blank" rel="noopener">${escape(source.label)} ↗</a>`:`<p>${escape(source.label)}</p>`).join('')||'<p>No sources.</p>';renderComments(item);renderWorkflow();renderStart();updateComposer();document.title=`${deck.title} · Slide Studio`;history.replaceState(null,'',`?deck=${encodeURIComponent(deck.id)}#${encodeURIComponent(selectedId)}`);}
  /** Choose a slide and restore any draft feedback attached to it. */
  function choose(id){retainDraft();selectedId=id;renderRail();render();$('slides').querySelector('[aria-current="true"]')?.scrollIntoView({block:'nearest'});}
  /** Poll durable state while preserving a user's in-progress review of an older version. */
  async function refresh(force=false){if(!deck||isLoading||switchingDeck||presentationActions.isBusy())return;isLoading=true;try{const deckId=deck.id,snapshot=await api(`/api/decks/${deckId}`);if(deck?.id!==deckId||switchingDeck||presentationActions.isBusy())return;if(JSON.stringify(snapshot)===JSON.stringify(deck)){renderWorkflow();renderStart();return;}const contentChanged=snapshot.slides.find(item=>item.id===selectedId)?.content_hash!==slide()?.content_hash;const editing=currentTarget||$('notes').value!==noteBaseline;if(contentChanged&&editing&&!force){pendingDeck=snapshot;deck.workflow=snapshot.workflow;renderWorkflow();$('update-banner').hidden=false;return;}retainDraft();deck=snapshot;if(!deck.slides.some(item=>item.id===selectedId))selectedId=deck.slides[0].id;renderRail();render();}catch(error){toast(error.message,true);}finally{isLoading=false;}}
  /** Save notes explicitly, detecting another editor's changes. */
  async function saveNotes(){const value=$('notes').value,deckId=deck.id,id=selectedId;try{await api(`${base()}/notes`,{notes:value,expected_notes:noteBaseline});if(deck.id===deckId&&selectedId===id){noteBaseline=value;slide().notes=value;retainDraft();notesDirty();}toast('Notes saved.');}catch(error){toast(error.message,true);}}
  function notesDirty(){$('save-notes').disabled=$('notes').value===noteBaseline;$('notes-state').textContent=$('notes').value===noteBaseline?'':'Unsaved';}
  $('retry-preview').addEventListener('click',()=>{loadPreview($('preview'),slide(),deck.id);railSignature='';renderRail();});
  async function copyAgentPrompt(){
    const prompt=deck?`Work with presentation "${deck.title}" in Slide Studio (deck ID: ${deck.id}). Read get_working_context, get_presentation and read_feedback. Use the saved brief, preserve my notes, and address my released feedback. Ask about missing context before drafting. Use concise text and useful visuals. For automatic review, follow live_review_guide and call start_live_review with this deck ID after hooks are loaded. Workspace: ${location.origin}/?deck=${deck.id}`:`Help me add a design in Slide Studio at ${location.origin}. Call get_working_context and read_design_requests. Inspect the reference I provide, prepare and register a design pack, then help me create a presentation from a short brief. For automatic review, follow live_review_guide and call start_live_review after hooks are loaded.`;
    try{await navigator.clipboard.writeText(prompt);toast('Prompt copied.');}catch{toast('Copy is unavailable. Ask your agent to open Slide Studio and call get_working_context.',true);}
  }
  $('copy-start').addEventListener('click',copyAgentPrompt);
  $('copy-agent-prompt').addEventListener('click',copyAgentPrompt);
  $('copy-live-setup').addEventListener('click',async()=>{
    const prompt=`Set up automatic Slide Studio feedback for this agent harness at ${location.origin}. Call get_working_context and read live_review_guide. Configure the supported harness's project-local lifecycle hooks in your own working project with studio.hook_client configure, using app_path from that context and the actual MCP server name. Preserve existing settings. Follow native hook trust/reload requirements; do not bypass them or claim availability before they are loaded. Then call start_live_review(${deck?`deck_id="${deck.id}"`:''}), read the presentation if selected and read the feedback/design inbox. Keep this review session open and address released feedback, preserving my notes and visually checking changes.`;
    try{await navigator.clipboard.writeText(prompt);toast('Setup prompt copied.');}catch{toast('Copy is unavailable.',true);}
  });
  $('end-review').addEventListener('click',async()=>{if(!deck)return;const selected=deck.id;$('end-review').disabled=true;try{const state=await api(`/api/decks/${selected}/review/disconnect`,{});if(deck?.id===selected){deck.workflow=state;renderWorkflow();}toast('Review ended.');}catch(error){toast(error.message,true);}finally{$('end-review').disabled=false;}});
  $('slides').addEventListener('click',event=>{const button=event.target.closest('[data-id]');if(button)choose(button.dataset.id);});
  $('prev-slide').addEventListener('click',()=>choose(deck.slides[Math.max(0,deck.slides.findIndex(item=>item.id===selectedId)-1)].id));$('next-slide').addEventListener('click',()=>choose(deck.slides[Math.min(deck.slides.length-1,deck.slides.findIndex(item=>item.id===selectedId)+1)].id));
  $('clear-target').addEventListener('click',()=>selectElement(null));$('select-parent').addEventListener('click',()=>selectElement(selectedElement?.parentElement));
  $('notes').addEventListener('input',()=>{notesDirty();retainDraft();});$('save-notes').addEventListener('click',saveNotes);$('feedback-text').addEventListener('input',()=>{retainDraft();updateComposer();});
  /** Send chat messages with Enter while preserving multiline and composition input. */
  function bindMessageEnter(inputId,buttonId,send){$(inputId).addEventListener('keydown',event=>{if(event.key!=='Enter'||event.shiftKey||event.altKey||event.isComposing||event.keyCode===229)return;event.preventDefault();if(!event.repeat&&!$(buttonId).disabled)send();});}
  bindMessageEnter('feedback-text','send-feedback',()=>$('feedback-form').requestSubmit());
  $('feedback-form').addEventListener('submit',async event=>{event.preventDefault();if(!deck||feedbackSending||!$('feedback-text').value.trim())return;retainDraft();const button=$('send-feedback'),sendingDeck=deck.id,context=feedbackDraft(),message=$('feedback-text').value;feedbackSending=true;updateComposer();$('feedback-text').disabled=true;try{const endpoint=context.target?`/api/decks/${sendingDeck}/slides/${context.slide_id}/feedback`:`/api/decks/${sendingDeck}/feedback`;const body=context.target?{message,content_hash:context.content_hash,target:context.target,request_id:context.request_id}:{message,context_slide_id:context.context_slide_id,request_id:context.request_id};await api(endpoint,body);feedbackDrafts.delete(sendingDeck);if(deck.id===sendingDeck){$('feedback-text').value='';selectElement(null,false);}await refresh();toast(deck.workflow?.mode==='batch'?'Added to batch.':deck.workflow?.agent_waiting?'Sent to agent.':'Saved for agent.');}catch(error){toast(error.message,true);}finally{feedbackSending=false;$('feedback-text').disabled=false;updateComposer();if(deck?.id===sendingDeck&&!document.querySelector('dialog[open]')&&[document.body,button].includes(document.activeElement))$('feedback-text').focus();}});

  $('approve-button').addEventListener('click',async()=>{try{await api(`${base()}/approve`,{content_hash:slide().content_hash,approved:!slide().approved});await refresh();}catch(error){toast(error.message,true);await refresh();}});
  $('feedback-list').addEventListener('click',event=>{const button=event.target.closest('[data-event]');if(!button)return;const comment=slide().feedback.find(item=>item.event_id===Number(button.dataset.event));if(comment.content_hash!==slide().content_hash){toast(`This comment targets an earlier version: ${comment.target.quote||comment.target.label}`);return;}const element=$('preview').contentDocument.querySelector(comment.target.selector);if(element)selectElement(element);else toast('The original element is no longer present.');});
  $('load-update').addEventListener('click',()=>{if(!pendingDeck)return;retainDraft();const draft=feedbackDraft();if(draft&&draft.target){toast('Draft kept on its original version. Select an element again to retarget it.');}deck=pendingDeck;pendingDeck=null;$('update-banner').hidden=true;if(!slide())selectedId=deck.slides[0].id;renderRail();render();});
  $('deck-picker').addEventListener('change',async event=>{retainDraft();try{await start(event.target.value);}catch(error){if(deck)$('deck-picker').value=deck.id;toast(error.message,true);}});
  for(const mode of ['live','batch'])$('mode-'+mode).addEventListener('click',async()=>{try{deck.workflow=await api('/api/decks/'+deck.id+'/feedback-mode',{mode});renderWorkflow();}catch(error){toast(error.message,true);}});
  $('send-batch').addEventListener('click',async()=>{try{deck.workflow=await api('/api/decks/'+deck.id+'/send-batch',{});renderWorkflow();await refresh();toast('Batch released.');}catch(error){toast(error.message,true);}});
  const attachmentFiles={design:[],brief:[]};
  let designSignature='',inboxSignature='',designTimer=null,designRequestId=null;
  /** Refresh the library without resetting the user's selected brand or draft message. */
  async function loadDesigns(reset=false){const [designs,preferences]=await Promise.all([api('/api/designs'),api('/api/preferences')]);const defaultId=preferences.default_design_id;const signature=JSON.stringify([designs,defaultId]);if(!reset&&signature===designSignature)return;const selected=reset?defaultId:document.querySelector('input[name="design"]:checked')?.value||defaultId;designSignature=signature;designs.sort((a,b)=>(a.id===defaultId?-1:b.id===defaultId?1:a.name.localeCompare(b.name)));$('design-options').innerHTML=designs.length?designs.map(design=>`<label class="design-card"><input type="radio" name="design" value="${escape(design.id)}" ${design.id===selected?'checked':''} required><span class="design-preview"><iframe title="${escape(design.name)} design example" src="/api/designs/${encodeURIComponent(design.id)}/preview" tabindex="-1" sandbox></iframe></span><strong title="${escape(design.description)}">${escape(design.name)}</strong></label>`).join(''):'<p class="library-help">Send a PowerPoint, image or link below to add a design.</p>';if(!document.querySelector('input[name="design"]:checked'))document.querySelector('input[name="design"]')?.click();}
  /** Display durable agent replies and attachment links beside the design choices. */
  async function loadDesignChat(){const inbox=await api('/api/design-requests');$('design-availability').textContent=inbox.agent_waiting?'Available':'Unavailable';$('design-availability').setAttribute('aria-label',inbox.agent_waiting?'Agent available':'Agent unavailable');$('design-availability').classList.toggle('available',inbox.agent_waiting);const signature=JSON.stringify(inbox.messages);if(signature!==inboxSignature){inboxSignature=signature;const list=$('design-messages');const atBottom=list.scrollHeight-list.scrollTop-list.clientHeight<35;list.innerHTML=inbox.messages.map(item=>`<article class="design-message ${item.author}"><strong>${item.author==='user'?'You':'Agent'}</strong><p>${escape(item.message)}</p>${item.files.map((file,index)=>`<a class="file-chip" href="/api/design-requests/${item.event_id}/files/${index}" download>${svg('attach')}${escape(file.name)}</a>`).join('')}${item.author==='user'?`<span class="request-status">${({queued:'Queued',working:'Working',needs_input:'Needs your input',ready:'Ready'})[item.status]}</span>`:''}${item.design_id&&item.author==='agent'?`<button class="use-design" type="button" data-use-design="${escape(item.design_id)}">${svg('check')}Use this design</button>`:''}</article>`).join('');if(atBottom)list.scrollTop=list.scrollHeight;}await loadDesigns();}
  /** Render removable files identically for brief and design references. */
  function renderFiles(kind){$(kind+'-files').innerHTML=attachmentFiles[kind].map((file,index)=>`<span class="file-chip">${svg('attach')}${escape(file.name)}<button type="button" data-remove-file="${index}" aria-label="Remove ${escape(file.name)}" title="Remove ${escape(file.name)}">${svg('close')}</button></span>`).join('');}
  /** Encode selected local references for the shared attachment API. */
  function readFiles(files){return Promise.all(files.map(file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve({name:file.name,data_base64:String(reader.result).split(',')[1]});reader.onerror=()=>reject(new Error('Could not read '+file.name));reader.readAsDataURL(file);})) );}
  function toggleDesignChat(open){$('design-chat').hidden=!open;$('toggle-design-chat').setAttribute('aria-expanded',String(open));iconButton($('toggle-design-chat'),open?'close':'plus',open?'Close design chat':'Add a design',false);if(open)$('design-message').focus();}
  $('toggle-design-chat').addEventListener('click',()=>toggleDesignChat($('design-chat').hidden));
  $('new-button').addEventListener('click',async()=>{try{await loadDesigns(true);await loadDesignChat();$('create-error').textContent='';toggleDesignChat(false);$('new-dialog').showModal();$('new-title').focus();clearInterval(designTimer);designTimer=setInterval(()=>{if($('new-dialog').open)loadDesignChat().catch(error=>$('design-error').textContent=error.message);},2000);}catch(error){toast(error.message,true);}});
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
  window.addEventListener('resize',()=>{placeBox($('selection-box'),selectedElement);placeBox($('hover-box'),hoverElement);});
  window.addEventListener('beforeunload',event=>{retainDraft();if($('new-title').value.trim()||$('new-brief').value.trim()||$('design-message').value.trim()||attachmentFiles.design.length||attachmentFiles.brief.length||[...feedbackDrafts.values()].some(draft=>draft.feedback.trim())||[...drafts.values()].some(draft=>draft.notes!==undefined&&draft.notes!==draft.expected_notes)){event.preventDefault();event.returnValue='';}});
  document.addEventListener('keydown',event=>{if(!deck||event.target.closest('textarea,input,select,dialog'))return;if(event.key==='ArrowLeft')$('prev-slide').click();if(event.key==='ArrowRight')$('next-slide').click();if(event.key==='Escape')selectElement(null,false);});
  /** Clear stale slide content when the last selector entry is removed. */
  function renderEmpty(){$('deck-picker').selectedIndex=-1;deck=null;selectedId=null;pendingDeck=null;currentTarget=null;selectedElement=null;hoverElement=null;previewKey='';railSignature='';noteBaseline='';document.querySelector('.workspace').classList.add('is-empty');$('slides').replaceChildren();$('progress-text').replaceChildren();$('progress-text').removeAttribute('aria-label');$('progress-text').removeAttribute('title');$('progress-bar').style.width='0%';$('preview').onload=null;$('preview').dataset.loadToken='';$('preview').srcdoc='';$('notes').value='';$('feedback-text').value='';$('update-banner').hidden=true;$('slide-title').textContent=$('deck-picker').options.length?'Choose a presentation':'No presentations';$('slide-eyebrow').textContent=$('deck-picker').options.length?'':'Create a presentation to begin.';$('present-link').removeAttribute('href');$('present-link').setAttribute('aria-disabled','true');$('present-link').tabIndex=-1;for(const id of ['approve-button','prev-slide','next-slide','mode-live','mode-batch'])$(id).disabled=true;updateComposer();document.title='Slide Studio';history.replaceState(null,'','/');}
  /** Rebuild the selector while keeping a requested or still-visible presentation selected. */
  async function start(preferred=null){switchingDeck=true;updateComposer();try{const items=await api('/api/decks');$('deck-picker').innerHTML=items.map(item=>`<option value="${escape(item.id)}">${escape(item.title)}</option>`).join('');if(!deck)$('deck-picker').selectedIndex=-1;if(!items.length){renderEmpty();return;}const requested=preferred||deck?.id||new URLSearchParams(location.search).get('deck');const id=items.some(item=>item.id===requested)?requested:items[0].id;const snapshot=await api(`/api/decks/${id}`);$('deck-picker').value=id;deck=snapshot;selectedId=deck.slides.some(item=>item.id===decodeURIComponent(location.hash.slice(1)))?decodeURIComponent(location.hash.slice(1)):deck.slides[0].id;pendingDeck=null;$('update-banner').hidden=true;renderRail();render();}finally{switchingDeck=false;updateComposer();}}
  const presentationActions=window.SlidePresentations.init({api,toast,escape,currentDeck:()=>deck,retainDraft,reload:start,forget:id=>{$('deck-picker').querySelector(`option[value="${CSS.escape(id)}"]`)?.remove();if(deck?.id===id)renderEmpty();},recover:item=>{if(![...$('deck-picker').options].some(option=>option.value===item.id))$('deck-picker').add(new Option(item.title,item.id));if(!deck)renderEmpty();}});
  window.SlideLibraries.init({api,toast,escape,currentDeck:()=>deck});
  start().catch(error=>{toast(error.message,true);$('slide-title').textContent='Could not open the local presentation.';});presentationActions.refresh().catch(error=>toast(error.message,true));
  setInterval(()=>refresh(),3500);
})();
