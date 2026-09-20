"""
LifeCity Bot - Casino (jeux solo) v3
Chaque jeu a une mise VARIABLE (avec un minimum) et un avantage maison
raisonnable : le casino garde un edge, mais les joueurs gagnent vraiment
assez souvent pour que ça reste amusant.
Répartition cible (roue, rebet, crash, slots, roulette) :
  💀 Perte : 50%   🔁 Remboursé (égalité) : 20%   ✨ Gain réel : 30%

Usage (identique pour tous les jeux a mise variable) :
    /roue              -> affiche les tarifs et la table des multiplicateurs
                           possibles (SANS les % de proba, pour ne pas
                           afficher l'avantage maison aux joueurs)
    /roue <montant>     -> mise ce montant et joue

Meme principe pour : /rebet /crash /slots /roulette

/apple est different : c'est une tour a 10 niveaux avec mise VARIABLE
(minimum 50 000). Sans argument, /apple affiche la table des gains.
Avec une mise, /apple <mise> lance directement l'ascension.

/mines nb_mines mise reste inchange (mise libre), non concerne par la
demande de rééquilibrage — dis-moi si tu veux aussi lui fixer un tarif.
"""

import asyncio
import random

from telegram import Update
from telegram.ext import ContextTypes

from db import get_or_create_player, add_balance, log_transaction
from utils import fmt_money

try:
    from handlers.journal import log_big_win
except ImportError:
    from journal import log_big_win


def _move(user_id: int, delta: int, reason: str) -> None:
    """add_balance + log_transaction en un seul appel, pour que TOUT mouvement
    d'argent du casino solo apparaisse dans /historique et /histojoueur."""
    add_balance(user_id, delta)
    if delta > 0:
        log_transaction(None, user_id, delta, reason)
    elif delta < 0:
        log_transaction(user_id, None, -delta, reason)


# ============================================================
# CONFIGURATION DES JEUX A MISE FIXE
# ============================================================
# "segments"/"weights" : table des multiplicateurs possibles et leur poids
# (les poids sont donnes en pourcentage direct, ils totalisent 100).
# Le dernier segment (le plus grand multiplicateur) est le JACKPOT.

GAMES = {
    "roue": {
        "name": "Roue de la Fortune",
        "emoji": "🎡",
        "min_bet": 1_000,
        "max_bet": 10_000_000,
        "cap": 60_000_000,
        "segments": [0, 1, 1.5, 2, 5, 10, 25],
        # Perte 50% / Remboursé (égalité) 20% / Gain 30%
        "weights": [50.0, 20.0, 15.0, 7.5, 3.75, 3.0, 0.75],
    },
    "rebet": {
        "name": "Rebet (Quitte ou Double)",
        "emoji": "🪙",
        "min_bet": 5_000,
        "max_bet": 10_000_000,
        "cap": 60_000_000,
        "segments": [0, 1, 1.5, 2, 5],
        # Perte 50% / Remboursé (égalité) 20% / Gain 30%
        "weights":  [50.0, 20.0, 21.4297, 6.8593, 1.711],
    },
    "apple": {
        "name": "Apple of Fortune",
        "emoji": "🍏",
        "min_bet": 50_000,
        "max_bet": 10_000_000,
        "cap": 60_000_000,
        "segments": [0, 1.2, 2, 4, 8, 25],
        "weights":  [72, 17, 7, 2.7, 1, 0.3],
    },
    "crash": {
        "name": "Crash",
        "emoji": "📈",
        "min_bet": 15_000,
        "max_bet": 10_000_000,
        "cap": 60_000_000,
        "segments": [0, 1, 1.5, 2.5, 5, 10, 25],
        # Perte 50% / Remboursé (égalité) 20% / Gain 30%
        "weights":  [50.0, 20.0, 23.9939, 3.5975, 1.8026, 0.4833, 0.1227],
    },
    "slots": {
        "name": "Machine à sous",
        "emoji": "🎰",
        "min_bet": 10_000,
        "max_bet": 10_000_000,
        "cap": 50_000_000,
        "segments": [0, 1, 1.5, 2.5, 5, 10, 25],
        # Perte 50% / Remboursé (égalité) 20% / Gain 30%
        "weights":  [50.0, 20.0, 23.9939, 3.5975, 1.8026, 0.4833, 0.1227],
    },
    "roulette": {
        "name": "Roulette",
        "emoji": "🎲",
        "min_bet": 5_000,
        "max_bet": 10_000_000,
        "cap": 50_000_000,
        "segments": [0, 1, 1.5, 3, 6, 12, 25],
        # Perte 50% / Remboursé (égalité) 20% / Gain 30%
        "weights":  [50.0, 20.0, 23.9939, 3.5975, 1.8026, 0.4833, 0.1227],
    },
}

