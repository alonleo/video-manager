#!/usr/bin/env bash

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
ANDROID_DIR="$PROJECT_ROOT/android"
ANDROID_HOME="$PROJECT_ROOT/.android-sdk"
export ANDROID_HOME
export ANDROID_SDK_ROOT="$ANDROID_HOME"

SIGNING_DIR="$ANDROID_DIR/signing"
KEYSTORE_FILE="$SIGNING_DIR/frame-archive-mobile-release.jks"
PROPERTIES_FILE="$SIGNING_DIR/keystore.properties"
KEY_ALIAS="frame-archive-mobile"
STORE_FILE_PROPERTY="signing/frame-archive-mobile-release.jks"
APKSIGNER="$ANDROID_HOME/build-tools/36.0.0/apksigner"
GRADLEW="$ANDROID_DIR/gradlew"
BUILT_APK="$ANDROID_DIR/app/build/outputs/apk/release/app-release.apk"
ARTIFACT_DIR="$PROJECT_ROOT/artifacts"
ARTIFACT_NAME="frame-archive-mobile-0.1.0.apk"
ARTIFACT_APK="$ARTIFACT_DIR/$ARTIFACT_NAME"
ARTIFACT_SHA="$ARTIFACT_APK.sha256"
LOCK_DIR="$ARTIFACT_DIR/.build-android-apk.lock.d"

FRAMEARCHIVE_STORE_PASSWORD=''
FRAMEARCHIVE_KEY_PASSWORD=''
temporary_signing_dir=''
temporary_apk=''
temporary_apk_idsig=''
temporary_sha=''
backup_dir=''
backup_identity=''
lock_owned=0
lock_identity=''
publish_in_progress=0
had_previous_release=0

fail() {
    printf '错误：%s\n' "$1" >&2
    exit 1
}

path_identity() {
    stat -c '%d:%i' -- "$1"
}

cleanup_signing_temp() {
    [[ -n "$temporary_signing_dir" ]] || return 0
    case "$temporary_signing_dir" in
        "$ANDROID_DIR"/.signing.new.*) ;;
        *) printf '警告：拒绝清理意外的签名临时路径：%s\n' "$temporary_signing_dir" >&2; return 1 ;;
    esac
    if [[ -d "$temporary_signing_dir" && ! -L "$temporary_signing_dir" ]]; then
        rm -f -- \
            "$temporary_signing_dir/frame-archive-mobile-release.jks" \
            "$temporary_signing_dir/keystore.properties"
        rmdir -- "$temporary_signing_dir" 2>/dev/null || true
    fi
    temporary_signing_dir=''
}

cleanup_backup() {
    [[ -n "$backup_dir" ]] || return 0
    case "$backup_dir" in
        "$ARTIFACT_DIR"/.release-backup.*) ;;
        *) printf '警告：拒绝清理意外的发布备份路径：%s\n' "$backup_dir" >&2; return 1 ;;
    esac
    if [[ -d "$backup_dir" && ! -L "$backup_dir" ]] \
        && [[ "$(path_identity "$backup_dir" 2>/dev/null || true)" == "$backup_identity" ]]; then
        rm -f -- "$backup_dir/$ARTIFACT_NAME" "$backup_dir/${ARTIFACT_NAME}.sha256"
        rmdir -- "$backup_dir" 2>/dev/null || true
    fi
    backup_dir=''
    backup_identity=''
}

rollback_publish() {
    ((publish_in_progress == 1)) || return 0
    printf '%s\n' '发布未完成，正在恢复上一版 APK 与校验文件……' >&2
    rm -f -- "$ARTIFACT_APK" "$ARTIFACT_SHA"
    if ((had_previous_release == 1)); then
        [[ -f "$backup_dir/$ARTIFACT_NAME" && ! -L "$backup_dir/$ARTIFACT_NAME" ]] \
            && mv -f -- "$backup_dir/$ARTIFACT_NAME" "$ARTIFACT_APK"
        [[ -f "$backup_dir/${ARTIFACT_NAME}.sha256" && ! -L "$backup_dir/${ARTIFACT_NAME}.sha256" ]] \
            && mv -f -- "$backup_dir/${ARTIFACT_NAME}.sha256" "$ARTIFACT_SHA"
    fi
    publish_in_progress=0
}

release_lock() {
    ((lock_owned == 1)) || return 0
    if [[ -d "$LOCK_DIR" && ! -L "$LOCK_DIR" ]] \
        && [[ "$(path_identity "$LOCK_DIR" 2>/dev/null || true)" == "$lock_identity" ]]; then
        rmdir -- "$LOCK_DIR" 2>/dev/null || true
    fi
    lock_owned=0
}

