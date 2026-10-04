// produce.js — Produce tab: Script & audio / Shots & clips sub-tabs, segment table, dialogue banner.

const PRODUCE_SUBTAB_STORAGE = 'pipeline_ui_produce_subtab';
const PRODUCE_SUBTABS = ['script', 'shots', 'anchor-preview'];

function normalizedProduceSubtab(st) {
  const s = (st == null ? '' : String(st)).trim().toLowerCase();
  return PRODUCE_SUBTABS.includes(s) ? s : 'script';
}

function getProduceSubtab() {
  try {
    return normalizedProduceSubtab(localStorage.getItem(PRODUCE_SUBTAB_STORAGE));
  } catch (_) {
    return 'script';
  }
}

function syncProduceSubtabButtons(subtab) {
  const st = normalizedProduceSubtab(subtab);
  document.querySelectorAll('.produce-subtab').forEach(btn => {
    const on = btn.dataset.produceSubtab === st;
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-selected', on ? 'true' : 'false');
  });
}

function applyProduceFormFieldVisibility(subtab) {
  const st = normalizedProduceSubtab(subtab);
  document.querySelectorAll('#f fieldset[data-produce-scope]').forEach(fs => {
    const scope = fs.getAttribute('data-produce-scope') || 'both';
    const show = scope === 'both' || scope === st;
    fs.classList.toggle('hidden', !show);
  });
}

function switchProduceSubtab(nextSubtab) {
  const st = normalizedProduceSubtab(nextSubtab);
  try {
    localStorage.setItem(PRODUCE_SUBTAB_STORAGE, st);
  } catch (_) { /* ignore */ }
  syncProduceSubtabButtons(st);
  const scriptPanel = document.getElementById('produce-panel-script');
  const shotsPanel = document.getElementById('produce-panel-shots');
  const anchorPanel = document.getElementById('produce-panel-anchor-preview');
  if (scriptPanel) {
    const on = st === 'script';
    scriptPanel.classList.toggle('hidden', !on);
    scriptPanel.hidden = !on;
  }
  if (shotsPanel) {
    const on = st === 'shots';
    shotsPanel.classList.toggle('hidden', !on);
    shotsPanel.hidden = !on;
  }
  if (anchorPanel) {
    const on = st === 'anchor-preview';
    anchorPanel.classList.toggle('hidden', !on);
    anchorPanel.hidden = !on;
  }
  applyProduceFormFieldVisibility(st);
  if (st === 'shots' && normalizedWorkflow(window.__workflow) === 'narration') {
    const jd = getPickerJournalDate();
    if (jd && typeof loadBrollSuggestions === 'function') {
      loadBrollSuggestions().catch(() => {});
    }
  }
  if (st === 'anchor-preview' && normalizedWorkflow(window.__workflow) === 'narration') {
    const jd = getPickerJournalDate();
    if (jd && typeof refreshAnchorPreviewPanel === 'function') {
      refreshAnchorPreviewPanel(jd).catch(() => {});
    }
  }
}

function wireProduceSubtabs() {
  document.querySelectorAll('.produce-subtab').forEach(btn => {
    if (btn.dataset.produceWired) return;
    btn.dataset.produceWired = '1';
    btn.addEventListener('click', () => {
      const st = btn.dataset.produceSubtab;
      if (st) switchProduceSubtab(st);
    });
  });
  const details = document.getElementById('produce-raw-json-details');
  if (details && !details.dataset.rawJsonWired) {
    details.dataset.rawJsonWired = '1';
    details.addEventListener('toggle', () => {
      if (!details.open) return;
      const jd = getPickerJournalDate();
      if (jd) loadProduceRawNarrationJson(jd).catch(() => {});
    });
  }
  const linkJournal = document.getElementById('produce-link-journal');
  if (linkJournal && !linkJournal.dataset.wired) {
    linkJournal.dataset.wired = '1';
    linkJournal.addEventListener('click', (e) => {
      e.preventDefault();
      if (typeof switchPipelineWorkflow === 'function') switchPipelineWorkflow('journal');
    });
  }
}

