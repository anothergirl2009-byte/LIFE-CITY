"""
LifeCity Bot - Panel Admin / Owner
Réservé exclusivement à OWNER_ID (config.py) et aux administrateurs nommés.

/owner — Panel owner (liste des commandes)
/addmoney user_id montant — Ajouter de l'argent
/removemoney user_id montant — Retirer de l'argent
/ban user_id [raison] — Bannir un joueur (bloque toutes les commandes)
/unban user_id — Débannir un joueur
/dissoudre nom_entreprise — Dissoudre une entreprise
/save — Archive tout le projet et l'envoie sur Telegram (admin/owner only)
"""

from functools import wraps
from types import SimpleNamespace

import asyncio
import io
import os
import zipfile
import tempfile
import shutil
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import Forbidden, BadRequest

from config import OWNER_ID
from db import (
    get_or_create_player, get_player_by_name_or_id, find_player_by_identifier, update_player,
    add_balance, get_company_by_name, get_company_by_id, delete_company,
    add_company_log, get_conn, update_company_treasury,
    get_company_shares, get_user_shares, set_user_shares,
    get_user_total_bank_balance, get_user_bank_accounts, create_bank_account,
    log_transaction, set_company_last_recruitment_ad, get_state_treasury, add_state_treasury,
    CITY_NAME, get_all_cities, get_city, get_player_by_id,
    open_election, get_open_election, get_pending_election, close_election_voting,
    is_candidate, close_election, remove_candidate,
    install_mayor, remove_mayor, add_city_treasury, withdraw_city_treasury,
    add_candidate, set_election_commission, get_election_commission,
    get_latest_election, get_election_votes_detail,
)
from handlers.cities import _build_election_view
import time
from utils import fmt_money

try:
    from handlers.education import SECTORS as EDU_SECTORS, TIERS as EDU_TIERS
except ImportError:
    from education import SECTORS as EDU_SECTORS, TIERS as EDU_TIERS


# ── Décorateurs ──────────────────────────────────────────────────────────────

def owner_only(func):
    """Décorateur : uniquement l'owner peut utiliser cette commande."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != OWNER_ID:
            return  # aucune réponse
        return await func(update, context)
    return wrapper


def admin_or_owner_only(func):
    """Décorateur : autorise l'owner ET les administrateurs."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        # L'owner a toujours accès
        if user_id == OWNER_ID:
            return await func(update, context)
        
        # Vérifier si l'utilisateur est admin
        with get_conn() as conn:
            row = conn.execute(
                "SELECT is_admin FROM players WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row and row["is_admin"] == 1:
                return await func(update, context)
        
        return  # aucune réponse
    return wrapper


def owner_or_commission_only(func):
    """Décorateur : autorise l'owner ET le président de la commission électorale
    (nommé via /nommercommission). Le président peut ouvrir/clôturer le vote,
    mais ne peut jamais trancher l'élection ni révoquer un maire."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id == OWNER_ID or user_id == get_election_commission():
            return await func(update, context)
        return  # aucune réponse
    return wrapper


# ── Commandes Admin ──────────────────────────────────────────────────────────

# Chemin local de la vidéo d'intro du panel owner. Place le fichier vidéo à
# côté de admin.py (ou ajuste le chemin) puis relance le bot.
OWNER_PANEL_VIDEO_PATH = os.path.join(os.path.dirname(__file__), "owner_panel_video.mp4")

# Cache du file_id Telegram : après le tout premier envoi, Telegram nous
# donne un file_id réutilisable. On s'en sert pour les envois suivants afin
# de ne PAS re-uploader le fichier vidéo à chaque fois que l'owner tape
# /owner (uploader à chaque fois serait lent et gaspillerait de la bande
# passante).
_owner_panel_video_file_id: str | None = None


async def _send_owner_panel_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Envoie la vidéo d'intro du panel owner si le fichier existe."""
    global _owner_panel_video_file_id

    if not os.path.isfile(OWNER_PANEL_VIDEO_PATH):
        return  # pas de vidéo configurée, on ignore silencieusement

    try:
        if _owner_panel_video_file_id:
            msg = await update.message.reply_video(_owner_panel_video_file_id)
        else:
            with open(OWNER_PANEL_VIDEO_PATH, "rb") as f:
                msg = await update.message.reply_video(f)
        if msg.video:
            _owner_panel_video_file_id = msg.video.file_id
    except Exception:
        pass  # on ne bloque jamais l'affichage du panel à cause de la vidéo


@owner_only
async def owner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_owner_panel_video(update, context)
    text = (
        "👑 *Panel Owner — LifeCity*\n\n"
        "💰 /addmoney id montant\n"
        "💸 /removemoney id montant\n"
        "🏦 /addbanque id montant — Ajouter en banque\n"
        "🏦 /removebanque id montant — Retirer de la banque\n"
        "🏢 /addboite nom_entreprise montant — Ajouter à la trésorerie d'une entreprise\n"
        "🏢 /removeboite nom_entreprise montant — Retirer de la trésorerie d'une entreprise\n"
        "🔨 /ban id [raison]\n"
        "✅ /unban id\n"
        "📋 /listeban [page] — Liste des joueurs bannis\n"
        "🏢 /dissoudre nom_entreprise\n"
        "👑 /forcenommer nom_entreprise poste (en réponse au joueur, ou /forcenommer nom_joueur nom_entreprise poste)\n"
        "🎓 /setdiplome id secteur palier — Accorder un diplôme\n"
        "🎓 /retirerdiplome id secteur palier — Retirer un diplôme (dissout l'entreprise si PDG, renvoie l'employé s'il n'a plus aucun diplôme)\n"
        "📢 /resetrecrutement nom_entreprise — Lever le cooldown d'annonce de recrutement\n"
        "🏛️ /etatresor — Voir le solde du fonds État (impôts quotidiens)\n"
        "🏛️ /utiliserimpots id_ou_pseudo montant [raison] — Dépenser le fonds État\n"
        "🔓 /debannirtous — Débannir tous les joueurs bannis d'un coup\n\n"
        "🏙️ *Mairie de LIFECITY*\n"
        "🏛️ /villescaisses — Voir la caisse municipale\n"
        "💰 /addcaisseville montant — Ajouter à la caisse municipale\n"
        "💸 /removecaisseville montant — Retirer de la caisse municipale\n"
        "🗳️ /ouvrirelection — Ouvrir une élection municipale (48h)\n"
        "🔒 /cloturerelection — Clôturer le vote (sans désigner de vainqueur)\n"
        "👑 /trancherelection id_ou_pseudo — Désigner le maire élu (owner uniquement)\n"
        "🗳️ /votesmaire — Voir le détail des votes (qui a voté pour qui)\n"
        "⛔ /revoquermaire — Révoquer le maire en poste et relancer une élection\n"
        "📢 /lancervote @c1 @c2 [...] — Poster le vote à boutons dans ce chat (groupe compris)\n"
        "🧑‍⚖️ /nommercommission id_ou_pseudo — Nommer le président de la commission électorale\n"
        "🚫 /retirercandidat id_ou_pseudo — Retirer un joueur de l'élection en cours\n\n"
        "💹 *Économie de masse (tous les joueurs)*\n"
        "💰 /addmoneyall montant — Ajouter au solde de tous\n"
        "💸 /removemoneyall montant — Retirer du solde de tous\n"
        "🏦 /addbanqueall montant — Ajouter en banque à tous\n"
        "🏦 /removebanqueall montant — Retirer en banque à tous\n"
        "🔄 /resetmoneyall [montant] — Fixer le solde de tous (0 par défaut)\n"
        "🔄 /resetbanqueall [montant] — Fixer la banque de tous (0 par défaut)\n"
        "📊 /fixparts nom_entreprise — Normaliser les parts à 100%\n\n"
        "👥 *Gestion des administrateurs*\n"
        "👑 /setadmin id — Nommer un admin\n"
        "❌ /unsetadmin id — Retirer un admin\n"
        "📋 /listadmins — Liste des admins\n\n"
        "👥 *Gestion des joueurs*\n"
        "📋 /listejoueurs [page] — Liste tous les joueurs\n"
        "🎓 /listediplomes [page] — Liste des diplômes de tous les joueurs\n"
        "🔍 /recherchejoueur <nom> — Rechercher un joueur\n\n"
        "📜 *Historique & Surveillance*\n"
        "🕵️ /historique [nb|tout] — Dernières transactions (ou toutes)\n"
        "👤 /histojoueur id [nb] — Transactions d'un joueur\n"
        "🐋 /baleines [nb] — Joueurs les plus riches\n"
        "📊 /statsbot — Statistiques globales du bot\n"
        "🚨 /suspectfraude [seuil] — Joueurs au solde anormal\n"
        "🛑 /freezejoueur id — Bloquer les gains d'un joueur\n"
        "✅ /unfreezejoueur id — Débloquer un joueur\n\n"
        "⛓️ *Prison & Justice*\n"
        "🔓 /liberer id — Libérer un joueur de prison\n"
        "⛓️ /prolonger id [minutes] — Prolonger une peine de prison\n"
        "⛓️ /aggraver id [minutes] — Aggraver une peine de prison\n"
        "⚖️ /alleger id [minutes] — Alléger une peine de prison\n"
        "🔍 /voirprison — Voir tous les prisonniers\n"
        "⚖️ /voirproces — Voir les procès en cours\n"
        "🧹 /clearjail — Libérer tous les prisonniers\n"
        "⚖️ /verdict [user_id] [guilty/not_guilty] — Rendre un verdict\n\n"
        "⚙️ *Configuration*\n"
        "⚙️ /configurer_rank [rank] [prix] — Configurer les prix des rangs\n"
        "👥 /administrateurs — Voir les administrateurs\n"
        "⏸️ /pause — Mettre le bot en pause\n"
        "▶️ /resume — Reprendre le bot\n"
        "📢 /annonce <texte> — Envoyer une annonce à tous\n"
        "💾 /save — 📦 Archiver tout le projet et l'envoyer sur Telegram"
    )
    try:
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        if "Can't parse entities" in str(e):
            await update.message.reply_text(text, parse_mode=None)
        else:
            raise e


# ── Gestion des administrateurs ─────────────────────────────────────────────

@owner_only
async def setadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /setadmin user_id
    Nomme un autre joueur administrateur du bot.
    Usage : /setadmin user_id
    """
    if not context.args:
        await update.message.reply_text("Usage : /setadmin user_id")
        return

    identifier = context.args[0]
    player = get_player_by_name_or_id(identifier)
    if player is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    # Vérifier si le joueur est déjà admin
    if player["is_admin"] == 1:
        await update.message.reply_text(
            f"⚠️ *{player['first_name']}* est déjà administrateur.",
            parse_mode="Markdown"
        )
        return

    # Rendre admin
    update_player(player["user_id"], is_admin=1)
    
    await update.message.reply_text(
        f"✅ *{player['first_name']}* (ID `{player['user_id']}`) a été nommé administrateur du bot !\n\n"
        f"Il peut maintenant utiliser les commandes admin suivantes :\n"
        f"• /addmoney, /removemoney\n"
        f"• /addbanque, /removebanque\n"
        f"• /ban, /unban\n"
        f"• /freezejoueur, /unfreezejoueur\n"
        f"• /liberer, /prolonger, /aggraver, /alleger\n"
        f"• /voirprison, /clearjail, /verdict\n"
        f"• /listejoueurs, /recherchejoueur",
        parse_mode="Markdown"
    )


@owner_only
async def unsetadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /unsetadmin user_id
    Retire le statut d'administrateur à un joueur.
    Usage : /unsetadmin user_id
    """
    if not context.args:
        await update.message.reply_text("Usage : /unsetadmin user_id")
        return

    identifier = context.args[0]
    player = get_player_by_name_or_id(identifier)
    if player is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    # Vérifier si le joueur est admin
    if player["is_admin"] != 1:
        await update.message.reply_text(
            f"⚠️ *{player['first_name']}* n'est pas administrateur.",
            parse_mode="Markdown"
        )
        return

    # Retirer le statut admin
    update_player(player["user_id"], is_admin=0)
    
    await update.message.reply_text(
        f"✅ *{player['first_name']}* (ID `{player['user_id']}`) n'est plus administrateur.",
        parse_mode="Markdown"
    )


@admin_or_owner_only
async def listadmins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /listadmins
    Liste tous les administrateurs du bot.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT user_id, first_name, username, created_at
               FROM players
               WHERE is_admin = 1
               ORDER BY user_id"""
        ).fetchall()

    if not rows:
        await update.message.reply_text("👥 Aucun administrateur enregistré (sauf l'owner).")
        return

    lines = ["👑 *Liste des administrateurs*\n"]
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"👤 Owner : `{OWNER_ID}` (propriétaire)\n")
    
    for r in rows:
        name = r["first_name"] or r["username"] or f"#{r['user_id']}"
        lines.append(f"🆔 `{r['user_id']}` — {name}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Fonctions utilitaires ──────────────────────────────────────────────────

def _get_all_group_ids() -> list:
    """
    Récupère tous les IDs des groupes actifs depuis la base de données.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT chat_id FROM active_groups ORDER BY last_seen DESC"
        ).fetchall()
    return [row["chat_id"] for row in rows]


