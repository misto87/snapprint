# Third-party notices

SnapPrint is licensed under the GNU Affero General Public License v3.0 (see `LICENSE`).
It includes or uses the following third-party work.

## Snapmaker Orca (AGPL-3.0)

- https://github.com/Snapmaker/OrcaSlicer
- The Docker image downloads and runs the official Snapmaker Orca Linux AppImage unmodified
  and uses its bundled Snapmaker U1 machine/process/filament profiles.
- `tools/build_schema.py` reads `src/libslic3r/PrintConfig.cpp`, `src/slic3r/GUI/Tab.cpp` and
  `localization/i18n/de/Snapmaker_Orca_de.po` of the matching release at build time to generate
  the settings schema (option labels, tooltips, layout and German translations).
- Copyright (c) Snapmaker, the OrcaSlicer, Bambu Studio, PrusaSlicer and Slic3r contributors.

## SnapSlice (MIT)

- https://github.com/mon593-dot/SnapSlice
- `app/paint.py` is a Python port of `engine/snapslice-paint.js` (3MF paint data decoder).
  The MIT license notice is kept in that file.

```
MIT License

Copyright (c) 2026 SnapSlice contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Python packages (Ubuntu 24.04 packages)

Flask (BSD-3-Clause), Waitress (ZPL-2.1), Requests (Apache-2.0).
