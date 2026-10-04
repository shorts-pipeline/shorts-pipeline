// broll.js — B-roll suggestions table, prompt editor, anchor scene, thumbnail lightbox.

let __brollPayload = null;

let __brollPromptEditCtx = null;

function brollVideoPromptPreview(full) {
  const s = full != null ? String(full) : '';
  const t = s.trim();
  if (!t) return '(none)';
  if (t.length <= 240) return t;
  return t.slice(0, 240) + '…';
}

function brollNarrationPreview(full) {
  const s = full != null ? String(full) : '';
  const t = s.trim();
  if (!t) return '(none)';
  if (t.length <= 200) return t;
  return t.slice(0, 200) + '…';
}

/** Literal DELETEME removes the segment on save (Produce / Shots editors). */
function narrationTextRequestsSegmentDelete(text) {
  return String(text != null ? text : '').trim().toUpperCase() === 'DELETEME';
}

function brollPromptPreview(full) {
  return brollVideoPromptPreview(full);
}

function openBrollPromptEditor(si, fullText, cellDiv, field) {
  field = field || 'video_prompt';
  const jd = getPickerJournalDate();
  if (!jd) {
    window.alert('Choose a journal date in the picker above.');
    return;
  }
  const full = fullText != null ? String(fullText) : '';
  __brollPromptEditCtx = {
    si: si,
    jd: jd,
    initialFull: full,
    cellDiv: cellDiv,
    field: field,
  };
  const ta = document.getElementById('broll-prompt-editor-ta');
  const st = document.getElementById('broll-prompt-editor-status');
  if (ta) ta.value = full;
  if (ta) {
    const aria =
      field === 'narration'
        ? 'Narration text'
        : field === 'talking_head_prompt'
          ? 'Talking-head prompt text'
          : field === 'opening_frame'
            ? 'Opening frame text'
            : 'Video prompt text';
    ta.setAttribute('aria-label', aria);
  }
  if (st) {
    st.style.display = 'none';
    st.textContent = '';
    st.className = 'status';
  }
  const title = document.getElementById('broll-prompt-editor-title');
  if (title) {
    title.textContent =
      field === 'narration'
        ? 'Edit narration — segment ' + si
        : field === 'talking_head_prompt'
          ? 'Edit talking-head prompt — segment ' + si
          : field === 'opening_frame'
            ? 'Edit opening frame — segment ' + si
            : 'Edit video prompt — segment ' + si;
  }
  const deleteHint = document.getElementById('broll-prompt-delete-hint');
  if (deleteHint) {
    deleteHint.style.display =
      field === 'narration' || field === 'video_prompt' || field === 'talking_head_prompt'
        ? 'block'
        : 'none';
  }
  const ofHint = document.getElementById('broll-prompt-opening-frame-hint');
  if (ofHint) {
    ofHint.style.display = field === 'opening_frame' ? 'block' : 'none';
  }
  const bd = document.getElementById('broll-prompt-backdrop');
  if (bd) {
    bd.classList.add('open');
    bd.setAttribute('aria-hidden', 'false');
  }
  if (ta) ta.focus();
}

async function closeBrollPromptEditor() {
  const bd = document.getElementById('broll-prompt-backdrop');
  const ta = document.getElementById('broll-prompt-editor-ta');
  const st = document.getElementById('broll-prompt-editor-status');
  const ctx = __brollPromptEditCtx;
  if (!ctx) {
    if (bd) {
      bd.classList.remove('open');
      bd.setAttribute('aria-hidden', 'true');
    }
    if (ta) ta.value = '';
    return;
  }
  const newText = ta ? ta.value : '';
  const field = ctx.field || 'video_prompt';
  if (newText !== ctx.initialFull) {
    const willDelete =
      (field === 'narration' || field === 'video_prompt' || field === 'talking_head_prompt') &&
      narrationTextRequestsSegmentDelete(newText);
    if (willDelete) {
      const msg =
        'Remove segment ' + ctx.si + ' from narration JSON? (Text is DELETEME.)';
      if (!window.confirm(msg)) {
        return;
      }
    }
    if (st) {
      st.style.display = 'block';
      st.className = 'status';
      st.textContent = willDelete ? 'Removing segment…' : 'Saving…';
    }
    try {
      const url =
        field === 'narration'
          ? '/api/narration/update-narration'
          : field === 'talking_head_prompt'
            ? '/api/narration/update-talking-head-prompt'
            : field === 'opening_frame'
              ? '/api/narration/update-opening-frame'
              : '/api/narration/update-video-prompt';
      const payload =
        field === 'narration'
          ? {
              journal_date: ctx.jd,
              segment_index: ctx.si,
              narration: newText,
            }
          : field === 'talking_head_prompt'
            ? {
                journal_date: ctx.jd,
                segment_index: ctx.si,
                talking_head_prompt: newText,
              }
            : field === 'opening_frame'
              ? {
                  journal_date: ctx.jd,
                  segment_index: ctx.si,
                  opening_frame: newText,
                }
              : {
                  journal_date: ctx.jd,
                  segment_index: ctx.si,
                  video_prompt: newText,
                };
      const r = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.ok) {
        if (st) {
          st.className = 'status err';
          st.textContent = data.message || data.error || ('HTTP ' + r.status);
        }
        return;
      }
      if (data.deleted) {
        refreshNarrationInline(ctx.jd).catch(() => {});
        if (typeof loadProduceSegments === 'function') {
          loadProduceSegments(ctx.jd).catch(() => {});
        }
        if (typeof loadBrollSuggestions === 'function') {
          loadBrollSuggestions().catch(() => {});
        }
        __brollPromptEditCtx = null;
        if (bd) {
          bd.classList.remove('open');
          bd.setAttribute('aria-hidden', 'true');
        }
        if (ta) ta.value = '';
        return;
      }
      const segs = __brollPayload && __brollPayload.segments ? __brollPayload.segments : [];
      const seg = segs.find(function (s) {
        return s.segment_index === ctx.si;
      });
      if (seg) {
        if (field === 'narration') {
          seg.narration_full = newText;
          seg.narration_preview = brollNarrationPreview(newText);
        } else if (field === 'talking_head_prompt') {
          seg.talking_head_prompt_full = newText;
          seg.talking_head_prompt_preview = brollPromptPreview(newText);
        } else if (field === 'opening_frame') {
          seg.opening_frame_full = newText;
          seg.opening_frame_preview = brollPromptPreview(newText);
        } else {
          seg.video_prompt_full = newText;
          seg.video_prompt_preview = brollVideoPromptPreview(newText);
        }
      }
      if (ctx.cellDiv) {
        if (field === 'narration') {
          ctx.cellDiv.textContent = brollNarrationPreview(newText);
          ctx.cellDiv.title = newText;
        } else if (field === 'talking_head_prompt') {
          ctx.cellDiv.textContent = brollPromptPreview(newText);
          ctx.cellDiv.title = newText;
        } else if (field === 'opening_frame') {
          ctx.cellDiv.textContent = brollPromptPreview(newText) || '(none)';
          ctx.cellDiv.title = newText;
        } else {
          ctx.cellDiv.textContent = brollVideoPromptPreview(newText);
          ctx.cellDiv.title = newText;
        }
      }
      refreshNarrationInline(ctx.jd).catch(() => {});
      if (typeof loadProduceSegments === 'function') {
        loadProduceSegments(ctx.jd).catch(() => {});
      }
    } catch (e) {
      if (st) {
        st.className = 'status err';
        st.textContent = String(e);
      }
      return;
    }
  }
  __brollPromptEditCtx = null;
  if (bd) {
    bd.classList.remove('open');
    bd.setAttribute('aria-hidden', 'true');
  }
  if (ta) ta.value = '';
}

