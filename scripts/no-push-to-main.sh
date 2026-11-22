#!/usr/bin/env bash
# pre-push hook: `main` changes only through a pull request (see CLAUDE.md).
if [ "${PRE_COMMIT_REMOTE_BRANCH:-}" = "refs/heads/main" ]; then
  echo "Push to main is blocked. Push a branch and open a pull request." >&2
  exit 1
fi
