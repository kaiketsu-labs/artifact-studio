/* Cache only the offline shell; private presentation content stays in IndexedDB. */
'use strict';
const CACHE='artifact-studio-offline-shell-v1';
const ASSETS=['/web/offline.html','/web/offline-shell.js','/web/offline-store.js','/web/offline-client.js','/web/offline.css'];
self.addEventListener('install',event=>event.waitUntil((async()=>{
  const cache=await caches.open(CACHE);
  for(const path of ASSETS){
    const response=await fetch(path,{cache:'no-store'});
    const type=(response.headers.get('content-type')||'').toLowerCase();
    const expected=path.endsWith('.html')?'text/html':path.endsWith('.css')?'text/css':'javascript';
    if(!response.ok||response.redirected||!type.includes(expected))throw Error('Offline shell could not be verified.');
    await cache.put(path,response);
  }
  await self.skipWaiting();
})()));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('fetch',event=>{
  const url=new URL(event.request.url);
  if(event.request.method==='GET'&&url.origin===self.location.origin&&ASSETS.includes(url.pathname)){
    event.respondWith(caches.open(CACHE).then(cache=>cache.match(url.pathname)).then(response=>response||fetch(event.request)));
  }
});
