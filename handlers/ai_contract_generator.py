"""
LifeCity Bot - Génération de contrats via API IA (Gemini ou autre)
-------------------------------------------------------------------

Ce module remplace un appel direct à l'API par un appel "sécurisé" :
- retry automatique avec backoff exponentiel si l'API renvoie une erreur
  de quota / rate limit (429) ou une erreur réseau ponctuelle,
- timeout pour ne jamais bloquer la boucle du bot indéfiniment,
- fallback sur un contrat généré localement (sans IA) si l'API échoue
  après tous les essais, pour que la tâche planifiée ne "saute" jamais
  silencieusement un cycle.

Comment l'utiliser :
    1. Remplace ton appel Gemini actuel par un appel à generate_contract_text().
    2. Dans ta tâche planifiée (celle qui tourne à intervalle régulier),
       appelle generate_contracts_for_all_companies(companies) une seule
       fois par cycle, elle s'occupe de tout (log si erreur, fallback, etc).
"""

import asyncio
import logging
import random
import time

try:
    import config
    MAX_RETRIES = getattr(config, "AI_CONTRACT_MAX_RETRIES", 3)
    BASE_BACKOFF_SECONDS = getattr(config, "AI_CONTRACT_BASE_BACKOFF_SECONDS", 2)
    REQUEST_TIMEOUT_SECONDS = getattr(config, "AI_CONTRACT_TIMEOUT_SECONDS", 15)
except ImportError:
    # Pas de config.py trouvé : valeurs par défaut, rien ne casse.
    MAX_RETRIES = 3
    BASE_BACKOFF_SECONDS = 2
    REQUEST_TIMEOUT_SECONDS = 15

logger = logging.getLogger("ai_contract_generator")

# ============================================================
# CONFIG - les valeurs ci-dessus viennent de config.py si présent
# (voir config_ai_contracts_example.py pour la liste complète des
# paramètres à y ajouter : clé API, intervalle, délai entre appels...)
# ============================================================

# Message d'erreurs qui indiquent un quota / rate limit dépassé (à adapter
# selon les messages exacts renvoyés par l'API que tu utilises)
RATE_LIMIT_MARKERS = ("429", "rate limit", "quota", "resource_exhausted")


class AIProviderError(Exception):
    """Erreur générique remontée par le fournisseur d'API (Gemini, GPT, etc)."""
    pass


def _is_rate_limit_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in RATE_LIMIT_MARKERS)


async def _call_ai_api(prompt: str) -> str:
    """
    ⚠️ C'est ICI qu'il faut brancher ton appel réel à Gemini (ou GPT).
    Remplace le contenu de cette fonction par ton appel API existant,
    en gardant la signature (prend un prompt, renvoie le texte généré).

    Exemple avec Gemini (google-generativeai), clé lue depuis config.py :

        import google.generativeai as genai
        genai.configure(api_key=config.GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text

    Exemple avec l'API OpenAI (si tu passes sur un plan payant) :

        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content
    """
    raise NotImplementedError("Branche ton appel API réel ici (voir docstring).")


async def generate_contract_text(prompt: str) -> str | None:
    """
    Appelle l'API IA avec retry + backoff exponentiel + jitter en cas
    d'erreur de quota/réseau, et timeout pour ne jamais bloquer le bot.

    Retourne le texte généré, ou None si tous les essais ont échoué
    (dans ce cas, utilise generate_fallback_contract() pour ne pas
    laisser l'entreprise sans contrat ce cycle-ci).
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = await asyncio.wait_for(
                _call_ai_api(prompt), timeout=REQUEST_TIMEOUT_SECONDS
            )
            return result
        except asyncio.TimeoutError:
            logger.warning(f"[IA contrats] Timeout (essai {attempt}/{MAX_RETRIES})")
        except Exception as e:
            if _is_rate_limit_error(e):
                logger.warning(
                    f"[IA contrats] Quota/rate limit dépassé (essai {attempt}/{MAX_RETRIES}) : {e}"
                )
            else:
                logger.error(f"[IA contrats] Erreur API (essai {attempt}/{MAX_RETRIES}) : {e}")

        if attempt < MAX_RETRIES:
            # Backoff exponentiel + jitter pour éviter que toutes les
            # entreprises retentent exactement au même moment.
            delay = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 1)
            await asyncio.sleep(delay)

    logger.error("[IA contrats] Échec définitif après tous les essais, fallback utilisé.")
    return None


# ============================================================
# FALLBACK - contrat générique si l'IA est indisponible
# ============================================================

FALLBACK_TEMPLATES = [
    "Contrat de fourniture standard entre {from_name} et {to_name} : livraison de "
    "services dans les délais convenus, en échange de {reward}.",
    "Accord de partenariat commercial : {from_name} fournit à {to_name} une "
    "prestation de secteur, rémunérée à {reward}.",
    "Contrat-cadre {from_name} / {to_name} : engagement de collaboration "
    "ponctuelle, valorisé à {reward}.",
]


def generate_fallback_contract(from_name: str, to_name: str, reward: int) -> str:
    """Contrat texte généré localement (sans IA), utilisé si l'API a échoué."""
    template = random.choice(FALLBACK_TEMPLATES)
    return template.format(from_name=from_name, to_name=to_name, reward=f"{reward:,}€".replace(",", " "))


