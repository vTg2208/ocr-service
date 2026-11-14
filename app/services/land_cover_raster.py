"""Bounded Sentinel-2 L1C acquisition and real, pinned Dynamic World inference."""

from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import geometry_mask, shapes
from rasterio.io import MemoryFile
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_geom
from rasterio.windows import Window, from_bounds
from shapely.geometry import MultiPolygon, mapping, shape
from shapely.ops import unary_union


MODEL_VERSION = "dynamic-world-626dc911-v1"
SOURCE_REVISION = "626dc9114145f31534c5d6cebd28816f81c5e284"
WEIGHTS_SHA256 = "a70b3a60ceec1bd468dee75b94d30c511ecfbf19b31cd4a3c205c3dc2b8efb94"
BAND_KEYS = ("blue", "green", "red", "rededge1", "rededge2", "rededge3", "nir", "swir16", "swir22")
CLASS_NAMES = ("water", "trees", "grass", "flooded_vegetation", "crops", "shrub_and_scrub", "built_area", "bare_ground", "snow_and_ice")
COLORS = ("#419bdf", "#397d49", "#88b053", "#7a87c6", "#e49635", "#dfc35a", "#c4281b", "#a59b8f", "#b39fe1")
NORM_PERCENTILES = np.array([
    [1.7417268007636313, 2.023298706048351], [1.7261204997060209, 2.038905204308012],
    [1.6798346251414997, 2.179592821212937], [1.7734969472909623, 2.2890068333026603],
    [2.289154079164943, 2.6171674549378166], [2.382939712192371, 2.773418590375327],
    [2.3828939530384052, 2.7578332604178284], [2.1952484264967844, 2.789092484314204],
    [1.554812948247501, 2.4140534947492487],
], dtype="float32")


class LandCoverError(ValueError):
    pass


def weights_ready(path):
    value = Path(path)
    return value.is_file() and hashlib.sha256(value.read_bytes()).hexdigest() == WEIGHTS_SHA256


def asset_url(asset, *, allowed_hosts):
    """Only the public Sentinel L1C bucket may be rewritten from s3 to HTTPS."""
    href = str(asset.get("href", ""))
    parsed = urlparse(href)
    if parsed.scheme == "s3" and parsed.netloc == "sentinel-s2-l1c":
        href = "https://sentinel-s2-l1c.s3.amazonaws.com" + parsed.path
        parsed = urlparse(href)
    if (parsed.scheme != "https" or parsed.hostname not in allowed_hosts
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or ".." in parsed.path.split("/")):
        raise LandCoverError("Imagery source is not an allowed public satellite asset.")
    return href


