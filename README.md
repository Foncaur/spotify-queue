# 🎵 Kollaborative Spotify-Warteschlange

Eine Flask-Web-App für gemeinsame, faire Spotify-Playlists –
mit Login, Round-Robin-Rotation und Export-Funktion.

---

## Schnellstart (lokal, ohne Cloud)

```bash
# 1. Abhängigkeiten installieren
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 2. App starten (In-Memory-Modus – kein Firestore nötig)
python app.py
# → http://localhost:5000
```

Im In-Memory-Modus gehen Daten beim Neustart verloren – ideal zum Ausprobieren.

---

## Produktiv-Betrieb mit Google Cloud Firestore

### Firestore einrichten

```bash
# Google Cloud SDK installieren: https://cloud.google.com/sdk/docs/install

gcloud auth login
gcloud projects create meine-spotify-queue   # oder vorhandenes Projekt nutzen
gcloud config set project meine-spotify-queue

# Firestore in Native Mode aktivieren
gcloud firestore databases create --region=europe-west3

# Lokale Anmeldedaten setzen
gcloud auth application-default login
```

### Lokal mit Firestore starten

```bash
export SECRET_KEY="dein-geheimer-schlüssel"
python app.py
```

---

## Deployment auf Google Cloud Run

```bash
# Container bauen und deployen (ein Befehl)
gcloud run deploy spotify-queue \
  --source . \
  --region europe-west1 \
  --allow-unauthenticated \
  --set-env-vars SECRET_KEY="dein-geheimer-schlüssel"
```

Cloud Run vergibt automatisch eine HTTPS-URL.

---

## Deployment auf Google App Engine

```bash
# app.yaml anpassen (SECRET_KEY setzen), dann:
gcloud app deploy
```

---

## Features im Überblick

| Feature | Details |
|---|---|
| 🔐 Login / Registrierung | Passwörter gehasht (Werkzeug PBKDF2) |
| ➕ Song hinzufügen | Spotify-URL **oder** URI (`spotify:track:…`) |
| 🔄 Faire Rotation | Round-Robin: P1 → P2 → P3 → P1 → … |
| ✕ Song löschen | Nur eigene Songs löschbar |
| ⬇ Export (URIs) | Eine URI pro Zeile → in Spotlistr.com einfügen |
| ⬇ Export (JSON) | Maschinenlesbar, für Spotify Web API |
| ⬇ Export (Liste) | Lesbare Text-Datei mit Titeln |
| 🔗 JSON-API | `GET /api/queue` gibt aktuelle Reihenfolge zurück |

---

## Playlist in Spotify importieren

**Option A – Spotlistr (kein Code nötig)**
1. Export → „URIs (.txt)" herunterladen
2. [spotlistr.com](https://www.spotlistr.com) öffnen → „Spotify URI" wählen
3. Inhalt der Datei einfügen → Playlist erstellen

**Option B – Spotify Web API**
```bash
# 1. Access Token holen: https://developer.spotify.com/console/
# 2. Playlist erstellen
curl -X POST "https://api.spotify.com/v1/users/{user_id}/playlists" \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"name":"Kollaborative Queue","public":false}'

# 3. Songs hinzufügen (URIs aus /api/queue holen)
curl -X POST "https://api.spotify.com/v1/playlists/{playlist_id}/tracks" \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"uris":["spotify:track:4iV5W9uYEdYUVa79Axb7Rh","spotify:track:..."]}'
```

---

## Projektstruktur

```
spotify-queue/
├── app.py              # Flask-App (Routen, Logik, Datenzugriff)
├── requirements.txt
├── Dockerfile          # Cloud Run
├── app.yaml            # App Engine
└── templates/
    ├── base.html       # Layout + CSS
    ├── auth.html       # Login / Registrierung
    └── index.html      # Hauptseite (Warteschlange)
```
