import re
import sys
import time
import unicodedata
import requests
import bibtexparser
from pathlib import Path

ORCID = "0000-0001-9421-5480"
BIB_RAW = Path(__file__).resolve().parents[1] / "_bibliography" / "papers.raw.bib"

# What InspireHEP adds to a preprint's BibTeX once the paper is published
# (month is dropped: it described the arXiv posting, not the publication).
PUBLISHED_FIELDS = ("doi", "journal", "volume", "number", "pages", "year")
DROPPED_ON_PUBLISH = ("month",)


def fetch_inspire_bai(orcid):
    """Resolve an ORCID to InspireHEP's own author identifier (BAI, e.g.
    "D.Sathyan.1"). InspireHEP's literature search indexes authors by BAI,
    not by ORCID - querying "a <orcid>" silently matches nothing, since it's
    treated as a literal (and non-matching) author-name search rather than
    an identifier lookup."""
    r = requests.get(f"https://inspirehep.net/api/orcid/{orcid}", timeout=10)
    r.raise_for_status()
    ids = r.json().get("metadata", {}).get("ids", [])
    for identifier in ids:
        if identifier.get("schema") == "INSPIRE BAI":
            return identifier["value"]
    raise RuntimeError(f"No INSPIRE BAI found for ORCID {orcid}")


def fetch_inspire_bibtex(literature_id):
    url = f"https://inspirehep.net/api/literature/{literature_id}"
    r = requests.get(url, timeout=10)
    if r.status_code != 200:
        return None
    data = r.json()
    arxiv = data.get("metadata", {}).get("arxiv_eprints", [{}])[0].get("value")
    doi = data.get("metadata", {}).get("dois", [{}])[0].get("value")

    # fetch bibtex from inspire
    bib_url = f"https://inspirehep.net/api/literature/{literature_id}?format=bibtex"
    r2 = requests.get(bib_url, timeout=10)
    if r2.status_code != 200:
        return None
    return r2.text


def is_published_on_inspire(meta):
    return any(p.get("journal_title") for p in meta.get("publication_info", []))


def edit_entry_block(block, updates, remove=()):
    """Return one raw BibTeX entry with `updates` set (replacing a field of
    the same name, else appended) and `remove` fields dropped.

    Works on the entry's text rather than re-serializing the whole file, so
    hand-added fields (cv_exclude, preview, ...), field order, and the file's
    existing formatting are left alone."""
    lines = block.rstrip().split("\n")
    head, body, tail = lines[0], lines[1:-1], lines[-1]

    fields = []  # [name, [lines]]; continuation lines belong to the previous field
    for line in body:
        m = re.match(r"^\s+([A-Za-z_]+)\s*=", line)
        if m:
            fields.append([m.group(1), [line]])
        elif fields:
            fields[-1][1].append(line)
    for _, field_lines in fields:
        field_lines[-1] = field_lines[-1].rstrip().rstrip(",")

    drop = {name.lower() for name in remove}
    fields = [f for f in fields if f[0].lower() not in drop]

    # Updated fields go at the end in the order given (matching InspireHEP's
    # own BibTeX), even when the field already existed - e.g. "year" moves
    # from after "reportNumber" to after "pages".
    replaced = {name.lower() for name in updates}
    fields = [f for f in fields if f[0].lower() not in replaced]
    for name, value in updates.items():
        fields.append([name, [f'    {name} = "{value}"']])

    return head + "\n" + ",\n".join("\n".join(f[1]) for f in fields) + "\n" + tail


