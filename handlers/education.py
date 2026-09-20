"""
LifeCity Bot - Systeme de diplomes / examens sectoriels
/diplome  - Voir sa progression, changer de domaine, passer un examen

Regles :
- 10 secteurs, 4 diplomes par secteur (Bac, Licence, Master, MBA)
- 10 questions QCM par examen, score minimum requis pour valider
- 1 seule tentative par diplome. En cas d'echec, il faut payer 100 000 000 pour retenter
- Chaque diplome obtenu donne un bonus permanent sur /work (lie au secteur actif)

A ajouter dans main.py :
    from handlers.diplome import diplome, diplome_callback
    app.add_handler(CommandHandler("diplome", diplome))
    app.add_handler(CallbackQueryHandler(diplome_callback, pattern="^dip_"))
"""

import random
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from db import get_or_create_player, get_conn, add_balance
from utils import fmt_money


# ─────────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────

SECTORS = {
    "technologie":  {"label": "Technologie",  "emoji": "💻"},
    "finance":      {"label": "Finance",      "emoji": "📈"},
    "immobilier":   {"label": "Immobilier",   "emoji": "🏠"},
    "restauration": {"label": "Restauration", "emoji": "🍽️"},
    "industrie":    {"label": "Industrie",    "emoji": "🏭"},
    "commerce":     {"label": "Commerce",     "emoji": "🛒"},
    "transport":    {"label": "Transport",    "emoji": "🚚"},
    "medias":       {"label": "Médias",       "emoji": "📰"},
    "sante":        {"label": "Santé",        "emoji": "🩺"},
    "energie":      {"label": "Énergie",      "emoji": "⚡"},
}

TIERS = [
    {"key": "bac",     "name": "Bac",     "emoji": "📄", "cost": 0,          "required": 7,  "bonus": 0},
    {"key": "licence", "name": "Licence", "emoji": "🎓", "cost": 100_000,    "required": 8,  "bonus": 25},
    {"key": "master",  "name": "Master",  "emoji": "🏅", "cost": 1_000_000,  "required": 8,  "bonus": 50},
    {"key": "mba",     "name": "MBA",     "emoji": "👑", "cost": 10_000_000, "required": 10, "bonus": 100},
]

RETRY_COST = 2_000_000  # "Frais de repêchage" pour retenter un examen raté immédiatement
RETRY_COOLDOWN_SECONDS = 24 * 3600  # sans payer, il faut attendre 24h pour retenter gratuitement

# Sessions d'examen en cours, en memoire : {user_id: {...}}
EXAM_SESSIONS = {}


# ─────────────────────────────────────────────────────────────
#  BANQUE DE QUESTIONS (10 par secteur)
#  format : (question, [option_A, option_B, option_C, option_D], index_reponse_correcte)
# ─────────────────────────────────────────────────────────────