cleanup() {
    local status=$?
    trap - EXIT HUP INT TERM
    set +e
    rollback_publish
    [[ -z "$temporary_apk" ]] || rm -f -- "$temporary_apk"
    [[ -z "$temporary_apk_idsig" ]] || rm -f -- "$temporary_apk_idsig"
    [[ -z "$temporary_sha" ]] || rm -f -- "$temporary_sha"
    cleanup_signing_temp
    cleanup_backup
    release_lock
    FRAMEARCHIVE_STORE_PASSWORD=''
    FRAMEARCHIVE_KEY_PASSWORD=''
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

for command_name in java keytool openssl sha256sum unzip grep awk mktemp cut chmod mkdir mv cp rm stat rmdir; do
    command -v "$command_name" >/dev/null 2>&1 || fail "缺少必需命令：$command_name"
done
[[ -x "$GRADLEW" ]] || fail "找不到可执行的 Gradle Wrapper：$GRADLEW"
[[ -x "$APKSIGNER" ]] || fail "找不到 apksigner：$APKSIGNER；请先运行 scripts/bootstrap-android-sdk.sh"
[[ -d "$ANDROID_HOME/platforms/android-36" ]] \
    || fail "Android 36 平台未安装；请先在交互式终端运行 scripts/bootstrap-android-sdk.sh"

if [[ -e "$ARTIFACT_DIR" || -L "$ARTIFACT_DIR" ]]; then
    [[ -d "$ARTIFACT_DIR" && ! -L "$ARTIFACT_DIR" ]] || fail "artifacts 必须是普通目录且不能是符号链接"
else
    mkdir -- "$ARTIFACT_DIR"
fi
chmod 700 -- "$ARTIFACT_DIR"

# mkdir 在同一文件系统内提供原子、不可跟随符号链接的互斥；任何既有同名路径都关闭失败。
mkdir -- "$LOCK_DIR" 2>/dev/null \
    || fail "发布锁已存在；可能有另一构建正在运行，或上次异常退出，请人工核对：$LOCK_DIR"
lock_owned=1
lock_identity="$(path_identity "$LOCK_DIR")"

read_signing_properties() {
    local line key value
    local seen_store_file=0 seen_store_password=0 seen_alias=0 seen_key_password=0
    FRAMEARCHIVE_STORE_PASSWORD=''
    FRAMEARCHIVE_KEY_PASSWORD=''
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" != *$'\r'* ]] || fail "签名属性文件包含不支持的回车字符：$PROPERTIES_FILE"
        [[ "$line" == *=* ]] || fail "签名属性文件格式不正确：$PROPERTIES_FILE"
        key="${line%%=*}"
        value="${line#*=}"
        case "$key" in
            storeFile)
                ((seen_store_file == 0)) || fail "签名属性 storeFile 重复"
                seen_store_file=1
                [[ "$value" == "$STORE_FILE_PROPERTY" ]] || fail "storeFile 必须为 $STORE_FILE_PROPERTY"
                ;;
            storePassword)
                ((seen_store_password == 0)) || fail "签名属性 storePassword 重复"
                seen_store_password=1
                FRAMEARCHIVE_STORE_PASSWORD="$value"
                ;;
            keyAlias)
                ((seen_alias == 0)) || fail "签名属性 keyAlias 重复"
                seen_alias=1
                [[ "$value" == "$KEY_ALIAS" ]] || fail "keyAlias 必须为 $KEY_ALIAS"
                ;;
            keyPassword)
                ((seen_key_password == 0)) || fail "签名属性 keyPassword 重复"
                seen_key_password=1
                FRAMEARCHIVE_KEY_PASSWORD="$value"
                ;;
            *) fail "签名属性文件包含未知字段：$key" ;;
        esac
    done <"$PROPERTIES_FILE"
    ((seen_store_file == 1 && seen_store_password == 1 && seen_alias == 1 && seen_key_password == 1)) \
        || fail "签名属性文件字段不完整：$PROPERTIES_FILE"
    [[ "$FRAMEARCHIVE_STORE_PASSWORD" =~ ^[0-9a-f]{64}$ ]] \
        || fail "storePassword 格式无效；请从备份恢复完整签名目录"
    [[ "$FRAMEARCHIVE_KEY_PASSWORD" =~ ^[0-9a-f]{64}$ ]] \
        || fail "keyPassword 格式无效；请从备份恢复完整签名目录"
    [[ "$FRAMEARCHIVE_STORE_PASSWORD" != "$FRAMEARCHIVE_KEY_PASSWORD" ]] \
        || fail "仓库密码与私钥密码必须彼此独立"
}

