#!/bin/sh
# Assemble the static site into _site/. Usage: ./build.sh   (then: cd _site && python3 -m http.server 8000)
set -e
cd "$(dirname "$0")"
rm -rf _site && mkdir -p _site
python3 tools/catalog.py > _site/catalog.json
cp -r py samples vendor app.css app.js backend.js worker.js _site/
V=$(cat py/*.py app.js backend.js worker.js app.css index.html | sha1sum | cut -c1-10)
sed "s/__V__/$V/g" index.html > _site/index.html
touch _site/.nojekyll
for f in _site/*.js; do node --check "$f"; done
python3 -m py_compile py/*.py && rm -rf py/__pycache__
echo "built $V"
