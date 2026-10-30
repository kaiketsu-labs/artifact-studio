/* Local portable projects and audience exports. */
window.SlideProjects={
  init({toast,read,capture,restore}){
    const $=id=>document.getElementById(id),dialog=$('project-dialog');
    const {button,svg}=window.SlideIcons;
    for(const [id,icon,label,show] of [
      ['project-button','save','Project files',false],['close-project','close','Close',false]
    ])button($(id),icon,label,show);
    for(const [id,icon,label,detail,format] of [
      ['save-project','save','Save project','Slides, notes & feedback','project'],
      ['open-project','folder','Open project','.slide-studio.zip','open'],
      ['export-html','code','HTML','Images & animations','html'],
      ['export-pdf','pdf','PDF','Print or save as PDF','pdf']
    ]){
      const control=$(id);control.className='file-choice';control.dataset.format=format;
      control.innerHTML=`<span class="file-choice-icon" aria-hidden="true">${svg(icon)}</span><span class="file-choice-copy"><strong>${label}</strong><span id="${id}-detail">${detail}</span></span>`;
      control.setAttribute('aria-label',id.startsWith('export-')?`Export ${label}`:label);control.setAttribute('aria-describedby',`${id}-detail`);
    }
    let busy=false;
    function update(){const deck=read(),empty=!deck,context=$('project-context');context.textContent=deck?.title||'';context.title=context.textContent;context.hidden=empty;for(const id of ['save-project','export-html','export-pdf'])$(id).disabled=busy||empty;$('open-project').disabled=busy;$('project-file').disabled=busy;}
    function download(blob,name){const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);}
    async function responseData(response){if(response.ok)return response;const error=await response.json();throw new Error(typeof error.detail==='string'?error.detail:'Could not open the project.');}
    $('project-button').addEventListener('click',()=>{update();$('project-error').textContent='';dialog.showModal();});
    $('close-project').addEventListener('click',()=>dialog.close());
    $('save-project').addEventListener('click',async()=>{
      const deck=read();if(!deck||busy)return;busy=true;update();$('project-error').textContent='';
      try{const state=capture();const response=await responseData(await fetch(`/api/decks/${encodeURIComponent(deck.id)}/project`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(state)}));download(await response.blob(),`${deck.id}.slide-studio.zip`);toast('Project saved.');dialog.close();}
      catch(error){$('project-error').textContent=error.message;}
      finally{busy=false;update();}
    });
    $('open-project').addEventListener('click',()=>$('project-file').click());
    $('project-file').addEventListener('change',async event=>{
      const file=event.target.files[0];if(!file||busy)return;
      if(file.size>100*1024*1024){$('project-error').textContent='Choose a project file below 100 MB.';event.target.value='';return;}
      busy=true;update();$('project-error').textContent='';
      try{capture();const response=await responseData(await fetch('/api/projects/open',{method:'POST',headers:{'Content-Type':'application/zip'},body:file}));const opened=await response.json();await restore(opened);dialog.close();toast('Project opened.');}
      catch(error){$('project-error').textContent=error.message;}
      finally{busy=false;update();event.target.value='';}
    });
    $('export-html').addEventListener('click',async()=>{
      const deck=read();if(!deck||busy)return;busy=true;update();$('project-error').textContent='';
      try{const response=await responseData(await fetch(`/api/decks/${encodeURIComponent(deck.id)}/exports/html`));download(await response.blob(),`${deck.id}.html`);dialog.close();}
      catch(error){$('project-error').textContent=error.message;}
      finally{busy=false;update();}
    });
    $('export-pdf').addEventListener('click',()=>{const deck=read();if(!deck)return;window.open(`/api/decks/${encodeURIComponent(deck.id)}/exports/pdf`,'_blank','noopener');dialog.close();});
    return {update};
  }
};
