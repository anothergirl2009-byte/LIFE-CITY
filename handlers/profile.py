"""
LifeCity Bot - Profil joueur
/me — Affiche le profil complet du joueur
/setpic — Change la photo de profil (envoie une photo avec /setpic en
          légende, ou réponds à une photo avec /setpic)
/setnationalite — Choisit sa nationalité parmi tous les pays du monde
/setsexe — Choisit son genre
/setmariage — Choisit son mode relationnel (Monogame / Polygame)
"""

import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from db import (
    get_or_create_player, update_player, get_company_by_id, get_spouse_id, get_conn,
    get_state_treasury, add_state_treasury, add_balance,
)
from utils import fmt_money, fmt_big_money, progress_bar, karma_title
from config import NATIONALITIES, GENDERS, RELATIONSHIP_MODES
from metiers import METIER_CATEGORIES, METIER_COOLDOWN_SECONDS, get_metier_label

# Part de la trésorerie de l'État reversée au joueur à chaque choix de métier
METIER_STATE_BONUS_RATE = 0.02

try:
    from handlers.education import SECTORS as EDU_SECTORS, TIERS as EDU_TIERS
except ImportError:
    from education import SECTORS as EDU_SECTORS, TIERS as EDU_TIERS


def _md_escape(text: str) -> str:
    """Échappe les caractères spéciaux Markdown (_, *, `, [) pour éviter
    qu'un texte libre (pseudo, nom de famille...) ne casse le parsing."""
    if not text:
        return text
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def _count_family_members(family_name: str) -> int:
    """Compte le nombre de membres actifs (non bannis) d'une famille."""
    if not family_name:
        return 0
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS nb FROM players WHERE banned = 0 AND family_name = ?",
            (family_name,)
        ).fetchone()
    return row["nb"] if row else 0


def _famille_titre(nb_membres: int) -> str:
    """
    Retourne le titre de la famille en fonction de son nombre de membres.
    Seuils : 25+ Royale, 15+ Impériale, 10+ Noble, 5+ Honorée.
    """
    if nb_membres >= 25:
        return "🏰 Famille Royale"
    elif nb_membres >= 15:
        return "⚜️ Famille Impériale"
    elif nb_membres >= 10:
        return "🛡️ Famille Noble"
    elif nb_membres >= 5:
        return "🎖️ Famille Honorée"
    return ""


def _fortune_badge(fortune: int) -> tuple[str, str]:
    """
    Retourne (emoji, titre) selon la fortune du joueur.
    Progression carré → losange → rond, avec une couleur différente à chaque palier.
    """
    if fortune >= 500_000_000:
        return "⚫", "Empereur"
    elif fortune >= 250_000_000:
        return "🔴", "Magnat"
    elif fortune >= 100_000_000:
        return "🔷", "Riche"
    elif fortune >= 50_000_000:
        return "🔶", "Fortuné"
    elif fortune >= 25_000_000:
        return "🟪", "Prospère"
    elif fortune >= 10_000_000:
        return "🟨", "Confortable"
    elif fortune >= 5_000_000:
        return "🟦", "Aisé"
    elif fortune >= 1_000_000:
        return "🟩", "Modeste"
    return "⬜", "Débutant"


def _is_fortune_leader(first_name: str, fortune: int) -> bool:
    """
    Vérifie si le joueur est le n°1 de la richlist (fortune la plus haute).
    Tie-break identique à /richlist : fortune DESC, first_name ASC.
    """
    if fortune <= 0:
        return False
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT first_name, (balance + bank_balance) AS fortune
            FROM players
            WHERE banned = 0
            ORDER BY fortune DESC, first_name COLLATE NOCASE ASC
            LIMIT 1
            """
        ).fetchone()
    return bool(row) and row["fortune"] == fortune and row["first_name"] == first_name


def _get_diplomas_summary(user_id: int) -> str:
    """
    Lit la table réelle `user_diplomas` (remplie par education.py) et affiche,
    pour chaque secteur où le joueur a validé au moins un diplôme, le plus haut
    palier obtenu ainsi que la progression (Bac → Licence → Master → MBA).
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT sector, tier FROM user_diplomas WHERE user_id = ? AND status = 'passed'",
            (user_id,)
        ).fetchall()

    if not rows:
        return "╰┈➤  Aucun diplôme"

    tier_order = [t["key"] for t in EDU_TIERS]
    tier_emoji = {t["key"]: t["emoji"] for t in EDU_TIERS}
    tier_name = {t["key"]: t["name"] for t in EDU_TIERS}

    by_sector: dict[str, set[str]] = {}
    for r in rows:
        by_sector.setdefault(r["sector"], set()).add(r["tier"])

    lignes = []
    for sector, tiers in by_sector.items():
        info = EDU_SECTORS.get(sector, {"label": sector, "emoji": "🎓"})
        ordered = [t for t in tier_order if t in tiers]
        badges = "  ".join(f"{tier_emoji.get(t, '🎓')} {tier_name.get(t, t).upper()}" for t in ordered)
        lignes.append(f"╰┈➤  {info['emoji']} {info['label']} : {badges}")

    return "\n  ".join(lignes)


