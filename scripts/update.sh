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

install_desktop_entry() {
    # $1 = unpacked tree holding packaging/ and monitor_desktop/assets/.
    # $2 = absolute console-script path used for the desktop entry Exec= line.
    source_dir="$1"
    exec_path="$2"
    template="$source_dir/packaging/viewfindrr.desktop"
    png_asset="$source_dir/monitor_desktop/assets/icon.png"
    svg_asset="$source_dir/monitor_desktop/assets/icon.svg"
    apps_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    icons_root="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
    mkdir -p "$apps_dir" "$icons_root/256x256/apps" "$icons_root/scalable/apps"
    if [ ! -f "$template" ]; then
        printf '%s\n' "warning: desktop entry template not found at $template; skipping menu integration." >&2
        return 0
    fi
    escaped_exec=$(printf '%s' "$exec_path" | sed 's/[&|\\]/\\&/g')
    if ! sed "s|@EXEC@|$escaped_exec|g" "$template" > "$apps_dir/viewfindrr.desktop"; then
        printf '%s\n' "warning: could not write $apps_dir/viewfindrr.desktop; skipping menu integration." >&2
        return 0
    fi
    chmod 644 "$apps_dir/viewfindrr.desktop"
    if [ -f "$png_asset" ]; then
        cp -f "$png_asset" "$icons_root/256x256/apps/viewfindrr.png" || printf '%s\n' "warning: could not install the viewfindrr.png icon." >&2
    else
        printf '%s\n' "warning: icon asset not found at $png_asset; skipping PNG icon." >&2
    fi
    if [ -f "$svg_asset" ]; then
        cp -f "$svg_asset" "$icons_root/scalable/apps/viewfindrr.svg" || printf '%s\n' "warning: could not install the viewfindrr.svg icon." >&2
    else
        printf '%s\n' "warning: icon asset not found at $svg_asset; skipping SVG icon." >&2
    fi
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$apps_dir" || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t "$icons_root" || true
    fi
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
install_desktop_entry "$temporary_dir/source" "$install_dir/.venv/bin/viewfindrr"
printf '%s\n' "viewfindrr updated. Restart the app to use the new version."
printf '%s\n' "Find it in your application menu as: viewfindrr"
