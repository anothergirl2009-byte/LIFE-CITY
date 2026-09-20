"""
LifeCity Bot - Entreprises (partie 1 : gestion de base)
/creerboite — Créer son entreprise (50M€)
/emplacementboite — Définir l'emplacement du siège social (gratuit)
/dissoudreboite — Dissoudre son entreprise
/listeboites — Liste des entreprises par secteur
/infoboite — Détails d'une entreprise
/monentreprise — Sa fiche employé
/employes — Liste des employés
/postuler — Postuler dans une entreprise
/rejoindre — Accepter une invitation
/demissionner — Quitter son entreprise
/candidatures — Voir les candidatures (PDG/Dir.)
/accepter / /refuser — Traiter une candidature
/recruter — Inviter quelqu'un
/negociercontrat — Proposer poste + salaire à un joueur
/negociercontratmodifier — Modifier une offre en attente
/accepternegociation / /refusenegociation — Traiter une offre
/nommer — Promouvoir un employé
/licencier — Licencier un employé
"""

import time
import unicodedata

from types import SimpleNamespace

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from config import COMPANY_CREATION_COST, COMPANY_RELOCATION_COST, COMPANY_SECTORS, COMPANY_LOCATIONS, ALL_CITIES, ALL_COUNTRIES
from db import (
    get_or_create_player, get_conn, update_player, add_balance,
    create_company, get_company_by_name, get_company_by_id,
    get_company_employees, delete_company, add_company_log,
    create_application, get_pending_applications,
    update_application_status, get_player_by_name_or_id,
    update_company_description, get_company_shares, get_application,
    get_active_groups, create_recruitment_ad, get_recruitment_ad,
    delete_recruitment_ad, set_company_last_recruitment_ad, get_player_by_id,
    get_owned_company, withdraw_from_treasury, get_user_shares, set_user_shares,
    get_company_buildings, get_company_building, add_company_building, set_building_suspended,
)
from utils import fmt_money

try:
    from handlers.education import SECTORS as EDU_SECTORS, TIERS as EDU_TIERS
except ImportError:
    from education import SECTORS as EDU_SECTORS, TIERS as EDU_TIERS


def _ensure_education_tables(conn):
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
            PRIMARY KEY (user_id, sector, tier)
        )"""
    )


def _get_player_domain(user_id: int) -> str | None:
    """Renvoie le secteur (clé education, ex: 'finance') choisi par le joueur via /diplome."""
    with get_conn() as conn:
        _ensure_education_tables(conn)
        row = conn.execute(
            "SELECT sector FROM user_domain WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row["sector"] if row else None


def _has_mba(user_id: int, sector: str) -> bool:
    """Vérifie que le joueur a bien validé le MBA dans le secteur donné."""
    with get_conn() as conn:
        _ensure_education_tables(conn)
        row = conn.execute(
            "SELECT 1 FROM user_diplomas WHERE user_id = ? AND sector = ? AND tier = 'mba' AND status = 'passed'",
            (user_id, sector)
        ).fetchone()
    return row is not None


def _get_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    # Pas de reply, pas de @mention : accepte un ID numérique tapé
    # directement en premier argument (ex : /recruter 123456789), pas
    # besoin de taguer la personne.
    if context.args and context.args[0].isdigit():
        row = get_player_by_name_or_id(context.args[0])
        if row is not None:
            return SimpleNamespace(
                id=row["user_id"],
                username=row["username"],
                first_name=row["first_name"],
            )
    return None


def _get_passed_tiers(user_id: int, sector_label: str | None = None) -> set[str]:
    """
    Renvoie les paliers de diplôme validés par le joueur, tous secteurs confondus.
    Une candidature n'est plus liée au secteur de l'entreprise : avoir le
    Bac (ou plus) dans N'IMPORTE QUEL secteur suffit pour postuler/être recruté.
    Le paramètre `sector_label` est conservé pour compatibilité des appels
    existants mais n'est plus utilisé pour filtrer.
    """
    with get_conn() as conn:
        _ensure_education_tables(conn)
        rows = conn.execute(
            "SELECT tier FROM user_diplomas WHERE user_id = ? AND status = 'passed'",
            (user_id,)
        ).fetchall()
    return {r["tier"] for r in rows}


def _grade_from_diploma(user_id: int, sector_label: str | None = None) -> str:
    """
    Détermine le grade attribué à l'embauche selon le meilleur diplôme validé
    par le joueur, tous secteurs confondus :
      MBA -> Directeur | Master -> Manager | Licence/Bac -> Employé | Rien -> Employé
    """
    tiers = _get_passed_tiers(user_id)
    if "mba" in tiers:
        return "Directeur"
    if "master" in tiers:
        return "Manager"
    return "Employé"


def _diplomas_display(user_id: int, sector_label: str | None = None) -> str:
    """Affiche la meilleure progression de diplôme d'un joueur, tous secteurs confondus."""
    tiers = _get_passed_tiers(user_id)
    if not tiers:
        return "Aucun diplôme"
    tier_order = [t["key"] for t in EDU_TIERS]
    tier_emoji = {t["key"]: t["emoji"] for t in EDU_TIERS}
    tier_name = {t["key"]: t["name"] for t in EDU_TIERS}
    ordered = [t for t in tier_order if t in tiers]
    return "  ".join(f"{tier_emoji.get(t, '🎓')} {tier_name.get(t, t).upper()}" for t in ordered)


# ============================================================
# ANNUAIRE DES ENTREPRISES (style + pagination)
# ============================================================

SECTOR_EMOJI = {
    "Technologie": "💻",
    "Finance": "📈",
    "Immobilier": "🏗️",
    "Restauration": "🍽️",
    "Industrie": "🏭",
    "Commerce": "🛒",
    "Transport": "🚚",
    "Médias": "📰",
    "Santé": "🏥",
    "Énergie": "⚡",
}

LISTEBOITES_PAGE_SIZE = 8


def _fmt_treasury(n: int) -> str:
    n = n or 0
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B $"
    elif n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M $"
    return fmt_money(n)


def _company_badge(treasury: int) -> str:
    if treasury >= 10_000_000_000_000:
        return "👑"
    elif treasury >= 1_000_000_000_000:
        return "🏆"
    elif treasury >= 100_000_000_000:
        return "🏢"
    return "🏬"


BUILDING_SLOTS = [
    {"slot": "salle_reunion",    "level": 1, "cost": 500_000,           "maintenance": 2_000,         "effect": "nego_delay",          "value": 0.10},
    {"slot": "entrepot",         "level": 1, "cost": 1_000_000,         "maintenance": 5_000,         "effect": "treasury_cap",        "value": 0.15},
    {"slot": "siege_social",     "level": 2, "cost": 10_000_000,        "maintenance": 20_000,        "effect": "reputation",          "value": 0.10},
    {"slot": "datacenter",       "level": 3, "cost": 500_000_000,       "maintenance": 1_000_000,     "effect": "contract_revenue",    "value": 0.10},
    {"slot": "usine",            "level": 3, "cost": 500_000_000,       "maintenance": 1_000_000,     "effect": "daily_revenue",       "value": 0.10},
    {"slot": "agence_bancaire",  "level": 4, "cost": 50_000_000_000,    "maintenance": 50_000_000,    "effect": "interco_loans",       "value": None},
    {"slot": "campus_rd",        "level": 5, "cost": 5_000_000_000_000, "maintenance": 2_000_000_000, "effect": "exclusive_contracts", "value": None},
    {"slot": "tour_controle",    "level": 5, "cost": 5_000_000_000_000, "maintenance": 2_000_000_000, "effect": "leaderboard",         "value": None},
]
BUILDING_SLOTS_BY_KEY = {b["slot"]: b for b in BUILDING_SLOTS}

SECTOR_BUILDING_NAMES = {
    'Technologie': {
        'salle_reunion': ('Salle de Brainstorming', '🧠'),
        'entrepot': ('Serveur Cloud', '☁️'),
        'siege_social': ('Campus Tech', '🏛️'),
        'datacenter': ('Datacenter IA', '🖥️'),
        'usine': ('Usine de Composants', '🏭'),
        'agence_bancaire': ("Fonds d'Investissement Tech", '🏦'),
        'campus_rd': ('Laboratoire R&D', '🔬'),
        'tour_controle': ('Antenne Satellite', '🛰️'),
    },
    'Finance': {
        'salle_reunion': ('Salle des Marchés', '📊'),
        'entrepot': ('Coffre-Fort', '🗄️'),
        'siege_social': ('Siège Bancaire', '🏛️'),
        'datacenter': ('Datacenter Trading', '🖥️'),
        'usine': ('Centre de Traitement', '🏭'),
        'agence_bancaire': ('Agence Bancaire', '🏦'),
        'campus_rd': ('Campus Actuariat', '🔬'),
        'tour_controle': ('Tour de la Bourse', '🗼'),
    },
    'Immobilier': {
        'salle_reunion': ('Salle des Ventes', '🏘️'),
        'entrepot': ('Entrepôt de Matériaux', '📦'),
        'siege_social': ('Siège Immobilier', '🏛️'),
        'datacenter': ('Datacenter Cadastral', '🖥️'),
        'usine': ('Usine de Préfabriqués', '🏭'),
        'agence_bancaire': ('Agence de Financement', '🏦'),
        'campus_rd': ('Campus Architecture', '🔬'),
        'tour_controle': ('Tour Panoramique', '🗼'),
    },
    'Restauration': {
        'salle_reunion': ('Salle de Dégustation', '🍷'),
        'entrepot': ('Entrepôt Frigorifique', '🧊'),
        'siege_social': ('Siège Gastronomique', '🏛️'),
        'datacenter': ('Datacenter Commandes', '🖥️'),
        'usine': ('Cuisine Centrale', '🏭'),
        'agence_bancaire': ('Agence de Franchise', '🏦'),
        'campus_rd': ('Laboratoire Culinaire', '🔬'),
        'tour_controle': ('Enseigne Lumineuse', '🗼'),
    },
    'Industrie': {
        'salle_reunion': ('Salle des Ingénieurs', '🧑\u200d🔧'),
        'entrepot': ('Entrepôt Logistique', '📦'),
        'siege_social': ('Siège Industriel', '🏛️'),
        'datacenter': ('Datacenter Production', '🖥️'),
        'usine': ('Usine Automatisée', '🏭'),
        'agence_bancaire': ('Agence de Financement Industriel', '🏦'),
        'campus_rd': ('Campus R&D Matériaux', '🔬'),
        'tour_controle': ('Tour de Contrôle Qualité', '🗼'),
    },
    'Commerce': {
        'salle_reunion': ('Salle de Négociation', '🤝'),
        'entrepot': ('Entrepôt Central', '📦'),
        'siege_social': ('Siège Commercial', '🏛️'),
        'datacenter': ('Datacenter Ventes', '🖥️'),
        'usine': ("Usine d'Emballage", '🏭'),
        'agence_bancaire': ('Agence de Crédit Commercial', '🏦'),
        'campus_rd': ('Campus Marketing', '🔬'),
        'tour_controle': ('Enseigne Nationale', '🗼'),
    },
    'Transport': {
        'salle_reunion': ('Salle de Dispatch', '🚦'),
        'entrepot': ('Entrepôt Logistique', '📦'),
        'siege_social': ('Siège Transport', '🏛️'),
        'datacenter': ('Datacenter Flotte', '🖥️'),
        'usine': ("Usine d'Assemblage", '🏭'),
        'agence_bancaire': ('Agence de Leasing', '🏦'),
        'campus_rd': ('Campus Ingénierie', '🔬'),
        'tour_controle': ('Tour de Contrôle Aérien', '🗼'),
    },
    'Médias': {
        'salle_reunion': ('Salle de Rédaction', '📰'),
        'entrepot': ('Serveur de Stockage', '☁️'),
        'siege_social': ('Siège Éditorial', '🏛️'),
        'datacenter': ('Datacenter Diffusion', '🖥️'),
        'usine': ('Studio de Production', '🏭'),
        'agence_bancaire': ('Agence de Financement Média', '🏦'),
        'campus_rd': ('Campus Créatif', '🔬'),
        'tour_controle': ('Antenne Émettrice', '📡'),
    },
    'Santé': {
        'salle_reunion': ('Salle de Consultation', '🩺'),
        'entrepot': ('Entrepôt Pharmaceutique', '💊'),
        'siege_social': ('Siège Hospitalier', '🏛️'),
        'datacenter': ('Datacenter Dossiers Médicaux', '🖥️'),
        'usine': ('Usine Pharmaceutique', '🏭'),
        'agence_bancaire': ('Agence de Financement Santé', '🏦'),
        'campus_rd': ('Campus Recherche Médicale', '🔬'),
        'tour_controle': ("Tour d'Observation Sanitaire", '🗼'),
    },
    'Énergie': {
        'salle_reunion': ('Salle de Contrôle', '🎛️'),
        'entrepot': ('Entrepôt de Stockage', '🔋'),
        'siege_social': ('Siège Énergétique', '🏛️'),
        'datacenter': ('Datacenter Réseau Intelligent', '🖥️'),
        'usine': ('Centrale de Production', '🏭'),
        'agence_bancaire': ('Agence de Financement Énergétique', '🏦'),
        'campus_rd': ('Campus R&D Énergies', '🔬'),
        'tour_controle': ('Tour de Transmission', '🗼'),
    },
}


