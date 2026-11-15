# turbo-browser-mcp attribution

The local capture tools' optional `return_image` delivery behavior adapts the
`returnImage` pattern in `capturePage` from
[turbo-browser-mcp](https://github.com/Tthanh2k6/turbo-browser-mcp/blob/41c16f177aa27ecdbdfe04ff7dae45b1d5d385fe/src/dom-engine.mjs#L6172),
commit `41c16f177aa27ecdbdfe04ff7dae45b1d5d385fe`.

Studio implements the option in its existing Python MCP handlers, preserving
the renderer, saved images and version metadata. Images remain included by
default. No additional browser server or dependency is bundled. The upstream
copyright and permission notice is retained below.

## MIT License

Copyright (c) 2026 Trung Thanh Tran

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
