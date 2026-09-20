"""
LifeCity Bot - Dashboard Admin (Mini App Telegram)
====================================================
Affiche en temps réel :
- Le classement des joueurs par solde (cash + banque)
- Les dernières activités (transactions, gains, pertes...)
- Gestion complète des joueurs (ban, freeze, argent)
- Gestion des administrateurs
- Gestion des entreprises
- Gestion de la prison
- Communication (annonces)
- Statistiques des jeux
- Sécurité & Modération
- Gestion des diplômes

Accès réservé aux administrateurs (vérifié via is_admin() et l'identité
Telegram authentifiée par le WebApp, donc impossible à falsifier).
"""

import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import threading
import time
import urllib.request
import asyncio
from urllib.parse import parse_qsl

from flask import Flask, request, jsonify, Response
from telegram import Bot

from config import BOT_TOKEN, OWNER_ID
from db import get_conn, is_admin, get_player_by_name_or_id, update_player, add_balance, log_transaction

DASHBOARD_PORT = 8081

app_flask = Flask(__name__)


# ── Validation de l'identité Telegram ──────────────────────────────────────
def _check_telegram_init_data(init_data: str) -> dict | None:
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(parsed.items())
        )
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            return None

        user_json = parsed.get("user")
        if not user_json:
            return None
        return json.loads(user_json)
    except Exception:
        return None


def send_notification_sync(user_id: int, message: str) -> bool:
    """Envoie une notification à un joueur (version synchrone)."""
    try:
        async def _send():
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(chat_id=user_id, text=message, parse_mode="Markdown")
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_send())
        loop.close()
        return True
    except Exception:
        return False


# ── API ────────────────────────────────────────────────────────────────────
@app_flask.route("/api/dashboard")
def api_dashboard():
    init_data = request.args.get("initData", "")
    user = _check_telegram_init_data(init_data)

    if not user:
        return jsonify({"error": "Identité Telegram invalide."}), 403

    if not is_admin(user["id"]) and user["id"] != OWNER_ID:
        return jsonify({"error": "Réservé aux administrateurs."}), 403

    with get_conn() as conn:
        players = conn.execute(
            """SELECT user_id, first_name, username, balance, bank_balance,
                      (balance + bank_balance) AS total, banned, is_admin, jail_until, karma, company_role, company_id, ban_reason
               FROM players
               ORDER BY total DESC
               LIMIT 200"""
        ).fetchall()

        activity = conn.execute(
            """SELECT from_user, to_user, amount, reason, created_at
               FROM transactions
               ORDER BY created_at DESC
               LIMIT 300"""
        ).fetchall()

        companies = conn.execute(
            """SELECT company_id, name, sector, ceo_id, treasury, city, country, created_at
               FROM companies
               ORDER BY treasury DESC"""
        ).fetchall()

        diplomas = conn.execute(
            """SELECT user_id, sector, tier, status FROM user_diplomas WHERE status = 'passed'"""
        ).fetchall()

        # Récupérer le total en banque pour chaque joueur
        bank_totals = conn.execute(
            """SELECT user_id, SUM(balance) as total_bank
               FROM user_bank_accounts
               GROUP BY user_id"""
        ).fetchall()

    # Créer un dictionnaire pour les totaux en banque
    bank_dict = {row["user_id"]: row["total_bank"] for row in bank_totals}

    # Ajouter le total en banque à chaque joueur
    players_with_bank = []
    for p in players:
        p_dict = dict(p)
        p_dict["total_bank"] = bank_dict.get(p["user_id"], 0)
        players_with_bank.append(p_dict)

    return jsonify({
        "players": players_with_bank,
        "activity": [dict(a) for a in activity],
        "companies": [dict(c) for c in companies],
        "diplomas": [dict(d) for d in diplomas],
    })