validate_keystore() {
    local keystore_path="$1" details
    details="$(
        FRAMEARCHIVE_STORE_PASSWORD="$FRAMEARCHIVE_STORE_PASSWORD" \
            keytool -list -v \
                -keystore "$keystore_path" \
                -storepass:env FRAMEARCHIVE_STORE_PASSWORD \
                -alias "$KEY_ALIAS" \
                -J-Duser.language=en \
                -J-Duser.country=US 2>/dev/null
    )" || fail "无法读取 release 签名；请从备份恢复 android/signing"
    grep -Fq 'Entry type: PrivateKeyEntry' <<<"$details" || fail "release alias 不是私钥条目"
    grep -Fq 'Owner: CN=Frame Archive Mobile, O=Local' <<<"$details" || fail "release 证书主题不符合预期"
}

create_signing_material() {
    local staged_keystore staged_properties
    FRAMEARCHIVE_STORE_PASSWORD="$(openssl rand -hex 32)"
    FRAMEARCHIVE_KEY_PASSWORD="$(openssl rand -hex 32)"
    while [[ "$FRAMEARCHIVE_STORE_PASSWORD" == "$FRAMEARCHIVE_KEY_PASSWORD" ]]; do
        FRAMEARCHIVE_KEY_PASSWORD="$(openssl rand -hex 32)"
    done
    temporary_signing_dir="$(mktemp -d "$ANDROID_DIR/.signing.new.XXXXXX")"
    chmod 700 -- "$temporary_signing_dir"
    staged_keystore="$temporary_signing_dir/frame-archive-mobile-release.jks"
    staged_properties="$temporary_signing_dir/keystore.properties"
    FRAMEARCHIVE_STORE_PASSWORD="$FRAMEARCHIVE_STORE_PASSWORD" \
    FRAMEARCHIVE_KEY_PASSWORD="$FRAMEARCHIVE_KEY_PASSWORD" \
        keytool -genkeypair \
            -alias "$KEY_ALIAS" -keyalg RSA -keysize 4096 -sigalg SHA256withRSA \
            -validity 9125 -dname "CN=Frame Archive Mobile, O=Local" -storetype JKS \
            -keystore "$staged_keystore" \
            -storepass:env FRAMEARCHIVE_STORE_PASSWORD \
            -keypass:env FRAMEARCHIVE_KEY_PASSWORD -noprompt >/dev/null

    if [[ -n "${FRAMEARCHIVE_TEST_FAIL_AFTER_KEYSTORE:-}" ]]; then
        [[ "${FRAMEARCHIVE_TEST_FAIL_AFTER_KEYSTORE}" == 1 \
            && "${FRAMEARCHIVE_BUILD_TEST_ROOT:-}" == "$PROJECT_ROOT" \
            && ! -e "$PROJECT_ROOT/.git" ]] \
            || fail "拒绝在正式项目目录启用签名初始化测试钩子"
        fail "测试钩子：在生成 keystore 后模拟失败"
    fi

    printf '%s\n' \
        "storeFile=$STORE_FILE_PROPERTY" \
        "storePassword=$FRAMEARCHIVE_STORE_PASSWORD" \
        "keyAlias=$KEY_ALIAS" \
        "keyPassword=$FRAMEARCHIVE_KEY_PASSWORD" >"$staged_properties"
    chmod 600 -- "$staged_keystore" "$staged_properties"
    validate_keystore "$staged_keystore"
    [[ ! -e "$SIGNING_DIR" && ! -L "$SIGNING_DIR" ]] \
        || fail "签名目录刚刚由其他进程创建；不会覆盖，请核对后重试"
    if ! mv --no-copy --update=none-fail -T -- "$temporary_signing_dir" "$SIGNING_DIR"; then
        fail "无法原子发布签名目录；不会覆盖现有签名"
    fi
    temporary_signing_dir=''
    printf '已创建持久 release 签名：%s\n' "$SIGNING_DIR"
}

if [[ -e "$SIGNING_DIR" || -L "$SIGNING_DIR" ]]; then
    [[ -d "$SIGNING_DIR" && ! -L "$SIGNING_DIR" ]] \
        || fail "android/signing 必须是普通目录且不能是符号链接"
