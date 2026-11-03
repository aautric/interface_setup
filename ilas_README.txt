ilas.py - laser lines and galvos of the GATACA iLas2 (without MetaMorph / Modular)
==================================================================================

STATUS (2026-11-03): works on the card with the 561 nm (Oxxius): power steps 0-1-3-10-3-0 %
followed at the objective, fixed beam at 5 deg, 360 deg circle at 5 deg half angle, no galvo
noise. The other lines, TIRF angles and the angle calibration are not checked yet.

Files
-----
  ilas.py           the device (ILas), the stand-in card for tests (FakeLD2000)
  ilas_config.json  laser lines and galvo calibrations (edit it to add a line)

How the iLas2 works
-------------------
- One Pangolin QM2000 PCI card ("QM2000 PCI Device", serial 9437) drives
  - the galvos (X / Y outputs): where the beam is in the back focal plane (BFP), i.e. the
    incidence angle (epi, HiLo, TIRF) and its direction. The card plays a frame of points in
    a loop: one point repeated = fixed beam; 100 points on a circle = 360 deg TIRF (6.67 ms
    per turn at 15000 points/s, as Modular);
  - the laser powers: colour channels 1-6 (analog 0-255). Each point has a colour code, and
    the palette entry (colour register) of the code gives the value of each channel. On the
    Oxxius combiner (USB "Oxxius System", serial BTC-00523; Oxxius software) the 561 nm
    (LAS-02053, COM5, emission on at 100 %, 106 mW) goes through an AOM with analog
    modulation enabled: channel 3 = AOM input. No Oxxius command is needed; Modular does not
    talk to the Oxxius either. The Oxxius software only switches emission on / off.
- Library: Pangolin LD2000, 64-bit C:\Windows\System32\LD2000.dll (C:\Windows\LD2000.ini:
  "Number of boards=1"). The colour function is spelled RGB1toRGB4 in this DLL.
- Modular (C:\Program Files\GATACA Systems\Modular\MainApp.exe, .NET) is GATACA's GUI, driven
  from MetaMorph (variable ILAS.RPC). Read from its LaserCore.dll and TIRFLib.dll (not run)
  for what ilas.py does. Only one program can drive the card.

What ilas.py does (same as Modular)
-----------------------------------
- connect(): refuses if MainApp.exe / MetaMorph.exe / MMHost.exe run; InitialQMCheck;
  CheckSessionSettings (no session -> BeginSession(20000 frames, 6000 points, 100, 1));
  scale -8000...8000; 15000 points/s; laser off.
- Powers: colour code = 1000 + sum over the enabled lines of channel x (10, 10, 100, 100,
  1000, 1000 for channels 1-6): 1300 = 561 nm alone, 1700 = 561 + 488. Palette entry of
  the code: channel value = 255 x power / 100 (offsets low / high of the line), intensity
  and shutter bytes 0 (Modular TIRF). An arbitrary code (0xABCD) did not work: the card
  stored the points as 0xAFCC, with no power.
- Galvos: frame 19000 (far from Modular's), points written one by one with WritePoint,
  NUMBERED FROM 1 (point 0 is ignored by the card). Fixed beam = 20 identical points.
- Angles (Modular TIRFFormules): in the glass; BFP radius fraction r = sin(angle) x 1.52 /
  1.49 of the calibration ellipse of the line (half axes ax, ay, centre cx, cy):
  point = (cx + r ax cos(direction), cy + r ay sin(direction)). Max 78.6 deg (NA 1.49),
  TIRF above the critical angle 62.6 deg (n medium 1.35).
- Galvo calibrations: each line has one (ilas_config.json "calibration"). Read from Modular's
  C:\Users\MFM-S30\Modular\TIRFCalibration.xml (active ones: 4 beam positions = left, right,
  top, bottom of the BFP edge; ellipse with axes along X and Y through them), or written in
  ilas_config.json "calibrations" {name: {"center": [x, y], "half_axes": [ax, ay]}}, which
  win. On 2026-11-03:
      "Calib 100x 1.49 NA" (validated): centre (2080, 1840), half axes (4046, 3920)
      "centrage" (not validated; used by Modular for the 561): centre (26.5, -26.5), half
      axes (3834, 3814)
  5 deg for the 561 nm = galvo (367, -26) along +X. The angles of a beam use the calibration
  of the first ticked line (or the line chosen in "Angles of"): one beam position for all
  the lines on together.
- Safety: laser off at connect, disconnect, Python exit (atexit) and after any error;
  refuses positions outside -8000...8000, angles above 78.6 deg, powers outside 0-100 %,
  laser on with no line ticked; unticking the last line switches the laser off. The card
  keeps playing its last frame if Python is killed: connect again (laser off) or switch the
  lasers off.

Adding a line (Oxxius 405, 488, 561, 640 nm on channels 1-4; e.g. a new 561 nm; 6 channels max)
---------------------------------------------------------------------
In ilas_config.json "lines", one entry per line, names and channels unique:
    {"name": "561 HP", "wavelength": 561, "channel": 6, "calibration": "centrage",
     "low": 0, "high": 100}
channel = the QM2000 output wired to the analog modulation input of that laser (check the
cabling; channels 1-4 are used by 405, 640, 561, 488 as in Modular, which calls the 640
642); calibration = the
name of its galvo calibration. Restart Bumblebee.

Bumblebee panel "Lasers (iLas)" (tabbed with the objective piezo)
-----------------------------------------------------------------
Connect (asks for confirmation; the lasers stay at power 0), the big Laser ON / OFF button
(red when on), one row per line (tick = used when on, power in %, channel and calibration in
grey), galvos: mode (fixed beam: angle + direction; 360 deg circle: half angle; fixed beam:
galvo X, Y), "Angles of" (line whose calibration is used), calibration and incidence shown.
"Calibration..." (to come): a window for the angle and power calibrations. Saved with each
acquisition: X.json > devices > lasers (lines, powers, laser on, galvo mode / angle / position,
calibration, incidence). Disconnect switches the lasers off.

Tests on the card (2026-11-03, 561 nm, Modular and MetaMorph closed)
--------------------------------------------------------------------
1. python ilas.py status: OK (serial 9437, 15000 points/s, laser off).
2. Power steps with the colour code 0xABCD: no power at the objective (code changed by the
   card). With Modular's code 1300: the power follows 0-1-3-10-3-0 % (channel 3 = 0, 3, 8,
   26, 8, 0). Galvos fixed: no noise.
3. 50 %, fixed beam at 5 deg (367, -26), then a 360 deg circle at 5 deg half angle: as
   Modular, no noise.
Still to do: the other lines (405, 488, 640), TIRF angles, check the calibrations (which one
is right for each line; measure them: future calibration window), the power in mW at the
objective vs % (calibration window).

Test with the stand-in (no card)
--------------------------------
    import ilas
    fake = ilas.FakeLD2000()
    d = ilas.ILas(backend=fake); d.connect(); d.enable("561"); d.set_power("561", 10)
    d.set_angle(5, 0); d.laser_on(); fake.emitted()   # {3: 26}: channel 3 at 26 / 255
