"""
User interface to control the SLM (Thorlabs EXULUS-HD2) and the camera (Prime 95B).

    - SLM panel: load an image or a .npy phase mask (0-255), preview it, send it to the SLM.
    - Camera panel: live display, snap, exposure / binning / ROI / readout settings,
      sensor temperature, saving of images and sequences with their metadata.

Requires: pip install PyQt5 pyqtgraph   (plus the requirements of SLM.py and camera.py)
Run:      python setup_gui.py
"""

import os
import sys

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtCore, QtWidgets

from SLM import SLM, SLM_HEIGHT, SLM_WIDTH, load_phase_pattern

try:
    from camera import Camera
    CAMERA_ERROR = None
except Exception as error:  # PyVCAM / PVCAM missing: the SLM part still works
    Camera = None
    CAMERA_ERROR = error

pg.setConfigOptions(imageAxisOrder="row-major")  # numpy arrays are (y, x)

LIVE_PERIOD = 30          # ms between two live frames
TEMPERATURE_PERIOD = 2000  # ms between two temperature readings


def make_image_view():
    """Image display with contrast histogram, without the ROI/Norm buttons."""
    view = pg.ImageView()
    view.ui.roiBtn.hide()
    view.ui.menuBtn.hide()
    return view


def show_error(parent, error):
    QtWidgets.QMessageBox.critical(parent, "Error", str(error))


class SLMPanel(QtWidgets.QGroupBox):
    """Load, preview and send phase masks to the SLM."""

    def __init__(self, parent=None):
        super().__init__("SLM", parent)
        self.slm = None
        self.pattern = np.zeros((SLM_HEIGHT, SLM_WIDTH), dtype=np.uint8)
        self.pattern_name = "blank"
        self.shown_name = None  # name of the mask currently displayed on the SLM

        # widgets
        self.connect_button = QtWidgets.QPushButton("Connect")
        self.status = QtWidgets.QLabel("Disconnected")
        self.view = make_image_view()
        self.load_button = QtWidgets.QPushButton("Load image / .npy...")
        self.send_button = QtWidgets.QPushButton("Send to SLM")
        self.blank_button = QtWidgets.QPushButton("Blank (0)")
        self.auto_send = QtWidgets.QCheckBox("Send automatically after loading")
        self.mask_label = QtWidgets.QLabel()

        # layout
        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.connect_button)
        top.addWidget(self.status, 1)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.load_button)
        buttons.addWidget(self.send_button)
        buttons.addWidget(self.blank_button)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.mask_label)
        layout.addLayout(buttons)
        layout.addWidget(self.auto_send)

        # signals
        self.connect_button.clicked.connect(self.toggle_connection)
        self.load_button.clicked.connect(self.load_file)
        self.send_button.clicked.connect(self.send)
        self.blank_button.clicked.connect(self.blank)

        self.update_state()
        self.display_pattern()

    def update_state(self):
        connected = self.slm is not None
        self.connect_button.setText("Disconnect" if connected else "Connect")
        self.status.setText(f"Connected ({self.slm.serial_number})" if connected else "Disconnected")
        self.send_button.setEnabled(connected)

    def toggle_connection(self):
        try:
            if self.slm is None:
                slm = SLM()
                slm.connect()
                self.slm = slm
            else:
                self.close_device()
        except Exception as error:
            show_error(self, error)
        self.update_state()

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
        if self.auto_send.isChecked() and self.slm is not None:
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
            self.slm.show(self.pattern)
            self.shown_name = self.pattern_name
            self.status.setText(f"Connected ({self.slm.serial_number}) - showing {self.pattern_name}")
        except Exception as error:
            show_error(self, error)

    def close_device(self):
        if self.slm is not None:
            self.slm.close()
            self.slm = None
        self.shown_name = None


