"""
LifeCity Bot - Base de données (SQLite)
Gère l'initialisation et l'accès aux données des joueurs.
"""

import sqlite3
import time
from contextlib import contextmanager

from config import DB_PATH, STARTING_BALANCE

# Une seule ville jouable (résidence de tous les joueurs), distincte des
# villes de siège social des entreprises (companies.city).
CITY_NAME = "LIFECITY"
PLAYABLE_CITIES = [CITY_NAME]  # conservé (liste à 1 élément) pour compat avec le code existant

# Règles du système de villes (voir spec validée avec l'owner)
CITY_CHANGE_COOLDOWN = 7 * 86400          # (obsolète : ville unique, plus de changement possible)
MAYOR_TAX_MIN = 0                          # taux d'impôt municipal min (%)
MAYOR_TAX_MAX = 15                         # taux d'impôt municipal max (%)
MAYOR_EMBEZZLEMENT_RATIO = 0.30            # retrait unique > 30% de la caisse => destitution
MAYOR_MANDATE_DURATION = 14 * 86400        # mandat de 2 semaines (14 jours)
MAYOR_ELECTION_PERIOD = 48 * 3600          # 48h de vote avant tranchage par l'owner
MAYOR_STATE_CUT = 0.10                     # 10% des impôts collectés reversés à l'État
MAYOR_CANDIDACY_THRESHOLD = 10_000_000_000  # 10 milliards (solde + banque additionnés)

_conn = None  # connexion SQLite persistante, réutilisée par tout le bot


