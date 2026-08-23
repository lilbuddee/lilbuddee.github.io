import bibtexparser
import yaml
import math
import json
import re
import requests
from pathlib import Path
from datetime import datetime
from bibtexparser.bwriter import BibTexWriter

# =========================
# PATHS
# =========================
BASE_DIR = Path(__file__).resolve().parents[1]

BIB_RAW = BASE_DIR / "_bibliography" / "papers.raw.bib"
BIB_OUT = BASE_DIR / "_bibliography" / "papers.bib"

CV_PATH = BASE_DIR / "_data" / "cv.raw.yml"
ARTICLES_PATH = BASE_DIR / "_data" / "articles.yml"
SNOWMASS_PATH = BASE_DIR / "_data" / "snowmass2021.yml"
MUONG2_PATH = BASE_DIR / "_data" / "muong2.yml"
OUTPUT_PUBS = BASE_DIR / "_data" / "publications.yml"
OUTPUT_CV_FINAL = BASE_DIR / "_data" / "cv.yml"

CACHE_PATH = BASE_DIR / "_data" / ".cache" / "inspire.json"
CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

CURRENT_YEAR = datetime.now().year

# def build_date(entry):
#     year = str(entry.get("year") or "").strip()
#     month = str(entry.get("month") or "").strip()

#     if year and month.isdigit():
#         return f"{year}-{int(month):02d}"

#     return year

def build_date(entry):
    year = str(entry.get("year") or "").strip()
    month = str(entry.get("month") or "").strip()

    if year and month.isdigit():
        return f"{year}-{int(month):02d}"

    return int(year) if year else None


def arxiv_submission_date(arxiv_id):
    """YYMM.NNNNN arXiv IDs (2007+) encode the submission year/month in their
    first 4 digits - use that for sorting/display instead of the bibtex
    year/month, which for a published article reflect acceptance/publication
    date, not when the work was actually posted."""
    if not arxiv_id or len(arxiv_id) < 4 or not arxiv_id[:4].isdigit():
        return None
    year, month = 2000 + int(arxiv_id[:2]), int(arxiv_id[2:4])
    if not 1 <= month <= 12:
        return None
    return f"{year}-{month:02d}"


# =========================
# CACHE
# =========================
def load_cache():
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text())
    except:
        return {}


def save_cache(cache):
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))


cache = load_cache()


# =========================
# CLEAN TITLE
# =========================
def clean_braces(s):
    if not s:
        return ""
    s = str(s).strip()

    while len(s) > 1 and (
        (s.startswith("{") and s.endswith("}")) or
        (s.startswith('"') and s.endswith('"'))
    ):
        s = s[1:-1].strip()

    return s


# =========================
# AUTHOR NORMALIZATION (FIXED)
# =========================
_TRUNCATED_AUTHORS_RE = re.compile(r"\s+and\s+others\b|\s+et\s+al\.?\b", re.IGNORECASE)


