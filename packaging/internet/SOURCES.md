# Internet helper binary and source distribution

The release helper is an aggregation of Weblab's MIT code and unmodified Alpine
packages under their respective licenses. It is not wholly MIT licensed. See
[NOTICE](NOTICE) and the retained upstream notices in the release image.

The matching Weblab release supplies:

- `weblab-internet-linux-amd64.tar.gz`: the tested Docker image;
- `weblab-internet-sources.tar.gz`: exact runtime package recipes, patches,
  checksum-verified upstream distfiles, upstream notices and Weblab build inputs;
- `weblab-internet-image.json`: image/config identity, public source revision,
  package inventory and hashes of original build inputs;
- `weblab-internet-NOTICE.txt` and `weblab-internet-SHA256SUMS`.

Keep the binary and matching source materials available together when redistributing.
The image retains notices/licenses, runtime package inventory and Weblab build
inputs under `/usr/share/weblab-internet/`; corresponding source downloads are
linked from its notice and OCI source/version labels. No vendor image is included.

The collector snapshots the installed APK database before installing build tools.
It resolves each runtime origin at the exact recorded Alpine aports commit and
uses `abuild fetch verify` to validate distfiles. Unknown/dirty source identities
fail collection. Builder-only packages are not represented as runtime packages.
Recipes and patches for meta-packages with no external distfile are retained too.

Build both targets from the same checkout/arguments, then bundle the pair:

```sh
docker build -f packaging/internet/Dockerfile.release --target sources \
  -t weblab-internet-sources:local .
docker build -f packaging/internet/Dockerfile.release --target release \
  -t weblab-internet:release-local .
python3 tools/bundle_internet_release.py --image weblab-internet:release-local \
  --sources-image weblab-internet-sources:local --output /tmp/internet-bundle
python3 tools/verify_internet_bundle.py /tmp/internet-bundle
```

The output directory must be new. A completed checksum file is written last;
failure never produces a completed pair. The bundler compares APK snapshots,
package inventories and build-input hashes before archiving the selected image.
It refuses a development image or image tags that change during collection.
From 0.11.1, the offline verifier hashes every image layer, verifies retained
source files against an inventory embedded in the runtime, and checks executable
helper code against the retained build inputs. Missing or altered upstream
distfiles fail verification even when the outer archive checksums are regenerated.
This stricter verifier requires the 0.11.1 source inventory; use an older release's
own verifier for its original bundles.

For publication, CI sets `WEBLAB_VERSION` to the release version and
`WEBLAB_REVISION` to the public GitHub commit, tests the release image, and uploads
the pair. The release job re-verifies its expected version/revision, publishes
source downloads, then loads/pushes that exact binary without rebuilding. Branch
checks retain artifacts; they do not publish a helper registry tag. First registry
publication must also verify anonymous pull access to the new GHCR package.

To rebuild original helper code, extract the source archive and run
`docker build --target release -f build/packaging/internet/Dockerfile.release build/`.
Runtime package sources can be rebuilt with their matching Alpine release,
APKBUILD and abuild toolchain. This is not an offline build environment or a
promise of bit-identical binaries. A rebuild that resolves newer APKs needs its
own matching inventory/source bundle. Docker and the host kernel are prerequisites.