QUESTIONS = {
    "technologie": [
        ("Que signifie \"CPU\" ?", ["Central Processing Unit", "Computer Personal Unit", "Central Program Utility", "Core Processing Unix"], 0),
        ("Quel langage est surtout utilisé côté client sur le web ?", ["Python", "JavaScript", "C++", "Swift"], 1),
        ("Que signifie \"RAM\" ?", ["Random Access Memory", "Read Access Module", "Rapid Application Model", "Remote Access Memory"], 0),
        ("Quelle entreprise a créé Windows ?", ["Apple", "Google", "Microsoft", "IBM"], 2),
        ("Que signifie l'acronyme \"IA\" ?", ["Interface Automatique", "Intelligence Artificielle", "Internet Avancé", "Information Analytique"], 1),
        ("Quel protocole sécurise les sites web (https) ?", ["FTP", "SSL/TLS", "SMTP", "DNS"], 1),
        ("Quel est le rôle principal d'une base de données ?", ["Afficher des images", "Stocker et organiser des données", "Envoyer des emails", "Compiler du code"], 1),
        ("Que signifie \"API\" ?", ["Application Programming Interface", "Automated Program Index", "Advanced Protocol Integration", "Application Process Identifier"], 0),
        ("Quel type de mémoire perd ses données à l'arrêt ?", ["Disque dur", "SSD", "RAM", "Clé USB"], 2),
        ("Quel langage est très utilisé en data science ?", ["HTML", "Python", "CSS", "SQL uniquement"], 1),
    ],
    "finance": [
        ("Que signifie \"PIB\" ?", ["Produit Intérieur Brut", "Prix Indexé Boursier", "Plan d'Investissement Bancaire", "Profit Immédiat Brut"], 0),
        ("Qu'est-ce qu'une action en bourse ?", ["Une dette", "Une part de propriété d'une entreprise", "Un type de crédit", "Une taxe"], 1),
        ("Que représente un taux d'intérêt ?", ["Le prix de l'argent emprunté", "Le salaire d'un banquier", "Une taxe gouvernementale", "Un type d'assurance"], 0),
        ("Qu'est-ce que l'inflation ?", ["Une baisse générale des prix", "Une hausse générale des prix", "Une stabilité des prix", "Une hausse des salaires uniquement"], 1),
        ("Que signifie \"ROI\" ?", ["Return On Investment", "Rate Of Interest", "Revenue Over Income", "Risk On Investment"], 0),
        ("Qu'est-ce qu'une obligation financière ?", ["Un titre de propriété", "Un titre de dette", "Une devise", "Un impôt"], 1),
        ("Qu'est-ce qu'un dividende ?", ["Une part de bénéfice versée aux actionnaires", "Un type d'impôt", "Un prêt bancaire", "Un frais de gestion"], 0),
        ("Que signifie \"IPO\" ?", ["Introduction en bourse", "Indice de production", "Intérêt payé obligatoire", "Investissement personnel optimal"], 0),
        ("Qu'est-ce que la diversification en investissement ?", ["Tout miser sur une seule action", "Répartir les investissements pour réduire le risque", "Vendre tous ses actifs", "Emprunter davantage"], 1),
        ("Que signifie \"liquidité\" ?", ["La facilité à convertir un actif en argent", "La valeur totale d'une entreprise", "Un type de taxe", "Le taux de change"], 0),
    ],
    "immobilier": [
        ("Qu'est-ce qu'une hypothèque ?", ["Un prêt garanti par un bien immobilier", "Un type d'assurance", "Une taxe foncière", "Un contrat de location"], 0),
        ("Que signifie \"m²\" dans une annonce ?", ["Mètre cube", "Mètre carré", "Mille euros", "Mètre linéaire"], 1),
        ("Quel est le rôle du notaire dans une vente ?", ["Agent commercial", "Officier public qui authentifie la vente", "Banquier", "Architecte"], 1),
        ("Qu'est-ce qu'un loyer ?", ["Le prix d'achat d'un bien", "Le paiement périodique pour occuper un logement", "Une taxe unique", "Un prêt bancaire"], 1),
        ("Qu'est-ce qu'une plus-value immobilière ?", ["Le bénéfice réalisé à la revente d'un bien", "Une taxe foncière", "Un crédit immobilier", "Une caution"], 0),
        ("Qu'est-ce que la taxe foncière ?", ["Un impôt payé par le propriétaire d'un bien", "Un impôt sur le revenu", "Une taxe sur les meubles", "Un frais de notaire"], 0),
        ("Que fait un promoteur immobilier ?", ["Il construit et vend des logements neufs", "Il gère des locations", "Il est banquier", "Il est locataire"], 0),
        ("Que signifie \"SCI\" ?", ["Société Civile Immobilière", "Système de Crédit Immobilier", "Service Client Immobilier", "Société Commerciale Internationale"], 0),
        ("Qu'est-ce qu'une caution locative ?", ["Une somme versée en garantie par le locataire", "Le loyer mensuel", "Une taxe d'habitation", "Un prêt bancaire"], 0),
        ("Qu'est-ce que le marché locatif ?", ["Le marché de la vente de biens", "Le marché de la location de logements", "Le marché boursier", "Le marché du travail"], 1),
    ],
    "restauration": [
        ("Que signifie \"HACCP\" ?", ["Une norme d'hygiène alimentaire", "Un type de menu", "Un logiciel de caisse", "Un diplôme de chef"], 0),
        ("Qu'est-ce que la \"mise en place\" ?", ["Le service des clients", "La préparation des ingrédients avant le service", "Le nettoyage final", "La facturation"], 1),
        ("Que signifie \"à la carte\" ?", ["Un menu fixe", "Des plats choisis individuellement", "Un buffet à volonté", "Un menu enfant"], 1),
        ("Qu'est-ce qu'un sommelier ?", ["Un spécialiste des vins", "Un chef pâtissier", "Un plongeur", "Un serveur uniquement"], 0),
        ("Que signifie \"table d'hôte\" ?", ["Un menu unique à prix fixe", "Une table réservée au chef", "Un buffet froid", "Un menu à la carte"], 0),
        ("Qu'est-ce que le \"food cost\" ?", ["Le coût des ingrédients / prix de vente", "Le salaire des cuisiniers", "Le loyer du restaurant", "La taxe sur la nourriture"], 0),
        ("Que désigne la \"brigade de cuisine\" ?", ["L'équipe organisée des cuisiniers", "Les clients réguliers", "Le service de livraison", "Le personnel de nettoyage"], 0),
        ("Qu'est-ce qu'un amuse-bouche ?", ["Le plat principal", "Une petite bouchée offerte avant le repas", "Le dessert", "La boisson d'accueil"], 1),
        ("Que signifie \"flambé\" ?", ["Cuire à la vapeur", "Faire brûler de l'alcool sur un plat", "Griller au four", "Mariner longtemps"], 1),
        ("Qu'est-ce que la restauration rapide ?", ["Un service gastronomique", "Un service rapide et à faible coût", "Un traiteur haut de gamme", "Un restaurant étoilé"], 1),
    ],
    "industrie": [
        ("Qu'est-ce qu'une chaîne de production ?", ["Une suite d'étapes pour fabriquer un produit", "Un type de contrat", "Un syndicat", "Une taxe industrielle"], 0),
        ("Qu'est-ce que la robotique industrielle ?", ["L'usage de robots pour automatiser la production", "Un logiciel comptable", "Une matière première", "Un syndicat"], 0),
        ("Qu'est-ce qu'une matière première ?", ["Le produit fini", "La ressource de base pour fabriquer un produit", "Le salaire ouvrier", "Un brevet"], 1),
        ("Qu'est-ce que le contrôle qualité ?", ["Vérifier que les produits respectent les normes", "Vendre les produits", "Transporter les marchandises", "Faire la comptabilité"], 0),
        ("Que désigne \"l'usine 4.0\" ?", ["Une usine entièrement automatisée et connectée", "Une petite usine artisanale", "Un entrepôt de stockage", "Une usine fermée"], 0),
        ("Qu'est-ce qu'un brevet industriel ?", ["Un droit exclusif sur une invention", "Un salaire d'ouvrier", "Une taxe sur les machines", "Un type de contrat"], 0),
        ("Que couvre la logistique industrielle ?", ["La gestion des flux de production et transport", "La publicité d'un produit", "Le recrutement", "La comptabilité fiscale"], 0),
        ("Qu'est-ce que la maintenance préventive ?", ["Réparer après une panne", "Entretenir avant qu'une panne survienne", "Remplacer tout chaque année", "Ne rien faire"], 1),
        ("Que mesure la productivité ?", ["La quantité produite par ressource utilisée", "Le nombre d'employés", "Le prix de vente", "La taille de l'usine"], 0),
        ("Qu'est-ce qu'une norme ISO ?", ["Un standard international de qualité", "Un impôt sur l'industrie", "Un syndicat", "Un type de machine"], 0),
    ],
    "commerce": [
        ("Que signifie \"B2B\" ?", ["Business to Business", "Bank to Bank", "Buy to Build", "Brand to Buyer"], 0),
        ("Qu'est-ce que la marge commerciale ?", ["La différence entre prix de vente et prix d'achat", "Le salaire du vendeur", "La taxe sur les ventes", "Le coût du transport"], 0),
        ("Que signifie \"B2C\" ?", ["Business to Consumer", "Bank to Client", "Buy to Collect", "Brand to Company"], 0),
        ("Qu'est-ce qu'un grossiste ?", ["Il vend en grande quantité à des détaillants", "Il vend au détail aux particuliers", "Un transporteur", "Un banquier"], 0),
        ("Qu'est-ce qu'un point de vente ?", ["Le lieu où se fait la vente", "Le siège social", "L'entrepôt", "Le service comptable"], 0),
        ("Qu'est-ce que le e-commerce ?", ["La vente de produits en ligne", "La vente uniquement en magasin", "Un type de taxe", "Un syndicat"], 0),
        ("Que désigne le \"stock\" ?", ["L'ensemble des marchandises disponibles", "Le chiffre d'affaires", "Le bénéfice net", "Le salaire des employés"], 0),
        ("Qu'est-ce qu'un détaillant ?", ["Il vend directement au consommateur final", "Il fabrique le produit", "Il transporte les marchandises", "Un investisseur"], 0),
        ("Que signifie \"chiffre d'affaires\" ?", ["Le total des ventes sur une période", "Le bénéfice net", "Le nombre de clients", "Le salaire du gérant"], 0),
        ("Qu'est-ce que la fidélisation client ?", ["Attirer de nouveaux clients uniquement", "Garder les clients existants", "Baisser les prix uniquement", "Fermer une boutique"], 1),
    ],
    "transport": [
        ("Qu'est-ce que la logistique ?", ["La gestion du flux de marchandises et d'informations", "La vente de billets", "Un type de carburant", "Un syndicat"], 0),
        ("Qu'est-ce qu'un fret ?", ["Une marchandise transportée", "Un billet de train", "Un type de véhicule", "Un péage"], 0),
        ("Que signifie \"transport multimodal\" ?", ["Utiliser plusieurs modes de transport pour un trajet", "Un seul type de véhicule", "Le transport gratuit", "Un transport interdit"], 0),
        ("Qu'est-ce qu'un entrepôt logistique ?", ["Un lieu de stockage des marchandises", "Une gare", "Un aéroport", "Un garage personnel"], 0),
        ("Que désigne la \"chaîne d'approvisionnement\" ?", ["Le processus du fournisseur au client final", "Un type de camion", "Une taxe", "Un syndicat"], 0),
        ("Qu'est-ce qu'un transporteur routier ?", ["Une entreprise qui achemine par la route", "Une compagnie aérienne", "Un armateur", "Un service postal"], 0),
        ("Que désigne le \"délai de livraison\" ?", ["Le temps entre commande et réception", "Le prix du transport", "La distance parcourue", "Le poids du colis"], 0),
        ("Qu'est-ce que le transport maritime ?", ["Le transport de marchandises par bateau", "Le transport par avion", "Le transport par train", "Le transport par camion"], 0),
        ("Que signifie \"traçabilité\" ?", ["Suivre un colis tout au long de son trajet", "Le prix du carburant", "Le type d'emballage", "La vitesse du véhicule"], 0),
        ("Qu'est-ce qu'un hub logistique ?", ["Un point central de tri et redistribution", "Un simple garage", "Une boutique", "Un bureau administratif"], 0),
    ],
    "medias": [
        ("Qu'est-ce que l'audience ?", ["Le nombre de personnes touchées par un contenu", "Le prix d'une publicité", "Le nom d'une chaîne", "Un type de caméra"], 0),
        ("Qu'est-ce qu'un éditorial ?", ["Un article exprimant l'opinion de la rédaction", "Une publicité", "Un flash info", "Un sondage"], 0),
        ("Que signifie \"streaming\" ?", ["Diffusion de contenu en continu via internet", "L'impression d'un journal", "Un type de micro", "Une chaîne de radio"], 0),
        ("Que fait un community manager ?", ["Il gère la présence d'une marque sur les réseaux", "Il fait du journalisme d'investigation", "Il gère le son", "Il imprime des journaux"], 0),
        ("Que signifie \"prime time\" ?", ["La tranche horaire de plus grande audience", "Le début d'une émission", "Un type de publicité", "Le générique de fin"], 0),
        ("Qu'est-ce qu'un scoop ?", ["Une information exclusive révélée en premier", "Un type de caméra", "Un format d'article", "Un logiciel de montage"], 0),
        ("Qu'est-ce qu'un podcast ?", ["Un programme audio diffusé sur internet", "Une émission de télévision uniquement", "Un journal papier", "Une affiche publicitaire"], 0),
        ("Qu'est-ce que la ligne éditoriale ?", ["L'orientation et les valeurs d'un média", "Le budget publicitaire", "Le nombre de journalistes", "Le format du logo"], 0),
        ("Que mesure l'audimat ?", ["L'audience télévisée", "Le prix d'un abonnement", "Un type de caméra", "Le nom d'une chaîne"], 0),
        ("Qu'est-ce qu'un influenceur ?", ["Une personne qui influence opinions/achats via réseaux sociaux", "Un journaliste imprimé", "Un technicien de studio", "Un producteur uniquement"], 0),
    ],
    "sante": [
        ("Qu'est-ce qu'un diagnostic ?", ["L'identification d'une maladie à partir de symptômes", "Un type de médicament", "Une opération chirurgicale", "Un vaccin"], 0),
        ("Qu'est-ce qu'un généraliste ?", ["Un médecin traitant tous types de soins courants", "Un chirurgien spécialisé uniquement", "Un pharmacien", "Un infirmier"], 0),
        ("Qu'est-ce qu'une prescription médicale ?", ["Un document du médecin indiquant un traitement", "Une facture d'hôpital", "Un carnet de vaccination", "Une assurance santé"], 0),
        ("Qu'est-ce que la prévention en santé ?", ["Des actions pour éviter l'apparition de maladies", "Le traitement d'une maladie déjà présente", "Une opération chirurgicale", "Un remboursement"], 0),
        ("Qu'est-ce qu'une urgence médicale ?", ["Une situation nécessitant une intervention rapide", "Un rendez-vous de routine", "Un bilan annuel", "Une consultation à distance"], 0),
        ("Qu'est-ce qu'un vaccin ?", ["Une substance qui immunise contre une maladie", "Un antidouleur", "Un antibiotique", "Un anesthésiant"], 0),
        ("Qu'est-ce que la télémédecine ?", ["Consultation médicale à distance via la technologie", "Un type de chirurgie", "Un médicament générique", "Une assurance maladie"], 0),
        ("Qu'est-ce qu'un kinésithérapeute ?", ["Un professionnel de la rééducation physique", "Un chirurgien", "Un pharmacien", "Un psychiatre"], 0),
        ("Qu'est-ce qu'une épidémie ?", ["La propagation rapide d'une maladie dans une population", "Un vaccin obligatoire", "Un traitement chronique", "Une opération de routine"], 0),
        ("Qu'est-ce qu'un médicament générique ?", ["Une copie moins chère d'un médicament de marque", "Un médicament expérimental", "Un vaccin", "Un placebo"], 0),
    ],
    "energie": [
        ("Qu'est-ce qu'une énergie renouvelable ?", ["Une énergie issue de sources naturelles inépuisables", "Une énergie issue du pétrole", "Une énergie nucléaire uniquement", "Une énergie fossile"], 0),
        ("Qu'est-ce qu'un panneau photovoltaïque ?", ["Un dispositif qui convertit la lumière en électricité", "Un type d'éolienne", "Une batterie", "Un transformateur"], 0),
        ("Que signifie \"kWh\" ?", ["Kilowattheure, une unité d'énergie", "Kilomètre par heure", "Kilogramme par heure", "Un type de batterie"], 0),
        ("Qu'est-ce qu'une éolienne ?", ["Une machine qui convertit le vent en électricité", "Un panneau solaire", "Un réacteur nucléaire", "Un barrage"], 0),
        ("Qu'est-ce que la transition énergétique ?", ["Le passage vers des énergies plus propres", "Une coupure de courant", "Une taxe sur l'électricité", "Un type de moteur"], 0),
        ("Qu'est-ce que l'énergie fossile ?", ["Une énergie issue du pétrole ou du charbon", "L'énergie solaire", "L'énergie éolienne", "L'énergie hydraulique"], 0),
        ("Qu'est-ce qu'un réseau électrique ?", ["L'infrastructure qui distribue l'électricité", "Un type de centrale", "Une batterie géante", "Un panneau solaire"], 0),
        ("Qu'est-ce qu'un barrage hydroélectrique ?", ["Une installation qui produit de l'électricité grâce à l'eau", "Une centrale nucléaire", "Un panneau solaire", "Une éolienne"], 0),
        ("Qu'est-ce que la sobriété énergétique ?", ["Réduire sa consommation d'énergie", "Augmenter sa consommation", "Un type de carburant", "Une taxe carbone"], 0),
        ("Qu'est-ce qu'une centrale nucléaire ?", ["Une installation qui produit de l'électricité par fission", "Un panneau solaire", "Une éolienne", "Un barrage"], 0),
    ],
}


