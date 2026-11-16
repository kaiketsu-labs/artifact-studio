/* Keep push updates live without repeatedly downloading idle decks. */
((root)=>{
  'use strict';
  function create({refresh,workflow,checkChat=null,onError=()=>{},document=root.document,createSource=typeof root.EventSource==='function'?url=>new root.EventSource(url):null,repeat=setInterval,cancelRepeat=clearInterval}){
    let deckId=null,source=null,reading=false,checkingChat=false;
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
    /** While chat is open, its small change check can run between snapshot refreshes. */
    async function pollChat(){
      if(!deckId||document.hidden||reading||checkingChat)return;
      checkingChat=true;
      try{await checkChat(deckId);}
      catch{/* The regular refresh can recover after a connection or authorization error. */}
      finally{checkingChat=false;}
    }
    function watch(id){if(id===deckId)return;close();deckId=id;connect();}
    function visibility(){if(document.hidden)close();else{connect();if(deckId)void refresh();}}
    document.addEventListener('visibilitychange',visibility);
    const timer=repeat(()=>void poll(),3500);
    const chatTimer=checkChat?repeat(()=>void pollChat(),750):null;
    function stop(){close();cancelRepeat(timer);if(chatTimer!==null)cancelRepeat(chatTimer);document.removeEventListener('visibilitychange',visibility);deckId=null;}
    return {watch,poll,stop};
  }
  if(typeof module!=='undefined')module.exports={create};else root.SlideUpdates={create};
})(typeof window==='undefined'?{}:window);
