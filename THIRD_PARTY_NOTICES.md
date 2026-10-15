# Third-Party Notices

## Reactive Resume

CareerGPS's CV template layer uses a Django-native layout adapter informed by the public template model and selected layout patterns from **Reactive Resume**, by Amruth Pillai.

- Project: Reactive Resume
- Upstream repository: `amruthpillai/reactive-resume`
- License: MIT
- Current template identifiers used by the CareerGPS adapter: `azurill`, `bronzor`, `chikorita`, `ditto`

CareerGPS does **not** embed Reactive Resume's authentication, database, API, or full web application. CareerGPS remains the owner of users, profile facts, applications, generated-document history, and fact-integrity controls.

The upstream MIT license permits use, modification, distribution, sublicensing, and private/commercial use provided the copyright and permission notice are retained in copies or substantial portions of the software.

Copyright (c) 2026 Amruth Pillai

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

## React PDF

Reactive Resume's current upstream PDF architecture uses `@react-pdf/renderer`. CareerGPS's present Django adapter renders PDFs with the existing ReportLab dependency, so no React PDF package is bundled in this build.