def _open_conn():
    conn = sqlite3.connect(DB_PATH, timeout=20.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Mode WAL pour meilleure concurrence (plusieurs lecteurs + 1 écrivain)
    conn.execute("PRAGMA journal_mode=WAL")
    # Renforce le timeout d'attente en cas de verrou (en ms, en plus du
    # timeout de connexion ci-dessus)
    conn.execute("PRAGMA busy_timeout=20000")
    # NORMAL (au lieu de FULL, la valeur par défaut) : avec le mode WAL,
    # c'est la combinaison recommandée par SQLite. Ça réduit ÉNORMÉMENT le
    # temps que chaque écriture (add_balance, update_player, etc.) passe à
    # attendre le disque, donc beaucoup moins de "database is locked" et un
    # bot globalement plus rapide, sans perte de fiabilité en cas de crash.
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def get_conn():
    """
    Renvoie la connexion SQLite persistante du bot (ouverte une seule fois,
    réutilisée pour tous les appels). Avant, chaque appel à get_conn()
    ouvrait une TOUTE NOUVELLE connexion et ré-exécutait les 4 PRAGMA à
    chaque fois — ça multipliait inutilement le travail sur chaque commande
    et ralentissait le bot. Une connexion unique, réglée une seule fois,
    est beaucoup plus rapide.
    """
    global _conn
    if _conn is None:
        _conn = _open_conn()
    try:
        yield _conn
        _conn.commit()
    except sqlite3.OperationalError as e:
        _conn.rollback()
        raise e
    except Exception:
        _conn.rollback()
        raise

def init_db() -> None:
    """Crée les tables si elles n'existent pas encore."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS players (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance INTEGER NOT NULL DEFAULT 0,
                bank_balance INTEGER NOT NULL DEFAULT 0,
                bank_name TEXT,
                family_name TEXT,
                profile_pic TEXT,
                profile_color TEXT DEFAULT '#FFFFFF',
                karma INTEGER NOT NULL DEFAULT 0,
                nationality TEXT,
                gender TEXT,
                relationship_mode TEXT,
                diploma TEXT,
                diploma_pending TEXT,
                diploma_ready_at INTEGER NOT NULL DEFAULT 0,
                company_id INTEGER,
                company_role TEXT,
                salary INTEGER NOT NULL DEFAULT 0,
                last_daily INTEGER NOT NULL DEFAULT 0,
                last_work INTEGER NOT NULL DEFAULT 0,
                jail_until INTEGER NOT NULL DEFAULT 0,
                security_level INTEGER NOT NULL DEFAULT 0,
                banned INTEGER NOT NULL DEFAULT 0,
                ban_reason TEXT,
                cmd_count INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS marriages (
                user_id_1 INTEGER NOT NULL,
                user_id_2 INTEGER NOT NULL,
                married_at INTEGER NOT NULL,
                PRIMARY KEY (user_id_1, user_id_2)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS family_links (
                parent_id INTEGER NOT NULL,
                child_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (parent_id, child_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS friendships (
                user_id_1 INTEGER NOT NULL,
                user_id_2 INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (user_id_1, user_id_2)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_requests (
                request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                from_user INTEGER NOT NULL,
                to_user INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS companies (
                company_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                sector TEXT NOT NULL,
                ceo_id INTEGER NOT NULL,
                treasury INTEGER NOT NULL DEFAULT 0,
                city TEXT NOT NULL DEFAULT 'Paris',
                country TEXT NOT NULL DEFAULT '🇫🇷 France',
                description TEXT,
                created_at INTEGER NOT NULL,
                valuation_ref INTEGER NOT NULL DEFAULT 0,
                valuation_updated_at INTEGER NOT NULL DEFAULT 0,
                last_dividend_at INTEGER NOT NULL DEFAULT 0
            )
        """)
        # Migration pour les bases de données déjà existantes créées avant
        # l'ajout de la valorisation lissée (valuation_ref) et des dividendes
        # (last_dividend_at) : on ajoute les colonnes si elles n'existent pas
        # déjà, sans rien casser.
        for col_def in (
            "ALTER TABLE companies ADD COLUMN valuation_ref INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE companies ADD COLUMN valuation_updated_at INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE companies ADD COLUMN last_dividend_at INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE companies ADD COLUMN last_salary_payment INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                conn.execute(col_def)
            except sqlite3.OperationalError:
                pass  # la colonne existe déjà
        conn.execute("""
            CREATE TABLE IF NOT EXISTS company_shares (
                company_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                shares INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (company_id, user_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS company_applications (
                application_id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS company_logs (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS company_contracts (
                contract_id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_company_id INTEGER NOT NULL,
                to_company_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                reward INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_contracts (
                ai_contract_id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                contract_text TEXT NOT NULL,
                reward INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'ai',
                status TEXT NOT NULL DEFAULT 'active',
                created_at INTEGER NOT NULL,
                completed_at INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS share_offers (
                offer_id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                seller_id INTEGER NOT NULL,
                shares INTEGER NOT NULL,
                price_per_share INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS thefts (
                theft_id INTEGER PRIMARY KEY AUTOINCREMENT,
                thief_id INTEGER NOT NULL,
                victim_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                success INTEGER NOT NULL,
                reported INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lawsuits (
                lawsuit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                plaintiff_id INTEGER NOT NULL,
                defendant_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS items (
                item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                value INTEGER NOT NULL,
                for_sale_price INTEGER,
                created_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS auctions (
                auction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_name TEXT NOT NULL,
                current_bid INTEGER NOT NULL DEFAULT 0,
                current_bidder INTEGER,
                status TEXT NOT NULL DEFAULT 'open',
                created_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bank_loans (
                loan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                remaining INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES players(user_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_user INTEGER,
                to_user INTEGER,
                amount INTEGER NOT NULL,
                reason TEXT,
                created_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bureau_contracts (
                bc_id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                sector TEXT NOT NULL,
                task TEXT NOT NULL,
                target_cmd INTEGER NOT NULL,
                reward INTEGER NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                created_at INTEGER NOT NULL,
                claimed_at INTEGER,
                baseline_cmd INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS active_groups (
                chat_id INTEGER PRIMARY KEY,
                chat_title TEXT,
                last_seen INTEGER NOT NULL
            )
        """)
        
        # NOUVELLE TABLE POUR LES COMPTES BANCAIRES MULTIPLES AVEC last_interest_at
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_bank_accounts (
                account_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                bank_name TEXT NOT NULL,
                balance INTEGER NOT NULL DEFAULT 0,
                last_interest_at INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES players(user_id),
                UNIQUE(user_id, bank_name)
            )
        """)

        # NOUVELLE TABLE POUR LA CONFIGURATION DES RANGS
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rank_config (
                rank INTEGER PRIMARY KEY,
                price INTEGER NOT NULL DEFAULT 0
            )
        """)

        # NOUVELLE TABLE POUR LES DEMANDES D'ACHAT DE PARTS
        conn.execute("""
            CREATE TABLE IF NOT EXISTS purchase_requests (
                request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                buyer_id INTEGER NOT NULL,
                company_id INTEGER NOT NULL,
                shares INTEGER NOT NULL,
                total_price INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL,
                FOREIGN KEY (buyer_id) REFERENCES players(user_id),
                FOREIGN KEY (company_id) REFERENCES companies(company_id)
            )
        """)

        # Migration douce : ajoute les colonnes manquantes sur une base déjà existante
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(players)")}
        for col, ddl in (
            ("nationality", "ALTER TABLE players ADD COLUMN nationality TEXT"),
            ("gender", "ALTER TABLE players ADD COLUMN gender TEXT"),
            ("relationship_mode", "ALTER TABLE players ADD COLUMN relationship_mode TEXT"),
            ("is_admin", "ALTER TABLE players ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"),
            ("company_cooldown_until", "ALTER TABLE players ADD COLUMN company_cooldown_until INTEGER NOT NULL DEFAULT 0"),
            ("metier", "ALTER TABLE players ADD COLUMN metier TEXT"),
            ("metier_last_change", "ALTER TABLE players ADD COLUMN metier_last_change INTEGER NOT NULL DEFAULT 0"),
        ):
            if col not in existing_cols:
                conn.execute(ddl)

        # Migration pour ajouter la colonne description à companies
        company_cols = {row["name"] for row in conn.execute("PRAGMA table_info(companies)")}
        if "description" not in company_cols:
            conn.execute("ALTER TABLE companies ADD COLUMN description TEXT")

        # Migration pour bank_loans : ajoute bank_name si nécessaire
        loan_cols = {row["name"] for row in conn.execute("PRAGMA table_info(bank_loans)")}
        if "bank_name" not in loan_cols:
            conn.execute("ALTER TABLE bank_loans ADD COLUMN bank_name TEXT")
        
        # Migration pour bureau_contracts : ajoute baseline_cmd (point de départ du
        # compteur de commandes, pour que la progression reparte à 0 à chaque
        # nouveau contrat au lieu d'utiliser le total de commandes depuis toujours)
        bc_cols = {row["name"] for row in conn.execute("PRAGMA table_info(bureau_contracts)")}
        if "baseline_cmd" not in bc_cols:
            conn.execute("ALTER TABLE bureau_contracts ADD COLUMN baseline_cmd INTEGER NOT NULL DEFAULT 0")
        if "base_target" not in bc_cols:
            conn.execute("ALTER TABLE bureau_contracts ADD COLUMN base_target INTEGER NOT NULL DEFAULT 0")
        if "reward_rate" not in bc_cols:
            conn.execute("ALTER TABLE bureau_contracts ADD COLUMN reward_rate REAL NOT NULL DEFAULT 0")
        if "reward_rate" not in bc_cols:
            conn.execute("ALTER TABLE bureau_contracts ADD COLUMN reward_rate REAL NOT NULL DEFAULT 0")

        # Migration pour company_contracts : ajoute bonus_percent / duration_days / expires_at
        contract_cols = {row["name"] for row in conn.execute("PRAGMA table_info(company_contracts)")}
        for col, ddl in (
            ("bonus_percent", "ALTER TABLE company_contracts ADD COLUMN bonus_percent INTEGER NOT NULL DEFAULT 0"),
            ("duration_days", "ALTER TABLE company_contracts ADD COLUMN duration_days INTEGER NOT NULL DEFAULT 0"),
            ("expires_at", "ALTER TABLE company_contracts ADD COLUMN expires_at INTEGER NOT NULL DEFAULT 0"),
            ("is_rare", "ALTER TABLE company_contracts ADD COLUMN is_rare INTEGER NOT NULL DEFAULT 0"),
        ):
            if col not in contract_cols:
                conn.execute(ddl)

        # Migration pour pending_requests : ajoute amount (montant proposé, ex. offre de salaire)
        pending_cols = {row["name"] for row in conn.execute("PRAGMA table_info(pending_requests)")}
        if "amount" not in pending_cols:
            conn.execute("ALTER TABLE pending_requests ADD COLUMN amount INTEGER NOT NULL DEFAULT 0")

        # Migration pour companies : ajoute last_recruitment_ad (cooldown des annonces de recrutement)
        companies_cols = {row["name"] for row in conn.execute("PRAGMA table_info(companies)")}
        if "last_recruitment_ad" not in companies_cols:
            conn.execute("ALTER TABLE companies ADD COLUMN last_recruitment_ad INTEGER NOT NULL DEFAULT 0")
        if "total_tax_paid" not in companies_cols:
            conn.execute("ALTER TABLE companies ADD COLUMN total_tax_paid INTEGER NOT NULL DEFAULT 0")
        if "total_dividends_paid" not in companies_cols:
            conn.execute("ALTER TABLE companies ADD COLUMN total_dividends_paid INTEGER NOT NULL DEFAULT 0")

        # Migration pour players : ajoute last_cmd_count_at (anti-spam du
        # compteur de commandes, utilisé notamment par les contrats Bureau)
        players_cmd_cols = {row["name"] for row in conn.execute("PRAGMA table_info(players)")}
        if "last_cmd_count_at" not in players_cmd_cols:
            conn.execute("ALTER TABLE players ADD COLUMN last_cmd_count_at INTEGER NOT NULL DEFAULT 0")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS bc_member_baseline (
                bc_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                baseline INTEGER NOT NULL,
                PRIMARY KEY (bc_id, user_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS company_buildings (
                company_id INTEGER NOT NULL,
                slot TEXT NOT NULL,
                purchased_at INTEGER NOT NULL,
                suspended INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (company_id, slot)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_recruitment_ads (
                ad_id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                poste TEXT NOT NULL,
                from_user INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)

        # Fonds État : accumule les impôts prélevés automatiquement sur les
        # entreprises. Une seule ligne (id=1) qui fait office de compteur global.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS state_treasury (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                balance INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("INSERT OR IGNORE INTO state_treasury (id, balance) VALUES (1, 0)")

        # ============================================================
        # SYSTÈME DE VILLES / MAIRIES
        # ============================================================
        # Liste fixe des 15 villes jouables (résidence des joueurs), distinctes
        # des villes de siège social des entreprises (companies.city).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cities (
                city_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                treasury INTEGER NOT NULL DEFAULT 0,
                tax_rate INTEGER NOT NULL DEFAULT 5,
                mayor_id INTEGER,
                mandate_start INTEGER NOT NULL DEFAULT 0,
                mandate_end INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            )
        """)
        now_ts = int(time.time())
        for city_name in PLAYABLE_CITIES:
            conn.execute(
                "INSERT OR IGNORE INTO cities (name, treasury, tax_rate, created_at) VALUES (?, 0, 5, ?)",
                (city_name, now_ts),
            )

        # Élections municipales : une ligne par élection ouverte/close pour une ville.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mayor_elections (
                election_id INTEGER PRIMARY KEY AUTOINCREMENT,
                city TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ouverte',
                opened_at INTEGER NOT NULL,
                closes_at INTEGER NOT NULL,
                winner_id INTEGER,
                decided_at INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mayor_candidates (
                election_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (election_id, user_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mayor_votes (
                election_id INTEGER NOT NULL,
                voter_id INTEGER NOT NULL,
                candidate_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (election_id, voter_id)
            )
        """)
        # Historique : élections, destitutions, sanctions, fins de mandat...
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mayor_history (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                city TEXT NOT NULL,
                user_id INTEGER,
                event TEXT NOT NULL,
                amount INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            )
        """)
        # Président de la commission électorale : nommé par l'owner, il peut
        # ouvrir/clôturer le vote des élections, mais seul l'owner tranche le
        # vainqueur. Une seule ligne (id=1) : un seul président à la fois.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS election_commission (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                user_id INTEGER NOT NULL,
                appointed_at INTEGER NOT NULL
            )
        """)
        # Migration players : ville de résidence + cooldown de changement.
        player_cols = {row["name"] for row in conn.execute("PRAGMA table_info(players)")}
        if "city" not in player_cols:
            conn.execute("ALTER TABLE players ADD COLUMN city TEXT")
        if "city_changed_at" not in player_cols:
            conn.execute("ALTER TABLE players ADD COLUMN city_changed_at INTEGER NOT NULL DEFAULT 0")
        # Ville unique : tout joueur (ancien système multi-villes ou sans ville) est
        # rattaché à LIFECITY.
        conn.execute(
            "UPDATE players SET city = ? WHERE city IS NULL OR city != ?",
            (CITY_NAME, CITY_NAME),
        )

        # Biens immobiliers possédés par les joueurs (module immobilier.py).
        # dernier_loyer = timestamp du dernier passage de /loyer (ou de l'achat),
        # sert à calculer combien d'intervalles de loyer se sont écoulés.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS biens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type_bien TEXT NOT NULL,
                dernier_loyer INTEGER NOT NULL,
                purchased_at INTEGER NOT NULL
            )
        """)

        # Migration pour user_bank_accounts : ajoute last_interest_at si nécessaire
        bank_account_cols = {row["name"] for row in conn.execute("PRAGMA table_info(user_bank_accounts)")}
        if "last_interest_at" not in bank_account_cols:
            conn.execute("ALTER TABLE user_bank_accounts ADD COLUMN last_interest_at INTEGER NOT NULL DEFAULT 0")
            # Mettre à jour les comptes existants
            conn.execute(
                "UPDATE user_bank_accounts SET last_interest_at = created_at WHERE last_interest_at = 0"
            )
        
        # Migration pour bank_accounts vers user_bank_accounts si nécessaire
        # Vérifier si l'ancienne table bank_accounts existe et migrer les données
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bank_accounts'"
        ).fetchone()
        
        if tables:
            # Vérifier si la colonne last_interest_at existe dans l'ancienne table
            old_account_cols = {row["name"] for row in conn.execute("PRAGMA table_info(bank_accounts)")}
            if "last_interest_at" in old_account_cols:
                # Migrer les comptes existants avec last_interest_at
                conn.execute("""
                    INSERT OR IGNORE INTO user_bank_accounts (user_id, bank_name, balance, last_interest_at, created_at)
                    SELECT user_id, bank_name, balance, 
                           COALESCE(last_interest_at, strftime('%s', 'now')) as last_interest_at,
                           COALESCE(opened_at, strftime('%s', 'now')) as created_at
                    FROM bank_accounts
                    WHERE bank_name IS NOT NULL
                """)
            else:
                # Migrer sans last_interest_at
                conn.execute("""
                    INSERT OR IGNORE INTO user_bank_accounts (user_id, bank_name, balance, last_interest_at, created_at)
                    SELECT user_id, bank_name, balance, 
                           strftime('%s', 'now') as last_interest_at,
                           strftime('%s', 'now') as created_at
                    FROM bank_accounts
                    WHERE bank_name IS NOT NULL
                """)
            # Note: On ne supprime pas l'ancienne table immédiatement au cas où
            # La suppression serait faite manuellement après vérification

        # ── Index (essentiels pour la vitesse à mesure que les tables grossissent) ──
        # Sans ça, chaque recherche par user_id/company_id parcourt TOUTE la
        # table ligne par ligne. Ça ne se voit pas au début (peu de données),
        # mais ça ralentit progressivement le bot à mesure que les joueurs
        # jouent (transactions, vols, procès, logs d'entreprise...).
        conn.execute("CREATE INDEX IF NOT EXISTS idx_players_company ON players(company_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_from ON pending_requests(from_user)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_to ON pending_requests(to_user)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_company_apps_company ON company_applications(company_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_company_apps_user ON company_applications(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_company_logs_company ON company_logs(company_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_share_offers_company ON share_offers(company_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_share_offers_seller ON share_offers(seller_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_thefts_thief ON thefts(thief_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_thefts_victim ON thefts(victim_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lawsuits_plaintiff ON lawsuits(plaintiff_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lawsuits_defendant ON lawsuits(defendant_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_owner ON items(owner_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bank_loans_user ON bank_loans(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_from ON transactions(from_user)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_to ON transactions(to_user)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bureau_contracts_company ON bureau_contracts(company_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_bank_accounts_user ON user_bank_accounts(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_purchase_requests_buyer ON purchase_requests(buyer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_purchase_requests_company ON purchase_requests(company_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_company_contracts_from ON company_contracts(from_company_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_company_contracts_to ON company_contracts(to_company_id)")

        # Index pour les tables ajoutées par les fonctionnalités récentes
        # (diplômes, journal), créées seulement si ces tables existent déjà.
        existing_tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "user_diplomas" in existing_tables:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user_diplomas_user ON user_diplomas(user_id)")
        if "events_log" in existing_tables:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_log_created ON events_log(created_at)")


def get_player_by_id(user_id: int):
    """Lecture seule : renvoie le joueur s'il existe, sans jamais créer ni écraser username/first_name.
    À utiliser à la place de get_or_create_player(id, None, None), qui écraserait le vrai nom en base."""
    with get_conn() as conn:
        return conn.execute("SELECT * FROM players WHERE user_id = ?", (user_id,)).fetchone()


def get_or_create_player(user_id: int, username: str, first_name: str) -> sqlite3.Row:
    """Récupère un joueur, le crée avec le solde de départ s'il n'existe pas."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM players WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO players (user_id, username, first_name, balance, created_at, city)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, username, first_name, STARTING_BALANCE, int(time.time()), CITY_NAME),
            )
            row = conn.execute(
                "SELECT * FROM players WHERE user_id = ?", (user_id,)
            ).fetchone()
        else:
            # Ne jamais écraser un pseudo/prénom déjà en base avec None : certains
            # appels (vérifications internes, lookups d'ID) passent volontairement
            # username=None/first_name=None et ne doivent pas corrompre le vrai nom.
            new_username = username if username is not None else row["username"]
            new_first_name = first_name if first_name is not None else row["first_name"]
            if row["username"] != new_username or row["first_name"] != new_first_name:
                conn.execute(
                    "UPDATE players SET username = ?, first_name = ? WHERE user_id = ?",
                    (new_username, new_first_name, user_id),
                )
                row = conn.execute(
                    "SELECT * FROM players WHERE user_id = ?", (user_id,)
                ).fetchone()
        return row


