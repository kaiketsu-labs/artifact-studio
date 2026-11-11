/* Nested project navigation; grouping never changes artifact source locations. */
((root) => {
  'use strict';
  const MIME='application/x-artifact-studio-project';
  const escape=value=>String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const children=(tree,parent=null)=>tree.nodes.filter(node=>node.parent_id===parent);
  const ancestors=(tree,id)=>{
    const result=[],seen=new Set();let node=tree.nodes.find(node=>node.id===id);
    while(node?.parent_id && !seen.has(node.parent_id)){seen.add(node.parent_id);result.push(node.parent_id);node=tree.nodes.find(item=>item.id===node.parent_id);}
    return result;
  };
  const projectsIn=(tree,id)=>tree.nodes.filter(node=>node.kind==='project' && ancestors(tree,node.id).includes(id)).map(node=>node.id);
  /** Resolve one sibling insertion or folder drop, rejecting self and descendant moves. */
  function placement(tree,id,targetId,zone='inside') {
    const node=tree.nodes.find(item=>item.id===id),target=tree.nodes.find(item=>item.id===targetId);
    if(!node || id===targetId || targetId!==null && !target)return null;
    const parent_id=zone==='inside'?(target?.kind==='folder'?target.id:null):target?.parent_id ?? null;
    if(zone==='inside' && target?.kind==='project' || parent_id===id || parent_id && ancestors(tree,parent_id).includes(id))return null;
    const siblings=children(tree,parent_id).filter(item=>item.id!==id);
    let before_id=null;
    if(zone==='before')before_id=target.id;
    if(zone==='after')before_id=siblings[siblings.findIndex(item=>item.id===target.id)+1]?.id ?? null;
    return {action:'move',id,parent_id,before_id};
  }
  /** Render compact rows with separate folder disclosure and project navigation controls. */
  function markup({tree,projects,selected,collapsed,icon,manage=true}) {
    const projectMap=new Map(projects.map(project=>[project.id,project]));
    function branch(parent=null,depth=0) {
      return children(tree,parent).map(node=>{
        const folder=node.kind==='folder',project=projectMap.get(node.id),title=folder?node.title:project?.title;
        if(!title)return '';
        const open=!collapsed.has(node.id),key=folder?'folder:'+node.id:node.id;
        const count=folder?projectsIn(tree,node.id).length:project.artifact_count;
        return `<div class="project-tree-row" data-tree-node="${escape(node.id)}" data-node-kind="${node.kind}" ${folder||!manage?'':`data-project-folder="${escape(node.id)}"`} draggable="${manage}" style="--tree-depth:${Math.min(depth,8)}"><button type="button" class="project-tree-disclosure ${folder?'':'is-spacer'}" ${folder?`data-toggle-folder="${escape(node.id)}" aria-expanded="${open}" aria-label="${open?'Collapse':'Expand'} ${escape(title)}" title="${open?'Collapse':'Expand'} folder"`:'tabindex="-1" aria-hidden="true"'}>${folder?icon('right'):''}</button><button type="button" class="project-tree-open" aria-label="${escape(title)}" data-project="${escape(key)}" aria-current="${selected===key}" title="${escape(title)}">${icon(folder?(open?'folderOpen':'folder'):'project')}<span class="project-tree-name">${escape(title)}</span>${count?`<span class="hub-count" aria-hidden="true" title="${folder?'Projects':'Artifacts'}">${count}</span>`:''}</button>${manage?`<button type="button" class="project-tree-options" data-tree-menu="${escape(node.id)}" aria-label="Options for ${escape(title)}" title="Options" aria-haspopup="menu">${icon('more')}</button>`:''}</div>${folder&&open?`<div class="project-tree-children" data-folder-children="${escape(node.id)}">${branch(node.id,depth+1)}</div>`:''}`;
      }).join('');
    }
    return `<nav class="project-tree" aria-label="Projects">${branch()}${manage?`<div class="project-tree-root" data-tree-root-drop>${icon('folder')}<span>Move to top level</span></div>`:''}</nav>`;
  }
  /** Attach pointer and keyboard moves to the stable workspace container. */
  function create({content,getTree,onMove,onExpand,onError}) {
    let drag=null,highlight=null,busy=false,expandTimer=null;
    const clear=()=>{if(highlight)highlight.classList.remove('tree-drop-before','tree-drop-after','tree-drop-inside');highlight=null;clearTimeout(expandTimer);expandTimer=null;};
    const reset=()=>{clear();drag=null;content.classList.remove('is-tree-dragging');content.querySelectorAll('.project-tree-row.is-dragging').forEach(node=>node.classList.remove('is-dragging'));};
    const destination=event=>{
      if(!drag || busy || !event.dataTransfer || Array.from(event.dataTransfer.types || []).includes('Files'))return null;
      const row=event.target?.closest?.('[data-tree-node], [data-tree-root-drop]');if(!row)return null;
      const id=row.dataset.treeNode ?? null,rect=row.getBoundingClientRect(),fraction=(event.clientY-rect.top)/rect.height;
      const zone=id===null?'inside':row.dataset.nodeKind==='folder'?(fraction<.25?'before':fraction>.75?'after':'inside'):(fraction<.5?'before':'after');
      const plan=placement(getTree(),drag,id,zone);return plan?{row,plan,zone}:null;
    };
    const handlers={
      dragstart(event){
        reset();const row=event.target?.closest?.('[data-tree-node]');if(busy || !row || !event.dataTransfer)return;
        drag=row.dataset.treeNode;event.dataTransfer.setData(MIME,drag);event.dataTransfer.effectAllowed='move';row.classList.add('is-dragging');content.classList.add('is-tree-dragging');
      },
      dragover(event){
        const result=destination(event);if(!result){clear();return;}
        event.preventDefault();event.dataTransfer.dropEffect='move';
        if(highlight!==result.row || !highlight.classList.contains('tree-drop-'+result.zone)){
          clear();highlight=result.row;highlight.classList.add('tree-drop-'+result.zone);
          if(result.zone==='inside' && result.row.dataset.nodeKind==='folder')expandTimer=setTimeout(()=>onExpand(result.row.dataset.treeNode),650);
        }
      },
      dragleave(event){if(highlight && !highlight.contains(event.relatedTarget))clear();},
      async drop(event){
        const result=destination(event);if(!result || event.dataTransfer.getData(MIME)!==drag){reset();return;}
        event.preventDefault();busy=true;reset();
        try{await onMove(result.plan);}catch(error){onError(error);}finally{busy=false;}
      },
      dragend:reset,
      keydown(event){
        if(!event.altKey || event.ctrlKey || event.metaKey || !['ArrowUp','ArrowDown','ArrowLeft'].includes(event.key))return;
        const row=event.target?.closest?.('[data-tree-node]'),tree=getTree(),node=tree.nodes.find(item=>item.id===row?.dataset.treeNode);if(!node || busy)return;
        const siblings=children(tree,node.parent_id),index=siblings.findIndex(item=>item.id===node.id);
        let plan=null;
        if(event.key==='ArrowUp' && index>0)plan=placement(tree,node.id,siblings[index-1].id,'before');
        if(event.key==='ArrowDown' && index<siblings.length-1)plan=placement(tree,node.id,siblings[index+1].id,'after');
        if(event.key==='ArrowLeft' && node.parent_id)plan=placement(tree,node.id,node.parent_id,'after');
        if(!plan)return;event.preventDefault();busy=true;Promise.resolve(onMove(plan)).catch(onError).finally(()=>{busy=false;});
      }
    };
    for(const [name,handler] of Object.entries(handlers))content.addEventListener(name,handler);
    return {destroy(){for(const [name,handler] of Object.entries(handlers))content.removeEventListener(name,handler);reset();}};
  }
  const api={MIME,children,ancestors,projectsIn,placement,markup,create};
  if(typeof module!=='undefined' && module.exports)module.exports=api;else root.ProjectNavigation=api;
})(typeof window==='undefined'?globalThis:window);
