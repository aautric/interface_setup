setup_gui.py - user interface of the setup
==========================================

One window with a panel for each device:
  SLM               Thorlabs EXULUS-HD2              (SLM.py, SLM_README.txt)
  Prime 95B         Teledyne Photometrics camera     (camera.py, camera_README.txt)
  Orca Flash4       Hamamatsu ORCA-Flash4.0 V2       (orca.py, orca_README.txt)
  Microscope        Nikon Eclipse Ti-E               (nikon_ti.py, nikon_ti_README.txt)
  Objective piezo   Thorlabs PPC001, objective Z     (piezo.py, piezo_README.txt)

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
Default: SLM top left, microscope and piezo below it, the two cameras tabbed
on the right.


SLM panel
---------
  Load image / .npy...       choose a mask file (.png .bmp .tif .npy); it is
                             checked (1200 x 1920, values 0-255) and previewed
  Send to SLM                display the previewed mask on the SLM (HDMI)
  Blank (0)                  load an all-zero mask
  Send automatically...      if ticked, a loaded mask is sent immediately


Camera panels (Prime 95B, Orca Flash4)
--------------------------------------
  Display                    last image, contrast histogram on the right
  Snap / Start-Stop live     one image / continuous acquisition
  Auto contrast              adjust the contrast on every new image
  top right                  Prime: sensor temperature (not updated during the
                             live: reading it would stop the live); Orca:
                             cooler status
  Settings tab
    Exposure                 in ms, applied when you leave the field (Enter/Tab)
    Binning                  applied immediately
    Readout                  Prime: port / speed / gain indexes + Apply readout
                             Orca: fast (standard) / slow (low noise)
    ROI x, y, w, h           Apply ROI; Full frame goes back to the full sensor
                             and binning 1 (the Orca rounds the ROI to 4 px)
    Select on image          shows a yellow rectangle on the image: move it,
                             resize it with its corner handle; x, y, w, h follow
                             (in full-sensor pixels, also on an image already
                             cropped / binned); then Apply ROI
    Prime readout            valid combinations (PVCAM table): port 0,
                             speed 0 with gain 1 (full well) / 2 (balanced) /
                             3 (sensitivity), or speed 1 with gain 1 (HDR, 16 bit)
  Saving tab
    Folder, Name, Frames     destination, base name, number of frames
    Save last image / Acquire and save sequence
  If the live is running it is paused while a setting is changed or a
  sequence is acquired, then restarted.

Files: <name>_<date>_<time>.tif + .json (saving.py). Besides the camera
settings, the metadata contains the state of the other devices at saving time:
  slm_mask              name of the mask shown on the SLM (null if none)
  microscope_...        objective, light path, z, xy, Perfect Focus
  piezo_...             loop mode, voltage, position
(only for the devices connected in the GUI)


Microscope panel
----------------
  Objective                  displayed only: it is never changed from software
  Light path                 Eye / Left / Right / Left80
  Focus                      Z - / Z + by Step (max 100 µm); Go to an absolute
                             position (asks for confirmation beyond 100 µm)
  Stage                      X - / X + / Y + / Y - by Step
  Perfect Focus              On / status / offset
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
  - The lives read the frames in the main thread: with long exposures, or both
    cameras in live, the window reacts slowly. Fix: acquisition in a QThread.
  - Prime readout port/speed/gain are free indexes (0-10): the valid
    combinations are in its PVCAM speed table (visible in PVCamTest).
  - Checked on the hardware: connection, reading, snap, live, save with
    metadata, piezo moves (open and closed loop). The move buttons of the
    microscope are not tested yet from the GUI (the same moves work in
    nikon_ti.py).
