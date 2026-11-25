from __future__ import annotations

import difflib
import html
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

import requests

WIKI_USER_AGENT = "SaavnDownloader/1.0 (https://github.com/JuanmiRodas/Saavn-Downloader)"


@dataclass
class ReferenceSong:
    kind: str  # "opening" | "ending"
    number: int
    title: str
    artist: str
    episodes: str = ""
    title_aliases: list[str] = field(default_factory=list)
    artist_aliases: list[str] = field(default_factory=list)
    source: str = "builtin"

    @property
    def label(self) -> str:
        prefix = {"opening": "OP", "ending": "ED", "movie": "Movie"}.get(self.kind, self.kind.upper())
        return f"One Piece {prefix} #{self.number}"

    def all_titles(self) -> list[str]:
        return [self.title, *self.title_aliases]

    def all_artists(self) -> list[str]:
        return [self.artist, *self.artist_aliases]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["label"] = self.label
        return data


@dataclass
class VerificationResult:
    status: str  # "verified" | "warning" | "flagged"
    is_original_artist: bool
    is_cover_or_karaoke: bool
    enabled_by_default: bool
    flags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    matched_reference: ReferenceSong | None = None
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "is_original_artist": self.is_original_artist,
            "is_cover_or_karaoke": self.is_cover_or_karaoke,
            "enabled_by_default": self.enabled_by_default,
            "flags": list(self.flags),
            "warnings": list(self.warnings),
            "matched_reference": self.matched_reference.to_dict()
            if self.matched_reference
            else None,
            "confidence": round(self.confidence, 3),
        }


# Known title & artist aliases (Japanese / Kanji / Romaji / English / alternate romanizations)
# These enrich both live One Piece Wiki results and the offline fallback table.
ONE_PIECE_OPENING_ALIASES: dict[int, dict[str, list[str]]] = {
    1: {"titles": ["ウィーアー!", "ウィーアー！", "We Are!", "We Are"], "artists": ["きただにひろし", "Kitadani Hiroshi", "Hiroshi Kitadani"]},
    2: {"titles": ["Believe"], "artists": ["Folder5", "Folder 5"]},
    3: {"titles": ["ヒカリへ", "Hikari e", "Hikari He", "Toward the Light"], "artists": ["The BabyStars", "The Babystars", "ザ・ベイビースターズ"]},
    4: {"titles": ["BON VOYAGE!", "Bon Voyage!"], "artists": ["Bon-Bon Blanco", "BON-BON BLANCO"]},
    5: {"titles": ["ココロのちず", "Kokoro no Chizu", "Map of the Heart"], "artists": ["BOYSTYLE"]},
    6: {"titles": ["Brand New World", "BRAND NEW WORLD"], "artists": ["D-51"]},
    7: {
        "titles": ["ウィーアー!", "ウィーアー! - 7人の麦わら海賊団篇", "ウィーアー! - 9人の麦わらの一味編", "We Are! (7 Straw Hat Pirates Ver.)", "We Are! (Straw Hat Pirates Version)"],
        "artists": ["7人の麦わら海賊団", "9人の麦わらの一味", "Straw Hat Pirates", "7 Straw Hat Pirates", "Cast of the Straw Hat Pirates", "Mayumi Tanaka"],
    },
    8: {"titles": ["Crazy Rainbow", "×〜ダメ〜/Crazy Rainbow", "Dame/Crazy Rainbow"], "artists": ["Tackey & Tsubasa", "Tackey and Tsubasa", "タッキー&翼", "タッキー＆翼"]},
    9: {"titles": ["Jungle P"], "artists": ["5050"]},
    10: {
        "titles": ["ウィーアー!", "We Are!", "We Are! (Remix)", "We Are! (One Piece Animation 10th Anniversary Ver.)"],
        "artists": ["TVXQ!", "TVXQ", "Tohoshinki", "東方神起"],
    },
    11: {"titles": ["Share The World", "Share the World"], "artists": ["TVXQ!", "TVXQ", "Tohoshinki", "東方神起"]},
    12: {"titles": ["風をさがして", "Kaze o Sagashite", "Kaze wo Sagashite"], "artists": ["Mari Yaguchi", "矢口真里", "Mari Yaguchi and Straw Hats", "矢口真里とストローハット"]},
    13: {"titles": ["One day", "One Day"], "artists": ["The ROOTLESS", "THE ROOTLESS", "Rootless"]},
    14: {"titles": ["Fight Together"], "artists": ["Namie Amuro", "安室奈美恵"]},
    15: {"titles": ["ウィーゴー!", "ウィーゴー！", "We Go!"], "artists": ["きただにひろし", "Kitadani Hiroshi", "Hiroshi Kitadani"]},
    16: {"titles": ["HANDS UP!", "HANDS UP !", "Hands Up!"], "artists": ["Kota Shinzato", "Kota Shinto", "新里宏太"]},
    17: {"titles": ["Wake up!", "Wake Up!"], "artists": ["AAA"]},
    18: {"titles": ["Hard Knock Days"], "artists": ["GENERATIONS from EXILE TRIBE", "Generations from Exile Tribe"]},
    19: {"titles": ["ウィーキャン!", "ウィーキャン！", "We Can!"], "artists": ["Kishidan", "氣志團", "Hiroshi Kitadani", "きただにひろし", "氣志團ときただにひろし"]},
    20: {"titles": ["Hope"], "artists": ["Namie Amuro", "安室奈美恵"]},
    21: {"titles": ["Super Powers"], "artists": ["V6"]},
    22: {"titles": ["OVER THE TOP", "Over the Top"], "artists": ["Hiroshi Kitadani", "きただにひろし", "Kitadani Hiroshi"]},
    23: {"titles": ["DREAMIN' ON", "Dreamin' On"], "artists": ["Da-iCE", "Da-ice"]},
    24: {"titles": ["PAINT", "Paint"], "artists": ["I Don't Like Mondays."]},
    25: {"titles": ["最高到達点", "Saiko Totatsuten", "Saikou Toutatsuten", "The Peak"], "artists": ["SEKAI NO OWARI", "Sekai no Owari"]},
    26: {"titles": ["あーーっす!", "あーーっす！", "UUUUUS!", "Assu!"], "artists": ["Hiroshi Kitadani", "きただにひろし", "Kitadani Hiroshi"]},
    27: {"titles": ["天使と悪魔", "Tenshi to Akuma", "Angel & Devil", "Angel and Devil"], "artists": ["GRe4N BOYZ", "GReeeeN"]},
    28: {"titles": ["カーマイン", "Carmine"], "artists": ["ELLEGARDEN", "Ellegarden"]},
    29: {"titles": ["ルミナス", "ルミナス - Luminous", "Luminous"], "artists": ["AiNA THE END", "Aina the End", "アイナ・ジ・エンド"]},
}

