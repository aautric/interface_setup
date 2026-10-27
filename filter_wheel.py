"""
Control of the Sutter Instrument Lambda 10-3: filter wheels A, B (C) and shutters A, B.

The controller is connected by USB (Sutter "SI Unified Lambda USB driver", VCP mode, so
that it appears as a COM port) and driven with pyserial (pip install pyserial).
Protocol: Lambda 10-3 operation manual, chapter 5 (one byte per command, each byte is
echoed back, then a carriage return (13) is sent when the command has finished).

Close any other software using the Lambda (Micro-Manager, MetaMorph...) before connecting.
"""

import time

import serial
from serial.tools import list_ports

USB_VID, USB_PID = 0x1342, 0x1003  # Sutter Instrument Lambda 10-3 USB
BAUD_RATE = 128000  # USB (9600 for the RS-232 port)
TIMEOUT = 5         # s, maximum time for a command (a wheel move) to finish
DEFAULT_SPEED = 1   # wheel speed 0 (fastest) to 7 (slowest)

# Filter in each wheel position (0 to 9), shown in the GUI and saved in the metadata.
# "" = position number only. Wheel A: left filters, wheel B: right filters (setup notes,
# 2026-10-27, checked on the setup); the numbers of the notes are taken as the positions.
FILTERS = {
    "A": ["", "690/40 (exc. 640)", "607/36 (exc. 560)", "520/70 (exc. 488)", "",
          "open", "", "", "", ""],
    "B": ["", "617/40", "MF469/35", "525/50", "", "", "", "", "", ""],
    "C": [""] * 10,
}
# side of the setup of each wheel, shown in the GUI and in the metadata keys
WHEEL_SIDES = {"A": "left", "B": "right", "C": ""}

# command bytes
WHEEL_C_PREFIX = 252
SHUTTER_OPEN = {"A": 170, "B": 186}
SHUTTER_CLOSE = {"A": 172, "B": 188}
SHUTTER_STATES = {170: "open", 171: "conditional", 172: "closed",
                  186: "open", 187: "conditional", 188: "closed"}
STATUS = 204
ON_LINE = 238
LOCAL = 239
CONFIGURATION = 253
CR = 13


def find_port():
    """Return the COM port of the Lambda 10-3 USB connection."""
    ports = [p.device for p in list_ports.comports() if (p.vid, p.pid) == (USB_VID, USB_PID)]
    if not ports:
        raise RuntimeError("No Lambda 10-3 found on USB. Check the cable, the power and the "
                           "Sutter USB driver (VCP mode).")
    return ports[0]


