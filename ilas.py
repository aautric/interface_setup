"""
Laser lines and galvos of the GATACA iLas2 (TIRF), without MetaMorph or Modular.

The iLas2 is driven by a Pangolin QM2000 PCI card, through the Pangolin LD2000 library
(64-bit C:\\Windows\\System32\\LD2000.dll, loaded with ctypes). The card does two things:
  - its X / Y outputs drive the galvos: where the beam goes in the back focal plane (BFP),
    i.e. the incidence angle (epi, HiLo, TIRF) and its direction; a frame of points is
    played in a loop (one point: fixed beam; a circle: 360 deg TIRF);
  - its colour channels 1-6 (analog 0-255) set the power of each laser line (Oxxius: the
    analog modulation input of each line, e.g. the AOM of the 561 nm): each point of a frame
    has a colour code, and the palette ("colour register") of that code gives the value of
    each channel.
The lines (name, wavelength, channel, galvo calibration) are in ilas_config.json: up to 6,
one per channel. What Modular (GATACA, C:\\Program Files\\GATACA Systems\\Modular) does, read
from its LaserCore.dll / TIRFLib.dll, is done the same way here: session start, working scale
-8000...8000, 15000 points/s, frame 0 = laser off, TIRF colour codes 1000 + sum over the
lines of channel x (10, 10, 100, 100, 1000, 1000 for channels 1-6), palette entry of the
code with channel value = 255 x power / 100 (WriteColorRegEx, colour number -1), points
written one by one with WritePoint (numbered from 1). Angles as Modular: in the glass,
BFP radius = sin(angle) x n_glass / NA, as a fraction of the calibration ellipse of the
line (Modular's TIRFCalibration.xml: 4 beam positions on the BFP edge).
Tested on the card 2026-11-03 with the 561 nm (power steps, fixed beam, 5 deg circle).

Only one program can drive the card: close Modular (MainApp.exe) and MetaMorph first.
The lasers are switched off (frame 0, all powers 0) at connection, at close, at exit of
Python and after any error; the card keeps playing its last frame if Python is killed.

Usage:
    with ILas() as ilas:
        ilas.enable("561"); ilas.set_power("561", 5)   # % of the line
        ilas.set_angle(5, 0)        # deg in the glass, direction deg (or set_ring, set_point)
        ilas.laser_on()
        ...
        ilas.laser_off()
"""

import atexit
import ctypes
import json
import math
import os
import subprocess
import xml.etree.ElementTree as ET

LD2000_PATH = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "LD2000.dll")
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ilas_config.json")
OTHER_PROGRAMS = ("MainApp.exe", "MetaMorph.exe", "MMHost.exe")  # must not use the card

# session (as Modular)
MAX_FRAMES, MAX_POINTS, MAX_BUFFER, UNDOS = 20000, 6000, 100, 1
SCALE = 8000             # galvo units: -SCALE...SCALE on X and Y
POINTS_PER_SECOND = 15000
MIN_POINTS = 20          # DisplayMinimumPoints
STATUS_OK, NO_SESSION, NO_BOOT_FILE = 0, -101, -401
# frames: 0 = nothing (laser off, as Modular); ours far from Modular's (which start at 1)
FRAME_OFF, FRAME_BEAM = 0, 19000
BLANK_CODE = 0           # colour code of blank points: every channel 0
TIRF_CODE_OFFSET = 1000  # Modular's TIRF colour codes (an arbitrary code such as 0xABCD was
                         # stored as 0xAFCC by the card and gave no power, 2026-11-03)
CHANNEL_CODE = {1: 10, 2: 10, 3: 100, 4: 100, 5: 1000, 6: 1000}  # Modular GetCodeForChannel
PALETTE = 0              # palette written (Modular: 0 for TIRF)
CHANNELS = range(1, 7)
RING_POINTS = 100        # points of a 360 deg circle (Modular: 100, 6.67 ms per turn)


# ---------------------------------------------------------------- LD2000 structures


class Point(ctypes.Structure):
    """LD2000 point (lpt): position, colour code (rgba) and flags."""
    _fields_ = [(name, ctypes.c_int32) for name in
                ("x", "y", "z", "bb", "rgba", "x3d", "y3d", "grp", "vo_type")]


