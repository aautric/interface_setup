flir.py - control of the FLIR Blackfly S BFS-U3-51S5M camera
============================================================

Camera: Blackfly S BFS-U3-51S5M (Teledyne FLIR), serial 19423764, USB 3,
2448 x 2048 pixels, monochrome, 10 or 12-bit ADC.
It is driven through Micro-Manager (SpinnakerCamera device adapter, using the
FLIR Spinnaker SDK installed on the PC) and pymmcore, as for the microscope.
(FLIR's own Python package, PySpin, exists only for old Python versions with
the Spinnaker 2.3 installed here.)

Setup:
  1. Spinnaker SDK 2.3 (C:/Program Files/FLIR Systems/Spinnaker) and
     Micro-Manager 2.0 (MM_PATH in flir.py) - both installed
  2. pymmcore and tifffile: already in setup_env
  3. Close SpinView / Micro-Manager before connecting.
  4. Use a USB 3 port (blue): on USB 2 a full 12-bit frame (10 MB) limits
     the camera to ~3 frames/s.


Settings at connection (DEFAULT_SETTINGS in flir.py)
-----------------------------------------------------
The camera starts with automatic gain and gamma 0.8, not suited to
measurements. connect() sets:
  Exposure Auto Off, Gain Auto Off, Gain 0 dB, Gamma off,
  ADC 12 bit, Pixel Format Mono16 (12-bit values in uint16 images)


The Flir class
--------------
Same methods as the Camera class of camera.py (Prime 95B) and Orca:

  Flir.list_cameras()          Spinnaker cameras of the PC
  connect() / close()
  set_exposure(ms), set_gain(dB), get_gain()
  set_roi(x, y, w, h)          full-sensor pixels; the camera rounds the ROI
                               to its steps (e.g. (100, 200, 400, 300) ->
                               (96, 198, 404, 304)): the ROI really used is
                               saved as roi_camera in the metadata
  reset_roi()                  full frame, binning 1
  set_binning(n)               1 or 2 (the ROI is kept)
  snap(), acquire_sequence(n), start_live() / get_live_frame() / stop_live()
  save(images, folder, name), snap_and_save(), acquire_sequence_and_save()
  get_metadata()               exposure, gain, pixel format, ADC, ROI,
                               binning, gamma, black level, trigger mode
  temperature                  None (not available through Micro-Manager)
  with Flir() as cam:          connect() / close()

    from flir import Flir

    with Flir(exposure_time=50) as cam:
        image = cam.snap()
        cam.save(image, r"D:\data\2026-10-27", name="flir")

Quick check from a terminal (setup_env): python flir.py


Checked on the camera (2026-10-27)
----------------------------------
  - snap (12 bit, gain 0, gamma off), ROI, binning 2 with ROI, full frame,
    sequence of 10, live, save with metadata, gain change: all work, also
    from the GUI with the microscope connected at the same time
  - USB: through a USB 2.0 hub (chip 1A40:0101) it gave only ~3 frames/s;
    moved to USB 3 (SuperSpeed) on 2026-10-27: live 26 frames/s at full frame
    in 12 bit (camera maximum), sequences 13 frames/s (12 bit) / 19 frames/s
    (8 bit), 130 MB/s. Keep it on a USB 3 port.
  - GUI live: get_live_frame(wait=False) returns None when no new frame has
    arrived, so the window is not blocked between two frames (it was blocked
    ~230 ms per refresh at 4 fps)
