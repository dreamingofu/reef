"""Lossless upgrade on keep (production plan P1.7): candidate selection, the preview probe, silent fallback."""
import json
import queue
import shutil
import subprocess

import pytest

import app
import config
import downloader
import history
import settings
import sources

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

Q = {"title": "Despacito", "artist": "Luis Fonsi", "duration": 229, "album": ""}


def mc(id_, title="Despacito", artists="Luis Fonsi, Daddy Yankee", duration=229, isrc="", album=""):
    return {"id": f"mc:{id_}", "title": title, "channel": artists, "duration": duration, "isrc": isrc,
            "album": album, "source": "mc"}


# ---------- selection ----------

@pytest.mark.parametrize("cand, want", [
    (mc(1), 500),                                          # all there: raw 1.0 x 500
    (mc(2, duration=240), 420),                            # 11 s off: duration 0.2 -> raw 0.84
    (mc(3, duration=255), None),                           # > 25 s off: rejected
    (mc(4, title="Despacito - Remix"), None),              # hard version words must agree
    (mc(5, title="Despacito (Live)"), None),
    (mc(6, title="Despacito (Instrumental)"), None),
    (mc(7, title="Despacito (Karaoke Version)"), None),
    (mc(8, title="Despacito (Sped Up)"), None),
    (mc(9, title="Despacito (Slowed)"), None),
    (mc(10, title="Despacito - Remastered 2017"), 455),   # soft: -45
    (mc(11, title="Despacito (Radio Edit)"), 455),
    (mc(12, title="Despacito (Acoustic)"), 455),
    (mc(13, album="Vida (Deluxe Edition)"), 455),          # "edition" isn't an edit; deluxe is soft
    (mc(14, title="Gangnam Style", artists="PSY", duration=219), None),  # below the title floor
])
def test_points_table(cand, want):
    assert downloader.lossless_points(Q, cand) == want


def test_isrc_wins_and_minimum():
    plain, same_isrc = mc(1), mc(2, duration=240, isrc="USUM71607007")
    assert downloader.pick_lossless(Q, [plain, same_isrc], "usum71607007") == (same_isrc, 1420)
    assert downloader.pick_lossless(Q, [plain, same_isrc]) == (plain, 500)
    weak = mc(3, title="Despacit", artists="Someone Else", duration=None)
    assert downloader.lossless_points(Q, weak) < downloader.LOSSLESS_MIN
    assert downloader.pick_lossless(Q, [weak, mc(4, title="Despacito (Live)")]) is None


def test_searches_narrow_down():
    q = {"title": "Despacito (feat. Daddy Yankee)", "artist": "Luis Fonsi", "album": "Vida", "duration": 229}
    assert downloader._lossless_searches(q, "USUM1") == [
        ("USUM1", True), ("Despacito Luis Fonsi Vida", False), ("Despacito Luis Fonsi", False), ("Despacito", False)]
    assert downloader._lossless_searches({**q, "album": ""}, "") == [("Despacito Luis Fonsi", False), ("Despacito", False)]


# ---------- the keep ----------

VID = "kJQP7kiw5Fk"


class FakeMonochrome:
    key, name, can_keep, codec_label = "mc", "Monochrome", True, "FLAC"

    def __init__(self, results):
        self.results, self.asked = results, []

    def search(self, q, limit=10):
        self.asked.append(q)
        if isinstance(self.results, Exception):
            raise self.results
        return [dict(t) for t in self.results.get(q, [])] if isinstance(self.results, dict) else list(self.results)

    def info(self, native, hint=None):
        return {"title": "Despacito", "artist": "Luis Fonsi, Daddy Yankee", "album": "Vida", "art": "",
                "duration": 12, "isrc": "USUM71607007"}

    def art(self, native, size):
        return None


def _flac(path, seconds):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=f=440:r=8000:d={seconds}",
                    "-c:a", "flac", str(path)], check=True)