def update_player(user_id: int, **fields) -> None:
    """Met à jour des champs arbitraires du joueur."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE players SET {set_clause} WHERE user_id = ?", values)


def add_balance(user_id: int, amount: int) -> None:
    """Ajoute (ou retire si négatif) un montant au solde liquide d'un joueur."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE players SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id),
        )


def is_banned(user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT banned FROM players WHERE user_id = ?", (user_id,)
        ).fetchone()
    return bool(row and row["banned"])


def is_admin(user_id: int) -> bool:
    """Vérifie si un joueur est administrateur."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT is_admin FROM players WHERE user_id = ?", (user_id,)
        ).fetchone()
    return bool(row and row["is_admin"])


def get_player_by_name_or_id(identifier: str):
    """Cherche un joueur par user_id numérique ou par @username."""
    identifier = identifier.lstrip("@")
    with get_conn() as conn:
        if identifier.isdigit():
            return conn.execute(
                "SELECT * FROM players WHERE user_id = ?", (int(identifier),)
            ).fetchone()
        return conn.execute(
            "SELECT * FROM players WHERE username = ? COLLATE NOCASE", (identifier,)
        ).fetchone()


def find_player_by_identifier(identifier: str):
    """
    Cherche un joueur par ID numérique, @pseudo exact, ou nom/pseudo partiel.
    Renvoie le joueur UNIQUEMENT s'il y a une correspondance certaine :
    - ID numérique ou @pseudo exact → renvoie directement s'il existe.
    - Sinon, recherche partielle sur le prénom ou le pseudo → renvoie le
      joueur seulement s'il y a EXACTEMENT UNE correspondance ; renvoie None
      si aucune ou plusieurs correspondances (ambigu, à l'appelant de
      redemander un identifiant plus précis).
    """
    identifier = identifier.lstrip("@")
    with get_conn() as conn:
        if identifier.isdigit():
            return conn.execute(
                "SELECT * FROM players WHERE user_id = ?", (int(identifier),)
            ).fetchone()

        exact = conn.execute(
            "SELECT * FROM players WHERE username = ? COLLATE NOCASE", (identifier,)
        ).fetchone()
        if exact is not None:
            return exact

        rows = conn.execute(
            "SELECT * FROM players WHERE first_name LIKE ? COLLATE NOCASE "
            "OR username LIKE ? COLLATE NOCASE",
            (f"%{identifier}%", f"%{identifier}%"),
        ).fetchall()
        if len(rows) == 1:
            return rows[0]
        return None


def log_transaction(from_user: int | None, to_user: int | None, amount: int, reason: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO transactions (from_user, to_user, amount, reason, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (from_user, to_user, amount, reason, int(time.time())),
        )


# ============================================================
# FAMILLE
# ============================================================

def get_spouse_id(user_id: int) -> int | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT user_id_1, user_id_2 FROM marriages
               WHERE user_id_1 = ? OR user_id_2 = ?""",
            (user_id, user_id),
        ).fetchone()
    if row is None:
        return None
    return row["user_id_2"] if row["user_id_1"] == user_id else row["user_id_1"]


