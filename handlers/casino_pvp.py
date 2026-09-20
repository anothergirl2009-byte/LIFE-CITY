"""
LifeCity Bot - Casino PvP
/blackjack montant — Blackjack contre le bot
/cockfight montant — Combat de coqs (PvP)
/ppc montant — Pierre-papier-ciseaux
/lancer montant — Dés
"""

import random
import time

from telegram import Update
from telegram.ext import ContextTypes

from config import MIN_BET, MAX_BET
from db import get_or_create_player, add_balance, log_transaction, get_conn
from utils import fmt_money

try:
    from handlers.journal import log_big_win
except ImportError:
    from journal import log_big_win

# ─── Cooldown anti-spam ───────────────────────────────────────────────────────
_last_play: dict[int, float] = {}
COOLDOWN = 3  # secondes


def _check_cooldown(user_id: int) -> float:
    now = time.time()
    elapsed = now - _last_play.get(user_id, 0)
    if elapsed < COOLDOWN:
        return COOLDOWN - elapsed
    _last_play[user_id] = now
    return 0.0


def _parse_bet(player, args) -> tuple[int | None, str | None]:
    """Valide et retourne (montant, erreur)."""
    if not args:
        return None, "❌ Indique un montant. Ex : /blackjack 1000"
    try:
        amount = int(args[0])
    except ValueError:
        return None, "❌ Montant invalide."
    if amount < MIN_BET:
        return None, f"❌ Mise minimale : {fmt_money(MIN_BET)}."
    if amount > MAX_BET:
        return None, f"❌ Mise maximale : {fmt_money(MAX_BET)}."
    if player["balance"] < amount:
        return None, f"❌ Solde insuffisant. Tu as {fmt_money(player['balance'])}."
    return amount, None


# ─── Blackjack ────────────────────────────────────────────────────────────────

def _bj_value(hand: list[int]) -> int:
    total = sum(hand)
    aces = hand.count(11)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def _bj_card() -> int:
    c = random.randint(1, 13)
    return min(c, 10) if c != 1 else 11


async def blackjack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    wait = _check_cooldown(user.id)
    if wait:
        await update.message.reply_text(f"⏳ Attends {wait:.1f}s avant de rejouer.")
        return

    amount, err = _parse_bet(player, context.args)
    if err:
        await update.message.reply_text(err)
        return

    player_hand = [_bj_card(), _bj_card()]
    dealer_hand = [_bj_card(), _bj_card()]

    # Tirage automatique joueur (s'arrête à 17+)
    while _bj_value(player_hand) < 17:
        player_hand.append(_bj_card())

    while _bj_value(dealer_hand) < 17:
        dealer_hand.append(_bj_card())

    pv = _bj_value(player_hand)
    dv = _bj_value(dealer_hand)

    if pv > 21:
        result = "bust"
    elif dv > 21 or pv > dv:
        result = "win"
    elif pv == dv:
        result = "draw"
    else:
        result = "lose"

    gain = 0
    if result == "win":
        gain = amount
        add_balance(user.id, amount)
        log_transaction(None, user.id, amount, "blackjack_win")
        log_big_win("Blackjack", user.id, user.first_name, amount)
        msg = f"🃏 *Blackjack*\n\nTon jeu : {player_hand} = **{pv}**\nCroupier : {dealer_hand} = **{dv}**\n\n✅ Tu gagnes {fmt_money(amount)} !"
    elif result == "draw":
        msg = f"🃏 *Blackjack*\n\nTon jeu : {player_hand} = **{pv}**\nCroupier : {dealer_hand} = **{dv}**\n\n🤝 Égalité — mise remboursée."
    else:
        add_balance(user.id, -amount)
        log_transaction(user.id, None, amount, "blackjack_loss")
        msg = f"🃏 *Blackjack*\n\nTon jeu : {player_hand} = **{pv}**\nCroupier : {dealer_hand} = **{dv}**\n\n❌ Tu perds {fmt_money(amount)}."

    await update.message.reply_text(msg, parse_mode="Markdown")


# ─── Combat de coqs ──────────────────────────────────────────────────────────

COQS = ["🐓 Death", "🐔 aziz", "🐓 olivares", "🐔 manuella", "🐓 lyon"]

