import os
import json
import uuid
import hashlib
import threading
from datetime import datetime
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, Response, jsonify, send_file)
from waitress import serve

app = Flask(__name__)
app.secret_key = "game-store-super-secret-key-2026"

# ============================================================
#  CONFIGURACAO PARA ARQUIVOS MUITO GRANDES (36 GB+)
# ============================================================
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024 * 1024  # 100 GB
app.config["MAX_FORM_MEMORY_SIZE"] = 100 * 1024 * 1024

CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB

# Paths
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
GAMES_DIR = os.path.join(UPLOAD_DIR, "games")
COVERS_DIR = os.path.join(UPLOAD_DIR, "covers")
TEMP_DIR = os.path.join(UPLOAD_DIR, "temp")  # uploads parciais
DATA_FILE = os.path.join(os.path.dirname(__file__), "games.json")
ADMIN_PASSWORD = "admin123"

# Categorias disponiveis
CATEGORIES = [
    "Acao", "Aventura", "Tiro", "Carros", "Futebol",
    "RPG", "Terror", "Esportes", "Puzzle", "Luta",
    "Estrategia", "Plataforma", "Simulacao", "Corrida",
    "Mod", "Multiplayer", "Offline", "Online",
    "Open World", "Sobrevivencia", "Fantasia", "Anime",
    "Pixel Art", "Indie", "Classicos", "Outros",
]

ALLOWED_ZIP = {".zip", ".rar", ".7z"}
ALLOWED_IMG = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

for d in [GAMES_DIR, COVERS_DIR, TEMP_DIR]:
    os.makedirs(d, exist_ok=True)


# ---------- helpers ----------
def load_games():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    return []


def save_games(games):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(games, f, ensure_ascii=False, indent=2)


def allowed_file(filename, allowed):
    return os.path.splitext(filename)[1].lower() in allowed


def is_admin():
    return session.get("admin", False)


def human_size(n):
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ============================================================
#  UPLOAD EM BLOCOS (CHUNKED UPLOAD) - RESUMABLE
#  Se a conexao cair, continua de onde parou!
# ============================================================
UPLOAD_CHUNKS = {}  # { upload_id: { received, total, filename, title, desc, cover } }
UPLOAD_LOCK = threading.Lock()

# ============================================================
#  DOWNLOADS ATIVOS - watchdog nao reinicia com download rolando
# ============================================================
ACTIVE_DOWNLOADS = 0
DOWNLOADS_LOCK = threading.Lock()


def get_active_downloads():
    with DOWNLOADS_LOCK:
        return ACTIVE_DOWNLOADS


@app.route("/admin/upload/check", methods=["POST"])
def upload_check():
    """Ve se ja existe upload parcial (retomada)."""
    if not is_admin():
        return jsonify({"error": "unauthorized"}), 401

    upload_id = request.json.get("upload_id", "")
    with UPLOAD_LOCK:
        info = UPLOAD_CHUNKS.get(upload_id)
        if info:
            return jsonify({"received": info["received"], "total": info["total"]})
        # Verifica arquivo parcial no disco
        temp_file = os.path.join(TEMP_DIR, f"{upload_id}.part")
        if os.path.exists(temp_file):
            received = os.path.getsize(temp_file)
            return jsonify({"received": received, "total": info.get("total", 0) if info else 0})
        return jsonify({"received": 0, "total": 0})


@app.route("/admin/upload/chunk", methods=["POST"])
def upload_chunk():
    """Recebe um bloco do arquivo (retomavel)."""
    if not is_admin():
        return jsonify({"error": "unauthorized"}), 401

    upload_id = request.form.get("upload_id", "")
    if not upload_id:
        return jsonify({"error": "no upload_id"}), 400

    chunk_file = request.files.get("chunk")
    offset = int(request.form.get("offset", 0))
    total_size = int(request.form.get("total_size", 0))
    filename = request.form.get("filename", "")
    is_last = request.form.get("is_last") == "1"

    temp_file = os.path.join(TEMP_DIR, f"{upload_id}.part")

    # Escreve o bloco no offset certo
    mode = "r+b" if os.path.exists(temp_file) else "wb"
    with open(temp_file, mode) as f:
        f.seek(offset)
        while True:
            data = chunk_file.stream.read(CHUNK_SIZE)
            if not data:
                break
            f.write(data)

    received = os.path.getsize(temp_file)

    with UPLOAD_LOCK:
        if upload_id not in UPLOAD_CHUNKS:
            UPLOAD_CHUNKS[upload_id] = {
                "filename": filename,
                "title": request.form.get("title", ""),
                "description": request.form.get("description", ""),
                "cover": None,
                "received": 0,
                "total": total_size,
            }
        UPLOAD_CHUNKS[upload_id]["received"] = received
        UPLOAD_CHUNKS[upload_id]["total"] = total_size

    return jsonify({"received": received, "total": total_size,
                    "percent": round(received * 100 / total_size, 1) if total_size else 0})


