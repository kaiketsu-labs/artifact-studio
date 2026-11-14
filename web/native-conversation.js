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

  /** Present known editor requests without changing the original agent message. */
  function displayEdit(record) {
    const original = {message: record.message || '', edit_summary: []};
    if (typeof record.author !== 'string' || !record.author || record.author !== record.person || !record.assigned || !record.target) return original;
    const at = original.message.indexOf(editMarker);
    if (at < 1) return original;
    const prefix = original.message.slice(0, at), encoded = original.message.slice(at + editMarker.length);
    const replacement = encoded.indexOf(replacementMarker);
    let intent;
    try { intent = JSON.parse(replacement < 0 ? encoded : encoded.slice(0, replacement)); }
    catch { return original; }
    if (!intent || Object.keys(intent).sort().join(',') !== 'action,values' ||
      !['move','rotate','transform','delete','replace_text','style','update'].includes(intent.action)) return original;
    const values = intent.values;
    const fields = new Set(['text','font_size','font_size_unit','font_weight','font_style','text_align','color',
      'x','y','position_reference','canvas_reference','canvas_width','canvas_height',
      'rotation_degrees','rotation_reference','rotation_origin']);
    if (!values || typeof values !== 'object' || Array.isArray(values) || Object.keys(values).length > 20 ||
      Object.entries(values).some(([key,value]) => !fields.has(key) ||
        !(typeof value === 'string' || typeof value === 'number' && Number.isFinite(value)))) return original;
    if (replacement >= 0) {
      if (Object.hasOwn(values, 'text')) return original;
      values.text = encoded.slice(replacement + replacementMarker.length);
    }
    if (['font_size','x','y','canvas_width','canvas_height','rotation_degrees'].some(key => key in values && typeof values[key] !== 'number') ||
      ['text','font_size_unit','font_weight','font_style','text_align','color','position_reference','canvas_reference','rotation_reference','rotation_origin'].some(key => key in values && typeof values[key] !== 'string')) return original;
    if ('position_reference' in values && values.position_reference !== 'visual_top_left' ||
      'canvas_reference' in values && values.canvas_reference !== 'artifact_document' ||
      'rotation_reference' in values && values.rotation_reference !== 'original_element_local' ||
      'rotation_origin' in values && values.rotation_origin !== 'existing_transform_origin') return original;
    if ('font_size_unit' in values && !['px','pt','em','rem','%','vw','vh','cqw','cqh'].includes(values.font_size_unit)) return original;
    const summary = [];
    if (typeof values.text === 'string') summary.push('Text: ' + values.text);
    if ('font_size' in values) summary.push('Text size: ' + values.font_size + (values.font_size_unit ? ' ' + values.font_size_unit : ''));
    if ('font_weight' in values) summary.push('Text weight: ' + values.font_weight);
    if ('font_style' in values) summary.push('Text style: ' + values.font_style);
    if ('text_align' in values) summary.push('Text alignment: ' + values.text_align);
    if ('color' in values) summary.push('Text color: ' + values.color);
    const rounded = value => Math.round(value * 100) / 100;
    for (const [axis,edge] of [['x','left'],['y','top']]) if (typeof values[axis] === 'number') {
      const dimension = values[axis === 'x' ? 'canvas_width' : 'canvas_height'];
      const position = values.canvas_reference === 'artifact_document' && typeof dimension === 'number'
        ? rounded(values[axis] * dimension) + ' px in the document' : rounded(values[axis] * 100) + '% of the slide';
      summary.push('Position from ' + edge + ': ' + position);
    }
    if ('rotation_degrees' in values) summary.push('Rotate ' + values.rotation_degrees + '° from the original');
    if (!summary.length && intent.action !== 'delete' || intent.action === 'delete' && Object.keys(values).length) return original;
    const captions = {'Update element properties.': 'Element change requested.', 'Replace text.': 'Text change requested.',
      'Update element style.': 'Formatting change requested.', 'Update element text and style.': 'Text and formatting change requested.'};
    return {message: Object.hasOwn(captions, prefix) ? captions[prefix] : prefix, edit_summary: summary, original_request: original.message};
  }

  /** Share the authenticated actor and preserve scoped access and retry errors. */
  function create({api}) {
    let personRequest;
    const pendingRequests = new Map();
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
      hasPendingRequest(artifactId, requestId) {
        return pendingRequests.has(`${base(artifactId)}/${requestId}`);
      },
      async send(artifactId, {message, requestId, contentHash, slideId, slideContentHash, target,
        editIntent, files, proposal, channel = 'shared', released = true, assigned = '', forAgent = false, includeSharedContext = false}) {
        const endpoint = base(artifactId), anchor = artifactTarget(target, slideId);
        const stale = message => Object.assign(new Error(message), {status: 409, stale: true});
        const key = `${endpoint}/${requestId}`;
        const previous = pendingRequests.get(key);
        if (!previous && forAgent && !assigned) throw new Error('Connect your agent or turn off the robot icon to send to Shared chat.');
        // Shared discussion follows the slide ID, while edits and selected objects keep their revision.
        const discussion = slideId && !target && !editIntent && !assigned && channel === 'shared';
        if (!previous && !discussion && !proposal && !contentHash) throw stale(staleArtifact);
        // A lost response may already have saved this exact request. Replaying its
        // original anchors and routing lets the server acknowledge it idempotently.
        const payload = previous || {
          message: editMessage(message, editIntent), request_id: requestId,
          content_hash: discussion || proposal ? '' : contentHash,
          target: structuredClone(anchor), channel, released, assigned,
          ...(channel === 'shared' && assigned && includeSharedContext ? {include_shared_context: true} : {}),
          ...((!discussion || proposal) && slideContentHash ? {slide_content_hash: slideContentHash} : {}),
          ...(proposal ? {proposal: structuredClone(proposal)} : {}),
          ...(files?.length ? {files: files.map(file => ({...file}))} : {}),
        };
        try {
          const result = await request(`${endpoint}/chat`, payload);
          pendingRequests.delete(key);
          return result;
        } catch (error) {
          // An uncertain response may already have persisted this request and its routing.
          if (!error.status || error.status >= 500 || error.status === 408) {
            pendingRequests.set(key, payload);
            if (pendingRequests.size > 100) pendingRequests.delete(pendingRequests.keys().next().value);
          } else pendingRequests.delete(key);
          throw error;
        }
      },
      release(artifactId, channel = 'shared') {
        return request(inChannel(`${base(artifactId)}/release`, channel), {});
      },
    };
  }

  const api = {create, takeAgentShortcut, agentRequest, agentBadge, setAgentToggle, displayEdit};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.StudioNativeConversation = api;
})(typeof window === 'undefined' ? {} : window);
