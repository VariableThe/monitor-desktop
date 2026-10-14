#!/usr/bin/env sh
# Update an existing viewfindrr installation without touching global Python.
set -eu

repository="https://github.com/VariableThe/viewfindrr"
archive_url="${VIEWFINDRR_TARBALL_URL:-$repository/archive/refs/heads/main.tar.gz}"
install_dir="${VIEWFINDRR_HOME:-$HOME/.local/share/viewfindrr}"
python_bin="$install_dir/.venv/bin/python"

fail() {
    printf '%s\n' "viewfindrr update failed: $*" >&2
    exit 1
}

[ -x "$python_bin" ] || fail "no installed application was found at $install_dir. Run the installer first."
command -v curl >/dev/null 2>&1 || fail "curl is required to download the update."
command -v tar >/dev/null 2>&1 || fail "tar is required to unpack the update."

temporary_dir=$(mktemp -d "${TMPDIR:-/tmp}/viewfindrr-update.XXXXXX")
cleanup() {
    rm -rf "$temporary_dir"
}
trap cleanup EXIT HUP INT TERM

printf '%s\n' "Downloading viewfindrr update..."
curl -fsSL "$archive_url" -o "$temporary_dir/viewfindrr.tar.gz"
mkdir -p "$temporary_dir/source"
tar -xzf "$temporary_dir/viewfindrr.tar.gz" --strip-components=1 -C "$temporary_dir/source"

printf '%s\n' "Installing update..."
"$python_bin" -m pip install --upgrade "$temporary_dir/source"
printf '%s\n' "viewfindrr updated. Restart the app to use the new version."
