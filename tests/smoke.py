"""End-to-end slicing smoke test, run inside the built image by CI.

Writes GitHub Actions annotations so results are visible without log access.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.profiles import Profiles  # noqa: E402
from app.slicer import ORCA_BIN, JobManager  # noqa: E402

MACHINE = "Snapmaker U1 (0.4 nozzle)"
GH = os.environ.get("GITHUB_ACTIONS") == "true"


def annotate(level: str, title: str, msg: str) -> None:
    print(f"[{level}] {title}: {msg}")
    if GH:
        print(f"::{level} title={title}::" + msg.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))


def cube_stl(path: Path, size=20.0, x0=0.0) -> None:
    s = size
    v = [(x0, 0, 0), (x0 + s, 0, 0), (x0 + s, s, 0), (x0, s, 0),
         (x0, 0, s), (x0 + s, 0, s), (x0 + s, s, s), (x0, s, s)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    lines = ["solid cube"]
    for a, b, c in faces:
        lines += ["facet normal 0 0 0", " outer loop"]
        lines += [f"  vertex {v[i][0]} {v[i][1]} {v[i][2]}" for i in (a, b, c)]
        lines += [" endloop", "endfacet"]
    lines.append("endsolid cube")
    path.write_text("\n".join(lines))


class Upload:
    def __init__(self, path: Path):
        self.path = path

    def save(self, target):
        shutil.copy(self.path, target)


def wait(jm: JobManager, job_id: str, timeout=900) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        job = jm.get(job_id)
        if job["state"] in ("done", "error"):
            return job
        time.sleep(2)
    return jm.get(job_id)


def toolchanges(gcode: Path) -> set:
    return set(re.findall(r"^T(\d+)\s*$", gcode.read_text(errors="replace"), re.M))


def gdb_backtrace(title: str, jm: JobManager, job: dict) -> None:
    """Re-run a failed job's CLI call under gdb (CI debug step, runs as root)."""
    workdir = jm.jobs_dir / job["id"]
    cmd = jm.build_command(job, workdir)
    args = cmd[cmd.index(ORCA_BIN) + 1:]
    binary = next(p for p in (Path(ORCA_BIN).parent / "bin").iterdir() if os.access(p, os.X_OK))
    libs = sorted({str(p.parent) for p in Path(ORCA_BIN).parent.rglob("*.so*")})
    env = {**os.environ, "LD_LIBRARY_PATH": ":".join(libs), "LC_ALL": "C"}
    r = subprocess.run(["xvfb-run", "-a", "gdb", "-q", "-batch", "-ex", "run", "-ex", "bt", "--args", str(binary)]
                       + args, capture_output=True, text=True, timeout=900, env=env, cwd=workdir)
    frames = [l[:230] for l in (r.stdout + r.stderr).splitlines() if re.match(r"#\d+|.*SIG", l)]
    annotate("warning", f"gdb {title}", "\n".join(frames[:30]) or (r.stdout + r.stderr)[-2000:])


