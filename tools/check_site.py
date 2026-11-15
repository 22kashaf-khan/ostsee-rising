"""
Safety check that runs before every deploy (see .github/workflows/pages.yml).
If anything is wrong, the deploy stops and the last working site stays online.

    python tools/check_site.py

Checks:
  1. every element app.js looks up with $("id") exists in index.html
  2. index.html loads app.js, style.css and intro.js with a ?v= version (so browsers never mix old and new files)
  3. every script and stylesheet index.html loads exists
  4. manifest.json is valid and every image it lists exists
"""
import json
import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"

html = (WEB / "index.html").read_text(encoding="utf-8")
js = (WEB / "app.js").read_text(encoding="utf-8")
problems = []

# 1. page elements the script needs (in index.html, or created by app.js itself, e.g. the popup chart)
html_ids = set(re.findall(r'id="([^"]+)"', html)) | set(re.findall(r'id="([^"]+)"', js))
for element_id in sorted(set(re.findall(r'\$\("([^"]+)"\)', js))):
    if element_id not in html_ids:
        problems.append(f'app.js uses $("{element_id}") but index.html has no id="{element_id}"')

# 2. versioned script and stylesheet
for name in ("app.js", "style.css", "intro.js"):
    if not re.search(rf'"{re.escape(name)}\?v=[^"]+"', html):
        problems.append(f"index.html must load {name} as {name}?v=dev (the deploy fills in the version)")

# 3. files the page loads must exist (e.g. the intro globe)
for src in re.findall(r'(?:src|href)="([^"?#:]+\.(?:js|css))', html):
    if not (WEB / src).exists():
        problems.append(f"index.html loads {src}, but that file is missing")
if "intro.js" in html and not (WEB / "data" / "globe-dots.json").exists():
    problems.append("intro.js needs data/globe-dots.json")

# 4. data
try:
    manifest = json.loads((WEB / "data" / "manifest.json").read_text(encoding="utf-8"))
    frames = manifest["frames"]
    if not frames:
        problems.append("manifest.json has no frames")
    for frame in frames:
        for path in frame["layers"].values():
            if not (WEB / "data" / path).exists():
                problems.append(f"{frame['date']}: missing image data/{path}")
except (OSError, ValueError, KeyError) as error:
    problems.append(f"manifest.json unreadable: {error}")

if problems:
    print("Site check FAILED - not deploying:")
    print("\n".join(f"  - {p}" for p in problems))
    sys.exit(1)
print(f"Site check OK: {len(html_ids)} page elements, {len(frames)} dates, all images present")
