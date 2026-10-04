// journal.js — journal date picker cascade: UNL link, phase1 preview, narration inline,
// inline video preview, dialogue mode sync, journal entry preview.

const UNL_JOURNAL_ITEM_BASE = 'https://lewisandclarkjournals.unl.edu/item/lc.jrn.';

function resolveJournalDateStr() {
  const wf = normalizedWorkflow(window.__workflow || 'journal');
  if (wf === 'full') {
    const dateField = document.getElementById('date');
    if (dateField && dateField.value.trim()) return dateField.value.trim();
  }
  return getPickerJournalDate();
}

function syncUnlJournalLink() {
  const a = document.getElementById('journal-unl-link');
  if (!a) return;
  const raw = resolveJournalDateStr();
  let ok = false;
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    const y = parseInt(raw.slice(0, 4), 10);
    if (y >= 1803 && y <= 1806) {
      a.href = UNL_JOURNAL_ITEM_BASE + raw;
      ok = true;
    }
  }
  if (ok) {
    a.classList.remove('is-disabled');
    a.dataset.ready = '1';
    a.setAttribute('aria-disabled', 'false');
  } else {
    a.href = '#';
    a.classList.add('is-disabled');
    a.dataset.ready = '0';
    a.setAttribute('aria-disabled', 'true');
  }
}