def main() -> int:
    probe = subprocess.run(["xvfb-run", "-a", ORCA_BIN, "--help"], capture_output=True, text=True, timeout=180)
    out = (probe.stdout + probe.stderr).strip()
    annotate("notice" if probe.returncode == 0 else "warning", "Orca CLI --help",
             f"exit {probe.returncode}\n{out[:600]}\n...\n{out[-800:]}")
    profiles = Profiles()
    process = profiles.default_process(MACHINE)
    pla = "Generic PLA" if "Generic PLA" in profiles._by_kind["filament"] else profiles.index["filaments"][0]["name"]
    annotate("notice", "Profiles", f"{len(profiles.index['machines'])} machines, "
             f"{len(profiles.index['processes'])} processes, {len(profiles.index['filaments'])} filaments; "
             f"default process: {process}")
    work = Path(tempfile.mkdtemp())
    jm = JobManager(work / "data", profiles)
    failures = 0

    def run(title, model: Path, req: dict, check):
        nonlocal failures
        meta = jm.save_upload(model.name, Upload(model))
        job = jm.submit({"upload_id": meta["upload_id"], "machine": MACHINE, "process": process, **req})
        job = wait(jm, job["id"])
        if job["state"] != "done":
            failures += 1
            annotate("error", title, f"{job['message']}\n{jm.log_tail(job['id'], 60)}")
            if os.environ.get("SNAPPRINT_GDB") and shutil.which("gdb"):
                gdb_backtrace(title, jm, jm.get(job["id"]))
            return None
        gcode = jm.gcode_path(job["id"])
        problem = check(gcode, meta)
        if problem:
            failures += 1
            annotate("error", title, problem)
        else:
            annotate("notice", title, f"ok · {job['stats']} · toolchanges {sorted(toolchanges(gcode))}")
        return job

    cube = work / "#Würfel test.stl"
    cube_stl(cube)

    def check_stl(head):
        def check(gcode, _meta):
            text = gcode.read_text(errors="replace")
            if "PRINT_START" not in text:
                return "official start gcode missing"
            if str(head) not in toolchanges(gcode):
                return f"expected toolhead T{head}, got {sorted(toolchanges(gcode))}"
            return None
        return check

    job = run("STL on T1", cube, {"filaments": [{"name": pla, "color": "#FF0000"}], "toolhead": 0}, check_stl(0))
    if job:
        lines = jm.gcode_path(job["id"]).read_text(errors="replace").splitlines()
        hits = [l for l in lines if l.startswith(";")
                and re.search(r"estimated|total filament|printing time|filament used \[", l, re.I)]
        annotate("notice", "G-code summary lines", "\n".join(hits[:25]))
    run("STL on T3", cube, {"filaments": [{"name": pla}], "toolhead": 2}, check_stl(2))

    # Build a two-colour 3MF with the Orca CLI (object 1 -> filament 1, object 2 -> filament 2).
    a, b = work / "a.stl", work / "b.stl"
    cube_stl(a, x0=0)
    cube_stl(b, x0=40)
    fil = profiles.path("filament", pla)
    out = work / "mk3mf"
    out.mkdir()
    cmd = ["xvfb-run", "-a", ORCA_BIN, "--datadir", str(work / "orca"),
           "--load-settings", f"{profiles.path('machine', MACHINE)};{jm.process_file(out, process, MACHINE)}",
           "--load-filaments", f"{fil};{fil}", "--load-filament-ids", "1,2", "--arrange", "1",
           "--outputdir", str(out), "--export-3mf", "two_colour.3mf", str(a), str(b)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    mf = out / "two_colour.3mf"
    if not mf.exists():
        failures += 1
        annotate("error", "Build test 3MF", (proc.stdout + proc.stderr)[-3000:])
    else:
        def check_3mf(gcode, meta):
            if len(meta["slots"]) != 2:
                return f"expected 2 slots, inspect found {meta['slots']}"
            tc = toolchanges(gcode)
            if not {"0", "1"} <= tc:
                info = []
                for label, path in (("source", mf), ("prepared", gcode.parent.parent / "prepared.3mf")):
                    if path.exists():
                        with zipfile.ZipFile(path) as zf:
                            cfg = zf.read("Metadata/model_settings.config").decode(errors="replace") \
                                if "Metadata/model_settings.config" in zf.namelist() else ""
                            info.append(f"{label} files: {[n for n in zf.namelist() if n.startswith(('3D/', 'Metadata/model'))]}")
                        info.append(f"{label} extruder keys: " + " ".join(
                            re.findall(r'<(?:object|part|volume)[^>]*>|key="extruder" value="\d+"', cfg))[:900])
                return f"expected T0 and T1, got {sorted(tc)}\n" + "\n".join(info)
            return None
        run("3MF two colours", mf, {"filaments": [{"name": pla, "color": "#E72F1D"},
                                                   {"name": pla, "color": "#1E88E5"}]}, check_3mf)

    if failures:
        annotate("error", "Smoke test", f"{failures} failure(s)")
        return 1
    annotate("notice", "Smoke test", "all slicing checks passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        annotate("error", "Smoke test crashed", traceback.format_exc()[-3000:])
        sys.exit(1)
