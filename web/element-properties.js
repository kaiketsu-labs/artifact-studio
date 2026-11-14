/* Familiar text controls over revision-bound, provisional element edits. */
((root)=>{
  'use strict';
  const sizeSteps=[8,9,10,11,12,14,16,18,20,22,24,26,28,32,36,40,44,48,54,60,66,72,80,88,96,112,128,144,192,256,320,400,512,720];
  const round=value=>Number(value.toFixed(3));
  /** Read the effective text formatting, retaining a supported authored size unit. */
  function typographyOf(element, view=element.ownerDocument.defaultView){
    const computed=view.getComputedStyle(element);
    const raw=element.style.fontSize||computed.fontSize;
    const match=/^([\d.]+)(px|pt|em|rem|%|vw|vh|cqw|cqh)$/.exec(raw);
    const rgb=/rgba?\(\s*(\d+)[, ]+\s*(\d+)[, ]+\s*(\d+)/.exec(computed.color);
    const color=rgb?'#'+rgb.slice(1,4).map(value=>Number(value).toString(16).padStart(2,'0')).join(''):(/^#[0-9a-f]{6}$/i.test(computed.color)?computed.color:'#000000');
    return {font_size:match?Number(match[1]):16,font_size_unit:match?.[2]||'px',font_size_px:Number.parseFloat(computed.fontSize)||16,
      font_weight:computed.fontWeight||'normal',font_style:computed.fontStyle||'normal',
      text_align:['left','center','right','justify'].includes(computed.textAlign)?computed.textAlign:'left',color};
  }
  /** Apply a supported text patch without copying the rendered document. */
  function applyTextPatch(element, values, baseline){
    if('text' in values)element.textContent=values.text;
    for(const [key,css] of Object.entries({font_weight:'fontWeight',font_style:'fontStyle',text_align:'textAlign',color:'color'})){
      if(key in values)element.style[css]=values[key];
    }
    if('font_size' in values||'font_size_unit' in values)element.style.fontSize=`${values.font_size??baseline.font_size}${values.font_size_unit??baseline.font_size_unit}`;
  }
  /** Present a familiar point size while preserving the source's responsive CSS unit. */
  function sizeModel(typography={}){
    const raw=Number(typography.font_size)>0?Number(typography.font_size):16;
    const unit=typography.font_size_unit||'px';
    const pixels=Number(typography.font_size_px)>0?Number(typography.font_size_px):raw*(unit==='pt'?4/3:1);
    const points=pixels*.75,display=Number(points.toFixed(1));
    return {display,patch(value){return {font_size:value===display?raw:Number((raw*value/points).toPrecision(12)),font_size_unit:unit};}};
  }
  /** Move through familiar document sizes instead of stepping raw CSS values. */
  function nextSize(value,direction){
    return direction>0?(sizeSteps.find(size=>size>value)??Math.min(2000,Math.round(value*1.1))):([...sizeSteps].reverse().find(size=>size<value)??Math.max(1,Math.floor(value*.9)));
  }
  /** Name the selected content without exposing HTML tags or selector syntax. */
  function selectionLabel(selection){
    const label=String(selection.label||'').trim(),tag=label.toLowerCase();
    if(/^h[1-6]$/.test(tag))return 'Heading';
    if(['p','span','text','tspan','strong','em','b','i','label','li','a','small'].includes(tag))return 'Text';
    if(['img','picture','image'].includes(tag))return 'Image';
    if(['svg','canvas'].includes(tag))return 'Graphic';
    if(['rect','path','line','circle','ellipse','polygon','polyline'].includes(tag))return 'Shape';
    if(tag==='button')return 'Button';
    if(['input','textarea','select'].includes(tag))return 'Field';
    if(tag==='table')return 'Table';
    if(['div','section','article','header','footer','main','g','ul','ol','nav'].includes(tag))return 'Group';
    return label && !/[#<>.[\]:]/.test(label) && tag!=='element'?label:(selection.canText?'Text':'Selection');
  }
  /** Describe the requested changes without displaying implementation details in chat. */
  function summary(intent){
    if(!intent || !['style','update','replace_text'].includes(intent.action))return [];
    const values=intent.values||{},result=[];
    if(typeof values.text==='string')result.push(`“${values.text.slice(0,160)}${values.text.length>160?'…':''}”`);
    if(typeof values.font_size==='number')result.push(['px','pt'].includes(values.font_size_unit)?`Size ${round(values.font_size*(values.font_size_unit==='px'?.75:1))}`:'Text size changed');
    if(values.font_weight)result.push(values.font_weight==='bold'||values.font_weight==='bolder'||Number(values.font_weight)>=600?'Bold':'Regular');
    if(values.font_style)result.push(values.font_style==='italic'?'Italic':'Upright');
    if(values.text_align)result.push(({left:'Align left',center:'Center',right:'Align right',justify:'Justify'})[values.text_align]||'Alignment changed');
    if(values.color)result.push('Text color changed');
    return result;
  }
  const alignmentIcon=value=>{
    const lines={left:'M4 5h16M4 10h10M4 15h16M4 20h10',center:'M4 5h16M7 10h10M4 15h16M7 20h10',right:'M4 5h16M10 10h10M4 15h16M10 20h10',justify:'M4 5h16M4 10h16M4 15h16M4 20h16'};
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><path d="${lines[value]}"/></svg>`;
  };
  /** Translate an element's iframe rectangle into the host viewport. */
  function frameAnchor(rect, frame){
    if(!rect || !frame || !['left','top','width','height'].every(key=>Number.isFinite(rect[key])))return null;
    const bounds=frame.getBoundingClientRect(),scaleX=bounds.width/(frame.offsetWidth||bounds.width),scaleY=bounds.height/(frame.offsetHeight||bounds.height);
    return {left:bounds.left+(frame.clientLeft+rect.left)*scaleX,top:bounds.top+(frame.clientTop+rect.top)*scaleY,width:Math.max(0,rect.width*scaleX),height:Math.max(0,rect.height*scaleY),bounds};
  }
  /** Keep the floating row visible beside its target without changing the canvas. */
  function toolbarPosition(anchor, size, viewport){
    const margin=8,gap=9,clamp=(value,min,max)=>Math.max(min,Math.min(value,Math.max(min,max)));
    const bounds=anchor.bounds||{left:0,top:0,width:viewport.width,height:viewport.height};
    const minX=Math.max(margin,bounds.left),maxX=Math.min(viewport.width-margin,bounds.left+bounds.width);
    const minY=Math.max(margin,bounds.top),maxY=Math.min(viewport.height-margin,bounds.top+bounds.height);
    const left=clamp(anchor.left,maxX-minX>=size.width?minX:margin,maxX-minX>=size.width?maxX-size.width:viewport.width-size.width-margin);
    // On a fitted narrow slide the row can cover the next object. Dock it
    // outside the preview when there is room, as the mobile text panel does.
    if(viewport.width<=600 && anchor.bounds){
      const belowFrame=bounds.top+bounds.height+gap,aboveFrame=bounds.top-size.height-gap;
      if(belowFrame>=margin && belowFrame+size.height<=viewport.height-48)return {left,top:belowFrame};
      if(aboveFrame>=margin && aboveFrame+size.height<=viewport.height-48)return {left,top:aboveFrame};
    }
    const above=anchor.top-size.height-gap,below=anchor.top+anchor.height+gap;
    const top=above>=minY?above:below>=minY && below+size.height<=maxY?below:clamp(above,margin,viewport.height-size.height-margin);
    return {left,top:viewport.width<=600?clamp(top,margin,viewport.height-size.height-48):top};
  }
  /** Show compact formatting next to the selection, with optional detail panels. */
  function create({container,onPreview,onSend,onCancel,getAnchor=()=>null,extraControls=null,getVoice=()=>root.slideVoice,onDockHeight=()=>{},saveLabel='Send edit'}){
    const doc=container.ownerDocument,view=doc.defaultView,controller=new view.AbortController();
    const svg=name=>root.SlideIcons?.svg(name)||'';
    container.classList.add('element-properties');container.hidden=true;
    container.innerHTML=`<span class="ep-label sr-only"></span><div class="ep-formatting" role="toolbar" aria-label="Text formatting"><button type="button" data-panel="text" title="Edit text" aria-label="Edit text" aria-expanded="false">${svg('edit')}</button><span class="ep-group ep-size" role="group" aria-label="Text size"><button type="button" data-step="-1" title="Smaller text" aria-label="Smaller text"><span class="ep-smaller" aria-hidden="true">A<sup>−</sup></span></button><input type="number" min="1" max="2000" step="any" aria-label="Font size" aria-description="Text size in points" title="Font size"><button type="button" data-step="1" title="Larger text" aria-label="Larger text"><span class="ep-larger" aria-hidden="true">A<sup>+</sup></span></button></span><span class="ep-group" role="group" aria-label="Text style"><button type="button" data-toggle="font_weight" title="Bold" aria-label="Bold"><b>B</b></button><button type="button" data-toggle="font_style" title="Italic" aria-label="Italic"><i>I</i></button><label class="ep-color" title="Text color"><span aria-hidden="true">A</span><input type="color" aria-label="Text color"></label></span><button type="button" data-panel="alignment" title="Text alignment" aria-label="Text alignment" aria-expanded="false">${alignmentIcon('left')}</button>${extraControls?`<button type="button" data-panel="objects" title="Move, rotate or remove" aria-label="Object options" aria-expanded="false">${svg('more')}</button>`:''}</div><div class="ep-panel ep-text-panel" data-detail="text" hidden><textarea aria-label="Text" rows="2" maxlength="9000"></textarea></div><span class="ep-actions" hidden><button type="button" data-action="send" title="Send edit" aria-label="Send edit">${svg('send')}</button><button type="button" data-action="cancel" title="Cancel edit" aria-label="Cancel edit">${svg('close')}</button></span><div class="ep-panel ep-alignment-panel" data-detail="alignment" hidden role="group" aria-label="Text alignment">${[['left','Align left'],['center','Center text'],['right','Align right'],['justify','Justify text']].map(([value,label])=>`<button type="button" data-align="${value}" title="${label}" aria-label="${label}" aria-pressed="false">${alignmentIcon(value)}</button>`).join('')}</div>${extraControls?'<div class="ep-panel ep-object-panel" data-detail="objects" hidden></div>':''}`;
    if(extraControls)container.querySelector('.ep-object-panel').append(extraControls);
    const save=container.querySelector('[data-action=send]');
    save.setAttribute('aria-label',saveLabel);save.title=saveLabel;
    const text=container.querySelector('textarea'),size=container.querySelector('input[type=number]'),color=container.querySelector('input[type=color]'),bold=container.querySelector('[data-toggle=font_weight]'),italic=container.querySelector('[data-toggle=font_style]'),actions=container.querySelector('.ep-actions'),textButton=container.querySelector('[data-panel=text]');
    let model=sizeModel(),state={},selectionKey=null,canText=false,frame=null,textVoice=null,selectionGeneration=0;
    function cancelVoice(){selectionGeneration++;textVoice?.cancel();}
    async function stopVoice(){return !textVoice || !getVoice()?.isActive(text) || await textVoice.stop();}
    function position(){
      frame=null;if(container.hidden || !container.isConnected){onDockHeight(0);return;}
      const anchor=getAnchor();if(!anchor){onDockHeight(0);return;}
      const viewport=view.visualViewport, mobile=view.innerWidth<=600 && !container.querySelector('[data-detail=text]').hidden;
      container.classList.toggle('ep-mobile-text',mobile);
      if(mobile){
        const left=viewport?.offsetLeft||0, top=viewport?.offsetTop||0, width=viewport?.width||view.innerWidth, height=viewport?.height||view.innerHeight;
        container.style.setProperty('--ep-available-height',`${Math.max(80,height-56)}px`);
        container.style.left=`${left+8}px`;
        const preferred=anchor.bounds.top+anchor.bounds.height+8, limit=top+height-container.offsetHeight-48;
        container.style.top=`${Math.max(top+8,Math.min(preferred,limit))}px`;
        container.style.width=`${Math.max(0,width-16)}px`;
        container.classList.remove('ep-panels-above');
        onDockHeight(Math.max(0,parseFloat(container.style.top)+container.offsetHeight-anchor.bounds.top-anchor.bounds.height+9));return;
      }
      container.style.width='';
      const point=toolbarPosition(anchor,{width:container.offsetWidth,height:container.offsetHeight},{width:view.innerWidth,height:view.innerHeight});
      container.style.left=`${point.left}px`;container.style.top=`${point.top}px`;
      const panel=container.querySelector('[data-detail]:not([hidden])');
      container.classList.toggle('ep-panels-above',!!panel && point.top>panel.offsetHeight+16 && (point.top+container.offsetHeight<=anchor.top || point.top+container.offsetHeight+panel.offsetHeight+16>view.innerHeight));
      const docked=view.innerWidth<=600&&point.top>=anchor.bounds.top+anchor.bounds.height;
      const panelHeight=panel&&!container.classList.contains('ep-panels-above')?panel.offsetHeight+6:0;
      onDockHeight(docked?container.offsetHeight+panelHeight+18:0);
    }
    function reposition(){if(frame===null)frame=view.requestAnimationFrame(position);}
    function closePanels(){if(getVoice()?.isActive(text))cancelVoice();for(const panel of container.querySelectorAll('[data-detail]'))panel.hidden=true;for(const button of container.querySelectorAll('[data-panel]'))button.setAttribute('aria-expanded','false');}
    function focusText(){if(!canText || state.busy || state.disabled)return;closePanels();textButton.setAttribute('aria-expanded','true');container.querySelector('[data-detail=text]').hidden=false;textVoice=getVoice()?.register(text)||textVoice;getVoice()?.refresh();text.focus({preventScroll:true});text.select();reposition();}
    const align=value=>{for(const button of container.querySelectorAll('[data-align]'))button.setAttribute('aria-pressed',String(button.dataset.align===value));container.querySelector('[data-panel=alignment]').innerHTML=alignmentIcon(value);};
    function resize(){if(size.validity.valid&&size.value)onPreview(model.patch(Number(size.value)));}
    function setSelection(selection){
      if(!selection){clear();return;}
      const key=selection.selector||selection.key||selection;
      if(key!==selectionKey){cancelVoice();selectionKey=key;closePanels();}
      const typography=selection.typography||{};model=sizeModel(typography);canText=selection.canText;
      container.hidden=false;container.querySelector('.ep-label').textContent=selectionLabel(selection);
      container.setAttribute('aria-label',`${selectionLabel(selection)} formatting`);
      text.value=selection.text||'';size.value=String(model.display);
      color.value=/^#[a-f\d]{6}$/i.test(typography.color)?typography.color:'#000000';
      color.parentElement.style.setProperty('--ep-color',color.value);
      align(['left','center','right','justify'].includes(typography.text_align)?typography.text_align:'left');
      bold.setAttribute('aria-pressed',String(Number(typography.font_weight)>=600||typography.font_weight==='bold'));
      italic.setAttribute('aria-pressed',String(typography.font_style==='italic'||typography.font_style==='oblique'));
      setState({dirty:false,busy:false,sent:false,disabled:false});reposition();
    }
    function setState(next){
      const showObjects=next.objectDraft && !state.objectDraft;
      state={...state,...next};actions.hidden=!state.dirty;container.classList.toggle('ep-dirty',!!state.dirty);
      for(const control of container.querySelectorAll('.ep-formatting button,.ep-formatting input,.ep-actions button,.ep-text-panel textarea,.ep-alignment-panel button'))control.disabled=!!(state.disabled||state.busy);
      const objects=container.querySelector('[data-panel=objects]');
      if(objects){
        objects.disabled=!!state.busy;
        if(showObjects){closePanels();objects.setAttribute('aria-expanded','true');container.querySelector('[data-detail=objects]').hidden=false;}
      }
      textButton.hidden=!canText;reposition();
    }
    function clear(){cancelVoice();container.hidden=true;onDockHeight(0);selectionKey=null;closePanels();}
    text.addEventListener('input',()=>onPreview({text:text.value}));
    size.addEventListener('input',resize);
    color.addEventListener('input',()=>{color.parentElement.style.setProperty('--ep-color',color.value);onPreview({color:color.value});});
    container.addEventListener('click',event=>{
      if(extraControls?.contains(event.target))return;
      const button=event.target.closest('button');if(!button||button.disabled)return;
      if(button.dataset.panel){const open=button.getAttribute('aria-expanded')!=='true';closePanels();if(open){if(button.dataset.panel==='text')focusText();else{button.setAttribute('aria-expanded','true');container.querySelector(`[data-detail=${button.dataset.panel}]`).hidden=false;}}reposition();}
      if(button.dataset.step){size.value=String(nextSize(Number(size.value)||model.display,Number(button.dataset.step)));resize();}
      if(button.dataset.toggle){const key=button.dataset.toggle,pressed=button.getAttribute('aria-pressed')!=='true';button.setAttribute('aria-pressed',String(pressed));onPreview({[key]:key==='font_weight'?(pressed?'bold':'normal'):(pressed?'italic':'normal')});}
      if(button.dataset.align){align(button.dataset.align);onPreview({text_align:button.dataset.align});closePanels();}
      if(button.dataset.action){
        const result=button.dataset.action==='send'?onSend():onCancel();
        Promise.resolve(result).then(success=>{
          if(container.hidden || textButton.disabled || (doc.activeElement!==doc.body && !actions.contains(doc.activeElement)))return;
          (success===false && !text.closest('[hidden]')?text:textButton).focus({preventScroll:true});
        });
      }
    });
    container.addEventListener('keydown',event=>{if(event.key==='Escape' && container.querySelector('[data-detail]:not([hidden])')){event.preventDefault();event.stopPropagation();const button=container.querySelector('[data-panel][aria-expanded=true]');closePanels();button?.focus();}});
    view.addEventListener('resize',reposition,{signal:controller.signal});view.visualViewport?.addEventListener('resize',reposition,{signal:controller.signal});view.visualViewport?.addEventListener('scroll',reposition,{signal:controller.signal});doc.addEventListener('scroll',reposition,{capture:true,passive:true,signal:controller.signal});
    const observer=new view.ResizeObserver(reposition);observer.observe(container);
    function destroy(){cancelVoice();onDockHeight(0);controller.abort();observer.disconnect();if(frame!==null)view.cancelAnimationFrame(frame);}
    return {setSelection,setState,clear,reposition,focusText,cancelVoice,stopVoice,destroy,selectionGeneration:()=>selectionGeneration};
  }
  const api={create,summary,sizeModel,nextSize,selectionLabel,frameAnchor,toolbarPosition,typographyOf,applyTextPatch};
  if(typeof module!=='undefined')module.exports=api;else root.ElementProperties=api;
})(typeof window==='undefined'?{}:window);
