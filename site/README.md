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

Only the reviewed overview and poster are included here. The public website does
not include source recordings or editing material. Keep the MP4 and WebM versions
in sync when updating the video.

When changing `style.css` or `page.js`, update its `?v=` content hash in
`index.html` (the first 12 characters of SHA-256). This prevents returning
visitors from combining new page markup with older cached styles or scripts.