class FilterWheel:
    """
    Sutter Lambda 10-3 controller with its filter wheels and shutters.

    Usage:
        with FilterWheel() as lambda10:
            lambda10.move("A", 3)
            lambda10.open_shutter("A")
    """

    def __init__(self, port=None, baud_rate=BAUD_RATE, timeout=TIMEOUT):
        """port: COM port (e.g. "COM6"); if None, the Lambda 10-3 USB port is searched."""
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.serial = None
        self.wheels = []     # connected wheels, e.g. ["A", "B"]
        self.shutters = []   # connected shutters, e.g. ["A", "B"]
        self.configuration = None
        self.speed = {wheel: DEFAULT_SPEED for wheel in "ABC"}

    def connect(self):
        """Open the port, put the controller on line and read its configuration."""
        if self.port is None:
            self.port = find_port()
        self.serial = serial.Serial(self.port, self.baud_rate, timeout=0.1)
        try:
            self._command([ON_LINE])
            reply = self._command([CONFIGURATION])  # e.g. "10-3WA-25WB-25WC-NCSA-IQSB-IQ"
        except Exception:
            self.serial.close()
            self.serial = None
            raise
        self.configuration = reply.decode("ascii", "replace")
        items = [self.configuration[i:i + 5] for i in range(4, len(self.configuration), 5)]
        self.wheels = [item[1] for item in items
                       if item.startswith("W") and item[3:] not in ("NC", "ER")]
        self.shutters = [item[1] for item in items if item.startswith("S")]
        print(f"Connected to Lambda {self.configuration[:4]} on {self.port} "
              f"(wheels {', '.join(self.wheels) or '-'}, shutters {', '.join(self.shutters) or '-'}).")

    def is_connected(self):
        return self.serial is not None and self.serial.is_open

    def _command(self, command):
        """
        Send the command bytes, wait for their echo and the final carriage return.
        Return the bytes the controller sent between the echo and the carriage return.
        """
        self.serial.reset_input_buffer()
        self.serial.write(bytes(command))
        reply = bytearray()
        end = time.time() + self.timeout
        while len(reply) <= len(command) or reply[-1] != CR:
            if time.time() > end:
                raise RuntimeError(f"The Lambda 10-3 did not answer command {list(command)} "
                                   f"(got {list(reply)}). Is it on line (ON LINE key)?")
            reply += self.serial.read(self.serial.in_waiting or 1)
        if reply[:len(command)] != bytes(command):
            raise RuntimeError(f"Unexpected answer {list(reply)} to command {list(command)}.")
        return bytes(reply[len(command):-1])

    # ---------- filter wheels ----------

    def move(self, wheel, position, speed=None):
        """
        Move a wheel ("A", "B" or "C") to a position (0 to 9) and wait until it is there.
        speed: 0 (fastest) to 7 (slowest); None = the speed last used for this wheel.
        """
        if wheel not in self.wheels:
            raise ValueError(f"Wheel {wheel} is not connected (wheels: {self.wheels}).")
        if not 0 <= position <= 9:
            raise ValueError("Position must be in 0..9.")
        if speed is not None:
            if not 0 <= speed <= 7:
                raise ValueError("Speed must be in 0..7.")
            self.speed[wheel] = speed
        byte = (wheel == "B") * 128 + self.speed[wheel] * 16 + position
        self._command([WHEEL_C_PREFIX, byte] if wheel == "C" else [byte])

    def get_position(self, wheel):
        return self.get_status()["wheels"][wheel]

    @staticmethod
    def wheel_label(wheel):
        """'A (right)', or 'A' if the side is not known."""
        side = WHEEL_SIDES.get(wheel, "")
        return f"{wheel} ({side})" if side else wheel

    def filter_name(self, wheel, position=None):
        """'position' or 'position - filter' for the current (or given) position."""
        if position is None:
            position = self.get_position(wheel)
        name = FILTERS.get(wheel, [""] * 10)[position]
        return f"{position} - {name}" if name else str(position)

    # ---------- shutters ----------

    def open_shutter(self, shutter):
        self._check_shutter(shutter)
        self._command([SHUTTER_OPEN[shutter]])

    def close_shutter(self, shutter):
        self._check_shutter(shutter)
        self._command([SHUTTER_CLOSE[shutter]])

    def is_shutter_open(self, shutter):
        return self.get_status()["shutters"][shutter] == "open"

    def _check_shutter(self, shutter):
        if shutter not in self.shutters:
            raise ValueError(f"Shutter {shutter} is not connected (shutters: {self.shutters}).")

    # ---------- status ----------

    def get_status(self):
        """
        {"wheels": {"A": position, ...}, "speeds": {"A": speed, ...},
         "shutters": {"A": "open" / "closed" / "conditional", ...}}
        """
        data = list(self._command([STATUS]))
        status = {"wheels": {}, "speeds": {}, "shutters": {}}
        wheel_bytes = {"A": data[0], "B": data[1]}
        if data[2] == WHEEL_C_PREFIX:
            wheel_bytes["C"] = data[3]
            shutter_bytes = data[4:6]
        else:
            shutter_bytes = data[2:4]
        for wheel, byte in wheel_bytes.items():
            if wheel in self.wheels:
                status["wheels"][wheel] = byte & 0x0F
                status["speeds"][wheel] = (byte >> 4) & 0x07
        for shutter, byte in zip("AB", shutter_bytes):
            if shutter in self.shutters:
                status["shutters"][shutter] = SHUTTER_STATES.get(byte, f"unknown ({byte})")
        return status

    def get_metadata(self):
        """State of the wheels and shutters as a dictionary (to save with the images),
        e.g. {"filter_A_right": "2 - MF469/35", "shutter_A": "closed"}."""
        status = self.get_status()
        metadata = {f"filter_{self.wheel_label(wheel).replace(' (', '_').rstrip(')')}":
                    self.filter_name(wheel, position)
                    for wheel, position in status["wheels"].items()}
        metadata.update({f"shutter_{shutter}": state
                         for shutter, state in status["shutters"].items()})
        return metadata

    # ---------- closing ----------

    def close(self):
        """Give the control back to the keypad (local mode) and close the port.
        The wheels and shutters keep their state."""
        if self.serial is not None:
            try:
                self._command([LOCAL])
            finally:
                self.serial.close()
                self.serial = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()


if __name__ == "__main__":
    # quick check: connect and print the state, no move
    with FilterWheel() as lambda10:
        print("configuration:", lambda10.configuration)
        for key, value in lambda10.get_metadata().items():
            print(f"  {key:10s} {value}")
