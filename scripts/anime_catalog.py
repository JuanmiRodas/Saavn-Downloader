from __future__ import annotations

import html
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from .artwork_and_tags import ArtworkInfo, HighResArtworkFetcher
from .reference_checker import (
    OnePieceReferenceProvider,
    ReferenceSong,
    WIKI_USER_AGENT,
    _artist_matches,
    _similarity,
    detect_cover_or_karaoke,
    normalize_text,
    strip_title_annotations,
)
from .saavn_matcher import SaavnMatcher

SOUNDTRACK_EXCLUDE_RE = re.compile(
    r"\b(?:original\s+soundtrack|soundtrack|o\.?s\.?t\.?|bgm|original\s+score|"
    r"background\s+music|drama\s+cd|radio\s+cd|audio\s+drama|symphonic\s+suite|"
    r"piano\s+collections?|orchestra\s+concert|music\s+box|lofi|lo-fi|karaoke|8-?bit|16-?bit|"
    r"english\s+dub|english\s+version|4kids|funimation\s+dub|spanish\s+version|latino\s+version)\b|"
    r"オリジナル[\s・]?サウンドトラック|サウンドトラック|サントラ|ドラマCD|劇伴|オルゴール|カラオケ|カバー",
    re.I,
)


@dataclass
class AnimeFranchise:
    slug: str
    name: str
    jp_name: str
    aliases: list[str] = field(default_factory=list)
    en_wiki_pages: list[str] = field(default_factory=list)
    ja_wiki_pages: list[str] = field(default_factory=list)
    fandom_music_api: str | None = None
    default_playlists: list[dict[str, str]] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GalleryEntry:
    entry_id: str
    anime_slug: str
    category: str  # "OP" | "ED" | "Movie" | "Special" | "Character" | "Insert"
    number: int | None
    label: str
    title: str
    artist: str
    episodes_or_context: str = ""
    single_or_album: str = ""
    single_track_count: int | None = None
    single_tracks: list[dict[str, Any]] = field(default_factory=list)
    release_date: str = ""
    label_company: str = ""
    cover_url: str | None = None
    cover_resolution: str = ""
    cover_source: str = ""
    spotify_track_url: str | None = None
    spotify_album_url: str | None = None
    youtube_music_query_url: str = ""
    saavn_match: dict[str, Any] | None = None
    saavn_note: str = ""
    status: str = "todo_cd"  # "ready_saavn" | "todo_cd" | "flagged_cover" | "owned_cd"
    flags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    owned_from_cd: bool = False
    source: str = "wiki"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# All 83 anime series requested by the user (Japanese originals: TV OPs/EDs, sequels, specials, movies & character singles; soundtracks excluded)
