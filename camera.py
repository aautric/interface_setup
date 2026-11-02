"""
Control of the Teledyne Photometrics Prime 95B camera.

The camera is driven through PVCAM (Teledyne driver/SDK) and its Python wrapper PyVCAM:
    1. Install the PVCAM SDK from the Teledyne Vision Solutions website.
    2. pip install PyVCAM
Saving images needs tifffile: pip install tifffile

Close the camera in any other software (PVCamTest, Micro-Manager...) before connecting.
"""

from datetime import datetime

from pyvcam import constants as const
from pyvcam import pvc
from pyvcam.camera import Camera as PVCamera

from saving import FrameCollector, SequenceWriter, save_images

EXPOSURE_TIME = 20  # ms (default exposure resolution of PVCAM is milliseconds)
TIMEOUT = 5000      # ms, maximum wait for a frame
SEQUENCE_BUFFER_BYTES = 1e9  # PVCAM circular buffer of a sequence (RAM): ~350 full frames
# despeckle filters of the camera (replace isolated bright / dark pixels): off at each
# connection, raw data for quantitative work (they were all on, 2026-10-29)
DESPECKLE = False

_pvcam_users = 0  # number of open Camera objects / list_cameras() calls using PVCAM


def _init_pvcam():
    """Initialise PVCAM only if nobody uses it yet."""
    global _pvcam_users
    if _pvcam_users == 0:
        pvc.init_pvcam()
    _pvcam_users += 1


def _uninit_pvcam():
    """Close PVCAM only when its last user is done with it."""
    global _pvcam_users
    _pvcam_users -= 1
    if _pvcam_users == 0:
        pvc.uninit_pvcam()


