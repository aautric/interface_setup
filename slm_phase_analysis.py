"""
Analysis of the slm_phase calibration (calibration.py): phase applied by the SLM for each
gray level 0...255, for each Orca pixel lit by the BFP disks.

The recording (one run): for each angle of the quarter-wave plate (QWP_ANGLES, turned by hand,
before the polarizer of the Orca), one acquisition: one frame per flat mask on the SLM, at
gray level 0, step, 2 step... 255 (gray_levels, the step chosen in the window, default 4: 65
frames; the levels are in "levels" of X.json). The light reaches the SLM at ~45 deg of its
liquid-crystal axis: the SLM delays one polarization component, not the other, so the
polarization of each pixel changes with the gray level.

For each Orca pixel and gray level g (polarimetry repo, polarimetre.py, same formulas):
    1. Fourier coefficients of the intensity over the 9 plate angles: p0, p2, q2, p4, q4;
       zero of the plate: offset d with p2 = q2 tan(2 d), fitted on all pixels and gray levels
       at once (the ramp gives elliptical light, needed for it), then
       S1 = 2 p4 / (cos phi - 1), S2 = 2 q4 / (cos phi - 1), S3 = -q2 / sin phi,
       S0 = p0 + S1 (1 + cos phi) / 2, phi = retardance of the plate at the wavelength;
    2. the normalized Stokes vector s(g) = (S1, S2, S3) / S0 moves on a circle of the Poincare
       sphere, a rotation about the axis n of the SLM liquid crystal, the same for all pixels:
       n = normal of the planes of the circles, fitted once on all pixels (a fit per pixel fails
       on single noisy Orca pixels), and the phase of g is the angle turned about n from g = 0,
       unwrapped along the median curve of the disk (each pixel within +-pi of it; positive
       towards 255). Optics after the SLM (beam splitter, mirrors) only
       rotate the whole sphere: the angles, so the phase, do not change. Unmodulated or
       depolarized light makes the circle smaller (modulation), not the phase wrong.
The dark level (camera offset, ~100 counts on the Orca, + stray light) is the median of the
image away from the disks, subtracted first.
Only phase differences from g = 0 are measured (phase(0) = 0). No correction of a drift of the
light between the sequences (to add if needed: a g = 0 frame at the end of each sequence, and
the 0 deg sequence again at the end).

Results (next to the recordings, <first>-<last>_slm_phase_<YYYY_MM_DD_HHMMSS>):
    .json  summary: settings, plate offset, per disk the median phase curve and the
           gray level of 2 pi
    .npz   per lit Orca pixel: rows, cols (Orca px), phase (levels x pixels, rad), levels, modulation
           (circle radius), plane_rms (distance of the points to the plane), axis (3, common),
           mean_intensity; and the disk of each pixel
    .png   phase curves of the disks, maps of the phase at 255, of the gray level of 2 pi and
           of the modulation

Only numpy, scipy.ndimage and tifffile: matrix products and np.linalg crash Python in setup_env
(broken BLAS): the 3 x 3 eigenvectors are computed by explicit formulas.

Only the disk centred in the right half of the Orca (one channel) is analysed by default (DEFAULT_CHANNELS = "right",
for the first tests with one channel); "both" = the whole image (the two channels).

Command line: python slm_phase_analysis.py <folder> <index of any acquisition of the run> [bin]
[right|both]
"""

import json
import os
import sys
from datetime import datetime

import numpy as np
import tifffile
from scipy import ndimage

QWP_ANGLES = [0, 40, 80, 120, 160, 200, 240, 280, 320]  # deg, 9 equally spaced over 360
GRAY_STEP = 4  # default step between the gray levels of a sequence (flat masks)


def gray_levels(step=GRAY_STEP):
    """Gray levels of a sequence: 0, step, 2 step... and always 255 last."""
    levels = list(range(0, 256, step))
    return levels if levels[-1] == 255 else levels + [255]


LEVELS = gray_levels()
# Orca part analysed: "right" = the disk centred in the right half only (one channel, for the first
# tests; the whole disk, even past the middle), "both" = all the disks (the two channels)
CHANNELS = {"right": "Right channel only (disk centred in the right half of the Orca)",
            "both": "Both channels (whole Orca image)"}
DEFAULT_CHANNELS = "right"
CHUNK = 20000  # pixels analysed at once
ROW_BLOCK = 64  # Orca rows read at once

