#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
ANDROID_SDK_DIR="$PROJECT_ROOT/.android-sdk"
TOOLS_ARCHIVE="commandlinetools-linux-15859902_latest.zip"
TOOLS_SHA256="4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583"
TOOLS_REVISION="22.0"
TOOLS_URL="https://dl.google.com/android/repository/$TOOLS_ARCHIVE"
TOOLS_DIR="$ANDROID_SDK_DIR/cmdline-tools/latest"
TOOLS_MARKER="$TOOLS_DIR/.framearchive-command-line-tools"
SDKMANAGER="$TOOLS_DIR/bin/sdkmanager"
LOCAL_PROPERTIES_MARKER='# Managed by scripts/bootstrap-android-sdk.sh; this file may be replaced.'

tools_payload_matches() {
    local tools_dir="$1"
    local revision_lines=''
    [[ -f "$tools_dir/source.properties" ]] || return 1
    revision_lines="$(grep -E '^Pkg\.Revision=' "$tools_dir/source.properties" || true)"
    [[ -x "$tools_dir/bin/sdkmanager" ]] \
        && [[ "$revision_lines" == "Pkg.Revision=$TOOLS_REVISION" ]]
}

tools_installation_matches() {
    tools_payload_matches "$TOOLS_DIR" \
        && [[ -f "$TOOLS_MARKER" ]] \
        && [[ "$(<"$TOOLS_MARKER")" == "$TOOLS_ARCHIVE"$'\n'"$TOOLS_SHA256" ]]
}

install_command_line_tools() (
    set -euo pipefail
    local download_dir archive_path tools_parent staged_tools backup_tools
    download_dir="$(mktemp -d "${TMPDIR:-/tmp}/framearchive-android-sdk.XXXXXX")"
    trap 'rm -rf -- "$download_dir"' EXIT
    archive_path="$download_dir/$TOOLS_ARCHIVE"

    curl --fail --location --retry 3 --retry-all-errors --output "$archive_path" "$TOOLS_URL"
    printf '%s  %s\n' "$TOOLS_SHA256" "$archive_path" | sha256sum --check --status

    unzip -q "$archive_path" -d "$download_dir/unpacked"
    tools_payload_matches "$download_dir/unpacked/cmdline-tools"
    printf '%s\n%s\n' "$TOOLS_ARCHIVE" "$TOOLS_SHA256" \
        >"$download_dir/unpacked/cmdline-tools/.framearchive-command-line-tools"

    tools_parent="$ANDROID_SDK_DIR/cmdline-tools"
    mkdir -p "$tools_parent"
    staged_tools="$(mktemp -d "$tools_parent/.latest.new.XXXXXX")"
    rmdir -- "$staged_tools"
    mv -- "$download_dir/unpacked/cmdline-tools" "$staged_tools"

    backup_tools="$tools_parent/.latest.previous"
    if [[ ! -e "$TOOLS_DIR" && -e "$backup_tools" ]]; then
        mv -- "$backup_tools" "$TOOLS_DIR"
    elif [[ -e "$TOOLS_DIR" && -e "$backup_tools" ]]; then
        rm -rf -- "$backup_tools"
    fi
    if [[ -e "$TOOLS_DIR" ]]; then
        mv -- "$TOOLS_DIR" "$backup_tools"
    fi
    if ! mv -- "$staged_tools" "$TOOLS_DIR"; then
        [[ ! -e "$TOOLS_DIR" && -e "$backup_tools" ]] && mv -- "$backup_tools" "$TOOLS_DIR"
        exit 1
    fi
    rm -rf -- "$backup_tools"
)

escape_java_properties_value() {
    local value="$1" escaped='' character index
    LC_ALL=C
    for ((index = 0; index < ${#value}; index++)); do
        character="${value:index:1}"
        case "$character" in
            \\|':'|'='|' '|'#'|'!') escaped+="\\$character" ;;
            $'\t') escaped+='\t' ;;
            $'\n') escaped+='\n' ;;
            $'\r') escaped+='\r' ;;
            *) escaped+="$character" ;;
        esac
    done
    printf '%s' "$escaped"
}

local_properties_is_managed() {
    local properties_file="$1" actual_content='' expected_content=''
    local -a lines=()
    [[ -f "$properties_file" && ! -L "$properties_file" ]] || return 1
    mapfile -t lines <"$properties_file"
    [[ ${#lines[@]} -eq 2 ]] \
        && [[ "${lines[0]}" == "$LOCAL_PROPERTIES_MARKER" ]] \
        && [[ "${lines[1]}" == sdk.dir=* ]] \
        || return 1
    printf -v expected_content '%s\n%s\n' "$LOCAL_PROPERTIES_MARKER" "${lines[1]}"
    IFS= read -r -d '' actual_content <"$properties_file" || true
    [[ "$actual_content" == "$expected_content" ]]
}

write_local_properties() (
    set -euo pipefail
    local properties_dir properties_file temporary_file escaped_sdk_dir
    properties_dir="$PROJECT_ROOT/android"
    properties_file="$properties_dir/local.properties"
    mkdir -p "$properties_dir"
    if [[ -e "$properties_file" || -L "$properties_file" ]] \
        && ! local_properties_is_managed "$properties_file"; then
        printf '错误：%s 已存在且不是本脚本管理的精确格式；为避免丢失本地配置，不会覆盖。\n' "$properties_file" >&2
        printf '%s\n' '请手工设置 sdk.dir，或备份并删除/迁移该文件后重试。' >&2
        exit 1
    fi
    temporary_file="$(mktemp "$properties_dir/.local.properties.XXXXXX")"
    trap 'rm -f -- "$temporary_file"' EXIT
    escaped_sdk_dir="$(escape_java_properties_value "$ANDROID_SDK_DIR")"
    printf '%s\nsdk.dir=%s\n' "$LOCAL_PROPERTIES_MARKER" "$escaped_sdk_dir" >"$temporary_file"
    mv -- "$temporary_file" "$properties_file"
)

for command_name in curl grep sha256sum unzip; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        printf '缺少必需命令：%s\n' "$command_name" >&2
        exit 1
    fi
done

if ! tools_installation_matches; then
    install_command_line_tools
fi

write_local_properties

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