let __anchorSceneZoom = 1;

async function brollRegenerateSegment(si, btn, statusEl, curRel, curImg) {
  const jd = getPickerJournalDate();
  if (!jd) {
    if (statusEl) {
      statusEl.textContent = 'Pick a journal date first.';
      statusEl.className = 'broll-regen-status err';
    }
    return;
  }
  const vendorEl = document.getElementById('vendor');
  const vendor = vendorEl && vendorEl.value ? String(vendorEl.value).trim().toLowerCase() : 'fal';
  if (vendor === 'sora') {
    window.alert('Per-segment regen uses fal or google. Change Video vendor in the form above (not Sora).');
    return;
  }
  if (statusEl) {
    statusEl.className = 'broll-regen-status';
    statusEl.textContent = 'Running segment regen (run-daily)…';
  }
  if (btn) btn.disabled = true;
  try {
    const r = await fetch('/api/regenerate-video-segment', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ journal_date: jd, segment_index: si, vendor: vendor }),
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
      if (statusEl) {
        statusEl.className = 'broll-regen-status err';
        statusEl.textContent = data.message || 'Another pipeline run is already in progress.';
      }
      return;
    }
    if (!r.ok || !data.ok) {
      let err = data.error || data.message;
      if (!err && r.status === 404) {
        err =
          'HTTP 404 (no JSON) — restart the Pipeline UI server so it loads POST /api/regenerate-video-segment.';
      }
      if (!err) err = r.statusText || String(r.status);
      if (statusEl) {
        statusEl.className = 'broll-regen-status err';
        statusEl.textContent = 'Failed: ' + err + (data.stderr ? '\n' + String(data.stderr).slice(0, 400) : '');
      }
      return;
    }
    if (statusEl) {
      statusEl.className = 'broll-regen-status';
      statusEl.textContent = data.cancelled ? 'Cancelled.' : 'Done.';
    }
    // Regen may create the segment MP4 even when it didn't exist before.
    // Reload the B-roll suggestions so the "current clip" choice + thumbnail appear.
    if (jd && !data.cancelled) {
      try {
        await loadBrollSuggestions();
      } catch (_) {
        // If reload fails, at least the regen action itself succeeded.
      }
      if (typeof loadInlineVideoPreview === 'function') {
        try {
          await loadInlineVideoPreview(jd);
        } catch (_) {
          /* preview refresh optional */
        }
      }
    }
  } catch (e) {
    if (statusEl) {
      statusEl.className = 'broll-regen-status err';
      statusEl.textContent = String(e);
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

function refreshBrollAnchorThumb(imgEl, jd, si) {
  if (!imgEl || !jd) return;
  imgEl.style.display = 'none';
  const u = '/api/preview/anchor-image?journal_date=' + encodeURIComponent(jd) + '&segment=' + encodeURIComponent(String(si)) + '&_=' + Date.now();
  imgEl.onload = function () {
    imgEl.style.display = 'block';
  };
  imgEl.onerror = function () {
    imgEl.style.display = 'none';
  };
  imgEl.src = u;
}

function refreshBrollAnchorThumbsInRun(jd, segmentIndices) {
  if (!jd || !segmentIndices || !segmentIndices.length) return;
  segmentIndices.forEach(function (runSi) {
    const row = document.querySelector('#broll-tbody tr[data-segment-index="' + runSi + '"]');
    const img = row && row.querySelector('.broll-anchor-thumb');
    if (img) refreshBrollAnchorThumb(img, jd, runSi);
  });
}

async function runBrollSceneAnchorI2i(si, btn, statusEl, imgEl) {
  const jd = getPickerJournalDate();
  if (!jd) {
    if (statusEl) {
      statusEl.textContent = 'Pick a journal date first.';
      statusEl.className = 'broll-anchor-status err';
    }
    return;
  }
  const aspect_ratio = '9:16';
  let convRunSegs = null;
  if (__brollPayload && Array.isArray(__brollPayload.segments)) {
    const row = __brollPayload.segments.find(function (s) {
      return s && s.segment_index === si;
    });
    if (row && row.conversation_run_segments && row.conversation_run_segments.length) {
      convRunSegs = row.conversation_run_segments;
    }
  }
  if (statusEl) {
    statusEl.className = 'broll-anchor-status';
    statusEl.textContent =
      convRunSegs && convRunSegs.length > 1
        ? 'Starting shared backdrop for segments ' + convRunSegs.join(', ') + '…'
        : 'Starting FAL scene-anchor i2i…';
  }
  if (btn) btn.disabled = true;
  try {
    const r = await fetch('/api/fal/scene-anchor-i2i', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ journal_date: jd, segment_index: si, aspect_ratio: aspect_ratio }),
    });
    const startData = await r.json().catch(() => ({}));
    if (!r.ok || !startData.ok) {
      const err = startData.error || startData.message || r.statusText;
      if (statusEl) {
        statusEl.className = 'broll-anchor-status err';
        statusEl.textContent = 'Failed: ' + err + (startData.message ? ' — ' + startData.message : '');
      }
      return;
    }

    let data = startData;
    const jobId = startData.job_id;
    if (jobId && startData.async) {
      const pollUrl = startData.poll_url || ('/api/fal/scene-anchor-i2i/status?job_id=' + encodeURIComponent(jobId));
      const pollMs = 2000;
      const maxMs = 600000;
      const t0 = Date.now();
      while (Date.now() - t0 < maxMs) {
        await new Promise(function (resolve) {
          setTimeout(resolve, pollMs);
        });
        const pr = await fetch(pollUrl, { cache: 'no-store' });
        data = await pr.json().catch(() => ({}));
        if (data.status === 'running') {
          if (statusEl) {
            statusEl.textContent =
              convRunSegs && convRunSegs.length > 1
                ? 'Shared backdrop i2i running for segments ' + convRunSegs.join(', ') + '…'
                : 'Scene anchor i2i running… (usually 1–2 min)';
          }
          continue;
        }
        break;
      }
      if (data.status === 'running') {
        if (statusEl) {
          statusEl.className = 'broll-anchor-status err';
          statusEl.textContent = 'Timed out waiting for scene anchor (10 min). Check server logs and try again.';
        }
        return;
      }
    }

    if (!data.ok) {
      const err = data.error || data.message || 'scene anchor failed';
      if (statusEl) {
        statusEl.className = 'broll-anchor-status err';
        statusEl.textContent = 'Failed: ' + err + (data.message ? ' — ' + data.message : '');
      }
      return;
    }
    if (statusEl) {
      statusEl.className = 'broll-anchor-status';
      if (data.conversation_anchor_run && data.conversation_refresh_segments && data.conversation_refresh_segments.length > 1) {
        statusEl.textContent =
          'Shared backdrop: master + anchors saved for segments ' +
          data.conversation_refresh_segments.join(', ') +
          (data.relative_path ? ' (' + data.relative_path + ')' : '') +
          '. Thumbnails updated for the whole run.';
      } else if (data.relative_path) {
        statusEl.textContent =
          'Anchor saved. Click Regenerate on this row, then Assemble final video (Video tab).';
      } else {
        statusEl.textContent = 'OK — Regenerate this segment, then Assemble final video.';
      }
    }
    const refreshSegs =
      data.conversation_refresh_segments && data.conversation_refresh_segments.length
        ? data.conversation_refresh_segments
        : [si];
    refreshBrollAnchorThumbsInRun(jd, refreshSegs);
    if (typeof loadProduceSegments === 'function') {
      try {
        await loadProduceSegments(jd);
      } catch (_) {
        /* optional sync Script & audio table */
      }
    }
  } catch (e) {
    if (statusEl) {
      statusEl.className = 'broll-anchor-status err';
      const msg = String(e);
      if (/failed to fetch/i.test(msg)) {
        statusEl.textContent =
          'Network error (Failed to fetch). Confirm http://127.0.0.1:8765/ is open. If the server restarted, try Scene anchor again.';
      } else {
        statusEl.textContent = msg;
      }
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

function applyAnchorSceneZoom() {
  const img = document.getElementById('anchor-scene-lightbox-img');
  const pct = document.getElementById('anchor-zoom-pct');
  if (img) {
    img.style.transform = 'scale(' + __anchorSceneZoom + ')';
  }
  if (pct) pct.textContent = Math.round(__anchorSceneZoom * 100) + '%';
}

function __anchorSceneLightboxOnEscape(e) {
  if (e.key === 'Escape') closeAnchorSceneLightbox();
}

function closeAnchorSceneLightbox() {
  const lb = document.getElementById('anchor-scene-lightbox');
  const img = document.getElementById('anchor-scene-lightbox-img');
  if (!lb) return;
  lb.classList.remove('open');
  lb.setAttribute('aria-hidden', 'true');
  if (img) {
    img.removeAttribute('src');
    img.style.transform = '';
  }
  __anchorSceneZoom = 1;
  applyAnchorSceneZoom();
  document.removeEventListener('keydown', __anchorSceneLightboxOnEscape);
}

function openAnchorSceneLightbox(src) {
  if (!src) return;
  const lb = document.getElementById('anchor-scene-lightbox');
  const img = document.getElementById('anchor-scene-lightbox-img');
  if (!lb || !img) return;
  __anchorSceneZoom = 1;
  img.src = src;
  img.style.transform = 'scale(1)';
  applyAnchorSceneZoom();
  lb.classList.add('open');
  lb.setAttribute('aria-hidden', 'false');
  document.removeEventListener('keydown', __anchorSceneLightboxOnEscape);
  document.addEventListener('keydown', __anchorSceneLightboxOnEscape);
}

function closeBrollThumbLightbox() {
  const lb = document.getElementById('broll-thumb-lightbox');
  const im = document.getElementById('broll-thumb-lightbox-img');
  if (!lb) return;
  lb.classList.remove('open');
  lb.setAttribute('aria-hidden', 'true');
  if (im) im.removeAttribute('src');
  document.removeEventListener('keydown', __brollLightboxOnEscape);
}

function __brollLightboxOnEscape(e) {
  if (e.key === 'Escape') closeBrollThumbLightbox();
}

function openBrollThumbLightbox(src) {
  if (!src) return;
  const lb = document.getElementById('broll-thumb-lightbox');
  const im = document.getElementById('broll-thumb-lightbox-img');
  if (!lb || !im) return;
  im.src = src;
  lb.classList.add('open');
  lb.setAttribute('aria-hidden', 'false');
  document.removeEventListener('keydown', __brollLightboxOnEscape);
  document.addEventListener('keydown', __brollLightboxOnEscape);
}

function brollRefreshSummary() {
  const ul = document.getElementById('broll-summary-list');
  const sumPanel = document.getElementById('broll-summary');
  if (!ul || !__brollPayload || !__brollPayload.segments) {
    if (sumPanel) sumPanel.style.display = 'none';
    return;
  }
  ul.innerHTML = '';
  const dateId = __brollPayload.date_id;
  let any = false;
  __brollPayload.segments.forEach(seg => {
    const si = seg.segment_index;
    const tbl = document.getElementById('broll-table');
    const sel = tbl ? tbl.querySelector('input[name="broll-s-' + si + '"]:checked') : null;
    if (!sel || !sel.value) return;
    const targetRel = seg.target_movie_rel ||
      ('movie-images/' + dateId + '/' + String(si).padStart(2, '0') + '.mp4');
    const targetUri = seg.target_movie_file_uri;
    if (sel.value === 'current') {
      any = true;
      const parts = [document.createTextNode('Segment ' + si + ': keep current clip ')];
      if (targetUri && targetRel) {
        parts.push(el('a', { href: targetUri, target: '_blank', rel: 'noopener noreferrer' },
          [document.createTextNode(targetRel)]));
      } else {
        parts.push(el('code', {}, [document.createTextNode(targetRel)]));
      }
      ul.appendChild(el('li', {}, parts));
      return;
    }
    const rank = parseInt(sel.value, 10);
    const cand = (seg.candidates || []).find(c => c.rank === rank);
    if (!cand) return;
    const sourceRel = cand.source_movie_rel;
    const thumbRel = cand.thumb_video_rel;
    const sourceUri = cand.source_movie_file_uri;
    any = true;
    const parts = [document.createTextNode('Segment ' + si + ': ')];
    if (sourceUri && sourceRel) {
      parts.push(el('a', { href: sourceUri, target: '_blank', rel: 'noopener noreferrer' },
        [document.createTextNode(sourceRel)]));
    } else if (sourceRel) {
      parts.push(el('code', {}, [document.createTextNode(sourceRel)]));
      parts.push(document.createTextNode(' (source file missing)'));
    } else if (cand.thumbnail_episode_fallback && thumbRel) {
      parts.push(document.createTextNode('Preview from '));
      parts.push(el('code', {}, [document.createTextNode(thumbRel)]));
      parts.push(document.createTextNode(
        ' — canonical library/source MP4 missing; thumbnail is this episode’s segment file with the same index.'));
    } else {
      parts.push(document.createTextNode('(no source path)'));
    }
    parts.push(document.createTextNode(' → '));
    if (targetUri) {
      parts.push(el('a', { href: targetUri, target: '_blank', rel: 'noopener noreferrer' },
        [document.createTextNode(targetRel)]));
    } else {
      parts.push(el('code', {}, [document.createTextNode(targetRel)]));
      parts.push(document.createTextNode(' (target not on disk yet)'));
    }
    ul.appendChild(el('li', {}, parts));
  });
  sumPanel.style.display = any ? 'block' : 'none';
}

function brollHasAnyNonNoneSelection() {
  if (!__brollPayload || !Array.isArray(__brollPayload.segments)) return false;
  const tbl = document.getElementById('broll-table');
  if (!tbl) return false;
  for (const seg of __brollPayload.segments) {
    const si = seg.segment_index;
    const sel = tbl.querySelector('input[name="broll-s-' + si + '"]:checked');
    if (sel && sel.value) return true;
  }
  return false;
}

/** @returns {null|{date_id:string,assignments:string[]}|{blocked:true}|{error:true}} */
function collectBrollMaterializePayload() {
  const jd = getPickerJournalDate();
  if (!jd) return null;
  const dateId = jd.replace(/-/g, '');
  if (!__brollPayload) return null;
  if (__brollPayload.date_id !== dateId) {
    if (brollHasAnyNonNoneSelection()) return { blocked: true };
    return null;
  }
  const tbl = document.getElementById('broll-table');
  if (!tbl) return null;
  const assignments = [];
  for (const seg of __brollPayload.segments || []) {
    const si = seg.segment_index;
    const sel = tbl.querySelector('input[name="broll-s-' + si + '"]:checked');
    if (!sel || !sel.value) continue;
    if (sel.value === 'current') continue;
    const rank = parseInt(sel.value, 10);
    const cand = (seg.candidates || []).find(c => c.rank === rank);
    if (!cand || !cand.source_movie_rel) {
      window.alert('Segment ' + si + ': this choice has no source MP4 on disk. Pick another candidate or None.');
      return { error: true };
    }
    const rel = String(cand.source_movie_rel).replace(/\\/g, '/');
    assignments.push(String(si) + '=' + rel);
  }
  if (!assignments.length) return null;
  return { date_id: dateId, assignments: assignments };
}

function formatApiSubprocessOutput(data) {
  const lines = [];
  if (data.command) lines.push('$ ' + JSON.stringify(data.command));
  if (data.cwd) lines.push('cwd: ' + data.cwd);
  if (data.message && data.error && !data.stdout && !data.stderr) lines.push(String(data.message));
  if (data.stdout) lines.push('--- stdout ---\n' + data.stdout);
  if (data.stderr) lines.push('--- stderr ---\n' + data.stderr);
  if (data.error) lines.push('--- error ---\n' + data.error);
  if (data.returncode !== undefined && data.returncode !== null) {
    lines.push('return code: ' + data.returncode);
  }
  return lines.join('\n');
}

function renderBrollTable(payload) {
  __brollPayload = payload;
  const tbody = document.getElementById('broll-tbody');
  const host = document.getElementById('broll-table-host');
  tbody.innerHTML = '';
  if (!payload || !Array.isArray(payload.segments) || !payload.segments.length) {
    host.style.display = 'none';
    const sum = document.getElementById('broll-summary');
    if (sum) sum.style.display = 'none';
    return;
  }
  payload.segments.forEach(seg => {
    const si = seg.segment_index;
    const tr = el('tr', { class: 'broll-row', 'data-segment-index': String(si) });
    tr.appendChild(el('td', { 'data-label': 'Segment' }, [document.createTextNode(String(si))]));
    const narDiv = el('div', { class: 'broll-narr broll-narr-click' });
    const narFull = seg.narration_full != null ? String(seg.narration_full) : '';
    const narPrev = seg.narration_preview != null ? String(seg.narration_preview) : '';
    narDiv.title =
      'Click to edit narration (saved when you close the dialog). Full text:\n' +
      (narFull || narPrev || '(none)');
    narDiv.textContent = narPrev || narFull || '(none)';
    narDiv.addEventListener('click', function () {
      openBrollPromptEditor(si, narFull, narDiv, 'narration');
    });
    tr.appendChild(el('td', { 'data-label': 'Narration' }, [narDiv]));
    const mode = seg.visual_mode || 'b_roll';
    const isTh = mode === 'talking_head';
    const promptPreview = isTh
      ? seg.talking_head_prompt_preview || seg.talking_head_prompt_full || ''
      : seg.video_prompt_preview || '';
    const fullPrompt = isTh
      ? seg.talking_head_prompt_full != null
        ? String(seg.talking_head_prompt_full)
        : promptPreview && promptPreview !== '(none)'
          ? String(promptPreview)
          : ''
      : seg.video_prompt_full != null
        ? String(seg.video_prompt_full)
        : promptPreview && promptPreview !== '(none)'
          ? String(promptPreview)
          : '';
    const promptField = isTh ? 'talking_head_prompt' : 'video_prompt';
    const promptLabel = isTh ? 'Talking-head prompt' : 'Video prompt';
    const p = el('div', { class: 'broll-prompt broll-prompt-click' });
    p.title =
      'Click to edit ' +
      promptField +
      ' (saved when you close the dialog). Full text:\n' +
      (fullPrompt || promptPreview || '(none)');
    p.textContent = promptPreview || fullPrompt || '(none)';
    p.addEventListener('click', function () {
      openBrollPromptEditor(si, fullPrompt, p, promptField);
    });
    tr.appendChild(el('td', { 'data-label': promptLabel }, [p]));
    const hasCurrentClip = !!(seg.target_movie_file_uri && seg.target_movie_rel);
    const choicesWrap = el('div', { class: 'broll-choices-wrap' });
    const choices = el('div', { class: 'broll-choices' });
    if (!hasCurrentClip) {
      const noneLab = el('label', { class: 'broll-choice broll-none' });
      const noneInp = el('input', { type: 'radio', name: 'broll-s-' + si, value: '' });
      noneInp.checked = true;
      noneLab.appendChild(noneInp);
      noneLab.appendChild(el('span', {}, [document.createTextNode('None')]));
      choices.appendChild(noneLab);
    }
    let curEpisodeImg = null;
    let curEpisodeRel = null;
    if (hasCurrentClip) {
      curEpisodeRel = String(seg.target_movie_rel).replace(/\\/g, '/');
      const curLab = el('label', { class: 'broll-choice broll-current' });
      const curInp = el('input', { type: 'radio', name: 'broll-s-' + si, value: 'current' });
      curInp.checked = true;
      curLab.appendChild(curInp);
      curEpisodeImg = el('img', { alt: '' });
      curEpisodeImg.loading = 'eager';
      curEpisodeImg.decoding = 'async';
      curEpisodeImg.src = '/api/b-roll/thumbnail?rel=' + encodeURIComponent(curEpisodeRel) + '&_=' + Date.now();
      curEpisodeImg.title = 'This episode’s segment MP4 on disk; click to enlarge';
      curEpisodeImg.addEventListener('error', function () {
        const ph = el('div', { class: 'broll-ph', html: 'no preview' });
        if (this.parentNode) this.parentNode.replaceChild(ph, this);
      });
      curEpisodeImg.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        openBrollThumbLightbox(this.src);
      });
      curLab.appendChild(curEpisodeImg);
      curLab.appendChild(el('span', {}, [document.createTextNode('This episode')]));
      choices.appendChild(curLab);
    }
    (seg.candidates || []).forEach(cand => {
      const r = cand.rank;
      const lab = el('label', { class: 'broll-choice' });
      const inp = el('input', { type: 'radio', name: 'broll-s-' + si, value: String(r) });
      lab.appendChild(inp);
      const rel = cand.thumb_video_rel;
      if (rel) {
        const img = el('img', { alt: '' });
        img.loading = 'eager';
        img.decoding = 'async';
        img.src = '/api/b-roll/thumbnail?rel=' + encodeURIComponent(rel) + '&_=' + Date.now();
        img.title = 'Click to enlarge; click again or press Escape to close';
        img.addEventListener('error', function () {
          const ph = el('div', { class: 'broll-ph', html: 'no preview' });
          if (this.parentNode) this.parentNode.replaceChild(ph, this);
        });
        img.addEventListener('click', function (e) {
          e.preventDefault();
          e.stopPropagation();
          openBrollThumbLightbox(this.src);
        });
        lab.appendChild(img);
      } else {
        lab.appendChild(el('div', { class: 'broll-ph', html: 'no video' }));
      }
      const shortId = (cand.clip_id || '').length > 16
        ? (cand.clip_id || '').slice(0, 14) + '…'
        : (cand.clip_id || '');
      lab.appendChild(el('span', {}, [document.createTextNode('#' + r + ' ' + shortId)]));
      if (cand.thumbnail_episode_fallback) {
        lab.appendChild(el('div', { class: 'broll-fallback-hint' },
          [document.createTextNode('preview: this episode')]));
      }
      choices.appendChild(lab);
    });
    choicesWrap.appendChild(choices);
    // Always show the per-segment regen button, even if the segment MP4 isn't on disk yet.
    const regenRow = el('div', { class: 'broll-regen-row' });
    const regenBtn = el('button', { type: 'button', class: 'linklike produce-action-btn' }, [document.createTextNode('Regenerate')]);
    regenBtn.title = 'Re-run this segment via run-daily --regenerate-video-segments (creates clip if missing)';
    const regenSt = el('span', { class: 'broll-regen-status', 'aria-live': 'polite' });
    regenBtn.addEventListener('click', function () {
      brollRegenerateSegment(si, regenBtn, regenSt, curEpisodeRel, curEpisodeImg);
    });
    regenRow.appendChild(regenBtn);
    regenRow.appendChild(regenSt);
    choicesWrap.appendChild(regenRow);
    tr.appendChild(el('td', { 'data-label': 'Top matches' }, [choicesWrap]));

    const tdAnchor = el('td', { class: 'broll-anchor-cell', 'data-label': 'Scene anchor' });
    const wrap = el('div', { class: 'broll-anchor-tools' });
    const imgA = el('img', { class: 'broll-anchor-thumb', alt: 'Scene anchor still' });
    imgA.style.display = 'none';
    imgA.loading = 'eager';
    imgA.decoding = 'async';
    imgA.title = 'Click to open zoom viewer';
    imgA.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (imgA.src) openAnchorSceneLightbox(imgA.src);
    });
    const stA = el('span', { class: 'broll-anchor-status', 'aria-live': 'polite' });
    const vm = seg.visual_mode || 'b_roll';
    const showSceneAnchor =
      vm === 'talking_head' || (vm === 'b_roll' && seg.scene_anchor_eligible === true);
    if (!showSceneAnchor) {
      wrap.appendChild(
        el('span', { class: 'help broll-anchor-disabled' }, [
          document.createTextNode(
            vm === 'b_roll'
              ? 'Wan text-to-video (no portrait-backed character for this segment)'
              : 'Scene anchor not available'
          ),
        ])
      );
    } else {
      const btnA = el('button', { type: 'button', class: 'linklike produce-action-btn' }, [
        document.createTextNode('Scene anchor'),
      ]);
      if (seg.conversation_anchor_run && seg.conversation_run_segments && seg.conversation_run_segments.length) {
        btnA.title =
          'Build shared backdrop for segments ' +
          seg.conversation_run_segments.join(', ') +
          ' (one composite master i2i, split per speaker). Regenerates the whole run.';
        const hint = el('div', { class: 'help broll-conv-hint' }, [
          document.createTextNode('Shared run: ' + seg.conversation_run_segments.join(', ')),
        ]);
        wrap.appendChild(hint);
      } else {
        btnA.title =
          'Generate opening still under anchors/ (portrait i2i or text-to-image). Full video uses image-to-video when this file exists.';
      }
      btnA.addEventListener('click', function () {
        runBrollSceneAnchorI2i(si, btnA, stA, imgA);
      });
      wrap.appendChild(btnA);
      wrap.appendChild(imgA);
      wrap.appendChild(stA);
      const jdNow = getPickerJournalDate();
      if (jdNow) refreshBrollAnchorThumb(imgA, jdNow, si);
    }
    tdAnchor.appendChild(wrap);
    tr.appendChild(tdAnchor);

    tbody.appendChild(tr);
  });
  host.style.display = 'block';
  document.getElementById('broll-summary').style.display = 'block';
  tbody.querySelectorAll('input[type="radio"]').forEach(inp => {
    inp.addEventListener('change', brollRefreshSummary);
  });
  brollRefreshSummary();
}

