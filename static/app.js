/* Canon Import Tool — frontend */

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function fmt_bytes(b) {
  if (b >= 1e9) return (b / 1e9).toFixed(1) + ' GB';
  if (b >= 1e6) return (b / 1e6).toFixed(0) + ' MB';
  return (b / 1e3).toFixed(0) + ' KB';
}

function fmt_duration(s) {
  if (!s) return '—';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec.toString().padStart(2,'0')}s`;
  return `${sec}s`;
}

function fmt_date(iso) {
  if (!iso) return '—';
  return iso.replace('T', ' ').slice(0, 16);
}

function log_line(container, text, cls = 'log-info') {
  const line = document.createElement('div');
  line.className = cls;
  line.textContent = text;
  container.appendChild(line);
  container.scrollTop = container.scrollHeight;
}

// ---------------------------------------------------------------------------
// Tab switching
// ---------------------------------------------------------------------------

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => {
      p.classList.add('hidden');
      p.classList.remove('active');
    });
    btn.classList.add('active');
    const panel = document.getElementById(`tab-${btn.dataset.tab}`);
    panel.classList.remove('hidden');
    panel.classList.add('active');
    if (btn.dataset.tab === 'catalog') load_catalog();
    if (btn.dataset.tab === 'settings') load_settings_form();
  });
});

// ---------------------------------------------------------------------------
// Import tab — state
// ---------------------------------------------------------------------------

let detected_cameras = [];
let scan_recordings  = [];
let bytes_done       = 0;
let bytes_total      = 0;

function selected_mounts() {
  return detected_cameras
    .filter(c => document.getElementById(`cam-${c.mount}`)?.checked)
    .map(c => c.mount);
}

// ---------------------------------------------------------------------------
// Import tab — detect
// ---------------------------------------------------------------------------

async function detect_cameras() {
  const btn = document.getElementById('btn-detect');
  btn.disabled = true;
  btn.textContent = 'Detecting…';
  try {
    const res = await fetch('/api/detect');
    detected_cameras = await res.json();
    render_camera_list();
  } catch (e) {
    document.getElementById('camera-list').innerHTML =
      `<p class="muted" style="color:var(--error)">Detection failed: ${e.message}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Detect Camera';
  }
}

function render_camera_list() {
  const el         = document.getElementById('camera-list');
  const eject_btn  = document.getElementById('btn-eject');
  const safe_msg   = document.getElementById('safe-to-unplug');

  safe_msg.classList.add('hidden');

  if (!detected_cameras.length) {
    el.innerHTML = '<p class="muted">No AVCHD camera volumes found. Is the camera plugged in and mounted?</p>';
    document.getElementById('btn-scan').disabled = true;
    eject_btn.classList.add('hidden');
    return;
  }

  el.innerHTML = '';
  detected_cameras.forEach(c => {
    const item = document.createElement('label');
    item.className = 'camera-item selected';
    item.innerHTML = `
      <input type="checkbox" id="cam-${c.mount}" checked>
      <span class="camera-label">${c.label}</span>
      <span class="camera-meta">${c.volume_type} &nbsp;·&nbsp; ${c.file_count} files</span>`;
    item.querySelector('input').addEventListener('change', () => {
      item.classList.toggle('selected', item.querySelector('input').checked);
      update_scan_btn();
    });
    el.appendChild(item);
  });

  eject_btn.classList.remove('hidden');
  eject_btn.disabled = false;
  eject_btn.textContent = '⏏ Eject Camera';
  update_scan_btn();
}

function update_scan_btn() {
  document.getElementById('btn-scan').disabled = selected_mounts().length === 0;
}

// ---------------------------------------------------------------------------
// Import tab — scan
// ---------------------------------------------------------------------------

