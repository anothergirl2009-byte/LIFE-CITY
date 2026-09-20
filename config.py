import os
# ============================================================
# ADMIN / OWNER
# ============================================================
OWNER_ID = 8473872336
BOT_TOKEN = os.environ["8812109866:AAGmDp6MNpGXmH5IUcekoNfhvzv2VfT5yz4"]
DB_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "lifecity.db")
CURRENCY = "€"
CURRENCY_NAME = "LifeCity Coins"
STARTING_BALANCE = 10_000

DAILY_MIN = 5_000
DAILY_MAX = 20_000
DAILY_COOLDOWN_HOURS = 24

WORK_MIN = 3_000
WORK_MAX = 30_000
WORK_COOLDOWN_HOURS = 8

# ============================================================
# BANQUE
# ============================================================
# ==========================================================
#  Config des banques 
# ==========================================================
BANK_DEPOSIT_CAP_GLOBAL = 900_000_000_000_000  # plafond de dépôt par défaut
BANK_LOAN_MAX_GLOBAL = 5_000_000              # prêt max par défaut

BANKS = [
    {
        "name": "Death",
        "emoji": "🥇",
        "rank": 1,
        "tagline": "Banque populaire, accessible à tous",
        "min_deposit": 2_000_000,
        "max_deposit": 900_000_000_000_000,
        "interest_rate": 0.050,   # +3.0% / 6h
        "interest_hours": 6,
        "loan_max": 5_000_000,
        "loan_rate": 0.08,        # 8%
    },
    {
        "name": "Nova",
        "emoji": "🥈",
        "rank": 2,
        "tagline": "Pour les épargnants sérieux",
        "min_deposit": 1_000_000,
        "max_deposit": 300_000_000_000_000,
        "interest_rate": 0.025,   # +2.5% / 6h
        "interest_hours": 6,
        "loan_max": 5_000_000,
        "loan_rate": 0.06,        # 6%
    },
    {
        "name": "crystal",
        "emoji": "🥉",
        "rank": 3,
        "tagline": "Banque des investisseurs fortunés",
        "min_deposit": 2_500_000,
        "max_deposit": 50_000_000_000_000,
        "interest_rate": 0.020,   # +2.0% / 6h
        "interest_hours": 6,
        "loan_max": 2_000_000,
        "loan_rate": 0.05,        # 5%
    },
    {
        "name": "Smile",
        "emoji": "🙃",
        "rank": 4,
        "tagline": "Réservée aux élites financières",
        "min_deposit": 500_000,
        "max_deposit": 10_000_000_000_000,
        "interest_rate": 0.015,   # +1.5% / 6h
        "interest_hours": 6,
        "loan_max": 50_000,
        "loan_rate": 0.04,        # 4%
    },
    {
        "name": "Life",
        "emoji": "💎",
        "rank": 5,
        "tagline": "La banque des milliardaires",
        "min_deposit": 100_000,
        "max_deposit": 2_000_000_000_000,
        "interest_rate": 0.010,   # +1.0% / 6h
        "interest_hours": 6,
        "loan_max": 20_000,
        "loan_rate": 0.03,        # 3%
    },
]
# ============================================================
# DIPLÔMES
# ============================================================
DIPLOMAS = [
    {"name": "Bac", "cost": 50_000, "duration_minutes": 10, "requires": None},
    {"name": "Licence", "cost": 250_000, "duration_minutes": 30, "requires": "Bac"},
    {"name": "Master", "cost": 1_000_000, "duration_minutes": 90, "requires": "Licence"},
    {"name": "MBA", "cost": 5_000_000, "duration_minutes": 180, "requires": "Master"},
]

# ============================================================
# ENTREPRISES
# ============================================================
COMPANY_CREATION_COST = 50_000_000
COMPANY_RELOCATION_COST = 50_000_000_000  # 50 milliards pour changer d'emplacement
COMPANY_SECTORS = [
    "Technologie", "Finance", "Immobilier", "Restauration",
    "Industrie", "Commerce", "Transport", "Médias", "Santé", "Énergie"
]

