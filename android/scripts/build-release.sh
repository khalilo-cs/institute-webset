#!/usr/bin/env bash
# Builds the PRODUCTION apps. It refuses to run (and Gradle refuses too) unless you provide:
#   SERVER_ORIGIN               https://<your permanent domain>      (no path, no IP, no tunnel/preview host)
#   ANDROID_KEYSTORE_FILE       path to YOUR release keystore (never commit it)
#   ANDROID_KEYSTORE_PASSWORD, ANDROID_KEY_ALIAS, ANDROID_KEY_PASSWORD
# Output: ../artifacts/release/*.apk (side-loading / direct download) and *.aab (Google Play upload).
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SERVER_ORIGIN:?set SERVER_ORIGIN, e.g. https://your-domain.example}"
: "${ANDROID_KEYSTORE_FILE:?set ANDROID_KEYSTORE_FILE}"
: "${ANDROID_KEYSTORE_PASSWORD:?set ANDROID_KEYSTORE_PASSWORD}"
: "${ANDROID_KEY_ALIAS:?set ANDROID_KEY_ALIAS}"
: "${ANDROID_KEY_PASSWORD:?set ANDROID_KEY_PASSWORD}"
./gradlew --no-daemon -q :app:testDebugUnitTest :app:assembleRelease :app:bundleRelease
out=../artifacts/release
mkdir -p "$out"
cp app/build/outputs/apk/release/app-release.apk "$out/alfakhamah.apk"
cp app/build/outputs/bundle/release/app-release.aab "$out/alfakhamah.aab"
(cd "$out" && sha256sum *.apk *.aab | tee SHA256SUMS.txt)
echo "Signer certificate fingerprints (needed for Digital Asset Links only if you ever switch to a TWA):"
for f in "$out"/*.apk; do "${ANDROID_HOME:-$(sed -n 's/^sdk.dir=//p' local.properties)}"/build-tools/*/apksigner verify --print-certs "$f" | grep "SHA-256"; done
