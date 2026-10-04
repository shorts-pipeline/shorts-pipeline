// app.js — load() orchestrator and pipeline run lifecycle (run/cancel/exec/run-report + DOM wiring).

async function load() {
  const errEl = document.getElementById('load-err');
  errEl.style.display = 'none';
  const links = await loadLatestLinks();
  if (links) applySuggestedJournalDate(links, {});
  try {
    const r = await fetch('/api/options', { cache: 'no-store' });
    const spec = await r.json();
    if (!r.ok) throw new Error(spec.error || r.statusText);
    window.__spec = spec;
    let initialWf = 'journal';
    try {
      initialWf = normalizedWorkflow(localStorage.getItem(PIPELINE_WORKFLOW_STORAGE));
    } catch (_) { /* ignore */ }
    window.__workflow = initialWf;
    document.getElementById('title').textContent = spec.title || 'Pipeline UI';
    document.getElementById('description').textContent = spec.description || '';
    syncWorkflowTabButtons(window.__workflow);
    updateWorkflowDescription(spec, window.__workflow);
    showWorkflowSections(window.__workflow);
    buildForm(spec, window.__workflow);
    syncLongConversationDialogueDependency();
    syncPickerFromDateField();
    wireJournalPicker();
    wireWorkflowTabs();
    wireAppTabs();
    wireLibraryTabs();
    wirePortraitCarousel();
    let initialAppTab = 'pipeline';
    try {
      initialAppTab = normalizedAppTab(localStorage.getItem(APP_TAB_STORAGE));
    } catch (_) { /* ignore */ }
    switchAppTab(initialAppTab);
    syncRunStatus();
    initUiVersion().catch(() => {});
    const jdAfter = getPickerJournalDate();
    if (jdAfter && normalizedWorkflow(window.__workflow) === 'narration') {
      onPipelineJournalDateChanged(jdAfter).catch(() => {});
    }
  } catch (e) {
    errEl.textContent = 'Failed to load /api/options: ' + e;
    errEl.style.display = 'block';
  }
}

async function cancelPipeline() {
  const out = document.getElementById('out');
  try {
    const r = await fetch('/api/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}'
    });
    const data = await r.json().catch(() => ({}));
    if (data.ok) {
      const prev = out.textContent || '';
      out.textContent = prev + (prev.endsWith('\n') ? '' : '\n') + '[Stop requested — terminating pipeline…]\n';
    } else if (data.error === 'no_run') {
      const prev = out.textContent || '';
      out.textContent = prev + (prev.endsWith('\n') ? '' : '\n') + '[Stop: no pipeline run is active.]\n';
    } else if (data.error === 'already_finished') {
      const prev = out.textContent || '';
      out.textContent = prev + (prev.endsWith('\n') ? '' : '\n') + '[Stop: run already finished.]\n';
    } else {
      const prev = out.textContent || '';
      out.textContent = prev + (prev.endsWith('\n') ? '' : '\n') + '[Stop: ' + (data.error || 'unknown') + ']\n';
    }
  } catch (e) {
    const prev = out.textContent || '';
    out.textContent = prev + (prev.endsWith('\n') ? '' : '\n') + '[Stop request failed: ' + e + ']\n';
  }
}

async function _executePipelineRequest(values, workflowApi, hooks, execOpts) {
  hooks = hooks || {};
  execOpts = execOpts || {};
  const btnRun = document.getElementById('btn-run');
  const btnDry = document.getElementById('btn-dry');
  const btnStop = document.getElementById('btn-stop');
  runInFlight = true;
  if (btnRun) btnRun.disabled = true;
  if (btnDry) btnDry.disabled = true;
  if (btnStop) btnStop.disabled = false;
  const out = document.getElementById('out');
  out.textContent = 'Running…';
  try {
    const r = await fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values, workflow: workflowApi })
    });
    const data = await r.json().catch(() => ({}));
    const lines = [];
    if (r.status === 409 && data.error) {
      lines.push('--- error ---\n' + data.error);
      out.textContent = lines.join('\n');
      return;
    }
    if (execOpts.preambleText) {
      lines.push(String(execOpts.preambleText).trimEnd());
      lines.push('--- pipeline ---');
    }
    if (data.command) lines.push('$ ' + JSON.stringify(data.command));
    if (data.cwd) lines.push('cwd: ' + data.cwd);
    if (data.cancelled) lines.push('--- stopped ---\nPipeline was stopped by user (Stop).\n');
    if (data.stdout) lines.push('--- stdout ---\n' + data.stdout);
    if (data.stderr) lines.push('--- stderr ---\n' + data.stderr);
    if (data.error) lines.push('--- error ---\n' + data.error);
    lines.push('return code: ' + (data.returncode != null ? data.returncode : '?'));
    out.textContent = lines.join('\n');
    if (data.returncode === 0) {
      const ld = await loadLatestLinks();
      if (ld) applySuggestedJournalDate(ld, { forceDateField: true });
      if (hooks.afterSuccess) await hooks.afterSuccess();
    }
  } catch (e) {
    out.textContent = 'Request failed: ' + formatPipelineFetchError(e);
  } finally {
    runInFlight = false;
    if (btnStop) btnStop.disabled = true;
    if (btnRun) btnRun.disabled = false;
    if (btnDry) btnDry.disabled = false;
    syncRunStatus();
  }
}

