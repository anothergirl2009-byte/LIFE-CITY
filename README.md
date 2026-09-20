🏙️ LifeCity Bot
Bot Telegram de simulation de vie urbaine avec économie virtuelle, entreprises, casino, famille et bien plus.
Toute la monnaie est fictive (€ LifeCity) — aucune valeur réelle, aucun achat in-app.
📁 Structure du projet
LifeCity/
│
├── main.py                  # Point d'entrée — lance le bot et enregistre tous les handlers
├── config.py                # Configuration centrale (token, paramètres économie, casino, etc.)
├── db.py                    # Base de données SQLite — toutes les fonctions CRUD
├── utils.py                 # Fonctions utilitaires partagées (ex: fmt_money)
├── init.py                  # Initialisation au démarrage
├── start.py                 # Handler /start — accueil du joueur
├── pause.py                 # 🆕 Gestion de la pause manuelle du bot
├── pause_state.json         # 🆕 Fichier d'état de la pause (auto-généré)
│
├── handlers/                # Un fichier par module fonctionnel
│   ├── profile.py           # /me — profil du joueur
│   ├── economy.py           # /acc /daily /work /pay /richlist
│   ├── bank.py              # /banks /openbank /depositbank /withdrawbank ...
│   ├── family.py            # /marry /adopt /friend /tree ...
│   ├── company.py           # /creerboite /postuler /recruter ...
│   ├── company_finance.py   # /depotboite /parts /versersalaires /proposercontrat ...
│   ├── casino_solo.py       # /slots /roulette /mines /crash /apple /roue
│   ├── casino_pvp.py        # /blackjack /cockfight /ppc /lancer
│   ├── crime.py             # /steal /police /bail /juge /security
│   ├── auctions.py          # /bid /myitems /sellitem /shopitems /open
│   ├── education.py         # /diplome
│   └── admin.py             # /owner /addmoney /ban /pause /resume ...
│
├── welcome.jpg              # Image d'accueil envoyée au /start
├── lifecity.db              # Base de données SQLite (auto-générée au 1er lancement)
└── Requirements.txt         # Dépendances Python
⚙️ Installation
1. Prérequis
Python 3.10 ou supérieur
Un bot Telegram créé via @BotFather
2. Cloner / décompresser le projet
unzip LifeCity.zip -d LifeCity
cd LifeCity
3. Installer les Dépendances
pip install -r Requirements.txt
Dépendance principale : python-telegram-bot>=20.0
4. Configurer le token
Dans config.py, remplace :
BOT_TOKEN = os.environ.get("BOT_TOKEN", "METS_TON_TOKEN_ICI")
Option A — variable d'environnement (recommandé) :
export BOT_TOKEN="ton_token_ici"
Option B — directement dans config.py :
BOT_TOKEN = "ton_token_ici"
5. Configurer l'owner
Dans config.py, remplace par ton ID Telegram :
OWNER_ID = 123456789  # Ton ID Telegram
Tu peux obtenir ton ID via @userinfobot
6. Lancer le bot
python main.py
🎮 Commandes disponibles
👤 Profil & Économie
Commande
Description
/start
Créer son compte et recevoir le message d'accueil
/me
Afficher son profil complet
/acc
Voir son solde
/daily
Réclamer sa récompense quotidienne (cooldown 24h)
/work
Travailler pour gagner des coins (cooldown 8h)
/pay @user montant
Envoyer de l'argent à un joueur
/richlist
Classement des joueurs les plus riches
🏦 Banque
Commande
Description
/banks
Liste des banques disponibles
/openbank nom
Ouvrir un compte dans une banque
/depositbank montant
Déposer de l'argent
/withdrawbank montant
Retirer de l'argent
/balancebank
Voir le solde bancaire
/loanbank montant
Contracter un prêt
/repaybank montant
Rembourser un prêt
/loansbank
Voir ses prêts en cours
👨‍👩‍👧 Famille & Social
Commande
Description
/marry @user
Demander en mariage
/acceptmarry
Accepter une demande en mariage
/refusemarry
Refuser une demande en mariage
/divorce
Divorcer
/adopt @user
Adopter un joueur
/acceptadopt / /refuseadopt
Répondre à une adoption
/disown @user
Désavouer un enfant adopté
/friend @user
Envoyer une demande d'ami
/acceptfriend / /refusefriend / /unfriend
Gérer ses amis
/setfamilyname nom
Définir le nom de famille
/leave
Quitter sa famille
/tree
Afficher l'arbre généalogique
🏢 Entreprises
Commande
Description
/creerboite nom secteur ville
Créer une entreprise (50M€)
/dissoudreboite
Dissoudre son entreprise
/listeboites
Lister toutes les entreprises
/infoboite nom
Infos sur une entreprise
/monentreprise
Infos sur sa propre entreprise
/employes
Voir ses employés
/postuler nom
Postuler dans une entreprise
/rejoindre
Rejoindre après acceptation
/demissionner
Démissionner
/candidatures
Voir les candidatures reçues
/accepter @user / /refuser @user
Gérer les candidatures
/recruter @user
Recruter directement
/nommer @user poste
Nommer un employé
/licencier @user
Licencier un employé
/annoncerecrutement
Publier une annonce
/deplacerboite ville
Déplacer l'entreprise (50Mrd€)
💰 Finance Entreprise
Commande
Description
/depotboite montant
Déposer dans la caisse
/retraitboite montant
Retirer de la caisse
/logsboite
Historique financier
/parts
Voir la répartition des parts
/acheterparts nb
Acheter des parts
/vendreparts nb
Vendre des parts
/versersalaires
Verser les salaires
/presences
Rapport de présences
/proposercontrat
Proposer un contrat inter-entreprises
/acceptercontrat id / /refusercontrat id
Gérer les contrats
/mescontrats
Voir ses contrats
/soumettredossier
Soumettre un dossier au BC
/mescontratsbc
Contrats Business Center
/claimcontratbc id
Réclamer un contrat BC
/classement
Classement des entreprises
🎓 Éducation
Commande
Description
/diplome nom
Passer un diplôme (Bac → Licence → Master → MBA)
🎰 Casino (Solo)
Commande
Description
/slots montant
Machine à sous
/roulette couleur/numéro montant
Roulette
/mines montant nb_mines
Jeu de mines
/crash montant
Crash game
/apple montant
Pomme de fortune
/roue montant
Roue de la fortune
/rebet
Rejouer le dernier pari
🃏 Casino (PvP)
Commande
Description
/blackjack montant
Blackjack
/cockfight montant
Combat de coqs
/ppc montant
Pierre-papier-ciseaux
/lancer montant
Lancer de dés
🔫 Crime
Commande
Description
/steal @user
Voler un joueur
/police
Porter plainte
/bail
Payer la caution
/juge
Passer devant le juge
/security niveau
Améliorer sa sécurité
🏷️ Enchères & Objets
Commande
Description
/bid id montant
Enchérir sur un objet
/myitems
Voir ses objets
/expertise id
Estimer la valeur d'un objet
/sellitem id prix
Mettre un objet en vente
/shopitems
Boutique d'objets
/buyitem id
Acheter un objet
/open
Ouvrir un coffre
👑 Admin / Owner uniquement
Commande
Description
/owner
Panel d'administration
/addmoney id montant
Ajouter de l'argent
/removemoney id montant
Retirer de l'argent
/ban id [raison]
Bannir un joueur
/unban id
Débannir un joueur
/dissoudre nom
Dissoudre une entreprise
/historique [nb]
Dernières transactions
/histojoueur id [nb]
Transactions d'un joueur
/baleines [nb]
Joueurs les plus riches
/statsbot
Statistiques du bot
/suspectfraude
Joueurs suspects
/freezejoueur id
Geler un compte
/unfreezejoueur id
Dégeler un compte
/pause
⏸️ Mettre le bot en pause manuelle
/resume
▶️ Relancer le bot
⏸️ Mode Pause
L'owner peut suspendre le bot à tout moment. Pendant la pause, tous les joueurs (sauf l'owner) reçoivent :
🏳️‍🌈 hey darling le bot est manuellement en pause reviens plus tard
/pause   → Active la pause
/resume  → Désactive la pause
L'état de pause est sauvegardé dans pause_state.json et persiste même après un redémarrage du bot.
🗄️ Base de données
Le bot utilise SQLite (fichier lifecity.db auto-généré au premier lancement). Aucune installation externe requise.
🛠️ Paramètres configurables (config.py)
Paramètre
Valeur par défaut
Description
STARTING_BALANCE
5 000 €
Solde de départ
DAILY_MIN / MAX
5 000 / 20 000 €
Plage de récompense daily
WORK_MIN / MAX
3 000 / 30 000 €
Plage de gain au travail
COMPANY_CREATION_COST
50 000 000 €
Coût de création d'entreprise
MIN_BET / MAX_BET
100 / 50 000 000 €
Limites des mises casino
BANK_LOAN_MAX
5 000 000 €
Prêt bancaire maximum
CRIME_SUCCESS_BASE_RATE
45%
Taux de succès de base d'un vol
🤝 Contribuer
1,Crée un nouveau fichier dans handlers/ pour ton module
2,Enregistre tes handlers dans main.py
3,Ajoute tes constantes dans config.py
4,Utilise les fonctions de db.py pour l'accès aux données
⚠️ Avertissement
Toute la monnaie utilisée dans LifeCity est virtuelle et fictive. Elle n'a aucune valeur réelle. Il n'y a aucun achat in-app, aucun retrait réel possible.
CREAT BY DEATH EMPEROR 