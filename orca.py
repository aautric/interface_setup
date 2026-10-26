"""
Control of the Hamamatsu ORCA-Flash4.0 V2 camera (C11440-22CU, USB 3).

The camera is driven through DCAM-API (Hamamatsu driver) and the DCAM part of pylablib:
    1. Install DCAM-API from the Hamamatsu website (with the module of the frame
       grabber or USB 3 link used by the camera).
    2. pip install pylablib
Saving images needs tifffile: pip install tifffile

Close the camera in any other software (HCImage, Micro-Manager...) before connecting.
Same methods as the Camera class of camera.py, so both cameras are used the same way.
"""

from datetime import datetime

import numpy as np
from pylablib.devices import DCAM

from saving import save_images

EXPOSURE_TIME = 20  # ms
TIMEOUT = 5000      # ms, maximum wait for a frame (on top of the exposure time)
LIVE_BUFFER = 100   # frames in the ring buffer of the live acquisition


class Orca:
    """
    Hamamatsu ORCA-Flash4.0 camera.

    Usage:
        with Orca() as cam:
            cam.set_exposure(50)
            image = cam.snap()
    """

    def __init__(self, index=0, exposure_time=EXPOSURE_TIME, timeout=TIMEOUT):
        """
        index: DCAM index of the camera (see Orca.list_cameras()).
        exposure_time: exposure time in ms.
        """
        self.index = index
        self.exposure_time = exposure_time
        self.timeout = timeout
        self.cam = None  # pylablib DCAMCamera object
        self.name = None
        self.serial_number = None
        self.live = False
        self.roi = None  # (x, y, width, height), None = full frame
        self.binning = 1
        self.last_metadata = None  # metadata of the last snap / sequence

    @staticmethod
    def list_cameras():
        """Return 'model serial' of each DCAM camera connected to the PC (not opened elsewhere)."""
        names = []
        for index in range(DCAM.get_cameras_number()):
            try:
                cam = DCAM.DCAMCamera(idx=index)
                info = cam.get_device_info()
                cam.close()
                names.append(f"{info.model} {info.serial_number}")
            except Exception as error:
                names.append(f"camera {index} (not available: {error})")
        return names

    def connect(self):
        """Find the camera and open it."""
        if DCAM.get_cameras_number() <= self.index:
            raise RuntimeError("No DCAM camera found. Check that the camera is on, its cable "
                               "(frame grabber / USB 3) and that HCImage is closed.")
        self.cam = DCAM.DCAMCamera(idx=self.index)
        info = self.cam.get_device_info()
        self.name = f"{info.model} {info.serial_number}"
        self.serial_number = info.serial_number
        self._apply_exposure(None)
        print(f"Connected to camera {self.name} (sensor {self.sensor_size}).")

    def is_connected(self):
        """Return True if the camera is open."""
        return self.cam is not None and self.cam.is_opened()

    # ---------- settings ----------

    @property
    def sensor_size(self):
        """(width, height) of the full sensor in pixels (2048 x 2048 for the Flash4.0)."""
        return tuple(self.cam.get_detector_size())

    @property
    def temperature(self):
        """Current sensor temperature (°C), None if the camera does not give it (Flash4.0 V2)."""
        return self.cam.get_attribute_value("sensor_temperature", error_on_missing=False)

    @property
    def cooler_status(self):
        """Sensor cooler status, e.g. 'READY' (given instead of the temperature by the Flash4.0 V2)."""
        return self.cam.get_attribute_value("sensor_cooler_status", enum_as_str=True,
                                            error_on_missing=False)

    def set_exposure(self, exposure_time):
        """Set the exposure time (ms) used by snap, acquire_sequence and start_live."""
        self.exposure_time = exposure_time
        self._apply_exposure(None)

    def _apply_exposure(self, exposure_time):
        """Send exposure_time (ms), or the default one if None, to the camera; return it."""
        exposure_time = self.exposure_time if exposure_time is None else exposure_time
        self.cam.set_exposure(exposure_time / 1000)  # pylablib uses seconds
        return exposure_time

    def set_roi(self, x, y, width, height):
        """
        Acquire only a region of interest: top-left corner (x, y), size width x height,
        in full-sensor pixels. Raises ValueError if the ROI does not fit on the sensor.
        The camera may round the ROI to its allowed steps (see get_metadata()).
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
        self.binning = 1
        self._apply_roi()

    def set_binning(self, binning):
        """Set the binning, same in x and y (1, 2 or 4 for the Flash4.0)."""
        self.binning = binning
        self._apply_roi()

    def _apply_roi(self):
        x, y, width, height = self.roi or (0, 0, *self.sensor_size)
        self.cam.set_roi(x, x + width, y, y + height, self.binning, self.binning)

    def set_readout_speed(self, speed):
        """'fast' (standard scan) or 'slow' (low noise scan)."""
        self.cam.set_readout_speed(speed)

    # ---------- acquisition ----------

    def snap(self, exposure_time=None):
        """Acquire one image and return it as a 2D numpy array (uint16)."""
        exposure_time = self._apply_exposure(exposure_time)
        self.last_metadata = self.get_metadata(exposure_time, n_frames=1)
        return self.cam.snap(timeout=self._timeout_s(exposure_time))

    def acquire_sequence(self, n_frames, exposure_time=None):
        """Acquire n_frames images and return them as a 3D numpy array (n_frames, y, x)."""
        exposure_time = self._apply_exposure(exposure_time)
        self.last_metadata = self.get_metadata(exposure_time, n_frames=n_frames)
        frames = self.cam.grab(n_frames, frame_timeout=self._timeout_s(exposure_time))
        if len(frames) != n_frames:
            raise RuntimeError(f"Only {len(frames)} of {n_frames} frames received.")
        return np.array(frames)

    def start_live(self, exposure_time=None):
        """Start continuous acquisition; read the images with get_live_frame()."""
        self._apply_exposure(exposure_time)
        self.cam.start_acquisition(mode="sequence", nframes=LIVE_BUFFER)
        self.live = True

    def get_live_frame(self):
        """Return the latest image of the live acquisition as a 2D numpy array."""
        self.cam.wait_for_frame(timeout=self._timeout_s(self.exposure_time))
        return self.cam.read_newest_image()

    def stop_live(self):
        """Stop the continuous acquisition."""
        if self.live:
            self.cam.stop_acquisition()
            self.live = False

    def _timeout_s(self, exposure_time):
        return (exposure_time + self.timeout) / 1000

    # ---------- saving ----------

    def get_metadata(self, exposure_time=None, n_frames=1):
        """Return the current acquisition settings of the camera as a dictionary."""
        exposure_time = self.exposure_time if exposure_time is None else exposure_time

        def attribute(name):
            return self.cam.get_attribute_value(name, enum_as_str=True, error_on_missing=False)

        return {
            "date": datetime.now().isoformat(timespec="seconds"),
            "camera": self.name,
            "serial_number": self.serial_number,
            "exposure_time_ms": exposure_time,
            "exposure_time_camera_ms": self.cam.get_exposure() * 1000,  # after camera rounding
            "n_frames": n_frames,
            "sensor_size": list(self.sensor_size),
            "roi_x_y_width_height": list(self.roi) if self.roi else "full frame",
            "roi_camera": list(self.cam.get_roi()),  # (hstart, hend, vstart, vend, hbin, vbin)
            "binning": [self.binning, self.binning],
            "bit_depth": attribute("bit_per_channel"),
            "readout_speed": self.cam.get_readout_speed(),
            "sensor_mode": attribute("sensor_mode"),
            "trigger_source": attribute("trigger_source"),
            "temperature_C": attribute("sensor_temperature"),
            "cooler_status": attribute("sensor_cooler_status"),
        }

    def save(self, images, folder, name="image", metadata=None, extra_metadata=None):
        """
        Save a snap (2D array) or a sequence (3D array) as TIFF + .json metadata (see saving.py).
        metadata: acquisition metadata; if None, the one of the last snap / sequence.
        extra_metadata: dictionary of anything else to keep (SLM mask, microscope state...).
        Returns the path of the TIFF file.
        """
        if metadata is None:
            metadata = self.last_metadata or self.get_metadata()
        metadata = dict(metadata)
        if extra_metadata:
            metadata.update(extra_metadata)
        return save_images(images, folder, name, metadata)

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
        """Stop any acquisition and close the camera."""
        if self.cam is not None:
            self.stop_live()
            self.cam.close()
            self.cam = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()


if __name__ == "__main__":
    # quick check: list the cameras, connect, snap one image
    print("DCAM cameras:", Orca.list_cameras())
    with Orca() as cam:
        image = cam.snap()
        print(f"snap {image.shape} min {image.min()} max {image.max()} mean {image.mean():.1f}")
        print("temperature:", cam.temperature, "| cooler:", cam.cooler_status)
