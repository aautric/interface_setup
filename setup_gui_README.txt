setup_gui.py - user interface of the setup
==========================================

One window with a panel for each device:
  SLM               Thorlabs EXULUS-HD2              (SLM.py, SLM_README.txt)
  Prime 95B         Teledyne Photometrics camera     (camera.py, camera_README.txt)
  Orca Flash4       Hamamatsu ORCA-Flash4.0 V2       (orca.py, orca_README.txt)
  FLIR Blackfly     FLIR Blackfly S BFS-U3-51S5M     (flir.py, flir_README.txt)
  Microscope        Nikon Eclipse Ti-E               (nikon_ti.py, nikon_ti_README.txt)
  Objective piezo   Thorlabs PPC001, objective Z     (piezo.py, piezo_README.txt)
  Filter wheels     Sutter Lambda 10-3, wheels A B   (filter_wheel.py,
                    and shutters A B                  filter_wheel_README.txt)
  Lasers (iLas)     GATACA iLas2: laser powers and   (ilas.py, ilas_config.json,
                    galvos, QM2000 card               ilas_README.txt)

Run:
  - double-click "Bumblebee" on the desktop (shortcut to run_setup_gui.bat,
    which activates setup_env and starts the GUI; its console window stays
    minimized and stays open if the GUI stops with an error), or
  - Anaconda Prompt:  conda activate setup_env
                      python setup_gui.py

A panel whose library is missing shows "Not available: ..." and the others
still work. Each device is connected with its own Connect button.


Arranging the panels
--------------------
Each panel is a dock window:
  - toolbar buttons (or View menu)  show / hide a panel
  - drag a panel's title bar        move it: next to another panel, or on top
                                    of it to make tabs
  - double-click the title bar      take it out as a separate window, e.g. to
    (or drag it out)                put it on another screen; it can then be
                                    minimized / maximized like any window
  - View > Dock all panels          put the separate windows back
  - View > Reset layout             back to the default arrangement
The arrangement (including separate windows and their screens) is saved when
the main window is closed and restored at the next start.
Default: SLM top left, microscope below it with the piezo next to it and the
filter wheels under the piezo, the lasers panel tabbed with the piezo, the
two cameras tabbed on the right. A panel added in a
new version appears at its default place the first time; use View > Reset
layout if needed.


SLM panel
---------
  Connect                    checks the SLM screen (HDMI); the USB link is not
                             used (the Thorlabs USB library crashes the GUI,
                             see SLM_README.txt)
  Load image / .npy...       choose a mask file (.png .bmp .tif .npy), opens in
                             Desktop\SLM_masks (easy masks: two_vortex.png,
                             diagonal diffraction grating made by
                             make_slm_masks.py); it is
                             checked (1200 x 1920, values 0-255), a window
                             asks whether to link it to a calibration
                             (align_BFP), and it is previewed (corrected if
                             linked: each SLM half moved onto its BFP)
  Send to SLM                display the previewed mask on the SLM (HDMI)
  Blank (0)                  load an all-zero mask
  Calibration...             link / unlink a calibration (the mask is
                             corrected again, re-sent if it was on the SLM)
  Corner of the display      "Calibration linked: <file>" while one is linked
                             (see calibration_README.txt)
  Send automatically...      if ticked, a loaded mask is sent immediately