# Localisations disponibles pour les entreprises : pays → liste de villes
# Couvre les 192 pays membres + observateurs de l'ONU
COMPANY_LOCATIONS = {
    "🇫🇷 France": ["Paris", "Lyon", "Marseille", "Toulouse", "Bordeaux", "Nice", "Nantes", "Strasbourg", "Lille", "Montpellier"],
    "🇺🇸 États-Unis": ["New York", "Los Angeles", "Chicago", "Miami", "Las Vegas", "San Francisco", "Houston", "Boston"],
    "🇦🇪 Émirats Arabes Unis": ["Dubaï", "Abu Dhabi", "Sharjah"],
    "🇬🇧 Royaume-Uni": ["Londres", "Manchester", "Birmingham", "Édimbourg"],
    "🇯🇵 Japon": ["Tokyo", "Osaka", "Kyoto", "Yokohama"],
    "🇧🇷 Brésil": ["São Paulo", "Rio de Janeiro", "Brasília"],
    "🇸🇬 Singapour": ["Singapour"],
    "🇨🇭 Suisse": ["Genève", "Zurich", "Bâle"],
    "🇨🇦 Canada": ["Toronto", "Montréal", "Vancouver"],
    "🇩🇪 Allemagne": ["Berlin", "Munich", "Hambourg", "Francfort"],
    "🇮🇹 Italie": ["Rome", "Milan", "Naples", "Florence"],
    "🇪🇸 Espagne": ["Madrid", "Barcelone", "Séville", "Valence"],
    "🇨🇳 Chine": ["Shanghai", "Pékin", "Shenzhen", "Hong Kong"],
    "🇲🇦 Maroc": ["Casablanca", "Rabat", "Marrakech", "Tanger"],
    "🇨🇲 Cameroun": ["Yaoundé", "Douala", "Bafoussam", "Garoua", "Buea"],
    "🇨🇮 Côte d'Ivoire": ["Abidjan", "Yamoussoukro", "Bouaké", "San-Pédro"],
    "🇸🇳 Sénégal": ["Dakar", "Thiès", "Saint-Louis", "Ziguinchor"],
    "🇨🇬 Congo": ["Brazzaville", "Pointe-Noire", "Dolisie"],
    "🇨🇩 RDC": ["Kinshasa", "Lubumbashi", "Goma", "Bukavu"],
    "🇬🇦 Gabon": ["Libreville", "Port-Gentil", "Franceville"],
    "🇳🇬 Nigeria": ["Lagos", "Abuja", "Kano", "Ibadan", "Port Harcourt"],
    "🇬🇭 Ghana": ["Accra", "Kumasi", "Tamale"],
    "🇪🇹 Éthiopie": ["Addis-Abeba", "Dire Dawa", "Mekele"],
    "🇰🇪 Kenya": ["Nairobi", "Mombasa", "Kisumu"],
    "🇿🇦 Afrique du Sud": ["Johannesburg", "Le Cap", "Durban", "Pretoria"],
    "🇹🇳 Tunisie": ["Tunis", "Sfax", "Sousse"],
    "🇩🇿 Algérie": ["Alger", "Oran", "Constantine"],
    "🇦🇫 Afghanistan": ["Kaboul"],
    "🇦🇱 Albanie": ["Tirana"],
    "🇦🇩 Andorre": ["Andorre-la-Vieille"],
    "🇦🇴 Angola": ["Luanda"],
    "🇦🇬 Antigua-et-Barbuda": ["Saint-John's"],
    "🇸🇦 Arabie Saoudite": ["Riyad"],
    "🇦🇷 Argentine": ["Buenos Aires"],
    "🇦🇲 Arménie": ["Erevan"],
    "🇦🇺 Australie": ["Canberra"],
    "🇦🇹 Autriche": ["Vienne"],
    "🇦🇿 Azerbaïdjan": ["Bakou"],
    "🇧🇸 Bahamas": ["Nassau"],
    "🇧🇭 Bahreïn": ["Manama"],
    "🇧🇩 Bangladesh": ["Dacca"],
    "🇧🇧 Barbade": ["Bridgetown"],
    "🇧🇪 Belgique": ["Bruxelles"],
    "🇧🇿 Belize": ["Belmopan"],
    "🇧🇹 Bhoutan": ["Thimphou"],
    "🇲🇲 Birmanie": ["Naypyidaw"],
    "🇧🇾 Biélorussie": ["Minsk"],
    "🇧🇴 Bolivie": ["Sucre"],
    "🇧🇦 Bosnie-Herzégovine": ["Sarajevo"],
    "🇧🇼 Botswana": ["Gaborone"],
    "🇧🇳 Brunei": ["Bandar Seri Begawan"],
    "🇧🇬 Bulgarie": ["Sofia"],
    "🇧🇫 Burkina Faso": ["Ouagadougou"],
    "🇧🇮 Burundi": ["Gitega"],
    "🇧🇯 Bénin": ["Porto-Novo"],
    "🇰🇭 Cambodge": ["Phnom Penh"],
    "🇨🇻 Cap-Vert": ["Praia"],
    "🇨🇱 Chili": ["Santiago"],
    "🇨🇾 Chypre": ["Nicosie"],
    "🇨🇴 Colombie": ["Bogotá"],
    "🇰🇲 Comores": ["Moroni"],
    "🇰🇵 Corée du Nord": ["Pyongyang"],
    "🇰🇷 Corée du Sud": ["Séoul"],
    "🇨🇷 Costa Rica": ["San José"],
    "🇭🇷 Croatie": ["Zagreb"],
    "🇨🇺 Cuba": ["La Havane"],
    "🇩🇰 Danemark": ["Copenhague"],
    "🇩🇯 Djibouti": ["Djibouti"],
    "🇩🇲 Dominique": ["Roseau"],
    "🇪🇪 Estonie": ["Tallinn"],
    "🇸🇿 Eswatini": ["Mbabane"],
    "🇫🇯 Fidji": ["Suva"],
    "🇫🇮 Finlande": ["Helsinki"],
    "🇬🇲 Gambie": ["Banjul"],
    "🇬🇩 Grenade": ["Saint-Georges"],
    "🇬🇷 Grèce": ["Athènes"],
    "🇬🇹 Guatemala": ["Guatemala"],
    "🇬🇳 Guinée": ["Conakry"],
    "🇬🇶 Guinée Équatoriale": ["Malabo"],
    "🇬🇼 Guinée-Bissau": ["Bissau"],
    "🇬🇾 Guyana": ["Georgetown"],
    "🇬🇪 Géorgie": ["Tbilissi"],
    "🇭🇹 Haïti": ["Port-au-Prince"],
    "🇭🇳 Honduras": ["Tegucigalpa"],
    "🇭🇺 Hongrie": ["Budapest"],
    "🇮🇳 Inde": ["New Delhi"],
    "🇮🇩 Indonésie": ["Jakarta"],
    "🇮🇶 Irak": ["Bagdad"],
    "🇮🇷 Iran": ["Téhéran"],
    "🇮🇪 Irlande": ["Dublin"],
    "🇮🇸 Islande": ["Reykjavik"],
    "🇮🇱 Israël": ["Jérusalem"],
    "🇯🇲 Jamaïque": ["Kingston"],
    "🇯🇴 Jordanie": ["Amman"],
    "🇰🇿 Kazakhstan": ["Astana"],
    "🇰🇬 Kirghizistan": ["Bichkek"],
    "🇰🇮 Kiribati": ["Tarawa"],
    "🇰🇼 Koweït": ["Koweït"],
    "🇱🇦 Laos": ["Vientiane"],
    "🇱🇸 Lesotho": ["Maseru"],
    "🇱🇻 Lettonie": ["Riga"],
    "🇱🇧 Liban": ["Beyrouth"],
    "🇱🇷 Liberia": ["Monrovia"],
    "🇱🇾 Libye": ["Tripoli"],
    "🇱🇮 Liechtenstein": ["Vaduz"],
    "🇱🇹 Lituanie": ["Vilnius"],
    "🇱🇺 Luxembourg": ["Luxembourg"],
    "🇲🇰 Macédoine du Nord": ["Skopje"],
    "🇲🇬 Madagascar": ["Antananarivo"],
    "🇲🇾 Malaisie": ["Kuala Lumpur"],
    "🇲🇼 Malawi": ["Lilongwe"],
    "🇲🇻 Maldives": ["Malé"],
    "🇲🇱 Mali": ["Bamako"],
    "🇲🇹 Malte": ["La Valette"],
    "🇲🇭 Marshall": ["Majuro"],
    "🇲🇺 Maurice": ["Port-Louis"],
    "🇲🇷 Mauritanie": ["Nouakchott"],
    "🇲🇽 Mexique": ["Mexico"],
    "🇫🇲 Micronésie": ["Palikir"],
    "🇲🇩 Moldavie": ["Chișinău"],
    "🇲🇨 Monaco": ["Monaco"],
    "🇲🇳 Mongolie": ["Oulan-Bator"],
    "🇲🇪 Monténégro": ["Podgorica"],
    "🇲🇿 Mozambique": ["Maputo"],
    "🇳🇦 Namibie": ["Windhoek"],
    "🇳🇷 Nauru": ["Yaren"],
    "🇳🇮 Nicaragua": ["Managua"],
    "🇳🇪 Niger": ["Niamey"],
    "🇳🇴 Norvège": ["Oslo"],
    "🇳🇿 Nouvelle-Zélande": ["Wellington"],
    "🇳🇵 Népal": ["Katmandou"],
    "🇴🇲 Oman": ["Mascate"],
    "🇺🇬 Ouganda": ["Kampala"],
    "🇺🇿 Ouzbékistan": ["Tachkent"],
    "🇵🇰 Pakistan": ["Islamabad"],
    "🇵🇼 Palaos": ["Ngerulmud"],
    "🇵🇦 Panama": ["Panama"],
    "🇵🇬 Papouasie-Nouvelle-Guinée": ["Port Moresby"],
    "🇵🇾 Paraguay": ["Asunción"],
    "🇳🇱 Pays-Bas": ["Amsterdam"],
    "🇵🇭 Philippines": ["Manille"],
    "🇵🇱 Pologne": ["Varsovie"],
    "🇵🇹 Portugal": ["Lisbonne"],
    "🇵🇪 Pérou": ["Lima"],
    "🇶🇦 Qatar": ["Doha"],
    "🇷🇴 Roumanie": ["Bucarest"],
    "🇷🇺 Russie": ["Moscou"],
    "🇷🇼 Rwanda": ["Kigali"],
    "🇰🇳 Saint-Kitts-et-Nevis": ["Basseterre"],
    "🇸🇲 Saint-Marin": ["Saint-Marin"],
    "🇻🇨 Saint-Vincent-et-les-Grenadines": ["Kingstown"],
    "🇱🇨 Sainte-Lucie": ["Castries"],
    "🇸🇧 Salomon": ["Honiara"],
    "🇸🇻 Salvador": ["San Salvador"],
    "🇼🇸 Samoa": ["Apia"],
    "🇸🇹 Sao Tomé-et-Principe": ["São Tomé"],
    "🇷🇸 Serbie": ["Belgrade"],
    "🇸🇨 Seychelles": ["Victoria"],
    "🇸🇱 Sierra Leone": ["Freetown"],
    "🇸🇰 Slovaquie": ["Bratislava"],
    "🇸🇮 Slovénie": ["Ljubljana"],
    "🇸🇴 Somalie": ["Mogadiscio"],
    "🇸🇩 Soudan": ["Khartoum"],
    "🇸🇸 Soudan du Sud": ["Djouba"],
    "🇱🇰 Sri Lanka": ["Colombo"],
    "🇸🇷 Suriname": ["Paramaribo"],
    "🇸🇪 Suède": ["Stockholm"],
    "🇸🇾 Syrie": ["Damas"],
    "🇹🇯 Tadjikistan": ["Douchanbé"],
    "🇹🇿 Tanzanie": ["Dodoma"],
    "🇹🇩 Tchad": ["N'Djaména"],
    "🇨🇿 Tchéquie": ["Prague"],
    "🇹🇭 Thaïlande": ["Bangkok"],
    "🇹🇱 Timor Oriental": ["Dili"],
    "🇹🇬 Togo": ["Lomé"],
    "🇹🇴 Tonga": ["Nuku'alofa"],
    "🇹🇹 Trinité-et-Tobago": ["Port-d'Espagne"],
    "🇹🇲 Turkménistan": ["Achgabat"],
    "🇹🇷 Turquie": ["Ankara"],
    "🇹🇻 Tuvalu": ["Funafuti"],
    "🇺🇦 Ukraine": ["Kiev"],
    "🇺🇾 Uruguay": ["Montevideo"],
    "🇻🇺 Vanuatu": ["Port-Vila"],
    "🇻🇦 Vatican": ["Vatican"],
    "🇻🇪 Venezuela": ["Caracas"],
    "🇻🇳 Vietnam": ["Hanoï"],
    "🇾🇪 Yémen": ["Sanaa"],
    "🇿🇲 Zambie": ["Lusaka"],
    "🇿🇼 Zimbabwe": ["Harare"],
    "🇪🇬 Égypte": ["Le Caire"],
    "🇪🇨 Équateur": ["Quito"],
    "🇪🇷 Érythrée": ["Asmara"],
}

