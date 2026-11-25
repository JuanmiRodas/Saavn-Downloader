from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..anime_catalog import AnimeCatalogManager, detect_anime_franchise, is_soundtrack_album
from ..artwork_and_tags import ArtworkInfo, HighResArtworkFetcher, write_rich_mp4_tags
from ..download_manager import Manager
from ..reference_checker import ReferenceChecker, ReferenceSong, VerificationResult
from ..saavn_matcher import SaavnMatch, SaavnMatcher
from ..spotify_client import SpotifyClient, SpotifyPlaylist, SpotifyTrack
from ..todo_exporter import export_todo_and_plan


def compute_album_expansion(
    track: dict[str, Any],
    include_albums: bool,
    max_album_tracks: int,
) -> tuple[bool, str]:
    """Decide whether a track's parent single/album should be expanded based on max_album_tracks."""
    total = track.get("album_total_tracks")
    alb_name = track.get("album_name") or ""
    if not include_albums:
        return False, "Songs-only mode"
    if is_soundtrack_album(alb_name):
        return False, f"Soundtrack excluded ('{alb_name}')"
    if total is None:
        return False, "Album track count unknown (song only)"
    if total <= 1:
        return False, "Single-track release (1 track)"
    if total <= max_album_tracks:
        return True, f"Expand full single/EP ({total} tracks <= max {max_album_tracks})"
    return False, f"Song only ({total} tracks > max {max_album_tracks})"


