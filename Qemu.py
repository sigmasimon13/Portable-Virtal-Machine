"""
QEMU VM Manager - a VirtualBox-style front end for a portable QEMU install.

Expects QEMU portable at: C:\\qemu-portable-20241220
    (qemu-system-x86_64.exe, qemu-img.exe should be inside that folder,
     possibly in a subfolder - see QEMU_DIR / find_exe() below)

Run with:  python qemu_manager.py
Needs:     pip install PySide6
"""

import sys
import os
import json
import uuid
import subprocess
import shutil
import shlex
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QAction, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QListWidget, QListWidgetItem, QAbstractItemView,
    QHBoxLayout, QVBoxLayout, QFormLayout, QLabel, QPushButton, QLineEdit,
    QSpinBox, QComboBox, QFileDialog, QToolBar, QSplitter, QMessageBox,
    QDialog, QDialogButtonBox, QGroupBox, QCheckBox, QStyle, QPlainTextEdit,
    QTabWidget
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _default_qemu_dir() -> Path:
    """
    Figure out where the portable QEMU folder should be.

    Priority:
      1. QEMU_DIR environment variable, if set.
      2. A "qemu-portable" folder bundled next to this exe/script
         (this is where --add-data lands when frozen with PyInstaller,
         or where you'd manually copy the folder in dev/onedir builds).
      3. Fallback hardcoded default (old behavior).
    """
    env_override = os.environ.get("QEMU_DIR")
    if env_override:
        return Path(env_override)

    if getattr(sys, "frozen", False):
        # Running as a PyInstaller exe.
        # onefile: bundled data extracted to sys._MEIPASS at runtime.
        # onedir: everything sits next to the exe itself.
        base_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        exe_dir = Path(sys.executable).parent
    else:
        base_dir = Path(__file__).resolve().parent
        exe_dir = base_dir

    for candidate_root in (base_dir, exe_dir):
        candidate = candidate_root / "qemu-portable"
        if candidate.exists():
            return candidate

    return Path(r"C:\qemu-portable-20241220")


QEMU_DIR = _default_qemu_dir()
CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "QemuManager"
CONFIG_FILE = CONFIG_DIR / "vms.json"
VM_DISK_DIR = CONFIG_DIR / "disks"

CONFIG_DIR.mkdir(parents=True, exist_ok=True)
VM_DISK_DIR.mkdir(parents=True, exist_ok=True)


def print_command(args, label=""):
    """Print a copy-pasteable version of a command to the console."""
    printable = " ".join(shlex.quote(a) for a in args)
    if label:
        print(f"\n[{label}]")
    print(printable)
    print()


def find_exe(name: str) -> str:
    """Look for name.exe directly under QEMU_DIR, or in any subfolder."""
    direct = QEMU_DIR / name
    if direct.exists():
        return str(direct)
    if QEMU_DIR.exists():
        for p in QEMU_DIR.rglob(name):
            return str(p)
    return name


QEMU_SYSTEM = find_exe("qemu-system-x86_64.exe")
QEMU_IMG = find_exe("qemu-img.exe")

try:
    _ver = subprocess.run([QEMU_SYSTEM, "--version"], capture_output=True, text=True, timeout=5)
    print(f"[QEMU Manager] Using: {QEMU_SYSTEM}")
    print(f"[QEMU Manager] {_ver.stdout.strip()}")
except Exception as _e:
    print(f"[QEMU Manager] Could not run --version on {QEMU_SYSTEM}: {_e}")

