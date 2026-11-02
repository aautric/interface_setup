Calibrations of the setup (calibration.py)
==========================================

Launched from the calibration buttons of Bumblebee, in the upper right corner
of the window (end of the toolbar row, "Calibration:"; CalibrationPanel in
setup_gui.py): one button per calibration. Each button opens a step-by-step window
(CalibrationWizard, not modal: the rest of the GUI stays usable meanwhile):

  1. Optical elements   what to put in the light path; each must be ticked
                        before Next
  2. Devices            the devices needed, shown green (connected) or red,
                        with a Connect button each (or connect them in their
                        panel); checked every 0.5 s, Next once all connected
  3. Light path         each calibration is for one light path of the
                        microscope (Calibration.side): Left = red, Right =
                        green (also the colour of its button). The microscope
                        goes there when the page opens (button to do it
                        again); the user chooses the emission filter of that
                        path (button Show the filter wheels) and ticks. Next
                        once on that path and ticked (LightPathPage)
  4...                  the steps of the calibration (InstructionPage: text,
                        buttons acting on the GUI panels, a status refreshed
                        every 0.5 s, an optional box to tick before Next, an
                        action run by Next)

The window numbers the steps (titles without number). Closing it and
clicking the button again starts from step 1.

Calibration files: D:\BUMBLEBEE_calibration (CALIBRATION_ROOT of saving.py),
one folder per type of calibration, then one per day: <name>\YYYY_MM_DD (e.g.
align_BFP\2026_10_29; saving.calibration_folder; before 2026-11-02 it was
YYYY_MM_DD_<name>, folders moved); the recordings in it are numbered 0, 1, ... like
the acquisitions. Written directly on D: (no staging), independent of the
experiment name and subfolder of the main window.


Calibrations
------------
align_BFP ("Align BFP", red button): real position of the two back focal
planes (2 channels) on the SLM. Left path.
  1. Light path: a half-wave plate before the SLM, the 50/50 beam splitter, a
     polarization analyzer before the Orca.
  2. Devices: microscope, filter wheels, Prime, Orca, SLM.
  3. Light path: Left (red) = 2-Left100 (SIDE_LIGHT_PATHS; "Left" alone also
     matches 4-Left80), emission filter of the left path.
  4. TetraSpeck beads (size 1) in focus on the Prime: buttons Show the Prime,
     Start / stop Prime live; tick "in focus". Next stops the Prime live.
     No Perfect Focus step: the PFS does not lock on TetraSpeck beads (step
     removed 2026-10-29).
  5. Phase masks on the SLM recorded on the Orca (AlignBfpRecordPage). With
     45 deg light on the SLM, the quarter-wave plate + polarizer before the
     Orca turn the phase into contrast, I ~ (1 + sin phase) / 2.
     "Record the vortex masks": bfp_vortex_phase, in each half of the SLM a
     vortex centred on the nominal BFP centre (BFP_CENTERS, row 600, columns
     480 / 1440), the phase growing by "phase around the centre" (VORTEX_PHASE,
     10 pi) over one turn: contrast lines pointing to the centre on the Orca
     (5 periods for 10 pi). Then the same shifted by "vortex shift"
     (VORTEX_SHIFT, 50 px) along x, then along y: the scale and rotation SLM
     -> Orca; then the vortex in SLM half 0 only (half 1 at 0): which disk on
     the Orca is lit through which half (4 frames since 2026-10-29 evening;
     the first real recording has the first 3 only). Each mask can be put on
     the SLM before recording (list + "Show on the SLM", with the current
     settings) to look at it on the Orca live; it stays on the SLM. Only the
     shape matters: gray level 255 is taken as 2 pi
     (GRAY_2PI); the exact phase will come from a separate SLM phase
     calibration (estimate_gray_2pi, a fit of a uniform gray-level sweep, is
     kept for it; the sweep was removed from align_BFP on 2026-10-29).
     The recording = one acquisition of
     D:\BUMBLEBEE_calibration\align_BFP\YYYY_MM_DD (one frame per mask, X.json
     with "calibration" in its acquisition section, X_frames.csv with the mask
     of each frame, X_slm_masks\ the masks). The Orca live is paused
     meanwhile, the mask shown before is put back on the SLM after. Next once
     the vortex masks are recorded.
  6. Position of the BFPs on the SLM (AlignBfpAnalysisPage, bfp_analysis.py):
     "Analyse the recording" (the last one of step 5; "Analyse another
     recording..." for an older one) runs bfp_analysis.run in a thread (about
     20 s per disk). For each disk on the Orca:
       - edge: radial profiles, crossing of the level halfway between the
         local inside and outside (sub-pixel), robust ELLIPSE fit (the disk is
         not round on the Orca: the magnification differs ~3 % along x and
         y); again at 30 % and 70 % of the step: the spread = systematic error;
       - vortex centre on each frame: fit of background (quadratic) +
         a cos(5 t) + b sin(5 t) around (x0, y0), grid search + 2D parabola
         (centre and its error);
       - SLM -> Orca 2x2 matrix from the shifted vortices; BFP centre on the
         SLM = vortex centre (known) + inverse(matrix) (ellipse centre -
         vortex centre on the Orca);
       - SLM half: pinwheel still there in the "half 0 only" frame -> half 0.
     Written next to the recording: X_bfp_calibration_<YYYY_MM_DD_HHMMSS>.json
     (time of the analysis, a new analysis never overwrites one; per SLM half:
     bfp_center_slm_row_col, statistical / systematic errors, radius; per
     disk: all the details) and the same name .png (edge points, ellipse,
     vortex centres). "Link to the SLM masks" links it to the SLM panel
     (see "Calibrations linked to the SLM masks" below). Command line:
     python bfp_analysis.py <folder> [index] [SLM half of each disk from
     left to right, for old recordings].
  Tested 2026-10-29: step 4 with the real Prime; steps 3 and 5 with a
  simulated microscope / Orca / SLM; step 6 on a simulated recording (2
  disks, one mirrored: centres right to 0.1 px, halves found) and on the
  first real recording (align_BFP\2026_10_29\0, one full disk, 3 frames):
  BFP - vortex = +0.3 (x) / -30.9 (y) SLM px, statistical +-0.2, systematic
  +-2.0 in x (soft left edge of the disk) / +-0.2 in y, radius 466 SLM px,
  SLM -> Orca 0.778 / 0.800 Orca px per SLM px, rotation -1.5 deg.