def clean_author_string(s):
    if not s:
        return ""

    s = str(s)
    s = s.replace("\n", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def is_truncated_author_list(s):
    """True for BibTeX author strings truncated to a single name, e.g. 'Bose, Tulika and others'."""
    return bool(_TRUNCATED_AUTHORS_RE.search(s))


def format_author_name(name):
    if "," in name:
        last, first = [x.strip() for x in name.split(",", 1)]
    else:
        parts = name.split()
        first = parts[0] if parts else ""
        last = " ".join(parts[1:]) if len(parts) > 1 else ""

    initials = "".join([f"{p[0]}." for p in first.split() if p])
    return f"{initials} {last}".strip()


def extract_first_author(raw):
    """Pull the name preceding 'and others' / 'et al.' out of a truncated author string."""
    first = _TRUNCATED_AUTHORS_RE.split(raw, maxsplit=1)[0].strip()
    return first or raw.strip()


# =========================
# CANONICAL ID
# =========================
def paper_id(entry):
    if entry.get("doi"):
        return f"doi:{entry['doi']}"
    if entry.get("eprint"):
        return f"arxiv:{entry['eprint']}"
    return f"title:{clean_braces(entry.get('title'))}"


# =========================
# SAFE URL
# =========================
def clean_url(entry, arxiv_id):
    url = entry.get("url")
    if isinstance(url, str) and url.strip():
        return url.strip()
    if arxiv_id:
        return f"https://arxiv.org/abs/{arxiv_id}"
    return None


# =========================
# COLLABORATION FIELD (BIBTEX ONLY)
# =========================
def get_collaboration(entry):
    collab = entry.get("collaboration")

    if not collab:
        return None

    collab = clean_braces(collab)

    if not collab.lower().endswith("collaboration"):
        collab += " Collaboration"

    return collab


# =========================
# AUTHOR COUNT (FIXED)
# =========================
def get_author_count(entry, collab=None):
    raw = clean_author_string(entry.get("author", ""))

    # collaboration detected either way
    if collab or is_truncated_author_list(raw):
        return 1000

    authors = [a.strip() for a in raw.split(" and ") if a.strip()]
    return len(authors)


# =========================
# AUTHOR FORMATTER (FIXED)
# =========================
def format_authors(entry, collab=None):
    raw = clean_author_string(entry.get("author", ""))

    # Named collaboration (e.g. "Muon g-2" -> "Muon g-2 Collaboration"): use the
    # already-formatted name, not the raw bibtex field, or the "Collaboration"
    # suffix gets silently dropped.
    if collab:
        return [collab]

    # Truncated author list with no named collaboration (e.g. Snowmass reports
    # like "Bose, Tulika and others"): cite as "First Author et al.", not the
    # literal string "Collaboration".
    if is_truncated_author_list(raw):
        first_author = extract_first_author(raw)
        return [f"{format_author_name(first_author)} et al."] if first_author else ["et al."]

    authors = [a.strip() for a in raw.split(" and ") if a.strip()]
    return [format_author_name(a) for a in authors]


# =========================
# INSPIRE (CITATIONS ONLY + CACHE)
# =========================
def fetch_inspire_citations(entry):

    doi = str(entry.get("doi") or "").strip()
    arxiv_id = str(entry.get("eprint") or "").replace("arXiv:", "").strip()

    cache_key = doi if doi else arxiv_id

    if not cache_key:
        return 0

    if cache_key in cache:
        return cache[cache_key].get("citations", 0)

    if doi:
        url = f"https://inspirehep.net/api/literature?q=doi:{doi}"
    else:
        url = f"https://inspirehep.net/api/literature?q=arxiv:{arxiv_id}"

    try:
        r = requests.get(url, timeout=10)

        if r.status_code != 200:
            cache[cache_key] = {"citations": 0}
            return 0

        data = r.json()
        hits = data.get("hits", {}).get("hits", [])

        if not hits:
            cache[cache_key] = {"citations": 0}
            return 0

        metadata = hits[0].get("metadata", {})
        citations = int(metadata.get("citation_count", 0))

        cache[cache_key] = {"citations": citations}
        return citations

    except Exception:
        cache[cache_key] = {"citations": 0}
        return 0

# =========================
# SCORING (FINAL)
# =========================
def score(p):
    citations = int(p.get("citations", 0))
    n = int(p.get("n_authors", 1))

    base = math.log1p(citations) * 5

    # HARD PENALTY FOR LARGE COLLABORATIONS
    if n > 10:
        author_penalty = 0.5
    else:
        author_penalty = 1.0

    journal = (p.get("journal") or "").lower()

    if "phys. rev. lett" in journal:
        field = 6.0
    elif "jhep" in journal or "prd" in journal or "phys. lett. b" in journal or "phys. rev. accel. beams" in journal:
        field = 3.0
    else:
        field = 1.0

    try:
        year = int(p.get("date"))
    except:
        year = 0

    recency = 5 if CURRENT_YEAR - year <= 2 else 0
    arxiv = 2 if p.get("eprint") else 0

    return base * author_penalty * field + recency + arxiv


# =========================
# LOAD BIB
# =========================
with open(BIB_RAW, "r") as f:
    bib_db = bibtexparser.load(f)


# =========================
# PIPELINE BUILD
# =========================
seen = set()
pubs = []

for e in bib_db.entries:

    uid = paper_id(e)
    if uid in seen:
        continue
    seen.add(uid)

    arxiv_id = str(e.get("eprint") or "").replace("arXiv:", "").strip()

    entry = {
        "uid": uid,
        "title": clean_braces(e.get("title")),
        "date": arxiv_submission_date(arxiv_id) or build_date(e),
        "journal": str(e.get("journal") or ""),
        "volume": clean_braces(e.get("volume")),
        "pages": clean_braces(e.get("pages")),
        "year": clean_braces(e.get("year")),
        "url": clean_url(e, arxiv_id),
        "eprint": arxiv_id,
        "is_phd_thesis": e.get("ENTRYTYPE") == "phdthesis",
        "citations": fetch_inspire_citations(e),
        "n_authors": 0,
        "authors": [],
        "raw": e,
    }

    collab = get_collaboration(e)
    
    entry["authors"] = format_authors(e, collab)
    entry["n_authors"] = get_author_count(e, collab)
    entry["score"] = float(score(entry))

    pubs.append(entry)


# =========================
# SORT + SELECT
# =========================
TOP_K = 5
RECENT_K = 2  # always-selected slots reserved for the most recent papers, preprints included

pubs_by_score = sorted(pubs, key=lambda x: -x["score"])
pubs_by_date = sorted(pubs, key=lambda x: str(x.get("date") or ""), reverse=True)

# The most recent papers (including preprints with no journal yet) always make
# the selected list, regardless of citation score - a brand-new preprint has
# had no time to accumulate citations, so score-only selection would bury it.
selected = set(p["uid"] for p in pubs_by_date[:RECENT_K])

for p in pubs_by_score:
    if len(selected) >= TOP_K:
        break
    selected.add(p["uid"])

pubs = pubs_by_score
for p in pubs:
    p["selected"] = "true" if p["uid"] in selected else "false"


# =========================
# UPDATE BIBTEX OUTPUT
# =========================
for p in pubs:
    e = p["raw"]
    uid = paper_id(e)

    if uid in selected:
        e["selected"] = "true"
    else:
        e.pop("selected", None)

# BibTexWriter defaults to alphabetizing entries by citation key, which
# discards date order entirely. selected_papers.liquid renders the
# "selected=true" query without a group/sort override, so it just follows
# whatever order the entries are written in here - write them most-recent
# first so the guaranteed-recent selections (see RECENT_K above) actually
# appear first on the page instead of wherever their texkey happens to sort.
bib_db.entries = [p["raw"] for p in pubs_by_date]

writer = BibTexWriter()
writer.indent = "    "
writer.comma_first = False
writer.order_entries_by = None

with open(BIB_OUT, "w") as f:
    f.write(writer.write(bib_db))


# =========================
# YAML OUTPUT
# =========================
def sanitize(obj):
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, str) and obj.strip() == "":
        return None
    return obj

