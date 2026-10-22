import json
import queue
import shutil
import subprocess
import threading

import pytest
import requests
import yt_dlp

import app
import config
import downloader
import history
from downloader import music_tags


# ---------- music_tags ----------

def test_topic_upload_uses_music_fields():
    tags = music_tags({
        "title": "One More Time", "track": "One More Time", "artists": ["Daft Punk"], "artist": "Daft Punk",
        "channel": "Daft Punk - Topic", "uploader": "Daft Punk - Topic", "album": "Discovery",
        "release_year": 2001, "release_date": "20010312", "upload_date": "20150101", "track_number": 1,
    })
    assert tags == {"meta_title": "One More Time", "meta_artist": "Daft Punk", "meta_album_artist": "Daft Punk",
                    "meta_album": "Discovery", "meta_date": "2001", "meta_track": "1"}


def test_known_artist_prefix_and_video_noise_are_stripped():
    info = {"title": "Rick Astley - Never Gonna Give You Up (Official Video) (4K Remaster)",
            "artist": "Rick Astley", "channel": "Rick Astley", "upload_date": "20091025"}
    tags = music_tags(info)
    assert tags["meta_title"] == "Never Gonna Give You Up"
    assert tags["meta_artist"] == "Rick Astley"
    assert tags["meta_date"] == "2009"
    assert "meta_album" not in tags and "meta_track" not in tags
    assert info["title"].startswith("Rick Astley - ")  # the filename comes from info["title"]: untouched


def test_artist_title_split_without_artist_info():
    tags = music_tags({"title": "Daft Punk - Around the World [Official Music Video]",
                       "channel": "Some Reuploader", "upload_date": "20120101"})
    assert tags["meta_artist"] == "Daft Punk"
    assert tags["meta_title"] == "Around the World"


def test_vevo_and_topic_suffixes():
    assert music_tags({"title": "Song (Lyrics)", "channel": "RickAstleyVEVO"})["meta_artist"] == "RickAstley"
    assert music_tags({"title": "Song", "uploader": "Daft Punk - Topic"})["meta_artist"] == "Daft Punk"
    tags = music_tags({"title": "Song [HD]", "artists": ["Adele VEVO", "Someone - Topic"]})
    assert tags["meta_artist"] == "Adele, Someone"
    assert tags["meta_album_artist"] == "Adele"
    assert tags["meta_title"] == "Song"


def test_nothing_known():
    assert music_tags({"title": "Untitled jam"}) == {"meta_title": "Untitled jam"}
    assert music_tags({}) == {}


def test_two_separators_are_not_split():
    tags = music_tags({"title": "A - B - C (Official Audio)", "channel": "Chan"})
    assert tags["meta_title"] == "A - B - C"
    assert tags["meta_artist"] == "Chan"


@pytest.mark.parametrize("title", ["Song (Radio Edit)", "Song (feat. X)", "Song (Remix)", "Song (Live)",
                                   "Song (Remastered 2009)", "(Official Video)"])
def test_meaningful_groups_are_kept(title):
    assert music_tags({"title": title, "artist": "A"})["meta_title"] == title


def test_strips_noise_but_keeps_meaningful_group_before_it():
    tags = music_tags({"title": "A - Song (feat. X) (Official Lyric Video)"})
    assert tags == {"meta_title": "Song (feat. X)", "meta_artist": "A", "meta_album_artist": "A"}


def test_artist_prefix_needs_a_word_boundary():
    assert music_tags({"title": "Alice - Song", "artist": "Ali"})["meta_title"] == "Alice - Song"


def test_tags_pp_feeds_ffmpeg_metadata():
    ydl = yt_dlp.YoutubeDL(downloader._options({}))
    pp = downloader._MusicTagsPP(ydl)
    _, info = pp.run({"title": "Rick Astley - Never Gonna Give You Up (Official Video)", "artist": "Rick Astley",
                      "upload_date": "20091025"})
    from yt_dlp.postprocessor.ffmpeg import FFmpegMetadataPP
    args = list(FFmpegMetadataPP(ydl)._get_metadata_opts(info))
    assert ("-metadata", "title=Never Gonna Give You Up") in args
    assert ("-metadata", "date=2009") in args
    assert info["title"] == "Rick Astley - Never Gonna Give You Up (Official Video)"
    assert ydl.prepare_filename({**info, "id": "x", "ext": "opus"}).endswith(
        "/Rick Astley - Never Gonna Give You Up (Official Video).opus")


