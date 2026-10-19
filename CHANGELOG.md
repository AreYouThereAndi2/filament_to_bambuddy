# Changelog

Releases of the fork [AreYouThereAndi2/filament_to_bambuddy](https://github.com/AreYouThereAndi2/filament_to_bambuddy). Installation and updates in German: [ANLEITUNG.md](ANLEITUNG.md).

## Unreleased

### New
- **The scale creates spools through the app** (`POST /api/spool`), with everything the page does. It falls back to creating them itself only when the app can't be reached.
- **Labels from the scale in the phone's layout.** The layout arranged on the page is kept on the server too (`/api/label-layout`, `/data/label_layout.json`). `GET /api/label/<id>.bits` draws a spool's label as 1-bit rows for a label printer, and `.png` shows it. Same lines, sizes, band, QR side and font (Barlow Semi Condensed, now served by the app, also for the page offline). A new phone takes the kept layout.
- **A message when filament runs out.** Every few hours (`REORDER_CHECK_HOURS`, default 3) the stock is read. A kind (maker, material, line, colour) whose spools together hold less than the limit is reported once through BamBuddy's notification channels (`/api/v1/notifications/app-message`). The key needs **Send notifications**, and a channel needs **Messages from connected apps**. Switch it on in *Your stock → Running low*; *Check now* sends it at once.
- Holding a Bambu spool's chip to the phone (MIFARE Classic, unreadable by Web NFC) now says to find the spool with the search or put it on the scale.
- **Slicer profiles linked to spools, per printer model.** Any filament profile imported into BamBuddy (OrcaSlicer, Bambu Studio, any maker) is matched to a spool by maker, material and line ("Matte", "Silk", "PLA+" rather than "PLA"). A profile belongs to a printer model ("@BBL X1C", "@BBL P1S 0.2 nozzle"), so the app reads your printers from BamBuddy and gives the spool its line's profile for each of your models: the most used model's on the spool, the others (and other nozzles) as BamBuddy's per-model profiles. Profiles set in BamBuddy are kept.
  - **Form:** a *Slicer profile* field, suggested by itself and searchable, shows what each printer loads. The lookup returns the suggestion too (`slicer_filament`).
  - **Spool record:** change the profile; a spool without one gets a suggestion, taken over with Save.
  - **Your stock → Link slicer profiles:** every spool without a profile, listed with the one that fits, then linked at once. Spools with an imported profile get the rows their other printer models lack.
  - New: `GET /api/presets`, `GET /api/presets/plan`, `POST /api/presets/link-all`. They need the key's **Read Status**.
- **Arrange the own label yourself.** Each line can be shown or hidden, moved up or down, set in a size (XS–XL), bold, or white on a black band. The QR code goes right, left or off, in three sizes. The lines stack from the top, keep the last line at the bottom, or spread over the height. A label pinned above the rows shows every change at once. What does not fit the roll shrinks alike. The slicer profile can be a line too.
- **Your stock** (new card), read once from the active inventory (`GET /api/inventory`, archived spools left out, kept 30 s):
  - **Search:** every word narrows the list (maker, material, colour, location, tag, article number, `#12`); `<200` keeps spools with less than 200 g left. A tap opens the spool's record.
  - **Running low:** spools under a threshold (200 g, changeable), grouped by kind, with what is left, the last price paid and a *Buy more* search.
- **Hold a tag to the phone to open its spool** (Chrome on Android): the scan card's NFC button reads the tag and opens the spool it is linked to.
- **Already in stock:** the form says when spools of the same maker, material and colour are there already, with their numbers, locations and what is left; a tap opens one.
- **Several labels at once:** the label card takes `12, 15, 20-24`, *All from a location…* or *Added today*; the preview shows the first, **Print** sends them one after another over one Bluetooth connection. After adding several spools, all of them are offered.
- **Empty: archive** in a spool's record archives it in BamBuddy (`POST /api/spool/<id>/archive`), after a question.
- **Scan a spool's QR code to open it.** The QR code on a spool's label (BamBuddy's `…/inventory?spool=12`, Spoolman's `web+spoolman:s-12` or `/spool/show/12`), the same link pasted into the code field, `#12` typed there, or the page opened as `/?spool=12` shows that spool's record instead of a lookup: material, maker, colour, what is left, article number and date. There the NFC tag can be linked afterwards (read with the phone or typed), the storage location and the note changed and what is left corrected; **Print label** goes to the label card with the spool's number, **Open in BamBuddy** opens it there. New `PATCH /api/spool/<id>`: fields through BamBuddy's spool update, the tag through its link route (Spoolman's `/tag` or the built-in `/link-tag`); a tag another spool carries is refused and said.
- **Search your Spoolman.** Under *Scan barcode*, in Spoolman mode: "…or search your Spoolman". Every word typed narrows the list (maker, name, material, colour, article number), with the colour as a dot and material, weight and article number below. Taking one fills the form from that filament and links the spool to it; a code scanned before stays on the form.
- **Fields you can type in.** Material, maker, subtype, colour name, net weight and storage location are type-ahead lists instead of drop-downs: typing narrows the list, a tap or Enter takes an entry, and what the list does not have is offered as "Add “…”". Typed in another case ("pet"), a known entry takes the list's spelling. Keyboard: arrows, Enter, Escape.
- **The inventory status folds away.** Below the inventory select only a light and one line remain (green connected, amber address missing, red not reachable or refused); a tap shows the details and *Check again*.

