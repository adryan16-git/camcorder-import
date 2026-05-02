#!/usr/bin/env python3
"""
Canon Import Tool — Flask web server.
Run with: python app.py
Then open: http://localhost:8080
"""

import json
import os
import queue
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

import camcorder_import as ci

app = Flask(__name__)

SETTINGS_FILE = Path(__file__).parent / "settings.json"
SETTINGS_EXAMPLE_FILE = Path(__file__).parent / "settings.example.json"

_user = os.environ.get("USER") or os.environ.get("LOGNAME") or "user"
DEFAULT_SETTINGS = {
    "archive_path": "/mnt/8tb/Camcorder Videos",
    "manifest_path": "",
    "catalog_path": "",
    "camera_base_path": f"/media/{_user}",
}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE) as f:
            return {**DEFAULT_SETTINGS, **json.load(f)}
    return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict) -> None:
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


def resolved_paths(settings: dict) -> tuple[Path, Path, Path]:
    archive = Path(settings["archive_path"])
    manifest = Path(settings["manifest_path"]) if settings.get("manifest_path") else archive / "manifest.json"
    catalog = Path(settings["catalog_path"]) if settings.get("catalog_path") else archive / "catalog.json"
    return archive, manifest, catalog


# ---------------------------------------------------------------------------
# Job state (one job at a time — single-user tool)
# ---------------------------------------------------------------------------

_job_lock = threading.Lock()
_job = {"type": None, "status": "idle", "q": None}
_cancel_event = threading.Event()


def _start_job(job_type: str, target_fn, *args):
    global _job
    with _job_lock:
        if _job["status"] == "running":
            return False
        _cancel_event.clear()
        q = queue.Queue()
        _job = {"type": job_type, "status": "running", "q": q}

    def run():
        try:
            for event in target_fn(*args):
                q.put(event)
        except Exception as exc:
            q.put({"type": "error", "msg": str(exc)})
        finally:
            with _job_lock:
                _job["status"] = "done"
            q.put(None)  # sentinel

    threading.Thread(target=run, daemon=True).start()
    return True


def _sse_stream(expected_type: str):
    with _job_lock:
        if _job["type"] != expected_type or _job["q"] is None:
            yield f"data: {json.dumps({'type': 'error', 'msg': 'No active job of this type'})}\n\n"
            return
        q = _job["q"]

    while True:
        try:
            event = q.get(timeout=120)
        except queue.Empty:
            yield "data: {\"type\": \"heartbeat\"}\n\n"
            continue
        if event is None:
            break
        yield f"data: {json.dumps(event)}\n\n"


# ---------------------------------------------------------------------------
# Routes — pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Routes — settings
# ---------------------------------------------------------------------------

@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify(load_settings())


@app.route("/api/settings", methods=["POST"])
def post_settings():
    data = request.json or {}
    settings = load_settings()
    for key in DEFAULT_SETTINGS:
        if key in data:
            settings[key] = data[key]
    save_settings(settings)
    return jsonify({"status": "saved"})


# ---------------------------------------------------------------------------
# Routes — camera detection & scan
# ---------------------------------------------------------------------------

@app.route("/api/detect")
def detect():
    settings = load_settings()
    cameras = ci.detect_cameras(settings.get("camera_base_path"))
    return jsonify(cameras)


@app.route("/api/scan", methods=["POST"])
def scan():
    data = request.json or {}
    mounts = [Path(m) for m in data.get("mounts", [])]
    if not mounts:
        return jsonify({"error": "No mounts specified"}), 400
    settings = load_settings()
    _, manifest_path, _ = resolved_paths(settings)
    try:
        recordings = ci.scan_new_recordings(mounts, manifest_path)
        return jsonify(recordings)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Routes — import
# ---------------------------------------------------------------------------

@app.route("/api/import/start", methods=["POST"])
def import_start():
    data = request.json or {}
    mounts = [Path(m) for m in data.get("mounts", [])]
    if not mounts:
        return jsonify({"error": "No mounts specified"}), 400
    dry_run = bool(data.get("dry_run", False))
    settings = load_settings()
    archive, manifest_path, catalog_path = resolved_paths(settings)
    if not _start_job("import", ci.run_import, mounts, archive, manifest_path, catalog_path, dry_run):
        return jsonify({"error": "A job is already running"}), 409
    return jsonify({"status": "started"})


