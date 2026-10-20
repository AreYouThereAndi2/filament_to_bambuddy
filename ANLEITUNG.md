# filament_to_bambuddy: Installation und Update (deutsch)

Diese Anleitung gilt für den Fork [AreYouThereAndi2/filament_to_bambuddy](https://github.com/AreYouThereAndi2/filament_to_bambuddy). Sie geht von einem Heimserver mit Docker aus, zum Beispiel einem ZimaBoard mit CasaOS. Die englische [README](README.md) beschreibt alle Funktionen im Detail.

## Was die App macht

- Box-Barcode (EAN/UPC, Hersteller-Nummer) mit dem Handy scannen oder eintippen.
- Die App sucht das Produkt im eigenen Cache, dann im **TigerTag-Katalog** (rund 16 000 Filamente mit Hersteller-Artikelnummer, viele mit EAN), in der Open Filament Database und in SpoolmanDB-Community. Kennt keine der Datenbanken eine EAN, fragt sie UPCitemdb, das die Angebote von Amazon und anderen Shops sammelt. Von dort kommen Titel, Marke und der **Durchschnittspreis pro Rolle**.
- Formular prüfen, Preis übernehmen, auf Wunsch den NFC-Tag mit dem Handy lesen, dann **Hinzufügen**: Die Spule landet in BamBuddy, bei eingeschaltetem Spoolman-Modus in Spoolman.
- Die **SpoolmanScale** fragt dieselbe App, wenn ihr Barcode-Scanner einen unbekannten Code liest, und legt die Spule dann selbst an.

## Voraussetzungen

- Docker mit `docker compose` (bei CasaOS vorhanden) und `git`.
- BamBuddy erreichbar im Netz.
- Ein **BamBuddy-API-Schlüssel** (BamBuddy → Settings → API Keys) mit
  - **Manage Inventory** (legt Spulen an, Pflicht),
  - **Read Status** (Verbindungsanzeige, Inventar-Modus, Druckprofile und Druckermodelle),
  - **Benachrichtigungen senden** (nur für die Nachricht, wenn Filament knapp wird).

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
# TIGERTAG=off               # TigerTag-Katalog nicht verwenden
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
- **Auf NFC-Aufkleber schreiben** (im Datensatz einer Spule und gleich nach dem Anlegen): Das Handy schreibt den OpenSpool-Datensatz der Spule (Material, Farbe, Marke, Düsentemperaturen, Spulennummer) auf einen NTAG213/215-Aufkleber, so wie die SpoolmanScale ihn liest, und verknüpft die UID des Aufklebers gleich mit der Spule. Höchstens 230 Byte: Ein langer Markenname wird zuerst gekürzt, dann fallen die Temperaturen weg.
- **Der Aufkleber wird nie gesperrt.** Er bleibt beschreibbar und kann für die nächste Rolle wiederverwendet werden. Ein schon schreibgeschützter Aufkleber lässt sich nicht beschreiben; die Seite sagt das.

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
| NFC-Aufkleber beschreiben (OpenSpool) | ja (NTAG213/215) | nein, der Knopf erscheint nicht |
| Shop-Seite aus der Zwischenablage oder als Link übernehmen | ja | ja |
| Shop-Seite per **Teilen → Filament** übernehmen | ja, mit installierter App | nein: iOS bietet Web-Apps nicht im Teilen-Menü an |

## Daten von einer Shop-Seite übernehmen

Kennt keine Datenbank den Code (z. B. eine Artikelnummer wie `700-001-1535`), so geht es ohne Abtippen:

1. In der App den Code suchen, dann **„🔍 Im Web nach diesem Code suchen“** tippen. Die App merkt sich den Code für zwei Stunden.
2. In den Ergebnissen die Produktseite öffnen (Amazon, 3DJake, Herstellerseite …).
3. Auf der Produktseite oder in der Shop-App (auch Amazon) **Teilen → Kopieren**, zurück in die App und **„📋 Aus der Zwischenablage übernehmen“** tippen. Oder den Link bzw. den kopierten Text ins Feld „Produkttitel …“ einfügen und **Ausfüllen** tippen. Auch Links aus der Google-Suche (`google.com/url?…`) gehen. Die App übernimmt Name, Marke, Material, Farbe und Preis der Seite. Hat die Seite eine EAN, schlägt die App diese zusätzlich in den Datenbanken nach. Führt eine Seite mehrere Farben, nimmt die App die Farbe, deren Artikelnummer oder EAN zum gesuchten Code passt.
4. Prüfen, **Hinzufügen**. Die App merkt sich die Daten für die Artikelnummer und für die EAN der Seite: Beim nächsten Scan sind sie sofort da.

Aus dem Link liest der Server die Seite: Bei normalen Shops kommen Name, EAN und Preis. Amazon gibt Servern keine Seite heraus, dort zählt der mitkopierte Name aus „Teilen → Kopieren“.

**Android, noch einfacher:** Mit installierter App auf der Produktseite **Teilen → Filament** wählen, auch in der Amazon-App. Die App bekommt Titel und Link. Bei normalen Shops liest der Server die geteilte Seite einmal und holt EAN und Preis dazu. Amazon gibt Servern keine Seite heraus, dort reicht der Titel.

## Etiketten drucken (TYPONOS PM240)

Die Karte **🏷️ Etikett drucken** holt das Etikett einer Spule fertig von BamBuddy, in BamBuddys eigenen Vorlagen. Das geht auch im Spoolman-Modus. Dafür braucht es **BamBuddy 1.2.5.7 oder neuer**: Erst ab dieser Version liefert BamBuddy Etiketten als Bild.

1. Die Spulen-ID eingeben, also die # aus BamBuddy. Nach dem Anlegen einer Spule steht ihre ID schon da; bei mehreren Spulen kann man sie antippen.
2. Unter **Vorlage** ein Format wählen. **Eigenes Layout** (Standard) nutzt die ganze Rolle mit den Zeilen, die du eingeschaltet hast: Marke, Material, Farbe, Farbcode, Lagerort, Düsentemperatur, Gewicht, Artikelnummer, Druckprofil, Notiz (darin z. B. das Druckbett), Datum, Spulennummer und QR-Code. Die Vorlagen von BamBuddy gibt es weiter; **Box 40 × 30 mm** lässt aber weg, wofür kein Platz ist, deshalb fehlten dort Lagerort und Temperatur. Der QR-Code öffnet die Spule in BamBuddy unter der *External URL* aus BamBuddys Einstellungen (oder `BAMBUDDY_PUBLIC_URL`).
3. Unter **Etikettenrolle und Zeilen** die Rolle (Standard 50 × 30 mm) einstellen und das Etikett **selbst anordnen**: Jede Zeile ein- oder ausschalten, mit ▲ ▼ verschieben, Größe XS–XL, **B** für fett, **■** für weiß auf schwarzem Band. Dazu QR-Code rechts, links oder keiner (klein, mittel, groß) und wie die Zeilen die Höhe füllen (letzte Zeile unten, von oben, gleichmäßig verteilt). Ein Beispiel-Etikett über den Zeilen zeigt jede Änderung sofort, mit der zuletzt angezeigten Spule. Passt nicht alles auf die Rolle, wird alles gleichmäßig kleiner. **Zurück zur Standard-Anordnung** setzt alles zurück. Das Handy merkt sich die Anordnung.
4. **Vorschau** zeigt das Etikett Punkt für Punkt so, wie der Drucker es druckt.
5. Erst **🖨️ Drucken** schickt es los. Beim ersten Mal fragt Chrome, welcher Drucker es sein soll: den **PM240** wählen.

- **Android:** Chrome druckt direkt über Bluetooth (Web Bluetooth). Der Drucker darf dabei nicht gleichzeitig mit der Labelnize-App verbunden sein.
- **iPhone/iPad:** Safari kann kein Bluetooth. **Bild teilen** gibt das Etikett an die App des Druckers weiter, oder man speichert es in den Fotos.
## Was wurde erkannt? Etikett-Foto und eingefügte Titel

Nach **Etikett fotografieren** oder **Ausfüllen** (eingefügter Titel) erscheint im Formular **„🔍 So wurde der Text gelesen“**:

- **Erkannter Text**, farbig markiert: Jede Farbe zeigt, aus welcher Stelle ein Feld stammt.
- **Liste der Felder**: Wert und Herkunft, also ein Textstück, eine eigene Regel, die Datenbank (über einen Barcode im Text) oder der Standardwert.
- **Korrigieren:** Den Text im Feld darunter berichtigen (OCR-Fehler) und **Neu auswerten** tippen.
- **Selbst zuordnen:** Ein Stück Text markieren, das Feld wählen, den Wert prüfen und **Übernehmen** tippen.
- **Als Regel merken:** Mit diesem Haken gilt die Zuordnung künftig immer, wenn das Textstück vorkommt. Beispiel: „Marmor“ → Untertyp = Marble.
  - Regeln gelten für Etikett-Fotos, eingefügte Titel und Shop-Seiten. Kennt eine Datenbank den Barcode, haben deren Daten Vorrang.
  - Unter **Eigene Regeln** lassen sie sich ansehen und löschen. Gespeichert werden sie auf dem Server in `/data/parse_rules.json`.

## BamBuddy mit Spoolman: welches Inventar?

Ganz unten auf der Seite steht **BamBuddy-Inventar**:

- **Automatisch:** Die App fragt BamBuddy, ob Spoolman eingeschaltet ist, und zeigt darunter, was erkannt wurde.
- **Spoolman (über BamBuddy):** Neue Spulen und Etiketten gehen über BamBuddys Spoolman-Routen (`/api/v1/spoolman/inventory`).
- **BamBuddys eigenes Inventar:** `/api/v1/inventory`.

BamBuddy beantwortet die Frage nur einem API-Schlüssel mit **„Read Status“**. Fehlt das, steht dort in Rot „BamBuddy verweigert die Abfrage (HTTP 403)“. Dann entweder dem Schlüssel „Read Status“ geben oder hier **Spoolman** fest wählen. Die Wahl liegt in `/data/settings.json`; in der `.env` geht auch `INVENTORY_MODE=spoolman`.

Antwortet BamBuddy, gilt immer BamBuddys Einstellung, auch wenn hier etwas anderes fest gewählt ist: Eine Spule im anderen Inventar würde BamBuddy nicht anzeigen, und die Waage folgt ohnehin BamBuddy. Die Seite sagt dann in Rot, welches Inventar benutzt wird. Die feste Wahl gilt nur, wenn BamBuddy die Frage verweigert.

### Deine Filamente aus Spoolman

**Suchen:** Unter *Barcode scannen* steht im Spoolman-Modus „…oder in deinem Spoolman suchen“. Jedes getippte Wort schränkt die Liste weiter ein (Marke, Name, Material, Farbe, Artikelnummer). Ein Treffer füllt das Formular und verknüpft die Spule mit genau diesem Filament. Ein vorher gescannter Code bleibt dabei erhalten und wird beim Anlegen als Artikelnummer eingetragen.

**Auswahlfelder:** Material, Marke, Subtyp, Farbname, Nettogewicht und Lagerort sind Felder zum Tippen: Die Liste darunter zeigt nur noch Passendes. Was es noch nicht gibt, bietet sie als „„…“ anlegen“ an.


Im Spoolman-Modus schaut die App zuerst in **deine eigenen Spoolman-Filamente**: über die Artikelnummer des Filaments (oder eine EAN in einem Extrafeld), sonst nach jedem anderen Treffer über Marke, Material und Farbe. Passt eines, stehen seine Werte im Formular (Düse, Bett, Leergewicht der Rolle, Preis), darüber „Auf dein Spoolman-Filament: …“, und die Spule wird **genau auf diesem Filament** angelegt. Mit „nicht verknüpfen“ sucht oder erstellt BamBuddy das Filament wie bisher.

Nach dem Anlegen trägt die App ins Filament ein, was dort noch leer ist: Temperaturen, Leergewicht, Preis und den gescannten Code als Artikelnummer. Beim nächsten Scan desselben Codes findet sie es dann direkt.

Dafür muss die App Spoolman selbst erreichen. In der `.env` die Adresse eintragen, wie der Container sie sieht:

```
SPOOLMAN_URL=http://<server-ip>:7912
```

`192.168.1.5:7912` ohne `http://` und eine Adresse mit `/api/v1` am Ende gehen auch. Ohne `SPOOLMAN_URL` nimmt die App die Spoolman-Adresse aus BamBuddys Einstellungen (Schlüssel mit „Read Status“).

Ganz unten auf der Seite, unter **BamBuddy-Inventar**, steht der Status:

- 🟢 **Spoolman verbunden: 240 Filamente**, darunter die Adresse und woher sie kommt.
- 🟡 **Spoolman-Adresse fehlt:** `SPOOLMAN_URL` ist im Container nicht gesetzt. Steht sie in der `.env` neben der `docker-compose.yml`? Danach `docker compose up -d`, damit der Container sie bekommt.
- 🔴 **Spoolman unter … nicht erreichbar**, darunter der Grund: keine Antwort, Verbindung abgelehnt (vom Container aus ist `localhost` der Container selbst, also die IP des Servers nehmen), eine HTTP-Fehlernummer (Port oder Pfad falsch) oder keine Filamentliste (eine andere Adresse als Spoolman).

Solange Spoolman nicht direkt erreichbar ist, liest die App die Filamente über BamBuddy. Dann fehlen Düsen- und Betttemperatur, Preis und Artikelnummer, und in Spoolman wird nichts ergänzt. **Erneut prüfen** fragt sofort wieder, statt die zwei Minuten abzuwarten, die die Liste sonst gemerkt wird.

## Druckprofile (importierte Slicer-Profile)

Profile, die in BamBuddy importiert sind (**Settings → Profiles**, aus OrcaSlicer oder Bambu Studio, von jedem Hersteller), verknüpft die App mit den Spulen. BamBuddy schreibt das Profil der Spule (Temperaturen, Fluss, Pressure Advance) in den AMS-Slot, in den sie kommt.

- **Je Druckertyp ein Profil.** Importierte Profile gibt es pro Druckermodell („eSUN PLA+ @BBL X1C“, „… @BBL P1S“, auch „… 0.2 nozzle“). Die App liest deine Drucker aus BamBuddy und gibt der Spule für **jedes deiner Druckermodelle** dessen Profil derselben Linie. Bei mehreren Modellen landet das des häufigsten Modells an der Spule selbst, die übrigen als BamBuddys „Profil je Druckermodell“, ebenso Profile für andere Düsen. Profile, die du in BamBuddy selbst gesetzt hast, bleiben.
- **Im Formular** (Druck & Preis → **Druckprofil**) schlägt die App das passende Profil aus Hersteller, Material und Linie vor („Matte“, „Silk“, „PLA+“ statt „PLA“) und zeigt darunter, was jeder Drucker lädt. Antippen und tippen sucht in allen importierten Profilen; **Kein Profil** lässt BamBuddy das allgemeine nehmen.
- **In der Spulenakte** (QR-Code scannen) lässt sich das Profil ändern. Hat die Spule keins, steht der Vorschlag da und wird mit **Speichern** übernommen.
- **Für den ganzen Bestand:** **Dein Bestand → Druckprofile zuordnen → Prüfen** listet jede Spule ohne Profil mit dem passenden. Erst **N Spulen verknüpfen** schreibt es. Spulen, die schon ein importiertes Profil haben, bekommen dabei nur die Profile für fehlende Druckermodelle.

Profile und Drucker liest die App mit dem Recht **Read Status** des API-Schlüssels.

## Eine vorhandene Spule öffnen (QR-Code)

Den QR-Code auf dem Etikett einer Spule mit **Barcode scannen** lesen. Das geht mit BamBuddys Etiketten, den eigenen der App und Spoolmans `web+spoolman:s-…`. Statt einer Suche öffnet sich dann **Spule #12** mit Material, Marke, Farbe, Restgewicht, Artikelnummer und Datum. Dort lässt sich:

- **ein NFC-Tag nachträglich verknüpfen:** *Scannen* tippen und den Tag ans Handy halten (Chrome auf Android) oder die UID eintippen. Hängt der Tag schon an einer anderen Spule, sagt die App das.
- **einen NFC-Aufkleber beschreiben:** *Auf NFC-Aufkleber schreiben* tippen und den Aufkleber ans Handy halten. Er bekommt den OpenSpool-Datensatz und wird mit der Spule verknüpft (siehe *NFC am Handy*).
- **Lagerort, Notiz und Restgewicht ändern**, dann **Änderungen speichern**. Das Restgewicht ist netto, ohne Leerspule. Mit der Küchenwaage samt Spule gewogen? Den Wert unter **Gewogen (brutto)** eintragen, die App zieht die Leerspule ab.
- **das Druckprofil ändern** (siehe *Druckprofile*).
- Hängt an der Spule ein **Bambu-RFID** (vom AMS oder der Waage), steht das dabei. Hinter Spoolman hat eine Spule nur einen Tag: Ein anderer Tag würde den RFID ersetzen, daran erkennen AMS und Waage die Spule. Die App fragt vorher.
- **das Etikett neu drucken:** springt zur Etikett-Karte mit der Spulennummer.
- **die Spule in BamBuddy öffnen.**

Ohne Kamera geht es auch: den Link vom Etikett ins Code-Feld einfügen oder `#12` eintippen.

## Dein Bestand

Die Karte **Dein Bestand** liest deine Spulen aus dem aktiven Inventar (BamBuddy oder Spoolman dahinter):

- **Spule suchen:** Jedes Wort grenzt ein: Marke, Material, Farbe, Lagerort, Tag, Artikelnummer oder `#12`. `<200` zeigt nur Spulen mit weniger als 200 g. Ein Tipp öffnet die Spule.
- **Nachbestellen:** Spulen unter einer Grenze (200 g, änderbar), nach Sorte gruppiert, mit Restmenge, letztem Preis und *Nachkaufen* (Websuche).
- **Benachrichtigen, wenn eine Sorte knapp wird** (unten in *Nachbestellen*): Alle paar Stunden schaut die App in den Bestand. Liegen alle Spulen einer Sorte (Marke, Material, Linie, Farbe) zusammen unter der Grenze, kommt einmal eine Nachricht über die Kanäle von BamBuddy (Telegram, ntfy, E-Mail …). Erst wenn die Sorte wieder über der Grenze war, z. B. nach einer neuen Spule, meldet sie sich erneut. Dafür braucht es zwei Dinge in BamBuddy:
  1. Der API-Schlüssel braucht **Benachrichtigungen senden**.
  2. Bei mindestens einem Kanal (Einstellungen → Benachrichtigungen) **Nachrichten verbundener Apps** einschalten.

  Darunter steht, über welche Kanäle die Nachricht kommt. **Jetzt prüfen** schaut sofort nach und schickt, was knapp ist. Wie oft geprüft wird: `REORDER_CHECK_HOURS` (Standard 3).

Außerdem:

- **Tag ans Handy halten** (Chrome auf Android): Der Knopf in der Scan-Karte liest den NFC-Tag und öffnet die Spule, an der er hängt.
- **Schon im Bestand:** Beim Anlegen zeigt das Formular, ob es Spulen derselben Marke, desselben Materials und derselben Farbe schon gibt.
- **Mehrere Etiketten:** In *Spulen-ID* gehen `12, 15, 20-24`; dazu *Alle aus Lagerort …* und *Heute angelegt*. *Drucken* schickt alle nacheinander an den Drucker.
- **Leer: archivieren:** Im Spulen-Datensatz archiviert das eine aufgebrauchte Spule in BamBuddy. Sie verschwindet aus dem Bestand, ihre Daten bleiben erhalten.

## Mit der SpoolmanScale verbinden

In der Weboberfläche der Waage → Einstellungen → Barcode-Scanner → **filament_to_bambuddy-Adresse**: `http://<server-ip>:8088`. Scannt die Waage dann einen Code, den keine Spule kennt, fragt sie die App und bietet an, die Spule anzulegen, mit oder ohne den gefundenen Preis.

- **Angelegt wird die Spule von der App**, genau wie vom Handy: Sie kommt auf dein Spoolman-Filament und bekommt die Druckprofile für jedes Druckermodell. Lagerort und Preis kommen aus dem Speicher, und der Code wird gemerkt. Ist die App nicht erreichbar, legt die Waage die Spule selbst über BamBuddy an.
- **Etiketten von der Waage** kommen im selben Layout, das du am Handy unter *Etikettenrolle und Zeilen* angeordnet hast. Die App merkt sich die Anordnung (`/data/label_layout.json`) und zeichnet das Etikett für die Waage. Ein neues Handy übernimmt sie von dort.

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
