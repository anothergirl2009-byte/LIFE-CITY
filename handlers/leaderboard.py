"""
LifeCity Bot - /leaderboard
Affiche le classement royal des familles par NOMBRE DE MEMBRES.
Titres de rang : Royale (25+), Impériale (15+), Noble (10+), Honorée (5+).
En cas d'égalité de membres, tri alphabétique sur le nom de famille.
"""

from telegram import Update
from telegram.ext import ContextTypes

from db import get_conn
from utils import fmt_money


def _fmt_fortune(total: int) -> str:
    """Formate la fortune en B€ ou M€ pour un rendu propre."""
    total = total or 0
    if total >= 1_000_000_000:
        return f"{total / 1_000_000_000:.2f}B €"
    elif total >= 1_000_000:
        return f"{total / 1_000_000:.1f}M €"
    return fmt_money(total)


def _get_titre(nb_membres: int) -> tuple[str, str]:
    """
    Retourne (emoji, titre) en fonction du nombre de membres de la famille.
    Seuils : 25+ Royale, 15+ Impériale, 10+ Noble, 5+ Honorée.
    """
    if nb_membres >= 25:
        return "🏰", "Famille Royale"
    elif nb_membres >= 15:
        return "⚜️", "Famille Impériale"
    elif nb_membres >= 10:
        return "🛡️", "Famille Noble"
    elif nb_membres >= 5:
        return "🎖️", "Famille Honorée"
    return "🌱", "Famille"


def _get_family_leaderboard(limit: int = 10) -> list[dict]:
    """
    Calcule le classement des familles par NOMBRE DE MEMBRES (ordre principal).
    En cas d'égalité, tri alphabétique sur family_name (insensible à la casse).
    Une famille = tous les joueurs partageant le même family_name.
    Les joueurs sans nom de famille sont ignorés.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                family_name,
                COUNT(*)                        AS nb_membres,
                SUM(balance + bank_balance)     AS fortune_totale,
                MAX(balance + bank_balance)     AS fortune_max,
                GROUP_CONCAT(first_name, ', ')  AS membres
            FROM players
            WHERE banned = 0
              AND family_name IS NOT NULL
              AND family_name != ''
            GROUP BY family_name
            ORDER BY nb_membres DESC, family_name COLLATE NOCASE ASC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /leaderboard — Classement royal des familles LifeCity.
    Affiche le Top 10 des familles par NOMBRE DE MEMBRES avec design majestueux.
    """
    familles = _get_family_leaderboard(limit=10)

    SEP  = "👑━━━━━━━━━━━━━━━━━━━━━━👑"
    SEP2 = "─────────────────────────"

    if not familles:
        await update.message.reply_text(
            f"{SEP}\n"
            "🏰 *CLASSEMENT ROYAL DES FAMILLES*\n"
            f"{SEP}\n\n"
            "_Aucune famille enregistrée pour le moment._\n"
            "Utilise /setfamilyname pour créer ta famille !\n\n"
            f"{SEP}",
            parse_mode="Markdown"
        )
        return

    # ── Médailles de position ───────────────────────────────────────────────
    RANGS = {
        1: "👑",
        2: "🥈",
        3: "🥉",
        4: "4️⃣",
        5: "5️⃣",
        6: "6️⃣",
        7: "7️⃣",
        8: "8️⃣",
        9: "9️⃣",
        10: "🔟",
    }

    # ── Construction du message ───────────────────────────────────────────────
    lignes = [
        f"{SEP}",
        f"🏰 *CLASSEMENT ROYAL DES FAMILLES* 🏰",
        f"👑 *LifeCity — Dynasties & Effectifs* 👑",
        f"{SEP}",
        "",
    ]

    for i, fam in enumerate(familles, start=1):
        rang_emoji       = RANGS.get(i, "🏅")
        nb               = fam["nb_membres"]
        titre_emoji, titre = _get_titre(nb)
        fortune          = _fmt_fortune(fam["fortune_totale"])
        nom              = fam["family_name"]

        # Ligne de rang
        lignes.append(f"{rang_emoji} *Famille {nom}*")
        lignes.append(f"   {titre_emoji} {titre}")
        lignes.append(f"   👥 Membres : *{nb}*")
        lignes.append(f"   💰 Fortune : *{fortune}*")

        # Membres (tronqués si trop long)
        membres_raw = fam.get("membres", "") or ""
        membres_list = membres_raw.split(", ")
        if len(membres_list) > 4:
            membres_affich = ", ".join(membres_list[:4]) + f" +{len(membres_list) - 4}"
        else:
            membres_affich = membres_raw
        lignes.append(f"   👤 _{membres_affich}_")

        # Séparateur entre familles (sauf le dernier)
        if i < len(familles):
            lignes.append(f"   {SEP2}")

    # ── Footer ────────────────────────────────────────────────────────────────
    total_membres = sum(f["nb_membres"] for f in familles)

    lignes += [
        "",
        f"{SEP}",
        f"👥 *Total des membres classés :* {total_membres}",
        f"🏆 *{len(familles)} familles* se disputent le trône de LifeCity !",
        f"{SEP}",
        "",
        "👑 _25+ membres = Royale · 15+ = Impériale · 10+ = Noble · 5+ = Honorée_",
        "👑 _Utilise /setfamilyname pour rejoindre la noblesse !_",
        "📡 *LifeCity Bot News* ❤️ — Classement mis à jour en temps réel",
    ]

    await update.message.reply_text(
        "\n".join(lignes),
        parse_mode="Markdown"
    )
