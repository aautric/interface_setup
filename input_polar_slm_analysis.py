"""
Analysis of the input_polar_SLM calibration (calibration.py): polarization of the light reaching
the SLM, for each Orca pixel of the BFP disks, and its stability in time.

The recording (one run): the analyzer (quarter-wave plate turned by hand, then the polarizer) is
put right before the SLM; for each angle of the plate (QWP_ANGLES, as slm_phase), one acquisition
of N Orca frames (time series), a flat mask on the SLM (the same for all the angles). After the
polarizer the light is linear along its axis whatever the plate angle: what comes after (SLM,
beam splitter, mirrors) changes the intensity on the Orca by the same factor for all the angles.

Polarimetry (slm_phase_analysis, same formulas as polarimetre.py of the polarimetry repo):
Fourier coefficients of the intensity over the 9 plate angles, then S0, S1, S2, S3 in the frame of
the analyzer (polarizer along y), with the zero of the plate (offset of the quarter-wave plate in
its mount) either fixed (default: the one fitted by the latest slm_phase result, same mount) or
fitted on this recording (needs elliptical light: poorly defined for nearly linear light).
    azimuth     = 0.5 atan2(S2, S1)        (deg, of the major axis, in ]-90, 90])
    ellipticity = 0.5 asin(S3 / |S|)       (deg, 0 linear, +-45 circular)
    degree of polarization = |S| / S0
1. Per pixel: the mean of the N frames of each angle (photon noise averaged), dark level
   (median away from the disks) subtracted.
2. Per disk: the summed intensity of the disk, the same polarimetry, for each frame k (frame k of
   each angle): the polarization vs time (fluctuations); and per angle the intensity drift during
   its N frames (linear slope) and the rms fluctuation around it. A drift between the angles
   cannot be separated from the polarization (not measured).

Results (next to the recordings, <first>-<last>_input_polar_SLM_<YYYY_MM_DD_HHMMSS>):
    .json  summary: settings, plate zero, per disk the Stokes vector, azimuth, ellipticity,
           degree of polarization (whole disk, spread over its pixels), their fluctuation in time,
           the intensity drift of each angle
    .npz   per lit Orca pixel: rows, cols, disk, s0, s1, s2, s3, azimuth, ellipticity, dop;
           per disk and frame: intensity (angles x frames x disks), azimuth, ellipticity, dop
    .png   maps of the azimuth, ellipticity and degree of polarization, the azimuth and
           ellipticity of each disk vs frame

Only numpy, scipy.ndimage and tifffile (np.linalg and matrix products crash setup_env).

Command line: python input_polar_slm_analysis.py <folder> <index of any acquisition of the run>
[bin] [right|both] [plate zero in deg | fit]
"""

import glob
import json
import os
import sys
from datetime import datetime

import numpy as np
from scipy import ndimage

import saving
from slm_phase_analysis import (QWP_ANGLES, binned, fourier, lit_pixels, open_stack, plate_offset,
                                rotate_offset, stokes)

NAME = "input_polar_SLM"
CHANNELS = {"both": "Both channels (whole Orca image)",
            "right": "Right channel only (disk centred in the right half of the Orca)"}
DEFAULT_CHANNELS = "both"
FRAMES = 20  # default frames per angle


# ---------------------------------------------------------------- loading


def load_run(folder, index):
    """The acquisitions of the run of acquisition index, [{"index", "angle", "path",
    "calibration"}] in recording order (an angle recorded again: the last one)."""
    def calibration(i):
        with open(os.path.join(folder, f"{i}.json")) as f:
            return json.load(f)["acquisition"].get("calibration", {})

    run = calibration(index).get("run")
    if calibration(index).get("name") != NAME or run is None:
        raise ValueError(f"Acquisition {index} of {folder} is not an {NAME} recording.")
    items = []
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json") or not name[:-5].isdigit():
            continue
        i = int(name[:-5])
        c = calibration(i)
        if c.get("name") == NAME and c.get("run") == run:
            items.append({"index": i, "angle": c["qwp_angle_deg"],
                          "path": os.path.join(folder, f"{i}_0.tiff"), "calibration": c})
    last = {item["angle"]: item for item in sorted(items, key=lambda item: item["index"])}
    return sorted(last.values(), key=lambda item: item["index"])


