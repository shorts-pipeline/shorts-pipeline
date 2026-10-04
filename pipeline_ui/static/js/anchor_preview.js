// anchor_preview.js — Produce → Anchor preview: batch anchors, TTS preview video, handoff to full FAL video.

function getAnchorPreviewCuePart() {
  const vid = document.getElementById('anchor-preview-video');
  const cues = window.__anchorPreviewCues;
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

function syncAnchorPreviewCueTextarea() {
  const ta = document.getElementById('anchor-preview-cues');
  const cues = window.__anchorPreviewCues;
  if (!ta || !cues || !cues.ok || !cues.parts || !cues.parts.length) return;
  const p = getAnchorPreviewCuePart();
  if (!p) return;
  if (typeof formatVideoCuePartText === 'function') {
    ta.value = formatVideoCuePartText(p, cues);
    return;
  }
  ta.value = p.label + '  ·  ' + p.start_sec.toFixed(2) + 's – ' + p.end_sec.toFixed(2) + 's';
}

function anchorPreviewVideoSyncBundle() {
  syncAnchorPreviewCueTextarea();
}

function formatAnchorPreviewIssueLine(issue) {
  if (!issue || !issue.message) return '';
  const seg =
    issue.segment_index != null && issue.segment_index !== ''
      ? 'Seg ' + issue.segment_index + ': '
      : '';
  const kind = issue.kind ? '[' + issue.kind + '] ' : '';
  return kind + seg + issue.message;
}

function renderAnchorPreviewIssuesPanel(issues, hasError) {
  const el = document.getElementById('anchor-preview-issues');
  if (!el) return;
  if (!issues || !issues.length) {
    el.textContent = '';
    el.classList.add('hidden');
    el.classList.remove('err');
    return;
  }
  el.textContent = issues.map(formatAnchorPreviewIssueLine).join('\n');
  el.classList.remove('hidden');
  el.classList.toggle('err', !!hasError);
}

function applyAnchorPreviewJobResult(data, journalDate) {
  const st = document.getElementById('anchor-preview-job-status');
  if (!data) return;
  const payload = data.result && typeof data.result === 'object' ? data.result : data;
  const issues = payload.issues || [];
  const failed = data.status === 'failed' || payload.ok === false;
  if (st) {
    if (failed) {
      st.textContent =
        'Failed: ' + (payload.message || payload.error || data.error || 'unknown error');
      st.className = 'help err';
    } else if (issues.length) {
      st.textContent =
        (payload.phase === 'anchors' ? 'Anchor build' : 'Assemble') +
        ' finished with warnings (see below).';
      st.className = 'help';
    } else {
      st.textContent =
        payload.phase === 'anchors'
          ? 'Anchor build complete.'
          : 'Preview assembly complete.';
      st.className = 'help';
    }
  }
  renderAnchorPreviewIssuesPanel(
    issues,
    failed || issues.some(function (i) {
      return i.kind === 'clip_render' || i.kind === 'assembly' || i.kind === 'anchor_build';
    })
  );
  if (journalDate) {
    loadAnchorPreviewStatus(journalDate);
  }
}

async function loadAnchorPreviewStatus(journalDate) {
  const el = document.getElementById('anchor-preview-status');
  if (!el) return null;
  if (!journalDate) {
    el.textContent = 'Pick a journal date above.';
    return null;
  }
  el.textContent = 'Loading status…';
  try {
    const r = await fetch(
      '/api/anchor-preview/status?journal_date=' + encodeURIComponent(journalDate),
      { cache: 'no-store' }
    );
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) {
      el.textContent = data.message || data.error || 'Status failed';
      el.className = 'help anchor-preview-status err';
      return null;
    }
    el.className = 'help anchor-preview-status';
    const lines = [];
    lines.push(
      'TTS: ' + (data.tts_ready ? 'ready' : 'missing — ' + (data.tts_error || 'run TTS first'))
    );
    lines.push(
      'Scene anchors: ' +
        data.anchors_on_disk +
        ' / ' +
        data.eligible_count +
        ' segments'
    );
    const convRuns = data.conversation_runs || [];
    if (convRuns.length) {
      lines.push(
        'Shared backdrop runs: ' +
          convRuns.length +
          ' (segments ' +
          convRuns
            .map(function (r) {
              return (r.segment_indices || []).join('/');
            })
            .join('; ') +
          ') — Build anchors creates one master per run'
      );
    }
    lines.push('Preview clips on disk: ' + (data.preview_clips_count || 0));
    lines.push(
      'Preview video: ' + (data.preview_video_exists ? data.preview_video_rel : 'not assembled yet')
    );
    if (data.talking_head_missing_anchor && data.talking_head_missing_anchor.length) {
      lines.push(
        'Talking-head missing anchor (segments): ' + data.talking_head_missing_anchor.join(', ')
      );
    }
    if (data.has_warnings && data.issues && data.issues.length) {
      lines.push('Warnings: ' + data.issues.length + ' (see details below)');
      el.className = 'help anchor-preview-status err';
    }
    el.textContent = lines.join('\n');
    renderAnchorPreviewIssuesPanel(
      data.issues || [],
      !!(data.issues && data.issues.length)
    );
    if (typeof updateTtsAudioStatusFromPayload === 'function') {
      updateTtsAudioStatusFromPayload(data);
    }
    if (typeof syncTtsPrimaryButtonLabels === 'function') {
      syncTtsPrimaryButtonLabels(!!data.tts_ready);
    }
    return data;
  } catch (e) {
    el.textContent = 'Error: ' + e;
    el.className = 'help anchor-preview-status err';
    return null;
  }
}

