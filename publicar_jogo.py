#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================
 NEXUSPLAY - PUBLICADOR DE JOGOS NA NUVEM GRATIS
 Internet Archive (archive.org): 1 TB por jogo, $0, sem cartao
============================================================

O que faz:
  1. Sobe o arquivo do jogo (.zip/.rar/.7z) pro Internet Archive
     - Upload MULTIPART de 50 MB: se cair a internet, roda o mesmo
       comando de novo e CONTINUA de onde parou (nao refaz tudo!)
  2. Gera o link direto de download
  3. Registra o jogo automaticamente na loja NexusPlay
     (titulo, descricao, categoria, capa)

Uso:
  python publicar_jogo.py "Meu Jogo.zip"
  python publicar_jogo.py "Meu Jogo.zip" --titulo "Need for Speed" --categoria Carros --capa capa.jpg
  python publicar_jogo.py "Meu Jogo.zip" --site https://nexusplay-dsrs.onrender.com

Primeira vez: o script pede suas chaves do Internet Archive
(crie gratis em https://archive.org/account/s3.php) e salva no
arquivo ia_config.json pra usar sempre.

Opcoes uteis:
  --sem-registrar   So sobe pro Internet Archive, nao mexe na loja
  --reset           Ignora progresso salvo e sobe tudo de novo
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

# requests e usado pra upload/streaming (instala sozinho se faltar)
try:
    import requests
except ImportError:
    print("[*] Instalando biblioteca 'requests'...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "requests"])
    import requests

# ------------------------------------------------------------
#  CONFIGURACAO
# ------------------------------------------------------------
S3_URL = "https://s3.us.archive.org"
METADATA_URL = "https://archive.org/metadata/{item}"
UA = "NexusPlay-Publisher/1.0 (publicador de jogos)"
PART_SIZE = 50 * 1024 * 1024          # 50 MB por bloco
MAX_TRIES_PER_PART = 10               # tentativas por bloco
ALLOWED_EXT = {".zip", ".rar", ".7z"}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(SCRIPT_DIR, "ia_config.json")


def log(msg=""):
    print(msg, flush=True)


def fail(msg):
    print(f"\n❌ {msg}", flush=True)
    sys.exit(1)


def slugify(text):
    """'GTA San Andreas' -> 'gta-san-andreas'"""
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:60] or "jogo"


def human_size(n):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ------------------------------------------------------------
#  CONFIG (chaves do Internet Archive + dados da loja)
# ------------------------------------------------------------
def load_config(path):
    cfg = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

    cfg.setdefault("access_key", os.environ.get("IA_ACCESS_KEY", ""))
    cfg.setdefault("secret_key", os.environ.get("IA_SECRET_KEY", ""))
    cfg.setdefault("site", os.environ.get("NEXUSPLAY_SITE", "http://localhost:5000"))
    cfg.setdefault("password", os.environ.get("NEXUSPLAY_PASS", "admin123"))
    return cfg


def ensure_keys(cfg, config_path):
    """Pede as chaves na primeira vez e salva no arquivo."""
    if cfg.get("access_key") and cfg.get("secret_key"):
        return cfg

    log("\n🔑 Primeira vez! Preciso das suas chaves do Internet Archive.")
    log("   (Criar gratis: https://archive.org/account/s3.php  ->  'Generate New Keys')\n")
    cfg["access_key"] = input("   Access Key: ").strip()
    cfg["secret_key"] = input("   Secret Key: ").strip()
    if not cfg["access_key"] or not cfg["secret_key"]:
        fail("Chaves vazias. Gere as chaves em https://archive.org/account/s3.php")

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        log(f"   ✅ Chaves salvas em {os.path.basename(config_path)}\n")
    except Exception as e:
        log(f"   ⚠️ Nao consegui salvar o config: {e}\n")
    return cfg


# ------------------------------------------------------------
#  UPLOAD MULTIPART PRO INTERNET ARCHIVE (RETOMAVEL)
# ------------------------------------------------------------
def state_file_for(zip_path):
    return os.path.join(os.path.dirname(zip_path),
                        "." + os.path.basename(zip_path) + ".nexusplay_state.json")


def load_state(path, zip_path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            st = json.load(f)
        # So retoma se o arquivo nao mudou
        if (st.get("file_size") == os.path.getsize(zip_path)
                and st.get("file_mtime") == int(os.path.getmtime(zip_path))):
            return st
    except Exception:
        pass
    return None


def save_state(path, st):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except Exception:
        pass


def archive_request(method, url, cfg, **kwargs):
    headers = kwargs.pop("headers", {})
    headers.setdefault("User-Agent", UA)
    headers["Authorization"] = f"LOW {cfg['access_key']}:{cfg['secret_key']}"
    return requests.request(method, url, headers=headers, timeout=180, **kwargs)


def upload_to_archive(zip_path, item, filename, cfg, reset=False):
    """Sobe o arquivo em blocos de 50MB. Retomavel: mesma instrucao = continua."""
    state_path = state_file_for(zip_path)
    total = os.path.getsize(zip_path)
    file_mtime = int(os.path.getmtime(zip_path))

    state = None if reset else load_state(state_path, zip_path)
    if state is None:
        state = {
            "item": item,
            "filename": filename,
            "upload_id": None,
            "parts": {},          # numero da parte -> etag
            "file_size": total,
            "file_mtime": file_mtime,
            "part_size": PART_SIZE,
        }

    endpoint = f"{S3_URL}/{requests.utils.quote(item)}/{requests.utils.quote(filename)}"

    # --- 1) Inicia o multipart (ou retoma o que ja existia) ---
    if not state["upload_id"]:
        log("\n[1/3] Criando item no Internet Archive...")
        resp = archive_request(
            "POST", f"{endpoint}?uploads", cfg,
            headers={
                "x-amz-meta-title": filename,
                "x-amz-meta-mediatype": "software",
            },
        )
        if resp.status_code not in (200, 201):
            fail(f"Internet Archive recusou o inicio do upload (HTTP {resp.status_code}).\n"
                 f"Resposta: {resp.text[:300]}")
        root = ET.fromstring(resp.text)
        upload_id = None
        for el in root.iter():
            if el.tag.endswith("UploadId"):
                upload_id = el.text
                break
        if not upload_id:
            fail(f"Nao achei o UploadId na resposta: {resp.text[:300]}")
        state["upload_id"] = upload_id
        save_state(state_path, state)
        log(f"      Item criado: {item}")
    else:
        log(f"\n[1/3] Retomando upload anterior ({len(state['parts'])} blocos ja enviados)")

    # --- 2) Envia os blocos que faltam ---
    n_parts = (total + PART_SIZE - 1) // PART_SIZE
    done_before = len(state["parts"])
    log(f"[2/3] Enviando {n_parts} blocos de {human_size(PART_SIZE)} "
        f"({human_size(total)} no total)...")
    if done_before:
        log(f"      Ja pronto: {done_before}/{n_parts} blocos")

    start_time = time.time()
    bytes_sent_before = done_before * PART_SIZE

    with open(zip_path, "rb") as f:
        for part_no in range(1, n_parts + 1):
            if str(part_no) in state["parts"]:
                continue

            offset = (part_no - 1) * PART_SIZE
            f.seek(offset)
            data = f.read(PART_SIZE)
            md5 = hashlib.md5(data).hexdigest()

            ok = False
            for attempt in range(1, MAX_TRIES_PER_PART + 1):
                try:
                    resp = archive_request(
                        "PUT",
                        f"{endpoint}?partNumber={part_no}&uploadId={state['upload_id']}",
                        cfg,
                        data=data,
                        headers={"Content-MD5": md5},
                    )
                    if resp.status_code in (200, 201):
                        etag = resp.headers.get("ETag", "").strip()
                        if not etag:
                            etag = f'"{md5}"'
                        state["parts"][str(part_no)] = etag
                        save_state(state_path, state)
                        ok = True
                        break
                    # 4xx (exceto 429) = nao adianta insistir muito
                    if resp.status_code in (400, 401, 403, 404) and attempt >= 2:
                        fail(f"Erro {resp.status_code} no bloco {part_no}: {resp.text[:300]}")
                    time.sleep(min(60, 2 ** attempt))
                except (requests.RequestException, OSError) as e:
                    if attempt >= MAX_TRIES_PER_PART:
                        fail(f"Bloco {part_no} falhou {MAX_TRIES_PER_PART}x: {e}\n"
                             f"Progresso SALVO. Rode o mesmo comando de novo pra continuar "
                             f"de onde parou.")
                    time.sleep(min(60, 2 ** attempt))

            if not ok:
                fail(f"Bloco {part_concluido(part_no, n_parts)} com problema. "
                     f"Progresso SALVO - rode o mesmo comando pra retomar.")

            # Barra de progresso simples
            enviados = done_before + part_no
            pct = enviados * 100 / n_parts
            elapsed = time.time() - start_time
            veloc = (part_no * PART_SIZE / elapsed / 1024 / 1024) if elapsed > 5 else 0
            eta_s = ((n_parts - enviados) * PART_SIZE / (veloc * 1024 * 1024)) if veloc else 0
            eta = f" - falta ~{eta_s/60:.0f} min" if eta_s > 60 else ""
            log(f"      Bloco {enviados}/{n_parts}  ({pct:.0f}%)  "
                f"{veloc:.1f} MB/s{eta}")

    # --- 3) Finaliza o multipart ---
    log("[3/3] Finalizando upload...")
    parts_xml = "".join(
        f"<Part><PartNumber>{n}</PartNumber><ETag>{etag}</ETag></Part>"
        for n, etag in sorted(state["parts"].items(), key=lambda x: int(x[0]))
    )
    body = f"<CompleteMultipartUpload>{parts_xml}</CompleteMultipartUpload>"

    resp = None
    for attempt in range(1, 6):
        try:
            resp = archive_request(
                "POST", f"{endpoint}?uploadId={state['upload_id']}", cfg,
                data=body.encode("utf-8"),
                headers={"Content-Type": "application/xml"},
            )
        except (requests.RequestException, OSError) as e:
            log(f"      ⚠️ Falha ao finalizar ({e}) - tentando de novo ({attempt}/5)...")
            time.sleep(10)
            continue

        if resp.status_code in (200, 201) and "Error" not in resp.text[:200]:
            save_state(state_path, {**state, "parts": {}, "upload_id": None, "done": True})
            try:
                os.remove(state_path)
            except OSError:
                pass
            return True

        # Upload id expirou/refaz: recomeca do zero automaticamente
        if "NoSuchUpload" in resp.text and attempt == 1:
            log("      ⚠️ Upload antigo expirou - recomecando (automático)...")
            try:
                os.remove(state_path)
            except OSError:
                pass
            return upload_to_archive(zip_path, item, filename, cfg, reset=True)

        time.sleep(10)

    fail(f"Nao consegui finalizar o multipart: {resp.text[:300]}\n"
         f"Progresso SALVO - rode o mesmo comando pra retomar.")


def part_concluido(a, b):
    return f"{a}/{b}"


# ------------------------------------------------------------
#  METADADOS + CHECAR SE O LINK JA FUNCIONA
# ------------------------------------------------------------
def set_metadata(item, title, description, cfg):
    payload = {
        "title": title,
        "description": description or title,
        "mediatype": "software",
    }
    try:
        archive_request("POST", METADATA_URL.format(item=item), cfg,
                        json=payload, timeout=60)
    except Exception:
        pass  # nao critico: o arquivo ja esta la


def wait_download_ready(url, minutes=8):
    """Espera o Internet Archive terminar de 'ingerir' o arquivo."""
    deadline = time.time() + minutes * 60
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            r = requests.get(url, headers={"Range": "bytes=0-1023", "User-Agent": UA},
                             timeout=60, stream=True)
            if r.status_code in (200, 206):
                r.close()
                return True
            r.close()
        except requests.RequestException:
            pass
        if attempt <= 4 or attempt % 8 == 0:
            log(f"      ⏳ Aguardando o arquivo ficar disponivel... "
                f"(tentativa {attempt})")
        time.sleep(15)
    return False


# ------------------------------------------------------------
#  REGISTRA O JOGO NA LOJA NEXUSPLAY
# ------------------------------------------------------------
def register_on_site(site, cfg, title, description, category, external_url,
                     size, cover_path):
    s = requests.Session()
    s.headers["User-Agent"] = UA

    # Login
    r = s.post(f"{site.rstrip('/')}/login",
               data={"password": cfg["password"]}, timeout=60)
    if r.status_code != 200:
        return False, f"Login falhou (HTTP {r.status_code}). Site no ar? ({site})"

    # Cadastra o jogo
    data = {
        "title": title,
        "description": description,
        "category": category,
        "external_url": external_url,
        "size": str(size),
    }
    files = None
    if cover_path and os.path.exists(cover_path):
        files = {"cover_file": (os.path.basename(cover_path),
                                open(cover_path, "rb"),
                                "application/octet-stream")}
    try:
        r = s.post(f"{site.rstrip('/')}/admin/upload_link", data=data, files=files,
                   headers={"X-Requested-With": "XMLHttpRequest"}, timeout=120)
    finally:
        if files:
            try:
                files["cover_file"][1].close()
            except Exception:
                pass

    if r.status_code != 200:
        return False, f"Cadastro falhou (HTTP {r.status_code}): {r.text[:200]}"
    try:
        j = r.json()
    except Exception:
        return False, f"Resposta inesperada: {r.text[:200]}"
    if not j.get("ok"):
        return False, f"Resposta: {r.text[:200]}"
    return True, j.get("id", "")


# ------------------------------------------------------------
#  MAIN
# ------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Publica um jogo na loja NexusPlay usando a nuvem gratis do Internet Archive.")
    ap.add_argument("arquivo", help="Caminho do arquivo do jogo (.zip/.rar/.7z)")
    ap.add_argument("--titulo", default=None, help="Titulo do jogo")
    ap.add_argument("--descricao", default="", help="Descricao do jogo")
    ap.add_argument("--categoria", default="", help="Ex: Acao, Carros, Tiro...")
    ap.add_argument("--capa", default=None, help="Imagem de capa (.jpg/.png)")
    ap.add_argument("--site", default=None, help="URL da loja (padrao: localhost:5000)")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="Arquivo de configuracao")
    ap.add_argument("--senha", default=None, help="Senha do admin da loja")
    ap.add_argument("--sem-registrar", action="store_true",
                    help="So sobe pro Internet Archive, nao registra na loja")
    ap.add_argument("--reset", action="store_true",
                    help="Ignora progresso salvo e sobe tudo de novo")
    args = ap.parse_args()

    zip_path = os.path.abspath(args.arquivo)
    if not os.path.exists(zip_path):
        fail(f"Arquivo nao encontrado: {zip_path}")
    ext = os.path.splitext(zip_path)[1].lower()
    if ext not in ALLOWED_EXT:
        fail(f"Formato invalido ({ext}). Use .zip, .rar ou .7z")

    size = os.path.getsize(zip_path)
    titulo = (args.titulo or os.path.splitext(os.path.basename(zip_path))[0]).strip()

    cfg = load_config(args.config)
    cfg = ensure_keys(cfg, args.config)
    if args.site:
        cfg["site"] = args.site
    if args.senha:
        cfg["password"] = args.senha

    slug = slugify(titulo)
    item = f"nexusplay-{slug}-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:4]}"
    filename = f"{slug}{ext}"
    download_url = f"https://archive.org/download/{item}/{requests.utils.quote(filename)}"

    log("=" * 58)
    log("  🎮 NEXUSPLAY - PUBLICAR JOGO NA NUVEM GRATIS")
    log("=" * 58)
    log(f"  📦 Arquivo : {os.path.basename(zip_path)} ({human_size(size)})")
    log(f"  📝 Titulo  : {titulo}")
    log(f"  🌐 Nuven   : Internet Archive (1 TB por jogo, $0)")
    log("=" * 58)

    upload_to_archive(zip_path, item, filename, cfg, reset=args.reset)

    log("\n[*] Definindo titulo/descricao no Internet Archive...")
    set_metadata(item, titulo, args.descricao, cfg)

    log("[*] Checando se o link de download ja funciona...")
    if wait_download_ready(download_url):
        log("      ✅ Link pronto!")
    else:
        log("      ⚠️ Ainda processando - o link deve funcionar em alguns minutos.")

    if args.sem_registrar:
        log("\n" + "=" * 58)
        log("  ✅ ARQUIVO NA NUVEM! (registro manual pulado)")
        log(f"  🔗 {download_url}")
        log("=" * 58)
        return

    log(f"\n[*] Registrando na loja {cfg['site']} ...")
    ok, info = register_on_site(cfg["site"], cfg, titulo, args.descricao,
                                args.categoria, download_url, size, args.capa)
    if ok:
        log("      ✅ Jogo cadastrado na loja!")
    else:
        log(f"      ⚠️ Nao consegui cadastrar automaticamente: {info}")
        log("      Cadastre manualmente no painel colando o link abaixo.")

    log("\n" + "=" * 58)
    log("  🎉 JOGO PUBLICADO!")
    log(f"  📦 Tamanho : {human_size(size)}")
    log(f"  🔗 Link    : {download_url}")
    log(f"  🌐 Loja    : {cfg['site']}")
    log("=" * 58)
    log("  Dica: se publicou na loja local, rode 'git push' pra")
    log("  atualizar tambem a loja online no Render.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏸️  Interrompido. Progresso SALVO - rode o mesmo comando pra continuar.")
        sys.exit(0)