async def cockfight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    wait = _check_cooldown(user.id)
    if wait:
        await update.message.reply_text(f"⏳ Attends {wait:.1f}s avant de rejouer.")
        return

    amount, err = _parse_bet(player, context.args)
    if err:
        await update.message.reply_text(err)
        return

    ton_coq = random.choice(COQS)
    adversaire = random.choice([c for c in COQS if c != ton_coq])
    win = random.random() < 0.5

    if win:
        add_balance(user.id, amount)
        log_transaction(None, user.id, amount, "cockfight_win")
        log_big_win("Combat de coqs", user.id, user.first_name, amount)
        msg = (
            f"🐓 *Combat de coqs*\n\n"
            f"Ton coq : {ton_coq}\nAdversaire : {adversaire}\n\n"
            f"🏆 {ton_coq} gagne ! Tu remportes {fmt_money(amount)} !"
        )
    else:
        add_balance(user.id, -amount)
        log_transaction(user.id, None, amount, "cockfight_loss")
        msg = (
            f"🐓 *Combat de coqs*\n\n"
            f"Ton coq : {ton_coq}\nAdversaire : {adversaire}\n\n"
            f"💀 {ton_coq} est KO ! Tu perds {fmt_money(amount)}."
        )

    await update.message.reply_text(msg, parse_mode="Markdown")


# ─── Pierre-Papier-Ciseaux ───────────────────────────────────────────────────

CHOIX = ["🪨 Pierre", "📄 Papier", "✂️ Ciseaux"]
GAGNE_CONTRE = {
    "🪨 Pierre": "✂️ Ciseaux",
    "📄 Papier": "🪨 Pierre",
    "✂️ Ciseaux": "📄 Papier",
}


async def ppc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    wait = _check_cooldown(user.id)
    if wait:
        await update.message.reply_text(f"⏳ Attends {wait:.1f}s avant de rejouer.")
        return

    amount, err = _parse_bet(player, context.args)
    if err:
        await update.message.reply_text(err)
        return

    joueur = random.choice(CHOIX)
    bot_choix = random.choice(CHOIX)

    if joueur == bot_choix:
        result = "draw"
    elif GAGNE_CONTRE[joueur] == bot_choix:
        result = "win"
    else:
        result = "lose"

    if result == "win":
        add_balance(user.id, amount)
        log_transaction(None, user.id, amount, "ppc_win")
        log_big_win("Pierre-Papier-Ciseaux", user.id, user.first_name, amount)
        msg = f"✊ *PPC*\n\nToi : {joueur}\nBot : {bot_choix}\n\n✅ Tu gagnes {fmt_money(amount)} !"
    elif result == "draw":
        msg = f"✊ *PPC*\n\nToi : {joueur}\nBot : {bot_choix}\n\n🤝 Égalité — mise remboursée."
    else:
        add_balance(user.id, -amount)
        log_transaction(user.id, None, amount, "ppc_loss")
        msg = f"✊ *PPC*\n\nToi : {joueur}\nBot : {bot_choix}\n\n❌ Tu perds {fmt_money(amount)}."

    await update.message.reply_text(msg, parse_mode="Markdown")


# ─── Lancer de dés ───────────────────────────────────────────────────────────

async def lancer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    wait = _check_cooldown(user.id)
    if wait:
        await update.message.reply_text(f"⏳ Attends {wait:.1f}s avant de rejouer.")
        return

    amount, err = _parse_bet(player, context.args)
    if err:
        await update.message.reply_text(err)
        return

    d_joueur = random.randint(1, 6)
    d_bot = random.randint(1, 6)

    if d_joueur > d_bot:
        add_balance(user.id, amount)
        log_transaction(None, user.id, amount, "dice_win")
        log_big_win("Lancer de dés", user.id, user.first_name, amount)
        msg = f"🎲 *Dés*\n\nToi : {d_joueur} | Bot : {d_bot}\n\n✅ Tu gagnes {fmt_money(amount)} !"
    elif d_joueur == d_bot:
        msg = f"🎲 *Dés*\n\nToi : {d_joueur} | Bot : {d_bot}\n\n🤝 Égalité — mise remboursée."
    else:
        add_balance(user.id, -amount)
        log_transaction(user.id, None, amount, "dice_loss")
        msg = f"🎲 *Dés*\n\nToi : {d_joueur} | Bot : {d_bot}\n\n❌ Tu perds {fmt_money(amount)}."

    await update.message.reply_text(msg, parse_mode="Markdown")
