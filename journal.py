"""
LifeCity Bot - Journal quotidien automatique
Tracke tous les événements du jour (mariages, divorces, adoptions, désaveux)
et les envoie dans le groupe à 21h00 avec le design LifeCity Bot News.

À ajouter dans db.py :
    - La table events_log (voir init_events_log ci-dessous)
    - Les fonctions log_event() et get_events_since()
"""

import time
import asyncio
from datetime import datetime

from utils import fmt_money
from db import get_conn

# ── Jours en français ─────────────────────────────────────────────────────────
JOURS_FR   = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
MOIS_FR    = ["janvier","février","mars","avril","mai","juin",
               "juillet","août","septembre","octobre","novembre","décembre"]


def date_fr() -> str:
    """Retourne la date du jour en français, ex : Dimanche 28 juin 2026"""
    now = datetime.now()
    return f"{JOURS_FR[now.weekday()]} {now.day} {MOIS_FR[now.month - 1]} {now.year}"


def heure_fr() -> str:
    """Retourne l'heure actuelle formatée, ex : 21h00"""
    now = datetime.now()
    return f"{now.hour:02d}h{now.minute:02d}"


# ═══════════════════════════════════════════════════════════════════════════════
# INITIALISATION DE LA TABLE (à appeler dans init_db() de db.py)
# ═══════════════════════════════════════════════════════════════════════════════

def init_events_log() -> None:
    """
    Crée la table events_log si elle n'existe pas.
    À appeler UNE FOIS depuis init_db() dans db.py :

        from journal import init_events_log
        init_events_log()
    """
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events_log (
                event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT    NOT NULL,
                user1_id   INTEGER NOT NULL,
                user2_id   INTEGER,
                user1_name TEXT,
                user2_name TEXT,
                amount     INTEGER,
                detail     TEXT,
                created_at INTEGER NOT NULL
            )
        """)
        # Migration douce : si la table existait déjà sans ces colonnes
        # (anciennes installations), on les ajoute.
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(events_log)")}
        if "amount" not in cols:
            conn.execute("ALTER TABLE events_log ADD COLUMN amount INTEGER")
        if "detail" not in cols:
            conn.execute("ALTER TABLE events_log ADD COLUMN detail TEXT")


# ═══════════════════════════════════════════════════════════════════════════════
# FONCTIONS DE LOGGING — à appeler depuis family.py
# ═══════════════════════════════════════════════════════════════════════════════

def log_event(event_type: str, user1_id: int, user1_name: str,
               user2_id: int = None, user2_name: str = None,
               amount: int = None, detail: str = None) -> None:
    """
    Enregistre un événement (familial ou de jeu).
    Types : 'mariage', 'divorce', 'adoption', 'desaveu',
            'gros_gain' (gros gain à un jeu), 'vol_massif' (gros vol réussi)

    Exemples d'appel depuis family.py :
        log_event("mariage",   user.id, user.first_name, target.id, target.first_name)
        log_event("divorce",   user.id, user.first_name, spouse_id, spouse_name)
        log_event("adoption",  user.id, user.first_name, target.id, target.first_name)
        log_event("desaveu",   user.id, user.first_name, target.id, target.first_name)

    Exemples d'appel depuis les jeux (casino, crime) :
        log_event("gros_gain", user.id, user.first_name, amount=1_000_000_000, detail="Roue de la Fortune")
        log_event("vol_massif", thief.id, thief.first_name, victim.id, victim.first_name, amount=500_000_000)
    """
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO events_log
               (event_type, user1_id, user2_id, user1_name, user2_name, amount, detail, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_type, user1_id, user2_id, user1_name, user2_name, amount, detail, int(time.time()))
        )


# ── Gros gains aux jeux (casino solo/pvp, crime) ──────────────────────────────
# Seuil par défaut au-delà duquel un gain à un jeu est considéré comme
# "gros gain" et publié dans le journal. Ajustable au cas par cas via le
# paramètre `threshold` des fonctions ci-dessous.
GROS_GAIN_THRESHOLD = 50_000_000  # 50M€


def log_big_win(game_label: str, user_id: int, user_name: str,
                 amount: int, threshold: int = None) -> None:
    """
    À appeler après un gain à un jeu (casino solo/pvp...). N'enregistre
    l'événement que si `amount` dépasse le seuil (GROS_GAIN_THRESHOLD par
    défaut, ou `threshold` si fourni).

    Exemple :
        log_big_win("Roue de la Fortune", user.id, user.first_name, winnings)
    """
    seuil = threshold if threshold is not None else GROS_GAIN_THRESHOLD
    if amount >= seuil:
        log_event("gros_gain", user_id, user_name, amount=amount, detail=game_label)


def log_big_theft(thief_id: int, thief_name: str, victim_id: int, victim_name: str,
                   amount: int, threshold: int = None) -> None:
    """
    À appeler après un vol réussi (crime.py /steal). N'enregistre l'événement
    que si `amount` dépasse le seuil (GROS_GAIN_THRESHOLD par défaut).
    """
    seuil = threshold if threshold is not None else GROS_GAIN_THRESHOLD
    if amount >= seuil:
        log_event("vol_massif", thief_id, thief_name, victim_id, victim_name, amount=amount)


def get_events_since(timestamp: int) -> list:
    """Récupère tous les événements depuis un timestamp donné."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM events_log WHERE created_at >= ? ORDER BY created_at ASC",
            (timestamp,)
        ).fetchall()
    return rows


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTRUCTION DU MESSAGE JOURNAL
# ═══════════════════════════════════════════════════════════════════════════════