let __convMasterSettings = null;
let __convMasterStatus = null;

async function fetchConvMasterJson(url, init) {
  const r = await fetch(url, Object.assign({ cache: 'no-store', credentials: 'same-origin' }, init || {}));
  const raw = await r.text();
  let data = null;
  try {
    data = raw ? JSON.parse(raw) : null;
  } catch (parseErr) {
    const excerpt = raw ? raw.slice(0, 120) : '(empty)';
    const hint = (r.status === 404 && /not found/i.test(raw))
      ? ' API route missing — restart pipeline_ui/server.py with --reload and hard-refresh.'
      : '';
    const err = new Error('Bad JSON from ' + url + ' (HTTP ' + r.status + '): ' + excerpt + hint);
    err.cause = parseErr;
    throw err;
  }
  if (!r.ok) {
    throw new Error((data && (data.message || data.error)) || ('HTTP ' + r.status + ' from ' + url));
  }
  return data;
}

async function loadConvMasterSettings() {
  const data = await fetchConvMasterJson('/api/conversation-scene-anchor/settings');
  if (!data.ok) throw new Error(data.message || data.error || 'settings failed');
  __convMasterSettings = data;
  const sel = document.getElementById('conv-anchor-strategy');
  if (sel && data.strategy) sel.value = data.strategy;
  updateConvMasterStrategyUi();
  return data;
}