class Camera:
    """
    Teledyne Photometrics Prime 95B camera.

    Usage:
        with Camera() as cam:
            cam.set_exposure(50)
            image = cam.snap()
    """

    def __init__(self, name=None, exposure_time=EXPOSURE_TIME, timeout=TIMEOUT):
        """
        name: PVCAM name of the camera (see Camera.list_cameras()); if None, the first one found.
        exposure_time: exposure time in ms.
        """
        self.name = name
        self.exposure_time = exposure_time
        self.timeout = timeout
        self.cam = None  # PyVCAM camera object
        self.live = False
        self.roi = None  # (x, y, width, height), None = full frame
        self.last_metadata = None  # metadata of the last snap / sequence
        self._temperature = None  # last temperature read (see temperature)
        self._sequence_running = False
        self._defaults = {}  # adjustable parameters as read at connection (advanced settings)

    @staticmethod
    def list_cameras():
        """Return the names of the PVCAM cameras connected to the PC (safe while connected)."""
        _init_pvcam()
        try:
            return PVCamera.get_available_camera_names()
        finally:
            _uninit_pvcam()

    def connect(self):
        """Initialise PVCAM, find the camera and open it."""
        _init_pvcam()
        try:
            if self.name is None:
                self.cam = next(PVCamera.detect_camera())
            else:
                self.cam = PVCamera.select_camera(self.name)
        except (StopIteration, RuntimeError):
            _uninit_pvcam()
            raise RuntimeError("No PVCAM camera found. Check USB/PCIe cable, power and driver.")

        self.cam.open()
        self.cam.exp_time = self.exposure_time
        self.name = self.cam.name
        try:
            self.set_despeckle(DESPECKLE)
        except Exception as error:
            print("Could not set the despeckle filters:", error)
        # the camera comes back to these values each time it is opened: they are the defaults
        # of the advanced settings (PVCAM's own ATTR_DEFAULT is not that state: e.g. frame
        # summing on, driver time-out 0)
        self._defaults = {}
        try:
            self._defaults = {p["name"]: p["value"] for p in self.get_parameters()
                              if p["writable"] and p["value"] is not None
                              and not str(p["value"]).startswith("(")
                              and p["name"] not in _PP_SELECTORS}
        except Exception as error:
            print("Could not read the camera parameters:", error)
        # kept by the camera when it is closed: fixed normal values instead
        self._defaults.update((k, v) for k, v in _FIXED_DEFAULTS.items() if k in self._defaults)
        print(f"Connected to camera {self.name} (sensor {self.sensor_size}, "
              f"{self.cam.bit_depth} bits, {self.temperature:.1f} °C).")

    def is_connected(self):
        """Return True if the camera is open."""
        return self.cam is not None and self.cam.is_open

    # ---------- settings ----------

    @property
    def sensor_size(self):
        """(width, height) of the full sensor in pixels (1200 x 1200 for the Prime 95B)."""
        return self.cam.sensor_size

    @property
    def temperature(self):
        """
        Current sensor temperature (°C). During the live or a sequence, the last value read
        before it: reading the temperature stops a running PVCAM acquisition
        ("Acquisition not active").
        """
        if not (self.live or self._sequence_running):
            self._temperature = self.cam.temp
        return self._temperature

    def set_exposure(self, exposure_time):
        """Set the exposure time (ms) used by snap, acquire_sequence and start_live."""
        self.exposure_time = exposure_time
        self.cam.exp_time = exposure_time

    def set_roi(self, x, y, width, height):
        """
        Acquire only a region of interest: top-left corner (x, y), size width x height,
        in full-sensor pixels. Raises ValueError if the ROI does not fit on the sensor.
        """
        sensor_width, sensor_height = self.sensor_size
        if x < 0 or y < 0 or width < 1 or height < 1:
            raise ValueError(f"Invalid ROI {(x, y, width, height)}: x, y must be >= 0 "
                             f"and width, height >= 1.")
        if x + width > sensor_width or y + height > sensor_height:
            raise ValueError(f"ROI {(x, y, width, height)} goes beyond the sensor "
                             f"({sensor_width} x {sensor_height}).")
        self.cam.reset_rois()
        self.cam.set_roi(x, y, width, height)
        self.roi = (x, y, width, height)

    def reset_roi(self):
        """Go back to full frame, binning 1x1."""
        self.cam.reset_rois()
        self.cam.binning = (1, 1)
        self.roi = None

    def set_binning(self, binning):
        """Set the binning, same in x and y (e.g. 1, 2...)."""
        self.cam.binning = (binning, binning)

    def set_readout(self, readout_port=None, speed=None, gain=None):
        """
        Set readout port, speed index and gain index (indexes of the PVCAM speed table).
        They are set in this order, as required by PVCAM. None = unchanged.
        """
        if readout_port is not None:
            self.cam.readout_port = readout_port
        if speed is not None:
            self.cam.speed = speed
        if gain is not None:
            self.cam.gain = gain

    # ---------- all parameters (advanced settings) ----------

    def get_parameters(self):
        """
        All the PVCAM parameters supported by the camera, as a list of dictionaries:
        name, value, writable, kind ("enum", "bool", "int", "float", "text", "other"),
        options (enum, bool), min / max (numbers), live (False: reading it during the
        live stops the acquisition, so stop the live first), description, and default
        (adjustable ones: the value read at connection).
        Exposure, ROI and binning are better changed with set_exposure / set_roi / set_binning.
        """
        parameters = []
        for name in sorted(n for n in dir(const) if n.startswith("PARAM_")):
            pid = getattr(const, name)
            entry = {"name": name[len("PARAM_"):], "writable": False, "kind": "other",
                     "value": None, "live": True,
                     "description": _PVCAM_DESCRIPTIONS.get(name[len("PARAM_"):], "")}
            try:
                if not self.cam.check_param(pid):
                    continue
                access = self.cam.get_param(pid, const.ATTR_ACCESS)
                kind = self.cam.get_param(pid, const.ATTR_TYPE)
                entry["live"] = bool(self.cam.get_param(pid, const.ATTR_LIVE))
                entry["writable"] = access in (const.ACC_READ_WRITE, const.ACC_WRITE_ONLY)
                if kind == const.TYPE_CHAR_PTR and \
                        self.cam.get_param(pid, const.ATTR_COUNT) > _PVCAM_TEXT_MAX:
                    # PyVCAM copies text into a 32-character buffer: a longer one crashes Python
                    entry.update(kind="text", value="(text too long to be read)", writable=False)
                    parameters.append(entry)
                    continue
                value = (self.cam.get_param(pid) if access in (const.ACC_READ_ONLY, const.ACC_READ_WRITE)
                         else None)
                show = (lambda raw: raw)  # raw PVCAM value -> value shown
                if kind == const.TYPE_ENUM:
                    names = {v: k for k, v in self.cam.read_enum(pid).items()}
                    show = (lambda raw: names.get(raw, raw))
                    entry.update(kind="enum", options=list(names.values()))
                elif kind == const.TYPE_BOOLEAN:
                    show = (lambda raw: str(bool(raw)))
                    entry.update(kind="bool", options=["False", "True"])
                elif kind in _PVCAM_NUMBER_TYPES:
                    entry.update(kind="float" if kind in _PVCAM_FLOAT_TYPES else "int",
                                 min=self.cam.get_param(pid, const.ATTR_MIN),
                                 max=self.cam.get_param(pid, const.ATTR_MAX))
                elif kind == const.TYPE_CHAR_PTR:
                    entry.update(kind="text")
                else:  # regions, smart streaming...: shown only
                    entry.update(writable=False)
                entry["value"] = None if value is None else show(value)
                if entry["writable"] and entry["name"] in self._defaults:
                    entry["default"] = self._defaults[entry["name"]]
            except Exception as error:
                entry.update(value=f"(not readable: {error})", writable=False)
            parameters.append(entry)
        return parameters

    def set_parameter(self, name, value):
        """Set a PVCAM parameter by name (as given by get_parameters), value as text or number."""
        pid = getattr(const, "PARAM_" + name)
        kind = self.cam.get_param(pid, const.ATTR_TYPE)
        if kind == const.TYPE_ENUM:
            value = self.cam.read_enum(pid)[value] if isinstance(value, str) else int(value)
        elif kind == const.TYPE_BOOLEAN:
            value = str(value).lower() in ("true", "1")
        elif kind in _PVCAM_FLOAT_TYPES:
            value = float(value)
        elif kind in _PVCAM_NUMBER_TYPES:
            value = int(float(value))
        # PyVCAM keeps these two itself and passes them at each acquisition start
        if name == "EXPOSURE_MODE":
            self.cam.exp_mode = value
        elif name == "EXPOSE_OUT_MODE":
            self.cam.exp_out_mode = value
        else:
            self.cam.set_param(pid, value)

    def reset_parameters(self):
        """
        Set every adjustable parameter back to its value at connection (the camera default),
        except exposure, binning and ROI (main panel). Returns (names changed, errors).
        """
        parameters = {p["name"]: p for p in self.get_parameters()
                      if p["writable"] and "default" in p and p["name"] not in _MAIN_PANEL_PARAMETERS}
        readout = [n for n in ("READOUT_PORT", "SPDTAB_INDEX", "GAIN_INDEX") if n in parameters]
        changed, errors = [], []
        for name in readout + [n for n in parameters if n not in readout]:
            p = parameters[name]
            # port, speed and gain are always set, in this order (each one may change the next)
            if name not in readout and str(p["value"]) == str(p["default"]):
                continue
            try:
                self.set_parameter(name, p["default"])
                if str(p["value"]) != str(p["default"]):
                    changed.append(name)
            except Exception as error:
                errors.append(f"{name}: {error}")
        try:
            self.set_despeckle(DESPECKLE)
        except Exception as error:
            errors.append(f"despeckle: {error}")
        return changed, errors

    # ---------- acquisition ----------

    def snap(self, exposure_time=None):
        """Acquire one image and return it as a 2D numpy array (uint16)."""
        exposure_time = self.exposure_time if exposure_time is None else exposure_time
        self.last_metadata = self.get_metadata(exposure_time, n_frames=1)
        return self._acquire(self.cam.get_frame, exp_time=exposure_time,
                             timeout_ms=exposure_time + self.timeout)

    def acquire_sequence(self, n_frames, exposure_time=None, progress=None, writer=None):
        """
        Acquire n_frames images and return them as a 3D numpy array (n_frames, y, x).
        progress: optional function progress(done, n_frames, frame) called after each frame;
        if it returns False the acquisition stops and the frames already taken are returned.
        writer: optional saving.SequenceWriter; the frames are then written to its file as
        they arrive instead of being kept in memory, and only the last frame is returned,
        with the camera frame number and time stamp of each frame (X_frames.csv).

        Continuous acquisition: the camera runs without stopping (no dead time between frames
        other than the readout) into a circular buffer of SEQUENCE_BUFFER_BYTES, read here
        frame after frame. If the PC falls behind by more than the buffer, frames are lost:
        detected with the camera frame counter, the acquisition then stops with an error
        (the frames before are kept).
        """
        exposure_time = self.exposure_time if exposure_time is None else exposure_time
        self.last_metadata = self.get_metadata(exposure_time, n_frames=n_frames)
        metadata_was_on = bool(_read(self.cam, "metadata_enabled"))
        timestamps = self._set_frame_metadata(True)
        self.last_metadata["frame_times"] = ("camera time stamps (start of exposure)" if timestamps
                                             else "PC clock only")
        buffer_frames = int(min(n_frames, max(16, SEQUENCE_BUFFER_BYTES // self._frame_bytes())))
        collect = FrameCollector(self.last_metadata, writer)
        # sensor cleared once at the start, not before each frame (2 clears = +25 ms per frame:
        # 21 instead of 80 frames/s at full frame); the clear mode before is restored at the end
        clear_mode_before = _read(self.cam, "clear_mode")
        if clear_mode_before is not None and clear_mode_before != const.CLEAR_PRE_SEQUENCE:
            self.cam.clear_mode = const.CLEAR_PRE_SEQUENCE
            self.last_metadata["clear_mode"] = const.CLEAR_PRE_SEQUENCE
        self._sequence_running = True  # no temperature reading meanwhile: it would stop it
        try:
            self._acquire(self.cam.start_live, exp_time=int(round(exposure_time)),
                          buffer_frame_count=buffer_frames, reset_frame_counter=True)
            previous = 0
            for _ in range(n_frames):
                frame, _, count = self._acquire(self.cam.poll_frame, oldestFrame=True,
                                                timeout_ms=int(exposure_time + self.timeout))
                if count != previous + 1:
                    raise RuntimeError(
                        f"Frames lost: the camera sent frame {count} after frame {previous} "
                        f"(the PC did not read the frames fast enough, buffer of {buffer_frames} "
                        f"frames). The {collect.count} frames before are saved.")
                previous = count
                info = {"camera_frame": count}
                header = (frame.get("meta_data") or {}).get("frame_header")
                if header:
                    info["camera_time_s"] = header["timestampBofPs"] * 1e-12
                if not collect(frame["pixel_data"], n_frames, progress, info):
                    break
        finally:
            self.cam.finish()
            self._sequence_running = False
            if timestamps and not metadata_was_on:
                self._set_frame_metadata(False)
            if clear_mode_before is not None and clear_mode_before != const.CLEAR_PRE_SEQUENCE:
                self.cam.clear_mode = clear_mode_before
        return collect.result()

    def _set_frame_metadata(self, on):
        """Turn PVCAM frame metadata (frame number, time stamps) on / off; False if unsupported."""
        try:
            if not self.cam.check_param(const.PARAM_METADATA_ENABLED):
                return False
            self.cam.metadata_enabled = on
            return True
        except Exception:
            return False

    def _frame_bytes(self):
        """Bytes of one frame with the current ROI and binning (uint16)."""
        width, height = self.roi[2:] if self.roi else self.sensor_size
        binning = (_read(self.cam, "binning") or (1, 1))[0]
        return max(1, (width // binning) * (height // binning) * 2)

    def start_live(self, exposure_time=None):
        """Start continuous acquisition; read the images with get_live_frame()."""
        exposure_time = self.exposure_time if exposure_time is None else exposure_time
        self.cam.start_live(exp_time=exposure_time)
        self.live = True

    def get_live_frame(self):
        """Return the latest image of the live acquisition as a 2D numpy array."""
        # newest frame (not the oldest of the buffer) so the display does not lag;
        # PyVCAM already returns a copy of the data
        frame, fps, frame_count = self._acquire(
            self.cam.poll_frame, timeout_ms=self.exposure_time + self.timeout, oldestFrame=False)
        return frame["pixel_data"]

    def _acquire(self, function, *args, **kwargs):
        """Run a PyVCAM acquisition call; on a frame timeout, add what to do."""
        try:
            return function(*args, **kwargs)
        except RuntimeError as error:
            if "timeout" not in str(error).lower():
                raise
            raise RuntimeError(f"{error}\n\nIf the camera gives no image at all (every snap "
                               f"times out), its link (USB or PCIe card) is stuck: close the "
                               f"program, switch the camera off for 10 s and on again, then "
                               f"reconnect.") from error

    def stop_live(self):
        """Stop the continuous acquisition."""
        if self.live:
            self.cam.finish()
            self.live = False

    # ---------- saving ----------

    def get_metadata(self, exposure_time=None, n_frames=1):
        """Return the current acquisition settings of the camera as a dictionary."""
        exposure_time = self.exposure_time if exposure_time is None else exposure_time
        return {
            "date": datetime.now().isoformat(timespec="seconds"),
            "camera": self.name,
            "serial_number": _read(self.cam, "serial_no"),
            "exposure_time_ms": exposure_time,
            "n_frames": n_frames,
            "sensor_size": list(self.sensor_size),
            "roi_x_y_width_height": list(self.roi) if self.roi else "full frame",
            "binning": list(_read(self.cam, "binning") or []),
            "bit_depth": _read(self.cam, "bit_depth"),
            "readout_port": _read(self.cam, "readout_port"),
            "speed_index": _read(self.cam, "speed"),
            "gain_index": _read(self.cam, "gain"),
            "exposure_mode": _read(self.cam, "exp_mode"),
            "clear_mode": _read(self.cam, "clear_mode"),
            "temperature_C": self.temperature,
            "advanced": self.get_advanced_metadata(),
        }

    def get_advanced_metadata(self):
        """All the camera parameters {name: value} and the post-processing, for the metadata."""
        try:
            advanced = {p["name"]: p["value"] for p in self.get_parameters()}
        except Exception as error:
            advanced = {"error": f"not readable: {error}"}
        advanced["post_processing"] = self.get_post_processing()
        return advanced

    def get_post_processing(self):
        """Image processing in the camera (despeckle, QuantView...): {feature: {setting: value}}."""
        try:
            return {feature: {setting: self.cam.get_post_processing_param(feature, setting)
                              for setting in settings}
                    for feature, settings in self.cam.post_processing_table.items()}
        except Exception as error:
            return f"(not readable: {error})"

    def set_despeckle(self, on):
        """Turn all the despeckle filters of the camera on or off."""
        for feature, settings in self.cam.post_processing_table.items():
            if feature.startswith("DESPECKLE") and "ENABLED" in settings:
                self.cam.set_post_processing_param(feature, "ENABLED", int(on))

    def save(self, images, folder, metadata=None, extra_metadata=None, pixel_bits=16, index=None,
             slm_mask=None):
        """
        Save a snap (2D array) or a sequence (3D array) in folder as acquisition X:
        X_0.tiff + X.json (see saving.py), the metadata also in the TIFF.

        folder: destination folder (created if needed).
        metadata: acquisition metadata; if None, the one of the last snap / sequence.
        extra_metadata: state of the other devices and anything else to keep: "devices",
            "slm" sections and other keys (see saving.organize_metadata).
        pixel_bits: 16 (as acquired) or 8 (1 byte per pixel: see saving.to_saved_pixels).
        index: acquisition number X; None = the next free one in folder.
        slm_mask: (pattern, name) of the SLM mask shown when the images were taken, or None.
        Returns the path of the TIFF file.
        """
        if metadata is None:
            metadata = self.last_metadata or self.get_metadata()
        return save_images(images, folder, metadata, pixel_bits, index, extra_metadata, slm_mask)

    def snap_and_save(self, folder, exposure_time=None, extra_metadata=None):
        """Acquire one image and save it. Returns (image, tif_path)."""
        image = self.snap(exposure_time)
        return image, self.save(image, folder, extra_metadata=extra_metadata)

    def acquire_sequence_and_save(self, n_frames, folder, exposure_time=None, extra_metadata=None,
                                  pixel_bits=16):
        """
        Acquire n_frames images as the next acquisition X of folder (X_Y.tiff files written as
        they arrive, X_frames.csv, X.json). pixel_bits: 16 or 8. Returns the first TIFF path.
        """
        with SequenceWriter(folder, extra_metadata, pixel_bits=pixel_bits) as writer:
            self.acquire_sequence(n_frames, exposure_time, writer=writer)
        return writer.tif_path

    # ---------- closing ----------

    def close(self):
        """Stop any acquisition, close the camera and PVCAM."""
        if self.cam is not None:
            self.stop_live()
            if self.cam.is_open:
                self.cam.close()
            self.cam = None
            _uninit_pvcam()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()


_PVCAM_FLOAT_TYPES = (const.TYPE_FLT32, const.TYPE_FLT64)
_PVCAM_NUMBER_TYPES = _PVCAM_FLOAT_TYPES + (
    const.TYPE_INT8, const.TYPE_INT16, const.TYPE_INT32, const.TYPE_INT64,
    const.TYPE_UNS8, const.TYPE_UNS16, const.TYPE_UNS32, const.TYPE_UNS64)
_PVCAM_TEXT_MAX = 32  # size of PyVCAM's text buffer (MAX_PP_NAME_LEN, with the final zero)
# not set by "Reset to defaults" of the advanced settings: set from the main panel, or
# post-processing selectors (feature / setting index + value: set alone, they would write
# a value into another setting; the despeckle filters are set by set_despeckle instead)
_PP_SELECTORS = {"PP_INDEX", "PP_PARAM_INDEX", "PP_PARAM"}  # no default either
_MAIN_PANEL_PARAMETERS = {"BINNING_SER", "BINNING_PAR", "EXP_TIME", "EXP_RES", "EXP_RES_INDEX",
                          "EXPOSURE_TIME"} | _PP_SELECTORS

# defaults of the parameters the camera keeps after being closed (until switched off):
# not the value read at connection but the normal one
_FIXED_DEFAULTS = {"TEMP_SETPOINT": -1500}  # -15 °C (found at -30 °C on 2026-10-29)

# short description of each PVCAM parameter (advanced settings window)
_PVCAM_DESCRIPTIONS = {
    "ACCUM_CAPABLE": "The camera can accumulate (add) frames on the sensor.",
    "ACTUAL_GAIN": "Actual gain of the current readout mode, in e-/ADU.",
    "ADC_OFFSET": "Offset (bias) added by the analog-to-digital converter, in ADU: the dark level.",
    "BINNING_PAR": "Binning along the rows (y). Use the binning of the main panel.",
    "BINNING_SER": "Binning along the columns (x). Use the binning of the main panel.",
    "BIT_DEPTH": "Bits per pixel of the camera readout (12 = Full well/Balanced/Sensitivity, "
                 "16 = HDR).",
    "BIT_DEPTH_HOST": "Bits per pixel of the images given to the PC.",
    "BOF_EOF_CLR": "Reset the begin/end-of-frame counter.",
    "BOF_EOF_COUNT": "Number of begin/end-of-frame events counted.",
    "BOF_EOF_ENABLE": "Count begin-of-frame / end-of-frame events.",
    "CAMERA_PART_NUMBER": "Part number of the camera.",
    "CAM_FW_VERSION": "Firmware version of the camera.",
    "CAM_INTERFACE_MODE": "Link mode: Imaging (normal) or Control only.",
    "CAM_INTERFACE_TYPE": "Link to the PC: USB 2.0, USB 3.0 or PCIe card.",
    "CAM_SYSTEMS_INFO": "Information on the camera systems (text too long for PyVCAM).",
    "CENTROIDS_BG_COUNT": "Centroids (PrimeLocate): frames used for the background.",
    "CENTROIDS_COUNT": "Centroids (PrimeLocate): number of spots sent per frame.",
    "CENTROIDS_ENABLED": "Centroids (PrimeLocate): send only small regions around bright spots "
                         "instead of full frames. Keep off.",
    "CENTROIDS_MODE": "Centroids (PrimeLocate): locate or track mode.",
    "CENTROIDS_RADIUS": "Centroids (PrimeLocate): radius of the regions sent, in pixels.",
    "CENTROIDS_THRESHOLD": "Centroids (PrimeLocate): detection threshold.",
    "CHIP_NAME": "Name of the sensor.",
    "CIRC_BUFFER": "The camera supports continuous acquisition in a circular buffer.",
    "CLEARING_TIME": "Time to clear the sensor once (readable during an acquisition only).",
    "CLEAR_CYCLES": "Number of clears of the sensor each time it is cleared.",
    "CLEAR_MODE": "When the sensor is cleared. Pre-Exposure: before each frame (slower, +~25 ms "
                  "per frame here). Pre-Sequence: once at the start (used during the sequences).",
    "COLOR_MODE": "Colour mask of the sensor (none = monochrome).",
    "COOLING_MODE": "Type of cooling: air or water, cryogenic, none.",
    "DD_INFO": "Information on the driver (text too long for PyVCAM).",
    "DD_INFO_LENGTH": "Length of the driver information text.",
    "DD_RETRIES": "Number of retries of the driver on a communication error.",
    "DD_TIMEOUT": "Time-out of the driver, in ms.",
    "DD_VERSION": "Version of the device driver.",
    "EXPOSE_OUT_MODE": "When the Expose Out signal (trigger output) is high: First Row, All Rows "
                       "(all rows exposing together), Any Row, Rolling Shutter.",
    "EXPOSURE_MODE": "How exposures start: Internal Trigger (camera timing, normal), Edge Trigger "
                     "(each frame on an external pulse), Trigger first (the sequence starts on a "
                     "pulse), Trigger Level (exposure as long as the input is high).",
    "EXPOSURE_TIME": "Exposure time of the acquisition being set up. Use the main panel.",
    "EXP_RES": "Unit of the exposure time (ms, us). Use the main panel.",
    "EXP_RES_INDEX": "Index of the exposure time unit. Use the main panel.",
    "EXP_TIME": "Exposure time of variable timed mode. Use the main panel.",
    "FAN_SPEED_SETPOINT": "Speed of the camera fan. Lower = less vibration but less cooling; "
                          "Off only with liquid cooling.",
    "FLASH_DWNLD_CAPABLE": "The firmware can be updated.",
    "FRAME_BUFFER_SIZE": "Size of the frame buffer (readable during an acquisition only).",
    "FRAME_CAPABLE": "The sensor is a frame-transfer sensor.",
    "FRAME_DELIVERY_MODE": "How frames are sent to the PC.",
    "FRAME_FLIP": "Mirror the image in the camera (x, y or both).",
    "FRAME_ROTATE": "Rotate the image in the camera.",
    "FWELL_CAPACITY": "Full-well capacity of a pixel, in electrons.",
    "GAIN_INDEX": "Gain of the readout (1 = Full well, 2 = Balanced, 3 = Sensitivity at speed 0; "
                  "1 = HDR at speed 1). Also in the main panel.",
    "GAIN_MULT_ENABLE": "EM gain on/off (EMCCD cameras only).",
    "GAIN_MULT_FACTOR": "EM gain (EMCCD cameras only).",
    "GAIN_NAME": "Name of the current gain.",
    "HEAD_SER_NUM_ALPHA": "Serial number of the camera.",
    "HOST_FRAME_DECOMPRESSION_ENABLED": "The PC decompresses frames sent compressed by the camera.",
    "HOST_FRAME_FLIP": "Mirror the image on the PC.",
    "HOST_FRAME_ROTATE": "Rotate the image on the PC.",
    "HOST_FRAME_SUMMING_COUNT": "Frames added together on the PC when summing is on.",
    "HOST_FRAME_SUMMING_ENABLED": "Add consecutive frames together on the PC (one image per "
                                  "Summing count frames).",
    "HOST_FRAME_SUMMING_FORMAT": "Pixel format of summed frames (16 or 32 bit).",
    "IMAGE_COMPRESSION": "Compression of the frames sent by the camera.",
    "IMAGE_COMPRESSION_HOST": "Compression of the frames given to the program.",
    "IMAGE_FORMAT": "Pixel format of the camera.",
    "IMAGE_FORMAT_HOST": "Pixel format of the images given to the PC.",
    "IO_ADDR": "Address of the selected input/output line.",
    "IO_BITDEPTH": "Bit depth of the selected input/output line.",
    "IO_DIRECTION": "Direction of the selected input/output line.",
    "IO_STATE": "State of the selected input/output line.",
    "IO_TYPE": "Type of the selected input/output line.",
    "LAST_MUXED_SIGNAL": "Last trigger output signal of the multiplexer.",
    "METADATA_ENABLED": "Frame metadata (frame number, time stamps). Turned on by Bumblebee "
                        "during the sequences.",
    "METADATA_RESET_TIMESTAMP": "Reset the time stamp counter of the metadata.",
    "MPP_CAPABLE": "The sensor supports MPP mode (CCD).",
    "NAME_LEN": "Length of a parameter name.",
    "PAR_SIZE": "Number of rows of the sensor.",
    "PCI_FW_VERSION": "Firmware version of the PCIe card.",
    "PIX_PAR_DIST": "Distance between pixel centres along y, in nm.",
    "PIX_PAR_SIZE": "Pixel size along y, in nm.",
    "PIX_SER_DIST": "Distance between pixel centres along x, in nm.",
    "PIX_SER_SIZE": "Pixel size along x, in nm.",
    "PIX_TIME": "Time to read one pixel (per column), in ns: set by the readout speed.",
    "PMODE": "Parallel clocking mode of the sensor (Normal).",
    "POSTMASK": "Masked rows after the image (CCD).",
    "POSTSCAN": "Extra pixels read after each row.",
    "POST_TRIGGER_DELAY": "Delay after the trigger output (readable during an acquisition only).",
    "PP_FEAT_ID": "Post-processing: identifier of the selected feature.",
    "PP_FEAT_NAME": "Post-processing: name of the selected feature.",
    "PP_INDEX": "Post-processing: index of the selected feature (despeckle filters, QuantView). "
                "The despeckle filters are turned off at each connection (DESPECKLE in camera.py).",
    "PP_PARAM": "Post-processing: value of the selected setting.",
    "PP_PARAM_ID": "Post-processing: identifier of the selected setting.",
    "PP_PARAM_INDEX": "Post-processing: index of the selected setting of the feature.",
    "PP_PARAM_NAME": "Post-processing: name of the selected setting.",
    "PREAMP_DELAY": "Delay before the preamplifier (CCD).",
    "PREAMP_OFF_CONTROL": "Preamplifier off during exposure (CCD).",
    "PREMASK": "Masked rows before the image (CCD).",
    "PRESCAN": "Extra pixels read before each row.",
    "PRE_TRIGGER_DELAY": "Delay before the trigger output (readable during an acquisition only).",
    "PRODUCT_NAME": "Product name of the camera.",
    "READOUT_PORT": "Readout port (one, CMOS, on the Prime 95B).",
    "READOUT_TIME": "Time to read one full frame, in us. Sets the maximum frame rate.",
    "READ_NOISE": "Read noise of the current readout mode.",
    "ROI": "Region of interest. Use the ROI of the main panel.",
    "ROI_COUNT": "Number of regions of interest supported.",
    "SCAN_DIRECTION": "Programmable scan: direction of the rolling readout.",
    "SCAN_DIRECTION_RESET": "Programmable scan: restart the direction at each frame.",
    "SCAN_LINE_DELAY": "Programmable scan: delay between two rows.",
    "SCAN_LINE_TIME": "Programmable scan: time to read one row, in ns.",
    "SCAN_MODE": "Readout scan: Auto (normal), Line Delay or Scan Width (light-sheet).",
    "SCAN_WIDTH": "Programmable scan: number of rows exposed together.",
    "SER_SIZE": "Number of columns of the sensor.",
    "SHTR_CLOSE_DELAY": "Delay to close a shutter driven by the camera (none here).",
    "SHTR_OPEN_DELAY": "Delay to open a shutter driven by the camera (none here).",
    "SHTR_OPEN_MODE": "When the camera opens its shutter output (no shutter connected here).",
    "SHTR_STATUS": "State of the camera shutter output (Fault: no shutter connected).",
    "SMART_STREAM_DLY_PARAMS": "SMART streaming: delays.",
    "SMART_STREAM_EXP_PARAMS": "SMART streaming: list of exposure times cycled frame after frame.",
    "SMART_STREAM_MODE": "SMART streaming mode.",
    "SMART_STREAM_MODE_ENABLED": "SMART streaming: a different exposure for each frame. Keep off.",
    "SPDTAB_INDEX": "Readout speed (0 = 12 bit, fast; 1 = 16 bit HDR). Also in the main panel.",
    "SPDTAB_NAME": "Name of the current readout speed.",
    "SUMMING_WELL": "The sensor has a summing well (CCD).",
    "SYSTEM_NAME": "Name of the camera system.",
    "TEMP": "Sensor temperature, in hundredths of a degree C.",
    "TEMP_SETPOINT": "Target sensor temperature, in hundredths of a degree C (-1500 = -15 C, the "
                     "normal value). Kept by the camera after closing, until it is switched off.",
    "TRIGTAB_SIGNAL": "Signal sent on the trigger output.",
    "VENDOR_NAME": "Manufacturer.",
}


def _read(cam, attribute):
    """Read a camera parameter, or None if the camera does not support it."""
    try:
        return getattr(cam, attribute)
    except Exception:
        return None