# ---------- yt-dlp options ----------

def _keys(opts):
    return [p["key"] for p in opts["postprocessors"]]


def test_options_retry_profile_and_quiet_progress():
    opts = downloader._options({})
    for k, v in {"retries": 10, "fragment_retries": 10, "extractor_retries": 3, "file_access_retries": 3,
                 "socket_timeout": 15, "continuedl": True, "concurrent_fragment_downloads": 4,
                 "noprogress": True, "quiet": True}.items():
        assert opts[k] == v, k
    assert opts["retry_sleep_functions"]["http"](3) == 8
    assert opts["retry_sleep_functions"]["http"](20) == 120
    assert opts["retry_sleep_functions"]["fragment"](20) == 60
    assert opts["outtmpl"] == {"default": "%(title)s.%(ext)s"}


def test_options_jpg_cover_before_embed():
    opts = downloader._options({})
    assert "thumbnailsconvertor" not in (opts.get("postprocessor_args") or {})  # the crop is _SquareCoverPP's job
    keys = _keys(opts)
    conv = next(p for p in opts["postprocessors"] if p["key"] == "FFmpegThumbnailsConvertor")
    assert conv["format"] == "jpg" and conv["when"] == "before_dl"
    assert keys.index("FFmpegThumbnailsConvertor") < keys.index("EmbedThumbnail")
    assert keys.index("FFmpegExtractAudio") < keys.index("FFmpegMetadata") < keys.index("EmbedThumbnail")
    assert keys[0] == "MetadataParser"  # the description blanking stays


def test_options_are_accepted_by_yt_dlp():
    with yt_dlp.YoutubeDL(downloader._options({})) as ydl:
        for pp, when in downloader._custom_pps(ydl):
            ydl.add_post_processor(pp, when=when)
        names = {when: [type(p).__name__ for p in pps] for when, pps in ydl._pps.items()}
    assert "_MusicTagsPP" in names["pre_process"]
    # The crop runs after the format conversion; EmbedThumbnail is post_process, so after both.
    assert names["before_dl"] == ["FFmpegThumbnailsConvertorPP", "_SquareCoverPP"]
    assert "EmbedThumbnailPP" in names["post_process"]


def test_sponsorblock_only_with_flag(monkeypatch):
    monkeypatch.setattr(config, "SPONSORBLOCK", False)
    assert "ModifyChapters" not in _keys(downloader._options({}))
    ydl = yt_dlp.YoutubeDL({"quiet": True})
    assert "after_filter" not in [w for _, w in downloader._custom_pps(ydl)]

    monkeypatch.setattr(config, "SPONSORBLOCK", True)
    opts = downloader._options({})
    keys = _keys(opts)
    # yt-dlp's order: extract audio, then cut, then tag (cutting would drop the tags otherwise).
    assert keys.index("FFmpegExtractAudio") < keys.index("ModifyChapters") < keys.index("FFmpegMetadata")
    mc = next(p for p in opts["postprocessors"] if p["key"] == "ModifyChapters")
    assert set(mc["remove_sponsor_segments"]) == {"music_offtopic", "sponsor", "intro", "outro"}
    pps = dict((w, p) for p, w in downloader._custom_pps(ydl))
    assert isinstance(pps["after_filter"], downloader._SafeSponsorBlockPP)
    with yt_dlp.YoutubeDL(opts):
        pass


needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")


def _size(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout
    return tuple(map(int, out.strip().split(",")))


@needs_ffmpeg
@pytest.mark.parametrize("ext", ["jpg", "png"])
def test_square_cover_crops_in_place(tmp_path, ext):
    img = tmp_path / f"cover.{ext}"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=320x180", "-frames:v", "1",
                    str(img)], check=True)
    pp = downloader._SquareCoverPP(yt_dlp.YoutubeDL({"quiet": True}))
    info = {"thumbnails": [{"filepath": str(img)}, {"url": "not downloaded"}, {"filepath": str(tmp_path / "gone.jpg")}]}
    assert pp.run(info) == ([], info)
    assert _size(img) == (180, 180)
    assert sorted(p.name for p in tmp_path.iterdir()) == [img.name]


