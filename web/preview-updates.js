/* One browser preference for applying incoming artifact previews. */
((root)=>{
  'use strict';
  const key='artifact-studio:auto-update-previews';
  /** Remember the choice while keeping unavailable browser storage harmless. */
  function create({storage,events=root,onChange=()=>{}}={}){
    let enabled=true;
    try{storage??=root.localStorage;enabled=storage.getItem(key)!=='false';}catch{}
    function set(value){enabled=!!value;try{storage.setItem(key,String(enabled));}catch{}onChange(enabled);}
    function changed(event){if(event.key===key){enabled=event.newValue!=='false';onChange(enabled);}}
    events?.addEventListener?.('storage',changed);
    return {get enabled(){return enabled;},set,stop:()=>events?.removeEventListener?.('storage',changed)};
  }
  const api={create};if(typeof module!=='undefined')module.exports=api;else root.SlidePreviewUpdates=api;
})(typeof window==='undefined'?{}:window);
