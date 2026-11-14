/* Keep the rendered selection separate from the authored HTML saved to the outbox. */
(() => {
  'use strict';
  const editable = 'h1,h2,h3,h4,h5,h6,p,li,span,div,label,text,tspan,[data-element-id]';
  const nestedObject = 'div,p,h1,h2,h3,h4,h5,h6,li,svg,img,video,button,input,textarea,table,ul,ol,text,tspan';
  const indexAttribute = 'data-offline-source-index';

  /** Build a script-free preview with stable links back to the unmodified source tree. */
  function markup(source, inlineAssets) {
    const template = document.createElement('template');
    template.innerHTML = source;
    [...template.content.querySelectorAll('*')].forEach((element, index) => {
      element.setAttribute(indexAttribute, String(index));
      if (element.matches('script,iframe,frame,object,embed,form,base,link,meta,foreignObject,animate,set,animateMotion,animateTransform')) {
        element.remove();
        return;
      }
      for (const attribute of [...element.attributes]) {
        const name = attribute.name.toLowerCase();
        if (name.startsWith('on') || ['srcset','srcdoc','ping','action','formaction'].includes(name) ||
            (['href','xlink:href'].includes(name) && element.matches('a,area'))) {
          element.removeAttribute(attribute.name);
        }
      }
    });
    return inlineAssets(template.innerHTML);
  }

  /** Reuse the editor toolbar while saving just the changes to the original source. */
  function create({picker,container,source,onSource,onSave,onStatus}) {
    let frame, template, sourceElements=[], entries=[], selected=null, baseline=null;
    let beforeEdit, selectedSource, savedRect, transform, removed=false;
    const tools=document.createElement('div');
    tools.className='offline-object-tools';
    tools.innerHTML=`<div role="group" aria-label="Move object">
      ${[['left','←'],['up','↑'],['down','↓'],['right','→']].map(([name,icon])=>`<button type="button" data-move="${name}" aria-label="Move ${name}">${icon}</button>`).join('')}
      </div><label>Rotation <input type="number" min="-180" max="180" step="1" value="0" aria-label="Rotate object (degrees)">°</label>
      <button type="button" data-remove>Remove object</button>`;
    const angle=tools.querySelector('input');
    const canvasWidth=1600;
    const properties = ElementProperties.create({
      container, extraControls:tools, getVoice:()=>null, saveLabel:'Save visual edit for later',
      getAnchor:()=>selected&&ElementProperties.frameAnchor(removed?savedRect:selected.getBoundingClientRect(),frame),
      onPreview:values=>{
        beforeEdit??=source.value;
        ElementProperties.applyTextPatch(selected,values,baseline);
        ElementProperties.applyTextPatch(selectedSource,values,baseline);
        savePreview();
      },
      onSend:onSave,
      onCancel:()=>{
        if(beforeEdit===undefined)return;
        source.value=beforeEdit;
        onSource(sourceIndex(selected));
      },
    });
    const sourceIndex=element=>Number(element.getAttribute(indexAttribute));
    function savePreview(){
      source.value=template.innerHTML.trim()?template.innerHTML:'<article class="slide"></article>';
      properties.setState({dirty:true});
    }
    /** Measure the selected element's local axes in the scaled slide preview. */
    function startTransform(){
      const view=frame.contentWindow;
      const authoredStyle=selected.getAttribute('style');
      const base=view.getComputedStyle(selected).transform;
      for(let node=selected;node;node=node.parentElement){
        const style=view.getComputedStyle(node);
        if(style.perspective!=='none'||!new view.DOMMatrixReadOnly(style.transform).is2D)return null;
      }
      const suffix=base==='none'?'':` ${base}`;
      selected.style.setProperty('transition','none','important');
      const probe=(x,y)=>{
        selected.style.setProperty('transform',`translate(${x}px,${y}px)${suffix}`,'important');
        return selected.getBoundingClientRect();
      };
      const origin=probe(0,0), right=probe(10,0), down=probe(0,10);
      if(authoredStyle===null)selected.removeAttribute('style');else selected.setAttribute('style',authoredStyle);
      const unit=10*Math.max(.01,frame.clientWidth/canvasWidth);
      const basis={xx:(right.left-origin.left)/unit,yx:(right.top-origin.top)/unit,xy:(down.left-origin.left)/unit,yy:(down.top-origin.top)/unit};
      return SlideEdits.localDelta(0,0,basis)?{suffix,basis,x:0,y:0,angle:0}:null;
    }
    function applyTransform(){
      beforeEdit??=source.value;
      const {x,y,angle,suffix}=transform;
      const value=`translate(${x}px,${y}px)${suffix} rotate(${angle}deg)`;
      selected.style.setProperty('transform',value,'important');
      selectedSource.style.setProperty('transform',value,'important');
      savePreview();
    }
    tools.addEventListener('click',event=>{
      const button=event.target.closest('button');
      if(!selected||removed||source.disabled||!button)return;
      if(button.hasAttribute('data-remove')){
        beforeEdit??=source.value;
        savedRect=selected.getBoundingClientRect();
        selected.hidden=true;
        selected.style.setProperty('display','none','important');
        selectedSource.remove();removed=true;
        container.classList.add('offline-object-removed');
        savePreview();
        container.querySelector('[data-action=cancel]').focus({preventScroll:true});
        return;
      }
      if(!button.dataset.move)return;
      transform??=startTransform();
      if(!transform){onStatus('This object uses a transform that cannot be moved offline.');return;}
      const step=event.shiftKey?40:10;
      const [x,y]={left:[-step,0],right:[step,0],up:[0,-step],down:[0,step]}[button.dataset.move];
      const delta=SlideEdits.localDelta(x,y,transform.basis);
      if(delta){transform.x+=delta.x;transform.y+=delta.y;applyTransform();}
    });
    angle.addEventListener('input',()=>{
      if(!selected||removed||source.disabled||!angle.validity.valid||angle.value==='')return;
      transform??=startTransform();
      if(!transform){onStatus('This object uses a transform that cannot be rotated offline.');return;}
      transform.angle=Number(angle.value);applyTransform();
    });
    function keepPendingSelection(){
      picker.value=String(entries.findIndex(entry=>entry.element===selected));
      onStatus('Save or cancel this edit before selecting another object.');
    }
    function select(element) {
      if(source.disabled||!element?.isConnected||!template.content.contains(sourceElements[sourceIndex(element)]))return;
      if(selected===element)return;
      if(beforeEdit!==undefined){keepPendingSelection();return;}
      selected?.classList.remove('offline-selected');
      selected=element;
      const index=entries.findIndex(entry=>entry.element===element);
      if(index<0){selected=null;return;}
      removed=false;transform=null;angle.value='0';
      element.classList.add('offline-selected');
      picker.value=String(index);
      const {canText,label}=entries[index];
      const typography=canText?ElementProperties.typographyOf(element):{};
      baseline=typography;selectedSource=sourceElements[sourceIndex(element)];beforeEdit=undefined;
      container.classList.toggle('offline-object-only',!canText);
      container.classList.remove('offline-object-removed');
      properties.setSelection({key:element,label,text:element.textContent,canText,typography});
    }
    function reset() {
      frame=null;template=null;sourceElements=[];entries=[];selected=null;baseline=null;beforeEdit=undefined;selectedSource=null;removed=false;transform=null;
      container.classList.remove('offline-object-only','offline-object-removed');
      picker.replaceChildren(new Option('Select an object',''));
      picker.disabled=true;
      properties.clear();
    }
    picker.addEventListener('change',()=>{
      if(picker.value!=='')select(entries[Number(picker.value)]?.element);
      else if(beforeEdit!==undefined)keepPendingSelection();
      else {selected?.classList.remove('offline-selected');selected=null;properties.clear();}
    });
    return {
      reset,
      sync(){picker.disabled=source.disabled||!entries.length;properties.setState({disabled:source.disabled});for(const control of tools.querySelectorAll('button,input'))control.disabled=source.disabled;},
      resize(){
        const canvas=frame?.contentDocument?.querySelector('.offline-canvas');
        if(canvas)canvas.style.transform=`scale(${Math.max(.01,frame.clientWidth/canvasWidth)})`;
        properties.reposition();
      },
      bind(next, html, restoreIndex) {
        reset();
        frame=next;
        template=document.createElement('template');
        template.innerHTML=html;
        sourceElements=[...template.content.querySelectorAll('*')];
        frame.addEventListener('load',()=>{
          if(frame!==next||!next.isConnected)return;
          const doc=next.contentDocument;
          const canvas=doc?.querySelector('.offline-canvas');
          if(!canvas)return;
          entries=SlideObjects.collect(doc,canvas.querySelector('.slide')||canvas)
            .filter(({element})=>element.hasAttribute(indexAttribute)&&element.style)
            .map(entry=>({...entry,canText:entry.element.matches(editable)&&!entry.element.querySelector(nestedObject)&&!!entry.element.textContent.trim()}));
          picker.append(...entries.map(({label},index)=>new Option(label,String(index))));
          picker.disabled=source.disabled||!entries.length;
          doc.addEventListener('click',event=>{
            if(event.target.closest('a,area'))event.preventDefault();
            select(entries.findLast(({element})=>element.contains(event.target))?.element);
          });
          if(restoreIndex!==undefined)select(entries.find(({element})=>sourceIndex(element)===restoreIndex)?.element);
        },{once:true});
      },
    };
  }
  window.OfflineVisualEdit={create,markup};
})();
