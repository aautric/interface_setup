filter_wheel.py - Sutter Lambda 10-3: filter wheels and shutters
================================================================

Controller: Sutter Instrument Lambda 10-3 (USB serial SIADA3F8), with
  wheel A    25 mm, 10 positions (0-9)   left filters of the setup notes
  wheel B    25 mm, 10 positions (0-9)   right filters
  wheel C    not connected
  shutter A  SmartShutter
  shutter B  SmartShutter
(configuration read from the controller: "10-3WA-25WB-25WC-NCSA-IQSB-IQ")

Setup:
  1. Sutter "SI Unified Lambda USB driver" (free), kept in VCP mode so that the
     Lambda appears as a COM port ("Sutter Instrument Lambda 10-3 USB Serial
     Port", COM6 here):
       https://www.sutter.com/hubfs/SOFTWARE/SI_CDM_v2_12_36.zip
  2. pyserial: already in setup_env.
  3. Close any other software using the Lambda.
The port is found automatically (USB id 1342:1003); USB runs at 128000 baud.


Filter names
------------
FILTERS at the top of filter_wheel.py gives the filter in each position, shown
in the GUI and saved in the metadata. From the setup notes (2026-10-27), the
note numbers taken as the Lambda positions:
  wheel A (left)   1 690/40 (exc. 640), 2 607/36 (exc. 560),
                   3 520/70 (exc. 488), 5 open
  wheel B (right)  1 617/40, 2 MF469/35, 3 525/50
(left / right checked on the setup, 2026-10-27)
To check once: select a position in the GUI and look which filter is in the
beam; shift the lists by one if the numbering starts at 0 on the wheel.


The FilterWheel class
---------------------
  connect() / close()          open the port, put the controller ON LINE and
                               read its configuration / back to LOCAL (keypad)
                               mode; wheels and shutters keep their state
  wheels, shutters             connected ones, e.g. ["A", "B"]
  move(wheel, position, speed=None)   position 0-9, speed 0 (fastest) to 7
                               (slowest, default 1); waits until the move is done
  get_position(wheel), filter_name(wheel, position=None)
  open_shutter(s), close_shutter(s), is_shutter_open(s)
  get_status()                 positions, speeds, shutter states
  wheel_label(wheel)           "A (left)", "B (right)" (WHEEL_SIDES in filter_wheel.py)
  get_metadata()               filter_A_left, filter_B_right, shutter_A, shutter_B
  with FilterWheel() as lambda10:   connect() / close()

    from filter_wheel import FilterWheel

    with FilterWheel() as lambda10:
        lambda10.move("A", 2)          # 607/36
        lambda10.open_shutter("A")

Quick check from a terminal (setup_env): python filter_wheel.py
(connects, prints the state, no move)


Protocol (Lambda 10-3 manual, chapter 5)
----------------------------------------
One byte per command, echoed back by the controller, then a carriage return
(13) when the command is finished.
  move           wheel*128 + speed*16 + position (wheel A or C: 0, B: 1);
                 wheel C: byte 252 first
  shutter A/B    open 170 / 186, close 172 / 188
  status 204, on line 238, local 239, configuration 253


Checked on the controller (2026-10-27)
--------------------------------------
  - configuration and status read; wheels A and B moved 0 -> 1 -> 0 (~50 ms
    per move), position confirmed by the status
  - shutters not opened yet (light in the setup): to test with the user
