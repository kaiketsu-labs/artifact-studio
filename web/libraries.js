/* References, local persistence and explicitly opted-in GitHub backups. */
((root)=>{
  'use strict';
  function backupSummary(libraries,statuses,local='saved',unavailable=false){
    if(local==='error')return {phase:'error',icon:'info',text:'Some notes have not saved. Open storage to review them.'};
    if(local==='pending')return {phase:'pending',icon:'save',text:'Saving notes on this device…'};
    if(unavailable)return {phase:'error',icon:'info',text:'Backup status unavailable. Check the connection.'};
    if(libraries===null)return {phase:'checking',icon:'folder',text:'Checking storage and backup…'};
    const enabled=libraries.filter(item=>item.auto_sync_enabled),states=new Map(statuses.map(item=>[item.library_id,item]));
    if(!enabled.length)return {phase:'local',icon:'folder',text:'Saved on this device. Automatic GitHub backup is off.'};
    const phases=enabled.map(item=>states.get(item.id)?.phase||'pending');
    if(phases.includes('error'))return {phase:'error',icon:'info',text:'Saved on this device. GitHub backup needs attention.'};
    if(phases.includes('syncing'))return {phase:'syncing',icon:'upload',text:'Saved on this device. Backing up to GitHub…'};
    if(phases.some(phase=>phase!=='synced'))return {phase:'pending',icon:'upload',text:'Saved on this device. GitHub backup is pending.'};
    return {phase:'synced',icon:'check',text:'Saved on this device. Last GitHub backup succeeded.'};
  }
  function init({api,toast,escape,currentDeck,onNotesError=()=>{},beforeSaveWorkspace=async()=>true}){
    const $=id=>document.getElementById(id),{svg,button}=window.SlideIcons;
    button($('libraries-button'),'folder','Storage and backup',true);
    button($('close-libraries'),'close','Close');button($('add-library'),'plus','Connect library');
    let busy=false,rendered='',revision=0,libraries=null,statuses=[],localState='saved',unavailable=false,polling=false;
    const recovery=root.WorkspaceRecoveryUI.create({api,getLibraries:()=>libraries||[]});
    const archiveActions=document.createElement('div');archiveActions.className='workspace-archive-actions';
    archiveActions.innerHTML=`<button type="button" data-save-workspace>${svg('archive')}Save workspace</button><button type="button" data-restore-workspace>${svg('restore')}Restore workspace</button>`;
    $('library-list').after(archiveActions);
    archiveActions.querySelector('[data-save-workspace]').addEventListener('click',async()=>{try{if(await beforeSaveWorkspace())recovery.save();}catch(error){toast(error.message,true);}});
    archiveActions.querySelector('[data-restore-workspace]').addEventListener('click',()=>recovery.open());
    function syncOption(){
      const github=/^(?:https:\/\/github\.com\/)?[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+(?:\.git)?\/?$/.test($('library-location').value.trim());
      $('library-sync-setting').hidden=!github;if(!github)$('library-sync').checked=false;
    }
    function summary(){
      const state=backupSummary(libraries,statuses,localState,unavailable),control=$('storage-status');
      button(control,state.icon,'Storage and backup: '+state.text);control.dataset.phase=state.phase;control.title=state.text;
    }
    function timestamp(value){
      if(!value)return '';
      const date=new Date(value);return Number.isNaN(date.getTime())?'':new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'short'}).format(date);
    }
    function renderRows(force=false){
      if(!libraries||busy&&!force)return;
      const states=new Map(statuses.map(item=>[item.library_id,item]));
      const localLabel=localState==='error'?'Some notes need attention':localState==='pending'?'Saving notes…':
        libraries.some(item=>item.auto_sync_enabled)?'Saved on this device':'Saved on this device · Automatic backup off';
      const markup=libraries.map(item=>{
        const state=states.get(item.id),collection=item.id==='collection';
        const type=collection?'Working folder':item.kind==='github'?(item.auto_sync_enabled?'Private GitHub backup':item.sync_enabled?'Manual GitHub backup':'GitHub reference · read-only'):'Local reference · read-only';
        return `<article class="library-row"><span class="library-symbol">${svg(item.kind==='github'?'github':'folder')}</span>
          <div class="library-description"><strong>${escape(collection?'On this device':item.location)}</strong><small>${type}</small>
          ${collection?`<small class="library-path">${escape(item.location)}</small><small>${localLabel}</small>`:''}
          ${item.kind==='github'?`<label class="library-auto"><input type="checkbox" data-auto-sync="${escape(item.id)}" ${item.auto_sync_enabled?'checked':''}><span>Back up all artifacts</span></label>`:''}
          ${item.auto_sync_enabled?`<small class="sync-state sync-${escape(state?.phase||'pending')}">${({pending:'Waiting to back up',syncing:'Backing up…',synced:'Backed up',error:'Backup needs attention'})[state?.phase]||'Waiting to back up'}</small>
            ${state?.last_synced_at?`<small>Last backup · <time datetime="${escape(state.last_synced_at)}">${escape(timestamp(state.last_synced_at))}</time></small>`:''}
            ${state?.error?`<small class="sync-error" role="alert">${escape(state.error)}</small>`:''}`:''}</div>
          <div class="library-actions">${collection?`<button type="button" data-library="collection" data-action="copy" aria-label="Copy working folder path" title="Copy working folder path">${svg('copy')}</button>${localState==='error'?`<button type="button" data-library="collection" data-action="notes" aria-label="Review unsaved notes" title="Review unsaved notes">${svg('info')}</button>`:''}`:''}
          ${item.kind==='github'?`<a class="icon-control icon-only" data-library="${escape(item.id)}" data-action="open" href="https://github.com/${escape(item.location)}" target="_blank" rel="noopener" aria-label="Open repository" title="Open repository">${svg('external')}</a><button type="button" data-library="${escape(item.id)}" data-action="refresh" aria-label="Refresh references" title="Refresh references">${svg('refresh')}</button>`:''}
          ${item.auto_sync_enabled?`<button type="button" data-library="${escape(item.id)}" data-action="retry" aria-label="${state?.phase==='error'?'Retry GitHub backup':'Back up now'}" title="${state?.phase==='error'?'Retry GitHub backup':'Back up now'}">${svg(state?.phase==='error'?'refresh':'upload')}</button>`:item.sync_enabled?`<button type="button" data-library="${escape(item.id)}" data-action="current" aria-label="Back up current presentation" title="Back up current presentation" ${currentDeck()?'':'disabled'}>${svg('upload')}</button><button type="button" data-library="${escape(item.id)}" data-action="all" aria-label="Back up all artifacts" title="Back up all artifacts">${svg('batch')}</button>`:''}</div></article>`;
      }).join('');
      if(markup===rendered)return;
      const list=$('library-list'),focused=document.activeElement,focus=list.contains(focused)?focused?.dataset:null;
      list.innerHTML=markup;rendered=markup;
      if(busy)working(true);
      const selector=focus?.autoSync?`[data-auto-sync="${CSS.escape(focus.autoSync)}"]`:focus?.library?`[data-library="${CSS.escape(focus.library)}"][data-action="${CSS.escape(focus.action)}"]`:null;
      if(selector&&!busy)list.querySelector(selector)?.focus();
    }
    async function refresh(force=false){
      const request=++revision;
      try{
        const data=await Promise.all([api('/api/libraries'),api('/api/library-sync')]);
        if(request!==revision)return;
        [libraries,statuses]=data;unavailable=false;summary();renderRows(force);
      }catch(error){if(request!==revision)return;unavailable=true;summary();throw error;}
    }
    function working(value,message=''){
      busy=value;$('library-status').textContent=message;$('library-dialog').setAttribute('aria-busy',String(value));
      $('library-dialog').querySelectorAll('button:not(#close-libraries),input').forEach(element=>element.disabled=value||element.dataset.action==='current'&&!currentDeck());
    }
    async function open(){
      $('library-dialog').showModal();$('library-error').textContent='';syncOption();
      try{await refresh();working(busy);}catch(error){$('library-error').textContent=error.message;}
    }
    $('libraries-button').addEventListener('click',()=>void open());$('storage-status').addEventListener('click',()=>void open());
    $('close-libraries').addEventListener('click',()=>$('library-dialog').close());
    $('library-location').addEventListener('input',syncOption);
    $('library-list').addEventListener('change',async event=>{
      const control=event.target.closest('input[data-auto-sync]');if(!control||busy)return;
      const enabled=control.checked;working(true,enabled?'Enabling GitHub backup…':'Turning off automatic backup…');$('library-error').textContent='';
      try{await api(`/api/libraries/${encodeURIComponent(control.dataset.autoSync)}/settings`,{auto_sync_enabled:enabled});await refresh(true);}
      catch(error){control.checked=!enabled;$('library-error').textContent=error.message;}
      finally{working(false);}
    });
    $('library-form').addEventListener('submit',async event=>{
      event.preventDefault();if(busy)return;
      const location=$('library-location').value.trim();if(!location)return;
      working(true,'Connecting…');$('library-error').textContent='';
      try{await api('/api/libraries',{location,auto_sync_enabled:$('library-sync').checked});$('library-form').reset();syncOption();await refresh(true);toast('Library connected.');}
      catch(error){$('library-error').textContent=error.message;}
      finally{working(false);}
    });
    $('library-list').addEventListener('click',async event=>{
      const control=event.target.closest('button[data-library]');if(!control||busy)return;
      const {library,action}=control.dataset;
      if(action==='notes'){$('library-dialog').close();try{await onNotesError();}catch(error){toast(error.message,true);}return;}
      if(action==='copy'){try{await navigator.clipboard.writeText(libraries.find(item=>item.id===library).location);toast('Folder path copied.');}catch{toast('Could not copy the folder path.',true);}return;}
      const selected=currentDeck();if(action==='current'&&!selected)return;
      working(true,action==='refresh'?'Refreshing references…':'Backing up to private repository…');$('library-error').textContent='';
      try{
        if(action==='retry'){await api(`/api/libraries/${encodeURIComponent(library)}/retry`,{});await refresh(true);toast('Backup queued.');return;}
        const result=await api(`/api/libraries/${encodeURIComponent(library)}/${action==='refresh'?'refresh':'sync'}`,action==='refresh'?{}:{deck_id:action==='current'?selected.id:null});
        if(action==='refresh')toast('References updated.');else toast(result.files_changed?`Backed up ${result.files_changed} file${result.files_changed===1?'':'s'}.`:'Backup is up to date.');
        await refresh(true);
      }catch(error){$('library-error').textContent=error.message;}
      finally{working(false);}
    });
    async function poll(){
      if(polling||busy||document.visibilityState==='hidden')return;
      polling=true;
      try{await refresh();}catch(error){if($('library-dialog').open)$('library-error').textContent=error.message;}
      finally{polling=false;}
    }
    summary();void poll();let timer=setInterval(()=>void poll(),3000);
    window.addEventListener('pagehide',()=>{clearInterval(timer);timer=null;});
    window.addEventListener('pageshow',()=>{if(timer===null)timer=setInterval(()=>void poll(),3000);void poll();});
    document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')void poll();});
    window.addEventListener('online',()=>void poll());
    return {open,setLocalState(value){localState=value;summary();if($('library-dialog').open)renderRows();}};
  }
  const api={init,backupSummary};if(typeof module!=='undefined')module.exports=api;else root.SlideLibraries=api;
})(typeof window==='undefined'?{}:window);
