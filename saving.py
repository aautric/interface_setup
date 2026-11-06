"""
Saving of camera images with their metadata, shared by camera.py (Prime 95B), orca.py and flir.py.
Requires tifffile: pip install tifffile

File names in a folder (all the information is in the metadata):
    X_Y.tiff        acquisition X (0, 1, 2... in the order of the acquisitions in the folder),
                    file Y of it (0, 1...: a long sequence is split into files of ~PIECE_BYTES)
    X.json          metadata of acquisition X, in 4 sections (organize_metadata):
                    acquisition (index, date, camera, n_frames, files...), devices (connected
                    or not, state of the microscope, piezo, filter wheels...), slm (state,
                    masks shown), camera (main settings, then all the "advanced" parameters)
    X_frames.csv    time of each frame of a sequence (PC clock, and camera clock if available)
                    and the SLM mask shown during it (slm_mask: number in X_slm_masks, or none)
    X_slm_masks\\    the SLM masks shown during acquisition X, as K.png (K = 0, 1... in the
                    order they were first shown; a mask shown again keeps its number)
    X_calibrations\\ copies of the calibration files that corrected the SLM mask (linked in
                    the SLM panel; metadata slm > linked_calibrations), with their .png
"""

import csv
import hashlib
import json
import os
import queue
import re
import shutil
import threading
import time
from datetime import datetime

import numpy as np
import tifffile
from PIL import Image

TIFF_LIMIT = 2**32 - 2**25  # bytes; above, a BigTIFF file is needed (classic TIFF < 4 GB)
PIECE_BYTES = 10e9  # a sequence is split into TIFF files of at most this size

ACQUISITION_ROOT = r"D:\BUMBLEBEE_acquisitions"  # final destination (hard disk, 2 TB)
CALIBRATION_ROOT = r"D:\BUMBLEBEE_calibration"  # calibrations: <calibration name>\YYYY_MM_DD
STAGING_ROOT = r"C:\BUMBLEBEE_staging"  # fast disk (NVMe SSD): sequences are written here
                                        # during the acquisition, then moved
PIXEL_BITS = (16, 8)  # saved pixel sizes: 2 bytes (as acquired) or 1 byte
FREE_SPACE_MARGIN = 5e9  # bytes always left free on a disk (C: is also the system disk)


def _drive(path):
    return os.path.splitdrive(os.path.abspath(path))[0].upper()


def check_free_space(size, folders, incoming=None, margin=FREE_SPACE_MARGIN):
    """
    Raise ValueError if one of the folders (created if needed) has not size bytes free on its
    disk, plus margin, plus incoming(folder) bytes still to arrive (files being moved there).
    """
    for folder in dict.fromkeys(folders):  # each folder once, in order
        os.makedirs(folder, exist_ok=True)
        free = shutil.disk_usage(folder).free
        coming = incoming(folder) if incoming else 0
        if size + margin + coming > free:
            moving = f" + {coming / 1e9:.1f} GB still being moved there" if coming else ""
            raise ValueError(
                f"Not enough space on {_drive(folder)} ({folder}):\n"
                f"needed {size / 1e9:.1f} GB{moving} + {margin / 1e9:.0f} GB kept free, "
                f"only {free / 1e9:.1f} GB free.\n"
                f"Free some space, or acquire fewer frames / 8 bit pixels / a smaller ROI.")


def experiment_folder(name, subfolder="", root=ACQUISITION_ROOT, date=None):
    """
    root/YYYY_MM_DD_name, or root/YYYY_MM_DD_name/subfolder if subfolder is not empty;
    ValueError if name is empty or name / subfolder is not a valid folder name.
    """
    name, subfolder = name.strip(), subfolder.strip()
    if not name:
        raise ValueError("Enter an experiment name first (folder YYYY_MM_DD_name).")
    for label, text in (("Experiment name", name), ("Subfolder", subfolder)):
        if any(char in '<>:"/\\|?*' for char in text) or text.endswith("."):
            raise ValueError(f'{label} "{text}": these characters are not allowed in a '
                             f'folder name: < > : " / \\ | ? * (nor a final dot)')
    folder = os.path.join(root, f"{(date or datetime.now()):%Y_%m_%d}_{name}")
    return os.path.join(folder, subfolder) if subfolder else folder


def calibration_folder(name, date=None):
    """CALIBRATION_ROOT/<calibration name>/YYYY_MM_DD: one folder per type of calibration, then
    one per day."""
    return os.path.join(CALIBRATION_ROOT, name, f"{(date or datetime.now()):%Y_%m_%d}")


