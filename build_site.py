"""Embed data/stats.json into a single self-contained site/index.html (works from file://)."""
import json, os

ROOT = os.path.dirname(os.path.abspath(__file__))
out_path = os.path.join(ROOT, "data", "eliminated.txt")
out_teams = {l.strip().upper() for l in open(out_path, encoding="utf-8").read().splitlines()
             if l.strip() and not l.startswith("#")} if os.path.exists(out_path) else set()
data = json.load(open(os.path.join(ROOT, "data", "stats.json"), encoding="utf-8"))
data["players"] = [p for p in data["players"] if p["team"].upper() not in out_teams]
stats = json.dumps(data, ensure_ascii=False)
html = open(os.path.join(ROOT, "template.html"), encoding="utf-8").read().replace("/*__DATA__*/null", stats)
for d in ("site", "docs"):  # site/ for the local preview, docs/ is what GitHub Pages serves
    os.makedirs(os.path.join(ROOT, d), exist_ok=True)
    open(os.path.join(ROOT, d, "index.html"), "w", encoding="utf-8").write(html)
print("wrote site/index.html and docs/index.html")
