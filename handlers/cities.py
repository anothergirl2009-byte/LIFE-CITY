"""
LifeCity Bot - Mairie de LIFECITY (ville unique)
Tous les joueurs sont citoyens de LIFECITY (attribué automatiquement).

/maville — Fiche de la mairie de LIFECITY
/candidature — Se présenter comme maire (PDG uniquement, 10 milliards requis)
/retirercandidature — Retirer sa candidature de l'élection en cours
/candidats — Voir les candidats à l'élection en cours + décompte des votes
/voter — Voter pour un candidat
/fixerimpot — (maire) Fixer le taux d'impôt municipal (0-15%)
/caissemairie — (maire) Voir la caisse de la mairie
/retraitmairie — (maire) Retirer de la caisse municipale (destitution auto si abus)
/historiquemaire — Historique des maires de LIFECITY

Admin (owner) :
/ouvrirelection — Ouvrir une élection
/trancherelection — Désigner le vainqueur d'une élection (l'owner tranche)
/revoquermaire — Révoquer immédiatement le maire en poste et relancer une élection

Tâches planifiées (voir main.py) :
run_weekly_city_taxes — Prélève l'impôt municipal hebdo sur le salaire des citoyens
run_daily_mandate_check — Fin de mandat automatique (2 semaines) -> nouvelle élection
"""

import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from db import (
    get_or_create_player, get_player_by_id, update_player, add_balance,
    find_player_by_identifier, get_user_total_bank_balance,
    add_state_treasury,
    CITY_NAME,
    MAYOR_TAX_MIN, MAYOR_TAX_MAX, MAYOR_EMBEZZLEMENT_RATIO,
    MAYOR_MANDATE_DURATION, MAYOR_STATE_CUT, MAYOR_CANDIDACY_THRESHOLD,
    get_all_cities, get_city, get_city_by_mayor,
    get_city_residents, set_city_tax_rate, add_city_treasury,
    withdraw_city_treasury, install_mayor, remove_mayor,
    get_mayors_with_expired_mandate,
    open_election, get_open_election, get_election, add_candidate, remove_candidate,
    get_candidates, is_candidate, cast_vote, get_vote_counts, close_election,
    get_mayor_history,
)
from utils import fmt_money


def _fmt_cooldown(seconds: int) -> str:
    if seconds <= 0:
        return "maintenant"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    if days:
        return f"{days}j {hours}h"
    if hours:
        return f"{hours}h {minutes}min"
    return f"{minutes}min"


def _display_name(row) -> str:
    if row is None:
        return "Inconnu"
    return row["username"] and f"@{row['username']}" or (row["first_name"] or str(row["user_id"]))


async def maville(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)

    city = get_city(CITY_NAME)
    mayor = get_player_by_id(city["mayor_id"]) if city["mayor_id"] else None
    residents = get_city_residents(city["name"])
    mandate_txt = ""
    if mayor:
        remaining = max(0, city["mandate_end"] - int(time.time()))
        mandate_txt = f"\nFin de mandat dans : {_fmt_cooldown(remaining)}"

    await update.message.reply_text(
        f"🏛️ *Mairie de {city['name']}*\n\n"
        f"👑 Maire : {_display_name(mayor) if mayor else '— aucun —'}{mandate_txt}\n"
        f"💰 Caisse municipale : {fmt_money(city['treasury'])}\n"
        f"🧾 Taux d'impôt : {city['tax_rate']}%\n"
        f"👥 Citoyens : {len(residents)}",
        parse_mode="Markdown",
    )


# ── Élections ────────────────────────────────────────────────────────────

async def candidature(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)
    city_name = CITY_NAME

    if player["company_role"] != "PDG":
        await update.message.reply_text(
            "❌ Seuls les chefs d'entreprise (PDG) peuvent se présenter aux élections municipales."
        )
        return

    total_wealth = (player["balance"] or 0) + get_user_total_bank_balance(user.id)
    if total_wealth < MAYOR_CANDIDACY_THRESHOLD:
        await update.message.reply_text(
            f"❌ Il faut posséder au moins {fmt_money(MAYOR_CANDIDACY_THRESHOLD)} "
            f"(solde + banque additionnés) pour candidater.\n"
            f"Ton total actuel : {fmt_money(total_wealth)}"
        )
        return

    election = get_open_election(city_name)
    if election is None:
        election_id = open_election(city_name, int(time.time()))
    else:
        election_id = election["election_id"]

    if not add_candidate(election_id, user.id, int(time.time())):
        await update.message.reply_text("Tu es déjà candidat à cette élection.")
        return

    await update.message.reply_text(
        f"✅ Candidature déposée pour la mairie de *{city_name}* !",
        parse_mode="Markdown",
    )
    text, keyboard = _build_election_view(election_id, city_name)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def retirercandidature(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/retirercandidature — Se retire de l'élection en cours (les votes déjà reçus sont effacés)."""
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)
    city_name = CITY_NAME

    election = get_open_election(city_name)
    if not election:
        await update.message.reply_text("Aucune élection en cours à LIFECITY.")
        return

    if not is_candidate(election["election_id"], user.id):
        await update.message.reply_text("Tu n'es pas candidat à cette élection.")
        return

    remove_candidate(election["election_id"], user.id)
    await update.message.reply_text("✅ Ta candidature a été retirée de l'élection à LIFECITY.")

    text, keyboard = _build_election_view(election["election_id"], city_name)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


