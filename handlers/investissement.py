"""
Commandes économiques additionnelles :
- /investir : placer de l'argent dans les caisses de l'État, récupérable
  avec intérêts après un délai fixe.
- /defi : petit défi aléatoire une fois par jour, pour faire revenir les
  joueurs sans dépendre de l'argent d'autres joueurs ou d'une entreprise.
"""

import random
import time

from telegram import Update
from telegram.ext import ContextTypes

from db import (
    get_or_create_player,
    create_state_investment,
    get_active_state_investment,
    claim_state_investment,
    update_player,
    add_balance,
)
from utils import fmt_money

# --- /investir ---------------------------------------------------------

INVESTMENT_RATE = 0.08              # 8% de gain à maturité
INVESTMENT_DURATION_SECONDS = 24 * 3600   # 24h
MIN_INVESTMENT = 100_000
MAX_INVESTMENT = 10_000_000


async def investir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    active = get_active_state_investment(user.id)

    # Aucun argument : afficher le statut du placement en cours (ou réclamer s'il est mûr)
    if not context.args:
        if active is None:
            await update.message.reply_text(
                "💼 Tu n'as aucun placement en cours.\n\n"
                f"Utilisation : /investir <montant>\n"
                f"Taux : {int(INVESTMENT_RATE * 100)}% récupérable après "
                f"{INVESTMENT_DURATION_SECONDS // 3600}h.\n"
                f"Montant : entre {fmt_money(MIN_INVESTMENT)} et {fmt_money(MAX_INVESTMENT)}."
            )
            return

        now = int(time.time())
        if now >= active["matures_at"]:
            ok = claim_state_investment(active["investment_id"], user.id)
            if ok:
                gain = int(active["amount"] * active["rate"])
                await update.message.reply_text(
                    f"🏦 Placement arrivé à maturité !\n"
                    f"Capital récupéré : {fmt_money(active['amount'])}\n"
                    f"Intérêts : {fmt_money(gain)}\n"
                    f"Total reçu : {fmt_money(active['amount'] + gain)}"
                )
            else:
                await update.message.reply_text("❌ Impossible de récupérer ce placement.")
            return

        remaining = active["matures_at"] - now
        hours, minutes = divmod(remaining // 60, 60)
        await update.message.reply_text(
            f"💼 Placement en cours : {fmt_money(active['amount'])}\n"
            f"⏳ Disponible dans {hours}h{minutes:02d}min\n"
            f"Gain prévu : {fmt_money(int(active['amount'] * active['rate']))}"
        )
        return

    # Un montant est fourni : ouvrir un nouveau placement
    if active is not None:
        await update.message.reply_text(
            "❌ Tu as déjà un placement en cours. Utilise /investir sans argument "
            "pour voir son statut ou le récupérer s'il est mûr."
        )
        return

    try:
        amount = int(context.args[0].replace(" ", "").replace(",", ""))
    except ValueError:
        await update.message.reply_text("❌ Montant invalide. Exemple : /investir 500000")
        return

    if amount < MIN_INVESTMENT or amount > MAX_INVESTMENT:
        await update.message.reply_text(
            f"❌ Le montant doit être entre {fmt_money(MIN_INVESTMENT)} et {fmt_money(MAX_INVESTMENT)}."
        )
        return

    if player["balance"] < amount:
        await update.message.reply_text("❌ Solde insuffisant.")
        return

    investment_id = create_state_investment(
        user.id, amount, INVESTMENT_RATE, INVESTMENT_DURATION_SECONDS
    )
    if not investment_id:
        await update.message.reply_text("❌ Solde insuffisant.")
        return

    hours = INVESTMENT_DURATION_SECONDS // 3600
    await update.message.reply_text(
        f"✅ {fmt_money(amount)} placés dans les caisses de l'État.\n"
        f"Récupérable dans {hours}h avec {int(INVESTMENT_RATE * 100)}% d'intérêts "
        f"({fmt_money(amount + int(amount * INVESTMENT_RATE))} au total).\n"
        f"Tape /investir pour suivre ton placement."
    )


# --- /defi ---------------------------------------------------------------

DEFI_COOLDOWN_SECONDS = 24 * 3600

# (texte affiché, montant min, montant max, probabilité de réussite)
DEFIS = [
    ("As-tu bien fermé les yeux avant de dormir ce matin ? Bref, {gain}", 30_000, 90_000, 0.9),
    ("Tu as croisé un contrôleur fiscal sans te faire remarquer.", 40_000, 120_000, 0.85),
    ("Un inconnu t'a filé un pourboire pour un service que tu n'as pas rendu.", 20_000, 150_000, 0.8),
    ("Tu as trouvé une pièce rare au fond de ta poche.", 50_000, 200_000, 0.75),
    ("Grosse journée : tout t'a réussi aujourd'hui.", 80_000, 250_000, 0.65),
]


async def defi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    now = int(time.time())
    last = player["last_defi"] or 0
    remaining = DEFI_COOLDOWN_SECONDS - (now - last)
    if remaining > 0:
        hours, minutes = divmod(remaining // 60, 60)
        await update.message.reply_text(
            f"⏳ Prochain défi dans {hours}h{minutes:02d}min."
        )
        return

    _, low, high, success_rate = random.choice(DEFIS)
    update_player(user.id, last_defi=now)

    if random.random() > success_rate:
        await update.message.reply_text(
            "🎯 Défi du jour : raté ! Retente ta chance demain."
        )
        return

    gain = random.randint(low, high)
    add_balance(user.id, gain)
    await update.message.reply_text(
        f"🎯 Défi du jour réussi ! Tu gagnes {fmt_money(gain)}."
    )
