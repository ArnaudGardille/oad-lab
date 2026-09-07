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
import re
import shutil
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
                " aggression, boom, games,"
                # Le score confirmé vit sur un autre protocole que
                # combined_score : le front doit pouvoir distinguer un
                # candidat estimé sur 24 parties d'un champion rejoué
                # sur 96 (P3), donc il reçoit les deux séparément.
                " confirmed_score, confirmed_games, changes, code_sha,"
                " hypothesis, prediction, verdict,"
                " length(code) AS code_len")

# Séries de metadata.json montrées par défaut dans les graphes de
# partie (le reste est listé et disponible à la demande).
DEFAULT_SERIES = [
    ("populationCount", "population"),
    ("enemyUnitsKilledValue", "valeur ennemie détruite"),
    ("unitsLostValue", "valeur perdue"),
    ("percentMapControlled", "% carte contrôlée"),
]


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
            if path == "/api/lessons":
                return self._json(rows(
                    con, "SELECT * FROM lessons WHERE status='active'"
                    " ORDER BY kind, id"))
            if path == "/api/matches":
                return self._json(self._matches(con, q.get("id")))
            if path == "/api/series":
                return self._json(self._series(q.get("replay"),
                                               int(q.get("player", 1))))
            return self._json({"error": "route inconnue"}, 404)
        except (ValueError, KeyError) as e:
            return self._json({"error": f"requête invalide: {e}"}, 400)
        except Exception as e:  # noqa: BLE001 — toujours répondre en JSON
            return self._json({"error": str(e)}, 500)
        finally:
            con.close()

    MATCH_COLS = ("ts, candidate, opponent, opp_diff, cand_pos, seed,"
                  " cand_won, timed_out, game_s, turns, replay,"
                  " aggression, boom, military, map_control, protocol")

    def _matches(self, con, program_id):
        """Les matchs d'un programme. Lien nominal : tag par hash de
        code (cand-<sha>). Repli pour les runs d'avant ce lien : la
        fenêtre temporelle autour de l'événement `evaluated`."""
        p = rows(con, "SELECT code_sha, run FROM programs WHERE id = ?",
                 (program_id,))
        if not p:
            return []
        sha = p[0]["code_sha"]
        if sha:
            # Les trois étages de la cascade portent le même hash de
            # code : -s1 la sonde, le tag nu la sélection, -s3 la
            # confirmation. Omettre -s3 cachait justement les parties
            # qui fondent le score défendable — or c'est ce que
            # l'atelier doit montrer (P3 : 24 parties et 200 parties ne
            # doivent jamais se ressembler à l'écran).
            got = rows(con, f"SELECT {self.MATCH_COLS} FROM matches"
                       " WHERE candidate IN (?, ?, ?) ORDER BY ts",
                       (f"cand-{sha}", f"cand-{sha}-s1", f"cand-{sha}-s3"))
            if got:
                return got
        ev = rows(con, "SELECT ts FROM events WHERE run = ? AND"
                  " kind = 'evaluated' AND program_id = ?"
                  " ORDER BY ts DESC LIMIT 1", (p[0]["run"], program_id))
        if not ev:
            return []
        # parallel_evaluations=1 : les batchs sont sérialisés, la
        # borne basse exacte est l'événement `evaluated` précédent.
        prev = rows(con, "SELECT max(ts) AS t FROM events WHERE run = ?"
                    " AND kind = 'evaluated' AND ts < ?",
                    (p[0]["run"], ev[0]["ts"]))
        lo = prev[0]["t"] or ev[0]["ts"] - 900
        return rows(con, f"SELECT {self.MATCH_COLS} FROM matches"
                    " WHERE ts > ? AND ts <= ? ORDER BY ts",
                    (lo, ev[0]["ts"] + 5))

    def _series(self, replay, player):
        """Les séries temporelles d'une partie, côté `player` (1 ou 2).
        Lues du metadata.json du replay moissonné — jamais de chemin
        arbitraire : nom de dossier strict, résolu sous runs/matches."""
        if not replay or not re.fullmatch(r"[\w.-]+", replay) \
                or replay in (".", ".."):
            raise ValueError("replay invalide")
        meta = (config.MATCHES_DIR / replay / "metadata.json").resolve()
        if config.MATCHES_DIR.resolve() not in meta.parents:
            raise ValueError("replay invalide")
        if not meta.is_file():
            return {"error": "replay inconnu"}
        m = json.loads(meta.read_text(errors="replace"))
        states = m.get("playerStates", [])
        if len(states) <= player:
            return {"error": "joueur absent"}
        seq = states[player].get("sequences") or {}
        out = {"time": seq.get("time", []), "series": {}, "available": []}
        for key, label in DEFAULT_SERIES:
            if key in seq and isinstance(seq[key], list):
                out["series"][label] = seq[key]
        for k, v in seq.items():
            if isinstance(v, list) and k != "time":
                out["available"].append(k)
        return out

    CHAT_SYSTEM = (
        "Tu es l'analyste de l'atelier oad-lab : un laboratoire qui fait "
        "évoluer des bots 0 A.D. par mutations LLM, évaluées par parties "
        "réelles contre des ancres Petra et un hall of fame. Chaque "
        "programme porte une hypothèse stratégique, une prédiction et un "
        "verdict mesuré ; les descripteurs (aggression, boom, military, "
        "map_control) sont dans [0,1]. Les scores sur moins de ~50 parties "
        "sont bruités (IC ±0,15-0,2) — ne surinterprète jamais un delta "
        "fin. Réponds en français, bref et concret, en t'appuyant "
        "uniquement sur le CONTEXTE JSON fourni ; dis-le franchement "
        "quand les données ne permettent pas de conclure. Tu ne peux "
        "RIEN exécuter : si une action serait utile (épingler, couper, "
        "explorer une zone, confirmer un score sur plus de parties), "
        "suggère-la à l'humain.")

    def _chat_context(self, con, program_id):
        ctx = {"runs": rows(con, "SELECT id, status, iterations_done,"
                            " errors, games, hof FROM runs"
                            " ORDER BY started DESC LIMIT 3")}
        ctx["meilleurs_programmes"] = rows(
            con, f"SELECT {PROGRAM_COLS} FROM programs"
            " WHERE combined_score IS NOT NULL"
            " ORDER BY combined_score DESC LIMIT 8")
        ctx["carnet"] = rows(
            con, "SELECT kind, statement, confidence FROM lessons"
            " WHERE status='active' ORDER BY kind, id")
        ctx["derniers_evenements"] = rows(
            con, "SELECT ts, run, kind, iteration, program_id FROM events"
            " WHERE kind IN ('new_best','error','completed')"
            " ORDER BY ts DESC LIMIT 10")
        if program_id:
            p = rows(con, f"SELECT {PROGRAM_COLS} FROM programs"
                     " WHERE id = ?", (program_id,))
            if p:
                ctx["programme_selectionne"] = p[0]
                ctx["ses_matchs"] = self._matches(con, program_id)
        for progs in (ctx["meilleurs_programmes"],):
            for p in progs:
                p.pop("changes", None)
        return ctx

    def _chat(self, con, question, program_id):
        claude = shutil.which("claude")
        if not claude:
            return {"error": "CLI claude introuvable"}
        ctx = self._chat_context(con, program_id)
        prompt = (f"CONTEXTE JSON:\n{json.dumps(ctx, ensure_ascii=False)}"
                  f"\n\nQUESTION: {question}")
        # Même règle que la boucle (P5) : l'agent produit du texte,
        # il n'agit pas.
        # --strict-mcp-config sans --mcp-config : AUCUN serveur MCP.
        # Le contexte contient du texte écrit par le LLM générateur ;
        # sans ceci, une injection y trouverait les connecteurs réels
        # de la machine (mail, banque...) que --disallowedTools ne
        # couvre pas (revue 2026-09-03).
        # Read/Glob/Grep/NotebookRead bloqués aussi : même contexte
        # injectable, risque d'exfiltration via lecture du disque.
        r = subprocess.run(
            [claude, "-p", "--model", "sonnet", "--no-session-persistence",
             "--output-format", "text", "--max-budget-usd", "0.5",
             "--strict-mcp-config", "--disallowedTools",
             "Bash,Edit,Write,NotebookEdit,Task,Agent,WebFetch,WebSearch,"
             "TodoWrite,KillShell,Read,Glob,Grep,NotebookRead",
             "--system-prompt", self.CHAT_SYSTEM, prompt],
            capture_output=True, text=True, timeout=120)
        answer = r.stdout.strip()
        if not answer:
            return {"error": f"pas de réponse ({r.stderr.strip()[:200]})"}
        return {"answer": answer}

    def _same_origin(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        host = self.headers.get("Host", "")
        return origin in (f"http://{host}", f"https://{host}")

    def do_POST(self):  # noqa: N802
        if not self._same_origin():
            return self._json({"error": "origine refusée"}, 403)
        path = urlparse(self.path).path
        if path == "/api/chat":
            try:
                length = min(int(self.headers.get("Content-Length", 0)),
                             16384)
                data = json.loads(self.rfile.read(length))
                question = str(data["question"])[:2000]
            except (json.JSONDecodeError, KeyError, ValueError):
                return self._json({"error": "question invalide"}, 400)
            con = db.connect()
            try:
                return self._json(self._chat(con, question,
                                             data.get("program_id")))
            except subprocess.TimeoutExpired:
                return self._json({"error": "délai dépassé (120 s)"}, 504)
            except Exception as e:  # noqa: BLE001
                return self._json({"error": str(e)}, 500)
            finally:
                con.close()
        if path != "/api/intents":
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
