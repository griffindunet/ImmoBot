#!/usr/bin/env python3
import json, sqlite3, hashlib, re, threading, os
import requests
from flask import Flask, render_template_string, request, redirect, jsonify
import lbc as lbclib

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    "search": {
        "villes": ["Cannes", "Antibes"],
        "prix_min": 100000,
        "prix_max": 190000,
        "surface_min": 30,
        "nb_pieces_min": 2,
        "mots_exclus": ["viager", "cave uniquement", "vendu loué", "vendu loue"]
    },
    "intervalle_minutes": 30,
    "db_path": os.path.join(BASE_DIR, "annonces.db")
}

def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except:
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

# ─── DATABASE ─────────────────────────────────────────────────────────────────
def get_conn(path):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS annonces (
            id TEXT PRIMARY KEY,
            source TEXT,
            titre TEXT,
            prix INTEGER,
            surface REAL,
            pieces INTEGER,
            ville TEXT,
            url TEXT,
            description TEXT,
            image TEXT,
            favori INTEGER DEFAULT 0,
            date_ajout TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn

def annonce_existe(conn, aid):
    return conn.execute("SELECT 1 FROM annonces WHERE id=?", (aid,)).fetchone()

def sauvegarder(conn, a):
    conn.execute("""
        INSERT OR IGNORE INTO annonces
        (id,source,titre,prix,surface,pieces,ville,url,description,image)
        VALUES (:id,:source,:titre,:prix,:surface,:pieces,:ville,:url,:description,:image)
    """, a)
    conn.commit()

def get_annonces(conn, f={}):
    q = "SELECT * FROM annonces WHERE 1=1"
    p = []
    if f.get("prix_min"):    q += " AND prix >= ?";    p.append(f["prix_min"])
    if f.get("prix_max"):    q += " AND prix <= ?";    p.append(f["prix_max"])
    if f.get("surface_min"): q += " AND surface >= ?"; p.append(f["surface_min"])
    if f.get("favori"):      q += " AND favori = 1"
    if f.get("q"):
        q += " AND (titre LIKE ? OR description LIKE ?)";
        p += [f"%{f['q']}%", f"%{f['q']}%"]
    q += " ORDER BY date_ajout DESC"
    return conn.execute(q, p).fetchall()

# ─── SCRAPER ──────────────────────────────────────────────────────────────────
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}

def filtrer(a, cfg, ad_attributes=None):
    s = cfg["search"]
    
    # Filtres de prix / surface basiques
    if a["prix"]:
        if s.get("prix_min") and a["prix"] < s["prix_min"]: return False
        if s.get("prix_max") and a["prix"] > s["prix_max"]: return False
    if a["surface"] and s.get("surface_min") and a["surface"] < s["surface_min"]: return False
    
    texte = (a["titre"] + " " + a["description"]).lower()
    
    # Exclusion mots clés (vendu loué, viager, etc)
    for mot in s.get("mots_exclus", []):
        if mot.lower() in texte: return False
        
    # Parking obligatoire (titre, description ou caractéristiques)
    mots_parking = ["parking", "garage", "box", "stationnement", "place de parking"]
    parking_trouve = any(m in texte for m in mots_parking)
    
    # Vérification dans les caractéristiques brutes de LBC (si existant)
    if not parking_trouve and ad_attributes:
        attr_text = str(ad_attributes).lower()
        parking_trouve = any(m in attr_text for m in mots_parking)
        
    if not parking_trouve:
        return False

    return True

def get_coords(ville):
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": ville, "format": "json", "limit": 1},
            headers={"User-Agent": "ImmoBot/1.0"},
            timeout=10
        )
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        print(f"[Geocode] Erreur pour {ville}: {e}")
    return None, None