function convMasterUsesSharedStrategy() {
  const s = __convMasterSettings && __convMasterSettings.strategy;
  return (
    s === 'shared_master_split'
    || s === 'shared_master_mask'
    || s === 'shared_master_bookend'
  );
}

function updateConvMasterStrategyUi() {
  const shared = convMasterUsesSharedStrategy();
  const bookend = __convMasterSettings && __convMasterSettings.strategy === 'shared_master_bookend';
  const mask = __convMasterSettings && __convMasterSettings.strategy === 'shared_master_mask';
  const help = document.getElementById('conv-master-strategy-help');
  if (help) {
    if (bookend) {
      help.textContent =
        'Bookend: wide master on open/close (both visible + mask); middle turns use solo '
        + 'crops from that master. Save backdrop, then Regenerate master.';
    } else if (mask) {
      help.textContent =
        'Shared master + mask: edit Shared backdrop below, Save, then Regenerate master. '
        + 'One FAL i2i reframed to Shorts; per-turn white masks drive OmniHuman v1.5.';
    } else if (shared) {
      help.textContent =
        'Shared master + split: edit Shared backdrop below, Save, then Regenerate master. '
        + 'One FAL i2i for the pair, split per segment.';
    } else {
      help.textContent =
        'Solo per speaker: regenerate each talking_head row with Scene anchor below (no shared master).';
    }
  }
}

