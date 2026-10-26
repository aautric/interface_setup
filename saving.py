"""
Saving of camera images with their metadata, shared by camera.py (Prime 95B) and orca.py.
Requires tifffile: pip install tifffile
"""

import json
import os
from datetime import datetime

import numpy as np
import tifffile


def save_images(images, folder, name, metadata):
    """
    Save a snap (2D array) or a sequence (3D array) as a TIFF file in folder,
    with the metadata in the TIFF description and in a .json file next to it.

    folder: destination folder (created if needed).
    name: base file name; date and time (down to ms) are added so nothing is overwritten.
    Returns the path of the TIFF file.
    """
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # down to ms
    base = os.path.join(folder, f"{name}_{stamp}")
    tif_path = base + ".tif"
    tifffile.imwrite(tif_path, np.asarray(images),
                     description=json.dumps(metadata, default=str))
    with open(base + ".json", "w") as f:
        json.dump(metadata, f, indent=4, default=str)

    print(f"Saved {tif_path}")
    return tif_path
