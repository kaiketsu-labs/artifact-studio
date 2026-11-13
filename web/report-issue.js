/* Draft a public issue with an optional screenshot kept in the browser. */
((root) => {
  'use strict';
  const doc = root.document;
  const trigger = doc.querySelector('[data-report-issue]');
  if (!trigger) return;

  const dialog = doc.createElement('dialog');
  dialog.className = 'app-about studio-report';
  dialog.setAttribute('aria-labelledby', 'studio-report-title');
  dialog.setAttribute('data-voice-exclude', '');
  dialog.innerHTML = `
    <header><h2 id="studio-report-title">Report an issue</h2>
      <button type="button" aria-label="Close report">×</button></header>
    <form>
      <label>Title<input name="title" required maxlength="120" autofocus></label>
      <label>What happened?<textarea name="body" required maxlength="1000" rows="4"></textarea></label>
      <p>GitHub issues are public. Review your text and screenshot for private information.</p>
      <div class="studio-report-actions">
        <button type="button" data-capture>Capture browser tab</button>
        <label>Choose screenshot<input type="file" accept="image/png,image/jpeg,image/webp"></label>
      </div>
      <figure hidden><img alt="Screenshot preview">
        <figcaption><a download="artifact-studio-issue.png">Save screenshot</a>
          <button type="button" data-remove>Remove screenshot</button></figcaption></figure>
      <p role="status"></p>
      <button type="submit">Open GitHub draft</button>
    </form>`;
  doc.body.append(dialog);
  const form = dialog.querySelector('form');
  const capture = dialog.querySelector('[data-capture]');
  const picker = dialog.querySelector('input[type=file]');
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
    status.textContent = 'Save the screenshot and attach it to the GitHub draft if you want to include it.';
  }

  picker.addEventListener('change', () => {
    const file = picker.files?.[0];
    if (!file) return;
    const extension = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}[file.type];
    if (!extension || file.size > 10_000_000) {
      status.textContent = 'Choose a PNG, JPEG or WebP screenshot under 10 MB.';
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

  figure.querySelector('[data-remove]').addEventListener('click', clearScreenshot);
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
