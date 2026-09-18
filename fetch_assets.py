#!/usr/bin/env python3
"""Download the pinned browser assets once; the lab then works offline."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen

VENDOR = Path(__file__).resolve().parent / "web" / "vendor"
ASSETS = {
    "markdown-it.js": "markdown-it@15.0.2/dist/browser/markdown-it.umd.min.js",
    "markdown-it-LICENSE": "markdown-it@15.0.2/LICENSE",
    "xterm.js": "@xterm/xterm@5.5.0/lib/xterm.js",
    "xterm.css": "@xterm/xterm@5.5.0/css/xterm.css",
    "xterm-LICENSE": "@xterm/xterm@5.5.0/LICENSE",
    "addon-fit.js": "@xterm/addon-fit@0.10.0/lib/addon-fit.js",
    "addon-fit-LICENSE": "@xterm/addon-fit@0.10.0/LICENSE",
}


def download(item):
    name, package_path = item
    with urlopen("https://cdn.jsdelivr.net/npm/" + package_path, timeout=45) as response:
        data = response.read()
    (VENDOR / name).write_bytes(data)
    print(f"Saved {name} ({len(data)} bytes)")


if __name__ == "__main__":
    VENDOR.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(download, ASSETS.items()))
