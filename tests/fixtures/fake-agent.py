#!/usr/bin/env python3
"""Deterministic review agent for the browser E2E test."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any


def required_env(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing {name}")
    return Path(value)


def comment_id(comment: dict[str, Any]) -> str:
    return str(comment.get("id") or comment.get("commentId") or "")


def round_number(blueprint: str) -> int:
    match = re.search(r'<meta name="review-fixture-round" content="(\d+)">', blueprint)
    return int(match.group(1)) if match else 0


def set_round(blueprint: str, number: int) -> str:
    marker = f'<meta name="review-fixture-round" content="{number}">'
    if 'name="review-fixture-round"' in blueprint:
        return re.sub(r'<meta name="review-fixture-round" content="\d+">', marker, blueprint, count=1)
    return blueprint.replace("<head>", "<head>\n" + marker, 1)


def apply_replacements(blueprint: str, replacements: list[dict[str, str]]) -> str:
    candidate = blueprint
    for replacement in replacements:
        old = replacement["old"]
        new = replacement["new"]
        if old not in candidate:
            raise SystemExit(f"fixture replacement source is missing: {old!r}")
        candidate = candidate.replace(old, new, 1)
    return candidate


def mapping_for(comment: dict[str, Any]) -> dict[str, Any]:
    anchor = comment.get("anchor") if isinstance(comment.get("anchor"), dict) else {}
    kind = anchor.get("kind", "text")
    target: dict[str, Any] = {"kind": kind}
    for key in ("blockKey", "sectionId", "diagramId", "selector", "range", "quote", "exact"):
        if anchor.get(key) is not None:
            target[key] = anchor[key]
    if "exact" not in target and anchor.get("quote"):
        target["exact"] = anchor["quote"]
    return {
        "commentId": comment_id(comment),
        "status": "mapped" if target else "unmapped",
        "targets": [target] if target else [],
    }


def decision_value(decisions: dict[str, Any], identifier: str) -> str:
    value = decisions.get(identifier)
    if isinstance(value, dict):
        return str(value.get("decision", "")).lower()
    return str(value or "").lower()


def append_log(path: Path | None, record: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    context_path = required_env("LEARNING_HUB_REVIEW_CONTEXT")
    blueprint_path = required_env("LEARNING_HUB_REVIEW_BLUEPRINT")
    result_path = required_env("LEARNING_HUB_REVIEW_RESULT")
    sequence_path = Path(
        os.environ.get("LEARNING_HUB_FAKE_SEQUENCE", Path(__file__).with_name("review-sequence.json"))
    )
    context = json.loads(context_path.read_text(encoding="utf-8"))
    sequence = json.loads(sequence_path.read_text(encoding="utf-8"))
    blueprint = blueprint_path.read_text(encoding="utf-8")
    current_round = round_number(blueprint)
    stages = sequence["stages"]
    if current_round >= len(stages):
        raise SystemExit(f"no fake-agent stage after round {current_round}")
    stage = stages[current_round]
    operation = os.environ.get("LEARNING_HUB_REVIEW_OPERATION", context.get("operation", ""))
    if operation != stage["operation"]:
        raise SystemExit(f"expected {stage['operation']} for round {current_round + 1}, got {operation}")

    candidate = set_round(apply_replacements(blueprint, stage.get("replacements", [])), current_round + 1)
    comments = [item for item in context.get("comments", []) if isinstance(item, dict)]
    decisions = context.get("decisions", {})
    if not isinstance(decisions, dict):
        decisions = {}
    active_comments = [
        comment for comment in comments if decision_value(decisions, comment_id(comment)) != "yes"
    ]
    mappings = [mapping_for(comment) for comment in active_comments]
    result = {"candidate": candidate, "comments": mappings, "summary": stage["summary"]}
    blueprint_path.write_text(candidate, encoding="utf-8")
    result_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    log_value = os.environ.get("LEARNING_HUB_FAKE_AGENT_LOG")
    append_log(
        Path(log_value) if log_value else None,
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "round": current_round + 1,
            "operation": operation,
            "commentIds": [comment_id(comment) for comment in active_comments],
            "decisions": decisions,
        },
    )
    verbose_bytes = int(os.environ.get("LEARNING_HUB_FAKE_VERBOSE_BYTES", "0"))
    if verbose_bytes:
        sys.stdout.write("x" * verbose_bytes)
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
