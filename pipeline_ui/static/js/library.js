// library.js — Library tab: app-tab switching, library subtabs, portraits, prompt packs, diversity.

const APP_TAB_STORAGE = 'pipeline_ui_app_tab';
const LIBRARY_TAB_STORAGE = 'pipeline_ui_library_tab';
const APP_TABS = ['pipeline', 'library'];
const LIBRARY_TABS = ['portraits', 'prompt-packs', 'diversity', 'week-arcs'];
let __libraryLoading = false;

function normalizedLibraryTab(name) {
  const s = (name == null ? '' : String(name)).trim().toLowerCase();
  return LIBRARY_TABS.includes(s) ? s : 'portraits';
}

function switchLibraryTab(name, opts) {
  const tab = normalizedLibraryTab(name);
  const force = opts && opts.force;
  document.querySelectorAll('.library-tab').forEach(btn => {
    const on = btn.dataset.libraryTab === tab;
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  LIBRARY_TABS.forEach(id => {
    const panel = document.getElementById('library-panel-' + id);
    if (panel) panel.classList.toggle('hidden', id !== tab);
  });
  try { localStorage.setItem(LIBRARY_TAB_STORAGE, tab); } catch (_) { /* ignore */ }
  if (tab !== 'portraits') stopPortraitVoice();
  loadLibrarySubtab(tab, !!force).catch(err => console.error('library subtab load failed', err));
}

function wireLibraryTabs() {
  document.querySelectorAll('.library-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      const t = btn.dataset.libraryTab;
      if (t) switchLibraryTab(t, { force: false });
    });
  });
}

async function loadLibrarySubtab(tab, forceReload) {
  if (tab === 'portraits') return loadLibraryPortraits(forceReload);
  if (tab === 'prompt-packs') return loadLibraryPromptPacks(forceReload);
  if (tab === 'diversity') return loadLibraryDiversity(forceReload);
  if (tab === 'week-arcs') return loadLibraryWeekArcs(forceReload);
}

function normalizedAppTab(name) {
  const s = (name == null ? '' : String(name)).trim().toLowerCase();
  return APP_TABS.includes(s) ? s : 'pipeline';
}

function switchAppTab(name) {
  const tab = normalizedAppTab(name);
  document.querySelectorAll('.app-tab').forEach(btn => {
    const on = btn.dataset.appTab === tab;
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  const pPipe = document.getElementById('panel-pipeline');
  const pLib = document.getElementById('panel-library');
  if (pPipe) pPipe.classList.toggle('hidden', tab !== 'pipeline');
  if (pLib) pLib.classList.toggle('hidden', tab !== 'library');
  if (tab !== 'library') stopPortraitVoice();
  try { localStorage.setItem(APP_TAB_STORAGE, tab); } catch (_) { /* ignore */ }
  if (tab === 'library' && !__libraryLoading) {
    let libTab = 'portraits';
    try {
      libTab = normalizedLibraryTab(localStorage.getItem(LIBRARY_TAB_STORAGE));
    } catch (_) { /* ignore */ }
    switchLibraryTab(libTab, { force: false });
  }
}

function wireAppTabs() {
  document.querySelectorAll('.app-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      const t = btn.dataset.appTab;
      if (t) switchAppTab(t);
    });
  });
}

function escapeHtmlText(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

async function fetchJsonStrict(url) {
  let r;
  try {
    r = await fetch(url, { cache: 'no-store', credentials: 'same-origin' });
  } catch (netErr) {
    const e = new Error('Network error: ' + (netErr && netErr.message ? netErr.message : netErr));
    e.code = 'network';
    throw e;
  }
  let raw = '';
  try { raw = await r.text(); } catch (_) { raw = ''; }
  const ctype = (r.headers && r.headers.get && r.headers.get('Content-Type')) || '';
  let parsed = null;
  let parseErr = null;
  try {
    parsed = raw ? JSON.parse(raw) : null;
  } catch (e) {
    parseErr = e;
  }
  if (!r.ok) {
    const excerpt = raw ? (' body: ' + raw.slice(0, 200)) : '';
    const e = new Error('HTTP ' + r.status + ' ' + (r.statusText || '') + ' from ' + url + excerpt);
    e.code = 'http';
    e.status = r.status;
    throw e;
  }
  if (parseErr) {
    const excerpt = raw ? (' body[0..200]: ' + raw.slice(0, 200)) : ' empty body';
    const e = new Error('Bad JSON from ' + url + ' (content-type=' + (ctype || 'n/a') + ').' + excerpt);
    e.code = 'parse';
    e.cause = parseErr;
    throw e;
  }
  return parsed;
}

function formatBytes(n) {
  if (!n || n < 1024) return (n || 0) + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / (1024 * 1024)).toFixed(2) + ' MB';
}

