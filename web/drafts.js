/* Durable, local drafts shared by the workspace and its reload-recovery checks. */
((root) => {
  'use strict';
  const validEntries=(entries,valid)=>Array.isArray(entries)?entries.filter(entry=>Array.isArray(entry)&&entry.length===2&&typeof entry[0]==='string'&&valid(entry[1])):[];
  const validNotes=value=>value&&typeof value==='object'&&(value.notes===undefined||typeof value.notes==='string')&&(value.expected_notes===undefined||typeof value.expected_notes==='string');
  const validFeedback=value=>value&&typeof value.feedback==='string'&&(!value.target||(typeof value.target==='object'&&typeof value.target.selector==='string'&&typeof value.target.label==='string'));
  /** Recover drafts without allowing corrupt browser storage to prevent startup. */
  function create(storage,key='slide-studio:drafts:v1'){
    let saved={};
    try{const parsed=JSON.parse(storage.getItem(key)||'{}');if(parsed?.version===1)saved=parsed;}catch{}
    const notes=new Map(validEntries(saved.notes,validNotes));
    const feedback=new Map(validEntries(saved.feedback,validFeedback));
    /** Save only unfinished work; original revision context travels with the draft. */
    function persist(){
      const document={version:1,notes:[...notes].filter(([,value])=>value.notes!==undefined),feedback:[...feedback].filter(([,value])=>value.feedback.trim()||value.target)};
      try{if(document.notes.length||document.feedback.length)storage.setItem(key,JSON.stringify(document));else storage.removeItem(key);return true;}catch{return false;}
    }
    return {notes,feedback,persist};
  }
  if(typeof module!=='undefined')module.exports={create};else root.SlideDrafts={create};
})(typeof window==='undefined'?{}:window);