# retardance of the quarter-wave plate (in waves) for 400...800 nm, from the polarimetry repo
# (polarimetre.py, manufacturer data of the plate of the polarimeter)
QWP_WAVELENGTHS = list(range(400, 801))
QWP_RETARDANCE = [
    0.19896, 0.19991, 0.20086, 0.20180, 0.20274, 0.20368, 0.20461, 0.20553,
    0.20645, 0.20737, 0.20828, 0.20919, 0.21009, 0.21099, 0.21188, 0.21277,
    0.21365, 0.21452, 0.21539, 0.21625, 0.21711, 0.21796, 0.21880, 0.21964,
    0.22047, 0.22130, 0.22212, 0.22293, 0.22373, 0.22453, 0.22532, 0.22610,
    0.22688, 0.22765, 0.22841, 0.22917, 0.22992, 0.23066, 0.23140, 0.23213,
    0.23285, 0.23356, 0.23427, 0.23496, 0.23566, 0.23634, 0.23702, 0.23769,
    0.23835, 0.23901, 0.23965, 0.24029, 0.24093, 0.24155, 0.24217, 0.24278,
    0.24339, 0.24399, 0.24458, 0.24516, 0.24574, 0.24630, 0.24687, 0.24742,
    0.24797, 0.24851, 0.24904, 0.24957, 0.25009, 0.25060, 0.25111, 0.25161,
    0.25210, 0.25258, 0.25306, 0.25353, 0.25400, 0.25446, 0.25491, 0.25535,
    0.25579, 0.25623, 0.25665, 0.25707, 0.25748, 0.25789, 0.25829, 0.25868,
    0.25907, 0.25945, 0.25983, 0.26020, 0.26056, 0.26092, 0.26127, 0.26162,
    0.26195, 0.26229, 0.26262, 0.26294, 0.26325, 0.26357, 0.26387, 0.26417,
    0.26446, 0.26475, 0.26504, 0.26531, 0.26559, 0.26585, 0.26611, 0.26637,
    0.26662, 0.26687, 0.26711, 0.26735, 0.26758, 0.26780, 0.26802, 0.26824,
    0.26845, 0.26866, 0.26886, 0.26906, 0.26925, 0.26944, 0.26962, 0.26980,
    0.26998, 0.27015, 0.27031, 0.27047, 0.27063, 0.27078, 0.27093, 0.27107,
    0.27121, 0.27135, 0.27148, 0.27161, 0.27174, 0.27186, 0.27197, 0.27208,
    0.27219, 0.27230, 0.27240, 0.27249, 0.27259, 0.27268, 0.27276, 0.27285,
    0.27293, 0.27300, 0.27308, 0.27314, 0.27321, 0.27327, 0.27333, 0.27339,
    0.27344, 0.27349, 0.27353, 0.27358, 0.27362, 0.27365, 0.27369, 0.27372,
    0.27375, 0.27377, 0.27379, 0.27381, 0.27383, 0.27384, 0.27385, 0.27386,
    0.27387, 0.27387, 0.27387, 0.27387, 0.27386, 0.27386, 0.27385, 0.27383,
    0.27382, 0.27380, 0.27378, 0.27376, 0.27373, 0.27371, 0.27368, 0.27365,
    0.27361, 0.27358, 0.27354, 0.27350, 0.27346, 0.27341, 0.27337, 0.27332,
    0.27327, 0.27321, 0.27316, 0.27310, 0.27305, 0.27298, 0.27292, 0.27286,
    0.27279, 0.27272, 0.27266, 0.27258, 0.27251, 0.27244, 0.27236, 0.27228,
    0.27220, 0.27212, 0.27204, 0.27195, 0.27187, 0.27178, 0.27169, 0.27160,
    0.27151, 0.27141, 0.27132, 0.27122, 0.27112, 0.27102, 0.27092, 0.27082,
    0.27072, 0.27061, 0.27051, 0.27040, 0.27029, 0.27018, 0.27007, 0.26996,
    0.26985, 0.26973, 0.26962, 0.26950, 0.26938, 0.26926, 0.26914, 0.26902,
    0.26890, 0.26877, 0.26865, 0.26852, 0.26840, 0.26827, 0.26814, 0.26801,
    0.26788, 0.26775, 0.26762, 0.26749, 0.26735, 0.26722, 0.26708, 0.26695,
    0.26681, 0.26667, 0.26653, 0.26639, 0.26625, 0.26611, 0.26597, 0.26582,
    0.26568, 0.26554, 0.26539, 0.26525, 0.26510, 0.26495, 0.26481, 0.26466,
    0.26451, 0.26436, 0.26421, 0.26406, 0.26391, 0.26375, 0.26360, 0.26345,
    0.26329, 0.26314, 0.26299, 0.26283, 0.26267, 0.26252, 0.26236, 0.26220,
    0.26205, 0.26189, 0.26173, 0.26157, 0.26141, 0.26125, 0.26109, 0.26093,
    0.26077, 0.26061, 0.26045, 0.26028, 0.26012, 0.25996, 0.25979, 0.25963,
    0.25947, 0.25930, 0.25914, 0.25897, 0.25881, 0.25864, 0.25847, 0.25831,
    0.25814, 0.25797, 0.25781, 0.25764, 0.25747, 0.25731, 0.25714, 0.25697,
    0.25680, 0.25663, 0.25646, 0.25629, 0.25612, 0.25595, 0.25578, 0.25561,
    0.25544, 0.25527, 0.25510, 0.25493, 0.25476, 0.25459, 0.25442, 0.25425,
    0.25408, 0.25391, 0.25373, 0.25356, 0.25339, 0.25322, 0.25305, 0.25288,
    0.25270, 0.25253, 0.25236, 0.25219, 0.25201, 0.25184, 0.25167, 0.25150,
    0.25132, 0.25115, 0.25098, 0.25080, 0.25063, 0.25046, 0.25029, 0.25011,
    0.24994, 0.24977, 0.24959, 0.24942, 0.24925, 0.24907, 0.24890, 0.24873,
    0.24856, 0.24838, 0.24821, 0.24804, 0.24786, 0.24769, 0.24752, 0.24734,
    0.24717, 0.24700, 0.24683, 0.24665, 0.24648, 0.24631, 0.24614, 0.24596,
    0.24579, 0.24562, 0.24545, 0.24527, 0.24510, 0.24493, 0.24476, 0.24459,
    0.24441]


