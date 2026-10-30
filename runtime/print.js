/* Wait for portable assets before opening the browser's PDF destination selector. */
(() => {
  'use strict';
  async function ready(){
    await document.fonts.ready;
    await Promise.all([...document.images].map(image=>image.decode().catch(()=>{})));
    if(!new URLSearchParams(location.search).has('preview'))window.print();
  }
  document.getElementById('print').addEventListener('click',()=>window.print());
  void ready();
})();