# Liste à plat pour validation
ALL_CITIES = [city for cities in COMPANY_LOCATIONS.values() for city in cities]
ALL_COUNTRIES = list(COMPANY_LOCATIONS.keys())

# Nationalités disponibles pour les joueurs (/setnationalite) — les mêmes
# 192 pays que pour les entreprises, dans le même format "🇫🇷 France"
NATIONALITIES = list(COMPANY_LOCATIONS.keys())

# ============================================================
# GENRE / ÉTAT CIVIL (profil joueur)
# ============================================================
GENDERS = ["🧑 Homme", "👩 Femme", "🧑‍🦱 Non-binaire"]
RELATIONSHIP_MODES = ["❤️ Monogame", "💞 Polygame"]

# ============================================================
# CASINO / JEUX
# ============================================================
APPLE_OF_FORTUNE_MAX_WIN = 10_000_000_000_000
ROUE_FORTUNE_MAX_WIN = 100_000_000_000

MIN_BET = 1000
MAX_BET = 50_000_000_000

SLOTS_SYMBOLS = ["🍒", "🍋", "🍇", "🔔", "⭐", "💎"]
SLOTS_PAYOUTS = {
    "💎💎💎": 50,
    "⭐⭐⭐": 20,
    "🔔🔔🔔": 10,
    "🍇🍇🍇": 7,
    "🍋🍋🍋": 5,
    "🍒🍒🍒": 3,
}

