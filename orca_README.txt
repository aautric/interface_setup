orca.py - control of the Hamamatsu ORCA-Flash4.0 V2 camera
==========================================================

Camera: C11440-22CU (ORCA-Flash4.0 V2), S/N 740887, connected by USB 3.
It is driven through DCAM-API (Hamamatsu driver) and the DCAM part of pylablib.

Setup:
  1. DCAM-API installed (C:/Windows/System32/dcamapi.dll, version 26.1 here)
  2. pylablib and tifffile: already in the setup_env conda environment
  3. Close the camera in any other software (HCImage, Micro-Manager...)
     before connecting from Python.


The Orca class
--------------
Same methods as the Camera class of camera.py (Prime 95B), so both cameras are
used the same way and the GUI camera panel can be reused for it:

  Orca.list_cameras()          'model serial' of the DCAM cameras of the PC
  connect() / close()          open / close the camera
  is_connected()               return True if the camera is open
  set_exposure(ms)             exposure time used by default everywhere
  set_roi(x, y, w, h)          region of interest (full-sensor pixels)
  reset_roi()                  back to full frame (2048 x 2048), binning 1x1
  set_binning(n)               n x n binning (1, 2 or 4)
  set_readout_speed(speed)     'fast' (standard scan) or 'slow' (low noise)
  snap()                       one image, 2D uint16 numpy array
  acquire_sequence(n)          n images, 3D numpy array (n, y, x)
  start_live() / get_live_frame() / stop_live()
  sensor_size, cooler_status   read-only information ('READY' when cooled;
  temperature                  the Flash4.0 V2 gives no temperature: None)
  save(images, folder, name)   TIFF + .json metadata (saving.py, as camera.py)
  snap_and_save(folder), acquire_sequence_and_save(n, folder)
  get_metadata()               current acquisition settings as a dictionary
  with Orca() as cam:          connect() at the start, close() at the end

Exposure times are in ms (pylablib uses seconds, the conversion is done here).
The camera may round the exposure time and the ROI to its allowed steps: the
values actually used are saved in the metadata (exposure_time_camera_ms,
roi_camera).

    from orca import Orca

    with Orca(exposure_time=50) as cam:
        image = cam.snap()
        cam.save(image, r"D:\data\2026-10-26", name="orca")

Quick check from a terminal (setup_env): python orca.py


Checked on the camera (2026-10-26)
----------------------------------
  - snap, ROI, binning 2, full frame, sequence (20 x 2048 x 2048 at 10 ms in
    ~1 s), live, save with metadata: all work
  - ROI position and size are rounded by the camera to multiples of 4
    (asked (101, 203, 500, 300) -> used (100, 200, 500, 300)); minimum 4 px;
    binning up to 4
  - 16-bit images, sensor mode AREA, internal trigger, readout 'fast'
  - The camera must be plugged in and switched on before connecting (it is
    only seen by DCAM then).
