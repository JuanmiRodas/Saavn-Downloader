import io
import json
import os
import struct
import tempfile
import threading
import urllib.request
from types import SimpleNamespace

import pytest

from scripts import helper
from scripts.anime_catalog import (
    ANIME_FRANCHISES,
    AnimeCatalogManager,
    detect_anime_franchise,
    is_soundtrack_album,
)
from scripts.artwork_and_tags import detect_image_dimensions
from scripts.content.spotify_playlist import compute_album_expansion
from scripts.reference_checker import ReferenceChecker, detect_cover_or_karaoke
from scripts.saavn_matcher import SaavnMatch, SaavnMatcher
from scripts.spotify_client import parse_spotify_url
from scripts.todo_exporter import build_todo_markdown, export_todo_and_plan
from scripts.web_ui import AppState, make_handler
from http.server import ThreadingHTTPServer


class TestSpotifyUrlAndHelper:
    @pytest.mark.parametrize(
        "url, expected_parse, expected_scan",
        [
            (
                "https://open.spotify.com/playlist/4IQUdsVry6H16OT9e19Ldz",
                ("playlist", "4IQUdsVry6H16OT9e19Ldz"),
                "spotify_playlist",
            ),
            (
                "https://open.spotify.com/playlist/4v6JxXh5OonEhXn2suacOO?si=abc123",
                ("playlist", "4v6JxXh5OonEhXn2suacOO"),
                "spotify_playlist",
            ),
            (
                "https://open.spotify.com/intl-es/album/3EJNxm64X3fPDaBX4U4ui4",
                ("album", "3EJNxm64X3fPDaBX4U4ui4"),
                "spotify_album",
            ),
            (
                "https://open.spotify.com/intl-es/album/74FWMBzqWcuqRh18aGyphf",
                ("album", "74FWMBzqWcuqRh18aGyphf"),
                "spotify_album",
            ),
            (
                "spotify:track:37wZDW2UoJHpzG5xJyBpgn",
                ("track", "37wZDW2UoJHpzG5xJyBpgn"),
                "spotify_track",
            ),
        ],
    )
    def test_spotify_url_parsing(self, url, expected_parse, expected_scan):
        assert parse_spotify_url(url) == expected_parse
        assert helper.scan_url(url) == expected_scan


