"""
CommentDM — ECV Instagram bot.
Détecte "coaching" dans les commentaires → répond sous le commentaire + envoie directement le lien par DM.

Usage:
  python instagram_dm_bot.py            # exécution normale
  python instagram_dm_bot.py --dry-run  # simulation (aucun DM envoyé)
  python instagram_dm_bot.py --verbose  # logs détaillés
"""

import json
import os
import argparse
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# ── Configuration ──────────────────────────────────────────────────────────────
BASE_URL             = "https://graph.instagram.com/v21.0"
CONFIG_FILE          = Path(__file__).parent / "social_config.json"
STATE_FILE           = Path(__file__).parent / "instagram_dm_bot_state.json"
LOG_FILE             = Path(__file__).parent / "instagram_dm_bot.log"

KEYWORD              = "coaching"
POSTS_LOOKBACK_DAYS  = 7
STATE_RETENTION_DAYS = 30
TOKEN_REFRESH_DAYS   = 50  # Rafraîchir avant les 60 jours d'expiration

# ── Messages ───────────────────────────────────────────────────────────────────
MSG_2_TEXT = (
    "La technique vocale s'installe avec des exercices précis, ciblés, "
    "à travailler régulièrement. Le but est de préparer la voix, mais aussi de la développer. "
    "Ma routine personnelle te donnera un bagage technique nécessaire pour aborder "
    "tous les répertoires et styles musicaux.\n\n"
    "Bonne nouvelle, je te propose une réduction de 30 % cette méthode avec le code « GIFTEMILE »."
)
MSG_2_BUTTON_TITLE = "Voir la méthode 👉"
MSG_2_BUTTON_URL   = "https://emilecoachvocal.com/prépare-ta-voix"

COMMENT_REPLY = "Salut {username} ! 👋 Je t'envoie les infos par DM dans quelques minutes 📩"


# ── State helpers ──────────────────────────────────────────────────────────────
def load_config():
    cfg = {"instagram": {"access_token": "", "ig_user_id": ""}}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    # Env vars override config (pour GitHub Actions)
    if os.environ.get("IG_ACCESS_TOKEN"):
        cfg["instagram"]["access_token"] = os.environ["IG_ACCESS_TOKEN"]
    if os.environ.get("IG_USER_ID"):
        cfg["instagram"]["ig_user_id"] = os.environ["IG_USER_ID"]
    return cfg


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"processed_comments": {}, "sent_msg2": {}}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def purge_old_state(state):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=STATE_RETENTION_DAYS)).isoformat()
    state["processed_comments"] = {
        k: v for k, v in state["processed_comments"].items() if v > cutoff
    }


# ── Token refresh ──────────────────────────────────────────────────────────────
def refresh_token_if_needed(token, state):
    """Rafraîchit le token Instagram si proche de l'expiration (tous les 50 jours)."""
    last_refresh = state.get("last_token_refresh")
    if last_refresh:
        days_since = (datetime.now(timezone.utc) - _parse_time(last_refresh)).days
        if days_since < TOKEN_REFRESH_DAYS:
            return token

    resp = requests.get(
        "https://graph.instagram.com/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": token},
        timeout=15,
    )
    data = resp.json()
    if "access_token" not in data:
        logging.warning("Impossible de rafraîchir le token : %s", data.get("error"))
        return token

    new_token = data["access_token"]
    state["last_token_refresh"] = datetime.now(timezone.utc).isoformat()
    logging.info("Token Instagram rafraîchi (valide 60 jours supplémentaires)")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"new_token={new_token}\n")

    return new_token


# ── API helpers ────────────────────────────────────────────────────────────────
def _parse_time(ts: str) -> datetime:
    normalized = ts.replace("Z", "+00:00").replace("+0000", "+00:00")
    return datetime.fromisoformat(normalized)


def ig_post_msg(ig_id, recipient_id, text, token, dry_run=False):
    if dry_run:
        logging.info("[DRY RUN] DM → %s : %.60s…", recipient_id, text)
        return {"message_id": "dry_run"}
    message = {"text": text}
    resp = requests.post(
        f"{BASE_URL}/{ig_id}/messages",
        json={"recipient": {"id": recipient_id}, "message": message},
        params={"access_token": token},
        timeout=15,
    )
    return resp.json()


def ig_post_url_button(ig_id, recipient_id, text, button_title, button_url, token, dry_run=False):
    """Envoie un message avec un bouton URL (template button)."""
    if dry_run:
        logging.info("[DRY RUN] DM button → %s : %s", recipient_id, button_url)
        return {"message_id": "dry_run"}
    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "attachment": {
                "type": "template",
                "payload": {
                    "template_type": "button",
                    "text": text,
                    "buttons": [
                        {"type": "web_url", "url": button_url, "title": button_title}
                    ],
                },
            }
        },
    }
    resp = requests.post(
        f"{BASE_URL}/{ig_id}/messages",
        json=payload,
        params={"access_token": token},
        timeout=15,
    )
    result = resp.json()
    if "error" in result:
        logging.warning("Template button non supporté, envoi texte seul : %s", result.get("error", {}).get("message"))
        return ig_post_msg(ig_id, recipient_id, f"{text}\n\n👉 {button_url}", token, dry_run)
    return result


