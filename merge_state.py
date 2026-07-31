"""
Fusionne et pousse instagram_dm_bot_state.json sans jamais perdre l'état
d'un run concurrent : au lieu de laisser git faire un merge textuel du
JSON (qui peut échouer en conflit de façon déterministe, sans espoir
qu'un simple retry le résolve), on relit l'état distant à chaque essai
et on fusionne les dictionnaires en mémoire (union par clé), ce qui est
toujours correct puisque chaque run n'ajoute que des clés qui lui sont
propres (un commentaire ou un user_id donné n'est traité que par un
seul run à la fois).

Usage: python merge_state.py <chemin_du_state_local_fraichement_ecrit>
"""

import json
import subprocess
import sys
import time
from pathlib import Path

STATE_FILE = Path("instagram_dm_bot_state.json")
MAX_ATTEMPTS = 5


def run(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check)


def merge(remote: dict, local: dict) -> dict:
    merged = {
        "processed_comments": {**remote.get("processed_comments", {}), **local.get("processed_comments", {})},
        "pending_follow": {**remote.get("pending_follow", {}), **local.get("pending_follow", {})},
        "sent_msg2": {**remote.get("sent_msg2", {}), **local.get("sent_msg2", {})},
    }
    refreshes = [r for r in (remote.get("last_token_refresh"), local.get("last_token_refresh")) if r]
    if refreshes:
        merged["last_token_refresh"] = max(refreshes)
    return merged


def main():
    local_new_path = Path(sys.argv[1])
    local_new = json.loads(local_new_path.read_text(encoding="utf-8"))

    for attempt in range(1, MAX_ATTEMPTS + 1):
        run("git", "fetch", "origin", "main")
        show = run("git", "show", f"origin/main:{STATE_FILE.name}", check=False)
        remote = json.loads(show.stdout) if show.returncode == 0 and show.stdout.strip() else {}

        merged = merge(remote, local_new)
        STATE_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

        run("git", "reset", "--soft", "origin/main")
        run("git", "add", str(STATE_FILE))
        diff = run("git", "diff", "--cached", "--quiet", check=False)
        if diff.returncode == 0:
            print("Rien à committer.")
            return
        run("git", "commit", "-m", "chore: update bot state")

        push = run("git", "push", "origin", "main", check=False)
        if push.returncode == 0:
            print(f"État poussé avec succès (essai {attempt}).")
            return
        print(f"Essai {attempt} : push refusé, on refetch et on refusionne. {push.stderr}")
        time.sleep(2)

    print("::error::Impossible de pousser l'état après plusieurs essais.")
    sys.exit(1)


if __name__ == "__main__":
    main()
