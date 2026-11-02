/* Durable, local drafts shared by the workspace and its reload-recovery checks. */
((root) => {
  'use strict';
  const {validTarget}=typeof module!=='undefined'?require('./selection.js'):root.SlideSelection;
  const validEntries=(entries,valid)=>Array.isArray(entries)?entries.filter(entry=>Array.isArray(entry)&&entry.length===2&&typeof entry[0]==='string'&&valid(entry[1])):[];
  const validNotes=value=>value&&typeof value==='object'&&(value.notes===undefined||typeof value.notes==='string')&&(value.expected_notes===undefined||typeof value.expected_notes==='string');
  const validFeedback=value=>value&&typeof value.feedback==='string'&&(!value.target||validTarget(value.target));
  /** Recover drafts without allowing corrupt browser storage to prevent startup. */
  function create(storage,key='artifact-studio:drafts:v1'){
    const prefix=key.replace(/:v1$/,':v2')+':';
    const notes=new Map(),feedback=new Map();
    const groups={notes,feedback},validators={notes:validNotes,feedback:validFeedback};
    const baseline={notes:new Map(),feedback:new Map()};
    const itemKey=(kind,id)=>prefix+kind+':'+encodeURIComponent(id);
    try{
      const legacy=JSON.parse(storage.getItem(key)||'{}');
      if(legacy?.version===1){
        for(const kind of ['notes','feedback'])for(const [id,value] of validEntries(legacy[kind],validators[kind])){
          groups[kind].set(id,value);
          if(storage.getItem(itemKey(kind,id))===null)storage.setItem(itemKey(kind,id),JSON.stringify({kind,id,value}));
        }
        storage.removeItem(key);
      }
    }catch{}
    try{
      for(const name of storage.keys()){
        if(!name.startsWith(prefix))continue;
        try{
          const entry=JSON.parse(storage.getItem(name));
          if(['notes','feedback'].includes(entry?.kind)&&typeof entry.id==='string'&&name===itemKey(entry.kind,entry.id)&&validators[entry.kind](entry.value)){groups[entry.kind].set(entry.id,entry.value);baseline[entry.kind].set(entry.id,JSON.stringify(entry.value));}
        }catch{}
      }
    }catch{}
    /** Save only unfinished work; original revision context travels with the draft. */
    function persist(){
      try{
        storage.getItem(key);
        for(const kind of ['notes','feedback']){
          const current=new Map([...groups[kind]].filter(([,value])=>kind==='notes'?value.notes!==undefined:value.feedback.trim()||value.target).map(([id,value])=>[id,JSON.stringify(value)]));
          for(const id of new Set([...baseline[kind].keys(),...current.keys()])){
            const value=current.get(id);
            if(value===baseline[kind].get(id))continue;
            if(value===undefined){storage.removeItem(itemKey(kind,id));baseline[kind].delete(id);}
            else{storage.setItem(itemKey(kind,id),JSON.stringify({kind,id,value:JSON.parse(value)}));baseline[kind].set(id,value);}
          }
        }
        return true;
      }catch{return false;}
    }
    return {notes,feedback,persist};
  }
  if(typeof module!=='undefined')module.exports={create};else root.SlideDrafts={create};
})(typeof window==='undefined'?{}:window);
