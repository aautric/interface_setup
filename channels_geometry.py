"""
Analysis of the channels_geometry recordings (calibration.py): correspondence between the two
channels of the Prime, top (reference) and bottom, as a vector field and a rotation. No
correction of the images (no interpolation: the noise statistics are kept), only the mapping.
Inspired by reconstruction_juliette.py (polMFM), for 2 channels.

Axes of the channels (image: col to the right, row to the bottom):
    top = reference:  x = col - cx_top,    y = cy_top - row      (x to the right, y UP)
    bottom:           x = col - cx_bottom, y = row - cy_bottom   (x to the right, y DOWN)
(cx, cy: centre of the channel found on the lamp image). In these axes the same point of the
sample has about the same (x, y) in both channels: the bottom channel is the top one mirrored
along y on the camera.

Recordings (each an acquisition of CALIBRATION_ROOT/channels_geometry/YYYY_MM_DD, N frames,
averaged here):
    lamp:   the white transmitted lamp, no beads in focus: the two lit rectangles give the
            channels (edges, centres: where to cut the image in 2), a first guess of the
            mapping (centre to centre, the mirror) and the global normalization of the
            channels (mean lamp signal above the dark level in each, as reconstruction_juliette);
    beads:  one or more fields of TetraSpeck beads (size 4) in focus.
Pipeline:
    1. channels on the lamp (row profile: two bright bands; column profile of each band);
    2. beads in each channel: local maxima above the background (median filter) + noise
       threshold, 2D Gaussian fit (sub-pixel position);
    3. pairs: each top bead to the nearest bottom bead around its predicted position (lamp
       guess, then the fitted model, smaller search radius);
    4. mapping, from top (x, y) to bottom (x, y), in channel axes:
         affine  (x_b, y_b) = A (x_t, y_t) + t, robust (sigma clipping): rotation, scales,
                 shear, translation (= mean vector);
         field   the remaining residual, a polynomial of degree <= 3 in (x_t, y_t);
       vector field D(x, y) = mapped - (x, y) on a grid of the top channel; local rotation
       = (dDy/dx - dDx/dy) / 2.
Only numpy and scipy.ndimage (np.linalg and matrix products crash Python in setup_env):
small systems solved by bfp_analysis.solve.

Command line: python channels_geometry.py <folder> <lamp index> <bead index> [bead index...]
writes <first bead index>_channels_geometry_<YYYY_MM_DD_HHMMSS>.json and .png in <folder>.
"""

import glob
import json
import os
import sys
from datetime import datetime

import numpy as np
from scipy import ndimage

from bfp_analysis import least_squares, solve

CHANNELS = ("top", "bottom")  # top = reference
BAND_LEVEL = 0.5          # channel edge: halfway between dark and lit (lamp profiles)
BACKGROUND_SIZE = 25      # px, median filter of the background (larger than a bead)
THRESHOLD_SIGMAS = 6.0    # bead detection: above background + this x noise
SPOT_HALF = 4             # px, half size of the Gaussian fit window
MIN_SPACING = 8           # px, beads closer than this to another one are left out
EDGE_MARGIN = 6           # px, beads closer to a channel edge are left out
SEARCH_RADII = (25.0, 4.0, 2.0)  # px, pairing radius: lamp guess, then the fitted model (twice)
CLIP_SIGMAS = 3.0         # robust affine: pairs beyond this x the robust residual are left out
FIELD_DEGREE = 3          # polynomial of the residual field (lowered with few beads)
GRID_STEP = 50            # px, grid of the vector field in the result file
NORM_MARGIN = 10          # px from the channel edges left out of the lamp normalization
EDGE_BIN = 16             # px along a lamp edge averaged for one edge point
EDGE_INSET = 40           # px from the corners left out of the edge fits
EDGE_SEARCH = 25          # px, half window across an edge around its first guess
CV_FOLDS = 5              # one bead field only: cross-validation on this many folds of the beads
CV_TOLERANCE = 0.02       # field degree: the lowest within 2 % of the best predicting one
CHECK_RADIUS = 5.0        # px, check: bottom bead searched this far from its predicted position
PIXEL_NM = 68.2           # nm per Prime pixel in the sample (stage moves, 2026-11-03), for the
                          # check figure only


# ---------------------------------------------------------------- recordings


def load_mean(folder, index):
    """Mean image of acquisition index (all its X_k.tiff files) and its calibration metadata."""
    import tifffile
    paths = sorted(glob.glob(os.path.join(folder, f"{index}_*.tiff")),
                   key=lambda p: int(os.path.basename(p).rsplit("_", 1)[1].split(".")[0]))
    if not paths:
        raise FileNotFoundError(f"No {index}_0.tiff in {folder}")
    total, n = None, 0
    for path in paths:
        stack = np.asarray(tifffile.imread(path), float)
        stack = stack[None] if stack.ndim == 2 else stack
        total = stack.sum(axis=0) if total is None else total + stack.sum(axis=0)
        n += len(stack)
    metadata = {}
    json_path = os.path.join(folder, f"{index}.json")
    if os.path.exists(json_path):
        with open(json_path) as f:
            metadata = json.load(f)
    return total / n, metadata


# ---------------------------------------------------------------- 1. channels (lamp)


def _edges(profile, level=BAND_LEVEL):
    """Runs of the profile above level (between its dark and lit values): [(start, end)] with
    sub-pixel edges (linear interpolation), longest first."""
    p = np.asarray(profile, float)
    dark, lit = np.percentile(p, 5), np.percentile(p, 95)
    threshold = dark + level * (lit - dark)
    above = p > threshold
    runs, start = [], None
    for i, a in enumerate(np.append(above, False)):
        if a and start is None:
            start = i
        elif not a and start is not None:
            runs.append((start, i - 1))
            start = None

    def crossing(i, j):  # threshold between samples i and j
        if not (0 <= i < len(p) and 0 <= j < len(p)) or p[i] == p[j]:
            return float(j)
        return i + (threshold - p[i]) / (p[j] - p[i]) * (j - i)

    edges = [(crossing(s - 1, s), crossing(e, e + 1)) for s, e in runs]
    return sorted(edges, key=lambda r: r[1] - r[0], reverse=True)


