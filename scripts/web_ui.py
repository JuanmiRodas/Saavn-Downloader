from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
import threading
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .activity_log import activity_log, log_event
from .anime_catalog import ANIME_FRANCHISES, AnimeCatalogManager, FRANCHISE_BY_SLUG
from .content.spotify_playlist import SpotifyPlaylistDownloader
from .helper import setProxy
from .todo_exporter import build_todo_markdown, export_todo_and_plan


def _resolve_assets_dir() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidate = os.path.join(sys._MEIPASS, "scripts", "web_assets")
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_assets")


ASSETS_DIR = _resolve_assets_dir()


class AppState:
    def __init__(self, out_folder: str | None = None, proxy_url: str | None = None):
        self.out_folder = out_folder or os.getcwd()
        self.proxy_url = proxy_url or os.environ.get("http_proxy", "")
        self.jobs: dict[str, dict[str, Any]] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.plans: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def get_proxies_headers(self, proxy_override: str | None = None) -> tuple[dict[str, str], dict[str, str]]:
        proxies, headers = setProxy()
        eff = (proxy_override if proxy_override is not None else self.proxy_url).strip()
        if eff:
            proxies = {"http": eff, "https": eff}
        return proxies, headers

    def create_job(self, kind: str) -> str:
        job_id = uuid.uuid4().hex[:12]
        with self.lock:
            self.jobs[job_id] = {
                "job_id": job_id,
                "kind": kind,
                "status": "running",
                "progress": 0,
                "message": "Starting...",
                "result": None,
                "error": None,
            }
            self.cancel_events[job_id] = threading.Event()
        return job_id

    def cancel_job(self, job_id: str) -> bool:
        with self.lock:
            ev = self.cancel_events.get(job_id)
            if ev is not None:
                ev.set()
                if job_id in self.jobs:
                    self.jobs[job_id]["message"] = "Cancelling after current anime..."
                return True
        return False

    def update_job(self, job_id: str, message: str, progress: int) -> None:
        with self.lock:
            if job_id in self.jobs:
                self.jobs[job_id]["message"] = message
                self.jobs[job_id]["progress"] = max(0, min(100, int(progress)))

    def finish_job(self, job_id: str, result: Any) -> None:
        with self.lock:
            if job_id in self.jobs:
                self.jobs[job_id]["status"] = "completed"
                self.jobs[job_id]["progress"] = 100
                self.jobs[job_id]["message"] = "Completed"
                self.jobs[job_id]["result"] = result

    def fail_job(self, job_id: str, error: str) -> None:
        log_event("ERROR", f"Job {job_id} failed: {error}", level="ERROR")
        with self.lock:
            if job_id in self.jobs:
                self.jobs[job_id]["status"] = "error"
                self.jobs[job_id]["error"] = error
                self.jobs[job_id]["message"] = f"Error: {error}"