class ColorRegister(ctypes.Structure):
    """LD2000 colour register (creg): values 0-255 of the 6 channels for one colour code."""
    _fields_ = [(name, ctypes.c_int32) for name in
                ("colornumber", "screen_red", "screen_green", "screen_blue",
                 "ch1", "ch2", "ch3", "ch4", "ch5", "ch6", "intensity", "shutter")]


INT = ctypes.c_int32
REF = ctypes.POINTER(ctypes.c_int32)
SIGNATURES = {  # all return void; status through the last int*
    "InitialQMCheck": [REF],
    "CheckSessionSettings": [REF, REF, REF, REF, REF],
    "BeginSession": [INT, INT, INT, INT, REF],
    "EndSession": [],
    "CloseLDCommWindow": [],
    "GetSerialNumber": [REF],
    "SetWorkingScanners": [INT],
    "SetWorkingTracks": [INT],
    "SetWorkingScale": [INT, INT, INT, INT, INT, INT],
    "SetWorkingFrame": [INT],
    "DisplayFlags": [INT],
    "DisplayMinimumPoints": [INT],
    "DisplaySkew": [INT, INT, INT],
    "DisplayFreq3": [INT, INT, INT, REF, REF, REF],
    "DisplayFrame": [INT],
    "DisplayUpdate": [],
    "WritePoint": [INT, ctypes.POINTER(Point)],
    "DeletePoints": [INT, INT],
    "RGB1toRGB4": [REF, REF, REF, REF, REF],  # spelled so in the 64-bit DLL
    "WriteColorRegEx": [INT, ctypes.POINTER(ColorRegister)],
}


class LD2000:
    """The functions of LD2000.dll used here (loaded when created: only at connect())."""

    def __init__(self, path=LD2000_PATH):
        self.dll = ctypes.WinDLL(path)
        for name, argtypes in SIGNATURES.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = argtypes, None
            setattr(self, name, function)


def ints(n):
    return [ctypes.c_int32(0) for _ in range(n)]


# ---------------------------------------------------------------- configuration


def ellipse_from_points(points):
    """Calibration ellipse {"center", "half_axes"} (galvo units, axes along X and Y) through
    Modular's 4 beam positions: two at the same y (left, right), two at the same x (top,
    bottom). Falls back on the midpoints and half spans."""
    (x1, y1), (x2, y2), (x3, y3), (x4, y4) = points
    xc, yc = (x1 + x2) / 2, (y3 + y4) / 2
    # (x - xc)^2 u + (y - yc)^2 v = 1 for a left / right point and a top / bottom point
    a11, a12, a21, a22 = (x1 - xc) ** 2, (y1 - yc) ** 2, (x3 - xc) ** 2, (y3 - yc) ** 2
    det = a11 * a22 - a12 * a21
    if det:
        u, v = (a22 - a12) / det, (a11 - a21) / det
        if u > 0 and v > 0:
            return {"center": [xc, yc], "half_axes": [u ** -0.5, v ** -0.5]}
    return {"center": [xc, yc], "half_axes": [abs(x2 - x1) / 2, abs(y4 - y3) / 2]}


def read_modular_calibrations(path):
    """{name: ellipse} of the named, active calibrations of Modular's TIRFCalibration.xml."""
    calibrations = {}
    try:
        root = ET.parse(os.path.expanduser(path)).getroot()
    except (OSError, ET.ParseError):
        return calibrations
    for entry in root.findall("CalibrationPoint"):
        name = (entry.findtext("name_calib") or "").strip()
        laser = entry.find("calibPointsLaser")
        if not name or entry.findtext("active") != "true" or laser is None:
            continue
        try:
            points = [(float(p.findtext("xpos")), float(p.findtext("ypos"))) for p in laser]
            if len(points) == 4:
                calibrations[name] = {**ellipse_from_points(points), "source": "Modular",
                                      "validated": entry.findtext("validated_calib") == "true"}
        except (TypeError, ValueError):
            pass
    return calibrations