async function scan_camera() {
  const mounts = selected_mounts();
  if (!mounts.length) return;

  const btn        = document.getElementById('btn-scan');
  const status_row = document.getElementById('scan-status-row');
  const status_msg = document.getElementById('scan-status-msg');
  const status_txt = document.getElementById('scan-status');

  btn.disabled = true;
  btn.textContent = 'Scanning…';
  status_row.classList.remove('hidden');
  status_msg.textContent = 'Running ffprobe on camera files…';
  status_txt.classList.add('hidden');
  document.getElementById('scan-results').classList.add('hidden');
  document.getElementById('import-actions').classList.add('hidden');

  try {
    const res = await fetch('/api/scan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mounts}),
    });
    if (!res.ok) throw new Error(await res.text());
    scan_recordings = await res.json();
    render_scan_results();
  } catch (e) {
    status_msg.textContent = `Scan failed: ${e.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Scan Camera';
    status_row.classList.add('hidden');
    status_txt.classList.remove('hidden');
  }
}

function render_scan_results() {
  const tbody = document.getElementById('scan-tbody');
  tbody.innerHTML = '';
  scan_recordings.forEach(r => {
    const tr = document.createElement('tr');
    const status = r.needs_stitch
      ? '<span class="badge badge-stitch">Needs stitch</span>'
      : r.already_imported
        ? '<span class="badge badge-done">Imported</span>'
        : '<span class="badge badge-new">New</span>';
    const multi = r.is_multipart
      ? ' <span class="badge badge-multi">Multi-part</span>' : '';
    tr.innerHTML = `
      <td>${fmt_date(r.recorded_at)}</td>
      <td>${fmt_duration(r.duration_seconds)}</td>
      <td>${fmt_bytes(r.size_bytes)}</td>
      <td>${r.files.join(' + ')}</td>
      <td>${status}${multi}</td>`;
    tbody.appendChild(tr);
  });

  const new_recs    = scan_recordings.filter(r => !r.already_imported);
  const stitch_recs = scan_recordings.filter(r => r.needs_stitch);
  const new_bytes   = new_recs.reduce((s, r) => s + r.size_bytes, 0);

  document.getElementById('scan-status').textContent = '';
  const stitch_note = stitch_recs.length > 0
    ? ` · ${stitch_recs.length} need stitch` : '';
  document.getElementById('scan-summary').textContent =
    `${scan_recordings.length} recordings found · ` +
    `${new_recs.length} to import (${fmt_bytes(new_bytes)})` +
    stitch_note +
    ` · ${scan_recordings.length - new_recs.length - stitch_recs.length} already imported`;

  document.getElementById('scan-results').classList.remove('hidden');

  if (new_recs.length > 0) {
    document.getElementById('import-actions').classList.remove('hidden');
  }
}

// ---------------------------------------------------------------------------
// Import tab — import
// ---------------------------------------------------------------------------

async function start_import() {
  const mounts = selected_mounts();
  document.getElementById('btn-import').disabled = true;
  document.getElementById('btn-eject').disabled = true;
  document.getElementById('progress-card').classList.remove('hidden');
  document.getElementById('import-done').classList.add('hidden');
  document.getElementById('import-log').innerHTML = '';
  bytes_done  = 0;
  bytes_total = scan_recordings
    .filter(r => !r.already_imported)
    .reduce((s, r) => s + r.size_bytes, 0);

  const res = await fetch('/api/import/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mounts}),
  });
  if (!res.ok) {
    alert('Failed to start import: ' + (await res.text()));
    document.getElementById('btn-import').disabled = false;
    document.getElementById('btn-eject').disabled = false;
    return;
  }

  const log = document.getElementById('import-log');
  const src = new EventSource('/api/import/stream');

  src.onmessage = e => {
    const ev = JSON.parse(e.data);
    handle_import_event(ev, log);
    if (ev.type === 'import_complete' || ev.type === 'error' || ev.type === 'cancelled') {
      src.close();
      document.getElementById('btn-import').disabled = false;
      document.getElementById('btn-eject').disabled = false;
    }
  };
  src.onerror = () => {
    log_line(log, 'Stream disconnected.', 'log-err');
    src.close();
    document.getElementById('btn-import').disabled = false;
    document.getElementById('btn-eject').disabled = false;
  };
}

function handle_import_event(ev, log) {
  const bar   = document.getElementById('progress-bar');
  const label = document.getElementById('progress-label');

  if (ev.type === 'info') {
    log_line(log, ev.msg);
  } else if (ev.type === 'import_start') {
    bytes_total = ev.total_bytes;
    log_line(log, `${ev.total_new} new recordings · ${fmt_bytes(ev.total_bytes)} to copy`);
  } else if (ev.type === 'file_start') {
    const action = ev.is_multipart ? 'concat' : 'copy';
    log_line(log, `[${ev.index+1}/${ev.total}] ${action} ${ev.files.join(' + ')} → ${ev.dest}`, 'log-start');
  } else if (ev.type === 'file_done') {
    bytes_done += scan_recordings.find(r => r.files.includes(
      ev.dest.split('/').pop().replace(/_\d+\.mts$/, '.mts')
    ))?.size_bytes || 0;
    const pct = bytes_total > 0 ? Math.min(100, bytes_done / bytes_total * 100) : 0;
    bar.style.width = pct + '%';
    label.textContent = `${fmt_bytes(bytes_done)} / ${fmt_bytes(bytes_total)}`;
    log_line(log, `  ✓ done`, 'log-ok');
  } else if (ev.type === 'file_error') {
    log_line(log, `  ✗ error: ${ev.error}`, 'log-err');
  } else if (ev.type === 'import_complete') {
    bar.style.width = '100%';
    label.textContent = `${fmt_bytes(bytes_total)} / ${fmt_bytes(bytes_total)}`;
    const done = document.getElementById('import-done');
    done.classList.remove('hidden', 'error', 'success');
    if (ev.errors > 0) {
      done.classList.add('error');
      done.textContent = `Finished with errors: ${ev.imported} imported, ${ev.errors} failed.`;
    } else {
      done.classList.add('success');
      done.textContent = `${ev.imported} recording(s) imported successfully. Use the Eject button above before unplugging.`;
    }
    scan_camera();
  } else if (ev.type === 'error') {
    log_line(log, `Error: ${ev.msg}`, 'log-err');
  }
}

// ---------------------------------------------------------------------------
// Catalog tab
// ---------------------------------------------------------------------------

let catalog_data = null;

async function load_catalog() {
  try {
    const res = await fetch('/api/catalog');
    catalog_data = await res.json();
    render_catalog();
  } catch (e) {
    console.error('Failed to load catalog', e);
  }
}

function render_catalog() {
  if (!catalog_data || !catalog_data.recordings.length) {
    document.getElementById('catalog-empty').classList.remove('hidden');
    document.getElementById('catalog-table').classList.add('hidden');
    document.getElementById('catalog-stats').innerHTML = '';
    return;
  }

  document.getElementById('catalog-empty').classList.add('hidden');

  const recs = catalog_data.recordings;
  const total_bytes    = recs.reduce((s, r) => s + (r.size_bytes || 0), 0);
  const total_secs     = recs.reduce((s, r) => s + (r.duration_seconds || 0), 0);
  const total_hours    = (total_secs / 3600).toFixed(1);
  const glacier_count  = recs.filter(r => r.glacier_archived).length;

  document.getElementById('catalog-stats').innerHTML = `
    <div class="stat-item"><strong>${recs.length}</strong>Recordings</div>
    <div class="stat-item"><strong>${total_hours}h</strong>Total footage</div>
    <div class="stat-item"><strong>${fmt_bytes(total_bytes)}</strong>Total size</div>
    <div class="stat-item"><strong>${glacier_count}</strong>In Glacier</div>`;

  // Populate year filter
  const years = [...new Set(recs.map(r => r.recorded_at?.slice(0,4)).filter(Boolean))].sort().reverse();
  const yearSel = document.getElementById('filter-year');
  const currentYear = yearSel.value;
  yearSel.innerHTML = '<option value="">All</option>';
  years.forEach(y => {
    const opt = document.createElement('option');
    opt.value = y; opt.textContent = y;
    if (y === currentYear) opt.selected = true;
    yearSel.appendChild(opt);
  });

  apply_catalog_filters();
}

function apply_catalog_filters() {
  if (!catalog_data) return;
  const year    = document.getElementById('filter-year').value;
  const source  = document.getElementById('filter-source').value;
  const glacier = document.getElementById('filter-glacier').value;
  const search  = document.getElementById('filter-search').value.trim().toLowerCase();

  let recs = catalog_data.recordings;
  if (year)    recs = recs.filter(r => r.recorded_at?.startsWith(year));
  if (source)  recs = recs.filter(r => r.source === source);
  if (glacier) recs = recs.filter(r => String(r.glacier_archived) === glacier);
  if (search)  recs = recs.filter(r =>
    (r.recorded_at || '').toLowerCase().includes(search) ||
    (r.path || '').toLowerCase().includes(search)
  );

  const tbody = document.getElementById('catalog-tbody');
  tbody.innerHTML = '';
  recs.forEach(r => {
    const src_badge = {
      canon_import: '<span class="badge badge-ok">This tool</span>',
      legacy_tvd:   '<span class="badge badge-legacy">Legacy</span>',
      manual:       '<span class="badge badge-manual">Manual</span>',
    }[r.source] || r.source;
    const glacier_badge = r.glacier_archived
      ? '<span class="badge badge-ok">Archived</span>'
      : '<span class="badge badge-done">No</span>';
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${fmt_date(r.recorded_at)}</td>
      <td>${fmt_duration(r.duration_seconds)}</td>
      <td>${fmt_bytes(r.size_bytes)}</td>
      <td style="font-family:monospace;font-size:12px">${r.path}</td>
      <td>${src_badge}</td>
      <td>${glacier_badge}</td>`;
    tbody.appendChild(tr);
  });

  document.getElementById('catalog-table').classList.toggle('hidden', recs.length === 0);
  document.getElementById('catalog-empty').classList.toggle('hidden', recs.length > 0);
}

