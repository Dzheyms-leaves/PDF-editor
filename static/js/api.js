/* Thin fetch wrapper. Every backend error surfaces as a thrown Error with the
   server's own `detail` message, so callers can just toast it. */
window.API = (() => {
  async function handle(response) {
    if (response.ok) {
      const type = response.headers.get('content-type') || '';
      if (type.includes('application/json')) return response.json();
      return response;
    }
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body && body.detail) {
        detail = typeof body.detail === 'string'
          ? body.detail
          : JSON.stringify(body.detail);
      }
    } catch (_) { /* non-JSON error body */ }
    throw new Error(detail);
  }

  const json = (method) => (url, body) => fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then(handle);

  const get = (url) => fetch(url).then(handle);
  const post = json('POST');
  const del = json('DELETE');

  async function upload(url, files, field = 'files') {
    const form = new FormData();
    for (const file of files) form.append(field, file, file.name);
    return fetch(url, { method: 'POST', body: form }).then(handle);
  }

  /** POST that returns a file, triggering a browser download. */
  async function download(url, body, fallbackName) {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) return handle(response);
    await saveBlob(response, fallbackName);
  }

  async function downloadGet(url, fallbackName) {
    const response = await fetch(url);
    if (!response.ok) return handle(response);
    await saveBlob(response, fallbackName);
  }

  async function saveBlob(response, fallbackName) {
    const blob = await response.blob();
    const disposition = response.headers.get('content-disposition') || '';
    const match = /filename="?([^"]+)"?/.exec(disposition);
    const name = match ? match[1] : fallbackName || 'download';
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = href;
    anchor.download = name;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(href), 4000);
  }

  return { get, post, del, upload, download, downloadGet };
})();
