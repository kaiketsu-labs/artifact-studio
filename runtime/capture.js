/* Deterministic slide-content capture; loaded only in isolated renderer documents. */
(()=>{
  'use strict';
  window.SlideStudioCaptureReady=(async()=>{
    const config=JSON.parse(document.getElementById('capture-config').textContent);
    const slide=document.querySelector('#capture-stage > .slide');
    const {BuildAnimations,buildCount}=window.SlideStudioBuilds;
    const steps=buildCount(slide.outerHTML);
    if(config.stage!==null&&config.stage>steps)throw new Error(`This slide has ${steps} reveal steps.`);
    let now=0;
    const animation=new BuildAnimations(slide,{now:()=>now,requestFrame:()=>0,cancelFrame:()=>{},reducedMotion:()=>config.reduced_motion});
    if(config.stage===null)animation.update(steps,{build:false});
    else{
      animation.update(0);
      for(let step=1;step<config.stage;step++){
        animation.update(step);
        now=Math.max(now,...animation.parts.filter(part=>part.active).map(part=>animation.deadline(part)+part.duration))+1;
        animation.draw(now);
      }
      animation.update(config.stage);now+=config.elapsed_ms;animation.draw(now);
    }
    await document.fonts.ready;
    const failed=[];
    await Promise.all([...slide.querySelectorAll('img')].map(async image=>{try{await image.decode();}catch{failed.push(image.getAttribute('alt')||'Image');}}));
    for(const item of document.getAnimations()){
      item.pause();
      const target=item.effect?.target,build=target?.closest?.('[data-build],[data-auto-reveal]');
      const step=build?animation.parts.find(part=>part.element===build)?.step:0;
      if(config.stage===null||step>0&&step<config.stage){const timing=item.effect?.getComputedTiming();item.currentTime=Number.isFinite(timing?.endTime)?timing.endTime:0;}
      else item.currentTime=step>config.stage?0:step===0?now:config.elapsed_ms;
    }
    for(const media of slide.querySelectorAll('video,audio'))media.pause();
    const canvas=slide.getBoundingClientRect();
    const overflow=[...slide.querySelectorAll('h1,h2,h3,h4,h5,h6,p,li,img,svg,[data-element-id]')].filter(element=>{
      if(element.closest('[data-build].unrevealed,[data-auto-reveal].unrevealed'))return false;
      const rect=element.getBoundingClientRect(),style=getComputedStyle(element);
      return style.visibility!=='hidden'&&style.display!=='none'&&rect.width>0&&rect.height>0&&(rect.left<canvas.left-2||rect.top<canvas.top-2||rect.right>canvas.right+2||rect.bottom>canvas.bottom+2);
    }).slice(0,25).map(element=>element.getAttribute('data-element-id')||element.getAttribute('alt')||element.textContent.trim().slice(0,100)||element.tagName);
    return {reveal_steps:steps,failed_fonts:[...document.fonts].filter(face=>face.status==='error').map(face=>face.family),failed_images:failed,possible_overflow:overflow,media_count:slide.querySelectorAll('video,audio').length};
  })();
})();
