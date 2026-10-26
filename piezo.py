"""
Control of the Thorlabs PPC001 precision piezo controller (1 channel, 450 µm travel,
capacitive feedback).

The controller is driven through the Kinesis C library
(Thorlabs.MotionControl.Benchtop.PrecisionPiezo.dll):
    1. Install Thorlabs Kinesis, 64-bit version (C:/Program Files/Thorlabs/Kinesis).
    2. Unplug / plug the USB cable once after the installation.
Close the Kinesis software before connecting.

Two ways to drive the piezo:
    - open loop: set_voltage(V), the position depends on the piezo response
      (hysteresis, creep);
    - closed loop: set_position(µm), the controller uses the capacitive sensor.
      The sensor is calibrated in the factory: no zeroing is needed (the PPC001 ignores
      the zero command). At 0 V it reads about -16 µm, so 0 µm is reached at about 6 V.
"""

import ctypes
import os
import time

KINESIS_PATH = r"C:\Program Files\Thorlabs\Kinesis"
PPC_TYPE_ID = 44      # Kinesis device type of the PPC001 (first digits of the serial number)
POLLING_PERIOD = 50   # ms, the DLL reads the controller state at this period
SETTLE_TIME = 0       # s waited in addition once a move is confirmed by the readings
WAIT_TIMEOUT = 2      # s, maximum wait for a command to show in the readings
POSITION_TOLERANCE = 0.05  # µm, closed loop position considered reached (holds ~0.02 µm)
VOLTAGE_TOLERANCE = 0.1   # V, open loop voltage considered applied
FULL_SCALE = 32767    # voltages and positions are given as a fraction of this value

OPEN_LOOP = 1
CLOSED_LOOP = 2

# "error" is always set on this PPC001 and does not prevent anything
STATUS_BITS = {
    "actuator_connected": 0x00000001,
    "closed_loop": 0x00000400,
    "amplifier_too_hot": 0x00000800,
    "position_below_range": 0x00001000,
    "position_above_range": 0x00002000,
    "active": 0x20000000,
    "error": 0x40000000,
    "enabled": 0x80000000,
}

_lib = None


def _load_library():
    """Load the Kinesis PPC library once and declare the return types used here."""
    global _lib
    if _lib is None:
        os.add_dll_directory(KINESIS_PATH)  # the DLL needs the other Kinesis DLLs
        lib = ctypes.CDLL(os.path.join(KINESIS_PATH,
                                       "Thorlabs.MotionControl.Benchtop.PrecisionPiezo.dll"))
        for name in ("PPC_GetOutputVoltage", "PPC_GetPosition", "PPC_GetMaxOutputVoltage",
                     "PPC_GetPositionControlMode", "PPC_Open", "PPC_SetOutputVoltage",
                     "PPC_SetPosition", "PPC_SetPositionControlMode",
                     "TLI_BuildDeviceList", "TLI_GetDeviceListByTypeExt"):
            getattr(lib, name).restype = ctypes.c_short
        lib.PPC_GetStatusBits.restype = ctypes.c_uint32
        lib.PPC_GetMaximumTravel.restype = ctypes.c_uint16
        lib.PPC_StartPolling.restype = ctypes.c_bool
        _lib = lib
    return _lib


def list_controllers():
    """Return the serial numbers of the PPC001 controllers connected to the PC."""
    lib = _load_library()
    lib.TLI_BuildDeviceList()
    buffer = ctypes.create_string_buffer(1024)
    lib.TLI_GetDeviceListByTypeExt(buffer, 1024, PPC_TYPE_ID)
    return [s for s in buffer.value.decode().split(",") if s]


