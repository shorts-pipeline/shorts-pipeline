// workflow.js — workflow tabs, form building, presets, collectValues, setDryRun.

function fieldWrap(field, inner) {
  const help = field.help
    ? el('div', { class: 'help', html: field.help })
    : null;
  return el('div', { class: 'field' }, [
    el('label', { class: 'main', html: field.label }),
    inner,
    ...(help ? [help] : [])
  ]);
}

const PIPELINE_UI_FORM_GROUPS_NV = [
  { title: 'Voice & narration', ids: ['tts', 'dialogue_mode', 'long_conversation_mode', 'model'], produceScope: 'script' },
  { title: 'Video generation', ids: ['vendor', 'regenerate_video_segments'], produceScope: 'shots' },
  { title: 'Run & assembly', ids: ['skip_existing', 'ambient', 'dry_run'], produceScope: 'both' }
];
const PIPELINE_UI_FORM_GROUPS_FULL = [
  { title: 'Journal date', ids: ['date'] },
  { title: 'Voice & narration', ids: ['tts', 'dialogue_mode', 'long_conversation_mode', 'model'] },
  { title: 'Video generation', ids: ['vendor', 'regenerate_video_segments'] },
  { title: 'Narration scope', ids: ['narration_only', 'no_focus_topic'] },
  { title: 'Run & assembly', ids: ['skip_existing', 'ambient', 'dry_run'] },
  { title: 'YouTube', ids: ['upload', 'privacy'] }
];

let __formSummaryHandler = null;

function appendFieldTo(container, field, wf) {
  const id = field.id;
  const type = field.type || 'text';

  if (type === 'text') {
    const inp = el('input', {
      type: 'text',
      id,
      name: id,
      placeholder: field.placeholder || '',
      value: field.default || ''
    });
    container.appendChild(fieldWrap(field, inp));
    return;
  }

  if (type === 'number') {
    const attrs = {
      type: 'number',
      id,
      name: id,
      value: field.default != null ? String(field.default) : '1',
      step: '1'
    };
    if (field.min != null) attrs.min = String(field.min);
    if (field.max != null) attrs.max = String(field.max);
    const inp = el('input', attrs);
    container.appendChild(fieldWrap(field, inp));
    return;
  }

  if (type === 'select') {
    const sel = el('select', { id, name: id });
    (field.choices || []).forEach(ch => {
      const opt = el('option', { value: ch.value, html: ch.label });
      if (ch.value === field.default) opt.selected = true;
      sel.appendChild(opt);
    });
    container.appendChild(fieldWrap(field, sel));
    return;
  }

  if (type === 'checkbox') {
    const inp = el('input', {
      type: 'checkbox',
      id,
      name: id
    });
    if (effectiveFieldDefault(field, wf)) inp.checked = true;
    const row = el('label', { class: 'main' }, [inp, document.createTextNode(' ' + field.label)]);
    const wrap = el('div', { class: 'field' }, [row]);
    if (field.help) wrap.appendChild(el('div', { class: 'help', html: field.help }));
    container.appendChild(wrap);
    return;
  }

  if (type === 'radio') {
    const name = id;
    const containerInner = el('div');
    (field.choices || []).forEach((ch, i) => {
      const rid = name + '_' + i;
      const inp = el('input', {
        type: 'radio',
        name,
        id: rid,
        value: ch.value
      });
      if (ch.value === field.default) inp.checked = true;
      const lbl = el('label', { for: rid, html: ch.label });
      containerInner.appendChild(el('div', { class: 'radio-row' }, [inp, document.createTextNode(' '), lbl]));
    });
    container.appendChild(fieldWrap(field, containerInner));
  }
}

function wireFormSummaryListeners(form) {
  if (__formSummaryHandler) {
    form.removeEventListener('input', __formSummaryHandler);
    form.removeEventListener('change', __formSummaryHandler);
  }
  __formSummaryHandler = function () {
    updateRunFlagSummary();
  };
  form.addEventListener('input', __formSummaryHandler);
  form.addEventListener('change', __formSummaryHandler);
}