ONE_PIECE_ENDING_ALIASES: dict[int, dict[str, list[str]]] = {
    1: {"titles": ["memories", "Memories"], "artists": ["Maki Otsuki", "大槻マキ", "Otsuki Maki"]},
    2: {"titles": ["RUN! RUN! RUN!", "Run! Run! Run!"], "artists": ["Maki Otsuki", "大槻マキ", "Otsuki Maki"]},
    3: {"titles": ["私がいるよ", "Watashi ga Iru Yo", "Watashi ga Iruyo"], "artists": ["TOMATO CUBE", "Tomato Cube"]},
    4: {"titles": ["しょうちのすけ", "Shochi no suke", "Shōchi no suke", "Shouchi no Suke"], "artists": ["Suitei Shoujo", "Suiteisho-jo", "推定少女"]},
    5: {"titles": ["BEFORE DAWN", "Before Dawn"], "artists": ["AI-SACHI"]},
    6: {"titles": ["fish", "Fish"], "artists": ["The Kaleidoscope", "the kaleidoscope", "ザ・カレイドスコープ"]},
    7: {"titles": ["GLORY-君がいるから-", "GLORY -Kimi ga Iru Kara-", "GLORY -君がいるから-"], "artists": ["Takako Uehara", "上原多香子"]},
    8: {"titles": ["Shining ray", "Shining Ray"], "artists": ["Janne Da Arc", "Janne da Arc", "ジャンヌダルク"]},
    9: {"titles": ["Free Will", "Free will"], "artists": ["Ruppina", "Ruppina+", "Mai"]},
    10: {"titles": ["FAITH", "Faith"], "artists": ["Ruppina", "Ruppina+", "Mai"]},
    11: {"titles": ["A to Z", "A to Z - ONE PIECE Edition"], "artists": ["ZZ"]},
    12: {"titles": ["月と太陽", "Tsuki to Taiyo", "Tsuki to Taiyou", "Moon & Sun", "Moon and Sun"], "artists": ["shela", "Shela"]},
    13: {"titles": ["DREAMSHIP", "Dreamship"], "artists": ["Aiko Ikuta", "Ikuta Aiko", "幾田愛子", "ACKO"]},
    14: {"titles": ["未来航海", "Mirai Kokai", "Mirai Kōkai", "Mirai Koukai"], "artists": ["Tackey & Tsubasa", "Tackey and Tsubasa", "タッキー&翼", "タッキー＆翼"]},
    15: {"titles": ["エターナルポーズ", "Eternal Pose"], "artists": ["Asia Engineer", "エイジアエンジニア"]},
    16: {"titles": ["Dear friends", "Dear Friends"], "artists": ["TRIPLANE", "Triplane"]},
    17: {"titles": ["明日は来るから", "Asu wa Kuru Kara", "Asu wa Kuru kara"], "artists": ["TVXQ!", "TVXQ", "Tohoshinki", "東方神起"]},
    18: {"titles": ["ADVENTURE WORLD", "Adventure World"], "artists": ["Delicatessen", "デリカテッセン"]},
    19: {"titles": ["Raise"], "artists": ["Chilli Beans.", "Chilli Beans"]},
    20: {"titles": ["Dear sunrise", "Dear Sunrise"], "artists": ["Maki Otsuki", "大槻マキ", "Otsuki Maki"]},
    21: {"titles": ["The 1"], "artists": ["muque"]},
    22: {"titles": ["PUNKS", "Punks"], "artists": ["CHAMELEON LIME WHOOPIEPIE", "Chameleon Lime Whoopiepie", "Chameleon Lime Whoopie Pie", "カメレオン・ライム・ウーピーパイ"]},
    23: {"titles": ["その未来", "Sono Mirai"], "artists": ["時速36km", "Jisoku 36km", "Jisoku 36km/h", "36km/h", "速36km"]},
}

