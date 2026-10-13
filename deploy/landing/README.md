# next-role.dev landing page

The pre-launch marketing page for [next-role.dev](https://next-role.dev). Plain HTML, CSS, and a few lines of vanilla JavaScript; no build step. Upload this folder as-is.

## Files

| Path | Purpose |
| --- | --- |
| `index.html` | The landing page. Fonts from Google Fonts; everything else is inline. |
| `assets/` | Logo, app screenshot (demo data only), Open Graph image, favicons. Copies of `docs/images/*` and `frontend/src/app/favicon.ico`. |
| `_headers` | Cloudflare Pages security headers + long cache for `assets/`. |
| `robots.txt` | Allows crawling. |

## Deploy (Cloudflare Pages, drag and drop)

1. Cloudflare dashboard → **Workers & Pages** → **Create application** → **Pages** → **Upload assets** (drag and drop).
2. Name the project (for example `next-role-landing`), then drag the whole `deploy/landing/` folder into the upload frame and **Deploy site**. The site is served from `<project>.pages.dev`.
3. Project → **Custom domains** → add `next-role.dev` and `www.next-role.dev`. The zone is already on Cloudflare, so the DNS records are created for you.
4. Re-deploying is the same upload again; `_headers` ships with the folder on every deploy.

The same folder also deploys from the CLI if you prefer: `npx wrangler pages deploy deploy/landing --project-name next-role-landing`.

## Post-deploy checklist

- **Email.** The page's contact CTA is `hello@next-role.dev`. Set up **Email Routing** for the zone (Email → Email Routing → create the address and forward it to your inbox) or that mailto bounces.
- **Analytics.** Nothing to add. Cloudflare Web Analytics is injected automatically at the edge because the domain is proxied through Cloudflare; the dashboard (Analytics & Logs → Web Analytics → next-role.dev) already shows visits and Core Web Vitals. Do not paste a manual beacon tag into `index.html`, it would double-count. The `_headers` CSP already allows the injected beacon.
- **Social preview.** Paste `https://next-role.dev` into a card validator (LinkedIn Post Inspector, X card validator, or the Facebook sharing debugger) to confirm `assets/og-image.png` is picked up.
- **Star count.** The GitHub button fetches `api.github.com` from the browser. The unauthenticated limit is per visitor IP, so it only disappears for people who hammer the API; the button still works without it.

## Preview locally

```bash
cd deploy/landing
python3 -m http.server 8787
# open http://localhost:8787/
```

Absolute `/assets/...` paths are used so the page works at the domain root. If you ever serve it from a sub-path, make them relative.

## Editing

Brand tokens at the top of `index.html` mirror the app's palette in `frontend/src/app/globals.css` (paper + espresso, emerald accent) and the font trio in `frontend/src/app/layout.tsx`. Keep the two in sync when the app's design changes. All product claims on the page are taken from the root `README.md`; update both when a feature changes.
