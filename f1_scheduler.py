#!/usr/bin/env python3
"""
F1 Scheduler - Unified Session Monitor and Download Watcher
============================================================

Runs continuously inside the f1arr container with TWO responsibilities:

1. CALENDAR MONITORING: Watches for race weekends and triggers searches
2. DOWNLOAD WATCHING: Monitors downloads folder for new downloads
"""

import os
import sys
import time
import signal
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

# Import centralized logging FIRST
from f1_common import (
    setup_logging,
    get_logger,
    load_calendar,
    F1_LIBRARY_PATH,
    F1_DOWNLOADS_PATH,
    SESSION_TO_CALENDAR_KEY,
    CALENDAR_KEY_TO_DAY,
    get_config_dir,
    get_downloads_path,
)

from f1_watcher import DownloadWatcher, create_watcher

# =============================================================================
# CONFIGURATION
# =============================================================================

CONFIG = {
    "season": 2026,
    "race_weekend_interval_minutes": 30,
    "off_weekend_interval_minutes": 360,
    "search_delay_hours": 1,
    "post_race_check_days": 2,
    "watch_dir": get_downloads_path(),
    "watch_stable_seconds": 30,
    "watch_check_interval": 10,
    "quiet_hours": {
        "enabled": True,
        "start": 1,
        "end": 6,
        "interval_minutes": 120,
    },
    "grabber_script": SCRIPT_DIR / "f1_grabber.py",
    "sort_script": SCRIPT_DIR / "f1_sort.py",
    "config_dir": get_config_dir(),
    "python_path": sys.executable,
}

# =============================================================================
# MODULE LOGGER
# =============================================================================

# Module logger - will be configured by setup_logging() in main()
logger = get_logger("scheduler")

# =============================================================================
# SORT PROCESSOR
# =============================================================================

