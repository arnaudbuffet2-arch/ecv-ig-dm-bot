"""
One-off migration : envoie MSG_2 (le nouveau message direct) à tous les
utilisateurs encore coincés dans pending_follow depuis la suppression de
l'étape de vérification d'abonnement (commit 4b2d00d).

Usage:
  python flush_pending_follow.py            # exécution normale
  python flush_pending_follow.py --dry-run  # simulation (aucun DM envoyé)
"""

import argparse
import logging

from instagram_dm_bot import (
    load_config,
    load_state,
    save_state,
    ig_post_url_button,
    MSG_2_TEXT,
    MSG_2_BUTTON_TITLE,
    MSG_2_BUTTON_URL,
)
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser(description="Flush pending_follow -> MSG_2")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")

    cfg   = load_config()
    token = cfg["instagram"]["access_token"]
    ig_id = cfg["instagram"]["ig_user_id"]
    state = load_state()

    pending = dict(state.get("pending_follow", {}))
    logging.info("Utilisateurs en attente : %d", len(pending))

    sent = 0
    for user_id, info in pending.items():
        username = info.get("username", "?")
        result = ig_post_url_button(
            ig_id, user_id, MSG_2_TEXT, MSG_2_BUTTON_TITLE, MSG_2_BUTTON_URL, token, args.dry_run
        )
        if "message_id" in result:
            now_iso = datetime.now(timezone.utc).isoformat()
            state["sent_msg2"][user_id] = now_iso
            state["pending_follow"].pop(user_id, None)
            sent += 1
            logging.info("MSG_2 envoyé → @%s", username)
        else:
            err = result.get("error", result)
            logging.warning("Erreur MSG_2 → @%s : %s", username, err)

    if not args.dry_run:
        save_state(state)
    logging.info("Terminé. MSG_2 envoyés : %d / %d", sent, len(pending))


if __name__ == "__main__":
    main()
