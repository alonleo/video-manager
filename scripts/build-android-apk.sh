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

FRAMEARCHIVE_STORE_PASSWORD=''
FRAMEARCHIVE_KEY_PASSWORD=''
temporary_keystore=''
temporary_properties=''
temporary_apk=''
temporary_apk_idsig=''
temporary_sha=''

cleanup() {
    [[ -z "$temporary_keystore" ]] || rm -f -- "$temporary_keystore"
    [[ -z "$temporary_properties" ]] || rm -f -- "$temporary_properties"
    [[ -z "$temporary_apk" ]] || rm -f -- "$temporary_apk"
    [[ -z "$temporary_apk_idsig" ]] || rm -f -- "$temporary_apk_idsig"
    [[ -z "$temporary_sha" ]] || rm -f -- "$temporary_sha"
    unset FRAMEARCHIVE_STORE_PASSWORD FRAMEARCHIVE_KEY_PASSWORD
}
trap cleanup EXIT

fail() {
    printf '错误：%s\n' "$1" >&2
    exit 1
}

for command_name in java keytool openssl sha256sum unzip grep mktemp cut chmod mv flock; do
    command -v "$command_name" >/dev/null 2>&1 \
        || fail "缺少必需命令：$command_name"
done
[[ -x "$GRADLEW" ]] || fail "找不到可执行的 Gradle Wrapper：$GRADLEW"
[[ -x "$APKSIGNER" ]] || fail "找不到 apksigner：$APKSIGNER；请先运行 scripts/bootstrap-android-sdk.sh"
[[ -d "$ANDROID_HOME/platforms/android-36" ]] \
    || fail "Android 36 平台未安装；请先在交互式终端运行 scripts/bootstrap-android-sdk.sh"

[[ ! -L "$SIGNING_DIR" ]] || fail "android/signing 不能是符号链接"
mkdir -p -- "$SIGNING_DIR"
[[ -d "$SIGNING_DIR" ]] || fail "android/signing 不是目录"
chmod 700 -- "$SIGNING_DIR"
exec 9>"$SIGNING_DIR/.build-android-apk.lock"
flock -n 9 || fail "另一个 Android APK 构建正在使用签名目录，请稍后重试"

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
                [[ "$value" == "$STORE_FILE_PROPERTY" ]] \
                    || fail "storeFile 必须为 $STORE_FILE_PROPERTY"
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
    export FRAMEARCHIVE_STORE_PASSWORD FRAMEARCHIVE_KEY_PASSWORD
}

create_signing_material() {
    FRAMEARCHIVE_STORE_PASSWORD="$(openssl rand -hex 32)"
    FRAMEARCHIVE_KEY_PASSWORD="$(openssl rand -hex 32)"
    while [[ "$FRAMEARCHIVE_STORE_PASSWORD" == "$FRAMEARCHIVE_KEY_PASSWORD" ]]; do
        FRAMEARCHIVE_KEY_PASSWORD="$(openssl rand -hex 32)"
    done
    export FRAMEARCHIVE_STORE_PASSWORD FRAMEARCHIVE_KEY_PASSWORD

    temporary_keystore="$(mktemp "$SIGNING_DIR/.frame-archive-mobile-release.XXXXXX.jks")"
    rm -f -- "$temporary_keystore"
    keytool -genkeypair \
        -alias "$KEY_ALIAS" \
        -keyalg RSA \
        -keysize 4096 \
        -sigalg SHA256withRSA \
        -validity 9125 \
        -dname "CN=Frame Archive Mobile, O=Local" \
        -storetype JKS \
        -keystore "$temporary_keystore" \
        -storepass:env FRAMEARCHIVE_STORE_PASSWORD \
        -keypass:env FRAMEARCHIVE_KEY_PASSWORD \
        -noprompt >/dev/null

    temporary_properties="$(mktemp "$SIGNING_DIR/.keystore.properties.XXXXXX")"
    printf '%s\n' \
        "storeFile=$STORE_FILE_PROPERTY" \
        "storePassword=$FRAMEARCHIVE_STORE_PASSWORD" \
        "keyAlias=$KEY_ALIAS" \
        "keyPassword=$FRAMEARCHIVE_KEY_PASSWORD" \
        >"$temporary_properties"
    chmod 600 -- "$temporary_keystore" "$temporary_properties"

    # 两个最终文件都已在开头确认不存在；禁止覆盖以保护升级签名身份。
    [[ ! -e "$KEYSTORE_FILE" && ! -L "$KEYSTORE_FILE" ]] || fail "签名文件刚刚被其他进程创建，请重试"
    [[ ! -e "$PROPERTIES_FILE" && ! -L "$PROPERTIES_FILE" ]] || fail "签名属性刚刚被其他进程创建，请重试"
    mv -- "$temporary_keystore" "$KEYSTORE_FILE"
    temporary_keystore=''
    mv -- "$temporary_properties" "$PROPERTIES_FILE"
    temporary_properties=''
    printf '已创建持久 release 签名：%s\n' "$SIGNING_DIR"
}

