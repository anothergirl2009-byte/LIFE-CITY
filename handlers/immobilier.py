"""
LifeCity Bot - Immobilier
by ANOTHERGIRL
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from db import (
    get_biens_joueur,
    compter_biens,
    acheter_bien,
    collecter_loyers_db,
    vendre_bien,
)

TYPES_BIENS = {
    "studio":   {"nom": "🏠 Studio",        "prix": 5000,    "loyer": 450,    "intervalle": 3600},
    "appart":   {"nom": "🏢 Appartement",   "prix": 20000,   "loyer": 1800,   "intervalle": 3600},
    "maison":   {"nom": "🏡 Maison",        "prix": 60000,   "loyer": 5400,   "intervalle": 3600},
    "villa":    {"nom": "🏰 Villa de luxe", "prix": 200000,  "loyer": 18000,  "intervalle": 3600},
    "immeuble": {"nom": "🏙️ Immeuble",      "prix": 750000,  "loyer": 66000,  "intervalle": 3600},
}

MAX_BIENS_PAR_JOUEUR = 10
TAUX_REVENTE = 0.7


async def immobilier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    keyboard = []
    for key, infos in TYPES_BIENS.items():
        label = f"{infos['nom']} — {infos['prix']}€ ({infos['loyer']}€/h)"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"immo_acheter_{key}_{user_id}")])

    await update.message.reply_text(
        "🏘️ *Marché immobilier*\n\nChoisis un bien à acheter :",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def immo_acheter_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    # callback_data au format "immo_acheter_<type>_<user_id_du_menu>"
    payload = query.data.replace("immo_acheter_", "")
    type_bien, _, owner_id_str = payload.rpartition("_")
    owner_id = int(owner_id_str)

    if query.from_user.id != owner_id:
        await query.answer("❌ Ce n'est pas ton menu, tape /immobilier pour ouvrir le tien.", show_alert=True)
        return

    await query.answer()

    infos = TYPES_BIENS.get(type_bien)
    if not infos:
        await query.edit_message_text("❌ Type de bien inconnu.")
        return

    user_id = query.from_user.id

    if compter_biens(user_id) >= MAX_BIENS_PAR_JOUEUR:
        await query.edit_message_text(f"❌ Tu possèdes déjà le maximum de {MAX_BIENS_PAR_JOUEUR} biens.")
        return

    ok = acheter_bien(user_id, type_bien, infos["prix"])
    if not ok:
        await query.edit_message_text(f"❌ Solde insuffisant pour acheter {infos['nom']} ({infos['prix']}€).")
        return

    await query.edit_message_text(
        f"✅ Tu as acheté : {infos['nom']} pour {infos['prix']}€ !\nLoyer : {infos['loyer']}€ / heure."
    )


async def mesbiens(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    biens = get_biens_joueur(user_id)

    if not biens:
        await update.message.reply_text("Tu ne possèdes aucun bien immobilier. Utilise /immobilier pour en acheter un.")
        return

    lignes = ["🏘️ *Tes biens immobiliers*\n"]
    for bien in biens:
        infos = TYPES_BIENS.get(bien["type_bien"], {})
        lignes.append(f"#{bien['id']} — {infos.get('nom', bien['type_bien'])} ({infos.get('loyer', 0)}€/h)")

    lignes.append("\n💰 Utilise /loyer pour collecter tes revenus.")
    await update.message.reply_text("\n".join(lignes), parse_mode="Markdown")


async def loyer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    total, details, company_name = collecter_loyers_db(user_id, TYPES_BIENS)

    if total == 0:
        await update.message.reply_text("⏳ Aucun loyer disponible pour le moment. Reviens plus tard !")
        return

    if company_name:
        destination = f"🏦 Versés dans la trésorerie de *{company_name}*"
    else:
        destination = "💳 Versés sur ton compte"

    texte = f"💰 Loyers collectés : *+{total}€*\n\n" + "\n".join(details) + f"\n\n{destination}"
    await update.message.reply_text(texte, parse_mode="Markdown")


async def vendrebien(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage : /vendrebien <id>\n(vois tes ids avec /mesbiens)")
        return
    try:
        bien_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID invalide.")
        return

    user_id = update.effective_user.id
    biens = get_biens_joueur(user_id)
    bien = next((b for b in biens if b["id"] == bien_id), None)
    if bien is None:
        await update.message.reply_text("❌ Ce bien ne t'appartient pas.")
        return

    infos = TYPES_BIENS.get(bien["type_bien"])
    prix_revente = int(infos["prix"] * TAUX_REVENTE)

    ok = vendre_bien(user_id, bien_id, prix_revente)
    if not ok:
        await update.message.reply_text("❌ Ce bien ne t'appartient pas.")
        return

    await update.message.reply_text(f"✅ Bien vendu pour {prix_revente}€.")
