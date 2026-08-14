#!/usr/bin/env bash

set -euo pipefail

# ============================================================
# CapeDar Raspberry Pi systemd installer
# ============================================================

SERVICE_NAME="capedar"
SERVICE_DESCRIPTION="CapeDar TI mmWave Radar Service"

APP_USER="${SUDO_USER:-$USER}"
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"

# ----- CHANGE THESE IF YOUR LAYOUT DIFFERS ------------------

PROJECT_DIR="${APP_HOME}/capedar"
VENV_DIR="${PROJECT_DIR}/.venv"

CLI_PORT="/dev/ttyUSB0"
DATA_PORT="/dev/ttyUSB1"

RADAR_CFG="${PROJECT_DIR}/configs/airborne.cfg"
DETECTION_CFG="${PROJECT_DIR}/configs/detection_gates_pi.json"

# Installed console command inside the venv
EXECUTABLE="${VENV_DIR}/bin/aop-presence"

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# ============================================================

if [[ $EUID -ne 0 ]]; then
    echo "Run this installer with sudo:"
    echo
    echo "    sudo $0"
    echo
    exit 1
fi

echo "CapeDar system service installation"
echo "==================================="
echo "User:        ${APP_USER}"
echo "Project:     ${PROJECT_DIR}"
echo "Virtualenv:  ${VENV_DIR}"
echo

# ------------------------------------------------------------
# 1. Install basic Python/system dependencies
# ------------------------------------------------------------

echo "[1/7] Installing required system packages..."

apt-get update

apt-get install -y \
    python3 \
    python3-venv \
    python3-pip

# ------------------------------------------------------------
# 2. Verify project
# ------------------------------------------------------------

echo "[2/7] Verifying CapeDar project..."

if [[ ! -d "$PROJECT_DIR" ]]; then
    echo "ERROR: CapeDar project not found:"
    echo "    $PROJECT_DIR"
    exit 1
fi

# ------------------------------------------------------------
# 3. Create virtual environment if missing
# ------------------------------------------------------------

echo "[3/7] Checking Python virtual environment..."

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then

    echo "Creating virtual environment..."

    sudo -u "$APP_USER" \
        python3 -m venv "$VENV_DIR"

fi

# ------------------------------------------------------------
# 4. Install / update CapeDar
# ------------------------------------------------------------

echo "[4/7] Installing CapeDar into virtual environment..."

sudo -u "$APP_USER" \
    "${VENV_DIR}/bin/python" -m pip install --upgrade pip

# Install project.
#
# This assumes pyproject.toml / setup.py exists and
# aop-presence is defined as a console script.
#
sudo -u "$APP_USER" \
    "${VENV_DIR}/bin/pip" install -e "$PROJECT_DIR"

# Verify executable exists

if [[ ! -x "$EXECUTABLE" ]]; then
    echo "ERROR: CapeDar executable was not found:"
    echo "    $EXECUTABLE"
    echo
    echo "Check the package installation / console-script name."
    exit 1
fi

# ------------------------------------------------------------
# 5. Give application user serial access
# ------------------------------------------------------------

echo "[5/7] Configuring serial-port permissions..."

usermod -aG dialout "$APP_USER"

# ------------------------------------------------------------
# 6. Create systemd service
# ------------------------------------------------------------

echo "[6/7] Creating systemd service..."

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=${SERVICE_DESCRIPTION}
After=multi-user.target
StartLimitIntervalSec=0

[Service]
Type=simple

User=${APP_USER}
Group=${APP_USER}
SupplementaryGroups=dialout

WorkingDirectory=${PROJECT_DIR}

ExecStart=${EXECUTABLE} \\
    --cli-port ${CLI_PORT} \\
    --data-port ${DATA_PORT} \\
    --radar-cfg ${RADAR_CFG} \\
    --detection-cfg ${DETECTION_CFG}

Environment=PYTHONUNBUFFERED=1

Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

chmod 644 "$SERVICE_FILE"

# ------------------------------------------------------------
# 7. Enable service
# ------------------------------------------------------------

echo "[7/7] Enabling CapeDar service..."

systemctl daemon-reload
systemctl enable "$SERVICE_NAME.service"

echo
echo "CapeDar installation complete."
echo
echo "Service:"
echo "    ${SERVICE_NAME}.service"
echo
echo "Start now:"
echo "    sudo systemctl start ${SERVICE_NAME}"
echo
echo "Status:"
echo "    systemctl status ${SERVICE_NAME}"
echo
echo "Live logs:"
echo "    journalctl -u ${SERVICE_NAME} -f"
echo
echo "The Pi should be rebooted once so the '${APP_USER}' user"
echo "receives its new dialout group membership."