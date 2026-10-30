/* Apply the saved appearance before styles paint, keeping slide documents independent. */
((root)=>{
  'use strict';
  const key='slide-studio:appearance';
  const valid=value=>['light','dark','system'].includes(value)?value:'system';
  /** Follow system appearance until a local choice is saved, including across open tabs. */
  function create({doc,view,storage,media,icons=()=>view.SlideIcons}){
    let preference='system',button,menu;
    try{preference=valid(storage.getItem(key));}catch{}
    function apply(){
      const dark=preference==='dark'||preference==='system'&&media.matches;
      doc.documentElement.dataset.theme=dark?'dark':'light';
      if(!button)return;
      icons().button(button,dark?'moon':'sun','Appearance');
      for(const option of menu.querySelectorAll('[data-appearance]'))option.setAttribute('aria-checked',String(option.dataset.appearance===preference));
    }
    function choose(value){preference=valid(value);try{storage.setItem(key,preference);}catch{}apply();}
    function mount(){
      button=doc.getElementById('appearance-button');menu=doc.getElementById('appearance-menu');if(!button||!menu)return;
      for(const option of menu.querySelectorAll('[data-appearance]')){
        const value=option.dataset.appearance,label={light:'Light',dark:'Dark',system:'System'}[value];
        icons().button(option,{light:'sun',dark:'moon',system:'monitor'}[value],label,true);
        option.addEventListener('click',()=>{choose(value);menu.hidePopover();button.focus();});
      }
      button.addEventListener('click',()=>{
        if(menu.matches(':popover-open')){menu.hidePopover();return;}
        const rect=button.getBoundingClientRect();menu.style.top=`${rect.bottom+8}px`;menu.style.left=`${Math.max(8,Math.min(rect.right-170,view.innerWidth-178))}px`;
        menu.showPopover();menu.querySelector('[aria-checked="true"]').focus();
      });
      menu.addEventListener('toggle',()=>button.setAttribute('aria-expanded',String(menu.matches(':popover-open'))));
      menu.addEventListener('keydown',event=>{
        const options=[...menu.querySelectorAll('[data-appearance]')],index=options.indexOf(doc.activeElement);
        if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();options[event.key==='Home'?0:event.key==='End'?options.length-1:(index+(event.key==='ArrowDown'?1:-1)+options.length)%options.length].focus();}
        if(event.key==='Escape'){event.preventDefault();menu.hidePopover();button.focus();}
      });
      apply();
    }
    media.addEventListener('change',()=>{if(preference==='system')apply();});
    view.addEventListener('storage',event=>{if(event.key===key||event.key===null){preference=valid(event.newValue);apply();}});
    apply();if(doc.readyState==='loading')doc.addEventListener('DOMContentLoaded',mount,{once:true});else mount();
    return {choose,preference:()=>preference};
  }
  if(typeof module!=='undefined')module.exports={create};
  else{let storage;try{storage=root.localStorage;}catch{storage={getItem:()=>null,setItem:()=>{}};}create({doc:root.document,view:root,storage,media:root.matchMedia('(prefers-color-scheme: dark)')});}
})(typeof window==='undefined'?{}:window);
