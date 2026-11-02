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
  acquire_sequence(n, progress=None, writer=None)
                               n images, 3D numpy array (n, y, x), acquired
                               continuously (see "Sequences" below);
                               progress(done, n, frame) is called after each
                               frame (returning False stops); with a
                               saving.SequenceWriter the frames are written to
                               the files as they arrive (not kept in memory)
  start_live()                 start continuous acquisition
  get_live_frame()             latest image of the live acquisition
  stop_live()                  stop continuous acquisition
  sensor_size, temperature     read-only information
  save(images, folder)         save a snap or a sequence as the next
                               acquisition X of folder (X_0.tiff + X.json)
  snap_and_save(folder)        snap() then save()
  acquire_sequence_and_save(n, folder)
                               sequence written as it is acquired (any length,
                               X_0.tiff, X_1.tiff... + X_frames.csv + X.json);
                               returns the first TIFF path
  get_metadata()               current acquisition settings as a dictionary
  get_parameters()             all the PVCAM parameters the camera supports
                               (value, type, range / choices, adjustable,
                               readable during live)
  set_parameter(name, value)   set one of them by name (e.g. "GAIN_INDEX", 2)
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
If every snap / live times out ("Frame timeout"), the camera's link (USB now,
PCIe card soon) is stuck (e.g. after a program holding the camera was killed):
close the program, switch the camera off for 10 s and on again, then
reconnect. The error message says so too.

Reading the temperature from the camera stops a running PVCAM acquisition
(next frame: "Acquisition not active"). During the live and the sequences,
cam.temperature therefore returns the last value read before.

Sequences
---------
acquire_sequence runs the camera continuously (PVCAM circular buffer, as the
live), so there is no dead time between frames other than the readout: the
frame rate is set by the exposure and the readout (until 2026-10-28 it took
one snap per frame: ~0.25 s per frame at 100 ms exposure).
  - Clear mode: during the sequence the sensor is cleared once at the start
    ("Pre-Sequence"), not before each frame; the clear mode before (Pre-
    Exposure by default, 2 clear cycles = +25 ms per frame) is restored at the
    end. Frame time = max(exposure, readout 12.4 ms at full frame).
  - Tested 2026-10-29 (USB 3, full frame, 16 bit, dark): 10 ms exposure ->
    80.5 frames/s (camera time stamps every 12.418 ms), 10 000 frames in
    125 s, 3 files (10 + 10 + 8.8 GB), no frame lost, longest PC pause 0.1 s;
    30 ms -> 33.3 frames/s. With Pre-Exposure clearing it was 21 frames/s.
  - Buffer: SEQUENCE_BUFFER_BYTES (1 GB of RAM, ~350 full frames) in camera.py.
  - Lost frames: each frame has the camera frame counter; if the PC reads
    the frames slower than the camera makes them for longer than the buffer,
    the counter jumps and the sequence stops with "Frames lost" (the frames
    before are saved).
  - Time stamps: PVCAM frame metadata is turned on during the sequence; the
    camera time stamp of the start of each exposure (camera_time_s, in s) and
    the frame counter (camera_frame) go to X_frames.csv, with the PC time of
    reception (pc_time_s). If the camera has no metadata, only the PC time.
    The .json says which ("frame_times").
  - Connection: USB 3 now; a PCIe card will replace it (faster readout, same
    code: PVCAM handles both).


Advanced settings: get_parameters skips the value of text parameters longer
than 31 characters (CAM_SYSTEMS_INFO, DD_INFO): PyVCAM copies them into a
32-character buffer and reading them crashed Python (fixed 2026-10-29).
Defaults: connect() reads all the adjustable parameters; these values (the
state of the camera each time it is opened) are the "default" of
get_parameters, and reset_parameters() sets them back (readout port, speed,
gain first; not exposure, binning, ROI). PVCAM's own ATTR_DEFAULT is NOT
used: it differs from the opening state (frame summing on, driver time-out
0, ...). Exception: TEMP_SETPOINT is kept by the camera after closing (until
it is switched off; found at -30 °C on 2026-10-29), so its default is fixed
at -15 °C (_FIXED_DEFAULTS). The post-processing selectors (PP_INDEX,
PP_PARAM_INDEX, PP_PARAM) have no default and are not reset.
Each parameter has a short description (_PVCAM_DESCRIPTIONS).