# Bonus "Cadeau" : ultra rare, independant de la mise, montant fixe.
CADEAU_AMOUNTS = [1_000_000, 500_000, 2_000_000, 10_000_000]
CADEAU_CHANCE = 1 / 4000  # ~0.025% de chance a chaque partie

# "Gros lot" : bonus fixe indépendant de la mise, dispo sur TOUS les jeux
# solo (roue, apple, crash, slots, roulette, mines). Remplace l'ancien bonus
# qui n'existait que sur la roue — chances x5 par rapport à cet ancien bonus.
GROS_LOT_AMOUNTS = [200_000, 500_000, 1_000_000]
GROS_LOT_WEIGHTS = [60, 30, 10]  # 200k plus courant, 1M plus rare
GROS_LOT_CHANCE = 0.25  # 25% de chance à chaque partie, tous jeux solo

# Phrases dramatiques tirees au hasard a chaque defaite, pour que perdre
# se ressente vraiment (au lieu d'un simple "-X" plat et sans saveur).
LOSS_FLAVORS = [
    "💸 RUINE TOTALE. Le casino ne fait jamais de cadeau...",
    "💀 Anéanti. Ton argent est parti en fumée.",
    "🩸 Saigné à blanc par la maison.",
    "📉 Effondrement complet. Tu repars les poches vides.",
    "🔥 Ton argent brûle sous tes yeux.",
    "☠️ Le casino t'a dévoré cru.",
    "🕳️ Englouti dans le trou noir de la maison.",
    "😵 KO direct. La maison gagne encore.",
    "🥀 Fauché, encore une fois.",
    "🚨 Alerte solde : catastrophe financière.",
]


def _loss_line(bet: int) -> str:
    return f"{random.choice(LOSS_FLAVORS)}\n💸 Perdu : *-{fmt_money(bet)}*"


def _maybe_cadeau(user_id: int, user_name: str = None, game_label: str = None):
    """Tire le bonus cadeau ultra rare. Renvoie le montant gagne, ou None."""
    if random.random() < CADEAU_CHANCE:
        amount = random.choice(CADEAU_AMOUNTS)
        _move(user_id, amount, "casino_cadeau")
        if user_name:
            # Ultra rare (1 chance sur 4000) : toujours publié dans le journal.
            log_big_win(game_label or "Cadeau Surprise", user_id, user_name, amount, threshold=0)
        return amount
    return None


def _maybe_gros_lot(user_id: int):
    """Tire le 'gros lot' (200k/500k/1M), dispo sur tous les jeux solo.
    Renvoie le montant gagné, ou None."""
    if random.random() < GROS_LOT_CHANCE:
        amount = random.choices(GROS_LOT_AMOUNTS, weights=GROS_LOT_WEIGHTS, k=1)[0]
        _move(user_id, amount, "casino_gros_lot")
        return amount
    return None


def _spin(game_key: str) -> float:
    g = GAMES[game_key]
    return random.choices(g["segments"], weights=g["weights"], k=1)[0]


def _game_stats(game_key: str):
    """Renvoie (pct_perte, pct_rembourse, pct_gain_reel) pour un jeu."""
    g = GAMES[game_key]
    loss_pct = 0.0
    breakeven_pct = 0.0
    win_pct = 0.0
    for seg, w in zip(g["segments"], g["weights"]):
        if seg == 0:
            loss_pct += w
        elif seg == 1:
            breakeven_pct += w
        else:
            win_pct += w
    return loss_pct, breakeven_pct, win_pct


