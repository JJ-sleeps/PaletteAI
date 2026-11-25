/* PaletteAI front-end: plain JavaScript, no build step, no third-party libraries. */
'use strict';

const API = '/api/v1';
const CVD_LABELS = {
  protanopia: 'Protanopia (no red cones)',
  deuteranopia: 'Deuteranopia (no green cones)',
  tritanopia: 'Tritanopia (no blue cones)',
};
const state = { user: 'guest', palette: null };
const $ = (sel, root = document) => root.querySelector(sel);

/* ---------------------------------------------------------------- helpers */
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k === 'style') Object.assign(el.style, v);
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) el.append(kid);
  return el;
}

async function api(path, options = {}) {
  const opts = { ...options };
  if (opts.json !== undefined) {
    opts.body = JSON.stringify(opts.json);
    opts.headers = { 'Content-Type': 'application/json' };
    delete opts.json;
  }
  let res;
  try {
    res = await fetch(API + path, opts);
  } catch (e) {
    throw new Error('Could not reach the PaletteAI server. Is it still running?');
  }
  if (res.status === 204) return null;
  let body = null;
  try { body = await res.json(); } catch (e) { /* non-JSON error page */ }
  if (!res.ok) {
    const detail = body && body.detail;
    throw new Error((detail && detail.message) || `Request failed (${res.status}).`);
  }
  return body;
}

let toastTimer;
function announce(message) {
  const toast = $('#toast');
  toast.textContent = message;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.textContent = ''; }, 4000);
}

function showError(box, message) {
  box.textContent = message || '';
  box.hidden = !message;
}

const userParam = () => 'user=' + encodeURIComponent(state.user);
const fmtPct = (v) => v.toFixed(1) + '%';
const fmtScore = (v) => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(2);

function textOn(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return 1.05 / (lum + 0.05) >= (lum + 0.05) / 0.05 ? '#FFFFFF' : '#000000';
}

function verdict(passed) {
  return h('span', { class: 'verdict ' + (passed ? 'pass' : 'fail') }, passed ? '✓ Pass' : '✗ Fail');
}

function dot(hex) {
  return h('span', { class: 'dot', style: { background: hex }, 'aria-hidden': 'true' });
}

async function copyHex(hex) {
  try {
    await navigator.clipboard.writeText(hex);
    announce(`Copied ${hex}`);
  } catch (e) {
    announce(`Could not copy automatically. The value is ${hex}`);
  }
}

/** A colour block that always carries its HEX value and name as text. */
function swatchButton(color, sub, onclick) {
  const hex = color.hex;
  const props = { class: 'swatch-btn', style: { background: hex, color: color.text_color || textOn(hex) } };
  const kids = [h('span', { text: hex }), h('span', { class: 'sub', text: sub || color.name || '' })];
  if (onclick === null) return h('div', props, kids);
  return h('button', { ...props, type: 'button', title: 'Copy ' + hex, 'aria-label': `Copy ${hex}${sub || color.name ? ', ' + (sub || color.name) : ''}`,
    onclick: onclick || (() => copyHex(hex)) }, kids);
}

/**
 * Horizontal bar chart built as a real table: the chart *is* its own data table,
 * so it works with screen readers, text browsers and without colour.
 * rows: [{label: Node|string, value, display, color?, chosen?, note?}]
 */
function barTable({ caption, headers, rows, max, diverging = false }) {
  const top = max || Math.max(...rows.map((r) => Math.abs(r.value)), 0.0001);
  return h('div', { class: 'table-wrap' },
    h('table', { class: 'bars' },
      h('caption', { text: caption }),
      h('thead', null, h('tr', null,
        h('th', { scope: 'col', text: headers[0] }),
        h('th', { scope: 'col' }, h('span', { class: 'visually-hidden', text: 'Bar' })),
        h('th', { scope: 'col', class: 'num', text: headers[1] }),
        headers[2] ? h('th', { scope: 'col', text: headers[2] }) : null)),
      h('tbody', null, rows.map((r) => {
        const frac = Math.min(1, Math.abs(r.value) / top);
        const style = diverging
          ? { width: frac * 50 + '%', left: r.value < 0 ? (50 - frac * 50) + '%' : '50%' }
          : { width: frac * 100 + '%' };
        if (r.color) style.background = r.color;
        const cls = 'bar' + (r.color ? ' outlined' : '') + (diverging && r.value < 0 ? ' neg' : '');
        return h('tr', { class: r.chosen ? 'chosen' : null },
          h('th', { scope: 'row' }, r.label),
          h('td', { class: 'bar-cell' }, h('div', { class: 'track' + (diverging ? ' diverging' : ''), 'aria-hidden': 'true' },
            h('div', { class: cls, style, title: r.display }))),
          h('td', { class: 'num', text: r.display }),
          headers[2] ? h('td', null, r.note || '') : null);
      }))));
}

