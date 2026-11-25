from __future__ import annotations

import base64
import html
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

import requests

SPOTIFY_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0.0.0 Safari/537.36"
)
# Spotify serves full OpenGraph/music:* meta tags (including music:album) to minimal UAs
SPOTIFY_OG_USER_AGENT = "Mozilla/5.0"

_SPOTIFY_URL_RE = re.compile(
    r"(?:https?://open\.spotify\.com/(?:intl-[a-z0-9-]+/)?|spotify:)"
    r"(playlist|album|track)[/:]([A-Za-z0-9]+)",
    re.IGNORECASE,
)

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)

_META_TAG_RE = re.compile(
    r'<meta\s+[^>]*?(?:property|name)=["\']([^"\']+)["\'][^>]*?content=["\']([^"\']*)["\'][^>]*?>',
    re.IGNORECASE,
)
_META_TAG_REV_RE = re.compile(
    r'<meta\s+[^>]*?content=["\']([^"\']*)["\'][^>]*?(?:property|name)=["\']([^"\']+)["\'][^>]*?>',
    re.IGNORECASE,
)


def parse_spotify_url(url: str) -> tuple[str, str] | None:
    """Return (kind, spotify_id) for a Spotify playlist/album/track URL or URI."""
    if not url:
        return None
    match = _SPOTIFY_URL_RE.search(url.strip())
    if not match:
        return None
    return match.group(1).lower(), match.group(2)


def is_spotify_url(url: str) -> bool:
    return parse_spotify_url(url) is not None


def _clean_text(text: str | None) -> str:
    if not text:
        return ""
    return html.unescape(text).replace("\xa0", " ").strip()


def _split_artists(subtitle: str) -> list[str]:
    cleaned = _clean_text(subtitle)
    if not cleaned:
        return []
    return [part.strip() for part in cleaned.split(",") if part.strip()]


def _best_image_url(images: list[dict[str, Any]] | None) -> tuple[str | None, int, int]:
    if not images:
        return None, 0, 0
    best = max(
        images,
        key=lambda img: (img.get("maxWidth") or img.get("width") or 0)
        * (img.get("maxHeight") or img.get("height") or 0),
    )
    return (
        best.get("url"),
        int(best.get("maxWidth") or best.get("width") or 0),
        int(best.get("maxHeight") or best.get("height") or 0),
    )


@dataclass
class SpotifyAlbumTrack:
    track_id: str
    title: str
    artists: list[str]
    track_number: int
    duration_ms: int = 0
    is_playable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SpotifyAlbum:
    album_id: str
    name: str
    url: str
    total_tracks: int
    release_date: str = ""
    cover_url: str | None = None
    cover_width: int = 0
    cover_height: int = 0
    artists: list[str] = field(default_factory=list)
    tracks: list[SpotifyAlbumTrack] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tracks"] = [t.to_dict() for t in self.tracks]
        return data


@dataclass
class SpotifyTrack:
    position: int
    track_id: str
    title: str
    artists: list[str]
    url: str
    duration_ms: int = 0
    is_playable: bool = True
    is_explicit: bool = False
    album_id: str | None = None
    album_name: str = ""
    album_url: str | None = None
    album_total_tracks: int | None = None
    track_number: int | None = None
    release_date: str = ""
    artist_urls: list[str] = field(default_factory=list)
    cover_url: str | None = None
    cover_width: int = 0
    cover_height: int = 0
    album: SpotifyAlbum | None = None

    @property
    def primary_artist(self) -> str:
        return self.artists[0] if self.artists else ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["album"] = self.album.to_dict() if self.album else None
        return data


@dataclass
class SpotifyPlaylist:
    playlist_id: str
    name: str
    url: str
    owner: str = ""
    cover_url: str | None = None
    tracks: list[SpotifyTrack] = field(default_factory=list)
    source: str = "embed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "playlist_id": self.playlist_id,
            "name": self.name,
            "url": self.url,
            "owner": self.owner,
            "cover_url": self.cover_url,
            "source": self.source,
            "track_count": len(self.tracks),
            "tracks": [t.to_dict() for t in self.tracks],
        }


