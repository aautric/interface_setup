"""
Control of the Thorlabs EXULUS-HD2 spatial light modulator.

The SLM has two links to the PC:
    - USB (virtual COM port): settings/commands, handled by EXULUS_COMMAND_LIB.
    - HDMI: the phase image itself, the SLM is seen as a second monitor.

Python SDK (see EXULUS SDK Manual, chapter 3):
    C:/Program Files/Thorlabs/EXULUS/Help/SDK/Thorlabs_EXULUS_PythonSDK.zip
It is unzipped in Documents/Thorlabs_EXULUS_PythonSDK; SDK_PATHS below points to
the folders containing the .py wrappers and their DLLs (each wrapper must stay in
the same folder as its DLL).
"""

import ctypes
import os
import sys
import time

import numpy as np
from PIL import Image

# Folders of the unzipped Python SDK: one for the control
# (EXULUS_COMMAND_LIB.py + exulus_command_library.dll) and one for the display
# (Thorlabs_EXULUS_CGHDisplay.py + cgh_display.dll + glew32.dll). Adapt to your installation.
SDK_FOLDER = os.path.join(os.path.expanduser("~"), "Documents", "Thorlabs_EXULUS_PythonSDK")
SDK_PATHS = [
    os.path.join(SDK_FOLDER, "Thorlabs.EXULUS.Python.SDK"),
    os.path.join(SDK_FOLDER, "Thorlabs.EXULUS.CGHDisplay"),
]
for path in SDK_PATHS:
    if os.path.isdir(path):
        sys.path.append(path)
        os.add_dll_directory(path)  # so Windows finds the DLLs

try:
    from EXULUS_COMMAND_LIB import *  # noqa: E402,F401,F403
    from Thorlabs_EXULUS_CGHDisplay import *  # noqa: E402,F401,F403
    SDK_ERROR = None
except Exception as error:  # SDK missing: load_phase_pattern still usable
    SDK_ERROR = error

BAUD_RATE = 38400  # value used in EXULUS_COMMAND_LIB_EXAMPLE.py
TIMEOUT = 3        # seconds
SETTLE_TIME = 0.1  # s waited after show() so the liquid crystal settles (to measure on the setup)

ACK = 0x06  # answer of EXULUSCheckCommunication when the SLM is OK

# USB ids of the Silicon Labs CP210x bridge of the EXULUS-HD2. EXULUSListDevices sometimes
# returns nothing (the "unplug / plug again" state), but EXULUSOpen accepts the COM port.
CP210X_VID = 0x10C4
CP210X_PID = 0xEA60

SLM_WIDTH = 1920   # EXULUS-HD2 resolution (pixels), 8 µm pitch
SLM_HEIGHT = 1200