# ─────────────────────────────────────────────────────────────
#  BASE DE DONNÉES
# ─────────────────────────────────────────────────────────────

def _ensure_tables(conn):
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
    dip_cols = {row["name"] for row in conn.execute("PRAGMA table_info(user_diplomas)")}
    if "retry_unlock_at" not in dip_cols:
        conn.execute("ALTER TABLE user_diplomas ADD COLUMN retry_unlock_at INTEGER NOT NULL DEFAULT 0")


def _get_domain(conn, user_id):
    row = conn.execute("SELECT sector FROM user_domain WHERE user_id = ?", (user_id,)).fetchone()
    return row["sector"] if row else None


def _set_domain(conn, user_id, sector):
    conn.execute(
        "INSERT INTO user_domain (user_id, sector) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET sector = excluded.sector",
        (user_id, sector),
    )


def _get_statuses(conn, user_id, sector):
    rows = conn.execute(
        "SELECT tier, status, attempt_used, retry_unlock_at FROM user_diplomas WHERE user_id = ? AND sector = ?",
        (user_id, sector),
    ).fetchall()
    return {r["tier"]: r for r in rows}


def _next_tier(statuses):
    """Renvoie le prochain diplome a tenter (le premier non 'passed')."""
    for tier in TIERS:
        st = statuses.get(tier["key"])
        if not st or st["status"] != "passed":
            return tier
    return None


