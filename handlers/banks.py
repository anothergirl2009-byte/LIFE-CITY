"""
LifeCity Bot - Banque
/banks         — Liste des banques disponibles
/openbank      — Ouvrir un compte dans une banque (plusieurs banques possibles)
/depositbank   — Déposer de l'argent dans une banque
/withdrawbank  — Retirer de l'argent d'une banque
/balancebank   — Voir le solde de tous ses comptes bancaires
/loanbank      — Prendre un prêt dans une banque
/repaybank     — Rembourser un prêt
/loansbank     — Voir ses prêts actifs

NOTE : nécessite la migration db_bank_migration.sql (table bank_accounts
+ colonne bank_name sur bank_loans) avant de fonctionner.
"""

import time

from telegram import Update
from telegram.ext import ContextTypes

from config import BANKS
from db import (
    get_or_create_player, 
    get_conn, 
    add_balance,
    log_transaction,
    create_bank_account,
    get_user_bank_accounts,
    get_bank_account,
    deposit_to_bank,
    withdraw_from_bank,
    get_user_total_bank_balance
)
from utils import fmt_money


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _find_bank(name_query: str):
    name_query = name_query.strip().lower()
    for b in BANKS:
        if b["name"].lower() == name_query or b["name"].lower().endswith(name_query):
            return b
    return None


def _get_account(conn, user_id: int, bank_name: str):
    """Récupère un compte bancaire depuis la nouvelle table."""
    return conn.execute(
        "SELECT * FROM user_bank_accounts WHERE user_id = ? AND bank_name = ?",
        (user_id, bank_name),
    ).fetchone()


def _apply_interest(conn, account, bank: dict, now: int):
    """Applique les intérêts composés dus depuis la dernière visite, retourne le nouveau solde."""
    interval = bank["interest_hours"] * 3600
    elapsed = now - account["last_interest_at"]
    periods = elapsed // interval
    if periods <= 0:
        return account["balance"]

    balance = account["balance"]
    for _ in range(int(periods)):
        balance += int(balance * bank["interest_rate"])

    new_last_interest_at = account["last_interest_at"] + periods * interval
    conn.execute(
        "UPDATE user_bank_accounts SET balance = ?, last_interest_at = ? WHERE user_id = ? AND bank_name = ?",
        (balance, new_last_interest_at, account["user_id"], bank["name"]),
    )
    return balance


def _rank_emoji(rank: int) -> str:
    return {1: "🥉", 2: "🥈", 3: "🥇", 4: "💠", 5: "💎"}.get(rank, "🏦")


def _get_balance_emoji(balance: int) -> str:
    """Retourne un emoji en fonction du montant."""
    if balance >= 1_000_000_000:
        return "👑"
    elif balance >= 100_000_000:
        return "💎"
    elif balance >= 10_000_000:
        return "🌟"
    elif balance >= 1_000_000:
        return "💫"
    elif balance >= 100_000:
        return "✨"
    elif balance >= 10_000:
        return "💰"
    elif balance >= 1_000:
        return "💵"
    else:
        return "🪙"


# ----------------------------------------------------------------------
# /banks
# ----------------------------------------------------------------------

async def banks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = ["🏦 *Banques disponibles*\n"]

    for b in BANKS:
        emoji = _rank_emoji(b["rank"])
        lines.append(f"{emoji} *Banque {b['name']}*  (Rang {b['rank']}/{len(BANKS)})")
        lines.append(f"  └ {b['tagline']}")
        lines.append(
            f"  └ Dépôt min : {fmt_money(b['min_deposit'])} · Max : {fmt_money(b['max_deposit'])}"
        )
        lines.append(f"  └ Intérêts  : +{b['interest_rate'] * 100:.1f}% / {b['interest_hours']}h")
        lines.append(
            f"  └ Prêt max  : {fmt_money(b['loan_max'])}  (taux {b['loan_rate'] * 100:.0f}%)"
        )
        lines.append("")

    lines.append("Utilisez /openbank [banque] pour ouvrir un compte.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ----------------------------------------------------------------------
# /openbank
# ----------------------------------------------------------------------

async def openbank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)

    if not context.args:
        bank_list = ", ".join(b["name"] for b in BANKS)
        await update.message.reply_text(
            f"Utilisation : /openbank nom_de_la_banque\n\nBanques : {bank_list}"
        )
        return

    query = " ".join(context.args)
    bank = _find_bank(query)
    if bank is None:
        await update.message.reply_text(
            "❌ Banque inconnue. Utilise /banks pour voir la liste."
        )
        return

    # Utiliser la nouvelle fonction
    if create_bank_account(user.id, bank["name"]):
        await update.message.reply_text(
            f"✅ Compte ouvert avec succès à *{bank['name']}* !\n"
            f"Tu peux ouvrir un compte dans plusieurs banques à la fois.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"❌ Tu as déjà un compte à *{bank['name']}*.",
            parse_mode="Markdown",
        )


# ----------------------------------------------------------------------
# /depositbank
# ----------------------------------------------------------------------

