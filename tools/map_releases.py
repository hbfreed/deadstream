#!/usr/bin/env python3
"""
Map official Grateful Dead releases onto show dates for the Time Machine.

Runs on a laptop, not on the Time Machine. For each release folder (Dick's Picks, Dave's Picks, ...):

  1. read the tracks (tags, falling back to file names) and the dates the release might cover
     (folder name, file names, tags, text files in the folder),
  2. align the track titles with archive.org setlists for those dates, so each track gets a date,
  3. decide, per date, how much of the show the release covers,
  4. write folders that the Time Machine's LocalArchive reads as tapes:

       <out>/GratefulDead/official/<date> <venue> [DP07]/            that night's tracks, in show order
       <out>/GratefulDead/official/<date> <venue> [DP07 complete]/   the whole release, in album order
       <out>/.release_audio/<release>/...                            the audio, FLAC converted to Ogg Vorbis

     Each tape folder holds symlinks to the audio and a metadata.json (tracks, sets, venue, and a "release"
     entry that the Time Machine uses to order tapes and draw the official/partial marker).

Low-confidence assignments are listed in <out>/report.txt. Fix them in an overrides file (TOML) and run again:

    ["Dave's Picks Vol. 06 - 1970-02-02 & 1969-12-20 FLAC [16]"]   # the release folder name
    dates = ["1970-02-02", "1969-12-20"]     # the candidate dates, replacing the ones found automatically
    tracks = { 23 = "1969-12-20" }           # track number (in release order, from 1) -> date
    skip = false                             # true leaves the release out

Usage:
    python tools/map_releases.py --out /path/to/timemachine-releases "/path/to/Music/Grateful Dead"
    rsync -a --delete /path/to/timemachine-releases/.release_audio/ deadhead@timemachine.local:archive/.release_audio/
    rsync -a --delete /path/to/timemachine-releases/GratefulDead/official/ deadhead@timemachine.local:archive/GratefulDead/official/
"""
import argparse
import concurrent.futures
import datetime
import difflib
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tomllib
import unicodedata

logger = logging.getLogger("map_releases")

AUDIO_EXTENSIONS = (".flac", ".mp3", ".m4a", ".ogg", ".shn", ".wav")
LOSSLESS_EXTENSIONS = (".flac", ".shn", ".wav")
TEXT_EXTENSIONS = (".txt", ".nfo", ".cue", ".md5", ".ffp", ".log")
SKIP_DIRS = re.compile(r"^(scans?|artwork|covers?|images?|art)$", re.IGNORECASE)
DISC_MARK = re.compile(r"(?:^|[\s(\[_-])(cd|dis[ck])\s*[-_]?\s*(\d+)\s*[)\]]?\s*$", re.IGNORECASE)
COLLECTION = "GratefulDead"
# A release plays first on a date (before the archive.org tapes) if it holds this many minutes of that night, this
# much of the setlist, or a whole set. Less than that is usually filler from another night on a bonus disc.
PRIMARY_MINUTES = 60
PRIMARY_COVERAGE = 0.5

# ---------------------------------------------------------------------------------------------------------
# Releases and their tracks
# ---------------------------------------------------------------------------------------------------------


class Track:
    def __init__(self, path, tags, duration):
        self.path = path
        self.tags = {k.lower(): v for k, v in tags.items()}
        self.duration = duration
        self.tag_position = self._tag_position()
        self.file_position = self._file_position()
        self.disc, self.number = self.tag_position
        self.title = self._title()

    def _tag_position(self):
        disc = _int((self.tags.get("disc") or self.tags.get("discnumber") or "").split("/")[0])
        number = _int((self.tags.get("track") or self.tags.get("tracknumber") or "").split("/")[0])
        file_disc, file_number = self._file_position()
        return (disc if disc is not None else file_disc, number if number is not None else file_number)

    def _file_position(self):
        name = os.path.basename(self.path)
        disc = number = None
        m = re.search(r"d(\d+)[_ -]?t?(\d+)", name, re.IGNORECASE)  # gd740909d1_01_..., gd73-12-19d2t03
        if m:
            disc, number = int(m.group(1)), int(m.group(2))
        m = re.match(r"^(\d)(\d\d)[ ._-]", name)  # 207 Grateful Dead - The Eleven.flac
        if m and number is None:
            disc, number = int(m.group(1)), int(m.group(2))
        m = re.match(r"^(\d+)[-.](\d+)[ ._-]", name)  # 1-07 Title.flac
        if m and number is None:
            disc, number = int(m.group(1)), int(m.group(2))
        m = re.match(r"^(\d+)[ ._-]", name)  # 07 Title.flac, 25. Loser.mp3
        if m and number is None:
            number = int(m.group(1))
        if disc is None:
            m = DISC_MARK.search(os.path.basename(os.path.dirname(self.path)))
            disc = int(m.group(2)) if m else 1
        return disc, number if number is not None else 0

    def _title(self):
        title = self.tags.get("title")
        if not title:
            title = os.path.splitext(os.path.basename(self.path))[0]
            title = re.sub(r"^gd\d{2,4}[-.]?\d{2}[-.]?\d{2}d\d+[_ -]?t?\d+[_ -]*", "", title, flags=re.IGNORECASE)
            title = re.sub(r"^\d+[-.]?\d*[ ._-]+", "", title)
            title = re.sub(r"^grateful dead\s*-\s*", "", title, flags=re.IGNORECASE)
            title = title.replace("__", " & ").replace("_", " ")
        return title.strip()

    @property
    def lossless(self):
        return self.path.lower().endswith(LOSSLESS_EXTENSIONS)