def detect_channels(lamp):
    """The two lit rectangles of the lamp image: {"top": {...}, "bottom": {...},
    "split_row"}; each {"rows": [first, last], "cols": [first, last], "center_xy": [cx, cy],
    "size_xy"} (image px, sub-pixel edges)."""
    raw = np.asarray(lamp, float)
    lamp = ndimage.gaussian_filter(raw, 2)
    bands = _edges(lamp.mean(axis=1))
    if len(bands) < 2:
        raise ValueError("Lamp image: two lit bands (top and bottom channels) not found.")
    bands = sorted(bands[:2])
    channels = {}
    for name, (r0, r1) in zip(CHANNELS, bands):
        rows = slice(int(np.ceil(r0)) + 2, int(np.floor(r1)) - 1)
        cols = _edges(lamp[rows].mean(axis=0))
        if not cols:
            raise ValueError(f"Lamp image: columns of the {name} channel not found.")
        c0, c1 = cols[0]
        channels[name] = {"rows": [r0, r1], "cols": [c0, c1],
                          "center_xy": [(c0 + c1) / 2, (r0 + r1) / 2],
                          "size_xy": [c1 - c0, r1 - r0]}
    channels["split_row"] = (channels["top"]["rows"][1] + channels["bottom"]["rows"][0]) / 2
    for name in CHANNELS:
        channels[name]["shape"] = rectangle_shape(raw, channels[name], name)
    # the frame of the channels = the top rectangle; bottom rotation seen on the lamp (a check
    # of the bead mapping)
    channels["frame_rotation_deg"] = channels["top"]["shape"]["rotation_deg"]
    channels["bottom_rotation_lamp_deg"] = (channels["bottom"]["shape"]["rotation_deg"]
                                            - channels["top"]["shape"]["rotation_deg"])
    return channels


def _edge_crossing(profile, guess, rising, search=EDGE_SEARCH):
    """Sub-pixel position of the half-level crossing nearest to guess in a 1D profile across an
    edge (rising: dark then lit along the profile), or nan."""
    g = int(round(guess))
    if g - search < 0 or g + search + 1 > len(profile):
        return np.nan
    seg = np.asarray(profile[g - search:g + search + 1], float)
    half = (np.percentile(seg, 5) + np.percentile(seg, 95)) / 2
    s = seg if rising else seg[::-1]
    k = np.nonzero((s[:-1] < half) & (s[1:] >= half))[0]
    if not len(k):
        return np.nan
    k = k[np.argmin(np.abs(k - search))]
    pos = k + (half - s[k]) / (s[k + 1] - s[k])
    return g - search + (pos if rising else 2 * search - pos)


def rectangle_shape(lamp, channel, name):
    """The 4 edges of a lit rectangle (lamp, image not smoothed), each measured every EDGE_BIN
    px (away from the corners) and fitted by a line and a parabola. Angles in the axes of the
    channel (to_local without the frame rotation), counterclockwise positive: rotation of each
    edge from the channel x axis (upper, lower) or y axis (left, right). Returns {"edges":
    {upper, lower, left, right: {angle_deg, bow_px, line_rms_px, points}}, "rotation_deg" (mean
    of the 4), "skew_deg" (vertical - horizontal edges: 0 for a rectangle), "parallelism_deg"
    {horizontal: upper - lower, vertical: left - right}}. upper / lower / left / right as seen
    on the camera; bow_px: parabola sagitta at the ends of the edge (image px, + = towards
    larger row / col)."""
    lamp = np.asarray(lamp, float)
    (r0, r1), (c0, c1) = channel["rows"], channel["cols"]
    y_sign = -1.0 if name == "top" else 1.0  # channel y = y_sign * (row - cy)
    edges = {}
    for edge in ("upper", "lower", "left", "right"):
        horizontal = edge in ("upper", "lower")
        start, stop = (c0, c1) if horizontal else (r0, r1)
        along, across = [], []
        for t in np.arange(start + EDGE_INSET, stop - EDGE_INSET - EDGE_BIN, EDGE_BIN):
            t = int(t)
            if horizontal:
                profile = lamp[:, t:t + EDGE_BIN].mean(axis=1)
                v = _edge_crossing(profile, r0 if edge == "upper" else r1, edge == "upper")
            else:
                profile = lamp[t:t + EDGE_BIN, :].mean(axis=0)
                v = _edge_crossing(profile, c0 if edge == "left" else c1, edge == "left")
            if np.isfinite(v):
                along.append(t + (EDGE_BIN - 1) / 2)
                across.append(v)
        along, across = np.array(along), np.array(across)
        if len(along) < 5:
            raise ValueError(f"Lamp image: {edge} edge of the {name} channel not found.")
        half = (along.max() - along.min()) / 2
        u = (along - along.mean()) / half
        line, _ = least_squares([np.ones_like(u), u], across)
        parabola, _ = least_squares([np.ones_like(u), u, u ** 2], across)
        slope = line[1] / half  # d row / d col (horizontal edges), d col / d row (vertical)
        rms = float(np.sqrt(np.mean((across - line[0] - line[1] * u) ** 2)))
        angle = np.degrees(np.arctan(slope)) * (y_sign if horizontal else -y_sign)
        edges[edge] = {"angle_deg": float(angle), "bow_px": float(parabola[2]),
                       "line_rms_px": rms, "points": int(len(along))}
    a = {e: edges[e]["angle_deg"] for e in edges}
    return {"edges": edges, "rotation_deg": float(np.mean(list(a.values()))),
            "skew_deg": float((a["left"] + a["right"] - a["upper"] - a["lower"]) / 2),
            "parallelism_deg": {"horizontal": float(a["upper"] - a["lower"]),
                                "vertical": float(a["left"] - a["right"])}}


