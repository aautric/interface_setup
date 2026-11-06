"""
Calibrations of the setup, launched from the Calibration panel of the GUI (setup_gui.py).

Each calibration opens a step-by-step window (CalibrationWizard):
    1. the optical elements to put in the light path, each to be ticked once placed;
    2. the devices it needs, which must all be connected (Connect button for each);
    3. the light path of the calibration (each calibration is for one path of the microscope,
       Left = red or Right = green): the microscope goes there by itself, the user puts the
       emission filter of that path;
    4. its own steps (InstructionPage: text, buttons acting on the GUI panels, a live status,
       an optional box to tick, an action when Next is clicked).
The window numbers the steps.

Calibrations:
    align_BFP   (left path, red) position of the two back focal planes (2 channels) on the
                SLM: beads in focus on the Prime (no Perfect Focus: it does not lock on
                TetraSpeck beads), then phase masks on the SLM recorded on the Orca
                (half-wave plate before the SLM, quarter-wave plate + polarizer before the
                Orca: the phase becomes contrast): a vortex in each half of the SLM (phase
                10 pi around the centre: contrast lines pointing to the centre on the Orca),
                the same vortex shifted along x and along y (scale and rotation SLM ->
                Orca), then in SLM half 0 only (which disk is which half). Only the shape
                matters, not the exact 2 pi gray level (a separate SLM phase calibration will
                give it). Then bfp_analysis: BFP centre and radius on the SLM of each disk,
                written next to the recording; "Link to the SLM masks" links it to the SLM
                panel.

    slm_phase   (left path, red) phase applied by the SLM for each gray level, for each Orca
                pixel: same optics, the analyzer = quarter-wave plate turned by hand +
                polarizer. Wavelength chosen first (retardance of the plate); beads in focus;
                for each of the 9 plate angles (a window asks to turn it) flat masks 0...255
                recorded on the Orca; then slm_phase_analysis (polarimetry per pixel, phase =
                angle about the SLM axis on the Poincare sphere).

    channels_geometry (left path, red) correspondence between the two channels of the Prime
                (top = reference, x right y up; bottom, x right y down): the white lamp
                (recorded, N frames) gives the channels (where the image is cut in 2), a first
                guess of the mapping and the normalization of the channels; then one or more
                fields of TetraSpeck beads (size 4, N frames each); channels_geometry.py fits
                and pairs the beads and fits the mapping top -> bottom (affine: rotation,
                scales, shift; + a smooth residual field, local rotation). A mapping only: no
                image is corrected (no interpolation of the noise).

    input_polar_SLM (left path, red) polarization of the light reaching the SLM: the analyzer
                (quarter-wave plate turned by hand + polarizer) right before the SLM; wavelength
                chosen first; beads in focus; for each of the 9 plate angles a time series of N
                Orca frames (flat mask on the SLM); then input_polar_slm_analysis (Stokes vector
                per Orca pixel of the BFPs, azimuth / ellipticity / degree of polarization, and
                per disk their fluctuation in time; plate zero from the slm_phase result).

    aberrations (left path, red; DRAFT 2026-11-04) field-dependent aberrations: piezo z stacks
                of 200 nm TetraSpeck beads (several fields) without mask, a model-free first
                look (widths vs z, focus, astigmatism, asymmetry vs the field), z stacks with
                phase masks (double helix, Airy, tri-spot...), a correction of the common
                aberrations on the SLM (Zernike per SLM half, by hand for now), check stacks,
                a calibration file. The PSF fit (Zernike per bead, field model) is to come:
                plan in aberrations.py.

A new calibration = a new Calibration in CALIBRATIONS, with its steps.
Calibrations linked to the SLM masks (LINKABLE_CALIBRATIONS, LinkCalibrationDialog): when a
mask is loaded in the SLM panel, the user is asked to link it to a calibration file (or not).
A linked calibration corrects the masks sent to the SLM (align_BFP: each SLM half moved by the
BFP offset of its channel), is shown in the corner of the SLM display, and is copied with every
acquisition taken while the corrected mask is on the SLM (X_calibrations, see saving.py).
A new kind = a new LinkableCalibration in LINKABLE_CALIBRATIONS.
"""

import os
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

import saving
from saving import SequenceWriter, calibration_folder, next_index

SLM_SHAPE = (1200, 1920)  # height, width (EXULUS-HD2)
BFP_CENTERS = ((600, 480), (600, 1440))  # nominal BFP centres (row, column) on the SLM
VORTEX_PHASE = 10  # phase around the vortex centre, in units of pi
GRAY_2PI = 255  # nominal gray level of 2 pi (the exact one: SLM phase calibration, to come)
SWEEP_LEVELS = list(range(0, 256, 8)) + [255]  # uniform gray levels, for estimate_gray_2pi
VORTEX_SHIFT = 50  # px on the SLM, shift of the 2nd and 3rd vortex masks (x, then y)


# ---------------------------------------------------------------- align_BFP phase mask


def bfp_vortex_phase(centers=BFP_CENTERS, total_phase=VORTEX_PHASE * np.pi, shape=SLM_SHAPE):
    """
    Phase (rad, wrapped to [0, 2 pi)) of the align_BFP mask: in each half of the SLM (one per
    BFP), a vortex centred on the nominal BFP centre (row, column), the phase growing by
    total_phase over one turn around the centre (10 pi: 5 wraps, so 5 contrast periods on the
    Orca, lines pointing to the centre).
    """
    rows, cols = np.mgrid[0:shape[0], 0:shape[1]]
    phase = np.zeros(shape)
    half_width = shape[1] // len(centers)
    for k, (row0, col0) in enumerate(centers):
        half = (cols >= k * half_width) & (cols < (k + 1) * half_width)
        azimuth = np.arctan2(rows - row0, cols - col0) % (2 * np.pi)
        phase[half] = (azimuth[half] * total_phase / (2 * np.pi)) % (2 * np.pi)
    return phase


def phase_to_gray(phase, gray_2pi=GRAY_2PI):
    """Gray levels of a phase (rad), gray_2pi = level of 2 pi (from the SLM calibration)."""
    return np.clip(np.round(phase / (2 * np.pi) * gray_2pi), 0, 255).astype(np.uint8)


def vortex_masks(total_phase_pi=VORTEX_PHASE, shift=VORTEX_SHIFT, centers=BFP_CENTERS,
                 gray_2pi=GRAY_2PI):
    """[(name, gray mask)]: the vortex (total_phase_pi x pi around the centre) at the nominal
    centres, then shifted by shift px along x (columns), then along y (rows), then the vortex
    in SLM half 0 only (half 1 uniform 0: tells which disk on the Orca is lit through which
    half, bfp_analysis)."""
    masks = []
    for name, (d_row, d_col) in (("vortex", (0, 0)), (f"vortex_x+{shift}", (0, shift)),
                                 (f"vortex_y+{shift}", (shift, 0))):
        moved = [(row + d_row, col + d_col) for row, col in centers]
        phase = bfp_vortex_phase(moved, total_phase_pi * np.pi)
        masks.append((name, phase_to_gray(phase, gray_2pi)))
    half0 = masks[0][1].copy()
    half0[:, SLM_SHAPE[1] // len(centers):] = 0
    masks.append(("vortex_half0_only", half0))
    return masks


def estimate_gray_2pi(levels, images, max_pixels=20000, periods=np.arange(64, 1025)):
    """
    Not used by align_BFP any more; kept for the SLM phase calibration to come.
    Gray level of a 2 pi phase from a sweep of uniform masks (e.g. SWEEP_LEVELS): with the
    quarter-wave plate and polarizer, the intensity of each lit pixel oscillates as
    a + b sin(2 pi g / period + c). The period common to all pixels is fitted (each pixel has
    its own a, b, c, so a channel with inverted contrast does not cancel the other).
    Returns (period in gray levels, fraction of the intensity variance explained by the fit).
    A period above 255: the full range of the SLM is less than 2 pi at this wavelength.
    """
    stack = np.asarray(images, dtype=float).reshape(len(images), -1)
    mean = stack.mean(axis=0)
    median, high = np.percentile(mean, [50, 99.5])
    lit = mean > median + 0.5 * (high - median)  # the BFP disks, not the dark background
    pixels = np.flatnonzero(lit)
    if len(pixels) > max_pixels:
        pixels = np.random.default_rng(0).choice(pixels, max_pixels, replace=False)
    y = stack[:, pixels]
    y = y - y.mean(axis=0)
    total = (y ** 2).sum()
    if total == 0:
        return float("nan"), 0.0
    g = np.asarray(levels, dtype=float)
    best = (float("nan"), -1.0)
    # least squares on (sin, cos) solved by hand with einsum: matrix products (@) and
    # np.linalg crash Python in the GUI environment (broken BLAS of setup_env)
    for period in periods:
        s, c = np.sin(2 * np.pi * g / period), np.cos(2 * np.pi * g / period)
        s, c = s - s.mean(), c - c.mean()
        ss, cc, sc = (s * s).sum(), (c * c).sum(), (s * c).sum()
        det = ss * cc - sc * sc
        if det <= 1e-12:
            continue
        bs, bc = np.einsum("i,ij->j", s, y), np.einsum("i,ij->j", c, y)
        fitted = ((cc * bs * bs - 2 * sc * bs * bc + ss * bc * bc) / det).sum()
        explained = fitted / total
        if explained > best[1]:
            best = (float(period), float(explained))
    return best

DEVICE_CHECK_PERIOD = 500  # ms between two checks of the connections / statuses
FIGURE_SIZE = 280  # px, figure of the BFP analysis in its page
OK_COLOR, MISSING_COLOR, NEUTRAL_COLOR = "#388e3c", "#d32f2f", "#555555"  # green, red, grey
SIDE_COLORS = {"Left": "#d32f2f", "Right": "#388e3c"}  # as LIGHT_PATH_COLORS of setup_gui
SIDE_COLOR_NAMES = {"Left": "red", "Right": "green"}
# light path of each side (Ti labels 1-Eye100, 2-Left100, 3-Right100, 4-Left80: "Left" alone
# matches two of them); all the light to that port
SIDE_LIGHT_PATHS = {"Left": "Left100", "Right": "Right100"}


@dataclass
class Calibration:
    name: str          # e.g. "align_BFP"
    title: str         # button text
    description: str
    requirements: list = field(default_factory=list)  # optical elements to put in the light path
    devices: list = field(default_factory=list)       # keys of the GUI panels needed
    side: str = None   # light path of the microscope: "Left" (red) or "Right" (green)
    # function(panels, tools) -> list of pages after the checks; panels: {key: panel},
    # tools: {name: function} of the main window (extra_metadata) + "side" of the calibration
    steps: object = None


# ---------------------------------------------------------------- actions on the GUI panels


def show_panel(panel):
    """Bring the dock window of a panel to the front (its tab, if tabbed)."""
    widget = panel
    while widget is not None and not isinstance(widget, QtWidgets.QDockWidget):
        widget = widget.parentWidget()
    if widget is not None:
        widget.show()
        widget.raise_()


def start_live(panel):
    if panel.device is not None and not panel.device.live:
        panel.toggle_live()  # shows the error if it fails


def stop_live(panel):
    if panel.device is not None and panel.device.live:
        panel.stop_live()


def toggle_live(panel):
    if panel.device is not None:
        panel.toggle_live()


def exposure_box(camera):
    """
    Spin box of the exposure of a camera panel, for the recording steps: the same setting as
    the Exposure of the panel (applied to the camera when changed, also during live). Call
    its sync() regularly to show a change made in the panel.
    """
    box = QtWidgets.QSpinBox()
    box.setRange(camera.exposure.minimum(), camera.exposure.maximum())
    box.setPrefix("exposure ")
    box.setSuffix(" ms")
    box.setValue(camera.exposure.value())
    box.setToolTip("Exposure time of the camera (same setting as in its panel); keep it the same "
                   "for all the recordings of a calibration")

    def apply():
        if box.value() != camera.exposure.value():
            camera.exposure.setValue(box.value())
            camera.apply_exposure()

    def sync():
        if not box.hasFocus() and box.value() != camera.exposure.value():
            box.setValue(camera.exposure.value())

    box.editingFinished.connect(apply)
    box.sync = sync
    return box


def type_folder(name):
    """Folder of the calibrations of type name (where the file dialogs open), else the root."""
    folder = os.path.join(saving.CALIBRATION_ROOT, name)
    return folder if os.path.isdir(folder) else saving.CALIBRATION_ROOT


def live_status(panel, name):
    if panel.device is None:
        return f"{name}: not connected", MISSING_COLOR
    return (f"{name} live: running", OK_COLOR) if panel.device.live else \
        (f"{name} live: stopped", NEUTRAL_COLOR)


def record_masks(parent, orca, slm, n_masks, mask_of, folder, extra, label, on_image=None):
    """
    For k = 0... n_masks - 1: show mask_of(k) = (name, mask) on the SLM (show_mask waits the
    SLM settle time), take one Orca image; all saved as one acquisition of folder (X_0.tiff,
    X.json with extra, X_frames.csv, X_slm_masks/). Progress window with Stop; the Orca live
    is paused meanwhile and the SLM mask shown before is put back after.
    on_image(k, image): optional. Returns (number of images taken, writer).
    """
    before = slm.shown_mask()
    progress = QtWidgets.QProgressDialog(f"{label}...", "Stop", 0, n_masks, parent)
    progress.setWindowModality(QtCore.Qt.WindowModal)
    progress.setMinimumDuration(0)
    done = 0

    def acquire():
        nonlocal done
        writer = SequenceWriter(folder, extra, None, 16, index=next_index(folder),
                                slm_mask=slm.shown_mask)
        with writer:
            for k in range(n_masks):
                name, mask = mask_of(k)
                progress.setLabelText(f"{label}: {name} ({k + 1} / {n_masks})")
                progress.setValue(k)
                QtWidgets.QApplication.processEvents()
                if progress.wasCanceled():
                    break
                slm.show_mask(mask, name)
                image = orca.device.snap()
                if k == 0:
                    writer.start(orca.device.last_metadata)
                writer.write(image)
                done += 1
                if on_image is not None:
                    on_image(k, image)
                orca.display(image)
        return writer

    try:
        writer = orca.run_paused(acquire)  # live stopped meanwhile
    finally:
        progress.close()
        if before is not None:
            slm.show_mask(*before)
    return done, writer


# ---------------------------------------------------------------- align_BFP


def align_bfp_steps(panels, tools):
    prime = panels["prime"]
    steps = [
        InstructionPage(
            "Beads in focus on the Prime",
            "Put a sample of TetraSpeck beads (size 1) on the microscope and bring the beads in "
            "focus on the Prime (live). No Perfect Focus: it does not lock on TetraSpeck beads."
            "<br><b>Next stops the Prime live.</b>",
            actions=[("Show the Prime", lambda: show_panel(prime)),
                     ("Start / stop Prime live", lambda: toggle_live(prime))],
            status=lambda: live_status(prime, "Prime"),
            confirm="The beads are in focus on the Prime.",
            on_next=lambda: stop_live(prime)),
    ]
    record = AlignBfpRecordPage(panels, tools)
    return steps + [record, AlignBfpAnalysisPage(record, tools)]


class AlignBfpRecordPage(QtWidgets.QWizardPage):
    """
    Step 5 of align_BFP: phase masks on the SLM recorded on the Orca, each recording saved as
    an acquisition of CALIBRATION_ROOT/align_BFP/YYYY_MM_DD (X_0.tiff: one frame per mask,
    X.json, X_frames.csv with the mask of each frame, X_slm_masks/ the masks).
    masks: the vortex at the nominal centres, then shifted along x and along y. Each can be
    shown on the SLM before recording (to look at it on the Orca live).
    The Orca live is paused meanwhile and the mask shown before is put back on the SLM after.
    """

    def __init__(self, panels, tools):
        super().__init__()
        self.setTitle("Phase masks on the SLM, recorded on the Orca")
        self.orca, self.slm = panels["orca"], panels["slm"]
        self.tools = tools
        self.vortex_saved = False
        self.last_recording = None  # (folder, index) of the last vortex recording
        text = QtWidgets.QLabel(
            "Look at the Orca (live): the two BFP disks must be visible and not saturated. "
            "<b>Show on the SLM</b> puts one of the masks on the SLM to check it on the Orca "
            "live. Then record the <b>vortex masks</b>: in each half of the SLM a vortex (the "
            "phase grows by the value below around the centre: contrast lines pointing to the centre "
            "on the Orca), then the same shifted along x and along y, then in SLM half 0 only "
            "(tells which disk is which half). Only the shape matters: "
            "gray level 255 is taken as 2&pi;.<br>Saved in "
            f"<code>{saving.CALIBRATION_ROOT}\\align_BFP\\YYYY_MM_DD</code>. The SLM mask shown "
            "before is put back after.")
        text.setWordWrap(True)
        self.total_phase = QtWidgets.QSpinBox()
        self.total_phase.setRange(2, 40)
        self.total_phase.setSingleStep(2)
        self.total_phase.setValue(VORTEX_PHASE)
        self.total_phase.setSuffix(" π")
        self.total_phase.setToolTip("Phase over one turn around the vortex centre "
                                    "(10 π: 5 contrast periods around the centre)")
        self.shift = QtWidgets.QSpinBox()
        self.shift.setRange(5, 400)
        self.shift.setValue(VORTEX_SHIFT)
        self.shift.setSuffix(" px")
        self.preview = QtWidgets.QComboBox()
        self.preview.addItems(["vortex", "vortex shifted along x", "vortex shifted along y",
                               "vortex in SLM half 0 only"])
        self.preview.setToolTip("Mask put on the SLM by Show on the SLM")
        buttons = QtWidgets.QHBoxLayout()
        for label, function in (("Show the Orca", lambda: show_panel(self.orca)),
                                ("Start / stop Orca live", lambda: toggle_live(self.orca)),
                                ("Record the vortex masks", self.record_vortex)):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(lambda _, f=function: self.run(f))
            buttons.addWidget(button)
        self.exposure = exposure_box(self.orca)
        buttons.insertWidget(2, self.exposure)
        buttons.addStretch()
        settings = QtWidgets.QHBoxLayout()
        settings.addWidget(QtWidgets.QLabel("phase around the centre"))
        settings.addWidget(self.total_phase)
        settings.addWidget(QtWidgets.QLabel("   vortex shift"))
        settings.addWidget(self.shift)
        settings.addStretch()
        preview = QtWidgets.QHBoxLayout()
        preview.addWidget(self.preview)
        show = QtWidgets.QPushButton("Show on the SLM")
        show.setToolTip("Put this mask on the SLM now (with the settings above), to look at it "
                        "on the Orca live before recording")
        show.clicked.connect(lambda: self.run(self.show_preview))
        preview.addWidget(show)
        preview.addStretch()
        self.status = QtWidgets.QLabel()
        self.result = QtWidgets.QLabel()
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout = QtWidgets.QVBoxLayout(self)
        for item in (text, buttons, settings, preview, self.status, self.result):
            (layout.addLayout if isinstance(item, QtWidgets.QLayout) else layout.addWidget)(item)
        layout.addStretch()
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(DEVICE_CHECK_PERIOD)
        self.timer.timeout.connect(self.update_status)

    def initializePage(self):
        self.update_status()
        self.timer.start()

    def cleanupPage(self):
        self.timer.stop()

    def show_preview(self):
        """Put the mask chosen in the list on the SLM (it stays there until changed)."""
        masks = vortex_masks(self.total_phase.value(), self.shift.value())
        name, mask = masks[self.preview.currentIndex()]
        self.slm.show_mask(mask, name)
        self.result.setText(f"SLM: {name} ({self.total_phase.value()} π around the centre).")

    def update_status(self):
        text, color = live_status(self.orca, "Orca")
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color}; font-weight: bold")
        self.exposure.sync()

    def isComplete(self):
        return self.vortex_saved

    def validatePage(self):
        self.timer.stop()
        return True

    def run(self, function):
        try:
            function()
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "Error", str(error))
        self.update_status()

    def record(self, masks, recording):
        """Show each (name, mask) on the SLM, take one Orca image, save all as one acquisition
        of CALIBRATION_ROOT/align_BFP/YYYY_MM_DD. Returns the writer; None if stopped."""
        extra = self.tools["extra_metadata"]()
        extra["calibration"] = {"name": "align_BFP", "light_path": self.tools.get("side"),
                                "recording": recording,
                                "total_phase_pi": self.total_phase.value(),
                                "gray_2pi": GRAY_2PI, "shift_px": self.shift.value(),
                                "nominal_centers_row_col": [list(c) for c in BFP_CENTERS]}
        folder = calibration_folder("align_BFP")
        done, writer = record_masks(self, self.orca, self.slm, len(masks), lambda k: masks[k],
                                    folder, extra, recording)
        if done < len(masks):
            self.result.setText(f"{recording}: stopped after {done} of {len(masks)} masks.")
            return None
        return writer

    def record_vortex(self):
        writer = self.record(vortex_masks(self.total_phase.value(), self.shift.value()),
                             "vortex masks")
        if writer is None:
            return
        self.last_recording = (writer.folder, writer.index)  # analysed by the next step
        self.vortex_saved = True
        self.completeChanged.emit()
        self.result.setText(f"Vortex masks saved as acquisition {writer.index} in {writer.folder} "
                            f"(phase {self.total_phase.value()} π around the centre, shift "
                            f"{self.shift.value()} px).")


