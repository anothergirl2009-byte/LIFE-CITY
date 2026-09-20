"""
LifeCity Bot - Abonnement obligatoire
by ANOTHERGIRL

Vérifie que le joueur est bien abonné au canal et membre du groupe LifeCity
avant de le laisser utiliser une commande. Se vérifie en live à chaque
commande (pas de cache) : si un joueur quitte le groupe/canal après coup,
il est re-bloqué automatiquement.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ApplicationHandlerStop
from telegram.error import TelegramError

from config import CHANNEL_USERNAME, GROUP_USERNAME, OWNER_ID

STATUTS_VALIDES = ("member", "administrator", "creator", "restricted")


async def _est_membre(bot, chat_id: str, user_id: int) -> bool:
    """Vérifie l'appartenance à un chat donné. Toute erreur (bot pas admin,
    chat introuvable, utilisateur jamais vu par le chat, etc.) est traitée
    comme "pas membre" par sécurité, plutôt que de laisser passer."""
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in STATUTS_VALIDES
    except TelegramError:
        return False


async def est_abonne(bot, user_id: int) -> tuple[bool, bool]:
    """Retourne (dans_le_groupe, dans_le_canal)."""
    groupe_ok = await _est_membre(bot, GROUP_USERNAME, user_id)
    canal_ok = await _est_membre(bot, CHANNEL_USERNAME, user_id)
    return groupe_ok, canal_ok


def _clavier_rejoindre() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Rejoindre le groupe", url=f"https://t.me/{GROUP_USERNAME.lstrip('@')}")],
        [InlineKeyboardButton("📢 Rejoindre le canal", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}")],
        [InlineKeyboardButton("✅ J'ai rejoint", callback_data="subcheck")],
    ])


TEXTE_BLOQUE = (
    "🔒 *Accès bloqué*\n\n"
    "Pour utiliser LifeCity, tu dois d'abord :\n"
    "👥 Rejoindre notre groupe\n"
    "📢 T'abonner à notre canal\n\n"
    "Une fois fait, appuie sur *✅ J'ai rejoint*."
)


async def subscription_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler global (à enregistrer en group=-1) qui bloque toute commande
    tant que le joueur n'est pas membre du groupe ET abonné au canal.
    /start reste toujours accessible pour que le joueur voie les boutons."""
    user = update.effective_user
    if user is None:
        return

    if user.id == OWNER_ID:
        return

    message = update.message
    callback = update.callback_query

    if message is not None:
        if not message.text or not message.text.startswith("/"):
            return
        commande = message.text.split()[0].split("@")[0].lower()
        if commande == "/start":
            return
    elif callback is not None:
        if callback.data == "subcheck":
            return
    else:
        return

    groupe_ok, canal_ok = await est_abonne(context.bot, user.id)
    if groupe_ok and canal_ok:
        return

    if message is not None:
        await message.reply_text(
            TEXTE_BLOQUE, reply_markup=_clavier_rejoindre(), parse_mode="Markdown"
        )
    else:
        await callback.answer(
            "🔒 Rejoins le groupe et le canal pour continuer !", show_alert=True
        )
        await callback.message.reply_text(
            TEXTE_BLOQUE, reply_markup=_clavier_rejoindre(), parse_mode="Markdown"
        )

    raise ApplicationHandlerStop


async def subcheck_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback du bouton '✅ J'ai rejoint' : re-vérifie l'abonnement."""
    query = update.callback_query
    user = query.from_user

    groupe_ok, canal_ok = await est_abonne(context.bot, user.id)

    if groupe_ok and canal_ok:
        await query.answer("✅ Merci ! Tu peux maintenant utiliser le bot.", show_alert=True)
        await query.edit_message_text("✅ Abonnement confirmé — tu peux utiliser toutes les commandes de LifeCity !")
        return

    manquant = []
    if not groupe_ok:
        manquant.append("le groupe")
    if not canal_ok:
        manquant.append("le canal")
    await query.answer(f"❌ Il te manque encore : {' et '.join(manquant)}.", show_alert=True)