function renderEpisodeDiversityPanel(episodeDiversity) {
  const statusEl = document.getElementById('episode-diversity-status');
  const recentBlock = document.getElementById('episode-diversity-recent-block');
  const hintsBlock = document.getElementById('episode-diversity-hints-block');
  const llmBlock = document.getElementById('episode-diversity-llm-block');
  const llmMeta = document.getElementById('episode-diversity-llm-meta');
  const llmRefresh = document.getElementById('episode-diversity-llm-refresh');
  const recentUl = document.getElementById('episode-diversity-recent');
  const hintsUl = document.getElementById('episode-diversity-hints');
  const llmHintsUl = document.getElementById('episode-diversity-llm-hints');
  if (!statusEl) return;

  const clearList = ul => {
    if (!ul) return;
    while (ul.firstChild) ul.removeChild(ul.firstChild);
  };
  const fillList = (ul, items) => {
    if (!ul) return;
    clearList(ul);
    (items || []).forEach(line => {
      const li = document.createElement('li');
      li.textContent = line;
      ul.appendChild(li);
    });
  };
  const hideLlm = () => {
    if (llmBlock) llmBlock.style.display = 'none';
    if (llmMeta) llmMeta.textContent = '';
    if (llmRefresh) {
      while (llmRefresh.firstChild) llmRefresh.removeChild(llmRefresh.firstChild);
    }
    clearList(llmHintsUl);
  };

  if (!episodeDiversity) {
    statusEl.textContent = 'Pick a journal date above.';
    if (recentBlock) recentBlock.style.display = 'none';
    if (hintsBlock) hintsBlock.style.display = 'none';
    clearList(recentUl);
    clearList(hintsUl);
    hideLlm();
    return;
  }

  if (episodeDiversity.applicable === false) {
    const pack = episodeDiversity.prompt_pack ? String(episodeDiversity.prompt_pack) : '';
    statusEl.textContent =
      'Episode diversity applies only to Lewis & Clark prompt packs (lewis_clark, lewis_clark_dialogue, lewis_clark_long_conversation).' +
      (pack ? ' Current preview pack: ' + pack + '.' : '');
    if (recentBlock) recentBlock.style.display = 'none';
    if (hintsBlock) hintsBlock.style.display = 'none';
    clearList(recentUl);
    clearList(hintsUl);
    hideLlm();
    return;
  }

  const enabled = !!episodeDiversity.enabled;
  const recent = episodeDiversity.recent_episodes || [];
  const hints = episodeDiversity.hints || [];
  const windowN = episodeDiversity.recent_window;
  const llm = episodeDiversity.llm_audit || null;

  if (!enabled) {
    statusEl.textContent =
      'Episode diversity is disabled for this prompt pack (prompt_packs/' +
      (episodeDiversity.prompt_pack || 'lewis_clark') +
      '/pack.json → episode_diversity.enabled = false).';
    if (recentBlock) recentBlock.style.display = 'none';
    if (hintsBlock) hintsBlock.style.display = 'none';
    clearList(recentUl);
    clearList(hintsUl);
    hideLlm();
    return;
  }

  if (!recent.length && !hints.length) {
    const w = windowN != null ? String(windowN) : '5';
    statusEl.textContent =
      'No prior merged narrations on disk yet (or fewer than needed). Soft hints appear after enough earlier episodes exist; window = ' +
      w +
      ' prior files.';
    if (recentBlock) recentBlock.style.display = 'none';
    if (hintsBlock) hintsBlock.style.display = 'none';
    clearList(recentUl);
    clearList(hintsUl);
    hideLlm();
    return;
  }

  statusEl.textContent =
    'Hints below are appended to the Phase 1 user prompt (history is UI-only; variety must not contradict today\u2019s journal).';
  fillList(recentUl, recent);
  fillList(hintsUl, hints);
  if (recentBlock) recentBlock.style.display = recent.length ? 'block' : 'none';
  if (hintsBlock) hintsBlock.style.display = hints.length ? 'block' : 'none';

  if (llm && llm.enabled) {
    const parts = [];
    if (llm.cache_present) {
      if (llm.generated_at) parts.push('Generated ' + llm.generated_at);
      if (llm.through_date_id) parts.push('through ' + llm.through_date_id);
      if (llm.stale) parts.push('stale — refresh recommended');
      else parts.push('fresh');
      if (llm.dynamic_hints_active != null && llm.dynamic_hints_active > 0) {
        parts.push(String(llm.dynamic_hints_active) + ' dynamic hint(s) active in Phase 1');
      }
    } else {
      parts.push('No cache on disk yet');
    }
    if (llmMeta) {
      llmMeta.textContent = parts.join(' · ');
      llmMeta.classList.toggle('episode-diversity-llm-stale', !!llm.stale);
    }
    fillList(llmHintsUl, llm.cached_hints || []);
    if (llmRefresh) {
      while (llmRefresh.firstChild) llmRefresh.removeChild(llmRefresh.firstChild);
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'diversity-audit-refresh';
      btn.textContent = 'Refresh diversity audit';
      btn.title = 'One OpenAI call over the last 15 narrations; updates state/episode_diversity_lewis_clark.json';
      btn.addEventListener('click', () => {
        if (typeof refreshEpisodeDiversityAudit === 'function') {
          refreshEpisodeDiversityAudit(btn);
        } else {
          alert('Refresh is unavailable until Library scripts finish loading. Try again in a moment.');
        }
      });
      llmRefresh.appendChild(btn);
      const hint = document.createElement('div');
      hint.className = 'help diversity-refresh-cmd';
      hint.textContent =
        'CLI: python scripts/refresh_episode_diversity_audit.py --last 15 --prompt-pack lewis_clark';
      llmRefresh.appendChild(hint);
    }
    if (llmBlock) llmBlock.style.display = 'block';
  } else {
    hideLlm();
  }
}

function syncPickerFromDateField() {
  const dateField = document.getElementById('date');
  const picker = document.getElementById('journalDatePicker');
  if (!picker) return;
  if (dateField) {
    const v = dateField.value.trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(v)) {
      const y = parseInt(v.slice(0, 4), 10);
      if (y >= 1803 && y <= 1806) picker.value = v;
    }
  }
  syncUnlJournalLink();
  updateRunFlagSummary();
}

