/* Host-side controller for revision-bound, provisional sandbox edits. */
((root) => {
  'use strict';
  const properties = new Set(['text','font_size','font_size_unit','font_weight','font_style','text_align','color']);
  const units = new Set(['px','pt','em','rem','%','vw','vh','cqw','cqh']);
  function validPatch(values) {
    if (!values || typeof values !== 'object' || Array.isArray(values)) return false;
    const keys=Object.keys(values);
    if (!keys.length || keys.some(key=>!properties.has(key))) return false;
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
    const dirty=()=>Object.keys(changes).length>0 || pending.size>0;
    const state=()=>onState({dirty:dirty(),busy,sent});
    function post(message) {const frame=getFrame();if(!frame?.contentWindow) return false;frame.contentWindow.postMessage(message,'*');return true;}
    function baselineDescriptor(data) {const {text,...typography}=baseline;return {...data,text,typography:{...data.typography,...typography,...(baselinePixels?{font_size_px:baselinePixels}:{})}};}
    function settle() {if (!pending.size) for (const resolve of waiters.splice(0)) resolve();}
    const waiters=[];
    function waitPending(){return pending.size?new Promise(resolve=>waiters.push(resolve)):Promise.resolve();}
    function finish(){if(busy || dirty()){onError(busy?'Please wait for the edit to send.':'Send or cancel the edit first.');return false;}return true;}
    function inspect(target){
      if(!target || target.kind!=='element' || typeof target.selector!=='string' || !target.selector || target.selector.length>2000 || !finish())return false;
      const artifact=getArtifact();
      if(!artifact || typeof artifact.content_hash!=='string' || !artifact.id)return false;
      selection={target:JSON.parse(JSON.stringify(target)),content_hash:artifact.content_hash,revision:artifact.revision,artifact_id:artifact.id};
      changes={};baseline=null;baselinePixels=null;descriptor=null;sent=false;requestId=null;generation++;
      clearTimeout(inspectTimer);inspectId=++sequence;
      if(!post({type:'artifact:inspect',selector:target.selector,request_id:inspectId})){selection=null;inspectId=null;state();return false;}
      const current=inspectId;
      inspectTimer=setTimeout(()=>{if(inspectId!==current)return;inspectId=null;selection=null;onError('The selected element did not respond. Select it again.');state();},5000);
      inspectTimer?.unref?.();state();return true;
    }
    function preview(values){
      if(!selection || !baseline || busy || !validPatch(values))return false;
      const id=++sequence;
      pending.set(id,{values:{...values},generation});
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
        baseline={text:data.text,...typography};
        baselinePixels=Number.isFinite(data.typography.font_size_px) && data.typography.font_size_px>0?data.typography.font_size_px:null;
        selection.target.state=JSON.stringify({typography});
      } else {
        pending.delete(data.request_id);
        Object.assign(changes,item.values);
        for(const key of Object.keys(changes)){
          if(key==='font_size' || key==='font_size_unit')continue;
          if(changes[key]===baseline[key])delete changes[key];
        }
        if(changes.font_size===baseline.font_size && changes.font_size_unit===baseline.font_size_unit){delete changes.font_size;delete changes.font_size_unit;}
        settle();
      }
      descriptor=data;
      if(!initial && !dirty()){post({type:'artifact:edit-cancel'});descriptor=baselineDescriptor(data);}
      onInspector(descriptor);state();return true;
    }
    async function send(){
      if(!selection || busy)return false;
      busy=true;state();
      try{
        let timeout;
        try { await Promise.race([waitPending(),new Promise((_,reject)=>{timeout=setTimeout(()=>reject(Error('Preview did not respond. Cancel or retry after it loads.')),5000);})]); }
        finally { clearTimeout(timeout); }
        if(!Object.keys(changes).length)return false;
        const text=Object.hasOwn(changes,'text'),style=Object.keys(changes).some(key=>key!=='text');
        const action=text&&style?'update':text?'replace_text':'style';
        if(!requestId)requestId=root.crypto.randomUUID();
        const body={message:action==='replace_text'?'Replace text.':action==='style'?'Update element style.':'Update element text and style.',request_id:requestId,content_hash:selection.content_hash,target:selection.target,edit_intent:{action,values:{...changes}}};
        await onSend(body);
        post({type:'artifact:edit-accept'});Object.assign(baseline,changes);baselinePixels=descriptor?.typography.font_size_px||baselinePixels;changes={};requestId=null;sent=true;
        if(descriptor)onInspector(baselineDescriptor(descriptor));return true;
      }catch(error){onError(error?.message || 'Could not send the edit.');return false;}
      finally{busy=false;state();}
    }
    function cancel(){
      if(busy)return false;
      generation++;pending.clear();settle();changes={};requestId=null;sent=false;
      post({type:'artifact:edit-cancel'});if(descriptor && baseline)onInspector(baselineDescriptor(descriptor));state();return true;
    }
    function reset(){if(!finish())return false;clearTimeout(inspectTimer);inspectId=null;selection=null;baseline=null;baselinePixels=null;descriptor=null;changes={};sent=false;requestId=null;generation++;state();return true;}
    return {inspect,preview,send,cancel,receive,finish,isEditing:dirty,isBusy:()=>busy,reset};
  }
  const api={create,validPatch};
  if(typeof module!=='undefined' && module.exports)module.exports=api;
  root.ArtifactEditor=api;
})(typeof window!=='undefined'?window:globalThis);
