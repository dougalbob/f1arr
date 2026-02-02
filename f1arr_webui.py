#!/usr/bin/env python3
"""
F1arr Web UI
============
Web interface for F1 grabber - refactored with templates.
"""

import os
import sys
import subprocess
import threading
import json
import re
from pathlib import Path
from datetime import datetime, timedelta
from f1_grab_stats import get_stats
from flask import (
    Flask, render_template, request, jsonify, 
    send_from_directory, Response
)

# Add paths for imports
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from f1_common import (
    SessionType,
    parse_prefix,
    load_calendar,
    F1_LIBRARY_PATH,
    get_config_dir,
)

from utils import config

# =============================================================================
# CONFIGURATION
# =============================================================================

PYTHON_PATH = "/opt/venv/bin/python3"
GRABBER_SCRIPT = SCRIPT_DIR / "f1_grabber.py"
OVERLAY_SCRIPT = SCRIPT_DIR / "f1_overlay_utility.py"
CONFIG_DIR = get_config_dir()
WEBUI_CONFIG_FILE = CONFIG_DIR / "webui_settings.json"
LOG_FILE = CONFIG_DIR / "logs" / "f1.log"

SEASONS = [2024, 2025, 2026]
CURRENT_YEAR = datetime.now().year

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

app = Flask(__name__)

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

# Store command output
command_output = {
    "running": False,
    "command": "",
    "output": "",
    "timestamp": None
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

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
    return [
        {
            "number": r.get("round"),
            "venue": r.get("venue"),
            "date": r.get("date"),
            "sprint_weekend": r.get("sprint_weekend", False)
        }
        for r in calendar.get("rounds", [])
    ]


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
                weekend_start = (
                    datetime.strptime(weekend_start_str, "%Y-%m-%d").date()
                    if weekend_start_str
                    else race_date - timedelta(days=2)
                )
                weekend_end = race_date + timedelta(days=1)
                
                formatted_date = race_date.strftime("%d-%m-%Y")
                
                if weekend_start <= today <= weekend_end:
                    return {
                        "round": r.get("round"),
                        "venue": r.get("venue"),
                        "date": formatted_date,
                        "flag": r.get("flag", ""),
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
                        "date": formatted_date,
                        "flag": r.get("flag", ""),
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
        "flag": "",
        "sprint_weekend": False,
        "status": "none",
        "countdown": "Season ended",
        "days": None
    }


def load_webui_settings():
    """Load WebUI settings from config file."""
    defaults = {
        "max_grabs_per_run": 3,
        "sessions": {k: v["default"] for k, v in SESSION_TYPES.items()}
    }
    
    if WEBUI_CONFIG_FILE.exists():
        try:
            with open(WEBUI_CONFIG_FILE, 'r') as f:
                saved = json.load(f)
                defaults.update(saved)
        except Exception:
            pass
    
    return defaults


def save_webui_settings(settings):
    """Save WebUI settings to config file."""
    WEBUI_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(WEBUI_CONFIG_FILE, 'w') as f:
        json.dump(settings, f, indent=2)


def update_grabber_config(settings):
    """Update the f1_grabber.py with new settings."""
    # This could update settings.yml instead of the grabber script
    try:
        config.reload_settings()
        return True
    except Exception as e:
        print(f"Error updating config: {e}")
        return False


# =============================================================================
# LIBRARY SCANNING
# =============================================================================

def scan_library_for_season(year: int) -> dict:
    """Scan the library to find existing sessions for a season."""
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
    """Get library status for display."""
    calendar = load_calendar_data(year)
    existing = scan_library_for_season(year)
    settings = load_webui_settings()
    
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
        for key, info in SESSION_TYPES.items():
            if info["default"]:
                try:
                    desired_set.add(SessionType[key])
                except KeyError:
                    pass
    
    result = {
        "rounds": [], 
        "total_grabbed": 0, 
        "total_desired": 0,
        "past_rounds_grabbed": 0,
        "past_rounds_desired": 0,
        "total_in_library": 0
    }
    today = datetime.now().date()
    
    # Count total files in library (all sessions, not just desired)
    for round_num, sessions in existing.items():
        result["total_in_library"] += len(sessions)
    
    for r in calendar.get("rounds", []):
        round_num = r.get("round")
        
        if round_filter and round_num != round_filter:
            continue
        
        # Check if future round
        race_date_str = r.get("date", "")
        is_future = False
        if race_date_str:
            try:
                is_future = datetime.strptime(race_date_str, "%Y-%m-%d").date() > today
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
            "is_future": is_future,
            "sessions": sessions,
            "grabbed_count": grabbed_count,
            "desired_count": desired_count,
        })
        
        result["total_grabbed"] += grabbed_count
        result["total_desired"] += desired_count
        
        # Only count past rounds for the "preferred" stats
        if not is_future:
            result["past_rounds_grabbed"] += grabbed_count
            result["past_rounds_desired"] += desired_count
    
    return result

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
        
        if parts[0] == 'apply-season':
            year = parts[1]
            scale = "1.0"
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
            scale = "1.0"
            dry_run = False
            
            for i, p in enumerate(parts):
                if p == '--scale' and i + 1 < len(parts):
                    scale = parts[i + 1]
                if p == '--dry-run':
                    dry_run = True
            
            calendar = load_calendar_data(int(year))
            round_info = next(
                (r for r in calendar.get("rounds", []) if r.get("round") == int(round_num)),
                None
            )
            
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
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
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


