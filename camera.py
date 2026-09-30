"""
Control of the Teledyne Photometrics Prime 95B camera.

The camera is driven through PVCAM (Teledyne driver/SDK) and its Python wrapper PyVCAM:
    1. Install the PVCAM SDK from the Teledyne Vision Solutions website.
    2. pip install PyVCAM
Saving images needs tifffile: pip install tifffile

Close the camera in any other software (PVCamTest, Micro-Manager...) before connecting.
"""

import json
import os
from datetime import datetime

import numpy as np
import tifffile
from pyvcam import pvc
from pyvcam.camera import Camera as PVCamera

EXPOSURE_TIME = 20  # ms (default exposure resolution of PVCAM is milliseconds)
TIMEOUT = 5000      # ms, maximum wait for a frame


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

    @staticmethod
    def list_cameras():
        """Return the names of the PVCAM cameras connected to the PC."""
        pvc.init_pvcam()
        names = PVCamera.get_available_camera_names()
        pvc.uninit_pvcam()
        return names

    def connect(self):
        """Initialise PVCAM, find the camera and open it."""
        pvc.init_pvcam()
        try:
            if self.name is None:
                self.cam = next(PVCamera.detect_camera())
            else:
                self.cam = next(PVCamera.select_camera(self.name))
        except StopIteration:
            pvc.uninit_pvcam()
            raise RuntimeError("No PVCAM camera found. Check USB/PCIe cable, power and driver.")

        self.cam.open()
        self.cam.exp_time = self.exposure_time
        self.name = self.cam.name
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
        """Current sensor temperature (°C)."""
        return self.cam.temp

    def set_exposure(self, exposure_time):
        """Set the exposure time (ms) used by snap, acquire_sequence and start_live."""
        self.exposure_time = exposure_time
        self.cam.exp_time = exposure_time

    def set_roi(self, x, y, width, height):
        """Acquire only a region of interest: top-left corner (x, y), size width x height."""
        self.cam.reset_rois()
        self.cam.set_roi(x, y, width, height)
        self.roi = (x, y, width, height)

    def reset_roi(self):
        """Go back to full frame, binning 1x1."""
        self.cam.reset_rois()
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

    # ---------- acquisition ----------

    def snap(self, exposure_time=None):
        """Acquire one image and return it as a 2D numpy array (uint16)."""
        exposure_time = self.exposure_time if exposure_time is None else exposure_time
        self.last_metadata = self.get_metadata(exposure_time, n_frames=1)
        return self.cam.get_frame(exp_time=exposure_time, timeout_ms=self.timeout)

    def acquire_sequence(self, n_frames, exposure_time=None):
        """Acquire n_frames images and return them as a 3D numpy array (n_frames, y, x)."""
        exposure_time = self.exposure_time if exposure_time is None else exposure_time
        self.last_metadata = self.get_metadata(exposure_time, n_frames=n_frames)
        return self.cam.get_sequence(n_frames, exp_time=exposure_time, timeout_ms=self.timeout)

    def start_live(self, exposure_time=None):
        """Start continuous acquisition; read the images with get_live_frame()."""
        exposure_time = self.exposure_time if exposure_time is None else exposure_time
        self.cam.start_live(exp_time=exposure_time)
        self.live = True

    def get_live_frame(self):
        """Return the latest image of the live acquisition as a 2D numpy array."""
        frame, fps, frame_count = self.cam.poll_frame(timeout_ms=self.timeout)
        return np.copy(frame["pixel_data"])

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
            "temperature_C": _read(self.cam, "temp"),
        }

    def save(self, images, folder, name="image", metadata=None, extra_metadata=None):
        """
        Save a snap (2D array) or a sequence (3D array) as a TIFF file in folder,
        with its metadata in the TIFF and in a .json file next to it.

        folder: destination folder (created if needed).
        name: base file name; date and time are added so nothing is overwritten.
        metadata: acquisition metadata; if None, the one of the last snap / sequence.
        extra_metadata: dictionary of anything else to keep (SLM mask, sample, notes...).
        Returns the path of the TIFF file.
        """
        os.makedirs(folder, exist_ok=True)
        if metadata is None:
            metadata = self.last_metadata or self.get_metadata()
        metadata = dict(metadata)
        if extra_metadata:
            metadata.update(extra_metadata)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # down to ms
        base = os.path.join(folder, f"{name}_{stamp}")
        tif_path = base + ".tif"
        tifffile.imwrite(tif_path, np.asarray(images),
                         description=json.dumps(metadata, default=str))
        with open(base + ".json", "w") as f:
            json.dump(metadata, f, indent=4, default=str)

        print(f"Saved {tif_path}")
        return tif_path

    def snap_and_save(self, folder, name="image", exposure_time=None, extra_metadata=None):
        """Acquire one image and save it. Returns (image, tif_path)."""
        image = self.snap(exposure_time)
        return image, self.save(image, folder, name, extra_metadata=extra_metadata)

    def acquire_sequence_and_save(self, n_frames, folder, name="sequence",
                                  exposure_time=None, extra_metadata=None):
        """Acquire n_frames images and save them in one TIFF. Returns (stack, tif_path)."""
        stack = self.acquire_sequence(n_frames, exposure_time)
        return stack, self.save(stack, folder, name, extra_metadata=extra_metadata)

    # ---------- closing ----------

    def close(self):
        """Stop any acquisition, close the camera and PVCAM."""
        if self.cam is not None:
            self.stop_live()
            if self.cam.is_open:
                self.cam.close()
            self.cam = None
            pvc.uninit_pvcam()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()


def _read(cam, attribute):
    """Read a camera parameter, or None if the camera does not support it."""
    try:
        return getattr(cam, attribute)
    except Exception:
        return None
