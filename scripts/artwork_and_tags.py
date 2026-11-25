from __future__ import annotations

import html
import re
import struct
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote

import requests
from mutagen.mp4 import MP4, MP4Cover

from .reference_checker import _artist_matches, _similarity, strip_title_annotations

ITUNES_USER_AGENT = "SaavnDownloader/1.0 (https://github.com/JuanmiRodas/Saavn-Downloader)"


@dataclass
class ArtworkInfo:
    url: str
    source: str  # "itunes" | "spotify" | "jiosaavn"
    width: int = 0
    height: int = 0
    mime_type: str = "image/jpeg"

    @property
    def resolution_label(self) -> str:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return "unknown"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["resolution"] = self.resolution_label
        return data


def detect_image_dimensions(data: bytes) -> tuple[int, int, str]:
    """Detect (width, height, mime_type) from PNG or JPEG raw bytes using stdlib only."""
    if not data or len(data) < 24:
        return 0, 0, "image/jpeg"

    # PNG signature
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h), "image/png"

    # JPEG signature
    if data[:2] == b"\xff\xd8":
        i = 2
        length = len(data)
        while i < length - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            while i < length and data[i] == 0xFF:
                i += 1
            if i >= length:
                break
            marker = data[i]
            i += 1
            # Standalone markers
            if marker in (0xD8, 0xD9, 0x01) or (0xD0 <= marker <= 0xD7):
                continue
            if i + 2 > length:
                break
            seg_len = struct.unpack(">H", data[i : i + 2])[0]
            if seg_len < 2:
                break
            # SOF0..SOF3, SOF5..SOF7, SOF9..SOF11, SOF13..SOF15
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                if i + 7 <= length:
                    h, w = struct.unpack(">HH", data[i + 3 : i + 7])
                    return int(w), int(h), "image/jpeg"
                break
            i += seg_len
        return 0, 0, "image/jpeg"

    return 0, 0, "image/jpeg"