async function loadPhase1PromptPreview(journalDate) {
  const pre = document.getElementById('phase1-prompt-pre');
  const errEl = document.getElementById('phase1-prompt-err');
  const wrapDlg = document.getElementById('phase1-dialogue-prompt-wrap');
  const preDlg = document.getElementById('phase1-dialogue-prompt-pre');
  const srcDlg = document.getElementById('phase1-dialogue-user-source');
  if (!pre) return;
  if (wrapDlg) wrapDlg.style.display = 'none';
  if (preDlg) preDlg.textContent = '';
  if (srcDlg) srcDlg.textContent = '';
  if (errEl) {
    errEl.style.display = 'none';
    errEl.textContent = '';
  }
  if (!journalDate) {
    pre.textContent = 'Pick a journal date above to load the preview.';
    renderEpisodeDiversityPanel(null);
    return;
  }
  renderEpisodeDiversityPanel({ enabled: true, recent_episodes: [], hints: [] });
  pre.textContent = 'Loading Phase 1 prompt preview…';
  const noFocus = document.getElementById('journalNoFocusTopic');
  const nt = noFocus && noFocus.checked ? '1' : '0';
  const dlg = document.getElementById('journalDialogueMode');
  const d = dlg && dlg.checked ? '1' : '0';
  const lng = document.getElementById('journalLongConversationMode');
  const lc = lng && lng.checked ? '1' : '0';
  try {
    const r = await fetch(
      '/api/phase1-prompt-preview?journal_date=' +
        encodeURIComponent(journalDate) +
        '&no_focus_topic=' +
        nt +
        '&dialogue=' +
        d +
        '&long_conversation=' +
        lc,
      { cache: 'no-store' }
    );
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) {
      pre.textContent = data.message || data.error || r.statusText || 'Failed to load preview.';
      if (errEl && data.message) {
        errEl.textContent = data.message;
        errEl.style.display = 'block';
      }
      return;
    }
    pre.textContent = data.pretty || (data.system + '\n\n' + data.user);
    renderEpisodeDiversityPanel(data.episode_diversity || null);
    if (data.dialogue_effective && data.phase1_dialogue_pretty && wrapDlg && preDlg) {
      wrapDlg.style.display = 'block';
      preDlg.textContent = data.phase1_dialogue_pretty;
      if (srcDlg) {
        let s = data.phase1_dialogue_user_source || '';
        if (data.phase1_dialogue_user_truncated) s += (s ? ' ' : '') + '(user preview truncated)';
        if (data.phase1_dialogue_user_error) s += (s ? ' — ' : '') + String(data.phase1_dialogue_user_error);
        srcDlg.textContent = s || '(stub until voice sidecar exists)';
      }
    }
  } catch (e) {
    let msg = String(e);
    if (
      (e instanceof TypeError && /failed to fetch/i.test(msg)) ||
      (e instanceof DOMException && e.name === 'NetworkError')
    ) {
      msg =
        'Network error (could not reach this page’s server URL). ' +
        'Confirm pipeline_ui/server.py is running here; from another machine set PIPELINE_UI_HOST=0.0.0.0 and reopen the matching http://HOST:PORT. ' +
        'If you use --reload, saving files restarts the server and can abort in-flight previews — retry once.';
    }
    pre.textContent = 'Error: ' + msg;
  }
}

