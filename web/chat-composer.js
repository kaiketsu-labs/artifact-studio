(function(root){
  'use strict';
  const LIMITS={count:5,file:25000000,total:50000000};
  /** Explain a rejected file selection without accepting a partial selection. */
  function selectionError(files){
    if(files.length>LIMITS.count)return 'Choose up to 5 files.';
    if(files.some(file=>file.size>LIMITS.file))return 'Keep each file at or below 25 MB.';
    if(files.reduce((sum,file)=>sum+file.size,0)>LIMITS.total)return 'Keep all files together at or below 50 MB.';
    return '';
  }
  /** Format attachment sizes consistently in the composer and message history. */
  function sizeLabel(size){return size<1000?`${size} B`:size<1000000?`${Math.ceil(size/1000)} KB`:`${(size/1000000).toFixed(1)} MB`;}
  /** Add local file selection and readable progress to the existing feedback send path. */
  function create(options){
    const {form,input,attachButton,fileInput,filesList,error: errorNode,status,key,escape,svg,onChange}=options;
    const images=root.ChatImages?.create({document:filesList.ownerDocument,escape,svg});
    const drafts=new Map(),statuses=new Map();let disabled=false;
    const files=(context=key())=>drafts.get(context)||[];
    const state=(context=key())=>{if(!statuses.has(context))statuses.set(context,{processing:false,progress:'',error:''});return statuses.get(context);};
    const changed=()=>{render();onChange?.();};
    function error(message='',context=key()){state(context).error=message;render();}
    function render(){
      const selected=files(),{processing,progress,error:message}=state();
      images?.retain([...drafts.values()].flat());
      filesList.innerHTML=selected.map((file,index)=>{
        const remove=`<button type="button" data-chat-remove="${index}" aria-label="Remove ${escape(file.name)}" title="Remove ${escape(file.name)}" ${disabled||processing?'disabled':''}>${svg('close')}</button>`;
        return (file.slideReference?`<span class="chat-file-chip is-slide-reference">${svg('star')}<span class="chat-file-description"><span class="chat-file-name">${escape(file.slideReference.title)}</span><small>Slide reference · ${escape(file.slideReference.deckTitle)}</small></span>${remove}</span>`:'') || images?.draft(file,remove) || `<span class="chat-file-chip"><span class="chat-file-icon">${svg('attach')}</span><span class="chat-file-description"><span class="chat-file-name" title="${escape(file.name)}">${escape(file.name)}</span><small>${sizeLabel(file.size)}</small></span>${remove}</span>`;
      }).join('');
      attachButton.disabled=disabled||processing;
      fileInput.disabled=disabled||processing;
      form.classList.toggle('has-files',selected.length>0);
      form.setAttribute('aria-busy',String(processing));
      status.textContent=progress;
      errorNode.textContent=message;
    }
    function add(incoming){
      if(disabled||state().processing||!key())return false;
      const selected=[...files(),...incoming],message=selectionError(selected);
      if(message){error(message);return false;}
      drafts.set(key(),selected);error();changed();return true;
    }
    attachButton.addEventListener('click',()=>fileInput.click());
    fileInput.addEventListener('change',()=>{add(Array.from(fileInput.files||[]));fileInput.value='';});
    filesList.addEventListener('click',event=>{const button=event.target.closest('[data-chat-remove]');if(!button||disabled||state().processing)return;const selected=[...files()];selected.splice(Number(button.dataset.chatRemove),1);if(selected.length)drafts.set(key(),selected);else drafts.delete(key());error();changed();});
    form.addEventListener('dragover',event=>{if(Array.from(event.dataTransfer?.types||[]).includes('Files')){event.preventDefault();if(!disabled&&!state().processing)form.classList.add('is-file-drop');}});
    form.addEventListener('dragleave',event=>{if(!form.contains(event.relatedTarget))form.classList.remove('is-file-drop');});
    form.addEventListener('drop',event=>{if(!event.dataTransfer?.files.length)return;event.preventDefault();form.classList.remove('is-file-drop');add(Array.from(event.dataTransfer.files));});
    input.addEventListener('paste',event=>{const incoming=Array.from(event.clipboardData?.files||[]);if(!incoming.length)return;if(!event.clipboardData.getData('text/plain'))event.preventDefault();add(incoming);});
    /** Encode the selected files while reporting actual local read progress. */
    async function payload(context=key()){
      const selected=[...files(context)];if(!selected.length)return [];
      const pending=state(context);pending.processing=true;error('',context);let completed=0;
      try{
        const result=[];
        for(const file of selected){
          pending.progress=`Reading ${completed+1} of ${selected.length} · ${file.name}`;render();
          const encoded=await new Promise((resolve,reject)=>{
            const reader=new FileReader();
            reader.onprogress=event=>{if(event.lengthComputable){pending.progress=`Reading ${completed+1} of ${selected.length} · ${Math.round(event.loaded/event.total*100)}%`;render();}};
            reader.onload=()=>resolve({name:file.name,data_base64:String(reader.result).split(',')[1]});
            reader.onerror=()=>reject(new Error(`Could not read ${file.name}. Remove it and attach it again.`));
            reader.onabort=()=>reject(new Error(`Reading ${file.name} was cancelled.`));
            reader.readAsDataURL(file);
          });
          result.push(encoded);completed++;
        }
        pending.progress=`Sending ${selected.length} file${selected.length===1?'':'s'}…`;render();return result;
      }catch(reason){pending.progress='';error(reason.message,context);throw reason;}
      finally{pending.processing=false;render();}
    }
    /** Display only same-origin download routes supplied by the feedback endpoint. */
    function sentFiles(comment){
      const files=(comment.files||[]).map(file=>images?.sent(file) || (/^\/api\/decks\/[a-z0-9-]+\/feedback\/\d+\/files\/\d+$/.test(file.download_url)?`<a class="chat-file-chip chat-sent-file" href="${escape(file.download_url)}" download title="Download ${escape(file.name)}">${svg('attach')}<span class="chat-file-description"><span class="chat-file-name">${escape(file.name)}</span><small>${sizeLabel(file.size)}</small></span>${svg('down')}</a>`:'')).join('');
      return files?`<div class="chat-sent-images">${files}</div>`:'';
    }

    render();
    return {
      add,payload,sentFiles,error,
      sync(state={}){disabled=!!state.disabled;render();},
      hasFiles(context=key()){return files(context).length>0;},
      hasDrafts(){return [...drafts.values()].some(items=>items.length);},
      clear(context=key()){drafts.delete(context);statuses.delete(context);changed();},
      busy(label='Sending…',context=key()){state(context).progress=label;render();},
      idle(context=key()){state(context).progress='';render();},
    };
  }
  const api={create,selectionError,sizeLabel,LIMITS};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  root.SlideChatComposer=api;
})(typeof window!=='undefined'?window:globalThis);
