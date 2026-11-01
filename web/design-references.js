/* Reference uploads and unsaved selections stay associated with their design. */
((root)=>{
  'use strict';
  function create({api,button}){
    const $=id=>document.getElementById(id), dialog=$('design-references-dialog'), input=$('design-reference-input'), save=$('save-design-references'), close=$('close-design-references'), retry=$('retry-design-references'), clear=$('clear-design-reference-selection');
    const states=new Map();let designId='';
    const state=id=>{if(!states.has(id))states.set(id,{files:[],references:null,loading:false,saving:false,removing:false,error:'',loadError:'',status:'',version:0});return states.get(id);};
    const visible=id=>dialog.open&&designId===id;
    button(close,'close','Close',false);
    function validation(files){
      if(files.length>5)return 'Choose up to 5 files.';
      if(files.some(file=>file.size>25_000_000)||files.reduce((sum,file)=>sum+file.size,0)>50_000_000)return 'Keep each file at or below 25 MB and the total at or below 50 MB.';
      return '';
    }
    function render(id){
      if(!visible(id))return;
      const current=state(id),list=$('design-reference-list');list.replaceChildren();
      if(current.references!==null&&!current.loading){
        if(!current.references.length){const empty=document.createElement('p');empty.textContent='No reference files yet.';list.append(empty);}
        else{const ul=document.createElement('ul');for(const file of current.references){
          const li=document.createElement('li'),link=document.createElement('a'),remove=document.createElement('button');
          link.href=file.url;link.textContent=file.name;link.setAttribute('download','');
          remove.type='button';remove.textContent='Remove';remove.setAttribute('aria-label','Remove '+file.name+' from design context');
          remove.disabled=current.saving||current.removing;remove.addEventListener('click',()=>void removeReference(id,file));
          li.append(link,remove);ul.append(li);
        }list.append(ul);}
      }
      $('design-reference-pending').textContent=current.files.length?'Selected: '+current.files.map(file=>file.name).join(', '):'';
      clear.hidden=!current.files.length;clear.disabled=current.saving||current.removing;
      $('design-reference-error').textContent=current.error||current.loadError;
      $('design-reference-status').textContent=current.removing?'Removing…':current.saving?'Saving…':current.loading?'Loading…':current.status;
      retry.hidden=current.loading||!current.loadError;
      input.disabled=current.saving||current.removing;
      save.disabled=current.saving||current.removing||current.loading||current.references===null||!current.files.length||!!validation(current.files);
    }
    async function load(id){
      const current=state(id),version=++current.version;current.loading=true;current.references=null;current.loadError='';current.status='';render(id);
      try{const references=await api(`/api/designs/${encodeURIComponent(id)}/references`);if(version===current.version){current.references=references;current.loading=false;}}
      catch(error){if(version===current.version){current.loadError=error.message;current.loading=false;}}
      render(id);
    }
    input.addEventListener('change',()=>{const current=state(designId);current.files=[...input.files];current.error=validation(current.files);current.status='';render(designId);});
    clear.addEventListener('click',()=>{const current=state(designId);if(current.saving||current.removing)return;current.files=[];current.error='';current.status='';input.value='';render(designId);});
    retry.addEventListener('click',()=>void load(designId));
    close.addEventListener('click',()=>dialog.close());
    dialog.addEventListener('close',()=>{const current=state(designId);if(current.loading){current.version++;current.loading=false;}input.value='';designId='';});
    save.addEventListener('click',async()=>{
      const target=designId,current=state(target),files=current.files.slice(),error=validation(files);
      if(error||!files.length){current.error=error;render(target);return;}
      current.saving=true;current.error='';current.status='';render(target);
      try{
        const encoded=await Promise.all(files.map(file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve({name:file.name,data_base64:String(reader.result).split(',')[1]});reader.onerror=()=>reject(new Error('Could not read '+file.name));reader.readAsDataURL(file);} )));
        const references=await api(`/api/designs/${encodeURIComponent(target)}/references`,{files:encoded});
        current.version++;current.loading=false;current.references=references;current.files=[];current.status='References saved.';if(visible(target))input.value='';
      }catch(error){current.error=error.message;}
      finally{current.saving=false;render(target);}
    });
    async function removeReference(target,file){
      const current=state(target);if(current.saving||current.removing)return;
      current.removing=true;current.error='';current.status='';render(target);
      try{
        const references=await api(`/api/designs/${encodeURIComponent(target)}/references/remove`,{path:file.path,expected_version:file.version});
        current.version++;current.loading=false;current.references=references;
        current.status='Reference removed from future agent context.';
      }catch(error){current.error=error.message;if(error.status===409)await load(target);}
      finally{current.removing=false;render(target);}
    }
    function open(id,name){
      if(!id)return;
      designId=id;input.value='';$('design-references-name').textContent=name||id;dialog.showModal();
      if(state(id).saving||state(id).removing)render(id);else void load(id);
    }
    return {open,hasDrafts:()=>[...states.values()].some(item=>item.files.length)};
  }
  root.SlideDesignReferences={create};
})(window);