def load_config(path=CONFIG_PATH):
    """Lines {name: {...}} (config order), calibrations {name: ellipse}, objective."""
    with open(path, encoding="utf-8") as file:
        config = json.load(file)
    lines = {}
    for line in config["lines"]:
        name, channel = str(line["name"]), int(line["channel"])
        if name in lines or channel not in CHANNELS or \
                channel in [l["channel"] for l in lines.values()]:
            raise ValueError(f"{path}: line {name}: names and channels (1-6) must be unique.")
        lines[name] = {"wavelength": int(line["wavelength"]), "channel": channel,
                       "calibration": line.get("calibration"),
                       "low": float(line.get("low", 0)), "high": float(line.get("high", 100))}
    calibrations = read_modular_calibrations(config.get("modular_calibrations", ""))
    calibrations.update({name: {**c, "source": "ilas_config"}
                         for name, c in config.get("calibrations", {}).items()})
    objective = {"na": 1.49, "n_glass": 1.52, "n_medium": 1.35, **config.get("objective", {})}
    return lines, calibrations, objective


def channel_value(power, low=0.0, high=100.0):
    """Channel value 0-255 of a power 0-100 % (Modular's Wavelength.Intensity: 0 % -> 0)."""
    if power <= 0:
        return 0
    return int(min(255, round(255 * (power / 100 * (high - low) / 100 + low / 100))))


def colour_code(channels):
    """Modular's TIRF colour code of a set of channels."""
    return TIRF_CODE_OFFSET + sum(c * CHANNEL_CODE[c] for c in channels)


def other_programs():
    """Running programs that may use the card (Modular, MetaMorph)."""
    try:
        listing = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True,
                                 text=True, timeout=10).stdout.lower()
    except Exception:
        return []
    return [name for name in OTHER_PROGRAMS if f'"{name.lower()}"' in listing]


# ---------------------------------------------------------------- the device


