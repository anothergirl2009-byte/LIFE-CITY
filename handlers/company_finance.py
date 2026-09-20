"""
LifeCity Bot - Entreprises (partie 2 : finance & contrats)
/depotboite — Déposer en trésorerie
/retraitboite — Retirer de la trésorerie
/logsboite — Historique de l'entreprise
/parts — Voir la répartition des parts
/mesparts — Voir ses parts dans toutes les entreprises
/acheterparts — Proposer d'acheter des parts (le PDG doit accepter)
/mesachats — Voir mes demandes d'achat en cours
/annulerachat — Annuler une demande d'achat
/vendreparts — Vendre des parts instantanément (rachat direct par la trésorerie de l'entreprise)
/versersalaires — PDG : payer les salaires
(💸 dividendes : automatique, chaque dimanche, 3% de la trésorerie de chaque
 entreprise versés aux actionnaires — voir run_weekly_dividends, à brancher
 sur le JobQueue du bot, aucune commande joueur)
/proposersalaire — Proposer un salaire à un employé
/accepteroffre — Accepter une offre de salaire
/refuseroffre — Refuser une offre de salaire
/classement — Classement des entreprises
/proposercontrat — Proposer un contrat à une autre entreprise
/acceptercontrat / /refusercontrat — Traiter un contrat
/mescontrats — Voir les contrats actifs
"""

import time
import sqlite3
import asyncio
import random
import logging
from types import SimpleNamespace

from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

from db import (
    get_or_create_player, get_conn, update_player, add_balance,
    get_company_by_name, get_company_by_id, get_company_employees,
    update_company_treasury, add_company_log, get_company_logs, withdraw_from_treasury,
    get_company_shares, get_user_shares, set_user_shares,
    get_all_companies, get_companies_by_sector,
    get_bureau_contracts, create_bureau_contract,
    update_bureau_contract_progress, claim_bureau_contract,
    get_pending_request, delete_pending_request, create_pending_request,
    execute_share_purchase, execute_share_sale, get_or_update_valuation_ref,
    delete_company, get_player_by_id, get_owned_company, get_player_by_name_or_id,
    get_state_treasury, add_state_treasury, get_company_buildings, set_building_suspended,
    get_or_init_member_baseline, delete_member_baselines, set_company_last_salary_payment,
)
from utils import fmt_money
try:
    from handlers.company import (
        BUILDING_SLOTS_BY_KEY, _building_display_name, get_company_treasury_cap,
        get_company_contract_revenue_bonus, get_company_daily_revenue_bonus,
        get_company_reputation_bonus,
    )
except ImportError:
    from company import (
        BUILDING_SLOTS_BY_KEY, _building_display_name, get_company_treasury_cap,
        get_company_contract_revenue_bonus, get_company_daily_revenue_bonus,
        get_company_reputation_bonus,
    )


def _is_ceo_or_director(player) -> bool:
    return player["company_role"] in ("PDG", "Directeur")


def _get_manageable_company(user_id: int, player):
    """Renvoie l'entreprise que ce joueur peut gérer en tant que PDG ou directeur.

    Priorité à la PROPRIÉTÉ (companies.ceo_id) : un PDG garde le contrôle total
    de sa propre entreprise même s'il a par ailleurs pris un emploi salarié
    ailleurs (son company_id/role d'emploi ne reflète alors que ce second job).
    Sinon, retombe sur son emploi actuel s'il y est PDG ou Directeur.
    """
    owned = get_owned_company(user_id)
    if owned is not None:
        return owned
    if player["company_id"] and _is_ceo_or_director(player):
        return get_company_by_id(player["company_id"])
    return None


def get_ceo_of_company(company_id: int):
    """Récupère le PDG d'une entreprise via companies.ceo_id (pas via company_id/role
    des joueurs, qui ne reflète que leur EMPLOI actuel — un PDG peut désormais aussi
    être employé ailleurs tout en restant propriétaire de sa propre entreprise)."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT p.* FROM players p JOIN companies c ON c.ceo_id = p.user_id WHERE c.company_id = ?",
            (company_id,)
        ).fetchone()


def get_share_value(company_id: int) -> int:
    """
    Calcule la valeur dynamique d'une part.
    Formule : Valorisation lissée / Nombre total de parts (100 parts par défaut)

    Avant, la formule était "trésorerie brute / 100" : un PDG pouvait vider
    la caisse pour faire chuter le prix, racheter ses propres parts au sol,
    puis redéposer l'argent. Ici on utilise get_or_update_valuation_ref, qui
    ne laisse pas la valorisation chuter plus vite qu'un taux fixe par heure
    — un retrait suivi d'un rachat immédiat n'affecte donc plus le prix.
    """
    company = get_company_by_id(company_id)
    if company is None:
        return 100

    total_shares = 100
    if total_shares <= 0:
        return 100

    valuation = get_or_update_valuation_ref(company_id)
    value = valuation // total_shares
    return max(100, int(value))


def get_company_total_shares(company_id: int) -> int:
    """Retourne le nombre total de parts d'une entreprise (fixe = 100)."""
    return 100


# ============================================================
# TABLE POUR LES DEMANDES D'ACHAT
# ============================================================

def create_purchase_request(buyer_id: int, company_id: int, shares: int, total_price: int) -> int:
    """Crée une demande d'achat de parts."""
    with get_conn() as conn:
        # Vérifier si la table existe, sinon la créer
        conn.execute("""
            CREATE TABLE IF NOT EXISTS purchase_requests (
                request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                buyer_id INTEGER NOT NULL,
                company_id INTEGER NOT NULL,
                shares INTEGER NOT NULL,
                total_price INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL
            )
        """)
        cur = conn.execute(
            """INSERT INTO purchase_requests (buyer_id, company_id, shares, total_price, status, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?)""",
            (buyer_id, company_id, shares, total_price, int(time.time()))
        )
        return cur.lastrowid


def get_purchase_request(request_id: int):
    """Récupère une demande d'achat."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM purchase_requests WHERE request_id = ?", (request_id,)
        ).fetchone()


def get_pending_purchase_requests_for_company(company_id: int):
    """Récupère toutes les demandes d'achat en attente pour une entreprise."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT pr.*, p.first_name as buyer_name
               FROM purchase_requests pr
               JOIN players p ON pr.buyer_id = p.user_id
               WHERE pr.company_id = ? AND pr.status = 'pending'
               ORDER BY pr.created_at DESC""",
            (company_id,)
        ).fetchall()


def get_pending_purchase_requests_for_buyer(buyer_id: int):
    """Récupère toutes les demandes d'achat en attente d'un acheteur."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT pr.*, c.name as company_name
               FROM purchase_requests pr
               JOIN companies c ON pr.company_id = c.company_id
               WHERE pr.buyer_id = ? AND pr.status = 'pending'
               ORDER BY pr.created_at DESC""",
            (buyer_id,)
        ).fetchall()


def update_purchase_request_status(request_id: int, status: str):
    """Met à jour le statut d'une demande d'achat."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE purchase_requests SET status = ? WHERE request_id = ?",
            (status, request_id)
        )


# ============================================================
# TRÉSORERIE
# ============================================================

async def depotboite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if not player["company_id"]:
        await update.message.reply_text("❌ Tu ne travailles dans aucune entreprise.")
        return

    if not context.args:
        await update.message.reply_text("Utilisation : /depotboite montant")
        return

    try:
        amount = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return

    if amount <= 0 or player["balance"] < amount:
        await update.message.reply_text("❌ Montant invalide ou solde insuffisant.")
        return

    company_before = get_company_by_id(player["company_id"])
    cap = get_company_treasury_cap(player["company_id"])
    current_treasury = company_before["treasury"] or 0
    if current_treasury >= cap:
        await update.message.reply_text(
            f"❌ Trésorerie déjà au maximum autorisé ({fmt_money(cap)}).\n"
            f"Achète un 📦 Entrepôt (/batiments) pour augmenter ce plafond."
        )
        return
    if current_treasury + amount > cap:
        amount = cap - current_treasury
        await update.message.reply_text(
            f"⚠️ Montant réduit à {fmt_money(amount)} pour respecter le plafond de trésorerie ({fmt_money(cap)})."
        )

    add_balance(user.id, -amount)
    update_company_treasury(player["company_id"], amount)
    company = get_company_by_id(player["company_id"])
    add_company_log(player["company_id"], f"{player['first_name']} a déposé {fmt_money(amount)}")

    await update.message.reply_text(
        f"✅ {fmt_money(amount)} déposés dans la trésorerie de *{company['name']}*.",
        parse_mode="Markdown",
    )