async def me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    family_name = player["family_name"] or "Aucune famille"
    nationality = player["nationality"] or "Non définie  (/setnationalite)"
    gender = player["gender"] or "Non défini  (/setsexe)"
    relationship_mode = player["relationship_mode"] or "Non défini  (/setmariage)"

    fortune = player["balance"] + player["bank_balance"]
    karma = player["karma"]
    karma_pct = max(0, min(100, karma))

    created_at = player["created_at"]
    import datetime
    join_date = datetime.datetime.utcfromtimestamp(created_at).strftime("%d/%m/%Y") if created_at else "Inconnue"

    badge_emoji, badge_titre = _fortune_badge(fortune)
    if _is_fortune_leader(player["first_name"], fortune):
        badge_emoji, badge_titre = "👑", "Empereur Suprême"

    lines = []
    lines.append(f"「 {badge_emoji} 」*{_md_escape(player['first_name'])}*")
    lines.append(f"✦ 💰 {badge_titre}  ┊  🏅 {gender}")
    lines.append(f"✦ 📅 Depuis le {join_date}")
    lines.append("")
    lines.append("◈━━━━━━━━━━━━━━━━━━━━━━━━◈")
    lines.append("")
    lines.append("  💰 FORTUNE")
    lines.append(f"  ╰┈➤  {fmt_money(fortune)}")
    lines.append("")
    lines.append(f"  ❓ GENRE  ┊  ❤️ {relationship_mode}")
    lines.append(f"  ╰┈➤  {gender}")
    lines.append("")
    lines.append("  🌍 NATIONALITÉ")
    lines.append(f"  ╰┈➤  {nationality}")
    lines.append("")
    lines.append("  🏠 FAMILLE")
    if player["family_name"]:
        nb_membres = _count_family_members(player["family_name"])
        titre = _famille_titre(nb_membres)
        lines.append(f"  ╰┈➤  {_md_escape(family_name)}  ({nb_membres} membres)")
        if titre:
            lines.append(f"  ╰┈➤  {titre}")
    else:
        lines.append(f"  ╰┈➤  {family_name}")
    lines.append("")
    lines.append("  🎓 DIPLÔMES")
    lines.append(f"  {_get_diplomas_summary(user.id)}")
    lines.append("")

    lines.append("  🏢 ENTREPRISE(S)")
    if player["company_id"]:
        company = get_company_by_id(player["company_id"])
        if company:
            role = player["company_role"] or "Employé"
            # Échappe les underscores pour ne pas casser le parsing Markdown
            # (un nombre impair d'underscores fait planter tout le message)
            company_tag = company["name"].upper().replace(" ", "_").replace("_", "\\_")
            lines.append(f"  ╰┈➤  🏦 {role} chez {company_tag} 📈")
        else:
            lines.append("  ╰┈➤  Sans emploi")
    else:
        lines.append("  ╰┈➤  Sans emploi")
    lines.append("")
    lines.append("  🧑‍💼 MÉTIER")
    metier_label = get_metier_label(player["metier"])
    lines.append(f"  ╰┈➤  {metier_label if metier_label else 'Non défini  (/metier)'}")
    lines.append("")
    lines.append("◈━━━━━━━━━━━━━━━━━━━━━━━━◈")
    lines.append("")
    lines.append(f"  🌟 KARMA  {'+' if karma >= 0 else ''}{karma}  ┊  {karma_title(karma)}")
    lines.append(f"  ╰┈➤ {progress_bar(karma_pct, 100, 10)}")

    text = "\n".join(lines)

    if player["profile_pic"]:
        await update.message.reply_photo(
            photo=player["profile_pic"], caption=text, parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(text, parse_mode="Markdown")


async def setpic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)

    photo = None
    if update.message.photo:
        photo = update.message.photo[-1]  # la plus grande résolution
    elif update.message.reply_to_message and update.message.reply_to_message.photo:
        photo = update.message.reply_to_message.photo[-1]

    if photo is None:
        await update.message.reply_text(
            "Utilisation : envoie une photo avec /setpic en légende, "
            "ou réponds à une photo avec /setpic."
        )
        return

    update_player(user.id, profile_pic=photo.file_id)
    await update.message.reply_text("✅ Ta photo de profil a été mise à jour !")