# =============================================================================
# FLASK ROUTES - PAGES
# =============================================================================

@app.route("/")
def index():
    settings = load_webui_settings()
    session_defaults = {k: v["default"] for k, v in SESSION_TYPES.items()}
    next_race = get_next_race_info()
    calendar_data = {str(year): get_rounds_for_season(year) for year in SEASONS}
    
    return render_template(
        "dashboard.html",
        status=command_output,
        seasons=SEASONS,
        current_year=CURRENT_YEAR,
        settings=settings,
        session_types=SESSION_TYPES,
        session_groups=SESSION_GROUPS,
        session_defaults=session_defaults,
        next_race=next_race,
        calendar_data=calendar_data,
        active_tab="home"
    )


@app.route("/library")
def library():
    settings = load_webui_settings()
    next_race = get_next_race_info()
    calendar_data = {str(year): get_rounds_for_season(year) for year in SEASONS}
    
    # Build flag data per season
    flag_data = {}
    for year in SEASONS:
        flag_data[str(year)] = {}
        cal = load_calendar_data(year)
        for r in cal.get("rounds", []):
            flag_data[str(year)][r.get("round")] = r.get("flag", "")
    
    return render_template(
        "library.html",
        status=command_output,
        seasons=SEASONS,
        current_year=CURRENT_YEAR,
        settings=settings,
        next_race=next_race,
        calendar_data=calendar_data,
        flag_data=flag_data,
        active_tab="download"
    )


@app.route("/settings-page")
def settings_page():
    settings = load_webui_settings()
    session_defaults = {k: v["default"] for k, v in SESSION_TYPES.items()}
    next_race = get_next_race_info()
    calendar_data = {str(year): get_rounds_for_season(year) for year in SEASONS}
    
    return render_template(
        "settings.html",
        status=command_output,
        seasons=SEASONS,
        current_year=CURRENT_YEAR,
        settings=settings,
        session_types=SESSION_TYPES,
        session_groups=SESSION_GROUPS,
        session_defaults=session_defaults,
        next_race=next_race,
        calendar_data=calendar_data,
        active_tab="settings"
    )


@app.route("/logs")
def logs_page():
    next_race = get_next_race_info()
    
    return render_template(
        "logs.html",
        status=command_output,
        next_race=next_race,
        active_tab="logs"
    )


@app.route("/activity")
def activity():
    next_race = get_next_race_info()
    
    return render_template(
        "activity.html",
        status=command_output,
        next_race=next_race,
        active_tab="activity"
    )