class AlignBfpAnalysisPage(QtWidgets.QWizardPage):
    """
    Last step of align_BFP: bfp_analysis.run on the vortex recording (in a thread: ~25 s per
    disk), which writes X_bfp_calibration_<YYYY_MM_DD_HHMMSS>.json and .png next to it; the
    result is shown, and "Link to the SLM masks" links it to the SLM panel (tools
    "link_calibration"): the masks loaded there are corrected with it.
    """

    def __init__(self, record_page, tools):
        super().__init__()
        self.setTitle("Position of the BFPs on the SLM")
        self.record_page, self.tools = record_page, tools
        self.recording = None   # (folder, index) analysed
        self.path = None        # its calibration file
        self.thread = None
        text = QtWidgets.QLabel(
            "Each BFP disk on the Orca: its edge fitted by an ellipse, and the centre of the "
            "5-fold pattern of the vortex; the shifted vortices give the scale and rotation "
            "SLM &rarr; Orca, so the BFP centre is found on the SLM. The result is written next "
            "to the recording (<code>X_bfp_calibration_&lt;date_time&gt;.json</code> and <code>.png</code>).")
        text.setWordWrap(True)
        buttons = QtWidgets.QHBoxLayout()
        self.analyse_button = QtWidgets.QPushButton("Analyse the recording")
        self.analyse_button.clicked.connect(lambda: self.analyse(self.record_page.last_recording))
        choose = QtWidgets.QPushButton("Analyse another recording...")
        choose.clicked.connect(self.choose)
        self.use_button = QtWidgets.QPushButton("Link to the SLM masks")
        self.use_button.setToolTip("The masks of the SLM panel are then corrected with this "
                                   "calibration (each SLM half moved onto its BFP)")
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self.use)
        for button in (self.analyse_button, choose, self.use_button):
            buttons.addWidget(button)
        buttons.addStretch()
        self.result = QtWidgets.QLabel()
        self.result.setTextFormat(QtCore.Qt.RichText)
        self.result.setWordWrap(True)
        self.result.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        self.result.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.figure = QtWidgets.QLabel()
        self.figure.setFixedSize(FIGURE_SIZE, FIGURE_SIZE)
        self.figure.setAlignment(QtCore.Qt.AlignCenter)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.result, 1)
        row.addWidget(self.figure, 0, QtCore.Qt.AlignTop)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(text)
        layout.addLayout(buttons)
        layout.addLayout(row, 1)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.check_thread)

    def initializePage(self):
        self.analyse_button.setEnabled(self.record_page.last_recording is not None)

    def choose(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "align_BFP recording", type_folder("align_BFP"), "Recordings (*_0.tiff)")
        if path:
            folder, name = os.path.split(path)
            self.analyse((folder, int(name.split("_")[0])))

    def analyse(self, recording):
        import threading
        import bfp_analysis
        if recording is None or self.thread is not None:
            return
        self.recording, self.path, self.outcome = recording, None, None
        self.use_button.setEnabled(False)
        self.analyse_button.setEnabled(False)
        self.result.setText(f"Analysing acquisition {recording[1]} of {recording[0]}... "
                            "(about 25 s per disk)")
        self.figure.clear()

        def work():
            try:
                self.outcome = bfp_analysis.run(*recording)
            except Exception as error:
                self.outcome = error

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        self.timer.start()

    def check_thread(self):
        if self.thread is None or self.thread.is_alive():
            return
        self.timer.stop()
        self.thread = None
        self.analyse_button.setEnabled(self.record_page.last_recording is not None)
        if isinstance(self.outcome, Exception):
            self.result.setText(f"<b>Analysis failed</b>: {self.outcome}")
            return
        self.path, content = self.outcome
        lines = []
        for disk in content["disks"]:
            if "error" in disk:
                lines.append(f"Disk at {disk['orca_rough_center_xy']}: not analysed "
                             f"({disk['error']})")
                continue
            dx, dy = disk["bfp_minus_vortex_slm_x_y"]
            half = disk.get("slm_half")
            lines.append(f"Disk at Orca ({disk['orca_disk']['center_xy'][0]:.0f}, "
                         f"{disk['orca_disk']['center_xy'][1]:.0f}), SLM half "
                         f"{'unknown' if half is None else half}: BFP - vortex = "
                         f"{dx:+.1f} px (x), {dy:+.1f} px (y)")
        for name, channel in content["channels"].items():
            (row, col), (srow, scol) = channel["bfp_center_slm_row_col"], \
                channel["systematic_error_row_col"]
            stat = channel["statistical_error_row_col"]
            lines.append(f"<b>{name}: BFP centre row {row:.1f}, column {col:.1f}</b> "
                         f"(&plusmn; {stat[0]:.1f} / {stat[1]:.1f} statistical, "
                         f"&plusmn; {srow:.1f} / {scol:.1f} systematic), radius "
                         f"{channel['bfp_radius_slm_px']:.0f} px")
        if not content["channels"]:
            lines.append("No SLM half known for the disks (recording without the "
                         "<i>vortex_half0_only</i> frame): record again.")
        lines.append(f"Written: {self.path}")
        self.result.setText("<br>".join(lines))
        self.use_button.setEnabled(bool(content["channels"]))
        picture = self.path.replace(".json", ".png")
        pixmap = QtGui.QPixmap(picture)
        if not pixmap.isNull():
            self.figure.setPixmap(pixmap.scaled(FIGURE_SIZE, FIGURE_SIZE,
                                                QtCore.Qt.KeepAspectRatio,
                                                QtCore.Qt.SmoothTransformation))

    def use(self):
        try:
            self.tools["link_calibration"]("align_BFP", self.path)
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "Error", str(error))
            return
        self.result.setText(self.result.text() + f"<br><b>Linked to the SLM masks</b>: "
                            f"{os.path.basename(self.path)}")
        self.use_button.setEnabled(False)

    def cleanupPage(self):
        self.timer.stop()


# ---------------------------------------------------------------- slm_phase

SATURATION = 65000  # Orca counts (16 bit): above, a pixel is taken as saturated


def flat_mask(level):
    return np.full(SLM_SHAPE, level, dtype=np.uint8)


def slm_phase_steps(panels, tools):
    prime = panels["prime"]
    state = {"wavelength_nm": 640, "run": None}  # shared by the pages of this window
    record = SlmPhaseRecordPage(panels, tools, state)
    return [
        WavelengthPage(state),
        InstructionPage(
            "Beads in focus on the Prime",
            "Put a sample of TetraSpeck beads (size 1) on the microscope and bring the beads in "
            "focus on the Prime (live). No Perfect Focus: it does not lock on TetraSpeck beads."
            "<br><b>Next stops the Prime live.</b>",
            actions=[("Show the Prime", lambda: show_panel(prime)),
                     ("Start / stop Prime live", lambda: toggle_live(prime))],
            status=lambda: live_status(prime, "Prime"),
            confirm="The beads are in focus on the Prime.",
            on_next=lambda: stop_live(prime)),
        record,
        SlmPhaseAnalysisPage(record, state),
    ]