### Fixed
- **BamBuddy's inventory mode wins.** Set by hand to the other inventory than BamBuddy uses, the app posted spools where BamBuddy does not show them. It now follows BamBuddy whenever BamBuddy answers, and says so in red. The mode set by hand only counts when BamBuddy refuses the question. A create refused because BamBuddy just switched asks again and says to tap *Add* once more.
- **Empty spool weight no longer frozen on each spool.** The linked Spoolman filament's (or its maker's) empty-spool weight was copied onto every new spool, so a later correction of the filament never reached them. Now it stays on the filament; a different weight goes onto the filament when it has none.
- Colour names BamBuddy made up from the line ("Matte") are no longer shown as colours.
- **Spool record:** *Weighed (gross)* gives what is left minus the empty spool. A Bambu RFID is shown, and replacing it with another tag asks first: behind Spoolman a spool has one tag, and the AMS and the scale know the spool by it.
- The filament databases are rebuilt in the background at start and before they expire, so the day's first scan no longer waits for them (the scale gave up after 15 s). `?quick=1` lookups skip the shop price.
- "Amazon code" was shown for every unknown article number. It now appears only for Amazon's own codes (ASIN `B0…`, FNSKU `X0…`); other unknown codes read "not in the database".
- **Spoolman status.** Under the inventory select a status line says whether the app reads your Spoolman itself: green with the number of filaments and the address (and whether it came from `SPOOLMAN_URL` or BamBuddy), amber when no address is set, red with the reason when it is set but not read (no answer, connection refused, an HTTP code, or an answer that is not Spoolman), and what the app does meanwhile. **Check again** reads Spoolman at once. The reason also goes to the log.
- `SPOOLMAN_URL` is taken as meant: without `http://`, with a trailing slash or with `/api/v1` on the end.
- **Light, dark or as the system says.** A button at the top right switches between the three; the choice stays in this browser and is applied before the page is drawn, so it never flashes the other look. The browser bar follows.
- **A new mark.** A spool from the side whose windings are a barcode, with the strand running off: the app's job in one picture. Used in the header, as the favicon and for the home-screen icons.
- Line icons instead of emoji on the buttons and headings; the connection line under the title keeps to two lines until tapped.
- **Your own Spoolman first.** In Spoolman mode the app reads your Spoolman's filaments (`SPOOLMAN_URL`, else BamBuddy's Spoolman address, else BamBuddy's filament list) and looks there first: by the filament's article number or an EAN kept in an extra field, else by brand, material and colour after any other hit. A match fills the form with the filament's own values (nozzle and bed temperature, empty spool weight, price) and the spool is created **on that filament** (`spoolman_filament_id`), so it carries all of it; "don't link" leaves the choice to BamBuddy. After adding, the filament's empty fields are filled from the form: temperatures, empty spool, price, and the scanned code as its article number, so the next scan finds it by code. The bed temperature then goes onto the filament instead of the note. The footer says how many filaments were read and whether directly.
- **Empty spool weight** field ("core weight"), sent to BamBuddy in both inventories.
- **BamBuddy inventory by hand.** At the bottom of the page: *Automatic* (asked of BamBuddy, as before), *Spoolman (through BamBuddy)* or *BamBuddy's own inventory*. BamBuddy only answers the question to a key with **Read Status**; without it the app used to assume the own inventory, which is wrong behind Spoolman. The page now says so. Also `INVENTORY_MODE=auto|spoolman|local`; the choice is kept in `/data/settings.json`.
- **Own label layout.** The label card draws its own layout on the whole roll (50 × 30 mm by default) with every ticked line: maker, material, colour and colour code, storage location, nozzle temperature, weight, article number, note (e.g. the print bed), date added, the spool's number and the QR code. BamBuddy's 40 × 30 template leaves out what its size has no room for, which is why location and temperature were missing; its templates stay selectable. The QR code opens the spool in BamBuddy at its *External URL*, or `BAMBUDDY_PUBLIC_URL`.
- **Print bed min/max.** New fields, filled from TigerTag, the Open Filament Database and SpoolmanDB-Community. BamBuddy has no bed temperature on a spool, so it is added to the note ("Print bed: 45–55 °C").
- **More from the databases.** After any hit (code, label, title, shop page) the Open Filament Database and SpoolmanDB-Community are also searched by brand, material, line and colour, and fill what is still empty: nozzle and bed temperatures for the same line, the colour code for the same colour.
- **Price filled in.** An empty *price per spool* takes the suggested price at once (UPCitemdb's offers, or the shop page's). After a database hit the app now also asks UPCitemdb for the price when the product has an EAN.
- **The product name in the title field** after a database hit, ready to be changed and filled again.
- **A new look.** Graphite instead of black, with BamBuddy's green kept for the one thing that adds a spool; follows the phone's light or dark mode. The form opens with the spool itself: a reel in the filament's colour with material, brand, colour and weight beside it, updated as the fields change. The form is grouped (data from a shop page, filament, amount & storage, printing & price, tag & note) and **Add** stays at the bottom of the screen while scrolling. Connection status sits under the title; refresh and clear-cache moved to the footer.
- **See how a label or title was read, and correct it.** After a label photo or a pasted title, "🔍 How the text was read" shows the text with each field's place marked and, per field, where it came from: the text, a rule, the database or the default. The text can be corrected and read again; a selected piece of text can be assigned to a field, once or as a **rule** ("Marmor" → subtype Marble) that applies to later label photos, titles and shop pages. Rules are kept in `/data/parse_rules.json` and listed for deletion.
- **TigerTag catalogue first.** Lookups ask [TigerTag](https://tigersystem.io)'s product catalogue (~16,000 filaments, CC0) before the Open Filament Database and SpoolmanDB-Community: makers' article numbers such as Geeetech `700-001-1535` and many EANs resolve right away, with brand, material, subtype, colour, weight and nozzle temperatures. The other databases fill what it leaves open. Refreshed daily and with **Refresh DB**; `TIGERTAG=off` leaves it out.
- **Hand a shop page back to the app.** Copy the page (Share → Copy, also in the Amazon app) and tap **📋 From the clipboard**, or paste the link or the text into the title field. Google result links (`google.com/url?…`) are followed to the shop. The server reads the page once for its Product data: name, brand, EAN, price; on a page with several colours, the one whose article number or EAN is the searched code. The app remembers which code the user searched the web for and lands on it. On Android, **Share → Filament** does the same once the app is installed.
- The web search looks for the plain code, without quotes or "filament".
- The article number and the page's EAN are both remembered when the spool is added.
- **🔦 Torch** while scanning, wherever the browser allows a page to switch it.
- Amazon codes: an FNSKU (`X00…`) explains itself and links to the orders, and an ASIN (`B0…`) opens its listing.
- **🏷️ Spool labels from the phone.** The label card fetches a spool's label from BamBuddy, in BamBuddy's own templates (also in Spoolman mode), and shows it dot for dot as the label printer will print it. **🖨️ Print** sends it to a TYPONOS PM240 over Bluetooth (Chrome on Android), with the same TSPL job the SpoolmanScale sends. **Share image** hands the PNG to the printer's own app, e.g. on an iPhone, where Safari has no Bluetooth. Nothing is printed without a tap. After adding spools, their ids are offered right there. Needs BamBuddy 1.2.5.7 or newer.
- iOS: the NFC button is hidden. Web NFC does not exist there; the field for a UID stays.

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