let __portraitImageObserver = null;
let __portraitScrollObserver = null;
let __portraitCount = 0;
let __portraitVoiceAudio = null;
let __portraitVoiceBtn = null;

function stopPortraitVoice() {
  if (__portraitVoiceAudio) {
    try { __portraitVoiceAudio.pause(); } catch (_) { /* ignore */ }
    try { __portraitVoiceAudio.currentTime = 0; } catch (_) { /* ignore */ }
  }
  if (__portraitVoiceBtn) {
    __portraitVoiceBtn.classList.remove('is-playing');
    const lbl = __portraitVoiceBtn.querySelector('.portrait-voice-btn-label');
    if (lbl) lbl.textContent = __portraitVoiceBtn.dataset.idleLabel || '▶ Play voice';
    __portraitVoiceBtn = null;
  }
}

function togglePortraitVoice(button, filename) {
  if (!filename) return;
  const url = '/api/library/voice-preview/' + encodeURIComponent(filename);
  const labelEl = button.querySelector('.portrait-voice-btn-label');
  if (__portraitVoiceBtn === button && __portraitVoiceAudio && !__portraitVoiceAudio.paused) {
    stopPortraitVoice();
    return;
  }
  stopPortraitVoice();
  if (!__portraitVoiceAudio) {
    __portraitVoiceAudio = new Audio();
    __portraitVoiceAudio.preload = 'none';
    __portraitVoiceAudio.addEventListener('ended', () => stopPortraitVoice());
    __portraitVoiceAudio.addEventListener('error', () => {
      if (__portraitVoiceBtn) {
        const l = __portraitVoiceBtn.querySelector('.portrait-voice-btn-label');
        if (l) l.textContent = '⚠ Play failed';
        __portraitVoiceBtn.classList.add('is-error');
        __portraitVoiceBtn.classList.remove('is-playing');
      }
    });
  }
  __portraitVoiceAudio.src = url;
  __portraitVoiceBtn = button;
  button.classList.remove('is-error');
  button.classList.add('is-playing');
  if (labelEl) labelEl.textContent = '■ Stop voice';
  __portraitVoiceAudio.play().catch(err => {
    console.error('voice preview play failed', err);
    if (__portraitVoiceBtn === button) {
      button.classList.remove('is-playing');
      button.classList.add('is-error');
      if (labelEl) labelEl.textContent = '⚠ Play failed';
      __portraitVoiceBtn = null;
    }
  });
}

function setPortraitCounter(idx) {
  const c = document.getElementById('library-portraits-counter');
  if (!c) return;
  if (__portraitCount <= 0) {
    c.textContent = '';
    return;
  }
  const n = Math.min(Math.max(idx + 1, 1), __portraitCount);
  c.textContent = n + ' / ' + __portraitCount;
}

function buildPortraitSlide(row) {
  const slide = el('div', { class: 'portrait-slide' });
  slide.dataset.portraitId = row.id;
  slide.dataset.src = '/api/library/portrait/' + encodeURIComponent(row.filename);
  const wrap = el('div', { class: 'portrait-img-wrap' });
  wrap.appendChild(el('span', { class: 'portrait-placeholder', html: 'Loading image…' }));
  const img = document.createElement('img');
  img.alt = row.label || row.filename || row.id;
  img.decoding = 'async';
  img.style.visibility = 'hidden';
  img.addEventListener('load', () => {
    const ph = wrap.querySelector('.portrait-placeholder');
    if (ph) ph.remove();
    img.style.visibility = 'visible';
  });
  img.addEventListener('error', () => {
    const ph = wrap.querySelector('.portrait-placeholder');
    if (ph) { ph.textContent = 'Failed to load image'; ph.classList.add('err'); }
  });
  img.addEventListener('click', () => {
    if (img.src) openBrollThumbLightbox(img.src);
  });
  wrap.appendChild(img);
  slide.appendChild(wrap);

  const labelLine = el('div', { class: 'portrait-label' });
  const kindCls = row.kind === 'pair' ? 'kind-pair' : 'kind-person';
  const kindText = row.kind === 'pair' ? 'Pair' : 'Person';
  labelLine.appendChild(el('span', { class: 'portrait-kind-badge ' + kindCls, html: escapeHtmlText(kindText) }));
  labelLine.appendChild(document.createTextNode(row.label || row.id));
  slide.appendChild(labelLine);

  const idMeta = row.id + ' · ' + row.filename + ' · ' + formatBytes(row.size_bytes);
  slide.appendChild(el('div', { class: 'portrait-id', html: escapeHtmlText(idMeta) }));

  if (Array.isArray(row.roles) && row.roles.length) {
    slide.appendChild(el('div', { class: 'portrait-roles', html: escapeHtmlText(row.roles.join(', ')) }));
  } else if (Array.isArray(row.member_ids) && row.member_ids.length) {
    slide.appendChild(el('div', { class: 'portrait-roles', html: escapeHtmlText('Members: ' + row.member_ids.join(' + ')) }));
  }
  if (row.description) {
    slide.appendChild(el('div', { class: 'portrait-desc', html: escapeHtmlText(row.description) }));
  }
  if (row.voice_preview) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'portrait-voice-btn';
    const labelText = '▶ Play voice';
    btn.dataset.idleLabel = labelText;
    btn.setAttribute('aria-label', 'Play voice preview for ' + (row.label || row.id));
    const lbl = el('span', { class: 'portrait-voice-btn-label', html: escapeHtmlText(labelText) });
    btn.appendChild(lbl);
    btn.addEventListener('click', () => togglePortraitVoice(btn, row.voice_preview));
    slide.appendChild(btn);
  }
  return slide;
}

