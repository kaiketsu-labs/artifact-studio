/* Final speech results remain attached to the slide/version/objects captured at their start. */
((root)=>{
  'use strict';
  const MAX_SEGMENTS=100,MAX_MESSAGE=10000;
  const fields=['request_id','message','slide_id','context_slide_id','content_hash','target','design_option_id','design_option_name'];
  const captureFields=['key','request_id','slide_id','context_slide_id','content_hash','target','design_option_id','design_option_name'];
  const targetFields=['selector','label','quote','html','x','y','width','height'];
  const text=(value,max)=>typeof value==='string'&&value.length<=max;
  const identifier=value=>typeof value==='string'&&/^[a-z0-9][a-z0-9-]{0,79}$/.test(value);
  const exact=(value,allowed)=>value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).every(name=>allowed.includes(name));
  const requestId=value=>typeof value==='string'&&value.length>=8&&value.length<=100;
  const coordinate=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0&&value<=1;
  /** Validate bounded snapshots independently of live preview elements. */
  function validElement(value){return exact(value,targetFields)&&text(value.selector,2000)&&text(value.label,200)&&(value.quote===undefined||text(value.quote,2000))&&(value.html===undefined||text(value.html,15000))&&['x','y','width','height'].every(name=>coordinate(value[name]));}
  function validVoiceTarget(value){return value===null||validElement(value)||exact(value,['kind','label','elements'])&&value.kind==='group'&&text(value.label,200)&&Array.isArray(value.elements)&&value.elements.length>0&&value.elements.length<=100&&value.elements.every(validElement);}
  function validContext(value){return requestId(value.request_id)&&identifier(value.slide_id)&&(value.context_slide_id===null||identifier(value.context_slide_id))&&text(value.content_hash,128)&&validVoiceTarget(value.target)&&(value.design_option_id===undefined||typeof value.design_option_id==='string'&&/^[a-f0-9]{32}$/.test(value.design_option_id)&&text(value.design_option_name,60)&&value.target===null);}
  function validSegment(value){return exact(value,fields)&&validContext(value)&&text(value.message,MAX_MESSAGE);}
  function validSegments(values){return Array.isArray(values)&&values.length<=MAX_SEGMENTS&&values.every(validSegment)&&new Set(values.map(value=>value.request_id)).size===values.length;}
  function freeze(value){if(value&&typeof value==='object'){for(const item of Object.values(value))freeze(item);Object.freeze(value);}return value;}
  const copy=value=>structuredClone(value);
  const join=(previous,next)=>previous+(!previous||/\s$/.test(previous)||/^[,.;:!?，。！？、]/.test(next)?'':' ')+next;
  /** Queue normal comments locally; only the existing submit path sends them to the server. */
  function create({drafts,key,context,persist=()=>true,onChange=()=>{},uuid=()=>root.crypto.randomUUID()}){
    const retired=new Set();
    const identity=(draftKey,id)=>JSON.stringify([draftKey,id]);
    const raw=draftKey=>drafts.get(draftKey)?.voice_segments||[];
    const changed=draftKey=>{persist();onChange(draftKey);};
    function capture(){
      const draftKey=key(),current=context();
      if(typeof draftKey!=='string'||!draftKey||draftKey.length>210||!current)return null;
      const result={key:draftKey,request_id:uuid(),slide_id:current.slide_id,context_slide_id:current.context_slide_id??current.slide_id,content_hash:current.content_hash,target:copy(current.target||null),...(current.design_option_id?{design_option_id:current.design_option_id,design_option_name:current.design_option_name}:{})};
      return validContext(result)&&draftKey.endsWith('/'+result.slide_id)?freeze(result):null;
    }
    function available(snapshot){return exact(snapshot,captureFields)&&typeof snapshot.key==='string'&&snapshot.key.length>0&&snapshot.key.length<=210&&validContext(snapshot)&&snapshot.key.endsWith('/'+snapshot.slide_id)&&!retired.has(identity(snapshot.key,snapshot.request_id));}
    function insert(value,snapshot){
      if(!available(snapshot)||typeof value!=='string')return false;
      const addition=value.trim();if(!addition)return true;
      const draftKey=snapshot.key,existing=drafts.get(draftKey),current=raw(draftKey);
      if(!validSegments(current))return false;
      const index=current.findIndex(item=>item.request_id===snapshot.request_id),previous=index<0?'':current[index].message;
      if(index>=0&&['slide_id','context_slide_id','content_hash','target','design_option_id','design_option_name'].some(name=>JSON.stringify(current[index][name])!==JSON.stringify(snapshot[name])))return false;
      if(index<0&&current.length>=MAX_SEGMENTS)return false;
      const message=join(previous,addition);if(message.length>MAX_MESSAGE)return false;
      const segment=freeze({request_id:snapshot.request_id,message,slide_id:snapshot.slide_id,context_slide_id:snapshot.context_slide_id,content_hash:snapshot.content_hash,target:copy(snapshot.target),...(snapshot.design_option_id?{design_option_id:snapshot.design_option_id,design_option_name:snapshot.design_option_name}:{})});
      const next=[...current];if(index<0)next.push(segment);else next[index]=segment;
      drafts.set(draftKey,{...(existing||{feedback:'',slide_id:snapshot.slide_id,context_slide_id:snapshot.context_slide_id,content_hash:snapshot.content_hash,target:copy(snapshot.target),request_id:uuid(),...(snapshot.design_option_id?{design_option_id:snapshot.design_option_id,design_option_name:snapshot.design_option_name}:{})}),voice_segments:next});
      changed(draftKey);return true;
    }
    function edit(id,message,draftKey=key()){
      if(!text(message,MAX_MESSAGE))return false;
      const current=raw(draftKey),index=current.findIndex(item=>item.request_id===id);
      if(index<0)return false;
      if(current[index].message===message)return id;
      const request_id=uuid(),next=[...current];if(!requestId(request_id))return false;
      next[index]=freeze({...current[index],request_id,message});
      retired.add(identity(draftKey,id));drafts.set(draftKey,{...drafts.get(draftKey),voice_segments:next});changed(draftKey);return request_id;
    }
    function remove(id,draftKey=key()){
      retired.add(identity(draftKey,id));
      const current=raw(draftKey),next=current.filter(item=>item.request_id!==id);
      if(next.length===current.length)return false;
      drafts.set(draftKey,{...drafts.get(draftKey),voice_segments:next});changed(draftKey);return true;
    }
    function clear(draftKey=key()){for(const segment of raw(draftKey))retired.add(identity(draftKey,segment.request_id));if(drafts.has(draftKey)){drafts.set(draftKey,{...drafts.get(draftKey),voice_segments:[]});changed(draftKey);}}
    return {
      capture,available,insert,edit,remove,acknowledge:remove,clear,
      segments(draftKey=key()){const values=raw(draftKey);return validSegments(values)?copy(values):[];},
      hasDrafts(draftKey){return draftKey===undefined?[...drafts.values()].some(value=>value.voice_segments?.length):raw(draftKey).length>0;},
    };
  }
  const api={create,validSegments,MAX_SEGMENTS,MAX_MESSAGE};
  if(typeof module!=='undefined')module.exports=api;else root.SlideVoiceFeedback=api;
})(typeof window==='undefined'?globalThis:window);