@app.route("/calendar")
def calendar():
    year = request.args.get("year", CURRENT_YEAR, type=int)
    next_race = get_next_race_info()
    
    # Get calendar data for all seasons
    calendar_data = {}
    for y in SEASONS:
        calendar_data[y] = get_rounds_for_season(y)
    
    # Get library status for the selected year
    library_status = {}
    lib_data = get_library_status(year)
    for r in lib_data.get("rounds", []):
        library_status[r["number"]] = {
            "grabbed": r["grabbed_count"],
            "total": r["desired_count"],
            "sessions": [{"name": s["name"], "exists": s["exists"], "type": s["type"].name if hasattr(s["type"], "name") else s["type"]} for s in r["sessions"]]
        }
    
    # Build flag data per season
    flag_data = {}
    for y in SEASONS:
        flag_data[y] = {}
        cal = load_calendar_data(y)
        for r in cal.get("rounds", []):
            flag_data[y][r.get("round")] = r.get("flag", "")
    
    return render_template(
        "calendar.html",
        status=command_output,
        next_race=next_race,
        year=year,
        calendar_data=calendar_data,
        library_status=library_status,
        flag_data=flag_data,
        active_tab="calendar"
    )

@app.route("/stats")
def stats():
    """Statistics page."""
    next_race = get_next_race_info()
    stats_data, max_daily, max_monthly = get_stats()
    
    return render_template(
        "stats.html",
        status=command_output,
        next_race=next_race,
        stats=stats_data,
        max_daily=max_daily,
        max_monthly=max_monthly,
        active_tab="stats"
    )
  
    # # Try to load stats from grab_stats module if available
    # try:
    #     from grab_stats import get_stats_summary
    #     stats_data = get_stats_summary()
    # except ImportError:
    #     pass
    
    # max_daily = max([d.get("count", 0) for d in stats_data.get("by_day", [])], default=1) or 1
    # max_monthly = max([m.get("count", 0) for m in stats_data.get("by_month", [])], default=1) or 1
    
    # return render_template(
    #     "stats.html",
    #     status=command_output,
    #     next_race=next_race,
    #     stats=stats_data,
    #     max_daily=max_daily,
    #     max_monthly=max_monthly,
    #     active_tab="stats"
    # )


# =============================================================================
# FLASK ROUTES - API
# =============================================================================

@app.route("/run", methods=["POST"])
def run():
    if command_output["running"]:
        return jsonify({"error": "Command already running"}), 400
    
    data = request.json
    cmd = data.get("command", "status")
    
    if cmd.startswith("overlay "):
        thread = threading.Thread(target=run_overlay_command, args=(cmd[8:],))
    else:
        thread = threading.Thread(target=run_grabber_command, args=(cmd,))
    
    thread.start()
    return jsonify({"status": "started", "command": cmd})


@app.route("/settings", methods=["GET", "POST"])
def settings_route():
    if request.method == "POST":
        new_settings = request.json
        save_webui_settings(new_settings)
        update_grabber_config(new_settings)
        return jsonify({"status": "saved"})
    return jsonify(load_webui_settings())


@app.route("/status")
def status():
    return jsonify(command_output)


@app.route("/library-status")
def library_status():
    season = request.args.get("season", str(CURRENT_YEAR), type=int)
    round_num = request.args.get("round", "", type=str)
    round_filter = int(round_num) if round_num else None
    
    data = get_library_status(season, round_filter)
    
    for round_data in data["rounds"]:
        for session in round_data["sessions"]:
            session["type"] = session["type"].name if hasattr(session["type"], "name") else str(session["type"])
    
    return jsonify(data)


@app.route("/app-config", methods=["GET", "POST"])
def app_config_route():
    """Get or save app configuration (settings.yml)."""
    if request.method == "POST":
        new_config = request.json
        
        settings = {
            "prowlarr": new_config.get("prowlarr", {}),
            "sabnzbd": new_config.get("sabnzbd", {}),
            "plex": new_config.get("plex", {}),
            "library": new_config.get("library", {}),
            "discord": new_config.get("discord", {}),
            "timezone": new_config.get("timezone", "Europe/London"),
        }
        
        success = config.save_settings(settings)
        config.reload_settings()
        validation = config.validate()
        
        return jsonify({"status": "saved" if success else "error", "validation": validation})
    
    config.reload_settings()
    settings = config._load_yaml()
    validation = config.validate()
    return jsonify({**settings, "validation": validation})