def qwp_retardance(wavelength_nm):
    """Retardance of the quarter-wave plate (waves) at a wavelength (nm, 400-800)."""
    return float(np.interp(wavelength_nm, QWP_WAVELENGTHS, QWP_RETARDANCE))


# ---------------------------------------------------------------- loading


def load_run(folder, index):
    """
    The acquisitions of the run of acquisition index (same calibration "run" in X.json), as
    [{"index", "angle", "path", "calibration"}] in recording order (an angle recorded again:
    the last one).
    """
    def calibration(i):
        with open(os.path.join(folder, f"{i}.json")) as f:
            return json.load(f)["acquisition"].get("calibration", {})

    run = calibration(index).get("run")
    if run is None:
        raise ValueError(f"Acquisition {index} of {folder} is not an slm_phase recording.")
    items = []
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json") or not name[:-5].isdigit():
            continue
        i = int(name[:-5])
        c = calibration(i)
        if c.get("name") == "slm_phase" and c.get("run") == run:
            items.append({"index": i, "angle": c["qwp_angle_deg"],
                          "path": os.path.join(folder, f"{i}_0.tiff"), "calibration": c})
    last = {item["angle"]: item for item in sorted(items, key=lambda item: item["index"])}
    return sorted(last.values(), key=lambda item: item["index"])


def open_stack(path):
    """(frames, rows, cols) array of a recording, memory-mapped if possible."""
    try:
        return tifffile.memmap(path, mode="r")
    except Exception:  # not contiguous: read it all
        return tifffile.imread(path)


def binned(block, bin_size):
    """Sum of bin_size x bin_size pixels over the 2 last axes (truncated to whole bins)."""
    if bin_size == 1:
        return block.astype(np.float32)
    rows, cols = block.shape[-2] // bin_size, block.shape[-1] // bin_size
    block = block[..., :rows * bin_size, :cols * bin_size].astype(np.float32)
    return block.reshape(block.shape[:-2] + (rows, bin_size, cols, bin_size)).sum(axis=(-3, -1))


# ---------------------------------------------------------------- Stokes vectors


