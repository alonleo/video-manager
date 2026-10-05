#!/usr/bin/env bash

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SOURCE_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
INPUT_APK="$SOURCE_ROOT/android/app/build/outputs/apk/release/app-release.apk"
TEST_TMP="$(mktemp -d "${TMPDIR:-/tmp}/framearchive-build-test.XXXXXX")"

cleanup() {
    case "$TEST_TMP" in
        "${TMPDIR:-/tmp}"/framearchive-build-test.*) rm -rf -- "$TEST_TMP" ;;
        *) printf '拒绝清理意外测试路径：%s\n' "$TEST_TMP" >&2 ;;
    esac
}
trap cleanup EXIT

[[ -f "$INPUT_APK" ]] || {
    printf '缺少测试输入 APK，请先运行 Android assembleRelease：%s\n' "$INPUT_APK" >&2
    exit 1
}

make_root() {
    local name="$1" root gradlew
    root="$TEST_TMP/$name"
    mkdir -p -- "$root/scripts" "$root/android/app/build/outputs/apk/release"
    cp -- "$SOURCE_ROOT/scripts/build-android-apk.sh" "$root/scripts/build-android-apk.sh"
    chmod 755 -- "$root/scripts/build-android-apk.sh"
    ln -s -- "$SOURCE_ROOT/.android-sdk" "$root/.android-sdk"
    gradlew="$root/android/gradlew"
    printf '%s\n' \
        '#!/usr/bin/env bash' \
        'set -euo pipefail' \
        '[[ -z "${FRAMEARCHIVE_STORE_PASSWORD+x}" ]]' \
        '[[ -z "${FRAMEARCHIVE_KEY_PASSWORD+x}" ]]' \
        'mkdir -p app/build/outputs/apk/release' \
        'cp -- "$FRAMEARCHIVE_TEST_INPUT_APK" app/build/outputs/apk/release/app-release.apk' \
        >"$gradlew"
    chmod 755 -- "$gradlew"
    printf '%s\n' "$root"
}

run_build() {
    local root="$1"
    FRAMEARCHIVE_TEST_INPUT_APK="$INPUT_APK" "$root/scripts/build-android-apk.sh"
}

test_symlink_lock_is_fail_closed() {
    local root victim before after
    root="$(make_root symlink-lock)"
    mkdir -- "$root/artifacts"
    victim="$root/lock-victim"
    printf '%s\n' '不得修改' >"$victim"
    before="$(sha256sum "$victim" | cut -d' ' -f1)"
    ln -s -- "$victim" "$root/artifacts/.build-android-apk.lock.d"
    if run_build "$root" >/dev/null 2>&1; then
        printf '%s\n' '符号链接锁测试应当失败' >&2
        return 1
    fi
    after="$(sha256sum "$victim" | cut -d' ' -f1)"
    [[ "$before" == "$after" ]]
    [[ -L "$root/artifacts/.build-android-apk.lock.d" ]]
}

test_lock_competition_is_fail_fast() {
    local root
    root="$(make_root lock-competition)"
    mkdir -p -- "$root/artifacts/.build-android-apk.lock.d"
    if run_build "$root" >/dev/null 2>&1; then
        printf '%s\n' '锁竞争测试应当失败' >&2
        return 1
    fi
    [[ -d "$root/artifacts/.build-android-apk.lock.d" ]]
}

test_signing_initialization_is_transactional() {
    local root leftovers
    root="$(make_root signing-transaction)"
    if FRAMEARCHIVE_BUILD_TEST_ROOT="$root" \
        FRAMEARCHIVE_TEST_FAIL_AFTER_KEYSTORE=1 \
        FRAMEARCHIVE_TEST_INPUT_APK="$INPUT_APK" \
        "$root/scripts/build-android-apk.sh" >/dev/null 2>&1; then
        printf '%s\n' '签名初始化失败测试应当失败' >&2
        return 1
    fi
    [[ ! -e "$root/android/signing" && ! -L "$root/android/signing" ]]
    leftovers="$(find "$root/android" -maxdepth 1 -name '.signing.new.*' -print -quit)"
    [[ -z "$leftovers" ]]
    [[ ! -e "$root/artifacts/.build-android-apk.lock.d" ]]
}

