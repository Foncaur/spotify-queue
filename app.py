"""
Collaborative Spotify Queue - Flask App
Uses Google Cloud Firestore for persistence.
Falls back to in-memory storage if Firestore is unavailable (dev mode).
"""

import os
import json
import re
import uuid
import base64
import requests as _req
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify, send_file
)
try:
    from google.cloud import firestore
    from google.oauth2 import service_account

    # Check, ob der Schlüssel als JSON-Text in Render existiert
    if "GOOGLE_APPLICATION_CREDENTIALS_JSON" in os.environ:
        info = json.loads(os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"])
        creds = service_account.Credentials.from_service_account_info(info)
        db = firestore.Client(credentials=creds, project=info.get("project_id"))
        USE_FIRESTORE = True
    else:
        # Lokaler Fallback
        db = firestore.Client()
        USE_FIRESTORE = True

    print("✅  Connected to Firestore")
except Exception as e:
    print(f"⚠️  Firestore not available ({e}). Using in-memory storage.")
    db = None
    USE_FIRESTORE = False

# ── In-memory fallback ────────────────────────────────────────────────────────
_mem: dict = {"users": {}, "songs": [], "queue_order": []}

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me-in-prod")


# ═══════════════════════════════════════════════════════════════════════════════
#  Helper – Spotify URI / URL parsing
# ═══════════════════════════════════════════════════════════════════════════════

def parse_spotify_input(raw: str) -> dict | None:
    """
    Accepts:
      - spotify:track:4iV5W9uYEdYUVa79Axb7Rh
      - https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh
      - https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh?si=...
    Returns {"uri": "spotify:track:<id>", "id": "<id>"} or None.
    """
    raw = raw.strip()
    # URI form
    m = re.match(r"^spotify:(track|album|playlist):([A-Za-z0-9]+)$", raw)
    if m:
        return {"uri": raw, "type": m.group(1), "id": m.group(2)}
    # URL form
    m = re.match(
        r"^https?://open\.spotify\.com/(track|album|playlist)/([A-Za-z0-9]+)",
        raw
    )
    if m:
        return {
            "uri": f"spotify:{m.group(1)}:{m.group(2)}",
            "type": m.group(1),
            "id": m.group(2),
        }
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Data Access – abstracted over Firestore / in-memory
# ═══════════════════════════════════════════════════════════════════════════════

def get_user(username: str) -> dict | None:
    if USE_FIRESTORE:
        doc = db.collection("users").document(username).get()
        return doc.to_dict() if doc.exists else None
    return _mem["users"].get(username)


def create_user(username: str, password: str) -> bool:
    if get_user(username):
        return False
    data = {
        "username": username,
        "password_hash": generate_password_hash(password),
        "created_at": datetime.utcnow().isoformat(),
    }
    if USE_FIRESTORE:
        db.collection("users").document(username).set(data)
    else:
        _mem["users"][username] = data
        _mem["queue_order"].append(username)
    return True


def get_all_users() -> list[str]:
    if USE_FIRESTORE:
        return [d.id for d in db.collection("users").stream()]
    return list(_mem["users"].keys())


def get_songs() -> list[dict]:
    """Return all songs ordered by their position in the fair queue."""
    if USE_FIRESTORE:
        docs = db.collection("songs").order_by("position").stream()
        return [d.to_dict() | {"doc_id": d.id} for d in docs]
    return list(_mem["songs"])


def add_song(username: str, title: str, uri: str, track_id: str) -> dict:
    """Add a song and assign a fair-queue position."""
    songs = get_songs()
    users = get_all_users()

    # Determine next position based on fair rotation
    position = _next_position(songs, username, users)

    song = {
        "id": str(uuid.uuid4()),
        "username": username,
        "title": title,
        "uri": uri,
        "track_id": track_id,
        "added_at": datetime.utcnow().isoformat(),
        "position": position,
    }
    if USE_FIRESTORE:
        db.collection("songs").document(song["id"]).set(song)
    else:
        song["doc_id"] = song["id"]
        _mem["songs"].append(song)
        _mem["songs"].sort(key=lambda s: s["position"])
    return song


def delete_song(song_id: str, username: str) -> bool:
    if USE_FIRESTORE:
        ref = db.collection("songs").document(song_id)
        doc = ref.get()
        if not doc.exists:
            return False
        if doc.to_dict().get("username") != username:
            return False
        ref.delete()
        return True
    for i, s in enumerate(_mem["songs"]):
        if s["id"] == song_id and s["username"] == username:
            _mem["songs"].pop(i)
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
#  Fair-rotation logic
# ═══════════════════════════════════════════════════════════════════════════════

def _next_position(songs: list[dict], username: str, users: list[str]) -> float:
    """
    Assign a fractional position so that the queue always interleaves
    contributions fairly (round-robin by user).
    Strategy:
      - We track the last position each user has in the queue.
      - The new song is inserted "after" the user's last song but
        "before" any earlier-registered users who haven't had their turn yet
        in the current round.
    """
    if not songs:
        # First song: position = index of user in user list
        return float(users.index(username)) if username in users else 0.0

    # Count per-user songs already in queue
    per_user: dict[str, list[float]] = {u: [] for u in users}
    for s in songs:
        if s["username"] in per_user:
            per_user[s["username"]].append(s["position"])

    my_positions = per_user.get(username, [])
    n_mine = len(my_positions)

    # Find which "round" this new song belongs to
    # Round = how many songs the user already has
    round_idx = n_mine  # 0-based

    # Position within the round = user's order in the user list
    user_idx = users.index(username) if username in users else len(users)
    n_users = len(users)

    new_pos = round_idx * n_users + user_idx
    return float(new_pos)


# ═══════════════════════════════════════════════════════════════════════════════
#  Routes
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    if "username" not in session:
        return redirect(url_for("login"))
    songs = get_songs()
    users = get_all_users()
    return render_template("index.html", songs=songs, users=users,
                           current_user=session["username"])


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        if not username or not password:
            flash("Benutzername und Passwort erforderlich.", "error")
        elif len(username) < 3:
            flash("Benutzername muss mindestens 3 Zeichen haben.", "error")
        elif not re.match(r"^[a-z0-9_]+$", username):
            flash("Nur Kleinbuchstaben, Zahlen und _ erlaubt.", "error")
        elif create_user(username, password):
            session["username"] = username
            flash(f"Willkommen, {username}! 🎉", "success")
            return redirect(url_for("index"))
        else:
            flash("Benutzername bereits vergeben.", "error")
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = get_user(username)
        if user and check_password_hash(user["password_hash"], password):
            session["username"] = username
            return redirect(url_for("index"))
        flash("Ungültige Anmeldedaten.", "error")
    return render_template("auth.html", mode="login")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/add", methods=["POST"])
def add():
    if "username" not in session:
        return redirect(url_for("login"))
    raw = request.form.get("spotify_input", "").strip()
    title = request.form.get("title", "").strip() or "Unbekannter Titel"
    parsed = parse_spotify_input(raw)
    if not parsed:
        flash("Ungültiger Spotify-Link oder URI.", "error")
        return redirect(url_for("index"))
    if parsed["type"] != "track":
        flash("Nur Track-Links werden unterstützt.", "error")
        return redirect(url_for("index"))
    add_song(session["username"], title, parsed["uri"], parsed["id"])
    flash(f'„{title}" wurde zur Warteschlange hinzugefügt! 🎵', "success")
    return redirect(url_for("index"))


@app.route("/delete/<song_id>", methods=["POST"])
def delete(song_id):
    if "username" not in session:
        return redirect(url_for("login"))
    if delete_song(song_id, session["username"]):
        flash("Song entfernt.", "success")
    else:
        flash("Nicht gefunden oder keine Berechtigung.", "error")
    return redirect(url_for("index"))


# ── Export ────────────────────────────────────────────────────────────────────

@app.route("/export/<fmt>")
def export(fmt):
    if "username" not in session:
        return redirect(url_for("login"))
    songs = get_songs()
    now = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    if fmt == "json":
        payload = json.dumps(
            {"exported_at": now, "songs": [
                {"position": i + 1, "title": s["title"],
                 "uri": s["uri"], "added_by": s["username"]}
                for i, s in enumerate(songs)
            ]}, indent=2, ensure_ascii=False
        )
        buf = BytesIO(payload.encode())
        return send_file(buf, mimetype="application/json",
                         as_attachment=True,
                         download_name=f"spotify_queue_{now}.json")

    elif fmt == "txt":
        lines = ["# Collaborative Spotify Queue", f"# Exported: {now}", ""]
        for i, s in enumerate(songs):
            lines.append(f"{i+1:>3}. [{s['username']}] {s['title']}")
            lines.append(f"     {s['uri']}")
        payload = "\n".join(lines)
        buf = BytesIO(payload.encode())
        return send_file(buf, mimetype="text/plain",
                         as_attachment=True,
                         download_name=f"spotify_queue_{now}.txt")

    elif fmt == "uris":
        # Plain list of URIs – can be pasted into Spotify playlist import tools
        lines = [s["uri"] for s in songs]
        buf = BytesIO("\n".join(lines).encode())
        return send_file(buf, mimetype="text/plain",
                         as_attachment=True,
                         download_name=f"spotify_uris_{now}.txt")

    flash("Unbekanntes Format.", "error")
    return redirect(url_for("index"))


# ── API (JSON) ────────────────────────────────────────────────────────────────

@app.route("/api/queue")
def api_queue():
    songs = get_songs()
    return jsonify([
        {"position": i + 1, "title": s["title"],
         "uri": s["uri"], "added_by": s["username"],
         "track_id": s["track_id"]}
        for i, s in enumerate(songs)
    ])


# ═══════════════════════════════════════════════════════════════════════════════
#  Spotify Search (Client Credentials Flow)
# ═══════════════════════════════════════════════════════════════════════════════

def _spotify_token() -> str | None:
    cid = os.environ.get("SPOTIFY_CLIENT_ID")
    sec = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not cid or not sec:
        return None
    creds = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    r = _req.post("https://accounts.spotify.com/api/token",
                  data={"grant_type": "client_credentials"},
                  headers={"Authorization": f"Basic {creds}"}, timeout=5)
    return r.json().get("access_token") if r.ok else None

@app.route("/search")
def search():
    if "username" not in session:
        return jsonify([]), 401
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    token = _spotify_token()
    if not token:
        return jsonify({"error": "Spotify-Keys nicht konfiguriert"}), 503
    r = _req.get("https://api.spotify.com/v1/search",
                 params={"q": q, "type": "track", "limit": 6},
                 headers={"Authorization": f"Bearer {token}"}, timeout=5)
    tracks = r.json().get("tracks", {}).get("items", [])
    return jsonify([{
        "uri":    t["uri"],
        "id":     t["id"],
        "title":  t["name"],
        "artist": ", ".join(a["name"] for a in t["artists"]),
        "cover":  t["album"]["images"][-1]["url"] if t["album"]["images"] else "",
    } for t in tracks])


if __name__ == "__main__":
    app.run(debug=True, port=5000)