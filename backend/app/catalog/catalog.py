"""Shared skill / category catalog: the one vocabulary TalentFlow and the CV warehouse agree on.

Source of truth is `shared/catalog/` in the repo root; `sync.py` copies `catalog.py` and `catalog.json`
into each service and a test in each service fails when its copy drifts. Standard library only, so the
module can be copied anywhere. Everything that is data (aliases, ambiguity rules, section headings,
level and education ladders) lives in catalog.json; this file only knows how to apply it.

Public surface:
- `load_catalog()`           the parsed catalog (entries, categories, rules)
- `find_mentions(text)`      every catalog mention in free text, longest match wins, ambiguity-checked
- `extract_cv_profile(text)` skills / level / education / languages / certifications / sections of a CV
- `extract_jd_requirements`  required vs preferred skills, years, category, level... of a job description
- `classify` / `detect_for_query`  the fixed 10-category classifier
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Protocol

DATA_FILE = Path(__file__).with_name("catalog.json")
UNCLASSIFIED = "UNCLASSIFIED"
TECH_KINDS = frozenset({
    "language", "framework", "library", "database", "tool", "cloud", "concept", "methodology",
})
_MAX_CV_CHARS = 60_000
_PLURAL_MIN = 3


# --------------------------------------------------------------------------------------------
# Text normalisation
# --------------------------------------------------------------------------------------------

@lru_cache(maxsize=4096)
def _fold_char(char: str) -> str:
    if char == "\n":
        return char
    if char.isspace():
        return " "
    if char in "đĐ":
        return "d"
    base = unicodedata.normalize("NFD", char)[:1] or char
    lowered = base.lower()
    return lowered if len(lowered) == 1 else char


def fold(text: str) -> str:
    """Lowercase and strip Vietnamese diacritics while keeping every character offset intact."""
    return "".join(map(_fold_char, text))


# Every quantifier is bounded and an address can only start at the beginning of a word run, so a long
# run of letters, dots or digits cannot make the scan quadratic.
_CONTACT = re.compile(
    r"https?://\S{1,300}|www\.\S{1,300}"
    r"|(?<![\w.+-])[\w.+-]{1,64}@[\w-]{1,64}(?:\.[\w-]{1,64}){1,4}"
    r"|(?:linkedin|github|gitlab|portfolio|website)[ \t]{0,3}:[ \t]{0,3}\S{1,300}",
    re.IGNORECASE,
)


def _mask_contacts(folded: str) -> str:
    """Blank out URLs and e-mails (same length) so `GitHub: https://...` is not read as a skill."""
    return _CONTACT.sub(lambda match: " " * len(match.group(0)), folded)


# --------------------------------------------------------------------------------------------
# Catalog model
# --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Ambiguity:
    aliases: frozenset[str]            # folded aliases that need context
    forms: frozenset[str]              # exact surface forms allowed (case-sensitive); empty = any case
    cues: tuple[str, ...]              # folded words that, nearby, make the mention a skill
    deny_after: tuple[str, ...]
    deny_before: tuple[str, ...]


@dataclass(frozen=True)
class Entry:
    id: str
    name: str
    kind: str
    aliases: tuple[str, ...]
    categories: tuple[str, ...]
    ambiguity: Ambiguity | None = None
    rank: int | None = None
    implies: str | None = None


@dataclass(frozen=True)
class Category:
    code: str
    label: str
    role: str
    keywords: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class Mention:
    entry: Entry
    start: int
    end: int


@dataclass(frozen=True)
class Catalog:
    version: str
    categories: tuple[Category, ...]
    entries: tuple[Entry, ...]
    rules: dict[str, Any]
    by_id: dict[str, Entry] = field(default_factory=dict)
    by_alias: dict[str, Entry] = field(default_factory=dict)

    def resolve(self, value: str) -> Entry | None:
        """Entry for an id, canonical name or any alias (what a user typed into a filter)."""
        key = fold(value).strip()
        return self.by_id.get(key) or self.by_alias.get(key)

    def entries_of(self, kinds: Iterable[str]) -> list[Entry]:
        wanted = set(kinds)
        return [entry for entry in self.entries if entry.kind in wanted]


def _entry(raw: dict[str, Any]) -> Entry:
    ambiguity = raw.get("ambiguous")
    spoken = [*([raw["name"]] if raw.get("name_alias", True) else []), *raw.get("aliases", []),
              *(ambiguity or {}).get("aliases", [])]
    return Entry(
        id=raw["id"], name=raw["name"], kind=raw["kind"],
        aliases=tuple(dict.fromkeys(fold(value).strip() for value in spoken)),
        categories=tuple(raw.get("categories", ())),
        ambiguity=None if not ambiguity else Ambiguity(
            aliases=frozenset(fold(value).strip() for value in ambiguity.get("aliases", [raw["name"]])),
            forms=frozenset(ambiguity.get("forms", ())),
            cues=tuple(fold(value) for value in ambiguity.get("cues", ())),
            deny_after=tuple(fold(value) for value in ambiguity.get("deny_after", ())),
            deny_before=tuple(fold(value) for value in ambiguity.get("deny_before", ())),
        ),
        rank=raw.get("rank"), implies=raw.get("implies"),
    )


@lru_cache(maxsize=1)
def load_catalog() -> Catalog:
    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    entries = tuple(_entry(item) for item in raw["entries"])
    categories = tuple(
        Category(item["code"], item["label"], item["role"],
                 tuple((pattern, float(weight)) for pattern, weight in item["keywords"]))
        for item in raw["categories"]
    )
    by_alias: dict[str, Entry] = {}
    for entry in entries:
        for alias in entry.aliases:
            by_alias.setdefault(alias, entry)
    # "threat hunts", "risk assessments": a long enough alias also matches its plural.
    for entry in entries:
        if entry.ambiguity:
            continue
        for alias in entry.aliases:
            if len(alias) >= _PLURAL_MIN and alias[-1].isalpha() and alias[-1] != "s":
                by_alias.setdefault(alias + "s", entry)
    return Catalog(
        version=raw["version"], categories=categories, entries=entries, rules=raw["rules"],
        by_id={entry.id: entry for entry in entries}, by_alias=by_alias,
    )


def catalog_version() -> str:
    return load_catalog().version


@lru_cache(maxsize=1)
def catalog_fingerprint() -> dict[str, str]:
    """sha256 of the two files this process loaded, so a running service can prove which catalog it uses."""
    return {"version": load_catalog().version,
            "json_sha256": hashlib.sha256(DATA_FILE.read_bytes()).hexdigest(),
            "py_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


# --------------------------------------------------------------------------------------------
# Alias matcher: one trie-shaped regex, built once
# --------------------------------------------------------------------------------------------

def _trie_regex(words: Iterable[str]) -> str:
    """Alternation of literals compiled into a prefix trie so the regex engine never scans all aliases."""
    root: dict[str, Any] = {}
    for word in words:
        node = root
        for char in word:
            node = node.setdefault(char, {})
        node[""] = True

    def emit(node: dict[str, Any]) -> str:
        branches = [re.escape(char) + emit(child) for char, child in sorted(node.items()) if char]
        if not branches:
            return ""
        body = branches[0] if len(branches) == 1 else "(?:" + "|".join(branches) + ")"
        return f"(?:{body})?" if "" in node else body

    return emit(root)


@lru_cache(maxsize=1)
def _matcher() -> re.Pattern[str]:
    aliases = sorted(load_catalog().by_alias, key=len, reverse=True)
    # A name cannot start inside "foo.js" or end inside "c++" / "c#": those are other names.
    return re.compile(r"(?<![a-z0-9_.])" + _trie_regex(aliases)
                      + r"(?:(?<=[a-z0-9_])(?![a-z0-9_]|\+\+|#)|(?<![a-z0-9_]))")


_GLUE = re.compile(r"^[\s,;/|&+•·\-–—:()\[\]]*(?:(?:and|or|va|hoac|voi|cung|plus|nhu|as|including|nhu la)\b[\s,;/|&+•·\-–—:()\[\]]*)?$")
_GLUE_MAX = 14
_HEADER_LINE = re.compile(r"^[\s•·\-–*]*(?P<head>[^:\n]{0,40}):")
_HYPHEN_WRAP = re.compile(r"(?<=[a-z])-[ \t]*\n[ \t]*(?=[a-z])")


def _list_context_lines(folded: str, headers: tuple[str, ...]) -> list[tuple[int, int]]:
    """Spans of lines that are skill lists: a `Tools:` style header, or the wrapped tail of one."""
    pattern = re.compile(r"\b(?:" + "|".join(re.escape(word) for word in headers) + r")\b")
    spans: list[tuple[int, int]] = []
    active = False
    for match in re.finditer(r"[^\n]*", folded):
        line = match.group(0)
        header = _HEADER_LINE.match(line)
        starts_list = bool(header and pattern.search(header.group("head")))
        if starts_list or active:
            spans.append((match.start(), match.end()))
        stripped = line.rstrip()
        active = bool(stripped) and (starts_list or active) and stripped.endswith((",", "/", "&", "+"))
    return spans


def _within(spans: list[tuple[int, int]], starts: list[int], start: int, end: int) -> bool:
    index = bisect_right(starts, start) - 1
    return index >= 0 and spans[index][0] <= start and end <= spans[index][1]


@lru_cache(maxsize=512)
def _cue_pattern(cues: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(cue) for cue in cues) + r")(?![a-z0-9])")


def _word_near(folded: str, start: int, end: int, cues: tuple[str, ...], window: int) -> bool:
    if not cues:
        return False
    around = folded[max(0, start - window):start] + " " + folded[end:end + window]
    return bool(_cue_pattern(cues).search(around))


def _denied(folded: str, start: int, end: int, rule: Ambiguity, *, after: bool = True) -> bool:
    """A neighbouring word rules the name out ("Key Vault", "go to market"). `after=False` checks only the
    words before it: next to a confirmed skill, "Python or Go for tooling" is still Go."""
    following, preceding = folded[end:end + 24].lstrip(" "), folded[max(0, start - 24):start].rstrip(" ")
    if after:
        for word in rule.deny_after:
            if following.startswith(word) and not (word[-1].isalnum() and following[len(word):len(word) + 1].isalnum()):
                return True
    return any(preceding.endswith(word) and not (word[0].isalnum() and preceding[-len(word) - 1:-len(word)].isalnum())
               for word in rule.deny_before)


class _Anchors:
    """Confirmed mention spans, kept sorted so 'is this name next to a confirmed one?' is a binary search."""

    def __init__(self, spans: Iterable[tuple[int, int]]) -> None:
        self.spans = sorted(spans)
        self.starts = [start for start, _ in self.spans]

    def add(self, span: tuple[int, int]) -> None:
        index = bisect_left(self.starts, span[0])
        self.spans.insert(index, span)
        self.starts.insert(index, span[0])

    def next_to(self, folded: str, start: int, end: int) -> bool:
        """True when only list punctuation / joiner words separate [start, end) from an anchor."""
        left = bisect_right(self.starts, start) - 1
        if left >= 0 and self.spans[left][1] <= start:
            gap = folded[self.spans[left][1]:start]
            if len(gap) <= _GLUE_MAX and _GLUE.match(gap):
                return True
        right = bisect_left(self.starts, end)
        if right < len(self.spans):
            gap = folded[end:self.spans[right][0]]
            if len(gap) <= _GLUE_MAX and _GLUE.match(gap):
                return True
        return False


def _join_hyphen_wraps(folded: str, flat: str) -> tuple[str, list[int] | None]:
    """'Kuber-\\nnetes' read as 'Kubernetes': text with the wrap removed and a map back to original offsets."""
    wraps = list(_HYPHEN_WRAP.finditer(folded))
    if not wraps:
        return flat, None
    dropped = {index for wrap in wraps for index in range(wrap.start(), wrap.end())}
    kept = [index for index in range(len(flat)) if index not in dropped]
    return "".join(flat[index] for index in kept), kept


def _accepted_doubtful(doubtful: list[tuple[int, int, Entry, Ambiguity]], sure: list[tuple[int, int, Entry]],
                       folded: str, text: str, catalog: Catalog) -> list[tuple[int, int, Entry]]:
    """Ambiguous names that count as skills here: exact form alone, a skill-list line, a cue word nearby, or
    sitting next to a confirmed skill ("Python, Go, Rust")."""
    window = int(catalog.rules["cue_window"])
    lines = _list_context_lines(folded, tuple(catalog.rules["list_headers"]))
    line_starts = [start for start, _ in lines]
    anchors = _Anchors((start, end) for start, end, _ in sure)
    accepted: list[tuple[int, int, Entry]] = []
    pending: list[tuple[int, int, Entry, Ambiguity]] = []
    vouched: set[int] = set()
    for item in doubtful:
        start, end, entry, rule = item
        if rule.forms and text[start:end] not in rule.forms:
            continue
        standalone = bool(rule.forms) and not rule.cues
        if standalone or (not _denied(folded, start, end, rule) and (
                _within(lines, line_starts, start, end) or _word_near(folded, start, end, rule.cues, window))):
            accepted.append((start, end, entry))
            anchors.add((start, end))
        else:
            pending.append(item)
    # A forward then a backward sweep lets each accepted name vouch for its neighbour, in linear time.
    open_items = list(pending)
    for sweep in (open_items, open_items[::-1]):
        for item in sweep:
            if id(item) not in vouched and not _denied(folded, item[0], item[1], item[3], after=False) \
                    and anchors.next_to(folded, item[0], item[1]):
                vouched.add(id(item))
                accepted.append((item[0], item[1], item[2]))
                anchors.add((item[0], item[1]))
    return accepted


def find_mentions(text: str, kinds: Iterable[str] | None = None) -> list[Mention]:
    """Catalog mentions in reading order. Whole tokens only; the longest alias wins; ambiguous short
    names (Go, R, C, Swift, SOC...) only count in a skill context defined by their catalog entry."""
    catalog = load_catalog()
    text = text[:_MAX_CV_CHARS]
    folded = _mask_contacts(fold(text))
    flat, offsets = _join_hyphen_wraps(folded, folded.replace("\n", " "))
    wanted = None if kinds is None else frozenset(kinds)
    sure: list[tuple[int, int, Entry]] = []
    doubtful: list[tuple[int, int, Entry, Ambiguity]] = []
    for match in _matcher().finditer(flat):
        entry = catalog.by_alias[match.group(0)]
        if wanted is not None and entry.kind not in wanted:
            continue
        start, end = (match.start(), match.end()) if offsets is None else (offsets[match.start()], offsets[match.end() - 1] + 1)
        rule = entry.ambiguity
        if rule and match.group(0) in rule.aliases:
            doubtful.append((start, end, entry, rule))
        else:
            sure.append((start, end, entry))
    accepted = sure + (_accepted_doubtful(doubtful, sure, folded, text, catalog) if doubtful else [])
    accepted.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    return [Mention(entry, start, end) for start, end, entry in accepted]


def distinct_entries(mentions: Iterable[Mention]) -> list[Entry]:
    return list(dict.fromkeys(mention.entry for mention in mentions))


def technical_ids(ids: Iterable[str]) -> list[str]:
    """The ids that are skills a CV lists (languages, tools, concepts...), without certifications and the like."""
    by_id = load_catalog().by_id
    return [key for key in ids if key in by_id and by_id[key].kind in TECH_KINDS]


def extract_skills(text: str) -> list[Entry]:
    """Technical skills mentioned in `text`, once each, in order of first appearance."""
    return distinct_entries(find_mentions(text, TECH_KINDS))


def first_span(text: str, skill: str) -> tuple[int, int] | None:
    """(offset, length) of the first mention of a skill given by id / name / alias, or None."""
    entry = load_catalog().resolve(skill)
    if entry is None:
        return None
    for mention in find_mentions(text):
        if mention.entry is entry:
            return mention.start, mention.end - mention.start
    return None


# --------------------------------------------------------------------------------------------
# Fixed 10-category taxonomy (data lives in catalog.json)
# --------------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _compiled_keywords() -> dict[str, tuple[tuple[re.Pattern[str], float], ...]]:
    return {category.code: tuple((re.compile(pattern), weight) for pattern, weight in category.keywords)
            for category in load_catalog().categories}


def category_by_code() -> dict[str, Category]:
    return {category.code: category for category in load_catalog().categories}


def normalize_code(value: str | None) -> str | None:
    """Accept a category code, Vietnamese label, role name or legacy code; return the canonical code."""
    if not value:
        return None
    folded = value.strip().casefold()
    for category in load_catalog().categories:
        if folded in {category.code.casefold(), category.label.casefold(), category.role.casefold()}:
            return category.code
    return load_catalog().rules["legacy_categories"].get(folded)


def keyword_scores(title: str, body: str = "") -> dict[str, float]:
    title_text, body_text = title.casefold(), body.casefold()
    factor = float(load_catalog().rules["body_factor"])
    result: dict[str, float] = {}
    for code, patterns in _compiled_keywords().items():
        total = 0.0
        for pattern, weight in patterns:
            if pattern.search(title_text):
                total += weight
            if body_text and pattern.search(body_text):
                total += weight * factor
        result[code] = total
    return result


def classify(title: str, body: str = "") -> str:
    """Best-scoring category, or UNCLASSIFIED when the CV carries no usable signal."""
    ranked = sorted(keyword_scores(title, body).items(), key=lambda item: item[1], reverse=True)
    return ranked[0][0] if ranked[0][1] >= float(load_catalog().rules["min_score"]) else UNCLASSIFIED


def skill_votes(entries: Iterable[Entry]) -> dict[str, float]:
    """Each skill splits one vote across the categories it is characteristic of."""
    votes = {category.code: 0.0 for category in load_catalog().categories}
    for entry in entries:
        for code in entry.categories:
            votes[code] += 1 / len(entry.categories)
    return votes


def rank_categories(head: str, body: str, entries: Iterable[Entry]) -> list[str]:
    """Categories a text clearly targets, best first (empty when nothing stands out).

    The best one needs `query_min_score` and either a clear margin over the runner-up or a title that
    already prefers it; a runner-up within reach is returned as the secondary category."""
    rules = load_catalog().rules
    head_scores = keyword_scores(head)
    scores = keyword_scores(head, body)
    for code, value in skill_votes(entries).items():
        scores[code] += value * float(rules["skill_vote_weight"])
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    (best, top), (second, runner_up) = ranked[0], ranked[1]
    if top < float(rules["query_min_score"]):
        return []
    clear = top >= runner_up * float(rules["query_margin"])
    if not clear:
        # Too close to call on the whole text: the title decides, if it prefers one of the two.
        if max(head_scores[best], head_scores[second]) < float(rules["query_min_score"]) or head_scores[best] == head_scores[second]:
            return []
        best, second = (best, second) if head_scores[best] > head_scores[second] else (second, best)
    close = min(scores[second], top) >= top * float(rules["secondary_ratio"]) and scores[second] >= float(rules["query_min_score"])
    return [best, second] if close else [best]


# --------------------------------------------------------------------------------------------
# Ladders: level, education, spoken languages, certifications, domains
# --------------------------------------------------------------------------------------------

def _ranked(text: str, kind: str) -> list[Entry]:
    return distinct_entries(find_mentions(text, {kind}))


def detect_level(text: str, *, highest: bool = True) -> Entry | None:
    """Seniority named in `text`: the highest rank when several are named, or the lowest for a range
    such as 'Intern / Fresher' where the posting accepts the lower end."""
    found = _ranked(text, "level")
    return (max if highest else min)(found, key=lambda entry: entry.rank or 0) if found else None


def detect_education(text: str) -> Entry | None:
    """The highest completed degree named in `text` ("pursuing", "đang học", "incomplete" do not count)."""
    folded = fold(text)
    found = [m.entry for m in find_mentions(text, {"education"}) if not _in_progress(folded, m, _jd_rules())]
    return max(found, key=lambda entry: entry.rank or 0) if found else None


def detect_languages(text: str) -> list[str]:
    names: list[str] = []
    for mention in find_mentions(text, {"spoken_language", "language_test"}):
        entry = mention.entry
        language = entry.name if entry.kind == "spoken_language" else load_catalog().by_id[entry.implies].name
        if language not in names:
            names.append(language)
    return names


def detect_certifications(text: str) -> list[str]:
    return [entry.name for entry in _ranked(text, "certification")]


def detect_domains(text: str) -> list[str]:
    return [entry.name for entry in _ranked(text, "domain")]


# --------------------------------------------------------------------------------------------
# CV -> structured profile
# --------------------------------------------------------------------------------------------

_BULLET = re.compile(r"^\s*(?:[•·▪●○◦■►➢✓✔*+–—-]|\d+[.)])\s*")
_ROLE_LINE = re.compile(r"\|.*(?:\d{2}/\d{4}|\b(?:19|20)\d{2}\b)")


def _section_of(line: str, headings: dict[str, list[str]]) -> str | None:
    key = re.sub(r"[\s&:]+", " ", fold(line)).strip()
    if not key or len(key) > 40:
        return None
    for section, names in headings.items():
        if key in names:
            return section
    return None


def split_cv_sections(text: str) -> dict[str, list[str]]:
    """Lines of a CV grouped under its headings (EN + VI), wrapped bullet lines re-joined."""
    headings = {section: [re.sub(r"[\s&:]+", " ", fold(name)).strip() for name in names]
                for section, names in load_catalog().rules["cv_sections"].items()}
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for raw in text[:_MAX_CV_CHARS].splitlines():
        line = raw.strip()
        if not line:
            continue
        section = _section_of(line, headings)
        if section:
            current = section
            sections.setdefault(current, [])
            continue
        lines = sections.setdefault(current, [])
        starts_new = bool(_BULLET.match(line)) or bool(_ROLE_LINE.search(line)) or not lines
        if starts_new:
            lines.append(_BULLET.sub("", line))
        else:
            lines[-1] = f"{lines[-1]} {line}"
    return sections


def _first_segments(lines: list[str]) -> list[str]:
    return [segment for line in lines if _ROLE_LINE.search(line) and (segment := line.split("|")[0].strip())]


_SKILL_SECTIONS = ("skills", "summary", "experience", "projects", "languages")


def _skill_source(sections: dict[str, list[str]], text: str, title: str) -> str:
    """Text where a skill mention is a claim: not the education coursework, contact block or footer."""
    if len(sections) == 1:
        return text
    return "\n".join([title, *(line for name in _SKILL_SECTIONS for line in sections.get(name, []))])


_TITLE_LINES = 6
_TITLE_LINE_MAX = 80


@lru_cache(maxsize=1)
def _title_words() -> re.Pattern[str]:
    return _phrases(load_catalog().rules["title_words"])


def guess_title(text: str) -> str:
    """The headline of a CV (first short line near the top that names a role), or ''."""
    for line in [line.strip() for line in text.splitlines() if line.strip()][:_TITLE_LINES]:
        if len(line) <= _TITLE_LINE_MAX and not re.search(r"[@|:]|https?", line) and _title_words().search(fold(line)):
            return line
    return ""


def extract_cv_profile(text: str, title: str = "") -> dict[str, Any]:
    """Everything the two services need from CV text: canonical skills, category, level, education..."""
    title = title or guess_title(text)
    sections = split_cv_sections(text)
    skills = extract_skills(_skill_source(sections, text, title))
    experience = sections.get("experience", [])
    roles = list(dict.fromkeys([*([title] if title else []), *_first_segments(experience)]))
    level = detect_level(title) or detect_level(" ".join(_first_segments(experience)[:1]))
    education = detect_education(" ".join(sections.get("education", [])) or text)
    languages = detect_languages(" ".join(sections.get("languages", [])) or text)
    return {
        "catalog_version": catalog_version(),
        "skills": [entry.name for entry in skills],
        "skill_ids": [entry.id for entry in skills],
        "category": classify(title, text),
        "level": level.name if level else None,
        "education_level": education.name if education else "",
        "languages": languages,
        "certifications": detect_certifications(text),
        "roles": roles,
        "summary": " ".join(sections.get("summary", [])),
        "responsibilities": [line for line in experience if not _ROLE_LINE.search(line)],
        "projects": sections.get("projects", []),
        "education": sections.get("education", []),
    }


# --------------------------------------------------------------------------------------------
# JD -> requirements
# --------------------------------------------------------------------------------------------

class ExtractionProvider(Protocol):
    """Optional refinement hook: an LLM can adjust the rules draft; nothing here calls a network."""

    def refine_jd(self, text: str, draft: dict[str, Any]) -> dict[str, Any]: ...


def _phrases(words: Iterable[str]) -> re.Pattern[str]:
    ordered = sorted((fold(word) for word in words), key=len, reverse=True)
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(word) for word in ordered) + r")(?![a-z0-9])")


@dataclass(frozen=True)
class _JdRules:
    sections: dict[str, re.Pattern[str]]
    prefer_before: re.Pattern[str]
    prefer_after: re.Pattern[str]
    negation: re.Pattern[str]
    negation_after: re.Pattern[str]
    softener: re.Pattern[str]
    major_cue: re.Pattern[str]
    experience_cue: re.Pattern[str]
    years_qualifier: re.Pattern[str]
    years_denied: re.Pattern[str]
    title_cue: re.Pattern[str]
    hiring_cue: re.Pattern[str]
    context_statement: re.Pattern[str]
    context_break: re.Pattern[str]
    in_progress: re.Pattern[str]


@lru_cache(maxsize=1)
def _jd_rules() -> _JdRules:
    rules = load_catalog().rules["jd"]
    after = "|".join(fold(item) for item in rules["prefer_after"])
    subjects = "|".join(re.escape(fold(item)) for item in rules["context_subjects"])
    verbs = "|".join(re.escape(fold(item)) for item in rules["context_verbs"])
    return _JdRules(
        sections={name: _phrases(words) for name, words in rules["sections"].items()},
        prefer_before=_phrases(rules["prefer_before"]),
        prefer_after=re.compile(
            r"^(?P<lead>[\s,:()\-–]*(?:[a-z']+\s+){0,%d})(?:%s)(?![a-z0-9])" % (_FILLER_WORDS, after)),
        negation=_phrases(rules["negation"]),
        negation_after=_phrases(rules["negation_after"]),
        softener=_phrases(rules["softener"]),
        major_cue=_phrases(rules["major_cue"]),
        experience_cue=_phrases(rules["experience_cue"]),
        years_qualifier=_phrases(rules["years_qualifier"]),
        years_denied=_phrases(rules["years_denied"]),
        title_cue=_phrases(rules["title_cue"]),
        hiring_cue=_phrases(rules["hiring_cue"]),
        context_statement=re.compile(
            rf"^(?:{subjects})\s+(?:(?!can\b|tim\b|tuyen\b|need|looking|hiring|seek)[a-z]+\s+){{0,3}}(?:{verbs})(?![a-z0-9])"),
        context_break=_phrases(rules["context_break"]),
        in_progress=_phrases(rules["education_in_progress"]),
    )


_BULLET_START = re.compile(r"^\s*(?:[•·▪●○◦■►➢✓✔*+–—-]|\d+[.)])\s*")
_CLAUSE_END = re.compile(r"[.;!?](?=\s|$)")
_HEADING_NOISE = re.compile(r"[#*_\-–—\s]+")
_HEADING_WORDS = 5
_FILLER_WORDS = 3
_GAP_MAX = 40
_NEGATION_REACH = 40
_MAJOR_REACH = 30
_PREFER_REACH = 300
_TITLE_MAX = 100
_JD_KINDS = TECH_KINDS | {"certification", "education"}
_NUMBER_WORDS = {"mot": 1, "one": 1, "hai": 2, "two": 2, "ba": 3, "three": 3, "bon": 4, "four": 4, "five": 5,
                 "sau": 6, "six": 6, "bay": 7, "seven": 7, "tam": 8, "eight": 8, "chin": 9, "nine": 9,
                 "muoi": 10, "ten": 10}
_YEARS = re.compile(
    r"(?<![\d.,/])(?P<low>\d{1,2}(?:[.,]\d)?|" + "|".join(_NUMBER_WORDS) + r")\s{0,3}(?P<plus>\+|plus|or more|or above)?\s{0,3}"
    r"(?:(?:-|–|to|den|~)\s{0,3}\d{1,2}(?:[.,]\d)?\s{0,3}(?P<plus2>\+)?\s{0,3})?"
    r"(?P<unit>nam|years?|yrs?|yoe|thang|months?)(?![a-z])(?P<post>\s{1,3}(?:minimum|min|or more|or above|tro len))?"
)
_SKILL_SCOPE = 25
_MAX_YEARS = 40
_DENIED_REACH = 30


@dataclass(frozen=True)
class _Clause:
    start: int
    end: int
    section: str          # required | preferred | ignore


def _mentions_in(mentions: list[Mention], starts: list[int], low: int, high: int) -> list[Mention]:
    """Mentions that start in [low, high), by binary search (mentions are sorted by start)."""
    return mentions[bisect_left(starts, low):bisect_left(starts, high)]


def _heading_kind(content: str, rules: _JdRules, has_skill: bool) -> tuple[str, bool] | None:
    """(section kind, has inline content) when `content` is a heading such as 'Ưu tiên:' / 'Nice to have'."""
    head, colon, rest = content.partition(":")
    if colon and len(head) <= 50:
        candidate, inline = head, bool(rest.strip())
    elif len(content) <= 40:
        candidate, inline = content, False
    else:
        return None
    candidate = _HEADING_NOISE.sub(" ", re.sub(r"^[\W_]+", "", candidate)).strip()
    if has_skill or not candidate or len(candidate.split()) > _HEADING_WORDS or re.search(r"\d", candidate):
        return None
    for kind in ("preferred", "ignore", "required"):
        if rules.sections[kind].match(candidate):
            return kind, inline
    return None


def _statement_clauses(folded: str, start: int, end: int, section: str, rules: _JdRules) -> list[_Clause]:
    """A sentence about the company or its stack ("Our team uses Spark") describes context, not a
    requirement; what follows "but you will work in Python" is a requirement again."""
    if not rules.context_statement.match(folded[start:end].lstrip()):
        return [_Clause(start, end, section)]
    pivot = rules.context_break.search(folded, start, end)
    if not pivot:
        return [_Clause(start, end, "ignore")]
    return [_Clause(start, pivot.start(), "ignore"), _Clause(pivot.start(), end, section)]


def _split_clauses(text: str, folded: str, mentions: list[Mention], starts: list[int], rules: _JdRules) -> list[_Clause]:
    """Clause spans, each tagged with the heading in force when it was written. A heading can open a
    sentence in the middle of a line ("Not required: coding. Nice: BrowserStack")."""
    clauses: list[_Clause] = []
    section = "required"
    until_blank = False
    for line in re.finditer(r"[^\n]*", text):
        bullet = _BULLET_START.match(line.group(0))
        start = line.start() + (bullet.end() if bullet else 0)
        if not folded[start:line.end()].strip():
            if until_blank:
                section, until_blank = "required", False
            continue
        line_section = section      # a "Nice to have:" sentence does not turn the rest of its line into one
        carried: str | None = None  # ...except items separated by ";", which still belong to that heading
        position = start
        for end in [m.end() for m in _CLAUSE_END.finditer(folded, start, line.end())] + [line.end()]:
            content = folded[position:end]
            if not content.strip():
                position = end
                continue
            head_end = position + len(content.partition(":")[0]) + 1
            heading = _heading_kind(content, rules, bool(_mentions_in(mentions, starts, position, min(end, head_end))))
            clause_section = carried or line_section
            if heading:
                kind, inline = heading
                section, until_blank = kind, inline
                if not inline:
                    line_section = kind
                    position = end
                    continue
                clause_section = kind
                if kind == "required":
                    line_section = kind
            carried = clause_section if clause_section != line_section and folded[end - 1] == ";" else None
            clauses.extend(_statement_clauses(folded, position, end, clause_section, rules))
            position = end
    return clauses


def _linked(folded: str, clause_start: int, left: Mention, right: Mention) -> bool:
    """Two neighbouring mentions belong to one enumeration: joined by 'and/or/và/hoặc', or listed inside
    one pair of parentheses. A bare comma between top-level items starts a new statement."""
    gap = folded[left.end:right.start]
    if len(gap) > _GAP_MAX or ";" in gap:
        return False
    depth = folded.count("(", clause_start, left.end) - folded.count(")", clause_start, left.end)
    if ")" not in gap and ("(" in gap or depth > 0):
        return True
    return bool(_CONJUNCTION.search(gap))


_CONJUNCTION = re.compile(r"(?<![a-z0-9])(?:and|or|va|hoac|plus)(?![a-z0-9])|[&/]")


def _preferred_by_suffix(clause: _Clause, folded: str, inside: list[Mention], rules: _JdRules) -> set[int]:
    """Indexes of mentions followed by an 'is a plus' cue, together with the enumeration they belong to."""
    flagged: set[int] = set()
    for index, mention in enumerate(inside):
        found = rules.prefer_after.search(folded[mention.end:clause.end])
        if not found:
            continue
        cue_start = mention.end + len(found.group("lead"))
        if any(mention.end <= other.start < cue_start for other in inside[index + 1:index + 1 + _FILLER_WORDS + 1]):
            continue
        flagged.add(index)
        inside_parentheses = folded.count("(", clause.start, cue_start) > folded.count(")", clause.start, cue_start)
        cursor = index
        while cursor > 0:
            gap = folded[inside[cursor - 1].end:inside[cursor].start]
            if (inside_parentheses and "(" in gap) or not _linked(folded, clause.start, inside[cursor - 1], inside[cursor]):
                break
            cursor -= 1
            flagged.add(cursor)
    return flagged


def _status_of(clause: _Clause, folded: str, mention: Mention, rules: _JdRules) -> str:
    before = folded[clause.start:mention.start]
    reach = before[-_NEGATION_REACH:]
    negation = rules.negation.search(reach)
    if negation and not re.search(r"[,;]|\bnhung\b|\bbut\b", reach[negation.end():]):
        return "ignore"
    major = rules.major_cue.search(before[-_MAJOR_REACH:])
    if major and mention.entry.kind == "concept" and not re.search(r"[;.]", before[-_MAJOR_REACH:][major.end():]):
        return "ignore"
    after = re.split(r"[,;]", folded[mention.end:min(clause.end, mention.end + _NEGATION_REACH)])[0]
    denied = rules.negation_after.search(after)
    if denied:
        return "preferred" if rules.softener.search(after[:denied.start()]) else "ignore"
    return "preferred" if rules.prefer_before.search(before[-_PREFER_REACH:]) else clause.section


def _statuses(clauses: list[_Clause], folded: str, mentions: list[Mention], starts: list[int],
              rules: _JdRules) -> list[tuple[Mention, str]]:
    """required | preferred for every mention that is a real requirement (negated or ignored ones drop out)."""
    result: list[tuple[Mention, str]] = []
    for clause in clauses:
        if clause.section == "ignore":
            continue
        inside = [m for m in _mentions_in(mentions, starts, clause.start, clause.end)
                  if m.end <= clause.end and m.entry.kind in _JD_KINDS]
        by_suffix = _preferred_by_suffix(clause, folded, inside, rules)
        for index, mention in enumerate(inside):
            status = _status_of(clause, folded, mention, rules)
            if status != "ignore":
                result.append((mention, "preferred" if status == "preferred" or index in by_suffix else "required"))
    return result


def _unique(entries: Iterable[Entry]) -> list[Entry]:
    return list(dict.fromkeys(entries))


def _years_value(match: re.Match[str]) -> float:
    raw = match.group("low")
    number = float(_NUMBER_WORDS[raw]) if raw in _NUMBER_WORDS else float(raw.replace(",", "."))
    if match.group("unit") in {"thang", "month", "months"}:
        number = round(number / 12 * 2) / 2     # months are kept as half years: 6 tháng = 0.5, 18 months = 1.5
    return number


def _minimum_years(clauses: list[_Clause], folded: str, mentions: list[Mention], starts: list[int],
                   rules: _JdRules) -> float:
    """First stated experience floor (a range gives its lower end); skill-scoped mentions rank last.

    A number of years counts when the clause talks about experience, carries a qualifier ("at least", "+",
    "từ"), or at least is not about something else (company age, contract length, "the last 3 years")."""
    scoped: list[float] = []
    unscoped: list[float] = []
    for clause in clauses:
        if clause.section != "required":
            continue
        text = folded[clause.start:clause.end]
        cue = rules.prefer_before.search(text)
        scan = text[:cue.start()] if cue else text
        has_cue = bool(rules.experience_cue.search(scan))
        for match in _YEARS.finditer(scan):
            around = scan[max(0, match.start() - _DENIED_REACH):match.end() + _DENIED_REACH]
            qualified = bool(match.group("plus") or match.group("plus2") or match.group("post")
                             or rules.years_qualifier.search(scan[max(0, match.start() - 20):match.start()]))
            if rules.prefer_after.search(scan[match.end():]) or not (has_cue or qualified or not rules.years_denied.search(around)):
                continue
            value = _years_value(match)
            if value > _MAX_YEARS:
                continue
            tail = clause.start + match.end()
            near = _mentions_in(mentions, starts, tail, tail + _SKILL_SCOPE + 1)
            (scoped if any(m.entry.kind in TECH_KINDS for m in near) else unscoped).append(value)
    found = unscoped or scoped
    return found[0] if found else 0.0


def _head_lines(text: str, rules: _JdRules) -> str:
    """Title-like part of a JD: the first line plus any 'Vị trí: ...' style line near the top."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    picks = [lines[0], *[line for line in lines[1:12] if rules.title_cue.search(fold(line)[:40])]]
    return " . ".join(picks)[:240]


