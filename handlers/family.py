"""
LifeCity Bot - Famille
/marry — Demander en mariage (60s pour répondre)
/divorce — Divorcer
/adopt — Adopter un membre
/disown — Désavouer un enfant
/friend — Ajouter un ami
/unfriend — Retirer un ami
/setfamilyname — Changer le nom de famille
/leave — Quitter la famille
/tree — Arbre généalogique
"""

import time
import datetime

from telegram import Update
from telegram.ext import ContextTypes

from types import SimpleNamespace

from db import (
    get_or_create_player, get_conn, update_player,
    get_spouse_id, create_marriage, delete_marriage,
    get_parents, get_children, add_family_link, remove_family_link,
    get_friends, add_friendship, remove_friendship,
    create_pending_request, get_pending_request, delete_pending_request,
    get_player_by_name_or_id,
)
from imagecards import generate_marriage_card, generate_family_tree_card, generate_friendship_card

REQUEST_TTL = 60  # secondes


def _get_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Renvoie l'objet User Telegram cible, via reply, via mention en entities,
    ou via un ID numérique tapé directement en argument (pas besoin de taguer).

    Gère deux cas de mention :
    - text_mention : Telegram fournit directement l'objet User (mention sans @username).
    - mention : Telegram ne fournit que le texte "@pseudo" (pas d'objet User), il faut
      donc retrouver le joueur dans notre base via son pseudo (déjà vu par le bot).
    """
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user

    if update.message.entities:
        for entity in update.message.entities:
            if entity.type == "text_mention":
                return entity.user
            if entity.type == "mention":
                mention_text = update.message.text[entity.offset: entity.offset + entity.length]
                row = get_player_by_name_or_id(mention_text)
                if row is not None:
                    return SimpleNamespace(
                        id=row["user_id"],
                        username=row["username"],
                        first_name=row["first_name"],
                    )

    # Pas de reply, pas de @mention (ex : pseudo avec des caractères spéciaux
    # que Telegram ne reconnaît pas comme une vraie mention) : accepte un ID
    # numérique tapé directement en premier argument.
    if context.args and context.args[0].isdigit():
        row = get_player_by_name_or_id(context.args[0])
        if row is not None:
            return SimpleNamespace(
                id=row["user_id"],
                username=row["username"],
                first_name=row["first_name"],
            )
    return None


def _player_label(row) -> str:
    return row["first_name"] or row["username"] or "Joueur inconnu"


def _get_player_data(uid: int):
    """Récupère les données complètes d'un joueur depuis la base de données."""
    if uid is None:
        return None
    with get_conn() as conn:
        return conn.execute("SELECT * FROM players WHERE user_id = ?", (uid,)).fetchone()


def _get_player_data_list(uid_list: list) -> list:
    """Récupère les données complètes d'une liste de joueurs."""
    if not uid_list:
        return []
    result = []
    for uid in uid_list:
        data = _get_player_data(uid)
        if data:
            result.append(data)
    return result


# ============================================================
# MARIAGE
# ============================================================

async def marry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user

    target = _get_target_user(update, context)
    if target is None:
        await update.message.reply_text(
            "Utilisation : réponds au message de la personne avec /marry, "
            "ou mentionne-la avec @pseudo.\n"
            "⚠️ Si tu mentionnes avec @pseudo, la personne doit déjà avoir utilisé le bot "
            "au moins une fois (ex: /start), sinon Telegram ne permet pas de la retrouver."
        )
        return

    if target.id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas te marier avec toi-même.")
        return

    player = get_or_create_player(user.id, user.username, user.first_name)
    target_player = get_or_create_player(target.id, target.username, target.first_name)

    def _is_polygame(p) -> bool:
        mode = (p["relationship_mode"] or "").lower()
        return "polygam" in mode

    if get_spouse_id(user.id) is not None and not _is_polygame(player):
        await update.message.reply_text(
            "❌ Tu es déjà marié(e). Passe en mode *Polygamie* avec /setmariage si tu veux "
            "pouvoir te marier à plusieurs personnes.",
            parse_mode="Markdown"
        )
        return
    if get_spouse_id(target.id) is not None and not _is_polygame(target_player):
        await update.message.reply_text(f"❌ {target.first_name} est déjà marié(e) (et pas en mode polygamie).")
        return

    create_pending_request("marry", user.id, target.id, REQUEST_TTL)

    await update.message.reply_text(
        f"💍 {user.first_name} demande {target.first_name} en mariage !\n"
        f"{target.first_name}, réponds avec /acceptmarry ou /refusemarry dans les 60 secondes."
    )


async def acceptmarry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    req = get_pending_request("marry", to_user=user.id)

    if req is None:
        await update.message.reply_text("❌ Tu n'as aucune demande en mariage en attente.")
        return

    user_player = get_or_create_player(user.id, user.username, user.first_name)
    proposer_player = get_or_create_player(req["from_user"], None, None)

    def _is_polygame(p) -> bool:
        mode = (p["relationship_mode"] or "").lower()
        return "polygam" in mode

    if get_spouse_id(user.id) is not None and not _is_polygame(user_player):
        await update.message.reply_text(
            "❌ Tu es déjà marié(e) entre-temps, tu ne peux pas accepter (sauf en mode Polygamie)."
        )
        return
    if get_spouse_id(req["from_user"]) is not None and not _is_polygame(proposer_player):
        await update.message.reply_text(
            "❌ Cette personne est déjà mariée entre-temps (et pas en mode polygamie)."
        )
        return

    delete_pending_request(req["request_id"])
    create_marriage(req["from_user"], user.id)

    with get_conn() as conn:
        spouse_row = conn.execute(
            "SELECT * FROM players WHERE user_id = ?", (req["from_user"],)
        ).fetchone()
    user_player = get_or_create_player(user.id, user.username, user.first_name)

    name1 = (spouse_row["first_name"] if spouse_row else None) or "Joueur"
    name2 = user_player["first_name"] or user.first_name
    date_str = datetime.datetime.now().strftime("%d/%m/%Y")

    card = generate_marriage_card(name1, name2, date_str)
    await update.message.reply_photo(
        photo=card,
        caption="💒 Félicitations ! Vous êtes maintenant mariés ! 🎉",
    )


async def refusemarry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    req = get_pending_request("marry", to_user=user.id)

    if req is None:
        await update.message.reply_text("❌ Tu n'as aucune demande en mariage en attente.")
        return

    delete_pending_request(req["request_id"])
    await update.message.reply_text("💔 Demande en mariage refusée.")


async def divorce(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    spouse_id = get_spouse_id(user.id)

    if spouse_id is None:
        await update.message.reply_text("❌ Tu n'es pas marié(e).")
        return

    delete_marriage(user.id)
    await update.message.reply_text("💔 Tu as divorcé. C'est fini.")


# ============================================================
# ADOPTION
# ============================================================

async def adopt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)

    target = _get_target_user(update, context)
    if target is None:
        await update.message.reply_text(
            "Utilisation : réponds au message de la personne avec /adopt."
        )
        return

    if target.id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas t'adopter toi-même.")
        return

    get_or_create_player(target.id, target.username, target.first_name)

    if user.id in get_parents(target.id):
        await update.message.reply_text("❌ Cette personne est déjà ton enfant.")
        return

    create_pending_request("adopt", user.id, target.id, REQUEST_TTL)

    await update.message.reply_text(
        f"👶 {user.first_name} veut adopter {target.first_name} !\n"
        f"{target.first_name}, réponds avec /acceptadopt ou /refuseadopt dans les 60 secondes."
    )


async def acceptadopt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    req = get_pending_request("adopt", to_user=user.id)

    if req is None:
        await update.message.reply_text("❌ Tu n'as aucune demande d'adoption en attente.")
        return

    delete_pending_request(req["request_id"])
    add_family_link(req["from_user"], user.id)

    await update.message.reply_text("🎉 Adoption réussie ! Bienvenue dans la famille !")


async def refuseadopt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    req = get_pending_request("adopt", to_user=user.id)

    if req is None:
        await update.message.reply_text("❌ Tu n'as aucune demande d'adoption en attente.")
        return

    delete_pending_request(req["request_id"])
    await update.message.reply_text("❌ Adoption refusée.")


async def disown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    target = _get_target_user(update, context)

    if target is None:
        await update.message.reply_text(
            "Utilisation : réponds au message de l'enfant avec /disown."
        )
        return

    children = get_children(user.id)
    if target.id not in children:
        await update.message.reply_text("❌ Cette personne n'est pas ton enfant.")
        return

    remove_family_link(user.id, target.id)
    await update.message.reply_text(f"😢 Tu as désavoué {target.first_name}.")


# ============================================================
# AMIS
# ============================================================

async def friend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)

    target = _get_target_user(update, context)
    if target is None:
        await update.message.reply_text(
            "Utilisation : réponds au message de la personne avec /friend."
        )
        return

    if target.id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas être ami avec toi-même.")
        return

    get_or_create_player(target.id, target.username, target.first_name)

    if target.id in get_friends(user.id):
        await update.message.reply_text("❌ Vous êtes déjà amis.")
        return

    create_pending_request("friend", user.id, target.id, REQUEST_TTL)

    await update.message.reply_text(
        f"🤝 {user.first_name} veut devenir ami avec {target.first_name} !\n"
        f"{target.first_name}, réponds avec /acceptfriend ou /refusefriend dans les 60 secondes."
    )


