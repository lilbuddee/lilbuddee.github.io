import bibtexparser
import yaml
import requests
from pathlib import Path
from datetime import datetime

# =========================
# PATHS (repo-safe)
# =========================
BASE_DIR = Path(__file__).resolve().parents[1]

BIB_PATH = BASE_DIR / "_bibliography" / "papers.bib"
CV_PATH = BASE_DIR / "_data" / "cv.yml"
OUTPUT_PUBS = BASE_DIR / "_data" / "publications.yml"
OUTPUT_CV_FINAL = BASE_DIR / "_data" / "cv.final.yml"


# =========================
# CONFIG
# =========================
YOUR_LAST_NAME = "Sathyan"
MAX_AUTHORS = 6
CURRENT_YEAR = datetime.now().year


# =========================
# CLEAN FIELD
# =========================
def clean_bibtex_field(s):
    if not s:
        return s
    s = str(s).strip()

    while len(s) > 1 and (
        (s.startswith("{") and s.endswith("}")) or
        (s.startswith('"') and s.endswith('"'))
    ):
        s = s[1:-1].strip()

    return s


# =========================
# MONTH HANDLING
# =========================
MONTH_MAP = {
    "jan": "01", "january": "01",
    "feb": "02", "february": "02",
    "mar": "03", "march": "03",
    "apr": "04", "april": "04",
    "may": "05",
    "jun": "06", "june": "06",
    "jul": "07", "july": "07",
    "aug": "08", "august": "08",
    "sep": "09", "sept": "09", "september": "09",
    "oct": "10", "october": "10",
    "nov": "11", "november": "11",
    "dec": "12", "december": "12",
}


def get_month(entry):
    m = entry.get("month")
    if not m:
        return None

    m = str(m).strip().lower().replace("{", "").replace("}", "").replace('"', "")

    if m.isdigit():
        return f"{int(m):02d}"

    return MONTH_MAP.get(m)


# =========================
# ARXIV
# =========================
def get_arxiv_id(entry):
    eprint = entry.get("eprint")
    if not eprint:
        return None
    return str(eprint).replace("arXiv:", "").strip()


def get_arxiv_url(entry):
    arxiv_id = get_arxiv_id(entry)
    return f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None


def get_arxiv_year(entry):
    eprint = entry.get("eprint")
    if not eprint:
        return entry.get("year")

    try:
        yymm = str(eprint).replace("arXiv:", "").split(".")[0]
        return str(2000 + int(yymm[:2]))
    except:
        return entry.get("year")


# =========================
# AUTHORS
# =========================
def get_collaboration(entry):
    return entry.get("collaboration")


def clean_author_token(name: str):
    n = str(name).strip().lower()
    if n in ["others", "and others", "et al", "et al.", "et. al"]:
        return "et al."
    return str(name)


def format_author(name: str):
    name = str(name).strip()

    if "," in name:
        last, first = [x.strip() for x in name.split(",", 1)]
    else:
        parts = name.split()
        first = parts[0] if parts else ""
        last = " ".join(parts[1:]) if len(parts) > 1 else ""

    initials = "".join([f"{p[0]}." for p in first.split() if p])
    return f"{initials} {last}".strip()


def format_authors(entry):
    collab = get_collaboration(entry)
    if collab:
        return [str(collab)]

    authors_raw = entry.get("author", "")
    authors = [a.strip() for a in str(authors_raw).split(" and ") if a.strip()]

    out = []
    for a in authors:
        a = clean_author_token(a)

        if a == "et al.":
            out.append("et al.")
            break

        out.append(format_author(a))

    if len(out) > MAX_AUTHORS:
        out = out[:1] + ["et al."]

    return out


# =========================
# DATE
# =========================
def build_display_date(entry):
    year = entry.get("year")
    month = get_month(entry)

    if entry.get("eprint") and month:
        return f"{get_arxiv_year(entry)}-{month}"

    return str(year) if year else ""