/* ---------------------------------------------------------------- theme, profile, tabs */
function initTheme() {
  const toggle = $('#theme-toggle');            // a checkbox with role="switch": checked = dark theme
  const sync = () => { toggle.checked = document.documentElement.dataset.theme === 'dark'; };
  toggle.addEventListener('change', () => {
    const next = toggle.checked ? 'dark' : 'light';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('paletteai.theme', next); } catch (e) { /* private mode */ }
    sync();
    announce(next === 'dark' ? 'Dark theme on' : 'Light theme on');
  });
  sync();
}

function initProfile() {
  try { state.user = localStorage.getItem('paletteai.user') || 'guest'; } catch (e) { /* private mode */ }
  if (!/^[A-Za-z0-9_.-]{1,40}$/.test(state.user)) state.user = 'guest';
  const input = $('#user-input');
  const apply = $('#user-apply');
  input.value = state.user;
  // "Use" only lights up when the name differs from the active one.
  const sync = () => { apply.disabled = !input.value.trim() || input.value.trim() === state.user; };
  input.addEventListener('input', sync);
  $('#profile-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if (!input.checkValidity()) { input.reportValidity(); return; }
    state.user = input.value.trim();
    input.value = state.user;
    sync();
    try { localStorage.setItem('paletteai.user', state.user); } catch (e) { /* private mode */ }
    announce(`Now using ${state.user}'s saved palettes, likes and ranking.`);
    if (state.palette) openPalette(state.palette.id, false);
    if (!$('#panel-library').hidden) loadLibrary();
  });
}

const tabs = () => [...document.querySelectorAll('[role="tab"]')];
function selectTab(id, focus = false) {
  for (const tab of tabs()) {
    const on = tab.id === 'tab-' + id;
    tab.setAttribute('aria-selected', String(on));
    tab.tabIndex = on ? 0 : -1;
    $('#' + tab.getAttribute('aria-controls')).hidden = !on;
    if (on && focus) tab.focus();
  }
  if (id === 'library') loadLibrary();
  if (id === 'developers') loadPlugins();
  if (id === 'contrast') runContrast();
  if (id === 'harmony' && !$('#harmony-result').hasChildNodes()) runHarmony();
}
function initTabs() {
  const list = tabs();
  list.forEach((tab, i) => {
    tab.addEventListener('click', () => selectTab(tab.id.slice(4)));
    tab.addEventListener('keydown', (ev) => {
      const move = { ArrowRight: 1, ArrowLeft: -1 }[ev.key];
      let target = null;
      if (move) target = list[(i + move + list.length) % list.length];
      if (ev.key === 'Home') target = list[0];
      if (ev.key === 'End') target = list[list.length - 1];
      if (target) { ev.preventDefault(); selectTab(target.id.slice(4), true); }
    });
  });
}

/* ---------------------------------------------------------------- extraction */
async function loadSamples() {
  const list = $('#samples');
  try {
    const samples = await api('/samples');
    list.replaceChildren(...samples.map((s) => h('li', null,
      h('button', { type: 'button', class: 'sample-btn', onclick: () => extract(() => api(`/samples/${s.id}/extract${kQuery()}`, { method: 'POST' })) },
        h('img', { src: s.url, alt: '', loading: 'lazy', width: 240, height: 160 }),
        h('span', { class: 'name', text: s.name }),
        h('span', { class: 'desc', text: s.description })))));
  } catch (e) {
    list.replaceChildren(h('li', { text: e.message }));
  }
}

const kQuery = () => ($('#k-select').value ? '?k=' + $('#k-select').value : '');

// Loading state shown where the result will appear. Waits 200 ms first so a stored
// (cached) result, which comes back almost at once, doesn't flash the loader.
function showLoader() {
  const result = $('#result');
  const before = { hidden: result.hidden, kids: [...result.childNodes] };
  const timer = setTimeout(() => {
    result.hidden = false;
    result.replaceChildren(h('div', { class: 'card extract-loading' },
      h('div', { class: 'swatch-loader', 'aria-hidden': 'true' }, ...[0, 1, 2, 3, 4].map(() => h('span'))),
      h('p', { text: 'Extracting palette…' }),
      h('p', { class: 'meta', text: $('#k-select').value
        ? `Sampling pixels and grouping them into ${$('#k-select').value} colours`
        : 'Sampling pixels and testing 3–8 colour counts' })));
    $('#result').scrollIntoView({ block: 'nearest' });
  }, 200);
  return {
    cancel: () => clearTimeout(timer),          // success: the real result replaces it
    restore: () => {                            // failure: put back what was there before
      clearTimeout(timer);
      result.replaceChildren(...before.kids);
      result.hidden = before.hidden;
    },
  };
}

