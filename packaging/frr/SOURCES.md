# Weblab FRR binary and source distribution

Weblab FRR combines FRRouting 10.7.1, Weblab's CLI patch and MIT integration
helpers with Alpine, lldpd, Net-SNMP and their dependencies. Each component keeps
its own license. The container as a whole is **not** licensed under Weblab's MIT
license. See `NOTICE`, upstream `COPYING` and source-file license headers.

The v0.8.0 release bundle contains a Docker image archive, a corresponding source
archive, package inventory and SHA-256 checksums. Keep these together when
redistributing the binary. The source archive contains:

- `build/frr-source.tar`: the exact upstream commit, including license texts;
- `build/lldp-vtysh.patch`: Weblab changes with dated notices;
- `build/Dockerfile`, helpers and startup script: compilation/install inputs;
- `dependencies/`: inherited APK metadata, Alpine recipes/patches and distfiles
  verified by `abuild`, plus the upstream custom libyang recipe and source;
- `image.json`: the built image identity and labels, tying sources to the binary.

`packages.json` inventories APKs, not a claim that Weblab's rebuilt FRR binaries
are the original APK payload. The pinned FRR base contains custom FRR/libyang
packages marked `-dirty`; the collector explicitly handles their pinned sources
instead of assigning them an invented Alpine commit. The host kernel and Docker
Engine are prerequisites and are not included in this image.

To rebuild, extract the source archive and run `docker build -t weblab-frr:local
build/`. Docker fetches the pinned base and Alpine build dependencies. The
dependency bundle includes recipes and sources for the distributed runtime;
it is not an offline toolchain or a promise of bit-for-bit reproducibility.
Locally rebuilt images may resolve newer Alpine dependencies and require their
own inventory/source bundle. Weblab's untested-image opt-in applies to such builds.

The builder's `--context-dir` option retains the complete build context. The
release bundler collects sources from the **same image** that it archives; a
failed collection prevents producing a completed bundle. Publishing the image
without the corresponding source archive is not the release procedure.