def normalization(lamp, channels, margin=NORM_MARGIN):
    """Global normalization of the channels from the lamp: mean signal above the dark level
    (median of the rows outside the two channels) inside each channel (margin px from its
    edges). Returns {"dark_level", "signal": {top, bottom}, "coefficients": {top, bottom}
    (signal / mean of the two, as reconstruction_juliette), "bottom_over_top"}."""
    lamp = np.asarray(lamp, float)
    rows = np.arange(lamp.shape[0])
    outside = np.ones(len(rows), bool)
    for name in CHANNELS:
        r0, r1 = channels[name]["rows"]
        outside &= (rows < r0 - margin) | (rows > r1 + margin)
    dark = float(np.median(lamp[outside])) if outside.sum() >= 3 else \
        float(np.percentile(lamp, 1))
    signal = {}
    for name in CHANNELS:
        (c0, c1), (r0, r1) = channels[name]["cols"], channels[name]["rows"]
        inside = lamp[int(np.ceil(r0)) + margin:int(np.floor(r1)) - margin + 1,
                      int(np.ceil(c0)) + margin:int(np.floor(c1)) - margin + 1]
        signal[name] = float(inside.mean() - dark)
    mean = (signal["top"] + signal["bottom"]) / 2
    return {"dark_level": dark, "margin_px": margin, "signal": signal,
            "coefficients": {n: signal[n] / mean for n in CHANNELS},
            "bottom_over_top": signal["bottom"] / signal["top"]}


def to_local(name, xy, channels):
    """Image (col, row) -> frame of the channel: x right, y up on top, down on bottom, from the
    channel centre, then turned by -frame_rotation_deg (the top rectangle's rotation on the
    camera, the same for both channels: the bottom frame is the mirror of the top one), so that
    the axes are those of the top rectangle."""
    cx, cy = channels[name]["center_xy"]
    xy = np.asarray(xy, float)
    sign = -1.0 if name == "top" else 1.0
    x, y = xy[..., 0] - cx, sign * (xy[..., 1] - cy)
    th = np.radians(channels.get("frame_rotation_deg", 0.0))
    c, s = np.cos(th), np.sin(th)
    return np.stack([c * x + s * y, -s * x + c * y], axis=-1)


def to_image(name, local, channels):
    cx, cy = channels[name]["center_xy"]
    local = np.asarray(local, float)
    sign = -1.0 if name == "top" else 1.0
    th = np.radians(channels.get("frame_rotation_deg", 0.0))
    c, s = np.cos(th), np.sin(th)
    x, y = c * local[..., 0] - s * local[..., 1], s * local[..., 0] + c * local[..., 1]
    return np.stack([x + cx, sign * y + cy], axis=-1)


# ---------------------------------------------------------------- 2. beads


def fit_spot(image, x0, y0, half=SPOT_HALF, iterations=15):
    """2D Gaussian + constant fitted around (x0, y0) (Gauss-Newton, damped): (x, y, sigma,
    amplitude) or None."""
    xi, yi = int(round(x0)), int(round(y0))
    sub = image[yi - half:yi + half + 1, xi - half:xi + half + 1]
    if sub.shape != (2 * half + 1, 2 * half + 1):
        return None
    yy, xx = np.mgrid[yi - half:yi + half + 1, xi - half:xi + half + 1]
    xx, yy, z = xx.ravel().astype(float), yy.ravel().astype(float), sub.ravel()
    c = float(np.percentile(z, 20))
    w = np.clip(z - c, 0, None)
    if w.sum() <= 0:
        return None
    p = np.array([w.max(), (w * xx).sum() / w.sum(), (w * yy).sum() / w.sum(), 1.3, c])
    damping = 1e-3
    for _ in range(iterations):
        a, x, y, s, c = p
        r2 = (xx - x) ** 2 + (yy - y) ** 2
        e = np.exp(-r2 / (2 * s * s))
        model = c + a * e
        residual = z - model
        jac = [e, a * e * (xx - x) / s ** 2, a * e * (yy - y) / s ** 2, a * e * r2 / s ** 3,
               np.ones_like(z)]
        normal = np.einsum("in,jn->ij", np.stack(jac), np.stack(jac))
        normal += damping * np.diag(np.diag(normal))
        try:
            step = solve(normal, np.einsum("in,n->i", np.stack(jac), residual))
        except ValueError:
            return None
        p = p + step
        if np.abs(step[1:3]).max() < 1e-3:
            break
    a, x, y, s, c = p
    if not (a > 0 and 0.5 < s < half and abs(x - x0) < half - 1 and abs(y - y0) < half - 1):
        return None
    return float(x), float(y), float(s), float(a)


def find_beads(image, box, threshold_sigmas=THRESHOLD_SIGMAS):
    """Beads in box = (col0, col1, row0, row1) of the image: [(x, y, sigma, amplitude)]
    (image px), isolated and away from the edges."""
    c0, c1, r0, r1 = (int(round(v)) for v in box)
    c0, r0 = c0 + EDGE_MARGIN, r0 + EDGE_MARGIN
    c1, r1 = c1 - EDGE_MARGIN, r1 - EDGE_MARGIN
    region = np.asarray(image, float)[r0:r1, c0:c1]
    background = ndimage.median_filter(region, size=BACKGROUND_SIZE)
    signal = region - background
    noise = 1.4826 * np.median(np.abs(signal - np.median(signal)))
    smooth = ndimage.gaussian_filter(signal, 1)
    peaks = (smooth == ndimage.maximum_filter(smooth, size=2 * MIN_SPACING + 1)) & \
        (smooth > threshold_sigmas * max(noise, 1e-9) / 2)
    rows, cols = np.nonzero(peaks)
    found = []
    corrected = np.asarray(image, float).copy()
    corrected[r0:r1, c0:c1] = signal
    for row, col in zip(rows, cols):
        x, y = col + c0, row + r0
        if signal[row, col] < threshold_sigmas * noise:
            continue
        spot = fit_spot(corrected, x, y)
        if spot is not None:
            found.append(spot)
    xy = np.array([[f[0], f[1]] for f in found]).reshape(-1, 2)
    keep = []
    for i, p in enumerate(xy):  # isolated beads only
        d = np.hypot(*(xy - p).T)
        if np.sum(d < MIN_SPACING) == 1:
            keep.append(found[i])
    return keep


