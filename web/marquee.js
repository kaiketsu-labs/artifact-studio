/* Shared canvas marquee; selection remains local until feedback is explicitly sent. */
((root)=>{
  'use strict';
  const objectSelector='[data-element-id],h1,h2,h3,h4,h5,h6,p,li,img,svg,canvas,video,[role="img"]';
  function rectangle(start,end){return {left:Math.min(start.x,end.x),top:Math.min(start.y,end.y),right:Math.max(start.x,end.x),bottom:Math.max(start.y,end.y)};}
  function enclosed(area,box){return box.width>0&&box.height>0&&box.left>=area.left-.5&&box.top>=area.top-.5&&box.right<=area.right+.5&&box.bottom<=area.bottom+.5;}
  function roots(elements){return elements.filter(element=>!elements.some(other=>other!==element&&other.contains(element))).slice(0,100);}
  /** Retain semantic objects and explicit author groups without selecting SVG internals twice. */
  function objects(doc,scope=doc.body){
    return [...scope.querySelectorAll(objectSelector)].filter(element=>{
      if(element.closest('[data-review-chrome],[data-artifact-bridge],[hidden],[aria-hidden="true"]'))return false;
      if(element.closest('svg')!==null&&element.localName!=='svg'&&!element.hasAttribute('data-element-id'))return false;
      const style=doc.defaultView.getComputedStyle(element),box=element.getBoundingClientRect();
      return style.display!=='none'&&style.visibility!=='hidden'&&box.width>0&&box.height>0;
    });
  }
  /** Add a drag rectangle without moving objects, stealing text edits or changing source HTML. */
  function create({doc,candidates=()=>objects(doc),enabled=()=>true,onSelect=()=>{},onActivity=()=>{},onError=()=>{}}){
    const view=doc.defaultView;let gesture=null,overlay=null,suppressed=false,frame=null;
    function draw(){frame=null;if(!gesture?.dragging||!overlay)return;const box=rectangle(gesture.start,gesture.end);overlay.style.left=`${box.left}px`;overlay.style.top=`${box.top}px`;overlay.style.width=`${box.right-box.left}px`;overlay.style.height=`${box.bottom-box.top}px`;}
    function stop(commit=false){
      if(!gesture)return;
      const saved=gesture;gesture=null;
      if(frame!==null){root.cancelAnimationFrame(frame);frame=null;}
      overlay?.remove();overlay=null;
      if(doc.documentElement.hasPointerCapture?.(saved.pointer))doc.documentElement.releasePointerCapture(saved.pointer);
      if(saved.dragging){suppressed=true;onActivity(false);if(commit){const area=rectangle(saved.start,saved.end),selected=roots(saved.elements.filter(element=>element.isConnected&&enclosed(area,element.getBoundingClientRect())));Promise.resolve(onSelect(selected,saved.additive)).catch(error=>onError(error.message));}}
    }
    function down(event){
      if(!enabled()||gesture||event.button!==0||event.isPrimary===false||event.pointerType==='touch'||event.altKey||event.target.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"])'))return;
      suppressed=false;
      const elements=candidates();if(elements.some(element=>element===event.target||element.contains(event.target)))return;
      event.preventDefault();event.stopImmediatePropagation();
      gesture={pointer:event.pointerId,start:{x:event.clientX,y:event.clientY},end:{x:event.clientX,y:event.clientY},elements,additive:!!(event.shiftKey||event.metaKey||event.ctrlKey),dragging:false};
    }
    function move(event){
      if(!gesture||gesture.pointer!==event.pointerId)return;
      if(!enabled()){stop();return;}
      gesture.end={x:event.clientX,y:event.clientY};
      if(!gesture.dragging&&Math.hypot(event.clientX-gesture.start.x,event.clientY-gesture.start.y)<5)return;
      event.preventDefault();event.stopImmediatePropagation();
      if(!gesture.dragging){
        gesture.dragging=true;onActivity(true);doc.getSelection()?.removeAllRanges();
        overlay=doc.createElement('div');overlay.setAttribute('data-review-chrome','');overlay.setAttribute('aria-hidden','true');
        overlay.style.cssText='position:fixed;pointer-events:none;box-sizing:border-box;border:1px solid #4777e8;background:#4777e81a;z-index:2147483647;';doc.documentElement.append(overlay);
        doc.documentElement.setPointerCapture?.(event.pointerId);
      }
      // The host schedules paint because static slide frames deliberately disable scripts.
      if(frame===null)frame=root.requestAnimationFrame(draw);
    }
    function up(event){if(!gesture||gesture.pointer!==event.pointerId)return;gesture.end={x:event.clientX,y:event.clientY};if(gesture.dragging){event.preventDefault();event.stopImmediatePropagation();}stop(true);}
    function click(event){if(suppressed){suppressed=false;event.preventDefault();event.stopImmediatePropagation();}}
    function key(event){if(gesture&&event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();stop();}}
    const cancel=()=>stop();
    const bindings=[['pointerdown',down],['pointermove',move],['pointerup',up],['click',click],['keydown',key],['pointercancel',cancel],['blur',cancel],['scroll',cancel]];
    for(const [name,handler] of bindings)view.addEventListener(name,handler,{capture:true,passive:false});
    return {active:()=>!!gesture?.dragging,cancel,destroy(){stop();for(const [name,handler] of bindings)view.removeEventListener(name,handler,true);}};
  }
  const api={rectangle,enclosed,roots,objects,create};if(typeof module!=='undefined')module.exports=api;else root.MarqueeSelection=api;
})(typeof window==='undefined'?{}:window);