slm_phase ("SLM phase", red button): phase applied by the SLM for each gray
level 0-255, for each Orca pixel of the BFP disks. The recording is the same
whatever the channels; the analysis takes by default only the right half of
the Orca: the whole disk centred there, even past the middle (one channel,
for the first tests; "Both channels" in the analysis
step, or "both" on the command line, for the two). Same optics as
align_BFP; the analyzer before the Orca = the quarter-wave plate in its
rotation mount (turned by hand) + the polarizer. Steps:
  1-3. optical elements, devices (microscope, filter wheels, Prime, Orca,
     SLM), light path left + emission filter;
  4. wavelength (nm, 400-800) of the light reaching the Orca: gives the
     retardance of the quarter-wave plate (table of polarimetre.py, the
     polarimetry repository, aautric/polarimetry);
  5. TetraSpeck beads in focus on the Prime (Next stops the Prime live);
  6. recording: for each plate angle 0, 40... 320 deg (9 angles), a window
     says "Turn the quarter-wave plate to X deg", then the SLM shows the flat
     masks 0, step, 2 step... 255 and the Orca takes one image of each
     ("gray step", default 4: 65 images; the same for all the angles of a
     run: changing it after recording angles starts a new run, after a
     question), one acquisition of
     D:\BUMBLEBEE_calibration\slm_phase\YYYY_MM_DD; X.json > acquisition >
     calibration: run, qwp_angle_deg, wavelength_nm, qwp_retardance_waves,
     gray_step, levels. "Record the remaining angles" goes through the
     angles not recorded yet (Cancel in the window stops; continue later);
     "Record the selected angle again" (the analysis takes the last
     recording of each angle). Gray level + "Show flat mask on the SLM" to
     check the exposure before; a warning if a frame is saturated (>= 65000).
     The Orca exposure can be set in the recording steps of align_BFP and
     slm_phase ("exposure ... ms", the same setting as in the Orca panel).
  7. analysis (slm_phase_analysis.py, in the background; Orca pixels 1 x 1,
     2 x 2, 4 x 4, 8 x 8 or 16 x 16):
       - lit pixels = the BFP disks (smoothed mean image above half way
         between the median and the 99.5th percentile), disks sorted left to
         right; dark level (camera offset ~100 + stray light) = median of
         the image away from the disks, subtracted;
       - per pixel and gray level: Fourier coefficients over the 9 angles
         (p0, p2, q2, p4, q4), plate zero offset d (p2 = q2 tan 2d) fitted on
         all pixels and gray levels, Stokes S0-S3 as in polarimetre.py;
       - the Stokes vector, normalized by the pixel's S0 averaged over the
         gray levels, moves on a circle of the Poincare sphere as g goes
         0 -> 255 (rotation about the axis of the SLM liquid crystal, the same
         for all pixels): the axis = normal of the planes of the circles,
         fitted once on all pixels; phase(g) = angle turned about the axis
         from g = 0, unwrapped along the median curve of the disk (each pixel
         within +-pi of it), positive. (Since 2026-11-02: the first real
         recording was too faint for a fit per single Orca pixel.) The optics after the SLM only rotate the sphere (phase
         unchanged); unpolarized / unmodulated light makes the circle smaller
         ("modulation"), not the phase wrong.
       - written next to the recordings: <first>-<last>_slm_phase_<date_time>
         .json (plate offset, per disk: median phase curve with 25-75 %,
         phase at 255, gray level of 2 pi, modulation, degree of
         polarization), .npz (per lit Orca pixel: rows, cols, disk, phase
         levels x pixels in rad, levels, axis, modulation, plane_rms, mean_intensity),
         .png (curves and maps).
  Only phase differences from gray 0 are measured. No correction of a drift
  of the light between the sequences (postponed). Next: interpolation from
  the Orca pixels to the SLM pixels (with the align_BFP mapping).
  Tested 2026-10-30 with simulated recordings (light at 50 deg of the SLM
  axis, 30 % unpolarized, unknown retarder after the SLM, plate zero 7 deg,
  photon noise): plate zero 7.00 deg, phase per pixel rms error 0.04 rad
  with no bias, gray of 2 pi 227.0 (true 226.96); the window with a
  simulated Orca and SLM (9 angles, one recorded again, analysis).
  2026-11-02: gray step 4 gives the same accuracy (rms 0.04 rad, gray of 2 pi
  within 0.1) as every gray level; tested with a single disk too.
  First real recording 2026-11-02 (D:\BUMBLEBEE_calibration\slm_phase\2026_11_02,
  right channel, 640 nm, gray step 4, ~1 min per angle): 2 pi at gray
  253.5, 6.32 rad at 255, nearly linear; signal only ~20-55 counts above the
  offset: noise 1.2 rad per single Orca pixel and gray level, 0.12 rad over
  8 x 8 Orca pixels.
  Recording time with the real devices: not measured yet (number of levels x
  (SLM settle time + exposure) per angle).

