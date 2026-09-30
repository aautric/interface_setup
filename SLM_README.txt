SLM.py - control of the Thorlabs EXULUS-HD2 SLM
================================================

The SLM has two links to the PC:
  - USB  : settings/commands (EXULUS_COMMAND_LIB from the Thorlabs Python SDK)
  - HDMI : the phase mask itself, the SLM is seen as a second monitor
           (Thorlabs_EXULUS_CGHDisplay from the Thorlabs Python SDK)

Setup: unzip C:/Program Files/Thorlabs/EXULUS/Help/SDK/Thorlabs_EXULUS_PythonSDK.zip
and set SDK_PATHS at the top of SLM.py to the folders containing the .py wrappers
and their DLLs. Requires numpy and pillow.


The SLM class
-------------
It stores the serial number, the monitor, the USB handle (hdl) and the
display window (window) inside the object.

  connect()          find the SLM, open the USB port, check that the SLM answers
  is_connected()     return True if the SLM answers (useful to check at any time)
  open_window()      create the HDMI display window on the SLM monitor
  show(source)       load a mask (array, image or .npy) and display it;
                     opens the window the first time
  close()            close the window and the USB port
  with SLM() as slm: calls connect() at the start and close() at the end,
                     even after an error

load_phase_pattern(source) stays a plain function: it does not touch the
hardware, so it can be used on its own to check masks (size 1080x1920,
integer values from 0 to 255).


Usage in a script
-----------------
    from SLM import SLM

    with SLM() as slm:             # or SLM(serial_number="...", monitor=2)
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


Still to check against the Thorlabs example files (not run on the hardware yet)
-------------------------------------------------------------------------------
  - baud rate (38400 is a guess): EXULUS_COMMAND_LIB_EXAMPLE.py
  - how EXULUSCheckCommunication returns ack_code (assumed: a list filled in place)
  - how CghDisplayShowWindow expects the image buffer (a ctypes pointer is passed)
  - success return value of the display functions (assumed 0)
  - exact names of the SDK sub-folders in SDK_PATHS
