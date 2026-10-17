# filament_to_bambuddy: Installation und Update (deutsch)

Diese Anleitung gilt für den Fork [AreYouThereAndi2/filament_to_bambuddy](https://github.com/AreYouThereAndi2/filament_to_bambuddy). Sie geht von einem Heimserver mit Docker aus, zum Beispiel einem ZimaBoard mit CasaOS. Die englische [README](README.md) beschreibt alle Funktionen im Detail.

## Was die App macht

- Box-Barcode (EAN/UPC, Hersteller-Nummer) mit dem Handy scannen oder eintippen.
- Die App sucht das Produkt im eigenen Cache, in der Open Filament Database und in SpoolmanDB-Community. Kennen die beiden Datenbanken eine EAN nicht, fragt sie UPCitemdb, das die Angebote von Amazon und anderen Shops sammelt. Von dort kommen Titel, Marke und der **Durchschnittspreis pro Rolle**.
- Formular prüfen, Preis übernehmen, auf Wunsch den NFC-Tag mit dem Handy lesen, dann **Hinzufügen**: Die Spule landet in BamBuddy, bei eingeschaltetem Spoolman-Modus in Spoolman.
- Die **SpoolmanScale** fragt dieselbe App, wenn ihr Barcode-Scanner einen unbekannten Code liest, und legt die Spule dann selbst an.

## Voraussetzungen

- Docker mit `docker compose` (bei CasaOS vorhanden) und `git`.
- BamBuddy erreichbar im Netz.
- Ein **BamBuddy-API-Schlüssel** (BamBuddy → Settings → API Keys) mit
  - **Manage Inventory** (legt Spulen an, Pflicht),
  - **Read Status** (für die Verbindungsanzeige).

## Neu installieren

Per SSH auf den Server:

```bash
cd /DATA/AppData            # oder ein anderer Ordner für eigene Apps
git clone https://github.com/AreYouThereAndi2/filament_to_bambuddy.git
cd filament_to_bambuddy
cp .env.example .env
nano .env                   # BAMBUDDY_URL und BAMBUDDY_API_KEY eintragen
F2B_VERSION=$(git describe --tags --always) docker compose up -d --build
```

Die `.env` sieht so aus:

```ini
BAMBUDDY_URL=http://zimaboard:8000
BAMBUDDY_API_KEY=bb_xxxxxxxx
# optional:
# DEFAULT_LABEL_WEIGHT=1000
# UPCITEMDB_KEY=...          # bezahlter UPCitemdb-Schlüssel, sonst 100 Abfragen/Tag frei
# UPC_LOOKUP=off             # UPCitemdb ganz abschalten
```

Danach läuft die App auf Port **8088**: `http://<server-ip>:8088`. Die Statuszeile oben zeigt 🟢, wenn BamBuddy erreichbar ist und der Schlüssel passt. Unten im Fußbereich steht die Version.

### ZimaOS: „mkdir /root/.docker: read-only file system“

Auf ZimaOS ist das Systemlaufwerk schreibgeschützt, auch `/root`. Docker legt dort beim Bauen seinen Konfigurationsordner an und scheitert, auch mit `sudo`. Abhilfe: Docker einen beschreibbaren Ordner unter `/DATA` geben.

```bash
sudo mkdir -p /DATA/.docker
sudo DOCKER_CONFIG=/DATA/.docker F2B_VERSION=$(git describe --tags --always) docker compose up -d --build
```

`DOCKER_CONFIG` muss **hinter** `sudo` stehen, sonst kommt die Variable bei Docker nicht an. Dauerhaft: `export DOCKER_CONFIG=/DATA/.docker` in die `~/.profile` des eigenen Benutzers schreiben und Docker ohne `sudo` aufrufen, falls der Benutzer in der Gruppe `docker` ist.

## Den bestehenden Container umstellen

Auf dem Server läuft im Moment ein Container, in den Gemini `app.py` und `index.html` von außen hineingelegt hat. Diese Dateien sind jetzt im Fork enthalten, und zwar in korrigierter Form. Die Bind-Mounts müssen weg, sonst überschreiben die alten Dateien die neuen.

1. **Daten sichern (optional, aber sinnvoll).** Der gelernte Barcode-Cache liegt im Container unter `/data`. In CasaOS unter App → Einstellungen → Volumes nachsehen, welcher Host-Ordner auf `/data` zeigt, und ihn kopieren. Ohne Sicherung lernt die App alles neu; die Datenbank-Indizes lädt sie ohnehin selbst.
2. **Alten Container stoppen und entfernen.** In CasaOS: App → Deinstallieren. Oder per SSH: `docker ps` (Namen suchen), dann `docker rm -f <name>`.
3. **Neu installieren** wie oben.
4. **Alten Cache weiterverwenden:** Im `docker-compose.yml` die Zeile unter `volumes:` von `filament_data:/data` auf den alten Host-Ordner ändern, z. B.:
   ```yaml
       volumes:
         - /DATA/AppData/filament_to_bambuddy/data:/data
   ```
   Dann `docker compose up -d --build`.
5. **Prüfen:** `docker logs filament_to_bambuddy` darf keinen `ModuleNotFoundError` zeigen. Die Seite muss beim Scan einer Amazon-EAN die Plakette „EAN-Datenbank (UPCitemdb)“ und einen Preisvorschlag anzeigen.

> Hat CasaOS den alten Container aus einem fertigen Image (`ghcr.io/hibikipr/...`) gestartet, enthält das Image keine der neuen Funktionen. Gebaut wird jetzt immer aus dem Fork (`build: .`).

## Aktualisieren

```bash
cd /DATA/AppData/filament_to_bambuddy
git pull
F2B_VERSION=$(git describe --tags --always) docker compose up -d --build
docker image prune -f       # alte Images aufräumen (optional)
```

Die Version im Fußbereich der Seite zeigt danach den neuen Stand, z. B. `v1.0.0` oder `v1.0.0-3-gabc1234` für Commits nach dem Tag. Die `.env` und das Daten-Volume bleiben bei einem Update erhalten.

**Zurück auf einen älteren Stand:** `git checkout v1.0.0`, dann denselben `docker compose`-Befehl.

## HTTPS für Kamera, NFC und App-Installation

Handy-Browser geben Kamera und NFC nur über **HTTPS** frei. Über `http://192.168…:8088` funktioniert nur die Eingabe von Hand und das Foto vom Etikett.

Möglichkeiten:

- Ein Reverse Proxy mit Zertifikat (Nginx Proxy Manager, Caddy, Traefik), über den auch BamBuddy läuft, mit einem eigenen Hostnamen für die App.
- Ein Cloudflare Tunnel oder `tailscale serve` auf Port 8088.

Die **Waage** braucht kein HTTPS. Sie spricht die App direkt über `http://<server-ip>:8088` an.

## NFC am Handy

- Geht nur mit **Chrome auf Android** und nur über HTTPS.
- Gelesen werden **NTAG**-Sticker (NTAG213/215), wie sie die SpoolmanScale beschreibt.
- **Bambu-Lab-Tags sind MIFARE Classic.** Die kann Web NFC auf keinem Handy lesen: Chrome fragt nach der Berechtigung, und der Scan bricht danach mit einem Lesefehler ab oder liefert nichts. Die Seite sagt das jetzt. Solche Spulen auf die Waage legen, die liest und verknüpft sie.
- Nach 20 Sekunden ohne Tag bricht der Scan ab; ein zweiter Tipp auf den Knopf bricht ihn sofort ab.

## Amazon-Nummern und Preis

- Eine **EAN** auf der Box geht an UPCitemdb. Kennt UPCitemdb sie, füllt der Titel das Formular, und der Durchschnittspreis erscheint unter dem Preisfeld mit „Übernehmen“. Er wird nie von selbst eingetragen.
- Eine **ASIN** (`B0…`) oder ein Amazon-Lageretikett (`X00…`) ist kein Produkt-Barcode. Dafür den Titel aus dem Amazon-Angebot in das Feld **Produkttitel zum automatischen Ausfüllen einfügen** kopieren und **Ausfüllen** tippen. Deutsche Titel werden verstanden („Seide“, „Matt“, „Schwarz“, „1,75 mm“, „1 kg“).
- Die kostenlose Stufe von UPCitemdb erlaubt 100 Abfragen pro Tag und IP. Was einmal gefunden wurde, merkt sich die App.
- Der Preis wird pro Rolle eingegeben, der Preis pro kg rechnet sich über das Nettogewicht mit. Im Spoolman-Modus legt BamBuddy daraus den Spulenpreis an.

## Als App installieren (iPhone und Android)

Die Seite ist eine installierbare Web-App (PWA). Sie braucht HTTPS (siehe oben).

- **iPhone/iPad:** In Safari öffnen → Teilen → **Zum Home-Bildschirm**.
- **Android:** In Chrome öffnen → ⋮ → **App installieren** (oder „Zum Startbildschirm hinzufügen“).

Was geht wo:

| Funktion | Android (Chrome / App) | iPhone (Safari / App) |
|---|---|---|
| Barcode mit der Kamera, Etikett fotografieren | ja | ja |
| 🔦 Licht beim Scannen | ja | nur wenn iOS es der Seite erlaubt; sonst erscheint der Knopf nicht |
| NFC-Tag lesen | ja (NTAG) | nein: Apple lässt Web-Apps nicht an NFC, den Tag legt die Waage an |
| Shop-Seite per Lesezeichen „→ Filament“ übernehmen | ja | ja |
| Shop-Seite per **Teilen → Filament** übernehmen | ja, mit installierter App | nein: iOS bietet Web-Apps nicht im Teilen-Menü an |

## Daten von einer Shop-Seite übernehmen

Kennt keine Datenbank den Code (z. B. eine Artikelnummer wie `700-001-1535`), so geht es ohne Abtippen:

1. In der App den Code suchen, dann **„🔍 Im Web nach diesem Code suchen“** tippen. Die App merkt sich den Code für zwei Stunden.
2. In den Ergebnissen die Produktseite öffnen (Amazon, 3DJake, Herstellerseite …).
3. Auf der Produktseite das Lesezeichen **„→ Filament“** antippen. Die App öffnet sich mit Name, Marke, Material, Farbe und Preis der Seite. Hat die Seite eine EAN, schlägt die App diese zusätzlich in den Datenbanken nach.
4. Prüfen, **Hinzufügen**. Die App merkt sich die Daten für die Artikelnummer und für die EAN der Seite: Beim nächsten Scan sind sie sofort da.

**Am einfachsten, ohne Einrichtung (iPhone und Android):** Auf der Produktseite oder in der Shop-App (auch Amazon) **Teilen → Kopieren**, zurück in die App und **„📋 Aus der Zwischenablage übernehmen“** tippen. Alternativ den Link oder den kopierten Text ins Feld „Produkttitel …“ einfügen und **Ausfüllen** tippen. Aus dem Link liest der Server die Seite: Bei normalen Shops kommen Name, EAN und Preis. Amazon gibt Servern keine Seite heraus, dort zählt der mitkopierte Name aus „Teilen → Kopieren“.

**Das Lesezeichen einrichten:** In der App ganz unten „📲 Shop-Knopf einrichten“ aufklappen, **Code kopieren** und der Anleitung dort folgen. Kurz:
- **iPhone:** In Safari irgendeine Seite als Lesezeichen sichern, Name „→ Filament“. Dann Lesezeichen → Bearbeiten → das Lesezeichen öffnen → Adresse durch den kopierten Code ersetzen. Benutzen: Lesezeichen → „→ Filament“.
- **Android:** In Chrome ein Lesezeichen anlegen, bearbeiten, Name „Filament“, URL = kopierter Code. Benutzen: in die Adresszeile „Filament“ tippen und den Lesezeichen-Eintrag mit dem Stern wählen.

Das Lesezeichen läuft im eigenen Browser auf der Seite, die man gerade ansieht. Die App lädt dafür nichts von Amazon. Links aus der installierten App öffnen sich manchmal in einem eingebauten Browser ohne Lesezeichen: dort „In Safari öffnen“ bzw. „In Chrome öffnen“ wählen.

**Android, noch einfacher:** Mit installierter App auf der Produktseite **Teilen → Filament** wählen, auch in der Amazon-App. Die App bekommt Titel und Link. Bei normalen Shops liest der Server die geteilte Seite einmal und holt EAN und Preis dazu. Amazon gibt Servern keine Seite heraus, dort reicht der Titel.

## Etiketten drucken (TYPONOS PM240)

Die Karte **🏷️ Etikett drucken** holt das Etikett einer Spule fertig von BamBuddy, in BamBuddys eigenen Vorlagen. Das geht auch im Spoolman-Modus. Dafür braucht es **BamBuddy 1.2.5.7 oder neuer**: Erst ab dieser Version liefert BamBuddy Etiketten als Bild.

1. Die Spulen-ID eingeben, also die # aus BamBuddy. Nach dem Anlegen einer Spule steht ihre ID schon da; bei mehreren Spulen kann man sie antippen.
2. Unter **Vorlage** ein Format wählen. Auf die 50 × 30-mm-Rolle passt **Box 40 × 30 mm**; es wird mittig gedruckt. Die anderen Vorlagen sind breiter als der 48-mm-Druckkopf.
3. Unter **Etikettenrolle und Zeilen** die Rolle (Standard 50 × 30 mm) und die Zeilen einstellen. Das Handy merkt sich beides.
4. **Vorschau** zeigt das Etikett Punkt für Punkt so, wie der Drucker es druckt.
5. Erst **🖨️ Drucken** schickt es los. Beim ersten Mal fragt Chrome, welcher Drucker es sein soll: den **PM240** wählen.

- **Android:** Chrome druckt direkt über Bluetooth (Web Bluetooth). Der Drucker darf dabei nicht gleichzeitig mit der Labelnize-App verbunden sein.
- **iPhone/iPad:** Safari kann kein Bluetooth. **Bild teilen** gibt das Etikett an die App des Druckers weiter, oder man speichert es in den Fotos.

## Mit der SpoolmanScale verbinden

In der Weboberfläche der Waage → Einstellungen → Barcode-Scanner → **filament_to_bambuddy-Adresse**: `http://<server-ip>:8088`. Scannt die Waage dann einen Code, den keine Spule kennt, fragt sie die App und bietet an, die Spule anzulegen, mit oder ohne den gefundenen Preis.

## Fehlersuche

| Symptom | Ursache und Abhilfe |
|---|---|
| `mkdir /root/.docker: read-only file system` | ZimaOS, siehe [oben](#zimaos-mkdir-rootdocker-read-only-file-system): `DOCKER_CONFIG=/DATA/.docker` setzen. |
| Container startet nicht, Log zeigt `ModuleNotFoundError` | Alter Stand oder noch Bind-Mounts auf einzelne Dateien. Mounts entfernen, `git pull`, neu bauen. |
| Statuszeile 🔴 | `BAMBUDDY_URL` falsch oder vom Container aus nicht erreichbar. `localhost` zeigt im Container auf den Container selbst; die IP oder den Hostnamen des Servers nehmen. |
| Statuszeile 🟡 | Dem API-Schlüssel fehlt **Read Status**. |
| Hinzufügen schlägt mit 403 fehl | Dem API-Schlüssel fehlt **Manage Inventory**. |
| Hinweis „Spoolman hat den Preis nicht übernommen“ | Die Spule ist angelegt, Spoolman hat den Preis abgelehnt. In Spoolman nachtragen. |
| Hinweis „Tag nicht verknüpft“ | Die Spule ist angelegt, der Tag hängt schon an einer anderen Spule oder ist ungültig. |
| EAN ohne Daten | UPCitemdb kennt sie nicht oder das Tageslimit ist erreicht. Titel einfügen oder Etikett fotografieren. |