async function cancel_job() {
  await fetch('/api/cancel', {method: 'POST'});
}

async function build_catalog() {
  const btn        = document.getElementById('btn-build-catalog');
  const cancel_btn = document.getElementById('btn-cancel-catalog');
  const spinner    = document.getElementById('catalog-spinner');
  const status_msg = document.getElementById('catalog-status-msg');
  const progress   = document.getElementById('catalog-build-progress');
  const log        = document.getElementById('catalog-build-log');

  btn.disabled = true;
  progress.classList.remove('hidden');
  log.innerHTML = '';
  spinner.classList.remove('hidden');
  status_msg.textContent = 'Starting…';
  cancel_btn.disabled = false;

  const res = await fetch('/api/catalog/build/start', {method: 'POST'});
  if (!res.ok) {
    log_line(log, 'Failed to start: ' + (await res.text()), 'log-err');
    btn.disabled = false;
    spinner.classList.add('hidden');
    return;
  }

  const finish = () => {
    spinner.classList.add('hidden');
    cancel_btn.disabled = true;
    btn.disabled = false;
  };

  const src = new EventSource('/api/catalog/build/stream');
  src.onmessage = e => {
    const ev = JSON.parse(e.data);
    if (ev.type === 'catalog_start') {
      status_msg.textContent = `Scanning ${ev.new_files} new files…`;
      log_line(log, `${ev.known_files} already cataloged · ${ev.new_files} new files to scan`);
    } else if (ev.type === 'catalog_file') {
      const bar = document.getElementById('catalog-bar');
      bar.style.width = (ev.current / ev.total * 100) + '%';
      status_msg.textContent = `[${ev.current} / ${ev.total}] ${ev.path.split('/').pop()}`;
      if (ev.current % 10 === 0 || ev.current === ev.total) {
        log_line(log, `[${ev.current}/${ev.total}] ${ev.path}`);
      }
    } else if (ev.type === 'catalog_complete') {
      log_line(log, `Done. Added ${ev.added} entries · ${ev.total_recordings} total recordings.`, 'log-ok');
      status_msg.textContent = `Complete — ${ev.total_recordings} recordings.`;
      document.getElementById('catalog-bar').style.width = '100%';
      src.close();
      finish();
      load_catalog();
    } else if (ev.type === 'cancelled') {
      log_line(log, `Cancelled. Saved ${ev.added} entries so far.`, 'log-err');
      status_msg.textContent = 'Cancelled.';
      src.close();
      finish();
      load_catalog();
    } else if (ev.type === 'error') {
      log_line(log, 'Error: ' + ev.msg, 'log-err');
      status_msg.textContent = 'Error.';
      src.close();
      finish();
    }
  };
  src.onerror = () => { src.close(); finish(); };
}