class TestReferenceVerification:
    def _track(self, pos, title, artists, album_name=""):
        return SimpleNamespace(
            position=pos,
            title=title,
            artists=artists,
            album_name=album_name,
        )

    def test_one_piece_openings_crosscheck_flags_covers_and_accepts_originals(self):
        checker = ReferenceChecker(use_network=False)
        tracks = [
            self._track(1, "ウィーアー!", ["Hiroshi Kitadani"], "ウィーアー!"),
            self._track(2, "Believe", ["Folder 5"], "HYPER GROOVE 1"),
            self._track(8, "Crazy Rainbow (第284-325話 OP)", ["Super DJ Yuuki"], "熱烈！アニソン魂 THE BEST カバー楽曲集"),
            self._track(9, "Jungle P", ["Hiroshi Kitadani"], "ONE PIECE SONG COVERS"),
            self._track(10, "ウィーアー! (第1-47話 OP)", ["Hisao Noguchi"], "熱烈！アニソン魂 THE BEST カバー楽曲集"),
            self._track(15, "ウィーゴー！", ["Hiroshi Kitadani"], "ウィーゴー！"),
            self._track(16, "HANDS UP !", ["新里宏太"], "HANDS UP!"),
            self._track(29, "ルミナス - Luminous", ["AiNA THE END"], "ルミナス - Luminous"),
        ]
        mode, results, missing = checker.crosscheck_playlist("One Piece Openings (1-29)", tracks)
        assert mode == "one-piece-openings"

        by_pos = {t.position: r for t, r in zip(tracks, results)}
        assert by_pos[1].status == "verified" and by_pos[1].enabled_by_default is True
        assert by_pos[2].status == "verified" and by_pos[2].enabled_by_default is True
        assert by_pos[15].status == "verified" and by_pos[15].enabled_by_default is True
        assert by_pos[16].status == "verified" and by_pos[16].enabled_by_default is True
        assert by_pos[29].status == "verified" and by_pos[29].enabled_by_default is True

        # Flagged covers must be disabled by default
        for bad_pos in (8, 9, 10):
            assert by_pos[bad_pos].status == "flagged"
            assert by_pos[bad_pos].enabled_by_default is False
            assert len(by_pos[bad_pos].flags) >= 1

        assert by_pos[8].matched_reference.number == 8
        assert by_pos[8].matched_reference.artist == "Tackey & Tsubasa"
        assert by_pos[10].matched_reference.number == 10
        assert by_pos[10].matched_reference.artist == "TVXQ"

        # Unincluded openings appear in missing list
        missing_nums = {m.number for m in missing}
        assert 3 in missing_nums
        assert 1 not in missing_nums

    def test_one_piece_endings_detects_missing_ed3_and_karaoke(self):
        checker = ReferenceChecker(use_network=False)
        tracks = [
            self._track(1, "memories", ["Maki Otsuki"]),
            self._track(2, "RUN! RUN! RUN!", ["Maki Otsuki"]),
            # ED #3 Watashi ga Iru Yo (TOMATO CUBE) intentionally missing!
            self._track(3, "しょうちのすけ", ["Suiteisho-jo"]),
            self._track(4, "BEFORE DAWN", ["AI-SACHI"]),
            self._track(5, "fish(カラオケ)[原曲歌手:The Kaleidoscope]", ["歌っちゃ王"]),
            self._track(6, "GLORY-君がいるから-", ["上原多香子"]),
            self._track(12, "DREAMSHIP (アニメ「ONE PIECE」13thエンディングテーマ)(セルフカバー)", ["ACKO"]),
            self._track(22, "Sono Mirai (One Piece)", ["Rumia Orchestra"]),
        ]
        mode, results, missing = checker.crosscheck_playlist("One Piece Endings 1-22", tracks)
        assert mode == "one-piece-endings"

        by_pos = {t.position: r for t, r in zip(tracks, results)}
        assert by_pos[1].status == "verified"
        assert by_pos[3].status == "verified"
        assert by_pos[3].matched_reference.number == 4
        assert by_pos[6].status == "verified"
        assert by_pos[6].matched_reference.number == 7

        assert by_pos[5].status == "flagged" and by_pos[5].enabled_by_default is False
        assert by_pos[12].status == "flagged" and by_pos[12].enabled_by_default is False
        assert by_pos[22].status == "flagged" and by_pos[22].enabled_by_default is False

        missing_nums = {m.number for m in missing}
        assert 3 in missing_nums

    def test_general_cover_detector(self):
        assert detect_cover_or_karaoke("raise (one piece lofi)", ["Toastaku"])
        assert detect_cover_or_karaoke("Luminous (Piano Cover)", ["Capybara Piano"])
        assert detect_cover_or_karaoke("Believe", ["Folder 5"], album_name="HYPER GROOVE 1") == []


class TestAlbumExpansionAndSoundtrackFilter:
    def test_expansion_threshold_examples(self):
        # Single ウィーゴー！ has 4 tracks -> expands with max=6
        we_go = {"album_name": "ウィーゴー！", "album_total_tracks": 4}
        expand, _ = compute_album_expansion(we_go, include_albums=True, max_album_tracks=6)
        assert expand is True

        # Album HYPER GROOVE 1 has 13 tracks -> song only with max=6
        hyper_groove = {"album_name": "HYPER GROOVE 1", "album_total_tracks": 13}
        expand2, _ = compute_album_expansion(hyper_groove, include_albums=True, max_album_tracks=6)
        assert expand2 is False

        # Songs-only mode never expands
        expand3, _ = compute_album_expansion(we_go, include_albums=False, max_album_tracks=6)
        assert expand3 is False

        # Soundtracks are always excluded from expansion
        ost = {"album_name": "One Piece Original Soundtrack Vol. 1", "album_total_tracks": 4}
        expand4, _ = compute_album_expansion(ost, include_albums=True, max_album_tracks=6)
        assert expand4 is False

    def test_soundtrack_and_dub_exclusion(self):
        assert is_soundtrack_album("One Piece Original Soundtrack") is True
        assert is_soundtrack_album("BLEACH オリジナル・サウンドトラック") is True
        assert is_soundtrack_album("We Are! (4Kids English Dub)") is True
        assert is_soundtrack_album("ウィーゴー！") is False


