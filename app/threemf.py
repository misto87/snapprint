"""3MF preprocessing before handing a project to the Snapmaker Orca CLI.

Snapmaker Orca 2.4.0's CLI segfaults on every 3MF that carries plate data: its
wipe-tower check calls PartPlate::get_extruders_under_cli(), which reaches into
GUI_App (null in CLI mode). We therefore drop the <plate> blocks from
Metadata/model_settings.config. Geometry, painting and per-object filament
assignments are untouched. For multi-plate projects only the chosen plate's
objects are kept and moved onto the first plate position.
"""
import json
import math
import re
import zipfile
from pathlib import Path

MODEL_SETTINGS = "Metadata/model_settings.config"
PROJECT_SETTINGS = "Metadata/project_settings.config"
PLATE_GAP = 1.0 / 5.0  # LOGICAL_PART_PLATE_GAP in Bambu/Orca


def _plates(model_cfg: str) -> list:
    """Object ids per plate, in plate order."""
    plates = []
    for block in re.findall(r"<plate>(.*?)</plate>", model_cfg, re.S):
        ids = re.findall(r'<metadata key="object_id" value="(\d+)"\s*/>', block)
        plates.append(set(ids))
    return plates


def _plate_offset(index: int, count: int, width: float, depth: float) -> tuple:
    root = math.sqrt(count)
    cols = int(round(root)) + (1 if root > round(root) else 0)
    cols = max(cols, 1)
    col, row = index % cols, index // cols
    return col * width * (1 + PLATE_GAP), -row * depth * (1 + PLATE_GAP)


def _bed_size(zf: zipfile.ZipFile) -> tuple:
    try:
        cfg = json.loads(zf.read("Metadata/project_settings.config"))
        pts = [tuple(map(float, p.split("x"))) for p in cfg.get("printable_area", [])]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return max(xs) - min(xs), max(ys) - min(ys)
    except (KeyError, ValueError, IndexError):
        return 256.0, 256.0


def _shift_transform(transform: str, dx: float, dy: float) -> str:
    m = [float(v) for v in transform.split()]
    if len(m) != 12:
        return transform
    m[9] -= dx
    m[10] -= dy
    return " ".join(f"{v:.9g}" for v in m)


def _fix_colours(raw: bytes, colours: list) -> bytes:
    """One filament_colour entry per filament. The CLI clamps object filament
    assignments beyond the length of filament_colour back to filament 1."""
    try:
        cfg = json.loads(raw)
    except ValueError:
        return raw
    count = max(len(cfg.get("filament_settings_id") or []), len(colours))
    current = list(cfg.get("filament_colour") or [])
    fixed = []
    for i in range(count):
        chosen = colours[i] if i < len(colours) else ""
        fixed.append(chosen or (current[i] if i < len(current) and current[i] else "#FFFFFF"))
    cfg["filament_colour"] = fixed
    return json.dumps(cfg, indent=4).encode("utf-8")


def prepare(src: Path, dst: Path, plate: int = 1, colours=None) -> dict:
    """Write a CLI-safe copy of `src` to `dst`. Returns info for logging."""
    colours = [c if re.fullmatch(r"#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", c or "") else "" for c in (colours or [])]
    with zipfile.ZipFile(src) as zin:
        names = zin.namelist()
        model_cfg = zin.read(MODEL_SETTINGS).decode("utf-8", "replace") if MODEL_SETTINGS in names else ""
        plates = _plates(model_cfg)
        keep, offset = None, (0.0, 0.0)
        if len(plates) > 1:
            idx = max(0, min(plate - 1, len(plates) - 1))
            keep = plates[idx]
            offset = _plate_offset(idx, len(plates), *_bed_size(zin))
        root = "3D/3dmodel.model"
        rels = zin.read("_rels/.rels").decode("utf-8", "replace") if "_rels/.rels" in names else ""
        m = re.search(r'Target="/?([^"]+\.model)"[^>]*Type="[^"]*/3dmodel"|Type="[^"]*/3dmodel"[^>]*Target="/?([^"]+\.model)"', rels)
        if m:
            root = m.group(1) or m.group(2)

        def fix_item(match):
            item = match.group(0)
            oid = re.search(r'objectid="(\d+)"', item)
            if keep is not None and oid and oid.group(1) not in keep:
                return ""
            if offset != (0.0, 0.0):
                item = re.sub(r'transform="([^"]+)"',
                              lambda t: f'transform="{_shift_transform(t.group(1), *offset)}"', item)
            return item

        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == PROJECT_SETTINGS:
                    data = _fix_colours(data, colours)
                elif info.filename == MODEL_SETTINGS:
                    text = re.sub(r"\s*<plate>.*?</plate>", "", model_cfg, flags=re.S)
                    data = text.encode("utf-8")
                elif info.filename == root and (keep is not None or offset != (0.0, 0.0)):
                    text = data.decode("utf-8")
                    text = re.sub(r"<item\b[^>]*/>", fix_item, text)
                    data = text.encode("utf-8")
                zout.writestr(info, data)
    return {"plates": len(plates), "plate": plate, "kept_objects": sorted(keep) if keep else "all",
            "offset": offset}
