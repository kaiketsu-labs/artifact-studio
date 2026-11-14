/* Use the scoped collaboration conversation from the native artifact and slide editors. */
((root) => {
  'use strict';
  const staleArtifact = 'The artifact changed. Review the current version first.';
  const staleSlide = 'The slide changed. Review the current version first.';
  const editMarker = '\n\nEdit details:\n';
  const replacementMarker = '\nReplacement text:\n';
  const anchorFields = ['selector', 'label', 'quote', 'section', 'start', 'end', 'x', 'y', 'width', 'height', 'region', 'viewport', 'state'];
  const base = id => `/api/workspaces/${root.ArtifactSession?.workspace || 'default'}/artifacts/${encodeURIComponent(id)}`;
  const inChannel = (path, channel) => `${path}?${new URLSearchParams({channel})}`;

  /** Turn a leading shortcut into routing metadata, keeping the message and caret intact. */
  function takeAgentShortcut(input, submitting = false) {
    const prefix = input.value.match(/^\s*(?:@@(?!@)|@agent(?=\s))\s*/i)
      || (submitting && input.value.match(/^\s*@agent$/i));
    if (!prefix) return false;
    const {selectionStart, selectionEnd} = input;
    const length = prefix[0].length;
    input.setRangeText('', 0, length);
    input.setSelectionRange(Math.max(0, selectionStart - length), Math.max(0, selectionEnd - length));
    return true;
  }

  const agentRequest = message => message.channel === 'shared' && !!message.assigned && message.author === message.person;
  const agentBadge = () => `<span class="chat-agent-badge" role="img" aria-label="Agent request" title="Agent request">${root.SlideIcons.svg('robot')}</span>`;

  /** Keep the toggle's accessible name stable and describe its current delivery state. */
  function setAgentToggle(button, pressed, agent) {
    button.setAttribute('aria-pressed', String(pressed));
    button.title = !agent ? 'Connect an agent to send a request · visible to collaborators' : pressed
      ? (agent.status === 'listening' ? 'Agent addressed · visible to collaborators · click to turn off' : 'Agent addressed · visible to collaborators · queued until connected')
      : 'Ask your agent · visible to collaborators · @@ or @agent · Includes recent Shared messages';
  }

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
    const pendingRoutes = new Map();
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
        if (error.status === 409 && [staleArtifact, staleSlide].includes(error.message)) error.stale = true;
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
        return {access: state.access, agents: state.agents, messages, person: actor, contacts: state.contacts || [], mentions: state.mentions || [], incomingCursors: state.incoming_cursors || {},
          contentHash: state.content_hash, draftCount: state.draft_counts?.[channel] ?? 0};
      },
      history(artifactId, {channel = 'shared', before = 0} = {}) {
        return request(`${base(artifactId)}/chat?${new URLSearchParams({channel, before})}`);
      },
      readMention(artifactId, id) {
        return request(`${base(artifactId)}/mentions/${id}/read`, {});
      },
      async send(artifactId, {message, requestId, contentHash, slideId, slideContentHash, target,
        editIntent, files, proposal, channel = 'shared', released = true, assigned = '', forAgent = false, includeSharedContext = false}) {
        const endpoint = base(artifactId), anchor = artifactTarget(target, slideId);
        const stale = message => Object.assign(new Error(message), {status: 409, stale: true});
        const key = `${endpoint}/${requestId}`;
        const previousRoute = pendingRoutes.get(key);
        const route = previousRoute || {assigned, includeSharedContext};
        if (forAgent && !route.assigned) throw new Error('Connect your agent or turn off the robot icon to send to Shared chat.');
        // Shared discussion follows the slide ID, while edits and selected objects keep their revision.
        const discussion = slideId && !target && !editIntent && !route.assigned && channel === 'shared';
        if (!discussion && !proposal && !contentHash) throw stale(staleArtifact);
        try {
          const result = await request(`${endpoint}/chat`, {message: editMessage(message, editIntent), request_id: requestId,
            content_hash: discussion || proposal ? '' : contentHash, target: anchor, channel, released, assigned: route.assigned,
            ...(channel === 'shared' && route.assigned && route.includeSharedContext ? {include_shared_context: true} : {}),
            ...((!discussion || proposal) && slideContentHash ? {slide_content_hash: slideContentHash} : {}),
            ...(proposal ? {proposal} : {}),
            ...(files?.length ? {files} : {})});
          pendingRoutes.delete(key);
          return result;
        } catch (error) {
          // An uncertain response may already have persisted this request and its routing.
          if (!error.status || error.status >= 500 || error.status === 408) {
            pendingRoutes.set(key, route);
            if (pendingRoutes.size > 100) pendingRoutes.delete(pendingRoutes.keys().next().value);
          }
          throw error;
        }
      },
      release(artifactId, channel = 'shared') {
        return request(inChannel(`${base(artifactId)}/release`, channel), {});
      },
    };
  }

  root.StudioNativeConversation = {create, takeAgentShortcut, agentRequest, agentBadge, setAgentToggle};
})(window);