class WavelengthPage(QtWidgets.QWizardPage):
    """Wavelength of the light analysed: gives the retardance of the quarter-wave plate."""

    def __init__(self, state):
        super().__init__()
        import slm_phase_analysis
        self.state = state
        self.setTitle("Wavelength")
        text = QtWidgets.QLabel(
            "Wavelength of the light reaching the Orca (centre of the emission filter chosen at "
            "the previous step). It gives the retardance of the quarter-wave plate (table of "
            "the polarimetry repository, <code>polarimetre.py</code>), used by the analysis.")
        text.setWordWrap(True)
        self.wavelength = QtWidgets.QSpinBox()
        self.wavelength.setRange(slm_phase_analysis.QWP_WAVELENGTHS[0],
                                 slm_phase_analysis.QWP_WAVELENGTHS[-1])
        self.wavelength.setSuffix(" nm")
        self.wavelength.setValue(state["wavelength_nm"])
        self.retardance = QtWidgets.QLabel()
        self.wavelength.valueChanged.connect(self.update_retardance)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Wavelength"))
        row.addWidget(self.wavelength)
        row.addWidget(self.retardance)
        row.addStretch()
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(text)
        layout.addLayout(row)
        layout.addStretch()
        self.update_retardance()

    def update_retardance(self):
        import slm_phase_analysis
        waves = slm_phase_analysis.qwp_retardance(self.wavelength.value())
        self.state["wavelength_nm"] = self.wavelength.value()
        self.retardance.setText(f"   quarter-wave plate: {waves:.4f} wave ({360 * waves:.1f}°)")


class SlmPhaseRecordPage(QtWidgets.QWizardPage):
    """
    slm_phase recording: for each angle of the quarter-wave plate (turned by hand: a window
    asks for each one), the flat masks 0, step, 2 step... 255 on the SLM (gray step chosen
    here, the same for all the angles of a run), one Orca image each, saved as
    one acquisition of CALIBRATION_ROOT/slm_phase/YYYY_MM_DD (calibration "run": the same for
    the 9 angles of this window). An angle can be recorded again (the analysis takes the last).
    """

    def __init__(self, panels, tools, state):
        super().__init__()
        import slm_phase_analysis
        self.angles = slm_phase_analysis.QWP_ANGLES
        self.setTitle("Gray level ramp on the SLM, for each angle of the quarter-wave plate")
        self.orca, self.slm = panels["orca"], panels["slm"]
        self.tools, self.state = tools, state
        self.recorded = {}  # angle -> (folder, index)
        text = QtWidgets.QLabel(
            f"For each angle of the quarter-wave plate ({', '.join(map(str, self.angles))}°), "
            f"the SLM shows flat masks of gray level 0, step, 2&times;step&hellip; 255, one "
            f"Orca image each (gray step below, the same for all the angles). <b>The plate is "
            f"turned by hand</b>: a window tells the angle before each recording. Before: check "
            f"on the Orca live that the disks (one per channel lit) are not saturated (flat "
            f"masks below, at a few plate angles). The Orca settings must stay the same for all "
            f"the angles.<br>Saved in "
            f"<code>{saving.CALIBRATION_ROOT}\\slm_phase\\YYYY_MM_DD</code>.")
        text.setWordWrap(True)
        buttons = QtWidgets.QHBoxLayout()
        for label, function in (("Show the Orca", lambda: show_panel(self.orca)),
                                ("Start / stop Orca live", lambda: toggle_live(self.orca))):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(lambda _, f=function: self.run(f))
            buttons.addWidget(button)
        self.exposure = exposure_box(self.orca)
        buttons.addWidget(self.exposure)
        self.level = QtWidgets.QSpinBox()
        self.level.setRange(0, 255)
        self.level.setPrefix("gray ")
        show = QtWidgets.QPushButton("Show flat mask on the SLM")
        show.clicked.connect(lambda: self.run(
            lambda: self.slm.show_mask(flat_mask(self.level.value()), f"flat_{self.level.value()}")))
        buttons.addWidget(self.level)
        buttons.addWidget(show)
        buttons.addStretch()
        self.table = QtWidgets.QTableWidget(len(self.angles), 2)
        self.table.setHorizontalHeaderLabels(["Plate angle", "Recording"])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        for row, angle in enumerate(self.angles):
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(f"{angle}°"))
        record_buttons = QtWidgets.QHBoxLayout()
        self.step = QtWidgets.QSpinBox()
        self.step.setRange(1, 64)
        self.step.setValue(slm_phase_analysis.GRAY_STEP)
        self.step.setPrefix("gray step ")
        self.step.setToolTip("Gray levels recorded: 0, step, 2 step... and 255")
        self.n_images = QtWidgets.QLabel()
        self.step.valueChanged.connect(self.update_n_images)
        record_buttons.addWidget(self.step)
        record_buttons.addWidget(self.n_images)
        remaining = QtWidgets.QPushButton("Record the remaining angles")
        remaining.clicked.connect(lambda: self.run(self.record_remaining))
        again = QtWidgets.QPushButton("Record the selected angle again")
        again.clicked.connect(lambda: self.run(self.record_selected))
        record_buttons.addWidget(remaining)
        record_buttons.addWidget(again)
        record_buttons.addStretch()
        self.status = QtWidgets.QLabel()
        self.result = QtWidgets.QLabel()
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout = QtWidgets.QVBoxLayout(self)
        for item in (text, buttons, self.table, record_buttons, self.status, self.result):
            (layout.addLayout if isinstance(item, QtWidgets.QLayout) else layout.addWidget)(item)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(DEVICE_CHECK_PERIOD)
        self.timer.timeout.connect(self.update_status)
        self.update_table()
        self.update_n_images()

    @property
    def levels(self):
        import slm_phase_analysis
        return slm_phase_analysis.gray_levels(self.step.value())

    def update_n_images(self):
        self.n_images.setText(f"{len(self.levels)} images per angle")

    def initializePage(self):
        self.update_status()
        self.timer.start()

    def cleanupPage(self):
        self.timer.stop()

    def validatePage(self):
        self.timer.stop()
        return True

    def isComplete(self):
        return all(angle in self.recorded for angle in self.angles)

    def update_status(self):
        text, color = live_status(self.orca, "Orca")
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color}; font-weight: bold")
        self.exposure.sync()

    def update_table(self):
        for row, angle in enumerate(self.angles):
            done = self.recorded.get(angle)
            item = QtWidgets.QTableWidgetItem(
                f"acquisition {done[1]}" if done else "not recorded")
            item.setForeground(QtGui.QColor(OK_COLOR if done else NEUTRAL_COLOR))
            self.table.setItem(row, 1, item)

    def run(self, function):
        try:
            function()
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "Error", str(error))
        self.update_status()

    def record_remaining(self):
        for angle in self.angles:
            if angle not in self.recorded and not self.record_angle(angle):
                return

    def record_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            raise ValueError("Select an angle in the table first.")
        self.record_angle(self.angles[rows[0].row()])

    def record_angle(self, angle):
        """Ask the user to turn the plate, then record the ramp. False if cancelled / stopped."""
        import slm_phase_analysis
        answer = QtWidgets.QMessageBox.question(
            self, "Quarter-wave plate",
            f"<b>Turn the quarter-wave plate to {angle}°.</b><br><br>Then click OK to record "
            f"the {len(self.levels)} gray levels.",
            QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel)
        if answer != QtWidgets.QMessageBox.Ok:
            return False
        levels = self.levels
        if self.state["run"] is not None and levels != self.state.get("levels"):
            if self.recorded:
                answer = QtWidgets.QMessageBox.question(
                    self, "Gray step changed",
                    f"The angles already recorded used the gray step "
                    f"{self.state.get('step')}. All the angles must use the same gray levels: "
                    f"start a new run with the gray step {self.step.value()} (all the angles "
                    f"to record again)?", QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel)
                if answer != QtWidgets.QMessageBox.Ok:
                    return False
            self.recorded.clear()
            self.state["run"] = None
            self.update_table()
            self.completeChanged.emit()
        if self.state["run"] is None:
            self.state["run"] = datetime.now().strftime("%Y_%m_%d_%H%M%S")
            self.state["levels"], self.state["step"] = levels, self.step.value()
        wavelength = self.state["wavelength_nm"]
        extra = self.tools["extra_metadata"]()
        extra["calibration"] = {
            "name": "slm_phase", "light_path": self.tools.get("side"), "run": self.state["run"],
            "qwp_angle_deg": angle, "wavelength_nm": wavelength,
            "qwp_retardance_waves": slm_phase_analysis.qwp_retardance(wavelength),
            "gray_step": self.step.value(), "levels": levels}
        folder = calibration_folder("slm_phase")
        brightest = [0]

        def on_image(k, image):
            brightest[0] = max(brightest[0], int(image.max()))

        done, writer = record_masks(
            self, self.orca, self.slm, len(levels),
            lambda k: (f"flat_{levels[k]}", flat_mask(levels[k])), folder, extra,
            f"Plate {angle}°", on_image)
        if done < len(levels):
            remove_acquisition(writer.folder, writer.index)
            self.result.setText(f"Plate {angle}°: stopped after {done} of {len(levels)} "
                                f"images (acquisition {writer.index} deleted).")
            return False
        replaced = replace_recording(self.recorded, angle, writer.folder, writer.index)
        self.update_table()
        self.completeChanged.emit()
        warning = (f" <b style='color:{MISSING_COLOR}'>Saturated pixels (max {brightest[0]}): "
                   f"lower the exposure and record all the angles again.</b>"
                   if brightest[0] >= SATURATION else f" (max {brightest[0]} counts)")
        again = "" if replaced is None else f" (previous one, {replaced}, deleted)"
        self.result.setText(f"Plate {angle}°: acquisition {writer.index} in {writer.folder}"
                            f"{again}.{warning}")
        return True


class SlmPhaseAnalysisPage(QtWidgets.QWizardPage):
    """
    Last step of slm_phase: slm_phase_analysis.run on the run just recorded (in a thread),
    which writes <first>-<last>_slm_phase_<YYYY_MM_DD_HHMMSS>.json / .npz / .png next to it.
    """

    def __init__(self, record_page, state):
        super().__init__()
        import slm_phase_analysis
        self.setTitle("Phase of each gray level, for each Orca pixel")
        self.record_page, self.state = record_page, state
        self.tools = record_page.tools
        self.path = None  # result file of the last analysis
        self.thread = None
        self.outcome = None
        self.message = ""
        text = QtWidgets.QLabel(
            "For each lit Orca pixel: its polarization (Stokes vector) at each gray level from the "
            "9 plate angles, then the phase applied by the SLM = the angle turned about the "
            "liquid-crystal axis on the Poincar&eacute; sphere, from gray level 0. Written next "
            "to the recordings (<code>.json</code> summary, <code>.npz</code> per pixel, "
            "<code>.png</code>).")
        text.setWordWrap(True)
        buttons = QtWidgets.QHBoxLayout()
        self.analyse_button = QtWidgets.QPushButton("Analyse the recording")
        self.analyse_button.clicked.connect(self.analyse_last)
        choose = QtWidgets.QPushButton("Analyse another recording...")
        choose.clicked.connect(self.choose)
        self.bin = QtWidgets.QComboBox()
        for size in (1, 2, 4, 8, 16):
            self.bin.addItem(f"Orca pixels {size} x {size}", size)
        self.bin.setToolTip("Binning of the Orca pixels for the analysis (1: each pixel)")
        self.channels = QtWidgets.QComboBox()
        for key, label in slm_phase_analysis.CHANNELS.items():
            self.channels.addItem(label, key)
        self.channels.setCurrentIndex(self.channels.findData(slm_phase_analysis.DEFAULT_CHANNELS))
        self.use_button = QtWidgets.QPushButton("Link to the SLM masks")
        self.use_button.setToolTip("The masks of the SLM panel are then corrected with the phase "
                                   "curve of this result (right SLM half), after align_BFP")
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self.use)
        for widget in (self.analyse_button, choose, self.bin, self.channels, self.use_button):
            buttons.addWidget(widget)
        buttons.addStretch()
        self.result = QtWidgets.QLabel()
        self.result.setTextFormat(QtCore.Qt.RichText)
        self.result.setWordWrap(True)
        self.result.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        self.result.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.figure = QtWidgets.QLabel()
        self.figure.setFixedSize(2 * FIGURE_SIZE, 2 * FIGURE_SIZE)
        self.figure.setAlignment(QtCore.Qt.AlignCenter)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.result, 1)
        row.addWidget(self.figure, 0, QtCore.Qt.AlignTop)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(text)
        layout.addLayout(buttons)
        layout.addLayout(row, 1)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.check_thread)

    def initializePage(self):
        self.analyse_button.setEnabled(bool(self.record_page.recorded))

    def cleanupPage(self):
        self.timer.stop()

    def analyse_last(self):
        recorded = self.record_page.recorded
        if recorded:
            self.analyse(*max(recorded.values(), key=lambda r: r[1]))

    def choose(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Any acquisition of an slm_phase recording", type_folder("slm_phase"),
            "Acquisitions (*.json)")
        if path:
            folder, name = os.path.split(path)
            self.analyse(folder, int(name.split("_")[0].split(".")[0]))

    def analyse(self, folder, index):
        import threading
        import slm_phase_analysis
        if self.thread is not None:
            return
        self.outcome = None
        self.use_button.setEnabled(False)
        self.message = f"Analysing the recording of acquisition {index} in {folder}..."
        self.analyse_button.setEnabled(False)
        self.figure.clear()
        bin_size, channels = self.bin.currentData(), self.channels.currentData()

        def say(text):
            self.message = text

        def work():
            try:
                self.outcome = slm_phase_analysis.run(folder, index, bin_size, progress=say,
                                                      channels=channels)
            except Exception as error:
                self.outcome = error

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        self.timer.start()

    def check_thread(self):
        import slm_phase_analysis
        if self.thread is None:
            return
        if self.thread.is_alive():
            self.result.setText(self.message)
            return
        self.timer.stop()
        self.thread = None
        self.analyse_button.setEnabled(bool(self.record_page.recorded))
        if isinstance(self.outcome, Exception):
            self.result.setText(f"<b>Analysis failed</b>: {self.outcome}")
            return
        path, summary = self.outcome
        lines = [f"Plate zero offset {summary['plate_offset_deg']:+.2f}°, "
                 f"{summary['n_pixels']} lit pixels ({summary['bin']} x {summary['bin']}). "
                 f"Analysed: {slm_phase_analysis.CHANNELS[summary['channels']]}."]
        for disk in summary["disks"]:
            gray = disk["gray_of_2pi"]
            lines.append(
                f"<b>Disk {disk['disk']}</b> (Orca x {disk['center_orca_xy'][0]:.0f}): phase at "
                f"255 = {disk['phase_at_255_rad']:.2f} rad "
                f"({disk['phase_at_255_rad'] / np.pi:.2f} &pi;), <b>2&pi; at gray "
                f"{'not reached' if gray is None else f'{gray:.1f}'}</b>; modulation "
                f"{disk['modulation_median']:.2f}, degree of polarization "
                f"{disk['polarization_degree_median']:.2f}")
        lines.append(f"Written: {path}")
        self.result.setText("<br>".join(lines))
        self.path = path
        self.use_button.setEnabled(True)
        pixmap = QtGui.QPixmap(path.replace(".json", ".png"))
        if not pixmap.isNull():
            self.figure.setPixmap(pixmap.scaled(2 * FIGURE_SIZE, 2 * FIGURE_SIZE,
                                                QtCore.Qt.KeepAspectRatio,
                                                QtCore.Qt.SmoothTransformation))

    def use(self):
        try:
            self.tools["link_calibration"]("slm_phase", self.path)
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "Error", str(error))
            return
        self.result.setText(self.result.text() + f"<br><b>Linked to the SLM masks</b>: "
                            f"{os.path.basename(self.path)}")
        self.use_button.setEnabled(False)