@app_flask.route("/api/action", methods=["POST"])
def api_action():
    body = request.get_json(silent=True) or {}
    init_data = body.get("initData", "")
    user = _check_telegram_init_data(init_data)

    if not user:
        return jsonify({"error": "Identité Telegram invalide."}), 403
    if not is_admin(user["id"]) and user["id"] != OWNER_ID:
        return jsonify({"error": "Réservé aux administrateurs."}), 403

    action = body.get("action")
    target = body.get("target")
    admin_name = user.get("first_name", "Administrateur")

    if not target:
        return jsonify({"error": "Cible manquante."}), 400

    player = get_player_by_name_or_id(str(target))
    if player is None:
        return jsonify({"error": f"Joueur '{target}' introuvable."}), 404

    uid = player["user_id"]
    pname = player["first_name"] or player["username"] or str(uid)

    if action in ("ban", "freeze") and uid == OWNER_ID:
        return jsonify({"error": "Impossible de bannir/geler le propriétaire du bot."}), 403

    if action == "ban":
        reason = body.get("reason") or "Banni depuis le dashboard"
        update_player(uid, banned=1, ban_reason=reason)
        return jsonify({"message": f"🔨 {pname} banni. Raison : {reason}"})

    elif action == "unban":
        update_player(uid, banned=0, ban_reason=None)
        return jsonify({"message": f"✅ {pname} débanni."})

    elif action == "freeze":
        update_player(uid, banned=1, ban_reason="❄️ Compte gelé (dashboard)")
        return jsonify({"message": f"❄️ {pname} gelé."})

    elif action == "unfreeze":
        update_player(uid, banned=0, ban_reason=None)
        return jsonify({"message": f"✅ {pname} dégelé."})

    elif action == "addmoney":
        try:
            amount = int(body.get("amount", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "Montant invalide."}), 400
        if amount <= 0:
            return jsonify({"error": "Montant invalide."}), 400
        
        # Ajouter l'argent
        add_balance(uid, amount)
        log_transaction(None, uid, amount, f"admin_dashboard_addmoney ({admin_name})")
        
        # Envoyer une notification au joueur (version synchrone)
        try:
            new_balance = player["balance"] + amount
            send_notification_sync(
                uid,
                f"💰 *Bonus reçu !*\n\n"
                f"🎉 L'administration de *LifeCity* vous a offert un bonus de *{amount:,} €* !\n"
                f"👤 Offert par : *{admin_name}*\n\n"
                f"📊 Votre nouveau solde : *{new_balance:,} €*"
            )
        except Exception:
            pass
        
        return jsonify({"message": f"✅ {amount:,} € ajoutés à {pname}.".replace(",", " ")})

    elif action == "removemoney":
        try:
            amount = int(body.get("amount", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "Montant invalide."}), 400
        if amount <= 0:
            return jsonify({"error": "Montant invalide."}), 400
        add_balance(uid, -amount)
        log_transaction(uid, None, amount, f"admin_dashboard_removemoney ({admin_name})")
        return jsonify({"message": f"✅ {amount:,} € retirés à {pname}.".replace(",", " ")})

    elif action == "setadmin":
        update_player(uid, is_admin=1)
        return jsonify({"message": f"👑 {pname} est maintenant administrateur."})

    elif action == "unsetadmin":
        update_player(uid, is_admin=0)
        return jsonify({"message": f"👑 {pname} n'est plus administrateur."})

    elif action == "liberer":
        update_player(uid, jail_until=0)
        return jsonify({"message": f"🔓 {pname} a été libéré de prison."})

    elif action == "prolonger":
        minutes = int(body.get("minutes", 30))
        current_jail = player["jail_until"] or 0
        now = int(time.time())
        if current_jail <= now:
            new_jail = now + (minutes * 60)
        else:
            new_jail = current_jail + (minutes * 60)
        update_player(uid, jail_until=new_jail)
        return jsonify({"message": f"⛓️ Peine de {pname} prolongée de {minutes} minutes."})

    elif action == "aggraver":
        minutes = int(body.get("minutes", 60))
        current_jail = player["jail_until"] or 0
        now = int(time.time())
        if current_jail <= now:
            new_jail = now + (minutes * 60)
        else:
            new_jail = current_jail + (minutes * 60)
        update_player(uid, jail_until=new_jail)
        return jsonify({"message": f"⛓️ Peine de {pname} aggravée de {minutes} minutes."})

    elif action == "alleger":
        minutes = int(body.get("minutes", 30))
        current_jail = player["jail_until"] or 0
        now = int(time.time())
        if current_jail <= now:
            return jsonify({"error": f"{pname} n'est pas en prison."}), 400
        new_jail = max(now, current_jail - (minutes * 60))
        update_player(uid, jail_until=new_jail)
        return jsonify({"message": f"⚖️ Peine de {pname} allégée de {minutes} minutes."})

    elif action == "clearjail":
        with get_conn() as conn:
            count = conn.execute(
                "UPDATE players SET jail_until = 0 WHERE jail_until > ?",
                (int(time.time()),)
            ).rowcount
        return jsonify({"message": f"🧹 {count} prisonniers ont été libérés."})

    elif action == "give_diploma":
        sector = body.get("sector")
        tier = body.get("tier")
        with get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO user_diplomas (user_id, sector, tier, status, obtained_at)
                   VALUES (?, ?, ?, 'passed', ?)""",
                (uid, sector, tier, int(time.time()))
            )
        return jsonify({"message": f"🎓 Diplôme {tier} ({sector}) accordé à {pname}."})

    elif action == "remove_diploma":
        sector = body.get("sector")
        tier = body.get("tier")
        with get_conn() as conn:
            conn.execute(
                "DELETE FROM user_diplomas WHERE user_id = ? AND sector = ? AND tier = ?",
                (uid, sector, tier)
            )
        return jsonify({"message": f"🗑️ Diplôme {tier} ({sector}) retiré à {pname}."})

    elif action == "dissoudre_company":
        company_id = body.get("company_id")
        from db import delete_company
        delete_company(company_id)
        return jsonify({"message": f"🏢 Entreprise dissoute."})

    return jsonify({"error": f"Action inconnue : {action}"}), 400


@app_flask.route("/api/announce", methods=["POST"])
def api_announce():
    body = request.get_json(silent=True) or {}
    init_data = body.get("initData", "")
    user = _check_telegram_init_data(init_data)

    if not user:
        return jsonify({"error": "Identité Telegram invalide."}), 403
    if not is_admin(user["id"]) and user["id"] != OWNER_ID:
        return jsonify({"error": "Réservé aux administrateurs."}), 403

    message = body.get("message", "")
    target = body.get("target", "all")

    if not message:
        return jsonify({"error": "Message vide."}), 400

    with get_conn() as conn:
        if target == "admins":
            players = conn.execute("SELECT user_id, first_name, username FROM players WHERE is_admin = 1").fetchall()
        elif target == "players":
            players = conn.execute("SELECT user_id, first_name, username FROM players WHERE is_admin = 0").fetchall()
        else:
            players = conn.execute("SELECT user_id, first_name, username FROM players").fetchall()
        
        groups = conn.execute("SELECT chat_id FROM active_groups").fetchall()

    admin_name = user.get("first_name", "Administrateur")
    full_message = f"📢 *Annonce LifeCity*\n\n{message}\n\n— *{admin_name}* (Administration)"
    
    # Fonction asynchrone pour envoyer les messages
    async def send_messages():
        success_count = 0
        fail_count = 0
        bot = Bot(token=BOT_TOKEN)
        
        # Envoyer aux groupes
        for group in groups:
            try:
                await bot.send_message(chat_id=group["chat_id"], text=full_message, parse_mode="Markdown")
                success_count += 1
            except Exception:
                fail_count += 1
        
        # Envoyer aux joueurs en PV
        for player in players:
            try:
                await bot.send_message(
                    chat_id=player["user_id"], 
                    text=full_message, 
                    parse_mode="Markdown"
                )
                success_count += 1
            except Exception:
                fail_count += 1
        
        return success_count, fail_count
    
    # Exécuter la fonction asynchrone
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        success_count, fail_count = loop.run_until_complete(send_messages())
        loop.close()
    except Exception as e:
        return jsonify({"error": f"Erreur d'envoi : {str(e)}"}), 500

    return jsonify({
        "message": f"📢 Annonce envoyée à {success_count} destinataires ({fail_count} échecs).",
        "success": success_count,
        "failed": fail_count
    })


@app_flask.route("/api/player")
def api_player():
    init_data = request.args.get("initData", "")
    user = _check_telegram_init_data(init_data)

    if not user:
        return jsonify({"error": "Identité Telegram invalide."}), 403
    if not is_admin(user["id"]) and user["id"] != OWNER_ID:
        return jsonify({"error": "Réservé aux administrateurs."}), 403

    target = request.args.get("target", "")
    player = get_player_by_name_or_id(str(target))
    if player is None:
        return jsonify({"error": f"Joueur '{target}' introuvable."}), 404

    uid = player["user_id"]

    with get_conn() as conn:
        diplomas = conn.execute(
            "SELECT sector, tier FROM user_diplomas WHERE user_id = ? AND status = 'passed'",
            (uid,),
        ).fetchall()

        company = None
        if player["company_id"]:
            company = conn.execute(
                "SELECT name, sector, city, country FROM companies WHERE company_id = ?",
                (player["company_id"],),
            ).fetchone()

        # Récupérer le total en banque
        bank_total = conn.execute(
            "SELECT SUM(balance) as total_bank FROM user_bank_accounts WHERE user_id = ?",
            (uid,)
        ).fetchone()

    now = int(time.time())
    profile = dict(player)
    profile["diplomas"] = [dict(d) for d in diplomas]
    profile["company"] = dict(company) if company else None
    profile["in_jail"] = bool(player["jail_until"] and player["jail_until"] > now)
    profile["total_bank"] = bank_total["total_bank"] if bank_total else 0

    return jsonify(profile)


# ─── HTML avec toutes les fonctionnalités ──────────────────────────────────
_HTML_PAGE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>LifeCity Dashboard</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
  /* ─── RESET & BASE ─── */
  * { box-sizing: border-box; margin: 0; padding: 0; }
  
  :root {
    --bg-primary: #0a0a12;
    --bg-secondary: #12111e;
    --bg-card: rgba(18, 17, 30, 0.7);
    --border-color: rgba(255, 255, 255, 0.06);
    --text-primary: #f0eef8;
    --text-secondary: #a09bb8;
    --text-muted: #6f6a8a;
    --accent-purple: #7b5cff;
    --accent-pink: #ff2e9a;
    --accent-cyan: #6ee7ff;
    --accent-gold: #ffd76e;
    --accent-green: #22c55e;
    --accent-red: #ff4d6d;
    --gradient-main: linear-gradient(135deg, #ff2e9a, #7b5cff 60%, #6ee7ff);
    --shadow-card: 0 8px 32px rgba(0, 0, 0, 0.4);
    --radius: 16px;
  }
  
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg-primary);
    color: var(--text-primary);
    min-height: 100vh;
    padding: 16px 16px 90px;
    overflow-x: hidden;
    -webkit-font-smoothing: antialiased;
  }
  
  .bg-gradient {
    position: fixed;
    inset: 0;
    z-index: -2;
    background: radial-gradient(ellipse at 20% 50%, rgba(123, 92, 255, 0.15) 0%, transparent 60%),
                radial-gradient(ellipse at 80% 20%, rgba(255, 46, 154, 0.10) 0%, transparent 50%),
                radial-gradient(ellipse at 50% 80%, rgba(110, 231, 255, 0.08) 0%, transparent 50%),
                var(--bg-primary);
  }
  .bg-overlay { position: fixed; inset: 0; z-index: -1; background: linear-gradient(180deg, transparent 0%, rgba(10, 10, 18, 0.8) 100%); }
  
  .icon { display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0; }
  .icon svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
  .icon-sm svg { width: 16px; height: 16px; }
  
  /* ─── TOP BAR ─── */
  .topbar { display: flex; align-items: center; justify-content: space-between; padding: 8px 0 4px; margin-bottom: 4px; }
  .logo { display: flex; align-items: center; gap: 10px; }
  .logo-icon { width: 36px; height: 36px; background: var(--gradient-main); border-radius: 10px; display: flex; align-items: center; justify-content: center; color: #fff; }
  .logo-icon svg { width: 22px; height: 22px; stroke: #fff; stroke-width: 2.5; }
  .logo-text { font-size: 18px; font-weight: 900; letter-spacing: -0.5px; background: var(--gradient-main); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
  .logo-sub { font-size: 10px; color: var(--text-secondary); font-weight: 500; letter-spacing: 0.5px; text-transform: uppercase; -webkit-text-fill-color: var(--text-secondary); }
  
  /* ─── SEARCH BAR ─── */
  .search-container {
    margin-bottom: 14px;
    position: relative;
  }
  .search-container .search-icon {
    position: absolute;
    left: 12px;
    top: 50%;
    transform: translateY(-50%);
    color: var(--text-muted);
    pointer-events: none;
  }
  .search-container .search-icon svg { width: 18px; height: 18px; stroke: var(--text-muted); }
  .search-container input {
    width: 100%;
    padding: 12px 16px 12px 40px;
    border-radius: 12px;
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    color: var(--text-primary);
    font-family: 'Inter', sans-serif;
    font-size: 14px;
    outline: none;
    transition: all 0.2s;
    backdrop-filter: blur(10px);
  }
  .search-container input:focus { border-color: var(--accent-purple); }
  .search-container input::placeholder { color: var(--text-muted); }
  .search-container .search-clear {
    position: absolute;
    right: 12px;
    top: 50%;
    transform: translateY(-50%);
    background: none;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
    padding: 4px;
    display: none;
  }
  .search-container .search-clear.visible { display: block; }
  .search-container .search-clear:hover { color: var(--text-primary); }
  
  /* ─── STATS RAPIDES ─── */
  .stats-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 16px; }
  .stat-box { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px; padding: 12px 10px; text-align: center; backdrop-filter: blur(10px); }
  .stat-box .stat-value { font-size: 16px; font-weight: 800; background: var(--gradient-main); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
  .stat-box .stat-label { font-size: 9px; color: var(--text-secondary); font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 2px; -webkit-text-fill-color: var(--text-secondary); }
  
  /* ─── TABLEAU ─── */
  .table-wrapper {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: var(--radius);
    overflow: hidden;
    backdrop-filter: blur(10px);
  }
  .table-scroll { overflow-x: auto; padding: 0 4px; }
  .table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    min-width: 750px;
  }
  .table thead { background: rgba(255, 255, 255, 0.03); }
  .table th {
    padding: 12px 10px;
    text-align: left;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-muted);
    font-weight: 700;
    border-bottom: 1px solid var(--border-color);
    white-space: nowrap;
    position: sticky;
    top: 0;
    background: var(--bg-primary);
    z-index: 5;
  }
  .table td { padding: 10px 10px; border-bottom: 1px solid var(--border-color); vertical-align: middle; }
  .table tr:hover td { background: rgba(255, 255, 255, 0.02); }
  .table .player-cell { display: flex; align-items: center; gap: 8px; }
  .table .player-avatar {
    width: 28px; height: 28px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 11px; font-weight: 700;
    color: #fff;
    flex-shrink: 0;
  }
  .table .player-name { font-weight: 600; color: var(--text-primary); white-space: nowrap; }
  .table .player-username { font-size: 10px; color: var(--text-secondary); }
  .table .amount-cell { font-weight: 700; font-size: 13px; color: var(--accent-cyan); white-space: nowrap; font-variant-numeric: tabular-nums; }
  .table .bank-cell { font-weight: 700; font-size: 13px; color: var(--accent-gold); white-space: nowrap; font-variant-numeric: tabular-nums; }
  
  .badge-status {
    font-size: 8px; padding: 2px 6px; border-radius: 4px;
    font-weight: 700; text-transform: uppercase; letter-spacing: 0.3px; white-space: nowrap;
  }
  .badge-status.admin { background: rgba(110, 231, 255, 0.15); color: var(--accent-cyan); }
  .badge-status.banned { background: rgba(255, 77, 109, 0.15); color: #ff6b8b; }
  .badge-status.frozen { background: rgba(110, 231, 255, 0.08); color: var(--accent-cyan); }
  .badge-status.jail { background: rgba(255, 215, 110, 0.12); color: var(--accent-gold); }
  .badge-status.ceo { background: rgba(255, 46, 154, 0.12); color: var(--accent-pink); }
  
  .action-btn {
    width: 28px; height: 28px; border: none; border-radius: 6px;
    cursor: pointer; display: inline-flex; align-items: center; justify-content: center;
    transition: all 0.2s; background: rgba(255, 255, 255, 0.05); color: var(--text-secondary);
  }
  .action-btn:hover { background: rgba(255, 255, 255, 0.12); transform: scale(1.05); }
  .action-btn.danger:hover { background: rgba(255, 77, 109, 0.2); color: #ff6b8b; }
  .action-btn.success:hover { background: rgba(34, 197, 94, 0.15); color: #7cf0a4; }
  .action-btn.warning:hover { background: rgba(255, 215, 110, 0.15); color: var(--accent-gold); }
  .action-btn.primary:hover { background: rgba(123, 92, 255, 0.15); color: var(--accent-purple); }
  .action-btn svg { width: 14px; height: 14px; stroke: currentColor; stroke-width: 2; fill: none; }
  .action-btn-group { display: flex; gap: 4px; flex-wrap: wrap; }
  
  /* ─── PANEL ACTIONS ─── */
  .action-panel {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: var(--radius);
    padding: 16px;
    margin-top: 12px;
    backdrop-filter: blur(10px);
    display: none;
    animation: slideUp 0.3s ease;
  }
  .action-panel.open { display: block; }
  .action-panel .ap-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  .action-panel .ap-title { font-size: 14px; font-weight: 700; color: var(--text-primary); display: flex; align-items: center; gap: 8px; }
  .action-panel .ap-close { background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 18px; padding: 4px 8px; }
  .action-panel .ap-close:hover { color: var(--text-primary); }
  .action-panel .ap-player { display: flex; align-items: center; gap: 12px; padding: 10px; background: rgba(255, 255, 255, 0.03); border-radius: 10px; margin-bottom: 12px; }
  .action-panel .ap-avatar { width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 16px; font-weight: 700; color: #fff; flex-shrink: 0; }
  .action-panel .ap-info .ap-name { font-weight: 700; font-size: 14px; color: var(--text-primary); }
  .action-panel .ap-info .ap-balance { font-size: 12px; color: var(--text-secondary); }
  .action-panel .ap-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
  .action-panel .ap-btn {
    padding: 10px 8px; border: none; border-radius: 8px;
    font-family: 'Inter', sans-serif; font-weight: 600; font-size: 10px;
    cursor: pointer; transition: all 0.2s; color: #fff;
    display: flex; align-items: center; justify-content: center; gap: 6px;
    text-transform: uppercase; letter-spacing: 0.3px;
  }
  .action-panel .ap-btn:hover { transform: scale(0.97); }
  .action-panel .ap-btn .icon svg { width: 14px; height: 14px; stroke-width: 2.5; }
  
  .ap-btn-money { background: linear-gradient(135deg, #6ee7ff, #7b5cff); }
  .ap-btn-ban { background: linear-gradient(135deg, #ff4d6d, #c0284a); }
  .ap-btn-unban { background: linear-gradient(135deg, #22c55e, #16a34a); }
  .ap-btn-freeze { background: linear-gradient(135deg, #6ee7ff, #3b82f6); }
  .ap-btn-unfreeze { background: linear-gradient(135deg, #f59e0b, #d97706); }
  .ap-btn-profile { background: rgba(255, 255, 255, 0.1); }
  .ap-btn-admin { background: linear-gradient(135deg, #7b5cff, #5c3dcc); }
  .ap-btn-jail { background: linear-gradient(135deg, #ff6b8b, #c0284a); }
  .ap-btn-diploma { background: linear-gradient(135deg, #ffd76e, #f59e0b); color: #000; }
  
  /* ─── MODAL ─── */
  .modal-backdrop {
    display: none; position: fixed; inset: 0; z-index: 200;
    background: rgba(0, 0, 0, 0.7); backdrop-filter: blur(8px);
    align-items: flex-end;
  }
  .modal-backdrop.open { display: flex; }
  .modal-sheet {
    background: var(--bg-secondary);
    border-radius: 24px 24px 0 0;
    width: 100%;
    max-height: 85vh;
    overflow-y: auto;
    padding: 12px 16px calc(20px + env(safe-area-inset-bottom));
    border-top: 1px solid var(--border-color);
    animation: slideUp 0.3s ease;
  }
  @keyframes slideUp { from { transform: translateY(30px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
  .modal-handle { width: 36px; height: 4px; background: rgba(255, 255, 255, 0.15); border-radius: 4px; margin: 0 auto 16px; }
  
  .form-group { margin-bottom: 14px; }
  .form-group label { display: block; font-size: 11px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
  .form-group input, .form-group select, .form-group textarea {
    width: 100%; padding: 10px 12px; border-radius: 10px;
    background: rgba(255, 255, 255, 0.04); border: 1px solid var(--border-color);
    color: var(--text-primary); font-family: 'Inter', sans-serif; font-size: 14px;
    outline: none; transition: border-color 0.2s;
  }
  .form-group input:focus, .form-group select:focus, .form-group textarea:focus { border-color: var(--accent-purple); }
  .form-group textarea { resize: vertical; min-height: 80px; }
  .form-group select option { background: var(--bg-secondary); }
  
  .btn-row { display: flex; gap: 8px; margin-top: 4px; }
  .btn {
    flex: 1; padding: 12px; border: none; border-radius: 10px;
    font-family: 'Inter', sans-serif; font-weight: 700; font-size: 13px;
    cursor: pointer; transition: all 0.2s; color: #fff;
    display: flex; align-items: center; justify-content: center; gap: 6px;
  }
  .btn .icon svg { width: 16px; height: 16px; stroke-width: 2.5; }
  .btn:hover { transform: scale(0.97); }
  .btn-green { background: linear-gradient(135deg, #22c55e, #16a34a); }
  .btn-red { background: linear-gradient(135deg, #ff4d6d, #c0284a); }
  .btn-blue { background: linear-gradient(135deg, #6ee7ff, #7b5cff); }
  .btn-gold { background: linear-gradient(135deg, #ffd76e, #f59e0b); color: #000; }
  .btn-grey { background: rgba(255, 255, 255, 0.08); }
  .btn-purple { background: linear-gradient(135deg, #7b5cff, #5c3dcc); }
  
  .action-msg {
    font-size: 12px; margin-top: 12px; padding: 10px 12px; border-radius: 10px; display: none;
  }
  .action-msg.ok { display: block; background: rgba(34, 197, 94, 0.12); color: #7cf0a4; border: 1px solid rgba(34, 197, 94, 0.2); }
  .action-msg.err { display: block; background: rgba(255, 77, 109, 0.12); color: #ff8fa8; border: 1px solid rgba(255, 77, 109, 0.2); }
  
  /* ─── BOTTOM NAV ─── */
  .bottomnav {
    position: fixed; bottom: 0; left: 0; right: 0; z-index: 100;
    display: flex; justify-content: space-around;
    background: rgba(10, 9, 18, 0.92); backdrop-filter: blur(16px);
    border-top: 1px solid var(--border-color);
    padding: 6px 2px calc(6px + env(safe-area-inset-bottom));
    gap: 2px;
  }
  .navitem {
    flex: 1; text-align: center; font-size: 7px; font-weight: 600; color: var(--text-muted);
    display: flex; flex-direction: column; align-items: center; gap: 2px; cursor: pointer;
    padding: 4px 2px; transition: color 0.2s; user-select: none; text-transform: uppercase; letter-spacing: 0.3px;
  }
  .navitem .icon svg { width: 18px; height: 18px; stroke: var(--text-muted); transition: stroke 0.2s; }
  .navitem.active { color: var(--accent-pink); }
  .navitem.active .icon svg { stroke: var(--accent-pink); }
  
  /* ─── CARDS ─── */
  .card {
    background: var(--bg-card); border: 1px solid var(--border-color); border-radius: var(--radius);
    padding: 14px 16px; margin-bottom: 8px; display: flex; align-items: center; gap: 12px;
    cursor: pointer; transition: all 0.2s; backdrop-filter: blur(10px);
  }
  .card:hover { background: rgba(255, 255, 255, 0.05); transform: translateX(4px); }
  .card-rank { font-size: 13px; font-weight: 700; color: var(--accent-pink); min-width: 28px; font-variant-numeric: tabular-nums; }
  .card-avatar { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 14px; color: #fff; flex-shrink: 0; }
  .card-info { flex: 1; min-width: 0; }
  .card-name { font-weight: 600; font-size: 14px; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .card-sub { font-size: 11px; color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .card-amount { font-weight: 700; font-size: 15px; color: var(--accent-cyan); white-space: nowrap; font-variant-numeric: tabular-nums; }
  
  .activity-card { flex-direction: column; align-items: stretch; }
  .activity-line { display: flex; align-items: center; justify-content: space-between; font-size: 13px; color: var(--text-secondary); }
  .activity-line .amount { font-weight: 700; color: var(--accent-cyan); font-variant-numeric: tabular-nums; }
  .activity-line .amount.negative { color: #ff4d6d; }
  .activity-reason { font-size: 11px; color: var(--text-muted); margin-top: 4px; display: flex; align-items: center; gap: 8px; }
  .activity-time { font-size: 10px; color: var(--text-muted); font-variant-numeric: tabular-nums; }
  
  .game-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .game-tile { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: var(--radius); padding: 20px 12px; text-align: center; backdrop-filter: blur(10px); transition: all 0.2s; cursor: default; }
  .game-tile:hover { background: rgba(255, 255, 255, 0.04); }
  .game-emoji { font-size: 40px; display: block; margin-bottom: 10px; }
  .game-name { font-weight: 700; font-size: 13px; color: var(--text-primary); letter-spacing: 0.5px; }
  .game-desc { font-size: 11px; color: var(--text-secondary); margin-top: 4px; }
  
  .cmd-category { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: var(--accent-pink); margin: 20px 0 8px; padding-left: 2px; }
  .cmd-category:first-child { margin-top: 0; }
  .cmd-row { display: flex; align-items: center; gap: 10px; padding: 8px 4px; border-bottom: 1px solid var(--border-color); font-size: 13px; }
  .cmd-name { font-weight: 600; color: var(--accent-cyan); font-family: 'Inter', monospace; font-size: 12px; white-space: nowrap; }
  .cmd-desc { color: var(--text-secondary); flex: 1; font-size: 12px; }
  .cmd-badge { font-size: 9px; font-weight: 700; padding: 2px 8px; border-radius: 6px; background: rgba(255, 46, 154, 0.15); color: var(--accent-pink); text-transform: uppercase; letter-spacing: 0.5px; white-space: nowrap; }
  .cmd-badge.admin { background: rgba(123, 92, 255, 0.15); color: var(--accent-purple); }
  
  .profile-header { display: flex; align-items: center; gap: 14px; margin-bottom: 16px; }
  .profile-avatar-large { width: 64px; height: 64px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 26px; font-weight: 800; color: #fff; flex-shrink: 0; border: 3px solid rgba(255, 255, 255, 0.1); }
  .profile-name { font-size: 18px; font-weight: 800; color: var(--text-primary); }
  .profile-sub { font-size: 12px; color: var(--text-secondary); }
  .profile-badges { display: flex; gap: 6px; flex-wrap: wrap; margin: 8px 0 14px; }
  .badge { font-size: 10px; font-weight: 700; padding: 4px 10px; border-radius: 8px; text-transform: uppercase; letter-spacing: 0.3px; }
  .badge-admin { background: rgba(110, 231, 255, 0.12); color: var(--accent-cyan); }
  .badge-banned { background: rgba(255, 77, 109, 0.12); color: #ff6b8b; }
  .badge-jail { background: rgba(255, 215, 110, 0.12); color: var(--accent-gold); }
  .badge-ceo { background: rgba(255, 46, 154, 0.12); color: var(--accent-pink); }
  .profile-stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 14px; }
  .profile-stat { background: rgba(255, 255, 255, 0.03); border-radius: 10px; padding: 10px 12px; }
  .profile-stat-label { font-size: 9px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); }
  .profile-stat-value { font-size: 15px; font-weight: 700; color: var(--text-primary); margin-top: 2px; font-variant-numeric: tabular-nums; }
  .diploma-chip { font-size: 11px; background: rgba(255, 215, 110, 0.08); color: var(--accent-gold); padding: 4px 10px; border-radius: 8px; border: 1px solid rgba(255, 215, 110, 0.1); }
  
  .loading, .error, .empty { text-align: center; padding: 60px 20px; color: var(--text-secondary); }
  .error { color: #ff6b8b; }
  .empty { color: var(--text-muted); font-size: 13px; }
  .spinner { width: 32px; height: 32px; border: 3px solid var(--border-color); border-top-color: var(--accent-purple); border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 16px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  
  ::-webkit-scrollbar { width: 4px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--text-muted); border-radius: 4px; }
  .hidden { display: none !important; }
</style>
</head>
<body>
  <div class="bg-gradient"></div>
  <div class="bg-overlay"></div>

  <!-- TOP BAR -->
  <div class="topbar">
    <div class="logo">
      <div class="logo-icon"><svg viewBox="0 0 24 24"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polygon points="2 17 12 22 22 17"/><polygon points="2 12 12 17 22 12"/></svg></div>
      <div><div class="logo-text">LIFECITY</div><div class="logo-sub">Dashboard Admin</div></div>
    </div>
  </div>

  <!-- SEARCH BAR -->
  <div class="search-container">
    <span class="search-icon"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg></span>
    <input type="text" id="searchInput" placeholder="Rechercher par ID, @pseudo ou prénom...">
    <button class="search-clear" id="searchClear">✕</button>
  </div>

  <!-- STATS -->
  <div class="stats-row" id="statsRow">
    <div class="stat-box"><div class="stat-value" id="statPlayers">0</div><div class="stat-label">Joueurs</div></div>
    <div class="stat-box"><div class="stat-value" id="statTotal">0</div><div class="stat-label">Total en jeu</div></div>
    <div class="stat-box"><div class="stat-value" id="statTop">0</div><div class="stat-label">Top fortune</div></div>
  </div>

  <!-- CONTENT -->
  <div id="content"><div class="loading"><div class="spinner"></div>Chargement...</div></div>

  <!-- BOTTOM NAV -->
  <div class="bottomnav">
    <div class="navitem active" data-tab="users"><span class="icon"><svg viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg></span>Users</div>
    <div class="navitem" data-tab="admins"><span class="icon"><svg viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg></span>Admins</div>
    <div class="navitem" data-tab="companies"><span class="icon"><svg viewBox="0 0 24 24"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg></span>Boîtes</div>
    <div class="navitem" data-tab="prison"><span class="icon"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/><line x1="15" y1="3" x2="15" y2="21"/></svg></span>Prison</div>
    <div class="navitem" data-tab="announce"><span class="icon"><svg viewBox="0 0 24 24"><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/></svg></span>Annonce</div>
    <div class="navitem" data-tab="diplomas"><span class="icon"><svg viewBox="0 0 24 24"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg></span>Diplômes</div>
  </div>

  <!-- MODAL ARGENT -->
  <div class="modal-backdrop money-modal" id="moneyModal">
    <div class="modal-sheet">
      <div class="modal-handle"></div>
      <div class="money-form" id="moneyForm">
        <div class="player-info" id="moneyPlayerInfo">
          <div class="pi-avatar" id="moneyAvatar" style="background:#7b5cff;">?</div>
          <div>
            <div class="pi-name" id="moneyPlayerName">Joueur</div>
            <div class="pi-balance" id="moneyPlayerBalance">Solde : 0 €</div>
          </div>
        </div>
        <div class="form-group">
          <label>Montant</label>
          <input type="number" id="moneyAmount" placeholder="Entrez le montant..." min="1" step="1">
        </div>
        <div class="btn-row">
          <button class="btn btn-green" id="moneyAddBtn"><span class="icon"><svg viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></span>Ajouter</button>
          <button class="btn btn-red" id="moneyRemoveBtn"><span class="icon"><svg viewBox="0 0 24 24"><line x1="5" y1="12" x2="19" y2="12"/></svg></span>Retirer</button>
        </div>
        <div class="action-msg" id="moneyModalMsg"></div>
      </div>
    </div>
  </div>

  <!-- MODAL PROFIL -->
  <div class="modal-backdrop" id="profileModal">
    <div class="modal-sheet">
      <div class="modal-handle"></div>
      <div id="profileContent">Chargement...</div>
    </div>
  </div>

  <!-- MODAL ANNONCE -->
  <div class="modal-backdrop" id="announceModal">
    <div class="modal-sheet">
      <div class="modal-handle"></div>
      <div class="announce-form">
        <div class="form-group">
          <label>Message</label>
          <textarea id="announceMessage" placeholder="Votre message..."></textarea>
        </div>
        <div class="form-group">
          <label>Destinataires</label>
          <select id="announceTarget">
            <option value="all">Tous les joueurs + groupes</option>
            <option value="admins">Administrateurs</option>
            <option value="players">Joueurs (non-admins)</option>
          </select>
        </div>
        <div style="font-size:11px;color:var(--text-muted);margin-bottom:12px;">
          💡 L'annonce sera envoyée à tous les groupes actifs et en message privé.
        </div>
        <div class="btn-row">
          <button class="btn btn-gold" id="announceSendBtn"><span class="icon"><svg viewBox="0 0 24 24"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg></span>Envoyer</button>
        </div>
        <div class="action-msg" id="announceMsg"></div>
      </div>
    </div>
  </div>

  <!-- MODAL DIPLÔME -->
  <div class="modal-backdrop" id="diplomaModal">
    <div class="modal-sheet">
      <div class="modal-handle"></div>
      <div class="diploma-form">
        <div class="form-group">
          <label>Joueur</label>
          <input type="text" id="diplomaPlayer" placeholder="ID ou @pseudo du joueur">
        </div>
        <div class="form-group">
          <label>Secteur</label>
          <select id="diplomaSector">
            <option value="technologie">Technologie</option>
            <option value="finance">Finance</option>
            <option value="immobilier">Immobilier</option>
            <option value="restauration">Restauration</option>
            <option value="industrie">Industrie</option>
            <option value="commerce">Commerce</option>
            <option value="transport">Transport</option>
            <option value="medias">Médias</option>
            <option value="sante">Santé</option>
            <option value="energie">Énergie</option>
          </select>
        </div>
        <div class="form-group">
          <label>Niveau</label>
          <select id="diplomaTier">
            <option value="bac">Bac</option>
            <option value="licence">Licence</option>
            <option value="master">Master</option>
            <option value="mba">MBA</option>
          </select>
        </div>
        <div class="btn-row">
          <button class="btn btn-gold" id="diplomaGiveBtn"><span class="icon"><svg viewBox="0 0 24 24"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg></span>Accorder</button>
          <button class="btn btn-red" id="diplomaRemoveBtn"><span class="icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg></span>Retirer</button>
        </div>
        <div class="action-msg" id="diplomaMsg"></div>
      </div>
    </div>
  </div>

  <!-- MODAL ACTIONS PRISON -->
  <div class="modal-backdrop" id="prisonActionModal">
    <div class="modal-sheet">
      <div class="modal-handle"></div>
      <div class="prison-action-form">
        <div class="form-group">
          <label>Joueur</label>
          <input type="text" id="prisonPlayer" placeholder="ID ou @pseudo du joueur">
        </div>
        <div class="form-group">
          <label>Minutes</label>
          <input type="number" id="prisonMinutes" placeholder="30" min="1" value="30">
        </div>
        <div class="btn-row">
          <button class="btn btn-red" id="prisonProlongerBtn"><span class="icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg></span>Prolonger</button>
          <button class="btn btn-purple" id="prisonAggraverBtn"><span class="icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 8 14"/></svg></span>Aggraver</button>
          <button class="btn btn-green" id="prisonAllegerBtn"><span class="icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 10"/></svg></span>Alléger</button>
        </div>
        <div class="action-msg" id="prisonActionMsg"></div>
      </div>
    </div>
  </div>

<script>
  const tg = window.Telegram.WebApp;
  tg.ready();
  tg.expand();

  let currentTab = "users";
  let cachedData = null;
  let searchQuery = "";
  let currentMoneyTarget = null;
  let selectedUserId = null;

  // ─── JEUX ───
  const GAMES = [
    { name: 'SLOTS', desc: 'Machine à sous', emoji: '🎰' },
    { name: 'CRASH', desc: 'Multiplicateur', emoji: '📈' },
    { name: 'BLACKJACK', desc: '21 ou rien', emoji: '🃏' },
    { name: 'ROUE', desc: 'Tente ta chance', emoji: '🎡' },
    { name: 'MINES', desc: 'Évite les bombes', emoji: '💣' },
    { name: 'ROULETTE', desc: 'Rouge ou noir', emoji: '🎲' },
    { name: 'PPC', desc: 'Pierre Papier Ciseaux', emoji: '✂️' },
    { name: 'COQS', desc: 'Combat de coqs', emoji: '🐓' },
    { name: 'APPLE', desc: 'Tour des pommes', emoji: '🍎' },
    { name: 'REBET', desc: 'Quitte ou double', emoji: '🪙' },
  ];

  const COMMANDS = {
    "Économie": [["/acc","Voir son compte"],["/daily","Bonus quotidien"],["/work","Travailler"],["/pay","Envoyer de l'argent"],["/richlist","Top 10"],["/me","Profil complet"]],
    "Banque": [["/banks","Voir les banques"],["/openbank","Ouvrir un compte"],["/depositbank","Déposer"],["/withdrawbank","Retirer"],["/loanbank","Demander un prêt"],["/repaybank","Rembourser"]],
    "Crime": [["/steal","Voler un joueur"],["/police","Porter plainte"],["/bail","Payer sa caution"],["/security","Améliorer sa protection"]],
    "Famille": [["/marry","Demander en mariage"],["/divorce","Divorcer"],["/adopt","Adopter"],["/friend","Demander en ami"],["/tree","Arbre familial"]],
    "Entreprise": [["/creerboite","Créer son entreprise"],["/monentreprise","Voir son entreprise"],["/postuler","Postuler"],["/employes","Voir les employés"]],
    "Éducation": [["/diplome","Voir/passer ses diplômes"]],
  };
  const ADMIN_COMMANDS = {
    "Owner": [["/owner","Panel complet"],["/setadmin","Nommer un admin"],["/statsbot","Stats du bot"]],
    "Modération": [["/ban","Bannir"],["/unban","Débannir"],["/freezejoueur","Geler"],["/unfreezejoueur","Dégeler"],["/listejoueurs","Liste des joueurs"]],
    "Justice": [["/liberer","Libérer de prison"],["/verdict","Rendre un verdict"],["/voirprison","Voir les prisonniers"],["/clearjail","Libérer tous"]],
  };

  function fmtMoney(n) { return new Intl.NumberFormat('fr-FR').format(n || 0) + ' €'; }
  function fmtTime(ts) { const d = new Date(ts*1000); return d.toLocaleString('fr-FR', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}); }
  function fmtDateShort(ts) { const d = new Date(ts*1000); return d.toLocaleDateString('fr-FR', {day:'2-digit',month:'2-digit'}); }

  function getColor(userId) {
    const colors = ['#7b5cff','#ff2e9a','#6ee7ff','#ffd76e','#22c55e','#ff6b8b','#f59e0b','#8b5cf6'];
    return colors[Math.abs(userId||0)%colors.length];
  }
  function getInitial(name) { if (!name) return '?'; return name.charAt(0).toUpperCase(); }

  function matchesSearch(player) {
    if (!searchQuery) return true;
    const query = searchQuery.toLowerCase().trim();
    const cleanQuery = query.startsWith('@') ? query.substring(1) : query;
    if (player.user_id && player.user_id.toString() === query) return true;
    if (player.user_id && query.match(/^\d+$/) && player.user_id.toString().includes(query)) return true;
    if (player.username && player.username.toLowerCase().includes(cleanQuery)) return true;
    if (player.first_name && player.first_name.toLowerCase().includes(query)) return true;
    return false;
  }

  function renderStats(players) {
    document.getElementById('statPlayers').textContent = players.length;
    const total = players.reduce((s,p) => s + (p.total||0), 0);
    const totalBank = players.reduce((s,p) => s + (p.total_bank||0), 0);
    document.getElementById('statTotal').textContent = fmtMoney(total) + ' | 🏦 ' + fmtMoney(totalBank);
    document.getElementById('statTop').textContent = players.length ? fmtMoney(players[0].total) : '0 €';
  }

  // ─── RENDER USERS ───
  function renderUsers() {
    const el = document.getElementById('content');
    if (!cachedData) { el.innerHTML = '<div class="loading"><div class="spinner"></div>Chargement...</div>'; return; }
    const players = cachedData.players;
    renderStats(players);
    const matches = players.filter(p => matchesSearch(p));
    if (!matches.length) { el.innerHTML = '<div class="empty">Aucun joueur trouvé.</div>'; return; }

    let html = `
      <div class="table-wrapper"><div class="table-scroll"><table class="table">
        <thead><tr><th>Joueur</th><th>Fortune</th><th>🏦 Banque</th><th>Statut</th></tr></thead><tbody>
    `;
    matches.forEach(p => {
      const color = getColor(p.user_id);
      const initial = getInitial(p.first_name || p.username);
      const name = p.first_name || p.username || 'Joueur';
      const username = p.username ? '@' + p.username : '—';
      const total = (p.balance||0) + (p.bank_balance||0);
      const totalBank = p.total_bank || 0;
      let statuses = [];
      if (p.is_admin) statuses.push('<span class="badge-status admin">Admin</span>');
      if (p.banned) statuses.push('<span class="badge-status banned">Banni</span>');
      if (p.banned && p.ban_reason && p.ban_reason.includes('gelé')) statuses.push('<span class="badge-status frozen">Gelé</span>');
      if (p.jail_until && p.jail_until > Math.floor(Date.now()/1000)) statuses.push('<span class="badge-status jail">Prison</span>');
      if (p.company_role === 'PDG') statuses.push('<span class="badge-status ceo">PDG</span>');
      if (!statuses.length) statuses.push('<span class="badge-status" style="color:var(--text-muted);background:transparent;">—</span>');
      html += `
        <tr onclick="selectPlayer(${p.user_id})" data-userid="${p.user_id}">
          <td><div class="player-cell"><div class="player-avatar" style="background:${color}">${initial}</div><div><div class="player-name">${name}</div><div class="player-username">${username}</div></div></div></td>
          <td class="amount-cell">${fmtMoney(total)}</td>
          <td class="bank-cell">${fmtMoney(totalBank)}</td>
          <td>${statuses.join(' ')}</td>
        </tr>
      `;
    });
    html += `</tbody></table></div></div>`;

    if (selectedUserId !== null) {
      const p = players.find(p => p.user_id == selectedUserId);
      if (p) html += renderActionPanel(p);
    }
    html += `<div style="margin-top:8px;font-size:11px;color:var(--text-muted);text-align:center;">${matches.length} joueur${matches.length>1?'s':''} affiché${matches.length>1?'s':''}</div>`;
    el.innerHTML = html;
  }

  function renderActionPanel(p) {
    const color = getColor(p.user_id);
    const initial = getInitial(p.first_name || p.username);
    const total = (p.balance||0) + (p.bank_balance||0);
    const totalBank = p.total_bank || 0;
    const isBanned = p.banned || false;
    const isFrozen = p.banned && p.ban_reason && p.ban_reason.includes('gelé');
    const isAdmin = p.is_admin || false;

    return `
      <div class="action-panel open" id="actionPanel">
        <div class="ap-header">
          <div class="ap-title"><span class="icon" style="color:var(--accent-gold);"><svg viewBox="0 0 24 24"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg></span>Actions</div>
          <button class="ap-close" onclick="deselectPlayer()">✕</button>
        </div>
        <div class="ap-player">
          <div class="ap-avatar" style="background:${color}">${initial}</div>
          <div class="ap-info">
            <div class="ap-name">${p.first_name || p.username || 'Joueur'}</div>
            <div class="ap-balance">💵 Liquide: ${fmtMoney(p.balance||0)} | 🏦 Banque: ${fmtMoney(totalBank)}</div>
            <div class="ap-balance" style="color:var(--accent-gold);">💰 Total: ${fmtMoney(total)}</div>
          </div>
        </div>
        <div class="ap-actions">
          <button class="ap-btn ap-btn-money" onclick="openMoneyModal('${p.user_id}')"><span class="icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg></span>Argent</button>
          ${isAdmin ? `
            <button class="ap-btn ap-btn-admin" onclick="doUnsetAdmin('${p.user_id}')"><span class="icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg></span>Retirer Admin</button>
          ` : `
            <button class="ap-btn ap-btn-admin" onclick="doSetAdmin('${p.user_id}')"><span class="icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M8 12l3 3 5-5"/></svg></span>Nommer Admin</button>
          `}
          ${isBanned ? `
            <button class="ap-btn ap-btn-unban" onclick="doUnban('${p.user_id}')"><span class="icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M8 12l3 3 5-5"/></svg></span>Débannir</button>
          ` : `
            <button class="ap-btn ap-btn-ban" onclick="doBan('${p.user_id}')"><span class="icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg></span>Bannir</button>
          `}
          ${isFrozen ? `
            <button class="ap-btn ap-btn-unfreeze" onclick="doUnfreeze('${p.user_id}')"><span class="icon"><svg viewBox="0 0 24 24"><polygon points="19 14 19 22 12 17 5 22 5 14 12 9 19 14"/><line x1="12" y1="9" x2="12" y2="2"/></svg></span>Dégeler</button>
          ` : `
            <button class="ap-btn ap-btn-freeze" onclick="doFreeze('${p.user_id}')"><span class="icon"><svg viewBox="0 0 24 24"><polygon points="19 14 19 22 12 17 5 22 5 14 12 9 19 14"/></svg></span>Geler</button>
          `}
          <button class="ap-btn ap-btn-jail" onclick="openPrisonAction('${p.user_id}')"><span class="icon"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/><line x1="15" y1="3" x2="15" y2="21"/></svg></span>Prison</button>
          <button class="ap-btn ap-btn-diploma" onclick="openDiplomaModal('${p.user_id}')"><span class="icon"><svg viewBox="0 0 24 24"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg></span>Diplôme</button>
          <button class="ap-btn ap-btn-profile" onclick="openProfile('${p.user_id}')"><span class="icon"><svg viewBox="0 0 24 24"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg></span>Profil</button>
        </div>
      </div>
    `;
  }

  window.selectPlayer = (userId) => { selectedUserId = userId; renderUsers(); setTimeout(() => { const panel = document.getElementById('actionPanel'); if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'center' }); }, 100); };
  window.deselectPlayer = () => { selectedUserId = null; renderUsers(); };

  // ─── RENDER ADMINS ───
  function renderAdmins() {
    const el = document.getElementById('content');
    if (!cachedData) { el.innerHTML = '<div class="loading"><div class="spinner"></div>Chargement...</div>'; return; }
    const admins = cachedData.players.filter(p => p.is_admin);
    if (!admins.length) { el.innerHTML = '<div class="empty">Aucun administrateur.</div>'; return; }
    let html = `<div class="table-wrapper"><div class="table-scroll"><table class="table"><thead><tr><th>Admin</th><th>Fortune</th><th>🏦 Banque</th><th>Actions</th></tr></thead><tbody>`;
    admins.forEach(p => {
      const color = getColor(p.user_id);
      const initial = getInitial(p.first_name || p.username);
      const name = p.first_name || p.username || 'Joueur';
      const total = (p.balance||0) + (p.bank_balance||0);
      const totalBank = p.total_bank || 0;
      html += `
        <tr>
          <td><div class="player-cell"><div class="player-avatar" style="background:${color}">${initial}</div><div><div class="player-name">${name}</div><div class="player-username">@${p.username||'—'}</div></div></div></td>
          <td class="amount-cell">${fmtMoney(total)}</td>
          <td class="bank-cell">${fmtMoney(totalBank)}</td>
          <td><div class="action-btn-group">
            <button class="action-btn danger" onclick="doUnsetAdmin('${p.user_id}')" title="Retirer admin"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg></button>
          </div></td>
        </tr>
      `;
    });
    html += `</tbody></table></div></div>
      <div style="margin-top:12px;"><div class="btn-row"><button class="btn btn-gold" onclick="openAddAdmin()"><span class="icon"><svg viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></span>Ajouter un admin</button></div></div>
      <div class="action-msg" id="addAdminMsg"></div>
    `;
    el.innerHTML = html;
  }

  window.openAddAdmin = () => {
    const target = prompt("Entrez l'ID ou @pseudo du joueur à nommer admin :");
    if (target) doSetAdmin(target);
  };

  // ─── RENDER COMPANIES ───
  function renderCompanies() {
    const el = document.getElementById('content');
    if (!cachedData) { el.innerHTML = '<div class="loading"><div class="spinner"></div>Chargement...</div>'; return; }
    const companies = cachedData.companies || [];
    if (!companies.length) { el.innerHTML = '<div class="empty">Aucune entreprise.</div>'; return; }
    let html = `<div class="table-wrapper"><div class="table-scroll"><table class="table"><thead><tr><th>Nom</th><th>Secteur</th><th>Trésorerie</th><th>PDG</th><th>Actions</th></tr></thead><tbody>`;
    companies.forEach(c => {
      const ceo = cachedData.players.find(p => p.user_id == c.ceo_id);
      const ceoName = ceo ? ceo.first_name || ceo.username || '#'+c.ceo_id : 'Inconnu';
      html += `
        <tr>
          <td><strong>${c.name}</strong></td>
          <td>${c.sector}</td>
          <td class="amount-cell">${fmtMoney(c.treasury)}</td>
          <td>${ceoName}</td>
          <td><div class="action-btn-group">
            <button class="action-btn danger" onclick="doDissoudreCompany('${c.company_id}')" title="Dissoudre"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg></button>
          </div></td>
        </tr>
      `;
    });
    html += `</tbody></table></div></div>`;
    el.innerHTML = html;
  }

  // ─── RENDER PRISON ───
  function renderPrison() {
    const el = document.getElementById('content');
    if (!cachedData) { el.innerHTML = '<div class="loading"><div class="spinner"></div>Chargement...</div>'; return; }
    const now = Math.floor(Date.now()/1000);
    const prisoners = cachedData.players.filter(p => p.jail_until && p.jail_until > now);
    if (!prisoners.length) { el.innerHTML = '<div class="empty">🔓 Aucun prisonnier.</div>'; return; }
    let html = `<div class="table-wrapper"><div class="table-scroll"><table class="table"><thead><tr><th>Prisonnier</th><th>Temps restant</th><th>Actions</th></tr></thead><tbody>`;
    prisoners.forEach(p => {
      const color = getColor(p.user_id);
      const initial = getInitial(p.first_name || p.username);
      const name = p.first_name || p.username || 'Joueur';
      const remaining = p.jail_until - now;
      const minutes = Math.floor(remaining / 60);
      const seconds = Math.floor(remaining % 60);
      html += `
        <tr>
          <td><div class="player-cell"><div class="player-avatar" style="background:${color}">${initial}</div><div><div class="player-name">${name}</div><div class="player-username">@${p.username||'—'}</div></div></div></td>
          <td>${minutes}min ${seconds}s</td>
          <td><div class="action-btn-group">
            <button class="action-btn success" onclick="doLiberer('${p.user_id}')" title="Libérer"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M8 12l3 3 5-5"/></svg></button>
            <button class="action-btn warning" onclick="openPrisonAction('${p.user_id}')" title="Modifier peine"><svg viewBox="0 0 24 24"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg></button>
          </div></td>
        </tr>
      `;
    });
    html += `</tbody></table></div></div>
      <div style="margin-top:12px;"><div class="btn-row"><button class="btn btn-red" onclick="doClearJail()"><span class="icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg></span>Libérer tous</button></div></div>
    `;
    el.innerHTML = html;
  }

  // ─── RENDER ANNOUNCE ───
  function renderAnnounce() {
    const el = document.getElementById('content');
    el.innerHTML = `
      <div class="action-panel open" style="margin-top:0;">
        <div class="ap-title" style="margin-bottom:12px;">📢 Envoyer une annonce</div>
        <div class="form-group">
          <label>Message</label>
          <textarea id="announceMessage" placeholder="Votre message..." style="width:100%;padding:10px;border-radius:10px;background:rgba(255,255,255,0.04);border:1px solid var(--border-color);color:var(--text-primary);font-family:'Inter',sans-serif;font-size:14px;resize:vertical;min-height:100px;outline:none;"></textarea>
        </div>
        <div class="form-group">
          <label>Destinataires</label>
          <select id="announceTarget" style="width:100%;padding:10px;border-radius:10px;background:rgba(255,255,255,0.04);border:1px solid var(--border-color);color:var(--text-primary);font-family:'Inter',sans-serif;font-size:14px;outline:none;">
            <option value="all">Tous les joueurs + groupes</option>
            <option value="admins">Administrateurs</option>
            <option value="players">Joueurs (non-admins)</option>
          </select>
        </div>
        <div style="font-size:11px;color:var(--text-muted);margin-bottom:12px;">
          💡 L'annonce sera envoyée à tous les groupes actifs et en message privé.
        </div>
        <div class="btn-row"><button class="btn btn-gold" id="announceSendBtn"><span class="icon"><svg viewBox="0 0 24 24"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg></span>Envoyer</button></div>
        <div class="action-msg" id="announceMsg"></div>
      </div>
    `;
    document.getElementById('announceSendBtn').addEventListener('click', sendAnnounce);
  }

  // ─── RENDER DIPLOMAS ───
  function renderDiplomas() {
    const el = document.getElementById('content');
    if (!cachedData) { el.innerHTML = '<div class="loading"><div class="spinner"></div>Chargement...</div>'; return; }
    const diplomas = cachedData.diplomas || [];
    if (!diplomas.length) { el.innerHTML = '<div class="empty">Aucun diplôme accordé.</div>'; return; }
    let html = `<div class="table-wrapper"><div class="table-scroll"><table class="table"><thead><tr><th>Joueur</th><th>Secteur</th><th>Niveau</th></tr></thead><tbody>`;
    diplomas.forEach(d => {
      const player = cachedData.players.find(p => p.user_id == d.user_id);
      const name = player ? player.first_name || player.username || '#'+d.user_id : '#'+d.user_id;
      html += `<tr><td>${name}</td><td>${d.sector}</td><td><span class="badge-status" style="background:rgba(255,215,110,0.12);color:var(--accent-gold);">${d.tier}</span></td></tr>`;
    });
    html += `</tbody></table></div></div>
      <div style="margin-top:12px;"><div class="btn-row"><button class="btn btn-gold" onclick="openDiplomaModal()"><span class="icon"><svg viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></span>Accorder un diplôme</button></div></div>
    `;
    el.innerHTML = html;
  }

  // ─── RENDER PLAYERS ───
  function renderPlayers() {
    const el = document.getElementById('content');
    if (!cachedData) { el.innerHTML = '<div class="loading"><div class="spinner"></div>Chargement...</div>'; return; }
    const players = cachedData.players;
    renderStats(players);
    const matches = players.filter(p => matchesSearch(p));
    if (!matches.length) { el.innerHTML = '<div class="empty">Aucun joueur trouvé.</div>'; return; }
    el.innerHTML = matches.map((p, idx) => {
      const rank = players.indexOf(p)+1;
      const color = getColor(p.user_id);
      const initial = getInitial(p.first_name || p.username);
      const total = (p.balance||0)+(p.bank_balance||0);
      return `
      <div class="card" onclick="selectPlayer(${p.user_id})" style="cursor:pointer;">
        <div class="card-rank">#${rank}</div>
        <div class="card-avatar" style="background:${color}">${initial}</div>
        <div class="card-info"><div class="card-name">${p.first_name||p.username||'Joueur'}</div><div class="card-sub">@${p.username||'—'}</div></div>
        <div class="card-amount">${fmtMoney(total)}</div>
      </div>`;
    }).join('');
  }

  // ─── RENDER HISTORY ───
  function renderHistory() {
    const el = document.getElementById('content');
    if (!cachedData) { el.innerHTML = '<div class="loading"><div class="spinner"></div>Chargement...</div>'; return; }
    const rows = cachedData.activity.filter(a => {
      const blob = `${a.from_user||''} ${a.to_user||''} ${a.reason||''} ${fmtDateShort(a.created_at)} ${a.amount}`;
      return blob.toLowerCase().includes(searchQuery.toLowerCase());
    });
    if (!rows.length) { el.innerHTML = '<div class="empty">Aucune activité trouvée.</div>'; return; }
    el.innerHTML = rows.map(a => {
      const isNegative = a.from_user !== null && a.amount < 0;
      return `
      <div class="card activity-card" style="flex-direction:column;align-items:stretch;">
        <div class="activity-line"><span>${a.from_user?'#'+a.from_user:'Système'} → ${a.to_user?'#'+a.to_user:'Système'}</span><span class="amount ${isNegative?'negative':''}">${fmtMoney(Math.abs(a.amount))}</span></div>
        <div class="activity-reason"><span>${a.reason||'—'}</span><span class="activity-time">${fmtTime(a.created_at)}</span></div>
      </div>`;
    }).join('');
  }

  // ─── RENDER GAMES ───
  function renderGames() {
    const el = document.getElementById('content');
    el.innerHTML = `<div class="game-grid">${GAMES.map(g => `<div class="game-tile"><span class="game-emoji">${g.emoji}</span><div class="game-name">${g.name}</div><div class="game-desc">${g.desc}</div></div>`).join('')}</div>`;
  }

  // ─── RENDER COMMANDS ───
  function renderCommands() {
    const el = document.getElementById('content');
    let html = '';
    Object.entries(COMMANDS).forEach(([cat, cmds]) => {
      html += `<div class="cmd-category">${cat}</div>`;
      cmds.forEach(([name, desc]) => { html += `<div class="cmd-row"><span class="cmd-name">${name}</span><span class="cmd-desc">${desc}</span></div>`; });
    });
    html += `<div class="cmd-category">Administration</div>`;
    Object.entries(ADMIN_COMMANDS).forEach(([cat, cmds]) => {
      html += `<div class="cmd-category" style="color:var(--accent-purple);font-size:10px;margin:4px 0 4px 8px;">${cat}</div>`;
      cmds.forEach(([name, desc]) => { html += `<div class="cmd-row"><span class="cmd-name" style="color:var(--accent-purple);">${name}</span><span class="cmd-desc">${desc}</span><span class="cmd-badge admin">Admin</span></div>`; });
    });
    el.innerHTML = html;
  }

  // ─── ACTIONS ───
  async function sendAction(action, extra, callback) {
    try {
      const res = await fetch('/api/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ initData: tg.initData, action, ...extra }),
      });
      const data = await res.json();
      if (callback) callback(data);
      loadData(true);
    } catch (e) { console.error(e); }
  }

  window.doBan = (target) => { const reason = prompt('Raison du bannissement :'); if (reason === null) return; sendAction('ban', { target, reason: reason || 'Banni depuis le dashboard' }, () => { selectedUserId = null; loadData(true); }); };
  window.doUnban = (target) => sendAction('unban', { target }, () => { selectedUserId = null; loadData(true); });
  window.doFreeze = (target) => sendAction('freeze', { target }, () => { selectedUserId = null; loadData(true); });
  window.doUnfreeze = (target) => sendAction('unfreeze', { target }, () => { selectedUserId = null; loadData(true); });
  window.doSetAdmin = (target) => sendAction('setadmin', { target }, () => { selectedUserId = null; loadData(true); });
  window.doUnsetAdmin = (target) => sendAction('unsetadmin', { target }, () => { selectedUserId = null; loadData(true); });
  window.doLiberer = (target) => sendAction('liberer', { target }, () => { selectedUserId = null; loadData(true); });
  window.doClearJail = () => { if (confirm('Libérer tous les prisonniers ?')) sendAction('clearjail', {}, () => { loadData(true); }); };
  window.doDissoudreCompany = (companyId) => { if (confirm('Dissoudre cette entreprise ?')) sendAction('dissoudre_company', { company_id: companyId }, () => { loadData(true); }); };

  window.openPrisonAction = (userId) => {
    const modal = document.getElementById('prisonActionModal');
    document.getElementById('prisonPlayer').value = userId;
    document.getElementById('prisonMinutes').value = 30;
    document.getElementById('prisonActionMsg').className = 'action-msg';
    document.getElementById('prisonActionMsg').style.display = 'none';
    modal.classList.add('open');
  };

  document.getElementById('prisonProlongerBtn').addEventListener('click', () => {
    const target = document.getElementById('prisonPlayer').value;
    const minutes = document.getElementById('prisonMinutes').value;
    const msg = document.getElementById('prisonActionMsg');
    if (!target || !minutes || parseInt(minutes) <= 0) { msg.className = 'action-msg err'; msg.textContent = '❌ Entrez un joueur et un nombre de minutes valide.'; msg.style.display = 'block'; return; }
    sendAction('prolonger', { target, minutes: parseInt(minutes) }, (data) => {
      msg.className = data.error ? 'action-msg err' : 'action-msg ok';
      msg.textContent = data.error ? '❌ ' + data.error : '✅ ' + data.message;
      msg.style.display = 'block';
      if (!data.error) { document.getElementById('prisonActionModal').classList.remove('open'); loadData(true); }
    });
  });

  document.getElementById('prisonAggraverBtn').addEventListener('click', () => {
    const target = document.getElementById('prisonPlayer').value;
    const minutes = document.getElementById('prisonMinutes').value;
    const msg = document.getElementById('prisonActionMsg');
    if (!target || !minutes || parseInt(minutes) <= 0) { msg.className = 'action-msg err'; msg.textContent = '❌ Entrez un joueur et un nombre de minutes valide.'; msg.style.display = 'block'; return; }
    sendAction('aggraver', { target, minutes: parseInt(minutes) }, (data) => {
      msg.className = data.error ? 'action-msg err' : 'action-msg ok';
      msg.textContent = data.error ? '❌ ' + data.error : '✅ ' + data.message;
      msg.style.display = 'block';
      if (!data.error) { document.getElementById('prisonActionModal').classList.remove('open'); loadData(true); }
    });
  });

  document.getElementById('prisonAllegerBtn').addEventListener('click', () => {
    const target = document.getElementById('prisonPlayer').value;
    const minutes = document.getElementById('prisonMinutes').value;
    const msg = document.getElementById('prisonActionMsg');
    if (!target || !minutes || parseInt(minutes) <= 0) { msg.className = 'action-msg err'; msg.textContent = '❌ Entrez un joueur et un nombre de minutes valide.'; msg.style.display = 'block'; return; }
    sendAction('alleger', { target, minutes: parseInt(minutes) }, (data) => {
      msg.className = data.error ? 'action-msg err' : 'action-msg ok';
      msg.textContent = data.error ? '❌ ' + data.error : '✅ ' + data.message;
      msg.style.display = 'block';
      if (!data.error) { document.getElementById('prisonActionModal').classList.remove('open'); loadData(true); }
    });
  });

  document.getElementById('prisonActionModal').addEventListener('click', (e) => {
    if (e.target.id === 'prisonActionModal') document.getElementById('prisonActionModal').classList.remove('open');
  });

  // ─── MONEY MODAL ───
  window.openMoneyModal = (userId) => {
    const modal = document.getElementById('moneyModal');
    const player = cachedData?.players?.find(p => p.user_id == userId);
    if (!player) { alert('Joueur introuvable.'); return; }
    currentMoneyTarget = userId;
    document.getElementById('moneyAvatar').textContent = getInitial(player.first_name || player.username);
    document.getElementById('moneyAvatar').style.background = getColor(player.user_id);
    document.getElementById('moneyPlayerName').textContent = player.first_name || player.username || 'Joueur';
    document.getElementById('moneyPlayerBalance').textContent = 'Solde : ' + fmtMoney(player.balance) + ' | Banque : ' + fmtMoney(player.total_bank||0);
    document.getElementById('moneyAmount').value = '';
    document.getElementById('moneyModalMsg').className = 'action-msg';
    document.getElementById('moneyModalMsg').style.display = 'none';
    modal.classList.add('open');
  };

  document.getElementById('moneyAddBtn').addEventListener('click', () => {
    const amount = document.getElementById('moneyAmount').value;
    const msg = document.getElementById('moneyModalMsg');
    if (!amount || parseInt(amount) <= 0) { msg.className = 'action-msg err'; msg.textContent = '❌ Entrez un montant valide.'; msg.style.display = 'block'; return; }
    sendAction('addmoney', { target: currentMoneyTarget, amount: parseInt(amount) }, (data) => {
      msg.className = data.error ? 'action-msg err' : 'action-msg ok';
      msg.textContent = data.error ? '❌ ' + data.error : '✅ ' + data.message;
      msg.style.display = 'block';
      if (!data.error) { document.getElementById('moneyAmount').value = ''; loadData(true); }
    });
  });

  document.getElementById('moneyRemoveBtn').addEventListener('click', () => {
    const amount = document.getElementById('moneyAmount').value;
    const msg = document.getElementById('moneyModalMsg');
    if (!amount || parseInt(amount) <= 0) { msg.className = 'action-msg err'; msg.textContent = '❌ Entrez un montant valide.'; msg.style.display = 'block'; return; }
    sendAction('removemoney', { target: currentMoneyTarget, amount: parseInt(amount) }, (data) => {
      msg.className = data.error ? 'action-msg err' : 'action-msg ok';
      msg.textContent = data.error ? '❌ ' + data.error : '✅ ' + data.message;
      msg.style.display = 'block';
      if (!data.error) { document.getElementById('moneyAmount').value = ''; loadData(true); }
    });
  });

  document.getElementById('moneyModal').addEventListener('click', (e) => {
    if (e.target.id === 'moneyModal') document.getElementById('moneyModal').classList.remove('open');
  });

  // ─── DIPLOMA MODAL ───
  window.openDiplomaModal = (userId) => {
    const modal = document.getElementById('diplomaModal');
    if (userId) document.getElementById('diplomaPlayer').value = userId;
    else document.getElementById('diplomaPlayer').value = '';
    document.getElementById('diplomaMsg').className = 'action-msg';
    document.getElementById('diplomaMsg').style.display = 'none';
    modal.classList.add('open');
  };

  document.getElementById('diplomaGiveBtn').addEventListener('click', () => {
    const target = document.getElementById('diplomaPlayer').value;
    const sector = document.getElementById('diplomaSector').value;
    const tier = document.getElementById('diplomaTier').value;
    const msg = document.getElementById('diplomaMsg');
    if (!target) { msg.className = 'action-msg err'; msg.textContent = '❌ Entrez un joueur.'; msg.style.display = 'block'; return; }
    sendAction('give_diploma', { target, sector, tier }, (data) => {
      msg.className = data.error ? 'action-msg err' : 'action-msg ok';
      msg.textContent = data.error ? '❌ ' + data.error : '✅ ' + data.message;
      msg.style.display = 'block';
      if (!data.error) { document.getElementById('diplomaModal').classList.remove('open'); loadData(true); }
    });
  });

  document.getElementById('diplomaRemoveBtn').addEventListener('click', () => {
    const target = document.getElementById('diplomaPlayer').value;
    const sector = document.getElementById('diplomaSector').value;
    const tier = document.getElementById('diplomaTier').value;
    const msg = document.getElementById('diplomaMsg');
    if (!target) { msg.className = 'action-msg err'; msg.textContent = '❌ Entrez un joueur.'; msg.style.display = 'block'; return; }
    sendAction('remove_diploma', { target, sector, tier }, (data) => {
      msg.className = data.error ? 'action-msg err' : 'action-msg ok';
      msg.textContent = data.error ? '❌ ' + data.error : '✅ ' + data.message;
      msg.style.display = 'block';
      if (!data.error) { document.getElementById('diplomaModal').classList.remove('open'); loadData(true); }
    });
  });

  document.getElementById('diplomaModal').addEventListener('click', (e) => {
    if (e.target.id === 'diplomaModal') document.getElementById('diplomaModal').classList.remove('open');
  });

  // ─── ANNOUNCE ───
  async function sendAnnounce() {
    const message = document.getElementById('announceMessage').value;
    const target = document.getElementById('announceTarget').value;
    const msg = document.getElementById('announceMsg');
    if (!message.trim()) { msg.className = 'action-msg err'; msg.textContent = '❌ Entrez un message.'; msg.style.display = 'block'; return; }
    
    msg.className = 'action-msg';
    msg.textContent = '⏳ Envoi en cours...';
    msg.style.display = 'block';
    
    try {
      const res = await fetch('/api/announce', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ initData: tg.initData, message, target }),
      });
      const data = await res.json();
      if (data.error) {
        msg.className = 'action-msg err';
        msg.textContent = '❌ ' + data.error;
      } else {
        msg.className = 'action-msg ok';
        msg.textContent = '✅ ' + data.message;
        document.getElementById('announceMessage').value = '';
      }
    } catch (e) {
      msg.className = 'action-msg err';
      msg.textContent = '❌ Erreur de connexion.';
    }
    msg.style.display = 'block';
  }

  // ─── PROFILE MODAL ───
  window.openProfile = async (userId) => {
    const modal = document.getElementById('profileModal');
    const content = document.getElementById('profileContent');
    content.innerHTML = '<div class="loading"><div class="spinner"></div>Chargement...</div>';
    modal.classList.add('open');
    try {
      const res = await fetch('/api/player?initData='+encodeURIComponent(tg.initData)+'&target='+encodeURIComponent(userId));
      const p = await res.json();
      if (p.error) { content.innerHTML = '<div class="error">❌ '+p.error+'</div>'; return; }
      const color = getColor(p.user_id);
      const initial = getInitial(p.first_name||p.username);
      const total = (p.balance||0)+(p.bank_balance||0);
      const totalBank = p.total_bank || 0;
      let badges = [];
      if (p.is_admin) badges.push('<span class="badge badge-admin">Admin</span>');
      if (p.banned) badges.push('<span class="badge badge-banned">Banni</span>');
      if (p.in_jail) badges.push('<span class="badge badge-jail">Prison</span>');
      if (p.company_role === 'PDG') badges.push('<span class="badge badge-ceo">PDG</span>');
      content.innerHTML = `
        <div class="profile-header"><div class="profile-avatar-large" style="background:${color}">${initial}</div>
          <div><div class="profile-name">${p.first_name||p.username||'Joueur'}</div><div class="profile-sub">@${p.username||'—'} · ID ${p.user_id}</div></div>
        </div>
        <div class="profile-badges">${badges.join('')||'<span style="color:var(--text-muted);font-size:11px;">Aucun statut</span>'}</div>
        <div class="profile-stat-grid">
          <div class="profile-stat"><div class="profile-stat-label">En poche</div><div class="profile-stat-value">${fmtMoney(p.balance)}</div></div>
          <div class="profile-stat"><div class="profile-stat-label">En banque</div><div class="profile-stat-value">${fmtMoney(totalBank)}</div></div>
          <div class="profile-stat"><div class="profile-stat-label">Fortune totale</div><div class="profile-stat-value" style="color:var(--accent-gold);">${fmtMoney(total)}</div></div>
          <div class="profile-stat"><div class="profile-stat-label">Karma</div><div class="profile-stat-value">${p.karma||0}</div></div>
          <div class="profile-stat"><div class="profile-stat-label">Salaire</div><div class="profile-stat-value">${fmtMoney(p.salary||0)}</div></div>
          <div class="profile-stat"><div class="profile-stat-label">Commandes</div><div class="profile-stat-value">${p.cmd_count||0}</div></div>
        </div>
        ${p.company ? `<div style="font-size:13px;color:var(--text-secondary);margin-bottom:10px;">🏢 ${p.company.name} (${p.company.sector}) ${p.company_role?'· '+p.company_role:''}</div>` : '<div style="font-size:13px;color:var(--text-muted);margin-bottom:10px;">🏢 Aucune entreprise</div>'}
        ${p.diplomas && p.diplomas.length ? `<div style="display:flex;gap:6px;flex-wrap:wrap;">${p.diplomas.map(d => `<span class="diploma-chip">${d.tier||''} · ${d.sector||''}</span>`).join('')}</div>` : '<div style="font-size:12px;color:var(--text-muted);">🎓 Aucun diplôme</div>'}
      `;
    } catch(e) { content.innerHTML = '<div class="error">❌ Erreur de connexion.</div>'; }
  };
  document.getElementById('profileModal').addEventListener('click', (e) => {
    if (e.target.id === 'profileModal') document.getElementById('profileModal').classList.remove('open');
  });

  // ─── SEARCH ───
  const searchInput = document.getElementById('searchInput');
  const searchClear = document.getElementById('searchClear');
  searchInput.addEventListener('input', (e) => {
    searchQuery = e.target.value;
    selectedUserId = null;
    render();
    if (searchQuery.length > 0) searchClear.classList.add('visible');
    else searchClear.classList.remove('visible');
  });
  searchClear.addEventListener('click', () => {
    searchInput.value = '';
    searchQuery = '';
    searchClear.classList.remove('visible');
    selectedUserId = null;
    render();
  });

  // ─── NAVIGATION ───
  document.querySelectorAll('.navitem').forEach(item => {
    item.addEventListener('click', () => {
      document.querySelectorAll('.navitem').forEach(t => t.classList.remove('active'));
      item.classList.add('active');
      currentTab = item.dataset.tab;
      selectedUserId = null;
      render();
    });
  });

  function render() {
    switch(currentTab) {
      case 'users': renderUsers(); break;
      case 'admins': renderAdmins(); break;
      case 'companies': renderCompanies(); break;
      case 'prison': renderPrison(); break;
      case 'announce': renderAnnounce(); break;
      case 'diplomas': renderDiplomas(); break;
      case 'players': renderPlayers(); break;
      case 'history': renderHistory(); break;
      case 'games': renderGames(); break;
      case 'commands': renderCommands(); break;
      default: renderUsers();
    }
  }

  function loadData(silent) {
    if (!silent) document.getElementById('content').innerHTML = '<div class="loading"><div class="spinner"></div>Chargement...</div>';
    fetch('/api/dashboard?initData='+encodeURIComponent(tg.initData))
      .then(r => r.json())
      .then(data => {
        if (data.error) { document.getElementById('content').innerHTML = '<div class="error">❌ '+data.error+'</div>'; return; }
        cachedData = data;
        render();
      })
      .catch(() => { document.getElementById('content').innerHTML = '<div class="error">❌ Erreur de connexion.</div>'; });
  }

  loadData(false);
</script>
</body>
</html>
"""


