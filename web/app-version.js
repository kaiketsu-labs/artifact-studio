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
    const workspaces=doc.createElement('section');workspaces.className='app-session-identity app-workspace-switcher';workspaces.hidden=true;workspaces.setAttribute('aria-label','Workspaces');identity.after(workspaces);
    const checks=doc.createElement('details');checks.className='app-security-checks';checks.innerHTML='<summary>Server checks</summary><div></div>';workspaces.after(checks);
    async function workspaceList(){
      const session=await root.ArtifactSession.get();
      if(!session.collaboration_url||sessionExpired)return;
      const response=root.ArtifactSession.observe(await root.fetch('/api/together/session',{cache:'no-store'}));
      root.ArtifactSession.assertActive();
      if(!response.ok)throw new Error('Workspaces could not be loaded.');
      const data=await response.json();
      workspaces.replaceChildren();
      workspaces.hidden=data.workspaces.length<2&&!data.can_create;
      if(workspaces.hidden)return;
      const heading=doc.createElement('strong');heading.textContent='Workspaces';
      const warning=doc.createElement('p');warning.hidden=true;warning.setAttribute('role','alert');
      warning.textContent='Finish your current draft or edit before switching workspaces.';
      workspaces.append(heading,warning);
      function canChangeWorkspace(){
        const allowed=root.dispatchEvent(new CustomEvent('studio:before-conversation-change',{cancelable:true}));
        warning.hidden=allowed;
        return allowed;
      }
      for(const item of data.workspaces){
        const link=doc.createElement('a');link.textContent=item.title;link.href=root.ArtifactSession.home(item.id);
        if(item.id===root.ArtifactSession.workspace)link.setAttribute('aria-current','page');
        link.addEventListener('click',event=>{
          if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
          if(item.id===root.ArtifactSession.workspace||!canChangeWorkspace())event.preventDefault();
        });
        workspaces.append(link);
      }
      if(!data.can_create)return;
      const form=doc.createElement('form');
      form.innerHTML='<label>Workspace name <input name="title" required maxlength="160"></label> <button type="submit">New workspace</button><p role="alert" hidden></p>';
      form.addEventListener('submit',async event=>{
        event.preventDefault();
        if(!canChangeWorkspace())return;
        const button=event.submitter,error=form.querySelector('[role="alert"]');
        button.disabled=true;error.hidden=true;
        try{
          const body=JSON.stringify({title:new FormData(form).get('title')});
          const result=root.ArtifactSession.observe(await root.fetch('/api/workspaces',{method:'POST',headers:{'Content-Type':'application/json'},body}));
          root.ArtifactSession.assertActive();
          if(!result.ok)throw new Error('The workspace could not be created.');
          root.location.assign(root.ArtifactSession.home((await result.json()).id));
        }catch(reason){error.textContent=reason.message;error.hidden=false;button.disabled=false;}
      });
      workspaces.append(form);
    }
    function renderSecurity(security){
      const body=checks.querySelector('div');body.replaceChildren();
      const counts=security?.counts;
      const summary=checks.querySelector('summary');
      summary.textContent=counts?`Server checks · ${security.summary==='review'?'Review needed':'Configuration'} · ${counts.warnings} warning${counts.warnings===1?'':'s'}, ${counts.unverified} unverified`:'Server checks · unavailable';
      if(!Array.isArray(security?.checks)){body.textContent='Server checks are unavailable. Ask your server administrator to review the deployment configuration.';return;}
      const order={warning:0,unverified:1,pass:2};
      for(const check of [...security.checks].sort((a,b)=>(order[a.status]??1)-(order[b.status]??1))){
        const item=doc.createElement('details');item.className='app-security-item';
        const title=doc.createElement('summary');title.textContent=`${check.status==='pass'?'Configured':check.status==='warning'?'Warning':'Unverified'}: ${check.title}`;
        const detail=doc.createElement('p');detail.textContent=check.detail;item.append(title,detail);body.append(item);
      }
      const guide=doc.createElement('a');guide.href='https://github.com/kaiketsu-labs/artifact-studio/blob/main/docs/HOSTED.md';guide.target='_blank';guide.rel='noopener noreferrer';guide.textContent='Hosted setup guide';body.append(guide);
    }
    let sessionExpired=false;
    const expiredBanner=doc.createElement('div');expiredBanner.className='app-session-expired';expiredBanner.setAttribute('role','alert');expiredBanner.hidden=true;
    expiredBanner.innerHTML='<span>Your session has expired. Your work is still on this page. Sign in again to continue.</span><button type="button">Sign in again</button>';
    doc.body.append(expiredBanner);expiredBanner.querySelector('button').addEventListener('click',()=>root.location.reload());
    root.addEventListener('artifact-session-expired',()=>{
      if(sessionExpired)return;
      sessionExpired=true;root.artifactStudioSession=null;expiredBanner.hidden=false;
      doc.querySelectorAll('#hub-mcp-url, #mcp-url').forEach(input=>{input.value='';input.placeholder='Session expired';});
      doc.querySelectorAll('[data-copy-mcp], #copy-url, #copy-agent-prompt').forEach(button=>{button.disabled=true;});
      doc.querySelectorAll('[data-copy-status]').forEach(node=>{node.textContent='';});
      doc.querySelectorAll('[data-session-identity], [data-hub-connection-status]').forEach(node=>{node.textContent='Your session has expired. Sign in again to continue.';});
      doc.querySelectorAll('[data-hosted-agent-help], [data-hub-cloudflare], [data-hub-provider], [data-hub-setup], [data-hosted-hooks-link], #connect-dialog [data-local-only]').forEach(node=>{node.hidden=true;});
      const editorTransport=doc.querySelector('#mcp-url')?.closest('details')?.querySelector('p');if(editorTransport)editorTransport.hidden=true;
      for(const modal of doc.querySelectorAll('dialog')){
        const notice=expiredBanner.cloneNode(true);notice.className='app-session-expired-modal';notice.querySelector('button').addEventListener('click',()=>root.location.reload());modal.prepend(notice);
      }
      identity.textContent='Your session has expired. Sign in again to continue.';
      workspaces.hidden=true;
    });
    void root.ArtifactSession.get().then(session=>{
      if(sessionExpired)return;
      const hosted=session.mode==='hosted';
      if(hosted)doc.documentElement.setAttribute('data-hosted-workspace','');
      checks.hidden=!hosted||(session.scoped_editor&&!session.security);
      if(!checks.hidden)renderSecurity(session.security);
      identity.replaceChildren();
      const heading=doc.createElement('strong');heading.textContent=hosted?'Hosted workspace':'Local workspace';identity.append(heading);
      const url=doc.createElement('span');url.className='app-session-url';url.textContent=session.public_url;identity.append(url);
      if(hosted){const metadata=doc.createElement('span');metadata.textContent=[session.auth_provider,session.email].filter(Boolean).join(' · ');identity.append(metadata);const shared=doc.createElement('small');shared.textContent=session.collaboration_url?'Share individual presentations and projects with people and agents. Files are stored on the server.':'All members share this workspace. Files are stored on the server.';identity.append(shared);}
      doc.querySelectorAll('[data-session-identity]').forEach(node=>{node.textContent=hosted?`Hosted workspace · ${session.public_url}`:`Local workspace · ${session.public_url}`;});
      doc.querySelectorAll('[data-hosted-agent-help]').forEach(node=>{node.hidden=!hosted||session.auth_provider!=='cloudflare';});
      if(hosted){
        const label='Workspace & server';
        dialog.querySelector('#app-about-title').textContent=label;
        for(const entry of entries){
          entry.querySelector('span').textContent=label;
          entry.setAttribute('aria-label',label);
        }
        doc.querySelectorAll('[data-local-only], #storage-status, #libraries-button, a[href="/web/copilot-setup.html"]').forEach(node=>{node.hidden=true;});
        dialog.querySelector('.app-nightly-option').hidden=true;dialog.querySelector('#app-nightly-help').hidden=true;dialog.querySelector('.app-about-status').hidden=true;
      }
      if(session.capabilities?.automatic_feedback===false)doc.querySelectorAll('[data-automatic-feedback]').forEach(node=>{node.hidden=true;});
      if(hosted&&session.capabilities?.hosted_hooks&&!session.collaboration_url){
        for(const panel of doc.querySelectorAll('#connect-dialog, .hub-connect')){
          const link=doc.createElement('a');link.dataset.hostedHooksLink='';link.className='app-hosted-hooks';link.href='/api/agent/hooks-client';link.download='artifact-studio-agent-hooks.zip';link.textContent='Download agent hooks';link.title='Optional client for automatic feedback in an open Codex or Claude Code session';panel.append(link);
        }
      }
      root.artifactStudioSession=session;
      mountServerVersion();
    }).catch(error=>{
      if(sessionExpired)return;
      identity.textContent=error.expired?'Your session has expired. Sign in again to continue.':'Workspace connection information unavailable.';
      doc.querySelectorAll('[data-session-identity]').forEach(node=>{node.textContent=identity.textContent;});
    });
    const version=dialog.querySelector('[data-installed-version]'),status=dialog.querySelector('[role="status"]'),release=dialog.querySelector('.app-release-link'),help=dialog.querySelector('[data-update-help]');
    const nightly=dialog.querySelector('input'),build=dialog.querySelector('.app-installed-build');
    const triggers=[doc.getElementById('workspace-menu-button'),doc.getElementById('hub-tools-button')].filter(Boolean);
    let opener=null,timer=null,reading=false,checking=false,saving=false,sequence=0,lastInfo=null;
    const serverVersion=doc.createElement('button');
    serverVersion.type='button';
    serverVersion.dataset.serverVersion='';
    serverVersion.setAttribute('aria-haspopup','dialog');
    function mountServerVersion(){
      const slot=doc.querySelector('[data-server-version-slot]');
      if(!slot||!lastInfo)return;
      if(serverVersion.parentElement!==slot)slot.append(serverVersion);
      slot.hidden=false;
    }
    function showAbout(trigger){
      opener=trigger;
      dialog.showModal();
      void refresh();
      void workspaceList().catch(()=>{workspaces.hidden=true;});
    }
    root.StudioAbout={open:showAbout,mountServerVersion};
    serverVersion.addEventListener('click',()=>showAbout(serverVersion));
    const mark='<svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><circle cx="10" cy="10" r="8"/><path d="M10 5v9m-3-3 3 3 3-3"/></svg>';
    for(const entry of entries){
      if(!entry.querySelector('.ui-icon'))entry.insertAdjacentHTML('afterbegin',root.SlideIcons.svg('info'));
      const icon=doc.createElement('span');icon.className='app-update-icon';icon.hidden=true;icon.innerHTML=mark;entry.append(icon);
      entry.addEventListener('click',event=>{
        event.preventDefault();
        const panel=entry.closest('[popover]');
        const trigger=panel?triggers.find(item=>item.getAttribute('aria-controls')===panel.id||item.getAttribute('popovertarget')===panel.id):entry;
        if(panel?.matches(':popover-open'))panel.hidePopover();
        showAbout(trigger);
      });
    }
    const connectEntry=doc.querySelector('[data-hub-connect]');
    if(connectEntry){
      const connect=doc.createElement('dialog');connect.className='app-about hub-connect';connect.setAttribute('aria-labelledby','hub-connect-title');
      connect.innerHTML='<header><h2 id="hub-connect-title">Agent connection</h2><button type="button" aria-label="Close">×</button></header><p data-hub-connection-status>Checking workspace connection…</p><label for="hub-mcp-url">MCP server URL</label><div class="hub-connect-url"><input id="hub-mcp-url" readonly><button type="button" data-copy-mcp>Copy</button></div><p data-hub-cloudflare hidden>Create a Cloudflare Access service token for your agent, then configure its client ID and secret as CF-Access-Client-Id and CF-Access-Client-Secret request headers in your agent environment.</p><p data-hub-provider hidden>Ask your server administrator how to authenticate your agent with this provider.</p><p data-hub-setup>Use Streamable HTTP and reload your agent session.</p><p data-hub-setup><a href="https://github.com/kaiketsu-labs/artifact-studio/blob/main/docs/HOSTED.md" target="_blank" rel="noopener noreferrer">Hosted setup guide</a></p><p data-copy-status role="status"></p>';
      doc.body.append(connect);const input=connect.querySelector('input');
      connectEntry.addEventListener('click',event=>{event.preventDefault();const panel=connectEntry.closest('[popover]');if(panel?.matches(':popover-open'))panel.hidePopover();const session=sessionExpired?null:root.artifactStudioSession;connect.querySelector('[data-copy-status]').textContent='';input.value=session?.mcp_url||'';input.placeholder=session?'':'Connection information unavailable';connect.querySelector('[data-copy-mcp]').disabled=!session?.mcp_url;connect.querySelector('[data-hub-connection-status]').textContent=session?`${session.mode==='hosted'?'Hosted':'Local'} workspace · ${session.public_url}`:(sessionExpired?'Your session has expired. Sign in again to continue.':'Workspace connection information unavailable.');connect.querySelector('[data-hub-cloudflare]').hidden=session?.mode!=='hosted'||session.auth_provider!=='cloudflare';connect.querySelector('[data-hub-provider]').hidden=session?.mode!=='hosted'||session.auth_provider==='cloudflare';connect.querySelectorAll('[data-hub-setup]').forEach(node=>{node.hidden=!session;});const guide=connect.querySelector('a');guide.textContent=session?.mode==='hosted'?'Hosted setup guide':'Agent setup guide';guide.href=session?.mode==='hosted'?'https://github.com/kaiketsu-labs/artifact-studio/blob/main/docs/HOSTED.md':'https://github.com/kaiketsu-labs/artifact-studio#connect-your-agent';connect.showModal();});
      connect.querySelector('header button').addEventListener('click',()=>connect.close());connect.addEventListener('close',()=>doc.getElementById('hub-tools-button')?.focus());
      connect.querySelector('[data-copy-mcp]').addEventListener('click',async()=>{try{await root.navigator.clipboard.writeText(input.value);if(!sessionExpired)connect.querySelector('[data-copy-status]').textContent='URL copied.';}catch{if(!sessionExpired){input.select();connect.querySelector('[data-copy-status]').textContent='Select and copy the URL.';}}});
    }
    dialog.querySelector('button').addEventListener('click',()=>dialog.close());
    dialog.addEventListener('close',()=>opener?.focus());
    function render(info){
      lastInfo=info;nightly.checked=info.include_nightly===true;
      const label=`Version ${info.version}`;
      version.textContent=label;
      const revision=/^[a-f0-9]{7,64}$/.test(info.build_revision||'')?info.build_revision.slice(0,7):'';
      serverVersion.textContent=`v${info.version}${revision?` · ${revision}`:''}`;
      serverVersion.setAttribute('aria-label',`${label}${revision?`, build ${revision}`:''}`);
      mountServerVersion();
      const nightlyTag=/^nightly-\d{8}\.[1-9]\d{0,11}$/;
      const available=info.update_available===true&&(/^v\d+\.\d+\.\d+$/.test(info.latest_tag||'')||(info.include_nightly===true&&nightlyTag.test(info.latest_tag||'')));
      const installedBuild=nightlyTag.test(info.installed_tag||'')?info.installed_tag:revision;
      build.hidden=!installedBuild;build.textContent=installedBuild?`Installed build: ${installedBuild}`:'';
      for(const icon of doc.querySelectorAll('.app-update-icon')){icon.hidden=!available;icon.title=available?`${info.latest_tag} available`:'';}
      for(const trigger of triggers){trigger.toggleAttribute('data-app-update',available);trigger.title=available?`Menu — ${info.latest_tag} available`:'Menu';}
      for(const entry of entries)entry.title=available?`${info.latest_tag} available`:'';
      checking=info.update_check==='checking';
      status.textContent=available?`Update available: ${info.latest_tag}`:checking?'Checking for updates…':info.update_check==='unavailable'?'Update information is unavailable.':info.update_check==='disabled'?'Automatic update checks are off.':info.latest_tag?'You’re up to date.':`No ${info.include_nightly?'eligible':'stable'} release is available yet.`;
      help.hidden=!available;release.hidden=!available;
      if(available)release.href=`https://github.com/kaiketsu-labs/artifact-studio/releases/tag/${encodeURIComponent(info.latest_tag)}`;
      for(const trigger of triggers){trigger.setAttribute('aria-describedby','app-update-description');}
      let description=doc.getElementById('app-update-description');
      if(!description){description=doc.createElement('span');description.id='app-update-description';description.className='app-version-sr';(doc.querySelector('header')||dialog).append(description);}
      description.textContent=available?`${info.latest_tag} update available`:'';
    }
    function schedule(){clearTimeout(timer);if(!doc.hidden&&!sessionExpired)timer=setTimeout(()=>void refresh(),checking?1500:60000);}
    async function refresh(){
      if(reading||saving||doc.hidden||sessionExpired)return;reading=true;const request=++sequence;
      try{const response=root.ArtifactSession.observe(await root.fetch(root.ArtifactSession.path('/api/app-info'),{cache:'no-store'}));if(!response.ok)throw new Error('Version unavailable');const info=await response.json();if(typeof info.version!=='string'||!info.version)throw new Error('Version unavailable');if(request===sequence)render(info);}
      catch{if(request===sequence){checking=false;if(lastInfo)render({...lastInfo,latest_tag:null,update_available:false,update_check:'unavailable'});else status.textContent='Update information is unavailable.';}}
      finally{reading=false;schedule();}
    }
    nightly.addEventListener('change',async()=>{
      const previous=lastInfo?.include_nightly===true;const include=nightly.checked;const request=++sequence;
      saving=true;nightly.disabled=true;clearTimeout(timer);
      try{const response=root.ArtifactSession.observe(await root.fetch(root.ArtifactSession.path('/api/app-info/settings'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({include_nightly:include})}));if(!response.ok)throw new Error('Could not save the update preference.');const info=await response.json();if(request===sequence)render(info);}
      catch{nightly.checked=previous;status.textContent='Could not save the update preference. Try again.';}
      finally{saving=false;nightly.disabled=false;schedule();}
    });
    doc.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!doc.hidden)void refresh();});
    root.addEventListener('pagehide',()=>clearTimeout(timer));void refresh();
  }
  if(doc.readyState==='loading')doc.addEventListener('DOMContentLoaded',start,{once:true});else start();
})(window);