@needs_ffmpeg
def test_square_cover_failure_keeps_original(tmp_path):
    img = tmp_path / "cover.jpg"
    img.write_bytes(b"not an image")
    pp = downloader._SquareCoverPP(yt_dlp.YoutubeDL({"quiet": True}))
    info = {"thumbnails": [{"filepath": str(img)}]}
    assert pp.run(info) == ([], info)
    assert img.read_bytes() == b"not an image"
    assert sorted(p.name for p in tmp_path.iterdir()) == [img.name]


def test_safe_sponsorblock_swallows_api_failure(monkeypatch):
    pp = downloader._SafeSponsorBlockPP(yt_dlp.YoutubeDL({"quiet": True}), categories=["sponsor"])

    def boom(*a, **k):
        raise OSError("api down")
    monkeypatch.setattr(pp, "_get_sponsor_chapters", boom)
    info = {"extractor_key": "Youtube", "id": "x", "duration": 10}
    assert pp.run(info) == ([], info)


def test_ydl_extra_cannot_clobber_protected_keys(monkeypatch):
    monkeypatch.setattr(config, "YDL_EXTRA", {
        "postprocessors": [], "progress_hooks": [], "paths": {"home": "/"}, "outtmpl": "%(id)s",
        "cookiefile": "/c.txt", "retries": 2})
    opts = downloader._options({})
    assert opts["cookiefile"] == "/c.txt" and opts["retries"] == 2
    assert opts["outtmpl"] == {"default": "%(title)s.%(ext)s"}
    assert opts["paths"]["home"] == str(downloader.DOWNLOAD_DIR)
    assert opts["postprocessors"] and len(opts["progress_hooks"]) == 1


def test_ydl_extra_parsing(caplog):
    assert config._ydl_extra('{"a": 1}') == {"a": 1}
    assert config._ydl_extra("") == {} and config._ydl_extra(None) == {}
    assert config._ydl_extra("{nope") == {}
    assert config._ydl_extra("[1]") == {}
    assert caplog.text.count("REEF_YDL_EXTRA") == 2


# ---------- generic fetch retries ----------

class _Resp:
    def __init__(self, status=200, chunks=(b"abc", b"def"), fail_after=None):
        self.status_code, self.chunks, self.fail_after = status, chunks, fail_after
        self.headers = {"Content-Length": str(sum(map(len, chunks)))}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def iter_content(self, size):
        for i, c in enumerate(self.chunks):
            if self.fail_after is not None and i >= self.fail_after:
                raise requests.exceptions.ChunkedEncodingError("dropped")
            yield c

    def close(self):
        pass


class _Session:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def get(self, *a, **k):
        self.calls += 1
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


@pytest.fixture
def sleeps(monkeypatch):
    slept = []
    monkeypatch.setattr(downloader, "_sleep", slept.append)
    return slept


def test_fetch_retries_then_succeeds(monkeypatch, sleeps, tmp_path):
    s = _Session(requests.ConnectionError("down"), _Resp(503), _Resp(fail_after=1), _Resp())
    monkeypatch.setattr(downloader.streamer, "session", s)
    dest = tmp_path / "a.bin"
    downloader._fetch({"percent": 0}, {"url": "http://x"}, dest)
    assert dest.read_bytes() == b"abcdef"  # the half-written attempt was started over, not appended to
    assert sleeps == [2, 4, 8] and s.calls == 4


def test_fetch_gives_up_after_last_attempt(monkeypatch, sleeps, tmp_path):
    s = _Session(*[requests.Timeout("slow")] * 4)
    monkeypatch.setattr(downloader.streamer, "session", s)
    with pytest.raises(requests.Timeout):
        downloader._fetch({"percent": 0}, {"url": "http://x"}, tmp_path / "a.bin")
    assert sleeps == [2, 4, 8]


def test_fetch_does_not_retry_client_errors(monkeypatch, sleeps, tmp_path):
    s = _Session(_Resp(404))
    monkeypatch.setattr(downloader.streamer, "session", s)
    with pytest.raises(requests.HTTPError):
        downloader._fetch({"percent": 0}, {"url": "http://x"}, tmp_path / "a.bin")
    assert sleeps == [] and s.calls == 1


# ---------- duplicate guard ----------

VID = "dQw4w9WgXcQ"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(downloader, "_queue", queue.Queue())  # no worker: queued jobs just sit there
    monkeypatch.setattr(downloader, "_jobs", {})
    config.HISTORY_FILE.write_text("[]", encoding="utf-8")
    yield app.app.test_client()
    config.HISTORY_FILE.write_text("[]", encoding="utf-8")


