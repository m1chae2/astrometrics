#!/usr/bin/env bash
# Rejects commit messages that don't start with one of the repo's conventional
# prefixes. Wired in as a pre-commit `commit-msg` stage hook (see
# .pre-commit-config.yaml) rather than a raw .git/hooks script so it installs
# the same way as the rest of the repo's git hooks.
set -euo pipefail

commit_msg_file="$1"
first_line="$(head -n1 "$commit_msg_file")"

# Let git's own auto-generated merge commit messages through unchecked.
if [[ "$first_line" == Merge\ * ]]; then
  exit 0
fi

allowed_prefixes='doc|refactor|new|fix|maintenance'
pattern="^(${allowed_prefixes}): .+"

if ! [[ "$first_line" =~ $pattern ]]; then
  cat >&2 <<EOF
Commit message must start with one of: doc:, refactor:, new:, fix:, maintenance:
followed by a space and a description.

Got: $first_line
EOF
  exit 1
fi