def _jd_level(text: str, rules: _JdRules) -> Entry | None:
    found = detect_level(_head_lines(text, rules), highest=False)
    if found:
        return found
    for sentence in re.finditer(r"[^\n.]+", text[:600]):
        if rules.hiring_cue.search(fold(sentence.group(0))) and (found := detect_level(sentence.group(0), highest=False)):
            return found
    return None


def _split_title_concepts(mentions: list[Mention], text: str) -> tuple[list[Mention], list[Entry]]:
    """'Cloud Engineer', 'QA Engineer', 'BI Analyst': a concept named only in the title describes the role,
    not a requirement, so it is not emitted as a skill. It still tells the category. Tools, languages and
    frameworks in the title are kept as skills."""
    lines = [m for m in re.finditer(r"[^\n]+", text) if m.group(0).strip()]
    if len(lines) < 2 or len(lines[0].group(0)) > _TITLE_MAX:
        return mentions, []
    first_line_end = lines[0].end()
    later = {m.entry for m in mentions if m.start >= first_line_end}
    title_only = [m for m in mentions
                  if m.start < first_line_end and m.entry not in later and m.entry.kind in {"concept", "methodology"}]
    return [m for m in mentions if m not in title_only], _unique(m.entry for m in title_only)


def _refine(draft: dict[str, Any], refined: dict[str, Any]) -> dict[str, Any]:
    """Merge provider output into the draft; skill names are normalised through the catalog."""
    catalog = load_catalog()
    merged = {**draft, **{key: value for key, value in refined.items() if key in draft}}
    for key in ("required", "preferred"):
        names: list[str] = []
        for value in merged.get(f"{key}_skills") or []:
            entry = catalog.resolve(str(value))
            name = entry.name if entry else str(value).strip()
            if name and name not in names:
                names.append(name)
        merged[f"{key}_skills"] = names
        merged[f"{key}_skill_ids"] = [entry.id for name in names if (entry := catalog.resolve(name))]
    return merged


