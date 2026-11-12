"""Writes catalog.json (rules, profiles, formats) so the page can render before Python loads."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "py"))
import rules, extract  # noqa: E402
c = rules.catalog()
c["formats"] = extract.FORMATS
json.dump(c, sys.stdout, ensure_ascii=False, separators=(",", ":"))