class CameraPanel(QtWidgets.QGroupBox):
    """Live display, settings, snap and saving for the camera."""

    def __init__(self, get_extra_metadata=None, parent=None):
        super().__init__("Camera", parent)
        self.cam = None
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

        # connection
        self.connect_button = QtWidgets.QPushButton("Connect")
        self.status = QtWidgets.QLabel("Disconnected" if CAMERA_ERROR is None
                                       else f"PyVCAM not available: {CAMERA_ERROR}")
        self.temperature_label = QtWidgets.QLabel("T: -")

        # display
        self.view = make_image_view()
        self.stats_label = QtWidgets.QLabel()
        self.auto_levels = QtWidgets.QCheckBox("Auto contrast")
        self.auto_levels.setChecked(True)

        # acquisition
        self.snap_button = QtWidgets.QPushButton("Snap")
        self.live_button = QtWidgets.QPushButton("Start live")

        # settings
        self.exposure = QtWidgets.QSpinBox()
        self.exposure.setRange(1, 60000)
        self.exposure.setValue(20)
        self.exposure.setSuffix(" ms")
        self.binning = QtWidgets.QComboBox()
        self.binning.addItems(["1", "2", "4"])
        self.readout_port = QtWidgets.QSpinBox()
        self.speed = QtWidgets.QSpinBox()
        self.gain = QtWidgets.QSpinBox()
        for box in (self.readout_port, self.speed, self.gain):
            box.setRange(0, 10)
        self.gain.setValue(1)
        self.readout_button = QtWidgets.QPushButton("Apply readout")
        self.roi_boxes = [QtWidgets.QSpinBox() for _ in range(4)]  # x, y, width, height
        for box, value in zip(self.roi_boxes, (0, 0, 1200, 1200)):
            box.setRange(0, 1200)
            box.setValue(value)
        self.roi_button = QtWidgets.QPushButton("Apply ROI")
        self.full_frame_button = QtWidgets.QPushButton("Full frame")

        # saving
        self.folder = QtWidgets.QLineEdit(os.getcwd())
        self.browse_button = QtWidgets.QPushButton("...")
        self.file_name = QtWidgets.QLineEdit("image")
        self.n_frames = QtWidgets.QSpinBox()
        self.n_frames.setRange(1, 100000)
        self.n_frames.setValue(10)
        self.save_button = QtWidgets.QPushButton("Save last image")
        self.sequence_button = QtWidgets.QPushButton("Acquire and save sequence")

        # layout
        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.connect_button)
        top.addWidget(self.status, 1)
        top.addWidget(self.temperature_label)

        acquisition = QtWidgets.QHBoxLayout()
        acquisition.addWidget(self.snap_button)
        acquisition.addWidget(self.live_button)
        acquisition.addWidget(self.auto_levels)
        acquisition.addWidget(self.stats_label, 1)

        settings = QtWidgets.QGroupBox("Settings")
        form = QtWidgets.QFormLayout(settings)
        form.addRow("Exposure", self.exposure)
        form.addRow("Binning", self.binning)
        readout = QtWidgets.QHBoxLayout()
        for label, box in (("port", self.readout_port), ("speed", self.speed), ("gain", self.gain)):
            readout.addWidget(QtWidgets.QLabel(label))
            readout.addWidget(box)
        readout.addWidget(self.readout_button)
        form.addRow("Readout", readout)
        roi = QtWidgets.QHBoxLayout()
        for label, box in zip(("x", "y", "w", "h"), self.roi_boxes):
            roi.addWidget(QtWidgets.QLabel(label))
            roi.addWidget(box)
        roi.addWidget(self.roi_button)
        roi.addWidget(self.full_frame_button)
        form.addRow("ROI", roi)

        saving = QtWidgets.QGroupBox("Saving")
        save_form = QtWidgets.QFormLayout(saving)
        folder_row = QtWidgets.QHBoxLayout()
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(self.browse_button)
        save_form.addRow("Folder", folder_row)
        save_form.addRow("Name", self.file_name)
        save_form.addRow("Frames", self.n_frames)
        save_buttons = QtWidgets.QHBoxLayout()
        save_buttons.addWidget(self.save_button)
        save_buttons.addWidget(self.sequence_button)
        save_form.addRow(save_buttons)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.view, 1)
        layout.addLayout(acquisition)
        layout.addWidget(settings)
        layout.addWidget(saving)

        # signals
        self.connect_button.clicked.connect(self.toggle_connection)
        self.snap_button.clicked.connect(self.snap)
        self.live_button.clicked.connect(self.toggle_live)
        self.exposure.editingFinished.connect(self.apply_exposure)
        self.binning.currentTextChanged.connect(self.apply_binning)
        self.readout_button.clicked.connect(self.apply_readout)
        self.roi_button.clicked.connect(self.apply_roi)
        self.full_frame_button.clicked.connect(self.apply_full_frame)
        self.browse_button.clicked.connect(self.browse_folder)
        self.save_button.clicked.connect(self.save_image)
        self.sequence_button.clicked.connect(self.save_sequence)

        self.update_state()

    # ---------- connection ----------

    def update_state(self):
        connected = self.cam is not None
        self.connect_button.setText("Disconnect" if connected else "Connect")
        self.connect_button.setEnabled(Camera is not None)
        if Camera is not None:
            self.status.setText(f"Connected ({self.cam.name})" if connected else "Disconnected")
        for widget in (self.snap_button, self.live_button, self.exposure, self.binning,
                       self.readout_button, self.roi_button, self.full_frame_button,
                       self.sequence_button):
            widget.setEnabled(connected)
        self.save_button.setEnabled(self.image is not None)

    def toggle_connection(self):
        try:
            if self.cam is None:
                cam = Camera(exposure_time=self.exposure.value())
                cam.connect()
                self.cam = cam
                self.temperature_timer.start()
                self.update_temperature()
            else:
                self.close_device()
        except Exception as error:
            show_error(self, error)
        self.update_state()

    def update_temperature(self):
        try:
            self.temperature_label.setText(f"T: {self.cam.temperature:.1f} °C")
        except Exception:
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
        was_live = self.cam.live
        if was_live:
            self.stop_live()
        try:
            return function(*args)
        finally:
            if was_live:
                self.start_live()

    def snap(self):
        try:
            self.image = self.run_paused(self.cam.snap)
            self.image_metadata = self.cam.last_metadata
            self.display(self.image)
        except Exception as error:
            show_error(self, error)
        self.update_state()

    def start_live(self):
        self.cam.start_live()
        self.live_timer.start()
        self.live_button.setText("Stop live")

    def stop_live(self):
        self.live_timer.stop()
        self.cam.stop_live()
        self.live_button.setText("Start live")

    def toggle_live(self):
        try:
            if self.cam.live:
                self.stop_live()
            else:
                self.start_live()
        except Exception as error:
            show_error(self, error)

    def update_live(self):
        try:
            self.display(self.cam.get_live_frame())
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
        if self.cam is not None:
            self.apply_setting(self.cam.set_exposure, self.exposure.value())

    def apply_binning(self, text):
        if self.cam is not None:
            self.apply_setting(self.cam.set_binning, int(text))

    def apply_readout(self):
        self.apply_setting(self.cam.set_readout, self.readout_port.value(),
                           self.speed.value(), self.gain.value())

    def apply_roi(self):
        self.apply_setting(self.cam.set_roi, *(box.value() for box in self.roi_boxes))

    def apply_full_frame(self):
        self.apply_setting(self.cam.reset_roi)
        self.binning.setCurrentText("1")

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
            stack = self.run_paused(self.cam.acquire_sequence, self.n_frames.value())
            self.image, self.image_metadata = stack, self.cam.last_metadata
            self.display(stack)
            path = self.save(stack, self.image_metadata)
            self.status.setText(f"Saved {os.path.basename(path)}")
        except Exception as error:
            show_error(self, error)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.update_state()

    def save(self, images, metadata):
        return self.cam.save(images, self.folder.text(), self.file_name.text() or "image",
                             metadata=metadata, extra_metadata=self.get_extra_metadata())

    def close_device(self):
        self.temperature_timer.stop()
        if self.cam is not None:
            if self.cam.live:
                self.stop_live()
            self.cam.close()
            self.cam = None


class MainWindow(QtWidgets.QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SLM + camera")
        self.slm_panel = SLMPanel()
        self.camera_panel = CameraPanel(get_extra_metadata=self.extra_metadata)

        splitter = QtWidgets.QSplitter()
        splitter.addWidget(self.slm_panel)
        splitter.addWidget(self.camera_panel)
        self.setCentralWidget(splitter)
        self.resize(1600, 900)

    def extra_metadata(self):
        """Information about the other devices, saved with each camera image."""
        return {"slm_mask": self.slm_panel.shown_name}  # None if nothing sent to the SLM

    def closeEvent(self, event):
        for panel in (self.camera_panel, self.slm_panel):
            try:
                panel.close_device()
            except Exception as error:
                print(f"Error while closing {panel.title()}: {error}")
        event.accept()


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    app.exec_()


if __name__ == "__main__":
    main()
