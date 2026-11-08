/* File-first reusable designs; slide examples are an explicit secondary action. */
((root)=>{
  'use strict';
  function create({api,icon,escape,sizeLabel,toast,modal,changed}){
    const doc=root.document,dialog=doc.getElementById('hub-dialog');
    let selected=null,pending=[],busy=false,textRequest=null,textPreview=null,editorLoad=null;
    const path=id=>`/api/design-library/${encodeURIComponent(id)}`;
    const control=(action,label,name,attrs='')=>`<button type="button" data-design-action="${action}" ${attrs}>${icon(name)}${label?`<span>${label}</span>`:''}</button>`;
    const type=file=>file.name.includes('.')?file.name.split('.').at(-1).toUpperCase().slice(0,8):'FILE';
    const fileLabel=file=>file.path.startsWith('reference/uploads/')?'Uploaded reference':file.path;
    const imageTypes=new Set(['SVG','PNG','JPG','JPEG','WEBP','GIF','AVIF','BMP','TIF','TIFF','ICO']);
    const binaryTypes=new Set([...imageTypes,'PPT','PPTX','POTX','PDF','DOC','DOCX','XLS','XLSX','ZIP','GZ','7Z','RAR','TTF','OTF','WOFF','WOFF2','EMF','WMF','WDP','MP3','MP4','MOV','WAV']);
    const textFile=file=>!binaryTypes.has(type(file));
    const fileKinds={PPTX:'slides',POTX:'slides',PPT:'slides',PDF:'pdf',DOC:'document',DOCX:'document',MD:'document',MARKDOWN:'document',TXT:'document',RTF:'document',CSV:'data',TSV:'data',XLS:'data',XLSX:'data',JSON:'data',YAML:'data',YML:'data',TOML:'data',CSS:'stylesheet',SCSS:'stylesheet',SASS:'stylesheet',LESS:'stylesheet',JS:'script',JSX:'script',MJS:'script',CJS:'script',TS:'typescript',TSX:'typescript',HTML:'markup',HTM:'markup',XML:'markup',PY:'code',RB:'code',GO:'code',RS:'code',SH:'code',ZIP:'archive',TTF:'font',OTF:'font',WOFF:'font',WOFF2:'font',EMF:'image',WMF:'image',WDP:'image',TIFF:'image',TIF:'image',BMP:'image',ICO:'image'};
    const fileIcons={slides:'slides',pdf:'pdf',document:'notes',data:'table',stylesheet:'palette',script:'code',typescript:'code',markup:'code',code:'code',archive:'archive',font:'font',image:'image',file:'file'};
    function filePreview(file){
      const extension=type(file),endpoint=extension==='SVG'?'image':imageTypes.has(extension)||['PPTX','POTX'].includes(extension)?'thumbnail':'';
      return endpoint?`${file.url.replace('/files/',`/${endpoint}/`)}?v=${encodeURIComponent(file.version)}`:'';
    }
    function fileVisual(file){
      const extension=type(file),kind=imageTypes.has(extension)?'image':fileKinds[extension]||'file',preview=filePreview(file);
      const badge=`<span class="design-file-badge is-${kind}" aria-hidden="true">${icon(fileIcons[kind])}<b>${escape(extension)}</b></span>`;
      return preview||textFile(file)?`<button type="button" class="design-file-visual has-preview${preview?'':' is-text'}" data-design-action="preview-file" data-file-path="${escape(file.path)}" aria-label="Preview ${escape(file.name)}" title="Preview ${escape(file.name)}">${badge}${preview?`<img src="${escape(preview)}" alt="" loading="lazy" decoding="async" data-design-file-image>`:''}</button>`:`<span class="design-file-visual" role="img" aria-label="${escape(extension)} file" title="${escape(extension)} file">${badge}</span>`;
    }
    function fileRow(file){
      const label=`<strong>${escape(file.name)}</strong><small>${escape(fileLabel(file))}</small>`;
      const name=textFile(file)?`<button type="button" class="design-file-name" data-design-action="preview-file" data-file-path="${escape(file.path)}" title="${escape(file.path)}" aria-label="Open ${escape(file.name)}">${label}</button>`:`<a href="${escape(file.url)}" title="${escape(file.path)}" download>${label}</a>`;
      return `<li>${fileVisual(file)}${name}<small class="design-file-size">${sizeLabel(file.size)}</small>${file.removable?control('remove-file','','trash',`data-file-path="${escape(file.path)}" class="hub-icon-only" aria-label="Remove ${escape(file.name)} from design context" title="Remove from context"`):''}</li>`;
    }
    /** Bound reference reads without preloading file bodies into discovery or agent context. */
    async function readText(file,signal){
      const response=await root.fetch(file.url,{signal,cache:'no-store'});
      if(!response.ok)throw Error('This file could not be opened.');
      const reader=response.body?.getReader();if(!reader)return {text:'',truncated:false};
      const limit=1024*1024,chunks=[];let total=0,truncated=false;
      try{while(true){const {value,done}=await reader.read();if(done)break;const remaining=limit-total;if(value.length>remaining){chunks.push(value.slice(0,remaining));total+=remaining;truncated=true;break;}chunks.push(value);total+=value.length;}}
      finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
      const data=new Uint8Array(total);let offset=0;for(const chunk of chunks){data.set(chunk,offset);offset+=chunk.length;}
      const encoding=data[0]===255&&data[1]===254?'utf-16le':data[0]===254&&data[1]===255?'utf-16be':'utf-8';
      const decoder=new TextDecoder(encoding,{fatal:true});let text;
      try{text=decoder.decode(data,{stream:truncated});}catch{throw Error('This file is not readable text. Download the original to open it.');}
      if(/[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(text))throw Error('This file is not readable text. Download the original to open it.');
      const hash=Array.from(new Uint8Array(await root.crypto.subtle.digest('SHA-256',data)),byte=>byte.toString(16).padStart(2,'0')).join('');
      return {text,truncated,hash};
    }
    const textDirty=()=>!!textPreview?.editing&&textPreview.editor?.value()!==textPreview.text.replace(/\r\n?/g,'\n');
    function textActions(){
      if(!textPreview?.panel.isConnected)return;
      const {panel,editing,saving,truncated}=textPreview;
      for(const node of panel.querySelectorAll('[data-text-read]'))node.hidden=!!editing;
      for(const node of panel.querySelectorAll('[data-text-edit]'))node.hidden=!editing;
      panel.querySelector('[data-design-action="edit-text"]').disabled=!!truncated||!!textPreview.loadingEditor;
      panel.querySelector('[data-design-action="save-text"]').disabled=!!saving||!textDirty();
      panel.querySelector('[data-design-action="cancel-text"]').disabled=!!saving;
      panel.querySelector('[data-design-action="save-text"] span').textContent=saving?'Saving…':'Save';
      dialog.querySelector('[data-action="close-dialog"]').disabled=!!saving;
      panel.setAttribute('aria-busy',String(!!saving));
      const dirty=panel.querySelector('[data-code-dirty]');if(dirty)dirty.hidden=!textDirty();
    }
    function showText(source=false){
      if(!textPreview?.panel.isConnected||textPreview.editing)return;
      textPreview.source=source;
      const {panel,text,markdown}=textPreview,content=panel.querySelector('[data-design-text-content]');
      content.classList.toggle('is-source',!markdown||source);
      if(!text)content.innerHTML='<p class="design-text-state">Empty file</p>';
      else if(markdown&&!source)content.innerHTML=root.SlideChatMarkdown.render(text);
      else{const pre=doc.createElement('pre'),code=doc.createElement('code');code.textContent=text;pre.append(code);content.replaceChildren(pre);}
      for(const button of panel.querySelectorAll('[data-text-view]'))button.setAttribute('aria-pressed',String((button.dataset.textView==='source')===source));
      content.scrollTop=0;
      textActions();
    }
    async function editText(){
      if(!textPreview||textPreview.truncated)return;
      const current=textPreview,{panel,text,file}=current,content=panel.querySelector('[data-design-text-content]');
      current.loadingEditor=true;textActions();
      try{
        if(!editorLoad)editorLoad=new Promise((resolve,reject)=>{const script=doc.createElement('script');script.src='/web/vendor/codemirror.js';script.onload=resolve;script.onerror=()=>{script.remove();editorLoad=null;reject(Error('The editor could not load. Try again.'));};doc.head.append(script);});
        await editorLoad;if(textPreview!==current||!panel.isConnected)return;
        current.editing=true;content.classList.add('is-source','is-editing');
        content.innerHTML=`<div class="design-code-header">${icon('code')}<span>${escape(file.name)}</span><i data-code-dirty hidden aria-label="Unsaved changes"></i>${control('find-text','','search','class="hub-icon-only" aria-label="Find in file" title="Find (⌘F / Ctrl+F)"')}</div><div class="design-code-body" data-voice-exclude></div><div class="design-code-status"><span data-code-position>Ln 1, Col 1</span><span data-code-language></span></div>`;
        current.editor=root.ArtifactTextEditor.create(content.querySelector('.design-code-body'),{value:text,name:file.name,onChange:textActions,onSave:()=>void saveText(),onPosition:(line,column)=>{content.querySelector('[data-code-position]').textContent=`Ln ${line}, Col ${column}`;}});
        content.querySelector('[data-code-language]').textContent=current.editor.language;textActions();current.editor.focus();
      }finally{current.loadingEditor=false;if(textPreview===current)textActions();}
    }
    function cancelText(){
      if(!textPreview||textPreview.saving)return;
      textPreview.editor?.destroy();textPreview.editor=null;textPreview.editing=false;textPreview.panel.querySelector('[data-design-text-content]').classList.remove('is-editing');
      textPreview.panel.querySelector('[data-design-text-error]').hidden=true;
      showText(textPreview.source);textPreview.panel.querySelector('[data-design-action="edit-text"]').focus();
    }
    async function saveText(){
      if(!textPreview||textPreview.saving||!textDirty())return;
      const current=textPreview,{panel,file,designId,editor}=current,error=panel.querySelector('[data-design-text-error]');
      current.saving=true;editor.setReadOnly(true);error.hidden=true;textActions();
      try{
        const item=await api(path(designId)+'/files/edit',{path:file.path,text:editor.value(),expected_hash:current.hash});
        current.text=editor.value();current.file=item.files.find(item=>item.path===file.path);current.hash=current.file.sha256;current.editing=false;current.saving=false;
        editor.destroy();current.editor=null;
        panel.querySelector('[data-design-text-content]').classList.remove('is-editing');
        showText(current.source);toast('File saved.');
        await changed(designId).catch(()=>toast('File saved. Refresh the file list to update it.',true));
      }catch(failure){
        error.hidden=false;error.querySelector('[role="alert"]').textContent=failure.message;
        error.querySelector('[data-text-conflict]').hidden=failure.status!==409;
        editor.setReadOnly(false);editor.focus();
      }finally{current.saving=false;if(textPreview===current){textActions();if(!current.editing)panel.querySelector('[data-design-action="edit-text"]').focus();}}
    }
    async function openText(file){
      textRequest?.abort();textPreview?.editor?.destroy();const request=new AbortController();textRequest=request;textPreview=null;
      const markdown=['MD','MARKDOWN','MDOWN','MKD'].includes(type(file));
      modal(file.name,`<section class="design-text-preview"><div class="design-text-toolbar">${markdown?`<div class="design-text-views" role="group" aria-label="File view" data-text-read>${control('text-view','Preview','eye','data-text-view="preview" aria-pressed="true"')}${control('text-view','Source','code','data-text-view="source" aria-pressed="false"')}</div>`:'<span></span>'}<div class="design-text-actions"><a href="${escape(file.url)}" download data-text-read>${icon('download')}Download original</a>${control('edit-text','Edit','edit','data-text-read disabled')}${control('cancel-text','Cancel','close','data-text-edit hidden')}${control('save-text','Save','save','class="hub-primary" data-text-edit hidden disabled title="Save (⌘S / Ctrl+S)"')}</div></div><div class="design-text-content" data-design-text-content tabindex="0" aria-label="File content"><p class="design-text-state" role="status">Loading…</p></div><div class="design-text-error" data-design-text-error hidden><p role="alert"></p><div data-text-conflict hidden>${control('copy-text','Copy edits','copy')}${control('reload-text','Open latest','refresh')}</div></div><small class="design-text-limit" hidden>Preview limited to the first 1 MB. Download the original to edit the complete file.</small></section>`);
      const panel=dialog.querySelector('.design-text-preview');
      try{const result=await readText(file,request.signal);if(textRequest!==request||!panel.isConnected||!dialog.open)return;textPreview={panel,...result,markdown,file,designId:selected.id,editing:false,saving:false};panel.querySelector('.design-text-limit').hidden=!result.truncated;showText();}
      catch(error){if(request.signal.aborted||!panel.isConnected)return;const content=panel.querySelector('[data-design-text-content]');content.replaceChildren();const message=doc.createElement('p');message.className='design-text-state';message.setAttribute('role','alert');message.textContent=error.message;content.append(message);}
    }
    dialog.addEventListener('close',()=>{textRequest?.abort();textPreview?.editor?.destroy();textRequest=null;textPreview=null;});
    const allowTextClose=()=>!textPreview?.saving&&(!textDirty()||root.confirm('Discard unsaved changes?'));
    dialog.addEventListener('cancel',event=>{if(!allowTextClose())event.preventDefault();});
    dialog.addEventListener('click',event=>{if(event.target.closest('[data-action="close-dialog"]')&&!allowTextClose()){event.preventDefault();event.stopPropagation();}},true);
    dialog.addEventListener('keydown',event=>{if(!event.defaultPrevented&&(event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='s'&&textPreview?.editing){event.preventDefault();void saveText();}});
    root.addEventListener('beforeunload',event=>{if(textDirty()||textPreview?.saving){event.preventDefault();event.returnValue='';}});
    function cover(item){
      const formats=(item.formats||[]).filter(format=>!['CSS','HTML','JS'].includes(format)).slice(0,3);
      const fallback=`<span class="design-cover-sheet"><b>${escape(formats[0]||'DESIGN')}</b><span>${escape(item.name)}</span><i></i><i></i><i></i></span><span class="design-cover-formats">${formats.map(format=>`<b>${escape(format)}</b>`).join('')}</span>`;
      const preview=item.preview_url?(item.kind==='slide_pack'?`<iframe src="${escape(item.preview_url)}" title="${escape(item.name)} design preview" tabindex="-1" sandbox loading="lazy"></iframe>`:`<img src="${escape(item.preview_url)}" alt="" loading="lazy" decoding="async" data-design-preview>`):'';
      return `<span class="design-card-art" aria-hidden="true">${fallback}${preview}</span>`;
    }
    function cards(items,search=''){
      if(!items.length)return `<div class="hub-empty">${icon('palette')}<h2>${search?'No matching designs':'Build your design library'}</h2><p>${search?'Try another name.':'Collect guidelines, images, fonts, masters and other references.'}</p>${search?'':control('new','Add design','plus','class="hub-primary"')}</div>`;
      return items.map(item=>`<article class="design-card-summary"><button class="design-card-body" data-open-design="${escape(item.id)}" aria-label="Open ${escape(item.name)} design" aria-describedby="${item.is_default?`design-default-${escape(item.id)} `:''}design-description-${escape(item.id)}">${cover(item)}<span class="design-card-heading">${escape(item.name)}${item.is_default?`<small id="design-default-${escape(item.id)}">Default</small>`:''}</span><span class="design-card-description" id="design-description-${escape(item.id)}">${escape(item.description||'Add references to define this design.')}</span></button><footer class="design-card-meta"><span>${item.kind==='slide_pack'?'Slide design pack':'Reference collection'}</span><span>${item.file_count} ${item.file_count===1?'file':'files'}</span>${defaultControl(item)}${control('delete','','trash',`class="design-card-delete" data-design-id="${escape(item.id)}" aria-label="Delete ${escape(item.name)} design" title="Delete design"`)}</footer></article>`).join('');
    }
    function defaultControl(item){
      if(item.kind!=='slide_pack')return '';
      const label=item.is_default?`${item.name} is the default design`:`Set ${item.name} as default`;
      return control('set-default','','star',`class="hub-icon-only design-default-control" data-design-id="${escape(item.id)}" aria-label="${escape(label)}" title="${escape(item.is_default?'Default for new presentations':'Set as default for new presentations')}" aria-pressed="${!!item.is_default}"`);
    }
    function detail(item){
      selected=item;
      const order=['References','Guidance','Assets & styling','Examples & player'];
      const groups=[...new Set(item.files.map(file=>file.group))].sort((a,b)=>order.indexOf(a)-order.indexOf(b));
      return `<nav class="hub-crumbs" aria-label="Breadcrumb">${control('back','Designs','palette')}${icon('right')}<span>${escape(item.name)}</span></nav><header class="design-detail-heading"><div><p class="design-eyebrow">${item.kind==='slide_pack'?'Slide design pack':'Reference collection'}</p><h1>${escape(item.name)}</h1><p class="design-description">${escape(item.description)}</p></div><div class="design-detail-actions">${defaultControl(item)}${control('use','Use with agent','robot',`class="hub-primary" ${!item.files.length&&!item.description.trim()?'disabled title="Add reference files or a description first"':''}`)}${control('attach','Add files','attach')}${control('refresh','','refresh','aria-label="Refresh design context" title="Refresh context"')}${control('delete','','trash',`data-design-id="${escape(item.id)}" aria-label="Delete ${escape(item.name)} design" title="Delete design"`)}</div></header><section class="design-context"><header><div><h2>Context files <span class="hub-count">${item.files.length}</span></h2><p>Available to your agent as references. Adding files does not apply their styling automatically.</p></div></header>${groups.length?groups.map(group=>`<section class="design-file-group"><h3>${escape(group)}</h3><ul>${item.files.filter(file=>file.group===group).map(fileRow).join('')}</ul></section>`).join(''):`<div class="design-empty-files">${icon('attach')}<p>No context files yet.</p>${control('attach','Add files','plus')}</div>`}</section>${item.kind==='slide_pack'?`<a class="design-example-link" href="/?deck=${encodeURIComponent(item.folder_id)}">${icon('slides')}Open example slides${icon('external')}</a>`:''}`;
    }
    function filesForm(creating){
      pending=[];
      modal(creating?'Add design':'Add reference files',`<form id="design-library-form" class="hub-form" data-creating="${creating}" data-design-id="${escape(selected?.id||'')}">
        ${creating?`<p class="design-form-intro">Collect reference files to guide your agent.</p>
        <label class="hub-field"><span>Name</span><input name="name" required maxlength="80" placeholder="e.g. Studio Blue"></label>`:''}
        <section class="design-reference-field" aria-label="Reference files">
          <p class="design-field-label">Reference files ${creating?'<small>· optional</small>':''}</p>
          <div class="design-upload" data-design-drop>
            ${control('choose','Drop files here or choose files','attach','class="design-upload-choose"')}
            <p>Master slides, guidelines, logos or images. Any file type.</p>
            <input type="file" id="design-library-input" multiple hidden>
            <ul id="design-library-pending"></ul>
          </div>
          <small class="design-upload-limits">Up to 5 files · 25 MB each / 50 MB total</small>
        </section>
        ${creating?`<details class="design-description-disclosure"><summary>Add a description <small>· optional</small></summary><textarea name="description" aria-label="Description" maxlength="2000" placeholder="What defines this design?"></textarea></details>`:''}
        <p id="design-library-error" class="hub-compose-error" role="alert"></p>
        <div class="hub-form-actions"><button type="submit" class="hub-primary" ${creating?'disabled':''}>${creating?'Add design':'Add files'}</button></div>
      </form>`);
      updateSubmit();
      if(creating)dialog.querySelector('[name=name]').focus();
    }
    function updateSubmit(){
      const form=doc.getElementById('design-library-form');if(!form)return;
      const creating=form.dataset.creating==='true';
      form.querySelector('[type=submit]').disabled=busy||(creating?!form.querySelector('[name=name]').value.trim():!pending.length);
    }
    function renderPending(){
      doc.getElementById('design-library-pending').innerHTML=pending.map((file,index)=>`<li><span>${escape(file.name)}</span><small>${sizeLabel(file.size)}</small>${control('unselect','','close',`data-file-index="${index}" class="hub-icon-only" aria-label="Remove ${escape(file.name)} from selection"`)}</li>`).join('');
      updateSubmit();
    }
    function addFiles(files){
      if(busy||!doc.getElementById('design-library-form'))return;
      const next=[...pending,...files];
      if(next.length>5||next.some(file=>file.size>25000000)||next.reduce((n,file)=>n+file.size,0)>50000000){doc.getElementById('design-library-error').textContent='Up to 5 files, 25 MB each and 50 MB total.';return;}
      pending=next;doc.getElementById('design-library-error').textContent='';renderPending();
    }
    function useDialog(){
      const link=new URL('/',root.location.origin);link.searchParams.set('view','designs');link.searchParams.set('design',selected.id);
      const message=`Use “${selected.name}” from my Artifact Studio design library as visual reference for my next task. Inspect its current references first and flag anything you cannot read.

${link}`;
      const share=root.ArtifactSharing.canShare({title:selected.name,text:message})?control('share-message','Share…','share','aria-label="Share message" title="Share this message using apps on your device"'):'';
      modal('Use with agent',`<div class="design-agent-handoff"><p>Use this message in your connected agent’s chat.</p><label class="hub-field"><span>Message for your agent</span><textarea id="design-agent-message" readonly rows="7">${escape(message)}</textarea></label><p id="design-agent-copy-status" role="status"></p><div class="hub-form-actions">${share}${control('copy-message','Copy message','copy','class="hub-primary"')}</div></div>`);
    }
    async function encoded(){
      return Promise.all(pending.map(file=>new Promise((resolve,reject)=>{
        const reader=new FileReader();reader.onload=()=>resolve({name:file.name,data_base64:String(reader.result).split(',')[1]});reader.onerror=()=>reject(Error(`Could not read ${file.name}.`));reader.readAsDataURL(file);
      })));
    }
    async function removeDialog(id){
      const item=await api(path(id));
      modal(`Delete ${item.name}?`,`<p>Remove this design from your library. Existing presentations and source files are kept. You can restore it from Deleted designs.</p><form id="design-delete-form" data-design-id="${escape(id)}" data-version="${escape(item.version)}"><p class="hub-compose-error" role="alert"></p><div class="hub-form-actions"><button type="submit" class="design-danger">${icon('trash')}Delete design</button></div></form>`);
    }
    async function deletedDialog(){
      const items=await api('/api/design-library/deleted');
      modal('Deleted designs',items.length?`<ul class="design-deleted-list">${items.map(item=>`<li><span>${escape(item.name)}</span>${control('restore','Restore','restore',`data-design-id="${escape(item.id)}"`)}</li>`).join('')}</ul>`:'<p>No deleted designs.</p>');
    }
    doc.addEventListener('click',event=>{
      const node=event.target.closest('[data-design-action]');if(!node)return;
      const run=async()=>{
        const action=node.dataset.designAction;
        if(busy)return;
        if(action==='set-default'){
          if(node.getAttribute('aria-pressed')==='true')return;
          const id=node.dataset.designId,detailView=!!node.closest('.design-detail-heading');busy=true;node.disabled=true;
          try{await api(path(id)+'/default',{});await changed(detailView?id:null);toast('Default design updated.');doc.querySelector(`[data-design-action="set-default"][data-design-id="${CSS.escape(id)}"]`)?.focus();}
          finally{busy=false;if(node.isConnected){node.disabled=false;node.focus({preventScroll:true});}}return;
        }
        if(action==='new')return filesForm(true);
        if(action==='attach')return filesForm(false);
        if(action==='use')return useDialog();
        if(action==='text-view'){showText(node.dataset.textView==='source');return;}
        if(action==='edit-text')return editText();
        if(action==='cancel-text')return cancelText();
        if(action==='save-text')return saveText();
        if(action==='find-text')return textPreview?.editor?.find();
        if(action==='copy-text'){await root.navigator.clipboard.writeText(textPreview.editor.value());toast('Edits copied.');return;}
        if(action==='reload-text'){if(root.confirm('Discard your edits and open the latest version?'))return openText(textPreview.file);return;}
        if(action==='preview-file'){
          const file=selected.files.find(file=>file.path===node.dataset.filePath);
          if(textFile(file))return openText(file);
          modal(file.name,`<div class="design-preview-dialog"><img src="${escape(filePreview(file))}" alt="${escape(file.name)}" decoding="async"><a href="${escape(file.url)}" download>${icon('download')}Download original</a></div>`);return;
        }
        if(action==='share-message'){
          const field=doc.getElementById('design-agent-message');node.disabled=true;doc.getElementById('design-agent-copy-status').textContent='';
          try{await root.navigator.share({title:selected.name,text:field.value});}
          catch(error){if(error.name!=='AbortError')doc.getElementById('design-agent-copy-status').textContent='Sharing unavailable. Copy the message instead.';}
          finally{node.disabled=false;}return;
        }
        if(action==='copy-message'){
          const field=doc.getElementById('design-agent-message');node.disabled=true;
          try{await root.navigator.clipboard.writeText(field.value);dialog.close();toast('Message copied. Paste it into your agent’s chat.');}
          catch{field.focus();field.select();doc.getElementById('design-agent-copy-status').textContent='Copy unavailable. Select and copy the message above.';}
          finally{node.disabled=false;}return;
        }
        if(action==='choose')return doc.getElementById('design-library-input').click();
        if(action==='unselect'){pending.splice(Number(node.dataset.fileIndex),1);doc.getElementById('design-library-error').textContent='';renderPending();return;}
        if(action==='back')return changed(null);
        if(action==='refresh')return changed(selected.id);
        if(action==='delete')return removeDialog(node.dataset.designId);
        if(action==='deleted')return deletedDialog();
        if(action==='restore'){
          node.disabled=true;try{await api(path(node.dataset.designId)+'/restore',{});dialog.close();await changed(null);toast('Design restored.');}finally{node.disabled=false;}return;
        }
        if(action==='remove-file'){
          const item=selected,file=item.files.find(file=>file.path===node.dataset.filePath);node.disabled=true;
          try{await api(path(item.id)+'/files/remove',{path:file.path,expected_version:file.version});await changed(item.id);toast('File removed from design context.');}finally{node.disabled=false;}
        }
      };run().catch(error=>toast(error.message,true));
    });
    doc.addEventListener('error',event=>{
      const image=event.target;
      if(image.matches?.('[data-design-preview]'))image.remove();
      if(image.matches?.('[data-design-file-image]')){
        const button=image.parentElement;
        image.remove();button.classList.remove('has-preview');button.disabled=true;
        button.removeAttribute('title');button.setAttribute('aria-label',button.getAttribute('aria-label').replace(/^Preview /,''));
      }
    },true);
    doc.addEventListener('input',event=>{if(event.target.matches('#design-library-form [name=name]'))updateSubmit();});
    doc.addEventListener('change',event=>{
      if(event.target.id!=='design-library-input')return;
      addFiles([...event.target.files]);event.target.value='';
    });
    doc.addEventListener('dragover',event=>{
      const zone=event.target.closest('[data-design-drop]');if(!zone||busy||![...(event.dataTransfer?.types||[])].includes('Files'))return;
      event.preventDefault();event.dataTransfer.dropEffect='copy';zone.classList.add('is-dragover');
    });
    doc.addEventListener('dragleave',event=>{
      const zone=event.target.closest('[data-design-drop]');if(zone&&!zone.contains(event.relatedTarget))zone.classList.remove('is-dragover');
    });
    doc.addEventListener('drop',event=>{
      const zone=event.target.closest('[data-design-drop]');if(!zone||!event.dataTransfer?.files.length)return;
      event.preventDefault();zone.classList.remove('is-dragover');addFiles([...event.dataTransfer.files]);
    });
    doc.addEventListener('submit',event=>{
      const form=event.target;if(!['design-library-form','design-delete-form'].includes(form.id))return;
      event.preventDefault();if(busy)return;busy=true;
      const error=form.querySelector('[role=alert]');error.textContent='';
      const submit=form.querySelector('[type=submit]'),label=submit.textContent;
      form.setAttribute('aria-busy','true');submit.textContent=form.id==='design-delete-form'?'Deleting…':'Adding…';
      for(const node of form.querySelectorAll('button,input,textarea'))node.disabled=true;
      (async()=>{
        if(form.id==='design-delete-form'){
          await api(path(form.dataset.designId)+'/delete',{expected_version:form.dataset.version});dialog.close();await changed(null);toast('Design deleted.');return;
        }
        const files=await encoded(),creating=form.dataset.creating==='true';
        if(!creating&&!files.length)throw Error('Choose a file to add.');
        const item=await api(creating?'/api/design-library':path(form.dataset.designId)+'/files',creating?{name:form.querySelector('[name=name]').value.trim(),description:form.querySelector('[name=description]').value,files}:{files});
        pending=[];dialog.close();await changed(item.id);toast(creating?'Design added.':'Reference files added.');
      })().catch(failure=>{error.textContent=failure.message;}).finally(()=>{busy=false;form.removeAttribute('aria-busy');submit.textContent=label;for(const node of form.querySelectorAll('button,input,textarea'))node.disabled=false;updateSubmit();});
    });
    return {cards,detail};
  }
  root.SlideDesignLibrary={create};
})(window);