function loadPortraitSlideImage(slide) {
  const src = slide.dataset.src;
  if (!src) return;
  const img = slide.querySelector('img');
  if (!img || img.src) return;
  img.src = src;
  delete slide.dataset.src;
}

function setupPortraitImageObserver(track) {
  if (__portraitImageObserver) {
    __portraitImageObserver.disconnect();
    __portraitImageObserver = null;
  }
  const slides = Array.from(track.querySelectorAll('.portrait-slide[data-src]'));
  if (!('IntersectionObserver' in window)) {
    slides.forEach(loadPortraitSlideImage);
    return;
  }
  __portraitImageObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      loadPortraitSlideImage(entry.target);
      __portraitImageObserver.unobserve(entry.target);
    });
  }, { root: track, rootMargin: '0px 600px 0px 600px', threshold: 0.01 });
  slides.forEach(s => __portraitImageObserver.observe(s));
}

function setupPortraitScrollObserver(track) {
  if (__portraitScrollObserver) {
    __portraitScrollObserver.disconnect();
    __portraitScrollObserver = null;
  }
  if (!('IntersectionObserver' in window)) {
    setPortraitCounter(0);
    return;
  }
  __portraitScrollObserver = new IntersectionObserver((entries) => {
    let best = null;
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      if (best == null || entry.intersectionRatio > best.intersectionRatio) best = entry;
    });
    if (best) {
      const slides = Array.from(track.children);
      const idx = slides.indexOf(best.target);
      if (idx >= 0) setPortraitCounter(idx);
    }
  }, { root: track, threshold: [0.5, 0.75, 1.0] });
  Array.from(track.children).forEach(slide => __portraitScrollObserver.observe(slide));
}

function scrollPortraitBy(delta) {
  const track = document.getElementById('library-portraits-track');
  if (!track) return;
  const first = track.querySelector('.portrait-slide');
  const slideWidth = first ? (first.getBoundingClientRect().width + 14) : track.clientWidth * 0.6;
  track.scrollBy({ left: delta * slideWidth, behavior: 'smooth' });
}

function wirePortraitCarousel() {
  const track = document.getElementById('library-portraits-track');
  const prev = document.getElementById('library-portraits-prev');
  const next = document.getElementById('library-portraits-next');
  if (prev) prev.addEventListener('click', () => scrollPortraitBy(-1));
  if (next) next.addEventListener('click', () => scrollPortraitBy(1));
  if (track) {
    track.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowRight') { e.preventDefault(); scrollPortraitBy(1); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); scrollPortraitBy(-1); }
      else if (e.key === 'Home') { e.preventDefault(); track.scrollTo({ left: 0, behavior: 'smooth' }); }
      else if (e.key === 'End') { e.preventDefault(); track.scrollTo({ left: track.scrollWidth, behavior: 'smooth' }); }
    });
  }
}