// ---------------------------------------------------------------------------
// Settings tab
// ---------------------------------------------------------------------------

async function load_settings_form() {
  const res = await fetch('/api/settings');
  const s   = await res.json();
  document.getElementById('s-archive').value      = s.archive_path    || '';
  document.getElementById('s-manifest').value     = s.manifest_path   || '';
  document.getElementById('s-catalog').value      = s.catalog_path    || '';
  document.getElementById('s-camera-base').value  = s.camera_base_path || '';

  document.getElementById('archive-display').textContent = s.archive_path || '(not set)';
}

document.getElementById('settings-form').addEventListener('submit', async e => {
  e.preventDefault();
  const data = {
    archive_path:     document.getElementById('s-archive').value.trim(),
    manifest_path:    document.getElementById('s-manifest').value.trim(),
    catalog_path:     document.getElementById('s-catalog').value.trim(),
    camera_base_path: document.getElementById('s-camera-base').value.trim(),
  };
  await fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data),
  });
  document.getElementById('archive-display').textContent = data.archive_path || '(not set)';
  const saved = document.getElementById('settings-saved');
  saved.classList.remove('hidden');
  setTimeout(() => saved.classList.add('hidden'), 2500);
});

// ---------------------------------------------------------------------------
// Duplicate detection
// ---------------------------------------------------------------------------

