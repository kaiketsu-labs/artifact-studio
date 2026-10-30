/* Keep push updates live without repeatedly downloading idle decks. */
((root)=>{
  'use strict';
  function create({refresh,workflow,onError=()=>{},document=root.document,createSource=typeof root.EventSource==='function'?url=>new root.EventSource(url):null,repeat=setInterval,cancelRepeat=clearInterval}){
    let deckId=null,source=null,reading=false;
    function close(){source?.close();source=null;}
    function connect(){
      if(!deckId||source||document.hidden||!createSource)return;
      const id=deckId,opened=createSource(`/api/decks/${encodeURIComponent(id)}/events`);source=opened;
      opened.addEventListener('change',()=>{if(source===opened&&deckId===id&&!document.hidden)void refresh();});
    }
    /** Poll availability only when push is healthy; retain full refresh as the fallback. */
    async function poll(){
      if(!deckId||document.hidden||reading)return;
      reading=true;
      try{
        if(source?.readyState===1){try{await workflow(deckId);}catch{await refresh();}}
        else await refresh();
      }
      catch(error){onError(error);}
      finally{reading=false;}
    }
    function watch(id){if(id===deckId)return;close();deckId=id;connect();}
    function visibility(){if(document.hidden)close();else{connect();if(deckId)void refresh();}}
    document.addEventListener('visibilitychange',visibility);
    const timer=repeat(()=>void poll(),3500);
    function stop(){close();cancelRepeat(timer);document.removeEventListener('visibilitychange',visibility);deckId=null;}
    return {watch,poll,stop};
  }
  if(typeof module!=='undefined')module.exports={create};else root.SlideUpdates={create};
})(typeof window==='undefined'?{}:window);
