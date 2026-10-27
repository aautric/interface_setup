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

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtCore, QtGui, QtWidgets

from SLM import SLM, SLM_HEIGHT, SLM_WIDTH, load_phase_pattern


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
Piezo, PIEZO_ERROR = optional_import("piezo", "Piezo")
FilterWheel, FILTER_WHEEL_ERROR = optional_import("filter_wheel", "FilterWheel")

pg.setConfigOptions(imageAxisOrder="row-major")  # numpy arrays are (y, x)

LIVE_PERIOD = 30           # ms between two live frames
TEMPERATURE_PERIOD = 2000  # ms between two temperature readings
POSITION_PERIOD = 500      # ms between two readings of the microscope / piezo state
MAX_Z_JUMP = 100           # µm, larger focus moves ask for a confirmation
MAX_PIEZO_JUMP = 20        # µm, larger piezo moves ask for a confirmation
MIN_VIEW_SIZE = 300        # px, minimum size of the image displays
# window and taskbar icon (kept outside the repository); default Qt icon if missing
ICON_PATH = os.path.join(os.path.expanduser("~"), "Pictures", "bumblebee.ico")


def make_image_view():
    """Image display with contrast histogram, without the ROI/Norm buttons."""
    view = pg.ImageView()
    view.ui.roiBtn.hide()
    view.ui.menuBtn.hide()
    view.setMinimumSize(MIN_VIEW_SIZE, MIN_VIEW_SIZE)
    return view


def show_error(parent, error):
    QtWidgets.QMessageBox.critical(parent, "Error", str(error))


def confirm(parent, text):
    answer = QtWidgets.QMessageBox.question(parent, "Confirm", text)
    return answer == QtWidgets.QMessageBox.Yes


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
                self.on_connected()
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
    """Load, preview and send phase masks to the SLM."""

    device_class = SLM

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pattern = np.zeros((SLM_HEIGHT, SLM_WIDTH), dtype=np.uint8)
        self.pattern_name = "blank"
        self.shown_name = None  # name of the mask currently displayed on the SLM

        self.view = make_image_view()
        self.load_button = QtWidgets.QPushButton("Load image / .npy...")
        self.send_button = QtWidgets.QPushButton("Send to SLM")
        self.blank_button = QtWidgets.QPushButton("Blank (0)")
        self.auto_send = QtWidgets.QCheckBox("Send automatically after loading")
        self.mask_label = QtWidgets.QLabel()

        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.load_button)
        buttons.addWidget(self.send_button)
        buttons.addWidget(self.blank_button)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(self.top)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.mask_label)
        layout.addLayout(buttons)
        layout.addWidget(self.auto_send)

        self.load_button.clicked.connect(self.load_file)
        self.send_button.clicked.connect(self.send)
        self.blank_button.clicked.connect(self.blank)

        self.device_widgets = [self.send_button]
        self.update_state()
        self.display_pattern()

    def device_name(self):
        return self.device.name

    def display_pattern(self):
        self.view.setImage(self.pattern, levels=(0, 255))
        self.mask_label.setText(f"Mask: {self.pattern_name}  "
                                f"(min {self.pattern.min()}, max {self.pattern.max()})")

    def set_pattern(self, source, name):
        """Load a mask (array or file path), display it and send it if asked."""
        try:
            self.pattern = load_phase_pattern(source)
        except Exception as error:
            show_error(self, error)
            return
        self.pattern_name = name
        self.display_pattern()
        if self.auto_send.isChecked() and self.device is not None:
            self.send()

    def load_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load phase mask", "",
            "Masks (*.png *.bmp *.tif *.tiff *.npy);;All files (*)")
        if path:
            self.set_pattern(path, os.path.basename(path))

    def blank(self):
        self.set_pattern(np.zeros((SLM_HEIGHT, SLM_WIDTH), dtype=np.uint8), "blank")

    def send(self):
        try:
            self.device.show(self.pattern)
            self.shown_name = self.pattern_name
            self.status.setText(f"Connected ({self.device_name()}) - showing {self.pattern_name}")
        except Exception as error:
            show_error(self, error)

    def close_device(self):
        super().close_device()
        self.shown_name = None

    def get_metadata(self):
        return {"slm_mask": self.shown_name}  # None if nothing sent to the SLM