# Built-in fallback catalog (identical to One Piece Wiki TV Episodes tables as of 2026)
BUILTIN_ONE_PIECE_OPENINGS: list[tuple[int, str, str, str]] = [
    (1, "We Are!", "Hiroshi Kitadani", "1-47, 1000"),
    (2, "Believe", "Folder 5", "48-115"),
    (3, "Hikari e", "The Babystars", "116-168"),
    (4, "BON VOYAGE!", "Bon-Bon Blanco", "169-206"),
    (5, "Kokoro no Chizu", "BOYSTYLE", "207-263"),
    (6, "Brand New World", "D-51", "264-278"),
    (7, "We Are! (Straw Hat Pirates Version)", "Cast of the Straw Hat Pirates", "279-283"),
    (8, "Crazy Rainbow", "Tackey & Tsubasa", "284-325"),
    (9, "Jungle P", "5050", "326-372"),
    (10, "We Are! (Remix)", "TVXQ", "373-394"),
    (11, "Share the World", "TVXQ", "395-425"),
    (12, "Kaze o Sagashite", "Mari Yaguchi and Straw Hats", "426-458"),
    (13, "One day", "THE ROOTLESS", "459-492"),
    (14, "Fight Together", "Namie Amuro", "493-516"),
    (15, "We Go!", "Hiroshi Kitadani", "517-590"),
    (16, "Hands Up!", "Kota Shinzato", "591-628"),
    (17, "Wake up!", "AAA", "629-686"),
    (18, "Hard Knock Days", "Generations from Exile Tribe", "687-746"),
    (19, "We Can!", "Hiroshi Kitadani and Kishidan", "747-806"),
    (20, "Hope", "Namie Amuro", "807-855"),
    (21, "Super Powers", "V6", "856-891"),
    (22, "OVER THE TOP", "Hiroshi Kitadani", "892-934"),
    (23, "DREAMIN' ON", "Da-iCE", "935-999, 1001-1004"),
    (24, "PAINT", "I Don't Like Mondays.", "1005-1073"),
    (25, "Saiko Totatsuten", "Sekai no Owari", "1074-1088"),
    (26, "UUUUUS!", "Hiroshi Kitadani", "1089-1122"),
    (27, "Tenshi to Akuma", "GRe4N BOYZ", "1123-1138"),
    (28, "Carmine", "Ellegarden", "1139-1155"),
    (29, "Luminous", "AiNA THE END", "1156-1180"),
]