def process_download(folder_path: str) -> bool:
    import subprocess
    
    logger.info(f"🔄 Processing download: {os.path.basename(folder_path)}")
    
    try:
        result = subprocess.run(
            [CONFIG["python_path"], str(CONFIG["sort_script"]), "--source", folder_path],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode == 0:
            logger.info(f"✅ Sort completed successfully")
            for line in result.stdout.split('\n'):
                if any(x in line for x in ['✅', '❌', 'Moving', 'Error']):
                    logger.info(f"   {line.strip()}")
            return True
        else:
            logger.error(f"❌ Sort failed with code {result.returncode}")
            if result.stderr:
                logger.error(f"   {result.stderr[:500]}")
            return False
    
    except subprocess.TimeoutExpired:
        logger.error("❌ Sort timed out after 5 minutes")
        return False
    except Exception as e:
        logger.error(f"❌ Sort error: {e}")
        return False

# =============================================================================
# CALENDAR MONITORING
# =============================================================================

def has_session_times(calendar: dict) -> bool:
    if not calendar:
        return False
    rounds = calendar.get("rounds", [])
    if not rounds:
        return False
    return "sessions" in rounds[0]

def get_current_race_weekend(calendar: dict) -> Optional[dict]:
    now = datetime.now()
    today = now.date()
    
    for round_info in calendar.get("rounds", []):
        race_date_str = round_info.get("date")
        weekend_start_str = round_info.get("weekend_start")
        
        if not race_date_str:
            continue
        
        try:
            race_date = datetime.strptime(race_date_str, "%Y-%m-%d").date()
            
            if weekend_start_str:
                weekend_start = datetime.strptime(weekend_start_str, "%Y-%m-%d").date()
            else:
                weekend_start = race_date - timedelta(days=2)
            
            window_start = weekend_start - timedelta(days=1)
            window_end = race_date + timedelta(days=CONFIG["post_race_check_days"])
            
            if window_start <= today <= window_end:
                return {
                    "round": round_info.get("round"),
                    "venue": round_info.get("venue"),
                    "weekend_start": weekend_start,
                    "race_date": race_date,
                    "window_start": window_start,
                    "window_end": window_end,
                    "round_info": round_info,
                }
        except ValueError:
            continue
    
    return None

def get_next_race_weekend(calendar: dict) -> Optional[dict]:
    now = datetime.now()
    today = now.date()
    
    next_round = None
    next_date = None
    
    for round_info in calendar.get("rounds", []):
        race_date_str = round_info.get("date")
        weekend_start_str = round_info.get("weekend_start")
        
        if not race_date_str:
            continue
        
        try:
            race_date = datetime.strptime(race_date_str, "%Y-%m-%d").date()
            
            if weekend_start_str:
                weekend_start = datetime.strptime(weekend_start_str, "%Y-%m-%d").date()
            else:
                weekend_start = race_date - timedelta(days=2)
            
            window_start = weekend_start - timedelta(days=1)
            
            if window_start > today:
                if next_date is None or window_start < next_date:
                    next_date = window_start
                    next_round = {
                        "round": round_info.get("round"),
                        "venue": round_info.get("venue"),
                        "weekend_start": weekend_start,
                        "race_date": race_date,
                        "window_start": window_start,
                    }
        except ValueError:
            continue
    
    return next_round

def run_grabber_scheduled() -> dict:
    import subprocess
    
    logger.info("🚀 Running grabber in scheduled mode...")
    
    try:
        result = subprocess.run(
            [CONFIG["python_path"], str(CONFIG["grabber_script"]), "scheduled"],
            capture_output=True,
            text=True,
            timeout=1800
        )
        
        grabbed_count = 0
        
        for line in result.stdout.split('\n'):
            if 'Releases grabbed:' in line:
                try:
                    grabbed_count = int(line.split(':')[-1].strip())
                except ValueError:
                    pass
            elif any(x in line for x in ['✅ Grabbed', '❌ No matching']):
                logger.info(f"  {line.strip()}")
        
        if result.returncode == 0:
            logger.info(f"✅ Grabber completed: {grabbed_count} grabbed")
            return {"success": True, "grabbed": grabbed_count}
        else:
            logger.error(f"❌ Grabber failed with code {result.returncode}")
            return {"success": False, "grabbed": 0}
    
    except subprocess.TimeoutExpired:
        logger.error("❌ Grabber timed out")
        return {"success": False, "grabbed": 0}
    except Exception as e:
        logger.error(f"❌ Grabber error: {e}")
        return {"success": False, "grabbed": 0}

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def format_timedelta(td: timedelta) -> str:
    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        return "now"
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"

def is_quiet_hours() -> bool:
    if not CONFIG["quiet_hours"]["enabled"]:
        return False
    hour = datetime.now().hour
    start = CONFIG["quiet_hours"]["start"]
    end = CONFIG["quiet_hours"]["end"]
    if start < end:
        return start <= hour < end
    else:
        return hour >= start or hour < end

def get_sleep_interval(in_race_weekend: bool) -> int:
    if is_quiet_hours():
        return CONFIG["quiet_hours"]["interval_minutes"] * 60
    if in_race_weekend:
        return CONFIG["race_weekend_interval_minutes"] * 60
    else:
        return CONFIG["off_weekend_interval_minutes"] * 60

# =============================================================================
# SCHEDULER STATE
# =============================================================================

class SchedulerState:
    def __init__(self):
        self.running = True
        self.last_check: Optional[datetime] = None
        self.last_grabber_run: Optional[datetime] = None
        self.current_weekend: Optional[dict] = None
        self.total_grabbed = 0
    
    def reset_for_new_weekend(self, weekend: dict):
        if self.current_weekend and self.current_weekend.get("round") != weekend.get("round"):
            logger.info(f"🏎️ New race weekend: Round {weekend['round']} {weekend['venue']}")
        self.current_weekend = weekend

# =============================================================================
# MAIN LOOP
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='F1 Scheduler')
    parser.add_argument('--watcher-only', action='store_true', help='Run only the download watcher')
    parser.add_argument('--no-watcher', action='store_true', help='Disable download watcher')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()
    
    # Setup centralized logging
    setup_logging(debug_mode=args.debug)
    logger.info("F1 Scheduler initialized")
    
    state = SchedulerState()
    
    def signal_handler(signum, frame):
        logger.info("🛑 Shutdown signal received")
        state.running = False
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info("=" * 60)
    logger.info("  F1ARR SCHEDULER")
    logger.info("=" * 60)
    logger.info(f"  Season:              {CONFIG['season']}")
    logger.info(f"  Race weekend check:  Every {CONFIG['race_weekend_interval_minutes']} minutes")
    logger.info(f"  Off-weekend check:   Every {CONFIG['off_weekend_interval_minutes']} minutes")
    logger.info(f"  Search delay:        {CONFIG['search_delay_hours']} hours")
    logger.info(f"  Watch directory:     {CONFIG['watch_dir']}")
    logger.info(f"  Library path:        {F1_LIBRARY_PATH}")
    logger.info("=" * 60)
    
    # Initialize watcher
    watcher = None
    if not args.no_watcher:
        logger.info("📂 Initializing download watcher...")
        watcher = create_watcher(
            watch_dir=CONFIG["watch_dir"],
            processor=process_download,
            config_dir=str(CONFIG["config_dir"])
        )
        watcher.start()
        logger.info(f"👁️ Watching: {CONFIG['watch_dir']}")
    
    if args.watcher_only:
        logger.info("Running in watcher-only mode")
        try:
            while state.running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            if watcher:
                watcher.stop()
        return 0
    
    calendar = load_calendar(CONFIG["season"])
    if not calendar:
        logger.error(f"❌ Could not load calendar for {CONFIG['season']}")
        if watcher:
            watcher.stop()
        return 1
    
    if not has_session_times(calendar):
        logger.warning(f"⚠️ Calendar doesn't have detailed session times")
    
    logger.info(f"📅 Loaded calendar: {len(calendar.get('rounds', []))} rounds")
    
    current_weekend = get_current_race_weekend(calendar)
    if current_weekend:
        logger.info(f"🏎️ Currently in race weekend: Round {current_weekend['round']} {current_weekend['venue']}")
        state.reset_for_new_weekend(current_weekend)
    else:
        next_weekend = get_next_race_weekend(calendar)
        if next_weekend:
            until = datetime.combine(next_weekend['window_start'], datetime.min.time()) - datetime.now()
            logger.info(f"⏳ Next race weekend: Round {next_weekend['round']} {next_weekend['venue']}")
            logger.info(f"   Starts in {format_timedelta(until)}")
    
    while state.running:
        try:
            now = datetime.now()
            state.last_check = now
            
            calendar = load_calendar(CONFIG["season"]) or calendar
            
            current_weekend = get_current_race_weekend(calendar)
            in_race_weekend = current_weekend is not None
            
            if in_race_weekend:
                state.reset_for_new_weekend(current_weekend)
                if has_session_times(calendar):
                    result = run_grabber_scheduled()
                    state.last_grabber_run = datetime.now()
                    if result["grabbed"] > 0:
                        state.total_grabbed += result["grabbed"]
            else:
                next_weekend = get_next_race_weekend(calendar)
                if next_weekend:
                    until = datetime.combine(next_weekend['window_start'], datetime.min.time()) - now
                    if until.total_seconds() > 0:
                        logger.info(f"💤 Next: Round {next_weekend['round']} {next_weekend['venue']} in {format_timedelta(until)}")
            
            sleep_seconds = get_sleep_interval(in_race_weekend)
            
            for _ in range(sleep_seconds // 30):
                if not state.running:
                    break
                time.sleep(30)
            
            if state.running:
                time.sleep(sleep_seconds % 30)
        
        except Exception as e:
            logger.error(f"❌ Error in main loop: {e}", exc_info=True)
            time.sleep(60)
    
    if watcher:
        watcher.stop()
    
    logger.info("🛑 Scheduler stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())