async def setnationalite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)

    if not context.args:
        await update.message.reply_text(
            "Utilisation : /setnationalite nom_du_pays\n"
            "Exemple : /setnationalite Cameroun\n\n"
            f"Pays disponibles ({len(NATIONALITIES)}) : utilise /help pour voir la liste complète."
        )
        return

    query = " ".join(context.args).strip().lower()
    match = None
    for nat in NATIONALITIES:
        # nat est du style "🇨🇲 Cameroun" -> on compare sur le nom sans le drapeau
        name = nat.split(" ", 1)[1] if " " in nat else nat
        if name.lower() == query or query in name.lower():
            match = nat
            break

    if match is None:
        await update.message.reply_text(
            "❌ Pays introuvable. Vérifie l'orthographe (ex : /setnationalite Sénégal)."
        )
        return

    update_player(user.id, nationality=match)
    await update.message.reply_text(f"✅ Nationalité mise à jour : {match}")


async def setsexe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)

    if not context.args:
        options = "\n".join(f"• {g}" for g in GENDERS)
        await update.message.reply_text(
            f"Utilisation : /setsexe genre\n\nOptions disponibles :\n{options}"
        )
        return

    query = " ".join(context.args).strip().lower()
    match = None
    for g in GENDERS:
        name = g.split(" ", 1)[1] if " " in g else g
        if name.lower() == query or query in name.lower():
            match = g
            break

    if match is None:
        await update.message.reply_text("❌ Genre invalide. Utilise /setsexe pour voir les options.")
        return

    update_player(user.id, gender=match)
    await update.message.reply_text(f"✅ Genre mis à jour : {match}")


async def setmariage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)

    if not context.args:
        options = "\n".join(f"• {m}" for m in RELATIONSHIP_MODES)
        await update.message.reply_text(
            f"Utilisation : /setmariage mode\n\nOptions disponibles :\n{options}"
        )
        return

    query = " ".join(context.args).strip().lower()
    match = None
    for m in RELATIONSHIP_MODES:
        name = m.split(" ", 1)[1] if " " in m else m
        if name.lower() == query or query in name.lower():
            match = m
            break

    if match is None:
        await update.message.reply_text("❌ Mode invalide. Utilise /setmariage pour voir les options.")
        return

    update_player(user.id, relationship_mode=match)
    await update.message.reply_text(f"✅ Mode relationnel mis à jour : {match}")


def _metier_categories_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for cat_key, (label, emoji, _metiers) in METIER_CATEGORIES.items():
        rows.append([InlineKeyboardButton(f"{emoji} {label}", callback_data=f"metier|cat|{cat_key}")])
    return InlineKeyboardMarkup(rows)


def _metier_choices_keyboard(cat_key: str) -> InlineKeyboardMarkup:
    _label, _emoji, metiers = METIER_CATEGORIES[cat_key]
    rows = [
        [InlineKeyboardButton(label, callback_data=f"metier|set|{key}")]
        for key, label in metiers
    ]
    rows.append([InlineKeyboardButton("◀️ Retour aux catégories", callback_data="metier|back")])
    return InlineKeyboardMarkup(rows)


async def metier(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)
    await update.message.reply_text(
        "🧑‍💼 Choisis une catégorie de métier :",
        reply_markup=_metier_categories_keyboard(),
    )


async def metier_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = query.from_user
    await query.answer()

    parts = query.data.split("|")
    action = parts[1] if len(parts) > 1 else ""

    if action == "back":
        await query.edit_message_text(
            "🧑‍💼 Choisis une catégorie de métier :",
            reply_markup=_metier_categories_keyboard(),
        )
        return

    if action == "cat":
        cat_key = parts[2]
        if cat_key not in METIER_CATEGORIES:
            await query.answer("❌ Catégorie introuvable.", show_alert=True)
            return
        label, emoji, _ = METIER_CATEGORIES[cat_key]
        await query.edit_message_text(
            f"{emoji} {label} — choisis ton métier :",
            reply_markup=_metier_choices_keyboard(cat_key),
        )
        return

    if action == "set":
        metier_key = parts[2]
        player = get_or_create_player(user.id, user.username, user.first_name)

        now = int(time.time())
        last_change = player["metier_last_change"] or 0
        remaining = METIER_COOLDOWN_SECONDS - (now - last_change)
        if last_change and remaining > 0:
            jours = remaining // 86400
            heures = (remaining % 86400) // 3600
            await query.answer(
                f"⏳ Tu dois attendre encore {jours}j {heures}h avant de changer de métier.",
                show_alert=True,
            )
            return

        label = get_metier_label(metier_key)
        if not label:
            await query.answer("❌ Métier introuvable.", show_alert=True)
            return

        update_player(user.id, metier=metier_key, metier_last_change=now)

        # Bonus à l'embauche : 2% de la trésorerie de l'État reversés au joueur,
        # prélevés directement sur cette même trésorerie de l'État.
        bonus = int(get_state_treasury() * METIER_STATE_BONUS_RATE)
        bonus_line = ""
        if bonus > 0:
            add_state_treasury(-bonus)
            add_balance(user.id, bonus)
            bonus_line = f"\n\n🏛️ Prime d'État (2%) : +{fmt_money(bonus)}"

        await query.edit_message_text(f"✅ Ton métier est maintenant : {label}{bonus_line}")
