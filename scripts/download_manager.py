from mutagen.mp4 import MP4, MP4Cover
import urllib.request
import re
import html
import json
import base64
import os

from .pyDes import *
from .helper import argManager, download_file

REQUEST_TIMEOUT = 60

class Manager():
    def __init__(self, out_folder=None):
        self.unicode = str
        self._explicit_out_folder = out_folder
        self.args = None if out_folder is not None else argManager()
        self.des_cipher = self.setDecipher()
    
    def setDecipher(self):
        return des(b"38346591", ECB, b"\0\0\0\0\0\0\0\0", pad=None, padmode=PAD_PKCS5)
    
    def get_dec_url(self, enc_url):
        enc_url = base64.b64decode(enc_url.strip())
        dec_url = self.des_cipher.decrypt(enc_url, padmode=PAD_PKCS5).decode("utf-8")
        dec_url = dec_url.replace("_96.mp4", "_320.mp4")
        return dec_url

    def get_dec_url_variants(self, enc_url):
        """Return candidate decrypted stream URLs from highest to lowest bitrate."""
        base_url = self.get_dec_url(enc_url)
        variants = [base_url]
        if "_320.mp4" in base_url:
            variants.append(base_url.replace("_320.mp4", "_160.mp4"))
            variants.append(base_url.replace("_320.mp4", "_96.mp4"))
        return variants

    def sanitize_name(self, name):
        cleaned = html.unescape(str(name or "")).strip()
        cleaned = re.sub(r'[<>:"/\\|?*]', "-", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
        return cleaned or "Untitled"
    
    def format_filename(self, filename):
        filename = html.unescape(filename) + ".mp4"
        filename = re.sub(r"[<>:\"/\\|?*]", "-", filename)
        filename = filename.replace("\"", "'")
        return filename
    
    def get_download_location(self, *args):
        out_folder = self._explicit_out_folder
        if out_folder is None and self.args is not None:
            out_folder = self.args.outFolder
        if not out_folder:
            location = os.getcwd()
        else:
            location = out_folder
        for folder in args:
            if folder:
                location = os.path.join(location, folder)
        return location
    
    def start_download(self, filename, location, dec_url):
        if os.path.isfile(location):
            print(f"Downloaded {filename}")
            return False
        else :
            print(f"Downloading {filename}")
            download_file(dec_url, location, 60, True)
            return True

    def start_download_with_fallback(self, filename, location, enc_url, show_progress=True):
        """Download song trying 320kbps -> 160kbps -> 96kbps. Returns (downloaded, bitrate_label)."""
        if os.path.isfile(location):
            return False, "existing"
        last_err = None
        for url in self.get_dec_url_variants(enc_url):
            bitrate = "320kbps" if "_320." in url else ("160kbps" if "_160." in url else "96kbps")
            try:
                download_file(url, location, REQUEST_TIMEOUT, show_progress, retries=1)
                return True, bitrate
            except Exception as exc:
                last_err = exc
                if os.path.isfile(location):
                    try:
                        os.remove(location)
                    except OSError:
                        pass
        raise RuntimeError(f"Failed downloading {filename}: {last_err}")
    
    def downloadSongs(self, songs_json, album_name="songs", artist_name="Non-Artist"):
        for song in songs_json["songs"]:
            try:
                dec_url = self.get_dec_url(song["encrypted_media_url"])
                filename = self.format_filename(song["song"])
                location = self.get_download_location(artist_name, album_name, filename)
                has_downloaded = self.start_download(filename, location, dec_url)
                if has_downloaded:
                    try:
                        name = songs_json["name"] if ("name" in songs_json) else songs_json["listname"]
                    except:
                        name = ""
                    try:
                        song["primary_artists"] = [artist.strip() for artist in song["primary_artists"].split(",")]
                        song["singers"] = [artist.strip() for artist in song["singers"].split(",")]
                        song["music"] = [artist.strip() for artist in song["music"].split(",")]
                        song["starring"] = [artist.strip() for artist in song["starring"].split(",")]
                        self.addtags(location, song, name)
                    except Exception as e:
                        print("============== Error Adding Meta Data ==============")
                        print(f"Error : {e}")
                    print("\n")
            except Exception as e:
                print(f"Download Error : {e}")
    
    def addtags(self, filename, json_data, playlist_name):
        # https://mutagen.readthedocs.io/en/latest/api/mp4.html
        audio = MP4(filename)
        if "song" in json_data:
            audio["\xa9nam"] = html.unescape(self.unicode(json_data["song"]))
        if "primary_artists" in json_data:
            audio["\xa9ART"] = [html.unescape(self.unicode(artist)) for artist in json_data["primary_artists"]]
        if "album" in json_data:
            audio["\xa9alb"] = html.unescape(self.unicode(json_data["album"]))
        if "singers" in json_data:
            audio["aART"] = [html.unescape(self.unicode(singer)) for singer in json_data["singers"]]
        if "music" in json_data:
            audio["\xa9wrt"] = [html.unescape(self.unicode(music)) for music in json_data["music"]]
        if "starring" in json_data:
            audio["desc"] = html.unescape(self.unicode(f"Starring: {','.join(json_data['starring'])}"))
        # audio["\xa9gen"] = html.unescape(self.unicode(playlist_name))
        if "copyright_text" in json_data and json_data["copyright_text"]:
            audio["cprt"] = html.unescape(self.unicode(json_data["copyright_text"]))
        elif "label" in json_data and json_data["label"]:
            audio["cprt"] = html.unescape(self.unicode(json_data["label"]))
        if "year" in json_data:
            audio["\xa9day"] = html.unescape(self.unicode(json_data["year"]))
        cover_url = re.sub(r'-\d+x\d+\.(?:webp|jpg|png)', '-500x500.jpg', json_data["image"])
        fd = urllib.request.urlopen(cover_url)
        cover = MP4Cover(fd.read(), getattr(MP4Cover, "FORMAT_PNG" if cover_url.endswith("png") else "FORMAT_JPEG"))
        fd.close()
        audio["covr"] = [cover]
        audio.save()