def build_sort_date(entry):
    year = entry.get("year") or 0
    month = get_month(entry) or "00"

    try:
        year = int(year)
    except:
        year = 0

    return year * 100 + int(month)


# =========================
# SCORE (UPDATED RULES)
# =========================
def score(p):
    s = 0

    # journal boost
    if p.get("journal"):
        s += 3

    if p.get("journal") in ["Phys. Rev. Lett.", "Nature", "Science"]:
        s += 5

    # author count boost (NEW RULE)
    authors = p.get("authors", [])
    if isinstance(authors, list) and len(authors) <= 5:
        s += 2

    # recency boost (last 3 years)
    try:
        y = int(str(p.get("date", ""))[:4])
        if CURRENT_YEAR - y <= 3:
            s += 3
    except:
        pass

    # arXiv presence
    if p.get("eprint"):
        s += 1

    return s


# =========================
# CROSSREF (OPTIONAL)
# =========================
def fetch_crossref(doi):
    if not doi:
        return {}

    try:
        r = requests.get(f"https://api.crossref.org/works/{doi}", timeout=10)
        if r.status_code != 200:
            return {}

        msg = r.json()["message"]

        return {
            "journal": msg.get("short-container-title", [None])[0],
            "volume": msg.get("volume"),
            "pages": msg.get("page"),
            "year": (
                msg.get("published-print", {}).get("date-parts", [[None]])[0][0]
                or msg.get("published-online", {}).get("date-parts", [[None]])[0][0]
            ),
        }
    except:
        return {}


# =========================
# SAFE YAML SANITIZER (CRITICAL)
# =========================
def sanitize(obj):
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, (int, float)):
        return str(obj)
    return obj


# =========================
# MAIN
# =========================
with open(BIB_PATH, "r") as f:
    bib_db = bibtexparser.load(f)

seen = set()
pubs = []

for e in bib_db.entries:

    uid = e.get("doi") or e.get("eprint") or e.get("title")
    if uid in seen:
        continue
    seen.add(uid)

    doi = e.get("doi")
    crossref = fetch_crossref(doi)

    title = clean_bibtex_field(e.get("title"))

    url = (
        e.get("url")
        or (f"https://doi.org/{doi}" if doi else None)
        or get_arxiv_url(e)
    )

    entry = {
        "title": str(title),
        "authors": format_authors(e),
        "date": str(build_display_date(e)),   # 🔥 FORCE STRING
        "journal": str(e.get("journal") or crossref.get("journal") or "") or None,
        "volume": str(crossref.get("volume")) if crossref.get("volume") else None,
        "pages": str(crossref.get("pages")) if crossref.get("pages") else None,
        "url": str(url) if url else None,
        "eprint": get_arxiv_id(e),
    }

    entry["score"] = int(score(entry))
    entry["_sort"] = int(build_sort_date(e))

    pubs.append(entry)


# =========================
# SORT
# =========================
pubs = sorted(pubs, key=lambda x: -x["_sort"])

for p in pubs:
    p.pop("_sort", None)


# =========================
# SELECTED
# =========================
selected = sorted(pubs, key=lambda x: -x["score"])[:5]


# =========================
# SANITIZE EVERYTHING (FINAL SAFETY NET)
# =========================
pubs = sanitize(pubs)
selected = sanitize(selected)


# =========================
# WRITE OUTPUTS
# =========================
with open(OUTPUT_PUBS, "w") as f:
    yaml.dump(pubs, f, sort_keys=False, allow_unicode=True)

cv = {}
if CV_PATH.exists():
    with open(CV_PATH, "r") as f:
        cv = yaml.safe_load(f) or {}

cv.setdefault("cv", {})
cv["cv"].setdefault("sections", {})

cv["cv"]["sections"]["Publications"] = pubs
cv["cv"]["sections"]["Selected Publications"] = selected

with open(OUTPUT_CV_FINAL, "w") as f:
    yaml.dump(cv, f, sort_keys=False, allow_unicode=True)