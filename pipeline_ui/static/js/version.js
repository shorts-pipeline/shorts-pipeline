// version.js — UI version label, disk alignment check, optional server restart.

let __uiClientFingerprint = null;
let __uiClientVersion = null;
let __uiReconnectTimer = null;

function _stopUiReconnectPolling() {
  if (__uiReconnectTimer) {
    clearInterval(__uiReconnectTimer);
    __uiReconnectTimer = null;
  }
}

// The server is unreachable (fully down, or mid-restart). There is nothing a page
// can do to start a process that isn't running — but the --reload watchdog respawns
// a crashed worker on its own, so poll quietly and pick the UI back up the moment it does.
function _startUiReconnectPolling(btn) {
  if (__uiReconnectTimer) return;
  __uiReconnectTimer = setInterval(async () => {
    try {
      const r = await fetch('/api/ui/version', { cache: 'no-store' });
      if (!r.ok) return;
      const data = await r.json();
      _stopUiReconnectPolling();
      __uiClientFingerprint = data.fingerprint;
      __uiClientVersion = data.version;
      _setUiVersionBtnText(data);
      btn.title = 'Server is back. Click to verify UI version alignment with disk.';
    } catch (_e) {
      // still unreachable; keep polling
    }
  }, 4000);
}

function _uiVersionBtnLabel(version, shortFp) {
  const v = version || '?';
  const fp = shortFp || '--------';
  return 'UI v' + v + ' · ' + fp;
}

function _setUiVersionBtnText(data) {
  const btn = document.getElementById('ui-version-btn');
  if (!btn || !data) return;
  btn.textContent = _uiVersionBtnLabel(data.version, data.fingerprint_short);
  btn.classList.toggle('ui-version-btn--stale', !!(data.server_stale || data.client_stale));
}

function _setRestartBtnState(restartBtn, enabled, reason) {
  if (!restartBtn) return;
  restartBtn.classList.toggle('ui-version-action--disabled', !enabled);
  restartBtn.setAttribute('aria-disabled', enabled ? 'false' : 'true');
  restartBtn.title = enabled
    ? 'Ask the --reload parent to restart the UI server'
    : reason || 'Start server with --reload to enable restart from the UI';
  if (reason) restartBtn.dataset.disabledReason = reason;
  else delete restartBtn.dataset.disabledReason;
}

function _restartBtnEnabled(restartBtn) {
  return !!(restartBtn && restartBtn.getAttribute('aria-disabled') !== 'true');
}

async function initUiVersion() {
  const btn = document.getElementById('ui-version-btn');
  const panel = document.getElementById('ui-version-panel');
  if (!btn || !panel) return;

  try {
    const r = await fetch('/api/ui/version', { cache: 'no-store' });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || r.statusText);
    __uiClientFingerprint = data.fingerprint;
    __uiClientVersion = data.version;
    _setUiVersionBtnText(data);
  } catch (e) {
    btn.textContent = 'UI server unreachable — retrying…';
    btn.title = String(e);
    btn.classList.add('ui-version-btn--stale');
    _startUiReconnectPolling(btn);
  }

  btn.addEventListener('click', (ev) => {
    ev.stopPropagation();
    const open = !panel.hidden;
    if (open) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;
    verifyUiVersion().catch(() => {});
  });

  // iOS Safari: stop panel taps from reaching the document dismiss handler.
  panel.addEventListener('click', (ev) => ev.stopPropagation());
  panel.addEventListener('pointerdown', (ev) => ev.stopPropagation());

  document.addEventListener('click', (ev) => {
    if (panel.hidden) return;
    const footer = document.getElementById('ui-version-footer');
    if (footer && !footer.contains(ev.target)) panel.hidden = true;
  });

  const restartBtn = document.getElementById('ui-version-restart');
  if (restartBtn) {
    restartBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      if (!_restartBtnEnabled(restartBtn)) {
        const statusEl = document.getElementById('ui-version-status');
        if (statusEl) {
          statusEl.textContent =
            restartBtn.dataset.disabledReason ||
            'Start server with --reload to enable restart from the UI';
          statusEl.className = 'ui-version-status ui-version-status--warn';
        }
        return;
      }
      requestUiServerRestart().catch(() => {});
    });
  }

  const refreshBtn = document.getElementById('ui-version-refresh');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      verifyUiVersion().catch(() => {});
    });
  }
}

