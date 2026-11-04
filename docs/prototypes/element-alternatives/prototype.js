/* In-memory UX draft only. No collection, MCP or production APIs are accessed. */
(() => {
  'use strict';
  const doc=document,$=id=>doc.getElementById(id),escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icon=name=>window.SlideIcons.svg(({designs:'slides',layers:'batch',compare:'batch',live:'live'})[name]||name);
  doc.querySelectorAll('[data-icon]').forEach(node=>node.innerHTML=icon(node.dataset.icon));
  const groups={
    headline:{label:'Headline',glyph:'T',kind:'headline',options:[
      {name:'Original',text:'Good slides happen in the loop.'},
      {name:'Clearer promise',text:'A stronger story, every review.'},
      {name:'More direct',text:'From first thought to a clear story.'},
    ]},
    text:{label:'Text',glyph:'≡',kind:'text',options:[
      {name:'Original',text:'Bring your ideas. Let your agent draft. Refine together, in context.'},
      {name:'Action led',text:'Brief together. Explore a direction. Point to what needs to change.'},
      {name:'More concise',text:'One shared brief. A few directions. A clearer result.'},
    ]},
    graphic:{label:'Process graphic',glyph:'◇',kind:'graphic',options:[
      {name:'Original',text:'Simple flow',variant:'flow'},
      {name:'Continuous loop',text:'Continuous loop',variant:'orbit'},
      {name:'Clear sequence',text:'Clear sequence',variant:'stack'},
    ]},
    slide:{label:'Slide designs',glyph:'▱',kind:'slide',options:[
      {name:'Original',text:'Original design',variant:'original'},
      {name:'Editorial',text:'Editorial design',variant:'editorial'},
      {name:'Bold',text:'Bold design',variant:'bold'},
    ]},
  };
  const initial={current:{headline:0,text:0,graphic:0,slide:0},pending:{headline:true,text:true,graphic:true,slide:true},stale:{headline:false,text:false,graphic:false,slide:false},history:[]};
  let state=structuredClone(initial),scope='headline',selected=1,trayOpen=true,previous=null,left=0,right=1,view='element',menuOpener=null,compareOpener=null,draftContext=null;
  const published={headline:structuredClone(initial.current),text:structuredClone(initial.current),graphic:structuredClone(initial.current),slide:structuredClone(initial.current)};
  let inlineOpen=false,pinned=null,inlineAnchor=null,openedByHover=false,hoverTimer=null;
  let question=new URLSearchParams(location.search).get('scenario')==='question',waitingForAgent=false,answeredAudience=null;
  if(question){selected=0;trayOpen=false;for(const key of Object.keys(state.pending))state.pending[key]=false;}
  const announce=text=>$('status').textContent=text;
  function graphic(variant){return `<div class="graphic-content graphic-${variant}"><div class="node"><small>01</small><strong>Brief</strong></div>${variant==='flow'?'<span>→</span>':''}<div class="node"><small>02</small><strong>Draft</strong></div>${variant==='flow'?'<span>→</span>':''}<div class="node"><small>03</small><strong>Review</strong></div>${variant==='orbit'?'<span>↻</span>':''}</div>`;}
  function fragment(key,index){const item=groups[key].options[index];return key==='graphic'?graphic(item.variant):escape(item.text);}
  function renderSlide({preview=true,interactive=false,choice=selected,context=state.current}={}){
    const values={...context};if(preview)values[scope]=choice;
    const variant=groups.slide.options[values.slide].variant;
    const target=(key,html)=>`<div class="element-target ${preview&&key===scope?'is-selected':''}" ${interactive?`data-scope="${key}" role="button" tabindex="0" aria-label="${escape(groups[key].label+': '+groups[key].options[values[key]].text)}"`:''}>${html}</div>`;
    return `<div class="fixed-canvas"><article class="slide-art component-slide variant-${variant}"><p class="slide-eyebrow">From first thought to final slide</p>${target('headline',`<h2>${fragment('headline',values.headline)}</h2>`)}<div class="component-body">${target('text',`<p class="body-copy">${fragment('text',values.text)}</p>`)}${target('graphic',fragment('graphic',values.graphic))}</div><footer class="slide-footer"><span>Artifact Studio / A clearer way to work</span><span>02</span></footer></article></div>`;
  }
  function renderRail(){
    const title=['A clearer way to work','Good slides happen in the loop','One slide. One clear idea.','Let the story move','Ready for the room','Space for the details'];
    $('slide-overview').innerHTML=title.map((name,index)=>`${index===0?'<div class="draft-section-label">The story <span>3</span></div>':''}<div class="draft-slide-entry"><button class="draft-slide-thumb" data-rail-slide="${index}" ${index===1?'aria-current="true"':''} aria-label="${escape(name)}"><div class="rail-slide-image" aria-hidden="true">${index===1?renderSlide({preview:false}):`<article class="slide-art ${index===0||index===4?'cover':'simple'}"><p class="slide-eyebrow">A clearer way to work</p><h2>${name}</h2></article>`}</div><span class="rail-slide-caption"><span class="rail-slide-number" aria-hidden="true">0${index+1}</span><span>${escape(name)}</span><span class="dot ${index===0?'green':'red'}" aria-hidden="true"></span></span></button>${index===1&&state.pending.slide?`<button class="rail-alternatives" data-open-scope="slide" aria-label="View 2 alternative slide designs">${icon('designs')}2</button>`:''}</div>`).join('');
  }
  function renderMenu(){
    const rows=(keys)=>keys.filter(key=>state.pending[key]).map(key=>`<button type="button" role="menuitem" data-open-scope="${key}" aria-current="${key===scope}"><span class="menu-icon" aria-hidden="true">${groups[key].glyph}</span>${groups[key].label}<small>${state.stale[key]?'Earlier design':'2 options'}</small></button>`).join('');
    $('scope-menu').innerHTML=`<h2 role="presentation">This slide</h2>${rows(['slide'])||'<span class="menu-empty">No new slide designs</span>'}<h2 role="presentation">Elements</h2>${rows(['headline','text','graphic'])||'<span class="menu-empty">All choices saved</span>'}<button type="button" role="menuitem" class="history-link" data-open-history>${icon('history')}History &amp; saved options</button>`;
    const pending=Object.values(state.pending).some(Boolean);$('options').hidden=!pending;$('options').querySelector('.pending-dot').hidden=!pending;
  }
  function contextLabel(){return trayOpen&&selected!==state.current[scope]?`${groups[scope].label} · ${groups[scope].options[selected].name}`:'';}
  function renderFeedback(){const label=draftContext??contextLabel();$('feedback-scope').textContent=label;$('feedback-scope').hidden=!label;}
  function renderMessages(){
    const audienceScenario=question||waitingForAgent||answeredAudience;
    const userText=audienceScenario?'Rewrite the headline for our new audience.':'Keep the story. Explore the headline, supporting text and process graphic.';
    let body='';
    if(question)body='<p>One detail before I write the options.</p><div class="draft-question"><p>Who is this slide for?</p><button data-answer="Leadership">Leadership</button><button data-answer="Project team">Project team</button><small>Or write your own answer below.</small></div>';
    else if(waitingForAgent)body='<p>Your answer is saved for the agent.</p>';
    else{
      const intro=state.pending.headline?(answeredAudience?'Two headline options for '+answeredAudience.toLowerCase()+'.':'Two headline options, with the same message. The text and graphic have their own options.'):Object.values(state.pending).some(Boolean)?'Headline choice saved. Your other suggestions stay available.':'Your choice is saved. Earlier options are in History.';
      const headline=state.pending.headline?'<button class="element-card" data-open-scope="headline"><span>Headline <span>2 options →</span></span><small>Clearer promise · More direct</small></button>':state.history.some(item=>item.scope==='headline')?`<button class="element-card settled" data-open-history>${icon('check')}Headline ${state.current.headline?'updated':'kept'}${icon('right')}</button>`:'';
      const graphicCard=state.pending.graphic?'<button class="element-card" data-open-scope="graphic"><span>Process graphic <span>2 options →</span></span></button>':'';
      const saved=!Object.values(state.pending).some(Boolean)&&state.history.length?`<button class="element-card settled" data-open-history>${icon('check')}Saved options${icon('right')}</button>`:'';body=`<p>${intro}</p>${saved||headline}${graphicCard}`;
    }
    $('messages').innerHTML=`<article class="draft-message user"><header><time>10:42</time><span class="draft-avatar" role="img" aria-label="You">${icon('user')}</span></header><p>${userText}</p></article><article class="draft-message agent"><header><span class="draft-avatar agent-avatar" role="img" aria-label="Agent">${icon('robot')}</span><time>10:43</time></header>${body}</article>`;
  }
  function renderOptions(){
    const group=groups[scope];$('element-options').setAttribute('aria-label',group.label+' options');
    $('element-options').innerHTML=group.options.map((item,index)=>`<button class="element-option kind-${group.kind}" role="tab" id="option-${index}" aria-selected="${index===selected}" aria-controls="slide-stage" tabindex="${index===selected?'0':'-1'}" data-option="${index}" data-chosen="${index===state.current[scope]}" aria-label="${escape(item.name+': '+item.text+(index===state.current[scope]?', chosen option':''))}"><span class="option-content">${scope==='slide'?`<span class="mini-slide">${renderSlide({choice:index,interactive:false})}</span>`:fragment(scope,index)}</span><span class="option-name">${escape(item.name)}</span>${index===state.current[scope]?`<span class="chosen-mark" aria-hidden="true">${icon('check')}</span>`:''}</button>`).join('');
  }
  function render(){
    const current=selected===state.current[scope],ctx=state.stale[scope]&&trayOpen?published[scope]:state.current;
    $('slide-stage').innerHTML=renderSlide({preview:trayOpen,interactive:true,context:ctx})+(state.stale[scope]&&trayOpen?'<span class="canvas-context" role="status">Earlier design · saved preview</span>':'');
    $('slide-stage').setAttribute('role',trayOpen?'tabpanel':'region');
    if(trayOpen){$('slide-stage').setAttribute('aria-labelledby',`${inlineOpen?'inline-option':'option'}-${selected}`);$('slide-stage').removeAttribute('aria-label');}else{$('slide-stage').removeAttribute('aria-labelledby');$('slide-stage').setAttribute('aria-label','Chosen slide');}
    $('component-tray').hidden=!trayOpen||inlineOpen;$('preview-state').textContent=current?'✓':'Preview';$('preview-state').classList.toggle('is-current',current);$('preview-state').setAttribute('aria-label',current?'Chosen option':'Preview');
    $('preview-name').textContent=groups[scope].options[selected].name;$('scope-label').textContent=groups[scope].label;$('scope-count').textContent='2 options';
    $('use').innerHTML=`Use ${scope==='slide'?'this design':scope==='graphic'?'graphic':scope}${icon('right')}`;$('use').hidden=current;$('use').disabled=state.stale[scope];
    $('keep').textContent=state.pending[scope]?'Keep current':'Back to current';
    $('context-change').hidden=!state.stale[scope];$('context-change').innerHTML='These options belong to the earlier slide design. <button type="button" id="refresh-options">Ask to refresh</button>';
    renderOptions();renderRail();renderMenu();renderMessages();renderFeedback();
    for(const node of doc.querySelectorAll('[data-rail-slide],[data-scope],[data-option]')){
      const label=node.textContent.replace(/\s+/g,' ').trim();
      node.setAttribute('aria-label',label+(node.dataset.chosen==='true'?', chosen option':''));
    }
    if($('comparison').open)renderComparison();fitCanvases();renderInline();
  }
  function openScope(key,{byHover=false,focus=true}={}){
    if(!groups[key])return;
    if(inlineOpen&&scope===key){if(!byHover)openedByHover=false;$('scope-menu').hidePopover();return;}
    scope=key;selected=state.current[key];pinned=null;trayOpen=true;inlineOpen=key!=='slide';openedByHover=byHover;inlineAnchor=null;view=key==='slide'?'slide':'element';$('scope-menu').hidePopover();render();
    if(focus&&!byHover)(inlineOpen?$(`inline-option-${selected}`):$(`option-${selected}`))?.focus({preventScroll:true});announce(groups[key].label+' options. The slide is unchanged.');
  }
  function selectElement(key){
    if(state.pending[key]){openScope(key);return;}
    scope=key;selected=state.current[key];trayOpen=false;inlineOpen=false;pinned=null;render();$('slide-stage').querySelector(`[data-scope="${key}"]`).classList.add('is-selected');announce(groups[key].label+' selected. Saved options are in History.');
  }
  function preview(index,{focus=false}={}){selected=index;trayOpen=true;render();if(focus)$(`option-${index}`).focus({preventScroll:true});announce(groups[scope].options[index].name+' preview. The slide is unchanged.');}
  function decide(index){
    if(state.stale[scope]&&index!==state.current[scope])return;
    previous=structuredClone(state);const changed=index!==state.current[scope];state.current[scope]=index;state.pending[scope]=false;
    state.history.push({scope,index,action:changed?'updated':'kept',name:groups[scope].options[index].name});
    if(scope==='slide'&&changed)for(const key of ['headline','text','graphic']){state.stale[key]=true;state.pending[key]=false;}
    selected=index;trayOpen=false;inlineOpen=false;pinned=null;$('comparison').close();render();$('toast-message').textContent=`${groups[scope].label} ${changed?'updated':'kept'}. Options saved.`;$('toast').hidden=false;($('options').hidden?$('history'):$('options')).focus({preventScroll:true});
  }
  function showMenu(opener){menuOpener=opener;renderMenu();const menu=$('scope-menu'),box=opener.getBoundingClientRect();if(menu.matches(':popover-open')){menu.hidePopover();return;}menu.showPopover();menu.style.left=`${Math.max(8,Math.min(box.left,innerWidth-menu.offsetWidth-8))}px`;menu.style.top=`${Math.max(8,Math.min(box.bottom+8,innerHeight-menu.offsetHeight-8))}px`;menu.querySelector('button').focus({preventScroll:true});}
  function openComparison(){left=state.current[scope];right=selected===left?(left+1)%groups[scope].options.length:selected;compareOpener=doc.activeElement;$('comparison').showModal();renderComparison();}
  function renderComparison(){
    const group=groups[scope];$('comparison-title').textContent=`Compare ${scope==='slide'?'designs':scope==='graphic'?'graphic':scope}${state.stale[scope]?' · Earlier design':''}`;
    for(const [side,index] of [['left',left],['right',right]]){
      $(side+'-option').innerHTML=group.options.map((item,i)=>`<option value="${i}" ${i===index?'selected':''}>${escape(item.name)}</option>`).join('');
      const chosen=index===state.current[scope],frame=$(side+'-preview');$(side+'-chosen').innerHTML=chosen?`<span role="img" aria-label="Chosen option">${icon('check')}</span>`:'';
      frame.className=`component-comparison-frame kind-${group.kind} ${view==='slide'?'full-slide':''}`;frame.dataset.chosen=String(chosen);
      frame.innerHTML=view==='slide'?renderSlide({choice:index,interactive:false,context:state.stale[scope]?published[scope]:state.current}):`<div class="copy-detail">${fragment(scope,index)}</div>`;
      $('use-'+side).textContent=`${chosen?'Keep':'Use'} ${group.options[index].name}`;$('use-'+side).disabled=state.stale[scope]&&!chosen;
    }
    doc.querySelectorAll('[data-view]').forEach(button=>{button.setAttribute('aria-pressed',String(button.dataset.view===view));button.disabled=scope==='slide'&&button.dataset.view==='element';});
    $('compare-keep').hidden=left===state.current[scope]||right===state.current[scope];fitCanvases();
  }
  function openHistory(){
    const rows=state.history.length?state.history.map(item=>`<p>${icon('check')}<strong>${groups[item.scope].label}</strong> ${escape(item.name)} · ${item.action}</p>`).join(''):'<p>No decisions yet.</p>';
    $('history-content').innerHTML=rows+`<details><summary>Saved element options</summary>${['headline','text','graphic','slide'].map(key=>`<details><summary>${groups[key].label}${state.stale[key]?' · Earlier design':''}</summary><div class="saved-elements">${groups[key].options.map((item,index)=>`<button class="saved-element kind-${groups[key].kind}" data-saved-scope="${key}" data-saved-index="${index}" data-chosen="${index===state.current[key]}" aria-label="Preview saved ${escape(groups[key].label+' '+item.name)}"><span class="saved-visual">${key==='graphic'?graphic(item.variant):escape(item.text)}</span><strong>${item.name}</strong>${index===state.current[key]?`<span class="chosen-mark" aria-hidden="true">${icon('check')}</span>`:''}</button>`).join('')}</div></details>`).join('')}</details>`;
    $('history-dialog').showModal();for(const button of $('history-dialog').querySelectorAll('.saved-element'))button.setAttribute('aria-label',button.textContent.replace(/\s+/g,' ').trim()+' · Preview saved '+groups[button.dataset.savedScope].label);
  }
  doc.addEventListener('click',event=>{
    const item=event.target.closest('[data-open-scope]'),target=event.target.closest('[data-scope]'),option=event.target.closest('[data-option]'),saved=event.target.closest('[data-saved-scope]');
    if(item)openScope(item.dataset.openScope);else if(target)selectElement(target.dataset.scope);else if(option)preview(Number(option.dataset.option),{focus:true});else if(saved){$('history-dialog').close();openScope(saved.dataset.savedScope);preview(Number(saved.dataset.savedIndex),{focus:true});}else if(event.target.closest('[data-open-history]')){$('scope-menu').hidePopover();openHistory();}
    const answer=event.target.closest('[data-answer]');if(answer)answerQuestion(answer.dataset.answer);
    if(event.target.closest('#refresh-options')){$('draft-message').value=`Please refresh the ${groups[scope].label.toLowerCase()} options for the current slide design.`;draftContext='';renderFeedback();doc.querySelector('.draft-workspace').classList.add('chat-open');$('draft-message').focus();}
  });
  $('slide-stage').addEventListener('keydown',event=>{if(event.target.closest('[data-scope]')&&['Enter',' '].includes(event.key)){event.preventDefault();selectElement(event.target.closest('[data-scope]').dataset.scope);}});
  $('element-options').addEventListener('keydown',event=>{const count=groups[scope].options.length;if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();preview(event.key==='Home'?0:event.key==='End'?count-1:(selected+(event.key==='ArrowRight'?1:-1)+count)%count,{focus:true});});
  $('scope-menu').addEventListener('keydown',event=>{if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;event.preventDefault();const buttons=[...$('scope-menu').querySelectorAll('button')],index=buttons.indexOf(doc.activeElement);buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length].focus();});
  $('scope-menu').addEventListener('toggle',event=>{for(const id of ['options','scope-switch'])$(id).setAttribute('aria-expanded',String(event.newState==='open'));if(event.newState==='closed'&&menuOpener?.isConnected&&doc.activeElement===doc.body)menuOpener.focus({preventScroll:true});});
  $('options').onclick=()=>showMenu($('options'));$('scope-switch').onclick=()=>showMenu($('scope-switch'));$('compare').onclick=openComparison;$('close-comparison').onclick=()=>$('comparison').close();
  $('comparison').addEventListener('close',()=>compareOpener?.isConnected&&compareOpener.focus({preventScroll:true}));
  for(const side of ['left','right'])$(side+'-option').onchange=event=>{if(side==='left')left=Number(event.target.value);else right=Number(event.target.value);renderComparison();};
  doc.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>{view=button.dataset.view;renderComparison();});
  $('use-left').onclick=()=>decide(left);$('use-right').onclick=()=>decide(right);$('compare-keep').onclick=()=>decide(state.current[scope]);
  $('use').onclick=()=>decide(selected);$('keep').onclick=()=>{if(state.pending[scope])decide(state.current[scope]);else{trayOpen=false;selected=state.current[scope];render();($('options').hidden?$('history'):$('options')).focus();}};
  $('history').onclick=openHistory;$('close-history').onclick=()=>$('history-dialog').close();
  $('undo').onclick=()=>{if(!previous)return;state=previous;previous=null;trayOpen=false;inlineOpen=false;pinned=null;selected=state.current[scope];$('toast').hidden=true;render();announce('Choice undone.');};$('dismiss-toast').onclick=()=>$('toast').hidden=true;
  $('toggle-theme').onclick=()=>{const dark=doc.documentElement.dataset.theme!=='dark';doc.documentElement.dataset.theme=dark?'dark':'light';$('toggle-theme').setAttribute('aria-label',`Switch to ${dark?'light':'dark'} appearance`);};
  $('toggle-chat').onclick=()=>{const root=doc.querySelector('.draft-workspace'),open=root.classList.toggle('chat-open');$('toggle-chat').setAttribute('aria-expanded',String(open));};
  $('toggle-slides').onclick=()=>{const open=doc.querySelector('.draft-workspace').classList.toggle('rail-open');$('toggle-slides').setAttribute('aria-expanded',String(open));};
  $('present').onclick=()=>{$('presentation-stage').innerHTML=renderSlide({preview:false});$('presentation-dialog').showModal();fitCanvases();};$('close-present').onclick=()=>$('presentation-dialog').close();
  $('approve').onclick=()=>{$('approve').innerHTML=icon('check')+'Approved';announce('Chosen slide approved in this fictional draft.');};
  $('draft-message').addEventListener('input',()=>{if($('draft-message').value.trim()&&draftContext===null)draftContext=contextLabel();if(!$('draft-message').value.trim())draftContext=null;renderFeedback();});
  $('draft-composer').onsubmit=event=>{event.preventDefault();if(!$('draft-message').value.trim())return;const message=$('draft-message').value;$('draft-message').value='';draftContext=null;if(question)answerQuestion(message);$('messages').insertAdjacentHTML('beforeend',`<article class="draft-message user"><header><time>10:44</time><span class="draft-avatar" role="img" aria-label="You">${icon('user')}</span></header><p>${escape(message)}</p></article>`);renderFeedback();announce('Message added to this in-memory draft.');};
  function answerQuestion(value){
    question=false;waitingForAgent=!['Leadership','Project team'].includes(value);state.pending.headline=!waitingForAgent;
    if(!waitingForAgent){
      answeredAudience=value;const copy=value==='Leadership'?['A clearer story for the next decision.','Turn a shared brief into a clear decision.']:['Better slides, shaped together.','One team. One clearer story.'];
      groups.headline.options[1].text=copy[0];groups.headline.options[2].text=copy[1];
    }
    render();announce('Answer saved.');
  }
  function renderInline(){
    const panel=$('inline-options');
    if(!inlineOpen){if(panel.matches(':popover-open'))panel.hidePopover();return;}
    const group=groups[scope],signature=JSON.stringify([scope,state.current[scope],group.options]);
    if(panel.dataset.signature!==signature){
      panel.dataset.signature=signature;
      panel.innerHTML=`<header class="inline-header"><button type="button" class="inline-scope" id="inline-scope">${group.label}${icon('down')}</button><span class="inline-status" role="status">2 options</span><div><button class="icon-button" id="inline-compare" type="button" aria-label="Compare ${group.label.toLowerCase()} options" title="Compare">${icon('compare')}</button><button class="icon-button" id="inline-all" type="button" aria-label="Show all ${group.label.toLowerCase()} options" title="Show all options">${icon('grid')}</button><button class="icon-button" id="inline-close" type="button" aria-label="Close element options">${icon('close')}</button></div></header><div class="inline-choices" role="tablist" aria-label="${group.label} options">${group.options.map((item,index)=>`<button type="button" role="tab" id="inline-option-${index}" data-inline-option="${index}" data-chosen="${index===state.current[scope]}" aria-controls="slide-stage" aria-label="${escape(item.name+'. '+item.text+(index===state.current[scope]?', chosen option':''))}">${scope==='graphic'?`<span class="inline-graphic">${graphic(item.variant)}</span>`:''}<span>${item.name}${index===state.current[scope]?icon('check'):''}</span></button>`).join('')}</div><footer><button type="button" id="inline-keep" class="text-button">Keep current</button><button type="button" id="inline-use" class="draft-primary">Use ${scope==='graphic'?'graphic':scope}${icon('right')}</button></footer>`;
    }
    for(const button of panel.querySelectorAll('[data-inline-option]')){const active=Number(button.dataset.inlineOption)===selected;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;const item=group.options[Number(button.dataset.inlineOption)];button.setAttribute('aria-label',button.textContent.replace(/\s+/g,' ').trim()+'. '+item.text+(button.dataset.chosen==='true'?', chosen option':''));}
    $('inline-use').disabled=selected===state.current[scope]||state.stale[scope];const status=panel.querySelector('.inline-status');status.textContent=selected===state.current[scope]?'2 options':'Preview';status.classList.toggle('is-preview',selected!==state.current[scope]);
    if(!panel.matches(':popover-open'))panel.showPopover();
    if(!inlineAnchor){const target=$('slide-stage').querySelector(`[data-scope="${scope}"]`);inlineAnchor=target?.getBoundingClientRect()||$('slide-stage').getBoundingClientRect();}
    const width=Math.min(430,innerWidth-24);panel.style.width=width+'px';
    panel.style.left=Math.max(12,Math.min(inlineAnchor.left,innerWidth-width-12))+'px';
    const above=inlineAnchor.top-panel.offsetHeight-8,minTop=scope==='headline'?64:Math.max(64,doc.querySelector('.draft-canvas-header').getBoundingClientRect().bottom+6);
    panel.style.top=Math.max(8,Math.min(above>=minTop?above:inlineAnchor.bottom+12,innerHeight-panel.offsetHeight-12))+'px';
  }
  function closeInline(){inlineOpen=false;pinned=null;selected=state.current[scope];trayOpen=false;render();}
  function scheduleInlineClose(){if(!openedByHover||pinned!==null)return;clearTimeout(hoverTimer);hoverTimer=setTimeout(()=>{if(!$('inline-options').matches(':hover')&&!doc.querySelector('.canvas-option-badge:hover'))closeInline();},220);}
  $('inline-options').addEventListener('pointerenter',()=>clearTimeout(hoverTimer));
  $('inline-options').addEventListener('pointerover',event=>{const button=event.target.closest('[data-inline-option]');if(!button||button.contains(event.relatedTarget))return;selected=Number(button.dataset.inlineOption);render();});
  $('inline-options').addEventListener('pointerleave',()=>{selected=pinned??state.current[scope];render();scheduleInlineClose();});
  $('inline-options').addEventListener('click',event=>{
    const option=event.target.closest('[data-inline-option]');if(option){pinned=Number(option.dataset.inlineOption);selected=pinned;openedByHover=false;render();return;}
    if(event.target.closest('#inline-use'))decide(selected);
    else if(event.target.closest('#inline-keep')){if(state.pending[scope])decide(state.current[scope]);else closeInline();}
    else if(event.target.closest('#inline-close'))closeInline();
    else if(event.target.closest('#inline-compare'))openComparison();
    else if(event.target.closest('#inline-all')){inlineOpen=false;pinned=selected;render();$(`option-${selected}`).focus({preventScroll:true});}
    else if(event.target.closest('#inline-scope'))showMenu($('inline-scope'));
  });
  $('inline-options').addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const count=groups[scope].options.length;selected=event.key==='Home'?0:event.key==='End'?count-1:(selected+(event.key==='ArrowRight'?1:-1)+count)%count;pinned=selected;render();$(`inline-option-${selected}`).focus({preventScroll:true});});
  doc.addEventListener('pointerdown',event=>{if(inlineOpen&&!event.target.closest('#inline-options,#scope-menu,[data-scope],[data-open-scope],dialog'))closeInline();},true);
  doc.addEventListener('keydown',event=>{if(event.key==='Escape'&&inlineOpen&&!doc.querySelector('dialog[open]')&&!$('scope-menu').matches(':popover-open')){event.preventDefault();closeInline();($('options').hidden?$('history'):$('options')).focus();}},true);
  window.addEventListener('resize',()=>{inlineAnchor=null;if(inlineOpen)renderInline();});
  function fitHolder(holder){
    const node=holder.querySelector(':scope > .fixed-canvas');if(!node)return;
    const factor=holder.clientWidth/1600;node.style.transform=`scale(${factor})`;node.style.setProperty('--inverse-scale',String(1/factor));
    if(holder.id==='slide-stage')mountBadges();
  }
  const viewportObserver=new ResizeObserver(entries=>{for(const entry of entries)fitHolder(entry.target);});
  function fitCanvases(){viewportObserver.disconnect();for(const node of doc.querySelectorAll('.fixed-canvas')){const holder=node.parentElement;viewportObserver.observe(holder);fitHolder(holder);}}
  function mountBadges(){
    const stage=$('slide-stage'),box=stage.getBoundingClientRect();for(const badge of stage.querySelectorAll('.canvas-option-badge'))badge.remove();
    if(state.stale[scope]&&trayOpen)return;
    for(const target of stage.querySelectorAll('[data-scope]')){
      const key=target.dataset.scope;if(!state.pending[key])continue;const bounds=target.getBoundingClientRect(),badge=doc.createElement('button');
      badge.addEventListener('pointerenter',event=>{if(event.pointerType==='touch')return;clearTimeout(hoverTimer);if(!inlineOpen||scope!==key)openScope(key,{byHover:true,focus:false});});badge.addEventListener('pointerleave',scheduleInlineClose);
      badge.type='button';badge.className='canvas-option-badge'+(key===scope&&trayOpen?' is-active':'');badge.dataset.openScope=key;badge.setAttribute('aria-label',`Review 2 ${groups[key].label.toLowerCase()} options`);badge.innerHTML=icon('designs')+'2';
      badge.style.cssText=`left:${Math.min(stage.clientWidth-35,Math.max(6,bounds.right-box.left-26))}px;top:${Math.max(6,bounds.top-box.top-18)}px`;stage.append(badge);
    }
  }
  if(question)render();else openScope('headline',{focus:false});
})();
