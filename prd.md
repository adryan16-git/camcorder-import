# Canon HFM52 Import Tool — PRD

---

## v1 — CLI (Shipped ✅)

### Problem
Pixela VideoBrowser (Windows abandonware) re-imports all camera files on every session,
creating hundreds of GBs of duplicates (~960GB found in 2026 cleanup). No Linux alternative
exists that handles the full AVCHD workflow.

### Solution
A Python CLI script (`camcorder_import.py`) that runs on Linux, imports only new files,
handles split recordings, and writes to a clean archive.

### Shipped Features
- Detects AVCHD camera mount (handles `BDMV/STREAM`, `AVCHD/BDMV/STREAM`,
  `PRIVATE/AVCHD/BDMV/STREAM` — Canon's actual on-disk layouts)
- Identifies new MTS files not yet in manifest (key: `filename|size`)
- Groups multi-part recordings via ffprobe timestamp comparison + size fallback
- Losslessly concatenates multi-part recordings via `ffmpeg -f concat -c copy`
- Writes to archive: `YYYY/YYYYMMDD_HHMMSS.mts`
- Updates `manifest.json` after each successful import
- `--dry-run` mode

### CLI Usage
```bash
python3 camcorder_import.py [--dry-run] [--archive DIR] [--manifest FILE] <mount_point>
```

### Scope Out (v1)
- No re-encoding (lossless only)
- No Glacier/S3 upload (separate step)
- No GUI
- No scheduled/background watching
- No automatic USB mount detection

---

## v2 — Web UI (Planned)

### Motivation
The CLI works but requires knowing the mount point, running the script twice for two volumes,
and watching terminal output. The goal is a minimal browser-based UI that makes the
import workflow self-contained: plug in camera, open browser tab, confirm and copy.

A second file, `catalog.json`, is introduced as a full archive inventory — separate from
the import manifest and designed to be open enough for future use cases like Glacier comparison.

### Architecture

```
┌─────────────────────────────────────┐
│  Browser (any device on LAN)        │
│  http://localhost:8080              │
└──────────────┬──────────────────────┘
               │ HTTP
┌──────────────▼──────────────────────┐
│  Flask app (app.py)                 │
│  Runs on the same machine as camera │
│  Serves UI + wraps camcorder_import │
└──────────────┬──────────────────────┘
               │ Python calls
┌──────────────▼──────────────────────┐
│  camcorder_import.py (core logic)   │
│  ffprobe / ffmpeg                   │
│  Camera USB mount                   │
│  NAS (mounted locally)              │
└─────────────────────────────────────┘
```

**Key constraint:** The Flask server and the camera must be on the same physical machine.
The browser can be anywhere on the LAN. This is normal — the browser is just a display.

### Data Files

Two separate files with distinct purposes:

#### `manifest.json` — Import log (camera-centric)
Keyed by `filename|size`. Sole purpose: deduplication. "Have I pulled this file off
a camera before?" Fast to look up, camera-centric. Written by the import process only.

```json
{
  "imported": {
    "00001.MTS|2047100928": {
      "camera_file": "AVCHD/BDMV/STREAM/00001.MTS",
      "size": 2047100928,
      "import_date": "2026-05-01",
      "output": "2025/20251224_125208.mts",
      "parts": ["00001.MTS"]
    }
  }
}
```

#### `catalog.json` — Archive inventory (content-centric)
Keyed by archive path. Purpose: full inventory of everything in the collection,
regardless of how it got there. Built by scanning the archive folder with ffprobe.
Captures old VideoBrowser imports (`TVD_AVCHD/`) as well as new imports.

Designed to be open enough for future Glacier comparison: `path` + `size_bytes`
map directly to Fast Glacier inventory exports (filename + size). The
`glacier_archived` and `glacier_archive_id` fields are placeholders — this tool
never uploads to Glacier, but a future script can fill these in during a sync check.

```json
{
  "version": 1,
  "updated": "2026-05-01",
  "recordings": [
    {
      "path": "2025/20251224_125208.mts",
      "recorded_at": "2025-12-24T12:52:08",
      "duration_seconds": 681,
      "size_bytes": 2046965760,
      "imported_at": "2026-05-01",
      "source": "canon_import",
      "glacier_archived": false,
      "glacier_archive_id": null
    }
  ]
}
```

**`source` values:**
- `"canon_import"` — imported by this tool
- `"legacy_tvd"` — found in existing `TVD_AVCHD/` archive, scanned in via Catalog tab
- `"manual"` — added by any other means

Both files live in the archive root alongside the `YYYY/` folders. Both are plain JSON —
processable with Python, `jq`, a spreadsheet, or any future tooling.

---

### UI — Three Tabs

#### Tab 1: Import
1. **Camera detect** — button scans `/media/$USER/` for any AVCHD structure;
   shows detected volumes with labels (Internal Memory / SD Card)
2. **Volume selector** — toggle: Internal / SD Card / Both
3. **Archive path** — text field pre-filled from settings, with Browse button
4. **Scan** — quick ffprobe pass; shows a preview table:
   - Date recorded, duration, file size, already-imported badge (grey) vs new (green)
5. **Confirm & Import** — starts the copy; disabled until scan is complete
6. **Progress** — overall progress bar (GB copied / GB total), per-file status log
   scrolling in real time via Server-Sent Events

#### Tab 2: Catalog
- **Build / update inventory** — scans the archive folder with ffprobe and upserts
  entries into `catalog.json`; skips already-known paths (incremental)
  - Handles new `YYYY/YYYYMMDD_HHMMSS.mts` layout (`source: canon_import`)
  - Handles old `TVD_AVCHD/YYYYMMDD/` layout from VideoBrowser (`source: legacy_tvd`)
- **Inventory table** — browsable, filterable by year and source; columns: date
  recorded, duration, size, path, Glacier status
- **Stats bar** — total recordings, total hours, total GB, % archived to Glacier

#### Tab 3: Settings
- Archive path (default: `/mnt/8tb/Camcorder Videos`)
- Manifest path (default: `<archive>/manifest.json`)
- Catalog path (default: `<archive>/catalog.json`)
- Camera mount base path (default: `/media/$USER`, used for auto-detect)
- Settings saved to `settings.json` in the app directory

### File Structure
```
camcorder-import/
├── camcorder_import.py     # Core logic (CLI still works standalone)
├── app.py                  # Flask web server
├── templates/
│   └── index.html          # Single-page UI (tabs)
├── static/
│   ├── app.css
│   └── app.js
├── settings.json           # User settings (gitignored)
├── settings.example.json   # Committed defaults
├── requirements.txt        # flask
├── install.sh              # MVP install script (Option A)
├── prd.md
└── README.md
```

### Key Technical Notes
- Progress streaming via **Server-Sent Events** (SSE) — no websocket dependency,
  works with plain Flask, real-time log lines pushed to browser
- `camcorder_import.py` core logic stays import-safe (no `sys.exit` in library paths,
  yields progress events instead of printing) so `app.py` can call it directly
- `settings.json` is gitignored; `settings.example.json` is committed with defaults
- The CLI (`camcorder_import.py`) continues to work standalone — the web UI is
  a frontend, not a replacement

### Dependencies
- Python 3.8+
- `flask` (pip)
- `ffmpeg` + `ffprobe` (system)
- No other pip packages

### install.sh (MVP — Option A)
Script should:
1. Check Python 3.8+ and ffprobe are present; exit with clear error if not
2. Create a virtualenv at `./venv/`
3. `pip install flask` into the venv
4. Print how to run: `./venv/bin/python app.py`
5. Optionally offer to create a `.desktop` launcher file for the current user

### README.md should cover
- What this tool is and what camera it supports
- Prerequisites (Python 3.8+, ffmpeg, Linux)
- Install: `git clone` + `bash install.sh`
- Run: `python app.py` (or via venv)
- How to mount the NAS / camera before running
- How to use each tab
- Note on the same-machine requirement (camera + server on same host)
- How to run as a systemd service (post-MVP, but document it)

### Out of Scope (v2 MVP)
- Option B install script / systemd service (post-MVP, document in README)
- Re-encoding or format conversion
- Glacier/S3 upload
- Windows or macOS support
- Authentication on the web UI (LAN-only tool)
- Thumbnail previews of video content

### Post-MVP
- `install.sh` Option B: systemd unit file, auto-start on boot
- File integrity verification (size check or checksum after copy)
- `--status` CLI flag: print manifest summary without importing
- Handle both volumes in one CLI invocation (auto-detect all Canon mounts)
- Glacier comparison script: diff `catalog.json` against a Fast Glacier inventory
  export (CSV) and report what's missing; update `glacier_archived` /
  `glacier_archive_id` fields on confirmed uploads
