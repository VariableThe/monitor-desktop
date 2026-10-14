#!/usr/bin/env sh
# One-time privileged setup for the Monitor Desktop virtual camera.
#
# Creates a persistent v4l2loopback device labelled "Monitor Desktop" at
# /dev/video10 and keeps it across reboots. Run once, explicitly:
#
#   sudo sh scripts/setup-v4l2loopback.sh [--dry-run] [--force]
#
# Invoke with sudo once: the script escalates per command internally,
# so there is a single password prompt up front.
#
# After this runs once, the app itself needs zero privileges.
# This script is the only place that uses sudo, and never installs silently.
#
# Overrides: CARD_LABEL (default "Monitor Desktop"), VIDEO_NR (default 10),
# DRY_RUN=1, FORCE=1, MODPROBE_DIR, MODULES_LOAD_DIR, SYS_CLASS_DIR, DEV_DIR
# (the last four default to the live system paths; point them at temp dirs
# to exercise the dry-run/clobber logic without touching the system).
set -eu

card_label="${CARD_LABEL:-Monitor Desktop}"
video_nr="${VIDEO_NR:-10}"
modprobe_dir="${MODPROBE_DIR:-/etc/modprobe.d}"
modules_load_dir="${MODULES_LOAD_DIR:-/etc/modules-load.d}"
sys_class_dir="${SYS_CLASS_DIR:-/sys/class/video4linux}"
dev_dir="${DEV_DIR:-/dev}"
conf_name="60-monitor-desktop-v4l2loopback.conf"

dry_run="${DRY_RUN:-0}"
force="${FORCE:-0}"

fail() {
    printf '%s\n' "Monitor Desktop virtual camera setup failed: $*" >&2
    exit 1
}

warn() {
    printf '%s\n' "Monitor Desktop virtual camera setup warning: $*" >&2
}

say() {
    printf '%s\n' "Monitor Desktop virtual camera setup: $*"
}

usage() {
    printf '%s\n' "usage: setup-v4l2loopback.sh [--dry-run] [--force]"
    printf '%s\n' "  --dry-run  print every action, including privileged commands; change nothing"
    printf '%s\n' "  --force    back up conflicting config to <file>.bak.<timestamp> and replace it"
    printf '%s\n' "env: CARD_LABEL VIDEO_NR DRY_RUN=1 FORCE=1 MODPROBE_DIR MODULES_LOAD_DIR"
}

for arg in "$@"; do
    case "$arg" in
        --dry-run) dry_run=1 ;;
        --force) force=1 ;;
        -h|--help) usage; exit 0 ;;
        -*) fail "unknown option: $arg (see --help)" ;;
        *) fail "unexpected argument: $arg (see --help)" ;;
    esac
done
case "$dry_run" in 1|true|yes) dry_run=1 ;; *) dry_run=0 ;; esac
case "$force" in 1|true|yes) force=1 ;; *) force=0 ;; esac

case "$video_nr" in
    ''|*[!0-9]*) fail "VIDEO_NR must be a non-negative integer, got: $video_nr" ;;
esac
[ -n "$card_label" ] || fail "CARD_LABEL must not be empty."

command -v uname >/dev/null 2>&1 || fail "uname is required."
command -v grep >/dev/null 2>&1 || fail "grep is required."
command -v cat >/dev/null 2>&1 || fail "cat is required."
command -v sed >/dev/null 2>&1 || fail "sed is required."
command -v sleep >/dev/null 2>&1 || fail "sleep is required."
command -v id >/dev/null 2>&1 || fail "id is required."

# The desktop user owns the 'video' membership, not the sudo-invoked root:
# prefer SUDO_USER, then USER, then id -un. When run as a normal user
# (sh scripts/setup-v4l2loopback.sh) SUDO_USER is simply unset.
target_user="${SUDO_USER:-}"
if [ -z "$target_user" ]; then
    target_user="${USER:-}"
fi
if [ -z "$target_user" ]; then
    target_user=$(id -un 2>/dev/null || true)
fi

os_name=$(uname -s)
if [ "$os_name" != "Linux" ]; then
    printf '%s\n' "Monitor Desktop virtual camera setup is Linux-only (v4l2loopback); on $os_name (macOS/Windows) nothing needs to be done." >&2
    exit 1
fi

if [ "$dry_run" -eq 0 ]; then
    command -v sudo >/dev/null 2>&1 || fail "sudo is required for this one-time setup."
fi

modprobe_file="$modprobe_dir/$conf_name"
modules_load_file="$modules_load_dir/$conf_name"
expected_modprobe="options v4l2loopback devices=1 video_nr=$video_nr card_label=\"$card_label\" exclusive_caps=1"
expected_modules="v4l2loopback"
device="$dev_dir/video$video_nr"
sys_name_file="$sys_class_dir/video$video_nr/name"