# ---------------------------------------------------------------- cameras


class CameraPanel(DevicePanel):
    """
    Live display, settings, snap and saving of a camera. Common part of the Prime and
    Orca panels, which add their readout settings (make_readout_row / apply_readout).
    """

    sensor_size = 1200    # pixels, updated from the camera at connection
    binnings = ("1", "2", "4")
    file_name = "image"   # default base name of the saved files

    def __init__(self, get_extra_metadata=None, parent=None):
        super().__init__(parent)
        self.image = None           # last acquired image (2D) or sequence (3D)
        self.image_metadata = None  # metadata of self.image
        self.get_extra_metadata = get_extra_metadata or dict
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
        self.select_button = QtWidgets.QPushButton("Select on image")
        self.select_button.setCheckable(True)
        self.select_button.setToolTip("Draw the ROI as a rectangle on the image, then Apply ROI.")
        self.roi_rect = pg.RectROI([0, 0], [100, 100], pen=pg.mkPen("y", width=2),
                                   translateSnap=True, scaleSnap=True)
        self.roi_rect.hide()
        self.view.addItem(self.roi_rect)

        # saving
        self.folder = QtWidgets.QLineEdit(os.getcwd())
        self.browse_button = QtWidgets.QPushButton("...")
        self.file_name_edit = QtWidgets.QLineEdit(self.file_name)
        self.n_frames = spin_box(1, 100000, 10)
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

        saving = QtWidgets.QWidget()
        save_form = QtWidgets.QFormLayout(saving)
        folder_row = QtWidgets.QHBoxLayout()
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(self.browse_button)
        save_form.addRow("Folder", folder_row)
        save_form.addRow("Name", self.file_name_edit)
        save_form.addRow("Frames", self.n_frames)
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
        self.roi_rect.sigRegionChanged.connect(self.roi_rect_changed)
        self.browse_button.clicked.connect(self.browse_folder)
        self.save_button.clicked.connect(self.save_image)
        self.sequence_button.clicked.connect(self.save_sequence)

        self.device_widgets = [self.snap_button, self.live_button, self.exposure, self.binning,
                               self.select_button, self.roi_button, self.full_frame_button,
                               self.sequence_button, *self.readout_widgets]
        self.update_state()

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

    def update_temperature(self):
        try:
            self.temperature_label.setText(self.temperature_text())
        except Exception:
            # some cameras cannot be read during the live: keep the last value
            if not self.device.live:
                self.temperature_label.setText("T: -")

    # ---------- display ----------

    def display(self, image):
        auto = self.auto_levels.isChecked() or self.first_display
        self.view.setImage(image, autoLevels=auto, autoRange=self.first_display,
                           autoHistogramRange=auto)
        self.first_display = False
        frame = image if image.ndim == 2 else image[-1]
        self.stats_label.setText(f"{frame.shape[1]}x{frame.shape[0]}  min {frame.min()}  "
                                 f"max {frame.max()}  mean {frame.mean():.1f}")

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
            self.display(self.device.get_live_frame())
        except Exception as error:
            self.stop_live()
            show_error(self, error)

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

    def browse_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Saving folder", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def save_image(self):
        try:
            path = self.save(self.image, self.image_metadata)
            self.status.setText(f"Saved {os.path.basename(path)}")
        except Exception as error:
            show_error(self, error)

    def save_sequence(self):
        try:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            stack = self.run_paused(self.device.acquire_sequence, self.n_frames.value())
            self.image, self.image_metadata = stack, self.device.last_metadata
            self.display(stack)
            path = self.save(stack, self.image_metadata)
            self.status.setText(f"Saved {os.path.basename(path)}")
        except Exception as error:
            show_error(self, error)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.update_state()

    def save(self, images, metadata):
        return self.device.save(images, self.folder.text(), self.file_name_edit.text() or "image",
                                metadata=metadata, extra_metadata=self.get_extra_metadata())

    def close_device(self):
        self.temperature_timer.stop()
        if self.device is not None and self.device.live:
            self.stop_live()
        super().close_device()


