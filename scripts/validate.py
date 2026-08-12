#!/usr/bin/env python3
"""Validate topics/*.html against the Learning Hub conventions.

Checks:
  1. All required <meta> tags present (source-chat, last-quizzed, prerequisites,
     tags, created, last-updated)
  2. The hub stylesheet is linked by relative path, and no stylesheet or script
     is loaded over http(s) - the hub must render from disk with no network
  3. Required class names are present in the document
  4. No <a href="X.html"> links to blueprints that don't exist in topics/

Exits 0 if all blueprints pass, 1 if any violations found.
"""

import os
import re
import sys
from html.parser import HTMLParser

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOPICS_DIR = os.path.join(REPO_ROOT, "topics")
EXPECTED_CSS_HREF = "../assets/blueprint.css"

REQUIRED_METAS = [
    "source-chat",
    "last-quizzed",
    "prerequisites",
    "tags",
    "created",
    "last-updated",
]

REQUIRED_CLASSES = [
    "eyebrow",
    "chno",
    "formula",
    "where",
    "key",
    "closing",
    "breath",
    "cross-refs",
]

# Reference pages (meta blueprint-type=reference) are exempt from
# teaching-specific classes — they may have no formulas or diagrams.
REFERENCE_EXEMPT_CLASSES = {"formula", "where", "key", "closing", "breath"}


class BlueprintValidator(HTMLParser):
    def __init__(self):
        super().__init__()
        self.metas = {}
        self.css_hrefs = []
        self.script_srcs = []
        self.body_links = set()
        self.class_names = set()
        self._in_body = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "meta":
            name = attrs_dict.get("name", "")
            if name:
                self.metas[name] = attrs_dict.get("content", "")
        elif tag == "link" and attrs_dict.get("rel") == "stylesheet":
            self.css_hrefs.append(attrs_dict.get("href", ""))
        elif tag == "script" and attrs_dict.get("src"):
            self.script_srcs.append(attrs_dict["src"])
        elif tag == "body":
            self._in_body = True
        elif tag == "a" and self._in_body:
            href = attrs_dict.get("href", "")
            if href.endswith(".html") and not href.startswith("http") and not href.startswith("#"):
                self.body_links.add(os.path.basename(href))

        cls = attrs_dict.get("class", "")
        if cls:
            for c in cls.split():
                self.class_names.add(c)

    def handle_endtag(self, tag):
        if tag == "body":
            self._in_body = False


def validate_file(filepath, all_filenames):
    errors = []
    warnings = []

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    parser = BlueprintValidator()
    parser.feed(content)

    filename = os.path.basename(filepath)

    # 1. Required metas
    for meta_name in REQUIRED_METAS:
        if meta_name not in parser.metas:
            errors.append(f'missing <meta name="{meta_name}">')

    # 2. Local-first assets
    if not parser.css_hrefs:
        errors.append("no <link rel=\"stylesheet\"> found")
    elif EXPECTED_CSS_HREF not in parser.css_hrefs:
        errors.append(
            f'no <link rel="stylesheet" href="{EXPECTED_CSS_HREF}"> - '
            f'found {parser.css_hrefs}'
        )
    for href in parser.css_hrefs:
        if href.startswith(("http://", "https://", "//")):
            errors.append(f'stylesheet loaded over the network: "{href}"')
    for src in parser.script_srcs:
        if src.startswith(("http://", "https://", "//")):
            errors.append(f'script loaded over the network: "{src}"')

    # 3. Required class names
    is_reference = parser.metas.get("blueprint-type", "").strip() == "reference"
    for cls in REQUIRED_CLASSES:
        if is_reference and cls in REFERENCE_EXEMPT_CLASSES:
            continue
        if cls not in parser.class_names:
            errors.append(f'missing required class "{cls}"')

    # 4. Body links to non-existent blueprints
    for link in parser.body_links:
        if link not in all_filenames:
            errors.append(f'links to "{link}" which does not exist in topics/')

    # Warnings (not failures)
    if parser.metas.get("source-chat", "").strip() == "":
        warnings.append("source-chat meta is empty — fill it with the session URL")

    return errors, warnings


def main():
    if not os.path.isdir(TOPICS_DIR):
        print("ERROR: topics/ directory not found")
        return 1

    html_files = sorted(
        f for f in os.listdir(TOPICS_DIR) if f.endswith(".html")
    )
    all_filenames = set(html_files)

    if not html_files:
        print("WARNING: no blueprints in topics/")
        return 0

    total_errors = 0
    total_warnings = 0

    for filename in html_files:
        filepath = os.path.join(TOPICS_DIR, filename)
        errors, warnings = validate_file(filepath, all_filenames)

        if errors:
            total_errors += len(errors)
            print(f"\n✗ {filename}")
            for e in errors:
                print(f"  ERROR: {e}")
        else:
            print(f"✓ {filename}")

        for w in warnings:
            total_warnings += 1
            print(f"  WARN: {w}")

    print(f"\n{'=' * 60}")
    print(f"{len(html_files)} blueprint(s) checked, {total_errors} error(s), {total_warnings} warning(s)")

    if total_errors > 0:
        print("\nFAILED — fix errors before deploying.")
        return 1
    else:
        print("\nPASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())
