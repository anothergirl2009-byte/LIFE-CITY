import logging
import time
import os
import datetime as dt
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    ApplicationBuilder,
    Application,
    CommandHandler,
    CallbackQueryHandler,
    TypeHandler,
    ApplicationHandlerStop,
    Defaults,
)

from config import BOT_TOKEN, OWNER_ID, CHANNEL_USERNAME, GROUP_USERNAME
from db import init_db, is_banned, increment_cmd_count, register_group
from subscription import subscription_gate, subcheck_callback
from antispam import anti_spam_gate
from dashboard import start_dashboard_thread, start_tunnel_thread, get_dashboard_url

try:
    from config import DASHBOARD_URL
except ImportError:
    DASHBOARD_URL = None

try:
    from config import AI_CONTRACT_INTERVAL_SECONDS
except ImportError:
    AI_CONTRACT_INTERVAL_SECONDS = 6 * 3600  # valeur par défaut si absente de config.py

# ── Handlers ────────────────────────────────────────────────────────────────
from handlers.start import start, nouveautes
from handlers.profile import me, setpic, setnationalite, setsexe, setmariage, metier, metier_callback
from handlers.economy import acc, daily, work, pay, richlist
from handlers.banks import (
    banks, openbank, depositbank, withdrawbank,
    balancebank, loanbank, repaybank, loansbank,
)
from handlers.family import (
    marry, acceptmarry, refusemarry, divorce,
    adopt, acceptadopt, refuseadopt, disown,
    friend, acceptfriend, refusefriend, unfriend,
    setfamilyname, leave, tree,
)
from handlers.company import (
    creerboite, emplacementboite, dissoudreboite, listeboites, listeboites_callback, infoboite,
    batiments, acheterbatiment, mesbatiments,
    cederentreprise, handle_cederentreprise_callback, handle_negotiation_callback,
    monentreprise, employes, postuler, rejoindre, demissionner, payerindemnite,
    candidatures, accepter, refuser, recruter, nommer, licencier,
    annoncerecrutement, deplacerboite,
    negociercontrat, negociercontratmodifier, accepternegociation, refusenegociation,
    handle_application_callback, handle_invite_callback, payercooldown,
    handle_recruitment_ad_callback, handle_postuler_ad_callback,
)
from handlers.company_finance import (
    depotboite, retraitboite, logsboite, parts, mesparts, acheterparts, vendreparts,
    versersalaires, proposersalaire, accepteroffre, refuseroffre, presences, classement,
    proposercontrat, acceptercontrat, bilan,
    refusercontrat, mescontrats,
    soumettredossier, mescontratsbc, claimcontratbc,
    mesachats, annulerachat, handle_purchase_callback, handle_contract_callback,
    run_weekly_dividends, run_daily_company_tax, run_daily_building_maintenance,
)
try:
    from handlers.education import diplome, diplome_callback
except ImportError:
    from education import diplome, diplome_callback
