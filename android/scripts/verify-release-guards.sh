#!/usr/bin/env bash
# Negative tests for the release guard in app/build.gradle.kts: every call below MUST be refused,
# and no release APK may appear. Run from the android/ folder:  ./scripts/verify-release-guards.sh
set -u
cd "$(dirname "$0")/.."
fail=0
tmp_keystore="$(mktemp -d)/guard-test.jks"
keytool -genkeypair -keystore "$tmp_keystore" -storepass guardtest123 -keypass guardtest123 -alias guard \
  -keyalg RSA -keysize 2048 -validity 2 -dname "CN=guard-test" >/dev/null 2>&1
SIGN=(-PANDROID_KEYSTORE_FILE="$tmp_keystore" -PANDROID_KEYSTORE_PASSWORD=guardtest123 -PANDROID_KEY_ALIAS=guard -PANDROID_KEY_PASSWORD=guardtest123)

expect_refused() {
  local label="$1" expected="$2"; shift 2
  out="$(./gradlew --no-daemon -q :app:assembleRelease "$@" 2>&1)"; code=$?
  if [ $code -ne 0 ] && grep -qF "$expected" <<<"$out"; then
    echo "PASS  refused: $label"
  else
    echo "FAIL  not refused as expected: $label (exit $code)"; echo "$out" | tail -8; fail=1
  fi
}

expect_refused "no origin, no signing"                  "SERVER_ORIGIN is not set"        
expect_refused "http origin"                            "must start with https://"        -PSERVER_ORIGIN=http://shop.example-store.com "${SIGN[@]}"
expect_refused "localhost"                              "not a permanent production"      -PSERVER_ORIGIN=https://localhost "${SIGN[@]}"
expect_refused "IP address"                             "IP address"                      -PSERVER_ORIGIN=https://192.168.1.10 "${SIGN[@]}"
expect_refused "emulator alias"                         "IP address"                      -PSERVER_ORIGIN=https://10.0.2.2 "${SIGN[@]}"
expect_refused "tunnel domain (trycloudflare)"          "not a permanent production"      -PSERVER_ORIGIN=https://abc-def.trycloudflare.com "${SIGN[@]}"
expect_refused "tunnel domain (ngrok)"                  "not a permanent production"      -PSERVER_ORIGIN=https://abc.ngrok-free.app "${SIGN[@]}"
expect_refused "placeholder example.com"                "not a permanent production"      -PSERVER_ORIGIN=https://shop.example.com "${SIGN[@]}"
expect_refused "single-label host"                      "not a public domain"             -PSERVER_ORIGIN=https://shop "${SIGN[@]}"
expect_refused "origin with a path"                     "without path"                    -PSERVER_ORIGIN=https://shop.my-store.com/admin "${SIGN[@]}"
expect_refused "valid origin but no signing key"        "signing is not configured"       -PSERVER_ORIGIN=https://shop.my-store.com
expect_refused "valid origin, missing keystore file"    "does not exist"                  -PSERVER_ORIGIN=https://shop.my-store.com -PANDROID_KEYSTORE_FILE=/nonexistent.jks -PANDROID_KEYSTORE_PASSWORD=x -PANDROID_KEY_ALIAS=x -PANDROID_KEY_PASSWORD=x

if ls app/build/outputs/apk/*/release/*.apk >/dev/null 2>&1; then echo "FAIL  a release APK exists although every attempt was refused"; fail=1; fi
rm -rf "$(dirname "$tmp_keystore")"
exit $fail