function convMasterSharedSettingForRun(runIndex) {
  const ta = document.querySelector(
    '.conv-master-shared-text[data-run-index="' + String(runIndex) + '"]'
  );
  return ta ? String(ta.value || '').trim() : '';
}

function renderConvMasterRuns(status) {
  const host = document.getElementById('conv-master-runs-host');
  if (!host) return;
  host.innerHTML = '';
  const runs = (status && status.conversation_runs) || [];
  const sharedStrategy = convMasterUsesSharedStrategy();
  if (!runs.length) {
    host.style.display = 'none';
    return;
  }
  host.style.display = 'block';
  runs.forEach(run => {
    const card = el('div', { class: 'conv-master-run' });
    const title = el('div', { class: 'conv-master-run-title' }, [
      document.createTextNode(
        'Run segments ' + run.segment_indices.join(', ') +
        ' (' + run.left_speaker_id + ' / ' + run.right_speaker_id + ')'
      ),
    ]);
    card.appendChild(title);
    const settingText = run.shared_setting || run.shared_setting_preview || '';
    const label = el('label', {
      class: 'conv-master-shared-label',
      for: 'conv-shared-' + run.run_index,
    }, [document.createTextNode('Shared backdrop (master prompt)')]);
    card.appendChild(label);
    const ta = el('textarea', {
      class: 'conv-master-shared-text',
      id: 'conv-shared-' + run.run_index,
      'data-run-index': String(run.run_index),
      rows: '5',
      spellcheck: 'true',
    });
    ta.value = settingText;
    card.appendChild(ta);
    card.appendChild(el('div', { class: 'help' }, [
      document.createTextNode(
        'Fixed set for the whole ping-pong exchange. Saved to narration JSON; Regenerate rebuilds master + per-speaker anchors.'
      ),
    ]));
    if (run.master_preview_url) {
      const img = el('img', {
        class: 'conv-master-thumb',
        alt: 'Conversation master still',
        title: 'Click to enlarge',
      });
      img.src = run.master_preview_url + '&_=' + Date.now();
      img.addEventListener('click', function () {
        if (img.src) openAnchorSceneLightbox(img.src);
      });
      card.appendChild(img);
    } else {
      card.appendChild(el('div', { class: 'help' }, [
        document.createTextNode('No master PNG yet — save backdrop and regenerate.'),
      ]));
    }
    const actions = el('div', { class: 'conv-master-run-actions' });
    const saveBtn = el('button', { type: 'button' }, [document.createTextNode('Save backdrop')]);
    saveBtn.addEventListener('click', function () {
      saveConvMasterSharedSetting(run.run_index);
    });
    actions.appendChild(saveBtn);
    if (sharedStrategy) {
      const regenBtn = el('button', { type: 'button' }, [
        document.createTextNode('Regenerate master + anchors'),
      ]);
      regenBtn.addEventListener('click', function () {
        runConvMasterRegen(run.run_index);
      });
      actions.appendChild(regenBtn);
    }
    card.appendChild(actions);
    card.dataset.runIndex = String(run.run_index);
    host.appendChild(card);
  });
}

