LifeCity/
├── main.py              Point d'entrée — lance le bot et enregistre les handlers
├── config.py             Configuration centrale (token, économie, casino, etc.)
├── db.py                 Base de données SQLite — fonctions CRUD
├── utils.py               Fonctions utilitaires partagées
├── pause.py               Pause manuelle du bot
├── requirements.txt        Dépendances Python
├── Procfile                Commande de lancement (déploiement)
│
└── handlers/                Un fichier par module fonctionnel
    ├── profile.py            /me — profil du joueur
    ├── economy.py             /acc /daily /work /pay /richlist
    ├── banks.py                /banks /openbank /depositbank /withdrawbank …
    ├── family.py                /marry /adopt /friend /tree …
    ├── company.py                 /creerboite /postuler /recruter …
    ├── company_finance.py          /parts /versersalaires /proposercontrat …
    ├── casino_solo.py               /slots /roulette /mines /crash /apple /roue
    ├── casino_pvp.py                 /blackjack /cockfight /ppc /lancer
    ├── crime.py                       /steal /police /bail /juge /security
    ├── auctions.py                     /bid /myitems /sellitem /shopitems /open
    ├── education.py                     /diplome
    └── admin.py                          /owner /addmoney /ban /pause /resume …