function updateNarrationMetaNoteFromRaw(rawText) {
  const note = document.getElementById('narration-meta-note');
  if (!note) return;
  const clearNote = () => {
    note.style.display = 'none';
    note.textContent = '';
    note.classList.remove('narration-meta-note--review');
  };
  const asStyleLabel = value => {
    if (value == null) return '';
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      return String(value).trim();
    }
    if (Array.isArray(value)) {
      return value
        .map(v => asStyleLabel(v))
        .filter(Boolean)
        .join(', ');
    }
    if (typeof value === 'object') {
      if (value.name != null) {
        const name = String(value.name).trim();
        if (name) return name;
      }
      if (value.description != null) {
        const description = String(value.description).trim();
        if (description) return description;
      }
      return '';
    }
    return '';
  };
  let data;
  try {
    data = JSON.parse(rawText);
  } catch (_) {
    clearNote();
    return;
  }
  if (!data || typeof data !== 'object') {
    clearNote();
    return;
  }
  const focusRaw = data.focus_topic;
  const focusTopic =
    focusRaw == null || String(focusRaw).trim() === '' ? '(none)' : String(focusRaw).trim();

  const styles = [];
  const pushStyle = (label, value) => {
    const s = asStyleLabel(value);
    if (!s) return;
    styles.push(label + ': ' + s);
  };
  pushStyle('visual', data.visual_style);
  pushStyle('tone', data.tone_register);
  pushStyle('overall tone', data.overall_visual_tone);
  pushStyle('style', data.style);
  pushStyle('narration style', data.narration_style);
  pushStyle('styles', data.styles);

  const script = Array.isArray(data.narration_script) ? data.narration_script : [];
  const segVisual = new Set();
  script.forEach(seg => {
    if (!seg || typeof seg !== 'object') return;
    const label = asStyleLabel(seg.visual_style);
    if (label) segVisual.add(label);
  });
  if (segVisual.size) {
    styles.push('segment visuals: ' + Array.from(segVisual).slice(0, 4).join(', '));
  }

  const styleText = styles.length ? styles.join('  |  ') : '(none detected)';
  const parts = ['Focus topic: ' + focusTopic, 'Styles: ' + styleText];

  const oq = Array.isArray(data.open_questions)
    ? data.open_questions.map(q => String(q || '').trim()).filter(Boolean)
    : [];
  if (oq.length) {
    parts.push('Open questions (' + oq.length + '): ' + oq.slice(0, 2).join(' · '));
  }
  const editorNotes = String(data.editor_notes || '').trim();
  if (editorNotes) {
    const short =
      editorNotes.length <= 100 ? editorNotes : editorNotes.slice(0, 97) + '…';
    parts.push('Editor notes: ' + short);
  }

  const lineage = data.prompt_pack_lineage;
  if (lineage && typeof lineage === 'object') {
    const packs = [];
    for (const key of ['phase1', 'phase2', 'phase1_dialogue']) {
      const block = lineage[key];
      if (!block || typeof block !== 'object') continue;
      const pid = String(block.pack_id || '').trim();
      const ver =
        block.manifest && block.manifest.version != null
          ? String(block.manifest.version).trim()
          : '';
      if (pid) packs.push(ver ? pid + '@' + ver : pid);
    }
    if (packs.length) parts.push('Packs: ' + packs.join(', '));
  }

  const tsd = data.theme_selector_decision;
  if (tsd && typeof tsd === 'object') {
    const reason = String(tsd.reason || '').trim();
    const closest = String(tsd.closest_prior_date_id || '').trim();
    if (reason || closest) {
      let prov = 'Theme';
      if (reason) prov += ' (' + reason + ')';
      if (closest) prov += ' · closest ' + closest;
      parts.push(prov);
    }
  }

  if (data.long_conversation_mode) {
    const threads = [];
    script.forEach((seg, i) => {
      if (!seg || typeof seg !== 'object') return;
      const ct = seg.conversation_tracking;
      if (!ct || typeof ct !== 'object') return;
      const carry = String(ct.continuity_carry || '').trim();
      const focus = String(ct.speaker_focus || '').trim();
      if (carry || focus) {
        const idx = seg.segment_index != null ? seg.segment_index : i + 1;
        threads.push(
          'seg ' + idx + (focus ? ' ' + focus : '') + (carry ? ': ' + carry : '')
        );
      }
    });
    if (threads.length) {
      parts.push('Threads: ' + threads.slice(0, 2).join(' · '));
    }
  }

  note.textContent = parts.join('   ·   ');
  note.classList.toggle('narration-meta-note--review', oq.length > 0);
  note.style.display = 'block';
}

