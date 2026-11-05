/* Restore local archives, repository content, or complete private GitHub backups. */
((root)=>{
  'use strict';
  function create({api,getLibraries}){
    const doc=root.document,icon=name=>root.SlideIcons.svg(name),dialog=doc.createElement('dialog');
    dialog.className='workspace-recovery';dialog.setAttribute('aria-labelledby','recovery-title');dialog.setAttribute('aria-modal','true');
    dialog.innerHTML=`<header role="presentation"><h2 id="recovery-title">Restore workspace</h2><button type="button" data-recovery-close aria-label="Close recovery">${icon('close')}</button></header><form><label for="recovery-name">New folder name</label><input id="recovery-name" name="name" value="Recovered workspace" required maxlength="100"><label class="recovery-source-label">Recovery source<select data-recovery-source aria-label="Recovery source"><option value="archive">Local archive</option><option value="current">Current repository content</option><option value="github">Full GitHub backup</option></select></label><div class="recovery-sources"><section data-recovery-panel="archive"><label class="recovery-file">${icon('archive')}<strong>Local archive</strong><small>Content, saved chats, approvals, attachments and history</small><input type="file" accept=".zip,application/zip" aria-label="Choose workspace archive"></label></section><section class="recovery-repository" data-recovery-panel="current" hidden>${icon('github')}<strong>Current repository content</strong><small>Current content and folder structure. Saved chats, approvals and history are not included.</small><select aria-label="Backup source" data-recovery-current></select><button type="submit" data-recovery-repository>Preview backup</button></section><section class="recovery-repository" data-recovery-panel="github" hidden>${icon('github')}<strong>Full GitHub backup</strong><small>Restore content, saved chats, approvals, attachments and history, including backups from another workspace or computer.</small><select aria-label="Full backup repository" data-recovery-github></select><button type="button" data-recovery-load>Load backups</button><div class="recovery-archives" hidden><label>Workspace backup<select aria-label="Workspace backup" data-recovery-asset></select></label><p class="recovery-archive-info"></p><button type="button" data-recovery-full>Preview full backup</button></div><p class="recovery-archive-empty" hidden>No full workspace backups found in this repository.</p></section></div></form><section class="recovery-preview" hidden tabindex="-1"></section><p class="recovery-status" role="status"></p><p class="recovery-error" role="alert"></p><footer role="presentation"><button type="button" data-recovery-back hidden>Choose another backup</button><button type="button" data-recovery-restore hidden>${icon('restore')}Restore files</button></footer>`;
    doc.body.append(dialog);
    const $=selector=>dialog.querySelector(selector);
    let candidate=null,busy=false,opener=null,archives=[];
    function working(value,text=''){
      busy=value;dialog.setAttribute('aria-busy',String(value));dialog.querySelectorAll('button,input,select').forEach(element=>element.disabled=value);
      $('[data-recovery-repository]').disabled=value||!$('[data-recovery-current]').options.length;
      $('[data-recovery-load]').disabled=value||!$('[data-recovery-github]').options.length;
      $('[data-recovery-full]').disabled=value||!archives.length;
      $('.recovery-status').textContent=text;
    }
    async function discard(){if(!candidate)return;const token=candidate.token;candidate=null;try{await root.fetch('/api/workspace/recovery/'+token,{method:'DELETE'});}catch{}}
    function display(prepared){
      candidate=prepared;const info=prepared.preview,section=$('.recovery-preview');section.replaceChildren();
      const strong=doc.createElement('strong');strong.textContent=`${info.projects} projects · ${info.artifacts+info.presentations} artifacts · ${info.folders} folders`;section.append(strong);
      const path=doc.createElement('p');path.className='recovery-path';path.textContent=prepared.destination;section.append(path);
      for(const message of [...info.warnings,...info.conflicts]){const p=doc.createElement('p');p.textContent=message==='Repository registrations, upload permissions and agent sessions are reset.'?'Reconnect repositories and agents after recovery.':message;section.append(p);}
      section.hidden=false;$('form').hidden=true;$('[data-recovery-restore]').hidden=!info.can_restore;$('[data-recovery-back]').hidden=false;section.focus();
    }
    async function prepare(mode,file){
      if(busy)return;$('.recovery-error').textContent='';working(true,'Checking backup…');
      try{
        const name=$('#recovery-name').value.trim();if(!name)throw new Error('Choose a name for the new workspace folder.');
        let result;
        if(mode==='archive'){
          const response=await root.fetch('/api/workspace/recovery/upload?name='+encodeURIComponent(name),{method:'POST',headers:{'Content-Type':'application/zip'},body:file});
          result=await response.json();if(!response.ok)throw new Error(result.error||result.detail||'Could not read this archive.');
        }else if(mode==='github'){
          const asset=archives.find(item=>String(item.asset_id)===$('[data-recovery-asset]').value);
          if(!asset)throw new Error('Load and choose a full workspace backup first.');
          result=await api('/api/workspace/recovery/github-archive',{library_id:$('[data-recovery-github]').value,asset_id:asset.asset_id,name});
        }else result=await api('/api/workspace/recovery/library',{library_id:$('[data-recovery-current]').value,name});
        display(result);working(false);
      }catch(error){working(false);$('.recovery-error').textContent=error.message;}
    }
    function changeSource(){
      const source=$('[data-recovery-source]').value;dialog.querySelectorAll('[data-recovery-panel]').forEach(panel=>panel.hidden=panel.dataset.recoveryPanel!==source);
      $('.recovery-error').textContent='';$('.recovery-status').textContent='';
    }
    function clearArchives(){archives=[];$('[data-recovery-asset]').replaceChildren();$('.recovery-archives').hidden=true;$('.recovery-archive-empty').hidden=true;working(false);}
    function archiveInfo(){
      const asset=archives.find(item=>String(item.asset_id)===$('[data-recovery-asset]').value);
      $('.recovery-archive-info').textContent=asset?`${asset.name} · Workspace ${asset.workspace_id} · ${Math.max(1,Math.round(asset.size/1024))} KB`:'';
    }
    async function loadArchives(){
      if(busy||!$('[data-recovery-github]').value)return;$('.recovery-error').textContent='';working(true,'Loading full backups from GitHub…');
      try{
        archives=await api(`/api/libraries/${encodeURIComponent($('[data-recovery-github]').value)}/full-backup/archives`);
        $('[data-recovery-asset]').replaceChildren(...archives.map(asset=>{
          const option=doc.createElement('option');option.value=asset.asset_id;const date=new Date(asset.created_at);
          option.textContent=`${Number.isNaN(date.getTime())?asset.created_at:date.toLocaleString()} · Workspace ${asset.workspace_id}`;return option;
        }));
        $('.recovery-archives').hidden=!archives.length;$('.recovery-archive-empty').hidden=!!archives.length;archiveInfo();working(false);
      }catch(error){clearArchives();$('.recovery-error').textContent=error.message;}
    }
    $('form').addEventListener('submit',event=>{event.preventDefault();if($('[data-recovery-source]').value==='current')void prepare('current');});
    $('input[type=file]').addEventListener('change',event=>{const file=event.target.files[0];if(file)void prepare('archive',file);});
    $('[data-recovery-source]').addEventListener('change',changeSource);
    $('[data-recovery-github]').addEventListener('change',clearArchives);
    $('[data-recovery-asset]').addEventListener('change',archiveInfo);
    $('[data-recovery-load]').addEventListener('click',()=>void loadArchives());
    $('[data-recovery-full]').addEventListener('click',()=>void prepare('github'));
    dialog.addEventListener('click',async event=>{
      if(event.target.closest('[data-recovery-close]')){if(!busy)dialog.close();return;}
      if(event.target.closest('[data-recovery-back]')){if(!busy){await discard();reset();}return;}
      if(!event.target.closest('[data-recovery-restore]')||busy||!candidate)return;
      working(true,'Restoring files…');$('.recovery-error').textContent='';
      try{
        const restored=await api('/api/workspace/recovery/'+candidate.token+'/restore',{content_hash:candidate.preview.content_hash});
        working(false,'Restored. Your current workspace is unchanged.');$('.recovery-preview').replaceChildren();
        const title=doc.createElement('strong');title.textContent='Workspace restored';const path=doc.createElement('p');path.className='recovery-path';path.textContent=restored.destination;
        const copy=doc.createElement('button');copy.type='button';copy.innerHTML=icon('copy')+'Copy folder path';copy.addEventListener('click',async()=>{try{await root.navigator.clipboard.writeText(restored.destination);copy.textContent='Copied';}catch{path.tabIndex=0;path.focus();}});
        $('.recovery-preview').append(title,path,copy);$('[data-recovery-restore]').hidden=true;$('[data-recovery-back]').hidden=true;
      }catch(error){working(false);$('.recovery-error').textContent=error.message;}
    });
    function reset(){
      $('form').hidden=false;$('.recovery-preview').hidden=true;$('.recovery-error').textContent='';$('.recovery-status').textContent='';$('[data-recovery-restore]').hidden=true;$('[data-recovery-back]').hidden=true;$('input[type=file]').value='';working(false);
    }
    dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});dialog.addEventListener('close',()=>{void discard();opener?.focus();});
    return {
      open(){
        if(busy)return;reset();
        const sources=getLibraries().filter(item=>item.id!=='collection');
        for(const [selector,items] of [['[data-recovery-current]',sources],['[data-recovery-github]',sources.filter(item=>item.kind==='github')]]){
          $(selector).replaceChildren(...items.map(item=>{const option=doc.createElement('option');option.value=item.id;option.textContent=item.location;return option;}));
        }
        $('[data-recovery-source]').value='archive';changeSource();clearArchives();opener=doc.activeElement;dialog.showModal();
      },
      save(){const link=doc.createElement('a');link.href='/api/workspace/archive';link.download='workspace.artifact-studio.zip';doc.body.append(link);link.click();link.remove();}
    };
  }
  root.WorkspaceRecoveryUI={create};
})(window);