def make_handler(state: AppState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            # Keep terminal output clean
            return

        def _send_json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, text: str, content_type: str = "text/markdown; charset=utf-8", filename: str | None = None) -> None:
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))

        def _serve_static(self, rel_path: str) -> None:
            if rel_path in ("", "/"):
                rel_path = "index.html"
            rel_path = rel_path.lstrip("/")
            full_path = os.path.normpath(os.path.join(ASSETS_DIR, rel_path))
            if not full_path.startswith(os.path.normpath(ASSETS_DIR)) or not os.path.isfile(full_path):
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            mime, _ = mimetypes.guess_type(full_path)
            with open(full_path, "rb") as fh:
                data = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", f"{mime or 'application/octet-stream'}; charset=utf-8" if (mime or "").startswith("text/") or mime == "application/javascript" else (mime or "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            if path == "/api/config":
                self._send_json(
                    {
                        "out_folder": state.out_folder,
                        "proxy_url": state.proxy_url,
                        "log_path": activity_log.log_path,
                    }
                )
                return

            if path == "/api/logs":
                try:
                    since = int((qs.get("since") or ["0"])[0])
                except ValueError:
                    since = 0
                seq, entries = activity_log.get_since(since_seq=since)
                self._send_json({"seq": seq, "log_path": activity_log.log_path, "entries": entries})
                return

            if path.startswith("/api/jobs/"):
                job_id = path.split("/api/jobs/", 1)[1].strip("/")
                with state.lock:
                    job = state.jobs.get(job_id)
                if not job:
                    self._send_json({"error": "Job not found"}, 404)
                else:
                    self._send_json(job)
                return

            if path == "/api/animes":
                proxies, headers = state.get_proxies_headers()
                catalog = AnimeCatalogManager(proxies=proxies, headers=headers)
                self._send_json({"animes": catalog.list_franchises_summary()})
                return

            if path.startswith("/api/animes/") and path.endswith("/todo.md"):
                slug = path[len("/api/animes/") : -len("/todo.md")].strip("/")
                proxies, headers = state.get_proxies_headers()
                catalog = AnimeCatalogManager(proxies=proxies, headers=headers)
                if slug == "all":
                    md = catalog.build_master_todo_markdown(None)
                    self._send_text(md, filename="ANIME_MASTER_CD_TODO.md")
                elif slug in FRANCHISE_BY_SLUG:
                    md = catalog.build_master_todo_markdown(slug)
                    self._send_text(md, filename=f"{slug}_CD_TODO.md")
                else:
                    self._send_json({"error": "Anime not found"}, 404)
                return

            if path.startswith("/api/animes/"):
                slug = path.split("/api/animes/", 1)[1].strip("/")
                proxies, headers = state.get_proxies_headers()
                catalog = AnimeCatalogManager(proxies=proxies, headers=headers)
                try:
                    self._send_json(catalog.get_anime_gallery(slug))
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, 404)
                return

            if path.startswith("/api/plans/") and path.endswith("/todo.md"):
                plan_id = path[len("/api/plans/") : -len("/todo.md")].strip("/")
                with state.lock:
                    plan = state.plans.get(plan_id)
                if not plan:
                    self._send_json({"error": "Plan not found"}, 404)
                    return
                md = build_todo_markdown(plan)
                if qs.get("download") == ["1"]:
                    self._send_text(md, filename="CD_TODO_LIST.md")
                else:
                    self._send_json({"markdown": md})
                return

            self._serve_static(path)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                body = self._read_json_body()
            except Exception as exc:
                self._send_json({"error": f"Invalid JSON: {exc}"}, 400)
                return

            if path == "/api/analyze":
                url = (body.get("url") or "").strip()
                if not url:
                    self._send_json({"error": "Spotify URL is required"}, 400)
                    return
                include_albums = bool(body.get("include_albums", False))
                max_album_tracks = max(1, int(body.get("max_album_tracks", 6) or 6))
                reference_mode = (body.get("reference_mode") or "auto").strip()
                strict_versions = bool(body.get("strict_versions", False))
                proxy_override = body.get("proxy_url")
                out_folder = (body.get("out_folder") or state.out_folder).strip() or state.out_folder
                state.out_folder = out_folder
                if proxy_override is not None:
                    state.proxy_url = proxy_override.strip()

                job_id = state.create_job("analyze")

                def _worker() -> None:
                    try:
                        proxies, headers = state.get_proxies_headers(proxy_override)
                        downloader = SpotifyPlaylistDownloader(
                            proxies=proxies,
                            headers=headers,
                            out_folder=out_folder,
                            strict_versions=strict_versions,
                        )
                        plan = downloader.analyze(
                            url,
                            include_albums=include_albums,
                            max_album_tracks=max_album_tracks,
                            reference_mode=reference_mode,
                            progress_callback=lambda msg, pct: state.update_job(job_id, msg, pct),
                        )
                        plan_id = uuid.uuid4().hex[:12]
                        plan["plan_id"] = plan_id
                        with state.lock:
                            state.plans[plan_id] = plan
                        state.finish_job(job_id, plan)
                    except Exception as exc:
                        state.fail_job(job_id, str(exc))

                threading.Thread(target=_worker, daemon=True).start()
                self._send_json({"job_id": job_id})
                return

            if path == "/api/download":
                plan_id = (body.get("plan_id") or "").strip()
                with state.lock:
                    plan = state.plans.get(plan_id)
                if not plan:
                    self._send_json({"error": "Analyzed plan not found. Re-analyze the playlist first."}, 404)
                    return
                selected_positions = set(int(p) for p in (body.get("selected_positions") or []))
                include_albums = bool(body.get("include_albums", False))
                max_album_tracks = max(1, int(body.get("max_album_tracks", 6) or 6))
                out_folder = (body.get("out_folder") or state.out_folder).strip() or state.out_folder
                dry_run = bool(body.get("dry_run", False))
                proxy_override = body.get("proxy_url")

                job_id = state.create_job("download")

                def _worker() -> None:
                    try:
                        proxies, headers = state.get_proxies_headers(proxy_override)
                        downloader = SpotifyPlaylistDownloader(
                            proxies=proxies,
                            headers=headers,
                            out_folder=out_folder,
                        )
                        res = downloader.download_plan(
                            plan,
                            selected_positions=selected_positions,
                            include_albums=include_albums,
                            max_album_tracks=max_album_tracks,
                            out_folder=out_folder,
                            dry_run=dry_run,
                            show_progress=False,
                            progress_callback=lambda msg, pct: state.update_job(job_id, msg, pct),
                        )
                        state.finish_job(job_id, res)
                    except Exception as exc:
                        state.fail_job(job_id, str(exc))

                threading.Thread(target=_worker, daemon=True).start()
                self._send_json({"job_id": job_id})
                return

            if path == "/api/export-todo":
                plan_id = (body.get("plan_id") or "").strip()
                out_folder = (body.get("out_folder") or state.out_folder).strip() or state.out_folder
                with state.lock:
                    plan = state.plans.get(plan_id)
                if not plan:
                    self._send_json({"error": "Plan not found"}, 404)
                    return
                from .download_manager import Manager

                mgr = Manager(out_folder=out_folder)
                pl_name = mgr.sanitize_name((plan.get("playlist") or {}).get("name") or "Spotify Playlist")
                dest = mgr.get_download_location(pl_name)
                todo_path, plan_path = export_todo_and_plan(dest, plan)
                self._send_json({"todo_path": todo_path, "plan_path": plan_path, "markdown": build_todo_markdown(plan)})
                return

            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                job_id = path[len("/api/jobs/") : -len("/cancel")].strip("/")
                ok = state.cancel_job(job_id)
                self._send_json({"cancelled": ok}, 200 if ok else 404)
                return

            if path == "/api/logs/clear":
                activity_log.clear()
                self._send_json({"cleared": True})
                return

            if path == "/api/open-log":
                log_path = activity_log.log_path
                try:
                    os.makedirs(os.path.dirname(log_path), exist_ok=True)
                    if not os.path.exists(log_path):
                        with open(log_path, "a", encoding="utf-8"):
                            pass
                    if sys.platform == "darwin":
                        subprocess.Popen(["open", "-R", log_path])
                    elif sys.platform.startswith("linux"):
                        subprocess.Popen(["xdg-open", os.path.dirname(log_path)])
                    elif sys.platform == "win32":
                        os.startfile(os.path.dirname(log_path))  # type: ignore[attr-defined]
                    self._send_json({"opened": log_path})
                except Exception as exc:
                    self._send_json({"error": str(exc)}, 500)
                return

            if path == "/api/open-folder":
                folder = (body.get("path") or state.out_folder).strip() or state.out_folder
                os.makedirs(folder, exist_ok=True)
                try:
                    if sys.platform == "darwin":
                        subprocess.Popen(["open", folder])
                    elif sys.platform.startswith("linux"):
                        subprocess.Popen(["xdg-open", folder])
                    elif sys.platform == "win32":
                        os.startfile(folder)  # type: ignore[attr-defined]
                    self._send_json({"opened": folder})
                except Exception as exc:
                    self._send_json({"error": str(exc)}, 500)
                return

            if path == "/api/animes/run-all":
                out_folder = (body.get("out_folder") or state.out_folder).strip() or state.out_folder
                state.out_folder = out_folder
                include_singles = bool(body.get("include_singles", True))
                max_album_tracks = max(1, int(body.get("max_album_tracks", 6) or 6))
                skip_scanned = bool(body.get("skip_already_scanned", False))
                strict_versions = bool(body.get("strict_versions", True))
                proxy_override = body.get("proxy_url")
                if proxy_override is not None:
                    state.proxy_url = proxy_override.strip()

                slugs = body.get("slugs") or [f.slug for f in ANIME_FRANCHISES]
                job_id = state.create_job("autopilot_all")
                cancel_ev = state.cancel_events[job_id]

                def _worker() -> None:
                    try:
                        proxies, headers = state.get_proxies_headers(proxy_override)
                        catalog = AnimeCatalogManager(proxies=proxies, headers=headers)
                        res = catalog.run_autopilot(
                            slugs=slugs,
                            out_folder=out_folder,
                            include_singles=include_singles,
                            max_album_tracks=max_album_tracks,
                            skip_already_scanned=skip_scanned,
                            strict_versions=strict_versions,
                            cancel_event=cancel_ev,
                            progress_callback=lambda msg, pct: state.update_job(job_id, msg, pct),
                        )
                        state.finish_job(job_id, res)
                    except Exception as exc:
                        state.fail_job(job_id, str(exc))

                threading.Thread(target=_worker, daemon=True).start()
                self._send_json({"job_id": job_id})
                return

            if path.startswith("/api/animes/") and path.endswith("/run"):
                slug = path[len("/api/animes/") : -len("/run")].strip("/")
                if slug not in FRANCHISE_BY_SLUG:
                    self._send_json({"error": "Unknown anime"}, 404)
                    return
                out_folder = (body.get("out_folder") or state.out_folder).strip() or state.out_folder
                state.out_folder = out_folder
                include_singles = bool(body.get("include_singles", True))
                max_album_tracks = max(1, int(body.get("max_album_tracks", 6) or 6))
                strict_versions = bool(body.get("strict_versions", True))
                proxy_override = body.get("proxy_url")
                if proxy_override is not None:
                    state.proxy_url = proxy_override.strip()

                job_id = state.create_job("autopilot_one")
                cancel_ev = state.cancel_events[job_id]

                def _worker() -> None:
                    try:
                        proxies, headers = state.get_proxies_headers(proxy_override)
                        catalog = AnimeCatalogManager(proxies=proxies, headers=headers)
                        res = catalog.run_autopilot(
                            slugs=[slug],
                            out_folder=out_folder,
                            include_singles=include_singles,
                            max_album_tracks=max_album_tracks,
                            skip_already_scanned=False,
                            strict_versions=strict_versions,
                            cancel_event=cancel_ev,
                            progress_callback=lambda msg, pct: state.update_job(job_id, msg, pct),
                        )
                        res["gallery"] = catalog.get_anime_gallery(slug)
                        state.finish_job(job_id, res)
                    except Exception as exc:
                        state.fail_job(job_id, str(exc))

                threading.Thread(target=_worker, daemon=True).start()
                self._send_json({"job_id": job_id})
                return

            if path.startswith("/api/animes/") and path.endswith("/download"):
                slug = path[len("/api/animes/") : -len("/download")].strip("/")
                if slug not in FRANCHISE_BY_SLUG:
                    self._send_json({"error": "Unknown anime"}, 404)
                    return
                out_folder = (body.get("out_folder") or state.out_folder).strip() or state.out_folder
                state.out_folder = out_folder
                include_singles = bool(body.get("include_singles", True))
                max_album_tracks = max(1, int(body.get("max_album_tracks", 6) or 6))
                entry_ids_raw = body.get("entry_ids")
                entry_ids = set(entry_ids_raw) if entry_ids_raw else None
                proxy_override = body.get("proxy_url")
                if proxy_override is not None:
                    state.proxy_url = proxy_override.strip()

                job_id = state.create_job("download_anime")

                def _worker() -> None:
                    try:
                        proxies, headers = state.get_proxies_headers(proxy_override)
                        catalog = AnimeCatalogManager(proxies=proxies, headers=headers)
                        res = catalog.download_anime_entries(
                            slug,
                            out_folder=out_folder,
                            include_singles=include_singles,
                            max_album_tracks=max_album_tracks,
                            entry_ids=entry_ids,
                            progress_callback=lambda msg, done, tot: state.update_job(
                                job_id, msg, int(100 * done / max(1, tot))
                            ),
                        )
                        res["gallery"] = catalog.get_anime_gallery(slug)
                        state.finish_job(job_id, res)
                    except Exception as exc:
                        state.fail_job(job_id, str(exc))

                threading.Thread(target=_worker, daemon=True).start()
                self._send_json({"job_id": job_id})
                return

            if path.startswith("/api/animes/") and path.endswith("/scan"):
                slug = path[len("/api/animes/") : -len("/scan")].strip("/")
                if slug not in FRANCHISE_BY_SLUG:
                    self._send_json({"error": "Unknown anime"}, 404)
                    return
                check_saavn = bool(body.get("check_saavn", True))
                enrich_itunes = bool(body.get("enrich_itunes", True))
                strict_versions = bool(body.get("strict_versions", True))
                proxy_override = body.get("proxy_url")
                job_id = state.create_job("scan_anime")

                def _worker() -> None:
                    try:
                        proxies, headers = state.get_proxies_headers(proxy_override)
                        catalog = AnimeCatalogManager(proxies=proxies, headers=headers)
                        state.update_job(job_id, f"Fetching playlists & wikis for {FRANCHISE_BY_SLUG[slug].name}...", 10)
                        gallery = catalog.scan_anime(
                            slug,
                            check_saavn=check_saavn,
                            enrich_itunes=enrich_itunes,
                            strict_versions=strict_versions,
                            progress_callback=lambda done, total: state.update_job(
                                job_id,
                                f"Resolving singles, 3000px covers & JioSaavn ({done}/{total})...",
                                15 + int(80 * done / max(1, total)),
                            ),
                        )
                        state.finish_job(job_id, gallery)
                    except Exception as exc:
                        state.fail_job(job_id, str(exc))

                threading.Thread(target=_worker, daemon=True).start()
                self._send_json({"job_id": job_id})
                return

            if path.startswith("/api/animes/") and path.endswith("/toggle-cd"):
                slug = path[len("/api/animes/") : -len("/toggle-cd")].strip("/")
                entry_id = (body.get("entry_id") or "").strip()
                owned = bool(body.get("owned", False))
                proxies, headers = state.get_proxies_headers()
                catalog = AnimeCatalogManager(proxies=proxies, headers=headers)
                updated = catalog.toggle_cd_owned(slug, entry_id, owned)
                if not updated:
                    self._send_json({"error": "Entry not found"}, 404)
                else:
                    self._send_json({"entry": updated})
                return

            self._send_json({"error": "Endpoint not found"}, 404)

    return Handler


def start_web_server_background(
    host: str = "127.0.0.1",
    port: int = 0,
    out_folder: str | None = None,
    proxy_url: str | None = None,
) -> tuple[ThreadingHTTPServer, str]:
    """Start the HTTP server on a daemon thread and return (server, base_url)."""
    state = AppState(out_folder=out_folder, proxy_url=proxy_url)
    server = ThreadingHTTPServer((host, port), make_handler(state))
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, url


def start_web_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    out_folder: str | None = None,
    proxy_url: str | None = None,
    open_browser: bool = False,
) -> None:
    state = AppState(out_folder=out_folder, proxy_url=proxy_url)
    server = ThreadingHTTPServer((host, port), make_handler(state))
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}"
    print(f"\nSaavn-Downloader Studio Web UI running at {url}")
    print("Press Ctrl+C to stop.\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down web server...")
        server.server_close()
