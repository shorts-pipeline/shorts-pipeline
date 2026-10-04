// video.js — video preview cues, B-roll add from preview, YouTube upload, modal helpers.

function formatExpeditionJournalDate(iso) {
  if (!iso || !/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso || '';
  const dt = new Date(iso + 'T12:00:00Z');
  if (Number.isNaN(dt.getTime())) return iso;
  return dt.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    timeZone: 'UTC'
  });
}

async function refreshYoutubeUploadStatus() {
  const el = document.getElementById('youtube-upload-status-body');
  if (!el) return;
  el.textContent = 'Loading upload status…';
  try {
    const r = await fetch('/api/youtube/upload-status', { cache: 'no-store' });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) {
      el.textContent =
        'Could not load upload status' + (data.error ? ': ' + data.error : '.');
      return;
    }
    const lines = [];
    if (data.last_journal_date) {
      const pretty = formatExpeditionJournalDate(data.last_journal_date);
      lines.push(
        'Last uploaded journal date: ' +
          pretty +
          ' (' +
          data.last_journal_date +
          ')'
      );
      if (data.uploaded_count > 1) {
        lines.push(
          data.uploaded_count +
            ' expedition days on YouTube (manifests with youtube_video_id).'
        );
      }
    } else {
      lines.push(
        'No YouTube uploads recorded yet (no youtube_video_id in output/ or archive manifests).'
      );
    }
    if (data.pending_count > 0) {
      const oldestIso = data.oldest_pending_journal_date;
      const oldest =
        oldestIso != null
          ? formatExpeditionJournalDate(oldestIso) + ' (' + oldestIso + ')'
          : '—';
      lines.push(
        'Ready to upload: ' +
          data.pending_count +
          ' assembled video(s) in output/ (oldest backlog date: ' +
          oldest +
          ').'
      );
    } else {
      lines.push(
        'No assembled backlog in output/ (no manifest without youtube_video_id).'
      );
    }
    if (data.scan_archive) {
      lines.push(
        'Upload history includes archive/*/output; backlog counts are output/ only.'
      );
    }
    el.textContent = lines.join('\n');
  } catch (e) {
    el.textContent = 'Failed to load upload status: ' + e;
  }
}

function formatVideoCuePartBody(p) {
  if (!p) return '';
  if (p.kind === 'intro') {
    return '(Intro slot — map/date opening if your build includes it. No narration segment fields.)';
  }
  const lines = [];
  const vm = p.visual_mode || 'b_roll';
  lines.push('visual_mode: ' + vm);
  if (p.talking_head_subject) {
    lines.push('talking_head_subject: ' + p.talking_head_subject);
  }
  if (p.reference_character_id) {
    lines.push('reference_character_id: ' + p.reference_character_id);
  }
  const dlg = p.dialogue;
  if (dlg && dlg.length) {
    lines.push('');
    lines.push('dialogue[]:');
    dlg.forEach(function (line) {
      const sp = line.speaker_id || '?';
      const tx = line.preview != null && line.preview !== '' ? line.preview : line.text || '';
      lines.push('  ' + sp + ': ' + tx);
    });
  } else if (p.narration_preview && String(p.narration_preview).trim()) {
    lines.push('');
    lines.push('narration: ' + String(p.narration_preview).trim());
  }
  const thp = p.talking_head_prompt != null ? String(p.talking_head_prompt).trim() : '';
  const of = p.opening_frame != null ? String(p.opening_frame).trim() : '';
  const isTh = (p.visual_mode || '').trim() === 'talking_head';
  lines.push('');
  if (isTh) {
    if (thp) {
      lines.push('talking_head_prompt:');
      lines.push(thp);
    } else {
      lines.push('(No talking_head_prompt for this segment.)');
    }
  } else {
    if (thp) {
      lines.push('talking_head_prompt: ' + thp);
      lines.push('');
    }
    const vp = p.video_prompt != null ? String(p.video_prompt).trim() : '';
    if (vp) {
      lines.push('video_prompt:');
      lines.push(vp);
    } else {
      lines.push('(No video_prompt in narration JSON for this timeline slot.)');
    }
  }
  if (of) {
    lines.push('');
    lines.push('opening_frame:');
    lines.push(of);
  }
  return lines.join('\n');
}

