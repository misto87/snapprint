# SnapPrint Slice-Server

Slice-Server für den **Snapmaker U1** als Umbrel-App: Modelle vom iPhone (Safari oder die
App *SnapPrint U1*) hochladen, auf dem Umbrel mit der offiziellen **Snapmaker Orca** CLI
headless slicen und per Moonraker an den U1 schicken – ohne Mac/PC.

- **3MF**: Multicolor mit fertig bemalten bzw. pro Objekt zugewiesenen Dateien (max. 4 Farben).
  Farbe *n* wird auf Toolhead *T n* gedruckt.
- **STL**: immer einfarbig, Toolhead wählbar.
- Ausschließlich offizielle U1-Profile (machine/process/filament) aus der Snapmaker-Orca-AppImage,
  inklusive des offiziellen Start-/End-G-Codes. Es wird kein eigener G-Code eingefügt; ein Ergebnis
  ohne `PRINT_START` wird verworfen.
- Upload auf den U1 startet **nie** einen Druck. Gestartet wird nur nach ausdrücklicher
  Bestätigung in der UI (`POST /api/printer/start` mit `"confirm": true`, Drucker muss frei sein).
- G-Code-Dateinamen werden bereinigt: nur `A–Z a–z 0–9 . _ -`, nie mit `#` am Anfang.
- **Alle Orca-Einstellungen**: ~300 Prozess- und ~80 Filamentoptionen mit derselben Gliederung,
  Beschriftung und Erklärung (deutsch) wie in Snapmaker Orca – automatisch aus den Orca-Quellen der
  gebündelten Version erzeugt. Einfach-/Experten-Ansicht, Suche, Änderungen markiert und rücksetzbar,
  eigene Druck- und Filamentprofile auf dem Umbrel.
- Einstellungen aus U1-Projekt-3MFs werden übernommen (als Änderungen gegenüber dem Systemprofil).
- Vorschaubild des geslicten Modells.

## API

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/api/health` | Version |
| GET | `/api/profiles` | Düsen, Druckprofile, Filamente |
| GET/PUT | `/api/settings` | `printer_host`, `printer_port` |
| POST | `/api/uploads` | Multipart `file` (.3mf/.stl) → `upload_id`, Farb-Slots, Platten |
| POST | `/api/jobs` | `{upload_id, machine, process, process_overrides?, filaments:[{name,color,overrides?}], toolhead?, plate?, arrange?}` |
| GET | `/api/jobs/<id>` | Status `queued/slicing/done/error`, Statistik |
| GET | `/api/jobs/<id>/gcode` | G-Code herunterladen |
| POST | `/api/jobs/<id>/send` | G-Code auf den U1 hochladen (ohne Start), optional `{filename}` |
| GET | `/api/printer/status` | Status, 4 Toolheads inkl. geladenem Filament |
| GET | `/api/printer/info` | Firmware/Moonraker-Version |
| POST | `/api/printer/start` | `{filename, confirm: true}` |
| GET | `/api/schema` | Einstellungs-Schema (Seiten, Gruppen, Optionen) |
| GET | `/api/profile-values?kind=process\|filament&name=…` | Werte eines Systemprofils |
| GET/POST/DELETE | `/api/presets` | eigene Profile |
| GET | `/api/jobs/<id>/thumbnail.png` | Vorschaubild |

## Image

`ghcr.io/misto87/snapprint` wird von GitHub Actions gebaut (linux/amd64). Vor dem Push slict ein
Smoke-Test im Container einen Würfel auf T1/T3 und eine zweifarbige 3MF.

Snapmaker-Orca-Version ändern: `ORCA_VERSION`/`ORCA_TAG` im `Dockerfile`.

## Umbrel

Installation über den Community App Store [misto87/misto](https://github.com/misto87/misto)
(App `misto-snapprint`, Port 5535). Daten liegen unter `${APP_DATA_DIR}/data`.

## Lizenz

AGPL-3.0 – siehe [LICENSE](LICENSE) und [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
