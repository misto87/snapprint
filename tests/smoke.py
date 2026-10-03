"""End-to-end slicing smoke test, run inside the built image by CI.

Writes GitHub Actions annotations so results are visible without log access.
"""
import json
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


def derive_project(src: Path, dst: Path, extruder: int, paint_state=None) -> None:
    """Copy an Orca project 3MF: drop sliced G-code, set every object's filament to
    `extruder` and optionally paint the top-face triangles with `paint_state`."""
    code = format(paint_state << 2, "X") if paint_state else None
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            name = info.filename
            if re.match(r"Metadata/plate_\d+\.gcode", name):
                continue
            data = zin.read(name)
            if name == "Metadata/model_settings.config":
                text = re.sub(r'key="extruder" value="\d+"', f'key="extruder" value="{extruder}"', data.decode())
                data = text.encode()
            elif code and name.endswith(".model") and b"<mesh" in data:
                text = data.decode()
                zs = [float(z) for z in re.findall(r'<vertex [^>]*z="([-\d.eE+]+)"', text)]
                top = max(zs)
                verts = [abs(z - top) < 1e-4 for z in zs]

                def paint(m):
                    tri = m.group(0)
                    idx = [int(i) for i in re.findall(r'v[123]="(\d+)"', tri)]
                    if len(idx) == 3 and all(verts[i] for i in idx) and "paint_color" not in tri:
                        tri = tri.replace("/>", f' paint_color="{code}"/>')
                    return tri
                text = re.sub(r"<triangle [^>]*/>", paint, text)
                data = text.encode()
            zout.writestr(info, data)


def gcode_facts(gcode: Path) -> str:
    text = gcode.read_text(errors="replace")
    objs = re.findall(r"^EXCLUDE_OBJECT_DEFINE NAME=(\S+)", text, re.M)
    used = re.findall(r"^; filament used \[mm\] = (.*)$", text, re.M)
    m109 = sorted(set(re.findall(r"^M109 S\d+ T(\d+)", text, re.M)))
    return f"objects={objs} filament_mm={used} M109_tools={m109}"


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
        if meta["kind"] == "3mf":
            fl = req["filaments"]
            req = {**req, "filaments": [fl[i % len(fl)] for i in range(len(meta["slots"]))]}
            annotate("notice", f"{title} inspect", f"slots={meta['slots']} painted={meta['painted']} plates={meta['plates']}")
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

    run("STL on T1", cube, {"filaments": [{"name": pla, "color": "#FF0000"}], "toolhead": 0}, check_stl(0))
    run("STL on T3", cube, {"filaments": [{"name": pla}], "toolhead": 2}, check_stl(2))

    # Settings overrides from the web UI must reach the G-code.
    def check_overrides(gcode, _meta):
        text = gcode.read_text(errors="replace")
        want = {"layer_height": "0.16", "sparse_infill_density": "40%", "ironing_type": "top",
                "wall_loops": "4", "nozzle_temperature": "215"}
        problems = []
        for key, val in want.items():
            m = re.search(rf"^; {key} = (.*)$", text, re.M)
            if not m or val not in m.group(1).split(","):
                problems.append(f"{key}: want {val}, got {m.group(1) if m else 'missing'}")
        if "PRINT_START" not in text:
            problems.append("official start gcode missing")
        thumb = jm.thumbnail_path(gcode.parent.parent.name)
        if not thumb or thumb.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            r3 = gcode.parent / "result.3mf"
            pngs = [n for n in zipfile.ZipFile(r3).namelist() if n.endswith(".png")] if r3.exists() else "no result.3mf"
            heads = [l for l in text.splitlines()[:60] if "thumbnail" in l.lower()][:5]
            problems.append(f"no PNG thumbnail (3mf pngs: {pngs}; gcode thumb lines: {heads})")
        return "; ".join(problems) or None
    run("STL with settings overrides", cube, {
        "filaments": [{"name": pla, "overrides": {"nozzle_temperature": "215"}}], "toolhead": 0,
        "process_overrides": {"layer_height": "0.16", "sparse_infill_density": "40", "ironing_type": "top",
                              "wall_loops": "4"}}, check_overrides)

    schema = jm.schema
    n = {k: sum(len(g["options"]) for p in schema.data[k] for g in p["groups"]) for k in ("process", "filament")}
    annotate("notice", "Settings schema", f"{n['process']} process / {n['filament']} filament options, "
             f"pages: {[p['title'] for p in schema.data['process']]}")

    # Realistic 3MF projects: let Snapmaker Orca write a project 3MF with three
    # different filament presets (full project_settings, plate data), then derive cases.
    names = [n for n in ("Generic PLA", "Generic PLA Silk", "Generic PLA High Speed")
             if n in profiles._by_kind["filament"]] or [pla]
    names = (names * 3)[:3]
    projdir = work / "project"
    projdir.mkdir()
    cmd = ["xvfb-run", "-a", ORCA_BIN, "--datadir", str(work / "orca"),
           "--load-settings", f"{profiles.path('machine', MACHINE)};{jm.process_file(projdir, process, MACHINE)}",
           "--load-filaments", ";".join(str(profiles.path("filament", n)) for n in names),
           "--load-filament-ids", "1", "--arrange", "1", "--slice", "0",
           "--outputdir", str(projdir), "--export-3mf", "project.3mf", str(cube)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    source = projdir / "project.3mf"
    if not source.exists():
        failures += 1
        annotate("error", "3MF tests", "could not create project 3MF: " + (proc.stdout + proc.stderr)[-1500:])
    else:
        fils = [{"name": n, "color": c} for n, c in zip(names, ("#E72F1D", "#1E88E5", "#FCE94F"))]

        def expect(tools, painted=None):
            def check(gcode, meta):
                if painted is not None and meta.get("painted") != painted:
                    return f"inspect painted={meta.get('painted')} slots={len(meta['slots'])}"
                tc = toolchanges(gcode)
                if tc != set(tools):
                    prepared = gcode.parent.parent / "prepared.3mf"
                    dump = ""
                    if prepared.exists():
                        with zipfile.ZipFile(prepared) as zf:
                            ms = zf.read("Metadata/model_settings.config").decode(errors="replace")
                            ps = json.loads(zf.read("Metadata/project_settings.config"))
                        keys = {k: v for k, v in ps.items() if re.search(r"filament_map|extruder_id|filament_settings_id|filament_colour$", k)}
                        flat = re.sub(r"\s+", " ", ms)[:1500]
                        dump = f"\nmodel_settings: {flat}\nproject: {keys}"
                    return f"expected tools {sorted(tools)}, got {sorted(tc)} · {gcode_facts(gcode)}{dump}"
                return None
            return check

        painted = work / "painted_project.3mf"
        derive_project(source, painted, extruder=1, paint_state=2)
        run("3MF painted (base T1, top T2)", painted, {"filaments": fils}, expect({"0", "1"}, painted=True))

        per_object = work / "object_project.3mf"
        derive_project(source, per_object, extruder=2, paint_state=None)
        run("3MF object on T2", per_object, {"filaments": fils}, expect({"1"}, painted=False))

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