# ---------------------------------------------------------------- channels_geometry

LAMP_FRAMES, BEAD_FRAMES = 10, 20  # frames per recording (averaged by the analysis)


def record_frames(parent, camera, n_frames, folder, extra, label):
    """n_frames snaps of a camera panel saved as one acquisition of folder (X_0.tiff, X.json,
    X_frames.csv); progress window with Stop, live paused meanwhile. Returns (frames taken,
    writer, mean image)."""
    progress = QtWidgets.QProgressDialog(f"{label}...", "Stop", 0, n_frames, parent)
    progress.setWindowModality(QtCore.Qt.WindowModal)
    progress.setMinimumDuration(0)
    done, total = 0, None

    def acquire():
        nonlocal done, total
        writer = SequenceWriter(folder, extra, None, 16, index=next_index(folder))
        with writer:
            for k in range(n_frames):
                progress.setLabelText(f"{label}: frame {k + 1} / {n_frames}")
                progress.setValue(k)
                QtWidgets.QApplication.processEvents()
                if progress.wasCanceled():
                    break
                image = camera.device.snap()
                if k == 0:
                    writer.start(camera.device.last_metadata)
                writer.write(image)
                total = image.astype(float) if total is None else total + image
                done += 1
                camera.display(image)
        return writer

    try:
        writer = camera.run_paused(acquire)
    finally:
        progress.close()
    return done, writer, None if total is None else total / done


def remove_acquisition(folder, index):
    """Delete the files of acquisition index of folder (an angle recorded again, or a recording
    stopped before the end): X.json, X_K.tiff, X_frames.csv, X_slm_masks, X_calibrations."""
    import shutil
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if name in (saving.masks_folder_name(index), saving.calibrations_folder_name(index)):
            shutil.rmtree(path)
        elif name in (saving.json_name(index), saving.frames_name(index)) or (
                name.startswith(f"{index}_") and name.endswith(".tiff")
                and name[len(f"{index}_"):-5].isdigit()):
            os.remove(path)


def replace_recording(recorded, angle, folder, index):
    """recorded[angle] = (folder, index); the previous recording of this angle is deleted
    (the last one is the good one)."""
    previous = recorded.get(angle)
    recorded[angle] = (folder, index)
    if previous is not None and previous != (folder, index):
        remove_acquisition(*previous)
        return previous[1]
    return None


def channels_geometry_steps(panels, tools):
    # shared by the pages of this window
    state = {"lamp": None, "channels": None, "beads": [], "calibration": None}
    return [ChannelsLampPage(panels, tools, state), ChannelsBeadsPage(panels, tools, state),
            ChannelsAnalysisPage(state), ChannelsCheckPage(panels, tools, state)]


class _ChannelsRecordPage(QtWidgets.QWizardPage):
    """Common part of the lamp and bead pages: Prime buttons, exposure, number of frames."""

    def __init__(self, panels, tools, state, title, text, frames, record_label):
        super().__init__()
        self.setTitle(title)
        self.prime, self.tools, self.state = panels["prime"], tools, state
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        self.frames = QtWidgets.QSpinBox()
        self.frames.setRange(1, 1000)
        self.frames.setValue(frames)
        self.frames.setSuffix(" frames")
        self.frames.setToolTip("Frames of the recording, averaged by the analysis")
        self.exposure = exposure_box(self.prime)
        buttons = QtWidgets.QHBoxLayout()
        for text_, function in (("Show the Prime", lambda: show_panel(self.prime)),
                                ("Start / stop Prime live", lambda: toggle_live(self.prime))):
            button = QtWidgets.QPushButton(text_)
            button.clicked.connect(lambda _, f=function: self.run(f))
            buttons.addWidget(button)
        buttons.addWidget(self.exposure)
        buttons.addWidget(self.frames)
        record = QtWidgets.QPushButton(record_label)
        record.clicked.connect(lambda: self.run(self.record))
        buttons.addWidget(record)
        buttons.addStretch()
        self.status = QtWidgets.QLabel()
        self.result = QtWidgets.QLabel()
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(label)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        layout.addWidget(self.result)
        layout.addStretch()
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(DEVICE_CHECK_PERIOD)
        self.timer.timeout.connect(self.update_status)

    def initializePage(self):
        self.update_status()
        self.timer.start()

    def cleanupPage(self):
        self.timer.stop()

    def validatePage(self):
        self.timer.stop()
        return True

    def update_status(self):
        text, color = live_status(self.prime, "Prime")
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color}; font-weight: bold")
        self.exposure.sync()

    def run(self, function):
        try:
            function()
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "Error", str(error))
        self.update_status()

    def acquire(self, recording):
        """Record the frames as an acquisition of CALIBRATION_ROOT/channels_geometry/YYYY_MM_DD:
        (folder, index, mean image), or None if stopped."""
        extra = self.tools["extra_metadata"]()
        extra["calibration"] = {"name": "channels_geometry", "light_path": self.tools.get("side"),
                                "recording": recording, "frames": self.frames.value()}
        folder = calibration_folder("channels_geometry")
        done, writer, mean = record_frames(self, self.prime, self.frames.value(), folder, extra,
                                           recording)
        if done < self.frames.value():
            self.result.setText(f"{recording}: stopped after {done} of {self.frames.value()} "
                                f"frames.")
            return None
        return writer.folder, writer.index, mean


class ChannelsLampPage(_ChannelsRecordPage):
    """White lamp recording: the two channels (edges, centres, split row) and the lamp
    normalization, shown at once."""

    def __init__(self, panels, tools, state):
        super().__init__(
            panels, tools, state, "White lamp: the two channels",
            "Switch the <b>white transmitted lamp</b> on (by hand), nothing in focus: both "
            "channels evenly lit on the Prime (top = reference, bottom), not saturated (Prime "
            "live). <b>Record the lamp</b>: the channels are found at once (edges, centres, "
            "where the image is cut in 2), with the normalization of the channels.<br>Saved in "
            f"<code>{saving.CALIBRATION_ROOT}\\channels_geometry\\YYYY_MM_DD</code>.",
            LAMP_FRAMES, "Record the lamp")

    def record(self):
        import channels_geometry
        recorded = self.acquire("white lamp")
        if recorded is None:
            return
        folder, index, mean = recorded
        try:
            channels = channels_geometry.detect_channels(mean)
            norm = channels_geometry.normalization(mean, channels)
        except ValueError as error:
            self.state["lamp"] = self.state["channels"] = None
            self.completeChanged.emit()
            self.result.setText(f"Lamp saved as acquisition {index} in {folder}, but <b>the "
                                f"channels are not found</b>: {error}")
            return
        self.state["lamp"], self.state["channels"] = (folder, index), channels
        lines = [f"Lamp saved as acquisition {index} in {folder}."]
        for name in channels_geometry.CHANNELS:
            c = channels[name]
            lines.append(f"<b>{name}</b>{' (reference)' if name == 'top' else ''}: columns "
                         f"{c['cols'][0]:.1f}-{c['cols'][1]:.1f}, rows {c['rows'][0]:.1f}-"
                         f"{c['rows'][1]:.1f}, centre {c['center_xy'][0]:.1f}, "
                         f"{c['center_xy'][1]:.1f}")
        (tx, ty), (bx, by) = channels["top"]["center_xy"], channels["bottom"]["center_xy"]
        lines.append(f"Cut at row {channels['split_row']:.1f}; centre to centre (bottom - top) "
                     f"{bx - tx:+.1f}, {by - ty:+.1f} px (image).")
        shape = channels["top"]["shape"]
        lines.append(f"Frame (top rectangle) rotation on the camera "
                     f"{channels['frame_rotation_deg']:+.3f} deg (skew {shape['skew_deg']:+.3f} "
                     f"deg); bottom rectangle wrt top {channels['bottom_rotation_lamp_deg']:+.3f} "
                     f"deg.")
        lines.append(f"Normalization: bottom / top = {norm['bottom_over_top']:.4f}.")
        self.result.setText("<br>".join(lines))
        self.completeChanged.emit()

    def isComplete(self):
        return self.state["channels"] is not None


class ChannelsBeadsPage(_ChannelsRecordPage):
    """Bead fields: each recording one field (N frames); the beads found in each channel are
    counted at once."""

    def __init__(self, panels, tools, state):
        super().__init__(
            panels, tools, state, "Beads: one or more fields",
            "Switch the white lamp <b>off</b>. Put the <b>TetraSpeck beads (size 4)</b> in focus "
            "on the Prime, with the excitation on (Lasers panel), not saturated. <b>Record this "
            "field</b>; move the stage to another field and record again for more beads (all "
            "the fields are pooled by the analysis).",
            BEAD_FRAMES, "Record this field")

    def initializePage(self):
        super().initializePage()
        same = [r for r in self.state["beads"] if r[0] == calibration_folder("channels_geometry")]
        self.state["beads"] = same

    def record(self):
        import channels_geometry
        recorded = self.acquire(f"beads field {len(self.state['beads']) + 1}")
        if recorded is None:
            return
        folder, index, mean = recorded
        channels = self.state["channels"]
        counts = []
        for name in channels_geometry.CHANNELS:
            box = (*channels[name]["cols"], *channels[name]["rows"])
            counts.append(len(channels_geometry.find_beads(mean, box)))
        self.state["beads"].append((folder, index))
        self.result.setText(self.result.text() + ("<br>" if self.result.text() else "") +
                            f"Field {len(self.state['beads'])}: acquisition {index}, beads found "
                            f"top {counts[0]}, bottom {counts[1]}.")
        self.completeChanged.emit()

    def isComplete(self):
        return bool(self.state["beads"])


class ChannelsAnalysisPage(QtWidgets.QWizardPage):
    """Last step: channels_geometry.run on the lamp and the bead fields (in a thread); result
    file X_channels_geometry_<date_time>.json / .png next to the first bead field."""

    def __init__(self, state):
        super().__init__()
        self.setTitle("Mapping between the channels")
        self.state = state
        self.thread, self.outcome = None, None
        text = QtWidgets.QLabel(
            "The beads of each channel are fitted and paired; the mapping top &rarr; bottom (in "
            "the axes of each channel: top x right y up, bottom x right y down) is fitted as a "
            "rotation + scales + shift (affine) and a smooth residual field. No image is "
            "corrected. The result is written next to the first bead field "
            "(<code>X_channels_geometry_&lt;date_time&gt;.json</code> and <code>.png</code>).")
        text.setWordWrap(True)
        self.button = QtWidgets.QPushButton("Analyse")
        self.button.clicked.connect(self.analyse)
        self.result = QtWidgets.QLabel()
        self.result.setTextFormat(QtCore.Qt.RichText)
        self.result.setWordWrap(True)
        self.result.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        self.result.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.figure = QtWidgets.QLabel()
        self.figure.setFixedSize(FIGURE_SIZE, FIGURE_SIZE)
        self.figure.setAlignment(QtCore.Qt.AlignCenter)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.result, 1)
        row.addWidget(self.figure, 0, QtCore.Qt.AlignTop)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.button)
        buttons.addStretch()
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(text)
        layout.addLayout(buttons)
        layout.addLayout(row, 1)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.check_thread)

    def initializePage(self):
        self.button.setEnabled(self.state["lamp"] is not None and bool(self.state["beads"]))

    def cleanupPage(self):
        self.timer.stop()

    def analyse(self):
        import threading
        import channels_geometry
        if self.thread is not None:
            return
        lamp_folder, lamp_index = self.state["lamp"]
        beads = [i for folder, i in self.state["beads"] if folder == lamp_folder]
        if not beads:
            self.result.setText("The bead fields are not in the folder of the lamp recording "
                                "(another day?): record the lamp again.")
            return
        self.button.setEnabled(False)
        self.figure.clear()
        self.result.setText(f"Analysing lamp {lamp_index} and bead fields {beads}...")

        def work():
            try:
                self.outcome = channels_geometry.run(lamp_folder, lamp_index, beads)
            except Exception as error:
                self.outcome = error

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        self.timer.start()

    def check_thread(self):
        import channels_geometry
        if self.thread is None or self.thread.is_alive():
            return
        self.timer.stop()
        self.thread = None
        self.button.setEnabled(True)
        if isinstance(self.outcome, Exception):
            self.result.setText(f"<b>Analysis failed</b>: {self.outcome}")
            return
        path, content = self.outcome
        self.state["calibration"] = path
        self.result.setText(channels_geometry.summary(content) + f"<br>Written: {path} (and "
                            f"<code>_check.png</code>: the beads left out)")
        pixmap = QtGui.QPixmap(path.replace(".json", ".png"))
        if not pixmap.isNull():
            self.figure.setPixmap(pixmap.scaled(FIGURE_SIZE, FIGURE_SIZE, QtCore.Qt.KeepAspectRatio,
                                                QtCore.Qt.SmoothTransformation))