class Piezo:
    """
    Thorlabs PPC001 piezo controller.

    Usage:
        with Piezo() as piezo:
            piezo.set_voltage(10)
            print(piezo.get_position())
    """

    def __init__(self, serial_number=None, settle_time=SETTLE_TIME):
        """
        serial_number: serial number of the PPC001; if None, the first one found is used.
        settle_time: seconds waited in addition after each move, once the readings
                     confirm it (e.g. to let the objective stabilise before an image).
        """
        self.serial_number = serial_number
        self.settle_time = settle_time
        self.lib = None
        self.max_voltage = None  # V
        self.max_travel = None   # µm

    def connect(self):
        """Open the controller and start reading its state (does not move the piezo)."""
        self.lib = _load_library()
        if self.serial_number is None:
            controllers = list_controllers()
            if not controllers:
                raise RuntimeError("No PPC001 piezo controller found. Check USB and power, "
                                   "and that the Kinesis software is closed.")
            self.serial_number = controllers[0]
        else:
            self.lib.TLI_BuildDeviceList()
        self._serial = self.serial_number.encode()

        error = self.lib.PPC_Open(self._serial)
        if error != 0:
            raise RuntimeError(f"Could not open piezo controller {self.serial_number} "
                               f"(Kinesis error {error}).")
        self.lib.PPC_StartPolling(self._serial, POLLING_PERIOD)
        self.lib.PPC_RequestSettings(self._serial)
        time.sleep(3 * POLLING_PERIOD / 1000)  # first state readings
        self.max_voltage = self.lib.PPC_GetMaxOutputVoltage(self._serial) / 10
        self.max_travel = self.lib.PPC_GetMaximumTravel(self._serial) / 10
        print(f"Connected to piezo controller {self.serial_number} "
              f"(travel {self.max_travel:.0f} µm, max {self.max_voltage:.0f} V).")

    def is_connected(self):
        """Return True if the controller answers."""
        return self.lib is not None and bool(self.lib.PPC_CheckConnection(self._serial))

    # ---------- state ----------

    def get_status(self):
        """Status flags of the controller as a dictionary (see STATUS_BITS)."""
        self.lib.PPC_RequestStatus(self._serial)
        bits = self.lib.PPC_GetStatusBits(self._serial)
        return {name: bool(bits & bit) for name, bit in STATUS_BITS.items()}

    def is_closed_loop(self):
        self.lib.PPC_RequestPositionControlMode(self._serial)
        return self.lib.PPC_GetPositionControlMode(self._serial) == CLOSED_LOOP

    def get_voltage(self):
        """Output voltage (V)."""
        self.lib.PPC_RequestOutputVoltage(self._serial)
        return self.lib.PPC_GetOutputVoltage(self._serial) / FULL_SCALE * self.max_voltage

    def get_position(self):
        """Position measured by the sensor (µm), about -16 µm at 0 V."""
        self.lib.PPC_RequestActualPosition(self._serial)
        return self.lib.PPC_GetPosition(self._serial) / FULL_SCALE * self.max_travel

    # ---------- moves ----------

    def set_voltage(self, voltage):
        """Open loop: set the output voltage (V, 0 to max_voltage), wait until it is applied."""
        if self.is_closed_loop():
            raise RuntimeError("The piezo is in closed loop: use set_position(), "
                               "or set_closed_loop(False) first.")
        if not 0 <= voltage <= self.max_voltage:
            raise ValueError(f"Voltage must be in [0, {self.max_voltage}] V.")
        self._check(self.lib.PPC_SetOutputVoltage(
            self._serial, round(voltage / self.max_voltage * FULL_SCALE)), "set the voltage")
        self._wait_until(lambda: abs(self.get_voltage() - voltage) < VOLTAGE_TOLERANCE,
                         f"output voltage {voltage} V")

    def set_position(self, position):
        """Closed loop: move to position (µm, 0 to max_travel), wait until it is reached."""
        if not self.is_closed_loop():
            raise RuntimeError("The piezo is in open loop: use set_voltage(), "
                               "or set_closed_loop(True) first.")
        if not 0 <= position <= self.max_travel:
            raise ValueError(f"Position must be in [0, {self.max_travel}] µm.")
        self._check(self.lib.PPC_SetPosition(
            self._serial, round(position / self.max_travel * FULL_SCALE)), "set the position")
        self._wait_until(lambda: abs(self.get_position() - position) < POSITION_TOLERANCE,
                         f"position {position} µm")

    def move_by(self, distance):
        """Closed loop: move by distance (µm, relative)."""
        self.set_position(self.get_position() + distance)

    def set_closed_loop(self, closed):
        """
        Switch between closed loop (True, positions in µm) and open loop (False, volts).
        Entering the closed loop keeps the current position (no jump).
        """
        self._check(self.lib.PPC_SetPositionControlMode(
            self._serial, CLOSED_LOOP if closed else OPEN_LOOP), "change the loop mode")
        self._wait_until(lambda: self.is_closed_loop() == closed,
                         "closed loop" if closed else "open loop", settle=False)

    def _wait_until(self, done, what, settle=True):
        """
        Wait until done() is True: the readings of the DLL arrive ~100 ms after a
        command, so reading right after it gives the previous state. Then wait settle_time.
        """
        start = time.time()
        while not done():
            if time.time() - start > WAIT_TIMEOUT:
                raise RuntimeError(f"The piezo did not reach {what} within {WAIT_TIMEOUT} s "
                                   f"(now {self.get_voltage():.2f} V, {self.get_position():.3f} µm).")
            time.sleep(POLLING_PERIOD / 1000 / 2)
        if settle and self.settle_time:
            time.sleep(self.settle_time)

    def _check(self, error, action):
        if error != 0:
            raise RuntimeError(f"Could not {action} (Kinesis error {error}).")

    # ---------- metadata ----------

    def get_metadata(self):
        """State of the piezo as a dictionary (to save with the camera images)."""
        return {
            "piezo_serial_number": self.serial_number,
            "piezo_closed_loop": self.is_closed_loop(),
            "piezo_voltage_V": round(self.get_voltage(), 3),
            "piezo_position_um": round(self.get_position(), 3),
        }

    # ---------- closing ----------

    def close(self):
        """Stop reading the controller and close it. The piezo keeps its voltage."""
        if self.lib is not None:
            self.lib.PPC_StopPolling(self._serial)
            self.lib.PPC_Close(self._serial)
            self.lib = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()


if __name__ == "__main__":
    # quick check: connect and print the state, no move
    print("PPC001 controllers:", list_controllers())
    with Piezo() as piezo:
        print("status:", {k: v for k, v in piezo.get_status().items() if v})
        for key, value in piezo.get_metadata().items():
            print(f"  {key:22s} {value}")
