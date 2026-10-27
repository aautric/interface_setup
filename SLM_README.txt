SLM.py - control of the Thorlabs EXULUS-HD2 SLM
================================================

The SLM has two links to the PC:
  - USB  : settings/commands (EXULUS_COMMAND_LIB from the Thorlabs Python SDK)
  - HDMI : the phase mask itself, the SLM is seen as a second monitor
           (Thorlabs_EXULUS_CGHDisplay from the Thorlabs Python SDK)

Setup: C:/Program Files/Thorlabs/EXULUS/Help/SDK/Thorlabs_EXULUS_PythonSDK.zip
is unzipped in Documents/Thorlabs_EXULUS_PythonSDK. SDK_PATHS at the top of SLM.py
points to its two sub-folders Thorlabs.EXULUS.Python.SDK (control) and
Thorlabs.EXULUS.CGHDisplay (display); adapt it if the SDK is moved.
Requires numpy and pillow (all included in the setup_env conda environment).


The SLM class
-------------
It stores the serial number, the monitor, the USB handle (hdl) and the
display window (window) inside the object.

  connect()          check that the SLM monitor (HDMI) is seen; with
                     SLM(use_usb=True), also open the USB port and check that the
                     SLM answers. USB is OFF by default (USE_USB in SLM.py): see
                     below, the Thorlabs USB library crashes Python on this PC.
                     Masks only need HDMI.
  is_connected()     return True if the SLM monitor is seen (and, with USB, if
                     the SLM answers)
  name               "HDMI" or "HDMI + USB COM4"
  open_window()      create the HDMI display window on the SLM monitor
  show(source)       load a mask (array, image or .npy) and display it;
                     opens the window the first time, then waits settle_time
                     seconds (default 0.1 s, SLM(settle_time=...)) so that the
                     mask is stable before a camera acquisition
  close()            close the window and the USB port
  with SLM() as slm: calls connect() at the start and close() at the end,
                     even after an error

load_phase_pattern(source) stays a plain function: it does not touch the
hardware, so it can be used on its own to check masks (size 1200x1920,
integer values from 0 to 255).


Usage in a script
-----------------
    from SLM import SLM

    with SLM() as slm:             # or SLM(serial_number="COM4", monitor=2)
        slm.show(mask1)
        slm.show("mask2.png")

Usage in a notebook (where a "with" block is less convenient)
-------------------------------------------------------------
    slm = SLM()
    slm.connect()
    slm.show(mask)
    ...
    slm.close()        # otherwise the Thorlabs GUI cannot connect afterwards


Other devices
-------------
Use the same method names (connect, close, __enter__/__exit__) for the camera
class and the other machines, so that the final script can drive them together:

    with SLM() as slm, Camera() as cam:
        for mask in masks:
            slm.show(mask)
            images.append(cam.snap())


Checked against the Thorlabs SDK files (not run on the hardware yet)
-------------------------------------------------------------------
  - baud rate 38400, ack_code list filled in place, image buffer passed as a
    ctypes POINTER(c_ubyte), negative return value = failure: as in the
    Thorlabs wrappers and examples
  - the DLLs are 64-bit: use a 64-bit Python (setup_env is)

Checked on the setup (2026-10-26)
---------------------------------
  - the EXULUS-HD2 is 1920 x 1200 pixels, 8 µm pitch (manual p.29); Windows
    sees it as DISPLAY2 "Generic Monitor (Thorlabs SLM)" at 1920 x 1200
  - its USB link is a Silicon Labs CP210x bridge (COM4 here, serial 00444118).
    EXULUSListDevices() sometimes returns nothing (the state that usually needs
    the USB cable to be unplugged and plugged again), while
    EXULUSOpen("COM4", ...) still works and the SLM answers ACK.
    connect() therefore falls back to the CP210x COM port (find_exulus_port()).
  - 2026-10-27: since the Sutter Lambda 10-3 and its USB driver were added,
    the Thorlabs USB library (exulus_command_library.dll) corrupts memory
    while scanning the serial devices: EXULUSListDevices() always crashes
    Python (Windows heap corruption, 0xc0000374), and EXULUSOpen() too when
    other libraries are loaded (PVCAM, pylablib, pyqtgraph: i.e. in the GUI).
    The bug is in the Thorlabs DLL. The USB link is therefore off by default
    and its library is not even loaded; the display (HDMI, cgh_display.dll)
    is not affected. To use USB again: SLM(use_usb=True), in a small script
    that loads nothing else, or with a newer Thorlabs EXULUS SDK.
Still to check on the setup:
  - settle_time: measure how long the SLM takes to settle after a new mask