ANIME_FRANCHISES: list[AnimeFranchise] = [
    AnimeFranchise("ah-my-goddess", "Ah! My Goddess (Aa! Megami-sama)", "ああっ女神さまっ", ["Oh My Goddess!", "Aa! Megami-sama"], ["Ah! My Goddess"], ["ああっ女神さまっ (アニメ)"], notes="OVA, Movie, TV S1/S2, Fighting Wings & character singles"),
    AnimeFranchise("air-gear", "Air Gear", "エア・ギア", ["Air Gear"], ["Air Gear"], ["エア・ギア"], notes="TV OP/ED & OVA themes"),
    AnimeFranchise("beck", "Beck", "BECK", ["Beck: Mongolian Chop Squad"], ["Beck (manga)"], ["BECK (漫画)"], notes="OP/ED & Mongolian Chop Squad / Dying Breed vocal singles"),
    AnimeFranchise("beyblade", "Beyblade", "爆転シュート ベイブレード", ["Bakuten Shoot Beyblade", "Beyblade G-Revolution", "Beyblade V-Force", "Metal Fight Beyblade"], ["Beyblade"], ["爆転シュート ベイブレード (アニメ)", "メタルファイト ベイブレード"], notes="Bakuten Shoot, V-Force, G-Revolution, Metal Fight & movie singles"),
    AnimeFranchise("black-cat", "Black Cat", "BLACK CAT", ["Black Cat"], ["Black Cat (manga)"], ["BLACK CAT (漫画)"], notes="TV OP/ED & character singles"),
    AnimeFranchise("bleach", "Bleach", "BLEACH", ["Bleach: Thousand-Year Blood War", "BLEACH TYBW"], ["Bleach (TV series)", "Bleach: Thousand-Year Blood War"], ["BLEACH (アニメ)"], fandom_music_api="https://bleach.fandom.com/api.php?action=parse&page=Music&prop=wikitext&format=json", notes="TV OPs/EDs, TYBW, 4 Movie themes & Bleach Beat Collection character singles"),
    AnimeFranchise("bobobo", "Bobobo-bo Bo-bobo", "ボボボーボ・ボーボボ", ["Bobobo"], ["Bobobo-bo Bo-bobo"], ["ボボボーボ・ボーボボ (アニメ)"], notes="TV OPs/EDs & character singles"),
    AnimeFranchise("cardcaptor-sakura", "Cardcaptor Sakura", "カードキャプターさくら", ["Cardcaptor Sakura: Clear Card", "CCS"], ["Cardcaptor Sakura", "Cardcaptor Sakura: Clear Card"], ["カードキャプターさくら", "カードキャプターさくら クリアカード編"], notes="Original TV, Clear Card, 2 Movies & character song singles"),
    AnimeFranchise("chobits", "Chobits", "ちょびっツ", ["Chobits"], ["Chobits"], ["ちょびっツ"], notes="TV OP/ED & character song singles"),
    AnimeFranchise("chrono-crusade", "Chrono Crusade", "クロノクルセイド", ["Chrno Crusade"], ["Chrono Crusade"], ["クロノクルセイド"], notes="TV OP/ED & character singles"),
    AnimeFranchise("code-geass", "Code Geass", "コードギアス 反逆のルルーシュ", ["Code Geass: Lelouch of the Rebellion", "Code Geass R2", "Akito the Exiled", "Lelouch of the Re;surrection"], ["Code Geass"], ["コードギアス 反逆のルルーシュ"], notes="R1, R2, Akito, Movie trilogy, Re;surrection, Rozé & character singles"),
    AnimeFranchise("crayon-shin-chan", "Crayon Shin-chan", "クレヨンしんちゃん", ["Shin-chan", "Crayon Shinchan"], ["Crayon Shin-chan"], ["クレヨンしんちゃん (アニメ)"], notes="TV OPs/EDs, movie song singles & character singles"),
    AnimeFranchise("d-gray-man", "D.Gray-man", "D.Gray-man", ["D.Gray-man Hallow"], ["D.Gray-man", "D.Gray-man Hallow"], ["D.Gray-man"], notes="Original TV, Hallow OPs/EDs & character songs"),
    AnimeFranchise("death-note", "Death Note", "DEATH NOTE", ["DEATH NOTE"], ["Death Note"], ["DEATH NOTE (アニメ)"], notes="TV OPs/EDs & Relight specials"),
    AnimeFranchise("detective-conan", "Detective Conan", "名探偵コナン", ["Case Closed", "Meitantei Conan"], ["Case Closed", "List of Case Closed episodes"], ["名探偵コナン (アニメ)"], fandom_music_api="https://www.detectiveconanworld.com/wiki/api.php?action=parse&page=Music&prop=wikitext&format=json", notes="TV OPs/EDs, 27+ Movie song singles, specials & character singles"),
    AnimeFranchise("di-gi-charat", "Di Gi Charat", "デ・ジ・キャラット", ["Di Gi Charat Nyo!", "Panyo Panyo Di Gi Charat"], ["Di Gi Charat"], ["デ・ジ・キャラット"], notes="TV, Specials, Nyo!, Movie & character song singles"),
    AnimeFranchise("digimon", "Digimon", "デジモンアドベンチャー", ["Digimon Adventure", "Digimon Adventure 02", "Digimon Tamers", "Digimon Frontier", "Digimon Savers", "Digimon tri."], ["Digimon Adventure", "Digimon Tamers", "Digimon Frontier"], ["デジモンアドベンチャー", "デジモンアドベンチャー02", "デジモンテイマーズ", "デジモンフロンティア"], notes="Adventure, 02, Tamers, Frontier, Savers, tri., Movies & character singles"),
    AnimeFranchise("dnangel", "DNangel", "D・N・ANGEL", ["D.N.Angel", "D·N·Angel"], ["D.N.Angel"], ["D・N・ANGEL"], notes="TV OP/ED & vocal/character singles"),
    AnimeFranchise("dragon-ball", "Dragon Ball", "ドラゴンボール", ["Dragon Ball Z", "Dragon Ball GT", "Dragon Ball Kai", "Dragon Ball Super", "Dragon Ball Daima"], ["Dragon Ball (TV series)", "Dragon Ball Z", "Dragon Ball GT", "Dragon Ball Super", "Dragon Ball Daima"], ["ドラゴンボール (アニメ)", "ドラゴンボールZ", "ドラゴンボールGT", "ドラゴンボール超", "ドラゴンボールDAIMA"], notes="DB, DBZ, DBGT, Kai, Super, Daima OPs/EDs, Movie singles & character singles"),
    AnimeFranchise("durarara", "Durarara!!", "デュラララ!!", ["Durarara!!×2"], ["Durarara!!"], ["デュラララ!!"], notes="S1, ×2 Shou/Ten/Ketsu OPs/EDs & character cover/vocal singles"),
    AnimeFranchise("ergo-proxy", "Ergo Proxy", "Ergo Proxy", ["Ergo Proxy"], ["Ergo Proxy"], ["Ergo Proxy"], notes="MONORAL OP & Radiohead ED singles"),
    AnimeFranchise("evangelion", "Evangelion", "新世紀エヴァンゲリオン", ["Neon Genesis Evangelion", "Rebuild of Evangelion", "End of Evangelion"], ["Music of Neon Genesis Evangelion"], ["新世紀エヴァンゲリオン"], notes="TV, Death & Rebirth, End of Evangelion, Rebuild movie singles & character singles"),
    AnimeFranchise("fullmetal-alchemist", "Full Metal Alchemist", "鋼の錬金術師", ["Fullmetal Alchemist", "Fullmetal Alchemist: Brotherhood", "FMA", "FMAB"], ["Fullmetal Alchemist (TV series)", "Fullmetal Alchemist: Brotherhood"], ["鋼の錬金術師 (アニメ)", "鋼の錬金術師 FULLMETAL ALCHEMIST"], notes="FMA 2003, Brotherhood, Conqueror of Shamballa, Milos movie singles & character singles"),
    AnimeFranchise("fushigi-yuugi", "Fushigi Yuugi", "ふしぎ遊戯", ["Fushigi Yūgi", "Fushigi Yugi"], ["Fushigi Yûgi"], ["ふしぎ遊戯"], notes="TV, OVAs & character vocal singles"),
    AnimeFranchise("genshiken", "Genshiken", "げんしけん", ["Genshiken 2", "Genshiken Nidaime", "Kujibiki Unbalance"], ["Genshiken"], ["げんしけん"], notes="S1, OVA, S2, Nidaime & Kujibiki Unbalance OPs/EDs/character singles"),
    AnimeFranchise("ghost-in-the-shell", "Ghost in the Shell", "攻殻機動隊", ["Ghost in the Shell: Stand Alone Complex", "SAC 2nd GIG", "Arise", "SAC_2045"], ["Ghost in the Shell: Stand Alone Complex", "Ghost in the Shell: Arise", "Ghost in the Shell: SAC_2045"], ["攻殻機動隊 STAND ALONE COMPLEX", "攻殻機動隊 ARISE", "攻殻機動隊 SAC_2045"], notes="SAC, 2nd GIG, Solid State Society, Arise & SAC_2045 OP/ED vocal singles"),
    AnimeFranchise("gto", "GTO", "グレート・ティーチャー・オニヅカ", ["Great Teacher Onizuka"], ["Great Teacher Onizuka"], ["GTO (漫画)"], notes="TV OPs & EDs singles"),
    AnimeFranchise("hamtaro", "Hamtaro", "とっとこハム太郎", ["Tottoko Hamtaro"], ["Hamtaro"], ["とっとこハム太郎"], notes="TV OPs/EDs, Movie singles & Ham-Hams character singles"),
    AnimeFranchise("hellsing", "Hellsing", "HELLSING", ["Hellsing Ultimate"], ["Hellsing"], ["HELLSING"], notes="TV OP/ED & Ultimate OVA singles"),
    AnimeFranchise("hunter-x-hunter", "Hunter × Hunter", "HUNTER×HUNTER", ["Hunter x Hunter", "HxH", "Hunter x Hunter 1999", "Hunter x Hunter 2011"], ["Hunter × Hunter (1999 TV series)", "Hunter × Hunter (2011 TV series)"], ["HUNTER×HUNTER (1999年のアニメ)", "HUNTER×HUNTER (2011年のアニメ)"], notes="1999 TV, Greed Island OVAs, 2011 TV, Phantom Rouge / Last Mission movie singles & character singles"),
    AnimeFranchise("inuyasha", "Inuyasha", "犬夜叉", ["InuYasha", "Inuyasha: The Final Act", "Yashahime"], ["Inuyasha", "Inuyasha: The Final Act", "Yashahime"], ["犬夜叉 (アニメ)", "半妖の夜叉姫"], notes="Original TV, Final Act, 4 Movie singles, Yashahime & character singles"),
    AnimeFranchise("k-on", "K-On!", "けいおん!", ["K-ON!", "K-ON!!", "Ho-kago Tea Time"], ["K-On!", "List of K-On! albums"], ["けいおん! (アニメ)"], notes="S1, S2, Movie singles, Ho-kago Tea Time & Character Image Song singles"),
    AnimeFranchise("kamikaze-kaito-jeanne", "Kamikaze Kaitō Jeanne", "神風怪盗ジャンヌ", ["Phantom Thief Jeanne", "Kamikaze Kaito Jeanne"], ["Phantom Thief Jeanne"], ["神風怪盗ジャンヌ"], notes="TV OPs/EDs & character vocal singles"),
    AnimeFranchise("kare-kano", "Kare Kano (Kareshi Kanojo no Jijou)", "彼氏彼女の事情", ["Kare Kano", "His and Her Circumstances", "Kareshi Kanojo no Jijou"], ["Kare Kano"], ["彼氏彼女の事情"], notes="TV OP/ED & vocal singles"),
    AnimeFranchise("katekyo-hitman-reborn", "Katekyo Hitman Reborn!", "家庭教師ヒットマンREBORN!", ["Reborn!", "Hitman Reborn"], ["Reborn!"], ["家庭教師ヒットマンREBORN! (アニメ)"], notes="TV OPs/EDs & Vongola character song singles"),
    AnimeFranchise("legendz", "Legendz", "レジェンズ 甦る竜王伝説", ["Legendz: Tale of the Dragon Kings"], ["Legendz"], ["レジェンズ"], notes="TV OP/ED & character singles"),
    AnimeFranchise("love-hina", "Love Hina", "ラブひな", ["Love Hina Again", "Love Hina Christmas Special", "Love Hina Spring Special"], ["Love Hina"], ["ラブひな"], notes="TV, Specials, Love Hina Again & Hinata Girls character singles"),
    AnimeFranchise("lucky-star", "Lucky Star", "らき☆すた", ["Raki Suta"], ["Lucky Star (manga)"], ["らき☆すた (アニメ)"], notes="Motteke! Sailor Fuku, OVA & Character Song singles Vol. 1-12"),
    AnimeFranchise("marmalade-boy", "Marmalade Boy", "ママレード・ボーイ", ["Marmalade Boy"], ["Marmalade Boy"], ["ママレード・ボーイ"], notes="TV OP/ED, Movie & character vocal singles"),
    AnimeFranchise("matantei-loki-ragnarok", "Matantei Loki Ragnarok", "魔探偵ロキRAGNAROK", ["Mythical Detective Loki Ragnarok"], ["Mythical Detective Loki Ragnarok"], ["魔探偵ロキ"], notes="TV OP/ED & character singles"),
    AnimeFranchise("nana", "NANA", "NANA", ["Nana"], ["Nana (manga)"], ["NANA"], notes="Anna Tsuchiya (BLACK STONES) & OLIVIA (TRAPNEST) OPs/EDs & singles"),
    AnimeFranchise("naruto", "Naruto", "NARUTO -ナルト-", ["Naruto Shippuden", "Boruto"], ["Naruto", "Naruto: Shippuden"], ["NARUTO -ナルト- (アニメ)", "NARUTO -ナルト- 疾風伝"], notes="Naruto, Shippuden, Boruto OPs/EDs, Movie song singles & character singles"),
    AnimeFranchise("nodame-cantabile", "Nodame Cantabile", "のだめカンタービレ", ["Nodame Cantabile: Paris-hen", "Nodame Cantabile: Finale"], ["Nodame Cantabile"], ["のだめカンタービレ (アニメ)"], notes="TV S1, Paris-hen & Finale OPs/EDs singles"),
    AnimeFranchise("ojamajo-doremi", "Ojamajo Doremi", "おジャ魔女どれみ", ["Magical DoReMi", "Ojamajo Doremi Sharp", "Motto! Ojamajo Doremi", "Dokkan!", "Na-i-sho"], ["Ojamajo Doremi"], ["おジャ魔女どれみ"], notes="All seasons, Na-i-sho, Movies & MAHO-dou character singles"),
    AnimeFranchise("one-piece", "One Piece", "ワンピース", ["ONE PIECE"], ["Music of One Piece"], ["ONE PIECE (アニメ)"], fandom_music_api="https://onepiece.fandom.com/api.php?action=parse&page=One_Piece_Music&prop=wikitext&format=json", default_playlists=[{"label": "One Piece Openings (1-29)", "url": "https://open.spotify.com/playlist/4IQUdsVry6H16OT9e19Ldz"}, {"label": "One Piece Endings (1-22)", "url": "https://open.spotify.com/playlist/4v6JxXh5OonEhXn2suacOO"}], notes="TV OPs 1-29, EDs 1-23, Movie singles (incl. Film Red/Uta), specials & Straw Hat character singles"),
    AnimeFranchise("orphen", "Orphen", "魔術士オーフェン", ["Sorcerous Stabber Orphen", "Majutsushi Orphen", "Orphen Revenge"], ["Sorcerous Stabber Orphen"], ["魔術士オーフェン"], notes="1998, Revenge & 2020-2023 remake OPs/EDs & singles"),
    AnimeFranchise("paradise-kiss", "Paradise Kiss", "Paradise Kiss", ["ParaKiss"], ["Paradise Kiss"], ["Paradise Kiss"], notes="Tommy february6 OP & Franz Ferdinand ED singles"),
    AnimeFranchise("paranoia-agent", "Paranoia Agent", "妄想代理人", ["Mousou Dairinin"], ["Paranoia Agent"], ["妄想代理人"], notes="Susumu Hirasawa OP/ED singles"),
    AnimeFranchise("patlabor", "Patlabor", "機動警察パトレイバー", ["Mobile Police Patlabor"], ["Patlabor"], ["機動警察パトレイバー"], notes="Early Days OVA, TV, New Files OVA & Movie theme singles"),
    AnimeFranchise("peace-maker-kurogane", "Peace Maker Kurogane", "PEACE MAKER鐵", ["Peacemaker Kurogane"], ["Peacemaker Kurogane"], ["新撰組異聞PEACE MAKER"], notes="TV OP/ED & 2018 Movie singles"),
    AnimeFranchise("pokemon", "Pokémon", "ポケットモンスター", ["Pokemon", "Pocket Monsters"], ["Pokémon (TV series)"], ["ポケットモンスター (アニメ)"], notes="Japanese TV OPs/EDs across all generations, Movie song singles & character singles"),
    AnimeFranchise("powerpuff-girls-z", "Powerpuff Girls Z", "出ましたっ!パワパフガールズZ", ["Demashita! Powerpuff Girls Z", "PPGZ"], ["Powerpuff Girls Z"], ["出ましたっ!パワパフガールズZ"], notes="Japanese TV OPs/EDs singles"),
    AnimeFranchise("pretty-cure", "Pretty Cure", "プリキュアシリーズ", ["Precure", "Futari wa Pretty Cure", "PreCure"], ["Pretty Cure"], ["プリキュアシリーズ", "ふたりはプリキュア"], notes="All Pretty Cure TV OPs/EDs, All Stars / Movie song singles & character singles"),
    AnimeFranchise("ranma-half", "Ranma ½", "らんま1/2", ["Ranma 1/2", "Ranma 1/2 2024"], ["Ranma ½"], ["らんま1/2 (アニメ)"], notes="1989 TV, OVAs, Movies, 2024 remake OPs/EDs & DoCo character singles"),
    AnimeFranchise("rurouni-kenshin", "Rurouni Kenshin", "るろうに剣心", ["Samurai X", "Rurouni Kenshin 2023"], ["Rurouni Kenshin (1996 TV series)", "Rurouni Kenshin (2023 TV series)"], ["るろうに剣心 -明治剣客浪漫譚- (アニメ)"], notes="1996 TV, OVAs, Ishin Shishi Movie, 2023 reboot OPs/EDs & character singles"),
    AnimeFranchise("saber-marionette-j", "Saber Marionette J", "セイバーマリオネットJ", ["Saber Marionette J to X", "Saber Marionette J Again"], ["Saber Marionette J"], ["セイバーマリオネット"], notes="J, J Again, J to X OPs/EDs & Megumi Hayashibara / character singles"),
    AnimeFranchise("sailor-moon", "Sailor Moon", "美少女戦士セーラームーン", ["Bishoujo Senshi Sailor Moon", "Sailor Moon Crystal", "Sailor Moon Eternal", "Sailor Moon Cosmos"], ["Sailor Moon (TV series)", "Sailor Moon Crystal"], ["美少女戦士セーラームーン (テレビアニメ)", "美少女戦士セーラームーンCrystal"], notes="90s TV (Classic-Stars), Movies, Crystal, Eternal, Cosmos & character singles"),
    AnimeFranchise("samurai-7", "Samurai 7", "SAMURAI 7", ["Samurai Seven"], ["Samurai 7"], ["SAMURAI 7"], notes="TV OP/ED singles"),
    AnimeFranchise("school-rumble", "School Rumble", "スクールランブル", ["School Rumble Ni Gakki"], ["School Rumble"], ["スクールランブル"], notes="S1, S2, OVAs & character singles"),
    AnimeFranchise("seirei-no-moribito", "Seirei no Moribito", "精霊の守り人", ["Moribito: Guardian of the Spirit"], ["Moribito: Guardian of the Spirit"], ["精霊の守り人"], notes="L'Arc-en-Ciel OP & Sachi Tainaka ED singles"),
    AnimeFranchise("serial-experiments-lain", "Serial Experiments Lain", "serial experiments lain", ["Lain"], ["Serial Experiments Lain"], ["serial experiments lain"], notes="bôa 'Duvet' OP & Reichi Nakaido ED singles"),
    AnimeFranchise("shaman-king", "Shaman King", "シャーマンキング", ["Shaman King 2021", "Shaman King Flowers"], ["Shaman King", "Shaman King (2021 TV series)"], ["シャーマンキング (アニメ)"], notes="2001 TV, 2021 remake, Flowers & character song singles"),
    AnimeFranchise("shuffle", "Shuffle!", "SHUFFLE!", ["Shuffle! Memories"], ["Shuffle!"], ["SHUFFLE!"], notes="TV, Memories OPs/EDs & character singles"),
    AnimeFranchise("shura-no-toki", "Shura no Toki", "陸奥圓明流外伝 修羅の刻", ["Mutsu Enmei-ryū Gaiden: Shura no Toki"], ["Shura no Toki: Age of Chaos"], ["陸奥圓明流外伝 修羅の刻"], notes="TV OP/ED singles"),
    AnimeFranchise("slayers", "Slayers", "スレイヤーズ", ["Slayers Next", "Slayers Try", "Slayers Revolution", "Slayers Evolution-R"], ["Slayers"], ["スレイヤーズ (アニメ)"], notes="All TV seasons, OVAs, 5 Movie theme singles & Megumi Hayashibara character singles"),
    AnimeFranchise("solty-rei", "Solty Rei", "SoltyRei", ["SoltyRei"], ["SoltyRei"], ["SoltyRei"], notes="TV OP/ED & character singles"),
    AnimeFranchise("soul-eater", "Soul Eater", "ソウルイーター", ["Soul Eater Not!"], ["Soul Eater"], ["ソウルイーター"], notes="Soul Eater, Soul Eater Not! OPs/EDs & character song singles"),
    AnimeFranchise("super-gals", "Super GALS!", "超GALS! 寿蘭", ["Super GALS! Kotobuki Ran", "GALS!"], ["Gals!"], ["GALS!"], notes="TV OP/ED & character singles"),
    AnimeFranchise("suzumiya-haruhi", "Suzumiya Haruhi no Yuuutsu", "涼宮ハルヒの憂鬱", ["The Melancholy of Haruhi Suzumiya", "The Disappearance of Haruhi Suzumiya", "Haruhi Suzumiya"], ["List of Haruhi Suzumiya albums", "The Melancholy of Haruhi Suzumiya"], ["涼宮ハルヒの憂鬱 (アニメ)"], notes="TV 2006/2009, Disappearance movie single, Nagato Yuki-chan, ENOZ & SOS Brigade character singles"),
    AnimeFranchise("sword-art-online", "Sword Art Online", "ソードアート・オンライン", ["SAO", "Sword Art Online II", "Alicization", "Ordinal Scale", "Progressive", "Gun Gale Online"], ["Sword Art Online", "Sword Art Online: Alicization"], ["ソードアート・オンライン (アニメ)"], notes="SAO I/II, Ordinal Scale, Alicization, Progressive movies, GGO & character song singles"),
    AnimeFranchise("gurren-lagann", "Tengen Toppa Gurren Lagann", "天元突破グレンラガン", ["Gurren Lagann"], ["Gurren Lagann"], ["天元突破グレンラガン"], notes="TV OPs/EDs, 2 Movie singles & character singles"),
    AnimeFranchise("tokyo-mew-mew", "Tokyo Mew Mew", "東京ミュウミュウ", ["Tokyo Mew Mew New"], ["Tokyo Mew Mew"], ["東京ミュウミュウ"], notes="2002 TV, Tokyo Mew Mew New OPs/EDs & character singles"),
    AnimeFranchise("tsubasa-chronicle", "Tsubasa Chronicle", "ツバサ・クロニクル", ["Tsubasa: Reservoir Chronicle", "Tsubasa Tokyo Revelations"], ["Tsubasa: Reservoir Chronicle", "List of Tsubasa: Reservoir Chronicle albums"], ["ツバサ・クロニクル"], notes="TV S1/S2, Tokyo Revelations, Spring Thunder OVAs, Movie single & character singles"),
    AnimeFranchise("ueki-no-housoku", "Ueki no Hōsoku (The Law of Ueki)", "うえきの法則", ["The Law of Ueki", "Ueki no Hosoku"], ["The Law of Ueki"], ["うえきの法則"], notes="TV OPs/EDs & character song singles"),
    AnimeFranchise("wolfs-rain", "Wolf's Rain", "WOLF'S RAIN", ["Wolfs Rain"], ["Wolf's Rain"], ["WOLF'S RAIN"], notes="OP/ED & vocal singles"),
    AnimeFranchise("xxxholic", "xxxHOLiC", "xxxHOLiC", ["xxxHOLiC Kei", "xxxHOLiC Shunmuki", "xxxHOLiC Rou"], ["XxxHolic"], ["XxxHOLiC"], notes="TV S1, Kei, OVAs & Movie theme single"),
    AnimeFranchise("yat-anshin-uchuu-ryokou", "YAT Anshin! Uchuu Ryokou", "YAT安心!宇宙旅行", ["YAT Anshin! Luxury Space Tours", "YAT Anshin! Uchū Ryokō"], ["YAT Anshin! Uchū Ryokō"], ["YAT安心!宇宙旅行"], notes="Season 1 & Season 2 TV OPs/EDs & character singles"),
    AnimeFranchise("yawara", "Yawara", "YAWARA!", ["Yawara!", "Yawara! A Fashionable Judo Girl"], ["Yawara!"], ["YAWARA!"], notes="TV OPs/EDs, Movie singles & TV specials"),
    AnimeFranchise("yu-yu-hakusho", "Yu Yu Hakusho", "幽☆遊☆白書", ["YuYu Hakusho", "Yū Yū Hakusho"], ["Yu Yu Hakusho"], ["幽☆遊☆白書 (テレビアニメ)"], notes="TV OP/EDs, Movies & character vocal singles"),
    AnimeFranchise("yu-gi-oh", "Yu-Gi-Oh!", "遊☆戯☆王", ["Yu-Gi-Oh! Duel Monsters", "Yu-Gi-Oh! GX", "Yu-Gi-Oh! 5D's", "Yu-Gi-Oh! ZEXAL", "Yu-Gi-Oh! ARC-V", "Yu-Gi-Oh! VRAINS"], ["Yu-Gi-Oh! Duel Monsters", "Yu-Gi-Oh! GX", "Yu-Gi-Oh! 5D's"], ["遊☆戯☆王デュエルモンスターズ", "遊☆戯☆王デュエルモンスターズGX", "遊☆戯☆王5D's"], notes="Toei 1998, Duel Monsters, GX, 5D's, ZEXAL, Movies & character singles"),
    AnimeFranchise("zatch-bell", "Zatch Bell (Gash bell)", "金色のガッシュベル!!", ["Konjiki no Gash Bell!!", "Zatch Bell!", "Gash Bell"], ["Zatch Bell!"], ["金色のガッシュベル!!"], notes="TV OPs/EDs, 2 Movie singles & character song singles"),
    AnimeFranchise("ef-a-tale-of-memories", "ef a tale of memories", "ef - a tale of memories.", ["ef - a tale of memories.", "ef: A Tale of Memories"], ["Ef: A Fairy Tale of the Two"], ["ef - a tale of memories."], notes="TV OP/ED & character ending theme singles"),
    AnimeFranchise("ef-a-tale-of-melodies", "ef a tale of melodies", "ef - a tale of melodies.", ["ef - a tale of melodies.", "ef: A Tale of Melodies"], ["Ef: A Fairy Tale of the Two"], ["ef - a tale of melodies."], notes="TV OP/ED & character ending theme singles"),
]

