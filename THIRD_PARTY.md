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

The optional FRRouting profile runs the separately downloaded
[upstream FRR container](https://github.com/FRRouting/frr). Its software and
notices remain in that image under their upstream licenses. FRR binaries/source
are not bundled into Weblab's standard image or source tree. The Weblab profile
and TAP adapter are independently implemented.


The optional Agent companion installs `@openai/codex` 0.160.0 (package license:
Apache-2.0) and the separately pinned MCP Python SDK/dependencies. These are
installed at image build time. The Codex 0.160.0 upstream
[license](packaging/agent/Codex-LICENSE.txt) and
[notice](packaging/agent/Codex-NOTICE.txt) are retained in the companion at
`/usr/local/share/licenses/codex/`, alongside installed package notices; they are not
part of the standard Weblab image. Node.js and Debian packages retain their own
licenses. The independently implemented Weblab bridge and UI use the project's
MIT license. Model weights are supplied separately by the operator's model server.

The prebuilt `weblab-agent` image redistributes the unmodified Codex package,
retaining its Apache-2.0 license and NOTICE. Weblab does not claim OpenAI
endorsement. That software license does not grant model-service access: each
operator uses their own eligible account or local model service. OpenAI's
[app-server authentication guidance](https://learn.chatgpt.com/docs/app-server#authentication)
distinguishes local/open-source integrations from commercial or hosted services;
the current device-login integration is intended for owner-operated self-hosted
Weblab, not a public hosted login service.
