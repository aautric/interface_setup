"""
Control of the Nikon Eclipse Ti(-E) microscope.

The microscope is driven through Micro-Manager (NikonTI device adapter, which uses the
Nikon Ti SDK installed in C:/Program Files/Nikon) and its Python core pymmcore:
    1. Micro-Manager 2.0 installed in MM_PATH (device interface 70)
    2. pip install pymmcore==10.1.1.70.6   (the version must match the Micro-Manager one)

Close NIS-Elements / Micro-Manager / TiControl before connecting.
"""

import os

import pymmcore

MM_PATH = r"C:\Program Files\Micro-Manager-2.0"

# Micro-Manager names of the Ti devices
HUB = "TIScope"
NOSEPIECE = "TINosePiece"
LIGHT_PATH = "TILightPath"
Z_DRIVE = "TIZDrive"
XY_DRIVE = "TIXYDrive"
PFS = "TIPFSStatus"
PFS_OFFSET = "TIPFSOffset"


class NikonTi:
    """
    Nikon Eclipse Ti microscope: objectives, light path, focus, XY stage, Perfect Focus.

    Usage:
        with NikonTi() as ti:
            ti.set_objective("60x")
            ti.move_z(1.5)
    """

    def __init__(self, mm_path=MM_PATH):
        self.mm_path = mm_path
        self.core = None    # pymmcore.CMMCore
        self.devices = []   # Ti devices found (e.g. TINosePiece, TIZDrive...)

    def connect(self):
        """Load the Ti hub and all the devices it reports as installed."""
        if self.mm_path not in os.environ["PATH"]:
            os.environ["PATH"] = self.mm_path + os.pathsep + os.environ["PATH"]  # adapter DLLs
        core = pymmcore.CMMCore()
        core.setDeviceAdapterSearchPaths([self.mm_path])
        try:
            core.loadDevice(HUB, "NikonTI", HUB)
            core.initializeDevice(HUB)
            devices = list(core.getInstalledDevices(HUB))
            for name in devices:
                core.loadDevice(name, "NikonTI", name)
                core.initializeDevice(name)
        except Exception as error:
            core.unloadAllDevices()
            raise RuntimeError(f"Could not connect to the Nikon Ti ({error}). Check that the "
                               f"microscope is on and that no other software uses it.")
        self.core = core
        self.devices = devices
        print(f"Connected to Nikon {core.getProperty(HUB, 'FirmwareVersion')}, "
              f"devices: {', '.join(devices)}.")

    def is_connected(self):
        """Return True if the microscope is connected."""
        return self.core is not None

    def has(self, device):
        """Return True if the device (e.g. XY_DRIVE) is installed on this microscope."""
        return device in self.devices

    # ---------- objectives and light path ----------

    @property
    def objectives(self):
        """Labels of the nosepiece positions, e.g. '2-Plan Fluor 60x NA 1.25 Oil'."""
        return list(self.core.getStateLabels(NOSEPIECE))

    def get_objective(self):
        """Label of the objective in place."""
        return self.core.getStateLabel(NOSEPIECE)

    def set_objective(self, objective):
        """
        objective: nosepiece position (1 to 6, as written on the microscope), full label,
        or a part of the label found in only one of them (e.g. "60x", "TIRF").
        """
        self._set_state(NOSEPIECE, objective)

    @property
    def light_paths(self):
        """Labels of the light path positions, e.g. '1-Eye100', '2-Left100'."""
        return list(self.core.getStateLabels(LIGHT_PATH))

    def get_light_path(self):
        return self.core.getStateLabel(LIGHT_PATH)

    def set_light_path(self, path):
        """path: position (1 to 4), full label or part of it (e.g. "Eye", "Left100")."""
        self._set_state(LIGHT_PATH, path)

    def _set_state(self, device, value):
        """Set a state device by position (1-based) or by (part of) its label, and wait."""
        labels = list(self.core.getStateLabels(device))
        if isinstance(value, int):
            if not 1 <= value <= len(labels):
                raise ValueError(f"{device} position must be in 1..{len(labels)}.")
            self.core.setState(device, value - 1)
        else:
            matches = [label for label in labels if value == label] or \
                      [label for label in labels if value.lower() in label.lower()]
            if len(matches) != 1:
                raise ValueError(f"{value!r} matches {len(matches)} of {labels}.")
            self.core.setStateLabel(device, matches[0])
        self.core.waitForDevice(device)

    # ---------- focus and stage (µm) ----------

    def get_z(self):
        """Focus position (µm)."""
        return self.core.getPosition(Z_DRIVE)

    def set_z(self, z):
        """Move the focus to z (µm, absolute) and wait. Take care of the sample!"""
        self.core.setPosition(Z_DRIVE, z)
        self.core.waitForDevice(Z_DRIVE)

    def move_z(self, dz):
        """Move the focus by dz (µm, relative) and wait."""
        self.core.setRelativePosition(Z_DRIVE, dz)
        self.core.waitForDevice(Z_DRIVE)

    def get_xy(self):
        """Stage position (x, y) in µm."""
        return self.core.getXPosition(XY_DRIVE), self.core.getYPosition(XY_DRIVE)

    def set_xy(self, x, y):
        """Move the stage to (x, y) (µm, absolute) and wait."""
        self.core.setXYPosition(XY_DRIVE, x, y)
        self.core.waitForDevice(XY_DRIVE)

    def move_xy(self, dx, dy):
        """Move the stage by (dx, dy) (µm, relative) and wait."""
        self.core.setRelativeXYPosition(XY_DRIVE, dx, dy)
        self.core.waitForDevice(XY_DRIVE)

    # ---------- Perfect Focus System ----------

    def get_pfs_status(self):
        """PFS status text from the microscope (e.g. 'Locked in focus', 'Unsupported objective lens')."""
        return self.core.getProperty(PFS, "Status")

    def is_pfs_on(self):
        return self.core.getProperty(PFS, "State") == "On"

    def set_pfs(self, on):
        """Switch the Perfect Focus on (True) or off (False)."""
        self.core.setProperty(PFS, "State", "On" if on else "Off")
        self.core.waitForDevice(PFS)

    def get_pfs_offset(self):
        return self.core.getPosition(PFS_OFFSET)

    def set_pfs_offset(self, offset):
        """PFS offset (focus position kept by the PFS, in offset-lens units)."""
        self.core.setPosition(PFS_OFFSET, offset)
        self.core.waitForDevice(PFS_OFFSET)

    # ---------- metadata ----------

    def get_metadata(self):
        """Current state of the microscope as a dictionary (to save with the images)."""
        readers = {
            "objective": (NOSEPIECE, self.get_objective),
            "light_path": (LIGHT_PATH, self.get_light_path),
            "z_um": (Z_DRIVE, self.get_z),
            "xy_um": (XY_DRIVE, self.get_xy),
            "pfs_on": (PFS, self.is_pfs_on),
            "pfs_status": (PFS, self.get_pfs_status),
            "pfs_offset": (PFS_OFFSET, self.get_pfs_offset),
        }
        metadata = {}
        for key, (device, read) in readers.items():
            if self.has(device):
                try:
                    metadata[key] = read()
                except Exception:
                    metadata[key] = None
        return metadata

    # ---------- closing ----------

    def close(self):
        """Release the microscope (it stays in its current state)."""
        if self.core is not None:
            self.core.unloadAllDevices()
            self.core = None
            self.devices = []

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()


if __name__ == "__main__":
    # quick check: connect, print the state, change nothing
    with NikonTi() as ti:
        print("objectives:", ti.objectives)
        print("light paths:", ti.light_paths)
        for key, value in ti.get_metadata().items():
            print(f"  {key:12s} {value}")