class HighResArtworkFetcher:
    """Resolves the highest resolution official album artwork from iTunes, Spotify, and JioSaavn."""

    def __init__(self, timeout: int = 12, proxies: dict[str, str] | None = None):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": ITUNES_USER_AGENT})
        if proxies and any(proxies.values()):
            self.session.proxies.update(proxies)
        self._cache: dict[str, ArtworkInfo | None] = {}

    def _search_itunes_artwork(
        self,
        title: str,
        artists: list[str],
        album: str = "",
        artist_aliases: list[str] | None = None,
    ) -> ArtworkInfo | None:
        query_parts = [strip_title_annotations(title)]
        if artists:
            query_parts.append(artists[0])
        term = " ".join(p for p in query_parts if p).strip()
        if not term:
            return None

        expected_artists = list(artists) + list(artist_aliases or [])
        best_url: str | None = None
        best_score = 0.0

        # Query JP store first for Japanese/anime music, then US store
        for country in ("jp", "us"):
            try:
                url = (
                    "https://itunes.apple.com/search?"
                    f"term={quote(term)}&entity=song&limit=6&country={country}"
                )
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code != 200:
                    continue
                results = resp.json().get("results") or []
            except Exception:
                continue

            for item in results:
                art_100 = item.get("artworkUrl100")
                if not art_100:
                    continue
                t_name = item.get("trackName") or ""
                a_name = item.get("artistName") or ""
                c_name = item.get("collectionName") or ""

                artist_ok, a_score = _artist_matches([a_name], expected_artists)
                if not artist_ok:
                    continue
                t_score = max(
                    _similarity(t_name, title),
                    _similarity(t_name, strip_title_annotations(title)),
                )
                alb_score = _similarity(c_name, album) if album else 0.0
                if t_score < 0.65 and alb_score < 0.72:
                    continue

                score = 0.55 * t_score + 0.35 * a_score + 0.10 * alb_score
                if score > best_score:
                    best_score = score
                    best_url = art_100

            if best_url:
                break

        if not best_url:
            return None

        # Upgrade thumbnail URL to highest available resolution (up to 3000x3000)
        for dim in (3000, 1400, 1000, 600):
            candidate = re.sub(r"/\d+x\d+bb\.(jpg|png)$", f"/{dim}x{dim}bb.\\1", best_url)
            try:
                head = self.session.head(candidate, timeout=self.timeout)
                if head.status_code == 200:
                    ctype = head.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
                    return ArtworkInfo(
                        url=candidate,
                        source="itunes",
                        width=dim,
                        height=dim,
                        mime_type=ctype or "image/jpeg",
                    )
            except Exception:
                continue
        return None

    def resolve_best_artwork(
        self,
        title: str,
        artists: list[str],
        album: str = "",
        spotify_cover_url: str | None = None,
        spotify_width: int = 640,
        saavn_cover_url: str | None = None,
        artist_aliases: list[str] | None = None,
        allow_itunes: bool = True,
    ) -> ArtworkInfo | None:
        cache_key = f"{album or title}||{','.join(artists)}||{spotify_cover_url or ''}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        candidates: list[ArtworkInfo] = []

        if allow_itunes and artists:
            itunes_art = self._search_itunes_artwork(
                title=title,
                artists=artists,
                album=album,
                artist_aliases=artist_aliases,
            )
            if itunes_art:
                candidates.append(itunes_art)

        if spotify_cover_url:
            w = spotify_width or 640
            candidates.append(
                ArtworkInfo(
                    url=spotify_cover_url,
                    source="spotify",
                    width=w,
                    height=w,
                    mime_type="image/jpeg",
                )
            )

        if saavn_cover_url:
            hi_saavn = re.sub(r"-\d+x\d+\.(?:jpg|webp|png)$", "-500x500.jpg", saavn_cover_url)
            candidates.append(
                ArtworkInfo(
                    url=hi_saavn,
                    source="jiosaavn",
                    width=500,
                    height=500,
                    mime_type="image/jpeg",
                )
            )

        best = max(candidates, key=lambda c: c.width * c.height) if candidates else None
        self._cache[cache_key] = best
        return best

    def download_artwork_bytes(self, artwork: ArtworkInfo | None) -> tuple[bytes | None, ArtworkInfo | None]:
        if not artwork or not artwork.url:
            return None, artwork
        try:
            resp = self.session.get(artwork.url, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.content
            w, h, mime = detect_image_dimensions(data)
            if w and h:
                artwork = ArtworkInfo(
                    url=artwork.url,
                    source=artwork.source,
                    width=w,
                    height=h,
                    mime_type=mime,
                )
            return data, artwork
        except Exception:
            return None, artwork


def write_rich_mp4_tags(
    filepath: str,
    *,
    title: str,
    artists: list[str],
    album: str = "",
    album_artists: list[str] | None = None,
    track_number: int | None = None,
    total_tracks: int | None = None,
    disc_number: int | None = None,
    total_discs: int | None = None,
    release_date: str = "",
    genre: str = "",
    composers: list[str] | None = None,
    copyright_text: str = "",
    comment: str = "",
    cover_bytes: bytes | None = None,
) -> None:
    """Embed rich metadata and highest-resolution cover art into an MP4/M4A file."""
    audio = MP4(filepath)

    if title:
        audio["\xa9nam"] = [html.unescape(title)]
    if artists:
        audio["\xa9ART"] = [html.unescape(a) for a in artists if a]
    if album_artists:
        audio["aART"] = [html.unescape(a) for a in album_artists if a]
    elif artists:
        audio["aART"] = [html.unescape(artists[0])]
    if album:
        audio["\xa9alb"] = [html.unescape(album)]
    if track_number is not None and track_number > 0:
        audio["trkn"] = [(int(track_number), int(total_tracks or 0))]
    if disc_number is not None and disc_number > 0:
        audio["disk"] = [(int(disc_number), int(total_discs or 1))]
    if release_date:
        audio["\xa9day"] = [html.unescape(str(release_date))]
    if genre:
        audio["\xa9gen"] = [html.unescape(genre)]
    if composers:
        audio["\xa9wrt"] = [html.unescape(c) for c in composers if c]
    if copyright_text:
        audio["cprt"] = [html.unescape(copyright_text)]
    if comment:
        audio["\xa9cmt"] = [html.unescape(comment)]

    if cover_bytes:
        _, _, mime = detect_image_dimensions(cover_bytes)
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(cover_bytes, imageformat=fmt)]

    audio.save()