class ILas:
    """
    Laser lines (power in %) and galvos (fixed beam at an angle, 360 deg circle, or galvo X, Y)
    of the iLas2. backend: LD2000-like object (tests); None: the real LD2000.dll, loaded at
    connect().
    """

    def __init__(self, backend=None, config_path=CONFIG_PATH):
        self.backend = backend
        self.ld = None
        self.lines, self.calibrations, self.objective = load_config(config_path)
        self.power = {name: 0.0 for name in self.lines}      # %
        self.enabled = {name: False for name in self.lines}  # lines on when the laser is on
        self.geometry_line = None   # line whose calibration sets the angles (None: first enabled)
        self.shape = {"mode": "angle", "angle_deg": 0.0, "direction_deg": 0.0}
        self.is_on = False
        self.written_code = None    # colour code of our palette entry on the card
        self.serial_number = None
        self.pps = None
        self.name = "iLas2 (QM2000)"

    # ---------- connection

    def connect(self):
        if self.backend is None:
            running = other_programs()
            if running:
                raise RuntimeError(f"Close {', '.join(running)} first: only one program can "
                                   f"drive the QM2000 card (galvos and laser powers).")
            if not os.path.exists(LD2000_PATH):
                raise RuntimeError(f"{LD2000_PATH} not found (Pangolin LD2000, 64-bit).")
        ld = self.backend or LD2000()
        status, = ints(1)
        ld.InitialQMCheck(ctypes.byref(status))
        if status.value != STATUS_OK:
            raise RuntimeError(f"QM2000 card not found or not ready (InitialQMCheck status "
                               f"{status.value}). Is the iLas / PC powered, LD2000.ini in "
                               f"C:\\Windows?")
        frames, points, buffer, undos, status = ints(5)
        ld.CheckSessionSettings(*(ctypes.byref(v) for v in (frames, points, buffer, undos, status)))
        if status.value < -1000:
            raise RuntimeError(f"LD2000 session problem (status {status.value}).")
        if status.value == NO_SESSION:
            status, = ints(1)
            ld.BeginSession(MAX_FRAMES, MAX_POINTS, MAX_BUFFER, UNDOS, ctypes.byref(status))
            ld.CloseLDCommWindow()
            if status.value == NO_BOOT_FILE:
                raise RuntimeError("QM2000 boot program LD.Q32 not found (BeginSession -401).")
            if status.value < 0:
                raise RuntimeError(f"LD2000 BeginSession failed (status {status.value}).")
        self.ld = ld
        atexit.register(self._safe_off)
        try:
            ld.SetWorkingScanners(-1)
            ld.SetWorkingTracks(-1)
            ld.DisplayFlags(15)
            ld.SetWorkingScale(-SCALE, SCALE, -SCALE, SCALE, -SCALE, SCALE)
            ld.SetWorkingScanners(1)
            ld.DisplayMinimumPoints(MIN_POINTS)
            ld.DisplaySkew(0, 4, 0)  # inter-track points, blank colour shift, beam brush
            ld.SetWorkingTracks(1)
            actual = ints(3)
            ld.DisplayFreq3(POINTS_PER_SECOND, 30000, 30000, *(ctypes.byref(v) for v in actual))
            self.pps = actual[0].value
            serial, = ints(1)
            ld.GetSerialNumber(ctypes.byref(serial))
            self.serial_number = serial.value
            self._write_code(BLANK_CODE, {})
            self.laser_off()
        except Exception:
            self._safe_off()
            self.ld = None
            raise

    def is_connected(self):
        return self.ld is not None

    def _check(self):
        if self.ld is None:
            raise RuntimeError("iLas not connected.")

    # ---------- palette (laser powers)

    def _write_code(self, code, values):
        """Palette entry of a colour code: {channel: value 0-255}."""
        reg = ColorRegister()
        rgb, msb = ctypes.c_int32(code), ctypes.c_int32(0)
        r, g, b = ints(3)
        self.ld.RGB1toRGB4(*(ctypes.byref(v) for v in (rgb, r, g, b, msb)))
        reg.screen_red, reg.screen_green, reg.screen_blue = r.value, g.value, b.value
        for channel in CHANNELS:
            setattr(reg, f"ch{channel}", int(values.get(channel, 0)))
        # Modular sets these bytes to 255 only for its blanking type, 0 for TIRF palettes
        reg.intensity = reg.shutter = 0
        reg.colornumber = -1
        self.ld.WriteColorRegEx(PALETTE, ctypes.byref(reg))

    def channel_values(self):
        """{channel: value} of the enabled lines at their power."""
        return {line["channel"]: channel_value(self.power[name], line["low"], line["high"])
                for name, line in self.lines.items() if self.enabled[name]}

    def beam_code(self):
        return colour_code(sorted(self.lines[n]["channel"] for n in self.lines if self.enabled[n]))

    def set_power(self, name, percent):
        """Power of a line (0-100 % of its channel range); applied at once if on."""
        self._line(name)
        if not 0 <= percent <= 100:
            raise ValueError(f"Power {percent} %: must be 0-100.")
        self.power[name] = float(percent)
        self._apply()

    def enable(self, name, on=True):
        """Line used (or not) when the laser is on. The last line switched off while the laser
        is on switches the laser off."""
        self._line(name)
        self.enabled[name] = bool(on)
        if self.is_on and not any(self.enabled.values()):
            self.laser_off()
        else:
            self._apply()

    def _line(self, name):
        if name not in self.lines:
            raise ValueError(f"No line {name!r} (lines: {list(self.lines)}).")

    # ---------- galvos (shape of the beam frame)

    def calibration(self):
        """(line name, calibration name, ellipse) used for the angles."""
        name = self.geometry_line or next((n for n in self.lines if self.enabled[n]),
                                          next(iter(self.lines)))
        calibration = self.lines[name]["calibration"]
        if calibration not in self.calibrations:
            raise RuntimeError(f"Line {name}: galvo calibration {calibration!r} not found "
                               f"(known: {list(self.calibrations)}).")
        return name, calibration, self.calibrations[calibration]

    def set_geometry_line(self, name):
        """Line whose galvo calibration sets the angles (None: the first enabled line)."""
        if name is not None:
            self._line(name)
        self.geometry_line = name
        self._apply()

    def max_angle(self):
        """Largest angle in the glass (beam on the BFP edge: NA of the objective)."""
        return math.degrees(math.asin(min(self.objective["na"] / self.objective["n_glass"], 1)))

    def critical_angle(self):
        return math.degrees(math.asin(self.objective["n_medium"] / self.objective["n_glass"]))

    def radius_fraction(self, angle_deg):
        """Fraction of the calibration ellipse for an angle in the glass (Modular AngleToRadius)."""
        return math.sin(math.radians(angle_deg)) * self.objective["n_glass"] / self.objective["na"]

    def _check_angle(self, angle_deg):
        if not 0 <= angle_deg <= self.max_angle():
            raise ValueError(f"Angle {angle_deg} deg: must be 0-{self.max_angle():.1f} "
                             f"(NA {self.objective['na']}).")

    def _check_xy(self, points):
        for x, y in points:
            if not (-SCALE <= x <= SCALE and -SCALE <= y <= SCALE):
                raise ValueError(f"Galvo position ({x:.0f}, {y:.0f}) outside -{SCALE}...{SCALE}.")

    def set_angle(self, angle_deg, direction_deg=0.0):
        """Fixed beam: angle in the glass (0 = epi), direction (azimuth) in deg."""
        self._check_angle(angle_deg)
        self._set_shape({"mode": "angle", "angle_deg": float(angle_deg),
                         "direction_deg": float(direction_deg) % 360})

    def set_ring(self, angle_deg):
        """360 deg TIRF: the beam turns on a cone of half angle angle_deg in the glass."""
        self._check_angle(angle_deg)
        self._set_shape({"mode": "ring", "angle_deg": float(angle_deg)})

    def set_point(self, x, y):
        """Fixed beam at galvo units (x, y)."""
        self._set_shape({"mode": "point", "x": float(x), "y": float(y)})

    def _set_shape(self, shape):
        old = self.shape
        self.shape = shape
        try:
            self._check_xy(self.frame_points())
        except Exception:
            self.shape = old
            raise
        self._apply()

    def frame_points(self):
        """(x, y) of the points of the beam frame."""
        if self.shape["mode"] == "point":
            return [(self.shape["x"], self.shape["y"])] * MIN_POINTS
        (cx, cy), (ax, ay) = (lambda e: (e["center"], e["half_axes"]))(self.calibration()[2])
        r = self.radius_fraction(self.shape["angle_deg"])
        if self.shape["mode"] == "angle":
            phi = math.radians(self.shape["direction_deg"])
            return [(cx + r * ax * math.cos(phi), cy + r * ay * math.sin(phi))] * MIN_POINTS
        return [(cx + r * ax * math.cos(2 * math.pi * k / RING_POINTS),
                 cy + r * ay * math.sin(2 * math.pi * k / RING_POINTS)) for k in range(RING_POINTS)]

    def _write_frame(self, frame, xy, code):
        """Points of a frame, written one by one (Modular's 64-bit way), the rest deleted.
        Points are numbered from 1 (WritePoint(0) is ignored, checked on the card 2026-11-03)."""
        self.ld.SetWorkingFrame(frame)
        for k, (x, y) in enumerate(xy, start=1):
            point = Point(x=int(round(x)), y=int(round(y)), z=0, rgba=code)
            self.ld.WritePoint(k, ctypes.byref(point))
        self.ld.DeletePoints(len(xy) + 1, MAX_POINTS)

    # ---------- on / off

    def _apply(self):
        """Palette entry and beam frame from the current state (only when on)."""
        if self.ld is None or not self.is_on:
            return
        try:
            self._play()
        except Exception:
            self._safe_off()
            raise

    def _play(self):
        code = self.beam_code()
        if self.written_code not in (None, code):
            self._write_code(self.written_code, {})  # entry of the old set of lines back to 0
        self._write_code(code, self.channel_values())
        self.written_code = code
        self._write_frame(FRAME_BEAM, self.frame_points(), code)
        self.ld.DisplayFrame(FRAME_BEAM)
        self.ld.DisplayUpdate()

    def laser_on(self):
        """Play the beam frame with the enabled lines at their power."""
        self._check()
        if not any(self.enabled.values()):
            raise RuntimeError("No laser line enabled.")
        try:
            self._play()
            self.is_on = True
        except Exception:
            self._safe_off()
            raise

    def laser_off(self):
        """Frame 0 (nothing), and our palette entry back to 0 on every channel."""
        self._check()
        self.is_on = False
        self.ld.DisplayFrame(FRAME_OFF)
        self.ld.DisplayUpdate()
        for code in {self.written_code, self.beam_code()} - {None}:
            self._write_code(code, {})
        self.ld.DisplayUpdate()

    def _safe_off(self):
        """Laser off, ignoring errors (exit, error paths)."""
        if self.ld is None:
            return
        try:
            self.laser_off()
        except Exception:
            pass

    # ---------- state

    def incidence(self):
        """{"angle_in_glass_deg", "effective_na", "illumination"} of the current beam (None
        for galvo X, Y)."""
        if self.shape["mode"] == "point":
            return None
        angle = self.shape["angle_deg"]
        kind = ("TIRF" if angle >= self.critical_angle() else "epi" if angle < 1 else "HiLo")
        return {"angle_in_glass_deg": round(angle, 2),
                "effective_na": round(self.objective["n_glass"] * math.sin(math.radians(angle)), 4),
                "illumination": kind}

    def get_metadata(self):
        galvos = {**self.shape, "units": f"galvo units -{SCALE}...{SCALE}"}
        if self.shape["mode"] != "point":
            line, name, ellipse = self.calibration()
            galvos.update(calibration=name, calibration_line=line,
                          calibration_center=ellipse["center"],
                          calibration_half_axes=ellipse["half_axes"], **self.incidence())
        x, y = self.frame_points()[0] if self.shape["mode"] != "ring" else (None, None)
        if x is not None:
            galvos.update(x=round(x, 1), y=round(y, 1))
        return {"device": self.name, "serial_number": self.serial_number,
                "laser_on": self.is_on,
                "lines": {name: {"wavelength_nm": line["wavelength"], "enabled": self.enabled[name],
                                 "power_percent": self.power[name], "channel": line["channel"],
                                 "calibration": line["calibration"]}
                          for name, line in self.lines.items()},
                "galvos": galvos, "objective": self.objective,
                "points_per_second": self.pps}

    def close(self):
        """Laser off and end of the LD2000 session."""
        if self.ld is None:
            return
        try:
            self.laser_off()
        finally:
            try:
                self.ld.EndSession()
            finally:
                self.ld = None
                atexit.unregister(self._safe_off)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()


