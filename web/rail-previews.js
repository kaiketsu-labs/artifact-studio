/* Keep only thumbnails near the scroll viewport as live documents. */
((root)=>{
  'use strict';
  function create({rail,load,cancel,Observer=root.IntersectionObserver}){
    let snapshot=null,items=new Map();
    const active=new Set();
    function release(slot){
      active.delete(slot);
      const frame=slot.querySelector('iframe');
      if(frame){cancel(frame);frame.remove();}
      delete slot.dataset.previewStatus;
    }
    function update(slot){
      const item=items.get(slot.dataset.slideId);
      if(!item||!snapshot)return;
      let frame=slot.querySelector('iframe');
      if(!frame){
        frame=rail.ownerDocument.createElement('iframe');
        frame.title=`Thumbnail ${item.index+1}`;frame.tabIndex=-1;
        frame.setAttribute('sandbox','allow-same-origin');slot.append(frame);
      }else frame.title=`Thumbnail ${item.index+1}`;
      load(frame,item.slide,snapshot.id,snapshot.design_hash);
    }
    const observer=new Observer(entries=>{
      for(const entry of entries){
        if(!rail.contains(entry.target))continue;
        if(entry.isIntersecting){active.add(entry.target);update(entry.target);}
        else release(entry.target);
      }
    },{root:rail,rootMargin:'200px 0px'});
    function reset(){observer.disconnect();for(const slot of active)release(slot);active.clear();snapshot=null;items.clear();}
    function sync(next,rebuild=false){
      snapshot=next;items=new Map(next.slides.map((slide,index)=>[slide.id,{slide,index}]));
      if(rebuild){for(const slot of rail.querySelectorAll('.thumb-preview'))observer.observe(slot);}
      else for(const slot of active)update(slot);
    }
    return {sync,reset};
  }
  const api={create};if(typeof module!=='undefined')module.exports=api;else root.SlideRailPreviews=api;
})(typeof window==='undefined'?{}:window);