ROULETTE_RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
ROULETTE_BLACK = {2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35}

MINES_GRID_SIZE = 25  # grille 5x5
MINES_MAX_COUNT = 24

CRASH_MAX_MULTIPLIER = 50.0

# ============================================================
# CRIME
# ============================================================
CRIME_SUCCESS_BASE_RATE = 0.45   # probabilité de succès de base pour un vol
CRIME_PRISON_MIN_MINUTES = 15
CRIME_PRISON_MAX_MINUTES = 240
CRIME_BAIL_MULTIPLIER = 1.5      # coût caution = peine restante (min) * multiplicateur * facteur
THEFT_MIN_PERCENT = 0.05         # vol entre 5% et 25% du solde liquide de la victime
THEFT_MAX_PERCENT = 0.25
SECURITY_LEVELS = {
    0: {"cost": 0, "protection": 0.0},
    1: {"cost": 100_000, "protection": 0.25},
    2: {"cost": 500_000, "protection": 0.50},
    3: {"cost": 2_000_000, "protection": 0.75},
}
LAWSUIT_FINE_MIN = 50_000
LAWSUIT_FINE_MAX = 500_000

# ============================================================
# ENCHÈRES
# ============================================================
AUCTION_MIN_BID_INCREMENT = 6000

# ============================================================
# DIVERS
# ============================================================
TIMEZONE = "Europe/Paris"