def create_marriage(user_id_1: int, user_id_2: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO marriages (user_id_1, user_id_2, married_at) VALUES (?, ?, ?)",
            (user_id_1, user_id_2, int(time.time())),
        )


def delete_marriage(user_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM marriages WHERE user_id_1 = ? OR user_id_2 = ?",
            (user_id, user_id),
        )


def get_parents(child_id: int) -> list[int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT parent_id FROM family_links WHERE child_id = ?", (child_id,)
        ).fetchall()
    return [r["parent_id"] for r in rows]


def get_children(parent_id: int) -> list[int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT child_id FROM family_links WHERE parent_id = ?", (parent_id,)
        ).fetchall()
    return [r["child_id"] for r in rows]


def add_family_link(parent_id: int, child_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO family_links (parent_id, child_id, created_at) VALUES (?, ?, ?)",
            (parent_id, child_id, int(time.time())),
        )


def remove_family_link(parent_id: int, child_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM family_links WHERE parent_id = ? AND child_id = ?",
            (parent_id, child_id),
        )


def get_friends(user_id: int) -> list[int]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT user_id_1, user_id_2 FROM friendships
               WHERE user_id_1 = ? OR user_id_2 = ?""",
            (user_id, user_id),
        ).fetchall()
    friends = []
    for r in rows:
        friends.append(r["user_id_2"] if r["user_id_1"] == user_id else r["user_id_1"])
    return friends


def add_friendship(user_id_1: int, user_id_2: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO friendships (user_id_1, user_id_2, created_at) VALUES (?, ?, ?)",
            (user_id_1, user_id_2, int(time.time())),
        )


def remove_friendship(user_id_1: int, user_id_2: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """DELETE FROM friendships WHERE
               (user_id_1 = ? AND user_id_2 = ?) OR (user_id_1 = ? AND user_id_2 = ?)""",
            (user_id_1, user_id_2, user_id_2, user_id_1),
        )


def create_pending_request(kind: str, from_user: int, to_user: int, ttl_seconds: int = 60, amount: int = 0) -> int:
    now = int(time.time())
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO pending_requests (kind, from_user, to_user, created_at, expires_at, amount)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (kind, from_user, to_user, now, now + ttl_seconds, amount),
        )
        return cur.lastrowid


def get_pending_request(kind: str, to_user: int, from_user: int | None = None):
    now = int(time.time())
    with get_conn() as conn:
        if from_user is not None:
            row = conn.execute(
                """SELECT * FROM pending_requests
                   WHERE kind = ? AND to_user = ? AND from_user = ? AND expires_at > ?
                   ORDER BY created_at DESC LIMIT 1""",
                (kind, to_user, from_user, now),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM pending_requests
                   WHERE kind = ? AND to_user = ? AND expires_at > ?
                   ORDER BY created_at DESC LIMIT 1""",
                (kind, to_user, now),
            ).fetchone()
    return row


def delete_pending_request(request_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM pending_requests WHERE request_id = ?", (request_id,))


# ============================================================
# ENTREPRISES
# ============================================================

def create_company(name: str, sector: str, ceo_id: int, city: str = "Paris", country: str = "🇫🇷 France") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO companies (name, sector, ceo_id, treasury, city, country, created_at)
               VALUES (?, ?, ?, 0, ?, ?, ?)""",
            (name, sector, ceo_id, city, country, int(time.time())),
        )
        company_id = cur.lastrowid
        conn.execute(
            "INSERT INTO company_shares (company_id, user_id, shares) VALUES (?, ?, 100)",
            (company_id, ceo_id),
        )
        return company_id


def get_company_by_name(name: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM companies WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()


def get_company_by_id(company_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM companies WHERE company_id = ?", (company_id,)
        ).fetchone()


def update_company_description(company_id: int, description: str) -> None:
    """Met à jour la description d'une entreprise."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET description = ? WHERE company_id = ?",
            (description, company_id)
        )


def get_company_description(company_id: int) -> str | None:
    """Récupère la description d'une entreprise."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT description FROM companies WHERE company_id = ?",
            (company_id,)
        ).fetchone()
    return row["description"] if row else None


def get_company_employees(company_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM players WHERE company_id = ?", (company_id,)
        ).fetchall()


def delete_company(company_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE players SET company_id = NULL, company_role = NULL, salary = 0 WHERE company_id = ?", (company_id,))
        conn.execute("DELETE FROM company_shares WHERE company_id = ?", (company_id,))
        conn.execute("DELETE FROM company_applications WHERE company_id = ?", (company_id,))
        conn.execute("DELETE FROM purchase_requests WHERE company_id = ?", (company_id,))
        conn.execute(
            "DELETE FROM company_contracts WHERE from_company_id = ? OR to_company_id = ?",
            (company_id, company_id),
        )
        conn.execute("DELETE FROM pending_recruitment_ads WHERE company_id = ?", (company_id,))
        conn.execute("DELETE FROM company_logs WHERE company_id = ?", (company_id,))
        # company_negotiations est créée à la volée ailleurs (_ensure_negotiation_table) :
        # elle peut ne pas encore exister si aucune négociation n'a jamais eu lieu.
        existing = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='company_negotiations'"
        )}
        if "company_negotiations" in existing:
            conn.execute("DELETE FROM company_negotiations WHERE company_id = ?", (company_id,))
        conn.execute("DELETE FROM companies WHERE company_id = ?", (company_id,))


def update_company_treasury(company_id: int, delta: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET treasury = treasury + ? WHERE company_id = ?",
            (delta, company_id),
        )


def withdraw_from_treasury(company_id: int, amount: int) -> bool:
    """
    Retire `amount` de la trésorerie de façon ATOMIQUE : la vérification
    (trésorerie suffisante) et la déduction se font dans une seule requête
    SQL conditionnelle, pas en deux étapes séparées (lecture puis écriture).

    Avant, /retraitboite lisait la trésorerie, vérifiait en Python, puis
    faisait la déduction à part : si la commande était envoyée plusieurs
    fois très vite, chaque appel pouvait lire la même valeur avant que les
    précédents ne l'aient déduite, permettant de retirer plusieurs fois le
    même argent (trésorerie qui passe en négatif).

    Retourne True si le retrait a été effectué, False si la trésorerie était
    insuffisante (aucune modification faite).
    """
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE companies SET treasury = treasury - ? WHERE company_id = ? AND treasury >= ?",
            (amount, company_id, amount),
        )
        return cur.rowcount > 0


def set_company_last_salary_payment(company_id: int, timestamp: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET last_salary_payment = ? WHERE company_id = ?",
            (timestamp, company_id),
        )


def add_company_log(company_id: int, message: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO company_logs (company_id, message, created_at) VALUES (?, ?, ?)",
            (company_id, message, int(time.time())),
        )


def get_company_logs(company_id: int, limit: int = 10):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM company_logs WHERE company_id = ? ORDER BY created_at DESC LIMIT ?",
            (company_id, limit),
        ).fetchall()


def create_application(company_id: int, user_id: int, kind: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO company_applications (company_id, user_id, kind, status, created_at)
               VALUES (?, ?, ?, 'pending', ?)""",
            (company_id, user_id, kind, int(time.time())),
        )
        return cur.lastrowid


def get_application(application_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM company_applications WHERE application_id = ?", (application_id,)
        ).fetchone()


def get_pending_applications(company_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM company_applications WHERE company_id = ? AND status = 'pending' ORDER BY created_at",
            (company_id,),
        ).fetchall()


def update_application_status(application_id: int, status: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE company_applications SET status = ? WHERE application_id = ?",
            (status, application_id),
        )


def get_company_shares(company_id: int):
    with get_conn() as conn:
        return conn.execute(
          "SELECT * FROM company_shares WHERE company_id = ? ORDER BY shares DESC",
            (company_id,),
        ).fetchall()


def get_user_shares(company_id: int, user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT shares FROM company_shares WHERE company_id = ? AND user_id = ?",
            (company_id, user_id),
        ).fetchone()
    return row["shares"] if row else 0


