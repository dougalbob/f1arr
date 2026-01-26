#!/usr/bin/env python3
"""
F1 Grabber Web UI V2
====================

Web interface for F1 grabber with:
- Quick actions (status, scheduled checks)
- Backfill controls
- Overlay utility integration
- Library status viewer

Usage:
    python f1_webui.py
    
Then open http://your-unraid-ip:5678 in a browser.
"""

import os
import sys
import subprocess
import threading
import json
import re
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, jsonify

# Add paths for imports
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from f1_common import (
    SessionType,
    parse_prefix,
    load_calendar,
    get_round_by_number,
    F1_LIBRARY_PATH,
    get_config_dir,
)

# Configuration
PYTHON_PATH = "/opt/venv/bin/python3"
GRABBER_SCRIPT = SCRIPT_DIR / "f1_grabber.py"
OVERLAY_SCRIPT = SCRIPT_DIR / "f1_overlay_utility.py"
CONFIG_DIR = get_config_dir()
CONFIG_FILE = CONFIG_DIR / "webui_settings.json"

# Available seasons
SEASONS = [2024, 2025, 2026]

# Session types with display names and defaults
SESSION_TYPES = {
    "WEEKEND_WARMUP": {"name": "Weekend Warm Up", "default": True},
    "TEAM_PRINCIPALS": {"name": "Team Principals Press Conference", "default": False},
    "DRIVER_PRESS_CONFERENCE": {"name": "Driver Press Conference", "default": True},
    "PADDOCK_UNCUT": {"name": "Paddock Uncut", "default": False},
    "PADDOCK_SHOW": {"name": "Paddock Show", "default": True},
    "F1_SHOW": {"name": "F1 Show", "default": False},
    "PRACTICE_1": {"name": "Practice 1", "default": True},
    "PRACTICE_2": {"name": "Practice 2", "default": True},
    "PRACTICE_3": {"name": "Practice 3", "default": True},
    "SPRINT_QUALIFYING": {"name": "Sprint Qualifying", "default": True},
    "SPRINT": {"name": "Sprint", "default": True},
    "POST_SPRINT_SHOW": {"name": "Post Sprint Show", "default": False},
    "TEDS_SPRINT_NOTEBOOK": {"name": "Ted's Sprint Notebook", "default": False},
    "PRE_QUALIFYING_SHOW": {"name": "Pre Qualifying Show", "default": False},
    "QUALIFYING": {"name": "Qualifying", "default": True},
    "POST_QUALIFYING_SHOW": {"name": "Post Qualifying Show", "default": False},
    "TEDS_QUALIFYING_NOTEBOOK": {"name": "Ted's Qualifying Notebook", "default": False},
    "PRE_RACE_SHOW": {"name": "Pre Race Show", "default": True},
    "RACE": {"name": "Race", "default": True},
    "RACE_UNCUT": {"name": "Race Uncut", "default": False},
    "POST_RACE_SHOW": {"name": "Post Race Show", "default": False},
    "TEDS_RACE_NOTEBOOK": {"name": "Ted's Race Notebook", "default": False},
    "TEDS_NOTEBOOK": {"name": "Ted's Notebook", "default": True},
    "JOLYON_PALMERS_ANALYSIS": {"name": "Jolyon Palmer's Analysis", "default": True},
    "FULL_EVENT": {"name": "Full Event Replay", "default": False},
}

# Group sessions for display
SESSION_GROUPS = {
    "Pre-Weekend": ["WEEKEND_WARMUP", "TEAM_PRINCIPALS", "DRIVER_PRESS_CONFERENCE"],
    "Practice": ["PRACTICE_1", "PRACTICE_2", "PRACTICE_3"],
    "Sprint Weekend": ["SPRINT_QUALIFYING", "SPRINT", "POST_SPRINT_SHOW", "TEDS_SPRINT_NOTEBOOK"],
    "Qualifying": ["PRE_QUALIFYING_SHOW", "QUALIFYING", "POST_QUALIFYING_SHOW", "TEDS_QUALIFYING_NOTEBOOK"],
    "Race": ["PRE_RACE_SHOW", "RACE", "RACE_UNCUT", "POST_RACE_SHOW", "TEDS_RACE_NOTEBOOK"],
    "Analysis & Shows": ["TEDS_NOTEBOOK", "JOLYON_PALMERS_ANALYSIS", "PADDOCK_SHOW", "PADDOCK_UNCUT", "F1_SHOW"],
    "Other": ["FULL_EVENT"],
}

# Desired sessions for library scanning (matches grabber config)
DESIRED_SESSIONS = {
    SessionType.WEEKEND_WARMUP,
    SessionType.DRIVER_PRESS_CONFERENCE,
    SessionType.PADDOCK_SHOW,
    SessionType.PRACTICE_1,
    SessionType.PRACTICE_2,
    SessionType.PRACTICE_3,
    SessionType.SPRINT_QUALIFYING,
    SessionType.SPRINT,
    SessionType.QUALIFYING,
    SessionType.PRE_RACE_SHOW,
    SessionType.RACE,
    SessionType.TEDS_NOTEBOOK,
    SessionType.JOLYON_PALMERS_ANALYSIS,
}

