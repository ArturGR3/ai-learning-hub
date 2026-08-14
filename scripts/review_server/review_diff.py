"""Small, text-first summaries of semantic blueprint changes."""

from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
from html.parser import HTMLParser
import re
from typing import Any


_SPACE = re.compile(r"\s+")
_VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class _BlockParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict[str, str]] = []
        self.section = "document"
        self._sections: list[str] = []
        self._capture: dict[str, Any] | None = None
        self._capture_depth = 0
        self._skip_depth = 0

    @staticmethod
    def _markup_token(tag: str, attrs: list[tuple[str, str | None]], *, closing: bool = False) -> str:
        if closing:
            return f"</{tag}>"
        normalized = " ".join(
            f'{name}="{_SPACE.sub(" ", value or "").strip()}"'
            for name, value in sorted(attrs, key=lambda item: item[0])
        )
        return f"<{tag}{(' ' + normalized) if normalized else ''}>"

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], *, self_closing: bool) -> None:
        tag = tag.lower()
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "section":
            self._sections.append(self.section)
            self.section = values.get("id") or self.section
        if tag in {"script", "style"}:
            self._skip_depth += 1
        if self._capture is not None:
            if self._capture["kind"] == "diagram" and tag not in {"script", "style"}:
                self._capture["visual"].append(self._markup_token(tag, attrs))
            if not self_closing and tag not in _VOID_ELEMENTS:
                self._capture_depth += 1
            return
        kind = ""
        if tag in {"p", "li", "h1", "h2", "h3"}:
            kind = tag
        elif tag == "div" and ("diagram" in classes or "caption" in classes):
            kind = "diagram"
        elif tag == "div" and "formula" in classes:
            kind = "formula"
        if kind and not self._skip_depth:
            visual = [self._markup_token(tag, attrs)] if kind == "diagram" else []
            self._capture = {"kind": kind, "sectionId": self.section, "parts": [], "visual": visual}
            self._capture_depth = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, self_closing=True)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._capture is not None:
            if self._capture["kind"] == "diagram" and tag not in {"script", "style"}:
                self._capture["visual"].append(self._markup_token(tag, [], closing=True))
            self._capture_depth -= 1
            if self._capture_depth == 0:
                text = _SPACE.sub(" ", " ".join(self._capture["parts"])).strip()
                if text or self._capture["kind"] == "diagram":
                    text = text or "Diagram"
                    self.blocks.append(
                        {
                            "kind": self._capture["kind"],
                            "sectionId": self._capture["sectionId"],
                            "text": text,
                            "visual": "".join(self._capture["visual"]),
                        }
                    )
                self._capture = None
        if tag in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "section":
            self.section = self._sections.pop() if self._sections else "document"

    def handle_data(self, data: str) -> None:
        if self._capture is not None and not self._skip_depth:
            self._capture["parts"].append(data)


def _blocks(document: str) -> list[dict[str, str]]:
    parser = _BlockParser()
    parser.feed(document)
    parser.close()
    return parser.blocks


def semantic_changes(original: str, candidate: str) -> list[dict[str, Any]]:
    """Return compact changed-block groups for reader-facing comparison."""
    before = _blocks(original)
    after = _blocks(candidate)
    before_keys = [f"{item['kind']}\x1f{item['text']}\x1f{item.get('visual', '')}" for item in before]
    after_keys = [f"{item['kind']}\x1f{item['text']}\x1f{item.get('visual', '')}" for item in after]
    matcher = SequenceMatcher(a=before_keys, b=after_keys, autojunk=False)
    changes: list[dict[str, Any]] = []
    for opcode, i1, i2, j1, j2 in matcher.get_opcodes():
        if opcode == "equal":
            continue
        removed = before[i1:i2]
        added = after[j1:j2]
        sections = [item["sectionId"] for item in added + removed if item["sectionId"]]
        section = Counter(sections).most_common(1)[0][0] if sections else "document"
        changes.append(
            {
                "sectionId": section,
                "blockCount": max(len(removed), len(added)),
                "before": [item["text"][:360] for item in removed[:8]],
                "after": [item["text"][:360] for item in added[:8]],
                "truncated": len(removed) > 8 or len(added) > 8,
            }
        )
    return changes[:24]


def summary_for_comment(comment: dict[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidate_anchor = comment.get("candidateAnchor") if isinstance(comment.get("candidateAnchor"), dict) else {}
    original_anchor = comment.get("originalAnchor") if isinstance(comment.get("originalAnchor"), dict) else {}
    section = candidate_anchor.get("sectionId") or original_anchor.get("sectionId")
    matching = [item for item in changes if not section or item.get("sectionId") == section]
    if not matching:
        return None
    before = [text for item in matching for text in item.get("before", [])]
    after = [text for item in matching for text in item.get("after", [])]
    return {
        # Complete-HTML agent results do not carry trustworthy edit-to-comment
        # provenance. Present this honestly as a section-level comparison.
        "scope": "section",
        "sectionId": section or matching[0].get("sectionId") or "document",
        "blockCount": sum(int(item.get("blockCount", 0)) for item in matching),
        "before": before[:8],
        "after": after[:8],
        "truncated": len(before) > 8 or len(after) > 8 or any(item.get("truncated") for item in matching),
    }