def fourier(intensity, angles_deg):
    """p0, p2, q2, p4, q4 of the intensity (angles first axis), as in polarimetre.py."""
    omega = np.radians(np.asarray(angles_deg, dtype=float))
    n = len(omega)
    def coefficient(weights):
        # einsum, not tensordot / @ (BLAS: crashes in setup_env)
        return np.einsum("a,a...->...", weights.astype(np.float32), intensity) * (2 / n)
    p0 = intensity.mean(axis=0)
    return (p0, coefficient(np.cos(2 * omega)), coefficient(np.sin(2 * omega)),
            coefficient(np.cos(4 * omega)), coefficient(np.sin(4 * omega)))


def plate_offset(p2, q2):
    """Zero of the plate d (rad, in ]-45, 45] deg) with p2 = q2 tan(2 d), least squares over all
    pixels and gray levels (as 0.5 arctan(p2 / q2) of polarimetre.py for one measurement)."""
    a, b, c = float((p2 * p2).sum()), float((p2 * q2).sum()), float((q2 * q2).sum())
    # minimum of sum (p2 cos 2d - q2 sin 2d)^2 over 2d: tan(4d) = 2b / (c - a)
    theta = 0.5 * np.arctan2(2 * b, c - a)
    best = min((theta, theta + np.pi / 2),
               key=lambda t: a * np.cos(t) ** 2 - 2 * b * np.cos(t) * np.sin(t)
               + c * np.sin(t) ** 2)
    best = (best + np.pi / 2) % np.pi - np.pi / 2  # 2d in [-90, 90[ deg
    return best / 2


def rotate_offset(p2, q2, p4, q4, offset):
    """Coefficients relative to the corrected plate angle omega + offset."""
    c2, s2, c4, s4 = np.cos(2 * offset), np.sin(2 * offset), np.cos(4 * offset), np.sin(4 * offset)
    return (p2 * c2 - q2 * s2, p2 * s2 + q2 * c2, p4 * c4 - q4 * s4, p4 * s4 + q4 * c4)


def stokes(p0, q2, p4, q4, retardance_waves):
    """S0, S1, S2, S3 (polarimetre.py, polarizer along y)."""
    phi = 2 * np.pi * retardance_waves
    s1 = 2 * p4 / (np.cos(phi) - 1)
    s2 = 2 * q4 / (np.cos(phi) - 1)
    s3 = -q2 / np.sin(phi)
    return p0 + s1 * (1 + np.cos(phi)) / 2, s1, s2, s3


# ---------------------------------------------------------------- circle on the Poincare sphere


def smallest_eigenvector(a00, a01, a02, a11, a12, a22):
    """Eigenvector of the smallest eigenvalue of symmetric 3 x 3 matrices (arrays of their 6
    elements), and that eigenvalue; explicit formulas (no np.linalg)."""
    q = (a00 + a11 + a22) / 3
    p1 = a01 ** 2 + a02 ** 2 + a12 ** 2
    p = np.sqrt(((a00 - q) ** 2 + (a11 - q) ** 2 + (a22 - q) ** 2 + 2 * p1) / 6)
    p = np.where(p > 0, p, np.nan)
    b00, b11, b22, b01, b02, b12 = (a00 - q) / p, (a11 - q) / p, (a22 - q) / p, a01 / p, \
        a02 / p, a12 / p
    det = b00 * (b11 * b22 - b12 ** 2) - b01 * (b01 * b22 - b12 * b02) + b02 * (b01 * b12 - b11 * b02)
    angle = np.arccos(np.clip(det / 2, -1, 1)) / 3
    smallest = q + 2 * p * np.cos(angle + 2 * np.pi / 3)
    # rows of A - smallest I; the eigenvector is the largest cross product of two rows
    r0 = np.stack([a00 - smallest, a01, a02])
    r1 = np.stack([a01, a11 - smallest, a12])
    r2 = np.stack([a02, a12, a22 - smallest])
    candidates = [np.cross(r0, r1, axis=0), np.cross(r0, r2, axis=0), np.cross(r1, r2, axis=0)]
    norms = np.stack([np.sqrt((c ** 2).sum(axis=0)) for c in candidates])
    pick = np.argmax(np.nan_to_num(norms, nan=-1), axis=0)
    vector = np.where(pick == 0, candidates[0], np.where(pick == 1, candidates[1], candidates[2]))
    norm = np.sqrt((vector ** 2).sum(axis=0))
    return vector / np.where(norm > 0, norm, np.nan), smallest