def upgrade_entry_in_text(text, texkey, updates, remove=DROPPED_ON_PUBLISH):
    pattern = re.compile(rf"^@\w+\{{{re.escape(texkey)},\n(?:.*\n)*?^\}}[ \t]*$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        raise RuntimeError(f"Entry {texkey} not found in {BIB_RAW.name}")
    return text[: match.start()] + edit_entry_block(match.group(0), updates, remove) + text[match.end():]


def published_updates_from_inspire(literature_id):
    """Fields to add to an existing preprint entry, from InspireHEP's BibTeX
    for the (now published) record; None if it has no journal info yet."""
    bibtex = fetch_inspire_bibtex(literature_id)
    if not bibtex:
        return None
    entries = bibtexparser.loads(bibtex).entries
    if not entries or not entries[0].get("journal"):
        return None
    return {k: entries[0][k] for k in PUBLISHED_FIELDS if entries[0].get(k)}


# InspireHEP can lag a journal's publication by weeks, but Crossref has the
# paper as soon as the publisher registers it. Crossref names journals in
# full and splits volume/issue differently than InspireHEP's BibTeX, so the
# journals we can translate faithfully are listed explicitly; anything else
# is left for InspireHEP to fill in rather than guessed at.
#   "monthly": Crossref volume=year, issue=month; InspireHEP volume=month
#              (JHEP 10 (2026) 207)
#   "plain":   volume and article number carry over as they are
#   "aps":     like "plain", plus the issue as `number`
CROSSREF_JOURNALS = {
    "Journal of High Energy Physics": ("JHEP", "monthly"),
    "Journal of Cosmology and Astroparticle Physics": ("JCAP", "monthly"),
    "Physics Letters B": ("Phys. Lett. B", "plain"),
    "Nuclear Physics B": ("Nucl. Phys. B", "plain"),
    "Physical Review D": ("Phys. Rev. D", "aps"),
    "Physical Review Letters": ("Phys. Rev. Lett.", "aps"),
    "Physical Review C": ("Phys. Rev. C", "aps"),
    "Physical Review Research": ("Phys. Rev. Res.", "aps"),
    "Physical Review Accelerators and Beams": ("Phys. Rev. Accel. Beams", "aps"),
}
CROSSREF_WORKS = "https://api.crossref.org/works"


def normalize_text(text):
    text = re.sub(r"<[^>]+>", "", text or "")  # markup in Crossref titles
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower())


def crossref_works(**params):
    """Items from a Crossref works query; [] if Crossref is unreachable (a
    flaky lookup must never fail the daily sync, but it is logged so an
    outage doesn't look like "nothing published"). Crossref's public pool
    allows about one request per second, so calls are spaced out and a
    rate-limited one is retried."""
    for attempt in range(3):
        time.sleep(1.1)
        try:
            r = requests.get(CROSSREF_WORKS, params={"rows": 5, **params}, timeout=15)
            if r.status_code == 429:
                continue
            r.raise_for_status()
            return r.json()["message"]["items"]
        except (requests.RequestException, ValueError, KeyError) as exc:
            print(f"  ! Crossref lookup failed: {exc}", file=sys.stderr)
            return []
    print("  ! Crossref lookup rate-limited; skipping for this run", file=sys.stderr)
    return []


def crossref_to_bib_fields(item):
    """BibTeX fields for a Crossref journal-article, in InspireHEP's
    conventions; None if the journal isn't one we translate or the volume and
    article number haven't been assigned yet (online-first)."""
    journal, kind = CROSSREF_JOURNALS.get((item.get("container-title") or [""])[0], (None, None))
    if not journal:
        return None

    volume = str(item.get("volume") or "").strip()
    issue = str(item.get("issue") or "").strip()
    pages = str(item.get("article-number") or item.get("page") or "").strip()
    printed = (item.get("published-print") or item.get("issued") or {}).get("date-parts", [[None]])[0][0]
    if not (volume and pages and printed):
        return None

    fields = {"doi": item["DOI"]}
    if kind == "monthly":
        if not (issue.isdigit() and volume.isdigit()):
            return None
        fields.update(journal=journal, volume=f"{int(issue):02d}", pages=pages.zfill(3), year=volume)
        if journal == "JHEP":  # keep the publisher's canonical casing
            fields["doi"] = f"10.1007/JHEP{int(issue):02d}({volume}){pages.zfill(3)}"
    else:
        fields.update(journal=journal, volume=volume, pages=pages, year=str(printed))
        if kind == "aps" and issue:
            fields["number"] = issue
    return {k: fields[k] for k in PUBLISHED_FIELDS if k in fields}


def published_updates_from_crossref(entry):
    """Fields to add to an existing preprint entry, from Crossref; None if
    Crossref has no published version of it yet.

    Primary match is the publisher's own link between the journal article and
    the arXiv preprint. Not every publisher deposits that link, so a second
    pass accepts an identical title plus the same first author."""
    arxiv = entry.get("eprint", "").replace("arXiv:", "").strip()
    items = []
    if arxiv:
        items = crossref_works(filter=f"type:journal-article,relation.type:has-preprint,relation.object:{arxiv}")

    if not items and entry.get("title"):
        first_author = normalize_text(entry.get("author", "").split(" and ")[0].split(",")[0])
        title = normalize_text(entry["title"])
        for item in crossref_works(**{"query.bibliographic": f"{entry['title'].replace('{', '').replace('}', '')} {first_author}", "filter": "type:journal-article"}):
            families = {normalize_text(a.get("family")) for a in item.get("author", [])}
            if normalize_text((item.get("title") or [""])[0]) == title and first_author in families:
                items = [item]
                break

    for item in items:
        fields = crossref_to_bib_fields(item)
        if fields:
            return fields
    return None