FRANCHISE_BY_SLUG: dict[str, AnimeFranchise] = {f.slug: f for f in ANIME_FRANCHISES}


def detect_anime_franchise(text: str) -> AnimeFranchise | None:
    norm = normalize_text(text)
    if not norm:
        return None
    for franchise in ANIME_FRANCHISES:
        candidates = [franchise.name, franchise.jp_name, *franchise.aliases]
        for cand in candidates:
            nc = normalize_text(cand)
            if nc and len(nc) >= 3 and nc in norm:
                return franchise
    return None


def is_soundtrack_album(album_name: str) -> bool:
    if not album_name:
        return False
    return bool(SOUNDTRACK_EXCLUDE_RE.search(album_name))


def _clean_wiki_markup(text: str) -> str:
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S | re.I)
    text = re.sub(r"<ref[^>]*/?>", "", text, flags=re.I)
    text = re.sub(r"\{\{nihongo\|([^|{}]+)(?:\|([^|{}]*))?[^}]*\}\}", r"\1", text, flags=re.I)
    text = re.sub(r"\{\{[Ww]\|(?:[^|{}]*\|)?([^|{}]+)\}\}", r"\1", text)
    text = re.sub(r"\{\{仮リンク\|([^|{}]+)[^}]*\}\}", r"\1", text)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"''+", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip(" \"'「」『』")
    return text


