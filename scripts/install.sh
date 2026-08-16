#!/usr/bin/env sh
# Install Monitor Desktop from GitHub into an isolated user-local environment.
set -eu

repository="https://github.com/VariableThe/monitor-desktop"
archive_url="${MONITOR_DESKTOP_TARBALL_URL:-$repository/archive/refs/heads/main.tar.gz}"
install_dir="${MONITOR_DESKTOP_HOME:-$HOME/.local/share/monitor-desktop}"
bin_dir="${MONITOR_DESKTOP_BIN:-$HOME/.local/bin}"
python_bin="${PYTHON_BIN:-python3}"

fail() {
    printf '%s\n' "Monitor Desktop install failed: $*" >&2
    exit 1
}

command -v "$python_bin" >/dev/null 2>&1 || fail "Python 3.11 or newer is required."
command -v curl >/dev/null 2>&1 || fail "curl is required to download the application."
command -v tar >/dev/null 2>&1 || fail "tar is required to unpack the application."
"$python_bin" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' || fail "Python 3.11 or newer is required."

if [ -e "$install_dir" ]; then
    fail "an installation already exists at $install_dir. Remove it before reinstalling."
fi

temporary_dir=$(mktemp -d "${TMPDIR:-/tmp}/monitor-desktop.XXXXXX")
install_created=0
install_complete=0
cleanup() {
    rm -rf "$temporary_dir"
    if [ "$install_created" -eq 1 ] && [ "$install_complete" -ne 1 ]; then
        rm -rf "$install_dir"
    fi
}
trap cleanup EXIT HUP INT TERM

printf '%s\n' "Downloading Monitor Desktop..."
curl -fsSL "$archive_url" -o "$temporary_dir/monitor-desktop.tar.gz"
mkdir -p "$temporary_dir/source"
tar -xzf "$temporary_dir/monitor-desktop.tar.gz" --strip-components=1 -C "$temporary_dir/source"

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
    "exec \"$install_dir/.venv/bin/monitor-desktop\" \"\$@\"" \
    > "$bin_dir/monitor-desktop"
chmod +x "$bin_dir/monitor-desktop"

install_complete=1
printf '%s\n' "Monitor Desktop is installed."
printf '%s\n' "Launch it with: $bin_dir/monitor-desktop"
printf '%s\n' "Add $bin_dir to PATH to launch it as: monitor-desktop"
