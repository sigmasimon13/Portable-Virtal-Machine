# Portable Virtual Machine Manager (QemuManager)

A VirtualBox-style graphical front end for a portable QEMU install on Windows, Linux, and macOS. Instead of hand-typing complex `qemu-system-x86_64` command lines, you can manage your virtual machines from a clean, modern Qt-based GUI: create machines, tweak granular hardware settings, handle USB passthrough, and launch them with a single click. `Qemu.py` can be placed anywhere—whether on the desktop or in the Documents folder.

---

## Features

* **VirtualBox-Style Layout**: VM list sidebar paired with a comprehensive overview and detail panel.


* **New VM Wizard**: Quickly set up a machine name, OS type, RAM, processor count, initial disk size, and ISO image.


* **Granular Per-VM Settings (Multi-Tab Dialog)**:


* **General**: Name and OS type.


* **System**: RAM size, CPU count, machine type (`q35` / `pc`), extensive CPU models (`host`, `max`, `qemu64`, etc.), acceleration backend (`whpx`, `haxm`, `kvm`, `hvf`, `tcg`), display backend (`gtk`, `sdl`, `none`), and video device (`std`, `virtio-vga`, `qxl`, `vmware`).


* **Storage**: Disk file path configuration, disk size, format (`qcow2`, `raw`), disk bus (`virtio`, `sata`), and ISO mounting/clearing.


* **Network**: Connection modes (`user` / NAT, `bridge`, `none`), adapter models (`virtio`, `e1000`, `rtl8139`, `vmxnet3`), custom MAC address, IPv6 toggle, DNS server configuration, bridge name, and multi-line port forwarding (`hostfwd`) rules.


* **USB Passthrough**: Automatic detection of host USB devices (including Windows drive-letter mapping for USB flash drives/external disks) and easy add/remove passthrough management.




* **Toolbar Actions**: Start, Duplicate (with an option for a full disk clone or fresh disk), and Remove VMs.


* **Persistent Storage**: VM configurations are automatically saved as JSON in `%APPDATA%\QemuManager\vms.json`, and virtual disks are stored under `%APPDATA%\QemuManager\disks\`.


* **Command Debugging**: Prints the exact copy-pasteable `qemu-system-x86_64` and `qemu-img` commands to the console for easy debugging or external execution.



---

## Requirements

* **Python**: Version 3.9 or higher.


* **Python Dependencies**: PySide6 (`pip install PySide6`).


* **Portable QEMU**: A portable QEMU build (such as [ganarcasas/qemu-portable](https://github.com/ganarcasas/qemu-portable)).



---

## Setup & Installation

1. **Install Dependencies**:
```bash
pip install PySide6

```


2. **Extract Portable QEMU**:
Extract your portable QEMU build somewhere on your system (e.g., `C:\qemu-portable-20241220` or right next to the script in a folder named `qemu-portable`).


3. **Configure QEMU Directory**:
The application looks for `qemu-system-x86_64.exe` and `qemu-img.exe` automatically using the following priority:


* **Environment Variable**: Set `QEMU_DIR` to your custom path:
```cmd
set QEMU_DIR=C:\path\to\qemu-portable

```


* **Local Folder**: A folder named `qemu-portable` placed in the same directory as the script or frozen executable.


* **Fallback Path**: Defaults to `C:\qemu-portable-20241220`.




4. **Run the Application**:
```bash
python Qemu.py

```



---

## Notes & Open Source

* Disk images and configuration files reside in `%APPDATA%\QemuManager\`.


* This project is open source—feel free to adjust default parameters, modify execution paths, or extend accelerator support as needed.
