"""Exercise experimental offline data partitioning in real IndexedDB."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_offline_store_partitions_retries_and_cross_tab_purge() -> None:
    script = Path(__file__).resolve().parents[1] / "web" / "offline-store.js"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
        )
        context = browser.new_context()
        page = context.new_page()
        page.route(
            "https://offline.example.test/",
            lambda route: route.fulfill(
                status=200, content_type="text/html", body="<!doctype html><title>Fictional</title>"
            ),
        )
        page.goto("https://offline.example.test/")
        page.evaluate("""() => new Promise((resolve,reject)=>{
          const request=indexedDB.open('artifact-studio-offline-v1',1);
          request.onupgradeneeded=()=>{
            const decks=request.result.createObjectStore('decks',{keyPath:'key'});
            decks.add({key:'@epoch',value:'legacy-epoch'});
            decks.add({key:'a'.repeat(64)+':legacy',snapshot:{title:'Legacy'},
              outbox:[{kind:'comment',body:'Old queued draft'}]});
          };
          request.onerror=()=>reject(request.error);
          request.onsuccess=()=>{request.result.close();resolve();};
        })""")
        page.add_script_tag(path=script)
        other_page = context.new_page()
        other_page.route(
            "https://offline.example.test/",
            lambda route: route.fulfill(
                status=200, content_type="text/html", body="<!doctype html><title>Fictional</title>"
            ),
        )
        other_page.goto("https://offline.example.test/")
        other_page.add_script_tag(path=script)
        other_page.evaluate("window.saved=OfflineStore.create({enabled:true,scope:'b'.repeat(64)})")
        result = page.evaluate("""async () => {
          const a='a'.repeat(64),b='b'.repeat(64);
          let denied=false;try{OfflineStore.create({scope:a});}catch{denied=true;}
          let noBroadcast=false;
          try{OfflineStore.create({enabled:true,scope:a,BroadcastChannel:null});}
          catch{noBroadcast=true;}
          const first=OfflineStore.create({enabled:true,scope:a});
          const second=OfflineStore.create({enabled:true,scope:b});
          const legacy=await first.drafts('legacy');
          const oldTabRejected=await new Promise((resolve,reject)=>{
            const request=indexedDB.open('artifact-studio-offline-v1',1);
            request.onerror=()=>resolve(request.error.name==='VersionError');
            request.onsuccess=()=>{request.result.close();reject(Error('Old database opened'));};
          });
          await first.save('deck',{title:'Fictional'});
          const operation={id:'operation-1',slide_id:'slide',anchor:'original-hash',
            kind:'comment',body:'Draft',token:'must-not-be-stored'};
          await first.queue('deck',operation);await first.queue('deck',operation);
          let collision=false;
          try{await first.queue('deck',{...operation,body:'Different'});}catch{collision=true;}
          const reopened=OfflineStore.create({enabled:true,scope:a});
          const persisted=await reopened.read('deck');reopened.close();
          const own=await first.read('deck'),other=await second.read('deck');
          let overwrite='';
          try{await first.save('deck',{title:'Replacement'});}catch(error){overwrite=error.message;}
          const receipt='v2.'+'f'.repeat(360),replacement='v2.'+'e'.repeat(360);
          const original={id:'slide',html:'Original source',receipt};
          await first.save('editable',{title:'Original',slides:[original]});
          await first.save('editable',{
            title:'Online refresh',slides:[{...original,receipt:replacement}]});
          const refreshed=await first.read('editable');
          const edit={id:'edit-1',slide_id:'slide',anchor:'original-hash',
            kind:'edit',body:'Draft',receipt,base_html:'Untrusted caller source'};
          let staleMessage='';
          try{await first.queue('editable',edit);}catch(error){staleMessage=error.message;}
          await first.queue('editable',{...edit,receipt:replacement});
          const signed=(await first.read('editable')).outbox;
          let release;
          const delayed={open(...args){
            const request=indexedDB.open(...args);
            return new Proxy(request,{
            get(target,property){return Reflect.get(target,property,target);},
            set(target,property,value){
              if(property==='onsuccess'){
                target.onsuccess=event=>{release=()=>value(event);};return true;
              }
              target[property]=value;return true;
            }});
          }};
          const stale=OfflineStore.create({enabled:true,scope:a,indexedDB:delayed});
          const writing=stale.save('late',{title:'Must not survive'}).then(()=>false,()=>true);
          while(!release)await new Promise(resolve=>setTimeout(resolve,0));
          await first.purge();release();const blocked=await writing;
          await new Promise(resolve=>setTimeout(resolve,100));
          let closed=false;try{await second.read('deck');}catch{closed=true;}
          const fresh=OfflineStore.create({enabled:true,scope:a});
          const after=await fresh.read('deck');fresh.close();
          return {denied,noBroadcast,legacy,oldTabRejected,own,persisted,collision,
            other:other??null,overwrite,closed,blocked,after:after??null,refreshed,staleMessage,signed};
        }""")
        assert result["denied"] and result["noBroadcast"]
        assert result["oldTabRejected"]
        assert result["legacy"] == [{"kind": "comment", "body": "Old queued draft"}]
        assert "review your queued changes" in result["overwrite"]
        assert result["closed"] and result["blocked"]
        assert result["other"] is None and result["after"] is None
        assert other_page.evaluate("saved.read('deck').then(()=>false,()=>true)")
        assert result["collision"] and result["persisted"] == result["own"]
        assert result["own"]["outbox"] == [
            {
                "id": "operation-1",
                "slide_id": "slide",
                "anchor": "original-hash",
                "kind": "comment",
                "body": "Draft",
            }
        ]
        assert result["refreshed"]["snapshot"]["title"] == "Online refresh"
        assert result["refreshed"]["outbox"] == []
        assert "refreshed in another tab" in result["staleMessage"]
        assert result["signed"] == [
            {
                "id": "edit-1",
                "slide_id": "slide",
                "anchor": "original-hash",
                "kind": "edit",
                "body": "Draft",
                "receipt": "v2." + "e" * 360,
                "base_html": "Original source",
            }
        ]
        browser.close()
