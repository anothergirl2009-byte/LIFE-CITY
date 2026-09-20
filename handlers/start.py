"""
LifeCity Bot - Commande /start
Message de bienvenue avec image + boutons inline (groupe, canal, devs).
"""

import os

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

WELCOME_IMAGE_PATH = os.path.join(os.path.dirname(__file__), "..", "welcome.jpg")
WELCOME_SOUND_PATH = os.path.join(os.path.dirname(__file__), "..", "welcome.m4a")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Réagit à /start avec le message de bienvenue LifeCity."""
    user = update.effective_user
    name = user.first_name or "Joueur"

    welcome_text = (
        f"🕸️ Bienvenue *{name}*, sur 𝗟𝗜𝗙𝗘 𝗖𝗜𝗧𝗬 🕷️🌆\n\n"
        "🕷️ *LifeCity ❤️* — Construis ta vie virtuelle !\n\n"
        "🏠 Achète ta maison et crée ton quartier.\n"
        "💼 Trouve un job et fais évoluer ta carrière.\n"
        "🚗 Collectionne des véhicules et explore la ville.\n"
        "💰 Gère ton argent, investis et deviens riche.\n"
        "🎓 Passe tes diplômes et débloque de nouvelles opportunités.\n\n"
        "⚠️ *Obligatoire* : rejoins notre groupe officiel et notre canal "
        "ci-dessous pour pouvoir jouer.\n\n"
        "📖 Tape /help pour voir toutes les commandes.\n"
        "🆕 Tape /nouveautes pour les dernières mises à jour.\n\n"
        "🕸️ _With great power comes great responsibility._ 🕸️"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "👥 Groupe officiel LifeCity",
                url="https://t.me/lifecity_anothergirl"
            )
        ],
        [
            InlineKeyboardButton(
                "📢 Canal LifeCity🕸️🕷️",
                url="https://t.me/lifeCitychannel"
            )
        ],
        [
            InlineKeyboardButton(
                "➕ Ajouter le bot à un autre groupe",
                url="https://t.me/LIFE_CITIZE_BOT?startgroup=true"
            )
        ],
        [
            InlineKeyboardButton("🕷️ Another girl🕸️", url="https://t.me/Anothergirl2000"),
        ],
    ])

    if os.path.exists(WELCOME_IMAGE_PATH):
        with open(WELCOME_IMAGE_PATH, "rb") as photo:
            await update.message.reply_photo(
                photo=photo,
                caption=welcome_text,
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
    else:
        await update.message.reply_text(
            welcome_text, reply_markup=keyboard, parse_mode="Markdown"
        )

    # Petit son d'ambiance envoyé juste après le message de bienvenue.
    # Place un fichier "welcome.mp3" à la racine du projet (à côté de
    # welcome.jpg) pour l'activer — sinon ça ne fait rien de spécial.
    if os.path.exists(WELCOME_SOUND_PATH):
        try:
            with open(WELCOME_SOUND_PATH, "rb") as audio:
                await update.message.reply_audio(audio=audio, title="LifeCity")
        except Exception:
            pass


async def nouveautes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Réagit à /nouveautes avec la liste des dernières mises à jour du bot."""
    text = (
        "🆕 *Dernières mises à jour de LifeCity*\n\n"
        "🏛️ *Mairie de LIFECITY & élections* — Tous les citoyens vivent "
        "désormais à LIFECITY. Présente-toi aux élections, vote, et si tu es "
        "élu, gère la caisse municipale !\n"
        "  👤 *Commandes joueur*\n"
        "  • */maville* — Fiche de la mairie (maire, impôt, caisse)\n"
        "  • */candidature* — Se présenter comme maire (PDG, 10 milliards requis)\n"
        "  • */retirercandidature* — Retirer sa propre candidature\n"
        "  • */candidats* — Voir les candidats et voter\n"
        "  • */voter <@joueur|id>* — Voter pour un candidat\n"
        "  • */historiquemaire* — Historique des maires\n"
        "  • */commission* — Voir qui est le président de la commission électorale\n"
        "  👑 *Commandes du maire élu*\n"
        "  • */fixerimpot <taux>* — Fixer le taux d'impôt (0-15%)\n"
        "  • */caissemairie* — Consulter la caisse municipale\n"
        "  • */retraitmairie <montant> [raison]* — Retirer de la caisse "
        "(destitution automatique en cas d'abus)\n"
        "  🛡️ *Commandes owner / admin*\n"
        "  • */ouvrirelection* — Ouvrir une élection (owner + commission)\n"
        "  • */cloturerelection* — Clôturer le vote sans trancher (owner + commission)\n"
        "  • */trancherelection <id_ou_pseudo>* — Désigner le maire élu (owner uniquement)\n"
        "  • */revoquermaire* — Révoquer le maire en poste (owner uniquement)\n"
        "  • */lancervote @c1 @c2 [...]* — Poster le vote à boutons (owner uniquement)\n"
        "  • */nommercommission <id_ou_pseudo>* — Nommer le président de la commission (owner)\n"
        "  • */retirercandidat <id_ou_pseudo>* — Retirer un candidat (owner + admin)\n\n"
        "🧑‍💼 */metier* — Choisis un métier (Avocat, Juge, Scientifique, "
        "Astronaute, Psychologue, Médecin, et bien d'autres) via un menu à "
        "boutons. Purement cosmétique, affiché sur ton profil /me, sans "
        "test à passer. Changeable une fois tous les 7 jours.\n\n"
        "🏗️ *Bâtiments d'entreprise* — Chaque PDG peut désormais acheter des "
        "bâtiments pour sa boîte avec /batiments (catalogue), /acheterbatiment "
        "et /mesbatiments. Chaque secteur a ses propres bâtiments thématiques, "
        "débloqués selon le niveau de l'entreprise (basé sur sa trésorerie) :\n"
        "  • 🪑 Salle de Réunion — réduit le délai de négociation\n"
        "  • 📦 Entrepôt — augmente la trésorerie max autorisée\n"
        "  • 🏛️ Siège Social — boost de réputation\n"
        "  • 🖥️ Datacenter — boost des revenus de contrats\n"
        "  • 🏭 Usine — boost des revenus journaliers\n"
        "  • 🏦 Agence Bancaire, 🔬 Campus R&D, 🗼 Tour de Contrôle — débloqués "
        "aux niveaux supérieurs\n"
        "⚠️ Chaque bâtiment a une maintenance quotidienne : si la trésorerie "
        "ne suit pas, il se suspend automatiquement (et se réactive tout seul "
        "dès que possible).\n\n"
        "💼 */investir <montant>* — Place 100k€ à 10M€ dans les caisses de "
        "l'État et récupère ton capital +8% après 24h. Tape /investir sans "
        "argument pour suivre ton placement.\n\n"
        "🎯 */defi* — Un défi aléatoire une fois par jour, avec un gain "
        "variable à la clé.\n\n"
        "📈 *Parts d'entreprise plus fiables* — Le prix d'une part reflète "
        "maintenant fidèlement la trésorerie réelle de l'entreprise.\n\n"
        "🏛️ *Fonds État renforcé* — Les entreprises dissoutes suite au "
        "retrait d'un diplôme de leur PDG voient leur trésorerie restante "
        "reversée au fonds État plutôt que perdue.\n\n"
        "📖 Tape /help pour la liste complète des commandes."
    )
    await update.message.reply_text(text, parse_mode="Markdown")