@pytest.fixture
def keep_env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", tmp_path / "music")
    monkeypatch.setattr(downloader, "TMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(config, "YTMUSIC", False)
    monkeypatch.setattr(downloader.navidrome, "rescan", lambda: False)
    monkeypatch.setattr(downloader.navidrome, "find_song", lambda *a: None)
    monkeypatch.setattr(downloader.loudness, "measure", lambda p: None)
    monkeypatch.setattr(downloader.lyrics, "for_track", lambda *a: None)
    config.HISTORY_FILE.write_text("[]", encoding="utf-8")
    settings.update({"keep_lossless": True})
    yt_runs = []

    def fake_ytdlp(job):
        yt_runs.append(job["video_id"])
        job["file"] = str(tmp_path / "music" / "yt.opus")
        history.add({"id": job["video_id"], "title": job["title"], "file": job["file"]})
        return {"title": job["title"], "artist": "", "album": "", "duration": 229}
    monkeypatch.setattr(downloader, "_run_ytdlp", fake_ytdlp)
    monkeypatch.setattr(downloader.streamer, "resolve", lambda tid: {"url": "http://mc/" + tid, "ext": "flac"})
    env = {"yt_runs": yt_runs, "tmp": tmp_path, "file_seconds": 12}

    def fake_fetch(job, entry, dest, *a):
        _flac(dest, env["file_seconds"])
    monkeypatch.setattr(downloader, "_fetch", fake_fetch)

    def use(results):
        src = FakeMonochrome(results)
        monkeypatch.setitem(sources.SOURCES, "mc", src)
        return src
    env["use"] = use
    yield env
    config.HISTORY_FILE.write_text("[]", encoding="utf-8")


def _job(**kw):
    job = downloader._new_job(VID, "Despacito", "Luis Fonsi, Daddy Yankee",
                              {"album": "Vida", "track_number": 3, "year": "2019"}, duration=12)
    job.update(kw)
    return job


@needs_ffmpeg
def test_lossless_keep_writes_flac_with_hints(keep_env):
    from mutagen import File as MutagenFile
    src = keep_env["use"]([mc(99, duration=12, album="Vida"), mc(98, title="Despacito - Remix", duration=12)])
    job = _job()
    downloader._run(job)
    assert job["status"] == "done", job["error"]
    assert job["lossless"] is True and job["upgraded_to"] == "mc:99" and keep_env["yt_runs"] == []
    assert job["file"].endswith(".flac") and src.asked[0] == "Despacito Luis Fonsi Vida"
    f = MutagenFile(job["file"])
    assert type(f).__name__ == "FLAC"
    assert (f["title"], f["album"], f["tracknumber"], f["date"], f["isrc"]) == (
        ["Despacito"], ["Vida"], ["3"], ["2019"], ["USUM71607007"])
    entry = history.get("mc:99")
    assert entry["upgraded_from"] == VID and entry["source"] == "mc" and history.get(VID) is None
    # the YouTube id counts as kept: marked in lists, and a second keep of it is a duplicate
    assert app._mark_kept([{"id": VID}])[0]["kept"] is True
    c = app.app.test_client()
    r = c.post("/api/download", data=json.dumps({"id": VID, "title": "Despacito"}), content_type="application/json")
    assert r.status_code == 409 and r.get_json()["entry"]["id"] == "mc:99"


@needs_ffmpeg
def test_preview_falls_back_to_youtube(keep_env):
    keep_env["use"]([mc(99, duration=12)])
    keep_env["file_seconds"] = 3  # a 30 s preview of a 3:49 song, scaled down
    job = _job()
    downloader._run(job)
    assert job["status"] == "done" and keep_env["yt_runs"] == [VID]
    assert job["lossless"] is False and job["upgraded_to"] is None and job["title"] == "Despacito"
    assert history.get(VID)["file"].endswith("yt.opus") and history.get("mc:99") is None
    assert list((keep_env["tmp"] / "tmp").iterdir()) == []  # the preview was cleaned up


def test_no_match_or_search_failure_falls_back(keep_env):
    keep_env["use"]([mc(1, title="Gangnam Style", artists="PSY")])
    job = _job()
    downloader._run(job)
    assert job["status"] == "done" and keep_env["yt_runs"] == [VID] and job["lossless"] is False
    keep_env["use"](RuntimeError("Monochrome down"))
    job = _job()
    downloader._run(job)
    assert job["status"] == "done" and keep_env["yt_runs"] == [VID, VID]


def test_off_by_default_and_only_for_youtube(keep_env, monkeypatch):
    src = keep_env["use"]([mc(99, duration=12)])
    settings.update({"keep_lossless": None})  # back to REEF_KEEP_LOSSLESS (off)
    assert settings.get("keep_lossless") is False
    downloader._run(_job())
    assert src.asked == [] and keep_env["yt_runs"] == [VID]
    settings.update({"keep_lossless": True})
    monkeypatch.setattr(downloader, "_run_generic", lambda job, *a, **k: pytest.fail("mc keeps aren't upgraded"))
    assert downloader._run_lossless({**_job(), "video_id": "mc:5", "source": "mc"}) is None
    monkeypatch.delitem(sources.SOURCES, "mc")
    assert downloader._run_lossless(_job()) is None  # Monochrome off: nothing to upgrade to


def test_keep_passes_duration(monkeypatch):
    monkeypatch.setattr(downloader, "_queue", queue.Queue())
    monkeypatch.setattr(downloader, "_jobs", {})
    config.HISTORY_FILE.write_text("[]", encoding="utf-8")
    c = app.app.test_client()
    r = c.post("/api/download", data=json.dumps({"id": VID, "title": "Despacito", "duration": 229.4}),
               content_type="application/json")
    assert r.status_code == 202 and r.get_json()["duration"] == 229.4 and r.get_json()["lossless"] is False
    r = c.post("/api/download", data=json.dumps({"id": "dQw4w9WgXcQ", "duration": "nan"}),
               content_type="application/json")
    assert r.get_json()["duration"] is None


# ---------- failures around the final move ----------

@needs_ffmpeg
def test_failure_after_the_move_keeps_flac_and_never_falls_back(keep_env, monkeypatch):
    keep_env["use"]([mc(99, duration=12)])

    def broken_history(entry):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(downloader.history, "add", broken_history)
    job = _job()
    downloader._run(job)
    assert keep_env["yt_runs"] == []  # no .opus next to the .flac
    assert job["status"] == "done" and job["lossless"] is True and job["file"].endswith(".flac")
    assert "couldn't record it as kept" in job["warning"]
    music = keep_env["tmp"] / "music"
    assert sorted(p.name for p in music.iterdir()) == ["Despacito.flac"]


def test_any_error_once_the_file_is_saved_fails_the_job_instead(keep_env, monkeypatch):
    keep_env["use"]([mc(99, duration=12)])
    saved = keep_env["tmp"] / "music" / "Despacito.flac"

    def saved_then_boom(job, *a, **k):
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(b"fLaC")
        job["file"] = str(saved)
        raise RuntimeError("bookkeeping exploded")
    monkeypatch.setattr(downloader, "_run_generic", saved_then_boom)
    job = _job()
    downloader._run(job)
    assert keep_env["yt_runs"] == [] and job["status"] == "error" and "bookkeeping" in job["error"]
    assert saved.exists()


@needs_ffmpeg
def test_failure_before_the_move_cleans_up_then_falls_back(keep_env, monkeypatch):
    keep_env["use"]([mc(99, duration=12)])
    real_move = downloader.shutil.move

    def half_copy(src, dst):  # a cross-filesystem copy that dies halfway (storage full, SD card pulled)
        if str(dst).endswith(".flac.part"):
            with open(dst, "wb") as f:
                f.write(b"fLaC half")
            raise OSError(28, "No space left on device")
        return real_move(src, dst)
    monkeypatch.setattr(downloader.shutil, "move", half_copy)
    job = _job()
    downloader._run(job)
    assert keep_env["yt_runs"] == [VID] and job["status"] == "done" and job["lossless"] is False
    music, tmp = keep_env["tmp"] / "music", keep_env["tmp"] / "tmp"
    assert not list(music.glob("*.part")) and not list(music.glob("*.flac"))
    assert list(tmp.iterdir()) == []
    assert history.get("mc:99") is None and history.get(VID)["file"].endswith("yt.opus")


@needs_ffmpeg
def test_tagging_failure_before_the_move_falls_back(keep_env, monkeypatch):
    keep_env["use"]([mc(99, duration=12)])

    def bad_tag(*a, **k):
        raise ValueError("mutagen choked")
    monkeypatch.setattr(downloader, "_tag", bad_tag)
    job = _job()
    downloader._run(job)
    assert keep_env["yt_runs"] == [VID] and job["status"] == "done"
    assert not list((keep_env["tmp"] / "music").glob("*.flac*")) and list((keep_env["tmp"] / "tmp").iterdir()) == []
