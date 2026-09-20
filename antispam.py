"""
LifeCity Bot - Anti-spam global
by ANOTHERGIRL

Rate-limiter à fenêtre glissante : au-delà de MAX_IN_WINDOW commandes en
WINDOW secondes (peu importe LESQUELLES : /me, /acc, alterner entre plein
de commandes différentes compte pareil), le joueur prend une pénalité de
blocage total de BASE_PENALTY secondes, qui grimpe à chaque récidive
jusqu'à MAX_PENALTY. Contrairement à une v1 avec cooldown simple, la
pénalité ne redescend PAS après une seule commande "propre" — il faut
STRIKE_DECAY_AFTER secondes sans aucune récidive pour repartir à zéro.
"""

import time

from telegram import Update
from telegram.ext import ContextTypes, ApplicationHandlerStop

from config import OWNER_ID

WINDOW = 5.0                # fenêtre glissante d'observation (secondes)
MAX_IN_WINDOW = 3           # nb de commandes max tolérées dans la fenêtre
BASE_PENALTY = 5.0          # durée du 1er blocage
PENALTY_STEP = 5.0          # pénalité ajoutée à chaque récidive
MAX_PENALTY = 60.0          # plafond du blocage
STRIKE_DECAY_AFTER = 120.0  # sans récidive pendant ce délai, on efface l'ardoise
WARN_INTERVAL = 3.0         # n'avertit pas plus d'une fois toutes les 3s

# Commandes totalement exemptées de l'anti-spam : elles ne comptent pas dans
# la fenêtre glissante et ne sont jamais bloquées, quel que soit le rythme.
EXEMPT_COMMANDS = {"candidats"}

# user_id -> {"timestamps": [...], "strikes": int,
#             "blocked_until": ts, "last_strike": ts, "last_warn": ts}
_etat: dict[int, dict] = {}


def _get_etat(user_id: int) -> dict:
    return _etat.setdefault(user_id, {
        "timestamps": [],
        "strikes": 0,
        "blocked_until": 0.0,
        "last_strike": 0.0,
        "last_warn": 0.0,
    })


async def anti_spam_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler global (group=-5, avant tout le reste). Bloque le flood en
    comptant les commandes sur une fenêtre glissante, indépendamment de
    quelle(s) commande(s) sont utilisées."""
    user = update.effective_user
    if user is None:
        return

    if user.id == OWNER_ID:
        return

    message = update.message
    if message is None or not message.text or not message.text.startswith("/"):
        return  # ne gate que les vraies commandes

    # Ex : "/candidats@LifeCityBot arg1" -> "candidats"
    command = message.text[1:].split()[0].split("@")[0].lower()
    if command in EXEMPT_COMMANDS:
        return  # commande exemptée : ni comptée, ni bloquée

    now = time.time()
    etat = _get_etat(user.id)

    # Déjà en pénalité : on bloque tout, sans même regarder la fenêtre.
    if now < etat["blocked_until"]:
        if now - etat["last_warn"] > WARN_INTERVAL:
            etat["last_warn"] = now
            restant = etat["blocked_until"] - now
            await message.reply_text(
                f"🐢 Trop de spam ! Attends encore {restant:.0f}s. idiot la"
            )
        raise ApplicationHandlerStop

    # Aucune récidive depuis longtemps : on efface l'ardoise.
    if now - etat["last_strike"] > STRIKE_DECAY_AFTER:
        etat["strikes"] = 0

    # Purge des timestamps hors fenêtre.
    etat["timestamps"] = [t for t in etat["timestamps"] if now - t < WINDOW]

    if len(etat["timestamps"]) >= MAX_IN_WINDOW:
        etat["strikes"] += 1
        etat["last_strike"] = now
        penalite = min(BASE_PENALTY + PENALTY_STEP * (etat["strikes"] - 1), MAX_PENALTY)
        etat["blocked_until"] = now + penalite
        etat["timestamps"] = []

        etat["last_warn"] = now
        await message.reply_text(
            f"🐢 Trop de spam ! Blocage de {penalite:.0f}s. tu veux foutre mon bot ou quoi"
        )
        raise ApplicationHandlerStop

    etat["timestamps"].append(now)
    # Commande acceptée, rien à faire de plus : le handler suivant s'exécute.
