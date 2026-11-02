/* File-first reusable designs; slide examples are an explicit secondary action. */
((root)=>{
  'use strict';
  function create({api,icon,escape,sizeLabel,toast,modal,changed}){
    const doc=root.document,dialog=doc.getElementById('hub-dialog');
    let selected=null,pending=[],busy=false;
    const path=id=>`/api/design-library/${encodeURIComponent(id)}`;
    const control=(action,label,name,attrs='')=>`<button type="button" data-design-action="${action}" ${attrs}>${icon(name)}${label?`<span>${label}</span>`:''}</button>`;
    const type=file=>file.name.includes('.')?file.name.split('.').at(-1).toUpperCase().slice(0,8):'FILE';
    const fileLabel=file=>file.path.startsWith('reference/uploads/')?'Uploaded reference':file.path;
    function cover(item){
      const formats=(item.formats||[]).filter(format=>!['CSS','HTML','JS'].includes(format)).slice(0,3);
      const fallback=`<span class="design-cover-sheet"><b>${escape(formats[0]||'DESIGN')}</b><span>${escape(item.name)}</span><i></i><i></i><i></i></span><span class="design-cover-formats">${formats.map(format=>`<b>${escape(format)}</b>`).join('')}</span>`;
      const preview=item.preview_url?(item.kind==='slide_pack'?`<iframe src="${escape(item.preview_url)}" title="${escape(item.name)} design preview" tabindex="-1" sandbox loading="lazy"></iframe>`:`<img src="${escape(item.preview_url)}" alt="" loading="lazy" decoding="async" data-design-preview>`):'';
      return `<span class="design-card-art" aria-hidden="true">${fallback}${preview}</span>`;
    }
    function cards(items,search=''){
      if(!items.length)return `<div class="hub-empty">${icon('palette')}<h2>${search?'No matching designs':'Build your design library'}</h2><p>${search?'Try another name.':'Collect guidelines, images, fonts, masters and other references.'}</p>${search?'':control('new','Add design','plus','class="hub-primary"')}</div>`;
      return items.map(item=>`<article class="design-card-summary"><button class="design-card-body" data-open-design="${escape(item.id)}" aria-label="Open ${escape(item.name)} design">${cover(item)}<span class="design-card-heading">${escape(item.name)}${item.is_default?'<small>Default</small>':''}</span><span class="design-card-description">${escape(item.description||'Add references to define this design.')}</span></button><footer class="design-card-meta"><span>${item.kind==='slide_pack'?'Slide design pack':'Reference collection'}</span><span>${item.file_count} ${item.file_count===1?'file':'files'}</span>${control('delete','','trash',`class="design-card-delete" data-design-id="${escape(item.id)}" aria-label="Delete ${escape(item.name)} design" title="Delete design"`)}</footer></article>`).join('');
    }
    function detail(item){
      selected=item;
      const order=['References','Guidance','Assets & styling','Examples & player'];
      const groups=[...new Set(item.files.map(file=>file.group))].sort((a,b)=>order.indexOf(a)-order.indexOf(b));
      return `<nav class="hub-crumbs" aria-label="Breadcrumb">${control('back','Designs','palette')}${icon('right')}<span>${escape(item.name)}</span></nav><header class="design-detail-heading"><div><p class="design-eyebrow">${item.kind==='slide_pack'?'Slide design pack':'Reference collection'}</p><h1>${escape(item.name)}</h1><p class="design-description">${escape(item.description)}</p></div><div class="design-detail-actions">${control('attach','Add files','attach')}${control('refresh','','refresh','aria-label="Refresh design context" title="Refresh context"')}${control('delete','','trash',`data-design-id="${escape(item.id)}" aria-label="Delete ${escape(item.name)} design" title="Delete design"`)}</div></header><section class="design-context"><header><div><h2>Context files <span class="hub-count">${item.files.length}</span></h2><p>Available to your agent as references. Adding files does not apply their styling automatically.</p></div></header>${groups.length?groups.map(group=>`<section class="design-file-group"><h3>${escape(group)}</h3><ul>${item.files.filter(file=>file.group===group).map(file=>`<li><span class="design-file-type" aria-hidden="true">${escape(type(file))}</span><a href="${escape(file.url)}" title="${escape(file.path)}" download><strong>${escape(file.name)}</strong><small>${escape(fileLabel(file))}</small></a><small class="design-file-size">${sizeLabel(file.size)}</small>${file.removable?control('remove-file','','trash',`data-file-path="${escape(file.path)}" class="hub-icon-only" aria-label="Remove ${escape(file.name)} from design context" title="Remove from context"`):''}</li>`).join('')}</ul></section>`).join(''):`<div class="design-empty-files">${icon('attach')}<p>No context files yet.</p>${control('attach','Add files','plus')}</div>`}</section>${item.kind==='slide_pack'?`<a class="design-example-link" href="/?deck=${encodeURIComponent(item.folder_id)}">${icon('slides')}Open example slides${icon('external')}</a>`:''}`;
    }
    function filesForm(creating){
      pending=[];
      modal(creating?'Add design':'Add reference files',`<form id="design-library-form" class="hub-form" data-creating="${creating}" data-design-id="${escape(selected?.id||'')}">${creating?'<label class="hub-field"><span>Name</span><input name="name" required maxlength="80" placeholder="e.g. Studio Blue"></label><label class="hub-field"><span>Description <small>· optional</small></span><textarea name="description" maxlength="2000" placeholder="What defines this design?"></textarea></label>':''}<div class="design-upload">${control('choose','Choose files','attach')}<small>Any file type · up to 5 files · 25 MB each / 50 MB total</small><input type="file" id="design-library-input" multiple hidden><ul id="design-library-pending"></ul></div><p id="design-library-error" class="hub-compose-error" role="alert"></p><div class="hub-form-actions"><button type="submit" class="hub-primary">${creating?'Create design':'Add files'}</button></div></form>`);
      if(creating)dialog.querySelector('[name=name]').focus();
    }
    function renderPending(){
      doc.getElementById('design-library-pending').innerHTML=pending.map((file,index)=>`<li><span>${escape(file.name)}</span><small>${sizeLabel(file.size)}</small>${control('unselect','','close',`data-file-index="${index}" class="hub-icon-only" aria-label="Remove ${escape(file.name)} from selection"`)}</li>`).join('');
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
        if(action==='new')return filesForm(true);
        if(action==='attach')return filesForm(false);
        if(action==='choose')return doc.getElementById('design-library-input').click();
        if(action==='unselect'){pending.splice(Number(node.dataset.fileIndex),1);renderPending();return;}
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
    doc.addEventListener('error',event=>{if(event.target.matches?.('[data-design-preview]'))event.target.remove();},true);
    doc.addEventListener('change',event=>{
      if(event.target.id!=='design-library-input')return;
      const next=[...pending,...event.target.files];event.target.value='';
      if(next.length>5||next.some(file=>file.size>25000000)||next.reduce((n,file)=>n+file.size,0)>50000000){doc.getElementById('design-library-error').textContent='Up to 5 files, 25 MB each and 50 MB total.';return;}
      pending=next;doc.getElementById('design-library-error').textContent='';renderPending();
    });
    doc.addEventListener('submit',event=>{
      const form=event.target;if(!['design-library-form','design-delete-form'].includes(form.id))return;
      event.preventDefault();if(busy)return;busy=true;
      const error=form.querySelector('[role=alert]');error.textContent='';
      for(const node of form.querySelectorAll('button,input,textarea'))node.disabled=true;
      (async()=>{
        if(form.id==='design-delete-form'){
          await api(path(form.dataset.designId)+'/delete',{expected_version:form.dataset.version});dialog.close();await changed(null);toast('Design deleted.');return;
        }
        const files=await encoded(),creating=form.dataset.creating==='true';
        if(!creating&&!files.length)throw Error('Choose a file to add.');
        const item=await api(creating?'/api/design-library':path(form.dataset.designId)+'/files',creating?{name:form.querySelector('[name=name]').value.trim(),description:form.querySelector('[name=description]').value,files}:{files});
        pending=[];dialog.close();await changed(item.id);toast(creating?'Design added.':'Reference files added.');
      })().catch(failure=>{error.textContent=failure.message;}).finally(()=>{busy=false;for(const node of form.querySelectorAll('button,input,textarea'))node.disabled=false;});
    });
    return {cards,detail};
  }
  root.SlideDesignLibrary={create};
})(window);