def _detect_usb_drive_letters():
    """
    Windows only. Returns {(vendor_id, product_id): (drive_letter, model)}
    for USB disks that currently have an assigned drive letter, by walking
    LogicalDisk -> Partition -> DiskDrive and pulling the VID/PID from the
    disk's parent USB device entry (PNPDeviceID on the storage node doesn't
    contain a VID/PID, so this looks up the matching parent USB\\VID_ node
    by matching the shared serial number).
    """
    ps_cmd = r"""
$results = @()
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=2" | ForEach-Object {
    $ld = $_
    $partition = Get-CimAssociatedInstance -InputObject $ld -ResultClassName Win32_DiskPartition -ErrorAction SilentlyContinue
    if ($partition) {
        $disk = Get-CimAssociatedInstance -InputObject $partition -ResultClassName Win32_DiskDrive -ErrorAction SilentlyContinue
        if ($disk) {
            $results += "$($ld.DeviceID)||$($disk.Model)||$($disk.PNPDeviceID)"
        }
    }
}
$results
"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=10,
        ).stdout
        # Pull all USB parent nodes once so we can match by serial number.
        usb_out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'USB\\VID_*' } "
             "| Select-Object -ExpandProperty InstanceId"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        usb_instance_ids = [l.strip() for l in usb_out.splitlines() if l.strip().startswith("USB\\VID_")]

        drive_map = {}
        for line in out.splitlines():
            line = line.strip()
            if "||" not in line:
                continue
            parts = line.split("||")
            if len(parts) != 3:
                continue
            device_id, model, pnp_id = parts
            letter = device_id.rstrip(":")
            # The storage PNPDeviceID ends in the drive's serial number after
            # the last backslash; USB parent nodes share that same serial.
            serial = pnp_id.rsplit("\\", 1)[-1].split("&")[0]
            if not serial:
                continue
            for instance_id in usb_instance_ids:
                if serial and serial in instance_id:
                    try:
                        vid_part, pid_part = instance_id.split("&PID_", 1)
                        vendor_id = vid_part.split("VID_", 1)[1][:4].lower()
                        product_id = pid_part[:4].lower()
                    except (IndexError, ValueError):
                        continue
                    drive_map[(vendor_id, product_id)] = (letter, model.strip())
                    break
        return drive_map
    except Exception as e:
        print(f"[QEMU Manager] Drive-letter detection failed: {e}")
        return {}


def detect_usb_devices():
    """
    Return a list of (vendor_id, product_id, description) tuples for
    currently connected USB devices, using whatever mechanism is available
    on the host OS. Best-effort — returns [] if detection fails.
    """
    devices = []
    try:
        if sys.platform.startswith("linux") or sys.platform == "darwin":
            out = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5).stdout
            # Example line: "Bus 001 Device 003: ID 046d:c52b Logitech, Inc. Unifying Receiver"
            for line in out.splitlines():
                parts = line.split("ID ", 1)
                if len(parts) != 2:
                    continue
                rest = parts[1].strip()
                ids, _, desc = rest.partition(" ")
                if ":" not in ids:
                    continue
                vendor_id, _, product_id = ids.partition(":")
                devices.append((vendor_id, product_id, desc.strip() or ids))
        else:
            # Windows: query PnP devices via PowerShell, get both the instance id
            # (for VID/PID) and the friendly device name.
            ps_cmd = (
                "Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'USB\\VID_*' } "
                "| Select-Object InstanceId, FriendlyName "
                "| ForEach-Object { \"$($_.InstanceId)||$($_.FriendlyName)\" }"
            )
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=10,
            ).stdout
            seen = set()
            for line in out.splitlines():
                line = line.strip()
                if not line.startswith("USB\\VID_") or "||" not in line:
                    continue
                instance_id, _, friendly_name = line.partition("||")
                try:
                    vid_part, pid_part = instance_id.split("&PID_", 1)
                    vendor_id = vid_part.split("VID_", 1)[1][:4]
                    product_id = pid_part[:4]
                except (IndexError, ValueError):
                    continue
                key = (vendor_id.lower(), product_id.lower())
                if key in seen:
                    continue
                seen.add(key)
                devices.append((vendor_id.lower(), product_id.lower(), friendly_name.strip() or instance_id))

            # Map USB storage devices to their drive letter + model, so
            # flash drives/external disks are identifiable at a glance
            # instead of showing up as generic "USB Mass Storage Device".
            drive_map = _detect_usb_drive_letters()
            if drive_map:
                labeled = []
                for vendor_id, product_id, name in devices:
                    match = drive_map.get((vendor_id.lower(), product_id.lower()))
                    if match:
                        letter, model = match
                        labeled.append((vendor_id, product_id, f"{letter}: — {model}"))
                    else:
                        labeled.append((vendor_id, product_id, name))
                devices = labeled
    except Exception as e:
        print(f"[QEMU Manager] USB detection failed: {e}")
    return devices


if sys.platform.startswith("linux"):
    ACCEL_OPTIONS = ["kvm", "tcg", "haxm"]
    _DEFAULT_CPU_MODEL = "host"
elif sys.platform == "darwin":
    ACCEL_OPTIONS = ["hvf", "tcg", "haxm"]
    _DEFAULT_CPU_MODEL = "host"
else:
    # Windows: WHPX has known interrupt-injection issues with q35 on some
    # builds, so default to the slower but reliable tcg backend, and to
    # the "max" CPU model since "host" requires a working accelerator.
    ACCEL_OPTIONS = ["tcg", "whpx", "haxm"]
    _DEFAULT_CPU_MODEL = "max"
OS_TYPES = ["Windows", "Linux", "Other"]
MACHINE_OPTIONS = ["q35", "pc"]
CPU_OPTIONS = [
    "qemu64",
    "max",
    "host",
    "kvm64",
    "kvm32",
    "qemu32",
    "486",
    "pentium",
    "pentium2",
    "pentium3",
    "athlon",
    "phenom",
    "core2duo",
    "coreduo",
    "n270",
    "Conroe",
    "Penryn",
    "Nehalem",
    "Nehalem-IBRS",
    "Westmere",
    "Westmere-IBRS",
    "SandyBridge",
    "SandyBridge-IBRS",
    "IvyBridge",
    "IvyBridge-IBRS",
    "Haswell",
    "Haswell-noTSX",
    "Haswell-IBRS",
    "Haswell-noTSX-IBRS",
    "Broadwell",
    "Broadwell-noTSX",
    "Broadwell-IBRS",
    "Broadwell-noTSX-IBRS",
    "Skylake-Client",
    "Skylake-Client-IBRS",
    "Skylake-Client-noTSX-IBRS",
    "Skylake-Server",
    "Skylake-Server-IBRS",
    "Skylake-Server-noTSX-IBRS",
    "Cascadelake-Server",
    "Cascadelake-Server-noTSX",
    "Cooperlake",
    "Icelake-Client",
    "Icelake-Client-noTSX",
    "Icelake-Server",
    "Icelake-Server-noTSX",
    "SapphireRapids",
    "Denverton",
    "Snowridge",
    "KnightsMill",
    "Dhyana",
    "EPYC",
    "EPYC-IBPB",
    "EPYC-Rome",
    "EPYC-Milan",
    "EPYC-Genoa",
    "Opteron_G1",
    "Opteron_G2",
    "Opteron_G3",
    "Opteron_G4",
    "Opteron_G5",
    "athlon64",
]
DISPLAY_OPTIONS = ["gtk", "sdl", "none"]
VGA_OPTIONS = ["std", "virtio-vga", "qxl", "vmware"]
DISK_FORMATS = ["qcow2", "raw"]
DISK_BUS_OPTIONS = ["virtio", "sata"]
NETWORK_MODES = ["user", "bridge", "none"]
NETWORK_MODE_LABELS = {
    "user": "NAT (user networking)",
    "bridge": "Bridged adapter",
    "none": "Disabled",
}
NETWORK_MODELS = ["virtio", "e1000", "rtl8139", "vmxnet3"]


# ---------------------------------------------------------------------------
# VM data model + storage
# ---------------------------------------------------------------------------

def load_vms() -> list:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            return []
    return []


def save_vms(vms: list):
    CONFIG_FILE.write_text(json.dumps(vms, indent=2))


def default_vm(name="New Virtual Machine") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "os_type": "Windows",
        "ram_mb": 6144,
        "cpus": 2,
        "disk_path": "",
        "disk_size_gb": 60,
        "iso_path": "",
        "accel": ACCEL_OPTIONS[0],
        "network": True,
        "network_mode": "user",
        "network_model": "virtio",
        "network_mac": "",
        "network_ipv6": True,
        "network_dns": "",
        "network_bridge": "",
        "network_hostfwd": "",
        "machine": "q35",
        "cpu_model": _DEFAULT_CPU_MODEL,
        "display": "gtk",
        "vga": "std",
        "disk_format": "qcow2",
        "disk_bus": "virtio",
        "usb_devices": [],  # list of "vendor_id:product_id" strings, e.g. "046d:c52b"
    }


# ---------------------------------------------------------------------------
# New VM wizard
# ---------------------------------------------------------------------------

class NewVMDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create New Virtual Machine")
        self.setMinimumWidth(420)

        self.vm = default_vm()

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit(self.vm["name"])
        form.addRow("Name:", self.name_edit)

        self.os_combo = QComboBox()
        self.os_combo.addItems(OS_TYPES)
        form.addRow("Type:", self.os_combo)

        self.ram_spin = QSpinBox()
        self.ram_spin.setRange(256, 131072)
        self.ram_spin.setSingleStep(256)
        self.ram_spin.setValue(self.vm["ram_mb"])
        self.ram_spin.setSuffix(" MB")
        form.addRow("Memory (RAM):", self.ram_spin)

        self.cpu_spin = QSpinBox()
        self.cpu_spin.setRange(1, 32)
        self.cpu_spin.setValue(self.vm["cpus"])
        form.addRow("Processors:", self.cpu_spin)

        self.disk_size_spin = QSpinBox()
        self.disk_size_spin.setRange(1, 4096)
        self.disk_size_spin.setValue(self.vm["disk_size_gb"])
        self.disk_size_spin.setSuffix(" GB")
        form.addRow("New Disk Size:", self.disk_size_spin)

        iso_row = QHBoxLayout()
        self.iso_edit = QLineEdit(self.vm["iso_path"])
        iso_browse = QPushButton("Browse...")
        iso_browse.clicked.connect(self._browse_iso)
        iso_row.addWidget(self.iso_edit)
        iso_row.addWidget(iso_browse)
        form.addRow("ISO image:", iso_row)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_iso(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select ISO image", "", "ISO images (*.iso);;All files (*)"
        )
        if path:
            self.iso_edit.setText(path)

    def get_vm(self) -> dict:
        self.vm["name"] = self.name_edit.text().strip() or "New Virtual Machine"
        self.vm["os_type"] = self.os_combo.currentText()
        self.vm["ram_mb"] = self.ram_spin.value()
        self.vm["cpus"] = self.cpu_spin.value()
        self.vm["disk_size_gb"] = self.disk_size_spin.value()
        self.vm["iso_path"] = self.iso_edit.text().strip()
        safe_name = "".join(c for c in self.vm["name"] if c.isalnum() or c in " _-").strip()
        ext = "img" if self.vm["disk_format"] == "raw" else "qcow2"
        self.vm["disk_path"] = str(VM_DISK_DIR / f"{safe_name or self.vm['id']}.{ext}")
        return self.vm


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QEMU Manager")
        self.resize(950, 600)

        self.vms = load_vms()
        for vm in self.vms:
            vm.setdefault("machine", "q35")
            vm.setdefault("cpu_model", "qemu64")
            vm.setdefault("display", "gtk")
            vm.setdefault("vga", "std")
            vm.setdefault("disk_format", "qcow2")
            vm.setdefault("disk_bus", "virtio")
            vm.setdefault("network_mode", "user" if vm.get("network", True) else "none")
            vm.setdefault("network_model", "virtio")
            vm.setdefault("network_mac", "")
            vm.setdefault("network_ipv6", True)
            vm.setdefault("network_dns", "")
            vm.setdefault("network_bridge", "")
            vm.setdefault("network_hostfwd", "")
        self.current_vm_id = None
        self.processes = {}

        self._build_ui()
        self._refresh_list()

        if not QEMU_DIR.exists():
            QMessageBox.warning(
                self, "QEMU not found",
                f"Could not find the QEMU portable folder at:\n{QEMU_DIR}\n\n"
                "Starting VMs will fail until this is fixed."
            )

    def _build_ui(self):
        toolbar = QToolBar("Main")
        toolbar.setIconSize(QSize(28, 28))
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        style = self.style()

        act_new = QAction(style.standardIcon(QStyle.SP_FileDialogNewFolder), "New", self)
        act_new.triggered.connect(self.new_vm)
        toolbar.addAction(act_new)

        act_settings = QAction(style.standardIcon(QStyle.SP_FileDialogDetailedView), "Settings", self)
        act_settings.triggered.connect(self.open_settings)
        toolbar.addAction(act_settings)

        act_start = QAction(style.standardIcon(QStyle.SP_MediaPlay), "Start", self)
        act_start.triggered.connect(self.start_vm)
        toolbar.addAction(act_start)

        act_duplicate = QAction(style.standardIcon(QStyle.SP_FileDialogListView), "Duplicate", self)
        act_duplicate.triggered.connect(self.duplicate_vm)
        toolbar.addAction(act_duplicate)

        act_delete = QAction(style.standardIcon(QStyle.SP_TrashIcon), "Remove", self)
        act_delete.triggered.connect(self.delete_vm)
        toolbar.addAction(act_delete)

        splitter = QSplitter(Qt.Horizontal)

        self.list_widget = QListWidget()
        self.list_widget.setMinimumWidth(230)
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        self.list_widget.itemDoubleClicked.connect(lambda _: self.start_vm())
        splitter.addWidget(self.list_widget)

        self.details_panel = QWidget()
        self.details_layout = QVBoxLayout(self.details_panel)
        self.details_layout.setAlignment(Qt.AlignTop)

        self.title_label = QLabel("No machine selected")
        f = QFont()
        f.setPointSize(16)
        f.setBold(True)
        self.title_label.setFont(f)
        self.details_layout.addWidget(self.title_label)
        self.subtitle_label = QLabel("Virtual machine overview")
        self.subtitle_label.setStyleSheet("color: #718096; font-size: 12px;")
        self.details_layout.addWidget(self.subtitle_label)

        self.info_group = QGroupBox("General")
        info_form = QFormLayout(self.info_group)
        self.info_os = QLabel("-")
        self.info_ram = QLabel("-")
        self.info_cpu = QLabel("-")
        self.info_disk = QLabel("-")
        self.info_iso = QLabel("-")
        self.info_iso.setWordWrap(True)
        self.info_network = QLabel("-")
        self.info_network.setWordWrap(True)
        info_form.addRow("Type:", self.info_os)
        info_form.addRow("RAM:", self.info_ram)
        info_form.addRow("Processors:", self.info_cpu)
        info_form.addRow("Disk:", self.info_disk)
        info_form.addRow("ISO:", self.info_iso)
        info_form.addRow("Network:", self.info_network)
        self.details_layout.addWidget(self.info_group)

        self.details_layout.addStretch()
        splitter.addWidget(self.details_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        self.setCentralWidget(splitter)

    def _refresh_list(self):
        self.list_widget.clear()
        for vm in self.vms:
            item = QListWidgetItem(vm["name"])
            item.setData(Qt.UserRole, vm["id"])
            item.setIcon(self.style().standardIcon(QStyle.SP_ComputerIcon))
            self.list_widget.addItem(item)
        if self.vms:
            self.list_widget.setCurrentRow(0)

    def _get_vm(self, vm_id):
        for vm in self.vms:
            if vm["id"] == vm_id:
                return vm
        return None

    def _current_vm(self):
        if self.current_vm_id is None:
            return None
        return self._get_vm(self.current_vm_id)

    def _on_selection_changed(self, current, _previous):
        if current is None:
            self.current_vm_id = None
            self.title_label.setText("No machine selected")
            self.subtitle_label.setText("Select a machine from the list")
            self.info_os.setText("-")
            self.info_ram.setText("-")
            self.info_cpu.setText("-")
            self.info_disk.setText("-")
            self.info_iso.setText("-")
            self.info_network.setText("-")
            return
        vm_id = current.data(Qt.UserRole)
        self.current_vm_id = vm_id
        vm = self._get_vm(vm_id)
        if not vm:
            return
        self.title_label.setText(vm["name"])
        self.subtitle_label.setText("Ready to start · Double-click a machine to launch it")
        self.info_os.setText(vm["os_type"])
        self.info_ram.setText(f'{vm["ram_mb"]} MB')
        self.info_cpu.setText(str(vm["cpus"]))
        self.info_disk.setText(f'{vm["disk_path"] or "(no disk)"}  ({vm["disk_size_gb"]} GB)')
        self.info_iso.setText(vm["iso_path"] or "(none)")
        network_mode = vm.get("network_mode", "user" if vm.get("network", True) else "none")
        network_text = NETWORK_MODE_LABELS.get(network_mode, network_mode)
        if network_mode != "none":
            network_text += f" · {vm.get('network_model', 'virtio')}"
            forwards = [
                line.strip() for line in vm.get("network_hostfwd", "").splitlines()
                if line.strip()
            ]
            if forwards:
                network_text += f" · {len(forwards)} port forward(s)"
        self.info_network.setText(network_text)

    def new_vm(self):
        dlg = NewVMDialog(self)
        if dlg.exec() == QDialog.Accepted:
            vm = dlg.get_vm()
            self.vms.append(vm)
            save_vms(self.vms)
            self._refresh_list()
            for i in range(self.list_widget.count()):
                if self.list_widget.item(i).data(Qt.UserRole) == vm["id"]:
                    self.list_widget.setCurrentRow(i)
                    break

    def open_settings(self):
        vm = self._current_vm()
        if not vm:
            QMessageBox.information(self, "No selection", "Select a virtual machine first.")
            return
        dlg = SettingsDialog(vm, self)
        if dlg.exec() == QDialog.Accepted:
            save_vms(self.vms)
            self._refresh_list()
            for i in range(self.list_widget.count()):
                if self.list_widget.item(i).data(Qt.UserRole) == vm["id"]:
                    self.list_widget.setCurrentRow(i)
                    break

    def delete_vm(self):
        vm = self._current_vm()
        if not vm:
            return
        resp = QMessageBox.question(
            self, "Remove machine",
            f'Remove "{vm["name"]}" from the list?'
        )
        if resp != QMessageBox.Yes:
            return

        disk_path = Path(vm["disk_path"]) if vm.get("disk_path") else None
        if disk_path and disk_path.exists():
            disk_resp = QMessageBox.question(
                self, "Delete disk file",
                f'Also delete the disk file for "{vm["name"]}"?\n\n{disk_path}\n\n'
                "This cannot be undone.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if disk_resp == QMessageBox.Yes:
                try:
                    disk_path.unlink()
                except Exception as e:
                    QMessageBox.warning(self, "Failed to delete disk", str(e))

        self.vms = [v for v in self.vms if v["id"] != vm["id"]]
        save_vms(self.vms)
        self._refresh_list()

    def duplicate_vm(self):
        vm = self._current_vm()
        if not vm:
            QMessageBox.information(self, "No selection", "Select a virtual machine first.")
            return

        base_name = f'{vm["name"]} - Copy'
        existing_names = {v["name"] for v in self.vms}
        new_name = base_name
        n = 2
        while new_name in existing_names:
            new_name = f"{base_name} ({n})"
            n += 1

        clone_disk = False
        src_disk = Path(vm["disk_path"]) if vm.get("disk_path") else None
        if src_disk and src_disk.exists():
            resp = QMessageBox.question(
                self, "Duplicate machine",
                f'Also copy the disk file for "{vm["name"]}"?\n\n'
                f"Yes = full clone with its own disk (uses extra space, may take a while)\n"
                f"No = duplicate settings only, new VM will get a fresh empty disk",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            clone_disk = resp == QMessageBox.Yes

        new_vm = json.loads(json.dumps(vm))
        new_vm["id"] = str(uuid.uuid4())
        new_vm["name"] = new_name

        safe_name = "".join(c for c in new_name if c.isalnum() or c in " _-").strip()
        ext = "img" if vm.get("disk_format", "qcow2") == "raw" else "qcow2"
        new_disk_path = VM_DISK_DIR / f"{safe_name or new_vm['id']}.{ext}"

        if clone_disk and src_disk and src_disk.exists():
            try:
                shutil.copy2(src_disk, new_disk_path)
                new_vm["disk_path"] = str(new_disk_path)
            except Exception as e:
                QMessageBox.critical(self, "Failed to copy disk", str(e))
                new_vm["disk_path"] = str(new_disk_path)
        else:
            new_vm["disk_path"] = str(new_disk_path)

        self.vms.append(new_vm)
        save_vms(self.vms)
        self._refresh_list()
        for i in range(self.list_widget.count()):
            if self.list_widget.item(i).data(Qt.UserRole) == new_vm["id"]:
                self.list_widget.setCurrentRow(i)
                break

    def start_vm(self):
        vm = self._current_vm()
        if not vm:
            QMessageBox.information(self, "No selection", "Select a virtual machine first.")
            return

        if not Path(QEMU_SYSTEM).exists():
            QMessageBox.critical(
                self, "QEMU not found",
                f"Could not find qemu-system-x86_64.exe under:\n{QEMU_DIR}"
            )
            return

        disk_format = vm.get("disk_format", "qcow2")
        disk_path = Path(vm["disk_path"])
        if vm["disk_path"] and not disk_path.exists():
            try:
                img_args = [QEMU_IMG, "create", "-f", disk_format, str(disk_path), f'{vm["disk_size_gb"]}G']
                print_command(img_args, label="qemu-img create")
                subprocess.run(
                    img_args,
                    check=True, cwd=str(QEMU_DIR) if QEMU_DIR.exists() else None,
                )
            except Exception as e:
                QMessageBox.critical(self, "Failed to create disk", str(e))
                return

        ram_mb = vm["ram_mb"]
        ram_str = f"{ram_mb // 1024}G" if ram_mb % 1024 == 0 else str(ram_mb)

        args = [
            QEMU_SYSTEM,
            "-machine", vm.get("machine", "q35"),
            "-cpu", vm.get("cpu_model", "qemu64"),
            "-smp", str(vm["cpus"]),
            "-m", ram_str,
        ]

        if vm.get("iso_path"):
            args += ["-boot", "d", "-cdrom", vm["iso_path"]]

        disk_bus = vm.get("disk_bus", "virtio")
        if disk_bus == "sata":
            args += [
                "-drive", f'file={vm["disk_path"]},format={disk_format},if=none,id=drive0',
                "-device", "ahci,id=ahci",
                "-device", "ide-hd,drive=drive0,bus=ahci.0",
            ]
        else:
            args += ["-drive", f'file={vm["disk_path"]},format={disk_format},if=virtio']
            
        vga = vm.get("vga", "std")
        if vga == "std":
            args += ["-vga", "std"]
        else:
            args += ["-device", vga]

        display = vm.get("display", "gtk")
        if display != "none":
            args += ["-display", display]
        else:
            args += ["-display", "none"]

        accel = vm.get("accel", ACCEL_OPTIONS[0])
        if accel.startswith("whpx"):
            args += ["-accel", "whpx,kernel-irqchip=off"]
        elif accel.startswith("haxm"):
            args += ["-accel", "hax"]
        elif accel.startswith("kvm"):
            args += ["-accel", "kvm"]
        elif accel.startswith("hvf"):
            args += ["-accel", "hvf"]

        network_mode = vm.get("network_mode", "user" if vm.get("network", True) else "none")
        if network_mode == "none":
            args += ["-nic", "none"]
        else:
            nic_options = [network_mode, f'model={vm.get("network_model", "virtio")}']
            mac = vm.get("network_mac", "").strip()
            if mac:
                nic_options.append(f"mac={mac}")
            if network_mode == "user":
                nic_options.append(f'ipv6={"on" if vm.get("network_ipv6", True) else "off"}')
                dns = vm.get("network_dns", "").strip()
                if dns:
                    nic_options.append(f"dns={dns}")
                for forwarding in vm.get("network_hostfwd", "").splitlines():
                    forwarding = forwarding.strip()
                    if forwarding:
                        nic_options.append(f"hostfwd={forwarding}")
            elif network_mode == "bridge":
                bridge = vm.get("network_bridge", "").strip()
                if not bridge:
                    QMessageBox.critical(
                        self, "Missing bridge name",
                        "Bridged networking requires a bridge name or interface."
                    )
                    return
                nic_options.append(f"br={bridge}")
            args += ["-nic", ",".join(nic_options)]

        usb_devices = vm.get("usb_devices", [])
        if usb_devices:
            args += ["-usb", "-device", "qemu-xhci,id=xhci"]
            for dev_id in usb_devices:
                try:
                    vendor_id, product_id = dev_id.split(":")
                    int(vendor_id, 16)
                    int(product_id, 16)
                except ValueError:
                    print(f"[QEMU Manager] Skipping invalid USB device id: {dev_id!r}")
                    continue
                args += ["-device", f"usb-host,vendorid=0x{vendor_id},productid=0x{product_id}"]

        try:
            print_command(args, label=f'qemu-system-x86_64 ({vm["name"]})')
            proc = subprocess.Popen(args, cwd=str(QEMU_DIR) if QEMU_DIR.exists() else None)
            self.processes[vm["id"]] = proc
        except Exception as e:
            QMessageBox.critical(self, "Failed to start VM", str(e))


# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):
    def __init__(self, vm: dict, parent=None):
        super().__init__(parent)
        self.vm = vm
        self.setWindowTitle(f'Settings - {vm["name"]}')
        self.setMinimumSize(560, 420)
        self.resize(620, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        general_page = QWidget()
        general_layout = QVBoxLayout(general_page)
        system_page = QWidget()
        system_layout = QVBoxLayout(system_page)
        storage_page = QWidget()
        storage_layout = QVBoxLayout(storage_page)
        network_page = QWidget()
        network_layout = QVBoxLayout(network_page)
        usb_page = QWidget()
        usb_layout = QVBoxLayout(usb_page)

        general_box = QGroupBox("General")
        general_form = QFormLayout(general_box)
        self.name_edit = QLineEdit(vm["name"])
        general_form.addRow("Name:", self.name_edit)
        self.os_combo = QComboBox()
        self.os_combo.addItems(OS_TYPES)
        self.os_combo.setCurrentText(vm["os_type"])
        general_form.addRow("Type:", self.os_combo)
        general_layout.addWidget(general_box)

        system_box = QGroupBox("System")
        system_form = QFormLayout(system_box)
        self.ram_spin = QSpinBox()
        self.ram_spin.setRange(256, 131072)
        self.ram_spin.setSingleStep(256)
        self.ram_spin.setValue(vm["ram_mb"])
        self.ram_spin.setSuffix(" MB")
        system_form.addRow("Memory (RAM):", self.ram_spin)

        self.cpu_spin = QSpinBox()
        self.cpu_spin.setRange(1, 32)
        self.cpu_spin.setValue(vm["cpus"])
        system_form.addRow("Processors:", self.cpu_spin)

        self.accel_combo = QComboBox()
        self.accel_combo.addItems(ACCEL_OPTIONS)
        self.accel_combo.setCurrentText(vm.get("accel", ACCEL_OPTIONS[0]))
        system_form.addRow("Acceleration:", self.accel_combo)

        self.machine_combo = QComboBox()
        self.machine_combo.addItems(MACHINE_OPTIONS)
        self.machine_combo.setCurrentText(vm.get("machine", "q35"))
        system_form.addRow("Machine type:", self.machine_combo)

        self.cpu_model_combo = QComboBox()
        self.cpu_model_combo.addItems(CPU_OPTIONS)
        self.cpu_model_combo.setCurrentText(vm.get("cpu_model", "qemu64"))
        system_form.addRow("CPU model:", self.cpu_model_combo)

        self.display_combo = QComboBox()
        self.display_combo.addItems(DISPLAY_OPTIONS)
        self.display_combo.setCurrentText(vm.get("display", "gtk"))
        system_form.addRow("Display:", self.display_combo)

        self.vga_combo = QComboBox()
        self.vga_combo.addItems(VGA_OPTIONS)
        self.vga_combo.setCurrentText(vm.get("vga", "std"))
        system_form.addRow("Video device:", self.vga_combo)
        system_layout.addWidget(system_box)

        storage_box = QGroupBox("Storage")
        storage_form = QFormLayout(storage_box)

        disk_row = QHBoxLayout()
        self.disk_edit = QLineEdit(vm["disk_path"])
        disk_browse = QPushButton("Browse...")
        disk_browse.clicked.connect(self._browse_disk)
        disk_row.addWidget(self.disk_edit)
        disk_row.addWidget(disk_browse)
        storage_form.addRow("Disk file:", disk_row)

        self.disk_size_spin = QSpinBox()
        self.disk_size_spin.setRange(1, 4096)
        self.disk_size_spin.setValue(vm["disk_size_gb"])
        self.disk_size_spin.setSuffix(" GB")
        storage_form.addRow("Disk size (new disk):", self.disk_size_spin)

        self.disk_format_combo = QComboBox()
        self.disk_format_combo.addItems(DISK_FORMATS)
        self.disk_format_combo.setCurrentText(vm.get("disk_format", "qcow2"))
        storage_form.addRow("Disk format (new disk):", self.disk_format_combo)

        self.disk_bus_combo = QComboBox()
        self.disk_bus_combo.addItems(DISK_BUS_OPTIONS)
        self.disk_bus_combo.setCurrentText(vm.get("disk_bus", "virtio"))
        storage_form.addRow("Disk bus:", self.disk_bus_combo)

        iso_row = QHBoxLayout()
        self.iso_edit = QLineEdit(vm["iso_path"])
        iso_browse = QPushButton("Browse...")
        iso_browse.clicked.connect(self._browse_iso)
        iso_clear = QPushButton("Clear")
        iso_clear.clicked.connect(lambda: self.iso_edit.setText(""))
        iso_row.addWidget(self.iso_edit)
        iso_row.addWidget(iso_browse)
        iso_row.addWidget(iso_clear)
        storage_form.addRow("ISO image:", iso_row)

        storage_layout.addWidget(storage_box)

        network_box = QGroupBox("Network")
        network_form = QFormLayout(network_box)
        self.network_mode_combo = QComboBox()
        for mode in NETWORK_MODES:
            self.network_mode_combo.addItem(NETWORK_MODE_LABELS[mode], mode)
        saved_mode = vm.get("network_mode", "user" if vm.get("network", True) else "none")
        self.network_mode_combo.setCurrentIndex(max(0, self.network_mode_combo.findData(saved_mode)))
        self.network_mode_combo.currentIndexChanged.connect(self._update_network_fields)
        network_form.addRow("Connection:", self.network_mode_combo)

        self.network_model_combo = QComboBox()
        self.network_model_combo.addItems(NETWORK_MODELS)
        self.network_model_combo.setCurrentText(vm.get("network_model", "virtio"))
        network_form.addRow("Adapter model:", self.network_model_combo)

        self.network_mac_edit = QLineEdit(vm.get("network_mac", ""))
        self.network_mac_edit.setPlaceholderText("Auto-generated (or e.g. 52:54:00:12:34:56)")
        network_form.addRow("MAC address:", self.network_mac_edit)

        self.network_ipv6_check = QCheckBox("Enable IPv6")
        self.network_ipv6_check.setChecked(vm.get("network_ipv6", True))
        network_form.addRow("", self.network_ipv6_check)

        self.network_dns_edit = QLineEdit(vm.get("network_dns", ""))
        self.network_dns_edit.setPlaceholderText("Optional DNS server, e.g. 1.1.1.1")
        network_form.addRow("DNS server:", self.network_dns_edit)

        self.network_bridge_edit = QLineEdit(vm.get("network_bridge", ""))
        self.network_bridge_edit.setPlaceholderText("Windows bridge name or interface")
        network_form.addRow("Bridge name:", self.network_bridge_edit)

        self.network_hostfwd_edit = QPlainTextEdit(vm.get("network_hostfwd", ""))
        self.network_hostfwd_edit.setPlaceholderText(
            "One rule per line, for example:\n"
            "tcp::2222-:22\n"
            "tcp::8080-:80"
        )
        self.network_hostfwd_edit.setFixedHeight(72)
        network_form.addRow("Port forwarding:", self.network_hostfwd_edit)
        network_hint = QLabel(
            "NAT is the easiest option. Bridged mode may require a configured QEMU bridge adapter."
        )
        network_hint.setWordWrap(True)
        network_hint.setStyleSheet("color: #718096; font-size: 11px;")
        network_form.addRow("", network_hint)
        self._update_network_fields()
        network_layout.addWidget(network_box)

        usb_box = QGroupBox("USB Passthrough")
        usb_box_layout = QVBoxLayout(usb_box)
        usb_hint = QLabel("Pick a device and click Add. Add as many as you need.")
        usb_hint.setStyleSheet("color: gray; font-size: 11px;")
        usb_box_layout.addWidget(usb_hint)

        usb_pick_row = QHBoxLayout()
        self.usb_combo = QComboBox()
        usb_refresh_btn = QPushButton("Refresh")
        usb_refresh_btn.clicked.connect(self._refresh_usb_devices)
        usb_add_btn = QPushButton("Add")
        usb_add_btn.clicked.connect(self._add_usb_device)
        usb_pick_row.addWidget(self.usb_combo, stretch=1)
        usb_pick_row.addWidget(usb_refresh_btn)
        usb_pick_row.addWidget(usb_add_btn)
        usb_box_layout.addLayout(usb_pick_row)

        self.usb_selected_list = QListWidget()
        self.usb_selected_list.setMaximumHeight(100)
        usb_box_layout.addWidget(self.usb_selected_list)

        usb_remove_btn = QPushButton("Remove selected")
        usb_remove_btn.setObjectName("removeUsbButton")
        usb_remove_btn.clicked.connect(self._remove_usb_device)
        usb_box_layout.addWidget(usb_remove_btn)

        self._usb_available = []  # list of (vendor_id, product_id, name)
        for dev_key in vm.get("usb_devices", []):
            item = QListWidgetItem(dev_key)
            item.setData(Qt.UserRole, dev_key)
            self.usb_selected_list.addItem(item)
        self._refresh_usb_devices()
        self._relabel_saved_usb_items()

        usb_layout.addWidget(usb_box)

        general_layout.addStretch()
        system_layout.addStretch()
        storage_layout.addStretch()
        network_layout.addStretch()
        usb_layout.addStretch()
        tabs.addTab(general_page, "General")
        tabs.addTab(system_page, "System")
        tabs.addTab(storage_page, "Storage")
        tabs.addTab(network_page, "Network")
        tabs.addTab(usb_page, "USB")
        layout.addWidget(tabs, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_disk(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Select disk file", self.disk_edit.text() or str(VM_DISK_DIR),
            "QCOW2 Disk (*.qcow2);;All files (*)"
        )
        if path:
            self.disk_edit.setText(path)

    def _browse_iso(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select ISO image", "", "ISO images (*.iso);;All files (*)"
        )
        if path:
            self.iso_edit.setText(path)

    def _on_accept(self):
        self.vm["name"] = self.name_edit.text().strip() or self.vm["name"]
        self.vm["os_type"] = self.os_combo.currentText()
        self.vm["ram_mb"] = self.ram_spin.value()
        self.vm["cpus"] = self.cpu_spin.value()
        self.vm["accel"] = self.accel_combo.currentText()
        self.vm["machine"] = self.machine_combo.currentText()
        self.vm["cpu_model"] = self.cpu_model_combo.currentText()
        self.vm["display"] = self.display_combo.currentText()
        self.vm["vga"] = self.vga_combo.currentText()
        self.vm["disk_path"] = self.disk_edit.text().strip()
        self.vm["disk_size_gb"] = self.disk_size_spin.value()
        self.vm["disk_format"] = self.disk_format_combo.currentText()
        self.vm["disk_bus"] = self.disk_bus_combo.currentText()
        self.vm["iso_path"] = self.iso_edit.text().strip()
        network_mode = self.network_mode_combo.currentData()
        self.vm["network_mode"] = network_mode
        self.vm["network"] = network_mode != "none"
        self.vm["network_model"] = self.network_model_combo.currentText()
        self.vm["network_mac"] = self.network_mac_edit.text().strip()
        self.vm["network_ipv6"] = self.network_ipv6_check.isChecked()
        self.vm["network_dns"] = self.network_dns_edit.text().strip()
        self.vm["network_bridge"] = self.network_bridge_edit.text().strip()
        self.vm["network_hostfwd"] = self.network_hostfwd_edit.toPlainText().strip()
        self.vm["usb_devices"] = [
            self.usb_selected_list.item(i).data(Qt.UserRole)
            for i in range(self.usb_selected_list.count())
        ]
        self.accept()

    def _update_network_fields(self):
        mode = self.network_mode_combo.currentData()
        is_user = mode == "user"
        is_bridge = mode == "bridge"
        self.network_model_combo.setEnabled(mode != "none")
        self.network_mac_edit.setEnabled(mode != "none")
        self.network_ipv6_check.setEnabled(is_user)
        self.network_dns_edit.setEnabled(is_user)
        self.network_hostfwd_edit.setEnabled(is_user)
        self.network_bridge_edit.setEnabled(is_bridge)

    def _refresh_usb_devices(self):
        self.usb_combo.clear()
        self._usb_available = detect_usb_devices()
        if not self._usb_available:
            self.usb_combo.addItem("No USB devices detected", None)
            return
        for vendor_id, product_id, name in self._usb_available:
            dev_key = f"{vendor_id}:{product_id}"
            self.usb_combo.addItem(f"{name}  ({dev_key})", dev_key)

    def _add_usb_device(self):
        dev_key = self.usb_combo.currentData()
        if not dev_key:
            return
        existing = {
            self.usb_selected_list.item(i).data(Qt.UserRole)
            for i in range(self.usb_selected_list.count())
        }
        if dev_key in existing:
            return
        label = self.usb_combo.currentText()
        item = QListWidgetItem(label)
        item.setData(Qt.UserRole, dev_key)
        self.usb_selected_list.addItem(item)

    def _remove_usb_device(self):
        for item in self.usb_selected_list.selectedItems():
            self.usb_selected_list.takeItem(self.usb_selected_list.row(item))

    def _relabel_saved_usb_items(self):
        name_by_key = {
            f"{vendor_id}:{product_id}": name
            for vendor_id, product_id, name in self._usb_available
        }
        for i in range(self.usb_selected_list.count()):
            item = self.usb_selected_list.item(i)
            dev_key = item.data(Qt.UserRole)
            if dev_key in name_by_key:
                item.setText(f"{name_by_key[dev_key]}  ({dev_key})")


# ---------------------------------------------------------------------------
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QMainWindow, QDialog {
            background: #f4f7fb;
        }
        QMainWindow {
            color: #243b53;
        }
        QWidget {
            color: #243b53;
        }
        QToolBar {
            background: #ffffff;
            border: 0;
            border-bottom: 1px solid #d9e2ec;
            spacing: 6px;
            padding: 6px;
        }
        QToolButton {
            color: #243b53;
            padding: 6px 10px;
            border-radius: 5px;
        }
        QToolButton:hover {
            background: #e6f0ff;
        }
        QListWidget, QGroupBox, QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
            background: #ffffff;
            color: #243b53;
            border: 1px solid #cbd5e1;
            border-radius: 5px;
        }
        QComboBox QAbstractItemView {
            background: #ffffff;
            color: #243b53;
            selection-background-color: #2f80ed;
            selection-color: #ffffff;
            border: 1px solid #cbd5e1;
        }
        QComboBox::drop-down {
            width: 26px;
            border: 0;
            border-left: 1px solid #d9e2ec;
        }
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled,
        QPlainTextEdit:disabled {
            background: #edf2f7;
            color: #718096;
        }
        QListWidget {
            padding: 4px;
            outline: 0;
        }
        QListWidget::item {
            padding: 8px;
            border-radius: 4px;
        }
        QListWidget::item:selected {
            background: #2f80ed;
            color: #ffffff;
        }
        QGroupBox {
            margin-top: 10px;
            padding: 12px 8px 8px 8px;
            font-weight: 600;
        }
        QTabWidget::pane {
            border: 1px solid #d9e2ec;
            border-radius: 6px;
            background: #ffffff;
            top: -1px;
        }
        QTabBar::tab {
            background: #eaf0f6;
            color: #486581;
            padding: 8px 14px;
            margin-right: 2px;
            border: 1px solid transparent;
        }
        QTabBar::tab:selected {
            background: #ffffff;
            color: #1769c2;
            border-color: #d9e2ec;
            border-bottom-color: #ffffff;
        }
        QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
            padding: 5px 7px;
            min-height: 26px;
        }
        QCheckBox {
            color: #243b53;
            spacing: 7px;
        }
        QTabWidget {
            color: #243b53;
        }
        QTabWidget QWidget {
            background: #ffffff;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
            color: #243b53;
        }
        QPushButton {
            background: #2f80ed;
            color: white;
            border: 0;
            border-radius: 5px;
            padding: 7px 13px;
        }
        QPushButton:hover {
            background: #1769c2;
        }
        QPushButton#removeUsbButton {
            background: #edf2f7;
            color: #243b53;
            border: 1px solid #bcccdc;
        }
        QPushButton#removeUsbButton:hover {
            background: #ffe3e3;
            color: #9b2c2c;
            border-color: #fc8181;
        }
        QPushButton#removeUsbButton:pressed {
            background: #feb2b2;
        }
        QStatusBar {
            background: #eaf0f6;
            color: #486581;
        }
    """)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
