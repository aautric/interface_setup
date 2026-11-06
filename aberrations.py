"""
DRAFT (2026-11-04) - aberrations calibration (calibration.py, "Aberrations"): field-dependent
aberrations of the left path (Prime, 2 channels) from z stacks of TetraSpeck beads (200 nm), their
common part corrected by a phase mask on the SLM, the zero-mean field-dependent rest stored.
The core (the PSF model and the fit) is to be discussed: here the plan, the parts that do not
depend on it, and a model-free first look.

PLAN
 1. Z stacks of beads WITHOUT phase mask (flat SLM): several fields (stage moved between them) so
    that the beads cover the whole field of each channel. Piezo z stack (camera Saving tab or this
    window): reference at the focus, step 0.1 um, +-1.5 um, a few frames per plane.
 2. Each stack: beads found in each channel (channels_geometry: channel boxes, isolation), a
    patch per bead and plane. First look (here, no model): widths sigma_x / sigma_y vs z, best focus
    of each channel, astigmatism (focus of x - focus of y), asymmetry above / below the focus
    (spherical), all vs the bead position in the field.
 3. FIT (to come, to be discussed): each bead fitted on its whole stack with a pupil model
    (vectorial, as SGD_aberrations_claude.py of the analysis PC: Zernike Noll 5-11 + effective NA +
    Gaussian blur (bead R / sqrt 5 = 45 nm + instrument) + drift / registration vs z + n2 1.47,
    glycerol). Backend: pytorch on this PC (no WSL here), or jax in WSL (analysis PC code). On the
    analysis PC (2026-11-03): 40 beads, fit errors ~1 m-lambda, bead-to-bead scatter 3-8, field
    gradients ~5-9 m-lambda per 10 um (Z6, Z8, Z11, Z7), see its suivi 2026_11_03\aberrations_field.
 4. The same with phase masks on the SLM (double helix, Airy, tri-spot, bisected pupil...;
    Desktop\SLM_masks): a known pupil phase added to the unknown aberrations makes the fit
    better conditioned (phase diversity: the sign of the even modes, the separation of NA / blur /
    spherical). All the stacks of a bead position fitted together (the same aberrations, a different
    known mask per stack).
 5. Field model of each Zernike (and NA, blur) per channel: polynomial in the field position
    (constant / linear / quadratic, chosen by leave-one-out as channels_geometry), weighted by
    the fit errors + a bead-to-bead scatter. Common part = mean over the field; field part = the
    rest, zero mean over the field.
 6. Correction: the common part (per channel = per SLM half) sent as a phase mask (minus the
    aberration, in the pupil of the SLM half: centre and radius of align_BFP, wavelength of the
    emission), added to any mask. Before: the sign and the orientation of the pupil SLM -> camera
    checked with a known mode (e.g. +50 m-lambda of Z5, Z6, Z7, Z8 one after the other, measured
    by the same analysis).
 7. Check: z stacks with the correction mask; the common part should be ~0 (within the errors)
    and the field part unchanged.
 8. Calibration file: wavelength, correction applied (common Zernike per channel, SLM half, BFP
    centre / radius), the zero-mean field model of each mode per channel (polynomial coefficients
    in the channel frame of channels_geometry), the check, the recordings used.

CONVENTIONS (to keep the same as the analysis PC)
- Zernike Noll index j, rms normalised (Z = sqrt(n+1) R (m = 0) or sqrt(2(n+1)) R cos/sin m theta),
  coefficients in milli-lambda rms of wavefront; wavefront phase = 2 pi c / 1000 Z (rad).
- Field position: in the frame of each channel (channels_geometry: top x right y up, bottom
  x right y down), in um from the channel centre (PIXEL_NM).

Only numpy, scipy.ndimage and tifffile (np.linalg and matrix products crash setup_env).
"""

import glob
import json
import os
from datetime import datetime

import numpy as np
import tifffile
from scipy import ndimage

import saving