async function loadLibraryPortraits(forceReload) {
  const status = document.getElementById('library-portraits-status');
  const track = document.getElementById('library-portraits-track');
  if (!status || !track) return;
  if (!forceReload && status.dataset.loadedOk === '1') return;
  status.classList.remove('err');
  status.dataset.loadedOk = '0';
  status.textContent = 'Loading…';
  track.innerHTML = '';
  __portraitCount = 0;
  setPortraitCounter(0);
  try {
    const data = await fetchJsonStrict('/api/library/pipeline-portraits');
    if (!data || data.ok !== true) {
      const detail = data && (data.error || data.message) ? (data.error || data.message) : 'unknown server error';
      throw new Error('Server returned ok=false: ' + detail);
    }
    const rows = Array.isArray(data.portraits) ? data.portraits : [];
    if (rows.length === 0) {
      status.textContent = 'No pipeline portraits resolved from config/narration_characters.json.';
      status.dataset.loadedOk = '1';
      return;
    }
    const pairs = rows.filter(function (rr) { return rr && rr.kind === 'pair'; }).length;
    const people = rows.length - pairs;
    const missing = (data.missing_people || []).concat(data.missing_composites || []);
    let summary = rows.length + ' portraits (' + pairs + ' pair, ' + people + ' individual)';
    if (missing.length) summary += ' · missing: ' + missing.join(', ');
    status.textContent = summary;
    __portraitCount = rows.length;
    rows.forEach(function (row) { track.appendChild(buildPortraitSlide(row)); });
    setupPortraitImageObserver(track);
    setupPortraitScrollObserver(track);
    setPortraitCounter(0);
    status.dataset.loadedOk = '1';
  } catch (e) {
    status.classList.add('err');
    const msg = (e && e.message) ? e.message : String(e);
    status.textContent = 'Failed to load pipeline portraits: ' + msg;
    console.error('loadLibraryPortraits failed', e);
  }
}

function renderPackJsonBlock(packJson, err) {
  if (err) {
    const wrap = el('div', { class: 'pack-file' });
    wrap.appendChild(el('div', { class: 'pack-file-name', html: 'pack.json (parse error)' }));
    wrap.appendChild(el('div', { class: 'pack-json-err', html: escapeHtmlText(err) }));
    return wrap;
  }
  if (packJson == null) return null;
  const wrap = el('div', { class: 'pack-file' });
  wrap.appendChild(el('div', { class: 'pack-file-name', html: 'pack.json' }));
  const pre = document.createElement('pre');
  pre.textContent = JSON.stringify(packJson, null, 2);
  wrap.appendChild(pre);
  return wrap;
}

function renderPackFileBlock(file) {
  const wrap = el('div', { class: 'pack-file' });
  const label = file.name + (file.kind !== 'text' ? '  (' + formatBytes(file.size_bytes) + ', binary skipped)' : '');
  wrap.appendChild(el('div', { class: 'pack-file-name', html: escapeHtmlText(label) }));
  if (file.kind === 'text') {
    const pre = document.createElement('pre');
    pre.textContent = file.text || '';
    wrap.appendChild(pre);
  }
  return wrap;
}

async function loadLibraryPromptPacks(forceReload) {
  const status = document.getElementById('library-packs-status');
  const list = document.getElementById('library-packs-list');
  if (!status || !list) return;
  if (!forceReload && status.dataset.loadedOk === '1') return;
  status.classList.remove('err');
  status.dataset.loadedOk = '0';
  status.textContent = 'Loading…';
  list.innerHTML = '';
  try {
    const data = await fetchJsonStrict('/api/library/prompt-packs');
    if (!data || data.ok !== true) {
      const detail = data && (data.error || data.message) ? (data.error || data.message) : 'unknown server error';
      throw new Error('Server returned ok=false: ' + detail);
    }
    const packs = Array.isArray(data.packs) ? data.packs : [];
    if (packs.length === 0) {
      status.textContent = 'No prompt packs found in prompt_packs/.';
      status.dataset.loadedOk = '1';
      return;
    }
    status.textContent = packs.length + ' director' + (packs.length === 1 ? 'y' : 'ies') + ' in prompt_packs/';
    status.dataset.loadedOk = '1';
    packs.forEach(pack => {
      const details = document.createElement('details');
      details.className = 'pack-card';
      const summary = document.createElement('summary');
      const idSpan = el('span', { html: escapeHtmlText(pack.id) });
      summary.appendChild(idSpan);
      const pj = pack.pack_json;
      if (pj && typeof pj === 'object') {
        if (pj.version) {
          summary.appendChild(el('span', { class: 'pack-version', html: 'v' + escapeHtmlText(pj.version) }));
        }
        if (pj.description) {
          summary.appendChild(el('span', { class: 'pack-desc', html: '· ' + escapeHtmlText(pj.description) }));
        }
      } else if (pack.pack_json_error) {
        summary.appendChild(el('span', { class: 'pack-version', html: '(pack.json error)' }));
      } else {
        summary.appendChild(el('span', { class: 'pack-version', html: '(no pack.json)' }));
      }
      summary.appendChild(el('span', { class: 'pack-version', html: '· ' + escapeHtmlText(pack.path) }));
      details.appendChild(summary);
      const body = el('div', { class: 'pack-body' });
      const pjBlock = renderPackJsonBlock(pack.pack_json, pack.pack_json_error);
      if (pjBlock) body.appendChild(pjBlock);
      (pack.files || []).forEach(file => body.appendChild(renderPackFileBlock(file)));
      details.appendChild(body);
      list.appendChild(details);
    });
  } catch (e) {
    status.classList.add('err');
    const msg = (e && e.message) ? e.message : String(e);
    status.textContent = 'Failed to load prompt packs: ' + msg;
    console.error('loadLibraryPromptPacks failed', e);
  }
}