def set_user_shares(company_id: int, user_id: int, shares: int) -> None:
    """Met à jour les parts d'un utilisateur dans une entreprise."""
    with get_conn() as conn:
        # Vérifier si l'utilisateur a déjà des parts
        existing = conn.execute(
            "SELECT shares FROM company_shares WHERE company_id = ? AND user_id = ?",
            (company_id, user_id)
        ).fetchone()
        
        if existing:
            if shares <= 0:
                # Supprimer la ligne si les parts sont à 0 ou négatives
                conn.execute(
                    "DELETE FROM company_shares WHERE company_id = ? AND user_id = ?",
                    (company_id, user_id)
                )
            else:
                # Mettre à jour les parts
                conn.execute(
                    "UPDATE company_shares SET shares = ? WHERE company_id = ? AND user_id = ?",
                    (shares, company_id, user_id)
                )
        else:
            if shares > 0:
                # Insérer une nouvelle ligne
                conn.execute(
                    "INSERT INTO company_shares (company_id, user_id, shares) VALUES (?, ?, ?)",
                    (company_id, user_id, shares)
                )

def execute_share_purchase(company_id: int, seller_id: int, buyer_id: int, shares: int, total_price: int) -> bool:
    """
    Exécute un achat de parts (acheteur <-> vendeur, ex : le PDG) de façon
    ATOMIQUE : débit acheteur, crédit du vendeur, transfert des parts se
    font dans UN SEUL bloc get_conn() (une seule transaction SQL).

    Le vendeur (celui qui possédait les parts, ex. le PDG) reçoit l'argent
    personnellement — ce n'est pas la trésorerie de l'entreprise qui est
    créditée.

    Avant, ces opérations étaient faites via plusieurs appels séparés à
    add_balance()/update_company_treasury()/set_user_shares(), chacun
    ouvrant sa propre transaction. Si le process crashait entre deux
    appels, on pouvait se retrouver avec de l'argent débité mais des
    parts jamais transférées (ou l'inverse). Ici, soit tout est appliqué,
    soit rien ne l'est (rollback automatique en cas d'exception).

    Retourne True si l'achat a été exécuté, False si le solde de l'acheteur
    ou les parts du vendeur sont insuffisants (aucune modification faite).
    """
    with get_conn() as conn:
        buyer = conn.execute(
            "SELECT balance FROM players WHERE user_id = ?", (buyer_id,)
        ).fetchone()
        if buyer is None or buyer["balance"] < total_price:
            return False

        seller_row = conn.execute(
            "SELECT shares FROM company_shares WHERE company_id = ? AND user_id = ?",
            (company_id, seller_id),
        ).fetchone()
        seller_shares = seller_row["shares"] if seller_row else 0
        if seller_shares < shares:
            return False

        # Débit acheteur + crédit du VENDEUR (le PDG touche l'argent de ses
        # propres parts vendues — ce n'est pas la trésorerie de l'entreprise
        # qui est créditée).
        conn.execute(
            "UPDATE players SET balance = balance - ? WHERE user_id = ?",
            (total_price, buyer_id),
        )
        conn.execute(
            "UPDATE players SET balance = balance + ? WHERE user_id = ?",
            (total_price, seller_id),
        )

        # Retrait des parts au vendeur
        new_seller_shares = seller_shares - shares
        if new_seller_shares <= 0:
            conn.execute(
                "DELETE FROM company_shares WHERE company_id = ? AND user_id = ?",
                (company_id, seller_id),
            )
        else:
            conn.execute(
                "UPDATE company_shares SET shares = ? WHERE company_id = ? AND user_id = ?",
                (new_seller_shares, company_id, seller_id),
            )

        # Ajout des parts à l'acheteur
        buyer_row = conn.execute(
            "SELECT shares FROM company_shares WHERE company_id = ? AND user_id = ?",
            (company_id, buyer_id),
        ).fetchone()
        buyer_shares = (buyer_row["shares"] if buyer_row else 0) + shares
        if buyer_row:
            conn.execute(
                "UPDATE company_shares SET shares = ? WHERE company_id = ? AND user_id = ?",
                (buyer_shares, company_id, buyer_id),
            )
        else:
            conn.execute(
                "INSERT INTO company_shares (company_id, user_id, shares) VALUES (?, ?, ?)",
                (company_id, buyer_id, buyer_shares),
            )

        return True


def execute_share_sale(company_id: int, seller_id: int, shares: int, total_price: int) -> bool:
    """
    Exécute une vente instantanée de parts (rachat par la trésorerie de
    l'entreprise), de façon ATOMIQUE : vérifie trésorerie ET parts, puis
    débite la trésorerie, retire les parts du vendeur et crédite son
    portefeuille — tout dans une seule transaction.

    Retourne True si la vente a été exécutée, False si la trésorerie de
    l'entreprise ou les parts du vendeur sont insuffisantes.
    """
    with get_conn() as conn:
        company = conn.execute(
            "SELECT treasury FROM companies WHERE company_id = ?", (company_id,)
        ).fetchone()
        if company is None or company["treasury"] < total_price:
            return False

        seller_row = conn.execute(
            "SELECT shares FROM company_shares WHERE company_id = ? AND user_id = ?",
            (company_id, seller_id),
        ).fetchone()
        seller_shares = seller_row["shares"] if seller_row else 0
        if seller_shares < shares:
            return False

        conn.execute(
            "UPDATE companies SET treasury = treasury - ? WHERE company_id = ?",
            (total_price, company_id),
        )

        new_seller_shares = seller_shares - shares
        if new_seller_shares <= 0:
            conn.execute(
                "DELETE FROM company_shares WHERE company_id = ? AND user_id = ?",
                (company_id, seller_id),
            )
        else:
            conn.execute(
                "UPDATE company_shares SET shares = ? WHERE company_id = ? AND user_id = ?",
                (new_seller_shares, company_id, seller_id),
            )

        conn.execute(
            "UPDATE players SET balance = balance + ? WHERE user_id = ?",
            (total_price, seller_id),
        )

        return True


def get_or_update_valuation_ref(company_id: int, decay_rate_per_hour: float = 0.05) -> int:
    """
    Renvoie une valeur de référence "lissée" de la trésorerie, utilisée pour
    calculer le prix des parts (voir get_share_value), au lieu de la
    trésorerie brute instantanée.

    Pourquoi : avec la trésorerie brute, un PDG peut vider la caisse (le
    prix de la part s'effondre instantanément), racheter les parts au sol,
    puis redéposer l'argent — abus de type "pump and dump" inversé.

    Comment : la référence ne PEUT PAS chuter plus vite qu'un taux de
    décroissance par heure (decay_rate_per_hour, 5% par défaut). Si la
    trésorerie remonte, la référence suit instantanément (pas de plafond à
    la hausse). Si elle baisse brutalement, la référence ne descend que
    progressivement, heure après heure — un retrait suivi d'un rachat
    immédiat n'a donc quasiment aucun effet sur le prix de la part.

    Retourne la nouvelle valeur de référence (entier), et la persiste.
    """
    now = int(time.time())
    with get_conn() as conn:
        row = conn.execute(
            "SELECT treasury, valuation_ref, valuation_updated_at FROM companies WHERE company_id = ?",
            (company_id,),
        ).fetchone()
        if row is None:
            return 0

        treasury = row["treasury"] or 0
        ref = row["valuation_ref"] or 0
        updated_at = row["valuation_updated_at"] or 0

        if ref <= 0 or updated_at <= 0:
            # Première initialisation : la référence part de la trésorerie actuelle.
            new_ref = treasury
        else:
            elapsed_hours = max(0.0, (now - updated_at) / 3600.0)
            max_drop = ref * decay_rate_per_hour * elapsed_hours
            floor = ref - max_drop
            new_ref = max(treasury, floor)

        new_ref = int(new_ref)
        conn.execute(
            "UPDATE companies SET valuation_ref = ?, valuation_updated_at = ? WHERE company_id = ?",
            (new_ref, now, company_id),
        )
        return new_ref


def create_ai_contract(company_id: int, contract_text: str, reward: int, source: str = "ai") -> int:
    """
    Enregistre un contrat généré (par l'IA, ou par le fallback local si
    l'API était indisponible — voir ai_contract_generator.py). Le champ
    `source` permet de distinguer les deux dans les logs/stats, utile pour
    surveiller si le quota Gemini est souvent dépassé.
    """
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO ai_contracts (company_id, contract_text, reward, source, status, created_at)
               VALUES (?, ?, ?, ?, 'active', ?)""",
            (company_id, contract_text, reward, source, int(time.time())),
        )
        return cur.lastrowid


def get_active_ai_contract(company_id: int):
    """Renvoie le contrat IA actif d'une entreprise, s'il y en a un."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM ai_contracts WHERE company_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (company_id,),
        ).fetchone()


