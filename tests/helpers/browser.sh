#!/usr/bin/env bash

# Thin shell helpers around chrome-devtools-axi. The caller owns cleanup traps.

set -Eeuo pipefail

BROWSER_ARTIFACTS_DIR="${BROWSER_ARTIFACTS_DIR:?set BROWSER_ARTIFACTS_DIR before sourcing browser.sh}"
mkdir -p "$BROWSER_ARTIFACTS_DIR"

if [[ -n "${CHROME_DEVTOOLS_AXI_BIN:-}" ]]; then
  BROWSER_AXI=("$CHROME_DEVTOOLS_AXI_BIN")
else
  BROWSER_AXI=(npx -y chrome-devtools-axi)
fi

browser_axi() {
  "${BROWSER_AXI[@]}" "$@"
}

browser_open() {
  browser_axi open "$1" >"$BROWSER_ARTIFACTS_DIR/open.txt"
}

browser_stop() {
  browser_axi stop >/dev/null 2>&1 || true
}

browser_snapshot() {
  local name="${1:-snapshot}"
  browser_axi snapshot --full >"$BROWSER_ARTIFACTS_DIR/$name.txt"
}

browser_eval() {
  browser_axi eval "$1"
}

browser_do() {
  local expression="$1"
  local output
  output="$(browser_eval "(() => { $expression; return 'LH_ACTION_OK'; })()")"
  if [[ "$output" != *"LH_ACTION_OK"* ]]; then
    echo "browser action did not complete: $expression" >&2
    echo "$output" >&2
    return 1
  fi
}

browser_wait_js() {
  local expression="$1"
  local label="${2:-browser condition}"
  local attempts="${3:-80}"
  local output
  local attempt
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    output="$(browser_eval "(() => ($expression) ? 'LH_WAIT_TRUE' : 'LH_WAIT_FALSE')()" 2>&1 || true)"
    if [[ "$output" == *'\"LH_WAIT_TRUE\"'* ]]; then
      return 0
    fi
    sleep 0.25
  done
  echo "timed out waiting for $label" >&2
  browser_snapshot "timeout-snapshot" || true
  return 1
}

browser_click_testid() {
  local testid="$1"
  browser_do "const node = document.querySelector('[data-testid=\"$testid\"]'); if (!node) throw new Error('missing $testid'); node.click()"
}

browser_fill_testid() {
  local testid="$1"
  local value="$2"
  local encoded
  encoded="$(printf '%s' "$value" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"
  browser_do "const node = document.querySelector('[data-testid=\"$testid\"]'); if (!node) throw new Error('missing $testid'); node.focus(); node.value = $encoded; node.dispatchEvent(new Event('input', {bubbles:true})); node.dispatchEvent(new Event('change', {bubbles:true}))"
}

browser_click_decision() {
  local comment_id="$1"
  local decision="$2"
  browser_do "const node = document.querySelector('[data-decision=\"$comment_id\"] input[value=\"$decision\"]'); if (!node) throw new Error('missing decision'); node.click()"
}

browser_screenshot() {
  local name="$1"
  browser_axi screenshot "$BROWSER_ARTIFACTS_DIR/$name.png" >/dev/null
}

browser_capture_diagnostics() {
  browser_axi console >"$BROWSER_ARTIFACTS_DIR/console.txt" 2>&1 || true
  browser_axi network >"$BROWSER_ARTIFACTS_DIR/network.txt" 2>&1 || true
  browser_snapshot "final-snapshot" || true
}

browser_assert_clean_diagnostics() {
  browser_capture_diagnostics
  if rg -i "uncaught|typeerror|referenceerror|syntaxerror|failed to load" "$BROWSER_ARTIFACTS_DIR/console.txt"; then
    echo "unexpected browser console error" >&2
    return 1
  fi
  if rg -i "(^|[[:space:]])(4[0-9]{2}|5[0-9]{2})([[:space:]]|$)|failed" "$BROWSER_ARTIFACTS_DIR/network.txt"; then
    echo "unexpected failed browser request" >&2
    return 1
  fi
}
