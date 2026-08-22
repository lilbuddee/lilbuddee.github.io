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
        "date": build_date(e),
        "journal": str(e.get("journal") or ""),
        "url": clean_url(e, arxiv_id),
        "eprint": arxiv_id,
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

def rendercv_publication(p):
    return {
        "title": p["title"],
        "authors": p["authors"],
        "date": p["date"],
        "journal": p["journal"] or None,
        "url": p["url"],
    }

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

# date order
publications_export = [
    rendercv_publication(p)
    for p in sorted(
        pubs,
        # key=lambda x: x["date"] or "",
        key=lambda x: str(x.get("date") or ""),
        reverse=True,
    )
]

cv = {}
if CV_PATH.exists():
    with open(CV_PATH, "r") as f:
        cv = yaml.safe_load(f) or {}

cv.setdefault("cv", {})
cv["cv"].setdefault("sections", {})

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
    # "Selected Publications": selected_export,
    "Publications": publications_export,
    "Seminars": old_sections.get("Seminars", []),
    "Conference Talks": old_sections.get("Conference Talks", []),
    "Conferences Organized": old_sections.get("Conferences Organized", []),
    "Awards": old_sections.get("Awards", []),
    "Schools Attended": old_sections.get("Schools Attended", []),
    "Skills": old_sections.get("Skills", []),
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