def _ligne_evenement(row) -> str:
    """Formate une ligne d'événement selon son type."""
    u1 = row["user1_name"] or "Quelqu'un"
    u2 = row["user2_name"] or "Quelqu'un"

    if row["event_type"] == "mariage":
        return (
            f"💍 *{u1}* et *{u2}* se sont mariés aujourd'hui ! "
            f"La rédaction Family Bot News ❤️ leur souhaite beaucoup de bonheur."
        )
    elif row["event_type"] == "divorce":
        return (
            f"💔 *{u1}* et *{u2}* ont divorcé. Une page se tourne..."
        )
    elif row["event_type"] == "adoption":
        return (
            f"👶 *{u1}* a officiellement adopté *{u2}*. Bienvenue dans la famille !"
        )
    elif row["event_type"] == "desaveu":
        return (
            f"😢 *{u1}* a désavoué *{u2}*. La famille se déchire..."
        )
    elif row["event_type"] == "gros_gain":
        montant = fmt_money(row["amount"] or 0)
        jeu = row["detail"] or "un jeu"
        return (
            f"🎰💰 *{u1}* a raflé *{montant}* à {jeu} ! Quelle veine !"
        )
    elif row["event_type"] == "vol_massif":
        montant = fmt_money(row["amount"] or 0)
        return (
            f"🦹💸 *{u1}* a dérobé *{montant}* à *{u2}* dans un braquage spectaculaire !"
        )
    return ""


async def construire_journal(heure: str = None) -> str:
    """
    Construit le message complet du journal quotidien.
    Récupère les événements des 24 dernières heures.
    """
    maintenant = int(time.time())
    debut = maintenant - 86400  # 24h

    events = get_events_since(debut)

    # ── Stats générales ───────────────────────────────────────────────────────
    with get_conn() as conn:
        nb_actifs = conn.execute(
            "SELECT COUNT(*) FROM players WHERE banned = 0"
        ).fetchone()[0]

        nb_events_total = conn.execute(
            "SELECT COUNT(*) FROM events_log WHERE created_at >= ?", (debut,)
        ).fetchone()[0]

        # Transactions du jour
        nb_tx = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE created_at >= ?", (debut,)
        ).fetchone()[0]

        # Leader #1
        leader = conn.execute(
            """SELECT first_name, username, user_id,
                      (balance + bank_balance) AS total
               FROM players WHERE banned = 0
               ORDER BY total DESC LIMIT 1"""
        ).fetchone()

    # ── Formatage leader ──────────────────────────────────────────────────────
    if leader:
        nom_leader = leader["first_name"] or leader["username"] or f"#{leader['user_id']}"
        total = leader["total"]
        # Format milliards / millions
        if total >= 1_000_000_000:
            fortune_str = f"{total / 1_000_000_000:.1f}B €"
        elif total >= 1_000_000:
            fortune_str = f"{total / 1_000_000:.1f}M $"
        else:
            fortune_str = fmt_money(total)
        leader_line = f"👑 Leader actuel : *{nom_leader}* — *{fortune_str}*"
    else:
        leader_line = "👑 Leader actuel : _Aucun joueur enregistré_"

    # ── Lignes d'événements ───────────────────────────────────────────────────
    lignes_events = [_ligne_evenement(e) for e in events if _ligne_evenement(e)]

    heure_affichee = heure or heure_fr()

    # ── Assemblage du message ─────────────────────────────────────────────────
    separateur = "━━━━━━━━━━━━━━━━━━━━━━━"

    lignes = [
        f"🚨 *ALERTE INFO — 📰 LE JOURNAL DU JOUR*",
        f"🗓️ *{date_fr()} | {heure_affichee}*",
        separateur,
    ]

    if lignes_events:
        lignes += lignes_events
    else:
        lignes.append("_Aucun événement familial aujourd'hui._")

    lignes += [
        separateur,
        leader_line,
        f"👥 Joueurs actifs : *{nb_actifs}*",
        f"📊 Événements aujourd'hui : *{nb_events_total + nb_tx}*",
        separateur,
        "📺 Restez connectés pour la suite des événements.",
        "📡 *LifeCity Bot News* ❤️ — Votre source exclusive",
    ]

    return "\n".join(lignes)


# ═══════════════════════════════════════════════════════════════════════════════
# TÂCHE PLANIFIÉE — appelée depuis main.py à 21h
# ═══════════════════════════════════════════════════════════════════════════════

async def envoyer_journal(context) -> None:
    """
    Tâche planifiée run_daily() à 21h00.
    Envoie le journal dans tous les groupes actifs où le bot est présent.
    """
    import logging
    logger = logging.getLogger(__name__)

    from db import get_active_groups

    groupes = get_active_groups()

    if not groupes:
        logger.warning("⚠️ Aucun groupe actif enregistré — journal non envoyé.")
        return

    message = await construire_journal(heure="21h00")
    nb_ok = 0
    nb_err = 0

    for groupe in groupes:
        try:
            await context.bot.send_message(
                chat_id=groupe["chat_id"],
                text=message,
                parse_mode="Markdown"
            )
            nb_ok += 1
        except Exception as e:
            logger.warning("❌ Impossible d'envoyer dans %s (%s) : %s", groupe["chat_title"], groupe["chat_id"], e)
            nb_err += 1
        await asyncio.sleep(0.1)  # anti flood-control Telegram

    logger.info("✅ Journal quotidien envoyé : %d groupes OK, %d erreurs.", nb_ok, nb_err)
