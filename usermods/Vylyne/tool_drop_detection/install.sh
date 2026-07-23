#!/bin/bash
#
# Installs the tool_drop_detection usermod:
#   - accelerometer-based tool-drop / dock-tilt detection (tool_drop_detection.py)
#   - TC_DOCK_AUTOTUNE dock parking-position autotune (dock_autotune.py)
#
# Symlinks both extras into Klipper's klippy/extras/, and symlinks the
# example configs into your config directory so you can review/edit them
# in place. This script does NOT touch your printer.cfg -- you still need
# to add the [include ...] lines yourself and configure [tool_drop_detection]
# for your printer's accelerometers. See readme.md.

set -eu
export LC_ALL=C

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KLIPPER_PATH="${KLIPPER_PATH:-${HOME}/klipper}"
CONFIG_PATH="${CONFIG_PATH:-${HOME}/printer_data/config}"
USERMOD_CONFIG_DIR="${CONFIG_PATH}/tool_drop_detection"

function preflight_checks {
    if [ "$EUID" -eq 0 ]; then
        echo "[PRE-CHECK] This script must not be run as root!"
        exit 1
    fi
    if [ ! -d "${KLIPPER_PATH}/klippy/extras" ]; then
        echo "[ERROR] Klipper not found at ${KLIPPER_PATH}/klippy/extras"
        echo "        Set KLIPPER_PATH=/path/to/klipper to override."
        exit 1
    fi
}

function link_extras {
    echo "[INSTALL] Linking extras into ${KLIPPER_PATH}/klippy/extras/..."
    ln -sfn "${SCRIPT_DIR}/tool_drop_detection.py" "${KLIPPER_PATH}/klippy/extras/tool_drop_detection.py"
    ln -sfn "${SCRIPT_DIR}/dock_autotune.py" "${KLIPPER_PATH}/klippy/extras/dock_autotune.py"
}

function link_configs {
    echo "[INSTALL] Linking example configs into ${USERMOD_CONFIG_DIR}/..."
    mkdir -p "${USERMOD_CONFIG_DIR}"
    ln -sfn "${SCRIPT_DIR}/tool_drop_detection.cfg" "${USERMOD_CONFIG_DIR}/tool_drop_detection.cfg"
    ln -sfn "${SCRIPT_DIR}/dock_autotune.cfg" "${USERMOD_CONFIG_DIR}/dock_autotune.cfg"
}

function remind_include {
    local includes_found=1
    if [ -d "${CONFIG_PATH}" ] && grep -Rq "tool_drop_detection/tool_drop_detection.cfg" "${CONFIG_PATH}" 2>/dev/null; then
        includes_found=0
    fi
    if [ "${includes_found}" -ne 0 ]; then
        echo ""
        echo "[ACTION NEEDED] Add these to your printer.cfg, then fill in your"
        echo "accelerometer names / thresholds (see readme.md):"
        echo "    [include tool_drop_detection/tool_drop_detection.cfg]"
        echo "    [include tool_drop_detection/dock_autotune.cfg]"
    fi
    echo ""
    echo "[NEXT] Once printer.cfg is updated, restart Klipper (e.g. 'sudo systemctl restart klipper')."
}

printf "\n==========================================\n"
echo "- tool_drop_detection usermod installer -"
printf "==========================================\n\n"

preflight_checks
link_extras
link_configs
remind_include

echo ""
echo "[DONE] Extras and example configs linked."