async function runPipeline(opts) {
  opts = opts || {};
  const spec = window.__spec;
  if (!spec) return;
  const wfUi = window.__workflow || 'journal';
  let values = collectValues(spec, wfUi);
  values = mergeJournalDateIntoValues(values, wfUi);

  let hooks = {};
  if (normalizedWorkflow(wfUi) === 'journal') {
    const jd = getPickerJournalDate();
    if (!jd) {
      window.alert('Choose a journal date first.');
      return;
    }
    const regenNar = document.getElementById('journalRegenerateNarration');
    values.skip_existing = !(regenNar && regenNar.checked);
    values.no_focus_topic = !!(document.getElementById('journalNoFocusTopic') && document.getElementById('journalNoFocusTopic').checked);
    values.dialogue_mode = !!(document.getElementById('journalDialogueMode') && document.getElementById('journalDialogueMode').checked);
    values.long_conversation_mode = !!(
      document.getElementById('journalLongConversationMode') && document.getElementById('journalLongConversationMode').checked
    );
    values.use_week_arc = !!(document.getElementById('journalUseWeekArc') && document.getElementById('journalUseWeekArc').checked);
    values.refresh_week_arc = !!(
      document.getElementById('journalRefreshWeekArc') && document.getElementById('journalRefreshWeekArc').checked
    );
    values.dry_run = !!opts.forceDryRun;
    hooks = {
      afterSuccess: async () => {
        const picker = document.getElementById('journalDatePicker');
        if (picker && jd) picker.value = jd;
        const dateField = document.getElementById('date');
        if (dateField && jd) dateField.value = jd;
        syncUnlJournalLink();
        await onPipelineJournalDateChanged(jd).catch(() => {});
        switchPipelineWorkflow('narration');
        await refreshNarrationInline(jd);
      }
    };
  } else if (opts.forceDryRun) {
    values.dry_run = true;
  }

  if (normalizedWorkflow(wfUi) === 'narration' && !values.dry_run) {
    const jdVid = getPickerJournalDate();
    hooks = {
      afterSuccess: async () => {
        const picker = document.getElementById('journalDatePicker');
        if (picker && jdVid) picker.value = jdVid;
        const dateField = document.getElementById('date');
        if (dateField && jdVid) dateField.value = jdVid;
        syncUnlJournalLink();
        switchPipelineWorkflow('video');
        if (jdVid) await onPipelineJournalDateChanged(jdVid).catch(() => {});
      }
    };
  }

  let preambleText = '';
  let brollMaterializedForRun = false;
  const wfNorm = normalizedWorkflow(wfUi);
  // Copy chosen library clips into movie-images/ before run-daily whenever the pipeline will
  // build video (API workflow video or full). Previously only the Narration tab ran this; the
  // Video tab sends workflow "video" too but wfNorm is "video", so B-roll choices were ignored.
  const apiWorkflow = workflowForApiRun(wfUi);
  if ((apiWorkflow === 'video' || apiWorkflow === 'full') && !opts.forceDryRun) {
    const br = collectBrollMaterializePayload();
    if (br && br.blocked) {
      window.alert(
        'Journal date does not match the loaded B-roll suggestions. Reload B-roll for this date or align the journal picker.'
      );
      return;
    }
    if (br && br.error) return;
    if (br && br.assignments && br.assignments.length) {
      const out = document.getElementById('out');
      out.textContent = 'Materializing B-roll…';
      try {
        const r = await fetch('/api/b-roll/materialize', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ date_id: br.date_id, assignments: br.assignments })
        });
        const md = await r.json().catch(() => ({}));
        if (!md.ok || (md.returncode !== undefined && md.returncode !== null && md.returncode !== 0)) {
          out.textContent = formatApiSubprocessOutput(md);
          return;
        }
        preambleText = formatApiSubprocessOutput(md) + '\n\n';
        brollMaterializedForRun = true;
      } catch (e) {
        out.textContent = 'B-roll materialize request failed: ' + e;
        return;
      }
    }
  }
  // With skip-existing, run-daily skips both clip gen and assembly when outputs already match
  // segment counts—so a B-roll file swap would not remux. Force one assembly after materialize.
  if (brollMaterializedForRun) {
    values.force_assembly = true;
  }

  await _executePipelineRequest(values, workflowForApiRun(wfUi), hooks, { preambleText: preambleText });
}