@app.route("/api/import/stream")
def import_stream():
    return Response(
        stream_with_context(_sse_stream("import")),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Routes — catalog
# ---------------------------------------------------------------------------

@app.route("/api/catalog")
def get_catalog():
    settings = load_settings()
    _, _, catalog_path = resolved_paths(settings)
    catalog = ci.load_catalog(catalog_path)
    return jsonify(catalog)


@app.route("/api/catalog/duplicates")
def catalog_duplicates():
    settings = load_settings()
    _, _, catalog_path = resolved_paths(settings)
    catalog = ci.load_catalog(catalog_path)

    # Group by (recorded_at, duration_seconds, size_bytes)
    groups: dict[tuple, list] = {}
    for r in catalog["recordings"]:
        key = (r.get("recorded_at"), r.get("duration_seconds"), r.get("size_bytes"))
        if None in key:
            continue
        groups.setdefault(key, []).append(r)

    source_rank = {"canon_import": 0, "legacy_tvd": 1, "manual": 2}

    def preferred(entries):
        def score(r):
            s = source_rank.get(r.get("source", "manual"), 2)
            has_suffix = bool(__import__("re").search(r"_\d+\.mts$", r["path"]))
            return (s, has_suffix, len(r["path"]))
        return sorted(entries, key=score)

    duplicates = []
    for key, entries in groups.items():
        if len(entries) < 2:
            continue
        ranked = preferred(entries)
        duplicates.append({
            "recorded_at": key[0],
            "duration_seconds": key[1],
            "size_bytes": key[2],
            "keep": ranked[0],
            "dupes": ranked[1:],
        })

    duplicates.sort(key=lambda d: d["recorded_at"] or "")
    return jsonify({"count": len(duplicates), "groups": duplicates})


@app.route("/api/eject", methods=["POST"])
def eject():
    import subprocess
    mounts = (request.json or {}).get("mounts", [])
    if not mounts:
        return jsonify({"error": "No mounts specified"}), 400
    results = []
    for mount in mounts:
        try:
            # Resolve block device from mount point
            dev = subprocess.run(
                ["findmnt", "-n", "-o", "SOURCE", mount],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if not dev:
                results.append({"mount": mount, "ok": False, "error": "mount point not found"})
                continue
            r = subprocess.run(
                ["udisksctl", "unmount", "-b", dev],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                results.append({"mount": mount, "ok": True})
            else:
                results.append({"mount": mount, "ok": False, "error": r.stderr.strip()})
        except Exception as e:
            results.append({"mount": mount, "ok": False, "error": str(e)})
    all_ok = all(r["ok"] for r in results)
    return jsonify({"ok": all_ok, "results": results})


@app.route("/api/cancel", methods=["POST"])
def cancel():
    _cancel_event.set()
    return jsonify({"status": "cancel requested"})


@app.route("/api/catalog/build/start", methods=["POST"])
def catalog_build_start():
    settings = load_settings()
    archive, _, catalog_path = resolved_paths(settings)
    if not _start_job("catalog_build", ci.build_catalog_from_archive, archive, catalog_path, _cancel_event):
        return jsonify({"error": "A job is already running"}), 409
    return jsonify({"status": "started"})


@app.route("/api/catalog/build/stream")
def catalog_build_stream():
    return Response(
        stream_with_context(_sse_stream("catalog_build")),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Routes — filesystem browser (for settings path picker)
# ---------------------------------------------------------------------------

@app.route("/api/browse")
def browse():
    path = request.args.get("path", "/")
    p = Path(path).resolve()
    if not p.exists() or not p.is_dir():
        return jsonify({"error": "Not a directory"}), 400
    try:
        entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        dirs = [
            {"name": e.name, "path": str(e)}
            for e in entries
            if e.is_dir() and not e.name.startswith(".")
        ]
        return jsonify({
            "path": str(p),
            "parent": str(p.parent) if str(p) != str(p.parent) else None,
            "dirs": dirs,
        })
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True, debug=False)