def _int(s):
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return None


class Release:
    def __init__(self, path, tracks, texts, name=None, context=""):
        self.path = path
        self.name = name or os.path.basename(path)
        self.context = context  # names of the folders above, for the title
        tag_keys = [t.tag_position for t in tracks]
        file_keys = [t.file_position for t in tracks]
        if len(set(tag_keys)) < len(tag_keys) and len(set(file_keys)) == len(file_keys):
            for t in tracks:  # tags restart numbering on each disc without saying which disc: use the file names
                t.disc, t.number = t.file_position
        self.tracks = sorted(tracks, key=lambda t: (t.disc, t.number, t.path))
        self.texts = texts  # contents of text files in the release folder
        self.title, self.short = release_title(self.name, self.tracks, context)
        self.slug = slugify(f"{self.short} {self.name}")[:120]

    def signature(self):
        """Identifies the same release copied to two places"""
        h = hashlib.sha1()
        for t in self.tracks:
            h.update(f"{os.path.basename(t.path)}:{os.path.getsize(t.path)}\n".encode())
        return h.hexdigest()


def release_title(name, tracks, context=""):
    """A readable title and a short code (DP07, DaP06, ...) for a release"""
    series = [
        (r"dick'?s picks", "Dick's Picks", "DP"),
        (r"dave'?s picks", "Dave's Picks", "DaP"),
        (r"download series", "Download Series", "DS"),
        (r"road trips", "Road Trips", "RT"),
        (r"sunshine daydream", "Sunshine Daydream", "SD"),
    ]
    album = tracks[0].tags.get("album", "") if tracks else ""
    for text in [name, album, context]:
        for pattern, title, code in series:
            if re.search(pattern, text, re.IGNORECASE):
                m = re.search(pattern + r"\W*(?:vol(?:ume)?\.?)?\s*(\d+)", text, re.IGNORECASE)
                if m:
                    return f"{title} Vol. {int(m.group(1))}", f"{code}{int(m.group(1)):02d}"
                if re.search("bonus", text, re.IGNORECASE):
                    return f"{title} Bonus", f"{code}-bonus"
                return title, code
    return name, slugify(name)[:12]


def slugify(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9.,&' -]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def probe(path, cache):
    st = os.stat(path)
    key = f"{path}:{st.st_size}:{st.st_mtime_ns}"
    if key not in cache:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_entries", "format=duration:format_tags", path],
            capture_output=True,
            text=True,
        ).stdout
        fmt = json.loads(out or "{}").get("format", {})
        cache[key] = {"tags": fmt.get("tags", {}), "duration": float(fmt.get("duration", 0) or 0)}
    return cache[key]


def disc_base(name):
    """The release name of a disc folder: "Dave's Picks Vol. 10 (Disc 1)" -> "Dave's Picks Vol. 10", "CD2" -> "" """
    m = DISC_MARK.search(name)
    return name[:m.start()].strip(" -_([") if m else None


def find_releases(roots, cache):
    """Folders holding audio. Disc folders ("CD1", "Disc 2", or siblings like "Vol. 10 (Disc 1)" and
    "Vol. 10 (Disc 2)") are joined into one release"""
    units = []  # (path, name, folders, context)
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not SKIP_DIRS.match(d))
            context = os.path.relpath(dirpath, os.path.dirname(root))
            groups = {}
            for d in dirnames:
                base = disc_base(d)
                if base is not None:
                    groups.setdefault(base, []).append(d)
            joined = set()
            own = [dirpath] if any(f.lower().endswith(AUDIO_EXTENSIONS) for f in filenames) else []
            for base, ds in groups.items():
                if base == "" or len(ds) > 1:  # "CD1"/"CD2" belong to this folder; named siblings form a release
                    joined.update(ds)
                    folders = [os.path.join(dirpath, d) for d in ds]
                    if base == "":
                        own += folders
                    else:
                        units.append((os.path.join(dirpath, base), base, folders, context))
            if own:
                units.append((dirpath, os.path.basename(dirpath), own, os.path.dirname(context)))
            dirnames[:] = [d for d in dirnames if d not in joined]
    releases = []
    for path, name, folders, context in units:
        audio, text_files = [], []
        for folder in folders:
            for f in sorted(os.listdir(folder)):
                p = os.path.join(folder, f)
                if f.lower().endswith(AUDIO_EXTENSIONS):
                    audio.append(p)
                elif f.lower().endswith(TEXT_EXTENSIONS):
                    text_files.append(p)
        tracks = []
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            for p, info in zip(audio, pool.map(lambda p: probe(p, cache), audio)):
                tracks.append(Track(p, info["tags"], info["duration"]))
        texts = []
        for p in text_files:
            try:
                with open(p, "r", errors="replace") as f:
                    texts.append(f.read(200_000))
            except OSError:
                pass
        releases.append(Release(path, tracks, texts, name=name, context=context))
    return releases


# ---------------------------------------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------------------------------------

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _date(y, m, d):
    y = int(y)
    if y < 100:
        y += 1900 if y >= 60 else 2000
    try:
        return datetime.date(y, int(m), int(d)).isoformat()
    except ValueError:
        return None