# ============================================================
# CHANNEL & GROUPE JOURNAL
# ============================================================
CHANNEL_USERNAME = "@lifeCitychannel"

# Groupe officiel obligatoire pour jouer (vérifié par subscription.py)
GROUP_USERNAME = "@lifecity_anothergirl"

# ID du groupe Telegram qui reçoit le journal quotidien à 21h
# Remplace par l'ID réel de ton groupe (commence par -100...)
JOURNAL_GROUP_ID = None  # ex: -1001234567890

# ============================================================
# DASHBOARD ADMIN (mini app Telegram - /dashboard)
# ============================================================
# Laisse à None : le bot lance automatiquement un tunnel Cloudflare gratuit
# (aucun domaine requis) et /dashboard utilise cette URL toute seule.
# Ne mets une valeur ici que si tu as un jour ton propre domaine à toi.
DASHBOARD_URL = None

# ============================================================
# CONTRATS IA (Gemini / GPT) — génération auto de contrats d'entreprise
# ============================================================

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Si tu passes un jour sur l'API OpenAI payante (plus fiable que le quota
# gratuit de Gemini) :
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Quel fournisseur utiliser : "gemini" ou "openai"
AI_CONTRACT_PROVIDER = "gemini"

# Intervalle entre deux cycles de génération de contrats (en secondes).
AI_CONTRACT_INTERVAL_SECONDS = 6 * 3600

# Durée de vie d'un contrat IA avant expiration s'il n'est pas complété.
AI_CONTRACT_MAX_AGE_SECONDS = 24 * 3600

# Nombre de tentatives avant de basculer sur le contrat de secours (fallback)
AI_CONTRACT_MAX_RETRIES = 3

# Backoff de base entre deux tentatives (secondes) — double à chaque essai
AI_CONTRACT_BASE_BACKOFF_SECONDS = 2

# Timeout max pour un appel à l'API (secondes)
AI_CONTRACT_TIMEOUT_SECONDS = 15

# Pause entre deux entreprises dans un même cycle (quota Gemini gratuit
# ~15 req/min -> 1.5s+ recommandé entre appels)
AI_CONTRACT_DELAY_BETWEEN_CALLS = 1.5
