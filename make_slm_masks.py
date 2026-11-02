"""
Easy SLM phase masks (1920 x 1200, 8-bit gray, 255 = 2 pi nominal) in Desktop\\SLM_masks, the
folder opened first by "Load image / .npy..." of the SLM panel (masks can also be put there by
hand, e.g. two_vortex.png made with the Thorlabs software). Run it again to rebuild them:
    python make_slm_masks.py
"""
import os

import numpy as np
from PIL import Image

FOLDER = os.path.join(os.path.expanduser("~"), "Desktop", "SLM_masks")
HEIGHT, WIDTH = 1200, 1920  # EXULUS-HD2
PIXEL_PITCH = 8e-6  # m


def diffraction(wavelength=633e-9, deviation=0.1, rotation=45.0):
    """
    Blazed grating as the "Diffraction" pattern of the Thorlabs EXULUS software: the first order
    deviated by deviation (degrees) at wavelength (m), the grating turned by rotation (degrees;
    45: diagonal lines going up to the right). Phase (rad) in [0, 2 pi).
    """
    period = wavelength / np.sin(np.radians(deviation)) / PIXEL_PITCH  # px
    rows, cols = np.mgrid[0:HEIGHT, 0:WIDTH]
    angle = np.radians(rotation)
    along = cols * np.cos(angle) + rows * np.sin(angle)  # rows go down: + gives "/" lines
    return (2 * np.pi * along / period) % (2 * np.pi), period


def to_gray(phase):
    return np.clip(np.round(phase / (2 * np.pi) * 255), 0, 255).astype(np.uint8)


if __name__ == "__main__":
    os.makedirs(FOLDER, exist_ok=True)
    phase, period = diffraction(633e-9, 0.1, 45.0)
    name = "diffraction_633nm_0.1deg_rot45.png"
    Image.fromarray(to_gray(phase)).save(os.path.join(FOLDER, name))
    print(f"{name}: period {period:.1f} px")
