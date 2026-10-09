#!/usr/bin/env python3
"""daily-summary: fetch five named sites, write one markdown file into ~/summaries.
In v0 agos-run DENIES all network, so this app runs but every fetch fails closed; the manifest
documents the intent so the per-domain egress work has a real target."""
import datetime, os, urllib.request
SITES = ["https://news.ycombinator.com", "https://lobste.rs", "https://arstechnica.com", "https://theverge.com", "https://hnrss.org/frontpage"]
out = os.path.expanduser("~/summaries"); os.makedirs(out, exist_ok=True)
lines = ["# %s" % datetime.date.today()]
for s in SITES:
    try:
        n = len(urllib.request.urlopen(s, timeout=10).read())
        lines.append("- %s: %d bytes" % (s, n))
    except Exception as e:
        lines.append("- %s: unavailable (%s)" % (s, type(e).__name__))
open(os.path.join(out, "%s.md" % datetime.date.today()), "w").write("\n".join(lines) + "\n")