async def retraitboite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = get_owned_company(user.id)
    if company is None:
        await update.message.reply_text("❌ Réservé au PDG. Seul le PDG a accès à la trésorerie de l'entreprise.")
        return

    if not context.args:
        await update.message.reply_text("Utilisation : /retraitboite montant")
        return

    try:
        amount = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return

    if amount <= 0:
        await update.message.reply_text("❌ Montant invalide.")
        return

    if not withdraw_from_treasury(company["company_id"], amount):
        await update.message.reply_text("❌ Trésorerie insuffisante.")
        return

    add_balance(user.id, amount)
    add_company_log(company["company_id"], f"{player['first_name']} a retiré {fmt_money(amount)}")

    await update.message.reply_text(f"✅ {fmt_money(amount)} retirés de la trésorerie.")


async def logsboite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if not player["company_id"]:
        await update.message.reply_text("❌ Tu ne travailles dans aucune entreprise.")
        return

    logs = get_company_logs(player["company_id"])
    if not logs:
        await update.message.reply_text("Aucun historique.")
        return

    lines = ["📜 *Historique de l'entreprise*\n"]
    for log in logs:
        lines.append(f"• {log['message']}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ============================================================
# PARTS
# ============================================================

async def parts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if context.args:
        name = " ".join(context.args)
        company = get_company_by_name(name)
        if company is None:
            await update.message.reply_text("❌ Entreprise introuvable.")
            return
    else:
        if not player["company_id"]:
            await update.message.reply_text("❌ Précise un nom : /parts nom_entreprise")
            return
        company = get_company_by_id(player["company_id"])

    share_value = get_share_value(company["company_id"])
    
    shares = get_company_shares(company["company_id"])
    if not shares:
        await update.message.reply_text("Aucune part émise.")
        return

    lines = [f"📊 *Répartition des parts — {company['name']}*\n"]
    lines.append(f"💰 Valeur d'une part : *{fmt_money(share_value)}*\n")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    distributed_shares = sum(s["shares"] for s in shares)
    
    for s in shares:
        with get_conn() as conn:
            holder = conn.execute(
                "SELECT * FROM players WHERE user_id = ?", (s["user_id"],)
            ).fetchone()
        name_h = holder["first_name"] if holder else "Inconnu"
        part_value = s["shares"] * share_value
        lines.append(f"• {name_h} — {s['shares']}% ({fmt_money(part_value)})")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📊 Total parts : {distributed_shares}%")
    lines.append(f"🏦 Trésorerie : {fmt_money(company['treasury'])}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def bilan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bilan financier détaillé d'une entreprise : /bilan [nom_entreprise].
    Sans argument, affiche le bilan de ta propre entreprise (si tu en as une)
    ou de celle où tu travailles."""
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if context.args:
        name = " ".join(context.args)
        company = get_company_by_name(name)
        if company is None:
            await update.message.reply_text("❌ Entreprise introuvable.")
            return
    else:
        company = get_owned_company(user.id)
        if company is None and player["company_id"]:
            company = get_company_by_id(player["company_id"])
        if company is None:
            await update.message.reply_text(
                "❌ Tu ne possèdes ni ne travailles dans aucune entreprise.\n"
                "Utilisation : /bilan nom_entreprise"
            )
            return

    company_id = company["company_id"]
    employees = get_company_employees(company_id)
    shares = get_company_shares(company_id)
    treasury = company["treasury"] or 0
    share_value = get_share_value(company_id)
    valuation = share_value * 100
    daily_payroll = sum((e["salary"] or 0) for e in employees)

    with get_conn() as conn:
        ceo = conn.execute("SELECT * FROM players WHERE user_id = ?", (company["ceo_id"],)).fetchone()
        active_contracts = conn.execute(
            """SELECT cc.*, c1.name AS from_name, c2.name AS to_name
               FROM company_contracts cc
               JOIN companies c1 ON c1.company_id = cc.from_company_id
               JOIN companies c2 ON c2.company_id = cc.to_company_id
               WHERE (cc.from_company_id = ? OR cc.to_company_id = ?) AND cc.status = 'active'""",
            (company_id, company_id),
        ).fetchall()

    lines = [
        f"📊 *BILAN FINANCIER — {company['name']}*",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🏦 Trésorerie actuelle : *{fmt_money(treasury)}*",
        f"💎 Valeur estimée (100 parts) : *{fmt_money(valuation)}*",
        f"👥 Employés : *{len(employees)}*",
        f"💵 Masse salariale/jour : *{fmt_money(daily_payroll)}*",
        "",
        "💸 *Cumuls historiques*",
        f"🏛️ Impôts payés au total : *{fmt_money(company['total_tax_paid'] or 0)}*",
        f"💰 Dividendes versés au total : *{fmt_money(company['total_dividends_paid'] or 0)}*",
        "",
        "📈 *Actionnariat*",
    ]

    if shares:
        for s in shares[:10]:
            with get_conn() as conn:
                holder = conn.execute("SELECT first_name FROM players WHERE user_id = ?", (s["user_id"],)).fetchone()
            nom = holder["first_name"] if holder else "Inconnu"
            tag = " (PDG)" if s["user_id"] == company["ceo_id"] else ""
            lines.append(f"  • {nom}{tag} — {s['shares']}%")
    else:
        lines.append("  Aucun actionnaire enregistré.")

    lines.append("")
    lines.append("🤝 *Contrats actifs*")
    if active_contracts:
        for c in active_contracts:
            partner = c["to_name"] if c["from_company_id"] == company_id else c["from_name"]
            expires_in = c["expires_at"] - int(time.time())
            jours_restants = max(0, expires_in // 86400)
            rare_tag = "🌟 " if c["is_rare"] else ""
            lines.append(
                f"  • {rare_tag}Avec *{partner}* — +{c['bonus_percent']}% ({jours_restants}j restants)"
            )
    else:
        lines.append("  Aucun contrat actif.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def mesparts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Voir ses parts dans toutes les entreprises."""
    user = update.effective_user

    with get_conn() as conn:
        shares = conn.execute(
            """SELECT cs.company_id, cs.shares, c.name, c.treasury
               FROM company_shares cs
               JOIN companies c ON cs.company_id = c.company_id
               WHERE cs.user_id = ?
               ORDER BY cs.shares DESC""",
            (user.id,)
        ).fetchall()

    if not shares:
        await update.message.reply_text(
            "📊 *Tes parts*\n\n"
            "Tu ne possèdes aucune part dans aucune entreprise.\n"
            "Utilise /acheterparts pour en acheter !",
            parse_mode="Markdown"
        )
        return

    lines = ["📊 *Tes parts dans les entreprises*\n"]
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    total_value = 0
    for s in shares:
        share_value = get_share_value(s["company_id"])
        part_value = s["shares"] * share_value
        total_value += part_value
        
        bar_length = 10
        filled = min(s["shares"] // 10, bar_length)
        bar = "█" * filled + "░" * (bar_length - filled)
        
        lines.append(f"🏢 *{s['name']}*")
        lines.append(f"   📊 Parts : {s['shares']}% {bar}")
        lines.append(f"   💰 Valeur : {fmt_money(part_value)} (part unitaire : {fmt_money(share_value)})")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"💰 *Valeur totale de ton portefeuille* : {fmt_money(total_value)}")
    lines.append("\n💡 Utilise /vendreparts pour vendre tes parts.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ============================================================
# ACHAT DE PARTS (AVEC APPROBATION DU PDG)
# ============================================================

async def acheterparts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Propose d'acheter des parts. Le PDG doit accepter ou refuser."""
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if len(context.args) < 2:
        await update.message.reply_text(
            "📥 *Acheter des parts*\n\n"
            "Utilisation : /acheterparts nb nom_entreprise\n"
            "Exemple : /acheterparts 10 THETEENRICH\n\n"
            "📝 Le PDG recevra une notification et devra accepter ou refuser.",
            parse_mode="Markdown"
        )
        return

    try:
        nb = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Nombre de parts invalide.")
        return

    # Tout le reste est blindé : si une erreur inattendue survient, le
    # gestionnaire d'erreurs global du bot se contente de la logger (le
    # joueur ne voit RIEN). Ici on attrape nous-mêmes pour informer le
    # joueur au lieu de laisser la commande mourir en silence.
    try:
        name = " ".join(context.args[1:])
        company = get_company_by_name(name)
        if company is None:
            await update.message.reply_text("❌ Entreprise introuvable.")
            return

        if company["ceo_id"] == user.id:
            await update.message.reply_text("❌ Tu ne peux pas acheter des parts de ta propre entreprise.")
            return

        share_value = get_share_value(company["company_id"])
        total_cost = share_value * nb

        if player["balance"] < total_cost:
            await update.message.reply_text(
                f"❌ Il te faut {fmt_money(total_cost)} pour acheter ces parts.\n"
                f"💰 Ton solde : {fmt_money(player['balance'])}",
                parse_mode="Markdown"
            )
            return

        ceo = get_ceo_of_company(company["company_id"])
        if not ceo:
            await update.message.reply_text("❌ Cette entreprise n'a pas de PDG actif, achat impossible.")
            return

        ceo_shares = get_user_shares(company["company_id"], ceo["user_id"])
        if nb > ceo_shares:
            await update.message.reply_text(
                f"❌ Le PDG ne possède que {ceo_shares}% de parts disponibles à la vente.\n"
                f"Tu ne peux pas demander plus que ça."
            )
            return

        request_id = create_purchase_request(user.id, company["company_id"], nb, total_cost)

        try:
            keyboard = [
                [
                    InlineKeyboardButton("✅ Accepter", callback_data=f"accept_purchase_{request_id}"),
                    InlineKeyboardButton("❌ Refuser", callback_data=f"refuse_purchase_{request_id}"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await context.bot.send_message(
                chat_id=ceo["user_id"],
                text=(
                    f"🔔 *Demande d'achat de parts !*\n\n"
                    f"👤 *Acheteur :* {player['first_name']}\n"
                    f"📊 *Parts demandées :* {nb}%\n"
                    f"💰 *Prix unitaire :* {fmt_money(share_value)}\n"
                    f"💵 *Total :* {fmt_money(total_cost)}\n"
                    f"🏢 *{company['name']}*\n\n"
                    f"Veuillez accepter ou refuser cette demande."
                ),
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.warning(f"⚠️ acheterparts : impossible d'envoyer le PV au PDG ({ceo['user_id']}) : {e}")
            await update.message.reply_text(
                "⚠️ Demande créée, mais impossible d'envoyer le message au PDG "
                "(il n'a peut-être jamais démarré une conversation privée avec le bot — "
                "il doit taper /start en PV au bot au moins une fois)."
            )

        add_company_log(
            company["company_id"],
            f"{player['first_name']} a demandé à acheter {nb} parts pour {fmt_money(total_cost)}"
        )

        await update.message.reply_text(
            f"📤 *Demande d'achat envoyée !*\n\n"
            f"🏢 Entreprise : *{company['name']}*\n"
            f"📊 Parts demandées : *{nb}%*\n"
            f"💰 Montant : *{fmt_money(total_cost)}*\n"
            f"💵 Prix unitaire : *{fmt_money(share_value)}*\n\n"
            f"⏳ En attente de l'approbation du PDG.\n"
            f"Tu seras notifié quand il aura répondu.\n\n"
            f"💡 Tu peux annuler ta demande avec /annulerachat.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.exception(f"❌ Erreur dans /acheterparts pour user {user.id} : {e}")
        await update.message.reply_text(
            f"❌ Une erreur est survenue pendant l'achat de parts.\n"
            f"Détail (pour le debug) : `{type(e).__name__}: {e}`",
            parse_mode="Markdown"
        )


async def mesachats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Voir toutes mes demandes d'achat en cours."""
    user = update.effective_user
    
    requests = get_pending_purchase_requests_for_buyer(user.id)
    
    if not requests:
        await update.message.reply_text(
            "📭 *Aucune demande d'achat en cours*\n\n"
            "Tu n'as aucune demande d'achat en attente.\n"
            "Utilise /acheterparts pour en faire une.",
            parse_mode="Markdown"
        )
        return
    
    lines = ["📋 *Mes demandes d'achat en cours*\n"]
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    for req in requests:
        lines.append(f"🏢 *{req['company_name']}*")
        lines.append(f"   📊 Parts : {req['shares']}%")
        lines.append(f"   💰 Total : {fmt_money(req['total_price'])}")
        lines.append(f"   📅 Demandé le : {time.strftime('%d/%m/%Y %H:%M', time.localtime(req['created_at']))}")
        lines.append(f"   🆔 ID : #{req['request_id']}")
        lines.append("")
    
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 Utilise /annulerachat ID pour annuler une demande.")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def annulerachat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Annule une demande d'achat en cours."""
    user = update.effective_user
    
    if not context.args:
        await update.message.reply_text(
            "❌ Utilisation : /annulerachat ID\n"
            "Exemple : /annulerachat 1\n\n"
            "Utilise /mesachats pour voir tes demandes.",
            parse_mode="Markdown"
        )
        return
    
    try:
        request_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID invalide.")
        return
    
    request = get_purchase_request(request_id)
    if not request:
        await update.message.reply_text("❌ Demande d'achat introuvable.")
        return
    
    if request["buyer_id"] != user.id:
        await update.message.reply_text("❌ Tu n'es pas le demandeur de cette demande.")
        return
    
    if request["status"] != "pending":
        await update.message.reply_text(
            f"❌ Cette demande a déjà été traitée (statut : {request['status']})."
        )
        return
    
    update_purchase_request_status(request_id, "cancelled")
    
    company = get_company_by_id(request["company_id"])
    
    await update.message.reply_text(
        f"❌ Demande d'achat annulée.\n"
        f"🏢 {company['name']} — {request['shares']}% de parts.",
        parse_mode="Markdown"
    )


# ============================================================
# CALLBACKS POUR LES DEMANDES D'ACHAT
# ============================================================

async def handle_purchase_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gère les réponses du PDG aux demandes d'achat."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    # callback_data est de la forme "accept_purchase_<id>" ou "refuse_purchase_<id>".
    # (avant : data.split("_", 2) lisait "purchase" comme action au lieu de
    # "accept"/"refuse" -> le clic du PDG ne faisait jamais rien)
    parts_data = data.split("_")
    action = parts_data[0]
    request_id = int(parts_data[-1])
    
    request = get_purchase_request(request_id)
    if not request:
        await query.edit_message_text("❌ Demande d'achat introuvable.")
        return
    
    if request["status"] != "pending":
        await query.edit_message_text(
            f"❌ Cette demande a déjà été traitée (statut : {request['status']})."
        )
        return
    
    buyer = get_player_by_id(request["buyer_id"])
    company = get_company_by_id(request["company_id"])
    ceo = get_ceo_of_company(request["company_id"])
    
    if action == "accept":
        if update.effective_user.id != ceo["user_id"]:
            await query.edit_message_text("❌ Seul le PDG peut traiter cette demande.")
            return
        
        # Exécution ATOMIQUE : solde acheteur, parts du PDG, trésorerie et
        # parts de l'acheteur sont vérifiés puis mis à jour dans UNE SEULE
        # transaction (execute_share_purchase). Fini le risque d'un débit
        # appliqué sans que les parts soient transférées (ou l'inverse) en
        # cas d'erreur/crash entre deux étapes.
        max_retries = 3
        success = False
        for attempt in range(max_retries):
            try:
                success = execute_share_purchase(
                    request["company_id"],
                    ceo["user_id"],
                    request["buyer_id"],
                    request["shares"],
                    request["total_price"],
                )
                break
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                await query.edit_message_text(f"❌ Erreur lors de l'achat, réessaie. ({e})")
                return

        if not success:
            # Rechecke pourquoi ça a échoué pour donner un message précis
            fresh_buyer = get_player_by_id(request["buyer_id"])
            ceo_shares_now = get_user_shares(request["company_id"], ceo["user_id"])
            if fresh_buyer["balance"] < request["total_price"]:
                await query.edit_message_text(
                    f"❌ L'acheteur n'a plus assez d'argent.\n"
                    f"💰 Solde actuel : {fmt_money(fresh_buyer['balance'])}\n"
                    f"💰 Prix : {fmt_money(request['total_price'])}"
                )
            else:
                await query.edit_message_text(
                    f"❌ Tu ne possèdes plus que {ceo_shares_now}% de parts disponibles.\n"
                    f"Demande : {request['shares']}%"
                )
            update_purchase_request_status(request_id, "failed")
            return

        update_purchase_request_status(request_id, "accepted")

        add_company_log(
            request["company_id"],
            f"{buyer['first_name']} a acheté {request['shares']} parts à {ceo['first_name']} pour {fmt_money(request['total_price'])}"
        )

        try:
            await context.bot.send_message(
                chat_id=request["buyer_id"],
                text=(
                    f"✅ *Achat approuvé !*\n\n"
                    f"🏢 Entreprise : *{company['name']}*\n"
                    f"📊 Parts achetées : *{request['shares']}%*\n"
                    f"💰 Montant : *{fmt_money(request['total_price'])}*\n\n"
                    f"Le PDG a accepté ta demande d'achat !"
                ),
                parse_mode="Markdown"
            )
        except Exception:
            pass

        await query.edit_message_text(
            f"✅ *Demande d'achat acceptée !*\n\n"
            f"🏢 {company['name']}\n"
            f"👤 Acheteur : {buyer['first_name']}\n"
            f"📊 Parts : {request['shares']}%\n"
            f"💰 Montant reçu sur ton compte : {fmt_money(request['total_price'])}\n\n"
            f"💵 L'acheteur a été notifié et les parts ont été transférées.",
            parse_mode="Markdown"
        )
        
    elif action == "refuse":
        if update.effective_user.id != ceo["user_id"]:
            await query.edit_message_text("❌ Seul le PDG peut traiter cette demande.")
            return
        
        update_purchase_request_status(request_id, "refused")
        
        try:
            await context.bot.send_message(
                chat_id=request["buyer_id"],
                text=(
                    f"❌ *Achat refusé !*\n\n"
                    f"🏢 Entreprise : *{company['name']}*\n"
                    f"📊 Parts demandées : *{request['shares']}%*\n"
                    f"💰 Montant : *{fmt_money(request['total_price'])}*\n\n"
                    f"Le PDG a refusé ta demande d'achat."
                ),
                parse_mode="Markdown"
            )
        except Exception:
            pass
        
        await query.edit_message_text(
            f"❌ *Demande d'achat refusée*\n\n"
            f"🏢 {company['name']}\n"
            f"👤 Acheteur : {buyer['first_name']}\n"
            f"📊 Parts : {request['shares']}%\n"
            f"💰 Montant : {fmt_money(request['total_price'])}",
            parse_mode="Markdown"
        )


# ============================================================
# VENTE DE PARTS (MARCHÉ)
# ============================================================

async def vendreparts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Vend des parts instantanément : rachat direct par la trésorerie de l'entreprise,
    l'argent tombe immédiatement sur le compte du vendeur (pas d'attente d'acheteur).

    On peut posséder des parts dans PLUSIEURS entreprises (achetées via /acheterparts,
    ou sa propre boîte), indépendamment de là où on travaille actuellement — il faut
    donc préciser l'entreprise, /vendreparts ne peut plus deviner ça depuis l'emploi."""
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if len(context.args) < 2:
        await update.message.reply_text(
            "📤 *Vendre des parts*\n\n"
            "Utilisation : /vendreparts nom_entreprise nb\n"
            "Exemple : /vendreparts MaBoite 10\n\n"
            "💰 Le prix est automatiquement calculé et versé immédiatement.\n"
            "Utilise /mesparts pour voir dans quelles entreprises tu as des parts.",
            parse_mode="Markdown"
        )
        return

    try:
        nb = int(context.args[-1])
    except ValueError:
        await update.message.reply_text("❌ Nombre de parts invalide.")
        return

    name = " ".join(context.args[:-1])
    company = get_company_by_name(name)
    if company is None:
        await update.message.reply_text("❌ Entreprise introuvable.")
        return

    company_id = company["company_id"]

    if nb <= 0:
        await update.message.reply_text("❌ Le nombre de parts doit être supérieur à 0.")
        return

    owned = get_user_shares(company_id, user.id)
    if owned < nb:
        await update.message.reply_text(
            f"❌ Tu ne possèdes que {owned}% de parts dans *{company['name']}*.",
            parse_mode="Markdown"
        )
        return

    share_value = get_share_value(company_id)
    total_price = share_value * nb

    if company["treasury"] < total_price:
        await update.message.reply_text(
            f"❌ Trésorerie insuffisante pour racheter ces parts.\n"
            f"💰 Requis : {fmt_money(total_price)}\n"
            f"🏦 Disponible : {fmt_money(company['treasury'])}"
        )
        return

    # Tout (vérif trésorerie, débit trésorerie, retrait des parts, crédit du
    # vendeur) est fait dans UNE SEULE transaction via execute_share_sale,
    # pour éviter qu'un crash entre deux étapes ne laisse la trésorerie
    # débitée sans que le vendeur soit crédité (ou l'inverse).
    max_retries = 3
    success = False
    for attempt in range(max_retries):
        try:
            success = execute_share_sale(company_id, user.id, nb, total_price)
            break
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < max_retries - 1:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            else:
                await update.message.reply_text(
                    f"❌ Erreur lors de la vente. Veuillez réessayer.\n"
                    f"Erreur : {str(e)}"
                )
                return

    if not success:
        await update.message.reply_text(
            "❌ La vente a échoué (trésorerie de l'entreprise insuffisante entre-temps, "
            "ou parts déjà vendues ailleurs)."
        )
        return

    add_company_log(
        company_id,
        f"{player['first_name']} a vendu {nb} parts à {fmt_money(share_value)} chacune ({fmt_money(total_price)})"
    )

    # Si le PDG vient de vendre ses dernières parts, l'entreprise se
    # liquide : les actionnaires externes restants (ceux qui ont acheté des
    # parts via /acheterparts) sont remboursés au prorata depuis la
    # trésorerie, puis l'entreprise disparaît.
    company_after = get_company_by_id(company_id)
    ceo_shares_now = get_user_shares(company_id, company_after["ceo_id"])

    if ceo_shares_now <= 0:
        dissolved_company_id = company_id
        treasury_now = company_after["treasury"]
        remaining_holders = get_company_shares(dissolved_company_id)
        former_employees = get_company_employees(dissolved_company_id)

        payout_lines = []
        for holder in remaining_holders:
            payout = (treasury_now * holder["shares"]) // 100
            if payout <= 0:
                continue
            add_balance(holder["user_id"], payout)
            with get_conn() as conn:
                h = conn.execute(
                    "SELECT first_name FROM players WHERE user_id = ?", (holder["user_id"],)
                ).fetchone()
            payout_lines.append(f"• {h['first_name'] if h else 'Inconnu'} ({holder['shares']}%) — {fmt_money(payout)}")
            if holder["user_id"] != user.id:
                try:
                    await context.bot.send_message(
                        chat_id=holder["user_id"],
                        text=(
                            f"💥 *{company['name']} a été dissoute !*\n\n"
                            f"Le PDG a vendu toutes ses parts. Ton intérêt dans l'entreprise "
                            f"t'a été remboursé : *{fmt_money(payout)}* (pour {holder['shares']}% de parts).\n"
                            f"Tu es maintenant sans emploi si tu y travaillais."
                        ),
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

        add_company_log(dissolved_company_id, f"Entreprise dissoute (PDG a vendu toutes ses parts)")
        delete_company(dissolved_company_id)

        notified_ids = {h["user_id"] for h in remaining_holders}
        for e in former_employees:
            if e["user_id"] == user.id or e["user_id"] in notified_ids:
                continue
            try:
                await context.bot.send_message(
                    chat_id=e["user_id"],
                    text=(
                        f"💥 *{company['name']} a été dissoute !*\n\n"
                        f"Le PDG a vendu toutes ses parts. Tu es maintenant sans emploi."
                    ),
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        msg = (
            f"✅ *Vente effectuée !*\n\n"
            f"🏢 Entreprise : *{company['name']}*\n"
            f"📊 Parts vendues : *{nb}%*\n"
            f"💰 Prix unitaire : *{fmt_money(share_value)}*\n"
            f"💵 Total reçu : *{fmt_money(total_price)}*\n\n"
            f"💥 *Tu as vendu tes dernières parts : {company['name']} n'existe plus !*"
        )
        if payout_lines:
            msg += "\n\n💸 *Actionnaires remboursés :*\n" + "\n".join(payout_lines)

        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    await update.message.reply_text(
        f"✅ *Vente effectuée !*\n\n"
        f"🏢 Entreprise : *{company['name']}*\n"
        f"📊 Parts vendues : *{nb}%*\n"
        f"💰 Prix unitaire : *{fmt_money(share_value)}*\n"
        f"💵 Total reçu : *{fmt_money(total_price)}*\n\n"
        f"🏦 Montant crédité immédiatement sur ton compte.",
        parse_mode="Markdown"
    )


# ============================================================
# SALAIRES
# ============================================================

# Un versement de salaires équivaut à 1 mois de salaire, versable une fois
# toutes les 24h (pas de spam de /versersalaires payer pour driver la trésorerie).
SALARY_PAYMENT_COOLDOWN_SECONDS = 24 * 60 * 60


async def versersalaires(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = get_owned_company(user.id)
    if company is None:
        await update.message.reply_text("❌ Réservé au PDG.")
        return

    employees = get_company_employees(company["company_id"])
    total_due = sum(e["salary"] for e in employees)

    now = int(time.time())
    last_payment = company["last_salary_payment"] or 0
    remaining = SALARY_PAYMENT_COOLDOWN_SECONDS - (now - last_payment)

    if not context.args:
        lines = ["💰 *Suggestions de salaires à verser (1 mois)*\n"]
        for e in employees:
            lines.append(f"• {e['first_name']} — {fmt_money(e['salary'])}")
        lines.append(f"\nTotal : {fmt_money(total_due)}")
        lines.append(f"Trésorerie disponible : {fmt_money(company['treasury'])}")
        if last_payment and remaining > 0:
            heures = remaining // 3600
            minutes = (remaining % 3600) // 60
            lines.append(f"\n⏳ Prochain versement possible dans {heures}h {minutes}min.")
        else:
            lines.append("\nUtilise /versersalaires payer pour payer.")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    if context.args[0] == "payer":
        if last_payment and remaining > 0:
            heures = remaining // 3600
            minutes = (remaining % 3600) // 60
            await update.message.reply_text(
                f"⏳ Les salaires (1 mois) ne peuvent être versés qu'une fois toutes les 24h.\n"
                f"Prochain versement possible dans {heures}h {minutes}min."
            )
            return

        if total_due <= 0:
            await update.message.reply_text("Aucun salaire à verser.")
            return

        if not withdraw_from_treasury(company["company_id"], total_due):
            await update.message.reply_text("❌ Trésorerie insuffisante.")
            return

        for e in employees:
            if e["salary"] > 0:
                add_balance(e["user_id"], e["salary"])

        set_company_last_salary_payment(company["company_id"], now)
        add_company_log(company["company_id"], f"Salaires versés ({fmt_money(total_due)})")

        await update.message.reply_text(f"✅ Salaires (1 mois) versés pour un total de {fmt_money(total_due)}.")
    else:
        await update.message.reply_text("Utilisation : /versersalaires ou /versersalaires payer")


# ============================================================
# DIVIDENDES (ACTIONNAIRES) — AUTOMATIQUE, chaque dimanche
# ============================================================

DIVIDEND_RATE = 0.03  # 3% de la trésorerie de chaque entreprise, chaque dimanche
COMPANY_TAX_RATE = 0.03  # 3% de la trésorerie de chaque entreprise, prélevés chaque jour


async def run_daily_company_tax(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Job automatique (planifié chaque jour via le JobQueue) : prélève 3% de la
    trésorerie de CHAQUE entreprise et le verse dans le fonds État. Aucune
    commande à taper, ça tombe tout seul. Pas de DM au PDG à chaque fois
    (ça ferait un spam quotidien) — juste un log consultable via /logsboite.
    """
    companies = get_all_companies()

    for company in companies:
        try:
            company_id = company["company_id"]
            treasury = company["treasury"] or 0
            tax = int(treasury * COMPANY_TAX_RATE)
            if tax <= 0:
                continue

            if not withdraw_from_treasury(company_id, tax):
                continue

            add_state_treasury(tax)
            with get_conn() as conn:
                conn.execute(
                    "UPDATE companies SET total_tax_paid = total_tax_paid + ? WHERE company_id = ?",
                    (tax, company_id),
                )
            add_company_log(company_id, f"🏛️ Impôt quotidien prélevé : -{fmt_money(tax)} (3% de la trésorerie)")
        except Exception:
            logging.exception(f"Erreur impôt quotidien pour l'entreprise {company['company_id']}")
            continue


async def run_daily_building_maintenance(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Job automatique (planifié chaque jour) : prélève la maintenance de chaque
    bâtiment possédé sur la trésorerie de l'entreprise. Si la trésorerie ne
    suffit pas, le bâtiment passe en "suspendu" (ses effets ne s'appliquent
    plus tant que la maintenance n'est pas repayée). Se réactive tout seul
    dès que la trésorerie permet à nouveau de payer.
    """
    companies = get_all_companies()

    for company in companies:
        try:
            company_id = company["company_id"]
            buildings = get_company_buildings(company_id)
            if not buildings:
                continue

            for b_row in buildings:
                slot = b_row["slot"]
                b = BUILDING_SLOTS_BY_KEY.get(slot)
                if b is None:
                    continue

                maintenance = b["maintenance"]
                current_company = get_company_by_id(company_id)
                if current_company is None:
                    break
                treasury = current_company["treasury"] or 0

                if treasury >= maintenance and withdraw_from_treasury(company_id, maintenance):
                    if b_row["suspended"]:
                        set_building_suspended(company_id, slot, False)
                        name, _ = _building_display_name(current_company["sector"], slot)
                        add_company_log(company_id, f"✅ {name} réactivé (maintenance repayée)")
                else:
                    if not b_row["suspended"]:
                        set_building_suspended(company_id, slot, True)
                        name, _ = _building_display_name(current_company["sector"], slot)
                        add_company_log(company_id, f"🚫 {name} suspendu (maintenance impayée : {fmt_money(maintenance)})")
        except Exception:
            logging.exception(f"Erreur maintenance bâtiments pour l'entreprise {company['company_id']}")
            continue


async def run_weekly_dividends(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Job automatique (à planifier chaque dimanche via le JobQueue du bot,
    voir instructions de branchement) : verse 3% de la trésorerie de
    CHAQUE entreprise à ses actionnaires, au prorata de leurs parts (le
    PDG compris s'il détient encore des parts). Aucune commande à taper,
    ça tombe tout seul.
    """
    companies = get_all_companies()
    now = int(time.time())

    for company in companies:
        try:
            company_id = company["company_id"]
            treasury = company["treasury"] or 0
            amount = int(treasury * DIVIDEND_RATE)
            if amount <= 0:
                continue

            shares = get_company_shares(company_id)
            if not shares:
                continue

            update_company_treasury(company_id, -amount)
            with get_conn() as conn:
                conn.execute(
                    "UPDATE companies SET last_dividend_at = ?, total_dividends_paid = total_dividends_paid + ? "
                    "WHERE company_id = ?",
                    (now, amount, company_id),
                )

            for s in shares:
                payout = (amount * s["shares"]) // 100
                if payout <= 0:
                    continue
                add_balance(s["user_id"], payout)
                try:
                    await context.bot.send_message(
                        chat_id=s["user_id"],
                        text=(
                            f"💸 *Dividendes hebdomadaires !*\n\n"
                            f"🏢 *{company['name']}* t'a versé *{fmt_money(payout)}* "
                            f"({s['shares']}% de parts)."
                        ),
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

            add_company_log(
                company_id,
                f"Dividendes hebdomadaires versés : {fmt_money(amount)} (3% de la trésorerie)"
            )
        except Exception:
            # Une erreur sur une entreprise ne doit jamais bloquer les autres.
            continue


async def testdividendes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Commande OWNER-ONLY : déclenche immédiatement run_weekly_dividends, pour
    tester sans attendre dimanche. Ne remplace pas le job automatique.
    """
    from config import OWNER_ID

    user = update.effective_user
    if user.id != OWNER_ID:
        await update.message.reply_text("❌ Réservé au owner.")
        return

    await update.message.reply_text("⏳ Déclenchement manuel des dividendes hebdomadaires...")
    await run_weekly_dividends(context)
    await update.message.reply_text("✅ Terminé. Vérifie /parts ou les logs (/logsboite) d'une entreprise.")


async def proposersalaire(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Propose un salaire à un employé : /proposersalaire id_ou_pseudo montant
    (ou en réponse à son message : /proposersalaire montant). Réservé au PDG."""
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = get_owned_company(user.id)
    if company is None:
        await update.message.reply_text("❌ Réservé au PDG.")
        return

    if not context.args:
        await update.message.reply_text(
            "Utilisation : /proposersalaire id_ou_pseudo montant\n"
            "(ou en réponse à son message : /proposersalaire montant)"
        )
        return

    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
        amount_args = context.args
    else:
        row = get_player_by_name_or_id(context.args[0])
        if row is None:
            await update.message.reply_text("❌ Joueur introuvable.")
            return
        target = SimpleNamespace(id=row["user_id"], username=row["username"], first_name=row["first_name"])
        amount_args = context.args[1:]

    if target.id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas te proposer un salaire à toi-même.")
        return

    if not amount_args:
        await update.message.reply_text("❌ Il manque le montant.")
        return

    try:
        amount = int(amount_args[0])
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return

    if amount < 0:
        await update.message.reply_text("❌ Le salaire ne peut pas être négatif.")
        return

    target_player = get_or_create_player(target.id, target.username, target.first_name)
    if target_player["company_id"] != company["company_id"]:
        await update.message.reply_text("❌ Cette personne ne travaille pas dans ton entreprise.")
        return

    if company["treasury"] < amount:
        await update.message.reply_text(
            f"❌ Trésorerie insuffisante. Disponible : {fmt_money(company['treasury'])}"
        )
        return

    create_pending_request(
        kind="salary",
        from_user=user.id,
        to_user=target.id,
        ttl_seconds=300,
        amount=amount,
    )

    await update.message.reply_text(
        f"💼 *Offre de salaire*\n\n"
        f"{target.first_name}, *{player['first_name']}* te propose un salaire de *{fmt_money(amount)}*.\n\n"
        f"Réponds avec /accepteroffre ou /refuseroffre dans les 5 minutes.",
        parse_mode="Markdown"
    )


async def accepteroffre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Accepter une offre de salaire."""
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    req = get_pending_request("salary", to_user=user.id)

    if req is None:
        await update.message.reply_text("❌ Tu n'as aucune offre de salaire en attente.")
        return

    proposer = get_player_by_id(req["from_user"])
    amount = req["amount"]

    if player["company_id"] != proposer["company_id"]:
        await update.message.reply_text("❌ Vous ne travaillez pas dans la même entreprise.")
        return

    update_player(user.id, salary=amount)
    delete_pending_request(req["request_id"])

    await update.message.reply_text(
        f"✅ Tu as accepté l'offre de salaire de *{fmt_money(amount)}* !\n"
        f"Le PDG devra payer les salaires avec /versersalaires.",
        parse_mode="Markdown"
    )


async def refuseroffre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refuser une offre de salaire."""
    user = update.effective_user

    req = get_pending_request("salary", to_user=user.id)

    if req is None:
        await update.message.reply_text("❌ Tu n'as aucune offre de salaire en attente.")
        return

    delete_pending_request(req["request_id"])
    await update.message.reply_text("❌ Offre de salaire refusée.")


# ============================================================
# CLASSEMENT
# ============================================================

async def classement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args:
        sector = " ".join(context.args)
        companies = get_companies_by_sector(sector)
        title = f"🏆 *Classement — secteur {sector}*\n"
    else:
        companies = get_all_companies()
        title = "🏆 *Classement global des entreprises*\n"

    if not companies:
        await update.message.reply_text("Aucune entreprise trouvée.")
        return

    lines = [title]
    medals = ["🥇", "🥈", "🥉"]
    for i, c in enumerate(companies[:10]):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        share_value = get_share_value(c["company_id"])
        lines.append(f"{prefix} {c['name']} — {fmt_money(c['treasury'])} (part : {fmt_money(share_value)})")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ============================================================
# PRÉSENCES
# ============================================================

async def presences(update, context) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if not player["company_id"] or player["company_role"] not in ("PDG", "Directeur"):
        await update.message.reply_text("❌ Réservé au PDG ou aux Directeurs.")
        return

    employees = get_company_employees(player["company_id"])
    company = get_company_by_id(player["company_id"])

    if not employees:
        await update.message.reply_text("Aucun employé.")
        return

    with get_conn() as conn:
        emp_data = []
        for e in employees:
            row = conn.execute(
                "SELECT cmd_count FROM players WHERE user_id = ?", (e["user_id"],)
            ).fetchone()
            cmd_count = row["cmd_count"] if row else 0
            emp_data.append((e["first_name"], e["company_role"], cmd_count))

    emp_data.sort(key=lambda x: x[2], reverse=True)

    lines = [f"📊 *Présences — {company['name']}*\n"]
    medals = ["🥇","🥈","🥉"] + ["👤"] * 50
    for i, (name, role, count) in enumerate(emp_data):
        bar = "█" * min(count // 5, 15) + "░" * max(0, 15 - count // 5)
        lines.append(f"{medals[i]} *{name}* [{role}]\n   └ {count} commandes {bar}")

    total = sum(x[2] for x in emp_data)
    lines.append(f"\n📈 Total entreprise : *{total} commandes*")
    lines.append("💡 Chaque commande utilisée sur le bot = 1 présence.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ============================================================
# CONTRATS ENTRE ENTREPRISES
# ============================================================

CONTRACT_BONUS_PERCENT = 8        # bonus revenus normal, pour les deux entreprises
CONTRACT_DURATION_DAYS = 7        # durée fixe du partenariat
RARE_CONTRACT_CHANCE = 0.15       # 15% de chances d'obtenir un contrat rare
RARE_CONTRACT_BONUS_PERCENT = 20  # bonus revenus d'un contrat rare


async def proposercontrat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    own_company = _get_manageable_company(user.id, player)
    if own_company is None:
        await update.message.reply_text("❌ Réservé au PDG ou aux directeurs.")
        return

    if not context.args:
        await update.message.reply_text("Utilisation : /proposercontrat nom_entreprise")
        return

    name = " ".join(context.args)
    target_company = get_company_by_name(name)
    if target_company is None:
        await update.message.reply_text("❌ Entreprise introuvable.")
        return

    if target_company["company_id"] == own_company["company_id"]:
        await update.message.reply_text("❌ Tu ne peux pas proposer un contrat à ta propre entreprise.")
        return

    is_rare = random.random() < RARE_CONTRACT_CHANCE
    bonus_percent = RARE_CONTRACT_BONUS_PERCENT if is_rare else CONTRACT_BONUS_PERCENT

    # 🖥️ Datacenter : +10% sur le bonus du contrat, si possédé et actif (chez
    # l'une des deux entreprises impliquées).
    datacenter_bonus = max(
        get_company_contract_revenue_bonus(own_company["company_id"]),
        get_company_contract_revenue_bonus(target_company["company_id"]),
    )
    if datacenter_bonus > 0:
        bonus_percent = round(bonus_percent * (1 + datacenter_bonus))

    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO company_contracts
               (from_company_id, to_company_id, status, reward, bonus_percent, duration_days, is_rare, created_at)
               VALUES (?, ?, 'pending', 0, ?, ?, ?, ?)""",
            (
                own_company["company_id"],
                target_company["company_id"],
                bonus_percent,
                CONTRACT_DURATION_DAYS,
                int(is_rare),
                int(time.time()),
            ),
        )
        contract_id = cur.lastrowid

    if is_rare:
        await update.message.reply_text(
            f"🌟 *CONTRAT RARE !* 🌟\n\n"
            f"Contrat #{contract_id} proposé à *{target_company['name']}* avec un bonus exceptionnel de "
            f"*+{RARE_CONTRACT_BONUS_PERCENT}%* !",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"📄 Contrat #{contract_id} proposé à *{target_company['name']}*.",
            parse_mode="Markdown",
        )

    target_ceo = get_ceo_of_company(target_company["company_id"])
    if target_ceo is not None:
        titre = "🌟 *CONTRAT RARE reçu !* 🌟" if is_rare else "🤝 *Proposition de contrat reçue !*"
        notif = (
            f"{titre}\n\n"
            f"*{own_company['name']}* propose un partenariat avec *{target_company['name']}*.\n\n"
            f"📈 Bonus revenus : +{bonus_percent}% pour les deux\n"
            f"⏳ Durée : {CONTRACT_DURATION_DAYS} jours"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Accepter", callback_data=f"contratdecision|accept|{contract_id}"),
            InlineKeyboardButton("❌ Refuser", callback_data=f"contratdecision|refuse|{contract_id}"),
        ]])
        try:
            await context.bot.send_message(
                chat_id=target_ceo["user_id"], text=notif, parse_mode="Markdown", reply_markup=keyboard
            )
        except Exception:
            pass


async def handle_contract_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gère les clics sur les boutons ✅/❌ d'une proposition de contrat (/proposercontrat)."""
    query = update.callback_query

    try:
        _, action, contract_id_str = query.data.split("|")
        contract_id = int(contract_id_str)
    except (ValueError, AttributeError):
        await query.answer()
        return

    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = _get_manageable_company(user.id, player)
    if company is None:
        await query.answer("❌ Tu n'es plus PDG ou directeur de cette entreprise.", show_alert=True)
        return

    with get_conn() as conn:
        contract = conn.execute(
            "SELECT * FROM company_contracts WHERE contract_id = ?", (contract_id,)
        ).fetchone()

    if contract is None or contract["to_company_id"] != company["company_id"] or contract["status"] != "pending":
        await query.answer("❌ Contrat introuvable ou déjà traité.", show_alert=True)
        return

    is_rare = bool(contract["is_rare"])
    tag = "🌟 " if is_rare else ""

    if action == "refuse":
        await query.answer()
        with get_conn() as conn:
            conn.execute("UPDATE company_contracts SET status = 'refused' WHERE contract_id = ?", (contract_id,))
        await query.edit_message_text(f"❌ {tag}Contrat #{contract_id} refusé.")

        from_ceo = get_ceo_of_company(contract["from_company_id"])
        if from_ceo is not None:
            to_company = get_company_by_id(contract["to_company_id"])
            notif = f"❌ *Contrat #{contract_id} refusé.*\n\n*{to_company['name']}* a refusé votre proposition de partenariat."
            try:
                await context.bot.send_message(chat_id=from_ceo["user_id"], text=notif, parse_mode="Markdown")
            except Exception:
                pass
        return

    await query.answer()

    duration_days = contract["duration_days"] or CONTRACT_DURATION_DAYS
    expires_at = int(time.time()) + duration_days * 86400

    with get_conn() as conn:
        conn.execute(
            "UPDATE company_contracts SET status = 'active', expires_at = ? WHERE contract_id = ?",
            (expires_at, contract_id),
        )

    await query.edit_message_text(f"✅ {tag}Contrat #{contract_id} accepté.")

    from_ceo = get_ceo_of_company(contract["from_company_id"])
    if from_ceo is not None:
        to_company = get_company_by_id(contract["to_company_id"])
        titre = "🌟 *Contrat RARE accepté !* 🌟" if is_rare else f"✅ *Contrat #{contract_id} accepté !*"
        notif = (
            f"{titre}\n\n"
            f"*{to_company['name']}* a accepté votre proposition de partenariat.\n"
            f"📈 Bonus revenus : +{contract['bonus_percent'] or CONTRACT_BONUS_PERCENT}% pour les deux\n"
            f"⏳ Durée : {duration_days} jours"
        )
        try:
            await context.bot.send_message(chat_id=from_ceo["user_id"], text=notif, parse_mode="Markdown")
        except Exception:
            pass


async def acceptercontrat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = _get_manageable_company(user.id, player)
    if company is None:
        await update.message.reply_text("❌ Réservé au PDG ou aux directeurs.")
        return

    if not context.args:
        await update.message.reply_text("Utilisation : /acceptercontrat id")
        return

    try:
        contract_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID invalide.")
        return

    with get_conn() as conn:
        contract = conn.execute(
            "SELECT * FROM company_contracts WHERE contract_id = ?", (contract_id,)
        ).fetchone()

    if contract is None or contract["to_company_id"] != company["company_id"] or contract["status"] != "pending":
        await update.message.reply_text("❌ Contrat introuvable ou déjà traité.")
        return

    duration_days = contract["duration_days"] or CONTRACT_DURATION_DAYS
    expires_at = int(time.time()) + duration_days * 86400

    with get_conn() as conn:
        conn.execute(
            "UPDATE company_contracts SET status = 'active', expires_at = ? WHERE contract_id = ?",
            (expires_at, contract_id),
        )

    await update.message.reply_text(f"✅ Contrat #{contract_id} accepté.")

    from_ceo = get_ceo_of_company(contract["from_company_id"])
    if from_ceo is not None:
        to_company = get_company_by_id(contract["to_company_id"])
        notif = (
            f"✅ *Contrat #{contract_id} accepté !*\n\n"
            f"*{to_company['name']}* a accepté votre proposition de partenariat.\n"
            f"📈 Bonus revenus : +{contract['bonus_percent'] or CONTRACT_BONUS_PERCENT}% pour les deux\n"
            f"⏳ Durée : {duration_days} jours"
        )
        try:
            await context.bot.send_message(chat_id=from_ceo["user_id"], text=notif, parse_mode="Markdown")
        except Exception:
            pass


async def refusercontrat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = _get_manageable_company(user.id, player)
    if company is None:
        await update.message.reply_text("❌ Réservé au PDG ou aux directeurs.")
        return

    if not context.args:
        await update.message.reply_text("Utilisation : /refusercontrat id")
        return

    try:
        contract_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID invalide.")
        return

    with get_conn() as conn:
        contract = conn.execute(
            "SELECT * FROM company_contracts WHERE contract_id = ?", (contract_id,)
        ).fetchone()

    if contract is None or contract["to_company_id"] != company["company_id"] or contract["status"] != "pending":
        await update.message.reply_text("❌ Contrat introuvable ou déjà traité.")
        return

    with get_conn() as conn:
        conn.execute("UPDATE company_contracts SET status = 'refused' WHERE contract_id = ?", (contract_id,))

    await update.message.reply_text(f"❌ Contrat #{contract_id} refusé.")

    from_ceo = get_ceo_of_company(contract["from_company_id"])
    if from_ceo is not None:
        to_company = get_company_by_id(contract["to_company_id"])
        notif = f"❌ *Contrat #{contract_id} refusé.*\n\n*{to_company['name']}* a refusé votre proposition de partenariat."
        try:
            await context.bot.send_message(chat_id=from_ceo["user_id"], text=notif, parse_mode="Markdown")
        except Exception:
            pass


async def mescontrats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if not player["company_id"]:
        await update.message.reply_text("❌ Tu ne travailles dans aucune entreprise.")
        return

    with get_conn() as conn:
        contracts = conn.execute(
            """SELECT * FROM company_contracts
               WHERE (from_company_id = ? OR to_company_id = ?) AND status = 'active'""",
            (player["company_id"], player["company_id"]),
        ).fetchall()

    if not contracts:
        await update.message.reply_text("Aucun contrat actif.")
        return

    lines = ["📄 *Contrats actifs*\n"]
    for c in contracts:
        from_c = get_company_by_id(c["from_company_id"])
        to_c = get_company_by_id(c["to_company_id"])
        lines.append(f"• #{c['contract_id']} — {from_c['name']} ↔ {to_c['name']}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ============================================================
# BUREAU DES CONTRATS
# ============================================================

BUREAU_TASKS = {
    "Technologie":  [("Développer de nouvelles fonctionnalités", 10, 500_000), ("Lancer un nouveau produit", 20, 1_200_000), ("Former les employés", 15, 800_000)],
    "Finance":      [("Effectuer des transactions", 15, 600_000), ("Verser les salaires", 3, 400_000), ("Atteindre un objectif de commandes", 50, 2_000_000)],
    "Immobilier":   [("Signer des contrats", 5, 700_000), ("Recruter des employés", 2, 300_000), ("Atteindre un objectif de présences", 30, 1_000_000)],
    "Restauration": [("Servir des clients", 20, 400_000), ("Ouvrir de nouvelles tables", 3, 250_000), ("Atteindre un objectif de commandes", 40, 1_500_000)],
    "Industrie":    [("Produire des unités", 25, 900_000), ("Former les ouvriers", 3, 350_000), ("Atteindre un objectif de présences", 60, 2_500_000)],
    "Commerce":     [("Réaliser des ventes", 10, 500_000), ("Recruter un commercial", 1, 200_000), ("Atteindre un objectif de commandes", 35, 1_300_000)],
    "Transport":    [("Livrer des colis", 15, 600_000), ("Effectuer des trajets", 5, 350_000), ("Atteindre un objectif de présences", 45, 1_800_000)],
    "Médias":       [("Publier des articles", 8, 400_000), ("Atteindre un objectif d'interactions", 25, 1_000_000), ("Former des journalistes", 2, 300_000)],
    "Santé":        [("Traiter des patients", 12, 700_000), ("Former des médecins", 4, 500_000), ("Atteindre un objectif de présences", 50, 2_000_000)],
    "Énergie":      [("Produire de l'énergie", 30, 1_000_000), ("Installer des équipements", 5, 600_000), ("Atteindre un objectif de commandes", 70, 3_000_000)],
}

# La récompense d'une mission Bureau dépend du BUDGET (trésorerie) de
# l'entreprise, pas d'un montant fixe. Chaque mission d'un secteur est
# classée facile/moyenne/difficile selon son ancien montant fixe (le plus
# petit = facile, le plus gros = difficile), et ce taux s'applique à la
# trésorerie actuelle. Plancher de 1000€ dans tous les cas.
BUREAU_DIFFICULTY_RATES = (0.05, 0.10, 0.15)  # facile, moyen, difficile
BUREAU_MIN_REWARD = 1000
BUREAU_MIN_TARGET = 750           # objectif MINIMUM de commandes, quelle que soit la trésorerie
BUREAU_TARGET_GROWTH_UNIT = 10_000  # plus petit = l'objectif grimpe plus vite avec la trésorerie


def _compute_bc_progress(bc_id: int, employees) -> int:
    """
    Calcule la progression d'une mission Bureau en comptant, pour CHAQUE
    employé actuel, uniquement les commandes faites depuis SON propre point
    de départ (soit la création de la mission s'il était déjà là, soit son
    arrivée dans l'entreprise s'il a rejoint après) — jamais son historique
    d'avant. Ça évite qu'un nouvel employé fasse exploser la progression
    d'un coup avec tout son passif de commandes.
    """
    total = 0
    with get_conn() as conn:
        for e in employees:
            row = conn.execute("SELECT cmd_count FROM players WHERE user_id = ?", (e["user_id"],)).fetchone()
            current = row["cmd_count"] if row else 0
            baseline = get_or_init_member_baseline(bc_id, e["user_id"], current)
            total += max(0, current - baseline)
    return total


def _recompute_bc_target_reward(contract, current_treasury: int) -> tuple[int, int]:
    """
    Recalcule l'objectif et la récompense d'un contrat Bureau selon la
    trésorerie ACTUELLE de l'entreprise, pas celle du moment de la création.
    Si la trésorerie baisse (retrait, taxe...), tout baisse ; si elle monte,
    tout monte — impossible de figer une grosse récompense en gonflant la
    trésorerie puis en retirant l'argent juste après.
    """
    treasury = max(0, current_treasury)

    # Accès défensif : si la migration de db.py n'a pas encore tourné (colonne
    # absente), on retombe sur l'ancien comportement au lieu de planter.
    contract_keys = contract.keys() if hasattr(contract, "keys") else contract
    rate = (contract["reward_rate"] if "reward_rate" in contract_keys else None) or 0.0

    # 750 est un PLANCHER minimum, pas un plafond : ça grimpe sans limite
    # avec la trésorerie (en racine carrée pour rester raisonnable même sur
    # des trésoreries énormes, plutôt que d'exploser linéairement).
    target = BUREAU_MIN_TARGET + round((treasury / BUREAU_TARGET_GROWTH_UNIT) ** 0.5)

    reward = max(BUREAU_MIN_REWARD, round(treasury * rate))

    # 🏭 Usine : +10% sur la récompense, si possédée et active.
    daily_bonus = get_company_daily_revenue_bonus(contract["company_id"])
    if daily_bonus > 0:
        reward = round(reward * (1 + daily_bonus))

    return target, reward


async def soumettredossier(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = get_owned_company(user.id)
    if company is None:
        await update.message.reply_text("❌ Réservé au PDG.")
        return

    sector = company["sector"]

    existing = get_bureau_contracts(company["company_id"])
    active = [c for c in existing if c["status"] == "active"]
    if len(active) >= 2:
        await update.message.reply_text(
            f"❌ Tu as déjà 2 contrats Bureau actifs (le maximum) ! "
            f"Utilise /mescontratsbc pour les suivre, ou /claimcontratbc pour en terminer un."
        )
        return

    tasks = BUREAU_TASKS.get(sector, BUREAU_TASKS["Commerce"])
    task_name, base_target, base_reward = random.choice(tasks)

    # Classe la mission choisie facile/moyenne/difficile parmi les 3 du
    # secteur (selon son ancien montant fixe), pour déterminer le taux à
    # appliquer à la trésorerie actuelle.
    sorted_rewards = sorted(t[2] for t in tasks)
    difficulty_rank = sorted_rewards.index(base_reward)
    rate = BUREAU_DIFFICULTY_RATES[difficulty_rank]

    treasury = max(0, company["treasury"])

    # 750 est un PLANCHER minimum, pas un plafond : ça grimpe sans limite
    # avec la trésorerie (en racine carrée pour rester raisonnable même sur
    # des trésoreries énormes, plutôt que d'exploser linéairement).
    target = BUREAU_MIN_TARGET + round((treasury / BUREAU_TARGET_GROWTH_UNIT) ** 0.5)

    reward = max(BUREAU_MIN_REWARD, round(treasury * rate))

    employees = get_company_employees(company["company_id"])

    bc_id = create_bureau_contract(
        company["company_id"], sector, task_name, target, reward, 0,
        base_target=base_target, reward_rate=rate,
    )

    # Chaque employé actuel a son propre point de départ initialisé à SON
    # compteur du moment — pas un total global figé pour toute l'entreprise.
    with get_conn() as conn:
        for e in employees:
            row = conn.execute("SELECT cmd_count FROM players WHERE user_id = ?", (e["user_id"],)).fetchone()
            current = row["cmd_count"] if row else 0
            get_or_init_member_baseline(bc_id, e["user_id"], current)

    difficulty_label = ("🟢 Facile", "🟡 Moyen", "🔴 Difficile")[difficulty_rank]

    await update.message.reply_text(
        f"📋 *Contrat Bureau assigné !*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏢 Entreprise : *{company['name']}*\n"
        f"📁 Secteur : *{sector}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Mission : *{task_name}*\n"
        f"⚡ Difficulté : *{difficulty_label}*\n"
        f"🎯 Objectif : *{target} commandes*\n"
        f"💰 Récompense : *{fmt_money(reward)}* ({int(rate*100)}% de la trésorerie)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Progrès : *0/{target}* (0%)\n"
        f"💼 Trésorerie : *{fmt_money(treasury)}*\n"
        f"\n✅ Claim avec /claimcontratbc dès que l'objectif est atteint.",
        parse_mode="Markdown"
    )

    # Informe toute l'équipe qu'une nouvelle mission est lancée : leurs
    # commandes à partir de MAINTENANT comptent pour l'objectif.
    notif = (
        f"📋 *Nouvelle mission Bureau lancée chez {company['name']} !*\n\n"
        f"📌 {task_name}\n"
        f"🎯 Objectif : {target} commandes (à partir de maintenant)\n"
        f"💰 Récompense si réussie : {fmt_money(reward)}\n\n"
        f"Chaque commande que tu tapes compte pour l'objectif de l'équipe !"
    )
    for e in employees:
        if e["user_id"] == user.id:
            continue
        try:
            await context.bot.send_message(chat_id=e["user_id"], text=notif, parse_mode="Markdown")
        except Exception:
            pass


async def mescontratsbc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if not player["company_id"]:
        await update.message.reply_text("❌ Tu n'es dans aucune entreprise.")
        return

    contracts = get_bureau_contracts(player["company_id"])
    company = get_company_by_id(player["company_id"])

    if not contracts:
        await update.message.reply_text(
            f"📭 Aucun contrat Bureau.\nLe PDG peut en obtenir un avec /soumettredossier."
        )
        return

    employees = get_company_employees(player["company_id"])

    lines = [f"📋 *Contrats Bureau — {company['name']}*\n"]
    for c in contracts:
        if c["status"] == "claimed":
            status_emoji = "✅"
            progress_txt = "Terminé !"
        else:
            target, reward = _recompute_bc_target_reward(c, company["treasury"])
            progress = max(0, min(_compute_bc_progress(c["bc_id"], employees), target))
            update_bureau_contract_progress(c["bc_id"], progress)
            pct = min(100, int(progress / target * 100))
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            progress_txt = f"{progress}/{target} [{bar}] {pct}%"
            status_emoji = "🟢" if pct >= 100 else "🔄"

        lines.append(
            f"{status_emoji} *#{c['bc_id']}* — {c['task']}\n"
            f"   📊 {progress_txt}\n"
            f"   💰 Récompense actuelle : {fmt_money(reward if c['status'] != 'claimed' else c['reward'])}"
        )

    if any(c["status"] == "active" for c in contracts):
        lines.append("\n✅ /claimcontratbc pour réclamer.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def claimcontratbc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = get_owned_company(user.id)
    if company is None:
        await update.message.reply_text("❌ Réservé au PDG.")
        return

    contracts = get_bureau_contracts(company["company_id"])
    active = [c for c in contracts if c["status"] == "active"]

    if not active:
        await update.message.reply_text("❌ Aucun contrat Bureau actif.")
        return

    employees = get_company_employees(company["company_id"])

    def _current(c):
        target, reward = _recompute_bc_target_reward(c, company["treasury"])
        progress = max(0, min(_compute_bc_progress(c["bc_id"], employees), target))
        return target, reward, progress

    # Avec 2 contrats actifs possibles, on peut préciser lequel réclamer :
    # /claimcontratbc <id>. Sans argument, on réclame automatiquement le seul
    # qui est complété (ou on demande de préciser s'il y en a plusieurs).
    if context.args:
        try:
            target_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ ID invalide.")
            return
        matching = [c for c in active if c["bc_id"] == target_id]
        if not matching:
            await update.message.reply_text("❌ Contrat introuvable parmi tes contrats actifs.")
            return
        contract = matching[0]
    else:
        completed = [c for c in active if _current(c)[2] >= _current(c)[0]]
        if not completed:
            lines = ["⏳ Aucun contrat encore complété !\n"]
            for c in active:
                target, reward, p = _current(c)
                pct = int(p / target * 100)
                lines.append(f"• *#{c['bc_id']}* {c['task']} — {p}/{target} ({pct}%)")
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
            return
        if len(completed) > 1:
            ids = ", ".join(f"/claimcontratbc {c['bc_id']}" for c in completed)
            await update.message.reply_text(
                f"✅ Plusieurs contrats sont complétés ! Précise lequel réclamer :\n{ids}"
            )
            return
        contract = completed[0]

    target, reward, progress = _current(contract)
    if progress < target:
        remaining = target - progress
        pct = int(progress / target * 100)
        await update.message.reply_text(
            f"⏳ Objectif pas encore atteint !\n"
            f"📊 Progrès : *{progress}/{target}* ({pct}%)\n"
            f"Il manque *{remaining} commandes*.",
            parse_mode="Markdown"
        )
        return

    # La récompense versée est celle recalculée à l'instant du claim, selon
    # la trésorerie ACTUELLE — pas celle figée à la création du contrat.
    update_company_treasury(company["company_id"], reward)
    claim_bureau_contract(contract["bc_id"])
    delete_member_baselines(contract["bc_id"])
    add_company_log(
        company["company_id"],
        f"Contrat Bureau '{contract['task']}' complété — +{fmt_money(reward)}"
    )

    await update.message.reply_text(
        f"🎉 *Contrat Bureau complété !*\n"
        f"📌 Mission : *{contract['task']}*\n"
        f"💰 *+{fmt_money(reward)}* versés !\n"
        f"\nSoumets un nouveau dossier avec /soumettredossier.",
        parse_mode="Markdown"
    )