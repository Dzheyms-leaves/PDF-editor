/* Edit mode: markup tools, page surgery, forms, redaction, watermark, Bates. */
window.ModeEdit = (() => {
  const { el, field, select, number } = UI;

  const style = {
    colour: '#ffd400',
    fill: '',
    opacity: 1,
    width: 1.8,
    fontSize: 11,
    text: '',
  };
  let tool = 'none';
  let pendingCallback = null;

  const TOOLS = [
    ['none', 'Select', 'M4 3l9 9-4 1 3 4-2 1-3-4-3 3z'],
    ['highlight', 'Highlight', 'M3 12h10v3H3zM5 3h6v8H5z'],
    ['underline', 'Underline', 'M4 3v5a4 4 0 008 0V3M3 14h10'],
    ['strikeout', 'Strike', 'M4 3v4a4 4 0 008 0V3M2 8h12'],
    ['note', 'Note', 'M3 3h10v7l-3 3H3z'],
    ['ink', 'Draw', 'M2 13c3-1 4-9 7-9s2 7 5 6'],
    ['rect', 'Box', 'M3 4h10v8H3z'],
    ['circle', 'Circle', 'M8 3a5 5 0 100 10 5 5 0 000-10'],
    ['arrow', 'Arrow', 'M3 13L13 3M13 3H8M13 3v5'],
    ['freetext', 'Text box', 'M3 4h10M8 4v9'],
    ['edittext', 'Edit text', 'M3 11l7-7 2 2-7 7H3zM9 13h4'],
    ['erase', 'Erase', 'M4 12l6-6 3 3-6 6H4zM2 14h12'],
    ['image', 'Image', 'M2 4h12v8H2zM5 9l2-2 3 3 2-2'],
    ['signature', 'Sign', 'M2 12c3 0 3-6 6-6s1 4 3 4 2-2 3-2'],
    ['redact', 'Redact', 'M2 6h12v4H2z'],
    ['region', 'Copy text', 'M5 2h9v9H5zM2 5v9h9'],
  ];

  const MARKUP = new Set(['highlight', 'underline', 'strikeout', 'note',
    'rect', 'circle', 'arrow', 'freetext']);

  function doc() { return App.currentDoc(); }

  // ------------------------------------------------------------- actions

  async function commitRect(rect) {
    const id = doc()?.doc_id;
    if (!id) return;

    try {
      if (MARKUP.has(tool)) {
        await API.post(`/api/documents/${id}/annotations`, {
          kind: tool, page: rect.page, rect,
          text: tool === 'note' || tool === 'freetext' ? (style.text || '') : null,
          colour: style.colour,
          fill: style.fill || null,
          opacity: style.opacity,
          stroke_width: style.width,
          font_size: style.fontSize,
        });
        UI.ok(`${tool} added`);
      } else if (tool === 'erase') {
        await API.post(`/api/documents/${id}/content/delete`, { page: rect.page, rect });
        UI.ok('Content erased');
      } else if (tool === 'redact') {
        await API.post(`/api/documents/${id}/redact/apply`, {
          redactions: [{ page: rect.page, rect, fill: '#000000' }],
          remove_images: true, scrub_metadata: true,
        });
        UI.ok('Redacted — content permanently removed');
      } else if (tool === 'edittext') {
        await promptEditText(rect);
        return;
      } else if (tool === 'image') {
        await placeImage(rect);
        return;
      } else if (tool === 'signature') {
        await promptSignature(rect);
        return;
      } else if (tool === 'region') {
        await copyRegion(rect);
        return;
      }
      await App.reloadDoc();
      // Keep the markup list in step with what was just added.
      await loadAnnotations();
    } catch (error) {
      UI.err(error.message);
    }
    Viewer.clearOverlay();
  }

  async function promptEditText(rect) {
    const id = doc().doc_id;
    let spans = [];
    try {
      spans = await API.get(`/api/documents/${id}/pages/${rect.page}/spans`);
    } catch (_) { /* non-fatal */ }
    const existing = spans
      .filter((s) => s.rect.x1 > rect.x0 && s.rect.x0 < rect.x1
        && s.rect.y1 > rect.y0 && s.rect.y0 < rect.y1)
      .map((s) => s.text).join('');

    const input = el('textarea', { rows: 3 });
    input.value = existing;
    const close = UI.modal({
      title: 'Replace text',
      body: el('div', {}, [
        field('New text', input, 'The original font, size and colour are matched automatically.'),
      ]),
      actions: [
        { label: 'Cancel' },
        {
          label: 'Replace', kind: 'primary',
          onClick: async (dismiss) => {
            dismiss();
            try {
              const res = await API.post(`/api/documents/${id}/text/edit`, {
                page: rect.page, rect, new_text: input.value,
              });
              if (res.data?.overflowed) {
                UI.toast('Text replaced, but it overflowed the box — check the result');
              } else UI.ok('Text replaced');
              await App.reloadDoc();
            } catch (error) { UI.err(error.message); }
          },
        },
      ],
    });
    setTimeout(() => input.focus(), 60);
    return close;
  }

  async function placeImage(rect) {
    const picker = el('input', { type: 'file', accept: 'image/*' });
    picker.style.display = 'none';
    document.body.appendChild(picker);
    picker.addEventListener('change', async () => {
      if (!picker.files.length) { picker.remove(); return; }
      try {
        const asset = await API.upload('/api/assets', [picker.files[0]], 'file');
        await API.post(`/api/documents/${doc().doc_id}/images/add`, {
          page: rect.page, rect, asset_id: asset.asset_id,
        });
        UI.ok('Image placed');
        await App.reloadDoc();
      } catch (error) { UI.err(error.message); }
      picker.remove();
      Viewer.clearOverlay();
    });
    picker.click();
  }

  async function promptSignature(rect) {
    const canvas = el('canvas', {
      width: 520, height: 180,
      style: 'background:#fff; border-radius:6px; width:100%; cursor:crosshair; touch-action:none;',
    });
    const ctx = canvas.getContext('2d');
    ctx.lineWidth = 2.4; ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.strokeStyle = '#101010';
    const strokes = [];
    let drawing = false; let current = [];

    const point = (event) => {
      const box = canvas.getBoundingClientRect();
      return [
        ((event.clientX - box.left) / box.width) * canvas.width,
        ((event.clientY - box.top) / box.height) * canvas.height,
      ];
    };
    canvas.addEventListener('pointerdown', (e) => {
      drawing = true; current = [point(e)];
      canvas.setPointerCapture(e.pointerId);
      ctx.beginPath(); ctx.moveTo(...current[0]);
    });
    canvas.addEventListener('pointermove', (e) => {
      if (!drawing) return;
      const p = point(e); current.push(p);
      ctx.lineTo(...p); ctx.stroke();
    });
    canvas.addEventListener('pointerup', () => {
      if (drawing && current.length > 1) strokes.push(current);
      drawing = false;
    });

    UI.modal({
      title: 'Draw your signature',
      body: el('div', {}, [
        canvas,
        el('p', { class: 'hint', text: 'Draw above — it will be scaled into the box you selected.' }),
      ]),
      actions: [
        { label: 'Clear', onClick: () => { ctx.clearRect(0, 0, canvas.width, canvas.height); strokes.length = 0; } },
        { label: 'Cancel' },
        {
          label: 'Place', kind: 'primary',
          onClick: async (dismiss) => {
            if (!strokes.length) { UI.err('Nothing drawn yet'); return; }
            dismiss();
            // Map canvas pixels into the selected PDF rect.
            const sx = (rect.x1 - rect.x0) / canvas.width;
            const sy = (rect.y1 - rect.y0) / canvas.height;
            const mapped = strokes.map((s) => s.map(([x, y]) => [
              rect.x0 + x * sx, rect.y0 + y * sy,
            ]));
            try {
              await API.post(`/api/documents/${doc().doc_id}/signature`, {
                page: rect.page, rect, strokes: mapped, flatten: true,
              });
              UI.ok('Signature placed');
              await App.reloadDoc();
            } catch (error) { UI.err(error.message); }
          },
        },
      ],
    });
  }

  async function copyRegion(rect) {
    const id = doc().doc_id;
    try {
      const results = await API.post(`/api/documents/${id}/ocr`, {
        region: rect, region_page: rect.page, mode: 'plain',
      });
      const text = (results[0]?.text || '').trim();
      if (!text) { UI.err('No text found in that region'); return; }
      await UI.copy(text);
      UI.ok(`Copied ${text.length} characters (${results[0].engine})`);
      showOutput(text);
    } catch (error) { UI.err(error.message); }
  }

  function showOutput(text) {
    const host = document.getElementById('edit-output');
    if (host) host.textContent = text;
  }

  async function commitInk() {
    const strokes = Viewer.takeInk();
    if (!strokes.length) { UI.err('Draw something first'); return; }
    try {
      await API.post(`/api/documents/${doc().doc_id}/annotations`, {
        kind: 'ink', page: Viewer.page, points: strokes,
        colour: style.colour, stroke_width: style.width, opacity: style.opacity,
      });
      UI.ok('Drawing added');
      await App.reloadDoc();
    } catch (error) { UI.err(error.message); }
  }

  function setTool(next) {
    tool = next;
    document.querySelectorAll('#tool-grid .tool').forEach((node) => {
      node.classList.toggle('active', node.dataset.tool === next);
    });
    if (next === 'none') {
      Viewer.setInteraction('none');
    } else if (next === 'ink') {
      Viewer.setInteraction('ink');
    } else {
      Viewer.setInteraction('rect', { onRect: commitRect });
    }
    const inkBar = document.getElementById('ink-actions');
    if (inkBar) inkBar.style.display = next === 'ink' ? '' : 'none';
    const textField = document.getElementById('annot-text-field');
    if (textField) {
      textField.style.display = (next === 'note' || next === 'freetext') ? '' : 'none';
    }
  }

  async function loadAnnotations() {
    const host = document.getElementById('annot-list');
    if (!host || !doc()) return;
    let list = [];
    try {
      list = await API.get(`/api/documents/${doc().doc_id}/annotations?page=${Viewer.page}`);
    } catch (error) {
      host.innerHTML = '';
      host.appendChild(el('p', { class: 'hint', text: error.message, style: 'margin:0' }));
      return;
    }
    if (!document.getElementById('annot-list')) return;  // panel was rebuilt

    host.innerHTML = '';
    if (!list.length) {
      host.appendChild(el('p', {
        class: 'hint', style: 'margin:0',
        text: 'Nothing marked up on this page yet.',
      }));
      return;
    }

    list.forEach((annot) => {
      const label = annot.text
        ? `${annot.kind} · ${annot.text.slice(0, 28)}`
        : annot.kind;
      const row = el('div', { class: 'queue-item', style: 'cursor:default' }, [
        el('span', {
          style: `width:10px;height:10px;border-radius:2px;flex:0 0 auto;background:${annot.colour || '#c1893f'}`,
        }),
        el('span', { class: 'q-name', text: label, title: label }),
        el('button', {
          class: 'btn sm ghost', text: '⌖', title: 'Show where this is',
          onClick: () => {
            if (annot.rect) Viewer.setHighlights([annot.rect], 0);
          },
        }),
        el('button', {
          class: 'btn sm ghost', text: '✕', title: 'Delete this markup',
          onClick: async () => {
            try {
              await API.post(`/api/documents/${doc().doc_id}/annotations/delete`, {
                page: Viewer.page, indices: [annot.index],
              });
              UI.ok('Markup deleted');
              await App.reloadDoc();
              App.refreshSide();
            } catch (error) { UI.err(error.message); }
          },
        }),
      ]);
      host.appendChild(row);
    });

    host.appendChild(el('button', {
      class: 'btn sm danger', style: 'width:100%; margin-top:6px',
      text: `Delete all ${list.length} on this page`,
      onClick: async () => {
        if (!await UI.confirm(`Delete all markup on page ${Viewer.page}?`, { danger: true })) return;
        try {
          await API.post(`/api/documents/${doc().doc_id}/annotations/delete`, {
            page: Viewer.page, indices: list.map((a) => a.index),
          });
          UI.ok('Markup cleared');
          await App.reloadDoc();
          App.refreshSide();
        } catch (error) { UI.err(error.message); }
      },
    }));
  }

  // ---------------------------------------------------------------- page ops

  async function pageOp(path, body, label) {
    const id = doc()?.doc_id;
    if (!id) return;
    try {
      await API.post(`/api/documents/${id}/pages/${path}`, body);
      UI.ok(label);
      await App.reloadDoc();
    } catch (error) { UI.err(error.message); }
  }

  // ------------------------------------------------------------------ panel

  function build(host) {
    host.innerHTML = '';
    if (!doc()) {
      host.appendChild(el('p', { class: 'empty', text: 'Open a PDF to start editing.' }));
      return;
    }

    // Tools
    host.appendChild(el('h2', { class: 'section', text: 'Tools' }));
    const grid = el('div', { class: 'tool-grid', id: 'tool-grid' },
      TOOLS.map(([id, label, path]) => el('button', {
        class: `tool${tool === id ? ' active' : ''}`, 'data-tool': id, title: label,
        onClick: () => setTool(id),
      }, [
        (() => {
          const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
          svg.setAttribute('viewBox', '0 0 16 16');
          const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
          p.setAttribute('d', path);
          svg.appendChild(p);
          return svg;
        })(),
        el('span', { text: label }),
      ])));
    host.appendChild(grid);

    host.appendChild(el('div', { class: 'row', id: 'ink-actions', style: 'display:none; margin-bottom:12px;' }, [
      el('button', { class: 'btn sm primary', text: 'Commit drawing', onClick: commitInk }),
      el('button', { class: 'btn sm', text: 'Discard', onClick: () => Viewer.takeInk() }),
    ]));

    // Style
    const colour = el('input', { type: 'color', value: style.colour,
      onInput: (e) => { style.colour = e.target.value; } });
    const opacity = el('input', { type: 'range', min: '0.1', max: '1', step: '0.05',
      value: style.opacity, onInput: (e) => { style.opacity = parseFloat(e.target.value); } });
    const width = number(style.width, (v) => { style.width = v || 1; },
      { min: '0.2', max: '12', step: '0.2' });

    host.appendChild(el('div', { class: 'field inline' }, [
      el('label', { text: 'Colour' }), colour,
      el('label', { text: 'Line', style: 'flex:0 0 auto' }), width,
    ]));
    host.appendChild(field('Opacity', opacity));

    const annotText = el('input', { type: 'text', placeholder: 'Note or text-box content',
      onInput: (e) => { style.text = e.target.value; } });
    const textWrap = field('Text content', annotText);
    textWrap.id = 'annot-text-field';
    textWrap.style.display = 'none';
    host.appendChild(textWrap);

    host.appendChild(el('div', { class: 'out', id: 'edit-output',
      text: 'Region text will appear here.', style: 'max-height:120px; margin-bottom:6px;' }));

    // Markup already on this page
    host.appendChild(el('h2', { class: 'section', text: 'Markup on this page' }));
    const annotHost = el('div', { id: 'annot-list' }, [
      el('p', { class: 'hint', text: 'Loading…', style: 'margin:0' }),
    ]);
    host.appendChild(annotHost);
    loadAnnotations();

    // Page operations
    host.appendChild(el('h2', { class: 'section', text: 'Pages' }));
    host.appendChild(el('p', { class: 'hint',
      text: 'Ctrl/Shift-click thumbnails to select several. Drag a thumbnail to reorder.' }));
    host.appendChild(el('div', { class: 'row tight' }, [
      el('button', { class: 'btn sm', text: 'Rotate ⟳', onClick: () => pageOp('rotate', { pages: Viewer.selectedPages(), degrees: 90 }, 'Rotated') }),
      el('button', { class: 'btn sm', text: 'Rotate ⟲', onClick: () => pageOp('rotate', { pages: Viewer.selectedPages(), degrees: -90 }, 'Rotated') }),
      el('button', { class: 'btn sm', text: 'Duplicate', onClick: () => pageOp('duplicate', { pages: Viewer.selectedPages() }, 'Duplicated') }),
      el('button', { class: 'btn sm', text: 'Blank after', onClick: () => pageOp('insert-blank', { after_page: Viewer.page, count: 1 }, 'Blank page inserted') }),
      el('button', { class: 'btn sm danger', text: 'Delete', onClick: async () => {
        const pages = Viewer.selectedPages();
        if (await UI.confirm(`Delete page${pages.length > 1 ? 's' : ''} ${pages.join(', ')}?`, { danger: true })) {
          Viewer.setSelectedPages([]);
          pageOp('delete', { pages }, 'Deleted');
        }
      } }),
    ]));

    host.appendChild(el('div', { class: 'row tight', style: 'margin-top:8px' }, [
      el('button', { class: 'btn sm', text: 'Extract selected', onClick: () => {
        API.download(`/api/documents/${doc().doc_id}/extract`, { pages: Viewer.selectedPages() })
          .catch((e) => UI.err(e.message));
      } }),
      el('button', { class: 'btn sm', text: 'Split…', onClick: splitDialog }),
      el('button', { class: 'btn sm', text: 'Merge…', onClick: mergeDialog }),
    ]));

    // Document tools
    host.appendChild(el('h2', { class: 'section', text: 'Document' }));
    host.appendChild(el('div', { class: 'row tight' }, [
      el('button', { class: 'btn sm', text: 'Find & replace', onClick: findReplaceDialog }),
      el('button', { class: 'btn sm', text: 'Watermark', onClick: watermarkDialog }),
      el('button', { class: 'btn sm', text: 'Bates', onClick: batesDialog }),
      el('button', { class: 'btn sm', text: 'Redact text', onClick: redactDialog }),
      el('button', { class: 'btn sm', text: 'Form fields', onClick: formDialog }),
      el('button', { class: 'btn sm', text: 'Flatten markup', onClick: async () => {
        try {
          await API.post(`/api/documents/${doc().doc_id}/annotations/flatten`, null);
          UI.ok('Annotations flattened into the page');
          await App.reloadDoc();
        } catch (e) { UI.err(e.message); }
      } }),
      el('button', { class: 'btn sm', text: 'Make searchable', onClick: async () => {
        try {
          const res = await API.post(`/api/documents/${doc().doc_id}/ocr/searchable`,
            { pages: { mode: 'all' } });
          UI.ok(`Text layer added to ${res.data.pages_updated} page(s)`);
          await App.reloadDoc();
        } catch (e) { UI.err(e.message); }
      } }),
    ]));
  }

  // ------------------------------------------------------------- dialogs

  function splitDialog() {
    const mode = select([['every_n', 'Every N pages'], ['at_pages', 'At page numbers'], ['ranges', 'Custom ranges']], 'every_n', () => {});
    const n = number(1, () => {}, { min: '1' });
    const spec = el('input', { type: 'text', placeholder: '1-3, 4-8' });
    UI.modal({
      title: 'Split document',
      body: el('div', {}, [
        field('Mode', mode), field('Every N pages', n),
        field('Pages / ranges', spec, 'Used by the "at page numbers" and "custom ranges" modes.'),
      ]),
      actions: [{ label: 'Cancel' }, {
        label: 'Split', kind: 'primary',
        onClick: async (close) => {
          close();
          const body = { mode: mode.value, every_n: parseInt(n.value, 10) || 1, at_pages: [], ranges: [] };
          if (mode.value === 'at_pages') {
            body.at_pages = spec.value.split(',').map((s) => parseInt(s.trim(), 10)).filter(Boolean);
          } else if (mode.value === 'ranges') {
            body.ranges = spec.value.split(',').map((s) => s.trim()).filter(Boolean);
          }
          try { await API.download(`/api/documents/${doc().doc_id}/split`, body); }
          catch (e) { UI.err(e.message); }
        },
      }],
    });
  }

  function mergeDialog() {
    const others = App.documents().filter((d) => d.doc_id !== doc().doc_id);
    if (!others.length) { UI.err('Open another PDF first to merge it in'); return; }
    const boxes = others.map((d) => {
      const cb = el('input', { type: 'checkbox', value: d.doc_id });
      return { cb, node: el('label', { class: 'row', style: 'margin-bottom:6px; cursor:pointer' }, [cb, el('span', { text: `${d.filename} (${d.total_pages}p)` })]) };
    });
    UI.modal({
      title: 'Merge into this document',
      body: el('div', {}, boxes.map((b) => b.node)),
      actions: [{ label: 'Cancel' }, {
        label: 'Merge', kind: 'primary',
        onClick: async (close) => {
          const ids = boxes.filter((b) => b.cb.checked).map((b) => b.cb.value);
          if (!ids.length) { UI.err('Nothing selected'); return; }
          close();
          try {
            await API.post(`/api/documents/${doc().doc_id}/merge`, { doc_ids: ids });
            UI.ok('Merged');
            await App.reloadDoc();
          } catch (e) { UI.err(e.message); }
        },
      }],
    });
  }

  function findReplaceDialog() {
    const findInput = el('input', { type: 'text' });
    const replaceInput = el('input', { type: 'text' });
    const caseBox = el('input', { type: 'checkbox' });
    UI.modal({
      title: 'Find and replace',
      body: el('div', {}, [
        field('Find', findInput), field('Replace with', replaceInput),
        el('label', { class: 'row' }, [caseBox, el('span', { text: 'Match case' })]),
        el('p', { class: 'hint', text: 'Each hit keeps its original font, size and colour.' }),
      ]),
      actions: [{ label: 'Cancel' }, {
        label: 'Replace all', kind: 'primary',
        onClick: async (close) => {
          close();
          try {
            const res = await API.post(`/api/documents/${doc().doc_id}/text/replace`, {
              find: findInput.value, replace: replaceInput.value,
              pages: { mode: 'all' }, match_case: caseBox.checked,
            });
            UI.ok(`Replaced ${res.data.replaced} occurrence(s)`);
            await App.reloadDoc();
          } catch (e) { UI.err(e.message); }
        },
      }],
    });
  }

  function watermarkDialog() {
    const text = el('input', { type: 'text', value: 'DRAFT' });
    const colour = el('input', { type: 'color', value: '#b00020' });
    const opacity = el('input', { type: 'range', min: '0.03', max: '1', step: '0.02', value: '0.15' });
    const position = select([['center', 'Centre'], ['tile', 'Tiled'], ['top', 'Top'], ['bottom', 'Bottom']], 'center', () => {});
    UI.modal({
      title: 'Add watermark',
      body: el('div', {}, [field('Text', text), field('Colour', colour), field('Opacity', opacity), field('Position', position)]),
      actions: [{ label: 'Cancel' }, {
        label: 'Apply', kind: 'primary',
        onClick: async (close) => {
          close();
          try {
            await API.post(`/api/documents/${doc().doc_id}/watermark`, {
              text: text.value, pages: { mode: 'all' },
              opacity: parseFloat(opacity.value), colour: colour.value, position: position.value,
            });
            UI.ok('Watermark applied');
            await App.reloadDoc();
          } catch (e) { UI.err(e.message); }
        },
      }],
    });
  }

  function batesDialog() {
    const prefix = el('input', { type: 'text', value: 'AES-' });
    const start = number(1, () => {}, { min: '0' });
    const digits = number(6, () => {}, { min: '1', max: '12' });
    const position = select([
      ['bottom-right', 'Bottom right'], ['bottom-left', 'Bottom left'],
      ['bottom-center', 'Bottom centre'], ['top-right', 'Top right'],
    ], 'bottom-right', () => {});
    UI.modal({
      title: 'Bates numbering',
      body: el('div', {}, [field('Prefix', prefix), field('Start at', start), field('Digits', digits), field('Position', position)]),
      actions: [{ label: 'Cancel' }, {
        label: 'Apply', kind: 'primary',
        onClick: async (close) => {
          close();
          try {
            const res = await API.post(`/api/documents/${doc().doc_id}/bates`, {
              prefix: prefix.value, start: parseInt(start.value, 10) || 1,
              digits: parseInt(digits.value, 10) || 6,
              position: position.value, pages: { mode: 'all' },
            });
            UI.ok(`Stamped ${res.data.first} … ${res.data.last}`);
            await App.reloadDoc();
          } catch (e) { UI.err(e.message); }
        },
      }],
    });
  }

  function redactDialog() {
    const patterns = el('textarea', { rows: 4, placeholder: 'One per line.\nWrap in / / for a regex, e.g. /\\d{3}-\\d{4}/' });
    UI.modal({
      title: 'Find and redact text',
      body: el('div', {}, [
        field('Patterns', patterns),
        el('p', { class: 'hint', text: 'Redaction permanently removes the content — it is not a black box over the top. This cannot be undone except via Undo.' }),
      ]),
      actions: [{ label: 'Cancel' }, {
        label: 'Find & redact', kind: 'danger',
        onClick: async (close) => {
          close();
          const list = patterns.value.split('\n').map((s) => s.trim()).filter(Boolean);
          if (!list.length) { UI.err('No patterns given'); return; }
          try {
            const res = await API.post(`/api/documents/${doc().doc_id}/redact/find`, {
              patterns: list, pages: { mode: 'all' }, apply_now: true,
            });
            UI.ok(`Redacted ${res.count} match(es)`);
            await App.reloadDoc();
          } catch (e) { UI.err(e.message); }
        },
      }],
    });
  }

  async function formDialog() {
    let fields = [];
    try { fields = await API.get(`/api/documents/${doc().doc_id}/form/fields`); }
    catch (e) { UI.err(e.message); return; }
    if (!fields.length) { UI.toast('This PDF has no form fields'); return; }

    const inputs = new Map();
    const body = el('div', {}, fields.map((f) => {
      let control;
      if (f.field_type === 'checkbox') {
        control = el('input', { type: 'checkbox' });
        control.checked = Boolean(f.value);
      } else if (f.options && f.options.length) {
        control = select(f.options.map((o) => [o, o]), f.value || f.options[0], () => {});
      } else {
        control = el('input', { type: 'text', value: f.value ?? '' });
      }
      inputs.set(f.name, { control, type: f.field_type });
      return field(`${f.name} · p${f.page}${f.read_only ? ' (read-only)' : ''}`, control);
    }));

    const flatten = el('input', { type: 'checkbox' });
    body.appendChild(el('label', { class: 'row' }, [flatten, el('span', { text: 'Flatten after filling (makes values permanent)' })]));

    UI.modal({
      title: `Form fields (${fields.length})`,
      body,
      actions: [{ label: 'Cancel' }, {
        label: 'Fill', kind: 'primary',
        onClick: async (close) => {
          close();
          const values = {};
          inputs.forEach(({ control, type }, name) => {
            values[name] = type === 'checkbox' ? control.checked : control.value;
          });
          try {
            await API.post(`/api/documents/${doc().doc_id}/form/fill`, { values, flatten: flatten.checked });
            UI.ok('Form updated');
            await App.reloadDoc();
          } catch (e) { UI.err(e.message); }
        },
      }],
    });
  }

  return {
    id: 'edit',
    activate(host) { build(host); setTool(tool); },
    deactivate() { Viewer.setInteraction('none'); },
    refresh(host) { build(host); setTool(tool); },
    onPage() { loadAnnotations(); },
  };
})();
