// core.js — shared utilities, workflow primitives, latest links, run-status polling.

function el(tag, attrs, children) {
  const e = document.createElement(tag);
  if (attrs) Object.entries(attrs).forEach(([k, v]) => {
    if (k === 'class') e.className = v;
    else if (k === 'html') e.innerHTML = v;
    else e.setAttribute(k, v);
  });
  (children || []).forEach(c => e.appendChild(c));
  return e;
}

const PIPELINE_WORKFLOW_STORAGE = 'pipeline_ui_workflow';
const PIPELINE_WORKFLOWS = ['journal', 'narration', 'video', 'full'];
const B_ROLL_SUGGEST_TOP = 5;

function normalizedWorkflow(wf) {
  const s = (wf == null ? '' : String(wf)).trim().toLowerCase();
  return PIPELINE_WORKFLOWS.includes(s) ? s : 'journal';
}

function workflowForApiRun(wf) {
  const w = normalizedWorkflow(wf);
  if (w === 'journal') return 'narration';
  /* Narration tab: review JSON but pipeline run is video (TTS → assembly), not --narration-only */
  if (w === 'narration') return 'video';
  return w;
}

function getPickerJournalDate() {
  const p = document.getElementById('journalDatePicker');
  return (p && p.value) ? p.value : '';
}

/** Safari/WebKit often reports fetch failures as "Load failed". */
function formatPipelineFetchError(e) {
  let msg = (e && e.message) ? String(e.message) : String(e);
  if (
    (e instanceof TypeError && (/failed to fetch/i.test(msg) || /load failed/i.test(msg))) ||
    (e instanceof DOMException && e.name === 'NetworkError')
  ) {
    return (
      'Network error (browser lost contact with the Pipeline UI server). ' +
      'The run may still be going — check the terminal running pipeline_ui/server.py and logs/pipeline_ui_last.json. ' +
      'If you use --reload, saving files restarts the server and can drop in-flight requests; wait for the run to finish or restart the server without reload. ' +
      'Confirm the same http://HOST:PORT you opened in the browser.'
    );
  }
  return msg;
}

function mergeJournalDateIntoValues(values, wf) {
  const w = normalizedWorkflow(wf);
  let d = '';
  if (w === 'full') {
    const tf = document.getElementById('date');
    if (tf && tf.value.trim()) d = tf.value.trim();
  }
  if (!d) d = getPickerJournalDate();
  if (!d) return { ...values };
  return { ...values, date: d };
}

function syncLongConversationDialogueDependency() {
  const journalLong = document.getElementById('journalLongConversationMode');
  const journalDialogue = document.getElementById('journalDialogueMode');
  const formLong = document.getElementById('long_conversation_mode');
  const formDialogue = document.getElementById('dialogue_mode');

  const applyPair = (longNode, dialogueNode) => {
    if (!longNode || !dialogueNode) return;
    const longOn = !!longNode.checked;
    if (longOn) dialogueNode.checked = true;
    dialogueNode.disabled = longOn;
    dialogueNode.title = longOn ? 'Long conversation implies dialogue mode.' : '';
  };

  applyPair(journalLong, journalDialogue);
  applyPair(formLong, formDialogue);
}

function syncJournalWeekArcControls() {
  const useArc = document.getElementById('journalUseWeekArc');
  const refresh = document.getElementById('journalRefreshWeekArc');
  const wrap = document.getElementById('journalRefreshWeekArcWrap');
  if (!useArc) return;
  const on = !!useArc.checked;
  if (refresh) {
    refresh.disabled = !on;
    if (!on) refresh.checked = false;
  }
  if (wrap) wrap.style.opacity = on ? '1' : '0.55';
}

function updatePipelineActionLabels(wf) {
  const btn = document.getElementById('btn-run');
  if (!btn) return;
  const w = normalizedWorkflow(wf != null ? wf : window.__workflow || 'journal');
  btn.textContent = w === 'narration' ? 'Generate video' : 'Run';
}

