"""Embed data/stats.json into a single self-contained site/index.html (works from file://)."""
import json, os

ROOT = os.path.dirname(os.path.abspath(__file__))
stats = open(os.path.join(ROOT, "data", "stats.json"), encoding="utf-8").read()
html = open(os.path.join(ROOT, "template.html"), encoding="utf-8").read().replace("/*__DATA__*/null", stats)
for d in ("site", "docs"):  # site/ for the local preview, docs/ is what GitHub Pages serves
    os.makedirs(os.path.join(ROOT, d), exist_ok=True)
    open(os.path.join(ROOT, d, "index.html"), "w", encoding="utf-8").write(html)
print("wrote site/index.html and docs/index.html")