from handlers.casino_solo import slots, roulette, mines, crash, apple, roue, rebet
from handlers.casino_pvp import blackjack, cockfight, ppc, lancer
from handlers.auctions import (
    bid, myitems, expertise, sellitem, shopitems, buyitem, open_chest,
)
from handlers.admin import (
    owner_panel, addmoney, removemoney, ban, unban, listeban, dissoudre,
    forcenommer,
    historique, histojoueur, baleines, statsbot, suspectfraude,
    freezejoueur, unfreezejoueur,
    pause_bot, resume_bot,
    annonce,
    liberer,
    prolonger,
    aggraver,
    alleger,
    voirprison,
    voirproces,
    clearjail,
    verdict,
    configurer_rank,
    administrateurs,
    listejoueurs,
    listediplomes,
    setdiplome,
    retirerdiplome,
    resetrecrutement, etatresor, debannirtous, utiliserimpots,
    addboite, removeboite, save_project,
    recherchejoueur,
    setadmin,
    unsetadmin,
    fixparts,
    listadmins,
    addbanque,
    removebanque,
    addmoneyall,
    removemoneyall,
    addbanqueall,
    removebanqueall,
    resetmoneyall,
    resetbanqueall,
    villescaisses, addcaisseville, removecaisseville, votesmaire,
    ouvrirelection, cloturerelection, trancherelection, revoquermaire, lancervote,
    nommercommission, commission, retirercandidat,
)
from handlers.immobilier import immobilier, immo_acheter_callback, mesbiens, loyer, vendrebien
from handlers.cities import (
    maville, candidature, retirercandidature, candidats, voter,
    historiquemaire, fixerimpot, caissemairie, retraitmairie,
    run_weekly_city_taxes, run_daily_mandate_check,
    handle_vote_callback,
)
from handlers.leaderboard import leaderboard
from handlers.help import help_command
from handlers.ai_contract_generator import run_ai_contract_cycle
from journal import envoyer_journal, init_events_log
from pause import is_paused

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.ERROR,
)
logger = logging.getLogger(__name__)

# ── ID du groupe qui reçoit le journal quotidien ─────────────────────────────
try:
    from config import JOURNAL_GROUP_ID
except ImportError:
    JOURNAL_GROUP_ID = None


# ═══════════════════════════════════════════════════════════════════════════════
# FILTRES GLOBAUX
# ═══════════════════════════════════════════════════════════════════════════════

async def block_banned_users(update: Update, context) -> None:
    """Bloque silencieusement toute commande venant d'un joueur banni (sauf l'owner)."""
    user = update.effective_user
    if user is None:
        return
    if user.id == OWNER_ID:
        return
    if is_banned(user.id):
        raise ApplicationHandlerStop


async def track_commands(update: Update, context) -> None:
    """Incrémente le compteur de commandes pour chaque joueur actif."""
    user = update.effective_user
    if user is None:
        return
    if update.message and update.message.text and update.message.text.startswith("/"):
        increment_cmd_count(user.id)
        chat = update.effective_chat
        if chat and chat.type in ("group", "supergroup"):
            register_group(chat.id, chat.title)


async def block_if_paused(update: Update, context) -> None:
    """Bloque toutes les commandes si le bot est en pause manuelle (sauf l'owner).
    Ne répond QUE si c'est une vraie commande (/xxx) — sinon ça spammerait
    "le bot est en pause" à chaque message envoyé dans un groupe, même sans
    rapport avec le bot."""
    user = update.effective_user
    if user is None:
        return
    if user.id == OWNER_ID:
        return
    if not is_paused():
        return

    is_command = bool(update.message and update.message.text and update.message.text.startswith("/"))
    if not is_command:
        return

    await update.message.reply_text(
        "🏳️‍🌈 hey darling le bot est manuellement en pause reviens plus tard"
    )
    raise ApplicationHandlerStop


# ═══════════════════════════════════════════════════════════════════════════════
# LANCEMENT DU BOT
# ═══════════════════════════════════════════════════════════════════════════════

async def global_error_handler(update: object, context) -> None:
    """
    Gestionnaire d'erreurs global : attrape TOUTE exception non gérée
    (RetryAfter de Telegram inclus) pour empêcher le bot de crasher.
    Avant ça, une simple limite de flood Telegram (RetryAfter) faisait
    planter tout le process -> le bot redémarrait en boucle.
    """
    error = context.error

    if isinstance(error, RetryAfter):
        logger.warning(f"⏳ Flood control Telegram : retry dans {error.retry_after}s (ignoré, pas de crash).")
        return

    if isinstance(error, TelegramError):
        logger.warning(f"⚠️ Erreur Telegram non bloquante : {error}")
        return

    logger.error(f"❌ Erreur non gérée : {error}", exc_info=error)


_dashboard_started = False


