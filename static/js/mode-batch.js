/* Batch mode — work on every open document at once, and issue job packs.

   The stage becomes the running order: which documents are in, what each is
   called in the contents, and the page each will land on once the front matter
   is counted. The side panel holds the pack details and the bulk tools. */
window.ModeBatch = (() => {
  const { el, field, select } = UI;

  const chosen = new Set();          // doc_ids included in the job
  const order = [];                  // doc_ids, in issue order
  const titles = {};                 // doc_id -> contents title override
  let outline = null;                // server's preview of the assembled pack
  let previewTimer = null;
  let tool = 'stamp';

  const cover = {
    enabled: true, title: '', project: '', client: '', reference: '',
    revision: '', date: '', prepared_by: '', notes: '', logo_asset: null,
  };
  const opts = {
    contents: true, bookmarks: true, page_numbers: true,
    number_format: 'Page {page} of {total}', number_position: 'bottom-right',
    footer: '', start_number: 1, filename: 'job-pack.pdf',
  };
  const params = {
    stamp: { footer: '', numbers: true, number_format: 'Page {page} of {total}',
      position: 'bottom-right', start_number: 1, skip_first: false },
    watermark: { text: 'DRAFT', opacity: 0.15, rotation: 45, colour: '#b00020',
      font_size: 60, position: 'center' },
    rotate: { degrees: 90 },
    optimise: {}, flatten: {}, scrub: {},
  };
  const split = { every: 2, ranges: '' };
  const naming = { pattern: '{nn}_{name}', project: '', revision: '' };

  // ------------------------------------------------------------- selection

  /** Keep the running order in step with the documents that are open. */
  function sync() {
    const open = App.documents().map((d) => d.doc_id);
    for (let i = order.length - 1; i >= 0; i -= 1) {
      if (!open.includes(order[i])) { chosen.delete(order[i]); order.splice(i, 1); }
    }
    open.forEach((id) => {
      if (!order.includes(id)) { order.push(id); chosen.add(id); }
    });
  }

  function sources() {
    return order.filter((id) => chosen.has(id))
      .map((id) => ({ doc_id: id, title: titles[id] || '' }));
  }

  function docById(id) {
    return App.documents().find((d) => d.doc_id === id) || null;
  }

  function move(id, delta) {
    const from = order.indexOf(id);
    const to = from + delta;
    if (from < 0 || to < 0 || to >= order.length) return;
    order.splice(to, 0, order.splice(from, 1)[0]);
    schedulePreview();
    render();
  }

  // --------------------------------------------------------------- preview

  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(refreshPreview, 250);
  }

  async function refreshPreview() {
    const chosenSources = sources();
    if (!chosenSources.length) { outline = null; renderStage(); return; }
    try {
      outline = await API.post('/api/batch/pack/preview', {
        sources: chosenSources,
        cover: { ...cover },
        contents: opts.contents,
      });
    } catch (error) {
      outline = null;
      UI.err(error.message);
    }
    renderStage();
  }

  // ----------------------------------------------------------------- stage

  function renderStage() {
    const host = document.getElementById('batch-canvas');
    if (!host) return;
    host.innerHTML = '';
    sync();

    if (!App.documents().length) {
      host.appendChild(el('div', { class: 'empty',
        text: 'Open some PDFs — batch tools and job packs work across everything that is open.' }));
      return;
    }

    const table = el('div', { class: 'batch-table reticle' });
    table.appendChild(el('div', { class: 'batch-head' }, [
      el('span', { class: 'bt-pick' }),
      el('span', { class: 'bt-order', text: '#' }),
      el('span', { class: 'bt-title', text: 'Title in the contents' }),
      el('span', { class: 'bt-file', text: 'File' }),
      el('span', { class: 'bt-pages', text: 'Pages' }),
      el('span', { class: 'bt-start', text: 'Starts' }),
      el('span', { class: 'bt-move' }),
    ]));

    let position = 0;
    order.forEach((id) => {
      const doc = docById(id);
      if (!doc) return;
      const included = chosen.has(id);
      if (included) position += 1;
      const section = included && outline
        ? outline.sections[position - 1] : null;

      const box = el('input', { type: 'checkbox' });
      box.checked = included;
      box.addEventListener('change', () => {
        if (box.checked) chosen.add(id); else chosen.delete(id);
        schedulePreview();
        render();
      });

      const title = el('input', {
        type: 'text', class: 'bt-title-input',
        value: titles[id] || '',
        placeholder: doc.filename.replace(/\.pdf$/i, ''),
      });
      title.addEventListener('input', () => { titles[id] = title.value; });
      title.addEventListener('change', schedulePreview);

      const row = el('div', { class: `batch-row${included ? '' : ' off'}` }, [
        el('span', { class: 'bt-pick' }, [box]),
        el('span', { class: 'bt-order', text: included ? String(position) : '—' }),
        el('span', { class: 'bt-title' }, [title]),
        el('span', { class: 'bt-file', text: doc.filename, title: doc.filename }),
        el('span', { class: 'bt-pages', text: String(doc.total_pages) }),
        el('span', { class: 'bt-start', text: section ? `p${section.start}` : '' }),
        el('span', { class: 'bt-move' }, [
          el('button', { class: 'btn sm ghost', text: '↑', title: 'Move up',
            onClick: () => move(id, -1) }),
          el('button', { class: 'btn sm ghost', text: '↓', title: 'Move down',
            onClick: () => move(id, 1) }),
        ]),
      ]);
      table.appendChild(row);
    });
    host.appendChild(table);

    const picked = sources().length;
    host.appendChild(el('div', { class: 'batch-summary' }, [
      el('span', { text: `${picked} of ${App.documents().length} selected` }),
      outline ? el('span', {}, [
        el('strong', { text: `${outline.total_pages} pages` }),
        el('span', { text: ` — ${outline.front_matter} of front matter` }),
      ]) : el('span', { text: '' }),
    ]));
  }

  // ------------------------------------------------------------ side panel

  function download(url, body, fallback) {
    return API.download(url, body, fallback).catch((error) => UI.err(error.message));
  }

  function guard() {
    if (!sources().length) { UI.err('Select at least one document'); return false; }
    return true;
  }

  function build(host) {
    host.innerHTML = '';
    sync();

    host.appendChild(el('h2', { class: 'section', text: 'Selection' }));
    host.appendChild(el('div', { class: 'row tight' }, [
      el('button', { class: 'btn sm', style: 'flex:1', text: 'All',
        onClick: () => { App.documents().forEach((d) => chosen.add(d.doc_id));
          schedulePreview(); render(); } }),
      el('button', { class: 'btn sm', style: 'flex:1', text: 'None',
        onClick: () => { chosen.clear(); schedulePreview(); render(); } }),
      el('button', { class: 'btn sm', style: 'flex:1', text: 'Sort A–Z',
        onClick: () => {
          order.sort((a, b) => (docById(a)?.filename || '')
            .localeCompare(docById(b)?.filename || ''));
          schedulePreview(); render();
        } }),
    ]));

    // -- job pack --------------------------------------------------------
    host.appendChild(el('h2', { class: 'section', text: 'Job pack' }));
    host.appendChild(el('p', { class: 'hint',
      text: 'Cover sheet, linked contents, bookmarks and continuous numbering — the form an as-built or O&M pack gets issued in.' }));

    const text = (label, key, store, placeholder) => field(label, el('input', {
      type: 'text', value: store[key], placeholder: placeholder || '',
      onInput: (e) => { store[key] = e.target.value; },
    }));

    host.appendChild(el('div', { class: 'field inline' }, [
      el('label', { text: 'Cover sheet' }),
      (() => {
        const box = el('input', { type: 'checkbox' });
        box.checked = cover.enabled;
        box.addEventListener('change', () => {
          cover.enabled = box.checked; schedulePreview(); render();
        });
        return box;
      })(),
    ]));

    if (cover.enabled) {
      host.appendChild(text('Title', 'title', cover, 'Riverside Hotel — Stage 2'));
      host.appendChild(text('Project', 'project', cover, 'Lighting control as-built'));
      host.appendChild(text('Client', 'client', cover));
      host.appendChild(el('div', { class: 'field-pair' }, [
        text('Reference', 'reference', cover, 'AES-2481'),
        text('Revision', 'revision', cover, 'C'),
      ]));
      host.appendChild(text('Prepared by', 'prepared_by', cover));
      host.appendChild(field('Notes', el('textarea', {
        rows: '2', value: cover.notes,
        onInput: (e) => { cover.notes = e.target.value; },
      })));
    }

    [['contents', 'Contents page'], ['bookmarks', 'PDF bookmarks'],
      ['page_numbers', 'Page numbers']].forEach(([key, label]) => {
      const box = el('input', { type: 'checkbox' });
      box.checked = opts[key];
      box.addEventListener('change', () => {
        opts[key] = box.checked;
        if (key === 'contents') schedulePreview();
        render();
      });
      host.appendChild(el('div', { class: 'field inline' }, [
        el('label', { text: label }), box]));
    });

    if (opts.page_numbers) {
      host.appendChild(field('Number position', select(
        [['bottom-right', 'Bottom right'], ['bottom-centre', 'Bottom centre'],
          ['bottom-left', 'Bottom left'], ['top-right', 'Top right'],
          ['top-centre', 'Top centre'], ['top-left', 'Top left']],
        opts.number_position, (v) => { opts.number_position = v; })));
      host.appendChild(text('Number format', 'number_format', opts));
    }
    host.appendChild(text('Footer', 'footer', opts, 'AES-2481 · Rev C'));
    host.appendChild(text('File name', 'filename', opts));

    host.appendChild(el('button', {
      class: 'btn primary', style: 'width:100%', text: 'Build job pack',
      onClick: () => guard() && download('/api/batch/pack', {
        sources: sources(), cover, ...opts,
      }, opts.filename),
    }));
    host.appendChild(el('div', { class: 'row tight', style: 'margin-top:6px' }, [
      el('button', { class: 'btn sm', style: 'flex:1', text: 'Merge only (no cover)',
        onClick: () => guard() && download('/api/batch/merge', {
          doc_ids: sources().map((s) => s.doc_id), bookmarks: opts.bookmarks,
        }, 'merged.pdf') }),
      el('button', { class: 'btn sm', style: 'flex:1', text: 'Register (Excel)',
        title: 'Document register for this pack, plus the rename plan',
        onClick: () => guard() && download('/api/batch/manifest', {
          sources: sources(), cover, contents: opts.contents, ...naming,
        }, 'pack-manifest.xlsx') }),
    ]));

    buildTools(host);
  }

  function buildTools(host) {
    host.appendChild(el('h2', { class: 'section', text: 'Bulk tools' }));
    host.appendChild(field('Tool', select([
      ['stamp', 'Page numbers and footer'],
      ['watermark', 'Watermark'],
      ['rotate', 'Rotate every page'],
      ['optimise', 'Optimise / shrink'],
      ['flatten', 'Flatten annotations'],
      ['scrub', 'Strip metadata'],
      ['split', 'Split'],
      ['rename', 'Rename'],
    ], tool, (v) => { tool = v; render(); })));

    const bind = (store, key) => (e) => { store[key] = e.target.value; };
    const num = (store, key) => (e) => {
      store[key] = parseFloat(e.target.value) || 0;
    };

    if (tool === 'stamp') {
      const p = params.stamp;
      host.appendChild(field('Footer', el('input', { type: 'text', value: p.footer,
        placeholder: 'AES-2481 · Rev C', onInput: bind(p, 'footer') })));
      host.appendChild(field('Format', el('input', { type: 'text',
        value: p.number_format, onInput: bind(p, 'number_format') }),
      'Tokens: {page} and {total}.'));
      host.appendChild(field('Position', select(
        [['bottom-right', 'Bottom right'], ['bottom-centre', 'Bottom centre'],
          ['bottom-left', 'Bottom left'], ['top-right', 'Top right'],
          ['top-centre', 'Top centre'], ['top-left', 'Top left']],
        p.position, (v) => { p.position = v; })));
      host.appendChild(field('Start at', el('input', { type: 'number', value: p.start_number,
        min: '1', onInput: num(p, 'start_number') })));
      const skip = el('input', { type: 'checkbox' });
      skip.checked = p.skip_first;
      skip.addEventListener('change', () => { p.skip_first = skip.checked; });
      host.appendChild(el('div', { class: 'field inline' }, [
        el('label', { text: 'Skip the first page' }), skip]));
    } else if (tool === 'watermark') {
      const p = params.watermark;
      host.appendChild(field('Text', el('input', { type: 'text', value: p.text,
        onInput: bind(p, 'text') })));
      host.appendChild(el('div', { class: 'field-pair' }, [
        field('Size', el('input', { type: 'number', value: p.font_size, min: '8',
          onInput: num(p, 'font_size') })),
        field('Rotation', el('input', { type: 'number', value: p.rotation, step: '15',
          onInput: num(p, 'rotation') })),
      ]));
      host.appendChild(field('Opacity', el('input', { type: 'range', value: p.opacity,
        min: '0.03', max: '0.6', step: '0.01', onInput: num(p, 'opacity') })));
      host.appendChild(field('Colour', el('input', { type: 'color', value: p.colour,
        onInput: bind(p, 'colour') })));
      host.appendChild(field('Placement', select(
        [['center', 'Centre'], ['tile', 'Tiled']], p.position,
        (v) => { p.position = v; })));
    } else if (tool === 'rotate') {
      host.appendChild(field('Turn', select(
        [['90', '90° clockwise'], ['180', '180°'], ['270', '90° anticlockwise']],
        String(params.rotate.degrees), (v) => { params.rotate.degrees = parseInt(v, 10); })));
    } else if (tool === 'split') {
      host.appendChild(field('Every N pages', el('input', { type: 'number',
        value: split.every, min: '1', onInput: num(split, 'every') })));
      host.appendChild(field('…or page ranges', el('input', { type: 'text',
        value: split.ranges, placeholder: '1-4, 5, 9-12', onInput: bind(split, 'ranges') }),
      'Ranges win when both are filled in.'));
    } else if (tool === 'rename') {
      host.appendChild(field('Pattern', el('input', { type: 'text', value: naming.pattern,
        onInput: (e) => { naming.pattern = e.target.value; } }),
      'Tokens: {name} {n} {nn} {pages} {date} {project} {rev}'));
      host.appendChild(el('div', { class: 'field-pair' }, [
        field('Project', el('input', { type: 'text', value: naming.project,
          onInput: bind(naming, 'project') })),
        field('Revision', el('input', { type: 'text', value: naming.revision,
          onInput: bind(naming, 'revision') })),
      ]));
      host.appendChild(el('button', {
        class: 'btn sm', style: 'width:100%; margin-bottom:8px', text: 'Preview names',
        onClick: async () => {
          if (!guard()) return;
          try {
            const rows = await API.post('/api/batch/rename/preview', {
              doc_ids: sources().map((s) => s.doc_id), ...naming,
            });
            UI.modal({
              title: 'New names',
              body: el('div', { class: 'rename-list' }, rows.map((r) => el('div', {
                class: 'rename-row' }, [
                el('span', { class: 'rn-from', text: r.from }),
                el('span', { class: 'rn-to', text: r.to }),
              ]))),
              actions: [{ label: 'Close', kind: 'primary' }],
            });
          } catch (error) { UI.err(error.message); }
        },
      }));
    } else {
      host.appendChild(el('p', { class: 'hint', text: {
        optimise: 'Recompresses images and fonts and drops orphaned objects.',
        flatten: 'Bakes annotations into the page so markup cannot be edited away.',
        scrub: 'Removes author, producer and creation metadata before a document leaves the office.',
      }[tool] }));
    }

    if (tool === 'split') {
      host.appendChild(el('button', { class: 'btn primary', style: 'width:100%',
        text: 'Split and download ZIP',
        onClick: () => guard() && download('/api/batch/split', {
          doc_ids: sources().map((s) => s.doc_id), ...split,
        }, 'split.zip') }));
    } else if (tool === 'rename') {
      host.appendChild(el('button', { class: 'btn primary', style: 'width:100%',
        text: 'Download renamed ZIP',
        onClick: () => guard() && download('/api/batch/rename', {
          doc_ids: sources().map((s) => s.doc_id), ...naming,
        }, 'renamed.zip') }));
    } else {
      const run = (inPlace) => async (event) => {
        if (!guard()) return;
        const body = {
          doc_ids: sources().map((s) => s.doc_id),
          operation: tool,
          params: params[tool],
          in_place: inPlace,
        };
        if (inPlace) {
          const count = body.doc_ids.length;
          if (!await UI.confirm(
            `Apply “${tool}” to ${count} document${count === 1 ? '' : 's'} in place? `
            + 'Each one can still be undone individually.')) return;
          await UI.busy(event.target, async () => {
            try {
              const res = await API.post('/api/batch/run', body);
              (res.outcomes || []).filter((o) => !o.ok)
                .forEach((o) => UI.err(`${o.filename}: ${o.detail}`));
              UI.ok(res.message);
              await App.reloadAll();
              render();
            } catch (error) { UI.err(error.message); }
          });
        } else {
          download('/api/batch/run', body, `${tool}.zip`);
        }
      };
      host.appendChild(el('div', { class: 'row tight' }, [
        el('button', { class: 'btn primary', style: 'flex:1', text: 'Apply in place',
          onClick: run(true) }),
        el('button', { class: 'btn', style: 'flex:1', text: 'Download copies',
          onClick: run(false) }),
      ]));
      host.appendChild(el('p', { class: 'hint',
        text: 'In place edits the open documents and is undoable per document. Download leaves them untouched.' }));
    }
  }

  function render() {
    renderStage();
    App.refreshSide();
  }

  // ------------------------------------------------------------- module

  return {
    id: 'batch',

    activate(host) {
      document.getElementById('batch-stage').hidden = false;
      Viewer.setInteraction('none');
      sync();
      build(host);
      refreshPreview();
    },

    deactivate() {
      const stage = document.getElementById('batch-stage');
      if (stage) stage.hidden = true;
    },

    refresh(host) { build(host); renderStage(); },
  };
})();