async def depositbank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if len(context.args) < 2:
        await update.message.reply_text("Utilisation : /depositbank banque montant")
        return

    bank = _find_bank(context.args[0])
    if bank is None:
        await update.message.reply_text(
            "❌ Banque inconnue. Utilise /banks pour voir la liste."
        )
        return

    try:
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return

    if amount <= 0:
        await update.message.reply_text("❌ Le montant doit être positif.")
        return

    if amount < bank["min_deposit"]:
        await update.message.reply_text(
            f"❌ Dépôt minimum à *{bank['name']}* : {fmt_money(bank['min_deposit'])}.",
            parse_mode="Markdown",
        )
        return

    if player["balance"] < amount:
        await update.message.reply_text("❌ Tu n'as pas assez de liquide.")
        return

    now = int(time.time())
    with get_conn() as conn:
        account = _get_account(conn, user.id, bank["name"])
        if account is None:
            await update.message.reply_text(
                f"❌ Tu n'as pas de compte à *{bank['name']}*. Utilise /openbank d'abord.",
                parse_mode="Markdown",
            )
            return

        current_balance = _apply_interest(conn, account, bank, now)

        if current_balance + amount > bank["max_deposit"]:
            await update.message.reply_text(
                f"❌ Plafond de dépôt atteint à *{bank['name']}* "
                f"({fmt_money(bank['max_deposit'])} max).",
                parse_mode="Markdown",
            )
            return

        # Utiliser add_balance pour retirer du liquide
        add_balance(user.id, -amount)
        log_transaction(user.id, None, amount, f"depositbank:{bank['name']}")
        # Mettre à jour le compte bancaire
        conn.execute(
            "UPDATE user_bank_accounts SET balance = balance + ? WHERE user_id = ? AND bank_name = ?",
            (amount, user.id, bank["name"]),
        )

    await update.message.reply_text(
        f"✅ Dépôt de {fmt_money(amount)} effectué à *{bank['name']}*.",
        parse_mode="Markdown",
    )


# ----------------------------------------------------------------------
# /withdrawbank
# ----------------------------------------------------------------------

async def withdrawbank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)

    if len(context.args) < 2:
        await update.message.reply_text("Utilisation : /withdrawbank banque montant")
        return

    bank = _find_bank(context.args[0])
    if bank is None:
        await update.message.reply_text(
            "❌ Banque inconnue. Utilise /banks pour voir la liste."
        )
        return

    try:
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return

    if amount <= 0:
        await update.message.reply_text("❌ Le montant doit être positif.")
        return

    now = int(time.time())
    with get_conn() as conn:
        account = _get_account(conn, user.id, bank["name"])
        if account is None:
            await update.message.reply_text(
                f"❌ Tu n'as pas de compte à *{bank['name']}*.",
                parse_mode="Markdown",
            )
            return

        current_balance = _apply_interest(conn, account, bank, now)

        if current_balance < amount:
            await update.message.reply_text("❌ Solde bancaire insuffisant.")
            return

        conn.execute(
            "UPDATE user_bank_accounts SET balance = balance - ? WHERE user_id = ? AND bank_name = ?",
            (amount, user.id, bank["name"]),
        )

    add_balance(user.id, amount)
    log_transaction(None, user.id, amount, f"withdrawbank:{bank['name']}")
    await update.message.reply_text(
        f"✅ Retrait de {fmt_money(amount)} effectué depuis *{bank['name']}*.",
        parse_mode="Markdown",
    )


# ----------------------------------------------------------------------
# /balancebank
# ----------------------------------------------------------------------

async def balancebank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)

    now = int(time.time())
    with get_conn() as conn:
        accounts = conn.execute(
            "SELECT * FROM user_bank_accounts WHERE user_id = ?", (user.id,)
        ).fetchall()

        if not accounts:
            await update.message.reply_text(
                "❌ Tu n'as encore aucun compte bancaire. Utilise /openbank d'abord."
            )
            return

        lines = ["🏦 *Tes comptes bancaires*\n"]
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        total = 0

        for account in accounts:
            bank = _find_bank(account["bank_name"])
            balance = _apply_interest(conn, account, bank, now) if bank else account["balance"]
            total += balance
            emoji = _rank_emoji(bank["rank"]) if bank else "🏦"
            balance_emoji = _get_balance_emoji(balance)
            
            # Barre de progression visuelle du solde
            bar_length = 10
            if total > 0:
                ratio = min(balance / total, 1.0)
                filled = int(ratio * bar_length)
            else:
                filled = 0
            progress_bar = "█" * filled + "░" * (bar_length - filled)
            
            lines.append(f"{emoji} *{account['bank_name']}*")
            lines.append(f"   └ {balance_emoji} {fmt_money(balance)} {progress_bar}")
            
            # Ajouter des informations supplémentaires si disponibles
            if bank:
                lines.append(f"   └ 📈 Taux: {bank['interest_rate'] * 100:.1f}% / {bank['interest_hours']}h")
            lines.append("")

        # Total avec un style différent
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 *Total en banque* : {fmt_money(total)}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ----------------------------------------------------------------------
# /loanbank
# ----------------------------------------------------------------------