async function refreshNarrationInline(journalDate) {
  if (typeof refreshProduceScriptPanel === 'function') {
    await refreshProduceScriptPanel(journalDate);
    return;
  }
  const pre = document.getElementById('narration-inline-pre');
  if (!pre) return;
  if (!journalDate) {
    pre.textContent = 'Pick a journal date above.';
    return;
  }
  pre.textContent = 'Loading narration JSON…';
  try {
    const r = await fetch(
      '/api/preview/narration?journal_date=' + encodeURIComponent(journalDate),
      { cache: 'no-store' }
    );
    const t = await r.text();
    pre.textContent = r.ok ? t : t || 'No narration file for this date yet.';
    if (r.ok) updateNarrationMetaNoteFromRaw(t);
  } catch (e) {
    pre.textContent = 'Error: ' + e;
  }
}

function getInlineVideoCuePart() {
  const vid = document.getElementById('inline-video');
  const cues = window.__inlineVideoCues;
  if (!vid || !cues || !cues.ok || !cues.parts || !cues.parts.length) return null;
  const t = vid.currentTime;
  let p = null;
  for (let i = 0; i < cues.parts.length; i++) {
    const x = cues.parts[i];
    if (t >= x.start_sec && t < x.end_sec) {
      p = x;
      break;
    }
  }
  if (!p && cues.parts.length) {
    const last = cues.parts[cues.parts.length - 1];
    const first = cues.parts[0];
    if (t >= last.end_sec) p = last;
    else if (t < first.start_sec) p = first;
  }
  return p;
}

function syncInlineVideoCueTextarea() {
  const ta = document.getElementById('inline-video-cues');
  const cues = window.__inlineVideoCues;
  if (!ta || !cues || !cues.ok || !cues.parts || !cues.parts.length) return;
  const p = getInlineVideoCuePart();
  if (!p) return;
  if (typeof formatVideoCuePartText === 'function') {
    ta.value = formatVideoCuePartText(p, cues);
    return;
  }
  ta.value = p.label + '  ·  ' + p.start_sec.toFixed(2) + 's – ' + p.end_sec.toFixed(2) + 's';
}

function inlineVideoSyncBundle() {
  syncInlineVideoCueTextarea();
  updateInlineBrollAddButtonState();
}