def format_journal_citation(entry):
    """Full Inspire/arXiv-style citation, e.g. "Phys.Lett.B 868 (2025) 139765".
    Falls back to the bare journal name if volume/pages/year aren't all
    available (preprints, or a hand-maintained entry with no pipeline match)."""
    journal = str(entry.get("journal") or "").strip()
    if not journal:
        return None
    volume = str(entry.get("volume") or "").strip()
    pages = str(entry.get("pages") or "").strip()
    year = str(entry.get("year") or "").strip()
    if volume and pages and year:
        return f"{journal.replace(' ', '')} {volume} ({year}) {pages}"
    return journal


def rendercv_publication(p, number=None):
    title = p["title"]
    if number is not None:
        title = f"{number}. {title}"
    return {
        "title": title,
        "authors": p["authors"],
        "date": p["date"],
        "journal": format_journal_citation(p),
        "url": p["url"],
    }


def numbered_publications(pubs_unordered, newest_first=True):
    """Number entries by chronological rank - oldest is 1, newest is
    len(pubs) - independent of display order. newest_first=True displays
    newest-on-top (numbers count down from the top, the default for every
    subsection); newest_first=False displays oldest-on-top instead (numbers
    count up top-to-bottom, 1, 2, 3... - used for Articles in Preparation)."""
    ranked = sorted(pubs_unordered, key=lambda p: str(p.get("date") or ""))  # oldest first
    date_rank = {id(p): rank + 1 for rank, p in enumerate(ranked)}
    ordered = list(reversed(ranked)) if newest_first else ranked
    return [rendercv_publication(p, number=date_rank[id(p)]) for p in ordered]

