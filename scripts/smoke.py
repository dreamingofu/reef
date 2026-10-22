"""Live smoke test against YouTube (needs network).

Usage:
  python scripts/smoke.py                 search -> resolve -> fetch the first 64 KiB of the stream
  python scripts/smoke.py --keep ID ...   keep each id synchronously, then ffprobe the file's tags and cover

--keep writes real files to REEF_MUSIC_DIR/Reef and adds them to REEF_HISTORY_FILE (default: ./history.json),
so point both at a scratch dir. The Navidrome rescan is skipped when REEF_NAVIDROME_USER / REEF_NAVIDROME_PASS
are empty, e.g.
  REEF_MUSIC_DIR=/tmp/reef/music REEF_HISTORY_FILE=/tmp/reef/history.json REEF_NAVIDROME_USER= REEF_NAVIDROME_PASS= \
    python scripts/smoke.py --keep dQw4w9WgXcQ
Exit status: 0 when everything passed, 1 otherwise.
"""
import argparse
import json
import os
import queue
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import downloader  # noqa: E402
import streamer  # noqa: E402
from sources import youtube  # noqa: E402

QUERY = "daft punk one more time"


def _ms(t0):
    return int((time.monotonic() - t0) * 1000)


def play():
    t0 = time.monotonic()
    results = youtube.SOURCE.search(QUERY, 3)
    search_ms = _ms(t0)
    if not results:
        raise RuntimeError(f"no search results for {QUERY!r}")
    vid = results[0]["id"]
    print(f"search_ms={search_ms} n={len(results)} id={vid} title={results[0].get('title')!r}")

    t0 = time.monotonic()
    entry = streamer.resolve(vid)
    print(f"resolve_ms={_ms(t0)} ext={entry.get('ext')} mime={entry.get('mime')}")

    t0 = time.monotonic()
    r = streamer.session.get(entry["url"], headers={**entry["headers"], "Range": "bytes=0-65535"},
                             stream=True, timeout=(10, 30))
    try:
        ttfb_ms = _ms(t0)
        n = sum(len(c) for c in r.iter_content(16 * 1024))
    finally:
        r.close()
    print(f"ttfb_ms={ttfb_ms} status={r.status_code} bytes={n}")
    if r.status_code not in (200, 206) or n <= 0:
        raise RuntimeError(f"stream fetch failed: status={r.status_code} bytes={n}")


def _probe(path):
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format_tags:stream_tags:stream=codec_name,codec_type,width,height:stream_disposition=attached_pic",
         "-of", "json", path],
        capture_output=True, text=True)
    if p.returncode != 0:
        print(f"  ffprobe failed: {p.stderr.strip()[:200]}")
        return
    data = json.loads(p.stdout or "{}")
    streams = data.get("streams") or []
    # Ogg/Opus keeps its comments on the audio stream, not the container, so show both.
    tags = dict((data.get("format") or {}).get("tags") or {})
    for s in streams:
        if s.get("codec_type") == "audio":
            tags.update(s.get("tags") or {})
            break
    print("  tags: " + json.dumps(tags, ensure_ascii=False))
    for s in streams:
        if (s.get("disposition") or {}).get("attached_pic") or s.get("codec_type") == "video":
            w, h = s.get("width"), s.get("height")
            print(f"  cover: codec={s.get('codec_name')} {w}x{h} square={bool(w) and w == h}")
            break
    else:
        print("  cover: none")


def keep(ids):
    failed = False
    for tid in ids:
        job = downloader.enqueue(tid)
        # No worker thread here: take the id back off the queue and run the job inline.
        try:
            while True:
                downloader._queue.get_nowait()
                downloader._queue.task_done()
        except queue.Empty:
            pass
        t0 = time.monotonic()
        downloader._run(job)
        print(f"{tid}: status={job['status']} ms={_ms(t0)} error={job['error']} file={job['file']}")
        if job["status"] == "error":
            failed = True
        if job["file"] and os.path.exists(job["file"]):
            _probe(job["file"])
    return not failed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", nargs="+", metavar="ID", help="keep these track ids instead of the play check")
    args = ap.parse_args()
    try:
        if args.keep:
            ok = keep(args.keep)
        else:
            play()
            ok = True
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {str(e)[:300]}")
        return 1
    print("OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