# ---------------------------------------------------------------- 3. pairs, 4. mapping


def pair(top_local, bottom_local, predict, radius):
    """Indexes (i_top, i_bottom) of mutual nearest pairs within radius of the prediction."""
    if not len(top_local) or not len(bottom_local):
        return np.zeros((0, 2), int)
    predicted = predict(top_local)
    d = np.hypot(predicted[:, None, 0] - bottom_local[None, :, 0],
                 predicted[:, None, 1] - bottom_local[None, :, 1])
    best_b = d.argmin(axis=1)
    best_t = d.argmin(axis=0)
    pairs = [(i, j) for i, j in enumerate(best_b) if d[i, j] < radius and best_t[j] == i]
    return np.array(pairs, int).reshape(-1, 2)


def fit_affine(src, dst, clip=CLIP_SIGMAS, iterations=10):
    """dst ~ A src + t, robust: (A 2x2, t, kept mask, residuals)."""
    keep = np.ones(len(src), bool)
    for _ in range(iterations):
        columns = [src[keep, 0], src[keep, 1], np.ones(keep.sum())]
        px, _ = least_squares(columns, dst[keep, 0])
        py, _ = least_squares(columns, dst[keep, 1])
        a = np.array([[px[0], px[1]], [py[0], py[1]]])
        t = np.array([px[2], py[2]])
        residual = dst - (np.einsum("ij,nj->ni", a, src) + t)
        r = np.hypot(*residual.T)
        scale = 1.4826 * np.median(r[keep]) + 1e-6
        new_keep = r < clip * scale + 0.05
        if (new_keep == keep).all():
            break
        keep = new_keep
    return a, t, keep, residual


def decompose(a):
    """Rotation (deg), scales along x and y, shear of a 2x2 matrix a = R(theta) [[sx, k], [0, sy]]."""
    theta = np.arctan2(a[1, 0], a[0, 0])
    sx = np.hypot(a[0, 0], a[1, 0])
    c, s = np.cos(theta), np.sin(theta)
    k = c * a[0, 1] + s * a[1, 1]
    sy = -s * a[0, 1] + c * a[1, 1]
    return {"rotation_deg": float(np.degrees(theta)), "scale_x": float(sx), "scale_y": float(sy),
            "shear": float(k / sy if sy else 0.0)}


def poly_terms(xy, degree, norm):
    """Monomials x^i y^j (i + j <= degree) of the coordinates divided by norm."""
    x, y = xy[:, 0] / norm, xy[:, 1] / norm
    return [x ** i * y ** (d - i) for d in range(degree + 1) for i in range(d, -1, -1)]


def fit_field(src, residual, degree):
    """Polynomial of the residual (x and y) in the top coordinates: coefficients, norm."""
    norm = max(float(np.abs(src).max()), 1.0)
    terms = poly_terms(src, degree, norm)
    cx, _ = least_squares(terms, residual[:, 0])
    cy, _ = least_squares(terms, residual[:, 1])
    return cx, cy, norm


class Mapping:
    """Top local (x, y) -> bottom local (x, y): affine + polynomial residual field."""

    def __init__(self, a, t, cx=None, cy=None, norm=1.0, degree=0):
        self.a, self.t = np.asarray(a, float), np.asarray(t, float)
        self.cx, self.cy, self.norm, self.degree = cx, cy, norm, degree

    def __call__(self, xy):
        xy = np.asarray(xy, float).reshape(-1, 2)
        out = np.einsum("ij,nj->ni", self.a, xy) + self.t
        if self.cx is not None:
            terms = np.stack(poly_terms(xy, self.degree, self.norm))
            out = out + np.stack([np.einsum("k,kn->n", self.cx, terms),
                                  np.einsum("k,kn->n", self.cy, terms)], axis=1)
        return out


def field_degree(n_pairs):
    """Polynomial degree the number of pairs allows (about 3 pairs per coefficient)."""
    for degree in range(FIELD_DEGREE, -1, -1):
        if n_pairs >= 3 * (degree + 1) * (degree + 2) // 2:
            return degree
    return -1


def fit_mapping(src, dst, degree):
    """Robust affine, then the polynomial field of degree (none if < 1) on the pairs kept:
    (Mapping, kept mask, affine residual)."""
    a, t, keep, residual = fit_affine(src, dst)
    if degree >= 1:
        cx, cy, norm = fit_field(src[keep], residual[keep], degree)
        return Mapping(a, t, cx, cy, norm, degree), keep, residual
    return Mapping(a, t), keep, residual


def cross_validate(src, dst, groups, degree):
    """Each group (bead field) predicted by the mapping fitted on the others: errors (measured -
    predicted bottom position, px, frame axes), one per pair."""
    errors = np.full(src.shape, np.nan)
    for g in np.unique(groups):
        test = groups == g
        model, _, _ = fit_mapping(src[~test], dst[~test], degree)
        errors[test] = dst[test] - model(src[test])
    return errors


def choose_degree(src, dst, which):
    """Degree of the field (0 = affine only) predicting best the beads left out: leave one bead
    field out (several fields) or CV_FOLDS folds of the beads (one field). The lowest degree
    within CV_TOLERANCE of the best. Returns (degree, {degree: rms px}, errors at that degree,
    groups used)."""
    if len(np.unique(which)) >= 2:
        groups, kind = which, "bead fields (leave one field out)"
    else:
        groups, kind = np.arange(len(src)) % CV_FOLDS, f"{CV_FOLDS} folds of the beads"
    smallest = min(np.sum(groups != g) for g in np.unique(groups))
    rms, errors = {}, {}
    for degree in range(0, max(field_degree(int(smallest)), 0) + 1):
        e = cross_validate(src, dst, groups, degree)
        errors[degree] = e
        rms[degree] = float(np.sqrt(np.nanmean(np.sum(e ** 2, axis=1))))
    best = min(rms.values())
    degree = min(d for d in rms if rms[d] <= best * (1 + CV_TOLERANCE))
    return degree, rms, errors[degree], kind