async function find_duplicates() {
  const btn = document.getElementById('btn-find-dupes');
  btn.disabled = true;
  btn.textContent = 'Scanning…';

  document.getElementById('dupes-empty').classList.add('hidden');
  document.getElementById('dupes-none').classList.add('hidden');
  document.getElementById('dupes-list').classList.add('hidden');

  try {
    const res = await fetch('/api/catalog/duplicates');
    const data = await res.json();

    if (data.count === 0) {
      document.getElementById('dupes-none').classList.remove('hidden');
      return;
    }

    const container = document.getElementById('dupes-list');
    container.innerHTML = `<p id="dupes-summary">${data.count} duplicate group(s) found — suggested "keep" shown in green, duplicates in orange.</p>`;

    data.groups.forEach(g => {
      const group = document.createElement('div');
      group.className = 'dupe-group';

      group.innerHTML = `<div class="dupe-header">
        ${fmt_date(g.recorded_at)} &nbsp;·&nbsp; ${fmt_duration(g.duration_seconds)} &nbsp;·&nbsp; ${fmt_bytes(g.size_bytes)}
      </div>`;

      const make_row = (entry, cls, verdict) => {
        const row = document.createElement('div');
        row.className = `dupe-row ${cls}`;
        const src_label = {canon_import: 'this tool', legacy_tvd: 'legacy', manual: 'manual'}[entry.source] || entry.source;
        row.innerHTML = `
          <span class="dupe-verdict">${verdict}</span>
          <span class="dupe-path">${entry.path}</span>
          <span class="dupe-meta">${src_label}</span>`;
        return row;
      };

      group.appendChild(make_row(g.keep, 'keep', 'Keep'));
      g.dupes.forEach(d => group.appendChild(make_row(d, 'dupe', 'Dupe')));
      container.appendChild(group);
    });

    container.classList.remove('hidden');
  } catch (e) {
    document.getElementById('dupes-empty').textContent = 'Error: ' + e.message;
    document.getElementById('dupes-empty').classList.remove('hidden');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Find Duplicates';
  }
}

// ---------------------------------------------------------------------------
// Stitch candidates
// ---------------------------------------------------------------------------

let stitch_groups = [];

