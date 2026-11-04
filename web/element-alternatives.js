/* Immutable element proposals, provisional canvas previews and deliberate human decisions. */
((root)=>{
  'use strict';
  const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  function revealOffset(card,strip,offset){
    return card.left<strip.left+2||card.right>strip.right-2?offset+card.left-strip.left-2:offset;
  }
  function panelPosition(anchor,size,viewport,frame,{alignEnd=false,minTop=64,avoid=null}={}){
    const fits=frame&&frame.width>=size.width,left=alignEnd?anchor.right-size.width:anchor.left;
    const above=anchor.top-size.height-26,covers=avoid&&above<avoid.bottom&&anchor.top>avoid.top;
    return {
      left:Math.max(fits?frame.left:12,Math.min(left,fits?frame.right-size.width:viewport.width-size.width-12)),
      top:Math.max(8,Math.min(above>=minTop&&!covers?above:anchor.bottom+12,viewport.height-size.height-12)),
    };
  }
  function create({api,getContext,activate=async()=>true,beforePreview=async()=>true,beforeDecide=async()=>true,afterDecision=async()=>{},onPreview=()=>{},onBusy=()=>{},notify=()=>{},openSlideDesigns=()=>{},onRequestRefresh=()=>{}}){
    const doc=root.document,icon=name=>root.SlideIcons.svg(name),wrap=doc.getElementById('preview-wrap'),preview=doc.getElementById('preview'),canvas=doc.querySelector('.canvas-area');
    const toggle=doc.createElement('button');toggle.id='element-alternative-toggle';toggle.className='ea-toggle';toggle.type='button';toggle.hidden=true;toggle.setAttribute('aria-haspopup','menu');toggle.setAttribute('aria-expanded','false');toggle.innerHTML=icon('copy')+'<span>Options</span><i aria-hidden="true"></i>';doc.querySelector('.preview-controls').prepend(toggle);
    const menu=doc.createElement('div');menu.className='ea-scope-menu';menu.id='element-alternative-menu';menu.setAttribute('popover','manual');menu.setAttribute('role','menu');menu.setAttribute('aria-label','Slide and element options');doc.body.append(menu);toggle.setAttribute('aria-controls',menu.id);
    const panel=doc.createElement('section');panel.className='ea-panel';panel.id='element-alternative-panel';panel.setAttribute('popover','manual');panel.setAttribute('aria-label','Element options');doc.body.append(panel);
    const tray=doc.createElement('section');tray.className='ea-tray';tray.id='element-alternative-tray';tray.hidden=true;tray.setAttribute('aria-label','Expanded element options');wrap.after(tray);
    const layer=doc.createElement('div');layer.className='ea-badges';doc.body.append(layer);
    const outline=doc.createElement('div');outline.className='ea-outline';outline.hidden=true;layer.append(outline);
    const savedOverlay=doc.createElement('div');savedOverlay.className='ea-saved-overlay';savedOverlay.hidden=true;savedOverlay.innerHTML='<div class="ea-frame"></div><span class="ea-saved-label" role="status">Earlier design · saved preview</span>';wrap.append(savedOverlay);
    const comparison=doc.createElement('dialog');comparison.className='ea-comparison';comparison.setAttribute('aria-labelledby','ea-comparison-title');
    comparison.innerHTML=`<header role="presentation"><h2 id="ea-comparison-title"></h2><div class="ea-view-switch" role="group" aria-label="Comparison view"><button type="button" data-ea-view="element" aria-pressed="true">Element</button><button type="button" data-ea-view="slide" aria-pressed="false">Full slide</button></div><button type="button" data-ea-compare-close aria-label="Close element comparison">${icon('close')}</button></header><p class="ea-context" role="status" hidden></p><div class="ea-compare-grid">${['left','right'].map(side=>`<section><header><label class="sr-only" for="ea-${side}-option">${side==='left'?'Left':'Right'} option</label><select id="ea-${side}-option" data-ea-${side}></select><span class="ea-side-current"></span></header><div class="ea-compare-preview ea-compare-${side}"></div></section>`).join('')}</div><footer role="presentation"><button type="button" data-ea-keep>Keep current</button><div><button type="button" data-ea-use-left></button><button type="button" class="primary" data-ea-use-right></button></div></footer><p class="ea-error" role="alert"></p>`;doc.body.append(comparison);
    let context=null,scope='',page=null,group=null,selected=null,pinned=null,expanded=false,opened=false,busy=false,hoverOpened=false,alignEnd=false,anchor=null,original=null;
    let generation=0,loadGeneration=0,loadKey='',expectedHash='',expectedDecision=null,closeTimer=null,request=null,compareOpener=null,historyGeneration=0,view='element',left=null,right=null,menuOpener=null,boundDocument=null;
    const bound=new WeakSet(),resize=new ResizeObserver(()=>{position();mountBadges();}),frameResize=new ResizeObserver(entries=>{for(const entry of entries)scaleFrame(entry.target);});resize.observe(wrap);resize.observe(preview);
    const base=(value=context)=>`/api/decks/${encodeURIComponent(value.deckId)}/slides/${encodeURIComponent(value.slideId)}/element-alternatives`;
    const currentId=()=>group?.current_option_id||null;
    const option=id=>group?.options.find(item=>item.id===id)||null;
    const selectedOption=()=>option(selected);
    const stateTarget=()=>preview.contentDocument?.getElementById(group?.target_id||'');
    const feedbackContext=()=>{
      const item=selectedOption();return opened&&item&&(group.stale||item.id!==currentId())?{element_option_id:item.id,element_option_name:item.name,element_context_hash:group.context_hash,content_hash:item.source_hash}:null;
    };
    function announce(message){let status=doc.getElementById('element-alternative-status');if(!status){status=doc.createElement('span');status.id='element-alternative-status';status.className='sr-only';status.setAttribute('role','status');doc.body.append(status);}status.textContent=message;}
    function focusControl(){const button=!toggle.hidden?toggle:doc.getElementById('version-history');button?.focus({preventScroll:true});}
    function anchorFor(element){const rect=element?root.ElementProperties.frameAnchor(element.getBoundingClientRect(),preview):null;return rect?{...rect,right:rect.left+rect.width,bottom:rect.top+rect.height}:null;}
    function proposalEnvelope(){
      const target=stateTarget();if(!target||group.stale)return null;const bounds=[anchorFor(target)];
      for(const item of group.options){const template=target.ownerDocument.createElement('template');template.innerHTML=item.html;const replacement=template.content.firstElementChild;if(!replacement||replacement.id!==group.target_id)continue;target.replaceWith(replacement);try{bounds.push(anchorFor(replacement));}finally{replacement.replaceWith(target);}}
      const visible=bounds.filter(Boolean),left=Math.min(...visible.map(item=>item.left)),top=Math.min(...visible.map(item=>item.top)),right=Math.max(...visible.map(item=>item.right)),bottom=Math.max(...visible.map(item=>item.bottom));return {left,top,right,bottom,width:right-left,height:bottom-top};
    }
    function scaleFrame(slot){const frame=slot.querySelector('iframe');if(frame){frame.style.width='1600px';frame.style.height='900px';frame.style.transform=`scale(${slot.clientWidth/1600})`;}}
    function clearFrame(slot){frameResize.unobserve(slot);slot.replaceChildren();delete slot.dataset.url;}
    function frame(slot,item,{element=false,decorative=true}={}){
      const url=item.preview_url+(element?(item.preview_url.includes('?')?'&':'?')+'element=true':'');
      if(slot.dataset.url===url&&slot.querySelector('iframe'))return;
      clearFrame(slot);slot.dataset.url=url;const child=doc.createElement('iframe');child.title=`${item.name} ${element?'element':'slide'} preview`;child.setAttribute('sandbox','allow-scripts');child.referrerPolicy='no-referrer';child.tabIndex=-1;if(decorative)child.setAttribute('aria-hidden','true');child.src=url;slot.append(child);frameResize.observe(slot);scaleFrame(slot);
    }
    function restore(){
      if(original){if(original.replacement.isConnected)original.replacement.replaceWith(original.node);original=null;}
      outline.hidden=true;canvas.classList.remove('ea-preview-mode');savedOverlay.hidden=true;wrap.classList.remove('ea-saved-preview');clearFrame(savedOverlay.querySelector('.ea-frame'));
    }
    function applyPreview(){
      restore();const item=selectedOption();if(!opened||!item||!group)return;
      if(group.stale){savedOverlay.hidden=false;wrap.classList.add('ea-saved-preview');canvas.classList.add('ea-preview-mode');frame(savedOverlay.querySelector('.ea-frame'),item,{decorative:false});}
      else{
        const target=stateTarget();
        if(target&&item.id!==currentId()){
          const template=target.ownerDocument.createElement('template');template.innerHTML=item.html;const replacement=template.content.firstElementChild;
          if(!replacement||replacement.id!==group.target_id){notify('This option no longer matches its element. Ask for refreshed options.',true);return;}
          original={node:target,replacement};target.replaceWith(replacement);canvas.classList.add('ea-preview-mode');
        }
      }
      onPreview(feedbackContext());positionOutline();mountBadges();
    }
    function positionOutline(){
      const rect=opened&&!group?.stale?anchorFor(stateTarget()):null;outline.hidden=!rect;
      if(rect)outline.style.cssText=`left:${rect.left-5}px;top:${rect.top-5}px;width:${rect.width+10}px;height:${rect.height+10}px;`;
    }
    function position(){
      if(!opened)return;positionOutline();if(expanded)return;
      if(!anchor)anchor=proposalEnvelope()||preview.getBoundingClientRect();
      panel.style.width=`${Math.min(group.kind==='graphic'?500:640,root.innerWidth-24)}px`;
      const header=doc.querySelector('.canvas-header')?.getBoundingClientRect(),headline=group.kind!=='headline'?preview.contentDocument?.querySelector('h1,h2,h3'):null;
      const at=panelPosition(anchor,{width:panel.offsetWidth,height:panel.offsetHeight},{width:root.innerWidth,height:root.innerHeight},preview.getBoundingClientRect(),{alignEnd,minTop:group.kind==='headline'?60:Math.max(64,(header?.bottom||58)+6),avoid:anchorFor(headline)});
      panel.style.left=at.left+'px';panel.style.top=at.top+'px';
    }
    function choicesMarkup(prefix){
      return group.options.map(item=>{
        const chosen=item.id===currentId();return `<button type="button" role="tab" id="${prefix}-${escape(item.id)}" data-ea-option="${escape(item.id)}" data-chosen="${chosen}" aria-selected="${item.id===selected}" aria-controls="preview-wrap" tabindex="${item.id===selected?'0':'-1'}" aria-label="${escape(item.name+(group.kind==='graphic'?'':' '+item.text)+(chosen?', chosen option':''))}">${group.kind==='graphic'?'<span class="ea-frame ea-graphic" aria-hidden="true"></span>':''}<span class="ea-option-label">${escape(item.name)}${chosen?icon('check'):''}</span> ${group.kind!=='graphic'?`<span class="ea-copy">${escape(item.text)}</span>`:''}</button>`;
      }).join('');
    }
    function renderSurface(surface,isExpanded){
      const signature=JSON.stringify([group.id,group.context_hash,group.current_option_id,group.pending,group.stale,group.options.map(item=>[item.id,item.source_hash])]);
      if(surface.dataset.signature!==signature){
        for(const slot of surface.querySelectorAll('.ea-frame'))clearFrame(slot);surface.dataset.signature=signature;surface.dataset.kind=group.kind;
        surface.innerHTML=`<header class="ea-header"><button type="button" class="ea-scope" data-ea-scope title="${escape(group.label)}"><span>${escape(group.label)}</span>${icon('down')}</button><span class="ea-status" role="status"></span><div><button type="button" class="ea-icon" data-ea-compare aria-label="Compare ${escape(group.label.toLowerCase())} options" title="Compare">${icon('batch')}</button><button type="button" class="ea-icon" ${isExpanded?'data-ea-compact aria-label="Show options on slide" title="Show on slide"':'data-ea-all aria-label="Show all options" title="Show all options"'}>${icon(isExpanded?'up':'grid')}</button><button type="button" class="ea-icon" data-ea-close aria-label="Close element options">${icon('close')}</button></div></header><p class="ea-context" role="status" ${group.stale?'':'hidden'}>${escape(group.stale_reason||'Earlier design · saved preview')} <button type="button" data-ea-refresh>Ask to refresh</button></p><div class="ea-choices" role="tablist" aria-label="${escape(group.label)} options">${choicesMarkup(isExpanded?'ea-expanded-option':'ea-option')}</div><footer><button type="button" data-ea-keep>${group.pending?'Keep current':'Back to current'}</button><button type="button" class="primary" data-ea-use>Use ${escape(group.kind==='graphic'?'graphic':group.kind)}${icon('right')}</button></footer><p class="ea-error" role="alert"></p>`;
        if(group.kind==='graphic')for(const button of surface.querySelectorAll('[data-ea-option]'))frame(button.querySelector('.ea-frame'),option(button.dataset.eaOption),{element:true});
      }
      const current=selected===currentId(),status=surface.querySelector('.ea-status');status.textContent=group.stale?'Earlier design':current?`${group.options.length-1} options`:'Preview';status.classList.toggle('is-preview',!current);
      for(const button of surface.querySelectorAll('[data-ea-option]')){const active=button.dataset.eaOption===selected;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;}
      for(const button of surface.querySelectorAll('button'))button.disabled=busy;
      surface.querySelector('[data-ea-use]').disabled=busy||current||group.stale;
    }
    function render(){
      const pending=page?.sets.some(item=>item.pending&&!item.stale);toggle.hidden=!context||(!pending&&!opened);toggle.disabled=busy;toggle.querySelector('i').hidden=!pending;
      if(!opened||!group){if(panel.matches(':popover-open'))panel.hidePopover();tray.hidden=true;mountCards();mountBadges();return;}
      const surface=expanded?tray:panel;renderSurface(surface,expanded);
      tray.hidden=!expanded;if(expanded){if(panel.matches(':popover-open'))panel.hidePopover();}else if(!panel.matches(':popover-open'))panel.showPopover();
      position();mountCards();mountBadges();if(comparison.open)renderComparison();
    }
    function reveal(){const surface=expanded?tray:panel,strip=surface.querySelector('.ea-choices'),button=surface.querySelector(`[data-ea-option="${CSS.escape(selected||'')}"]`);if(button&&strip)strip.scrollLeft=revealOffset(button.getBoundingClientRect(),strip.getBoundingClientRect(),strip.scrollLeft);}
    function select(id,{pin=false,focus=false}={}){
      if(busy||!option(id))return;selected=id;request=null;if(pin){pinned=id;hoverOpened=false;}
      applyPreview();render();if(focus)(expanded?tray:panel).querySelector(`[data-ea-option="${CSS.escape(id)}"]`)?.focus({preventScroll:true});if(pin||focus)reveal();
    }
    function dismiss({focus=false}={}){
      generation++;clearTimeout(closeTimer);restore();opened=false;expanded=false;selected=null;pinned=null;anchor=null;request=null;onPreview(null);if(comparison.open)comparison.close();if(panel.matches(':popover-open'))panel.hidePopover();if(menu.matches(':popover-open'))menu.hidePopover();render();if(focus)focusControl();
    }
    async function fetchPage({setId=null}={}){
      if(!context)return null;const target={...context},at=scope,token=++loadGeneration;
      const result=await api(base(target));if(scope!==at||token!==loadGeneration)return null;
      if(setId&&!result.sets.some(item=>item.id===setId)){const old=await api(base(target)+'?set_id='+encodeURIComponent(setId));if(scope!==at||token!==loadGeneration)return null;result.sets.push(...old.sets);}
      page=result;if(group&&!opened)group=result.sets.find(item=>item.id===group.id)||group;render();return result;
    }
    async function open({setId=null,optionId=null,byHover=false,focus=true,slideId=context?.slideId}={}){
      if(busy||!context||!slideId)return false;
      if(slideId!==context.slideId&&!await activate(slideId))return false;
      const at=scope,token=++generation;if(!await beforePreview()||at!==scope||token!==generation)return false;
      try{
        await fetchPage({setId});if(at!==scope||token!==generation||!page)return false;
        const next=setId?page.sets.find(item=>item.id===setId):page?.sets.find(item=>item.pending&&!item.stale)||page?.sets[0];if(!next)return false;if(byHover&&!layer.querySelector(`[data-ea-badge="${CSS.escape(next.id)}"]`)?.matches(':hover'))return false;if(!next.stale&&preview.contentDocument?.body?.dataset.previewKey!==next.context_hash){notify('Show the latest slide update before reviewing these options.',true);return false;}
        restore();group=next;selected=optionId&&optionId!==currentId()?optionId:currentId()||next.options[0]?.id;pinned=optionId?selected:null;opened=true;expanded=false;hoverOpened=byHover;alignEnd=byHover;anchor=proposalEnvelope();expectedHash=page.content_hash;expectedDecision=group.decision_id;request=null;
        if(menu.matches(':popover-open'))menu.hidePopover();applyPreview();render();
        if(focus&&!byHover){panel.querySelector(`[data-ea-option="${CSS.escape(selected)}"]`)?.focus({preventScroll:true});reveal();}announce(`${group.label} options. Choose Use or Keep current to decide.`);return true;
      }catch(error){notify(error.message,true);return false;}
    }
    function latestPending(){const seen=new Set();return (page?.sets||[]).filter(item=>{if(seen.has(item.target_id))return false;seen.add(item.target_id);return item.pending&&!item.stale;});}
    function mountBadges(){
      const ready=preview.dataset.loadToken?.endsWith('-ready')&&preview.contentDocument?.body?.dataset.previewKey===context?.item.content_hash;
      const pending=!ready||opened&&group?.stale?[]:latestPending(),seen=new Set();
      for(const item of pending){const target=preview.contentDocument?.getElementById(item.target_id),rect=opened&&item.id===group?.id&&anchor?anchor:anchorFor(target);if(!rect)continue;
        const bounds=preview.getBoundingClientRect();if(rect.bottom<bounds.top||rect.top>bounds.bottom||rect.right<bounds.left||rect.left>bounds.right)continue;
        seen.add(item.id);let badge=layer.querySelector(`[data-ea-badge="${CSS.escape(item.id)}"]`);if(!badge){badge=doc.createElement('button');badge.type='button';badge.className='ea-badge';badge.dataset.eaBadge=item.id;badge.setAttribute('aria-controls',panel.id);layer.append(badge);}
        badge.innerHTML=icon('copy')+`<span>${item.options.length-1}</span>`;badge.setAttribute('aria-label',`${item.label}: ${item.options.length-1} alternative${item.options.length===2?'':'s'}`);badge.setAttribute('aria-expanded',String(opened&&group?.id===item.id));badge.disabled=busy;
        const coarse=root.matchMedia('(pointer:coarse)').matches;badge.style.left=`${Math.max(bounds.left,Math.min(rect.right-24-(coarse?5:0),bounds.right-(coarse?44:34)))}px`;badge.style.top=`${Math.max(bounds.top,rect.top-(coarse?33:23))}px`;
      }
      for(const badge of layer.querySelectorAll('[data-ea-badge]'))if(!seen.has(badge.dataset.eaBadge))badge.remove();
    }
    function scheduleClose(){if(!hoverOpened||pinned!==null)return;clearTimeout(closeTimer);closeTimer=setTimeout(()=>{if(!panel.matches(':hover')&&!layer.querySelector('[data-ea-badge]:hover')&&!panel.contains(doc.activeElement))dismiss();},260);}
    function showMenu(opener=toggle){
      if(!context)return;menuOpener=opener;
      const slideDesign=!!context.snapshot?.slide_designs?.[context.slideId];menu.innerHTML=`${slideDesign?`<h3 role="presentation">This slide</h3><button type="button" role="menuitem" data-ea-slide>${icon('slides')}Slide designs${icon('right')}</button>`:''}<h3 role="presentation">Elements</h3>${latestPending().map(item=>`<button type="button" role="menuitem" data-ea-open="${escape(item.id)}">${icon(item.kind==='graphic'?'image':'edit')}<span>${escape(item.label)}</span><small>${item.options.length-1} options</small></button>`).join('')||'<span class="ea-menu-empty">All choices saved</span>'}<button type="button" role="menuitem" data-ea-history>${icon('history')}History &amp; saved options</button>`;
      if(menu.matches(':popover-open')){menu.hidePopover();return;}menu.showPopover();const rect=opener.getBoundingClientRect();menu.style.left=Math.max(8,Math.min(rect.left,root.innerWidth-menu.offsetWidth-8))+'px';menu.style.top=Math.max(8,Math.min(rect.bottom+7,root.innerHeight-menu.offsetHeight-8))+'px';menu.querySelector('button')?.focus({preventScroll:true});toggle.setAttribute('aria-expanded','true');
    }
    function selection(element){
      if(!element||busy)return false;const match=latestPending().find(item=>{const target=element.ownerDocument?.getElementById(item.target_id);return target&&(target===element||target.contains(element));});if(!match)return false;
      void open({setId:match.id});return true;
    }
    function bindPreview(frameDoc){
      if(!frameDoc)return;if(boundDocument&&boundDocument!==frameDoc&&opened)dismiss();boundDocument=frameDoc;mountBadges();if(bound.has(frameDoc))return;bound.add(frameDoc);
      frameDoc.addEventListener('click',event=>{
        if(event.ctrlKey||event.metaKey||event.shiftKey)return;
        const match=latestPending().find(item=>{const node=frameDoc.getElementById(item.target_id);return node&&(node===event.target||node.contains(event.target));});
        if(match){event.preventDefault();event.stopImmediatePropagation();void open({setId:match.id});}else if(opened)dismiss();
      },true);
      frameDoc.addEventListener('keydown',event=>{if(event.key==='Escape'&&opened){event.preventDefault();event.stopImmediatePropagation();dismiss({focus:true});}},true);
    }
    async function decide(id){
      if(busy||!group||id&&group.stale)return;const target={...context},at=scope,was=group.id,item=id?option(id):null;
      busy=true;onBusy(true);render();
      try{
        if(!await beforeDecide()||scope!==at||group?.id!==was)return;
        if(!request)request={path:base(target)+'/decide',body:{set_id:was,option_id:item?.id===currentId()?null:item?.id||null,expected_content_hash:expectedHash,expected_decision_id:expectedDecision,request_id:root.crypto.randomUUID()}};
        const result=await api(request.path,request.body);request=null;if(scope!==at)return;dismiss();await afterDecision(result,target);await fetchPage();
        notify(result.action==='keep'?`${group.label} kept. Options saved.`:`${item.name} chosen. Options saved.`,false,{label:'Undo',run:async()=>{
          if(busy)return;busy=true;onBusy(true);
          try{const receipt=await api(base(target)+'/undo',{decision_id:result.id,expected_content_hash:result.content_hash,request_id:root.crypto.randomUUID()});if(scope!==at)return;dismiss();await afterDecision(receipt,target);await fetchPage();focusControl();}finally{busy=false;onBusy(false);render();}
        }});focusControl();
      }catch(error){if(error.status)request=null;if(scope===at){for(const surface of [panel,tray,comparison]){const text=surface.querySelector('.ea-error');if(text)text.textContent=error.message;}notify(error.message,true);}}
      finally{busy=false;onBusy(false);if(scope===at)render();}
    }
    function renderComparison(){
      comparison.querySelector('#ea-comparison-title').textContent=`Compare ${group.label.toLowerCase()}`;
      const warning=comparison.querySelector('.ea-context');warning.hidden=!group.stale;warning.textContent=group.stale_reason||'Earlier design · saved preview';
      for(const [side,id] of [['left',left],['right',right]]){
        const select=comparison.querySelector(`[data-ea-${side}]`);select.disabled=busy;select.innerHTML=group.options.map(item=>`<option value="${escape(item.id)}" ${item.id===id?'selected':''}>${escape(item.name)}</option>`).join('');
        const item=option(id),slot=comparison.querySelector(`.ea-compare-${side}`),chosen=id===currentId();slot.dataset.chosen=String(chosen);slot.dataset.kind=group.kind;slot.dataset.view=view;
        for(const old of slot.querySelectorAll('.ea-frame'))clearFrame(old);slot.replaceChildren();
        if(view==='slide'||group.kind==='graphic'){const holder=doc.createElement('div');holder.className='ea-frame';slot.append(holder);frame(holder,item,{element:view==='element',decorative:false});}
        else{const copy=doc.createElement('div');copy.className='ea-compare-copy';copy.textContent=item.text;slot.append(copy);}
        select.parentElement.querySelector('.ea-side-current').innerHTML=chosen?`<span role="img" aria-label="Chosen option">${icon('check')}</span>`:'';
        const use=comparison.querySelector(`[data-ea-use-${side}]`);use.textContent=`${chosen?'Keep':'Use'} ${item.name}`;use.disabled=busy||group.stale&&!chosen;
      }
      for(const control of comparison.querySelectorAll('[data-ea-view]')){control.setAttribute('aria-pressed',String(control.dataset.eaView===view));control.disabled=busy;}comparison.querySelector('[data-ea-compare-close]').disabled=busy;
      comparison.querySelector('[data-ea-keep]').hidden=left===currentId()||right===currentId();comparison.querySelector('[data-ea-keep]').disabled=busy;
    }
    function openComparison(){if(!opened||busy)return;left=currentId()||group.options[0].id;right=selected!==left?selected:group.options.find(item=>item.id!==left)?.id||left;view='element';compareOpener=doc.activeElement;renderComparison();comparison.showModal();comparison.querySelector('[data-ea-compare-close]').focus();}
    function mountCards(){
      for(const card of doc.querySelectorAll('[data-element-set]')){
        const item=page?.sets.find(value=>value.id===card.dataset.elementSet);if(!item)continue;card.classList.toggle('is-resolved',!item.pending||item.stale);card.innerHTML=`${icon(item.pending&&!item.stale?'copy':'check')}<span>${escape(item.label)} ${item.pending&&!item.stale?`<small>${item.options.length-1} options</small>`:'<small>Saved options</small>'}</span>${icon('right')}`;card.setAttribute('aria-label',card.textContent.replace(/\s+/g,' ').trim());
      }
    }
    async function history(dialog,target){
      dialog.querySelector('.ea-history')?.remove();if(target.kind!=='slide')return;const token=++historyGeneration,path=base({deckId:target.deckId,slideId:target.slideId});
      try{
        const result=await api(path);if(token!==historyGeneration||!dialog.open||!result.sets.length)return;
        const details=doc.createElement('details');details.className='ea-history';dialog.querySelector('.version-layout').before(details);let loading=false;
        const paint=()=>{for(const button of details.querySelectorAll('[data-ea-saved]')){const holder=button.querySelector('.ea-frame');if(!holder)continue;if(!button.checkVisibility()){clearFrame(holder);continue;}const saved=result.sets.find(item=>item.id===button.dataset.eaSet),item=saved.options.find(value=>value.id===button.dataset.eaSaved);frame(holder,item,{element:true});}};
        const draw=()=>{
          const expandedIds=new Set([...details.querySelectorAll('.ea-saved-group[open]')].map(item=>item.dataset.setId));for(const holder of details.querySelectorAll('.ea-frame'))clearFrame(holder);
          details.innerHTML=`<summary>${icon('copy')}Element decisions &amp; saved options</summary><div class="ea-history-decisions">${result.decisions.map(item=>`<p>${icon(item.action==='undo'?'restore':'check')}<span>${escape(item.action==='undo'?'Choice undone':item.action==='keep'?'Kept current':item.name+' chosen')}</span><time>${escape(new Date(item.created_at).toLocaleString())}</time></p>`).join('')}</div>${result.sets.map(saved=>`<details class="ea-saved-group" data-set-id="${escape(saved.id)}" ${expandedIds.has(saved.id)?'open':''}><summary>${escape(saved.label)}${saved.stale?' · Earlier design':''} · ${escape(new Date(saved.created_at).toLocaleString())}</summary><div class="ea-saved-options">${saved.options.map(item=>`<button type="button" data-ea-saved="${escape(item.id)}" data-ea-set="${escape(saved.id)}" data-chosen="${item.id===saved.current_option_id}" aria-label="Preview saved ${escape(saved.label+' '+item.name)}">${saved.kind==='graphic'?'<span class="ea-frame" aria-hidden="true"></span>':`<span class="ea-saved-copy">${escape(item.text)}</span>`}<strong>${escape(item.name)}${item.id===saved.current_option_id?icon('check'):''}</strong></button>`).join('')}</div></details>`).join('')}${result.next_cursor!=null||result.next_decision_cursor!=null?'<button type="button" data-ea-earlier>Load earlier element history</button>':''}`;
          for(const button of details.querySelectorAll('[data-ea-saved]'))button.setAttribute('aria-label',button.textContent.replace(/\s+/g,' ').trim()+' · Preview saved option');paint();
        };draw();details.addEventListener('toggle',paint,{capture:true});
        details.addEventListener('click',async event=>{
          const saved=event.target.closest('[data-ea-saved]');if(saved){if(getContext()?.deckId!==target.deckId){notify('Open saved options from their presentation.',true);return;}dialog.close();void open({slideId:target.slideId,setId:saved.dataset.eaSet,optionId:saved.dataset.eaSaved});return;}
          if(!event.target.closest('[data-ea-earlier]')||loading)return;loading=true;event.target.closest('[data-ea-earlier]').disabled=true;
          try{const earlier=await api(path+'?'+new URLSearchParams({cursor:String(result.next_cursor??0),decision_cursor:String(result.next_decision_cursor??0)}));if(token!==historyGeneration||!dialog.open)return;for(const key of ['sets','decisions']){const seen=new Set(result[key].map(item=>item.id));result[key].push(...earlier[key].filter(item=>!seen.has(item.id)));}result.next_cursor=earlier.next_cursor;result.next_decision_cursor=earlier.next_decision_cursor;draw();}catch(error){notify(error.message,true);}finally{loading=false;const button=details.querySelector('[data-ea-earlier]');if(button)button.disabled=false;}
        });dialog.addEventListener('close',()=>{for(const holder of details.querySelectorAll('.ea-frame'))clearFrame(holder);},{once:true});
      }catch(error){notify(error.message,true);}
    }
    function sync(snapshot,slideId){
      const item=snapshot.slides.find(value=>value.id===slideId);if(!item)return;const next=`${snapshot.id}/${slideId}`,summary=snapshot.element_alternatives?.[slideId],signature=JSON.stringify([next,item.content_hash,summary]);
      if(scope!==next){dismiss();scope=next;context=null;page=null;group=null;loadKey='';loadGeneration++;}
      else if(context?.item.content_hash!==item.content_hash&&opened&&!busy)dismiss();
      context={deckId:snapshot.id,slideId,item,snapshot};if(signature!==loadKey){loadKey=signature;if(summary||page){void fetchPage().catch(error=>notify(error.message,true));}else render();}else render();
    }
    function surfaceClick(event){
      const tab=event.target.closest('[data-ea-option]');if(tab){select(tab.dataset.eaOption,{pin:true});return;}
      if(event.target.closest('[data-ea-use]'))void decide(selected);
      else if(event.target.closest('[data-ea-keep]')){if(group.pending)void decide(null);else dismiss({focus:true});}
      else if(event.target.closest('[data-ea-close]'))dismiss({focus:true});
      else if(event.target.closest('[data-ea-compare]'))openComparison();
      else if(event.target.closest('[data-ea-all],[data-ea-compact]')){expanded=!expanded;pinned=selected;hoverOpened=false;render();const button=(expanded?tray:panel).querySelector(`[data-ea-option="${CSS.escape(selected)}"]`);button?.focus({preventScroll:true});reveal();}
      else if(event.target.closest('[data-ea-scope]'))showMenu(event.target.closest('[data-ea-scope]'));
      else if(event.target.closest('[data-ea-refresh]')){onRequestRefresh(group);}
    }
    for(const surface of [panel,tray]){
      surface.addEventListener('click',surfaceClick);
      surface.addEventListener('keydown',event=>{if(!event.target.closest('[data-ea-option]')||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const index=group.options.findIndex(item=>item.id===selected),next=event.key==='Home'?0:event.key==='End'?group.options.length-1:(index+(event.key==='ArrowRight'?1:-1)+group.options.length)%group.options.length;select(group.options[next].id,{pin:true,focus:true});});
      surface.addEventListener('pointerover',event=>{const button=event.target.closest('[data-ea-option]');if(!button||button.contains(event.relatedTarget)||event.pointerType==='touch')return;select(button.dataset.eaOption);});
      surface.addEventListener('pointerenter',()=>clearTimeout(closeTimer));
      surface.addEventListener('pointerleave',()=>{if(opened){selected=pinned||currentId()||group.options[0].id;applyPreview();render();}scheduleClose();});
    }
    toggle.addEventListener('click',()=>showMenu(toggle));
    menu.addEventListener('click',event=>{const button=event.target.closest('[data-ea-open]');if(button)void open({setId:button.dataset.eaOpen});else if(event.target.closest('[data-ea-slide]')){dismiss();openSlideDesigns();}else if(event.target.closest('[data-ea-history]')){dismiss();doc.getElementById('version-history').click();}});
    menu.addEventListener('keydown',event=>{if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;event.preventDefault();const buttons=[...menu.querySelectorAll('button')],index=buttons.indexOf(doc.activeElement);buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length]?.focus();});
    menu.addEventListener('toggle',event=>{toggle.setAttribute('aria-expanded',String(event.newState==='open'));if(event.newState==='closed'&&doc.activeElement===doc.body&&menuOpener?.isConnected)menuOpener.focus({preventScroll:true});});
    layer.addEventListener('click',event=>{const button=event.target.closest('[data-ea-badge]');if(button)void open({setId:button.dataset.eaBadge});});
    layer.addEventListener('pointerover',event=>{const button=event.target.closest('[data-ea-badge]');if(!button||event.pointerType==='touch'||button.contains(event.relatedTarget))return;clearTimeout(closeTimer);if(!opened||group.id!==button.dataset.eaBadge)void open({setId:button.dataset.eaBadge,byHover:true,focus:false});});
    layer.addEventListener('pointerout',scheduleClose);
    doc.addEventListener('click',event=>{const card=event.target.closest('[data-element-set]');if(card)void open({setId:card.dataset.elementSet});});
    doc.addEventListener('pointerdown',event=>{if(pinned!==null&&event.target.closest('.feedback-compose'))return;if(opened&&!busy&&!event.target.closest('.ea-panel,.ea-tray,.ea-scope-menu,.ea-badge,.ea-comparison,[data-element-set],#element-alternative-toggle')&&!comparison.open)dismiss();},true);
    doc.addEventListener('keydown',event=>{if(event.key==='Escape'&&!comparison.open){if(menu.matches(':popover-open')){event.preventDefault();menu.hidePopover();menuOpener?.focus({preventScroll:true});}else if(opened&&!doc.querySelector('dialog[open]')){event.preventDefault();dismiss({focus:true});}}},true);
    comparison.addEventListener('click',event=>{if(event.target.closest('[data-ea-compare-close]')){if(!busy)comparison.close();}else if(event.target.closest('[data-ea-keep]'))void decide(null);else if(event.target.closest('[data-ea-use-left]'))void decide(left);else if(event.target.closest('[data-ea-use-right]'))void decide(right);else{const button=event.target.closest('[data-ea-view]');if(button){view=button.dataset.eaView;renderComparison();}}});
    for(const side of ['left','right'])comparison.querySelector(`[data-ea-${side}]`).addEventListener('change',event=>{if(side==='left')left=event.target.value;else right=event.target.value;renderComparison();});
    comparison.addEventListener('cancel',event=>{if(busy)event.preventDefault();});comparison.addEventListener('close',()=>{for(const holder of comparison.querySelectorAll('.ea-frame'))clearFrame(holder);if(compareOpener?.isConnected)compareOpener.focus({preventScroll:true});});
    root.addEventListener('resize',()=>{anchor=null;position();mountBadges();if(opened)reveal();});doc.addEventListener('scroll',event=>{if(event.target.closest?.('.ea-panel,.ea-tray,.ea-scope-menu,.ea-comparison,.ea-history'))return;anchor=null;position();mountBadges();},true);
    return {sync,bindPreview,selection,history,mountCards,open,dismiss,close:dismiss,isOpen:()=>opened||comparison.open,isPreviewing:()=>!!feedbackContext(),isBusy:()=>busy,feedbackContext,
      reset(){dismiss();scope='';context=null;page=null;group=null;loadKey='';loadGeneration++;render();},
      card(comment){return comment.element_set_id?`<button type="button" class="ea-chat-card" data-element-set="${escape(comment.element_set_id)}">Element options ${icon('right')}</button>`:'';}};
  }
  const api={create,revealOffset,panelPosition};if(typeof module!=='undefined')module.exports=api;else root.ElementAlternatives=api;
})(typeof window!=='undefined'?window:globalThis);
