from __future__ import annotations

from pathlib import Path

ACCOUNT_NAME = "_opensweorchard"
ACCOUNT_UID = 450
ACCOUNT_GID = 450

INSTALL_ROOT = Path("/opt/mobilyze/open-swe-orchard")
RELEASES_ROOT = INSTALL_ROOT / "releases"
ORCHARD_VERSION = "0.55.0"
ORCHARD_SHA256 = "35a6ca9f1770a6da9ccc508cee46df21f962d4c5fb437161e4fe8daa34bb14fe"
ORCHARD_RELEASE = RELEASES_ROOT / "orchard" / ORCHARD_VERSION
ORCHARD_BINARY = ORCHARD_RELEASE / "orchard"
TART_VERSION = "2.32.1"
TART_RELEASE_NAME = "2.32.1-r1"
TART_SHA256 = "05b65d5c14e8b41e8e44b6d9fd1278de4bedbc8b735d9b99f3c748f76f75862d"
TART_RELEASE = RELEASES_ROOT / "tart" / TART_RELEASE_NAME
TART_APP = TART_RELEASE / "tart.app"
TART_APP_EXECUTABLE_RELATIVE = Path("Contents/MacOS/tart")
TART_EXECUTABLE_RELATIVE = Path("tart.app") / TART_APP_EXECUTABLE_RELATIVE
TART_BINARY = TART_RELEASE / TART_EXECUTABLE_RELATIVE

CURRENT_ORCHARD = INSTALL_ROOT / "current-orchard"
CURRENT_TART = INSTALL_ROOT / "current-tart"
PREVIOUS_ORCHARD = INSTALL_ROOT / "previous-orchard"
PREVIOUS_TART = INSTALL_ROOT / "previous-tart"
CURRENT_BIN = INSTALL_ROOT / "current"
CURRENT_ORCHARD_BINARY = CURRENT_BIN / "orchard"
CURRENT_TART_BINARY = CURRENT_BIN / "tart"

DATA_ROOT = Path("/var/db/mobilyze-open-swe-orchard")
CONTROLLER_DATA = DATA_ROOT / "controller"
SECRETS_ROOT = DATA_ROOT / "secrets"
ADMIN_TOKEN = SECRETS_ROOT / "bootstrap-admin.token"
WORKER_TOKEN = SECRETS_ROOT / "worker-bootstrap.token"
LOG_ROOT = Path("/var/log/mobilyze-open-swe-orchard")
CONTROLLER_LOG = LOG_ROOT / "controller.log"
WORKER_LOG = LOG_ROOT / "worker-launchd.log"

CONTROLLER_LABEL = "com.mobilyze.open-swe.orchard.controller"
WORKER_LABEL = "com.mobilyze.open-swe.orchard.worker"
SERVICE_LABELS = (CONTROLLER_LABEL, WORKER_LABEL)
CONTROLLER_WRAPPER = Path("/usr/local/libexec/open-swe-orchard-controller")
WORKER_WRAPPER = Path("/usr/local/libexec/open-swe-orchard-worker")
CONTROLLER_PLIST = Path(f"/Library/LaunchDaemons/{CONTROLLER_LABEL}.plist")
WORKER_PLIST = Path(f"/Library/LaunchDaemons/{WORKER_LABEL}.plist")

LISTENER_HOST = "100.107.128.12"
LISTENER_PORT = 6120
CONTROLLER_URL = f"https://{LISTENER_HOST}:{LISTENER_PORT}"
WORKER_NAME = "studio2-open-swe"
WORKER_LABEL_VALUE = "oswe.pool=apple"
WORKER_SLOT_RESOURCE = "oswe.vm-slots=2"
VM_SLOTS = 2
DEFAULT_CPU = 4
DEFAULT_MEMORY_MIB = 8192
DEFAULT_DISK_GIB = 40
