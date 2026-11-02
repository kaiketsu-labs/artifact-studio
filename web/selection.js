/* Immutable feedback anchors are independent of the current preview's DOM. */
((root)=>{
  'use strict';
  const targetsOf=target=>target?.kind==='group'?target.elements:target?[target]:[];
  const isAdditive=event=>!!(event.metaKey||event.ctrlKey||event.shiftKey);
  const individualTarget=target=>target&&typeof target==='object'&&target.kind!=='group'&&typeof target.selector==='string'&&typeof target.label==='string'&&!target.elements;
  /** Accept existing single anchors and bounded flat groups in saved drafts. */
  function validTarget(target){return individualTarget(target)||target?.kind==='group'&&typeof target.label==='string'&&Array.isArray(target.elements)&&target.elements.length>0&&target.elements.length<=100&&target.elements.every(individualTarget);}
  /** Retain the single-target wire format and label a group without duplicating snapshots. */
  function groupTargets(targets){return targets.length>1?{kind:'group',label:`${targets.length} objects`,elements:targets}:targets[0]||null;}
  /** Toggle by stable selector while keeping every other original snapshot intact. */
  function selectTargets(targets,target,additive=false){
    if(!target)return additive?targets:[];
    if(!additive)return [target];
    return targets.some(item=>item.selector===target.selector)?targets.filter(item=>item.selector!==target.selector):[...targets,target].slice(0,100);
  }
  const api={targetsOf,isAdditive,validTarget,groupTargets,selectTargets};
  if(typeof module!=='undefined')module.exports=api;else root.SlideSelection=api;
})(typeof window==='undefined'?{}:window);