# ============================================================
# BOUCLE PRINCIPALE - un cycle de génération pour toutes les entreprises
# ============================================================

async def generate_contracts_for_all_companies(
    companies: list,
    build_prompt,
    create_contract_fn,
    delay_between_calls: float = 1.5,
) -> dict:
    """
    Lance un cycle de génération de contrats pour une liste d'entreprises.

    Args:
        companies: liste des entreprises (ex: db.get_all_companies()).
        build_prompt: fonction (company) -> str, qui construit le prompt.
        create_contract_fn: fonction (company, contract_text, source) -> None,
            qui enregistre le contrat en DB. `source` vaut "ai" ou
            "fallback" selon que le texte vient de l'API ou du secours
            local — pratique pour surveiller si le quota est souvent
            dépassé (voir db.get_ai_contract_stats).
        delay_between_calls: pause entre deux entreprises, pour rester
            sous le quota même quand il y a beaucoup d'entreprises
            (ex: quota Gemini gratuit ~15 requêtes/minute -> 1.5s+ entre
            appels pour ne jamais le taper).

    Retourne un résumé {"ok": n, "fallback": n, "errors": n} pour que tu
    puisses logger/monitorer sans que les échecs restent invisibles.
    """
    stats = {"ok": 0, "fallback": 0, "errors": 0}

    for company in companies:
        try:
            prompt = build_prompt(company)
            text = await generate_contract_text(prompt)

            if text is None:
                # Fallback : pas d'IA dispo, mais l'entreprise a quand même
                # un contrat ce cycle-ci (pas de silence total).
                text = generate_fallback_contract(
                    from_name=company["name"], to_name="un partenaire", reward=0
                )
                stats["fallback"] += 1
                source = "fallback"
            else:
                stats["ok"] += 1
                source = "ai"

            create_contract_fn(company, text, source)

        except Exception as e:
            logger.exception(f"[IA contrats] Erreur inattendue pour {company['name']} : {e}")
            stats["errors"] += 1

        # Espacement entre les entreprises pour respecter le quota,
        # même en cas de forte croissance du nombre d'entreprises.
        await asyncio.sleep(delay_between_calls)

    logger.info(f"[IA contrats] Cycle terminé : {stats}")
    return stats


# ============================================================
# EXEMPLE COMPLET ET FONCTIONNEL, branché sur les fonctions db.py
# (ai_contracts) créées pour ce système. Adapte build_prompt() au
# ton/format que tu veux pour tes contrats, le reste marche tel quel.
# ============================================================

def _build_prompt_example(company) -> str:
    """Exemple de prompt — adapte librement le texte."""
    return (
        f"Génère un contrat commercial court (3-4 phrases) pour l'entreprise "
        f"'{company['name']}', secteur {company['sector']}, trésorerie "
        f"actuelle {company['treasury']}€. Le contrat doit décrire une "
        f"mission ou un partenariat réaliste pour ce secteur, en français, "
        f"sans montant de récompense (le montant est géré séparément)."
    )


def _create_contract_example(company, contract_text: str, source: str = "ai") -> None:
    """
    Enregistre le contrat en DB via db.create_ai_contract(), et calcule une
    récompense proportionnelle à la trésorerie (adapte la formule à ton
    équilibrage économique).
    """
    import db  # import local pour éviter une dépendance circulaire

    reward = max(50_000, int((company["treasury"] or 0) * 0.02))
    db.create_ai_contract(company["company_id"], contract_text, reward, source=source)
    db.add_company_log(
        company["company_id"],
        f"📄 Nouveau contrat IA généré — récompense estimée {reward:,}€".replace(",", " "),
    )


async def run_ai_contract_cycle() -> dict:
    """
    Point d'entrée à appeler depuis ta tâche planifiée (JobQueue de
    python-telegram-bot, ou ton propre scheduler).

    Exemple d'intégration avec JobQueue (dans ton setup du bot) :

        from ai_contract_generator import run_ai_contract_cycle
        import config

        application.job_queue.run_repeating(
            lambda ctx: asyncio.create_task(run_ai_contract_cycle()),
            interval=config.AI_CONTRACT_INTERVAL_SECONDS,
            first=60,
        )
    """
    import db  # import local pour éviter une dépendance circulaire

    try:
        import config
        max_age = getattr(config, "AI_CONTRACT_MAX_AGE_SECONDS", 24 * 3600)
        delay = getattr(config, "AI_CONTRACT_DELAY_BETWEEN_CALLS", 1.5)
    except ImportError:
        max_age = 24 * 3600
        delay = 1.5

    # On expire les vieux contrats IA non complétés avant d'en générer
    # de nouveaux, pour ne pas empiler des contrats actifs indéfiniment.
    expired = db.expire_stale_ai_contracts(max_age)
    if expired:
        logger.info(f"[IA contrats] {expired} contrat(s) expiré(s) avant le nouveau cycle.")

    companies = db.get_all_companies()

    # On ne régénère pas de contrat pour une entreprise qui en a déjà un actif.
    companies_needing_contract = [
        c for c in companies if db.get_active_ai_contract(c["company_id"]) is None
    ]

    return await generate_contracts_for_all_companies(
        companies_needing_contract,
        build_prompt=_build_prompt_example,
        create_contract_fn=_create_contract_example,
        delay_between_calls=delay,
    )