def normalized(s0, s1, s2, s3, part):
    """(S1, S2, S3) / S0 of the pixels part: (3, levels, pixels). S0 = mean over the gray levels
    of the pixel (the SLM does not change the intensity; S0 of one noisy frame can be ~0)."""
    mean = s0[:, part].mean(axis=0)
    norm = np.where(mean > 0, mean, np.nan)
    return np.stack([s1[:, part], s2[:, part], s3[:, part]]) / norm


def common_axis(s0, s1, s2, s3):
    """
    Axis of the SLM liquid crystal on the Poincare sphere, the same for all the pixels: normal
    of the plane of the circles, from the covariance of s(g) - mean of its pixel pooled over
    all pixels (photon noise only adds to all directions). Returns the unit axis (3,) and the
    rms distance of the points to their plane.
    """
    total, count = np.zeros(6), 0
    pairs = ((0, 0), (0, 1), (0, 2), (1, 1), (1, 2), (2, 2))
    for start in range(0, s0.shape[1], CHUNK):
        s = normalized(s0, s1, s2, s3, slice(start, start + CHUNK))
        d = s - s.mean(axis=1, keepdims=True)
        ok = np.isfinite(d).all(axis=(0, 1))
        total += [float((d[i][:, ok] * d[j][:, ok]).sum()) for i, j in pairs]
        count += d.shape[1] * int(ok.sum())
    cov = [np.array([t / count]) for t in total]
    axis, smallest = smallest_eigenvector(*cov)
    return axis[:, 0], float(np.sqrt(max(smallest[0], 0)))


def circle_angle(s, axis):
    """
    s: normalized Stokes vectors (3, levels, pixels), axis (3,): angle turned about the axis
    from the first level (levels, pixels; rad, wrapped in ]-pi, pi]), circle radius
    (modulation) and rms distance to the plane of each pixel.
    """
    a = axis[:, None, None]
    mean = s.mean(axis=1, keepdims=True)
    centre = a * (a * mean).sum(axis=0)  # projection of the mean on the axis
    r = s - centre
    height = (r * a).sum(axis=0)
    r = r - a * height  # in the plane
    u = r[:, 0] / np.sqrt((r[:, 0] ** 2).sum(axis=0))
    v = np.cross(axis[:, None], u, axis=0)
    angle = np.arctan2((r * v[:, None]).sum(axis=0), (r * u[:, None]).sum(axis=0))
    radius = np.sqrt((r ** 2).sum(axis=0)).mean(axis=0)
    plane_rms = np.sqrt(((height - height.mean(axis=0)) ** 2).mean(axis=0))
    return angle.astype(np.float32), radius, plane_rms


def wrap(angle):
    return (angle + np.pi) % (2 * np.pi) - np.pi


def unwrap_on_reference(angle):
    """
    Phase of each pixel from its wrapped angles (levels, pixels): the reference = the
    unwrapped circular mean over the pixels, and each pixel = reference + its difference to
    it wrapped in ]-pi, pi] (no unwrapping of noisy single pixels, which jumps by 2 pi).
    Oriented so that the phase grows. Returns (phase, reference).
    """
    mean = np.nanmean(np.exp(1j * angle), axis=1)
    reference = np.unwrap(np.angle(mean))
    reference -= reference[0]
    phase = reference[:, None] + wrap(angle - reference[:, None])
    phase -= phase[:1]
    if np.nanmean(np.diff(reference)) < 0:
        phase, reference = -phase, -reference
    return phase.astype(np.float32), reference


# ---------------------------------------------------------------- analysis


def lit_pixels(image, fraction=0.5):
    """Pixels of the BFP disks: smoothed image above median + fraction (99.5th pct - median);
    label of each disk (sorted left to right) and their number."""
    smooth = ndimage.gaussian_filter(image.astype(float), 2)
    median, high = np.percentile(smooth, [50, 99.5])
    lit = smooth > median + fraction * (high - median)
    lit = ndimage.binary_opening(lit, iterations=2)
    labels, n = ndimage.label(lit)
    sizes = ndimage.sum(lit, labels, range(1, n + 1))
    keep = [k + 1 for k in range(n) if sizes[k] > 0.05 * sizes.max()]
    centres = ndimage.center_of_mass(lit, labels, keep)
    order = [k for _, k in sorted(zip([c[1] for c in centres], keep))]
    disks = np.zeros_like(labels)
    for number, k in enumerate(order, start=1):
        disks[labels == k] = number
    return disks, len(order)


