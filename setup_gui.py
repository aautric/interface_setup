"""
User interface of the setup:
    - SLM (Thorlabs EXULUS-HD2): load / preview / send phase masks
    - Prime 95B, Orca Flash4.0 and FLIR Blackfly S cameras: live, snap, settings, saving
      with metadata
    - Nikon Ti microscope: light path, focus, XY stage, Perfect Focus (objective only displayed)
    - Objective piezo (Thorlabs PPC001): open loop (V) or closed loop (µm)
    - Filter wheels and shutters (Sutter Lambda 10-3)

Each device has its own panel, in a dock window that can be:
    - hidden / shown with the toolbar buttons (or the View menu),
    - tabbed with another panel (drag its title bar onto the other panel),
    - taken out as a separate window (double-click its title bar, or drag it out),
      e.g. to put it on another screen; such windows can be minimized / maximized,
    - put back in the main window with View > Dock all panels.
The layout is saved when the window is closed and restored at the next start.

Requires: PyQt5, pyqtgraph and the requirements of each device module (setup_env)
Run:      python setup_gui.py
"""

import importlib
import os
import sys
import time

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtCore, QtGui, QtWidgets

from calibration import (CALIBRATIONS, CalibrationWizard, LinkCalibrationDialog, correct_mask,
                         describe_link, load_linked)
from SLM import SLM, SLM_HEIGHT, SLM_WIDTH, load_phase_pattern
from saving import (ACQUISITION_ROOT, STAGING_ROOT, FileMover, SequenceWriter,
                    check_free_space, experiment_folder, next_index)


def optional_import(module, name):
    """Import a device class: (class, None), or (None, error) if its library is missing."""
    try:
        return getattr(importlib.import_module(module), name), None
    except Exception as error:  # the other panels still work
        return None, error


Camera, CAMERA_ERROR = optional_import("camera", "Camera")
Orca, ORCA_ERROR = optional_import("orca", "Orca")
Flir, FLIR_ERROR = optional_import("flir", "Flir")
NikonTi, NIKON_ERROR = optional_import("nikon_ti", "NikonTi")
FILTER_CUBES = optional_import("nikon_ti", "FILTER_CUBES")[0] or []
DEFAULT_FILTER_CUBE = optional_import("nikon_ti", "DEFAULT_FILTER_CUBE")[0] or "unknown"
# colour of the light path box: where the light goes (side of the microscope)
LIGHT_PATH_COLORS = {"Left": "#d32f2f", "Right": "#388e3c"}  # red, green
Piezo, PIEZO_ERROR = optional_import("piezo", "Piezo")
FilterWheel, FILTER_WHEEL_ERROR = optional_import("filter_wheel", "FilterWheel")
# folder opened first by "Load image / .npy..." of the SLM panel (easy masks, see its README)
SLM_MASK_FOLDER = os.path.join(os.path.expanduser("~"), "Desktop", "SLM_masks")

pg.setConfigOptions(imageAxisOrder="row-major")  # numpy arrays are (y, x)

LIVE_PERIOD = 30           # ms between two live frames
TEMPERATURE_PERIOD = 2000  # ms between two temperature readings
POSITION_PERIOD = 500      # ms between two readings of the microscope / piezo state
MAX_Z_JUMP = 100           # µm, larger focus moves ask for a confirmation
MAX_PIEZO_JUMP = 20        # µm, larger piezo moves ask for a confirmation
MIN_VIEW_SIZE = 300        # px, minimum size of the image displays
AUTO_LEVELS_PERCENTILES = (0.5, 99.5)  # auto contrast range, ignoring hot / dead pixels
SEQUENCE_DISPLAY_PERIOD = 100  # ms between two frames shown during a sequence
MAX_FRAMES = 10_000_000  # frames of a sequence (the disk space is checked before starting)
# window and taskbar icon (kept outside the repository); default Qt icon if missing
ICON_PATH = os.path.join(os.path.expanduser("~"), "Pictures", "bumblebee.ico")


def make_image_view():
    """Image display with contrast histogram, without the ROI/Norm buttons."""
    view = pg.ImageView()
    view.ui.roiBtn.hide()
    view.ui.menuBtn.hide()
    view.setMinimumSize(MIN_VIEW_SIZE, MIN_VIEW_SIZE)
    return view


def auto_levels(frame, low=AUTO_LEVELS_PERCENTILES[0], high=AUTO_LEVELS_PERCENTILES[1]):
    """
    Display levels between two percentiles of the image (on a subsample, for speed), so that
    a few hot pixels do not squeeze the rest of the image into black (as min / max would).
    """
    step = max(1, int(np.sqrt(frame.size / 250_000)))
    lo, hi = np.percentile(frame[::step, ::step], (low, high))
    return (lo, hi) if hi > lo else (lo, lo + 1)


def light_path_color(path):
    """Colour of a light path ('2-Left100' -> red, '3-Right100' -> green), or None (eyepieces)."""
    for side, color in LIGHT_PATH_COLORS.items():
        if side.lower() in path.lower():
            return color
    return None


def pfs_color(status):
    """Colour of the Perfect Focus status: green locked, orange searching, red otherwise."""
    status = status.lower()
    if "lock" in status:
        return "#2e7d32"
    if any(word in status for word in ("focusing", "search", "within")) and "out of" not in status:
        return "#ef6c00"
    return "#c62828"


def show_error(parent, error):
    QtWidgets.QMessageBox.critical(parent, "Error", str(error))


def confirm(parent, text):
    answer = QtWidgets.QMessageBox.question(parent, "Confirm", text)
    return answer == QtWidgets.QMessageBox.Yes