def get_work_bonus_percent(user_id) -> int:
    """A appeler depuis /work : renvoie le bonus % lie au diplome le plus eleve
    obtenu dans le secteur actuellement actif du joueur."""
    with get_conn() as conn:
        _ensure_tables(conn)
        sector = _get_domain(conn, user_id)
        if not sector:
            return 0
        statuses = _get_statuses(conn, user_id, sector)
        bonus = 0
        for tier in TIERS:
            st = statuses.get(tier["key"])
            if st and st["status"] == "passed":
                bonus = tier["bonus"]
        return bonus


# ─────────────────────────────────────────────────────────────
#  AFFICHAGE
# ─────────────────────────────────────────────────────────────

def _menu_text(sector, statuses):
    info = SECTORS[sector]
    lines = ["🎓 *VOS DIPLÔMES*", "━━━━━━━━━━━━━━━━━━━━━━━━"]
    for tier in TIERS:
        st = statuses.get(tier["key"])
        mark = "✅" if st and st["status"] == "passed" else "⬜"
        cost_label = "Gratuit" if tier["cost"] == 0 else f"{fmt_money(tier['cost'])}"
        lines.append(f"{mark} {tier['emoji']} {tier['name']} — {cost_label}  ({tier['required']}/10 requis)")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📌 Domaine : {info['emoji']} {info['label']}")

    bonus = 0
    for tier in TIERS:
        st = statuses.get(tier["key"])
        if st and st["status"] == "passed":
            bonus = tier["bonus"]
    lines.append(f"\n💰 Bonus /work actif : +{bonus}%")

    if all(statuses.get(t["key"]) and statuses[t["key"]]["status"] == "passed" for t in TIERS):
        lines.append("\n🏆 Tu as tous les diplômes ! Félicitations.")

    return "\n".join(lines)