def _post(client, **body):
    return client.post("/api/download", data=json.dumps({"id": VID, **body}), content_type="application/json")


def test_dup_guard_existing_file(client, tmp_path):
    f = tmp_path / "song.opus"
    f.write_bytes(b"x")
    history.add({"id": VID, "title": "Song", "file": str(f)})
    r = _post(client)
    assert r.status_code == 409
    assert r.get_json()["error"] == "Already in your library"
    assert r.get_json()["entry"]["id"] == VID
    assert downloader._queue.qsize() == 0


def test_dup_guard_missing_file_rekeeps(client, tmp_path):
    history.add({"id": VID, "title": "Song", "file": str(tmp_path / "gone.opus")})
    r = _post(client)
    assert r.status_code == 202 and r.get_json()["video_id"] == VID


def test_dup_guard_force(client, tmp_path):
    f = tmp_path / "song.opus"
    f.write_bytes(b"x")
    history.add({"id": VID, "title": "Song", "file": str(f)})
    assert _post(client, force=True).status_code == 202


def test_dup_guard_active_job(client):
    first = _post(client)
    assert first.status_code == 202
    r = _post(client)
    assert r.status_code == 409
    assert r.get_json()["error"] == "Already keeping"
    assert r.get_json()["job"]["id"] == first.get_json()["id"]
    assert _post(client, force=True).status_code == 409  # force never runs two keeps of one track at once
    downloader._jobs[first.get_json()["id"]]["status"] = "error"
    assert _post(client).status_code == 202


def test_enqueue_unique_is_one_job_per_track(monkeypatch):
    monkeypatch.setattr(downloader, "_queue", queue.Queue())
    monkeypatch.setattr(downloader, "_jobs", {})
    job, created = downloader.enqueue_unique(VID, "Song")
    again, created2 = downloader.enqueue_unique(VID, "Song")
    assert created and not created2 and again is job
    assert downloader._queue.qsize() == 1 and len(downloader._jobs) == 1
    job["status"] = "done"
    assert downloader.enqueue_unique(VID)[1] and downloader._queue.qsize() == 2