async function loadAnchorPreviewVideo(journalDate) {
  const vid = document.getElementById('anchor-preview-video');
  const ta = document.getElementById('anchor-preview-cues');
  if (!vid || !ta) return;
  vid.removeEventListener('timeupdate', anchorPreviewVideoSyncBundle);
  vid.removeEventListener('seeked', anchorPreviewVideoSyncBundle);
  vid.removeEventListener('pause', anchorPreviewVideoSyncBundle);
  vid.removeEventListener('playing', anchorPreviewVideoSyncBundle);
  vid.removeEventListener('loadedmetadata', anchorPreviewVideoSyncBundle);
  vid.pause();
  vid.removeAttribute('src');
  vid.load();
  window.__anchorPreviewCues = null;
  if (!journalDate) {
    ta.value = 'Pick a journal date above.';
    return;
  }
  ta.value = 'Loading…';
  try {
    const cr = await fetch(
      '/api/preview/video-cues?journal_date=' +
        encodeURIComponent(journalDate) +
        '&preview_mode=anchor&_=' +
        Date.now(),
      { cache: 'no-store' }
    );
    const cues = await cr.json().catch(() => ({}));
    window.__anchorPreviewCues = cues.ok ? cues : null;
    if (!cues.ok) {
      ta.value = cues.message || cues.error || 'Could not load timeline cues.';
    }
  } catch (e) {
    ta.value = 'Cue load error: ' + e;
  }
  const st = await loadAnchorPreviewStatus(journalDate);
  if (!st || !st.preview_video_exists) {
    if (st && !st.tts_ready) {
      /* status panel already explains */
    } else if (!st || !st.preview_video_exists) {
      ta.value = (ta.value || '') + '\n\n(No preview video yet — build clips + assemble.)';
    }
    return;
  }
  vid.src =
    '/api/preview/anchor-preview-video?journal_date=' +
    encodeURIComponent(journalDate) +
    '&_=' +
    Date.now();
  vid.addEventListener('timeupdate', anchorPreviewVideoSyncBundle);
  vid.addEventListener('seeked', anchorPreviewVideoSyncBundle);
  vid.addEventListener('pause', anchorPreviewVideoSyncBundle);
  vid.addEventListener('playing', anchorPreviewVideoSyncBundle);
  vid.addEventListener('loadedmetadata', anchorPreviewVideoSyncBundle);
  syncAnchorPreviewCueTextarea();
}

async function pollAnchorPreviewJob(pollUrl, statusEl) {
  const pollMs = 2000;
  const maxMs = 7200000;
  const t0 = Date.now();
  while (Date.now() - t0 < maxMs) {
    await new Promise(function (resolve) {
      setTimeout(resolve, pollMs);
    });
    const pr = await fetch(pollUrl, { cache: 'no-store' });
    const data = await pr.json().catch(() => ({}));
    if (data.status === 'running') {
      if (statusEl) {
        statusEl.textContent =
          'Job running (' + (data.phase || '…') + ') — scene anchors can take several minutes per segment.';
      }
      continue;
    }
    return data;
  }
  return { ok: false, error: 'timeout', message: 'Timed out waiting for job.' };
}

