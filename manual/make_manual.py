"""
Build the user manual of Bumblebee: screenshots of the GUI (rendered off screen, no device
connected, nothing saved) in manual/img, then manual/user_manual_bumblebee.html -> PDF on the
desktop with Microsoft Edge (headless).

    conda activate setup_env
    python manual\\make_manual.py            (screenshots + PDF)
    python manual\\make_manual.py --pdf      (PDF only, after editing the HTML)
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(HERE, "user_manual_bumblebee.html")
PDF = os.path.join(os.path.expanduser("~"), "Desktop", "user_manual_bumblebee.pdf")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PDF_TIMEOUT = 60  # s to wait for the PDF printed by Edge
# a bead image shown in the camera panels of the screenshots (FLIR on the microscope, 2026-10-27)
SAMPLE_IMAGE = r"D:\BUMBLEBEE_acquisitions\2026_10_27_test\beads4_flir_on_microscope\0_0.tiff"


def screenshots():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    sys.path.insert(0, os.path.dirname(HERE))
    os.chdir(os.path.dirname(HERE))
    from PyQt5 import QtGui, QtWidgets
    import setup_gui

    # do not touch the files of a GUI running at the same time
    setup_gui.FileMover.move_leftovers = lambda self, *args, **kwargs: 0
    app = QtWidgets.QApplication(sys.argv)
    app.setFont(QtGui.QFont("Segoe UI", 9))
    window = setup_gui.MainWindow()
    window.restoreState(window.default_state)  # default layout (the saved one is not changed)
    window.resize(1700, 1000)
    window.show()
    try:
        import tifffile
        image = tifffile.imread(SAMPLE_IMAGE, key=0)[424:1624, 624:1824]
        window.prime_panel.display(image)
    except Exception as error:  # no sample image: empty display
        print("no sample image:", error)
    img = os.path.join(HERE, "img")
    os.makedirs(img, exist_ok=True)

    def grab(widget, name, size=None):
        if size:
            widget.resize(*size)
        for _ in range(10):
            app.processEvents()
        widget.grab().save(os.path.join(img, name))

    grab(window, "main_window.png")
    tabs = window.prime_panel.findChild(QtWidgets.QTabWidget)
    grab(window.prime_panel, "camera_settings.png", (900, 820))
    tabs.setCurrentIndex(1)
    grab(window.prime_panel, "camera_saving.png", (900, 820))
    for panel, name, size in ((window.slm_panel, "slm.png", (700, 700)),
                              (window.nikon_panel, "microscope.png", (520, 620)),
                              (window.piezo_panel, "piezo.png", (520, 330)),
                              (window.filter_panel, "filter_wheels.png", (520, 330))):
        grab(panel, name, size)
    # calibration window: the first two steps (no device connected)
    window.calibration_panel.findChild(QtWidgets.QPushButton).click()
    wizard = window.calibration_panel.wizards["align_BFP"]
    grab(wizard, "calibration_step1.png")
    for box in wizard.currentPage().boxes:
        box.setChecked(True)
    wizard.next()
    grab(wizard, "calibration_step2.png")
    print("screenshots in", img)
    os._exit(0)  # no closeEvent: the layout and settings saved by the GUI are not changed


def pdf():
    # printed to a temporary file first: Edge fails silently if the PDF is open in a reader
    url = "file:///" + HTML.replace("\\", "/")
    temporary = os.path.join(HERE, "user_manual_bumblebee.tmp.pdf")
    if os.path.exists(temporary):
        os.remove(temporary)
    # own profile: a headless Edge sharing the profile of an open Edge window does not print
    profile = os.path.join(os.environ.get("TEMP", HERE), "bumblebee_manual_edge")
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--user-data-dir={profile}",
                    f"--print-to-pdf={temporary}", url], check=True, timeout=120)
    # Edge 156 returns before the PDF is written: wait for it, until its size stops changing
    size, deadline = -1, time.monotonic() + PDF_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(1)
        if os.path.exists(temporary) and os.path.getsize(temporary) == size > 0:
            break
        size = os.path.getsize(temporary) if os.path.exists(temporary) else -1
    if not os.path.exists(temporary):
        raise RuntimeError("Edge did not write the PDF")
    try:
        os.replace(temporary, PDF)
        print("PDF:", PDF)
    except PermissionError:  # open in Acrobat Reader...
        other = PDF[:-len(".pdf")] + " (new).pdf"
        os.replace(temporary, other)
        print(f"{PDF} is open in another program: saved as {other}; close the reader and "
              f"run again to replace it.")


if __name__ == "__main__":
    if "--pdf" in sys.argv:
        pdf()
    else:
        subprocess.run([sys.executable, __file__, "--screenshots"], check=True) \
            if "--screenshots" not in sys.argv else screenshots()
        if "--screenshots" not in sys.argv:
            pdf()
