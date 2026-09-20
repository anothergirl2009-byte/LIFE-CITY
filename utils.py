"""
LifeCity Bot - Fonctions utilitaires partagées
"""

from config import CURRENCY


def fmt_money(amount: int) -> str:
    """Formate un montant avec le symbole €.
    En dessous d'1 million : chiffres complets avec séparateur de milliers
    (ex: 500 000 €). À partir d'1 million : notation compacte K/M/B/T
    (ex: 1.5M €, 10B €, 2.3T €) — sinon les gros montants deviennent
    illisibles à force d'accumuler des zéros."""
    amount = float(amount)
    sign = "-" if amount < 0 else ""
    abs_amount = abs(amount)

    for unit, divisor in (("T", 1_000_000_000_000), ("B", 1_000_000_000), ("M", 1_000_000)):
        if abs_amount >= divisor:
            value = abs_amount / divisor
            formatted = f"{value:,.2f}".rstrip("0").rstrip(".")
            return f"{sign}{formatted}{unit} {CURRENCY}"

    return f"{sign}{abs_amount:,.0f}".replace(",", " ") + f" {CURRENCY}"


def fmt_duration(seconds: int) -> str:
    """Formate une durée en secondes vers un texte lisible (ex: 2h 15min)."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}min")
    if not hours and not minutes:
        parts.append(f"{secs}s")
    return " ".join(parts)


def progress_bar(value: int, max_value: int = 100, length: int = 10) -> str:
    """Construit une barre de progression du style [███████░░░]."""
    if max_value <= 0:
        max_value = 1
    ratio = max(0.0, min(1.0, value / max_value))
    filled = round(ratio * length)
    filled = max(0, min(length, filled))
    return "[" + "█" * filled + "░" * (length - filled) + "]"


def karma_title(karma: int) -> str:
    """Retourne le titre correspondant au niveau de karma du joueur."""
    if karma >= 80:
        return "Vénéré"
    if karma >= 50:
        return "Respecté"
    if karma >= 20:
        return "Apprécié"
    if karma >= 0:
        return "Neutre"
    if karma >= -50:
        return "Méprisé"
    return "Maudit"


def fmt_big_money(amount: int) -> str:
    """Formate un grand montant en notation compacte (ex: 9863.26B)."""
    amount = float(amount)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    for unit, divisor in (("T", 1_000_000_000_000), ("B", 1_000_000_000),
                           ("M", 1_000_000), ("K", 1_000)):
        if amount >= divisor:
            return f"{sign}{amount / divisor:,.2f}{unit}".replace(",", " ")
    return f"{sign}{amount:,.0f}".replace(",", " ")


def escape_markdown(text: str) -> str:
    """
    Échappe les caractères spéciaux Markdown pour éviter les erreurs de parsing.
    """
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text


def safe_send_message(update, text: str, parse_mode: str = "Markdown") -> None:
    """
    Envoie un message de manière sécurisée en gérant les erreurs de parsing.
    """
    try:
        update.message.reply_text(text, parse_mode=parse_mode)
    except Exception as e:
        # Si le Markdown pose problème, on envoie sans formatage
        if "Can't parse entities" in str(e):
            update.message.reply_text(text, parse_mode=None)
        else:
            raise e