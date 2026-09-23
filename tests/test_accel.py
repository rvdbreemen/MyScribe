"""scribe.accel: what this machine can accelerate with.

The hardware probe moved here from scribe/doctor.py with TASK-092, so the
runtime can ask whether there is an NVIDIA card without importing the doctor.
Its tests moved with it from tests/test_doctor.py, unchanged but for the
module they name.
"""

import sys
import types

import pytest

from scribe import accel


# --- the hardware probe: any doubt counts as hardware present ------------------------


def _fake_winreg(monkeypatch, subkeys=(), fail=False):
    """A winreg for the PCI enumeration, so the rule can be tested off Windows."""

    class Key:
        def __init__(self, names):
            self.names = names

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def open_key(root, path):
        if fail:
            raise OSError(5, "Access is denied")
        return Key(list(subkeys))

    module = types.ModuleType("winreg")
    module.HKEY_LOCAL_MACHINE = object()
    module.OpenKey = open_key
    module.QueryInfoKey = lambda key: (len(key.names), 0, 0)
    module.EnumKey = lambda key, index: key.names[index]
    monkeypatch.setitem(sys.modules, "winreg", module)
    return module


def _no_nvidia_smi(monkeypatch):
    monkeypatch.setattr(accel.shutil, "which", lambda name: None)
    monkeypatch.setattr(accel, "_NVIDIA_SMI_LOCATIONS", ())


def test_the_driver_tool_off_the_path_still_counts_as_a_card(monkeypatch, tmp_path):
    """The second half of the nvidia-smi signal: a machine whose PATH does not
    carry the driver's tool, but whose disk does. It is only ever looked for,
    never run - a broken driver's nvidia-smi exits non-zero with the card still
    in the slot. Without this test the whole `_NVIDIA_SMI_LOCATIONS` arm can be
    deleted and every other probe test stays green, and that arm is the only
    signal left on a machine whose card the OS has stopped enumerating."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(accel.shutil, "which", lambda name: None)
    _fake_winreg(monkeypatch, subkeys=["VEN_8086&DEV_A0E0"])

    installed = tmp_path / "nvidia-smi.exe"
    installed.write_text("", encoding="utf-8")
    monkeypatch.setattr(accel, "_NVIDIA_SMI_LOCATIONS", (installed,))

    assert accel.nvidia_hardware_present() is True

    # A location that is merely listed proves nothing; the registry decides.
    monkeypatch.setattr(accel, "_NVIDIA_SMI_LOCATIONS", (tmp_path / "not-installed.exe",))

    assert accel.nvidia_hardware_present() is False


def test_a_registry_that_will_not_answer_counts_as_hardware_present(monkeypatch):
    """Any doubt is hardware present, in the code and not in a comment. An
    access-denied read must never be told apart from "enumerated, none found"."""
    monkeypatch.setattr(sys, "platform", "win32")
    _no_nvidia_smi(monkeypatch)
    _fake_winreg(monkeypatch, fail=True)

    assert accel.nvidia_hardware_present() is True


def test_the_registry_finds_a_card_by_its_pci_vendor_id(monkeypatch):
    """Vendor 10DE, which is the ground truth and needs no driver: a card whose
    driver was never installed is still enumerated under Enum\\PCI."""
    monkeypatch.setattr(sys, "platform", "win32")
    _no_nvidia_smi(monkeypatch)
    _fake_winreg(monkeypatch, subkeys=["VEN_8086&DEV_A0E0", "VEN_10DE&DEV_2216&SUBSYS_38821462"])

    assert accel.nvidia_hardware_present() is True


def test_a_registry_with_no_nvidia_vendor_key_is_a_machine_without_a_card(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    _no_nvidia_smi(monkeypatch)
    _fake_winreg(monkeypatch, subkeys=["VEN_8086&DEV_A0E0", "VEN_1022&DEV_1450"])

    assert accel.nvidia_hardware_present() is False


def test_nvidia_smi_on_path_is_enough_on_any_os(monkeypatch):
    """The driver's own tool being installed is evidence of a card, whatever
    the registry or sysfs would say next."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        accel.shutil, "which", lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None
    )

    assert accel.nvidia_hardware_present() is True


def test_a_sysfs_that_is_not_there_counts_as_hardware_present(monkeypatch, tmp_path):
    """A container without /sys, a kernel that lists nothing: learned nothing
    is not "no card"."""
    monkeypatch.setattr(sys, "platform", "linux")
    _no_nvidia_smi(monkeypatch)
    monkeypatch.setattr(accel, "_SYSFS_PCI_DEVICES", tmp_path / "absent")

    assert accel.nvidia_hardware_present() is True

    empty = tmp_path / "devices"
    empty.mkdir()
    monkeypatch.setattr(accel, "_SYSFS_PCI_DEVICES", empty)

    assert accel.nvidia_hardware_present() is True


def test_sysfs_reads_the_pci_vendor_of_every_device(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    _no_nvidia_smi(monkeypatch)
    # Named without the colons a real sysfs uses: Windows cannot create those,
    # and what is under test is the vendor file, not the directory's spelling.
    devices = tmp_path / "devices"
    (devices / "0000.00.02.0").mkdir(parents=True)
    (devices / "0000.00.02.0" / "vendor").write_text("0x8086\n", encoding="utf-8")
    monkeypatch.setattr(accel, "_SYSFS_PCI_DEVICES", devices)

    assert accel.nvidia_hardware_present() is False

    (devices / "0000.01.00.0").mkdir()
    (devices / "0000.01.00.0" / "vendor").write_text("0x10de\n", encoding="utf-8")

    assert accel.nvidia_hardware_present() is True


def test_an_operating_system_this_probe_does_not_know_counts_as_hardware_present(monkeypatch):
    monkeypatch.setattr(sys, "platform", "sunos5")
    _no_nvidia_smi(monkeypatch)

    assert accel.nvidia_hardware_present() is True
