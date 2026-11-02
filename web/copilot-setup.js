(() => {
  'use strict';
  for(const node of document.querySelectorAll('[data-icon]'))node.innerHTML=window.SlideIcons.svg(node.dataset.icon);
  const status=document.getElementById('copilot-copy-status');
  const prompt='Set up Microsoft 365 Copilot for this Slide Studio checkout. Read docs/MICROSOFT-COPILOT.md and agent/copilot.md. Reuse existing local settings, scope the separate authenticated MCP gateway to my chosen project, and test first with fictional artifacts. Keep port 8790 private. Prepare the connection before enabling any external access; confirm the exact endpoint, provider and shared scope with me. Verify an actual Copilot tool call, saved revision and rendered result. Report any sign-in, tenant or visual-review blocker honestly.';
  document.getElementById('copy-copilot-setup').addEventListener('click',async()=>{
    try{await navigator.clipboard.writeText(prompt);status.textContent='Copied. Paste into your local agent.';}
    catch{status.textContent=prompt;}
  });
  let loaded=false,loading=false;
  document.getElementById('copilot-manual').addEventListener('toggle',async event=>{
    if(!event.target.open||loaded||loading)return;loading=true;
    const target=document.getElementById('copilot-guide');target.textContent='Loading setup guide…';
    try{
      const response=await fetch('/api/copilot/setup/guide');if(!response.ok)throw new Error('Setup guide unavailable. Restart Slide Studio after updating, then reopen this section.');
      target.innerHTML=window.SlideChatMarkdown.render((await response.text()).replaceAll('../agent/copilot.md','/api/copilot/setup/instructions'));loaded=true;
    }catch(error){target.textContent=error.message;}
    finally{loading=false;}
  });
})();