def _company_numeric_level(treasury: int) -> int:
    """Niveau numérique 1-5 pour le déblocage des bâtiments, basé sur la trésorerie."""
    if treasury >= 100_000_000_000_000:
        return 5
    elif treasury >= 10_000_000_000_000:
        return 4
    elif treasury >= 1_000_000_000_000:
        return 3
    elif treasury >= 100_000_000_000:
        return 2
    return 1


def _building_display_name(sector: str, slot: str) -> tuple[str, str]:
    """Renvoie (nom, emoji) du bâtiment pour ce secteur, avec repli générique."""
    per_sector = SECTOR_BUILDING_NAMES.get(sector)
    if per_sector and slot in per_sector:
        return per_sector[slot]
    generic = {
        "salle_reunion": ("Salle de Réunion", "🪑"),
        "entrepot": ("Entrepôt", "📦"),
        "siege_social": ("Siège Social", "🏛️"),
        "datacenter": ("Datacenter", "🖥️"),
        "usine": ("Usine", "🏭"),
        "agence_bancaire": ("Agence Bancaire", "🏦"),
        "campus_rd": ("Campus R&D", "🔬"),
        "tour_controle": ("Tour de Contrôle", "🗼"),
    }
    return generic.get(slot, (slot, "🏢"))


def get_company_treasury_cap(company_id: int, base_cap: int = 200_000_000_000_000) -> int:
    """Plafond de trésorerie autorisé, augmenté par l'Entrepôt (+15%) si possédé et actif."""
    b = get_company_building(company_id, "entrepot")
    if b is not None and not b["suspended"]:
        return round(base_cap * 1.15)
    return base_cap


def get_company_reputation_bonus(company_id: int) -> float:
    """Bonus de réputation (0.10 = +10%) si le Siège Social est possédé et actif."""
    b = get_company_building(company_id, "siege_social")
    if b is not None and not b["suspended"]:
        return 0.10
    return 0.0


def get_company_contract_revenue_bonus(company_id: int) -> float:
    """Bonus sur les revenus de contrats (0.10 = +10%) si le Datacenter est possédé et actif."""
    b = get_company_building(company_id, "datacenter")
    if b is not None and not b["suspended"]:
        return 0.10
    return 0.0


def get_company_daily_revenue_bonus(company_id: int) -> float:
    """Bonus sur les revenus journaliers (0.10 = +10%) si l'Usine est possédée et active."""
    b = get_company_building(company_id, "usine")
    if b is not None and not b["suspended"]:
        return 0.10
    return 0.0


def _company_level(treasury: int) -> str:
    """Niveau affiché (Startup/Entreprise/Groupe/Holding), sur les mêmes seuils que le badge."""
    if treasury >= 10_000_000_000_000:
        return "Holding"
    elif treasury >= 1_000_000_000_000:
        return "Groupe"
    elif treasury >= 100_000_000_000:
        return "Entreprise"
    return "Startup"


def _company_detail_text(company) -> str:
    """Texte détaillé d'une entreprise, réutilisé par /infoboite et la navigation 1 à 1."""
    employees = get_company_employees(company["company_id"])
    with get_conn() as conn:
        ceo = conn.execute(
            "SELECT * FROM players WHERE user_id = ?", (company["ceo_id"],)
        ).fetchone()
        ceo_shares_row = conn.execute(
            "SELECT shares FROM company_shares WHERE company_id = ? AND user_id = ?",
            (company["company_id"], company["ceo_id"]),
        ).fetchone()

    emoji = SECTOR_EMOJI.get(company["sector"], "🏢")
    treasury = company["treasury"]
    badge = _company_badge(treasury)
    level = _company_level(treasury)
    description = company["description"] if company["description"] else "Aucune description."
    max_employees = company["max_employees"] or 200
    founded = time.strftime("%d/%m/%Y", time.localtime(company["created_at"]))
    reputation = min(5.0, (company["reputation"] if company["reputation"] is not None else 3.0) * (1 + get_company_reputation_bonus(company["company_id"])))
    ceo_shares = ceo_shares_row["shares"] if ceo_shares_row else 0

    # Revenus/jour = somme des salaires que le PDG doit verser (sortant, via /versersalaires)
    daily_payroll = sum((e["salary"] or 0) for e in employees)

    return (
        f"╔══════════════════════════════╗\n"
        f"║  {emoji} {company['name']}\n"
        f"╚══════════════════════════════╝\n\n"
        f"📋 Secteur : {company['sector']}\n"
        f"{badge} Niveau : {level}\n"
        f"📝 {description}\n\n"
        f"◈━━━━━━━━━━━━━━━━━━━━━━━━◈\n\n"
        f"💰 Valeur : {_fmt_treasury(treasury)}\n"
        f"🏦 Trésorerie : {_fmt_treasury(treasury)}\n"
        f"📈 Revenus/jour : {_fmt_treasury(daily_payroll)} (salaires à verser via /versersalaires)\n"
        f"⭐ Réputation : {reputation:.1f}/5\n"
        f"💎 PDG : {ceo['first_name'] if ceo else 'Inconnu'}\n"
        f"👥 Employés : {len(employees)}/{max_employees}\n"
        f"🎂 Fondée le : {founded}\n\n"
        f"◈━━━━━━━━━━━━━━━━━━━━━━━━◈\n"
        f"📦 Parts PDG : {ceo_shares}/100 · 💡 /parts {company['name']} pour le détail"
    )


def _get_sorted_companies(sector_code: str) -> tuple[list, str | None]:
    from db import get_all_companies, get_companies_by_sector

    if sector_code == "A":
        companies = get_all_companies()
        sector_label = None
    else:
        idx = int(sector_code)
        sector_label = COMPANY_SECTORS[idx]
        companies = get_companies_by_sector(sector_label)

    return sorted(companies, key=lambda c: c["treasury"], reverse=True), sector_label