async function loadLibraryAll() {
  if (__libraryLoading) return;
  __libraryLoading = true;
  try {
    let libTab = 'portraits';
    try {
      libTab = normalizedLibraryTab(localStorage.getItem(LIBRARY_TAB_STORAGE));
    } catch (_) { /* ignore */ }
    await loadLibrarySubtab(libTab, true);
  } finally {
    __libraryLoading = false;
  }
}

function renderDiversityRuleCard(rule) {
  const card = el('div', { class: 'diversity-rule-card' + (rule.fired ? ' is-fired' : '') });
  const head = el('div', { class: 'diversity-rule-head' });
  head.appendChild(el('span', { class: 'diversity-rule-label', html: escapeHtmlText(rule.label || rule.id) }));
  head.appendChild(el('span', {
    class: 'diversity-badge ' + (rule.fired ? 'badge-fired' : 'badge-idle'),
    html: rule.fired ? 'active' : 'idle',
  }));
  head.appendChild(el('span', { class: 'diversity-category', html: escapeHtmlText(rule.category || '') }));
  card.appendChild(head);
  if (rule.detail) card.appendChild(el('div', { class: 'diversity-rule-detail', html: escapeHtmlText(rule.detail) }));
  if (rule.trigger) card.appendChild(el('div', { class: 'diversity-rule-trigger', html: escapeHtmlText(rule.trigger) }));
  if (rule.hint_when_fired) {
    card.appendChild(el('div', { class: 'diversity-rule-hint', html: escapeHtmlText(rule.hint_when_fired) }));
  }
  if (Array.isArray(rule.patterns) && rule.patterns.length) {
    const pre = document.createElement('pre');
    pre.className = 'diversity-patterns-pre';
    pre.textContent = rule.patterns.join('\n');
    card.appendChild(pre);
  }
  return card;
}

function renderDiversityPatternCard(row) {
  const card = el('div', { class: 'diversity-pattern-card' + (row.active ? ' is-active' : '') });
  const head = el('div', { class: 'diversity-rule-head' });
  head.appendChild(el('span', { class: 'diversity-rule-label', html: escapeHtmlText(row.id || 'pattern') }));
  head.appendChild(el('span', {
    class: 'diversity-badge ' + (row.active ? 'badge-fired' : row.suppressed_by_static ? 'badge-idle' : 'badge-idle'),
    html: row.active ? 'hint active' : row.suppressed_by_static ? 'suppressed (static covers)' : 'gated off',
  }));
  if (row.category) head.appendChild(el('span', { class: 'diversity-category', html: escapeHtmlText(row.category) }));
  card.appendChild(head);
  if (row.prevalence) card.appendChild(el('div', { class: 'diversity-rule-detail', html: 'Prevalence: ' + escapeHtmlText(row.prevalence) }));
  if (row.suggested_hint) card.appendChild(el('div', { class: 'diversity-rule-hint', html: escapeHtmlText(row.suggested_hint) }));
  if (Array.isArray(row.evidence) && row.evidence.length) {
    const ul = el('ul', { class: 'diversity-evidence-list' });
    row.evidence.forEach(ev => ul.appendChild(el('li', { html: escapeHtmlText(ev) })));
    card.appendChild(ul);
  }
  const checker = row.checker;
  const patternId = String(row.id || '').trim();
  const chk = el('div', { class: 'diversity-checker-block' });
  if (checker && typeof checker === 'object') {
    chk.appendChild(el('div', { class: 'diversity-subhead', html: 'Checker (' + escapeHtmlText(checker.status || 'proposed') + ')' }));
    chk.appendChild(el('div', { class: 'diversity-rule-detail', html: escapeHtmlText(
      (checker.check_type || '') + (checker.skew_threshold != null ? ' · threshold ' + checker.skew_threshold : '')
    ) }));
    if (Array.isArray(checker.patterns) && checker.patterns.length) {
      const pre = document.createElement('pre');
      pre.className = 'diversity-patterns-pre';
      pre.textContent = checker.patterns.join('\n');
      chk.appendChild(pre);
    }
  } else {
    chk.appendChild(el('div', { class: 'diversity-subhead', html: 'Checker' }));
    chk.appendChild(el('p', { class: 'help', html: 'No checker in cache for this pattern yet.' }));
  }
  if (patternId) {
    const actions = el('div', { class: 'diversity-checker-actions' });
    const regenBtn = document.createElement('button');
    regenBtn.type = 'button';
    regenBtn.className = 'diversity-checker-regen';
    regenBtn.textContent = checker ? 'Regenerate checker' : 'Generate checker';
    regenBtn.title = 'One OpenAI call to rebuild this pattern\'s recommended_checker in the audit cache';
    regenBtn.addEventListener('click', () => regenerateDiversityChecker(patternId, regenBtn));
    actions.appendChild(regenBtn);
    chk.appendChild(actions);
  }
  card.appendChild(chk);
  return card;
}

