/* Serial, revision-guarded speaker-note saves with per-slide recovery. */
((root)=>{
  'use strict';
  function create({write,read=null,change=()=>{},saved=()=>{},delay=700,setTimer=setTimeout,clearTimer=clearTimeout}){
    const entries=new Map();
    let revision=0;
    const dirty=entry=>entry.value!==entry.expected||!!entry.sending||entry.force||['error','conflict'].includes(entry.phase);
    function state(key){
      const entry=entries.get(key);if(!entry)return null;
      // A close while a request is in flight must preserve a later edit or reversal.
      const pending=entry.sending?.notes??entry.pending;
      return {value:entry.value,expected:entry.expected,phase:entry.phase,error:entry.error,
        dirty:dirty(entry),draft:dirty(entry)?{notes:entry.value,expected_notes:entry.expected,
          ...(Number.isInteger(entry.expectedRevision)?{expected_revision:entry.expectedRevision}:{}),
          ...(pending!==null?{pending_notes:pending}:{})}:null};
    }
    const notify=key=>change(key,state(key));
    function cancel(entry){if(entry.timer!==null)clearTimer(entry.timer);entry.timer=null;}
    function schedule(key){
      const entry=entries.get(key);cancel(entry);
      if(!dirty(entry)||['conflict','error'].includes(entry.phase))return;
      entry.timer=setTimer(()=>{entry.timer=null;void flush(key);},delay);
    }
    /** Observe canonical values without replacing a dirty draft or an in-flight save. */
    function observe(key,value,draft=null,version=null){
      let entry=entries.get(key);
      if(!entry){
        const pending=typeof draft?.pending_notes==='string'?draft.pending_notes:null;
        entry={value:draft?.notes??value,expected:draft?.expected_notes??value,
          expectedRevision:draft?.expected_revision??version,pending,
          force:pending!==null&&draft.notes===draft.expected_notes&&value===draft.expected_notes,
          phase:'saved',error:'',timer:null,promise:null,sending:null};
        entries.set(key,entry);
      }
      if(!entry.promise){
        if(entry.force&&Number.isInteger(version)&&version>entry.expectedRevision&&value===entry.value)entry.force=false;
        if(entry.pending!==null&&value===entry.pending){
          entry.expected=value;entry.expectedRevision=version;entry.pending=null;entry.force=false;
          entry.phase=entry.value===value?'saved':'pending';entry.error='';
        }
        if((!dirty(entry)||value===entry.value)&&!entry.force){
          cancel(entry);entry.value=value;entry.expected=value;entry.expectedRevision=version;entry.pending=null;entry.phase='saved';entry.error='';
        }else if(value!==entry.expected){
          cancel(entry);entry.phase='conflict';entry.error='Notes changed in another window. Your draft is kept here.';
        }else if(!['error','conflict'].includes(entry.phase)){entry.phase='pending';if(entry.timer===null)schedule(key);}
      }
      notify(key);return state(key);
    }
    function edit(key,value){
      const entry=entries.get(key);if(!entry)return;
      entry.value=value;
      if((entry.sending||entry.pending!==null)&&value===entry.expected)entry.force=true;
      if(entry.phase!=='conflict'){
        entry.error='';entry.phase=entry.promise?'saving':dirty(entry)?'pending':'saved';
      }
      schedule(key);notify(key);
    }
    /** Capture the key and baseline once; subsequent edits queue behind this request. */
    function flush(key,{retry=false}={}){
      const entry=entries.get(key);if(!entry)return Promise.resolve(true);
      cancel(entry);
      if(entry.promise)return entry.promise.then(ok=>ok?flush(key):false);
      if(entry.phase==='conflict'||entry.phase==='error'&&!retry)return Promise.resolve(false);
      if(!dirty(entry)){entry.phase='saved';entry.error='';notify(key);return Promise.resolve(true);}
      const body={notes:entry.value,expected_notes:entry.expected,
        ...(Number.isInteger(entry.expectedRevision)?{expected_revision:entry.expectedRevision}:{})};
      entry.sending=body;entry.phase='saving';entry.error='';
      entry.promise=Promise.resolve().then(async()=>{
        try{
          const receipt=await write(key,body);
          entry.expected=body.notes;entry.expectedRevision=receipt?.notes_revision??
            (Number.isInteger(body.expected_revision)?body.expected_revision+1:null);
          entry.pending=null;entry.force=false;entry.phase=entry.value===body.notes?'saved':'pending';
          revision++;saved(key,body.notes,entry.expectedRevision);return true;
        }catch(error){
          if(error.status===409&&entry.pending!==null&&read){
            try{
              const canonical=await read(key);
              if(canonical.notes===entry.pending){
                entry.expected=canonical.notes;entry.expectedRevision=canonical.notes_revision;
                entry.pending=null;entry.force=false;entry.phase=entry.value===canonical.notes?'saved':'pending';entry.error='';
                revision++;saved(key,canonical.notes,canonical.notes_revision);return true;
              }
            }catch{}
          }
          entry.pending??=body.notes;
          entry.phase=error.status===409?'conflict':'error';
          entry.error=error.status===409?'Notes changed in another window. Your draft is kept here.':
            'Could not save notes. Your draft is kept in this browser. Retry when connected.';
          return false;
        }finally{
          entry.promise=null;entry.sending=null;notify(key);
          if(entry.phase==='pending')schedule(key);
        }
      });
      notify(key);return entry.promise;
    }
    /** Apply an explicit conflict choice against the canonical text the human just reviewed. */
    function resolve(key,canonical,keepDraft,version=null){
      const entry=entries.get(key);if(!entry||entry.promise)return Promise.resolve(false);
      cancel(entry);entry.expected=canonical;entry.expectedRevision=version;entry.error='';
      entry.force=keepDraft&&Number.isInteger(version);entry.pending=null;
      if(!keepDraft)entry.value=canonical;
      entry.phase=entry.value===entry.expected&&!entry.force?'saved':'pending';
      revision++;notify(key);return flush(key);
    }
    function flushAll(prefix='',options={}){
      return Promise.all([...entries.keys()].filter(key=>key.startsWith(prefix)).map(async key=>{
        do{if(!await flush(key,options))return false;}while(state(key).dirty);
        return true;
      }))
        .then(results=>results.every(Boolean));
    }
    function recover(key,value,draft,version=null){
      const previous=entries.get(key);if(previous&&dirty(previous))return false;
      if(previous)cancel(previous);entries.delete(key);observe(key,value,draft,version);return true;
    }
    return {observe,edit,state,flush,flushAll,resolve,recover,get revision(){return revision;},
      status:()=>[...entries.values()].some(entry=>['error','conflict'].includes(entry.phase))?'error':
        [...entries.values()].some(dirty)?'pending':'saved',
      firstFailed:()=>[...entries].find(([,entry])=>['error','conflict'].includes(entry.phase))?.[0],
      failed:()=>[...entries.values()].some(entry=>['error','conflict'].includes(entry.phase))};
  }
  const api={create};if(typeof module!=='undefined')module.exports=api;else root.SlideNotesAutosave=api;
})(typeof window==='undefined'?{}:window);
