/* Familiar text controls over revision-bound, provisional element edits. */
((root)=>{
  'use strict';
  const sizeSteps=[8,9,10,11,12,14,16,18,20,22,24,26,28,32,36,40,44,48,54,60,66,72,80,88,96,112,128,144,192,256,320,400,512,720];
  const round=value=>Number(value.toFixed(3));
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
  /** Render a text editor with document-style formatting and explicit Send/Cancel. */
  function create({container,onPreview,onSend,onCancel}){
    container.classList.add('element-properties');container.hidden=true;
    container.innerHTML=`<div class="ep-heading"><span class="ep-label"></span><span class="ep-actions"><button type="button" data-action="send" title="Send edit" aria-label="Send edit">${root.SlideIcons?.svg('send')||''}</button><button type="button" data-action="cancel" title="Cancel edit" aria-label="Cancel edit">${root.SlideIcons?.svg('close')||''}</button></span></div><textarea aria-label="Text" title="Text" rows="1"></textarea><div class="ep-formatting" role="toolbar" aria-label="Text formatting"><span class="ep-group ep-size" role="group" aria-label="Text size"><button type="button" data-step="-1" title="Smaller text" aria-label="Smaller text"><span class="ep-smaller" aria-hidden="true">A<sup>−</sup></span></button><input type="number" min="1" max="2000" step="any" aria-label="Font size" aria-description="Text size in points" title="Font size"><button type="button" data-step="1" title="Larger text" aria-label="Larger text"><span class="ep-larger" aria-hidden="true">A<sup>+</sup></span></button></span><span class="ep-group" role="group" aria-label="Text style"><button type="button" data-toggle="font_weight" title="Bold" aria-label="Bold"><b>B</b></button><button type="button" data-toggle="font_style" title="Italic" aria-label="Italic"><i>I</i></button><label class="ep-color" title="Text color"><span aria-hidden="true">A</span><input type="color" aria-label="Text color"></label></span><span class="ep-group ep-align" role="group" aria-label="Text alignment">${[['left','Align left'],['center','Center text'],['right','Align right'],['justify','Justify text']].map(([value,label])=>`<button type="button" data-align="${value}" title="${label}" aria-label="${label}" aria-pressed="false">${alignmentIcon(value)}</button>`).join('')}</span></div>`;
    const text=container.querySelector('textarea'),size=container.querySelector('input[type=number]'),color=container.querySelector('input[type=color]'),bold=container.querySelector('[data-toggle=font_weight]'),italic=container.querySelector('[data-toggle=font_style]'),actions=container.querySelector('.ep-actions');
    let model=sizeModel(),state={};
    const align=value=>{for(const button of container.querySelectorAll('[data-align]'))button.setAttribute('aria-pressed',String(button.dataset.align===value));};
    function resize(){if(size.validity.valid&&size.value)onPreview(model.patch(Number(size.value)));}
    function setSelection(selection){
      if(!selection){clear();return;}
      const typography=selection.typography||{};model=sizeModel(typography);
      container.hidden=false;container.querySelector('.ep-label').textContent=selectionLabel(selection);
      text.hidden=!selection.canText;text.value=selection.text||'';size.value=String(model.display);
      color.value=/^#[a-f\d]{6}$/i.test(typography.color)?typography.color:'#000000';
      color.parentElement.style.setProperty('--ep-color',color.value);
      align(['left','center','right','justify'].includes(typography.text_align)?typography.text_align:'left');
      bold.setAttribute('aria-pressed',String(Number(typography.font_weight)>=600||typography.font_weight==='bold'));
      italic.setAttribute('aria-pressed',String(typography.font_style==='italic'||typography.font_style==='oblique'));
      setState({dirty:false,busy:false,sent:false,disabled:false});
    }
    function setState(next){state={...state,...next};actions.hidden=!state.dirty;for(const control of container.querySelectorAll('button,input,textarea'))control.disabled=!!(state.disabled||state.busy);}
    function clear(){container.hidden=true;}
    text.addEventListener('input',()=>onPreview({text:text.value}));
    size.addEventListener('input',resize);
    color.addEventListener('input',()=>{color.parentElement.style.setProperty('--ep-color',color.value);onPreview({color:color.value});});
    container.addEventListener('click',event=>{
      const button=event.target.closest('button');if(!button||button.disabled)return;
      if(button.dataset.step){size.value=String(nextSize(Number(size.value)||model.display,Number(button.dataset.step)));resize();}
      if(button.dataset.toggle){const key=button.dataset.toggle,pressed=button.getAttribute('aria-pressed')!=='true';button.setAttribute('aria-pressed',String(pressed));onPreview({[key]:key==='font_weight'?(pressed?'bold':'normal'):(pressed?'italic':'normal')});}
      if(button.dataset.align){align(button.dataset.align);onPreview({text_align:button.dataset.align});}
      if(button.dataset.action==='send')onSend();if(button.dataset.action==='cancel')onCancel();
    });
    return {setSelection,setState,clear};
  }
  const api={create,summary,sizeModel,nextSize,selectionLabel};
  if(typeof module!=='undefined')module.exports=api;else root.ElementProperties=api;
})(typeof window==='undefined'?{}:window);
