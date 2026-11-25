import os
import re
import json
import argparse
from typing import NoReturn

import requests
from tqdm import tqdm

from .spotify_client import parse_spotify_url

def scan_url(url):
    spotify = parse_spotify_url(url)
    if spotify is not None:
        kind, _ = spotify
        return f"spotify_{kind}"
    url_parts = url.split("/")
    if "album" in url_parts:
        return "album"
    elif "artist" in url_parts:
        return "artist"
    elif "playlist" in url_parts or "featured" in url_parts:
        return "playlist"
    elif "song" in url_parts:
        return "song"

def setProxy():
    proxy_ip = ""
    if ("http_proxy" in os.environ):
        proxy_ip = os.environ["http_proxy"]
    proxies = {
        "http": proxy_ip,
        "https": proxy_ip,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
        "cache-control": "private, max-age=0, no-cache"
    }
    return proxies, headers

def download_file(url, dest_path=None, timeout=30, show_progress=False, retries=3):
    """
    Download a file from a URL with retry logic and optional progress bar.
    
    Args:
        url (str): URL of the file to download
        dest_path (str, optional): Destination path. If None, uses current directory
        timeout (int): Timeout in seconds for each request
        show_progress (bool): Whether to show progress bar
        retries (int): Number of retry attempts (default: 3)
    
    Returns:
        str: Path to the downloaded file
        
    Raises:
        Exception: If download fails after specified retries
    """
    # Determine filename from URL if not provided in path
    if dest_path is None:
        filename = url.split("/")[-1] or "downloaded_file"
        dest_path = os.path.join(os.getcwd(), filename)
    elif os.path.isdir(dest_path):
        filename = url.split("/")[-1] or "downloaded_file"
        dest_path = os.path.join(dest_path, filename)

    for attempt in range(retries + 1):
        try:
            # Head request to get file size for progress bar
            total_size = 0
            progress_bar: tqdm[NoReturn] | None = None
            if show_progress:
                with requests.head(url, timeout=timeout) as response:
                    total_size: int = int(response.headers.get("content-length", 0))
            
            # Stream download
            with requests.get(url, stream=True, timeout=timeout) as response:
                response.raise_for_status()
                
                # Initialize progress bar
                if show_progress and total_size > 0:
                    progress_bar = tqdm(
                        total=total_size,
                        unit="B",
                        unit_scale=True,
                        desc=os.path.basename(dest_path),
                        bar_format="{percentage:3.0f}%|{bar:40}{r_bar}",
                    )
                
                # Download file
                directory = os.path.dirname(dest_path)
                if directory and not os.path.exists(directory):
                    os.makedirs(directory, exist_ok=True)
                with open(dest_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            if show_progress and total_size > 0 and progress_bar:
                                progress_bar.update(len(chunk))
                
                if show_progress and total_size > 0 and progress_bar:
                    progress_bar.close()
            
            # Download completed successfully
            return dest_path
            
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {str(e)}")
            if attempt == retries:
                raise Exception(f"Download failed after {retries + 1} attempts: {str(e)}")
            print("Retrying...")

def argManager():
    parser = argparse.ArgumentParser()
    parser.add_argument("-artist", "--artist", action="store_true", help="Download for an Artist")
    parser.add_argument("-song", "--song", action="store_true", help="Download Songs")
    parser.add_argument("-album", "--album", action="store_true", help="Download Albums")
    parser.add_argument("-fast", "--fast", action="store_true", help="Get details asynchronously")

    parser.add_argument("-user", "--user", action="store_true", help="Signin as JioSaavn user")
    parser.add_argument("-p", "--p", action="store_true", help="Download Playlists by signing in")
    parser.add_argument("-a", "--a", action="store_true", help="Download Albums by signing in")
    parser.add_argument("-s", "--s", action="store_true", help="Download Shows by signing in")

    parser.add_argument("-clone", "--clone", action="store_true", help="Clone songs,albums,playlists to new account")
    parser.add_argument("-create", "--create", action="store_true", help="To create new account for cloning")
    parser.add_argument("-copy", "--copy", action="store_true", help="To copy to another account")

    parser.add_argument("-o", "--outFolder", help="Path to store the Downloaded songs folder")
    parser.add_argument("-u", "--url", help="URL of the song, playlist, album, artist (JioSaavn or Spotify)")
    parser.add_argument("-e", "--email", help="Email of the Jio Saavn User")
    parser.add_argument("-pw", "--password", help="Password of the Jio Saavn User")
    parser.add_argument("-f", "--file", help="file with the urls of songs, albums, playlists, artists")

    # Spotify -> JioSaavn, verification & Anime Gallery options
    parser.add_argument("--web", action="store_true", help="Launch local Studio Web UI & Anime Collection Gallery")
    parser.add_argument("--port", type=int, default=8765, help="Port for --web server (default: 8765)")
    parser.add_argument("--include-albums", action="store_true", help="Also download parent singles/albums up to --max-album-tracks")
    parser.add_argument("--max-album-tracks", type=int, default=6, help="Max tracks in an album/single to expand (default: 6)")
    parser.add_argument(
        "--reference",
        choices=["auto", "one-piece-openings", "one-piece-endings", "one-piece", "none"],
        default="auto",
        help="Reference crosscheck list for Spotify playlists (default: auto)",
    )
    parser.add_argument("--strict-versions", action="store_true", help="Flag alternate/re-recorded versions by original artists")
    parser.add_argument("-y", "--no-interactive", action="store_true", help="Skip interactive track selection prompt")
    parser.add_argument("--dry-run", action="store_true", help="Analyze, crosscheck, and export CD_TODO_LIST.md without downloading")
    parser.add_argument("--proxy", help="Optional HTTP/HTTPS proxy URL for JioSaavn requests")
    parser.add_argument("--list-animes", action="store_true", help="List all 83 supported anime series slugs")
    parser.add_argument("--scan-anime", help="Scan an anime slug from the catalog (e.g. one-piece) to build its Gallery & CD To-Do list")
    args = parser.parse_args()
    return args