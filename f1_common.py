#!/usr/bin/env python3
"""
F1 Common - Shared constants, patterns, and utilities
======================================================

Self-contained version for f1arr container.
Paths are configured via environment variables.
"""

import os
import re
import json
import argparse
import logging
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Set
from dataclasses import dataclass
from enum import IntEnum

# =============================================================================
# PATH CONFIGURATION (Environment-based for container)
# =============================================================================

def get_script_dir() -> Path:
    """Get the directory where scripts live."""
    return Path(__file__).parent.resolve()

def get_config_dir() -> Path:
    """Get the config directory path."""
    # Check environment variable first, then fall back to script dir
    env_config = os.environ.get("CONFIG_DIR")
    if env_config:
        return Path(env_config)
    return get_script_dir() / "config"

def get_library_path() -> str:
    """Get F1 library path from environment or default."""
    return os.environ.get("F1_LIBRARY_PATH", "/data/media/Motorsport/F1")

def get_downloads_path() -> str:
    """Get F1 downloads path from environment or default."""
    return os.environ.get("F1_DOWNLOADS_PATH", "/data/downloads/complete/f1")

# Export commonly used paths
SCRIPT_DIR = get_script_dir()
CONFIG_DIR = get_config_dir()
F1_LIBRARY_PATH = get_library_path()
F1_DOWNLOADS_PATH = get_downloads_path()

# =============================================================================
# SESSION DEFINITIONS (Single Source of Truth)
# =============================================================================

class SessionType(IntEnum):
    """F1 session types with their chronological order."""
    WEEKEND_WARMUP = 1
    TEAM_PRINCIPALS = 2
    DRIVER_PRESS_CONFERENCE = 3
    PADDOCK_UNCUT = 4
    PADDOCK_SHOW = 5
    F1_SHOW = 6
    PRACTICE_1 = 7
    PRACTICE_2 = 8
    PRACTICE_3 = 9
    SPRINT_QUALIFYING = 10
    SPRINT = 11
    POST_SPRINT_SHOW = 12
    TEDS_SPRINT_NOTEBOOK = 13
    PRE_QUALIFYING_SHOW = 14
    QUALIFYING = 15
    POST_QUALIFYING_SHOW = 16
    TEDS_QUALIFYING_NOTEBOOK = 17
    PRE_RACE_SHOW = 18
    RACE = 19
    RACE_UNCUT = 20
    POST_RACE_SHOW = 21
    TEDS_RACE_NOTEBOOK = 22
    TEDS_NOTEBOOK = 23
    JOLYON_PALMERS_ANALYSIS = 24
    FULL_EVENT = 25
    UNKNOWN = 99

    @property
    def display_name(self) -> str:
        return SESSION_DISPLAY_NAMES.get(self, "Unknown Session")
    
    @property
    def is_core_session(self) -> bool:
        return self in CORE_SESSIONS
    
    @property
    def is_sprint_session(self) -> bool:
        return self in SPRINT_SESSIONS


SESSION_DISPLAY_NAMES = {
    SessionType.WEEKEND_WARMUP: "Weekend Warm Up",
    SessionType.TEAM_PRINCIPALS: "Team Principals",
    SessionType.DRIVER_PRESS_CONFERENCE: "Driver Press Conference",
    SessionType.PADDOCK_UNCUT: "Paddock Uncut",
    SessionType.PADDOCK_SHOW: "The Paddock Show",
    SessionType.F1_SHOW: "The F1 Show",
    SessionType.PRACTICE_1: "Practice 1",
    SessionType.PRACTICE_2: "Practice 2",
    SessionType.PRACTICE_3: "Practice 3",
    SessionType.SPRINT_QUALIFYING: "Sprint Qualifying",
    SessionType.SPRINT: "Sprint",
    SessionType.POST_SPRINT_SHOW: "Post-Sprint Show",
    SessionType.TEDS_SPRINT_NOTEBOOK: "Ted's Sprint Notebook",
    SessionType.PRE_QUALIFYING_SHOW: "Pre-Qualifying Show",
    SessionType.QUALIFYING: "Qualifying",
    SessionType.POST_QUALIFYING_SHOW: "Post-Qualifying Show",
    SessionType.TEDS_QUALIFYING_NOTEBOOK: "Ted's Qualifying Notebook",
    SessionType.PRE_RACE_SHOW: "Pre-Race Show",
    SessionType.RACE: "Race",
    SessionType.RACE_UNCUT: "Race Uncut",
    SessionType.POST_RACE_SHOW: "Post-Race Show",
    SessionType.TEDS_RACE_NOTEBOOK: "Ted's Race Notebook",
    SessionType.TEDS_NOTEBOOK: "Ted's Notebook",
    SessionType.JOLYON_PALMERS_ANALYSIS: "Jolyon Palmer's Analysis",
    SessionType.FULL_EVENT: "Full Event",
    SessionType.UNKNOWN: "Unknown Session",
}