async function loadConvMasterPanel(journalDate) {
  const jd = journalDate || getPickerJournalDate();
  if (!__convMasterSettings) {
    try {
      await loadConvMasterSettings();
    } catch (_) {
      /* keep DOM defaults */
    }
  }
  if (!jd) {
    __convMasterStatus = null;
    renderConvMasterRuns(null);
    updateConvMasterStrategyUi();
    const st = document.getElementById('conv-master-job-status');
    if (st) st.textContent = 'Pick a journal date above.';
    return;
  }
  try {
    const data = await fetchConvMasterJson('/api/conversation-scene-anchor/status?journal_date=' + encodeURIComponent(jd));
    if (!data.ok) throw new Error(data.message || data.error || 'status failed');
    __convMasterStatus = data;
    renderConvMasterRuns(data);
    updateConvMasterStrategyUi();
    const st = document.getElementById('conv-master-job-status');
    if (st && !st.dataset.busy) {
      if (!data.conversation_runs || !data.conversation_runs.length) {
        st.textContent = data.long_conversation_mode
          ? 'No conversation anchor runs in this narration.'
          : 'Not a long-conversation episode.';
      } else {
        st.textContent = '';
      }
    }
  } catch (e) {
    const st = document.getElementById('conv-master-job-status');
    if (st) st.textContent = String(e);
  }
}

