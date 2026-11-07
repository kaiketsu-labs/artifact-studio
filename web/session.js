/* One credential-free workspace description shared by connection and storage UI. */
((root)=>{
  'use strict';
  let pending=null;
  root.ArtifactSession={get(){
    if(!pending)pending=root.fetch('/api/session',{cache:'no-store'}).then(response=>{
      if(!response.ok){const error=new Error(response.status===401?'Your session has expired. Refresh this page to sign in again.':'Workspace connection information unavailable.');error.expired=response.status===401;throw error;}
      return response.json();
    }).catch(error=>{pending=null;throw error;});
    return pending;
  }};
})(window);