class ChannelsCheckPage(_ChannelsRecordPage):
    """Optional check, as check_calib of reconstruction_juliette: bead fields NOT used for the
    mapping, each top bead mapped to the bottom channel and compared with the bottom bead
    measured there (error map and histogram)."""

    def __init__(self, panels, tools, state):
        super().__init__(
            panels, tools, state, "Check on new beads (optional)",
            "Move the stage to a <b>new field</b> of beads (not recorded in step 5), in focus. "
            "<b>Record and check</b>: the beads of the two channels are found, each top bead is "
            "mapped to the bottom channel with the mapping of step 6 and compared with the "
            "bottom bead measured there. Shown: the errors (rms, median, 95 %, std, "
            "interquartile, mean vector), the map of the errors and their histogram. Written "
            "next to the recording: <code>X_channels_geometry_check_&lt;date_time&gt;.json"
            "</code> and <code>.png</code>.",
            BEAD_FRAMES, "Record and check")
        self.figure = QtWidgets.QLabel()
        self.figure.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        self.layout().insertWidget(self.layout().count() - 1, self.figure)

    def initializePage(self):
        super().initializePage()
        if self.state["calibration"] is None:
            self.result.setText("Analyse first (step 6).")

    def record(self):
        import channels_geometry
        if self.state["calibration"] is None:
            self.result.setText("Analyse first (step 6).")
            return
        recorded = self.acquire("check beads")
        if recorded is None:
            return
        folder, index, _ = recorded
        path, out = channels_geometry.check(self.state["calibration"], folder, [index])
        self.result.setText(channels_geometry.check_summary(out) + f"<br>Written: {path}")
        pixmap = QtGui.QPixmap(path.replace(".json", ".png"))
        if not pixmap.isNull():
            self.figure.setPixmap(pixmap.scaled(2 * FIGURE_SIZE, FIGURE_SIZE,
                                                QtCore.Qt.KeepAspectRatio,
                                                QtCore.Qt.SmoothTransformation))


# ---------------------------------------------------------------- input_polar_SLM


def input_polar_steps(panels, tools):
    prime = panels["prime"]
    state = {"wavelength_nm": 640, "run": None}  # shared by the pages of this window
    record = InputPolarRecordPage(panels, tools, state)
    return [
        WavelengthPage(state),
        InstructionPage(
            "Beads in focus on the Prime",
            "Put a sample of TetraSpeck beads (size 1) on the microscope and bring the beads in "
            "focus on the Prime (live), excitation on. No Perfect Focus: it does not lock on "
            "TetraSpeck beads.<br><b>Next stops the Prime live.</b>",
            actions=[("Show the Prime", lambda: show_panel(prime)),
                     ("Start / stop Prime live", lambda: toggle_live(prime))],
            status=lambda: live_status(prime, "Prime"),
            confirm="The beads are in focus on the Prime.",
            on_next=lambda: stop_live(prime)),
        record,
        InputPolarAnalysisPage(record, state),
    ]


class InputPolarRecordPage(QtWidgets.QWizardPage):
    """
    input_polar_SLM recording: for each angle of the quarter-wave plate of the analyzer (right
    before the SLM, turned by hand: a window asks for each one), a time series of N Orca frames,
    the same flat mask on the SLM, saved as one acquisition of
    CALIBRATION_ROOT/input_polar_SLM/YYYY_MM_DD (calibration "run": the same for the 9 angles of
    this window). An angle can be recorded again (the analysis takes the last).
    """

    def __init__(self, panels, tools, state):
        super().__init__()
        import input_polar_slm_analysis as analysis
        import slm_phase_analysis
        self.angles = slm_phase_analysis.QWP_ANGLES
        self.setTitle("Time series on the Orca, for each angle of the quarter-wave plate")
        self.orca, self.slm = panels["orca"], panels["slm"]
        self.tools, self.state = tools, state
        self.recorded = {}  # angle -> (folder, index)
        text = QtWidgets.QLabel(
            f"For each angle of the quarter-wave plate ({', '.join(map(str, self.angles))}°), a "
            f"time series of Orca frames (number below, the same for all the angles), the SLM "
            f"showing the flat mask below. <b>The plate is turned by hand</b>: a window tells the "
            f"angle before each recording. Before: check on the Orca live that the disks are not "
            f"saturated, at a few plate angles. The Orca settings must stay the same for all the "
            f"angles.<br>Saved in <code>{saving.CALIBRATION_ROOT}\\{analysis.NAME}\\YYYY_MM_DD"
            f"</code>.")
        text.setWordWrap(True)
        buttons = QtWidgets.QHBoxLayout()
        for label, function in (("Show the Orca", lambda: show_panel(self.orca)),
                                ("Start / stop Orca live", lambda: toggle_live(self.orca))):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(lambda _, f=function: self.run(f))
            buttons.addWidget(button)
        self.exposure = exposure_box(self.orca)
        buttons.addWidget(self.exposure)
        self.level = QtWidgets.QSpinBox()
        self.level.setRange(0, 255)
        self.level.setPrefix("SLM flat gray ")
        self.level.setToolTip("Flat mask shown on the SLM during the recordings (after the "
                              "analyzer: it does not change the measured polarization)")
        show = QtWidgets.QPushButton("Show it on the SLM")
        show.clicked.connect(lambda: self.run(self.show_flat))
        buttons.addWidget(self.level)
        buttons.addWidget(show)
        buttons.addStretch()
        self.table = QtWidgets.QTableWidget(len(self.angles), 2)
        self.table.setHorizontalHeaderLabels(["Plate angle", "Recording"])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        for row, angle in enumerate(self.angles):
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(f"{angle}°"))
        record_buttons = QtWidgets.QHBoxLayout()
        self.frames = QtWidgets.QSpinBox()
        self.frames.setRange(2, 1000)
        self.frames.setValue(analysis.FRAMES)
        self.frames.setSuffix(" frames per angle")
        self.frames.setToolTip("Frames of the time series of each angle (mean for the "
                               "polarization, frame by frame for its fluctuations)")
        record_buttons.addWidget(self.frames)
        remaining = QtWidgets.QPushButton("Record the remaining angles")
        remaining.clicked.connect(lambda: self.run(self.record_remaining))
        again = QtWidgets.QPushButton("Record the selected angle again")
        again.clicked.connect(lambda: self.run(self.record_selected))
        record_buttons.addWidget(remaining)
        record_buttons.addWidget(again)
        record_buttons.addStretch()
        self.status = QtWidgets.QLabel()
        self.result = QtWidgets.QLabel()
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout = QtWidgets.QVBoxLayout(self)
        for item in (text, buttons, self.table, record_buttons, self.status, self.result):
            (layout.addLayout if isinstance(item, QtWidgets.QLayout) else layout.addWidget)(item)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(DEVICE_CHECK_PERIOD)
        self.timer.timeout.connect(self.update_status)
        self.update_table()

    def initializePage(self):
        self.update_status()
        self.timer.start()

    def cleanupPage(self):
        self.timer.stop()

    def validatePage(self):
        self.timer.stop()
        return True

    def isComplete(self):
        return all(angle in self.recorded for angle in self.angles)

    def update_status(self):
        text, color = live_status(self.orca, "Orca")
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color}; font-weight: bold")
        self.exposure.sync()

    def update_table(self):
        for row, angle in enumerate(self.angles):
            done = self.recorded.get(angle)
            item = QtWidgets.QTableWidgetItem(
                f"acquisition {done[1]}" if done else "not recorded")
            item.setForeground(QtGui.QColor(OK_COLOR if done else NEUTRAL_COLOR))
            self.table.setItem(row, 1, item)

    def run(self, function):
        try:
            function()
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "Error", str(error))
        self.update_status()

    def show_flat(self):
        level = self.level.value()
        self.slm.show_mask(flat_mask(level), f"flat_{level}")

    def record_remaining(self):
        for angle in self.angles:
            if angle not in self.recorded and not self.record_angle(angle):
                return

    def record_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            raise ValueError("Select an angle in the table first.")
        self.record_angle(self.angles[rows[0].row()])

    def record_angle(self, angle):
        """Ask the user to turn the plate, then record the time series. False if cancelled /
        stopped."""
        import input_polar_slm_analysis as analysis
        import slm_phase_analysis
        n_frames, level = self.frames.value(), self.level.value()
        answer = QtWidgets.QMessageBox.question(
            self, "Quarter-wave plate",
            f"<b>Turn the quarter-wave plate (before the SLM) to {angle}°.</b><br><br>Then click "
            f"OK to record {n_frames} frames.",
            QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel)
        if answer != QtWidgets.QMessageBox.Ok:
            return False
        settings = (n_frames, level)
        if self.state["run"] is not None and settings != self.state.get("settings"):
            if self.recorded:
                answer = QtWidgets.QMessageBox.question(
                    self, "Settings changed",
                    f"The angles already recorded used {self.state['settings'][0]} frames and "
                    f"the SLM flat gray {self.state['settings'][1]}. All the angles must use the "
                    f"same: start a new run ({n_frames} frames, gray {level}; all the angles to "
                    f"record again)?", QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel)
                if answer != QtWidgets.QMessageBox.Ok:
                    return False
            self.recorded.clear()
            self.state["run"] = None
            self.update_table()
            self.completeChanged.emit()
        if self.state["run"] is None:
            self.state["run"] = datetime.now().strftime("%Y_%m_%d_%H%M%S")
            self.state["settings"] = settings
        wavelength = self.state["wavelength_nm"]
        extra = self.tools["extra_metadata"]()
        extra["calibration"] = {
            "name": analysis.NAME, "light_path": self.tools.get("side"), "run": self.state["run"],
            "qwp_angle_deg": angle, "wavelength_nm": wavelength,
            "qwp_retardance_waves": slm_phase_analysis.qwp_retardance(wavelength),
            "frames": n_frames, "slm_flat_level": level}
        before = self.slm.shown_mask()
        self.show_flat()
        try:
            done, writer, mean = record_frames(self, self.orca, n_frames,
                                               calibration_folder(analysis.NAME), extra,
                                               f"Plate {angle}°")
        finally:
            if before is not None:
                self.slm.show_mask(*before)
        if done < n_frames:
            remove_acquisition(writer.folder, writer.index)
            self.result.setText(f"Plate {angle}°: stopped after {done} of {n_frames} frames "
                                f"(acquisition {writer.index} deleted).")
            return False
        replaced = replace_recording(self.recorded, angle, writer.folder, writer.index)
        self.update_table()
        self.completeChanged.emit()
        brightest = int(mean.max())
        warning = (f" <b style='color:{MISSING_COLOR}'>Saturated pixels (mean max {brightest}): "
                   f"lower the exposure and record all the angles again.</b>"
                   if brightest >= SATURATION else f" (mean max {brightest} counts)")
        again = "" if replaced is None else f" (previous one, {replaced}, deleted)"
        self.result.setText(f"Plate {angle}°: acquisition {writer.index} in {writer.folder}"
                            f"{again}.{warning}")
        return True


class InputPolarAnalysisPage(QtWidgets.QWizardPage):
    """
    Last step of input_polar_SLM: input_polar_slm_analysis.run on the run just recorded (in a
    thread), which writes <first>-<last>_input_polar_SLM_<YYYY_MM_DD_HHMMSS>.json / .npz / .png
    next to it.
    """

    def __init__(self, record_page, state):
        super().__init__()
        import input_polar_slm_analysis as analysis
        self.setTitle("Polarization reaching the SLM")
        self.record_page, self.state = record_page, state
        self.thread, self.outcome, self.message = None, None, ""
        text = QtWidgets.QLabel(
            "For each lit Orca pixel: the Stokes vector of the light reaching the SLM from the 9 "
            "plate angles (mean of the frames), in the frame of the analyzer (polarizer along "
            "y): azimuth, ellipticity, degree of polarization; for each disk the same frame by "
            "frame (fluctuations in time) and the intensity drift of each series. Written next "
            "to the recordings (<code>.json</code> summary, <code>.npz</code> per pixel, "
            "<code>.png</code>).")
        text.setWordWrap(True)
        buttons = QtWidgets.QHBoxLayout()
        self.analyse_button = QtWidgets.QPushButton("Analyse the recording")
        self.analyse_button.clicked.connect(self.analyse_last)
        choose = QtWidgets.QPushButton("Analyse another recording...")
        choose.clicked.connect(self.choose)
        self.bin = QtWidgets.QComboBox()
        for size in (1, 2, 4, 8, 16):
            self.bin.addItem(f"Orca pixels {size} x {size}", size)
        self.bin.setCurrentIndex(2)
        self.bin.setToolTip("Binning of the Orca pixels for the maps (1: each pixel)")
        self.channels = QtWidgets.QComboBox()
        for key, label in analysis.CHANNELS.items():
            self.channels.addItem(label, key)
        self.zero = QtWidgets.QComboBox()
        zero, path = analysis.slm_phase_plate_zero()
        if zero is not None:
            self.zero.addItem(f"plate zero {zero:+.2f}° (slm_phase {os.path.basename(path)})",
                              (zero, f"slm_phase {os.path.basename(path)}"))
        self.zero.addItem("plate zero fitted on this recording (needs elliptical light)", None)
        self.zero.addItem("plate zero 0°", (0.0, "0 deg"))
        self.zero.setToolTip("Zero of the quarter-wave plate in its mount: the one fitted by the "
                             "SLM phase calibration (same mount) by default")
        for widget in (self.analyse_button, choose, self.bin, self.channels, self.zero):
            buttons.addWidget(widget)
        buttons.addStretch()
        self.result = QtWidgets.QLabel()
        self.result.setTextFormat(QtCore.Qt.RichText)
        self.result.setWordWrap(True)
        self.result.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        self.result.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.figure = QtWidgets.QLabel()
        self.figure.setFixedSize(2 * FIGURE_SIZE, 2 * FIGURE_SIZE)
        self.figure.setAlignment(QtCore.Qt.AlignCenter)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.result, 1)
        row.addWidget(self.figure, 0, QtCore.Qt.AlignTop)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(text)
        layout.addLayout(buttons)
        layout.addLayout(row, 1)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.check_thread)

    def initializePage(self):
        self.analyse_button.setEnabled(bool(self.record_page.recorded))

    def cleanupPage(self):
        self.timer.stop()

    def analyse_last(self):
        recorded = self.record_page.recorded
        if recorded:
            self.analyse(*max(recorded.values(), key=lambda r: r[1]))

    def choose(self):
        import input_polar_slm_analysis as analysis
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, f"Any acquisition of an {analysis.NAME} recording", type_folder(analysis.NAME),
            "Acquisitions (*.json)")
        if path:
            folder, name = os.path.split(path)
            self.analyse(folder, int(name.split("_")[0].split(".")[0]))

    def analyse(self, folder, index):
        import threading
        import input_polar_slm_analysis as analysis
        if self.thread is not None:
            return
        self.outcome = None
        self.message = f"Analysing the recording of acquisition {index} in {folder}..."
        self.analyse_button.setEnabled(False)
        self.figure.clear()
        bin_size, channels = self.bin.currentData(), self.channels.currentData()
        zero = self.zero.currentData()
        offset, source = zero if zero is not None else (None, None)

        def say(text):
            self.message = text

        def work():
            try:
                self.outcome = analysis.run(folder, index, bin_size, offset, say, channels, source)
            except Exception as error:
                self.outcome = error

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        self.timer.start()

    def check_thread(self):
        import input_polar_slm_analysis as analysis
        if self.thread is None:
            return
        if self.thread.is_alive():
            self.result.setText(self.message)
            return
        self.timer.stop()
        self.thread = None
        self.analyse_button.setEnabled(bool(self.record_page.recorded))
        if isinstance(self.outcome, Exception):
            self.result.setText(f"<b>Analysis failed</b>: {self.outcome}")
            return
        path, summary = self.outcome
        self.result.setText("<br>".join(analysis.summary_lines(summary) + [f"Written: {path}"]))
        pixmap = QtGui.QPixmap(path.replace(".json", ".png"))
        if not pixmap.isNull():
            self.figure.setPixmap(pixmap.scaled(2 * FIGURE_SIZE, 2 * FIGURE_SIZE,
                                                QtCore.Qt.KeepAspectRatio,
                                                QtCore.Qt.SmoothTransformation))