def saved_pixel_metadata(metadata, pixel_bits=16):
    """
    metadata with how the pixels are saved. pixel_bits=16: as acquired. pixel_bits=8: 1 byte
    per pixel, the 8 highest bits of the camera values (metadata "bit_depth": range of the
    values, e.g. 12 for the Prime, 16 for the Orca and the FLIR whose 12-bit values are
    scaled to 16 bit); camera value ~ saved value * 2**saved_value_shift.
    """
    if pixel_bits == 16:
        return {**metadata, "saved_pixel_bits": 16}
    if pixel_bits != 8:
        raise ValueError(f"pixel_bits must be 16 or 8, not {pixel_bits}")
    shift = max(int(metadata.get("bit_depth") or 16) - 8, 0)
    return {**metadata, "saved_pixel_bits": 8, "saved_value_shift": shift}


def to_saved_pixels(images, metadata, pixel_bits=16):
    """(images, metadata) as saved, see saved_pixel_metadata."""
    metadata = saved_pixel_metadata(metadata, pixel_bits)
    if pixel_bits == 8:
        images = _to_8bit(images, metadata["saved_value_shift"])
    return images, metadata


def _to_8bit(images, shift):
    return np.minimum(np.asarray(images) >> shift, 255).astype(np.uint8)


_INDEX = re.compile(r"^(\d+)[_.]")  # acquisition index at the start of a file name


def next_index(*folders):
    """
    Index of the next acquisition: 1 + the highest X of the files X_Y.tiff / X.json /
    X_frames.csv (also .part files being moved) found in the folders (None or missing: skipped).
    Give the destination and its fast-disk folder so both are counted.
    """
    indexes = [-1]
    for folder in folders:
        if folder and os.path.isdir(folder):
            indexes += [int(m.group(1)) for m in map(_INDEX.match, os.listdir(folder)) if m]
    return max(indexes) + 1


def tiff_name(index, piece=0):
    return f"{index}_{piece}.tiff"


def json_name(index):
    return f"{index}.json"


def frames_name(index):
    return f"{index}_frames.csv"


def masks_folder_name(index):
    return f"{index}_slm_masks"


def calibrations_folder_name(index):
    return f"{index}_calibrations"


def _write_json(path, metadata):
    with open(path, "w") as f:
        json.dump(metadata, f, indent=4, default=str)


# keys of the camera metadata that go to the "acquisition" section
_ACQUISITION_KEYS = ("date", "n_frames", "frame_times", "saved_pixel_bits", "saved_value_shift")


def organize_metadata(camera_metadata, extra_metadata=None):
    """
    Metadata of an acquisition in 4 sections, in this order:
      acquisition  index, date, camera, n_frames, files... (filled by SequenceWriter), and
                   any key of extra_metadata other than devices / slm (e.g. notes)
      devices      extra_metadata["devices"]: connected or not, state of each device
      slm          extra_metadata["slm"] + the masks shown (filled by SequenceWriter)
      camera       camera settings: main ones first, then "advanced" (all the parameters)
    camera_metadata: flat dictionary of the camera (get_metadata), with "advanced".
    """
    camera = dict(camera_metadata)
    extra = dict(extra_metadata or {})
    acquisition = {key: camera.pop(key) for key in _ACQUISITION_KEYS if key in camera}
    acquisition = {"camera": camera.get("camera"), **acquisition}  # also kept in "camera"
    devices = extra.pop("devices", {})
    slm = dict(extra.pop("slm", {}) or {})
    acquisition.update(extra)
    advanced = camera.pop("advanced", None)
    if advanced is not None:
        camera["advanced"] = advanced  # last
    return {"acquisition": acquisition, "devices": devices, "slm": slm, "camera": camera}


def save_images(images, folder, metadata, pixel_bits=16, index=None, extra_metadata=None,
                slm_mask=None):
    """
    Save a snap (2D array) or a sequence (3D array) in folder as X_0.tiff (split into
    X_1.tiff... if very large) + X.json, the metadata also in the TIFF description.

    folder: destination folder (created if needed).
    metadata: camera metadata; extra_metadata: devices / slm / other (see organize_metadata).
    pixel_bits: 16 (as acquired) or 8 (1 byte per pixel, see to_saved_pixels).
    index: acquisition number X; None = next free one in folder (next_index).
    slm_mask: (pattern, name) of the SLM mask shown when the images were taken, or None.
    Returns the path of the (first) TIFF file.
    """
    images = np.asarray(images)
    frames = images if images.ndim == 3 else images[np.newaxis]
    with SequenceWriter(folder, extra_metadata, size_bytes=frames.nbytes, pixel_bits=pixel_bits,
                        index=index, frame_times=False, slm_mask=lambda: slm_mask) as writer:
        writer.start(metadata)
        for frame in frames:
            writer.write(frame)
    return writer.paths[0]


