/* Purchase-order mode: structured extraction with click-to-copy everywhere.

   The whole point is speed: read the PO, click the cell you need, paste it into
   the ordering system. Every cell, every header field, every row and the whole
   table are one click from the clipboard. */
window.ModePO = (() => {
  const { el, field, select } = UI;

  let result = null;
  let engines = [];
  let forceOcr = false;
  let engineChoice = '';
  let resultsHost = null;
  let edited = false;      // a cell has been corrected by hand
  let batch = [];          // results when several POs are read at once

  const COLUMNS = [
    ['line_no', '#', 42],
    ['part_code', 'Part code', 130],
    ['description', 'Description', 300],
    ['quantity', 'Qty', 60],
    ['unit', 'Unit', 55],
    ['unit_price', 'Unit price', 90],
    ['discount', 'Disc', 60],
    ['tax', 'Tax', 60],
    ['total_price', 'Line total', 95],
    ['due_date', 'Due', 90],
  ];

  const HEADER_FIELDS = [
    ['po_number', 'PO number'],
    ['order_date', 'Order date'],
    ['required_date', 'Required'],
    ['supplier', 'From'],
    ['ship_to', 'Ship to'],
    ['reference', 'Reference'],
    ['currency', 'Currency'],
    ['subtotal', 'Subtotal'],
    ['tax', 'Tax'],
    ['total', 'Total'],
  ];

  function doc() { return App.currentDoc(); }

  function ensureResultsHost() {
    if (resultsHost && resultsHost.isConnected) return resultsHost;
    resultsHost = el('div', {
      id: 'po-results',
      style: 'display:none; width:100%; max-width:1100px; align-self:flex-start;',
    });
    document.getElementById('canvas-wrap').appendChild(resultsHost);
    return resultsHost;
  }

  function showTable(show) {
    const holder = document.getElementById('page-holder');
    const host = ensureResultsHost();
    host.style.display = show ? '' : 'none';
    if (holder) holder.style.display = show ? 'none' : '';
    const toggle = document.getElementById('po-view-toggle');
    if (toggle) toggle.textContent = show ? 'Show page' : 'Show table';
  }

  // ------------------------------------------------------------- extract

  async function extract(button) {
    const id = doc()?.doc_id;
    if (!id) { UI.err('Open a PDF first'); return; }
    await UI.busy(button, async () => {
      try {
        result = await API.post(`/api/documents/${id}/purchase-order`, {
          pages: { mode: 'all' },
          force_ocr: forceOcr,
          engine: engineChoice || null,
        });
        batch = [];
        edited = false;
        renderResults();
        showTable(true);
        const count = result.line_items.length;
        if (count) UI.ok(`Found ${count} line item${count === 1 ? '' : 's'}`);
        else UI.toast('No line items recognised — try Region copy, or force OCR');
        App.refreshSide();
      } catch (error) { UI.err(error.message); }
    });
  }

  /** Read every open PDF and merge the line items into one table. */
  async function extractAll(button) {
    const docs = App.documents();
    if (!docs.length) { UI.err('Open some PDFs first'); return; }
    await UI.busy(button, async () => {
      try {
        batch = await API.post('/api/purchase-orders/batch', {
          doc_ids: docs.map((d) => d.doc_id),
          force_ocr: forceOcr,
          engine: engineChoice || null,
        });
        const good = batch.filter((r) => r.line_items.length);
        if (!good.length) {
          UI.err('No line items found in any of the open PDFs');
          return;
        }
        // Merge, tagging each row with the order it came from.
        const merged = {
          ...good[0],
          filename: `${good.length} purchase orders`,
          line_items: [],
          warnings: [],
        };
        good.forEach((entry) => {
          entry.line_items.forEach((item) => {
            merged.line_items.push({
              ...item,
              extra: {
                ...(item.extra || {}),
                source: entry.header.po_number || entry.filename,
              },
            });
          });
          entry.warnings.forEach((w) => merged.warnings.push(`${entry.filename}: ${w}`));
        });
        merged.line_items.forEach((item, index) => { item.line_no = index + 1; });
        result = merged;
        edited = false;
        renderResults();
        showTable(true);
        UI.ok(`${merged.line_items.length} line items from ${good.length} orders`);
        App.refreshSide();
      } catch (error) { UI.err(error.message); }
    });
  }

  function cellValue(item, key) {
    const value = item[key];
    if (value === null || value === undefined || value === '') return '';
    return String(value);
  }

  function copyCell(node, text) {
    if (!text) return;
    UI.copyFrom(node, text);
  }

  /** Turn a cell into an input so a mis-parsed value can be corrected. */
  function editCell(td, item, key) {
    if (td.querySelector('input')) return;
    const original = cellValue(item, key);
    const input = el('input', {
      type: 'text', value: original,
      style: 'width:100%; background:var(--surface-2); border:1px solid var(--accent);'
        + ' color:var(--text); font-family:var(--font-display); font-size:11.5px;'
        + ' padding:2px 4px; border-radius:3px;',
    });
    td.textContent = '';
    td.appendChild(input);
    input.focus();
    input.select();

    const commit = (save) => {
      const value = save ? input.value : original;
      if (save) {
        item[key] = key === 'line_no'
          ? (parseInt(value, 10) || null)
          : (value || null);
        edited = true;
      }
      td.textContent = value;
      if (save) App.refreshSide();
    };
    input.addEventListener('blur', () => commit(true));
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); input.blur(); }
      else if (event.key === 'Escape') { event.preventDefault(); commit(false); }
    });
  }

  function rowText(item) {
    return COLUMNS.map(([key]) => cellValue(item, key)).join('\t');
  }

  function tableText() {
    const head = COLUMNS.map(([, label]) => label).join('\t');
    return [head, ...result.line_items.map(rowText)].join('\n');
  }

  function renderResults() {
    const host = ensureResultsHost();
    host.innerHTML = '';
    if (!result) return;

    // Header summary
    const entries = HEADER_FIELDS
      .map(([key, label]) => [label, result.header[key]])
      .filter(([, value]) => value);

    const kv = el('dl', { class: 'kv' });
    entries.forEach(([label, value]) => {
      kv.appendChild(el('dt', { text: label }));
      const dd = el('dd', { text: value, title: 'Click to copy' });
      dd.addEventListener('click', () => copyCell(dd, value));
      kv.appendChild(dd);
    });

    const sourceBadge = el('span', {
      class: `badge ${result.source === 'ocr' ? 'warn' : 'ok'}`,
      text: result.source === 'ocr' ? `OCR · ${result.engine || 'unknown'}` : 'PDF text layer',
    });

    host.appendChild(el('div', {
      style: 'background:var(--surface); border:1px solid var(--border); border-radius:9px; padding:16px; margin-bottom:14px;',
    }, [
      el('div', { class: 'row', style: 'margin-bottom:12px' }, [
        el('h2', { class: 'section', text: 'Order details', style: 'margin:0; flex:1' }),
        edited ? el('span', { class: 'badge info', text: 'edited by hand' }) : null,
        sourceBadge,
      ]),
      entries.length ? kv : el('p', { class: 'empty', text: 'No header fields were recognised.' }),
    ]));

    // Warnings
    if (result.warnings?.length) {
      host.appendChild(el('div', {
        style: 'background:rgba(193,137,63,.1); border-left:3px solid var(--accent); border-radius:6px; padding:10px 13px; margin-bottom:14px; font-size:12px; line-height:1.6;',
      }, result.warnings.map((w) => el('div', { text: `• ${w}` }))));
    }

    // Line items
    const extraKeys = [];
    result.line_items.forEach((item) => {
      Object.keys(item.extra || {}).forEach((k) => {
        if (!extraKeys.includes(k)) extraKeys.push(k);
      });
    });

    const thead = el('tr', {}, [
      ...COLUMNS.map(([, label, width]) => el('th', { text: label, style: `min-width:${width}px` })),
      ...extraKeys.map((k) => el('th', { text: k })),
      el('th', { text: '' }),
    ]);

    const rows = result.line_items.map((item) => {
      const cells = COLUMNS.map(([key]) => {
        const value = cellValue(item, key);
        const td = el('td', {
          class: `copy-cell${key === 'description' ? ' po-desc' : ''}`,
          text: value,
          title: value ? 'Click to copy · double-click to correct' : 'Double-click to fill in',
        });
        td.addEventListener('click', () => copyCell(td, cellValue(item, key)));
        // Double-click to fix anything the parser got wrong before exporting.
        td.addEventListener('dblclick', () => editCell(td, item, key));
        return td;
      });
      extraKeys.forEach((k) => {
        const value = (item.extra || {})[k] || '';
        const td = el('td', { class: 'copy-cell', text: value });
        if (value) td.addEventListener('click', () => copyCell(td, value));
        cells.push(td);
      });
      cells.push(el('td', {}, [
        el('button', {
          class: 'btn sm ghost', text: '⧉', title: 'Copy the whole row',
          onClick: (e) => UI.copyFrom(e.target, rowText(item)),
        }),
      ]));
      return el('tr', {}, cells);
    });

    host.appendChild(el('div', {
      style: 'background:var(--surface); border:1px solid var(--border); border-radius:9px; overflow:hidden;',
    }, [
      el('div', { class: 'row', style: 'padding:12px 16px; border-bottom:1px solid var(--border)' }, [
        el('h2', { class: 'section', style: 'margin:0; flex:1',
          text: `Line items (${result.line_items.length})` }),
        el('button', { class: 'btn sm', text: 'Copy table', onClick: (e) => UI.copyFrom(e.target, tableText()) }),
      ]),
      el('div', { style: 'overflow-x:auto; max-height:52vh' }, [
        el('table', { class: 'po-table' }, [el('thead', {}, [thead]), el('tbody', {}, rows)]),
      ]),
    ]));
  }

  // --------------------------------------------------------------- panel

  async function loadEngines() {
    try {
      const caps = await API.get('/api/ocr/capabilities');
      engines = caps.engines || [];
    } catch (_) { engines = []; }
  }

  function build(host) {
    host.innerHTML = '';
    if (!doc()) {
      host.appendChild(el('p', { class: 'empty', text: 'Open a purchase order PDF to extract it.' }));
      return;
    }

    host.appendChild(el('h2', { class: 'section', text: 'Extract' }));
    host.appendChild(el('p', { class: 'hint',
      text: 'Reads the PO into fields and line items. Born-digital PDFs use the embedded text layer; scans fall through to OCR automatically.' }));

    const extractBtn = el('button', { class: 'btn primary', style: 'flex:1',
      text: 'Read this PO' });
    extractBtn.addEventListener('click', () => extract(extractBtn));
    const allBtn = el('button', { class: 'btn', style: 'flex:1',
      text: `Read all ${App.documents().length}` });
    allBtn.addEventListener('click', () => extractAll(allBtn));
    host.appendChild(el('div', { class: 'row' }, [extractBtn, allBtn]));

    const forceBox = el('input', { type: 'checkbox', onChange: (e) => { forceOcr = e.target.checked; } });
    forceBox.checked = forceOcr;
    host.appendChild(el('label', { class: 'row', style: 'margin-top:10px; cursor:pointer' }, [
      forceBox, el('span', { text: 'Force OCR (ignore the text layer)', style: 'font-size:12px' }),
    ]));

    const available = engines.filter((e) => e.available && e.name !== 'native');
    host.appendChild(field('OCR engine',
      select([['', available.length ? 'Automatic (best available)' : 'None installed'],
        ...available.map((e) => [e.name, e.label])], engineChoice, (v) => { engineChoice = v; })));

    if (!available.length) {
      host.appendChild(el('p', { class: 'hint',
        text: 'No OCR engine installed — scanned POs cannot be read yet. See Settings for install commands.' }));
    }

    if (result) {
      host.appendChild(el('h2', { class: 'section', text: 'Export' }));
      const exportRow = el('div', { class: 'row tight' },
        [['csv', 'CSV'], ['tsv', 'TSV'], ['xlsx', 'Excel'], ['txt', 'Text']].map(([fmt, label]) =>
          el('button', {
            class: 'btn sm', text: label,
            onClick: () => API.download(
              `/api/documents/${doc().doc_id}/purchase-order/export?fmt=${fmt}`, result,
            ).catch((e) => UI.err(e.message)),
          })));
      host.appendChild(exportRow);

      host.appendChild(el('button', {
        class: 'btn sm', style: 'width:100%; margin-top:8px',
        text: 'Copy all line items (TSV)',
        onClick: (e) => UI.copyFrom(e.target, tableText()),
      }));

      host.appendChild(el('h2', { class: 'section', text: 'Supplier template' }));
      host.appendChild(el('p', { class: 'hint',
        text: 'If this layout parsed well, save it so the next PO from this supplier uses the same columns.' }));
      host.appendChild(el('button', {
        class: 'btn sm', style: 'width:100%', text: 'Save this layout as a template',
        onClick: saveTemplateDialog,
      }));
    }

    host.appendChild(el('h2', { class: 'section', text: 'Grab anything else' }));
    host.appendChild(el('p', { class: 'hint',
      text: 'Drag a box on the page to OCR just that area and copy it.' }));
    host.appendChild(el('button', {
      class: 'btn sm', style: 'width:100%', text: 'Region copy',
      onClick: () => {
        showTable(false);
        Viewer.setInteraction('rect', { onRect: regionCopy });
        UI.toast('Drag a box on the page');
      },
    }));
    host.appendChild(el('div', { class: 'out', id: 'po-region-out',
      style: 'margin-top:8px; max-height:150px', text: 'Region text appears here.' }));
  }

  async function regionCopy(rect) {
    try {
      const results = await API.post(`/api/documents/${doc().doc_id}/ocr`, {
        region: rect, region_page: rect.page, mode: 'plain', engine: engineChoice || null,
      });
      const text = (results[0]?.text || '').trim();
      const out = document.getElementById('po-region-out');
      if (!text) { if (out) out.textContent = '(nothing found there)'; return; }
      if (out) out.textContent = text;
      await UI.copy(text);
      UI.ok(`Copied ${text.length} characters`);
    } catch (error) { UI.err(error.message); }
  }

  function saveTemplateDialog() {
    const name = el('input', { type: 'text', value: result?.header?.supplier || 'New supplier' });
    const hint = el('input', { type: 'text', value: result?.header?.supplier || '' });
    UI.modal({
      title: 'Save supplier template',
      body: el('div', {}, [
        field('Template name', name),
        field('Match on text', hint, 'Any PO containing this text will use this template automatically.'),
      ]),
      actions: [{ label: 'Cancel' }, {
        label: 'Save', kind: 'primary',
        onClick: async (close) => {
          close();
          try {
            await API.post(`/api/documents/${doc().doc_id}/po-templates/learn`, {
              name: name.value, supplier_hint: hint.value || null, page: 1,
            });
            UI.ok('Template saved');
          } catch (e) { UI.err(e.message); }
        },
      }],
    });
  }

  function stageExtra() {
    return [
      el('button', {
        class: 'btn sm', id: 'po-view-toggle', text: 'Show table',
        onClick: () => showTable(ensureResultsHost().style.display === 'none'),
      }),
    ];
  }

  return {
    id: 'po',
    async activate(host) {
      await loadEngines();
      build(host);
      Viewer.setInteraction('none');
      App.setStageExtra(stageExtra());
      if (result && result.doc_id === doc()?.doc_id) { renderResults(); showTable(true); }
      else { result = null; showTable(false); }
    },
    deactivate() {
      showTable(false);
      Viewer.setInteraction('none');
      App.setStageExtra([]);
    },
    refresh(host) { build(host); },
  };
})();