SESSION_BY_NAME = {v: k for k, v in SESSION_DISPLAY_NAMES.items()}

CORE_SESSIONS = {
    SessionType.PRACTICE_1,
    SessionType.PRACTICE_2,
    SessionType.PRACTICE_3,
    SessionType.SPRINT_QUALIFYING,
    SessionType.SPRINT,
    SessionType.QUALIFYING,
    SessionType.RACE,
}

SPRINT_SESSIONS = {
    SessionType.SPRINT_QUALIFYING,
    SessionType.SPRINT,
    SessionType.POST_SPRINT_SHOW,
    SessionType.TEDS_SPRINT_NOTEBOOK,
}

DEFAULT_DESIRED_SESSIONS = {
    SessionType.WEEKEND_WARMUP: True,
    SessionType.TEAM_PRINCIPALS: False,
    SessionType.DRIVER_PRESS_CONFERENCE: True,
    SessionType.PADDOCK_UNCUT: False,
    SessionType.PADDOCK_SHOW: True,
    SessionType.F1_SHOW: False,
    SessionType.PRACTICE_1: True,
    SessionType.PRACTICE_2: True,
    SessionType.PRACTICE_3: True,
    SessionType.SPRINT_QUALIFYING: True,
    SessionType.SPRINT: True,
    SessionType.POST_SPRINT_SHOW: False,
    SessionType.TEDS_SPRINT_NOTEBOOK: False,
    SessionType.PRE_QUALIFYING_SHOW: False,
    SessionType.QUALIFYING: True,
    SessionType.POST_QUALIFYING_SHOW: False,
    SessionType.TEDS_QUALIFYING_NOTEBOOK: False,
    SessionType.PRE_RACE_SHOW: True,
    SessionType.RACE: True,
    SessionType.RACE_UNCUT: False,
    SessionType.POST_RACE_SHOW: False,
    SessionType.TEDS_RACE_NOTEBOOK: False,
    SessionType.TEDS_NOTEBOOK: True,
    SessionType.JOLYON_PALMERS_ANALYSIS: True,
    SessionType.FULL_EVENT: False,
}

# =============================================================================
# SPRINT WEEKEND HELPERS
# =============================================================================

def is_sprint_weekend(round_info: dict) -> bool:
    return round_info.get("sprint_weekend", False)

def get_excluded_sessions_for_round(round_info: dict) -> Set[SessionType]:
    excluded: Set[SessionType] = set()
    if is_sprint_weekend(round_info):
        excluded.add(SessionType.PRACTICE_2)
        excluded.add(SessionType.PRACTICE_3)
    else:
        excluded.add(SessionType.SPRINT_QUALIFYING)
        excluded.add(SessionType.SPRINT)
    return excluded

def get_valid_sessions_for_round(
    round_info: dict,
    desired_sessions: Dict[SessionType, bool] = None
) -> List[SessionType]:
    if desired_sessions is None:
        desired_sessions = DEFAULT_DESIRED_SESSIONS
    excluded = get_excluded_sessions_for_round(round_info)
    return [
        session for session, wanted in desired_sessions.items()
        if wanted and session not in excluded
    ]

