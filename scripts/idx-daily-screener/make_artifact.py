#!/usr/bin/env python3
"""make_artifact.py — strips gen_report.py's full HTML document down to a
bare content fragment suitable for Claude Artifact publishing (no
<!doctype>/<html>/<head>/<body> wrapper of its own — the Artifact host
supplies those; <title>/<link>/<style> stay inline at the top of the
fragment, same as the source document, since the host only fills in
charset/viewport meta and a light reset).

Usage: make_artifact.py <full-report.html> <artifact-fragment.html>
"""
import re
import sys


def to_fragment(html_text):
    html_text = re.sub(r'^\s*<!doctype[^>]*>\s*', '', html_text, flags=re.IGNORECASE)
    html_text = re.sub(r'<html[^>]*>', '', html_text, count=1, flags=re.IGNORECASE)
    html_text = html_text.replace('</html>', '')
    html_text = re.sub(r'<head>', '', html_text, count=1, flags=re.IGNORECASE)
    html_text = re.sub(r'</head>\s*<body>', '', html_text, count=1, flags=re.IGNORECASE)
    html_text = html_text.replace('</body>', '')
    # meta charset/viewport are redundant once the artifact host supplies
    # its own — drop them rather than leave dangling tags mid-fragment.
    html_text = re.sub(r'<meta charset="utf-8">\s*', '', html_text, count=1, flags=re.IGNORECASE)
    html_text = re.sub(r'<meta name="viewport"[^>]*>\s*', '', html_text, count=1, flags=re.IGNORECASE)
    return html_text.strip() + '\n'


def main():
    src, dst = sys.argv[1], sys.argv[2]
    with open(src, encoding='utf-8') as f:
        full = f.read()
    fragment = to_fragment(full)
    with open(dst, 'w', encoding='utf-8') as f:
        f.write(fragment)
    print(f"wrote {dst} ({len(fragment)} bytes)")


if __name__ == '__main__':
    main()