async function find_stitch_candidates() {
  const btn = document.getElementById('btn-find-stitch');
  btn.disabled = true;
  btn.textContent = 'Scanning…';

  document.getElementById('stitch-empty').classList.add('hidden');
  document.getElementById('stitch-none').classList.add('hidden');
  document.getElementById('stitch-list').classList.add('hidden');
  document.getElementById('stitch-build-progress').classList.add('hidden');

  try {
    const res = await fetch('/api/catalog/stitch-candidates');
    const data = await res.json();
    stitch_groups = data.groups || [];

    if (stitch_groups.length === 0) {
      document.getElementById('stitch-none').classList.remove('hidden');
      return;
    }

    const container = document.getElementById('stitch-list');
    const total_size = stitch_groups.reduce((s, g) =>
      s + g.reduce((gs, r) => gs + (r.size_bytes || 0), 0), 0);

    container.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">
        <p style="font-size:13px;color:var(--muted)">
          ${stitch_groups.length} group(s) found · ${fmt_bytes(total_size)} total · parts will be merged and originals deleted
        </p>
        <button id="btn-stitch-all" class="btn-primary">Stitch All</button>
      </div>`;

    stitch_groups.forEach((g, gi) => {
      const group = document.createElement('div');
      group.className = 'dupe-group';
      const total_dur = g.reduce((s, r) => s + (r.duration_seconds || 0), 0);
      const total_sz  = g.reduce((s, r) => s + (r.size_bytes || 0), 0);
      group.innerHTML = `<div class="dupe-header">
        ${fmt_date(g[0].recorded_at)} &nbsp;·&nbsp; ${fmt_duration(total_dur)} merged &nbsp;·&nbsp; ${fmt_bytes(total_sz)} &nbsp;·&nbsp; ${g.length} parts
      </div>`;
      g.forEach((r, ri) => {
        const row = document.createElement('div');
        row.className = `dupe-row ${ri === g.length - 1 ? 'keep' : 'dupe'}`;
        row.innerHTML = `
          <span class="dupe-verdict">Part ${ri + 1}</span>
          <span class="dupe-path">${r.path}</span>
          <span class="dupe-meta">${fmt_bytes(r.size_bytes)} · ${fmt_duration(r.duration_seconds)}</span>`;
        group.appendChild(row);
      });
      container.appendChild(group);
    });

    container.classList.remove('hidden');
    document.getElementById('btn-stitch-all').addEventListener('click', start_stitch_all);
  } catch (e) {
    document.getElementById('stitch-empty').textContent = 'Error: ' + e.message;
    document.getElementById('stitch-empty').classList.remove('hidden');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Find Candidates';
  }
}

async function start_stitch_all() {
  document.getElementById('btn-stitch-all').disabled = true;
  document.getElementById('btn-find-stitch').disabled = true;

  const progress = document.getElementById('stitch-build-progress');
  const spinner  = document.getElementById('stitch-spinner');
  const status   = document.getElementById('stitch-status-msg');
  const log      = document.getElementById('stitch-log');
  const cancel   = document.getElementById('btn-cancel-stitch');

  progress.classList.remove('hidden');
  log.innerHTML = '';
  spinner.classList.remove('hidden');
  status.textContent = 'Starting…';
  cancel.disabled = false;

  const res = await fetch('/api/catalog/stitch/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({groups: stitch_groups}),
  });
  if (!res.ok) {
    log_line(log, 'Failed to start: ' + (await res.text()), 'log-err');
    spinner.classList.add('hidden');
    document.getElementById('btn-find-stitch').disabled = false;
    return;
  }

  const finish = () => {
    spinner.classList.add('hidden');
    cancel.disabled = true;
    document.getElementById('btn-find-stitch').disabled = false;
  };

  const src = new EventSource('/api/catalog/stitch/stream');
  src.onmessage = e => {
    const ev = JSON.parse(e.data);
    if (ev.type === 'stitch_start') {
      status.textContent = `Stitching ${ev.total_groups} group(s)…`;
    } else if (ev.type === 'stitch_file_start') {
      log_line(log, `[${ev.index+1}/${ev.total}] ${ev.parts.join(' + ')} → ${ev.dest}`, 'log-start');
      status.textContent = `[${ev.index+1}/${ev.total}] ${ev.dest.split('/').pop()}`;
    } else if (ev.type === 'stitch_file_done') {
      log_line(log, `  ✓ merged · removed: ${ev.deleted.join(', ')}`, 'log-ok');
    } else if (ev.type === 'stitch_error') {
      log_line(log, `  ✗ ${ev.error}`, 'log-err');
    } else if (ev.type === 'stitch_warning') {
      log_line(log, `  ⚠ ${ev.msg}`, 'log-info');
    } else if (ev.type === 'info') {
      log_line(log, ev.msg);
    } else if (ev.type === 'stitch_complete') {
      log_line(log, `Done. ${ev.stitched} group(s) stitched, ${ev.errors} error(s).`,
        ev.errors > 0 ? 'log-err' : 'log-ok');
      status.textContent = `Complete — ${ev.stitched} merged.`;
      src.close();
      finish();
      stitch_groups = [];
      document.getElementById('stitch-list').classList.add('hidden');
      document.getElementById('stitch-none').classList.remove('hidden');
      document.getElementById('stitch-none').textContent = `Done — ${ev.stitched} recording(s) stitched.`;
      document.getElementById('stitch-none').style.color = 'var(--success)';
      load_catalog();
    } else if (ev.type === 'cancelled') {
      log_line(log, `Cancelled after ${ev.stitched} group(s).`, 'log-err');
      status.textContent = 'Cancelled.';
      src.close();
      finish();
    } else if (ev.type === 'error') {
      log_line(log, 'Error: ' + ev.msg, 'log-err');
      src.close();
      finish();
    }
  };
  src.onerror = () => { src.close(); finish(); };
}

// ---------------------------------------------------------------------------
// Path browser modal
// ---------------------------------------------------------------------------

let browser_target_id = null;
let browser_current_path = '/';

async function open_browser(target_input_id) {
  browser_target_id = target_input_id;
  const current_val = document.getElementById(target_input_id).value.trim();
  await browse_to(current_val || '/');
  document.getElementById('browser-modal').classList.remove('hidden');
}

async function browse_to(path) {
  const res = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
  if (!res.ok) {
    const err = await res.json();
    alert('Cannot browse: ' + (err.error || res.statusText));
    return;
  }
  const data = await res.json();
  browser_current_path = data.path;

  document.getElementById('browser-breadcrumb').textContent = data.path;
  document.getElementById('browser-selected-path').textContent = data.path;

  const list = document.getElementById('browser-list');
  list.innerHTML = '';

  if (data.parent) {
    const up = document.createElement('div');
    up.className = 'browser-entry up';
    up.innerHTML = '<span class="browser-icon">&#x2B06;</span> .. (up one level)';
    up.addEventListener('click', () => browse_to(data.parent));
    list.appendChild(up);
  }

  if (!data.dirs.length) {
    const empty = document.createElement('div');
    empty.className = 'browser-entry';
    empty.style.color = 'var(--muted)';
    empty.textContent = '(no subdirectories)';
    list.appendChild(empty);
  }

  data.dirs.forEach(d => {
    const row = document.createElement('div');
    row.className = 'browser-entry';
    row.innerHTML = `<span class="browser-icon">&#x1F4C1;</span>${d.name}`;
    row.addEventListener('click', () => browse_to(d.path));
    list.appendChild(row);
  });
}

