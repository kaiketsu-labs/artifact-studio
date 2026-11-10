/* Keep the current artifact selected when moving from the editor to collaboration. */
((root) => {
  'use strict';
  const button = document.querySelector('[data-collaborate]');
  if (!button) return;

  root.ArtifactSession.get().then(session => {
    if (session.collaboration_url !== '/together') return;
    root.SlideIcons.button(button, 'chat', 'Collaborate', true);
    button.hidden = false;
    const open = (action = '') => {
      const source = new URLSearchParams(root.location.search);
      const target = new URL(session.collaboration_url, root.location.origin);
      target.searchParams.set('workspace', 'default');
      const deck = source.get('deck');
      const artifact = button.closest('.hub-top') ? button.dataset.artifact : deck;
      if (artifact) target.searchParams.set('artifact', artifact);
      if (deck && root.location.hash) target.searchParams.set('slide', root.location.hash.slice(1));
      if (action) target.searchParams.set('action', action);
      root.location.assign(target);
    };
    button.addEventListener('click', () => open());
    document.querySelectorAll('#connect-button, [data-hub-connect]').forEach(entry => {
      entry.addEventListener('click', event => {
        event.preventDefault();
        event.stopImmediatePropagation();
        open('agent');
      }, {capture: true});
    });
  }).catch(() => {});

  root.addEventListener('artifact-session-expired', () => { button.hidden = true; });
})(window);