class TestSaavnStrictMatcher:
    def test_matcher_rejects_lofi_cover_and_accepts_original(self, monkeypatch):
        matcher = SaavnMatcher()

        fake_results = [
            SaavnMatch(
                song_id="bad1",
                title="raise (one piece lofi)",
                artists=["Toastaku"],
                album="raise (one piece lofi)",
                album_id="a1",
                year="2024",
                duration_sec=180,
                encrypted_media_url="ENC1",
                is_320kbps=True,
                image_url="https://example.com/1-500x500.jpg",
                language="instrumental",
            ),
            SaavnMatch(
                song_id="good1",
                title="Raise",
                artists=["Chilli Beans."],
                album="Raise",
                album_id="a2",
                year="2023",
                duration_sec=184,
                encrypted_media_url="ENC2",
                is_320kbps=True,
                image_url="https://example.com/2-500x500.jpg",
                language="japanese",
            ),
        ]
        monkeypatch.setattr(matcher, "search_songs", lambda q, limit=6: fake_results)
        monkeypatch.setattr(matcher, "check_artist_verified", lambda aid: True)

        match, note = matcher.match_track("Raise", ["Chilli Beans."], duration_ms=185000)
        assert match is not None
        assert match.song_id == "good1"

        # If ONLY cover is returned, matcher must return None!
        monkeypatch.setattr(matcher, "search_songs", lambda q, limit=6: [fake_results[0]])
        match2, note2 = matcher.match_track("Raise", ["Chilli Beans."], duration_ms=185000)
        assert match2 is None
        assert "cover" in note2.lower() or "unverified" in note2.lower()


class TestArtworkAndTodoAndCatalog:
    def test_png_dimension_detection(self):
        # Minimal valid PNG header with 1400x1400 IHDR
        png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 1400, 1400) + b"\x00" * 10
        w, h, mime = detect_image_dimensions(png)
        assert (w, h, mime) == (1400, 1400, "image/png")

    def test_all_83_animes_registered_and_unique(self):
        assert len(ANIME_FRANCHISES) == 83
        slugs = [f.slug for f in ANIME_FRANCHISES]
        assert len(slugs) == len(set(slugs))
        assert detect_anime_franchise("One Piece Openings (1-29)").slug == "one-piece"
        assert detect_anime_franchise("Fullmetal Alchemist: Brotherhood OP").slug == "fullmetal-alchemist"
        assert detect_anime_franchise("ef a tale of melodies").slug == "ef-a-tale-of-melodies"

    def test_todo_export_writes_markdown_and_json(self):
        plan = {
            "playlist": {"name": "One Piece Endings 1-22", "url": "https://open.spotify.com/playlist/4v6JxXh5OonEhXn2suacOO"},
            "reference_mode": "one-piece-endings",
            "summary": {"total_tracks": 1, "ready_to_download": 0},
            "items": [
                {
                    "position": 5,
                    "track": {"position": 5, "title": "fish(カラオケ)", "artists": ["歌っちゃ王"]},
                    "verification": {
                        "status": "flagged",
                        "flags": ["Karaoke track"],
                        "warnings": [],
                        "matched_reference": {"label": "One Piece ED #6", "title": "fish", "artist": "The Kaleidoscope", "episodes": "95-106"},
                    },
                    "saavn_match": None,
                }
            ],
            "missing_references": [
                {"label": "One Piece ED #3", "title": "Watashi ga Iru Yo", "artist": "TOMATO CUBE", "episodes": "64-73"}
            ],
        }
        md = build_todo_markdown(plan)
        assert "One Piece ED #6 — fish" in md
        assert "One Piece ED #3 — Watashi ga Iru Yo" in md
        with tempfile.TemporaryDirectory() as tmp:
            todo_path, plan_path = export_todo_and_plan(tmp, plan)
            assert os.path.isfile(todo_path)
            assert os.path.isfile(plan_path)

    def test_web_ui_server_endpoints(self):
        state = AppState(out_folder=tempfile.gettempdir())
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
        port = server.server_address[1]
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
                assert r.status == 200
                assert b"Saavn-Downloader Studio" in r.read()
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/animes") as r:
                data = json.loads(r.read().decode("utf-8"))
                assert len(data["animes"]) == 83
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/animes/one-piece") as r:
                gal = json.loads(r.read().decode("utf-8"))
                assert gal["franchise"]["slug"] == "one-piece"
        finally:
            server.shutdown()
            server.server_close()
