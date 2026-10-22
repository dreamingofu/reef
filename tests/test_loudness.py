"""loudness.py: ebur128 parsing, gain tags, and a real ffmpeg measurement."""
import shutil
import subprocess

import pytest
from mutagen import File as MutagenFile

import loudness

# Tail of `ffmpeg -nostats -i x.opus -map 0:a:0 -af ebur128=peak=true:framelog=quiet -f null -` (ffmpeg 8).
STDERR = """Input #0, ogg, from 'song.opus':
  Duration: 00:03:33.02, start: 0.007500, bitrate: 131 kb/s
  Stream #0:0: Audio: opus, 48000 Hz, stereo, fltp
[Parsed_ebur128_0 @ 0x95f040900] Summary:

  Integrated loudness:
    I:          -9.3 LUFS
    Threshold: -19.6 LUFS

  Loudness range:
    LRA:         4.1 LU
    Threshold: -29.6 LUFS
    LRA low:   -11.9 LUFS
    LRA high:   -7.8 LUFS

  True peak:
    Peak:        0.5 dBFS
[out#0/null @ 0x95f040180] video:0KiB audio:39943KiB subtitle:0KiB other streams:0KiB global headers:0KiB
size=N/A time=00:03:33.00 bitrate=N/A speed= 241x
"""
M = {"lufs": -9.3, "peak_dbtp": 0.5}


def test_parse_summary():
    assert loudness._parse(STDERR) == M


def test_parse_silence_and_garbage():
    silent = STDERR.replace("I:          -9.3 LUFS", "I:         -70.0 LUFS").replace("0.5 dBFS", "-inf dBFS")
    assert loudness._parse(silent) == {"lufs": -70.0, "peak_dbtp": float("-inf")}
    assert loudness._parse("") is None
    assert loudness._parse("Error opening input file song.opus.") is None
    assert loudness._parse(STDERR.split("  True peak:")[0]) is None


def test_measure_handles_missing_ffmpeg_and_failures(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError("ffmpeg")
    monkeypatch.setattr(loudness.subprocess, "run", missing)
    assert loudness.measure("x.opus") is None

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("ffmpeg", 120)
    monkeypatch.setattr(loudness.subprocess, "run", timeout)
    assert loudness.measure("x.opus") is None

    monkeypatch.setattr(loudness.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", STDERR))
    assert loudness.measure("x.opus") is None  # nonzero exit: don't trust a partial summary
    monkeypatch.setattr(loudness.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", STDERR))
    assert loudness.measure("x.opus") == M


def test_track_gain():
    assert loudness.track_gain(-9.3) == -8.7
    assert loudness.track_gain(-23) == 5.0


needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
_CODECS = {"opus": ["-c:a", "libopus"], "flac": ["-c:a", "flac"], "m4a": ["-c:a", "aac"],
           "mp3": ["-c:a", "libmp3lame"]}


def _make(tmp_path, ext, seconds=1):
    path = tmp_path / f"tone.{ext}"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=d={seconds}",
                    *_CODECS[ext], str(path)], check=True)
    return path


def _rg(path):
    f = MutagenFile(path)
    if path.suffix == ".mp3":
        return (f.tags["TXXX:REPLAYGAIN_TRACK_GAIN"].text[0], f.tags["TXXX:REPLAYGAIN_TRACK_PEAK"].text[0])
    if path.suffix == ".m4a":
        return tuple(bytes(f.tags[f"----:com.apple.iTunes:replaygain_track_{k}"][0]).decode() for k in ("gain", "peak"))
    return f["replaygain_track_gain"][0], f["replaygain_track_peak"][0]


@needs_ffmpeg
@pytest.mark.parametrize("ext", ["opus", "flac", "m4a", "mp3"])
def test_tag_round_trip(tmp_path, ext):
    path = _make(tmp_path, ext)
    assert loudness.tag(path, M) is True
    assert loudness.tag(path, M) is True  # re-tagging replaces, never duplicates
    assert _rg(path) == ("-8.70 dB", "1.059254")
    f = MutagenFile(path)
    if ext == "opus":
        assert f["R128_TRACK_GAIN"] == [str(round((-23 + 9.3) * 256))] == ["-3507"]
    elif ext in ("flac",):
        assert "R128_TRACK_GAIN" not in f
    if ext == "mp3":
        assert len(f.tags.getall("TXXX:REPLAYGAIN_TRACK_GAIN")) == 1


@needs_ffmpeg
def test_r128_is_clamped_to_int16(tmp_path):
    path = _make(tmp_path, "opus")
    loudness.tag(path, {"lufs": -200.0, "peak_dbtp": float("-inf")})
    f = MutagenFile(path)
    assert f["R128_TRACK_GAIN"] == ["32767"] and f["REPLAYGAIN_TRACK_PEAK"] == ["0.000000"]


def test_tag_unsupported(tmp_path):
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"not audio")
    assert loudness.tag(junk, M) is False
    assert loudness.tag(tmp_path / "missing.opus", M) is False


@needs_ffmpeg
def test_measure_real_file(tmp_path):
    # ffmpeg's sine source is 1/8 full scale: a -18 dBFS peak, about -21 LUFS for a 440 Hz mono tone.
    m = loudness.measure(_make(tmp_path, "flac", seconds=3))
    assert m is not None
    assert -25 < m["lufs"] < -17 and -19.5 < m["peak_dbtp"] < -16.5