BUILTIN_ONE_PIECE_ENDINGS: list[tuple[int, str, str, str]] = [
    (1, "Memories", "Maki Otsuki", "1-30"),
    (2, "RUN! RUN! RUN!", "Maki Otsuki", "31-63"),
    (3, "Watashi ga Iru Yo", "TOMATO CUBE", "64-73"),
    (4, "Shōchi no suke", "Suitei Shoujo", "74-81"),
    (5, "BEFORE DAWN", "AI-SACHI", "82-94"),
    (6, "fish", "The Kaleidoscope", "95-106"),
    (7, "GLORY -Kimi ga Iru Kara-", "Takako Uehara", "107-118"),
    (8, "Shining ray", "Janne da Arc", "119-132"),
    (9, "Free will", "Ruppina", "133-156"),
    (10, "FAITH", "Ruppina", "157-168"),
    (11, "A to Z", "ZZ", "169-181"),
    (12, "Tsuki to Taiyo", "Shela", "182-195"),
    (13, "Dreamship", "Aiko Ikuta", "196-206"),
    (14, "Mirai Kōkai", "Tackey & Tsubasa", "207-230"),
    (15, "Eternal Pose", "Asia Engineer", "231-245"),
    (16, "Dear friends", "TRIPLANE", "246-255"),
    (17, "Asu wa Kuru Kara", "TVXQ", "256-263"),
    (18, "Adventure World", "Delicatessen", "264-278"),
    (19, "Raise", "Chilli Beans.", "1071-1088"),
    (20, "Dear sunrise", "Maki Otsuki", "1089-1122"),
    (21, "The 1", "muque", "1123-1136, 1138"),
    (22, "Punks", "Chameleon Lime Whoopiepie", "1139-1155"),
    (23, "Sono Mirai", "時速36km", "1157-1162, 1164-1180"),
]

# Keywords that strongly indicate karaoke, cover, tribute, lofi, or unofficial re-upload
COVER_KARAOKE_TITLE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"カラオケ|karaoke|ガイドメロディ|歌っちゃ王", re.I), "Karaoke track"),
    (re.compile(r"原曲歌手|オリジナルアーティスト|originally\s+performed\s+by|made\s+famous\s+by", re.I), "Cover / karaoke referencing original artist"),
    (re.compile(r"セルフカバー|self[\s-]*cover", re.I), "Self-cover (not original release)"),
    (re.compile(r"\bcover\b|\btribute\b|\blofi\b|\blo-fi\b|\bpiano\s*(?:version|cover|box)?\b|\borchestra\b|\bmusic\s*box\b|オルゴール|\b8-?bit\b|\b16-?bit\b|\bemulation\b", re.I), "Cover / arrangement / lofi / instrumental version"),
    (re.compile(r"\bacoustic\s+version\b|\bepic\s+version\b", re.I), "Unofficial / alternate arrangement"),
]

COVER_ARTIST_KEYWORDS: tuple[str, ...] = (
    "歌っちゃ王",
    "karaoke",
    "lofi",
    "lo-fi",
    "piano",
    "orchestra",
    "music box",
    "8-bit",
    "arcade player",
    "tribute",
    "cover",
    "relaxu",
    "toastaku",
    "save up for a rainy day",
    "zzang karaoke",
)

ALTERNATE_VERSION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"red\s+ocean\s+ver", re.I), "Alternate version (RED OCEAN Ver.)"),
    (re.compile(r"tomorrow\s+version", re.I), "Re-recorded version (TOMORROW Version)"),
    (re.compile(r"9人の麦わらの一味", re.I), "9 Straw Hat Pirates version (OP 7 originally used 7 Straw Hat Pirates version)"),
    (re.compile(r"one\s+piece\s+edition|tv\s+size|tv\s+edit", re.I), "TV / Anime Edition"),
]