async function extract(request) {
  const btn = $('#extract-btn');
  showError($('#extract-error'), '');
  btn.disabled = true;
  announce('Extracting palette…');
  const loader = showLoader();
  try {
    const { palette, cached } = await request();
    // Re-read with the current profile so saved / feedback state is right.
    const mine = state.user === 'guest' ? palette : await api(`/palettes/${palette.id}?${userParam()}`);
    loader.cancel();
    renderResult(mine, cached);
    announce(`Palette ready: ${mine.k} colours${cached ? ' (loaded from the database)' : ''}.`);
    showResult();
  } catch (e) {
    loader.restore();
    showError($('#extract-error'), e.message);
    announce('');
  } finally {
    btn.disabled = false;
  }
}

function initUpload() {
  const input = $('#file-input');
  const zone = $('#dropzone');
  const send = (file) => {
    if (!file) { showError($('#extract-error'), 'Choose an image file first.'); input.focus(); return; }
    const form = new FormData();
    form.append('file', file);
    if ($('#k-select').value) form.append('k', $('#k-select').value);
    extract(() => api('/images', { method: 'POST', body: form }));
  };
  $('#upload-form').addEventListener('submit', (ev) => { ev.preventDefault(); send(input.files[0]); });
  for (const type of ['dragenter', 'dragover']) {
    zone.addEventListener(type, (ev) => { ev.preventDefault(); zone.classList.add('dragover'); });
  }
  for (const type of ['dragleave', 'drop']) {
    zone.addEventListener(type, (ev) => { ev.preventDefault(); zone.classList.remove('dragover'); });
  }
  zone.addEventListener('drop', (ev) => send(ev.dataTransfer.files[0]));
}

// Bring a freshly rendered result to the top of the screen and give it keyboard focus.
// focus() on its own only scrolls until the heading peeks in at the bottom edge.
function showResult() {
  const heading = $('#result-heading');
  if (!heading) return;
  const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  heading.focus({ preventScroll: true });
  $('#result').scrollIntoView({ block: 'start', behavior: smooth ? 'smooth' : 'auto' });
}

async function openPalette(id, switchTab = true) {
  try {
    const palette = await api(`/palettes/${id}?${userParam()}`);
    if (switchTab) selectTab('extract');
    renderResult(palette, null);
    if (switchTab) showResult();
  } catch (e) {
    announce(e.message);
  }
}

/* ---------------------------------------------------------------- result view */
function actionBar(p, onChange) {
  const act = async (path, options, message) => {
    try {
      const updated = await api(path, options);
      announce(message);
      onChange(updated);
    } catch (e) { announce(e.message); }
  };
  const feedbackBtn = (value, label) => h('button', {
    type: 'button', class: 'btn', 'aria-pressed': String(p.feedback === value),
    onclick: () => (p.feedback === value
      ? act(`/palettes/${p.id}/feedback?${userParam()}`, { method: 'DELETE' }, 'Feedback cleared.')
      : act(`/palettes/${p.id}/feedback`, { method: 'PUT', json: { user: state.user, value } },
        `${value === 'like' ? 'Liked' : 'Disliked'}. Your ranking has been updated.`)),
  }, label);
  return h('div', { class: 'btn-row', role: 'group', 'aria-label': 'Palette actions' },
    h('button', {
      type: 'button', class: 'btn' + (p.saved ? '' : ' primary'), 'aria-pressed': String(p.saved),
      onclick: () => (p.saved
        ? act(`/palettes/${p.id}/save?${userParam()}`, { method: 'DELETE' }, 'Removed from your library.')
        : act(`/palettes/${p.id}/save`, { method: 'PUT', json: { user: state.user } }, 'Saved to your library.')),
    }, p.saved ? '✓ Saved' : 'Save to library'),
    feedbackBtn('like', '\u{1F44D} Like'),
    feedbackBtn('dislike', '\u{1F44E} Dislike'));
}