def get_ai_contracts_history(company_id: int, limit: int = 10):
    """Historique des contrats IA d'une entreprise (les plus récents d'abord)."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM ai_contracts WHERE company_id = ? ORDER BY created_at DESC LIMIT ?",
            (company_id, limit),
        ).fetchall()


def complete_ai_contract(ai_contract_id: int) -> None:
    """Marque un contrat IA comme complété (et verse la récompense côté appelant)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE ai_contracts SET status = 'completed', completed_at = ? WHERE ai_contract_id = ?",
            (int(time.time()), ai_contract_id),
        )


def expire_stale_ai_contracts(max_age_seconds: int) -> int:
    """
    Marque comme 'expired' les contrats IA actifs trop vieux, avant d'en
    générer de nouveaux lors du prochain cycle. Renvoie le nombre expiré.
    """
    cutoff = int(time.time()) - max_age_seconds
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE ai_contracts SET status = 'expired' WHERE status = 'active' AND created_at < ?",
            (cutoff,),
        )
        return cur.rowcount


def get_ai_contract_stats(since_seconds: int = 86400) -> dict:
    """
    Stats sur les contrats générés récemment (utile pour surveiller si le
    quota Gemini est souvent dépassé : proportion 'fallback' vs 'ai').
    """
    cutoff = int(time.time()) - since_seconds
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT source, COUNT(*) as n FROM ai_contracts WHERE created_at >= ? GROUP BY source",
            (cutoff,),
        ).fetchall()
    return {r["source"]: r["n"] for r in rows}


def get_state_treasury() -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM state_treasury WHERE id = 1").fetchone()
        return row["balance"] if row else 0


def add_state_treasury(amount: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE state_treasury SET balance = balance + ? WHERE id = 1", (amount,))


def get_all_companies():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM companies ORDER BY treasury DESC").fetchall()


def get_companies_by_sector(sector: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM companies WHERE sector = ? COLLATE NOCASE ORDER BY treasury DESC",
            (sector,),
        ).fetchall()


def increment_cmd_count(user_id: int) -> None:
    """Incrémente le compteur de commandes d'un joueur, avec une limite anti-spam
    d'1 commande comptabilisée par seconde (évite de gonfler le compteur en
    spammant des commandes sans rapport, notamment pour les contrats Bureau)."""
    now = int(time.time())
    with get_conn() as conn:
        conn.execute(
            "UPDATE players SET cmd_count = cmd_count + 1, last_cmd_count_at = ? "
            "WHERE user_id = ? AND last_cmd_count_at < ?",
            (now, user_id, now),
        )


def get_or_init_member_baseline(bc_id: int, user_id: int, current_cmd_count: int) -> int:
    """
    Renvoie le point de départ ("baseline") de commandes de cet employé pour
    cette mission Bureau précise. S'il n'en a pas encore (il vient de
    rejoindre l'entreprise APRÈS la création de la mission), on lui en crée
    un tout de suite basé sur son compteur ACTUEL — pour qu'on ne compte que
    les commandes qu'il fait À PARTIR DE MAINTENANT, pas tout son historique
    d'avant (qui pouvait venir d'une autre entreprise, ou d'aucune).
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT baseline FROM bc_member_baseline WHERE bc_id = ? AND user_id = ?",
            (bc_id, user_id),
        ).fetchone()
        if row is not None:
            return row["baseline"]
        conn.execute(
            "INSERT OR IGNORE INTO bc_member_baseline (bc_id, user_id, baseline) VALUES (?, ?, ?)",
            (bc_id, user_id, current_cmd_count),
        )
        return current_cmd_count


def delete_member_baselines(bc_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM bc_member_baseline WHERE bc_id = ?", (bc_id,))


def get_bureau_contracts(company_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM bureau_contracts WHERE company_id = ? ORDER BY created_at DESC",
            (company_id,)
        ).fetchall()


def create_bureau_contract(company_id: int, sector: str, task: str, target_cmd: int, reward: int, baseline_cmd: int = 0, base_target: int = 0, reward_rate: float = 0.0) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO bureau_contracts (company_id, sector, task, target_cmd, reward, progress, status, created_at, baseline_cmd, base_target, reward_rate)
               VALUES (?, ?, ?, ?, ?, 0, 'active', ?, ?, ?, ?)""",
            (company_id, sector, task, target_cmd, reward, int(time.time()), baseline_cmd, base_target, reward_rate)
        )
        return cur.lastrowid


def update_bureau_contract_progress(bc_id: int, progress: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE bureau_contracts SET progress = ? WHERE bc_id = ?", (progress, bc_id))


def claim_bureau_contract(bc_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE bureau_contracts SET status = 'claimed', claimed_at = ? WHERE bc_id = ?",
            (int(time.time()), bc_id)
        )


def register_group(chat_id: int, chat_title: str) -> None:
    """Enregistre ou met à jour un groupe actif."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO active_groups (chat_id, chat_title, last_seen)
               VALUES (?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET chat_title = excluded.chat_title, last_seen = excluded.last_seen""",
            (chat_id, chat_title or "", int(time.time()))
        )


def get_company_buildings(company_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM company_buildings WHERE company_id = ?", (company_id,)
        ).fetchall()


def get_company_building(company_id: int, slot: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM company_buildings WHERE company_id = ? AND slot = ?",
            (company_id, slot),
        ).fetchone()


def add_company_building(company_id: int, slot: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO company_buildings (company_id, slot, purchased_at, suspended) "
            "VALUES (?, ?, ?, 0)",
            (company_id, slot, int(time.time())),
        )


def set_building_suspended(company_id: int, slot: str, suspended: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE company_buildings SET suspended = ? WHERE company_id = ? AND slot = ?",
            (1 if suspended else 0, company_id, slot),
        )


def get_owned_company(user_id: int):
    """Renvoie l'entreprise dont ce joueur est PDG (companies.ceo_id), indépendamment
    d'où il travaille actuellement (player.company_id). Un PDG peut désormais aussi
    être employé ailleurs tout en restant propriétaire de sa propre entreprise."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM companies WHERE ceo_id = ?", (user_id,)
        ).fetchone()


def get_biens_joueur(user_id: int):
    """Renvoie tous les biens immobiliers possédés par ce joueur."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM biens WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()


def compter_biens(user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM biens WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["c"]


def acheter_bien(user_id: int, type_bien: str, prix: int) -> bool:
    """
    Achète un bien immobilier de façon ATOMIQUE : vérification du solde et
    déduction dans la même requête SQL conditionnelle (même pattern que
    withdraw_from_treasury), pour éviter qu'un double-clic ne fasse acheter
    deux biens avec un seul paiement.
    Retourne True si l'achat a réussi, False si solde insuffisant.
    """
    now = int(time.time())
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE players SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
            (prix, user_id, prix),
        )
        if cur.rowcount == 0:
            return False
        conn.execute(
            "INSERT INTO biens (user_id, type_bien, dernier_loyer, purchased_at) VALUES (?, ?, ?, ?)",
            (user_id, type_bien, now, now),
        )
        return True


def vendre_bien(user_id: int, bien_id: int, prix_revente: int) -> bool:
    """
    Vend un bien de façon ATOMIQUE : le DELETE vérifie lui-même que le bien
    appartient bien à user_id (WHERE id = ? AND user_id = ?). Si rowcount == 0,
    soit le bien n'existe pas, soit il n'appartient pas à ce joueur : dans les
    deux cas on ne crédite rien.
    """
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM biens WHERE id = ? AND user_id = ?", (bien_id, user_id)
        )
        if cur.rowcount == 0:
            return False
        conn.execute(
            "UPDATE players SET balance = balance + ? WHERE user_id = ?",
            (prix_revente, user_id),
        )
        return True


