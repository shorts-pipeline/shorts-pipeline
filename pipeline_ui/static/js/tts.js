// tts.js — Generate / regenerate TTS (narration-to-mp3) from Produce Script & audio or Anchor preview.

function getTtsEngineFromForm() {
  const sel = document.getElementById('tts');
  if (sel && sel.value) return String(sel.value).trim();
  return 'openai';
}

function appendTtsRunToOutput(data) {
  const out = document.getElementById('out');
  if (!out || !data || !data.command) return;
  const prev = out.textContent || '';
  const tail = (data.stderr || data.stdout || '').trim();
  out.textContent =
    prev +
    (prev.endsWith('\n') ? '' : '\n') +
    '$ ' +
    (Array.isArray(data.command) ? data.command.join(' ') : data.command) +
    '\n' +
    (tail ? tail + '\n' : '');
}

function updateTtsAudioStatusFromPayload(data) {
  const el = document.getElementById('tts-audio-status');
  if (!el) return;
  if (!data) {
    el.textContent = '';
    el.className = 'help tts-audio-status';
    return;
  }
  const ready = !!(data.tts_ready || data.final_mp3_exists);
  const count = data.segment_mp3_count != null ? data.segment_mp3_count : '';
  const rel = data.final_mp3_rel || '';
  if (ready) {
    el.textContent =
      'Audio ready' +
      (rel ? ' — ' + rel : '') +
      (count !== '' ? ' (' + count + ' segment MP3s on disk)' : '');
    el.className = 'help tts-audio-status';
  } else if (data.tts_error) {
    el.textContent = 'Audio not ready — ' + data.tts_error;
    el.className = 'help tts-audio-status err';
  } else if (count) {
    el.textContent = 'Partial audio on disk (' + count + ' segment MP3s); final.mp3 or durations may be missing.';
    el.className = 'help tts-audio-status err';
  } else {
    el.textContent = 'No TTS audio yet for this date.';
    el.className = 'help tts-audio-status';
  }
}

function syncTtsPrimaryButtonLabels(hasAudio) {
  const gen = document.getElementById('btn-generate-tts');
  const anchorGen = document.getElementById('btn-anchor-generate-tts');
  if (gen) gen.textContent = hasAudio ? 'Generate TTS (full)' : 'Generate TTS';
  if (anchorGen) anchorGen.textContent = hasAudio ? 'Generate TTS (full)' : 'Generate TTS';
}

/**
 * POST /api/generate-tts for the picker journal date.
 * opts: { journalDate, feedbackEl, buttonEl, segmentIndices, regenAll, onSuccess }
 */
async function runGenerateTts(opts) {
  opts = opts || {};
  const jd = opts.journalDate || getPickerJournalDate();
  const fb = opts.feedbackEl;
  const btn = opts.buttonEl;
  if (!jd) {
    if (fb) fb.textContent = 'Pick a journal date first.';
    return null;
  }

  const segIndices =
    opts.segmentIndices != null && String(opts.segmentIndices).trim()
      ? String(opts.segmentIndices).trim()
      : null;
  const regenAll = !!opts.regenAll && !segIndices;

  if (regenAll && !opts.skipConfirm) {
    const ok = window.confirm(
      'Regenerate all TTS for ' +
        jd +
        '? This re-synthesizes every segment and rebuilds final.mp3 (uses the TTS engine in the run form).'
    );
    if (!ok) return null;
  }

  if (fb) {
    fb.textContent = segIndices
      ? 'Regenerating TTS for segment(s) ' + segIndices + '…'
      : 'Generating TTS…';
    fb.style.color = '';
  }
  if (btn) btn.disabled = true;

  const body = {
    journal_date: jd,
    tts: getTtsEngineFromForm(),
  };
  if (segIndices) body.segment_indices = segIndices;

  try {
    const r = await fetch('/api/generate-tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const rawText = await r.text();
    let data = {};
    try {
      data = rawText ? JSON.parse(rawText) : {};
    } catch (_) {
      data = {};
    }
    if (!r.ok || !data.ok) {
      if (fb) {
        let reason = data.error || data.message || r.statusText;
        if (r.status === 404 && (!data || !Object.keys(data).length)) {
          reason =
            'API route not found. Restart pipeline_ui/server.py with --reload and hard-refresh the page.';
        } else if (r.status === 404 && data.error === 'no_narration') {
          reason = 'No narration JSON for this date. Generate narration first.';
        }
        fb.textContent = 'Failed: ' + reason;
        fb.style.color = 'var(--err)';
      }
      appendTtsRunToOutput(data);
      return data;
    }
    if (fb) {
      const rel = data.final_mp3_rel || 'audio/…/final.mp3';
      const prefix = segIndices ? 'Segment TTS done' : regenAll ? 'Regenerated' : 'Done';
      fb.textContent = data.cancelled ? 'Cancelled.' : prefix + ' — ' + rel;
      fb.style.color = 'var(--ok, inherit)';
    }
    appendTtsRunToOutput(data);
    if (typeof opts.onSuccess === 'function') {
      await opts.onSuccess(data);
    } else if (typeof refreshProduceScriptPanel === 'function') {
      await refreshProduceScriptPanel(jd);
    }
    return data;
  } catch (e) {
    if (fb) {
      fb.textContent = 'Error: ' + e;
      fb.style.color = 'var(--err)';
    }
    return null;
  } finally {
    if (btn) btn.disabled = false;
  }
}

function wireGenerateTtsButtons() {
  const scriptGen = document.getElementById('btn-generate-tts');
  if (scriptGen && !scriptGen.dataset.ttsWired) {
    scriptGen.dataset.ttsWired = '1';
    scriptGen.addEventListener('click', function () {
      runGenerateTts({
        feedbackEl: document.getElementById('generate-tts-feedback'),
        buttonEl: scriptGen,
      }).catch(function () {});
    });
  }
  const scriptRegen = document.getElementById('btn-regenerate-tts-all');
  if (scriptRegen && !scriptRegen.dataset.ttsWired) {
    scriptRegen.dataset.ttsWired = '1';
    scriptRegen.addEventListener('click', function () {
      runGenerateTts({
        feedbackEl: document.getElementById('generate-tts-feedback'),
        buttonEl: scriptRegen,
        regenAll: true,
      }).catch(function () {});
    });
  }

  const anchorGen = document.getElementById('btn-anchor-generate-tts');
  if (anchorGen && !anchorGen.dataset.ttsWired) {
    anchorGen.dataset.ttsWired = '1';
    anchorGen.addEventListener('click', function () {
      runGenerateTts({
        feedbackEl: document.getElementById('anchor-generate-tts-feedback'),
        buttonEl: anchorGen,
        onSuccess: function () {
          const jd = getPickerJournalDate();
          if (jd && typeof refreshAnchorPreviewPanel === 'function') {
            return refreshAnchorPreviewPanel(jd);
          }
        },
      }).catch(function () {});
    });
  }
  const anchorRegen = document.getElementById('btn-anchor-regenerate-tts-all');
  if (anchorRegen && !anchorRegen.dataset.ttsWired) {
    anchorRegen.dataset.ttsWired = '1';
    anchorRegen.addEventListener('click', function () {
      runGenerateTts({
        feedbackEl: document.getElementById('anchor-generate-tts-feedback'),
        buttonEl: anchorRegen,
        regenAll: true,
        onSuccess: function () {
          const jd = getPickerJournalDate();
          if (jd && typeof refreshAnchorPreviewPanel === 'function') {
            return refreshAnchorPreviewPanel(jd);
          }
        },
      }).catch(function () {});
    });
  }
}
