setup_gui.py - user interface for the SLM and the camera
=========================================================

One window with the SLM (Thorlabs EXULUS-HD2) on the left and the camera
(Teledyne Photometrics Prime 95B) on the right. It uses the classes of
SLM.py and camera.py (see SLM_README.txt and camera_README.txt).
Screenshot: setup_gui_screenshot.png

Setup:
  1. Requirements of SLM.py and camera.py (Thorlabs SDK, PVCAM, PyVCAM,
     numpy, pillow, tifffile)
  2. pip install PyQt5 pyqtgraph
Run:
  python setup_gui.py

The window also opens without the Thorlabs SDK or PyVCAM: the missing
device cannot be connected, but masks can still be loaded and previewed.


SLM panel (left)
----------------
  Connect / Disconnect       USB connection, serial number shown when connected
  Load image / .npy...       choose a mask file (.png .bmp .tif .npy); it is
                             checked (1080 x 1920, values 0-255) and previewed
  Send to SLM                display the previewed mask on the SLM (HDMI)
  Blank (0)                  load an all-zero mask
  Send automatically...      if ticked, a loaded mask is sent immediately

The line under the preview gives the mask name and its min / max values.


Camera panel (right)
--------------------
  Connect / Disconnect       open / close the camera; sensor temperature shown
                             top right, refreshed every 2 s
  Display                    last image, with contrast histogram on the right
                             (drag the yellow handles to change the contrast)
  Snap                       acquire one image
  Start / Stop live          continuous acquisition
  Auto contrast              adjust the contrast on every new image
  (stats)                    image size, min, max and mean

  Settings
    Exposure                 in ms, applied when you leave the field (Enter/Tab)
    Binning                  1, 2 or 4, applied immediately
    Readout port/speed/gain  indexes of the PVCAM speed table, "Apply readout"
    ROI x, y, w, h           "Apply ROI"; "Full frame" goes back to 1200 x 1200
                             and binning 1
  If the live is running it is paused while a setting is changed, then restarted.

  Saving
    Folder, Name             destination folder ("..." to browse) and base name
    Frames                   number of frames for a sequence
    Save last image          save the last snapped image
    Acquire and save sequence
                             acquire "Frames" images and save them in one TIFF
  Files: <name>_<date>_<time>.tif + .json, as in camera.py. The metadata also
  contains "slm_mask": the name of the mask displayed on the SLM at that time
  (null if nothing was sent).

Closing the window disconnects both devices.


Code structure
--------------
  SLMPanel      widgets and actions of the SLM part (holds an SLM object)
  CameraPanel   widgets and actions of the camera part (holds a Camera object)
  MainWindow    puts both panels side by side, gives the SLM mask name to the
                camera metadata, closes the devices when the window is closed
A new device = a new panel class holding the device object, added to MainWindow.


Known limits (first version, not run on the hardware yet)
---------------------------------------------------------
  - The live reads the frames in the main thread: with long exposures
    (several hundred ms) the window reacts slowly. Fix: acquisition in a
    QThread.
  - Readout port/speed/gain are free indexes (0-10): the valid combinations
    of the Prime 95B are in its PVCAM speed table (visible in PVCamTest).
  - Tested only with fake SLM / camera libraries.
