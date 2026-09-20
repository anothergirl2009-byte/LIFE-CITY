"""
LifeCity Bot - /topactif
Classement des joueurs les plus actifs (par nombre de commandes utilisées).
"""

from telegram import Update
from telegram.ext import ContextTypes

from db import get_conn


async def topactif(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /topactif — Affiche le Top 15 des joueurs les plus actifs du serveur.
    Basé sur le compteur cmd_count (incrémenté à chaque commande utilisée).
    """
    SEP = "⚡━━━━━━━━━━━━━━━━━━━━━━⚡"

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT first_name, username, user_id, cmd_count
               FROM players
               WHERE banned = 0 AND cmd_count > 0
               ORDER BY cmd_count DESC
               LIMIT 15"""
        ).fetchall()

    if not rows:
        await update.message.reply_text(
            f"{SEP}\n"
            "🏃 *TOP ACTIFS — LIFECITY*\n"
            f"{SEP}\n\n"
            "_Aucun joueur actif pour le moment._\n\n"
            f"{SEP}",
            parse_mode="Markdown"
        )
        return

    RANGS = {
        1: "🥇", 2: "🥈", 3: "🥉",
        4: "4️⃣", 5: "5️⃣", 6: "6️⃣", 7: "7️⃣",
        8: "8️⃣", 9: "9️⃣", 10: "🔟",
    }

    TITRES = {
        1: "⚡ Légende Active",
        2: "🔥 Super Actif",
        3: "💪 Très Actif",
        4: "🌟 Actif",
        5: "✨ Régulier",
    }

    lignes = [
        SEP,
        "⚡ *TOP ACTIFS — LIFECITY* ⚡",
        "🏃 *Les joueurs qui font vivre la ville !*",
        SEP,
        "",
    ]

    total_cmds = sum(r["cmd_count"] for r in rows)

    for i, row in enumerate(rows, start=1):
        rang = RANGS.get(i, "🏅")
        titre = TITRES.get(i, "🎯 Joueur Actif")
        name = row["first_name"] or row["username"] or f"Joueur #{row['user_id']}"
        cmds = row["cmd_count"]
        pct = (cmds / total_cmds * 100) if total_cmds else 0

        # Barre de progression visuelle (max 10 blocs)
        blocs = round(pct / 10)
        barre = "█" * blocs + "░" * (10 - blocs)

        lignes.append(f"{rang} *{name}*")
        lignes.append(f"   ✦ {titre}")
        lignes.append(f"   💬 Commandes : *{cmds:,}*")
        lignes.append(f"   📊 `{barre}` {pct:.1f}%")

        if i < len(rows):
            lignes.append("   ─────────────────────────")

    lignes += [
        "",
        SEP,
        f"📊 *Total commandes comptabilisées :* {total_cmds:,}",
        f"👥 *{len(rows)} joueurs* dans ce classement",
        SEP,
        "",
        "⚡ _Plus tu joues, plus tu grimpes ! Utilise /work, /daily et plus encore._",
        "📡 *LifeCity Bot News* ❤️ — Classement en temps réel",
    ]

    await update.message.reply_text(
        "\n".join(lignes),
        parse_mode="Markdown"
    )