def test_enqueue_unique_under_concurrency(monkeypatch):
    monkeypatch.setattr(downloader, "_queue", queue.Queue())
    monkeypatch.setattr(downloader, "_jobs", {})
    start = threading.Barrier(16)
    results = []

    def tap():
        start.wait()
        results.append(downloader.enqueue_unique(VID)[1])
    threads = [threading.Thread(target=tap) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1 and downloader._queue.qsize() == 1


# ---------- post steps: loudness + lyrics after the file is final ----------

def _fake_keep(monkeypatch, path):
    def run(job):
        job["file"] = str(path)
        history.add({"id": job["video_id"], "title": "Song", "file": job["file"]})
        return {"title": "Song", "artist": "Rick Astley", "album": "", "duration": 213.0}
    monkeypatch.setattr(downloader, "_run_ytdlp", run)
    monkeypatch.setattr(config, "REPLAYGAIN", True)
    config.HISTORY_FILE.write_text("[]", encoding="utf-8")


def test_post_step_failures_do_not_fail_the_keep(monkeypatch, tmp_path, caplog):
    f = tmp_path / "song.opus"
    f.write_bytes(b"x")
    _fake_keep(monkeypatch, f)

    def boom(*a, **k):
        raise RuntimeError("nope")
    monkeypatch.setattr(downloader.loudness, "measure", boom)
    monkeypatch.setattr(downloader.lyrics, "for_track", boom)
    job = downloader._new_job(VID, "Song", "")
    downloader._run(job)
    assert job["status"] == "done" and job["error"] is None and job["file"] == str(f)
    entry = history.get(VID)
    assert entry["lyrics"] is None and entry["gain_db"] is None
    out = caplog.text
    assert "loudness skipped" in out and "lyrics skipped" in out


def test_post_steps_record_lyrics_and_gain(monkeypatch, tmp_path):
    f = tmp_path / "song.opus"
    f.write_bytes(b"x")
    _fake_keep(monkeypatch, f)
    seen = {}
    job = downloader._new_job(VID, "Song", "")

    def for_track(tid, title, artist, album, duration):
        seen.update(status=job["status"], args=(tid, title, artist, album, duration))
        return {"synced": "[00:01.00] la", "plain": "la", "instrumental": False}
    monkeypatch.setattr(downloader.loudness, "measure", lambda p: {"lufs": -9.3, "peak_dbtp": 0.5})
    monkeypatch.setattr(downloader.loudness, "tag", lambda p, m: True)
    monkeypatch.setattr(downloader.lyrics, "for_track", for_track)
    monkeypatch.setattr(downloader.lyrics, "embed", lambda p, found: True)
    downloader._run(job)
    assert seen == {"status": "converting", "args": (VID, "Song", "Rick Astley", None, 213.0)}
    assert job["status"] == "done"
    entry = history.get(VID)
    assert entry["lyrics"] == "synced" and entry["gain_db"] == -8.7
    config.HISTORY_FILE.write_text("[]", encoding="utf-8")


def test_replaygain_off_skips_measuring(monkeypatch, tmp_path):
    f = tmp_path / "song.opus"
    f.write_bytes(b"x")
    _fake_keep(monkeypatch, f)
    monkeypatch.setattr(config, "REPLAYGAIN", False)
    monkeypatch.setattr(downloader.loudness, "measure", lambda p: pytest.fail("measured with REPLAYGAIN off"))
    monkeypatch.setattr(downloader.lyrics, "for_track", lambda *a: None)
    job = downloader._new_job(VID, "Song", "")
    downloader._run(job)
    assert job["status"] == "done" and history.get(VID)["gain_db"] is None
    config.HISTORY_FILE.write_text("[]", encoding="utf-8")


# ---------- where a keep lands: same titles never overwrite another track's file (audit F3) ----------

V1, V2, V3 = "aaaaaaaaaa1", "bbbbbbbbbb2", "ccccccccccc"
_RealYDL = yt_dlp.YoutubeDL


class FakeYDL(_RealYDL):
    """yt-dlp minus the network: extraction returns a canned result, processing writes the file where yt-dlp's own
    output template (as _run_ytdlp left it) says. Everything else (options, prepare_filename) is the real thing."""
    titles, extractions = {}, []

    def extract_info(self, url, download=True, process=True, **kw):
        vid = url.rsplit("=", 1)[1]
        FakeYDL.extractions.append((vid, download, process))
        raw = {"id": vid, "title": self.titles[vid], "channel": "Some Artist", "duration": 200, "ext": "webm",
               "webpage_url": url, "extractor": "youtube", "extractor_key": "Youtube"}
        return self.process_ie_result(raw, download) if process else raw

    def process_ie_result(self, ie_result, download=True, extra_info=None):
        path = self.prepare_filename({**ie_result, "ext": "opus"})
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"audio of {ie_result['id']} #{len(FakeYDL.extractions)}")
        return {**ie_result, "ext": "opus", "requested_downloads": [{"filepath": path}]}