function previewFigure(p) {
  const base = p.image.preview_url;
  const colours = p.colors.map((c) => `${c.name} ${fmtPct(c.percent)}`).join(', ');
  const img = h('img', { src: base, alt: `${p.image.filename}. Dominant colours: ${colours}.` });
  const caption = h('figcaption', { text: 'Shown with normal colour vision.' });
  const radio = (value, label) => h('label', { class: 'radio' },
    h('input', {
      type: 'radio', name: 'cvd', value, checked: value === '',
      onchange: () => {
        img.src = value ? `${base}?cvd=${value}` : base;
        img.alt = `${p.image.filename}${value ? ', simulated ' + value : ''}. Dominant colours: ${colours}.`;
        caption.textContent = value ? `Simulated ${CVD_LABELS[value]}.` : 'Shown with normal colour vision.';
      },
    }), label);
  return h('div', null,
    h('figure', { class: 'preview' }, img, caption),
    h('fieldset', { style: { marginTop: '.75rem' } },
      h('legend', { text: 'Colour-vision preview' }),
      radio('', 'Normal'), radio('protanopia', 'Protanopia'), radio('deuteranopia', 'Deuteranopia'), radio('tritanopia', 'Tritanopia')));
}

function swatchList(p) {
  return h('ol', { class: 'swatches', 'aria-label': 'Palette colours, most dominant first' },
    p.colors.map((c) => h('li', { class: 'swatch-row' },
      h('div', { class: 'chip', style: { background: c.hex, color: c.text_color }, 'aria-hidden': 'true' }, fmtPct(c.percent)),
      h('div', { class: 'swatch-info' },
        h('div', { class: 'hex', text: c.hex }),
        h('div', { class: 'sub', title: `rgb(${c.rgb.join(', ')})`, text: `${c.name} · ${fmtPct(c.percent)} of image` })),
      h('div', { class: 'btn-row' },
        h('button', { type: 'button', class: 'btn small', 'aria-label': `Copy ${c.hex}`, onclick: () => copyHex(c.hex) }, 'Copy'),
        h('button', { type: 'button', class: 'btn small', 'aria-label': `Harmonies for ${c.hex}`,
          onclick: () => { setColor('base', c.hex); selectTab('harmony', true); runHarmony(); } }, 'Harmonies')))));
}

function kSection(p) {
  const kids = [h('h3', { text: 'How the colour count was chosen' }), h('p', { text: p.reason })];
  if (p.fallback_used) {
    kids.push(h('p', { class: 'status-line warn' }, h('span', { class: 'icon', text: '⚠' }),
      'Fallback used: the silhouette scores could not separate the candidates.'));
  }
  if (p.k_scores.length > 1) {
    kids.push(barTable({
      caption: 'Silhouette score for each candidate number of colours (higher means better-separated clusters; maximum 1)',
      headers: ['Colours (k)', 'Silhouette', 'Result'],
      max: 1,
      rows: p.k_scores.map((s) => ({
        label: `k = ${s.k}`, value: Math.max(0, s.silhouette), display: s.silhouette.toFixed(3),
        chosen: s.chosen, note: s.chosen ? h('span', { class: 'tag', text: '✓ chosen' }) : '',
      })),
    }));
  } else if (p.silhouette != null) {
    kids.push(h('p', { class: 'meta', text: `Silhouette score at k = ${p.k}: ${p.silhouette.toFixed(3)}` }));
  }
  return h('div', { class: 'card' }, kids);
}

function cvdSection(p) {
  const cell = (hex) => h('td', null, dot(hex), h('span', { class: 'mono', text: hex }));
  return h('div', { class: 'card' },
    h('h3', { text: 'Palette under colour-vision deficiencies' }),
    h('div', { class: 'table-wrap' }, h('table', null,
      h('caption', { text: 'How each palette colour appears with three common colour-vision deficiencies (simulated)' }),
      h('thead', null, h('tr', null, ['Colour', 'Normal', 'Protanopia', 'Deuteranopia', 'Tritanopia'].map((t) => h('th', { scope: 'col', text: t })))),
      h('tbody', null, p.colors.map((c) => h('tr', null,
        h('th', { scope: 'row', text: c.name }), cell(c.hex), cell(c.cvd.protanopia), cell(c.cvd.deuteranopia), cell(c.cvd.tritanopia)))))));
}