def _extract_ja_wiki_themes(wikitext: str, franchise: AnimeFranchise) -> list[dict[str, Any]]:
    """Extract OP / ED / Movie / Insert / Character theme songs from Japanese Wikipedia 主題歌 sections."""
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    current_category = "OP"
    counters = {"OP": 0, "ED": 0, "Movie": 0, "Insert": 0, "Character": 0}

    lines = wikitext.splitlines()
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        if re.search(r"オープニングテーマ|オープニング", line) and (line.startswith(";") or line.startswith("=") or line.startswith("'''")):
            current_category = "OP"
            continue
        if re.search(r"エンディングテーマ|エンディング", line) and (line.startswith(";") or line.startswith("=") or line.startswith("'''")):
            current_category = "ED"
            continue
        if re.search(r"挿入歌|劇中歌", line) and (line.startswith(";") or line.startswith("=") or line.startswith("'''")):
            current_category = "Insert"
            continue
        if re.search(r"キャラクターソング|キャラソン", line) and (line.startswith(";") or line.startswith("=") or line.startswith("'''")):
            current_category = "Character"
            continue
        if re.search(r"劇場版|映画", line) and (line.startswith(";") or line.startswith("=")):
            current_category = "Movie"
            continue

        # Japanese Wikipedia pattern: 「Song Title」（episodes） followed by 歌 - Artist / 作詞...
        title_match = re.search(r"[「『]([^」』]{1,80})[」』]", line)
        if not title_match:
            continue

        # Check current line and next 2 lines for singer ("歌 - ..." or "歌：..." or "歌 / ...")
        context_block = "\n".join(lines[i : min(len(lines), i + 3)])
        artist_match = re.search(
            r"(?:歌|唄|歌唱|アーティスト|演奏)\s*[-ー:：・/]\s*([^\n、,。/（(]+)",
            _clean_wiki_markup(context_block),
        )
        if not artist_match:
            continue

        title = _clean_wiki_markup(title_match.group(1))
        artist = _clean_wiki_markup(artist_match.group(1))
        artist = re.sub(r"\s*[\(（].*$", "", artist)
        artist = re.sub(r"\s*[:：]\s*(?:第?\d+期|最終期|シーズン\d+|OVA|劇場版).*$", "", artist).strip()
        if not title or not artist or len(title) > 80 or len(artist) > 60:
            continue
        if is_soundtrack_album(title):
            continue

        key = (normalize_text(title), normalize_text(artist))
        if key in seen:
            continue
        seen.add(key)

        counters[current_category] = counters.get(current_category, 0) + 1
        num = counters[current_category]
        ep_match = re.search(r"（([^）]{1,40})）|\(([^)]{1,40})\)", line[title_match.end() :])
        ep = _clean_wiki_markup(ep_match.group(1) or ep_match.group(2)) if ep_match else ""

        entries.append(
            {
                "category": current_category,
                "number": num,
                "title": title,
                "artist": artist,
                "episodes": ep,
                "source": "ja_wikipedia",
            }
        )

    return entries


