#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $# -gt 1 || ${1:-demo} != demo ]]; then
  echo "Usage: $0 demo" >&2
  exit 2
fi

mkdir -p test-results
export REPORT_DIR
REPORT_DIR=$(mktemp -d "$PWD/test-results/demo-XXXXXXXX")
project="rental-$(basename "$REPORT_DIR" | tr '[:upper:]' '[:lower:]')"
dc=(docker compose -p "$project" -f docker-compose.yml -f testing/compose.yml)
finish() {
  local status=$?
  trap - EXIT
  "${dc[@]}" logs --no-color > "$REPORT_DIR/services.log" 2>&1 || true
  "${dc[@]}" down -v --remove-orphans >> "$REPORT_DIR/runner.log" 2>&1 || status=1
  exit "$status"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "Поднимаем отдельный стенд. Результаты прогона в $REPORT_DIR"
if ! ( "${dc[@]}" build harness && "${dc[@]}" up -d --build nginx rental-worker ) > "$REPORT_DIR/runner.log" 2>&1; then
  tail -30 "$REPORT_DIR/runner.log" >&2
  exit 1
fi
"${dc[@]}" run --rm -T --no-deps harness 2>&1 | tee "$REPORT_DIR/benchmark.log"