async function refreshEpisodeDiversityAudit(btn) {
  if (!btn) return;
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Refreshing audit…';
  try {
    const r = await fetch('/api/library/diversity/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ prompt_pack: 'lewis_clark', last: 15 }),
    });
    let data = null;
    try { data = await r.json(); } catch (_) { data = null; }
    if (!r.ok || !data || data.ok !== true) {
      const msg = (data && (data.message || data.error)) || ('HTTP ' + r.status);
      throw new Error(msg);
    }
    const n = data.episode_count != null ? data.episode_count : '?';
    const through = data.through_date_id || '?';
    const added = (data.hints_added || []).length;
    const removed = (data.hints_removed || []).length;
    alert(
      'Diversity audit refreshed.\n' +
      n + ' episode(s), through ' + through +
      (added || removed ? ('\nHints: +' + added + ' / −' + removed) : '')
    );
    if (typeof loadLibraryDiversity === 'function') {
      await loadLibraryDiversity(true);
    }
    const picker = document.getElementById('journalDatePicker');
    if (typeof loadPhase1PromptPreview === 'function' && picker && picker.value) {
      try { await loadPhase1PromptPreview(picker.value); } catch (_) { /* optional */ }
    }
  } catch (e) {
    alert('Failed to refresh diversity audit: ' + ((e && e.message) ? e.message : String(e)));
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

function appendDiversityAuditRefreshControls(parent) {
  if (!parent) return;
  const actions = el('div', { class: 'diversity-audit-refresh-actions' });
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'diversity-audit-refresh';
  btn.textContent = 'Refresh diversity audit';
  btn.title = 'One OpenAI call over the last 15 narrations; updates state/episode_diversity_lewis_clark.json';
  btn.addEventListener('click', () => refreshEpisodeDiversityAudit(btn));
  actions.appendChild(btn);
  parent.appendChild(actions);
}

async function regenerateDiversityChecker(patternId, btn) {
  if (!patternId || !btn) return;
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Regenerating…';
  try {
    const r = await fetch('/api/library/diversity/regenerate-checker', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ pattern_id: patternId }),
    });
    let data = null;
    try { data = await r.json(); } catch (_) { data = null; }
    if (!r.ok || !data || data.ok !== true) {
      const msg = (data && (data.message || data.error)) || ('HTTP ' + r.status);
      throw new Error(msg);
    }
    await loadLibraryDiversity(true);
  } catch (e) {
    alert('Failed to regenerate checker: ' + ((e && e.message) ? e.message : String(e)));
    btn.disabled = false;
    btn.textContent = orig;
  }
}

function fillHintList(ul, hints) {
  if (!ul) return;
  while (ul.firstChild) ul.removeChild(ul.firstChild);
  (hints || []).forEach(h => {
    const li = document.createElement('li');
    li.textContent = h;
    ul.appendChild(li);
  });
}

