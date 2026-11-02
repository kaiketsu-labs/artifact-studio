/* Share audience files through familiar destinations, never send a message. */
((root) => {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  /** Require an explicit audience format whenever a presentation is included. */
  function needsFormat(target) { return !!target.presentation; }
  /** Keep exports tied to the saved artifact version shown when Share was opened. */
  function requestFor(target, format) {
    if (needsFormat(target) && !['html','pdf'].includes(format)) throw new Error('Choose HTML or PDF.');
    return {scope:target.scope,id:target.id,format:needsFormat(target)?format:null,...(target.expected_hash?{expected_hash:target.expected_hash}:{})};
  }
  /** Mount one reusable macOS-inspired share panel for library and slide workspaces. */
  function create({doc = root.document, view = root, onError = () => {}} = {}) {
    const svg = name => view.SlideIcons.svg(name);
    const dialog = doc.createElement('dialog');
    dialog.className = 'studio-share'; dialog.setAttribute('aria-labelledby','studio-share-title');
    doc.body.append(dialog);
    let target = null, format = null, prepared = null, busy = false, epoch = 0, trigger = null;
    let capabilities = null, preparing = null, nativeFile = null, generation = 0;
    async function exchange(path, body) {
      const response = await view.fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not prepare the shared file.');
      return result;
    }
    function controls() {
      for (const button of dialog.querySelectorAll('[data-destination]')) {button.disabled = busy || (needsFormat(target) && !format) || (button.dataset.destination === 'outlook' && !capabilities?.outlook) || (button.dataset.destination === 'system' && !nativeFile);if(button.dataset.destination==='outlook')button.title=capabilities?.outlook?'New Outlook message with attachment':'Requires Outlook on this Mac';}
      for (const input of dialog.querySelectorAll('[name=share-format]')) input.disabled = busy;
      dialog.setAttribute('aria-busy', String(busy));
    }
    function render() {
      const project = target.scope === 'project', presentation = needsFormat(target);
      dialog.innerHTML = `<header class="studio-share-header"><h2 id="studio-share-title">Share ${project?'project':''}</h2><button type="button" data-share-close aria-label="Close share panel">${svg('close')}</button></header>
        <div class="studio-share-item"><span class="studio-share-file" data-file-kind="${project?'zip':presentation?'slides':escape(target.kind || 'file')}">${svg(project?'folder':presentation?'slides':target.kind==='image'?'image':target.kind==='html'?'code':'file')}</span><div><strong>${escape(target.title)}</strong><span>${project?`${target.count ?? ''} artifacts · ZIP`:presentation?'Presentation':target.bundle?'File bundle · ZIP':'Saved artifact'}</span></div></div>
        ${presentation?`<fieldset class="studio-share-formats"><legend>${project?'Choose presentation format':'Choose format'}</legend>${[['html','code','HTML','Interactive · images & animations'],['pdf','pdf','PDF','All slides · still pages']].map(([value,icon,label,hint])=>`<label><input type="radio" name="share-format" value="${value}"><span class="studio-share-format-icon ${value}">${svg(icon)}</span><span><strong>${label}</strong><small>${hint}</small></span><span class="studio-share-radio" aria-hidden="true"></span></label>`).join('')}</fieldset><p class="studio-share-private">${svg('eyeOff')}No speaker notes or review chat</p>`:''}
        <div class="studio-share-destinations"><button type="button" data-destination="outlook" disabled><span class="studio-share-app outlook" aria-hidden="true"><b>O</b>${svg('mail')}</span><span>Outlook</span></button><button type="button" data-destination="save"><span class="studio-share-app save">${svg('download')}</span><span>Save file</span></button>${typeof view.navigator.share==='function'?`<button type="button" data-destination="system"><span class="studio-share-app system">${svg('share')}</span><span>More…</span></button>`:''}</div>
        <p class="studio-share-status" role="status" aria-live="polite"></p><p class="studio-share-error" role="alert" hidden></p><details class="studio-share-excluded" hidden><summary>Excluded files</summary><ul></ul></details>`;
      controls();
    }
    function status(text) { dialog.querySelector('.studio-share-status').textContent = text; }
    function showError(error) { const node=dialog.querySelector('.studio-share-error');node.textContent=error.message;node.hidden=false;onError(error); }
    async function prepare() {
      if (!prepared && !preparing) {
        status('Preparing attachment…');
        const version=generation;
        preparing=exchange('/api/shares',requestFor(target, format)).then(receipt=>{if(version===generation)prepared=receipt;return receipt;}).finally(()=>{if(version===generation)preparing=null;});
      }
      const receipt=prepared || await preparing;
      if(receipt===prepared){
        const excluded = dialog.querySelector('.studio-share-excluded');
        excluded.hidden = !receipt.excluded.length;
        excluded.querySelector('ul').innerHTML = receipt.excluded.map(path=>`<li>${escape(path)}</li>`).join('');
      }
      return receipt;
    }
    /** Prepare native sharing before the destination click retains browser activation. */
    async function warmSystem() {
      if(!dialog.querySelector('[data-destination=system]') || (needsFormat(target) && !format))return;
      const version=generation;
      try {
        const file=await prepare(),response=await view.fetch(file.download_url);
        if(!response.ok)throw new Error('The attachment is unavailable.');
        const attachment=new view.File([await response.blob()],file.filename,{type:file.media_type});
        if(version!==generation)return;
        const supported=view.navigator.canShare?.({files:[attachment]});
        nativeFile=supported?attachment:null;
        dialog.querySelector('[data-destination=system]').hidden=!supported;
        if(!busy)status('');controls();
      }catch {if(version===generation){dialog.querySelector('[data-destination=system]').hidden=true;if(!busy)status('');}}
    }
    async function deliver(destination) {
      if (busy) return;
      busy=true; controls();dialog.querySelector('.studio-share-error').hidden=true;
      try {
        if(destination==='system') {await view.navigator.share({files:[nativeFile],title:target.title});status('Shared.');return;}
        const file=await prepare();
        if (destination==='outlook') {
          status('Opening Outlook…');
          await exchange(`/api/shares/${encodeURIComponent(file.token)}/outlook`,{});
          status('Draft opened in Outlook.');
        } else if (destination==='save') {
          const link=doc.createElement('a');link.href=file.download_url;link.download=file.filename;doc.body.append(link);link.click();link.remove();
          status(file.filename);
        }
      } catch(error) { status('');if(error.name!=='AbortError')showError(error); }
      finally { busy=false;controls(); }
    }
    dialog.addEventListener('change',event=>{if(event.target.name==='share-format'){generation++;format=event.target.value;prepared=null;preparing=null;nativeFile=null;status('');controls();void warmSystem();}});
    dialog.addEventListener('click',event=>{
      if(event.target.closest('[data-share-close]')){dialog.close();return;}
      const button=event.target.closest('[data-destination]');
      if(button && !button.onclick)void deliver(button.dataset.destination);
    });
    dialog.addEventListener('close',()=>{epoch++;trigger?.focus?.();});
    return {async open(value) {
      if(dialog.open || busy)return;
      trigger=doc.activeElement;generation++;target={...value};format=null;prepared=null;preparing=null;nativeFile=null;busy=false;const version=++epoch;render();dialog.showModal();void warmSystem();
      try {capabilities=await exchange('/api/shares/capabilities');if(version===epoch)controls();}
      catch {if(version===epoch){capabilities={outlook:false};controls();}}
    }};
  }
  if(typeof module!=='undefined' && module.exports)module.exports={needsFormat,requestFor};
  else root.ArtifactSharing={create};
})(typeof window==='undefined'?globalThis:window);