test_signing_failure_hook_is_refused_in_formal_root() {
    local root leftovers
    root="$(make_root formal-hook-refusal)"
    mkdir -- "$root/.git"
    if FRAMEARCHIVE_BUILD_TEST_ROOT="$root" \
        FRAMEARCHIVE_TEST_FAIL_AFTER_KEYSTORE=1 \
        FRAMEARCHIVE_TEST_INPUT_APK="$INPUT_APK" \
        "$root/scripts/build-android-apk.sh" >/dev/null 2>&1; then
        printf '%s\n' '正式项目目录必须拒绝签名初始化测试钩子' >&2
        return 1
    fi
    [[ ! -e "$root/android/signing" && ! -L "$root/android/signing" ]]
    leftovers="$(find "$root/android" -maxdepth 1 -name '.signing.new.*' -print -quit)"
    [[ -z "$leftovers" ]]
}

create_alternate_signing() {
    local root="$1" signing="$1/android/signing.alternate" store_password key_password
    store_password="$(openssl rand -hex 32)"
    key_password="$(openssl rand -hex 32)"
    mkdir -- "$signing"
    FRAMEARCHIVE_STORE_PASSWORD="$store_password" \
    FRAMEARCHIVE_KEY_PASSWORD="$key_password" \
        keytool -genkeypair \
            -alias frame-archive-mobile -keyalg RSA -keysize 2048 -sigalg SHA256withRSA \
            -validity 9125 -dname 'CN=Frame Archive Mobile, O=Local' -storetype JKS \
            -keystore "$signing/frame-archive-mobile-release.jks" \
            -storepass:env FRAMEARCHIVE_STORE_PASSWORD \
            -keypass:env FRAMEARCHIVE_KEY_PASSWORD -noprompt >/dev/null 2>&1
    printf '%s\n' \
        'storeFile=signing/frame-archive-mobile-release.jks' \
        "storePassword=$store_password" \
        'keyAlias=frame-archive-mobile' \
        "keyPassword=$key_password" >"$signing/keystore.properties"
    chmod 600 -- "$signing/frame-archive-mobile-release.jks" "$signing/keystore.properties"
}

test_signer_continuity_preserves_old_release() {
    local root apk_before sha_before apk_after sha_after
    root="$(make_root signer-continuity)"
    run_build "$root" >/dev/null 2>&1
    apk_before="$(sha256sum "$root/artifacts/frame-archive-mobile-0.1.0.apk" | cut -d' ' -f1)"
    sha_before="$(sha256sum "$root/artifacts/frame-archive-mobile-0.1.0.apk.sha256" | cut -d' ' -f1)"
    create_alternate_signing "$root"
    mv -- "$root/android/signing" "$root/android/signing.original"
    mv -- "$root/android/signing.alternate" "$root/android/signing"
    if run_build "$root" >/dev/null 2>&1; then
        printf '%s\n' '不同证书的发布覆盖测试应当失败' >&2
        return 1
    fi
    apk_after="$(sha256sum "$root/artifacts/frame-archive-mobile-0.1.0.apk" | cut -d' ' -f1)"
    sha_after="$(sha256sum "$root/artifacts/frame-archive-mobile-0.1.0.apk.sha256" | cut -d' ' -f1)"
    [[ "$apk_before" == "$apk_after" ]]
    [[ "$sha_before" == "$sha_after" ]]
    [[ ! -e "$root/artifacts/.build-android-apk.lock.d" ]]
}

test_symlink_lock_is_fail_closed
test_lock_competition_is_fail_fast
test_signing_initialization_is_transactional
test_signing_failure_hook_is_refused_in_formal_root
test_signer_continuity_preserves_old_release
printf '%s\n' 'Android 发布脚本隔离测试通过（锁、防半套签名、signer 连续性）。'
