camera.py - control of the Teledyne Photometrics Prime 95B camera
==================================================================

The camera is driven through PVCAM (Teledyne driver/SDK) and its Python
wrapper PyVCAM.

Setup:
  1. Install the PVCAM SDK from the Teledyne Vision Solutions website.
  2. pip install PyVCAM
  3. pip install tifffile   (for saving)
  (steps 2-3 are already done in the setup_env conda environment)
  4. Close the camera in any other software (PVCamTest, Micro-Manager...)
     before connecting from Python.


The Camera class
----------------
It stores the camera name, the exposure time, the timeout and the PyVCAM
camera object (cam) inside the object.

  Camera.list_cameras()        names of the cameras connected to the PC
                               (can be called while a camera is connected)
  connect()                    start PVCAM and open the camera
  is_connected()               return True if the camera is open
  set_exposure(ms)             exposure time used by default everywhere
  set_roi(x, y, w, h)          acquire only a region of interest (full-sensor
                               pixels; ValueError if it does not fit on the sensor)
  reset_roi()                  back to full frame (1200 x 1200), binning 1x1
  set_binning(n)               n x n binning
  snap()                       one image, 2D uint16 numpy array
  acquire_sequence(n)          n images, 3D numpy array (n, y, x)
  start_live()                 start continuous acquisition
  get_live_frame()             latest image of the live acquisition
  stop_live()                  stop continuous acquisition
  sensor_size, temperature     read-only information
  save(images, folder, name)   save a snap or a sequence as TIFF + metadata
  snap_and_save(folder)        snap() then save()
  acquire_sequence_and_save(n, folder)
                               acquire_sequence() then save()
  get_metadata()               current acquisition settings as a dictionary
  close()                      stop everything, close the camera and PVCAM
  with Camera() as cam:        calls connect() at the start and close() at
                               the end, even after an error

The exposure time argument of snap(), acquire_sequence() and start_live() is
optional: it overrides the default for that call only.


Usage in a script
-----------------
    from camera import Camera

    with Camera(exposure_time=50) as cam:     # or Camera(name="...")
        image = cam.snap()
        stack = cam.acquire_sequence(10)

Usage in a notebook
-------------------
    cam = Camera()
    cam.connect()
    cam.set_exposure(50)
    image = cam.snap()
    ...
    cam.close()        # otherwise other software cannot open the camera

Live acquisition
----------------
    cam.start_live()
    for i in range(100):
        image = cam.get_live_frame()
    cam.stop_live()

The frame timeout is the exposure time + 5 s (TIMEOUT in camera.py).
If every snap / live times out ("Frame timeout"), the camera's USB link is
stuck (e.g. after a program holding the camera was killed): close the
program, switch the camera off for 10 s and on again (or unplug / plug its USB
cable), then reconnect. The error message says so too.

Reading the temperature from the camera stops the PVCAM live acquisition
(next get_live_frame: "Acquisition not active"). During the live,
cam.temperature therefore returns the last value read before the live.


Saving
------
    cam.snap_and_save(r"D:\data\2026-09-30", name="bead")
    cam.acquire_sequence_and_save(50, r"D:\data\2026-09-30", name="stack",
                                  extra_metadata={"sample": "beads 100 nm"})

    # or acquire first, save later
    image = cam.snap()
    cam.save(image, r"D:\data\2026-09-30", name="bead")

Each save writes two files, e.g. bead_20260930_144252_123.tif / .json
(date and time down to ms are added, so nothing is overwritten):
  - .tif  : the image (2D) or the sequence (3D, frames x y x x), uint16,
            with the metadata also stored in the TIFF description
  - .json : the metadata, readable in any text editor

Metadata saved: date, camera name and serial number, exposure time (ms),
number of frames, sensor size, ROI, binning, bit depth, readout port, speed
and gain indexes, exposure and clear modes, sensor temperature, plus
anything given in extra_metadata (SLM mask, sample, notes...).
The settings are recorded at acquisition time (snap / acquire_sequence),
not at saving time.

Read back in Python:
    import json, tifffile
    images = tifffile.imread("bead_....tif")
    metadata = json.load(open("bead_....json"))


With the SLM
------------
    from SLM import SLM
    from camera import Camera

    with SLM() as slm, Camera(exposure_time=50) as cam:
        images = []
        for i, mask in enumerate(masks):
            slm.show(mask)
            images.append(cam.snap())
            cam.save(images[-1], "D:/data", name=f"mask{i}",
                     extra_metadata={"slm_mask_index": i})

slm.show() waits slm.settle_time seconds (default 0.1 s) after displaying the
mask, so snap() can be called right after it.


Checked against PyVCAM 2.3.2 (installed in setup_env)
-----------------------------------------------------
  - cam.is_open, cam.temp, set_roi(x, y, w, h), get_frame/get_sequence(exp_time,
    timeout_ms) exist as used here
  - poll_frame() returns (frame, fps, frame_count), image in frame["pixel_data"]
    (a single array with one ROI); the live reads the newest frame
Still to check on the hardware:
  - exposure times are in ms (default PVCAM exposure resolution, cam.exp_res)
  - get_sequence() starts/stops the camera for each frame: fine for a few
    frames, slow for long fast sequences
