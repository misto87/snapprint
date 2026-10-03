#!/usr/bin/env python3
"""Flatten the official Snapmaker U1 profiles shipped with Snapmaker Orca.

The system profiles are layered via "inherits" (e.g. "Snapmaker PLA SnapSpeed @U1"
-> "... @U1 base" -> "fdm_filament_pla" -> ...). The Orca CLI is most reliable
with fully resolved files, so we resolve every chain once at image build time and
write self-contained JSON files plus an index for the web UI.

Nothing is edited: every key/value comes from the official profiles, including
machine_start_gcode / machine_end_gcode / change_filament_gcode.

Usage: build_profiles.py <resources/profiles dir> <output dir>
"""
import json
import re
import sys
from pathlib import Path

VENDOR = "Snapmaker"
PRINTER_MODEL = "Snapmaker U1"


def load_all(vendor_dir: Path) -> dict:
    presets = {}
    for path in vendor_dir.rglob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(data, dict) and "name" in data and "type" in data:
            presets[data["name"]] = data
    return presets


def resolve(name: str, presets: dict, seen=()) -> dict:
    if name in seen:
        raise ValueError(f"inheritance loop at {name}")
    if name not in presets:
        raise KeyError(f"parent preset not found: {name}")
    node = presets[name]
    parent = node.get("inherits") or ""
    merged = resolve(parent, presets, seen + (name,)) if parent else {}
    merged.update(node)
    return merged


def finalize(flat: dict) -> dict:
    flat = dict(flat)
    flat.pop("inherits", None)
    flat["inherits"] = ""
    flat["from"] = "system"
    flat["instantiation"] = "true"
    return flat


def safe_file(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") + ".json"


def compatible(flat: dict, machine: str) -> bool:
    printers = flat.get("compatible_printers") or []
    if printers:
        return machine in printers
    cond = flat.get("compatible_printers_condition") or ""
    if not cond:
        return False  # generic presets without explicit U1 binding are skipped
    return PRINTER_MODEL in cond and ("nozzle_diameter" not in cond or nozzle_of(machine) in cond)


def nozzle_of(machine: str) -> str:
    m = re.search(r"\(([\d.]+) nozzle\)", machine)
    return m.group(1) if m else ""


def first(value, default=""):
    if isinstance(value, list):
        return value[0] if value else default
    return value if value is not None else default


def main(src: Path, out: Path) -> None:
    presets = load_all(src / VENDOR)
    for sub in ("machine", "process", "filament"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    index = {"vendor": VENDOR, "printer_model": PRINTER_MODEL,
             "machines": [], "processes": [], "filaments": []}

    machines = [n for n, p in presets.items()
                if p["type"] == "machine" and p.get("printer_model") == PRINTER_MODEL
                and str(p.get("instantiation", "true")) == "true"]

    for name in sorted(machines):
        flat = finalize(resolve(name, presets))
        fname = safe_file(name)
        (out / "machine" / fname).write_text(json.dumps(flat, indent=2), encoding="utf-8")
        index["machines"].append({
            "name": name, "file": f"machine/{fname}", "nozzle": nozzle_of(name),
            "default_process": flat.get("default_print_profile", ""),
            "extruders": len(flat.get("nozzle_diameter", [])),
        })

    for name, p in sorted(presets.items()):
        if p["type"] not in ("process", "filament"):
            continue
        if str(p.get("instantiation", "false")) != "true":
            continue
        try:
            flat = resolve(name, presets)
        except KeyError as exc:
            print(f"skip {name}: {exc}", file=sys.stderr)
            continue
        compat = [m for m in machines if compatible(flat, m)]
        if not compat:
            continue
        flat = finalize(flat)
        fname = safe_file(name)
        (out / p["type"] / fname).write_text(json.dumps(flat, indent=2), encoding="utf-8")
        entry = {"name": name, "file": f"{p['type']}/{fname}", "compatible": compat}
        if p["type"] == "process":
            entry["layer_height"] = flat.get("layer_height", "")
            entry["renamed_from"] = [n for n in (flat.get("renamed_from") or "").split(";") if n]
            index["processes"].append(entry)
        else:
            entry.update({
                "type": first(flat.get("filament_type")),
                "vendor": first(flat.get("filament_vendor")),
                "nozzle_temp": first(flat.get("nozzle_temperature")),
            })
            index["filaments"].append(entry)

    (out / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"{len(index['machines'])} machines, {len(index['processes'])} processes, "
          f"{len(index['filaments'])} filaments -> {out}")
    if not index["machines"] or not index["processes"] or not index["filaments"]:
        sys.exit("no U1 profiles found - Snapmaker Orca layout changed?")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]))
