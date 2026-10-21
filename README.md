# Canon Import Tool

A Linux utility for importing footage from Canon AVCHD camcorders (HFM52 and similar).
Replaces Windows-only software like Pixela VideoBrowser with a clean, duplicate-free workflow.

## Features

- **Duplicate-safe** — manifest tracks every imported file by name + size; re-running never re-imports
- **Multi-part support** — losslessly concatenates recordings split across FAT32 4 GB boundaries via ffmpeg
- **Web UI** — browser-based interface with live progress, runs on desktop or headless server
- **Catalog** — full inventory of your archive, buildable from existing footage; Glacier-ready JSON format
- **Duplicate detection** — finds matching recordings across the archive (catches `_2` copies, old imports)
- **Safe eject** — unmounts camera volumes via `udisksctl` before prompting to unplug
- **CLI** — all functionality also available as a standalone command-line script

## Requirements

- Linux
- Python 3.8+
- `ffmpeg` / `ffprobe`
- `udisksctl` (for eject — included in most distros as part of `udisks2`)

## Install

```bash
git clone https://github.com/your-username/camcorder-import.git
cd camcorder-import
bash install.sh
```

The installer will:
1. Verify Python 3.8+ and ffprobe are available
2. Create a local virtualenv at `./venv/`
3. Install `flask` and `boto3`
4. Copy `settings.example.json` → `settings.json` for you to edit
5. Create `launch.sh`, a "Canon Import Tool" application-menu entry, a
   `camcorder` command (`~/.local/bin/camcorder`), and a user-level systemd
   service with boot auto-start (all paths are derived from the install
   location, so a new machine just needs the clone + install)

## Configure

Edit `settings.json` (or use the Settings tab in the UI):

```json
{
  "archive_path": "/mnt/nas/Camcorder Videos",
  "manifest_path": "",
  "catalog_path":  "",
  "camera_base_path": "/media/your-username"
}
```

| Setting | Default | Description |
|---|---|---|
| `archive_path` | — | Root folder where `YYYY/` year directories are written |
| `manifest_path` | `<archive>/manifest.json` | Import dedup log (leave blank for default) |
| `catalog_path` | `<archive>/catalog.json` | Archive inventory (leave blank for default) |
| `camera_base_path` | `/media/$USER` | Where the OS mounts camera volumes |

## Before running

**Mount your NAS** (if applicable) so `archive_path` is accessible:

```bash
sudo mount -t cifs "//192.168.1.x/ShareName" /mnt/nas -o guest,uid=$(id -u),gid=$(id -g)
```

**Connect the camera** via USB — the OS will mount it automatically (usually under `/media/$USER/`).

> **Important:** The app must run on the same machine the camera is plugged into.
> The browser can be on any device on your LAN.

## Start the app

`bash install.sh` (see above) sets up every method below with the correct paths.

**Easiest** — one command that health-checks the server, starts it via the venv
if it isn't already running, then opens the browser:

```bash
~/projects/camcorder-import/launch.sh   # "Canon Import Tool" in the app menu
camcorder                               # same thing: symlink in ~/.local/bin
```

Log output goes to `~/.cache/camcorder-import.log`.

**Manually:**

```bash
./venv/bin/python app.py
```

Then open **http://localhost:8080** in a browser (desktop entry also available as
"Canon Import Tool" in the applications menu).

## Usage

### Import tab

1. Click **Detect Camera** — finds all connected AVCHD volumes (Internal Memory, SD Card)
2. Select which volumes to import (Internal / SD Card / both)
3. Click **Scan Camera** — runs ffprobe on each file; shows a preview table with new vs already-imported badges
4. Review the list, then click **Confirm & Import**
5. Watch live progress; each file is logged as it copies
6. When complete, click **Eject Camera** → wait for **✅ Safe to unplug**

### Catalog tab

- **Build / Update Catalog** — walks your archive folder, runs ffprobe on every `.mts` file not yet known, and writes `catalog.json`. Handles both the new `YYYY/YYYYMMDD_HHMMSS.mts` layout and old `TVD_AVCHD/YYYYMMDD/` folders from VideoBrowser. Incremental — only scans new files on subsequent runs.
- **Find Duplicates** — groups catalog entries by recorded timestamp + duration + size; surfaces any with multiple matches. Suggests which to keep (prefers clean imports over legacy files, non-suffixed names over `_2` copies).
- Filter the inventory by year, source, or Glacier status.

### Settings tab

Set paths using the **Browse…** button (navigates the server filesystem) or type them directly.

## CLI usage

The core script works standalone without the web UI:

```bash
# Import from a mounted camera
./venv/bin/python camcorder_import.py /media/user/CANON

# Dry run — show what would be imported without writing anything
./venv/bin/python camcorder_import.py --dry-run /media/user/CANON

# Custom archive and manifest paths
./venv/bin/python camcorder_import.py \
  --archive /mnt/nas/Camcorder\ Videos \
  /media/user/CANON

# Detect connected cameras
./venv/bin/python camcorder_import.py --detect

# Build/update catalog from an existing archive
./venv/bin/python camcorder_import.py --build-catalog \
  --archive /mnt/nas/Camcorder\ Videos
```

## Data files

Two JSON files live in the archive root:

**`manifest.json`** — import dedup log (camera-centric, keyed by `filename|size`).
Written after every successful import. Moving to a new machine: copy this file to preserve import history.

**`catalog.json`** — archive inventory (content-centric, keyed by archive path).
Built by the Catalog tab. Each entry includes `glacier_archived` and `glacier_archive_id` fields
as placeholders for a future Glacier sync script. Portable: copy to a new machine and run
Build/Update — it adds new files and preserves all existing metadata including Glacier flags.

## Archive layout

```
/mnt/nas/Camcorder Videos/
├── 2022/
│   └── 20220625_084812.mts
├── 2023/
│   └── 20230221_130127.mts
├── 2025/
│   └── 20251224_125208.mts
├── manifest.json
└── catalog.json
```

## Running as a service

`bash install.sh` installs and enables a **user-level systemd service** so the
app starts at boot and auto-restarts on crash — your browser bookmark to
`http://localhost:8080` then always works, no launcher needed. It also enables
`loginctl enable-linger` so the service starts even before you log in.

The unit lives at `~/.config/systemd/user/canon-import.service`:

```ini
[Unit]
Description=Canon Import Tool (Flask web UI on port 8080)
After=default.target

[Service]
WorkingDirectory=/path/to/camcorder-import
ExecStart=/path/to/camcorder-import/venv/bin/python app.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
```

Manage it with:

```bash
systemctl --user status canon-import    # check state / recent log lines
journalctl --user -u canon-import -f    # follow logs
systemctl --user restart canon-import   # restart
systemctl --user disable canon-import   # turn off boot-start
```

Notes:

- Stop any manually started copy before enabling the service, or the port will
  clash: `pkill -f "venv/bin/python app.py"`
- If the machine reboots before systemd has taken over, `launch.sh` (menu item
  or `~/.local/bin/camcorder`) still works as a fallback: it starts the server
  itself if systemd hasn't.

The UI will be available at `http://localhost:8080` (or the machine's LAN IP).

## Camera compatibility

Tested on:
- Canon HFM52 (AVCHD, `.MTS` files)
  - Internal memory: `AVCHD/BDMV/STREAM/`
  - SD card: `PRIVATE/AVCHD/BDMV/STREAM/`

Should work with any Canon AVCHD camcorder that mounts as USB mass storage.
The script also checks `BDMV/STREAM/` directly for other brands.

## License

MIT