# sq single-quotes a string for safe embedding in a shell snippet.
sq() {
    printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

# priv runs a privileged shell snippet, or only prints it under --dry-run,
# so the normal and dry-run paths share the same call sites. sudo appears
# only in snippets passed to priv(); nothing else in this script may use it.
priv() {
    if [ "$dry_run" -eq 1 ]; then
        printf '%s\n' "(dry run) would run: $1"
    else
        sh -c "$1"
    fi
}

# find_label_device prints the $dev_dir node whose sysfs name currently
# equals $card_label, and returns non-zero when no such node exists.
find_label_device() {
    for scan_file in "$sys_class_dir"/video*/name; do
        [ -f "$scan_file" ] || continue
        if [ "$(cat "$scan_file" 2>/dev/null || true)" = "$card_label" ]; then
            scan_dir=${scan_file%/name}
            scan_dir=${scan_dir%/}
            printf '%s\n' "$dev_dir/${scan_dir##*/}"
            return 0
        fi
    done
    return 1
}

if [ "$dry_run" -eq 1 ]; then
    say "dry run: no changes will be made; privileged commands are only printed."
fi

say "checking for conflicting v4l2loopback configuration in $modprobe_dir..."
conflicts=""
if [ -d "$modprobe_dir" ]; then
    for candidate in "$modprobe_dir"/*.conf; do
        [ -e "$candidate" ] || continue
        grep -q "v4l2loopback" "$candidate" 2>/dev/null || continue
        if [ "$candidate" = "$modprobe_file" ] && [ -f "$modprobe_file" ] \
            && [ "$(cat "$modprobe_file")" = "$expected_modprobe" ]; then
            continue
        fi
        case " $conflicts " in
            *" $candidate "*) ;;
            *) conflicts="$conflicts $candidate" ;;
        esac
    done
fi
# Our own files with differing content also need --force (with backup),
# including the case where our modprobe file no longer mentions v4l2loopback.
if [ -f "$modprobe_file" ] && [ "$(cat "$modprobe_file")" != "$expected_modprobe" ]; then
    case " $conflicts " in
        *" $modprobe_file "*) ;;
        *) conflicts="$conflicts $modprobe_file" ;;
    esac
fi
if [ -f "$modules_load_file" ] && [ "$(cat "$modules_load_file")" != "$expected_modules" ]; then
    case " $conflicts " in
        *" $modules_load_file "*) ;;
        *) conflicts="$conflicts $modules_load_file" ;;
    esac
fi
if [ -n "$conflicts" ]; then
    if [ "$force" -eq 0 ]; then
        printf '%s\n' "existing v4l2loopback configuration conflicts with the Monitor Desktop setup:" >&2
        # shellcheck disable=SC2086
        for conflict in $conflicts; do
            printf '%s\n' "  conflicting file: $conflict" >&2
        done
        printf '%s\n' "re-run with --force to back up each file to <file>.bak.<timestamp> and replace it." >&2
        exit 1
    fi
    command -v date >/dev/null 2>&1 || fail "date is required to timestamp backups."
    stamp=$(date +%Y%m%d%H%M%S)
    # shellcheck disable=SC2086
    for conflict in $conflicts; do
        say "backing up $conflict to $conflict.bak.$stamp"
        priv "sudo cp -a $(sq "$conflict") $(sq "$conflict.bak.$stamp")"
    done
fi

if [ -e "$device" ]; then
    current_label=""
    if [ -f "$sys_name_file" ]; then
        current_label=$(cat "$sys_name_file")
    fi
    if [ "$current_label" = "$card_label" ]; then
        say "$device already provides '$card_label'; continuing."
    else
        fail "$device already exists and belongs to '${current_label:-an unknown device}'. Pick a free number (e.g. sudo VIDEO_NR=11 sh scripts/setup-v4l2loopback.sh) and re-run."
    fi
fi

driver_ok=0
if command -v modinfo >/dev/null 2>&1; then
    if modinfo v4l2loopback >/dev/null 2>&1; then
        driver_ok=1
    fi
fi
if [ "$driver_ok" -eq 1 ]; then
    say "v4l2loopback driver already available for this kernel; skipping package installation."
else
    if command -v apt-get >/dev/null 2>&1; then
        say "installing the v4l2loopback driver packages..."
        priv "sudo apt-get update"
        priv "sudo apt-get install -y v4l2loopback-dkms v4l2loopback-utils"
        if [ "$dry_run" -eq 0 ]; then
            if command -v modinfo >/dev/null 2>&1 && modinfo v4l2loopback >/dev/null 2>&1; then
                say "v4l2loopback driver installed."
            else
                fail "the driver is still unavailable after installation. Reboot (or check 'dkms status' for a failed build) and re-run."
            fi
        else
            say "dry run: skipping the post-install driver re-check."
        fi
    else
        printf '%s\n' "the v4l2loopback driver is not available and apt-get was not found." >&2
        printf '%s\n' "install your distro's v4l2loopback package (with kernel headers for DKMS) manually, then re-run this script." >&2
        exit 1
    fi
fi

write_config() {
    w_path=$1
    w_content=$2
    if [ -f "$w_path" ] && [ "$(cat "$w_path")" = "$w_content" ]; then
        say "$w_path already configured; leaving it unchanged."
    else
        if [ -f "$w_path" ] && [ "$force" -eq 0 ]; then
            fail "$w_path already exists with different content. Re-run with --force to back it up and replace it."
        fi
        priv "printf '%s\n' $(sq "$w_content") | sudo tee $(sq "$w_path") >/dev/null"
        priv "sudo chmod 644 $(sq "$w_path")"
        say "wrote $w_path."
    fi
}

write_config "$modprobe_file" "$expected_modprobe"
write_config "$modules_load_file" "$expected_modules"

groups_list=""
if [ -z "$target_user" ]; then
    warn "could not determine the desktop user; skipping 'video' group setup."
elif groups_list=$(id -nG "$target_user" 2>/dev/null); then
    case " $groups_list " in
        *" video "*)
            say "$target_user is already in the 'video' group."
            ;;
        *)
            priv "sudo usermod -aG video $(sq "$target_user")" \
                || warn "could not add $target_user to the 'video' group; continuing anyway."
            warn "group changes need a re-login to take effect (logind ACLs may already grant camera access)."
            ;;
    esac
else
    warn "could not query group membership for $target_user; skipping 'video' group setup."
fi

module_loaded=0
if command -v lsmod >/dev/null 2>&1; then
    if lsmod 2>/dev/null | grep -q "^v4l2loopback"; then
        module_loaded=1
    fi
elif [ -d /sys/module/v4l2loopback ]; then
    module_loaded=1
fi

if [ "$module_loaded" -eq 0 ]; then
    say "loading v4l2loopback..."
    priv "sudo modprobe v4l2loopback"
else
    # The charp array parameter does not reliably read back (it can be
    # empty, N/A, or quoted), so verify reality first: if a node labelled
    # with our card label already exists, the module is providing it.
    if already_providing=$(find_label_device); then
        say "v4l2loopback is already providing '$card_label' at $already_providing; continuing."
    else
        # Parameter reads are diagnostic only: empty or unparseable
        # values mean "unknown", never proof of a mismatch.
        loaded_label="unknown"
        loaded_nr="unknown"
        if [ -f /sys/module/v4l2loopback/parameters/card_label ]; then
            param_value=$(cat /sys/module/v4l2loopback/parameters/card_label 2>/dev/null || true)
            case "$param_value" in
                ''|N/A) ;;
                *) loaded_label="$param_value" ;;
            esac
        fi
        if [ -f /sys/module/v4l2loopback/parameters/video_nr ]; then
            param_value=$(cat /sys/module/v4l2loopback/parameters/video_nr 2>/dev/null || true)
            case "$param_value" in
                ''|*[!0-9]*) ;;
                *) loaded_nr="$param_value" ;;
            esac
        fi
        printf '%s\n' "v4l2loopback is already loaded but no device labelled '$card_label' is present (card_label='$loaded_label' video_nr='$loaded_nr')." >&2
        printf '%s\n' "not unloading it (it may be in use): reboot, or manually run 'sudo modprobe -r v4l2loopback' and re-run this script." >&2
        exit 2
    fi
fi

if [ "$dry_run" -eq 1 ]; then
    say "dry run: would wait up to ~5 s for $sys_name_file to contain '$card_label'."
else
    found=""
    poll=0
    while [ "$poll" -lt 6 ]; do
        if found=$(find_label_device); then
            break
        fi
        poll=$((poll + 1))
        if [ "$poll" -lt 6 ]; then
            sleep 1
        fi
    done
    if [ -n "$found" ]; then
        printf '%s\n' "Virtual camera ready: $found ($card_label)"
        printf '%s\n' "Select '$card_label' as the camera in Zoom, Meet, or Teams to use it."
    else
        fail "no video device labelled '$card_label' appeared within ~5 s. Reboot (the module loads at boot), or run 'sudo modprobe v4l2loopback' and check 'dmesg | tail' for errors."
    fi
fi
