/* Local multi-object previews, submitted as one version-anchored feedback message. */
((root)=>{
  'use strict';
  const alignments=new Set(['left','center','right','top','middle','bottom']);
  const layers=new Set(['front','back','forward','backward']);
  function readableMessage(message){
    let data;try{data=JSON.parse(message);}catch{return null;}
    if(!data||data.action!=='edit_selection'||!Array.isArray(data.operations)||!data.operations.length)return null;
    const labels={delete:'Delete selection',front:'Bring to front',forward:'Bring forward',backward:'Send backward',back:'Send to back',left:'Align left',center:'Center horizontally',right:'Align right',top:'Align top',middle:'Center vertically',bottom:'Align bottom'};
    if(data.operations.some(item=>!item||!Object.hasOwn(labels,item.action)))return null;
    return data.operations.map(item=>labels[item.action]).join(' · ');
  }
  function create({doc,getElements,getContext,onSend,onChange=()=>{},onError=()=>{}}){
    let elements=[],context=null,original=null,operations=[],past=[],future=[],busy=false,sent=false;
    const toolbar=doc.createElement('div');toolbar.className='element-tools multi-element-tools';toolbar.hidden=true;
    toolbar.setAttribute('role','toolbar');toolbar.setAttribute('aria-label','Selection edit');
    const status=doc.createElement('span');status.textContent='Unsent selection edit';toolbar.append(status);
    const buttons={};
    for(const [key,label] of [['undo','Undo'],['redo','Redo'],['send','Send edit'],['cancel','Cancel edit']]){
      const button=doc.createElement('button');button.type='button';button.textContent=label;button.setAttribute('aria-label',label);toolbar.append(button);buttons[key]=button;
    }
    doc.body.append(toolbar);
    function state(){return {dirty:operations.length>0,sent,busy,canUndo:past.length>0,canRedo:future.length>0,operations:operations.map(item=>({...item}))};}
    function update(){const current=state();toolbar.hidden=!current.dirty&&!current.canRedo&&!sent;status.textContent=sent?'Edit saved':operations.some(item=>item.request_only&&layers.has(item.action))?'Unsent selection request · arrangement is not previewed':operations.some(item=>alignments.has(item.action))?'Unsent selection request · alignment is not previewed':'Unsent selection edit';buttons.cancel.textContent=sent?'Dismiss':'Cancel edit';buttons.cancel.setAttribute('aria-label',buttons.cancel.textContent);buttons.undo.disabled=busy||!current.canUndo;buttons.redo.disabled=busy||!current.canRedo;buttons.send.disabled=busy||!current.dirty;buttons.cancel.disabled=busy;onChange(current);}
    function selection(){const unique=[...new Set(getElements())].filter(element=>element?.isConnected);return unique.filter(element=>!unique.some(other=>other!==element&&other.contains(element)));}
    function snapshot(){
      const parents=[...new Set(elements.map(element=>element.parentNode))];
      return {styles:elements.map(element=>element.getAttribute('style')),orders:parents.map(parent=>({parent,nodes:[...parent.childNodes]})),operations:operations.map(item=>({...item}))};
    }
    function restore(value){
      for(const {parent,nodes} of value.orders){if(!parent?.isConnected)continue;for(const node of nodes)if(node.parentNode===parent)parent.appendChild(node);}
      elements.forEach((element,index)=>{const style=value.styles[index];if(style===null)element.removeAttribute('style');else element.setAttribute('style',style);});
      operations=value.operations.map(item=>({...item}));
    }
    function reset(){elements=[];context=null;original=null;operations=[];past=[];future=[];sent=false;update();}
    function cancel(){if(busy)return false;if(original)restore(original);reset();return true;}
    function step(redo){if(busy)return false;const from=redo?future:past,to=redo?past:future,value=from.pop();if(!value)return false;to.push(snapshot());restore(value);update();return true;}
    function begin(){
      if(original)return true;elements=selection();if(!elements.length)return false;
      context=getContext();if(!context){elements=[];return false;}context=JSON.parse(JSON.stringify(context));original=snapshot();return true;
    }
    function arrange(action){
      const parent=elements[0].parentElement;if(!parent||elements.some(element=>element.parentElement!==parent))return false;
      const view=parent.ownerDocument.defaultView;
      if(elements.some(element=>{const style=view.getComputedStyle(element);return !['absolute','fixed'].includes(style.position)||style.zIndex!=='auto';}))return false;
      const selected=new Set(elements),nodes=[...parent.children];
      if(action==='front')for(const node of nodes)if(selected.has(node))parent.appendChild(node);
      if(action==='back'){const first=nodes.find(node=>!selected.has(node));if(first)for(const node of nodes)if(selected.has(node))parent.insertBefore(node,first);}
      if(action==='forward')for(const node of nodes.reverse())if(selected.has(node)&&node.nextElementSibling&&!selected.has(node.nextElementSibling))parent.insertBefore(node.nextElementSibling,node);
      if(action==='backward')for(const node of nodes)if(selected.has(node)&&node.previousElementSibling&&!selected.has(node.previousElementSibling))parent.insertBefore(node,node.previousElementSibling);
      return true;
    }
    function preview(action,values={}){
      if(busy||sent||(!['delete',...layers,...alignments].includes(action)))return false;
      if(action==='delete'&&operations.some(item=>item.action==='delete'))return false;
      if(!begin())return false;const before=snapshot();
      try{
        if(action==='delete'){if(elements.some(element=>element.matches('html,body,.slide,.page')))throw Error('Select specific objects to delete.');for(const element of elements)element.style.setProperty('visibility','hidden','important');}
        else if(layers.has(action)&&!arrange(action))values={...values,request_only:true};
        // Alignment is an explicit request; layout implementation remains with the agent.
        operations.push({action,...values});past.push(before);if(past.length>40)past.shift();future=[];update();return true;
      }catch(error){restore(before);if(!operations.length)reset();onError(error.message);return false;}
    }
    async function send(){
      if(busy||!operations.length)return false;busy=true;update();
      try{await onSend({context,message:JSON.stringify({action:'edit_selection',operations})});operations=[];past=[];future=[];sent=true;return true;}
      catch(error){onError(error?.message||'Could not send the selection edit.');return false;}
      finally{busy=false;update();}
    }
    buttons.undo.addEventListener('click',()=>step(false));buttons.redo.addEventListener('click',()=>step(true));buttons.send.addEventListener('click',()=>void send());buttons.cancel.addEventListener('click',cancel);
    return {preview,send,cancel,undo:()=>step(false),redo:()=>step(true),finish:()=>!operations.length&&!busy,onSelectionChange:cancel,state,toolbar};
  }
  const api={create,readableMessage};if(typeof module!=='undefined'&&module.exports)module.exports=api;root.MultiElementEdits=api;
})(typeof window==='undefined'?globalThis:window);