def analyse(lamp, bead_images):
    """Channels (lamp), beads, pairs and mapping. Returns a dict (keys starting with _ for the
    figure only)."""
    channels = detect_channels(lamp)
    boxes = {n: (*channels[n]["cols"], *channels[n]["rows"]) for n in CHANNELS}
    tops, bottoms, fields = [], [], []
    a0 = np.eye(2)
    t0 = np.zeros(2)  # lamp guess: the same local (x, y) in both channels
    for k, image in enumerate(bead_images):
        top = find_beads(image, boxes["top"])
        bottom = find_beads(image, boxes["bottom"])
        tops.append(to_local("top", np.array([b[:2] for b in top]).reshape(-1, 2), channels))
        bottoms.append(to_local("bottom", np.array([b[:2] for b in bottom]).reshape(-1, 2),
                                channels))
        fields.append({"field": k, "beads_top": len(top), "beads_bottom": len(bottom),
                       "_top": top, "_bottom": bottom})
    model = Mapping(a0, t0)
    for radius in SEARCH_RADII:
        src, dst, which = [], [], []
        for k, (t, b) in enumerate(zip(tops, bottoms)):
            p = pair(t, b, model, radius)
            fields[k]["pairs"] = len(p)
            src.append(t[p[:, 0]])
            dst.append(b[p[:, 1]])
            which.append(np.full(len(p), k))
        src, dst, which = np.concatenate(src), np.concatenate(dst), np.concatenate(which)
        if len(src) < 3:
            raise ValueError(f"Only {len(src)} bead pairs found (search radius {radius} px): "
                             "beads in focus, bright enough, both channels?")
        a, t, keep, residual = fit_affine(src, dst)
        model = Mapping(a, t)
    affine_residual = residual
    # degree of the field by cross-validation on the pairs kept, then the mapping on all of them
    degree, cv_rms, cv_errors, cv_kind = choose_degree(src[keep], dst[keep], which[keep])
    model, _, _ = fit_mapping(src[keep], dst[keep], degree)
    if degree < 1:
        model = Mapping(a, t)
    final = dst - model(src)
    r_aff, r_fin = np.hypot(*affine_residual[keep].T), np.hypot(*final[keep].T)
    # vector field on a grid of the top channel
    (c0, c1), (r0, r1) = channels["top"]["cols"], channels["top"]["rows"]
    cols = np.arange(np.ceil(c0 + EDGE_MARGIN), c1 - EDGE_MARGIN, GRID_STEP)
    rows = np.arange(np.ceil(r0 + EDGE_MARGIN), r1 - EDGE_MARGIN, GRID_STEP)
    grid_img = np.array([[c, r] for r in rows for c in cols], float)
    grid = to_local("top", grid_img, channels)
    mapped = model(grid)
    h = 1.0
    dxp = model(grid + [h, 0]) - model(grid - [h, 0])
    dyp = model(grid + [0, h]) - model(grid - [0, h])
    # D = mapped - identity: dDy/dx = dyp... derivative of the mapping minus identity
    local_rotation = np.degrees(0.5 * ((dxp[:, 1] / (2 * h)) - (dyp[:, 0] / (2 * h))))
    vectors = mapped - grid
    # deformation: what is left once the translation and the rotation of the bottom are removed
    th = np.radians(decompose(a)["rotation_deg"])
    rotated = np.stack([np.cos(th) * grid[:, 0] - np.sin(th) * grid[:, 1],
                        np.sin(th) * grid[:, 0] + np.cos(th) * grid[:, 1]], axis=1)
    deformation = mapped - rotated - t
    return {
        "channels": channels,
        "normalization": normalization(lamp, channels),
        "fields": [{k: v for k, v in f.items() if not k.startswith("_")} for f in fields],
        "pairs_used": int(keep.sum()), "pairs_found": int(len(keep)),
        "affine": {"matrix": a.tolist(), "translation_xy": t.tolist(), **decompose(a)},
        "field": {"degree": int(max(degree, 0)),
                  "coefficients_x": None if degree < 1 else model.cx.tolist(),
                  "coefficients_y": None if degree < 1 else model.cy.tolist(),
                  "norm_px": model.norm},
        "residual_px": {"affine_rms": float(np.sqrt(np.mean(r_aff ** 2))),
                        "affine_max": float(r_aff.max()),
                        "final_rms": float(np.sqrt(np.mean(r_fin ** 2))),
                        "final_max": float(r_fin.max())},
        "cross_validation": {"groups": cv_kind, "rms_px_by_degree": {str(d): v for d, v in
                                                                     cv_rms.items()},
                             "degree": int(degree), **error_stats(cv_errors)},
        "local_rotation_deg": {"mean": float(local_rotation.mean()),
                               "min": float(local_rotation.min()),
                               "max": float(local_rotation.max())},
        "vector_field": {
            "grid_top_image_col_row": grid_img.round(2).tolist(),
            "grid_bottom_image_col_row": to_image("bottom", mapped, channels).round(3).tolist(),
            "vector_local_xy": vectors.round(3).tolist(),
            "deformation_local_xy": deformation.round(3).tolist(),
            "local_rotation_deg": local_rotation.round(4).tolist()},
        "_pairs": (src, dst, keep, which), "_fields": fields, "_model": model,
        "_grid": (grid_img, grid, deformation),
        "_cv": (to_image("top", src[keep], channels), cv_errors),
    }


# ---------------------------------------------------------------- result file and figure