@pytest.fixture
def keep_dir(monkeypatch, tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", music)
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(downloader, "TMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(history, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(downloader, "AUDIO_FORMAT", "opus")
    monkeypatch.setattr(downloader.navidrome, "rescan", lambda: False)
    monkeypatch.setattr(downloader.navidrome, "find_song", lambda *a: None)
    monkeypatch.setattr(downloader.loudness, "measure", lambda p: None)
    monkeypatch.setattr(downloader.lyrics, "for_track", lambda *a: None)
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(FakeYDL, "titles", {})
    monkeypatch.setattr(FakeYDL, "extractions", [])
    return music


def _keep_yt(vid, title):
    FakeYDL.titles[vid] = title
    job = downloader._new_job(vid, title, "")
    downloader._run(job)
    assert job["status"] == "done", job["error"]
    return job


def test_ytdlp_keeps_of_one_title_get_their_own_files(keep_dir):
    a = _keep_yt(V1, "Hallelujah")
    b = _keep_yt(V2, "Hallelujah")
    assert a["file"] == str(keep_dir / "Hallelujah.opus")  # a name nobody uses stays plain
    assert b["file"] == str(keep_dir / f"Hallelujah [{V2}].opus")
    assert (keep_dir / "Hallelujah.opus").read_text().startswith(f"audio of {V1}")  # the first keep's bytes
    assert history.get(V1)["file"] != history.get(V2)["file"]
    assert FakeYDL.extractions == [(V1, True, True), (V2, True, True)]  # one extraction per keep


def test_ytdlp_titles_that_sanitise_alike_and_untracked_files(keep_dir):
    _keep_yt(V1, "AC/DC")
    b = _keep_yt(V2, "AC⧸DC")  # yt-dlp writes "/" as "⧸": both names would be "AC⧸DC.opus"
    assert b["file"].endswith(f"AC⧸DC [{V2}].opus") and history.get(V1)["file"].endswith("AC⧸DC.opus")
    (keep_dir / "Intro.opus").write_text("the user's own file")
    (keep_dir / f"Intro [{V3}].opus").write_text("another file of theirs")
    c = _keep_yt(V3, "Intro")
    assert c["file"] == str(keep_dir / f"Intro [{V3}] (2).opus")
    assert (keep_dir / "Intro.opus").read_text() == "the user's own file"
    assert (keep_dir / f"Intro [{V3}].opus").read_text() == "another file of theirs"


def test_ytdlp_rekeep_of_the_same_track_replaces_its_own_file(keep_dir):
    _keep_yt(V1, "Hallelujah")
    b = _keep_yt(V2, "Hallelujah")
    again = _keep_yt(V2, "Hallelujah")  # force / re-keep: back into its own (suffixed) file
    assert again["file"] == b["file"] and (keep_dir / f"Hallelujah [{V2}].opus").read_text().endswith("#3")
    first = _keep_yt(V1, "Hallelujah")
    assert first["file"] == str(keep_dir / "Hallelujah.opus") and len(list(keep_dir.iterdir())) == 2
    assert (keep_dir / f"Hallelujah [{V2}].opus").read_text().endswith("#3")  # untouched by V1's re-keep


def test_a_file_two_tracks_already_share_is_not_rewritten(keep_dir):
    """History written before the fix may point two tracks at one file: a re-keep of either gets a file of its own."""
    shared = keep_dir / "Hallelujah.opus"
    shared.write_text("one of them")
    history._file.save([{"id": V1, "title": "Hallelujah", "file": str(shared)},
                        {"id": V2, "title": "Hallelujah", "file": str(shared)}])
    b = _keep_yt(V2, "Hallelujah")
    assert b["file"] == str(keep_dir / f"Hallelujah [{V2}].opus") and shared.read_text() == "one of them"


class _KeepSource:
    key, name, can_keep, codec_label = "mc", "Monochrome", True, "FLAC"

    def info(self, native, hint=None):
        return {"title": "Hallelujah", "artist": f"Artist {native}", "album": "", "art": "", "duration": 200}

    def art(self, native, size):
        return None


@pytest.fixture
def generic_keep(keep_dir, monkeypatch):
    monkeypatch.setitem(downloader.sources.SOURCES, "mc", _KeepSource())
    monkeypatch.setattr(downloader.streamer, "resolve", lambda tid: {"url": "http://mc/" + tid, "ext": "flac"})
    monkeypatch.setattr(downloader, "_fetch", lambda job, entry, dest, tid=None: dest.write_text(f"flac of {tid}"))
    monkeypatch.setattr(downloader, "_convert", lambda src, ext, dest, fmt=None: shutil.copy(src, dest))
    monkeypatch.setattr(downloader, "_tag", lambda *a, **k: None)
    monkeypatch.setattr(downloader, "_length", lambda p: None)
    return keep_dir


def _keep_generic(tid):
    job = downloader._new_job(tid, "", "")
    downloader._run_generic(job, fmt="flac")
    return job["file"]


def test_generic_keeps_of_one_title_get_their_own_files(generic_keep):
    music = generic_keep
    (music / "Hallelujah [333].flac").write_text("untracked")
    a, b, c = _keep_generic("mc:111"), _keep_generic("mc:222"), _keep_generic("mc:333")
    assert [a, b, c] == [str(music / "Hallelujah.flac"), str(music / "Hallelujah [222].flac"),
                         str(music / "Hallelujah [333] (2).flac")]
    assert [history.get(t)["file"] for t in ("mc:111", "mc:222", "mc:333")] == [a, b, c]
    assert (music / "Hallelujah.flac").read_text() == "flac of mc:111"  # the first keep's bytes survive
    assert (music / "Hallelujah [333].flac").read_text() == "untracked"
    assert _keep_generic("mc:222") == b and len(list(music.iterdir())) == 4  # re-keep: its own file again


# ---------- staging: a keep only touches files it made (root review of audit batch 2) ----------

def _wav(path, seconds):
    import struct
    import wave
    with wave.open(str(path), "wb") as w:
        w.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        w.writeframes(struct.pack("<h", 0) * 8000 * seconds)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_real_ytdlp_processing_never_reuses_or_deletes_files_it_didnt_make(keep_dir, tmp_path, monkeypatch):
    """Real yt-dlp + FFmpeg (download, cover convert / crop / embed, audio extract); only the extraction is local.
    A same-named .wav, cover and temp leftovers already in the library and in TMP_DIR must not be used or touched."""
    from mutagen import File as MutagenFile
    src = tmp_path / "src"
    src.mkdir()
    _wav(src / "source.wav", 1)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=64x48", "-frames:v", "1",
                    str(src / "cover.jpg")], check=True)
    untouched = {keep_dir / "Title.wav": None, keep_dir / "Title.jpg": b"not our cover",
                 keep_dir / "Title.webp": b"nor this", downloader.TMP_DIR / "Title.wav": None,
                 downloader.TMP_DIR / "Title.temp.opus": b"someone's temp file"}
    for path, data in untouched.items():
        _wav(path, 4) if data is None else path.write_bytes(data)
    before = {p: p.read_bytes() for p in untouched}

    class LocalYDL(_RealYDL):
        def extract_info(self, url, download=True, process=True, **kw):
            raw = {"id": V1, "title": "Title", "channel": "Artist", "duration": 1, "ext": "wav", "vcodec": "none",
                   "acodec": "pcm_s16le", "url": (src / "source.wav").as_uri(), "extractor": "generic",
                   "extractor_key": "Generic", "webpage_url": "https://example.invalid/" + V1,
                   "thumbnails": [{"id": "0", "url": (src / "cover.jpg").as_uri()}]}
            return self.process_ie_result(raw, download) if process else raw

    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", LocalYDL)
    monkeypatch.setattr(config, "YDL_EXTRA", {"enable_file_urls": True})
    job = downloader._new_job(V1, "Title", "Artist")
    downloader._run(job)
    assert job["status"] == "done", job["error"]
    assert job["file"] == str(keep_dir / "Title.opus")
    f = MutagenFile(job["file"])
    assert abs(f.info.length - 1) < 0.15  # the 1 s source, not the 4 s file that was already there
    assert f.get("metadata_block_picture")  # the cover went through convert / crop / embed in the stage
    assert {p: p.read_bytes() for p in untouched} == before
    assert sorted(p.name for p in downloader.TMP_DIR.iterdir()) == ["Title.temp.opus", "Title.wav"]  # stage removed
    assert sorted(p.name for p in keep_dir.iterdir()) == ["Title.jpg", "Title.opus", "Title.wav", "Title.webp"]


