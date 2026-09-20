"""
LifeCity Bot - Économie de base
/acc — Voir son compte
/daily — Bonus quotidien
/work — Travailler
/pay — Envoyer de l'argent à un autre joueur
/richlist — Top 10 des plus riches
"""

import random
import time

from telegram import Update
from telegram.ext import ContextTypes

from config import (
    DAILY_MIN, DAILY_MAX, DAILY_COOLDOWN_HOURS,
    WORK_MIN, WORK_MAX, WORK_COOLDOWN_HOURS,
)
from db import (
    get_or_create_player, get_conn, add_balance, log_transaction,
    get_player_by_name_or_id
)
from utils import fmt_money, fmt_duration, escape_markdown


async def acc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    target_id = None

    # Vérifier si on répond à un message
    if update.effective_message.reply_to_message:
        ru = update.effective_message.reply_to_message.from_user
        get_or_create_player(ru.id, ru.username, ru.first_name)
        target_id = ru.id
    # Vérifier si un argument est passé (username ou ID)
    elif context.args:
        identifier = context.args[0].lstrip("@")
        player = get_player_by_name_or_id(identifier)
        if player is None:
            await update.effective_message.reply_text(
                "❌ Joueur introuvable (il doit avoir déjà utilisé le bot)."
            )
            return
        target_id = player["user_id"]

    if target_id is None:
        player = get_or_create_player(user.id, user.username, user.first_name)
    else:
        with get_conn() as conn:
            player = conn.execute(
                "SELECT * FROM players WHERE user_id = ?", (target_id,)
            ).fetchone()

    name_safe = escape_markdown(player['first_name'] or "Joueur")
    text = (
        f"💳 *Compte de {name_safe}*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💰 *Solde :* {fmt_money(player['balance'])}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📥 /daily  |  🔨 /work  |  💸 /pay"
    )
    try:
        await update.effective_message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        if "Can't parse entities" in str(e):
            await update.effective_message.reply_text(text.replace("*", ""))
        else:
            raise e


async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    now = int(time.time())
    cooldown = DAILY_COOLDOWN_HOURS * 3600
    elapsed = now - player["last_daily"]

    if elapsed < cooldown:
        remaining = cooldown - elapsed
        await update.effective_message.reply_text(
            f"⏳ Tu as déjà récupéré ton bonus quotidien.\n"
            f"Reviens dans {fmt_duration(remaining)}."
        )
        return

    gain = random.randint(DAILY_MIN, DAILY_MAX)
    add_balance(user.id, gain)
    with get_conn() as conn:
        conn.execute(
            "UPDATE players SET last_daily = ? WHERE user_id = ?", (now, user.id)
        )
    log_transaction(None, user.id, gain, "daily")

    await update.effective_message.reply_text(
        f"🎁 Bonus quotidien récupéré : +*{fmt_money(gain)}* !",
        parse_mode="Markdown",
    )


async def work(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    now = int(time.time())
    cooldown = WORK_COOLDOWN_HOURS * 3600
    elapsed = now - player["last_work"]

    if elapsed < cooldown:
        remaining = cooldown - elapsed
        await update.effective_message.reply_text(
            f"😴 Tu es fatigué, tu ne peux pas encore retravailler.\n"
            f"Reviens dans {fmt_duration(remaining)}."
        )
        return

    gain = random.randint(WORK_MIN, WORK_MAX)
    add_balance(user.id, gain)
    with get_conn() as conn:
        conn.execute(
            "UPDATE players SET last_work = ? WHERE user_id = ?", (now, user.id)
        )
    log_transaction(None, user.id, gain, "work")

    messages = [
        f"💼 Tu as travaillé dur et gagné *{fmt_money(gain)}* !",
        f"🔨 Journée de boulot terminée : +*{fmt_money(gain)}*.",
        f"📈 Bon travail ! Tu empoches *{fmt_money(gain)}*.",
    ]
    await update.effective_message.reply_text(random.choice(messages), parse_mode="Markdown")


async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    sender = get_or_create_player(user.id, user.username, user.first_name)

    # Vérifier les différentes façons d'utiliser la commande
    target_user = None
    amount_str = None
    target_identifier = None

    # Cas 1: Réponse à un message + montant en argument
    if update.effective_message.reply_to_message and context.args:
        target_user = update.effective_message.reply_to_message.from_user
        amount_str = context.args[0]
    # Cas 2: /pay @username montant
    elif len(context.args) >= 2:
        target_identifier = context.args[0].lstrip("@")
        amount_str = context.args[1]
        # Rechercher le joueur par son nom ou ID
        player = get_player_by_name_or_id(target_identifier)
        if player is None:
            await update.effective_message.reply_text(
                f"❌ Joueur '{escape_markdown(target_identifier)}' introuvable. "
                "Il doit avoir déjà utilisé le bot."
            )
            return
        target_user_id = player["user_id"]
        target_first_name = player["first_name"]
        get_or_create_player(target_user_id, player["username"], player["first_name"])
    else:
        await update.effective_message.reply_text(
            "Utilisation : /pay @joueur montant (ou répond au message du joueur avec /pay montant)"
        )
        return

    # Si on a un target_user (cas 1), récupérer ses infos
    if target_user is not None:
        target_user_id = target_user.id
        target_first_name = target_user.first_name
        get_or_create_player(target_user.id, target_user.username, target_user.first_name)

    if target_user_id == user.id:
        await update.effective_message.reply_text("❌ Tu ne peux pas te payer toi-même.")
        return

    try:
        amount = int(amount_str)
    except (TypeError, ValueError):
        await update.effective_message.reply_text("❌ Montant invalide.")
        return

    if amount <= 0:
        await update.effective_message.reply_text("❌ Le montant doit être positif.")
        return

    if sender["balance"] < amount:
        await update.effective_message.reply_text("❌ Solde insuffisant.")
        return

    add_balance(user.id, -amount)
    add_balance(target_user_id, amount)
    log_transaction(user.id, target_user_id, amount, "pay")

    text = f"✅ Tu as envoyé *{fmt_money(amount)}* à {escape_markdown(target_first_name)}."
    try:
        await update.effective_message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        if "Can't parse entities" in str(e):
            await update.effective_message.reply_text(text.replace("*", ""))
        else:
            raise e


async def richlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT first_name, username, (balance + bank_balance) AS total
               FROM players ORDER BY total DESC LIMIT 10"""
        ).fetchall()

    if not rows:
        await update.effective_message.reply_text("Aucun joueur enregistré pour le moment.")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = [
        "🎰━━━━━━━━━━━━━━━━━━━━🎰",
        "      💸 FORTUNE RANKING 💸",
        "🎰━━━━━━━━━━━━━━━━━━━━🎰",
        "",
    ]

    for i, row in enumerate(rows):
        name = escape_markdown(row["first_name"] or row["username"] or "Joueur inconnu")
        total = fmt_money(row["total"])

        if i < 3:
            lines.append(f"{medals[i]} {name}")
            lines.append(f"   ┗━▶ *{total}*")
            lines.append("")
        else:
            if i == 3:
                lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"{i + 1:>2} ◈ {name} ········ *{total}*")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")

    text = "\n".join(lines)
    try:
        await update.effective_message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        if "Can't parse entities" in str(e):
            await update.effective_message.reply_text(text.replace("*", ""))
        else:
            raise e