class FrameCollector:
    """
    Frames of a sequence as they arrive, used by the acquire_sequence of the cameras:
    kept in memory, or written to a SequenceWriter if one is given.
    metadata: the camera's metadata of the sequence; its n_frames is set to the frames taken.
    """

    def __init__(self, metadata, writer=None):
        self.metadata = metadata
        self.writer = writer
        self.frames = []
        self.count = 0
        self.last = None
        if writer is not None:
            writer.start(metadata)

    def __call__(self, frame, n_frames, progress=None, info=None):
        """
        Store one frame; return False if progress(done, n_frames, frame) asks to stop.
        info: optional dictionary saved with the frame time (e.g. camera_frame, camera_time_s).
        """
        if self.writer is None:
            self.frames.append(frame)
        else:
            self.writer.write(frame, info)
        self.count += 1
        self.last = frame
        self.metadata["n_frames"] = self.count
        return progress is None or progress(self.count, n_frames, frame) is not False

    def result(self):
        """3D array of the frames, or only the last frame (2D) if they went to a file."""
        if self.writer is None:
            return np.array(self.frames)
        return self.last


class SequenceWriter:
    """
    Write a sequence frame by frame while it is acquired, so the frames are not kept in
    memory (any number of frames, limited by the disk only), as acquisition X of folder:
      X_0.tiff, X_1.tiff...  the frames, a new file every ~piece_bytes (each file has the
                             metadata in its first page, with "file_index" and "first_frame")
      X_frames.csv           frame, pc_time_s (PC clock when the frame was received),
                             camera_frame / camera_time_s if the camera gives them,
                             slm_mask (number of the SLM mask shown, or none)
      X_calibrations\\       copies of the calibration files of extra_metadata["slm"]
                             ["linked_calibrations"] ({name: {"file"...}}), with their .png
      X_slm_masks\\K.png      the SLM masks shown, numbered in the order they first appear
      X.json                 metadata (organize_metadata), written at the end with the number
                             of frames really saved and the list of files

        with SequenceWriter(folder, extra_metadata) as writer:
            cam.acquire_sequence(1000, writer=writer)

    extra_metadata: devices / slm / other (see organize_metadata).
    size_bytes: expected size; BigTIFF files are used above 4 GB (or if unknown).
    pixel_bits: 16 (as acquired) or 8 (1 byte per pixel, see to_saved_pixels).
    index: acquisition number X; None = next free one in folder (next_index).
    on_file_done: optional function(path) called for each file once complete (a TIFF file as
        soon as the next one starts, the others at close), e.g. to move it to another disk.
    frame_times: write X_frames.csv (otherwise the mask of each frame goes in the .json).
    slm_mask: optional function() -> (pattern, name) of the SLM mask shown now, or None (no
        mask, SLM not connected); called at each frame.
    frame_columns: optional {name: function()}, more columns of X_frames.csv, each function
        called at each frame (e.g. the z plane of a z stack).
    """

    def __init__(self, folder, extra_metadata=None, size_bytes=None, pixel_bits=16, index=None,
                 on_file_done=None, piece_bytes=PIECE_BYTES, frame_times=True, slm_mask=None,
                 frame_columns=None):
        os.makedirs(folder, exist_ok=True)
        self.folder = folder
        self.index = next_index(folder) if index is None else index
        self.json_path = os.path.join(folder, json_name(self.index))
        self.frames_path = os.path.join(folder, frames_name(self.index)) if frame_times else None
        self.masks_folder = os.path.join(folder, masks_folder_name(self.index))
        self.paths = []  # TIFF files, in order
        self.extra_metadata = dict(extra_metadata or {})
        self.metadata = organize_metadata({}, self.extra_metadata)
        self.pixel_bits = pixel_bits
        self.piece_bytes = piece_bytes
        self.on_file_done = on_file_done
        self.slm_mask = slm_mask
        self.n_frames = 0
        self._shift = 0  # 8-bit saving: camera value >> shift
        self._piece_frames = 0  # frames in the current TIFF file
        self._bigtiff = size_bytes is None or size_bytes > TIFF_LIMIT
        self._tif = None
        self._new_piece()  # created now, so the index is taken
        self._masks = []  # SLM masks saved: {"file", "name", "first_frame"}
        self._mask_numbers = {}  # content hash -> mask number
        self._last_mask = (None, "none")  # (pattern object, number) of the previous frame
        self._frame_masks = []  # mask of each frame, when there is no X_frames.csv
        self._times = None
        self._t0 = None
        self.frame_columns = dict(frame_columns or {})
        if frame_times:
            self._times_file = open(self.frames_path, "w", newline="")
            self._times = csv.writer(self._times_file)
            self._times.writerow(["frame", "pc_time_s", "camera_frame", "camera_time_s",
                                  "slm_mask", *self.frame_columns])

    def _new_piece(self):
        if self._tif is not None:
            self._tif.close()
            self._done(self.paths[-1])
        self.paths.append(os.path.join(self.folder, tiff_name(self.index, len(self.paths))))
        self._tif = tifffile.TiffWriter(self.paths[-1], bigtiff=self._bigtiff)
        self._piece_frames = 0

    def _done(self, path):
        if self.on_file_done is not None:
            self.on_file_done(path)

    @property
    def tif_path(self):
        """First TIFF file."""
        return self.paths[0]

    def start(self, metadata):
        """Camera metadata (called by the camera before the first frame; a sequence acquired in
        several parts, e.g. the planes of a z stack, keeps the metadata of its first part)."""
        if self.n_frames:
            return
        camera = saved_pixel_metadata(metadata, self.pixel_bits)
        self._shift = camera.get("saved_value_shift", 0)
        self.metadata = organize_metadata(camera, self.extra_metadata)
        self.metadata["acquisition"] = {"index": self.index, **self.metadata["acquisition"]}

    def _mask_number(self):
        """Number of the SLM mask shown now ("none" if none); its PNG is saved the first time."""
        shown = self.slm_mask() if self.slm_mask is not None else None
        if shown is None:
            return "none"
        pattern, name = shown
        if pattern is self._last_mask[0]:  # same mask as the previous frame: no hashing
            return self._last_mask[1]
        pattern = np.asarray(pattern, dtype=np.uint8)
        key = hashlib.sha1(np.ascontiguousarray(pattern).tobytes()).hexdigest()
        number = self._mask_numbers.get(key)
        if number is None:
            number = len(self._masks)
            os.makedirs(self.masks_folder, exist_ok=True)
            Image.fromarray(pattern).save(os.path.join(self.masks_folder, f"{number}.png"),
                                          compress_level=1)
            self._mask_numbers[key] = number
            self._masks.append({"number": number,
                                "file": f"{masks_folder_name(self.index)}/{number}.png",
                                "name": name, "first_frame": self.n_frames})
        self._last_mask = (shown[0], number)
        return number

    def write(self, frame, info=None):
        """Write one frame; info: optional dict with camera_frame and / or camera_time_s."""
        now = time.time()
        mask = self._mask_number()
        if self.pixel_bits == 8:
            frame = _to_8bit(frame, self._shift)
        if self._piece_frames and (self._piece_frames + 1) * frame.nbytes > self.piece_bytes:
            self._new_piece()
        description = None
        if self._piece_frames == 0:  # metadata in the first page of each file
            acquisition = {**self.metadata["acquisition"], "file_index": len(self.paths) - 1,
                           "first_frame": self.n_frames}
            description = json.dumps({**self.metadata, "acquisition": acquisition}, default=str)
        self._tif.write(frame, contiguous=True, description=description, metadata=None)
        if self._times is not None:
            if self._t0 is None:
                self._t0 = now
                self.metadata["acquisition"]["first_frame_pc_time"] = \
                    datetime.fromtimestamp(now).isoformat()
            info = info or {}
            self._times.writerow([self.n_frames, f"{now - self._t0:.6f}", info.get("camera_frame", ""),
                                  "" if info.get("camera_time_s") is None
                                  else f"{info['camera_time_s']:.9f}", mask,
                                  *(column() for column in self.frame_columns.values())])
        else:
            self._frame_masks.append(mask)
        self._piece_frames += 1
        self.n_frames += 1

    def close(self):
        if self._tif is None:
            return
        self._tif.close()
        self._tif = None
        if self._times is not None:
            self._times_file.close()
        mask_paths = [os.path.join(self.folder, m["file"]) for m in self._masks]
        if self.n_frames == 0:  # nothing acquired (error at the first frame): no file
            for path in self.paths + [self.frames_path] + mask_paths:
                if path and os.path.exists(path):
                    os.remove(path)
            if os.path.isdir(self.masks_folder) and not os.listdir(self.masks_folder):
                os.rmdir(self.masks_folder)
            return
        acquisition = self.metadata["acquisition"]
        acquisition["n_frames"] = self.n_frames
        acquisition["files"] = [os.path.basename(p) for p in self.paths]
        if self.frames_path:
            acquisition["frame_times_file"] = os.path.basename(self.frames_path)
        slm = self.metadata["slm"]
        slm["masks_folder"] = masks_folder_name(self.index) if self._masks else None
        slm["masks"] = self._masks
        slm["mask_of_each_frame"] = (f"{os.path.basename(self.frames_path)}, column slm_mask"
                                     if self.frames_path else self._frame_masks)
        calibration_paths = self._copy_calibrations()
        _write_json(self.json_path, self.metadata)
        for path in mask_paths + calibration_paths + [self.paths[-1], self.frames_path,
                                                      self.json_path]:
            if path:
                self._done(path)
        print(f"Saved acquisition {self.index} in {self.folder} ({self.n_frames} frames, "
              f"{len(self.paths)} file(s))")

    def _copy_calibrations(self):
        """Copy the linked calibration files (and their .png) to X_calibrations; their
        metadata gets "copy" (path relative to the folder). Returns the copied files."""
        slm = self.metadata["slm"]
        linked = {name: dict(info) for name, info in
                  (slm.get("linked_calibrations") or {}).items()}  # not shared with the caller
        copied = []
        for name, info in linked.items():
            source = info.get("file")
            try:
                target_folder = os.path.join(self.folder, calibrations_folder_name(self.index))
                os.makedirs(target_folder, exist_ok=True)
                for path in (source, os.path.splitext(source)[0] + ".png"):
                    if path == source or os.path.isfile(path):
                        copied.append(shutil.copy2(path, target_folder))
                info["copy"] = (f"{calibrations_folder_name(self.index)}/"
                                f"{os.path.basename(source)}")
            except Exception as error:
                info["copy"] = None
                info["copy_error"] = str(error)
                print(f"Calibration {name} not copied with acquisition {self.index}: {error}")
        if linked:
            slm["linked_calibrations"] = linked
        return copied

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FileMover:
    """
    Moves finished files from the fast disk to their final folder in a background thread,
    one file at a time, so the GUI is not blocked (10 GB take ~1 min to a hard disk).
    Each file is copied as <name>.part, renamed, then deleted from the fast disk: if the
    program is closed during a move, the file is still on the fast disk and
    move_leftovers() moves it at the next start.
    """

    def __init__(self):
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self.pending = 0  # files waiting or being moved
        self._incoming = {}  # drive -> bytes of the files waiting or being moved to it
        self.errors = []  # messages of the failed moves (the files stay on the fast disk)
        threading.Thread(target=self._run, daemon=True).start()

    def move(self, path, folder):
        """Move the file path into folder (created if needed), in the background."""
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        with self._lock:
            self.pending += 1
            self._incoming[_drive(folder)] = self._incoming.get(_drive(folder), 0) + size
        self._queue.put((path, folder, size))

    def incoming_bytes(self, folder):
        """Bytes still to be moved to the disk of folder."""
        with self._lock:
            return self._incoming.get(_drive(folder), 0)

    def _run(self):
        while True:
            path, folder, size = self._queue.get()
            try:
                os.makedirs(folder, exist_ok=True)
                target = os.path.join(folder, os.path.basename(path))
                shutil.copyfile(path, target + ".part")
                os.replace(target + ".part", target)
                os.remove(path)
                source = os.path.dirname(path)
                if source.endswith(("_slm_masks", "_calibrations")) and not os.listdir(source):
                    os.rmdir(source)  # all the masks / calibrations of the acquisition moved
            except Exception as error:
                with self._lock:
                    self.errors.append(f"{path} -> {folder}: {error}")
            finally:
                with self._lock:
                    self.pending -= 1
                    self._incoming[_drive(folder)] -= size

    def take_errors(self):
        """Error messages since the last call."""
        with self._lock:
            errors, self.errors = self.errors, []
        return errors

    def move_leftovers(self, staging_root=STAGING_ROOT, final_root=ACQUISITION_ROOT):
        """
        Move the files left in staging_root/<folder>[/<subfolder>]/ to the same folder in
        final_root and remove the empty folders. Call it only when no acquisition is writing
        there. Returns the number of files.
        """
        if not os.path.isdir(staging_root):
            return 0
        n_files = 0
        for folder, subfolders, files in os.walk(staging_root, topdown=False):
            if folder == staging_root:
                continue  # files directly in staging_root are not from the GUI
            if not files and not os.listdir(folder):
                os.rmdir(folder)
            relative = os.path.relpath(folder, staging_root)
            for name in sorted(files):
                self.move(os.path.join(folder, name), os.path.join(final_root, relative))
            n_files += len(files)
        return n_files