function showWorkflowSections(wf) {
  const w = normalizedWorkflow(wf);
  const j = document.getElementById('wf-section-journal');
  const n = document.getElementById('wf-section-narration');
  const v = document.getElementById('wf-section-video');
  const fWrap = document.getElementById('wf-section-form-wrap');
  const formTools = document.getElementById('pipeline-form-tools');
  const act = document.getElementById('pipeline-actions-row');
  const pipe = document.getElementById('panel-pipeline');
  if (j) j.classList.toggle('hidden', w !== 'journal');
  if (n) n.classList.toggle('hidden', w !== 'narration');
  if (v) v.classList.toggle('hidden', w !== 'video');
  if (w === 'video' && typeof refreshYoutubeUploadStatus === 'function') {
    refreshYoutubeUploadStatus().catch(() => {});
  }
  if (w === 'narration' && typeof initProduceSubtabForWorkflow === 'function') {
    initProduceSubtabForWorkflow();
  }
  if (fWrap) fWrap.classList.toggle('hidden', w === 'journal');
  if (formTools) formTools.classList.toggle('hidden', w === 'journal');
  if (act) act.classList.remove('hidden');
  if (pipe) pipe.classList.toggle('panel-pipeline--wide', w === 'narration');
  updatePipelineActionLabels(w);
}

function fieldAllowedForWorkflow(field, wf) {
  const wfs = field.workflows;
  if (!wfs || !Array.isArray(wfs) || wfs.length === 0) return true;
  return wfs.includes(wf);
}

function fieldsForWorkflow(spec, wf) {
  const w = normalizedWorkflow(wf);
  if (w === 'journal') return [];
  return (spec.fields || []).filter(f => fieldAllowedForWorkflow(f, w));
}

function effectiveFieldDefault(field, wf) {
  const w = normalizedWorkflow(wf);
  if (
    field.type === 'checkbox' &&
    field.default_workflow &&
    typeof field.default_workflow === 'object' &&
    Object.prototype.hasOwnProperty.call(field.default_workflow, w)
  ) {
    return !!field.default_workflow[w];
  }
  return field.default;
}

function snapshotCurrentFormFieldValues(spec) {
  const snap = {};
  (spec.fields || []).forEach(f => {
    const id = f.id;
    const type = f.type || 'text';
    if (type === 'checkbox') {
      const node = document.getElementById(id);
      if (node) snap[id] = node.checked;
      return;
    }
    if (type === 'radio') {
      const sel = document.querySelector(`input[name="${id}"]:checked`);
      if (sel) snap[id] = sel.value;
      return;
    }
    const node = document.getElementById(id);
    if (node) snap[id] = node.value;
  });
  return snap;
}

function applySnapshotToForm(snap) {
  if (!snap || typeof snap !== 'object') return;
  Object.keys(snap).forEach(id => {
    const node = document.getElementById(id);
    if (!node) return;
    const val = snap[id];
    if (node.type === 'checkbox') {
      node.checked = !!val;
      return;
    }
    if (node.type === 'radio') {
      const radios = document.querySelectorAll(`input[name="${id}"]`);
      radios.forEach(r => {
        r.checked = r.value === String(val);
      });
      return;
    }
    node.value = val != null ? String(val) : '';
  });
}

function updateWorkflowDescription(spec, wf) {
  const p = document.getElementById('workflow-desc');
  if (!p) return;
  const w = normalizedWorkflow(wf);
  const map = (spec && spec.workflow_descriptions) || {};
  const line = map[w] || '';
  p.textContent = line;
}

function syncWorkflowTabButtons(wf) {
  const w = normalizedWorkflow(wf);
  document.querySelectorAll('.workflow-tab').forEach(btn => {
    const on = btn.dataset.workflow === w;
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-selected', on ? 'true' : 'false');
  });
}

function switchPipelineWorkflow(nextWf) {
  const spec = window.__spec;
  if (!spec) return;
  const snap = snapshotCurrentFormFieldValues(spec);
  window.__workflow = normalizedWorkflow(nextWf);
  try {
    localStorage.setItem(PIPELINE_WORKFLOW_STORAGE, window.__workflow);
  } catch (_) { /* ignore */ }
  syncWorkflowTabButtons(window.__workflow);
  updateWorkflowDescription(spec, window.__workflow);
  showWorkflowSections(window.__workflow);
  buildForm(spec, window.__workflow);
  applySnapshotToForm(snap);
  syncPickerFromDateField();
  const jd = getPickerJournalDate();
  if (jd) {
    onPipelineJournalDateChanged(jd).catch(() => {});
  }
}

