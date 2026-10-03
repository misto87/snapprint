import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.util import sanitize_gcode_name  # noqa: E402

CASES = {
    "#Benchy.3mf": "Benchy.gcode",
    "##__-Würfel groß (v2).stl": "Wuerfel_gross_v2.gcode",
    "Halter #3 – 0.2mm.3mf": "Halter_3_0.2mm.gcode",
    "...": "snapprint.gcode",
    "../../etc/passwd": "etc_passwd.gcode",
    "日本語.stl": "snapprint.gcode",
    "plate_1.gcode": "plate_1.gcode",
}


def test_sanitize():
    for raw, expected in CASES.items():
        got = sanitize_gcode_name(raw)
        assert got == expected, f"{raw!r}: {got!r} != {expected!r}"
        assert not got.startswith("#")
        assert all(c.isalnum() or c in "._-" for c in got)


if __name__ == "__main__":
    test_sanitize()
    print("ok")
