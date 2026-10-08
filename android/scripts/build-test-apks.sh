#!/usr/bin/env bash
# Builds the two TEST (debug-signed) APKs and copies them to ../artifacts with checksums.
# They contain no server address: the tester types one at first launch. NOT for production use.
# Requires JDK 17+ and an Android SDK (ANDROID_HOME or android/local.properties with sdk.dir).
set -euo pipefail
cd "$(dirname "$0")/.."
./gradlew --no-daemon -q :app:testCustomerDebugUnitTest :app:assembleCustomerDebug :app:assembleAdminDebug
out=../artifacts
mkdir -p "$out"
cp app/build/outputs/apk/customer/debug/app-customer-debug.apk "$out/alfakhamah-customer-TEST.apk"
cp app/build/outputs/apk/admin/debug/app-admin-debug.apk "$out/alfakhamah-admin-TEST.apk"
(cd "$out" && sha256sum alfakhamah-customer-TEST.apk alfakhamah-admin-TEST.apk > SHA256SUMS.txt && cat SHA256SUMS.txt)