# =============================================================================
# SESSION PATTERN MATCHING
# =============================================================================

@dataclass
class SessionPattern:
    session_type: SessionType
    must_have: List[str]
    must_not_have: List[str]
    priority: int = 0

SESSION_PATTERNS: List[SessionPattern] = [
    SessionPattern(SessionType.FULL_EVENT, ["full event"], [], 115),
    SessionPattern(SessionType.TEDS_SPRINT_NOTEBOOK, ["teds sprint notebook", "teds sprint race notebook", "ted's sprint notebook", "ted's sprint race notebook"], [], 110),
    SessionPattern(SessionType.TEDS_QUALIFYING_NOTEBOOK, ["teds qualifying notebook", "ted's qualifying notebook"], ["sprint"], 109),
    SessionPattern(SessionType.TEDS_RACE_NOTEBOOK, ["teds race notebook", "ted's race notebook"], ["sprint"], 108),
    SessionPattern(SessionType.TEDS_NOTEBOOK, ["teds notebook", "ted's notebook"], ["qualifying", "race", "sprint"], 107),
    SessionPattern(SessionType.JOLYON_PALMERS_ANALYSIS, ["jolyon palmer", "palmer's analysis", "palmers analysis"], [], 105),
    SessionPattern(SessionType.POST_RACE_SHOW, ["post race show", "post-race show"], [], 100),
    SessionPattern(SessionType.PRE_RACE_SHOW, ["pre race show", "pre-race show"], [], 100),
    SessionPattern(SessionType.POST_QUALIFYING_SHOW, ["post qualifying show", "post-qualifying show"], [], 100),
    SessionPattern(SessionType.PRE_QUALIFYING_SHOW, ["pre qualifying show", "pre-qualifying show"], [], 100),
    SessionPattern(SessionType.POST_SPRINT_SHOW, ["post sprint show", "post-sprint show"], [], 100),
    SessionPattern(SessionType.PADDOCK_UNCUT, ["paddock uncut"], [], 95),
    SessionPattern(SessionType.PADDOCK_SHOW, ["paddock show"], ["uncut"], 94),
    SessionPattern(SessionType.F1_SHOW, ["f1 show", "the f1 show", "f1 show preview"], ["paddock"], 93),
    SessionPattern(SessionType.DRIVER_PRESS_CONFERENCE, ["drivers press conference", "driver press conference", "drivers press", "press conference"], ["team principal"], 90),
    SessionPattern(SessionType.TEAM_PRINCIPALS, ["team principals", "team principal"], [], 90),
    SessionPattern(SessionType.WEEKEND_WARMUP, ["weekend warm-up", "weekend warm up", "weekend warmup"], [], 85),
    SessionPattern(SessionType.SPRINT_QUALIFYING, ["sprint qualifying", "sprint shootout"], ["post", "pre", "notebook"], 80),
    SessionPattern(SessionType.SPRINT, ["sprint race", "sprint"], ["qualifying", "shootout", "pre-sprint", "post-sprint", "pre sprint", "post sprint", "notebook"], 70),
    SessionPattern(SessionType.QUALIFYING, ["qualifying", "quali"], ["sprint", "pre-qualifying", "pre qualifying", "post-qualifying", "post qualifying", "notebook"], 60),
    SessionPattern(SessionType.PRACTICE_1, ["fp1", "practice 1", "practice one", "free practice 1"], ["fp2", "fp3", "practice 2", "practice 3", "practice two", "practice three"], 55),
    SessionPattern(SessionType.PRACTICE_2, ["fp2", "practice 2", "practice two", "free practice 2"], ["fp1", "fp3", "practice 1", "practice 3", "practice one", "practice three"], 55),
    SessionPattern(SessionType.PRACTICE_3, ["fp3", "practice 3", "practice three", "free practice 3"], ["fp1", "fp2", "practice 1", "practice 2", "practice one", "practice two"], 55),
    SessionPattern(SessionType.RACE_UNCUT, ["uncut"], ["paddock"], 45),
    SessionPattern(SessionType.RACE, ["race"], ["sprint", "pre-race", "post-race", "pre race", "post race", "notebook", "uncut"], 40),
]