class PrimePanel(CameraPanel):
    """Teledyne Photometrics Prime 95B (camera.py)."""

    device_class = Camera
    import_error = CAMERA_ERROR
    sensor_size = 1200
    file_name = "prime"

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
    file_name = "orca"

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
    file_name = "flir"

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
        self.cube.setCurrentText(self.settings.value("filter_cube", "unknown"))
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
        self.pfs_status = QtWidgets.QLabel("-")
        self.pfs_offset = spin_box(-10000, 10000, 0, decimals=3)
        self.pfs_offset_button = QtWidgets.QPushButton("Set offset")

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
        offset_row = QtWidgets.QHBoxLayout()
        offset_row.addWidget(self.pfs_offset)
        offset_row.addWidget(self.pfs_offset_button)
        pfs_form.addRow("Offset", offset_row)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(self.top)
        for box in (optics, self.focus_box, self.stage_box, self.pfs_box):
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

        self.device_widgets = [self.light_path, self.z_step, self.z_up, self.z_down, self.z_target,
                               self.z_go, self.xy_step, *self.xy_buttons.values(), self.pfs_on,
                               self.pfs_offset, self.pfs_offset_button]
        self.update_state()

    def device_name(self):
        return ", ".join(self.device.devices)

    def on_connected(self):
        ti = self.device
        self.light_path.clear()
        self.light_path.addItems(ti.light_paths)
        self.focus_box.setVisible(ti.has("TIZDrive"))
        self.stage_box.setVisible(ti.has("TIXYDrive"))
        self.pfs_box.setVisible(ti.has("TIPFSStatus"))
        self.z_target.setValue(ti.get_z())
        self.pfs_offset.setValue(ti.get_pfs_offset())
        self.refresh()
        self.timer.start()

    def refresh(self):
        """Read the state of the microscope and display it."""
        ti = self.device
        try:
            self.objective_label.setText(ti.get_objective())
            self.light_path.setCurrentText(ti.get_light_path())
            if ti.has("TIZDrive"):
                self.z_label.setText(f"{ti.get_z():.3f} µm")
            if ti.has("TIXYDrive"):
                x, y = ti.get_xy()
                self.xy_label.setText(f"x {x:.1f} µm   y {y:.1f} µm")
            if ti.has("TIPFSStatus"):
                self.pfs_on.setChecked(ti.is_pfs_on())
                self.pfs_status.setText(ti.get_pfs_status())
        except Exception as error:
            self.status.setText(f"Read error: {error}")

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
        metadata = {"microscope_filter_cube": self.cube.currentText()}  # even if not connected
        if self.device is not None:
            metadata.update({f"microscope_{key}": value
                             for key, value in self.device.get_metadata().items()})
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
        return {} if self.device is None else self.device.get_metadata()


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

        self.slm_panel = SLMPanel()
        self.prime_panel = PrimePanel(get_extra_metadata=self.extra_metadata)
        self.orca_panel = OrcaPanel(get_extra_metadata=self.extra_metadata)
        self.flir_panel = FlirPanel(get_extra_metadata=self.extra_metadata)
        self.nikon_panel = NikonPanel()
        self.piezo_panel = PiezoPanel()
        self.filter_panel = FilterWheelPanel()

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
        """SLM, microscope, piezo / filter wheels on the left, the cameras tabbed on the right."""
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
        self.tabifyDockWidget(piezo, filters)
        prime.raise_()
        piezo.raise_()
        self.resizeDocks([slm, prime], [700, 1000], QtCore.Qt.Horizontal)

    def dock_all(self):
        for dock in self.docks:
            dock.setFloating(False)
            dock.show()

    def reset_layout(self):
        self.dock_all()
        self.restoreState(self.default_state)

    def extra_metadata(self):
        """State of the SLM, microscope, piezo and filters, saved with each camera image."""
        metadata = {}
        for panel in (self.slm_panel, self.nikon_panel, self.piezo_panel, self.filter_panel):
            try:
                metadata.update(panel.get_metadata())
            except Exception as error:
                print(f"Could not read the metadata of {type(panel).__name__}: {error}")
        return metadata

    def closeEvent(self, event):
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
