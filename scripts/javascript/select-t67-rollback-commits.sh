#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 || "$1" == -* || "$2" == -* || "$3" == -* ]]; then
  echo "Usage: select-t67-rollback-commits.sh CANDIDATE BASELINE REVIEWED_MERGE" >&2
  exit 2
fi

readonly candidate="$1"
readonly baseline="$2"
readonly reviewed_merge="$3"
repository="$(git rev-parse --show-toplevel)"
readonly repository

fail() {
  echo "T67 rollback selection failed: $*" >&2
  exit 1
}

git -C "$repository" merge-base --is-ancestor "$baseline" "$candidate" \
  || fail "baseline is not an ancestor of candidate"
mapfile -t first_parent_commits < <(
  git -C "$repository" rev-list --first-parent --reverse "$baseline..$candidate"
)
(( ${#first_parent_commits[@]} > 0 )) || fail "candidate does not contain T67"

t67_commits=()
for commit in "${first_parent_commits[@]}"; do
  read -r -a parents <<< "$(git -C "$repository" show -s --format=%P "$commit")"
  if (( ${#parents[@]} > 1 )); then
    [[ "$commit" == "$reviewed_merge" ]] \
      || fail "unrelated merge commit in candidate range: $commit"
    continue
  fi
  subject="$(git -C "$repository" show -s --format=%s "$commit")"
  [[ "$subject" =~ ^(chore|docs|test|fix)\(T67\):[[:space:]] ]] \
    || fail "unrelated first-parent commit in candidate range: $commit $subject"
  t67_commits+=("$commit")
done
(( ${#t67_commits[@]} > 0 )) || fail "candidate range has no T67 commit"

oldest_parent="$(git -C "$repository" show -s --format=%P "${t67_commits[0]}")"
[[ "$oldest_parent" == "$baseline" ]] \
  || fail "baseline is not the direct npm parent of the T67 series"

printf '%s\n' "${t67_commits[@]}"
