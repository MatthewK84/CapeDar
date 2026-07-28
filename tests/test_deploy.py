"""Deployment artifacts for the Raspberry Pi systemd service."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT = (ROOT / "deploy" / "capedar.service.in").read_text(encoding="utf-8")
INSTALLER = (ROOT / "deploy" / "install-service.sh").read_text(encoding="utf-8")


def test_service_uses_requested_detection_settings() -> None:
    assert "--cli-port /dev/ttyUSB0" in UNIT
    assert "--data-port /dev/ttyUSB1" in UNIT
    assert "--radar-cfg @PROJECT_ROOT@/configs/aop_presence_10fps.cfg" in UNIT
    assert "--detection-cfg @PROJECT_ROOT@/configs/detection_gates_pi.json" in UNIT
    assert "--configure always" in UNIT
    assert "--gpio on" in UNIT
    assert "--gpio-active-low" not in UNIT


def test_service_restarts_failures_and_stops_cleanly() -> None:
    assert "Restart=on-failure" in UNIT
    assert "RestartSec=5s" in UNIT
    assert "KillSignal=SIGTERM" in UNIT
    assert "WantedBy=multi-user.target" in UNIT


def test_service_does_not_run_as_root() -> None:
    assert "User=@SERVICE_USER@" in UNIT
    assert "NoNewPrivileges=yes" in UNIT


def test_installer_restarts_an_existing_service_after_updating_it() -> None:
    assert 'systemctl enable "${SERVICE_NAME}"' in INSTALLER
    assert 'systemctl restart "${SERVICE_NAME}"' in INSTALLER
