/* Application shell: document tabs, mode switching, global keys, settings. */
window.App = (() => {
  const { el, field, select } = UI;

  const state = {
    docs: [],
    currentId: null,
    mode: 'edit',
  };

  const MODES = {
    edit: () => ModeEdit,
    po: () => ModePO,
    panels: () => ModePanels,
    stamp: () => ModeStamp,
  };

  function currentDoc() {
    return state.docs.find((d) => d.doc_id === state.currentId) || null;
  }
  function documents() { return state.docs; }
  function mode() { return MODES[state.mode](); }

  // ---------------------------------------------------------------- tabs

  function renderTabs() {
    const host = document.getElementById('doc-tabs');
    host.innerHTML = '';
    state.docs.forEach((d) => {
      const tab = el('div', {
        class: `doc-tab${d.doc_id === state.currentId ? ' active' : ''}`,
        title: `${d.filename} — ${d.total_pages} pages`,
      }, [
        el('span', { class: 'tab-name', text: d.filename }),
        el('span', { class: 'tab-close', text: '✕' }),
      ]);
      tab.querySelector('.tab-name').addEventListener('click', () => select_(d.doc_id));
      tab.querySelector('.tab-close').addEventListener('click', async (event) => {
        event.stopPropagation();
        await closeDoc(d.doc_id);
      });
      host.appendChild(tab);
    });

    const has = Boolean(currentDoc());
    document.getElementById('btn-save').disabled = !has;
    document.getElementById('btn-undo').disabled = !has || !currentDoc().can_undo;
    document.getElementById('btn-redo').disabled = !has || !currentDoc().can_redo;
    document.getElementById('btn-clear-all').disabled = state.docs.length === 0;
  }

  async function clearAll() {
    if (!state.docs.length) return;
    const count = state.docs.length;
    const confirmed = await UI.confirm(
      `Close all ${count} open PDF${count === 1 ? '' : 's'}? `
      + 'Anything you have not saved will be lost.',
      { title: 'Clear all PDFs', danger: true },
    );
    if (!confirmed) return;
    try {
      await API.del('/api/documents');
    } catch (error) {
      UI.err(error.message);
      return;
    }
    state.docs = [];
    state.currentId = null;
    Viewer.refresh(null);
    Viewer.setHighlights([]);
    closeFind();
    renderTabs();
    refreshSide();
    UI.ok(`Closed ${count} PDF${count === 1 ? '' : 's'}`);
  }

  function select_(docId) {
    state.currentId = docId;
    const doc = currentDoc();
    if (doc) Viewer.setDocument(doc);
    renderTabs();
    refreshSide();
  }

  async function closeDoc(docId) {
    try { await API.del(`/api/documents/${docId}`); } catch (_) { /* already gone */ }
    state.docs = state.docs.filter((d) => d.doc_id !== docId);
    if (state.currentId === docId) state.currentId = state.docs[0]?.doc_id || null;
    const doc = currentDoc();
    if (doc) Viewer.setDocument(doc); else Viewer.refresh(null);
    renderTabs();
    refreshSide();
  }

  async function openFiles(files) {
    const pdfs = [...files].filter((f) => f.name.toLowerCase().endsWith('.pdf'));
    if (!pdfs.length) { UI.err('No PDFs in that drop'); return; }
    try {
      const added = await API.upload('/api/documents', pdfs);
      state.docs.push(...added);
      if (!state.currentId && added.length) state.currentId = added[0].doc_id;
      const doc = currentDoc();
      if (doc) Viewer.setDocument(doc);
      renderTabs();
      refreshSide();
      UI.ok(`Opened ${added.length} PDF${added.length === 1 ? '' : 's'}`);
    } catch (error) { UI.err(error.message); }
  }

  /** Re-read the current document from the server after an edit. */
  async function reloadDoc() {
    const id = state.currentId;
    if (!id) return;
    try {
      const fresh = await API.get(`/api/documents/${id}`);
      const index = state.docs.findIndex((d) => d.doc_id === id);
      if (index >= 0) state.docs[index] = fresh;
      Viewer.refresh(fresh);
      renderTabs();
    } catch (error) { UI.err(error.message); }
  }

  // --------------------------------------------------------------- modes

  function setMode(next) {
    if (state.mode === next) return;
    const previous = mode();
    if (previous?.deactivate) previous.deactivate();
    state.mode = next;
    document.querySelectorAll('.mode-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.mode === next);
    });
    const host = document.getElementById('side-body');
    host.innerHTML = '';
    const current = mode();
    if (current?.activate) current.activate(host);
  }

  function refreshSide() {
    const host = document.getElementById('side-body');
    const current = mode();
    if (current?.refresh) current.refresh(host);
    else if (current?.activate) current.activate(host);
  }

  function setStageExtra(nodes) {
    const host = document.getElementById('stage-extra');
    host.innerHTML = '';
    [].concat(nodes || []).forEach((n) => host.appendChild(n));
  }

  // ---------------------------------------------------------------- find

  const find = { hits: [], index: -1, term: '' };

  function openFind() {
    document.getElementById('findbar').style.display = '';
    const input = document.getElementById('find-input');
    input.focus();
    input.select();
  }

  function closeFind() {
    document.getElementById('findbar').style.display = 'none';
    find.hits = [];
    find.index = -1;
    find.term = '';
    document.getElementById('find-count').textContent = '—';
    Viewer.setHighlights([]);
  }

  async function runFind(term) {
    const doc = currentDoc();
    const count = document.getElementById('find-count');
    if (!doc || !term.trim()) {
      find.hits = [];
      find.index = -1;
      count.textContent = '—';
      Viewer.setHighlights([]);
      return;
    }
    try {
      const response = await API.post(`/api/documents/${doc.doc_id}/text/find`, {
        find: term, replace: '', pages: { mode: 'all' }, limit: 500,
      });
      find.hits = response.hits || [];
      find.term = term;
      find.index = find.hits.length ? 0 : -1;
      count.textContent = find.hits.length ? `1 of ${find.hits.length}` : 'no matches';
      if (find.hits.length) showHit(0);
      else Viewer.setHighlights([]);
    } catch (error) {
      UI.err(error.message);
    }
  }

  function showHit(index) {
    if (!find.hits.length) return;
    find.index = (index + find.hits.length) % find.hits.length;
    const hit = find.hits[find.index];
    document.getElementById('find-count').textContent =
      `${find.index + 1} of ${find.hits.length}`;
    if (Viewer.page !== hit.page) Viewer.goto(hit.page);
    // Highlight every hit on this page, emphasising the current one.
    const onPage = find.hits.filter((h) => h.page === hit.page);
    Viewer.setHighlights(onPage.map((h) => h.rect), onPage.indexOf(hit));
  }

  function shortcutsDialog() {
    const rows = [
      ['Ctrl/Cmd + Z', 'Undo'],
      ['Ctrl/Cmd + Y', 'Redo'],
      ['Ctrl/Cmd + S', 'Save a copy of the PDF'],
      ['Ctrl/Cmd + F', 'Find in document'],
      ['Enter / Shift+Enter', 'Next / previous match'],
      ['Esc', 'Close find or dialog'],
      ['Page Up / Page Down', 'Previous / next page'],
      ['Ctrl/Shift + click thumbnail', 'Select several pages'],
      ['Drag thumbnail', 'Reorder pages'],
      ['1 – 9  (Panels mode)', 'Copy that button label'],
      ['N  (Panels mode)', 'Mark panel done and move on'],
    ];
    UI.modal({
      title: 'Keyboard shortcuts',
      body: el('dl', { class: 'kv', style: 'grid-template-columns:190px 1fr' },
        rows.flatMap(([key, what]) => [
          el('dt', { text: key, style: 'text-transform:none; font-family:var(--font-display)' }),
          el('dd', { text: what, style: 'cursor:default' }),
        ])),
      actions: [{ label: 'Close' }],
    });
  }

  // ------------------------------------------------------------ settings

  async function settingsDialog() {
    let caps; let settings;
    try {
      [caps, settings] = await Promise.all([
        API.get('/api/ocr/capabilities'),
        API.get('/api/settings'),
      ]);
    } catch (error) { UI.err(error.message); return; }

    const engineRows = caps.engines.map((e) => el('div', { class: 'engine-row' }, [
      el('span', { class: `status-dot${e.available ? ' on' : ''}` }),
      el('span', { class: 'eng-name', text: e.label }),
      el('span', { class: 'eng-detail', text: e.available ? e.detail : (e.detail + (e.install_hint ? ` — ${e.install_hint}` : '')) }),
    ]));

    const engineSelect = select(
      [['auto', 'Automatic (best available)'],
        ...caps.engines.filter((e) => e.name !== 'native').map((e) => [e.name, e.label])],
      settings.ocr_engine || 'auto', () => {},
    );
    const modeSelect = select(
      [['tiny', 'Tiny — fastest'], ['small', 'Small'], ['base', 'Base'],
        ['large', 'Large — most accurate'], ['gundam', 'Gundam — dynamic tiling (recommended)']],
      settings.deepseek_mode || 'gundam', () => {},
    );
    const deviceSelect = select(
      [['auto', 'Automatic'], ['cuda', 'Force CUDA'], ['mps', 'Apple Metal'], ['cpu', 'Force CPU (slow)']],
      settings.deepseek_device || 'auto', () => {},
    );
    const dpi = el('input', { type: 'number', value: settings.ocr_render_dpi || 200, min: '72', max: '600', step: '10' });
    const localPath = el('input', { type: 'text', value: settings.deepseek_local_path || '',
      placeholder: 'Optional: folder holding a downloaded DeepSeek-OCR' });
    const remoteUrl = el('input', { type: 'text', value: settings.ocr_remote_url || '',
      placeholder: 'https://gpu-box.local:8000/ocr' });
    const company = el('input', { type: 'text',
      value: (settings.my_company_names || []).join(', '),
      placeholder: 'Automated Electrical Solutions, AES' });

    const gpuLine = caps.gpu_available
      ? `GPU detected: ${caps.gpu_name}`
      : (caps.torch_version ? 'PyTorch installed, but no CUDA GPU is visible' : 'PyTorch is not installed');

    UI.modal({
      title: 'Settings',
      body: el('div', {}, [
        el('h2', { class: 'section', text: 'OCR engines', style: 'margin-top:0' }),
        el('p', { class: 'hint', text: gpuLine }),
        ...engineRows,
        el('h2', { class: 'section', text: 'Preferences' }),
        field('Preferred engine', engineSelect),
        field('DeepSeek-OCR resolution', modeSelect,
          'Gundam tiles the page dynamically and handles dense purchase orders best.'),
        field('DeepSeek-OCR device', deviceSelect),
        field('Render resolution (DPI)', dpi, 'Higher is more accurate and slower. 200 suits most POs.'),
        field('Local model folder', localPath),
        field('Remote OCR endpoint', remoteUrl,
          'Leave blank to keep everything on this machine.'),
        el('h2', { class: 'section', text: 'Purchase orders' }),
        field('Our company names', company,
          'Used to tell which party on a PO is us, so the other one is reported as the counterparty.'),
      ]),
      actions: [
        { label: 'Close' },
        {
          label: 'Save', kind: 'primary',
          onClick: async (close) => {
            try {
              await API.post('/api/settings', {
                ocr_engine: engineSelect.value,
                deepseek_mode: modeSelect.value,
                deepseek_device: deviceSelect.value,
                ocr_render_dpi: parseInt(dpi.value, 10) || 200,
                deepseek_local_path: localPath.value.trim(),
                ocr_remote_url: remoteUrl.value.trim(),
                my_company_names: company.value.split(',').map((s) => s.trim()).filter(Boolean),
              });
              UI.ok('Settings saved');
              close();
            } catch (error) { UI.err(error.message); }
          },
        },
      ],
    });
  }

  // ---------------------------------------------------------------- init

  function bindGlobal() {
    document.getElementById('btn-open').addEventListener('click',
      () => document.getElementById('file-input').click());
    document.getElementById('file-input').addEventListener('change', (event) => {
      openFiles(event.target.files);
      event.target.value = '';
    });

    document.querySelectorAll('.mode-btn').forEach((btn) => {
      btn.addEventListener('click', () => setMode(btn.dataset.mode));
    });

    document.getElementById('btn-save').addEventListener('click', () => {
      const doc = currentDoc();
      if (doc) API.downloadGet(`/api/documents/${doc.doc_id}/download`, doc.filename);
    });
    document.getElementById('btn-settings').addEventListener('click', settingsDialog);

    document.getElementById('btn-undo').addEventListener('click', async () => {
      const doc = currentDoc();
      if (!doc) return;
      await API.post(`/api/documents/${doc.doc_id}/undo`);
      await reloadDoc();
      refreshSide();
    });
    document.getElementById('btn-redo').addEventListener('click', async () => {
      const doc = currentDoc();
      if (!doc) return;
      await API.post(`/api/documents/${doc.doc_id}/redo`);
      await reloadDoc();
      refreshSide();
    });

    document.getElementById('btn-prev').addEventListener('click', () => Viewer.goto(Viewer.page - 1));
    document.getElementById('btn-next').addEventListener('click', () => Viewer.goto(Viewer.page + 1));
    document.getElementById('btn-zoom-in').addEventListener('click', () => Viewer.setZoom(Viewer.state.zoom * 1.25));
    document.getElementById('btn-zoom-out').addEventListener('click', () => Viewer.setZoom(Viewer.state.zoom / 1.25));
    document.getElementById('btn-zoom-fit').addEventListener('click', () => Viewer.fit('page'));
    document.getElementById('btn-zoom-width').addEventListener('click', () => Viewer.fit('width'));
    document.getElementById('btn-clear-all').addEventListener('click', clearAll);
    document.getElementById('btn-help').addEventListener('click', shortcutsDialog);

    // Find
    document.getElementById('btn-find').addEventListener('click', openFind);
    document.getElementById('find-close').addEventListener('click', closeFind);
    document.getElementById('find-next').addEventListener('click', () => showHit(find.index + 1));
    document.getElementById('find-prev').addEventListener('click', () => showHit(find.index - 1));

    let findTimer = null;
    const findInput = document.getElementById('find-input');
    findInput.addEventListener('input', (event) => {
      clearTimeout(findTimer);
      const term = event.target.value;
      findTimer = setTimeout(() => runFind(term), 260);
    });
    findInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        if (find.term !== findInput.value) runFind(findInput.value);
        else showHit(find.index + (event.shiftKey ? -1 : 1));
      } else if (event.key === 'Escape') {
        event.preventDefault();
        closeFind();
      }
    });

    // Selectable text layer
    const textBtn = document.getElementById('btn-select-text');
    textBtn.addEventListener('click', () => {
      const on = !Viewer.state.textLayerOn;
      Viewer.setTextLayer(on);
      textBtn.classList.toggle('active', on);
      if (on) UI.toast('Text selection on — drag over the page, then Ctrl+C');
    });
    document.getElementById('btn-sel-all').addEventListener('click', () => {
      const doc = currentDoc();
      if (doc) Viewer.setSelectedPages(Array.from({ length: doc.total_pages }, (_, i) => i + 1));
    });
    document.getElementById('btn-sel-none').addEventListener('click', () => Viewer.setSelectedPages([]));

    // Page reordering by thumbnail drag
    document.addEventListener('viewer:movepage', async (event) => {
      const doc = currentDoc();
      if (!doc) return;
      try {
        await API.post(`/api/documents/${doc.doc_id}/pages/move`, {
          page: event.detail.page, to_index: event.detail.to,
        });
        await reloadDoc();
      } catch (error) { UI.err(error.message); }
    });

    document.addEventListener('viewer:page', () => {
      const current = mode();
      if (current?.onPage) current.onPage();
    });

    // Drag and drop anywhere
    let dragDepth = 0;
    let dragNode = null;
    window.addEventListener('dragenter', (event) => {
      event.preventDefault();
      dragDepth += 1;
      if (!dragNode) {
        dragNode = el('div', { class: 'drag-overlay', text: 'Drop PDFs to open' });
        document.body.appendChild(dragNode);
      }
    });
    window.addEventListener('dragover', (event) => event.preventDefault());
    window.addEventListener('dragleave', (event) => {
      event.preventDefault();
      dragDepth -= 1;
      if (dragDepth <= 0 && dragNode) { dragNode.remove(); dragNode = null; dragDepth = 0; }
    });
    window.addEventListener('drop', (event) => {
      event.preventDefault();
      dragDepth = 0;
      if (dragNode) { dragNode.remove(); dragNode = null; }
      if (event.dataTransfer?.files?.length) openFiles(event.dataTransfer.files);
    });

    // Keyboard
    document.addEventListener('keydown', (event) => {
      const tag = (event.target.tagName || '').toLowerCase();
      const typing = tag === 'input' || tag === 'textarea' || tag === 'select';
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z' && !event.shiftKey) {
        event.preventDefault();
        document.getElementById('btn-undo').click();
      } else if ((event.ctrlKey || event.metaKey)
        && (event.key.toLowerCase() === 'y' || (event.shiftKey && event.key.toLowerCase() === 'z'))) {
        event.preventDefault();
        document.getElementById('btn-redo').click();
      } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault();
        document.getElementById('btn-save').click();
      } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f') {
        event.preventDefault();
        openFind();
      } else if (event.key === 'Escape' && !typing) {
        closeFind();
      } else if (!typing && event.key === 'PageDown') {
        Viewer.goto(Viewer.page + 1);
      } else if (!typing && event.key === 'PageUp') {
        Viewer.goto(Viewer.page - 1);
      }
    });
  }

  async function init() {
    Viewer.init();
    bindGlobal();
    try {
      state.docs = await API.get('/api/documents');
      if (state.docs.length) select_(state.docs[0].doc_id);
    } catch (_) { /* fresh session */ }
    renderTabs();
    setMode('edit');
    const host = document.getElementById('side-body');
    ModeEdit.activate(host);
  }

  return {
    init, currentDoc, documents, reloadDoc, refreshSide, setStageExtra,
    openFiles, select: select_,
  };
})();

document.addEventListener('DOMContentLoaded', () => App.init());