function contrastSection(p) {
  const a = p.accessibility;
  const kids = [h('h3', { text: 'Contrast between palette colours' })];
  if (!a.total_pairs) {
    kids.push(h('p', { text: 'This palette has one colour, so there are no pairs to compare.' }));
    return h('div', { class: 'card' }, kids);
  }
  kids.push(h('p', { text: `${a.aa_normal_pairs} of ${a.total_pairs} colour pairs pass WCAG AA for normal text (4.5:1); ` +
    `${a.aa_large_pairs} pass for large text (3:1).` }));
  kids.push(h('div', { class: 'table-wrap' }, h('table', null,
    h('caption', { text: 'Every pair of palette colours, highest contrast first' }),
    h('thead', null, h('tr', null,
      ['Pair', 'Sample', 'Contrast', 'AA normal text', 'AA large text', 'AAA normal text', ''].map((t, i) =>
        h('th', { scope: 'col', class: i === 2 ? 'num' : null }, t || h('span', { class: 'visually-hidden', text: 'Actions' }))))),
    h('tbody', null, a.pairs.map((pair) => h('tr', null,
      h('th', { scope: 'row', class: 'mono' }, dot(pair.a_hex), pair.a_hex, ' / ', dot(pair.b_hex), pair.b_hex),
      h('td', null, h('span', { class: 'aa-sample', style: { color: pair.a_hex, background: pair.b_hex }, 'aria-hidden': 'true' }, 'Aa'), ' ',
        h('span', { class: 'aa-sample', style: { color: pair.b_hex, background: pair.a_hex }, 'aria-hidden': 'true' }, 'Aa')),
      h('td', { class: 'num', text: pair.ratio.toFixed(2) + ':1' }),
      h('td', null, verdict(pair.aa_normal_text)),
      h('td', null, verdict(pair.aa_large_text)),
      h('td', null, verdict(pair.aaa_normal_text)),
      h('td', null, h('button', { type: 'button', class: 'btn small', 'aria-label': `Open ${pair.a_hex} on ${pair.b_hex} in the contrast checker`,
        onclick: () => { setColor('fg', pair.a_hex); setColor('bg', pair.b_hex); selectTab('contrast', true); } }, 'Check'))))))));
  return h('div', { class: 'card' }, kids);
}

function renderResult(p, cached) {
  state.palette = p;
  const actions = h('div');
  const drawActions = (pal) => { state.palette = pal; actions.replaceChildren(actionBar(pal, drawActions)); };
  drawActions(p);
  const img = p.image;
  const meta = [`${img.width} × ${img.height} px`, img.source === 'sample' ? 'sample image' : 'uploaded image'];
  if (img.has_alpha && img.transparent_fraction > 0) meta.push(`${Math.round(img.transparent_fraction * 100)}% transparent pixels ignored`);
  if (cached) meta.push('identical image found: loaded stored result without re-processing');
  const pluginArea = h('div', { id: 'plugin-panels', 'aria-live': 'polite' }, h('p', { class: 'meta', text: 'Loading plugin panels…' }));

  const result = $('#result');
  result.hidden = false;
  result.replaceChildren(
    h('div', { class: 'card' },
      h('div', { class: 'result-head' },
        h('div', null,
          h('h3', { id: 'result-heading', tabindex: '-1', text: `Palette for ${p.saved_name || img.filename}` }),
          h('p', { class: 'meta', text: meta.join(' · ') })),
        actions),
      h('div', { class: 'grid-2', style: { marginTop: '1rem' } }, previewFigure(p), swatchList(p))),
    h('div', { class: 'card' },
      h('h3', { text: 'Composition' }),
      barTable({
        caption: 'Share of the image covered by each dominant colour',
        headers: ['Colour', 'Share'],
        max: Math.max(...p.colors.map((c) => c.percent)),
        rows: p.colors.map((c) => ({
          label: [dot(c.hex), h('span', { class: 'mono', text: c.hex }), ` ${c.name}`],
          value: c.percent, display: fmtPct(c.percent), color: c.hex,
        })),
      })),
    kSection(p), cvdSection(p), contrastSection(p),
    h('h3', { text: 'Plugin panels' }), pluginArea);
  loadPanels(pluginArea, `/panels?slot=palette.result&palette_id=${p.id}&${userParam()}`);
}

/* ---------------------------------------------------------------- plugin panels */
const STATUS = { info: ['ℹ', 'Note'], ok: ['✓', 'OK'], warn: ['⚠', 'Warning'], fail: ['✗', 'Problem'] };

function renderBlock(block) {
  switch (block.type) {
    case 'text': return h('p', { text: block.text });
    case 'status': {
      const [icon, word] = STATUS[block.level] || STATUS.info;
      return h('p', { class: 'status-line ' + block.level }, h('span', { class: 'icon', text: `${icon} ${word}:` }), block.text);
    }
    case 'stat': return h('div', { class: 'stat' }, h('div', { class: 'label', text: block.label }), h('div', { class: 'value', text: block.value }));
    case 'swatches': return h('div', null,
      block.label ? h('p', { class: 'meta', text: block.label }) : null,
      h('ul', { class: 'swatch-strip' }, block.colors.map((c) => h('li', null, swatchButton({ hex: c.hex.toUpperCase() }, c.label || '')))));
    case 'table': return h('div', { class: 'table-wrap' }, h('table', null,
      h('caption', { text: block.caption }),
      h('thead', null, h('tr', null, block.columns.map((c) => h('th', { scope: 'col', text: c })))),
      h('tbody', null, block.rows.map((row) => h('tr', null, row.map((v) => h('td', { text: v })))))));
    case 'bars': return barTable({
      caption: block.caption, headers: ['Item', 'Value'],
      rows: block.items.map((it) => ({ label: it.label, value: it.value, display: `${it.value}${block.unit || ''}`, color: it.hex || null })),
    });
    default: return null;
  }
}