def dates_in(text):
    """Show dates mentioned in text: 1973-12-19, 1970-02-13&14, 1974-09-09-11, 05-03-77, gd740909, December 19, 1973"""
    found = []
    # YYYY-MM-DD with optional extra days: 1970-02-13&14, 1974-09-09-11, 1971-08-6&07, 1973 -11-17
    for m in re.finditer(r"(?<!\d)(19[6-9]\d)\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})((?:\s*(?:&|-|,|and)\s*\d{1,2}(?!\d))*)", text):
        y, mo, d, extra = m.groups()
        found.append(_date(y, mo, d))
        extra_days = [int(x) for x in re.findall(r"\d{1,2}", extra or "")]
        if extra and "-" in extra and len(extra_days) == 1:  # a range: 1974-09-09-11
            found += [_date(y, mo, x) for x in range(int(d) + 1, extra_days[0] + 1)]
        else:
            found += [_date(y, mo, x) for x in extra_days]
    # MM-DD-YY or MM/DD/YYYY: 05-03-77, 12/19/73
    for m in re.finditer(r"(?<![\d-])(\d{1,2})[-/.](\d{1,2})[-/.]((?:19)?[6-9]\d)(?![\d-])", text):
        found.append(_date(m.group(3), m.group(1), m.group(2)))
    # gd740909, gd1974-09-09, gd74-09-09
    for m in re.finditer(r"gd(?:19)?([6-9]\d)[-.]?(\d{2})[-.]?(\d{2})", text, re.IGNORECASE):
        found.append(_date(*m.groups()))
    # December 19, 1973 / Dec 19 1973
    for m in re.finditer(r"\b([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(19[6-9]\d)\b", text):
        mo = MONTHS.get(m.group(1).lower())
        if mo:
            found.append(_date(m.group(3), mo, m.group(2)))
    # 111771, 082572, 4273 (no separators, in file and folder names): every way to read it, but only when
    # nothing else in the text is a date, and not years like 1993
    for m in re.finditer(r"(?<![\d])(\d{4,6})(?![\d])", text if not any(found) else ""):
        digits = m.group(1)
        if re.match(r"^(19|20)\d\d$", digits):
            continue
        yy = digits[-2:]
        md = digits[:-2]
        for split in range(1, len(md)):
            found.append(_date(yy, md[:split], md[split:]))
    return [d for d in dict.fromkeys(found) if d]


def candidate_dates(release, show_dates):
    """Dates the release may cover, best evidence first, limited to dates with a known show"""
    names = [release.name] + [os.path.basename(t.path) for t in release.tracks]
    tags = [" ".join(str(v) for v in t.tags.values()) for t in release.tracks]
    found = []
    for text in names + tags:
        found += dates_in(text)
    near = [datetime.date.fromisoformat(d) for d in found]
    for text in release.texts:  # info files also hold rip and release dates: keep the ones near the show dates
        for d in dates_in(text):
            if not near or any(abs((datetime.date.fromisoformat(d) - n).days) <= 3 * 366 for n in near):
                found.append(d)
    counts = {}
    for d in found:
        counts[d] = counts.get(d, 0) + 1
    from_name = dates_in(release.name)
    for d in listed_track_dates(release) or []:
        if d:
            counts[d] = counts.get(d, 0) + 5
    ranked = sorted(counts, key=lambda d: (d not in from_name, -counts[d]))
    return [d for d in ranked if d in show_dates][:8]


def ranged_track_dates(release):
    """Dates for each track from notes like "Tracks 101-204: Recorded live at Fox Theatre, St. Louis, MO, 2/2/70",
    where 101 is disc 1 track 1 (or a plain track number), or "Disc 3: ... 12/20/69". None if there are none."""
    out = [None] * len(release.tracks)
    codes = [t.disc * 100 + t.number for t in release.tracks]
    found = False
    for text in release.texts:
        for line in text.splitlines():
            line_dates = dates_in(line)
            if len(line_dates) != 1:
                continue
            m = re.search(r"\btracks?\s+(\d+)\s*(?:-|–|to|thru|through)\s*(\d+)", line, re.IGNORECASE)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                for i, code in enumerate(codes):
                    if (a <= code <= b) if a >= 100 else (a <= i + 1 <= b):
                        out[i] = line_dates[0]
                        found = True
                continue
            m = re.search(r"\bdis[ck]s?\s+(\d+)(?:\s*(?:-|–|&|and)\s*(\d+))?\s*:", line, re.IGNORECASE)
            if m:
                a = int(m.group(1))
                b = int(m.group(2)) if m.group(2) else a
                for i, t in enumerate(release.tracks):
                    if a <= t.disc <= b:
                        out[i] = line_dates[0]
                        found = True
    return out if found and sum(x is not None for x in out) >= 0.7 * len(out) else None