else
    create_signing_material
fi
[[ -f "$KEYSTORE_FILE" && ! -L "$KEYSTORE_FILE" ]] \
    || fail "release keystore 缺失、不是普通文件或是符号链接；请从备份恢复整个签名目录"
[[ -f "$PROPERTIES_FILE" && ! -L "$PROPERTIES_FILE" ]] \
    || fail "keystore.properties 缺失、不是普通文件或是符号链接；请从备份恢复整个签名目录"
chmod 700 -- "$SIGNING_DIR"
chmod 600 -- "$KEYSTORE_FILE" "$PROPERTIES_FILE"
read_signing_properties
validate_keystore "$KEYSTORE_FILE"

keystore_cert_digest="$(
    FRAMEARCHIVE_STORE_PASSWORD="$FRAMEARCHIVE_STORE_PASSWORD" \
        keytool -exportcert -keystore "$KEYSTORE_FILE" \
            -storepass:env FRAMEARCHIVE_STORE_PASSWORD -alias "$KEY_ALIAS" 2>/dev/null \
        | sha256sum | cut -d' ' -f1
)" || fail "无法计算 release 证书摘要"
[[ "$keystore_cert_digest" =~ ^[0-9a-f]{64}$ ]] || fail "release 证书摘要格式无效"

apk_signer_digest() {
    local apk_path="$1" output signer_count digest_count digest
    output="$("$APKSIGNER" verify --verbose --print-certs --min-sdk-version 21 "$apk_path" 2>/dev/null)" \
        || return 1
    grep -Fq 'Verified using v1 scheme (JAR signing): true' <<<"$output" || return 1
    grep -Fq 'Verified using v2 scheme (APK Signature Scheme v2): true' <<<"$output" || return 1
    signer_count="$(awk -F': ' '/^Number of signers:/ {print $2}' <<<"$output")"
    [[ "$signer_count" == 1 ]] || return 1
    digest_count="$(grep -c '^Signer #1 certificate SHA-256 digest: ' <<<"$output")"
    [[ "$digest_count" == 1 ]] || return 1
    digest="$(awk -F': ' '/^Signer #1 certificate SHA-256 digest:/ {print $2}' <<<"$output")"
    [[ "$digest" =~ ^[0-9a-fA-F]{64}$ ]] || return 1
    printf '%s\n' "${digest,,}"
}

previous_signer_digest=''
artifact_exists=0
sha_exists=0
[[ -e "$ARTIFACT_APK" || -L "$ARTIFACT_APK" ]] && artifact_exists=1
[[ -e "$ARTIFACT_SHA" || -L "$ARTIFACT_SHA" ]] && sha_exists=1
((artifact_exists == sha_exists)) \
    || fail "现有发布产物只剩 APK 或 SHA-256 文件；为避免破坏旧版本，不会覆盖"
if ((artifact_exists == 1)); then
    [[ -f "$ARTIFACT_APK" && ! -L "$ARTIFACT_APK" ]] || fail "现有 APK 不是普通文件或是符号链接"
    [[ -f "$ARTIFACT_SHA" && ! -L "$ARTIFACT_SHA" ]] || fail "现有 SHA-256 文件不是普通文件或是符号链接"
    (cd "$ARTIFACT_DIR" && sha256sum --check --status "${ARTIFACT_NAME}.sha256") \
        || fail "现有 APK 的 SHA-256 校验失败；不会覆盖旧产物"
    previous_signer_digest="$(apk_signer_digest "$ARTIFACT_APK")" \
        || fail "现有 APK 无效、签名方案不完整或 signer 数量不是 1；不会覆盖"
    [[ "$previous_signer_digest" == "$keystore_cert_digest" ]] \
        || fail "现有 APK 与当前 keystore 的 signer 不一致；不会覆盖，请恢复正确签名备份"
fi

printf '%s\n' '正在执行 release 单元测试、Lint 和 APK 构建……'
(cd "$ANDROID_DIR" && "$GRADLEW" testReleaseUnitTest lintRelease assembleRelease)
[[ -f "$BUILT_APK" ]] || fail "Gradle 未生成 release APK：$BUILT_APK"