def save_figure(lamp, beads, result, path, arrow_gain=None):
    """Lamp image with the channel edges (yellow), first bead image with the beads (green: paired,
    red: not), and the vector field minus its mean on the top channel (cyan arrows, magnified)."""
    from PIL import Image, ImageDraw
    image = np.asarray(beads, float)
    lo, hi = np.percentile(image, [1, 99.8])
    gray = (np.clip((image - lo) / max(hi - lo, 1e-9), 0, 1) * 255).astype(np.uint8)
    picture = Image.fromarray(gray).convert("RGB")
    draw = ImageDraw.Draw(picture)
    channels = result["channels"]
    for name in CHANNELS:
        (c0, c1), (r0, r1) = channels[name]["cols"], channels[name]["rows"]
        draw.rectangle([c0, r0, c1, r1], outline=(255, 220, 0), width=2)
        draw.text((c0 + 5, r0 + 5), f"{name}{' (reference)' if name == 'top' else ''}",
                  fill=(255, 220, 0))
    field0 = result["_fields"][0]
    src, dst, keep, which = result["_pairs"]
    first = which == 0
    src, dst, keep = src[first], dst[first], keep[first]
    paired = to_image("top", src, channels)
    for x, y, *_ in field0["_top"] + field0["_bottom"]:
        draw.ellipse([x - 4, y - 4, x + 4, y + 4], outline=(255, 60, 60), width=1)
    for (x, y), k in zip(paired, keep):
        draw.ellipse([x - 5, y - 5, x + 5, y + 5], outline=(0, 255, 0) if k else (255, 0, 0),
                     width=2)
    for (x, y), k in zip(to_image("bottom", dst, channels), keep):
        draw.ellipse([x - 5, y - 5, x + 5, y + 5], outline=(0, 255, 0) if k else (255, 0, 0),
                     width=2)
    grid_img, grid, deformation = result["_grid"]
    span = float(np.abs(deformation).max()) or 1.0
    gain = arrow_gain or 0.4 * GRID_STEP / span
    ends = to_image("top", grid + gain * deformation, channels)  # frame axes -> image
    for (x, y), (x1, y1) in zip(grid_img, ends):
        draw.line([x, y, x1, y1], fill=(0, 220, 255), width=2)
        draw.ellipse([x - 1.5, y - 1.5, x + 1.5, y + 1.5], fill=(0, 220, 255))
    draw.text((channels["top"]["cols"][0] + 5, channels["top"]["rows"][1] - 15),
              f"deformation of the bottom (translation and rotation removed), arrows x{gain:.0f}",
              fill=(0, 220, 255))
    # frame of the channels: x and y axes of the top rectangle from its centre
    cx, cy = channels["top"]["center_xy"]
    for axis, label in (([150.0, 0.0], "x"), ([0.0, 150.0], "y")):
        (x1, y1), = to_image("top", np.array([axis]), channels)
        draw.line([cx, cy, x1, y1], fill=(255, 140, 0), width=3)
        draw.text((x1 + 4, y1 - 12), label, fill=(255, 140, 0))
    picture.save(path)


# ---------------------------------------------------------------- check (as check_calib of
# reconstruction_juliette): beads predicted by the mapping vs measured


def error_stats(errors):
    """Statistics of the errors (n x 2, px, frame axes; nan rows left out): distances |e| and
    the mean vector (a bias)."""
    e = np.asarray(errors, float).reshape(-1, 2)
    e = e[np.isfinite(e).all(axis=1)]
    if not len(e):
        return {"n": 0}
    d = np.hypot(*e.T)
    q1, median, q3, p95 = np.percentile(d, [25, 50, 75, 95])
    return {"n": int(len(e)), "rms_px": float(np.sqrt(np.mean(d ** 2))),
            "median_px": float(median), "p95_px": float(p95), "max_px": float(d.max()),
            "std_px": float(d.std(ddof=1)) if len(d) > 1 else 0.0,
            "interquartile_px": float(q3 - q1),
            "mean_vector_px": e.mean(axis=0).tolist(),
            "rms_xy_px": np.sqrt(np.mean(e ** 2, axis=0)).tolist()}


def mapping_from_file(content):
    """The Mapping (frame axes) of a channels_geometry result."""
    affine, field = content["affine"], content["field"]
    if field["degree"] >= 1:
        return Mapping(affine["matrix"], affine["translation_xy"], np.array(
            field["coefficients_x"]), np.array(field["coefficients_y"]), field["norm_px"],
            field["degree"])
    return Mapping(affine["matrix"], affine["translation_xy"])


def check_images(content, bead_images, radius=CHECK_RADIUS):
    """Beads of new images (not used for the mapping) in both channels (the channels of the
    calibration), each top bead mapped to the bottom channel and compared with the bottom bead
    found there: {"stats", "fields": [{field, beads_top, beads_bottom, pairs, stats}],
    "_points": (top bead image positions, errors)}."""
    channels = content["channels"]
    model = mapping_from_file(content)
    boxes = {n: (*channels[n]["cols"], *channels[n]["rows"]) for n in CHANNELS}
    positions, errors, fields = [], [], []
    for k, image in enumerate(bead_images):
        beads = {n: np.array([b[:2] for b in find_beads(image, boxes[n])]).reshape(-1, 2)
                 for n in CHANNELS}
        top = to_local("top", beads["top"], channels)
        bottom = to_local("bottom", beads["bottom"], channels)
        p = pair(top, bottom, model, radius)
        e = bottom[p[:, 1]] - model(top[p[:, 0]])
        positions.append(beads["top"][p[:, 0]])
        errors.append(e)
        fields.append({"field": k, "beads_top": len(top), "beads_bottom": len(bottom),
                       "pairs": int(len(p)), **error_stats(e)})
    positions, errors = np.concatenate(positions), np.concatenate(errors)
    return {"stats": error_stats(errors), "fields": fields, "_points": (positions, errors)}


