/* Retain a loaded design between slides without retaining stale slide content. */
((root)=>{
  'use strict';
  /** Load a new preview or replace only its slide when the shared design is unchanged. */
  function create({documentFor,onStart=()=>{},onReady=()=>{},onFailure=()=>{},schedule=setTimeout,cancel=clearTimeout}){
    const states=new WeakMap();
    function invalidate(frame){
      const previous=states.get(frame);cancel(previous?.timer);cancel(previous?.retry);
      states.delete(frame);frame.onload=null;
    }
    function load(frame,item,deckId,designHash,attempt=0){
      const version=`${deckId}/${item.id}/${item.content_hash}/${designHash}`;
      const previous=states.get(frame);
      if(!attempt&&previous?.version===version&&(previous.ready||previous.pending))return;
      invalidate(frame);
      const design=`${deckId}/${designHash}`,doc=frame.contentDocument;
      const reused=!attempt&&previous?.designReady&&previous.design===design&&previous.doc===doc;
      const state={version,design,doc:reused?doc:null,designReady:!!reused,ready:false,pending:true,timer:null,retry:null};
      states.set(frame,state);
      const current=()=>frame.isConnected&&states.get(frame)===state;
      const fail=()=>{
        if(!current()||!state.pending)return;
        state.pending=false;cancel(state.timer);frame.onload=null;frame.style.visibility='hidden';
        if(!attempt){state.retry=schedule(()=>{if(current())load(frame,item,deckId,designHash,1);},500);return;}
        onFailure(frame);
      };
      const ready=async()=>{
        if(!current()||!state.pending)return;
        const document=frame.contentDocument;
        if(!document||document.body?.dataset.previewKey!==item.content_hash)return;
        state.doc=document;
        try{
          const stylesheet=document.querySelector('link[rel="stylesheet"]');
          if(!stylesheet?.sheet?.cssRules.length){fail();return;}
          state.designReady=true;
          // Layout starts fonts newly needed by the replacement slide before fonts.ready is read.
          void document.body.offsetWidth;
          const svgImages=[...document.querySelectorAll('svg image')].flatMap(node=>{
            const source=node.getAttribute('href')||node.getAttribute('xlink:href');
            if(!source||source.startsWith('#'))return [];
            const image=document.createElement('img');image.src=source;return [image];
          });
          await Promise.all([document.fonts.ready,...[...document.images,...svgImages].map(image=>image.decode())]);
          if(!current()||!state.pending)return;
          if([...document.fonts].some(font=>font.status==='error')){fail();return;}
          frame.dataset.loadToken=version+'-ready';
          frame.style.visibility='visible';
          onReady(frame,document);
          if(!current())return;
          cancel(state.timer);state.ready=true;state.pending=false;frame.onload=null;
        }catch{fail();}
      };
      frame.dataset.loadToken=version;frame.style.visibility='hidden';
      onStart(frame,!!reused);
      state.timer=schedule(fail,10000);
      if(reused){
        doc.body.dataset.previewKey=item.content_hash;doc.body.innerHTML=item.html;
        void ready();
      }else{
        frame.onload=ready;
        frame.srcdoc=documentFor(item,deckId,designHash,attempt);
      }
    }
    return {load,cancel:invalidate};
  }
  const api={create};
  if(typeof module!=='undefined')module.exports=api;else root.SlidePreviewLoader=api;
})(typeof window==='undefined'?{}:window);
