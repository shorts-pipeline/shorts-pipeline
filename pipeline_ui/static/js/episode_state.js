// episode_state.js — pipeline state sidecar viewer (journal picker button).

function episodeStateNodes() {
  return {
    bd: document.getElementById('episode-state-backdrop'),
    summary: document.getElementById('episode-state-summary'),
    pre: document.getElementById('episode-state-pre'),
    title: document.getElementById('episode-state-title'),
  };
}

function formatEpisodeStateSummary(payload) {
  if (!payload || !payload.ok) {
    return payload && payload.error ? String(payload.error) : 'Could not load pipeline state.';
  }
  const st = payload.state || {};
  const narr = st.narration || {};
  const tts = st.tts || {};
  const video = st.video || {};
  const output = st.output || {};
  const summary = st.summary || {};
  const lines = [];
  lines.push('Date: ' + (payload.journal_date || st.journal_date || '—') + ' (' + (payload.date_id || st.date_id || '') + ')');
  if (narr.title) lines.push('Title: ' + narr.title);
  lines.push(
    'Narration: ' +
      (narr.exists ? narr.segment_count + ' segment(s)' : 'missing') +
      (narr.dialogue_mode ? ' · dialogue' : '') +
      (narr.long_conversation_mode ? ' · long conversation' : '')
  );
  lines.push(
    'TTS: ' +
      (tts.ready ? 'ready' : 'not ready') +
      (tts.final_mp3 ? ' · final.mp3' : '') +
      (tts.missing_audio_indices && tts.missing_audio_indices.length
        ? ' · missing audio ' + tts.missing_audio_indices.join(', ')
        : '')
  );
  lines.push(
    'Video clips: ' +
      (video.clip_count != null ? video.clip_count : '—') +
      '/' +
      (narr.segment_count != null ? narr.segment_count : '—') +
      (video.missing_clip_indices && video.missing_clip_indices.length
        ? ' · missing ' + video.missing_clip_indices.join(', ')
        : '')
  );
  lines.push(
    'Output MP4: ' + (output.final_mp4_exists ? output.final_mp4_rel || 'yes' : 'not assembled')
  );
  if (payload.sidecar_rel) lines.push('Sidecar: ' + payload.sidecar_rel);
  if (st.updated_at) lines.push('Updated: ' + st.updated_at + (st.source ? ' (' + st.source + ')' : ''));
  if (summary.complete) {
    lines.push('Status: complete');
  } else if (summary.blocking && summary.blocking.length) {
    lines.push('Blocking:');
    summary.blocking.forEach(function (b) {
      lines.push('  • ' + b);
    });
  } else {
    lines.push('Status: in progress');
  }
  if (tts.error && !tts.ready) lines.push('TTS note: ' + tts.error);
  return lines.join('\n');
}

function closeEpisodeStateModal() {
  const { bd, pre, summary } = episodeStateNodes();
  if (bd) {
    bd.classList.remove('open');
    bd.setAttribute('aria-hidden', 'true');
  }
  if (pre) pre.textContent = '';
  if (summary) summary.textContent = '';
}

async function openEpisodeStateModal() {
  const jd = getPickerJournalDate();
  if (!jd) {
    window.alert('Choose a journal date in the picker above.');
    return;
  }
  const { bd, pre, summary, title } = episodeStateNodes();
  if (!bd || !pre) return;
  if (title) title.textContent = 'Pipeline state — ' + jd;
  if (summary) summary.textContent = 'Loading…';
  pre.textContent = '';
  bd.classList.add('open');
  bd.setAttribute('aria-hidden', 'false');
  try {
    const r = await fetch(
      '/api/episode-state?journal_date=' + encodeURIComponent(jd) + '&refresh=1',
      { cache: 'no-store' }
    );
    const data = await r.json();
    if (!r.ok || !data.ok) {
      const msg = data.message || data.error || 'HTTP ' + r.status;
      if (summary) summary.textContent = msg;
      pre.textContent = JSON.stringify(data, null, 2);
      return;
    }
    if (summary) summary.textContent = formatEpisodeStateSummary(data);
    pre.textContent = JSON.stringify(data.state, null, 2);
  } catch (e) {
    if (summary) summary.textContent = String(e);
  }
}

document.addEventListener('DOMContentLoaded', function () {
  const btn = document.getElementById('btn-episode-state');
  if (btn) btn.addEventListener('click', function () { openEpisodeStateModal(); });
  const closeBtn = document.getElementById('episode-state-close');
  if (closeBtn) closeBtn.addEventListener('click', function () { closeEpisodeStateModal(); });
  const bd = document.getElementById('episode-state-backdrop');
  if (bd) {
    bd.addEventListener('click', function (e) {
      if (e.target && e.target.id === 'episode-state-backdrop') closeEpisodeStateModal();
    });
  }
});