def _render_listeboites(sector_code: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    companies, sector_label = _get_sorted_companies(sector_code)
    total = len(companies)
    total_pages = max(1, -(-total // LISTEBOITES_PAGE_SIZE))
    page = max(1, min(page, total_pages))
    start = (page - 1) * LISTEBOITES_PAGE_SIZE
    page_companies = companies[start:start + LISTEBOITES_PAGE_SIZE]

    lines = [
        "🏙️ *ANNUAIRE DES ENTREPRISES*",
        f"_Page {page}/{total_pages} · {total} entreprises actives_",
        f"_Secteur : {sector_label or 'Tous'}_",
        "",
    ]

    if not page_companies:
        lines.append("Aucune entreprise dans ce secteur.")
    else:
        for i, c in enumerate(page_companies, start=start + 1):
            emoji = SECTOR_EMOJI.get(c["sector"], "🏢")
            badge = _company_badge(c["treasury"])
            employees = get_company_employees(c["company_id"])
            # Nom affiché en police normale (gras Markdown standard *texte*,
            # pas en police unicode "mathématique" fantaisie) pour rester
            # lisible et cohérent avec le style du bilan (/infoboite).
            lines.append(f"{i}. {emoji} *{c['name']}*")
            lines.append(f"   {badge} · 💰 {_fmt_treasury(c['treasury'])} · 👥 {len(employees)} employés")

    lines.append("")
    lines.append("💡 /infoboite [nom] · /postuler [nom]")

    keyboard: list[list[InlineKeyboardButton]] = []

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("◀ Précédent", callback_data=f"boites|list|{sector_code}|{page - 1}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Suivant ▶", callback_data=f"boites|list|{sector_code}|{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    if page_companies:
        keyboard.append([
            InlineKeyboardButton("🔍 Voir en détail (1 à 1)", callback_data=f"boites|detail|{sector_code}|{start}")
        ])

    sector_buttons = [
        InlineKeyboardButton(SECTOR_EMOJI.get(sec, "🏢"), callback_data=f"boites|list|{idx}|1")
        for idx, sec in enumerate(COMPANY_SECTORS)
    ]
    for i in range(0, len(sector_buttons), 5):
        keyboard.append(sector_buttons[i:i + 5])

    keyboard.append([InlineKeyboardButton("🔄 Tous les secteurs", callback_data="boites|list|A|1")])

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


def _render_boite_detail(sector_code: str, index: int) -> tuple[str, InlineKeyboardMarkup]:
    """Vue détaillée d'une entreprise avec navigation Précédent/Suivant, une par une."""
    companies, _ = _get_sorted_companies(sector_code)
    total = len(companies)

    if total == 0:
        return (
            "Aucune entreprise dans ce secteur.",
            InlineKeyboardMarkup([[InlineKeyboardButton("📋 Retour à la liste", callback_data=f"boites|list|{sector_code}|1")]])
        )

    index = max(0, min(index, total - 1))
    company = companies[index]

    text = _company_detail_text(company) + f"\n\n_Entreprise {index + 1}/{total}_"

    nav_row = []
    if index > 0:
        nav_row.append(InlineKeyboardButton("◀ Précédent", callback_data=f"boites|detail|{sector_code}|{index - 1}"))
    if index < total - 1:
        nav_row.append(InlineKeyboardButton("Suivant ▶", callback_data=f"boites|detail|{sector_code}|{index + 1}"))

    page = index // LISTEBOITES_PAGE_SIZE + 1
    keyboard = []
    if nav_row:
        keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("📋 Retour à la liste", callback_data=f"boites|list|{sector_code}|{page}")])

    return text, InlineKeyboardMarkup(keyboard)


def _is_ceo_or_director(player) -> bool:
    return player["company_role"] in ("PDG", "Directeur")


# ============================================================
# NÉGOCIATION DE CONTRAT (poste + salaire)
# ============================================================

VALID_POSTES = ["Employé", "Manager", "Directeur"]


def _normalize(s: str) -> str:
    """Retire les accents et met en minuscule, pour comparer 'manager' et 'Manager'."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    ).lower()


_POSTE_MAP = {_normalize(p): p for p in VALID_POSTES}


def _parse_poste(raw: str) -> str | None:
    return _POSTE_MAP.get(_normalize(raw))


def _ensure_negotiation_table(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS company_negotiations (
            negotiation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            poste TEXT NOT NULL,
            salaire INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL
        )"""
    )


def _strip_mention_arg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    """
    Si la cible a été donnée par @mention OU par ID numérique en argument
    (pas par reply), retire ce premier token de la liste pour ne garder
    que poste/salaire.
    """
    args = list(context.args)
    if not update.message.reply_to_message and args and (args[0].startswith("@") or args[0].isdigit()):
        return args[1:]
    return args


# ============================================================
# CRÉATION / DISSOLUTION
# ============================================================

async def creerboite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if get_owned_company(user.id) is not None:
        await update.message.reply_text("❌ Tu possèdes déjà une entreprise.")
        return

    cooldown = _cooldown_remaining(player)
    if cooldown > 0:
        await update.message.reply_text(
            f"⏳ Tu dois attendre encore *{_format_cooldown(cooldown)}* avant de pouvoir rejoindre "
            f"ou créer une entreprise, ou payer {fmt_money(REJOIN_SKIP_COST)} avec /payercooldown.",
            parse_mode="Markdown"
        )
        return

    domain = _get_player_domain(user.id)
    domain_label = EDU_SECTORS.get(domain, {}).get("label") if domain else None

    if not domain or not _has_mba(user.id, domain):
        await update.message.reply_text(
            "❌ Il te faut au minimum le diplôme *MBA* (dans ton domaine d'études) pour créer une entreprise.\n"
            "Utilise /diplome pour voir ta progression et passer tes examens.",
            parse_mode="Markdown"
        )
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            f"Utilisation : /creerboite nom secteur\n\n"
            f"Ton secteur (selon ton MBA) : *{domain_label}*\n\n"
            f"Exemple : /creerboite MonEntreprise {domain_label}\n"
            f"Coût : {fmt_money(COMPANY_CREATION_COST)}\n\n"
            f"📍 L'emplacement se choisit après la création, avec /emplacementboite.",
            parse_mode="Markdown"
        )
        return

    sector = context.args[-1]
    name = " ".join(context.args[:-1])

    if sector not in COMPANY_SECTORS:
        sectors_list = ", ".join(COMPANY_SECTORS)
        await update.message.reply_text(
            f"❌ Secteur invalide. Secteurs disponibles :\n{sectors_list}"
        )
        return

    if sector != domain_label:
        await update.message.reply_text(
            f"❌ Ton MBA est en *{domain_label}*, tu ne peux créer une entreprise que dans ce secteur.\n"
            f"Utilise /diplome pour changer de domaine (il faudra revalider les diplômes dans le nouveau secteur).",
            parse_mode="Markdown"
        )
        return

    if get_company_by_name(name) is not None:
        await update.message.reply_text("❌ Ce nom d'entreprise est déjà pris.")
        return

    if player["balance"] < COMPANY_CREATION_COST:
        await update.message.reply_text(
            f"❌ Il te faut {fmt_money(COMPANY_CREATION_COST)} pour créer une entreprise."
        )
        return

    add_balance(user.id, -COMPANY_CREATION_COST)
    # Ville/pays par défaut (Paris/France) — à changer avec /emplacementboite
    company_id = create_company(name, sector, user.id, "Paris", "🇫🇷 France")
    update_player(user.id, company_id=company_id, company_role="PDG", salary=0, company_joined_at=int(time.time()))
    add_company_log(company_id, f"Entreprise créée par {player['first_name']}")

    await update.message.reply_text(
        f"🎉 *{name}* a été créée dans le secteur *{sector}* !\n"
        f"Tu es maintenant PDG. 👑\n\n"
        f"📍 Choisis maintenant l'emplacement de ton siège social avec /emplacementboite.",
        parse_mode="Markdown",
    )


async def emplacementboite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PDG uniquement — définit gratuitement l'emplacement du siège social."""
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = get_owned_company(user.id)
    if company is None:
        await update.message.reply_text("❌ Réservé au PDG.")
        return

    if not context.args:
        pays_liste = "\n".join(
            f"{country} : {', '.join(cities)}"
            for country, cities in list(COMPANY_LOCATIONS.items())[:12]
        )
        await update.message.reply_text(
            f"🌍 *Choisis une ville pour ton siège social*\n\n"
            f"{pays_liste}\n\n"
            f"… et bien d'autres pays disponibles.\n\n"
            f"Utilisation : /emplacementboite NomDeLaVille",
            parse_mode="Markdown"
        )
        return

    city = " ".join(context.args)
    if city not in ALL_CITIES:
        exemples_villes = ", ".join(ALL_CITIES[:15]) + "…"
        await update.message.reply_text(
            f"❌ Ville invalide.\nExemples de villes valides : {exemples_villes}"
        )
        return

    country = next(
        (c for c, cities in COMPANY_LOCATIONS.items() if city in cities), "🌍 Inconnu"
    )

    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET city = ?, country = ? WHERE company_id = ?",
            (city, country, company["company_id"])
        )

    add_company_log(company["company_id"], f"Siège social installé à {city} ({country})")

    await update.message.reply_text(
        f"📍 Siège social défini : *{city}*, {country}",
        parse_mode="Markdown"
    )


async def dissoudreboite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = get_owned_company(user.id)
    if company is None:
        await update.message.reply_text("❌ Tu n'es pas PDG d'une entreprise.")
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
        f"💥 *{company['name']}* a été dissoute.\n"
        + (f"🏦 Trésorerie redistribuée aux actionnaires : {fmt_money(treasury)}"
           if payouts else "🏦 Aucune trésorerie à redistribuer."),
        parse_mode="Markdown",
    )

    for shareholder_id, payout in payouts:
        if shareholder_id == user.id:
            continue
        try:
            await context.bot.send_message(
                chat_id=shareholder_id,
                text=(
                    f"💥 *{company['name']}* a été dissoute par son PDG.\n"
                    f"💰 Tu as reçu *{fmt_money(payout)}* correspondant à tes parts."
                ),
                parse_mode="Markdown",
            )
        except Exception:
            pass


# ============================================================
# LISTE / INFOS
# ============================================================

async def listeboites(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sector_code = "A"
    if context.args:
        name = " ".join(context.args)
        if name in COMPANY_SECTORS:
            sector_code = str(COMPANY_SECTORS.index(name))

    text, markup = _render_listeboites(sector_code, 1)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)


async def listeboites_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        _, mode, sector_code, num_str = query.data.split("|")
        num = int(num_str)
    except (ValueError, AttributeError):
        return

    if mode == "detail":
        text, markup = _render_boite_detail(sector_code, num)
    else:
        text, markup = _render_listeboites(sector_code, num)

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)


