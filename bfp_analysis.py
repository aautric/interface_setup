"""
Analysis of the align_BFP recordings (calibration.py): position of the back focal plane (BFP)
of each channel on the SLM, from the Orca images of the vortex masks.

The recording: the vortex at the nominal centre of each SLM half, the same shifted by shift px
along x (SLM columns), then along y (SLM rows), and (since 2026-10-29) the vortex in SLM half 0
only (the other half uniform), which tells which disk is lit through which half. On the Orca,
each BFP is a bright disk (its edge = the pupil); inside, the vortex gives a pinwheel of
m = total_phase / 2 pi contrast periods around the vortex centre.

For each disk:
    1. edge: radial profiles from a rough centre; edge = crossing of the level halfway between
       the local inside (just inside the edge) and the local outside (sub-pixel); robust ELLIPSE
       fit (the disk is slightly elliptical on the Orca: the magnification differs a little
       along x and y) -> centre of the disk on the Orca. The fit is repeated at 30 % and 70 % of
       the step: the spread is a systematic error (soft edge, ringing);
    2. vortex centre on each frame: the model background (quadratic) + a cos(m t) + b sin(m t),
       t = angle around (x0, y0), fitted inside the disk; (x0, y0) = the best fit (grid search,
       then a 2D parabola: sub-pixel position and its uncertainty);
    3. SLM -> Orca 2x2 matrix from the vortex moves of the shifted frames; the disk centre is
       brought onto the SLM:
           BFP centre (SLM) = vortex centre (SLM, known) + inverse(matrix) (disk - vortex) (Orca)

Only numpy and scipy.ndimage: matrix products and np.linalg crash Python in setup_env (broken
BLAS), small systems are solved by hand (solve).

Command line: python bfp_analysis.py <folder> [index] [half of each disk, e.g. 1 0]
(e.g. D:\\BUMBLEBEE_calibration\\align_BFP\\2026_10_29 0); writes <index>_bfp_calibration_<YYYY_MM_DD_HHMMSS>.json
and .png next to the recording (time of the analysis: a new analysis never overwrites one).
"""

import json
import os
import sys
from datetime import datetime

import numpy as np
from scipy import ndimage

EDGE_ANGLES = 720           # radial profiles for the disk edge
EDGE_LEVELS = (0.3, 0.7)    # edge levels of the systematic check (0.5 is the result)
VORTEX_BIN = 4              # binning of the images for the coarse vortex search
VORTEX_INNER = 15           # Orca px around the vortex centre left out (angle undefined)
VORTEX_DISK_FRACTION = 0.9  # part of the disk radius used for the vortex fit


# ---------------------------------------------------------------- small linear algebra


def solve(a, b):
    """Solve a x = b (small square system) by Gauss elimination with pivoting."""
    a = [list(map(float, row)) for row in a]
    b = list(map(float, b))
    n = len(b)
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-300:
            raise ValueError("singular system")
        a[col], a[pivot] = a[pivot], a[col]
        b[col], b[pivot] = b[pivot], b[col]
        for row in range(col + 1, n):
            factor = a[row][col] / a[col][col]
            for k in range(col, n):
                a[row][k] -= factor * a[col][k]
            b[row] -= factor * b[col]
    x = [0.0] * n
    for row in range(n - 1, -1, -1):
        x[row] = (b[row] - sum(a[row][k] * x[k] for k in range(row + 1, n))) / a[row][row]
    return np.array(x)


def least_squares(columns, y):
    """Linear least squares y ~ sum p_k columns_k; returns (p, residual sum of squares)."""
    m = np.stack(columns)                      # (k, n)
    normal = np.einsum("in,jn->ij", m, m)
    p = solve(normal, np.einsum("in,n->i", m, y))
    residual = y - np.einsum("i,in->n", p, m)
    return p, float(np.einsum("n,n->", residual, residual))


def inverse_2x2(m):
    (a, b), (c, d) = m
    det = a * d - b * c
    return np.array([[d, -b], [-c, a]]) / det