# ---------------------------------------------------------------- stand-in for the tests


class FakeLD2000:
    """Records the LD2000 calls (no card): frames, palette, displayed frame."""

    def __init__(self, session_running=False):
        self.calls = []
        self.frames = {}
        self.palette = {}
        self.working_frame = None
        self.displayed = None
        self.session = session_running

    def _log(self, name, *args):
        self.calls.append(name)

    @staticmethod
    def _set(ref, value):
        ref._obj.value = value  # ref: ctypes.byref(...)

    def InitialQMCheck(self, status):
        self._set(status, 0)

    def CheckSessionSettings(self, *refs):
        self._set(refs[-1], 0 if self.session else NO_SESSION)

    def BeginSession(self, frames, points, buffer, undos, status):
        self.session = True
        self._set(status, 0)

    def EndSession(self):
        self.session = False
        self._log("EndSession")

    def GetSerialNumber(self, ref):
        self._set(ref, 1234)

    def DisplayFreq3(self, a, b, c, ra, rb, rc):
        for ref, value in ((ra, a), (rb, b), (rc, c)):
            self._set(ref, value)

    def RGB1toRGB4(self, rgb, r, g, b, msb):
        code = rgb._obj.value
        for ref, value in ((r, code & 255), (g, (code >> 8) & 255), (b, (code >> 16) & 255)):
            self._set(ref, value)

    def WriteColorRegEx(self, palette, reg):
        reg = reg._obj
        code = reg.screen_red | reg.screen_green << 8 | reg.screen_blue << 16
        self.palette[code] = {c: getattr(reg, f"ch{c}") for c in CHANNELS}

    def SetWorkingFrame(self, frame):
        self.working_frame = frame
        self.frames.setdefault(frame, [])

    def WritePoint(self, number, point):  # numbered from 1, as the card (0 ignored)
        point = point._obj
        points = self.frames[self.working_frame]
        entry = (point.x, point.y, point.rgba)
        if number < 1:
            return
        if number <= len(points):
            points[number - 1] = entry
        else:
            points.append(entry)

    def DeletePoints(self, start, end):
        del self.frames[self.working_frame][start - 1:end]

    def DisplayFrame(self, frame):
        self.displayed = frame

    def DisplayUpdate(self):
        self._log("DisplayUpdate")

    def emitted(self):
        """{channel: value} reaching the lasers now (what the card plays)."""
        out = {}
        for _, _, code in self.frames.get(self.displayed, []):
            for channel, value in self.palette.get(code, {}).items():
                if value:
                    out[channel] = max(out.get(channel, 0), value)
        return out

    def __getattr__(self, name):  # the other calls: only recorded
        if name[:1].isupper():
            return lambda *args: self._log(name, *args)
        raise AttributeError(name)


if __name__ == "__main__":
    # connect, show the state, disconnect (laser off)
    import sys
    if sys.argv[1:] != ["status"]:
        sys.exit("Usage: python ilas.py status")
    with ILas() as device:
        print(json.dumps(device.get_metadata(), indent=4))