async function loadPanels(area, path) {
  try {
    const results = await api(path);
    if (!results.length) {
      area.replaceChildren(h('p', { class: 'meta', text: 'No plugins contribute here yet. See the Developers tab to add one.' }));
      return;
    }
    area.replaceChildren(...results.map((r) => h('section', { class: 'card plugin-panel' },
      h('h4', null, r.ok ? r.panel.title : r.plugin, h('span', { class: 'by', text: `plugin: ${r.plugin} (${r.kind})` })),
      r.ok ? r.panel.blocks.map(renderBlock)
        : h('p', { class: 'status-line fail' }, h('span', { class: 'icon', text: '✗ Unavailable:' }), r.error))));
  } catch (e) {
    area.replaceChildren(h('p', { class: 'notice error', text: e.message }));
  }
}

/* ---------------------------------------------------------------- library & ranking */
async function loadLibrary() {
  const body = $('#library-body');
  const scope = $('#scope-select').value;
  let data;
  try {
    data = await api(`/ranking?scope=${scope}&${userParam()}`);
  } catch (e) {
    body.replaceChildren(h('p', { class: 'notice error', text: e.message }));
    return;
  }
  const m = data.model;
  const personalised = m.status === 'personalised';
  const kids = [];

  kids.push(h('div', { class: 'card' },
    h('h3', { text: `Preference model for ${data.user}` }),
    h('div', { class: 'stats' },
      h('div', { class: 'stat' }, h('div', { class: 'label', text: 'Palettes liked' }), h('div', { class: 'value', text: String(m.liked) })),
      h('div', { class: 'stat' }, h('div', { class: 'label', text: 'Palettes disliked' }), h('div', { class: 'value', text: String(m.disliked) })),
      h('div', { class: 'stat' }, h('div', { class: 'label', text: 'Ranking' }), h('div', { class: 'value', text: personalised ? 'Personalised' : 'Not yet personalised' }))),
    h('p', { text: personalised
      ? 'Palettes are ordered by how similar they are to the ones you liked, minus how similar they are to the ones you disliked.'
      : 'Like or dislike a few palettes and this list will reorder to match your taste. Until then it is newest first.' }),
    personalised && m.liked && m.disliked ? barTable({
      caption: 'What your feedback emphasises: features that best separate your likes from your dislikes get more weight (average weight is 1)',
      headers: ['Palette feature', 'Weight'],
      rows: Object.entries(m.feature_weights).sort((x, y) => y[1] - x[1])
        .map(([key, w]) => ({ label: m.feature_labels[key], value: w, display: w.toFixed(2) })),
    }) : null));

  if (!data.items.length) {
    kids.push(h('p', { class: 'notice info', text: scope === 'saved'
      ? 'Your library is empty. Extract a palette and choose "Save to library".'
      : 'No palettes have been extracted yet.' }));
    body.replaceChildren(...kids);
    return;
  }

  if (personalised) {
    kids.push(h('div', { class: 'card' },
      h('h3', { text: 'Preference scores' }),
      barTable({
        caption: 'Preference score per palette, from −1 (like your dislikes) to +1 (like your likes)',
        headers: ['Palette', 'Score'], max: 1, diverging: true,
        rows: data.items.map((it) => ({
          label: `${it.rank}. ${it.palette.saved_name || it.palette.image.filename}`,
          value: it.score, display: fmtScore(it.score),
        })),
      })));
  }

  kids.push(h('h3', { text: personalised ? 'Ranked palettes' : 'Palettes (newest first)' }));
  kids.push(h('ol', { class: 'rank-list' }, data.items.map((it) => {
    const p = it.palette;
    const actions = h('div');
    const draw = (pal) => actions.replaceChildren(
      actionBar(pal, () => loadLibrary()),
      h('div', { class: 'btn-row', style: { marginTop: '.5rem' } },
        h('button', { type: 'button', class: 'btn small', onclick: () => openPalette(p.id) }, 'Open details')));
    draw(p);
    return h('li', { class: 'card rank-item' },
      h('div', { class: 'rank-no', 'aria-label': `Rank ${it.rank}` }, String(it.rank)),
      h('div', null,
        h('div', { class: 'rank-title' },
          h('h4', { text: p.saved_name || p.image.filename }),
          personalised ? h('span', { class: 'tag', text: `score ${fmtScore(it.score)}` }) : null,
          p.feedback ? h('span', { class: 'tag', text: p.feedback === 'like' ? 'you liked this' : 'you disliked this' }) : null,
          !p.saved ? h('span', { class: 'tag', text: 'not saved' }) : null),
        h('div', { class: 'strip', 'aria-hidden': 'true' }, p.colors.map((c) => h('span', { style: { background: c.hex, flex: `${c.share} 1 0` } }))),
        h('p', { class: 'hexes', text: p.colors.map((c) => `${c.hex} ${c.name} ${fmtPct(c.percent)}`).join(' · ') }),
        h('p', { class: 'meta', text: it.reasons.join(' ') }),
        actions));
  })));

  const pluginArea = h('div', { 'aria-live': 'polite' });
  kids.push(pluginArea);
  body.replaceChildren(...kids);
  api(`/panels?slot=library&${userParam()}`).then((results) => {
    if (results.length) { pluginArea.before(h('h3', { text: 'Plugin panels' })); loadPanels(pluginArea, `/panels?slot=library&${userParam()}`); }
  }).catch(() => {});
}

