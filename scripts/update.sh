#!/usr/bin/env bash
#
# Refresh the Google Groups archives.
#
# For every group directory (one with a config.json) this fetches the
# conversations that are new or have changed since the last run, when the
# group is still active, writes its monthly mbox files from the stored
# pages, re-parses it with rmail-parser, and rebuilds the top-level
# lists.tsv index.
#
# Usage: scripts/update.sh [--no-fetch] [group ...]
#   --no-fetch  skip fetching; only rebuild from what is already in source/
#   group ...   limit the run to these groups (default: all of them)
#
# Environment:
#   RMAIL_PARSER  path to the rmail-parser binary (default: ./rmail-parser)
#   IMAP_USERNAME, IMAP_PASSWORD
#                 the member account and its app password (optional)
#   ALIASES       path to an aliases.json for name resolution (optional)
#   PYTHON        the Python to run scripts/gg.py with (default: python3)

set -euo pipefail

cd "$(dirname "$0")/.."

RMAIL_PARSER=${RMAIL_PARSER:-./rmail-parser}
ALIASES=${ALIASES:-}
PYTHON=${PYTHON:-python3}

FETCH=true
NAMES=()
for arg in "$@"; do
  case "$arg" in
    --no-fetch) FETCH=false ;;
    -*) echo "Unknown option: $arg" >&2; exit 2 ;;
    *) NAMES+=("$arg") ;;
  esac
done

if [ ${#NAMES[@]} -eq 0 ]; then
  for cfg in */config.json; do
    NAMES+=("${cfg%/config.json}")
  done
fi

# Parse <group>/raw/ into <group>/processed/ and lift the stats files next to
# it, matching the layout of the other archive repositories.
parse_group() {
  local group=$1 f log
  local aliases_args=()
  if [ -n "$ALIASES" ]; then
    aliases_args=(--aliases "$ALIASES")
  fi

  rm -rf "$group/processed"
  mkdir -p "$group/processed"
  # The parser logs one line per file it writes; only surface that on failure.
  if ! log=$("$RMAIL_PARSER" parse --input "$group/raw/" --output "$group/processed/" \
      --list "$group" ${aliases_args[@]+"${aliases_args[@]}"} 2>&1); then
    echo "$log" >&2
    echo "  $group: rmail-parser failed" >&2
    return 1
  fi

  "$PYTHON" -I scripts/gg.py urls "$group"

  for f in meta.json index.json message-order.json contributors.json; do
    mv "$group/processed/$f" "$group/$f"
  done
}

# A member account, when one is configured, gets each active group's new
# messages by email; their headers say which message a reply answers.
if [ "$FETCH" = true ] && [ -n "${IMAP_USERNAME:-}" ] && [ -n "${IMAP_PASSWORD:-}" ]; then
  "$PYTHON" -I scripts/gg.py mail all || echo "  the mail could not be read; carrying on without it" >&2
fi

for group in "${NAMES[@]}"; do
  if [ ! -f "$group/config.json" ]; then
    echo "No such group: $group" >&2
    exit 1
  fi
  echo "$group"
  # A group whose conversations are not listed yet only collects its mail:
  # building it from its newest pages would publish part of it as the whole.
  if [ ! -f "$group/threads.txt" ]; then
    echo "  not listed yet; only its mail is read"
    continue
  fi
  # A closed group is not asked again: its pages no longer change.
  if [ "$FETCH" = true ] && [ "$(jq -r '.active // false' "$group/config.json")" = true ]; then
    "$PYTHON" -I scripts/gg.py recent "$group"
  fi
  "$PYTHON" -I scripts/gg.py scrub "$group"
  "$PYTHON" -I scripts/gg.py mbox "$group"
  parse_group "$group"
done

# Rebuild the index of every group from its config.json and meta.json.
{
  printf 'list\tdescription\tmessages\tthreads\tfirst_message\tlast_message\tsource_url\n'
  for cfg in */config.json; do
    # A group that has not been fetched yet has no statistics to index.
    [ -f "${cfg%/config.json}/meta.json" ] || continue
    jq -r --slurpfile meta "${cfg%/config.json}/meta.json" '
      [.list, .description, $meta[0].total_messages, $meta[0].total_threads,
       ($meta[0].first_message // ""), ($meta[0].last_message // ""), .source_url] | @tsv
    ' "$cfg"
  done
} > lists.tsv

echo "Indexed $(($(wc -l < lists.tsv) - 1)) groups in lists.tsv"