@app.route("/admin/upload/finish", methods=["POST"])
def upload_finish():
    """Finaliza o upload - move arquivo temporario e registra."""
    if not is_admin():
        return jsonify({"error": "unauthorized"}), 401

    upload_id = request.form.get("upload_id", "")
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    category = request.form.get("category", "").strip()
    cover_file = request.files.get("cover_file")

    temp_file = os.path.join(TEMP_DIR, f"{upload_id}.part")
    if not os.path.exists(temp_file):
        return jsonify({"error": "upload nao encontrado"}), 404

    # Determina extensao do arquivo original
    orig_name = ""
    with UPLOAD_LOCK:
        info = UPLOAD_CHUNKS.get(upload_id, {})
        orig_name = info.get("filename", "game.zip")
    ext = os.path.splitext(orig_name)[1].lower() or ".zip"
    if ext not in ALLOWED_ZIP:
        ext = ".zip"

    # Move pra pasta final
    game_id = uuid.uuid4().hex[:12]
    game_filename = f"{game_id}{ext}"
    final_path = os.path.join(GAMES_DIR, game_filename)
    os.rename(temp_file, final_path)
    size = os.path.getsize(final_path)

    # Salva capa
    cover_filename = "default.png"
    if cover_file and cover_file.filename:
        if allowed_file(cover_file.filename, ALLOWED_IMG):
            cext = os.path.splitext(cover_file.filename)[1].lower()
            cover_filename = f"{game_id}{cext}"
            cover_file.save(os.path.join(COVERS_DIR, cover_filename))

    games = load_games()
    games.append({
        "id": game_id,
        "title": title or os.path.splitext(orig_name)[0],
        "description": description,
        "category": category,
        "file": game_filename,
        "cover": cover_filename,
        "size": size,
        "downloads": 0,
        "created_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
    })
    save_games(games)

    with UPLOAD_LOCK:
        UPLOAD_CHUNKS.pop(upload_id, None)

    return jsonify({"ok": True, "id": game_id, "title": title, "size": size})


# ============================================================
#  UPLOAD ANTIGO (FORMULARIO NORMAL) - continua funcionando
# ============================================================
@app.route("/admin/upload", methods=["POST"])
def upload_game():
    if not is_admin():
        return redirect(url_for("login"))

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    category = request.form.get("category", "").strip()
    zip_file = request.files.get("game_file")
    cover_file = request.files.get("cover_file")

    if not title:
        flash("O titulo e obrigatorio!", "error")
        return redirect(url_for("admin"))
    if not zip_file or not zip_file.filename:
        flash("Selecione o arquivo do jogo!", "error")
        return redirect(url_for("admin"))
    if not allowed_file(zip_file.filename, ALLOWED_ZIP):
        flash("Formato invalido! Use .zip, .rar ou .7z", "error")
        return redirect(url_for("admin"))

    game_id = uuid.uuid4().hex[:12]
    ext = os.path.splitext(zip_file.filename)[1].lower()
    game_filename = f"{game_id}{ext}"
    game_path = os.path.join(GAMES_DIR, game_filename)

    # Salva em blocos
    with open(game_path, "wb") as f:
        while True:
            chunk = zip_file.stream.read(CHUNK_SIZE)
            if not chunk:
                break
            f.write(chunk)
    size = os.path.getsize(game_path)

    cover_filename = "default.png"
    if cover_file and cover_file.filename:
        if allowed_file(cover_file.filename, ALLOWED_IMG):
            cext = os.path.splitext(cover_file.filename)[1].lower()
            cover_filename = f"{game_id}{cext}"
            cover_file.save(os.path.join(COVERS_DIR, cover_filename))

    games = load_games()
    games.append({
        "id": game_id, "title": title, "description": description,
        "category": category,
        "file": game_filename, "cover": cover_filename,
        "size": size, "downloads": 0,
        "created_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
    })
    save_games(games)

    flash(f"Jogo '{title}' adicionado com sucesso!", "success")
    return redirect(url_for("admin"))


