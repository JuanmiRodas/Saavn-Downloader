from scripts.helper import argManager, setProxy, scan_url
from scripts.content.playlist import Playlist
from scripts.content.album import Album
from scripts.content.artist import Artist
from scripts.content.song import Song
from scripts.content.spotify_playlist import SpotifyPlaylistDownloader
from scripts.saavnaccount import Account
from scripts.anime_catalog import AnimeCatalogManager, ANIME_FRANCHISES
from scripts.web_ui import start_web_server

class Download():
    def __init__(self):
        urls = []

    def read_urls(self, filepath):
        urls = []
        with open(filepath, "r") as fh:
            for line in fh:
                url = line.strip()
                if url:
                    urls.append(url)
        return urls

    def run(self):
        args = argManager()
        album_name="songs"
        proxies, headers = setProxy()
        if getattr(args, "proxy", None):
            proxies = {"http": args.proxy, "https": args.proxy}

        # Launch local Web UI & 83-Anime Collection Gallery
        if getattr(args, "web", False):
            start_web_server(
                port=args.port,
                out_folder=args.outFolder,
                proxy_url=args.proxy,
            )
            return

        if getattr(args, "list_animes", False):
            print(f"\nSupported Anime Series ({len(ANIME_FRANCHISES)}):")
            for idx, f in enumerate(ANIME_FRANCHISES, start=1):
                print(f"  {idx:2d}. {f.slug:26s} | {f.name} ({f.jp_name})")
            return

        if getattr(args, "scan_anime", None):
            slug = args.scan_anime.strip()
            catalog = AnimeCatalogManager(proxies=proxies, headers=headers)
            print(f"Scanning anime '{slug}' from wikis, singles & JioSaavn...")
            gallery = catalog.scan_anime(
                slug,
                check_saavn=True,
                enrich_itunes=True,
                progress_callback=lambda done, total: print(f"  [{done}/{total}] resolving singles & covers..."),
            )
            entries = gallery.get("entries") or []
            print(f"Built {len(entries)} gallery entries for {gallery['franchise']['name']}.")
            md = catalog.build_master_todo_markdown(slug)
            out_dir = args.outFolder or "."
            import os
            todo_file = os.path.join(out_dir, f"{slug}_CD_TODO.md")
            with open(todo_file, "w", encoding="utf-8") as fh:
                fh.write(md)
            print(f"Saved CD To-Do list to: {todo_file}")
            return

        # Manage for jio saavn users
        if args.user:
            if args.email and args.password:
                email = args.email
                password = args.password
            else:
                email = input("Enter your email for jiosaavn: ").strip()
                password = input("Enter your password for jiosaavn: ").strip()
            account = Account(proxies=proxies, headers=headers, email=email, password=password)
            if args.p:
                account.start_download_playlist()
            elif args.a:
                account.start_download_album()
            elif args.s:
                account.start_download_podcast()
            elif args.clone:
                account.get_details_n_clone(args.clone, args.create, args.copy)
            elif args.create:
                account.create_user(email, password)
        
        # Manage for all default downloads
        # Note: Passing the url parameter to the contructor of Playlist, Album and Artist is must
        else:
            if args.url is None and args.file is None:
                dl_urls = [input("Enter the URL : ").strip()]
            elif args.url is None and args.file:
                dl_urls = self.read_urls(args.file)
            else:
                dl_urls = [args.url]

            for dl_url in dl_urls:
                dl_type = scan_url(url=dl_url)
                if dl_type in ("spotify_playlist", "spotify_album", "spotify_track"):
                    sp_dl = SpotifyPlaylistDownloader(
                        proxies=proxies,
                        headers=headers,
                        out_folder=args.outFolder,
                        strict_versions=args.strict_versions,
                    )
                    sp_dl.run_cli(
                        dl_url,
                        include_albums=args.include_albums,
                        max_album_tracks=args.max_album_tracks,
                        reference_mode=args.reference,
                        interactive=not args.no_interactive,
                        dry_run=args.dry_run,
                    )
                elif dl_type == "playlist":      
                    playlist = Playlist(proxies, headers, dl_url)
                    playlist.start_download()
                elif dl_type == "album":
                    album = Album(proxies, headers, dl_url)
                    album.start_download()
                elif dl_type == "artist":
                    artist = Artist(proxies, headers, args, dl_url)
                    artist.start_download()
                elif dl_type == "song":
                    song = Song(proxies, headers, dl_url)
                    song.start_download()
                else:
                    print("Invalid URL:", dl_url)
        print("DONE\n")


if __name__ == "__main__":
    obj = Download()
    obj.run()