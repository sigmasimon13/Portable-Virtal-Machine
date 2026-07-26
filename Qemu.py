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
    QApplication, QMainWindow, QWidget, QListWidget, QListWidgetItem,
    QHBoxLayout, QVBoxLayout, QFormLayout, QLabel, QPushButton, QLineEdit,
    QSpinBox, QComboBox, QFileDialog, QToolBar, QSplitter, QMessageBox,
    QDialog, QDialogButtonBox, QGroupBox, QCheckBox, QStyle
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

QEMU_DIR = Path(r"C:\qemu-portable-20241220")
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

ACCEL_OPTIONS = ["whpx", "haxm", "tcg (no acceleration)"]
OS_TYPES = ["Windows", "Linux", "Other"]
MACHINE_OPTIONS = ["q35", "pc"]
CPU_OPTIONS = ["qemu64", "max"]
DISPLAY_OPTIONS = ["gtk", "sdl", "none"]
VGA_OPTIONS = ["std", "virtio-vga", "qxl", "vmware"]
DISK_FORMATS = ["qcow2", "raw"]
DISK_BUS_OPTIONS = ["virtio", "sata"]


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
        "ram_mb": 2048,
        "cpus": 1,
        "disk_path": "",
        "disk_size_gb": 40,
        "iso_path": "",
        "accel": ACCEL_OPTIONS[0],
        "network": True,
        "machine": "q35",
        "cpu_model": "qemu64",
        "display": "gtk",
        "vga": "std",
        "disk_format": "qcow2",
        "disk_bus": "virtio",
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

        self.info_group = QGroupBox("General")
        info_form = QFormLayout(self.info_group)
        self.info_os = QLabel("-")
        self.info_ram = QLabel("-")
        self.info_cpu = QLabel("-")
        self.info_disk = QLabel("-")
        self.info_iso = QLabel("-")
        self.info_iso.setWordWrap(True)
        info_form.addRow("Type:", self.info_os)
        info_form.addRow("RAM:", self.info_ram)
        info_form.addRow("Processors:", self.info_cpu)
        info_form.addRow("Disk:", self.info_disk)
        info_form.addRow("ISO:", self.info_iso)
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
            self.info_os.setText("-")
            self.info_ram.setText("-")
            self.info_cpu.setText("-")
            self.info_disk.setText("-")
            self.info_iso.setText("-")
            return
        vm_id = current.data(Qt.UserRole)
        self.current_vm_id = vm_id
        vm = self._get_vm(vm_id)
        if not vm:
            return
        self.title_label.setText(vm["name"])
        self.info_os.setText(vm["os_type"])
        self.info_ram.setText(f'{vm["ram_mb"]} MB')
        self.info_cpu.setText(str(vm["cpus"]))
        self.info_disk.setText(f'{vm["disk_path"] or "(no disk)"}  ({vm["disk_size_gb"]} GB)')
        self.info_iso.setText(vm["iso_path"] or "(none)")

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

        if vm.get("network", True):
            args += ["-nic", "user,model=virtio"]

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
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        general_box = QGroupBox("General")
        general_form = QFormLayout(general_box)
        self.name_edit = QLineEdit(vm["name"])
        general_form.addRow("Name:", self.name_edit)
        self.os_combo = QComboBox()
        self.os_combo.addItems(OS_TYPES)
        self.os_combo.setCurrentText(vm["os_type"])
        general_form.addRow("Type:", self.os_combo)
        layout.addWidget(general_box)

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
        layout.addWidget(system_box)

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

        layout.addWidget(storage_box)

        network_box = QGroupBox("Network")
        network_form = QFormLayout(network_box)
        self.network_check = QCheckBox("Enable NAT network adapter")
        self.network_check.setChecked(vm.get("network", True))
        network_form.addRow(self.network_check)
        layout.addWidget(network_box)

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
        self.vm["network"] = self.network_check.isChecked()
        self.accept()


# ---------------------------------------------------------------------------
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()