function wireWorkflowTabs() {
  document.querySelectorAll('.workflow-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      const wf = btn.dataset.workflow;
      if (wf) switchPipelineWorkflow(wf);
    });
  });
}

function applySuggestedJournalDate(data, opts) {
  opts = opts || {};
  if (!data || !data.suggested_journal_date) return;
  const ds = data.suggested_journal_date;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(ds)) return;
  const picker = document.getElementById('journalDatePicker');
  const dateField = document.getElementById('date');
  if (picker) picker.value = ds;
  if (dateField && (!dateField.value.trim() || opts.forceDateField)) dateField.value = ds;
  syncUnlJournalLink();
  onPipelineJournalDateChanged(ds).catch(() => {});
}

/** Fetches `/api/latest-links` for `suggested_journal_date` (picker hint). No UI list. */
async function loadLatestLinks() {
  try {
    const r = await fetch('/api/latest-links', { cache: 'no-store' });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  } catch (e) {
    return null;
  }
}

let runInFlight = false;

function updateResetDayButtonFromStatus(statusPayload) {
  const btn = document.getElementById('btn-reset-day');
  const statusEl = document.getElementById('reset-day-status');
  if (!btn) return;
  const supported = !!(statusPayload && statusPayload.features && statusPayload.features.reset_day);
  window.__pipelineResetDaySupported = supported;
  if (supported) {
    btn.disabled = false;
    btn.title = 'Move narration, TTS, clips, and final output for this date into archive/reset_*';
    if (statusEl && statusEl.dataset.staleServer) {
      statusEl.style.display = 'none';
      statusEl.textContent = '';
      delete statusEl.dataset.staleServer;
    }
    return;
  }
  btn.disabled = true;
  btn.title =
    'Reset day requires a restarted Pipeline UI server (POST /api/reset-day missing). Stop the server and run: python pipeline_ui/server.py --reload';
  if (statusEl) {
    statusEl.style.display = 'block';
    statusEl.dataset.staleServer = '1';
    statusEl.classList.add('err');
    statusEl.textContent =
      'Reset day is unavailable until you restart pipeline_ui/server.py from this repo (the running server is an older build).';
  }
}

async function syncRunStatus() {
  const btnStop = document.getElementById('btn-stop');
  const btnRun = document.getElementById('btn-run');
  const btnDry = document.getElementById('btn-dry');
  try {
    const r = await fetch('/api/status', { cache: 'no-store' });
    const d = await r.json().catch(() => ({}));
    updateResetDayButtonFromStatus(d);
    if (runInFlight) {
      if (btnStop) btnStop.disabled = false;
      if (btnRun) btnRun.disabled = true;
      if (btnDry) btnDry.disabled = true;
      return;
    }
    if (d.running) {
      if (btnStop) btnStop.disabled = false;
      if (btnRun) btnRun.disabled = true;
      if (btnDry) btnDry.disabled = true;
    } else {
      if (btnStop) btnStop.disabled = true;
      if (btnRun) btnRun.disabled = false;
      if (btnDry) btnDry.disabled = false;
    }
  } catch (_) {
    if (!runInFlight) {
      if (btnStop) btnStop.disabled = true;
      if (btnRun) btnRun.disabled = false;
      if (btnDry) btnDry.disabled = false;
    }
  }
}

const RUN_STATUS_POLL_MS = 5000;
let __runStatusPollId = null;
function startRunStatusPollingIfVisible() {
  if (document.hidden || __runStatusPollId !== null) return;
  __runStatusPollId = setInterval(syncRunStatus, RUN_STATUS_POLL_MS);
}
function stopRunStatusPolling() {
  if (__runStatusPollId !== null) {
    clearInterval(__runStatusPollId);
    __runStatusPollId = null;
  }
}
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    stopRunStatusPolling();
  } else {
    syncRunStatus();
    startRunStatusPollingIfVisible();
  }
});

load();
