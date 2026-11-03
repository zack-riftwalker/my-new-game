#!/usr/bin/env bash
# Publish parts/v1 (105 segments + patch + manifest/specs) to the
# feature/three-part-loader branch of zack-riftwalker/my-new-game.
# Run from THIS folder (the package root, containing parts/). Needs git
# and your GitHub credentials (your usual login). ~830 MB push.
set -euo pipefail

BRANCH="feature/three-part-loader"
REPO_URL="https://github.com/zack-riftwalker/my-new-game.git"
PKG_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Verifying package..."
test -f "$PKG_DIR/parts/v1/manifest.json" || { echo "manifest.json missing - run from the package root"; exit 1; }
SEG_COUNT=$(find "$PKG_DIR/parts/v1/segments" -type f | wc -l | tr -d ' ')
echo "    segments: $SEG_COUNT (expect 105)"
[ "$SEG_COUNT" = "105" ] || { echo "segment count mismatch, aborting"; exit 1; }

WORK="$(mktemp -d)"
echo "==> Cloning $BRANCH into $WORK ..."
git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$WORK/repo"

echo "==> Copying parts/ ..."
mkdir -p "$WORK/repo/parts/v1"
cp -R "$PKG_DIR/parts/v1/." "$WORK/repo/parts/v1/"

cd "$WORK/repo"
git add parts
echo "==> Staged files: $(git diff --cached --name-only | wc -l | tr -d ' ') (expect 118)"
git commit -m "M5: publish parts/v1 - staged segments, patch files, manifest and specs"
echo "==> HEAD after commit: $(git rev-parse HEAD)"
echo "==> Pushing (large, be patient)..."
git push origin "$BRANCH"
echo ""
echo "DONE. Pinned test URL (replace SHA if it changed):"
echo "https://cdn.jsdelivr.net/gh/zack-riftwalker/my-new-game@$(git rev-parse HEAD)/index.html"