async def loanbank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)

    if len(context.args) < 2:
        await update.message.reply_text("Utilisation : /loanbank banque montant")
        return

    bank = _find_bank(context.args[0])
    if bank is None:
        await update.message.reply_text(
            "❌ Banque inconnue. Utilise /banks pour voir la liste."
        )
        return

    try:
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return

    if amount <= 0 or amount > bank["loan_max"]:
        await update.message.reply_text(
            f"❌ Le prêt à *{bank['name']}* doit être entre 1 et {fmt_money(bank['loan_max'])}.",
            parse_mode="Markdown",
        )
        return

    with get_conn() as conn:
        account = _get_account(conn, user.id, bank["name"])
        if account is None:
            await update.message.reply_text(
                f"❌ Tu n'as pas de compte à *{bank['name']}*. Utilise /openbank d'abord.",
                parse_mode="Markdown",
            )
            return

        existing = conn.execute(
            "SELECT COUNT(*) as c FROM bank_loans WHERE user_id = ? AND bank_name = ? AND remaining > 0",
            (user.id, bank["name"]),
        ).fetchone()
        if existing["c"] > 0:
            await update.message.reply_text(
                f"❌ Tu as déjà un prêt en cours à *{bank['name']}*. "
                f"Rembourse-le avec /repaybank avant d'en prendre un autre.",
                parse_mode="Markdown",
            )
            return

        # Le montant à rembourser inclut les intérêts du prêt
        total_due = int(amount * (1 + bank["loan_rate"]))

        conn.execute(
            """INSERT INTO bank_loans (user_id, bank_name, amount, remaining, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (user.id, bank["name"], amount, total_due, int(time.time())),
        )

    add_balance(user.id, amount)
    log_transaction(None, user.id, amount, f"loanbank:{bank['name']}")
    await update.message.reply_text(
        f"✅ Prêt de {fmt_money(amount)} accordé par *{bank['name']}*.\n"
        f"À rembourser : {fmt_money(total_due)} (taux {bank['loan_rate'] * 100:.0f}%)\n"
        f"Utilise /repaybank {bank['name']} montant.",
        parse_mode="Markdown",
    )


# ----------------------------------------------------------------------
# /repaybank
# ----------------------------------------------------------------------

async def repaybank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if not context.args:
        await update.message.reply_text("Utilisation : /repaybank banque montant")
        return

    bank = _find_bank(context.args[0])
    if bank is None:
        await update.message.reply_text(
            "❌ Banque inconnue. Utilise /banks pour voir la liste."
        )
        return

    with get_conn() as conn:
        loan = conn.execute(
            "SELECT * FROM bank_loans WHERE user_id = ? AND bank_name = ? AND remaining > 0 "
            "ORDER BY created_at LIMIT 1",
            (user.id, bank["name"]),
        ).fetchone()

    if loan is None:
        await update.message.reply_text(
            f"✅ Tu n'as aucun prêt en cours à *{bank['name']}*.", parse_mode="Markdown"
        )
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            f"Utilisation : /repaybank {bank['name']} montant\n"
            f"Reste à rembourser : {fmt_money(loan['remaining'])}"
        )
        return

    try:
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return

    if amount <= 0:
        await update.message.reply_text("❌ Le montant doit être positif.")
        return

    if player["balance"] < amount:
        await update.message.reply_text("❌ Tu n'as pas assez de liquide.")
        return

    amount_to_apply = min(amount, loan["remaining"])

    add_balance(user.id, -amount_to_apply)
    log_transaction(user.id, None, amount_to_apply, f"repaybank:{bank['name']}")
    with get_conn() as conn:
        conn.execute(
            "UPDATE bank_loans SET remaining = remaining - ? WHERE loan_id = ?",
            (amount_to_apply, loan["loan_id"]),
        )

    new_remaining = loan["remaining"] - amount_to_apply
    if new_remaining <= 0:
        await update.message.reply_text(
            f"✅ Prêt à *{bank['name']}* entièrement remboursé ! ({fmt_money(amount_to_apply)})",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"✅ Remboursement de {fmt_money(amount_to_apply)} effectué à *{bank['name']}*.\n"
            f"Reste à rembourser : {fmt_money(new_remaining)}",
            parse_mode="Markdown",
        )


# ----------------------------------------------------------------------
# /loansbank
# ----------------------------------------------------------------------

async def loansbank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user

    with get_conn() as conn:
        loans = conn.execute(
            "SELECT * FROM bank_loans WHERE user_id = ? AND remaining > 0 ORDER BY created_at",
            (user.id,),
        ).fetchall()

    if not loans:
        await update.message.reply_text("✅ Tu n'as aucun prêt actif.")
        return

    lines = ["💳 *Tes prêts actifs*\n"]
    for loan in loans:
        bank_name = loan["bank_name"] or "?"
        lines.append(
            f"• *{bank_name}* — Prêt initial {fmt_money(loan['amount'])} — "
            f"Reste : {fmt_money(loan['remaining'])}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")