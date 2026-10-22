"""Track loudness (EBU R128, via ffmpeg's ebur128 filter) written as ReplayGain / R128 gain tags, so
Navidrome clients can play kept tracks at an even volume next to the rest of the library."""
import logging
import math
import re
import subprocess

from mutagen import File as MutagenFile
from mutagen import MutagenError
from mutagen.flac import FLAC
from mutagen.id3 import TXXX
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4FreeForm
from mutagen.ogg import OggFileType
from mutagen.oggopus import OggOpus

logger = logging.getLogger(__name__)

RG_REFERENCE = -18.0    # ReplayGain 2.0 reference loudness, LUFS
R128_REFERENCE = -23.0  # RFC 7845 R128_TRACK_GAIN reference, LUFS
_TIMEOUT = 120          # a long DJ mix on a phone takes a while; ebur128 decodes the whole file

_NUM = r"(-?(?:\d+(?:\.\d+)?|inf))"


def _parse(stderr):
    """{"lufs", "peak_dbtp"} from the filter's closing Summary block, or None when it isn't there."""
    summary = stderr.rpartition("Summary:")[2]
    i = re.search(r"Integrated loudness:\s*I:\s*" + _NUM + r"\s*LUFS", summary)
    p = re.search(r"True peak:\s*Peak:\s*" + _NUM + r"\s*dBFS", summary)
    if not i or not p:
        return None
    lufs, peak = float(i.group(1)), float(p.group(1))
    if not math.isfinite(lufs):
        return None
    return {"lufs": lufs, "peak_dbtp": peak}  # peak is -inf for digital silence; tag() copes


def measure(path):
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0",
           "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return _parse(p.stderr) if p.returncode == 0 else None


def track_gain(lufs):
    return round(RG_REFERENCE - lufs, 2)


def tag(path, m):
    """Write the gain tags for measurement `m`; False when the format isn't handled or the write failed."""
    gain = f"{RG_REFERENCE - m['lufs']:.2f} dB"
    peak = f"{10 ** (m['peak_dbtp'] / 20):.6f}"  # linear, 1.0 = full scale; 0.000000 for silence
    try:
        f = MutagenFile(path)
        if f is None:
            return False
        if f.tags is None and isinstance(f, (MP3, MP4, FLAC, OggFileType)):
            f.add_tags()
        if isinstance(f, MP3):
            for desc, value in (("REPLAYGAIN_TRACK_GAIN", gain), ("REPLAYGAIN_TRACK_PEAK", peak)):
                f.tags.setall(f"TXXX:{desc}", [TXXX(encoding=3, desc=desc, text=[value])])
        elif isinstance(f, MP4):
            f.tags["----:com.apple.iTunes:replaygain_track_gain"] = [MP4FreeForm(gain.encode())]
            f.tags["----:com.apple.iTunes:replaygain_track_peak"] = [MP4FreeForm(peak.encode())]
        elif isinstance(f, (FLAC, OggFileType)):
            f["REPLAYGAIN_TRACK_GAIN"] = [gain]
            f["REPLAYGAIN_TRACK_PEAK"] = [peak]
            if isinstance(f, OggOpus):
                # Opus players read R128_TRACK_GAIN: Q7.8 fixed point dB against -23 LUFS, an int16.
                q78 = round((R128_REFERENCE - m["lufs"]) * 256)
                f["R128_TRACK_GAIN"] = [str(max(-32768, min(32767, q78)))]
        else:
            return False
        f.save()
    except (MutagenError, OSError, ValueError) as e:
        logger.warning(f"reef: couldn't write gain tags: {e}")
        return False
    return True