def test_an_alias_of_another_tracks_file_is_not_this_tracks_own(generic_keep):
    """History may point a track at a link (or another spelling) of a file another track owns: that's not its own
    file to replace."""
    music = generic_keep
    real, alias = music / "Hallelujah.flac", music / "Alias.flac"
    real.write_text("mc:111's audio")
    alias.symlink_to(real)
    history._file.save([{"id": "mc:111", "title": "Hallelujah", "file": str(real)},
                        {"id": "mc:222", "title": "Hallelujah", "file": str(alias)}])
    other = music / "Hallelujah [222].flac"
    assert downloader._keep_path("mc:222", [real, other]) == other
    assert _keep_generic("mc:222") == str(other)
    assert real.read_text() == "mc:111's audio" and alias.is_symlink()


def test_files_already_named_like_temp_parts_are_left_alone(generic_keep):
    music = generic_keep
    (music / "Hallelujah.flac.part").write_text("someone's half copy")
    (music / "Hallelujah.lrc").write_text("lyrics for another file")
    target = music / "elsewhere.txt"
    target.write_text("link target")
    (music / "Hallelujah [111].flac.part").symlink_to(target)
    got = _keep_generic("mc:111")
    assert got == str(music / "Hallelujah [111].flac")  # the plain name's .lrc belongs to something else
    assert (music / "Hallelujah.flac.part").read_text() == "someone's half copy"
    assert (music / "Hallelujah.lrc").read_text() == "lyrics for another file" and target.read_text() == "link target"
    assert (music / "Hallelujah [111].flac").read_text() == "flac of mc:111"