def save_check_figure(image, channels, positions, errors, path, title):
    """Check figure: left, the top channel with the error of each bead (arrow, magnified,
    coloured by its length); right, the histogram of the errors |e| (px and nm)."""
    from PIL import Image, ImageDraw
    image = np.asarray(image, float)
    (c0, c1), (r0, r1) = channels["top"]["cols"], channels["top"]["rows"]
    c0, c1, r0, r1 = int(c0), int(np.ceil(c1)), int(r0), int(np.ceil(r1))
    sub = image[r0:r1, c0:c1]
    lo, hi = np.percentile(sub, [1, 99.8])
    gray = (np.clip((sub - lo) / max(hi - lo, 1e-9), 0, 1) * 160).astype(np.uint8)
    map_ = Image.fromarray(gray).convert("RGB")
    ok = np.isfinite(errors).all(axis=1)
    positions, errors = positions[ok], errors[ok]
    d = np.hypot(*errors.T) if len(errors) else np.zeros(0)
    scale = float(np.percentile(d, 95)) if len(d) else 1.0
    gain = 40.0 / max(scale, 1e-3)  # the 95th percentile drawn 40 px long
    draw = ImageDraw.Draw(map_)

    def colour(v):  # blue (0) -> red (2 x the 95th percentile and more)
        f = min(v / (2 * max(scale, 1e-9)), 1.0)
        return int(60 + 195 * f), int(200 * (1 - abs(2 * f - 1))), int(255 * (1 - f))

    for (x, y), e, v in zip(positions, errors, d):
        x, y = x - c0, y - r0
        local = to_local("top", np.array([[x + c0, y + r0]]), channels)
        (x1, y1), = to_image("top", local + gain * e, channels) - [c0, r0]
        draw.line([x, y, x1, y1], fill=colour(v), width=3)
        draw.ellipse([x - 3, y - 3, x + 3, y + 3], outline=colour(v), width=2)
    draw.text((6, 6), f"top channel: error of each bead (bottom measured - predicted), arrows "
                      f"x{gain:.0f}; beads of all the fields on the image of the first",
              fill=(255, 255, 0))
    # histogram
    hw, hh, m = 520, map_.height, 50
    hist = Image.new("RGB", (hw, hh), (255, 255, 255))
    hd = ImageDraw.Draw(hist)
    if len(d):
        top = max(float(d.max()), 1e-3)
        bins = np.linspace(0, top * 1.02, 21)
        counts, _ = np.histogram(d, bins)
        cmax = max(int(counts.max()), 1)
        w = (hw - 2 * m) / len(counts)
        for i, n in enumerate(counts):
            x0 = m + i * w
            hd.rectangle([x0 + 1, hh - m - (hh - 2 * m) * n / cmax, x0 + w - 1, hh - m],
                         fill=(70, 130, 200))
        hd.line([m, hh - m, hw - m, hh - m], fill=(0, 0, 0))
        hd.line([m, m, m, hh - m], fill=(0, 0, 0))
        for k in (0, 5, 10, 15, 20):
            x = m + k * w
            hd.text((x - 10, hh - m + 5), f"{bins[k]:.2f}", fill=(0, 0, 0))
        hd.text((m, hh - m + 20), f"|error| (px; 1 px = {PIXEL_NM:.1f} nm)", fill=(0, 0, 0))
        hd.text((6, m - 12), f"{cmax}", fill=(0, 0, 0))
        s = error_stats(errors)
        text = [title, f"{s['n']} beads", f"rms {s['rms_px']:.3f} px ({s['rms_px'] * PIXEL_NM:.0f} nm)",
                f"median {s['median_px']:.3f} px, 95 % {s['p95_px']:.3f} px",
                f"std {s['std_px']:.3f} px, interquartile {s['interquartile_px']:.3f} px",
                "mean vector %+.3f, %+.3f px" % tuple(s["mean_vector_px"]),
                "rms along x, y %.3f, %.3f px" % tuple(s["rms_xy_px"])]
        for i, line in enumerate(text):
            hd.text((hw - m - 250, m + 14 * i), line, fill=(0, 0, 0))
    picture = Image.new("RGB", (map_.width + hw, hh), (255, 255, 255))
    picture.paste(map_, (0, 0))
    picture.paste(hist, (map_.width, 0))
    picture.save(path)


def check(calibration_path, folder, bead_indexes):
    """Check a channels_geometry result on new bead recordings of folder; writes <first
    index>_channels_geometry_check_<YYYY_MM_DD_HHMMSS>.json and .png there. Returns (path,
    content)."""
    with open(calibration_path) as f:
        content = json.load(f)
    images = [load_mean(folder, i)[0] for i in bead_indexes]
    result = check_images(content, images)
    now = datetime.now()
    name = os.path.join(folder, f"{bead_indexes[0]}_channels_geometry_check_"
                                f"{now:%Y_%m_%d_%H%M%S}")
    out = {"type": "channels_geometry_check", "date": now.isoformat(timespec="seconds"),
           "calibration": calibration_path,
           "bead_recordings": [os.path.join(folder, f"{i}_0.tiff") for i in bead_indexes],
           "errors": "bottom bead measured - predicted by the mapping (px, frame axes)",
           "pixel_nm": PIXEL_NM, "stats": result["stats"], "fields": result["fields"]}
    with open(name + ".json", "w") as f:
        json.dump(out, f, indent=4)
    positions, errors = result["_points"]
    save_check_figure(images[0], content["channels"], positions, errors, name + ".png",
                      "check on new beads")
    return name + ".json", out


def check_summary(out):
    s = out["stats"]
    if not s.get("n"):
        return "Check: no bead pair found (beads in focus? the channels moved?)."
    return (f"Check on {s['n']} beads: error rms {s['rms_px']:.3f} px ({s['rms_px'] * PIXEL_NM:.0f} "
            f"nm), median {s['median_px']:.3f} px, 95 % {s['p95_px']:.3f} px, std "
            f"{s['std_px']:.3f} px, interquartile {s['interquartile_px']:.3f} px; mean vector "
            f"{s['mean_vector_px'][0]:+.3f}, {s['mean_vector_px'][1]:+.3f} px.")


