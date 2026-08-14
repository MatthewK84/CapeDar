#!/usr/bin/env bash
set -euo pipefail

readonly SERVICE_NAME="capedar.service"
readonly UNIT_TARGET="/etc/systemd/system/${SERVICE_NAME}"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
readonly UNIT_TEMPLATE="${SCRIPT_DIR}/capedar.service.in"

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this installer with sudo: sudo ./deploy/install-service.sh [service-user]" >&2
    exit 1
fi

SERVICE_USER="${1:-${SUDO_USER:-}}"
if [[ -z ${SERVICE_USER} || ${SERVICE_USER} == "root" ]]; then
    echo "Pass the non-root account that should run CapeDar." >&2
    echo "Example: sudo ./deploy/install-service.sh ubuntu" >&2
    exit 1
fi
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
    echo "No such service user: ${SERVICE_USER}" >&2
    exit 1
fi

readonly RADAR_CFG="${PROJECT_ROOT}/configs/aop_presence_10fps.cfg"
readonly DETECTION_CFG="${PROJECT_ROOT}/configs/detection_gates_pi.json"

CAPEDAR_BIN=""
for candidate in \
    "${PROJECT_ROOT}/.venv/bin/capedar" \
    "${PROJECT_ROOT}/venv/bin/capedar"; do
    if [[ -x ${candidate} ]]; then
        CAPEDAR_BIN="${candidate}"
        break
    fi
done
if [[ -z ${CAPEDAR_BIN} ]]; then
    echo "CapeDar is not installed in .venv or venv under ${PROJECT_ROOT}." >&2
    echo 'Create a venv and run: venv/bin/pip install -e ".[pi]"' >&2
    exit 1
fi
readonly CAPEDAR_BIN

for required_path in "${RADAR_CFG}" "${DETECTION_CFG}" "${UNIT_TEMPLATE}"; do
    if [[ ! -e ${required_path} ]]; then
        echo "Required file is missing: ${required_path}" >&2
    fi
done

supplementary_groups=("dialout")
if getent group gpio >/dev/null 2>&1; then
    supplementary_groups+=("gpio")
fi
readonly SUPPLEMENTARY_GROUPS="${supplementary_groups[*]}"
readonly USERMOD_GROUPS="$(IFS=,; echo "${supplementary_groups[*]}")"

usermod --append --groups "${USERMOD_GROUPS}" "${SERVICE_USER}"

escaped_project_root="${PROJECT_ROOT//\\/\\\\}"
escaped_project_root="${escaped_project_root//&/\\&}"
escaped_capedar_bin="${CAPEDAR_BIN//\\/\\\\}"
escaped_capedar_bin="${escaped_capedar_bin//&/\\&}"
escaped_service_user="${SERVICE_USER//&/\\&}"
escaped_groups="${SUPPLEMENTARY_GROUPS//&/\\&}"

temporary_unit="$(mktemp)"
trap 'rm -f -- "${temporary_unit}"' EXIT
sed \
    -e "s|@PROJECT_ROOT@|${escaped_project_root}|g" \
    -e "s|@CAPEDAR_BIN@|${escaped_capedar_bin}|g" \
    -e "s|@SERVICE_USER@|${escaped_service_user}|g" \
    -e "s|@SUPPLEMENTARY_GROUPS@|${escaped_groups}|g" \
    "${UNIT_TEMPLATE}" >"${temporary_unit}"

install -o root -g root -m 0644 "${temporary_unit}" "${UNIT_TARGET}"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo "Installed and started ${SERVICE_NAME} as ${SERVICE_USER}."
echo "Status:  systemctl status ${SERVICE_NAME}"
echo "Logs:    journalctl -u ${SERVICE_NAME} -f"
