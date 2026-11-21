# filament_to_bambuddy

[![Build and publish image](https://github.com/hibikipr/filament_to_bambuddy/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/hibikipr/filament_to_bambuddy/actions/workflows/docker-publish.yml)

> 🇩🇪 **Installation, Update und Fehlersuche auf Deutsch: [ANLEITUNG.md](ANLEITUNG.md).** Änderungen: [CHANGELOG.md](CHANGELOG.md).

Scan a third-party filament box barcode with your phone and add the new spool to
your [Bambuddy](https://github.com/) filament inventory.

It's a small mobile web app:

1. **Scan** the box's barcode (UPC/EAN, or a Code 128 manufacturer SKU/article
   number for boxes with no retail barcode at all) — or type the number.
2. **Look up** the product, in order:
   1. your **per-code cache** (anything you've confirmed before),
   2. the **[TigerTag catalogue](https://tigersystem.io)** (~16,000 filaments
      with the makers' article numbers and many EANs, CC0) first, then the
      **[Open Filament Database](https://openfilamentdatabase.org)** and
      **[SpoolmanDB-Community](https://github.com/Icezaza2543/SpoolmanDB-Community)** —
      cross-referenced against each other so a hit in one fills in fields the
      others are missing (e.g. print temps from one, colour from another),
   3. for a retail barcode neither knows (the Amazon long tail):
      **[UPCitemdb](https://www.upcitemdb.com)**, which indexes shop listings
      and offers — its product title is parsed into the form, and the average
      offer price is offered as the spool price,
   4. otherwise a blank form, with a **paste-the-title** auto-fill helper
      (English and German titles: "Seide", "Matt", "Schwarz", "1,75 mm"…).
   Or skip the barcode entirely and **📷 photograph the label** — on-device OCR
   reads the text and the same parser fills the form (works even over plain
   HTTP, unlike the live barcode camera).
3. **Review** the auto-filled details (correct anything), enter the **price
   per spool** (the price per kg follows, and the other way round) and
   optionally **📶 scan the spool's NFC tag** with the phone. Any other codes
   discovered for the same product (other package-size barcodes, the refill
   barcode, the manufacturer SKU) are shown as "Also matches" — informational
   only.
4. **Add** the spool(s) to Bambuddy via its inventory API. With Bambuddy's
   Spoolman integration switched on, the spools go to the Spoolman inventory
   behind it; the app reads the mode from Bambuddy, no setting needed.

The lookup also **learns**: whatever you confirm is remembered for that code
*and* every code cross-referenced alongside it, so a later scan of any of
them — the same barcode, a sibling package size, or the manufacturer SKU —
auto-fills instantly next time — price included, never the NFC tag, which
belongs to one physical spool. *(Scope: new, unopened spools still in the box.)*

### Invoices and Amazon orders

The **Invoices** card reads the invoice a spool came with (a PDF, or a photo
from the phone's camera: Tesseract OCR on the server) and puts the **gross
price paid** onto the spools — existing ones it suggests, or new ones — as
BamBuddy's `cost_per_kg`; shipping can be spread by price. Amazon's own
order-history export (ZIP) works the same way, without logging into Amazon.
Only the extracted data is kept, never the file or any address.

### NFC tags from the phone

The **📶 Scan** button reads a tag's UID with
[Web NFC](https://developer.mozilla.org/docs/Web/API/Web_NFC_API): Chrome on
Android, over https. It reads NDEF tags — the NTAG213/215 stickers the
[SpoolmanScale](https://github.com/AreYouThereAndi2/SpoolmanScale) writes and
links. **Bambu Lab's own spool tags are MIFARE Classic, which Web NFC cannot
read on any phone**; the page then says so. Put such a spool on the
SpoolmanScale instead, which reads and links it.

In Bambuddy's own inventory the tag goes into the create request; in Spoolman
mode it is linked right after (`PATCH /spools/{id}/tag`), because that create
request has no tag. A tag only makes sense with a quantity of 1.

**Write to NFC sticker** (in a spool's record, and after adding spools)
writes the spool's OpenSpool record onto an NTAG213/215
sticker, the record the SpoolmanScale reads: one NDEF record of type
`application/json` with `protocol`, `version`, `type` (material), `color_hex`,
`brand`, `min_temp`, `max_temp` and `spool_id`. The server builds it
(`GET /api/spool/<id>/openspool`, the body is exactly what is written) and
keeps it to 230 bytes: a long maker name is shortened first, then the
temperatures are left out. The sticker's UID, read in the same tap, is then
linked to the spool as a scanned tag is. **The sticker is never locked**: it is
written with overwrite and never made read-only, so it can be rewritten for the
next spool. A sticker that is already read-only is reported as such. Like the
scan, this needs Chrome on Android; on iOS the button is not shown.

### Amazon numbers

Amazon has no public barcode lookup, and reading its pages is against its
terms, so the app does neither. An **EAN/UPC** printed on the box goes to
UPCitemdb, which mostly covers what Amazon sells. An **ASIN** (`B0…`) or an
Amazon FNSKU label (`X00…`) is not a product barcode; for those, copy the
listing title into **Paste product title** and tap **Fill**.

### Storage locations

The **Storage locations** card shows BamBuddy's list of locations
(`/api/v1/inventory/locations`) as tiles, grouped by what the names start with
("Shelf A" for "Shelf A-01" … "Shelf A-12"). Each tile shows how many spools
are stored there and their colours as dots; empty places are drawn as an
outline only. A tap opens the place: its spools (a tap opens a spool's record),
**Rename** (BamBuddy moves the spools along, in Spoolman too), **Print label**
(the name large and a QR code holding it, on the label roll set up for spools)
and **Delete**, offered only for an empty place. Spools without a location are
behind the *No location* chip. Names that spools carry but BamBuddy's list
lacks are shown under *Unknown locations*, to adopt one by one or all at once.

**Create locations** makes up to 200 at once, in one of three ways:

- **List**: one name per line.
- **Range**: text before, from–to (numbers or letters), text after; *Leading
  zeros* gives `Shelf A-01` … `Shelf A-12`.
- **Grid**: rows × columns (e.g. A–D × 1–6) into a pattern such as
  `{row}{col}` or `Shelf {row} / Box {col}`.

The names appear before anything is created, those BamBuddy already has
struck through. Afterwards the new ones can be labelled in one go.

Routes: `GET /api/locations` (list merged with the stock, plus the spools
without a location and the unknown names), `POST /api/locations/preview`
(the names a pattern gives), `POST /api/locations/bulk` (`{"names": [...]}`,
each answered with `created`, `exists` or `error`), `PATCH` and `DELETE
/api/locations/<id>`. Reading needs the key's **Read Status**, changing it
**Manage Inventory**; a refusal names the one missing.

### Sorting and shelves

Lists of spools (*Your stock*, *All spools*, *Running low*, a location's
spools) sort by **material, colour, maker**, what is left, location, number or
date added, in two steps ("material, then colour"), each up or down. Colour
sorts by hue (then white → grey → black, then transparent), material by family
(PLA, PLA+, PLA Silk together). *All spools* shows a heading per group, and the
stock comes as statistics and as a CSV file in the same order.

**Sort into a shelf** plans which spool goes into which place of a group
("Regal A-1" … "Regal A-12", in name order) with a number of spools per place,
keeping as many spools where they are as the order allows. New spools wait
without a location and are placed here. The plan is carried out **step by
step** (one spool at a time, always into a place with room, rings of full
places through the table; *Done* writes the location at once; scanning the
spool's label or tag and the compartment's label checks each step) or booked
at once. **Scan & put away** puts any single spool where it belongs.

**2 on 1** (label card): two half-size labels on one roll label, stacked or
side by side, with a dashed cut line in the middle, for small holders; several
labels are paired, an odd last one printed twice or beside an empty half. The
scale asks for it with `GET /api/label/<id>.bits?twoup=stack|side`.

Routes: `GET /api/inventory?sort=material,color:desc`, `GET
/api/inventory.csv`, `POST /api/locations/plan` (the plan and its `steps`),
`POST /api/locations/apply` (`{"moves": [{"id", "from", "to"}]}`) and `POST
/api/locations/suggest` (a place for a new spool). Planning needs **Read
Status**, applying **Manage Inventory**.

> **Tested with** Bambuddy v0.2.4.8; the spool create payload (built-in inventory and Spoolman mode) is checked against Bambuddy 1.3.1's schemas. Requires **Python 3.10+** (the image runs 3.15).

---

## Setup

```bash
pip install -r requirements.txt
```

Then provide your Bambuddy details. The easiest way is a local **`run.sh`**
(gitignored, so your key never gets committed):

```bash
#!/bin/bash
export BAMBUDDY_URL="https://your-bambuddy.example.com"
export BAMBUDDY_API_KEY="your-bambuddy-api-key"
cd "$(dirname "$0")" && exec python3 app.py
```

```bash
chmod +x run.sh
./run.sh
```

…or just export the variables yourself and run `python3 app.py`. Then open
`http://<this-computer-ip>:8088/` on your phone (same network). On startup the
page shows a status line: 🟢 connected, 🟡 connected-but-permission-issue, or
🔴 unreachable — with the specific reason.

### Bambuddy API key permissions (important)

Create the key in **Bambuddy → Settings → API Keys** with these enabled:

- ✅ **Manage Inventory** — *required* (this is what creates spools; also
  creating, renaming and deleting storage locations)
- ✅ **Read Status** — the connection check, BamBuddy's inventory mode, the
  storage locations, and the imported slicer profiles and printer models the
  profile linking needs
- ☑️ **Send notifications** — optional, only for the message when filament runs out

Without **Manage Inventory** the add will fail with `403`; without **Read
Status** the status line shows an amber permission warning.

### Install it as an app (PWA)

The app is an installable **PWA** — add it to your phone's home screen for a
full-screen, app-like experience with its own icon:

- **Android (Chrome):** menu ⋮ → *Add to Home screen* / *Install app*.
- **iOS (Safari):** Share → *Add to Home Screen*.

Like the camera, the installable PWA (service worker / offline shell) needs a
**secure origin** (HTTPS or `localhost`) — see below.

### ⚠️ Camera access needs a secure origin

Phone browsers only allow camera access over **HTTPS** (or `localhost`). Opening
the app at `http://192.168.x.x:8088` will **not** grant the camera — but you can
always type the barcode number in the manual field, which works anywhere.

To enable the camera, serve the app over HTTPS, e.g.:
- behind a reverse proxy with TLS (Caddy / nginx / Cloudflare Tunnel), the same
  way Bambuddy itself is exposed, **or**
- a local HTTPS tunnel (e.g. `cloudflared`, `ngrok`).

### Run with Docker (in a stack)

A `Dockerfile` and `docker-compose.yml` are included. It runs under **gunicorn**
and persists its caches (`barcode_cache.json`, `ofd_index.json`) to a volume.

```bash
cp .env.example .env      # then edit .env with your BAMBUDDY_URL + API key
docker compose up -d --build
```

**Prebuilt image:** a GitHub Actions workflow publishes a multi-arch image
(amd64 + arm64) to **GHCR** on every push to `main` and on `v*` tags. Pull it
instead of building:

```yaml
    image: ghcr.io/areyouthereandi2/filament_to_bambuddy:latest
```

(This fork's workflows only run once GitHub Actions is switched on for it
under **Actions** in the repository; until then build from the checkout.)

(After the first publish, set the package's visibility to **Public** in your
GitHub package settings if you want to pull it without authenticating.)

The app is then on port **8088**. To add it to an existing stack, drop this
service into your compose (point `env_file` or `environment` at your config):

```yaml
  filament-to-bambuddy:
    build: ./filament_to_bambuddy      # or image: <your-built-image>
    restart: unless-stopped
    ports: ["8088:8088"]
    env_file: [./filament_to_bambuddy/.env]
    volumes: ["filament_data:/data"]
# (add `filament_data:` under your top-level `volumes:`)
```

For the **camera** and **PWA install** you still need HTTPS — route this
container through the same reverse proxy (Traefik / nginx / Caddy / Cloudflare
Tunnel) you use for Bambuddy, with its own hostname.

Docker-specific env vars: `OFD_CACHE_FILE` and `BARCODE_CACHE_FILE` default to
`/data/...` in the image so the caches land on the mounted volume.

### Configuration (environment variables)

| Variable | Default | Description |
|---|---|---|
| `BAMBUDDY_URL` | `http://localhost:8000` | Your Bambuddy base URL |
| `BAMBUDDY_API_KEY` | — | Bambuddy API key (**required**, needs *Manage Inventory*) |
| `DEFAULT_LABEL_WEIGHT` | `1000` | Net grams assumed when unknown |
| `BARCODE_CACHE_FILE` | `barcode_cache.json` | Where learned lookups are stored |
| `OFD_CACHE_FILE` | `ofd_index.json` | Where the OFD index is cached |
| `SPOOLMANDB_COMMUNITY_CACHE_FILE` | `spoolmandb_community_index.json` | Where the SpoolmanDB-Community index is cached |
| `TIGERTAG_CACHE_FILE` | `tigertag_index.json` | Where the TigerTag catalogue index is cached |
| `TIGERTAG` | `on` | `off` leaves the TigerTag catalogue out |
| `INVENTORY_MODE` | `auto` | `auto` asks Bambuddy; `spoolman` / `local` set the inventory (also on the page) |
| `SETTINGS_FILE` | `settings.json` | Where the page's inventory choice is kept |
| `SPOOLMAN_URL` | — | Your Spoolman as this server reaches it (`http://spoolman:7912`). In Spoolman mode its filaments come first: matched by article number, an EAN in an extra field, or brand, material and colour; the spool is made on that filament, and the filament's empty fields (temperatures, empty spool, price, article number) are filled in. Without it, BamBuddy's own Spoolman address is tried, then BamBuddy's filament list (no temperatures) |
| `BAMBUDDY_PUBLIC_URL` | — | Address a phone reaches Bambuddy at, for label QR codes; else Bambuddy's External URL |
| `PARSE_RULES_FILE` | `parse_rules.json` | The user's reading rules for label/title text |
| `HOST` / `PORT` | `0.0.0.0` / `8088` | Server bind address, only for `python app.py` (the Docker image always listens on 8088) |
| `UPC_LOOKUP` | `on` | `off` disables the UPCitemdb fallback |
| `UPCITEMDB_KEY` | — | Paid UPCitemdb key; without one the free trial is used (100 lookups a day per IP) |
| `PRINT_BRIDGE_URL` | — | The print bridge for the TYPONOS PM240 (`http://printbridge.local`, `http://192.168.0.50`): labels go to it through this server. Only `http(s)://host[:port]`. Also settable on the page (*More → Print bridge*), which wins. See [Print bridge](#print-bridge-typonos-pm240) |
| `PRINT_BRIDGE_KEY` | — | The bridge's API key, if one is set on it (sent as `X-Api-Key`; never shown on the page) |
| `AUTH_MODE` | `off` | `bambuddy`: the page asks for a BamBuddy user (with 2FA), and every BamBuddy request made for it carries that user's token, so BamBuddy checks their permissions and records who changed what. `off`: no sign-in, everything with the API key. See [Sign-in through BamBuddy](#sign-in-through-bambuddy) |

The Open Filament Database dump is downloaded once and cached in
`ofd_index.json`, refreshed daily (or on demand via the **Refresh DB** button).

### Print bridge (TYPONOS PM240)

The PM240 prints only over **Bluetooth Classic (SPP)**; its Bluetooth LE
channels accept data and print nothing, and browsers (Web Bluetooth) only
speak BLE. A small ESP32 next to the printer, the *print bridge*
("Druckbrücke"), takes the job over HTTP on the LAN and sends it on over
SPP. The page is https and the bridge plain http, so the page sends the
same TSPL bytes to this server, which forwards them; no Home Assistant or
other service is involved.

Set `PRINT_BRIDGE_URL` (and `PRINT_BRIDGE_KEY` if the bridge has one), or
enter both under *More → Print bridge* (kept server-side in
`print_bridge.json` beside the settings, mode 600). With a bridge set, every
print (single, batch, 2-on-1, location labels, the step guide's labels) goes
through it; without one the page falls back to Web Bluetooth for printers
that do print over BLE.

| Endpoint | Permission | |
|---|---|---|
| `GET /api/print/status` | `inventory:read` | the bridge's `GET /api/status` (3 s timeout) plus `configured`, `reachable` |
| `POST /api/print/raw` | `inventory:read` | body = the raw job (`application/octet-stream`, ≤ 512 KiB) → bridge `POST /api/print`; the bridge's JSON and status (409 busy, 412 no printer, 502 connect/write failed, 401 api key); bridge down → `502 {"ok":false,"error":"unreachable"}` |
| `POST /api/print/test` | `inventory:read` | the bridge's own test label (`/api/test`) |
| `GET /api/print/bridge` | — | `url`, `source` (`env`/`settings`), `key_set` — never the key |
| `POST /api/print/bridge` | `settings:update` | `{"url": …, "key": …}`; `"url": ""` falls back to the environment, `"key": ""` clears it, `{"reset": true}` forgets both |

Only the one configured base address is ever contacted, on fixed paths and
without following redirects.

### Sign-in through BamBuddy

With `AUTH_MODE=bambuddy` the page and its API need a BamBuddy user
(BamBuddy's own sign-in must be on: *Settings → Users*). The sign-in page
sends the name and password to BamBuddy (`POST /api/v1/auth/login`, and
`/auth/2fa/verify` or the email code when the user has two-step
verification); the password is never stored. The token BamBuddy hands back is
kept on this server in a session (`sessions.json` in the data directory,
mode 600, next to a secret made on the first start, `f2b_secret.key`); the
browser holds a random id in an HttpOnly, SameSite=Lax cookie, Secure on
https (also behind a proxy that sends `X-Forwarded-Proto: https`).

- **Actions run as the user.** Every BamBuddy request made for the page
  carries the user's token (`Authorization: Bearer …`), so BamBuddy checks
  their permissions and its history shows who added or moved a spool.
  `BAMBUDDY_API_KEY` is still used for what runs without a browser (the
  running-out message, *Send notifications*) and for BamBuddy settings read
  into caches all users share (inventory mode, Spoolman address, external URL,
  slicer profiles); keep it set. The stock list cache is kept per user.
- **Permissions** come from BamBuddy (`/auth/me`): adding and changing spools,
  locations and shelf plans need `inventory:update`, reading the stock
  `inventory:read`, the inventory mode and the label layout `settings:update`,
  the running-out message `notifications:update`; admins may do everything.
  The page greys out what the user may not do, and the server refuses it with
  `403` naming the permission.
- **Sessions** last as long as BamBuddy's token (BamBuddy's *session_max_hours*,
  24 hours by default; BamBuddy has no refresh). *Stay signed in* keeps the
  cookie until then; otherwise it ends with the browser. When it ends the page
  shows the sign-in over itself and nothing typed is lost. *Sign out* (the
  initial in the header) also revokes the token in BamBuddy.
- **Links** such as `/?barcode=…`, `/?spool=12` or a share open the sign-in
  first and come back afterwards.
- **Changes** from the page carry a CSRF token. Failed sign-ins are limited per
  address here too (10 in 15 minutes), besides BamBuddy's own limit.
- **The SpoolmanScale** calls `GET /api/lookup`, `POST /api/spool` and
  `GET /api/label/<id>.bits` itself. It has to send its BamBuddy API key in
  `X-API-Key`; the key is checked with BamBuddy (`/auth/me`, the answer kept
  five minutes) and the scale's request is made with it. **Update the scale's
  firmware before switching `AUTH_MODE` on**; an older one gets `401`.

---

## How it maps to Bambuddy

Spools are created via `POST /api/v1/inventory/spools`, or
`POST /api/v1/spoolman/inventory/spools` when Bambuddy runs with Spoolman (Bambuddy
then stores the price as the spool price). Only **`material`** is
required; the form also sends brand, subtype, colour name + RGBA, net weight,
storage location, category, nozzle temps, cost/kg and a note where available.
Each created spool is tagged `data_origin = "barcode-scan"`.

## Project layout

| File | Purpose |
|---|---|
| `auth.py` | Sign-in through BamBuddy (`AUTH_MODE=bambuddy`): sessions, 2FA, CSRF, permissions, the scale's API key; `templates/login.html`, `static/auth.js` |
| `app.py` | Flask backend: serves the page; `/api/lookup`, `/api/parse`, `/api/spool`, `/api/health`; the learning cache; posts to Bambuddy |
| `ofd.py` | Open Filament Database client — builds the GTIN + article-number (SKU) indexes, cached/refreshed daily |
| `tigertag.py` | TigerTag catalogue client — EAN + article-number (SKU) indexes from TigerTag's CC0 catalogue, cached/refreshed daily |
| `spoolmandb_community.py` | SpoolmanDB-Community client — builds the GTIN + SKU indexes from the community filament repo, cached/refreshed daily |
| `catalog_util.py` | Helpers the catalogue clients share: GTIN canonicalisation, `#RRGGBB` → RRGGBBAA |
| `filament_parse.py` | Heuristics turning product/label text into filament fields (paste-title + label OCR), English and German |
| `label_reader.py` | What a label says beyond that: codes to try in order (FNSKU skipped), effect, dual-colour parts, BamBuddy's material spelling (from the original app, #29) |
| `locations.py` | Storage locations: names from a list, a range or a grid; the locations merged with the stock and grouped for the tiles |
| `spool_sort.py` | Sort keys (material family, colour hue, maker …), the shelf plan that keeps most spools in place, the steps to get there, and a place for a new spool |
| `invoices.py`, `invoice_extract.py`, `invoice_parse.py`, `amazon_orders.py` | Invoices: routes and the record of imported ones; text from PDFs and OCR of scans/photos; reading the table into items with gross prices; Amazon's order history export |
| `print_bridge.py` | Labels through the print bridge (ESP32, classic Bluetooth) for the PM240: `/api/print/*`, its address and key |
| `upc_lookup.py` | UPCitemdb fallback for retail barcodes the filament databases don't know; title + average offer price |
| `templates/index.html` | Mobile UI (barcode scan + manual entry + editable form), Bambuddy dark/green theme |
| `static/manifest.webmanifest`, `static/sw.js`, `static/icons/` | PWA manifest, service worker, and app icons |

Not in the repo (gitignored): `run.sh` (your secrets), `barcode_cache.json`
(your learned lookups), `ofd_index.json` (downloaded database cache).

---

## License

Licensed under the **GNU Affero General Public License v3.0 or later**
(AGPL-3.0-or-later), matching Bambuddy. See [LICENSE](LICENSE).

Copyright © 2026 Victor Manuel ([hibikipr](https://github.com/hibikipr)).

---

🤖 Built with [Claude Code](https://claude.com/claude-code)
