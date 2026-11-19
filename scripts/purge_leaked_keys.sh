#!/usr/bin/env bash
# Purge the two OpenAlgo API keys that were committed in sample/ (finding SEC-02) from ALL git history.
#
# Run this in a clone of the UPSTREAM project repository (e.g. github.com/marketcalls/historify), which is where the
# keys were published. It was deliberately NOT run in the review workspace: that .git is a one-commit snapshot created
# before the review (not the project's history), and the maintainer's instruction was to abort if .git pre-existed.
#
#   !!! REWRITES HISTORY. Every clone/fork must re-clone afterwards. Run only after maintainer approval. !!!
#
# 1. FIRST rotate both keys in OpenAlgo (they must be considered compromised regardless of this purge).
# 2. pip install git-filter-repo
# 3. bash scripts/purge_leaked_keys.sh && git push --force --all && git push --force --tags
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
cat > /tmp/historify-leaked-keys.txt <<'KEYS'
regex:[0-9a-f]{64}==>REDACTED_API_KEY
KEYS
git filter-repo --replace-text /tmp/historify-leaked-keys.txt --force
rm -f /tmp/historify-leaked-keys.txt
echo "History rewritten. Verify with: gitleaks detect --log-opts=--all"