function formatVideoCuePartText(p, cues) {
  if (!p) return '';
  const idxPart =
    p.narrative_segment_index != null
      ? ' (narrative segment ' + p.narrative_segment_index + ')'
      : '';
  const header =
    p.label +
    idxPart +
    '  ·  ' +
    p.start_sec.toFixed(2) +
    's – ' +
    p.end_sec.toFixed(2) +
    's';
  let prefix = '';
  if (cues && cues.ok && p.kind !== 'intro') {
    if (cues.dialogue_mode || cues.long_conversation_mode) {
      prefix += 'Episode pack: ' + (cues.prompt_pack_hint || 'lewis_clark');
      if (cues.focus_topic) prefix += ' · focus: ' + cues.focus_topic;
      prefix += '\n';
    }
    if (cues.week_arc_active) {
      prefix +=
        'Week arc: week_' +
        (cues.week_arc_week_id || '?') +
        (cues.week_arc_role ? ' (' + cues.week_arc_role + ')' : '') +
        '\n';
    }
  }
  return prefix + header + '\n\n' + formatVideoCuePartBody(p);
}

function getCurrentVideoCuePart() {
  const vid = document.getElementById('modal-video');
  const cues = window.__videoCues;
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

function syncVideoCueTextarea() {
  const ta = document.getElementById('modal-video-cues');
  const cues = window.__videoCues;
  if (!ta || !cues || !cues.ok || !cues.parts || !cues.parts.length) return;
  const p = getCurrentVideoCuePart();
  if (!p) return;
  ta.value = formatVideoCuePartText(p, cues);
}

function updateBrollAddButtonState() {
  const btn = document.getElementById('btn-broll-add');
  const vid = document.getElementById('modal-video');
  if (!btn || !vid) return;
  const part = getCurrentVideoCuePart();
  const seg = part && part.narrative_segment_index;
  const narrative =
    part &&
    part.kind !== 'intro' &&
    seg != null &&
    seg !== '' &&
    Number.isFinite(Number(seg));
  const armed = Boolean(vid.paused && narrative);
  btn.disabled = !armed;
  btn.classList.toggle('broll-add-armed', armed);
}

function updateInlineBrollAddButtonState() {
  const btn = document.getElementById('btn-inline-broll-add');
  const vid = document.getElementById('inline-video');
  if (!btn || !vid) return;
  const cues = window.__inlineVideoCues;
  const part = getInlineVideoCuePart();
  const seg = part && part.narrative_segment_index;
  const narrative =
    part &&
    part.kind !== 'intro' &&
    seg != null &&
    seg !== '' &&
    Number.isFinite(Number(seg));
  const armed = Boolean(
    cues && cues.ok && cues.date_id && vid.paused && narrative
  );
  btn.disabled = !armed;
  btn.classList.toggle('broll-add-armed', armed);
}

function videoPreviewSync() {
  syncVideoCueTextarea();
  updateBrollAddButtonState();
  updateYoutubeUploadButtonState();
}

function updateYoutubeUploadButtonState() {
  const btn = document.getElementById('btn-youtube-upload');
  const link = document.getElementById('youtube-upload-existing');
  const cues = window.__videoCues;
  if (!btn) return;
  if (!cues || !cues.ok) {
    btn.disabled = true;
    btn.classList.remove('yt-upload-armed');
    btn.textContent = 'Upload to YouTube';
    btn.title = '';
    if (link) link.style.display = 'none';
    return;
  }
  const hasManifest = Boolean(cues.manifest_exists);
  const watched = Boolean(window.__videoPreviewWatchedToEnd);
  const existing = cues.youtube_url && String(cues.youtube_url).trim();
  if (link) {
    if (existing) {
      link.href = existing;
      link.style.display = 'inline';
    } else {
      link.style.display = 'none';
    }
  }
  if (existing) {
    btn.disabled = true;
    btn.classList.remove('yt-upload-armed');
    btn.textContent = 'Already on YouTube';
    btn.title = 'Manifest already has youtube_url. Re-upload from the CLI if you replaced the video.';
    return;
  }
  btn.textContent = 'Upload to YouTube';
  const canGo = hasManifest && watched;
  btn.disabled = !canGo;
  btn.classList.toggle('yt-upload-armed', canGo);
  if (!hasManifest) {
    btn.title = 'After assembly, output/…/*.manifest.json must exist beside the MP4.';
  } else if (!watched) {
    btn.title = 'Watch this preview to the end once to enable upload.';
  } else {
    btn.title = 'Runs youtube_upload.py with --manifest (same metadata as run-daily --upload).';
  }
}

function videoPreviewOnEnded() {
  window.__videoPreviewWatchedToEnd = true;
  updateYoutubeUploadButtonState();
}

function updateInlineYoutubeUploadButtonState() {
  const btn = document.getElementById('btn-inline-youtube-upload');
  const link = document.getElementById('inline-youtube-upload-existing');
  const cues = window.__inlineVideoCues;
  if (!btn) return;
  if (!cues || !cues.ok) {
    btn.disabled = true;
    btn.classList.remove('yt-upload-armed');
    btn.textContent = 'Upload to YouTube';
    btn.title = '';
    if (link) link.style.display = 'none';
    return;
  }
  const hasManifest = Boolean(cues.manifest_exists);
  const watched = Boolean(window.__inlineVideoPreviewWatchedToEnd);
  const existing = cues.youtube_url && String(cues.youtube_url).trim();
  if (link) {
    if (existing) {
      link.href = existing;
      link.style.display = 'inline';
    } else {
      link.style.display = 'none';
    }
  }
  if (existing) {
    btn.disabled = true;
    btn.classList.remove('yt-upload-armed');
    btn.textContent = 'Already on YouTube';
    btn.title = 'Manifest already has youtube_url. Re-upload from the CLI if you replaced the video.';
    return;
  }
  btn.textContent = 'Upload to YouTube';
  const canGo = hasManifest && watched;
  btn.disabled = !canGo;
  btn.classList.toggle('yt-upload-armed', canGo);
  if (!hasManifest) {
    btn.title = 'After assembly, output/…/*.manifest.json must exist beside the MP4.';
  } else if (!watched) {
    btn.title = 'Watch this preview to the end once to enable upload.';
  } else {
    btn.title = 'Runs youtube_upload.py with --manifest for the selected journal date (public).';
  }
}

function inlineVideoPreviewOnEnded() {
  window.__inlineVideoPreviewWatchedToEnd = true;
  updateInlineYoutubeUploadButtonState();
}

async function assembleFinalVideo() {
  const btn = document.getElementById('btn-assemble-final-video');
  const fb = document.getElementById('assemble-final-feedback');
  const jd = getPickerJournalDate();
  if (!jd) {
    if (fb) {
      fb.textContent = 'Pick a journal date first.';
      fb.style.color = 'var(--err)';
    }
    return;
  }
  const ambientEl = document.getElementById('ambient');
  const ambient = ambientEl && ambientEl.checked;
  if (fb) {
    fb.textContent = 'Assembling…';
    fb.style.color = '';
  }
  if (btn) btn.disabled = true;
  try {
    const r = await fetch('/api/assemble-video', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ journal_date: jd, ambient: ambient, wide_screen: false }),
    });
    const raw = await r.text();
    let data = {};
    if (raw) {
      try {
        data = JSON.parse(raw);
      } catch (_) {
        /* plain-text 404 etc. */
      }
    }
    if (r.status === 409 || data.error === 'run_in_progress') {
      if (fb) {
        fb.textContent = data.message || 'Another pipeline run is already in progress.';
        fb.style.color = 'var(--err)';
      }
      return;
    }
    if (!r.ok || !data.ok) {
      let err = data.error || data.message;
      if (!err && r.status === 404) {
        err =
          'HTTP 404 — restart the Pipeline UI server so it loads POST /api/assemble-video.';
      }
      if (!err) err = r.statusText || String(r.status);
      if (fb) {
        fb.textContent = 'Failed: ' + err;
        fb.style.color = 'var(--err)';
      }
      return;
    }
    if (fb) {
      fb.textContent = data.cancelled ? 'Cancelled.' : 'Done.';
      fb.style.color = data.cancelled ? '' : 'var(--ok)';
    }
    if (!data.cancelled && typeof loadInlineVideoPreview === 'function') {
      await loadInlineVideoPreview(jd);
    }
  } catch (e) {
    if (fb) {
      fb.textContent = 'Error: ' + e;
      fb.style.color = 'var(--err)';
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function onInlineYoutubeUploadFromPreview() {
  const btn = document.getElementById('btn-inline-youtube-upload');
  const fb = document.getElementById('inline-youtube-upload-feedback');
  const cues = window.__inlineVideoCues;
  const jd = getPickerJournalDate();
  if (!btn || btn.disabled || !cues || !cues.ok || !cues.manifest_exists || !jd) return;
  if (fb) {
    fb.textContent = 'Uploading (this can take several minutes)…';
    fb.style.color = '';
  }
  btn.disabled = true;
  btn.classList.remove('yt-upload-armed');
  try {
    const r = await fetch('/api/youtube/upload-from-preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ privacy: 'public', journal_date: jd }),
    });
    let data;
    try {
      data = await r.json();
    } catch {
      if (fb) {
        fb.textContent = 'Invalid JSON response (' + r.status + ')';
        fb.style.color = 'var(--err)';
      }
      updateInlineYoutubeUploadButtonState();
      return;
    }
    if (data.ok && data.youtube_url) {
      if (window.__inlineVideoCues) window.__inlineVideoCues.youtube_url = data.youtube_url;
      if (fb) {
        fb.textContent = data.message || 'Uploaded.';
        fb.style.color = 'var(--ok)';
      }
      refreshYoutubeUploadStatus().catch(() => {});
    } else if (fb) {
      fb.textContent = data.message || data.error || 'Upload failed';
      fb.style.color = 'var(--err)';
    }
    updateInlineYoutubeUploadButtonState();
    window.setTimeout(() => {
      const f2 = document.getElementById('inline-youtube-upload-feedback');
      if (f2 && data && data.ok) {
        f2.textContent = '';
        f2.style.color = '';
      }
    }, 8000);
  } catch (e) {
    if (fb) {
      fb.textContent = 'Error: ' + e;
      fb.style.color = 'var(--err)';
    }
    updateInlineYoutubeUploadButtonState();
  }
}

async function onYoutubeUploadFromPreview() {
  const btn = document.getElementById('btn-youtube-upload');
  const fb = document.getElementById('youtube-upload-feedback');
  const cues = window.__videoCues;
  if (!btn || btn.disabled || !cues || !cues.ok || !cues.manifest_exists) return;
  if (fb) {
    fb.textContent = 'Uploading (this can take several minutes)…';
    fb.style.color = '';
  }
  btn.disabled = true;
  btn.classList.remove('yt-upload-armed');
  try {
    const r = await fetch('/api/youtube/upload-from-preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ privacy: 'public' }),
    });
    let data;
    try {
      data = await r.json();
    } catch {
      if (fb) {
        fb.textContent = 'Invalid JSON response (' + r.status + ')';
        fb.style.color = 'var(--err)';
      }
      updateYoutubeUploadButtonState();
      return;
    }
    if (data.ok && data.youtube_url) {
      if (window.__videoCues) window.__videoCues.youtube_url = data.youtube_url;
      if (fb) {
        fb.textContent = data.message || 'Uploaded.';
        fb.style.color = 'var(--ok)';
      }
      refreshYoutubeUploadStatus().catch(() => {});
    } else if (fb) {
      fb.textContent = data.message || data.error || 'Upload failed';
      fb.style.color = 'var(--err)';
    }
    updateYoutubeUploadButtonState();
    window.setTimeout(() => {
      const f2 = document.getElementById('youtube-upload-feedback');
      if (f2 && data && data.ok) {
        f2.textContent = '';
        f2.style.color = '';
      }
    }, 8000);
  } catch (e) {
    if (fb) {
      fb.textContent = 'Error: ' + e;
      fb.style.color = 'var(--err)';
    }
    updateYoutubeUploadButtonState();
  }
}

