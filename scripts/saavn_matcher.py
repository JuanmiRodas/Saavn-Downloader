from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import quote

import requests

from .reference_checker import (
    ReferenceSong,
    _artist_matches,
    _similarity,
    detect_cover_or_karaoke,
    strip_title_annotations,
)


@dataclass
class SaavnMatch:
    song_id: str
    title: str
    artists: list[str]
    album: str
    album_id: str
    year: str
    duration_sec: int
    encrypted_media_url: str
    is_320kbps: bool
    image_url: str
    perma_url: str = ""
    language: str = ""
    label: str = ""
    copyright_text: str = ""
    singers: list[str] = field(default_factory=list)
    music: list[str] = field(default_factory=list)
    starring: list[str] = field(default_factory=list)
    artist_verified: bool = False
    match_score: float = 0.0
    title_score: float = 0.0
    artist_score: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw", None)
        return data


def _extract_json_payload(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    for line in text.splitlines():
        if line.strip().startswith("{"):
            return json.loads(line)
    raise ValueError("No JSON object found in JioSaavn response")


def _clean_saavn_text(val: Any) -> str:
    if not val:
        return ""
    return html.unescape(str(val)).strip()


def _parse_v4_song(item: dict[str, Any]) -> SaavnMatch | None:
    more = item.get("more_info") or {}
    enc_url = more.get("encrypted_media_url") or item.get("encrypted_media_url") or ""
    if not enc_url:
        return None

    artist_map = more.get("artistMap") or {}
    primary = [
        _clean_saavn_text(a.get("name"))
        for a in (artist_map.get("primary_artists") or [])
        if a.get("name")
    ]
    if not primary:
        pa_raw = more.get("primary_artists") or item.get("primary_artists") or ""
        primary = [_clean_saavn_text(p) for p in str(pa_raw).split(",") if p.strip()]

    all_artists = artist_map.get("artists") or []
    singers = [
        _clean_saavn_text(a.get("name"))
        for a in all_artists
        if a.get("role") == "singer" and a.get("name")
    ] or list(primary)
    music = [
        _clean_saavn_text(a.get("name"))
        for a in all_artists
        if a.get("role") == "music" and a.get("name")
    ]
    if not music and more.get("music"):
        music = [_clean_saavn_text(m) for m in str(more.get("music")).split(",") if m.strip()]
    starring = [
        _clean_saavn_text(a.get("name"))
        for a in all_artists
        if a.get("role") == "starring" and a.get("name")
    ]

    image = _clean_saavn_text(item.get("image") or "")
    if image:
        image = re.sub(r"-\d+x\d+\.(?:jpg|webp|png)$", "-500x500.jpg", image)

    try:
        dur = int(more.get("duration") or item.get("duration") or 0)
    except (ValueError, TypeError):
        dur = 0

    return SaavnMatch(
        song_id=str(item.get("id") or ""),
        title=_clean_saavn_text(item.get("title") or item.get("song") or ""),
        artists=primary,
        album=_clean_saavn_text(more.get("album") or item.get("album") or ""),
        album_id=str(more.get("album_id") or item.get("albumid") or ""),
        year=_clean_saavn_text(item.get("year") or ""),
        duration_sec=dur,
        encrypted_media_url=enc_url,
        is_320kbps=str(more.get("320kbps") or item.get("320kbps") or "").lower() == "true",
        image_url=image,
        perma_url=_clean_saavn_text(item.get("perma_url") or ""),
        language=_clean_saavn_text(item.get("language") or ""),
        label=_clean_saavn_text(more.get("label") or item.get("label") or ""),
        copyright_text=_clean_saavn_text(more.get("copyright_text") or item.get("copyright_text") or ""),
        singers=singers,
        music=music,
        starring=starring,
        raw=item,
    )


def _parse_legacy_song(item: dict[str, Any]) -> SaavnMatch | None:
    enc_url = item.get("encrypted_media_url") or ""
    if not enc_url:
        return None
    primary = [
        _clean_saavn_text(p)
        for p in str(item.get("primary_artists") or "").split(",")
        if p.strip()
    ]
    singers = [
        _clean_saavn_text(p)
        for p in str(item.get("singers") or "").split(",")
        if p.strip()
    ] or list(primary)
    music = [
        _clean_saavn_text(p)
        for p in str(item.get("music") or "").split(",")
        if p.strip()
    ]
    starring = [
        _clean_saavn_text(p)
        for p in str(item.get("starring") or "").split(",")
        if p.strip()
    ]
    image = _clean_saavn_text(item.get("image") or "")
    if image:
        image = re.sub(r"-\d+x\d+\.(?:jpg|webp|png)$", "-500x500.jpg", image)
    try:
        dur = int(item.get("duration") or 0)
    except (ValueError, TypeError):
        dur = 0

    return SaavnMatch(
        song_id=str(item.get("id") or ""),
        title=_clean_saavn_text(item.get("song") or item.get("title") or ""),
        artists=primary,
        album=_clean_saavn_text(item.get("album") or ""),
        album_id=str(item.get("albumid") or ""),
        year=_clean_saavn_text(item.get("year") or ""),
        duration_sec=dur,
        encrypted_media_url=enc_url,
        is_320kbps=str(item.get("320kbps") or "").lower() == "true",
        image_url=image,
        perma_url=_clean_saavn_text(item.get("perma_url") or ""),
        language=_clean_saavn_text(item.get("language") or ""),
        label=_clean_saavn_text(item.get("label") or ""),
        copyright_text=_clean_saavn_text(item.get("copyright_text") or ""),
        singers=singers,
        music=music,
        starring=starring,
        raw=item,
    )


class SaavnMatcher:
    """Searches JioSaavn and strictly verifies that matches are original recordings, not covers."""

    def __init__(
        self,
        proxies: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 15,
    ):
        self.session = requests.Session()
        if headers:
            self.session.headers.update(headers)
        else:
            self.session.headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
                    )
                }
            )
        if proxies and any(proxies.values()):
            self.session.proxies.update(proxies)
        self.timeout = timeout
        self._artist_verified_cache: dict[str, bool] = {}
        self._album_cache: dict[str, list[SaavnMatch]] = {}

    def search_songs(self, query: str, limit: int = 8) -> list[SaavnMatch]:
        if not query.strip():
            return []
        url = (
            "https://www.jiosaavn.com/api.php?"
            "__call=search.getResults&_format=json&_marker=0&api_version=4&ctx=web6dot0"
            f"&p=1&n={limit}&q={quote(query.strip())}"
        )
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code != 200:
                return []
            payload = _extract_json_payload(resp.text)
            results: list[SaavnMatch] = []
            for item in payload.get("results") or []:
                parsed = _parse_v4_song(item)
                if parsed:
                    results.append(parsed)
            return results
        except Exception:
            return []

    def check_artist_verified(self, artist_id: str) -> bool:
        if not artist_id:
            return False
        if artist_id in self._artist_verified_cache:
            return self._artist_verified_cache[artist_id]
        url = (
            "https://www.jiosaavn.com/api.php?"
            f"_marker=0&_format=json&__call=artist.getArtistPageDetails&artistId={artist_id}&n_song=1&n_album=1"
        )
        verified = False
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                payload = _extract_json_payload(resp.text)
                verified = bool(payload.get("isVerified", False))
        except Exception:
            verified = False
        self._artist_verified_cache[artist_id] = verified
        return verified

    def fetch_album_songs(self, album_id: str) -> list[SaavnMatch]:
        if not album_id:
            return []
        if album_id in self._album_cache:
            return self._album_cache[album_id]
        url = (
            "https://www.jiosaavn.com/api.php?"
            f"_format=json&__call=content.getAlbumDetails&albumid={album_id}"
        )
        songs: list[SaavnMatch] = []
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                payload = _extract_json_payload(resp.text)
                for item in payload.get("songs") or []:
                    parsed = _parse_legacy_song(item)
                    if parsed:
                        songs.append(parsed)
        except Exception:
            songs = []
        self._album_cache[album_id] = songs
        return songs

    def _build_queries(
        self,
        title: str,
        artists: list[str],
        reference: ReferenceSong | None = None,
    ) -> list[str]:
        queries: list[str] = []
        primary_artist = artists[0] if artists else ""
        clean_title = strip_title_annotations(title)

        def _add(q: str) -> None:
            q = re.sub(r"\s+", " ", q).strip()
            if q and q not in queries:
                queries.append(q)

        if reference:
            # Prefer Latin/romanized title + artist first since JioSaavn indexes mostly in Latin script
            latin_titles = [t for t in reference.all_titles() if t and t.isascii()]
            latin_artists = [a for a in reference.all_artists() if a and a.isascii()]
            if latin_titles and latin_artists:
                _add(f"{latin_titles[0]} {latin_artists[0]}")
            _add(f"{reference.title} {reference.artist}")

        if clean_title and primary_artist:
            _add(f"{clean_title} {primary_artist}")
        if title and primary_artist and title != clean_title:
            _add(f"{title} {primary_artist}")

        if not queries and title:
            _add(title)
        return queries[:3]

    def match_track(
        self,
        title: str,
        artists: list[str],
        duration_ms: int = 0,
        reference: ReferenceSong | None = None,
        allow_instrumental: bool = False,
    ) -> tuple[SaavnMatch | None, str]:
        """Search JioSaavn and return (verified_match, diagnostic_note).

        Strictly rejects covers, karaoke, lofi, piano, remixes, live, slowed/nightcore,
        TV-size cuts, alternate versions, and wrong artists.
        """
        queries = self._build_queries(title, artists, reference=reference)
        candidates_by_id: dict[str, SaavnMatch] = {}
        for q in queries:
            for cand in self.search_songs(q, limit=6):
                if cand.song_id and cand.song_id not in candidates_by_id:
                    candidates_by_id[cand.song_id] = cand

        if not candidates_by_id:
            return None, "No search results returned by JioSaavn"

        expected_titles = [title, strip_title_annotations(title)]
        expected_artists = list(artists)
        if reference:
            expected_titles.extend(reference.all_titles())
            expected_artists.extend(reference.all_artists())
        expected_blob = " ".join(expected_titles).lower()

        alt_version_checks: list[tuple[re.Pattern[str], str, str]] = [
            (re.compile(r"\bremix\b", re.I), "remix", "Remix / alternate version"),
            (re.compile(r"\blive\b|\bconcert\b|\btour\s+ver", re.I), "live", "Live concert version"),
            (re.compile(r"\bslowed\b|\breverb\b|\bnightcore\b|\bsped\s*up\b|\bspeed\s*up\b|\b8d\b", re.I), "slowed", "Slowed / nightcore / edited version"),
            (re.compile(r"\bacoustic\b|\bunplugged\b", re.I), "acoustic", "Acoustic / unplugged version"),
            (re.compile(r"\btv\s*size\b|\btv\s*edit\b|\banime\s*size\b|\bshort\s*ver", re.I), "tv size", "TV-size / shortened edit"),
            (re.compile(r"\bmashup\b|\bmedley\b|\bre-?recorded\b|\benglish\s*(?:ver|dub)\b|\bspanish\s*ver|\bfrench\s*ver|\b4kids\b", re.I), "mashup", "Alternate / foreign dub / re-recorded version"),
        ]

        best_match: SaavnMatch | None = None
        best_score = -1.0
        rejection_reasons: list[str] = []

        target_sec = duration_ms // 1000 if duration_ms else 0

        for cand in candidates_by_id.values():
            # 1. Reject obvious covers / karaoke / lofi / piano / instrumental
            cover_flags = detect_cover_or_karaoke(
                cand.title,
                cand.artists or cand.singers,
                allow_instrumental=allow_instrumental,
                album_name=cand.album,
            )
            if not allow_instrumental and cand.language.lower() == "instrumental":
                cover_flags.append("JioSaavn track language is instrumental")
            if cover_flags:
                rejection_reasons.append(f"Rejected '{cand.title}' by {', '.join(cand.artists)}: {cover_flags[0]}")
                continue

            # 2. Reject alternate versions (remix, live, slowed, acoustic, TV-size, dub) unless expected
            cand_blob = f"{cand.title} {cand.album}"
            alt_rejected = False
            for pat, kw, reason in alt_version_checks:
                if pat.search(cand_blob) and kw not in expected_blob:
                    rejection_reasons.append(f"Rejected '{cand.title}': {reason}")
                    alt_rejected = True
                    break
            if alt_rejected:
                continue

            # 3. Title similarity (strict)
            cand_title_variants = [cand.title, strip_title_annotations(cand.title)]
            title_score = 0.0
            for ct in cand_title_variants:
                for et in expected_titles:
                    if ct and et:
                        title_score = max(title_score, _similarity(ct, et))

            if title_score < 0.78:
                continue

            # 4. Artist verification (must match original official artist!)
            cand_artists = list(dict.fromkeys([*cand.artists, *cand.singers]))
            artist_ok, artist_score = _artist_matches(cand_artists, expected_artists)
            if not artist_ok:
                rejection_reasons.append(
                    f"Rejected '{cand.title}' by {', '.join(cand_artists) or 'Unknown'}: artist mismatch"
                )
                continue

            # 5. Duration verification (reject mismatched-length edits/TV-size/live cuts)
            dur_bonus = 0.0
            if target_sec >= 90 and cand.duration_sec > 0:
                diff = abs(cand.duration_sec - target_sec)
                if diff > 40:
                    rejection_reasons.append(
                        f"Rejected '{cand.title}': duration mismatch ({cand.duration_sec}s vs official {target_sec}s)"
                    )
                    continue
                if diff <= 10:
                    dur_bonus = 0.08
                elif diff <= 25:
                    dur_bonus = 0.03

            quality_bonus = 0.03 if cand.is_320kbps else 0.0
            total_score = (0.55 * title_score) + (0.45 * artist_score) + dur_bonus + quality_bonus

            if total_score > best_score:
                best_score = total_score
                cand.title_score = round(title_score, 3)
                cand.artist_score = round(artist_score, 3)
                cand.match_score = round(min(1.0, max(0.0, total_score)), 3)
                best_match = cand

        if best_match is None:
            if rejection_reasons:
                return None, f"Only unverified/cover/alternate versions on JioSaavn ({rejection_reasons[0]})"
            return None, "No verified original recording found on JioSaavn"

        # Check verified badge on JioSaavn artist profile if primary artist ID is available
        artist_map = (best_match.raw.get("more_info") or {}).get("artistMap") or {}
        primary_objs = artist_map.get("primary_artists") or []
        if primary_objs and primary_objs[0].get("id"):
            best_match.artist_verified = self.check_artist_verified(str(primary_objs[0]["id"]))

        return best_match, "Verified original JioSaavn match"
