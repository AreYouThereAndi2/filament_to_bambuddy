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

> **Tested with** Bambuddy v0.2.4.8. Requires **Python 3.10+**.

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

The Open Filament Database dump is downloaded once and cached in
`ofd_index.json`, refreshed daily (or on demand via the **Refresh DB** button).

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
| `app.py` | Flask backend: serves the page; `/api/lookup`, `/api/parse`, `/api/spool`, `/api/health`; the learning cache; posts to Bambuddy |
| `ofd.py` | Open Filament Database client — builds the GTIN + article-number (SKU) indexes, cached/refreshed daily |
| `tigertag.py` | TigerTag catalogue client — EAN + article-number (SKU) indexes from TigerTag's CC0 catalogue, cached/refreshed daily |
| `spoolmandb_community.py` | SpoolmanDB-Community client — builds the GTIN + SKU indexes from the community filament repo, cached/refreshed daily |
| `catalog_util.py` | Helpers the catalogue clients share: GTIN canonicalisation, `#RRGGBB` → RRGGBBAA |
| `filament_parse.py` | Heuristics turning product/label text into filament fields (paste-title + label OCR), English and German |
| `locations.py` | Storage locations: names from a list, a range or a grid; the locations merged with the stock and grouped for the tiles |
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
