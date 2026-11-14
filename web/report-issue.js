/* Draft a public issue with an optional screenshot kept in the browser. */
((root) => {
  'use strict';
  const doc = root.document;
  const trigger = doc.querySelector('[data-report-issue]');
  if (!trigger) return;
  const icon = root.SlideIcons.svg;

  const dialog = doc.createElement('dialog');
  dialog.id = 'studio-report';
  dialog.className = 'app-about studio-report';
  trigger.setAttribute('aria-controls', dialog.id);
  trigger.setAttribute('aria-expanded', 'false');
  dialog.setAttribute('aria-labelledby', 'studio-report-title');
  dialog.setAttribute('data-voice-exclude', '');
  dialog.innerHTML = `
    <header><div class="studio-report-heading">${icon('flag')}<h2 id="studio-report-title">Report an issue</h2></div>
      <button type="button" aria-label="Close report" title="Close report">${icon('close')}</button></header>
    <form>
      <div class="studio-report-fields">
        <label>Title<input name="title" required maxlength="120" autofocus></label>
        <label>What happened?<textarea name="body" required maxlength="1000" rows="4"></textarea></label>
      </div>
      <fieldset class="studio-report-attachment">
        <legend>Screenshot <span>optional</span></legend>
        <div class="studio-report-actions">
          <button type="button" data-capture>${icon('image')}<span>Capture tab or window</span></button>
          <button type="button" data-choose>${icon('upload')}<span>Choose screenshot</span></button>
          <input type="file" aria-label="Choose screenshot" accept="image/png,image/jpeg,image/webp" hidden>
        </div>
        <p role="status"></p>
        <figure hidden><img alt="Screenshot preview">
          <figcaption><button type="button" data-copy hidden>${icon('copy')}<span>Copy screenshot</span></button><a download="artifact-studio-issue.png">${icon('download')}<span>Save screenshot</span></a>
            <button type="button" data-remove aria-label="Remove screenshot" title="Remove screenshot">${icon('trash')}</button></figcaption>
          <p data-attachment-help>Save the image, then choose it in the Screenshot field on GitHub.</p>
        </figure>
      </fieldset>
      <footer class="studio-report-footer">
        <p>${icon('info')}<span>GitHub issues are public. Check your report for private information.</span></p>
        <button type="submit"><span>Open GitHub draft</span> ${icon('external')}</button>
        <a data-draft-link target="_blank" rel="noopener noreferrer" hidden>Open the draft in GitHub</a>
      </footer>
    </form>`;
  doc.body.append(dialog);
  const form = dialog.querySelector('form');
  const capture = dialog.querySelector('[data-capture]');
  const picker = dialog.querySelector('input[type=file]');
  const choose = dialog.querySelector('[data-choose]');
  const remove = dialog.querySelector('[data-remove]');
  const copy = dialog.querySelector('[data-copy]');
  const submit = dialog.querySelector('[type=submit]');
  const submitLabel = submit.querySelector('span');
  const draftLink = dialog.querySelector('[data-draft-link]');
  const clipboard = root.navigator.clipboard;
  const canCopy = typeof clipboard?.write === 'function' && typeof root.ClipboardItem === 'function';
  copy.hidden = !canCopy;
  if (canCopy) dialog.querySelector('[data-attachment-help]').textContent =
    'Paste the screenshot into “What happened?” on GitHub, or add the saved file in the Screenshot field.';
  const figure = dialog.querySelector('figure');
  const preview = figure.querySelector('img');
  const save = figure.querySelector('a');
  const status = dialog.querySelector('[role=status]');
  let url, screenshot, generation = 0, activeStream;
  let handoff = 0;

  function clearDraft() {
    handoff++;
    draftLink.hidden = true;
    draftLink.removeAttribute('href');
    submit.disabled = false;
  }

  function clearScreenshot() {
    clearDraft();
    if (url) root.URL.revokeObjectURL(url);
    url = undefined;
    screenshot = undefined;
    submitLabel.textContent = 'Open GitHub draft';
    picker.value = '';
    preview.removeAttribute('src');
    save.removeAttribute('href');
    figure.hidden = true;
    status.textContent = '';
  }

  function showScreenshot(file, extension) {
    clearScreenshot();
    screenshot = file;
    if (canCopy) submitLabel.textContent = 'Copy screenshot and open GitHub draft';
    url = root.URL.createObjectURL(file);
    preview.src = url;
    save.href = url;
    save.download = 'artifact-studio-issue.' + extension;
    figure.hidden = false;
  }

  choose.addEventListener('click', () => picker.click());
  function chooseScreenshot(file) {
    const extension = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}[file.type];
    if (!extension || file.size > 10_000_000) {
      picker.value = '';
      status.textContent = 'Choose a PNG, JPEG or WebP screenshot under 10 MB.' +
        (url ? ' Your previous screenshot is still selected.' : '');
      return;
    }
    showScreenshot(file, extension);
  }

  picker.addEventListener('change', () => {
    if (picker.files?.[0]) chooseScreenshot(picker.files[0]);
  });
  dialog.addEventListener('paste', event => {
    const image = [...(event.clipboardData?.files ?? [])].find(file => file.type.startsWith('image/'));
    if (!image) return;
    event.preventDefault();
    chooseScreenshot(image);
  });

  function screenshotFromImage(image, width, height) {
    const canvas = doc.createElement('canvas');
    const ratio = Math.min(1, 1920 / Math.max(width, height));
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    const context = canvas.getContext('2d');
    if (!context || !canvas.width || !canvas.height) throw new Error('Empty screenshot');
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    return new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
  }

  function nextPaint() {
    return new Promise(resolve => root.requestAnimationFrame(resolve));
  }

  async function recordScreenshot(video) {
    let timer;
    try {
      return await Promise.race([
        (async () => {
          // Hide the form before playback consumes the first available frame.
          await nextPaint();
          await nextPaint();
          await video.play();
          return screenshotFromImage(video, video.videoWidth, video.videoHeight);
        })(),
        new Promise((_, reject) => {
          timer = root.setTimeout(() => reject(new Error('Capture timed out')), 4000);
        }),
      ]);
    } finally {
      root.clearTimeout(timer);
    }
  }

  function captureError(error) {
    if (error.name === 'NotAllowedError') {
      return 'Capture was canceled or blocked. Allow screen sharing when prompted, or choose a screenshot.';
    }
    if (error.name === 'NotReadableError') {
      return 'The browser could not read the selected window. Check screen recording permissions, or choose a screenshot.';
    }
    return 'Could not capture the selected tab or window. Choose a screenshot instead.';
  }

  capture.hidden = typeof root.navigator.mediaDevices?.getDisplayMedia !== 'function';
  capture.addEventListener('click', async () => {
    const attempt = generation;
    const video = doc.createElement('video');
    let stream;
    capture.disabled = true;
    status.textContent = '';
    try {
      stream = await root.navigator.mediaDevices.getDisplayMedia({
        audio: false, video: true, preferCurrentTab: true, monitorTypeSurfaces: 'exclude',
      });
      if (generation !== attempt || !dialog.open) return;
      activeStream = stream;
      const track = stream.getVideoTracks()[0];
      if (track?.getSettings().displaySurface === 'monitor') {
        status.textContent = 'Choose the Studio tab or browser window instead of the entire screen.';
        return;
      }
      video.muted = true;
      video.playsInline = true;
      video.srcObject = stream;
      dialog.classList.add('studio-report-capturing');
      const file = await recordScreenshot(video);
      if (!file || file.size > 10_000_000) throw new Error('Invalid capture');
      if (generation === attempt && dialog.open) showScreenshot(file, 'png');
    } catch (error) {
      if (generation === attempt && dialog.open) status.textContent = captureError(error);
    } finally {
      stream?.getTracks().forEach(track => track.stop());
      if (activeStream === stream) activeStream = undefined;
      video.srcObject = null;
      if (generation === attempt) {
        dialog.classList.remove('studio-report-capturing');
        capture.disabled = false;
      }
    }
  });

  async function screenshotPng(file) {
    if (file.type === 'image/png') return file;
    const bitmap = await root.createImageBitmap(file);
    try {
      const png = await screenshotFromImage(bitmap, bitmap.width, bitmap.height);
      if (!png || png.size > 10_000_000) throw new Error('Invalid screenshot');
      return png;
    } finally {
      bitmap.close();
    }
  }

  async function copyScreenshot(file) {
    if (!canCopy) return false;
    try {
      const png = screenshotPng(file);
      const item = new root.ClipboardItem({'image/png': png});
      await Promise.all([clipboard.write([item]), png]);
      return true;
    } catch {
      return false;
    }
  }

  copy.addEventListener('click', async () => {
    const selected = screenshot;
    const copied = await copyScreenshot(selected);
    if (dialog.open && selected === screenshot) {
      status.textContent = copied
        ? 'Screenshot copied. Paste it into “What happened?” on GitHub.'
        : 'Could not copy the screenshot. Save it and use the Screenshot field on GitHub.';
    }
  });

  form.addEventListener('input', clearDraft);
  form.addEventListener('submit', event => {
    event.preventDefault();
    const fields = new FormData(form);
    const title = fields.get('title').trim();
    const body = fields.get('body').trim();
    if (!title || !body) {
      status.textContent = 'Enter a title and describe what happened.';
      return;
    }
    const selected = screenshot;
    clearDraft();
    const attempt = handoff;
    const github = new URL('https://github.com/kaiketsu-labs/artifact-studio/issues/new');
    github.searchParams.set('template', 'studio-feedback.yml');
    github.searchParams.set('title', title);
    github.searchParams.set('report-details', body);
    const copyReport = github.href.length > 7500;
    if (copyReport) {
      github.searchParams.delete('title');
      github.searchParams.delete('report-details');
    }
    function openDraft(attachmentHint = '') {
      status.textContent = [copyReport ? 'Copy your title and description into GitHub.' : '', attachmentHint].filter(Boolean).join(' ');
      draftLink.href = github.href;
      draftLink.hidden = false;
      draftLink.click();
    }
    if (!selected || !canCopy) {
      openDraft(selected ? 'Save the screenshot and use the Screenshot field on GitHub.' : '');
      return;
    }
    submit.disabled = true;
    // Clipboard writes need the Studio page to retain focus until the write completes.
    void copyScreenshot(selected).then(success => {
      if (handoff !== attempt || !dialog.open) return;
      submit.disabled = false;
      openDraft(success
        ? 'Screenshot copied. Paste it into “What happened?” on GitHub.'
        : 'Save the screenshot and use the Screenshot field on GitHub.');
    });
  });

  remove.addEventListener('click', () => {
    clearScreenshot();
    choose.focus();
  });
  dialog.querySelector('[aria-label="Close report"]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    event.stopPropagation();
    dialog.close();
  });
  dialog.addEventListener('close', () => {
    trigger.setAttribute('aria-expanded', 'false');
    generation++;
    activeStream?.getTracks().forEach(track => track.stop());
    activeStream = undefined;
    dialog.classList.remove('studio-report-capturing');
    capture.disabled = false;
    clearScreenshot();
    form.reset();
    trigger.focus();
  });
  root.SlideIcons.button(trigger, 'feedback', 'Report an issue', false);
  trigger.addEventListener('click', () => {
    if (dialog.open) {
      dialog.close();
      return;
    }
    dialog.show();
    trigger.setAttribute('aria-expanded', 'true');
    dialog.querySelector('[autofocus]').focus();
  });
  trigger.hidden = false;
})(window);
