#!/usr/bin/env python3
"""Generate the settings schema for the web UI from Snapmaker Orca's own sources.

Inputs (all from the same Snapmaker Orca tag as the bundled AppImage):
  PrintConfig.cpp  option definitions: type, label, tooltip, unit, min/max, mode, enums
  Tab.cpp          page/group layout of the Process and Filament settings tabs
  *_de.po          German translations of labels and tooltips

Output: schema.json with {"process": layout, "filament": layout, "options": {...}}.

Usage: build_schema.py PrintConfig.cpp Tab.cpp de.po schema.json
"""
import json
import re
import sys
from pathlib import Path

# Options that must never be editable from the web UI.
BLOCKED = {
    "post_process",          # runs arbitrary scripts on the server
    "filament_start_gcode", "filament_end_gcode",
    "filename_format", "notes", "filament_notes", "print_settings_id", "filament_settings_id",
    "inherits", "compatible_printers", "compatible_printers_condition",
    "compatible_prints", "compatible_prints_condition",
}
SUPPORTED = {"coFloat", "coFloats", "coInt", "coInts", "coBool", "coBools", "coPercent", "coPercents",
             "coFloatOrPercent", "coFloatsOrPercents", "coEnum", "coEnums", "coString", "coStrings"}
MODES = {"comSimple": "simple", "comAdvanced": "advanced", "comDevelop": "develop"}

_STR = r'"(?:[^"\\]|\\.)*"'


def _join(literals: str) -> str:
    parts = re.findall(_STR, literals)
    text = "".join(p[1:-1] for p in parts)
    return bytes(text, "utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")


def _field(block: str, name: str):
    m = re.search(rf"def->{name}\s*=\s*(?:_?L\(|_u8L\(|_CTX\()?\s*((?:{_STR}\s*)+)", block)
    return _join(m.group(1)) if m else None


def parse_po(path: Path) -> dict:
    trans, msgid, msgstr, cur = {}, None, None, None
    for line in path.read_text(encoding="utf-8").splitlines() + [""]:
        if line.startswith("msgid "):
            if msgid is not None and msgstr:
                trans[msgid] = msgstr
            msgid, msgstr, cur = _join(line[6:]), "", "id"
        elif line.startswith("msgstr "):
            msgstr, cur = _join(line[7:]), "str"
        elif line.startswith('"') and cur:
            if cur == "id":
                msgid += _join(line)
            else:
                msgstr += _join(line)
        elif not line.strip():
            if msgid is not None and msgstr:
                trans[msgid] = msgstr
            msgid, msgstr, cur = None, None, None
    return trans


def parse_defaults(raw: str):
    raw = raw.strip()
    if raw.startswith("{") and raw.endswith("}"):
        inner = raw[1:-1]
        items = re.findall(rf'{_STR}|[-\w.]+', inner)
        return [i[1:-1] if i.startswith('"') else i for i in items]
    m = re.fullmatch(rf"({_STR})", raw)
    if m:
        return _join(raw)
    m = re.fullmatch(r"([-\d.eE]+)(?:\s*,\s*(true|false))?", raw)
    if m:
        return m.group(1) + ("%" if m.group(2) == "true" else "")
    if raw in ("true", "false"):
        return "1" if raw == "true" else "0"
    const = raw.split("::")[-1]
    if re.fullmatch(r"\w+", const) and const in ENUM_CONSTS:
        return ENUM_CONSTS[const]
    return None


ENUM_CONSTS = {}  # C++ enum constant (e.g. btAutoBrim) -> serialized value ("auto_brim")


def parse_enum_maps(src: str) -> dict:
    """s_keys_map_<Enum> tables: enum type name -> list of serialized values."""
    maps = {}
    for name, body in re.findall(r"s_keys_map_(\w+)\s*=?\s*\{(.*?)\n\};", src, re.S):
        pairs = re.findall(rf"\{{\s*({_STR})\s*,\s*(?:int\()?\s*([\w:]+)\s*\)?\s*\}}", body)
        maps[name] = [_join(v) for v, _ in pairs]
        for v, const in pairs:
            ENUM_CONSTS.setdefault(const.split("::")[-1], _join(v))
    return maps