async def dashboard_cmd(update: Update, context) -> None:
    """/dashboard — Ouvre la mini app (soldes + activités). Réservé aux admins."""
    global _dashboard_started
    from db import is_admin
    user = update.effective_user

    if not is_admin(user.id) and user.id != OWNER_ID:
        await update.effective_message.reply_text("❌ Réservé aux administrateurs.")
        return

    # Le dashboard (Flask + tunnel Cloudflare) ne démarre que si/quand on
    # en a besoin, pour ne pas consommer de ressources en continu le reste
    # du temps (c'était la cause d'une partie de la lenteur du bot).
    if not _dashboard_started:
        _dashboard_started = True
        start_dashboard_thread()
        start_tunnel_thread()
        await update.effective_message.reply_text(
            "⏳ Premier lancement du dashboard, ça prend 15-20 secondes. Retape /dashboard dans un instant."
        )
        return

    # Priorité à l'URL du tunnel Cloudflare automatique (pas de domaine requis).
    # Si tu as configuré ton propre domaine dans config.py, celui-ci prend le dessus.
    url = DASHBOARD_URL or get_dashboard_url()

    if not url:
        await update.effective_message.reply_text(
            "⏳ Le dashboard démarre encore (le tunnel se met en place). Réessaie dans 15-20 secondes."
        )
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Ouvrir le dashboard", web_app=WebAppInfo(url=url))
    ]])
    await update.effective_message.reply_text(
        "📊 Dashboard LifeCity — soldes et activités en direct.",
        reply_markup=keyboard,
    )


