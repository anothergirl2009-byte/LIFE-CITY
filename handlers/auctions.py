"""
LifeCity Bot - Enchères & Objets
/bid montant — Enchérir sur l'enchère en cours
/expertise — Valeur de ton dernier objet
/myitems — Ton inventaire
/sellitem id prix — Mettre en vente
/shopitems — Objets en vente
/buyitem id — Acheter un objet
/open — Ouvrir un coffre mystère
"""

import random
import time

from telegram import Update
from telegram.ext import ContextTypes

from config import AUCTION_MIN_BID_INCREMENT
from db import get_or_create_player, get_conn, add_balance
from utils import fmt_money

MYSTERY_ITEMS = [
    ("Montre en or", 50_000, 500_000),
    ("Tableau ancien", 100_000, 1_000_000),
    ("Voiture de collection", 1_000_000, 10_000_000),
    ("Diamant brut", 200_000, 3_000_000),
    ("Sculpture rare", 80_000, 800_000),
    ("Guitare vintage", 30_000, 300_000),
]


# ============================================================
# ENCHÈRES
# ============================================================

async def bid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    with get_conn() as conn:
        auction = conn.execute(
            "SELECT * FROM auctions WHERE status = 'open' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

    if auction is None:
        await update.message.reply_text("❌ Aucune enchère en cours actuellement.")
        return

    if not context.args:
        await update.message.reply_text(
            f"Utilisation : /bid montant\n\n"
            f"🔨 Objet : {auction['item_name']}\n"
            f"💰 Enchère actuelle : {fmt_money(auction['current_bid'])}"
        )
        return

    try:
        amount = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return

    min_bid = auction["current_bid"] + AUCTION_MIN_BID_INCREMENT
    if amount < min_bid:
        await update.message.reply_text(f"❌ Ton enchère doit être au moins {fmt_money(min_bid)}.")
        return

    if player["balance"] < amount:
        await update.message.reply_text("❌ Solde insuffisant.")
        return

    with get_conn() as conn:
        conn.execute(
            "UPDATE auctions SET current_bid = ?, current_bidder = ? WHERE auction_id = ?",
            (amount, user.id, auction["auction_id"]),
        )

    await update.message.reply_text(
        f"🔨 Nouvelle enchère sur *{auction['item_name']}* : {fmt_money(amount)} par {player['first_name']} !",
        parse_mode="Markdown",
    )


# ============================================================
# OBJETS / INVENTAIRE
# ============================================================

async def myitems(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)

    with get_conn() as conn:
        items = conn.execute(
            "SELECT * FROM items WHERE owner_id = ? ORDER BY created_at DESC", (user.id,)
        ).fetchall()

    if not items:
        await update.message.reply_text("📦 Ton inventaire est vide. Essaie /open pour ouvrir un coffre !")
        return

    lines = ["📦 *Ton inventaire*\n"]
    for item in items:
        sale_info = f" (en vente : {fmt_money(item['for_sale_price'])})" if item["for_sale_price"] else ""
        lines.append(f"• #{item['item_id']} — {item['name']} — valeur {fmt_money(item['value'])}{sale_info}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def expertise(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)

    with get_conn() as conn:
        item = conn.execute(
            "SELECT * FROM items WHERE owner_id = ? ORDER BY created_at DESC LIMIT 1", (user.id,)
        ).fetchone()

    if item is None:
        await update.message.reply_text("❌ Tu n'as aucun objet à faire expertiser.")
        return

    await update.message.reply_text(
        f"🔍 *{item['name']}* est estimé à {fmt_money(item['value'])} !",
        parse_mode="Markdown",
    )


async def sellitem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)

    if len(context.args) < 2:
        await update.message.reply_text("Utilisation : /sellitem id prix")
        return

    try:
        item_id = int(context.args[0])
        price = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Valeurs invalides.")
        return

    with get_conn() as conn:
        item = conn.execute(
            "SELECT * FROM items WHERE item_id = ? AND owner_id = ?", (item_id, user.id)
        ).fetchone()

    if item is None:
        await update.message.reply_text("❌ Objet introuvable dans ton inventaire.")
        return

    if price <= 0:
        await update.message.reply_text("❌ Le prix doit être positif.")
        return

    with get_conn() as conn:
        conn.execute(
            "UPDATE items SET for_sale_price = ? WHERE item_id = ?", (price, item_id)
        )

    await update.message.reply_text(
        f"🏷️ *{item['name']}* est maintenant en vente pour {fmt_money(price)}.",
        parse_mode="Markdown",
    )


async def shopitems(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_conn() as conn:
        items = conn.execute(
            "SELECT * FROM items WHERE for_sale_price IS NOT NULL ORDER BY for_sale_price ASC LIMIT 20"
        ).fetchall()

    if not items:
        await update.message.reply_text("🛒 Aucun objet en vente actuellement.")
        return

    lines = ["🛒 *Objets en vente*\n"]
    for item in items:
        lines.append(f"• #{item['item_id']} — {item['name']} — {fmt_money(item['for_sale_price'])}")

    lines.append("\nUtilise /buyitem id pour acheter.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def buyitem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if not context.args:
        await update.message.reply_text("Utilisation : /buyitem id")
        return

    try:
        item_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID invalide.")
        return

    with get_conn() as conn:
        item = conn.execute(
            "SELECT * FROM items WHERE item_id = ? AND for_sale_price IS NOT NULL", (item_id,)
        ).fetchone()

    if item is None:
        await update.message.reply_text("❌ Objet introuvable ou plus en vente.")
        return

    if item["owner_id"] == user.id:
        await update.message.reply_text("❌ Tu ne peux pas acheter ton propre objet.")
        return

    price = item["for_sale_price"]
    if player["balance"] < price:
        await update.message.reply_text("❌ Solde insuffisant.")
        return

    add_balance(user.id, -price)
    add_balance(item["owner_id"], price)

    with get_conn() as conn:
        conn.execute(
            "UPDATE items SET owner_id = ?, for_sale_price = NULL WHERE item_id = ?",
            (user.id, item_id),
        )

    await update.message.reply_text(
        f"✅ Tu as acheté *{item['name']}* pour {fmt_money(price)} !",
        parse_mode="Markdown",
    )


# ============================================================
# COFFRE MYSTÈRE
# ============================================================

async def open_chest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)

    name, min_val, max_val = random.choice(MYSTERY_ITEMS)
    value = random.randint(min_val, max_val)

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO items (owner_id, name, value, created_at)
               VALUES (?, ?, ?, ?)""",
            (user.id, name, value, int(time.time())),
        )

    await update.message.reply_text(
        f"✨ Tu ouvres le coffre mystère...\n\n"
        f"🎁 Tu as obtenu : *{name}* (valeur estimée : {fmt_money(value)}) !",
        parse_mode="Markdown",
    )
