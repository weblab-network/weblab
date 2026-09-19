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
source and the standard container image. Weblab does not generate device licenses.

The optional **weblab-exos** demo container additionally distributes the unmodified
Virtual EXOS 33.1.1.31 QCOW2 linked by the
[official upstream repository](https://github.com/extremenetworks/Virtual_EXOS).
The bundled topology starts with unconfigured switches. The upstream redistribution
notice is preserved in [Virtual-EXOS.txt](licenses/Virtual-EXOS.txt). The binary
is a build artifact, not a source-repository file. Metadata and
checksum are pinned in [image.json](packaging/exos/image.json); existing notices
inside the virtual image remain intact. EXOS is not covered by Weblab's MIT license.

Container runtime packages (including Debian, QEMU, Perl, Python and the Docker
client) retain their own licenses. Debian package copyright notices are retained
under `/usr/share/doc/` in the image. Container tags identify the corresponding
Weblab source revision; `iou2net.pl` is also included as source in `/app/`.