def listed_track_dates(release):
    """Dates for each track from a track listing in the release's text files, or None.

    Info files often list the tracks under headings like "Disc One San Diego 8/7/71" or "Chicago 8/24/71":
    a date on a line that isn't a track sets the date of the tracks after it.
    """
    ranged = ranged_track_dates(release)
    if ranged:
        return ranged
    best = None
    for text in release.texts:
        entries, date = [], None
        for line in text.splitlines():
            m = re.match(r"^\s*(?:\d+-)?(\d{1,2})[.)]?\s+(.+?)\s*$", line)
            line_dates = dates_in(line)
            if m and not (line_dates and len(m.group(2)) < 12):
                title = re.sub(r"\s+\d{1,2}:\d{2}.*$", "", m.group(2))  # drop "4:02 (Lewis, ...)"
                title = re.sub(r"\s*\(.*$", "", title)
                if date:
                    entries.append((norm(title), date))
            elif line_dates:
                date = line_dates[0]
        if len(entries) >= 0.8 * len(release.tracks) and (best is None or len(entries) > len(best)):
            best = entries
    if not best:
        return None
    names = [norm(t.title) for t in release.tracks]
    listed = [title for title, _ in best]
    out = [None] * len(names)
    for block in difflib.SequenceMatcher(None, names, listed, autojunk=False).get_matching_blocks():
        for k in range(block.size):
            out[block.a + k] = best[block.b + k][1]
    for i in range(len(out)):  # fill gaps between tracks that agree
        if out[i] is None:
            before = next((out[k] for k in range(i - 1, -1, -1) if out[k]), None)
            after = next((out[k] for k in range(i + 1, len(out)) if out[k]), None)
            if before and before == after:
                out[i] = before
    return out if sum(x is not None for x in out) >= 0.7 * len(out) else None


# ---------------------------------------------------------------------------------------------------------
# Song titles
# ---------------------------------------------------------------------------------------------------------

ALIASES = {
    "gdtrfb": "going down the road feeling bad",
    "goin down the road feelin bad": "going down the road feeling bad",
    "goin down the road feeling bad": "going down the road feeling bad",
    "fotd": "friend of the devil",
    "eotw": "eyes of the world",
    "nfa": "not fade away",
    "u s blues": "us blues",
    "around and around": "around and around",
    "me and my uncle": "me and my uncle",
    "playin in the band": "playing in the band",
    "truckin": "truckin",
    "the other one": "the other one",
    "that s it for the other one": "the other one",
    "lovelight": "turn on your lovelight",
    "rhythm devils": "drums",
    "drumz": "drums",
    "half step": "mississippi half step uptown toodeloo",
    "mississippi half step": "mississippi half step uptown toodeloo",
    "morning dew": "morning dew",
    "walk me out in the morning dew": "morning dew",
    "weather report suite": "weather report suite",
    "let it grow": "weather report suite",
    "wharf rat": "wharf rat",
    "bid you goodnight": "and we bid you goodnight",
    "we bid you goodnight": "and we bid you goodnight",
    "johnny b goode": "johnny b goode",
    "sugar mag": "sugar magnolia",
    "sunshine daydream": "sugar magnolia",
    "stella": "stella blue",
    "scarlet": "scarlet begonias",
    "fire": "fire on the mountain",
    "uncle john s band": "uncle johns band",
}
FILLER = re.compile(r"^(tuning|crowd|intro|introduction|banter|stage announcements?|announcements?|band intro.*|set break|encore break|"
                    r"silence|applause|noodling|dead air|soundcheck|interview)$")
NOISE = re.compile(r"\b(live|remaster(ed)?|bonus|track|version|take \d+|filler|incomplete|partial|cut|fade(s)? in|fade(s)? out)\b")


def norm(title):
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\[[^\]]*\]", " ", t)  # [Live], [Bonus Track]
    t = re.sub(r"\((?:[^)]*\b(?:live|bonus|remaster|incomplete|cut|filler|\d{4}|\d+/\d+/\d+)\b[^)]*)\)", " ", t)
    t = re.sub(r"^grateful dead\s*-\s*", "", t)
    t = re.sub(r"^(?:d\d+\s*t\d+|\d{1,3})(?:[\s.)\-_]+|$)", "", t)  # "07 Scarlet Begonias", "d1t03 ..."
    if re.search(r"\bsound\s*check\b", t):
        return "soundcheck"
    t = t.replace("&", " and ").replace("->", " ").replace(">", " ").replace("'", " ")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = NOISE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return ALIASES.get(t, t)