class SLM:
    """
    Thorlabs EXULUS-HD2 SLM.

    Usage:
        with SLM() as slm:
            slm.show("my_mask.png")
    """

    def __init__(self, serial_number=None, monitor=None,
                 baud_rate=BAUD_RATE, timeout=TIMEOUT, settle_time=SETTLE_TIME):
        """
        serial_number: serial number or COM port (e.g. "COM4") of the SLM;
                       if None, the first EXULUS found is used.
        monitor: monitor id of the SLM (from 1 to monitor count); if None the last one is used.
        settle_time: seconds waited at the end of show(), so that the mask is stable
                     before a camera acquisition (0 = no wait).
        """
        self.serial_number = serial_number
        self.monitor = monitor
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.settle_time = settle_time
        self.width = SLM_WIDTH
        self.height = SLM_HEIGHT
        self.hdl = None     # USB connection handle
        self.window = None  # HDMI display window handle

    def connect(self):
        """Find the SLM, open the USB connection and check that the SLM answers."""
        if SDK_ERROR is not None:
            raise RuntimeError(f"EXULUS SDK not loaded ({SDK_ERROR}). Check SDK_PATHS in SLM.py.")
        if self.serial_number is None:
            devices = EXULUSListDevices()  # list of [serialNumber, EXULUSType]
            if devices:
                print("EXULUS devices found:", devices)
                self.serial_number = devices[0][0]
            else:
                self.serial_number = find_exulus_port()

        if EXULUSIsOpen(self.serial_number) == 1:
            print(f"SLM {self.serial_number} is already open, closing it first is recommended.")

        hdl = EXULUSOpen(self.serial_number, self.baud_rate, self.timeout)
        if hdl < 0:
            raise RuntimeError(f"Could not open SLM {self.serial_number} (error code {hdl}).")
        self.hdl = hdl

        if not self.is_connected():
            self.close()
            raise RuntimeError(f"SLM {self.serial_number} does not answer.")

        print(f"Connected to SLM {self.serial_number} (handle {self.hdl}).")

    def is_connected(self):
        """Return True if the SLM answers on the USB connection."""
        if self.hdl is None:
            return False
        ack_code = [0]
        result = EXULUSCheckCommunication(self.hdl, ack_code)
        return result == 0 and ack_code[0] == ACK

    def open_window(self):
        """Create the full-screen display window on the SLM monitor (HDMI)."""
        n_monitors = CghDisplayGetMonitorCount()
        if n_monitors < 1:
            raise RuntimeError(f"No monitor found (return {n_monitors}).")
        if self.monitor is None:
            self.monitor = n_monitors

        window = CghDisplayCreateWindow(self.monitor, self.width, self.height, "SLM")
        if window < 0:
            raise RuntimeError(f"Could not create display window on monitor {self.monitor} "
                               f"(error code {window}).")
        self.window = window
        result = CghDisplaySetWindowInfo(window, self.width, self.height, 1)  # 1: gray scale
        if result < 0:
            raise RuntimeError(f"Could not set the display window info (error code {result}).")

    def show(self, source):
        """
        Load a phase pattern (gray levels 0-255), display it on the SLM and wait
        settle_time seconds.

        source: path to an image or .npy file, or a 2D numpy array (see load_phase_pattern).
        """
        pattern = load_phase_pattern(source, self.width, self.height)
        if self.window is None:
            self.open_window()

        buffer = pattern.ctypes.data_as(ctypes.POINTER(ctypes.c_ubyte))
        result = CghDisplayShowWindow(self.window, buffer)
        if result < 0:  # negative = failure, as in Thorlabs_EXULUS_CGHDisplay_Example.py
            raise RuntimeError(f"Could not display the pattern (error code {result}).")
        if self.settle_time:
            time.sleep(self.settle_time)

    def close(self):
        """Close the display window and the USB connection."""
        if self.window is not None:
            CghDisplayCloseWindow(self.window)
            self.window = None
        if self.hdl is not None:
            EXULUSClose(self.hdl)
            self.hdl = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()


def find_exulus_port():
    """Return the COM port of the EXULUS USB link (Silicon Labs CP210x bridge)."""
    from serial.tools import list_ports
    ports = [p.device for p in list_ports.comports()
             if p.vid == CP210X_VID and p.pid == CP210X_PID]
    if not ports:
        raise RuntimeError("No EXULUS SLM found. Check USB cable and power "
                           "(unplugging and plugging the USB cable again may help).")
    if len(ports) > 1:
        print(f"Several CP210x ports {ports}, using {ports[0]} (give SLM(serial_number='COMx')).")
    return ports[0]


def load_phase_pattern(source, width=SLM_WIDTH, height=SLM_HEIGHT):
    """
    Load a phase pattern encoded with integers from 0 to 255 (gray levels).

    source: path to an image (.png, .bmp, .tif...) or a .npy file, or a 2D numpy array.
    Returns a (height, width) uint8 array ready to be sent to the SLM.
    """
    if isinstance(source, np.ndarray):
        pattern = source
    elif str(source).endswith(".npy"):
        pattern = np.load(source)
    else:
        pattern = np.array(Image.open(source).convert("L"))  # 8-bit gray

    if pattern.shape != (height, width):
        raise ValueError(f"Pattern shape is {pattern.shape}, expected {(height, width)}.")
    if pattern.min() < 0 or pattern.max() > 255:
        raise ValueError(f"Pattern values must be in [0, 255] "
                         f"(found [{pattern.min()}, {pattern.max()}]).")
    if np.issubdtype(pattern.dtype, np.floating) and not np.all(pattern == np.round(pattern)):
        raise ValueError("Pattern values must be integers.")

    return np.ascontiguousarray(pattern, dtype=np.uint8)