document.addEventListener('change', (ev) => {
  const t = ev.target;
  if (!t || !t.id) return;
  if (t.id === 'dialogue_mode' || t.id === 'long_conversation_mode') {
    const journalDlg = document.getElementById('journalDialogueMode');
    const journalLong = document.getElementById('journalLongConversationMode');
    if (t.id === 'dialogue_mode' && journalDlg) journalDlg.checked = t.checked;
    if (t.id === 'long_conversation_mode' && journalLong) journalLong.checked = t.checked;
    syncLongConversationDialogueDependency();
    const jd = getPickerJournalDate();
    if (jd && normalizedWorkflow(window.__workflow) === 'narration') {
      if (typeof loadProduceSegments === 'function') loadProduceSegments(jd).catch(() => {});
      if (typeof loadProducePhase1DialoguePreview === 'function') {
        loadProducePhase1DialoguePreview(jd).catch(() => {});
      }
    }
  }
});

document.getElementById('btn-run').addEventListener('click', () => { runPipeline(); });
document.getElementById('btn-dry').addEventListener('click', () => {
  setDryRun(true);
  runPipeline({ forceDryRun: true });
});
document.getElementById('btn-stop').addEventListener('click', () => { cancelPipeline(); });
const _btnEditNarrationJson = document.getElementById('btn-edit-narration-json');
if (_btnEditNarrationJson) _btnEditNarrationJson.addEventListener('click', () => { openNarrationJsonEditor(); });

document.getElementById('modal-close').addEventListener('click', () => { closeModal(); });
const _btnBrollAdd = document.getElementById('btn-broll-add');
if (_btnBrollAdd) _btnBrollAdd.addEventListener('click', () => { onBrollAddFromPreview(false); });
const _btnInlineBrollAdd = document.getElementById('btn-inline-broll-add');
if (_btnInlineBrollAdd) _btnInlineBrollAdd.addEventListener('click', () => { onBrollAddFromPreview(true); });
async function loadRunReport() {
  const pre = document.getElementById('run-report-pre');
  if (!pre) return;
  const picker = document.getElementById('journalDatePicker');
  const jd = picker && picker.value ? picker.value : '';
  if (!jd) {
    pre.textContent = 'Set journal date first.';
    return;
  }
  pre.textContent = 'Loading…';
  try {
    const r = await fetch('/api/run-report?journal_date=' + encodeURIComponent(jd), { cache: 'no-store' });
    const j = await r.json();
    if (!j.ok) {
      pre.textContent = (j.error === 'no_report')
        ? (j.message || 'No run_report.json for this date yet (run FAL video first).')
        : (j.message || j.error || 'Unknown error');
      return;
    }
    pre.textContent = JSON.stringify(j.report, null, 2);
  } catch (e) {
    pre.textContent = 'Request failed: ' + e;
  }
}
const _btnRunReport = document.getElementById('btn-run-report-load');
if (_btnRunReport) _btnRunReport.addEventListener('click', () => { loadRunReport(); });
document.getElementById('btn-youtube-upload').addEventListener('click', () => { onYoutubeUploadFromPreview(); });
const _btnYtInline = document.getElementById('btn-inline-youtube-upload');
if (_btnYtInline) _btnYtInline.addEventListener('click', () => { onInlineYoutubeUploadFromPreview(); });
const _btnAssembleFinal = document.getElementById('btn-assemble-final-video');
if (_btnAssembleFinal) _btnAssembleFinal.addEventListener('click', () => { assembleFinalVideo(); });
document.getElementById('modal-backdrop').addEventListener('click', (e) => {
  if (e.target && e.target.id === 'modal-backdrop') closeModal();
});
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const bbd = document.getElementById('broll-prompt-backdrop');
  if (bbd && bbd.classList.contains('open')) {
    e.preventDefault();
    closeBrollPromptEditor();
    return;
  }
  const nbd = document.getElementById('narration-json-backdrop');
  if (nbd && nbd.classList.contains('open')) {
    e.preventDefault();
    closeNarrationJsonEditor();
    return;
  }
  closeModal();
});
