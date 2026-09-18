# Third-party components

The root [MIT License](LICENSE) applies to Weblab’s original code. The components
below retain their own licenses and are not relicensed by that file.

- `web/vendor/xterm.js` and `xterm.css`: xterm.js 5.5.0, MIT license;
  see `web/vendor/xterm-LICENSE`.
- `web/vendor/addon-fit.js`: @xterm/addon-fit 0.10.0, MIT license;
  see `web/vendor/addon-fit-LICENSE`.
- `web/vendor/markdown-it.js`: markdown-it 15.0.2, MIT license;
  see `web/vendor/markdown-it-LICENSE`. Browser assets are pinned in `fetch_assets.py`.
- `iou2net.pl`: iou2net 0.5 by "einval", from the
  [repository maintained by Jeremy L. Gaddis](https://github.com/jlgaddis/iou2net),
  licensed under GNU GPL version 2. See [the license](licenses/iou2net-GPL-2.0.txt).
  The Weblab copy adds creation of the local socket directory before use.
  This component retains its own license.

Vendor images and any required licenses are user-supplied and excluded from
source and container images. Weblab does not generate device licenses.
