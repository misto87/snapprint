"""Headless slicing with the Snapmaker Orca CLI and a single-worker job queue."""
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import threemf
from .profiles import Profiles
from .util import parse_gcode_stats, sanitize_gcode_name

log = logging.getLogger("snapprint.slicer")

ORCA_BIN = os.environ.get("SNAPPRINT_ORCA_BIN", "/opt/orca/AppRun")
SLICE_TIMEOUT = int(os.environ.get("SNAPPRINT_SLICE_TIMEOUT", "1200"))
MAX_TOOLHEADS = 4
JOB_TTL = 48 * 3600


def inspect_model(path: Path) -> dict:
    """Describe an uploaded model: kind, filament slots (3MF only) and plate count."""
    kind = path.suffix.lower().lstrip(".")
    if kind == "stl":
        return {"kind": "stl", "slots": [{"color": "", "type": "", "name": ""}], "plates": 1,
                "painted": False}
    if kind != "3mf":
        raise ValueError("Nur .3mf und .stl werden unterstützt")
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ValueError("Die 3MF-Datei ist beschädigt")
    with zf:
        names = set(zf.namelist())
        colours, types, ids = [], [], []
        if "Metadata/project_settings.config" in names:
            try:
                cfg = json.loads(zf.read("Metadata/project_settings.config"))
                colours = cfg.get("filament_colour") or []
                types = cfg.get("filament_type") or []
                ids = cfg.get("filament_settings_id") or []
            except ValueError:
                pass
        elif "Metadata/Slic3r_PE.config" in names:
            text = zf.read("Metadata/Slic3r_PE.config").decode("utf-8", "replace")
            m = re.search(r"^; extruder_colour = (.*)$", text, re.M)
            if m:
                colours = m.group(1).split(";")
        model_cfg = ""
        for cand in ("Metadata/model_settings.config", "Metadata/Slic3r_PE_model.config"):
            if cand in names:
                model_cfg = zf.read(cand).decode("utf-8", "replace")
                break
        used = {int(v) for v in re.findall(r'key="extruder" value="(\d+)"', model_cfg)}
        plates = max(1, len(re.findall(r"<plate>", model_cfg)))
        painted = False
        for n in names:
            if n.startswith("3D/") and n.endswith(".model"):
                with zf.open(n) as fh:
                    chunk = fh.read(64 * 1024 * 1024)
                if b"paint_color=" in chunk or b"mmu_segmentation=" in chunk:
                    painted = True
                    break
    count = max(len(colours), max(used) if used else 0, 1)
    slots = []
    for i in range(count):
        slots.append({
            "color": colours[i] if i < len(colours) else "",
            "type": types[i] if i < len(types) else "",
            "name": ids[i] if i < len(ids) else "",
        })
    return {"kind": "3mf", "slots": slots, "plates": plates, "painted": painted,
            "too_many": count > MAX_TOOLHEADS}