async function loadInlineVideoPreview(journalDate) {
  const vid = document.getElementById('inline-video');
  const ta = document.getElementById('inline-video-cues');
  if (!vid || !ta) return;
  window.__inlineVideoPreviewWatchedToEnd = false;
  const ytFb = document.getElementById('inline-youtube-upload-feedback');
  const ytBtn = document.getElementById('btn-inline-youtube-upload');
  const ytLink = document.getElementById('inline-youtube-upload-existing');
  const brollFb = document.getElementById('inline-broll-add-feedback');
  const brollBtn = document.getElementById('btn-inline-broll-add');
  if (brollFb) {
    brollFb.textContent = '';
    brollFb.style.color = '';
  }
  if (brollBtn) {
    brollBtn.disabled = true;
    brollBtn.classList.remove('broll-add-armed');
  }
  if (ytFb) {
    ytFb.textContent = '';
    ytFb.style.color = '';
  }
  if (ytBtn) {
    ytBtn.disabled = true;
    ytBtn.classList.remove('yt-upload-armed');
    ytBtn.textContent = 'Upload to YouTube';
    ytBtn.title = '';
  }
  if (ytLink) {
    ytLink.style.display = 'none';
    ytLink.removeAttribute('href');
  }
  vid.removeEventListener('timeupdate', inlineVideoSyncBundle);
  vid.removeEventListener('seeked', inlineVideoSyncBundle);
  vid.removeEventListener('pause', inlineVideoSyncBundle);
  vid.removeEventListener('playing', inlineVideoSyncBundle);
  vid.removeEventListener('loadedmetadata', inlineVideoSyncBundle);
  vid.removeEventListener('ended', inlineVideoPreviewOnEnded);
  vid.pause();
  vid.removeAttribute('src');
  vid.load();
  window.__inlineVideoCues = null;
  if (!journalDate) {
    ta.value = 'Pick a journal date above.';
    updateInlineYoutubeUploadButtonState();
    updateInlineBrollAddButtonState();
    return;
  }
  ta.value = 'Loading…';
  try {
    const cr = await fetch(
      '/api/preview/video-cues?journal_date=' + encodeURIComponent(journalDate) + '&_=' + Date.now(),
      { cache: 'no-store' }
    );
    let cues = null;
    const cuesRaw = await cr.text();
    try {
      cues = cuesRaw ? JSON.parse(cuesRaw) : null;
    } catch (_) {
      cues = null;
    }
    window.__inlineVideoCues = cues && cues.ok ? cues : null;
    if (!cues || !cues.ok) {
      ta.value =
        '(No video for this date: ' +
        (cues && (cues.message || cues.error) ? cues.message || cues.error : 'unknown') +
        ')';
      updateInlineYoutubeUploadButtonState();
      updateInlineBrollAddButtonState();
    } else {
      ta.value = 'Play the video — prompts follow segment timing.';
      vid.addEventListener('timeupdate', inlineVideoSyncBundle);
      vid.addEventListener('seeked', inlineVideoSyncBundle);
      vid.addEventListener('pause', inlineVideoSyncBundle);
      vid.addEventListener('playing', inlineVideoSyncBundle);
      vid.addEventListener('loadedmetadata', inlineVideoSyncBundle);
      vid.addEventListener('ended', inlineVideoPreviewOnEnded);
      vid.src =
        '/api/preview/video?journal_date=' + encodeURIComponent(journalDate) + '&_=' + Date.now();
      updateInlineYoutubeUploadButtonState();
      updateInlineBrollAddButtonState();
    }
  } catch (e) {
    window.__inlineVideoCues = null;
    ta.value =
      '(Failed to load video cues: ' +
      (typeof formatPipelineFetchError === 'function' ? formatPipelineFetchError(e) : e) +
      ')';
    updateInlineYoutubeUploadButtonState();
    updateInlineBrollAddButtonState();
  }
}

async function syncDialogueModeCheckboxesFromNarrationFile(journalDate) {
  if (!journalDate || !/^\d{4}-\d{2}-\d{2}$/.test(journalDate)) return;
  try {
    const r = await fetch(
      '/api/narration-dialogue-mode?journal_date=' + encodeURIComponent(journalDate),
      { cache: 'no-store' }
    );
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) return;
    // No narration file yet: keep journal/form checkboxes as-is (user intent for first run).
    if (data.source === 'no_file') {
      syncLongConversationDialogueDependency();
      return;
    }
    // When a file exists, mirror its flags so a prior tab/date cannot leave long-conversation stuck on.
    const j = document.getElementById('journalDialogueMode');
    if (j) j.checked = !!data.dialogue_mode;
    const f = document.getElementById('dialogue_mode');
    if (f) f.checked = !!data.dialogue_mode;
    const jl = document.getElementById('journalLongConversationMode');
    if (jl) jl.checked = !!data.long_conversation_mode;
    const fl = document.getElementById('long_conversation_mode');
    if (fl) fl.checked = !!data.long_conversation_mode;
    syncLongConversationDialogueDependency();
  } catch (e) {
    /* ignore */
  }
}