# ─── LEBONCOIN ────────────────────────────────────────────────────────────────
def run_scrape(cfg, conn):
    s = cfg["search"]
    client = lbclib.Client()
    total_ajoutees = 0

    for ville in s.get("villes", []):
        ville = ville.strip()
        if not ville: continue

        lat, lng = get_coords(ville)
        if not lat:
            print(f"[LBC] Impossible de géocoder '{ville}'")
            continue
            
        location = lbclib.City(lat=lat, lng=lng, radius=10000, city=ville)
        
        try:
            result = client.search(
                locations=[location],
                category=lbclib.Category.IMMOBILIER,
                ad_type=lbclib.AdType.OFFER,
                real_estate_type=["2"], # Appartement
                price=[s.get("prix_min", 0), s.get("prix_max", 9999999)],
                square=[s.get("surface_min", 0), 500],
                rooms=[s.get("nb_pieces_min", 1), 10],
                sort=lbclib.Sort.NEWEST,
                limit=50,
            )
            n = 0
            for ad in result.ads:
                uid = hashlib.md5(ad.url.encode()).hexdigest()
                if annonce_existe(conn, uid): continue
                annonce = {
                    "id":          uid,
                    "source":      "leboncoin",
                    "titre":       ad.subject or "",
                    "prix":        int(ad.price) if ad.price else None,
                    "surface":     None,
                    "pieces":      None,
                    "ville":       ad.location.city if (ad.location and ad.location.city) else ville,
                    "url":         ad.url,
                    "description": ad.body or "",
                    "image":       ad.images[0] if ad.images else ""
                }
                
                # Passage des attributs LBC pour valider la présence du parking
                if filtrer(annonce, cfg, getattr(ad, 'attributes', [])):
                    sauvegarder(conn, annonce)
                    n += 1
                    
            print(f"[LBC] {ville} : {n} annonce(s)")
            total_ajoutees += n
        except Exception as e:
            print(f"[LBC] Erreur sur {ville} : {e}")
            
    return total_ajoutees

# ─── SCHEDULER ────────────────────────────────────────────────────────────────
stop_event = threading.Event()

def scheduler_loop(conn):
    while not stop_event.is_set():
        cfg = load_config()
        run_scrape(cfg, conn)
        stop_event.wait(cfg.get("intervalle_minutes", 30) * 60)