def normalize_text(text: str) -> str:
    """Normalize text for cross-script/punctuation comparison."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", html.unescape(text)).lower()
    # Replace macrons / accents for romaji matching (e.g., Shōchi -> shochi, Kōkai -> kokai)
    decomposed = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[\s\-_!！?？.,'\"`~〜～・:：;；/／()（）\[\]【】「」『』&＆+＋]+", " ", text)
    return text.strip()


def strip_title_annotations(title: str) -> str:
    """Strip parenthetical / bracketed anime annotations for cleaner matching."""
    cleaned = unicodedata.normalize("NFKC", html.unescape(title))
    cleaned = re.sub(r"[\(\[（【].*?[\)\]）】]", " ", cleaned)
    cleaned = re.sub(r"\s*[-–—]\s*(?:from\s+anime|one\s+piece|red\s+ocean|tomorrow\s+version|9人の麦わら|tv\s+size).*$", "", cleaned, flags=re.I)
    return cleaned.strip()


def _similarity(a: str, b: str) -> float:
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
        if len(shorter) >= 3:
            return max(0.86, len(shorter) / len(longer))
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _best_title_similarity(track_title: str, ref: ReferenceSong) -> float:
    variants = [track_title, strip_title_annotations(track_title)]
    # Also split on " - " in case title is "ルミナス - Luminous"
    if " - " in track_title:
        variants.extend(part.strip() for part in track_title.split(" - ") if part.strip())
    best = 0.0
    for tv in variants:
        if not tv:
            continue
        for rt in ref.all_titles():
            best = max(best, _similarity(tv, rt))
            best = max(best, _similarity(tv, strip_title_annotations(rt)))
    return best


def _artist_matches(track_artists: list[str], expected_artists: list[str]) -> tuple[bool, float]:
    """Check whether any artist in track_artists matches the expected official artist(s)."""
    joined_track = " ".join(track_artists)
    best = 0.0
    for exp in expected_artists:
        n_exp = normalize_text(exp)
        if not n_exp:
            continue
        for ta in track_artists:
            score = _similarity(ta, exp)
            best = max(best, score)
            # Check token overlap for multi-word / Japanese band names
            n_ta = normalize_text(ta)
            if n_ta and n_exp and (n_ta == n_exp or n_ta in n_exp or n_exp in n_ta):
                if min(len(n_ta), len(n_exp)) >= 2:
                    best = max(best, 0.92)
        # Also compare against combined artist string (e.g. "Hiroshi Kitadani and Kishidan")
        n_joined = normalize_text(joined_track)
        if n_exp in n_joined and len(n_exp) >= 3:
            best = max(best, 0.9)

    # Special guard: if expected artist is a collaboration like "Hiroshi Kitadani and Kishidan"
    # and track artist is ONLY one unrelated cover artist, normal score is low (<0.6).
    # What about "5050" vs "Hiroshi Kitadani" (OP 9 Jungle P)? Score is ~0.0 -> properly rejected!
    return best >= 0.78, best


def _clean_wikitext(cell: str) -> str:
    """Convert MediaWiki cell markup into plain text."""
    text = cell.replace("&nbsp;", " ")
    # {{W|Page|Display}} or {{W|lang=ja|JP|Display}} or {{W|Name}}
    def _repl_w(match: re.Match[str]) -> str:
        parts = [p.strip() for p in match.group(1).split("|") if not p.strip().startswith("lang=")]
        return parts[-1] if parts else ""

    text = re.sub(r"\{\{[Ww]\|([^}]+)\}\}", _repl_w, text)
    # [[Link|Display]] -> Display, [[Link]] -> Link
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)
    # [https://... Label] -> Label
    text = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", text)
    # Remove HTML tags & refs
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.strip().strip('"').strip("'").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _parse_fandom_table(wikitext: str, kind: str) -> list[ReferenceSong]:
    """Parse One Piece Wiki TV Episodes table (handles rowspan on Original Artist column)."""
    table_match = re.search(r"\{\|.*?\|\}", wikitext, re.DOTALL)
    if not table_match:
        return []

    rows = re.split(r"\n\|-", table_match.group(0))
    results: list[ReferenceSong] = []
    # Track active rowspans by column index: col -> (value, remaining_rows)
    active_rowspans: dict[int, tuple[str, int]] = {}
    alias_map = ONE_PIECE_OPENING_ALIASES if kind == "opening" else ONE_PIECE_ENDING_ALIASES

    for raw_row in rows[1:]:
        lines = [ln.strip() for ln in raw_row.splitlines() if ln.strip()]
        if not lines or lines[0].startswith("!"):
            continue
        # Extract cells starting with "|"
        raw_cells: list[str] = []
        for ln in lines:
            if ln.startswith("|}"):
                break
            if ln.startswith("|"):
                # Split inline "||" cells if present
                content = ln[1:]
                for part in content.split("||"):
                    raw_cells.append(part.strip())

        if not raw_cells:
            continue

        # Expand rowspans into positional columns
        cols: list[str] = []
        cell_idx = 0
        col_idx = 0
        while col_idx < 6:
            if col_idx in active_rowspans:
                val, rem = active_rowspans[col_idx]
                cols.append(val)
                if rem <= 1:
                    del active_rowspans[col_idx]
                else:
                    active_rowspans[col_idx] = (val, rem - 1)
                col_idx += 1
                continue

            if cell_idx >= len(raw_cells):
                break
            cell = raw_cells[cell_idx]
            cell_idx += 1

            rowspan = 1
            # Check for attributes before single "|" (not inside [[...]] or {{...}})
            attr_match = re.match(r'^([^|\[{]*(?:rowspan|colspan|style|align)[^|\[{]*)\|(.*)$', cell, re.I)
            if attr_match:
                attrs, cell_body = attr_match.group(1), attr_match.group(2)
                rs_match = re.search(r'rowspan\s*=\s*["\']?(\d+)', attrs, re.I)
                if rs_match:
                    rowspan = int(rs_match.group(1))
                cell = cell_body

            cleaned = _clean_wikitext(cell)
            cols.append(cleaned)
            if rowspan > 1:
                active_rowspans[col_idx] = (cleaned, rowspan - 1)
            col_idx += 1

        if len(cols) < 3:
            continue
        num_match = re.search(r"(\d+)", cols[0])
        if not num_match:
            continue
        num = int(num_match.group(1))
        title = cols[1]
        artist = cols[2]
        if artist == "速36km":
            artist = "時速36km (Jisoku 36km)"
        episodes = cols[4] if len(cols) > 4 else ""
        aliases = alias_map.get(num, {})

        results.append(
            ReferenceSong(
                kind=kind,
                number=num,
                title=title,
                artist=artist,
                episodes=episodes,
                title_aliases=list(aliases.get("titles", [])),
                artist_aliases=list(aliases.get("artists", [])),
                source="onepiece_wiki",
            )
        )

    return results


def _build_builtin_references(kind: str) -> list[ReferenceSong]:
    raw = BUILTIN_ONE_PIECE_OPENINGS if kind == "opening" else BUILTIN_ONE_PIECE_ENDINGS
    alias_map = ONE_PIECE_OPENING_ALIASES if kind == "opening" else ONE_PIECE_ENDING_ALIASES
    refs: list[ReferenceSong] = []
    for num, title, artist, episodes in raw:
        aliases = alias_map.get(num, {})
        refs.append(
            ReferenceSong(
                kind=kind,
                number=num,
                title=title,
                artist=artist,
                episodes=episodes,
                title_aliases=list(aliases.get("titles", [])),
                artist_aliases=list(aliases.get("artists", [])),
                source="builtin",
            )
        )
    return refs


class OnePieceReferenceProvider:
    """Fetches authoritative One Piece openings & endings from One Piece Wiki with built-in fallback."""

    def __init__(self, timeout: int = 12, use_network: bool = True):
        self.timeout = timeout
        self.use_network = use_network
        self._cache: dict[str, list[ReferenceSong]] = {}

    def get_openings(self) -> list[ReferenceSong]:
        return self._get_list("opening", section=3)

    def get_endings(self) -> list[ReferenceSong]:
        return self._get_list("ending", section=8)

    def get_movie_themes(self) -> list[ReferenceSong]:
        if "movie" in self._cache:
            return self._cache["movie"]
        results: list[ReferenceSong] = []
        seen: set[tuple[str, str]] = set()
        if self.use_network:
            for sec in (6, 11):
                try:
                    url = (
                        "https://onepiece.fandom.com/api.php"
                        f"?action=parse&page=One_Piece_Music&section={sec}&prop=wikitext&format=json"
                    )
                    resp = requests.get(
                        url,
                        headers={"User-Agent": WIKI_USER_AGENT},
                        timeout=self.timeout,
                    )
                    if resp.status_code == 200:
                        wt = resp.json().get("parse", {}).get("wikitext", {}).get("*", "")
                        for item in _parse_fandom_table(wt, "movie"):
                            key = (normalize_text(item.title), normalize_text(item.artist))
                            if key not in seen:
                                seen.add(key)
                                results.append(item)
                except Exception:
                    pass
        self._cache["movie"] = results
        return results

    def _get_list(self, kind: str, section: int) -> list[ReferenceSong]:
        if kind in self._cache:
            return self._cache[kind]

        parsed: list[ReferenceSong] = []
        if self.use_network:
            try:
                url = (
                    "https://onepiece.fandom.com/api.php"
                    f"?action=parse&page=One_Piece_Music&section={section}&prop=wikitext&format=json"
                )
                resp = requests.get(
                    url,
                    headers={"User-Agent": WIKI_USER_AGENT},
                    timeout=self.timeout,
                )
                if resp.status_code == 200:
                    wikitext = resp.json().get("parse", {}).get("wikitext", {}).get("*", "")
                    parsed = _parse_fandom_table(wikitext, kind)
            except Exception:
                parsed = []

        # Require a sane count before trusting parsed wiki output; else fall back
        min_expected = 25 if kind == "opening" else 20
        if len(parsed) >= min_expected:
            self._cache[kind] = parsed
            return parsed

        fallback = _build_builtin_references(kind)
        self._cache[kind] = fallback
        return fallback


def detect_reference_mode(playlist_name: str, requested_mode: str = "auto") -> str:
    """Resolve reference checking mode from playlist title or explicit override."""
    mode = (requested_mode or "auto").strip().lower()
    if mode in ("one-piece-openings", "one-piece-endings", "one-piece", "none"):
        return mode

    norm = normalize_text(playlist_name)
    if "one piece" in norm or "ワンピース" in playlist_name:
        has_op = bool(re.search(r"\bopenings?\b|\bops?\b|オープニング", norm))
        has_ed = bool(re.search(r"\bendings?\b|\beds?\b|エンディング", norm))
        if has_op and not has_ed:
            return "one-piece-openings"
        if has_ed and not has_op:
            return "one-piece-endings"
        return "one-piece"
    return "none"


def detect_cover_or_karaoke(
    title: str,
    artists: list[str],
    allow_instrumental: bool = False,
    album_name: str = "",
) -> list[str]:
    """Detect karaoke, covers, lofi, piano, music-box, or tribute tracks across any playlist."""
    reasons: list[str] = []
    for pattern, reason in COVER_KARAOKE_TITLE_PATTERNS:
        if pattern.search(title):
            if reason not in reasons:
                reasons.append(reason)

    if not allow_instrumental and re.search(r"\binstrumental\b|インストゥルメンタル", title, re.I):
        reasons.append("Instrumental track in main playlist")

    if album_name:
        if re.search(r"カバー|カラオケ|\bcovers?\b|\bkaraoke\b|\btribute\b|\blofi\b|\blo-fi\b|\bpiano\s*(?:cover|box)\b|\bmusic\s*box\b", album_name, re.I):
            msg = f"Cover/karaoke album detected ('{album_name}')"
            if msg not in reasons:
                reasons.append(msg)

    artist_blob = " ".join(artists).lower()
    for kw in COVER_ARTIST_KEYWORDS:
        if kw in artist_blob:
            msg = f"Cover/karaoke artist detected ('{', '.join(artists)}')"
            if msg not in reasons:
                reasons.append(msg)
            break

    return reasons


def detect_alternate_version_notes(title: str) -> list[str]:
    notes: list[str] = []
    for pattern, note in ALTERNATE_VERSION_PATTERNS:
        if pattern.search(title):
            notes.append(note)
    return notes


class ReferenceChecker:
    """Crosschecks Spotify playlist tracks against authoritative lists and detects covers/copies."""

    def __init__(self, use_network: bool = True, strict_versions: bool = False):
        self.one_piece = OnePieceReferenceProvider(use_network=use_network)
        self.strict_versions = strict_versions

    def load_references(self, playlist_name: str, mode: str = "auto") -> tuple[str, list[ReferenceSong]]:
        resolved = detect_reference_mode(playlist_name, mode)
        if resolved == "one-piece-openings":
            return resolved, self.one_piece.get_openings()
        if resolved == "one-piece-endings":
            return resolved, self.one_piece.get_endings()
        if resolved == "one-piece":
            return resolved, [*self.one_piece.get_openings(), *self.one_piece.get_endings()]
        return "none", []

    def crosscheck_playlist(
        self,
        playlist_name: str,
        tracks: list[Any],
        reference_mode: str = "auto",
    ) -> tuple[str, list[VerificationResult], list[ReferenceSong]]:
        """Verify every track and return (resolved_mode, verification_results, missing_references)."""
        resolved_mode, references = self.load_references(playlist_name, reference_mode)

        # Assign each track to the best matching ReferenceSong (if references are active)
        matched_refs: list[tuple[ReferenceSong | None, float]] = [(None, 0.0) for _ in tracks]
        used_ref_keys: set[tuple[str, int]] = set()

        if references:
            # First pass: score every (track, reference) pair, giving a small tie-breaker
            # bonus when playlist position aligns with reference number (crucial for multiple
            # "We Are!" openings at OP #1, #7, #10!).
            candidate_pairs: list[tuple[float, int, ReferenceSong]] = []
            for idx, track in enumerate(tracks):
                pos = getattr(track, "position", idx + 1)
                for ref in references:
                    t_sim = _best_title_similarity(track.title, ref)
                    if t_sim < 0.62:
                        continue
                    artist_ok, a_sim = _artist_matches(track.artists, ref.all_artists())
                    # Positional alignment bonus (strongest when exact or within 1-2 slots)
                    pos_diff = abs(pos - ref.number)
                    pos_bonus = 0.08 if pos_diff == 0 else (0.04 if pos_diff <= 2 else 0.0)
                    # Check explicit OP/ED/Ending number in title e.g. "(Ending 9)" or "(第284-325話 OP)"
                    explicit_num_bonus = 0.0
                    ep_match = re.search(r"(?:ending|opening|op|ed)\s*#?\s*(\d+)", track.title, re.I)
                    if ep_match and int(ep_match.group(1)) == ref.number:
                        explicit_num_bonus = 0.15
                    if ref.episodes:
                        first_ep = ref.episodes.split("-")[0].split(",")[0].strip()
                        if first_ep and f"第{first_ep}" in track.title:
                            explicit_num_bonus = max(explicit_num_bonus, 0.15)

                    # Artist bonus helps distinguish OP #1 vs OP #7 vs OP #10 when artist matches
                    artist_bonus = 0.12 if artist_ok else 0.0
                    total = t_sim + artist_bonus + pos_bonus + explicit_num_bonus
                    candidate_pairs.append((total, idx, ref))

            # Greedy best-first assignment ensuring 1-to-1 reference slot assignment
            candidate_pairs.sort(key=lambda item: item[0], reverse=True)
            assigned_tracks: set[int] = set()
            for score, t_idx, ref in candidate_pairs:
                ref_key = (ref.kind, ref.number)
                if t_idx in assigned_tracks or ref_key in used_ref_keys:
                    continue
                assigned_tracks.add(t_idx)
                used_ref_keys.add(ref_key)
                matched_refs[t_idx] = (ref, min(1.0, score))

        results: list[VerificationResult] = []
        for idx, track in enumerate(tracks):
            ref, conf = matched_refs[idx]
            flags: list[str] = []
            warnings: list[str] = []

            cover_reasons = detect_cover_or_karaoke(
                track.title,
                track.artists,
                album_name=getattr(track, "album_name", "") or "",
            )
            flags.extend(cover_reasons)
            is_cover = len(cover_reasons) > 0

            version_notes = detect_alternate_version_notes(track.title)
            if self.strict_versions:
                flags.extend(version_notes)
            else:
                warnings.extend(version_notes)

            is_original_artist = not is_cover

            if ref is not None:
                artist_ok, _ = _artist_matches(track.artists, ref.all_artists())
                if not artist_ok:
                    is_original_artist = False
                    got = ", ".join(track.artists) or "Unknown"
                    flags.append(
                        f"Artist mismatch for {ref.label} '{ref.title}': "
                        f"expected '{ref.artist}', got '{got}'"
                    )
                # Special check for OP 10 ("We Are! (Remix)" by TVXQ!) if playlist contains
                # a cover labeled "(第1-47話 OP)" by a different artist
                if ref.kind == "opening" and ref.number == 10 and "1-47" in track.title:
                    flags.append("Track title references OP #1 (Episodes 1-47) instead of OP #10 (TVXQ! Remix)")
            elif references:
                # Reference list is active, but this track didn't match any official entry
                warnings.append("Track did not match any entry in the official One Piece theme list")

            # Also flag tracks whose title embeds another artist's name / "From anime" with suspicious cover markers
            if re.search(r'\bfrom\s+(?:anime\s+)?"?one\s+piece"?', track.title, re.I) and not is_original_artist:
                if not any("cover" in f.lower() or "mismatch" in f.lower() for f in flags):
                    flags.append("Unofficial anime re-recording / cover title pattern")

            if flags:
                status = "flagged"
                enabled = False
            elif warnings:
                status = "warning"
                enabled = True
            else:
                status = "verified"
                enabled = True

            results.append(
                VerificationResult(
                    status=status,
                    is_original_artist=is_original_artist,
                    is_cover_or_karaoke=is_cover,
                    enabled_by_default=enabled,
                    flags=flags,
                    warnings=warnings,
                    matched_reference=ref,
                    confidence=conf,
                )
            )

        missing_refs = [
            ref for ref in references if (ref.kind, ref.number) not in used_ref_keys
        ]
        return resolved_mode, results, missing_refs
