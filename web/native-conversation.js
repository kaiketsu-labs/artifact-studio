/* Use the scoped collaboration conversation from the native artifact and slide editors. */
((root) => {
  'use strict';
  const staleArtifact = 'The artifact changed. Review the current version first.';
  const editMarker = '\n\nEdit details:\n';
  const replacementMarker = '\nReplacement text:\n';
  const anchorFields = ['selector', 'label', 'quote', 'section', 'start', 'end', 'x', 'y', 'width', 'height', 'region', 'viewport', 'state'];
  const base = id => `/api/workspaces/${root.ArtifactSession?.workspace || 'default'}/artifacts/${encodeURIComponent(id)}`;
  const inChannel = (path, channel) => `${path}?${new URLSearchParams({channel})}`;

  /** Keep portable anchors and discard the native editor's HTML snapshot and UI metadata. */
  function artifactTarget(target, slideId) {
    const anchorSlide = slideId || target?.slide_id;
    if (!target) return anchorSlide ? {kind: 'slide', slide_id: anchorSlide} : null;
    const result = {kind: target.kind || 'element'};
    if (anchorSlide) result.slide_id = anchorSlide;
    for (const field of anchorFields) if (Object.hasOwn(target, field)) result[field] = target[field];
    if (result.kind === 'group') result.elements = target.elements.map(item => artifactTarget(item, anchorSlide));
    return result;
  }

  /** Attach structured edit details to the existing conversation text field. */
  function editMessage(message, intent) {
    if (!intent) return message;
    const {action, values} = intent;
    const full = message + editMarker + JSON.stringify({action, values});
    if (full.length <= 10000 || typeof values?.text !== 'string') return full;
    // A long replacement can exceed the message limit through JSON escaping alone.
    const {text, ...properties} = values;
    return message + editMarker + JSON.stringify({action, values: properties}) + replacementMarker + text;
  }

  /** Share the authenticated actor and preserve scoped access and retry errors. */
  function create({api}) {
    let personRequest;
    async function request(path, body) {
      root.ArtifactSession?.assertActive();
      try {
        const result = await api(path, body);
        root.ArtifactSession?.assertActive();
        return result;
      } catch (error) {
        if (error.status === 401 && !error.expired) {
          throw Object.assign(new Error('Your session has expired. Sign in again to continue.', {cause: error}), {status: 401, expired: true});
        }
        if (error.status === 409 && error.message === staleArtifact) error.stale = true;
        throw error;
      }
    }
    function person() {
      personRequest ||= request('/api/together/session').then(session => session.person).catch(error => {
        personRequest = undefined;
        throw error;
      });
      return personRequest;
    }

    return {
      async read(artifactId, {channel = 'shared'} = {}) {
        const endpoint = base(artifactId);
        const [state, messages, actor] = await Promise.all([
          request(`${endpoint}/state`), request(inChannel(`${endpoint}/chat`, channel)), person(),
        ]);
        return {access: state.access, agents: state.agents, messages, person: actor,
          contentHash: state.content_hash, draftCount: state.draft_counts?.[channel] ?? 0};
      },
      async send(artifactId, {message, requestId, contentHash, slideId, slideContentHash, target,
        editIntent, files, channel = 'shared', released = true, assigned = ''}) {
        const endpoint = base(artifactId), anchor = artifactTarget(target, slideId);
        const stale = message => Object.assign(new Error(message), {status: 409, stale: true});
        // Shared discussion follows the slide ID, while edits and selected objects keep their revision.
        const discussion = slideId && !target && !editIntent && !assigned && channel === 'shared';
        if (!discussion && !contentHash) throw stale(staleArtifact);
        if (!discussion && slideContentHash) {
          const state = await request(`${endpoint}/state`);
          // The server checks exact retries before rejecting a changed artifact hash.
          if (state.content_hash === contentHash && state.slides.find(item => item.id === anchor?.slide_id)?.content_hash !== slideContentHash) {
            throw stale('The slide changed. Review the current version first.');
          }
        }
        return request(`${endpoint}/chat`, {message: editMessage(message, editIntent), request_id: requestId,
          content_hash: discussion ? '' : contentHash, target: anchor, channel, released, assigned, ...(files?.length ? {files} : {})});
      },
      release(artifactId, channel = 'shared') {
        return request(inChannel(`${base(artifactId)}/release`, channel), {});
      },
    };
  }

  root.StudioNativeConversation = {create};
})(window);