# ─── TEMPLATE ─────────────────────────────────────────────────────────────────
TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>🏠 ImmoBot LBC</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<style>
  :root { --bg:#f0f2f5; --card:#ffffff; --border:#dee2e6; --accent:#ff6e14; --text:#212529; --muted:#6c757d; }
  body { background:var(--bg); color:var(--text); font-family:'Segoe UI',sans-serif; }
  .navbar { background:#fff !important; border-bottom:2px solid var(--border); box-shadow:0 2px 8px rgba(0,0,0,.07); }
  .navbar-brand { color:var(--accent) !important; font-weight:700; font-size:1.3rem; }
  .nav-link { color:var(--text) !important; font-weight:500; }
  .nav-link.active { color:var(--accent) !important; border-bottom:2px solid var(--accent); }
  .card { background:var(--card); border:1px solid var(--border); border-radius:14px; transition:.2s; box-shadow:0 2px 8px rgba(0,0,0,.05); }
  .card:hover { border-color:var(--accent); transform:translateY(-3px); box-shadow:0 8px 24px rgba(255,110,20,.12); }
  .form-control,.form-select { background:#fff; color:var(--text); border-color:var(--border); }
  .form-control:focus,.form-select:focus { border-color:var(--accent); box-shadow:0 0 0 3px rgba(255,110,20,.1); }
  .btn-primary { background:var(--accent); border:none; }
  .btn-primary:hover { background:#e85e0c; }
  .price { font-size:1.2rem; font-weight:700; color:var(--accent); }
  .card-img-top { height:160px; object-fit:cover; border-radius:14px 14px 0 0; }
  .badge-leboncoin { background:#ff6e14 !important; }
  .stat-box { background:#fff; border:1px solid var(--border); border-radius:12px; padding:12px 20px; font-weight:500; box-shadow:0 2px 6px rgba(0,0,0,.04); }
  .filter-bar { background:#fff; border:1px solid var(--border); border-radius:14px; padding:16px 20px; }
  label.form-label { font-size:.78rem; font-weight:600; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
  .card-footer { background:#f8f9fa; border-top:1px solid var(--border); border-radius:0 0 14px 14px; font-size:.7rem; color:var(--muted); }
  #toast-container { position:fixed; bottom:24px; right:24px; z-index:9999; }
</style>
</head>
<body>
<nav class="navbar navbar-light px-4 py-2">
  <a class="navbar-brand" href="/"><i class="fa fa-house-chimney me-2"></i>ImmoBot</a>
  <div class="d-flex gap-3">
    <a href="/" class="nav-link {% if page=='index' %}active{% endif %}"><i class="fa fa-list me-1"></i>Annonces</a>
    <a href="/settings" class="nav-link {% if page=='settings' %}active{% endif %}"><i class="fa fa-gear me-1"></i>Config</a>
  </div>
</nav>
<div class="container-fluid py-4 px-4">

{% if page == "index" %}
<div class="d-flex gap-3 mb-4 flex-wrap align-items-center">
  <div class="stat-box"><i class="fa fa-list me-2 text-primary"></i><strong>{{ annonces|length }}</strong> résultats</div>
  <div class="stat-box"><i class="fa fa-location-dot me-2 text-danger"></i>{{ config.search.villes|join(', ') }}</div>
  <div class="stat-box"><i class="fa fa-euro-sign me-2 text-success"></i>{{ config.search.prix_min|int }} – {{ config.search.prix_max|int }} €</div>
  <div class="ms-auto d-flex gap-2">
    <button class="btn btn-success btn-sm px-3" onclick="scrapeNow(this)"><i class="fa fa-rotate me-1"></i>Scraper maintenant</button>
    <form method="POST" action="/vider" onsubmit="return confirm('Vider toutes les annonces ?')">
      <button class="btn btn-outline-danger btn-sm px-3"><i class="fa fa-trash me-1"></i>Tout vider</button>
    </form>
  </div>
</div>

<div class="filter-bar mb-4">
  <form method="GET" class="row g-2 align-items-end">
    <div class="col-md-3">
      <label class="form-label">Recherche</label>
      <input type="text" name="q" class="form-control form-control-sm" placeholder="mot clé..." value="{{ filtres.q or '' }}">
    </div>
    <div class="col-md-2">
      <label class="form-label">Prix min</label>
      <input type="number" name="prix_min" class="form-control form-control-sm" placeholder="€" value="{{ filtres.prix_min or '' }}">
    </div>
    <div class="col-md-2">
      <label class="form-label">Prix max</label>
      <input type="number" name="prix_max" class="form-control form-control-sm" placeholder="€" value="{{ filtres.prix_max or '' }}">
    </div>
    <div class="col-md-2">
      <label class="form-label">Surface min</label>
      <input type="number" name="surface_min" class="form-control form-control-sm" placeholder="m²" value="{{ filtres.surface_min or '' }}">
    </div>
    <div class="col-auto">
      <div class="form-check mt-4">
        <input class="form-check-input" type="checkbox" name="favori" value="1" id="chk_fav" {% if filtres.favori %}checked{% endif %}>
        <label class="form-check-label small fw-semibold" for="chk_fav">⭐ Favoris</label>
      </div>
    </div>
    <div class="col-auto mt-3 d-flex gap-2">
      <button type="submit" class="btn btn-primary btn-sm px-3">Filtrer</button>
      <a href="/" class="btn btn-outline-secondary btn-sm">Reset</a>
    </div>
  </form>
</div>

<div class="row g-3">
{% for a in annonces %}
<div class="col-xl-3 col-lg-4 col-md-6" id="card-{{ a['id'] }}">
  <div class="card h-100">
    {% if a['image'] %}<img src="{{ a['image'] }}" class="card-img-top" onerror="this.style.display='none'">{% endif %}
    <div class="card-body d-flex flex-column">
      <div class="d-flex justify-content-between align-items-center mb-2">
        <span class="badge badge-leboncoin text-white" style="font-size:.7rem">LEBONCOIN</span>
        <button class="btn btn-sm p-0 border-0 bg-transparent fav-btn" data-id="{{ a['id'] }}"
          style="font-size:1.15rem;color:{{ '#f5a623' if a['favori'] else '#ccc' }}">⭐</button>
      </div>
      <h6 class="card-title text-dark" style="font-size:.85rem;line-height:1.4">
        {{ a['titre'][:70] }}{% if a['titre']|length > 70 %}...{% endif %}
      </h6>
      <p class="price mb-1">
        {% if a['prix'] %}{{ "{:,}".format(a['prix']).replace(",", " ") }} €
        {% else %}<span class="text-muted">Prix N/A</span>{% endif %}
      </p>
      <p class="text-muted small mb-0"><i class="fa fa-location-dot me-1 text-danger"></i>{{ a['ville'] }}</p>
      {% if a['description'] %}<p class="text-muted mt-2" style="font-size:.75rem">{{ a['description'][:100] }}...</p>{% endif %}
      <div class="mt-auto pt-3 d-flex gap-2">
        <a href="{{ a['url'] }}" target="_blank" class="btn btn-primary btn-sm flex-grow-1">
          <i class="fa fa-arrow-up-right-from-square me-1"></i>Voir l'annonce
        </a>
        <button class="btn btn-outline-danger btn-sm del-btn" data-id="{{ a['id'] }}"><i class="fa fa-trash"></i></button>
      </div>
    </div>
    <div class="card-footer"><i class="fa fa-clock me-1"></i>{{ a['date_ajout'] }}</div>
  </div>
</div>
{% else %}
<div class="col-12 text-center text-muted py-5">
  <i class="fa fa-house-chimney fa-4x mb-3 d-block" style="opacity:.2"></i>
  <p class="fw-semibold mb-3">Aucune annonce trouvée avec ce filtre de parking obligatoire.</p>
  <button class="btn btn-primary px-4" onclick="scrapeNow(this)"><i class="fa fa-rotate me-1"></i>Scraper maintenant</button>
</div>
{% endfor %}
</div>

{% elif page == "settings" %}
<div class="row justify-content-center">
<div class="col-md-6">
  {% if saved %}<div class="alert alert-success border-0">✅ Configuration sauvegardée !</div>{% endif %}
  <div class="card p-4">
    <h5 class="mb-4 text-dark"><i class="fa fa-sliders me-2 text-primary"></i>Configuration</h5>
    <form method="POST">
      <div class="mb-3">
        <label class="form-label">Villes (séparées par des virgules)</label>
        <input type="text" name="villes" class="form-control" value="{{ config.search.villes|join(', ') }}" required>
      </div>
      <div class="row g-3 mb-3">
        <div class="col-6">
          <label class="form-label">Prix min (€)</label>
          <input type="number" name="prix_min" class="form-control" value="{{ config.search.prix_min }}">
        </div>
        <div class="col-6">
          <label class="form-label">Prix max (€)</label>
          <input type="number" name="prix_max" class="form-control" value="{{ config.search.prix_max }}">
        </div>
      </div>
      <div class="row g-3 mb-3">
        <div class="col-6">
          <label class="form-label">Surface min (m²)</label>
          <input type="number" name="surface_min" class="form-control" value="{{ config.search.surface_min }}">
        </div>
        <div class="col-6">
          <label class="form-label">Pièces min</label>
          <input type="number" name="nb_pieces_min" class="form-control" value="{{ config.search.nb_pieces_min }}">
        </div>
      </div>
      <div class="mb-3">
        <label class="form-label">Mots exclus (séparés par virgule)</label>
        <input type="text" name="mots_exclus" class="form-control" value="{{ config.search.mots_exclus|join(', ') }}">
      </div>
      <div class="mb-4">
        <label class="form-label">⏱ Intervalle scraping (minutes)</label>
        <input type="number" name="intervalle" class="form-control" value="{{ config.intervalle_minutes }}" min="5">
      </div>
      
      <div class="alert alert-info py-2" style="font-size:0.85rem">
        <i class="fa fa-info-circle me-1"></i> <strong>Filtre Parking :</strong> Actif par défaut. Seules les annonces contenant "parking", "garage", "box" ou "stationnement" seront enregistrées.
      </div>
      
      <button type="submit" class="btn btn-primary w-100 py-2 fw-semibold">
        <i class="fa fa-floppy-disk me-2"></i>Sauvegarder
      </button>
    </form>
  </div>
</div>
</div>
{% endif %}
</div>
<div id="toast-container"></div>
<script>
function toast(msg, type="success") {
  const t = document.createElement("div");
  t.className = `alert alert-${type} shadow-sm py-2 px-3 mb-2`;
  t.style.cssText = "min-width:240px;border-radius:10px";
  t.innerText = msg;
  document.getElementById("toast-container").appendChild(t);
  setTimeout(() => t.remove(), 3000);
}
function scrapeNow(btn) {
  btn.disabled = true;
  btn.innerHTML = '<i class="fa fa-rotate fa-spin me-1"></i>En cours...';
  fetch("/scrape_now").then(r => r.json()).then(d => {
    toast(`✅ ${d.nouvelles} nouvelle(s) annonce(s) !`);
    if (d.nouvelles > 0) setTimeout(() => location.reload(), 1500);
    else { btn.disabled = false; btn.innerHTML = '<i class="fa fa-rotate me-1"></i>Scraper maintenant'; }
  }).catch(() => { btn.disabled = false; toast("Erreur réseau", "danger"); });
}
document.querySelectorAll(".fav-btn").forEach(btn => {
  btn.addEventListener("click", function() {
    fetch(`/favori/${this.dataset.id}`, {method:"POST"}).then(() => {
      const y = this.style.color === "rgb(245, 166, 35)";
      this.style.color = y ? "#ccc" : "#f5a623";
      toast(y ? "Retiré des favoris" : "⭐ Ajouté aux favoris");
    });
  });
});
document.querySelectorAll(".del-btn").forEach(btn => {
  btn.addEventListener("click", function() {
    if (!confirm("Supprimer cette annonce ?")) return;
    fetch(`/supprimer/${this.dataset.id}`, {method:"POST"}).then(() => {
      document.getElementById(`card-${this.dataset.id}`).remove();
      toast("🗑️ Annonce supprimée", "warning");
    });
  });
});
</script>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>"""

# ─── FLASK ────────────────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def index():
    cfg = load_config()
    filtres = {
        "prix_min":    request.args.get("prix_min", type=int),
        "prix_max":    request.args.get("prix_max", type=int),
        "surface_min": request.args.get("surface_min", type=int),
        "favori":      request.args.get("favori"),
        "q":           request.args.get("q"),
    }
    annonces = get_annonces(CONN, filtres)
    return render_template_string(TEMPLATE, annonces=annonces, filtres=filtres, config=cfg, page="index")

@app.route("/scrape_now")
def scrape_now():
    cfg = load_config()
    n = run_scrape(cfg, CONN)
    return jsonify({"nouvelles": n})

@app.route("/favori/<aid>", methods=["POST"])
def toggle_favori(aid):
    CONN.execute("UPDATE annonces SET favori = 1 - favori WHERE id=?", (aid,))
    CONN.commit()
    return jsonify({"ok": True})

@app.route("/supprimer/<aid>", methods=["POST"])
def supprimer(aid):
    CONN.execute("DELETE FROM annonces WHERE id=?", (aid,))
    CONN.commit()
    return ("", 204)

@app.route("/vider", methods=["POST"])
def vider():
    CONN.execute("DELETE FROM annonces")
    CONN.commit()
    return redirect("/")

@app.route("/settings", methods=["GET", "POST"])
def settings():
    cfg = load_config()
    if request.method == "POST":
        cfg["search"]["villes"]             = [v.strip() for v in request.form["villes"].split(",") if v.strip()]
        cfg["search"]["prix_min"]           = int(request.form["prix_min"])
        cfg["search"]["prix_max"]           = int(request.form["prix_max"])
        cfg["search"]["surface_min"]        = int(request.form["surface_min"])
        cfg["search"]["nb_pieces_min"]      = int(request.form["nb_pieces_min"])
        cfg["search"]["mots_exclus"]        = [m.strip() for m in request.form["mots_exclus"].split(",") if m.strip()]
        cfg["intervalle_minutes"]           = int(request.form["intervalle"])
        save_config(cfg)
        stop_event.set()
        stop_event.clear()
        threading.Thread(target=scheduler_loop, args=(CONN,), daemon=True).start()
        return redirect("/settings?saved=1")
    saved = request.args.get("saved")
    return render_template_string(TEMPLATE, config=cfg, page="settings", saved=saved)

@app.route("/api/annonces")
def api_annonces():
    return jsonify([dict(a) for a in get_annonces(CONN)])

# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cfg  = load_config()
    CONN = get_conn(cfg["db_path"])
    threading.Thread(target=scheduler_loop, args=(CONN,), daemon=True).start()
    print("🏠 ImmoBot LBC → http://localhost:5000")
    app.run(debug=False, host="0.0.0.0", port=5000)