NAME = "aberrations"
PIXEL_NM = 68.2          # Prime, nm per pixel at the sample (channels_geometry)
PATCH_HALF = 12          # px, half size of the bead patches (defocused PSF up to ~+-1.5 um)
ISOLATION = 25           # px, beads closer to another one are left out (defocused rings overlap)
NOLL_MODES = list(range(4, 12))  # Z4 defocus (for the mask / checks only) ... Z11 spherical
Z_STACK_DEFAULTS = {"step_um": 0.1, "down": 15, "up": 15, "frames": 5}
SLM_SHAPE = (1200, 1920)
NOMINAL_CENTERS = ((600, 480), (600, 1440))  # row, col of the BFP centre of each SLM half


# ---------------------------------------------------------------- Zernike (Noll)


def noll_to_nm(j):
    """(n, m) of the Noll index j (1 = piston, 4 = defocus, 5 / 6 astigmatism 45 / 0 deg,
    7 / 8 coma y / x, 11 primary spherical); m > 0: cos m theta, m < 0: sin |m| theta."""
    n, rest = 0, j - 1
    while rest > n:
        n += 1
        rest -= n
    m = (-1) ** j * ((n % 2) + 2 * int((rest + ((n + 1) % 2)) / 2))
    return n, m


def zernike(j, rho, theta):
    """Rms-normalised Zernike Noll j on the unit disk (0 outside)."""
    from math import factorial
    n, m = noll_to_nm(j)
    radial = np.zeros_like(rho, dtype=float)
    for s in range((n - abs(m)) // 2 + 1):
        c = ((-1) ** s * factorial(n - s) /
             (factorial(s) * factorial((n + abs(m)) // 2 - s) * factorial((n - abs(m)) // 2 - s)))
        radial = radial + c * rho ** (n - 2 * s)
    if m == 0:
        z = np.sqrt(n + 1) * radial
    elif m > 0:
        z = np.sqrt(2 * (n + 1)) * radial * np.cos(m * theta)
    else:
        z = np.sqrt(2 * (n + 1)) * radial * np.sin(-m * theta)
    return np.where(rho <= 1, z, 0.0)


# ---------------------------------------------------------------- correction mask on the SLM


def pupil_coordinates(center_row_col, radius_px, shape=SLM_SHAPE):
    """rho (1 at the BFP edge), theta of each SLM pixel about a BFP centre. theta from +x (columns),
    counterclockwise with rows going down (to check against the camera: step 6 of the plan)."""
    rows, cols = np.mgrid[0:shape[0], 0:shape[1]]
    dy, dx = -(rows - center_row_col[0]), cols - center_row_col[1]
    return np.hypot(dx, dy) / radius_px, np.arctan2(dy, dx)


def correction_phase(coefficients, radius_px, centers=NOMINAL_CENTERS, shape=SLM_SHAPE):
    """
    Phase (rad, not wrapped) that cancels the aberrations: coefficients {half: {noll: m-lambda}};
    in each SLM half, minus the wavefront about the NOMINAL centre of that half (the mask is then
    moved onto the measured BFP by align_BFP, like the other masks), radius from align_BFP.
    Halves without coefficients: 0.
    """
    phase = np.zeros(shape)
    width = shape[1] // len(centers)
    for half, modes in coefficients.items():
        rho, theta = pupil_coordinates(centers[half], radius_px, shape)
        part = np.zeros(shape)
        for j, c in modes.items():
            part -= 2 * np.pi * c / 1000 * zernike(int(j), rho, theta)
        phase[:, half * width:(half + 1) * width] = part[:, half * width:(half + 1) * width]
    return phase


def add_to_mask(mask_gray, phase, gray_2pi=255):
    """Gray mask (255 = 2 pi nominal) + a phase (rad), wrapped."""
    total = np.asarray(mask_gray, float) / gray_2pi * 2 * np.pi + phase
    gray = np.round((total % (2 * np.pi)) / (2 * np.pi) * gray_2pi)
    return np.where(gray > gray_2pi, 0, gray).astype(np.uint8)


def bfp_radius(root=None):
    """(BFP radius in SLM px, file) of the newest align_BFP result (mean of its halves)."""
    paths = sorted(glob.glob(os.path.join(root or saving.CALIBRATION_ROOT, "align_BFP", "*",
                                          "*_bfp_calibration_*.json")),
                   key=lambda p: os.path.splitext(os.path.basename(p))[0][-17:], reverse=True)
    for path in paths:
        with open(path) as f:
            channels = json.load(f).get("channels") or {}
        radii = [c["bfp_radius_slm_px"] for c in channels.values() if "bfp_radius_slm_px" in c]
        if radii:
            return float(np.mean(radii)), path
    return None, None


# ---------------------------------------------------------------- recordings


def load_z_stack(folder, index):
    """
    A z stack acquisition (piezo, "z_stack" in X.json): (stack (planes, rows, cols) mean of the
    frames of each plane, z of each plane (um, measured), metadata).
    """
    with open(os.path.join(folder, f"{index}.json")) as f:
        metadata = json.load(f)
    z_stack = metadata["acquisition"].get("z_stack")
    if not z_stack:
        raise ValueError(f"Acquisition {index} of {folder} is not a z stack.")
    parts = [tifffile.imread(os.path.join(folder, name))
             for name in metadata["acquisition"]["files"]]
    frames = np.concatenate([p.reshape(-1, *p.shape[-2:]) for p in parts]).astype(np.float32)
    per_plane = z_stack["frames_per_plane"]
    n_planes = len(frames) // per_plane
    stack = frames[:n_planes * per_plane].reshape(n_planes, per_plane, *frames.shape[1:]).mean(1)
    z = np.array(z_stack["positions_um"][:n_planes], dtype=float)
    frames_csv = os.path.join(folder, f"{index}_frames.csv")
    if os.path.isfile(frames_csv):  # measured positions, one per plane
        table = np.genfromtxt(frames_csv, delimiter=",", names=True)
        if "z_um" in table.dtype.names:
            z = np.array([table["z_um"][table["z_plane"] == k][0] for k in range(n_planes)])
    return stack, z, metadata


def newest_channels_geometry(root=None):
    paths = sorted(glob.glob(os.path.join(root or saving.CALIBRATION_ROOT, "channels_geometry",
                                          "*", "*_channels_geometry_2*.json")),
                   key=lambda p: os.path.splitext(os.path.basename(p))[0][-17:], reverse=True)
    if not paths:
        raise ValueError("No channels_geometry calibration: run it first (channel boxes).")
    with open(paths[0]) as f:
        return json.load(f), paths[0]


# ---------------------------------------------------------------- first look (no model)


def find_stack_beads(stack, box, isolation=ISOLATION):
    """Beads of a channel box (col0, col1, row0, row1) on the maximum projection of the stack:
    [(x, y)] image px, isolated by `isolation` px and a patch away from the edges."""
    import channels_geometry
    projection = stack.max(axis=0)
    found = channels_geometry.find_beads(projection, box)
    xy = np.array([(b[0], b[1]) for b in found]) if found else np.zeros((0, 2))
    keep = []
    c0, c1, r0, r1 = box
    for k, (x, y) in enumerate(xy):
        d = np.hypot(*(xy - (x, y)).T)
        d[k] = np.inf
        if d.min() >= isolation and c0 + PATCH_HALF <= x <= c1 - PATCH_HALF \
                and r0 + PATCH_HALF <= y <= r1 - PATCH_HALF:
            keep.append((float(x), float(y)))
    return keep


def moments(patch):
    """Background (median of the border), amplitude, sigma_x, sigma_y (px) of a patch from the
    second moments of the pixels above 10 % of the peak."""
    border = np.concatenate([patch[0], patch[-1], patch[:, 0], patch[:, -1]])
    signal = patch - np.median(border)
    peak = signal.max()
    if peak <= 0:
        return np.nan, np.nan, np.nan
    w = np.where(signal > 0.1 * peak, signal, 0)
    rows, cols = np.mgrid[0:patch.shape[0], 0:patch.shape[1]]
    total = w.sum()
    cx, cy = (w * cols).sum() / total, (w * rows).sum() / total
    sx = np.sqrt((w * (cols - cx) ** 2).sum() / total)
    sy = np.sqrt((w * (rows - cy) ** 2).sum() / total)
    return float(peak), float(sx), float(sy)


def focus_of(z, width):
    """z of the smallest width (parabola through the minimum and its neighbours) and that width."""
    width = np.asarray(width, float)
    if not np.isfinite(width).any():
        return np.nan, np.nan
    k = int(np.nanargmin(width))
    if 0 < k < len(z) - 1 and np.isfinite(width[k - 1:k + 2]).all():
        a, b, c = width[k - 1:k + 2]
        denominator = a - 2 * b + c
        if denominator > 0:
            shift = 0.5 * (a - c) / denominator
            return float(z[k] + shift * (z[k + 1] - z[k])), float(b - 0.25 * (a - c) * shift)
    return float(z[k]), float(width[k])


def first_look(stack, z, geometry):
    """
    Per channel and bead (no model): sigma_x, sigma_y vs z; focus of x and y, astigmatism
    (z focus x - z focus y), sigma at focus, asymmetry (mean width 0.5-1 um above the focus /
    the same below - 1: spherical aberration and defocus of the pupil); position in the channel
    frame (um from the channel centre). Returns {channel: [bead dict]}.
    """
    import channels_geometry
    channels = geometry["channels"]
    out = {}
    for name in channels_geometry.CHANNELS:
        box = (*channels[name]["cols"], *channels[name]["rows"])
        beads = []
        for x, y in find_stack_beads(stack, box):
            r, c = int(round(y)), int(round(x))
            widths = np.array([moments(plane[r - PATCH_HALF:r + PATCH_HALF + 1,
                                             c - PATCH_HALF:c + PATCH_HALF + 1])
                               for plane in stack])
            zx, sx = focus_of(z, widths[:, 1])
            zy, sy = focus_of(z, widths[:, 2])
            mean_width = widths[:, 1:].mean(axis=1)
            zf, sf = focus_of(z, mean_width)
            above = mean_width[(z > zf + 0.5) & (z < zf + 1.0)]
            below = mean_width[(z < zf - 0.5) & (z > zf - 1.0)]
            local = channels_geometry.to_local(name, np.array([[x, y]]), channels)[0]
            beads.append({
                "x_px": x, "y_px": y,
                "field_um": [float(local[0] * PIXEL_NM / 1000), float(local[1] * PIXEL_NM / 1000)],
                "peak": float(np.nanmax(widths[:, 0])),
                "focus_um": zf, "sigma_focus_px": sf,
                "focus_x_um": zx, "focus_y_um": zy, "astigmatism_um": zx - zy,
                "sigma_x_focus_px": sx, "sigma_y_focus_px": sy,
                "asymmetry": float(above.mean() / below.mean() - 1)
                if len(above) and len(below) else np.nan,
                "sigma_x_px": widths[:, 1].round(3).tolist(),
                "sigma_y_px": widths[:, 2].round(3).tolist()})
        out[name] = beads
    return out


# ---------------------------------------------------------------- field model


def field_model(xy_um, values, order=1, weights=None):
    """
    Polynomial of the field position (order 0, 1, 2) fitted to the values of the beads
    (weighted least squares). Returns {"order", "terms", "coefficients", "common" (mean of the
    model over the beads), "rms_residual"}; the field part = model - common (zero mean).
    """
    import bfp_analysis
    xy_um, values = np.asarray(xy_um, float), np.asarray(values, float)
    ok = np.isfinite(values) & np.isfinite(xy_um).all(axis=1)
    x, y, v = xy_um[ok, 0], xy_um[ok, 1], values[ok]
    w = np.ones_like(v) if weights is None else np.sqrt(np.asarray(weights, float)[ok])
    terms = [("1", np.ones_like(x))]
    if order >= 1:
        terms += [("x", x), ("y", y)]
    if order >= 2:
        terms += [("x2", x * x), ("xy", x * y), ("y2", y * y)]
    if len(v) < len(terms):
        raise ValueError(f"{len(v)} beads for a field model of {len(terms)} terms.")
    p, _ = bfp_analysis.least_squares([t * w for _, t in terms], v * w)
    model = np.einsum("i,in->n", p, np.stack([t for _, t in terms]))
    return {"order": order, "terms": [name for name, _ in terms],
            "coefficients": p.round(6).tolist(), "common": float(model.mean()),
            "rms_residual": float(np.sqrt(((v - model) ** 2).mean()))}


# ---------------------------------------------------------------- fit (to come)


def fit_bead(patches, z, masks_phase, settings):
    """
    TO COME (core, to be discussed). Fit of one bead on all its stacks (one per known SLM mask):
    patches (stacks, planes, rows, cols) per channel, z (um), masks_phase the pupil phase added by
    each mask (in the pupil of the channel, from the SLM mask, align_BFP and slm_phase), settings
    (NA, n1 / n2, wavelength, pixel, blur...). Returns the Zernike (m-lambda) with their errors,
    NA, blur, focus, position, photons, background. Backend: pytorch here, or jax in WSL.
    """
    raise NotImplementedError("The PSF fit of the aberrations calibration is not written yet "
                              "(draft: see the plan at the top of aberrations.py).")


# ---------------------------------------------------------------- results


def first_look_summary(result):
    """Text lines of a first look: per channel the medians and the field gradients."""
    lines = []
    for name, beads in result.items():
        if not beads:
            lines.append(f"{name}: no isolated bead found")
            continue
        def med(key):
            return float(np.nanmedian([b[key] for b in beads]))
        line = (f"{name}: {len(beads)} beads; focus {med('focus_um'):.3f} um, sigma at focus "
                f"{med('sigma_focus_px'):.2f} px ({med('sigma_focus_px') * PIXEL_NM:.0f} nm), "
                f"astigmatism (focus x - y) {1000 * med('astigmatism_um'):+.0f} nm, asymmetry "
                f"above / below {100 * med('asymmetry'):+.0f} %")
        if len(beads) >= 4:
            xy = [b["field_um"] for b in beads]
            for key, label, scale, unit in (("focus_um", "focus", 1000, "nm"),
                                            ("astigmatism_um", "astigmatism", 1000, "nm")):
                model = field_model(xy, [b[key] for b in beads], order=1)
                gx, gy = model["coefficients"][1:3]
                line += (f"; {label} gradient x {scale * gx * 10:+.0f}, y {scale * gy * 10:+.0f} "
                         f"{unit} per 10 um")
        lines.append(line)
    return lines


def run_first_look(recordings, geometry_path=None):
    """
    First look of the z stacks recordings [(folder, index)]: writes
    <first index>_aberrations_first_look_<date_time>.json next to the first one.
    Returns (path, {recording: result}, summary lines).
    """
    if geometry_path:
        with open(geometry_path) as f:
            geometry = json.load(f)
    else:
        geometry, geometry_path = newest_channels_geometry()
    results, lines = [], []
    for folder, index in recordings:
        stack, z, metadata = load_z_stack(folder, index)
        result = first_look(stack, z, geometry)
        mask = (metadata["acquisition"].get("calibration") or {}).get("slm_mask", "?")
        results.append({"folder": folder, "index": index, "slm_mask": mask,
                        "z_um": z.round(4).tolist(), "channels": result})
        lines.append(f"<b>Acquisition {index}</b> (mask {mask}):")
        lines += first_look_summary(result)
    now = datetime.now()
    folder, index = recordings[0]
    path = os.path.join(folder, f"{index}_{NAME}_first_look_{now:%Y_%m_%d_%H%M%S}.json")
    with open(path, "w") as f:
        json.dump({"type": "aberrations_first_look", "date": now.isoformat(timespec="seconds"),
                   "channels_geometry": geometry_path, "recordings": results,
                   "notes": "DRAFT, no PSF model: widths from second moments (pixels above 10 % "
                            "of the peak); field in um in the channel frame."}, f, indent=1)
    return path, results, lines
