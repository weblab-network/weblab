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
images into `site/assets/`, using stable filenames. The current page uses the
workspace, replacement floating-window, link-action and image-upload screenshots.

The relative asset paths work at a GitHub Pages project URL or a custom-domain
root. `.github/workflows/pages.yml` publishes only `site/` on changes to this
directory on `main`; it can also be run manually. Select **GitHub Actions** in
the repository’s **Settings → Pages → Source**. Set the custom domain there;
a CNAME file is not needed for an Actions deployment. The website does not run
the lab backend.