def collecter_loyers_db(user_id: int, types_biens: dict):
    """
    Calcule et verse les loyers accumulés sur tous les biens du joueur depuis
    leur dernière collecte (ou leur achat). Pour chaque bien, le nombre
    d'intervalles pleins écoulés est calculé, et dernier_loyer est avancé
    d'exactement (nb_intervalles * intervalle) — pas remis à `now` — pour ne
    pas perdre le reliquat de temps entre deux collectes.

    Si le joueur est PDG d'une entreprise (companies.ceo_id), les loyers sont
    versés dans la trésorerie de cette entreprise plutôt que sur son solde
    personnel.

    Retourne (total: int, details: list[str], company_name: str | None).
    """
    now = int(time.time())
    total = 0
    details = []

    with get_conn() as conn:
        biens = conn.execute(
            "SELECT * FROM biens WHERE user_id = ?", (user_id,)
        ).fetchall()

        for bien in biens:
            infos = types_biens.get(bien["type_bien"])
            if not infos:
                continue
            intervalle = infos["intervalle"]
            elapsed = now - bien["dernier_loyer"]
            nb_intervalles = elapsed // intervalle
            if nb_intervalles <= 0:
                continue

            gain = nb_intervalles * infos["loyer"]
            total += gain
            details.append(f"{infos['nom']} #{bien['id']} — +{gain}€")

            nouveau_dernier_loyer = bien["dernier_loyer"] + nb_intervalles * intervalle
            conn.execute(
                "UPDATE biens SET dernier_loyer = ? WHERE id = ?",
                (nouveau_dernier_loyer, bien["id"]),
            )

        if total == 0:
            return 0, [], None

        company = conn.execute(
            "SELECT * FROM companies WHERE ceo_id = ?", (user_id,)
        ).fetchone()

        if company:
            conn.execute(
                "UPDATE companies SET treasury = treasury + ? WHERE company_id = ?",
                (total, company["company_id"]),
            )
            company_name = company["name"]
        else:
            conn.execute(
                "UPDATE players SET balance = balance + ? WHERE user_id = ?",
                (total, user_id),
            )
            company_name = None

    return total, details, company_name


def get_active_groups() -> list:
    """Retourne tous les groupes actifs enregistrés."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT chat_id, chat_title FROM active_groups ORDER BY last_seen DESC"
        ).fetchall()


def create_recruitment_ad(company_id: int, poste: str, from_user: int) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO pending_recruitment_ads (company_id, poste, from_user, created_at)
               VALUES (?, ?, ?, ?)""",
            (company_id, poste, from_user, int(time.time())),
        )
        return cur.lastrowid


def get_recruitment_ad(ad_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM pending_recruitment_ads WHERE ad_id = ?", (ad_id,)
        ).fetchone()


def delete_recruitment_ad(ad_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM pending_recruitment_ads WHERE ad_id = ?", (ad_id,))


def set_company_last_recruitment_ad(company_id: int, timestamp: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET last_recruitment_ad = ? WHERE company_id = ?",
            (timestamp, company_id),
        )


# ============================================================
# COMPTES BANCAIRES MULTIPLES
# ============================================================

def create_bank_account(user_id: int, bank_name: str) -> bool:
    """Crée un nouveau compte bancaire pour un utilisateur dans une banque spécifique."""
    try:
        now = int(time.time())
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO user_bank_accounts (user_id, bank_name, balance, last_interest_at, created_at)
                   VALUES (?, ?, 0, ?, ?)""",
                (user_id, bank_name, now, now)
            )
            return True
    except sqlite3.IntegrityError:
        # Un compte existe déjà pour cette banque
        return False


def get_user_bank_accounts(user_id: int) -> list:
    """Récupère tous les comptes bancaires d'un utilisateur."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT account_id, bank_name, balance, last_interest_at, created_at FROM user_bank_accounts WHERE user_id = ? ORDER BY bank_name",
            (user_id,)
        ).fetchall()


def get_bank_account(user_id: int, bank_name: str):
    """Récupère un compte bancaire spécifique."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT account_id, bank_name, balance, last_interest_at FROM user_bank_accounts WHERE user_id = ? AND bank_name = ?",
            (user_id, bank_name)
        ).fetchone()


def deposit_to_bank(user_id: int, bank_name: str, amount: int) -> bool:
    """
    Dépose de l'argent du portefeuille vers un compte bancaire.
    Retourne True si succès, False sinon.
    """
    if amount <= 0:
        return False
    
    now = int(time.time())
    with get_conn() as conn:
        # Vérifier que le compte existe
        account = conn.execute(
            "SELECT account_id, last_interest_at FROM user_bank_accounts WHERE user_id = ? AND bank_name = ?",
            (user_id, bank_name)
        ).fetchone()
        
        if not account:
            return False
        
        # Vérifier et retirer du portefeuille
        conn.execute(
            "UPDATE players SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
            (amount, user_id, amount)
        )
        
        if conn.total_changes == 0:
            return False  # Solde insuffisant
        
        # Ajouter au compte bancaire
        conn.execute(
            "UPDATE user_bank_accounts SET balance = balance + ?, last_interest_at = ? WHERE user_id = ? AND bank_name = ?",
            (amount, now, user_id, bank_name)
        )
        
        return True


def withdraw_from_bank(user_id: int, bank_name: str, amount: int) -> bool:
    """
    Retire de l'argent d'un compte bancaire vers le portefeuille.
    Retourne True si succès, False sinon.
    """
    if amount <= 0:
        return False
    
    now = int(time.time())
    with get_conn() as conn:
        # Vérifier et retirer du compte bancaire
        conn.execute(
            "UPDATE user_bank_accounts SET balance = balance - ?, last_interest_at = ? WHERE user_id = ? AND bank_name = ? AND balance >= ?",
            (amount, now, user_id, bank_name, amount)
        )
        
        if conn.total_changes == 0:
            return False  # Solde insuffisant ou compte inexistant
        
        # Ajouter au portefeuille
        conn.execute(
            "UPDATE players SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id)
        )
        
        return True


def get_user_total_bank_balance(user_id: int) -> int:
    """Retourne le solde total de tous les comptes bancaires d'un utilisateur."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(balance), 0) as total FROM user_bank_accounts WHERE user_id = ?",
            (user_id,)
        ).fetchone()
    return row["total"] if row else 0


def transfer_between_banks(user_id: int, from_bank: str, to_bank: str, amount: int) -> bool:
    """
    Transfère de l'argent entre deux comptes bancaires du même utilisateur.
    """
    if amount <= 0 or from_bank == to_bank:
        return False
    
    now = int(time.time())
    with get_conn() as conn:
        # Vérifier que les deux comptes existent
        accounts = conn.execute(
            "SELECT bank_name FROM user_bank_accounts WHERE user_id = ? AND bank_name IN (?, ?)",
            (user_id, from_bank, to_bank)
        ).fetchall()
        
        if len(accounts) != 2:
            return False
        
        # Retirer du compte source
        conn.execute(
            "UPDATE user_bank_accounts SET balance = balance - ?, last_interest_at = ? WHERE user_id = ? AND bank_name = ? AND balance >= ?",
            (amount, now, user_id, from_bank, amount)
        )
        
        if conn.total_changes == 0:
            return False
        
        # Ajouter au compte destination
        conn.execute(
            "UPDATE user_bank_accounts SET balance = balance + ?, last_interest_at = ? WHERE user_id = ? AND bank_name = ?",
            (amount, now, user_id, to_bank)
        )
        
        return True


def get_bank_accounts_by_name(bank_name: str) -> list:
    """Récupère tous les comptes d'une banque spécifique."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT user_id, balance FROM user_bank_accounts 
               WHERE bank_name = ? ORDER BY balance DESC""",
            (bank_name,)
        ).fetchall()


def get_bank_statistics(bank_name: str) -> dict:
    """Récupère des statistiques sur une banque."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) as total_accounts, COALESCE(SUM(balance), 0) as total_balance,
               COALESCE(AVG(balance), 0) as avg_balance
               FROM user_bank_accounts WHERE bank_name = ?""",
            (bank_name,)
        ).fetchone()
    
    return {
        "total_accounts": row["total_accounts"],
        "total_balance": row["total_balance"],
        "avg_balance": row["avg_balance"]
    } if row else {"total_accounts": 0, "total_balance": 0, "avg_balance": 0}

# ============================================================
# SYSTÈME DE VILLES / MAIRIES
# ============================================================

def get_all_cities():
    """Retourne les 15 villes jouables avec leur état actuel (maire, caisse, taux)."""
    with get_conn() as conn:
        return conn.execute("SELECT * FROM cities ORDER BY name").fetchall()


def get_city(name: str):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM cities WHERE name = ?", (name,)).fetchone()


def get_city_by_mayor(user_id: int):
    """Renvoie la ville dont l'utilisateur est actuellement maire, s'il y en a une."""
    with get_conn() as conn:
        return conn.execute("SELECT * FROM cities WHERE mayor_id = ?", (user_id,)).fetchone()


def set_player_city(user_id: int, city: str, timestamp: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE players SET city = ?, city_changed_at = ? WHERE user_id = ?",
            (city, timestamp, user_id),
        )