function initProduceSubtabForWorkflow() {
  if (normalizedWorkflow(window.__workflow) !== 'narration') return;
  wireProduceSubtabs();
  if (typeof wireGenerateTtsButtons === 'function') wireGenerateTtsButtons();
  if (typeof wireAnchorPreviewPanel === 'function') wireAnchorPreviewPanel();
  switchProduceSubtab(getProduceSubtab());
}

function renderProduceDialogueBanner(data) {
  const banner = document.getElementById('produce-dialogue-banner');
  if (!banner) return;
  if (!data || !data.ok) {
    banner.classList.add('hidden');
    banner.innerHTML = '';
    return;
  }
  const parts = [];
  parts.push('<strong>Prompt pack:</strong> <code>' + escapeHtml(data.prompt_pack_hint || 'lewis_clark') + '</code>');
  if (data.dialogue_mode) {
    parts.push('<strong>Dialogue mode</strong> (from JSON)');
  } else {
    parts.push('Single-narrator / no dialogue flag in JSON');
  }
  if (data.long_conversation_mode) {
    parts.push('<strong>Long conversation</strong>');
  }
  const convRuns = data.conversation_runs || [];
  if (convRuns.length) {
    parts.push(
      '<strong>Shared backdrop:</strong> ' +
        convRuns.length +
        ' run(s) — segments ' +
        convRuns
          .map(function (r) {
            return (r.segment_indices || []).join('/');
          })
          .join('; ')
    );
  }
  if (data.focus_topic) {
    parts.push('<strong>Focus topic:</strong> ' + escapeHtml(data.focus_topic));
  }
  if (data.week_arc_active) {
    parts.push(
      '<strong>Week arc:</strong> week_' +
        escapeHtml(data.week_arc_week_id || '?') +
        (data.week_arc_role ? ' (' + escapeHtml(data.week_arc_role) + ')' : '')
    );
  }
  const th = data.talking_head_segment_count != null ? data.talking_head_segment_count : 0;
  parts.push(th + ' talking_head segment' + (th === 1 ? '' : 's'));
  const sc = data.sidecars || {};
  const side = [];
  if (sc.voice) side.push('_voice.json');
  if (sc.visual) side.push('_visual.json');
  if (side.length) parts.push('Sidecars: ' + side.join(', '));

  let html = '<div class="produce-banner-line">' + parts.join(' · ') + '</div>';
  const rules = data.rules_summary || [];
  if (rules.length) {
    html += '<ul class="produce-banner-rules">';
    rules.forEach(r => {
      html += '<li>' + escapeHtml(r) + '</li>';
    });
    html += '</ul>';
  }
  banner.innerHTML = html;
  banner.classList.remove('hidden');
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function produceVoiceCellHtml(seg) {
  const dlg = seg.dialogue || [];
  if (dlg.length) {
    return dlg
      .map(
        line =>
          '<div class="produce-dlg-line"><span class="produce-dlg-sp">' +
          escapeHtml(line.speaker_id || '?') +
          '</span>: ' +
          escapeHtml(line.preview || line.text || '') +
          '</div>'
      )
      .join('');
  }
  const prev = seg.narration_preview || '(none)';
  return '<span class="produce-narr-preview">' + escapeHtml(prev) + '</span>';
}

function producePromptField(seg) {
  return (seg.visual_mode || 'b_roll') === 'talking_head' ? 'talking_head_prompt' : 'video_prompt';
}

function producePromptPreview(seg) {
  if (producePromptField(seg) === 'talking_head_prompt') {
    return seg.talking_head_prompt_preview || '(none)';
  }
  return seg.video_prompt_preview || '(none)';
}

function producePromptFull(seg) {
  if (producePromptField(seg) === 'talking_head_prompt') {
    return seg.talking_head_prompt_full != null ? String(seg.talking_head_prompt_full) : '';
  }
  return seg.video_prompt_full != null ? String(seg.video_prompt_full) : '';
}

function produceExpandRowHtml(seg) {
  const bits = [];
  if (seg.dialogue && seg.dialogue.length) {
    const speakers = [];
    seg.dialogue.forEach(line => {
      const sp = line.speaker_id || '?';
      if (!speakers.includes(sp)) speakers.push(sp);
    });
    const label = 'dialogue[' + speakers.join(', ') + ']';
    bits.push('<div class="produce-expand-label">' + escapeHtml(label) + '</div><pre class="produce-expand-pre">');
    seg.dialogue.forEach(line => {
      bits.push(escapeHtml(line.text || '') + '\n');
    });
    bits.push('</pre>');
  } else if (seg.narration_full) {
    bits.push('<div class="produce-expand-label">narration</div><pre class="produce-expand-pre">' + escapeHtml(seg.narration_full) + '</pre>');
  }
  if (seg.visual_mode === 'talking_head') {
    if (seg.talking_head_prompt_full) {
      bits.push('<div class="produce-expand-label">talking_head_prompt</div><pre class="produce-expand-pre">' + escapeHtml(seg.talking_head_prompt_full) + '</pre>');
    }
  } else if (seg.video_prompt_full) {
    bits.push('<div class="produce-expand-label">video_prompt</div><pre class="produce-expand-pre">' + escapeHtml(seg.video_prompt_full) + '</pre>');
  }
  if (seg.opening_frame_full) {
    bits.push(
      '<div class="produce-expand-label">opening_frame</div><pre class="produce-expand-pre">' +
        escapeHtml(seg.opening_frame_full) +
        '</pre>'
    );
  }
  if (seg.reference_character_id) {
    bits.push('<div class="produce-expand-meta">reference_character_id: <code>' + escapeHtml(seg.reference_character_id) + '</code></div>');
  }
  if (seg.visual_mode === 'talking_head' && seg.portrait_status && seg.portrait_status !== 'ok' && seg.portrait_status !== 'n/a') {
    bits.push('<div class="produce-expand-warn">Portrait: ' + escapeHtml(seg.portrait_status) + '</div>');
  }
  if (seg.conversation_anchor_run && seg.conversation_run_segments && seg.conversation_run_segments.length) {
    bits.push(
      '<div class="produce-expand-meta produce-conv-hint">Shared backdrop run: segments ' +
        escapeHtml(seg.conversation_run_segments.join(', ')) +
        (seg.conversation_composite_id
          ? ' · composite <code>' + escapeHtml(seg.conversation_composite_id) + '</code>'
          : '') +
        '. One master scene anchor is split per speaker — use Anchor preview → Build anchors.</div>'
    );
  }
  return bits.join('');
}

function renderProduceSegmentsTable(data) {
  const host = document.getElementById('produce-segments-host');
  const tbody = document.getElementById('produce-segments-tbody');
  const status = document.getElementById('produce-segments-status');
  if (!tbody || !host) return;
  tbody.innerHTML = '';
  if (!data || !data.ok || !data.segments || !data.segments.length) {
    host.style.display = 'none';
    if (status) {
      status.textContent = data && data.message ? data.message : 'No segments in narration JSON.';
    }
    return;
  }
  if (status) {
    status.textContent =
      data.segment_count +
      ' segment(s). Edit voice / Edit TH or Edit VP / Edit OF to change text; type DELETEME to remove a segment.';
  }
  host.style.display = 'block';

  data.segments.forEach(seg => {
    const si = seg.index;
    const tr = document.createElement('tr');
    tr.className = 'produce-seg-row';
    const mode = seg.visual_mode || 'b_roll';
    const subj = seg.talking_head_subject || seg.reference_character_id || '—';
    const clipCell = seg.clip_exists
      ? '<span class="produce-ok" title="' + escapeHtml(seg.clip_rel || '') + '">yes</span>'
      : '<span class="produce-muted">—</span>';
    const audioCell = seg.segment_mp3_exists
      ? '<span class="produce-ok" title="' + escapeHtml(seg.segment_mp3_rel || '') + '">yes</span>'
      : '<span class="produce-muted">—</span>';
    let anchorHint = '';
    if (seg.anchor_exists) anchorHint = ' · anchor';
    if (seg.conversation_anchor_run && seg.conversation_run_segments && seg.conversation_run_segments.length) {
      anchorHint += ' · <span class="produce-conv-badge" title="Shared backdrop with segments ' + escapeHtml(seg.conversation_run_segments.join(', ')) + '">shared run</span>';
    }
    const promptPrev = producePromptPreview(seg);
    const promptLabel = producePromptField(seg) === 'talking_head_prompt' ? 'Prompt (talking_head)' : 'Prompt (video)';
    const ofPrev = seg.opening_frame_preview || '(none)';

    tr.innerHTML =
      '<td data-label="Segment">' +
      si +
      '</td>' +
      '<td data-label="Mode"><code>' +
      escapeHtml(mode) +
      '</code></td>' +
      '<td data-label="Subject">' +
      escapeHtml(subj) +
      anchorHint +
      '</td>' +
      '<td class="produce-voice-cell" data-label="Voice / narration">' +
      produceVoiceCellHtml(seg) +
      '</td>' +
      '<td class="produce-prompt-cell" data-label="' +
      escapeHtml(promptLabel) +
      '">' +
      escapeHtml(promptPrev) +
      '</td>' +
      '<td class="produce-of-cell" data-label="Opening frame" title="' +
      escapeHtml(seg.opening_frame_full || '') +
      '">' +
      escapeHtml(ofPrev) +
      '</td>' +
      '<td data-label="Audio">' +
      audioCell +
      '</td>' +
      '<td data-label="Clip">' +
      clipCell +
      '</td>' +
      '<td class="produce-actions" data-label="Actions"></td>';

    const actions = tr.querySelector('.produce-actions');
    const btnPrompt = document.createElement('button');
    btnPrompt.type = 'button';
    btnPrompt.className = 'linklike produce-action-btn';
    btnPrompt.textContent = mode === 'talking_head' ? 'Edit TH' : 'Edit VP';
    btnPrompt.addEventListener('click', () => {
      const field = producePromptField(seg);
      openBrollPromptEditor(si, producePromptFull(seg), tr.querySelector('.produce-prompt-cell'), field);
    });
    if (actions) {
      if (!seg.dialogue || !seg.dialogue.length) {
        const btnNarr = document.createElement('button');
        btnNarr.type = 'button';
        btnNarr.className = 'linklike produce-action-btn';
        btnNarr.textContent = 'Edit voice';
        btnNarr.addEventListener('click', () => {
          openBrollPromptEditor(si, seg.narration_full || '', tr.querySelector('.produce-voice-cell'), 'narration');
        });
        actions.appendChild(btnNarr);
      }
      actions.appendChild(btnPrompt);
      const btnOf = document.createElement('button');
      btnOf.type = 'button';
      btnOf.className = 'linklike produce-action-btn';
      btnOf.textContent = 'Edit OF';
      btnOf.title = 'Edit opening_frame (scene-anchor still)';
      btnOf.addEventListener('click', () => {
        openBrollPromptEditor(
          si,
          seg.opening_frame_full != null ? String(seg.opening_frame_full) : '',
          tr.querySelector('.produce-of-cell'),
          'opening_frame'
        );
      });
      actions.appendChild(btnOf);
      const btnTts = document.createElement('button');
      btnTts.type = 'button';
      btnTts.className = 'linklike produce-action-btn';
      btnTts.textContent = 'Regen TTS';
      btnTts.title = 'Regenerate audio for segment ' + si + ' only';
      btnTts.addEventListener('click', () => {
        if (typeof runGenerateTts !== 'function') return;
        runGenerateTts({
          journalDate: getPickerJournalDate(),
          segmentIndices: String(si),
          feedbackEl: document.getElementById('generate-tts-feedback'),
          skipConfirm: true,
        }).catch(() => {});
      });
      actions.appendChild(btnTts);
    }

    tbody.appendChild(tr);

    const expandHtml = produceExpandRowHtml(seg);
    if (expandHtml) {
      const trExp = document.createElement('tr');
      trExp.className = 'produce-seg-expand';
      const td = document.createElement('td');
      td.colSpan = 9;
      td.innerHTML = expandHtml;
      trExp.appendChild(td);
      tbody.appendChild(trExp);
    }
  });
}

async function loadProducePhase1DialoguePreview(journalDate) {
  const wrap = document.getElementById('produce-phase1-dialogue-wrap');
  const pre = document.getElementById('produce-phase1-dialogue-prompt-pre');
  const srcEl = document.getElementById('produce-phase1-dialogue-user-source');
  if (!wrap || !pre) return;
  const dlg = document.getElementById('dialogue_mode');
  const longConv = document.getElementById('long_conversation_mode');
  const dialogueOn = dlg && dlg.checked;
  const longOn = longConv && longConv.checked;
  if (!dialogueOn && !longOn) {
    wrap.style.display = 'none';
    return;
  }
  try {
    const r = await fetch(
      '/api/phase1-prompt-preview?journal_date=' +
        encodeURIComponent(journalDate) +
        '&dialogue=' +
        (dialogueOn ? '1' : '0') +
        '&long_conversation=' +
        (longOn ? '1' : '0'),
      { cache: 'no-store' }
    );
    const data = await r.json().catch(() => ({}));
    if (!data.ok || !data.dialogue_effective || !data.phase1_dialogue_pretty) {
      wrap.style.display = 'none';
      return;
    }
    wrap.style.display = 'block';
    pre.textContent = data.phase1_dialogue_pretty;
    if (srcEl) {
      let s = data.phase1_dialogue_user_source || '';
      if (data.phase1_dialogue_user_error) s += (s ? ' — ' : '') + String(data.phase1_dialogue_user_error);
      srcEl.textContent = s;
    }
  } catch (_) {
    wrap.style.display = 'none';
  }
}

async function loadProduceRawNarrationJson(journalDate) {
  const pre = document.getElementById('narration-inline-pre');
  if (!pre) return;
  pre.textContent = 'Loading…';
  try {
    const r = await fetch('/api/preview/narration?journal_date=' + encodeURIComponent(journalDate), {
      cache: 'no-store',
    });
    const t = await r.text();
    pre.textContent = r.ok ? t : t || 'No narration file.';
    if (r.ok && typeof updateNarrationMetaNoteFromRaw === 'function') {
      updateNarrationMetaNoteFromRaw(t);
    }
  } catch (e) {
    pre.textContent = 'Error: ' + e;
  }
}

async function loadProduceSegments(journalDate) {
  const pathCode = document.getElementById('narration-inline-path');
  const titleEl = document.getElementById('produce-segments-title');
  const status = document.getElementById('produce-segments-status');
  const linkJournal = document.getElementById('produce-link-journal');
  if (!journalDate) {
    if (pathCode) pathCode.textContent = 'narrations/narration<date>.json';
    if (status) status.textContent = 'Pick a journal date above.';
    if (titleEl) titleEl.textContent = '';
    renderProduceDialogueBanner(null);
    renderProduceSegmentsTable(null);
    return;
  }
  const dateId = journalDate.replace(/-/g, '');
  if (pathCode) pathCode.textContent = 'narrations/narration' + dateId + '.json';
  if (linkJournal) linkJournal.style.display = 'inline';
  if (status) status.textContent = 'Loading segments…';
  renderProduceDialogueBanner(null);
  renderProduceSegmentsTable(null);

  try {
    const r = await fetch('/api/narration-segments?journal_date=' + encodeURIComponent(journalDate), {
      cache: 'no-store',
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) {
      if (status) {
        status.textContent =
          (data && data.message) || (data && data.error) || 'No narration file for this date yet.';
      }
      renderProduceDialogueBanner(null);
      const wrap = document.getElementById('produce-phase1-dialogue-wrap');
      if (wrap) wrap.style.display = 'none';
      return;
    }
    window.__produceSegments = data;
    if (titleEl && data.title) titleEl.textContent = ' — ' + data.title;
    else if (titleEl) titleEl.textContent = '';
    if (typeof updateTtsAudioStatusFromPayload === 'function') {
      updateTtsAudioStatusFromPayload(data);
    }
    if (typeof syncTtsPrimaryButtonLabels === 'function') {
      syncTtsPrimaryButtonLabels(!!(data.tts_ready || data.final_mp3_exists));
    }
    renderProduceDialogueBanner(data);
    renderProduceSegmentsTable(data);
    if (data.dialogue_mode || data.long_conversation_mode) {
      await loadProducePhase1DialoguePreview(journalDate);
    } else {
      const wrap = document.getElementById('produce-phase1-dialogue-wrap');
      if (wrap) wrap.style.display = 'none';
    }
  } catch (e) {
    if (status) status.textContent = 'Error: ' + e;
  }
}

async function refreshProduceScriptPanel(journalDate) {
  await loadProduceSegments(journalDate);
  const details = document.getElementById('produce-raw-json-details');
  if (details && details.open) {
    await loadProduceRawNarrationJson(journalDate);
  }
}