async def descriptionboite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PDG uniquement — définit la description affichée sur /infoboite."""
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = get_owned_company(user.id)
    if company is None:
        await update.message.reply_text("❌ Réservé au PDG.")
        return

    if not context.args:
        await update.message.reply_text("Utilisation : /descriptionboite ton texte")
        return

    text = " ".join(context.args)[:300]
    update_company_description(company["company_id"], text)
    await update.message.reply_text("✅ Description mise à jour.")


async def infoboite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Utilisation : /infoboite nom")
        return

    name = " ".join(context.args)
    company = get_company_by_name(name)
    if company is None:
        await update.message.reply_text("❌ Entreprise introuvable.")
        return

    await update.message.reply_text(_company_detail_text(company), parse_mode="Markdown")


async def batiments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catalogue des bâtiments de ta propre entreprise (ou d'une autre en argument)."""
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
                "Utilisation : /batiments nom_entreprise"
            )
            return

    treasury = company["treasury"] or 0
    numeric_level = _company_numeric_level(treasury)
    owned = {b["slot"]: b for b in get_company_buildings(company["company_id"])}

    lines = [
        f"🏗️ *BÂTIMENTS — {company['name']}*",
        f"📊 Niveau : {numeric_level} · 🏦 Trésorerie : {fmt_money(treasury)}",
        "─────────────────────────────",
    ]

    for b in BUILDING_SLOTS:
        slot = b["slot"]
        name, emoji = _building_display_name(company["sector"], slot)
        if slot in owned:
            status = "🚫 Suspendu (maintenance impayée)" if owned[slot]["suspended"] else "✅"
            lines.append(f"{status} {emoji} {name}")
        elif numeric_level < b["level"]:
            lines.append(f"🔒 {emoji} {name} — Débloqué niveau {b['level']}")
            lines.append(f"   {_building_effect_text(b)}")
            continue
        else:
            lines.append(f"⬜ {emoji} {name}")

        if slot not in owned:
            lines.append(f"   💰 {fmt_money(b['cost'])} · Maintenance : {fmt_money(b['maintenance'])}/j")
            lines.append(f"   {_building_effect_text(b)}")
            lines.append(f"   👉 /acheterbatiment {slot}")
        else:
            lines.append(f"   {_building_effect_text(b)}")

    lines.append("─────────────────────────────")
    lines.append("💡 Maintenance prélevée quotidiennement sur la trésorerie.")
    lines.append("⚠️ Trésorerie insuffisante → bâtiment suspendu.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def _building_effect_text(b) -> str:
    effect = b["effect"]
    value = b["value"]
    if effect == "nego_delay":
        return f"-{int(value*100)}% délai négociation de contrats"
    if effect == "treasury_cap":
        return f"+{int(value*100)}% trésorerie max autorisée"
    if effect == "reputation":
        return f"+{int(value*100)}% réputation (boost passif)"
    if effect == "contract_revenue":
        return f"+{int(value*100)}% revenus des contrats"
    if effect == "daily_revenue":
        return f"+{int(value*100)}% revenus journaliers"
    if effect == "interco_loans":
        return "Débloque les prêts inter-entreprises"
    if effect == "exclusive_contracts":
        return "Débloque les contrats exclusifs"
    if effect == "leaderboard":
        return "Visibilité dans le classement mondial"
    return ""


async def acheterbatiment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = get_owned_company(user.id)
    if company is None:
        await update.message.reply_text("❌ Réservé au PDG.")
        return

    if not context.args:
        await update.message.reply_text("Utilisation : /acheterbatiment slot (voir /batiments)")
        return

    slot = context.args[0].lower()
    b = BUILDING_SLOTS_BY_KEY.get(slot)
    if b is None:
        await update.message.reply_text("❌ Bâtiment inconnu. Utilise /batiments pour voir les slots valides.")
        return

    treasury = company["treasury"] or 0
    numeric_level = _company_numeric_level(treasury)
    if numeric_level < b["level"]:
        await update.message.reply_text(
            f"❌ Ce bâtiment nécessite le niveau {b['level']} (tu es niveau {numeric_level})."
        )
        return

    if get_company_building(company["company_id"], slot) is not None:
        await update.message.reply_text("❌ Tu possèdes déjà ce bâtiment.")
        return

    if treasury < b["cost"]:
        await update.message.reply_text(
            f"❌ Trésorerie insuffisante.\n💰 Requis : {fmt_money(b['cost'])}\n🏦 Disponible : {fmt_money(treasury)}"
        )
        return

    if not withdraw_from_treasury(company["company_id"], b["cost"]):
        await update.message.reply_text("❌ Trésorerie insuffisante.")
        return

    add_company_building(company["company_id"], slot)
    name, emoji = _building_display_name(company["sector"], slot)
    add_company_log(company["company_id"], f"Bâtiment acheté : {name} ({fmt_money(b['cost'])})")

    await update.message.reply_text(
        f"✅ {emoji} *{name}* acheté pour *{fmt_money(b['cost'])}* !\n"
        f"🔧 Maintenance quotidienne : {fmt_money(b['maintenance'])}/j\n"
        f"{_building_effect_text(b)}",
        parse_mode="Markdown"
    )


async def mesbatiments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = get_owned_company(user.id)
    if company is None and player["company_id"]:
        company = get_company_by_id(player["company_id"])
    if company is None:
        await update.message.reply_text("❌ Tu ne possèdes ni ne travailles dans aucune entreprise.")
        return

    owned = get_company_buildings(company["company_id"])
    if not owned:
        await update.message.reply_text(
            "📭 Aucun bâtiment acheté pour l'instant.\nUtilise /batiments pour voir le catalogue."
        )
        return

    total_maintenance = 0
    lines = [f"🏗️ *TES BÂTIMENTS — {company['name']}*\n"]
    for row in owned:
        b = BUILDING_SLOTS_BY_KEY.get(row["slot"])
        if b is None:
            continue
        name, emoji = _building_display_name(company["sector"], row["slot"])
        status = "🚫 Suspendu" if row["suspended"] else "✅ Actif"
        total_maintenance += b["maintenance"]
        lines.append(f"{status} {emoji} {name} — {fmt_money(b['maintenance'])}/j")

    lines.append(f"\n💸 Maintenance totale/jour : {fmt_money(total_maintenance)}")
    lines.append(f"🏦 Trésorerie : {fmt_money(company['treasury'])}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


ROLE_EMOJI = {"PDG": "👑", "Directeur": "🏦", "Manager": "💼", "Employé": "👷"}


async def monentreprise(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if not player["company_id"]:
        await update.message.reply_text("❌ Tu ne travailles dans aucune entreprise.")
        return

    company = get_company_by_id(player["company_id"])
    employees = get_company_employees(player["company_id"])
    emoji = SECTOR_EMOJI.get(company["sector"], "🏢")
    treasury = company["treasury"]
    badge = _company_badge(treasury)
    level = _company_level(treasury)
    reputation = min(5.0, (company["reputation"] if company["reputation"] is not None else 3.0) * (1 + get_company_reputation_bonus(company["company_id"])))
    max_employees = company["max_employees"] or 200
    role_emoji = ROLE_EMOJI.get(player["company_role"], "🪙")

    text = (
        f"「 {emoji} 」{company['name']}\n"
        f"✦ {badge} {level}  ┊  ⭐ {reputation:.1f}/5\n\n"
        f"◈━━━━━━━━━━━━━━━━━━━━━━━━◈\n\n"
        f"  💰 VALEUR\n"
        f"  ╰┈➤  {_fmt_treasury(treasury)}\n\n"
        f"  🏦 TRÉSORERIE\n"
        f"  ╰┈➤  {_fmt_treasury(treasury)}\n\n"
        f"  📈 TON SALAIRE/JOUR\n"
        f"  ╰┈➤  💵 {fmt_money(player['salary'])}/jour\n\n"
        f"  👥 ÉQUIPE\n"
        f"  ╰┈➤  {len(employees)}/{max_employees} employés\n\n"
        f"◈━━━━━━━━━━━━━━━━━━━━━━━━◈\n\n"
        f"  🪙 TON POSTE\n"
        f"  ╰┈➤  {role_emoji} {player['company_role']}\n"
        f"  ╰┈➤  {player['cmd_count']} commandes utilisées"
    )
    await update.message.reply_text(text)


async def employes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            await update.message.reply_text(
                "❌ Tu ne travailles dans aucune entreprise. Précise un nom : /employes nom"
            )
            return
        company = get_company_by_id(player["company_id"])

    employees = get_company_employees(company["company_id"])
    if not employees:
        await update.message.reply_text("Aucun employé.")
        return

    with get_conn() as conn:
        ceo = conn.execute("SELECT * FROM players WHERE user_id = ?", (company["ceo_id"],)).fetchone()

    emoji = SECTOR_EMOJI.get(company["sector"], "🏢")
    badge = _company_badge(company["treasury"])
    level = _company_level(company["treasury"])
    max_employees = company["max_employees"] or 200

    def _tag(e, show_role=False):
        name = e["username"] or e["first_name"]
        date = time.strftime("%d/%m/%y", time.localtime(e["company_joined_at"])) if e["company_joined_at"] else "?"
        role_suffix = f" — {e['company_role']}" if show_role else ""
        return f"  ╰┈➤ @{name}{role_suffix}  (depuis {date})"

    groups = {"PDG": [], "Directeur": [], "Manager": [], "Employé": [], "Autres": []}
    for e in employees:
        role = e["company_role"] or "Employé"
        if role in ("PDG", "Directeur", "Manager", "Employé"):
            groups[role].append(e)
        else:
            # Poste personnalisé (attribué via /nommer) qui ne correspond à
            # aucun des 4 postes standards : avant, ça finissait rangé sous
            # "Employé" silencieusement, en perdant le vrai titre.
            groups["Autres"].append(e)

    lines = [
        f"「 {emoji} 」{company['name']}  ·  {badge} {level}",
        f"👥 {len(employees)}/{max_employees} employés",
        "◈━━━━━━━━━━━━━━━━━━━━━━━━◈",
    ]

    section_labels = [
        ("PDG", "👑 PDG :"),
        ("Directeur", "🏦 Directeurs :"),
        ("Manager", "💼 Managers :"),
        ("Employé", "👷 Employés :"),
        ("Autres", "🎖️ Autres postes :"),
    ]
    for role_key, label in section_labels:
        members = groups[role_key]
        if not members:
            continue
        lines.append("")
        lines.append(label)
        for e in members:
            lines.append(_tag(e, show_role=(role_key == "Autres")))

    lines.append("")
    lines.append("◈━━━━━━━━━━━━━━━━━━━━━━━━◈")
    lines.append("ℹ️ Utilise /infoboite pour les détails financiers.")

    await update.message.reply_text("\n".join(lines))


# ============================================================
# CANDIDATURES
# ============================================================

async def _submit_application(context: ContextTypes.DEFAULT_TYPE, user, player, company) -> int:
    """Crée la candidature et notifie le PDG avec boutons Accepter/Refuser. Renvoie l'application_id."""
    application_id = create_application(company["company_id"], user.id, "postuler")

    diplomas_txt = _diplomas_display(user.id)
    notif = (
        f"🔔 *Nouvelle candidature !*\n\n"
        f"👤 {player['first_name']} postule dans *{company['name']}*\n"
        f"🎓 Diplômes : {diplomas_txt}"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accepter", callback_data=f"appdecision|accept|{application_id}"),
        InlineKeyboardButton("❌ Refuser", callback_data=f"appdecision|refuse|{application_id}"),
    ]])
    try:
        await context.bot.send_message(chat_id=company["ceo_id"], text=notif, parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        pass

    return application_id


async def postuler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if player["company_id"] and player["company_role"] != "PDG":
        await update.message.reply_text("❌ Tu fais déjà partie d'une entreprise.")
        return

    cooldown = _cooldown_remaining(player)
    if cooldown > 0:
        await update.message.reply_text(
            f"⏳ Tu dois attendre encore *{_format_cooldown(cooldown)}* avant de pouvoir rejoindre "
            f"une nouvelle entreprise, ou payer {fmt_money(REJOIN_SKIP_COST)} avec /payercooldown.",
            parse_mode="Markdown"
        )
        return

    if not context.args:
        await update.message.reply_text("Utilisation : /postuler nom_entreprise")
        return

    name = " ".join(context.args)
    company = get_company_by_name(name)
    if company is None:
        await update.message.reply_text("❌ Entreprise introuvable.")
        return

    if not _get_passed_tiers(user.id):
        await update.message.reply_text(
            "❌ Tu n'as aucun diplôme.\nLe *Bac* minimum (dans n'importe quel secteur) est requis pour postuler.",
            parse_mode="Markdown"
        )
        return

    await _submit_application(context, user, player, company)
    await update.message.reply_text(
        f"📨 Candidature envoyée à *{company['name']}*. Le PDG doit l'accepter avec /accepter.",
        parse_mode="Markdown",
    )


async def rejoindre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if player["company_id"] and player["company_role"] != "PDG":
        await update.message.reply_text("❌ Tu fais déjà partie d'une entreprise.")
        return

    cooldown = _cooldown_remaining(player)
    if cooldown > 0:
        await update.message.reply_text(
            f"⏳ Tu dois attendre encore *{_format_cooldown(cooldown)}* avant de pouvoir rejoindre "
            f"une nouvelle entreprise, ou payer {fmt_money(REJOIN_SKIP_COST)} avec /payercooldown.",
            parse_mode="Markdown"
        )
        return

    if not context.args:
        await update.message.reply_text("Utilisation : /rejoindre nom_entreprise")
        return

    name = " ".join(context.args)
    company = get_company_by_name(name)
    if company is None:
        await update.message.reply_text("❌ Entreprise introuvable.")
        return

    with get_conn() as conn:
        invite = conn.execute(
            """SELECT * FROM company_applications
               WHERE company_id = ? AND user_id = ? AND kind = 'invite' AND status = 'pending'
               ORDER BY created_at DESC LIMIT 1""",
            (company["company_id"], user.id),
        ).fetchone()

    if invite is None:
        await update.message.reply_text("❌ Tu n'as aucune invitation en attente de cette entreprise.")
        return

    grade = _grade_from_diploma(user.id)
    update_application_status(invite["application_id"], "accepted")
    update_player(user.id, company_id=company["company_id"], company_role=grade, salary=0, company_joined_at=int(time.time()))
    add_company_log(company["company_id"], f"{player['first_name']} a rejoint l'entreprise en tant que {grade}")

    await update.message.reply_text(
        f"🎉 Tu as rejoint *{company['name']}* en tant que *{grade}* !",
        parse_mode="Markdown"
    )


REJOIN_COOLDOWN_SECONDS = 7 * 86400  # 1 semaine avant de pouvoir rejoindre une nouvelle entreprise
REJOIN_SKIP_COST = 3_000_000         # coût pour sauter le cooldown et rejoindre immédiatement


def _cooldown_remaining(player) -> int:
    """Secondes restantes avant de pouvoir rejoindre une nouvelle entreprise (0 si aucun cooldown)."""
    if player is None:
        return 0
    until = player["company_cooldown_until"] or 0
    remaining = until - int(time.time())
    return remaining if remaining > 0 else 0


def _format_cooldown(seconds: int) -> str:
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    if days > 0:
        return f"{days}j {hours}h"
    minutes = (seconds % 3600) // 60
    return f"{hours}h {minutes}min"


async def demissionner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if not player["company_id"]:
        await update.message.reply_text("❌ Tu ne travailles dans aucune entreprise.")
        return

    if player["company_role"] == "PDG":
        await update.message.reply_text(
            "❌ Tu es PDG, tu dois dissoudre l'entreprise (/dissoudreboite) ou la transmettre."
        )
        return

    company = get_company_by_id(player["company_id"])
    cooldown_until = int(time.time()) + REJOIN_COOLDOWN_SECONDS
    update_player(user.id, company_id=None, company_role=None, salary=0, company_cooldown_until=cooldown_until)
    add_company_log(company["company_id"], f"{player['first_name']} a démissionné")

    await update.message.reply_text(
        f"👋 Tu as démissionné de *{company['name']}*.\n\n"
        f"⏳ Tu dois attendre *7 jours* avant de pouvoir rejoindre une nouvelle entreprise, "
        f"ou payer *{fmt_money(REJOIN_SKIP_COST)}* avec /payercooldown pour rejoindre immédiatement.",
        parse_mode="Markdown"
    )


async def payercooldown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Paye pour sauter le cooldown d'attente avant de rejoindre une nouvelle entreprise."""
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    remaining = _cooldown_remaining(player)
    if remaining <= 0:
        await update.message.reply_text("✅ Tu n'as aucun cooldown en cours, tu peux déjà rejoindre une entreprise.")
        return

    if player["balance"] < REJOIN_SKIP_COST:
        await update.message.reply_text(
            f"❌ Il te faut {fmt_money(REJOIN_SKIP_COST)} pour sauter le cooldown.\n"
            f"Tu as : {fmt_money(player['balance'])}\n"
            f"Temps restant sinon : {_format_cooldown(remaining)}"
        )
        return

    add_balance(user.id, -REJOIN_SKIP_COST)
    update_player(user.id, company_cooldown_until=0)

    await update.message.reply_text(
        f"✅ Cooldown levé ! Tu peux rejoindre une nouvelle entreprise dès maintenant "
        f"(coût : {fmt_money(REJOIN_SKIP_COST)})."
    )


async def candidatures(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = _get_manageable_company(user.id, player)
    if company is None:
        await update.message.reply_text("❌ Réservé au PDG ou aux directeurs.")
        return

    apps = get_pending_applications(company["company_id"])
    apps = [a for a in apps if a["kind"] == "postuler"]
    if not apps:
        await update.message.reply_text("Aucune candidature en attente.")
        return

    lines = ["🔔 *Candidatures en attente*\n"]
    keyboard = []
    for a in apps:
        with get_conn() as conn:
            candidate = conn.execute(
                "SELECT * FROM players WHERE user_id = ?", (a["user_id"],)
            ).fetchone()
        nom = candidate["first_name"] if candidate else "Inconnu"
        diplomas_txt = _diplomas_display(a["user_id"])
        lines.append(f"👤 {nom} postule dans *{company['name']}*\n🎓 Diplômes : {diplomas_txt}\n")
        keyboard.append([
            InlineKeyboardButton(f"✅ {nom}", callback_data=f"appdecision|accept|{a['application_id']}"),
            InlineKeyboardButton(f"❌ {nom}", callback_data=f"appdecision|refuse|{a['application_id']}"),
        ])

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_application_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gère les clics sur les boutons ✅/❌ d'une candidature (/postuler, /candidatures)."""
    query = update.callback_query

    try:
        _, action, app_id_str = query.data.split("|")
        application_id = int(app_id_str)
    except (ValueError, AttributeError):
        await query.answer()
        return

    application = get_application(application_id)
    if application is None or application["status"] != "pending" or application["kind"] != "postuler":
        await query.answer("❌ Candidature introuvable ou déjà traitée.", show_alert=True)
        return

    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if player["company_id"] != application["company_id"] or not _is_ceo_or_director(player):
        await query.answer("❌ Tu n'es pas autorisé à traiter cette candidature.", show_alert=True)
        return

    if action == "accept":
        candidate_player = get_player_by_id(application["user_id"])
        cooldown = _cooldown_remaining(candidate_player)
        if cooldown > 0:
            await query.answer(
                f"⏳ Cette personne doit encore attendre {_format_cooldown(cooldown)} avant de rejoindre une entreprise.",
                show_alert=True
            )
            return

    await query.answer()

    company = get_company_by_id(application["company_id"])
    with get_conn() as conn:
        candidate = conn.execute(
            "SELECT * FROM players WHERE user_id = ?", (application["user_id"],)
        ).fetchone()
    nom = candidate["first_name"] if candidate else "Inconnu"

    if action == "accept":
        if not _get_passed_tiers(application["user_id"]):
            await query.edit_message_text(
                f"❌ {nom} n'a aucun diplôme.\nLe Bac minimum (dans n'importe quel secteur) est requis pour être recruté."
            )
            return

        grade = _grade_from_diploma(application["user_id"])
        update_application_status(application_id, "accepted")
        update_player(application["user_id"], company_id=application["company_id"], company_role=grade, salary=0, company_joined_at=int(time.time()))
        add_company_log(application["company_id"], f"{nom} a été recruté en tant que {grade}")

        await query.edit_message_text(f"✅ {nom} a été embauché(e) en tant que {grade} !")

        try:
            await context.bot.send_message(
                chat_id=application["user_id"],
                text=f"🎉 *Candidature acceptée !*\n\nTu as rejoint *{company['name']}* en tant que *{grade}* !",
                parse_mode="Markdown",
            )
        except Exception:
            pass
    else:
        update_application_status(application_id, "refused")
        await query.edit_message_text(f"❌ Candidature de {nom} refusée.")
        try:
            await context.bot.send_message(
                chat_id=application["user_id"],
                text=f"❌ Ta candidature chez *{company['name']}* a été refusée.",
                parse_mode="Markdown",
            )
        except Exception:
            pass


async def accepter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = _get_manageable_company(user.id, player)
    if company is None:
        await update.message.reply_text("❌ Réservé au PDG ou aux directeurs.")
        return

    if not context.args:
        await update.message.reply_text("Utilisation : /accepter id_joueur")
        return

    try:
        candidate_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID invalide.")
        return

    with get_conn() as conn:
        application = conn.execute(
            """SELECT * FROM company_applications
               WHERE company_id = ? AND user_id = ? AND kind = 'postuler' AND status = 'pending'
               ORDER BY created_at DESC LIMIT 1""",
            (company["company_id"], candidate_id),
        ).fetchone()

    if application is None:
        await update.message.reply_text("❌ Candidature introuvable ou déjà traitée.")
        return

    candidate_player = get_player_by_id(candidate_id)
    cooldown = _cooldown_remaining(candidate_player)
    if cooldown > 0:
        await update.message.reply_text(
            f"⏳ Cette personne doit encore attendre *{_format_cooldown(cooldown)}* "
            f"avant de pouvoir rejoindre une nouvelle entreprise (ou payer pour sauter le cooldown).",
            parse_mode="Markdown"
        )
        return

    if not _get_passed_tiers(application["user_id"]):
        with get_conn() as conn:
            candidate = conn.execute(
                "SELECT * FROM players WHERE user_id = ?", (application["user_id"],)
            ).fetchone()
        await update.message.reply_text(
            f"❌ {candidate['first_name']} n'a aucun diplôme.\n"
            f"Le *Bac* minimum (dans n'importe quel secteur) est requis pour être recruté.",
            parse_mode="Markdown"
        )
        return

    grade = _grade_from_diploma(application["user_id"])

    update_application_status(application["application_id"], "accepted")
    update_player(application["user_id"], company_id=company["company_id"], company_role=grade, salary=0, company_joined_at=int(time.time()))

    with get_conn() as conn:
        candidate = conn.execute(
            "SELECT * FROM players WHERE user_id = ?", (application["user_id"],)
        ).fetchone()

    add_company_log(company["company_id"], f"{candidate['first_name']} a été recruté en tant que {grade}")
    await update.message.reply_text(f"✅ {candidate['first_name']} a été embauché(e) en tant que *{grade}* !", parse_mode="Markdown")

    try:
        await context.bot.send_message(
            chat_id=application["user_id"],
            text=(
                f"🎉 *Candidature acceptée !*\n\n"
                f"Tu as rejoint *{company['name']}* en tant que *{grade}* !"
            ),
            parse_mode="Markdown",
        )
    except Exception:
        pass


async def refuser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = _get_manageable_company(user.id, player)
    if company is None:
        await update.message.reply_text("❌ Réservé au PDG ou aux directeurs.")
        return

    if not context.args:
        await update.message.reply_text("Utilisation : /refuser id_joueur")
        return

    try:
        candidate_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID invalide.")
        return

    with get_conn() as conn:
        application = conn.execute(
            """SELECT * FROM company_applications
               WHERE company_id = ? AND user_id = ? AND kind = 'postuler' AND status = 'pending'
               ORDER BY created_at DESC LIMIT 1""",
            (company["company_id"], candidate_id),
        ).fetchone()

    if application is None:
        await update.message.reply_text("❌ Candidature introuvable ou déjà traitée.")
        return

    update_application_status(application["application_id"], "refused")
    await update.message.reply_text("❌ Candidature refusée.")

    try:
        await context.bot.send_message(
            chat_id=application["user_id"],
            text=f"❌ Ta candidature chez *{company['name']}* a été refusée.",
            parse_mode="Markdown",
        )
    except Exception:
        pass


# ============================================================
# RECRUTEMENT DIRECT / GESTION DU PERSONNEL
# ============================================================

async def recruter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = _get_manageable_company(user.id, player)
    if company is None:
        await update.message.reply_text("❌ Réservé au PDG ou aux directeurs.")
        return

    target = _get_target_user(update, context)
    if target is None:
        await update.message.reply_text(
            "Utilisation : /recruter id_ou_pseudo [poste] (ou en réponse à son message)"
        )
        return

    target_player = get_or_create_player(target.id, target.username, target.first_name)
    if target_player["company_id"]:
        if target_player["company_id"] == company["company_id"]:
            await update.message.reply_text(
                "❌ Cette personne travaille déjà chez toi ! Utilise /proposersalaire pour lui fixer un salaire, "
                "ou /nommer pour changer son poste."
            )
        else:
            await update.message.reply_text("❌ Cette personne travaille déjà dans une autre entreprise.")
        return

    grade = _grade_from_diploma(target.id)
    diplomas_txt = _diplomas_display(target.id)

    if not _get_passed_tiers(target.id):
        await update.message.reply_text(
            f"❌ {target.first_name} n'a aucun diplôme.\n"
            f"Le *Bac* minimum (dans n'importe quel secteur) est requis pour être recruté.",
            parse_mode="Markdown"
        )
        return

    application_id = create_application(company["company_id"], target.id, "invite")
    await update.message.reply_text(
        f"📨 Invitation envoyée à {target.first_name}.\n"
        f"🎓 Diplômes : {diplomas_txt}\n"
        f"📋 Poste à l'embauche : *{grade}*",
        parse_mode="Markdown"
    )

    notif = (
        f"📨 *Invitation à rejoindre une entreprise !*\n\n"
        f"🏢 Entreprise : *{company['name']}*\n"
        f"📋 Poste proposé : *{grade}*"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Rejoindre", callback_data=f"invitedecision|accept|{application_id}"),
        InlineKeyboardButton("❌ Refuser", callback_data=f"invitedecision|refuse|{application_id}"),
    ]])
    try:
        await context.bot.send_message(chat_id=target.id, text=notif, parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        pass


async def handle_invite_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gère les clics sur les boutons ✅/❌ d'une invitation directe (/recruter)."""
    query = update.callback_query

    try:
        _, action, app_id_str = query.data.split("|")
        application_id = int(app_id_str)
    except (ValueError, AttributeError):
        await query.answer()
        return

    application = get_application(application_id)
    if application is None or application["status"] != "pending" or application["kind"] != "invite":
        await query.answer("❌ Invitation introuvable ou déjà traitée.", show_alert=True)
        return

    user = update.effective_user
    if user.id != application["user_id"]:
        await query.answer("❌ Cette invitation ne t'est pas destinée.", show_alert=True)
        return

    player = get_or_create_player(user.id, user.username, user.first_name)
    if player["company_id"] and player["company_role"] != "PDG":
        await query.answer("❌ Tu fais déjà partie d'une entreprise.", show_alert=True)
        return

    if action == "accept":
        cooldown = _cooldown_remaining(player)
        if cooldown > 0:
            await query.answer(
                f"⏳ Tu dois attendre encore {_format_cooldown(cooldown)} avant de rejoindre une entreprise.",
                show_alert=True
            )
            return

    company = get_company_by_id(application["company_id"])
    if company is None:
        await query.answer("❌ Cette entreprise n'existe plus.", show_alert=True)
        return

    await query.answer()

    if action == "accept":
        grade = _grade_from_diploma(user.id)
        update_application_status(application_id, "accepted")
        update_player(user.id, company_id=company["company_id"], company_role=grade, salary=0, company_joined_at=int(time.time()))
        add_company_log(company["company_id"], f"{player['first_name']} a rejoint l'entreprise en tant que {grade}")

        await query.edit_message_text(f"🎉 Tu as rejoint {company['name']} en tant que {grade} !")

        ceo = get_player_by_id(company["ceo_id"]) if company["ceo_id"] != user.id else None
        if ceo is not None:
            try:
                await context.bot.send_message(
                    chat_id=company["ceo_id"],
                    text=f"🎉 *{player['first_name']}* a rejoint *{company['name']}* en tant que *{grade}* !",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
    else:
        update_application_status(application_id, "refused")
        await query.edit_message_text("❌ Invitation refusée.")
        try:
            await context.bot.send_message(
                chat_id=company["ceo_id"],
                text=f"❌ {player['first_name']} a refusé l'invitation à rejoindre *{company['name']}*.",
                parse_mode="Markdown",
            )
        except Exception:
            pass


async def negociercontrat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PDG/Directeur — propose un poste + salaire précis à un joueur libre."""
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = _get_manageable_company(user.id, player)
    if company is None:
        await update.message.reply_text("❌ Réservé au PDG ou aux directeurs.")
        return

    target = _get_target_user(update, context)
    args = _strip_mention_arg(update, context)

    if target is None or len(args) < 2:
        await update.message.reply_text(
            "Utilisation : /negociercontrat @joueur poste salaire\n"
            "(ou en réponse à son message : /negociercontrat poste salaire)\n\n"
            f"Postes valides : {', '.join(VALID_POSTES)}"
        )
        return

    poste = _parse_poste(args[0])
    if poste is None:
        await update.message.reply_text(f"❌ Poste invalide. Postes valides : {', '.join(VALID_POSTES)}")
        return

    try:
        salaire = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ Salaire invalide.")
        return

    if salaire < 0:
        await update.message.reply_text("❌ Le salaire ne peut pas être négatif.")
        return

    target_player = get_or_create_player(target.id, target.username, target.first_name)
    is_own_employee = target_player["company_id"] == company["company_id"]
    if target_player["company_id"] and not is_own_employee:
        await update.message.reply_text("❌ Cette personne travaille déjà dans une autre entreprise.")
        return

    if not is_own_employee and not _get_passed_tiers(target.id):
        await update.message.reply_text(
            f"❌ {target.first_name} n'a aucun diplôme.\n"
            f"Le *Bac* minimum (dans n'importe quel secteur) est requis pour être recruté.",
            parse_mode="Markdown"
        )
        return

    with get_conn() as conn:
        _ensure_negotiation_table(conn)
        conn.execute(
            "UPDATE company_negotiations SET status = 'cancelled' "
            "WHERE company_id = ? AND user_id = ? AND status = 'pending'",
            (company["company_id"], target.id)
        )
        cur = conn.execute(
            "INSERT INTO company_negotiations (company_id, user_id, poste, salaire, status, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (company["company_id"], target.id, poste, salaire, int(time.time()))
        )
        negotiation_id = cur.lastrowid

    await update.message.reply_text(
        f"📝 Offre envoyée à {target.first_name} :\n"
        f"🏢 Entreprise : *{company['name']}*\n"
        f"📋 Poste : *{poste}*\n"
        f"💰 Salaire : *{fmt_money(salaire)}*\n\n"
        f"Il/elle peut valider ou refuser via le message reçu.",
        parse_mode="Markdown"
    )

    notif = (
        f"📝 *Offre d'embauche reçue !*\n\n"
        f"🏢 Entreprise : *{company['name']}*\n"
        f"📋 Poste proposé : *{poste}*\n"
        f"💰 Salaire : *{fmt_money(salaire)}*"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accepter", callback_data=f"negodecision|accept|{negotiation_id}"),
        InlineKeyboardButton("❌ Refuser", callback_data=f"negodecision|refuse|{negotiation_id}"),
    ]])
    try:
        await context.bot.send_message(chat_id=target.id, text=notif, parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        pass


async def negociercontratmodifier(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PDG/Directeur — modifie une offre de négociation encore en attente."""
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = _get_manageable_company(user.id, player)
    if company is None:
        await update.message.reply_text("❌ Réservé au PDG ou aux directeurs.")
        return

    target = _get_target_user(update, context)
    args = _strip_mention_arg(update, context)

    if target is None or len(args) < 2:
        await update.message.reply_text(
            "Utilisation : /negociercontratmodifier @joueur poste salaire\n"
            "(ou en réponse à son message : /negociercontratmodifier poste salaire)"
        )
        return

    poste = _parse_poste(args[0])
    if poste is None:
        await update.message.reply_text(f"❌ Poste invalide. Postes valides : {', '.join(VALID_POSTES)}")
        return

    try:
        salaire = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ Salaire invalide.")
        return

    if salaire < 0:
        await update.message.reply_text("❌ Le salaire ne peut pas être négatif.")
        return

    with get_conn() as conn:
        _ensure_negotiation_table(conn)
        negotiation = conn.execute(
            "SELECT * FROM company_negotiations WHERE company_id = ? AND user_id = ? AND status = 'pending' "
            "ORDER BY created_at DESC LIMIT 1",
            (company["company_id"], target.id)
        ).fetchone()

        if negotiation is None:
            await update.message.reply_text("❌ Aucune négociation en attente avec cette personne.")
            return

        conn.execute(
            "UPDATE company_negotiations SET poste = ?, salaire = ? WHERE negotiation_id = ?",
            (poste, salaire, negotiation["negotiation_id"])
        )

    await update.message.reply_text(
        f"✏️ Offre modifiée pour {target.first_name} :\n"
        f"📋 Nouveau poste : *{poste}*\n"
        f"💰 Nouveau salaire : *{fmt_money(salaire)}*",
        parse_mode="Markdown"
    )

    notif = (
        f"✏️ *Offre d'embauche modifiée !*\n\n"
        f"📋 Nouveau poste : *{poste}*\n"
        f"💰 Nouveau salaire : *{fmt_money(salaire)}*"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accepter", callback_data=f"negodecision|accept|{negotiation['negotiation_id']}"),
        InlineKeyboardButton("❌ Refuser", callback_data=f"negodecision|refuse|{negotiation['negotiation_id']}"),
    ]])
    try:
        await context.bot.send_message(chat_id=target.id, text=notif, parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        pass


async def handle_negotiation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gère les clics sur les boutons ✅/❌ d'une offre de négociation (/negociercontrat)."""
    query = update.callback_query

    try:
        _, action, negotiation_id_str = query.data.split("|")
        negotiation_id = int(negotiation_id_str)
    except (ValueError, AttributeError):
        await query.answer()
        return

    user = update.effective_user

    with get_conn() as conn:
        _ensure_negotiation_table(conn)
        negotiation = conn.execute(
            "SELECT * FROM company_negotiations WHERE negotiation_id = ?", (negotiation_id,)
        ).fetchone()

    if negotiation is None or negotiation["status"] != "pending":
        await query.answer("❌ Cette offre n'existe plus ou a déjà été traitée.", show_alert=True)
        return

    if negotiation["user_id"] != user.id:
        await query.answer("❌ Cette offre ne t'est pas destinée.", show_alert=True)
        return

    player = get_or_create_player(user.id, user.username, user.first_name)

    if action == "refuse":
        await query.answer()
        with get_conn() as conn:
            conn.execute(
                "UPDATE company_negotiations SET status = 'refused' WHERE negotiation_id = ?",
                (negotiation_id,)
            )
        await query.edit_message_text("❌ Offre de négociation refusée.")

        company = get_company_by_id(negotiation["company_id"])
        if company is not None:
            notif = f"❌ *Négociation refusée.*\n\n{player['first_name']} a refusé l'offre d'embauche."
            try:
                await context.bot.send_message(chat_id=company["ceo_id"], text=notif, parse_mode="Markdown")
            except Exception:
                pass
        return

    # action == "accept"
    if player["company_id"] and player["company_id"] != negotiation["company_id"] and player["company_role"] != "PDG":
        await query.answer("❌ Tu fais déjà partie d'une autre entreprise.", show_alert=True)
        return

    if not player["company_id"] or player["company_role"] == "PDG":
        cooldown = _cooldown_remaining(player)
        if cooldown > 0:
            await query.answer(
                f"⏳ Tu dois attendre encore {_format_cooldown(cooldown)} avant de pouvoir rejoindre une entreprise.",
                show_alert=True
            )
            return

    company = get_company_by_id(negotiation["company_id"])
    if company is None:
        await query.answer("❌ Cette entreprise n'existe plus.", show_alert=True)
        return

    await query.answer()

    with get_conn() as conn:
        conn.execute(
            "UPDATE company_negotiations SET status = 'accepted' WHERE negotiation_id = ?",
            (negotiation_id,)
        )

    was_already_employee = player["company_id"] == company["company_id"]

    update_player(
        user.id,
        company_id=company["company_id"],
        company_role=negotiation["poste"],
        salary=negotiation["salaire"],
        **({} if was_already_employee else {"company_joined_at": int(time.time())})
    )

    if was_already_employee:
        add_company_log(
            company["company_id"],
            f"{player['first_name']} a renégocié son contrat : {negotiation['poste']} — {fmt_money(negotiation['salaire'])}"
        )
        await query.edit_message_text(
            f"✅ Nouveau contrat validé chez {company['name']} : {negotiation['poste']}, "
            f"salaire {fmt_money(negotiation['salaire'])} !"
        )
    else:
        add_company_log(
            company["company_id"],
            f"{player['first_name']} a rejoint via négociation : {negotiation['poste']} — {fmt_money(negotiation['salaire'])}"
        )
        await query.edit_message_text(
            f"🎉 Tu as rejoint {company['name']} en tant que {negotiation['poste']} "
            f"avec un salaire de {fmt_money(negotiation['salaire'])} !"
        )

    if company["ceo_id"] != user.id:
        verb = "a renégocié son contrat chez" if was_already_employee else "a rejoint"
        notif = (
            f"🎉 *Négociation acceptée !*\n\n"
            f"{player['first_name']} {verb} *{company['name']}* en tant que *{negotiation['poste']}* "
            f"— {fmt_money(negotiation['salaire'])}."
        )
        try:
            await context.bot.send_message(chat_id=company["ceo_id"], text=notif, parse_mode="Markdown")
        except Exception:
            pass


async def accepternegociation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Le joueur ciblé valide l'offre de négociation reçue."""
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    with get_conn() as conn:
        _ensure_negotiation_table(conn)
        negotiation = conn.execute(
            "SELECT * FROM company_negotiations WHERE user_id = ? AND status = 'pending' "
            "ORDER BY created_at DESC LIMIT 1",
            (user.id,)
        ).fetchone()

    if negotiation is None:
        await update.message.reply_text("❌ Tu n'as aucune offre de négociation en attente.")
        return

    if player["company_id"] and player["company_id"] != negotiation["company_id"] and player["company_role"] != "PDG":
        await update.message.reply_text("❌ Tu fais déjà partie d'une autre entreprise.")
        return

    if not player["company_id"] or player["company_role"] == "PDG":
        cooldown = _cooldown_remaining(player)
        if cooldown > 0:
            await update.message.reply_text(
                f"⏳ Tu dois attendre encore *{_format_cooldown(cooldown)}* avant de pouvoir rejoindre "
                f"une nouvelle entreprise, ou payer {fmt_money(REJOIN_SKIP_COST)} avec /payercooldown.",
                parse_mode="Markdown"
            )
            return

    company = get_company_by_id(negotiation["company_id"])
    if company is None:
        await update.message.reply_text("❌ Cette entreprise n'existe plus.")
        return

    with get_conn() as conn:
        conn.execute(
            "UPDATE company_negotiations SET status = 'accepted' WHERE negotiation_id = ?",
            (negotiation["negotiation_id"],)
        )

    was_already_employee = player["company_id"] == company["company_id"]

    update_player(
        user.id,
        company_id=company["company_id"],
        company_role=negotiation["poste"],
        salary=negotiation["salaire"],
        **({} if was_already_employee else {"company_joined_at": int(time.time())})
    )

    if was_already_employee:
        add_company_log(
            company["company_id"],
            f"{player['first_name']} a renégocié son contrat : {negotiation['poste']} — {fmt_money(negotiation['salaire'])}"
        )
        await update.message.reply_text(
            f"✅ Nouveau contrat validé chez *{company['name']}* : *{negotiation['poste']}*, "
            f"salaire *{fmt_money(negotiation['salaire'])}* !",
            parse_mode="Markdown"
        )
    else:
        add_company_log(
            company["company_id"],
            f"{player['first_name']} a rejoint via négociation : {negotiation['poste']} — {fmt_money(negotiation['salaire'])}"
        )
        await update.message.reply_text(
            f"🎉 Tu as rejoint *{company['name']}* en tant que *{negotiation['poste']}* "
            f"avec un salaire de *{fmt_money(negotiation['salaire'])}* !",
            parse_mode="Markdown"
        )

    if company["ceo_id"] != user.id:
        verb = "a renégocié son contrat chez" if was_already_employee else "a rejoint"
        notif = (
            f"🎉 *Négociation acceptée !*\n\n"
            f"{player['first_name']} {verb} *{company['name']}* en tant que *{negotiation['poste']}* "
            f"— {fmt_money(negotiation['salaire'])}."
        )
        try:
            await context.bot.send_message(chat_id=company["ceo_id"], text=notif, parse_mode="Markdown")
        except Exception:
            pass


async def refusenegociation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Le joueur ciblé refuse l'offre de négociation reçue."""
    user = update.effective_user

    with get_conn() as conn:
        _ensure_negotiation_table(conn)
        negotiation = conn.execute(
            "SELECT * FROM company_negotiations WHERE user_id = ? AND status = 'pending' "
            "ORDER BY created_at DESC LIMIT 1",
            (user.id,)
        ).fetchone()

    if negotiation is None:
        await update.message.reply_text("❌ Tu n'as aucune offre de négociation en attente.")
        return

    with get_conn() as conn:
        conn.execute(
            "UPDATE company_negotiations SET status = 'refused' WHERE negotiation_id = ?",
            (negotiation["negotiation_id"],)
        )

    await update.message.reply_text("❌ Offre de négociation refusée.")

    company = get_company_by_id(negotiation["company_id"])
    if company is not None:
        target_player = get_or_create_player(user.id, user.username, user.first_name)
        notif = f"❌ *Négociation refusée.*\n\n{target_player['first_name']} a refusé l'offre d'embauche."
        try:
            await context.bot.send_message(chat_id=company["ceo_id"], text=notif, parse_mode="Markdown")
        except Exception:
            pass


async def cederentreprise(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PDG uniquement — cède la propriété de son entreprise à quelqu'un d'autre,
    avec confirmation par boutons avant que ça ne soit définitif."""
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = get_owned_company(user.id)
    if company is None:
        await update.message.reply_text("❌ Tu ne possèdes aucune entreprise.")
        return

    if not context.args:
        await update.message.reply_text("Utilisation : /cederentreprise id_ou_pseudo")
        return

    target_row = get_player_by_name_or_id(context.args[0])
    if target_row is None:
        await update.message.reply_text("❌ Joueur introuvable.")
        return

    target_id = target_row["user_id"]
    target_name = target_row["first_name"] or target_row["username"] or str(target_id)

    if target_id == user.id:
        await update.message.reply_text("❌ Tu ne peux pas te céder ta propre entreprise.")
        return

    if get_owned_company(target_id) is not None:
        await update.message.reply_text("❌ Cette personne possède déjà sa propre entreprise.")
        return

    target_domain = _get_player_domain(target_id)
    if not target_domain or not _has_mba(target_id, target_domain):
        await update.message.reply_text(
            f"❌ {target_name} doit avoir au minimum le diplôme *MBA* (dans son domaine d'études) "
            f"pour pouvoir devenir PDG.",
            parse_mode="Markdown"
        )
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Oui, céder", callback_data=f"cederboite|oui|{company['company_id']}|{target_id}"),
        InlineKeyboardButton("❌ Non, annuler", callback_data=f"cederboite|non|{company['company_id']}|{target_id}"),
    ]])

    await update.message.reply_text(
        f"⚠️ *Céder l'entreprise*\n\n"
        f"Tu es sur le point de céder *{company['name']}* à *{target_name}*.\n"
        f"Il/elle deviendra le/la nouveau/nouvelle PDG. Tu perdras tout contrôle sur "
        f"l'entreprise (dissolution, recrutement, trésorerie, etc.).\n\n"
        f"⚠️ Cette action est irréversible. Confirmer ?",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def handle_cederentreprise_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gère la confirmation ✅/❌ de /cederentreprise."""
    query = update.callback_query

    try:
        _, action, company_id_str, target_id_str = query.data.split("|")
        company_id = int(company_id_str)
        target_id = int(target_id_str)
    except (ValueError, AttributeError):
        await query.answer()
        return

    user = update.effective_user
    company = get_company_by_id(company_id)
    if company is None or company["ceo_id"] != user.id:
        await query.answer("❌ Tu ne possèdes plus cette entreprise (ou action déjà traitée).", show_alert=True)
        return

    if action == "non":
        await query.answer()
        await query.edit_message_text("❌ Cession annulée.")
        return

    if get_owned_company(target_id) is not None:
        await query.answer("❌ Cette personne possède déjà sa propre entreprise.", show_alert=True)
        return

    target_domain = _get_player_domain(target_id)
    if not target_domain or not _has_mba(target_id, target_domain):
        await query.answer(
            "❌ Cette personne doit avoir le diplôme MBA (dans son domaine) pour devenir PDG.",
            show_alert=True
        )
        return

    await query.answer()

    target_player = get_player_by_id(target_id)
    old_player = get_player_by_id(user.id)

    with get_conn() as conn:
        conn.execute("UPDATE companies SET ceo_id = ? WHERE company_id = ?", (target_id, company_id))

    # Les parts de l'ancien PDG passent au nouveau propriétaire (il ne garde
    # pas des parts dans une entreprise qui n'est plus la sienne, et le
    # nouveau PDG doit posséder au moins les parts cédées avec la boîte).
    old_shares = get_user_shares(company_id, user.id)
    if old_shares > 0:
        new_owner_shares = get_user_shares(company_id, target_id)
        set_user_shares(company_id, target_id, new_owner_shares + old_shares)
        set_user_shares(company_id, user.id, 0)

    # Si le nouveau propriétaire travaille déjà ici, on reflète son nouveau
    # statut de PDG dans son rôle. Sinon (il possède juste l'entreprise sans
    # y être employé), on ne touche pas à son emploi actuel.
    if target_player is not None and target_player["company_id"] == company_id:
        update_player(target_id, company_role="PDG")

    # L'ancien PDG, s'il travaillait encore ici, redevient un employé
    # normal (Directeur) plutôt que de perdre son emploi.
    if old_player is not None and old_player["company_id"] == company_id:
        update_player(user.id, company_role="Directeur")

    add_company_log(
        company_id,
        f"{old_player['first_name'] if old_player else 'PDG'} a cédé l'entreprise à "
        f"{target_player['first_name'] if target_player else 'un joueur'}"
    )

    await query.edit_message_text(
        f"✅ *{company['name']}* a été cédée à *{target_player['first_name'] if target_player else target_id}* !",
        parse_mode="Markdown"
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                f"👑 *Tu es maintenant PDG de {company['name']} !*\n\n"
                f"{old_player['first_name'] if old_player else 'Le précédent PDG'} t'a cédé l'entreprise."
            ),
            parse_mode="Markdown",
        )
    except Exception:
        pass


async def nommer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = get_owned_company(user.id)
    if company is None:
        await update.message.reply_text("❌ Réservé au PDG.")
        return

    target = _get_target_user(update, context)
    if target is None or not context.args:
        await update.message.reply_text(
            "Utilisation : /nommer id_ou_pseudo poste (ou en réponse à son message)"
        )
        return

    target_player = get_or_create_player(target.id, target.username, target.first_name)
    if target_player["company_id"] != company["company_id"]:
        await update.message.reply_text("❌ Cette personne ne travaille pas dans ton entreprise.")
        return

    args = _strip_mention_arg(update, context)
    if not args:
        await update.message.reply_text(
            "Utilisation : /nommer id_ou_pseudo poste (ou en réponse à son message)"
        )
        return

    new_role = " ".join(args)
    update_player(target.id, company_role=new_role)
    add_company_log(company["company_id"], f"{target.first_name} promu(e) {new_role}")

    await update.message.reply_text(f"🎉 {target.first_name} a été nommé(e) *{new_role}* !", parse_mode="Markdown")


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


async def licencier(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = _get_manageable_company(user.id, player)
    if company is None:
        await update.message.reply_text("❌ Réservé au PDG ou aux directeurs.")
        return

    target = _get_target_user(update, context)
    if target is None:
        await update.message.reply_text(
            "Utilisation : /licencier id_ou_pseudo (ou en réponse à son message)"
        )
        return

    target_player = get_or_create_player(target.id, target.username, target.first_name)
    if target_player["company_id"] != company["company_id"]:
        await update.message.reply_text("❌ Cette personne ne travaille pas dans ton entreprise.")
        return

    if target_player["company_role"] == "PDG":
        await update.message.reply_text("❌ Tu ne peux pas licencier le PDG.")
        return

    update_player(target.id, company_id=None, company_role=None, salary=0)
    add_company_log(company["company_id"], f"{target.first_name} a été licencié(e)")

    await update.message.reply_text(
    f"🚪 {target.first_name} a été licencié(e)."
)


# ============================================================
# INDEMNITÉ DE DÉPART
# ============================================================

async def payerindemnite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Le PDG verse une indemnité à un employé, basée sur X mois de son salaire.
    Le montant est prélevé sur la trésorerie de l'entreprise.
    Utilisation : réponds au message de l'employé avec /payerindemnite <mois>"""
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = get_owned_company(user.id)
    if company is None:
        await update.message.reply_text("❌ Réservé au PDG.")
        return

    target = _get_target_user(update, context)
    if target is None or not context.args:
        await update.message.reply_text(
            "Utilisation : /payerindemnite id_ou_pseudo nombre_de_mois (ou en réponse à son message)"
        )
        return

    target_player = get_or_create_player(target.id, target.username, target.first_name)
    if target_player["company_id"] != company["company_id"]:
        await update.message.reply_text("❌ Cette personne ne travaille pas dans ton entreprise.")
        return

    args = _strip_mention_arg(update, context)
    if not args:
        await update.message.reply_text(
            "Utilisation : /payerindemnite id_ou_pseudo nombre_de_mois (ou en réponse à son message)"
        )
        return

    try:
        mois = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Le nombre de mois doit être un nombre entier.")
        return

    if mois <= 0:
        await update.message.reply_text("❌ Le nombre de mois doit être supérieur à 0.")
        return

    salaire = target_player["salary"] or 0
    if salaire <= 0:
        await update.message.reply_text(
            "❌ Cet employé n'a pas de salaire défini, impossible de calculer une indemnité."
        )
        return

    montant = salaire * mois

    if not withdraw_from_treasury(company["company_id"], montant):
        await update.message.reply_text(
            f"❌ Trésorerie insuffisante.\n"
            f"Indemnité requise : {fmt_money(montant)}\n"
            f"Disponible : {fmt_money(company['treasury'])}"
        )
        return

    add_balance(target.id, montant)
    add_company_log(
        company["company_id"],
        f"{target.first_name} a reçu une indemnité de {fmt_money(montant)} ({mois} mois de salaire)",
    )

    await update.message.reply_text(
        f"💸 {target.first_name} a reçu une indemnité de *{fmt_money(montant)}*\n"
        f"({mois} mois × {fmt_money(salaire)} de salaire)\n"
        f"🏦 Montant prélevé sur la trésorerie de l'entreprise.",
        parse_mode="Markdown",
    )


# ============================================================
# ANNONCE DE RECRUTEMENT
# ============================================================

RECRUITMENT_AD_COOLDOWN_SECONDS = 7 * 86400  # 1 semaine entre deux annonces de recrutement


async def annoncerecrutement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    company = get_owned_company(user.id)
    if company is None:
        await update.message.reply_text(
            "❌ Seul le *PDG* d'une entreprise peut publier une "
            "annonce de recrutement.",
            parse_mode="Markdown"
        )
        return

    last_ad = company["last_recruitment_ad"] or 0
    remaining = last_ad + RECRUITMENT_AD_COOLDOWN_SECONDS - int(time.time())
    if remaining > 0:
        await update.message.reply_text(
            f"⏳ Tu dois attendre encore *{_format_cooldown(remaining)}* avant de "
            f"publier une nouvelle annonce de recrutement.",
            parse_mode="Markdown"
        )
        return

    employees = get_company_employees(company["company_id"])
    city = company["city"] if "city" in company else "Non renseignée"
    country = company["country"] if "country" in company else ""
    poste = " ".join(context.args) if context.args else "Non précisé"

    annonce = (
        "📢 *ANNONCE DE RECRUTEMENT*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🏢 *Entreprise :* {company['name']}\n"
        f"📁 *Secteur :* {company['sector']}\n"
        f"📍 *Localisation :* {city}, {country}\n"
        f"👑 *PDG :* {user.first_name}\n"
        f"👥 *Employés actuels :* {len(employees)}\n"
        f"💰 *Trésorerie :* {fmt_money(company['treasury'])}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 *Poste recherché :* {poste}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📩 Pour postuler : /postuler {company['name']}"
    )

    ad_id = create_recruitment_ad(player["company_id"], poste, user.id)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Oui, publier", callback_data=f"recrutads|oui|{ad_id}"),
        InlineKeyboardButton("❌ Non, annuler", callback_data=f"recrutads|non|{ad_id}"),
    ]])

    await update.message.reply_text(
        f"{annonce}\n\n"
        f"⚠️ Cette annonce sera envoyée dans *tous les groupes* où le bot est présent. Confirmer ?",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def handle_postuler_ad_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gère le clic sur le bouton 📩 Postuler d'une annonce de recrutement diffusée."""
    query = update.callback_query

    try:
        _, company_id_str = query.data.split("|")
        company_id = int(company_id_str)
    except (ValueError, AttributeError):
        await query.answer()
        return

    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    if player["company_id"] and player["company_role"] != "PDG":
        await query.answer("❌ Tu fais déjà partie d'une entreprise.", show_alert=True)
        return

    cooldown = _cooldown_remaining(player)
    if cooldown > 0:
        await query.answer(
            f"⏳ Tu dois attendre encore {_format_cooldown(cooldown)} avant de pouvoir rejoindre une entreprise.",
            show_alert=True
        )
        return

    company = get_company_by_id(company_id)
    if company is None:
        await query.answer("❌ Cette entreprise n'existe plus.", show_alert=True)
        return

    if not _get_passed_tiers(user.id):
        await query.answer(
            "❌ Tu n'as aucun diplôme. Le Bac minimum (dans n'importe quel secteur) est requis pour postuler.",
            show_alert=True
        )
        return

    await _submit_application(context, user, player, company)
    await query.answer(f"📨 Candidature envoyée à {company['name']} !", show_alert=True)


async def handle_recruitment_ad_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gère la confirmation ✅/❌ avant diffusion d'une annonce de recrutement dans tous les groupes."""
    query = update.callback_query

    try:
        _, action, ad_id_str = query.data.split("|")
        ad_id = int(ad_id_str)
    except (ValueError, AttributeError):
        await query.answer()
        return

    ad = get_recruitment_ad(ad_id)
    if ad is None:
        await query.answer("❌ Cette annonce n'existe plus ou a déjà été traitée.", show_alert=True)
        return

    user = update.effective_user
    if user.id != ad["from_user"]:
        await query.answer("❌ Seul le PDG qui a initié l'annonce peut confirmer.", show_alert=True)
        return

    company = get_company_by_id(ad["company_id"])
    if company is None:
        await query.answer("❌ Cette entreprise n'existe plus.", show_alert=True)
        delete_recruitment_ad(ad_id)
        return

    if action == "non":
        await query.answer()
        delete_recruitment_ad(ad_id)
        await query.edit_message_text("❌ Annonce de recrutement annulée.")
        return

    # Re-vérifie le cooldown au moment de la confirmation (au cas où il aurait publié entre-temps)
    last_ad = company["last_recruitment_ad"] or 0
    remaining = last_ad + RECRUITMENT_AD_COOLDOWN_SECONDS - int(time.time())
    if remaining > 0:
        await query.answer(f"⏳ Cooldown actif : {_format_cooldown(remaining)} restant.", show_alert=True)
        delete_recruitment_ad(ad_id)
        await query.edit_message_text("❌ Annonce annulée : le cooldown d'1 semaine est déjà actif.")
        return

    await query.answer("📢 Diffusion en cours...")

    employees = get_company_employees(ad["company_id"])
    city = company["city"] if "city" in company else "Non renseignée"
    country = company["country"] if "country" in company else ""
    poste = ad["poste"]

    annonce = (
        "📢 *ANNONCE DE RECRUTEMENT*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🏢 *Entreprise :* {company['name']}\n"
        f"📁 *Secteur :* {company['sector']}\n"
        f"📍 *Localisation :* {city}, {country}\n"
        f"👥 *Employés actuels :* {len(employees)}\n"
        f"💰 *Trésorerie :* {fmt_money(company['treasury'])}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 *Poste recherché :* {poste}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📩 Clique ci-dessous pour postuler !"
    )

    ad_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📩 Postuler", callback_data=f"postulerad|{ad['company_id']}"),
    ]])

    groups = get_active_groups()
    sent, failed = 0, 0
    for g in groups:
        try:
            await context.bot.send_message(chat_id=g["chat_id"], text=annonce, parse_mode="Markdown", reply_markup=ad_keyboard)
            sent += 1
        except Exception:
            failed += 1

    set_company_last_recruitment_ad(ad["company_id"], int(time.time()))
    delete_recruitment_ad(ad_id)

    await query.edit_message_text(
        f"✅ Annonce diffusée dans {sent} groupe(s)"
        + (f" ({failed} échec(s))" if failed else "")
        + ".\n⏳ Prochaine annonce possible dans 7 jours."
    )