def ig_reply_comment(comment_id, text, token, dry_run=False):
    """Répond publiquement à un commentaire Instagram."""
    if dry_run:
        logging.info("[DRY RUN] Réponse commentaire %s : %.60s…", comment_id, text)
        return {"id": "dry_run"}
    resp = requests.post(
        f"{BASE_URL}/{comment_id}/replies",
        params={"message": text, "access_token": token},
        timeout=15,
    )
    return resp.json()


# ── Data fetching ──────────────────────────────────────────────────────────────
def get_recent_posts(ig_id, token):
    """Retourne les IDs des posts publiés dans les POSTS_LOOKBACK_DAYS derniers jours."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=POSTS_LOOKBACK_DAYS)
    post_ids = []
    url = f"{BASE_URL}/{ig_id}/media"
    params = {"fields": "id,timestamp", "limit": 25, "access_token": token}

    while url:
        resp = requests.get(url, params=params, timeout=15).json()
        params = {}  # les pages suivantes ont les params dans l'URL
        for post in resp.get("data", []):
            if _parse_time(post["timestamp"]) < cutoff:
                return post_ids  # posts en ordre anti-chron, on peut s'arrêter
            post_ids.append(post["id"])
        url = resp.get("paging", {}).get("next")

    return post_ids


def get_comments(media_id, token):
    """Retourne tous les commentaires d'un post (avec pagination)."""
    comments = []
    url = f"{BASE_URL}/{media_id}/comments"
    params = {"fields": "id,text,from,timestamp", "limit": 50, "access_token": token}

    while url:
        resp = requests.get(url, params=params, timeout=15).json()
        params = {}
        comments.extend(resp.get("data", []))
        url = resp.get("paging", {}).get("next")

    return comments


# ── Bot logic ──────────────────────────────────────────────────────────────────
def scan_comments(ig_id, token, state, dry_run):
    """
    Parcourt les commentaires récents, détecte le mot-clé "coaching"
    et envoie directement MSG_2 (le lien) aux nouveaux commentateurs.
    """
    count = 0
    posts = get_recent_posts(ig_id, token)
    logging.info("Posts récents à scanner : %d", len(posts))

    for post_id in posts:
        for comment in get_comments(post_id, token):
            cid = comment.get("id", "")
            logging.debug("Commentaire brut : %s", comment)
            if cid in state["processed_comments"]:
                continue

            now_iso = datetime.now(timezone.utc).isoformat()

            if KEYWORD not in comment.get("text", "").lower():
                state["processed_comments"][cid] = now_iso
                continue

            logging.debug("Mot-clé détecté dans : %s", comment)
            frm      = comment.get("from") or {}
            user_id  = frm.get("id")
            username = frm.get("username", "toi")

            if not user_id:
                logging.warning("Commentaire sans user_id, sera retenté au prochain run : %s", cid)
                logging.warning("Objet from complet : %s", frm)
                continue
            if user_id == ig_id:
                logging.debug("Commentaire du compte ECV lui-même, ignoré")
                state["processed_comments"][cid] = now_iso
                continue

            reply = ig_reply_comment(cid, COMMENT_REPLY.format(username=username), token, dry_run)
            if "id" in reply:
                logging.info("Réponse commentaire → @%s", username)
            else:
                logging.warning("Erreur réponse commentaire → @%s : %s", username, reply.get("error", reply))

            result = ig_post_url_button(
                ig_id, user_id, MSG_2_TEXT, MSG_2_BUTTON_TITLE, MSG_2_BUTTON_URL, token, dry_run
            )
            state["processed_comments"][cid] = now_iso
            if "message_id" in result:
                state["sent_msg2"][user_id] = now_iso
                count += 1
                logging.info("MSG_2 envoyé → @%s", username)
            else:
                err = result.get("error", result)
                logging.warning("Erreur MSG_2 → @%s : %s", username, err)

    return count


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Instagram DM Bot ECV")
    parser.add_argument("--dry-run", action="store_true", help="Simulation sans envoi de DM")
    parser.add_argument("--verbose", action="store_true", help="Logs détaillés")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )

    cfg   = load_config()
    token = cfg["instagram"]["access_token"]
    ig_id = cfg["instagram"]["ig_user_id"]
    state = load_state()

    purge_old_state(state)
    if not args.dry_run:
        token = refresh_token_if_needed(token, state)

    if args.dry_run:
        logging.info("=== MODE DRY RUN — aucun DM ne sera envoyé ===")

    n2 = scan_comments(ig_id, token, state, args.dry_run)

    if not args.dry_run:
        save_state(state)
    logging.info("Terminé. MSG2 envoyés : %d", n2)


if __name__ == "__main__":
    main()
