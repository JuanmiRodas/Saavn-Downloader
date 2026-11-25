# Saavn Downloader
The fullset of functionality offered are:
  - High Quality - MP4 (320 Kbps bitrate, with graceful 160/96 Kbps fallback)
  - Songs with rich metadata & highest-resolution album covers (iTunes up to 3000×3000 → Spotify 640×640 → JioSaavn 500×500)
  - **Spotify Playlists & Albums → JioSaavn** with configurable single/album expansion (`--include-albums`, `--max-album-tracks N`)
  - **Original-Artist & Cover/Karaoke Verification** (auto-crosschecks One Piece Openings/Endings from One Piece Wiki + general cover/karaoke/lofi/wrong-artist detection; flags & disables copies by default)
  - **Automatic CD To-Do List (`CD_TODO_LIST.md`)** for flagged covers, missing official themes, and tracks unavailable on JioSaavn
  - **Local Studio Web UI & 83-Anime Collection Gallery (`--web`)** for Japanese Openings, Endings, Movie Singles & Character Song Singles (soundtracks/OSTs excluded)
  - Download Individual Song, Playlist, Album, or Artist from JioSaavn
  - Download Entire PlayList, Albums, JioSaavn Originals and Podcasts from user profile
  - Clone or Create JioSaavn accounts
 
###### Python 3.10.*+ recommended if you are installing it fresh for the project

### Installation
```sh
$ pip install -r requirements.txt
# or with uv:
$ uv sync
```

### Usage

#### Launch the Studio Web UI & 83-Anime Collection Gallery
```sh
$ python main.py --web
# Open http://127.0.0.1:8765 (custom port: --port 9000, output folder: -o /path/to/music)
```

#### Download a Spotify Playlist from JioSaavn (with Verification & Single Expansion)
```sh
# Interactive review (downloads songs only into "One Piece Openings (1-29)/"):
$ python main.py -u https://open.spotify.com/playlist/4IQUdsVry6H16OT9e19Ldz

# Also expand full singles/EPs up to 6 tracks (e.g., 4-track single expanded into a subfolder, 13-track album kept as song-only):
$ python main.py -u https://open.spotify.com/playlist/4IQUdsVry6H16OT9e19Ldz --include-albums --max-album-tracks 6

# Analyze, crosscheck, and export CD_TODO_LIST.md without downloading audio:
$ python main.py -u https://open.spotify.com/playlist/4v6JxXh5OonEhXn2suacOO --include-albums --max-album-tracks 6 --dry-run -y

# List or scan any of the 83 built-in Japanese anime series into the Gallery & CD To-Do archive:
$ python main.py --list-animes
$ python main.py --scan-anime one-piece
```

#### Download Song, Playlist, Album from JioSaavn Web URL
```sh
$ python main.py
$ Paste the URL: https://www.jiosaavn.com/album/tum-hi-aana-from-marjaavaan/j9bfphC2728_
```

#### You can also specify the url in the with -u or --url argument directly
```sh
$ python main.py -u https://www.jiosaavn.com/album/tum-hi-aana-from-marjaavaan/j9bfphC2728_
```

#### If you have more than 1 url and no time to wait use the -f or --file argument to specify the absolute path to txt file with urls pasted line by line
```sh
$ python main.py -f /home/monu/Desktop/urls.txt
```

#### Choose output folder path with the -o or --outFolder argument (The output path must be absolute path and not relative)
```
$ python main.py -o /home/monu/Desktop/
```


### NOTE: If at any point there is an Error or the Download fails you can Retry the same command

#### Download All Albums of an Artist
```sh
$ python main.py -album
$ Paste the URL of Artist Profile: https://www.jiosaavn.com/artist/babbal-rai-albums/pRd5ZTGrLv8_
``` 
*\*If none of the flag such as --album, --song is specified for the artist then by default the Artist music will be downloaded in Album folder structure i.e. there will be as many album folders as the number of albums an artist has on jiosaavn and not a single songs folder that you get when --song flag is specified.  


#### Download All Songs of an Artist
```sh
$ python main.py -song
$ Paste the URL of Artist Profile: https://www.jiosaavn.com/artist/babbal-rai-albums/pRd5ZTGrLv8_
```


### Note: Before using below commands as JioSaavn user make sure you have signed into the JioSaavn Android or iOS app at least once.

#### Download All Playlist from your profile
```sh
$ python main.py -user -p
$ Enter your Email: your_saavn_email
$ Enter your Password: your_saavn_password
```

#### You can also specify the username and password in the arguments using -e or --email, -pw or --password
```sh
$ python main.py -user -e YOUR_EMAIL -pw YOUR_PASSWORD -p
```

#### Download All Albums from your profile
```sh
$ python main.py -user -a
$ Enter your Email: your_saavn_email
$ Enter your Password: your_saavn_password
```

#### Clone songs, albums and playlists to a new account(account created by script itself)
```sh
$ python main.py -user -clone -create
$ Enter original account email(FROM): from_account_email
$ Enter original account password(FROM): from_account_password
$ Enter the email for new account(TO): new_account_email
$ Enter the password for new account(TO): new_account_password
```

#### Clone songs, albums and playlists to another account(must alreay exist)
```sh
$ python main.py -user -clone -copy
$ Enter original account email(FROM): from_account_email
$ Enter original account password(FROM): from_account_password
$ Enter the email of copy account(TO): to_account_email
$ Enter the password of copy account(TO): to_account_password
```

#### Create new account
```sh
$ python main.py -user -create
$ Enter the email: email_you_want_to_use
$ Enter the password: password_you_want_to_use
```

### Changelog V 0.3
 - Bug fixes for download and metadata store
 - Removed unnessary dependencies and deprecated packages
 - Bumped dependency versions

### Changelog V 0.2.2
  - Added download support for individual songs
  - Added command argument for input file with urls (-f, --file)
  - Added command argument for changing output directory (-o, --outFolder)
  - Added command argument for specifying jiosaavn username and password (-e, --email, -pw, --password)
  - Removed asynchronous code for artist data retrival


### Known Issues
  - Clone Functionality doesn't work with Saavn-Download-Reloaded package
  - Songs not having 320kbps don't get their meta data added
  - No Meta Data in JioSaavn Originals and Podcasts

### Development

Want to contribute? Great!


### Contributors
  - Arun ( Discovering Vulnerability in Saavn App )
  - [prabaprakash](https://github.com/prabaprakash/)
  - [monuyadav016](https://github.com/monuyadav016)


## License
This program is licensed under [MIT License](https://raw.githubusercontent.com/monuyadav016/Saavn-downloader/master/LICENSE)