def classify_session(text: str) -> SessionType:
    normalized = text.lower()
    normalized = re.sub(r'[.\-_]+', ' ', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    
    sorted_patterns = sorted(SESSION_PATTERNS, key=lambda p: p.priority, reverse=True)
    
    for pattern in sorted_patterns:
        has_required = any(term in normalized for term in pattern.must_have)
        if not has_required:
            continue
        has_excluded = any(term in normalized for term in pattern.must_not_have)
        if has_excluded:
            continue
        return pattern.session_type
    
    if "grand prix" in normalized:
        session_indicators = ["practice", "qualifying", "sprint", "notebook", "paddock", "press", "warm", "show", "analysis", "uncut"]
        if not any(indicator in normalized for indicator in session_indicators):
            return SessionType.RACE
    
    return SessionType.UNKNOWN

def session_matches_target(text: str, target_session: SessionType) -> bool:
    return classify_session(text) == target_session

# =============================================================================
# CALENDAR KEY MAPPINGS
# =============================================================================

SESSION_TO_CALENDAR_KEY: Dict[SessionType, str] = {
    SessionType.PRACTICE_1: "fp1",
    SessionType.PRACTICE_2: "fp2",
    SessionType.PRACTICE_3: "fp3",
    SessionType.SPRINT_QUALIFYING: "sprint_qualifying",
    SessionType.SPRINT: "sprint",
    SessionType.QUALIFYING: "qualifying",
    SessionType.RACE: "race",
}

CALENDAR_KEY_TO_DAY: Dict[str, str] = {
    "fp1": "friday",
    "fp2": "friday",
    "fp3": "saturday",
    "sprint_qualifying": "friday",
    "sprint": "saturday",
    "qualifying": "saturday",
    "race": "sunday",
}

# =============================================================================
# CALENDAR AND VENUE MATCHING
# =============================================================================

def load_calendar(season: int) -> Optional[dict]:
    config_dir = get_config_dir()
    for prefix in ["f1", "formula1"]:
        calendar_file = config_dir / f"{prefix}_{season}.json"
        if calendar_file.exists():
            try:
                with open(calendar_file, 'r') as f:
                    return json.load(f)
            except Exception:
                continue
    return None

def get_round_by_number(calendar: dict, round_num: int) -> Optional[dict]:
    for round_entry in calendar.get("rounds", []):
        if round_entry.get("round") == round_num:
            return round_entry
    return None

def extract_round_number(text: str) -> Optional[int]:
    match = re.search(r'round[.\s]*(\d{1,2})', text.lower())
    if match:
        return int(match.group(1))
    match = re.search(r'\br(\d{2})\b', text.lower())
    if match:
        return int(match.group(1))
    return None

def match_venue(text: str, calendar: dict) -> Optional[dict]:
    text_lower = text.lower()
    text_normalized = re.sub(r'[.\-_]+', ' ', text_lower)
    text_normalized = re.sub(r'\s+', ' ', text_normalized).strip()
    tokens = set(text_normalized.split())
    
    for round_entry in calendar.get("rounds", []):
        venue = round_entry.get("venue", "").lower()
        aliases = [a.lower() for a in round_entry.get("aliases", [])]
        all_names = [venue] + aliases
        
        for name in all_names:
            if ' ' in name:
                if name in text_normalized:
                    return round_entry
            else:
                if name in tokens:
                    return round_entry
    return None

def identify_round(text: str, calendar: dict) -> Optional[dict]:
    round_num = extract_round_number(text)
    if round_num:
        round_entry = get_round_by_number(calendar, round_num)
        if round_entry:
            return round_entry
    return match_venue(text, calendar)

# =============================================================================
# PREFIX UTILITIES
# =============================================================================

def build_prefix(year: int, round_num: int, session: SessionType) -> str:
    year_short = year % 100
    return f"{year_short:02d}R{round_num:02d}E{int(session):02d}"

def parse_prefix(text: str) -> Optional[Tuple[int, int, SessionType]]:
    match = re.match(r'(\d{2})R(\d{2})E(\d{2})', text)
    if not match:
        return None
    year_short, round_num, session_num = map(int, match.groups())
    year = 2000 + year_short
    try:
        session = SessionType(session_num)
    except ValueError:
        session = SessionType.UNKNOWN
    return (year, round_num, session)

# =============================================================================
# RELEASE PARSING AND FILTERING
# =============================================================================

@dataclass
class ReleaseInfo:
    title: str
    year: Optional[int] = None
    round_num: Optional[int] = None
    venue: Optional[str] = None
    session: SessionType = SessionType.UNKNOWN
    quality: Optional[str] = None
    group: Optional[str] = None

def extract_quality(text: str) -> Optional[str]:
    text_lower = text.lower()
    for quality in ["2160p", "1080p", "720p", "480p"]:
        if quality in text_lower:
            return quality
    return None

def extract_release_group(text: str) -> Optional[str]:
    match = re.search(r'[-.]([A-Za-z][A-Za-z0-9]{1,15})$', text)
    if match:
        return match.group(1).upper()
    return None

def parse_release_title(title: str, calendar: dict = None) -> ReleaseInfo:
    info = ReleaseInfo(title=title)
    year_match = re.search(r'\b(20\d{2})\b', title)
    if year_match:
        info.year = int(year_match.group(1))
    info.round_num = extract_round_number(title)
    if calendar:
        round_entry = identify_round(title, calendar)
        if round_entry:
            info.venue = round_entry.get("venue")
            if not info.round_num:
                info.round_num = round_entry.get("round")
    info.session = classify_session(title)
    info.quality = extract_quality(title)
    info.group = extract_release_group(title)
    return info

def filter_releases(
    releases: List[dict],
    target_session: SessionType,
    season: int,
    round_info: dict,
    required_quality: str = "1080p",
    allowed_groups: List[str] = None,
    calendar: dict = None
) -> List[dict]:
    if allowed_groups is None:
        allowed_groups = ["VERUM", "MWR", "DARKSPORT"]
    allowed_groups_upper = [g.upper() for g in allowed_groups]
    
    venue = round_info.get("venue", "").lower()
    aliases = [a.lower() for a in round_info.get("aliases", [])]
    round_num = round_info.get("round", 0)
    all_locations = [venue] + aliases
    
    scored = []
    
    for release in releases:
        title = release.get('title', '')
        info = parse_release_title(title, calendar)
        
        if not re.search(r'formula\s*1|f1', title.lower()):
            continue
        if info.year != season:
            continue
        location_match = any(loc in title.lower() for loc in all_locations if loc)
        round_match = info.round_num == round_num
        if not location_match and not round_match:
            continue
        if info.quality != required_quality:
            continue
        if info.group and info.group not in allowed_groups_upper:
            continue
        if info.session != target_session:
            continue
        
        score = 100
        if info.group == "MWR":
            score += 30
        elif info.group == "VERUM":
            score += 20
        elif info.group == "DARKSPORT":
            score += 5
        if round_match:
            score += 10
        if target_session == SessionType.RACE_UNCUT:
            score -= 20
        
        release['_score'] = score
        release['_parsed'] = info
        scored.append(release)
    
    scored.sort(key=lambda x: x.get('_score', 0), reverse=True)
    return scored


if __name__ == "__main__":
    print(f"F1 Library Path: {F1_LIBRARY_PATH}")
    print(f"F1 Downloads Path: {F1_DOWNLOADS_PATH}")
    print(f"Config Dir: {CONFIG_DIR}")
