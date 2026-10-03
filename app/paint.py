# Decoding of Bambu/Orca 3MF "paint_color" triangle data.
#
# Ported to Python from SnapSlice (engine/snapslice-paint.js),
# https://github.com/mon593-dot/SnapSlice
#
# MIT License
#
# Copyright (c) 2026 SnapSlice contributors
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""Each triangle's paint_color is a hex string read back to front as a nibble
stream describing a subdivision tree. A leaf nibble has split bits 00 and holds
the filament state (0 = object's own filament, n = filament n; 3+ continues in
following nibbles). Split nibbles subdivide the triangle into 2, 3 or 4 parts."""
import re

_HEX = re.compile(r"^[0-9A-Fa-f]+$")
_CHILDREN = {1: 2, 2: 3, 3: 4}
MAX_DEPTH = 16


class PaintError(ValueError):
    pass


def paint_states(encoded: str) -> set:
    """Return the set of filament states used in one triangle's paint data."""
    if not encoded:
        return {0}
    if not _HEX.match(encoded) or len(encoded) > 65536:
        raise PaintError("Ungültige Bemalungsdaten in der 3MF")
    codes = [int(c, 16) for c in reversed(encoded)]
    pos = 0
    states = set()

    def nxt():
        nonlocal pos
        if pos >= len(codes):
            raise PaintError("Abgeschnittene Bemalungsdaten in der 3MF")
        pos += 1
        return codes[pos - 1]

    # Iterative depth-first walk; only the structure matters, not the geometry.
    stack = [0]
    while stack:
        depth = stack.pop()
        if depth > MAX_DEPTH:
            raise PaintError("Bemalung ist zu fein unterteilt")
        code = nxt()
        split = code & 3
        if not split:
            state = code >> 2
            if state == 3:
                while True:
                    nibble = nxt()
                    state += nibble
                    if nibble != 15:
                        break
            states.add(state)
            continue
        side = code >> 2
        if side > 2 or (split == 3 and side != 0):
            raise PaintError("Ungültige Unterteilung in den Bemalungsdaten")
        stack.extend([depth + 1] * _CHILDREN[split])
    if pos != len(codes):
        raise PaintError("Überzählige Bemalungsdaten in der 3MF")
    return states


_ATTR = re.compile(rb'(?:paint_color|mmu_segmentation)="([0-9A-Fa-f]+)"')


def used_paint_states(model_xml: bytes) -> set:
    """All non-zero filament states referenced by paint data in a .model file."""
    used = set()
    for raw in set(_ATTR.findall(model_xml)):
        used |= paint_states(raw.decode("ascii"))
    used.discard(0)
    return used