async function onPipelineJournalDateChanged(v) {
  if (!v) return;
  await syncDialogueModeCheckboxesFromNarrationFile(v);
  const wf = normalizedWorkflow(window.__workflow || 'journal');
  if (wf === 'journal') await loadPhase1PromptPreview(v);
  else if (wf === 'narration') {
    await refreshNarrationInline(v);
    if (typeof getProduceSubtab === 'function' && getProduceSubtab() === 'shots') {
      await loadBrollSuggestions();
    }
    if (
      typeof getProduceSubtab === 'function' &&
      getProduceSubtab() === 'anchor-preview' &&
      typeof refreshAnchorPreviewPanel === 'function'
    ) {
      await refreshAnchorPreviewPanel(v);
    }
  } else if (wf === 'video') await loadInlineVideoPreview(v);
}

function wireJournalPicker() {
  const picker = document.getElementById('journalDatePicker');
  const dateField = document.getElementById('date');
  if (!picker) return;
  picker.addEventListener('change', async () => {
    const v = picker.value;
    if (!v) return;
    if (dateField) dateField.value = v;
    syncUnlJournalLink();
    await onPipelineJournalDateChanged(v);
    updateRunFlagSummary();
  });
  const noFocus = document.getElementById('journalNoFocusTopic');
  if (noFocus) {
    noFocus.addEventListener('change', () => {
      const jd = getPickerJournalDate();
      if (jd && normalizedWorkflow(window.__workflow) === 'journal') {
        loadPhase1PromptPreview(jd).catch(() => {});
      }
    });
  }
  const dlgMode = document.getElementById('journalDialogueMode');
  if (dlgMode) {
    dlgMode.addEventListener('change', () => {
      const formDlg = document.getElementById('dialogue_mode');
      if (formDlg) formDlg.checked = dlgMode.checked;
      syncLongConversationDialogueDependency();
      const jd = getPickerJournalDate();
      if (jd && normalizedWorkflow(window.__workflow) === 'journal') {
        loadPhase1PromptPreview(jd).catch(() => {});
      }
    });
  }
  const longConv = document.getElementById('journalLongConversationMode');
  if (longConv) {
    longConv.addEventListener('change', () => {
      const formLong = document.getElementById('long_conversation_mode');
      if (formLong) formLong.checked = longConv.checked;
      syncLongConversationDialogueDependency();
      const jd = getPickerJournalDate();
      if (jd && normalizedWorkflow(window.__workflow) === 'journal') {
        loadPhase1PromptPreview(jd).catch(() => {});
      }
    });
  }
  const weekArc = document.getElementById('journalUseWeekArc');
  if (weekArc) {
    weekArc.addEventListener('change', () => {
      syncJournalWeekArcControls();
    });
    syncJournalWeekArcControls();
  }
  if (dateField) {
    dateField.addEventListener('input', () => {
      syncPickerFromDateField();
    });
  }
  const unl = document.getElementById('journal-unl-link');
  if (unl) {
    unl.addEventListener('click', function (e) {
      if (this.dataset.ready !== '1') e.preventDefault();
    });
  }
  const btnXml = document.getElementById('btn-open-journal-xml');
  if (btnXml) {
    btnXml.addEventListener('click', () => {
      const d = getPickerJournalDate();
      if (d) openJournalPreview(d);
    });
  }
  const btnReset = document.getElementById('btn-reset-day');
  if (btnReset && !btnReset.dataset.resetWired) {
    btnReset.dataset.resetWired = '1';
    btnReset.addEventListener('click', () => {
      onResetDayArtifacts().catch((e) => {
        window.alert('Reset failed: ' + e);
      });
    });
  }
  const brollEnv = document.getElementById('brollPreferEnv');
  if (brollEnv) {
    brollEnv.addEventListener('change', () => {
      if (normalizedWorkflow(window.__workflow) === 'narration') {
        loadBrollSuggestions().catch(() => {});
      }
    });
  }
}

