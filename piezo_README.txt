piezo.py - control of the objective piezo (Thorlabs PPC001)
===========================================================

The PPC001 (benchtop precision piezo controller, 1 channel, serial 44454524)
moves the objective in Z: 450 µm travel, 0-150 V, capacitive position sensor.
It is driven through the Kinesis C library
(Thorlabs.MotionControl.Benchtop.PrecisionPiezo.dll) with ctypes.

Setup:
  1. Thorlabs Kinesis, 64-bit version for 64-bit Windows, installed in
     C:/Program Files/Thorlabs/Kinesis (KINESIS_PATH in piezo.py).
  2. Unplug / plug the USB cable once after installing Kinesis.
  3. Close the Kinesis software (and the GUI) before connecting from a script.


Open loop and closed loop
-------------------------
  open loop     set_voltage(V): the controller applies the voltage; the
                position depends on the piezo (hysteresis, creep), about
                2.8 µm/V here
  closed loop   set_position(µm): the controller reads the capacitive sensor
                and adjusts the voltage (PID) to hold the position, within
                ~0.02 µm
The sensor is calibrated in the factory: no zeroing is needed (the PPC001
ignores the zero command). At 0 V it reads about -16 µm, so 0 µm is reached at
about 6 V. Switching to closed loop keeps the current position (no jump).


The Piezo class
---------------
  list_controllers()           serial numbers of the PPC001 connected to the PC
  connect() / close()          open / close the controller (no move; the piezo
                               keeps its voltage when closed)
  is_connected()
  get_status()                 flags: actuator_connected, closed_loop, error...
                               ("error" is always on here and changes nothing)
  get_voltage(), get_position()   output (V), sensor position (µm)
  set_voltage(V)               open loop, 0 to 150 V
  set_closed_loop(True/False)  closed loop (µm) / open loop (V)
  set_position(µm), move_by(µm)   closed loop moves, 0 to 450 µm
  is_closed_loop(), max_voltage, max_travel
  get_metadata()               state to save with the camera images
  with Piezo() as piezo:       connect() at the start, close() at the end

Every command waits until the readings confirm it (mode changed, voltage
applied within 0.1 V, position reached within 0.05 µm), at most 2 s, then
settle_time more (0 by default, Piezo(settle_time=...)). This matters: the
Kinesis DLL updates its readings ~100 ms after a command, so a reading taken
right after it gives the previous state.

    from piezo import Piezo

    with Piezo() as piezo:
        piezo.set_closed_loop(True)
        piezo.set_position(10)
        piezo.move_by(0.5)

Quick check from a terminal (setup_env): python piezo.py
(connects, prints the state, no move)


Checked on the controller (2026-10-26)
--------------------------------------
  - open loop 0 / 2 / 4 V: sensor -16.3 / -10.9 / -5.0 µm (going up),
    -10.5 µm at 2 V going down (hysteresis)
  - closed loop: 2, 5, 2, 0 µm reached within 0.015 µm; steps of ±0.5 / ±1 µm
    within 0.03 µm, ~0.1-0.13 s per move
  - PID constants in the controller: P 540, I 480, D 50; feedback capacitive,
    polarity inverted (correct: the position increases with the voltage)
