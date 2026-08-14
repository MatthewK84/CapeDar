#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly VENV_DIR="${SCRIPT_DIR}/.venv"
readonly SERVICE_INSTALLER="${SCRIPT_DIR}/deploy/install-service.sh"

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this installer with sudo: sudo ./setup-install.sh [service-user]" >&2
    exit 1
fi

SERVICE_USER="${1:-${SUDO_USER:-}}"
if [[ -z ${SERVICE_USER} || ${SERVICE_USER} == "root" ]]; then
    echo "Pass the non-root account that should run CapeDar." >&2
    echo "Example: sudo ./setup-install.sh ubuntu" >&2
    exit 1
fi
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
    echo "No such service user: ${SERVICE_USER}" >&2
    exit 1
fi
if [[ ! -x ${SERVICE_INSTALLER} ]]; then
    echo "Service installer is missing or not executable: ${SERVICE_INSTALLER}" >&2
    exit 1
fi

echo "[1/4] Installing Python prerequisites..."
apt-get update
apt-get install -y python3 python3-venv python3-pip

echo "[2/4] Creating the virtual environment if needed..."
if [[ ! -x ${VENV_DIR}/bin/python ]]; then
    sudo -u "${SERVICE_USER}" python3 -m venv "${VENV_DIR}"
fi

echo "[3/4] Installing CapeDar and Raspberry Pi dependencies..."
sudo -u "${SERVICE_USER}" "${VENV_DIR}/bin/python" -m pip install --upgrade pip
sudo -u "${SERVICE_USER}" "${VENV_DIR}/bin/pip" install -e "${SCRIPT_DIR}[pi]"

echo "[4/4] Installing and starting the systemd service..."
"${SERVICE_INSTALLER}" "${SERVICE_USER}"