function updateRunFlagSummary() {
  const spec = window.__spec;
  const wfUi = window.__workflow || 'journal';
  const pre = document.getElementById('pipeline-run-summary-pre');
  if (!pre || !spec) return;
  const w = normalizedWorkflow(wfUi);
  if (w === 'journal') {
    pre.textContent = '(Journal — run uses tab-specific flags; switch to Narration / Video / Full for a form preview.)';
    return;
  }
  let vals = collectValues(spec, wfUi);
  vals = mergeJournalDateIntoValues(vals, wfUi);
  const lines = [];
  lines.push('UI tab: ' + w);
  lines.push('API workflow: ' + workflowForApiRun(wfUi));
  lines.push('Merged journal date → values.date: ' + (vals.date && String(vals.date).trim() ? vals.date : '(empty — run-daily may use calendar default)'));
  const fieldList = fieldsForWorkflow(spec, wfUi);
  fieldList.forEach(f => {
    const id = f.id;
    const v = vals[id];
    if (f.type === 'checkbox') {
      lines.push(id + ': ' + (v ? 'on' : 'off'));
    } else if (v != null && String(v).trim() !== '') {
      lines.push(id + ': ' + String(v));
    }
  });
  pre.textContent = lines.join('\n');
}

function buildPipelineFormPresets(wf) {
  const host = document.getElementById('pipeline-form-presets');
  if (!host) return;
  host.innerHTML = '';
  const w = normalizedWorkflow(wf);
  if (w === 'journal' || w === 'full') {
    host.classList.add('hidden');
    return;
  }
  host.classList.remove('hidden');
  const lab = el('span', { class: 'meta' });
  lab.textContent = 'Presets';
  host.appendChild(lab);
  [['quick', 'Quick video (fal, OpenAI TTS, skip on, ambient on)'], ['force_regen', 'Regenerate all (turn skip off)'], ['reset', 'Reset form defaults']].forEach(([pid, label]) => {
    const b = el('button', { type: 'button', class: 'secondary', 'data-preset': pid });
    b.textContent = label;
    host.appendChild(b);
  });
  const presetHelp = el('div', { class: 'help' });
  presetHelp.style.marginTop = '0.35rem';
  presetHelp.textContent =
    'These shortcuts only set the run flags below (vendor, TTS, skip-existing, ambient, etc.). The Narration tab runs the same video pipeline as the Video tab, so the same presets apply when you click Generate video.';
  host.appendChild(presetHelp);
}

function applyPipelinePreset(presetId) {
  const spec = window.__spec;
  if (!spec) return;
  const wf = normalizedWorkflow(window.__workflow || 'journal');
  if (wf !== 'narration' && wf !== 'video') return;
  const fields = fieldsForWorkflow(spec, wf);
  const setSel = (id, val) => {
    const n = document.getElementById(id);
    if (n && n.tagName === 'SELECT') n.value = val;
  };
  const setCb = (id, on) => {
    const n = document.getElementById(id);
    if (n && n.type === 'checkbox') n.checked = !!on;
  };
  const setTxt = (id, val) => {
    const n = document.getElementById(id);
    if (n && n.tagName === 'INPUT' && n.type === 'text') n.value = val != null ? String(val) : '';
  };
  if (presetId === 'quick') {
    setSel('vendor', 'fal');
    setSel('tts', 'openai');
    setCb('skip_existing', true);
    setCb('ambient', true);
    setCb('dry_run', false);
    setTxt('regenerate_video_segments', '');
    setTxt('model', '');
  } else if (presetId === 'force_regen') {
    setCb('skip_existing', false);
  } else if (presetId === 'reset') {
    fields.forEach(field => {
      const id = field.id;
      const type = field.type || 'text';
      const node = document.getElementById(id);
      if (!node) return;
      if (type === 'checkbox') {
        node.checked = !!effectiveFieldDefault(field, wf);
      } else if (type === 'select') {
        node.value = field.default != null ? String(field.default) : '';
      } else if (type === 'text' || type === 'number') {
        node.value = field.default != null ? String(field.default) : '';
      }
    });
  }
  updateRunFlagSummary();
}