def apply_2x2(m, v):
    return np.array([m[0][0] * v[0] + m[0][1] * v[1], m[1][0] * v[0] + m[1][1] * v[1]])


# ---------------------------------------------------------------- disk (BFP edge)


def fit_circle(x, y, rejection=3.0, iterations=5):
    """Robust algebraic circle fit (x^2 + y^2 + D x + E y + F = 0), outliers > rejection x MAD
    removed. Returns (xc, yc, r, rms residual, kept mask)."""
    keep = np.ones(len(x), bool)
    for _ in range(iterations):
        p, _ = least_squares([x[keep], y[keep], np.ones(keep.sum())],
                             -(x[keep] ** 2 + y[keep] ** 2))
        xc, yc = -p[0] / 2, -p[1] / 2
        r = np.sqrt(xc ** 2 + yc ** 2 - p[2])
        residual = np.hypot(x - xc, y - yc) - r
        mad = np.median(np.abs(residual[keep] - np.median(residual[keep]))) * 1.4826
        new_keep = np.abs(residual) < max(rejection * mad, 0.5)
        if (new_keep == keep).all():
            break
        keep = new_keep
    return xc, yc, r, float(np.sqrt(np.mean(residual[keep] ** 2))), keep


def fit_ellipse(x, y, rejection=3.0, iterations=8):
    """
    Robust algebraic ellipse fit: u^2 + B u v + C v^2 + D u + E v + F = 0 (u, v from the mean
    point), residual = algebraic value / its gradient (~ distance to the ellipse), outliers >
    rejection x MAD removed. Returns dict: center_xy, axes (half axes, large first), angle_deg
    (of the large axis from +x, y down), rms, and the kept mask.
    """
    keep = np.ones(len(x), bool)
    xm, ym = x.mean(), y.mean()
    u, v = x - xm, y - ym
    for _ in range(iterations):
        p, _ = least_squares([u[keep] * v[keep], v[keep] ** 2, u[keep], v[keep],
                              np.ones(keep.sum())], -(u[keep] ** 2))
        b, c, d, e, f = p
        value = u * u + b * u * v + c * v * v + d * u + e * v + f
        residual = value / np.hypot(2 * u + b * v + d, b * u + 2 * c * v + e)
        mad = np.median(np.abs(residual[keep] - np.median(residual[keep]))) * 1.4826
        new_keep = np.abs(residual) < max(rejection * mad, 0.3)
        if (new_keep == keep).all():
            break
        keep = new_keep
    cu, cv = solve([[2, b], [b, 2 * c]], [-d, -e])  # centre: zero gradient
    # (w - centre)^T A (w - centre) = k, A = [[1, b/2], [b/2, c]]
    k = cu * cu + b * cu * cv + c * cv * cv - f
    mean, diff = (1 + c) / 2, np.sqrt(((1 - c) / 2) ** 2 + (b / 2) ** 2)
    small, large = mean - diff, mean + diff  # eigenvalues of A
    angle = 0.5 * np.degrees(np.arctan2(b, 1 - c)) + 90  # direction of the small eigenvalue
    return {"center_xy": [float(xm + cu), float(ym + cv)],
            "axes": [float(np.sqrt(k / small)), float(np.sqrt(k / large))],
            "angle_deg": float((angle + 90) % 180 - 90),
            "rms_px": float(np.sqrt(np.mean(residual[keep] ** 2))),
            "points_kept": [int(keep.sum()), int(len(keep))], "_keep": keep}


def find_disks(image, min_area_fraction=0.01):
    """Bright disks of a (mean) image: [(rough x, rough y, rough radius, area)], largest first;
    with the background level and the threshold used."""
    smooth = ndimage.gaussian_filter(image, 4)
    background = np.median(smooth)
    top = np.percentile(smooth, 99.5)
    threshold = background + 0.5 * (top - background)
    labels, n = ndimage.label(smooth > threshold)
    disks = []
    for index in range(1, n + 1):
        rows, cols = np.nonzero(labels == index)
        if len(rows) < min_area_fraction * image.size:
            continue
        disks.append((cols.mean(), rows.mean(), np.sqrt(len(rows) / np.pi), len(rows)))
    disks.sort(key=lambda d: -d[3])
    return disks, background, threshold