# def rendercv_publication(p):
#     url = p["url"]
#     highlights = []
#     if url and "arxiv.org/abs/" in url:
#         arxiv_id = url.split("arxiv.org/abs/")[-1]
#         journal = p["journal"]
#         journal_str = f" ({journal})" if journal else ""
#         highlights.append(f"[arXiv:{arxiv_id}]({url}){journal_str}")

#     return {
#         "title": p["title"],
#         "authors": p["authors"],
#         "date": p["date"],
#         "journal": None,
#         "url": url,
#         "highlights": highlights or None,
#     }

# date order (now arXiv-submission-date order - see arxiv_submission_date())
pubs_date_order = sorted(pubs, key=lambda x: str(x.get("date") or ""), reverse=True)
publications_export = [rendercv_publication(p) for p in pubs_date_order]


# Hand-maintained publication-shaped YAML files (articles in preparation, plus
# the topical Snowmass2021/Muon g-2 cross-listings), already in the same shape
# rendercv_publication() produces. Reuse cv/publications.liquid on the website
# and rendercv's PublicationEntry on the PDF for both by keeping that field
# shape - entry type there is inferred structurally, not tied to the
# section's name.
def load_hand_maintained_publications(path):
    entries = []
    if path.exists():
        with open(path, "r") as f:
            entries = yaml.safe_load(f) or []
    for entry in entries:
        entry["authors"] = [name.strip() for name in entry.get("authors", [])]
    return entries


articles_in_prep = load_hand_maintained_publications(ARTICLES_PATH)

# Topical cross-listings, manually duplicated out of publications.yml by
# design: publications.yml keeps growing as new papers are added, so these
# two files are static snapshots the user curates by hand. A paper listed
# here is excluded from the refereed/review split below (see
# excluded_arxiv_ids) so it only shows up once, under its own topical section
# instead - rather than showing up a second time under refereed/review too.
snowmass_articles = load_hand_maintained_publications(SNOWMASS_PATH)
muong2_articles = load_hand_maintained_publications(MUONG2_PATH)


def arxiv_id_from_url(url):
    if url and "arxiv.org/abs/" in url:
        return url.split("arxiv.org/abs/")[-1].strip()
    return None


# The hand-maintained files above only carry a bare journal name (no volume/
# pages/year) and a hand-typed date rather than an arXiv-submission-date, so
# neither format_journal_citation() nor the newest-first sort would be
# accurate from them directly. Where a topical entry's arXiv ID matches a
# pipeline-derived paper, use that paper's data instead - full citation,
# arXiv-submission-date, and consistently formatted authors.
pubs_by_arxiv_id = {p["eprint"]: p for p in pubs if p.get("eprint")}


def prefer_canonical_source(entries):
    return [
        pubs_by_arxiv_id.get(arxiv_id_from_url(entry.get("url")), entry) for entry in entries
    ]


snowmass_articles = prefer_canonical_source(snowmass_articles)
muong2_articles = prefer_canonical_source(muong2_articles)

excluded_arxiv_ids = {
    arxiv_id_from_url(entry.get("url")) for entry in snowmass_articles + muong2_articles
} - {None}

# The Ph.D. thesis gets its own section rather than sitting in Articles in
# Refereed Journals alongside actual journal articles.
phd_thesis_pubs = [p for p in pubs_date_order if p.get("is_phd_thesis")]
excluded_uids = {p["uid"] for p in phd_thesis_pubs}

