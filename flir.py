"""
Control of the FLIR (Teledyne) Blackfly S BFS-U3-51S5M camera (USB 3, 2448 x 2048, mono).

The camera is driven through Micro-Manager (SpinnakerCamera device adapter, which uses the
FLIR Spinnaker SDK installed on the PC) and pymmcore, as for the microscope:
    1. Spinnaker SDK installed (2.3 here) and Micro-Manager 2.0 in MM_PATH
    2. pymmcore==10.1.1.70.6 (setup_env)
Saving images needs tifffile (setup_env).

Close SpinView / Micro-Manager before connecting.
Same methods as the Camera class of camera.py, so all the cameras are used the same way.
"""

import os
import time
from datetime import datetime

import pymmcore

from saving import FrameCollector, SequenceWriter, save_images

MM_PATH = r"C:\Program Files\Micro-Manager-2.0"
DEVICE = "Blackfly"  # Micro-Manager label of the camera
EXPOSURE_TIME = 20   # ms
TIMEOUT = 5000       # ms, maximum wait for a frame (on top of the exposure time)

# Settings applied at connection, for measurements: fixed gain, no gamma, 12-bit data
# (Mono16 holds the 12-bit values of the ADC, scaled to 16 bit: multiples of 16).
DEFAULT_SETTINGS = [
    ("Exposure Auto", "Off"),
    ("Gain Auto", "Off"),
    ("Gain", "0"),
    ("Gamma Enabled", "0"),
    ("Pixel Format", "Mono16"),  # before the ADC: 12 bit is refused while in Mono8
    ("ADC Bit Depth", "Bit12"),
]