async function onBrollAddFromPreview(useInline) {
  const btn = document.getElementById(useInline ? 'btn-inline-broll-add' : 'btn-broll-add');
  const fb = document.getElementById(useInline ? 'inline-broll-add-feedback' : 'broll-add-feedback');
  const cues = useInline ? window.__inlineVideoCues : window.__videoCues;
  const part = useInline ? getInlineVideoCuePart() : getCurrentVideoCuePart();
  if (!btn || btn.disabled || !cues || !cues.ok || !cues.date_id) return;
  if (!part || part.kind === 'intro' || part.narrative_segment_index == null) return;
  const seg = Number(part.narrative_segment_index);
  if (!Number.isFinite(seg)) return;
  if (fb) {
    fb.textContent = 'Saving…';
    fb.style.color = '';
  }
  try {
    const r = await fetch('/api/b-roll/add-from-preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date_id: String(cues.date_id), segment_index: seg }),
    });
    let data;
    try {
      data = await r.json();
    } catch {
      if (fb) {
        fb.textContent = 'Invalid JSON response (' + r.status + ')';
        fb.style.color = 'var(--err)';
      }
      return;
    }
    if (data.ok) {
      if (fb) {
        fb.textContent = data.message || (data.already_exists ? 'Already in library.' : 'Added.');
        fb.style.color = data.already_exists ? 'var(--muted)' : 'var(--ok)';
      }
    } else if (fb) {
      fb.textContent = data.message || data.error || 'Request failed';
      fb.style.color = 'var(--err)';
    }
    window.setTimeout(() => {
      if (fb) {
        fb.textContent = '';
        fb.style.color = '';
      }
    }, 5000);
  } catch (e) {
    if (fb) {
      fb.textContent = 'Error: ' + e;
      fb.style.color = 'var(--err)';
    }
  }
}