def format_duration(seconds):
    """'2 h 05 min', '3 min 20 s' or '12 s'."""
    seconds = int(round(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"
    if seconds >= 60:
        return f"{seconds // 60} min {seconds % 60:02d} s"
    return f"{seconds} s"


def spin_box(minimum, maximum, value, suffix="", decimals=None):
    """QSpinBox (decimals None) or QDoubleSpinBox with its range, value and unit."""
    box = QtWidgets.QSpinBox() if decimals is None else QtWidgets.QDoubleSpinBox()
    if decimals is not None:
        box.setDecimals(decimals)
    box.setRange(minimum, maximum)
    box.setValue(value)
    box.setSuffix(suffix)
    return box


class DevicePanel(QtWidgets.QWidget):
    """
    Common part of the device panels: Connect button and status line (self.top),
    widgets enabled only when connected (self.device_widgets), metadata for the images.
    """

    device_class = None  # class of the device module (SLM, Camera...), None if not importable
    import_error = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.device = None
        self.device_widgets = []
        self.connect_button = QtWidgets.QPushButton("Connect")
        self.status = QtWidgets.QLabel()
        self.top = QtWidgets.QHBoxLayout()
        self.top.addWidget(self.connect_button)
        self.top.addWidget(self.status, 1)
        self.connect_button.clicked.connect(self.toggle_connection)

    def make_device(self):
        return self.device_class()

    def device_name(self):
        return ""

    def on_connected(self):
        """Called after a successful connection."""

    def update_state(self):
        connected = self.device is not None
        self.connect_button.setText("Disconnect" if connected else "Connect")
        self.connect_button.setEnabled(self.device_class is not None)
        if self.device_class is None:
            self.status.setText(f"Not available: {self.import_error}")
        else:
            self.status.setText(f"Connected ({self.device_name()})" if connected else "Disconnected")
        for widget in self.device_widgets:
            widget.setEnabled(connected)

    def toggle_connection(self):
        try:
            if self.device is None:
                device = self.make_device()
                device.connect()
                self.device = device
                try:
                    self.on_connected()
                except Exception:
                    self.close_device()  # not left half connected
                    raise
            else:
                self.close_device()
        except Exception as error:
            show_error(self, error)
        self.update_state()

    def close_device(self):
        if self.device is not None:
            self.device.close()
            self.device = None

    def get_metadata(self):
        """State of the device to save with the camera images (empty if not connected)."""
        return {}


# ---------------------------------------------------------------- SLM


class SLMPanel(DevicePanel):
    """
    Load, preview and send phase masks to the SLM. A loaded mask can be linked to calibrations
    (asked at each loading, or "Calibration..."; calibration.LINKABLE_CALIBRATIONS): the mask
    previewed and sent is then the corrected one (align_BFP: each SLM half moved onto its
    BFP), the link is shown in the corner of the display and saved with the acquisitions.
    Masks put by scripts / calibrations (show_mask) are never corrected.
    """

    device_class = SLM

    def __init__(self, parent=None):
        super().__init__(parent)
        self.original = np.zeros((SLM_HEIGHT, SLM_WIDTH), dtype=np.uint8)  # as loaded
        self.pattern = self.original  # as sent: original corrected by pattern_links
        self.pattern_name = "blank"
        self.links = {}  # linked calibrations {name: {"file", "content"}}
        self.pattern_links = {}  # those applied to self.pattern
        self.script_mask = False  # self.pattern put by show_mask (never corrected)
        self.shown_name = None  # name of the mask currently displayed on the SLM
        self.shown_pattern = None  # the mask itself (saved with the camera images)
        self.shown_links = {}  # calibrations applied to it

        self.view = make_image_view()
        # linked calibrations in the top-left corner of the display
        self.link_overlay = QtWidgets.QLabel(self.view)
        self.link_overlay.setStyleSheet(
            f"background-color: {LIGHT_PATH_COLORS['Left']}; color: white; padding: 3px 6px; "
            f"font-weight: bold;")
        self.link_overlay.move(8, 8)
        self.link_overlay.hide()
        self.load_button = QtWidgets.QPushButton("Load image / .npy...")
        self.send_button = QtWidgets.QPushButton("Send to SLM")
        self.blank_button = QtWidgets.QPushButton("Blank (0)")
        self.link_button = QtWidgets.QPushButton("Calibration...")
        self.link_button.setToolTip("Link the masks to a calibration (e.g. align_BFP: each SLM "
                                    "half moved onto the measured BFP of its channel)")
        self.auto_send = QtWidgets.QCheckBox("Send automatically after loading")
        self.mask_label = QtWidgets.QLabel()

        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.load_button)
        buttons.addWidget(self.send_button)
        buttons.addWidget(self.blank_button)
        buttons.addWidget(self.link_button)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(self.top)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.mask_label)
        layout.addLayout(buttons)
        layout.addWidget(self.auto_send)

        self.load_button.clicked.connect(self.load_file)
        self.send_button.clicked.connect(self.send)
        self.blank_button.clicked.connect(self.blank)
        self.link_button.clicked.connect(lambda: self.ask_links())

        self.device_widgets = [self.send_button]
        self.update_state()
        self.display_pattern()

    def device_name(self):
        return self.device.name

    def display_pattern(self):
        self.view.setImage(self.pattern, levels=(0, 255))
        corrected = " (corrected: " + ", ".join(self.pattern_links) + ")" \
            if self.pattern_links else ""
        self.mask_label.setText(f"Mask: {self.pattern_name}{corrected}  "
                                f"(min {self.pattern.min()}, max {self.pattern.max()})")
        self.update_link_overlay()

    def update_link_overlay(self):
        """'Calibration linked: <file>' in the corner of the display (tooltip: the correction)."""
        if not self.links:
            self.link_overlay.hide()
            return
        lines, tips = [], []
        for name, link in self.links.items():
            lines.append(f"Calibration linked: {os.path.splitext(os.path.basename(link['file']))[0]}")
            tips.append(f"{name}: {link['file']}\n{describe_link(name, link)['correction']}")
        if self.links != self.pattern_links:  # mask put by a script / calibration
            lines.append("(not applied to this mask)")
        self.link_overlay.setText("\n".join(lines))
        self.link_overlay.setToolTip("\n\n".join(tips))
        self.link_overlay.adjustSize()
        self.link_overlay.show()
        self.link_overlay.raise_()

    def apply_links(self):
        """The mask to send: the loaded one corrected by the linked calibrations."""
        self.pattern = correct_mask(self.original, self.links)
        self.pattern_links = dict(self.links)
        self.display_pattern()

    def ask_links(self, mask_name=None):
        """Ask which calibrations to link (False if cancelled). Called alone ("Calibration..."),
        the mask is corrected again and re-sent if it was on the SLM."""
        dialog = LinkCalibrationDialog(self.links, mask_name, self)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return False
        if mask_name is None:
            self.link(dialog.links())
        else:
            self.links = dialog.links()
        return True

    def link(self, links):
        """Link these calibrations {name: {"file", "content"}} and correct the mask again;
        re-sent if it was on the SLM."""
        self.links = links
        if self.script_mask:  # left as it is, the next loaded mask is corrected
            self.update_link_overlay()
            return
        was_shown = self.device is not None and self.shown_pattern is self.pattern
        self.apply_links()
        if was_shown:
            self.send()

    def link_calibration(self, name, path):
        """Link one calibration file (e.g. from the align_BFP window); errors raised."""
        self.link({**self.links, name: load_linked(name, path)})

    def set_pattern(self, source, name, ask_links=False):
        """Load a mask (array or file path), display it (corrected by the linked calibrations;
        ask_links: ask which ones first) and send it if asked."""
        try:
            original = load_phase_pattern(source)
        except Exception as error:
            show_error(self, error)
            return
        if ask_links and not self.ask_links(name):
            return  # cancelled: the mask is not loaded
        self.original = original
        self.pattern_name = name
        self.script_mask = False
        self.apply_links()
        if self.auto_send.isChecked() and self.device is not None:
            self.send()

    def load_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load phase mask", SLM_MASK_FOLDER if os.path.isdir(SLM_MASK_FOLDER) else "",
            "Masks (*.png *.bmp *.tif *.tiff *.npy);;All files (*)")
        if path:
            self.set_pattern(path, os.path.basename(path), ask_links=True)

    def blank(self):
        self.set_pattern(np.zeros((SLM_HEIGHT, SLM_WIDTH), dtype=np.uint8), "blank")

    def send(self):
        try:
            self.send_pattern()
        except Exception as error:
            show_error(self, error)

    def show_mask(self, pattern, name):
        """
        Load a mask (array or file) and display it on the SLM now, for scripts and
        calibrations; errors are raised (not shown). The panel shows it as if sent by hand.
        """
        if self.device is None:
            raise RuntimeError("The SLM is not connected.")
        self.original = self.pattern = load_phase_pattern(pattern)  # never corrected
        self.pattern_links = {}
        self.script_mask = True
        self.pattern_name = name
        self.display_pattern()
        self.send_pattern()

    def send_pattern(self):
        self.device.show(self.pattern)
        self.shown_name = self.pattern_name
        self.shown_pattern = self.pattern  # a new array each time a mask is loaded
        self.shown_links = self.pattern_links
        self.status.setText(f"Connected ({self.device_name()}) - showing {self.pattern_name}")

    def close_device(self):
        super().close_device()
        self.shown_name = None
        self.shown_pattern = None
        self.shown_links = {}

    def shown_mask(self):
        """(pattern, name) of the mask displayed on the SLM now, None if none (or not connected)."""
        if self.device is None or self.shown_pattern is None:
            return None
        return self.shown_pattern, self.shown_name

    def get_metadata(self):
        if self.device is None:
            return {}
        return {"link": self.device.name, "monitor": self.device.monitor,
                "size": [self.device.width, self.device.height],
                "settle_time_s": self.device.settle_time,
                "mask_shown": self.shown_name,  # None if nothing sent to the SLM
                # calibrations that corrected it (files copied with the acquisition: saving.py)
                "linked_calibrations": {name: describe_link(name, link)
                                        for name, link in self.shown_links.items()}}


# ---------------------------------------------------------------- cameras


class AdvancedSettingsDialog(QtWidgets.QDialog):
    """
    Window listing all the parameters of a camera (device.get_parameters()), with an editor
    for the adjustable ones (device.set_parameter()). The live is paused while reading or
    changing a parameter. Values different from their default (device parameters with a
    "default") are highlighted; "Reset to defaults" needs device.reset_parameters().
    """

    COLUMNS = ("Parameter", "Value", "Default", "Allowed values / range", "Access", "Description")
    CHANGED = QtGui.QColor(255, 225, 150)  # background of a value different from its default

    def __init__(self, panel, title):
        super().__init__(panel)
        self.panel = panel
        self.setWindowTitle(f"{title} - advanced settings")
        self.resize(1400, 750)

        self.filter = QtWidgets.QLineEdit()
        self.filter.setPlaceholderText("Search a parameter or a description...")
        self.writable_only = QtWidgets.QCheckBox("Adjustable only")
        self.changed_only = QtWidgets.QCheckBox("Changed only")
        self.changed_only.setToolTip("Only the parameters different from their default")
        self.refresh_button = QtWidgets.QPushButton("Refresh")
        self.reset_button = QtWidgets.QPushButton("Reset to defaults...")
        self.reset_button.setToolTip("All adjustable parameters back to the camera defaults "
                                     "(as after connecting), except exposure, binning and ROI")
        self.reset_button.setVisible(hasattr(panel.device, "reset_parameters"))
        self.message = QtWidgets.QLabel()
        self.table = QtWidgets.QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        for column, width in enumerate((None, 170, 120, 220, None, None)):
            if width:
                header.setSectionResizeMode(column, QtWidgets.QHeaderView.Interactive)
                header.resizeSection(column, width)
            else:
                header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(len(self.COLUMNS) - 1, QtWidgets.QHeaderView.Stretch)
        # row heights follow the wrapped descriptions
        header.sectionResized.connect(lambda *_: self.table.resizeRowsToContents())

        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.filter, 1)
        top.addWidget(self.writable_only)
        top.addWidget(self.changed_only)
        top.addWidget(self.refresh_button)
        top.addWidget(self.reset_button)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.message)
        note = QtWidgets.QLabel("Exposure, binning and ROI: use the main panel. Changing a value "
                                "here applies it at once (Enter for typed values). Highlighted: "
                                "different from the default. Disconnect / Connect also brings "
                                "back all the defaults.")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.filter.textChanged.connect(self.apply_filter)
        self.writable_only.toggled.connect(self.apply_filter)
        self.changed_only.toggled.connect(self.apply_filter)
        self.refresh_button.clicked.connect(self.refresh)
        self.reset_button.clicked.connect(self.reset)
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.table.resizeRowsToContents()  # now that the columns have their final width

    def refresh(self):
        """Read all the parameters (live paused) and fill the table."""
        try:
            parameters = self.panel.run_paused(self.panel.device.get_parameters)
        except Exception as error:
            show_error(self, error)
            return
        grey = QtGui.QColor("gray")
        self.table.setRowCount(0)
        self.table.setRowCount(len(parameters))
        n_changed = 0
        for row, p in enumerate(parameters):
            value = "" if p["value"] is None else str(p["value"])
            default = str(p["default"]) if "default" in p else ""
            changed = "default" in p and value != default
            n_changed += changed
            if "options" in p:
                allowed = ", ".join(p["options"])
            elif "min" in p:
                allowed = f"{p['min']:g} .. {p['max']:g}"
            else:
                allowed = ""
            access = "adjustable" if p["writable"] else "read only"
            if not p.get("live", True):
                access += " (not during live)"
            description = p.get("description", "")
            for column, text in ((0, p["name"]), (2, default), (3, allowed), (4, access),
                                 (5, description)):
                item = QtWidgets.QTableWidgetItem(text)
                item.setToolTip(description)
                if not p["writable"]:
                    item.setForeground(grey)
                if changed:
                    item.setBackground(self.CHANGED)
                self.table.setItem(row, column, item)
            if not p["writable"]:
                item = QtWidgets.QTableWidgetItem(value)
                item.setForeground(grey)
                item.setToolTip(description)
                self.table.setItem(row, 1, item)
            elif p["kind"] in ("enum", "bool"):
                box = QtWidgets.QComboBox()
                box.addItems(p["options"])
                box.setCurrentText(value)
                box.activated.connect(lambda _, name=p["name"], box=box: self.apply(name, box.currentText()))
                self.table.setCellWidget(row, 1, box)
            else:
                edit = QtWidgets.QLineEdit(value)
                edit.returnPressed.connect(lambda name=p["name"], edit=edit: self.apply(name, edit.text()))
                self.table.setCellWidget(row, 1, edit)
            self.table.item(row, 0).setData(QtCore.Qt.UserRole, p["writable"])
            self.table.item(row, 0).setData(QtCore.Qt.UserRole + 1, changed)
        self.table.resizeRowsToContents()
        self.message.setText(f"{len(parameters)} parameters, "
                             f"{sum(p['writable'] for p in parameters)} adjustable, "
                             f"{n_changed} different from the default.")
        self.apply_filter()

    def reset(self):
        """All adjustable parameters back to their default (live paused), after confirmation."""
        answer = QtWidgets.QMessageBox.question(
            self, "Reset to defaults",
            "Set all the adjustable parameters back to the camera defaults (as after connecting)?\n"
            "Exposure, binning and ROI are not changed.")
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            changed, errors = self.panel.run_paused(self.panel.device.reset_parameters)
            self.panel.first_display = True
        except Exception as error:
            show_error(self, error)
            return
        self.refresh()
        text = (f"Back to defaults: {', '.join(changed)}." if changed
                else "Nothing to change: all at their default.")
        if errors:
            text += f" Not set: {'; '.join(errors)}"
        self.message.setText(text)

    def apply(self, name, value):
        """Set a parameter (live paused), then re-read everything (others may depend on it)."""
        try:
            self.panel.run_paused(self.panel.device.set_parameter, name, value)
            self.panel.first_display = True  # the image may have changed
            text = f"{name} set to {value}."
        except Exception as error:
            text = f"{name}: could not set {value} ({error})."
        scroll = self.table.verticalScrollBar().value()
        self.refresh()
        self.table.verticalScrollBar().setValue(scroll)
        self.message.setText(text)

    def apply_filter(self):
        text = self.filter.text().lower()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            searched = f"{item.text()} {self.table.item(row, 5).text()}".lower()
            hidden = text not in searched or (
                self.writable_only.isChecked() and not item.data(QtCore.Qt.UserRole)) or (
                self.changed_only.isChecked() and not item.data(QtCore.Qt.UserRole + 1))
            self.table.setRowHidden(row, hidden)