def disk_edge(image, x0, y0, r0, angles=EDGE_ANGLES, fraction=0.5):
    """Edge points of a disk: along radial lines from (x0, y0), the crossing of the level
    fraction between the inside (median of the profile at 0.85-0.95 r0, just inside the edge)
    and the outside (low percentile of the profile beyond 1.05 r0: the local background, not
    raised by a neighbouring disk), the last one before 1.2 r0."""
    smooth = ndimage.gaussian_filter(image, 2)
    radii = np.arange(0.4 * r0, 1.5 * r0, 0.5)
    end = np.searchsorted(radii, 1.2 * r0)
    xs, ys = [], []
    for t in np.linspace(0, 2 * np.pi, angles, endpoint=False):
        px, py = x0 + radii * np.cos(t), y0 + radii * np.sin(t)
        inside = (px >= 0) & (px <= image.shape[1] - 1) & (py >= 0) & (py <= image.shape[0] - 1)
        if not inside.all():
            continue
        profile = ndimage.map_coordinates(smooth, [py, px], order=1)
        level = np.median(profile[(radii > 0.85 * r0) & (radii < 0.95 * r0)])
        outside = min(np.percentile(profile[radii > 1.05 * r0], 20), level)
        half = outside + fraction * (level - outside)
        above = np.nonzero((profile > half) & (radii < 1.2 * r0))[0]
        if len(above) == 0 or above[-1] >= end - 1:
            continue
        k = above[-1]  # last point above, next one below: linear interpolation
        f = (profile[k] - half) / (profile[k] - profile[k + 1])
        r = radii[k] + f * (radii[k + 1] - radii[k])
        xs.append(x0 + r * np.cos(t))
        ys.append(y0 + r * np.sin(t))
    return np.array(xs), np.array(ys)


def measure_disk(image, rough):
    """
    Edge of one disk fitted by an ellipse (edge level 0.5), and again at EDGE_LEVELS for the
    systematic error. Returns dict: center_xy, radius (mean half axis), ellipse, the centres at
    the other levels, and the edge points (_edge).
    """
    x0, y0, r0, _ = rough
    for _ in range(2):  # a circle first: a good centre for the radial profiles
        xs, ys = disk_edge(image, x0, y0, r0)
        x0, y0, r0, _, _ = fit_circle(xs, ys)
    ellipse = fit_ellipse(xs, ys)
    others = []
    for fraction in EDGE_LEVELS:
        e = fit_ellipse(*disk_edge(image, x0, y0, r0, fraction=fraction))
        others.append({"edge_level": fraction, "center_xy": e["center_xy"]})
    centers = np.array([ellipse["center_xy"]] + [o["center_xy"] for o in others])
    keep = ellipse.pop("_keep")
    return {"center_xy": ellipse["center_xy"], "radius": float(np.mean(ellipse["axes"])),
            "ellipse": ellipse, "edge_level_centers": others,
            "systematic_xy": [float(s) for s in (centers.max(axis=0) - centers.min(axis=0)) / 2],
            "statistical_xy": [ellipse["rms_px"] * np.sqrt(2 / keep.sum())] * 2,
            "_edge": (xs, ys, keep)}


# ---------------------------------------------------------------- vortex centre


def vortex_cost(image, x0, y0, xs, ys, values, m):
    """Residual sum of squares of background (quadratic) + a cos(m t) + b sin(m t) around
    (x0, y0), for the pixels (xs, ys) (VORTEX_INNER px around the centre left out).
    Returns (rss, number of pixels, pinwheel amplitude)."""
    dx, dy = xs - x0, ys - y0
    use = dx * dx + dy * dy > VORTEX_INNER ** 2
    dx, dy, v = dx[use], dy[use], values[use]
    t = np.arctan2(dy, dx)
    u, w = xs[use] - xs.mean(), ys[use] - ys.mean()
    scale = max(np.abs(u).max(), np.abs(w).max())
    u, w = u / scale, w / scale
    columns = [np.ones_like(u), u, w, u * u, w * w, u * w, np.cos(m * t), np.sin(m * t)]
    p, rss = least_squares(columns, v)
    return rss, int(use.sum()), float(np.hypot(p[6], p[7]))


