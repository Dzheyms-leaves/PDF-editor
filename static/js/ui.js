/* Shared UI primitives: toasts, modals, clipboard, small DOM helpers. */
window.UI = (() => {
  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'html') node.innerHTML = value;
      else if (key === 'text') node.textContent = value;
      else if (key.startsWith('on') && typeof value === 'function') {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else if (value === true) node.setAttribute(key, '');
      else node.setAttribute(key, value);
    }
    for (const child of [].concat(children)) {
      if (child === null || child === undefined) continue;
      node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    }
    return node;
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  function toast(message, kind = '', ms = 3400) {
    const host = document.getElementById('toasts');
    const node = el('div', { class: `toast ${kind}`, text: message });
    host.appendChild(node);
    setTimeout(() => {
      node.style.opacity = '0';
      node.style.transition = 'opacity .25s';
      setTimeout(() => node.remove(), 260);
    }, ms);
    return node;
  }

  const ok = (m) => toast(m, 'ok');
  const err = (m) => toast(m, 'err', 6000);

  /** Clipboard with a fallback for non-secure origins (plain http on a LAN). */
  async function copy(text) {
    const value = String(text ?? '');
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(value);
        return true;
      }
    } catch (_) { /* fall through */ }
    try {
      const area = el('textarea', { style: 'position:fixed;opacity:0;top:0;left:0' });
      area.value = value;
      document.body.appendChild(area);
      area.select();
      const done = document.execCommand('copy');
      area.remove();
      return done;
    } catch (_) {
      return false;
    }
  }

  /** Copy and flash the element green so the user can see it landed. */
  async function copyFrom(node, text) {
    const done = await copy(text);
    if (!done) { err('Could not reach the clipboard'); return false; }
    node.classList.add('copied');
    const previous = node.dataset.copyLabel;
    if (previous !== undefined) node.textContent = '✓';
    setTimeout(() => {
      node.classList.remove('copied');
      if (previous !== undefined) node.textContent = previous;
    }, 1100);
    return true;
  }

  function modal({ title, body, actions = [], onClose, wide = false }) {
    const root = document.getElementById('modal-root');
    const close = () => { root.innerHTML = ''; if (onClose) onClose(); };
    const foot = el('div', { class: 'modal-foot' },
      actions.map((action) => el('button', {
        class: `btn ${action.kind || ''}`,
        text: action.label,
        onClick: () => action.onClick ? action.onClick(close) : close(),
      })));
    const backdrop = el('div', { class: 'modal-backdrop' }, [
      el('div', { class: `modal${wide ? ' wide' : ''}` }, [
        el('div', { class: 'modal-head' }, [
          el('h3', { text: title }),
          el('button', { class: 'btn icon ghost', text: '✕', onClick: close }),
        ]),
        el('div', { class: 'modal-body' }, [body]),
        actions.length ? foot : null,
      ]),
    ]);
    backdrop.addEventListener('mousedown', (event) => {
      if (event.target === backdrop) close();
    });
    root.innerHTML = '';
    root.appendChild(backdrop);
    return close;
  }

  /* Closing the dialog is what answers it. An earlier version resolved inside
     the button handlers as well, and since `close()` runs `onClose` first, the
     promise had already settled on `false` by then — every confirmed action
     silently did nothing. The answer is recorded, then the close resolves. */
  function confirm(message, { title = 'Are you sure?', danger = false } = {}) {
    return new Promise((resolve) => {
      let answer = false;
      modal({
        title,
        body: el('p', { text: message, style: 'margin:0; line-height:1.6;' }),
        actions: [
          { label: 'Cancel', onClick: (close) => close() },
          {
            label: danger ? 'Yes, do it' : 'Continue',
            kind: danger ? 'danger' : 'primary',
            onClick: (close) => { answer = true; close(); },
          },
        ],
        onClose: () => resolve(answer),
      });
    });
  }

  /** Wrap an async action with a busy state on its button. */
  async function busy(button, fn) {
    if (!button) return fn();
    const label = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span>';
    try {
      return await fn();
    } finally {
      button.disabled = false;
      button.innerHTML = label;
    }
  }

  function field(label, control, hint) {
    return el('div', { class: 'field' }, [
      el('label', { text: label }),
      control,
      hint ? el('p', { class: 'hint', text: hint, style: 'margin-bottom:0' }) : null,
    ]);
  }

  function select(options, value, onChange) {
    const node = el('select', { onChange: (e) => onChange(e.target.value) },
      options.map(([val, text]) => el('option', { value: val, selected: val === value }, text)));
    node.value = value;
    return node;
  }

  function number(value, onChange, attrs = {}) {
    return el('input', {
      type: 'number', value, ...attrs,
      onInput: (e) => onChange(parseFloat(e.target.value)),
    });
  }

  return { el, escapeHtml, toast, ok, err, copy, copyFrom, modal, confirm, busy, field, select, number };
})();
