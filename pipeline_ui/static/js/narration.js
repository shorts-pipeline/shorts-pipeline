// narration.js — narration JSON editor modal.

let __narrationJsonEditCtx = null;

function narrationJsonEditorNodes() {
  return {
    bd: document.getElementById('narration-json-backdrop'),
    ta: document.getElementById('narration-json-editor-ta'),
    st: document.getElementById('narration-json-status'),
  };
}

function setNarrationJsonEditorStatus(text, cls) {
  const { st } = narrationJsonEditorNodes();
  if (!st) return;
  if (!text) {
    st.style.display = 'none';
    st.className = 'status';
    st.textContent = '';
    return;
  }
  st.style.display = 'block';
  st.className = cls ? ('status ' + cls) : 'status';
  st.textContent = text;
}

async function openNarrationJsonEditor() {
  const jd = getPickerJournalDate();
  if (!jd) {
    window.alert('Choose a journal date in the picker above.');
    return;
  }
  const { bd, ta } = narrationJsonEditorNodes();
  if (!bd || !ta) return;
  __narrationJsonEditCtx = { jd: jd };
  setNarrationJsonEditorStatus('Loading…');
  bd.classList.add('open');
  bd.setAttribute('aria-hidden', 'false');
  try {
    const r = await fetch('/api/preview/narration?journal_date=' + encodeURIComponent(jd), {
      cache: 'no-store',
    });
    const txt = await r.text();
    if (!r.ok) {
      setNarrationJsonEditorStatus(txt || ('HTTP ' + r.status), 'err');
      return;
    }
    ta.value = txt;
    setNarrationJsonEditorStatus('');
  } catch (e) {
    setNarrationJsonEditorStatus(String(e), 'err');
  }
  ta.focus();
}

function closeNarrationJsonEditor() {
  const { bd, ta } = narrationJsonEditorNodes();
  __narrationJsonEditCtx = null;
  if (bd) {
    bd.classList.remove('open');
    bd.setAttribute('aria-hidden', 'true');
  }
  if (ta) ta.value = '';
  setNarrationJsonEditorStatus('');
}

function validateNarrationJsonEditor() {
  const { ta } = narrationJsonEditorNodes();
  if (!ta) return false;
  try {
    const parsed = JSON.parse(ta.value || '');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      setNarrationJsonEditorStatus('Top-level JSON must be an object.', 'err');
      return false;
    }
    if (!Array.isArray(parsed.narration_script)) {
      setNarrationJsonEditorStatus('Expected narration_script to be a list.', 'err');
      return false;
    }
    setNarrationJsonEditorStatus('Valid JSON.');
    return true;
  } catch (e) {
    setNarrationJsonEditorStatus(String(e), 'err');
    return false;
  }
}

async function saveNarrationJsonEditor() {
  const ctx = __narrationJsonEditCtx;
  const { ta } = narrationJsonEditorNodes();
  if (!ctx || !ta) return;
  const jd = ctx.jd;
  if (!validateNarrationJsonEditor()) return;
  setNarrationJsonEditorStatus('Saving…');
  try {
    const r = await fetch('/api/narration/update-json', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ journal_date: jd, json_text: ta.value }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) {
      setNarrationJsonEditorStatus(data.message || data.error || ('HTTP ' + r.status), 'err');
      return;
    }
    closeNarrationJsonEditor();
    await refreshNarrationInline(jd).catch(() => {});
  } catch (e) {
    setNarrationJsonEditorStatus(String(e), 'err');
  }
}

(function initNarrationJsonEditor() {
  const bd = document.getElementById('narration-json-backdrop');
  const dlg = document.getElementById('narration-json-dialog');
  const closeBtn = document.getElementById('narration-json-close');
  const validateBtn = document.getElementById('narration-json-validate');
  const saveBtn = document.getElementById('narration-json-save');
  if (bd) {
    bd.addEventListener('click', function (e) {
      if (e.target === bd) closeNarrationJsonEditor();
    });
  }
  if (dlg) {
    dlg.addEventListener('click', function (e) {
      e.stopPropagation();
    });
  }
  if (closeBtn) closeBtn.addEventListener('click', () => { closeNarrationJsonEditor(); });
  if (validateBtn) validateBtn.addEventListener('click', () => { validateNarrationJsonEditor(); });
  if (saveBtn) saveBtn.addEventListener('click', () => { saveNarrationJsonEditor(); });
})();