function closeModal() {
  const backdrop = document.getElementById('modal-backdrop');
  const vid = document.getElementById('modal-video');
  vid.removeEventListener('timeupdate', videoPreviewSync);
  vid.removeEventListener('seeked', videoPreviewSync);
  vid.removeEventListener('pause', videoPreviewSync);
  vid.removeEventListener('playing', videoPreviewSync);
  vid.removeEventListener('loadedmetadata', videoPreviewSync);
  vid.removeEventListener('ended', videoPreviewOnEnded);
  vid.pause();
  vid.removeAttribute('src');
  vid.load();
  window.__videoCues = null;
  window.__videoPreviewWatchedToEnd = false;
  const brollBtn = document.getElementById('btn-broll-add');
  const brollFb = document.getElementById('broll-add-feedback');
  if (brollBtn) {
    brollBtn.disabled = true;
    brollBtn.classList.remove('broll-add-armed');
  }
  if (brollFb) {
    brollFb.textContent = '';
    brollFb.style.color = '';
  }
  const ytBtn = document.getElementById('btn-youtube-upload');
  const ytFb = document.getElementById('youtube-upload-feedback');
  const ytLink = document.getElementById('youtube-upload-existing');
  if (ytBtn) {
    ytBtn.disabled = true;
    ytBtn.classList.remove('yt-upload-armed');
    ytBtn.textContent = 'Upload to YouTube';
    ytBtn.title = '';
  }
  if (ytFb) {
    ytFb.textContent = '';
    ytFb.style.color = '';
  }
  if (ytLink) {
    ytLink.style.display = 'none';
    ytLink.removeAttribute('href');
  }
  backdrop.classList.remove('open');
  backdrop.setAttribute('aria-hidden', 'true');
}

function openModal() {
  const backdrop = document.getElementById('modal-backdrop');
  backdrop.classList.add('open');
  backdrop.setAttribute('aria-hidden', 'false');
}
