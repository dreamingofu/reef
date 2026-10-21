"""Time yt-dlp stream resolution per YouTube player client, to choose REEF_YT_CLIENT.

Usage: python scripts/bench_resolve.py VIDEO_ID [client,client ...]
Each argument after the id is one candidate (a comma list); "default" means yt-dlp's own choice.
Every candidate gets a fresh YoutubeDL, i.e. a cold resolve like the first play after a restart
(the on-disk ~/.cache/yt-dlp player cache is still shared).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yt_dlp  # noqa: E402

from streamer import ydl_options  # noqa: E402

class _Quiet:
    def debug(self, msg):
        pass

    warning = info = error = debug


CANDIDATES = ["default", "ios", "android_vr", "web_safari", "tv", "mweb"]


def bench(video_id, candidate):
    clients = [] if candidate == "default" else [c.strip() for c in candidate.split(",") if c.strip()]
    t0 = time.monotonic()
    try:
        with yt_dlp.YoutubeDL({**ydl_options(clients), "logger": _Quiet()}) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        ms = int((time.monotonic() - t0) * 1000)
        abr = info.get("abr")
        return f"{candidate:<16} {ms:>7}  {info.get('ext', '?'):<5} {info.get('format_id', '?'):<8} {abr if abr else '?'}"
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        msg = str(e).replace("\n", " ").removeprefix("ERROR: ")[:160]
        return f"{candidate:<16} {ms:>7}  ERROR: {msg}"


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    video_id = sys.argv[1]
    candidates = sys.argv[2:] or CANDIDATES
    print(f"{'client':<16} {'wall_ms':>7}  {'ext':<5} {'format':<8} abr")
    for c in candidates:
        print(bench(video_id, c), flush=True)


if __name__ == "__main__":
    main()
