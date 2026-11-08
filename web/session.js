/* One credential-free workspace description shared by connection and storage UI. */
((root)=>{
  'use strict';
  let pending=null,expired=false;
  function expiryError(){const error=new Error('Your session has expired. Sign in again to continue.');error.expired=true;error.status=401;return error;}
  function assertActive(){if(expired)throw expiryError();}
  function observe(response){
    const loginPage=response.redirected && (response.headers?.get?.('content-type')||'').toLowerCase().includes('text/html');
    if(response.status===401||loginPage){
      pending=null;
      root.artifactStudioSession=null;
      if(!expired){expired=true;root.dispatchEvent(new Event('artifact-session-expired'));}
    }
    return response;
  }
  root.ArtifactSession={observe,assertActive,get(options={}){
    if(expired)return Promise.reject(expiryError());
    if(options.refresh)pending=null;
    if(!pending){
      const request=root.fetch('/api/session',{cache:'no-store'}).then(observe).then(response=>{
        if(expired)throw expiryError();
        if(!response.ok)throw new Error('Workspace connection information unavailable.');
        return response.json().then(session=>{if(expired)throw expiryError();return session;});
      }).catch(error=>{if(pending===request)pending=null;throw error;});
      pending=request;
    }
    return pending;
  }};
})(window);