def slm_phase_plate_zero(root=None):
    """(plate zero in deg, file) of the newest slm_phase result, (None, None) if there is none."""
    paths = glob.glob(os.path.join(root or saving.CALIBRATION_ROOT, "slm_phase", "*",
                                   "*_slm_phase_*.json"))
    for path in sorted(paths, key=lambda p: os.path.splitext(os.path.basename(p))[0][-17:],
                       reverse=True):
        try:
            with open(path) as f:
                return float(json.load(f)["plate_offset_deg"]), path
        except (OSError, KeyError, ValueError):
            continue
    return None, None


# ---------------------------------------------------------------- polarization


def ellipse(s0, s1, s2, s3):
    """Azimuth (deg, ]-90, 90]), ellipticity (deg) and degree of polarization."""
    norm = np.sqrt(s1 ** 2 + s2 ** 2 + s3 ** 2)
    azimuth = np.degrees(0.5 * np.arctan2(s2, s1))
    ellipticity = np.degrees(0.5 * np.arcsin(np.clip(s3 / np.where(norm > 0, norm, np.nan), -1, 1)))
    dop = norm / np.where(s0 > 0, s0, np.nan)
    return azimuth, ellipticity, dop


def polarimetry(intensity, angles, retardance_waves, offset):
    """Stokes (S0, S1, S2, S3) of intensities (angles first axis), plate zero offset (rad)."""
    p0, p2, q2, p4, q4 = fourier(intensity, angles)
    p2, q2, p4, q4 = rotate_offset(p2, q2, p4, q4, offset)
    return stokes(p0, q2, p4, q4, retardance_waves)


def circular_spread(azimuth_deg):
    """Spread (deg) of axial angles (period 180 deg): circular std of 2 x angle, halved."""
    a = np.radians(2 * np.asarray(azimuth_deg, dtype=float))
    a = a[np.isfinite(a)]
    if len(a) == 0:
        return float("nan")
    r = np.hypot(np.cos(a).mean(), np.sin(a).mean())
    return float(np.degrees(np.sqrt(max(-2 * np.log(max(r, 1e-12)), 0))) / 2)


def axial_mean(azimuth_deg):
    a = np.radians(2 * np.asarray(azimuth_deg, dtype=float))
    a = a[np.isfinite(a)]
    return float(np.degrees(np.arctan2(np.sin(a).mean(), np.cos(a).mean())) / 2)


def trend(values):
    """Linear drift over the frames (fraction of the mean, first to last frame) and rms
    fluctuation around the line (fraction of the mean)."""
    y = np.asarray(values, dtype=float)
    mean = y.mean()
    if len(y) < 2 or mean <= 0:
        return 0.0, 0.0
    t = np.arange(len(y)) - (len(y) - 1) / 2
    slope = (t * (y - mean)).sum() / (t * t).sum()
    residual = y - mean - slope * t
    return float(slope * (len(y) - 1) / mean), float(np.sqrt((residual ** 2).mean()) / mean)


# ---------------------------------------------------------------- analysis


