/* Draft a public issue with an optional screenshot kept in the browser. */
((root) => {
  'use strict';
  const doc = root.document;
  const trigger = doc.querySelector('[data-report-issue]');
  if (!trigger) return;
  const icon = root.SlideIcons.svg;

  const dialog = doc.createElement('dialog');
  dialog.className = 'app-about studio-report';
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
          <button type="button" data-capture>${icon('image')}<span>Capture tab</span></button>
          <button type="button" data-choose>${icon('upload')}<span>Choose screenshot</span></button>
          <input type="file" aria-label="Choose screenshot" accept="image/png,image/jpeg,image/webp" hidden>
        </div>
        <figure hidden><img alt="Screenshot preview">
          <figcaption><a download="artifact-studio-issue.png">${icon('download')}<span>Save screenshot</span></a>
            <button type="button" data-remove aria-label="Remove screenshot" title="Remove screenshot">${icon('trash')}</button></figcaption>
          <p>Save the image to attach it manually in GitHub.</p>
        </figure>
        <p role="status"></p>
      </fieldset>
      <footer class="studio-report-footer">
        <p>${icon('info')}<span>GitHub issues are public. Check your report for private information.</span></p>
        <button type="submit">Open GitHub draft ${icon('external')}</button>
      </footer>
    </form>`;
  doc.body.append(dialog);
  const form = dialog.querySelector('form');
  const capture = dialog.querySelector('[data-capture]');
  const picker = dialog.querySelector('input[type=file]');
  const choose = dialog.querySelector('[data-choose]');
  const remove = dialog.querySelector('[data-remove]');
  const figure = dialog.querySelector('figure');
  const preview = figure.querySelector('img');
  const save = figure.querySelector('a');
  const status = dialog.querySelector('[role=status]');
  let url, generation = 0, activeStream;

  function clearScreenshot() {
    if (url) root.URL.revokeObjectURL(url);
    url = undefined;
    picker.value = '';
    preview.removeAttribute('src');
    save.removeAttribute('href');
    figure.hidden = true;
    status.textContent = '';
  }

  function showScreenshot(file, extension) {
    clearScreenshot();
    url = root.URL.createObjectURL(file);
    preview.src = url;
    save.href = url;
    save.download = 'artifact-studio-issue.' + extension;
    figure.hidden = false;
  }

  choose.addEventListener('click', () => picker.click());
  picker.addEventListener('change', () => {
    const file = picker.files?.[0];
    if (!file) return;
    const extension = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}[file.type];
    if (!extension || file.size > 10_000_000) {
      picker.value = '';
      status.textContent = 'Choose a PNG, JPEG or WebP screenshot under 10 MB.' +
        (url ? ' Your previous screenshot is still selected.' : '');
      return;
    }
    showScreenshot(file, extension);
  });

  capture.hidden = typeof root.navigator.mediaDevices?.getDisplayMedia !== 'function';
  capture.addEventListener('click', async () => {
    const attempt = generation;
    const video = doc.createElement('video');
    let stream;
    capture.disabled = true;
    status.textContent = '';
    try {
      stream = await root.navigator.mediaDevices.getDisplayMedia({
        audio: false, video: {displaySurface: 'browser'}, preferCurrentTab: true,
      });
      if (generation !== attempt || !dialog.open) return;
      activeStream = stream;
      const track = stream.getVideoTracks()[0];
      if (track?.getSettings().displaySurface !== 'browser') {
        status.textContent = 'Choose a browser tab or select an existing screenshot.';
        return;
      }
      video.muted = true;
      video.srcObject = stream;
      dialog.classList.add('studio-report-capturing');
      await video.play();
      await new Promise(resolve => root.requestAnimationFrame(resolve));
      if (generation !== attempt || !dialog.open) return;
      const canvas = doc.createElement('canvas');
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      const context = canvas.getContext('2d');
      if (!context || !canvas.width || !canvas.height) throw new Error('Empty capture');
      context.drawImage(video, 0, 0);
      const file = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
      if (!file || file.size > 10_000_000) throw new Error('Invalid capture');
      if (generation === attempt && dialog.open) showScreenshot(file, 'png');
    } catch (error) {
      if (generation === attempt && dialog.open && error.name !== 'NotAllowedError') {
        status.textContent = 'Could not capture the tab. You can choose a screenshot instead.';
      }
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

  form.addEventListener('submit', event => {
    event.preventDefault();
    const fields = new FormData(form);
    const title = fields.get('title').trim();
    const body = fields.get('body').trim();
    if (!title || !body) {
      status.textContent = 'Enter a title and describe what happened.';
      return;
    }
    const github = new URL('https://github.com/kaiketsu-labs/artifact-studio/issues/new');
    github.searchParams.set('title', title);
    github.searchParams.set('body', body);
    root.open(github.href, '_blank', 'noopener,noreferrer');
  });

  remove.addEventListener('click', () => {
    clearScreenshot();
    choose.focus();
  });
  dialog.querySelector('[aria-label="Close report"]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    generation++;
    activeStream?.getTracks().forEach(track => track.stop());
    activeStream = undefined;
    dialog.classList.remove('studio-report-capturing');
    capture.disabled = false;
    clearScreenshot();
    form.reset();
    trigger.focus();
  });
  root.SlideIcons.button(trigger, 'flag', 'Report an issue', true);
  trigger.addEventListener('click', () => dialog.showModal());
  trigger.hidden = false;
})(window);