temporary_apk="$(mktemp "$ARTIFACT_DIR/.${ARTIFACT_NAME}.XXXXXX")"
temporary_apk_idsig="${temporary_apk}.idsig"
rm -f -- "$temporary_apk"
FRAMEARCHIVE_STORE_PASSWORD="$FRAMEARCHIVE_STORE_PASSWORD" \
FRAMEARCHIVE_KEY_PASSWORD="$FRAMEARCHIVE_KEY_PASSWORD" \
    "$APKSIGNER" sign \
        --ks "$KEYSTORE_FILE" --ks-key-alias "$KEY_ALIAS" \
        --ks-pass env:FRAMEARCHIVE_STORE_PASSWORD --key-pass env:FRAMEARCHIVE_KEY_PASSWORD \
        --min-sdk-version 21 --v1-signing-enabled true --v2-signing-enabled true \
        --v4-signing-enabled false --out "$temporary_apk" "$BUILT_APK"

verify_output="$("$APKSIGNER" verify --verbose --print-certs --min-sdk-version 21 "$temporary_apk")" \
    || fail "release APK 签名验证失败"
candidate_signer_digest="$(apk_signer_digest "$temporary_apk")" \
    || fail "新 APK 无效、签名方案不完整或 signer 数量不是 1"
[[ "$candidate_signer_digest" == "$keystore_cert_digest" ]] || fail "新 APK signer 与当前 keystore 不一致"
if [[ -n "$previous_signer_digest" ]]; then
    [[ "$candidate_signer_digest" == "$previous_signer_digest" ]] || fail "新 APK signer 与上一版发布 APK 不一致"
fi
unzip -tq "$temporary_apk" >/dev/null || fail "release APK 的 ZIP 结构无效"
rm -f -- "$temporary_apk_idsig"
temporary_apk_idsig=''
chmod 644 -- "$temporary_apk"

artifact_hash="$(sha256sum "$temporary_apk" | cut -d' ' -f1)"
temporary_sha="$(mktemp "$ARTIFACT_DIR/.${ARTIFACT_NAME}.sha256.XXXXXX")"
printf '%s  %s\n' "$artifact_hash" "$ARTIFACT_NAME" >"$temporary_sha"
chmod 644 -- "$temporary_sha"

backup_dir="$(mktemp -d "$ARTIFACT_DIR/.release-backup.XXXXXX")"
backup_identity="$(path_identity "$backup_dir")"
if ((artifact_exists == 1)); then
    cp --preserve=mode,timestamps -- "$ARTIFACT_APK" "$backup_dir/$ARTIFACT_NAME"
    cp --preserve=mode,timestamps -- "$ARTIFACT_SHA" "$backup_dir/${ARTIFACT_NAME}.sha256"
    had_previous_release=1
fi

# 两次 rename 之间收到的正常信号会触发 EXIT trap，从私有备份恢复旧的一对文件。
# SIGKILL 无法被任何进程捕获，这是本地文件系统发布流程的固有限制。
publish_in_progress=1
mv -f -- "$temporary_apk" "$ARTIFACT_APK"
temporary_apk=''
if [[ -n "${FRAMEARCHIVE_TEST_FAIL_AFTER_APK_RENAME:-}" ]]; then
    [[ "${FRAMEARCHIVE_TEST_FAIL_AFTER_APK_RENAME}" == 1 \
        && "${FRAMEARCHIVE_BUILD_TEST_ROOT:-}" == "$PROJECT_ROOT" \
        && ! -e "$PROJECT_ROOT/.git" ]] \
        || fail "拒绝在正式项目目录启用发布回滚测试钩子"
    fail "测试钩子：在 APK rename 后、SHA-256 rename 前模拟失败"
fi
mv -f -- "$temporary_sha" "$ARTIFACT_SHA"
temporary_sha=''
(cd "$ARTIFACT_DIR" && sha256sum --check --status "${ARTIFACT_NAME}.sha256") \
    || fail "发布后的 APK 与 SHA-256 文件不匹配"
published_signer_digest="$(apk_signer_digest "$ARTIFACT_APK")" || fail "发布后的 APK 签名验证失败"
[[ "$published_signer_digest" == "$keystore_cert_digest" ]] || fail "发布后的 APK signer 发生变化"
publish_in_progress=0
cleanup_backup

printf '\n%s\n' 'APK 签名验证结果：'
grep -E '^(Verifies|Verified using v1|Verified using v2|Number of signers|Signer #1 certificate (DN|SHA-256 digest)|Signer #1 key (algorithm|size))' \
    <<<"$verify_output"
printf '\nAPK：%s\nSHA-256：%s\n' "$ARTIFACT_APK" "$artifact_hash"
printf '%s\n' '重要：请立即备份整个 android/signing/ 目录。丢失后，后续版本将无法覆盖安装当前 APK。'
