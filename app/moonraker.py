"""Minimal Moonraker client for the Snapmaker U1.

Only whitelisted calls are exposed: status query, G-code upload (never with
auto-start) and an explicit print start. There is no generic pass-through.
"""
import requests

TOOLHEADS = ("extruder", "extruder1", "extruder2", "extruder3")
STATUS_OBJECTS = TOOLHEADS + ("heater_bed", "print_stats", "virtual_sdcard", "print_task_config",
                              "webhooks", "display_status")
IDLE_STATES = ("standby", "complete", "cancelled", "error")


class PrinterError(Exception):
    pass


class Moonraker:
    def __init__(self, host: str, port: int = 7125, timeout: float = 6.0):
        if not host:
            raise PrinterError("Keine Drucker-IP eingestellt")
        self.base = f"http://{host}:{port}"
        self.timeout = timeout

    def _req(self, method: str, path: str, timeout=None, **kw) -> dict:
        try:
            r = requests.request(method, self.base + path, timeout=timeout or self.timeout, **kw)
        except requests.RequestException as exc:
            raise PrinterError(f"U1 nicht erreichbar: {exc.__class__.__name__}")
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400:
            msg = (body.get("error") or {}).get("message") if isinstance(body, dict) else None
            raise PrinterError(msg or f"Moonraker-Fehler {r.status_code}")
        return body.get("result", body)

    def info(self) -> dict:
        server = self._req("GET", "/server/info")
        printer = self._req("GET", "/printer/info")
        return {"moonraker_version": server.get("moonraker_version"),
                "klippy_state": server.get("klippy_state"),
                "firmware": printer.get("software_version"),
                "hostname": printer.get("hostname")}

    def status(self) -> dict:
        query = "&".join(STATUS_OBJECTS)
        st = self._req("GET", f"/printer/objects/query?{query}").get("status", {})
        cfg = st.get("print_task_config", {})

        def at(key, i, default=None):
            values = cfg.get(key) or []
            return values[i] if i < len(values) else default

        heads = []
        for i, name in enumerate(TOOLHEADS):
            ex = st.get(name, {})
            rgba = at("filament_color_rgba", i, "") or ""
            heads.append({
                "index": i,
                "label": f"T{i + 1}",
                "temperature": round(ex.get("temperature", 0.0), 1),
                "target": round(ex.get("target", 0.0), 1),
                "state": ex.get("state", ""),
                "nozzle": ex.get("nozzle_diameter"),
                "filament": {
                    "loaded": bool(at("filament_exist", i, False)),
                    "vendor": at("filament_vendor", i, ""),
                    "type": at("filament_type", i, ""),
                    "sub_type": at("filament_sub_type", i, ""),
                    "color": ("#" + rgba[:6]) if len(rgba) >= 6 else "",
                    "official": bool(at("filament_official", i, False)),
                },
            })
        ps = st.get("print_stats", {})
        sd = st.get("virtual_sdcard", {})
        bed = st.get("heater_bed", {})
        return {
            "klippy": st.get("webhooks", {}).get("state", ""),
            "state": ps.get("state", ""),
            "filename": ps.get("filename", ""),
            "progress": round(sd.get("progress", 0.0) * 100, 1),
            "print_duration": ps.get("print_duration", 0),
            "layer": (ps.get("info") or {}).get("current_layer"),
            "total_layers": (ps.get("info") or {}).get("total_layer"),
            "message": ps.get("message", ""),
            "bed": {"temperature": round(bed.get("temperature", 0.0), 1),
                    "target": round(bed.get("target", 0.0), 1)},
            "toolheads": heads,
        }

    def upload(self, local_path, remote_name: str) -> dict:
        # Deliberately no "print" form field: an upload must never start a print.
        with open(local_path, "rb") as fh:
            files = {"file": (remote_name, fh, "application/octet-stream")}
            res = self._req("POST", "/server/files/upload", timeout=600,
                            files=files, data={"root": "gcodes"})
        return {"path": (res.get("item") or {}).get("path", remote_name)}

    def start_print(self, filename: str) -> dict:
        st = self.status()
        if st["klippy"] and st["klippy"] != "ready":
            raise PrinterError(f"Drucker nicht bereit ({st['klippy']})")
        if st["state"] not in IDLE_STATES:
            raise PrinterError(f"Drucker ist beschäftigt ({st['state']})")
        files = self._req("GET", "/server/files/list?root=gcodes")
        if not any(f.get("path") == filename for f in files):
            raise PrinterError("Datei ist auf dem U1 nicht vorhanden")
        self._req("POST", "/printer/print/start", params={"filename": filename})
        return {"started": filename}