(function wirePipelinePresetDelegation() {
  const host = document.getElementById('pipeline-form-presets');
  if (!host || host.dataset.presetWired) return;
  host.dataset.presetWired = '1';
  host.addEventListener('click', (ev) => {
    const btn = ev.target && ev.target.closest ? ev.target.closest('[data-preset]') : null;
    if (!btn || !host.contains(btn)) return;
    const pid = btn.getAttribute('data-preset');
    if (pid) applyPipelinePreset(pid);
  });
})();

function buildForm(spec, workflow) {
  const form = document.getElementById('f');
  form.innerHTML = '';
  const wf = normalizedWorkflow(workflow);
  buildPipelineFormPresets(wf);
  if (wf === 'journal') {
    updateRunFlagSummary();
    return;
  }
  const fields = fieldsForWorkflow(spec, wf);
  const groups = wf === 'full' ? PIPELINE_UI_FORM_GROUPS_FULL : PIPELINE_UI_FORM_GROUPS_NV;
  const groupedIds = new Set(groups.flatMap(g => g.ids));
  groups.forEach(group => {
    const chunk = [];
    group.ids.forEach(fid => {
      const field = fields.find(f => f.id === fid);
      if (field) chunk.push(field);
    });
    if (!chunk.length) return;
    const fs = el('fieldset', { class: 'pipeline-fieldset' });
    if (wf === 'narration' && group.produceScope) {
      fs.setAttribute('data-produce-scope', group.produceScope);
    }
    fs.appendChild(el('legend', {}, [document.createTextNode(group.title)]));
    chunk.forEach(field => appendFieldTo(fs, field, wf));
    form.appendChild(fs);
  });
  fields.forEach(field => {
    if (!groupedIds.has(field.id)) appendFieldTo(form, field, wf);
  });
  wireFormSummaryListeners(form);
  updateRunFlagSummary();
  if (wf === 'narration' && typeof applyProduceFormFieldVisibility === 'function') {
    applyProduceFormFieldVisibility(
      typeof getProduceSubtab === 'function' ? getProduceSubtab() : 'script'
    );
  }
}

function collectValues(spec, workflow) {
  const out = {};
  const wf = normalizedWorkflow(workflow);
  if (wf === 'journal') return out;
  const fields = fieldsForWorkflow(spec, wf);
  fields.forEach(field => {
    const id = field.id;
    const type = field.type || 'text';
    if (type === 'checkbox') {
      const cb = document.getElementById(id);
      out[id] = cb ? cb.checked : !!effectiveFieldDefault(field, wf);
      return;
    }
    if (type === 'radio') {
      const sel = document.querySelector(`input[name="${id}"]:checked`);
      out[id] = sel ? sel.value : field.default;
      return;
    }
    if (type === 'number') {
      const node = document.getElementById(id);
      if (!node) {
        out[id] = field.default;
        return;
      }
      const v = node.value.trim();
      out[id] = v === '' ? field.default : v;
      return;
    }
    const node = document.getElementById(id);
    if (!node) {
      out[id] = field.default != null ? field.default : '';
      return;
    }
    out[id] = node.value;
  });
  return out;
}

function setDryRun(on) {
  const spec = window.__spec;
  if (!spec) return;
  const wf = window.__workflow || 'journal';
  const fields = fieldsForWorkflow(spec, wf);
  const dry = fields.find(f => f.id === 'dry_run');
  if (!dry || dry.type !== 'checkbox') return;
  const cb = document.getElementById('dry_run');
  if (cb) cb.checked = on;
}