# ============================================================
#  ROTAS EXISTENTES
# ============================================================
@app.route("/")
def index():
    games = load_games()
    query = request.args.get("q", "").strip().lower()
    cat = request.args.get("cat", "").strip()

    if query:
        games = [g for g in games
                 if query in g["title"].lower() or query in g["description"].lower()
                 or query in g.get("category", "").lower()]
    if cat:
        games = [g for g in games if g.get("category", "") == cat]

    # Conta jogos por categoria
    cat_counts = {}
    for g in load_games():
        c = g.get("category", "")
        if c:
            cat_counts[c] = cat_counts.get(c, 0) + 1

    return render_template("index.html", games=games, query=query,
                           cat=cat, categories=sorted(cat_counts.items()),
                           cat_counts=cat_counts, logged_in=is_admin())


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            flash("Bem-vindo ao painel!", "success")
            return redirect(url_for("admin"))
        flash("Senha incorreta!", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("admin", None)
    flash("Você saiu do painel.", "info")
    return redirect(url_for("index"))


@app.route("/admin")
def admin():
    if not is_admin():
        return redirect(url_for("login"))
    games = load_games()
    return render_template("admin.html", games=games, categories=CATEGORIES)


@app.route("/covers/<filename>")
def serve_cover(filename):
    return send_file(os.path.join(COVERS_DIR, filename))


@app.route("/_status")
def status():
    """Status do servidor - usado pelo watchdog."""
    return jsonify({
        "ok": True,
        "active_downloads": get_active_downloads(),
        "active_uploads": len(UPLOAD_CHUNKS),
    })


@app.route("/download/<game_id>")
def download(game_id):
    games = load_games()
    game = next((g for g in games if g["id"] == game_id), None)
    if not game:
        flash("Jogo nao encontrado!", "error")
        return redirect(url_for("index"))

    game_path = os.path.join(GAMES_DIR, game["file"])
    if not os.path.exists(game_path):
        flash("Arquivo nao encontrado!", "error")
        return redirect(url_for("index"))

    game["downloads"] += 1
    save_games(games)

    download_name = f"{game['title'].replace(' ', '_')}{os.path.splitext(game['file'])[1]}"
    file_size = os.path.getsize(game_path)

    # Registra download ativo (watchdog espera terminar)
    global ACTIVE_DOWNLOADS
    with DOWNLOADS_LOCK:
        ACTIVE_DOWNLOADS += 1

    # Headers que impedem o navegador de cancelar
    common_headers = {
        "Content-Disposition": f'attachment; filename="{download_name}"',
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
        "X-Download-Options": "noopen",
    }

    try:
        # Suporte a Range (retomar download no navegador)
        range_header = request.headers.get("Range")
        if range_header:
            m = range_header.replace("bytes=", "").split("-")
            start = int(m[0]) if m[0] else 0
            end = int(m[1]) if m[1] else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1

            def ranged():
                global ACTIVE_DOWNLOADS
                try:
                    with open(game_path, "rb") as f:
                        f.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk = f.read(min(CHUNK_SIZE, remaining))
                            if not chunk:
                                break
                            yield chunk
                            remaining -= len(chunk)
                finally:
                    with DOWNLOADS_LOCK:
                        ACTIVE_DOWNLOADS = max(0, ACTIVE_DOWNLOADS - 1)

            resp = Response(ranged(), status=206, mimetype="application/octet-stream")
            resp.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
            resp.headers["Content-Length"] = str(length)
            for k, v in common_headers.items():
                resp.headers[k] = v
            return resp

        def generate():
            global ACTIVE_DOWNLOADS
            try:
                with open(game_path, "rb") as f:
                    while True:
                        chunk = f.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        yield chunk
            finally:
                with DOWNLOADS_LOCK:
                    ACTIVE_DOWNLOADS = max(0, ACTIVE_DOWNLOADS - 1)

        headers = dict(common_headers)
        headers["Content-Length"] = str(file_size)
        return Response(generate(), mimetype="application/octet-stream", headers=headers)

    except Exception:
        with DOWNLOADS_LOCK:
            ACTIVE_DOWNLOADS = max(0, ACTIVE_DOWNLOADS - 1)
        raise


@app.route("/admin/delete/<game_id>", methods=["POST"])
def delete_game(game_id):
    if not is_admin():
        return redirect(url_for("login"))

    games = load_games()
    game = next((g for g in games if g["id"] == game_id), None)
    if game:
        for folder, fname in [(GAMES_DIR, game["file"]), (COVERS_DIR, game["cover"])]:
            path = os.path.join(folder, fname)
            if os.path.exists(path):
                os.remove(path)
        games.remove(game)
        save_games(games)
        flash(f"Jogo '{game['title']}' removido!", "info")
    return redirect(url_for("admin"))


# ---------- COMPRESSAO ----------
@app.after_request
def compress(response):
    if request.path.startswith("/download") or request.path.startswith("/covers"):
        return response
    if request.path.endswith("/chunk") or request.path.endswith("/finish"):
        return response

    data = response.get_data()
    if len(data) < 500:
        return response

    encodings = request.headers.get("Accept-Encoding", "")
    if "gzip" in encodings:
        import gzip
        compressed = gzip.compress(data, 6)
        if len(compressed) < len(data):
            response.set_data(compressed)
            response.headers["Content-Encoding"] = "gzip"
            response.headers["Content-Length"] = str(len(compressed))
    return response


# ---------- INICIO ----------
if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    # Porta do ambiente (Render usa $PORT) ou 5000 local
    import os as _os
    port = int(_os.environ.get("PORT", 5000))
    host = _os.environ.get("HOST", "0.0.0.0")
    print(f"\n" + "=" * 55)
    print("  [!] NEXUSPLAY ONLINE")
    print(f"  [*] Porta: {port}")
    print("  [*] Senha admin: admin123")
    print("=" * 55 + "\n")

    serve(app,
          host=host,
          port=port,
          threads=8,
          backlog=2048,
          recv_bytes=32768,
          send_bytes=32768,
          channel_timeout=86400,   # 24 HORAS
          cleanup_interval=300,
          )