class JobManager:
    def __init__(self, data_dir: Path, profiles: Profiles):
        self.uploads = data_dir / "uploads"
        self.jobs_dir = data_dir / "jobs"
        self.orca_home = data_dir / "orca"
        for d in (self.uploads, self.jobs_dir, self.orca_home):
            d.mkdir(parents=True, exist_ok=True)
        self.profiles = profiles
        self.jobs = {}
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self._load_existing()
        threading.Thread(target=self._janitor, daemon=True).start()

    # ---------- uploads ----------
    def save_upload(self, filename: str, stream) -> dict:
        ext = Path(filename).suffix.lower()
        if ext not in (".3mf", ".stl"):
            raise ValueError("Nur .3mf und .stl werden unterstützt")
        upload_id = uuid.uuid4().hex[:12]
        folder = self.uploads / upload_id
        folder.mkdir()
        target = folder / ("model" + ext)
        stream.save(target)
        try:
            info = inspect_model(target)
        except ValueError:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        meta = {"upload_id": upload_id, "filename": filename, "created": time.time(), **info}
        (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return meta

    def get_upload(self, upload_id: str) -> tuple:
        if not re.fullmatch(r"[0-9a-f]{12}", upload_id or ""):
            raise ValueError("Ungültige Upload-ID")
        folder = self.uploads / upload_id
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        model = next(folder.glob("model.*"))
        return meta, model

    # ---------- jobs ----------
    def submit(self, req: dict) -> dict:
        meta, model = self.get_upload(req.get("upload_id", ""))
        machine = req.get("machine") or ""
        process = req.get("process") or ""
        filaments = req.get("filaments") or []
        self.profiles.path("machine", machine)
        self.profiles.check_compatible("process", process, machine)
        if meta["kind"] == "3mf":
            if meta.get("too_many"):
                raise ValueError(f"Die 3MF nutzt {len(meta['slots'])} Filamente, der U1 hat nur 4 Toolheads")
            if len(filaments) != len(meta["slots"]):
                raise ValueError("Für jede Farbe der 3MF muss ein Filament gewählt werden")
            toolhead = None
        else:
            if len(filaments) != 1:
                raise ValueError("STL-Dateien werden einfarbig gedruckt: genau ein Filament wählen")
            toolhead = int(req.get("toolhead", 0))
            if not 0 <= toolhead < MAX_TOOLHEADS:
                raise ValueError("Toolhead muss T1–T4 sein")
        for f in filaments:
            self.profiles.check_compatible("filament", f["name"], machine)
        plate = int(req.get("plate", 1))
        if not 1 <= plate <= meta["plates"]:
            raise ValueError("Ungültige Platte")

        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id, "state": "queued", "created": time.time(), "message": "",
            "upload_id": meta["upload_id"], "source_name": meta["filename"], "kind": meta["kind"],
            "machine": machine, "process": process, "filaments": filaments, "toolhead": toolhead,
            # Arranging 3MF projects hits another GUI-only code path in the CLI.
            "plate": plate, "arrange": meta["kind"] == "stl" and bool(req.get("arrange", True)),
            "gcode_name": sanitize_gcode_name(meta["filename"]), "stats": {}, "sent_as": None,
        }
        (self.jobs_dir / job_id).mkdir()
        shutil.copy(model, self.jobs_dir / job_id / model.name)
        self._save(job)
        self.pool.submit(self._run, job_id)
        return job

    def get(self, job_id: str) -> dict:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            return dict(job)

    def update(self, job_id: str, **fields) -> dict:
        with self.lock:
            self.jobs[job_id].update(fields)
            job = dict(self.jobs[job_id])
        (self.jobs_dir / job_id / "job.json").write_text(json.dumps(job), encoding="utf-8")
        return job

    def gcode_path(self, job_id: str) -> Path:
        return self.jobs_dir / job_id / "out" / "result.gcode"

    def _save(self, job: dict) -> None:
        with self.lock:
            self.jobs[job["id"]] = job
        (self.jobs_dir / job["id"] / "job.json").write_text(json.dumps(job), encoding="utf-8")

    def _load_existing(self) -> None:
        for jf in self.jobs_dir.glob("*/job.json"):
            try:
                job = json.loads(jf.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if job.get("state") in ("queued", "slicing"):
                job.update(state="error", message="Server wurde während des Slicens neu gestartet")
            self.jobs[job["id"]] = job

    def _janitor(self) -> None:
        while True:
            cutoff = time.time() - JOB_TTL
            for base in (self.uploads, self.jobs_dir):
                for d in base.iterdir():
                    try:
                        if d.is_dir() and d.stat().st_mtime < cutoff:
                            shutil.rmtree(d, ignore_errors=True)
                            with self.lock:
                                self.jobs.pop(d.name, None)
                    except OSError:
                        pass
            time.sleep(3600)

    # ---------- slicing ----------
    def _filament_file(self, workdir: Path, idx: int, name: str, color: str) -> Path:
        data = self.profiles.load("filament", name)
        if re.fullmatch(r"#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", color or ""):
            data["filament_colour"] = [color[:7].upper()]
        target = workdir / f"filament_{idx}.json"
        target.write_text(json.dumps(data), encoding="utf-8")
        return target

    def process_file(self, workdir: Path, process: str, machine: str) -> Path:
        """Process profile plus the machine's nozzle_diameter.

        The CLI runs normalize_fdm() on the process file alone and dereferences
        nozzle_diameter whenever wipe_tower_filament is set (segfault otherwise).
        The value is identical to the machine profile's, so nothing changes."""
        data = self.profiles.load("process", process)
        data["nozzle_diameter"] = self.profiles.load("machine", machine)["nozzle_diameter"]
        target = workdir / "process.json"
        target.write_text(json.dumps(data), encoding="utf-8")
        return target

    def build_command(self, job: dict, workdir: Path) -> list:
        model = next(workdir.glob("model.*"))
        if job["kind"] == "3mf":
            prepared = workdir / "prepared.3mf"
            info = threemf.prepare(model, prepared, job["plate"])
            log.info("job %s: 3mf prepared %s", job["id"], info)
            model = prepared
        machine = self.profiles.path("machine", job["machine"])
        process = self.process_file(workdir, job["process"], job["machine"])
        fil_paths = []
        if job["kind"] == "stl":
            # One filament per toolhead up to the chosen one; the object is bound to
            # the chosen toolhead via --load-filament-ids, the others stay unused.
            f = job["filaments"][0]
            fil = self._filament_file(workdir, 0, f["name"], f.get("color", ""))
            fil_paths = [fil] * (job["toolhead"] + 1)
        else:
            for i, f in enumerate(job["filaments"]):
                fil_paths.append(self._filament_file(workdir, i, f["name"], f.get("color", "")))
        out = workdir / "out"
        out.mkdir(exist_ok=True)
        cmd = [
            "xvfb-run", "-a", "-s", "-screen 0 1280x1024x24",
            ORCA_BIN,
            "--datadir", str(self.orca_home),
            "--load-settings", f"{machine};{process}",
            "--load-filaments", ";".join(str(p) for p in fil_paths),
            "--outputdir", str(out),
            "--slice", "0",
            "--export-3mf", "result.3mf",
        ]
        if job["kind"] == "stl":
            cmd += ["--load-filament-ids", str(job["toolhead"] + 1)]
        else:
            # The CLI reports an internal version (01.10.x) older than the 3MF files
            # written by current Snapmaker Orca / Bambu Studio releases.
            cmd += ["--allow-newer-file"]
        if job["arrange"]:
            cmd += ["--arrange", "1"]
        cmd.append(str(model))
        return cmd

    def _run(self, job_id: str) -> None:
        workdir = self.jobs_dir / job_id
        self.update(job_id, state="slicing", started=time.time())
        logfile = workdir / "slicer.log"
        try:
            cmd = self.build_command(self.get(job_id), workdir)
            log.info("job %s: %s", job_id, " ".join(cmd))
            with open(logfile, "w", encoding="utf-8") as lf:
                proc = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, cwd=workdir,
                                      timeout=SLICE_TIMEOUT,
                                      env={**os.environ, "HOME": str(self.orca_home)})
            gcode = self._collect_gcode(workdir / "out", self.get(job_id)["plate"])
            if not gcode:
                raise RuntimeError(self._error_text(workdir, proc.returncode))
            final = self.gcode_path(job_id)
            if gcode != final:
                shutil.move(str(gcode), final)
            if "PRINT_START" not in final.read_text(encoding="utf-8", errors="replace")[:200000]:
                raise RuntimeError("G-Code enthält den offiziellen U1-Start-G-Code nicht – abgebrochen")
            self.update(job_id, state="done", finished=time.time(), stats=parse_gcode_stats(final),
                        size=final.stat().st_size)
            log.info("job %s done", job_id)
        except subprocess.TimeoutExpired:
            self.update(job_id, state="error", message="Zeitüberschreitung beim Slicen")
        except Exception as exc:  # report every failure to the UI
            log.exception("job %s failed", job_id)
            self.update(job_id, state="error", message=str(exc))

    @staticmethod
    def _collect_gcode(out: Path, plate: int):
        plain = sorted(out.glob("*.gcode"))
        if plain:
            return plain[0]
        result_3mf = out / "result.3mf"
        if result_3mf.exists():
            with zipfile.ZipFile(result_3mf) as zf:
                names = [n for n in zf.namelist() if re.fullmatch(r"Metadata/plate_\d+\.gcode", n)]
                wanted = f"Metadata/plate_{plate}.gcode"
                pick = wanted if wanted in names else (names[0] if names else None)
                if pick:
                    target = out / "result.gcode"
                    with zf.open(pick) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    return target
        return None

    @staticmethod
    def _error_text(workdir: Path, returncode: int) -> str:
        result = workdir / "out" / "result.json"
        if result.exists():
            try:
                data = json.loads(result.read_text(encoding="utf-8"))
                if data.get("error_string"):
                    return f"Slicer-Fehler: {data['error_string']} (Code {data.get('return_code')})"
            except ValueError:
                pass
        tail = ""
        logfile = workdir / "slicer.log"
        if logfile.exists():
            lines = [l for l in logfile.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
            tail = " | ".join(lines[-3:])
        return f"Slicer lieferte keinen G-Code (Code {returncode}). {tail}".strip()

    def log_tail(self, job_id: str, lines: int = 40) -> str:
        logfile = self.jobs_dir / job_id / "slicer.log"
        if not logfile.exists():
            return ""
        return "\n".join(logfile.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