def _has_passed_any(statuses):
    """True si le joueur a deja valide au moins un diplome dans ce secteur."""
    return any(st and st["status"] == "passed" for st in statuses.values())


def _menu_keyboard(sector, locked):
    rows = [[InlineKeyboardButton("🎓 Passer un examen", callback_data="dip_exam")]]
    if not locked:
        # Aucun diplome valide dans ce secteur -> on peut encore changer de domaine.
        rows.append([InlineKeyboardButton("📊 Changer de domaine", callback_data="dip_domains")])
    return InlineKeyboardMarkup(rows)


def _domains_keyboard():
    rows = []
    items = list(SECTORS.items())
    for i in range(0, len(items), 2):
        row = []
        for key, info in items[i:i + 2]:
            row.append(InlineKeyboardButton(f"{info['emoji']} {info['label']}", callback_data=f"dip_setdom:{key}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("◀ Retour", callback_data="dip_menu")])
    return InlineKeyboardMarkup(rows)


def _question_keyboard(options):
    letters = ["A", "B", "C", "D"]
    rows = [[InlineKeyboardButton(f"{letters[i]}) {opt}", callback_data=f"dip_ans:{i}")] for i, opt in enumerate(options)]
    return InlineKeyboardMarkup(rows)


def _question_text(sector, tier, q_index, total, question):
    info = SECTORS[sector]
    return (
        f"{tier['emoji']} *Examen {tier['name']} — {info['emoji']} {info['label']}*\n"
        f"Question {q_index + 1}/{total}\n\n"
        f"❓ {question}\n\n"
        f"⏱ Tu as 10 secondes pour répondre !"
    )


# ─────────────────────────────────────────────────────────────
#  COMMANDE /diplome
# ─────────────────────────────────────────────────────────────

async def diplome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_or_create_player(user.id, user.username, user.first_name)

    with get_conn() as conn:
        _ensure_tables(conn)
        sector = _get_domain(conn, user.id)

        if not sector:
            await update.message.reply_text(
                "📊 Choisis d'abord un domaine pour commencer :",
                reply_markup=_domains_keyboard(),
            )
            return

        statuses = _get_statuses(conn, user.id, sector)

    await update.message.reply_text(
        _menu_text(sector, statuses),
        reply_markup=_menu_keyboard(sector, _has_passed_any(statuses)),
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────────────────────
#  GESTION DES BOUTONS (callback_data commence par "dip_")
# ─────────────────────────────────────────────────────────────

async def diplome_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = query.from_user
    data = query.data

    get_or_create_player(user.id, user.username, user.first_name)

    # ── Retour au menu principal ──────────────────────────────
    if data == "dip_menu":
        with get_conn() as conn:
            _ensure_tables(conn)
            sector = _get_domain(conn, user.id)
            statuses = _get_statuses(conn, user.id, sector) if sector else {}
        await query.answer()
        await query.edit_message_text(
            _menu_text(sector, statuses),
            reply_markup=_menu_keyboard(sector, _has_passed_any(statuses)),
            parse_mode="Markdown",
        )
        return

    # ── Liste des domaines ────────────────────────────────────
    if data == "dip_domains":
        with get_conn() as conn:
            _ensure_tables(conn)
            existing = _get_domain(conn, user.id)
            statuses = _get_statuses(conn, user.id, existing) if existing else {}
        if existing and _has_passed_any(statuses):
            # Un diplome est deja valide dans ce secteur : domaine definitif.
            await query.answer("Tu as déjà un diplôme dans ce domaine, tu ne peux plus en changer.", show_alert=True)
            return
        await query.answer()
        await query.edit_message_text(
            "📊 Choisis ton domaine :",
            reply_markup=_domains_keyboard(),
        )
        return

    # ── Choix d'un domaine ────────────────────────────────────
    if data.startswith("dip_setdom:"):
        sector = data.split(":", 1)[1]
        with get_conn() as conn:
            _ensure_tables(conn)
            existing = _get_domain(conn, user.id)
            existing_statuses = _get_statuses(conn, user.id, existing) if existing else {}
            if existing and _has_passed_any(existing_statuses):
                # Deja un diplome valide dans le domaine actuel : on refuse tout changement.
                await query.answer("Tu as déjà un diplôme dans ce domaine, tu ne peux plus en changer.", show_alert=True)
                return
            _set_domain(conn, user.id, sector)
            statuses = _get_statuses(conn, user.id, sector)
        await query.answer(f"Domaine choisi : {SECTORS[sector]['label']}")
        await query.edit_message_text(
            _menu_text(sector, statuses),
            reply_markup=_menu_keyboard(sector, _has_passed_any(statuses)),
            parse_mode="Markdown",
        )
        return

    # ── Lancer un examen ──────────────────────────────────────
    if data == "dip_exam":
        with get_conn() as conn:
            _ensure_tables(conn)
            sector = _get_domain(conn, user.id)
            if not sector:
                await query.answer("Choisis d'abord un domaine.", show_alert=True)
                return
            statuses = _get_statuses(conn, user.id, sector)
            tier = _next_tier(statuses)

            if tier is None:
                await query.answer("Tu as déjà tous les diplômes de ce secteur !", show_alert=True)
                return

            st = statuses.get(tier["key"])

            # Deja tente et rate : gratuit si le cooldown de 24h est passé, sinon amende
            if st and st["status"] == "failed" and st["attempt_used"] == 1:
                now = int(time.time())
                if now >= (st["retry_unlock_at"] or 0):
                    # Cooldown terminé : retentative gratuite (le coût du palier a déjà
                    # été payé au premier essai, on ne le refacture pas).
                    conn.execute(
                        "UPDATE user_diplomas SET attempt_used = 0 WHERE user_id = ? AND sector = ? AND tier = ?",
                        (user.id, sector, tier["key"]),
                    )
                    _start_exam(user.id, sector, tier)
                    await query.answer("✅ Retentative gratuite (24h écoulées) !")
                    await _send_current_question(context, query.message.chat_id, query.message.message_id, user.id)
                    return
                else:
                    remaining = (st["retry_unlock_at"] or 0) - now
                    hours = remaining // 3600
                    minutes = (remaining % 3600) // 60
                    await query.answer()
                    await query.edit_message_text(
                        f"❌ Tu as déjà échoué à l'examen *{tier['name']}*.\n\n"
                        f"⏳ Retentative gratuite dans : {hours}h{minutes:02d}\n"
                        f"💸 Ou paie des *frais de repêchage* pour retenter immédiatement : {fmt_money(RETRY_COST)}",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton(f"💳 Payer {fmt_money(RETRY_COST)} (frais de repêchage)", callback_data=f"dip_retrypay:{tier['key']}")],
                            [InlineKeyboardButton("◀ Retour", callback_data="dip_menu")],
                        ]),
                        parse_mode="Markdown",
                    )
                    return

            player = get_or_create_player(user.id, user.username, user.first_name)
            if player["balance"] < tier["cost"]:
                await query.answer(f"❌ Solde insuffisant. Il te faut {fmt_money(tier['cost'])}.", show_alert=True)
                return

            if tier["cost"] > 0:
                add_balance(user.id, -tier["cost"])

            conn.execute(
                "INSERT INTO user_diplomas (user_id, sector, tier, status, attempt_used) "
                "VALUES (?, ?, ?, 'failed', 0) "
                "ON CONFLICT(user_id, sector, tier) DO NOTHING",
                (user.id, sector, tier["key"]),
            )

        _start_exam(user.id, sector, tier)
        await query.answer()
        await _send_current_question(context, query.message.chat_id, query.message.message_id, user.id)
        return

    # ── Payer pour retenter ───────────────────────────────────
    if data.startswith("dip_retrypay:"):
        tier_key = data.split(":", 1)[1]
        tier = next(t for t in TIERS if t["key"] == tier_key)

        with get_conn() as conn:
            _ensure_tables(conn)
            sector = _get_domain(conn, user.id)
            player = get_or_create_player(user.id, user.username, user.first_name)

            if player["balance"] < RETRY_COST:
                await query.answer(f"❌ Solde insuffisant. Il te faut {fmt_money(RETRY_COST)}.", show_alert=True)
                return

            add_balance(user.id, -RETRY_COST)
            conn.execute(
                "UPDATE user_diplomas SET attempt_used = 0 WHERE user_id = ? AND sector = ? AND tier = ?",
                (user.id, sector, tier_key),
            )

        _start_exam(user.id, sector, tier)
        await query.answer("✅ Nouvelle tentative payée !")
        await _send_current_question(context, query.message.chat_id, query.message.message_id, user.id)
        return

    # ── Répondre à une question ───────────────────────────────
    if data.startswith("dip_ans:"):
        session = EXAM_SESSIONS.get(user.id)
        if not session:
            await query.answer("Cet examen n'est plus actif.", show_alert=True)
            return

        _cancel_timeout(session)

        chosen = int(data.split(":", 1)[1])
        _, options, correct = session["questions"][session["index"]]

        if chosen == correct:
            session["score"] += 1
            await query.answer("✅ Bonne réponse !")
        else:
            await query.answer("❌ Mauvaise réponse.")

        session["index"] += 1
        chat_id = query.message.chat_id
        message_id = query.message.message_id

        if session["index"] < len(session["questions"]):
            await _send_current_question(context, chat_id, message_id, user.id)
        else:
            await _finish_exam(context, chat_id, message_id, user.id)
        return


