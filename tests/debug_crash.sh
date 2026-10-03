#!/bin/bash
# CI-only diagnostics (runs as root inside the image): backtrace of a CLI crash
# and a few profile-format variants. Prints one GitHub annotation.
set -u
apt-get update -qq >/dev/null && apt-get install -y -qq gdb >/dev/null 2>&1
W=/tmp/dbg; mkdir -p $W/out
python3 - <<'EOF'
import sys; sys.path.insert(0, "/opt/snapprint")
from tests.smoke import cube_stl
from pathlib import Path
cube_stl(Path("/tmp/dbg/cube.stl"))
EOF
P=/opt/snapprint/profiles
M="$P/machine/Snapmaker_U1_0.4_nozzle.json"
PR="$P/process/0.20mm_Standard_Snapmaker_U1_0.4_nozzle.json"
SRC="$(dirname "$(find /opt/orca -type d -path '*/profiles/Snapmaker' | head -n1)")/Snapmaker"
BIN="$(find /opt/orca/bin -maxdepth 1 -type f -perm -u+x | head -n1)"
{
echo "AppRun: $(head -c 300 /opt/orca/AppRun | tr '\n' ' ' | tr -cd '[:print:]')"
echo "bin: $(ls /opt/orca/bin | tr '\n' ' ')"
python3 - "$M" "$PR" "$W" <<'EOF'
import json, sys
m, p, w = sys.argv[1:]
for src, dst, frm in ((m, "m_user.json", "User"), (p, "p_user.json", "User")):
    d = json.load(open(src)); d["from"] = frm; d["inherits"] = ""
    json.dump(d, open(f"{w}/{dst}", "w"))
for src, dst in ((m, "m_noinh.json"), (p, "p_noinh.json")):
    d = json.load(open(src)); d.pop("inherits", None)
    json.dump(d, open(f"{w}/{dst}", "w"))
EOF
try() {
  local name=$1; shift
  rm -rf $W/out/*; timeout 300 /opt/orca/AppRun "$@" --slice 0 --outputdir $W/out $W/cube.stl >$W/log 2>&1
  echo "variant $name: exit $? files=$(ls $W/out | tr '\n' ' ') :: $(grep -v '\[trace\]' $W/log | grep -v '^\s*$' | tail -2 | tr '\n' ' ' | cut -c1-300)"
}
try flat-system --load-settings "$M;$PR"
try flat-user --load-settings "$W/m_user.json;$W/p_user.json"
try flat-noinherits-key --load-settings "$W/m_noinh.json;$W/p_noinh.json"
try orig-machine-only --load-settings "$SRC/machine/Snapmaker U1 (0.4 nozzle).json"
try flat-machine-only --load-settings "$M"
try flat-process-only --load-settings "$PR"
echo "--- gdb backtrace (flat-system) ---"
LIBS="$(find /opt/orca -name '*.so*' -printf '%h\n' | sort -u | tr '\n' ':')"
LD_LIBRARY_PATH="$LIBS" gdb -q -batch -ex run -ex bt \
  --args "$BIN" --load-settings "$M;$PR" --slice 0 --outputdir $W/out $W/cube.stl 2>&1 \
  | grep -E '^#[0-9]+|signal|SIG' | head -25 | cut -c1-220
} > $W/report.txt 2>&1
cat $W/report.txt
python3 -c "import sys; d=open('/tmp/dbg/report.txt').read()[-3800:]; print('::warning title=crash debug::' + d.replace('%','%25').replace('\r','').replace('\n','%0A'))"