def _extract_en_wiki_themes(wikitext: str, franchise: AnimeFranchise) -> list[dict[str, Any]]:
    """Extract OP / ED / Theme songs from English Wikipedia sections."""
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    current_category = "OP"
    counters = {"OP": 0, "ED": 0, "Movie": 0, "Insert": 0, "Character": 0}

    for raw in wikitext.splitlines():
        line = raw.strip()
        low = line.lower()
        if line.startswith("=") or line.startswith(";"):
            if "opening" in low:
                current_category = "OP"
            elif "ending" in low or "closing" in low:
                current_category = "ED"
            elif "character" in low:
                current_category = "Character"
            elif "movie" in low or "film" in low:
                current_category = "Movie"
            elif "insert" in low:
                current_category = "Insert"
            continue

        cleaned = _clean_wiki_markup(line)
        # Pattern: "Song Title" by Artist ...
        for m in re.finditer(
            r'"([^"]{2,75})"\s+(?:\([^)]{1,60}\)\s+)?(?:is\s+performed\s+by|performed\s+by|sung\s+by|by)\s+([^.,;(\n]{2,60})',
            cleaned,
            re.I,
        ):
            title = m.group(1).strip()
            artist = m.group(2).strip()
            if not title or not artist or is_soundtrack_album(title):
                continue
            cat = current_category
            ctx_window = cleaned[max(0, m.start() - 60) : min(len(cleaned), m.end() + 60)].lower()
            if "ending" in ctx_window or "closing" in ctx_window:
                cat = "ED"
            elif "opening" in ctx_window:
                cat = "OP"
            elif "movie" in ctx_window or "film" in ctx_window:
                cat = "Movie"

            key = (normalize_text(title), normalize_text(artist))
            if key in seen:
                continue
            seen.add(key)

            counters[cat] = counters.get(cat, 0) + 1
            entries.append(
                {
                    "category": cat,
                    "number": counters[cat],
                    "title": title,
                    "artist": artist,
                    "episodes": "",
                    "source": "en_wikipedia",
                }
            )

    return entries