class SpotifyPlaylistDownloader:
    """End-to-end Spotify Playlist -> Verification -> JioSaavn Downloader + CD To-Do Exporter."""

    def __init__(
        self,
        proxies: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        out_folder: str | None = None,
        strict_versions: bool = False,
        use_network_refs: bool = True,
    ):
        self.proxies = proxies or {}
        self.headers = headers or {}
        self.out_folder = out_folder
        self.strict_versions = strict_versions
        self.spotify = SpotifyClient(proxies=self.proxies)
        self.checker = ReferenceChecker(use_network=use_network_refs, strict_versions=strict_versions)
        self.saavn = SaavnMatcher(proxies=self.proxies, headers=self.headers)
        self.artwork = HighResArtworkFetcher(proxies=self.proxies)

    def analyze(
        self,
        url: str,
        include_albums: bool = False,
        max_album_tracks: int = 6,
        reference_mode: str = "auto",
        resolve_highres_art: bool = True,
        sync_gallery: bool = True,
        progress_callback=None,
    ) -> dict[str, Any]:
        """Fetch Spotify playlist, crosscheck artists/references, match JioSaavn, and resolve high-res art."""
        if progress_callback:
            progress_callback("Fetching Spotify playlist & parent album metadata...", 5)

        playlist: SpotifyPlaylist = self.spotify.fetch_playlist(
            url,
            enrich_albums=True,
            progress_callback=lambda stage, done, total: (
                progress_callback(
                    f"Loading Spotify {stage.replace('_', ' ')} ({done}/{total})...",
                    5 + int(25 * done / max(1, total)),
                )
                if progress_callback
                else None
            ),
        )

        if progress_callback:
            progress_callback("Crosschecking tracks against authoritative theme lists & artist verifier...", 35)

        resolved_mode, verifications, missing_refs = self.checker.crosscheck_playlist(
            playlist.name, playlist.tracks, reference_mode=reference_mode
        )

        # Match JioSaavn & resolve highest-resolution cover art concurrently
        def _process_track(
            idx: int, track: SpotifyTrack, ver: VerificationResult
        ) -> dict[str, Any]:
            ref = ver.matched_reference
            saavn_match: SaavnMatch | None = None
            saavn_note = ""

            if ver.status == "flagged":
                # Even if flagged on Spotify, optionally check if JioSaavn has the TRUE original
                # (using the official reference song!) while keeping it disabled by default so the user knows
                if ref is not None:
                    alt_match, alt_note = self.saavn.match_track(
                        title=ref.title,
                        artists=[ref.artist],
                        reference=ref,
                    )
                    if alt_match:
                        saavn_match = alt_match
                        saavn_note = (
                            f"Spotify entry flagged, but official original found on JioSaavn "
                            f"('{alt_match.title}' by {', '.join(alt_match.artists)})"
                        )
                    else:
                        saavn_note = f"Flagged on Spotify; official original not on JioSaavn ({alt_note})"
                else:
                    saavn_note = "Disabled by default (flagged as cover/karaoke/unofficial)"
            else:
                saavn_match, saavn_note = self.saavn.match_track(
                    title=track.title,
                    artists=track.artists,
                    duration_ms=track.duration_ms,
                    reference=ref,
                )

            # Resolve highest-resolution album cover (preferring official reference artwork if Spotify track was a cover)
            art_title = ref.title if (ver.status == "flagged" and ref) else track.title
            art_artists = [ref.artist] if (ver.status == "flagged" and ref) else track.artists
            art_album = "" if ver.status == "flagged" else track.album_name
            best_art: ArtworkInfo | None = self.artwork.resolve_best_artwork(
                title=art_title,
                artists=art_artists,
                album=art_album,
                spotify_cover_url=None if ver.status == "flagged" else track.cover_url,
                spotify_width=track.cover_width,
                saavn_cover_url=saavn_match.image_url if saavn_match else None,
                artist_aliases=ref.artist_aliases if ref else None,
                allow_itunes=resolve_highres_art,
            )
            # If flagged and iTunes had no cover, keep Spotify cover as preview fallback
            if best_art is None and track.cover_url:
                best_art = ArtworkInfo(
                    url=track.cover_url,
                    source="spotify",
                    width=track.cover_width or 640,
                    height=track.cover_height or 640,
                )

            track_dict = track.to_dict()
            will_expand, expand_reason = compute_album_expansion(
                track_dict, include_albums=include_albums, max_album_tracks=max_album_tracks
            )

            enabled_by_default = bool(ver.enabled_by_default and saavn_match is not None)

            return {
                "index": idx,
                "position": track.position,
                "track": track_dict,
                "verification": ver.to_dict(),
                "saavn_match": saavn_match.to_dict() if saavn_match else None,
                "saavn_note": saavn_note,
                "artwork": best_art.to_dict() if best_art else None,
                "will_expand_album": will_expand,
                "album_decision_reason": expand_reason,
                "enabled_by_default": enabled_by_default,
            }

        items: list[dict[str, Any]] = []
        total_tracks = len(playlist.tracks)
        if total_tracks:
            ordered: dict[int, dict[str, Any]] = {}
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = {
                    pool.submit(_process_track, idx, tr, ver): idx
                    for idx, (tr, ver) in enumerate(zip(playlist.tracks, verifications), start=1)
                }
                done = 0
                for fut in as_completed(futures):
                    idx = futures[fut]
                    done += 1
                    ordered[idx] = fut.result()
                    if progress_callback:
                        progress_callback(
                            f"Verifying JioSaavn & high-res covers ({done}/{total_tracks})...",
                            35 + int(60 * done / total_tracks),
                        )
            items = [ordered[k] for k in sorted(ordered.keys())]

        ready_count = sum(1 for it in items if it["enabled_by_default"])
        flagged_count = sum(1 for it in items if it["verification"]["status"] == "flagged")
        warning_count = sum(1 for it in items if it["verification"]["status"] == "warning")
        unavailable_count = sum(
            1
            for it in items
            if it["verification"]["status"] != "flagged" and not it["saavn_match"]
        )
        expand_count = sum(1 for it in items if it["will_expand_album"] and it["enabled_by_default"])

        detected_franchise = detect_anime_franchise(playlist.name)

        plan: dict[str, Any] = {
            "playlist": playlist.to_dict(),
            "reference_mode": resolved_mode,
            "detected_anime_slug": detected_franchise.slug if detected_franchise else None,
            "detected_anime_name": detected_franchise.name if detected_franchise else None,
            "options": {
                "include_albums": include_albums,
                "max_album_tracks": max_album_tracks,
                "strict_versions": self.strict_versions,
            },
            "summary": {
                "total_tracks": total_tracks,
                "ready_to_download": ready_count,
                "flagged_tracks": flagged_count,
                "warning_tracks": warning_count,
                "unavailable_on_saavn": unavailable_count,
                "missing_official_themes": len(missing_refs),
                "albums_to_expand": expand_count,
            },
            "items": items,
            "missing_references": [m.to_dict() for m in missing_refs],
        }

        if sync_gallery and detected_franchise is not None:
            try:
                catalog = AnimeCatalogManager(proxies=self.proxies, headers=self.headers)
                catalog.merge_spotify_plan_into_gallery(detected_franchise.slug, plan)
            except Exception:
                pass

        if progress_callback:
            progress_callback("Analysis complete!", 100)

        return plan

    def _download_and_tag_single_saavn_track(
        self,
        manager: Manager,
        saavn_song: dict[str, Any],
        dest_dir: str,
        *,
        title: str,
        artists: list[str],
        album: str,
        album_artists: list[str],
        track_number: int | None,
        total_tracks: int | None,
        release_date: str,
        genre: str,
        comment: str,
        artwork_info: ArtworkInfo | None,
        show_progress: bool = False,
    ) -> dict[str, Any]:
        prefix = f"{track_number:02d} - " if track_number else ""
        filename = manager.format_filename(f"{prefix}{title}")
        location = os.path.join(dest_dir, filename)
        os.makedirs(dest_dir, exist_ok=True)

        downloaded, bitrate = manager.start_download_with_fallback(
            filename,
            location,
            saavn_song["encrypted_media_url"],
            show_progress=show_progress,
        )

        cover_res = artwork_info.resolution_label if artwork_info else "none"
        if downloaded:
            cover_bytes, resolved_art = self.artwork.download_artwork_bytes(artwork_info)
            if resolved_art:
                cover_res = resolved_art.resolution_label
            write_rich_mp4_tags(
                location,
                title=title,
                artists=artists,
                album=album,
                album_artists=album_artists,
                track_number=track_number,
                total_tracks=total_tracks,
                disc_number=1,
                total_discs=1,
                release_date=release_date or saavn_song.get("year") or "",
                genre=genre,
                composers=saavn_song.get("music") or None,
                copyright_text=saavn_song.get("copyright_text") or saavn_song.get("label") or "",
                comment=comment,
                cover_bytes=cover_bytes,
            )

        return {
            "file": location,
            "title": title,
            "status": "downloaded" if downloaded else "already_exists",
            "bitrate": bitrate,
            "cover_resolution": cover_res,
        }

    def download_plan(
        self,
        plan: dict[str, Any],
        *,
        selected_positions: set[int] | None = None,
        include_albums: bool | None = None,
        max_album_tracks: int | None = None,
        out_folder: str | None = None,
        dry_run: bool = False,
        show_progress: bool = True,
        progress_callback=None,
    ) -> dict[str, Any]:
        """Download selected tracks (and eligible singles/albums) and export CD_TODO_LIST.md."""
        opts = plan.get("options") or {}
        eff_include_albums = opts.get("include_albums", False) if include_albums is None else include_albums
        eff_max_tracks = int(opts.get("max_album_tracks", 6) if max_album_tracks is None else max_album_tracks)
        base_out = out_folder if out_folder is not None else self.out_folder

        manager = Manager(out_folder=base_out or "")
        playlist_name = manager.sanitize_name((plan.get("playlist") or {}).get("name") or "Spotify Playlist")
        playlist_dir = manager.get_download_location(playlist_name)
        os.makedirs(playlist_dir, exist_ok=True)

        # Always export the CD To-Do list and download plan JSON into the playlist folder
        todo_path, plan_path = export_todo_and_plan(playlist_dir, plan)

        items = plan.get("items") or []
        to_download = [
            it
            for it in items
            if (
                (selected_positions is None and it.get("enabled_by_default"))
                or (selected_positions is not None and it.get("position") in selected_positions)
            )
            and it.get("saavn_match")
        ]

        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        downloaded_albums: set[str] = set()
        genre = "Anime" if plan.get("detected_anime_slug") else ""

        if dry_run:
            return {
                "playlist_dir": playlist_dir,
                "todo_path": todo_path,
                "plan_path": plan_path,
                "dry_run": True,
                "selected_count": len(to_download),
                "downloaded": [],
                "errors": [],
            }

        for idx, it in enumerate(to_download, start=1):
            tr = it.get("track") or {}
            ver = it.get("verification") or {}
            ref = ver.get("matched_reference")
            saavn_song = it.get("saavn_match")
            art_dict = it.get("artwork")
            art_info = (
                ArtworkInfo(
                    url=art_dict["url"],
                    source=art_dict.get("source", "spotify"),
                    width=art_dict.get("width", 0),
                    height=art_dict.get("height", 0),
                    mime_type=art_dict.get("mime_type", "image/jpeg"),
                )
                if art_dict and art_dict.get("url")
                else None
            )

            will_expand, _ = compute_album_expansion(tr, eff_include_albums, eff_max_tracks)
            album_id = tr.get("album_id") or saavn_song.get("album_id") or ""
            album_name = tr.get("album_name") or saavn_song.get("album") or ""
            comment = (
                f"{ref['label']} (Episodes {ref['episodes']})"
                if ref and ref.get("episodes")
                else (ref["label"] if ref else f"From Spotify playlist: {playlist_name}")
            )

            if progress_callback:
                progress_callback(
                    f"Downloading ({idx}/{len(to_download)}): {tr.get('title', '')}",
                    int(100 * (idx - 1) / max(1, len(to_download))),
                )

            try:
                if will_expand and album_name:
                    if album_id and album_id in downloaded_albums:
                        continue
                    if album_id:
                        downloaded_albums.add(album_id)

                    album_folder = os.path.join(playlist_dir, manager.sanitize_name(album_name))
                    album_tracks_done = 0

                    # Strategy 1: If JioSaavn album exists and matches track count <= max_album_tracks, use it
                    saavn_album_id = saavn_song.get("album_id")
                    saavn_album_songs = (
                        self.saavn.fetch_album_songs(saavn_album_id) if saavn_album_id else []
                    )
                    if saavn_album_songs and len(saavn_album_songs) <= eff_max_tracks:
                        for t_idx, s_song in enumerate(saavn_album_songs, start=1):
                            res = self._download_and_tag_single_saavn_track(
                                manager,
                                s_song.to_dict(),
                                album_folder,
                                title=s_song.title,
                                artists=s_song.artists or tr.get("artists") or [],
                                album=album_name or s_song.album,
                                album_artists=tr.get("artists") or s_song.artists or [],
                                track_number=t_idx,
                                total_tracks=len(saavn_album_songs),
                                release_date=tr.get("release_date") or s_song.year,
                                genre=genre,
                                comment=comment,
                                artwork_info=art_info,
                                show_progress=show_progress,
                            )
                            results.append(res)
                            album_tracks_done += 1
                    else:
                        # Strategy 2: Match each track of the Spotify single/EP on JioSaavn (allowing B-side instrumentals)
                        sp_album_tracks = (tr.get("album") or {}).get("tracks") or []
                        for at in sp_album_tracks:
                            is_main = at.get("track_id") == tr.get("track_id")
                            if is_main:
                                matched_dict = saavn_song
                            else:
                                m, _ = self.saavn.match_track(
                                    title=at.get("title", ""),
                                    artists=at.get("artists") or tr.get("artists") or [],
                                    duration_ms=at.get("duration_ms") or 0,
                                    allow_instrumental=True,
                                )
                                matched_dict = m.to_dict() if m else None
                            if not matched_dict:
                                continue
                            res = self._download_and_tag_single_saavn_track(
                                manager,
                                matched_dict,
                                album_folder,
                                title=at.get("title") or matched_dict["title"],
                                artists=at.get("artists") or tr.get("artists") or matched_dict["artists"],
                                album=album_name,
                                album_artists=tr.get("artists") or matched_dict["artists"],
                                track_number=at.get("track_number"),
                                total_tracks=tr.get("album_total_tracks") or len(sp_album_tracks),
                                release_date=tr.get("release_date") or matched_dict.get("year", ""),
                                genre=genre,
                                comment=comment,
                                artwork_info=art_info,
                                show_progress=show_progress,
                            )
                            results.append(res)
                            album_tracks_done += 1

                    # Fallback: ensure at least the main track downloads if expansion found nothing extra
                    if album_tracks_done == 0:
                        res = self._download_and_tag_single_saavn_track(
                            manager,
                            saavn_song,
                            album_folder,
                            title=tr.get("title") or saavn_song["title"],
                            artists=tr.get("artists") or saavn_song["artists"],
                            album=album_name,
                            album_artists=tr.get("artists") or saavn_song["artists"],
                            track_number=tr.get("track_number") or tr.get("position"),
                            total_tracks=tr.get("album_total_tracks"),
                            release_date=tr.get("release_date") or saavn_song.get("year", ""),
                            genre=genre,
                            comment=comment,
                            artwork_info=art_info,
                            show_progress=show_progress,
                        )
                        results.append(res)
                else:
                    # Standalone track directly inside the playlist folder
                    res = self._download_and_tag_single_saavn_track(
                        manager,
                        saavn_song,
                        playlist_dir,
                        title=tr.get("title") or saavn_song["title"],
                        artists=tr.get("artists") or saavn_song["artists"],
                        album=album_name or playlist_name,
                        album_artists=tr.get("artists") or saavn_song["artists"],
                        track_number=tr.get("track_number") or tr.get("position"),
                        total_tracks=tr.get("album_total_tracks"),
                        release_date=tr.get("release_date") or saavn_song.get("year", ""),
                        genre=genre,
                        comment=comment,
                        artwork_info=art_info,
                        show_progress=show_progress,
                    )
                    results.append(res)
            except Exception as exc:
                errors.append({"position": tr.get("position"), "title": tr.get("title"), "error": str(exc)})

        if progress_callback:
            progress_callback("Finished downloads & exported CD To-Do list!", 100)

        return {
            "playlist_dir": playlist_dir,
            "todo_path": todo_path,
            "plan_path": plan_path,
            "dry_run": False,
            "selected_count": len(to_download),
            "downloaded": results,
            "errors": errors,
        }

    def run_cli(
        self,
        url: str,
        *,
        include_albums: bool = False,
        max_album_tracks: int = 6,
        reference_mode: str = "auto",
        interactive: bool = True,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """CLI entrypoint with rich review table and optional interactive checklist."""
        print(f"\nAnalyzing Spotify URL: {url}")
        plan = self.analyze(
            url,
            include_albums=include_albums,
            max_album_tracks=max_album_tracks,
            reference_mode=reference_mode,
            progress_callback=lambda msg, pct: print(f"  [{pct:3d}%] {msg}"),
        )

        selected: set[int] = {
            it["position"] for it in plan["items"] if it["enabled_by_default"]
        }
        cur_include_albums = include_albums
        cur_max_tracks = max_album_tracks

        def _print_table() -> None:
            pl = plan["playlist"]
            print("\n" + "=" * 88)
            print(f"Playlist : {pl['name']} ({len(plan['items'])} tracks)")
            print(f"Ref Mode : {plan['reference_mode']} | Mode: {'Songs + Singles/Albums (max ' + str(cur_max_tracks) + ' tracks)' if cur_include_albums else 'Songs Only'}")
            print("=" * 88)
            for it in plan["items"]:
                tr = it["track"]
                ver = it["verification"]
                pos = it["position"]
                checked = "x" if pos in selected else " "
                status = ver["status"].upper()
                if status != "FLAGGED" and not it["saavn_match"]:
                    status = "NO_SAAVN"
                will_exp, _ = compute_album_expansion(tr, cur_include_albums, cur_max_tracks)
                alb_tag = f" [EXPAND {tr.get('album_total_tracks')}t]" if will_exp else f" [{tr.get('album_total_tracks') or '?'}t]"
                art = it.get("artwork") or {}
                cov = f" ({art.get('resolution', '?')} {art.get('source', '')})" if art else ""
                print(f" [{checked}] {pos:2d}. [{status:8s}] {tr['title']} — {', '.join(tr['artists'])}{alb_tag}{cov}")
                if ver.get("matched_reference"):
                    ref = ver["matched_reference"]
                    if ver["status"] == "flagged":
                        print(f"         Official: {ref['label']} '{ref['title']}' by {ref['artist']}")
                for fl in ver.get("flags") or []:
                    print(f"         ! FLAG: {fl}")
                for wn in ver.get("warnings") or []:
                    print(f"         ~ NOTE: {wn}")
                if not it["saavn_match"] and ver["status"] != "flagged":
                    print(f"         - JioSaavn: {it['saavn_note']}")

            if plan["missing_references"]:
                print("\nMISSING OFFICIAL THEMES (Added to CD To-Do List):")
                for ref in plan["missing_references"]:
                    print(f"  - [ ] {ref['label']}: {ref['title']} — {ref['artist']} (Episodes {ref.get('episodes', '?')})")
            print("=" * 88)
            print(f"Selected for JioSaavn download: {len(selected)} | CD To-Do items: {plan['summary']['flagged_tracks'] + plan['summary']['unavailable_on_saavn'] + plan['summary']['missing_official_themes']}")

        _print_table()

        if interactive and not dry_run:
            while True:
                cmd = input(
                    "\nActions: [d]ownload & export To-Do | [e]xport To-Do only | "
                    "[t 1,2] toggle tracks | [m] toggle songs/albums | [k N] set max album tracks | [q]uit > "
                ).strip()
                if not cmd or cmd.lower() == "d":
                    break
                if cmd.lower() == "q":
                    print("Cancelled.")
                    return plan
                if cmd.lower() == "e":
                    dry_run = True
                    break
                if cmd.lower() == "m":
                    cur_include_albums = not cur_include_albums
                    _print_table()
                elif cmd.lower().startswith("k "):
                    try:
                        cur_max_tracks = max(1, int(cmd.split()[1]))
                        _print_table()
                    except ValueError:
                        print("Enter a valid integer, e.g. 'k 6'")
                elif cmd.lower().startswith("t "):
                    raw_nums = cmd[2:].replace(",", " ").split()
                    for tok in raw_nums:
                        if tok.isdigit():
                            p = int(tok)
                            if p in selected:
                                selected.remove(p)
                            else:
                                selected.add(p)
                    _print_table()
                else:
                    print("Unknown command.")

        dl_result = self.download_plan(
            plan,
            selected_positions=selected,
            include_albums=cur_include_albums,
            max_album_tracks=cur_max_tracks,
            dry_run=dry_run,
            show_progress=True,
        )
        print(f"\nFolder     : {dl_result['playlist_dir']}")
        print(f"CD To-Do   : {dl_result['todo_path']}")
        print(f"Plan JSON  : {dl_result['plan_path']}")
        print(f"Downloaded : {len(dl_result['downloaded'])} file(s), Errors: {len(dl_result['errors'])}")
        return dl_result