def calibration_file(folder, lamp_index, bead_indexes, result, now=None):
    affine = result["affine"]
    return {
        "type": "channels_geometry",
        "date": (now or datetime.now()).isoformat(timespec="seconds"),
        "lamp_recording": os.path.join(folder, f"{lamp_index}_0.tiff"),
        "bead_recordings": [os.path.join(folder, f"{i}_0.tiff") for i in bead_indexes],
        "axes": {"image": "col to the right, row to the bottom (px)",
                 "top": "reference: x' = col - cx_top, y' = cy_top - row (x right, y up), then "
                        "(x, y) = R(-frame_rotation_deg) (x', y')",
                 "bottom": "x' = col - cx_bottom, y' = row - cy_bottom (x right, y down), then "
                           "(x, y) = R(-frame_rotation_deg) (x', y')",
                 "frame": "the axes of the top rectangle (lamp); angles counterclockwise "
                          "positive in these axes"},
        "frame_rotation_deg": result["channels"]["frame_rotation_deg"],
        "bottom_rotation_deg": affine["rotation_deg"],
        "bottom_deformation": {"scale_x": affine["scale_x"], "scale_y": affine["scale_y"],
                               "shear": affine["shear"], "field_degree": result["field"]["degree"]},
        "bottom_rotation_lamp_deg": result["channels"]["bottom_rotation_lamp_deg"],
        "channels": result["channels"],
        "normalization": result["normalization"],
        "mean_vector_xy": affine["translation_xy"],
        "affine": affine,
        "field": result["field"],
        "local_rotation_deg": result["local_rotation_deg"],
        "residual_px": result["residual_px"],
        "cross_validation": result["cross_validation"],
        "pairs_used": result["pairs_used"], "pairs_found": result["pairs_found"],
        "fields": result["fields"],
        "vector_field": result["vector_field"],
        "notes": "frame_rotation_deg: rotation of the top rectangle (lamp edges, mean of the 4) "
                 "on the camera = the frame of the channels. bottom_rotation_deg: rotation of the "
                 "bottom channel wrt the top one (beads), bottom_deformation the rest of the "
                 "affine (scales along the frame x, y, shear) + the polynomial field; "
                 "bottom_rotation_lamp_deg: the same rotation from the bottom rectangle (check). "
                 "Mapping top (x, y) -> bottom (x, y), frame axes = affine (matrix, translation) "
                 "+ polynomial field (monomials x^i y^j, i + j <= degree, coordinates / norm_px, "
                 "order: degree 0, then 1 (x, y), then 2 (x^2, xy, y^2)...). vector_local_xy = "
                 "mapped - (x, y) at the grid points; deformation_local_xy = the same without "
                 "the translation and the rotation. No image is corrected.",
    }


def run(folder, lamp_index, bead_indexes):
    """Analyse a lamp and bead recordings of folder; writes <first bead index>_channels_geometry
    _<YYYY_MM_DD_HHMMSS>.json and .png there. Returns (path, content)."""
    lamp, _ = load_mean(folder, lamp_index)
    beads = [load_mean(folder, i)[0] for i in bead_indexes]
    result = analyse(lamp, beads)
    now = datetime.now()
    content = calibration_file(folder, lamp_index, bead_indexes, result, now)
    name = os.path.join(folder, f"{bead_indexes[0]}_channels_geometry_{now:%Y_%m_%d_%H%M%S}")
    with open(name + ".json", "w") as f:
        json.dump(content, f, indent=4)
    save_figure(lamp, beads[0], result, name + ".png")
    positions, errors = result["_cv"]
    save_check_figure(beads[0], result["channels"], positions, errors, name + "_check.png",
                      f"beads left out ({result['cross_validation']['groups']})")
    return name + ".json", content


def summary(content):
    """Short text of a result."""
    (vx, vy), r = content["mean_vector_xy"], content["residual_px"]
    a, lr = content["affine"], content["local_rotation_deg"]
    cv = content["cross_validation"]
    norm = content["normalization"]
    shape = content["channels"]["top"]["shape"]
    return (f"Frame (top rectangle) rotation on the camera {content['frame_rotation_deg']:+.3f} "
            f"deg (skew {shape['skew_deg']:+.3f} deg). Bottom rotation wrt top "
            f"{content['bottom_rotation_deg']:+.3f} deg (beads; lamp "
            f"{content['bottom_rotation_lamp_deg']:+.3f} deg). "
            f"Lamp normalization: bottom / top = {norm['bottom_over_top']:.4f} (coefficients top "
            f"{norm['coefficients']['top']:.4f}, bottom {norm['coefficients']['bottom']:.4f}). "
            f"{content['pairs_used']} bead pairs ({content['pairs_found']} found). Mean vector "
            f"(bottom - top, frame axes) {vx:+.2f}, {vy:+.2f} px; "
            f"scales {a['scale_x']:.4f}, {a['scale_y']:.4f}; "
            f"shear {a['shear']:+.4f}. Local rotation {lr['min']:+.3f} to {lr['max']:+.3f} deg. "
            f"Residual rms {r['affine_rms']:.3f} px (affine), {r['final_rms']:.3f} px (with the "
            f"field, degree {content['field']['degree']}). Beads left out ({cv['groups']}): "
            f"error rms {cv['rms_px']:.3f} px, median {cv['median_px']:.3f} px, 95 % "
            f"{cv['p95_px']:.3f} px; by field degree "
            + ", ".join(f"{d}: {v:.3f}" for d, v in cv["rms_px_by_degree"].items())
            + " px (0 = affine only).")


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "check":
        path, out = check(sys.argv[2], sys.argv[3], [int(i) for i in sys.argv[4:]])
        print(check_summary(out))
        print("written:", path)
        sys.exit()
    if len(sys.argv) < 4:
        sys.exit("Usage: python channels_geometry.py <folder> <lamp index> <bead index>...\n"
                 "       python channels_geometry.py check <calibration json> <folder> "
                 "<bead index>...")
    path, content = run(sys.argv[1], int(sys.argv[2]), [int(i) for i in sys.argv[3:]])
    print(summary(content))
    print("written:", path)
