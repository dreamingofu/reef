"""The interface every search/stream source implements."""

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")


class Source:
    key = ""            # two lowercase letters; non-YouTube track ids are "<key>:<native id>"
    name = ""           # shown on the search chips and the Now Playing pill
    can_keep = False    # whether /api/download can save its tracks into the library
    codec_label = ""    # what the stream usually is, e.g. "AAC"; a track's own `codec` wins

    def enabled(self):
        return True

    def track_id(self, native_id):
        return f"{self.key}:{native_id}"

    def search(self, q, limit):
        """Track dicts: {id, title, channel, duration, source, ...}."""
        raise NotImplementedError

    def resolve(self, native_id, turn=None):
        """{"url", "headers", "mime" (None = upstream Content-Type), "ext"} for the stream proxy.

        `turn` is set for background prefetches: a source that serialises on a shared resource calls
        `turn.wait()` before taking it and gives it back when `turn.should_yield()` (see streamer).
        """
        raise NotImplementedError

    def forget(self, native_id):
        """Called when a resolved URL failed upstream, before resolving it again."""

    def art(self, native_id, size):
        """("redirect", url) | ("bytes", data, mime) | None."""
        return None

    def info(self, native_id, hint=None):
        """Best-known tags for keeping a track: {title, artist, album, art, duration}; may be partial.

        `hint` is what the client knew ({"title", "artist"}), for sources that can't look an id up.
        """
        return {}

    def describe(self):
        return {"key": self.key, "name": self.name, "can_keep": self.can_keep, "codec_label": self.codec_label}
