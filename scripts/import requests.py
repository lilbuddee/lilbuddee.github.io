import requests
import bibtexparser
from bibtexparser.bwriter import BibTexWriter
from pathlib import Path

ORCID = "0000-0001-9421-5480"
BIB_RAW = Path(__file__).resolve().parents[1] / "_bibliography" / "papers.raw.bib"


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

    # fetch all papers from inspire for this author
    bai = fetch_inspire_bai(ORCID)
    url = "https://inspirehep.net/api/literature"
    r = requests.get(url, params={"sort": "mostrecent", "size": 100, "q": f"a {bai}"}, timeout=15)
    hits = r.json().get("hits", {}).get("hits", [])

    new_bibtex_blocks = []
    for hit in hits:
        meta = hit.get("metadata", {})
        lit_id = hit.get("id")
        arxiv = meta.get("arxiv_eprints", [{}])[0].get("value", "")
        doi = meta.get("dois", [{}])[0].get("value", "")
        texkeys = set(meta.get("texkeys", []))

        # skip if already in bib
        if arxiv in existing_keys or doi in existing_keys or texkeys & existing_texkeys:
            continue

        bibtex = fetch_inspire_bibtex(lit_id)
        if bibtex:
            new_bibtex_blocks.append(bibtex)
            print(f"  + added: {meta.get('titles', [{}])[0].get('title', lit_id)}")

    if not new_bibtex_blocks:
        print("No new papers found.")
        return

    # append to raw bib
    with open(BIB_RAW, "a") as f:
        for block in new_bibtex_blocks:
            f.write("\n\n")
            f.write(block.strip())

    print(f"{len(new_bibtex_blocks)} new paper(s) added to papers.raw.bib")

if __name__ == "__main__":
    main()