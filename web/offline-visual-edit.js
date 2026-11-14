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
  function create({picker,container,source,onSource,onSave}) {
    let frame, template, sourceElements=[], entries=[], selected=null, baseline=null;
    let beforeEdit, originalText;
    const properties = ElementProperties.create({
      container, getVoice:()=>null, saveLabel:'Save visual edit for later',
      getAnchor:()=>selected&&ElementProperties.frameAnchor(selected.getBoundingClientRect(),frame),
      onPreview:values=>{
        beforeEdit??=source.value;
        ElementProperties.applyTextPatch(selected,values,baseline);
        ElementProperties.applyTextPatch(sourceElements[sourceIndex(selected)],values,baseline);
        properties.setState({dirty:true});
        source.value=template.innerHTML;
      },
      onSend:onSave,
      onCancel:()=>{
        if(beforeEdit===undefined)return;
        const restoreIndex=[...template.content.querySelectorAll('*')].indexOf(originalText);
        source.value=beforeEdit;
        onSource(restoreIndex);
      },
    });
    const sourceIndex=element=>Number(element.getAttribute(indexAttribute));
    function select(element) {
      if(source.disabled||!element?.isConnected||!template.content.contains(sourceElements[sourceIndex(element)]))return;
      if(selected===element)return;
      selected?.classList.remove('offline-selected');
      selected=element;
      const index=entries.findIndex(entry=>entry.element===element);
      if(index<0){selected=null;return;}
      element.classList.add('offline-selected');
      picker.value=String(index);
      const typography=ElementProperties.typographyOf(element);
      baseline=typography;originalText=sourceElements[sourceIndex(element)];beforeEdit=undefined;
      properties.setSelection({key:element,label:element.tagName.toLowerCase(),text:element.textContent,canText:true,typography});
    }
    function reset() {
      frame=null;template=null;sourceElements=[];entries=[];selected=null;baseline=null;beforeEdit=undefined;originalText=null;
      picker.replaceChildren(new Option('Select text',''));
      picker.disabled=true;
      properties.clear();
    }
    picker.addEventListener('change',()=>{
      if(picker.value!=='')select(entries[Number(picker.value)]?.element);
    });
    return {
      reset,
      sync(){picker.disabled=source.disabled||!entries.length;properties.setState({disabled:source.disabled});},
      resize(){
        const canvas=frame?.contentDocument?.querySelector('.offline-canvas');
        if(canvas)canvas.style.transform=`scale(${Math.max(.01,frame.clientWidth/1600)})`;
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
          entries=SlideObjects.collect(doc,canvas.querySelector('.slide')||canvas).filter(({element})=>element.matches(editable)&&
            !element.querySelector(nestedObject)&&element.textContent.trim()&&element.hasAttribute(indexAttribute));
          picker.append(...entries.map(({label},index)=>new Option(label,String(index))));
          picker.disabled=source.disabled||!entries.length;
          doc.addEventListener('click',event=>{
            if(event.target.closest('a,area'))event.preventDefault();
            select(entries.find(({element})=>element.contains(event.target))?.element);
          });
          if(restoreIndex!==undefined)select(entries.find(({element})=>sourceIndex(element)===restoreIndex)?.element);
        },{once:true});
      },
    };
  }
  window.OfflineVisualEdit={create,markup};
})();