/* ---------------------------------------------------------------- colour inputs */
const HEX_RE = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i;
function normHex(value) {
  const m = HEX_RE.exec(value.trim());
  if (!m) return null;
  const d = m[1].length === 3 ? [...m[1]].map((c) => c + c).join('') : m[1];
  return '#' + d.toUpperCase();
}
function setColor(prefix, hex) {
  $(`#${prefix}-hex`).value = hex;
  $(`#${prefix}-hex`).removeAttribute('aria-invalid');
  $(`#${prefix}-pick`).value = hex.toLowerCase();
}
function bindColor(prefix, onChange) {
  const text = $(`#${prefix}-hex`);
  const pick = $(`#${prefix}-pick`);
  text.addEventListener('input', () => {
    const hex = normHex(text.value);
    text.setAttribute('aria-invalid', String(!hex));
    if (hex) { pick.value = hex.toLowerCase(); onChange(); }
  });
  text.addEventListener('blur', () => { const hex = normHex(text.value); if (hex) text.value = hex; });
  pick.addEventListener('input', () => { text.value = pick.value.toUpperCase(); text.removeAttribute('aria-invalid'); onChange(); });
}

/* ---------------------------------------------------------------- contrast checker */
let contrastSeq = 0;
async function runContrast() {
  const fg = normHex($('#fg-hex').value);
  const bg = normHex($('#bg-hex').value);
  const errorBox = $('#contrast-error');
  if (!fg || !bg) { showError(errorBox, 'Enter both colours as HEX values such as #1C5CAB or #FFF.'); return; }
  showError(errorBox, '');
  const seq = ++contrastSeq;
  try {
    const r = await api('/contrast', { method: 'POST', json: { foreground: fg, background: bg } });
    if (seq !== contrastSeq) return;            // a newer request has already been sent
    const passed = r.checks.filter((c) => c.passed).length;
    $('#contrast-result').replaceChildren(
      h('div', { class: 'contrast-preview', style: { color: r.foreground, background: r.background } },
        h('p', { class: 'large', text: 'Large text sample (24 px bold)' }),
        h('p', { class: 'normal', text: 'Normal text sample: the quick brown fox jumps over the lazy dog.' })),
      h('p', { class: 'ratio' }, r.ratio.toFixed(2) + ':1', h('small', { text: `contrast ratio · ${passed} of ${r.checks.length} checks pass` })),
      h('div', { class: 'table-wrap' }, h('table', null,
        h('caption', { text: `WCAG 2 results for ${r.foreground} text on ${r.background}` }),
        h('thead', null, h('tr', null,
          h('th', { scope: 'col', text: 'Requirement' }), h('th', { scope: 'col', class: 'num', text: 'Needs at least' }), h('th', { scope: 'col', text: 'Result' }))),
        h('tbody', null, r.checks.map((c) => h('tr', null,
          h('th', { scope: 'row', text: c.label }), h('td', { class: 'num', text: c.threshold + ':1' }), h('td', null, verdict(c.passed))))))));
  } catch (e) {
    showError(errorBox, e.message);
  }
}
function initContrast() {
  let timer;
  const later = () => { clearTimeout(timer); timer = setTimeout(runContrast, 150); };
  bindColor('fg', later);
  bindColor('bg', later);
  $('#contrast-form').addEventListener('submit', (ev) => { ev.preventDefault(); runContrast(); });
  $('#swap-btn').addEventListener('click', () => {
    const fg = normHex($('#fg-hex').value);
    const bg = normHex($('#bg-hex').value);
    if (fg && bg) { setColor('fg', bg); setColor('bg', fg); runContrast(); announce('Colours swapped.'); }
  });
}

