#!/usr/bin/env python3
"""
F1 Grab Stats - Statistics Generator for F1arr
===============================================

Parses grabber_state.json and generates statistics for the WebUI stats page.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from collections import defaultdict

# Add script directory to path
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from f1_common import (
    get_logger,
    get_config_dir,
    SessionType,
)

# Module logger
logger = get_logger("stats")

# =============================================================================
# CONFIGURATION
# =============================================================================

CONFIG_DIR = get_config_dir()
STATE_FILE = CONFIG_DIR / "grabber_state.json"

# Session type categories
CORE_SESSION_TYPES = {
    SessionType.PRACTICE_1,
    SessionType.PRACTICE_2,
    SessionType.PRACTICE_3,
    SessionType.SPRINT_QUALIFYING,
    SessionType.SPRINT,
    SessionType.QUALIFYING,
    SessionType.RACE,
}

SUPPORT_SESSION_TYPES = {
    SessionType.WEEKEND_WARMUP,
    SessionType.TEAM_PRINCIPALS,
    SessionType.DRIVER_PRESS_CONFERENCE,
    SessionType.PADDOCK_UNCUT,
    SessionType.PADDOCK_SHOW,
    SessionType.F1_SHOW,
    SessionType.POST_SPRINT_SHOW,
    SessionType.TEDS_SPRINT_NOTEBOOK,
    SessionType.PRE_QUALIFYING_SHOW,
    SessionType.POST_QUALIFYING_SHOW,
    SessionType.TEDS_QUALIFYING_NOTEBOOK,
    SessionType.PRE_RACE_SHOW,
    SessionType.RACE_UNCUT,
    SessionType.POST_RACE_SHOW,
    SessionType.TEDS_RACE_NOTEBOOK,
    SessionType.TEDS_NOTEBOOK,
    SessionType.JOLYON_PALMERS_ANALYSIS,
    SessionType.FULL_EVENT,
}

# =============================================================================
# STATE LOADING
# =============================================================================

def load_state() -> dict:
    """Load grabber state from JSON file."""
    if not STATE_FILE.exists():
        logger.warning(f"State file not found: {STATE_FILE}")
        return {}
    
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading state file: {e}")
        return {}


def parse_session_key(key: str) -> Optional[Dict[str, Any]]:
    """
    Parse a session key like '2025_R15_E19' into components.
    
    Returns:
        Dict with year, round, session_type or None if invalid
    """
    try:
        parts = key.split('_')
        if len(parts) != 3:
            return None
        
        year = int(parts[0])
        round_num = int(parts[1][1:])  # Remove 'R' prefix
        session_num = int(parts[2][1:])  # Remove 'E' prefix
        
        try:
            session_type = SessionType(session_num)
        except ValueError:
            session_type = SessionType.UNKNOWN
        
        return {
            "year": year,
            "round": round_num,
            "session_type": session_type,
            "session_num": session_num,
        }
    except Exception:
        return None


# =============================================================================
# STATISTICS CALCULATION
# =============================================================================

def calculate_stats() -> dict:
    """
    Calculate all statistics from grabber state.
    
    Returns:
        Dict containing all stats for the template
    """
    state = load_state()
    
    grabbed = state.get("grabbed", {})
    failed = state.get("failed", {})
    searched = state.get("searched", {})
    
    now = datetime.now()
    today = now.date()
    
    # Initialize stats
    stats = {
        "total_grabbed": len(grabbed),
        "total_failed": len(failed),
        "total_searched": len(searched),
        "grabs_today": 0,
        "grabs_this_week": 0,
        "grabs_this_month": 0,
        "last_run": state.get("last_run"),
        "last_scheduled": state.get("last_scheduled_check"),
        "success_rate": 0,
        "by_day": [],
        "by_month": [],
        "by_session_type": {
            "core": 0,
            "support": 0,
        },
        "by_year": defaultdict(int),
        "recent_grabs": [],
    }
    
    # Calculate success rate
    total_attempts = len(grabbed) + len(failed)
    if total_attempts > 0:
        stats["success_rate"] = round((len(grabbed) / total_attempts) * 100)
    
    # Process grabbed items
    grabs_by_date = defaultdict(int)
    grabs_by_month = defaultdict(int)
    
    for key, info in grabbed.items():
        timestamp_str = info.get("timestamp", "")
        
        try:
            timestamp = datetime.fromisoformat(timestamp_str)
            grab_date = timestamp.date()
            
            # Count by time period
            if grab_date == today:
                stats["grabs_today"] += 1
            
            week_ago = today - timedelta(days=7)
            if grab_date >= week_ago:
                stats["grabs_this_week"] += 1
            
            month_ago = today - timedelta(days=30)
            if grab_date >= month_ago:
                stats["grabs_this_month"] += 1
            
            # Track by date for charts
            grabs_by_date[grab_date] += 1
            
            # Track by month
            month_key = timestamp.strftime("%Y-%m")
            grabs_by_month[month_key] += 1
            
        except (ValueError, TypeError):
            pass
        
        # Parse session key for categorization
        parsed = parse_session_key(key)
        if parsed:
            # By year
            stats["by_year"][parsed["year"]] += 1
            
            # By session type category
            session_type = parsed["session_type"]
            if session_type in CORE_SESSION_TYPES:
                stats["by_session_type"]["core"] += 1
            elif session_type in SUPPORT_SESSION_TYPES:
                stats["by_session_type"]["support"] += 1
    
    # Build daily chart (last 14 days)
    stats["by_day"] = []
    for i in range(13, -1, -1):
        day = today - timedelta(days=i)
        count = grabs_by_date.get(day, 0)
        label = day.strftime("%d")  # Just day number
        if i == 0:
            label = "Today"
        elif i == 1:
            label = "Yest"
        stats["by_day"].append({
            "label": label,
            "count": count,
            "date": day.isoformat(),
        })
    
    # Build monthly chart (last 6 months)
    stats["by_month"] = []
    for i in range(5, -1, -1):
        month_date = today - timedelta(days=i * 30)
        month_key = month_date.strftime("%Y-%m")
        count = grabs_by_month.get(month_key, 0)
        label = month_date.strftime("%b")  # Short month name
        stats["by_month"].append({
            "label": label,
            "count": count,
            "month": month_key,
        })
    
    # Convert by_year to regular dict for template
    stats["by_year"] = dict(stats["by_year"])
    
    # Recent grabs (last 15)
    recent = []
    for key, info in grabbed.items():
        parsed = parse_session_key(key)
        if not parsed:
            continue
        
        timestamp_str = info.get("timestamp", "")
        try:
            timestamp = datetime.fromisoformat(timestamp_str)
            time_display = timestamp.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            time_display = "Unknown"
            timestamp = datetime.min
        
        session_type = parsed["session_type"]
        
        recent.append({
            "key": key,
            "session": session_type.display_name,
            "round": parsed["round"],
            "year": parsed["year"],
            "release": info.get("release", "Unknown"),
            "time": time_display,
            "timestamp": timestamp,
        })
    
    # Sort by timestamp descending and take last 15
    recent.sort(key=lambda x: x["timestamp"], reverse=True)
    stats["recent_grabs"] = recent[:15]
    
    # Remove timestamp from output (not needed in template)
    for grab in stats["recent_grabs"]:
        del grab["timestamp"]
    
    return stats


def get_max_values(stats: dict) -> tuple:
    """Get max values for chart scaling."""
    max_daily = max((d["count"] for d in stats["by_day"]), default=1)
    max_monthly = max((m["count"] for m in stats["by_month"]), default=1)
    
    # Ensure minimum of 1 to avoid division by zero
    return max(max_daily, 1), max(max_monthly, 1)


# =============================================================================
# PUBLIC API
# =============================================================================

def get_stats() -> tuple:
    """
    Get stats and max values for template rendering.
    
    Returns:
        Tuple of (stats_dict, max_daily, max_monthly)
    """
    stats = calculate_stats()
    max_daily, max_monthly = get_max_values(stats)
    return stats, max_daily, max_monthly


# =============================================================================
# CLI
# =============================================================================

def main():
    """CLI for testing stats generation."""
    import argparse
    
    parser = argparse.ArgumentParser(description="F1 Grab Stats")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()
    
    stats, max_daily, max_monthly = get_stats()
    
    if args.json:
        print(json.dumps(stats, indent=2, default=str))
    else:
        print("=" * 60)
        print("F1 GRAB STATISTICS")
        print("=" * 60)
        print(f"\nTotal Grabbed:    {stats['total_grabbed']}")
        print(f"Today:            {stats['grabs_today']}")
        print(f"This Week:        {stats['grabs_this_week']}")
        print(f"This Month:       {stats['grabs_this_month']}")
        print(f"Success Rate:     {stats['success_rate']}%")
        print(f"Failed:           {stats['total_failed']}")
        
        print(f"\nBy Session Type:")
        print(f"  Core Sessions:    {stats['by_session_type']['core']}")
        print(f"  Support Content:  {stats['by_session_type']['support']}")
        
        if stats['by_year']:
            print(f"\nBy Season:")
            for year, count in sorted(stats['by_year'].items(), reverse=True):
                print(f"  {year}: {count}")
        
        print(f"\nLast Run:         {stats['last_run'] or 'Never'}")
        print(f"Last Scheduled:   {stats['last_scheduled'] or 'Never'}")
        
        if stats['recent_grabs']:
            print(f"\nRecent Grabs:")
            for grab in stats['recent_grabs'][:5]:
                print(f"  R{grab['round']:02d} {grab['session']}: {grab['release'][:50]}...")
        
        print("=" * 60)


if __name__ == "__main__":
    main()