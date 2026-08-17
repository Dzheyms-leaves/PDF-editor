/* Page viewer: rendering, zoom, and pointer interaction.

   The overlay SVG uses a viewBox in PDF points, so every shape drawn on it and
   every rect handed back to a mode is already in PDF coordinates — no manual
   scaling anywhere else in the app. */
window.Viewer = (() => {
  const SVG_NS = 'http://www.w3.org/2000/svg';

  const state = {
    doc: null,
    page: 1,
    zoom: 1,
    fit: true,
    interaction: 'none',   // none | rect | ink | quads | point
    onRect: null,
    onInk: null,
    onPoint: null,
    strokeColour: '#c1893f',
    selection: null,       // last rect in PDF coords
    selectedPages: new Set(),
  };

  const dom = {};
  function cache() {
    dom.holder = document.getElementById('page-holder');
    dom.img = document.getElementById('page-img');
    dom.overlay = document.getElementById('overlay');
    dom.wrap = document.getElementById('canvas-wrap');
    dom.empty = document.getElementById('stage-empty');
    dom.thumbs = document.getElementById('thumbs');
    dom.indicator = document.getElementById('page-indicator');
    dom.zoomLabel = document.getElementById('zoom-label');
    dom.pageCount = document.getElementById('page-count');
  }

  function pageInfo() {
    if (!state.doc) return null;
    return state.doc.pages[state.page - 1] || null;
  }

  function displayWidth() {
    const info = pageInfo();
    if (!info) return 0;
    if (state.fit) {
      const available = dom.wrap.clientWidth - 48;
      const rotated = info.rotation % 180 !== 0;
      const w = rotated ? info.height : info.width;
      return Math.max(240, Math.min(available, w * 2));
    }
    const rotated = info.rotation % 180 !== 0;
    return (rotated ? info.height : info.width) * state.zoom;
  }

  function render() {
    if (!state.doc || !state.doc.total_pages) {
      dom.holder.style.display = 'none';
      dom.empty.style.display = '';
      dom.indicator.textContent = '— / —';
      dom.pageCount.textContent = '0';
      dom.thumbs.innerHTML = '';
      return;
    }
    dom.empty.style.display = 'none';
    dom.holder.style.display = '';

    const info = pageInfo();
    if (!info) return;
    const width = Math.round(displayWidth());
    const rotated = info.rotation % 180 !== 0;
    const pw = rotated ? info.height : info.width;
    const ph = rotated ? info.width : info.height;

    state.zoom = width / pw;
    dom.img.width = width;
    dom.img.height = Math.round(width * (ph / pw));
    dom.img.src = `/api/documents/${state.doc.doc_id}/pages/${state.page}/render`
      + `?width=${Math.min(3000, Math.round(width * 2))}&_r=${state.doc.revision}`;

    dom.overlay.setAttribute('viewBox', `0 0 ${pw} ${ph}`);
    dom.overlay.setAttribute('width', width);
    dom.overlay.setAttribute('height', Math.round(width * (ph / pw)));

    dom.indicator.textContent = `${state.page} / ${state.doc.total_pages}`;
    dom.pageCount.textContent = String(state.doc.total_pages);
    dom.zoomLabel.textContent = `${Math.round(state.zoom * 100)}%`;
    clearOverlay();
  }

  function renderThumbs() {
    if (!state.doc) { dom.thumbs.innerHTML = ''; return; }
    dom.thumbs.innerHTML = '';
    for (let n = 1; n <= state.doc.total_pages; n += 1) {
      const node = UI.el('div', {
        class: 'thumb'
          + (n === state.page ? ' active' : '')
          + (state.selectedPages.has(n) ? ' selected' : ''),
        draggable: 'true',
        'data-page': n,
      }, [
        UI.el('img', {
          src: `/api/documents/${state.doc.doc_id}/pages/${n}/render?width=150&_r=${state.doc.revision}`,
          loading: 'lazy', alt: `Page ${n}`,
        }),
        UI.el('span', { class: 'thumb-no', text: String(n) }),
      ]);

      node.addEventListener('click', (event) => {
        if (event.shiftKey || event.ctrlKey || event.metaKey) {
          if (state.selectedPages.has(n)) state.selectedPages.delete(n);
          else state.selectedPages.add(n);
          renderThumbs();
          document.dispatchEvent(new CustomEvent('viewer:selection'));
        } else {
          goto(n);
        }
      });

      node.addEventListener('dragstart', (event) => {
        node.classList.add('dragging');
        event.dataTransfer.setData('text/page', String(n));
        event.dataTransfer.effectAllowed = 'move';
      });
      node.addEventListener('dragend', () => node.classList.remove('dragging'));
      node.addEventListener('dragover', (event) => {
        event.preventDefault();
        node.classList.add('drop-target');
      });
      node.addEventListener('dragleave', () => node.classList.remove('drop-target'));
      node.addEventListener('drop', async (event) => {
        event.preventDefault();
        node.classList.remove('drop-target');
        const from = parseInt(event.dataTransfer.getData('text/page'), 10);
        if (!from || from === n) return;
        document.dispatchEvent(new CustomEvent('viewer:movepage', {
          detail: { page: from, to: n },
        }));
      });

      dom.thumbs.appendChild(node);
    }
  }

  // ------------------------------------------------------------- overlay

  function clearOverlay() { dom.overlay.innerHTML = ''; }

  function shape(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
  }

  function drawRect(rect, attrs = {}) {
    const node = shape('rect', {
      x: Math.min(rect.x0, rect.x1), y: Math.min(rect.y0, rect.y1),
      width: Math.abs(rect.x1 - rect.x0), height: Math.abs(rect.y1 - rect.y0),
      fill: attrs.fill || 'rgba(193,137,63,.18)',
      stroke: attrs.stroke || '#c1893f',
      'stroke-width': attrs.width || 1,
      'stroke-dasharray': attrs.dash || '',
      ...(attrs.extra || {}),
    });
    dom.overlay.appendChild(node);
    return node;
  }

  function drawPath(points, colour) {
    if (points.length < 2) return null;
    const d = points.map((p, i) => `${i ? 'L' : 'M'}${p[0]} ${p[1]}`).join(' ');
    const node = shape('path', {
      d, fill: 'none', stroke: colour || state.strokeColour,
      'stroke-width': 1.8, 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    });
    dom.overlay.appendChild(node);
    return node;
  }

  /** Pointer position in PDF points. */
  function toPdf(event) {
    const box = dom.overlay.getBoundingClientRect();
    const info = pageInfo();
    const rotated = info.rotation % 180 !== 0;
    const pw = rotated ? info.height : info.width;
    const ph = rotated ? info.width : info.height;
    return [
      ((event.clientX - box.left) / box.width) * pw,
      ((event.clientY - box.top) / box.height) * ph,
    ];
  }

  function bindPointer() {
    let dragging = false;
    let start = null;
    let preview = null;
    let strokes = [];
    let current = [];

    dom.overlay.addEventListener('pointerdown', (event) => {
      if (state.interaction === 'none' || event.button !== 0) return;
      dom.overlay.setPointerCapture(event.pointerId);
      dragging = true;
      start = toPdf(event);

      if (state.interaction === 'ink') {
        current = [start];
      } else if (state.interaction === 'point') {
        dragging = false;
        if (state.onPoint) state.onPoint({ x: start[0], y: start[1], page: state.page });
      } else {
        clearOverlay();
        preview = drawRect({ x0: start[0], y0: start[1], x1: start[0], y1: start[1] },
          { dash: '4 3' });
      }
    });

    dom.overlay.addEventListener('pointermove', (event) => {
      if (!dragging) return;
      const now = toPdf(event);
      if (state.interaction === 'ink') {
        current.push(now);
        clearOverlay();
        [...strokes, current].forEach((s) => drawPath(s));
      } else if (preview) {
        preview.setAttribute('x', Math.min(start[0], now[0]));
        preview.setAttribute('y', Math.min(start[1], now[1]));
        preview.setAttribute('width', Math.abs(now[0] - start[0]));
        preview.setAttribute('height', Math.abs(now[1] - start[1]));
      }
    });

    dom.overlay.addEventListener('pointerup', (event) => {
      if (!dragging) return;
      dragging = false;
      const end = toPdf(event);

      if (state.interaction === 'ink') {
        if (current.length > 1) strokes.push(current);
        current = [];
        return;
      }

      const rect = {
        x0: Math.min(start[0], end[0]), y0: Math.min(start[1], end[1]),
        x1: Math.max(start[0], end[0]), y1: Math.max(start[1], end[1]),
      };
      if (Math.abs(rect.x1 - rect.x0) < 2 || Math.abs(rect.y1 - rect.y0) < 2) {
        clearOverlay();
        return;
      }
      state.selection = { ...rect, page: state.page };
      if (state.onRect) state.onRect(state.selection);
    });

    // Ink is committed explicitly, so expose the buffer to the mode.
    Viewer.takeInk = () => {
      const out = strokes.slice();
      strokes = [];
      current = [];
      clearOverlay();
      return out;
    };
    Viewer.hasInk = () => strokes.length > 0;
  }

  // -------------------------------------------------------------- public

  function setDocument(doc) {
    const changed = !state.doc || state.doc.doc_id !== doc.doc_id;
    state.doc = doc;
    if (changed) {
      state.page = 1;
      state.selectedPages.clear();
      state.fit = true;
    }
    if (state.page > doc.total_pages) state.page = doc.total_pages || 1;
    render();
    renderThumbs();
  }

  function refresh(doc) {
    if (doc) state.doc = doc;
    if (state.doc && state.page > state.doc.total_pages) {
      state.page = state.doc.total_pages || 1;
    }
    render();
    renderThumbs();
  }

  function goto(page) {
    if (!state.doc) return;
    state.page = Math.max(1, Math.min(state.doc.total_pages, page));
    render();
    renderThumbs();
    document.dispatchEvent(new CustomEvent('viewer:page', { detail: { page: state.page } }));
  }

  function setZoom(zoom) {
    state.fit = false;
    state.zoom = Math.max(0.15, Math.min(6, zoom));
    render();
  }

  function fit() { state.fit = true; render(); }

  function setInteraction(kind, handlers = {}) {
    state.interaction = kind;
    state.onRect = handlers.onRect || null;
    state.onInk = handlers.onInk || null;
    state.onPoint = handlers.onPoint || null;
    dom.overlay.style.cursor = kind === 'none' ? 'default' : 'crosshair';
    dom.overlay.style.pointerEvents = kind === 'none' ? 'none' : 'auto';
    clearOverlay();
  }

  function selectedPages() {
    if (state.selectedPages.size) return [...state.selectedPages].sort((a, b) => a - b);
    return [state.page];
  }

  function setSelectedPages(pages) {
    state.selectedPages = new Set(pages);
    renderThumbs();
  }

  function init() {
    cache();
    bindPointer();
    window.addEventListener('resize', () => { if (state.fit) render(); });
  }

  return {
    init, setDocument, refresh, goto, setZoom, fit, render, renderThumbs,
    setInteraction, clearOverlay, drawRect, drawPath, selectedPages, setSelectedPages,
    get state() { return state; },
    get page() { return state.page; },
    get doc() { return state.doc; },
  };
})();
