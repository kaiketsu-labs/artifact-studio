/* The artifact library and adaptive review workspace share existing studio utilities. */
((root) => {
  'use strict';
  const kinds = {
    presentation: {label:'Presentations', singular:'Presentation', icon:'slides'},
    html: {label:'HTML', singular:'HTML page', icon:'code'},
    markdown: {label:'Markdown', singular:'Markdown', icon:'notes'},
    image: {label:'Images', singular:'Image', icon:'image'},
    file: {label:'Files', singular:'File', icon:'archive'},
  };
  const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
  const kindOf = item => ({svg:'image',document:'markdown',other:'file'}[item.kind] || (kinds[item.kind] ? item.kind : 'file'));
  const encodePath = value => String(value).split('/').map(encodeURIComponent).join('/');
  const artifactPath = (id, suffix='') => `/api/artifacts/${encodeURIComponent(id)}${suffix}`;
  const filePath = (item, path=item.entrypoint) => artifactPath(item.id, `/files/${encodePath(path)}`) + (item.revision ? `?revision=${encodeURIComponent(item.revision)}` : '');
  const targetLabel = target => target.label || target.quote?.slice(0,75) || target.selector || (target.kind === 'region' ? 'Image region' : 'Selection');
  const groupTargets = targets => targets.length > 1 ? {kind:'group',elements:targets} : targets[0] || null;
  /** Remove UI-only bookkeeping before submitting a strict, portable target envelope. */
  function feedbackTarget(target) {
    if (!target) return null;
    const {block_index, ...result}=target;
    if (result.elements) result.elements=result.elements.map(feedbackTarget);
    return result;
  }
  const targetKey = target => target.selector || JSON.stringify([target.kind,target.block_index,target.start,target.end,target.quote,target.region || [target.x,target.y,target.width,target.height]]);
  /** Add or toggle a selection without losing the anchors of other selected elements. */
  function selectTargets(targets, target, additive=false) {
    if (!additive) return [target];
    const key = targetKey(target), found = targets.some(item => targetKey(item) === key);
    return found ? targets.filter(item => targetKey(item) !== key) : [...targets,target].slice(0,100);
  }
  /** Filter the real registry without treating project names as artifact titles. */
  function filterArtifacts(artifacts, {project='all',kind='all',search=''}) {
    const query = search.trim().toLocaleLowerCase();
    return artifacts.filter(item => (project === 'all' || item.project_id === project) && (kind === 'all' || kindOf(item) === kind) && `${item.title} ${item.project_title || ''} ${item.entrypoint || ''}`.toLocaleLowerCase().includes(query));
  }
  /** Search registered visual languages independently of presentation artifacts. */
  function filterDesigns(designs, search='') {
    const query=search.trim().toLocaleLowerCase();
    return designs.filter(item=>`${item.name} ${item.description}`.toLocaleLowerCase().includes(query));
  }
  /** Resolve bundle-local links while rejecting schemes and traversal outside the bundle. */
  function bundlePath(entrypoint, value) {
    if (!value || /^(?:[a-z][a-z\d+.-]*:|\/\/|\/|#)/i.test(value)) return null;
    const clean = value.split(/[?#]/)[0], parts = entrypoint.split('/').slice(0,-1);
    for (const part of clean.split('/')) {
      let decoded;
      try { decoded=decodeURIComponent(part); } catch { return null; }
      if (!decoded || decoded === '.') continue;
      if (decoded === '..') { if (!parts.length) return null; parts.pop(); }
      else if (/[\\\0]/.test(decoded) || decoded.includes('/')) return null;
      else parts.push(decoded);
    }
    return parts.join('/');
  }
  /** Keep Markdown source authoritative while rendering owned images and safe links. */
  function renderDocument(markdown, item, source) {
    const renderer = new markdown.Renderer();
    renderer.html = token => escape(token.text);
    renderer.image = token => {
      const path = bundlePath(item.entrypoint, token.href);
      return path ? `<img src="${escape(filePath(item,path))}" alt="${escape(token.text)}" loading="lazy">` : `<span class="chat-image-alt">${escape(token.text || 'Image')}</span>`;
    };
    renderer.link = function(token) {
      const label = this.parser.parseInline(token.tokens), path = bundlePath(item.entrypoint,token.href);
      if (path) return `<a href="${escape(filePath(item,path))}" target="_blank" rel="noopener noreferrer">${label}</a>`;
      if (/^(https?:|mailto:)/i.test(token.href)) return `<a href="${escape(token.href)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
      return label;
    };
    return new markdown.Marked({renderer,gfm:true,breaks:false}).parse(source,{async:false});
  }
  const exports = {filterArtifacts,filterDesigns,kindOf,groupTargets,selectTargets,bundlePath,renderDocument,encodePath,feedbackTarget,filePath};
  if (typeof module !== 'undefined' && module.exports) { module.exports=exports; return; }

  const doc=root.document, content=doc.getElementById('hub-content'), dialog=doc.getElementById('hub-dialog');
  const extraIcons={search:'m21 21-5-5M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15',image:'M3 3h18v18H3zM3 16l6-6 5 5 3-3 4 4M16 7h.01',updated:'M20 4v5h-5M19.2 8a8 8 0 1 0-4.7 11.6'};
  const icon=name => extraIcons[name]?`<svg class="ui-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${extraIcons[name]}"/>${name==='updated'?'<circle cx="20" cy="17" r="2" fill="currentColor" stroke="none"/>':''}</svg>`:root.SlideIcons.svg(name);
  const sizeLabel=size => root.SlideChatComposer.sizeLabel(Number(size) || 0);
  const state={designActive:null,projects:[],artifacts:[],designs:[],project:'all',kind:'all',search:'',active:null,detail:null,source:'',sourceView:false,selecting:false,zoom:1,viewport:'fluid',newRevision:null,loading:0};
  const previewUpdates=root.SlidePreviewUpdates.create({onChange:()=>{syncAutoUpdate();if(previewUpdates.enabled)void refresh();}});
  const drafts=new Map(), attachments=new Map(), sending=new Set();
  let importFiles=[],toastTimer,refreshing=false,regionStart=null;
  try {
    const saved=JSON.parse(root.localStorage.getItem('artifact-hub:drafts:v1') || '{}');
    for (const [id,value] of Object.entries(saved)) if (value && typeof value.message === 'string') drafts.set(id,{...value,targets:Array.isArray(value.targets) ? value.targets : []});
  } catch {}
  const selectedProject=id => state.projects.find(project=>project.id===id);
  const projectTitle=id => id==='designs'?'Designs':selectedProject(id)?.title || (id === 'all' ? 'All artifacts' : 'Project');
  const fileRecords=item => (item.files || []).map(file=>typeof file==='string'?{path:file}:file);
  const draft = (item=state.detail) => {
    if (!drafts.has(item.id)) drafts.set(item.id,{message:'',targets:[],revision:item.revision,content_hash:item.content_hash,request_id:null});
    return drafts.get(item.id);
  };
  const persist=()=>{try{root.localStorage.setItem('artifact-hub:drafts:v1',JSON.stringify(Object.fromEntries([...drafts].filter(([,value])=>value.message || value.targets.length))));}catch{}};
  const isAdditive=event=>event.metaKey || event.ctrlKey || event.shiftKey;
  const button=(action,label,name,extra='')=>`<button type="button" data-action="${action}" ${extra}>${icon(name)}${label?`<span>${label}</span>`:''}</button>`;
  const status=item=>`<span class="hub-status ${item.status || 'draft'}">${icon(item.status==='approved'?'check':item.status==='updated'?'updated':'clock')}${item.status==='approved'?'Approved':item.status==='updated'?'Updated':'To review'}</span>`;

  async function api(path, body) {
    const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
    let result;
    try{result=await response.json();}catch{if(!response.ok)throw new Error(`Request failed (${response.status}).`);return {};}
    if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:result.error || `Request failed (${response.status}).`);
    return result;
  }
  function toast(message, error=false) {
    const node=doc.getElementById('hub-toast');node.textContent=message;node.classList.toggle('is-error',error);node.hidden=false;
    clearTimeout(toastTimer);toastTimer=setTimeout(()=>{node.hidden=true;},error?9000:4500);
  }
  function headerIcons() {
    for(const node of doc.querySelectorAll('[data-icon]')){const label=node.textContent;node.innerHTML=icon(node.dataset.icon)+(label?`<span>${escape(label)}</span>`:'');}
  }
  function sidebar(workspace=false) {
    if(workspace){
      const item=state.detail;
      return `<aside class="hub-sidebar" aria-label="Project artifacts">${button('home','Library','grid','class="hub-library-link"')}<label class="hub-project-picker">${icon('folder')}<select id="hub-project-picker" aria-label="Choose project">${state.projects.map(project=>`<option value="${escape(project.id)}" ${project.id===item.project_id?'selected':''}>${escape(project.title)}</option>`).join('')}</select></label><nav class="hub-project-artifacts" aria-label="Related artifacts">${state.artifacts.filter(other=>other.project_id===item.project_id).map(other=>`<button type="button" data-artifact="${escape(other.id)}" aria-current="${other.id===item.id}" title="${escape(other.title)}">${icon(kinds[kindOf(other)].icon)}<span>${escape(other.title)}</span>${other.status==='updated'?`<span class="hub-status updated" title="Updated">${icon('updated')}</span>`:''}</button>`).join('')}</nav></aside>`;
    }
    return `<aside class="hub-sidebar" aria-label="Projects"><button type="button" data-project="all" aria-current="${state.project==='all'}">${icon('grid')}<span>All artifacts</span><span class="hub-count">${state.artifacts.length}</span></button><button type="button" data-project="designs" aria-current="${state.project==='designs'}">${icon('palette')}<span>Designs</span><span class="hub-count">${state.designs.length}</span></button><div class="hub-sidebar-label">Projects${button('new-project','','plus','aria-label="New project" title="New project"')}</div>${state.projects.map(project=>`<button type="button" data-project="${escape(project.id)}" aria-current="${state.project===project.id}" title="${escape(project.title)}">${icon('folder')}<span>${escape(project.title)}</span><span class="hub-count">${state.artifacts.filter(item=>item.project_id===project.id).length}</span></button>`).join('')}</aside>`;
  }
  function visibleItems() {
    return state.project==='designs'?filterDesigns(state.designs,state.search):filterArtifacts(state.artifacts,state);
  }
  function resultCount() {
    const count=visibleItems().length;
    return `${count} ${state.project==='designs'?'design':'artifact'}${count===1?'':'s'}`;
  }
  function designCards(){return designLibrary.cards(visibleItems(),state.search);}
  function cards() {
    if(state.project==='designs')return designCards();
    const items=filterArtifacts(state.artifacts,state);
    if(!items.length)return `<div class="hub-empty">${icon(state.search?'eye':'folder')}<h2>${state.search || state.kind!=='all'?'No matching artifacts':state.project==='all'?'A place for your work':'This project is ready'}</h2><p>${state.search || state.kind!=='all'?'Try another search or format.':'Import a page, document, image, presentation or a bundle of files.'}</p>${state.search || state.kind!=='all'?'':`<div class="hub-empty-actions">${button('import','Import files','upload','class="hub-primary"')}</div>`}</div>`;
    return items.map(item=>{
      const kind=kinds[kindOf(item)], preview=item.preview_url || (kindOf(item)==='image'?filePath(item):kindOf(item)==='file'?'':`/api/hub/previews/${encodeURIComponent(item.id)}?v=${encodeURIComponent(item.content_hash || '')}`);
      const safePreview=typeof preview==='string' && preview.startsWith('/api/') && !preview.startsWith('//')?preview:'';
      return `<article class="hub-card"><button type="button" class="hub-card-open" data-artifact="${escape(item.id)}" aria-label="Open ${escape(item.title)}, ${kind.singular}, ${escape(item.project_title || projectTitle(item.project_id))}"><span class="hub-cover"><span class="hub-cover-fallback">${icon(kind.icon)}<span>${kind.singular}</span><small>${escape(item.entrypoint || '')}</small></span>${safePreview?`<img src="${escape(safePreview)}" alt="" loading="lazy" decoding="async" data-preview-image>`:''}</span><span class="hub-card-info"><span class="hub-card-title">${icon(kind.icon)}<span title="${escape(item.title)}">${escape(item.title)}</span></span><span class="hub-card-meta"><span>${escape(item.project_title || projectTitle(item.project_id))}</span>${status(item)}</span></span></button><button type="button" class="hub-card-move" data-move-artifact="${escape(item.id)}" aria-label="Move ${escape(item.title)} to project" title="Move to project">${icon('folder')}</button></article>`;
    }).join('');
  }
  function library() {
    const designs=state.project==='designs';
    for(const node of doc.querySelectorAll('.hub-top-actions [data-design-action]'))node.hidden=!designs;
    for(const node of doc.querySelectorAll('.hub-top-actions [data-action="new-project"],.hub-top-actions [data-action="import"],#hub-tools-button,.hub-top-actions [data-action="context"]'))node.hidden=designs;
    content.innerHTML=`<div class="hub-layout">${sidebar()}<main class="hub-main"><header class="hub-heading"><div><h1>${escape(projectTitle(state.project))}</h1><p id="hub-result-count" class="hub-heading-count">${resultCount()}</p></div><label class="hub-search">${icon('search')}<input type="search" id="hub-search" placeholder="Search ${designs?'designs':'artifacts'}…" aria-label="Search ${designs?'designs':'artifacts'}" value="${escape(state.search)}"></label></header><nav class="hub-chips" aria-label="Artifact formats" ${designs?'hidden':''}>${[['all',{label:'All',icon:'grid'}],...Object.entries(kinds)].map(([key,kind])=>`<button type="button" data-kind="${key}" aria-pressed="${state.kind===key}">${key==='all'?'':icon(kind.icon)}${kind.label}</button>`).join('')}</nav><div id="hub-grid" class="hub-grid">${cards()}</div></main></div>`;
    doc.title=`${projectTitle(state.project)} · Artifact Hub`;
  }
  function fileList(item) {
    return `<ul class="hub-file-list">${fileRecords(item).map(file=>`<li><a href="${escape(filePath(item,file.path || file.name))}" download>${icon('notes')}<span>${escape(file.path || file.name)}</span></a>${file.size!==undefined?`<small>${sizeLabel(file.size)}</small>`:''}</li>`).join('')}</ul>`;
  }
  function viewer(item) {
    const kind=kindOf(item), sourceView=kind==='markdown' && state.sourceView;
    if(kind==='html')return `<div class="hub-preview-scroll"><iframe id="hub-html-preview" class="hub-html-frame" title="${escape(item.title)} preview" sandbox="allow-scripts" referrerpolicy="no-referrer" src="${escape(artifactPath(item.id,'/preview'))}?revision=${encodeURIComponent(item.revision)}" style="width:${state.viewport==='fluid'?'100%':state.viewport+'px'}"></iframe></div>`;
    if(kind==='markdown')return sourceView?`<pre id="hub-markdown-source" class="hub-source">${escape(state.source)}</pre>`:`<article id="hub-markdown" class="hub-markdown ${state.selecting?'is-selecting':''}">${renderDocument(root.marked,item,state.source)}</article>`;
    if(kind==='image')return `<div class="hub-image-canvas" id="hub-image-canvas"><div class="hub-image-wrap ${state.selecting?'is-selecting':''}" id="hub-image-wrap"><img id="hub-image" src="${escape(filePath(item))}" alt="${escape(item.title)}" draggable="false"><div id="hub-region" class="hub-region" hidden></div></div></div><p id="hub-image-meta" class="hub-image-meta">${escape(item.entrypoint)}</p>`;
    return `<section class="hub-file-view">${icon(kinds[kind].icon)}<h2>${escape(item.entrypoint || item.title)}</h2><p>This file is stored in its original format. A dedicated preview is not available.</p><a class="hub-download hub-primary" href="${escape(filePath(item))}" download>${icon('down')}Download file</a></section>`;
  }
  function autoUpdateControl() {
    return button('auto-update','Auto-update','refresh',`id="hub-auto-update" aria-label="Auto-update preview" aria-pressed="${previewUpdates.enabled}" title="${previewUpdates.enabled?'Auto-update on · incoming revisions appear automatically':'Auto-update off · open updated versions manually'}"`);
  }
  function syncAutoUpdate() {const control=doc.getElementById('hub-auto-update');if(control)control.outerHTML=autoUpdateControl();}
  function viewerToolbar(item) {
    const kind=kindOf(item);
    return `<div class="hub-view-toolbar"><div>${autoUpdateControl()}${['html','markdown','image'].includes(kind)?button('select',state.selecting?'Selecting':'Select','cursor',`aria-pressed="${state.selecting}" title="Select targets · hold ⌘, Ctrl or Shift to add more"`):''}${kind==='markdown'?button('source',state.sourceView?'Preview':'Source',state.sourceView?'eye':'code',`aria-pressed="${state.sourceView}"`):''}</div><div>${kind==='html'?`<label class="sr-only" for="hub-viewport">Preview width</label><select id="hub-viewport" aria-label="Preview width"><option value="fluid" ${state.viewport==='fluid'?'selected':''}>Responsive</option><option value="1280" ${state.viewport==='1280'?'selected':''}>Desktop · 1280</option><option value="768" ${state.viewport==='768'?'selected':''}>Tablet · 768</option><option value="390" ${state.viewport==='390'?'selected':''}>Mobile · 390</option></select>${button('replay','','refresh','aria-label="Reload page preview" title="Reload page preview"')}`:kind==='image'?`${button('zoom-out','','minus','aria-label="Zoom out" title="Zoom out"')}<span id="hub-zoom-label" class="hub-muted">Fit</span>${button('zoom-in','','plus','aria-label="Zoom in" title="Zoom in"')}${button('fit','','fit','aria-label="Fit image" title="Fit image"')}`:''}</div></div>`;
  }
  function pendingCount(item) {return (item.feedback || []).filter(message=>message.released===false && (message.author || message.role || 'user')==='user').length;}
  function messageMarkup(message,item) {
    const author=message.author || message.role || 'user', user=author==='user', target=message.target, targets=target?.kind==='group'?target.elements:[target].filter(Boolean);
    const date=message.created_at?new Date(message.created_at):null;
    const files=(message.files || []).map(file=>{
      const url=file.download_url;
      return typeof url==='string' && /^\/api\/artifacts\//.test(url)?`<a class="hub-attachment" href="${escape(url)}" download>${icon('attach')}<span>${escape(file.name || file.path)}</span></a>`:`<span class="hub-attachment">${icon('attach')}<span>${escape(file.name || file.path)}</span></span>`;
    }).join('');
    return `<article class="hub-message ${user?'is-user':''}"><div class="hub-message-head">${icon(user?'user':'robot')}<span>${user?'You':'Agent'}</span>${date && !Number.isNaN(date.getTime())?`<time datetime="${date.toISOString()}" title="${escape(date.toLocaleString())}">${escape(date.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}))}</time>`:''}</div>${targets.length?`<div class="hub-message-target">${escape(targets.map(targetLabel).join(' · '))}${message.content_hash && message.content_hash!==item.content_hash?' · Earlier version':''}</div>`:''}<div class="hub-message-body">${root.SlideChatMarkdown.render(message.message || message.text || '')}</div>${files?`<div class="hub-attachments">${files}</div>`:''}${message.released===false?'<div class="hub-message-state">Private batch · not released</div>':''}</article>`;
  }
  function chatMarkup(item) {
    const value=draft(item), mode=item.mode || 'batch', count=pendingCount(item), live=item.delivery?.live_available===true;
    return `<header><div class="hub-chat-heading">${icon('reply')}Conversation</div><p class="hub-chat-title" title="${escape(item.title)}">${escape(item.title)} · version ${escape(item.revision || 1)}</p></header><div class="hub-delivery"><div class="hub-mode-switch" role="group" aria-label="Feedback delivery mode"><button type="button" data-mode="live" aria-pressed="${mode==='live'}" title="${live?'Release feedback immediately to the connected agent':'Release feedback immediately. Automatic agent continuation is unavailable.'}">${icon('live')}Live${live?'':'<span class="hub-live-dot" aria-label="Automatic continuation unavailable"></span>'}</button><button type="button" data-mode="batch" aria-pressed="${mode==='batch'}" title="Keep feedback private until Send batch">${icon('batch')}Batch</button></div>${mode==='batch'?button('release',`Send batch${count?' · '+count:''}`,'send',`class="hub-release" ${count?'':'disabled'}`):''}</div><div class="hub-thread" id="hub-thread">${(item.feedback || []).length?item.feedback.map(message=>messageMarkup(message,item)).join(''):''}</div><details class="hub-chat-context"><summary>Context & sources</summary><p>${escape(typeof item.context==='string'?item.context:typeof selectedProject(item.project_id)?.context==='string'?selectedProject(item.project_id).context:'')}</p>${button('context','Inspect agent context','code')}</details><form id="hub-feedback-form" class="hub-compose"><div id="hub-targets">${targetsMarkup(value,item)}</div><div id="hub-feedback-attachments" class="hub-attachments">${attachmentMarkup(item.id)}</div>${value.content_hash!==item.content_hash && (value.message || value.targets.length)?'<p class="hub-draft-warning">This draft refers to an earlier version. Its original targets are retained.</p>':''}<textarea id="hub-feedback-text" aria-label="Feedback for ${escape(item.title)}" placeholder="What would you change?" maxlength="10000">${escape(value.message)}</textarea><p id="hub-feedback-error" class="hub-compose-error" role="alert"></p><input type="file" id="hub-feedback-files" multiple hidden><div class="hub-compose-actions">${button('attach','','attach','aria-label="Attach files" title="Attach files"')}<button type="submit" id="hub-send" class="hub-primary" aria-label="Send feedback" title="Send · Enter; new line · Shift+Enter">${icon('send')}</button></div></form>`;
  }
  function attachmentMarkup(id) {return (attachments.get(id) || []).map((file,index)=>`<span class="hub-attachment"><span title="${escape(file.name)}">${escape(file.name)}</span><button type="button" data-remove-file="${index}" aria-label="Remove ${escape(file.name)}">${icon('close')}</button></span>`).join('');}
  function targetsMarkup(value,item) {
    if(!value.targets.length)return `<div class="hub-target-row"><span class="hub-target">${escape(item.title)}</span></div>`;
    return `${value.targets.length>1?`<div class="hub-target-row"><span class="hub-muted">${value.targets.length} selected</span>${button('clear-targets','','close','aria-label="Clear all targets" title="Clear all targets"')}</div>`:''}${value.targets.map((target,index)=>`<div class="hub-target-row"><span class="hub-target">${escape(targetLabel(target))}</span><button type="button" data-remove-target="${index}" aria-label="Remove ${escape(targetLabel(target))}" title="Remove target">${icon('close')}</button></div>`).join('')}`;
  }
  function workspace() {
    const item=state.detail;
    content.innerHTML=`<div class="hub-layout">${sidebar(true)}<main class="hub-workspace"><section class="hub-edit-area" aria-label="Artifact preview"><nav class="hub-crumbs" aria-label="Breadcrumb">${button('home','','grid','aria-label="Back to library" title="Back to library"')}${icon('right')}<button type="button" data-project="${escape(item.project_id)}">${escape(item.project_title || projectTitle(item.project_id))}</button>${icon('right')}<span>${escape(kinds[kindOf(item)].singular)}</span></nav><header class="hub-edit-heading"><div><h1>${escape(item.title)}</h1><div class="hub-version"><span>Version ${escape(item.revision || 1)}</span>${status(item)}</div></div>${button('approve',item.status==='approved'?'Approved':'Approve','check',`class="hub-approve ${item.status==='approved'?'is-approved':''}" ${item.status==='approved'?'disabled':''} title="Approve the displayed version"`)}</header><div id="hub-new-revision" ${state.newRevision?'':'hidden'}>${button('reload','Open updated version','updated','class="hub-approval-note"')}</div>${viewerToolbar(item)}<div id="hub-viewer">${viewer(item)}</div>${item.external_dependencies?.length?'<p class="hub-approval-note">External dependencies are mutable. Approval records the local files and does not freeze remote content.</p>':''}<details class="hub-files"><summary>${icon('folder')}Sources & files<span class="hub-count">${fileRecords(item).length}</span></summary>${fileList(item)}<p class="hub-source-hash" title="Content hash">${escape(item.content_hash)}</p><div class="hub-source-links">${button('context','Agent context','code')}${button('move','Move to project','folder')}</div>${item.source_path?`<p class="hub-muted" style="overflow-wrap:anywhere;margin-top:12px">${escape(item.source_path)}</p>`:''}</details></section><aside id="hub-chat" class="hub-chat" aria-label="Artifact conversation">${chatMarkup(item)}</aside></main></div>`;
    doc.title=`${item.title} · Artifact Hub`;
    bindViewer();syncSelection();lockComposer();
  }
  function renderChat() {
    const node=doc.getElementById('hub-chat');if(!node || !state.detail)return;
    node.innerHTML=chatMarkup(state.detail);const thread=doc.getElementById('hub-thread');thread.scrollTop=thread.scrollHeight;lockComposer();
  }
  function lockComposer() {for(const control of doc.querySelectorAll('#hub-feedback-form button,#hub-feedback-form textarea,#hub-feedback-form input'))control.disabled=sending.has(state.active);}
  function syncSelection() {
    if(!state.detail)return;
    const value=draft(),targetNode=doc.getElementById('hub-targets');if(targetNode)targetNode.innerHTML=targetsMarkup(value,state.detail);
    const frame=doc.getElementById('hub-html-preview');
    frame?.contentWindow?.postMessage({type:'artifact:select',enabled:state.selecting},'*');
    frame?.contentWindow?.postMessage({type:'artifact:selection',selectors:value.content_hash===state.detail.content_hash?value.targets.map(target=>target.selector).filter(Boolean):[]},'*');
    for(const block of doc.querySelectorAll('#hub-markdown [data-target-index]'))block.classList.toggle('hub-selected-block',value.content_hash===state.detail.content_hash && value.targets.some(target=>target.block_index===Number(block.dataset.targetIndex)));
    doc.getElementById('hub-markdown')?.classList.toggle('is-selecting',state.selecting);
    doc.getElementById('hub-image-wrap')?.classList.toggle('is-selecting',state.selecting);
    renderImageRegions();persist();
  }
  function setTarget(target,additive) {
    if(sending.has(state.active))return;
    const value=draft();
    if(value.content_hash!==state.detail.content_hash && (value.message || value.targets.length)){toast('Send or clear the earlier-version draft before selecting this version.',true);return;}
    value.content_hash=state.detail.content_hash;value.revision=state.detail.revision;value.targets=selectTargets(value.targets,target,additive);value.request_id=null;syncSelection();
  }
  function bindViewer() {
    const frame=doc.getElementById('hub-html-preview');frame?.addEventListener('load',syncSelection);
    const markdown=doc.getElementById('hub-markdown');
    if(markdown)for(const [index,block] of [...markdown.querySelectorAll('h1,h2,h3,h4,h5,h6,p,li,pre,blockquote')].entries())block.dataset.targetIndex=String(index);
    const image=doc.getElementById('hub-image');
    if(image){image.addEventListener('load',()=>{fitImage();const meta=doc.getElementById('hub-image-meta');if(meta)meta.textContent=`${state.detail.entrypoint} · ${image.naturalWidth} × ${image.naturalHeight}`;});if(image.complete && image.naturalWidth)fitImage();}
  }
  function fitImage() {
    const image=doc.getElementById('hub-image'),canvas=doc.getElementById('hub-image-canvas');if(!image?.naturalWidth)return;
    const padding=root.getComputedStyle(canvas);state.zoom=Math.min(1,(canvas.clientWidth-parseFloat(padding.paddingLeft)-parseFloat(padding.paddingRight))/image.naturalWidth);applyZoom();
  }
  function applyZoom() {const image=doc.getElementById('hub-image');if(!image)return;image.style.width=`${image.naturalWidth*state.zoom}px`;const output=doc.getElementById('hub-zoom-label');if(output)output.textContent=`${Math.round(state.zoom*100)}%`;renderImageRegions();}
  function renderImageRegions() {
    const wrap=doc.getElementById('hub-image-wrap');if(!wrap || !state.detail)return;
    for(const node of wrap.querySelectorAll('.hub-saved-region'))node.remove();
    if(draft().content_hash!==state.detail.content_hash)return;
    for(const target of draft().targets.filter(target=>target.kind==='region')){
      const region=target.region || target,image=doc.getElementById('hub-image'),width=region.image_width || image.naturalWidth,height=region.image_height || image.naturalHeight,node=doc.createElement('div');node.className='hub-region hub-saved-region';node.style.cssText=`left:${region.x*width*state.zoom}px;top:${region.y*height*state.zoom}px;width:${region.width*width*state.zoom}px;height:${region.height*height*state.zoom}px`;wrap.append(node);
    }
  }
  async function loadRegistry() {
    const data=await api('/api/hub');state.projects=data.projects || [];state.artifacts=data.artifacts || [];state.designs=data.designs || [];
    if(!['all','designs'].includes(state.project) && !state.projects.some(project=>project.id===state.project))state.project='all';
  }
  function updateHistory(id) {const url=new URL(root.location.href);id?url.searchParams.set('artifact',id):url.searchParams.delete('artifact');url.searchParams.delete('deck');url.searchParams.delete('design');state.project==='designs'?url.searchParams.set('view','designs'):url.searchParams.delete('view');root.history.pushState({},'',url);}
  async function openArtifact(id,push=true) {
    state.designActive=null;
    const item=state.artifacts.find(item=>item.id===id);
    if(item?.legacy_deck_id){root.location.href=`/?deck=${encodeURIComponent(item.legacy_deck_id)}`;return;}
    const token=++state.loading;state.active=id;state.selecting=false;state.sourceView=false;state.newRevision=null;
    content.innerHTML='<main class="hub-loading" role="status">Opening artifact…</main>';
    try {
      const detail=await api(artifactPath(id));
      if(token!==state.loading)return;
      if(detail.legacy_deck_id){root.location.href=`/?deck=${encodeURIComponent(detail.legacy_deck_id)}`;return;}
      let source='';
      if(kindOf(detail)==='markdown') {const response=await fetch(filePath(detail));if(!response.ok)throw new Error('The Markdown source could not be loaded.');source=await response.text();}
      if(token!==state.loading)return;
      state.detail=detail;state.source=source;state.project=detail.project_id;
      const value=draft(detail);if(!value.message && !value.targets.length){value.content_hash=detail.content_hash;value.revision=detail.revision;}
      workspace();if(push)updateHistory(id);
      if(detail.status==='updated')api(artifactPath(id,'/seen'),{content_hash:detail.content_hash}).then(()=>{const card=state.artifacts.find(item=>item.id===id);if(card)card.status='draft';if(state.detail===detail){detail.status='draft';const badge=doc.querySelector('.hub-version .hub-status');if(badge)badge.outerHTML=status(detail);}}).catch(()=>{});
    }catch(error){if(token===state.loading){state.active=null;state.detail=null;library();toast(error.message,true);}}
  }
  function home(project='all',push=true) {if((project==='designs')!==(state.project==='designs')){state.search='';state.kind='all';}++state.loading;state.designActive=null;state.active=null;state.detail=null;state.project=project;state.newRevision=null;library();if(push)updateHistory(null);}
  function modal(title,body,wide=false) {dialog.className=`hub-dialog ${wide?'hub-context-dialog':''}`;dialog.innerHTML=`<header class="hub-dialog-head"><h2 id="hub-dialog-title">${escape(title)}</h2>${button('close-dialog','','close','aria-label="Close dialog" title="Close"')}</header>${body}`;if(!dialog.open)dialog.showModal();}
  const designLibrary=root.SlideDesignLibrary.create({api,icon,escape,sizeLabel,toast,modal,changed:async id=>{
    await loadRegistry();if(id)return openDesign(id,false);home('designs');
  }});
  async function openDesign(id,push=true){
    const token=++state.loading;state.active=null;state.detail=null;state.project='designs';state.designActive=id;
    try{
      const item=await api(`/api/design-library/${encodeURIComponent(id)}`);if(token!==state.loading)return;
      library();state.designActive=id;
      doc.querySelector('.hub-main').innerHTML=designLibrary.detail(item);
      doc.title=`${item.name} · Designs`;
      if(push||new URL(root.location.href).searchParams.get('design')!==id){
        const url=new URL(root.location.href);url.searchParams.set('view','designs');url.searchParams.set('design',id);url.searchParams.delete('artifact');url.searchParams.delete('deck');root.history.pushState({},'',url);
      }
    }catch(error){if(token===state.loading){home('designs',false);toast(error.message,true);}}
  }
  function projectDialog(thenImport=false) {
    modal('New project',`<form id="hub-project-form" class="hub-form" data-then-import="${thenImport}"><label class="hub-field"><span>Project name</span><input name="title" required maxlength="200" autofocus placeholder="Name your project"></label><label class="hub-field"><span>Shared brief <span class="hub-muted">· optional</span></span><textarea name="context" placeholder="Purpose, audience and source context"></textarea></label><p id="hub-dialog-error" class="hub-compose-error" role="alert"></p><div class="hub-form-actions">${button('close-dialog','Cancel','close')}<button type="submit" class="hub-primary">Create project</button></div></form>`);
    dialog.querySelector('input').focus();
  }
  function importDialog() {
    const projects=state.projects.filter(project=>!project.legacy);
    if(!projects.length){projectDialog(true);return;}
    importFiles=[];
    modal('Import artifact',`<form id="hub-import-form" class="hub-form"><label class="hub-field"><span>Project</span><select name="project_id">${projects.map(project=>`<option value="${escape(project.id)}" ${project.id===state.project?'selected':''}>${escape(project.title)}</option>`).join('')}</select></label><label class="hub-field"><span>Title</span><input name="title" required maxlength="200" placeholder="Artifact title"></label><div class="hub-field"><span>Files</span><div class="hub-import-choices">${button('choose-files','Choose files','upload')}${button('choose-folder','Choose folder','folder')}</div><input id="hub-import-files" type="file" multiple hidden><input id="hub-import-folder" type="file" webkitdirectory multiple hidden><ul id="hub-import-list" class="hub-import-list"></ul></div><label class="hub-field" id="hub-entrypoint-field" hidden><span>Open first</span><select name="entrypoint" id="hub-entrypoint"></select></label><p id="hub-dialog-error" class="hub-compose-error" role="alert"></p><div class="hub-form-actions">${button('close-dialog','Cancel','close')}<button type="submit" class="hub-primary">Import artifact</button></div></form>`);
  }
  function setImportFiles(files) {
    importFiles=files;
    const names=files.map(file=>file.webkitRelativePath || file.name);
    doc.getElementById('hub-import-list').innerHTML=files.map((file,index)=>`<li>${escape(names[index])} · ${sizeLabel(file.size)}</li>`).join('');
    const entry=doc.getElementById('hub-entrypoint');entry.innerHTML=names.map(name=>`<option value="${escape(name)}">${escape(name)}</option>`).join('');
    entry.value=names.find(name=>/(^|\/)index\.html?$/i.test(name)) || names.find(name=>/\.(md|markdown|html?|svg|png|jpe?g|webp|gif)$/i.test(name)) || names[0] || '';
    doc.getElementById('hub-entrypoint-field').hidden=!files.length;
    const title=dialog.querySelector('[name=title]');if(!title.value && files.length)title.value=(files[0].webkitRelativePath?.split('/')[0] || files[0].name.replace(/\.[^.]+$/,'')).replace(/[-_]/g,' ');
  }
  async function encodeFiles(files,usePaths=false) {
    const result=[];
    for(const file of files){const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error(`Could not read ${file.name}.`));reader.readAsDataURL(file);});result.push({name:usePaths?(file.webkitRelativePath || file.name):file.name,data_base64:data});}
    return result;
  }
  async function contextDialog() {
    modal('Agent context','<p class="hub-muted" role="status">Loading context…</p>',true);
    const data=await api(`/api/hub/context${state.detail?'?artifact_id='+encodeURIComponent(state.detail.id):!['all','designs'].includes(state.project)?'?project_id='+encodeURIComponent(state.project):''}`);
    const sources=Array.isArray(data.sources)?data.sources:Array.isArray(data.context?.sources)?data.context.sources:[];
    if(!dialog.open)return;
    modal('Agent context',`<div class="hub-context-grid"><section><h3>Sources & delivery</h3><ul class="hub-context-list">${sources.length?sources.map(source=>`<li class="hub-context-source"><strong><span>${escape(source.title || source.name || source.kind || 'Context source')}</span><span class="hub-context-delivery">${escape(source.delivery || source.delivery_state || source.status || 'Available')}</span></strong><code>${escape(source.path || source.canonical_path || '')}</code><span class="hub-scope">Scope: ${escape(typeof source.scope==='object'?JSON.stringify(source.scope):source.scope || 'workspace')}</span>${source.hash || source.content_hash?`<code title="Content hash">${escape(source.hash || source.content_hash)}</code>`:''}</li>`).join(''):'<li class="hub-context-source">No source receipt was returned.</li>'}</ul></section><section><h3 style="margin-bottom:12px">${data.receipt?'Latest recorded delivery':'Available context'}</h3><pre class="hub-context-receipt">${escape(JSON.stringify(data.receipt || data,null,2))}</pre></section></div><p class="hub-context-note">Delivery records what the server returned. Available sources are not proof that an agent opened or retained them.</p>`,true);
  }
  function moveDialog(id=state.active) {
    const item=state.artifacts.find(item=>item.id===id) || state.detail;
    if(!state.projects.some(project=>!project.legacy)){projectDialog();return;}
    modal('Move to project',`<form id="hub-move-form" class="hub-form" data-artifact-id="${escape(item.id)}"><label class="hub-field"><span>Project for ${escape(item.title)}</span><select name="project_id">${state.projects.map(project=>`<option value="${escape(project.id)}" ${project.id===item.project_id?'selected':''}>${escape(project.title)}</option>`).join('')}</select></label><p id="hub-dialog-error" class="hub-compose-error" role="alert"></p><div class="hub-form-actions">${button('close-dialog','Cancel','close')}<button type="submit" class="hub-primary">Move artifact</button></div></form>`);
  }
  async function sendFeedback(form) {
    const item=state.detail,value=draft(item),files=attachments.get(item.id) || [];
    if(sending.has(item.id) || !value.message.trim() && !files.length)return;
    sending.add(item.id);
    const controls=[...form.querySelectorAll('button,textarea,input')],error=form.querySelector('[role=alert]');for(const control of controls)control.disabled=true;error.textContent='';
    value.request_id ||= root.crypto.randomUUID();persist();
    try{
      await api(artifactPath(item.id,'/feedback'),{message:value.message.trim(),target:feedbackTarget(groupTargets(value.targets)),files:await encodeFiles(files),content_hash:value.content_hash,request_id:value.request_id});
      drafts.delete(item.id);attachments.delete(item.id);persist();
      const detail=await api(artifactPath(item.id));if(state.active===item.id){state.detail={...state.detail,feedback:detail.feedback,mode:detail.mode,delivery:detail.delivery};renderChat();syncSelection();}
    }catch(reason){error.textContent=reason.message;}finally{sending.delete(item.id);for(const control of controls)control.disabled=false;lockComposer();if(state.active===item.id)doc.getElementById('hub-feedback-text')?.focus();}
  }
  function addAttachments(incoming) {
    if(sending.has(state.active))return;
    const selected=[...(attachments.get(state.active) || []),...incoming],error=root.SlideChatComposer.selectionError(selected);
    if(error){doc.getElementById('hub-feedback-error').textContent=error;return;}
    attachments.set(state.active,selected);doc.getElementById('hub-feedback-attachments').innerHTML=attachmentMarkup(state.active);
  }
  async function action(name,node) {
    if(name==='auto-update'){previewUpdates.set(!previewUpdates.enabled);return;}
    if(name==='home')return home();
    if(name==='retry-library'){await loadRegistry();return library();}
    if(name==='new-project')return projectDialog();
    if(name==='import')return importDialog();
    if(name==='close-dialog')return dialog.close();
    if(name==='context')return contextDialog();
    if(name==='choose-files')return doc.getElementById('hub-import-files').click();
    if(name==='choose-folder')return doc.getElementById('hub-import-folder').click();
    if(name==='move')return moveDialog();
    if(name==='attach')return doc.getElementById('hub-feedback-files').click();
    if(name==='clear-targets'){draft().targets=[];draft().request_id=null;syncSelection();return;}
    if(name==='reload')return openArtifact(state.active,false);
    if(name==='select'){state.selecting=!state.selecting;node.setAttribute('aria-pressed',String(state.selecting));node.innerHTML=icon('cursor')+`<span>${state.selecting?'Selecting':'Select'}</span>`;syncSelection();return;}
    if(name==='source'){state.sourceView=!state.sourceView;doc.getElementById('hub-viewer').innerHTML=viewer(state.detail);node.setAttribute('aria-pressed',String(state.sourceView));node.innerHTML=icon(state.sourceView?'eye':'code')+`<span>${state.sourceView?'Preview':'Source'}</span>`;bindViewer();syncSelection();return;}
    if(name==='replay'){const frame=doc.getElementById('hub-html-preview');frame.src=frame.src;return;}
    if(name==='fit')return fitImage();
    if(name==='zoom-in' || name==='zoom-out'){state.zoom=Math.max(.05,Math.min(8,state.zoom*(name==='zoom-in'?1.25:.8)));applyZoom();return;}
    if(name==='approve'){
      const item=state.detail;node.disabled=true;
      try{await api(artifactPath(item.id,'/approve'),{content_hash:item.content_hash});item.status='approved';item.approved=true;const card=state.artifacts.find(card=>card.id===item.id);if(card)card.status='approved';node.innerHTML=icon('check')+'<span>Approved</span>';node.classList.add('is-approved');const badge=doc.querySelector('.hub-version .hub-status');if(badge)badge.outerHTML=status(item);toast(`Approved version ${item.revision}.`);}catch(error){node.disabled=false;throw error;}return;
    }
    if(name==='release'){const id=state.active;node.disabled=true;try{await api(artifactPath(id,'/release'),{});const detail=await api(artifactPath(id));if(state.active===id){state.detail={...state.detail,feedback:detail.feedback,mode:detail.mode,delivery:detail.delivery};renderChat();}toast('Batch released.');}finally{node.disabled=false;}}
  }
  doc.addEventListener('click',event=>{
    const node=event.target.closest('button');if(!node)return;
    const run=async()=>{
      if(node.dataset.action)return action(node.dataset.action,node);
      if(node.dataset.openDesign)return openDesign(node.dataset.openDesign);
      if(node.dataset.artifact)return openArtifact(node.dataset.artifact);
      if(node.dataset.moveArtifact)return moveDialog(node.dataset.moveArtifact);
      if(node.dataset.project)return home(node.dataset.project);
      if(node.dataset.kind){state.kind=node.dataset.kind;library();return;}
      if(node.dataset.removeTarget!==undefined){draft().targets.splice(Number(node.dataset.removeTarget),1);draft().request_id=null;syncSelection();return;}
      if(node.dataset.removeFile!==undefined){attachments.get(state.active)?.splice(Number(node.dataset.removeFile),1);doc.getElementById('hub-feedback-attachments').innerHTML=attachmentMarkup(state.active);return;}
      if(node.dataset.mode){const id=state.active;node.disabled=true;try{await api(artifactPath(id,'/mode'),{mode:node.dataset.mode});const detail=await api(artifactPath(id));if(state.active===id){state.detail={...state.detail,feedback:detail.feedback,mode:detail.mode,delivery:detail.delivery};renderChat();}}finally{node.disabled=false;}}
    };run().catch(error=>toast(error.message,true));
  });
  doc.addEventListener('input',event=>{
    if(event.target.id==='hub-search'){state.search=event.target.value;doc.getElementById('hub-grid').innerHTML=cards();doc.getElementById('hub-result-count').textContent=resultCount();}
    if(event.target.id==='hub-feedback-text'){draft().message=event.target.value;draft().request_id=null;persist();}
  });
  doc.addEventListener('change',event=>{
    const node=event.target;
    if(node.id==='hub-project-picker')home(node.value);
    if(node.id==='hub-viewport'){state.viewport=node.value;const frame=doc.getElementById('hub-html-preview');frame.style.width=node.value==='fluid'?'100%':`${node.value}px`;}
    if(['hub-import-files','hub-import-folder'].includes(node.id))setImportFiles([...node.files]);
    if(node.id==='hub-feedback-files'){addAttachments([...node.files]);node.value='';}
  });
  doc.addEventListener('keydown',event=>{if(event.target.id==='hub-feedback-text' && event.key==='Enter' && !event.shiftKey && !event.isComposing){event.preventDefault();if(!doc.getElementById('hub-send').disabled)doc.getElementById('hub-feedback-form').requestSubmit();}});
  doc.addEventListener('submit',event=>{
    const form=event.target;if(!['hub-feedback-form','hub-project-form','hub-import-form','hub-move-form'].includes(form.id))return;event.preventDefault();
    if(form.id==='hub-feedback-form'){sendFeedback(form);return;}
    const submit=form.querySelector('[type=submit]'),error=form.querySelector('[role=alert]');submit.disabled=true;error.textContent='';
    (async()=>{
      const fields=new FormData(form);
      if(form.id==='hub-project-form'){const project=await api('/api/hub/projects',{title:String(fields.get('title')).trim(),context:String(fields.get('context'))});await loadRegistry();state.project=project.id || project.project?.id || 'all';dialog.close();home(state.project);if(form.dataset.thenImport==='true')importDialog();}
      if(form.id==='hub-import-form'){
        if(!importFiles.length)throw new Error('Choose files or a folder to import.');
        if(importFiles.length>1000 || importFiles.reduce((total,file)=>total+file.size,0)>100*1024*1024)throw new Error('Import up to 1,000 files and 100 MB at once.');
        const paths=importFiles.map(file=>file.webkitRelativePath || file.name);if(new Set(paths).size!==paths.length)throw new Error('The selection contains duplicate filenames. Choose a folder to preserve file paths.');
        submit.textContent='Importing…';const item=await api('/api/artifacts/import',{project_id:String(fields.get('project_id')),title:String(fields.get('title')).trim(),entrypoint:String(fields.get('entrypoint')),kind:'auto',files:await encodeFiles(importFiles,true)});await loadRegistry();dialog.close();await openArtifact(item.id || item.artifact?.id);
      }
      if(form.id==='hub-move-form'){const id=form.dataset.artifactId;await api(artifactPath(id,'/project'),{project_id:String(fields.get('project_id'))});await loadRegistry();dialog.close();if(state.active===id)await openArtifact(id,false);else library();toast('Artifact moved to the selected project.');}
    })().catch(reason=>{error.textContent=reason.message;submit.textContent=form.id==='hub-import-form'?'Import artifact':submit.textContent;}).finally(()=>{submit.disabled=false;});
  });
  content.addEventListener('error',event=>{if(event.target.matches?.('[data-preview-image]'))event.target.remove();},true);
  content.addEventListener('click',event=>{
    if(!state.selecting || kindOf(state.detail || {})!=='markdown' || state.sourceView)return;
    const block=event.target.closest('#hub-markdown [data-target-index]');if(!block)return;
    event.preventDefault();const selection=root.getSelection(),additive=isAdditive(event);
    const selectedText=!additive && selection?.anchorNode && doc.getElementById('hub-markdown').contains(selection.anchorNode)?selection.toString().trim():'';
    const quote=(selectedText || block.textContent.trim()).slice(0,2000),start=state.source.indexOf(quote),unique=start>=0 && start===state.source.lastIndexOf(quote);
    setTarget({kind:'text',label:quote.slice(0,80),quote,selector:`[data-target-index="${block.dataset.targetIndex}"]`,block_index:Number(block.dataset.targetIndex),...(unique?{start,end:start+quote.length}:{})},additive);
    selection?.removeAllRanges();
  });
  content.addEventListener('pointerdown',event=>{
    const wrap=event.target.closest('#hub-image-wrap');if(!wrap || !state.selecting || event.button!==0)return;
    event.preventDefault();const rect=wrap.getBoundingClientRect();regionStart={x:Math.max(0,event.clientX-rect.left),y:Math.max(0,event.clientY-rect.top),additive:isAdditive(event),pointer:event.pointerId};wrap.setPointerCapture(event.pointerId);
  });
  content.addEventListener('pointermove',event=>{
    if(!regionStart)return;const wrap=doc.getElementById('hub-image-wrap'),rect=wrap.getBoundingClientRect(),x=Math.max(0,Math.min(rect.width,event.clientX-rect.left)),y=Math.max(0,Math.min(rect.height,event.clientY-rect.top)),region=doc.getElementById('hub-region');region.hidden=false;region.style.cssText=`left:${Math.min(x,regionStart.x)}px;top:${Math.min(y,regionStart.y)}px;width:${Math.abs(x-regionStart.x)}px;height:${Math.abs(y-regionStart.y)}px`;
  });
  content.addEventListener('pointerup',event=>{
    if(!regionStart)return;const start=regionStart;regionStart=null;const wrap=doc.getElementById('hub-image-wrap'),rect=wrap.getBoundingClientRect(),image=doc.getElementById('hub-image'),x=Math.max(0,Math.min(rect.width,event.clientX-rect.left)),y=Math.max(0,Math.min(rect.height,event.clientY-rect.top));doc.getElementById('hub-region').hidden=true;
    const width=Math.abs(x-start.x)/state.zoom,height=Math.abs(y-start.y)/state.zoom;if(width<3 || height<3)return;
    const region={x:Math.round(Math.min(x,start.x)/state.zoom),y:Math.round(Math.min(y,start.y)/state.zoom),width:Math.round(width),height:Math.round(height)};
    setTarget({kind:'region',label:`Region ${region.x}, ${region.y} · ${region.width} × ${region.height}`,region:{x:region.x/image.naturalWidth,y:region.y/image.naturalHeight,width:region.width/image.naturalWidth,height:region.height/image.naturalHeight,image_width:image.naturalWidth,image_height:image.naturalHeight},viewport:{width:image.naturalWidth,height:image.naturalHeight}},start.additive);
  });
  content.addEventListener('pointercancel',()=>{regionStart=null;const region=doc.getElementById('hub-region');if(region)region.hidden=true;});
  content.addEventListener('dragover',event=>{if(event.target.closest('#hub-feedback-form') && [...(event.dataTransfer?.types || [])].includes('Files'))event.preventDefault();});
  content.addEventListener('drop',event=>{if(event.target.closest('#hub-feedback-form') && event.dataTransfer?.files.length){event.preventDefault();addAttachments([...event.dataTransfer.files]);}});
  content.addEventListener('paste',event=>{if(event.target.id==='hub-feedback-text' && event.clipboardData?.files.length){if(!event.clipboardData.getData('text/plain'))event.preventDefault();addAttachments([...event.clipboardData.files]);}});
  root.addEventListener('message',event=>{
    const frame=doc.getElementById('hub-html-preview');if(!frame || event.source!==frame.contentWindow || event.origin!=='null' || !event.data || typeof event.data!=='object')return;
    if(event.data.type==='artifact:height' && Number.isFinite(event.data.height))frame.style.height=`${Math.min(30000,Math.max(500,event.data.height))}px`;
    if(event.data.type==='artifact:target' && state.selecting){const target=event.data.target;if(target && target.kind==='element' && typeof target.selector==='string' && target.selector.length<=2000)setTarget({kind:'element',selector:target.selector,label:String(target.label || target.quote || target.selector).slice(0,200),quote:String(target.quote || '').slice(0,2000),...(target.viewport && Number.isFinite(target.viewport.width) && Number.isFinite(target.viewport.height)?{viewport:{width:target.viewport.width,height:target.viewport.height}}:{})},event.data.additive===true);}
  });
  root.addEventListener('popstate',()=>{const query=new URL(root.location.href).searchParams,id=query.get('artifact');query.get('design')?openDesign(query.get('design'),false):id?openArtifact(id,false):home(query.get('view')==='designs'?'designs':'all',false);});
  root.addEventListener('resize',()=>{if(state.detail && kindOf(state.detail)==='image')renderImageRegions();});
  function artifactPreviewBusy() {
    return !!regionStart || sending.has(state.active) || !!doc.activeElement?.closest('#hub-viewer') && (doc.activeElement.tagName==='IFRAME' || doc.activeElement.matches('input,textarea,select,[contenteditable]'));
  }
  /** Apply a new preview while retaining draft anchors, attachment choices and typing focus. */
  async function applyArtifactUpdate(detail) {
    const id=state.active,token=state.loading;
    let source='';
    if(kindOf(detail)==='markdown'){const response=await fetch(filePath(detail));if(!response.ok)throw new Error('The updated document could not be loaded.');source=await response.text();}
    if(state.active!==id || state.loading!==token || !previewUpdates.enabled || artifactPreviewBusy())return false;
    const focused=doc.activeElement,focusId=focused?.id,selection=focusId==='hub-feedback-text'?[focused.selectionStart,focused.selectionEnd]:null,thread=doc.getElementById('hub-thread'),scroll=thread?.scrollTop;
    const value=draft();
    state.detail=detail;state.source=source;state.newRevision=null;
    if(!value.message && !value.targets.length && !(attachments.get(id)||[]).length){value.content_hash=detail.content_hash;value.revision=detail.revision;}
    workspace();
    const restored=focusId && doc.getElementById(focusId);if(restored){restored.focus({preventScroll:true});if(selection)restored.setSelectionRange(...selection);}
    if(scroll!==undefined)doc.getElementById('hub-thread').scrollTop=scroll;
    if(detail.status==='updated')void api(artifactPath(id,'/seen'),{content_hash:detail.content_hash}).catch(()=>{});
    return true;
  }
  doc.addEventListener('focusin',()=>{if(state.newRevision && previewUpdates.enabled)void refresh();});
  doc.addEventListener('pointerup',()=>{if(state.newRevision && previewUpdates.enabled)queueMicrotask(()=>void refresh());});
  async function refresh() {
    if(refreshing || doc.hidden || dialog.open)return;refreshing=true;
    try {
      const previous=JSON.stringify([state.artifacts,state.designs,state.projects]),id=state.active;
      await loadRegistry();
      if(state.detail && state.active===id){
        const latest=state.artifacts.find(item=>item.id===id);
        const detail=await api(artifactPath(id));
        if(state.active!==id || !state.detail)return;
        if(detail.content_hash!==state.detail.content_hash){
          state.newRevision=latest || detail;
          const banner=doc.getElementById('hub-new-revision');if(banner)banner.hidden=false;
          if(previewUpdates.enabled && !artifactPreviewBusy() && await applyArtifactUpdate(detail))return;
        }
        if(state.active===id && JSON.stringify(detail.feedback)!==JSON.stringify(state.detail.feedback)){
          const thread=doc.getElementById('hub-thread'),atBottom=thread.scrollHeight-thread.scrollTop-thread.clientHeight<50;
          state.detail.feedback=detail.feedback;thread.innerHTML=detail.feedback.map(message=>messageMarkup(message,state.detail)).join('');
          if(atBottom)thread.scrollTop=thread.scrollHeight;
          const release=doc.querySelector('[data-action=release]'),count=pendingCount(state.detail);
          if(release){release.disabled=!count;release.innerHTML=icon('send')+`<span>Send batch${count?' · '+count:''}</span>`;}
        }
      }else if(state.designActive){
        if(!state.designs.some(item=>item.id===state.designActive))home('designs');
      }else if(!state.active && previous!==JSON.stringify([state.artifacts,state.designs,state.projects])){
        const search=doc.getElementById('hub-search'),focused=doc.activeElement===search,position=search?.selectionStart;
        library();if(focused){const next=doc.getElementById('hub-search');next.focus();try{next.setSelectionRange(position,position);}catch{}}
      }
    }catch{}finally{refreshing=false;}
  }
  headerIcons();
  loadRegistry().then(()=>{const query=new URL(root.location.href).searchParams;const id=query.get('artifact');const project=query.get('project');if(state.projects.some(item=>item.id===project))state.project=project;if(query.get('view')==='designs')state.project='designs';return query.get('design')?openDesign(query.get('design'),false):id?openArtifact(id,false):library();}).catch(error=>{content.innerHTML=`<main class="hub-empty">${icon('info')}<h2>The library could not be loaded</h2><p>${escape(error.message)}</p>${button('retry-library','Try again','refresh')}</main>`;toast(error.message,true);});
  setInterval(refresh,12000);
})(typeof window==='undefined'?{}:window);
