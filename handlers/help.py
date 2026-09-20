"""
LifeCity Bot - /help
Affiche la liste complète des commandes disponibles.
"""

from telegram import Update
from telegram.ext import ContextTypes

from config import CHANNEL_USERNAME


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Affiche la liste de toutes les commandes disponibles."""
    text = (
        "📖 *Commandes LifeCity*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "👤 *Profil & Économie*\n"
        "/start — Créer son compte\n"
        "/me — Voir son profil\n"
        "/acc — Voir son solde\n"
        "/daily — Bonus quotidien\n"
        "/work — Travailler\n"
        "/pay @user montant — Envoyer de l'argent\n"
        "/richlist — Top 10 des plus riches\n"
        "/leaderboard — 👑 Classement royal des familles\n"
        "/topactif — ⚡ Top 15 des joueurs les plus actifs\n\n"

        "🏦 *Banque*\n"
        "/bank — Liste des banques\n"
        "/openbank nom — Ouvrir un compte\n"
        "/depositbank montant — Déposer\n"
        "/withdrawbank montant — Retirer\n"
        "/balancebank — Solde bancaire\n"
        "/loanbank montant — Prêt bancaire\n"
        "/repaybank montant — Rembourser\n"
        "/loansbank — Prêts en cours\n\n"

        "👨‍👩‍👧 *Famille & Social*\n"
        "/marry @user — Demande en mariage\n"
        "/divorce — Divorcer\n"
        "/adopt @user — Adopter\n"
        "/disown @user — Désavouer\n"
        "/friend @user — Demande d'ami\n"
        "/unfriend @user — Retirer un ami\n"
        "/setfamilyname nom — Nom de famille\n"
        "/leave — Quitter sa famille\n"
        "/tree — Arbre généalogique\n\n"

        "🎓 *Éducation*\n"
        "/diplome — Voir les diplômes disponibles\n"
        "/diplome nom — Passer un diplôme\n\n"

        "🏢 *Entreprises*\n"
        "/creerboite nom secteur ville — Créer (50M€)\n"
        "/listeboites — Toutes les entreprises\n"
        "/infoboite nom — Infos entreprise\n"
        "/monentreprise — Ma boîte\n"
        "/postuler nom — Postuler\n"
        "/demissionner — Démissionner\n"
        "/recruter @user — Recruter\n"
        "/licencier @user — Licencier\n"
        "/nommer @user poste — Nommer\n"
        "/annoncerecrutement — Publier annonce\n"
        "/deplacerboite ville — Déplacer (50Mrd€)\n\n"

        "💰 *Finance Entreprise*\n"
        "/depotboite montant — Déposer caisse\n"
        "/retraitboite montant — Retirer caisse\n"
        "/logsboite — Historique caisse\n"
        "/parts — Répartition des parts\n"
        "/acheterparts nb — Acheter des parts\n"
        "/vendreparts nb — Vendre des parts\n"
        "/versersalaires — Verser salaires\n"
        "/presences — Rapport présences\n"
        "/classement — Top entreprises\n"
        "/proposercontrat — Proposer contrat\n"
        "/mescontrats — Mes contrats\n\n"

        "🎰 *Casino Solo*\n"
        "/slots montant — Machines à sous\n"
        "/roulette — Roulette\n"
        "/mines montant nb — Mines\n"
        "/crash montant — Crash\n"
        "/apple montant — Pomme de fortune\n"
        "/roue montant — Roue de la fortune\n"
        "/rebet — Rejouer le dernier pari\n\n"

        "🃏 *Casino PvP*\n"
        "/blackjack montant — Blackjack\n"
        "/cockfight montant — Combat de coqs\n"
        "/ppc montant — Pierre-papier-ciseaux\n"
        "/lancer montant — Dés\n\n"

        "🔫 *Crime*\n"
        "/steal @user — Voler\n"
        "/police — Porter plainte\n"
        "/bail — Payer caution\n"
        "/juge — Passer devant le juge\n"
        "/security niveau — Améliorer sécurité\n\n"

        "🏷️ *Enchères & Objets*\n"
        "/bid montant — Enchérir\n"
        "/myitems — Mes objets\n"
        "/sellitem id prix — Mettre en vente\n"
        "/shopitems — Boutique\n"
        "/buyitem id — Acheter\n"
        "/open — Ouvrir un coffre\n\n"

        "👉 Rejoins notre channel @LifeCitychannel"
    )
    await update.message.reply_text(text, parse_mode="Markdown")