Despeckle: the Prime has 4 despeckle filters (bright / dark, low / high:
replace isolated pixels much brighter or darker than their neighbours). The
camera turns them ON each time it is opened; connect() and
reset_parameters() turn them off (DESPECKLE = False in camera.py): raw data
for quantitative work. The state of all post-processing (despeckle,
QuantView) is saved in the metadata ("post_processing").
EXPOSURE_MODE and EXPOSE_OUT_MODE go through PyVCAM's exp_mode /
exp_out_mode (PyVCAM keeps them and passes them at each acquisition start;
set directly they had no effect).


Saving
------
    cam.snap_and_save(r"D:\data\2026-09-30")
    cam.acquire_sequence_and_save(50, r"D:\data\2026-09-30",
                                  extra_metadata={"sample": "beads 100 nm"})

    # or acquire first, save later
    image = cam.snap()
    cam.save(image, r"D:\data\2026-09-30")

Each acquisition gets the next free number X in its folder (0, 1, 2...; all
the information is in the metadata) - see saving.py:
  - X_Y.tiff     : the frames, uint16 (or uint8); a long sequence is split
                   into files of ~10 GB (Y = 0, 1...; PIECE_BYTES in
                   saving.py), each with the metadata in its first page
  - X.json       : the metadata, readable in any text editor (with n_frames
                   and the list of files)
  - X_frames.csv : sequences only: time of each frame (see Sequences)

Metadata saved: date, camera name and serial number, exposure time (ms),
number of frames, sensor size, ROI, binning, bit depth, readout port, speed
and gain indexes, exposure and clear modes, sensor temperature, plus
anything given in extra_metadata (SLM mask, sample, notes...).
The settings are recorded at acquisition time (snap / acquire_sequence),
not at saving time.

Read back in Python:
    import json, tifffile
    images = tifffile.imread("0_0.tiff")     # one file (all its frames)
    metadata = json.load(open("0.json"))
    # a split sequence:
    # numpy.concatenate([tifffile.imread(f) for f in metadata["acquisition"]["files"]])
The .json has 4 sections: acquisition, devices, slm, camera (main settings,
then "advanced": all the parameters + post_processing); see
setup_gui_README.txt, "File names". save() takes extra_metadata ("devices",
"slm" sections, other keys go to "acquisition") and slm_mask ((pattern,
name) of the SLM mask shown, saved in X_slm_masks\).


With the SLM
------------
    from SLM import SLM
    from camera import Camera

    with SLM() as slm, Camera(exposure_time=50) as cam:
        images = []
        for i, mask in enumerate(masks):
            slm.show(mask)
            images.append(cam.snap())
            cam.save(images[-1], "D:/data",
                     extra_metadata={"slm_mask_index": i})

slm.show() waits slm.settle_time seconds (default 0.1 s) after displaying the
mask, so snap() can be called right after it.


Checked against PyVCAM 2.3.2 (installed in setup_env)
-----------------------------------------------------
  - cam.is_open, cam.temp, set_roi(x, y, w, h), get_frame/get_sequence(exp_time,
    timeout_ms) exist as used here
  - poll_frame() returns (frame, fps, frame_count), image in frame["pixel_data"]
    (a single array with one ROI); the live reads the newest frame
  - start_live(exp_time, buffer_frame_count, reset_frame_counter) +
    poll_frame(oldestFrame=True): frames in order; frame_count = camera frame
    counter (1, 2, ...), the oldest frames are dropped if the buffer is full
  - frame["meta_data"]["frame_header"]["timestampBofPs"] (ps) with
    cam.metadata_enabled = True
Still to check on the hardware:
  - exposure times are in ms (default PVCAM exposure resolution, cam.exp_res)
  - continuous sequences (written 2026-10-28, tested with a simulated camera
    only): frame rate, time stamps (does the Prime 95B give metadata?)

save(), acquire_sequence_and_save(): pixel_bits=16 (as acquired) or 8 (1 byte
per pixel, the 8 highest bits of the values: see saving.to_saved_pixels).