@app_flask.route("/")
def index():
    return Response(_HTML_PAGE, mimetype="text/html")


def run_dashboard():
    app_flask.run(host="0.0.0.0", port=DASHBOARD_PORT, debug=False, use_reloader=False)


def start_dashboard_thread():
    t = threading.Thread(target=run_dashboard, daemon=True)
    t.start()


# ─── Tunnel Cloudflare ──────────────────────────────────────────────────────
CLOUDFLARED_PATH = os.path.join(os.path.dirname(__file__), "cloudflared")
CURRENT_TUNNEL_URL = None


def _ensure_cloudflared_binary() -> bool:
    if os.path.exists(CLOUDFLARED_PATH):
        return True
    try:
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
        urllib.request.urlretrieve(url, CLOUDFLARED_PATH)
        st = os.stat(CLOUDFLARED_PATH)
        os.chmod(CLOUDFLARED_PATH, st.st_mode | stat.S_IEXEC)
        return True
    except Exception as e:
        print(f"⚠️ Impossible de télécharger cloudflared : {e}")
        return False


def _run_tunnel_and_watch():
    global CURRENT_TUNNEL_URL
    if not _ensure_cloudflared_binary():
        return

    process = subprocess.Popen(
        [CLOUDFLARED_PATH, "tunnel", "--url", f"http://localhost:{DASHBOARD_PORT}", "--no-autoupdate"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

    for line in process.stdout:
        match = url_pattern.search(line)
        if match:
            CURRENT_TUNNEL_URL = match.group(0)
            print(f"📊 Dashboard accessible sur : {CURRENT_TUNNEL_URL}")


def start_tunnel_thread():
    t = threading.Thread(target=_run_tunnel_and_watch, daemon=True)
    t.start()


def get_dashboard_url(timeout: float = 15.0) -> str | None:
    waited = 0.0
    while CURRENT_TUNNEL_URL is None and waited < timeout:
        time.sleep(0.5)
        waited += 0.5
    return CURRENT_TUNNEL_URL