Camera panels (Prime 95B, Orca Flash4, FLIR Blackfly)
-----------------------------------------------------
  Display                    last image, contrast histogram on the right
  Snap / Start-Stop live     one image / continuous acquisition
  Auto contrast              adjust the contrast on every new image
  Temperature (Prime)        box in the top-left corner of the image:
                             "sensor": read every 2 s (frozen during the live,
                             "(before live)": reading it would stop the live);
                             "last image": temperature recorded with the last
                             snap / sequence (the value saved in its metadata)
  top right                  Orca: cooler status (the Flash4.0 V2 gives no
                             temperature); FLIR: nothing (its sensor
                             temperature is not exposed by Micro-Manager)
  Settings tab
    Exposure                 in ms, applied when you leave the field (Enter/Tab)
    Binning                  applied immediately
    Readout                  Prime: port / speed / gain indexes + Apply readout
                             Orca: fast (standard) / slow (low noise)
                             FLIR: gain in dB (automatic gain off)
    ROI x, y, w, h           Apply ROI; Full frame goes back to the full sensor
                             and binning 1 (the Orca rounds the ROI to 4 px)
    Select on image          shows a yellow rectangle on the image: move it,
                             resize it with its corner handle; x, y, w, h follow
                             (in full-sensor pixels, also on an image already
                             cropped / binned); then Apply ROI
    Advanced settings...     window with all the parameters of the camera:
                             value, default (Prime), allowed values / range,
                             adjustable or read only (greyed), description
                             (Prime); search box (names and descriptions),
                             "Adjustable only", "Changed only"; values
                             different from the default highlighted; "Reset
                             to defaults..." (Prime: back to the values read
                             at connection, except exposure, binning, ROI);
                             a drop-down / Enter in a value field
                             applies it at once (live paused meanwhile), then
                             everything is re-read. Prime: all PVCAM
                             parameters the camera supports; Orca: all DCAM
                             attributes; FLIR: the ~30 properties of the
                             Micro-Manager Spinnaker driver (the camera has
                             more GenICam features, not reachable this way).
                             Exposure, binning, ROI: use the main panel.
    Prime readout            valid combinations (PVCAM table): port 0,
                             speed 0 with gain 1 (full well) / 2 (balanced) /
                             3 (sensitivity), or speed 1 with gain 1 (HDR, 16 bit)
  Saving tab
    Folder                   D:\BUMBLEBEE_acquisitions\YYYY_MM_DD_<experiment>
                             [\<subfolder>] (toolbar, see below), and the
                             name of the next acquisition (see File names)
    Pixels                  16 bit (2 bytes, as acquired) or 8 bit (1 byte):
                             8 bit halves the files but keeps only the 8
                             highest bits of the camera values (Prime: of 12
                             bits, value / 16; Orca and FLIR: of 16 bits,
                             value / 256): faint signals become coarse. The
                             metadata has saved_pixel_bits and
                             saved_value_shift (camera value ~ saved value x
                             2^shift). Live and display are not changed.
    Frames                   number of frames, with the size of one frame and
                             of the sequence as saved (ROI, binning, pixels)
    Save last image / Acquire and save sequence
  If the live is running it is paused while a setting is changed or a
  sequence is acquired, then restarted.
  Acquire and save sequence: the frames are written to the TIFF files as they
  arrive (nothing kept in memory, up to 10 million frames, MAX_FRAMES; the
  disk space is the limit). A long sequence is split into files of ~10 GB
  (BigTIFF, opened by Fiji / tifffile), each moved to D: as soon as it is
  complete. A progress window shows the frame, the frame rate and the time
  left, with a Stop button; the image keeps refreshing with the frames of the
  sequence (~10 per s, SEQUENCE_DISPLAY_PERIOD); Stop keeps the frames
  already saved, and on an error the frames taken before stay in the files.
  The .json has the number of frames really saved (the TIFF description has
  the planned one).
  Prime: the sequence runs continuously (no dead time between frames; until
  2026-10-28 it was one snap per frame, ~4 frames/s at most), with lost-frame
  detection and the camera time stamp of each frame (camera_README.txt).
  Full frame: 80 frames/s at 10 ms exposure, tested with 10 000 frames.
  Before starting, the free space is checked on the fast disk (C:) and on
  the destination (D:), counting the files still being moved to D: and
  keeping 5 GB free (FREE_SPACE_MARGIN in saving.py); if it is short the
  acquisition does not start and the message says which disk. Same check
  for Save last image. After the sequence, the image shown
  is its last frame ("Save last image" saves this frame alone).
  Disk speed: C: is an NVMe SSD (FLIR full frame 26 frames/s = 250 MB/s:
  1000 frames, 10 GB, no loss); D: E: F: are hard disks: written directly,
  after ~3.5 GB the FLIR at full speed was faster than the disk (camera buffer
  overflow, "Frames lost"). So sequences are written on C:\BUMBLEBEE_staging
  and moved to D: in the background at the end (snaps go directly to D:).
  (2026-10-27: a long FLIR sequence blocked the window, Windows showed "Not
  responding", and closing it left the camera locked in acquisition.)

Experiment (toolbar)
--------------------
  Experiment: <name>         all the cameras save in
                             D:\BUMBLEBEE_acquisitions\YYYY_MM_DD_<name>
                             (date of the acquisition; ACQUISITION_ROOT in
                             saving.py). Nothing is saved until a name is set;
                             the name is kept between sessions.
  Subfolder: <sub>           optional: if not empty, everything is saved in
                             YYYY_MM_DD_<name>\<sub> (created if needed);
                             empty: directly in YYYY_MM_DD_<name>. Also kept
                             between sessions.
  moving n file(s) to D:...  sequences waiting to be moved from the fast disk
                             (C:\BUMBLEBEE_staging, STAGING_ROOT). Each file is
                             copied, then deleted from C:. When closing, the
                             window waits for the moves ("Close now" leaves them
                             on C:); files left on C: are moved at the next start.

Size of one frame, full sensor, 16 bit / 8 bit (1000 frames: x 1000):
  Prime 95B   1200 x 1200   2.9 MB / 1.4 MB
  Orca        2048 x 2048   8.4 MB / 4.2 MB
  FLIR        2448 x 2048  10.0 MB / 5.0 MB
  binning 2: / 4

File names (saving.py): all the information is in the metadata, so the names
are just numbers. In a folder, the acquisitions (snaps saved with "Save last
image" and sequences, all cameras) are numbered 0, 1, 2... in order:
  X_Y.tiff       acquisition X, file Y (0, 1...: a sequence larger than
                 ~10 GB, PIECE_BYTES, is split; each file has the metadata in
                 its first page, with file_index and first_frame)
  X.json         metadata of acquisition X (sections below)
  X_frames.csv   sequences: for each frame, pc_time_s (PC clock when the
                 frame arrived, from the first frame), camera_frame and
                 camera_time_s (Prime: camera counter and time stamp of the
                 start of the exposure), slm_mask (number of the SLM mask
                 shown, or none)
  X_slm_masks\   the SLM masks shown during acquisition X, K.png, numbered in
                 the order they first appear (a mask shown again keeps its
                 number: one mask all along = a single 0.png)
  X_calibrations\ copies of the calibration files that corrected the SLM
                 mask (linked in the SLM panel), with their .png
The next number is 1 + the highest one found in the folder (and in its
fast-disk copy), so it is never reused. The acquisitions of 2026-10-27 were
renamed to this scheme (2026_10_27_test\renamed_files.txt; the old name is in
each .json, original_file_name).
Metadata (X.json and the first page of each TIFF file), recorded when the
images are taken (a snap saved later: the state at the snap), in 4 sections
(saving.organize_metadata; since 2026-10-29, before: one flat dictionary):
  acquisition   index, camera, date, n_frames, frame_times, saved_pixel_bits
                (saved_value_shift), first_frame_pc_time, files,
                frame_times_file
  devices       connected: {slm, prime_95b, orca_flash4, flir_blackfly,
                microscope, objective_piezo, filter_wheels: true / false}
                (MainWindow.extra_metadata), then
                microscope: connected, filter_cube (set by hand, always),
                  objective, light_path, z_um, xy_um, pfs_on, pfs_status,
                  pfs_offset
                objective_piezo: connected, serial_number, closed_loop,
                  voltage_V, position_um
                filter_wheels: connected, filter_A_left, filter_B_right,
                  shutter_A, shutter_B
  slm           connected, link, monitor, size, settle_time_s, mask_shown,
                linked_calibrations (calibrations that corrected the mask
                shown: {align_BFP: file, date, light_path, correction, copy
                (in X_calibrations)}, empty if none),
                masks_folder, masks [{number, file, name, first_frame}],
                mask_of_each_frame ("X_frames.csv, column slm_mask", or the
                list for a snap). The SLM mask of each frame is read at each
                frame (SequenceWriter slm_mask = MainWindow.shown_slm_mask).
  camera        main settings (camera's get_metadata: exposure, ROI,
                binning, readout, temperature...), then advanced: every
                parameter of the advanced settings window {name: value}
                (Prime: + post_processing, the despeckle filters / QuantView)


Calibration
-----------
Upper right corner of the window (end of the toolbar row, CalibrationPanel):
one button per calibration of calibration.py, each opening its step-by-step
window (optical elements to place, devices to connect, then the calibration).
First one: Align BFP (align_BFP). See calibration_README.txt.


Microscope panel
----------------
  Objective                  displayed only: it is never changed from software
  Light path                 Eye / Left / Right / Left80, ~3 s per change; the
                             box is red when the light goes left, green when it
                             goes right (LIGHT_PATH_COLORS in setup_gui.py)
  Cube (set by hand)         the turret is not motorised: choose the cube in
                             place (FILTER_CUBES in nikon_ti.py, default cube 2); remembered
                             between sessions, saved with the images even if
                             the microscope is not connected
  Focus                      Z - / Z + by Step (max 100 µm); Go to an absolute
                             position (asks for confirmation beyond 100 µm)
  Stage                      X - / X + / Y + / Y - by Step
  Perfect Focus              just under Optics. On: lock the focus (sample with
                             oil under the objective, near focus first);
                             status in green (locked), orange (searching /
                             focusing) or red (out of range, error);
                             Offset: current PFS offset; - / + by Step move the
                             focal plane while the PFS holds the focus;
                             Target + Set offset for an absolute value.
                             Needs the objective registered in the Ti
                             (TiControl, see nikon_ti_README.txt)
The positions are read every 0.5 s.


Objective piezo panel
---------------------
  State                      loop mode, output voltage, position from the sensor
  Mode                       open loop: Target and Step in V
                             closed loop: Target and Step in µm, 0 to 450 µm
                             (the sensor reads about -16 µm at 0 V: go to a
                             positive position first); switching keeps the
                             position
  Go to / - / +              moves, each waits until the position / voltage is
                             reached; a move larger than about 20 µm asks for
                             confirmation


Filter wheels panel
-------------------
  Wheel A (left) /           choose a position (with its filter name, see
  Wheel B (right)
                             FILTERS / WHEEL_SIDES in filter_wheel.py): the wheel moves at
                             the speed next to it (0 fastest, 7 slowest)
  Shutter A / Shutter B      state, Open / Close button
The rows are built from the configuration read from the controller.


Lasers (iLas) panel (ilas.py; tested on the card 2026-11-03 with the 561 nm)
---------------------------------------------------------------------------
  Connect                    asks for confirmation (Modular and MetaMorph must be
                             closed); the lasers stay at power 0
  Laser ON / OFF             big button at the top, red when on
  Laser lines                one row per line of ilas_config.json (up to 6: one per
                             QM2000 channel): tick = used when on, power in % of the
                             line; channel and galvo calibration in grey
  Galvos                     fixed beam: angle (deg in the glass, 0 = epi) and
                             direction; 360 deg circle: half angle; fixed beam:
                             galvo X, Y (-8000...8000)
  Angles of                  line whose galvo calibration sets the angles (default:
                             the first line ticked); calibration and incidence shown
                             (epi / HiLo / TIRF above 62.6 deg, NA)
  Calibration...             to come: angle and power calibrations of the lines
  Disconnect / closing       lasers off, end of the LD2000 session
Saved with each acquisition: X.json > devices > lasers. See ilas_README.txt.


Code structure
--------------
  DevicePanel     common part: Connect button, status, widgets enabled when
                  connected, get_metadata()
  SLMPanel, PrimePanel / OrcaPanel (CameraPanel), NikonPanel, PiezoPanel
  MainWindow      dock windows, toolbar, layout saving, metadata of the other
                  devices for the camera files, closes all devices on exit
A new device = a new DevicePanel subclass, added in MainWindow.__init__.


Known limits
------------
  - Advanced settings tested on the FLIR (2026-10-27) and the Prime
    (2026-10-29, reading: two long text parameters crashed it, now skipped);
    the Orca window is not tested on the camera yet.
  - The lives read the frames in the main thread: with long exposures, or both
    cameras in live, the window reacts slowly. Fix: acquisition in a QThread.
  - Prime readout port/speed/gain are free indexes (0-10): the valid
    combinations are in its PVCAM speed table (visible in PVCamTest).
  - Checked on the hardware: connection, reading, snap, live, save with
    metadata, piezo moves (open and closed loop). The move buttons of the
    microscope are not tested yet from the GUI (the same moves work in
    nikon_ti.py).