app = Flask(__name__)

# Store command output
command_output = {
    "running": False,
    "command": "",
    "output": "",
    "timestamp": None
}


def load_calendar_data(year: int) -> dict:
    """Load calendar for a given year."""
    calendar_file = CONFIG_DIR / f"f1_{year}.json"
    if calendar_file.exists():
        try:
            with open(calendar_file, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {"rounds": []}


def get_rounds_for_season(year: int) -> list:
    """Get list of rounds with venue names for a season."""
    calendar = load_calendar_data(year)
    rounds = []
    for r in calendar.get("rounds", []):
        rounds.append({
            "number": r.get("round"),
            "venue": r.get("venue"),
            "date": r.get("date"),
            "sprint_weekend": r.get("sprint_weekend", False)
        })
    return rounds


def get_next_race_info() -> dict:
    """Get information about the next race weekend."""
    today = datetime.now().date()
    
    for year in [today.year, today.year + 1]:
        calendar = load_calendar_data(year)
        
        for r in calendar.get("rounds", []):
            race_date_str = r.get("date")
            weekend_start_str = r.get("weekend_start")
            
            if not race_date_str:
                continue
            
            try:
                race_date = datetime.strptime(race_date_str, "%Y-%m-%d").date()
                
                if weekend_start_str:
                    weekend_start = datetime.strptime(weekend_start_str, "%Y-%m-%d").date()
                else:
                    weekend_start = race_date - timedelta(days=2)
                
                weekend_end = race_date + timedelta(days=1)
                
                if weekend_start <= today <= weekend_end:
                    return {
                        "round": r.get("round"),
                        "venue": r.get("venue"),
                        "date": race_date_str,
                        "sprint_weekend": r.get("sprint_weekend", False),
                        "status": "now",
                        "countdown": "Race Weekend Now!",
                        "days": 0
                    }
                
                if weekend_start > today:
                    days_until = (weekend_start - today).days
                    
                    if days_until == 1:
                        countdown = "Tomorrow!"
                    elif days_until < 7:
                        countdown = f"{days_until} days"
                    elif days_until < 14:
                        countdown = f"{days_until} days (next week)"
                    else:
                        weeks = days_until // 7
                        countdown = f"{days_until} days (~{weeks} weeks)"
                    
                    return {
                        "round": r.get("round"),
                        "venue": r.get("venue"),
                        "date": race_date_str,
                        "sprint_weekend": r.get("sprint_weekend", False),
                        "status": "upcoming",
                        "countdown": countdown,
                        "days": days_until
                    }
            
            except ValueError:
                continue
    
    return {
        "round": None,
        "venue": "No upcoming races",
        "date": None,
        "sprint_weekend": False,
        "status": "none",
        "countdown": "Season ended",
        "days": None
    }


def load_settings():
    """Load settings from config file."""
    defaults = {
        "max_grabs_per_run": 3,
        "sessions": {k: v["default"] for k, v in SESSION_TYPES.items()}
    }
    
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                saved = json.load(f)
                defaults.update(saved)
        except Exception:
            pass
    
    return defaults


def save_settings(settings):
    """Save settings to config file."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(settings, f, indent=2)


def update_grabber_config(settings):
    """Update the f1_grabber.py with new settings."""
    grabber_path = SCRIPT_DIR / "f1_grabber.py"
    
    try:
        with open(grabber_path, 'r') as f:
            content = f.read()
        
        content = re.sub(
            r'MAX_GRABS_PER_RUN\s*=\s*\d+',
            f'MAX_GRABS_PER_RUN = {settings["max_grabs_per_run"]}',
            content
        )
        
        sessions_block = "DESIRED_SESSIONS = {\n"
        for key, info in SESSION_TYPES.items():
            enabled = settings["sessions"].get(key, info["default"])
            sessions_block += f"    SessionType.{key}: {enabled},\n"
        sessions_block += "}"
        
        content = re.sub(
            r'DESIRED_SESSIONS\s*=\s*\{[^}]+\}',
            sessions_block,
            content,
            flags=re.DOTALL
        )
        
        with open(grabber_path, 'w') as f:
            f.write(content)
        
        return True
    except Exception as e:
        print(f"Error updating grabber config: {e}")
        return False


# =============================================================================
# LIBRARY SCANNING
# =============================================================================

def scan_library_for_season(year: int) -> dict:
    """
    Scan the library to find existing sessions for a season.
    
    Returns: {round_num: {session_type: folder_path, ...}, ...}
    """
    season_path = os.path.join(F1_LIBRARY_PATH, str(year))
    
    if not os.path.exists(season_path):
        return {}
    
    existing = {}
    
    for round_folder in os.listdir(season_path):
        round_path = os.path.join(season_path, round_folder)
        
        if not os.path.isdir(round_path):
            continue
        
        round_match = re.match(r'Round\s*(\d+)', round_folder)
        if not round_match:
            continue
        
        round_num = int(round_match.group(1))
        existing[round_num] = {}
        
        for session_folder in os.listdir(round_path):
            session_path = os.path.join(round_path, session_folder)
            
            if not os.path.isdir(session_path):
                continue
            
            parsed = parse_prefix(session_folder)
            if parsed:
                folder_year, folder_round, session_type = parsed
                
                if folder_year == year and folder_round == round_num:
                    existing[round_num][session_type] = session_path
    
    return existing


def get_library_status(year: int, round_filter: int = None) -> dict:
    """
    Get library status for display.
    
    Returns: {
        "rounds": [
            {
                "number": 1,
                "venue": "Australia",
                "date": "2025-03-16",
                "sprint_weekend": False,
                "sessions": [
                    {"type": SessionType.PRACTICE_1, "name": "Practice 1", "exists": True},
                    ...
                ],
                "grabbed_count": 5,
                "desired_count": 7,
            },
            ...
        ],
        "total_grabbed": 45,
        "total_desired": 70,
    }
    """
    calendar = load_calendar_data(year)
    existing = scan_library_for_season(year)
    settings = load_settings()
    
    # Build desired sessions set from settings
    desired_set = set()
    for key, enabled in settings.get("sessions", {}).items():
        if enabled:
            try:
                session_type = SessionType[key]
                desired_set.add(session_type)
            except KeyError:
                pass
    
    # If no settings, use defaults
    if not desired_set:
        desired_set = DESIRED_SESSIONS
    
    result = {
        "rounds": [],
        "total_grabbed": 0,
        "total_desired": 0,
    }
    
    today = datetime.now().date()
    
    for r in calendar.get("rounds", []):
        round_num = r.get("round")
        
        if round_filter and round_num != round_filter:
            continue
        
        # Skip future rounds
        race_date_str = r.get("date", "")
        if race_date_str:
            try:
                race_date = datetime.strptime(race_date_str, "%Y-%m-%d").date()
                if race_date > today:
                    continue
            except ValueError:
                pass
        
        is_sprint = r.get("sprint_weekend", False)
        round_existing = existing.get(round_num, {})
        
        sessions = []
        grabbed_count = 0
        desired_count = 0
        
        for session_type in sorted(desired_set, key=lambda x: int(x)):
            # Skip FP3 for sprint weekends
            if is_sprint and session_type == SessionType.PRACTICE_3:
                continue
            # Skip sprint sessions for non-sprint weekends
            if not is_sprint and session_type in (SessionType.SPRINT, SessionType.SPRINT_QUALIFYING):
                continue
            
            exists = session_type in round_existing
            
            sessions.append({
                "type": session_type,
                "name": session_type.display_name,
                "exists": exists,
            })
            
            desired_count += 1
            if exists:
                grabbed_count += 1
        
        result["rounds"].append({
            "number": round_num,
            "venue": r.get("venue", "Unknown"),
            "date": r.get("date", ""),
            "sprint_weekend": is_sprint,
            "sessions": sessions,
            "grabbed_count": grabbed_count,
            "desired_count": desired_count,
        })
        
        result["total_grabbed"] += grabbed_count
        result["total_desired"] += desired_count
    
    return result


# =============================================================================
# HTML TEMPLATE
# =============================================================================

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>F1 Grabber</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #000000;
            color: #eee;
        }
        h1 {
            color: #e10600;
            border-bottom: 2px solid #e10600;
            padding-bottom: 10px;
        }
        h2 {
            color: #fff;
            margin-top: 30px;
            margin-bottom: 15px;
        }
        h3 {
            color: #ccc;
            margin: 15px 0 10px 0;
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .card {
            background: #0a0a12;
            border: 1px solid #1a1a2e;
            border-radius: 8px;
            padding: 20px;
            margin: 15px 0;
        }
        .next-race {
            background: linear-gradient(135deg, #e10600 0%, #8b0000 100%);
            border-radius: 8px;
            padding: 20px;
            margin: 15px 0;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 15px;
        }
        .next-race.race-now {
            background: linear-gradient(135deg, #27ae60 0%, #1e8449 100%);
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.8; }
        }
        .next-race-info { flex: 1; }
        .next-race-title {
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 1px;
            opacity: 0.9;
            margin-bottom: 5px;
        }
        .next-race-venue { font-size: 24px; font-weight: bold; }
        .next-race-round { font-size: 14px; opacity: 0.9; margin-top: 5px; }
        .sprint-badge {
            background: #f39c12;
            color: #000;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 12px;
            font-weight: bold;
            margin-left: 10px;
        }
        .next-race-countdown { text-align: right; }
        .countdown-number { font-size: 36px; font-weight: bold; }
        .countdown-label { font-size: 14px; opacity: 0.9; }
        .form-group { margin: 15px 0; }
        label { display: block; margin-bottom: 5px; font-weight: 500; }
        select, button, input[type="number"] {
            padding: 12px;
            border-radius: 5px;
            border: 1px solid #2a2a3e;
            font-size: 16px;
        }
        select, input[type="number"] {
            background: #141420;
            color: #fff;
            margin-bottom: 10px;
        }
        select:focus, input[type="number"]:focus {
            outline: none;
            border-color: #e10600;
        }
        select { width: 100%; }
        input[type="number"] { width: 100px; text-align: center; }
        button {
            background: #e10600;
            color: white;
            cursor: pointer;
            font-weight: bold;
            transition: background 0.2s;
            padding: 12px 24px;
            border: none;
        }
        button:hover { background: #ff1e00; }
        button:disabled { background: #333; color: #666; cursor: not-allowed; }
        button.secondary { background: #1a1a2e; border: 1px solid #2a2a3e; }
        button.secondary:hover { background: #2a2a3e; }
        button.success { background: #27ae60; }
        button.success:hover { background: #2ecc71; }
        button.warning { background: #f39c12; color: #000; }
        button.warning:hover { background: #f1c40f; }
        button.small { padding: 6px 12px; font-size: 12px; }
        .output {
            background: #050508;
            border: 1px solid #1a1a2e;
            border-radius: 5px;
            padding: 15px;
            font-family: 'Monaco', 'Menlo', 'Consolas', monospace;
            font-size: 13px;
            white-space: pre-wrap;
            word-wrap: break-word;
            max-height: 400px;
            overflow-y: auto;
            margin-top: 15px;
            color: #0f0;
        }
        .status { padding: 10px 15px; border-radius: 5px; margin: 10px 0; display: inline-block; }
        .status.running { background: #f39c12; color: #000; }
        .status.ready { background: #27ae60; }
        .quick-buttons {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 10px;
            margin-top: 15px;
        }
        .quick-buttons button { width: 100%; }
        .checkbox-group { display: flex; align-items: center; gap: 10px; margin: 8px 0; }
        .checkbox-group input[type="checkbox"] { width: 18px; height: 18px; cursor: pointer; accent-color: #e10600; }
        .checkbox-group label { margin: 0; cursor: pointer; font-weight: normal; }
        .timestamp { font-size: 12px; color: #666; margin-top: 5px; }
        .sessions-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
        }
        .session-group {
            background: #0d0d14;
            border: 1px solid #1a1a2e;
            border-radius: 5px;
            padding: 15px;
        }
        .session-group h3 { margin-top: 0; color: #e10600; }
        .inline-form { display: flex; align-items: center; gap: 15px; flex-wrap: wrap; }
        .inline-form label { margin: 0; }
        .save-notice {
            background: #27ae60;
            color: white;
            padding: 10px 15px;
            border-radius: 5px;
            margin: 10px 0;
            display: none;
        }
        .save-notice.show { display: block; }
        .tabs { display: flex; gap: 5px; margin-bottom: 0; }
        .tab {
            padding: 10px 20px;
            background: #0a0a12;
            border: 1px solid #1a1a2e;
            border-bottom: none;
            color: #888;
            cursor: pointer;
            border-radius: 5px 5px 0 0;
            font-size: 14px;
        }
        .tab:hover { color: #fff; }
        .tab.active { background: #0a0a12; color: #fff; border-color: #1a1a2e; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .button-row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 15px; }
        
        /* Library status styles */
        .library-round {
            background: #0d0d14;
            border: 1px solid #1a1a2e;
            border-radius: 5px;
            padding: 15px;
            margin: 10px 0;
        }
        .library-round-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
            flex-wrap: wrap;
            gap: 10px;
        }
        .library-round-title { font-weight: bold; font-size: 16px; }
        .library-round-stats { color: #888; font-size: 14px; }
        .library-sessions {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 5px;
        }
        .library-session {
            padding: 5px 10px;
            border-radius: 3px;
            font-size: 14px;
        }
        .library-session.exists { background: rgba(39, 174, 96, 0.2); color: #27ae60; }
        .library-session.missing { background: rgba(231, 76, 60, 0.2); color: #e74c3c; }
        .library-summary {
            background: linear-gradient(135deg, #1a1a2e 0%, #0a0a12 100%);
            border-radius: 8px;
            padding: 20px;
            margin: 15px 0;
            text-align: center;
        }
        .library-summary-number { font-size: 48px; font-weight: bold; color: #e10600; }
        .library-summary-label { font-size: 14px; color: #888; }
        .progress-bar {
            height: 8px;
            background: #1a1a2e;
            border-radius: 4px;
            overflow: hidden;
            margin-top: 10px;
        }
        .progress-bar-fill {
            height: 100%;
            background: linear-gradient(90deg, #e10600, #27ae60);
            transition: width 0.3s;
        }
        .library-filter { display: flex; gap: 15px; align-items: center; flex-wrap: wrap; margin-bottom: 20px; }
        .library-filter select { width: auto; min-width: 150px; }
        
        /* Output scrollbar */
        .output::-webkit-scrollbar { width: 8px; }
        .output::-webkit-scrollbar-track { background: #0a0a12; }
        .output::-webkit-scrollbar-thumb { background: #2a2a3e; border-radius: 4px; }
        .output::-webkit-scrollbar-thumb:hover { background: #3a3a4e; }
    </style>
</head>
<body>
    <h1>🏎️ F1 Grabber</h1>
    
    <!-- Next Race Countdown -->
    <div class="next-race {{ 'race-now' if next_race.status == 'now' }}">
        <div class="next-race-info">
            <div class="next-race-title">
                {% if next_race.status == 'now' %}🏁 Race Weekend In Progress
                {% else %}🏎️ Next Race Weekend{% endif %}
            </div>
            <div class="next-race-venue">
                {{ next_race.venue }}
                {% if next_race.sprint_weekend %}<span class="sprint-badge">SPRINT</span>{% endif %}
            </div>
            {% if next_race.round %}
            <div class="next-race-round">Round {{ next_race.round }} • {{ next_race.date }}</div>
            {% endif %}
        </div>
        <div class="next-race-countdown">
            {% if next_race.status == 'now' %}
                <div class="countdown-number">🟢 LIVE</div>
                <div class="countdown-label">Race Weekend Now!</div>
            {% elif next_race.days is not none %}
                <div class="countdown-number">{{ next_race.days }}</div>
                <div class="countdown-label">{{ 'day' if next_race.days == 1 else 'days' }} until Friday</div>
            {% else %}
                <div class="countdown-label">{{ next_race.countdown }}</div>
            {% endif %}
        </div>
    </div>
    
    <div class="status {{ 'running' if status.running else 'ready' }}">
        {% if status.running %}⏳ Running: {{ status.command }}
        {% else %}✅ Ready{% endif %}
    </div>

    <!-- Tabs -->
    <div class="tabs">
        <button class="tab active" onclick="showTab('commands')">Commands</button>
        <button class="tab" onclick="showTab('library')">Library</button>
        <button class="tab" onclick="showTab('settings')">Settings</button>
    </div>

    <!-- Commands Tab -->
    <div id="commands-tab" class="tab-content active">
        <!-- Quick Actions -->
        <div class="card">
            <h2>Quick Actions</h2>
            <div class="quick-buttons">
                <button onclick="runCommand('status')" {{ 'disabled' if status.running }}>📊 Check Status</button>
                <button onclick="runCommand('scheduled')" {{ 'disabled' if status.running }}>🔍 Run Scheduled Check</button>
                <button onclick="runCommand('scheduled --dry-run')" class="secondary" {{ 'disabled' if status.running }}>👁️ Scheduled (Dry Run)</button>
                <button onclick="refreshOutput()" class="secondary">🔄 Refresh Output</button>
            </div>
        </div>

        <!-- Backfill & Overlay -->
        <div class="card">
            <h2>Backfill & Overlay Tools</h2>
            <div class="form-group">
                <label>Season</label>
                <select id="season" onchange="updateRounds()">
                    {% for year in seasons %}<option value="{{ year }}" {{ 'selected' if year == 2025 }}>{{ year }}</option>{% endfor %}
                </select>
            </div>
            <div class="form-group">
                <label>Round (optional)</label>
                <select id="round">
                    <option value="">All Rounds</option>
                </select>
            </div>
            <div class="form-group checkbox-group">
                <input type="checkbox" id="force">
                <label for="force">Force retry failed searches</label>
            </div>
            <div class="form-group checkbox-group">
                <input type="checkbox" id="dry-run">
                <label for="dry-run">Dry run (don't actually grab)</label>
            </div>
            
            <div class="button-row">
                <button onclick="runBackfill()" {{ 'disabled' if status.running }}>🚀 Run Backfill</button>
            </div>
            
            <hr style="border-color: #1a1a2e; margin: 20px 0;">
            
            <h3>Overlay Scaling</h3>
            <div class="inline-form">
                <label for="scale-factor">Scale Factor:</label>
                <input type="number" id="scale-factor" min="0.5" max="3.0" step="0.1" value="1.0">
                <span style="color: #666; font-size: 14px;">(0.5 - 3.0)</span>
            </div>
            <div class="button-row">
                <button onclick="runOverlay()" class="warning" {{ 'disabled' if status.running }}>🎨 Apply Overlays</button>
                <button onclick="runOverlay(true)" class="secondary" {{ 'disabled' if status.running }}>👁️ Overlay (Dry Run)</button>
            </div>
        </div>

        <!-- Output -->
        <div class="card">
            <h2>Output</h2>
            {% if status.timestamp %}<div class="timestamp">Last run: {{ status.timestamp }}</div>{% endif %}
            <div class="output" id="output">{{ status.output or 'No output yet. Run a command to see results.' }}</div>
        </div>
    </div>

    <!-- Library Tab -->
    <div id="library-tab" class="tab-content">
        <div class="card">
            <h2>Library Status</h2>
            
            <div class="library-filter">
                <label>Season:</label>
                <select id="library-season" onchange="loadLibraryStatus()">
                    {% for year in seasons %}<option value="{{ year }}" {{ 'selected' if year == 2025 }}>{{ year }}</option>{% endfor %}
                </select>
                <label>Round:</label>
                <select id="library-round" onchange="loadLibraryStatus()">
                    <option value="">All Rounds</option>
                </select>
                <button onclick="loadLibraryStatus()" class="secondary small">🔄 Refresh</button>
            </div>
            
            <div id="library-content">
                <p style="color: #666;">Select a season and click Refresh to view library status.</p>
            </div>
        </div>
    </div>

    <!-- Settings Tab -->
    <div id="settings-tab" class="tab-content">
        <div class="card">
            <h2>Grabbing Settings</h2>
            <div class="save-notice" id="save-notice">✅ Settings saved successfully!</div>
            <div class="form-group inline-form">
                <label for="max-grabs">Max grabs per run:</label>
                <input type="number" id="max-grabs" min="1" max="50" value="{{ settings.max_grabs_per_run }}">
                <span style="color: #666; font-size: 14px;">Limits how many sessions are grabbed in one scheduled run</span>
            </div>
        </div>

        <div class="card">
            <h2>Desired Sessions</h2>
            <p style="color: #666; margin-bottom: 20px;">Select which session types to search for and download.</p>
            <div class="sessions-grid">
                {% for group_name, session_keys in session_groups.items() %}
                <div class="session-group">
                    <h3>{{ group_name }}</h3>
                    {% for key in session_keys %}
                    {% if key in session_types %}
                    <div class="checkbox-group">
                        <input type="checkbox" id="session-{{ key }}" data-session="{{ key }}"
                               {{ 'checked' if settings.sessions.get(key, session_types[key].default) }}>
                        <label for="session-{{ key }}">{{ session_types[key].name }}</label>
                    </div>
                    {% endif %}
                    {% endfor %}
                </div>
                {% endfor %}
            </div>
            <div style="margin-top: 20px; display: flex; gap: 10px; flex-wrap: wrap;">
                <button onclick="saveSettings()" class="success">💾 Save Settings</button>
                <button onclick="selectAll(true)" class="secondary">Select All</button>
                <button onclick="selectAll(false)" class="secondary">Deselect All</button>
                <button onclick="resetDefaults()" class="secondary">Reset Defaults</button>
            </div>
        </div>
    </div>

    <script>
        const calendarData = {{ calendar_data | tojson }};
        const isRunning = {{ 'true' if status.running else 'false' }};
        const sessionDefaults = {{ session_defaults | tojson }};
        
        function scrollOutputToBottom() {
            const output = document.getElementById('output');
            if (output) output.scrollTop = output.scrollHeight;
        }
        
        window.onload = function() {
            updateRounds();
            updateLibraryRounds();
            scrollOutputToBottom();
        };
        
        function showTab(tabName) {
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.getElementById(tabName + '-tab').classList.add('active');
            event.target.classList.add('active');
            
            if (tabName === 'library') {
                loadLibraryStatus();
            }
        }
        
        function updateRounds() {
            const season = document.getElementById('season').value;
            const roundSelect = document.getElementById('round');
            const rounds = calendarData[season] || [];
            
            roundSelect.innerHTML = '<option value="">All Rounds</option>';
            rounds.forEach(r => {
                const sprint = r.sprint_weekend ? ' 🏃' : '';
                roundSelect.innerHTML += `<option value="${r.number}">Round ${r.number} - ${r.venue}${sprint}</option>`;
            });
        }
        
        function updateLibraryRounds() {
            const season = document.getElementById('library-season').value;
            const roundSelect = document.getElementById('library-round');
            const rounds = calendarData[season] || [];
            
            roundSelect.innerHTML = '<option value="">All Rounds</option>';
            rounds.forEach(r => {
                const sprint = r.sprint_weekend ? ' 🏃' : '';
                roundSelect.innerHTML += `<option value="${r.number}">Round ${r.number} - ${r.venue}${sprint}</option>`;
            });
        }
        
        function runCommand(cmd) {
            fetch('/run', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({command: cmd})
            }).then(() => setTimeout(() => location.reload(), 500));
        }
        
        function runBackfill() {
            const season = document.getElementById('season').value;
            const round = document.getElementById('round').value;
            const force = document.getElementById('force').checked;
            const dryRun = document.getElementById('dry-run').checked;
            
            let cmd = `backfill --season ${season}`;
            if (round) cmd += ` --round ${round}`;
            if (force) cmd += ' --force';
            if (dryRun) cmd += ' --dry-run';
            
            runCommand(cmd);
        }
        
        function runOverlay(dryRun = false) {
            const season = document.getElementById('season').value;
            const round = document.getElementById('round').value;
            let scale = parseFloat(document.getElementById('scale-factor').value);
            
            // Validate scale
            if (isNaN(scale) || scale < 0.5) scale = 0.5;
            if (scale > 3.0) scale = 3.0;
            document.getElementById('scale-factor').value = scale;
            
            let cmd;
            if (round) {
                cmd = `overlay apply-round ${season} ${round} --scale ${scale}`;
            } else {
                cmd = `overlay apply-season ${season} --scale ${scale}`;
            }
            if (dryRun) cmd += ' --dry-run';
            
            runCommand(cmd);
        }
        
        function refreshOutput() { location.reload(); }
        
        function loadLibraryStatus() {
            const season = document.getElementById('library-season').value;
            const round = document.getElementById('library-round').value;
            updateLibraryRounds();
            
            fetch(`/library-status?season=${season}&round=${round}`)
                .then(r => r.json())
                .then(data => {
                    renderLibraryStatus(data);
                });
        }
        
        function renderLibraryStatus(data) {
            const container = document.getElementById('library-content');
            
            if (!data.rounds || data.rounds.length === 0) {
                container.innerHTML = '<p style="color: #666;">No past rounds found for this season.</p>';
                return;
            }
            
            const pct = data.total_desired > 0 ? Math.round(data.total_grabbed / data.total_desired * 100) : 0;
            
            let html = `
                <div class="library-summary">
                    <div class="library-summary-number">${data.total_grabbed} / ${data.total_desired}</div>
                    <div class="library-summary-label">Sessions in Library (${pct}%)</div>
                    <div class="progress-bar">
                        <div class="progress-bar-fill" style="width: ${pct}%"></div>
                    </div>
                </div>
            `;
            
            data.rounds.forEach(round => {
                const roundPct = round.desired_count > 0 ? Math.round(round.grabbed_count / round.desired_count * 100) : 0;
                const sprint = round.sprint_weekend ? ' <span class="sprint-badge">SPRINT</span>' : '';
                const hasMissing = round.grabbed_count < round.desired_count;
                
                html += `
                    <div class="library-round">
                        <div class="library-round-header">
                            <div>
                                <span class="library-round-title">Round ${round.number} - ${round.venue}${sprint}</span>
                                <span class="library-round-stats">${round.date}</span>
                            </div>
                            <div style="display: flex; align-items: center; gap: 10px;">
                                <span class="library-round-stats">${round.grabbed_count}/${round.desired_count} (${roundPct}%)</span>
                                ${hasMissing ? `<button class="small warning" ${isRunning ? "disabled" : ""} onclick="grabMissing(${round.number})">Grab Missing</button>` : ''}
                            </div>
                        </div>
                        <div class="library-sessions">
                `;
                
                round.sessions.forEach(session => {
                    const cls = session.exists ? 'exists' : 'missing';
                    const icon = session.exists ? '✅' : '❌';
                    html += `<div class="library-session ${cls}">${icon} ${session.name}</div>`;
                });
                
                html += '</div></div>';
            });
            
            container.innerHTML = html;
        }
        
        function grabMissing(roundNum) {
            const season = document.getElementById('library-season').value;
            runCommand(`backfill --season ${season} --round ${roundNum} --force`);
        }
        
        function saveSettings() {
            const sessions = {};
            document.querySelectorAll('[data-session]').forEach(cb => {
                sessions[cb.dataset.session] = cb.checked;
            });
            
            const settings = {
                max_grabs_per_run: parseInt(document.getElementById('max-grabs').value) || 3,
                sessions: sessions
            };
            
            fetch('/settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(settings)
            }).then(r => r.json()).then(data => {
                const notice = document.getElementById('save-notice');
                notice.classList.add('show');
                setTimeout(() => notice.classList.remove('show'), 3000);
            });
        }
        
        function selectAll(checked) {
            document.querySelectorAll('[data-session]').forEach(cb => cb.checked = checked);
        }
        
        function resetDefaults() {
            document.querySelectorAll('[data-session]').forEach(cb => {
                cb.checked = sessionDefaults[cb.dataset.session] || false;
            });
            document.getElementById('max-grabs').value = 3;
        }
        
        {% if status.running %}
        setTimeout(() => location.reload(), 3000);
        {% endif %}
    </script>
</body>
</html>
"""


def run_grabber_command(args: str):
    """Run a grabber command in the background."""
    global command_output
    
    command_output["running"] = True
    command_output["command"] = f"f1-grab {args}"
    command_output["output"] = "Starting...\n"
    command_output["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        cmd = [PYTHON_PATH, str(GRABBER_SCRIPT)] + args.split()
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        output_lines = []
        for line in process.stdout:
            output_lines.append(line)
            command_output["output"] = "".join(output_lines)
        
        process.wait()
        command_output["output"] = "".join(output_lines)
        
    except Exception as e:
        command_output["output"] += f"\nError: {e}"
    finally:
        command_output["running"] = False


def run_overlay_command(args: str):
    """Run an overlay command in the background."""
    global command_output
    
    command_output["running"] = True
    command_output["command"] = f"f1-overlay {args}"
    command_output["output"] = "Starting overlay...\n"
    command_output["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        parts = args.split()
        
        # Parse command: apply-season 2025 --scale 2.0
        # or: apply-round 2025 12 --scale 2.0
        if parts[0] == 'apply-season':
            year = parts[1]
            scale = "2.0"
            dry_run = False
            
            for i, p in enumerate(parts):
                if p == '--scale' and i + 1 < len(parts):
                    scale = parts[i + 1]
                if p == '--dry-run':
                    dry_run = True
            
            cmd = [PYTHON_PATH, str(OVERLAY_SCRIPT), "apply-season", year, "--scale", scale]
            if dry_run:
                cmd.append("--dry-run")
        
        elif parts[0] == 'apply-round':
            year = parts[1]
            round_num = parts[2]
            scale = "2.0"
            dry_run = False
            
            for i, p in enumerate(parts):
                if p == '--scale' and i + 1 < len(parts):
                    scale = parts[i + 1]
                if p == '--dry-run':
                    dry_run = True
            
            # Build round folder path
            calendar = load_calendar_data(int(year))
            round_info = None
            for r in calendar.get("rounds", []):
                if r.get("round") == int(round_num):
                    round_info = r
                    break
            
            if round_info:
                venue = round_info.get("venue", "Unknown")
                round_path = os.path.join(F1_LIBRARY_PATH, year, f"Round {round_num} {venue}")
                cmd = [PYTHON_PATH, str(OVERLAY_SCRIPT), "apply-round", round_path, "--scale", scale]
                if dry_run:
                    cmd.append("--dry-run")
            else:
                command_output["output"] = f"Error: Round {round_num} not found in calendar"
                command_output["running"] = False
                return
        else:
            command_output["output"] = f"Error: Unknown overlay command: {parts[0]}"
            command_output["running"] = False
            return
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        output_lines = []
        for line in process.stdout:
            output_lines.append(line)
            command_output["output"] = "".join(output_lines)
        
        process.wait()
        command_output["output"] = "".join(output_lines)
        
    except Exception as e:
        command_output["output"] += f"\nError: {e}"
    finally:
        command_output["running"] = False


@app.route("/")
def index():
    settings = load_settings()
    session_defaults = {k: v["default"] for k, v in SESSION_TYPES.items()}
    next_race = get_next_race_info()
    
    calendar_data = {}
    for year in SEASONS:
        calendar_data[str(year)] = get_rounds_for_season(year)
    
    return render_template_string(
        HTML_TEMPLATE,
        status=command_output,
        seasons=SEASONS,
        settings=settings,
        session_types=SESSION_TYPES,
        session_groups=SESSION_GROUPS,
        session_defaults=session_defaults,
        next_race=next_race,
        calendar_data=calendar_data
    )


@app.route("/run", methods=["POST"])
def run():
    if command_output["running"]:
        return jsonify({"error": "Command already running"}), 400
    
    data = request.json
    cmd = data.get("command", "status")
    
    # Check if it's an overlay command
    if cmd.startswith("overlay "):
        overlay_cmd = cmd[8:]  # Remove "overlay " prefix
        thread = threading.Thread(target=run_overlay_command, args=(overlay_cmd,))
    else:
        thread = threading.Thread(target=run_grabber_command, args=(cmd,))
    
    thread.start()
    return jsonify({"status": "started", "command": cmd})


@app.route("/settings", methods=["GET", "POST"])
def settings_route():
    if request.method == "POST":
        new_settings = request.json
        save_settings(new_settings)
        update_grabber_config(new_settings)
        return jsonify({"status": "saved"})
    else:
        return jsonify(load_settings())


@app.route("/status")
def status():
    return jsonify(command_output)


@app.route("/library-status")
def library_status():
    season = request.args.get("season", "2025", type=int)
    round_num = request.args.get("round", "", type=str)
    round_filter = int(round_num) if round_num else None
    
    data = get_library_status(season, round_filter)
    
    # Convert SessionType to serializable format
    for round_data in data["rounds"]:
        for session in round_data["sessions"]:
            session["type"] = session["type"].name
    
    return jsonify(data)


if __name__ == "__main__":
    print("=" * 50)
    print("  F1 Grabber Web UI V2")
    print("  Open http://your-server-ip:5678")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5678, debug=False)
