#!/usr/bin/env bash

set -u -o pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
BOOTSTRAP_SCRIPT="$SCRIPT_DIR/bootstrap-android-sdk.sh"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/framearchive-bootstrap-tests.XXXXXX")"
failures=0
trap 'rm -rf -- "$TEST_ROOT"' EXIT

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    failures=$((failures + 1))
}

make_fake_commands() {
    local fake_bin="$1"
    mkdir -p "$fake_bin"

    cat >"$fake_bin/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
output=''
while (($#)); do
    if [[ "$1" == "--output" ]]; then
        output="$2"
        shift 2
    else
        shift
    fi
done
: >"$output"
printf 'downloaded\n' >>"$FAKE_DOWNLOAD_LOG"
EOF

    cat >"$fake_bin/sha256sum" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF

    cat >"$fake_bin/unzip" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
destination=''
while (($#)); do
    if [[ "$1" == "-d" ]]; then
        destination="$2"
        shift 2
    else
        shift
    fi
done
tools_dir="$destination/cmdline-tools"
mkdir -p "$tools_dir/bin"
cat >"$tools_dir/bin/sdkmanager" <<'SCRIPT'
#!/usr/bin/env bash
exit 0
SCRIPT
chmod +x "$tools_dir/bin/sdkmanager"
printf 'Pkg.Revision=%s\nPkg.Path=cmdline-tools;%s\n' "$FAKE_TOOLS_REVISION" "$FAKE_TOOLS_REVISION" >"$tools_dir/source.properties"
EOF

    chmod +x "$fake_bin/curl" "$fake_bin/sha256sum" "$fake_bin/unzip"
}

make_project() {
    local project_root="$1"
    mkdir -p "$project_root/scripts" "$project_root/android"
    cp "$BOOTSTRAP_SCRIPT" "$project_root/scripts/bootstrap-android-sdk.sh"
}

test_writes_escaped_local_properties_before_license_prompt() {
    local case_root="$TEST_ROOT/local-properties-case"
    local project_root="$case_root/project with spaces:#!=\path"
    local fake_bin="$case_root/fake-bin"
    local download_log="$case_root/download.log"
    make_project "$project_root"
    make_fake_commands "$fake_bin"

    if PATH="$fake_bin:$PATH" \
        FAKE_DOWNLOAD_LOG="$download_log" \
        FAKE_TOOLS_REVISION="22.0" \
        "$project_root/scripts/bootstrap-android-sdk.sh" </dev/null >/dev/null 2>&1; then
        fail "bootstrap must stop for interactive license review"
    fi

    local expected="sdk.dir=$case_root/project\\ with\\ spaces\\:\\#\\!\\=\\\\path/.android-sdk"
    local actual=''
    [[ -f "$project_root/android/local.properties" ]] && actual="$(<"$project_root/android/local.properties")"
    [[ "$actual" == "$expected" ]] || fail "bootstrap must atomically write escaped local.properties; expected <$expected>, got <$actual>"
}

test_reinstalls_when_marker_is_forged_but_revision_is_wrong() {
    local case_root="$TEST_ROOT/mismatched tools case"
    local project_root="$case_root/project"
    local fake_bin="$case_root/fake-bin"
    local download_log="$case_root/download.log"
    local tools_dir="$project_root/.android-sdk/cmdline-tools/latest"
    make_project "$project_root"
    make_fake_commands "$fake_bin"
    mkdir -p "$tools_dir/bin"
    printf '#!/usr/bin/env bash\nexit 0\n' >"$tools_dir/bin/sdkmanager"
    chmod +x "$tools_dir/bin/sdkmanager"
    printf 'Pkg.Revision=0.0\n' >"$tools_dir/source.properties"
    printf '%s\n%s\n' \
        'commandlinetools-linux-15859902_latest.zip' \
        '4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583' \
        >"$tools_dir/.framearchive-command-line-tools"

    if PATH="$fake_bin:$PATH" \
        FAKE_DOWNLOAD_LOG="$download_log" \
        FAKE_TOOLS_REVISION="22.0" \
        "$project_root/scripts/bootstrap-android-sdk.sh" </dev/null >/dev/null 2>&1; then
        fail "bootstrap must stop for interactive license review"
    fi

    [[ -s "$download_log" ]] || fail "bootstrap must redownload tools when source.properties does not match the pinned revision"
    grep -Fqx 'Pkg.Revision=22.0' "$tools_dir/source.properties" 2>/dev/null || fail "bootstrap must replace mismatched command-line tools"
}

test_writes_escaped_local_properties_before_license_prompt
test_reinstalls_when_marker_is_forged_but_revision_is_wrong

if ((failures > 0)); then
    printf '%d bootstrap test(s) failed\n' "$failures" >&2
    exit 1
fi

printf '%s\n' 'All bootstrap tests passed.'
