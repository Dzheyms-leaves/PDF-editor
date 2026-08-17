/* Panels mode — the original batch panel extractor, rebuilt on the shared shell.

   Keeps everything that made the browser tool fast: per-label copy chips with
   sticky green ticks, matching-configuration grouping, 1-9 / N keyboard
   shortcuts, and the EZcad2 CSV layout. Scanned sheets now go through OCR
   instead of dead-ending. */
window.ModePanels = (() => {
  const { el, field, select } = UI;

  let queue = [];          // PanelEntry[] across every loaded document
  let activeId = null;
  let chipRefs = [];
  let engines = [];
  let engineChoice = '';
  const openGroups = new Set();

  function doc() { return App.currentDoc(); }
  function active() { return queue.find((e) => e.panel_id === activeId) || null; }
  function includedLabels(entry) {
    return entry.rows.flat().filter((l) => l.include && l.text.trim()).map((l) => l.text.trim());
  }

  // -------------------------------------------------------------- extract

  async function extractCurrent(button, forceOcr = false) {
    const id = doc()?.doc_id;
    if (!id) { UI.err('Open a panel PDF first'); return; }
    await UI.busy(button, async () => {
      try {
        const res = await API.post(`/api/documents/${id}/panels`, {
          force_ocr: forceOcr, engine: engineChoice || null, style: 'auto',
        });
        queue = queue.filter((e) => e.source_doc !== id);
        res.entries.forEach((entry) => {
          entry.source_doc = id;
          queue.push(entry);
        });
        if (!activeId || !active()) activeId = queue[0]?.panel_id || null;
        (res.warnings || []).forEach((w) => UI.toast(w));
        UI.ok(`${res.entries.length} panel(s) from ${res.filename} · ${res.style}${res.used_ocr ? ' · OCR' : ''}`);
        App.refreshSide();
      } catch (error) { UI.err(error.message); }
    });
  }

  async function extractAll(button) {
    await UI.busy(button, async () => {
      for (const d of App.documents()) {
        try {
          const res = await API.post(`/api/documents/${d.doc_id}/panels`, {
            engine: engineChoice || null, style: 'auto',
          });
          queue = queue.filter((e) => e.source_doc !== d.doc_id);
          res.entries.forEach((entry) => { entry.source_doc = d.doc_id; queue.push(entry); });
        } catch (error) { UI.err(`${d.filename}: ${error.message}`); }
      }
      if (!active()) activeId = queue[0]?.panel_id || null;
      UI.ok(`${queue.length} panels loaded`);
      App.refreshSide();
    });
  }

  // ------------------------------------------------------------ behaviour

  /** Select a panel and show it: switch document if needed, then jump to its page. */
  function selectPanel(entry) {
    activeId = entry.panel_id;
    if (entry.source_doc && doc()?.doc_id !== entry.source_doc) {
      App.select(entry.source_doc);
    }
    if (entry.page) Viewer.goto(entry.page);
    App.refreshSide();
  }

  function markDoneAndNext(entry) {
    entry.rows.flat().forEach((l) => { l.copied = false; });
    entry.done = true;
    goNext();
  }

  function goNext() {
    const index = queue.findIndex((e) => e.panel_id === activeId);
    const next = queue.find((e, i) => i > index && !e.done) || queue.find((e) => !e.done);
    activeId = next ? next.panel_id : (queue[index + 1]?.panel_id || activeId);
    App.refreshSide();
  }

  function markGroupDone(panelIds) {
    queue.forEach((entry) => {
      if (panelIds.includes(entry.panel_id)) {
        entry.rows.flat().forEach((l) => { l.copied = false; });
        entry.done = true;
      }
    });
    const next = queue.find((e) => !e.done);
    if (next) activeId = next.panel_id;
    App.refreshSide();
  }

  function configKey(entry) {
    return entry.rows
      .map((row) => row.filter((l) => l.include).map((l) => l.text.trim()).join(' | '))
      .filter(Boolean).join('\n').toLowerCase();
  }

  function findGroups() {
    const groups = new Map();
    queue.forEach((entry) => {
      if (entry.error || !entry.rows.length) return;
      const key = configKey(entry);
      if (!key) return;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(entry);
    });
    return [...groups.entries()].filter(([, members]) => members.length > 1);
  }

  // ---------------------------------------------------------------- panel

  function build(host) {
    host.innerHTML = '';
    chipRefs = [];

    host.appendChild(el('h2', { class: 'section', text: 'Extract panels' }));
    const one = el('button', { class: 'btn primary', style: 'flex:1', text: 'This PDF' });
    one.addEventListener('click', () => extractCurrent(one));
    const all = el('button', { class: 'btn', style: 'flex:1', text: 'All open' });
    all.addEventListener('click', () => extractAll(all));
    host.appendChild(el('div', { class: 'row' }, [one, all]));

    const ocrBtn = el('button', { class: 'btn sm', style: 'width:100%; margin-top:8px',
      text: 'Force OCR on this PDF' });
    ocrBtn.addEventListener('click', () => extractCurrent(ocrBtn, true));
    host.appendChild(ocrBtn);

    const available = engines.filter((e) => e.available && e.name !== 'native');
    if (available.length) {
      host.appendChild(field('OCR engine', select(
        [['', 'Automatic'], ...available.map((e) => [e.name, e.label])],
        engineChoice, (v) => { engineChoice = v; },
      )));
    }

    if (!queue.length) {
      host.appendChild(el('p', { class: 'empty',
        text: 'No panels yet. Dynalite spec sheets and Smart Home Works job packs are detected automatically.' }));
      return;
    }

    // Queue
    const done = queue.filter((e) => e.done).length;
    host.appendChild(el('h2', { class: 'section', text: `Queue — ${done} of ${queue.length} done` }));

    const list = el('div', {});
    queue.forEach((entry) => {
      const row = el('div', {
        class: `queue-item${entry.panel_id === activeId ? ' active' : ''}${entry.done ? ' is-done' : ''}`,
      }, [
        el('span', { class: `dot${entry.done ? ' done' : ''}`, text: entry.done ? '✓' : '' }),
        el('span', { class: 'q-name', text: entry.name, title: entry.name }),
        el('span', { style: 'color:var(--text-dim); font-size:11px', text: `p${entry.page}` }),
      ]);
      row.addEventListener('click', () => selectPanel(entry));
      list.appendChild(row);
    });
    host.appendChild(list);

    // Matching configuration groups
    findGroups().forEach(([key, members]) => {
      const doneCount = members.filter((m) => m.done).length;
      const allDone = doneCount === members.length;
      const box = el('div', { class: `config-group${openGroups.has(key) ? ' open' : ''}` });
      const head = el('div', { class: 'cg-head' }, [
        el('span', { class: 'cg-caret', text: '▶' }),
        el('span', { style: 'flex:1', html: `<strong>Matching config</strong> <span class="cg-count">×${members.length}</span>`
          + (doneCount ? ` <span style="color:var(--done); font-size:11px">(${doneCount} done)</span>` : '') }),
      ]);
      head.addEventListener('click', () => {
        if (openGroups.has(key)) openGroups.delete(key); else openGroups.add(key);
        App.refreshSide();
      });
      box.appendChild(head);
      box.appendChild(el('div', { class: 'cg-preview', text: includedLabels(members[0]).join(' · ') }));

      const body = el('div', { class: 'cg-body' });
      members.forEach((member) => {
        const btn = el('button', { class: `cg-panel${member.done ? ' is-done' : ''}`, text: member.name });
        btn.addEventListener('click', () => selectPanel(member));
        body.appendChild(btn);
      });
      body.appendChild(el('button', {
        class: 'btn sm', style: 'width:100%; margin-top:7px',
        text: allDone ? 'All marked done ✓' : `Mark all ${members.length} done`,
        disabled: allDone,
        onClick: () => markGroupDone(members.map((m) => m.panel_id)),
      }));
      box.appendChild(body);
      host.appendChild(box);
    });

    // Active panel
    const entry = active();
    if (entry) {
      host.appendChild(el('h2', { class: 'section', text: 'Current panel' }));
      const nameInput = el('input', { type: 'text', value: entry.name,
        onInput: (e) => { entry.name = e.target.value; } });
      host.appendChild(field('Panel name', nameInput));

      host.appendChild(el('p', { class: 'hint',
        text: 'Click a copy icon (or press its number) to copy one label. Untick anything that is not a button label.' }));

      const rowsWrap = el('div', {});
      entry.rows.forEach((row) => {
        const chips = el('div', { class: 'chips', style: 'margin-bottom:7px' });
        row.forEach((label) => {
          const chip = el('div', {
            class: `chip${label.include ? '' : ' off'}${label.copied ? ' copied' : ''}`,
          });
          const box = el('input', { type: 'checkbox' });
          box.checked = label.include;
          const text = el('input', { type: 'text', value: label.text,
            size: Math.max(label.text.length, 3) });
          const copyBtn = el('button', { class: 'copy-btn', text: label.copied ? '✓' : '⧉',
            title: 'Copy this label' });

          const number = label.include ? chipRefs.length + 1 : null;
          chip.appendChild(box);
          if (number && number <= 9) chip.appendChild(el('span', { class: 'key-hint', text: String(number) }));
          chip.appendChild(text);
          chip.appendChild(copyBtn);

          box.addEventListener('change', (e) => {
            label.include = e.target.checked;
            App.refreshSide();
          });
          text.addEventListener('input', (e) => {
            label.text = e.target.value;
            text.setAttribute('size', Math.max(label.text.length, 3));
            label.copied = false;
            chip.classList.remove('copied');
            copyBtn.textContent = '⧉';
          });
          const doCopy = async () => {
            if (await UI.copy(label.text)) {
              label.copied = true;
              chip.classList.add('copied');
              copyBtn.textContent = '✓';
            }
          };
          copyBtn.addEventListener('click', doCopy);
          if (label.include) chipRefs.push({ copy: doCopy });
          chips.appendChild(chip);
        });
        rowsWrap.appendChild(chips);
      });
      host.appendChild(rowsWrap);

      host.appendChild(el('div', { class: 'row', style: 'margin-top:10px' }, [
        el('button', {
          class: 'btn sm', text: 'Copy whole panel',
          onClick: (e) => UI.copyFrom(e.target, panelText(entry)),
        }),
        el('button', {
          class: 'btn sm primary', html: 'Mark done → <kbd>N</kbd>',
          onClick: () => markDoneAndNext(entry),
        }),
      ]));
    }

    // Export
    host.appendChild(el('h2', { class: 'section', text: 'Export job' }));
    const jobName = el('input', { type: 'text', value: jobDefault() });
    host.appendChild(field('Job name', jobName));
    host.appendChild(el('div', { class: 'row tight' },
      [['csv', 'CSV for EZcad2'], ['xlsx', 'Excel'], ['txt', 'Job sheet']].map(([fmt, label]) =>
        el('button', {
          class: 'btn sm', text: label,
          onClick: () => API.download('/api/panels/export', {
            entries: queue.map(stripLocal), job_name: jobName.value, fmt,
          }).catch((e) => UI.err(e.message)),
        }))));

    host.appendChild(el('button', {
      class: 'btn sm danger', style: 'width:100%; margin-top:10px', text: 'Clear queue',
      onClick: async () => {
        if (await UI.confirm('Clear every extracted panel?', { danger: true })) {
          queue = []; activeId = null; App.refreshSide();
        }
      },
    }));
  }

  function stripLocal(entry) {
    const { source_doc, ...rest } = entry;
    return rest;
  }

  function jobDefault() {
    return queue[0]?.source_file || doc()?.filename?.replace(/\.pdf$/i, '') || 'panel-job';
  }

  function panelText(entry) {
    const lines = entry.rows
      .map((row) => row.filter((l) => l.include).map((l) => l.text).join('     '))
      .filter(Boolean);
    return `Panel: ${entry.name}\n\n${lines.join('\n')}`;
  }

  function onKey(event) {
    if (event.ctrlKey || event.altKey || event.metaKey) return;
    const tag = (event.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;

    if (event.key >= '1' && event.key <= '9') {
      const ref = chipRefs[parseInt(event.key, 10) - 1];
      if (ref) { ref.copy(); event.preventDefault(); }
    } else if (event.key === 'n' || event.key === 'N') {
      const entry = active();
      if (entry && !entry.error && entry.rows.length) {
        markDoneAndNext(entry);
        event.preventDefault();
      }
    }
  }

  return {
    id: 'panels',
    async activate(host) {
      try { engines = (await API.get('/api/ocr/capabilities')).engines || []; }
      catch (_) { engines = []; }
      build(host);
      Viewer.setInteraction('none');
      document.addEventListener('keydown', onKey);
    },
    deactivate() { document.removeEventListener('keydown', onKey); },
    refresh(host) { build(host); },
  };
})();