async function postAnchorPreviewJob(path, journalDate, statusEl, extra) {
  const jd = journalDate || getPickerJournalDate();
  if (!jd) {
    if (statusEl) statusEl.textContent = 'Pick a journal date first.';
    return null;
  }
  const body = Object.assign({ journal_date: jd }, extra || {});
  if (statusEl) statusEl.textContent = 'Starting…';
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const startData = await r.json().catch(() => ({}));
  if (!r.ok || !startData.ok) {
    if (statusEl) {
      statusEl.textContent =
        'Failed: ' + (startData.error || startData.message || r.statusText);
    }
    return startData;
  }
  if (startData.async && startData.job_id) {
    const pollUrl =
      startData.poll_url ||
      '/api/anchor-preview/job-status?job_id=' + encodeURIComponent(startData.job_id);
    const final = await pollAnchorPreviewJob(pollUrl, statusEl);
    applyAnchorPreviewJobResult(final, jd);
    await loadAnchorPreviewVideo(jd);
    return final;
  }
  applyAnchorPreviewJobResult(startData, jd);
  await loadAnchorPreviewVideo(jd);
  return startData;
}

async function onBuildMissingAnchors() {
  const forceEl = document.getElementById('anchor-preview-force');
  const force = forceEl && forceEl.checked;
  const st = document.getElementById('anchor-preview-job-status');
  await postAnchorPreviewJob('/api/anchor-preview/build-anchors', getPickerJournalDate(), st, {
    force: force,
    skip_existing: !force,
  });
}

async function onBuildAllAnchors() {
  const st = document.getElementById('anchor-preview-job-status');
  await postAnchorPreviewJob('/api/anchor-preview/build-anchors', getPickerJournalDate(), st, {
    force: true,
  });
}

async function onAssembleAnchorPreview() {
  const forceEl = document.getElementById('anchor-preview-force');
  const force = forceEl && forceEl.checked;
  const st = document.getElementById('anchor-preview-job-status');
  await postAnchorPreviewJob(
    '/api/anchor-preview/assemble',
    getPickerJournalDate(),
    st,
    { force: force, skip_existing: !force }
  );
}

async function onGenerateVideoReuseAnchors() {
  const fb = document.getElementById('anchor-preview-handoff-feedback');
  const btn = document.getElementById('btn-generate-video-reuse-anchors');
  const jd = getPickerJournalDate();
  if (!jd) {
    if (fb) fb.textContent = 'Pick a journal date first.';
    return;
  }
  if (fb) fb.textContent = 'Starting full video (reuse anchors)…';
  if (btn) btn.disabled = true;
  try {
    const r = await fetch('/api/generate-video-reuse-anchors', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ journal_date: jd, assemble_after: true }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) {
      if (fb) {
        fb.textContent =
          'Failed: ' + (data.error || data.message || r.statusText);
      }
      return;
    }
    if (fb) {
      fb.textContent = data.cancelled
        ? 'Cancelled.'
        : 'Done — check command output below; final video on Video tab.';
    }
    const out = document.getElementById('out');
    if (out && data.command) {
      const prev = out.textContent || '';
      out.textContent =
        prev +
        (prev.endsWith('\n') ? '' : '\n') +
        '$ ' +
        (Array.isArray(data.command) ? data.command.join(' ') : data.command) +
        '\n' +
        (data.stderr || data.stdout || '') +
        '\n';
    }
  } catch (e) {
    if (fb) fb.textContent = 'Error: ' + e;
  } finally {
    if (btn) btn.disabled = false;
  }
}

function wireAnchorPreviewPanel() {
  const map = [
    ['btn-anchor-build-missing', onBuildMissingAnchors],
    ['btn-anchor-build-all', onBuildAllAnchors],
    ['btn-anchor-assemble', onAssembleAnchorPreview],
    ['btn-generate-video-reuse-anchors', onGenerateVideoReuseAnchors],
  ];
  map.forEach(function (pair) {
    const btn = document.getElementById(pair[0]);
    if (!btn || btn.dataset.anchorWired) return;
    btn.dataset.anchorWired = '1';
    btn.addEventListener('click', function () {
      pair[1]().catch(function (e) {
        const st = document.getElementById('anchor-preview-job-status');
        if (st) st.textContent = String(e);
      });
    });
  });
}

function refreshAnchorPreviewPanel(journalDate) {
  if (typeof wireGenerateTtsButtons === 'function') wireGenerateTtsButtons();
  wireAnchorPreviewPanel();
  return loadAnchorPreviewVideo(journalDate);
}