# ─────────────────────────────────────────────────────────────
#  LOGIQUE D'EXAMEN
# ─────────────────────────────────────────────────────────────

QUESTION_TIMEOUT_SECONDS = 20


def _start_exam(user_id, sector, tier):
    pool = QUESTIONS[sector][:]
    random.shuffle(pool)
    questions = pool[:10]
    EXAM_SESSIONS[user_id] = {
        "sector": sector,
        "tier": tier,
        "questions": questions,
        "index": 0,
        "score": 0,
        "job": None,
    }


def _cancel_timeout(session):
    """Annule le job de timeout en cours pour cette session, s'il existe."""
    job = session.get("job")
    if job is not None:
        try:
            job.schedule_removal()
        except Exception:
            pass
        session["job"] = None


async def _send_current_question(context: ContextTypes.DEFAULT_TYPE, chat_id, message_id, user_id):
    session = EXAM_SESSIONS.get(user_id)
    if not session:
        return

    _cancel_timeout(session)

    question, options, _ = session["questions"][session["index"]]
    text = _question_text(session["sector"], session["tier"], session["index"], len(session["questions"]), question)

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        reply_markup=_question_keyboard(options),
        parse_mode="Markdown",
    )

    job = context.job_queue.run_once(
        _handle_timeout,
        QUESTION_TIMEOUT_SECONDS,
        data={"user_id": user_id, "chat_id": chat_id, "message_id": message_id},
        name=f"dip_timeout_{user_id}",
    )
    session["job"] = job


