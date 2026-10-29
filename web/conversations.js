/* Slide-local conversations, including recovery of older deck-wide history and drafts. */
((root)=>{
  'use strict';
  /** Keep a legacy reply beside the slide where its original request was made. */
  function forSlide(deck,slideId){
    const all=[...(deck.feedback||[]),...deck.slides.flatMap(slide=>slide.feedback||[])];
    const byId=new Map(all.map(item=>[item.event_id,item]));
    function context(item,visited=new Set()){
      if(item.slide_id||item.context_slide_id)return item.slide_id||item.context_slide_id;
      if(item.reply_to&&byId.has(item.reply_to)&&!visited.has(item.event_id)){
        visited.add(item.event_id);return context(byId.get(item.reply_to),visited);
      }
      return deck.slides[0]?.id;
    }
    return [...byId.values()].filter(item=>context(item)===slideId).sort((a,b)=>a.event_id-b.event_id);
  }
  /** Recover the old shared composer onto its recorded slide without changing its revision anchor. */
  function migrateDraft(drafts,deck){
    const legacy=drafts.get(deck.id);if(!legacy)return false;
    const requested=legacy.slide_id||legacy.context_slide_id;
    const slideId=deck.slides.some(slide=>slide.id===requested)?requested:deck.slides[0]?.id;
    if(!slideId||drafts.has(`${deck.id}/${slideId}`))return false;
    drafts.set(`${deck.id}/${slideId}`,{...legacy,slide_id:slideId,context_slide_id:legacy.context_slide_id||slideId});drafts.delete(deck.id);return true;
  }
  const api={forSlide,migrateDraft};
  if(typeof module!=='undefined')module.exports=api;else root.SlideConversations=api;
})(typeof window==='undefined'?{}:window);