class SpotifyClient:
    """Fetches Spotify playlist, track, and album metadata without requiring API keys.

    If SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET are present in the environment,
    uses the official Spotify Web API for full pagination (>100 tracks) and rich metadata,
    falling back seamlessly to Spotify's public embed + OpenGraph pages.
    """

    def __init__(
        self,
        timeout: int = 20,
        max_workers: int = 8,
        proxies: dict[str, str] | None = None,
    ):
        self.timeout = timeout
        self.max_workers = max_workers
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": SPOTIFY_USER_AGENT})
        if proxies and any(proxies.values()):
            self.session.proxies.update(proxies)
        self._album_cache: dict[str, SpotifyAlbum] = {}
        self._api_token: str | None = None

    def _get_api_token(self) -> str | None:
        if self._api_token:
            return self._api_token
        client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
        client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            return None
        try:
            creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
            resp = self.session.post(
                "https://accounts.spotify.com/api/token",
                headers={"Authorization": f"Basic {creds}"},
                data={"grant_type": "client_credentials"},
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                self._api_token = resp.json().get("access_token")
                return self._api_token
        except Exception:
            pass
        return None

    def _fetch_embed_entity(self, kind: str, entity_id: str) -> dict[str, Any]:
        url = f"https://open.spotify.com/embed/{kind}/{entity_id}"
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        match = _NEXT_DATA_RE.search(resp.text)
        if not match:
            raise ValueError(f"Could not find __NEXT_DATA__ in Spotify embed page for {kind}/{entity_id}")
        data = json.loads(match.group(1))
        try:
            return data["props"]["pageProps"]["state"]["data"]["entity"]
        except KeyError as exc:
            raise ValueError(f"Unexpected Spotify embed JSON structure for {kind}/{entity_id}") from exc

    def _parse_meta_tags(self, html_text: str) -> dict[str, list[str]]:
        metas: dict[str, list[str]] = {}
        for key, val in _META_TAG_RE.findall(html_text):
            metas.setdefault(key.lower(), []).append(_clean_text(val))
        for val, key in _META_TAG_REV_RE.findall(html_text):
            k = key.lower()
            v = _clean_text(val)
            if v not in metas.get(k, []):
                metas.setdefault(k, []).append(v)
        return metas

    def fetch_track_metadata(self, track_id: str) -> dict[str, Any]:
        """Fetch track page OpenGraph/music metadata to obtain parent album URL, release date, and cover."""
        url = f"https://open.spotify.com/track/{track_id}"
        resp = self.session.get(
            url,
            headers={"User-Agent": SPOTIFY_OG_USER_AGENT},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        metas = self._parse_meta_tags(resp.text)

        album_url = (metas.get("music:album") or [None])[0]
        album_id = None
        if album_url:
            parsed = parse_spotify_url(album_url)
            if parsed and parsed[0] == "album":
                album_id = parsed[1]

        track_num_raw = (metas.get("music:album:track") or [None])[0]
        track_number = int(track_num_raw) if track_num_raw and track_num_raw.isdigit() else None

        cover_url = (metas.get("og:image") or [None])[0]
        cover_w = int((metas.get("og:image:width") or ["0"])[0] or 0)
        cover_h = int((metas.get("og:image:height") or ["0"])[0] or 0)

        og_desc = (metas.get("og:description") or [""])[0]
        album_name = ""
        # Format is usually: "Artist · Album Name · Song · Year"
        parts = [p.strip() for p in og_desc.split("·")]
        if len(parts) >= 2:
            album_name = parts[1]

        return {
            "title": (metas.get("og:title") or [""])[0],
            "album_id": album_id,
            "album_url": album_url,
            "album_name": album_name,
            "track_number": track_number,
            "release_date": (metas.get("music:release_date") or [""])[0],
            "artist_urls": metas.get("music:musician") or [],
            "artist_names": metas.get("music:musician_description") or [],
            "cover_url": cover_url,
            "cover_width": cover_w,
            "cover_height": cover_h,
            "duration_ms": int((metas.get("music:duration") or ["0"])[0] or 0) * 1000,
        }

    def fetch_album(self, album_id: str) -> SpotifyAlbum:
        if album_id in self._album_cache:
            return self._album_cache[album_id]

        entity = self._fetch_embed_entity("album", album_id)
        cover_url, cover_w, cover_h = _best_image_url(
            (entity.get("visualIdentity") or {}).get("image")
        )
        release_iso = ((entity.get("releaseDate") or {}).get("isoString") or "")
        release_date = release_iso[:10] if release_iso else ""
        album_name = _clean_text(entity.get("name") or entity.get("title") or "")

        tracks: list[SpotifyAlbumTrack] = []
        album_artists: list[str] = []
        for idx, item in enumerate(entity.get("trackList") or [], start=1):
            uri = item.get("uri", "")
            tid = uri.split(":")[-1] if ":" in uri else item.get("uid", "")
            artists = _split_artists(item.get("subtitle", ""))
            for artist in artists:
                if artist not in album_artists:
                    album_artists.append(artist)
            tracks.append(
                SpotifyAlbumTrack(
                    track_id=tid,
                    title=_clean_text(item.get("title", "")),
                    artists=artists,
                    track_number=idx,
                    duration_ms=int(item.get("duration") or 0),
                    is_playable=bool(item.get("isPlayable", True)),
                )
            )

        album = SpotifyAlbum(
            album_id=album_id,
            name=album_name,
            url=f"https://open.spotify.com/album/{album_id}",
            total_tracks=len(tracks),
            release_date=release_date,
            cover_url=cover_url,
            cover_width=cover_w,
            cover_height=cover_h,
            artists=album_artists,
            tracks=tracks,
        )
        self._album_cache[album_id] = album
        return album

    def _fetch_playlist_via_api(self, playlist_id: str, token: str) -> SpotifyPlaylist:
        headers = {"Authorization": f"Bearer {token}"}
        resp = self.session.get(
            f"https://api.spotify.com/v1/playlists/{playlist_id}",
            headers=headers,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        cover_url, _, _ = _best_image_url(data.get("images"))
        tracks: list[SpotifyTrack] = []
        next_url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks?limit=100"
        pos = 0
        while next_url:
            page = self.session.get(next_url, headers=headers, timeout=self.timeout)
            page.raise_for_status()
            payload = page.json()
            for item in payload.get("items") or []:
                tr = item.get("track")
                if not tr or not tr.get("id"):
                    continue
                pos += 1
                alb = tr.get("album") or {}
                alb_cover, alb_w, alb_h = _best_image_url(alb.get("images"))
                artists = [a["name"] for a in tr.get("artists") or [] if a.get("name")]
                artist_urls = [
                    (a.get("external_urls") or {}).get("spotify", "")
                    for a in tr.get("artists") or []
                    if (a.get("external_urls") or {}).get("spotify")
                ]
                tracks.append(
                    SpotifyTrack(
                        position=pos,
                        track_id=tr["id"],
                        title=_clean_text(tr.get("name", "")),
                        artists=artists,
                        url=(tr.get("external_urls") or {}).get(
                            "spotify", f"https://open.spotify.com/track/{tr['id']}"
                        ),
                        duration_ms=int(tr.get("duration_ms") or 0),
                        is_playable=bool(tr.get("is_playable", True)),
                        is_explicit=bool(tr.get("explicit", False)),
                        album_id=alb.get("id"),
                        album_name=_clean_text(alb.get("name", "")),
                        album_url=(alb.get("external_urls") or {}).get("spotify"),
                        album_total_tracks=alb.get("total_tracks"),
                        track_number=tr.get("track_number"),
                        release_date=alb.get("release_date", ""),
                        artist_urls=artist_urls,
                        cover_url=alb_cover,
                        cover_width=alb_w,
                        cover_height=alb_h,
                    )
                )
            next_url = payload.get("next")

        return SpotifyPlaylist(
            playlist_id=playlist_id,
            name=_clean_text(data.get("name", "Spotify Playlist")),
            url=(data.get("external_urls") or {}).get(
                "spotify", f"https://open.spotify.com/playlist/{playlist_id}"
            ),
            owner=_clean_text((data.get("owner") or {}).get("display_name", "")),
            cover_url=cover_url,
            tracks=tracks,
            source="web_api",
        )

    def fetch_playlist(
        self,
        url_or_id: str,
        enrich_albums: bool = True,
        progress_callback=None,
    ) -> SpotifyPlaylist:
        """Fetch a Spotify playlist and optionally enrich each track with parent album info."""
        parsed = parse_spotify_url(url_or_id)
        if parsed:
            kind, entity_id = parsed
            if kind == "album":
                return self.fetch_album_as_playlist(entity_id)
            if kind == "track":
                return self.fetch_single_track_as_playlist(entity_id, enrich_albums=enrich_albums)
            playlist_id = entity_id
        else:
            playlist_id = url_or_id.strip()

        token = self._get_api_token()
        playlist: SpotifyPlaylist | None = None
        if token:
            try:
                playlist = self._fetch_playlist_via_api(playlist_id, token)
            except Exception:
                playlist = None

        if playlist is None:
            entity = self._fetch_embed_entity("playlist", playlist_id)
            cover_url, _, _ = _best_image_url(
                (entity.get("visualIdentity") or {}).get("image")
                or (entity.get("coverArt") or {}).get("sources")
            )
            authors = entity.get("authors") or []
            owner = _clean_text(authors[0].get("name", "")) if authors else ""
            tracks: list[SpotifyTrack] = []
            for idx, item in enumerate(entity.get("trackList") or [], start=1):
                uri = item.get("uri", "")
                tid = uri.split(":")[-1] if ":" in uri else item.get("uid", "")
                tracks.append(
                    SpotifyTrack(
                        position=idx,
                        track_id=tid,
                        title=_clean_text(item.get("title", "")),
                        artists=_split_artists(item.get("subtitle", "")),
                        url=f"https://open.spotify.com/track/{tid}",
                        duration_ms=int(item.get("duration") or 0),
                        is_playable=bool(item.get("isPlayable", True)),
                        is_explicit=bool(item.get("isExplicit", False)),
                    )
                )
            playlist = SpotifyPlaylist(
                playlist_id=playlist_id,
                name=_clean_text(entity.get("name") or entity.get("title") or "Spotify Playlist"),
                url=f"https://open.spotify.com/playlist/{playlist_id}",
                owner=owner,
                cover_url=cover_url,
                tracks=tracks,
                source="embed",
            )

        if enrich_albums and playlist.tracks:
            self._enrich_tracks_with_albums(playlist.tracks, progress_callback=progress_callback)

        return playlist

    def fetch_album_as_playlist(self, album_id: str) -> SpotifyPlaylist:
        album = self.fetch_album(album_id)
        tracks: list[SpotifyTrack] = []
        for idx, tr in enumerate(album.tracks, start=1):
            tracks.append(
                SpotifyTrack(
                    position=idx,
                    track_id=tr.track_id,
                    title=tr.title,
                    artists=list(tr.artists),
                    url=f"https://open.spotify.com/track/{tr.track_id}",
                    duration_ms=tr.duration_ms,
                    is_playable=tr.is_playable,
                    album_id=album.album_id,
                    album_name=album.name,
                    album_url=album.url,
                    album_total_tracks=album.total_tracks,
                    track_number=tr.track_number,
                    release_date=album.release_date,
                    cover_url=album.cover_url,
                    cover_width=album.cover_width,
                    cover_height=album.cover_height,
                    album=album,
                )
            )
        return SpotifyPlaylist(
            playlist_id=album.album_id,
            name=album.name,
            url=album.url,
            owner=", ".join(album.artists[:2]),
            cover_url=album.cover_url,
            tracks=tracks,
            source="album_embed",
        )

    def fetch_single_track_as_playlist(self, track_id: str, enrich_albums: bool = True) -> SpotifyPlaylist:
        meta = self.fetch_track_metadata(track_id)
        track = SpotifyTrack(
            position=1,
            track_id=track_id,
            title=meta.get("title") or track_id,
            artists=meta.get("artist_names") or [],
            url=f"https://open.spotify.com/track/{track_id}",
            duration_ms=meta.get("duration_ms") or 0,
            album_id=meta.get("album_id"),
            album_name=meta.get("album_name") or "",
            album_url=meta.get("album_url"),
            track_number=meta.get("track_number"),
            release_date=meta.get("release_date") or "",
            artist_urls=meta.get("artist_urls") or [],
            cover_url=meta.get("cover_url"),
            cover_width=meta.get("cover_width") or 0,
            cover_height=meta.get("cover_height") or 0,
        )
        if enrich_albums and track.album_id:
            try:
                album = self.fetch_album(track.album_id)
                track.album = album
                track.album_name = album.name or track.album_name
                track.album_total_tracks = album.total_tracks
            except Exception:
                pass
        return SpotifyPlaylist(
            playlist_id=track_id,
            name=track.title,
            url=track.url,
            owner=track.primary_artist,
            cover_url=track.cover_url,
            tracks=[track],
            source="track_page",
        )

    def _enrich_tracks_with_albums(
        self, tracks: list[SpotifyTrack], progress_callback=None
    ) -> None:
        # Step 1: Fetch track page metadata for tracks missing album_id
        tracks_needing_meta = [t for t in tracks if not t.album_id and t.track_id]
        if tracks_needing_meta:
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                future_map = {
                    pool.submit(self.fetch_track_metadata, t.track_id): t
                    for t in tracks_needing_meta
                }
                done = 0
                for fut in as_completed(future_map):
                    track = future_map[fut]
                    done += 1
                    try:
                        meta = fut.result()
                        track.album_id = meta.get("album_id")
                        track.album_url = meta.get("album_url")
                        if meta.get("album_name") and not track.album_name:
                            track.album_name = meta["album_name"]
                        track.track_number = meta.get("track_number")
                        track.release_date = meta.get("release_date") or track.release_date
                        track.artist_urls = meta.get("artist_urls") or track.artist_urls
                        if meta.get("cover_url"):
                            track.cover_url = meta["cover_url"]
                            track.cover_width = meta.get("cover_width") or 0
                            track.cover_height = meta.get("cover_height") or 0
                    except Exception:
                        pass
                    if progress_callback:
                        progress_callback("track_meta", done, len(tracks_needing_meta))

        # Step 2: Fetch each unique album embed page to get exact total_tracks and full tracklist
        unique_album_ids = sorted({t.album_id for t in tracks if t.album_id})
        if unique_album_ids:
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                future_map = {
                    pool.submit(self.fetch_album, aid): aid for aid in unique_album_ids
                }
                done = 0
                for fut in as_completed(future_map):
                    done += 1
                    try:
                        fut.result()
                    except Exception:
                        pass
                    if progress_callback:
                        progress_callback("album_meta", done, len(unique_album_ids))

            for track in tracks:
                if track.album_id and track.album_id in self._album_cache:
                    alb = self._album_cache[track.album_id]
                    track.album = alb
                    track.album_name = alb.name or track.album_name
                    track.album_total_tracks = alb.total_tracks
                    if not track.release_date and alb.release_date:
                        track.release_date = alb.release_date
                    if (not track.cover_url or alb.cover_width > track.cover_width) and alb.cover_url:
                        track.cover_url = alb.cover_url
                        track.cover_width = alb.cover_width
                        track.cover_height = alb.cover_height
                    if track.track_number is None:
                        for at in alb.tracks:
                            if at.track_id == track.track_id:
                                track.track_number = at.track_number
                                break