async def _handle_timeout(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Appelé automatiquement si le joueur n'a pas répondu à temps : question comptée ratée."""
    job_data = context.job.data
    user_id = job_data["user_id"]
    chat_id = job_data["chat_id"]
    message_id = job_data["message_id"]

    session = EXAM_SESSIONS.get(user_id)
    if not session:
        return

    session["job"] = None
    session["index"] += 1

    if session["index"] < len(session["questions"]):
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="⏱ *Temps écoulé !* Question suivante...",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        await _send_current_question(context, chat_id, message_id, user_id)
    else:
        await _finish_exam(context, chat_id, message_id, user_id)


async def _finish_exam(context: ContextTypes.DEFAULT_TYPE, chat_id, message_id, user_id) -> None:
    session = EXAM_SESSIONS.pop(user_id, None)
    if not session:
        return

    _cancel_timeout(session)

    sector = session["sector"]
    tier = session["tier"]
    score = session["score"]
    total = len(session["questions"])
    passed = score >= tier["required"]

    with get_conn() as conn:
        _ensure_tables(conn)
        status = "passed" if passed else "failed"
        retry_unlock_at = int(time.time()) + RETRY_COOLDOWN_SECONDS if not passed else 0
        conn.execute(
            "UPDATE user_diplomas SET status = ?, attempt_used = 1, retry_unlock_at = ? "
            "WHERE user_id = ? AND sector = ? AND tier = ?",
            (status, retry_unlock_at, user_id, sector, tier["key"]),
        )

    info = SECTORS[sector]

    if passed:
        text = (
            "🎉 *FÉLICITATIONS !*\n\n"
            f"✅ Diplôme obtenu : {tier['emoji']} {tier['name']}  ·  {info['label']}\n"
            f"📊 Score : {score}/{total}\n\n"
            f"💰 Bonus /work permanent : +{tier['bonus']}%\n"
            f"🏆 Badge visible sur ton /me !\n\n"
            f"Tape /diplome pour voir ta progression."
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("◀ Retour au menu", callback_data="dip_menu")]])
    else:
        text = (
            "😔 *EXAMEN ÉCHOUÉ*\n\n"
            f"❌ Diplôme non obtenu : {tier['emoji']} {tier['name']}  ·  {info['label']}\n"
            f"📊 Score : {score}/{total}  (minimum requis : {tier['required']}/10)\n\n"
            f"⏳ Retentative gratuite dans 24h\n"
            f"💸 Ou frais de repêchage pour retenter maintenant : {fmt_money(RETRY_COST)}"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"💳 Payer {fmt_money(RETRY_COST)} (frais de repêchage)", callback_data=f"dip_retrypay:{tier['key']}")],
            [InlineKeyboardButton("◀ Retour au menu", callback_data="dip_menu")],
        ])

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        reply_markup=keyboard,
        parse_mode="Markdown",
    )