def analyse(items, retardance_waves, bin_size=1, offset_deg=None, progress=None,
            channels=DEFAULT_CHANNELS):
    """
    items: load_run; returns (result dict, per-pixel arrays dict).
    offset_deg: zero of the plate (deg), None: fitted on this recording.
    channels: "both" (whole Orca image) or "right" (disks of the right half only).
    """
    if channels not in CHANNELS:
        raise ValueError(f"channels: {channels}, expected one of {list(CHANNELS)}")
    say = progress or (lambda text: None)
    angles = [item["angle"] for item in items]
    if sorted(angles) != QWP_ANGLES:
        raise ValueError(f"Plate angles recorded: {sorted(angles)}, needed: {QWP_ANGLES}.")
    stacks = [open_stack(item["path"]) for item in items]
    n_frames = min(stack.shape[0] for stack in stacks)
    if any(stack.shape[0] != n_frames for stack in stacks):
        say(f"Not the same number of frames for all the angles: the first {n_frames} used.")

    means = []
    for item, stack in zip(items, stacks):
        say(f"Mean of the frames, plate {item['angle']} deg...")
        total = np.zeros(stack.shape[1:], dtype=np.float64)
        for k in range(n_frames):
            total += stack[k]
        means.append(binned((total / n_frames).astype(np.float32), bin_size))
    means = np.stack(means)

    say("Finding the BFP disks...")
    reference = means.mean(axis=0)
    all_disks, n_all = lit_pixels(reference)
    away = ~ndimage.binary_dilation(all_disks > 0, iterations=max(2, 20 // bin_size))
    dark = float(np.median(reference[away]))
    keep = list(range(1, n_all + 1))
    if channels == "right":
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

    say("Polarization of each pixel...")
    intensity = means[:, rows, cols] - dark
    if offset_deg is None:
        p0, p2, q2, p4, q4 = fourier(intensity, angles)
        offset = plate_offset(p2, q2)
    else:
        offset = np.radians(offset_deg)
    s0, s1, s2, s3 = polarimetry(intensity, angles, retardance_waves, offset)
    azimuth, ellipticity, dop = ellipse(s0, s1, s2, s3)

    say("Intensity of each disk vs time...")
    # summed intensity of each disk, per angle and frame (dark subtracted)
    full_disks = np.kron(disks, np.ones((bin_size, bin_size), dtype=disks.dtype)) \
        if bin_size > 1 else disks
    sizes = np.array([(full_disks == d).sum() for d in range(1, n_disks + 1)], dtype=float)
    per_frame = np.zeros((len(stacks), n_frames, n_disks))
    shape = full_disks.shape
    for a, stack in enumerate(stacks):
        say(f"Intensity of each disk vs time, plate {angles[a]} deg...")
        for k in range(n_frames):
            frame = np.asarray(stack[k, :shape[0], :shape[1]], dtype=np.float64)
            per_frame[a, k] = ndimage.sum(frame, full_disks, range(1, n_disks + 1))
    per_frame -= sizes * dark / bin_size ** 2
    t_s0, t_s1, t_s2, t_s3 = polarimetry(per_frame, angles, retardance_waves, offset)
    t_azimuth, t_ellipticity, t_dop = ellipse(t_s0, t_s1, t_s2, t_s3)  # (frames, disks)

    say("Summary...")
    result = {"n_pixels": int(len(rows)), "bin": bin_size, "channels": channels,
              "frames_per_angle": int(n_frames),
              "plate_offset_deg": float(np.degrees(offset)),
              "plate_offset_fitted": offset_deg is None,
              "dark_per_orca_pixel": dark / bin_size ** 2, "disks": []}
    for number in range(1, n_disks + 1):
        mine = pixel_disk == number
        d = number - 1
        whole = polarimetry(per_frame[:, :, d].mean(axis=1), angles, retardance_waves, offset)
        w_azimuth, w_ellipticity, w_dop = ellipse(*whole)
        drifts = [trend(per_frame[a, :, d]) for a in range(len(stacks))]
        result["disks"].append({
            "disk": number, "n_pixels": int(mine.sum()),
            "center_orca_xy": [float(cols[mine].mean() * bin_size),
                               float(rows[mine].mean() * bin_size)],
            "stokes_normalized": [float(whole[i] / whole[0]) for i in (1, 2, 3)],
            "azimuth_deg": float(w_azimuth), "ellipticity_deg": float(w_ellipticity),
            "polarization_degree": float(w_dop),
            "pixels": {"azimuth_mean_deg": axial_mean(azimuth[mine]),
                       "azimuth_spread_deg": circular_spread(azimuth[mine]),
                       "ellipticity_median_deg": float(np.nanmedian(ellipticity[mine])),
                       "ellipticity_iqr_deg": float(np.subtract(*np.nanpercentile(
                           ellipticity[mine], [75, 25]))),
                       "polarization_degree_median": float(np.nanmedian(dop[mine]))},
            "time": {"azimuth_std_deg": circular_spread(t_azimuth[:, d]),
                     "ellipticity_std_deg": float(np.nanstd(t_ellipticity[:, d])),
                     "polarization_degree_std": float(np.nanstd(t_dop[:, d])),
                     "intensity_drift_per_angle": [round(x, 5) for x, _ in drifts],
                     "intensity_fluctuation_rms_per_angle": [round(x, 5) for _, x in drifts]}})
    pixels = {"rows": (rows * bin_size).astype(np.int32), "cols": (cols * bin_size).astype(np.int32),
              "disk": pixel_disk.astype(np.int8), "bin": bin_size,
              "s0": s0.astype(np.float32), "s1": s1.astype(np.float32),
              "s2": s2.astype(np.float32), "s3": s3.astype(np.float32),
              "azimuth_deg": azimuth.astype(np.float32),
              "ellipticity_deg": ellipticity.astype(np.float32), "dop": dop.astype(np.float32),
              "angles": np.array(angles, dtype=np.int16),
              "disk_intensity": per_frame.astype(np.float32),
              "disk_azimuth_deg": t_azimuth.astype(np.float32),
              "disk_ellipticity_deg": t_ellipticity.astype(np.float32),
              "disk_dop": t_dop.astype(np.float32)}
    return result, pixels


# ---------------------------------------------------------------- figure

# colour scales of the maps (as matplotlib's, written here: no matplotlib in setup_env)
COOLWARM = np.array([[59, 76, 192], [141, 176, 254], [221, 221, 221], [244, 154, 123],
                     [180, 4, 38]], dtype=float)


def scale_colors(values, low, high, name):
    """RGB (uint8) of values between low and high; NaN black. name: "hsv" (cyclic: low and high
    the same colour), "spring" (magenta -> yellow) or "coolwarm" (blue - gray - red)."""
    t = np.clip((values - low) / (high - low if high > low else 1), 0, 1)
    t = np.nan_to_num(t)
    if name == "hsv":
        h = (t % 1) * 6
        k = np.floor(h).astype(int) % 6
        f = h - np.floor(h)
        one, zero = np.ones_like(f), np.zeros_like(f)
        r = np.choose(k, [one, 1 - f, zero, zero, f, one])
        g = np.choose(k, [f, one, one, 1 - f, zero, zero])
        b = np.choose(k, [zero, zero, f, one, one, 1 - f])
        rgb = np.stack([r, g, b], axis=-1) * 255
    elif name == "spring":
        rgb = np.stack([np.ones_like(t), t, 1 - t], axis=-1) * 255
    elif name == "coolwarm":
        position = t * (len(COOLWARM) - 1)
        k = np.minimum(np.floor(position).astype(int), len(COOLWARM) - 2)
        f = (position - k)[..., None]
        rgb = COOLWARM[k] * (1 - f) + COOLWARM[k + 1] * f
    else:
        raise ValueError(f"colour scale {name}")
    rgb[np.isnan(values)] = 0
    return rgb.astype(np.uint8)


def save_figure(result, pixels, path):
    """Maps of the azimuth (hsv, cyclic over 180 deg), the ellipticity (coolwarm, centred on 0)
    and the degree of polarization (spring), with their colour bars; azimuth and ellipticity of
    each disk vs frame."""
    from PIL import Image, ImageDraw
    size = 400
    picture = Image.new("RGB", (2 * size, 2 * size), "white")
    draw = ImageDraw.Draw(picture)
    rows, cols = pixels["rows"] // pixels["bin"], pixels["cols"] // pixels["bin"]
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1

    def percentile(values, q, default):
        finite = values[np.isfinite(values)]
        return float(np.percentile(finite, q)) if len(finite) else default

    ellipticity = pixels["ellipticity_deg"].astype(float)
    e_span = max(abs(percentile(ellipticity, 1, -1)), abs(percentile(ellipticity, 99, 1)), 0.1)
    dop = pixels["dop"].astype(float)
    dop_low = min(percentile(dop, 1, 0.9), 0.99)
    # (title, values, low, high, scale, unit, result key)
    maps = [("azimuth", (pixels["azimuth_deg"].astype(float) + 90) % 180 - 90, -90, 90, "hsv",
             "deg", "azimuth_deg"),
            ("ellipticity", ellipticity, -e_span, e_span, "coolwarm", "deg", "ellipticity_deg"),
            ("degree of polarization", dop, dop_low, 1, "spring", "", "polarization_degree")]
    for (title, values, low, high, scale_name, unit, result_key), (x, y) in zip(
            maps, ((0, 0), (size, 0), (0, size))):
        image = np.full((r1 - r0, c1 - c0), np.nan)
        image[rows - r0, cols - c0] = values
        tile = Image.fromarray(scale_colors(image, low, high, scale_name))
        scale = min((size - 20) / tile.width, (size - 110) / tile.height)
        tile = tile.resize((max(1, int(tile.width * scale)), max(1, int(tile.height * scale))),
                           Image.NEAREST)
        picture.paste(tile, (x + 10, y + 40))
        draw.text((x + 10, y + 5), f"{title} ({scale_name})", fill="black")
        values_text = ", ".join(f"disk {d['disk']} {d[result_key]:+.2f}" for d in result["disks"])
        draw.text((x + 10, y + 20), values_text + (f" {unit}" if unit else ""), fill="black")
        # colour bar
        bar_top, bar_width = y + 40 + tile.height + 8, size - 60
        bar = scale_colors(np.tile(np.linspace(low, high, bar_width), (12, 1)), low, high,
                           scale_name)
        picture.paste(Image.fromarray(bar), (x + 10, bar_top))
        for value, anchor in ((low, x + 10), ((low + high) / 2, x + 10 + bar_width / 2 - 12),
                              (high, x + bar_width - 15)):
            draw.text((anchor, bar_top + 15), f"{value:.3g}", fill="black")
    # time: azimuth and ellipticity of each disk vs frame, relative to their mean
    left, top, width, height = size + 50, size + 30, size - 70, size - 80
    curves = []
    for d in range(pixels["disk_azimuth_deg"].shape[1]):
        a = pixels["disk_azimuth_deg"][:, d]
        a = (a - axial_mean(a) + 90) % 180 - 90
        e = pixels["disk_ellipticity_deg"][:, d]
        curves += [(f"disk {d + 1} azimuth", a), (f"disk {d + 1} ellipticity", e - np.nanmean(e))]
    span = max([float(np.nanmax(np.abs(c))) for _, c in curves if np.isfinite(c).any()] + [0.1]) * 1.1
    draw.rectangle([left, top, left + width, top + height], outline="black")
    draw.line([left, top + height / 2, left + width, top + height / 2], fill=(200, 200, 200))
    draw.text((left - 45, top - 5), f"+{span:.2f}", fill="black")
    draw.text((left - 45, top + height - 8), f"-{span:.2f}", fill="black")
    draw.text((left, top - 22), "change vs mean (deg) per frame", fill="black")
    n = pixels["disk_azimuth_deg"].shape[0]
    draw.text((left + width / 2 - 15, top + height + 5), "frame", fill="black")
    colors = [(211, 47, 47), (239, 154, 154), (25, 118, 210), (144, 202, 249),
              (56, 142, 60), (165, 214, 167)]
    for k, ((label, values), color) in enumerate(zip(curves, colors)):
        points = [(left + i / max(n - 1, 1) * width, top + height / 2 - v / span * height / 2)
                  for i, v in enumerate(values) if np.isfinite(v)]
        if len(points) > 1:
            draw.line(points, fill=color, width=2)
        draw.text((left + 5 + 160 * (k % 2), top + height + 22 + 12 * (k // 2)), label,
                  fill=color)
    picture.save(path)


# ---------------------------------------------------------------- run


def run(folder, index, bin_size=1, offset_deg=None, progress=None, channels=DEFAULT_CHANNELS,
        offset_source=None):
    """Analyse the run of acquisition index; writes the .json, .npz and .png next to it.
    offset_source: where the fixed plate zero comes from (written in the result).
    Returns (json path, summary)."""
    items = load_run(folder, index)
    settings = items[0]["calibration"]
    result, pixels = analyse(items, settings["qwp_retardance_waves"], bin_size, offset_deg,
                             progress, channels)
    now = datetime.now()
    first, last = items[0]["index"], items[-1]["index"]
    name = os.path.join(folder, f"{first}-{last}_{NAME}_{now:%Y_%m_%d_%H%M%S}")
    summary = {
        "type": "input_polar_SLM_calibration",
        "date": now.isoformat(timespec="seconds"),
        "recordings": [{"index": item["index"], "qwp_angle_deg": item["angle"]}
                       for item in items],
        "folder": folder,
        "light_path": settings.get("light_path"),
        "wavelength_nm": settings.get("wavelength_nm"),
        "qwp_retardance_waves": settings.get("qwp_retardance_waves"),
        "slm_flat_level": settings.get("slm_flat_level"),
        "plate_offset_source": "fitted on this recording" if offset_deg is None else offset_source,
        "per_pixel_file": os.path.basename(name + ".npz"),
        **result,
        "notes": "Polarization of the light reaching the SLM (analyzer right before it), in the "
                 "frame of the analyzer (polarizer along y, azimuth from its x axis). Disks "
                 "sorted left to right on the Orca. Time: frame k of each angle combined.",
    }
    np.savez_compressed(name + ".npz", **pixels)
    with open(name + ".json", "w") as f:
        json.dump(summary, f, indent=4)
    save_figure(result, pixels, name + ".png")
    return name + ".json", summary


def summary_lines(summary):
    """Short text lines of a result (GUI and command line)."""
    lines = [f"Plate zero {summary['plate_offset_deg']:+.2f}° "
             f"({summary.get('plate_offset_source') or 'fixed'}), {summary['frames_per_angle']} "
             f"frames per angle, {summary['n_pixels']} lit pixels ({summary['bin']} x "
             f"{summary['bin']})."]
    for d in summary["disks"]:
        p, t = d["pixels"], d["time"]
        drift = max(d["time"]["intensity_drift_per_angle"], key=abs)
        lines.append(
            f"Disk {d['disk']} (Orca x {d['center_orca_xy'][0]:.0f}): azimuth "
            f"{d['azimuth_deg']:+.2f}°, ellipticity {d['ellipticity_deg']:+.2f}°, degree of "
            f"polarization {d['polarization_degree']:.3f}; over the disk: azimuth spread "
            f"{p['azimuth_spread_deg']:.2f}°, ellipticity IQR {p['ellipticity_iqr_deg']:.2f}°; in "
            f"time: azimuth {t['azimuth_std_deg']:.3f}°, ellipticity {t['ellipticity_std_deg']:.3f}° "
            f"rms, largest intensity drift in a series {100 * drift:+.1f} %")
    return lines


if __name__ == "__main__":
    folder, index = sys.argv[1], int(sys.argv[2])
    bin_size = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    channels = sys.argv[4] if len(sys.argv) > 4 else DEFAULT_CHANNELS
    zero = sys.argv[5] if len(sys.argv) > 5 else None
    if zero == "fit":
        offset, source = None, None
    elif zero is not None:
        offset, source = float(zero), "command line"
    else:
        offset, source = slm_phase_plate_zero()
        source = f"slm_phase {os.path.basename(source)}" if source else None
    path, summary = run(folder, index, bin_size, offset, print, channels, source)
    print("\n".join(summary_lines(summary)))
    print("written:", path)
