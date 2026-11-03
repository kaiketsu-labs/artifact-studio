/* Host-side controller for revision-bound, provisional sandbox edits. */
((root) => {
  'use strict';
  const properties = new Set(['text','font_size','font_size_unit','font_weight','font_style','text_align','color']);
  const placements=new Set(['x','y','position_reference','canvas_reference','canvas_width','canvas_height','deleted']);
  const units = new Set(['px','pt','em','rem','%','vw','vh','cqw','cqh']);
  function validPatch(values) {
    if (!values || typeof values !== 'object' || Array.isArray(values)) return false;
    const keys=Object.keys(values);
    if (!keys.length || keys.some(key=>!properties.has(key)&&!placements.has(key))) return false;
    for(const key of ['x','y'])if(key in values && (typeof values[key]!=='number'||!Number.isFinite(values[key])||values[key]<0||values[key]>1))return false;
    for(const key of ['canvas_width','canvas_height'])if(key in values && (typeof values[key]!=='number'||!Number.isFinite(values[key])||values[key]<1||values[key]>1000000))return false;
    if('deleted' in values&&!['true','false'].includes(values.deleted))return false;
    if('position_reference' in values&&values.position_reference!=='visual_top_left')return false;
    if('canvas_reference' in values&&values.canvas_reference!=='artifact_document')return false;
    if ('text' in values && (typeof values.text!=='string' || values.text.length>9000)) return false;
    if ('font_size' in values || 'font_size_unit' in values) {
      if (typeof values.font_size!=='number' || !Number.isFinite(values.font_size) || values.font_size<=0 || values.font_size>10000 || !units.has(values.font_size_unit)) return false;
    }
    if ('font_weight' in values && (typeof values.font_weight!=='string' || !/^(normal|bold|bolder|lighter|[1-9]00)$/.test(values.font_weight))) return false;
    if ('font_style' in values && !['normal','italic'].includes(values.font_style)) return false;
    if ('text_align' in values && !['left','center','right','justify'].includes(values.text_align)) return false;
    if ('color' in values && (typeof values.color!=='string' || values.color.length>100 || /[;{}]/.test(values.color))) return false;
    return true;
  }
  /** Keep edits local until an acknowledged, revision-anchored feedback submission. */
  function create({getFrame,getArtifact,onInspector,onState,onSend,onError}) {
    let selection=null,changes={},baseline=null,baselinePixels=null,descriptor=null,inspectId=null,inspectTimer=null,busy=false,sent=false,sequence=0,generation=0,requestId=null;
    const pending=new Map();
    const history=root.ElementCommands?.history()||{record(){},clear(){},undo(){},redo(){},canUndo:()=>false,canRedo:()=>false};
    let initialPosition=null;

    const dirty=()=>Object.keys(changes).length>0 || pending.size>0;
    const state=()=>onState({dirty:dirty(),busy,sent});
    function post(message) {const frame=getFrame();if(!frame?.contentWindow) return false;frame.contentWindow.postMessage(message,'*');return true;}
    function baselineDescriptor(data) {const {text,...typography}=baseline;return {...data,text,position:initialPosition||data.position,typography:{...data.typography,...typography,...(baselinePixels?{font_size_px:baselinePixels}:{})}};}
    function settle() {if (!pending.size) for (const resolve of waiters.splice(0)) resolve();}
    const waiters=[];
    function waitPending(){return pending.size?new Promise(resolve=>waiters.push(resolve)):Promise.resolve();}
    function finish(){if(busy || dirty()){onError(busy?'Please wait for the edit to send.':'Send or cancel the edit first.');return false;}return true;}
    function inspect(target){
      if(!target || target.kind!=='element' || typeof target.selector!=='string' || !target.selector || target.selector.length>2000 || !finish())return false;
      const artifact=getArtifact();
      if(!artifact || typeof artifact.content_hash!=='string' || !artifact.id)return false;
      selection={target:JSON.parse(JSON.stringify(target)),content_hash:artifact.content_hash,revision:artifact.revision,artifact_id:artifact.id};
      history.clear();initialPosition=null;changes={};baseline=null;baselinePixels=null;descriptor=null;sent=false;requestId=null;generation++;
      clearTimeout(inspectTimer);inspectId=++sequence;
      if(!post({type:'artifact:inspect',selector:target.selector,request_id:inspectId})){selection=null;inspectId=null;state();return false;}
      const current=inspectId;
      inspectTimer=setTimeout(()=>{if(inspectId!==current)return;inspectId=null;selection=null;onError('The selected element did not respond. Select it again.');state();},5000);
      inspectTimer?.unref?.();state();return true;
    }
    function preview(values,record=true){
      if(!selection || !baseline || busy || !validPatch(values))return false;
      if(!('deleted' in values)&&(changes.deleted==='true'||[...pending.values()].some(item=>item.values.deleted==='true')))return false;
      const id=++sequence;
      pending.set(id,{values:{...values},generation,record});
      if(!post({type:'artifact:edit-preview',selector:selection.target.selector,values,request_id:id})){pending.delete(id);settle();return false;}
      sent=false;requestId=null;state();return true;
    }
    function receive(event){
      const frame=getFrame();
      if(!frame?.contentWindow || event.source!==frame.contentWindow || event.origin!=='null' || !event.data || typeof event.data!=='object')return false;
      const data=event.data;
      if(data.type==='artifact:edit-error'){
        if(data.request_id===inspectId && inspectId!==null){clearTimeout(inspectTimer);inspectId=null;selection=null;state();}
        else {const item=pending.get(data.request_id);if(!item || item.generation!==generation)return false;pending.delete(data.request_id);settle();state();}
        onError(typeof data.message==='string'?data.message.slice(0,500):'Could not preview this edit.');return true;
      }
      if(data.type!=='artifact:inspector' || !selection || data.selector!==selection.target.selector)return false;
      const initial=data.request_id===inspectId && inspectId!==null;
      const item=pending.get(data.request_id);
      if(!initial && (!item || item.generation!==generation))return false;
      if(typeof data.text!=='string' || data.text.length>9000 || typeof data.canText!=='boolean' || !data.typography || typeof data.typography!=='object')return false;
      if(initial){
        clearTimeout(inspectTimer);inspectId=null;
        const typography={};
        for(const key of properties){const value=data.typography[key];if(key!=='text' && ((typeof value==='string' && value.length<=100) || (typeof value==='number' && Number.isFinite(value))))typography[key]=value;}
        baseline={text:data.text,...typography};initialPosition=data.position||null;
        baselinePixels=Number.isFinite(data.typography.font_size_px) && data.typography.font_size_px>0?data.typography.font_size_px:null;
        selection.target.state=JSON.stringify({typography});
      } else {
        pending.delete(data.request_id);
        const before={...changes};Object.assign(changes,item.values);
        for(const key of Object.keys(changes)){
          if(key==='font_size' || key==='font_size_unit')continue;
          if(changes[key]===baseline[key])delete changes[key];
        }
        if(changes.font_size===baseline.font_size && changes.font_size_unit===baseline.font_size_unit){delete changes.font_size;delete changes.font_size_unit;}
        if(changes.deleted==='false')delete changes.deleted;
        for(const key of ['x','y'])if(initialPosition&&Math.abs(changes[key]-initialPosition[key])<1e-9)delete changes[key];
        if(!('x' in changes)&&!('y' in changes)){delete changes.position_reference;delete changes.canvas_reference;delete changes.canvas_width;delete changes.canvas_height;}
        if(item.record)history.record(before,changes);
        settle();
      }
      descriptor=data;
      if(!initial && !dirty()){post({type:'artifact:edit-cancel'});descriptor=baselineDescriptor(data);}
      onInspector({...descriptor,restoreControls:item?.record===false});state();return true;
    }
    async function send(){
      if(!selection || busy)return false;
      busy=true;state();
      try{
        let timeout;
        try { await Promise.race([waitPending(),new Promise((_,reject)=>{timeout=setTimeout(()=>reject(Error('Preview did not respond. Cancel or retry after it loads.')),5000);})]); }
        finally { clearTimeout(timeout); }
        if(!Object.keys(changes).length)return false;
        const deleted=changes.deleted==='true',moving='x' in changes||'y' in changes,text=Object.hasOwn(changes,'text'),style=Object.keys(changes).some(key=>key!=='text'&&!placements.has(key));
        const action=deleted?'delete':moving?(text||style?'update':'move'):text&&style?'update':text?'replace_text':'style';
        if(!requestId)requestId=root.crypto.randomUUID();
        const body={message:action==='delete'?'Delete this element.':action==='move'?'Move this element.':action==='replace_text'?'Replace text.':action==='style'?'Update element style.':'Update element text and style.',request_id:requestId,content_hash:selection.content_hash,target:selection.target,edit_intent:{action,values:deleted?{}:{...changes}}};
        await onSend(body);
        history.clear();post({type:'artifact:edit-accept'});for(const key of Object.keys(changes))if(properties.has(key))baseline[key]=changes[key];if(descriptor?.position)initialPosition=descriptor.position;baselinePixels=descriptor?.typography.font_size_px||baselinePixels;changes={};requestId=null;sent=true;
        if(descriptor)onInspector(baselineDescriptor(descriptor));return true;
      }catch(error){onError(error?.message || 'Could not send the edit.');return false;}
      finally{busy=false;state();}
    }
    function cancel(){
      if(busy)return false;
      history.clear();generation++;pending.clear();settle();changes={};requestId=null;sent=false;
      post({type:'artifact:edit-cancel'});if(descriptor && baseline){descriptor=baselineDescriptor(descriptor);onInspector(descriptor);}state();return true;
    }
    function reset(){if(!finish())return false;history.clear();clearTimeout(inspectTimer);inspectId=null;selection=null;baseline=null;baselinePixels=null;descriptor=null;changes={};sent=false;requestId=null;generation++;state();return true;}
    async function drainPreview(){let timer;try{await Promise.race([waitPending(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Wait for the preview, then try Undo again.')),5000);timer?.unref?.();})]);}finally{clearTimeout(timer);}}
    async function stepHistory(redo=false){
      if(!selection||busy)return false;busy=true;state();
      try{
        await drainPreview();
        const values=redo?history.redo():history.undo();if(!values)return false;
        generation++;changes={};requestId=null;sent=false;post({type:'artifact:edit-cancel'});
        if(Object.keys(values).length){busy=false;preview(values,false);busy=true;await drainPreview();}
        else if(descriptor){descriptor=baselineDescriptor(descriptor);onInspector({...descriptor,restoreControls:true});}
        return true;
      }catch(error){onError(error.message);return false;}
      finally{busy=false;state();}
    }
    function nudge(key,step=1){
      const position=descriptor?.position?{...descriptor.position}:null,directions={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]},direction=directions[key];
      if(!position||!direction||!['x','y','width','height','canvas_width','canvas_height'].every(key=>Number.isFinite(position[key]))||position.canvas_width<=0||position.canvas_height<=0)return false;
      for(const item of pending.values()){if('x' in item.values)position.x=item.values.x;if('y' in item.values)position.y=item.values.y;}
      return preview({x:Math.max(0,Math.min(Math.max(0,1-position.width),position.x+direction[0]*step/position.canvas_width)),y:Math.max(0,Math.min(Math.max(0,1-position.height),position.y+direction[1]*step/position.canvas_height)),position_reference:'visual_top_left',canvas_reference:'artifact_document',canvas_width:position.canvas_width,canvas_height:position.canvas_height});
    }
    return {undo:()=>stepHistory(),redo:()=>stepHistory(true),canUndo:history.canUndo,canRedo:history.canRedo,delete:()=>preview({deleted:'true'}),nudge,hasSelection:()=>!!selection,inspect,preview,send,cancel,receive,finish,isEditing:dirty,isBusy:()=>busy,canEdit:()=>descriptor?.canText===true,ready:()=>!!selection&&!!baseline,reset};
  }
  const api={create,validPatch};
  if(typeof module!=='undefined' && module.exports)module.exports=api;
  root.ArtifactEditor=api;
})(typeof window!=='undefined'?window:globalThis);
