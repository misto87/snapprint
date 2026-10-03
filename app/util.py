import re
import unicodedata

_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


def sanitize_gcode_name(name: str, max_len: int = 80) -> str:
    """Printer-safe G-code filename: ASCII letters, digits, '.', '_', '-' only,
    never starting with '#' (or any other non-alphanumeric character)."""
    stem = re.sub(r"\.(gcode|3mf|stl)$", "", name or "", flags=re.IGNORECASE)
    stem = stem.translate(_TRANSLIT)
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)
    stem = re.sub(r"_{2,}", "_", stem)
    stem = re.sub(r"^[^A-Za-z0-9]+", "", stem)
    stem = stem[:max_len].rstrip("._-")
    return (stem or "snapprint") + ".gcode"


def parse_gcode_stats(path) -> dict:
    """Read the slicer summary comments Orca writes into the G-code."""
    stats = {}
    patterns = {
        "print_time": re.compile(r"^; estimated printing time \(normal mode\) = (.+)$"),
        "filament_g": re.compile(r"^; total filament used \[g\] = ([\d.]+)"),
        "filament_mm": re.compile(r"^; filament used \[mm\] = (.+)$"),
        "layers": re.compile(r"^; total layer number: (\d+)"),
    }
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    for line in lines[:400] + lines[-400:]:
        line = line.strip()
        for key, rx in patterns.items():
            if key not in stats:
                m = rx.match(line)
                if m:
                    stats[key] = m.group(1).strip()
    return stats