async function saveConvMasterStrategy(strategy) {
  const data = await fetchConvMasterJson('/api/conversation-scene-anchor/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy: strategy }),
  });
  if (!data.ok) throw new Error(data.message || data.error || 'save failed');
  __convMasterSettings = data;
  updateConvMasterStrategyUi();
}

async function saveConvMasterSharedSetting(runIndex) {
  const jd = getPickerJournalDate();
  if (!jd) {
    window.alert('Choose a journal date in the picker above.');
    return;
  }
  const setting = convMasterSharedSettingForRun(runIndex);
  if (!setting) {
    window.alert('Shared backdrop cannot be empty.');
    return;
  }
  const st = document.getElementById('conv-master-job-status');
  if (st) st.textContent = 'Saving shared backdrop…';
  try {
    const data = await fetchConvMasterJson('/api/conversation-scene-anchor/shared-setting', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        journal_date: jd,
        run_index: runIndex,
        shared_setting: setting,
      }),
    });
    if (!data.ok) throw new Error(data.message || data.error || 'save failed');
    if (st) st.textContent = 'Saved shared backdrop to narration JSON.';
    await loadConvMasterPanel(jd);
  } catch (e) {
    if (st) st.textContent = String(e);
  }
}

async function runConvMasterRegen(runIndex) {
  const jd = getPickerJournalDate();
  if (!jd) {
    window.alert('Choose a journal date in the picker above.');
    return;
  }
  const idx = typeof runIndex === 'number' ? runIndex : 0;
  const setting = convMasterSharedSettingForRun(idx);
  const st = document.getElementById('conv-master-job-status');
  if (st) {
    st.dataset.busy = '1';
    st.textContent = 'Starting master + anchor regeneration…';
  }
  try {
    const payload = { journal_date: jd, run_index: idx };
    if (setting) payload.shared_setting = setting;
    const startData = await fetchConvMasterJson('/api/conversation-scene-anchor/regenerate-master', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!startData.ok) throw new Error(startData.message || startData.error || 'regen failed');
    const pollUrl = startData.poll_url || ('/api/conversation-scene-anchor/regenerate-master/status?job_id=' + encodeURIComponent(startData.job_id));
    const deadline = Date.now() + 600000;
    while (Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 2500));
      const pdata = await fetchConvMasterJson(pollUrl);
      if (pdata.status === 'running') {
        if (st) st.textContent = 'Regenerating master + anchors… (usually 1–3 min)';
        continue;
      }
      if (!pdata.ok) throw new Error(pdata.message || pdata.error || 'regen failed');
      if (st) {
        st.textContent = 'Done — updated segments ' + (pdata.conversation_refresh_segments || []).join(', ');
      }
      await loadConvMasterPanel(jd);
      const refreshSegs = pdata.conversation_refresh_segments || [];
      if (refreshSegs.length) refreshBrollAnchorThumbsInRun(jd, refreshSegs);
      if (typeof loadProduceSegments === 'function') {
        try { await loadProduceSegments(jd); } catch (_) {}
      }
      break;
    }
  } catch (e) {
    if (st) st.textContent = String(e);
  } finally {
    if (st) delete st.dataset.busy;
    updateConvMasterStrategyUi();
  }
}

