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
            if f"T{head}" not in toolchanges(gcode) and f"T{head}_TEMP" not in text:
                return f"expected toolhead T{head}, got {sorted(toolchanges(gcode))}"
            return None
        return check

    run("STL on T1", cube, {"filaments": [{"name": pla, "color": "#FF0000"}], "toolhead": 0}, check_stl(0))
    run("STL on T3", cube, {"filaments": [{"name": pla}], "toolhead": 2}, check_stl(2))

    # Build a two-colour 3MF with the Orca CLI (object 1 -> filament 1, object 2 -> filament 2).
    a, b = work / "a.stl", work / "b.stl"
    cube_stl(a, x0=0)
    cube_stl(b, x0=40)
    fil = profiles.path("filament", pla)
    out = work / "mk3mf"
    out.mkdir()
    cmd = ["xvfb-run", "-a", ORCA_BIN, "--datadir", str(work / "orca"),
           "--load-settings", f"{profiles.path('machine', MACHINE)};{profiles.path('process', process)}",
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
                return f"expected T0 and T1, got {sorted(tc)}"
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
