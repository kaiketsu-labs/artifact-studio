/* The artifact library and adaptive review workspace share existing studio utilities. */
((root) => {
  'use strict';
  const kinds = {
    presentation: {label:'Presentations', singular:'Presentation', icon:'presentation'},
    html: {label:'HTML', singular:'HTML page', icon:'code'},
    markdown: {label:'Markdown', singular:'Markdown', icon:'notes'},
    image: {label:'Images', singular:'Image', icon:'image'},
    file: {label:'Files', singular:'File', icon:'archive'},
  };
  const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
  const kindOf = item => ({svg:'image',document:'markdown',other:'file'}[item.kind] || (kinds[item.kind] ? item.kind : 'file'));
  const encodePath = value => String(value).split('/').map(encodeURIComponent).join('/');
  const studioPath = path => root.ArtifactSession?.path?.(path) ?? path;
  const artifactPath = (id, suffix='') => studioPath(`/api/artifacts/${encodeURIComponent(id)}${suffix}`);
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
  /** Retain only bounded, versioned voice drafts recovered from private browser storage. */
  function validVoiceTarget(target,group=true) {
    if(target===null)return true;
    if(!target || typeof target!=='object')return false;
    if(target.kind==='group')return group && Array.isArray(target.elements) && target.elements.length>0 && target.elements.length<=100 && target.elements.every(item=>item!==null && validVoiceTarget(item,false));
    return ['element','text','region'].includes(target.kind) && ['selector','label','quote'].every(name=>target[name]===undefined || typeof target[name]==='string');
  }
  function validVoiceSegment(value) {
    return value && typeof value.message==='string' && value.message.length<=10000 && typeof value.request_id==='string' && value.request_id.length>=8 && value.request_id.length<=100 && (value.id===undefined || typeof value.id==='string' && value.id.length>=8 && value.id.length<=100) && Number.isInteger(value.revision) && value.revision>0 && typeof value.content_hash==='string' && /^[a-f0-9]{64}$/.test(value.content_hash) && validVoiceTarget(value.target);
  }
  /** Append final recognition text using the session's captured artifact and target snapshot. */
  function appendVoiceSegment(value, captured, text) {
    if(typeof text!=='string' || !text.trim())return true;
    const segments=value.voice_segments || [], prior=segments.find(segment=>(segment.id || segment.request_id)===(captured.id || captured.request_id));
    const message=[prior?.message,text.trim()].filter(Boolean).join(' ');
    if(message.length>10000 || !prior && segments.length>=100)return false;
    if(prior)prior.message=message;
    else {
      const segment={id:captured.id || captured.request_id,request_id:captured.request_id,message,revision:captured.revision,content_hash:captured.content_hash,target:structuredClone(captured.target)};
      if(!validVoiceSegment(segment))return false;
      value.voice_segments=[...segments,segment];
    }
    return true;
  }
  /** Filter the real registry without treating project names as artifact titles. */
  function filterArtifacts(artifacts, {project='all',projectIds=null,kind='all',search=''}) {
    const query = search.trim().toLocaleLowerCase();
    return artifacts.filter(item => (projectIds?projectIds.includes(item.project_id):project === 'all' || item.project_id === project) && (kind === 'all' || kindOf(item) === kind) && `${item.title} ${item.project_title || ''} ${item.entrypoint || ''}`.toLocaleLowerCase().includes(query));
  }
  const sortLabels={starred:'Starred first',name:'Name',updated:'Last changed',created:'Created',type:'File type'};
  const names=new Intl.Collator(undefined,{numeric:true,sensitivity:'base'});
  /** Group by the entrypoint's file format, keeping slide decks together. */
  function artifactType(item) {
    const extension=/\.([^.\/]+)$/.exec(item.entrypoint || '')?.[1].toLowerCase();
    const format=extension==='htm'?'html':extension==='markdown'?'md':extension;
    const key=item.kind==='presentation'?'presentation':format || (kindOf(item)==='markdown'?'md':kindOf(item));
    const labels={presentation:'Presentations',html:'HTML',md:'Markdown',image:'Images',file:'Files'};
    const label=Object.hasOwn(labels,key)?labels[key]:key.toLocaleUpperCase();
    return {key,label,icon:kinds[kindOf(item)].icon};
  }
  /** Sort a copy with natural names and stable ties; unknown dates always stay last. */
  function sortArtifacts(artifacts, {by='starred',direction='desc'}={}) {
    const sign=direction==='asc'?1:-1;
    const title=(a,b)=>names.compare(a.title || '',b.title || '') || String(a.id).localeCompare(String(b.id));
    return [...artifacts].sort((a,b)=>{
      if(by==='starred' && !!a.starred!==!!b.starred)return a.starred?-1:1;
      if(by==='name')return sign*title(a,b);
      if(by==='type')return sign*names.compare(artifactType(a).label,artifactType(b).label) || title(a,b);
      const first=Date.parse(a[by==='created'?'created_at':'updated_at']),second=Date.parse(b[by==='created'?'created_at':'updated_at']);
      if(!Number.isFinite(first) || !Number.isFinite(second))return Number.isFinite(first)?-1:Number.isFinite(second)?1:title(a,b);
      return sign*(first-second) || title(a,b);
    });
  }
  /** Ignore stale or malformed browser preferences without affecting saved artifacts. */
  function validSort(value) {
    return value && Object.hasOwn(sortLabels,value.by) && ['asc','desc'].includes(value.direction)?{by:value.by,direction:value.direction}:{by:'starred',direction:'desc'};
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
  /** Accept bounded text exports without paths, executable suffixes or caller-owned URLs. */
  function exportFile(value) {
    if (!value || typeof value !== 'object') return null;
    const {name,type,content}=value;
    if (typeof name !== 'string' || !name.length || name.length > 120 || /[\\/\x00-\x1f\x7f]/.test(name) || name.startsWith('.')) return null;
    const formats={html:'text/html',json:'application/json',csv:'text/csv',txt:'text/plain'};
    const extension=/\.([a-z]+)$/i.exec(name)?.[1].toLowerCase();
    const mime=extension && Object.hasOwn(formats,extension) ? formats[extension] : null;
    if (!mime || typeof type !== 'string' || type.split(';')[0] !== mime || typeof content !== 'string' || content.length > 20*1024*1024) return null;
    return {name,type:mime+';charset=utf-8',content};
  }
  const exports = {filterArtifacts,sortArtifacts,artifactType,validSort,filterDesigns,kindOf,groupTargets,selectTargets,bundlePath,renderDocument,encodePath,feedbackTarget,filePath,exportFile,validVoiceSegment,appendVoiceSegment};
  if (typeof module !== 'undefined' && module.exports) { module.exports=exports; return; }

  const doc=root.document, content=doc.getElementById('hub-content'), dialog=doc.getElementById('hub-dialog');
  const sharedEditor=doc.documentElement.hasAttribute('data-shared-editor');
  const canManageWorkspace=()=>!sharedEditor||doc.documentElement.hasAttribute('data-workspace-owner');
  const studioHome=root.ArtifactSession.home();
  const nativeChat=sharedEditor||new URLSearchParams(root.location.search).get('chat')==='shared';
  doc.documentElement.toggleAttribute('data-native-conversation',nativeChat);
  const scopedChat=root.StudioNativeConversation.create({api});
  const collaborationChat=nativeChat?scopedChat:null;
  const conversationModes=new Map(),conversationChannels=new Map();
  const canComment=item=>!nativeChat||['edit','comment'].includes(item?.collaboration?.access?.role);
  const channelOf=id=>conversationChannels.get(id)||'shared';
  root.addEventListener('studio:before-conversation-change',event=>{
    const id=state.active,value=drafts.get(id);
    if(!id||(!value?.message?.trim()&&!value?.voice_segments?.length&&!attachments.get(id)?.length&&!sending.has(id)&&!artifactEditor.isEditing()))return;
    event.preventDefault();toast('Send or clear your current draft before switching conversations.',true);
  });
  const ownChatAgent=item=>item.collaboration?.access?.role==='edit'?(item.collaboration.agents.find(agent=>agent.mine&&agent.status==='listening')||item.collaboration.agents.find(agent=>agent.mine)):null;

  const extraIcons={voiceSend:'M12 20V4m-7 7 7-7 7 7',search:'m21 21-5-5M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15',image:'M3 3h18v18H3zM3 16l6-6 5 5 3-3 4 4M16 7h.01',updated:'M20 4v5h-5M19.2 8a8 8 0 1 0-4.7 11.6'};
  const icon=name => extraIcons[name]?`<svg class="ui-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${extraIcons[name]}"/>${name==='updated'?'<circle cx="20" cy="17" r="2" fill="currentColor" stroke="none"/>':''}</svg>`:root.SlideIcons.svg(name);
  const sizeLabel=size => root.SlideChatComposer.sizeLabel(Number(size) || 0);
  const chatImages=root.ChatImages.create({document:doc,escape,svg:icon});
  const coverLoader=root.HubCovers.create({onChanged:()=>void refresh()});
  const state={designActive:null,projects:[],projectTree:{nodes:[],content_hash:''},artifacts:[],designs:[],project:'all',kind:'all',search:'',active:null,detail:null,source:'',sourceView:false,selecting:false,zoom:1,viewport:'fluid',newRevision:null,loading:0};
  let sort=validSort(null);
  try{sort=validSort(JSON.parse(root.localStorage.getItem('artifact-studio:library-sort')));}catch{}
  const collapsedFolders=new Set();
  try{for(const id of JSON.parse(root.localStorage.getItem('artifact-studio:collapsed-folders') || '[]'))if(typeof id==='string')collapsedFolders.add(id);}catch{}
  let treeBusy=false;
  const previewUpdates=root.SlidePreviewUpdates.create({onChange:()=>{syncAutoUpdate();if(previewUpdates.enabled)void refresh();}});
  const drafts=new Map(), attachments=new Map(), sending=new Set(),discardedVoiceSegments=new Set();
  let importFiles=[],toastTimer,refreshing=false,regionStart=null,exportUrl=null,previewImport=null,properties=null,inspectorSelector=null,inspectorRect=null,voiceRecordingBase=null,feedbackVoiceHandle=null,modalEpoch=0;
  const voice=root.SlideVoice.create({onIdle:input=>{if(input?.id==='hub-feedback-text')voiceRecordingBase=null;lockComposer();if(state.newRevision && previewUpdates.enabled)void refresh();}});
  const workspacePanels=root.WorkspacePanels.create({doc,view:root,storage:root.localStorage,kind:'hub',onViewChange:()=>syncSelection()});
  const sharing=root.ArtifactSharing.create({doc,view:root});
  const artifactEditor=root.ArtifactEditor.create({
    getFrame:()=>doc.getElementById('hub-html-preview'),getArtifact:()=>state.detail,
    onInspector:data=>{inspectorRect=data.rect;inspectorSelector=data.selector;if(data.restoreControls||!artifactEditor.isEditing())properties?.setSelection(data);properties?.reposition();},
    onState:next=>{properties?.setState(next);if(!next.dirty && !next.busy && state.newRevision && previewUpdates.enabled)queueMicrotask(()=>void refresh());},
    onSend:async body=>{
      const item=state.detail;
      try{await postArtifactFeedback(item,body);}
      catch(error){if(error.status===409)await refreshArtifactReview(item.id);throw error;}
      const detail=await artifactDetail(item.id);
      if(state.active===item.id){state.detail={...state.detail,feedback:detail.feedback,mode:detail.mode,delivery:detail.delivery,collaboration:detail.collaboration};renderChat();}
      toast(item.mode==='batch'?'Added to batch.':'Saved for agent.');
    },onError:message=>toast(message,true)
  });
  const elementMenu=root.ElementCommands.menu({doc,onError:message=>toast(message,true)});
  const hubMenu=root.ElementCommands.menu({doc,id:'hub-context-menu',onError:message=>toast(message,true)});
  let elementMenuEpoch=0;
  let pendingImageExport=null;
  const imageSelectors=new Set();
  function saveSelectionImage(original=false){
    const item=state.detail,value=draft(),frame=doc.getElementById('hub-html-preview');
    if(!frame||!value.targets.length||value.content_hash!==item.content_hash||value.targets.some(target=>target.kind!=='element'))throw Error('Select objects on this version first.');
    if(pendingImageExport)throw Error('An image is already being prepared.');
    const requestId=root.crypto.randomUUID(),timeout=root.setTimeout(()=>{if(pendingImageExport?.requestId===requestId){pendingImageExport=null;toast('The preview did not respond. Reload it and try again.',true);}},15000);
    pendingImageExport={requestId,id:item.id,revision:item.revision,content_hash:item.content_hash,original,frame:frame.contentWindow,timeout};
    toast('Preparing image…');frame.contentWindow.postMessage({type:'artifact:selection-export',request_id:requestId,selectors:value.targets.map(target=>target.selector)},'*');
  }
  function imageMenu(image,point){
    const id=state.active,run=action=>async()=>{if(id!==state.active||!image.isConnected)throw Error('The image changed. Open its menu again.');await action();};
    elementMenu.panel.setAttribute('aria-label','Image actions');
    elementMenu.show({...point,focus:image,commands:[
      {label:'Save as image…',icon:icon('download'),run:run(()=>root.ImageExport.raster(image))},
      {label:'Save original image…',icon:icon('image'),run:run(()=>{const link=doc.createElement('a');link.href=image.currentSrc||image.src;const url=new URL(link.href);link.download=url.protocol==='data:'?'image':decodeURIComponent(url.pathname.split('/').at(-1)||'image');doc.body.append(link);link.click();link.remove();})}
    ]});
  }
  function singleElement(){const targets=state.detail&&draft().targets;return !!(state.selecting&&kindOf(state.detail)==='html'&&targets?.length===1&&targets[0].kind==='element'&&draft().content_hash===state.detail.content_hash);}
  function runElementCommand(command){
    if(!singleElement()||artifactEditor.isBusy()||sending.has(state.active))return false;
    if(command==='undo'||command==='redo'){
      if(voice.isActive()){toast('Stop dictation before undoing an element edit.');return true;}
      void artifactEditor[command]();return true;
    }
    if(typeof command==='object'){artifactEditor.nudge(command.key,command.step);return true;}
    if(command==='delete'){if(voice.isActive()){toast('Stop dictation before deleting an element.');return true;}artifactEditor.delete();return true;}
    if(command==='edit'){void focusElementText();return true;}
    if(command==='send'){doc.querySelector('#hub-element-properties [data-action=send]')?.click();return true;}
    if(command==='cancel'){properties?.cancelVoice();if(artifactEditor.isEditing())artifactEditor.cancel();else{draft().targets=[];syncSelection();voiceBoundary();}return true;}
    return false;
  }
  async function focusElementText(){
    const id=state.active,loading=state.loading;await new Promise(resolve=>root.requestAnimationFrame(resolve));if(id!==state.active||loading!==state.loading)return;const selector=draft().targets[0]?.selector;
    for(let count=0;count<40&&!artifactEditor.ready();count++){await new Promise(resolve=>setTimeout(resolve,16));if(id!==state.active||loading!==state.loading||selector!==draft().targets[0]?.selector)return;}
    if(id===state.active&&loading===state.loading&&selector===draft().targets[0]?.selector&&artifactEditor.ready())properties?.focusText();
  }
  async function showElementMenu(point){
    if(!state.detail||!state.selecting||!draft().targets.length)return;
    const id=state.active,loading=state.loading,selector=draft().targets[0]?.selector,epoch=++elementMenuEpoch;
    if(singleElement())for(let count=0;count<40&&!artifactEditor.ready();count++){await new Promise(resolve=>setTimeout(resolve,16));if(id!==state.active||loading!==state.loading||epoch!==elementMenuEpoch)return;}
    if(id!==state.active||loading!==state.loading||epoch!==elementMenuEpoch)return;
    const editable=singleElement()&&artifactEditor.ready(),primary=/Mac|iPhone|iPad/.test(root.navigator.platform)?'⌘':'Ctrl+';
    const run=command=>()=>{if(state.active===id&&state.loading===loading&&draft().targets[0]?.selector===selector)runElementCommand(command);};
    const entry=(label,glyph,execute,extra={})=>({label,icon:icon(glyph),run:execute,...extra});
    elementMenu.show({...point,focus:doc.getElementById('hub-viewer'),commands:[
      entry('Undo','restore',run('undo'),{keys:primary+'Z',disabled:!editable||!artifactEditor.canUndo()}),
      entry('Redo','refresh',run('redo'),{keys:primary+'Shift+Z',disabled:!editable||!artifactEditor.canRedo()}),
      {separator:true},entry('Edit text','edit',run('edit'),{keys:'F2 / Enter',disabled:!editable||!artifactEditor.canEdit()}),
      entry('Delete element','trash',run('delete'),{keys:'Delete',disabled:!editable,danger:true}),
      {separator:true},entry('Save as image…','download',()=>saveSelectionImage(),{disabled:kindOf(state.detail)!=='html'}),
      ...(singleElement()&&imageSelectors.has(draft().targets[0]?.selector)?[entry('Save original image…','image',()=>saveSelectionImage(true))]:[]),
      {separator:true},entry('Send edit','send',run('send'),{keys:primary+'Enter',disabled:!editable||!artifactEditor.isEditing()}),
      entry('Cancel edit','close',run('cancel'),{keys:'Esc',disabled:!editable||!artifactEditor.isEditing()}),
      {separator:true},entry('Comment on selection','chat',()=>doc.getElementById('hub-feedback-text')?.focus()),
      entry('Clear selection','cursor',()=>{if(state.active===id){draft().targets=[];syncSelection();voiceBoundary();}},{disabled:artifactEditor.isEditing()})
    ]});
  }
  doc.addEventListener('keydown',event=>{
    if(elementMenu.isOpen()||dialog.open||!event.target.closest('#hub-viewer,#hub-element-properties'))return;
    const command=root.ElementCommands.shortcut(event);if(!command||!state.selecting)return;
    if(command==='menu'&&draft().targets.length){event.preventDefault();event.stopPropagation();const rect=doc.getElementById('hub-viewer').getBoundingClientRect();void showElementMenu({x:rect.left+16,y:rect.top+16});return;}
    if(command==='edit'&&event.target.id==='hub-viewer'){event.preventDefault();void focusElementText();return;}
    if(!singleElement()){if(command==='cancel'&&draft().targets.length&&!artifactEditor.isEditing()){event.preventDefault();draft().targets=[];syncSelection();voiceBoundary();}return;}
    event.preventDefault();event.stopPropagation();runElementCommand(command);
  });
  doc.addEventListener('contextmenu',event=>{
    if(!state.selecting||root.ElementCommands.typing(event.target)||!event.target.closest('#hub-markdown,#hub-image-wrap,#hub-element-properties'))return;
    const block=event.target.closest('#hub-markdown [data-target-index]');
    if(block&&!draft().targets.some(target=>target.block_index===Number(block.dataset.targetIndex))){const quote=block.textContent.trim().slice(0,2000),start=state.source.indexOf(quote),unique=start>=0&&start===state.source.lastIndexOf(quote);setTarget({kind:'text',label:quote.slice(0,80),quote,selector:`[data-target-index="${block.dataset.targetIndex}"]`,block_index:Number(block.dataset.targetIndex),...(unique?{start,end:start+quote.length}:{})},false);}
    if(!draft().targets.length)return;event.preventDefault();void showElementMenu({x:event.clientX,y:event.clientY});
  });
  if(canManageWorkspace())root.ProjectFolders.create({content,getArtifacts:()=>state.artifacts,getProjects:()=>state.projects,
    onMove:async(id,projectId)=>{
      try{await moveArtifact(id,projectId);}
      catch(error){if(error.code==='project-access-changed')moveDialog(id,projectId,error);else throw error;}
    },
    onError:error=>toast(error.message,true)
  });
  if(canManageWorkspace())root.ProjectNavigation.create({content,getTree:()=>state.projectTree,onMove:change=>changeTree(change),
    onExpand:id=>{
      if(!collapsedFolders.delete(id))return;
      root.localStorage.setItem('artifact-studio:collapsed-folders',JSON.stringify([...collapsedFolders]));
      const row=doc.querySelector(`[data-tree-node="${id}"]`),copy=doc.createElement('div');copy.innerHTML=sidebar();
      const children=copy.querySelector(`[data-folder-children="${id}"]`);if(row&&children){row.after(children);row.querySelector('[data-toggle-folder]').setAttribute('aria-expanded','true');row.querySelector('.project-tree-open>.ui-icon').outerHTML=icon('folderOpen');quickAccess.decorateProjects();}
    },onError:error=>toast(error.message,true)});
  function resetEditor(){if(!artifactEditor.reset())return false;properties?.clear();inspectorSelector=null;return true;}
  function syncInspector(){
    const value=state.detail && draft(),single=value?.targets.length===1 && value.targets[0];
    if(state.selecting && kindOf(state.detail || {})==='html' && value.content_hash===state.detail.content_hash && single?.kind==='element'){
      if(inspectorSelector!==single.selector)artifactEditor.inspect(single);
    }else if(!artifactEditor.isEditing() && !artifactEditor.isBusy()){resetEditor();}
  }
  try {
    const saved=nativeChat?{}:JSON.parse(root.localStorage.getItem('artifact-studio:artifact-drafts:v1') || '{}');
    for (const [id,value] of Object.entries(saved)) if (value && typeof value.message === 'string') {
      const seen=new Set(),segments=Array.isArray(value.voice_segments)?value.voice_segments.filter(segment=>validVoiceSegment(segment) && !seen.has(segment.request_id) && seen.add(segment.request_id)).slice(0,100):[];
      drafts.set(id,{...value,targets:Array.isArray(value.targets) ? value.targets : [],voice_segments:segments});
    }
  } catch {}
  const selectedProject=id => state.projects.find(project=>project.id===id);
  const quickAccess=root.ProjectQuickAccess.create({menu:doc.getElementById('hub-tools-menu'),button:doc.getElementById('hub-tools-button'),keyboard:true,onProject:id=>home(id),trackProjects:true});
  let initialProjectRecorded=false;
  const projectTitle=id => id==='designs'?'Designs':state.projectTree.nodes.find(node=>'folder:'+node.id===id)?.title || selectedProject(id)?.title || (id === 'all' ? 'All artifacts' : 'Project');
  const selectedFolder=()=>state.project.startsWith('folder:')?state.project.slice(7):state.projectTree.nodes.find(node=>node.id===state.project)?.parent_id ?? null;
  function rememberFolders(){try{root.localStorage.setItem('artifact-studio:collapsed-folders',JSON.stringify([...collapsedFolders]));}catch{}}
  function closeTreeMenu(){hubMenu.close();}
  function toggleFolder(id){collapsedFolders.has(id)?collapsedFolders.delete(id):collapsedFolders.add(id);rememberFolders();renderProjectSidebar();doc.querySelector(`[data-toggle-folder="${id}"]`)?.focus({preventScroll:true});}
  function renderProjectSidebar(workspace=false){const aside=content.querySelector('aside.hub-sidebar');if(aside){aside.outerHTML=sidebar(workspace);root.StudioAbout?.mountServerVersion();}}
  async function changeTree(change){
    if(treeBusy)return;treeBusy=true;closeTreeMenu();
    try{
      state.projectTree=await api('/api/hub/project-tree',{...change,expected_hash:state.projectTree.content_hash});
      if(change.parent_id)collapsedFolders.delete(change.parent_id);
      if(change.action==='remove')collapsedFolders.delete(change.id);rememberFolders();
      if(state.project.startsWith('folder:') && !state.projectTree.nodes.some(node=>'folder:'+node.id===state.project))state.project='all';
      library();const focus=change.id && doc.querySelector(`[data-tree-node="${change.id}"] .project-tree-open`);focus?.focus({preventScroll:true});
    }catch(error){await loadRegistry();library();throw error;}finally{treeBusy=false;}
  }
  function folderDialog(mode='create',id=null,parent=selectedFolder()){
    closeTreeMenu();const folder=state.projectTree.nodes.find(node=>node.id===id);
    modal(mode==='rename'?'Rename folder':'New folder',`<form id="hub-folder-form" class="hub-form" data-mode="${mode}" data-id="${escape(id || '')}" data-parent="${escape(parent || '')}"><label class="hub-field"><span>Name</span><input name="title" required maxlength="200" value="${escape(folder?.title || '')}" placeholder="e.g. Allianz" autofocus></label><p class="hub-compose-error" role="alert"></p><div class="hub-form-actions">${button('close-dialog','Cancel','close')}<button type="submit" class="hub-primary">${mode==='rename'?'Save':'Create folder'}</button></div></form>`);
    dialog.querySelector('input').focus();dialog.querySelector('input').select();
  }
  function treeMoveDialog(id){
    closeTreeMenu();const tree=state.projectTree,node=tree.nodes.find(item=>item.id===id);
    const folders=tree.nodes.filter(item=>item.kind==='folder' && item.id!==id && !root.ProjectNavigation.ancestors(tree,item.id).includes(id));
    const path=folder=>[...root.ProjectNavigation.ancestors(tree,folder.id).reverse().map(id=>tree.nodes.find(node=>node.id===id).title),folder.title].join(' / ');
    modal('Move '+(node.kind==='folder'?'folder':'project'),`<form id="hub-tree-move-form" class="hub-form" data-id="${escape(id)}"><label class="hub-field"><span>Folder</span><select name="parent_id"><option value="">Top level</option>${folders.map(folder=>`<option value="${escape(folder.id)}" ${node.parent_id===folder.id?'selected':''}>${escape(path(folder))}</option>`).join('')}</select></label><p class="hub-compose-error" role="alert"></p><div class="hub-form-actions">${button('close-dialog','Cancel','close')}<button type="submit" class="hub-primary">Move</button></div></form>`);
  }
  /** Share one menu's focus, positioning and keyboard behavior across library contexts. */
  function showHubMenu(anchor,label,commands,point=null){
    elementMenuEpoch++;elementMenu.close();
    const bounds=anchor.getBoundingClientRect(),background=anchor.matches('.hub-main,aside.hub-sidebar'),focus=anchor.matches('button,[tabindex]')?anchor:anchor.querySelector('.hub-card-open,.project-tree-open,button') || anchor;
    hubMenu.panel.setAttribute('aria-label',label);
    hubMenu.show({x:point?.x ?? bounds.left+(background?16:0),y:point?.y ?? (background?bounds.top+16:bounds.bottom+4),commands,focus});
  }
  const menuCommand=(label,glyph,run,options={})=>({label,icon:icon(glyph),run,...options});
  function presentationCreationUrl(project){
    const url=new URL(studioHome,root.location.href);
    url.search=new URLSearchParams({editor:'1',new:'1'});
    if(sharedEditor&&selectedProject(project)&&project!=='shared-with-me')url.searchParams.set('project',project);
    return url.href;
  }
  function newPresentation(project=state.project){
    if(artifactEditor.finish())root.location.href=presentationCreationUrl(project);
  }
  if(sharedEditor)doc.querySelector('[data-new-presentation]').addEventListener('click',event=>{
    event.preventDefault();newPresentation();
  });
  function creationCommands(project=state.project,parent=selectedFolder()){
    const create=menuCommand('New project','project',()=>projectDialog(false,parent));
    const upload=menuCommand('Import artifact','upload',()=>importDialog(project));
    return [create,menuCommand('New folder','folderPlus',()=>folderDialog('create',null,parent)),upload,menuCommand('New presentation','slideAdd',()=>newPresentation(project))];
  }
  /** Resolve tree changes when invoked, retaining the row captured when the menu opened. */
  async function treeAction(action,id){
    const tree=state.projectTree,item=tree.nodes.find(item=>item.id===id);if(!item)return;
    if(action==='rename')return folderDialog('rename',id);
    if(action==='move')return treeMoveDialog(id);
    if(action==='remove'){await changeTree({action:'remove',id});toast('Folder removed. Projects kept.');return;}
    const siblings=root.ProjectNavigation.children(tree,item.parent_id),index=siblings.findIndex(item=>item.id===id);
    const plan=action==='out'?root.ProjectNavigation.placement(tree,id,item.parent_id,'after'):root.ProjectNavigation.placement(tree,id,siblings[index+(action==='up'?-1:1)]?.id,action==='up'?'before':'after');
    if(plan)await changeTree(plan);
  }
  function projectTreeMenu(anchor,id=null,point=null){
    const tree=state.projectTree,node=tree.nodes.find(item=>item.id===id),folder=node?.kind==='folder';
    if(!node){showHubMenu(anchor,'Add to projects',creationCommands('all',null),point);return;}
    const project=selectedProject(id),siblings=root.ProjectNavigation.children(tree,node.parent_id),index=siblings.findIndex(item=>item.id===id),key=folder?'folder:'+id:id;
    const opener=anchor.matches('button')?anchor:anchor.querySelector('.project-tree-open');
    const commands=[menuCommand('Open','folderOpen',()=>home(key))];
    if(!canManageWorkspace()){
      if(canShareProject(project))commands.push(menuCommand('Share files','share',()=>shareProject(id,opener)));
      showHubMenu(anchor,'Project actions',commands,point);return;
    }
    if(folder)commands.push(menuCommand(collapsedFolders.has(id)?'Expand folder':'Collapse folder','folder',()=>toggleFolder(id)));
    else if(project){
      if(!sharedEditor)commands.push(menuCommand('Project brief','notes',()=>contextDialog({projectId:id})));
      if(root.StudioInvitation?.available)commands.push(menuCommand('Share link…','share',()=>root.StudioInvitation.open({kind:'project',id,title:project.title,anchor})));
      if(canShareProject(project))commands.push(menuCommand(shareFileLabel('Share project'),'share',()=>shareProject(id,opener)));
    }
    commands.push({separator:true},...creationCommands(key,folder?id:node.parent_id).filter(command=>command.label!=='Import artifact'||folder||!project?.legacy),{separator:true},menuCommand('Move to…','folder',()=>treeAction('move',id)),menuCommand('Move up','up',()=>treeAction('up',id),{keys:'Alt+↑',disabled:index===0}),menuCommand('Move down','down',()=>treeAction('down',id),{keys:'Alt+↓',disabled:index===siblings.length-1}));
    if(node.parent_id)commands.push(menuCommand('Move out','left',()=>treeAction('out',id),{keys:'Alt+←'}));
    if(folder)commands.push({separator:true},menuCommand('Rename','edit',()=>treeAction('rename',id)),menuCommand('Remove folder','trash',()=>treeAction('remove',id),{danger:true}));
    showHubMenu(anchor,folder?'Folder actions':'Project actions',commands,point);
  }
  /** Keep navigation, dialogs and copied links bound to the right-clicked artifact. */
  async function openArtifactCommand(id,mode='open'){
    if(state.active!==id)await openArtifact(id);
    if(state.active!==id || !state.detail)return;
    if(mode==='view')return action('clean-view');
    if(mode==='comment'&&canComment(state.detail)){workspacePanels.openChat();doc.getElementById('hub-feedback-text')?.focus();}
  }
  async function copyArtifactLink(item){
    const url=new URL(studioHome,root.location.href);url.searchParams.set(item.legacy_deck_id?'deck':'artifact',item.legacy_deck_id || item.id);
    await root.navigator.clipboard.writeText(url.href);toast('Artifact link copied.');
  }
  async function presentArtifact(item){
    const session=await root.ArtifactSession.get();
    if(!artifactEditor.finish())return;
    const viewer=session.mode==='hosted'?'presenter':'index.html';
    root.location.href=studioPath(`/decks/${encodeURIComponent(item.legacy_deck_id)}/${viewer}?present=1`);
  }
  function artifactMenu(anchor,id,point=null){
    const item=state.artifacts.find(item=>item.id===id);if(!item)return;
    const commands=[menuCommand('Open','folderOpen',()=>openArtifactCommand(id))];
    if(item.legacy_deck_id)commands.push(menuCommand('Present','present',()=>presentArtifact(item)));
    else {
      commands.push(menuCommand('View','eye',()=>openArtifactCommand(id,'view')));
      if(!sharedEditor||['comment','edit'].includes(item.access_role))commands.push(menuCommand('Comment','chat',()=>openArtifactCommand(id,'comment')));
      if(canManageWorkspace())commands.push(menuCommand('Version history','history',()=>openArtifactHistory(item)));
    }
    if(root.StudioInvitation?.available)commands.push({separator:true},menuCommand('Share link…','share',()=>root.StudioInvitation.open({kind:'artifact',id,title:item.title,anchor})));
    if(canManageWorkspace())commands.push({separator:true},menuCommand('Move to project','folder',()=>moveDialog(id)));
    const opener=anchor.matches('button')?anchor:anchor.querySelector('.hub-card-open');
    commands.push(menuCommand(shareFileLabel('Share'),'share',()=>shareArtifact(id,opener)));
    commands.push(menuCommand('Copy link','copy',()=>copyArtifactLink(item)));
    if(!sharedEditor)commands.push({separator:true},menuCommand('Agent context','code',()=>contextDialog({artifactId:id})));
    showHubMenu(anchor,'Artifact actions',commands,point);
  }
  const fileRecords=item => (item.files || []).map(file=>typeof file==='string'?{path:file}:file);
  const draft = (item=state.detail) => {
    if (!drafts.has(item.id)) drafts.set(item.id,{message:'',targets:[],revision:item.revision,content_hash:item.content_hash,request_id:null});
    return drafts.get(item.id);
  };
  const persist=()=>{if(nativeChat)return;try{root.localStorage.setItem('artifact-studio:artifact-drafts:v1',JSON.stringify(Object.fromEntries([...drafts].filter(([,value])=>value.message || value.targets.length || value.voice_segments?.length))));}catch{}};
  const isAdditive=event=>event.metaKey || event.ctrlKey || event.shiftKey;
  const button=(action,label,name,extra='')=>`<button type="button" data-action="${action}" ${extra}>${icon(name)}${label?`<span>${label}</span>`:''}</button>`;
  function status(item,id='') {
    const state=item.status||'draft';
    const label={approved:'Approved',updated:'Updated',draft:'To review'}[state];
    const glyph={approved:'check',updated:'updated',draft:'clock'}[state];
    return `<span ${id?`id="${escape(id)}"`:''} class="hub-status ${state}">${icon(glyph)}${label}</span>`;
  }

  const shareFileLabel=local=>sharedEditor||root.StudioInvitation?.available?'Share files':local;
  const canShareProject=project=>project&&project.id!=='shared-with-me';
  /** Put Share beside the item it exports, keeping library navigation unchanged. */
  function mountSharing(cardsOnly=false) {
    const control=(label,key,id,css='')=>{const node=doc.createElement('button');node.type='button';node.dataset[key]=id;node.className=css;node.innerHTML=icon('share');node.setAttribute('aria-label',label);node.title=label;return node;};
    for(const card of content.querySelectorAll('.hub-card[data-drag-artifact]'))card.append(control(`Share ${state.artifacts.find(item=>item.id===card.dataset.dragArtifact)?.title || 'artifact'}`,'shareArtifact',card.dataset.dragArtifact,'hub-card-share'));
    if(cardsOnly)return;
    const project=selectedProject(state.project);
    if(!state.active && canShareProject(project)){const header=content.querySelector('.hub-heading'),search=header.querySelector('.hub-search'),actions=doc.createElement('div');actions.className='hub-heading-actions';actions.append(control('Share project','shareProject',project.id),search);header.append(actions);}
    if(state.active){const heading=content.querySelector('.hub-edit-heading'),approve=heading.querySelector('.hub-approve'),actions=doc.createElement('div');actions.className='hub-artifact-actions';actions.append(control('Share artifact','shareArtifact',state.active),approve);heading.append(actions);}
  }
  async function shareArtifact(id,opener) {
    if(id===state.active && !artifactEditor.finish())return;
    const item=id===state.active?state.detail:state.artifacts.find(item=>item.id===id);
    if(!item)return;
    await sharing.open({scope:'artifact',id:item.id,title:item.title,kind:kindOf(item),presentation:!!item.legacy_deck_id,expected_hash:item.content_hash,bundle:fileRecords(item).length>1},opener);
  }
  async function shareProject(id,opener) {
    const item=selectedProject(id),children=state.artifacts.filter(item=>item.project_id===id);
    if(canShareProject(item))await sharing.open({scope:'project',id,title:item.title,count:children.length,presentation:children.some(item=>item.legacy_deck_id)},opener);
  }

  async function api(path, body) {
    const response=await fetch(studioPath(path),{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
    root.ArtifactSession?.observe(response);
    root.ArtifactSession?.assertActive();
    let result;
    try{result=await response.json();}catch{if(!response.ok)throw new Error(`Request failed (${response.status}).`);return {};}
    if(!response.ok){const error=new Error(typeof result.detail==='string'?result.detail:result.error || `Request failed (${response.status}).`);error.status=response.status;error.code=result.code;error.accessHash=result.access_hash;throw error;}
    return result;
  }
  async function artifactDetail(id) {
    const detail=await api(artifactPath(id));
    if((!collaborationChat&&!root.StudioInvitation?.available)||detail.legacy_deck_id)return detail;
    const info=await scopedChat.read(id,{channel:channelOf(id)});
    const scoped=info.messages.map(message=>({
      id:-message.id,author:message.author===message.person?'user':'agent',
      name:message.author===message.person?(message.person===info.person?'You':message.author_name):(info.agents.find(agent=>agent.id===message.author)?.name||'Agent'),
      collaborator:message.author===message.person&&message.person!==info.person,
      message:message.message,created_at:new Date(message.created*1000).toISOString(),
      content_hash:message.content_hash,target:message.target,released:Boolean(message.released),files:message.files||[],scoped:true
    }));
    const feedback=sharedEditor?scoped:[...(detail.feedback||[]),...scoped];
    if(!collaborationChat)return {...detail,feedback};
    return {...detail,collaboration:info,feedback,mode:conversationModes.get(id)||'live',
      delivery:{live_available:info.agents.some(agent=>agent.mine&&agent.status==='listening')}};
  }
  async function postArtifactFeedback(item,body) {
    if(!collaborationChat)return api(artifactPath(item.id,'/feedback'),body);
    if(!canComment(item))throw new Error('You have view access.');
    const channel=channelOf(item.id),assigned=body.edit_intent||channel==='private'?ownChatAgent(item)?.id||'':'';
    if((body.edit_intent||channel==='private')&&!assigned)throw new Error('Connect your agent before requesting an edit.');
    return collaborationChat.send(item.id,{message:body.message,files:body.files,requestId:body.request_id,contentHash:body.content_hash,target:body.target,editIntent:body.edit_intent,channel,assigned,released:body.expected_mode!=='batch'});
  }
  function toast(message, error=false) {
    const node=doc.getElementById('hub-toast');node.textContent=message;node.classList.toggle('is-error',error);node.hidden=false;
    clearTimeout(toastTimer);toastTimer=setTimeout(()=>{node.hidden=true;},error?9000:4500);
  }
  function headerIcons() {
    for(const node of doc.querySelectorAll('[data-icon]')){const label=node.textContent;node.innerHTML=icon(node.dataset.icon)+(label?`<span>${escape(label)}</span>`:'');}
  }
  function sidebar(workspace=false) {
    const footer='<div class="app-sidebar-footer" data-server-version-slot hidden></div>';
    if(workspace){
      const item=state.detail;
      return `<aside class="hub-sidebar" aria-label="Project artifacts">${button('home','Library','grid','class="hub-library-link"')}<label class="hub-project-picker">${icon('project')}<select id="hub-project-picker" aria-label="Choose project">${state.projects.map(project=>`<option value="${escape(project.id)}" ${project.id===item.project_id?'selected':''}>${escape(project.title)}</option>`).join('')}</select></label><nav class="hub-project-artifacts" aria-label="Related artifacts">${state.artifacts.filter(other=>other.project_id===item.project_id).map(other=>`<button type="button" data-artifact="${escape(other.id)}" aria-current="${other.id===item.id}" title="${escape(other.title)}">${icon(kinds[kindOf(other)].icon)}<span>${escape(other.title)}</span>${other.status==='updated'?`<span class="hub-status updated" title="Updated">${icon('updated')}</span>`:''}</button>`).join('')}</nav>${footer}</aside>`;
    }
    return `<aside class="hub-sidebar" aria-label="Projects"><button type="button" data-project="all" aria-current="${state.project==='all'}">${icon('grid')}<span>All artifacts</span><span class="hub-count">${state.artifacts.length}</span></button><button type="button" data-project="designs" aria-current="${state.project==='designs'}">${icon('palette')}<span>Designs</span><span class="hub-count">${state.designs.length}</span></button><button type="button" data-action="favorite-slides">${icon('favoriteSlides')}<span>Favorite slides</span></button><div class="hub-sidebar-label">Projects${button('project-add','','plus','aria-label="Add project or folder" title="Add project or folder" aria-haspopup="menu"')}</div>${root.ProjectNavigation.markup({tree:state.projectTree,projects:state.projects,selected:state.project,collapsed:collapsedFolders,icon,manage:canManageWorkspace()})}${footer}</aside>`;
  }
  function filteredArtifacts(){return sortArtifacts(filterArtifacts(state.artifacts,{...state,projectIds:state.project.startsWith('folder:')?root.ProjectNavigation.projectsIn(state.projectTree,state.project.slice(7)):null}),sort);}
  function visibleItems() {
    return state.project==='designs'?filterDesigns(state.designs,state.search):filteredArtifacts();
  }
  function resultCount() {
    const count=visibleItems().length;
    return `${count} ${state.project==='designs'?'design':'artifact'}${count===1?'':'s'}`;
  }
  function designCards(){return designLibrary.cards(visibleItems(),state.search);}
  function reverseSortLabel() {
    return ['updated','created'].includes(sort.by)?(sort.direction==='desc'?'Oldest first':'Newest first'):(sort.direction==='asc'?'Z to A':'A to Z');
  }
  function sortControl() {
    return `<div class="hub-sort">${icon('sort')}<select id="hub-sort" aria-label="Sort artifacts" title="Sort artifacts">${Object.entries(sortLabels).map(([key,label])=>`<option value="${key}" ${sort.by===key?'selected':''}>${label}</option>`).join('')}</select>${button('sort-direction','',sort.direction==='asc'?'up':'down',`aria-label="${reverseSortLabel()}" title="${reverseSortLabel()}" ${sort.by==='starred'?'hidden':''}`)}</div>`;
  }
  function refreshCards() {
    const grid=doc.getElementById('hub-grid');grid.classList.toggle('is-grouped',state.project!=='designs' && sort.by==='type');grid.innerHTML=cards();
    doc.getElementById('hub-result-count').textContent=resultCount();mountSharing(true);coverLoader.mount(grid);
  }
  function updateSort(value) {
    sort=validSort(value);
    try{root.localStorage.setItem('artifact-studio:library-sort',JSON.stringify(sort));}catch{}
    const control=doc.querySelector('.hub-sort');
    if(control){
      control.querySelector('select').value=sort.by;
      const toggle=control.querySelector('button');toggle.innerHTML=icon(sort.direction==='asc'?'up':'down');toggle.setAttribute('aria-label',reverseSortLabel());toggle.title=reverseSortLabel();toggle.hidden=sort.by==='starred';
    }
    refreshCards();
  }
  function cards() {
    if(state.project==='designs')return designCards();
    const items=filteredArtifacts();
    if(!items.length)return `<div class="hub-empty">${icon(state.search?'eye':'folder')}<h2>${state.search || state.kind!=='all'?'No matching artifacts':state.project==='all'?'A place for your work':'This project is ready'}</h2><p>${state.search || state.kind!=='all'?'Try another search or format.':'Import a page, document, image, presentation or a bundle of files.'}</p>${state.search || state.kind!=='all'?'':`<div class="hub-empty-actions">${button('import','Import files','upload','class="hub-primary"')}</div>`}</div>`;
    const card=item=>{
      const statusId='artifact-status-'+item.id;
      const kind=kinds[kindOf(item)],preview=kindOf(item)==='file'?'':studioPath(item.preview_url || `/api/hub/previews/${encodeURIComponent(item.id)}?v=${encodeURIComponent(item.content_hash || '')}`);
      const safePreview=root.HubCovers.coverURL(preview),fallback=root.HubCovers.coverURL(item.preview_fallback_url);
      return `<article class="hub-card" draggable="${canManageWorkspace()}" data-drag-artifact="${escape(item.id)}"><button type="button" class="hub-card-open" data-artifact="${escape(item.id)}" aria-describedby="${escape(statusId)}" aria-label="Open ${escape(item.title)}, ${kind.singular}, ${escape(item.project_title || projectTitle(item.project_id))}"><span class="hub-cover"><span class="hub-cover-fallback">${icon(kind.icon)}<span>${kind.singular}</span><small>${escape(item.entrypoint || '')}</small></span>${safePreview?`<img alt="" draggable="false" decoding="async" data-preview-image data-cover-url="${escape(safePreview)}" data-cover-fallback="${escape(fallback)}" data-cover-cached="${item.preview_cached===true}" data-cover-renderer="${item.kind!=='image'}" hidden><span class="hub-cover-status" data-cover-status hidden></span>`:''}</span><span class="hub-card-info"><span class="hub-card-title">${icon(kind.icon)}<span title="${escape(item.title)}">${escape(item.title)}</span></span><span class="hub-card-meta"><span>${escape(item.project_title || projectTitle(item.project_id))}</span>${status(item,statusId)}</span></span></button>${root.FavoriteSlides.button('artifact',item.id,item.title,'','hub-card-star')}${safePreview?`<button type="button" class="hub-cover-retry" data-action="retry-cover" data-cover-retry="${escape(safePreview)}" aria-label="Retry preview for ${escape(item.title)}" title="Retry preview" hidden>${icon('refresh')}</button>`:''}<button type="button" class="hub-card-move" data-move-artifact="${escape(item.id)}" aria-label="Move ${escape(item.title)} to project" title="Move to project">${icon('folder')}</button></article>`;
    };
    if(sort.by!=='type')return items.map(card).join('');
    const groups=Map.groupBy(items,item=>artifactType(item).key);
    return [...groups].map(([key,group],index)=>{
      const type=artifactType(group[0]);
      return `<section class="hub-type-group" aria-labelledby="hub-type-${index}" data-file-type="${escape(key)}"><h2 id="hub-type-${index}">${icon(type.icon)}${escape(type.label)}<span>${group.length}</span></h2><div class="hub-grid">${group.map(card).join('')}</div></section>`;
    }).join('');
  }
  function library() {
    if(sharedEditor)doc.querySelector('[data-new-presentation]').href=presentationCreationUrl(state.project);
    const collaboration=doc.querySelector('[data-collaborate]');
    if(collaboration)delete collaboration.dataset.artifact;
    if(!initialProjectRecorded){initialProjectRecorded=true;if(!sharedEditor&&selectedProject(state.project))void quickAccess.visit({project_id:state.project});}
    void voice.stop();
    properties?.destroy();properties=null;
    const designs=state.project==='designs';
    for(const node of doc.querySelectorAll('.hub-top-actions [data-design-action]'))node.hidden=!designs;
    for(const node of doc.querySelectorAll('.hub-top-actions [data-action="new-project"],.hub-top-actions [data-action="import"],.hub-top-actions [data-action="context"]'))node.hidden=designs;
    content.innerHTML=`<div class="hub-layout">${sidebar()}<main class="hub-main" tabindex="0" aria-label="Artifact library"><header class="hub-heading"><div><h1>${escape(projectTitle(state.project))}</h1><p id="hub-result-count" class="hub-heading-count">${resultCount()}</p></div><label class="hub-search">${icon('search')}<input type="search" id="hub-search" placeholder="Search ${designs?'designs':'artifacts'}…" aria-label="Search ${designs?'designs':'artifacts'}" value="${escape(state.search)}"></label></header><div class="hub-library-toolbar" ${designs?'hidden':''}><nav class="hub-chips" aria-label="Artifact formats">${[['all',{label:'All',icon:'grid'}],...Object.entries(kinds)].map(([key,kind])=>`<button type="button" data-kind="${key}" aria-pressed="${state.kind===key}">${key==='all'?'':icon(kind.icon)}${kind.label}</button>`).join('')}</nav>${sortControl()}</div><div id="hub-grid" class="hub-grid ${!designs && sort.by==='type'?'is-grouped':''}">${cards()}</div></main></div>`;
    doc.title=`${projectTitle(state.project)} · Artifact Studio`;workspacePanels.refresh();mountSharing();root.StudioAbout?.mountServerVersion();quickAccess.decorateProjects();coverLoader.mount(doc.getElementById('hub-grid'));
  }
  function fileList(item) {
    return `<ul class="hub-file-list">${fileRecords(item).map(file=>`<li><a href="${escape(filePath(item,file.path || file.name))}" download>${icon('notes')}<span>${escape(file.path || file.name)}</span></a>${file.size!==undefined?`<small>${sizeLabel(file.size)}</small>`:''}</li>`).join('')}</ul>`;
  }
  function viewer(item) {
    const kind=kindOf(item), sourceView=kind==='markdown' && state.sourceView;
    if(kind==='html')return `<div id="hub-preview-warning" class="hub-preview-warning" role="alert" hidden><span>Preview navigation was blocked. Reload to return to the artifact.</span>${button('replay','Reload preview','refresh')}</div><div class="hub-preview-scroll"><iframe id="hub-html-preview" class="hub-html-frame" title="${escape(item.title)} preview" sandbox="allow-scripts allow-downloads" referrerpolicy="no-referrer" src="${escape(root.ArtifactSession.artifactPreview(item.id))}?revision=${encodeURIComponent(item.revision)}" style="width:${state.viewport==='fluid'?'100%':state.viewport+'px'}"></iframe></div>`;
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
    return `<div class="hub-view-toolbar"><div>${button('clean-view','View','present','title="View artifact without panels"')}${autoUpdateControl()}${button('history','','history','aria-label="Version history" title="Version history"')}${['html','markdown','image'].includes(kind)?button('select',state.selecting?'Selecting':'Select','cursor',`aria-pressed="${state.selecting}" title="Select targets · hold ⌘, Ctrl or Shift to add more"`):''}${kind==='markdown'?button('source',state.sourceView?'Preview':'Source',state.sourceView?'eye':'code',`aria-pressed="${state.sourceView}"`):''}</div><div>${kind==='html'?`<label class="sr-only" for="hub-viewport">Preview width</label><select id="hub-viewport" aria-label="Preview width"><option value="fluid" ${state.viewport==='fluid'?'selected':''}>Responsive</option><option value="1280" ${state.viewport==='1280'?'selected':''}>Desktop · 1280</option><option value="768" ${state.viewport==='768'?'selected':''}>Tablet · 768</option><option value="390" ${state.viewport==='390'?'selected':''}>Mobile · 390</option></select>${button('replay','','refresh','aria-label="Reload page preview" title="Reload page preview"')}`:kind==='image'?`${button('zoom-out','','minus','aria-label="Zoom out" title="Zoom out"')}<span id="hub-zoom-label" class="hub-muted">Fit</span>${button('zoom-in','','plus','aria-label="Zoom in" title="Zoom in"')}${button('fit','','fit','aria-label="Fit image" title="Fit image"')}`:''}</div></div>`;
  }
  function pendingCount(item) {
    if(collaborationChat)return item.collaboration?.draftCount || 0;
    return (item.feedback || []).filter(message=>message.released===false && (message.author || message.role || 'user')==='user').length;
  }
  function messageMarkup(message,item) {
    const author=message.author || message.role || 'user', user=author==='user', target=message.target, targets=target?.kind==='group'?target.elements:[target].filter(Boolean);
    const date=message.created_at?new Date(message.created_at):null;
    const files=(message.files || []).map(file=>{
      const reference=!message.scoped&&root.ChatReferences?.sent(file,'hub-attachment');if(reference)return reference;
      const image=chatImages.sent(file);if(image)return image;
      const url=root.ChatImages.downloadURL(file.download_url);
      return url?`<a class="hub-attachment" href="${escape(url)}" download>${icon('attach')}<span>${escape(file.name || file.path)}</span></a>`:`<span class="hub-attachment">${icon('attach')}<span>${escape(file.name || file.path)}</span></span>`;
    }).join('');
    return `<article class="hub-message ${user&&!message.collaborator?'is-user':''}"${message.scoped?' data-scoped':''}><div class="hub-message-head">${icon(user?'user':'robot')}<span>${escape(message.name||(user?'You':'Agent'))}</span>${message.scoped&&!nativeChat?'<small class="shared-message-label">Shared</small>':''}${date && !Number.isNaN(date.getTime())?`<time datetime="${date.toISOString()}" title="${escape(date.toLocaleString())}">${escape(date.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}))}</time>`:''}</div>${targets.length?`<div class="hub-message-target">${escape(targets.map(targetLabel).join(' · '))}${message.content_hash && message.content_hash!==item.content_hash?' · Earlier version':''}</div>`:''}<div class="hub-message-body">${root.SlideChatMarkdown.render(message.message || message.text || '')}${root.ElementProperties.summary(message.edit_intent).map(value=>`<span class="element-edit-summary">${escape(value)}</span>`).join('')}</div>${files?`<div class="hub-attachments">${files}</div>`:''}${message.released===false?'<div class="hub-message-state">Private batch · not released</div>':''}</article>`;
  }
  function threadMarkup(item) {
    return root.StudioChatTimeline.render(item.feedback||[],message=>message.created_at,
      message=>messageMarkup(message,item),day=>`<p class="chat-day">${escape(day)}</p>`).join('');
  }
  function deliveryMarkup(item) {
    const mode=item.mode || 'batch',count=pendingCount(item),live=item.delivery?.live_available===true;
    const harness=item.workflow?.review_harness==='claude-code'?'Claude Code':'Codex';
    const availability=live?`${harness} connected${item.workflow?.review_session?' · '+item.workflow.review_session:''}`:'Automatic continuation unavailable';
    const writable=canComment(item),disabled=writable?'':'disabled';
    return `<div class="hub-delivery"><div class="hub-mode-switch" role="group" aria-label="Feedback delivery mode"><button type="button" data-mode="live" aria-pressed="${mode==='live'}" title="${escape(availability)}" ${disabled}>${icon('live')}Live<span class="hub-live-dot ${live?'is-connected':''}" aria-label="${live?'Agent connected':'Automatic continuation unavailable'}"></span></button><button type="button" data-mode="batch" aria-pressed="${mode==='batch'}" title="Keep feedback private until Send batch" ${disabled}>${icon('batch')}Batch</button></div>${mode==='batch'||(collaborationChat&&count)?button('release',`Send batch${count?' · '+count:''}`,'send',`class="hub-release" ${count&&writable?'':'disabled'}`):''}</div>`;
  }
  function chatMarkup(item) {
    const value=draft(item),mode=item.mode || 'batch';
    return `<header><div class="hub-chat-heading">${icon('reply')}Conversation${button('end-review','','stop',`id="hub-end-review" class="hub-end-review" aria-label="End review" title="End review" ${item.delivery?.live_available?'':'hidden'}`)}</div><p class="hub-chat-title" title="${escape(item.title)}">${escape(item.title)}${sharedEditor?' · '+escape({view:'Can view',comment:'Can comment',edit:'Can edit'}[item.access_role]||''):' · version '+escape(item.revision||1)}</p></header>${collaborationChat?`<label class="shared-channel"><span class="sr-only">Conversation</span><select id="hub-shared-channel"><option value="shared">Shared</option><option value="private" ${channelOf(item.id)==='private'?'selected':''}>My agent</option></select></label>`:''}${deliveryMarkup(item)}<div class="hub-thread" id="hub-thread">${threadMarkup(item)}</div><details class="hub-chat-context"><summary>Context & sources</summary><p>${escape(typeof item.context==='string'?item.context:typeof selectedProject(item.project_id)?.context==='string'?selectedProject(item.project_id).context:'')}</p>${button('context','Inspect agent context','code')}</details><form id="hub-feedback-form" class="hub-compose"><div id="hub-targets">${targetsMarkup(value,item)}</div><div id="hub-feedback-attachments" class="hub-attachments">${attachmentMarkup(item.id)}</div>${value.content_hash!==item.content_hash && (value.message || value.targets.length || attachments.get(item.id)?.length)?'<p class="hub-draft-warning">This draft refers to an earlier version. Its original targets are retained.</p>':''}<div id="hub-voice-segments" class="voice-segments" aria-label="Dictated feedback drafts"></div><textarea id="hub-feedback-text" aria-label="Feedback for ${escape(item.title)}" placeholder="${collaborationChat?(canComment(item)?channelOf(item.id)==='private'?'Message your agent…':'Message collaborators…':'View access'):'Message your agent… Type @ to reference'}" maxlength="10000">${escape(value.message)}</textarea><p id="hub-feedback-error" class="hub-compose-error" role="alert"></p><input type="file" id="hub-feedback-files" multiple hidden><div id="hub-feedback-recording" class="voice-recording-bar" aria-label="Voice recording" hidden><button id="hub-cancel-voice" type="button" aria-label="Cancel recording" title="Cancel recording">${icon('close')}</button><div class="voice-listening-dots" role="status" aria-label="Listening">${Array.from({length:28},()=>'<span aria-hidden="true"></span>').join('')}</div><button id="hub-stop-voice" type="button" aria-label="Stop recording" title="Stop recording"><span aria-hidden="true"></span></button><button id="hub-send-voice" type="button" aria-label="${mode==='batch'?'Add dictated feedback to batch':'Send dictated feedback'}" title="${mode==='batch'?'Add dictated feedback to batch':'Send dictated feedback'}">${icon('voiceSend')}</button></div><div class="hub-compose-actions">${button('attach','','attach','aria-label="Attach files" title="Attach files"')}${button('reference-slides','','favoriteSlides','aria-label="Reference favorite slides" title="Reference favorite slides"')}<button type="submit" id="hub-send" class="hub-primary" aria-label="${mode==='batch'?'Add feedback to batch':'Send feedback'}" title="${mode==='batch'?'Add feedback to batch':'Send'} · Enter; new line · Shift+Enter">${icon('voiceSend')}</button></div></form>`;
  }
  function attachmentMarkup(id) {
    chatImages.retain([...attachments.values()].flat());
    return (attachments.get(id) || []).map((file,index)=>{
      const remove=`<button type="button" data-remove-file="${index}" aria-label="Remove ${escape(file.name)}" title="Remove ${escape(file.name)}">${icon('close')}</button>`;
      const reference=root.ChatReferences?.chip(file,remove,'hub-attachment');if(reference)return reference;
      return (file.slideReference?`<span class="hub-attachment">${icon('star')}<span>${escape(file.slideReference.title)}<small class="favorite-reference-label">Slide reference · ${escape(file.slideReference.deckTitle)}</small>${file.slideReference.description?`<small class="favorite-reference-description">${escape(file.slideReference.description)}</small>`:''}</span>${remove}</span>`:'') || chatImages.draft(file,remove) || `<span class="hub-attachment"><span title="${escape(file.name)}">${escape(file.name)}</span>${remove}</span>`;
    }).join('');
  }
  function targetsMarkup(value,item) {
    if(!value.targets.length)return `<div class="hub-target-row"><span class="hub-target hub-artifact-context" aria-label="Context: ${escape(kinds[kindOf(item)].singular)}, ${escape(item.title)}" title="${escape(kinds[kindOf(item)].singular)} · Included with every message">${icon(kinds[kindOf(item)].icon)}<span>${escape(item.title)}</span></span></div>`;
    return `${value.targets.length>1?`<div class="hub-target-row"><span class="hub-muted">${value.targets.length} selected</span>${button('clear-targets','','close','aria-label="Clear all targets" title="Clear all targets"')}</div>`:''}${value.targets.map((target,index)=>`<div class="hub-target-row"><span class="hub-target">${escape(targetLabel(target))}</span><button type="button" data-remove-target="${index}" aria-label="Remove ${escape(targetLabel(target))}" title="Remove target">${icon('close')}</button></div>`).join('')}`;
  }
  function workspace() {
    if(sharedEditor)doc.documentElement.dataset.sharedRole=state.detail?.access_role||'view';
    elementMenuEpoch++;elementMenu.close();
    void voice.stop();
    properties?.destroy();
    const item=state.detail;
    content.innerHTML=`<div class="hub-layout">${sidebar(true)}<main class="hub-workspace"><section class="hub-edit-area" aria-label="Artifact preview"><nav class="hub-crumbs" aria-label="Breadcrumb">${button('home','','grid','aria-label="Back to library" title="Back to library"')}${icon('right')}<button type="button" data-project="${escape(item.project_id)}">${escape(item.project_title || projectTitle(item.project_id))}</button>${icon('right')}<span>${escape(kinds[kindOf(item)].singular)}</span></nav><header class="hub-edit-heading"><div><h1>${escape(item.title)}</h1><div class="hub-version"><span>Version ${escape(item.revision || 1)}</span>${status(item)}</div></div>${button('approve',item.status==='approved'?'Approved':'Approve','check',`class="hub-approve ${item.status==='approved'?'is-approved':''}" ${item.status==='approved'?'disabled':''} title="Approve the displayed version"`)}</header><div id="hub-new-revision" ${state.newRevision?'':'hidden'}>${button('reload','Open updated version','updated','class="hub-approval-note"')}</div>${viewerToolbar(item)}<div id="hub-element-properties" hidden></div><div id="hub-viewer">${viewer(item)}</div>${item.external_dependencies?.length?'<p class="hub-approval-note">External dependencies are mutable. Approval records the local files and does not freeze remote content.</p>':''}<details class="hub-files"><summary>${icon('folder')}Sources & files<span class="hub-count">${fileRecords(item).length}</span></summary>${fileList(item)}<p class="hub-source-hash" title="Content hash">${escape(item.content_hash)}</p><div class="hub-source-links">${button('context','Agent context','code')}${button('move','Move to project','folder')}</div>${item.source_path?`<p class="hub-muted" style="overflow-wrap:anywhere;margin-top:12px">${escape(item.source_path)}</p>`:''}</details></section><aside id="hub-chat" class="hub-chat" aria-label="Artifact conversation">${chatMarkup(item)}</aside></main></div>`;
    doc.title=`${item.title} · Artifact Studio`;
    properties=root.ElementProperties.create({container:doc.getElementById('hub-element-properties'),getVoice:()=>voice,getAnchor:()=>root.ElementProperties.frameAnchor(inspectorRect,doc.getElementById('hub-html-preview')),onPreview:values=>artifactEditor.preview(values),onSend:async()=>{const panel=properties,id=state.active,loading=state.loading,generation=panel.selectionGeneration(),mode=state.detail.mode;if(!await panel.stopVoice()){toast('Recording ended without sending. Review your edit before sending.');return;}if(properties!==panel || state.active!==id || state.loading!==loading || panel.selectionGeneration()!==generation)return;if(state.detail.mode!==mode){toast('Feedback mode changed. Review the current setting before sending.');return false;}return artifactEditor.send(mode);},onCancel:()=>{properties?.cancelVoice();artifactEditor.cancel();}});
    inspectorSelector=null;bindViewer();syncSelection();bindVoiceFeedback();lockComposer();workspacePanels.refresh();mountSharing();root.StudioAbout?.mountServerVersion();quickAccess.decorateProjects();
  }
  /** Update review status for the displayed snapshot without replacing the preview. */
  function syncArtifactStatus(item, detail, {opened=false}={}) {
    if(!item || item.id!==detail.id || item.content_hash!==detail.content_hash)return;
    const approved=opened?item.approved:detail.approved;
    const nextStatus=approved?'approved':detail.has_unseen_changes?'updated':'draft';
    const changed=item.status!==nextStatus || item.approved!==approved;
    Object.assign(item,{status:nextStatus,approved,has_unseen_changes:detail.has_unseen_changes});
    if(item!==state.detail || !changed)return;
    const badge=doc.querySelector('.hub-version .hub-status');
    if(badge)badge.outerHTML=status(item);
    const approve=doc.querySelector('.hub-approve');
    if(approve){
      approve.disabled=!!item.approved;
      approve.classList.toggle('is-approved',!!item.approved);
      approve.innerHTML=icon('check')+`<span>${item.approved?'Approved':'Approve'}</span>`;
    }
  }
  /** Clear the update marker for the exact version that was opened. */
  async function acknowledgeArtifact(item) {
    if(!item.has_unseen_changes)return;
    try{
      const saved=await api(artifactPath(item.id,'/seen'),{content_hash:item.content_hash});
      syncArtifactStatus(state.detail,saved,{opened:true});
      syncArtifactStatus(state.artifacts.find(card=>card.id===item.id),saved,{opened:true});
    }catch{}
  }
  /** Refresh review controls without replacing the preview, composer or dictated drafts. */
  function syncArtifactReview(detail) {
    const item=state.detail;if(!item || item.id!==detail.id)return;
    const deliveryChanged=canComment(item)!==canComment(detail)||pendingCount(item)!==pendingCount(detail)||JSON.stringify([item.mode,item.workflow,item.delivery])!==JSON.stringify([detail.mode,detail.workflow,detail.delivery]);
    const feedbackChanged=JSON.stringify(item.feedback)!==JSON.stringify(detail.feedback);
    for(const field of ['mode','workflow','delivery','collaboration','access_role'])item[field]=detail[field];
    if(sharedEditor)doc.documentElement.dataset.sharedRole=item.access_role||'view';
    if(feedbackChanged){
      const thread=doc.getElementById('hub-thread'),atBottom=thread && thread.scrollHeight-thread.scrollTop-thread.clientHeight<50;
      item.feedback=detail.feedback;
      if(thread){thread.innerHTML=threadMarkup(item);if(atBottom)thread.scrollTop=thread.scrollHeight;}
    }
    if(deliveryChanged || feedbackChanged){
      const end=doc.getElementById('hub-end-review');if(end)end.hidden=item.delivery?.live_available!==true;
      const node=doc.querySelector('.hub-delivery'),focused=doc.activeElement,mode=focused?.dataset.mode,release=focused?.dataset.action==='release';
      if(node){
        const ownedFocus=node.contains(focused);node.outerHTML=deliveryMarkup(item);
        if(ownedFocus){
          let next=doc.querySelector(mode?`[data-mode="${mode}"]`:release?'[data-action=release]':`[data-mode="${item.mode}"]`);
          if(!next || next.disabled)next=doc.querySelector(`[data-mode="${item.mode}"]`);
          next?.focus({preventScroll:true});
        }
      }
      const dictated=doc.getElementById('hub-send-voice'),label=item.mode==='batch'?'Add dictated feedback to batch':'Send dictated feedback';if(dictated){dictated.setAttribute('aria-label',label);dictated.title=label;}
    }
    syncArtifactStatus(item,detail);
    lockComposer();
  }
  async function refreshArtifactReview(id) {
    try{const detail=await artifactDetail(id);if(state.active===id)syncArtifactReview(detail);}catch{}
  }
  function renderChat() {
    const node=doc.getElementById('hub-chat');if(!node || !state.detail)return;
    void voice.stop();node.innerHTML=chatMarkup(state.detail);bindVoiceFeedback();const thread=doc.getElementById('hub-thread');thread.scrollTop=thread.scrollHeight;lockComposer();
  }
  function lockComposer() {
    for(const control of doc.querySelectorAll('#hub-feedback-form button,#hub-feedback-form textarea,#hub-feedback-form input'))if(!control.closest('.voice-input-controls'))control.disabled=sending.has(state.active)||!canComment(state.detail);
    const submit=doc.getElementById('hub-send'),active=voice.isActive();
    if(submit){submit.setAttribute('aria-label',state.detail?.mode==='batch'?'Add feedback to batch':'Send feedback');submit.disabled=sending.has(state.active)||active||!canComment(state.detail);submit.title=active?'Stop dictation before sending feedback':`${state.detail?.mode==='batch'?'Add feedback to batch':'Send'} · Enter; new line · Shift+Enter`;}
    syncVoiceChrome();
  }
  /** Display pinned drafts without replacing text fields that may be receiving dictation. */
  function renderVoiceSegments(id=state.active) {
    if(id!==state.active || !state.detail)return;
    const panel=doc.getElementById('hub-voice-segments');if(!panel)return;
    const segments=draft().voice_segments || [],ids=new Set(segments.map(segment=>segment.id || segment.request_id));
    for(const node of [...panel.children])if(!ids.has(node.dataset.voiceSegment))node.remove();
    panel.hidden=!segments.length;
    for(const [index,segment] of segments.entries()){
      let node=[...panel.children].find(node=>node.dataset.voiceSegment===(segment.id || segment.request_id));
      if(!node){
        node=doc.createElement('section');node.className='voice-segment';node.dataset.voiceSegment=segment.id || segment.request_id;
        const heading=doc.createElement('div');heading.className='voice-segment-heading';
        const label=doc.createElement('label');label.htmlFor=`hub-voice-${segment.id || segment.request_id}`;
        const remove=doc.createElement('button');remove.type='button';remove.dataset.removeVoiceSegment=segment.id || segment.request_id;remove.setAttribute('aria-label','Remove dictated feedback draft');remove.title='Remove dictated feedback draft';remove.innerHTML=icon('close');heading.append(label,remove);
        const text=doc.createElement('textarea');text.id=label.htmlFor;text.dataset.hubVoiceSegment=segment.id || segment.request_id;text.maxLength=10000;text.rows=1;
        const context=doc.createElement('small');context.className='voice-segment-context';
        node.append(heading,text,context);panel.append(node);voice.register(text,{controlsHost:heading,visible:()=>true});
      }
      const target=segment.target?.kind==='group'?`${segment.target.elements.length} selected objects`:segment.target?targetLabel(segment.target):state.detail.title;
      node.querySelector('label').textContent=`Voice draft ${index+1} · ${target}`;
      node.querySelector('small').textContent=`Version ${segment.revision} · retained selection${segment.content_hash!==state.detail.content_hash?' · earlier version':''}`;
      const text=node.querySelector('textarea');if(text.value!==segment.message){const start=text.selectionStart,end=text.selectionEnd;text.value=segment.message;if(doc.activeElement===text)text.setSelectionRange(start,end);}
    }
    voice.refresh();lockComposer();
  }
  /** Bind final text to the artifact/version/selection present when recognition began. */
  function bindVoiceFeedback() {
    const input=doc.getElementById('hub-feedback-text');if(!input || !state.detail)return;
    const handle=voice.register(input,{
      label:'artifact feedback',
      capture:()=>{const item=state.detail,value=draft(item),id=root.crypto.randomUUID(),original=value.targets.length?value:item;return {artifact_id:item.id,id,request_id:id,revision:original.revision,content_hash:original.content_hash,target:feedbackTarget(groupTargets(value.targets))};},
      available:context=>!!context?.artifact_id && drafts.has(context.artifact_id) && !discardedVoiceSegments.has(context.id || context.request_id),
      insert:(text,context)=>{
        const value=drafts.get(context.artifact_id);if(!value || discardedVoiceSegments.has(context.id || context.request_id) || !appendVoiceSegment(value,context,text))return false;
        persist();renderVoiceSegments(context.artifact_id);return true;
      },
    });
    feedbackVoiceHandle=handle;
    if(handle)doc.querySelector('#hub-feedback-form .hub-compose-actions').insertBefore(handle.controls,doc.getElementById('hub-send'));
    renderVoiceSegments();voice.refresh();
  }
  function voiceBoundary() {const input=doc.getElementById('hub-feedback-text');if(input)voice.boundary(input);}
  function syncVoiceChrome(phase='') {
    const input=doc.getElementById('hub-feedback-text'),form=doc.getElementById('hub-feedback-form'),bar=doc.getElementById('hub-feedback-recording');
    if(!input || !form || !bar)return;
    const active=voice.isActive(input);bar.hidden=!active;form.classList.toggle('is-recording',active);
    if(phase)bar.dataset.phase=phase;
    const currentPhase=bar.dataset.phase;bar.classList.toggle('is-finishing',currentPhase!=='listening');
    const statusLabel=currentPhase==='waiting'?'Waiting for microphone':currentPhase==='starting'?'Starting microphone':currentPhase==='finishing'?'Finishing dictation':'Listening';
    bar.querySelector('[role=status]').setAttribute('aria-label',statusLabel);
    for(const control of bar.querySelectorAll('button'))control.disabled=sending.has(state.active) || control.id==='hub-send-voice' && currentPhase!=='listening';
  }
  doc.addEventListener('voice-input-state',event=>{
    if(event.detail?.input===doc.getElementById('hub-feedback-text')){
      if(event.detail.active && (!voiceRecordingBase || voiceRecordingBase.artifact_id!==state.active))voiceRecordingBase={artifact_id:state.active,ids:new Set((draft().voice_segments || []).map(segment=>segment.id || segment.request_id))};
      syncVoiceChrome(event.detail.phase);
    }else if(event.detail?.active && !voice.isActive(doc.getElementById('hub-feedback-text')))voiceRecordingBase=null;
    lockComposer();
  });
  function syncSelection() {
    if(!state.detail)return;
    const selectionButton=doc.querySelector('[data-action="select"]');
    if(selectionButton){selectionButton.setAttribute('aria-pressed',String(state.selecting));selectionButton.innerHTML=icon('cursor')+`<span>${state.selecting?'Selecting':'Select'}</span>`;}
    const value=draft(),targetNode=doc.getElementById('hub-targets');if(targetNode)targetNode.innerHTML=targetsMarkup(value,state.detail);
    const frame=doc.getElementById('hub-html-preview');
    if(frame)frame.tabIndex=state.selecting?-1:0;
    frame?.contentWindow?.postMessage({type:'artifact:select',enabled:state.selecting},'*');
    const showTargets=!doc.body.classList.contains('workspace-clean-view') && value.content_hash===state.detail.content_hash;
    frame?.contentWindow?.postMessage({type:'artifact:selection',selectors:showTargets?value.targets.map(target=>target.selector).filter(Boolean):[]},'*');
    for(const block of doc.querySelectorAll('#hub-markdown [data-target-index]'))block.classList.toggle('hub-selected-block',showTargets && value.targets.some(target=>target.block_index===Number(block.dataset.targetIndex)));
    doc.getElementById('hub-markdown')?.classList.toggle('is-selecting',state.selecting);
    doc.getElementById('hub-image-wrap')?.classList.toggle('is-selecting',state.selecting);
    renderImageRegions();syncInspector();persist();
  }
  function setTarget(target,additive) {
    elementMenuEpoch++;elementMenu.close();
    if(sending.has(state.active) || !artifactEditor.finish())return;
    const value=draft();
    if(value.content_hash!==state.detail.content_hash && (value.message || value.targets.length)){toast('Send or clear the earlier-version draft before selecting this version.',true);return;}
    value.content_hash=state.detail.content_hash;value.revision=state.detail.revision;value.targets=selectTargets(value.targets,target,additive);value.request_id=null;syncSelection();voiceBoundary();if(singleElement())doc.getElementById('hub-viewer')?.focus({preventScroll:true});
  }
  function setTargets(targets,additive){
    elementMenuEpoch++;elementMenu.close();if(sending.has(state.active)||!artifactEditor.finish())return;
    const value=draft();if(value.content_hash!==state.detail.content_hash&&(value.message||value.targets.length)){toast('Send or clear the earlier-version draft before selecting this version.',true);return;}
    const unique=new Map((additive?value.targets:[]).map(target=>[target.selector,target]));for(const target of targets)unique.set(target.selector,target);
    value.content_hash=state.detail.content_hash;value.revision=state.detail.revision;value.targets=[...unique.values()].slice(0,100);value.request_id=null;syncSelection();voiceBoundary();
  }
  function bindViewer() {
    if(pendingImageExport){root.clearTimeout(pendingImageExport.timeout);pendingImageExport=null;}
    imageSelectors.clear();
    const canvas=doc.getElementById('hub-viewer');if(canvas){canvas.tabIndex=0;canvas.setAttribute('role','group');canvas.setAttribute('aria-label','Artifact canvas');}
    const frame=doc.getElementById('hub-html-preview');frame?.addEventListener('load',syncSelection);frame?.addEventListener('focus',()=>{if(state.selecting)canvas?.focus({preventScroll:true});});
    const markdown=doc.getElementById('hub-markdown');
    if(markdown)for(const [index,block] of [...markdown.querySelectorAll('h1,h2,h3,h4,h5,h6,p,li,pre,blockquote')].entries())block.dataset.targetIndex=String(index);
    const image=doc.getElementById('hub-image');
    if(image){image.addEventListener('load',()=>{fitImage();const meta=doc.getElementById('hub-image-meta');if(meta)meta.textContent=`${state.detail.entrypoint} · ${image.naturalWidth} × ${image.naturalHeight}`;});if(image.complete && image.naturalWidth)fitImage();}
    for(const picture of doc.querySelectorAll('#hub-image,#hub-markdown img'))picture.addEventListener('contextmenu',event=>{event.preventDefault();event.stopPropagation();imageMenu(picture,{x:event.clientX,y:event.clientY});});
  }
  root.addEventListener('securitypolicyviolation',event=>{
    if(event.target!==doc || event.effectiveDirective!=='frame-src')return;
    const warning=doc.getElementById('hub-preview-warning');
    if(warning)warning.hidden=false;
  });
  function fitImage() {
    const image=doc.getElementById('hub-image'),canvas=doc.getElementById('hub-image-canvas');if(!image?.naturalWidth)return;
    const padding=root.getComputedStyle(canvas);state.zoom=Math.min(1,(canvas.clientWidth-parseFloat(padding.paddingLeft)-parseFloat(padding.paddingRight))/image.naturalWidth);applyZoom();
  }
  function applyZoom() {const image=doc.getElementById('hub-image');if(!image)return;image.style.width=`${image.naturalWidth*state.zoom}px`;const output=doc.getElementById('hub-zoom-label');if(output)output.textContent=`${Math.round(state.zoom*100)}%`;renderImageRegions();}
  function renderImageRegions() {
    const wrap=doc.getElementById('hub-image-wrap');if(!wrap || !state.detail)return;
    for(const node of wrap.querySelectorAll('.hub-saved-region'))node.remove();
    if(draft().content_hash!==state.detail.content_hash)return;
    for(const target of draft().targets.filter(target=>target.kind==='region' && !doc.body.classList.contains('workspace-clean-view'))){
      const region=target.region || target,image=doc.getElementById('hub-image'),width=region.image_width || image.naturalWidth,height=region.image_height || image.naturalHeight,node=doc.createElement('div');node.className='hub-region hub-saved-region';node.style.cssText=`left:${region.x*width*state.zoom}px;top:${region.y*height*state.zoom}px;width:${region.width*width*state.zoom}px;height:${region.height*height*state.zoom}px`;wrap.append(node);
    }
  }
  async function loadRegistry() {
    const [data]=await Promise.all([api('/api/hub'),root.FavoriteSlides.refresh()]);state.projects=data.projects || [];state.projectTree=data.project_tree || {nodes:state.projects.map(project=>({id:project.id,kind:'project',parent_id:null})),content_hash:''};state.artifacts=(data.artifacts || []).map(item=>({...item,starred:root.FavoriteSlides.has('artifact',item.id)}));state.designs=data.designs || [];
    if(!['all','designs'].includes(state.project) && !state.projects.some(project=>project.id===state.project) && !state.projectTree.nodes.some(node=>'folder:'+node.id===state.project))state.project='all';
  }
  function updateHistory(id) {const url=new URL(root.location.href);id?url.searchParams.set('artifact',id):url.searchParams.delete('artifact');url.searchParams.delete('deck');url.searchParams.delete('design');state.project==='designs'?url.searchParams.set('view','designs'):url.searchParams.delete('view');!id&&!['all','designs'].includes(state.project)?url.searchParams.set('project',state.project):url.searchParams.delete('project');root.history.pushState({},'',url);}
  async function openArtifact(id,push=true) {
    if(!resetEditor())return;coverLoader.clear();closeTreeMenu();workspacePanels.exitView();
    const collaboration=doc.querySelector('[data-collaborate]');
    if(collaboration)collaboration.dataset.artifact=id;
    void voice.stop();
    state.designActive=null;
    const item=state.artifacts.find(item=>item.id===id);
    if(item?.legacy_deck_id){if(!sharedEditor)await quickAccess.visit({project_id:item.project_id});root.location.href=`${studioHome}?deck=${encodeURIComponent(item.legacy_deck_id)}${nativeChat&&!sharedEditor?'&chat=shared':''}${push?'':root.location.hash}`;return;}
    const token=++state.loading;state.active=id;state.selecting=false;state.sourceView=false;state.newRevision=null;
    content.innerHTML='<main class="hub-loading" role="status">Opening artifact…</main>';
    try {
      const detail=await artifactDetail(id);
      if(token!==state.loading)return;
      if(detail.legacy_deck_id){root.location.href=`${studioHome}?deck=${encodeURIComponent(detail.legacy_deck_id)}${push?'':root.location.hash}`;return;}
      let source='';
      if(kindOf(detail)==='markdown') {const response=await fetch(filePath(detail));if(!response.ok)throw new Error('The Markdown source could not be loaded.');source=await response.text();}
      if(token!==state.loading)return;
      state.detail=detail;state.source=source;state.project=detail.project_id;
      if(!sharedEditor)void quickAccess.visit({project_id:detail.project_id});
      const value=draft(detail);if(!value.message && !value.targets.length){value.content_hash=detail.content_hash;value.revision=detail.revision;}
      workspace();if(push)updateHistory(id);
      void acknowledgeArtifact(detail);
    }catch(error){if(token===state.loading){state.active=null;state.detail=null;library();toast(error.message,true);}}
  }
  function home(project='all',push=true) {if(!resetEditor())return;closeTreeMenu();workspacePanels.exitView();void voice.stop();if((project==='designs')!==(state.project==='designs')){state.search='';state.kind='all';}++state.loading;state.designActive=null;state.active=null;state.detail=null;state.project=project;state.newRevision=null;library();if(!sharedEditor&&selectedProject(project))void quickAccess.visit({project_id:project});if(push)updateHistory(null);}
  function modal(title,body,wide=false) {modalEpoch++;closeTreeMenu();void voice.stop();dialog.className=`hub-dialog ${wide?'hub-context-dialog':''}`;dialog.innerHTML=`<header class="hub-dialog-head"><h2 id="hub-dialog-title">${escape(title)}</h2>${button('close-dialog','','close','aria-label="Close dialog" title="Close"')}</header>${body}`;if(!dialog.open)dialog.showModal();voice.refresh();}
  /** Retain the explicit parent export handoff used by authored dashboards. */
  function exportDialog(file) {
    exportUrl=URL.createObjectURL(new Blob([file.content],{type:file.type}));
    modal('Export file',`<p>${escape(state.detail.title)}</p><p class="hub-muted">${escape(file.name)} · ${sizeLabel(new Blob([file.content]).size)}</p><div class="hub-form-actions"><a class="hub-download hub-primary" href="${escape(exportUrl)}" download="${escape(file.name)}">${icon('down')}Download ${escape(file.name)}</a></div>`);
  }
  dialog.addEventListener('close',()=>{if(exportUrl){URL.revokeObjectURL(exportUrl);exportUrl=null;}previewImport=null;});
  /** Let the human choose JSON for the current isolated preview without uploading it. */
  function previewImportDialog(frame) {
    previewImport={window:frame.contentWindow,artifact:state.active,revision:state.detail.revision};
    modal('Import data',`<label class="hub-field"><span>JSON file for ${escape(state.detail.title)}</span><input id="hub-preview-import" type="file" accept=".json,application/json"></label><p id="hub-preview-import-error" role="alert"></p>`);
  }
  dialog.addEventListener('change',async event=>{
    if(event.target.id!=='hub-preview-import')return;
    const file=event.target.files?.[0],pending=previewImport;if(!file || !pending)return;
    try{
      if(file.size>20*1024*1024 || !/\.json$/i.test(file.name))throw new Error('Choose a JSON file smaller than 20 MB.');
      const content=await file.text(),frame=doc.getElementById('hub-html-preview');
      if(!dialog.open || previewImport!==pending)return;
      if(!frame || frame.contentWindow!==pending.window || state.active!==pending.artifact || state.detail.revision!==pending.revision)throw new Error('The preview changed. Open import again in the current version.');
      pending.window.postMessage({type:'artifact:import-result',content},'*');dialog.close();
    }catch(error){if(previewImport===pending)doc.getElementById('hub-preview-import-error').textContent=error.message;}
  });
  const designLibrary=root.SlideDesignLibrary.create({api,icon,escape,sizeLabel,toast,modal,changed:async id=>{
    await loadRegistry();if(id)return openDesign(id,false);home('designs');
  }});
  async function openDesign(id,push=true){
    if(sharedEditor&&!canManageWorkspace())return home('all',push);
    if(!resetEditor())return;coverLoader.clear();workspacePanels.exitView();
    void voice.stop();const token=++state.loading;state.active=null;state.detail=null;state.project='designs';state.designActive=id;
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
  function projectDialog(thenImport=false,parent=selectedFolder()) {
    closeTreeMenu();modal('New project',`<form id="hub-project-form" class="hub-form" data-parent="${escape(parent || '')}" data-then-import="${thenImport}"><label class="hub-field"><span>Project name</span><input name="title" required maxlength="200" autofocus placeholder="Name your project"></label><label class="hub-field"><span>Shared brief <span class="hub-muted">· optional</span></span><textarea name="context" placeholder="Purpose, audience and source context"></textarea></label><p id="hub-dialog-error" class="hub-compose-error" role="alert"></p><div class="hub-form-actions">${button('close-dialog','Cancel','close')}<button type="submit" class="hub-primary">Create project</button></div></form>`);
    dialog.querySelector('input').focus();
  }
  function importDialog(projectId=state.project) {
    if(!canManageWorkspace())return;
    const folder=projectId.startsWith('folder:')?projectId.slice(7):null,ids=folder?root.ProjectNavigation.projectsIn(state.projectTree,folder):null;
    const projects=state.projects.filter(project=>!project.legacy && (!ids || ids.includes(project.id)));
    if(!projects.length){projectDialog(true,folder);return;}
    importFiles=[];
    modal('Import artifact',`<form id="hub-import-form" class="hub-form"><label class="hub-field"><span>Project</span><select name="project_id">${projects.map(project=>`<option value="${escape(project.id)}" ${project.id===projectId?'selected':''}>${escape(project.title)}</option>`).join('')}</select></label><label class="hub-field"><span>Title</span><input name="title" required maxlength="200" placeholder="Artifact title"></label><div class="hub-field"><span>Files</span><div class="hub-import-choices">${button('choose-files','Choose files','upload')}${button('choose-folder','Choose folder','folder')}</div><input id="hub-import-files" type="file" multiple hidden><input id="hub-import-folder" type="file" webkitdirectory multiple hidden><ul id="hub-import-list" class="hub-import-list"></ul></div><label class="hub-field" id="hub-entrypoint-field" hidden><span>Open first</span><select name="entrypoint" id="hub-entrypoint"></select></label><p id="hub-dialog-error" class="hub-compose-error" role="alert"></p><div class="hub-form-actions">${button('close-dialog','Cancel','close')}<button type="submit" class="hub-primary">Import artifact</button></div></form>`);
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
  async function contextDialog(target=null) {
    const artifactId=target?target.artifactId:state.detail?.id,projectId=target?target.projectId:!['all','designs'].includes(state.project)&&!state.project.startsWith('folder:')?state.project:null,title=target?.projectId?'Project brief':'Agent context';
    modal(title,'<p class="hub-muted" role="status">Loading context…</p>',true);
    const epoch=modalEpoch;
    const data=await api(`/api/hub/context${artifactId?'?artifact_id='+encodeURIComponent(artifactId):projectId?'?project_id='+encodeURIComponent(projectId):''}`);
    const sources=Array.isArray(data.sources)?data.sources:Array.isArray(data.context?.sources)?data.context.sources:[];
    if(!dialog.open || epoch!==modalEpoch)return;
    modal(title,`<div class="hub-context-grid"><section><h3>Sources & delivery</h3><ul class="hub-context-list">${sources.length?sources.map(source=>`<li class="hub-context-source"><strong><span>${escape(source.title || source.name || source.kind || 'Context source')}</span><span class="hub-context-delivery">${escape(source.delivery || source.delivery_state || source.status || 'Available')}</span></strong><code>${escape(source.path || source.canonical_path || '')}</code><span class="hub-scope">Scope: ${escape(typeof source.scope==='object'?JSON.stringify(source.scope):source.scope || 'workspace')}</span>${source.hash || source.content_hash?`<code title="Content hash">${escape(source.hash || source.content_hash)}</code>`:''}</li>`).join(''):'<li class="hub-context-source">No source receipt was returned.</li>'}</ul></section><section><h3 style="margin-bottom:12px">${data.receipt?'Latest recorded delivery':'Available context'}</h3><pre class="hub-context-receipt">${escape(JSON.stringify(data.receipt || data,null,2))}</pre></section></div><p class="hub-context-note">Delivery records what the server returned. Available sources are not proof that an agent opened or retained them.</p>`,true);
  }
  async function moveArtifact(id,projectId,accessHash='') {
    await api(artifactPath(id,'/project'),{project_id:projectId,...(accessHash?{access_hash:accessHash}:{})});
    await loadRegistry();
    if(state.active===id)await openArtifact(id,false);
    else if(state.active)renderProjectSidebar(true);
    else library();
    toast('Artifact moved.');
  }
  function showMoveConfirmation(form,error) {
    form.dataset.accessHash=error.accessHash;
    form.querySelector('[role=alert]').textContent=error.message;
    form.querySelector('[type=submit]').textContent='Confirm move';
  }
  function moveDialog(id=state.active,projectId=null,confirmation=null) {
    if(!canManageWorkspace())return;
    const item=id===state.active?state.detail:state.artifacts.find(item=>item.id===id);if(!item || id===state.active && !artifactEditor.finish())return;
    const projects=state.projects.filter(project=>item.legacy_deck_id || !project.legacy);
    if(!projects.length){projectDialog();return;}
    modal('Move to project',`<form id="hub-move-form" class="hub-form" data-artifact-id="${escape(item.id)}"><label class="hub-field"><span>Project for ${escape(item.title)}</span><select name="project_id">${projects.map(project=>`<option value="${escape(project.id)}" ${project.id===(projectId ?? item.project_id)?'selected':''}>${escape(project.title)}</option>`).join('')}</select></label><p id="hub-dialog-error" class="hub-compose-error" role="alert"></p><div class="hub-form-actions">${button('close-dialog','Cancel','close')}<button type="submit" class="hub-primary">Move artifact</button></div></form>`);
    const form=dialog.querySelector('form');
    if(confirmation)showMoveConfirmation(form,confirmation);
    form.querySelector('select').addEventListener('change',()=>{
      delete form.dataset.accessHash;
      form.querySelector('[role=alert]').textContent='';
      form.querySelector('[type=submit]').textContent='Move artifact';
    });
  }
  async function sendFeedback(form) {
    const item=state.detail;if(!canComment(item))return;const value=draft(item),files=attachments.get(item.id) || [],expectedMode=item.mode || 'batch';
    if(voice.isActive()){toast('Stop dictation before sending feedback.');return;}
    if(sending.has(item.id) || !artifactEditor.finish() || !value.message.trim() && !files.length && !value.voice_segments?.some(segment=>segment.message.trim()))return;
    sending.add(item.id);
    const controls=[...form.querySelectorAll('button,textarea,input')],error=form.querySelector('[role=alert]');for(const control of controls)control.disabled=true;error.textContent='';
    lockComposer();persist();
    try{
      // Acknowledge each completed prefix locally; failed or lost responses retain their retry IDs.
      for(const segment of [...(value.voice_segments || [])]){
        if(!segment.message.trim())continue;
        await postArtifactFeedback(item,{message:segment.message,target:segment.target,content_hash:segment.content_hash,request_id:segment.request_id,expected_mode:expectedMode});
        value.voice_segments=value.voice_segments.filter(current=>current.request_id!==segment.request_id);persist();renderVoiceSegments(item.id);
      }
      if(value.message.trim() || files.length){
        value.request_id ||= root.crypto.randomUUID();persist();
        await postArtifactFeedback(item,{message:value.message.trim(),target:feedbackTarget(groupTargets(value.targets)),files:await encodeFiles(files),content_hash:value.content_hash,request_id:value.request_id,expected_mode:expectedMode});
        value.message='';value.request_id=null;attachments.delete(item.id);persist();
      }
      drafts.delete(item.id);attachments.delete(item.id);persist();
      const detail=await artifactDetail(item.id);if(state.active===item.id){state.detail={...state.detail,feedback:detail.feedback,mode:detail.mode,delivery:detail.delivery,collaboration:detail.collaboration};renderChat();syncSelection();}
    }catch(reason){if(reason.status===409)await refreshArtifactReview(item.id);error.textContent=reason.message;renderVoiceSegments(item.id);}finally{sending.delete(item.id);for(const control of controls)if(!control.closest('.voice-input-controls'))control.disabled=false;lockComposer();if(state.active===item.id)doc.getElementById('hub-feedback-text')?.focus();}
  }
  function addAttachments(incoming) {
    if(!incoming.length||!state.detail||!canComment(state.detail)||sending.has(state.active))return false;
    incoming=incoming.filter(file=>!file.workspaceReference||!(attachments.get(state.active)||[]).some(saved=>saved.workspaceReference?.token===file.workspaceReference.token));
    const selected=[...(attachments.get(state.active) || []),...incoming],error=root.SlideChatComposer.selectionError(selected);
    if(error){doc.getElementById('hub-feedback-error').textContent=error;return false;}
    if(incoming.length){draft().request_id=null;doc.getElementById('hub-feedback-error').textContent='';}
    attachments.set(state.active,selected);doc.getElementById('hub-feedback-attachments').innerHTML=attachmentMarkup(state.active);return true;
  }
  if(!nativeChat)root.ChatReferences.register({inputId:'hub-feedback-text',key:()=>state.active,attach:addAttachments});
  const versions=root.VersionTimeline.create({api,onRestored:async target=>{await openArtifact(target.artifactId,false);toast('Version restored.');}});
  function openArtifactHistory(item){
    if(canManageWorkspace()&&item&&!item.legacy_deck_id&&artifactEditor.finish()){
      versions.open({kind:'artifact',artifactId:item.id,title:item.title});
    }
  }
  root.FavoriteSlides.onChange(()=>{
    state.artifacts=state.artifacts.map(item=>({...item,starred:root.FavoriteSlides.has('artifact',item.id)}));
    if(!state.active && !state.designActive && doc.getElementById('hub-grid')){const focus=doc.activeElement?.dataset.starId;refreshCards();if(focus)[...doc.querySelectorAll('[data-star-id]')].find(node=>node.dataset.starId===focus)?.focus();}
  });
  async function action(name,node) {
    if(name==='favorite-slides')return root.FavoriteSlides.open();
    if(name==='reference-slides'){const id=state.active;return root.FavoriteSlides.open({attach:files=>id===state.active&&addAttachments(files)});}
    if(name==='retry-cover'){coverLoader.retry(node.dataset.coverRetry);return;}
    if(name==='sort-direction'){updateSort({...sort,direction:sort.direction==='asc'?'desc':'asc'});return;}
    if(name==='history')return openArtifactHistory(state.detail);
    if(name==='clean-view'){if(!artifactEditor.finish())return;state.selecting=false;syncSelection();workspacePanels.enterView();return;}
    if(name==='auto-update'){previewUpdates.set(!previewUpdates.enabled);return;}
    if(name==='home')return home();
    if(name==='retry-library'){await loadRegistry();return library();}
    if(name==='new-project')return projectDialog();
    if(name==='project-add')return projectTreeMenu(node);
    if(name==='import')return importDialog();
    if(name==='close-dialog')return dialog.close();
    if(name==='context')return contextDialog();
    if(name==='choose-files')return doc.getElementById('hub-import-files').click();
    if(name==='choose-folder')return doc.getElementById('hub-import-folder').click();
    if(name==='move')return moveDialog();
    if(name==='attach')return doc.getElementById('hub-feedback-files').click();
    if(name==='clear-targets'){if(!resetEditor())return;draft().targets=[];draft().request_id=null;syncSelection();voiceBoundary();return;}
    if(name==='reload')return openArtifact(state.active,false);
    if(name==='select'){if(!resetEditor())return;state.selecting=!state.selecting;node.setAttribute('aria-pressed',String(state.selecting));node.innerHTML=icon('cursor')+`<span>${state.selecting?'Selecting':'Select'}</span>`;syncSelection();return;}
    if(name==='source'){if(!resetEditor())return;state.sourceView=!state.sourceView;doc.getElementById('hub-viewer').innerHTML=viewer(state.detail);node.setAttribute('aria-pressed',String(state.sourceView));node.innerHTML=icon(state.sourceView?'eye':'code')+`<span>${state.sourceView?'Preview':'Source'}</span>`;bindViewer();syncSelection();return;}
    if(name==='replay'){if(!resetEditor())return;doc.getElementById('hub-preview-warning').hidden=true;const frame=doc.getElementById('hub-html-preview');frame.src=frame.src;return;}
    if(name==='fit')return fitImage();
    if(name==='zoom-in' || name==='zoom-out'){state.zoom=Math.max(.05,Math.min(8,state.zoom*(name==='zoom-in'?1.25:.8)));applyZoom();return;}
    if(name==='approve'){
      const item=state.detail;
      if(!item || (sharedEditor && item.access_role!=='edit') || !artifactEditor.finish())return;
      node.disabled=true;
      try{
        const saved=await api(artifactPath(item.id,'/approve'),{content_hash:item.content_hash});
        syncArtifactStatus(state.detail,saved);
        syncArtifactStatus(state.artifacts.find(card=>card.id===item.id),saved);
        toast(`Approved version ${item.revision}.`);
      }catch(error){
        node.disabled=false;
        if(error.status===409)void refresh();
        throw error;
      }
      return;
    }
    if(name==='end-review'){const id=state.active;node.disabled=true;try{await api(artifactPath(id,'/review/disconnect'),{});const detail=await artifactDetail(id);if(state.active===id)syncArtifactReview(detail);toast('Review ended.');}finally{node.disabled=false;}return;}
    if(name==='release'){const id=state.active;node.disabled=true;try{if(collaborationChat)await collaborationChat.release(id,channelOf(id));else await api(artifactPath(id,'/release'),{});const detail=await artifactDetail(id);if(state.active===id){state.detail={...state.detail,feedback:detail.feedback,mode:detail.mode,delivery:detail.delivery,collaboration:detail.collaboration};renderChat();}toast('Batch released.');}finally{node.disabled=false;}}
  }
  doc.addEventListener('click',event=>{
    const node=event.target.closest('button');if(!node)return;
    const run=async()=>{
      if(node.dataset.toggleFolder)return toggleFolder(node.dataset.toggleFolder);
      if(node.dataset.treeMenu)return projectTreeMenu(node,node.dataset.treeMenu);
      if(node.id==='hub-stop-voice'){await feedbackVoiceHandle?.stop();syncVoiceChrome();return;}
      if(node.id==='hub-cancel-voice'){
        const recording=voiceRecordingBase;feedbackVoiceHandle?.cancel();voiceRecordingBase=null;
        if(recording){
          const value=drafts.get(recording.artifact_id);
          if(value){
            for(const segment of value.voice_segments || [])if(!recording.ids.has(segment.id || segment.request_id))discardedVoiceSegments.add(segment.id || segment.request_id);
            value.voice_segments=(value.voice_segments || []).filter(segment=>recording.ids.has(segment.id || segment.request_id));
            persist();renderVoiceSegments(recording.artifact_id);
          }
        }
        syncVoiceChrome();return;
      }
      if(node.id==='hub-send-voice'){
        const id=state.active,mode=state.detail?.mode,form=doc.getElementById('hub-feedback-form'),completed=await feedbackVoiceHandle?.stop();syncVoiceChrome();
        if(id!==state.active || !form.isConnected)return;
        if(state.detail?.mode!==mode){toast('Feedback mode changed. Review the current setting before sending.');return;}
        if(!completed){toast('Recording ended without sending. Review your draft before sending.');return;}
        form.requestSubmit();return;
      }
      if(node.dataset.action)return action(node.dataset.action,node);
      if(node.dataset.openDesign)return openDesign(node.dataset.openDesign);
      if(node.dataset.artifact)return openArtifact(node.dataset.artifact);
      if(node.dataset.shareArtifact)return shareArtifact(node.dataset.shareArtifact);
      if(node.dataset.shareProject)return shareProject(node.dataset.shareProject);
      if(node.dataset.moveArtifact)return moveDialog(node.dataset.moveArtifact);
      if(node.dataset.project)return home(node.dataset.project);
      if(node.dataset.kind){state.kind=node.dataset.kind;library();return;}
      if(node.dataset.removeTarget!==undefined){if(!resetEditor())return;draft().targets.splice(Number(node.dataset.removeTarget),1);draft().request_id=null;syncSelection();voiceBoundary();return;}
      if(node.dataset.removeVoiceSegment!==undefined){if(sending.has(state.active))return;discardedVoiceSegments.add(node.dataset.removeVoiceSegment);draft().voice_segments=(draft().voice_segments || []).filter(segment=>(segment.id || segment.request_id)!==node.dataset.removeVoiceSegment);persist();renderVoiceSegments();voiceBoundary();return;}
      if(node.dataset.removeFile!==undefined){
        if(!state.detail||!canComment(state.detail)||sending.has(state.active))return;
        attachments.get(state.active)?.splice(Number(node.dataset.removeFile),1);draft().request_id=null;
        doc.getElementById('hub-feedback-error').textContent='';
        doc.getElementById('hub-feedback-attachments').innerHTML=attachmentMarkup(state.active);return;
      }
      if(node.dataset.mode){const id=state.active;node.disabled=true;try{if(collaborationChat)conversationModes.set(id,node.dataset.mode);else await api(artifactPath(id,'/mode'),{mode:node.dataset.mode});const detail=await artifactDetail(id);if(state.active===id){state.detail={...state.detail,feedback:detail.feedback,mode:detail.mode,delivery:detail.delivery,collaboration:detail.collaboration};renderChat();}}finally{node.disabled=false;}}
    };run().catch(error=>toast(error.message,true));
  });
  /** Keep browser menus on editable fields, links and authored content outside selection mode. */
  function libraryContextMenu(event,point=null){
    const target=event.target;if(event.defaultPrevented || dialog.open || root.ElementCommands.typing(target) || target.closest('a[href]'))return false;
    const card=target.closest('[data-drag-artifact],[data-artifact]'),id=card?.dataset.dragArtifact || card?.dataset.artifact;
    if(id){artifactMenu(card,id,point);return true;}
    const row=target.closest('[data-tree-node]');
    if(row){projectTreeMenu(target.closest('button') || row,row.dataset.treeNode,point);return true;}
    if(state.active || state.designActive || state.project==='designs' || !target.closest('.hub-main,aside.hub-sidebar[aria-label="Projects"]') || target.closest('button,summary,[role="button"]'))return false;
    if(!canManageWorkspace())return false;
    showHubMenu(target.closest('.hub-main,aside.hub-sidebar'), 'Library actions',creationCommands(),point);return true;
  }
  content.addEventListener('contextmenu',event=>{if(libraryContextMenu(event,{x:event.clientX,y:event.clientY})){event.preventDefault();event.stopPropagation();}else closeTreeMenu();});
  content.addEventListener('keydown',event=>{if(root.ElementCommands.shortcut(event)==='menu' && libraryContextMenu(event)){event.preventDefault();event.stopPropagation();}});
  doc.addEventListener('input',event=>{
    if(event.target.id==='hub-search'){state.search=event.target.value;refreshCards();}
    if(event.target.id==='hub-feedback-text'){draft().message=event.target.value;draft().request_id=null;persist();}
    if(event.target.dataset.hubVoiceSegment){const segment=draft().voice_segments?.find(segment=>(segment.id || segment.request_id)===event.target.dataset.hubVoiceSegment);if(segment){segment.id ||= segment.request_id;segment.message=event.target.value;segment.request_id=root.crypto.randomUUID();persist();}}
  });
  doc.addEventListener('change',event=>{
    const node=event.target;
    if(node.id==='hub-sort')updateSort({by:node.value,direction:['starred','updated','created'].includes(node.value)?'desc':'asc'});
    if(node.id==='hub-project-picker')home(node.value);
    if(node.id==='hub-viewport'){state.viewport=node.value;const frame=doc.getElementById('hub-html-preview');frame.style.width=node.value==='fluid'?'100%':`${node.value}px`;}
    if(['hub-import-files','hub-import-folder'].includes(node.id))setImportFiles([...node.files]);
    if(node.id==='hub-feedback-files'){addAttachments([...node.files]);node.value='';}
  });
  doc.addEventListener('keydown',event=>{if(event.target.id==='hub-feedback-text' && event.key==='Enter' && !event.shiftKey && !event.isComposing){event.preventDefault();if(!doc.getElementById('hub-send').disabled)doc.getElementById('hub-feedback-form').requestSubmit();}});
  doc.addEventListener('submit',event=>{
    const form=event.target;if(!['hub-feedback-form','hub-project-form','hub-import-form','hub-move-form','hub-folder-form','hub-tree-move-form'].includes(form.id))return;event.preventDefault();
    if(form.id==='hub-feedback-form'){sendFeedback(form);return;}
    if(voice.isActive()){toast('Stop dictation before submitting this form.');return;}
    const submit=form.querySelector('[type=submit]'),error=form.querySelector('[role=alert]');submit.disabled=true;error.textContent='';
    (async()=>{
      const fields=new FormData(form);
      if(form.id==='hub-folder-form'){await changeTree({action:form.dataset.mode,id:form.dataset.id || null,title:String(fields.get('title')).trim(),parent_id:form.dataset.parent || null});dialog.close();}
      if(form.id==='hub-tree-move-form'){await changeTree({action:'move',id:form.dataset.id,parent_id:String(fields.get('parent_id')) || null});dialog.close();}
      if(form.id==='hub-project-form'){
        const project=await api('/api/hub/projects',{title:String(fields.get('title')).trim(),context:String(fields.get('context'))});await loadRegistry();state.project=project.id || project.project?.id || 'all';
        if(form.dataset.parent)try{await changeTree({action:'move',id:state.project,parent_id:form.dataset.parent});}catch{toast('Project created at top level. Move it into the folder again.',true);}
        dialog.close();home(state.project);if(form.dataset.thenImport==='true')importDialog();
      }
      if(form.id==='hub-import-form'){
        if(!importFiles.length)throw new Error('Choose files or a folder to import.');
        if(importFiles.length>1000 || importFiles.reduce((total,file)=>total+file.size,0)>100*1024*1024)throw new Error('Import up to 1,000 files and 100 MB at once.');
        const paths=importFiles.map(file=>file.webkitRelativePath || file.name);if(new Set(paths).size!==paths.length)throw new Error('The selection contains duplicate filenames. Choose a folder to preserve file paths.');
        submit.textContent='Importing…';
        const item=await api('/api/artifacts/import',{project_id:String(fields.get('project_id')),title:String(fields.get('title')).trim(),entrypoint:String(fields.get('entrypoint')),kind:'auto',files:await encodeFiles(importFiles,true)});
        await loadRegistry();dialog.close();await openArtifact(item.id || item.artifact?.id);
        const heading=content.querySelector('.hub-edit-heading h1');
        if(heading){heading.tabIndex=-1;heading.focus({preventScroll:true});}
      }
      if(form.id==='hub-move-form'){
        const select=form.querySelector('select');select.disabled=true;
        try{
          await moveArtifact(form.dataset.artifactId,String(fields.get('project_id')),form.dataset.accessHash);
          if(form.isConnected)dialog.close();
        }catch(reason){
          if(reason.code==='project-access-changed')showMoveConfirmation(form,reason);
          else throw reason;
        }finally{select.disabled=false;}
      }
    })().catch(reason=>{error.textContent=reason.message;submit.textContent=form.id==='hub-import-form'?'Import artifact':submit.textContent;}).finally(()=>{submit.disabled=false;});
  });
  content.addEventListener('error',event=>{if(event.target.matches?.('[data-preview-image]'))coverLoader.imageFailed(event.target);},true);
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
  doc.addEventListener('studio:reference-preview',event=>{
    doc.getElementById('hub-html-preview')?.contentWindow?.postMessage({type:'artifact:reference-preview',...event.detail},'*');
  });
  root.addEventListener('message',event=>{
    const frame=doc.getElementById('hub-html-preview');if(!frame || event.source!==frame.contentWindow || event.origin!=='null' || !event.data || typeof event.data!=='object')return;
    if(event.data.type==='artifact:pointer-enter'&&Number.isSafeInteger(event.data.request_id)){doc.dispatchEvent(new CustomEvent('studio:preview-pointer-enter',{detail:event.data.request_id}));return;}
    if(event.data.type==='artifact:inspector'&&typeof event.data.selector==='string'){if(event.data.image===true)imageSelectors.add(event.data.selector);else imageSelectors.delete(event.data.selector);}
    artifactEditor.receive(event);
    if(['artifact:selection-export-data','artifact:selection-export-error'].includes(event.data.type)){
      const pending=pendingImageExport;if(!pending||event.source!==pending.frame||event.data.request_id!==pending.requestId)return;
      pendingImageExport=null;root.clearTimeout(pending.timeout);
      if(state.active!==pending.id||state.detail?.revision!==pending.revision||state.detail.content_hash!==pending.content_hash)return;
      if(event.data.type==='artifact:selection-export-error'){toast(String(event.data.message||'The image could not be exported.').slice(0,500),true);return;}
      const data=event.data;if(typeof data.preview_html!=='string'||data.preview_html.length>5*1024*1024||!Array.isArray(data.selectors)||!data.selectors.length||data.selectors.length>100||data.selectors.some(selector=>typeof selector!=='string'||selector.length>2000))return;
      void root.ImageExport.save(artifactPath(pending.id,'/selection-image'),{revision:pending.revision,content_hash:pending.content_hash,selectors:data.selectors,preview_html:data.preview_html,width:data.width,height:data.height,color_scheme:data.color_scheme,transparent:true,format:pending.original?'original':'png'}).then(()=>toast('Image downloaded.')).catch(error=>toast(error.message,true));return;
    }
    if(event.data.type==='artifact:geometry' && event.data.selector===inspectorSelector){inspectorRect=event.data.rect;properties?.reposition();}
    if(event.data.type==='artifact:escape')workspacePanels.exitView();
    if(event.data.type==='artifact:import' && !state.selecting && !dialog.open)previewImportDialog(frame);
    if(event.data.type==='artifact:export'){
      const file=exportFile(event.data.file);
      const message=state.selecting?'Finish selecting before exporting.':dialog.open?'Close the Studio dialog and try exporting again.':!file?'Export rejected: use a supported text format and filename with at most 20 Mi characters.':'';
      if(!message)exportDialog(file);
      frame.contentWindow.postMessage({type:'artifact:export-result',ok:!message,message},'*');
    }
    if(event.data.type==='artifact:height' && Number.isFinite(event.data.height))frame.style.height=`${Math.min(30000,Math.max(500,event.data.height+frame.offsetHeight-frame.clientHeight))}px`;
    if(event.data.type==='artifact:contextmenu'&&state.selecting&&draft().targets.some(target=>target.selector===event.data.selector)){const point=root.ElementCommands.framePoint(frame,event.data.x,event.data.y,event.data.viewport);if(point)void showElementMenu(point);return;}
    if(event.data.type==='artifact:targets'&&state.selecting&&Array.isArray(event.data.targets)&&event.data.targets.length<=100){
      for(const target of event.data.targets)if(target&&typeof target.selector==='string'){if(target.image===true)imageSelectors.add(target.selector);else imageSelectors.delete(target.selector);}
      const targets=event.data.targets.filter(target=>target&&target.kind==='element'&&typeof target.selector==='string'&&target.selector.length>0&&target.selector.length<=2000).map(target=>({kind:'element',selector:target.selector,label:String(target.label||target.quote||target.selector).slice(0,200),quote:String(target.quote||'').slice(0,2000),...(target.viewport&&Number.isFinite(target.viewport.width)&&Number.isFinite(target.viewport.height)?{viewport:{width:target.viewport.width,height:target.viewport.height}}:{})}));
      setTargets(targets,event.data.additive===true);
    }
    if(event.data.type==='artifact:target' && state.selecting){const target=event.data.target;if(target && target.kind==='element' && typeof target.selector==='string' && target.selector.length<=2000){if(target.image===true)imageSelectors.add(target.selector);else imageSelectors.delete(target.selector);setTarget({kind:'element',selector:target.selector,label:String(target.label || target.quote || target.selector).slice(0,200),quote:String(target.quote || '').slice(0,2000),...(target.viewport && Number.isFinite(target.viewport.width) && Number.isFinite(target.viewport.height)?{viewport:{width:target.viewport.width,height:target.viewport.height}}:{})},event.data.additive===true);}}
  });
  root.addEventListener('beforeunload',event=>{if(artifactEditor.isEditing() || artifactEditor.isBusy()){event.preventDefault();event.returnValue='';}});
  root.addEventListener('popstate',()=>{const query=new URL(root.location.href).searchParams,id=query.get('artifact');query.get('design')?openDesign(query.get('design'),false):id?openArtifact(id,false):home(query.get('view')==='designs'?'designs':query.get('project') || 'all',false);});
  root.addEventListener('resize',()=>{if(state.detail && kindOf(state.detail)==='image')renderImageRegions();});
  root.addEventListener('beforeunload',event=>{if(voice.isActive() || [...attachments.values()].some(files=>files.length) || [...drafts.values()].some(value=>value.message.trim() || value.voice_segments?.length)){event.preventDefault();event.returnValue='';}});
  function artifactPreviewBusy() {
    return voice.isActive() || artifactEditor.isEditing() || artifactEditor.isBusy() || doc.body.classList.contains('workspace-clean-view') || !!regionStart || sending.has(state.active) || !!doc.activeElement?.closest('#hub-viewer') && (doc.activeElement.tagName==='IFRAME' || doc.activeElement.matches('input,textarea,select,[contenteditable]'));
  }
  /** Apply a new preview while retaining draft anchors, attachment choices and typing focus. */
  async function applyArtifactUpdate(detail) {
    const id=state.active,token=state.loading;
    let source='';
    if(kindOf(detail)==='markdown'){const response=await fetch(filePath(detail));if(!response.ok)throw new Error('The updated document could not be loaded.');source=await response.text();}
    if(state.active!==id || state.loading!==token || !previewUpdates.enabled || artifactPreviewBusy())return false;
    const focused=doc.activeElement,focusId=focused?.id,selection=focusId==='hub-feedback-text'?[focused.selectionStart,focused.selectionEnd]:null,thread=doc.getElementById('hub-thread'),scroll=thread?.scrollTop;
    const value=draft();
    if(!resetEditor())return false;
    state.detail=detail;state.source=source;state.newRevision=null;
    if(!value.message && !value.targets.length && !(attachments.get(id)||[]).length){value.content_hash=detail.content_hash;value.revision=detail.revision;}
    workspace();
    const restored=focusId && doc.getElementById(focusId);if(restored){restored.focus({preventScroll:true});if(selection)restored.setSelectionRange(...selection);}
    if(scroll!==undefined)doc.getElementById('hub-thread').scrollTop=scroll;
    void acknowledgeArtifact(detail);
    return true;
  }
  doc.addEventListener('focusin',()=>{if(state.newRevision && previewUpdates.enabled)void refresh();});
  doc.addEventListener('pointerup',()=>{if(state.newRevision && previewUpdates.enabled)queueMicrotask(()=>void refresh());});
  async function refresh() {
    if(refreshing || doc.hidden || dialog.open)return;refreshing=true;
    try {
      const previous=JSON.stringify([state.artifacts,state.designs,state.projects,state.projectTree]),id=state.active;
      await loadRegistry();
      if(state.detail && state.active===id){
        const latest=state.artifacts.find(item=>item.id===id);
        if(sharedEditor&&!latest){home();return;}
        const detail=await artifactDetail(id);
        if(state.active!==id || !state.detail)return;
        if(detail.content_hash!==state.detail.content_hash){
          state.newRevision=latest || detail;
          const banner=doc.getElementById('hub-new-revision');if(banner)banner.hidden=false;
          if(previewUpdates.enabled && !artifactPreviewBusy() && await applyArtifactUpdate(detail))return;
        }
        syncArtifactReview(detail);
      }else if(state.designActive){
        if(!state.designs.some(item=>item.id===state.designActive))home('designs');
      }else if(!state.active && previous!==JSON.stringify([state.artifacts,state.designs,state.projects,state.projectTree])){
        const search=doc.getElementById('hub-search'),focused=doc.activeElement===search,position=search?.selectionStart;
        library();if(focused){const next=doc.getElementById('hub-search');next.focus();try{next.setSelectionRange(position,position);}catch{}}
      }
    }catch{}finally{refreshing=false;}
  }
  content.addEventListener('change',async event=>{
    if(event.target.id!=='hub-shared-channel'||!state.detail)return;
    const item=state.detail,value=draft(item),before=channelOf(item.id);
    if(sending.has(item.id)||value.message.trim()||attachments.get(item.id)?.length||value.voice_segments?.length){
      event.target.value=before;toast('Send or clear this draft before switching conversations.',true);return;
    }
    conversationChannels.set(item.id,event.target.value);
    try{const detail=await artifactDetail(item.id);if(state.detail===item){state.detail=detail;renderChat();}}
    catch(error){conversationChannels.set(item.id,before);event.target.value=before;toast(error.message,true);}
  });
  const libraryControls=root.SlideLibraries.init({api,toast,escape,currentDeck:()=>null});
  if(new URL(root.location.href).searchParams.get('settings')==='backup')void libraryControls.open();
  headerIcons();
  loadRegistry().then(()=>{const query=new URL(root.location.href).searchParams;const id=query.get('artifact');const project=query.get('project');if(state.projects.some(item=>item.id===project)||state.projectTree.nodes.some(node=>'folder:'+node.id===project))state.project=project;if(query.get('view')==='designs')state.project='designs';return query.get('design')?openDesign(query.get('design'),false):id?openArtifact(id,false):library();}).catch(error=>{content.innerHTML=`<main class="hub-empty">${icon('info')}<h2>The library could not be loaded</h2><p>${escape(error.message)}</p>${button('retry-library','Try again','refresh')}</main>`;toast(error.message,true);});
  setInterval(refresh,12000);
  setInterval(()=>{if(state.active&&state.detail&&!doc.hidden&&(nativeChat||root.StudioInvitation?.available))void refreshArtifactReview(state.active);},3500);
})(typeof window==='undefined'?{}:window);