keystore_exists=0
properties_exists=0
[[ -e "$KEYSTORE_FILE" || -L "$KEYSTORE_FILE" ]] && keystore_exists=1
[[ -e "$PROPERTIES_FILE" || -L "$PROPERTIES_FILE" ]] && properties_exists=1

if ((keystore_exists != properties_exists)); then
    fail "android/signing 只存在一部分签名材料。不会覆盖；请从备份恢复整个目录后重试"
fi
if ((keystore_exists == 0)); then
    create_signing_material
fi

[[ -f "$KEYSTORE_FILE" && ! -L "$KEYSTORE_FILE" ]] \
    || fail "release keystore 必须是普通文件且不能是符号链接"
[[ -f "$PROPERTIES_FILE" && ! -L "$PROPERTIES_FILE" ]] \
    || fail "keystore.properties 必须是普通文件且不能是符号链接"
chmod 600 -- "$KEYSTORE_FILE" "$PROPERTIES_FILE"
read_signing_properties

keystore_details="$(keytool -list -v \
    -keystore "$KEYSTORE_FILE" \
    -storepass:env FRAMEARCHIVE_STORE_PASSWORD \
    -alias "$KEY_ALIAS" \
    -J-Duser.language=en \
    -J-Duser.country=US 2>/dev/null)" \
    || fail "无法读取 release 签名；请从备份恢复 android/signing"
grep -Fq 'Entry type: PrivateKeyEntry' <<<"$keystore_details" \
    || fail "release alias 不是私钥条目"
grep -Fq 'Owner: CN=Frame Archive Mobile, O=Local' <<<"$keystore_details" \
    || fail "release 证书主题不符合预期"

printf '%s\n' '正在执行 release 单元测试、Lint 和 APK 构建……'
(cd "$ANDROID_DIR" && "$GRADLEW" testReleaseUnitTest lintRelease assembleRelease)

[[ -f "$BUILT_APK" ]] || fail "Gradle 未生成 release APK：$BUILT_APK"

# AGP 产物已使用同一 release key 签名；这里再由固定版本 apksigner 规范化一次，
# 并显式生成兼容 Android 8+ 的 v1 与 v2 签名后再发布。
mkdir -p -- "$ARTIFACT_DIR"
temporary_apk="$(mktemp "$ARTIFACT_DIR/.${ARTIFACT_NAME}.XXXXXX")"
temporary_apk_idsig="${temporary_apk}.idsig"
rm -f -- "$temporary_apk"
$APKSIGNER sign \
    --ks "$KEYSTORE_FILE" \
    --ks-key-alias "$KEY_ALIAS" \
    --ks-pass env:FRAMEARCHIVE_STORE_PASSWORD \
    --key-pass env:FRAMEARCHIVE_KEY_PASSWORD \
    --min-sdk-version 21 \
    --v1-signing-enabled true \
    --v2-signing-enabled true \
    --v4-signing-enabled false \
    --out "$temporary_apk" \
    "$BUILT_APK"

verify_output="$($APKSIGNER verify --verbose --print-certs --min-sdk-version 21 "$temporary_apk")" \
    || fail "release APK 签名验证失败"
grep -Fq 'Verified using v1 scheme (JAR signing): true' <<<"$verify_output" \
    || fail "release APK 未启用 v1 签名"
grep -Fq 'Verified using v2 scheme (APK Signature Scheme v2): true' <<<"$verify_output" \
    || fail "release APK 未启用 v2 签名"
grep -Fq 'CN=Frame Archive Mobile' <<<"$verify_output" \
    || fail "release APK 没有使用预期证书"
if grep -Fqi 'Android Debug' <<<"$verify_output"; then
    fail "release APK 错误地使用了 debug 证书"
fi
unzip -tq "$temporary_apk" >/dev/null || fail "release APK 的 ZIP 结构无效"

rm -f -- "$temporary_apk_idsig"
temporary_apk_idsig=''
chmod 644 -- "$temporary_apk"
mv -f -- "$temporary_apk" "$ARTIFACT_APK"
temporary_apk=''

artifact_hash="$(sha256sum "$ARTIFACT_APK" | cut -d' ' -f1)"
temporary_sha="$(mktemp "$ARTIFACT_DIR/.${ARTIFACT_NAME}.sha256.XXXXXX")"
printf '%s  %s\n' "$artifact_hash" "$ARTIFACT_NAME" >"$temporary_sha"
chmod 644 -- "$temporary_sha"
mv -f -- "$temporary_sha" "$ARTIFACT_SHA"
temporary_sha=''
(cd "$ARTIFACT_DIR" && sha256sum --check "${ARTIFACT_NAME}.sha256")

printf '\n%s\n' 'APK 签名验证结果：'
grep -E '^(Verifies|Verified using v1|Verified using v2|Number of signers|Signer #1 certificate (DN|SHA-256 digest)|Signer #1 key (algorithm|size))' \
    <<<"$verify_output"
printf '\nAPK：%s\nSHA-256：%s\n' "$ARTIFACT_APK" "$artifact_hash"
printf '%s\n' '重要：请立即备份整个 android/signing/ 目录。丢失后，后续版本将无法覆盖安装当前 APK。'