def _get_all_player_ids() -> list:
    """
    Récupère tous les user_id des joueurs ayant déjà fait /start en PV.
    """
    with get_conn() as conn:
        rows = conn.execute("SELECT user_id FROM players").fetchall()
    return [row["user_id"] for row in rows]


# ── Commandes Admin (accessibles aux admins et à l'owner) ──────────────────

@admin_or_owner_only
async def addmoney(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /addmoney user_id montant")
        return

    identifier, amount_str = context.args[0], context.args[1]
    try:
        amount = int(amount_str)
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return

    player = get_player_by_name_or_id(identifier)
    if player is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    add_balance(player["user_id"], amount)
    log_transaction(None, player["user_id"], amount, f"admin_addmoney ({update.effective_user.first_name})")
    await update.message.reply_text(
        f"✅ {fmt_money(amount)} ajoutés au compte de {player['first_name']} ({player['user_id']}).",
        parse_mode="Markdown",
    )


@admin_or_owner_only
async def removemoney(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /removemoney user_id montant")
        return

    identifier, amount_str = context.args[0], context.args[1]
    try:
        amount = int(amount_str)
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return

    player = get_player_by_name_or_id(identifier)
    if player is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    add_balance(player["user_id"], -amount)
    log_transaction(player["user_id"], None, amount, f"admin_removemoney ({update.effective_user.first_name})")
    await update.message.reply_text(
        f"✅ {fmt_money(amount)} retirés du compte de {player['first_name']} ({player['user_id']}).",
        parse_mode="Markdown",
    )


DEFAULT_BANK_NAME = "Banque Centrale"


def _pick_bank_account(user_id: int) -> str:
    """Renvoie le nom de la banque à utiliser pour /addbanque et /removebanque :
    la première banque où le joueur a déjà un compte, sinon on lui en crée une par défaut."""
    accounts = get_user_bank_accounts(user_id)
    if accounts:
        return accounts[0]["bank_name"]
    create_bank_account(user_id, DEFAULT_BANK_NAME)
    return DEFAULT_BANK_NAME


@admin_or_owner_only
async def addbanque(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /addbanque user_id montant
    Ajoute de l'argent directement dans le compte en banque d'un joueur.
    """
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /addbanque user_id montant")
        return

    identifier, amount_str = context.args[0], context.args[1]
    try:
        amount = int(amount_str)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return

    player = get_player_by_name_or_id(identifier)
    if player is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    bank_name = _pick_bank_account(player["user_id"])
    with get_conn() as conn:
        conn.execute(
            "UPDATE user_bank_accounts SET balance = balance + ? WHERE user_id = ? AND bank_name = ?",
            (amount, player["user_id"], bank_name),
        )
    new_bank = get_user_total_bank_balance(player["user_id"])
    log_transaction(None, player["user_id"], amount, f"admin_addbanque:{bank_name} ({update.effective_user.first_name})")

    await update.message.reply_text(
        f"✅ {fmt_money(amount)} ajoutés à la banque de {player['first_name']} ({player['user_id']}) — compte *{bank_name}*.\n"
        f"🏦 Nouveau solde banque total : {fmt_money(new_bank)}",
        parse_mode="Markdown",
    )


@admin_or_owner_only
async def removebanque(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /removebanque user_id montant
    Retire de l'argent directement du compte en banque d'un joueur.
    """
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /removebanque user_id montant")
        return

    identifier, amount_str = context.args[0], context.args[1]
    try:
        amount = int(amount_str)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return

    player = get_player_by_name_or_id(identifier)
    if player is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    current_total = get_user_total_bank_balance(player["user_id"])
    amount = min(amount, current_total)

    remaining = amount
    with get_conn() as conn:
        accounts = conn.execute(
            "SELECT bank_name, balance FROM user_bank_accounts WHERE user_id = ? ORDER BY balance DESC",
            (player["user_id"],),
        ).fetchall()
        for acc in accounts:
            if remaining <= 0:
                break
            take = min(acc["balance"], remaining)
            if take > 0:
                conn.execute(
                    "UPDATE user_bank_accounts SET balance = balance - ? WHERE user_id = ? AND bank_name = ?",
                    (take, player["user_id"], acc["bank_name"]),
                )
                remaining -= take

    new_bank = get_user_total_bank_balance(player["user_id"])
    if amount > 0:
        log_transaction(player["user_id"], None, amount, f"admin_removebanque ({update.effective_user.first_name})")
    await update.message.reply_text(
        f"✅ {fmt_money(amount)} retirés de la banque de {player['first_name']} ({player['user_id']}).\n"
        f"🏦 Nouveau solde banque total : {fmt_money(new_bank)}",
        parse_mode="Markdown",
    )


@admin_or_owner_only
async def addboite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /addboite nom_entreprise montant
    Ajoute de l'argent directement dans la trésorerie d'une entreprise.
    """
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /addboite nom_entreprise montant")
        return

    amount_str = context.args[-1]
    name = " ".join(context.args[:-1])
    try:
        amount = int(amount_str)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return

    company = get_company_by_name(name)
    if company is None:
        await update.message.reply_text("❌ Entreprise introuvable.")
        return

    update_company_treasury(company["company_id"], amount)
    add_company_log(company["company_id"], f"[ADMIN] +{fmt_money(amount)} ajoutés par {update.effective_user.first_name}")

    await update.message.reply_text(
        f"✅ {fmt_money(amount)} ajoutés à la trésorerie de *{company['name']}*.",
        parse_mode="Markdown",
    )


@admin_or_owner_only
async def removeboite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /removeboite nom_entreprise montant
    Retire de l'argent de la trésorerie d'une entreprise.
    """
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /removeboite nom_entreprise montant")
        return

    amount_str = context.args[-1]
    name = " ".join(context.args[:-1])
    try:
        amount = int(amount_str)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return

    company = get_company_by_name(name)
    if company is None:
        await update.message.reply_text("❌ Entreprise introuvable.")
        return

    amount = min(amount, company["treasury"] or 0)
    update_company_treasury(company["company_id"], -amount)
    add_company_log(company["company_id"], f"[ADMIN] -{fmt_money(amount)} retirés par {update.effective_user.first_name}")

    await update.message.reply_text(
        f"✅ {fmt_money(amount)} retirés de la trésorerie de *{company['name']}*.",
        parse_mode="Markdown",
    )


@admin_or_owner_only
async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage : /ban user_id [raison]")
        return

    identifier = context.args[0]
    reason = " ".join(context.args[1:]) or "Aucune raison fournie"

    player = get_player_by_name_or_id(identifier)
    if player is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    update_player(player["user_id"], banned=1, ban_reason=reason)

    await update.message.reply_text(
        f"🔨 Joueur {player['first_name']} ({player['user_id']}) banni.\n"
        f"📝 Raison : {reason}",
        parse_mode="Markdown",
    )


@admin_or_owner_only
async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage : /unban user_id")
        return

    identifier = context.args[0]
    player = get_player_by_name_or_id(identifier)
    if player is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    update_player(player["user_id"], banned=0, ban_reason=None)

    await update.message.reply_text(
        f"✅ Joueur {player['first_name']} ({player['user_id']}) débanni.",
        parse_mode="Markdown",
    )


@admin_or_owner_only
async def listeban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /listeban [page]
    Liste tous les joueurs bannis avec leur ID, pseudo et raison.
    Usage : /listeban [page]
    """
    # Nombre de bannis par page
    per_page = 15

    # Récupérer le numéro de page (par défaut 1)
    page = 1
    if context.args:
        try:
            page = int(context.args[0])
            if page < 1:
                page = 1
        except ValueError:
            pass

    offset = (page - 1) * per_page

    with get_conn() as conn:
        # Compter le nombre total de joueurs bannis
        total = conn.execute(
            "SELECT COUNT(*) as count FROM players WHERE banned = 1"
        ).fetchone()["count"]

        if total == 0:
            await update.message.reply_text("✅ Aucun joueur banni actuellement.")
            return

        rows = conn.execute(
            """SELECT user_id, first_name, username, ban_reason
               FROM players
               WHERE banned = 1
               ORDER BY user_id
               LIMIT ? OFFSET ?""",
            (per_page, offset)
        ).fetchall()

    if not rows:
        await update.message.reply_text(
            f"📭 Page {page} vide. Total de bannis : {total}."
        )
        return

    total_pages = (total + per_page - 1) // per_page

    lines = []
    lines.append("🔨 *LISTE DES JOUEURS BANNIS*")
    lines.append(f"_Page {page}/{total_pages} — {total} banni(s) au total_\n")

    start_index = offset + 1
    for i, row in enumerate(rows):
        pseudo = row["first_name"] or "?"
        username = f" (@{row['username']})" if row["username"] else ""
        reason = row["ban_reason"] or "Aucune raison fournie"
        lines.append(
            f"{start_index + i}. *{pseudo}*{username} — `{row['user_id']}`\n"
            f"   📝 {reason}"
        )

    if total_pages > 1:
        if page < total_pages:
            lines.append(f"\n➡️ Page suivante : /listeban {page + 1}")
        if page > 1:
            lines.append(f"⬅️ Page précédente : /listeban {page - 1}")

    text = "\n".join(l for l in lines if l)

    try:
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        if "Can't parse entities" in str(e):
            await update.message.reply_text(text, parse_mode=None)
        else:
            raise e


@admin_or_owner_only
async def debannirtous(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/debannirtous — Débannit TOUS les joueurs bannis d'un coup."""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM players WHERE banned = 1").fetchone()["c"]
        if total == 0:
            await update.message.reply_text("✅ Aucun joueur banni actuellement.")
            return
        conn.execute("UPDATE players SET banned = 0, ban_reason = NULL WHERE banned = 1")

    await update.message.reply_text(f"✅ {total} joueur(s) débanni(s).")


@admin_or_owner_only
async def dissoudre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage : /dissoudre nom_entreprise")
        return

    name = " ".join(context.args)
    company = get_company_by_name(name)
    if company is None:
        await update.message.reply_text("❌ Entreprise introuvable.")
        return

    treasury = company["treasury"] or 0
    shares = get_company_shares(company["company_id"])
    payouts = []
    if treasury > 0 and shares:
        for s in shares:
            payout = (treasury * s["shares"]) // 100
            if payout <= 0:
                continue
            add_balance(s["user_id"], payout)
            payouts.append((s["user_id"], payout))

    delete_company(company["company_id"])

    await update.message.reply_text(
        f"🏢 Entreprise *{name}* dissoute (action admin).\n"
        + (f"🏦 Trésorerie redistribuée aux actionnaires : {fmt_money(treasury)}"
           if payouts else "🏦 Aucune trésorerie à redistribuer."),
        parse_mode="Markdown"
    )

    for shareholder_id, payout in payouts:
        try:
            await context.bot.send_message(
                chat_id=shareholder_id,
                text=(
                    f"💥 *{name}* a été dissoute (décision admin).\n"
                    f"💰 Tu as reçu *{fmt_money(payout)}* correspondant à tes parts."
                ),
                parse_mode="Markdown",
            )
        except Exception:
            pass


# ============================================================
# GESTION DES DIPLÔMES (admin)
# ============================================================

def _ensure_education_tables_admin(conn):
    """Sécurité : s'assure que les tables du système de diplôme existent."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS user_domain (
            user_id INTEGER PRIMARY KEY,
            sector TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS user_diplomas (
            user_id INTEGER NOT NULL,
            sector TEXT NOT NULL,
            tier TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'failed',
            attempt_used INTEGER NOT NULL DEFAULT 0,
            retry_unlock_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, sector, tier)
        )"""
    )


_DIPLOME_SECTOR_KEYS = set(EDU_SECTORS.keys())
_DIPLOME_TIER_KEYS = {t["key"]: t for t in EDU_TIERS}


def _parse_sector_tier(sector_raw: str, tier_raw: str):
    """Valide et normalise un couple (secteur, palier). Renvoie (None, None) si invalide."""
    sector = (sector_raw or "").strip().lower()
    tier = (tier_raw or "").strip().lower()
    if sector not in _DIPLOME_SECTOR_KEYS or tier not in _DIPLOME_TIER_KEYS:
        return None, None
    return sector, tier


def _diplome_usage_hint() -> str:
    sectors_list = ", ".join(sorted(_DIPLOME_SECTOR_KEYS))
    tiers_list = ", ".join(t["key"] for t in EDU_TIERS)
    return f"\n\nSecteurs : {sectors_list}\nPaliers : {tiers_list}"


@admin_or_owner_only
async def setdiplome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /setdiplome user_id secteur palier
    Accorde (force) un diplôme validé à un joueur, sans passer par l'examen.
    """
    if len(context.args) < 3:
        await update.message.reply_text(
            "Usage : /setdiplome user_id secteur palier" + _diplome_usage_hint()
        )
        return

    identifier = context.args[0]
    player = get_player_by_name_or_id(identifier)
    if player is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    sector, tier = _parse_sector_tier(context.args[1], context.args[2])
    if sector is None:
        await update.message.reply_text(
            "❌ Secteur ou palier invalide." + _diplome_usage_hint()
        )
        return

    # On ne peut pas accorder un palier en sautant les précédents : il faut
    # commencer par le Bac, puis Licence, Master, MBA, dans l'ordre.
    tier_order = [t["key"] for t in EDU_TIERS]
    tier_index = tier_order.index(tier)
    if tier_index > 0:
        with get_conn() as conn:
            _ensure_education_tables_admin(conn)
            passed_rows = conn.execute(
                "SELECT tier FROM user_diplomas WHERE user_id = ? AND sector = ? AND status = 'passed'",
                (player["user_id"], sector),
            ).fetchall()
        passed_tiers = {r["tier"] for r in passed_rows}
        required_tiers = tier_order[:tier_index]
        missing = [t for t in required_tiers if t not in passed_tiers]
        if missing:
            first_missing = missing[0]
            first_missing_name = _DIPLOME_TIER_KEYS[first_missing]["name"]
            await update.message.reply_text(
                f"❌ Il faut d'abord accorder le palier *{first_missing_name}* dans ce secteur "
                f"avant de pouvoir donner *{_DIPLOME_TIER_KEYS[tier]['name']}*.\n\n"
                f"👉 /setdiplome {identifier} {sector} {first_missing}",
                parse_mode="Markdown",
            )
            return

    with get_conn() as conn:
        _ensure_education_tables_admin(conn)
        conn.execute(
            """INSERT INTO user_diplomas (user_id, sector, tier, status, attempt_used, retry_unlock_at)
               VALUES (?, ?, ?, 'passed', 1, 0)
               ON CONFLICT(user_id, sector, tier)
               DO UPDATE SET status='passed', attempt_used=1, retry_unlock_at=0""",
            (player["user_id"], sector, tier),
        )
        # Le domaine est verrouillé sur un seul secteur dès qu'un diplôme y est
        # validé (règle du système /diplome). Comme l'admin FORCE ce diplôme,
        # on force aussi le domaine sur ce secteur, même si le joueur avait déjà
        # un domaine différent — sinon le diplôme est bien en base mais
        # /creerboite reste bloqué car domaine et diplôme ne correspondent pas.
        conn.execute(
            "INSERT INTO user_domain (user_id, sector) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET sector = excluded.sector",
            (player["user_id"], sector),
        )

    tier_info = _DIPLOME_TIER_KEYS[tier]
    sector_info = EDU_SECTORS[sector]
    await update.message.reply_text(
        f"✅ Diplôme accordé à *{player['first_name']}* (`{player['user_id']}`) :\n"
        f"{tier_info['emoji']} {tier_info['name']} · {sector_info['label']}",
        parse_mode="Markdown",
    )


@admin_or_owner_only
async def retirerdiplome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /retirerdiplome user_id secteur palier
    Retire un diplôme validé à un joueur.
    - Si ce joueur est PDG, son entreprise est automatiquement dissoute
      (comme /dissoudre), peu importe le diplôme retiré.
    - Si c'est un simple employé et qu'il n'a plus AUCUN diplôme après ce
      retrait (même plus le Bac), il est automatiquement renvoyé de son
      entreprise (le Bac est le minimum requis pour travailler).
    """
    if len(context.args) < 3:
        await update.message.reply_text(
            "Usage : /retirerdiplome user_id secteur palier" + _diplome_usage_hint()
        )
        return

    identifier = context.args[0]
    player = get_player_by_name_or_id(identifier)
    if player is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    sector, tier = _parse_sector_tier(context.args[1], context.args[2])
    if sector is None:
        await update.message.reply_text(
            "❌ Secteur ou palier invalide." + _diplome_usage_hint()
        )
        return

    with get_conn() as conn:
        _ensure_education_tables_admin(conn)
        row = conn.execute(
            "SELECT status FROM user_diplomas WHERE user_id = ? AND sector = ? AND tier = ?",
            (player["user_id"], sector, tier),
        ).fetchone()

        if not row or row["status"] != "passed":
            tier_info = _DIPLOME_TIER_KEYS[tier]
            sector_info = EDU_SECTORS[sector]
            await update.message.reply_text(
                f"⚠️ *{player['first_name']}* n'a pas le diplôme "
                f"{tier_info['emoji']} {tier_info['name']} · {sector_info['label']} (rien à retirer).",
                parse_mode="Markdown",
            )
            return

        conn.execute(
            "DELETE FROM user_diplomas WHERE user_id = ? AND sector = ? AND tier = ?",
            (player["user_id"], sector, tier),
        )

    tier_info = _DIPLOME_TIER_KEYS[tier]
    sector_info = EDU_SECTORS[sector]

    kicked_text = ""
    dissolved_text = ""

    if player["company_id"] and player["company_role"] == "PDG":
        # Si ce joueur est PDG, retirer n'importe quel diplôme dissout son
        # entreprise automatiquement (peu importe le diplôme retiré).
        # Dissolution PUNITIVE (sanction admin) : contrairement à /dissoudre
        # ou /dissoudreboite, la trésorerie restante n'est PAS redistribuée
        # aux actionnaires — elle est saisie et versée aux caisses de l'État.
        company = get_company_by_id(player["company_id"])
        if company:
            company_name = company["name"]
            seized = company["treasury"] or 0
            if seized > 0:
                add_state_treasury(seized)
            delete_company(company["company_id"])
            dissolved_text = (
                f"\n\n🏢 *{company_name}* a été automatiquement dissoute suite "
                f"au retrait de ce diplôme à son PDG."
                + (f"\n🏦 Trésorerie saisie et versée à l'État : {fmt_money(seized)}"
                   if seized > 0 else "")
            )
    elif player["company_id"] and player["company_role"]:
        # Simple employé : s'il n'a plus AUCUN diplôme (même plus le Bac),
        # il ne remplit plus la condition minimale pour travailler et est renvoyé.
        with get_conn() as conn:
            _ensure_education_tables_admin(conn)
            remaining = conn.execute(
                "SELECT COUNT(*) as c FROM user_diplomas WHERE user_id = ? AND status = 'passed'",
                (player["user_id"],),
            ).fetchone()["c"]

        if remaining == 0:
            company = get_company_by_id(player["company_id"])
            company_name = company["name"] if company else "son entreprise"
            update_player(player["user_id"], company_id=None, company_role=None, salary=0)
            add_company_log(
                player["company_id"],
                f"{player['first_name']} a été renvoyé(e) : plus aucun diplôme (Bac minimum requis).",
            )
            kicked_text = (
                f"\n\n🚪 {player['first_name']} a été renvoyé(e) de *{company_name}* : "
                f"il/elle n'a plus aucun diplôme (le Bac minimum est requis pour travailler)."
            )

    await update.message.reply_text(
        f"✅ Diplôme retiré à *{player['first_name']}* (`{player['user_id']}`) :\n"
        f"{tier_info['emoji']} {tier_info['name']} · {sector_info['label']}"
        f"{dissolved_text}{kicked_text}",
        parse_mode="Markdown",
    )


@admin_or_owner_only
async def resetrecrutement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /resetrecrutement nom_entreprise
    Lève le cooldown d'1 semaine entre deux annonces de recrutement (/annoncerecrutement)
    pour l'entreprise donnée, sans attendre.
    """
    if not context.args:
        await update.message.reply_text("Usage : /resetrecrutement nom_entreprise")
        return

    name = " ".join(context.args)
    company = get_company_by_name(name)
    if company is None:
        await update.message.reply_text("❌ Entreprise introuvable.")
        return

    set_company_last_recruitment_ad(company["company_id"], 0)

    await update.message.reply_text(
        f"✅ Cooldown d'annonce de recrutement levé pour *{company['name']}*.\n"
        f"Le PDG peut refaire /annoncerecrutement immédiatement.",
        parse_mode="Markdown",
    )


@admin_or_owner_only
async def etatresor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/etatresor — Affiche le solde du fonds État (impôts quotidiens des entreprises)."""
    balance = get_state_treasury()
    await update.message.reply_text(
        f"🏛️ *Fonds État*\n\n"
        f"💰 Solde actuel : *{fmt_money(balance)}*\n\n"
        f"Alimenté automatiquement par l'impôt quotidien de 3% sur la trésorerie de chaque entreprise.\n"
        f"Utilise /utiliserimpots id_ou_pseudo montant [raison] pour dépenser ce fonds.",
        parse_mode="Markdown",
    )


@admin_or_owner_only
async def utiliserimpots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/utiliserimpots id_ou_pseudo montant [raison] — Dépense le fonds État en le
    versant à un joueur (ex: événement, compensation, cadeau communautaire)."""
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /utiliserimpots id_ou_pseudo montant [raison]")
        return

    identifier, amount_str = context.args[0], context.args[1]
    reason = " ".join(context.args[2:]) or "Dépense du fonds État"

    try:
        amount = int(amount_str)
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return

    if amount <= 0:
        await update.message.reply_text("❌ Le montant doit être supérieur à 0.")
        return

    balance = get_state_treasury()
    if balance < amount:
        await update.message.reply_text(
            f"❌ Le fonds État n'a que {fmt_money(balance)}, tu ne peux pas dépenser {fmt_money(amount)}."
        )
        return

    player = get_player_by_name_or_id(identifier)
    if player is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    add_state_treasury(-amount)
    add_balance(player["user_id"], amount)
    log_transaction(None, player["user_id"], amount, f"etat->joueur : {reason}")

    await update.message.reply_text(
        f"✅ {fmt_money(amount)} versés à {player['first_name']} depuis le fonds État.\n"
        f"📝 Raison : {reason}\n"
        f"🏛️ Nouveau solde du fonds État : {fmt_money(balance - amount)}",
        parse_mode="Markdown",
    )

    try:
        await context.bot.send_message(
            chat_id=player["user_id"],
            text=(
                f"🏛️ *Tu as reçu {fmt_money(amount)} du fonds État !*\n\n"
                f"📝 {reason}"
            ),
            parse_mode="Markdown",
        )
    except Exception:
        pass


# ── Villes & Mairies (owner) ─────────────────────────────────────────────────
# Ville unique : LIFECITY. Plus besoin d'argument <ville> sur ces commandes.

@admin_or_owner_only
async def villescaisses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/villescaisses — Vue de la caisse municipale de LIFECITY pour l'admin."""
    cities = get_all_cities()
    lines = ["🏛️ *Caisses municipales*\n"]
    for c in cities:
        mayor = get_player_by_id(c["mayor_id"]) if c["mayor_id"] else None
        mayor_txt = (mayor["username"] and f"@{mayor['username']}") or (mayor["first_name"] if mayor else "—")
        lines.append(f"*{c['name']}* : {fmt_money(c['treasury'])} (impôt {c['tax_rate']}%, maire : {mayor_txt})")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@admin_or_owner_only
async def votesmaire(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/votesmaire — Détail vote par vote de l'élection en cours (ou la
    dernière tranchée) : qui a voté pour qui."""
    city_name = CITY_NAME
    election = get_latest_election(city_name)
    if not election:
        await update.message.reply_text(f"Aucune élection enregistrée pour {city_name}.")
        return

    votes = get_election_votes_detail(election["election_id"])
    status_labels = {
        "ouverte": "🟢 En cours",
        "votes_clos": "🟡 Votes clôturés (en attente de décision)",
        "tranchee": "✅ Tranchée",
        "annulee": "⛔ Annulée",
    }
    lines = [
        f"🗳️ *Votes — élection #{election['election_id']} ({city_name})*",
        f"Statut : {status_labels.get(election['status'], election['status'])}\n",
    ]

    if not votes:
        lines.append("Aucun vote enregistré pour cette élection.")
    else:
        current_candidate = None
        for v in votes:
            if v["candidate_id"] != current_candidate:
                current_candidate = v["candidate_id"]
                cand_name = (v["candidate_username"] and f"@{v['candidate_username']}") or v["candidate_first_name"] or str(v["candidate_id"])
                lines.append(f"\n👑 *{cand_name}*")
            voter_name = (v["voter_username"] and f"@{v['voter_username']}") or v["voter_first_name"] or str(v["voter_id"])
            lines.append(f"  • {voter_name}")
        lines.append(f"\nTotal : {len(votes)} vote(s)")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@owner_only
async def addcaisseville(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/addcaisseville montant — Ajoute de l'argent à la caisse de LIFECITY."""
    if not context.args:
        await update.message.reply_text("Usage : /addcaisseville montant")
        return
    city_name = CITY_NAME
    try:
        amount = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return
    add_city_treasury(city_name, amount)
    await update.message.reply_text(f"✅ {fmt_money(amount)} ajoutés à la caisse de {city_name}.")


@owner_only
async def removecaisseville(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/removecaisseville montant — Retire de l'argent de la caisse de LIFECITY."""
    if not context.args:
        await update.message.reply_text("Usage : /removecaisseville montant")
        return
    city_name = CITY_NAME
    try:
        amount = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return
    if not withdraw_city_treasury(city_name, amount):
        await update.message.reply_text("❌ Solde insuffisant dans cette caisse.")
        return
    await update.message.reply_text(f"✅ {fmt_money(amount)} retirés de la caisse de {city_name}.")


@owner_or_commission_only
async def ouvrirelection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ouvrirelection — Ouvre une élection municipale à LIFECITY (48h de vote).
    Accessible à l'owner et au président de la commission électorale."""
    city_name = CITY_NAME
    open_election(city_name, int(time.time()))
    await update.message.reply_text(f"🗳️ Nouvelle élection ouverte à {city_name} (48h de vote).")


@owner_or_commission_only
async def cloturerelection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/cloturerelection — Clôture le vote en cours (plus personne ne peut voter/candidater).
    Ne désigne PAS de vainqueur : seul l'owner tranche ensuite via /trancherelection.
    Accessible à l'owner et au président de la commission électorale."""
    city_name = CITY_NAME
    election = get_open_election(city_name)
    if not election:
        await update.message.reply_text("Aucune élection en cours (vote déjà clôturé ou aucune élection ouverte).")
        return
    close_election_voting(election["election_id"], int(time.time()))
    await update.message.reply_text(
        "🔒 Vote clôturé à LIFECITY. En attente de la décision de l'owner (/trancherelection)."
    )


@owner_only
async def trancherelection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/trancherelection id_ou_pseudo — Désigne le maire élu (l'owner tranche seul,
    que le vote soit encore ouvert ou déjà clôturé par la commission)."""
    if not context.args:
        await update.message.reply_text("Usage : /trancherelection id_ou_pseudo")
        return
    city_name = CITY_NAME
    election = get_pending_election(city_name)
    if not election:
        await update.message.reply_text(f"Aucune élection en cours à {city_name}.")
        return
    target = find_player_by_identifier(context.args[0])
    if not target:
        await update.message.reply_text("❌ Joueur introuvable.")
        return
    if not is_candidate(election["election_id"], target["user_id"]):
        await update.message.reply_text("❌ Ce joueur n'est pas candidat à cette élection.")
        return

    now = int(time.time())
    close_election(election["election_id"], target["user_id"], now)
    install_mayor(city_name, target["user_id"], now)
    name = (target["username"] and f"@{target['username']}") or target["first_name"] or str(target["user_id"])
    await update.message.reply_text(
        f"👑 {name} est désigné maire de {city_name} pour un mandat de 2 semaines.",
    )
    try:
        await context.bot.send_message(
            chat_id=target["user_id"],
            text=f"👑 Félicitations, tu es désormais maire de {city_name} (mandat de 2 semaines) !",
        )
    except Exception:
        pass


@owner_only
async def revoquermaire(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/revoquermaire — Met fin immédiatement au mandat du maire en poste et relance une élection."""
    city_name = CITY_NAME
    city = get_city(city_name)
    if not city["mayor_id"]:
        await update.message.reply_text(f"Il n'y a pas de maire en poste à {city_name}.")
        return
    now = int(time.time())
    remove_mayor(city_name, "revoque_owner", 0, now)
    open_election(city_name, now)
    await update.message.reply_text(
        f"⛔ Mandat du maire de {city_name} terminé par l'owner. Nouvelle élection ouverte (48h)."
    )


@owner_only
async def nommercommission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/nommercommission id_ou_pseudo — Nomme le président de la commission électorale
    (remplace le précédent s'il y en a un). Ce joueur pourra ouvrir/clôturer le vote
    des élections via /ouvrirelection et /cloturerelection, mais ne pourra jamais
    trancher une élection ni révoquer un maire : ça reste réservé à l'owner."""
    if not context.args:
        await update.message.reply_text("Usage : /nommercommission id_ou_pseudo")
        return
    target = find_player_by_identifier(context.args[0])
    if not target:
        await update.message.reply_text("❌ Joueur introuvable.")
        return
    set_election_commission(target["user_id"], int(time.time()))
    name = (target["username"] and f"@{target['username']}") or target["first_name"] or str(target["user_id"])
    await update.message.reply_text(
        f"🗳️ {name} est nommé président de la commission électorale.\n"
        f"Il peut désormais utiliser /ouvrirelection et /cloturerelection."
    )
    try:
        await context.bot.send_message(
            chat_id=target["user_id"],
            text=(
                "🗳️ Tu es désormais président de la commission électorale.\n"
                "Tu peux ouvrir un vote avec /ouvrirelection et le clôturer avec "
                "/cloturerelection. Seul l'owner peut trancher le vainqueur "
                "(/trancherelection)."
            ),
        )
    except Exception:
        pass


async def commission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/commission — Affiche qui est l'actuel président de la commission électorale."""
    user_id = get_election_commission()
    if not user_id:
        await update.message.reply_text("Aucun président de la commission électorale n'est nommé actuellement.")
        return
    p = get_player_by_id(user_id)
    name = (p and p["username"] and f"@{p['username']}") or (p and p["first_name"]) or str(user_id)
    await update.message.reply_text(f"🗳️ Président de la commission électorale : {name}")


@admin_or_owner_only
async def retirercandidat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/retirercandidat id_ou_pseudo — Retire un joueur de l'élection en cours à LIFECITY
    (accessible à l'owner et aux admins)."""
    if not context.args:
        await update.message.reply_text("Usage : /retirercandidat id_ou_pseudo")
        return
    city_name = CITY_NAME
    election = get_open_election(city_name)
    if not election:
        await update.message.reply_text(f"Aucune élection en cours à {city_name}.")
        return
    target = find_player_by_identifier(context.args[0])
    if not target:
        await update.message.reply_text("❌ Joueur introuvable.")
        return
    if not remove_candidate(election["election_id"], target["user_id"]):
        await update.message.reply_text("Ce joueur n'est pas candidat à cette élection.")
        return
    name = (target["username"] and f"@{target['username']}") or target["first_name"] or str(target["user_id"])
    await update.message.reply_text(f"✅ {name} a été retiré de l'élection à {city_name}.")
    try:
        await context.bot.send_message(
            chat_id=target["user_id"],
            text=f"⚠️ Ta candidature à la mairie de {city_name} a été retirée par un administrateur.",
        )
    except Exception:
        pass


@owner_only
async def lancervote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/lancervote @candidat1 @candidat2 ... — Toi seul choisis qui se présente,
    le bot poste directement le message à boutons dans ce chat (groupe compris)
    pour que les citoyens votent."""
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage : /lancervote @candidat1 @candidat2 [...]\n"
            "Ex : /lancervote @Jean @Marie @Kevin"
        )
        return

    city_name = CITY_NAME
    now = int(time.time())
    election = get_open_election(city_name)
    election_id = election["election_id"] if election else open_election(city_name, now)

    added, introuvables = [], []
    for identifier in context.args:
        target = find_player_by_identifier(identifier)
        if not target:
            introuvables.append(identifier)
            continue
        add_candidate(election_id, target["user_id"], now)  # ignore silencieusement si déjà candidat
        added.append(target)

    if not added:
        await update.message.reply_text("❌ Aucun des joueurs indiqués n'a été trouvé.")
        return

    text, keyboard = _build_election_view(election_id, city_name)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)

    if introuvables:
        await update.message.reply_text("⚠️ Introuvables : " + ", ".join(introuvables))


@owner_only
async def forcenommer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Force le poste d'un joueur dans une entreprise (PDG, Directeur, Employé...),
    sans passer par les règles normales (réservées au PDG en jeu).
    Usage : réponds au message du joueur avec /forcenommer nom_entreprise poste
    OU sans répondre : /forcenommer nom_ou_pseudo_joueur nom_entreprise poste
    Le poste peut contenir plusieurs mots (ex: "Directeur Marketing")."""
    target = None
    args = list(context.args)

    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
    elif len(args) >= 3:
        # Pas de reply : le 1er argument est le nom/pseudo/id du joueur à nommer.
        identifier = args.pop(0)
        target_player_row = find_player_by_identifier(identifier)
        if target_player_row is None:
            await update.message.reply_text(
                f"❌ Joueur introuvable pour '{identifier}' (ou plusieurs joueurs correspondent — "
                f"précise son @pseudo ou son ID)."
            )
            return
        target = SimpleNamespace(
            id=target_player_row["user_id"],
            username=target_player_row["username"],
            first_name=target_player_row["first_name"],
        )

    if target is None or len(args) < 2:
        await update.message.reply_text(
            "Usage : réponds au message du joueur avec\n"
            "/forcenommer nom_entreprise poste\n"
            "Exemple : /forcenommer THETEENRICH PDG\n\n"
            "Ou sans répondre, précise son nom/pseudo/ID :\n"
            "/forcenommer nom_ou_pseudo_joueur nom_entreprise poste"
        )
        return

    name = args[0]
    new_role = " ".join(args[1:])

    company = get_company_by_name(name)
    if company is None:
        await update.message.reply_text("❌ Entreprise introuvable.")
        return

    target_player = get_or_create_player(target.id, target.username, target.first_name)

    # Si on nomme un nouveau PDG, l'ancien est rétrogradé en Employé pour
    # éviter d'avoir deux PDG dans la même entreprise.
    if new_role.strip().lower() == "pdg":
        with get_conn() as conn:
            conn.execute(
                """UPDATE players SET company_role = 'Employé'
                   WHERE company_id = ? AND company_role = 'PDG' AND user_id != ?""",
                (company["company_id"], target.id),
            )

    update_player(
        target.id,
        company_id=company["company_id"],
        company_role=new_role,
        salary=target_player["salary"] if target_player["company_id"] == company["company_id"] else 0,
    )
    add_company_log(
        company["company_id"],
        f"[OWNER] {target.first_name} nommé(e) {new_role} par décision admin",
    )

    await update.message.reply_text(
        f"👑 {target.first_name} est désormais *{new_role}* chez *{company['name']}* (action owner).",
        parse_mode="Markdown",
    )


@admin_or_owner_only
async def liberer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /liberer user_id
    Libère un joueur de prison.
    Usage : /liberer user_id
    """
    if not context.args:
        await update.message.reply_text("Usage : /liberer user_id")
        return

    identifier = context.args[0]
    player = get_player_by_name_or_id(identifier)
    if player is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    # Mettre jail_until à 0 pour libérer immédiatement
    update_player(player["user_id"], jail_until=0)
    
    await update.message.reply_text(
        f"🔓 *{player['first_name']}* (ID `{player['user_id']}`) a été libéré de prison !",
        parse_mode="Markdown"
    )


@admin_or_owner_only
async def prolonger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /prolonger user_id [minutes]
    Prolonge la peine de prison d'un joueur.
    Usage : /prolonger user_id [minutes]
    """
    if not context.args:
        await update.message.reply_text("Usage : /prolonger user_id [minutes]")
        return

    identifier = context.args[0]
    
    # Temps par défaut : 60 minutes
    minutes = 60
    if len(context.args) > 1:
        try:
            minutes = int(context.args[1])
            if minutes <= 0:
                await update.message.reply_text("❌ Les minutes doivent être un nombre positif.")
                return
        except ValueError:
            await update.message.reply_text("❌ Veuillez entrer un nombre valide de minutes.")
            return

    player = get_player_by_name_or_id(identifier)
    if player is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    # Récupérer le temps actuel en prison
    current_jail = player["jail_until"] or 0
    now = int(time.time())
    
    # Si le joueur n'est pas en prison, on le met en prison pour la durée demandée
    if current_jail <= now:
        new_jail_time = now + (minutes * 60)
    else:
        # Prolonger la peine existante
        new_jail_time = current_jail + (minutes * 60)
    
    update_player(player["user_id"], jail_until=new_jail_time)
    
    await update.message.reply_text(
        f"⛓️ *{player['first_name']}* (ID `{player['user_id']}`) a vu sa peine prolongée de {minutes} minutes.\n"
        f"📅 Nouvelle libération prévue : {time.strftime('%d/%m/%Y %H:%M', time.localtime(new_jail_time))}",
        parse_mode="Markdown"
    )


@admin_or_owner_only
async def aggraver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /aggraver user_id [minutes]
    Aggrave la peine de prison d'un joueur (ajoute du temps à sa peine actuelle).
    Usage : /aggraver user_id [minutes]
    """
    if not context.args:
        await update.message.reply_text("Usage : /aggraver user_id [minutes]")
        return

    identifier = context.args[0]
    
    # Temps par défaut : 120 minutes (2 heures)
    minutes = 120
    if len(context.args) > 1:
        try:
            minutes = int(context.args[1])
            if minutes <= 0:
                await update.message.reply_text("❌ Les minutes doivent être un nombre positif.")
                return
        except ValueError:
            await update.message.reply_text("❌ Veuillez entrer un nombre valide de minutes.")
            return

    player = get_player_by_name_or_id(identifier)
    if player is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    # Récupérer le temps actuel en prison
    current_jail = player["jail_until"] or 0
    now = int(time.time())
    
    # Si le joueur n'est pas en prison, on le met en prison
    if current_jail <= now:
        new_jail_time = now + (minutes * 60)
        action = "a été condamné à"
    else:
        # Aggraver la peine existante (ajouter du temps)
        new_jail_time = current_jail + (minutes * 60)
        action = "a vu sa peine aggravée de"
    
    update_player(player["user_id"], jail_until=new_jail_time)
    
    await update.message.reply_text(
        f"⛓️ *{player['first_name']}* (ID `{player['user_id']}`) {action} {minutes} minutes de prison supplémentaires.\n"
        f"📅 Libération prévue : {time.strftime('%d/%m/%Y %H:%M', time.localtime(new_jail_time))}",
        parse_mode="Markdown"
    )


@admin_or_owner_only
async def alleger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /alleger user_id [minutes]
    Allège la peine de prison d'un joueur (réduit du temps à sa peine actuelle).
    Usage : /alleger user_id [minutes]
    """
    if not context.args:
        await update.message.reply_text("Usage : /alleger user_id [minutes]")
        return

    identifier = context.args[0]
    
    # Temps par défaut : 30 minutes
    minutes = 30
    if len(context.args) > 1:
        try:
            minutes = int(context.args[1])
            if minutes <= 0:
                await update.message.reply_text("❌ Les minutes doivent être un nombre positif.")
                return
        except ValueError:
            await update.message.reply_text("❌ Veuillez entrer un nombre valide de minutes.")
            return

    player = get_player_by_name_or_id(identifier)
    if player is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    # Récupérer le temps actuel en prison
    current_jail = player["jail_until"] or 0
    now = int(time.time())
    
    # Si le joueur n'est pas en prison, on ne fait rien
    if current_jail <= now:
        await update.message.reply_text(
            f"ℹ️ *{player['first_name']}* n'est pas actuellement en prison.",
            parse_mode="Markdown"
        )
        return
    
    # Alléger la peine (réduire le temps)
    new_jail_time = max(now, current_jail - (minutes * 60))
    
    update_player(player["user_id"], jail_until=new_jail_time)
    
    if new_jail_time <= now:
        await update.message.reply_text(
            f"⚖️ *{player['first_name']}* (ID `{player['user_id']}`) a été libéré !\n"
            f"📅 Sa peine a été réduite de {minutes} minutes.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"⚖️ *{player['first_name']}* (ID `{player['user_id']}`) a vu sa peine réduite de {minutes} minutes.\n"
            f"📅 Nouvelle libération prévue : {time.strftime('%d/%m/%Y %H:%M', time.localtime(new_jail_time))}",
            parse_mode="Markdown"
        )


@admin_or_owner_only
async def voirprison(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /voirprison
    Voir tous les joueurs actuellement en prison.
    """
    now = int(time.time())
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, first_name, username, jail_until FROM players WHERE jail_until > ? ORDER BY jail_until",
            (now,)
        ).fetchall()

    if not rows:
        await update.message.reply_text("🔓 Aucun joueur n'est actuellement en prison.")
        return

    lines = ["⛓️ *Prisonniers actuels*\n"]
    for r in rows:
        remaining = r["jail_until"] - now
        minutes = remaining // 60
        seconds = remaining % 60
        name = r["first_name"] or r["username"] or f"#{r['user_id']}"
        lines.append(f"• *{name}* (ID `{r['user_id']}`) — reste {minutes}min {seconds}s")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@admin_or_owner_only
async def voirproces(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /voirproces
    Voir les procès en cours.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT l.lawsuit_id, l.plaintiff_id, l.defendant_id, l.status, l.created_at,
                      p1.first_name as plaintiff_name, p2.first_name as defendant_name
               FROM lawsuits l
               LEFT JOIN players p1 ON p1.user_id = l.plaintiff_id
               LEFT JOIN players p2 ON p2.user_id = l.defendant_id
               WHERE l.status = 'pending'
               ORDER BY l.created_at DESC"""
        ).fetchall()

    if not rows:
        await update.message.reply_text("⚖️ Aucun procès en cours.")
        return

    lines = ["⚖️ *Procès en cours*\n"]
    for r in rows:
        plaintiff = r["plaintiff_name"] or f"#{r['plaintiff_id']}"
        defendant = r["defendant_name"] or f"#{r['defendant_id']}"
        date = time.strftime("%d/%m %H:%M", time.localtime(r["created_at"]))
        lines.append(f"• {plaintiff} vs {defendant} — déposé le {date}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@admin_or_owner_only
async def clearjail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /clearjail
    Libère tous les prisonniers.
    """
    with get_conn() as conn:
        count = conn.execute(
            "UPDATE players SET jail_until = 0 WHERE jail_until > ?",
            (int(time.time()),)
        ).rowcount

    await update.message.reply_text(
        f"🧹 *{count}* prisonniers ont été libérés !",
        parse_mode="Markdown"
    )


@admin_or_owner_only
async def verdict(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /verdict [user_id] [guilty/not_guilty]
    Rendre un verdict pour un joueur accusé.
    Usage : /verdict 123456789 guilty
    """
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /verdict [user_id] [guilty/not_guilty]")
        return

    identifier = context.args[0]
    decision = context.args[1].lower()

    if decision not in ("guilty", "not_guilty"):
        await update.message.reply_text("❌ Le verdict doit être 'guilty' ou 'not_guilty'.")
        return

    player = get_player_by_name_or_id(identifier)
    if player is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    if decision == "guilty":
        # Mettre en prison pour 30 minutes par défaut
        jail_time = int(time.time()) + (30 * 60)
        update_player(player["user_id"], jail_until=jail_time)
        await update.message.reply_text(
            f"⚖️ *{player['first_name']}* (ID `{player['user_id']}`) a été déclaré *coupable* !\n"
            f"⛓️ Peine : 30 minutes de prison.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"⚖️ *{player['first_name']}* (ID `{player['user_id']}`) a été déclaré *non coupable* et est libéré.",
            parse_mode="Markdown"
        )


@owner_only
async def configurer_rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /configurer_rank [rank] [prix]
    Configurer les prix des rangs.
    Usage : /configurer_rank 1 1000000
    """
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /configurer_rank [rank] [prix]")
        return

    try:
        rank = int(context.args[0])
        price = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Veuillez entrer des nombres valides.")
        return

    # Enregistrer dans la base de données (table rank_config)
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO rank_config (rank, price) VALUES (?, ?)
               ON CONFLICT(rank) DO UPDATE SET price = excluded.price""",
            (rank, price)
        )

    await update.message.reply_text(
        f"✅ Rang *{rank}* configuré avec succès au prix de {fmt_money(price)}.",
        parse_mode="Markdown"
    )


@admin_or_owner_only
async def administrateurs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /administrateurs
    Voir la liste des administrateurs (owner uniquement).
    """
    await update.message.reply_text(
        f"👑 *Administrateurs LifeCity*\n\n"
        f"• Owner : `{OWNER_ID}`\n"
        f"• Vous êtes le seul administrateur.",
        parse_mode="Markdown"
    )


# ── LISTE DES JOUEURS (Version claire et bien espacée) ─────────────────────

@admin_or_owner_only
async def listejoueurs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /listejoueurs [page]
    Liste tous les joueurs avec leur ID, pseudo et solde.
    Usage : /listejoueurs [page]
    """
    # Nombre de joueurs par page
    per_page = 15
    
    # Récupérer le numéro de page (par défaut 1)
    page = 1
    if context.args:
        try:
            page = int(context.args[0])
            if page < 1:
                page = 1
        except ValueError:
            pass
    
    # Calculer l'offset
    offset = (page - 1) * per_page
    
    with get_conn() as conn:
        # Compter le nombre total de joueurs
        total = conn.execute("SELECT COUNT(*) as count FROM players").fetchone()["count"]
        
        # Récupérer les joueurs pour la page demandée
        rows = conn.execute(
            """SELECT user_id, first_name, username, balance,
                      (SELECT COALESCE(SUM(balance), 0) FROM user_bank_accounts WHERE user_bank_accounts.user_id = players.user_id) AS bank_balance,
                      balance + (SELECT COALESCE(SUM(balance), 0) FROM user_bank_accounts WHERE user_bank_accounts.user_id = players.user_id) AS total_balance
               FROM players
               ORDER BY user_id
               LIMIT ? OFFSET ?""",
            (per_page, offset)
        ).fetchall()
    
    if not rows:
        await update.message.reply_text("📭 Aucun joueur trouvé.")
        return
    
    # Calculer le nombre total de pages
    total_pages = (total + per_page - 1) // per_page
    
    # Construction du message avec un design clair
    lines = []
    
    # En-tête
    lines.append("👥 *LISTE DES JOUEURS*")
    lines.append(f"📊 Page {page}/{total_pages} — {total} joueurs au total")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    
    # Corps de la liste
    for idx, r in enumerate(rows):
        # Numéro du joueur dans la page
        num = offset + idx + 1
        
        # Nom du joueur
        name = r["first_name"] or r["username"] or f"Joueur #{r['user_id']}"
        
        # Soldes
        balance = r["balance"] or 0
        bank_balance = r["bank_balance"] or 0
        total_bal = r["total_balance"] or 0
        
        # Emoji selon le rang (parmi les 3 premiers de la page)
        if idx == 0:
            rank_emoji = "🥇"
        elif idx == 1:
            rank_emoji = "🥈"
        elif idx == 2:
            rank_emoji = "🥉"
        else:
            rank_emoji = f"{num:>2}."
        
        lines.append(f"{rank_emoji} *{name}*")
        lines.append(f"   🆔 ID : `{r['user_id']}`")
        lines.append(f"   👤 Pseudo : @{r['username'] or 'Aucun'}")
        lines.append(f"   💰 Solde total : {fmt_money(total_bal)}")
        lines.append(f"      ├─ 💵 Liquide : {fmt_money(balance)}")
        lines.append(f"      └─ 🏦 Banque : {fmt_money(bank_balance)}")
        lines.append("")
    
    # Pied de page
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    # Informations de navigation
    nav = []
    if page > 1:
        nav.append(f"◀️ Page précédente : /listejoueurs {page - 1}")
    if page < total_pages:
        nav.append(f"Page suivante : /listejoueurs {page + 1} ▶️")
    
    if nav:
        lines.append(" | ".join(nav))
    
    lines.append("")
    lines.append("💡 *Astuce* : Utilisez /recherchejoueur <nom> pour trouver un joueur spécifique.")
    
    # Envoyer le message
    try:
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        # Fallback sans Markdown en cas d'erreur
        if "Can't parse entities" in str(e):
            # Nettoyer le texte des caractères Markdown
            clean_lines = []
            for line in lines:
                clean_lines.append(line.replace("*", "").replace("`", ""))
            await update.message.reply_text("\n".join(clean_lines))
        else:
            raise e


@admin_or_owner_only
async def listediplomes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /listediplomes [page]
    Liste tous les joueurs avec les diplômes qu'ils ont (ou n'ont pas) obtenus.
    Usage : /listediplomes [page]
    """
    per_page = 15

    page = 1
    if context.args:
        try:
            page = int(context.args[0])
            if page < 1:
                page = 1
        except ValueError:
            pass

    offset = (page - 1) * per_page

    with get_conn() as conn:
        _ensure_education_tables_admin(conn)

        total = conn.execute("SELECT COUNT(*) as count FROM players").fetchone()["count"]

        rows = conn.execute(
            """SELECT user_id, first_name, username
               FROM players
               ORDER BY user_id
               LIMIT ? OFFSET ?""",
            (per_page, offset)
        ).fetchall()

        if not rows:
            await update.message.reply_text("📭 Aucun joueur trouvé.")
            return

        user_ids = [r["user_id"] for r in rows]
        placeholders = ",".join("?" for _ in user_ids)
        diploma_rows = conn.execute(
            f"""SELECT user_id, sector, tier FROM user_diplomas
                WHERE status = 'passed' AND user_id IN ({placeholders})""",
            user_ids,
        ).fetchall()

    tier_order = [t["key"] for t in EDU_TIERS]
    tier_emoji = {t["key"]: t["emoji"] for t in EDU_TIERS}
    tier_name = {t["key"]: t["name"] for t in EDU_TIERS}

    by_user = {}
    for d in diploma_rows:
        by_user.setdefault(d["user_id"], {}).setdefault(d["sector"], set()).add(d["tier"])

    total_pages = (total + per_page - 1) // per_page

    lines = []
    lines.append("🎓 *LISTE DES DIPLÔMES*")
    lines.append(f"📊 Page {page}/{total_pages} — {total} joueurs au total")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")

    for idx, r in enumerate(rows):
        num = offset + idx + 1
        name = r["first_name"] or r["username"] or f"Joueur #{r['user_id']}"

        lines.append(f"{num:>2}. *{name}*  (`{r['user_id']}`)")

        sectors = by_user.get(r["user_id"])
        if not sectors:
            lines.append("   🚫 Aucun diplôme")
        else:
            for sector_key, tiers in sectors.items():
                sector_label = EDU_SECTORS.get(sector_key, {}).get("label", sector_key)
                ordered = [t for t in tier_order if t in tiers]
                chain = "  ".join(f"{tier_emoji[t]}{tier_name[t]}" for t in ordered)
                lines.append(f"   📁 {sector_label} : {chain}")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    nav = []
    if page > 1:
        nav.append(f"◀️ Page précédente : /listediplomes {page - 1}")
    if page < total_pages:
        nav.append(f"Page suivante : /listediplomes {page + 1} ▶️")
    if nav:
        lines.append(" | ".join(nav))

    lines.append("")
    lines.append("💡 *Astuce* : /setdiplome et /retirerdiplome id secteur palier pour modifier un diplôme.")

    try:
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        if "Can't parse entities" in str(e):
            clean_lines = [l.replace("*", "").replace("`", "") for l in lines]
            await update.message.reply_text("\n".join(clean_lines))
        else:
            raise e


@admin_or_owner_only
async def recherchejoueur(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /recherchejoueur <nom>
    Recherche un joueur par nom ou ID partiel.
    Usage : /recherchejoueur <nom>
    """
    if not context.args:
        await update.message.reply_text("Usage : /recherchejoueur <nom>")
        return
    
    search_term = " ".join(context.args).strip()
    
    if len(search_term) < 2:
        await update.message.reply_text("❌ Le terme de recherche doit contenir au moins 2 caractères.")
        return
    
    with get_conn() as conn:
        # Rechercher par user_id exact ou par nom partiel
        if search_term.isdigit():
            # Recherche par ID exact
            rows = conn.execute(
                """SELECT user_id, first_name, username, balance,
                          (SELECT COALESCE(SUM(balance), 0) FROM user_bank_accounts WHERE user_bank_accounts.user_id = players.user_id) AS bank_balance,
                          balance + (SELECT COALESCE(SUM(balance), 0) FROM user_bank_accounts WHERE user_bank_accounts.user_id = players.user_id) AS total_balance
                   FROM players
                   WHERE user_id = ?
                   ORDER BY user_id""",
                (int(search_term),)
            ).fetchall()
        else:
            # Recherche par nom partiel
            search_pattern = f"%{search_term}%"
            rows = conn.execute(
                """SELECT user_id, first_name, username, balance,
                          (SELECT COALESCE(SUM(balance), 0) FROM user_bank_accounts WHERE user_bank_accounts.user_id = players.user_id) AS bank_balance,
                          balance + (SELECT COALESCE(SUM(balance), 0) FROM user_bank_accounts WHERE user_bank_accounts.user_id = players.user_id) AS total_balance
                   FROM players
                   WHERE first_name LIKE ? OR username LIKE ?
                   ORDER BY user_id
                   LIMIT 30""",
                (search_pattern, search_pattern)
            ).fetchall()
    
    if not rows:
        await update.message.reply_text(f"🔍 Aucun joueur trouvé pour '{search_term}'.")
        return
    
    # Construction du message
    lines = []
    lines.append(f"🔍 *Résultats de recherche*")
    lines.append(f"📝 Terme : '{search_term}' — {len(rows)} joueur(s) trouvé(s)")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    
    for idx, r in enumerate(rows):
        num = idx + 1
        name = r["first_name"] or r["username"] or f"Joueur #{r['user_id']}"
        balance = r["balance"] or 0
        bank_balance = r["bank_balance"] or 0
        total_bal = r["total_balance"] or 0
        
        lines.append(f"{num}. *{name}*")
        lines.append(f"   🆔 ID : `{r['user_id']}`")
        lines.append(f"   👤 Pseudo : @{r['username'] or 'Aucun'}")
        lines.append(f"   💰 Solde total : {fmt_money(total_bal)}")
        lines.append(f"      ├─ 💵 Liquide : {fmt_money(balance)}")
        lines.append(f"      └─ 🏦 Banque : {fmt_money(bank_balance)}")
        lines.append("")
    
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@admin_or_owner_only
async def freezejoueur(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gèle les gains d'un joueur (balance bloquée à 0, il peut pas recevoir d'argent)."""
    if not context.args:
        await update.message.reply_text("Usage : /freezejoueur user_id")
        return

    player = get_player_by_name_or_id(context.args[0])
    if player is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    update_player(player["user_id"], banned=1, ban_reason="❄️ Compte gelé par l'administration")
    await update.message.reply_text(
        f"❄️ *{player['first_name']}* (ID `{player['user_id']}`) a été gelé.\n"
        f"Il ne peut plus utiliser le bot jusqu'au dégel.",
        parse_mode="Markdown"
    )


@admin_or_owner_only
async def unfreezejoueur(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dégèle un joueur."""
    if not context.args:
        await update.message.reply_text("Usage : /unfreezejoueur user_id")
        return

    player = get_player_by_name_or_id(context.args[0])
    if player is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    update_player(player["user_id"], banned=0, ban_reason=None)
    await update.message.reply_text(
        f"✅ *{player['first_name']}* (ID `{player['user_id']}`) a été dégelé.",
        parse_mode="Markdown"
    )


# ── Pause / Resume ───────────────────────────────────────────────────────────

@owner_only
async def pause_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Met le bot en pause manuelle — toutes les commandes sont bloquées pour les non-owners."""
    from pause import set_paused
    set_paused(True)
    await update.message.reply_text(
        "🏳️ Bot mis en *pause*. Toutes les commandes sont bloquées pour les joueurs.",
        parse_mode="Markdown"
    )


@owner_only
async def resume_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reprend le bot après une pause manuelle."""
    from pause import set_paused
    set_paused(False)
    await update.message.reply_text(
        "▶️ Bot *repris* ! Les commandes sont de nouveau disponibles.",
        parse_mode="Markdown"
    )


# ── Annonce ──────────────────────────────────────────────────────────────────

@owner_only
async def annonce(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /annonce <texte>
    Envoie l'annonce dans TOUS les groupes actifs ET en PV à TOUS les
    joueurs ayant déjà fait /start avec le bot.
    """
    texte_custom = " ".join(context.args) if context.args else None

    if not texte_custom:
        await update.message.reply_text(
            "❌ Usage : /annonce <ton message>\n"
            "Exemple : /annonce Nouvelle mise à jour disponible !"
        )
        return

    message = f"📢 *Annonce LifeCity*\n\n{texte_custom}"

    group_ids = _get_all_group_ids()
    player_ids = _get_all_player_ids()

    status = await update.message.reply_text(
        f"📤 Diffusion en cours...\n"
        f"Groupes : {len(group_ids)} | Joueurs (PV) : {len(player_ids)}"
    )

    async def _send(chat_id: int) -> bool:
        try:
            await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
            return True
        except Forbidden:
            return False
        except BadRequest as e:
            if "Can't parse entities" in str(e):
                try:
                    await context.bot.send_message(chat_id=chat_id, text=message, parse_mode=None)
                    return True
                except Exception:
                    return False
            return False
        except Exception:
            return False

    ok_groups = fail_groups = 0
    for chat_id in group_ids:
        if await _send(chat_id):
            ok_groups += 1
        else:
            fail_groups += 1
        await asyncio.sleep(0.05)

    ok_players = fail_players = 0
    for user_id in player_ids:
        if await _send(user_id):
            ok_players += 1
        else:
            fail_players += 1
        await asyncio.sleep(0.05)

    await status.edit_text(
        f"✅ *Annonce envoyée*\n\n"
        f"👥 Groupes : {ok_groups}/{len(group_ids)} réussis"
        + (f" ({fail_groups} échecs)\n" if fail_groups else "\n")
        + f"💬 Joueurs (PV) : {ok_players}/{len(player_ids)} réussis"
        + (f" ({fail_players} échecs, bot probablement bloqué)" if fail_players else ""),
        parse_mode="Markdown"
    )


# ── Historique & Surveillance ──────────────────────────────────────────────

@admin_or_owner_only
async def historique(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Affiche les dernières transactions globales du bot.
    Usage : /historique [nb]  -> les nb dernières (défaut 20)
            /historique tout  -> absolument toutes les transactions
    """
    limit = 20
    show_all = False
    if context.args:
        arg = context.args[0].strip().lower()
        if arg in ("tout", "all", "*"):
            show_all = True
        else:
            try:
                limit = max(1, int(arg))
            except ValueError:
                pass

    with get_conn() as conn:
        query = (
            """SELECT t.tx_id, t.from_user, t.to_user, t.amount, t.reason, t.created_at,
                      p1.first_name AS from_name, p2.first_name AS to_name
               FROM transactions t
               LEFT JOIN players p1 ON p1.user_id = t.from_user
               LEFT JOIN players p2 ON p2.user_id = t.to_user
               ORDER BY t.created_at DESC"""
        )
        if show_all:
            rows = conn.execute(query).fetchall()
        else:
            rows = conn.execute(query + " LIMIT ?", (limit,)).fetchall()

    if not rows:
        await update.message.reply_text("📭 Aucune transaction enregistrée.")
        return

    title = "Toutes les transactions" if show_all else f"{limit} dernières transactions"

    plain_lines = [f"📜 {title} ({len(rows)})\n"]
    for r in rows:
        date = time.strftime("%d/%m %H:%M", time.localtime(r["created_at"]))
        src = r["from_name"] or f"#{r['from_user']}" if r["from_user"] else "BOT"
        dst = r["to_name"] or f"#{r['to_user']}" if r["to_user"] else "BOT"
        plain_lines.append(f"[{date}] {src} → {dst} | {fmt_money(r['amount'])} | {r['reason'] or '—'}")

    full_text = "\n".join(plain_lines)

    # Au-delà de la limite de taille d'un message Telegram (~4096 caractères),
    # on envoie l'historique complet sous forme de fichier .txt en pièce jointe.
    if len(full_text) > 3500:
        buffer = io.BytesIO(full_text.encode("utf-8"))
        buffer.name = "historique_transactions.txt"
        await update.message.reply_document(
            document=buffer,
            filename="historique_transactions.txt",
            caption=f"📜 {title} — {len(rows)} transaction(s) (trop long pour un message, voir le fichier).",
        )
        return

    md_lines = [f"📜 *{title}* ({len(rows)})\n"]
    for r in rows:
        date = time.strftime("%d/%m %H:%M", time.localtime(r["created_at"]))
        src = r["from_name"] or f"#{r['from_user']}" if r["from_user"] else "BOT"
        dst = r["to_name"] or f"#{r['to_user']}" if r["to_user"] else "BOT"
        md_lines.append(f"`[{date}]` {src} → {dst} | *{fmt_money(r['amount'])}* | _{r['reason'] or '—'}_")

    try:
        await update.message.reply_text("\n".join(md_lines), parse_mode="Markdown")
    except Exception as e:
        if "Can't parse entities" in str(e):
            await update.message.reply_text(full_text)
        else:
            raise e


@admin_or_owner_only
async def histojoueur(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Historique des transactions d'un joueur précis."""
    if not context.args:
        await update.message.reply_text("Usage : /histojoueur user_id [nb]")
        return

    identifier = context.args[0]
    limit = 15
    if len(context.args) > 1:
        try:
            limit = max(1, min(int(context.args[1]), 50))
        except ValueError:
            pass

    player = get_player_by_name_or_id(identifier)
    if player is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    uid = player["user_id"]
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT t.tx_id, t.from_user, t.to_user, t.amount, t.reason, t.created_at,
                      p1.first_name AS from_name, p2.first_name AS to_name
               FROM transactions t
               LEFT JOIN players p1 ON p1.user_id = t.from_user
               LEFT JOIN players p2 ON p2.user_id = t.to_user
               WHERE t.from_user = ? OR t.to_user = ?
               ORDER BY t.created_at DESC LIMIT ?""",
            (uid, uid, limit)
        ).fetchall()

    if not rows:
        await update.message.reply_text(f"📭 Aucune transaction pour {player['first_name']}.")
        return

    lines = [f"👤 *Historique de {player['first_name']}* (ID {uid})\n"]
    for r in rows:
        date = time.strftime("%d/%m %H:%M", time.localtime(r["created_at"]))
        src = r["from_name"] or "BOT" if r["from_user"] else "BOT"
        dst = r["to_name"] or "BOT" if r["to_user"] else "BOT"
        direction = "📤" if r["from_user"] == uid else "📥"
        lines.append(f"{direction} `[{date}]` {src} → {dst} | *{fmt_money(r['amount'])}* | _{r['reason'] or '—'}_")

    try:
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        if "Can't parse entities" in str(e):
            plain_lines = [f"👤 Historique de {player['first_name']} (ID {uid})\n"]
            for r in rows:
                date = time.strftime("%d/%m %H:%M", time.localtime(r["created_at"]))
                src = r["from_name"] or "BOT" if r["from_user"] else "BOT"
                dst = r["to_name"] or "BOT" if r["to_user"] else "BOT"
                direction = "📤" if r["from_user"] == uid else "📥"
                plain_lines.append(f"{direction} [{date}] {src} → {dst} | {fmt_money(r['amount'])} | {r['reason'] or '—'}")
            await update.message.reply_text("\n".join(plain_lines))
        else:
            raise e


@admin_or_owner_only
async def baleines(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Top joueurs par solde total (portefeuille + banque)."""
    limit = 10
    if context.args:
        try:
            limit = max(1, min(int(context.args[0]), 30))
        except ValueError:
            pass

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT user_id, first_name, username, balance,
                      (SELECT COALESCE(SUM(balance), 0) FROM user_bank_accounts WHERE user_bank_accounts.user_id = players.user_id) AS bank_balance,
                      balance + (SELECT COALESCE(SUM(balance), 0) FROM user_bank_accounts WHERE user_bank_accounts.user_id = players.user_id) AS total
               FROM players
               WHERE banned = 0
               ORDER BY total DESC LIMIT ?""",
            (limit,)
        ).fetchall()

    if not rows:
        await update.message.reply_text("Aucun joueur trouvé.")
        return

    lines = [f"🐋 *Top {limit} — Plus grosses fortunes*\n"]
    medals = ["🥇","🥈","🥉"] + ["🏅"] * 27
    for i, r in enumerate(rows):
        name = r["first_name"] or r["username"] or f"#{r['user_id']}"
        lines.append(
            f"{medals[i]} *{name}* — Total : {fmt_money(r['total'])}\n"
            f"   └ 💵 {fmt_money(r['balance'])} | 🏦 {fmt_money(r['bank_balance'])}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@admin_or_owner_only
async def statsbot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Statistiques globales du bot."""
    with get_conn() as conn:
        total_players = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        banned_players = conn.execute("SELECT COUNT(*) FROM players WHERE banned = 1").fetchone()[0]
        total_companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        total_bank = conn.execute("SELECT COALESCE(SUM(balance), 0) FROM user_bank_accounts").fetchone()[0] or 0
        total_money = (conn.execute("SELECT SUM(balance) FROM players").fetchone()[0] or 0) + total_bank
        total_tx = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        total_tx_amount = conn.execute("SELECT SUM(amount) FROM transactions").fetchone()[0] or 0
        active_loans = conn.execute("SELECT COUNT(*), SUM(remaining) FROM bank_loans").fetchone()
        richest = conn.execute(
            """SELECT first_name,
                      balance + (SELECT COALESCE(SUM(balance), 0) FROM user_bank_accounts WHERE user_bank_accounts.user_id = players.user_id) AS t
               FROM players ORDER BY t DESC LIMIT 1"""
        ).fetchone()

    text = (
        f"📊 *Statistiques LifeCity*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Joueurs : *{total_players}* (bannis : {banned_players})\n"
        f"🏢 Entreprises actives : *{total_companies}*\n"
        f"💰 Argent total en jeu : *{fmt_money(total_money)}*\n"
        f"📜 Transactions totales : *{total_tx}* ({fmt_money(total_tx_amount)} échangés)\n"
        f"🏦 Prêts actifs : *{active_loans[0]}* (dû : {fmt_money(active_loans[1] or 0)})\n"
        f"🐋 Joueur le plus riche : *{richest['first_name'] if richest else '—'}* ({fmt_money(richest['t'] if richest else 0)})"
    )
    
    try:
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        if "Can't parse entities" in str(e):
            await update.message.reply_text(text, parse_mode=None)
        else:
            raise e


@admin_or_owner_only
async def suspectfraude(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Liste les joueurs avec un solde anormalement élevé (> seuil)."""
    seuil = 10_000_000_000  # 10 milliards par défaut
    if context.args:
        try:
            seuil = int(context.args[0])
        except ValueError:
            pass

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT user_id, first_name, username, balance,
                      (SELECT COALESCE(SUM(balance), 0) FROM user_bank_accounts WHERE user_bank_accounts.user_id = players.user_id) AS bank_balance,
                      balance + (SELECT COALESCE(SUM(balance), 0) FROM user_bank_accounts WHERE user_bank_accounts.user_id = players.user_id) AS total
               FROM players
               WHERE balance + (SELECT COALESCE(SUM(balance), 0) FROM user_bank_accounts WHERE user_bank_accounts.user_id = players.user_id) > ?
               ORDER BY total DESC""",
            (seuil,)
        ).fetchall()

    if not rows:
        await update.message.reply_text(f"✅ Aucun joueur au-dessus de {fmt_money(seuil)}.")
        return

    lines = [f"🚨 *Joueurs suspects (>{fmt_money(seuil)})*\n"]
    for r in rows:
        name = r["first_name"] or f"#{r['user_id']}"
        lines.append(
            f"⚠️ *{name}* (ID: `{r['user_id']}`)\n"
            f"   └ 💵 {fmt_money(r['balance'])} | 🏦 {fmt_money(r['bank_balance'])} | Total : {fmt_money(r['total'])}"
        )
    lines.append(f"\n→ /ban id pour bannir | /removemoney id montant pour corriger")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ============================================================
# FIXPARTS — Normaliser les parts d'une entreprise à 100%
# ============================================================

@owner_only
async def fixparts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Recalcule et normalise les parts d'une entreprise pour que le total
    fasse exactement 100%. Utilisation : /fixparts nom_entreprise"""
    if not context.args:
        await update.message.reply_text("Usage : /fixparts nom_entreprise")
        return

    name = " ".join(context.args)
    company = get_company_by_name(name)
    if company is None:
        await update.message.reply_text("❌ Entreprise introuvable.")
        return

    company_id = company["company_id"]
    shares = get_company_shares(company_id)

    # Cas 1 : aucune part enregistrée -> tout attribuer au PDG
    if not shares:
        ceo_id = company["ceo_id"]
        if not ceo_id:
            await update.message.reply_text(
                "❌ Aucune part trouvée et aucun PDG identifié pour cette entreprise."
            )
            return
        set_user_shares(company_id, ceo_id, 100)
        add_company_log(company_id, "🔧 Parts réinitialisées à 100% pour le PDG (fixparts)")
        await update.message.reply_text(
            f"✅ Aucune part n'existait pour *{company['name']}*.\n"
            f"100% attribué au PDG.",
            parse_mode="Markdown",
        )
        return

    total = sum(s["shares"] for s in shares)

    if total == 100:
        await update.message.reply_text(
            f"✅ Les parts de *{company['name']}* totalisent déjà 100%. Rien à corriger.",
            parse_mode="Markdown",
        )
        return

    if total <= 0:
        # Tous les détenteurs ont 0 part enregistrée : réattribuer au PDG
        ceo_id = company["ceo_id"]
        for s in shares:
            if s["user_id"] != ceo_id:
                set_user_shares(company_id, s["user_id"], 0)
        if ceo_id:
            set_user_shares(company_id, ceo_id, 100)
        add_company_log(company_id, "🔧 Parts réinitialisées à 100% pour le PDG (fixparts)")
        await update.message.reply_text(
            f"✅ Total de parts invalide (0%) pour *{company['name']}*.\n"
            f"100% réattribué au PDG.",
            parse_mode="Markdown",
        )
        return

    # Cas normal : redistribution proportionnelle au prorata des parts actuelles,
    # avec méthode du plus grand reste pour que la somme fasse exactement 100.
    raw_values = []
    for s in shares:
        exact = s["shares"] * 100 / total
        floor_val = int(exact)
        remainder = exact - floor_val
        raw_values.append({"user_id": s["user_id"], "old": s["shares"], "floor": floor_val, "remainder": remainder})

    allocated = sum(r["floor"] for r in raw_values)
    missing = 100 - allocated

    # Distribue les points manquants aux plus gros restes
    raw_values.sort(key=lambda r: r["remainder"], reverse=True)
    for i in range(missing):
        raw_values[i % len(raw_values)]["floor"] += 1

    lines = [f"🔧 *Parts recalculées pour {company['name']}*", f"(total précédent : {total}%)\n"]
    with get_conn() as conn:
        for r in raw_values:
            set_user_shares(company_id, r["user_id"], r["floor"])
            row = conn.execute(
                "SELECT first_name FROM players WHERE user_id = ?", (r["user_id"],)
            ).fetchone()
            display_name = (row["first_name"] if row else None) or f"#{r['user_id']}"
            lines.append(f"• {display_name} : {r['old']}% → {r['floor']}%")

    add_company_log(
        company_id,
        f"🔧 Parts normalisées à 100% (étaient à {total}%) via fixparts",
    )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ============================================================
# COMMANDES DE MASSE — Argent / Banque pour tous les joueurs
# ============================================================

def _parse_positive_amount(raw: str):
    """Retourne un int positif ou None si invalide."""
    try:
        amount = int(raw)
    except ValueError:
        return None
    if amount <= 0:
        return None
    return amount


@owner_only
async def addmoneyall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/addmoneyall montant — Ajoute le montant au solde (cash) de tous les joueurs."""
    if not context.args:
        await update.message.reply_text("Usage : /addmoneyall montant")
        return

    amount = _parse_positive_amount(context.args[0])
    if amount is None:
        await update.message.reply_text("❌ Montant invalide.")
        return

    with get_conn() as conn:
        user_ids = [r[0] for r in conn.execute("SELECT user_id FROM players").fetchall()]
        cur = conn.execute("UPDATE players SET balance = balance + ?", (amount,))
        nb = cur.rowcount
        now = int(time.time())
        conn.executemany(
            "INSERT INTO transactions (from_user, to_user, amount, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            [(None, uid, amount, f"admin_addmoneyall ({update.effective_user.first_name})", now) for uid in user_ids],
        )

    await update.message.reply_text(
        f"✅ {fmt_money(amount)} ajoutés au solde de *{nb}* joueur(s).",
        parse_mode="Markdown",
    )


@owner_only
async def removemoneyall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/removemoneyall montant — Retire le montant du solde (cash) de tous les joueurs (min. 0)."""
    if not context.args:
        await update.message.reply_text("Usage : /removemoneyall montant")
        return

    amount = _parse_positive_amount(context.args[0])
    if amount is None:
        await update.message.reply_text("❌ Montant invalide.")
        return

    with get_conn() as conn:
        user_ids = [r[0] for r in conn.execute("SELECT user_id FROM players").fetchall()]
        cur = conn.execute(
            "UPDATE players SET balance = MAX(0, balance - ?)", (amount,)
        )
        nb = cur.rowcount
        now = int(time.time())
        conn.executemany(
            "INSERT INTO transactions (from_user, to_user, amount, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            [(uid, None, amount, f"admin_removemoneyall ({update.effective_user.first_name})", now) for uid in user_ids],
        )

    await update.message.reply_text(
        f"✅ {fmt_money(amount)} retirés du solde de *{nb}* joueur(s) (plancher 0).",
        parse_mode="Markdown",
    )


@owner_only
async def addbanqueall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/addbanqueall montant — Ajoute le montant au compte en banque de tous les joueurs."""
    if not context.args:
        await update.message.reply_text("Usage : /addbanqueall montant")
        return

    amount = _parse_positive_amount(context.args[0])
    if amount is None:
        await update.message.reply_text("❌ Montant invalide.")
        return

    with get_conn() as conn:
        user_ids = [r[0] for r in conn.execute("SELECT DISTINCT user_id FROM user_bank_accounts").fetchall()]
        conn.execute(
            "UPDATE user_bank_accounts SET balance = balance + ?", (amount,)
        )
        nb = len(user_ids)
        now = int(time.time())
        conn.executemany(
            "INSERT INTO transactions (from_user, to_user, amount, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            [(None, uid, amount, f"admin_addbanqueall ({update.effective_user.first_name})", now) for uid in user_ids],
        )

    await update.message.reply_text(
        f"✅ {fmt_money(amount)} ajoutés à la banque de *{nb}* joueur(s) (chacun de leurs comptes).",
        parse_mode="Markdown",
    )


@owner_only
async def removebanqueall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/removebanqueall montant — Retire le montant du compte en banque de tous les joueurs (min. 0)."""
    if not context.args:
        await update.message.reply_text("Usage : /removebanqueall montant")
        return

    amount = _parse_positive_amount(context.args[0])
    if amount is None:
        await update.message.reply_text("❌ Montant invalide.")
        return

    with get_conn() as conn:
        user_ids = [r[0] for r in conn.execute("SELECT DISTINCT user_id FROM user_bank_accounts").fetchall()]
        conn.execute(
            "UPDATE user_bank_accounts SET balance = MAX(0, balance - ?)", (amount,)
        )
        nb = len(user_ids)
        now = int(time.time())
        conn.executemany(
            "INSERT INTO transactions (from_user, to_user, amount, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            [(uid, None, amount, f"admin_removebanqueall ({update.effective_user.first_name})", now) for uid in user_ids],
        )

    await update.message.reply_text(
        f"✅ {fmt_money(amount)} retirés de la banque de *{nb}* joueur(s) (plancher 0 par compte).",
        parse_mode="Markdown",
    )


# ============================================================
# RESET — Fixer le solde de tous les joueurs à une valeur exacte
# ============================================================

@owner_only
async def resetmoneyall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/resetmoneyall [montant] — Fixe le solde cash de TOUS les joueurs à
    la valeur donnée (0 par défaut si aucun montant n'est précisé)."""
    montant = 0
    if context.args:
        try:
            montant = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Montant invalide.")
            return
        if montant < 0:
            await update.message.reply_text("❌ Le montant doit être positif ou nul.")
            return

    with get_conn() as conn:
        cur = conn.execute("UPDATE players SET balance = ?", (montant,))
        nb = cur.rowcount

    await update.message.reply_text(
        f"✅ Solde cash de *{nb}* joueur(s) fixé à {fmt_money(montant)}.",
        parse_mode="Markdown",
    )


@owner_only
async def resetbanqueall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/resetbanqueall [montant] — Fixe le compte banque de TOUS les joueurs à
    la valeur donnée (0 par défaut si aucun montant n'est précisé)."""
    montant = 0
    if context.args:
        try:
            montant = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Montant invalide.")
            return
        if montant < 0:
            await update.message.reply_text("❌ Le montant doit être positif ou nul.")
            return

    with get_conn() as conn:
        conn.execute("UPDATE user_bank_accounts SET balance = ?", (montant,))
        nb = conn.execute("SELECT COUNT(DISTINCT user_id) FROM user_bank_accounts").fetchone()[0]

    await update.message.reply_text(
        f"✅ Compte(s) banque de *{nb}* joueur(s) fixé(s) à {fmt_money(montant)} (par compte).",
        parse_mode="Markdown",
    )


# ============================================================
# SAVE — Archivage du projet avec barre de progression animée
# ============================================================

@admin_or_owner_only
async def save_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /save — Archive tout le projet (sauf __pycache__, .cache, .local) 
    et l'envoie sur Telegram avec une barre de progression animée.
    Réservé aux administrateurs et à l'owner.
    """
    import zipfile
    import tempfile
    from datetime import datetime
    import os
    from pathlib import Path
    
    # ⚠️ CORRECTION ICI : remonter d'un niveau pour aller à la racine du projet
    # admin.py est dans handlers/, donc on remonte d'un niveau
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # Dossiers à ignorer
    IGNORE_DIRS = {
        '__pycache__',
        '.cache',
        '.local'
    }
    
    # Envoyer un message de progression initial
    status_msg = await update.message.reply_text(
        "📦 *Archivage du projet*\n"
        "⏳ Initialisation...\n\n"
        "🔒 *Dossiers exclus :*\n"
        "• __pycache__\n"
        "• .cache\n"
        "• .local",
        parse_mode="Markdown"
    )
    
    # Fonction pour mettre à jour la barre de progression
    async def update_progress(step: int, total: int, message: str):
        pct = int((step / total) * 100)
        bar_length = 20
        filled = int((step / total) * bar_length)
        bar = "█" * filled + "░" * (bar_length - filled)
        
        # Changer l'emoji en fonction de la progression
        if pct < 25:
            emoji = "⏳"
        elif pct < 50:
            emoji = "📂"
        elif pct < 75:
            emoji = "📦"
        else:
            emoji = "✅"
        
        text = (
            f"📦 *Archivage du projet*\n"
            f"{emoji} {message}\n\n"
            f"`{bar}` *{pct}%*\n"
            f"📁 {step}/{total} fichiers traités\n\n"
            f"🔒 Exclus : __pycache__, .cache, .local"
        )
        try:
            await status_msg.edit_text(text, parse_mode="Markdown")
        except Exception:
            # Si l'édition échoue, on ne bloque pas pour autant
            pass
    
    try:
        # Compter d'abord le nombre total de fichiers à archiver
        total_files = 0
        file_list = []
        
        await update_progress(0, 1, "Comptage des fichiers...")
        
        for root, dirs, files in os.walk(project_root):
            # Filtrer les dossiers à ignorer
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            
            for file in files:
                if file.endswith(('.pyc', '.pyo')):
                    continue
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, project_root)
                file_list.append((file_path, rel_path))
        
        total_files = len(file_list)
        
        if total_files == 0:
            await status_msg.edit_text(
                "❌ *Aucun fichier à archiver !*\n"
                "Vérifie que le projet n'est pas vide.",
                parse_mode="Markdown"
            )
            return
        
        # Créer l'archive
        with tempfile.TemporaryDirectory() as tmpdir:
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_name = f"LifeCity_Backup_{date_str}.zip"
            archive_path = os.path.join(tmpdir, archive_name)
            
            total_size = 0
            processed = 0
            
            with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path, rel_path in file_list:
                    processed += 1
                    
                    # Mettre à jour la progression toutes les 5 fichiers
                    if processed % 5 == 0 or processed == total_files:
                        await update_progress(
                            processed, 
                            total_files, 
                            f"Ajout de {os.path.basename(file_path)}"
                        )
                    
                    try:
                        zipf.write(file_path, rel_path)
                        total_size += os.path.getsize(file_path)
                    except Exception:
                        # Ignorer les fichiers qui posent problème
                        continue
            
            # Formater la taille
            size_mb = total_size / (1024 * 1024)
            if size_mb < 1024:
                size_str = f"{size_mb:.2f} MB"
            else:
                size_str = f"{size_mb/1024:.2f} GB"
            
            # Message de finalisation avec barre à 100%
            await update_progress(
                total_files, 
                total_files, 
                f"✅ Archive prête ! ({size_str})"
            )
            
            # Attendre un peu pour que l'utilisateur voie la barre à 100%
            await asyncio.sleep(0.5)
            
            # Envoyer le fichier
            await status_msg.edit_text(
                f"⬆️ *Envoi du fichier...*\n\n"
                f"📁 {total_files} fichiers\n"
                f"💾 {size_str}\n"
                f"📅 {datetime.now().strftime('%d/%m/%Y %H:%M')}",
                parse_mode="Markdown"
            )
            
            with open(archive_path, 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    filename=archive_name,
                    caption=(
                        f"📦 *Sauvegarde du projet LifeCity*\n"
                        f"📅 Date : {datetime.now().strftime('%d/%m/%Y à %H:%M')}\n"
                        f"📁 {total_files} fichiers\n"
                        f"💾 {size_str}\n\n"
                        f"🔒 *Dossiers exclus :*\n"
                        f"• __pycache__\n"
                        f"• .cache\n"
                        f"• .local\n\n"
                        f"👤 Demandé par : {update.effective_user.first_name}"
                    ),
                    parse_mode="Markdown"
                )
            
            # Supprimer le message de progression
            await status_msg.delete()
            
    except PermissionError as e:
        await status_msg.edit_text(
            f"❌ *Erreur de permission*\n"
            f"Impossible d'accéder à certains fichiers.\n\n"
            f"```\n{str(e)}\n```",
            parse_mode="Markdown"
        )
    except Exception as e:
        await status_msg.edit_text(
            f"❌ *Erreur lors de l'archivage*\n\n"
            f"```\n{str(e)}\n```",
            parse_mode="Markdown"
        )
        raise e