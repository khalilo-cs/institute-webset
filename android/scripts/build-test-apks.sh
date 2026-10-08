#!/usr/bin/env bash
# Builds the TEST (debug-signed) APK of the combined app and copies it to ../artifacts with a checksum.
# They contain no server address: the tester types one at first launch. NOT for production use.
# Requires JDK 17+ and an Android SDK (ANDROID_HOME or android/local.properties with sdk.dir).
set -euo pipefail
cd "$(dirname "$0")/.."
./gradlew --no-daemon -q :app:testDebugUnitTest :app:assembleDebug
out=../artifacts
mkdir -p "$out"
cp app/build/outputs/apk/debug/app-debug.apk "$out/alfakhamah-TEST.apk"
(cd "$out" && sha256sum alfakhamah-TEST.apk > SHA256SUMS.txt && cat SHA256SUMS.txt)
