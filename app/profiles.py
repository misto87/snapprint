import json
import os
from pathlib import Path

PROFILE_DIR = Path(os.environ.get("SNAPPRINT_PROFILE_DIR", "/opt/snapprint/profiles"))


class Profiles:
    def __init__(self, root: Path = PROFILE_DIR):
        self.root = root
        self.index = json.loads((root / "index.json").read_text(encoding="utf-8"))
        self._by_kind = {
            kind: {e["name"]: e for e in self.index[key]}
            for kind, key in (("machine", "machines"), ("process", "processes"), ("filament", "filaments"))
        }

    def path(self, kind: str, name: str) -> Path:
        entry = self._by_kind[kind].get(name)
        if not entry:
            raise ValueError(f"Unbekanntes {kind}-Profil: {name}")
        return self.root / entry["file"]

    def load(self, kind: str, name: str) -> dict:
        return json.loads(self.path(kind, name).read_text(encoding="utf-8"))

    def check_compatible(self, kind: str, name: str, machine: str) -> None:
        if machine not in self._by_kind[kind][name]["compatible"]:
            raise ValueError(f"„{name}“ passt nicht zu „{machine}“")

    def default_process(self, machine: str) -> str:
        m = self._by_kind["machine"][machine]
        wanted = m.get("default_process", "")
        candidates = [p for p in self.index["processes"] if machine in p["compatible"]]
        for p in candidates:
            if p["name"] == wanted or wanted in p.get("renamed_from", []):
                return p["name"]
        return candidates[0]["name"] if candidates else ""

    def public(self) -> dict:
        machines = []
        for m in self.index["machines"]:
            machines.append({**{k: m[k] for k in ("name", "nozzle", "extruders")},
                             "default_process": self.default_process(m["name"])})
        return {
            "machines": machines,
            "processes": [{k: p[k] for k in ("name", "compatible", "layer_height")}
                          for p in self.index["processes"]],
            "filaments": [{k: f[k] for k in ("name", "compatible", "type", "vendor", "nozzle_temp")}
                          for f in self.index["filaments"]],
        }