class SentinelLandCoverReader:
    """Read a 10 m grid plus context, checking allocation bounds before reads."""

    def __init__(self, *, max_pixels=1_000_000, opener=None):
        self.max_pixels = max_pixels
        self.opener = opener or rasterio.open

    def read(self, assets, geometry, quality_asset):
        allowed = {"sentinel-s2-l1c.s3.amazonaws.com", "sentinel-cogs.s3.us-west-2.amazonaws.com",
                   "e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com"}
        with ExitStack() as stack:
            stack.enter_context(rasterio.Env(GDAL_HTTP_TIMEOUT="30", GDAL_HTTP_MAX_RETRY="2",
                                             GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"))
            reference = stack.enter_context(self.opener(asset_url(assets[BAND_KEYS[0]], allowed_hosts=allowed)))
            if reference.crs is None or not reference.crs.is_projected:
                raise LandCoverError("Sentinel imagery requires a projected 10 m CRS.")
            if not (9.9 <= abs(reference.transform.a) <= 10.1 and 9.9 <= abs(reference.transform.e) <= 10.1):
                raise LandCoverError("The source grid must have 10 m pixels.")
            projected = transform_geom("EPSG:4326", reference.crs, geometry)
            bounds = shape(projected).bounds
            raw = from_bounds(*bounds, transform=reference.transform)
            left, top = int(np.floor(raw.col_off)) - 32, int(np.floor(raw.row_off)) - 32
            right = int(np.ceil(raw.col_off + raw.width)) + 32
            bottom = int(np.ceil(raw.row_off + raw.height)) + 32
            width, height = right - left, bottom - top
            if width < 1 or height < 1 or width * height > self.max_pixels:
                raise LandCoverError("Boundary exceeds the bounded land-cover raster size.")
            if left < 0 or top < 0 or right > reference.width or bottom > reference.height:
                raise LandCoverError("A single scene does not fully cover this boundary and its context.")
            window = Window(left, top, width, height)
            transform = reference.window_transform(window)
            profile = dict(driver="GTiff", width=width, height=height, crs=reference.crs, transform=transform)
            arrays = []
            for key in BAND_KEYS:
                asset = assets[key]
                dataset = stack.enter_context(self.opener(asset_url(asset, allowed_hosts=allowed)))
                vrt = stack.enter_context(WarpedVRT(dataset, crs=reference.crs, transform=transform,
                    width=width, height=height, resampling=Resampling.bilinear, dtype="float32", nodata=np.nan))
                array = vrt.read(1, masked=True).astype("float32").filled(np.nan)
                metadata = (asset.get("raster:bands") or [{}])[0]
                # Convert provider DN to TOA reflectance, then to the model's 0..10000 scale.
                scale, offset = float(metadata.get("scale", 0.0001)), float(metadata.get("offset", 0))
                if not np.isfinite([scale, offset]).all() or scale <= 0:
                    raise LandCoverError("Invalid Sentinel radiometric scale/offset.")
                arrays.append((array * scale + offset) * 10000)
            quality = stack.enter_context(self.opener(asset_url(quality_asset, allowed_hosts=allowed)))
            qa = stack.enter_context(WarpedVRT(quality, crs=reference.crs, transform=transform,
                width=width, height=height, resampling=Resampling.nearest, nodata=0)).read(1)
            data = np.stack(arrays)
            inside = geometry_mask([projected], (height, width), transform, invert=True)
            # SCL from the matching L2A acquisition: remove no-data, defects, shadows, and clouds.
            clear = ~np.isin(qa, [0, 1, 3, 8, 9, 10])
            valid = inside & clear & np.isfinite(data).all(axis=0)
            return data, inside, valid, profile


class DynamicWorldPredictor:
    def __init__(self, weights_path):
        if not weights_ready(weights_path):
            raise LandCoverError("Install the checksum-verified Dynamic World weights first.")
        try:
            import torch
        except ImportError as error:
            raise LandCoverError("Install requirements-ml.txt in the worker environment.") from error
        from app.services.vendor.dynamic_world import DynamicWorld
        self.model = DynamicWorld.from_pretrained(weights_path)
        self.runtime = {"torch": torch.__version__, "device": "cpu", "numpy": np.__version__}

    def predict(self, raw):
        import torch
        if raw.ndim != 3 or raw.shape[0] != len(BAND_KEYS):
            raise LandCoverError("Dynamic World requires all nine bands in the documented order.")
        # Brown et al. log-percentile normalization; no per-scene fitted statistics.
        x = np.log(np.maximum(np.nan_to_num(raw, nan=0), 0) * .005 + 1)
        x = (x - NORM_PERCENTILES[:, 0, None, None]) / NORM_PERCENTILES[:, 1, None, None]
        x = 1 / (1 + np.exp(-(x * 5 - 1)))
        _, height, width = x.shape
        probabilities = np.empty((9, height, width), dtype="float32")
        # Halo context avoids artificial seams; even origins preserve downsample alignment.
        with torch.inference_mode():
            for y in range(0, height, 256):
                for col in range(0, width, 256):
                    y1, x1 = min(y + 256, height), min(col + 256, width)
                    y0, x0 = max(0, y - 32), max(0, col - 32)
                    patch = x[:, y0:min(y1 + 32, height), x0:min(x1 + 32, width)]
                    pad_h, pad_w = max(0, 4-patch.shape[1]), max(0, 4-patch.shape[2])
                    patch = np.pad(patch, ((0, 0), (0, pad_h), (0, pad_w)), mode="edge")
                    out = self.model(torch.from_numpy(patch.copy()).unsqueeze(0)).softmax(dim=1)[0].cpu().numpy()
                    probabilities[:, y:y1, col:x1] = out[:, y-y0:y1-y0, col-x0:x1-x0]
        if not np.isfinite(probabilities).all():
            raise LandCoverError("Model returned non-finite probabilities.")
        return probabilities


def summarize(probabilities, inside, valid, profile, target_geometry):
    if probabilities.shape != (9, *inside.shape) or valid.shape != inside.shape:
        raise LandCoverError("Inference output does not match the georeferenced input.")
    if not np.isfinite(probabilities).all() or np.any(probabilities < 0) or not np.allclose(probabilities.sum(axis=0), 1, atol=1e-4):
        raise LandCoverError("Invalid model probability output.")
    labels = probabilities.argmax(axis=0).astype("uint8")
    confidence = probabilities.max(axis=0)
    labels[~valid] = 255
    pixel_area = abs(profile["transform"].a * profile["transform"].e)
    total = int(valid.sum())
    rows = []
    for index, name in enumerate(CLASS_NAMES):
        selected = (labels == index) & valid
        count = int(selected.sum())
        rows.append({"class_id": index, "class_name": name, "color": COLORS[index], "pixels": count,
            "estimated_area_sqm": round(count * pixel_area, 2), "fraction": count / total if total else 0,
            "mean_probability": float(confidence[selected].mean()) if count else None})
    features, pieces = [], []
    fragmented = False
    for geometry, value in shapes(labels, mask=valid, transform=profile["transform"]):
        if len(pieces) >= 10000:
            fragmented = True
            break
        pieces.append((int(value), shape(geometry)))
    if not fragmented:
        target = shape(target_geometry)
        for row in rows:
            members = [piece for value, piece in pieces if value == row["class_id"]]
            if not members:
                continue
            dissolved = unary_union(members).simplify(1, preserve_topology=True)
            clipped = shape(transform_geom(profile["crs"], "EPSG:4326", mapping(dissolved))).intersection(target)
            polygons = [clipped] if clipped.geom_type == "Polygon" else [g for g in getattr(clipped, "geoms", []) if g.geom_type == "Polygon"]
            if polygons:
                features.append({"type": "Feature", "geometry": mapping(MultiPolygon(polygons)), "properties": row})
    if len(json.dumps(features)) > 2_000_000:
        features = []
        fragmented = True
    warnings = ["10 m land-cover estimates; not a cadastral survey or verified asset inventory."]
    if np.any(inside & ~valid):
        warnings.append("Cloud, shadow, defective, or missing pixels were excluded; fractions use valid pixels only.")
    if total and float((confidence[valid] < .5).mean()) > .25:
        warnings.append("More than a quarter of valid pixels have maximum model probability below 0.5.")
    if fragmented:
        warnings.append("Vector complexity limit reached; use the raster and summary for review.")
    summary = {"classes": rows, "valid_pixels": total, "target_pixels": int(inside.sum()),
        "valid_fraction": total / int(inside.sum()) if inside.any() else 0,
        "estimated_area_sqm": total * pixel_area, "resolution_metres": 10,
        "warnings": warnings, "accuracy": None, "evaluation_status": "not_evaluated_on_local_ground_truth"}
    return labels, summary, {"type": "FeatureCollection", "features": features}


def encode_raster(data, profile, *, nodata, descriptions=None):
    array = data if data.ndim == 3 else data[None]
    with MemoryFile() as memory:
        with memory.open(**profile, count=array.shape[0], dtype=str(array.dtype), nodata=nodata, compress="deflate") as dst:
            dst.write(array)
            if descriptions:
                dst.descriptions = tuple(descriptions)
        return memory.read()


def encode_preview(labels):
    from io import BytesIO
    from PIL import Image
    rgba = np.zeros((*labels.shape, 4), dtype="uint8")
    for index, color in enumerate(COLORS):
        rgba[labels == index] = (*bytes.fromhex(color[1:]), 210)
    output = BytesIO()
    Image.fromarray(rgba, "RGBA").save(output, format="PNG")
    return output.getvalue()