async def acceptfriend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    req = get_pending_request("friend", to_user=user.id)

    if req is None:
        await update.message.reply_text("❌ Tu n'as aucune demande d'amitié en attente.")
        return

    delete_pending_request(req["request_id"])
    add_friendship(req["from_user"], user.id)

    with get_conn() as conn:
        friend_row = conn.execute(
            "SELECT * FROM players WHERE user_id = ?", (req["from_user"],)
        ).fetchone()
    user_player = get_or_create_player(user.id, user.username, user.first_name)

    name1 = (friend_row["first_name"] if friend_row else None) or "Joueur"
    name2 = user_player["first_name"] or user.first_name
    date_str = datetime.datetime.now().strftime("%d/%m/%Y")

    card = generate_friendship_card(name1, name2, date_str)
    await update.message.reply_photo(
        photo=card,
        caption="🎉 Vous êtes maintenant amis !",
    )


async def refusefriend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    req = get_pending_request("friend", to_user=user.id)

    if req is None:
        await update.message.reply_text("❌ Tu n'as aucune demande d'amitié en attente.")
        return

    delete_pending_request(req["request_id"])
    await update.message.reply_text("❌ Demande d'amitié refusée.")


async def unfriend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    target = _get_target_user(update, context)

    if target is None:
        await update.message.reply_text(
            "Utilisation : réponds au message de la personne avec /unfriend."
        )
        return

    if target.id not in get_friends(user.id):
        await update.message.reply_text("❌ Vous n'êtes pas amis.")
        return

    remove_friendship(user.id, target.id)
    await update.message.reply_text(f"💔 Tu n'es plus ami avec {target.first_name}.")


