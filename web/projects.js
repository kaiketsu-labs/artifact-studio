/* Local portable projects and audience exports. */
window.SlideProjects={
  init({toast,read,capture,restore}){
    const studioPath=path=>window.ArtifactSession?.path?.(path) ?? path;
    const $=id=>document.getElementById(id),dialog=$('project-dialog');
    const {button,svg}=window.SlideIcons;
    for(const [id,icon,label,show] of [
      ['project-button','external','Export presentation…',true],['close-project','close','Close',false]
    ])button($(id),icon,label,show);
    for(const [id,icon,label,detail,format] of [
      ['save-project','save','Download editable copy','.artifact-studio.zip · Slides, notes & feedback','project'],
      ['export-pptx','presentation','PowerPoint','Editable slides · .pptx','pptx'],
      ['export-html','code','HTML','Images & animations','html'],
      ['export-pdf','pdf','PDF','Print or save as PDF','pdf']
    ]){
      const control=$(id);control.className='file-choice';control.dataset.format=format;
      control.innerHTML=`<span class="file-choice-icon" aria-hidden="true">${svg(icon)}</span><span class="file-choice-copy"><strong>${label}</strong><span id="${id}-detail">${detail}</span></span>`;
      control.setAttribute('aria-label',id.startsWith('export-')?`Export ${label}`:label);control.setAttribute('aria-describedby',`${id}-detail`);
    }
    let busy=false;
    function update(){const deck=read(),empty=!deck,context=$('project-context');context.textContent=deck?.title||'';context.title=context.textContent;context.hidden=empty;for(const id of ['project-button','save-project','export-pptx','export-html','export-pdf'])$(id).disabled=busy||empty;$('project-file').disabled=busy;}
    function download(blob,name){const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);}
    async function responseData(response){
      if(response.ok)return response;
      const error=await response.json().catch(()=>null);
      throw new Error(typeof error?.detail==='string'?error.detail:'Studio could not complete the request. Try again.');
    }
    $('project-button').addEventListener('click',()=>{update();$('project-error').textContent='';dialog.showModal();});
    $('close-project').addEventListener('click',()=>dialog.close());
    $('save-project').addEventListener('click',async()=>{
      const deck=read();if(!deck||busy)return;busy=true;update();$('project-error').textContent='';
      try{const state=await capture();if(read()?.id!==deck.id)throw new Error('The presentation changed. Download a copy of the current presentation again.');const response=await responseData(await fetch(studioPath(`/api/decks/${encodeURIComponent(deck.id)}/project`),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(state)}));download(await response.blob(),`${deck.id}.artifact-studio.zip`);toast('Editable presentation copy downloaded.');dialog.close();}
      catch(error){$('project-error').textContent=error.message;}
      finally{busy=false;update();}
    });
    $('project-file').addEventListener('change',async event=>{
      const file=event.target.files[0];if(!file||busy)return;
      if(file.size>100*1024*1024){toast('Choose an editable copy below 100 MB.',true);event.target.value='';return;}
      busy=true;update();
      try{await capture();const response=await responseData(await fetch(studioPath('/api/projects/open'),{method:'POST',headers:{'Content-Type':'application/zip'},body:file}));const opened=await response.json();await restore(opened);toast('Presentation imported.');}
      catch(error){toast(error.message,true);}
      finally{busy=false;update();event.target.value='';}
    });
    for(const format of ['html','pptx']){
      const control=$('export-'+format),detail=$(control.id+'-detail'),description=detail.textContent;
      control.addEventListener('click',async()=>{
        const deck=read();
        if(!deck||busy)return;
        busy=true;update();$('project-error').textContent='';
        detail.textContent='Preparing download…';control.setAttribute('aria-busy','true');
        try{
          const path=studioPath(`/api/decks/${encodeURIComponent(deck.id)}/exports/${format}`);
          const response=await responseData(await fetch(path));
          download(await response.blob(),`${deck.id}.${format}`);
          dialog.close();
        }catch(error){$('project-error').textContent=error.message;}
        finally{detail.textContent=description;control.removeAttribute('aria-busy');busy=false;update();}
      });
    }
    $('export-pdf').addEventListener('click',()=>{const deck=read();if(!deck)return;window.open(`/api/decks/${encodeURIComponent(deck.id)}/exports/pdf?preview=1`,'_blank','noopener');dialog.close();});
    function save(){update();if($('save-project').disabled)return;$('project-error').textContent='';dialog.showModal();$('save-project').click();}
    function open(){if(!busy)$('project-file').click();}
    return {update,save,open};
  }
};
