#!/usr/bin/env python3
"""
Canon HFM52 AVCHD import utility.
Imports only new MTS files from a mounted camera, losslessly concatenates
multi-part recordings, and archives with timestamp-based naming.

Usage (CLI):
    python3 camcorder_import.py [--dry-run] [--archive DIR] [--manifest FILE] <mount_point>

Can also be imported as a module by app.py.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ARCHIVE_DEFAULT = "/mnt/8tb/Camcorder Videos"
PART_SIZE_THRESHOLD = 1_900_000_000  # camera splits recordings at ~2GB


# ---------------------------------------------------------------------------
# Camera detection
# ---------------------------------------------------------------------------

def find_stream_dir(mount: Path) -> Path | None:
    candidates = [
        mount / "BDMV" / "STREAM",
        mount / "AVCHD" / "BDMV" / "STREAM",
        mount / "PRIVATE" / "AVCHD" / "BDMV" / "STREAM",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    for match in mount.rglob("STREAM"):
        if match.is_dir() and match.parent.name.upper() == "BDMV":
            return match
    return None


def get_mts_files(stream_dir: Path) -> list[Path]:
    return sorted(
        f for f in stream_dir.iterdir()
        if f.suffix.upper() == ".MTS" and f.is_file()
    )


def detect_cameras(base_path: str = None) -> list[dict]:
    """Scan base_path for mounted AVCHD camera volumes."""
    if base_path is None:
        user = os.environ.get("USER") or os.environ.get("LOGNAME") or "user"
        base_path = f"/media/{user}"
    base = Path(base_path)
    if not base.exists():
        return []
    cameras = []
    for mount in sorted(base.iterdir()):
        if not mount.is_dir():
            continue
        stream_dir = find_stream_dir(mount)
        if stream_dir is None:
            continue
        rel = str(stream_dir.relative_to(mount)).upper()
        if rel.startswith("PRIVATE"):
            volume_type = "SD Card"
        elif rel.startswith("AVCHD"):
            volume_type = "Internal Memory"
        else:
            volume_type = "Unknown"
        files = get_mts_files(stream_dir)
        cameras.append({
            "label": mount.name,
            "mount": str(mount),
            "volume_type": volume_type,
            "file_count": len(files),
        })
    return cameras


# ---------------------------------------------------------------------------
# Manifest (import dedup log)
# ---------------------------------------------------------------------------

def manifest_key(f: Path) -> str:
    return f"{f.name}|{f.stat().st_size}"


def load_manifest(manifest_path: Path) -> dict:
    if manifest_path.exists():
        with open(manifest_path) as fh:
            return json.load(fh)
    return {"imported": {}}


def save_manifest(manifest_path: Path, manifest: dict) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, indent=2)


# ---------------------------------------------------------------------------
# Catalog (archive inventory)
# ---------------------------------------------------------------------------

def load_catalog(catalog_path: Path) -> dict:
    if catalog_path.exists():
        with open(catalog_path) as fh:
            return json.load(fh)
    return {"version": 1, "updated": None, "recordings": []}


def save_catalog(catalog_path: Path, catalog: dict) -> None:
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    with open(catalog_path, "w") as fh:
        json.dump(catalog, fh, indent=2)


# ---------------------------------------------------------------------------
# ffprobe helpers
# ---------------------------------------------------------------------------

def ffprobe_creation_time(path: Path) -> datetime | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_entries", "format_tags=creation_time", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        ts = data.get("format", {}).get("tags", {}).get("creation_time", "")
        if ts:
            return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
    except Exception:
        pass
    return None


def ffprobe_duration(path: Path) -> float | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_entries", "format=duration", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        dur = data.get("format", {}).get("duration")
        if dur:
            return float(dur)
    except Exception:
        pass
    return None


def get_timestamp(path: Path) -> datetime:
    ts = ffprobe_creation_time(path)
    if ts:
        return ts
    return datetime.fromtimestamp(path.stat().st_mtime)


# ---------------------------------------------------------------------------
# Recording grouping
# ---------------------------------------------------------------------------

def _sequential_filenames(a: Path, b: Path) -> bool:
    """True if b's numeric stem immediately follows a's (e.g. 00002 → 00003)."""
    try:
        return int(b.stem) == int(a.stem) + 1
    except ValueError:
        return False


def group_into_recordings(files: list[Path]) -> list[list[Path]]:
    """Group consecutive MTS files that belong to the same recording.

    Primary: ffprobe timestamp gap ≤ 2 s (for cameras that embed creation_time).
    Fallback: prev file is at the size limit AND filenames are sequential
              (Canon HFM52 splits at ~2 GB with no timestamp metadata).
    """
    if not files:
        return []
    groups: list[list[Path]] = []
    current_group = [files[0]]
    for i in range(1, len(files)):
        prev, curr = files[i - 1], files[i]
        is_continuation = False

        prev_start = ffprobe_creation_time(prev)
        prev_dur = ffprobe_duration(prev)
        curr_start = ffprobe_creation_time(curr)
        if prev_start and prev_dur and curr_start:
            gap = curr_start.timestamp() - (prev_start.timestamp() + prev_dur)
            if abs(gap) <= 2.0:
                is_continuation = True

        if not is_continuation:
            if prev.stat().st_size >= PART_SIZE_THRESHOLD and _sequential_filenames(prev, curr):
                is_continuation = True

        if is_continuation:
            current_group.append(curr)
        else:
            groups.append(current_group)
            current_group = [curr]
    groups.append(current_group)
    return groups


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------

def output_path(archive: Path, ts: datetime, parts: list[Path]) -> Path:
    return archive / str(ts.year) / (ts.strftime("%Y%m%d_%H%M%S") + ".mts")


def resolve_conflict(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, parent = dest.stem, dest.parent
    i = 2
    while True:
        candidate = parent / f"{stem}_{i}.mts"
        if not candidate.exists():
            return candidate
        i += 1


def concat_or_copy(parts: list[Path], dest: Path, dry_run: bool) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if len(parts) == 1:
        if dry_run:
            return True
        shutil.copy2(parts[0], dest)
        return True
    list_file = dest.parent / f".concat_{dest.stem}.txt"
    try:
        with open(list_file, "w") as fh:
            for p in parts:
                fh.write(f"file '{p}'\n")
        if dry_run:
            list_file.unlink(missing_ok=True)
            return True
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(list_file), "-c", "copy", str(dest)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return False
        return True
    except Exception:
        return False
    finally:
        list_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Scan (preview without copying)
# ---------------------------------------------------------------------------

def scan_new_recordings(mounts: list[Path], manifest_path: Path,
                        archive: Path = None) -> list[dict]:
    """
    Scan camera volumes and return structured recording list.
    Each entry includes already_imported flag for UI preview.
    """
    manifest = load_manifest(manifest_path)
    already_imported = set(manifest["imported"].keys())
    recordings = []
    for mount in mounts:
        stream_dir = find_stream_dir(mount)
        if stream_dir is None:
            continue
        files = get_mts_files(stream_dir)
        groups = group_into_recordings(files)
        for group in groups:
            ts = get_timestamp(group[0])
            duration = sum(ffprobe_duration(f) or 0.0 for f in group)
            size_bytes = sum(f.stat().st_size for f in group)
            all_in_manifest = all(manifest_key(f) in already_imported for f in group)

            # For multi-part groups, verify the merged output exists at the right size.
            # Files imported individually before stitch logic existed will either be
            # missing or be only one part's worth of bytes — both need re-stitching.
            needs_stitch = False
            if all_in_manifest and len(group) > 1 and archive is not None:
                expected = output_path(archive, ts, group)
                if not expected.exists() or expected.stat().st_size < size_bytes * 0.9:
                    needs_stitch = True

            already = all_in_manifest and not needs_stitch
            recordings.append({
                "mount": str(mount),
                "files": [f.name for f in group],
                "file_paths": [str(f) for f in group],
                "recorded_at": ts.isoformat(),
                "duration_seconds": round(duration),
                "size_bytes": size_bytes,
                "already_imported": already,
                "needs_stitch": needs_stitch,
                "is_multipart": len(group) > 1,
            })
    return recordings


# ---------------------------------------------------------------------------
# Import (generator — yields progress events)
# ---------------------------------------------------------------------------

def run_import(mounts: list[Path], archive: Path, manifest_path: Path,
               catalog_path: Path = None, dry_run: bool = False):
    """
    Generator that scans mounts and imports new recordings.
    Yields dicts with 'type' key describing progress.
    """
    yield {"type": "info", "msg": "Scanning camera..."}
    recordings = scan_new_recordings(mounts, manifest_path, archive)
    new = [r for r in recordings if not r["already_imported"] or r.get("needs_stitch")]
    total_bytes = sum(r["size_bytes"] for r in new)

    yield {
        "type": "import_start",
        "total_files": len(recordings),
        "total_new": len(new),
        "total_bytes": total_bytes,
        "dry_run": dry_run,
    }

    if not new:
        yield {"type": "import_complete", "imported": 0, "errors": 0, "dry_run": dry_run}
        return

    manifest = load_manifest(manifest_path)
    imported_count = 0
    errors = 0
    catalog_entries = []

    for i, rec in enumerate(new):
        parts = [Path(p) for p in rec["file_paths"]]
        mount = Path(rec["mount"])
        ts = datetime.fromisoformat(rec["recorded_at"])
        intended = output_path(archive, ts, parts)
        # For stitch jobs, overwrite the existing under-sized file rather than
        # creating a _2 conflict — the old file is just one unmerged part.
        dest = intended if rec.get("needs_stitch") else resolve_conflict(intended)
        names = " + ".join(rec["files"]) if rec["is_multipart"] else rec["files"][0]

        yield {
            "type": "file_start",
            "index": i,
            "total": len(new),
            "files": rec["files"],
            "dest": str(dest.relative_to(archive)),
            "size_bytes": rec["size_bytes"],
            "is_multipart": rec["is_multipart"],
        }

        # Collect old individual-part archive paths before overwriting manifest
        old_outputs = set()
        if rec.get("needs_stitch") and not dry_run:
            for part in parts:
                entry = manifest["imported"].get(manifest_key(part), {})
                old_out = entry.get("output")
                if old_out:
                    old_outputs.add(archive / old_out)

        ok = concat_or_copy(parts, dest, dry_run)

        if ok:
            if not dry_run:
                for part in parts:
                    manifest["imported"][manifest_key(part)] = {
                        "camera_file": str(part.relative_to(mount)),
                        "size": part.stat().st_size,
                        "import_date": datetime.now().strftime("%Y-%m-%d"),
                        "output": str(dest.relative_to(archive)),
                        "parts": [p.name for p in parts],
                    }
                catalog_entries.append({
                    "path": str(dest.relative_to(archive)),
                    "recorded_at": ts.isoformat(),
                    "duration_seconds": rec.get("duration_seconds"),
                    "size_bytes": dest.stat().st_size,
                    "imported_at": datetime.now().strftime("%Y-%m-%d"),
                    "source": "canon_import",
                    "glacier_archived": False,
                    "glacier_archive_id": None,
                })
                # Delete orphaned individual-part files left from old imports
                for old_path in old_outputs:
                    if old_path != dest and old_path.exists():
                        old_path.unlink(missing_ok=True)
                        yield {"type": "info", "msg": f"Removed old part: {old_path.relative_to(archive)}"}
            imported_count += 1
            yield {"type": "file_done", "index": i, "dest": str(dest.relative_to(archive))}
        else:
            errors += 1
            yield {"type": "file_error", "index": i, "files": rec["files"], "error": "copy failed"}

    if not dry_run and imported_count > 0:
        save_manifest(manifest_path, manifest)
        if catalog_path:
            catalog = load_catalog(catalog_path)
            # Remove any orphaned individual-part entries replaced by stitched files
            stitched_paths = {e["path"] for e in catalog_entries}
            catalog["recordings"] = [
                r for r in catalog["recordings"]
                if r["path"] not in stitched_paths
            ]
            for entry in catalog_entries:
                catalog["recordings"].append(entry)
            catalog["updated"] = datetime.now().isoformat()
            save_catalog(catalog_path, catalog)

    yield {"type": "import_complete", "imported": imported_count, "errors": errors, "dry_run": dry_run}


# ---------------------------------------------------------------------------
# Archive stitch candidates
# ---------------------------------------------------------------------------

def find_stitch_candidates(catalog: dict) -> list[list[dict]]:
    """
    Find groups of non-legacy archive recordings that appear to be individually-
    imported parts of the same recording (never merged).
    Criteria: prev size >= PART_SIZE_THRESHOLD and prev end timestamp ≈ curr start
    (within 60 s tolerance).
    """
    recs = [
        r for r in catalog.get("recordings", [])
        if r.get("source") != "legacy_tvd"
        and r.get("recorded_at")
        and r.get("duration_seconds")
        and r.get("size_bytes")
    ]
    recs = sorted(recs, key=lambda r: r["recorded_at"])

    groups: list[list[dict]] = []
    current: list[dict] = []

    for rec in recs:
        if not current:
            current = [rec]
            continue
        prev = current[-1]
        try:
            prev_end = (
                datetime.fromisoformat(prev["recorded_at"]).timestamp()
                + prev["duration_seconds"]
            )
            curr_start = datetime.fromisoformat(rec["recorded_at"]).timestamp()
            gap = abs(curr_start - prev_end)
        except (ValueError, TypeError):
            gap = float("inf")

        if prev.get("size_bytes", 0) >= PART_SIZE_THRESHOLD and gap <= 60.0:
            current.append(rec)
        else:
            if len(current) > 1:
                groups.append(current)
            current = [rec]

    if len(current) > 1:
        groups.append(current)

    return groups


def stitch_archive_parts(groups: list[list[dict]], archive: Path,
                         catalog_path: Path, manifest_path: Path = None,
                         cancel_event=None):
    """
    Generator that stitches groups of archive part-files into single merged files,
    deletes the individual parts, and updates the catalog (and manifest if given).
    """
    total = len(groups)
    yield {"type": "stitch_start", "total_groups": total}

    catalog = load_catalog(catalog_path)
    manifest = load_manifest(manifest_path) if manifest_path and manifest_path.exists() else None

    stitched = 0
    errors = 0

    for i, group in enumerate(groups):
        if cancel_event and cancel_event.is_set():
            yield {"type": "cancelled", "stitched": stitched}
            return

        part_paths = [archive / r["path"] for r in group]
        missing = [str(p) for p in part_paths if not p.exists()]
        if missing:
            yield {"type": "stitch_error", "index": i, "error": f"Missing: {', '.join(missing)}"}
            errors += 1
            continue

        dest = part_paths[0]  # merged file overwrites the first part
        part_labels = [r["path"].split("/")[-1] for r in group]
        yield {
            "type": "stitch_file_start",
            "index": i,
            "total": total,
            "parts": part_labels,
            "dest": group[0]["path"],
        }

        ok = concat_or_copy(part_paths, dest, dry_run=False)
        if not ok:
            yield {"type": "stitch_error", "index": i, "error": "ffmpeg concat failed"}
            errors += 1
            continue

        # Delete the extra part files (keep dest which is part_paths[0])
        deleted = []
        for p in part_paths[1:]:
            try:
                p.unlink()
                deleted.append(str(p.relative_to(archive)))
            except Exception as e:
                yield {"type": "stitch_warning", "msg": f"Could not delete {p.name}: {e}"}

        # Update catalog: remove all parts, add merged entry
        merged_size = dest.stat().st_size
        total_duration = sum(r.get("duration_seconds") or 0 for r in group)
        merged_path = str(dest.relative_to(archive))
        part_rel_paths = {r["path"] for r in group}
        catalog["recordings"] = [
            r for r in catalog["recordings"] if r["path"] not in part_rel_paths
        ]
        first = group[0]
        catalog["recordings"].append({
            "path": merged_path,
            "recorded_at": first["recorded_at"],
            "duration_seconds": round(total_duration),
            "size_bytes": merged_size,
            "imported_at": first.get("imported_at"),
            "source": first.get("source", "canon_import"),
            "glacier_archived": False,
            "glacier_archive_id": None,
        })

        # Update manifest if provided: point all parts' camera-file entries to merged output
        if manifest:
            for key, entry in manifest["imported"].items():
                if entry.get("output") in part_rel_paths:
                    entry["output"] = merged_path

        stitched += 1
        yield {
            "type": "stitch_file_done",
            "index": i,
            "dest": merged_path,
            "deleted": deleted,
        }

    catalog["updated"] = datetime.now().isoformat()
    save_catalog(catalog_path, catalog)
    if manifest and manifest_path:
        save_manifest(manifest_path, manifest)

    yield {"type": "stitch_complete", "stitched": stitched, "errors": errors}


# ---------------------------------------------------------------------------
# Catalog builder (generator — yields progress events)
# ---------------------------------------------------------------------------

def build_catalog_from_archive(archive: Path, catalog_path: Path, cancel_event=None):
    """
    Walk archive directory, run ffprobe on each MTS file not already in catalog,
    and upsert into catalog.json.  Pass a threading.Event as cancel_event to
    support mid-build cancellation.
    """
    catalog = load_catalog(catalog_path)
    known_paths = {r["path"] for r in catalog["recordings"]}

    all_mts = sorted(
        f for f in archive.rglob("*")
        if f.suffix.upper() == ".MTS" and f.is_file()
        and not any(p.name.startswith(".") for p in f.parents)
    )
    new_files = [f for f in all_mts if str(f.relative_to(archive)) not in known_paths]

    yield {
        "type": "catalog_start",
        "new_files": len(new_files),
        "known_files": len(known_paths),
    }

    added = 0
    for i, f in enumerate(new_files):
        if cancel_event and cancel_event.is_set():
            yield {"type": "cancelled", "added": added, "total_so_far": len(catalog["recordings"])}
            return
        rel = str(f.relative_to(archive))
        yield {"type": "catalog_file", "current": i + 1, "total": len(new_files), "path": rel}

        rel_upper = rel.upper()
        if "TVD_AVCHD" in rel_upper:
            source = "legacy_tvd"
        elif re.match(r"^\d{4}/\d{8}_\d{6}", rel):
            source = "canon_import"
        else:
            source = "manual"

        recorded_at = ffprobe_creation_time(f)
        duration = ffprobe_duration(f)

        if not recorded_at:
            m = re.search(r"(\d{8})_(\d{6})", f.stem)
            if m:
                try:
                    recorded_at = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
                except ValueError:
                    pass
        if not recorded_at:
            for parent in f.parents:
                if parent == archive:
                    break
                m = re.match(r"^(\d{8})$", parent.name)
                if m:
                    try:
                        recorded_at = datetime.strptime(m.group(1), "%Y%m%d")
                    except ValueError:
                        pass
                    break

        catalog["recordings"].append({
            "path": rel,
            "recorded_at": recorded_at.isoformat() if recorded_at else None,
            "duration_seconds": round(duration) if duration else None,
            "size_bytes": f.stat().st_size,
            "imported_at": None,
            "source": source,
            "glacier_archived": False,
            "glacier_archive_id": None,
        })
        added += 1

    catalog["updated"] = datetime.now().isoformat()
    save_catalog(catalog_path, catalog)

    yield {
        "type": "catalog_complete",
        "added": added,
        "total_recordings": len(catalog["recordings"]),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Canon HFM52 AVCHD import utility")
    parser.add_argument("mount", nargs="?", help="Camera mount point")
    parser.add_argument("--archive", default=ARCHIVE_DEFAULT)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--catalog", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--detect", action="store_true", help="Detect connected cameras and exit")
    parser.add_argument("--build-catalog", action="store_true", help="Build/update catalog from archive")
    args = parser.parse_args()

    archive = Path(args.archive)
    manifest_path = Path(args.manifest) if args.manifest else archive / "manifest.json"
    catalog_path = Path(args.catalog) if args.catalog else archive / "catalog.json"

    if args.detect:
        cameras = detect_cameras()
        if not cameras:
            print("No AVCHD camera volumes detected.")
        for c in cameras:
            print(f"  {c['label']} ({c['volume_type']}) — {c['file_count']} files at {c['mount']}")
        return

    if args.build_catalog:
        print(f"Building catalog from {archive} ...")
        for event in build_catalog_from_archive(archive, catalog_path):
            if event["type"] == "catalog_start":
                print(f"  {event['known_files']} already known, {event['new_files']} new to scan")
            elif event["type"] == "catalog_file":
                print(f"  [{event['current']}/{event['total']}] {event['path']}")
            elif event["type"] == "catalog_complete":
                print(f"\nDone. Added {event['added']} entries. Total: {event['total_recordings']} recordings.")
        return

    if not args.mount:
        parser.error("mount point required (or use --detect / --build-catalog)")

    mount = Path(args.mount)
    if not mount.exists():
        print(f"ERROR: Mount point does not exist: {mount}", file=sys.stderr)
        sys.exit(1)

    print(f"Archive:  {archive}")
    print(f"Manifest: {manifest_path}")
    if args.dry_run:
        print("Mode:     DRY RUN — no files will be written")
    print()

    imported = 0
    errors = 0
    for event in run_import([mount], archive, manifest_path, catalog_path, args.dry_run):
        t = event["type"]
        if t == "info":
            print(event["msg"])
        elif t == "import_start":
            n = event["total_new"]
            total_mb = event["total_bytes"] / 1024 ** 2
            print(f"Found {event['total_files']} recordings, {n} new ({total_mb:.0f} MB).\n")
        elif t == "file_start":
            names = " + ".join(event["files"])
            mb = event["size_bytes"] / 1024 ** 2
            print(f"  [{event['index']+1}/{event['total']}] {names} → {event['dest']} ({mb:.0f} MB)")
        elif t == "file_done":
            action = "concat" if event.get("is_multipart") else "copy"
            print(f"    [{action}] done")
        elif t == "file_error":
            print(f"    ERROR: {event['error']}", file=sys.stderr)
            errors += 1
        elif t == "import_complete":
            imported = event["imported"]
            errors = event["errors"]

    print(f"\nDone. {imported} recording(s) imported, {errors} error(s).")
    if args.dry_run:
        print("(Dry run — nothing written.)")


if __name__ == "__main__":
    main()
