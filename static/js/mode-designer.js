/* Designer mode — configure Antumbra panels and produce the engraving spec.

   The stage becomes a live front elevation you can click; the side panel holds
   the product, finish and engraving controls. Geometry always comes from the
   server (`/api/designer/check`) so the on-screen panel and the exported PDF
   are drawn from one set of millimetres. Text edits redraw locally, and only
   changes that move metal go back for fresh geometry. */
window.ModeDesigner = (() => {
  const { el, field, select } = UI;
  const SVG_NS = 'http://www.w3.org/2000/svg';

  let cat = null;                       // catalogue from the server
  const designs = [];
  let currentId = null;
  let selected = 0;                     // highlighted button position
  let backlit = false;
  let zoom = 4.4;                       // screen pixels per millimetre
  let checkTimer = null;

  const job = { name: 'antumbra-job', project: '', client: '' };
  const view = { layout: null, code: '', product: '', slots: 0, warnings: [] };

  // ------------------------------------------------------------ state

  function current() {
    return designs.find((d) => d.design_id === currentId) || null;
  }

  function newId() {
    return `d${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  }

  function blankDesign(from) {
    const base = from ? JSON.parse(JSON.stringify(from)) : {
      family: 'B', series: 'P', region: 'A', buttons: 6,
      button_finish: 'W', rim_finish: 'A', backlight: 'white',
      engraving: [], location: '', reference: '', order_12nc: '',
      quantity: 1, notes: '',
    };
    base.design_id = newId();
    base.name = from ? `${from.name} copy` : `Panel ${designs.length + 1}`;
    return base;
  }

  function engravingFor(index) {
    const design = current();
    if (!design) return null;
    let item = design.engraving.find((e) => e.index === index);
    if (!item) {
      item = { index, lines: [], icon: null, icon_side: 'left' };
      design.engraving.push(item);
      design.engraving.sort((a, b) => a.index - b.index);
    }
    return item;
  }

  function finishByCode(list, code) {
    return (cat[list] || []).find((f) => f.code === code) || { hex: '#888', name: code };
  }

  function iconById(id) {
    return (cat.icons || []).find((i) => i.id === id) || null;
  }

  // ------------------------------------------------------------ server

  /** Fetch geometry and the product code for the current configuration.

     The preview is always repainted — it holds the part code, and it has no
     inputs to steal focus from. The side panel is only rebuilt when the change
     came from outside a text field, so typing is never interrupted. */
  async function refreshCheck({ rebuildSide = true } = {}) {
    const design = current();
    if (!design) { view.layout = null; renderPreview(); return; }
    try {
      const res = await API.post('/api/designer/check', design);
      view.code = res.part_code;
      view.product = res.product;
      view.slots = res.slots;
      view.warnings = res.warnings || [];
      if (res.ok) view.layout = res.layout;
      if (!res.ok) UI.err(res.errors.join('; '));
      if (selected >= res.slots) selected = Math.max(0, res.slots - 1);
    } catch (error) {
      UI.err(error.message);
    }
    renderPreview();
    if (rebuildSide) App.refreshSide();
  }

  /** Debounced re-check for edits made inside a field, which must keep focus. */
  function scheduleCheck() {
    clearTimeout(checkTimer);
    checkTimer = setTimeout(() => refreshCheck({ rebuildSide: false }), 400);
  }

  // ----------------------------------------------------------- preview

  function svgEl(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined) continue;
      node.setAttribute(key, value);
    }
    return node;
  }

  /** Draw one icon from the shared library into a square of the given size. */
  function iconGroup(icon, x, y, size, colour, weight) {
    const group = svgEl('g', {});
    const px = (v) => x + (v / 100) * size;
    const py = (v) => y + (v / 100) * size;
    const stroke = weight || Math.max(0.12, size * 0.075);

    (icon.shapes || []).forEach((shape) => {
      const kind = shape[0];
      if (kind === 'line') {
        group.appendChild(svgEl('line', {
          x1: px(shape[1]), y1: py(shape[2]), x2: px(shape[3]), y2: py(shape[4]),
          stroke: colour, 'stroke-width': stroke, 'stroke-linecap': 'round',
        }));
      } else if (kind === 'poly') {
        const points = [];
        for (let i = 0; i < shape[1].length; i += 2) {
          points.push(`${px(shape[1][i])},${py(shape[1][i + 1])}`);
        }
        group.appendChild(svgEl(shape[2] ? 'polygon' : 'polyline', {
          points: points.join(' '), fill: 'none', stroke: colour,
          'stroke-width': stroke, 'stroke-linecap': 'round',
          'stroke-linejoin': 'round',
        }));
      } else if (kind === 'fpoly') {
        const points = [];
        for (let i = 0; i < shape[1].length; i += 2) {
          points.push(`${px(shape[1][i])},${py(shape[1][i + 1])}`);
        }
        group.appendChild(svgEl('polygon', { points: points.join(' '), fill: colour }));
      } else if (kind === 'circle') {
        group.appendChild(svgEl('circle', {
          cx: px(shape[1]), cy: py(shape[2]), r: (shape[3] / 100) * size,
          fill: 'none', stroke: colour, 'stroke-width': stroke,
        }));
      } else if (kind === 'disc') {
        group.appendChild(svgEl('circle', {
          cx: px(shape[1]), cy: py(shape[2]), r: (shape[3] / 100) * size, fill: colour,
        }));
      }
    });
    return group;
  }

  /** Standalone icon SVG, used by the picker and the engraving controls. */
  function iconPreview(icon, pixels, colour) {
    const svg = svgEl('svg', {
      viewBox: '0 0 100 100', width: pixels, height: pixels, class: 'icon-svg',
    });
    svg.appendChild(iconGroup(icon, 0, 0, 100, colour || 'currentColor', 6));
    return svg;
  }

  function renderPreview() {
    const host = document.getElementById('designer-canvas');
    if (!host) return;
    host.innerHTML = '';

    const design = current();
    if (!design || !view.layout) {
      host.appendChild(el('div', { class: 'empty',
        text: design ? 'Working out the geometry…' : 'Add a panel to start designing.' }));
      return;
    }

    const layout = view.layout;
    const buttonFinish = finishByCode('button_finishes', design.button_finish);
    const rimFinish = finishByCode('rim_finishes', design.rim_finish);
    const glow = (cat.backlights.find((b) => b.id === design.backlight) || {}).hex
      || '#F2F4F6';
    const ink = backlit ? glow : buttonFinish.ink;

    const svg = svgEl('svg', {
      viewBox: `0 0 ${layout.width_mm} ${layout.height_mm}`,
      width: layout.width_mm * zoom,
      height: layout.height_mm * zoom,
      class: `panel-svg${backlit ? ' backlit' : ''}`,
    });

    if (backlit) {
      const defs = svgEl('defs', {});
      const blur = svgEl('filter', { id: 'panel-glow',
        x: '-60%', y: '-60%', width: '220%', height: '220%' });
      blur.appendChild(svgEl('feGaussianBlur', { stdDeviation: 0.5, result: 'b' }));
      const merge = svgEl('feMerge', {});
      merge.appendChild(svgEl('feMergeNode', { in: 'b' }));
      merge.appendChild(svgEl('feMergeNode', { in: 'SourceGraphic' }));
      blur.appendChild(merge);
      defs.appendChild(blur);
      svg.appendChild(defs);
    }

    const plate = layout.plate;
    svg.appendChild(svgEl('rect', {
      x: plate.x, y: plate.y, width: plate.w, height: plate.h, rx: plate.r,
      fill: backlit ? shade(rimFinish.hex, 0.42) : rimFinish.hex,
      stroke: shade(rimFinish.hex, backlit ? 0.3 : 0.62), 'stroke-width': 0.3,
    }));
    const face = layout.face;
    svg.appendChild(svgEl('rect', {
      x: face.x, y: face.y, width: face.w, height: face.h, rx: face.r,
      fill: backlit ? shade(rimFinish.hex, 0.34) : shade(rimFinish.hex, 0.92),
      stroke: shade(rimFinish.hex, backlit ? 0.26 : 0.74), 'stroke-width': 0.22,
    }));

    if (layout.screen) {
      const screen = layout.screen;
      svg.appendChild(svgEl('rect', {
        x: screen.x, y: screen.y, width: screen.w, height: screen.h, rx: screen.r,
        fill: '#14161a', stroke: '#2b2f36', 'stroke-width': 0.3,
      }));
      const label = svgEl('text', {
        x: screen.x + screen.w / 2, y: screen.y + screen.h / 2,
        'text-anchor': 'middle', fill: '#7d8590', 'font-size': 3,
        'font-family': 'sans-serif',
      });
      label.textContent = 'Labelled in software';
      svg.appendChild(label);
    }

    (layout.buttons || []).forEach((button) => {
      const group = svgEl('g', { class: 'panel-button', 'data-index': button.index });
      const fill = backlit ? shade(buttonFinish.hex, 0.3) : buttonFinish.hex;
      group.appendChild(svgEl('rect', {
        x: button.x, y: button.y, width: button.w, height: button.h, rx: button.r,
        fill,
        stroke: button.index === selected
          ? '#c1893f'
          : shade(buttonFinish.hex, backlit ? 0.26 : 0.74),
        'stroke-width': button.index === selected ? 0.55 : 0.25,
        'stroke-dasharray': button.zone ? '1 1' : null,
      }));
      group.appendChild(svgEl('circle', {
        cx: button.led.cx, cy: button.led.cy, r: button.led.r,
        fill: backlit ? glow : shade(buttonFinish.hex, 0.88),
        filter: backlit ? 'url(#panel-glow)' : null,
      }));
      drawEngraving(group, button, ink);
      group.addEventListener('click', () => {
        selected = button.index;
        renderPreview();
        App.refreshSide();
      });
      svg.appendChild(group);
    });

    const frame = el('div', { class: 'panel-frame' });
    frame.appendChild(svg);
    host.appendChild(frame);

    host.appendChild(el('div', { class: 'panel-caption' }, [
      el('span', { class: 'code', text: view.code || '—' }),
      el('span', { text: `${layout.width_mm} × ${layout.height_mm} mm` }),
      el('span', { text: view.product || '' }),
    ]));

    fitTexts(svg);
  }

  function drawEngraving(group, button, ink) {
    const item = (current().engraving || []).find((e) => e.index === button.index);
    if (!item) return;
    const lines = (item.lines || []).map((l) => String(l).trim()).filter(Boolean);
    const icon = item.icon ? iconById(item.icon) : null;
    if (!lines.length && !icon) return;

    const area = button.text;
    let textX = area.x;
    let textW = area.w;
    let textTop = area.y;
    let textH = area.h;

    if (icon && lines.length && item.icon_side !== 'top') {
      const size = Math.min(area.h * 0.52, area.w * 0.34);
      group.appendChild(iconGroup(icon, area.x, area.y + (area.h - size) / 2, size, ink));
      const gap = size * 0.28;
      textX = area.x + size + gap;
      textW = area.w - size - gap;
    } else if (icon && lines.length) {
      const size = Math.min(area.h * 0.42, area.w * 0.5);
      group.appendChild(iconGroup(icon, area.x + (area.w - size) / 2,
        area.y + area.h * 0.14, size, ink));
      textTop = area.y + area.h * 0.14 + size;
      textH = area.h * 0.86 - size;
    } else if (icon) {
      const size = Math.min(area.h * 0.6, area.w * 0.7);
      group.appendChild(iconGroup(icon, area.x + (area.w - size) / 2,
        area.y + (area.h - size) / 2, size, ink));
      return;
    }

    const centred = Boolean(icon && item.icon_side === 'top');
    const size = Math.min(textH / (lines.length + 1.1), 3.0);
    const leading = size * 1.24;
    let cursor = textTop + (textH - leading * lines.length) / 2 + size * 0.82;

    lines.forEach((line) => {
      const node = svgEl('text', {
        x: centred ? textX + textW / 2 : textX,
        y: cursor,
        fill: ink,
        'font-size': size,
        'font-family': 'Helvetica, Arial, sans-serif',
        'text-anchor': centred ? 'middle' : 'start',
        'data-maxw': textW,
      });
      node.textContent = line;
      group.appendChild(node);
      cursor += leading;
    });
  }

  /** Shrink any label that overruns its button, the way the PDF does. */
  function fitTexts(svg) {
    svg.querySelectorAll('text[data-maxw]').forEach((node) => {
      const max = parseFloat(node.getAttribute('data-maxw'));
      let width;
      try { width = node.getComputedTextLength(); } catch (_) { return; }
      if (!width || width <= max) return;
      const size = parseFloat(node.getAttribute('font-size'));
      node.setAttribute('font-size', Math.max(1.1, size * (max / width)));
    });
  }

  function shade(hex, factor) {
    const value = hex.replace('#', '');
    const parts = [0, 2, 4].map((i) => {
      const channel = Math.round(parseInt(value.slice(i, i + 2), 16) * factor);
      return Math.max(0, Math.min(255, channel)).toString(16).padStart(2, '0');
    });
    return `#${parts.join('')}`;
  }

  // ------------------------------------------------------- icon picker

  function pickIcon(onPick) {
    const grid = el('div', { class: 'icon-grid' });
    const search = el('input', { type: 'text', placeholder: 'Search icons…' });

    function fill(term) {
      grid.innerHTML = '';
      const needle = term.trim().toLowerCase();
      cat.icon_groups.forEach((groupName) => {
        const matches = cat.icons.filter((i) => i.group === groupName
          && (!needle || i.name.toLowerCase().includes(needle)
            || i.id.includes(needle)));
        if (!matches.length) return;
        grid.appendChild(el('div', { class: 'icon-group-name', text: groupName }));
        const row = el('div', { class: 'icon-row' });
        matches.forEach((icon) => {
          const cell = el('button', { class: 'icon-cell', title: icon.name });
          cell.appendChild(iconPreview(icon, 30));
          cell.appendChild(el('span', { text: icon.name }));
          cell.addEventListener('click', () => { onPick(icon.id); close(); });
          row.appendChild(cell);
        });
        grid.appendChild(row);
      });
    }

    search.addEventListener('input', () => fill(search.value));
    fill('');

    const close = UI.modal({
      title: 'Choose an icon',
      body: el('div', {}, [
        el('div', { class: 'field' }, [search]),
        grid,
      ]),
      actions: [
        { label: 'No icon', onClick: (done) => { onPick(null); done(); } },
        { label: 'Close', kind: 'primary' },
      ],
    });
  }

  // ------------------------------------------------------- side panel

  function swatchRow(list, value, onPick) {
    const row = el('div', { class: 'swatches' });
    (cat[list] || []).forEach((finish) => {
      const cell = el('button', {
        class: `swatch${finish.code === value ? ' on' : ''}`,
        title: `${finish.name} (${finish.code})${finish.note ? ` — ${finish.note}` : ''}`,
      });
      cell.appendChild(el('span', { class: 'chip-colour',
        style: `background:${finish.hex}` }));
      cell.appendChild(el('span', { class: 'swatch-name', text: finish.name }));
      cell.addEventListener('click', () => onPick(finish.code));
      row.appendChild(cell);
    });
    return row;
  }

  function build(host) {
    host.innerHTML = '';
    if (!cat) {
      host.appendChild(el('p', { class: 'empty', text: 'Loading the catalogue…' }));
      return;
    }

    // -- panels in this job ---------------------------------------------
    host.appendChild(el('h2', { class: 'section', text: 'Panels in this job' }));
    const list = el('div', {});
    designs.forEach((design) => {
      const row = el('div', {
        class: `queue-item${design.design_id === currentId ? ' active' : ''}`,
      }, [
        el('span', { class: 'q-name', text: design.name, title: design.name }),
        el('span', { style: 'color:var(--text-dim); font-size:11px',
          text: design.quantity > 1 ? `×${design.quantity}` : '' }),
      ]);
      row.addEventListener('click', () => {
        currentId = design.design_id;
        selected = 0;
        refreshCheck();
      });
      list.appendChild(row);
    });
    host.appendChild(list);

    host.appendChild(el('div', { class: 'row tight', style: 'margin-top:8px' }, [
      el('button', { class: 'btn sm primary', style: 'flex:1', text: 'Add panel',
        onClick: () => {
          const design = blankDesign(null);
          designs.push(design);
          currentId = design.design_id;
          selected = 0;
          refreshCheck();
        } }),
      el('button', { class: 'btn sm', style: 'flex:1', text: 'Duplicate',
        disabled: !current(),
        onClick: () => {
          const design = blankDesign(current());
          designs.push(design);
          currentId = design.design_id;
          refreshCheck();
        } }),
      el('button', { class: 'btn sm danger', text: 'Delete', disabled: !current(),
        onClick: async () => {
          if (!await UI.confirm(`Delete “${current().name}”?`, { danger: true })) return;
          const index = designs.findIndex((d) => d.design_id === currentId);
          designs.splice(index, 1);
          currentId = designs[Math.min(index, designs.length - 1)]?.design_id || null;
          refreshCheck();
        } }),
    ]));

    const design = current();
    if (!design) {
      host.appendChild(el('p', { class: 'empty',
        text: 'Add a panel to configure its product, finishes and engraving.' }));
      buildJobBlock(host);
      return;
    }

    // -- product ---------------------------------------------------------
    host.appendChild(el('h2', { class: 'section', text: 'Product' }));
    const family = cat.families.find((f) => f.code === design.family);

    host.appendChild(field('Family', select(
      cat.families.map((f) => [f.code, f.name]), design.family,
      (value) => {
        design.family = value;
        const next = cat.families.find((f) => f.code === value);
        if (!next.series.includes(design.series)) [design.series] = next.series;
        if (next.counts.length && !next.counts.includes(design.buttons)) {
          design.buttons = next.counts[next.counts.length - 1];
        }
        selected = 0;
        refreshCheck();
      },
    ), family?.blurb));

    if (family && family.series.length > 1) {
      host.appendChild(field('Series', select(
        cat.series.filter((s) => family.series.includes(s.code))
          .map((s) => [s.code, s.name]),
        design.series, (value) => { design.series = value; refreshCheck(); },
      )));
    }

    host.appendChild(field('Region', select(
      cat.regions.map((r) => [r.code, r.name]), design.region,
      (value) => { design.region = value; refreshCheck(); },
    )));

    if (family && family.counts.length > 1) {
      host.appendChild(field('Buttons', select(
        family.counts.map((c) => [String(c), `${c} buttons`]),
        String(design.buttons),
        (value) => {
          design.buttons = parseInt(value, 10);
          design.engraving = design.engraving.filter((e) => e.index < design.buttons);
          selected = 0;
          refreshCheck();
        },
      )));
    }

    // -- finishes --------------------------------------------------------
    host.appendChild(el('h2', { class: 'section', text: 'Finishes' }));
    host.appendChild(field('Buttons', swatchRow('button_finishes', design.button_finish,
      (code) => { design.button_finish = code; renderPreview(); App.refreshSide();
        scheduleCheck(); })));
    host.appendChild(field('Rim', swatchRow('rim_finishes', design.rim_finish,
      (code) => { design.rim_finish = code; renderPreview(); App.refreshSide();
        scheduleCheck(); })));
    host.appendChild(field('Backlight', select(
      cat.backlights.map((b) => [b.id, b.name]), design.backlight,
      (value) => { design.backlight = value; renderPreview(); },
    ), 'Preview only — the installed colour is set in EnvisionProject.'));

    // -- engraving -------------------------------------------------------
    if (view.slots > 0) buildEngraving(host, design);

    // -- panel details ---------------------------------------------------
    host.appendChild(el('h2', { class: 'section', text: 'Panel details' }));
    host.appendChild(field('Name', el('input', {
      type: 'text', value: design.name,
      onInput: (e) => { design.name = e.target.value; },
      onChange: () => App.refreshSide(),
    })));
    host.appendChild(field('Location', el('input', {
      type: 'text', value: design.location, placeholder: 'Level 2 lobby',
      onInput: (e) => { design.location = e.target.value; },
    })));
    host.appendChild(field('Reference', el('input', {
      type: 'text', value: design.reference, placeholder: 'KP-201',
      onInput: (e) => { design.reference = e.target.value; },
    })));
    host.appendChild(field('Quantity', el('input', {
      type: 'number', value: design.quantity, min: '1', step: '1',
      onInput: (e) => { design.quantity = Math.max(1, parseInt(e.target.value, 10) || 1); },
      onChange: () => App.refreshSide(),
    })));
    host.appendChild(field('Signify 12NC', el('input', {
      type: 'text', value: design.order_12nc, placeholder: 'From your quote',
      onInput: (e) => { design.order_12nc = e.target.value; },
    }), 'Allocated by Signify — this tool cannot derive it, so copy it from your quote.'));
    host.appendChild(field('Notes', el('textarea', {
      rows: '2', value: design.notes,
      onInput: (e) => { design.notes = e.target.value; },
    })));

    if (view.warnings.length) {
      host.appendChild(el('h2', { class: 'section', text: 'Check' }));
      view.warnings.forEach((warning) => {
        host.appendChild(el('p', { class: 'hint warn', text: warning }));
      });
    }

    buildJobBlock(host);
  }

  function buildEngraving(host, design) {
    host.appendChild(el('h2', { class: 'section', text: 'Engraving' }));
    host.appendChild(el('p', { class: 'hint',
      text: 'Click a button on the panel to select it, or use the row below.' }));

    const positions = el('div', { class: 'pos-row' });
    for (let index = 0; index < view.slots; index += 1) {
      const item = design.engraving.find((e) => e.index === index);
      const filled = item && ((item.lines || []).some((l) => l.trim()) || item.icon);
      const chip = el('button', {
        class: `pos-chip${index === selected ? ' on' : ''}${filled ? ' filled' : ''}`,
        text: String(index + 1),
      });
      chip.addEventListener('click', () => { selected = index; renderPreview(); App.refreshSide(); });
      positions.appendChild(chip);
    }
    host.appendChild(positions);

    const item = engravingFor(selected);
    const lines = [item.lines[0] || '', item.lines[1] || ''];

    // Blank rows are dropped, so filling only the second box still engraves
    // a single centred line.
    const commit = () => {
      item.lines = lines.map((line) => line.trim()).filter(Boolean);
      renderPreview();
      scheduleCheck();
    };

    host.appendChild(field(`Position ${selected + 1} — line 1`, el('input', {
      type: 'text', value: lines[0], maxlength: '24', placeholder: 'LOUNGE',
      onInput: (e) => { lines[0] = e.target.value; commit(); },
    })));
    host.appendChild(field('Line 2', el('input', {
      type: 'text', value: lines[1], maxlength: '24', placeholder: 'optional',
      onInput: (e) => { lines[1] = e.target.value; commit(); },
    })));

    const icon = item.icon ? iconById(item.icon) : null;
    const iconBtn = el('button', { class: 'icon-choice' });
    if (icon) {
      iconBtn.appendChild(iconPreview(icon, 22));
      iconBtn.appendChild(el('span', { text: icon.name }));
    } else {
      iconBtn.appendChild(el('span', { text: 'Choose an icon…' }));
    }
    iconBtn.addEventListener('click', () => pickIcon((id) => {
      item.icon = id;
      renderPreview();
      App.refreshSide();
      scheduleCheck();
    }));
    host.appendChild(field('Icon', iconBtn));

    if (item.icon) {
      host.appendChild(field('Icon position', select(
        [['left', 'Left of the text'], ['top', 'Above the text']],
        item.icon_side || 'left',
        (value) => { item.icon_side = value; renderPreview(); },
      )));
    }

    host.appendChild(el('div', { class: 'row tight' }, [
      el('button', { class: 'btn sm', style: 'flex:1', text: 'Clear this button',
        onClick: () => {
          item.lines = []; item.icon = null;
          renderPreview(); App.refreshSide(); scheduleCheck();
        } }),
      el('button', { class: 'btn sm', style: 'flex:1', text: 'Clear all',
        onClick: async () => {
          if (!await UI.confirm('Clear the engraving on every button?')) return;
          design.engraving = [];
          renderPreview(); App.refreshSide(); scheduleCheck();
        } }),
    ]));
  }

  function buildJobBlock(host) {
    host.appendChild(el('h2', { class: 'section', text: 'Job' }));
    host.appendChild(field('Job name', el('input', {
      type: 'text', value: job.name,
      onInput: (e) => { job.name = e.target.value; },
    })));
    host.appendChild(field('Project', el('input', {
      type: 'text', value: job.project,
      onInput: (e) => { job.project = e.target.value; },
    })));
    host.appendChild(field('Client', el('input', {
      type: 'text', value: job.client,
      onInput: (e) => { job.client = e.target.value; },
    })));

    const download = (fmt) => API.download('/api/designer/export', {
      designs, job_name: job.name, project: job.project, client: job.client, fmt,
    }).catch((error) => UI.err(error.message));

    host.appendChild(el('div', { class: 'row tight' }, [
      el('button', { class: 'btn sm primary', style: 'flex:1',
        text: 'Spec sheet PDF', disabled: !designs.length,
        onClick: () => download('pdf') }),
      el('button', { class: 'btn sm', style: 'flex:1', text: 'Schedule CSV',
        disabled: !designs.length, onClick: () => download('csv') }),
    ]));
    host.appendChild(el('div', { class: 'row tight', style: 'margin-top:6px' }, [
      el('button', { class: 'btn sm', style: 'flex:1', text: 'Save job',
        disabled: !designs.length, onClick: () => download('json') }),
      el('button', { class: 'btn sm', style: 'flex:1', text: 'Open job',
        onClick: openJob }),
    ]));

    host.appendChild(el('button', {
      class: 'btn sm', style: 'width:100%; margin-top:8px',
      text: 'Send to Panels queue', disabled: !designs.length,
      onClick: async (event) => {
        await UI.busy(event.target, async () => {
          try {
            const entries = await API.post('/api/designer/panels', { designs });
            ModePanels.adopt(entries);
            UI.ok(`${entries.length} panel(s) queued for engraving`);
          } catch (error) { UI.err(error.message); }
        });
      },
    }));
    host.appendChild(el('p', { class: 'hint',
      text: 'Queued panels pick up the Panels tab’s copy chips and EZcad2 export.' }));
  }

  function openJob() {
    const input = el('input', { type: 'file', accept: '.json,application/json' });
    input.style.display = 'none';
    document.body.appendChild(input);
    input.addEventListener('change', async () => {
      const file = input.files[0];
      input.remove();
      if (!file) return;
      try {
        const data = JSON.parse(await file.text());
        const loaded = Array.isArray(data) ? data : data.designs;
        if (!Array.isArray(loaded) || !loaded.length) throw new Error('No panels in that file');
        designs.length = 0;
        loaded.forEach((d) => designs.push({ ...d, design_id: d.design_id || newId() }));
        job.name = data.job_name || job.name;
        job.project = data.project || '';
        job.client = data.client || '';
        currentId = designs[0].design_id;
        selected = 0;
        await refreshCheck();
        UI.ok(`Opened ${designs.length} panel(s)`);
      } catch (error) { UI.err(`Could not read that job: ${error.message}`); }
    });
    input.click();
  }

  // ------------------------------------------------------------- stage

  function stageControls() {
    const toggle = el('button', {
      class: `btn sm ghost${backlit ? ' active' : ''}`,
      text: backlit ? 'Backlit' : 'Daylight',
      title: 'Preview the panel lit or unlit',
    });
    toggle.addEventListener('click', () => {
      backlit = !backlit;
      renderPreview();
      App.setStageExtra(stageControls());
    });

    const out = el('button', { class: 'btn sm ghost', text: '−', title: 'Smaller' });
    out.addEventListener('click', () => { zoom = Math.max(1.4, zoom / 1.2); renderPreview(); });
    const into = el('button', { class: 'btn sm ghost', text: '+', title: 'Larger' });
    into.addEventListener('click', () => { zoom = Math.min(9, zoom * 1.2); renderPreview(); });

    return [toggle, out, into];
  }

  // ------------------------------------------------------------- module

  return {
    id: 'designer',

    async activate(host) {
      document.getElementById('designer-stage').hidden = false;
      Viewer.setInteraction('none');
      if (!cat) {
        try {
          cat = await API.get('/api/designer/catalogue');
        } catch (error) {
          UI.err(`Could not load the catalogue: ${error.message}`);
          host.appendChild(el('p', { class: 'empty',
            text: 'The product catalogue could not be loaded. Check the server and switch back to this tab.' }));
          return;
        }
      }
      if (!designs.length) {
        const design = blankDesign(null);
        designs.push(design);
        currentId = design.design_id;
      }
      App.setStageExtra(stageControls());
      build(host);
      await refreshCheck();
    },

    deactivate() {
      const stage = document.getElementById('designer-stage');
      if (stage) stage.hidden = true;
      App.setStageExtra([]);
    },

    refresh(host) { build(host); },
  };
})();