@app.route("/test-connections")
def test_connections():
    """Test connections to Prowlarr, SABnzbd, and Plex."""
    import requests as req
    
    results = {"prowlarr": False, "sabnzbd": False, "plex": False, "errors": []}
    
    try:
        url = config.get_prowlarr_url()
        key = config.get_prowlarr_api_key()
        if url and key:
            r = req.get(f"{url}/api/v1/health", headers={"X-Api-Key": key}, timeout=5)
            results["prowlarr"] = r.status_code == 200
    except Exception as e:
        results["errors"].append(f"Prowlarr: {str(e)}")
    
    try:
        url = config.get_sabnzbd_url()
        key = config.get_sabnzbd_api_key()
        if url and key:
            r = req.get(f"{url}/api?mode=version&apikey={key}&output=json", timeout=5)
            results["sabnzbd"] = r.status_code == 200
    except Exception as e:
        results["errors"].append(f"SABnzbd: {str(e)}")
    
    try:
        url = config.get_plex_url()
        token = config.get_plex_token()
        if url and token:
            r = req.get(f"{url}/?X-Plex-Token={token}", timeout=5)
            results["plex"] = r.status_code == 200
    except Exception as e:
        results["errors"].append(f"Plex: {str(e)}")
    
    return jsonify(results)


@app.route("/failed-sessions")
def failed_sessions():
    """Get recently failed session searches."""
    state_file = CONFIG_DIR / "grabber_state.json"
    failed = {}
    
    if state_file.exists():
        try:
            with open(state_file, 'r') as f:
                state = json.load(f)
                failed = state.get("failed", {})
        except Exception:
            pass
    
    return jsonify({"failed": failed})


@app.route("/download-selected", methods=["POST"])
def download_selected():
    """Download selected missing sessions."""
    data = request.json
    season = data.get("season")
    selections = data.get("selections", [])
    
    if not selections:
        return jsonify({"error": "No sessions selected"}), 400
    
    # Group by round for efficiency
    by_round = {}
    for sel in selections:
        round_num = sel.get("round")
        if round_num not in by_round:
            by_round[round_num] = []
        by_round[round_num].append(sel.get("session"))
    
    # Build commands
    commands = []
    for round_num, sessions in by_round.items():
        cmd = f"backfill --season {season} --round {round_num} --force"
        commands.append(cmd)
    
    # Run commands sequentially in background
    def run_all_commands(cmds):
        for cmd in cmds:
            run_grabber_command(cmd)
    
    if commands:
        thread = threading.Thread(target=run_all_commands, args=(commands,))
        thread.start()
    
    return jsonify({
        "status": "started",
        "rounds": list(by_round.keys()),
        "total_sessions": len(selections),
        "commands": commands
    })


@app.route("/calendar-data")
def calendar_data_api():
    """Get calendar and library data for a specific year."""
    year = request.args.get("year", CURRENT_YEAR, type=int)
    
    library_status = {}
    lib_data = get_library_status(year)
    for r in lib_data.get("rounds", []):
        library_status[r["number"]] = {
            "grabbed": r["grabbed_count"],
            "total": r["desired_count"],
            "sessions": [{"name": s["name"], "exists": s["exists"]} for s in r["sessions"]]
        }
    
    return jsonify({
        "year": year,
        "library_status": library_status
    })


# =============================================================================
# FLASK ROUTES - STREAMING
# =============================================================================

@app.route("/logs/stream")
def stream_logs():
    """Stream log file using Server-Sent Events."""
    def generate():
        try:
            if not LOG_FILE.exists():
                yield f"data: Log file not found: {LOG_FILE}\n\n"
                return
            
            with open(LOG_FILE, 'r') as f:
                lines = f.readlines()
                for line in lines[-100:]:
                    yield f"data: {line.rstrip()}\n\n"
            
            with open(LOG_FILE, 'r') as f:
                f.seek(0, 2)
                while True:
                    line = f.readline()
                    if line:
                        yield f"data: {line.rstrip()}\n\n"
                    else:
                        import time
                        time.sleep(0.5)
        except GeneratorExit:
            pass
        except Exception as e:
            yield f"data: Error reading log: {e}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')