def parse_options(src: str) -> dict:
    options, by_var = {}, {}
    enum_maps = parse_enum_maps(src)
    chunks = re.split(r"\n\s*(?:auto\s+(\w+)\s*=\s*)?def\s*=\s*this->add(?:_nullable)?\(", src)
    # re.split with a group yields [pre, var, chunk, var, chunk, ...]
    for var, chunk in zip(chunks[1::2], chunks[2::2]):
        m = re.match(r'"([a-z0-9_]+)"\s*,\s*(co\w+)\)', chunk)
        if not m:
            continue
        key, typ = m.groups()
        if var:
            by_var[var] = key
        block = chunk
        opt = {"type": typ}
        for name in ("label", "full_label", "category", "tooltip", "sidetext"):
            val = _field(block, name)
            if val is not None:
                opt[name] = val
        for name in ("min", "max"):
            mm = re.search(rf"def->{name}\s*=\s*([-\d.eE]+)\s*;", block)
            if mm:
                opt[name] = float(mm.group(1))
        mm = re.search(r"def->mode\s*=\s*(com\w+)", block)
        opt["mode"] = MODES.get(mm.group(1), "advanced") if mm else "simple"
        values = [_join(v) for v in re.findall(rf"def->enum_values\.(?:push|emplace)_back\(\s*({_STR})\s*\)", block)]
        labels = [_join(v) for v in re.findall(rf"def->enum_labels\.(?:push|emplace)_back\(\s*(?:_?L\()?\s*({_STR})", block)]
        ref = re.search(r"def->enum_values\s*=\s*(\w+)->enum_values", block)
        if not values and ref and by_var.get(ref.group(1)) in options:
            src_enum = options[by_var[ref.group(1)]].get("enum", [])
            values = [e["value"] for e in src_enum]
            labels = [e["label"] for e in src_enum]
        emap = re.search(r"ConfigOptionEnum<(\w+)>::get_enum_values", block)
        if not values and emap:
            values = enum_maps.get(emap.group(1), [])
        if values:
            opt["enum"] = [{"value": v, "label": labels[i] if i < len(labels) else v} for i, v in enumerate(values)]
        mm = re.search(r"set_default_value\(new ConfigOption[\w<>:]+\((.*?)\)\s*\);", block, re.S)
        if mm:
            opt["default"] = parse_defaults(mm.group(1))
        options.setdefault(key, opt)
    return options


def parse_layout(src: str, start: str, end: str) -> list:
    body = src[src.index(start):src.index(end)]
    pages, page, group = [], None, None
    token = re.compile(
        r'add_options_page\(L\("([^"]+)"\)|new_optgroup\(L\("([^"]+)"\)'
        r'|append_single_option_line\("([a-z0-9_]+)"|get_option\("([a-z0-9_]+)"')
    for m in token.finditer(body):
        p, g, o1, o2 = m.groups()
        if p:
            page = {"title": p, "groups": []}
            pages.append(page)
            group = None
        elif g and page is not None:
            group = {"title": g, "options": []}
            page["groups"].append(group)
        elif (o1 or o2) and group is not None:
            key = o1 or o2
            if key not in group["options"]:
                group["options"].append(key)
    return pages


def main(print_config: Path, tab: Path, po: Path, out: Path) -> None:
    de = parse_po(po)
    tr = lambda s: de.get(s, s) if s else s
    options = parse_options(print_config.read_text(encoding="utf-8"))
    tab_src = tab.read_text(encoding="utf-8")
    layouts = {
        "process": parse_layout(tab_src, "void TabPrint::build()", "void TabPrint::reload_config()"),
        "filament": parse_layout(tab_src, "void TabFilament::build()", "void TabFilament::reload_config()"),
    }
    used = {}
    for kind, pages in layouts.items():
        clean_pages = []
        for page in pages:
            groups = []
            for group in page["groups"]:
                keys = [k for k in group["options"]
                        if k in options and k not in BLOCKED and options[k]["type"] in SUPPORTED
                        and not k.endswith("_gcode")]
                if keys:
                    groups.append({"title": tr(group["title"]), "options": keys})
                    for k in keys:
                        used[k] = kind
            if groups:
                clean_pages.append({"title": tr(page["title"]), "groups": groups})
        layouts[kind] = clean_pages
    out_opts = {}
    for key, kind in used.items():
        o = dict(options[key])
        o["kind"] = kind
        for f in ("label", "full_label", "category", "tooltip", "sidetext"):
            if o.get(f):
                o[f] = tr(o[f])
        if "enum" in o:
            o["enum"] = [{"value": e["value"], "label": tr(e["label"])} for e in o["enum"]]
        out_opts[key] = o
    schema = {"process": layouts["process"], "filament": layouts["filament"], "options": out_opts}
    out.write_text(json.dumps(schema, ensure_ascii=False, indent=1), encoding="utf-8")
    n = {k: sum(len(g["options"]) for p in v for g in p["groups"]) for k, v in layouts.items()}
    print(f"schema: {n['process']} process options, {n['filament']} filament options -> {out}")
    if n["process"] < 50 or n["filament"] < 10:
        sys.exit("too few options parsed - Orca source layout changed?")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    main(*map(Path, sys.argv[1:]))
