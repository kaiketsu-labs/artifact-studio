/* Installed version and stable-release availability, shared by the library and editor. */
((root)=>{
  'use strict';
  const doc=root.document;
  function start(){
    const entries=[...doc.querySelectorAll('[data-app-about]')];if(!entries.length)return;
    const dialog=doc.createElement('dialog');dialog.className='app-about';dialog.setAttribute('aria-labelledby','app-about-title');
    dialog.innerHTML='<header><h2 id="app-about-title">About Artifact Studio</h2><button type="button" aria-label="Close">×</button></header><div class="app-version-row"><p class="app-about-version" data-installed-version>Version unavailable</p><a class="app-release-link" target="_blank" rel="noopener noreferrer" hidden>View release notes</a></div><p class="app-installed-build" hidden></p><p class="app-about-status" role="status" aria-live="polite">Checking for updates…</p><label class="app-nightly-option"><input type="checkbox" aria-describedby="app-nightly-help">Include nightly builds</label><p id="app-nightly-help" class="app-nightly-help">Preview builds from main. May be less stable.</p><p data-update-help hidden>Mac app: choose <strong>Update…</strong> in the menu bar. For other installations, see the release notes.</p>';
    doc.body.append(dialog);
    const identity=doc.createElement('section');identity.className='app-session-identity';identity.setAttribute('aria-label','Active workspace');identity.textContent='Checking workspace connection…';dialog.insertBefore(identity,dialog.children[1]);
    void root.ArtifactSession.get().then(session=>{
      const hosted=session.mode==='hosted';
      identity.replaceChildren();
      const heading=doc.createElement('strong');heading.textContent=hosted?'Hosted workspace':'Local workspace';identity.append(heading);
      const url=doc.createElement('span');url.className='app-session-url';url.textContent=session.public_url;identity.append(url);
      if(hosted){const metadata=doc.createElement('span');metadata.textContent=[session.auth_provider,session.email].filter(Boolean).join(' · ');identity.append(metadata);const shared=doc.createElement('small');shared.textContent='All members share this workspace. Files are stored on the server.';identity.append(shared);}
      doc.querySelectorAll('[data-session-mode]').forEach(node=>{node.textContent=hosted?'Hosted':'Local';node.title=hosted?'Hosted workspace':'Local workspace';});
      doc.querySelectorAll('[data-session-identity]').forEach(node=>{node.textContent=hosted?`Hosted workspace · ${session.public_url}`:`Local workspace · ${session.public_url}`;});
      doc.querySelectorAll('[data-hosted-agent-help]').forEach(node=>{node.hidden=!hosted||session.auth_provider!=='cloudflare';});
      if(hosted){
        doc.querySelectorAll('[data-local-only], #storage-status, #libraries-button, a[href="/web/copilot-setup.html"]').forEach(node=>{node.hidden=true;});
        dialog.querySelector('.app-nightly-option').hidden=true;dialog.querySelector('#app-nightly-help').hidden=true;dialog.querySelector('.app-about-status').hidden=true;
      }
      if(session.capabilities?.automatic_feedback===false)doc.querySelectorAll('[data-automatic-feedback]').forEach(node=>{node.hidden=true;});
      root.artifactStudioSession=session;
    }).catch(error=>{
      identity.textContent=error.expired?'Your session has expired. Refresh this page to sign in again.':'Workspace connection information unavailable.';
      doc.querySelectorAll('[data-session-identity]').forEach(node=>{node.textContent=identity.textContent;});
    });
    const version=dialog.querySelector('[data-installed-version]'),status=dialog.querySelector('[role="status"]'),release=dialog.querySelector('a'),help=dialog.querySelector('[data-update-help]');
    const nightly=dialog.querySelector('input'),build=dialog.querySelector('.app-installed-build');
    const triggers=[doc.getElementById('workspace-menu-button'),doc.getElementById('hub-tools-button')].filter(Boolean);
    let opener=null,timer=null,reading=false,checking=false,saving=false,sequence=0,lastInfo=null;
    const workspaceButton=doc.querySelector('[data-session-mode]');
    workspaceButton?.addEventListener('click',()=>{opener=workspaceButton;dialog.showModal();void refresh();});
    const mark='<svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><circle cx="10" cy="10" r="8"/><path d="M10 5v9m-3-3 3 3 3-3"/></svg>';
    for(const entry of entries){
      const icon=doc.createElement('span');icon.className='app-update-icon';icon.hidden=true;icon.innerHTML=mark;entry.append(icon);
      entry.addEventListener('click',event=>{
        event.preventDefault();
        const panel=entry.closest('[popover]');opener=panel?triggers.find(item=>item.getAttribute('aria-controls')===panel.id||item.getAttribute('popovertarget')===panel.id):entry;
        if(panel?.matches(':popover-open'))panel.hidePopover();
        dialog.showModal();void refresh();
      });
    }
    const connectEntry=doc.querySelector('[data-hub-connect]');
    if(connectEntry){
      const connect=doc.createElement('dialog');connect.className='app-about hub-connect';connect.setAttribute('aria-labelledby','hub-connect-title');
      connect.innerHTML='<header><h2 id="hub-connect-title">Agent connection</h2><button type="button" aria-label="Close">×</button></header><p data-hub-connection-status>Checking workspace connection…</p><label for="hub-mcp-url">MCP server URL</label><div class="hub-connect-url"><input id="hub-mcp-url" readonly><button type="button" data-copy-mcp>Copy</button></div><p data-hub-cloudflare hidden>Configure CF-Access-Client-Id and CF-Access-Client-Secret request headers in your agent environment. Credentials are not stored here.</p><p>Use Streamable HTTP and reload your agent session.</p><p><a href="https://github.com/kaiketsu-labs/artifact-studio/blob/main/docs/HOSTED.md" target="_blank" rel="noopener noreferrer">Hosted setup guide</a></p><p data-copy-status role="status"></p>';
      doc.body.append(connect);const input=connect.querySelector('input');
      connectEntry.addEventListener('click',event=>{event.preventDefault();const panel=connectEntry.closest('[popover]');if(panel?.matches(':popover-open'))panel.hidePopover();const session=root.artifactStudioSession;input.value=session?.mcp_url||`${root.location.origin}/mcp`;connect.querySelector('[data-hub-connection-status]').textContent=session?`${session.mode==='hosted'?'Hosted':'Local'} workspace · ${session.public_url}`:'Workspace connection information unavailable.';connect.querySelector('[data-hub-cloudflare]').hidden=session?.mode!=='hosted'||session.auth_provider!=='cloudflare';const guide=connect.querySelector('a');guide.textContent=session?.mode==='hosted'?'Hosted setup guide':'Agent setup guide';guide.href=session?.mode==='hosted'?'https://github.com/kaiketsu-labs/artifact-studio/blob/main/docs/HOSTED.md':'https://github.com/kaiketsu-labs/artifact-studio#connect-your-agent';connect.showModal();});
      connect.querySelector('header button').addEventListener('click',()=>connect.close());connect.addEventListener('close',()=>doc.getElementById('hub-tools-button')?.focus());
      connect.querySelector('[data-copy-mcp]').addEventListener('click',async()=>{try{await root.navigator.clipboard.writeText(input.value);connect.querySelector('[data-copy-status]').textContent='URL copied.';}catch{input.select();connect.querySelector('[data-copy-status]').textContent='Select and copy the URL.';}});
    }
    dialog.querySelector('button').addEventListener('click',()=>dialog.close());
    dialog.addEventListener('close',()=>opener?.focus());
    function render(info){
      lastInfo=info;nightly.checked=info.include_nightly===true;
      const label=`Version ${info.version}`;
      version.textContent=label;
      for(const node of doc.querySelectorAll('[data-app-version]'))node.textContent=label;
      const nightlyTag=/^nightly-\d{8}\.[1-9]\d{0,11}$/;
      const available=info.update_available===true&&(/^v\d+\.\d+\.\d+$/.test(info.latest_tag||'')||(info.include_nightly===true&&nightlyTag.test(info.latest_tag||'')));
      build.hidden=!nightlyTag.test(info.installed_tag||'');build.textContent=build.hidden?'':`Installed build: ${info.installed_tag}`;
      for(const icon of doc.querySelectorAll('.app-update-icon')){icon.hidden=!available;icon.title=available?`${info.latest_tag} available`:'';}
      for(const trigger of triggers){trigger.toggleAttribute('data-app-update',available);trigger.title=available?`Menu — ${info.latest_tag} available`:'Menu';}
      for(const entry of entries)entry.title=available?`${label} — ${info.latest_tag} available`:label;
      checking=info.update_check==='checking';
      status.textContent=available?`Update available: ${info.latest_tag}`:checking?'Checking for updates…':info.update_check==='unavailable'?'Update information is unavailable.':info.update_check==='disabled'?'Automatic update checks are off.':info.latest_tag?'You’re up to date.':`No ${info.include_nightly?'eligible':'stable'} release is available yet.`;
      help.hidden=!available;release.hidden=!available;
      if(available)release.href=`https://github.com/kaiketsu-labs/artifact-studio/releases/tag/${encodeURIComponent(info.latest_tag)}`;
      for(const trigger of triggers){trigger.setAttribute('aria-describedby','app-update-description');}
      let description=doc.getElementById('app-update-description');
      if(!description){description=doc.createElement('span');description.id='app-update-description';description.className='app-version-sr';(doc.querySelector('header')||dialog).append(description);}
      description.textContent=available?`${info.latest_tag} update available`:label;
    }
    function schedule(){clearTimeout(timer);if(!doc.hidden)timer=setTimeout(()=>void refresh(),checking?1500:60000);}
    async function refresh(){
      if(reading||saving||doc.hidden)return;reading=true;const request=++sequence;
      try{const response=await root.fetch('/api/app-info',{cache:'no-store'});if(!response.ok)throw new Error('Version unavailable');const info=await response.json();if(typeof info.version!=='string'||!info.version)throw new Error('Version unavailable');if(request===sequence)render(info);}
      catch{if(request===sequence){checking=false;if(lastInfo)render({...lastInfo,latest_tag:null,update_available:false,update_check:'unavailable'});else status.textContent='Update information is unavailable.';}}
      finally{reading=false;schedule();}
    }
    nightly.addEventListener('change',async()=>{
      const previous=lastInfo?.include_nightly===true;const include=nightly.checked;const request=++sequence;
      saving=true;nightly.disabled=true;clearTimeout(timer);
      try{const response=await root.fetch('/api/app-info/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({include_nightly:include})});if(!response.ok)throw new Error('Could not save the update preference.');const info=await response.json();if(request===sequence)render(info);}
      catch{nightly.checked=previous;status.textContent='Could not save the update preference. Try again.';}
      finally{saving=false;nightly.disabled=false;schedule();}
    });
    doc.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!doc.hidden)void refresh();});
    root.addEventListener('pagehide',()=>clearTimeout(timer));void refresh();
  }
  if(doc.readyState==='loading')doc.addEventListener('DOMContentLoaded',start,{once:true});else start();
})(window);
