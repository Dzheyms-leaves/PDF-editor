/* Logo stamp mode: smart whitespace detection, live preview, batch apply. */
window.ModeStamp = (() => {
  const { el, field, select, number } = UI;

  let logo = null;             // AssetInfo
  let analysis = [];           // DocumentAnalysisResult[]
  const overrides = {};        // doc_id -> { page: Rect }
  const config = {
    width_pt: 120, height_pt: 40, maintain_aspect_ratio: true, opacity: 1,
    margin_bottom: 24, margin_side: 28, content_padding: 10,
    search_band_ratio: 0.35,
    strategy_priority: ['bottom-right', 'bottom-left', 'bottom-center', 'best-fit'],
    fallback_behavior: 'shrink_to_fit', min_scale: 0.5,
    page_selection: 'all', custom_pages: null,
  };
  let showObstacles = true;
  let targetAll = true;

  function doc() { return App.currentDoc(); }
  function docIds() {
    return targetAll ? App.documents().map((d) => d.doc_id) : [doc()?.doc_id].filter(Boolean);
  }

  function currentPlacement() {
    const id = doc()?.doc_id;
    const entry = analysis.find((a) => a.doc_id === id);
    if (!entry) return null;
    return entry.page_placements.find((p) => p.page_number === Viewer.page) || null;
  }

  // ------------------------------------------------------------- overlay

  function drawOverlay() {
    Viewer.clearOverlay();
    const placement = currentPlacement();
    if (!placement) return;

    if (showObstacles) {
      (placement.obstacles || []).forEach((box) => {
        Viewer.drawRect(box, {
          fill: 'rgba(108,131,153,.10)', stroke: 'rgba(108,131,153,.5)', width: 0.5,
        });
      });
    }
    if (placement.rect) {
      Viewer.drawRect(placement.rect, {
        fill: placement.is_manual_override ? 'rgba(124,139,106,.25)' : 'rgba(193,137,63,.28)',
        stroke: placement.is_manual_override ? '#a8ff1a' : '#00e5ff',
        width: 1.4,
      });
    }
  }

  async function analyze(button) {
    if (!logo) { UI.err('Choose a logo image first'); return; }
    const ids = docIds();
    if (!ids.length) { UI.err('Open a PDF first'); return; }
    await UI.busy(button, async () => {
      try {
        analysis = await API.post('/api/stamp/analyze', {
          doc_ids: ids, logo_id: logo.asset_id, config, manual_overrides: overrides,
        });
        drawOverlay();
        const placed = analysis.reduce((sum, d) =>
          sum + d.page_placements.filter((p) => p.placed).length, 0);
        UI.ok(`${placed} page(s) have a clean spot`);
        App.refreshSide();
      } catch (error) { UI.err(error.message); }
    });
  }

  async function apply(button, inPlace) {
    if (!logo) { UI.err('Choose a logo image first'); return; }
    await UI.busy(button, async () => {
      try {
        const res = await API.post('/api/stamp/apply', {
          doc_ids: docIds(), logo_id: logo.asset_id, config,
          manual_overrides: overrides, apply_in_place: inPlace,
        });
        UI.ok(`Stamped ${res.total_pages_stamped} page(s) across ${res.total_documents} document(s)`);
        if (inPlace) {
          await App.reloadDoc();
        } else if (res.download_url) {
          await API.downloadGet(res.download_url, 'stamped.zip');
        }
      } catch (error) { UI.err(error.message); }
    });
  }

  async function pickLogo() {
    const picker = el('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp' });
    picker.style.display = 'none';
    document.body.appendChild(picker);
    picker.addEventListener('change', async () => {
      if (!picker.files.length) { picker.remove(); return; }
      try {
        logo = await API.upload('/api/assets', [picker.files[0]], 'file');
        if (config.maintain_aspect_ratio && logo.aspect_ratio) {
          config.height_pt = Math.round(config.width_pt / logo.aspect_ratio);
        }
        UI.ok(`Logo loaded (${logo.width}×${logo.height})`);
        App.refreshSide();
      } catch (error) { UI.err(error.message); }
      picker.remove();
    });
    picker.click();
  }

  function setOverride(rect) {
    const id = doc()?.doc_id;
    if (!id) return;
    overrides[id] = overrides[id] || {};
    overrides[id][rect.page] = { x0: rect.x0, y0: rect.y0, x1: rect.x1, y1: rect.y1 };
    const entry = analysis.find((a) => a.doc_id === id);
    if (entry) {
      const placement = entry.page_placements.find((p) => p.page_number === rect.page);
      if (placement) {
        placement.rect = overrides[id][rect.page];
        placement.placed = true;
        placement.is_manual_override = true;
        placement.message = 'Manually positioned';
      }
    }
    drawOverlay();
    UI.ok(`Page ${rect.page} positioned by hand`);
    App.refreshSide();
  }

  // --------------------------------------------------------------- panel

  function build(host) {
    host.innerHTML = '';

    host.appendChild(el('h2', { class: 'section', text: 'Logo' }));
    if (logo) {
      host.appendChild(el('div', { class: 'row', style: 'margin-bottom:10px' }, [
        el('img', { src: `/api/assets/${logo.asset_id}`,
          style: 'max-width:110px; max-height:52px; background:#fff; border-radius:5px; padding:4px' }),
        el('div', { class: 'grow' }, [
          el('div', { style: 'font-size:12px', text: logo.filename }),
          el('div', { style: 'font-size:11px; color:var(--text-dim)', text: `${logo.width}×${logo.height}px` }),
        ]),
        el('button', { class: 'btn sm ghost', text: '✕', onClick: () => { logo = null; App.refreshSide(); } }),
      ]));
    } else {
      const zone = el('div', { class: 'dropzone', text: 'Click to choose a logo (PNG with transparency works best)' });
      zone.addEventListener('click', pickLogo);
      host.appendChild(zone);
    }

    host.appendChild(el('h2', { class: 'section', text: 'Size & position' }));
    host.appendChild(el('div', { class: 'field-pair' }, [
      field('Width (pt)', number(config.width_pt, (v) => {
        config.width_pt = v || 120;
        if (config.maintain_aspect_ratio && logo?.aspect_ratio) {
          config.height_pt = Math.round(config.width_pt / logo.aspect_ratio);
          App.refreshSide();
        }
      }, { min: '20', max: '400', step: '5' })),
      field('Height (pt)', number(config.height_pt, (v) => { config.height_pt = v || 40; },
        { min: '10', max: '300', step: '5' })),
    ]));

    const aspect = el('input', { type: 'checkbox',
      onChange: (e) => { config.maintain_aspect_ratio = e.target.checked; } });
    aspect.checked = config.maintain_aspect_ratio;
    host.appendChild(el('label', { class: 'row', style: 'margin-bottom:10px; cursor:pointer' }, [
      aspect, el('span', { text: 'Keep the logo’s aspect ratio', style: 'font-size:12px' }),
    ]));

    host.appendChild(el('div', { class: 'field-pair' }, [
      field('Side margin', number(config.margin_side, (v) => { config.margin_side = v || 0; }, { min: '0', max: '120' })),
      field('Bottom margin', number(config.margin_bottom, (v) => { config.margin_bottom = v || 0; }, { min: '0', max: '120' })),
    ]));

    host.appendChild(field('Clearance from content',
      el('input', { type: 'range', min: '0', max: '30', step: '1', value: String(config.content_padding),
        onInput: (e) => { config.content_padding = parseFloat(e.target.value); } }),
      'How far the logo must stay from text, tables and images.'));

    host.appendChild(field('Opacity',
      el('input', { type: 'range', min: '0.1', max: '1', step: '0.05', value: String(config.opacity),
        onInput: (e) => { config.opacity = parseFloat(e.target.value); } })));

    host.appendChild(field('Pages', select([
      ['all', 'Every page'], ['first', 'First page only'], ['last', 'Last page only'],
      ['all_except_first', 'All but the first'], ['custom', 'Custom…'],
    ], config.page_selection, (v) => { config.page_selection = v; App.refreshSide(); })));

    if (config.page_selection === 'custom') {
      host.appendChild(field('Which pages',
        el('input', { type: 'text', value: config.custom_pages || '', placeholder: '1, 3-5',
          onInput: (e) => { config.custom_pages = e.target.value; } })));
    }

    host.appendChild(field('If the page is crowded', select([
      ['shrink_to_fit', 'Shrink the logo to fit'],
      ['subtle_overlay', 'Place it as a faint overlay'],
      ['skip_page', 'Skip the page'],
    ], config.fallback_behavior, (v) => { config.fallback_behavior = v; })));

    // Targets + actions
    host.appendChild(el('h2', { class: 'section', text: 'Run' }));
    const scope = el('input', { type: 'checkbox', onChange: (e) => { targetAll = e.target.checked; } });
    scope.checked = targetAll;
    host.appendChild(el('label', { class: 'row', style: 'margin-bottom:10px; cursor:pointer' }, [
      scope, el('span', { text: `Apply to all ${App.documents().length} open PDF(s)`, style: 'font-size:12px' }),
    ]));

    const obstacles = el('input', { type: 'checkbox',
      onChange: (e) => { showObstacles = e.target.checked; drawOverlay(); } });
    obstacles.checked = showObstacles;
    host.appendChild(el('label', { class: 'row', style: 'margin-bottom:10px; cursor:pointer' }, [
      obstacles, el('span', { text: 'Show detected content boxes', style: 'font-size:12px' }),
    ]));

    const analyzeBtn = el('button', { class: 'btn', style: 'flex:1', text: 'Analyse' });
    analyzeBtn.addEventListener('click', () => analyze(analyzeBtn));
    host.appendChild(el('div', { class: 'row' }, [analyzeBtn]));

    const inPlaceBtn = el('button', { class: 'btn primary', style: 'flex:1', text: 'Stamp in place' });
    inPlaceBtn.addEventListener('click', () => apply(inPlaceBtn, true));
    const zipBtn = el('button', { class: 'btn', style: 'flex:1', text: 'Stamp → ZIP' });
    zipBtn.addEventListener('click', () => apply(zipBtn, false));
    host.appendChild(el('div', { class: 'row', style: 'margin-top:8px' }, [inPlaceBtn, zipBtn]));

    host.appendChild(el('button', {
      class: 'btn sm', style: 'width:100%; margin-top:8px',
      text: 'Drag a box to place this page by hand',
      onClick: () => {
        Viewer.setInteraction('rect', { onRect: setOverride });
        UI.toast('Drag where the logo should sit on this page');
      },
    }));

    const placement = currentPlacement();
    if (placement) {
      host.appendChild(el('div', {
        style: 'margin-top:12px; padding:10px; border-radius:6px; background:var(--surface-2); font-size:11.5px; line-height:1.6;',
      }, [
        el('span', {
          class: `badge ${placement.placed ? (placement.is_manual_override ? 'info' : 'ok') : 'err'}`,
          text: placement.strategy_used || 'none',
        }),
        el('div', { text: placement.message, style: 'margin-top:6px; color:var(--text-muted)' }),
      ]));
    }
  }

  return {
    id: 'stamp',
    activate(host) {
      build(host);
      Viewer.setInteraction('none');
      drawOverlay();
    },
    deactivate() { Viewer.clearOverlay(); Viewer.setInteraction('none'); },
    refresh(host) { build(host); drawOverlay(); },
    onPage() { drawOverlay(); },
  };
})();
