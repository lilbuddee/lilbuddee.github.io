import yaml

with open("cv.yml") as f:
    cv = yaml.safe_load(f)

with open("publications.yml") as f:
    pubs = yaml.safe_load(f)

cv["cv"]["sections"]["Publications"] = pubs

with open("cv.final.yml", "w") as f:
    yaml.dump(cv, f, sort_keys=False, allow_unicode=True)