class Flir:
    """
    FLIR Blackfly S camera.

    Usage:
        with Flir() as cam:
            cam.set_exposure(50)
            image = cam.snap()
    """

    def __init__(self, name=None, exposure_time=EXPOSURE_TIME, timeout=TIMEOUT,
                 mm_path=MM_PATH):
        """
        name: Micro-Manager name of the camera (see Flir.list_cameras()); if None, the first one.
        exposure_time: exposure time in ms.
        """
        self.name = name
        self.exposure_time = exposure_time
        self.timeout = timeout
        self.mm_path = mm_path
        self.core = None  # pymmcore.CMMCore
        self.live = False
        self.roi = None   # (x, y, width, height) in full-sensor pixels, None = full frame
        self.binning = 1
        self.sensor_size = None  # (width, height), read at connection
        self.serial_number = None
        self.last_metadata = None  # metadata of the last snap / sequence

    def _make_core(self):
        if self.mm_path not in os.environ["PATH"]:
            os.environ["PATH"] = self.mm_path + os.pathsep + os.environ["PATH"]
        core = pymmcore.CMMCore()
        core.setDeviceAdapterSearchPaths([self.mm_path])
        return core

    @classmethod
    def list_cameras(cls):
        """Return the names of the Spinnaker cameras connected to the PC."""
        return list(cls()._make_core().getAvailableDevices("SpinnakerCamera"))

    def connect(self):
        """Find the camera, open it and apply DEFAULT_SETTINGS."""
        core = self._make_core()
        names = list(core.getAvailableDevices("SpinnakerCamera"))
        if not names:
            raise RuntimeError("No FLIR camera found. Check the USB 3 cable and that SpinView "
                               "or Micro-Manager is closed.")
        name = self.name or names[0]
        try:
            core.loadDevice(DEVICE, "SpinnakerCamera", name)
            core.initializeDevice(DEVICE)
            core.setCameraDevice(DEVICE)
            try:
                self._apply_default_settings(core)
            except RuntimeError:
                # camera left streaming by a program that was killed during an acquisition:
                # its settings are locked until the acquisition is stopped
                core.startContinuousSequenceAcquisition(0)
                time.sleep(0.2)
                core.stopSequenceAcquisition()
                self._apply_default_settings(core)
            core.setExposure(self.exposure_time)
        except Exception:
            core.unloadAllDevices()
            raise
        self.core = core
        self.name = name
        self.serial_number = core.getProperty(DEVICE, "Serial Number")
        self.sensor_size = (core.getImageWidth(), core.getImageHeight())
        print(f"Connected to camera {self.name} {self.serial_number} "
              f"(sensor {self.sensor_size}, {core.getImageBitDepth()} bits).")

    @staticmethod
    def _apply_default_settings(core):
        for prop, value in DEFAULT_SETTINGS:
            core.setProperty(DEVICE, prop, value)

    def is_connected(self):
        return self.core is not None

    # ---------- settings ----------

    @property
    def temperature(self):
        """Not available through Micro-Manager for this camera: None."""
        return None

    def set_exposure(self, exposure_time):
        """Set the exposure time (ms) used by snap, acquire_sequence and start_live."""
        self.exposure_time = exposure_time
        self.core.setExposure(exposure_time)

    def set_gain(self, gain):
        """Set the gain (dB, 0 to ~48)."""
        self.core.setProperty(DEVICE, "Gain", gain)

    def get_gain(self):
        return float(self.core.getProperty(DEVICE, "Gain"))

    def set_roi(self, x, y, width, height):
        """
        Acquire only a region of interest: top-left corner (x, y), size width x height,
        in full-sensor pixels. Raises ValueError if the ROI does not fit on the sensor.
        The camera rounds the size to its allowed steps (e.g. 400 x 300 -> 404 x 304): the
        ROI actually used is saved in the metadata (roi_camera).
        """
        sensor_width, sensor_height = self.sensor_size
        if x < 0 or y < 0 or width < 1 or height < 1:
            raise ValueError(f"Invalid ROI {(x, y, width, height)}: x, y must be >= 0 "
                             f"and width, height >= 1.")
        if x + width > sensor_width or y + height > sensor_height:
            raise ValueError(f"ROI {(x, y, width, height)} goes beyond the sensor "
                             f"({sensor_width} x {sensor_height}).")
        self.roi = (x, y, width, height)
        self._apply_roi()

    def reset_roi(self):
        """Go back to full frame, binning 1x1."""
        self.roi = None
        self.set_binning(1)

    def set_binning(self, binning):
        """Set the binning, same in x and y (1 or 2). The ROI is kept."""
        self.core.clearROI()  # the camera refuses a binning change while an ROI is set
        self.core.setProperty(DEVICE, "Binning", str(binning))
        self.binning = binning
        self._apply_roi()

    def _apply_roi(self):
        """Micro-Manager ROIs are in binned pixels: convert from full-sensor pixels."""
        self.core.clearROI()
        if self.roi is not None:
            b = self.binning
            x, y, width, height = self.roi
            self.core.setROI(x // b, y // b, width // b, height // b)

    # ---------- all parameters (advanced settings) ----------

    def get_parameters(self):
        """
        All the properties of the camera exposed by the Micro-Manager Spinnaker adapter
        (a subset of the camera's GenICam features), as a list of dictionaries:
        name, value, writable, kind ("enum", "int", "float", "text"), options (enum),
        min / max (numbers with limits), live (True).
        Exposure, ROI and binning are better changed with set_exposure / set_roi / set_binning.
        """
        core, parameters = self.core, []
        kinds = {pymmcore.Float: "float", pymmcore.Integer: "int", pymmcore.String: "text"}
        for name in core.getDevicePropertyNames(DEVICE):
            entry = {"name": name, "value": core.getProperty(DEVICE, name),
                     "writable": not core.isPropertyReadOnly(DEVICE, name), "live": True,
                     "kind": kinds.get(core.getPropertyType(DEVICE, name), "text")}
            options = list(core.getAllowedPropertyValues(DEVICE, name))
            if options:
                entry.update(kind="enum", options=options)
            elif core.hasPropertyLimits(DEVICE, name):
                entry.update(min=core.getPropertyLowerLimit(DEVICE, name),
                             max=core.getPropertyUpperLimit(DEVICE, name))
            parameters.append(entry)
        return parameters

    def set_parameter(self, name, value):
        """Set a camera property by name (as given by get_parameters)."""
        self.core.setProperty(DEVICE, name, str(value))

    # ---------- acquisition ----------

    def snap(self, exposure_time=None):
        """Acquire one image and return it as a 2D numpy array (uint16 in 12-bit mode)."""
        exposure_time = self._apply_exposure(exposure_time)
        self.last_metadata = self.get_metadata(exposure_time, n_frames=1)
        self.core.snapImage()
        return self.core.getImage()

    def acquire_sequence(self, n_frames, exposure_time=None, progress=None, writer=None):
        """
        Acquire n_frames images and return them as a 3D numpy array (n_frames, y, x).
        progress: optional function progress(done, n_frames, frame) called after each frame;
        if it returns False the acquisition stops and the frames already taken are returned.
        writer: optional saving.SequenceWriter; the frames are then written to its file as
        they arrive instead of being kept in memory, and only the last frame is returned.
        """
        exposure_time = self._apply_exposure(exposure_time)
        self.last_metadata = self.get_metadata(exposure_time, n_frames=n_frames)
        collect = FrameCollector(self.last_metadata, writer)
        core = self.core
        # stopOnOverflow: an error instead of silently lost frames if they are read too slowly
        core.startSequenceAcquisition(n_frames, 0, True)
        try:
            while collect.count < n_frames:
                self._wait_for_image(exposure_time)
                if not collect(core.popNextImage(), n_frames, progress):
                    break
        finally:
            core.stopSequenceAcquisition()
        return collect.result()

    def start_live(self, exposure_time=None):
        """Start continuous acquisition; read the images with get_live_frame()."""
        self._apply_exposure(exposure_time)
        self.core.startContinuousSequenceAcquisition(0)
        self.live = True

    def get_live_frame(self, wait=True):
        """
        Return the latest image of the live acquisition as a 2D numpy array.
        wait=False: return None at once if no new image has arrived (used by the GUI, so
        that the window is not blocked between two frames).
        """
        if not wait and self.core.getRemainingImageCount() == 0:
            return None
        self._wait_for_image(self.exposure_time)
        image = self.core.getLastImage()
        self.core.clearCircularBuffer()  # only the newest frame is kept
        return image

    def stop_live(self):
        """Stop the continuous acquisition."""
        if self.live:
            self.core.stopSequenceAcquisition()
            self.live = False

    def _apply_exposure(self, exposure_time):
        exposure_time = self.exposure_time if exposure_time is None else exposure_time
        self.core.setExposure(exposure_time)
        return exposure_time

    def _wait_for_image(self, exposure_time):
        end = time.time() + (exposure_time + self.timeout) / 1000
        while self.core.getRemainingImageCount() == 0:
            if self.core.isBufferOverflowed():
                raise RuntimeError("Frames lost: they arrived faster than they were saved "
                                   "(the circular buffer of the camera overflowed). Use a "
                                   "faster disk, a smaller ROI or a longer exposure.")
            if time.time() > end:
                raise RuntimeError("Frame timeout: no image from the FLIR camera.")
            time.sleep(0.002)

    # ---------- saving ----------

    def get_metadata(self, exposure_time=None, n_frames=1):
        """Return the current acquisition settings of the camera as a dictionary."""
        exposure_time = self.exposure_time if exposure_time is None else exposure_time
        prop = lambda name: self.core.getProperty(DEVICE, name)
        return {
            "date": datetime.now().isoformat(timespec="seconds"),
            "camera": self.name,
            "serial_number": self.serial_number,
            "exposure_time_ms": exposure_time,
            "exposure_time_camera_ms": self.core.getExposure(),
            "n_frames": n_frames,
            "sensor_size": list(self.sensor_size),
            "roi_x_y_width_height": list(self.roi) if self.roi else "full frame",
            "roi_camera": list(self.core.getROI()),  # x, y, w, h in binned pixels, after rounding
            "binning": [self.binning, self.binning],
            # range of the values: in Mono16 the 10 / 12-bit values are scaled to 16 bit
            # (multiples of 16 in 12 bit)
            "bit_depth": 8 if prop("Pixel Format") == "Mono8" else 16,
            "pixel_format": prop("Pixel Format"),
            "adc_bit_depth": prop("ADC Bit Depth"),
            "gain_dB": float(prop("Gain")),
            "gain_auto": prop("Gain Auto"),
            "gamma_enabled": prop("Gamma Enabled"),
            "black_level": float(prop("Black Level")),
            "trigger_mode": prop("Trigger Mode"),
            "advanced": self.get_advanced_metadata(),
        }

    def get_advanced_metadata(self):
        """All the camera parameters {name: value}, for the metadata."""
        try:
            return {p["name"]: p["value"] for p in self.get_parameters()}
        except Exception as error:
            return {"error": f"not readable: {error}"}

    def save(self, images, folder, metadata=None, extra_metadata=None, pixel_bits=16, index=None,
             slm_mask=None):
        """
        Save a snap (2D array) or a sequence (3D array) in folder as acquisition X:
        X_0.tiff + X.json (see saving.py), the metadata also in the TIFF.
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
        """Stop any acquisition and release the camera."""
        if self.core is not None:
            self.stop_live()
            self.core.unloadAllDevices()
            self.core = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()


if __name__ == "__main__":
    # quick check: list the cameras, connect, snap one image
    print("FLIR cameras:", Flir.list_cameras())
    with Flir() as cam:
        image = cam.snap()
        print(f"snap {image.shape} {image.dtype} min {image.min()} max {image.max()} "
              f"mean {image.mean():.1f}")
