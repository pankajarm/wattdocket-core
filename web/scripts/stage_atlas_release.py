"""Stage the built atlas as a self-contained HTML entry point.

Run after `npm run build`. Inlining the Vite bundle keeps the page usable if a
separate static-site sync removes unreferenced hashed assets. This does not
publish anything or replace the live API and address-lookup data. Browser
icons are staged alongside the app, with an ICO copy at the site root.
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys

web = Path(__file__).resolve().parents[1]
dist = web / "dist"
html = (dist / "index.html").read_text(encoding="utf-8")
script = re.search(r'<script type="module" crossorigin src="\./([^\"]+)"></script>', html)
style = re.search(r'<link rel="stylesheet" crossorigin href="\./([^\"]+)">', html)
if not script or not style:
    raise SystemExit("Vite entry point did not contain the expected script and stylesheet")
js = (dist / script.group(1)).read_text(encoding="utf-8").replace("</script", "<\\/script")
css = (dist / style.group(1)).read_text(encoding="utf-8").replace("</style", "<\\/style")
html = html.replace(script.group(0), '<script type="module">\n' + js + '\n</script>')
html = html.replace(style.group(0), '<style>\n' + css + '\n</style>')
subprocess.run([sys.executable, str(web.parent / "scripts" / "build_share_pages.py")], check=True)
site = web.parent / "site"
app = site / "app"
icons = ("favicon.ico", "favicon.svg", "favicon-32x32.png", "apple-touch-icon.png",
         "android-chrome-192x192.png", "android-chrome-512x512.png", "site.webmanifest", "share-preview.png")
for name in icons:
    source = dist / name
    if not source.is_file() or source.stat().st_size == 0:
        raise SystemExit(f"Missing built icon: {source}")
    shutil.copy2(source, app / name)
# Browsers and clients may request the conventional root path without reading
# link tags from the app entry point.
shutil.copy2(dist / "favicon.ico", site / "favicon.ico")
for name in ("privacy.html", "terms.html", "data-deletion.html", "policy.css", "appearance.js"):
    source = dist / name
    if not source.is_file() or source.stat().st_size == 0:
        raise SystemExit(f"Missing public policy file: {source}")
    shutil.copy2(source, site / name)
output = app / "index.html"
output.write_text(html, encoding="utf-8")
print(f"Staged {output} ({output.stat().st_size:,} bytes)")
