/** A revision can change only after explicit review, and never for anchored edits. */
(() => {
  'use strict';
  function state(draft,item,record,voiceSegments=[],hasFiles=false){
    const active=!!draft&&(!!draft.feedback?.trim()||!!draft.target||hasFiles||!!draft.voice_segments?.length||!!voiceSegments.length);
    const stale=active&&!!item&&(draft.content_hash!==item.content_hash||!!record?.conflict);
    const safe=!!draft&&!draft.target&&!draft.design_option_id&&!draft.element_option_id&&!draft.edit_intent&&!draft.voice_segments?.length&&!voiceSegments.length;
    const reviewed=!!record?.hash&&record.hash===item?.content_hash&&record.requestId===draft?.request_id;
    return {draft,item,stale,safe,reviewed};
  }
  const api={state};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  if(typeof window!=='undefined')window.SlideDraftRecovery=api;
})();
