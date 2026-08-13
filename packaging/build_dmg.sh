#!/bin/bash
# Build a distributable Meeting Scribe DMG from git-tracked files only.
#
# Usage: packaging/build_dmg.sh [version]
#
# Output: dist/MeetingScribe-<version>.dmg containing
#   Meeting Scribe.app   (bash-wrapper bundle; app code in Contents/Resources)
#   READ ME FIRST.md
#   Applications -> /Applications symlink
#
# Only `git archive HEAD` output goes into the bundle, so local data
# (MeetingScribe/data, Transcripts, config.json, CLAUDE.md, …) structurally
# cannot leak. A safety gate scans the staged DMG contents anyway and fails
# the build if anything private slips through.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-1.0}"
DIST="$REPO/dist"
STAGE="$(mktemp -d /tmp/meetingscribe-dist.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT

echo "● Staging git-tracked files (HEAD)…"
mkdir -p "$STAGE/src"
git -C "$REPO" archive HEAD | tar -x -C "$STAGE/src"

echo "● Assembling Meeting Scribe.app…"
APP="$STAGE/dmg/Meeting Scribe.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$REPO/packaging/Info.plist" "$APP/Contents/Info.plist"
cp "$REPO/packaging/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
cp "$REPO/packaging/MeetingScribe.stub" "$APP/Contents/MacOS/MeetingScribe"
chmod +x "$APP/Contents/MacOS/MeetingScribe"
cp -R "$STAGE/src/MeetingScribe" "$APP/Contents/Resources/MeetingScribe"
# Stamp the version into the bundle
/usr/bin/sed -i '' "s|<string>1.0.0</string>|<string>$VERSION</string>|g" "$APP/Contents/Info.plist"

cp "$STAGE/src/READ ME FIRST.md" "$STAGE/dmg/READ ME FIRST.md"
ln -s /Applications "$STAGE/dmg/Applications"

echo "● Safety gate: scanning for private files and secrets…"
BAD="$(find "$STAGE/dmg" \( \
        -name 'config.json' -o -name '*.db' -o -name '*.log' \
        -o -name '*.wav' -o -name '*.webm' -o -name '*.m4a' -o -name '*.mp4' \
        -o -name 'CLAUDE.md' -o -name '.claude' \
        -o \( -type d -name 'data' \) \) -print)"
if [ -n "$BAD" ]; then
  echo "✗ Private files found in staging — aborting:" >&2
  echo "$BAD" >&2
  exit 1
fi
if grep -rInE 'sk-[A-Za-z0-9_-]{20,}' "$STAGE/dmg" >&2; then
  echo "✗ Something that looks like an API key found in staging — aborting." >&2
  exit 1
fi
echo "  clean."

echo "● Creating DMG…"
mkdir -p "$DIST"
OUT="$DIST/MeetingScribe-$VERSION.dmg"
hdiutil create -volname "Meeting Scribe" -srcfolder "$STAGE/dmg" \
  -format UDZO -fs HFS+ -ov "$OUT" >/dev/null

echo "✓ Built $OUT"
du -h "$OUT" | cut -f1