def main():
    # load existing bib
    existing_keys = set()
    existing_texkeys = set()
    existing_entries = []
    if BIB_RAW.exists():
        with open(BIB_RAW) as f:
            db = bibtexparser.load(f)
        existing_entries = db.entries
        existing_keys = {
            e.get("eprint", "").replace("arXiv:", "").strip()
            for e in existing_entries
        } | {e.get("doi", "").strip() for e in existing_entries}
        existing_keys.discard("")
        # Entries with neither an eprint nor a doi (e.g. the phdthesis entry)
        # can never match on existing_keys above, so they'd always look
        # "new" without this - texkeys are Inspire's own, always-present
        # citation key, so they catch what arxiv/doi dedup misses.
        existing_texkeys = {e.get("ID", "").strip() for e in existing_entries}
        existing_texkeys.discard("")

    # Preprints we already have that could later be published
    # (an @article with no journal yet).
    preprints_by_texkey = {
        e["ID"]: e
        for e in existing_entries
        if e.get("ENTRYTYPE") == "article" and not e.get("journal") and e.get("ID")
    }

    # fetch all papers from inspire for this author
    bai = fetch_inspire_bai(ORCID)
    url = "https://inspirehep.net/api/literature"
    r = requests.get(url, params={"sort": "mostrecent", "size": 100, "q": f"a {bai}"}, timeout=15)
    hits = r.json().get("hits", {}).get("hits", [])

    new_bibtex_blocks = []
    upgrades = []  # (texkey, title, updates, source)
    for hit in hits:
        meta = hit.get("metadata", {})
        lit_id = hit.get("id")
        title = meta.get("titles", [{}])[0].get("title", lit_id)
        arxiv = meta.get("arxiv_eprints", [{}])[0].get("value", "")
        doi = meta.get("dois", [{}])[0].get("value", "")
        texkeys = set(meta.get("texkeys", []))

        # A preprint we already track that Inspire now lists as published:
        # pull in its journal/volume/pages/doi so it moves from "Preprints"
        # to "Publications" everywhere.
        preprint_key = next(iter(texkeys & preprints_by_texkey.keys()), None)
        if preprint_key and is_published_on_inspire(meta):
            updates = published_updates_from_inspire(lit_id)
            if updates:
                upgrades.append((preprint_key, title, updates, "InspireHEP"))
            continue

        # skip if already in bib
        if arxiv in existing_keys or doi in existing_keys or texkeys & existing_texkeys:
            continue

        bibtex = fetch_inspire_bibtex(lit_id)
        if bibtex:
            new_bibtex_blocks.append(bibtex)
            print(f"  + added: {title}")

    # Preprints InspireHEP hasn't caught up on yet: ask Crossref directly.
    upgraded = {texkey for texkey, *_ in upgrades}
    for texkey, entry in preprints_by_texkey.items():
        if texkey in upgraded:
            continue
        updates = published_updates_from_crossref(entry)
        if updates:
            upgrades.append((texkey, entry.get("title", texkey).strip("{}"), updates, "Crossref"))

    if upgrades:
        text = BIB_RAW.read_text()
        for texkey, title, updates, source in upgrades:
            text = upgrade_entry_in_text(text, texkey, updates)
            print(f"  ^ now published: {title} ({updates.get('journal')} {updates.get('volume', '')} ({updates.get('year', '')}) {updates.get('pages', '')}) [via {source}]")
        BIB_RAW.write_text(text)

    if new_bibtex_blocks:
        # append to raw bib
        with open(BIB_RAW, "a") as f:
            for block in new_bibtex_blocks:
                f.write("\n\n")
                f.write(block.strip())
        print(f"{len(new_bibtex_blocks)} new paper(s) added to papers.raw.bib")

    if upgrades:
        print(f"{len(upgrades)} preprint(s) updated to published in papers.raw.bib")

    if not new_bibtex_blocks and not upgrades:
        print("No new papers found.")


if __name__ == "__main__":
    main()
