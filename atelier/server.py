#!/usr/bin/env python3
"""Serveur de l'atelier (SPEC.md §3) — lecture de runs/oadlab.sqlite.

Stdlib uniquement, local uniquement (127.0.0.1). Le front (static/)
est une page unique qui polle les endpoints JSON. Seule écriture
autorisée : POST /api/intents (les verbes de pilotage, P4) — le
serveur n'exécute jamais rien.

Un fil de rafraîchissement importe toutes les 30 s le dernier run
d'évolution (film du log → events, checkpoint → programs), pour que la
base — la seule chose que le front lise (P1) — suive un run en cours.

Usage : .venv/bin/python atelier/server.py [port]   (défaut 8420)
"""

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "harness"))
from oadlab import config, db  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
EVOLUTION_RUNS = config.RUNS / "evolution"
REFRESH_S = 30

PROGRAM_COLS = ("id, run, parent_id, generation, iteration, ts,"
                " combined_score, wr_easy, wr_medium, wr_hard,"
                " aggression, boom, games, changes,"
                " length(code) AS code_len")


def rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def refresher():
    """Réimporte le dernier run en continu. Sous-processus (scripts/
    déjà idempotents) : un import qui casse ne tue pas le serveur."""
    py = sys.executable
    while True:
        try:
            run_dirs = sorted(d for d in EVOLUTION_RUNS.iterdir()
                              if (d / "night.log").exists()) \
                if EVOLUTION_RUNS.exists() else []
            if run_dirs:
                latest = run_dirs[-1]
                subprocess.run([py, str(REPO / "scripts/import_run.py"),
                                str(latest)], capture_output=True,
                               timeout=60)
                ckpts = sorted((latest / "checkpoints").glob("checkpoint_*"),
                               key=lambda p: int(p.name.split("_")[1])) \
                    if (latest / "checkpoints").exists() else []
                if ckpts:
                    subprocess.run(
                        [py, str(REPO / "scripts/export_lineage.py"),
                         str(ckpts[-1])], capture_output=True, timeout=60)
        except Exception as e:  # noqa: BLE001 — le fil ne doit pas mourir
            print(f"refresher: {e}", file=sys.stderr)
        time.sleep(REFRESH_S)


class Handler(BaseHTTPRequestHandler):

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, name):
        f = (STATIC / name).resolve()
        if not (f.is_file() and STATIC in f.parents):
            self.send_error(404)
            return
        ctype = {"html": "text/html", "js": "text/javascript",
                 "css": "text/css"}.get(f.suffix[1:], "text/plain")
        body = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path
        if path == "/":
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])

        con = db.connect()
        try:
            if path == "/api/runs":
                return self._json(rows(
                    con, "SELECT * FROM runs ORDER BY started DESC"))
            if path == "/api/programs":
                if not q.get("run"):
                    return self._json({"error": "run requis"}, 400)
                return self._json(rows(
                    con, f"SELECT {PROGRAM_COLS} FROM programs"
                    " WHERE run = ? ORDER BY iteration", (q["run"],)))
            if path == "/api/program":
                p = rows(con, "SELECT * FROM programs WHERE id = ?",
                         (q.get("id"),))
                if not p:
                    return self._json({"error": "inconnu"}, 404)
                prog = p[0]
                parent = rows(con,
                              "SELECT code FROM programs WHERE id = ?",
                              (prog["parent_id"],)) \
                    if prog["parent_id"] else []
                prog["parent_code"] = parent[0]["code"] if parent else None
                return self._json(prog)
            if path == "/api/events":
                if not q.get("run"):
                    return self._json({"error": "run requis"}, 400)
                return self._json(rows(
                    con, "SELECT ts, kind, iteration, program_id, payload"
                    " FROM events WHERE run = ? AND ts > ? ORDER BY ts",
                    (q["run"], float(q.get("since", 0)))))
            if path == "/api/intents":
                return self._json(rows(
                    con, "SELECT * FROM intents ORDER BY ts DESC"))
            return self._json({"error": "route inconnue"}, 404)
        except (ValueError, KeyError) as e:
            return self._json({"error": f"requête invalide: {e}"}, 400)
        except Exception as e:  # noqa: BLE001 — toujours répondre en JSON
            return self._json({"error": str(e)}, 500)
        finally:
            con.close()

    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path != "/api/intents":
            return self._json({"error": "route inconnue"}, 404)
        try:
            length = min(int(self.headers.get("Content-Length", 0)), 8192)
            data = json.loads(self.rfile.read(length))
            verb = data["verb"]
            if verb not in ("pin", "cut", "branch", "explore"):
                raise ValueError(verb)
            target = str(data["target"])[:200]
            note = (str(data["note"])[:2000]
                    if data.get("note") is not None else None)
        except (json.JSONDecodeError, KeyError, ValueError):
            return self._json({"error": "intent invalide"}, 400)
        con = db.connect()
        try:
            # author forcé : tout ce qui passe par HTTP vient du front.
            db.add_intent(con, author="user", verb=verb,
                          target=target, note=note)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 500)
        finally:
            con.close()
        return self._json({"ok": True})

    def log_message(self, fmt, *args):
        pass  # silencieux — le terminal reste lisible


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8420
    threading.Thread(target=refresher, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"atelier : http://127.0.0.1:{port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
