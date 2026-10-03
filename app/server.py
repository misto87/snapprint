"""SnapPrint slice server: web UI + JSON API for slicing on the Umbrel and
sending G-code to a Snapmaker U1 via Moonraker."""
import json
import logging
import os
import re
import threading
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

from .moonraker import Moonraker, PrinterError
from .profiles import Profiles
from .settings import Presets, Schema
from .slicer import JobManager
from .util import sanitize_gcode_name

VERSION = os.environ.get("SNAPPRINT_VERSION", "dev")
DATA_DIR = Path(os.environ.get("SNAPPRINT_DATA_DIR", "/data"))
STATIC_DIR = Path(__file__).parent / "static"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("snapprint")

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024

DATA_DIR.mkdir(parents=True, exist_ok=True)
profiles = Profiles()
schema = Schema()
presets = Presets(DATA_DIR / "presets.json")
jobs = JobManager(DATA_DIR, profiles, schema)

_settings_lock = threading.Lock()
SETTINGS_FILE = DATA_DIR / "config.json"


def load_settings() -> dict:
    settings = {"printer_host": os.environ.get("SNAPPRINT_PRINTER_HOST", ""),
                "printer_port": int(os.environ.get("SNAPPRINT_PRINTER_PORT", "7125"))}
    if SETTINGS_FILE.exists():
        try:
            settings.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except ValueError:
            pass
    return settings


def printer() -> Moonraker:
    s = load_settings()
    return Moonraker(s["printer_host"], int(s["printer_port"]))


def error(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


@app.errorhandler(ValueError)
def _value_error(exc):
    return error(str(exc), 400)


@app.errorhandler(PrinterError)
def _printer_error(exc):
    return error(str(exc), 502)


@app.errorhandler(413)
def _too_large(_exc):
    return error("Datei ist zu groß (max. 300 MB)", 413)


# ---------- UI ----------
@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(STATIC_DIR, name)


# ---------- API ----------
@app.get("/api/health")
def health():
    return jsonify({"ok": True, "version": VERSION})


@app.get("/api/profiles")
def get_profiles():
    return jsonify(profiles.public())


@app.get("/api/schema")
def get_schema():
    resp = jsonify(schema.data)
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.get("/api/profile-values")
def profile_values():
    """Resolved values of all editable options for one system profile."""
    kind = request.args.get("kind", "")
    if kind not in ("process", "filament"):
        raise ValueError("Ungültiger Profiltyp")
    return jsonify(schema.values(profiles.load(kind, request.args.get("name", "")), kind))


@app.get("/api/presets")
def list_presets():
    return jsonify(presets.list())


@app.post("/api/presets")
def save_preset():
    return jsonify(presets.save(profiles, schema, request.get_json(force=True) or {})), 201


@app.delete("/api/presets/<preset_id>")
def delete_preset(preset_id):
    try:
        presets.delete(preset_id)
    except KeyError:
        return error("Profil nicht gefunden", 404)
    return jsonify({"deleted": preset_id})


@app.get("/api/settings")
def get_settings():
    return jsonify(load_settings())


@app.put("/api/settings")
def put_settings():
    body = request.get_json(force=True) or {}
    host = str(body.get("printer_host", "")).strip()
    if host and not re.fullmatch(r"[A-Za-z0-9.\-]{1,253}", host):
        raise ValueError("Ungültige Drucker-Adresse")
    port = int(body.get("printer_port", 7125))
    if not 1 <= port <= 65535:
        raise ValueError("Ungültiger Port")
    with _settings_lock:
        s = load_settings()
        s.update(printer_host=host, printer_port=port)
        SETTINGS_FILE.write_text(json.dumps(s), encoding="utf-8")
    return jsonify(s)


@app.post("/api/uploads")
def upload_model():
    f = request.files.get("file")
    if not f or not f.filename:
        raise ValueError("Keine Datei übergeben")
    return jsonify(jobs.save_upload(f.filename, f))


@app.post("/api/jobs")
def create_job():
    return jsonify(jobs.submit(request.get_json(force=True) or {})), 201


@app.get("/api/jobs/<job_id>")
def get_job(job_id):
    try:
        job = jobs.get(job_id)
    except KeyError:
        return error("Job nicht gefunden", 404)
    if job["state"] == "error" or request.args.get("log"):
        job["log"] = jobs.log_tail(job_id)
    return jsonify(job)


@app.get("/api/jobs/<job_id>/gcode")
def download_gcode(job_id):
    try:
        job = jobs.get(job_id)
    except KeyError:
        return error("Job nicht gefunden", 404)
    path = jobs.gcode_path(job_id)
    if job["state"] != "done" or not path.exists():
        return error("Kein G-Code vorhanden", 404)
    return send_file(path, as_attachment=True, download_name=job["gcode_name"],
                     mimetype="text/x-gcode")


@app.get("/api/jobs/<job_id>/thumbnail.png")
def job_thumbnail(job_id):
    try:
        jobs.get(job_id)
    except KeyError:
        return error("Job nicht gefunden", 404)
    path = jobs.thumbnail_path(job_id)
    if not path:
        return error("Kein Vorschaubild", 404)
    return send_file(path, mimetype="image/png", max_age=3600)


@app.post("/api/jobs/<job_id>/send")
def send_to_printer(job_id):
    """Upload the G-code to the U1. This never starts a print."""
    try:
        job = jobs.get(job_id)
    except KeyError:
        return error("Job nicht gefunden", 404)
    if job["state"] != "done":
        raise ValueError("Job ist noch nicht fertig gesliced")
    body = request.get_json(silent=True) or {}
    name = sanitize_gcode_name(body.get("filename") or job["gcode_name"])
    res = printer().upload(jobs.gcode_path(job_id), name)
    jobs.update(job_id, sent_as=res["path"])
    log.info("job %s uploaded to printer as %s", job_id, res["path"])
    return jsonify({"filename": res["path"]})


@app.get("/api/printer/info")
def printer_info():
    return jsonify(printer().info())


@app.get("/api/printer/status")
def printer_status():
    return jsonify(printer().status())


@app.post("/api/printer/start")
def printer_start():
    """Start a print. Requires an explicit confirmation flag from the UI."""
    body = request.get_json(force=True) or {}
    if body.get("confirm") is not True:
        raise ValueError("Druckstart muss ausdrücklich bestätigt werden")
    filename = str(body.get("filename", ""))
    if not filename or filename != sanitize_gcode_name(filename):
        raise ValueError("Ungültiger Dateiname")
    res = printer().start_print(filename)
    log.info("print started on printer: %s", filename)
    return jsonify(res)


def main():
    from waitress import serve
    port = int(os.environ.get("SNAPPRINT_PORT", "8080"))
    log.info("SnapPrint %s listening on :%d", VERSION, port)
    serve(app, host="0.0.0.0", port=port, threads=8, max_request_body_size=app.config["MAX_CONTENT_LENGTH"])


if __name__ == "__main__":
    main()