async function onResetDayArtifacts() {
  if (window.__pipelineResetDaySupported === false) {
    window.alert(
      'Reset day is not available on the running Pipeline UI server.\n\n' +
        'Stop the server (Ctrl+C in its terminal), then from the repo root run:\n' +
        '  .\\.venv\\Scripts\\python.exe pipeline_ui\\server.py --reload\n\n' +
        'Hard-refresh this page (Ctrl+F5), then try again.'
    );
    return;
  }
  const jd = getPickerJournalDate();
  if (!jd) {
    window.alert('Choose a journal date first.');
    return;
  }
  const dateId = jd.replace(/-/g, '');
  const msg =
    'Archive all pipeline artifacts for ' +
    jd +
    ' (' +
    dateId +
    ')?\n\n' +
    'This moves narrations, audio, movie-images, and output files into archive/reset_' +
    dateId +
    '_<timestamp>/.\n\n' +
    'Journal XML is kept. This cannot be undone from the UI.';
  if (!window.confirm(msg)) return;

  const statusEl = document.getElementById('reset-day-status');
  const btn = document.getElementById('btn-reset-day');
  if (btn) btn.disabled = true;
  if (statusEl) {
    statusEl.style.display = 'block';
    statusEl.textContent = 'Archiving…';
    statusEl.classList.remove('err');
  }
  try {
    let r = await fetch('/api/reset-day', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ journal_date: jd }),
    });
    if (r.status === 404) {
      r = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'reset_day', journal_date: jd }),
      });
    }
    const raw = await r.text();
    let data = {};
    try {
      data = raw ? JSON.parse(raw) : {};
    } catch (_) {
      const hint =
        r.status === 404
          ? 'Server returned "Not found" for POST /api/reset-day — restart pipeline_ui/server.py (use --reload after updating).'
          : 'Response was not JSON: ' + (raw ? raw.slice(0, 200) : r.status);
      if (statusEl) {
        statusEl.textContent = hint;
        statusEl.classList.add('err');
      }
      window.alert('Reset failed: ' + hint);
      return;
    }
    if (!r.ok || !data.ok) {
      const err =
        data.message ||
        data.error ||
        (r.status === 404
          ? 'POST /api/reset-day not found — restart pipeline_ui/server.py'
          : String(r.status));
      if (statusEl) {
        statusEl.textContent = 'Reset failed: ' + err;
        statusEl.classList.add('err');
      }
      window.alert('Reset failed: ' + err);
      return;
    }
    const moved = data.moved && data.moved.length ? data.moved.join(', ') : '(nothing to move)';
    const backup = data.backup_dir || '(no backup folder)';
    if (statusEl) {
      statusEl.textContent = 'Archived to ' + backup + '. Moved: ' + moved;
    }
    await onPipelineJournalDateChanged(jd);
    if (typeof refreshNarrationInline === 'function') {
      await refreshNarrationInline(jd);
    }
    if (typeof loadInlineVideoPreview === 'function') {
      await loadInlineVideoPreview(jd);
    }
    window.alert('Day reset complete.\n\nBackup: ' + backup + '\n\nMoved: ' + moved);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function openJournalPreview(dateStr) {
  document.getElementById('modal-title').textContent = 'Diary entry — ' + dateStr;
  document.getElementById('modal-body-json').style.display = 'block';
  document.getElementById('modal-body-video').style.display = 'none';
  const pre = document.getElementById('modal-pre');
  pre.textContent = 'Loading…';
  openModal();
  try {
    const r = await fetch('/api/journal/' + encodeURIComponent(dateStr), { cache: 'no-store' });
    const text = await r.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      pre.textContent = 'Invalid response: ' + r.status + '\n' + text.slice(0, 4000);
      return;
    }
    if (data.ok && data.content != null) {
      pre.textContent = data.content;
    } else if (data.error === 'not_found') {
      pre.textContent =
        'No journal file for this date.\n\n' +
        'Expected: journal-entries/' + dateStr + '.xml\n\n' +
        'Run the scraper or add the file, then pick the date again.';
    } else {
      pre.textContent = data.message || data.error || JSON.stringify(data, null, 2);
    }
  } catch (e) {
    pre.textContent = 'Error: ' + e;
  }
}