def _rates_text(game_key: str) -> str:
    g = GAMES[game_key]
    max_seg = max(g["segments"])
    lines = [f"{g['emoji']} *{g['name']}* — Tarifs\n"]
    lines.append(f"💵 Mise minimum : *{fmt_money(g['min_bet'])}*")
    lines.append(f"💰 Mise maximum : *{fmt_money(g['max_bet'])}*")
    lines.append(f"🏆 Plafond de gain : *{fmt_money(g['cap'])}*\n")
    lines.append("📊 *Multiplicateurs possibles*")
    for seg in g["segments"]:
        if seg == 0:
            label = "💀 Perdu"
        elif seg == 1:
            label = "🔁 Remboursé (x1)"
        else:
            label = f"✨ x{seg}"
            if seg == max_seg:
                label += " — 🎉 JACKPOT"
        lines.append(f"• {label}")

    lines.append(f"\n💎 *Gros lot possible* (indépendant de la mise) :")
    lines.append("   " + " · ".join(fmt_money(a) for a in GROS_LOT_AMOUNTS))
    lines.append(f"\n🎁 *Cadeau surprise* (indépendant de la mise, ultra rare) :")
    lines.append("   " + " · ".join(fmt_money(a) for a in CADEAU_AMOUNTS))
    lines.append(f"\n▶️ Pour jouer : `/{game_key} <montant>`")
    lines.append(f"Ex : `/{game_key} {g['min_bet']}`")
    return "\n".join(lines)


def _parse_bet_for_game(game_key: str, args, balance: int):
    g = GAMES[game_key]
    if not args:
        return None, None
    try:
        bet = int(args[-1])
    except ValueError:
        return None, "❌ Mise invalide."
    if bet < g["min_bet"]:
        return None, f"❌ Mise minimum : {fmt_money(g['min_bet'])}."
    if bet > g["max_bet"]:
        return None, f"❌ Mise maximum : {fmt_money(g['max_bet'])}."
    if bet > balance:
        return None, "❌ Solde insuffisant."
    return bet, None


async def _reply(update: Update, text: str) -> None:
    try:
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        if "Can't parse entities" in str(e):
            await update.message.reply_text(text, parse_mode=None)
        else:
            raise e


# ============================================================
# MOTEUR GENERIQUE (mise variable + spin + cadeau + cap)
# ============================================================

async def _play_variable_game(update: Update, context: ContextTypes.DEFAULT_TYPE, game_key: str, result_text_fn) -> None:
    """
    Gere le flux commun a tous les jeux a mise variable :
    - sans montant -> affiche les tarifs + la liste des multiplicateurs
      possibles (pas de % de proba affiché aux joueurs)
    - avec un montant -> deduit la mise, tire le cadeau rare, sinon tire le
      multiplicateur normal et affiche le resultat via result_text_fn.
    """
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)
    g = GAMES[game_key]

    if not context.args:
        await _reply(update, _rates_text(game_key))
        return

    bet, error = _parse_bet_for_game(game_key, context.args, player["balance"])
    if error:
        await update.message.reply_text(error)
        return

    _move(user.id, -bet, f"{game_key}_mise")

    cadeau = _maybe_cadeau(user.id, user.first_name, g["name"])
    if cadeau:
        await _reply(
            update,
            f"🎁✨ *CADEAU SURPRISE* ✨🎁\n\n"
            f"Le casino te fait un cadeau exceptionnel de *{fmt_money(cadeau)}* !\n"
            f"C'est totalement indépendant de ta mise, tu as juste eu énormément de chance !",
        )
        return

    gros_lot = _maybe_gros_lot(user.id)
    if gros_lot:
        await _reply(
            update,
            f"💎🎉 *GROS LOT* 🎉💎\n\n"
            f"Le casino te fait gagner *{fmt_money(gros_lot)}* !\n"
            f"C'est totalement indépendant de ta mise, tu as juste eu énormément de chance !",
        )
        return

    multiplier = _spin(game_key)
    winnings = min(int(bet * multiplier), g["cap"]) if multiplier > 0 else 0
    if winnings > 0:
        _move(user.id, winnings, f"{game_key}_gain")
        log_big_win(g["name"], user.id, user.first_name, winnings)

    text = result_text_fn(bet, multiplier, winnings)
    await _reply(update, text)


# ============================================================
# JEUX
# ============================================================