(function initConvMasterPanel() {
  const sel = document.getElementById('conv-anchor-strategy');
  if (sel) {
    sel.addEventListener('change', function () {
      saveConvMasterStrategy(sel.value).catch(err => {
        window.alert(String(err));
      });
    });
  }
  loadConvMasterSettings().catch(() => {});
})();

async function loadBrollSuggestions() {
  const errEl = document.getElementById('broll-err');
  errEl.style.display = 'none';
  const jd = getPickerJournalDate();
  if (!jd) {
    errEl.textContent = 'Choose a journal date in the picker above.';
    errEl.style.display = 'block';
    return;
  }
  const pe = document.getElementById('brollPreferEnv');
  const prefer = pe && pe.checked ? '1' : '0';
  const u = '/api/b-roll/suggest?journal_date=' + encodeURIComponent(jd) + '&top=' + B_ROLL_SUGGEST_TOP + '&prefer_environment=' + prefer;
  try {
    const r = await fetch(u, { cache: 'no-store' });
    const text = await r.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      errEl.textContent =
        'Invalid JSON (HTTP ' + r.status + '). Is the pipeline UI server running? ' + text.slice(0, 160);
      errEl.style.display = 'block';
      document.getElementById('broll-table-host').style.display = 'none';
      return;
    }
    if (!data.ok) {
      errEl.textContent = data.message || data.error || ('Request failed (HTTP ' + r.status + ')');
      errEl.style.display = 'block';
      document.getElementById('broll-table-host').style.display = 'none';
      return;
    }
    if (!r.ok) {
      errEl.textContent = data.message || data.error || ('HTTP ' + r.status);
      errEl.style.display = 'block';
      document.getElementById('broll-table-host').style.display = 'none';
      return;
    }
    renderBrollTable(data);
    if (typeof loadConvMasterPanel === 'function') {
      loadConvMasterPanel(jd).catch(() => {});
    }
  } catch (e) {
    errEl.textContent = String(e);
    errEl.style.display = 'block';
  }
}

(function initBrollPromptEditor() {
  const bd = document.getElementById('broll-prompt-backdrop');
  const dlg = document.getElementById('broll-prompt-dialog');
  const closeBtn = document.getElementById('broll-prompt-editor-close');
  if (bd) {
    bd.addEventListener('click', function (e) {
      if (e.target === bd) closeBrollPromptEditor();
    });
  }
  if (dlg) {
    dlg.addEventListener('click', function (e) {
      e.stopPropagation();
    });
  }
  if (closeBtn) closeBtn.addEventListener('click', () => { closeBrollPromptEditor(); });
})();
(function initBrollThumbLightbox() {
  const lb = document.getElementById('broll-thumb-lightbox');
  if (lb) {
    lb.addEventListener('click', function () { closeBrollThumbLightbox(); });
  }
})();

(function initAnchorSceneLightbox() {
  const lb = document.getElementById('anchor-scene-lightbox');
  const closeBtn = document.getElementById('anchor-scene-lightbox-close');
  const zoomWrap = document.getElementById('anchor-scene-zoom-wrap');
  const img = document.getElementById('anchor-scene-lightbox-img');
  const zIn = document.getElementById('anchor-zoom-in');
  const zOut = document.getElementById('anchor-zoom-out');
  const zReset = document.getElementById('anchor-zoom-reset');
  if (!lb || !img) return;
  lb.addEventListener('click', function (e) {
    if (e.target === lb) closeAnchorSceneLightbox();
  });
  if (closeBtn) closeBtn.addEventListener('click', function (e) {
    e.preventDefault();
    e.stopPropagation();
    closeAnchorSceneLightbox();
  });
  function clampZoom(z) {
    if (z < 0.25) return 0.25;
    if (z > 6) return 6;
    return z;
  }
  if (zIn) zIn.addEventListener('click', function (e) {
    e.preventDefault();
    e.stopPropagation();
    __anchorSceneZoom = clampZoom(__anchorSceneZoom * 1.2);
    applyAnchorSceneZoom();
  });
  if (zOut) zOut.addEventListener('click', function (e) {
    e.preventDefault();
    e.stopPropagation();
    __anchorSceneZoom = clampZoom(__anchorSceneZoom / 1.2);
    applyAnchorSceneZoom();
  });
  if (zReset) zReset.addEventListener('click', function (e) {
    e.preventDefault();
    e.stopPropagation();
    __anchorSceneZoom = 1;
    applyAnchorSceneZoom();
  });
  if (zoomWrap) {
    zoomWrap.addEventListener('wheel', function (e) {
      if (!lb.classList.contains('open')) return;
      e.preventDefault();
      const delta = e.deltaY;
      const factor = delta < 0 ? 1.08 : 0.93;
      __anchorSceneZoom = clampZoom(__anchorSceneZoom * factor);
      applyAnchorSceneZoom();
    }, { passive: false });
  }
})();
