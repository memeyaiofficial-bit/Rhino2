const CACHE='black-rhino-pos-v2';
const SHELL=['/offline-pos','/static/app.css','/static/app.js','/static/logo.png'];
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL)).then(()=>self.skipWaiting())));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('fetch',event=>{
  const r=event.request;
  if(r.method!=='GET') return;
  const url=new URL(r.url);
  if(url.pathname==='/pos'){event.respondWith(fetch(r).catch(()=>caches.match('/offline-pos')));return;}
  if(url.pathname==='/offline-pos'){event.respondWith(caches.match('/offline-pos').then(x=>x||fetch(r)));return;}
  if(url.pathname.startsWith('/static/')){event.respondWith(caches.match(r).then(x=>x||fetch(r).then(resp=>{let copy=resp.clone();caches.open(CACHE).then(c=>c.put(r,copy));return resp})));}
});
