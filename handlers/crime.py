"""
LifeCity Bot - Crime
/steal @user — Voler un joueur
/police — Porter plainte contre le dernier voleur
/bail — Payer sa caution pour sortir de prison
/juge — Passer devant le juge
/security niveau — Améliorer son niveau de sécurité
"""

import random
import time

from telegram import Update
from telegram.ext import ContextTypes

from config import (
    CRIME_SUCCESS_BASE_RATE, CRIME_PRISON_MIN_MINUTES, CRIME_PRISON_MAX_MINUTES,
    CRIME_BAIL_MULTIPLIER, THEFT_MIN_PERCENT, THEFT_MAX_PERCENT,
    SECURITY_LEVELS, LAWSUIT_FINE_MIN, LAWSUIT_FINE_MAX,
)
from db import (
    get_or_create_player, get_player_by_name_or_id, update_player,
    add_balance, log_transaction, get_conn,
)
from utils import fmt_money, fmt_duration

try:
    from handlers.journal import log_big_theft
except ImportError:
    from journal import log_big_theft


def _is_in_jail(player) -> bool:
    return player["jail_until"] > int(time.time())


async def steal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Voler un autre joueur."""
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if _is_in_jail(player):
        remaining = player["jail_until"] - int(time.time())
        await update.message.reply_text(
            f"🔒 Tu es en prison ! Libéré dans {fmt_duration(remaining)}.\n"
            f"Utilise /bail pour payer ta caution ou /juge pour un jugement."
        )
        return

    # Identifier la cible
    target = None
    if update.message.reply_to_message:
        tu = update.message.reply_to_message.from_user
        target = get_or_create_player(tu.id, tu.username, tu.first_name)
    elif context.args:
        target = get_player_by_name_or_id(context.args[0])

    if target is None:
        await update.message.reply_text(
            "❌ Cible introuvable. Réponds au message du joueur ou utilise /steal @username."
        )
        return

    if target["user_id"] == user.id:
        await update.message.reply_text("❌ Tu ne peux pas te voler toi-même.")
        return

    if target["balance"] <= 0:
        await update.message.reply_text("❌ Cette personne n'a pas d'argent liquide à voler.")
        return

    # Calcul du succès
    protection = SECURITY_LEVELS.get(target["security_level"], {}).get("protection", 0.0)
    success_rate = max(0.05, CRIME_SUCCESS_BASE_RATE - protection)
    success = random.random() < success_rate

    now = int(time.time())

    if success:
        pct = random.uniform(THEFT_MIN_PERCENT, THEFT_MAX_PERCENT)
        amount = max(1, int(target["balance"] * pct))
        amount = min(amount, target["balance"])

        add_balance(user.id, amount)
        add_balance(target["user_id"], -amount)
        log_transaction(target["user_id"], user.id, amount, "theft")
        log_big_theft(user.id, player["first_name"] or user.first_name,
                       target["user_id"], target["first_name"], amount)

        # Enregistrer le vol pour /police
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO thefts (thief_id, victim_id, amount, success, reported, created_at)
                   VALUES (?, ?, ?, 1, 0, ?)""",
                (user.id, target["user_id"], amount, now)
            )

        await update.message.reply_text(
            f"🦹 Vol réussi ! Tu as dérobé {fmt_money(amount)} à {target['first_name']} !\n"
            f"(Protection de la cible : {int(protection * 100)}%)"
        )
    else:
        # Échec → prison
        jail_minutes = random.randint(CRIME_PRISON_MIN_MINUTES, CRIME_PRISON_MAX_MINUTES)
        jail_until = now + jail_minutes * 60
        update_player(user.id, jail_until=jail_until)

        # Enregistrer tentative
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO thefts (thief_id, victim_id, amount, success, reported, created_at)
                   VALUES (?, ?, 0, 0, 0, ?)""",
                (user.id, target["user_id"], now)
            )

        await update.message.reply_text(
            f"🚔 Tu t'es fait attraper ! Tu es en prison pour {jail_minutes} minutes.\n"
            f"Utilise /bail pour payer ta caution ou /juge pour un jugement."
        )


async def police(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Porter plainte contre le dernier voleur."""
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)

    with get_conn() as conn:
        theft = conn.execute(
            """SELECT * FROM thefts WHERE victim_id = ? AND success = 1 AND reported = 0
               ORDER BY created_at DESC LIMIT 1""",
            (user.id,)
        ).fetchone()

    if theft is None:
        await update.message.reply_text(
            "❌ Aucun vol récent à signaler (ou tu as déjà porté plainte)."
        )
        return

    thief_row = get_player_by_name_or_id(str(theft["thief_id"]))
    thief_name = thief_row["first_name"] if thief_row else f"#{theft['thief_id']}"

    fine = random.randint(LAWSUIT_FINE_MIN, LAWSUIT_FINE_MAX)

    with get_conn() as conn:
        conn.execute(
            "UPDATE thefts SET reported = 1 WHERE theft_id = ?", (theft["theft_id"],)
        )
        # Mettre le voleur en prison
        jail_minutes = random.randint(30, 120)
        jail_until = int(time.time()) + jail_minutes * 60
        if thief_row:
            conn.execute(
                "UPDATE players SET jail_until = ? WHERE user_id = ?",
                (jail_until, thief_row["user_id"])
            )
            # Amende reversée à la victime
            fine_applied = min(fine, thief_row["balance"])
            add_balance(thief_row["user_id"], -fine_applied)
            add_balance(user.id, fine_applied)
            log_transaction(thief_row["user_id"], user.id, fine_applied, "police_fine")

    await update.message.reply_text(
        f"⚖️ Plainte déposée contre *{thief_name}* !\n"
        f"Il/elle est arrêté(e) pour {jail_minutes} minutes\n"
        f"et devra payer une amende de {fmt_money(fine)}.",
        parse_mode="Markdown"
    )