# ---------------------------------------------------------------- aberrations (DRAFT)

Z_STACK_SETTLE = 0.05  # s at each plane once the piezo is there (as setup_gui)
SLM_MASK_FOLDER = os.path.join(os.path.expanduser("~"), "Desktop", "SLM_masks")


def record_z_stack(parent, camera, piezo, reference, step, down, up, n_frames, folder, extra,
                   label, slm_mask=None):
    """
    Z stack with the objective piezo (closed loop), as the Saving tab of the cameras: n_frames at
    each plane from reference - down x step to reference + up x step (lowest first), one
    acquisition of folder (X_frames.csv columns z_plane, z_um; "z_stack" in X.json); the piezo
    back to the reference at the end. Progress window with Stop. Returns (frames taken, total,
    writer).
    """
    import time
    planes = [round(reference + k * step, 4) for k in range(-down, up + 1)]
    if planes[0] < 0 or planes[-1] > piezo.max_travel:
        raise ValueError(f"Z stack {planes[0]:.3f} to {planes[-1]:.3f} um: outside the piezo "
                         f"travel (0 to {piezo.max_travel:.0f} um).")
    total = len(planes) * n_frames
    extra = dict(extra, z_stack={
        "reference_um": reference, "step_um": step, "steps_down": down, "steps_up": up,
        "n_planes": len(planes), "frames_per_plane": n_frames, "positions_um": planes,
        "order": "lowest to highest plane, all the frames of a plane together",
        "frames_file_columns": "z_plane (0 = lowest), z_um (piezo position measured when the "
                               "plane is reached)",
        "settle_s": Z_STACK_SETTLE, "piezo_after": "back to the reference"})
    progress = QtWidgets.QProgressDialog(f"{label}...", "Stop", 0, total, parent)
    progress.setWindowModality(QtCore.Qt.WindowModal)
    progress.setMinimumDuration(0)
    plane = {"index": 0, "z": reference}

    def on_frame(done, n, frame):
        done += plane["index"] * n_frames
        progress.setValue(done)
        progress.setLabelText(f"{label}: plane {plane['index'] + 1} / {len(planes)} "
                              f"(z {plane['z']:.3f} um), frame {done} / {total}")
        if done % n_frames == 0:
            camera.display(frame)
        QtWidgets.QApplication.processEvents()
        return not progress.wasCanceled()

    def acquire():
        writer = SequenceWriter(folder, extra, None, 16, index=next_index(folder),
                                slm_mask=slm_mask, frame_columns={
                                    "z_plane": lambda: plane["index"],
                                    "z_um": lambda: f"{plane['z']:.3f}"})
        with writer:
            try:
                for k, z in enumerate(planes):
                    piezo.set_position(z)
                    time.sleep(Z_STACK_SETTLE)
                    plane["index"], plane["z"] = k, piezo.get_position()
                    camera.device.acquire_sequence(n_frames, None, on_frame, writer)
                    if progress.wasCanceled() or writer.n_frames < (k + 1) * n_frames:
                        break
            finally:
                piezo.set_position(reference)
        return writer

    try:
        writer = camera.run_paused(acquire)
    finally:
        progress.close()
    return writer.n_frames, total, writer


def load_mask(path):
    """Gray mask (uint8, SLM size) of an image or .npy file."""
    from SLM import load_phase_pattern
    return np.asarray(load_phase_pattern(path), dtype=np.uint8)


def aberrations_steps(panels, tools):
    prime, piezo = panels["prime"], panels["piezo"]
    # shared by the pages: z stacks recorded [{"kind", "mask", "folder", "index", "field"}],
    # correction {half: {noll: m-lambda}} and its phase, first look results
    state = {"stacks": [], "correction": {}, "correction_phase": None, "first_look": None}
    return [
        InstructionPage(
            "Beads, piezo and SLM",
            "<b>TetraSpeck beads (200 nm)</b> in glycerol, sparse (isolated beads, defocused "
            "rings up to ~25 px), excitation on (Lasers panel). <b>Objective piezo</b> connected "
            "and in <b>closed loop</b> (&micro;m), roughly mid-travel. SLM connected; link "
            "align_BFP (and slm_phase) in the SLM panel (Calibration...): the masks of this "
            "window are corrected by the linked calibrations. Beads in focus on the Prime "
            "(live).",
            actions=[("Show the Prime", lambda: show_panel(prime)),
                     ("Start / stop Prime live", lambda: toggle_live(prime)),
                     ("Show the piezo", lambda: show_panel(piezo))],
            status=lambda: live_status(prime, "Prime"),
            confirm="Beads in focus, piezo in closed loop."),
        AberrationsStacksPage(
            panels, tools, state, "none", "Z stacks without phase mask",
            "Flat SLM (gray 0). For each field: <b>focus</b> on the beads, then <b>Record a z "
            "stack</b> (reference = the piezo position now). Then move the stage to another "
            "field (beads elsewhere in the field of view) and record again: several fields, so "
            "that the beads cover the whole field of each channel."),
        AberrationsFirstLookPage(state),
        AberrationsStacksPage(
            panels, tools, state, "masks", "Z stacks with phase masks",
            "Choose a phase mask (double helix, Airy, tri-spot, bisected pupil...; "
            "<code>Desktop\\SLM_masks</code>) and record z stacks of one or more fields, then the "
            "next mask. A known pupil phase added to the unknown aberrations helps the fit "
            "(phase diversity). The same fields as without mask, if possible."),
        AberrationsCorrectionPage(panels, state),
        AberrationsStacksPage(
            panels, tools, state, "check", "Check: z stacks with the correction",
            "The correction of the previous step is added to the mask chosen here (none by "
            "default). Record z stacks of several fields: the common aberrations should be ~0, "
            "the field-dependent part unchanged."),
        AberrationsStorePage(state),
    ]


class AberrationsStacksPage(QtWidgets.QWizardPage):
    """
    DRAFT. Z stacks of bead fields with the piezo, a mask on the SLM: kind "none" (flat), "masks"
    (a mask file chosen here) or "check" (the correction of AberrationsCorrectionPage added to the
    mask chosen). Each saved as one acquisition of CALIBRATION_ROOT/aberrations/YYYY_MM_DD.
    """

    def __init__(self, panels, tools, state, kind, title, text):
        import aberrations
        super().__init__()
        self.setTitle(title)
        self.prime, self.piezo, self.slm = panels["prime"], panels["piezo"], panels["slm"]
        self.tools, self.state, self.kind = tools, state, kind
        label = QtWidgets.QLabel(text + f"<br>Saved in <code>{saving.CALIBRATION_ROOT}\\"
                                        f"{aberrations.NAME}\\YYYY_MM_DD</code>.")
        label.setWordWrap(True)
        buttons = QtWidgets.QHBoxLayout()
        for text_, function in (("Show the Prime", lambda: show_panel(self.prime)),
                                ("Start / stop Prime live", lambda: toggle_live(self.prime))):
            button = QtWidgets.QPushButton(text_)
            button.clicked.connect(lambda _, f=function: self.run(f))
            buttons.addWidget(button)
        self.exposure = exposure_box(self.prime)
        buttons.addWidget(self.exposure)
        buttons.addStretch()
        defaults = aberrations.Z_STACK_DEFAULTS
        self.step = QtWidgets.QDoubleSpinBox()
        self.step.setDecimals(3)
        self.step.setRange(0.005, 2)
        self.step.setValue(defaults["step_um"])
        self.step.setPrefix("step ")
        self.step.setSuffix(" um")
        self.down, self.up, self.frames = (QtWidgets.QSpinBox() for _ in range(3))
        for box, prefix, suffix, value in ((self.down, "down ", " steps", defaults["down"]),
                                           (self.up, "up ", " steps", defaults["up"]),
                                           (self.frames, "", " frames per plane",
                                            defaults["frames"])):
            box.setRange(0 if box is not self.frames else 1, 1000)
            box.setValue(value)
            box.setPrefix(prefix)
            box.setSuffix(suffix)
        z_row = QtWidgets.QHBoxLayout()
        for widget in (self.step, self.down, self.up, self.frames):
            z_row.addWidget(widget)
        z_row.addStretch()
        mask_row = QtWidgets.QHBoxLayout()
        self.mask = QtWidgets.QComboBox()
        self.mask.addItem("no mask (flat, gray 0)", None)
        if kind != "none" and os.path.isdir(SLM_MASK_FOLDER):
            for name in sorted(os.listdir(SLM_MASK_FOLDER)):
                if name.lower().endswith((".png", ".bmp", ".tif", ".tiff", ".npy")):
                    self.mask.addItem(name, os.path.join(SLM_MASK_FOLDER, name))
        self.mask.setEnabled(kind != "none")
        self.linked = QtWidgets.QCheckBox("corrected by the calibrations linked in the SLM panel")
        self.linked.setChecked(True)
        mask_row.addWidget(QtWidgets.QLabel("SLM mask"))
        mask_row.addWidget(self.mask, 1)
        mask_row.addWidget(self.linked)
        record = QtWidgets.QPushButton("Record a z stack of this field")
        record.clicked.connect(lambda: self.run(self.record))
        record_row = QtWidgets.QHBoxLayout()
        record_row.addWidget(record)
        record_row.addStretch()
        self.status = QtWidgets.QLabel()
        self.list = QtWidgets.QListWidget()
        layout = QtWidgets.QVBoxLayout(self)
        for item in (label, buttons, z_row, mask_row, record_row, self.status, self.list):
            (layout.addLayout if isinstance(item, QtWidgets.QLayout) else layout.addWidget)(item)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(DEVICE_CHECK_PERIOD)
        self.timer.timeout.connect(self.update_status)

    def initializePage(self):
        self.update_status()
        self.timer.start()

    def cleanupPage(self):
        self.timer.stop()

    def validatePage(self):
        self.timer.stop()
        return True

    def isComplete(self):
        return self.kind != "none" or any(s["kind"] == "none" for s in self.state["stacks"])

    def update_status(self):
        text, color = live_status(self.prime, "Prime")
        piezo = self.piezo.device
        try:
            where = (f"piezo {piezo.get_position():.3f} um (reference)"
                     if piezo is not None and piezo.is_closed_loop()
                     else "piezo not connected or in open loop")
        except Exception as error:
            where = f"piezo: {error}"
        self.status.setText(f"{text}; {where}")
        self.status.setStyleSheet(f"color: {color}; font-weight: bold")
        self.exposure.sync()

    def run(self, function):
        try:
            function()
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "Error", str(error))
        self.update_status()

    def pattern(self):
        """(gray mask, name) to show: the chosen mask (or flat 0), + the correction (check)."""
        import aberrations
        path = self.mask.currentData()
        mask = flat_mask(0) if path is None else load_mask(path)
        name = "flat_0" if path is None else os.path.splitext(os.path.basename(path))[0]
        if self.kind == "check":
            if self.state["correction_phase"] is None:
                raise ValueError("No correction yet: set it in the correction step.")
            mask = aberrations.add_to_mask(mask, self.state["correction_phase"])
            name += "+correction"
        if self.linked.isChecked() and self.slm.links:
            mask = correct_mask(mask, self.slm.links)
        return mask, name

    def record(self):
        import aberrations
        piezo = self.piezo.device
        if piezo is None or not piezo.is_closed_loop():
            raise ValueError("Connect the objective piezo and put it in closed loop (um).")
        mask, name = self.pattern()
        same = [s for s in self.state["stacks"] if s["kind"] == self.kind]
        field_number = len(same) + 1
        extra = self.tools["extra_metadata"]()
        extra["calibration"] = {
            "name": aberrations.NAME, "light_path": self.tools.get("side"), "kind": self.kind,
            "slm_mask": name, "slm_mask_file": self.mask.currentData(),
            "linked_calibrations_applied": sorted(self.slm.links) if self.linked.isChecked()
            else [], "correction_mlambda": self.state["correction"] if self.kind == "check"
            else None, "field": field_number}
        before = self.slm.shown_mask()
        self.slm.show_mask(mask, name)
        try:
            done, total, writer = record_z_stack(
                self, self.prime, piezo, piezo.get_position(), self.step.value(),
                self.down.value(), self.up.value(), self.frames.value(),
                calibration_folder(aberrations.NAME), extra, f"Z stack {name}",
                slm_mask=self.slm.shown_mask)
        finally:
            if before is not None:
                self.slm.show_mask(*before)
        if done < total:
            self.list.addItem(f"{name}: stopped after {done} of {total} frames (acquisition "
                              f"{writer.index}, not used)")
            return
        self.state["stacks"].append({"kind": self.kind, "mask": name, "folder": writer.folder,
                                     "index": writer.index, "field": field_number})
        self.list.addItem(f"field {field_number}, mask {name}: acquisition {writer.index} "
                          f"({total} frames)")
        self.completeChanged.emit()


