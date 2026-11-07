# Desktop memory dependencies

The development build bundles unmodified dependencies, downloaded only at build time
from their official releases with fixed SHA-256 verification. No personal data enters
these downloads. Both wheel and matching source distribution are available inside
Contents/Resources; the Python library remains separately replaceable.

- pytypedstream 0.1.0, by dgelessus: LGPL-3.0-or-later.
  https://github.com/dgelessus/python-typedstream
  https://pypi.org/project/pytypedstream/0.1.0/
  The wheel includes COPYING/COPYING.LESSER in its dist-info metadata; the complete
  matching source and license files are shipped as pytypedstream-source.tar.gz.
  We call its parsing API without modifying its code. It does not instantiate native
  Objective-C classes. Runtime: Apple's /usr/bin/python3 (Command Line Tools required).
- GBrain 0.60.530.0: https://github.com/garrytan/gbrain/tree/v0.60.530.0
  Bundled official darwin-arm64 executable, isolated in the PersonalBrain data home.
  Upstream license: https://github.com/garrytan/gbrain/blob/v0.60.530.0/LICENSE
  Review upstream license/distribution terms before any commercial redistribution.

No model weights are bundled. Local learning uses an explicitly selected, already
installed Ollama model. Its license belongs to the installed model distribution.

## GBrain MIT license

Copyright (c) 2026 Garry Tan

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
