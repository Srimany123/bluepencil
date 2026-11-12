#!/bin/sh
# Assemble the static site into _site/. Usage: ./build.sh   (then: cd _site && python3 -m http.server 8000)
set -e
cd "$(dirname "$0")"
rm -rf _site && mkdir -p _site
python3 tools/catalog.py > catalog.json   # committed too, so a plain branch deploy of the repo also works
cp catalog.json _site/catalog.json
cp -r py samples vendor assets app.css app.js backend.js worker.js pdfreport.js docs.html docs.js owner.html privacy.html _site/
V=$(cat py/*.py app.js backend.js worker.js pdfreport.js docs.js app.css index.html | sha1sum | cut -c1-10)
sed "s/__V__/$V/g" index.html > _site/index.html
touch _site/.nojekyll
for f in _site/*.js; do node --check "$f"; done
python3 -m py_compile py/*.py && rm -rf py/__pycache__
echo "built $V"