async def bail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Payer sa caution pour sortir de prison."""
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if not _is_in_jail(player):
        await update.message.reply_text("✅ Tu n'es pas en prison.")
        return

    remaining_seconds = player["jail_until"] - int(time.time())
    remaining_minutes = remaining_seconds / 60
    bail_cost = max(1000, int(remaining_minutes * CRIME_BAIL_MULTIPLIER * 1000))

    if player["balance"] < bail_cost:
        await update.message.reply_text(
            f"❌ Caution : {fmt_money(bail_cost)}\n"
            f"Ton solde : {fmt_money(player['balance'])}\n"
            f"Tu n'as pas assez. Utilise /juge pour un jugement ou attend {fmt_duration(remaining_seconds)}."
        )
        return

    add_balance(user.id, -bail_cost)
    update_player(user.id, jail_until=0)
    log_transaction(user.id, None, bail_cost, "bail")

    await update.message.reply_text(
        f"🔓 Tu as payé ta caution : {fmt_money(bail_cost)}.\nTu es libre !"
    )


async def juge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Passer devant le juge (verdict aléatoire)."""
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if not _is_in_jail(player):
        await update.message.reply_text("✅ Tu n'es pas en prison.")
        return

    verdict = random.random()

    if verdict < 0.30:
        # Relaxé
        update_player(user.id, jail_until=0)
        await update.message.reply_text(
            "⚖️ Le juge t'a déclaré *innocent* ! Tu es libre immédiatement ! 🎉",
            parse_mode="Markdown"
        )
    elif verdict < 0.60:
        # Peine réduite de moitié
        new_jail = int(time.time()) + (player["jail_until"] - int(time.time())) // 2
        update_player(user.id, jail_until=new_jail)
        remaining = new_jail - int(time.time())
        await update.message.reply_text(
            f"⚖️ Le juge a réduit ta peine de moitié.\n"
            f"Tu seras libéré dans {fmt_duration(remaining)}.",
            parse_mode="Markdown"
        )
    else:
        # Peine prolongée + amende
        extra = random.randint(30, 120) * 60
        update_player(user.id, jail_until=player["jail_until"] + extra)
        fine = random.randint(LAWSUIT_FINE_MIN, LAWSUIT_FINE_MAX)
        fine = min(fine, player["balance"])
        add_balance(user.id, -fine)
        log_transaction(user.id, None, fine, "court_fine")
        await update.message.reply_text(
            f"⚖️ Le juge a aggravé ta peine de {extra // 60} minutes\n"
            f"et t'a condamné à une amende de {fmt_money(fine)}. 😰",
            parse_mode="Markdown"
        )


async def security(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Améliorer son niveau de sécurité."""
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if not context.args:
        lines = ["🛡️ *Niveaux de sécurité*\n"]
        for lvl, data in SECURITY_LEVELS.items():
            mark = "✅" if player["security_level"] == lvl else "  "
            lines.append(
                f"{mark} Niveau {lvl} — {int(data['protection'] * 100)}% protection — "
                f"{fmt_money(data['cost'])}"
            )
        lines.append(f"\nTon niveau actuel : *{player['security_level']}*")
        lines.append("Utilise /security <niveau> pour améliorer.")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    try:
        level = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Indique un niveau valide (0, 1, 2 ou 3).")
        return

    if level not in SECURITY_LEVELS:
        await update.message.reply_text("❌ Niveau invalide. Choisis entre 0 et 3.")
        return

    if level <= player["security_level"]:
        await update.message.reply_text(
            f"❌ Tu as déjà le niveau {player['security_level']}. Choisis un niveau supérieur."
        )
        return

    cost = SECURITY_LEVELS[level]["cost"]
    if player["balance"] < cost:
        await update.message.reply_text(
            f"❌ Il te faut {fmt_money(cost)} pour le niveau {level}.\n"
            f"Ton solde : {fmt_money(player['balance'])}."
        )
        return

    add_balance(user.id, -cost)
    update_player(user.id, security_level=level)
    log_transaction(user.id, None, cost, f"security_level_{level}")

    protection = int(SECURITY_LEVELS[level]["protection"] * 100)
    await update.message.reply_text(
        f"🛡️ Niveau de sécurité amélioré à *{level}* !\n"
        f"Tu bénéficies maintenant de {protection}% de protection contre les vols.",
        parse_mode="Markdown"
    )