Note: in the GUI environment (setup_env), numpy matrix products (@, dot) and
np.linalg crash Python (broken BLAS): the calculations here use einsum and
explicit formulas.


Calibrations linked to the SLM masks
------------------------------------
Each time a mask is loaded in the SLM panel (Load image / .npy...), a window
asks whether to link it to a calibration: for each kind (align_BFP, slm_phase) a
list of its calibration files in D:\BUMBLEBEE_calibration (newest first,
the one already linked selected), Browse..., then Link / No calibration /
Cancel (the mask is not loaded). "Calibration..." in the SLM panel changes
the link at any time (the mask is corrected again, and re-sent if it was on
the SLM); "Link to the SLM masks" in the last step of Align BFP / SLM phase
links the calibration just made. The corrections are applied in this order:
first align_BFP (geometry), then slm_phase (gray levels).
  - align_BFP: each SLM half (columns 0-959, 960-1919) is moved by the
    measured BFP centre - nominal centre of its channel (slm_half_0 /
    slm_half_1), rounded to whole pixels, so a mask drawn around the nominal
    centres (row 600, columns 480 / 1440) lands on the real BFPs. Each half
    stays in its own columns; the pixels left empty are 0. A file without
    channels (SLM half unknown) cannot be linked.
  - slm_phase (since 2026-11-02, global curve, right channel only): a mask
    gray g is read as the wanted phase 2 pi g / 255; on the right SLM half
    (half 1, the right channel), each g is replaced by the gray level that
    gives this phase on the median phase curve of the right disk of the
    result (inverted, rounded; 256-entry table). The left half is not changed.
    E.g. with 0-8_slm_phase_2026_11_02_154513: 64 -> 78, 128 -> 133,
    192 -> 197, 255 -> 254; residual 0.016 rad (rounding). Per SLM pixel
    later (Orca -> SLM mapping of align_BFP). Only the .json (+ .png) is
    copied with the acquisitions, not the .npz.
  - The link is shown in the top-left corner of the SLM display
    ("Calibration linked: <file name>", tooltip: the correction), and the
    mask line says "(corrected: align_BFP, slm_phase)".
  - Acquisitions taken while a corrected mask is on the SLM: the calibration
    file (+ its .png) is copied to X_calibrations\ next to X.json, and X.json
    > slm > linked_calibrations > align_BFP gives file (original), date,
    light_path, correction (shift of each half), copy (the copy). The masks
    saved in X_slm_masks are the corrected ones (as sent).
  - Masks put by a calibration window (e.g. the vortex masks of Align BFP)
    are never corrected; the corner then says "(not applied to this mask)".
  - A new kind: a LinkableCalibration(name, pattern of its files, check,
    correct, describe) in LINKABLE_CALIBRATIONS of calibration.py.
  Tested 2026-10-30 with a simulated calibration file (both halves): shifts,
  dialog, corner label, copy with an acquisition; 2026-11-02 with the real
  align_BFP 151017 + slm_phase 154513 files and a stand-in SLM (sent mask =
  shift then phase table, both files copied); not yet on the SLM.


Adding a calibration
--------------------
A Calibration(name, title, description, requirements, devices, side, steps)
in CALIBRATIONS of calibration.py; side "Left" or "Right" (needs the
microscope in devices) adds the light path step. devices are keys of the panels given by the
GUI (MainWindow.calibration_panel): microscope, orca, slm, prime, flir,
piezo, filter_wheels. steps(panels) returns the pages after the checks
(panels = {key: panel}); helpers: show_panel, start_live, stop_live,
toggle_live, live_status.
