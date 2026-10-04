#!/usr/bin/env bash
# Guard: every Claude Code skill under .claude/skills/<name>/ must have a
# byte-identical Codex mirror under .agents/skills/<name>/.
#
# The two trees give two different agents their instructions for the same task;
# when they drift (PR #50 seeded .agents/ with a mechanical "Claude -> Codex"
# rewrite that later diverged) the agents receive conflicting guidance. Rule:
#   - every file under .claude/skills/<name>/ must exist with identical content
#     under .agents/skills/<name>/ (SKILL.md, LICENSE.txt, ...);
#   - the .agents side may carry extra Codex-only files (agents/openai.yaml) and
#     Codex-only skills (current-docs) — those are not mirrored back.
# .claude/skills is canonical. Run this script with --sync to copy canonical
# files to .agents/skills without deleting agent-specific extras. The default
# check never overwrites files.
#
# Runs in pre-commit (skill-mirror-sync) and the hygiene CI workflow.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

case "${1:-}" in
  ""|--sync) ;;
  *) echo "Usage: $0 [--sync]" >&2; exit 2 ;;
esac

status=0
checked=0
# A shared skill installed only for .agents must not silently pass the check.
for skill in .agents/skills/*; do
  [[ -d "$skill" ]] || continue
  name="${skill##*/}"
  [[ "$name" == current-docs ]] && continue
  if [[ ! -f ".claude/skills/$name/SKILL.md" ]]; then
    echo "✗ missing canonical skill: .claude/skills/$name/SKILL.md"
    echo "    review and install this shared skill in .claude/skills first"
    status=1
  fi
done

while IFS= read -r -d '' src; do
  rel="${src#.claude/skills/}"
  dst=".agents/skills/${rel}"
  checked=$((checked + 1))
  if [[ "${1:-}" == --sync ]]; then
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
  fi
  if [[ ! -f "$dst" ]]; then
    echo "✗ missing mirror: $dst"
    echo "    fix: mkdir -p \"$(dirname "$dst")\" && cp \"$src\" \"$dst\""
    status=1
  elif ! cmp -s "$src" "$dst"; then
    echo "✗ drifted: $src != $dst"
    echo "    diff: diff \"$src\" \"$dst\""
    echo "    fix (if .claude is canonical): cp \"$src\" \"$dst\""
    status=1
  fi
done < <(find .claude/skills -type f -print0 | sort -z)

if [[ $status -eq 0 ]]; then
  echo "✓ $checked skill file(s) mirrored identically between .claude/skills and .agents/skills"
fi
exit $status