async function loadLibraryDiversity(forceReload) {
  const status = document.getElementById('library-diversity-status');
  const root = document.getElementById('library-diversity-content');
  if (!status || !root) return;
  if (!forceReload && status.dataset.loadedOk === '1') return;
  status.classList.remove('err');
  status.dataset.loadedOk = '0';
  status.textContent = 'Loading…';
  root.innerHTML = '';
  try {
    const data = await fetchJsonStrict('/api/library/diversity');
    if (!data || data.ok !== true) {
      throw new Error((data && (data.error || data.message)) || 'unknown server error');
    }

    const llm = data.llm_audit || {};
    const parts = [];
    if (data.newest_merged_date_id) parts.push('Newest narration: ' + data.newest_merged_date_id);
    if (data.cache_path) parts.push('Cache: ' + data.cache_path);
    if (llm.enabled) {
      if (llm.cache_present) {
        if (llm.generated_at) parts.push('Generated ' + llm.generated_at);
        if (llm.through_date_id) parts.push('through ' + llm.through_date_id);
        parts.push(llm.stale ? 'stale' : 'fresh');
      } else parts.push('no cache file yet');
    }
    status.textContent = parts.length ? parts.join(' · ') : 'Episode diversity (Lewis & Clark packs)';

    const overview = el('div', { class: 'diversity-block' });
    overview.appendChild(el('div', { class: 'diversity-subhead', html: 'Overview' }));
    if (data.preview_note) overview.appendChild(el('p', { class: 'help', html: escapeHtmlText(data.preview_note) }));
    appendDiversityAuditRefreshControls(overview);
    const refresh = el('p', { class: 'help diversity-refresh-cmd', html: escapeHtmlText(
      'CLI equivalent: python scripts/refresh_episode_diversity_audit.py --last 15 --prompt-pack lewis_clark'
    ) });
    overview.appendChild(refresh);
    if (data.episode_diversity) {
      const pre = document.createElement('pre');
      pre.className = 'diversity-config-pre';
      pre.textContent = JSON.stringify(data.episode_diversity, null, 2);
      overview.appendChild(pre);
    }
    root.appendChild(overview);

    const live = el('div', { class: 'diversity-block' });
    live.appendChild(el('div', { class: 'diversity-subhead', html: 'Live preview (next narration)' }));
    if (!(data.recent_episodes || []).length) {
      live.appendChild(el('p', { class: 'help', html: 'No prior merged narrations on disk yet.' }));
    } else {
      const recentUl = el('ul', { class: 'diversity-list' });
      data.recent_episodes.forEach(line => recentUl.appendChild(el('li', { html: escapeHtmlText(line) })));
      live.appendChild(el('div', { class: 'diversity-mini-label', html: 'Recent episodes' }));
      live.appendChild(recentUl);
      live.appendChild(el('div', { class: 'diversity-mini-label', html: 'Static hints active (' + (data.static_hints_active || []).length + ')' }));
      const staticUl = el('ul', { class: 'diversity-list diversity-hints-list' });
      fillHintList(staticUl, data.static_hints_active);
      live.appendChild(staticUl);
      live.appendChild(el('div', { class: 'diversity-mini-label', html: 'Dynamic hints active (' + (data.dynamic_hints_active || []).length + ')' }));
      const dynUl = el('ul', { class: 'diversity-list diversity-hints-list dynamic' });
      fillHintList(dynUl, data.dynamic_hints_active);
      live.appendChild(dynUl);
      live.appendChild(el('div', { class: 'diversity-mini-label', html: 'Merged for Phase 1 (' + (data.merged_hints_active || []).length + ', cap 6)' }));
      const mergedUl = el('ul', { class: 'diversity-list diversity-hints-list merged' });
      fillHintList(mergedUl, data.merged_hints_active);
      live.appendChild(mergedUl);
    }
    root.appendChild(live);

    const staticBlock = el('div', { class: 'diversity-block' });
    staticBlock.appendChild(el('div', { class: 'diversity-subhead', html: 'Static checks (deterministic)' }));
    (data.static_rules || []).forEach(rule => staticBlock.appendChild(renderDiversityRuleCard(rule)));
    root.appendChild(staticBlock);

    const dynBlock = el('div', { class: 'diversity-block' });
    dynBlock.appendChild(el('div', { class: 'diversity-subhead', html: 'Dynamic checks (LLM audit cache)' }));
    if (llm.notes) dynBlock.appendChild(el('p', { class: 'help', html: escapeHtmlText(llm.notes) }));
    const patterns = data.dynamic_patterns || [];
    if (!patterns.length) {
      dynBlock.appendChild(el('p', { class: 'help', html: 'No patterns in cache. Run the refresh script above.' }));
    } else {
      patterns.forEach(row => dynBlock.appendChild(renderDiversityPatternCard(row)));
    }
    root.appendChild(dynBlock);

    if (Array.isArray(data.lc_pack_configs) && data.lc_pack_configs.length > 1) {
      const packsBlock = el('div', { class: 'diversity-block' });
      packsBlock.appendChild(el('div', { class: 'diversity-subhead', html: 'LC pack configs' }));
      const pre = document.createElement('pre');
      pre.className = 'diversity-config-pre';
      pre.textContent = JSON.stringify(data.lc_pack_configs, null, 2);
      packsBlock.appendChild(pre);
      root.appendChild(packsBlock);
    }

    status.dataset.loadedOk = '1';
  } catch (e) {
    status.classList.add('err');
    status.textContent = 'Failed to load diversity checks: ' + ((e && e.message) ? e.message : String(e));
    console.error('loadLibraryDiversity failed', e);
  }
}