function close_browser() {
  document.getElementById('browser-modal').classList.add('hidden');
  browser_target_id = null;
}

document.getElementById('browser-close').addEventListener('click', close_browser);
document.getElementById('browser-cancel').addEventListener('click', close_browser);
document.getElementById('browser-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) close_browser();
});
document.getElementById('browser-select').addEventListener('click', () => {
  if (browser_target_id) {
    document.getElementById(browser_target_id).value = browser_current_path;
    if (browser_target_id === 's-archive') {
      document.getElementById('archive-display').textContent = browser_current_path;
    }
  }
  close_browser();
});

document.querySelectorAll('.btn-browse').forEach(btn => {
  btn.addEventListener('click', () => open_browser(btn.dataset.target));
});

// ---------------------------------------------------------------------------
// Wire up buttons
// ---------------------------------------------------------------------------

document.getElementById('btn-detect').addEventListener('click', detect_cameras);
document.getElementById('btn-scan').addEventListener('click', scan_camera);
document.getElementById('btn-import').addEventListener('click', start_import);
document.getElementById('btn-cancel-import').addEventListener('click', cancel_job);
document.getElementById('btn-eject').addEventListener('click', async () => {
  const btn = document.getElementById('btn-eject');
  btn.disabled = true;
  btn.textContent = 'Ejecting…';
  try {
    const res = await fetch('/api/eject', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mounts: selected_mounts()}),
    });
    const data = await res.json();
    if (data.ok) {
      btn.classList.add('hidden');
      document.getElementById('safe-to-unplug').classList.remove('hidden');
      // Clear camera list since volumes are now unmounted
      detected_cameras = [];
      render_camera_list();
    } else {
      const errors = data.results.filter(r => !r.ok).map(r => r.error).join('; ');
      btn.disabled = false;
      btn.textContent = '⏏ Eject Camera';
      alert('Eject failed: ' + errors);
    }
  } catch (e) {
    btn.disabled = false;
    btn.textContent = '⏏ Eject Camera';
    alert('Eject error: ' + e.message);
  }
});
document.getElementById('btn-build-catalog').addEventListener('click', build_catalog);
document.getElementById('btn-find-dupes').addEventListener('click', find_duplicates);
document.getElementById('btn-cancel-catalog').addEventListener('click', cancel_job);
document.getElementById('btn-find-stitch').addEventListener('click', find_stitch_candidates);
document.getElementById('btn-cancel-stitch').addEventListener('click', cancel_job);
document.getElementById('filter-year').addEventListener('change', apply_catalog_filters);
document.getElementById('filter-source').addEventListener('change', apply_catalog_filters);
document.getElementById('filter-glacier').addEventListener('change', apply_catalog_filters);
document.getElementById('filter-search').addEventListener('input', apply_catalog_filters);

