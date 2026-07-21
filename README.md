# lilbuddee.github.io

Source for [Deepak Sathyan's](https://lilbuddee.github.io) personal academic site — CV, publications, projects, and blog. Built on [al-folio](https://github.com/alshedivat/al-folio) v1.x, a gem-based Jekyll starter: this repo owns content and site wiring, while layouts/includes/styles/runtime behavior live in versioned `al_folio_*` / `al_*` gems (see [AGENTS.md](AGENTS.md) and [docs/BOUNDARIES.md](docs/BOUNDARIES.md) for the full ownership split — useful background if you're a coding agent working in this repo).

## Layout

- `_pages/` — top-level pages: `about.md`, `cv.md`, `publications.md`, `projects.md`, `blog.md`, `news.md`.
- `_posts/`, `_projects/`, `_news/` — blog posts, project pages, news items.
- `_bibliography/papers.raw.bib` — hand-maintained source bibliography. `_bibliography/papers.bib` is generated from it (citation IDs, `selected` flags) and shouldn't be hand-edited.
- `_data/cv.raw.yml` — hand-maintained CV source (education, experience, awards, talks, etc). `_data/cv.yml` and `_data/publications.yml` are generated from it plus the bibliography.
- `_data/citations.yml` — cached InspireHEP citation counts, keyed by BibTeX citation key.
- `_data/socials.yml`, `_data/venues.yml`, `_data/coauthors.yml` — supporting data (contact/social links, venue metadata, coauthor profile links used on the publications page).
- `assets/rendercv/` — RenderCV config (`design.yaml`, `locale.yaml`, `settings.yaml`) and its generated output (`rendercv_output/`), which is what the CV page's PDF download links to.
- `_includes/cv/`, `_layouts/bib.liquid`, `_sass/_themes.scss` — local overrides of gem-owned files, tracked in `.al-folio-overrides.yml` so upstream drift can be flagged on gem updates. Prefer porting a fix upstream to the owning gem over growing this list.
- `_config.yml` — site metadata, plugin list, and feature flags.

## Automation (`.github/workflows/`)

- `fetch-papers.yml` — weekly, pulls new papers by ORCID from the InspireHEP API into `papers.raw.bib` (`scripts/import requests.py`).
- `update-publications.yml` — on push to `papers.raw.bib` or `cv.raw.yml`, regenerates `papers.bib`, `cv.yml`, and `publications.yml` (`_data/make_publications.py`).
- `update-citations.yml` — Mon/Wed/Fri, refreshes `_data/citations.yml` from InspireHEP (`scripts/fetch_inspire_citations.py`).
- `render-cv.yml` — on push to `cv.yml`/`cv.raw.yml`/RenderCV config, re-renders the CV PDF/HTML/Markdown into `assets/rendercv/rendercv_output/`.
- `deploy.yml` — builds and publishes the site to GitHub Pages.
- `unit-tests.yml`, `visual-regression.yml`, `upgrade-check.yml`, `prettier.yml`, `axe.yml`, `broken-links*.yml`, `codeql.yml`, `lighthouse-badger.yml` — CI checks (style contract, integration tests, visual diffing, accessibility, dead links, upgrade audit, formatting).

## Local development

```bash
bundle install
bundle exec jekyll serve                      # http://localhost:4000
bundle exec jekyll build --baseurl /al-folio   # production-style build
```

See [AGENTS.md](AGENTS.md) for the fuller validated command set (linting, integration tests, Docker serving) and [docs/](docs/) for setup/customization guides inherited from the al-folio starter.