@app.route("/logs/recent")
def recent_logs():
    """Get recent log lines."""
    lines = request.args.get("lines", 100, type=int)
    
    if not LOG_FILE.exists():
        return jsonify({"error": "Log file not found", "lines": []})
    
    try:
        with open(LOG_FILE, 'r') as f:
            all_lines = f.readlines()
            return jsonify({"lines": [l.rstrip() for l in all_lines[-lines:]]})
    except Exception as e:
        return jsonify({"error": str(e), "lines": []})


@app.route("/activity/stream")
def activity_stream():
    """Stream activity events parsed from log file."""
    
    def parse_log_line(line):
        """Parse a log line into an activity event."""
        match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\] ([\w.]+): (.+)', line)
        if not match:
            return None
        
        timestamp, level, source, message = match.groups()
        event = {'timestamp': timestamp, 'level': level, 'source': source, 'raw': message}
        
        # Parse specific event types
        if '🔍 Searching:' in message:
            m = re.search(r'Round (\d+) (\w+) - (.+)', message)
            if m:
                event['stage'] = 'searching'
                event['round'] = m.group(1)
                event['venue'] = m.group(2)
                event['name'] = f"{m.group(2)} {m.group(3)}"
                event['session_key'] = f"R{m.group(1)}_{m.group(3).replace(' ', '_')}"
        
        elif '📋 Found' in message:
            m = re.search(r'Found (\d+) candidates for Round (\d+) (\w+) - (.+)', message)
            if m:
                event['stage'] = 'found'
                event['candidates'] = int(m.group(1))
                event['round'] = m.group(2)
                event['venue'] = m.group(3)
                event['name'] = f"{m.group(3)} {m.group(4)}"
                event['session_key'] = f"R{m.group(2)}_{m.group(4).replace(' ', '_')}"
        
        elif '⬇️ Sending to SABnzbd:' in message:
            m = re.search(r'Sending to SABnzbd: (.+)', message)
            if m:
                event['stage'] = 'sending'
                event['release'] = m.group(1)
        
        elif '✅ Grabbed:' in message:
            m = re.search(r'Grabbed: (\S+) (.+)', message)
            if m:
                event['stage'] = 'grabbed'
                event['session_key'] = m.group(1)
                event['name'] = m.group(2)
        
        elif '📦 Release:' in message:
            m = re.search(r'Release: (.+)', message)
            if m:
                event['release'] = m.group(1)
        
        elif '📊 Score:' in message:
            m = re.search(r'Score: (\d+)', message)
            if m:
                event['score'] = int(m.group(1))
        
        elif '📂 New download ready:' in message:
            m = re.search(r'New download ready: (\S+) (.+)', message)
            if m:
                event['stage'] = 'ready'
                event['session_key'] = m.group(1)
                event['name'] = m.group(2)
        
        elif 'Processing:' in message and 'f1.sort' in source:
            m = re.search(r'Processing: (\S+) (.+)', message)
            if m:
                event['stage'] = 'sorting'
                event['session_key'] = m.group(1)
                event['name'] = m.group(2)
        
        elif 'Applying Plex tags' in message:
            event['stage'] = 'tagging'
        
        elif 'Triggering Plex refresh' in message:
            event['stage'] = 'plex'
        
        elif '✅ Successfully processed:' in message:
            m = re.search(r'Successfully processed: (\S+) (.+)', message)
            if m:
                event['stage'] = 'complete'
                event['session_key'] = m.group(1)
                event['name'] = m.group(2)
        
        elif '✅ Sort completed:' in message:
            m = re.search(r'Sort completed: (\S+) (.+)', message)
            if m:
                event['stage'] = 'complete'
                event['session_key'] = m.group(1)
                event['name'] = m.group(2)
        
        elif '❌' in message or 'Error' in message or 'error' in message.lower():
            event['stage'] = 'error'
            event['detail'] = message
        
        return event if event.get('stage') or event.get('release') or event.get('score') else None
    
    def generate():
        try:
            if not LOG_FILE.exists():
                yield "data: " + json.dumps({"error": "Log file not found"}) + "\n\n"
                return
            
            with open(LOG_FILE, 'r') as f:
                lines = f.readlines()[-50:]
                for line in lines:
                    event = parse_log_line(line.strip())
                    if event:
                        yield "data: " + json.dumps(event) + "\n\n"
            
            with open(LOG_FILE, 'r') as f:
                f.seek(0, 2)
                while True:
                    line = f.readline()
                    if line:
                        event = parse_log_line(line.strip())
                        if event:
                            yield "data: " + json.dumps(event) + "\n\n"
                    else:
                        import time
                        time.sleep(0.5)
        except GeneratorExit:
            pass
        except Exception as e:
            yield "data: " + json.dumps({"error": str(e)}) + "\n\n"
    
    return Response(generate(), mimetype='text/event-stream')


