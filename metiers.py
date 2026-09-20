"""
LifeCity Bot - Métiers cosmétiques
Liste fixe de métiers que le joueur peut choisir pour affichage sur son
profil (/me). Purement cosmétique : aucun lien avec le système
d'entreprise/emploi existant (company_id, company_role, salary...).
"""

# Cooldown entre deux changements de métier (7 jours, en secondes)
METIER_COOLDOWN_SECONDS = 7 * 24 * 60 * 60

# Organisation par catégories : {clé_categorie: (label_categorie, emoji_categorie, [métiers])}
# Chaque métier est un tuple (clé_unique, label_affiché_avec_emoji)
METIER_CATEGORIES = {
    "justice": ("Justice & Droit", "⚖️", [
        ("avocat", "⚖️ Avocat"),
        ("juge", "👨‍⚖️ Juge"),
        ("procureur", "📜 Procureur"),
        ("notaire", "🖋️ Notaire"),
        ("huissier", "🔨 Huissier"),
    ]),
    "science": ("Sciences", "🔬", [
        ("scientifique", "🔬 Scientifique"),
        ("astronaute", "🚀 Astronaute"),
        ("astrophysicien", "🌌 Astrophysicien"),
        ("chimiste", "⚗️ Chimiste"),
        ("biologiste", "🧬 Biologiste"),
        ("ingenieur", "⚙️ Ingénieur"),
        ("informaticien", "💻 Informaticien"),
    ]),
    "sante": ("Santé", "🩺", [
        ("medecin", "🩺 Médecin"),
        ("chirurgien", "🔪 Chirurgien"),
        ("psychologue", "🧠 Psychologue"),
        ("psychiatre", "🧠 Psychiatre"),
        ("infirmier", "💉 Infirmier"),
        ("dentiste", "🦷 Dentiste"),
        ("pharmacien", "💊 Pharmacien"),
        ("veterinaire", "🐾 Vétérinaire"),
    ]),
    "art": ("Art & Culture", "🎨", [
        ("artiste_peintre", "🎨 Artiste peintre"),
        ("musicien", "🎵 Musicien"),
        ("acteur", "🎭 Acteur"),
        ("realisateur", "🎬 Réalisateur"),
        ("ecrivain", "✍️ Écrivain"),
        ("photographe", "📷 Photographe"),
        ("architecte", "🏛️ Architecte"),
    ]),
    "business": ("Business & Finance", "💼", [
        ("entrepreneur", "💼 Entrepreneur"),
        ("banquier", "🏦 Banquier"),
        ("comptable", "🧮 Comptable"),
        ("trader", "📈 Trader"),
        ("consultant", "📊 Consultant"),
    ]),
    "service": ("Services & Terrain", "🛠️", [
        ("policier", "👮 Policier"),
        ("pompier", "🚒 Pompier"),
        ("militaire", "🎖️ Militaire"),
        ("enseignant", "📚 Enseignant"),
        ("chef_cuisinier", "👨‍🍳 Chef cuisinier"),
        ("pilote", "✈️ Pilote"),
        ("mecanicien", "🔧 Mécanicien"),
        ("agriculteur", "🌾 Agriculteur"),
        ("journaliste", "📰 Journaliste"),
    ]),
}

# Index rapide clé -> label, construit une fois au chargement du module
METIER_LABELS: dict[str, str] = {
    key: label
    for _, _, metiers in METIER_CATEGORIES.values()
    for key, label in metiers
}


def get_metier_label(key: str | None) -> str | None:
    """Renvoie le label affichable (avec emoji) pour une clé de métier, ou None."""
    if not key:
        return None
    return METIER_LABELS.get(key)