async def roue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Version animee : la roue tourne, ralentit, s'arrete, puis affiche le gain.
    Inclut désormais les multiplicateurs x1, x1.5, x2 et les bonus fixes.
    """
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)
    g = GAMES["roue"]

    if not context.args:
        await _reply(update, _rates_text("roue"))
        return

    bet, error = _parse_bet_for_game("roue", context.args, player["balance"])
    if error:
        await update.message.reply_text(error)
        return

    _move(user.id, -bet, "roue_mise")

    cadeau = _maybe_cadeau(user.id, user.first_name, g["name"])
    if cadeau:
        msg = await update.message.reply_text("🎡 La roue tourne...")
        await asyncio.sleep(1.2)
        await msg.edit_text(
            f"🎁✨ *CADEAU SURPRISE* ✨🎁\n\n"
            f"Le casino te fait un cadeau exceptionnel de *{fmt_money(cadeau)}* !\n"
            f"C'est totalement indépendant de ta mise, tu as juste eu énormément de chance !",
            parse_mode="Markdown",
        )
        return

    gros_lot = _maybe_gros_lot(user.id)
    if gros_lot:
        msg = await update.message.reply_text("🎡 La roue tourne...")
        await asyncio.sleep(1.2)
        await msg.edit_text(
            f"💎🎉 *GROS LOT* 🎉💎\n\n"
            f"Le casino te fait gagner *{fmt_money(gros_lot)}* !\n"
            f"C'est totalement indépendant de ta mise, tu as juste eu énormément de chance !",
            parse_mode="Markdown",
        )
        return

    multiplier = _spin("roue")
    total_winnings = min(int(bet * multiplier), g["cap"]) if multiplier > 0 else 0

    if total_winnings > 0:
        _move(user.id, total_winnings, "roue_gain")
        log_big_win(g["name"], user.id, user.first_name, total_winnings)

    # ── Animation en 3 temps ─────────────────────────────
    msg = await update.message.reply_text("🎡 La roue tourne... 🌀")
    await asyncio.sleep(1.2)

    await msg.edit_text("🎡 La roue ralentit... 🐢")
    await asyncio.sleep(1.2)

    await msg.edit_text("🎡 La roue s'arrête... ⏳")
    await asyncio.sleep(1.0)

    if multiplier > 0:
        tag = " 🎉 JACKPOT !!!" if multiplier == max(g["segments"]) else ""

        if multiplier == 1:
            result_text = f"🔁 Remboursé ! (x1)"
        elif multiplier < 2:
            result_text = f"✨ Petit gain ! *x{multiplier}*"
        else:
            result_text = f"🎉 Gain ! *x{multiplier}*{tag}"

        final_text = f"🎡 La roue s'arrête sur *x{multiplier}* !\n{result_text}\n💰 Total : *+{fmt_money(total_winnings)}*"
    else:
        final_text = f"🎡 La roue s'arrête sur x0...\n{_loss_line(bet)}"

    try:
        await msg.edit_text(final_text, parse_mode="Markdown")
    except Exception as e:
        if "Can't parse entities" in str(e):
            await msg.edit_text(final_text)
        else:
            raise e


async def rebet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    def result(bet, multiplier, winnings):
        if multiplier > 0:
            tag = " 🎉 JACKPOT !!!" if multiplier == max(GAMES["rebet"]["segments"]) else ""
            return f"🪙 Pile ! *x{multiplier}*{tag} → *+{fmt_money(winnings)}*"
        return f"🪙 Face...\n{_loss_line(bet)}"
    await _play_variable_game(update, context, "rebet", result)


# ============================================================
# APPLE OF FORTUNE — tour a 10 niveaux, mise variable
# ============================================================

APPLE_LEVELS = [
    {"level": 1,  "multiplier": 1.50,   "safe": 3, "traps": 2},
    {"level": 2,  "multiplier": 2.10,   "safe": 3, "traps": 2},
    {"level": 3,  "multiplier": 3.20,   "safe": 2, "traps": 3},
    {"level": 4,  "multiplier": 4.80,   "safe": 2, "traps": 3},
    {"level": 5,  "multiplier": 7.00,   "safe": 2, "traps": 3},
    {"level": 6,  "multiplier": 12.00,  "safe": 1, "traps": 4},
    {"level": 7,  "multiplier": 22.00,  "safe": 1, "traps": 4},
    {"level": 8,  "multiplier": 45.00,  "safe": 1, "traps": 4},
    {"level": 9,  "multiplier": 100.00, "safe": 1, "traps": 4},
    {"level": 10, "multiplier": 500.00, "safe": 1, "traps": 4},
]
APPLE_MIN_BET = 50_000
APPLE_MAX_BET = 10_000_000
APPLE_CAP = 60_000_000


def _apple_rates_text() -> str:
    lines = [
        "🍏 *Apple of Fortune*",
        "━━━━━━━━━━━━━━━━━━━━",
        "Gravis 10 niveaux en choisissant une pomme parmi 5.",
        "🍏 Pomme verte = tu passes au niveau suivant",
        "🍎 Pomme rouge = BOOM, tu perds tout !",
        "Les pièges augmentent à chaque palier.\n",
        f"Mise minimum : {fmt_money(APPLE_MIN_BET)}\n",
        "Table des gains :",
    ]
    for lvl in APPLE_LEVELS:
        lines.append(
            f"  Niveau {lvl['level']:>2} — x{lvl['multiplier']:.2f}  "
            f"({lvl['safe']}/5 sûres  |  {lvl['traps']} pièges)"
        )
    lines.append(f"\n💎 *Gros lot* (indépendant de la mise — {GROS_LOT_CHANCE * 100:.0f}% de chances à chaque partie) :")
    lines.append("   " + " · ".join(fmt_money(a) for a in GROS_LOT_AMOUNTS))
    lines.append(f"\n🎁 Cadeau surprise possible (ultra rare — 1 chance sur {int(1 / CADEAU_CHANCE)}) :")
    lines.append("   " + " · ".join(fmt_money(a) for a in CADEAU_AMOUNTS))
    lines.append("\nUsage : /apple <mise>")
    lines.append("Ex : /apple 100000")
    return "\n".join(lines)


def _parse_apple_bet(args, balance: int):
    if not args:
        return None, None
    try:
        bet = int(args[-1])
    except ValueError:
        return None, "❌ Mise invalide."
    if bet < APPLE_MIN_BET:
        return None, f"❌ Mise minimum : {fmt_money(APPLE_MIN_BET)}."
    if bet > APPLE_MAX_BET:
        return None, f"❌ Mise maximum : {fmt_money(APPLE_MAX_BET)}."
    if bet > balance:
        return None, "❌ Solde insuffisant."
    return bet, None


async def apple(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if not context.args:
        await _reply(update, _apple_rates_text())
        return

    bet, error = _parse_apple_bet(context.args, player["balance"])
    if error:
        await update.message.reply_text(error)
        return

    _move(user.id, -bet, "apple_mise")

    cadeau = _maybe_cadeau(user.id, user.first_name, "Apple of Fortune")
    if cadeau:
        await _reply(
            update,
            f"🎁✨ *CADEAU SURPRISE* ✨🎁\n\nLe casino te fait un cadeau exceptionnel de *{fmt_money(cadeau)}* !",
        )
        return

    gros_lot = _maybe_gros_lot(user.id)
    if gros_lot:
        await _reply(
            update,
            f"💎🎉 *GROS LOT* 🎉💎\n\nLe casino te fait gagner *{fmt_money(gros_lot)}* !",
        )
        return

    # Escalade niveau par niveau : une seule pomme pourrie met fin a tout.
    progress_lines = []
    highest_cleared = 0
    for lvl in APPLE_LEVELS:
        safe_proba = lvl["safe"] / 5
        if random.random() < safe_proba:
            highest_cleared = lvl["level"]
            progress_lines.append(f"🍏 Niveau {lvl['level']} — pomme verte, tu montes ! (*x{lvl['multiplier']:.2f}*)")
        else:
            progress_lines.append(f"🍎 Niveau {lvl['level']} — BOOM, pomme pourrie !")
            break

    if highest_cleared == 10:
        multiplier = APPLE_LEVELS[9]["multiplier"]
        winnings = min(int(bet * multiplier), APPLE_CAP)
        add_balance(user.id, winnings)
        log_transaction(None, user.id, winnings, "apple_gain")
        log_big_win("Apple of Fortune", user.id, user.first_name, winnings)
        progress_lines.append(f"\n🏆🎉 TOUR COMPLÈTE ! JACKPOT *x{multiplier:.2f}* → *+{fmt_money(winnings)}*")
    elif highest_cleared > 0:
        # Une pomme pourrie a ete touchee apres avoir grimpe un peu : tout est perdu quand meme.
        progress_lines.append(f"\n{_loss_line(bet)}")
    else:
        progress_lines.append(f"\n{_loss_line(bet)}")

    await _reply(update, "\n".join(progress_lines))


async def crash(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    def result(bet, multiplier, winnings):
        if multiplier > 0:
            tag = " 🎉 JACKPOT !!!" if multiplier == max(GAMES["crash"]["segments"]) else ""
            return f"📈 Le multiplicateur grimpe... tu encaisses à *x{multiplier}*{tag} !\n🎉 *+{fmt_money(winnings)}*"
        return f"📈 Le multiplicateur grimpe... 💥 CRASH avant que tu encaisses !\n{_loss_line(bet)}"
    await _play_variable_game(update, context, "crash", result)


async def slots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    def result(bet, multiplier, winnings):
        if multiplier > 0:
            tag = " 🎉 JACKPOT !!!" if multiplier == max(GAMES["slots"]["segments"]) else ""
            return f"🎰 Combo gagnant ! *x{multiplier}*{tag} → *+{fmt_money(winnings)}*"
        return f"🎰 Pas de combo...\n{_loss_line(bet)}"
    await _play_variable_game(update, context, "slots", result)


async def roulette(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    def result(bet, multiplier, winnings):
        if multiplier > 0:
            tag = " 🎉 JACKPOT !!!" if multiplier == max(GAMES["roulette"]["segments"]) else ""
            return f"🎲 La bille tombe du bon côté ! *x{multiplier}*{tag} → *+{fmt_money(winnings)}*"
        return f"🎲 La bille tombe du mauvais côté...\n{_loss_line(bet)}"
    await _play_variable_game(update, context, "roulette", result)


# ============================================================
# MINES — inchangé (mise libre), non concerné par la demande initiale
# ============================================================

MINES_GRID_SIZE = 25
MINES_MAX_COUNT = 10
MIN_BET = 1_000
MAX_BET = 1_000_000_000


def _parse_bet(args, balance: int):
    if not args:
        return None, "Utilisation : précise une mise. Ex: /mines 3 1000"
    try:
        bet = int(args[-1])
    except ValueError:
        return None, "❌ Mise invalide."
    if bet < MIN_BET:
        return None, f"❌ Mise minimum : {fmt_money(MIN_BET)}."
    if bet > MAX_BET:
        return None, f"❌ Mise maximum : {fmt_money(MAX_BET)}."
    if bet > balance:
        return None, "❌ Solde insuffisant."
    return bet, None


async def mines(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if len(context.args) < 2:
        await update.message.reply_text("Utilisation : /mines nb_mines mise")
        return

    try:
        nb_mines = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Nombre de mines invalide.")
        return

    if nb_mines < 1 or nb_mines > MINES_MAX_COUNT:
        await update.message.reply_text(f"❌ Le nombre de mines doit être entre 1 et {MINES_MAX_COUNT}.")
        return

    bet, error = _parse_bet(context.args[1:], player["balance"])
    if error:
        await update.message.reply_text(error)
        return

    _move(user.id, -bet, "mines_mise")

    cadeau = _maybe_cadeau(user.id, user.first_name, "Mines")
    if cadeau:
        await _reply(
            update,
            f"🎁✨ *CADEAU SURPRISE* ✨🎁\n\nLe casino te fait un cadeau exceptionnel de *{fmt_money(cadeau)}* !",
        )
        return

    gros_lot = _maybe_gros_lot(user.id)
    if gros_lot:
        await _reply(
            update,
            f"💎🎉 *GROS LOT* 🎉💎\n\nLe casino te fait gagner *{fmt_money(gros_lot)}* !",
        )
        return

    safe_cells = MINES_GRID_SIZE - nb_mines
    cells_opened = 0
    target_opens = min(5, safe_cells)
    hit_mine = False

    for _ in range(target_opens):
        remaining_cells = MINES_GRID_SIZE - cells_opened
        remaining_mines = nb_mines
        proba_mine = min(1.0, (remaining_mines / remaining_cells) * 1.35)
        if random.random() < proba_mine:
            hit_mine = True
            break
        cells_opened += 1

    if hit_mine:
        text = (
            f"💣 BOOM ! Tu as touché une mine après {cells_opened} case(s) ouverte(s).\n"
            f"{_loss_line(bet)}"
        )
    else:
        multiplier = round(1 + (nb_mines / MINES_GRID_SIZE) * cells_opened * 1.1, 2)
        winnings = int(bet * multiplier)
        add_balance(user.id, winnings)
        log_transaction(None, user.id, winnings, "mines_gain")
        log_big_win("Mines", user.id, user.first_name, winnings)
        text = (
            f"💎 {cells_opened} case(s) sûre(s) ouverte(s), aucune mine !\n"
            f"🎉 Gagné ! *x{multiplier}* → *+{fmt_money(winnings)}*"
        )

    await _reply(update, text)