class AberrationsFirstLookPage(QtWidgets.QWizardPage):
    """DRAFT. Model-free first look of the stacks without mask (aberrations.run_first_look, in a
    thread); the fit is to come."""

    def __init__(self, state):
        super().__init__()
        self.setTitle("First look (no model) - the fit is to come")
        self.state = state
        self.thread, self.outcome = None, None
        text = QtWidgets.QLabel(
            "Per channel and bead, from the second moments (no PSF model): widths along x and y "
            "vs z, best focus, astigmatism (focus x - focus y), asymmetry above / below the focus "
            "(spherical), and their gradients over the field. <b>The fit of the aberrations "
            "(Zernike per bead, then their field model) is not written yet</b>: to be discussed "
            "(pytorch here, or jax in WSL).")
        text.setWordWrap(True)
        self.button = QtWidgets.QPushButton("First look at the stacks without mask")
        self.button.clicked.connect(self.analyse)
        self.result = QtWidgets.QLabel()
        self.result.setWordWrap(True)
        self.result.setTextFormat(QtCore.Qt.RichText)
        self.result.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.button)
        row.addStretch()
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(text)
        layout.addLayout(row)
        layout.addWidget(self.result, 1)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.check_thread)

    def cleanupPage(self):
        self.timer.stop()

    def analyse(self):
        import threading
        import aberrations
        stacks = [(s["folder"], s["index"]) for s in self.state["stacks"] if s["kind"] == "none"]
        if not stacks or self.thread is not None:
            return
        self.button.setEnabled(False)
        self.result.setText(f"Analysing {len(stacks)} z stacks...")

        def work():
            try:
                self.outcome = aberrations.run_first_look(stacks)
            except Exception as error:
                self.outcome = error

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        self.timer.start()

    def check_thread(self):
        if self.thread is None or self.thread.is_alive():
            return
        self.timer.stop()
        self.thread = None
        self.button.setEnabled(True)
        if isinstance(self.outcome, Exception):
            self.result.setText(f"<b>Analysis failed</b>: {self.outcome}")
            return
        path, results, lines = self.outcome
        self.state["first_look"] = path
        self.result.setText("<br>".join(lines + [f"Written: {path}"]))


class AberrationsCorrectionPage(QtWidgets.QWizardPage):
    """
    DRAFT. Correction of the common aberrations: Zernike (m-lambda) per SLM half, entered by hand
    for now (later: the common part of the field model of the fit), shown on the SLM as minus
    the wavefront in the pupil of each half (radius of align_BFP). Also for the sign /
    orientation test: one known mode at a time, measured by the stacks.
    """

    MODES = {4: "Z4 defocus", 5: "Z5 astigmatism 45°", 6: "Z6 astigmatism 0°",
             7: "Z7 coma y", 8: "Z8 coma x", 9: "Z9 trefoil", 10: "Z10 trefoil",
             11: "Z11 spherical"}

    def __init__(self, panels, state):
        import aberrations
        super().__init__()
        self.setTitle("Correction of the common aberrations (SLM)")
        self.slm, self.state = panels["slm"], state
        self.radius, self.radius_file = aberrations.bfp_radius()
        text = QtWidgets.QLabel(
            "Aberrations to cancel, in m&lambda; rms (Noll), for each SLM half (= each channel). "
            "<b>For now entered by hand</b> (the fit will give the common part of each mode). "
            "The SLM shows minus this wavefront in the BFP of each half, about the nominal "
            "centre (moved onto the measured BFP by align_BFP if linked). To check the sign and "
            "the orientation first: one mode at a time (e.g. +50 m&lambda; of Z6), then a z "
            f"stack. BFP radius: {'unknown' if self.radius is None else f'{self.radius:.1f}'} "
            f"SLM px (align_BFP{'' if self.radius_file is None else ' ' + os.path.basename(self.radius_file)}).")
        text.setWordWrap(True)
        self.table = QtWidgets.QTableWidget(len(self.MODES), 2)
        self.table.setHorizontalHeaderLabels(["SLM half 0 (left)", "SLM half 1 (right)"])
        self.table.setVerticalHeaderLabels(list(self.MODES.values()))
        for row in range(len(self.MODES)):
            for col in range(2):
                self.table.setItem(row, col, QtWidgets.QTableWidgetItem("0"))
        show = QtWidgets.QPushButton("Show the correction on the SLM")
        show.clicked.connect(self.show_correction)
        self.result = QtWidgets.QLabel()
        self.result.setWordWrap(True)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(show)
        row.addStretch()
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(self.table)
        layout.addLayout(row)
        layout.addWidget(self.result)

    def coefficients(self):
        out = {}
        for col in range(2):
            modes = {}
            for row, j in enumerate(self.MODES):
                value = float(self.table.item(row, col).text().replace(",", ".") or 0)
                if value:
                    modes[j] = value
            if modes:
                out[col] = modes
        return out

    def show_correction(self):
        import aberrations
        try:
            if self.radius is None:
                raise ValueError("No align_BFP result: the BFP radius on the SLM is unknown.")
            coefficients = self.coefficients()
            phase = aberrations.correction_phase(coefficients, self.radius)
            self.state["correction"] = coefficients
            self.state["correction_phase"] = phase
            mask = aberrations.add_to_mask(flat_mask(0), phase)
            if self.slm.links:
                mask = correct_mask(mask, self.slm.links)
            self.slm.show_mask(mask, "aberration_correction")
            self.result.setText(f"On the SLM: correction {coefficients or 'none (all 0)'}"
                                f"{' (corrected by the linked calibrations)' if self.slm.links else ''}.")
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "Error", str(error))


class AberrationsStorePage(QtWidgets.QWizardPage):
    """DRAFT. Writes what this window has (recordings, first look, correction applied, check);
    the zero-mean field model of each mode will be added once the fit exists."""

    def __init__(self, state):
        super().__init__()
        self.setTitle("Calibration file (draft)")
        self.state = state
        text = QtWidgets.QLabel(
            "Will store: the correction applied (common Zernike per channel / SLM half) and the "
            "<b>zero-mean field-dependent aberrations</b> of each mode per channel (polynomial of "
            "the position in the channel frame), with the check. <b>For now</b> (no fit yet): the "
            "recordings, the first look file and the correction applied, in "
            "<code>X_aberrations_calibration_&lt;date_time&gt;.json</code> next to the first "
            "stack.")
        text.setWordWrap(True)
        button = QtWidgets.QPushButton("Write the calibration file")
        button.clicked.connect(self.write)
        self.result = QtWidgets.QLabel()
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(button)
        row.addStretch()
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(text)
        layout.addLayout(row)
        layout.addWidget(self.result)
        layout.addStretch()

    def write(self):
        import json
        import aberrations
        stacks = self.state["stacks"]
        if not stacks:
            self.result.setText("No z stack recorded.")
            return
        now = datetime.now()
        path = os.path.join(stacks[0]["folder"], f"{stacks[0]['index']}_{aberrations.NAME}_"
                                                 f"calibration_{now:%Y_%m_%d_%H%M%S}.json")
        with open(path, "w") as f:
            json.dump({"type": "aberrations_calibration", "status": "draft (no fit yet)",
                       "date": now.isoformat(timespec="seconds"), "stacks": stacks,
                       "first_look": self.state["first_look"],
                       "correction_mlambda": {str(k): v for k, v in
                                              self.state["correction"].items()},
                       "field_dependent": None,
                       "notes": "Zernike Noll, rms m-lambda; correction = minus the wavefront "
                                "in each SLM half (key = half)."}, f, indent=1)
        self.result.setText(f"Written: {path}")


ALIGN_BFP = Calibration(
    name="align_BFP",
    title="Align BFP",
    description="Position of the two back focal planes (2 channels) on the SLM.",
    requirements=["A half-wave plate in the beam, before the SLM.",
                  "The 50/50 beam splitter in the beam.",
                  "A polarization analyzer in the beam, before the Orca camera."],
    devices=["microscope", "filter_wheels", "prime", "orca", "slm"],
    side="Left",
    steps=align_bfp_steps,
)

SLM_PHASE = Calibration(
    name="slm_phase",
    title="SLM phase",
    description="Phase applied by the SLM for each gray level 0-255, for each Orca pixel of "
                "the BFPs (polarimetry with the quarter-wave plate turned by hand).",
    requirements=["A half-wave plate in the beam, before the SLM.",
                  "The 50/50 beam splitter in the beam.",
                  "The quarter-wave plate in its rotation mount, then the polarizer, before "
                  "the Orca camera."],
    devices=["microscope", "filter_wheels", "prime", "orca", "slm"],
    side="Left",
    steps=slm_phase_steps,
)

CHANNELS_GEOMETRY = Calibration(
    name="channels_geometry",
    title="Channels geometry",
    description="Correspondence between the two channels of the Prime (top = reference, x right "
                "y up; bottom, x right y down): vector field and rotation, from a white lamp "
                "image and TetraSpeck bead images. A mapping only: no image is corrected.",
    requirements=["The two channels on the Prime (top and bottom), the detection path as for the "
                  "experiments."],
    devices=["microscope", "filter_wheels", "prime"],
    side="Left",
    steps=channels_geometry_steps,
)

INPUT_POLAR_SLM = Calibration(
    name="input_polar_SLM",
    title="Input polar SLM",
    description="Polarization of the light reaching the SLM (both channels, per Orca pixel of the "
                "BFPs) and its stability in time: the analyzer right before the SLM, a time "
                "series for each of the 9 angles of its quarter-wave plate (turned by hand).",
    requirements=["NO half-wave plate in the beam before the SLM.",
                  "The analyzer (the quarter-wave plate in its rotation mount, then the "
                  "polarizer) right before the SLM.",
                  "No analyzer before the Orca camera.",
                  "The 50/50 beam splitter in the beam."],
    devices=["microscope", "filter_wheels", "prime", "orca", "slm"],
    side="Left",
    steps=input_polar_steps,
)

ABERRATIONS = Calibration(
    name="aberrations",
    title="Aberrations (draft)",
    description="DRAFT. Field-dependent aberrations from z stacks of 200 nm TetraSpeck beads "
                "(piezo), without and with phase masks on the SLM; the common part corrected by "
                "an SLM mask and checked; the zero-mean field-dependent part stored. The fit is "
                "not written yet.",
    requirements=["The detection path as for the experiments (two channels on the Prime, SLM "
                  "in the path, no analyzer).",
                  "TetraSpeck beads 200 nm in glycerol, sparse."],
    devices=["microscope", "filter_wheels", "prime", "piezo", "slm"],
    side="Left",
    steps=aberrations_steps,
)

CALIBRATIONS = [ALIGN_BFP, SLM_PHASE, CHANNELS_GEOMETRY, INPUT_POLAR_SLM, ABERRATIONS]

# ---------------------------------------------------------------- calibrations linked to the masks


@dataclass
class LinkableCalibration:
    """
    A kind of calibration file that corrects the SLM masks.
    name: calibration name (folders <name>/YYYY_MM_DD in saving.CALIBRATION_ROOT).
    pattern: file names of its calibration files in these folders (glob).
    check(content): None if the file can correct masks, else why not.
    correct(pattern, content): the corrected mask.
    describe(content): short text of the correction.
    """
    name: str
    pattern: str
    check: callable
    correct: callable
    describe: callable

    def files(self, root=None):
        """Its calibration files, newest first (date and time at the end of the name)."""
        import glob
        paths = glob.glob(os.path.join(root or saving.CALIBRATION_ROOT, self.name, "*",
                                       self.pattern))
        return sorted(paths, key=lambda p: os.path.splitext(os.path.basename(p))[0][-17:],
                      reverse=True)


def _bfp_check(content):
    if not content.get("channels"):
        return "no BFP centre in it (SLM half of the disks unknown): record align_BFP again"
    return None


def _bfp_correct(pattern, content):
    import bfp_analysis
    return bfp_analysis.shift_halves(pattern, bfp_analysis.bfp_shifts(content))


def _bfp_describe(content):
    import bfp_analysis
    shifts = bfp_analysis.bfp_shifts(content)
    parts = [f"half {half}: {rows:+d} rows, {cols:+d} columns"
             for half, (rows, cols) in sorted(shifts.items())]
    parts += [f"half {half}: not moved (not measured)"
              for half in range(len(content["settings"]["nominal_centers_row_col"]))
              if half not in shifts]
    return "; ".join(parts)


# slm_phase: for now the global (median) phase curve of the right channel, applied to the right
# SLM half only (half 1: the right Orca disk goes through it, align_BFP 2026-11-02); the left half
# is left as it is. A mask is read as wanted phase = 2 pi x gray / 255 (nominal "255 = 2 pi").
PHASE_HALF = 1
ORCA_WIDTH = 2048  # px: the right channel = the disk centred in the right half of the Orca


def _phase_disk(content):
    """The disk of the right channel in an slm_phase result, None if there is none."""
    right = [d for d in content.get("disks", []) if d["center_orca_xy"][0] >= ORCA_WIDTH / 2]
    return right[0] if right else None


def _phase_check(content):
    if content.get("type") != "slm_phase_calibration":
        return "not an slm_phase result"
    if _phase_disk(content) is None:
        return "no disk of the right channel in it"
    return None


def phase_lut(content):
    """Gray level to send (uint8, 256) for each mask gray level g, so that the SLM applies the
    phase 2 pi g / 255 (the median phase curve of the right channel, inverted; phases beyond
    its last level: 255)."""
    disk = _phase_disk(content)
    levels = np.asarray(content.get("levels", list(range(256))), dtype=float)
    curve = np.maximum.accumulate(np.asarray(disk["phase_median_rad"], dtype=float))
    curve = curve + np.arange(len(curve)) * 1e-9  # strictly increasing for the inversion
    wanted = 2 * np.pi * np.arange(256) / 255
    return np.clip(np.round(np.interp(wanted, curve, levels)), 0, 255).astype(np.uint8)


