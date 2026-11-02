"""Replace only the OS share boundary; exports, files and UI remain real."""

import json
from typing import Literal

from playwright.sync_api import Page


def install_native_share(
    page: Page,
    mode: Literal["supported", "missing", "files-unsupported", "probe-error"] = "supported",
) -> None:
    """Record the exact native payload without launching an external application."""
    page.add_init_script(
        "const nativeShareMode = "
        + json.dumps(mode)
        + ";"
        + """
        window.nativeShares = [];
        Object.defineProperty(navigator, 'canShare', {configurable:true, value:data => {
          if (nativeShareMode === 'probe-error') throw new TypeError('Unsupported payload');
          return !(nativeShareMode === 'files-unsupported' && data.files);
        }});
        Object.defineProperty(navigator, 'share', {configurable:true,
          value:nativeShareMode === 'missing' ? undefined : async data => {
            const active = navigator.userActivation.isActive;
            const files = await Promise.all((data.files || []).map(async file => ({
              name:file.name, type:file.type, bytes:[...new Uint8Array(await file.arrayBuffer())]
            })));
            window.nativeShares.push({
              title:data.title, text:data.text, files, active, url:data.url
            });
            if (window.nativeShareError) {
              throw new DOMException('Share rejected', window.nativeShareError);
            }
          }
        });
        """
    )