def disk_pixels(image, disk, binning):
    """Pixels inside VORTEX_DISK_FRACTION of the disk, after binning; dark spots (dust: below
    half of the smoothed level) left out. Returns (x, y in Orca px, values)."""
    h, w = (image.shape[0] // binning) * binning, (image.shape[1] // binning) * binning
    binned = image[:h, :w].reshape(h // binning, binning, w // binning, binning).mean(axis=(1, 3))
    rows, cols = np.mgrid[0:binned.shape[0], 0:binned.shape[1]]
    xs, ys = (cols + 0.5) * binning - 0.5, (rows + 0.5) * binning - 0.5  # Orca px of the centres
    xc, yc = disk["center_xy"]
    inside = np.hypot(xs - xc, ys - yc) < VORTEX_DISK_FRACTION * disk["radius"]
    smooth = ndimage.gaussian_filter(binned, 12 / binning)
    level = np.median(smooth[inside])
    inside &= smooth > 0.5 * level
    return xs[inside], ys[inside], binned[inside]


def fit_parabola_2d(dx, dy, cost):
    """cost ~ c0 + c1 dx + c2 dy + c3 dx^2 + c4 dy^2 + c5 dx dy: returns (minimum dx, dy,
    hessian 2x2)."""
    p, _ = least_squares([np.ones_like(dx), dx, dy, dx * dx, dy * dy, dx * dy], cost)
    hessian = np.array([[2 * p[3], p[5]], [p[5], 2 * p[4]]])
    minimum = apply_2x2(inverse_2x2(hessian), [-p[1], -p[2]])
    return minimum, hessian


def vortex_center(image, disk, m, start=None, coarse_step=8, search_radius=None):
    """
    Centre (x, y Orca px) of the pinwheel of m periods inside the disk. Coarse grid on the
    binned image, then fine grids (2x2 binning), then a parabola on the cost around the
    minimum. Returns dict: center_xy, sigma_xy (from the curvature of the cost and the residual
    noise), contrast (amplitude of the pinwheel), noise_rms, n_pixels.
    """
    xc, yc = start if start is not None else disk["center_xy"]
    search_radius = search_radius or 0.6 * disk["radius"]
    xs, ys, values = disk_pixels(image, disk, VORTEX_BIN)
    best = None
    for gx in np.arange(xc - search_radius, xc + search_radius + 1, coarse_step):
        for gy in np.arange(yc - search_radius, yc + search_radius + 1, coarse_step):
            if np.hypot(gx - xc, gy - yc) > search_radius:
                continue
            cost = vortex_cost(image, gx, gy, xs, ys, values, m)[0]
            if best is None or cost < best[0]:
                best = (cost, gx, gy)
    _, x0, y0 = best
    xs, ys, values = disk_pixels(image, disk, 2)
    for step in (2.0, 0.5):
        grid = np.arange(-4, 5) * step
        costs = np.array([[vortex_cost(image, x0 + a, y0 + b, xs, ys, values, m)[0]
                           for a in grid] for b in grid])
        j, i = np.unravel_index(np.argmin(costs), costs.shape)
        x0, y0 = x0 + grid[i], y0 + grid[j]
    grid = np.arange(-3, 4) * 1.0
    gx, gy = np.meshgrid(grid, grid)
    costs = np.array([vortex_cost(image, x0 + a, y0 + b, xs, ys, values, m)[0]
                      for a, b in zip(gx.ravel(), gy.ravel())])
    (ddx, ddy), hessian = fit_parabola_2d(gx.ravel(), gy.ravel(), costs)
    if abs(ddx) < 3 and abs(ddy) < 3:
        x0, y0 = x0 + ddx, y0 + ddy
    rss, n, contrast = vortex_cost(image, x0, y0, xs, ys, values, m)
    noise = rss / (n - 8)
    # least squares: cost - min ~ (d - d0)^T (H / 2) (d - d0), covariance = 2 noise H^-1
    covariance = 2 * noise * inverse_2x2(hessian)
    sigma = np.sqrt(np.clip(np.diag(covariance), 0, None))
    return {"center_xy": [float(x0), float(y0)], "sigma_xy": [float(s) for s in sigma],
            "contrast": contrast, "noise_rms": float(np.sqrt(noise)), "n_pixels": n}


# ---------------------------------------------------------------- whole analysis


def frame_names(shift):
    return ("vortex", f"vortex_x+{shift}", f"vortex_y+{shift}", "vortex_half0_only")


def to_slm(inverse, d):
    return [inverse[0][0] * d[0] + inverse[0][1] * d[1],
            inverse[1][0] * d[0] + inverse[1][1] * d[1]]


def analyse_disk(stack, rough, m, shift):
    """
    One BFP disk: the disk (ellipse) and the vortex centre on each frame (Orca), the
    SLM -> Orca matrix from the shifted frames, and the BFP centre relative to the vortex
    centre on the SLM. Returns a dict (SLM px: x = column, y = row).
    """
    disk = measure_disk(stack[:3].mean(axis=0), rough)
    vortices = []
    for k, frame in enumerate(stack[:3]):
        start = vortices[0]["center_xy"] if vortices else None
        vortices.append(vortex_center(frame, disk, m, start=start,
                                      search_radius=None if k == 0 else 1.5 * shift))
    v0, vx, vy = (np.array(v["center_xy"]) for v in vortices)
    # columns: Orca move per SLM px along SLM x (columns) and SLM y (rows)
    matrix = np.array([(vx - v0) / shift, (vy - v0) / shift]).T
    inverse = inverse_2x2(matrix)
    offset = to_slm(inverse, np.array(disk["center_xy"]) - v0)
    # errors brought onto the SLM: |inverse| x (Orca errors)
    stat_orca = np.hypot(disk["statistical_xy"], vortices[0]["sigma_xy"])
    statistical = [abs(inverse[i][0]) * stat_orca[0] + abs(inverse[i][1]) * stat_orca[1]
                   for i in (0, 1)]
    systematic = [abs(inverse[i][0]) * disk["systematic_xy"][0] +
                  abs(inverse[i][1]) * disk["systematic_xy"][1] for i in (0, 1)]
    det = matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]
    scales = [float(np.hypot(matrix[0][0], matrix[1][0])),
              float(np.hypot(matrix[0][1], matrix[1][1]))]
    rotation = np.degrees(np.arctan2(matrix[1][0] - matrix[0][1] * np.sign(det),
                                     matrix[0][0] + matrix[1][1] * np.sign(det)))
    result = {
        "orca_disk": {key: value for key, value in disk.items() if not key.startswith("_")},
        "orca_vortex_centers": [{"frame": name, **v} for name, v in
                                zip(frame_names(shift), vortices)],
        "orca_disk_minus_vortex_px": [float(c) for c in np.array(disk["center_xy"]) - v0],
        "slm_to_orca_matrix": matrix.tolist(),
        "slm_to_orca_scale_x_y": scales,
        "slm_to_orca_rotation_deg": float(rotation),
        "slm_to_orca_mirrored": bool(det < 0),
        "bfp_minus_vortex_slm_x_y": [float(o) for o in offset],
        "bfp_minus_vortex_statistical_x_y": [float(s) for s in statistical],
        "bfp_minus_vortex_systematic_x_y": [float(s) for s in systematic],
        "bfp_radius_slm_px": float(disk["radius"] / np.sqrt(abs(det))),
        "_edge": disk["_edge"],
    }
    if len(stack) > 3:  # vortex only in SLM half 0: is this disk lit through half 0?
        xs, ys, values = disk_pixels(stack[3], disk, 2)
        contrast = vortex_cost(stack[3], *v0, xs, ys, values, m)[2]
        result["half0_only_contrast"] = contrast
        result["slm_half"] = 0 if contrast > 0.5 * vortices[0]["contrast"] else 1
    return result


def analyse(stack, metadata, halves=None):
    """
    stack: Orca frames of the recording (vortex, shifted along x, shifted along y[, vortex in
    SLM half 0 only]); metadata: its "calibration" dict. halves: SLM half of each disk (disks
    sorted from left to right on the Orca), used when the recording has no "half0_only" frame.
    Returns {"disks": [one dict per disk], "channels": {SLM half: BFP centre and radius}}.
    """
    stack = np.asarray(stack, float)
    m = metadata.get("total_phase_pi", 10) / 2
    shift = metadata.get("shift_px", 50)
    nominal = metadata.get("nominal_centers_row_col", [[600, 480], [600, 1440]])
    disks, _, _ = find_disks(stack[:3].mean(axis=0))
    if not disks:
        raise RuntimeError("No bright disk found on the Orca images.")
    disks.sort(key=lambda d: d[0])  # left to right on the Orca
    results, channels = [], {}
    for k, rough in enumerate(disks):
        try:
            result = analyse_disk(stack, rough, m, shift)
        except Exception as error:  # e.g. a disk cut by an aperture, no clear pinwheel
            results.append({"orca_rough_center_xy": [float(rough[0]), float(rough[1])],
                            "error": str(error)})
            continue
        if "slm_half" not in result and halves is not None and k < len(halves):
            result["slm_half"] = halves[k]
        half = result.get("slm_half")
        if half is not None:
            row, col = nominal[half]
            (ox, oy), (sx, sy) = result["bfp_minus_vortex_slm_x_y"], \
                result["bfp_minus_vortex_statistical_x_y"]
            channels[half] = {
                "bfp_center_slm_row_col": [row + oy, col + ox],
                "statistical_error_row_col": [sy, sx],
                "systematic_error_row_col": result["bfp_minus_vortex_systematic_x_y"][::-1],
                "bfp_radius_slm_px": result["bfp_radius_slm_px"],
                "vortex_center_slm_row_col": [row, col],
                "orca_disk_center_xy": result["orca_disk"]["center_xy"]}
        results.append(result)
    return {"disks": results, "channels": channels}


def load_recording(folder, index=0):
    import tifffile
    stack = tifffile.imread(os.path.join(folder, f"{index}_0.tiff"))
    with open(os.path.join(folder, f"{index}.json")) as f:
        metadata = json.load(f)
    return stack, metadata["acquisition"]["calibration"]


def save_figure(stack, result, path):
    """Mean image with, for each disk, its edge points (green: kept, red: left out), the fitted
    ellipse and its centre (yellow) and the vortex centres (red, orange: x shift, blue: y)."""
    from PIL import Image, ImageDraw
    image = np.asarray(stack, float)[:3].mean(axis=0)
    lo, hi = np.percentile(image, [1, 99.8])
    gray = (np.clip((image - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8)
    picture = Image.fromarray(gray).convert("RGB")
    draw = ImageDraw.Draw(picture)
    for disk in result["disks"]:
        if "error" in disk:
            continue
        xs, ys, keep = disk["_edge"]
        for x, y, k in zip(xs, ys, keep):
            draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(0, 255, 0) if k else (255, 0, 0))
        ellipse = disk["orca_disk"]["ellipse"]
        (cx, cy), (a, b) = ellipse["center_xy"], ellipse["axes"]
        angle = np.radians(ellipse["angle_deg"])
        t = np.linspace(0, 2 * np.pi, 361)
        ex = cx + a * np.cos(t) * np.cos(angle) - b * np.sin(t) * np.sin(angle)
        ey = cy + a * np.cos(t) * np.sin(angle) + b * np.sin(t) * np.cos(angle)
        draw.line(list(zip(ex, ey)), fill=(255, 220, 0), width=2)
        draw.line([cx - 20, cy, cx + 20, cy], fill=(255, 220, 0), width=3)
        draw.line([cx, cy - 20, cx, cy + 20], fill=(255, 220, 0), width=3)
        for v, color in zip(disk["orca_vortex_centers"],
                            ((255, 0, 0), (255, 160, 0), (0, 160, 255))):
            x, y = v["center_xy"]
            draw.ellipse([x - 8, y - 8, x + 8, y + 8], outline=color, width=3)
    picture.save(path)


def calibration_file(folder, index, metadata, result, now=None):
    """Content of the BFP calibration file of a recording."""
    return {
        "type": "bfp_calibration",
        "date": (now or datetime.now()).isoformat(timespec="seconds"),
        "recording": os.path.join(folder, f"{index}_0.tiff"),
        "light_path": metadata.get("light_path"),
        "settings": {key: metadata.get(key) for key in
                     ("total_phase_pi", "shift_px", "nominal_centers_row_col")},
        "channels": {f"slm_half_{half}": channel
                     for half, channel in sorted(result["channels"].items())},
        "disks": [{k: v for k, v in d.items() if not k.startswith("_")}
                  for d in result["disks"]],
        "notes": "BFP centre (row, column) in SLM px; errors: statistical (noise) and "
                 "systematic (edge level 0.3-0.7); x = column, y = row.",
    }


def bfp_shifts(content):
    """
    {SLM half: [rows, columns]} of a calibration file: measured BFP centre - nominal centre,
    rounded to whole SLM pixels (the move applied to the masks of that half).
    """
    nominal = content["settings"]["nominal_centers_row_col"]
    shifts = {}
    for name, channel in content["channels"].items():
        half = int(name.rsplit("_", 1)[1])
        row, col = channel["bfp_center_slm_row_col"]
        shifts[half] = [int(round(row - nominal[half][0])), int(round(col - nominal[half][1]))]
    return shifts


def shift_halves(pattern, shifts, n_halves=2):
    """
    Mask with each SLM half (columns cut in n_halves) moved by its shift {half: [rows,
    columns]}, so a mask drawn around the nominal centre lands on the measured BFP. Each half
    stays in its own columns: what leaves it is dropped, the pixels left empty are 0. Halves
    without a shift are unchanged.
    """
    pattern = np.asarray(pattern)
    out = np.zeros_like(pattern)
    height, width = pattern.shape[0], pattern.shape[1] // n_halves
    for half in range(n_halves):
        block = pattern[:, half * width:(half + 1) * width]
        target = out[:, half * width:(half + 1) * width]
        dr, dc = shifts.get(half, (0, 0))
        r0, r1 = max(dr, 0), min(height, height + dr)
        c0, c1 = max(dc, 0), min(width, width + dc)
        if r1 > r0 and c1 > c0:
            target[r0:r1, c0:c1] = block[r0 - dr:r1 - dr, c0 - dc:c1 - dc]
    return out


def run(folder, index=0, halves=None):
    """Analyse a recording and write <index>_bfp_calibration_<YYYY_MM_DD_HHMMSS>.json and .png
    next to it (time of the analysis)."""
    stack, metadata = load_recording(folder, index)
    result = analyse(stack, metadata, halves)
    now = datetime.now()
    content = calibration_file(folder, index, metadata, result, now)
    name = os.path.join(folder, f"{index}_bfp_calibration_{now:%Y_%m_%d_%H%M%S}")
    path = name + ".json"
    with open(path, "w") as f:
        json.dump(content, f, indent=4)
    save_figure(stack, result, name + ".png")
    return path, content


if __name__ == "__main__":
    folder = sys.argv[1]
    index = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    halves = [int(h) for h in sys.argv[3:]] or None
    path, content = run(folder, index, halves)
    print(json.dumps(content["channels"], indent=4))
    print("written:", path)