def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "":
        raise RuntimeError(
            "Aucun token configuré. Mets ton token dans config.py (BOT_TOKEN) "
            "ou via la variable d'environnement BOT_TOKEN."
        )

    if not JOURNAL_GROUP_ID:
        logger.warning(
            "⚠️  JOURNAL_GROUP_ID non défini dans config.py — "
            "le journal quotidien ne sera pas envoyé."
        )

    init_db()
    init_events_log()

    app: Application = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_error_handler(global_error_handler)

    # ── Dashboard admin (mini app Telegram) ─────────────────────────────────
    # Ne démarre plus automatiquement ici : voir dashboard_cmd (lancement à
    # la demande, seulement quand un admin tape /dashboard pour la 1ère fois).
    app.add_handler(CommandHandler("dashboard", dashboard_cmd))
    
    # Vérifier que job_queue est disponible
    if app.job_queue is None:
        logger.warning("⚠️ JobQueue non disponible. Les tâches planifiées ne fonctionneront pas.")
    else:
        logger.info("✅ JobQueue disponible.")

    # ── Filtres globaux (priorité haute) ─────────────────────────────────────
    app.add_handler(TypeHandler(Update, block_banned_users), group=-1)
    app.add_handler(TypeHandler(Update, track_commands), group=-2)
    app.add_handler(TypeHandler(Update, block_if_paused), group=-3)

    # ── Général ──────────────────────────────────────────────────────────────
    # ── Anti-spam (cooldown progressif) ─────────────────────────────────────
    # group=-5 : tourne AVANT TOUT, y compris subscription_gate. Bloque le
    # flood localement (pas d'appel API) avant que ça n'atteigne les checks
    # plus coûteux.
    app.add_handler(TypeHandler(Update, anti_spam_gate), group=-5)

    # ── Abonnement obligatoire (groupe + canal) ─────────────────────────────
    # group=-4 : tourne avant block_if_paused/track_commands/block_banned_users
    # (chacun a besoin de son propre numéro de groupe, un seul handler par
    # groupe ne s'exécute par update — cf. bug corrigé le 19/07).
    # Il bloque (ApplicationHandlerStop) toute commande sauf /start tant que
    # le joueur n'est pas membre du groupe ET abonné au canal.
    app.add_handler(TypeHandler(Update, subscription_gate), group=-4)
    app.add_handler(CallbackQueryHandler(subcheck_callback, pattern="^subcheck$"))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("nouveautes", nouveautes))
    app.add_handler(CommandHandler("help", help_command))

    # ── Profil ───────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("me", me))
    app.add_handler(CommandHandler("setpic", setpic))
    app.add_handler(CommandHandler("setnationalite", setnationalite))
    app.add_handler(CommandHandler("setsexe", setsexe))
    app.add_handler(CommandHandler("setmariage", setmariage))
    app.add_handler(CommandHandler("metier", metier))
    app.add_handler(CallbackQueryHandler(metier_callback, pattern=r"^metier\|"))

    # ── Économie ─────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("acc", acc))
    app.add_handler(CommandHandler("daily", daily))
    app.add_handler(CommandHandler("work", work))
    app.add_handler(CommandHandler("pay", pay))
    app.add_handler(CommandHandler("richlist", richlist))
    app.add_handler(CommandHandler("leaderboard", leaderboard))

    # ── Banque ───────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("banks", banks))
    app.add_handler(CommandHandler("openbank", openbank))
    app.add_handler(CommandHandler("depositbank", depositbank))
    app.add_handler(CommandHandler("withdrawbank", withdrawbank))
    app.add_handler(CommandHandler("balancebank", balancebank))
    app.add_handler(CommandHandler("loanbank", loanbank))
    app.add_handler(CommandHandler("repaybank", repaybank))
    app.add_handler(CommandHandler("loansbank", loansbank))

    # ── Famille ──────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("marry", marry))
    app.add_handler(CommandHandler("acceptmarry", acceptmarry))
    app.add_handler(CommandHandler("refusemarry", refusemarry))
    app.add_handler(CommandHandler("divorce", divorce))
    app.add_handler(CommandHandler("adopt", adopt))
    app.add_handler(CommandHandler("acceptadopt", acceptadopt))
    app.add_handler(CommandHandler("refuseadopt", refuseadopt))
    app.add_handler(CommandHandler("disown", disown))
    app.add_handler(CommandHandler("friend", friend))
    app.add_handler(CommandHandler("acceptfriend", acceptfriend))
    app.add_handler(CommandHandler("refusefriend", refusefriend))
    app.add_handler(CommandHandler("unfriend", unfriend))
    app.add_handler(CommandHandler("setfamilyname", setfamilyname))
    app.add_handler(CommandHandler("leave", leave))
    app.add_handler(CommandHandler("tree", tree))

    # ── Entreprises ──────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("creerboite", creerboite))
    app.add_handler(CommandHandler("emplacementboite", emplacementboite))
    app.add_handler(CommandHandler("dissoudreboite", dissoudreboite))
    app.add_handler(CommandHandler("cederentreprise", cederentreprise))
    app.add_handler(CallbackQueryHandler(handle_cederentreprise_callback, pattern=r"^cederboite\|"))
    app.add_handler(CallbackQueryHandler(handle_negotiation_callback, pattern=r"^negodecision\|"))
    app.add_handler(CommandHandler("listeboites", listeboites))
    app.add_handler(CallbackQueryHandler(listeboites_callback, pattern="^boites\\|"))
    app.add_handler(CommandHandler("infoboite", infoboite))
    app.add_handler(CommandHandler("batiments", batiments))
    app.add_handler(CommandHandler("acheterbatiment", acheterbatiment))
    app.add_handler(CommandHandler("mesbatiments", mesbatiments))
    app.add_handler(CommandHandler("immobilier", immobilier))
    app.add_handler(CallbackQueryHandler(immo_acheter_callback, pattern="^immo_acheter_"))
    app.add_handler(CommandHandler("mesbiens", mesbiens))
    app.add_handler(CommandHandler("loyer", loyer))
    app.add_handler(CommandHandler("vendrebien", vendrebien))

    # ── Villes & Mairies ─────────────────────────────────────────────────────
    app.add_handler(CommandHandler("maville", maville))
    app.add_handler(CommandHandler("candidature", candidature))
    app.add_handler(CommandHandler("retirercandidature", retirercandidature))
    app.add_handler(CommandHandler("candidats", candidats))
    app.add_handler(CommandHandler("voter", voter))
    app.add_handler(CommandHandler("historiquemaire", historiquemaire))
    app.add_handler(CommandHandler("fixerimpot", fixerimpot))
    app.add_handler(CommandHandler("caissemairie", caissemairie))
    app.add_handler(CommandHandler("retraitmairie", retraitmairie))
    # Réservées à l'owner (vérifié via @owner_only dans handlers/admin.py)
    app.add_handler(CommandHandler("villescaisses", villescaisses))
    app.add_handler(CommandHandler("votesmaire", votesmaire))
    app.add_handler(CommandHandler("addcaisseville", addcaisseville))
    app.add_handler(CommandHandler("removecaisseville", removecaisseville))
    app.add_handler(CommandHandler("ouvrirelection", ouvrirelection))
    app.add_handler(CommandHandler("cloturerelection", cloturerelection))
    app.add_handler(CommandHandler("trancherelection", trancherelection))
    app.add_handler(CommandHandler("revoquermaire", revoquermaire))
    app.add_handler(CommandHandler("lancervote", lancervote))
    app.add_handler(CommandHandler("nommercommission", nommercommission))
    app.add_handler(CommandHandler("commission", commission))
    app.add_handler(CommandHandler("retirercandidat", retirercandidat))
    app.add_handler(CallbackQueryHandler(handle_vote_callback, pattern=r"^votemaire\|"))
    app.add_handler(CommandHandler("bilan", bilan))
    app.add_handler(CommandHandler("monentreprise", monentreprise))
    app.add_handler(CommandHandler("employes", employes))
    app.add_handler(CommandHandler("postuler", postuler))
    app.add_handler(CommandHandler("rejoindre", rejoindre))
    app.add_handler(CommandHandler("demissionner", demissionner))
    app.add_handler(CommandHandler("payercooldown", payercooldown))
    app.add_handler(CommandHandler("payerindemnite", payerindemnite))
    app.add_handler(CommandHandler("candidatures", candidatures))
    app.add_handler(CommandHandler("accepter", accepter))
    app.add_handler(CommandHandler("refuser", refuser))
    app.add_handler(CallbackQueryHandler(handle_application_callback, pattern=r"^appdecision\|"))
    app.add_handler(CommandHandler("recruter", recruter))
    app.add_handler(CallbackQueryHandler(handle_invite_callback, pattern=r"^invitedecision\|"))
    app.add_handler(CommandHandler("negociercontrat", negociercontrat))
    app.add_handler(CommandHandler("negociercontratmodifier", negociercontratmodifier))
    app.add_handler(CommandHandler("accepternegociation", accepternegociation))
    app.add_handler(CommandHandler("refusenegociation", refusenegociation))
    app.add_handler(CommandHandler("nommer", nommer))
    app.add_handler(CommandHandler("licencier", licencier))
    app.add_handler(CommandHandler("annoncerecrutement", annoncerecrutement))
    app.add_handler(CallbackQueryHandler(handle_recruitment_ad_callback, pattern=r"^recrutads\|"))
    app.add_handler(CallbackQueryHandler(handle_postuler_ad_callback, pattern=r"^postulerad\|"))
    app.add_handler(CommandHandler("deplacerboite", deplacerboite))

    # ── Finance Entreprise ───────────────────────────────────────────────────
    app.add_handler(CommandHandler("depotboite", depotboite))
    app.add_handler(CommandHandler("retraitboite", retraitboite))
    app.add_handler(CommandHandler("logsboite", logsboite))
    app.add_handler(CommandHandler("parts", parts))
    app.add_handler(CommandHandler("mesparts", mesparts))
    app.add_handler(CommandHandler("acheterparts", acheterparts))
    app.add_handler(CommandHandler("vendreparts", vendreparts))
    app.add_handler(CommandHandler("mesachats", mesachats))
    app.add_handler(CommandHandler("annulerachat", annulerachat))
    app.add_handler(CommandHandler("versersalaires", versersalaires))
    app.add_handler(CommandHandler("proposersalaire", proposersalaire))
    app.add_handler(CommandHandler("accepteroffre", accepteroffre))
    app.add_handler(CommandHandler("refuseroffre", refuseroffre))
    app.add_handler(CommandHandler("presences", presences))
    app.add_handler(CommandHandler("soumettredossier", soumettredossier))
    app.add_handler(CommandHandler("mescontratsbc", mescontratsbc))
    app.add_handler(CommandHandler("claimcontratbc", claimcontratbc))
    app.add_handler(CommandHandler("classement", classement))
    app.add_handler(CommandHandler("proposercontrat", proposercontrat))
    app.add_handler(CommandHandler("acceptercontrat", acceptercontrat))
    app.add_handler(CommandHandler("refusercontrat", refusercontrat))
    app.add_handler(CommandHandler("mescontrats", mescontrats))
    
    # ── Callback pour les demandes d'achat de parts ──────────────────────────
    app.add_handler(CallbackQueryHandler(handle_purchase_callback, pattern="^(accept|refuse)_purchase_"))
    app.add_handler(CallbackQueryHandler(handle_contract_callback, pattern=r"^contratdecision\|"))

    # ── Diplômes ─────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("diplome", diplome))
    app.add_handler(CallbackQueryHandler(diplome_callback, pattern="^dip_"))

    # ── Casino Solo ──────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("slots", slots))
    app.add_handler(CommandHandler("roulette", roulette))
    app.add_handler(CommandHandler("mines", mines))
    app.add_handler(CommandHandler("crash", crash))
    app.add_handler(CommandHandler("apple", apple))
    app.add_handler(CommandHandler("roue", roue))
    app.add_handler(CommandHandler("rebet", rebet))

    # ── Casino PvP ───────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("blackjack", blackjack))
    app.add_handler(CommandHandler("cockfight", cockfight))
    app.add_handler(CommandHandler("ppc", ppc))
    app.add_handler(CommandHandler("lancer", lancer))
    
    # ── Crime ────────────────────────────────────────────────────────────────
    # (désactivé)
    # from handlers.crime import steal, police, bail, juge, security
    # app.add_handler(CommandHandler("steal", steal))
    # app.add_handler(CommandHandler("police", police))
    # app.add_handler(CommandHandler("bail", bail))
    # app.add_handler(CommandHandler("juge", juge))
    # app.add_handler(CommandHandler("security", security))

    # ── Enchères & Objets ────────────────────────────────────────────────────
    app.add_handler(CommandHandler("bid", bid))
    app.add_handler(CommandHandler("myitems", myitems))
    app.add_handler(CommandHandler("expertise", expertise))
    app.add_handler(CommandHandler("sellitem", sellitem))
    app.add_handler(CommandHandler("shopitems", shopitems))
    app.add_handler(CommandHandler("buyitem", buyitem))
    app.add_handler(CommandHandler("open", open_chest))

    # ── Admin / Owner ────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("owner", owner_panel))
    app.add_handler(CommandHandler("addmoney", addmoney))
    app.add_handler(CommandHandler("removemoney", removemoney))
    app.add_handler(CommandHandler("addbanque", addbanque))
    app.add_handler(CommandHandler("removebanque", removebanque))
    app.add_handler(CommandHandler("addmoneyall", addmoneyall))
    app.add_handler(CommandHandler("removemoneyall", removemoneyall))
    app.add_handler(CommandHandler("addbanqueall", addbanqueall))
    app.add_handler(CommandHandler("removebanqueall", removebanqueall))
    app.add_handler(CommandHandler("resetmoneyall", resetmoneyall))
    app.add_handler(CommandHandler("resetbanqueall", resetbanqueall))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("listeban", listeban))
    app.add_handler(CommandHandler("dissoudre", dissoudre))
    app.add_handler(CommandHandler("forcenommer", forcenommer))
    app.add_handler(CommandHandler("historique", historique))
    app.add_handler(CommandHandler("histojoueur", histojoueur))
    app.add_handler(CommandHandler("baleines", baleines))
    app.add_handler(CommandHandler("statsbot", statsbot))
    app.add_handler(CommandHandler("suspectfraude", suspectfraude))
    app.add_handler(CommandHandler("freezejoueur", freezejoueur))
    app.add_handler(CommandHandler("unfreezejoueur", unfreezejoueur))
    app.add_handler(CommandHandler("pause", pause_bot))
    app.add_handler(CommandHandler("resume", resume_bot))
    app.add_handler(CommandHandler("annonce", annonce))

    # ── Admin - Gestion des administrateurs ──────────────────────────────────
    app.add_handler(CommandHandler("setadmin", setadmin))
    app.add_handler(CommandHandler("unsetadmin", unsetadmin))
    app.add_handler(CommandHandler("fixparts", fixparts))
    app.add_handler(CommandHandler("listadmins", listadmins))
    
    # ── Admin - Gestion des joueurs ──────────────────────────────────────────
    app.add_handler(CommandHandler("listejoueurs", listejoueurs))
    app.add_handler(CommandHandler("recherchejoueur", recherchejoueur))

    # ── Admin - Diplômes ──────────────────────────────────────────────────────
    app.add_handler(CommandHandler("listediplomes", listediplomes))
    app.add_handler(CommandHandler("setdiplome", setdiplome))
    app.add_handler(CommandHandler("retirerdiplome", retirerdiplome))
    app.add_handler(CommandHandler("resetrecrutement", resetrecrutement))
    app.add_handler(CommandHandler("etatresor", etatresor))
    app.add_handler(CommandHandler("addboite", addboite))
    app.add_handler(CommandHandler("removeboite", removeboite))
    app.add_handler(CommandHandler("save", save_project))
    app.add_handler(CommandHandler("utiliserimpots", utiliserimpots))
    app.add_handler(CommandHandler("debannirtous", debannirtous))

    # ── Admin - Prison & Justice ─────────────────────────────────────────────
    app.add_handler(CommandHandler("liberer", liberer))
    app.add_handler(CommandHandler("prolonger", prolonger))
    app.add_handler(CommandHandler("aggraver", aggraver))
    app.add_handler(CommandHandler("alleger", alleger))
    app.add_handler(CommandHandler("voirprison", voirprison))
    app.add_handler(CommandHandler("voirproces", voirproces))
    app.add_handler(CommandHandler("clearjail", clearjail))
    app.add_handler(CommandHandler("verdict", verdict))
    app.add_handler(CommandHandler("configurer_rank", configurer_rank))
    app.add_handler(CommandHandler("administrateurs", administrateurs))

    # ── Tâche planifiée : Journal quotidien à 21h00 ─────────────────────────
    if app.job_queue:
        app.job_queue.run_daily(
            envoyer_journal,
            time=dt.time(hour=21, minute=0, second=0, tzinfo=ZoneInfo("Europe/Paris")),
            name="journal_quotidien",
        )
        logger.info("📋 Journal quotidien planifié à 21h00 (Europe/Paris)")
    else:
        logger.warning("⚠️ JobQueue non disponible. Le journal quotidien ne sera pas envoyé.")

    # ── Tâche planifiée : Dividendes hebdomadaires (dimanche 12h00) ─────────
    if app.job_queue:
        app.job_queue.run_daily(
            run_weekly_dividends,
            time=dt.time(hour=12, minute=0, second=0, tzinfo=ZoneInfo("Europe/Paris")),
            days=(6,),  # 0=lundi ... 6=dimanche
            name="dividendes_hebdomadaires",
        )
        logger.info("💸 Dividendes hebdomadaires planifiés le dimanche à 12h00 (Europe/Paris)")
    else:
        logger.warning("⚠️ JobQueue non disponible. Les dividendes ne seront pas versés automatiquement.")

    # ── Tâche planifiée : Impôt quotidien des entreprises (00h05) ───────────
    if app.job_queue:
        app.job_queue.run_daily(
            run_daily_company_tax,
            time=dt.time(hour=0, minute=5, second=0, tzinfo=ZoneInfo("Europe/Paris")),
            name="impot_quotidien_entreprises",
        )
        logger.info("🏛️ Impôt quotidien des entreprises planifié à 00h05 (Europe/Paris)")
    else:
        logger.warning("⚠️ JobQueue non disponible. L'impôt quotidien ne sera pas prélevé automatiquement.")

    # ── Tâche planifiée : Maintenance quotidienne des bâtiments (00h10) ─────
    if app.job_queue:
        app.job_queue.run_daily(
            run_daily_building_maintenance,
            time=dt.time(hour=0, minute=10, second=0, tzinfo=ZoneInfo("Europe/Paris")),
            name="maintenance_batiments",
        )
        logger.info("🏗️ Maintenance quotidienne des bâtiments planifiée à 00h10 (Europe/Paris)")
    else:
        logger.warning("⚠️ JobQueue non disponible. La maintenance des bâtiments ne sera pas prélevée automatiquement.")

    # ── Tâche planifiée : Contrats IA (Gemini) à intervalle régulier ────────
    async def _job_ai_contracts(context) -> None:
        try:
            await run_ai_contract_cycle()
        except Exception as e:
            logger.exception(f"⚠️ Erreur pendant le cycle de contrats IA : {e}")

    if app.job_queue:
        app.job_queue.run_repeating(
            _job_ai_contracts,
            interval=AI_CONTRACT_INTERVAL_SECONDS,
            first=60,  # premier lancement 60s après le démarrage du bot
            name="contrats_ia",
        )
        logger.info(
            f"📄 Contrats IA planifiés toutes les {AI_CONTRACT_INTERVAL_SECONDS // 3600}h."
        )
    else:
        logger.warning("⚠️ JobQueue non disponible. Les contrats IA ne seront pas générés.")

    # ── Tâche planifiée : Impôt municipal hebdomadaire (dimanche 13h00) ─────
    if app.job_queue:
        app.job_queue.run_daily(
            run_weekly_city_taxes,
            time=dt.time(hour=13, minute=0, second=0, tzinfo=ZoneInfo("Europe/Paris")),
            days=(6,),  # 0=lundi ... 6=dimanche
            name="impots_municipaux_hebdo",
        )
        logger.info("🏙️ Impôts municipaux hebdomadaires planifiés le dimanche à 13h00 (Europe/Paris)")
    else:
        logger.warning("⚠️ JobQueue non disponible. Les impôts municipaux ne seront pas prélevés automatiquement.")

    # ── Tâche planifiée : Fin de mandat des maires (00h15) ──────────────────
    if app.job_queue:
        app.job_queue.run_daily(
            run_daily_mandate_check,
            time=dt.time(hour=0, minute=15, second=0, tzinfo=ZoneInfo("Europe/Paris")),
            name="fin_mandat_maires",
        )
        logger.info("🏛️ Vérification quotidienne des mandats de maire planifiée à 00h15 (Europe/Paris)")
    else:
        logger.warning("⚠️ JobQueue non disponible. Les mandats de maire ne seront pas vérifiés automatiquement.")

    logger.info("🤖 LifeCity Bot en ligne !")
    app.run_polling()


if __name__ == "__main__":
    main()