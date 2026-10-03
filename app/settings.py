"""Settings schema, value resolution, override validation and user presets."""
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from .profiles import PROFILE_DIR, Profiles

SCHEMA_FILE = Path(os.environ.get("SNAPPRINT_SCHEMA", str(PROFILE_DIR / "schema.json")))
VECTOR = {"coFloats", "coInts", "coBools", "coPercents", "coFloatsOrPercents", "coStrings", "coEnums"}
NUMERIC = {"coFloat", "coFloats", "coInt", "coInts", "coPercent", "coPercents"}
_NUM = re.compile(r"^-?\d+(\.\d+)?$")


class Schema:
    def __init__(self, path: Path = SCHEMA_FILE):
        self.data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else \
            {"process": [], "filament": [], "options": {}}
        self.options = self.data["options"]

    def keys(self, kind: str) -> list:
        return [k for k, o in self.options.items() if o["kind"] == kind]

    def values(self, profile: dict, kind: str) -> dict:
        """Current value of every schema option of `kind` for a resolved profile."""
        out = {}
        for key in self.keys(kind):
            if key in profile:
                out[key] = profile[key]
            elif "default" in self.options[key]:
                out[key] = self.options[key]["default"]
        return out

    def _scalar(self, key: str, opt: dict, value) -> str:
        typ = opt["type"].rstrip("s") if opt["type"] in VECTOR else opt["type"]
        if typ == "coBool":
            if value in (True, "1", 1, "true"):
                return "1"
            if value in (False, "0", 0, "false"):
                return "0"
            raise ValueError(f"{opt.get('label', key)}: ungültiger Wert")
        text = str(value).strip()
        if typ in ("coEnum",):
            allowed = [e["value"] for e in opt.get("enum", [])]
            if allowed and text not in allowed:
                raise ValueError(f"{opt.get('label', key)}: ungültige Auswahl")
            return text
        if typ == "coString":
            if len(text) > 2000 or "\n" in text:
                raise ValueError(f"{opt.get('label', key)}: ungültiger Text")
            return text
        if typ in ("coPercent", "coFloatOrPercent", "coFloatsOrPercent"):
            pct = text.endswith("%")
            num = text[:-1].strip() if pct else text
            if not _NUM.match(num):
                raise ValueError(f"{opt.get('label', key)}: Zahl erwartet")
            self._range(key, opt, float(num))
            return num + "%" if (pct or typ == "coPercent") else num
        if not _NUM.match(text):
            raise ValueError(f"{opt.get('label', key)}: Zahl erwartet")
        if typ == "coInt" and "." in text:
            raise ValueError(f"{opt.get('label', key)}: Ganzzahl erwartet")
        self._range(key, opt, float(text))
        return text

    @staticmethod
    def _range(key, opt, num):
        if "min" in opt and num < opt["min"]:
            raise ValueError(f"{opt.get('label', key)}: Minimum ist {opt['min']:g}")
        if "max" in opt and num > opt["max"]:
            raise ValueError(f"{opt.get('label', key)}: Maximum ist {opt['max']:g}")

    def apply(self, profile: dict, kind: str, overrides: dict) -> dict:
        """Return a copy of `profile` with validated overrides applied."""
        result = dict(profile)
        for key, value in (overrides or {}).items():
            opt = self.options.get(key)
            if not opt or opt["kind"] != kind:
                raise ValueError(f"Einstellung „{key}“ ist nicht änderbar")
            if opt["type"] in VECTOR:
                base = result.get(key, opt.get("default"))
                size = len(base) if isinstance(base, list) and base else 1
                items = value if isinstance(value, list) else [value] * size
                result[key] = [self._scalar(key, opt, v) for v in items]
            else:
                result[key] = self._scalar(key, opt, value)
        return result

    def diff(self, base: dict, other: dict, kind: str) -> dict:
        """Options whose value in `other` differs from `base` (for 3MF project settings)."""
        out = {}
        for key in self.keys(kind):
            if key in other and key in base and _norm(other[key]) != _norm(base[key]):
                out[key] = other[key]
        return out


def _norm(value):
    if isinstance(value, list):
        return [_norm(v) for v in value]
    text = str(value).strip()
    if _NUM.match(text):
        return float(text)
    return text


class Presets:
    """User presets persisted in /data/presets.json."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()

    def _load(self) -> list:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            return []

    def list(self) -> list:
        with self.lock:
            return self._load()

    def save(self, profiles: Profiles, schema: Schema, body: dict) -> dict:
        name = str(body.get("name", "")).strip()
        kind = body.get("kind")
        base = body.get("base", "")
        if not name or len(name) > 80:
            raise ValueError("Bitte einen Namen (max. 80 Zeichen) angeben")
        if kind not in ("process", "filament"):
            raise ValueError("Ungültiger Profiltyp")
        schema.apply(profiles.load(kind, base), kind, body.get("overrides") or {})  # validates
        preset = {"id": uuid.uuid4().hex[:10], "name": name, "kind": kind, "base": base,
                  "machine": body.get("machine", ""), "overrides": body.get("overrides") or {},
                  "updated": time.time()}
        with self.lock:
            items = [p for p in self._load() if not (p["kind"] == kind and p["name"] == name)]
            items.append(preset)
            self.path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        return preset

    def delete(self, preset_id: str) -> None:
        with self.lock:
            items = self._load()
            kept = [p for p in items if p["id"] != preset_id]
            if len(kept) == len(items):
                raise KeyError(preset_id)
            self.path.write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")
