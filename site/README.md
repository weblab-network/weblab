# Project landing page

Static project website, separate from the lab application's `web/` directory.
No build step, external fonts, analytics or third-party JavaScript are required.

Preview from the repository root:

```sh
python3 -m http.server 18088 --bind 127.0.0.1 --directory site
```

Open `http://127.0.0.1:18088/`. When working on a remote host, forward that port.
The copy button appears only when the browser supports secure clipboard access;
the command remains selectable everywhere.

Keep original media in the ignored `1st_page/` staging folder. Copy only reviewed
images and finished videos into `site/assets/`, using stable filenames. The page
also uses workspace, floating-window, link-action and image-upload screenshots.

The relative asset paths work at a GitHub Pages project URL or a custom-domain
root. `.github/workflows/pages.yml` publishes only `site/` on changes to this
directory on `main`; it can also be run manually. Select **GitHub Actions** in
the repository’s **Settings → Pages → Source**. Set the custom domain there;
a CNAME file is not needed for an Actions deployment. The website does not run
the lab backend.

## Video overview

`/#watch` links directly to the overview section. The 1:55 video uses native
browser controls, an explicit poster, `playsinline` and `preload="none"`; it does
not autoplay. A keyboard-accessible poster button starts playback; native controls
remain available without JavaScript. MP4 is offered first, with a WebM fallback. A written overview is
available alongside the silent, captioned video. The hosted preview is linked
separately and identified as having restricted controls.

The website includes the reviewed overview, its poster and the approved full
walkthrough. Editing material is kept separately. Keep the overview’s MP4 and
WebM versions in sync when updating that video.

When changing `style.css` or `page.js`, update its `?v=` content hash in
`index.html` (the first 12 characters of SHA-256). This prevents returning
visitors from combining new page markup with older cached styles or scripts.

## Full walkthrough

`/#walkthrough` opens the optional full-length recording beneath the short
overview. The original 9:08 MP4 is available through a native player, a download
link and a YouTube alternative. It uses `preload="none"` and no autoplay;
collapsing the section pauses playback. No YouTube embed or external player
is loaded on page visit. The recording shows the full workspace at normal speed.

## README screenshot gallery

The repository README links to `showcase-topology.png`,
`showcase-tabbed-consoles.png` and `showcase-arranged-consoles.png` in `assets/`.
These screenshots retain the supplied image dimensions; HTML widths make their
README previews compact. Keep these public asset URLs stable.

## Tablet and phone screenshots

`/#mobile` shows fullscreen tablet and phone layouts, with full-size image links.
The README also includes browser and inspector tablet views in a collapsible gallery.
The four `assets/mobile-*.png` screenshots keep the approved captures intact apart
from removing the desktop title bars. Preserve their aspect ratios and keyboards;
use CSS or HTML widths for previews and keep the public asset URLs stable.

## Contact

`/#contact` points to the footer's Show email button. It assembles the project
address only after activation, then displays a selectable mail link. This reduces
basic plaintext scraping, but cannot prevent determined collection. Without
JavaScript, the footer offers GitHub issues as an alternative. Keep the README
linked to this section rather than duplicating the address in Markdown.

## Agent demonstrations

`/#agent-videos` contains two captioned, silent MP4 highlights alongside the
existing overview, with YouTube alternatives. A selector shows one full-width
recording at a time. `/#agent-create-exercise` and `/#agent-configure-verify` select
the respective recording directly; switching pauses the hidden player. Without
JavaScript, both recordings remain visible at full width. Both preserve the full source
frame, use preload=none and do not autoplay. Adjacent text identifies Agent-issued
console commands and device output. The creation exercise intentionally leaves
OSPF and RSTP tasks unfinished; the hosted restricted preview has no Agent login.
Keep the video, thumbnail and full-size screenshot URLs stable: the README and
Agent help page link to these assets.