def get_city_residents(city: str):
    """Tous les joueurs résidant dans une ville donnée."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT user_id, username, first_name, salary, balance FROM players WHERE city = ?",
            (city,),
        ).fetchall()


def set_city_tax_rate(city: str, rate: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE cities SET tax_rate = ? WHERE name = ?", (rate, city))


def add_city_treasury(city: str, amount: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE cities SET treasury = treasury + ? WHERE name = ?", (amount, city))


def withdraw_city_treasury(city: str, amount: int) -> bool:
    """Retire de l'argent de la caisse municipale si le solde le permet."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE cities SET treasury = treasury - ? WHERE name = ? AND treasury >= ?",
            (amount, city, amount),
        )
        return conn.total_changes > 0


def install_mayor(city: str, user_id: int, timestamp: int) -> None:
    """Installe un maire pour un mandat plein (2 semaines fixes) et log l'évènement."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE cities SET mayor_id = ?, mandate_start = ?, mandate_end = ?
               WHERE name = ?""",
            (user_id, timestamp, timestamp + MAYOR_MANDATE_DURATION, city),
        )
        conn.execute(
            "INSERT INTO mayor_history (city, user_id, event, amount, created_at) VALUES (?, ?, 'elu', 0, ?)",
            (city, user_id, timestamp),
        )


def remove_mayor(city: str, event: str, amount: int, timestamp: int) -> None:
    """Retire le maire en poste (destitution, fin de mandat, révocation owner...)."""
    with get_conn() as conn:
        row = conn.execute("SELECT mayor_id FROM cities WHERE name = ?", (city,)).fetchone()
        mayor_id = row["mayor_id"] if row else None
        conn.execute(
            "UPDATE cities SET mayor_id = NULL, mandate_start = 0, mandate_end = 0 WHERE name = ?",
            (city,),
        )
        conn.execute(
            "INSERT INTO mayor_history (city, user_id, event, amount, created_at) VALUES (?, ?, ?, ?, ?)",
            (city, mayor_id, event, amount, timestamp),
        )


def get_mayors_with_expired_mandate(timestamp: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM cities WHERE mayor_id IS NOT NULL AND mandate_end > 0 AND mandate_end <= ?",
            (timestamp,),
        ).fetchall()


# ── Élections municipales ──────────────────────────────────────────────

def open_election(city: str, timestamp: int) -> int:
    """Ouvre une nouvelle élection pour une ville (annule d'abord toute élection encore ouverte)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE mayor_elections SET status = 'annulee' WHERE city = ? AND status = 'ouverte'",
            (city,),
        )
        cur = conn.execute(
            """INSERT INTO mayor_elections (city, status, opened_at, closes_at)
               VALUES (?, 'ouverte', ?, ?)""",
            (city, timestamp, timestamp + MAYOR_ELECTION_PERIOD),
        )
        return cur.lastrowid


def get_open_election(city: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM mayor_elections WHERE city = ? AND status = 'ouverte' ORDER BY election_id DESC LIMIT 1",
            (city,),
        ).fetchone()


def get_pending_election(city: str):
    """Élection encore 'ouverte' (vote en cours) OU 'votes_clos' (vote clôturé par
    la commission, en attente de décision de l'owner). Utilisé par /trancherelection
    pour que l'owner puisse trancher même après clôture du vote."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM mayor_elections
               WHERE city = ? AND status IN ('ouverte', 'votes_clos')
               ORDER BY election_id DESC LIMIT 1""",
            (city,),
        ).fetchone()


def close_election_voting(election_id: int, timestamp: int) -> None:
    """Clôture le vote (plus personne ne peut voter/candidater) sans désigner de
    vainqueur : seul l'owner tranche ensuite via /trancherelection."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE mayor_elections SET status = 'votes_clos', closes_at = ? WHERE election_id = ? AND status = 'ouverte'",
            (timestamp, election_id),
        )


def get_election(election_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM mayor_elections WHERE election_id = ?", (election_id,)
        ).fetchone()


def add_candidate(election_id: int, user_id: int, timestamp: int) -> bool:
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO mayor_candidates (election_id, user_id, created_at) VALUES (?, ?, ?)",
                (election_id, user_id, timestamp),
            )
        return True
    except sqlite3.IntegrityError:
        return False  # déjà candidat


def remove_candidate(election_id: int, user_id: int) -> bool:
    """Retire un candidat d'une élection (retrait volontaire de candidature).
    Supprime aussi les votes déjà reçus par ce candidat sur cette élection."""
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM mayor_candidates WHERE election_id = ? AND user_id = ?",
            (election_id, user_id),
        )
        conn.execute(
            "DELETE FROM mayor_votes WHERE election_id = ? AND candidate_id = ?",
            (election_id, user_id),
        )
        return cur.rowcount > 0


def get_candidates(election_id: int):
    with get_conn() as conn:
        return conn.execute(
            """SELECT mc.user_id, p.username, p.first_name
               FROM mayor_candidates mc
               LEFT JOIN players p ON p.user_id = mc.user_id
               WHERE mc.election_id = ?""",
            (election_id,),
        ).fetchall()


def is_candidate(election_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM mayor_candidates WHERE election_id = ? AND user_id = ?",
            (election_id, user_id),
        ).fetchone()
        return row is not None


def cast_vote(election_id: int, voter_id: int, candidate_id: int, timestamp: int) -> None:
    """Un joueur ne peut avoir qu'un vote actif par élection ; revoter remplace le vote précédent."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO mayor_votes (election_id, voter_id, candidate_id, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(election_id, voter_id)
               DO UPDATE SET candidate_id = excluded.candidate_id, created_at = excluded.created_at""",
            (election_id, voter_id, candidate_id, timestamp),
        )


def get_vote_counts(election_id: int):
    """Décompte des voix par candidat (indicatif : c'est l'owner qui tranche au final)."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT mc.user_id, p.username, p.first_name, COUNT(mv.voter_id) as votes
               FROM mayor_candidates mc
               LEFT JOIN players p ON p.user_id = mc.user_id
               LEFT JOIN mayor_votes mv ON mv.candidate_id = mc.user_id AND mv.election_id = mc.election_id
               WHERE mc.election_id = ?
               GROUP BY mc.user_id
               ORDER BY votes DESC""",
            (election_id,),
        ).fetchall()


def close_election(election_id: int, winner_id: int, timestamp: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE mayor_elections SET status = 'tranchee', winner_id = ?, decided_at = ? WHERE election_id = ?",
            (winner_id, timestamp, election_id),
        )


def get_latest_election(city: str):
    """Dernière élection en date pour une ville, quel que soit son statut
    (ouverte, votes_clos, tranchee, annulee) — utilisé par /votesmaire pour
    afficher le détail des votes même après clôture."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM mayor_elections WHERE city = ? ORDER BY election_id DESC LIMIT 1",
            (city,),
        ).fetchone()


def get_election_votes_detail(election_id: int):
    """Détail vote par vote (qui a voté pour qui) pour une élection donnée.
    Les votes ne sont jamais supprimés à la clôture, donc ça marche aussi
    pour consulter une élection déjà tranchée."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT mv.voter_id, voter.username AS voter_username, voter.first_name AS voter_first_name,
                      mv.candidate_id, cand.username AS candidate_username, cand.first_name AS candidate_first_name,
                      mv.created_at
               FROM mayor_votes mv
               LEFT JOIN players voter ON voter.user_id = mv.voter_id
               LEFT JOIN players cand ON cand.user_id = mv.candidate_id
               WHERE mv.election_id = ?
               ORDER BY mv.candidate_id, mv.created_at""",
            (election_id,),
        ).fetchall()


def get_mayor_history(city: str, limit: int = 10):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM mayor_history WHERE city = ? ORDER BY log_id DESC LIMIT ?",
            (city, limit),
        ).fetchall()


def set_election_commission(user_id: int, timestamp: int) -> None:
    """Nomme (ou remplace) le président de la commission électorale."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO election_commission (id, user_id, appointed_at) VALUES (1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET user_id = excluded.user_id, appointed_at = excluded.appointed_at""",
            (user_id, timestamp),
        )


def get_election_commission():
    """Renvoie l'user_id du président de la commission électorale, ou None."""
    with get_conn() as conn:
        row = conn.execute("SELECT user_id FROM election_commission WHERE id = 1").fetchone()
        return row["user_id"] if row else None


def remove_election_commission() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM election_commission WHERE id = 1")
