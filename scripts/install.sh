#!/usr/bin/env sh
# Install viewfindrr from GitHub into an isolated user-local environment.
set -eu

repository="https://github.com/VariableThe/viewfindrr"
archive_url="${VIEWFINDRR_TARBALL_URL:-$repository/archive/refs/heads/main.tar.gz}"
install_dir="${VIEWFINDRR_HOME:-$HOME/.local/share/viewfindrr}"
bin_dir="${VIEWFINDRR_BIN:-$HOME/.local/bin}"
python_bin="${PYTHON_BIN:-python3}"

fail() {
    printf '%s\n' "viewfindrr install failed: $*" >&2
    exit 1
}

command -v "$python_bin" >/dev/null 2>&1 || fail "Python 3.11 or newer is required."
command -v curl >/dev/null 2>&1 || fail "curl is required to download the application."
command -v tar >/dev/null 2>&1 || fail "tar is required to unpack the application."
"$python_bin" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' || fail "Python 3.11 or newer is required."

if [ -e "$install_dir" ]; then
    fail "an installation already exists at $install_dir. Remove it before reinstalling."
fi

temporary_dir=$(mktemp -d "${TMPDIR:-/tmp}/viewfindrr.XXXXXX")
install_created=0
install_complete=0
cleanup() {
    rm -rf "$temporary_dir"
    if [ "$install_created" -eq 1 ] && [ "$install_complete" -ne 1 ]; then
        rm -rf "$install_dir"
    fi
}
trap cleanup EXIT HUP INT TERM

printf '%s\n' "Downloading viewfindrr..."
curl -fsSL "$archive_url" -o "$temporary_dir/viewfindrr.tar.gz"
mkdir -p "$temporary_dir/source"
tar -xzf "$temporary_dir/viewfindrr.tar.gz" --strip-components=1 -C "$temporary_dir/source"

printf '%s\n' "Creating an isolated Python environment..."
mkdir -p "$(dirname "$install_dir")"
mv "$temporary_dir/source" "$install_dir"
install_created=1
"$python_bin" -m venv "$install_dir/.venv"
"$install_dir/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"$install_dir/.venv/bin/python" -m pip install "$install_dir"

mkdir -p "$bin_dir"
printf '%s\n' \
    '#!/usr/bin/env sh' \
    "exec \"$install_dir/.venv/bin/viewfindrr\" \"\$@\"" \
    > "$bin_dir/viewfindrr"
chmod +x "$bin_dir/viewfindrr"

install_complete=1
printf '%s\n' "viewfindrr is installed."
printf '%s\n' "Launch it with: $bin_dir/viewfindrr"
printf '%s\n' "Add $bin_dir to PATH to launch it as: viewfindrr"

maybe_setup_vcam() {
    if [ "$(uname -s)" != "Linux" ]; then
        return 0
    fi
    for vcam_name_file in /sys/class/video4linux/video*/name; do
        [ -f "$vcam_name_file" ] || continue
        if [ "$(cat "$vcam_name_file" 2>/dev/null || true)" = "viewfindrr" ]; then
            printf '%s\n' "Virtual camera already configured; skipping virtual camera setup."
            return 0
        fi
    done
    vcam_script="$install_dir/scripts/setup-v4l2loopback.sh"
    if [ ! -f "$vcam_script" ]; then
        printf '%s\n' "Virtual camera setup script not found at $vcam_script; skipping." >&2
        return 0
    fi
    case "${VIEWFINDRR_SETUP_VCAM:-}" in
        0)
            return 0
            ;;
        1)
            sh "$vcam_script" || printf '%s\n' "Virtual camera setup did not complete; run later with: sudo \"$vcam_script\"" >&2
            return 0
            ;;
    esac
    if [ -t 0 ] && [ -r /dev/tty ]; then
        printf '%s\n' "Optional: expose a 'viewfindrr' virtual camera (/dev/video10) via the v4l2loopback kernel module (one-time setup, will ask for your sudo password, safe to re-run)."
        printf '%s' "Set up the virtual camera now? [y/N] "
        vcam_answer=""
        read -r vcam_answer < /dev/tty || true
        case "$vcam_answer" in
            [yY]|[yY][eE][sS])
                sh "$vcam_script" || printf '%s\n' "Virtual camera setup did not complete; run later with: sudo \"$vcam_script\"" >&2
                ;;
            *)
                printf '%s\n' "Skipping virtual camera setup. To enable it later, run: sudo \"$vcam_script\""
                ;;
        esac
    else
        printf '%s\n' "To enable the 'viewfindrr' virtual camera (/dev/video10), run: sudo \"$vcam_script\""
    fi
}

maybe_setup_vcam
