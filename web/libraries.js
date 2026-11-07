/* References, local persistence and explicitly opted-in GitHub backups. */
((root)=>{
  'use strict';
  function backupSummary(libraries,statuses,local='saved',unavailable=false,fullStatuses=[]){
    if(local==='error')return {phase:'error',icon:'info',text:'Some notes have not saved. Open storage to review them.'};
    if(local==='pending')return {phase:'pending',icon:'save',text:'Saving notes on this device…'};
    if(unavailable)return {phase:'error',icon:'info',text:'Backup status unavailable. Check the connection.'};
    if(libraries===null)return {phase:'checking',icon:'folder',text:'Checking storage and backup…'};
    const enabled=libraries.filter(item=>item.auto_sync_enabled),states=new Map(statuses.map(item=>[item.library_id,item]));
    const full=fullStatuses.filter(item=>item.settings?.enabled),phases=enabled.map(item=>states.get(item.id)?.phase||'pending');
    if(!enabled.length&&!full.length)return {phase:'local',icon:'folder',text:'Saved on this device. Automatic GitHub backup is off.'};
    phases.push(...full.map(item=>item.phase==='backing_up'?'syncing':item.phase==='queued'?'pending':item.phase==='error'?'error':item.last_backup_at?'synced':'pending'));
    if(phases.includes('error'))return {phase:'error',icon:'info',text:'Saved on this device. GitHub backup needs attention.'};
    if(phases.includes('syncing'))return {phase:'syncing',icon:'upload',text:'Saved on this device. Backing up to GitHub…'};
    if(phases.some(phase=>phase!=='synced'))return {phase:'pending',icon:'upload',text:'Saved on this device. GitHub backup is pending.'};
    return {phase:'synced',icon:'check',text:'Saved on this device. Last GitHub backup succeeded.'};
  }
  /** Explain transfer direction on hover and keyboard focus without changing the action. */
  function actionTips(dialog){
    const doc=dialog.ownerDocument,view=doc.defaultView,tip=doc.createElement('div');
    tip.id='library-action-tooltip';tip.className='library-action-tooltip';tip.setAttribute('role','tooltip');tip.hidden=true;dialog.append(tip);
    const descriptions={open:'View this repository on GitHub.',refresh:'Get the latest reference copies from GitHub.',retry:'Back up saved workspace files to your private GitHub repository.',current:'Back up this presentation’s saved files to GitHub.',all:'Back up all saved workspace files to GitHub.',copy:'Copy the location of your working files.',notes:'Find a note that could not be saved.',connect:'Connect the folder or GitHub repository you entered.'};
    let control=null,timer=null,description=null;
    const clear=()=>{view.clearTimeout(timer);timer=null;};
    function hide(){
      clear();tip.hidden=true;
      if(control){if(description===null)control.removeAttribute('aria-describedby');else control.setAttribute('aria-describedby',description);}
      control=null;description=null;
    }
    function show(node){
      hide();if(!node.isConnected||!dialog.open)return;
      control=node;description=node.getAttribute('aria-describedby');node.setAttribute('aria-describedby',[description,tip.id].filter(Boolean).join(' '));
      const heading=doc.createElement('strong'),body=doc.createElement('span');heading.textContent=node.getAttribute('aria-label');
      body.textContent=node.dataset.action==='retry'&&heading.textContent==='Retry GitHub backup'?'Try backing up saved workspace files again.':descriptions[node.id==='add-library'?'connect':node.dataset.action];
      tip.replaceChildren(heading,body);tip.hidden=false;
      const bounds=node.getBoundingClientRect(),width=tip.offsetWidth,height=tip.offsetHeight;
      tip.style.left=`${Math.max(12,Math.min(bounds.right-width,view.innerWidth-width-12))}px`;
      tip.style.top=`${Math.max(12,Math.min(bounds.bottom+height+8<view.innerHeight?bounds.bottom+8:bounds.top-height-8,view.innerHeight-height-12))}px`;
    }
    const target=event=>event.target.closest('.library-actions [data-action],#add-library');
    dialog.addEventListener('pointerover',event=>{
      if(tip.contains(event.target)){clear();return;}
      const node=target(event);if(!node||event.pointerType==='touch'||node.contains(event.relatedTarget))return;
      clear();timer=view.setTimeout(()=>show(node),180);
    });
    dialog.addEventListener('pointerout',event=>{
      const node=target(event);
      if(!node&&!tip.contains(event.target)||node?.contains(event.relatedTarget)||tip.contains(event.relatedTarget))return;
      if(control===doc.activeElement&&control?.matches(':focus-visible'))return;
      clear();timer=view.setTimeout(hide,180);
    });
    dialog.addEventListener('focusin',event=>{const node=target(event);if(node?.matches(':focus-visible'))show(node);});
    dialog.addEventListener('focusout',event=>{if(target(event))hide();});
    dialog.addEventListener('keydown',event=>{if(event.key==='Escape'&&!tip.hidden){event.preventDefault();event.stopPropagation();hide();}},true);
    dialog.addEventListener('pointerdown',hide,true);dialog.addEventListener('click',hide,true);dialog.addEventListener('close',hide);dialog.addEventListener('scroll',hide,true);view.addEventListener('resize',hide);view.addEventListener('blur',hide);
    return {hide};
  }
  function init({api,toast,escape,currentDeck,onNotesError=()=>{},beforeSaveWorkspace=async()=>true}){
    const $=id=>document.getElementById(id),{svg,button}=window.SlideIcons;
    button($('libraries-button'),'folder','Storage and backup',true);
    button($('close-libraries'),'close','Close');button($('add-library'),'plus','Connect library');
    $('add-library').removeAttribute('title');
    let busy=false,revision=0,libraries=null,statuses=[],fullStatuses=[],localState='saved',unavailable=false,polling=false;
    const rowMarkup=new WeakMap(),fullDrafts=new Map();
    const tips=actionTips($('library-dialog'));
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
      const state=backupSummary(libraries,statuses,localState,unavailable,fullStatuses),control=$('storage-status');
      button(control,state.icon,'Storage and backup: '+state.text);control.dataset.phase=state.phase;control.title=state.text;
    }
    function timestamp(value,seconds=false){
      if(!value)return '';
      const date=new Date(value);return Number.isNaN(date.getTime())?'':new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:seconds?'medium':'short'}).format(date);
    }
    function fullSettings(state){return state?.settings||{enabled:false,interval_hours:24,keep_count:7};}
    function fullState(id){return fullStatuses.find(item=>item.library_id===id);}
    function fullControls(details){
      const state=fullState(details.dataset.fullBackup),draft=fullDrafts.get(details.dataset.fullBackup),running=['queued','backing_up'].includes(state?.phase);
      details.querySelectorAll('input,[data-full-save]').forEach(control=>control.disabled=busy);
      details.querySelector('[data-full-run]').disabled=busy||!fullSettings(state).enabled||draft.dirty||running;
    }
    function updateFullBackup(details){
      const state=fullState(details.dataset.fullBackup),settings=fullSettings(state),draft=fullDrafts.get(details.dataset.fullBackup);
      if(!draft.dirty&&!details.contains(document.activeElement)){
        details.querySelector('[data-full-enabled]').checked=settings.enabled;
        details.querySelector('[data-full-interval]').value=settings.interval_hours;
        details.querySelector('[data-full-keep]').value=settings.keep_count;
      }
      details.querySelector('[data-full-label]').textContent=settings.enabled?'On':'Off';
      details.querySelector('[data-full-unsaved]').hidden=!draft.dirty;
      const status=details.querySelector('[data-full-status]');status.hidden=!settings.enabled;
      const phase=({disabled:'Backups are off',scheduled:state?.last_backup_at?'Scheduled':'Waiting for first backup',queued:'Check queued',backing_up:'Checking for changes…',backed_up:'Backed up',unchanged:'No changes since last backup',error:'Backup needs attention'})[state?.phase]||'Waiting for first backup';
      const statusMarkup=`<p class="sync-state sync-${['backed_up','unchanged'].includes(state?.phase)?'synced':escape(state?.phase||'scheduled')}">${phase}</p><dl class="full-backup-times"><div><dt>Last backup</dt><dd>${state?.last_backup_at?`<time datetime="${escape(state.last_backup_at)}">${escape(timestamp(state.last_backup_at))}</time>`:'None yet'}</dd></div>${state?.next_backup_at?`<div><dt>Next check</dt><dd><time datetime="${escape(state.next_backup_at)}">${escape(timestamp(state.next_backup_at))}</time></dd></div>`:''}</dl>${state?.error?`<p class="sync-error" role="alert">${escape(state.error)}</p>`:''}${state?.warning?`<p class="full-backup-warning">${escape(state.warning)}</p>`:''}`;
      if(status.innerHTML!==statusMarkup)status.innerHTML=statusMarkup;
      details.querySelector('[data-full-error]').textContent=draft.error;
      fullControls(details);
    }
    function createFullBackup(id){
      const details=document.createElement('details');details.className='full-backup-settings';details.dataset.fullBackup=id;
      details.innerHTML=`<summary>Full workspace backups <span data-full-label>Off</span></summary><div class="full-backup-content"><label class="library-auto"><input type="checkbox" data-full-enabled><span>Enable full backups</span></label><p class="full-backup-scope">Saved files, chats, approvals, attachments and history. Stored in this private repository.</p><div class="full-backup-fields"><label>Check every (hours)<input type="number" data-full-interval min="1" max="720" step="1" value="24" required></label><label>Keep latest (backups)<input type="number" data-full-keep min="1" max="100" step="1" value="7" required></label></div><p>Only changed workspaces are backed up.</p><div class="full-backup-actions"><button type="button" data-full-save>Save settings</button><button type="button" data-full-run>Check now</button></div><p data-full-unsaved hidden>Unsaved settings</p><div class="full-backup-status" data-full-status role="status" hidden></div><p class="sync-error" data-full-error role="alert"></p><details class="full-backup-help"><summary>Details</summary><p>Studio must be running; the browser can be closed. Missed checks run at startup. Older backups are removed after a verified upload.</p></details></div>`;
      fullDrafts.set(id,{dirty:false,error:''});return details;
    }
    function renderRows(force=false){
      if(!libraries||busy&&!force)return;
      const states=new Map(statuses.map(item=>[item.library_id,item]));
      const localLabel=localState==='error'?'Some notes need attention':localState==='pending'?'Saving notes…':
        libraries.some(item=>item.auto_sync_enabled)||fullStatuses.some(item=>item.settings?.enabled)?'Saved on this device':'Saved on this device · Automatic backup off';
      const list=$('library-list'),existing=new Map([...list.children].map(row=>[row.dataset.libraryRow,row]));
      libraries.forEach(item=>{
        const state=states.get(item.id),collection=item.id==='collection';
        const lastSaved=collection?timestamp(item.last_saved_at,true):'';
        const fullEnabled=fullSettings(fullState(item.id)).enabled;
        const type=collection?'Working folder':item.kind==='github'?(fullEnabled?(item.auto_sync_enabled?'Private GitHub · artifact and full workspace backups':'Private GitHub · full workspace backups'):item.auto_sync_enabled?'Private GitHub backup':item.sync_enabled?'Manual GitHub backup':'GitHub reference · read-only'):'Local reference · read-only';
        const markup=`<span class="library-symbol">${svg(item.kind==='github'?'github':'folder')}</span>
          <div class="library-description"><strong>${escape(collection?'On this device':item.location)}</strong><small>${type}</small>
          ${collection?`<small class="library-path">${escape(item.location)}</small><small>${localLabel}</small><small class="local-save-time">Last saved · ${lastSaved?`<time datetime="${escape(item.last_saved_at)}">${escape(lastSaved)}</time>`:'—'}</small>`:''}
          ${item.kind==='github'?`<label class="library-auto"><input type="checkbox" data-auto-sync="${escape(item.id)}" ${item.auto_sync_enabled?'checked':''}><span>Back up all artifacts</span></label>`:''}
          ${item.auto_sync_enabled?`<small class="sync-state sync-${escape(state?.phase||'pending')}">${({pending:'Waiting to back up',syncing:'Backing up…',synced:'Backed up',error:'Backup needs attention'})[state?.phase]||'Waiting to back up'}</small>
            ${state?.last_synced_at?`<small>Last backup · <time datetime="${escape(state.last_synced_at)}">${escape(timestamp(state.last_synced_at))}</time></small>`:''}
            ${state?.error?`<small class="sync-error" role="alert">${escape(state.error)}</small>`:''}`:''}</div>
          <div class="library-actions">${collection?`<button type="button" data-library="collection" data-action="copy" aria-label="Copy working folder path" title="Copy working folder path">${svg('copy')}</button>${localState==='error'?`<button type="button" data-library="collection" data-action="notes" aria-label="Review unsaved notes" title="Review unsaved notes">${svg('info')}</button>`:''}`:''}
          ${item.kind==='github'?`<a class="icon-control icon-only" data-library="${escape(item.id)}" data-action="open" href="https://github.com/${escape(item.location)}" target="_blank" rel="noopener" aria-label="Open repository" title="Open repository">${svg('external')}</a><button type="button" data-library="${escape(item.id)}" data-action="refresh" aria-label="Refresh references" title="Refresh references">${svg('refresh')}</button>`:''}
          ${item.auto_sync_enabled?`<button type="button" data-library="${escape(item.id)}" data-action="retry" aria-label="${state?.phase==='error'?'Retry GitHub backup':'Back up now'}" title="${state?.phase==='error'?'Retry GitHub backup':'Back up now'}">${svg('upload')}</button>`:item.sync_enabled?`<button type="button" data-library="${escape(item.id)}" data-action="current" aria-label="Back up current presentation" title="Back up current presentation" ${currentDeck()?'':'disabled'}>${svg('upload')}</button><button type="button" data-library="${escape(item.id)}" data-action="all" aria-label="Back up all artifacts" title="Back up all artifacts">${svg('batch')}</button>`:''}</div>`;
        let row=existing.get(item.id);existing.delete(item.id);
        if(!row){row=document.createElement('article');row.className='library-row';row.dataset.libraryRow=item.id;const main=document.createElement('div');main.className='library-main';row.append(main);if(item.kind==='github')row.append(createFullBackup(item.id));list.append(row);}
        const main=row.querySelector('.library-main');
        if(rowMarkup.get(row)!==markup){
          const focused=document.activeElement,focus=main.contains(focused)?focused?.dataset:null;tips.hide();main.innerHTML=markup;rowMarkup.set(row,markup);
          for(const control of main.querySelectorAll('.library-actions [data-action]'))control.removeAttribute('title');
          const selector=focus?.autoSync?`[data-auto-sync="${CSS.escape(focus.autoSync)}"]`:focus?.library?`[data-library="${CSS.escape(focus.library)}"][data-action="${CSS.escape(focus.action)}"]`:null;
          if(selector&&!busy)main.querySelector(selector)?.focus();
        }
        const details=row.querySelector('[data-full-backup]');if(details)updateFullBackup(details);
      });
      for(const [id,row] of existing){row.remove();fullDrafts.delete(id);}
      if(busy)working(true);
    }
    async function refresh(force=false){
      const session=await root.ArtifactSession?.get();
      if(session?.capabilities?.local_admin===false)return;
      const request=++revision;
      try{
        const data=await Promise.all([api('/api/libraries'),api('/api/library-sync'),api('/api/workspace/github-backups')]);
        if(request!==revision)return;
        [libraries,statuses,fullStatuses]=data;unavailable=false;summary();renderRows(force);
      }catch(error){if(request!==revision)return;unavailable=true;summary();throw error;}
    }
    function working(value,message=''){
      busy=value;$('library-status').textContent=message;$('library-dialog').setAttribute('aria-busy',String(value));
      $('library-dialog').querySelectorAll('button:not(#close-libraries),input').forEach(element=>element.disabled=value||element.dataset.action==='current'&&!currentDeck());
      $('library-list').querySelectorAll('[data-full-backup]').forEach(fullControls);
    }
    async function open(){
      $('library-dialog').showModal();$('library-error').textContent='';syncOption();
      try{await refresh();working(busy);}catch(error){$('library-error').textContent=error.message;}
    }
    $('libraries-button').addEventListener('click',()=>void open());$('storage-status').addEventListener('click',()=>void open());
    $('close-libraries').addEventListener('click',()=>$('library-dialog').close());
    $('library-location').addEventListener('input',syncOption);
    $('library-list').addEventListener('input',event=>{
      const details=event.target.closest('[data-full-backup]');if(!details||busy)return;
      const draft=fullDrafts.get(details.dataset.fullBackup);draft.dirty=true;draft.error='';updateFullBackup(details);
    });
    $('library-list').addEventListener('click',async event=>{
      const control=event.target.closest('[data-full-save],[data-full-run]');if(!control||busy)return;
      const details=control.closest('[data-full-backup]'),id=details.dataset.fullBackup,draft=fullDrafts.get(id),save=control.hasAttribute('data-full-save');
      if(save&&![...details.querySelectorAll('input')].every(input=>input.reportValidity()))return;
      const settings={enabled:details.querySelector('[data-full-enabled]').checked,interval_hours:Number(details.querySelector('[data-full-interval]').value),keep_count:Number(details.querySelector('[data-full-keep]').value)};
      ++revision;working(true,save?'Saving backup settings…':'Queueing backup check…');draft.error='';
      try{
        const state=await api(`/api/libraries/${encodeURIComponent(id)}/full-backup/${save?'settings':'run'}`,save?settings:{});
        fullStatuses=fullStatuses.filter(item=>item.library_id!==id).concat(state);if(save)draft.dirty=false;
        summary();renderRows(true);toast(save?'Backup settings saved.':'Backup check queued.');
      }catch(error){draft.error=error.message;updateFullBackup(details);}
      finally{working(false);if($('library-dialog').open)(control.disabled?details.querySelector('summary'):control).focus();}
    });
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
