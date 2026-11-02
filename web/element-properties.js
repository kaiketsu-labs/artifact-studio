/* Compact, reusable controls for provisional element properties. */
((root)=>{
  'use strict';
  /** Describe structured style changes for the human without duplicating agent payloads. */
  function summary(intent){
    if(!intent || !['style','update','replace_text'].includes(intent.action))return [];
    const values=intent.values || {},result=[];
    if(typeof values.text==='string')result.push(`“${values.text.slice(0,160)}${values.text.length>160?'…':''}”`);
    if(typeof values.font_size==='number')result.push(`${values.font_size}${values.font_size_unit || 'px'}`);
    if(values.font_weight)result.push(values.font_weight==='bold'?'Bold':values.font_weight==='normal'?'Regular':`Weight ${values.font_weight}`);
    if(values.font_style)result.push(values.font_style==='italic'?'Italic':'Upright');
    if(values.text_align)result.push(`Align ${values.text_align}`);
    if(values.color)result.push(values.color);
    return result;
  }
  /** Render an in-flow inspector and emit only changes from the selected baseline. */
  function create({container,onPreview,onSend,onCancel}){
    container.classList.add('element-properties');container.hidden=true;
    container.innerHTML=`<span class="ep-label"></span><textarea aria-label="Text" title="Text" rows="1"></textarea><span class="ep-group"><button type="button" data-step="-1" title="Decrease font size" aria-label="Decrease font size">−</button><input type="number" min="0.01" max="10000" step="any" aria-label="Font size" title="Font size"><button type="button" data-step="1" title="Increase font size" aria-label="Increase font size">+</button><select aria-label="Font size unit" title="Font size unit">${['px','pt','em','rem','%','vw','vh','cqw','cqh'].map(unit=>`<option>${unit}</option>`).join('')}</select></span><button type="button" data-toggle="font_weight" title="Bold" aria-label="Bold"><b>B</b></button><button type="button" data-toggle="font_style" title="Italic" aria-label="Italic"><i>I</i></button><label class="ep-color" title="Text color"><span aria-hidden="true">A</span><input type="color" aria-label="Text color"></label><select class="ep-align" aria-label="Text alignment" title="Text alignment"><option value="left">Left</option><option value="center">Center</option><option value="right">Right</option><option value="justify">Justify</option></select><span class="ep-actions"><button type="button" data-action="send" title="Send edit" aria-label="Send edit">${root.SlideIcons?.svg('send')||''}</button><button type="button" data-action="cancel" title="Cancel edit" aria-label="Cancel edit">${root.SlideIcons?.svg('close')||''}</button></span>`;
    const text=container.querySelector('textarea'),size=container.querySelector('input[type=number]'),unit=container.querySelector('select[aria-label="Font size unit"]'),color=container.querySelector('input[type=color]'),align=container.querySelector('.ep-align'),bold=container.querySelector('[data-toggle=font_weight]'),italic=container.querySelector('[data-toggle=font_style]'),actions=container.querySelector('.ep-actions');
    let baseline={},values={},state={};
    function emit(key){onPreview(key==='font_size'||key==='font_size_unit'?{font_size:Number(size.value),font_size_unit:unit.value}:{[key]:values[key]});}
    function setSelection(selection){
      if(!selection){clear();return;}
      const typography=selection.typography||{};
      baseline={text:selection.text,...typography};values={...baseline};
      container.hidden=false;container.querySelector('.ep-label').textContent=selection.label||'Element';
      text.hidden=!selection.canText;text.value=selection.text||'';
      size.value=typography.font_size??16;unit.value=typography.font_size_unit||'px';
      color.value=/^#[a-f\d]{6}$/i.test(typography.color)?typography.color:'#000000';align.value=['left','center','right','justify'].includes(typography.text_align)?typography.text_align:'left';
      bold.setAttribute('aria-pressed',String(Number(typography.font_weight)>=600||typography.font_weight==='bold'));
      italic.setAttribute('aria-pressed',String(typography.font_style==='italic'||typography.font_style==='oblique'));
      setState({dirty:false,busy:false,sent:false,disabled:false});
    }
    function setState(next){state={...state,...next};actions.hidden=!state.dirty;for(const control of container.querySelectorAll('button,input,textarea,select'))control.disabled=!!(state.disabled||state.busy);}
    function clear(){container.hidden=true;baseline={};values={};}
    text.addEventListener('input',()=>{values.text=text.value;emit('text');});
    size.addEventListener('input',()=>{if(size.validity.valid&&size.value){values.font_size=Number(size.value);emit('font_size');}});
    unit.addEventListener('change',()=>{values.font_size_unit=unit.value;emit('font_size_unit');});
    color.addEventListener('input',()=>{values.color=color.value;emit('color');});
    align.addEventListener('change',()=>{values.text_align=align.value;emit('text_align');});
    container.addEventListener('click',event=>{
      const button=event.target.closest('button');if(!button||button.disabled)return;
      if(button.dataset.step){size.value=String(Math.max(.01,(Number(size.value)||0)+Number(button.dataset.step)));values.font_size=Number(size.value);emit('font_size');}
      if(button.dataset.toggle){const key=button.dataset.toggle,pressed=button.getAttribute('aria-pressed')!=='true';button.setAttribute('aria-pressed',String(pressed));values[key]=key==='font_weight'?(pressed?'bold':'normal'):(pressed?'italic':'normal');emit(key);}
      if(button.dataset.action==='send')onSend();if(button.dataset.action==='cancel')onCancel();
    });
    return {setSelection,setState,clear};
  }
  if(typeof module!=='undefined')module.exports={create,summary};else root.ElementProperties={create,summary};
})(typeof window==='undefined'?{}:window);
