# Portable Virtual Machine

A VirtualBox-style graphical front end for a **portable QEMU install** on Windows.
Instead of hand-typing `qemu-system-x86_64` command lines, manage your VMs from
a simple GUI: create machines, tweak their settings, and start them with one click.

## Features

- VM list + detail panel (VirtualBox-style layout)
- New VM wizard — name, OS type, RAM, CPU count, disk size, ISO
- Per-VM settings dialog:
  - Machine type (`q35` / `pc`), CPU model, RAM, CPU count
  - Display backend (`gtk` / `sdl` / `none`) and video device (`std` / `virtio-vga` / `qxl` / `vmware`)
  - Disk format (`qcow2` / `raw`) and bus (`virtio` / `sata`)
  - ISO mounting
  - NAT networking toggle
  - Acceleration mode (`whpx` / `haxm` / `tcg`)
- Start, Duplicate (with optional full disk clone), and Remove VMs from the toolbar
- VM configs are saved as JSON in `%APPDATA%\QemuManager\vms.json`
- Prints the exact `qemu-system-x86_64` / `qemu-img` command it runs, so you can
  copy-paste it elsewhere or debug boot issues

## Requirements

- Windows
- Python 3.9+
- A portable QEMU build from https://github.com/ganarcasas/qemu-portable
- Python dependencies — see `requirements.txt`

## Setup

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Extract portable QEMU somewhere, e.g. `C:\qemu-portable-20241220`.
3. Tell the app where QEMU lives, using **one** of:
   - Environment variable: `set QEMU_DIR=C:\qemu-portable-20241220`
   - Edit the `QEMU_DIR` default near the top of `Qemu.py`
4. Run it:
   ```
   python Qemu.py
   ```

The app looks for `qemu-system-x86_64.exe` and `qemu-img.exe` directly inside
`QEMU_DIR`, or in any subfolder underneath it.

## Notes

- Disk images and app config are stored under `%APPDATA%\QemuManager\`.
- This is open source — feel free to change paths, defaults, or add options
  (e.g. more accelerators, snapshot support, additional disks).
