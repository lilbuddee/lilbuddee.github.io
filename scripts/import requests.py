import re
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
    upgrades = []  # (texkey, title, updates)
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
                upgrades.append((preprint_key, title, updates))
            continue

        # skip if already in bib
        if arxiv in existing_keys or doi in existing_keys or texkeys & existing_texkeys:
            continue

        bibtex = fetch_inspire_bibtex(lit_id)
        if bibtex:
            new_bibtex_blocks.append(bibtex)
            print(f"  + added: {title}")

    if upgrades:
        text = BIB_RAW.read_text()
        for texkey, title, updates in upgrades:
            text = upgrade_entry_in_text(text, texkey, updates)
            print(f"  ^ now published: {title} ({updates.get('journal')} {updates.get('volume', '')} ({updates.get('year', '')}) {updates.get('pages', '')})")
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