function renderWeekArcDayRow(dateId, day) {
  const d = day || {};
  const row = el('div', { class: 'pack-file' });
  const mode = d.recommended_mode || 'narration';
  const role = d.role || '—';
  const head = el('div', { class: 'pack-file-name' });
  head.appendChild(document.createTextNode(dateId + '  ·  role: ' + role + '  ·  mode: ' + mode));
  row.appendChild(head);
  if (Array.isArray(d.speakers) && d.speakers.length) {
    row.appendChild(el('div', { class: 'help', html: 'Speakers: ' + escapeHtmlText(d.speakers.join(', ')) }));
  }
  if (d.day_focus) row.appendChild(el('div', { class: 'help', html: escapeHtmlText(d.day_focus) }));
  if (d.reason) row.appendChild(el('div', { class: 'help', html: escapeHtmlText(d.reason) }));
  return row;
}

function renderWeekArcCard(arc) {
  const a = arc || {};
  const details = document.createElement('details');
  details.className = 'pack-card';
  const summary = document.createElement('summary');
  const start = a.week_start_date_id || '?';
  const end = a.week_end_date_id || '?';
  const dateIds = Array.isArray(a.journal_date_ids) ? a.journal_date_ids : [];
  const dayCount = dateIds.length;
  summary.appendChild(el('span', { html: escapeHtmlText('week_' + start) }));
  summary.appendChild(el('span', {
    class: 'pack-version',
    html: escapeHtmlText(start + '–' + end + ' · ' + dayCount + ' day' + (dayCount === 1 ? '' : 's')),
  }));
  if (a.model) summary.appendChild(el('span', { class: 'pack-version', html: escapeHtmlText('model: ' + a.model) }));
  if (a.through_line) summary.appendChild(el('span', { class: 'pack-desc', html: '· ' + escapeHtmlText(a.through_line) }));
  details.appendChild(summary);

  const body = el('div', { class: 'pack-body' });
  const avoid = Array.isArray(a.avoid_this_week) ? a.avoid_this_week.filter(Boolean) : [];
  if (avoid.length) {
    const avoidBlock = el('div', { class: 'pack-file' });
    avoidBlock.appendChild(el('div', { class: 'pack-file-name', html: 'Avoid this arc' }));
    const ul = el('ul', { class: 'diversity-list' });
    avoid.forEach(function (line) { ul.appendChild(el('li', { html: escapeHtmlText(line) })); });
    avoidBlock.appendChild(ul);
    body.appendChild(avoidBlock);
  }
  const days = (a.days && typeof a.days === 'object') ? a.days : {};
  const orderedDateIds = dateIds.length ? dateIds : Object.keys(days).sort();
  orderedDateIds.forEach(function (dateId) {
    body.appendChild(renderWeekArcDayRow(dateId, days[dateId]));
  });
  details.appendChild(body);
  return details;
}

async function loadLibraryWeekArcs(forceReload) {
  const status = document.getElementById('library-week-arcs-status');
  const list = document.getElementById('library-week-arcs-list');
  if (!status || !list) return;
  if (!forceReload && status.dataset.loadedOk === '1') return;
  status.classList.remove('err');
  status.dataset.loadedOk = '0';
  status.textContent = 'Loading…';
  list.innerHTML = '';
  try {
    const data = await fetchJsonStrict('/api/library/week-arcs');
    if (!data || data.ok !== true) {
      throw new Error((data && (data.error || data.message)) || 'unknown server error');
    }
    const cfg = data.config || {};
    const boundsEl = document.getElementById('library-week-arcs-bounds');
    if (boundsEl && cfg.week_journal_days_min != null && cfg.week_journal_days_max != null) {
      boundsEl.textContent = cfg.week_journal_days_min + '–' + cfg.week_journal_days_max + ' days, from config/week_arc.json';
    }
    const arcs = Array.isArray(data.arcs) ? data.arcs : [];
    if (arcs.length === 0) {
      status.textContent = 'No saved week arcs in state/week_arcs/ yet.';
      status.dataset.loadedOk = '1';
      return;
    }
    status.textContent = arcs.length + ' saved arc' + (arcs.length === 1 ? '' : 's') + ' in state/week_arcs/';
    arcs.forEach(function (arc) { list.appendChild(renderWeekArcCard(arc)); });
    status.dataset.loadedOk = '1';
  } catch (e) {
    status.classList.add('err');
    status.textContent = 'Failed to load week arcs: ' + ((e && e.message) ? e.message : String(e));
    console.error('loadLibraryWeekArcs failed', e);
  }
}