@app.route("/api/sabnzbd/queue")
def sabnzbd_queue():
    """Get SABnzbd queue status for f1 category."""
    import requests
    import yaml
    
    try:
        settings_file = CONFIG_DIR / "settings.yml"
        if not settings_file.exists():
            return jsonify({"error": "Settings file not found"}), 404
        
        with open(settings_file, 'r') as f:
            settings = yaml.safe_load(f)
        
        sab_config = settings.get('sabnzbd', {})
        sab_url = sab_config.get('url', '').rstrip('/')
        sab_api_key = sab_config.get('api_key', '')
        
        if not sab_url or not sab_api_key:
            return jsonify({"error": "SABnzbd not configured"}), 400
        
        api_url = f"{sab_url}/api"
        params = {
            'apikey': sab_api_key,
            'mode': 'queue',
            'output': 'json'
        }
        
        response = requests.get(api_url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        queue = data.get('queue', {})
        slots = queue.get('slots', [])
        
        # Filter for f1 category
        f1_downloads = []
        for slot in slots:
            if slot.get('cat', '').lower() == 'f1':
                f1_downloads.append({
                    'filename': slot.get('filename', 'Unknown'),
                    'size': slot.get('size', '0 MB'),
                    'sizeleft': slot.get('sizeleft', '0 MB'),
                    'percentage': slot.get('percentage', '0'),
                    'timeleft': slot.get('timeleft', 'Unknown'),
                    'status': slot.get('status', 'Unknown')
                })
        
        result = {
            'f1_downloads': f1_downloads,
            'f1_count': len(f1_downloads),
            'total_size': queue.get('size', '0 MB'),
            'total_sizeleft': queue.get('sizeleft', '0 MB'),
            'speed': queue.get('speed', '0 KB/s'),
            'timeleft': queue.get('timeleft', 'Unknown'),
            'paused': queue.get('paused', False),
            'total_slots': len(slots)
        }
        
        return jsonify(result)
    
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to connect to SABnzbd: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =============================================================================
# FLASK ROUTES - STATIC FILES
# =============================================================================

@app.route("/flags/<path:filename>")
def flag_files(filename):
    """Serve flag SVG files."""
    return send_from_directory(str(SCRIPT_DIR / "assets" / "flag_assets_svg"), filename)


@app.route("/manifest.json")
def manifest():
    return jsonify({
        "name": "F1arr",
        "short_name": "F1arr",
        "description": "F1 Automated Recording & Retrieval",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#000000",
        "theme_color": "#e10600",
        "orientation": "any",
        "icons": [
            {
                "src": "/assets/f1_icon.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable"
            }
        ]
    })


@app.route("/sw.js")
def service_worker():
    return send_from_directory(str(SCRIPT_DIR / "static" / "js"), "sw.js", mimetype='application/javascript')


@app.route("/assets/<path:filename>")
def static_assets(filename):
    return send_from_directory(str(SCRIPT_DIR / "assets"), filename)


# =============================================================================
# MAIN
# =============================================================================


@app.route("/verify-admin", methods=["POST"])
def verify_admin():
    """Verify admin password."""
    import yaml
    
    data = request.json
    password = data.get("password", "")
    
    try:
        settings_file = CONFIG_DIR / "settings.yml"
        if settings_file.exists():
            with open(settings_file, "r") as f:
                settings = yaml.safe_load(f)
                admin_password = settings.get("uiadminpassword", "")
                
                if admin_password and password == admin_password:
                    return jsonify({"valid": True})
    except Exception as e:
        print(f"Error verifying admin password: {e}")
    
    return jsonify({"valid": False})


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("=" * 50)
    print("  F1arr Web UI")
    print("  Open http://your-server-ip:5678")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5678, debug=False)