def analyse(items, retardance_waves, bin_size=1, offset_deg=None, progress=None,
            channels=DEFAULT_CHANNELS):
    """
    items: load_run; returns (result dict, per-pixel arrays dict).
    offset_deg: fixed plate zero instead of the fit. progress(text): optional.
    channels: "right" (disks of the right half of the Orca only) or "both" (CHANNELS).
    """
    if channels not in CHANNELS:
        raise ValueError(f"channels: {channels}, expected one of {list(CHANNELS)}")
    say = progress or (lambda text: None)
    angles = [item["angle"] for item in items]
    if sorted(angles) != QWP_ANGLES:
        raise ValueError(f"Plate angles recorded: {sorted(angles)}, needed: {QWP_ANGLES}.")
    levels = recorded_levels(items[0]["calibration"])
    for item in items:
        if recorded_levels(item["calibration"]) != levels:
            raise ValueError(f"Acquisition {item['index']}: not the same gray levels as "
                             f"acquisition {items[0]['index']} (gray step changed?).")
    stacks = [open_stack(item["path"]) for item in items]
    n_levels = len(levels)
    for item, stack in zip(items, stacks):
        if stack.shape[0] != n_levels:
            raise ValueError(f"Acquisition {item['index']}: {stack.shape[0]} frames, "
                             f"{n_levels} expected (stopped?).")

    say("Finding the BFP disks...")
    reference = sum(binned(stack[0], bin_size) for stack in stacks) / len(stacks)
    all_disks, n_all = lit_pixels(reference)  # whole image: a disk may cross the middle
    # dark level (camera offset + stray light, per binned pixel): median of the image away
    # from the disks; subtracted, else it acts as unpolarized light
    away = ~ndimage.binary_dilation(all_disks > 0, iterations=max(2, 20 // bin_size))
    dark = float(np.median(reference[away]))
    keep = list(range(1, n_all + 1))
    if channels == "right":  # the disks whose centre is in the right half of the Orca
        middle = reference.shape[1] / 2
        keep = [k for k in keep if np.nonzero(all_disks == k)[1].mean() >= middle]
    disks = np.zeros_like(all_disks)
    for number, k in enumerate(keep, start=1):
        disks[all_disks == k] = number
    n_disks = len(keep)
    if n_disks == 0:
        raise ValueError("No lit disk found on the Orca"
                         + (" (right half)." if channels == "right" else "."))
    rows, cols = np.nonzero(disks)
    pixel_disk = disks[rows, cols]

    intensity = np.empty((len(stacks), n_levels, len(rows)), dtype=np.float32)
    height = reference.shape[0]
    for start in range(0, height, ROW_BLOCK):
        in_block = (rows >= start) & (rows < start + ROW_BLOCK)
        if not in_block.any():
            continue
        say(f"Reading the recordings: Orca rows {start * bin_size} / {height * bin_size}")
        r, c = rows[in_block] - start, cols[in_block]
        for k, stack in enumerate(stacks):
            block = binned(stack[:, start * bin_size:(start + ROW_BLOCK) * bin_size], bin_size)
            intensity[k][:, in_block] = block[:, r, c] - dark

    say("Polarization of each pixel...")
    p0, p2, q2, p4, q4 = fourier(intensity, angles)
    offset = np.radians(offset_deg) if offset_deg is not None else plate_offset(p2, q2)
    p2, q2, p4, q4 = rotate_offset(p2, q2, p4, q4, offset)
    s0, s1, s2, s3 = stokes(p0, q2, p4, q4, retardance_waves)
    del intensity, p2, p4

    say("Axis of the SLM on the Poincare sphere...")
    axis, axis_plane_rms = common_axis(s0, s1, s2, s3)
    angle = np.empty((n_levels, len(rows)), dtype=np.float32)
    modulation = np.empty(len(rows), dtype=np.float32)
    plane_rms = np.empty(len(rows), dtype=np.float32)
    dop = np.empty(len(rows), dtype=np.float32)
    for start in range(0, len(rows), CHUNK):
        say(f"Phase of each pixel: {start} / {len(rows)}")
        part = slice(start, start + CHUNK)
        s = normalized(s0, s1, s2, s3, part)
        angle[:, part], modulation[part], plane_rms[part] = circle_angle(s, axis)
        dop[part] = np.sqrt((s ** 2).sum(axis=0)).mean(axis=0)
    phase = np.empty_like(angle)
    for number in range(1, n_disks + 1):  # each disk on its own reference curve
        mine = pixel_disk == number
        phase[:, mine] = unwrap_on_reference(angle[:, mine])[0]
    del angle

    say("Summary...")
    result = {"n_pixels": int(len(rows)), "bin": bin_size, "levels": levels, "channels": channels,
              "plate_offset_deg": float(np.degrees(offset)),
              "plate_offset_fitted": offset_deg is None,
              "dark_per_orca_pixel": dark / bin_size ** 2,
              "slm_axis_poincare": axis.round(4).tolist(),
              "slm_axis_plane_rms": axis_plane_rms, "disks": []}
    for number in range(1, n_disks + 1):
        mine = pixel_disk == number
        curve = np.nanpercentile(phase[:, mine], [25, 50, 75], axis=1)
        result["disks"].append({
            "disk": number, "n_pixels": int(mine.sum()),
            "center_orca_xy": [float(cols[mine].mean() * bin_size),
                               float(rows[mine].mean() * bin_size)],
            "phase_median_rad": curve[1].round(4).tolist(),
            "phase_q25_rad": curve[0].round(4).tolist(),
            "phase_q75_rad": curve[2].round(4).tolist(),
            "phase_at_255_rad": float(curve[1][-1]),
            "gray_of_2pi": gray_of(curve[1], 2 * np.pi, levels),
            "modulation_median": float(np.nanmedian(modulation[mine])),
            "polarization_degree_median": float(np.nanmedian(dop[mine])),
            "plane_rms_median": float(np.nanmedian(plane_rms[mine]))})
    pixels = {"rows": (rows * bin_size).astype(np.int32), "cols": (cols * bin_size).astype(np.int32),
              "disk": pixel_disk.astype(np.int8), "phase": phase, "axis": axis.astype(np.float32),
              "modulation": modulation, "plane_rms": plane_rms, "polarization_degree": dop,
              "mean_intensity": s0.mean(axis=0).astype(np.float32), "bin": bin_size,
              "levels": np.array(levels, dtype=np.int16)}
    return result, pixels


def recorded_levels(calibration):
    """Gray levels of a recording ("levels" of its X.json; older recordings: 0, 1... 255)."""
    levels = calibration.get("levels")
    return [int(g) for g in levels] if isinstance(levels, list) else list(range(256))


def gray_of(curve, value, levels):
    """First gray level (interpolated) where the phase curve (one value per level of levels)
    reaches value, None if never."""
    above = np.nonzero(np.asarray(curve) >= value)[0]
    if len(above) == 0:
        return None
    k = above[0]
    if k == 0:
        return float(levels[0])
    return float(levels[k - 1] + (value - curve[k - 1]) / (curve[k] - curve[k - 1])
                 * (levels[k] - levels[k - 1]))


# ---------------------------------------------------------------- figure


def colormap(values, low, high):
    """RGB (uint8) of values with a blue - green - yellow scale; NaN black."""
    anchors = np.array([[48, 18, 59], [40, 120, 220], [30, 190, 160], [160, 220, 60],
                        [250, 230, 40]], dtype=float)
    t = np.clip((values - low) / (high - low if high > low else 1), 0, 1) * (len(anchors) - 1)
    k = np.minimum(np.floor(np.nan_to_num(t)).astype(int), len(anchors) - 2)
    f = (np.nan_to_num(t) - k)[..., None]
    rgb = anchors[k] * (1 - f) + anchors[k + 1] * f
    rgb[np.isnan(values)] = 0
    return rgb.astype(np.uint8)


def save_figure(result, pixels, path):
    """Phase curves (median and 25-75 % of each disk) and 3 maps."""
    from PIL import Image, ImageDraw
    size = 400
    picture = Image.new("RGB", (2 * size, 2 * size), "white")
    draw = ImageDraw.Draw(picture)
    # curves
    left, top, width, height = 50, 20, size - 70, size - 70
    top_phase = max([d["phase_q75_rad"][-1] for d in result["disks"]] + [2 * np.pi]) * 1.05
    draw.rectangle([left, top, left + width, top + height], outline="black")
    for k in range(1, int(top_phase / np.pi) + 1):
        y = top + height - k * np.pi / top_phase * height
        draw.line([left, y, left + width, y], fill=(200, 200, 200) if k % 2 else (120, 120, 120))
        draw.text((5, y - 6), f"{k}pi", fill="black")
    for g in (0, 64, 128, 192, 255):
        draw.text((left + g / 255 * width - 8, top + height + 5), str(g), fill="black")
    draw.text((left + width / 2 - 30, top + height + 25), "gray level", fill="black")
    colors = [(211, 47, 47), (25, 118, 210), (56, 142, 60), (120, 120, 120)]
    levels = result["levels"]
    for d, color in zip(result["disks"], colors):
        def points(curve):
            return [(left + g / 255 * width, top + height - v / top_phase * height)
                    for g, v in zip(levels, curve)]
        band = points(d["phase_q75_rad"]) + points(d["phase_q25_rad"])[::-1]
        draw.polygon(band, fill=tuple(int(0.7 * 255 + 0.3 * c) for c in color))
        draw.line(points(d["phase_median_rad"]), fill=color, width=2)
        draw.text((left + 8, top + 8 + 14 * (d["disk"] - 1)),
                  f"disk {d['disk']}: 2pi at gray "
                  f"{'-' if d['gray_of_2pi'] is None else round(d['gray_of_2pi'], 1)}", fill=color)
    # maps (binned Orca pixels)
    rows, cols = pixels["rows"] // pixels["bin"], pixels["cols"] // pixels["bin"]
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    gray2pi = np.full(len(rows), np.nan)
    reached = pixels["phase"][-1] >= 2 * np.pi
    if reached.any():
        above = np.argmax(pixels["phase"][:, reached] >= 2 * np.pi, axis=0)
        gray2pi[reached] = pixels["levels"][above]
    maps = [("phase at 255 (rad)", pixels["phase"][-1]), ("gray level of 2 pi", gray2pi),
            ("modulation", pixels["modulation"])]
    for (title, values), (x, y) in zip(maps, ((size, 0), (0, size), (size, size))):
        image = np.full((r1 - r0, c1 - c0), np.nan)
        image[rows - r0, cols - c0] = values
        finite = values[np.isfinite(values)]
        low, high = (np.percentile(finite, [2, 98]) if len(finite) else (0, 1))
        tile = Image.fromarray(colormap(image, low, high))
        tile.thumbnail((size - 20, size - 40))
        picture.paste(tile, (x + 10, y + 25))
        draw.text((x + 10, y + 5), f"{title}: {low:.2f} - {high:.2f}", fill="black")
    picture.save(path)


# ---------------------------------------------------------------- run


def run(folder, index, bin_size=1, offset_deg=None, progress=None, channels=DEFAULT_CHANNELS):
    """Analyse the run of acquisition index; writes the .json, .npz and .png next to it.
    Returns (json path, summary)."""
    items = load_run(folder, index)
    settings = items[0]["calibration"]
    result, pixels = analyse(items, settings["qwp_retardance_waves"], bin_size, offset_deg,
                             progress, channels)
    now = datetime.now()
    first, last = items[0]["index"], items[-1]["index"]
    name = os.path.join(folder, f"{first}-{last}_slm_phase_{now:%Y_%m_%d_%H%M%S}")
    summary = {
        "type": "slm_phase_calibration",
        "date": now.isoformat(timespec="seconds"),
        "recordings": [{"index": item["index"], "qwp_angle_deg": item["angle"]}
                       for item in items],
        "folder": folder,
        "light_path": settings.get("light_path"),
        "wavelength_nm": settings.get("wavelength_nm"),
        "qwp_retardance_waves": settings.get("qwp_retardance_waves"),
        "per_pixel_file": os.path.basename(name + ".npz"),
        **result,
        "notes": "Phase (rad) applied by the SLM for each gray level, relative to gray level 0, "
                 "per Orca pixel in the .npz (rows, cols in Orca px of the full frame). Disks "
                 "sorted left to right on the Orca.",
    }
    np.savez_compressed(name + ".npz", **pixels)
    with open(name + ".json", "w") as f:
        json.dump(summary, f, indent=4)
    save_figure(result, pixels, name + ".png")
    return name + ".json", summary


if __name__ == "__main__":
    folder, index = sys.argv[1], int(sys.argv[2])
    bin_size = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    channels = sys.argv[4] if len(sys.argv) > 4 else DEFAULT_CHANNELS
    path, summary = run(folder, index, bin_size, progress=print, channels=channels)
    for disk in summary["disks"]:
        print(f"disk {disk['disk']}: phase at 255 = {disk['phase_at_255_rad']:.3f} rad, "
              f"2 pi at gray {disk['gray_of_2pi']}, modulation {disk['modulation_median']:.2f}")
    print("written:", path)
