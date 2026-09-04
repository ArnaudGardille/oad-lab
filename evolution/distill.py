#!/usr/bin/env python3
"""Le distillateur du carnet de laboratoire (SPEC.md §3 bis, chantier A).

Lit les expériences d'un run (hypothèses, prédictions, verdicts,
résumés de changements, deltas de métriques vs parent) et MET À JOUR
la table `lessons` : renforce, contredit, fusionne, retire — jamais
d'append aveugle. Le composeur (apply_intents) injecte ensuite le
carnet actif dans le prompt du générateur.

Agent jetable et désarmé (P5) ; coût compté en base (P6, agent_runs).
Les énoncés sont en anglais : leur consommateur premier est le
system_message du générateur.

Usage : distill.py <run_id> [--dry-run]
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "harness"))
from oadlab import db  # noqa: E402

ROLE = "distiller"
BUDGET_USD = 1.0
TIMEOUT_S = 300
MAX_ACTIVE_LESSONS = 24   # le carnet doit rester assimilable d'un
                          # coup d'œil — fusionner plutôt qu'empiler

SYSTEM = f"""You are the lab-notebook distiller for oad-lab, a system
that evolves 0 A.D. bot strategies through LLM mutations evaluated by
real games. You receive the CURRENT lesson book and a batch of NEW
experiments (each: strategic hypothesis if declared, prediction,
mechanically-computed verdict, LLM change summary, and measured deltas
vs its parent program).

Update the lesson book. Rules:
- A lesson is ONE actionable English sentence a strategy generator can
  use. Three kinds: "law" (what works, with conditions), "impasse"
  (what reliably fails), "question" (open, worth an experiment).
- UPDATE or MERGE existing lessons rather than adding near-duplicates;
  RETIRE lessons contradicted by new evidence. Keep the active book
  under {MAX_ACTIVE_LESSONS} lessons — prefer merging.
- Statistical honesty: score deltas under 0.1 on 24-48 games are
  noise. Only distill patterns supported by repetition or large
  effects. Cite evidence (program ids). Do not invent.
- confidence: short French tag, e.g. "confirmé 3x", "faible (1x)",
  "contesté".

Reply with a STRICT JSON array, nothing else. Each element:
  {{"op": "add", "kind": "law|impasse|question", "statement": "...",
    "confidence": "...", "evidence": ["id8", ...]}}
  {{"op": "update", "id": <lesson id>, "statement": "...",
    "confidence": "...", "evidence": [...]}}
  {{"op": "retire", "id": <lesson id>}}
An empty array [] is a valid answer if nothing new is learned."""

# Aligné sur evolution/bin/claude : Read/Glob/Grep/NotebookRead sont
# bloqués aussi — le matériau (hypothèses/changes) est écrit par le
# LLM générateur, donc non fiable ; sans path confinement, un texte
# injecté pourrait faire lire puis fuiter des fichiers arbitraires via
# les statements du carnet, qui atteignent tous les prompts suivants.
DISALLOWED = ("Bash,Edit,Write,NotebookEdit,Task,Agent,WebFetch,"
              "WebSearch,TodoWrite,KillShell,Read,Glob,Grep,NotebookRead")


def material(con, run):
    rows = [dict(r) for r in con.execute(
        "SELECT id, parent_id, iteration, combined_score, wr_easy,"
        " wr_medium, wr_hard, aggression, boom, games, hypothesis,"
        " prediction, verdict, changes FROM programs WHERE run = ?"
        " ORDER BY iteration", (run,))]
    by_id = {r["id"]: r for r in rows}
    out = []
    for r in rows:
        parent = by_id.get(r["parent_id"])
        exp = {"id": r["id"][:8], "iter": r["iteration"],
               "score": r["combined_score"], "games": r["games"],
               "hypothesis": r["hypothesis"], "prediction": r["prediction"],
               "verdict": r["verdict"],
               "changes": (r["changes"] or "")[:400] or None}
        if parent:
            deltas = {}
            for k in ("combined_score", "wr_easy", "wr_medium",
                      "wr_hard", "aggression", "boom"):
                if r[k] is not None and parent[k] is not None:
                    deltas[k] = round(r[k] - parent[k], 3)
            exp["deltas_vs_parent"] = deltas
        out.append(exp)
    return out


def parse_ops(text):
    """Le contrat est « JSON strict », mais on tolère les clôtures
    markdown que les modèles ajoutent parfois."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    ops = json.loads(text)
    if not isinstance(ops, list):
        raise ValueError("réponse non-liste")
    return ops


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    run = sys.argv[1]
    dry = "--dry-run" in sys.argv[2:]
    claude = shutil.which("claude")
    if not claude:
        sys.exit("CLI claude introuvable")

    con = db.connect()
    lessons = [{"id": r["id"], "kind": r["kind"],
                "statement": r["statement"],
                "confidence": r["confidence"]}
               for r in db.active_lessons(con)]
    exps = material(con, run)
    if not exps:
        sys.exit(f"aucun programme en base pour le run {run}")
    prompt = json.dumps({"current_lessons": lessons,
                         "new_experiments": exps}, ensure_ascii=False)

    r = subprocess.run(
        [claude, "-p", "--model", "sonnet", "--no-session-persistence",
         "--output-format", "json", "--max-budget-usd", str(BUDGET_USD),
         "--strict-mcp-config", "--disallowedTools", DISALLOWED,
         "--system-prompt", SYSTEM, prompt],
        capture_output=True, text=True, timeout=TIMEOUT_S)
    cost = None
    try:
        out = json.loads(r.stdout)
        text = out.get("result", "")
        cost = out.get("total_cost_usd", out.get("cost_usd"))
    except json.JSONDecodeError:
        text = r.stdout
    try:
        ops = parse_ops(text)
    except (json.JSONDecodeError, ValueError) as e:
        db.record_agent_run(con, role=ROLE, run=run, cost_usd=cost,
                            ok=False, detail=f"sortie illisible: {e}")
        sys.exit(f"sortie du distillateur illisible ({e}) :\n{text[:500]}")

    if dry:
        print(json.dumps(ops, indent=1, ensure_ascii=False))
        print(f"-- dry-run : {len(ops)} opération(s), coût {cost} USD,"
              " rien d'appliqué")
        return
    # Le coût est dû dès que l'appel LLM a eu lieu : il doit être
    # enregistré même si l'application des ops échoue.
    try:
        done, bad = db.apply_lesson_ops(con, ops, updated_by=run,
                                        max_active=MAX_ACTIVE_LESSONS)
    except Exception as e:
        db.record_agent_run(con, role=ROLE, run=run, cost_usd=cost,
                            ok=False, detail=f"application échouée: {e}")
        raise
    db.record_agent_run(con, role=ROLE, run=run, cost_usd=cost, ok=True,
                        detail=f"{done} ops, {len(bad)} rejetées")
    active = len(db.active_lessons(con))
    con.close()
    print(f"carnet mis à jour : {done} opération(s) appliquée(s),"
          f" {len(bad)} rejetée(s), {active} leçon(s) active(s),"
          f" coût {cost} USD")
    for b in bad:
        print(f"  rejetée : {b}", file=sys.stderr)


if __name__ == "__main__":
    main()