# ============================================================
# DÉPLACEMENT DE SIÈGE SOCIAL
# ============================================================

async def deplacerboite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    player = get_or_create_player(user.id, user.username, user.first_name)

    owned = get_owned_company(user.id)
    if owned is None:
        await update.message.reply_text("❌ Seul le *PDG* peut déplacer le siège social.", parse_mode="Markdown")
        return

    if not context.args:
        exemples_villes = ", ".join(ALL_CITIES[:15]) + "…"
        await update.message.reply_text(
            f"🏙️ *Déplacement de siège social*\n\n"
            f"Coût : *50 000 000 000€* (50 milliards)\n\n"
            f"Utilisation : `/deplacerboite NouvelleVille`\n\n"
            f"Exemples de villes valides : {exemples_villes}",
            parse_mode="Markdown"
        )
        return

    new_city = " ".join(context.args)

    if new_city not in ALL_CITIES:
        exemples_villes = ", ".join(ALL_CITIES[:15]) + "…"
        await update.message.reply_text(
            f"❌ Ville invalide.\nExemples de villes valides : {exemples_villes}"
        )
        return

    company = get_company_by_id(owned["company_id"])

    current_city = company["city"] if "city" in company.keys() else ""
    if new_city == current_city:
        await update.message.reply_text("❌ Ton siège est déjà dans cette ville !")
        return

    # Vérifier le solde (portefeuille du PDG)
    if player["balance"] < COMPANY_RELOCATION_COST:
        await update.message.reply_text(
            f"❌ Il te faut *50 000 000 000€* pour déplacer ton siège.\n"
            f"Tu as : *{fmt_money(player['balance'])}*",
            parse_mode="Markdown"
        )
        return

    new_country = next(
        (c for c, cities in COMPANY_LOCATIONS.items() if new_city in cities), "🌍 Inconnu"
    )

    add_balance(user.id, -COMPANY_RELOCATION_COST)

    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET city = ?, country = ? WHERE company_id = ?",
            (new_city, new_country, company["company_id"])
        )

    add_company_log(
        company["company_id"],
        f"Siège déplacé de {current_city} vers {new_city} ({new_country})"
    )

    await update.message.reply_text(
        f"🏙️ Siège social déplacé avec succès !\n"
        f"📍 Nouveau siège : *{new_city}*, {new_country}\n"
        f"💸 -*50 000 000 000€* débités.",
        parse_mode="Markdown"
    )