# Changelog

Releases of the fork [AreYouThereAndi2/filament_to_bambuddy](https://github.com/AreYouThereAndi2/filament_to_bambuddy). Installation and updates in German: [ANLEITUNG.md](ANLEITUNG.md).

## v1.0.0

This is the fork's first release. It is built for **BamBuddy with Spoolman behind it** and for the **SpoolmanScale** barcode scanner.

### New
- **Spoolman mode.** The app reads BamBuddy's inventory mode itself. With Spoolman switched on, new spools go to `/api/v1/spoolman/inventory` and appear in Spoolman. `POST /api/spool` returns the ids of the new spools, so the SpoolmanScale can open a spool right after creating it.
- **Amazon EANs with data.** If the Open Filament Database and SpoolmanDB-Community don't know a box barcode, the app asks [UPCitemdb](https://www.upcitemdb.com), which collects listings from Amazon and other shops.
  - The product title fills the form.
  - The average offer price, in the currency most offers use, is shown as a suggestion with **Use**.
  - Free tier: 100 lookups a day per IP. `UPCITEMDB_KEY` sets a paid key, `UPC_LOOKUP=off` turns the lookup off.
- **Price per spool.** A new field next to cost/kg; each one follows the other through the net weight. If Spoolman refuses the price, the spool is still created and the app says so.
- **NFC tag from the phone.** The 📶 button reads a tag's UID with Web NFC (Chrome on Android, https).
  - In BamBuddy's own inventory the tag goes into the create request. In Spoolman mode it is linked right after.
  - The tag is never remembered per barcode.
  - Bambu Lab's MIFARE Classic tags cannot be read by Web NFC on any phone; the page explains this.
- **German product titles.** Auto-fill understands German colours ("Schwarz", "Weiß"…), German subtypes ("Seide", "Matt", "Holz", "Marmor", "Glitzer"…) and decimal commas ("1,75 mm", "1,1 kg").

### Fixed
- The Docker image now contains `upc_lookup.py`. Without it a container built from `main` after the UPCitemdb change stopped at start. A test now checks that every module the app imports is copied into the image.
- `docker compose` passes `F2B_VERSION` into the build, so the page footer shows the version that is running instead of `dev`.

### Update
```bash
git pull
F2B_VERSION=$(git describe --tags --always) docker compose up -d --build
```
If `app.py` or `templates/index.html` were mounted into the container from outside, remove those mounts first (see ANLEITUNG.md).
