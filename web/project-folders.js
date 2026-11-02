/* Drag artifact cards to project folders without transferring file contents. */
((root) => {
  'use strict';
  const MIME = 'application/x-artifact-studio-card';
  const safeId = id => typeof id === 'string' && id.length > 0 && id.length <= 200 && !/[\\/\x00-\x1f\x7f]/.test(id);

  /** Attach delegated folder-drop handling to the stable hub container. */
  function create({content, getArtifacts, getProjects, onMove, onError}) {
    let drag = null, highlighted = null, busy = false;
    const clearHighlight = () => {
      if (highlighted) highlighted.classList.remove('is-drop-target');
      highlighted = null;
    };
    const reset = () => { clearHighlight(); drag = null; };
    const folder = target => target?.closest?.('.hub-sidebar [data-project-folder]');
    const validDestination = (target, transfer) => {
      if (!drag || busy || !transfer || Array.from(transfer.types || []).includes('Files')) return null;
      const node = folder(target), projectId = node?.dataset.projectFolder;
      if (!safeId(projectId) || projectId === drag.projectId || !getProjects().some(project => project.id === projectId)) return null;
      const artifact = getArtifacts().find(item => item.id === drag.id);
      if (!artifact || artifact.project_id !== drag.projectId) return null;
      return node;
    };
    const handlers = {
      dragstart(event) {
        const card = event.target?.closest?.('.hub-card[data-drag-artifact]');
        const id = card?.dataset.dragArtifact, artifact = getArtifacts().find(item => item.id === id);
        reset();
        if (busy || !safeId(id) || !artifact || !event.dataTransfer) return;
        drag = {id, projectId: artifact.project_id};
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData(MIME, id);
        card.classList.add('is-dragging');
      },
      dragover(event) {
        const node = validDestination(event.target, event.dataTransfer);
        if (!node) { clearHighlight(); return; }
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
        if (node !== highlighted) { clearHighlight(); highlighted = node; node.classList.add('is-drop-target'); }
      },
      dragleave(event) {
        if (highlighted && !highlighted.contains(event.relatedTarget)) clearHighlight();
      },
      async drop(event) {
        const node = validDestination(event.target, event.dataTransfer);
        if (!node || event.dataTransfer.getData(MIME) !== drag.id) { reset(); return; }
        event.preventDefault();
        const {id} = drag, projectId = node.dataset.projectFolder;
        busy = true;
        reset();
        try { await onMove(id, projectId); }
        catch (error) { onError(error); }
        finally { busy = false; }
      },
      dragend() {
        content.querySelectorAll('.hub-card.is-dragging').forEach(card => card.classList.remove('is-dragging'));
        reset();
      },
    };
    for (const [name, handler] of Object.entries(handlers)) content.addEventListener(name, handler);
    return {destroy() { for (const [name, handler] of Object.entries(handlers)) content.removeEventListener(name, handler); handlers.dragend(); }};
  }
  const api = {create, MIME};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ProjectFolders = api;
})(typeof window === 'undefined' ? globalThis : window);