def _build_election_view(election_id: int, city_name: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """Construit le texte + les boutons de vote (un par candidat) pour une élection."""
    election = get_election(election_id)
    results = get_vote_counts(election_id)

    remaining = max(0, election["closes_at"] - int(time.time()))
    status_txt = "en cours" if election["status"] == "ouverte" else "clôturée"
    lines = [
        f"🗳️ *Élection municipale — {city_name}*",
        f"Statut : {status_txt} • décision de l'owner dans ~{_fmt_cooldown(remaining)}\n",
    ]

    if not results:
        lines.append("Aucun candidat pour l'instant.")
        return "\n".join(lines), None

    buttons = []
    for r in results:
        name = f"@{r['username']}" if r["username"] else (r["first_name"] or str(r["user_id"]))
        lines.append(f"• {name} — {r['votes']} voix")
        if election["status"] == "ouverte":
            buttons.append([InlineKeyboardButton(f"🗳️ Voter {name}", callback_data=f"votemaire|{election_id}|{r['user_id']}")])

    lines.append("\n*Rappel :* les votes sont indicatifs, c'est l'owner qui tranche.")
    keyboard = InlineKeyboardMarkup(buttons) if buttons else None
    return "\n".join(lines), keyboard


async def candidats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    city_name = CITY_NAME

    election = get_open_election(city_name)
    if not election:
        await update.message.reply_text("Aucune élection en cours à LIFECITY.")
        return

    text, keyboard = _build_election_view(election["election_id"], city_name)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def handle_vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Traite le clic sur un bouton '🗳️ Voter <candidat>'."""
    query = update.callback_query
    _, election_id_str, candidate_id_str = query.data.split("|")
    election_id, candidate_id = int(election_id_str), int(candidate_id_str)

    election = get_election(election_id)
    if not election or election["status"] != "ouverte":
        await query.answer("❌ Cette élection est terminée.", show_alert=True)
        return

    if not is_candidate(election_id, candidate_id):
        await query.answer("❌ Ce candidat n'est plus dans la course.", show_alert=True)
        return

    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)
    cast_vote(election_id, user.id, candidate_id, int(time.time()))

    candidate = get_player_by_id(candidate_id)
    await query.answer(f"✅ Vote enregistré pour {_display_name(candidate)} !")

    text, keyboard = _build_election_view(election_id, election["city"])
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        pass  # message déjà à jour ou trop vieux pour être édité


async def voter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)
    city_name = CITY_NAME

    if not context.args:
        await update.message.reply_text("Utilisation : `/voter <@joueur|id>`", parse_mode="Markdown")
        return

    election = get_open_election(city_name)
    if not election:
        await update.message.reply_text("Aucune élection en cours à LIFECITY.")
        return

    target = find_player_by_identifier(context.args[0])
    if not target:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    if not is_candidate(election["election_id"], target["user_id"]):
        await update.message.reply_text("Ce joueur n'est pas candidat à cette élection.")
        return

    cast_vote(election["election_id"], user.id, target["user_id"], int(time.time()))
    await update.message.reply_text(f"✅ Vote enregistré pour {_display_name(target)} à LIFECITY.")


async def historiquemaire(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    city_name = CITY_NAME

    history = get_mayor_history(city_name, limit=10)
    if not history:
        await update.message.reply_text("Aucun historique pour LIFECITY.")
        return

    labels = {
        "elu": "👑 Élu maire",
        "destitue_detournement": "🚨 Destitué (détournement)",
        "fin_mandat": "📅 Fin de mandat",
        "revoque_owner": "⛔ Révoqué par l'owner",
    }
    lines = ["📜 *Historique — LIFECITY*\n"]
    for h in history:
        p = get_player_by_id(h["user_id"]) if h["user_id"] else None
        label = labels.get(h["event"], h["event"])
        extra = f" ({fmt_money(h['amount'])})" if h["amount"] else ""
        lines.append(f"• {label} — {_display_name(p)}{extra}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Commandes du maire ──────────────────────────────────────────────────

def _require_mayor(user_id: int):
    """Renvoie la ville dont l'utilisateur est maire, ou None."""
    return get_city_by_mayor(user_id)


async def fixerimpot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    city = _require_mayor(user.id)
    if not city:
        await update.message.reply_text("❌ Tu n'es maire d'aucune ville.")
        return

    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text(
            f"Utilisation : `/fixerimpot <taux>` (entre {MAYOR_TAX_MIN} et {MAYOR_TAX_MAX}%)",
            parse_mode="Markdown",
        )
        return

    rate = int(context.args[0])
    if not (MAYOR_TAX_MIN <= rate <= MAYOR_TAX_MAX):
        await update.message.reply_text(f"❌ Le taux doit être entre {MAYOR_TAX_MIN}% et {MAYOR_TAX_MAX}%.")
        return

    set_city_tax_rate(city["name"], rate)
    await update.message.reply_text(f"✅ Taux d'impôt de *{city['name']}* fixé à {rate}%.", parse_mode="Markdown")


async def caissemairie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    city = _require_mayor(user.id)
    if not city:
        await update.message.reply_text("❌ Tu n'es maire d'aucune ville.")
        return
    await update.message.reply_text(
        f"💰 Caisse de *{city['name']}* : {fmt_money(city['treasury'])}\n"
        f"🧾 Taux d'impôt actuel : {city['tax_rate']}%",
        parse_mode="Markdown",
    )


async def retraitmairie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    city = _require_mayor(user.id)
    if not city:
        await update.message.reply_text("❌ Tu n'es maire d'aucune ville.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Utilisation : `/retraitmairie <montant> [raison]`", parse_mode="Markdown")
        return

    amount = int(context.args[0])
    reason = " ".join(context.args[1:]) or "Non précisée"

    if amount <= 0:
        await update.message.reply_text("❌ Montant invalide.")
        return

    treasury = city["treasury"]
    if amount > treasury:
        await update.message.reply_text("❌ La caisse municipale ne contient pas assez d'argent.")
        return

    # Seuil de détournement : un retrait unique > 30% de la caisse = destitution automatique.
    is_embezzlement = treasury > 0 and (amount / treasury) > MAYOR_EMBEZZLEMENT_RATIO

    if not withdraw_city_treasury(city["name"], amount):
        await update.message.reply_text("❌ Retrait impossible (solde insuffisant).")
        return

    add_balance(user.id, amount)
    now = int(time.time())

    if is_embezzlement:
        remove_mayor(city["name"], "destitue_detournement", amount, now)
        open_election(city["name"], now)
        await update.message.reply_text(
            f"🚨 Retrait de {fmt_money(amount)} ({amount / treasury:.0%} de la caisse) : "
            f"seuil de détournement dépassé !\n"
            f"Tu es *automatiquement destitué* de la mairie de {city['name']}. "
            f"Une nouvelle élection vient de s'ouvrir.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"✅ Retrait de {fmt_money(amount)} effectué depuis la caisse de {city['name']}.\n"
            f"Raison : {reason}"
        )


# NOTE : les commandes réservées à l'owner (/ouvrirelection, /trancherelection,
# /revoquermaire, /addcaisseville, /removecaisseville, /villescaisses) vivent
# dans handlers/admin.py avec le décorateur @owner_only, comme le reste du
# panel owner. Elles réutilisent les fonctions ci-dessus (open_election,
# close_election, install_mayor, remove_mayor, etc.) importées depuis db.py.


# ── Tâches planifiées ──────────────────────────────────────────────────

async def run_weekly_city_taxes(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Chaque semaine : prélève, pour chaque citoyen, le taux d'impôt de sa ville
    sur le salaire perçu dans la semaine, verse le tout à la caisse municipale,
    puis reverse 10% du montant collecté à la caisse de l'État.
    """
    for city in get_all_cities():
        if city["tax_rate"] <= 0:
            continue
        residents = get_city_residents(city["name"])
        collected = 0
        for r in residents:
            salary = r["salary"] or 0
            if salary <= 0:
                continue
            tax = (salary * city["tax_rate"]) // 100
            if tax <= 0:
                continue
            tax = min(tax, max(0, r["balance"] or 0))  # ne jamais mettre un joueur en négatif
            if tax <= 0:
                continue
            add_balance(r["user_id"], -tax)
            collected += tax

        if collected > 0:
            state_cut = int(collected * MAYOR_STATE_CUT)
            city_share = collected - state_cut
            add_city_treasury(city["name"], city_share)
            add_state_treasury(state_cut)


async def run_daily_mandate_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Vérifie chaque jour les mandats arrivés à échéance (2 semaines) et relance une élection."""
    now = int(time.time())
    for city in get_mayors_with_expired_mandate(now):
        remove_mayor(city["name"], "fin_mandat", 0, now)
        open_election(city["name"], now)