document.getElementById('btn-goto-settings').addEventListener('click', () => {
  document.querySelector('.tab-btn[data-tab="settings"]').click();
});

// ---------------------------------------------------------------------------
// Status bar
// ---------------------------------------------------------------------------

async function load_status() {
  const bar = document.getElementById('status-bar');
  try {
    const res = await fetch('/api/status');
    const s = await res.json();

    const fmt_date_short = iso => iso ? iso.replace('T', ' ').slice(0, 16) : null;

    const items = [
      {
        label: 'Archive',
        ok: s.archive.ok,
        detail: s.archive.ok ? s.archive.path : `Not reachable: ${s.archive.path}`,
        level: s.archive.ok ? 'ok' : 'error',
      },
      {
        label: 'Manifest',
        ok: s.manifest.exists,
        detail: s.manifest.exists ? `${s.manifest.entries} entries` : 'Not found',
        level: s.manifest.exists ? 'ok' : 'warn',
      },
      {
        label: 'Catalog',
        ok: s.catalog.exists,
        detail: s.catalog.exists
          ? `${s.catalog.recordings} recordings · last built ${fmt_date_short(s.catalog.updated) || 'unknown'}`
          : 'Not built yet',
        level: s.catalog.exists ? 'ok' : 'warn',
      },
    ];

    const worst = items.some(i => i.level === 'error') ? 'error'
                : items.some(i => i.level === 'warn')  ? 'warn' : 'ok';

    bar.className = `status-bar ${worst}`;
    bar.innerHTML = items.map(i => `
      <div class="status-item">
        <span class="status-dot ${i.level}"></span>
        <span><strong>${i.label}:</strong> ${i.detail}</span>
      </div>`).join('');
    bar.classList.remove('hidden');
  } catch (e) {
    bar.className = 'status-bar error';
    bar.innerHTML = `<div class="status-item"><span class="status-dot error"></span><span>Could not load status</span></div>`;
    bar.classList.remove('hidden');
  }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

(async () => {
  await load_status();
  await load_settings_form();
  await detect_cameras();
})();
