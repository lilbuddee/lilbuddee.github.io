#!/usr/bin/env bash
# Render the three CV PDFs (full CV, publication list, CV without articles)
# plus their html/md previews. Mirrors the RenderCV step of
# .github/workflows/render-cv.yml - keep the two in sync - so a workflow that
# regenerates _data/cv*.yml (fetch-papers.yml) can re-render in the same run:
# a push made with the default GITHUB_TOKEN never triggers render-cv.yml.
set -euo pipefail
cd "$(dirname "$0")/.."

rendercv render _data/cv.yml --settings assets/rendercv/settings.yaml

# cv_publications.yml / cv_no_articles.yml carry their own
# settings.render_command block (distinct output paths per variant), so
# render them without --settings - passing it would replace their embedded
# settings wholesale and redirect all three variants to the same output files.
for variant in _data/cv_publications.yml _data/cv_no_articles.yml; do
  rendercv render "$variant"
done

rm -f assets/rendercv/rendercv_output/*.typ