# "Articles in Review" = already on arXiv (in papers.raw.bib) but with no
# journal yet, i.e. not yet accepted/published.
pubs_for_refereed_review = [
    p
    for p in pubs_date_order
    if p.get("eprint") not in excluded_arxiv_ids and p["uid"] not in excluded_uids
]
refereed_export = numbered_publications([p for p in pubs_for_refereed_review if p["journal"]])
in_review_export = numbered_publications([p for p in pubs_for_refereed_review if not p["journal"]])

phd_thesis_export = numbered_publications(phd_thesis_pubs)
# Chronological, not reverse-chronological, per explicit request: oldest is
# "1" at the top, newest is the highest number at the bottom.
articles_in_prep_export = numbered_publications(articles_in_prep, newest_first=False)
snowmass_export = numbered_publications(snowmass_articles)
muong2_export = numbered_publications(muong2_articles)

cv = {}
if CV_PATH.exists():
    with open(CV_PATH, "r") as f:
        cv = yaml.safe_load(f) or {}

cv.setdefault("cv", {})
cv["cv"].setdefault("sections", {})

# Skills entries author "keywords" (a plain string, e.g. "Mathematica, Python,
# C++") rather than "summary". Neither the website's generic-section renderer
# nor rendercv's NormalEntry template know about "keywords" - both use
# "summary" to show a plain (non-bold) line under the bolded name - so map it
# across here rather than teaching two separate renderers a new field.
def with_keywords_as_summary(entries):
    result = []
    for entry in entries:
        entry = dict(entry)
        if "keywords" in entry:
            entry["summary"] = entry.pop("keywords")
        result.append(entry)
    return result

selected_export = [
    rendercv_publication(p)
    for p in pubs
    if p["selected"] == "true"
]

# cv["cv"]["sections"]["Publications"] = publications_export
# cv["cv"]["sections"]["Selected Publications"] = selected_export

# Replace the two assignment lines at the bottom with:
old_sections = cv["cv"].get("sections", {})
cv["cv"]["sections"] = {
    # " ": old_sections.get(" ", []),
    "Education": old_sections.get("Education", []),
    "Experience": old_sections.get("Experience", []),
    "Research Interests": old_sections.get("Research Interests", []),
    # "Selected Publications": selected_export,
    # Formerly one flat "Publications" section - now split into subsections
    # (RenderCV has no native concept of subsections nested under one shared
    # heading, so each is its own top-level section, kept adjacent in output
    # order to read as one grouped "Research Papers" block on both the
    # website and the PDF).
    "Articles in Review": in_review_export,
    "Articles in Refereed Journals": refereed_export,
    "Articles in Preparation": articles_in_prep_export,
    "Ph.D. Thesis": phd_thesis_export,
    "Snowmass2021 Contributions": snowmass_export,
    "Muon g-2 Articles": muong2_export,
    "Seminars": old_sections.get("Seminars", []),
    "Conference Talks": old_sections.get("Conference Talks", []),
    "Conferences Organized": old_sections.get("Conferences Organized", []),
    "Awards": old_sections.get("Awards", []),
    "Schools Attended": old_sections.get("Schools Attended", []),
    "Outreach": old_sections.get("Outreach", []),
    "Skills": with_keywords_as_summary(old_sections.get("Skills", [])),
    # "Projects" intentionally omitted - not displayed on the website CV or PDF CV.
    # Source data is still in cv.raw.yml under Projects if this is ever reversed.
}

class IndentedDumper(yaml.SafeDumper):
    pass

def str_presenter(dumper, data):
    if '\n' in data:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)

IndentedDumper.add_representer(str, str_presenter)

with open(OUTPUT_PUBS, "w") as f:
    yaml.dump(
        sanitize(publications_export),
        f,
        sort_keys=False,
        allow_unicode=True,
        indent=4,
        Dumper=IndentedDumper,
    )

with open(OUTPUT_CV_FINAL, "w") as f:
    yaml.dump(
        sanitize(cv),
        f,
        sort_keys=False,
        allow_unicode=True,
        indent=4,
        Dumper=IndentedDumper,
    )
    


save_cache(cache)