def similarity(a, b):
    """How well a release title matches a setlist title, 0..1. Either may hold several songs"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    containment = len(small & big) / len(small) if small else 0
    if len(small) == 1 and len(big) > 3:
        containment *= 0.7  # one shared word in a long title is weak evidence
    return max(ratio, 0.95 * containment if len(small) >= 1 else 0)


def is_filler(title):
    return bool(FILLER.match(norm(title)))


# ---------------------------------------------------------------------------------------------------------
# Setlists from archive.org
# ---------------------------------------------------------------------------------------------------------


class Setlists:
    """Setlists (song titles in show order, with set numbers) for dates, from the best titled archive.org tapes"""

    def __init__(self, dbpath, cache):
        from timemachine import Archivary, config

        config.optd = config.default_options()
        self.archive = Archivary.GDArchive(dbpath=dbpath, collection_list=[COLLECTION])
        self.set_data = self.archive.set_data
        self.cache = cache.setdefault("setlists", {})

    @property
    def dates(self):
        """Dates with a known show: on archive.org, or in the set break data (some officially released shows
        have no archive.org tapes at all)"""
        if not hasattr(self, "_dates"):
            self._dates = set(self.archive.dates) | set(self.set_data.get_artist_set_dict(COLLECTION).keys())
        return self._dates

    def get(self, date):
        if date not in self.cache:
            self.cache[date] = self._fetch(date)
        return self.cache[date]

    def _fetch(self, date):
        songs = []
        if date in self.archive.tape_dates:
            for tape in self.archive.tape_dates[date][:4]:
                try:
                    tracks = [t.title for t in tape.tracks() if t.title not in ("Set Break", "Encore Break")]
                except Exception as e:  # network trouble, missing tape
                    logger.warning(f"setlist {date} {tape.identifier}: {e}")
                    continue
                titled = [t for t in tracks if not re.match(r"^(track|untitled|unknown)\b", t, re.IGNORECASE)]
                if len(titled) >= 0.7 * max(1, len(tracks)) and len(titled) > len(songs):
                    songs = titled
                if len(songs) >= 8:
                    break
        info = self.set_data.get_date(COLLECTION, date)
        venue = ["", ""]
        if info is not None:
            try:
                venue = [info.location[0], f"{info.location[1]}, {info.location[2]}"]
            except Exception:
                pass
        sets = self._sets(songs, info)
        return {"songs": songs, "sets": sets, "venue": venue}

    @staticmethod
    def _sets(songs, info):
        """Set number for each song: "1", "2", ... and "E" for encores, from the set break data"""
        long_breaks = [norm(x) for x in (info.longbreaks if info else [])]
        short_breaks = [norm(x) for x in (info.shortbreaks if info else [])]
        sets, current, encore = [], 1, False
        names = [norm(s) for s in songs]
        for i, name in enumerate(names):
            sets.append("E" if encore else str(current))
            last_of_its_title = name not in names[i + 1:]
            if last_of_its_title and any(similarity(name, b) > 0.85 for b in long_breaks):
                current += 1
            if last_of_its_title and any(similarity(name, b) > 0.85 for b in short_breaks):
                encore = True
        return sets


# ---------------------------------------------------------------------------------------------------------
# Aligning a release with setlists
# ---------------------------------------------------------------------------------------------------------

UNKNOWN = ("?", -1)  # a track from none of the candidate dates
MIN_SETLIST = 10  # fewer songs than this: archive.org only has part of the show


def setlist_complete(setlist):
    return len(setlist["songs"]) >= MIN_SETLIST


def align(titles, setlists, fixed=None, prior=None, strong=()):
    """Assign each release track to (date, setlist index), keeping each date's songs in show order.

    A Viterbi pass over the tracks. The state is the date and position in its setlist (or -1: a track of that
    date that isn't in the setlist, like tuning or an unlisted jam). Moving forward one song is free, skipping
    songs costs a little, going back or changing dates costs more. fixed maps track index -> date (overrides).
    prior is a date (or None) per track from a track listing; it adds weight to that date. A date without a
    setlist (archive.org has little of the show) can take any track, if it is one of the strong dates (named by the
    release, its track listing, or the overrides).
    Returns a list of (date, index, similarity) per track.
    """
    fixed = fixed or {}
    prior = prior or [None] * len(titles)
    names = [norm(t) for t in titles]
    states = [UNKNOWN]
    for date, sl in setlists.items():
        states.append((date, -1))
        states += [(date, j) for j in range(len(sl["songs"]))]
    song_names = {date: [norm(s) for s in sl["songs"]] for date, sl in setlists.items()}
    open_dates = {date for date, sl in setlists.items() if not setlist_complete(sl) and date in strong}

    def emission(i, state):
        date, j = state
        if i in fixed and date != fixed[i]:
            return -50.0
        if state == UNKNOWN:
            return -0.6
        bonus = 0.6 if prior[i] == date else 0.0
        if j < 0:
            if FILLER.match(names[i]):
                return 0.3 + bonus
            return (-0.1 if date in open_dates else -0.35) + bonus
        if FILLER.match(names[i]):
            return -1.0
        sim = similarity(names[i], song_names[date][j])
        return 2 * sim - 1 + bonus

    def transition(prev, state):
        (pd, pj), (d, j) = prev, state
        if state == UNKNOWN or prev == UNKNOWN:
            return -0.3
        if pd != d:
            return -1.0
        if j < 0 or pj < 0:
            return -0.05
        if j == pj + 1:
            return 0.0
        if j > pj:
            return -0.15 * min(j - pj - 1, 6)
        return -1.5  # back to an earlier song: a repeat or a resequenced release

    n = len(titles)
    score = [{s: emission(0, s) - (0.05 * s[1] if s[1] > 0 else 0) for s in states}]
    back = [{}]
    for i in range(1, n):
        score.append({})
        back.append({})
        for s in states:
            e = emission(i, s)
            best_prev, best = None, -1e9
            for p, ps in score[i - 1].items():
                v = ps + transition(p, s)
                if v > best:
                    best_prev, best = p, v
            score[i][s] = best + e
            back[i][s] = best_prev
    state = max(score[-1], key=score[-1].get)
    path = [state]
    for i in range(n - 1, 0, -1):
        state = back[i][state]
        path.append(state)
    path.reverse()
    result = []
    for i, (date, j) in enumerate(path):
        sim = similarity(names[i], song_names[date][j]) if j >= 0 else (1.0 if FILLER.match(names[i]) and date != "?" else 0.0)
        result.append((date, j, sim))
    return result


def smooth_fillers(assignment):
    """Give unassigned tuning/crowd tracks the date of their neighbours"""
    out = list(assignment)
    for i, (date, j, sim) in enumerate(out):
        if date == "?":
            neighbours = [out[k][0] for k in (i - 1, i + 1) if 0 <= k < len(out) and out[k][0] != "?"]
            if neighbours and len(set(neighbours)) == 1:
                out[i] = (neighbours[0], -1, sim)
    return out


def coverage(date, assignment, setlist):
    """How much of the show on date the release holds: fraction of setlist songs, and whether a whole set is in it"""
    songs = setlist["songs"]
    real = [k for k, s in enumerate(songs) if not FILLER.match(norm(s))]
    matched = {j for d, j, sim in assignment if d == date and j >= 0 and sim >= 0.6}
    fraction = len(matched & set(real)) / len(real) if real else 0.0
    sets = {}
    for k in real:
        sets.setdefault(setlist["sets"][k] if k < len(setlist["sets"]) else "1", []).append(k)
    full_sets = [name for name, ks in sets.items() if name != "E" and len(ks) >= 3 and all(k in matched for k in ks)]
    return fraction, full_sets


# ---------------------------------------------------------------------------------------------------------
# Mapping a release
# ---------------------------------------------------------------------------------------------------------


class Mapping:
    def __init__(self, release, dates, assignment, setlists, prior=None):
        self.release = release
        self.prior = prior  # per-track dates from a track listing
        self.dates = dates  # candidate dates
        self.assignment = assignment  # per track: (date, setlist index, similarity)
        self.setlists = setlists
        self.shows = {}  # date -> info about the release's part of that show
        for date in dict.fromkeys(d for d, _, _ in assignment if d != "?"):
            idx = [i for i, (d, _, _) in enumerate(assignment) if d == date]
            minutes = round(sum(release.tracks[i].duration for i in idx) / 60)
            if not setlist_complete(setlists[date]):  # archive.org has little or nothing of this show to compare
                self.shows[date] = {
                    "tracks": idx, "minutes": minutes, "coverage": None, "full_sets": [], "complete": None, "primary": True
                }
                continue
            fraction, full_sets = coverage(date, assignment, setlists[date])
            self.shows[date] = {
                "tracks": idx,
                "minutes": minutes,
                "coverage": round(fraction, 2),
                "full_sets": full_sets,
                "complete": fraction >= 0.9,
                "primary": minutes >= PRIMARY_MINUTES or fraction >= PRIMARY_COVERAGE or len(full_sets) > 0,
            }

    def problems(self):
        """Reasons to look at this mapping by hand"""
        out = []
        if not self.dates:
            out.append("no show dates found for this release")
        unknown = [i + 1 for i, (d, _, _) in enumerate(self.assignment) if d == "?"]
        if unknown:
            out.append(f"tracks {unknown} match none of the dates {self.dates}")
        weak = [i + 1 for i, (d, j, sim) in enumerate(self.assignment) if d != "?" and j >= 0 and sim < 0.6]
        if weak:
            out.append(f"tracks {weak} are weak title matches")
        unlisted = [
            i + 1 for i, (d, j, sim) in enumerate(self.assignment)
            if d != "?" and j < 0 and setlist_complete(self.setlists[d]) and not is_filler(self.release.tracks[i].title)
        ]
        if len(unlisted) > 2:
            out.append(f"tracks {unlisted} are not in the archive.org setlist of their date")
        for date, show in self.shows.items():
            if not setlist_complete(self.setlists[date]) and self.prior is None and len(self.shows) > 1:
                out.append(f"archive.org has little of {date}, and there is no track listing: check which tracks are from it")
        return out


def map_release(release, setlists, overrides):
    override = overrides.get(release.name, {})
    dates = override.get("dates") or candidate_dates(release, setlists.dates)
    fixed = {int(k) - 1: v for k, v in override.get("tracks", {}).items()}
    dates = list(dict.fromkeys(list(dates) + [d for d in fixed.values() if d not in dates]))
    sls = {d: setlists.get(d) for d in dates}
    titles = [t.title for t in release.tracks]
    prior = listed_track_dates(release)
    strong = set(dates_in(release.name)) | set(override.get("dates", [])) | set(fixed.values()) | set(prior or [])
    assignment = smooth_fillers(align(titles, sls, fixed, prior, strong)) if sls else [("?", -1, 0.0)] * len(titles)
    # Tracks that fit none of the dates may be filler from a nearby show: try the other shows of that tour.
    if not override.get("dates") and prior is None and dates and longest_run(assignment, "?") >= 3:
        nearby = nearby_dates(dates, setlists.dates)
        if nearby:
            sls2 = dict(sls)
            sls2.update({d: setlists.get(d) for d in nearby})
            assignment2 = smooth_fillers(align(titles, sls2, fixed, prior, strong))
            used = {d for d, _, _ in assignment2 if d != "?"}
            if sum(d == "?" for d, _, _ in assignment2) < sum(d == "?" for d, _, _ in assignment):
                sls = {d: sls2[d] for d in sls2 if d in used or d in dates}
                dates = dates + [d for d in nearby if d in used]
                assignment = assignment2
    return Mapping(release, dates, assignment, sls, prior)


def longest_run(assignment, date):
    best = run = 0
    for d, _, _ in assignment:
        run = run + 1 if d == date else 0
        best = max(best, run)
    return best


def nearby_dates(dates, show_dates, days=21):
    found = []
    for d in dates:
        day = datetime.date.fromisoformat(d)
        for k in range(-days, days + 1):
            x = (day + datetime.timedelta(days=k)).isoformat()
            if x in show_dates and x not in dates:
                found.append(x)
    return list(dict.fromkeys(found))[:12]


# ---------------------------------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------------------------------


def safe_name(s):
    return re.sub(r"\s+", " ", re.sub(r'[/\\:*?"<>|]+', " ", s)).strip()


def audio_name(i, track):
    ext = os.path.splitext(track.path)[1].lower()
    ext = ".ogg" if ext in LOSSLESS_EXTENSIONS else ext
    return safe_name(f"{i + 1:03d} {track.title}")[:100] + ext


class Writer:
    def __init__(self, out):
        self.out = out
        self.audio_root = os.path.join(out, ".release_audio")
        self.tape_root = os.path.join(out, COLLECTION, "official")
        self.jobs = {}  # destination -> source
        self.tape_dirs = set()

    def write(self, mapping):
        release = mapping.release
        audio_dir = os.path.join(self.audio_root, release.slug)
        files = []
        for i, track in enumerate(release.tracks):
            dst = os.path.join(audio_dir, audio_name(i, track))
            self.jobs[dst] = track.path
            files.append(dst)
        info = {"title": release.title, "short": release.short, "source": release.path, "dates": list(mapping.shows)}
        for date, show in mapping.shows.items():
            venue, location = (mapping.setlists[date]["venue"] + ["", ""])[:2]
            tracks = []
            current = "1"
            for k in show["tracks"]:
                _, j, _ = mapping.assignment[k]
                sets = mapping.setlists[date]["sets"]
                if 0 <= j < len(sets):
                    current = sets[j]
                tracks.append((files[k], release.tracks[k].title, current))
            role = {"role": "show", "date": date, **{x: show[x] for x in ("coverage", "complete", "primary", "full_sets")}}
            self._tape(f"{date} {venue} [{release.short}]", venue, location, tracks, {**info, **role})
        if len(mapping.shows) > 1:
            whole = [(files[i], t.title, "1") for i, t in enumerate(release.tracks)]
            for date, show in mapping.shows.items():
                venue, location = (mapping.setlists[date]["venue"] + ["", ""])[:2]
                role = {"role": "whole", "date": date, "complete": True, "primary": show["primary"], "coverage": 1.0}
                self._tape(f"{date} {venue} [{release.short} complete]", venue, location, whole, {**info, **role})

    def _tape(self, name, venue, location, tracks, release_info):
        tape_dir = os.path.join(self.tape_root, safe_name(name)[:150])
        self.tape_dirs.add(tape_dir)
        os.makedirs(tape_dir, exist_ok=True)
        wanted = set()
        meta_tracks = []
        for pos, (target, title, set_name) in enumerate(tracks, start=1):
            link = os.path.join(tape_dir, f"{pos:03d}{os.path.splitext(target)[1]}")
            wanted.add(os.path.basename(link))
            rel = os.path.relpath(target, tape_dir)
            if not (os.path.islink(link) and os.readlink(link) == rel):
                if os.path.lexists(link):
                    os.remove(link)
                os.symlink(rel, link)
            meta_tracks.append({"position": pos, "set": set_name, "path": os.path.basename(link), "title": title})
        for f in os.listdir(tape_dir):  # links left from an earlier run
            if f not in wanted and f != "metadata.json":
                os.remove(os.path.join(tape_dir, f))
        meta = {
            "data": {"venue": {"venue_name": venue or "Unknown", "venue_location": location or "Unknown"}, "tracks": meta_tracks},
            "release": release_info,
        }
        with open(os.path.join(tape_dir, "metadata.json"), "w") as f:
            json.dump(meta, f, indent=1)

    def remove_stale(self):
        """Delete tape folders and audio from releases that are gone or were renamed"""
        if os.path.isdir(self.tape_root):
            for name in os.listdir(self.tape_root):
                path = os.path.join(self.tape_root, name)
                if path not in self.tape_dirs:
                    logger.info(f"removing stale tape {name}")
                    for f in os.listdir(path):
                        os.remove(os.path.join(path, f))
                    os.rmdir(path)
        wanted_dirs = {os.path.dirname(d) for d in self.jobs}
        if os.path.isdir(self.audio_root):
            for name in os.listdir(self.audio_root):
                path = os.path.join(self.audio_root, name)
                if path not in wanted_dirs:
                    logger.info(f"removing stale audio {name}")
                    for f in os.listdir(path):
                        os.remove(os.path.join(path, f))
                    os.rmdir(path)
                else:
                    for f in os.listdir(path):
                        if os.path.join(path, f) not in self.jobs:
                            os.remove(os.path.join(path, f))

    def convert(self, workers, quality):
        todo = [(dst, src) for dst, src in self.jobs.items() if not up_to_date(dst, src)]
        logger.info(f"{len(todo)} of {len(self.jobs)} audio files to convert or copy")
        done = 0
        with concurrent.futures.ThreadPoolExecutor(workers) as pool:
            for _ in pool.map(lambda job: convert_one(job[1], job[0], quality), todo):
                done += 1
                if done % 100 == 0:
                    logger.info(f"  {done}/{len(todo)}")


def up_to_date(dst, src):
    return os.path.exists(dst) and os.path.getmtime(dst) >= os.path.getmtime(src)


def convert_one(src, dst, quality):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part"
    if dst.endswith(".ogg") and not src.lower().endswith(".ogg"):
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", src, "-map", "0:a", "-c:a", "libvorbis", "-q:a", str(quality),
               "-map_metadata", "0", "-f", "ogg", tmp]
        if subprocess.run(cmd).returncode != 0:
            logger.error(f"failed to convert {src}")
            return
    else:  # already lossy: copy, never re-encode
        with open(src, "rb") as fin, open(tmp, "wb") as fout:
            while chunk := fin.read(1 << 20):
                fout.write(chunk)
    os.replace(tmp, dst)


def write_report(path, mappings, skipped):
    lines = []
    flagged = [m for m in mappings if m.problems()]
    lines.append(f"{len(mappings)} releases mapped, {len(flagged)} to check, {len(skipped)} skipped\n")
    for m in sorted(mappings, key=lambda m: (not m.problems(), m.release.short)):
        r = m.release
        lines.append(f"{'CHECK ' if m.problems() else ''}{r.short}  {r.name}")
        for date, show in m.shows.items():
            flag = "plays first" if show["primary"] else "after archive.org"
            amount = f"{show['coverage']:.0%} of the show" if show["coverage"] is not None else "archive.org has little or none of it"
            lines.append(f"    {date}: {len(show['tracks'])} tracks ({show['minutes']} min), {amount}"
                         f"{', sets ' + ','.join(show['full_sets']) if show['full_sets'] else ''} -> {flag}")
        for p in m.problems():
            lines.append(f"    ! {p}")
        if m.problems():
            for i, (t, (date, j, sim)) in enumerate(zip(r.tracks, m.assignment)):
                song = m.setlists[date]["songs"][j] if date in m.setlists and j >= 0 else "-"
                lines.append(f"      {i + 1:3d}. {t.title[:45]:45s} -> {date} {song[:30]:30s} {sim:.2f}")
        lines.append("")
    if skipped:
        lines.append("Skipped (duplicates or overrides):")
        lines += [f"    {s}" for s in skipped]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return flagged


def write_mapping_json(path, mappings):
    """Track-by-track alignment, for tools that need more than the tape folders (eg filling gaps from archive.org)"""
    out = []
    for m in mappings:
        out.append({
            "release": m.release.name,
            "short": m.release.short,
            "shows": m.shows,
            "tracks": [
                {"title": t.title, "file": t.path, "date": d, "setlist_index": j, "similarity": round(sim, 3)}
                for t, (d, j, sim) in zip(m.release.tracks, m.assignment)
            ],
            "setlists": m.setlists,
        })
    with open(path, "w") as f:
        json.dump(out, f, indent=1)


# ---------------------------------------------------------------------------------------------------------


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("roots", nargs="+", help="folders holding official release folders")
    parser.add_argument("--out", required=True, help="output folder, synced to ~/archive on the Time Machine")
    parser.add_argument("--overrides", help="TOML file of manual fixes (default: <out>/overrides.toml)")
    parser.add_argument("--cache-dir", default=os.path.expanduser("~/.cache/timemachine-releases"))
    parser.add_argument("--only", help="only releases whose folder name contains this text")
    parser.add_argument("--no-audio", action="store_true", help="map and write tape folders, but don't convert audio")
    parser.add_argument("--quality", type=int, default=6, help="Ogg Vorbis quality for converted FLAC (6 is about 192 kbps)")
    parser.add_argument("-j", "--jobs", type=int, default=os.cpu_count())
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for name in ["timemachine", "timemachine.Archivary", "timemachine.config", "timemachine.utils", "timemachine.tapedb"]:
        logging.getLogger(name).setLevel(logging.WARNING)

    out = os.path.expanduser(args.out)
    os.makedirs(out, exist_ok=True)
    os.makedirs(args.cache_dir, exist_ok=True)
    cache_path = os.path.join(args.cache_dir, "cache.json")
    cache = load_json(cache_path, {})
    overrides_path = args.overrides or os.path.join(out, "overrides.toml")
    overrides = {}
    if os.path.exists(overrides_path):
        with open(overrides_path, "rb") as f:
            overrides = tomllib.load(f)

    logger.info("Loading archive.org show dates")
    setlists = Setlists(os.path.join(args.cache_dir, "metadata"), cache)
    logger.info("Reading releases")
    releases = find_releases(args.roots, cache.setdefault("probe", {}))
    if args.only:
        releases = [r for r in releases if args.only.lower() in r.name.lower()]

    mappings, skipped, seen = [], [], {}
    for r in releases:
        sig = r.signature()
        if sig in seen:
            skipped.append(f"{r.path} (same as {seen[sig]})")
            continue
        seen[sig] = r.path
        if overrides.get(r.name, {}).get("skip"):
            skipped.append(f"{r.path} (skip in overrides)")
            continue
        logger.info(f"Mapping {r.short}: {r.name}")
        mappings.append(map_release(r, setlists, overrides))
        with open(cache_path, "w") as f:  # setlists are slow to fetch: keep them as we go
            json.dump(cache, f)

    writer = Writer(out)
    for m in mappings:
        writer.write(m)
    if not args.only:
        writer.remove_stale()
    flagged = write_report(os.path.join(out, "report.txt"), mappings, skipped)
    write_mapping_json(os.path.join(out, "mapping.json"), mappings)
    with open(cache_path, "w") as f:
        json.dump(cache, f)
    logger.info(f"{len(mappings)} releases -> {len(writer.tape_dirs)} tapes. {len(flagged)} to check: see {out}/report.txt")
    if not args.no_audio:
        writer.convert(args.jobs, args.quality)
    return 0


if __name__ == "__main__":
    sys.exit(main())
