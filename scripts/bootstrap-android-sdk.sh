#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
ANDROID_SDK_DIR="$PROJECT_ROOT/.android-sdk"
TOOLS_ARCHIVE="commandlinetools-linux-15859902_latest.zip"
TOOLS_SHA256="4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583"
TOOLS_URL="https://dl.google.com/android/repository/$TOOLS_ARCHIVE"
SDKMANAGER="$ANDROID_SDK_DIR/cmdline-tools/latest/bin/sdkmanager"

for command_name in curl sha256sum unzip; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        printf '缺少必需命令：%s\n' "$command_name" >&2
        exit 1
    fi
done

if [[ ! -x "$SDKMANAGER" ]]; then
    download_dir="$(mktemp -d "${TMPDIR:-/tmp}/framearchive-android-sdk.XXXXXX")"
    trap 'rm -rf -- "$download_dir"' EXIT
    archive_path="$download_dir/$TOOLS_ARCHIVE"

    curl --fail --location --retry 3 --retry-all-errors --output "$archive_path" "$TOOLS_URL"
    printf '%s  %s\n' "$TOOLS_SHA256" "$archive_path" | sha256sum --check --status

    unzip -q "$archive_path" -d "$download_dir/unpacked"
    mkdir -p "$ANDROID_SDK_DIR/cmdline-tools"
    rm -rf -- "$ANDROID_SDK_DIR/cmdline-tools/latest"
    mv -- "$download_dir/unpacked/cmdline-tools" "$ANDROID_SDK_DIR/cmdline-tools/latest"
fi

if [[ ! -t 0 || ! -t 1 ]]; then
    printf '%s\n' 'Android SDK 许可证必须由用户在交互式终端中明确审阅和接受。' >&2
    printf '请运行：%q\n' "$SCRIPT_DIR/bootstrap-android-sdk.sh" >&2
    exit 1
fi

printf '%s\n' '即将显示 Android SDK 许可证。请逐项审阅，并在提示时明确选择是否接受。'
"$SDKMANAGER" --sdk_root="$ANDROID_SDK_DIR" --licenses

"$SDKMANAGER" \
    --sdk_root="$ANDROID_SDK_DIR" \
    "platform-tools" \
    "platforms;android-36" \
    "build-tools;36.0.0"

printf 'Android SDK 已安装到：%s\n' "$ANDROID_SDK_DIR"