def _phase_correct(pattern, content):
    pattern = np.array(pattern, copy=True)
    width = pattern.shape[1] // 2
    part = pattern[:, PHASE_HALF * width:(PHASE_HALF + 1) * width]
    pattern[:, PHASE_HALF * width:(PHASE_HALF + 1) * width] = \
        phase_lut(content)[part.astype(np.uint8)]
    return pattern


def _phase_describe(content):
    disk = _phase_disk(content)
    lut = phase_lut(content)
    two_pi = disk.get("gray_of_2pi")
    return (f"half {PHASE_HALF}: gray levels for phase 2 pi g / 255 from the global curve of the "
            f"right channel (2 pi at gray {'-' if two_pi is None else f'{two_pi:.1f}'}; "
            f"64 -> {lut[64]}, 128 -> {lut[128]}, 192 -> {lut[192]}, 255 -> {lut[255]}); "
            f"half {1 - PHASE_HALF}: not changed")


LINKABLE_CALIBRATIONS = [  # applied in this order: geometry first, then the phase
    LinkableCalibration("align_BFP", "*_bfp_calibration_*.json",
                        _bfp_check, _bfp_correct, _bfp_describe),
    LinkableCalibration("slm_phase", "*_slm_phase_*.json",
                        _phase_check, _phase_correct, _phase_describe),
]


def _kind(name):
    return next(k for k in LINKABLE_CALIBRATIONS if k.name == name)


def load_linked(name, path):
    """{"file", "content"} of a calibration file of the linkable calibration name; ValueError
    if it cannot correct masks."""
    import json
    with open(path) as f:
        content = json.load(f)
    problem = _kind(name).check(content)
    if problem:
        raise ValueError(f"{os.path.basename(path)}: {problem}.")
    return {"file": os.path.abspath(path), "content": content}


def correct_mask(pattern, links):
    """pattern corrected by the linked calibrations {name: {"file", "content"}}, in the order of
    LINKABLE_CALIBRATIONS."""
    for kind in LINKABLE_CALIBRATIONS:
        if kind.name in links:
            pattern = kind.correct(pattern, links[kind.name]["content"])
    return pattern


def describe_link(name, link):
    """Metadata of a linked calibration (the file itself is copied with the acquisition)."""
    content = link["content"]
    return {"file": link["file"], "date": content.get("date"),
            "light_path": content.get("light_path"),
            "correction": _kind(name).describe(content)}


class LinkCalibrationDialog(QtWidgets.QDialog):
    """
    Asked when a mask is loaded in the SLM panel: link it to a calibration (one list per kind
    of LINKABLE_CALIBRATIONS: its files, newest first, or "No calibration"); Cancel: the mask
    is not loaded. links(): {name: {"file", "content"}} chosen.
    """

    NONE = "No calibration"

    def __init__(self, links, mask_name=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Link a calibration")
        self.chosen = {}
        layout = QtWidgets.QVBoxLayout(self)
        what = f"the mask <b>{mask_name}</b>" if mask_name else "the SLM masks"
        text = QtWidgets.QLabel(
            f"Link {what} to a calibration? The mask sent to the SLM is then corrected "
            f"(first align_BFP: each SLM half moved onto the measured BFP of its channel; then "
            f"slm_phase: each gray level replaced by the one giving the phase 2&pi; g / 255, "
            f"right half for now), and the calibration files are copied with each acquisition.")
        text.setWordWrap(True)
        layout.addWidget(text)
        self.combos = {}
        form = QtWidgets.QFormLayout()
        for kind in LINKABLE_CALIBRATIONS:
            combo = QtWidgets.QComboBox()
            combo.addItem(self.NONE, None)
            files = kind.files()
            current = links.get(kind.name, {}).get("file")
            if current and os.path.normcase(current) not in map(os.path.normcase, files):
                files = [current] + files
            for path in files:
                combo.addItem(self.label(path), path)
            if current:  # the linked one, else the newest
                combo.setCurrentIndex(max(combo.findData(current), 0))
            elif files:
                combo.setCurrentIndex(1)
            browse = QtWidgets.QPushButton("Browse...")
            browse.clicked.connect(lambda _, k=kind, c=combo: self.browse(k, c))
            row = QtWidgets.QHBoxLayout()
            row.addWidget(combo, 1)
            row.addWidget(browse)
            form.addRow(kind.name, row)
            self.combos[kind.name] = combo
        layout.addLayout(form)
        buttons = QtWidgets.QHBoxLayout()
        for label, function in (("Link", self.accept), (self.NONE, self.no_calibration),
                                ("Cancel", self.reject)):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(function)
            buttons.addWidget(button)
        buttons.insertStretch(0)
        layout.addLayout(buttons)
        self.setMinimumWidth(650)

    @staticmethod
    def label(path):
        return f"{os.path.basename(os.path.dirname(path))} / {os.path.basename(path)}"

    def browse(self, kind, combo):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, f"{kind.name} calibration file", type_folder(kind.name),
            f"Calibration ({kind.pattern})")
        if path:
            path = os.path.abspath(path)
            if combo.findData(path) < 0:
                combo.addItem(self.label(path), path)
            combo.setCurrentIndex(combo.findData(path))

    def no_calibration(self):
        for combo in self.combos.values():
            combo.setCurrentIndex(0)
        self.accept()

    def accept(self):
        chosen = {}
        try:
            for name, combo in self.combos.items():
                if combo.currentData():
                    chosen[name] = load_linked(name, combo.currentData())
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "Calibration not usable", str(error))
            return
        self.chosen = chosen
        super().accept()

    def links(self):
        return self.chosen


class RequirementsPage(QtWidgets.QWizardPage):
    """The optical elements to put in the light path; Next once all are ticked."""

    def __init__(self, calibration):
        super().__init__()
        self.setTitle("Optical elements")
        self.setSubTitle(f"{calibration.description} Put these elements in the light path, "
                         f"then tick each one.")
        layout = QtWidgets.QVBoxLayout(self)
        self.boxes = []
        for text in calibration.requirements:
            box = QtWidgets.QCheckBox(text)
            box.toggled.connect(self.completeChanged)
            layout.addWidget(box)
            self.boxes.append(box)
        layout.addStretch()

    def isComplete(self):
        return all(box.isChecked() for box in self.boxes)


class DevicesPage(QtWidgets.QWizardPage):
    """The devices needed, checked every DEVICE_CHECK_PERIOD; Next once all are connected."""

    def __init__(self, calibration, device_panels):
        """device_panels: {key: (title, panel)} of the GUI (device None if not connected)."""
        super().__init__()
        self.setTitle("Devices")
        self.setSubTitle("These devices must be connected. Connect them here or in their panel.")
        self.panels = [device_panels[key] for key in calibration.devices]
        grid = QtWidgets.QGridLayout(self)
        self.rows = []
        for row, (title, panel) in enumerate(self.panels):
            status = QtWidgets.QLabel()
            button = QtWidgets.QPushButton("Connect")
            button.clicked.connect(lambda _, panel=panel: self.connect_device(panel))
            grid.addWidget(QtWidgets.QLabel(f"<b>{title}</b>"), row, 0)
            grid.addWidget(status, row, 1)
            grid.addWidget(button, row, 2)
            self.rows.append((panel, status, button))
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(len(self.panels), 1)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(DEVICE_CHECK_PERIOD)
        self.timer.timeout.connect(self.check)

    def initializePage(self):
        self.check()
        self.timer.start()

    def cleanupPage(self):  # Back
        self.timer.stop()

    def connect_device(self, panel):
        if panel.device is None:
            panel.toggle_connection()  # shows the error if it fails
        self.check()

    def check(self):
        for panel, status, button in self.rows:
            connected = panel.device is not None
            if panel.device_class is None:
                text, color = f"not available: {panel.import_error}", MISSING_COLOR
            else:
                text, color = ("connected", OK_COLOR) if connected else ("not connected",
                                                                         MISSING_COLOR)
            status.setText(text)
            status.setStyleSheet(f"color: {color}; font-weight: bold")
            button.setEnabled(not connected and panel.device_class is not None)
        self.completeChanged.emit()

    def isComplete(self):
        return all(panel.device is not None for panel, _, _ in self.rows)


class LightPathPage(QtWidgets.QWizardPage):
    """
    The light path of the calibration: the microscope goes to it when the page opens (button to
    do it again), the user puts the emission filter of that path and ticks; Next once both.
    """

    def __init__(self, side, nikon, filter_wheels=None):
        super().__init__()
        self.side, self.nikon = side, nikon
        color, name = SIDE_COLORS[side], SIDE_COLOR_NAMES[side]
        self.setTitle(f"Light path: {side} ({name})")
        text = QtWidgets.QLabel(
            f"This calibration is for the <b style='color: {color}'>{side.lower()} path "
            f"({name})</b>. The microscope goes to the {side.lower()} path by itself.<br>"
            f"Choose the <b>emission filter of the {side.lower()} path ({name})</b> (filter "
            f"wheels panel), then tick.")
        text.setWordWrap(True)
        button = QtWidgets.QPushButton(f"Go to the {side.lower()} path")
        button.clicked.connect(self.go)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(button)
        if filter_wheels is not None:
            show = QtWidgets.QPushButton("Show the filter wheels")
            show.clicked.connect(lambda: show_panel(filter_wheels))
            buttons.addWidget(show)
        buttons.addStretch()
        self.status = QtWidgets.QLabel()
        self.box = QtWidgets.QCheckBox(
            f"The emission filter of the {side.lower()} path ({name}) is in place.")
        self.box.toggled.connect(self.completeChanged)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(text)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        layout.addWidget(self.box)
        layout.addStretch()
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(DEVICE_CHECK_PERIOD)
        self.timer.timeout.connect(self.update_status)

    def path(self):
        """Light path shown by the microscope panel (read every 0.5 s by the panel)."""
        return self.nikon.light_path.currentText() if self.nikon.device is not None else ""

    def on_side(self):
        return SIDE_LIGHT_PATHS[self.side].lower() in self.path().lower()

    def go(self):
        try:
            if self.nikon.device is None:
                raise RuntimeError("The microscope is not connected.")
            self.nikon.device.set_light_path(SIDE_LIGHT_PATHS[self.side])
            self.nikon.refresh()
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "Error", f"Light path not changed: {error}")
        self.update_status()

    def update_status(self):
        path = self.path() or "unknown"
        if self.on_side():
            text, color = f"Light path: {path}", SIDE_COLORS[self.side]
        else:
            text, color = (f"Light path: {path}, not {SIDE_LIGHT_PATHS[self.side]} (the "
                           f"{self.side.lower()} path)", NEUTRAL_COLOR)
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color}; font-weight: bold")
        self.completeChanged.emit()

    def initializePage(self):
        if not self.on_side():
            self.go()
        self.update_status()
        self.timer.start()

    def cleanupPage(self):  # Back
        self.timer.stop()

    def isComplete(self):
        return self.on_side() and self.box.isChecked()

    def validatePage(self):
        self.timer.stop()
        return True


class InstructionPage(QtWidgets.QWizardPage):
    """
    A step of a calibration: text, buttons, a status line, an optional box to tick.
    actions: [(button text, function())]; function None = not written yet (button disabled).
    status: function() -> (text, color), refreshed every DEVICE_CHECK_PERIOD while shown.
    confirm: text of a box to tick before Next.
    on_next: function() run when Next is clicked (an error is shown and the page stays).
    """

    def __init__(self, title, text, actions=(), status=None, confirm=None, on_next=None):
        super().__init__()
        self.setTitle(title)
        self.status_function = status
        self.on_next = on_next
        layout = QtWidgets.QVBoxLayout(self)
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label)
        buttons = QtWidgets.QHBoxLayout()
        for text_, function in actions:
            button = QtWidgets.QPushButton(text_)
            if function is None:
                button.setEnabled(False)
                button.setToolTip("Not written yet")
            else:
                button.clicked.connect(lambda _, f=function: self.run(f))
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.status = QtWidgets.QLabel()
        layout.addWidget(self.status)
        self.box = None
        if confirm:
            self.box = QtWidgets.QCheckBox(confirm)
            self.box.toggled.connect(self.completeChanged)
            layout.addWidget(self.box)
        layout.addStretch()
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(DEVICE_CHECK_PERIOD)
        self.timer.timeout.connect(self.update_status)

    def run(self, function):
        try:
            function()
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "Error", str(error))
        self.update_status()

    def update_status(self):
        if self.status_function is not None:
            text, color = self.status_function()
            self.status.setText(text)
            self.status.setStyleSheet(f"color: {color}; font-weight: bold")

    def initializePage(self):
        self.update_status()
        self.timer.start()

    def cleanupPage(self):  # Back
        self.timer.stop()

    def isComplete(self):
        return self.box is None or self.box.isChecked()

    def validatePage(self):  # Next
        if self.on_next is not None:
            try:
                self.on_next()
            except Exception as error:
                QtWidgets.QMessageBox.critical(self, "Error", str(error))
                return False
        self.timer.stop()
        return True


class CalibrationWizard(QtWidgets.QWizard):
    """
    Step-by-step window of a calibration. Not modal: the rest of the GUI stays usable (to
    connect a device or look at a camera meanwhile).
    """

    def __init__(self, calibration, device_panels, tools=None, parent=None):
        super().__init__(parent)
        self.calibration = calibration
        side = f" ({calibration.side} path)" if calibration.side else ""
        self.setWindowTitle(f"Calibration: {calibration.name}{side}")
        self.setWizardStyle(QtWidgets.QWizard.ClassicStyle)
        panels = {key: panel for key, (_, panel) in device_panels.items()}
        tools = {**(tools or {}), "side": calibration.side}
        self.pages = [RequirementsPage(calibration), DevicesPage(calibration, device_panels)]
        if calibration.side is not None:
            self.pages.append(LightPathPage(calibration.side, panels["microscope"],
                                            panels.get("filter_wheels")))
        if calibration.steps is not None:
            self.pages += calibration.steps(panels, tools)
        for number, page in enumerate(self.pages, 1):
            page.setTitle(f"{number}. {page.title()}")
            self.addPage(page)
        self.resize(760, 460)

    def done(self, result):
        for page in self.pages:
            if hasattr(page, "timer"):
                page.timer.stop()
        super().done(result)