class CameraPanel(DevicePanel):
    """
    Live display, settings, snap and saving of a camera. Common part of the Prime and
    Orca panels, which add their readout settings (make_readout_row / apply_readout).
    """

    sensor_size = 1200    # pixels, updated from the camera at connection
    binnings = ("1", "2", "4")
    has_temperature = False  # True: temperature box in the corner of the image

    def __init__(self, get_extra_metadata=None, get_slm_mask=None, destination=None, mover=None,
                 parent=None):
        """
        get_extra_metadata(): state of the other devices, saved with the images.
        get_slm_mask(): (pattern, name) of the SLM mask shown now, or None; saved with each frame.
        destination(): folder of the saved files (ValueError with the reason if not set).
        mover: saving.FileMover; sequences are then written on the fast disk (STAGING_ROOT)
        and moved to their destination afterwards, if it is on another disk.
        """
        super().__init__(parent)
        self.image = None           # last acquired image (2D) or sequence (3D)
        self.image_metadata = None  # metadata of self.image
        self.image_extra = None     # state of the other devices when self.image was taken
        self.image_slm_mask = None  # SLM mask shown when self.image was taken
        self.get_extra_metadata = get_extra_metadata or dict
        self.get_slm_mask = get_slm_mask or (lambda: None)
        self.destination = destination or os.getcwd
        self.mover = mover
        self.first_display = True

        self.live_timer = QtCore.QTimer(self)
        self.live_timer.setInterval(LIVE_PERIOD)
        self.live_timer.timeout.connect(self.update_live)
        self.temperature_timer = QtCore.QTimer(self)
        self.temperature_timer.setInterval(TEMPERATURE_PERIOD)
        self.temperature_timer.timeout.connect(self.update_temperature)

        self.temperature_label = QtWidgets.QLabel()
        self.top.addWidget(self.temperature_label)

        # display and acquisition
        self.view = make_image_view()
        # temperature in the top-left corner of the image: sensor now / at the last image
        self.temperature_overlay = QtWidgets.QLabel(self.view)
        self.temperature_overlay.setStyleSheet(
            "background-color: rgba(0, 0, 0, 160); color: #ffd54f; padding: 3px 6px; "
            "font-weight: bold;")
        self.temperature_overlay.move(8, 8)
        self.temperature_overlay.hide()
        self.sensor_temperature = "-"  # last texts shown in the box
        self.image_temperature = None
        self.stats_label = QtWidgets.QLabel()
        self.auto_levels = QtWidgets.QCheckBox("Auto contrast")
        self.auto_levels.setChecked(True)
        self.snap_button = QtWidgets.QPushButton("Snap")
        self.live_button = QtWidgets.QPushButton("Start live")

        # settings
        self.exposure = spin_box(1, 60000, 20, " ms")
        self.binning = QtWidgets.QComboBox()
        self.binning.addItems(self.binnings)
        self.roi_boxes = [spin_box(minimum, self.sensor_size, value) for minimum, value
                          in zip((0, 0, 1, 1), (0, 0, self.sensor_size, self.sensor_size))]
        self.roi_button = QtWidgets.QPushButton("Apply ROI")
        self.full_frame_button = QtWidgets.QPushButton("Full frame")
        self.advanced_button = QtWidgets.QPushButton("Advanced settings...")
        self.advanced_button.setToolTip("All the parameters of the camera")
        self.advanced_dialog = None
        self.select_button = QtWidgets.QPushButton("Select on image")
        self.select_button.setCheckable(True)
        self.select_button.setToolTip("Draw the ROI as a rectangle on the image, then Apply ROI.")
        self.roi_rect = pg.RectROI([0, 0], [100, 100], pen=pg.mkPen("y", width=2),
                                   translateSnap=True, scaleSnap=True)
        self.roi_rect.hide()
        self.view.addItem(self.roi_rect)

        # saving
        self.folder_label = QtWidgets.QLabel()
        self.folder_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.n_frames = spin_box(1, MAX_FRAMES, 10)
        self.pixel_bits = QtWidgets.QComboBox()
        self.pixel_bits.addItem("16 bit (2 bytes, as acquired)", 16)
        self.pixel_bits.addItem("8 bit (1 byte, 8 highest bits)", 8)
        self.pixel_bits.setToolTip(
            "Size of the saved pixels. 8 bit halves the files but keeps only the 8 highest\n"
            "bits of the camera values (camera value ~ saved value x 2^saved_value_shift,\n"
            "in the metadata): faint signals become coarse. Live and display are not changed.")
        self.size_label = QtWidgets.QLabel()
        self.save_button = QtWidgets.QPushButton("Save last image")
        self.sequence_button = QtWidgets.QPushButton("Acquire and save sequence")

        # layout
        acquisition = QtWidgets.QHBoxLayout()
        acquisition.addWidget(self.snap_button)
        acquisition.addWidget(self.live_button)
        acquisition.addWidget(self.auto_levels)
        acquisition.addWidget(self.stats_label, 1)

        settings = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(settings)
        form.addRow("Exposure", self.exposure)
        form.addRow("Binning", self.binning)
        readout, self.readout_widgets = self.make_readout_row()
        form.addRow("Readout", readout)
        roi = QtWidgets.QHBoxLayout()
        for label, box in zip(("x", "y", "w", "h"), self.roi_boxes):
            roi.addWidget(QtWidgets.QLabel(label))
            roi.addWidget(box)
        roi.addWidget(self.select_button)
        roi.addWidget(self.roi_button)
        roi.addWidget(self.full_frame_button)
        form.addRow("ROI", roi)
        advanced = QtWidgets.QHBoxLayout()
        advanced.addWidget(self.advanced_button)
        advanced.addStretch()
        form.addRow("All parameters", advanced)

        saving = QtWidgets.QWidget()
        save_form = QtWidgets.QFormLayout(saving)
        save_form.addRow("Folder", self.folder_label)
        save_form.addRow("Pixels", self.pixel_bits)
        frames_row = QtWidgets.QHBoxLayout()
        frames_row.addWidget(self.n_frames)
        frames_row.addWidget(self.size_label, 1)
        save_form.addRow("Frames", frames_row)
        save_buttons = QtWidgets.QHBoxLayout()
        save_buttons.addWidget(self.save_button)
        save_buttons.addWidget(self.sequence_button)
        save_form.addRow(save_buttons)

        tabs = QtWidgets.QTabWidget()  # settings and saving share the space under the image
        tabs.addTab(settings, "Settings")
        tabs.addTab(saving, "Saving")

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(self.top)
        layout.addWidget(self.view, 1)
        layout.addLayout(acquisition)
        layout.addWidget(tabs)

        # signals
        self.snap_button.clicked.connect(self.snap)
        self.live_button.clicked.connect(self.toggle_live)
        self.exposure.editingFinished.connect(self.apply_exposure)
        self.binning.currentTextChanged.connect(self.apply_binning)
        self.roi_button.clicked.connect(self.apply_roi)
        self.full_frame_button.clicked.connect(self.apply_full_frame)
        self.select_button.toggled.connect(self.toggle_roi_selection)
        self.advanced_button.clicked.connect(self.open_advanced_settings)
        self.roi_rect.sigRegionChanged.connect(self.roi_rect_changed)
        self.n_frames.valueChanged.connect(self.update_size_label)
        self.pixel_bits.currentIndexChanged.connect(self.update_size_label)
        self.save_button.clicked.connect(self.save_image)
        self.sequence_button.clicked.connect(self.save_sequence)

        self.device_widgets = [self.snap_button, self.live_button, self.exposure, self.binning,
                               self.select_button, self.roi_button, self.full_frame_button,
                               self.advanced_button, self.sequence_button, *self.readout_widgets]
        self.update_state()
        self.update_destination()

    # ---------- camera specific (overridden by PrimePanel / OrcaPanel) ----------

    def make_readout_row(self):
        """Return (layout, widgets) of the readout settings."""
        return QtWidgets.QHBoxLayout(), []

    def apply_readout(self):
        pass

    def temperature_text(self):
        return f"T: {self.device.temperature:.1f} °C"

    # ---------- connection ----------

    def make_device(self):
        return self.device_class(exposure_time=self.exposure.value())

    def device_name(self):
        return self.device.name

    def on_connected(self):
        width, height = self.device.sensor_size
        for box, maximum in zip(self.roi_boxes, (width, height, width, height)):
            box.setMaximum(maximum)
        self.roi_boxes[2].setValue(width)
        self.roi_boxes[3].setValue(height)
        self.temperature_timer.start()
        self.update_temperature()

    def update_state(self):
        super().update_state()
        self.save_button.setEnabled(self.image is not None)
        self.update_size_label()

    def update_temperature(self):
        try:
            text = self.temperature_text()
        except Exception:
            # some cameras cannot be read during the live: keep the last value
            text = None if self.device.live else "T: -"
        if text is None:
            return
        if self.has_temperature:
            self.sensor_temperature = text
            self.show_temperatures()
        else:
            self.temperature_label.setText(text)

    def record_image_temperature(self, metadata):
        """Keep the temperature saved with the image just acquired, and show it."""
        temperature = (metadata or {}).get("temperature_C")
        if self.has_temperature and temperature is not None:
            self.image_temperature = f"{temperature:.1f} °C"
            self.show_temperatures()

    def show_temperatures(self):
        lines = [f"sensor {self.sensor_temperature}"]
        if self.image_temperature:
            lines.append(f"last image {self.image_temperature}")
        self.temperature_overlay.setText("\n".join(lines))
        self.temperature_overlay.adjustSize()
        self.temperature_overlay.show()
        self.temperature_overlay.raise_()

    # ---------- display ----------

    def display(self, image):
        frame = image if image.ndim == 2 else image[-1]
        shown = self.view.getImageItem().image
        size_changed = shown is None or shown.shape[-2:] != frame.shape or shown.dtype != frame.dtype
        auto = self.auto_levels.isChecked() or self.first_display
        levels = auto_levels(frame) if auto else None
        self.view.setImage(image, autoLevels=False, levels=levels, autoRange=self.first_display,
                           autoHistogramRange=auto)
        self.first_display = False
        self.stats_label.setText(f"{frame.shape[1]}x{frame.shape[0]}  min {frame.min()}  "
                                 f"max {frame.max()}  mean {frame.mean():.1f}")
        if size_changed:  # ROI, binning or pixel format changed
            self.update_size_label()

    # ---------- acquisition ----------

    def run_paused(self, function, *args):
        """Stop the live, run function(*args), restart the live if it was running."""
        was_live = self.device.live
        if was_live:
            self.stop_live()
        try:
            return function(*args)
        finally:
            if was_live:
                self.start_live()

    def snap(self):
        try:
            self.image = self.run_paused(self.device.snap)
            self.image_metadata = self.device.last_metadata
            # the state at the time of the snap, not of "Save last image"
            self.image_extra = self.get_extra_metadata()
            self.image_slm_mask = self.get_slm_mask()
            self.record_image_temperature(self.image_metadata)
            self.display(self.image)
        except Exception as error:
            show_error(self, error)
        self.update_state()

    def start_live(self):
        self.device.start_live()
        self.live_timer.start()
        self.live_button.setText("Stop live")

    def stop_live(self):
        self.live_timer.stop()
        self.device.stop_live()
        self.live_button.setText("Start live")

    def toggle_live(self):
        try:
            if self.device.live:
                self.stop_live()
            else:
                self.start_live()
        except Exception as error:
            show_error(self, error)

    def update_live(self):
        try:
            frame = self.read_live_frame()
            if frame is not None:  # None: no new frame yet
                self.display(frame)
        except Exception as error:
            self.stop_live()
            show_error(self, error)

    def read_live_frame(self):
        """Latest live frame (waits for it), or None if not available yet."""
        return self.device.get_live_frame()

    # ---------- settings ----------

    def apply_setting(self, function, *args):
        """Apply a camera setting (pausing the live if needed) and report errors."""
        try:
            self.run_paused(function, *args)
            self.first_display = True  # image size may have changed
        except Exception as error:
            show_error(self, error)

    def apply_exposure(self):
        if self.device is not None:
            self.apply_setting(self.device.set_exposure, self.exposure.value())

    def apply_binning(self, text):
        self.select_button.setChecked(False)
        if self.device is not None:
            self.apply_setting(self.device.set_binning, int(text))

    def apply_roi(self):
        self.select_button.setChecked(False)  # the image coordinates are going to change
        self.apply_setting(self.device.set_roi, *(box.value() for box in self.roi_boxes))

    # ---------- ROI drawn on the image ----------

    def image_origin(self):
        """(x0, y0, binning): sensor pixel of the image pixel (0, 0), and the binning."""
        x0, y0 = self.device.roi[:2] if self.device.roi else (0, 0)
        return x0, y0, int(self.binning.currentText())

    def toggle_roi_selection(self, on):
        """Show the rectangle (on the central half of the image) or hide it."""
        if not on:
            self.roi_rect.hide()
            return
        image = self.view.getImageItem().image
        if image is None:
            self.select_button.setChecked(False)
            show_error(self, "Snap or start the live first, then draw the ROI on the image.")
            return
        height, width = image.shape[-2:]
        self.roi_rect.maxBounds = QtCore.QRectF(0, 0, width, height)  # stay on the image
        self.roi_rect.setPos([width // 4, height // 4], update=False, finish=False)
        self.roi_rect.setSize([width // 2, height // 2])
        self.roi_rect.show()

    def roi_rect_changed(self):
        """Write the rectangle, converted to full-sensor pixels, in the x, y, w, h boxes."""
        if not self.select_button.isChecked() or self.device is None:
            return
        x0, y0, binning = self.image_origin()
        x, y = self.roi_rect.pos()
        width, height = self.roi_rect.size()
        values = (x0 + x * binning, y0 + y * binning, width * binning, height * binning)
        for box, value in zip(self.roi_boxes, values):
            box.setValue(round(value))

    def apply_full_frame(self):
        self.select_button.setChecked(False)
        self.apply_setting(self.device.reset_roi)  # also sets binning 1x1
        self.binning.blockSignals(True)  # do not apply the binning a second time
        self.binning.setCurrentText("1")
        self.binning.blockSignals(False)
        width, height = self.device.sensor_size
        for box, value in zip(self.roi_boxes, (0, 0, width, height)):
            box.setValue(value)

    # ---------- saving ----------

    def update_destination(self):
        """Show the destination folder (or why there is none)."""
        try:
            folder = self.destination()
        except ValueError as error:
            self.folder_label.setText(f"<span style='color:#c62828'>{error}</span>")
            return
        via = ""
        if self.staging_folder(folder):
            via = f"<br><small>sequences written on {STAGING_ROOT} (fast disk) then moved</small>"
        try:
            index = self.next_index(folder)
            files = f"<br><small>next acquisition: {index}_0.tiff + {index}.json</small>"
        except OSError:
            files = ""
        self.folder_label.setText(f"{folder}{files}{via}")

    def next_index(self, folder):
        """Number X of the next acquisition in folder (files X_Y.tiff, X.json), also counting
        the files still on the fast disk."""
        return next_index(folder, self.staging_folder(folder))

    def staging_folder(self, folder):
        """Folder on the fast disk for a sequence going to folder, None if not needed."""
        if self.mover is None:
            return None
        if os.path.splitdrive(folder)[0].upper() == os.path.splitdrive(STAGING_ROOT)[0].upper():
            return None
        # same date_name[/subfolder] path as under ACQUISITION_ROOT, so files left there
        # can be moved to the right place at the next start (FileMover.move_leftovers)
        relative = os.path.relpath(folder, ACQUISITION_ROOT)
        if relative.startswith(".."):  # destination outside ACQUISITION_ROOT
            relative = os.path.basename(os.path.normpath(folder))
        return os.path.join(STAGING_ROOT, relative)

    def check_space(self, size, folders):
        """ValueError if a disk lacks size bytes (plus a margin and the files moving to it)."""
        check_free_space(size, folders, self.mover.incoming_bytes if self.mover else None)

    def save_image(self):
        try:
            frames = self.image.shape[0] if self.image.ndim == 3 else 1
            self.check_space(frames * self.frame_bytes(), [self.destination()])
            path = self.save(self.image, self.image_metadata)
            self.status.setText(f"Saved {os.path.basename(path)}")
            self.update_destination()
        except Exception as error:
            show_error(self, error)

    def save_sequence(self):
        """
        Acquire a sequence, written to its TIFF file frame by frame (nothing kept in memory),
        with a progress window: the GUI keeps responding and shows the frames as they arrive
        (otherwise Windows marks it "Not responding" after ~5 s, and closing it then leaves
        the camera locked), and Stop keeps the frames already saved.
        If the destination is on a hard disk, the files are written on the fast disk
        (STAGING_ROOT) and moved in the background: each TIFF file of a long sequence as soon
        as it is complete, the last one with the .json / _frames.csv at the end.
        """
        n_frames = self.n_frames.value()
        size = n_frames * self.frame_bytes()
        try:
            folder = self.destination()
            write_folder = self.staging_folder(folder) or folder
            # the fast disk during the acquisition, and the final destination
            self.check_space(size, [write_folder, folder])
        except (OSError, ValueError) as error:
            show_error(self, error)
            return
        progress = QtWidgets.QProgressDialog(f"Acquiring {n_frames} frames...", "Stop", 0,
                                             n_frames, self)
        progress.setWindowTitle("Sequence")
        progress.setWindowModality(QtCore.Qt.WindowModal)  # no other click meanwhile
        progress.setMinimumDuration(0)

        last_display = 0
        start = time.monotonic()

        def on_frame(done, total, frame):
            nonlocal last_display
            progress.setValue(done)
            now = time.monotonic()
            if now - last_display > SEQUENCE_DISPLAY_PERIOD / 1000:
                self.display(frame)  # acts as the live during the sequence
                last_display = now
                rate = done / max(now - start, 1e-9)
                progress.setLabelText(f"Acquisition {writer.index}: frame {done} / {total}, "
                                      f"{rate:.1f} frames/s, about "
                                      f"{format_duration((total - done) / rate)} left")
            QtWidgets.QApplication.processEvents()
            return not progress.wasCanceled()

        def file_done(path):  # each finished file goes to the destination (hard disk)
            # same subfolder there (SLM masks folder)
            relative = os.path.relpath(os.path.dirname(path), write_folder)
            self.mover.move(path, os.path.normpath(os.path.join(folder, relative)))

        message = None
        writer = None
        try:
            extra = self.get_extra_metadata()
            writer = SequenceWriter(write_folder, extra, size, self.pixel_bits.currentData(),
                                    index=self.next_index(folder),
                                    on_file_done=file_done if write_folder != folder else None,
                                    slm_mask=self.get_slm_mask)
            with writer:  # also closes (and moves) the files of the frames before an error
                frame = self.run_paused(self.device.acquire_sequence, n_frames, None,
                                        on_frame, writer)
            # "Save last image" then saves this last frame alone
            self.image = frame
            self.image_metadata = dict(self.device.last_metadata, n_frames=1)
            self.image_extra = extra
            self.image_slm_mask = self.get_slm_mask()
            self.record_image_temperature(self.image_metadata)
            self.display(frame)
            done = writer.n_frames
            stopped = f" (stopped after {done} of {n_frames})" if done < n_frames else ""
            moving = f", moving to {folder}" if write_folder != folder else ""
            message = (f"Saved acquisition {writer.index} ({len(writer.paths)} TIFF file(s))"
                       f"{stopped}{moving}")
        except Exception as error:
            if writer is not None and writer.n_frames:
                error = (f"{error}\n\nThe {writer.n_frames} frames taken before are saved as "
                         f"acquisition {writer.index} in {folder}")
            show_error(self, error)
        finally:
            progress.close()
        self.update_state()
        self.update_destination()
        if message:
            self.status.setText(message)

    def frame_bytes(self):
        """Approximate size of one saved frame (bytes), from the last image or the sensor."""
        image = self.view.getImageItem().image
        if image is not None:
            frame = image[-1] if image.ndim == 3 else image
            pixels, itemsize = frame.size, frame.itemsize
        else:
            width, height = self.device.sensor_size
            binning = int(self.binning.currentText())
            pixels, itemsize = width * height // binning**2, 2
        return pixels * (1 if self.pixel_bits.currentData() == 8 else itemsize)

    def update_size_label(self):
        """Size of the saved files: one frame and the whole sequence."""
        try:
            frame = self.frame_bytes()
        except Exception:  # not connected
            self.size_label.setText("")
            return
        self.size_label.setText(f"{frame / 1e6:.1f} MB / frame, sequence "
                                f"{frame * self.n_frames.value() / 1e9:.2f} GB")

    def save(self, images, metadata):
        folder = self.destination()
        extra = self.image_extra if self.image_extra is not None else self.get_extra_metadata()
        return self.device.save(images, folder, metadata=metadata, extra_metadata=extra,
                                pixel_bits=self.pixel_bits.currentData(),
                                index=self.next_index(folder), slm_mask=self.image_slm_mask)

    def open_advanced_settings(self):
        """Window with all the camera parameters (one per panel, brought to front if open)."""
        if self.advanced_dialog is None:
            self.advanced_dialog = AdvancedSettingsDialog(self, self.device_name())
        else:
            self.advanced_dialog.refresh()
        self.advanced_dialog.show()
        self.advanced_dialog.raise_()

    def close_device(self):
        self.temperature_timer.stop()
        self.temperature_overlay.hide()
        self.sensor_temperature, self.image_temperature = "-", None
        if self.advanced_dialog is not None:
            self.advanced_dialog.close()
            self.advanced_dialog = None
        if self.device is not None and self.device.live:
            self.stop_live()
        super().close_device()


class PrimePanel(CameraPanel):
    """Teledyne Photometrics Prime 95B (camera.py)."""

    device_class = Camera
    import_error = CAMERA_ERROR
    sensor_size = 1200
    has_temperature = True

    def temperature_text(self):
        # during the live camera.py returns the last value read (reading would stop the live)
        suffix = " (before live)" if self.device.live else ""
        return f"{self.device.temperature:.1f} °C{suffix}"

    def make_readout_row(self):
        self.readout_port = spin_box(0, 10, 0)
        self.speed = spin_box(0, 10, 0)
        self.gain = spin_box(0, 10, 1)
        self.readout_button = QtWidgets.QPushButton("Apply readout")
        self.readout_button.clicked.connect(self.apply_readout)
        row = QtWidgets.QHBoxLayout()
        for label, box in (("port", self.readout_port), ("speed", self.speed), ("gain", self.gain)):
            row.addWidget(QtWidgets.QLabel(label))
            row.addWidget(box)
        row.addWidget(self.readout_button)
        return row, [self.readout_button]

    def apply_readout(self):
        self.apply_setting(self.device.set_readout, self.readout_port.value(),
                           self.speed.value(), self.gain.value())


class OrcaPanel(CameraPanel):
    """Hamamatsu ORCA-Flash4.0 V2 (orca.py)."""

    device_class = Orca
    import_error = ORCA_ERROR
    sensor_size = 2048

    def make_readout_row(self):
        self.readout_speed = QtWidgets.QComboBox()
        self.readout_speed.addItems(["fast", "slow"])
        self.readout_speed.setToolTip("fast: standard scan, slow: low noise scan")
        self.readout_speed.activated.connect(self.apply_readout)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.readout_speed)
        row.addStretch()
        return row, [self.readout_speed]

    def apply_readout(self):
        self.apply_setting(self.device.set_readout_speed, self.readout_speed.currentText())

    def temperature_text(self):
        return f"Cooler: {self.device.cooler_status}"  # the Flash4.0 V2 gives no temperature


class FlirPanel(CameraPanel):
    """FLIR Blackfly S BFS-U3-51S5M (flir.py)."""

    device_class = Flir
    import_error = FLIR_ERROR
    sensor_size = 2448
    binnings = ("1", "2")

    def make_readout_row(self):
        self.gain = spin_box(0, 47.9, 0, " dB", decimals=1)
        self.gain.setToolTip("Fixed gain (automatic gain is switched off at connection)")
        self.gain.editingFinished.connect(self.apply_readout)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("gain"))
        row.addWidget(self.gain)
        row.addStretch()
        return row, [self.gain]

    def apply_readout(self):
        if self.device is not None:
            self.apply_setting(self.device.set_gain, self.gain.value())

    def temperature_text(self):
        return ""  # not available for this camera

    def read_live_frame(self):
        return self.device.get_live_frame(wait=False)  # do not block the window


# ---------------------------------------------------------------- microscope


class NikonPanel(DevicePanel):
    """Light path, focus, XY stage and Perfect Focus of the Nikon Ti. Objective only displayed."""

    device_class = NikonTi
    import_error = NIKON_ERROR

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(POSITION_PERIOD)
        self.timer.timeout.connect(self.refresh)

        # optics
        self.objective_label = QtWidgets.QLabel("-")
        self.objective_label.setToolTip("The objective is never changed from this program.")
        self.light_path = QtWidgets.QComboBox()
        self.cube = QtWidgets.QComboBox()  # turret not motorised: set by hand, remembered
        self.cube.addItems(["unknown", *FILTER_CUBES])
        self.cube.setToolTip("Cube in the turret, chosen by hand (the turret is not motorised). "
                             "Saved with the images.")
        self.settings = QtCore.QSettings("interface_setup", "setup_gui")
        self.cube.setCurrentText(self.settings.value("filter_cube", DEFAULT_FILTER_CUBE))
        self.cube.currentTextChanged.connect(lambda text: self.settings.setValue("filter_cube", text))

        # focus
        self.z_label = QtWidgets.QLabel("-")
        self.z_step = spin_box(0.025, MAX_Z_JUMP, 1, " µm", decimals=3)
        self.z_up = QtWidgets.QPushButton("Z +")
        self.z_down = QtWidgets.QPushButton("Z −")
        self.z_target = spin_box(0, 20000, 0, " µm", decimals=3)
        self.z_go = QtWidgets.QPushButton("Go to")

        # stage
        self.xy_label = QtWidgets.QLabel("-")
        self.xy_step = spin_box(0.1, 5000, 10, " µm", decimals=1)
        self.xy_buttons = {text: QtWidgets.QPushButton(text) for text in ("Y +", "X −", "X +", "Y −")}

        # perfect focus
        self.pfs_on = QtWidgets.QCheckBox("On")
        self.pfs_on.setToolTip("Perfect Focus: keeps the focus locked on the coverslip "
                               "(needs a sample under the objective, near focus)")
        self.pfs_status = QtWidgets.QLabel("-")
        self.pfs_offset_label = QtWidgets.QLabel("-")
        self.pfs_offset = spin_box(-10000, 10000, 0, decimals=3)
        self.pfs_offset_button = QtWidgets.QPushButton("Set offset")
        self.pfs_step = spin_box(0.025, 100, 1, decimals=3)
        self.pfs_down = QtWidgets.QPushButton("−")
        self.pfs_up = QtWidgets.QPushButton("+")
        for widget in (self.pfs_down, self.pfs_up):
            widget.setToolTip("Change the PFS offset by Step: moves the focal plane while "
                              "the Perfect Focus holds the focus")

        # layout
        optics = QtWidgets.QGroupBox("Optics")
        optics_form = QtWidgets.QFormLayout(optics)
        optics_form.addRow("Objective", self.objective_label)
        optics_form.addRow("Light path", self.light_path)
        optics_form.addRow("Cube (set by hand)", self.cube)

        self.focus_box = QtWidgets.QGroupBox("Focus")
        focus_form = QtWidgets.QFormLayout(self.focus_box)
        focus_form.addRow("Position", self.z_label)
        step_row = QtWidgets.QHBoxLayout()
        for widget in (self.z_step, self.z_down, self.z_up):
            step_row.addWidget(widget)
        focus_form.addRow("Step", step_row)
        go_row = QtWidgets.QHBoxLayout()
        go_row.addWidget(self.z_target)
        go_row.addWidget(self.z_go)
        focus_form.addRow("Target", go_row)

        self.stage_box = QtWidgets.QGroupBox("Stage")
        stage_form = QtWidgets.QFormLayout(self.stage_box)
        stage_form.addRow("Position", self.xy_label)
        stage_form.addRow("Step", self.xy_step)
        arrows = QtWidgets.QGridLayout()
        for text, (row, column) in zip(self.xy_buttons, ((0, 1), (1, 0), (1, 2), (2, 1))):
            arrows.addWidget(self.xy_buttons[text], row, column)
        stage_form.addRow(arrows)

        self.pfs_box = QtWidgets.QGroupBox("Perfect Focus")
        pfs_form = QtWidgets.QFormLayout(self.pfs_box)
        pfs_form.addRow(self.pfs_on, self.pfs_status)
        pfs_form.addRow("Offset", self.pfs_offset_label)
        pfs_step_row = QtWidgets.QHBoxLayout()
        for widget in (self.pfs_step, self.pfs_down, self.pfs_up):
            pfs_step_row.addWidget(widget)
        pfs_form.addRow("Step", pfs_step_row)
        offset_row = QtWidgets.QHBoxLayout()
        offset_row.addWidget(self.pfs_offset)
        offset_row.addWidget(self.pfs_offset_button)
        pfs_form.addRow("Target", offset_row)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(self.top)
        for box in (optics, self.pfs_box, self.focus_box, self.stage_box):
            layout.addWidget(box)
        layout.addStretch()

        # signals (activated / clicked: only user actions, not the refreshes)
        self.light_path.activated.connect(
            lambda index: self.run(self.device.set_light_path, self.light_path.itemText(index)))
        self.z_up.clicked.connect(lambda: self.run(self.device.move_z, self.z_step.value()))
        self.z_down.clicked.connect(lambda: self.run(self.device.move_z, -self.z_step.value()))
        self.z_go.clicked.connect(self.go_to_z)
        for text, (dx, dy) in zip(self.xy_buttons, ((0, 1), (-1, 0), (1, 0), (0, -1))):
            self.xy_buttons[text].clicked.connect(
                lambda _, dx=dx, dy=dy: self.run(self.device.move_xy, dx * self.xy_step.value(),
                                                 dy * self.xy_step.value()))
        self.pfs_on.clicked.connect(lambda checked: self.run(self.device.set_pfs, checked))
        self.pfs_offset_button.clicked.connect(
            lambda: self.run(self.device.set_pfs_offset, self.pfs_offset.value()))
        self.pfs_up.clicked.connect(lambda: self.move_pfs_offset(self.pfs_step.value()))
        self.pfs_down.clicked.connect(lambda: self.move_pfs_offset(-self.pfs_step.value()))

        self.device_widgets = [self.light_path, self.z_step, self.z_up, self.z_down, self.z_target,
                               self.z_go, self.xy_step, *self.xy_buttons.values(), self.pfs_on,
                               self.pfs_offset, self.pfs_offset_button, self.pfs_step,
                               self.pfs_up, self.pfs_down]
        self.update_state()

    def device_name(self):
        name = ", ".join(self.device.devices)
        missing = self.device.missing_devices()
        if missing:  # shown in red in the status line
            name += (f" — <span style='color:#c62828'><b>not found: {', '.join(missing)}</b> "
                     f"(switch the microscope off / on, then reconnect)</span>")
        return name

    def on_connected(self):
        ti = self.device
        missing = ti.missing_devices()
        if missing:
            show_error(self, f"The microscope did not report: {', '.join(missing)}.\n"
                             f"Their controls are disabled. Usually the microscope or its "
                             f"controller was not ready: switch it off and on, wait for it to "
                             f"finish its start, then Disconnect / Connect.")
        self.light_path.clear()
        has_light_path = ti.has("TILightPath")
        self.light_path.setVisible(has_light_path)
        if has_light_path:
            self.light_path.addItems(ti.light_paths)
            for index, path in enumerate(ti.light_paths):  # colour the items of the list too
                color = light_path_color(path)
                if color:
                    self.light_path.setItemData(index, QtGui.QColor(color), QtCore.Qt.BackgroundRole)
                    self.light_path.setItemData(index, QtGui.QColor("white"), QtCore.Qt.ForegroundRole)
        self.focus_box.setVisible(ti.has("TIZDrive"))
        self.stage_box.setVisible(ti.has("TIXYDrive"))
        self.pfs_box.setVisible(ti.has("TIPFSStatus"))
        if ti.has("TIZDrive"):
            self.z_target.setValue(ti.get_z())
        if ti.has("TIPFSOffset"):
            self.pfs_offset.setValue(ti.get_pfs_offset())
        self.refresh()
        self.timer.start()

    def refresh(self):
        """Read the state of the microscope and display it."""
        ti = self.device
        try:
            if ti.has("TINosePiece"):
                self.objective_label.setText(ti.get_objective())
            if ti.has("TILightPath"):
                path = ti.get_light_path()
                self.light_path.setCurrentText(path)
                color = light_path_color(path)
                self.light_path.setStyleSheet(
                    f"QComboBox {{ background-color: {color}; color: white; font-weight: bold; }}"
                    if color else "")
            if ti.has("TIZDrive"):
                self.z_label.setText(f"{ti.get_z():.3f} µm")
            if ti.has("TIXYDrive"):
                x, y = ti.get_xy()
                self.xy_label.setText(f"x {x:.1f} µm   y {y:.1f} µm")
            if ti.has("TIPFSStatus"):
                self.pfs_on.setChecked(ti.is_pfs_on())
                status = ti.get_pfs_status()
                self.pfs_status.setText(status)
                self.pfs_status.setStyleSheet(f"color: {pfs_color(status)}; font-weight: bold;")
            if ti.has("TIPFSOffset"):
                self.pfs_offset_label.setText(f"{ti.get_pfs_offset():.3f}")
        except Exception as error:
            self.status.setText(f"Read error: {error}")

    def move_pfs_offset(self, step):
        """Change the PFS offset by step (moves the focal plane while the PFS is locked)."""
        self.run(self.device.set_pfs_offset, self.device.get_pfs_offset() + step)
        self.pfs_offset.setValue(self.device.get_pfs_offset())

    def run(self, function, *args):
        """Run a microscope command, report errors, refresh the display."""
        try:
            function(*args)
        except Exception as error:
            show_error(self, error)
        self.refresh()

    def go_to_z(self):
        target = self.z_target.value()
        if abs(target - self.device.get_z()) > MAX_Z_JUMP and not confirm(
                self, f"Move the focus by more than {MAX_Z_JUMP} µm, to {target:.3f} µm?"):
            return
        self.run(self.device.set_z, target)

    def close_device(self):
        self.timer.stop()
        super().close_device()

    def get_metadata(self):
        metadata = {"filter_cube": self.cube.currentText()}  # even if not connected
        if self.device is not None:
            metadata.update(self.device.get_metadata())
        return metadata


# ---------------------------------------------------------------- objective piezo


class PiezoPanel(DevicePanel):
    """Objective piezo (PPC001): open loop in volts, closed loop in µm."""

    device_class = Piezo
    import_error = PIEZO_ERROR

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(POSITION_PERIOD)
        self.timer.timeout.connect(self.refresh)

        self.state_label = QtWidgets.QLabel("-")
        self.closed_loop = QtWidgets.QCheckBox("Closed loop (µm)")
        self.closed_loop.setToolTip("Position from the capacitive sensor, 0 to 450 µm "
                                    "(about -16 µm at 0 V). Switching keeps the position.")
        self.target = spin_box(0, 150, 0, " V", decimals=3)
        self.go_button = QtWidgets.QPushButton("Go to")
        self.step = spin_box(0.001, MAX_PIEZO_JUMP, 1, decimals=3)
        self.plus = QtWidgets.QPushButton("+")
        self.minus = QtWidgets.QPushButton("−")

        form = QtWidgets.QFormLayout()
        form.addRow("State", self.state_label)
        form.addRow("Mode", self.closed_loop)
        go_row = QtWidgets.QHBoxLayout()
        go_row.addWidget(self.target)
        go_row.addWidget(self.go_button)
        form.addRow("Target", go_row)
        step_row = QtWidgets.QHBoxLayout()
        for widget in (self.step, self.minus, self.plus):
            step_row.addWidget(widget)
        form.addRow("Step", step_row)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(self.top)
        layout.addLayout(form)
        layout.addStretch()

        self.closed_loop.clicked.connect(self.set_closed_loop)
        self.go_button.clicked.connect(lambda: self.move_to(self.target.value()))
        self.plus.clicked.connect(lambda: self.move_to(self.current() + self.step.value()))
        self.minus.clicked.connect(lambda: self.move_to(self.current() - self.step.value()))

        self.device_widgets = [self.closed_loop, self.target, self.go_button,
                               self.step, self.plus, self.minus]
        self.update_state()

    def device_name(self):
        return self.device.serial_number

    def on_connected(self):
        self.update_units()
        self.target.setValue(self.current())
        self.refresh()
        self.timer.start()

    def update_units(self):
        """Target and step in µm in closed loop, in V in open loop."""
        closed = self.device.is_closed_loop()
        self.closed_loop.setChecked(closed)
        unit = " µm" if closed else " V"
        self.target.setRange(0, self.device.max_travel if closed else self.device.max_voltage)
        for box in (self.target, self.step):
            box.setSuffix(unit)

    def current(self):
        """Current position (µm, closed loop) or voltage (V, open loop)."""
        piezo = self.device
        return piezo.get_position() if piezo.is_closed_loop() else piezo.get_voltage()

    def refresh(self):
        piezo = self.device
        try:
            mode = "closed loop" if piezo.is_closed_loop() else "open loop"
            self.state_label.setText(f"{mode}   {piezo.get_voltage():.2f} V   "
                                     f"{piezo.get_position():.3f} µm")
        except Exception as error:
            self.status.setText(f"Read error: {error}")

    def move_to(self, value):
        piezo = self.device
        try:
            if piezo.is_closed_loop():
                jump = abs(value - piezo.get_position())
                move = piezo.set_position
            else:  # approximate distance from the full scale ratio
                jump = abs(value - piezo.get_voltage()) * piezo.max_travel / piezo.max_voltage
                move = piezo.set_voltage
            if jump > MAX_PIEZO_JUMP and not confirm(
                    self, f"Move the objective piezo by about {jump:.0f} µm?"):
                return
            move(value)
        except Exception as error:
            show_error(self, error)
        self.refresh()

    def set_closed_loop(self, closed):
        try:
            self.device.set_closed_loop(closed)
        except Exception as error:
            show_error(self, error)
        self.update_units()
        self.target.setValue(self.current())
        self.refresh()

    def close_device(self):
        self.timer.stop()
        super().close_device()

    def get_metadata(self):
        if self.device is None:
            return {}
        return {key.removeprefix("piezo_"): value  # already in the "objective_piezo" section
                for key, value in self.device.get_metadata().items()}


# ---------------------------------------------------------------- filter wheels


class FilterWheelPanel(DevicePanel):
    """Filter wheels and shutters of the Lambda 10-3; rows built from its configuration."""

    device_class = FilterWheel
    import_error = FILTER_WHEEL_ERROR

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = QtWidgets.QWidget()  # filled at connection
        self.wheel_boxes = {}            # wheel -> (filter combo box, speed box)
        self.shutter_widgets = {}        # shutter -> (state label, button)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(self.top)
        layout.addWidget(self.rows)
        layout.addStretch()
        self.update_state()

    def device_name(self):
        return f"{self.device.port}, Lambda {self.device.configuration[:4]}"

    def on_connected(self):
        """One row per connected wheel and shutter."""
        lambda10 = self.device
        old_rows, self.rows = self.rows, QtWidgets.QWidget()
        self.layout().replaceWidget(old_rows, self.rows)
        old_rows.deleteLater()
        form = QtWidgets.QFormLayout(self.rows)
        self.wheel_boxes, self.shutter_widgets = {}, {}
        for wheel in lambda10.wheels:
            filters = QtWidgets.QComboBox()
            filters.addItems([lambda10.filter_name(wheel, position) for position in range(10)])
            speed = spin_box(0, 7, lambda10.speed[wheel])
            speed.setToolTip("Wheel speed: 0 fastest, 7 slowest")
            filters.activated.connect(lambda position, wheel=wheel: self.run(
                self.device.move, wheel, position, self.wheel_boxes[wheel][1].value()))
            row = QtWidgets.QHBoxLayout()
            row.addWidget(filters, 1)
            row.addWidget(QtWidgets.QLabel("speed"))
            row.addWidget(speed)
            form.addRow(f"Wheel {lambda10.wheel_label(wheel)}", row)
            self.wheel_boxes[wheel] = (filters, speed)
        for shutter in lambda10.shutters:
            state = QtWidgets.QLabel("-")
            button = QtWidgets.QPushButton("Open")
            button.clicked.connect(lambda _, shutter=shutter: self.toggle_shutter(shutter))
            row = QtWidgets.QHBoxLayout()
            row.addWidget(state, 1)
            row.addWidget(button)
            form.addRow(f"Shutter {shutter}", row)
            self.shutter_widgets[shutter] = (state, button)
        self.refresh()

    def refresh(self):
        """Read the wheel positions and shutter states and display them."""
        try:
            status = self.device.get_status()
        except Exception as error:
            self.status.setText(f"Read error: {error}")
            return
        for wheel, (filters, speed) in self.wheel_boxes.items():
            filters.setCurrentIndex(status["wheels"][wheel])
        for shutter, (state, button) in self.shutter_widgets.items():
            is_open = status["shutters"][shutter] == "open"
            state.setText(status["shutters"][shutter])
            button.setText("Close" if is_open else "Open")

    def run(self, function, *args):
        try:
            function(*args)
        except Exception as error:
            show_error(self, error)
        self.refresh()

    def toggle_shutter(self, shutter):
        is_open = self.shutter_widgets[shutter][0].text() == "open"
        self.run(self.device.close_shutter if is_open else self.device.open_shutter, shutter)

    def update_state(self):
        super().update_state()
        self.rows.setEnabled(self.device is not None)

    def get_metadata(self):
        return {} if self.device is None else self.device.get_metadata()


# ---------------------------------------------------------------- calibration


class CalibrationPanel(QtWidgets.QWidget):
    """One button per calibration (calibration.py), each opening its step-by-step window."""

    def __init__(self, device_panels, tools=None, parent=None):
        """
        device_panels: {key: (title, panel)} of the devices a calibration may need.
        tools: {name: function} of the main window a calibration may use (extra_metadata(),
        link_calibration(name, path)).
        """
        super().__init__(parent)
        self.device_panels = device_panels
        self.tools = tools or {}
        self.wizards = {}  # calibration name -> its window (one at a time)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QtWidgets.QLabel(" Calibration: "))
        for calibration in CALIBRATIONS:
            button = QtWidgets.QPushButton(calibration.title)
            side = f" ({calibration.side} path)" if calibration.side else ""
            button.setToolTip(f"{calibration.name}{side}: {calibration.description}")
            if calibration.side in LIGHT_PATH_COLORS:  # red: left path, green: right path
                button.setStyleSheet(f"QPushButton {{ background-color: "
                                     f"{LIGHT_PATH_COLORS[calibration.side]}; color: white; "
                                     f"font-weight: bold; padding: 2px 8px; }}")
            button.clicked.connect(lambda _, c=calibration: self.launch(c))
            layout.addWidget(button)

    def launch(self, calibration):
        wizard = self.wizards.get(calibration.name)
        if wizard is None or not wizard.isVisible():  # a closed one starts again from step 1
            wizard = CalibrationWizard(calibration, self.device_panels, self.tools, self)
            self.wizards[calibration.name] = wizard
        wizard.show()
        wizard.raise_()
        wizard.activateWindow()


# ---------------------------------------------------------------- main window


class MainWindow(QtWidgets.QMainWindow):
    """All the panels as dock windows, with a toolbar to show / hide them."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Setup control")
        self.setDockOptions(QtWidgets.QMainWindow.AllowNestedDocks
                            | QtWidgets.QMainWindow.AllowTabbedDocks
                            | QtWidgets.QMainWindow.AnimatedDocks)
        self.settings = QtCore.QSettings("interface_setup", "setup_gui")

        # acquisitions: ACQUISITION_ROOT/YYYY_MM_DD_<experiment name>[/<subfolder>]
        self.experiment = QtWidgets.QLineEdit(self.settings.value("experiment", ""))
        self.experiment.setPlaceholderText("experiment name")
        self.experiment.setFixedWidth(250)
        self.experiment.setToolTip(f"Files saved in {ACQUISITION_ROOT}\\YYYY_MM_DD_<name>")
        self.subfolder = QtWidgets.QLineEdit(self.settings.value("subfolder", ""))
        self.subfolder.setPlaceholderText("none")
        self.subfolder.setFixedWidth(200)
        self.subfolder.setToolTip("Optional: files saved in YYYY_MM_DD_<name>\\<subfolder>\n"
                                  "(empty: directly in YYYY_MM_DD_<name>)")
        self.mover = FileMover()  # sequences: fast disk -> destination, in the background
        try:
            leftovers = self.mover.move_leftovers()  # files of a session closed during a move
            if leftovers:
                print(f"Moving {leftovers} file(s) left in {STAGING_ROOT} to {ACQUISITION_ROOT}")
        except OSError as error:
            print(f"Could not move the files left in {STAGING_ROOT}: {error}")
        self.moving_label = QtWidgets.QLabel()
        self.moving_timer = QtCore.QTimer(self)
        self.moving_timer.setInterval(1000)
        self.moving_timer.timeout.connect(self.update_moving)
        self.moving_timer.start()

        self.slm_panel = SLMPanel()
        camera_options = dict(get_extra_metadata=self.extra_metadata,
                              get_slm_mask=self.shown_slm_mask,
                              destination=self.destination, mover=self.mover)
        self.prime_panel = PrimePanel(**camera_options)
        self.orca_panel = OrcaPanel(**camera_options)
        self.flir_panel = FlirPanel(**camera_options)
        self.nikon_panel = NikonPanel()
        self.piezo_panel = PiezoPanel()
        self.filter_panel = FilterWheelPanel()
        self.calibration_panel = CalibrationPanel({
            "microscope": ("Microscope", self.nikon_panel), "orca": ("Orca Flash4", self.orca_panel),
            "slm": ("SLM", self.slm_panel), "prime": ("Prime 95B", self.prime_panel),
            "flir": ("FLIR Blackfly", self.flir_panel), "piezo": ("Objective piezo", self.piezo_panel),
            "filter_wheels": ("Filter wheels", self.filter_panel)},
            tools={"extra_metadata": self.extra_metadata,
                   "link_calibration": self.slm_panel.link_calibration})

        toolbar = self.addToolBar("Panels")
        toolbar.setObjectName("panels_toolbar")
        view_menu = self.menuBar().addMenu("View")
        self.docks = []
        self.panels = []
        for panel, title, scroll in ((self.slm_panel, "SLM", False),
                                     (self.prime_panel, "Prime 95B", False),
                                     (self.orca_panel, "Orca Flash4", False),
                                     (self.flir_panel, "FLIR Blackfly", False),
                                     (self.nikon_panel, "Microscope", True),
                                     (self.piezo_panel, "Objective piezo", True),
                                     (self.filter_panel, "Filter wheels", True)):
            dock = self.make_dock(panel, title, scroll)
            toolbar.addAction(dock.toggleViewAction())
            view_menu.addAction(dock.toggleViewAction())
            self.docks.append(dock)
        view_menu.addSeparator()
        view_menu.addAction("Dock all panels", self.dock_all)
        view_menu.addAction("Reset layout", self.reset_layout)

        saving_toolbar = self.addToolBar("Experiment")
        saving_toolbar.setObjectName("experiment_toolbar")
        saving_toolbar.addWidget(QtWidgets.QLabel(" Experiment: "))
        saving_toolbar.addWidget(self.experiment)
        saving_toolbar.addWidget(QtWidgets.QLabel("  Subfolder: "))
        saving_toolbar.addWidget(self.subfolder)
        saving_toolbar.addWidget(self.moving_label)
        self.experiment.textChanged.connect(self.experiment_changed)
        self.subfolder.textChanged.connect(self.experiment_changed)

        # calibration buttons in the upper right corner (end of the toolbar row, whatever the
        # layout of the panels)
        calibration_toolbar = self.addToolBar("Calibration")
        calibration_toolbar.setObjectName("calibration_toolbar")
        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        calibration_toolbar.addWidget(spacer)
        calibration_toolbar.addWidget(self.calibration_panel)

        self.default_layout()
        self.default_state = self.saveState()
        self.resize(1700, 1000)
        if self.settings.value("geometry") is not None:
            self.restoreGeometry(self.settings.value("geometry"))
            self.restoreState(self.settings.value("state"))

    def make_dock(self, panel, title, scroll):
        """Dock window holding panel; scroll: in a scroll area, so the dock can be made small."""
        dock = QtWidgets.QDockWidget(title, self)
        dock.setObjectName(title)  # needed to save / restore the layout
        if scroll:
            area = QtWidgets.QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QtWidgets.QFrame.NoFrame)
            area.setWidget(panel)
            dock.setWidget(area)
        else:
            dock.setWidget(panel)
        self.panels.append(panel)
        dock.topLevelChanged.connect(lambda floating: self.make_window(dock, floating))
        return dock

    def make_window(self, dock, floating):
        """A panel taken out of the main window becomes a normal window (minimize / maximize)."""
        if floating:
            dock.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.CustomizeWindowHint
                                | QtCore.Qt.WindowTitleHint | QtCore.Qt.WindowMinMaxButtonsHint
                                | QtCore.Qt.WindowCloseButtonHint)
            dock.show()

    def default_layout(self):
        """SLM, microscope, piezo above the filter wheels on the left (the space beside the
        microscope is kept for the laser panel, to come), the cameras tabbed on the right."""
        slm, prime, orca, flir, nikon, piezo, filters = self.docks
        for dock in self.docks:
            dock.setFloating(False)
            dock.show()
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, slm)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, prime)
        self.tabifyDockWidget(prime, orca)
        self.tabifyDockWidget(orca, flir)
        self.splitDockWidget(slm, nikon, QtCore.Qt.Vertical)
        self.splitDockWidget(nikon, piezo, QtCore.Qt.Horizontal)
        self.splitDockWidget(piezo, filters, QtCore.Qt.Vertical)
        prime.raise_()
        self.resizeDocks([slm, prime], [700, 1000], QtCore.Qt.Horizontal)

    def dock_all(self):
        for dock in self.docks:
            dock.setFloating(False)
            dock.show()

    def reset_layout(self):
        self.dock_all()
        self.restoreState(self.default_state)

    def destination(self):
        """Folder of the acquisitions; ValueError if the experiment name is not set."""
        return experiment_folder(self.experiment.text(), self.subfolder.text())

    def experiment_changed(self):
        self.settings.setValue("experiment", self.experiment.text())
        self.settings.setValue("subfolder", self.subfolder.text())
        for panel in (self.prime_panel, self.orca_panel, self.flir_panel):
            panel.update_destination()

    def update_moving(self):
        """Files still being moved to the hard disk, errors of the moves; date change."""
        pending = self.mover.pending
        self.moving_label.setText(f"  moving {pending} file(s) to {ACQUISITION_ROOT[:2]}..."
                                  if pending else "")
        errors = self.mover.take_errors()
        if errors:
            show_error(self, "Could not move to the destination (the files are still on the "
                             "fast disk, moved at the next start):\n" + "\n".join(errors))
        for panel in (self.prime_panel, self.orca_panel, self.flir_panel):
            panel.update_destination()  # the date in the folder name changes at midnight

    def extra_metadata(self):
        """
        Saved with each camera image (see saving.organize_metadata):
          devices: connected or not, for every device, then the state of the microscope,
                   objective piezo and filter wheels ({"connected": False} if not connected)
          slm: connected or not, link, mask shown and the calibrations that corrected it
               (the masks themselves: shown_slm_mask; the calibration files are copied in
               X_calibrations by SequenceWriter)
        """
        def state(panel):
            metadata = {"connected": panel.device is not None}
            try:
                metadata.update(panel.get_metadata())
            except Exception as error:
                metadata["error"] = f"not readable: {error}"
            return metadata

        titles = {self.slm_panel: "slm", self.prime_panel: "prime_95b",
                  self.orca_panel: "orca_flash4", self.flir_panel: "flir_blackfly",
                  self.nikon_panel: "microscope", self.piezo_panel: "objective_piezo",
                  self.filter_panel: "filter_wheels"}
        connected = {title: panel.device is not None for panel, title in titles.items()}
        devices = {"connected": connected}
        for panel in (self.nikon_panel, self.piezo_panel, self.filter_panel):
            devices[titles[panel]] = state(panel)
        return {"devices": devices, "slm": state(self.slm_panel)}

    def shown_slm_mask(self):
        """(pattern, name) of the mask on the SLM now, None if none: saved with each frame."""
        return self.slm_panel.shown_mask()

    def closeEvent(self, event):
        if self.mover.pending:
            wait = QtWidgets.QProgressDialog(
                f"Moving the last files to {ACQUISITION_ROOT}...", "Close now", 0, 0, self)
            wait.setWindowTitle("Closing")
            wait.setWindowModality(QtCore.Qt.WindowModal)
            wait.setMinimumDuration(0)
            while self.mover.pending and not wait.wasCanceled():
                QtWidgets.QApplication.processEvents()
                time.sleep(0.05)
            wait.close()  # "Close now": the files left are moved at the next start
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("state", self.saveState())
        for panel in self.panels:
            try:
                panel.close_device()
            except Exception as error:
                print(f"Error while closing {type(panel).__name__}: {error}")
        event.accept()


def main():
    if sys.platform == "win32":
        # own taskbar entry and icon, instead of being grouped under python.exe
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("interface_setup.setup_gui")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    if os.path.isfile(ICON_PATH):
        app.setWindowIcon(QtGui.QIcon(ICON_PATH))
    window = MainWindow()
    window.show()
    app.exec_()


if __name__ == "__main__":
    main()