def _unique_names(entries: Iterable[Entry]) -> list[str]:
    return [entry.name for entry in _unique(entries)]


def extract_jd_requirements(text: str, provider: ExtractionProvider | None = None) -> dict[str, Any]:
    """Rules-based reading of a job description (Vietnamese, English or mixed).

    Returns required_skills / preferred_skills (canonical names in reading order, certifications included) with their *_ids,
    minimum_experience (years; months become half years, so "6 tháng" is 0.5), category and
    secondary_category (codes or None), level, education (the lowest degree that is required), languages
    (other than Vietnamese), certifications, domains and soft_skills. `provider` may refine the draft.
    """
    text = text[:_MAX_CV_CHARS]
    rules = _jd_rules()
    folded = fold(text)
    mentions, title_concepts = _split_title_concepts(find_mentions(text), text)
    starts = [m.start for m in mentions]
    clauses = _split_clauses(text, folded, mentions, starts, rules)
    graded = _statuses(clauses, folded, mentions, starts, rules)
    skills = [(m.entry, status) for m, status in graded if m.entry.kind in TECH_KINDS | {"certification"}]
    required = _unique(entry for entry, status in skills if status == "required")
    preferred = [entry for entry in _unique(entry for entry, status in skills if status == "preferred")
                 if entry not in required]
    usable = " ".join(text[c.start:c.end] for c in clauses if c.section != "ignore")
    minimum = _minimum_years(clauses, folded, mentions, starts, rules)
    level = _jd_level(text, rules)
    educations = [m.entry for m, status in graded if m.entry.kind == "education" and status == "required"
                  and not _in_progress(folded, m, rules)]
    ranking = rank_categories(_head_lines(text, rules), usable, [*_unique(entry for entry, _ in skills if entry.kind in TECH_KINDS), *title_concepts])
    draft: dict[str, Any] = {
        "required_skills": [entry.name for entry in required],
        "preferred_skills": [entry.name for entry in preferred],
        "required_skill_ids": [entry.id for entry in required],
        "preferred_skill_ids": [entry.id for entry in preferred],
        "minimum_experience": int(minimum) if minimum == int(minimum) else minimum,
        "category": ranking[0] if ranking else None,
        "secondary_category": ranking[1] if len(ranking) > 1 else None,
        "level": level.name if level else None,
        "education": min(educations, key=lambda entry: entry.rank or 0).name if educations else None,
        "languages": [name for name in detect_languages(usable) if name != "Vietnamese"],
        "certifications": _unique_names(m.entry for m, _ in graded if m.entry.kind == "certification"),
        "domains": detect_domains(usable),
        "soft_skills": [entry.name for entry in _ranked(usable, "soft_skill")],
        "catalog_version": catalog_version(),
    }
    return _refine(draft, provider.refine_jd(text, draft)) if provider else draft


def _in_progress(folded: str, mention: Mention, rules: _JdRules) -> bool:
    """'pursuing', 'đang học', '(incomplete)' next to a degree, within the same comma-separated phrase."""
    before = re.split(r"[,;.\n]", folded[max(0, mention.start - 30):mention.start])[-1]
    after = re.split(r"[,;.\n]", folded[mention.end:mention.end + 30])[0]
    return bool(rules.in_progress.search(before + " | " + after))


def detect_for_query(query: str) -> str | None:
    """Category a free-text search query clearly targets: the same reading a job description gets."""
    return extract_jd_requirements(query)["category"]
