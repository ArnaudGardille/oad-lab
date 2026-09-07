#!/usr/bin/env python3
"""Vérifie EN JEU que la personnalité des bots du mod est bien épinglée.

Le pilotage se fait dans `_petrabot.js` (`this.Config.behavior`), et
0 A.D. 0.28 n'expose aucune option de ligne de commande pour le faire :
la seule preuve qui vaille est de lire la personnalité que le bot s'est
réellement tirée pendant une partie. Petra a bien une trace, mais elle
est inutilisable ici — `aiWarn(... + uneval(...))`, or `uneval` n'existe
plus dans le SpiderMonkey 128 du moteur. On construit donc un bot
jetable (`probebot`, copie de forkbot, donc porteur de l'épinglage), on
injecte notre propre trace juste après le tirage, on joue une partie et
on lit la valeur dans le journal du moteur (aiWarn n'atteint pas la
sortie standard).

Attendu : les deux tirages tombent dans la bande de AI_BEHAVIOR,
LUE dans le personalityList du bot (band()) et non recopiée ici — un
vérificateur qui code en dur ce qu'il vérifie ne vérifie rien. Pour
"balanced" cette bande vaut aujourd'hui [0.37, 0.63], donc toujours
sous personalityCut.strong (0.7) et au-dessus de personalityCut.weak
(0.3) : les branches discontinues de Petra sont inatteignables et la
politique ne dépend plus du tirage.

Usage : scripts/check_behavior_pin.py [nb_parties]   (défaut : 3)
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "harness"))
sys.path.insert(0, str(REPO / "scripts"))

from make_candidate import AI_DIR, make_bot  # noqa: E402
from oadlab import config, game  # noqa: E402

BOT = "probebot"
MARKER = "OADLAB_PERSONALITY"
GAME_LOG = Path.home() / "snap/0ad/current/.local/state/0ad/log/mainlog.html"

# Le moteur réécrit son journal à chaque lancement : on le lit après
# chaque partie, et le marqueur nous isole du bruit de Petra.
_TRACE = re.compile(MARKER + r"\s*(\{.*?\})")

# Ancre d'injection : la fin du bloc qui tire la personnalité.
_ANCHOR = '\t\t\t"cooperative": randFloat(0, 1)\n\t\t};\n\t}\n'


def build_probe_bot():
    dest = make_bot(BOT, "Bot jetable — trace de personnalité injectée.")
    cfg = dest / "config.js"
    src = cfg.read_text()
    if src.count(_ANCHOR) != 1:
        raise RuntimeError("bloc de tirage de la personnalité introuvable")
    trace = _ANCHOR.rstrip("\n") + \
        f'\n\taiWarn("{MARKER} " + JSON.stringify(this.personality));\n'
    cfg.write_text(src.replace(_ANCHOR, trace, 1))
    return dest


def play(seed):
    """Joue une partie headless : la trace atterrit dans le journal du
    moteur, que `personalities()` relit ensuite.

    Le lancement passe par Popen + game.kill_game, jamais par
    `subprocess.run(timeout=...)` : sous un label AppArmor de session,
    le kill de subprocess vers le petit-fils pyrogenesis part en EPERM,
    la partie survit et fuit à plusieurs Go (CLAUDE.md, game.py)."""
    cmd = [config.GAME_CMD,
           *[f"--mod={m}" for m in config.MODS],
           f"--autostart={config.MAP}",
           f"--autostart-size={config.MAP_SIZE}",
           f"--autostart-biome={config.BIOME}",
           f"--autostart-seed={seed}",
           f"--autostart-aiseed={seed * 7 + 1}",
           f"--autostart-civ=1:{config.CIV}",
           f"--autostart-civ=2:{config.CIV}",
           f"--autostart-ai=1:{BOT}",
           f"--autostart-ai=2:{BOT}",
           f"--autostart-aidiff=1:{config.CANDIDATE_DIFF}",
           f"--autostart-aidiff=2:{config.CANDIDATE_DIFF}",
           "--autostart-nonvisual"]
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        p.wait(timeout=config.GAME_TIMEOUT)
    except subprocess.TimeoutExpired:
        game.kill_game(p)


def band(behavior):
    """La bande [min, max] de `behavior`, LUE dans le config.js du bot
    plutôt que recopiée ici : ce script existe pour détecter une
    désynchronisation entre le JS et Python, il ne peut pas la
    détecter s'il code en dur ce qu'il vérifie."""
    src = (AI_DIR / BOT / "config.js").read_text()
    m = re.search(r'"%s"\s*:\s*\{\s*"min"\s*:\s*([\d.]+)\s*,'
                  r'\s*"max"\s*:\s*([\d.]+)' % re.escape(behavior), src)
    if not m:
        raise RuntimeError(
            f"bande {behavior!r} introuvable dans personalityList — "
            "AI_BEHAVIOR ne correspond à aucune personnalité de Petra")
    return float(m.group(1)), float(m.group(2))


def personalities():
    """Les tirages du journal du moteur — celui de la partie qui vient
    de se jouer (le moteur repart d'un journal neuf à chaque lancement)."""
    if not GAME_LOG.exists():
        return []
    log = GAME_LOG.read_text(errors="replace")
    out = []
    for blob in _TRACE.findall(log):
        try:
            v = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if "aggressive" in v:
            out.append(v)
    return out


def main():
    games = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(f"AI_BEHAVIOR attendu : {config.AI_BEHAVIOR!r}")
    dest = build_probe_bot()
    pinned = f'this.Config.behavior = "{config.AI_BEHAVIOR}";' in \
        (dest / "_petrabot.js").read_text()
    print(f"épinglage présent dans {BOT}/_petrabot.js : {pinned}")
    if not pinned:
        sys.exit("forkbot n'porte pas l'épinglage — rien à vérifier")

    # Lue AVANT la boucle : le `finally` détruit probebot/, et band()
    # lit son config.js — la lire après rendait le script incapable
    # d'imprimer son verdict, après avoir joué toutes les parties.
    lo, hi = band(config.AI_BEHAVIOR)

    try:
        seen = []
        for i in range(games):
            play(101 + i)
            found = personalities()
            if not found:
                print(f"  partie {i + 1}: aucune trace de personnalité "
                      "(le moteur n'a rien imprimé)")
                continue
            for v in found:
                print(f"  partie {i + 1}: aggressive={v['aggressive']:.3f} "
                      f"defensive={v['defensive']:.3f}")
            seen += found
    finally:
        shutil.rmtree(AI_DIR / BOT, ignore_errors=True)

    if not seen:
        sys.exit("\nAucune personnalité observée : vérification NON "
                 "concluante (la trace n'a pas atteint la sortie).")

    agg = [v["aggressive"] for v in seen]
    dfs = [v["defensive"] for v in seen]
    # defensive = 1 - max + d x (max - min) : même largeur, autre bord.
    dlo, dhi = 1 - hi, 1 - lo
    ok = all(lo - 1e-6 <= a <= hi + 1e-6 for a in agg) and \
        all(dlo - 1e-6 <= d <= dhi + 1e-6 for d in dfs)
    print(f"\n{len(seen)} tirages observés")
    print(f"  aggressive : [{min(agg):.3f}, {max(agg):.3f}]")
    print(f"  defensive  : [{min(dfs):.3f}, {max(dfs):.3f}]")
    print(f"  bandes {config.AI_BEHAVIOR!r} attendues : aggressive "
          f"[{lo}, {hi}], defensive [{dlo:.2f}, {dhi:.2f}]")
    print(f"  jamais > personalityCut.strong (0.7) : {max(agg) <= 0.7}")
    print(f"  jamais < personalityCut.weak (0.3)   : {min(dfs) >= 0.3}")
    print("\nVERDICT :", "épinglage EFFECTIF — les branches discontinues "
          "de Petra sont inatteignables, la politique ne dépend plus du "
          "tirage." if ok else
          "ÉCHEC — des tirages sortent de la bande, l'épinglage n'a pas "
          "pris effet.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
