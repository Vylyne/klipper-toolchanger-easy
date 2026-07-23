#!/bin/bash
#
# Uninstalls the tool_drop_detection usermod: removes the extras symlinks
# from Klipper, and removes the installed example configs IF they are still
# byte-identical to the shipped template (a customized config -- tuned
# accelerometer names, thresholds, park positions -- is left in place).
# Does NOT edit your printer config -- remove any
#   [include tool_drop_detection/...]
# lines yourself, then restart Klipper.

set -eu
export LC_ALL=C

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KLIPPER_PATH="${KLIPPER_PATH:-${HOME}/klipper}"
CONFIG_PATH="${CONFIG_PATH:-${HOME}/printer_data/config}"
USERMOD_CONFIG_DIR="${CONFIG_PATH}/toolchanger/tool_drop_detection"

function preflight_checks {
    if [ "$EUID" -eq 0 ]; then
        echo "[PRE-CHECK] This script must not be run as root!"
        exit 1
    fi
}

# Only remove a path if it is still a symlink pointing at this usermod
# folder -- never touch a real file or a link installed from elsewhere.
function unlink_if_matches {
    local link="$1" expect_target="$2"
    if [ -L "${link}" ]; then
        if [ "$(readlink "${link}")" = "${expect_target}" ]; then
            rm "${link}"
            echo "[REMOVE] ${link}"
        else
            echo "[SKIP] ${link} points elsewhere, leaving it alone."
        fi
    elif [ -e "${link}" ]; then
        echo "[SKIP] ${link} is not a symlink (not installed by this script), leaving it alone."
    fi
}

function unlink_extras {
    echo "[UNINSTALL] Removing extras symlinks..."
    unlink_if_matches "${KLIPPER_PATH}/klippy/extras/tool_drop_detection.py" "${SCRIPT_DIR}/tool_drop_detection.py"
    unlink_if_matches "${KLIPPER_PATH}/klippy/extras/dock_autotune.py" "${SCRIPT_DIR}/dock_autotune.py"
}

# Only delete an installed config copy if it's untouched -- if you've
# customized it we leave it in place rather than risk deleting something
# you'd want back.
function remove_config {
    local src="$1" dst="$2"
    if [ ! -e "${dst}" ]; then
        return
    fi
    if diff -q --strip-trailing-cr "${src}" "${dst}" >/dev/null 2>&1; then
        rm "${dst}"
        echo "[REMOVE] ${dst}"
    else
        echo "[SKIP] ${dst} has been customized, leaving it in place."
    fi
}

function remove_configs {
    echo "[UNINSTALL] Removing unmodified example configs..."
    remove_config "${SCRIPT_DIR}/tool_drop_detection.cfg" "${USERMOD_CONFIG_DIR}/tool_drop_detection.cfg"
    remove_config "${SCRIPT_DIR}/dock_autotune.cfg" "${USERMOD_CONFIG_DIR}/dock_autotune.cfg"
    rmdir "${USERMOD_CONFIG_DIR}" 2>/dev/null || true
}

printf "\n============================================\n"
echo "- tool_drop_detection usermod uninstaller -"
printf "============================================\n\n"

preflight_checks
unlink_extras
remove_configs

echo ""
echo "[ACTION NEEDED] If a customized config is still present, or you added"
echo "[include tool_drop_detection/...] lines yourself (or via install.sh's"
echo "toolchanger-config.cfg offer), remove those, then restart Klipper."