/* ---------------------------------------------------------------- harmony */
async function runHarmony() {
  const base = normHex($('#base-hex').value);
  const errorBox = $('#harmony-error');
  if (!base) { showError(errorBox, 'Enter the base colour as a HEX value such as #2A78D6.'); return; }
  showError(errorBox, '');
  try {
    const r = await api('/harmony', { method: 'POST', json: { color: base } });
    $('#harmony-result').replaceChildren(...r.schemes.map((s) => h('section', { class: 'card' },
      h('h3', { text: s.name }),
      h('p', { class: 'meta', text: s.description }),
      h('ul', { class: 'swatch-strip', 'aria-label': `${s.name} colours` }, s.colors.map((c) => h('li', null, swatchButton(c)))),
      h('div', { class: 'btn-row' }, s.colors.length > 1 ? h('button', { type: 'button', class: 'btn small',
        onclick: () => { setColor('fg', s.colors[0].hex); setColor('bg', s.colors[1].hex); selectTab('contrast', true); } },
      `Check contrast of ${s.colors[0].hex} on ${s.colors[1].hex}`) : null))));
    announce(`Harmonies for ${r.base.hex} (${r.base.name}) ready.`);
  } catch (e) {
    showError(errorBox, e.message);
  }
}
function initHarmony() {
  bindColor('base', () => {});
  $('#base-pick').addEventListener('change', runHarmony);
  $('#harmony-form').addEventListener('submit', (ev) => { ev.preventDefault(); runHarmony(); });
}

/* ---------------------------------------------------------------- developers */
const SLOT_LABELS = { 'palette.result': 'Under each palette', library: 'Library tab' };
async function loadPlugins() {
  const box = $('#plugins-list');
  try {
    const plugins = await api('/plugins');
    if (!plugins.length) { box.replaceChildren(h('p', { text: 'No plugins installed.' })); return; }
    box.replaceChildren(h('div', { class: 'table-wrap' }, h('table', { 'aria-labelledby': 'plugins-heading' },
      h('thead', null, h('tr', null, ['Name', 'Type', 'Shown', 'Endpoint', 'Status', ''].map((t) =>
        h('th', { scope: 'col' }, t || h('span', { class: 'visually-hidden', text: 'Actions' }))))),
      h('tbody', null, plugins.map((p) => h('tr', null,
        h('th', { scope: 'row' }, p.name, p.description ? h('div', { class: 'meta', text: p.description }) : null),
        h('td', { text: p.kind === 'local' ? 'Local (in-app)' : 'Webhook' }),
        h('td', { text: p.slots.map((s) => SLOT_LABELS[s] || s).join(', ') || '—' }),
        h('td', { class: 'mono', text: p.hook_url || p.routes_prefix || '—' }),
        h('td', null, p.error ? h('span', { class: 'verdict fail', text: '✗ Failed to load: ' + p.error }) : h('span', { class: 'verdict pass', text: '✓ Active' })),
        h('td', null, p.kind === 'webhook' ? h('button', { type: 'button', class: 'btn small', 'aria-label': `Unregister ${p.name}`,
          onclick: async () => {
            try { await api(`/plugins/${p.id}`, { method: 'DELETE' }); announce(`${p.name} unregistered.`); loadPlugins(); } catch (e) { announce(e.message); }
          } }, 'Unregister') : null)))))));
  } catch (e) {
    box.replaceChildren(h('p', { class: 'notice error', text: e.message }));
  }
}
function initDevelopers() {
  const form = $('#plugin-form');
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const slots = [...form.querySelectorAll('input[name="slot"]:checked')].map((i) => i.value);
    const errorBox = $('#plugin-error');
    if (!slots.length) { showError(errorBox, 'Choose at least one place to show the panel.'); return; }
    try {
      await api('/plugins', { method: 'POST', json: {
        name: $('#plugin-name').value.trim(), hook_url: $('#plugin-url').value.trim(), description: $('#plugin-desc').value.trim(), slots } });
      showError(errorBox, '');
      form.reset();
      announce('Plugin registered.');
      loadPlugins();
    } catch (e) {
      showError(errorBox, e.message);
    }
  });
}

/* ---------------------------------------------------------------- start */
initTheme();
initProfile();
initTabs();
initUpload();
initContrast();
initHarmony();
initDevelopers();
loadSamples();
$('#scope-select').addEventListener('change', loadLibrary);