# ============================================================
# NOM DE FAMILLE / QUITTER
# ============================================================

async def setfamilyname(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)

    if not context.args:
        await update.message.reply_text("Utilisation : /setfamilyname Nom")
        return

    name = " ".join(context.args).strip()
    if len(name) > 32:
        await update.message.reply_text("❌ Le nom de famille doit faire 32 caractères max.")
        return

    update_player(user.id, family_name=name)
    await update.message.reply_text(f"✅ Ton nom de famille est maintenant : *{name}*", parse_mode="Markdown")


async def leave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)

    update_player(user.id, family_name=None)
    delete_marriage(user.id)

    # Retire tous les liens parent/enfant impliquant ce joueur
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM family_links WHERE parent_id = ? OR child_id = ?",
            (user.id, user.id),
        )

    await update.message.reply_text("👋 Tu as quitté ta famille.")


# ============================================================
# ARBRE GÉNÉALOGIQUE
# ============================================================

async def tree(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /tree — Affiche l'arbre généalogique avec les photos de profil.
    """
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    # Récupération des IDs
    spouse_id = get_spouse_id(user.id)
    parents_ids = get_parents(user.id)
    children_ids = get_children(user.id)
    friends_ids = get_friends(user.id)

    # Récupération des données complètes
    spouse_data = _get_player_data(spouse_id) if spouse_id else None
    parents_data = _get_player_data_list(parents_ids) if parents_ids else []
    children_data = _get_player_data_list(children_ids) if children_ids else []
    friends_data = _get_player_data_list(friends_ids) if friends_ids else []

    try:
        # Génération de la carte de l'arbre généalogique avec les données complètes
        card = await generate_family_tree_card(
            player_data=player,
            spouse_data=spouse_data,
            parents_data=parents_data,
            children_data=children_data,
            friends_data=friends_data,
            bot=context.bot,
        )

        await update.message.reply_photo(
            photo=card,
            caption="🌳 *Arbre généalogique de la famille*\n\n"
                   "Les photos de profil de chaque membre sont affichées.",
            parse_mode="Markdown"
        )
    except Exception as e:
        # Fallback si la génération de la carte échoue
        error_msg = str(e)
        await update.message.reply_text(
            f"❌ Erreur lors de la génération de l'arbre : {error_msg}",
            parse_mode=None
        )
        
        # Fallback : afficher les informations textuelles
        text_lines = ["🌳 *Arbre généalogique*\n"]
        
        # Informations sur le joueur
        text_lines.append(f"👤 *{player['first_name']}*")
        if player['family_name']:
            text_lines.append(f"   └ Nom de famille : {player['family_name']}")
        
        # Conjoint
        if spouse_data:
            text_lines.append(f"💑 Marié(e) avec : *{spouse_data['first_name']}*")
        
        # Parents
        if parents_data:
            parent_names = [p['first_name'] for p in parents_data]
            text_lines.append(f"👨‍👩‍👦 Parents : {', '.join(parent_names)}")
        
        # Enfants
        if children_data:
            children_names = [c['first_name'] for c in children_data]
            text_lines.append(f"👶 Enfants : {', '.join(children_names)}")
        
        # Amis
        if friends_data:
            friend_names = [f['first_name'] for f in friends_data]
            text_lines.append(f"🤝 Amis : {', '.join(friend_names)}")
        
        if not spouse_data and not parents_data and not children_data and not friends_data:
            text_lines.append("📭 Aucune connexion familiale enregistrée.")
        
        await update.message.reply_text(
            "\n".join(text_lines),
            parse_mode="Markdown"
        )


async def tree_text_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Version textuelle de l'arbre généalogique (fallback).
    """
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    spouse_id = get_spouse_id(user.id)
    parents = get_parents(user.id)
    children = get_children(user.id)
    friends = get_friends(user.id)

    def label_for(uid: int) -> str:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM players WHERE user_id = ?", (uid,)).fetchone()
        return _player_label(row) if row else "Inconnu"

    spouse_name = label_for(spouse_id) if spouse_id else None
    parent_names = [label_for(p) for p in parents]
    children_names = [label_for(c) for c in children]
    friend_names = [label_for(f) for f in friends]

    lines = ["🌳 *Arbre généalogique*\n"]
    
    lines.append(f"👤 *{player['first_name']}*")
    if player['family_name']:
        lines.append(f"   └ Nom de famille : {player['family_name']}")
    
    if spouse_name:
        lines.append(f"💑 Marié(e) avec : *{spouse_name}*")
    
    if parent_names:
        lines.append(f"👨‍👩‍👦 Parents : {', '.join(parent_names)}")
    
    if children_names:
        lines.append(f"👶 Enfants : {', '.join(children_names)}")
    
    if friend_names:
        lines.append(f"🤝 Amis : {', '.join(friend_names)}")
    
    if not spouse_name and not parent_names and not children_names and not friend_names:
        lines.append("📭 Aucune connexion familiale enregistrée.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")