function _renderUiVerifyPanel(data) {
  const statusEl = document.getElementById('ui-version-status');
  const detailEl = document.getElementById('ui-version-detail');
  const restartBtn = document.getElementById('ui-version-restart');
  if (!statusEl || !detailEl) return;

  const aligned = !!data.aligned;
  statusEl.textContent = data.message || (aligned ? 'Aligned.' : 'Out of date.');
  statusEl.className = 'ui-version-status ' + (aligned ? 'ui-version-status--ok' : 'ui-version-status--warn');

  const lines = [];
  lines.push('Version: ' + (data.version || '?'));
  lines.push('Disk fingerprint: ' + (data.fingerprint_short || '—'));
  if (data.server_started_fingerprint) {
    lines.push('Server started: ' + String(data.server_started_fingerprint).slice(0, 8));
  }
  if (data.client_fingerprint) {
    lines.push('Browser loaded: ' + String(data.client_fingerprint).slice(0, 8));
  }
  if (data.server_pid != null) {
    lines.push('Server PID: ' + data.server_pid);
  }
  if (Array.isArray(data.changed_files) && data.changed_files.length) {
    lines.push('Changed since server start:');
    data.changed_files.forEach((f) => lines.push('  · ' + f));
  }
  detailEl.textContent = lines.join('\n');

  if (restartBtn) {
    _setRestartBtnState(
      restartBtn,
      !!data.reload_available,
      'Start server with --reload to enable restart from the UI',
    );
  }

  _setUiVersionBtnText({
    version: data.version,
    fingerprint_short: data.fingerprint_short,
    server_stale: data.server_stale,
    client_stale: data.client_stale,
  });
}

async function verifyUiVersion() {
  const statusEl = document.getElementById('ui-version-status');
  if (statusEl) {
    statusEl.textContent = 'Checking…';
    statusEl.className = 'ui-version-status';
  }
  const r = await fetch('/api/ui/verify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ client_fingerprint: __uiClientFingerprint || '' }),
    cache: 'no-store',
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.message || data.error || r.statusText);
  _renderUiVerifyPanel(data);
  return data;
}

async function requestUiServerRestart() {
  const statusEl = document.getElementById('ui-version-status');
  const restartBtn = document.getElementById('ui-version-restart');
  _setRestartBtnState(restartBtn, false, 'Restart in progress…');
  if (statusEl) {
    statusEl.textContent = 'Requesting server restart…';
    statusEl.className = 'ui-version-status';
  }

  let oldPid = null;
  try {
    const vr = await fetch('/api/ui/version', { cache: 'no-store' });
    const vd = await vr.json().catch(() => ({}));
    if (vd.server_pid != null) oldPid = vd.server_pid;
  } catch (_) { /* ignore */ }

  const r = await fetch('/api/ui/restart', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
    cache: 'no-store',
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok || !data.ok) {
    if (statusEl) {
      statusEl.textContent = data.message || data.error || 'Restart failed';
      statusEl.className = 'ui-version-status ui-version-status--warn';
    }
    try {
      const vr = await fetch('/api/ui/version', { cache: 'no-store' });
      const vd = await vr.json().catch(() => ({}));
      _setRestartBtnState(
        restartBtn,
        !!vd.reload_available,
        vd.reload_available
          ? ''
          : 'Start server with --reload to enable restart from the UI',
      );
    } catch (_) {
      _setRestartBtnState(restartBtn, true, '');
    }
    return;
  }

  if (statusEl) {
    statusEl.textContent = 'Restarting… waiting for server';
    statusEl.className = 'ui-version-status';
  }

  const deadline = Date.now() + 45000;
  let sawDown = false;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 800));
    try {
      const vr = await fetch('/api/ui/version', { cache: 'no-store' });
      if (!vr.ok) {
        sawDown = true;
        continue;
      }
      const vd = await vr.json();
      if (sawDown || (oldPid != null && vd.server_pid !== oldPid)) {
        __uiClientFingerprint = vd.fingerprint;
        __uiClientVersion = vd.version;
        if (statusEl) {
          statusEl.textContent = 'Server restarted. Hard-refresh the page to load new assets.';
          statusEl.className = 'ui-version-status ui-version-status--ok';
        }
        _setUiVersionBtnText(vd);
        _setRestartBtnState(
          restartBtn,
          !!vd.reload_available,
          vd.reload_available
            ? ''
            : 'Start server with --reload to enable restart from the UI',
        );
        return;
      }
    } catch (_) {
      sawDown = true;
    }
  }

  if (statusEl) {
    statusEl.textContent = 'Restart requested but server did not come back in time. Check the terminal.';
    statusEl.className = 'ui-version-status ui-version-status--warn';
  }
  try {
    const vr = await fetch('/api/ui/version', { cache: 'no-store' });
    const vd = await vr.json().catch(() => ({}));
    _setRestartBtnState(
      restartBtn,
      !!vd.reload_available,
      vd.reload_available
        ? ''
        : 'Start server with --reload to enable restart from the UI',
    );
  } catch (_) {
    _setRestartBtnState(restartBtn, true, '');
  }
}
