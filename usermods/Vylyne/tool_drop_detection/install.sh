#!/bin/bash
#
# Installs the tool_drop_detection usermod:
#   - accelerometer-based tool-drop / dock-tilt detection (tool_drop_detection.py)
#   - TC_DOCK_AUTOTUNE dock parking-position autotune (dock_autotune.py)
#
# Symlinks both extras into Klipper's klippy/extras/. The example configs
# are COPIED (not symlinked) into your config directory: Mainsail/Fluidd's
# config editor can't save through a symlink, and these configs are meant
# to be tuned per-printer anyway. An existing copy is never overwritten --
# if it differs from the template you get a diff instead. See readme.md.

set -eu
export LC_ALL=C

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KLIPPER_PATH="${KLIPPER_PATH:-${HOME}/klipper}"
CONFIG_PATH="${CONFIG_PATH:-${HOME}/printer_data/config}"
USERMOD_CONFIG_DIR="${CONFIG_PATH}/toolchanger/tool_drop_detection"
TOOLCHANGER_CONFIG="${CONFIG_PATH}/toolchanger/toolchanger-config.cfg"

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


# Copy src -> dst unless dst already exists. An existing copy is left
# untouched even if it differs from the template -- it's your tuned
# accelerometer/threshold values, not something to overwrite -- but the
# diff is shown so you can see what changed if the template was updated.
function install_config {
    local src="$1" dst="$2"
    if [ -e "${dst}" ]; then
        if diff -q --strip-trailing-cr "${src}" "${dst}" >/dev/null 2>&1; then
            echo "[SKIP] ${dst} already up to date."
        else
            echo "[SKIP] ${dst} already exists and differs from the template -- not overwriting your changes."
            echo "       diff (template vs. yours):"
            diff -u --strip-trailing-cr "${src}" "${dst}" | sed 's/^/       /'
        fi
    else
        cp "${src}" "${dst}"
        echo "[INSTALL] Copied ${dst}"
    fi
}

function install_configs {
    echo "[INSTALL] Installing example configs into ${USERMOD_CONFIG_DIR}/..."
    mkdir -p "${USERMOD_CONFIG_DIR}"
    install_config "${SCRIPT_DIR}/tool_drop_detection.cfg" "${USERMOD_CONFIG_DIR}/tool_drop_detection.cfg"
    install_config "${SCRIPT_DIR}/dock_autotune.cfg" "${USERMOD_CONFIG_DIR}/dock_autotune.cfg"
}

# Offer to wire the includes into toolchanger-config.cfg (commented out --
# you still have to review the configs and opt in) if that file exists and
# doesn't already reference tool_drop_detection. Idempotent: safe to re-run.
function offer_includes {
    if [ ! -f "${TOOLCHANGER_CONFIG}" ]; then
        echo ""
        echo "[ACTION NEEDED] ${TOOLCHANGER_CONFIG} not found -- add these to your"
        echo "printer config yourself, then restart Klipper:"
        echo "    [include tool_drop_detection/tool_drop_detection.cfg]"
        echo "    [include tool_drop_detection/dock_autotune.cfg]"
        return
    fi
    if grep -q "tool_drop_detection/tool_drop_detection.cfg" "${TOOLCHANGER_CONFIG}"; then
        echo "[SKIP] ${TOOLCHANGER_CONFIG} already references tool_drop_detection."
        return
    fi
    {
        echo "#[include tool_drop_detection/tool_drop_detection.cfg]"
        echo "#[include tool_drop_detection/dock_autotune.cfg]"
        cat "${TOOLCHANGER_CONFIG}"
    } > "${TOOLCHANGER_CONFIG}.new"
    mv "${TOOLCHANGER_CONFIG}.new" "${TOOLCHANGER_CONFIG}"
    echo "[INSTALL] Added commented includes to the top of ${TOOLCHANGER_CONFIG}"
    echo "          Review tool_drop_detection.cfg / dock_autotune.cfg, uncomment"
    echo "          them, then restart Klipper."
}

printf "\n==========================================\n"
echo "- tool_drop_detection usermod installer -"
printf "==========================================\n\n"

preflight_checks
link_extras
install_configs
offer_includes

echo ""
echo "[DONE] Extras linked, example configs installed."