class AnimeCatalogManager:
    """Manages the 41-anime collection gallery, live Wiki/iTunes/JioSaavn scanner, and persistent CD To-Do state."""

    def __init__(
        self,
        db_path: str | None = None,
        proxies: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ):
        import sys

        if db_path:
            self.db_path = db_path
        elif getattr(sys, "frozen", False):
            app_support = os.path.join(
                os.path.expanduser("~"),
                "Library",
                "Application Support",
                "Saavn-Downloader",
            )
            self.db_path = os.path.join(app_support, "anime_gallery_db.json")
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.db_path = os.path.join(base_dir, "data", "anime_gallery_db.json")
        self.proxies = proxies
        self.headers = headers
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": WIKI_USER_AGENT})
        self.artwork_fetcher = HighResArtworkFetcher(proxies=proxies)
        self.saavn_matcher = SaavnMatcher(proxies=proxies, headers=headers)
        self._db: dict[str, Any] = self._load_db()

    def _load_db(self) -> dict[str, Any]:
        if os.path.isfile(self.db_path):
            try:
                with open(self.db_path, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:
                pass
        return {"animes": {}, "updated_at": None}

    def _save_db(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._db["updated_at"] = datetime.now(timezone.utc).isoformat()
        with open(self.db_path, "w", encoding="utf-8") as fh:
            json.dump(self._db, fh, ensure_ascii=False, indent=2)

    def list_franchises_summary(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        stored_animes = self._db.get("animes") or {}
        for f in ANIME_FRANCHISES:
            rec = stored_animes.get(f.slug) or {}
            entries = rec.get("entries") or []
            ready = sum(1 for e in entries if e.get("status") == "ready_saavn")
            owned = sum(1 for e in entries if e.get("owned_from_cd") or e.get("status") == "owned_cd")
            flagged = sum(1 for e in entries if e.get("status") == "flagged_cover")
            todo = sum(1 for e in entries if e.get("status") == "todo_cd" and not e.get("owned_from_cd"))
            summaries.append(
                {
                    **f.to_dict(),
                    "scanned": bool(entries),
                    "last_scanned": rec.get("last_scanned"),
                    "total_entries": len(entries),
                    "ready_saavn": ready,
                    "todo_cd": todo,
                    "flagged_cover": flagged,
                    "owned_cd": owned,
                    "linked_playlists": rec.get("linked_playlists") or f.default_playlists,
                }
            )
        return summaries

    def get_anime_gallery(self, slug: str) -> dict[str, Any]:
        franchise = FRANCHISE_BY_SLUG.get(slug)
        if not franchise:
            raise ValueError(f"Unknown anime slug: {slug}")
        rec = (self._db.get("animes") or {}).get(slug) or {}
        return {
            "franchise": franchise.to_dict(),
            "scanned": bool(rec.get("entries")),
            "last_scanned": rec.get("last_scanned"),
            "linked_playlists": rec.get("linked_playlists") or list(franchise.default_playlists),
            "entries": rec.get("entries") or [],
        }

    def toggle_cd_owned(self, slug: str, entry_id: str, owned: bool) -> dict[str, Any] | None:
        rec = (self._db.get("animes") or {}).get(slug)
        if not rec:
            return None
        for entry in rec.get("entries") or []:
            if entry.get("entry_id") == entry_id:
                entry["owned_from_cd"] = bool(owned)
                if owned:
                    entry["status"] = "owned_cd"
                else:
                    entry["status"] = (
                        "ready_saavn"
                        if entry.get("saavn_match") and not entry.get("flags")
                        else ("flagged_cover" if entry.get("flags") else "todo_cd")
                    )
                self._save_db()
                return entry
        return None

    def _fetch_wiki_section_wikitext(self, lang: str, page: str) -> str:
        api = f"https://{lang}.wikipedia.org/w/api.php"
        try:
            sec_resp = self.session.get(
                api,
                params={"action": "parse", "page": page, "prop": "sections", "format": "json"},
                timeout=12,
            )
            if sec_resp.status_code != 200:
                return ""
            sections = sec_resp.json().get("parse", {}).get("sections") or []
            target_indices: list[str] = []
            for s in sections:
                line = s.get("line", "")
                if any(
                    kw in line.lower()
                    for kw in (
                        "theme",
                        "opening",
                        "ending",
                        "music",
                        "single",
                        "character song",
                        "主題歌",
                        "オープニング",
                        "エンディング",
                        "音楽",
                        "キャラクターソング",
                    )
                ):
                    target_indices.append(str(s["index"]))
            if not target_indices:
                target_indices = ["0"]

            chunks: list[str] = []
            for idx in target_indices[:4]:
                r = self.session.get(
                    api,
                    params={
                        "action": "parse",
                        "page": page,
                        "section": idx,
                        "prop": "wikitext",
                        "format": "json",
                    },
                    timeout=12,
                )
                if r.status_code == 200:
                    wt = r.json().get("parse", {}).get("wikitext", {}).get("*", "")
                    if wt:
                        chunks.append(wt)
            return "\n".join(chunks)
        except Exception:
            return ""

    def _resolve_itunes_single_metadata(
        self, title: str, artist: str, artist_aliases: list[str] | None = None
    ) -> dict[str, Any]:
        """Find the official non-OST single/album on iTunes with 3000x3000 cover and tracklist."""
        expected_artists = [artist, *(artist_aliases or [])]
        clean_title = strip_title_annotations(title)
        term = f"{clean_title} {artist}".strip()

        best_song: dict[str, Any] | None = None
        best_score = 0.0
        for country in ("jp", "us"):
            try:
                resp = self.session.get(
                    "https://itunes.apple.com/search",
                    params={"term": term, "entity": "song", "limit": 8, "country": country},
                    timeout=12,
                )
                if resp.status_code != 200:
                    continue
                results = resp.json().get("results") or []
            except Exception:
                continue

            for item in results:
                t_name = item.get("trackName") or ""
                a_name = item.get("artistName") or ""
                c_name = item.get("collectionName") or ""
                if is_soundtrack_album(c_name):
                    continue
                if detect_cover_or_karaoke(t_name, [a_name], album_name=c_name):
                    continue
                artist_ok, a_score = _artist_matches([a_name], expected_artists)
                if not artist_ok:
                    continue
                t_score = max(_similarity(t_name, title), _similarity(t_name, clean_title))
                if t_score < 0.65:
                    continue
                # Prefer singles (fewer tracks) over huge greatest-hits compilations
                track_cnt = int(item.get("trackCount") or 99)
                single_bonus = 0.12 if track_cnt <= 6 else (0.04 if track_cnt <= 12 else 0.0)
                score = 0.50 * t_score + 0.38 * a_score + single_bonus
                if score > best_score:
                    best_score = score
                    best_song = item
            if best_song:
                break

        if not best_song:
            return {}

        art_100 = best_song.get("artworkUrl100") or ""
        cover_url = re.sub(r"/\d+x\d+bb\.(jpg|png)$", "/3000x3000bb.\\1", art_100) if art_100 else None
        collection_id = best_song.get("collectionId")
        single_tracks: list[dict[str, Any]] = []
        label_company = ""

        # Lookup full single/album tracklist if trackCount <= 12 (to keep scans snappy)
        track_count = int(best_song.get("trackCount") or 0)
        if collection_id and track_count and track_count <= 12:
            try:
                lk = self.session.get(
                    "https://itunes.apple.com/lookup",
                    params={"id": collection_id, "entity": "song", "country": "jp"},
                    timeout=10,
                )
                if lk.status_code == 200:
                    rows = lk.json().get("results") or []
                    for row in rows:
                        if row.get("wrapperType") == "collection":
                            label_company = row.get("copyright") or ""
                        elif row.get("wrapperType") == "track":
                            single_tracks.append(
                                {
                                    "track_number": row.get("trackNumber"),
                                    "title": row.get("trackName"),
                                    "artist": row.get("artistName"),
                                    "duration_ms": row.get("trackTimeMillis") or 0,
                                }
                            )
            except Exception:
                pass

        return {
            "single_or_album": best_song.get("collectionName") or "",
            "single_track_count": track_count or len(single_tracks) or None,
            "single_tracks": single_tracks,
            "release_date": (best_song.get("releaseDate") or "")[:10],
            "label_company": label_company,
            "cover_url": cover_url,
            "cover_resolution": "3000x3000" if cover_url else "",
            "cover_source": "itunes" if cover_url else "",
        }

    def scan_anime(
        self,
        slug: str,
        check_saavn: bool = True,
        enrich_itunes: bool = True,
        max_items: int = 80,
        progress_callback=None,
    ) -> dict[str, Any]:
        """Discover openings, endings, movie/special themes, and character singles for an anime.

        Excludes soundtracks/OSTs, resolves official single metadata & 3000x3000 covers,
        verifies JioSaavn availability, and builds Gallery cards (marking unavailable items as CD To-Do).
        """
        franchise = FRANCHISE_BY_SLUG.get(slug)
        if not franchise:
            raise ValueError(f"Unknown anime slug: {slug}")

        existing_rec = (self._db.get("animes") or {}).get(slug) or {}
        existing_owned = {
            e.get("entry_id"): bool(e.get("owned_from_cd"))
            for e in (existing_rec.get("entries") or [])
        }

        raw_songs: list[dict[str, Any]] = []

        # 1. Special authoritative provider for One Piece
        if slug == "one-piece":
            op_provider = OnePieceReferenceProvider(use_network=True)
            for ref in op_provider.get_openings():
                raw_songs.append(
                    {
                        "category": "OP",
                        "number": ref.number,
                        "title": ref.title,
                        "artist": ref.artist,
                        "episodes": f"Episodes {ref.episodes}" if ref.episodes else "",
                        "reference": ref,
                        "source": ref.source,
                    }
                )
            for ref in op_provider.get_endings():
                raw_songs.append(
                    {
                        "category": "ED",
                        "number": ref.number,
                        "title": ref.title,
                        "artist": ref.artist,
                        "episodes": f"Episodes {ref.episodes}" if ref.episodes else "",
                        "reference": ref,
                        "source": ref.source,
                    }
                )
            for ref in op_provider.get_movie_themes():
                raw_songs.append(
                    {
                        "category": "Movie",
                        "number": ref.number,
                        "title": ref.title,
                        "artist": ref.artist,
                        "episodes": f"Movie #{ref.number}",
                        "reference": ref,
                        "source": ref.source,
                    }
                )
        else:
            # 2. Fetch from Japanese Wikipedia (most complete for anime OP/ED/Insert/Character songs)
            for jp_page in franchise.ja_wiki_pages:
                wt = self._fetch_wiki_section_wikitext("ja", jp_page)
                if wt:
                    raw_songs.extend(_extract_ja_wiki_themes(wt, franchise))

            # 3. Also parse English Wikipedia if Japanese Wikipedia returned few items
            if len(raw_songs) < 4:
                for en_page in franchise.en_wiki_pages:
                    wt = self._fetch_wiki_section_wikitext("en", en_page)
                    if wt:
                        raw_songs.extend(_extract_en_wiki_themes(wt, franchise))

            # 4. Also discover character song singles & theme singles on iTunes JP (filtering out OSTs)
            if len(raw_songs) < 6:
                for query_suffix in ("主題歌", "キャラクターソング", ""):
                    q = f"{franchise.jp_name} {query_suffix}".strip()
                    try:
                        r = self.session.get(
                            "https://itunes.apple.com/search",
                            params={"term": q, "entity": "album", "limit": 10, "country": "jp"},
                            timeout=10,
                        )
                        if r.status_code == 200:
                            for alb in r.json().get("results") or []:
                                cname = alb.get("collectionName") or ""
                                aname = alb.get("artistName") or ""
                                tcnt = int(alb.get("trackCount") or 0)
                                if is_soundtrack_album(cname) or detect_cover_or_karaoke(cname, [aname]):
                                    continue
                                if tcnt <= 0 or tcnt > 12:
                                    continue
                                raw_songs.append(
                                    {
                                        "category": "Character" if "キャラ" in cname or "character" in cname.lower() else "OP",
                                        "number": None,
                                        "title": cname,
                                        "artist": aname,
                                        "episodes": f"Single/EP ({tcnt} tracks)",
                                        "source": "itunes_catalog",
                                    }
                                )
                    except Exception:
                        pass

        # Deduplicate raw_songs by (normalized title, normalized artist)
        deduped: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str]] = set()
        for item in raw_songs:
            key = (normalize_text(item["title"]), normalize_text(item["artist"]))
            if not key[0] or key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(item)
            if len(deduped) >= max_items:
                break

        def _enrich_one(idx: int, item: dict[str, Any]) -> GalleryEntry:
            cat = item.get("category", "OP")
            num = item.get("number")
            title = item["title"]
            artist = item["artist"]
            ref: ReferenceSong | None = item.get("reference")
            label = f"{cat} #{num}" if num else cat
            entry_id = f"{slug}:{cat.lower()}:{num or idx}:{normalize_text(title)[:24]}"

            itunes_meta: dict[str, Any] = {}
            if enrich_itunes:
                itunes_meta = self._resolve_itunes_single_metadata(
                    title=title,
                    artist=artist,
                    artist_aliases=ref.artist_aliases if ref else None,
                )

            saavn_match_dict: dict[str, Any] | None = None
            saavn_note = "Not checked"
            if check_saavn:
                match, note = self.saavn_matcher.match_track(
                    title=title,
                    artists=[artist],
                    reference=ref,
                )
                saavn_match_dict = match.to_dict() if match else None
                saavn_note = note

            owned = existing_owned.get(entry_id, False)
            status = "owned_cd" if owned else ("ready_saavn" if saavn_match_dict else "todo_cd")

            ytm_q = quote(f"{title} {artist}")
            return GalleryEntry(
                entry_id=entry_id,
                anime_slug=slug,
                category=cat,
                number=num,
                label=label,
                title=title,
                artist=artist,
                episodes_or_context=item.get("episodes", ""),
                single_or_album=itunes_meta.get("single_or_album", ""),
                single_track_count=itunes_meta.get("single_track_count"),
                single_tracks=itunes_meta.get("single_tracks", []),
                release_date=itunes_meta.get("release_date", ""),
                label_company=itunes_meta.get("label_company", ""),
                cover_url=itunes_meta.get("cover_url") or (saavn_match_dict or {}).get("image_url"),
                cover_resolution=itunes_meta.get("cover_resolution") or ("500x500" if saavn_match_dict else ""),
                cover_source=itunes_meta.get("cover_source") or ("jiosaavn" if saavn_match_dict else ""),
                youtube_music_query_url=f"https://music.youtube.com/search?q={ytm_q}",
                saavn_match=saavn_match_dict,
                saavn_note=saavn_note,
                status=status,
                owned_from_cd=owned,
                source=item.get("source", "wiki"),
            )

        entries: list[GalleryEntry] = []
        if deduped:
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = {pool.submit(_enrich_one, idx, it): idx for idx, it in enumerate(deduped, start=1)}
                ordered: dict[int, GalleryEntry] = {}
                done = 0
                for fut in as_completed(futures):
                    idx = futures[fut]
                    done += 1
                    try:
                        ordered[idx] = fut.result()
                    except Exception:
                        pass
                    if progress_callback:
                        progress_callback(done, len(deduped))
                entries = [ordered[k] for k in sorted(ordered.keys())]

        self._db.setdefault("animes", {})[slug] = {
            "last_scanned": datetime.now(timezone.utc).isoformat(),
            "linked_playlists": existing_rec.get("linked_playlists") or list(franchise.default_playlists),
            "entries": [e.to_dict() for e in entries],
        }
        self._save_db()
        return self.get_anime_gallery(slug)

    def merge_spotify_plan_into_gallery(self, slug: str, plan: dict[str, Any]) -> dict[str, Any]:
        """Sync a crosschecked Spotify playlist analysis into an anime's gallery (preserving cover/metadata & CD To-Do)."""
        franchise = FRANCHISE_BY_SLUG.get(slug)
        if not franchise:
            raise ValueError(f"Unknown anime slug: {slug}")

        rec = self._db.setdefault("animes", {}).setdefault(
            slug,
            {
                "last_scanned": datetime.now(timezone.utc).isoformat(),
                "linked_playlists": list(franchise.default_playlists),
                "entries": [],
            },
        )
        pl_info = plan.get("playlist") or {}
        if pl_info.get("url"):
            linked = rec.setdefault("linked_playlists", [])
            if not any(p.get("url") == pl_info["url"] for p in linked):
                linked.append({"label": pl_info.get("name") or "Spotify Playlist", "url": pl_info["url"]})

        existing_by_id: dict[str, dict[str, Any]] = {
            e["entry_id"]: e for e in rec.get("entries") or [] if e.get("entry_id")
        }

        for item in plan.get("items") or []:
            tr = item.get("track") or {}
            ver = item.get("verification") or {}
            ref = ver.get("matched_reference")
            art = item.get("artwork") or {}
            saavn = item.get("saavn_match")

            if ref:
                cat = "OP" if ref.get("kind") == "opening" else "ED"
                num = ref.get("number")
                label = ref.get("label") or f"{cat} #{num}"
                title = ref.get("title") or tr.get("title", "")
                artist = ref.get("artist") or ", ".join(tr.get("artists") or [])
                episodes = f"Episodes {ref.get('episodes')}" if ref.get("episodes") else ""
                entry_id = f"{slug}:{cat.lower()}:{num}"
            else:
                cat = "OP"
                num = tr.get("position")
                label = f"Track #{num}"
                title = tr.get("title", "")
                artist = ", ".join(tr.get("artists") or [])
                episodes = ""
                entry_id = f"{slug}:sp:{tr.get('track_id') or num}"

            prev = existing_by_id.get(entry_id) or {}
            owned = bool(prev.get("owned_from_cd", False))
            flags = list(ver.get("flags") or [])
            warnings = list(ver.get("warnings") or [])

            if owned:
                status = "owned_cd"
            elif flags:
                status = "flagged_cover"
            elif saavn:
                status = "ready_saavn"
            else:
                status = "todo_cd"

            alb = tr.get("album") or {}
            single_tracks = [
                {
                    "track_number": at.get("track_number"),
                    "title": at.get("title"),
                    "artist": ", ".join(at.get("artists") or []),
                    "duration_ms": at.get("duration_ms") or 0,
                }
                for at in (alb.get("tracks") or [])
            ]

            entry = GalleryEntry(
                entry_id=entry_id,
                anime_slug=slug,
                category=cat,
                number=num,
                label=label,
                title=title,
                artist=artist,
                episodes_or_context=episodes,
                single_or_album=tr.get("album_name") or prev.get("single_or_album", ""),
                single_track_count=tr.get("album_total_tracks") or prev.get("single_track_count"),
                single_tracks=single_tracks or prev.get("single_tracks", []),
                release_date=tr.get("release_date") or prev.get("release_date", ""),
                cover_url=(None if flags else art.get("url")) or prev.get("cover_url") or art.get("url"),
                cover_resolution=(
                    art.get("resolution", "") if not flags else prev.get("cover_resolution", art.get("resolution", ""))
                ),
                cover_source=art.get("source", "") or prev.get("cover_source", ""),
                spotify_track_url=None if flags else tr.get("url"),
                spotify_album_url=None if flags else tr.get("album_url"),
                youtube_music_query_url=f"https://music.youtube.com/search?q={quote(f'{title} {artist}')}",
                saavn_match=saavn,
                saavn_note=item.get("saavn_note", ""),
                status=status,
                flags=flags,
                warnings=warnings,
                owned_from_cd=owned,
                source="spotify+reference",
            )
            existing_by_id[entry_id] = entry.to_dict()

        # Also add MISSING official reference themes directly into the Gallery as CD To-Do cards!
        for ref in plan.get("missing_references") or []:
            cat = "OP" if ref.get("kind") == "opening" else "ED"
            num = ref.get("number")
            entry_id = f"{slug}:{cat.lower()}:{num}"
            if entry_id in existing_by_id:
                continue
            title = ref.get("title", "")
            artist = ref.get("artist", "")
            itunes_meta = self._resolve_itunes_single_metadata(
                title=title,
                artist=artist,
                artist_aliases=ref.get("artist_aliases"),
            )
            entry = GalleryEntry(
                entry_id=entry_id,
                anime_slug=slug,
                category=cat,
                number=num,
                label=ref.get("label") or f"{cat} #{num}",
                title=title,
                artist=artist,
                episodes_or_context=f"Episodes {ref.get('episodes')}" if ref.get("episodes") else "",
                single_or_album=itunes_meta.get("single_or_album", ""),
                single_track_count=itunes_meta.get("single_track_count"),
                single_tracks=itunes_meta.get("single_tracks", []),
                release_date=itunes_meta.get("release_date", ""),
                label_company=itunes_meta.get("label_company", ""),
                cover_url=itunes_meta.get("cover_url"),
                cover_resolution=itunes_meta.get("cover_resolution", ""),
                cover_source=itunes_meta.get("cover_source", ""),
                youtube_music_query_url=f"https://music.youtube.com/search?q={quote(f'{title} {artist}')}",
                saavn_match=None,
                saavn_note="Missing from Spotify playlist — added to CD To-Do",
                status="todo_cd",
                flags=["Missing from Spotify playlist"],
                owned_from_cd=False,
                source="reference_missing",
            )
            existing_by_id[entry_id] = entry.to_dict()

        # Sort cleanly: OP ascending, then ED ascending, then Movie/Special/Character
        cat_order = {"OP": 0, "ED": 1, "Movie": 2, "Special": 3, "Insert": 4, "Character": 5}
        rec["entries"] = sorted(
            existing_by_id.values(),
            key=lambda e: (cat_order.get(e.get("category", "OP"), 9), e.get("number") or 9999, e.get("title", "")),
        )
        rec["last_scanned"] = datetime.now(timezone.utc).isoformat()
        self._save_db()
        return self.get_anime_gallery(slug)

    def build_master_todo_markdown(self, slug: str | None = None) -> str:
        """Build a CD To-Do Markdown report for a single anime or across all 41 franchises."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        slugs = [slug] if slug else [f.slug for f in ANIME_FRANCHISES]
        lines = [
            f"# Anime CD Extraction To-Do List ({FRANCHISE_BY_SLUG[slug].name if slug else f'All {len(ANIME_FRANCHISES)} Series'})",
            "",
            f"- **Generated:** {now}",
            "",
        ]
        total_todo = 0
        for s in slugs:
            franchise = FRANCHISE_BY_SLUG[s]
            rec = (self._db.get("animes") or {}).get(s) or {}
            entries = rec.get("entries") or []
            todos = [
                e
                for e in entries
                if e.get("status") in ("todo_cd", "flagged_cover") and not e.get("owned_from_cd")
            ]
            if not todos:
                continue
            total_todo += len(todos)
            lines.append(f"## {franchise.name} ({franchise.jp_name}) — {len(todos)} To-Do")
            lines.append("")
            for e in todos:
                alb = f" — Single/Album: `{e['single_or_album']}`" if e.get("single_or_album") else ""
                rel = f" ({e['release_date']})" if e.get("release_date") else ""
                reason = "; ".join(e.get("flags") or []) or e.get("saavn_note") or "Unavailable on JioSaavn"
                lines.append(
                    f"- [ ] **{e['label']} — {e['title']}** by **{e['artist']}**{alb}{rel}\n"
                    f"  - Status: {reason}"
                )
            lines.append("")

        if total_todo == 0:
            lines.append("No pending CD To-Do items yet. Scan an anime or analyze a Spotify playlist to populate this list.")
            lines.append("")
        return "\n".join(lines)
