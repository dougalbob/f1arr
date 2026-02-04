#!/usr/bin/env python3
"""
Formula 1 Grabber - Automated Session Downloader
=================================================

"""
import os
import sys
import json
import re
import argparse
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from typing import Dict, List, Optional, Tuple, Set

# Add script directory to path
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

# Import centralized logging FIRST, then other shared F1 module items
from f1_common import (
    setup_logging,
    get_logger,
    SessionType,
    SESSION_DISPLAY_NAMES,
    SESSION_BY_NAME,
    CORE_SESSIONS,
    SPRINT_SESSIONS,
    get_desired_sessions,  # ADD THIS
    classify_session,
    load_calendar,
    get_round_by_number,
    identify_round,
    extract_round_number,
    match_venue,
    build_prefix,
    parse_prefix,
    parse_release_title,
    filter_releases,
    extract_quality,
    extract_release_group,
    get_valid_sessions_for_round,
    is_sprint_weekend,
    get_config_dir,
    F1_LIBRARY_PATH,
    get_prowlarr_url,
    get_prowlarr_api_key,
    get_sabnzbd_url,
    get_sabnzbd_api_key,
)

#===============================================================================
# MODULE LOGGER
#===============================================================================

# Module logger - will be configured by setup_logging() in main()
logger = get_logger("grabber")

#===============================================================================
# CONFIGURATION
#===============================================================================

# --- Log Output Verbosity ---
VERBOSE_SEARCH_OUTPUT = True

# Library path (from settings.yml or environment)
F1_LIBRARY = F1_LIBRARY_PATH

# Config directory
CONFIG_DIR = get_config_dir()

# State file for tracking searched items
STATE_FILE = CONFIG_DIR / "grabber_state.json"

# --- Prowlarr Configuration (from settings.yml or environment) ---
PROWLARR_URL = get_prowlarr_url()
PROWLARR_API_KEY = get_prowlarr_api_key()

# --- SABnzbd Configuration (from settings.yml or environment) ---
SABNZBD_URL = get_sabnzbd_url()
SABNZBD_API_KEY = get_sabnzbd_api_key()
SABNZBD_CATEGORY = "f1"

# --- Grabbing Limits ---
MAX_GRABS_PER_RUN = 6

# --- Quality & Release Group Filters ---
REQUIRED_QUALITY = "1080p"
ALLOWED_GROUPS = ["VERUM", "MWR", "DARKSPORT"]

# --- Default Search Delay (hours after session ends) ---
DEFAULT_SEARCH_DELAY_HOURS = 4

# --- Current Season (for scheduled mode) ---
CURRENT_SEASON = 2026


# --- Session Key Mapping (calendar key -> SessionType) ---
SESSION_KEY_MAP = {
    "fp1": SessionType.PRACTICE_1,
    "fp2": SessionType.PRACTICE_2,
    "fp3": SessionType.PRACTICE_3,
    "sprint_qualifying": SessionType.SPRINT_QUALIFYING,
    "sprint": SessionType.SPRINT,
    "qualifying": SessionType.QUALIFYING,
    "race": SessionType.RACE,
}

# --- Reverse mapping for building calendar keys ---
SESSION_TO_CALENDAR_KEY = {v: k for k, v in SESSION_KEY_MAP.items()}

#===============================================================================
# STATE MANAGEMENT
#===============================================================================

class GrabberState:
    """Manages state persistence for the grabber."""
    
    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self.state = self._load()
    
    def _load(self) -> dict:
        """Load state from file."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load state file: {e}")
        
        return {
            "searched": {},
            "grabbed": {},
            "failed": {},
            "last_run": None,
            "last_scheduled_check": None,
        }
    
    def save(self):
        """Save state to file."""
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        self.state["last_run"] = datetime.now().isoformat()
        
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)
        
        logger.debug(f"State saved to {self.state_file}")
    
    def make_session_key(self, season: int, round_num: int, session: SessionType) -> str:
        """Create a unique key for a session."""
        return f"{season}_R{round_num:02d}_E{int(session):02d}"
    
    def was_searched(self, session_key: str, max_age_days: int = 7) -> bool:
        """Check if a session was recently searched."""
        if session_key not in self.state["searched"]:
            return False
        
        searched_time = datetime.fromisoformat(self.state["searched"][session_key])
        age = datetime.now() - searched_time
        
        return age.days < max_age_days
    
    def was_grabbed(self, session_key: str) -> bool:
        """Check if a session was already grabbed."""
        return session_key in self.state["grabbed"]
    
    def mark_searched(self, session_key: str):
        """Mark a session as searched."""
        self.state["searched"][session_key] = datetime.now().isoformat()
    
    def mark_grabbed(self, session_key: str, release_title: str):
        """Mark a session as grabbed."""
        self.state["grabbed"][session_key] = {
            "timestamp": datetime.now().isoformat(),
            "release": release_title
        }
    
    def mark_failed(self, session_key: str, reason: str):
        """Mark a session search as failed."""
        self.state["failed"][session_key] = {
            "timestamp": datetime.now().isoformat(),
            "reason": reason
        }
    
    def clear_session(self, session_key: str):
        """Clear all state for a session."""
        self.state["searched"].pop(session_key, None)
        self.state["grabbed"].pop(session_key, None)
        self.state["failed"].pop(session_key, None)
    
    def update_scheduled_check(self):
        """Update the last scheduled check timestamp."""
        self.state["last_scheduled_check"] = datetime.now().isoformat()

#===============================================================================
# CALENDAR HELPERS
#===============================================================================

def has_session_times(calendar: dict) -> bool:
    """Check if calendar has detailed session times (2026+ format)."""
    rounds = calendar.get("rounds", [])
    if not rounds:
        return False
    
    first_round = rounds[0]
    return "sessions" in first_round


def parse_session_datetime(round_info: dict, day: str, session_key: str) -> Optional[datetime]:
    """
    Parse a session's GMT datetime from the calendar.
    
    Args:
        round_info: Round dictionary from calendar
        day: 'friday', 'saturday', or 'sunday'
        session_key: e.g., 'fp1', 'race'
    
    Returns:
        datetime object in UTC, or None if not found
    """
    sessions = round_info.get("sessions", {})
    day_sessions = sessions.get(day, {})
    session_info = day_sessions.get(session_key)
    
    if not session_info:
        return None
    
    gmt_time = session_info.get("gmt")
    if not gmt_time:
        return None
    
    weekend_start = round_info.get("weekend_start")
    race_date = round_info.get("date")
    
    if not weekend_start or not race_date:
        return None
    
    try:
        weekend_start_dt = datetime.strptime(weekend_start, "%Y-%m-%d")
        race_date_dt = datetime.strptime(race_date, "%Y-%m-%d")
        
        if day == "friday":
            session_date = weekend_start_dt
        elif day == "saturday":
            session_date = weekend_start_dt + timedelta(days=1)
        elif day == "sunday":
            session_date = race_date_dt
        else:
            return None
        
        gmt_day = session_info.get("gmt_day")
        if gmt_day:
            day_offsets = {
                "thursday": -1 if day == "friday" else 0,
                "friday": -1 if day == "saturday" else 0,
                "saturday": -1 if day == "sunday" else 0,
            }
            offset = day_offsets.get(gmt_day, 0)
            session_date = session_date + timedelta(days=offset)
        
        hour, minute = map(int, gmt_time.split(":"))
        session_dt = session_date.replace(hour=hour, minute=minute)
        
        duration = session_info.get("duration_mins", 60)
        end_dt = session_dt + timedelta(minutes=duration)
        
        return end_dt
        
    except Exception as e:
        logger.debug(f"Error parsing session datetime: {e}")
        return None


def get_session_end_time(round_info: dict, session_type: SessionType) -> Optional[datetime]:
    """
    Get the end time (GMT) for a specific session.
    
    Args:
        round_info: Round dictionary from calendar
        session_type: SessionType enum
    
    Returns:
        datetime in UTC when session ends, or None
    """
    calendar_key = SESSION_TO_CALENDAR_KEY.get(session_type)
    if not calendar_key:
        logger.debug(f"No calendar key for {session_type.display_name}")
        return None
    
    day_mapping = {
        "fp1": "friday",
        "fp2": "friday",
        "fp3": "saturday",
        "sprint_qualifying": "friday",
        "sprint": "saturday",
        "qualifying": "saturday",
        "race": "sunday",
    }
    
    day = day_mapping.get(calendar_key)
    if not day:
        return None
    
    return parse_session_datetime(round_info, day, calendar_key)


def get_sessions_ready_for_search(calendar: dict, search_delay_hours: int = None) -> List[dict]:
    """
    Find sessions that have finished and are ready to be searched.
    """
    ready_sessions = []
    now = datetime.now()
    
    for round_info in calendar.get("rounds", []):
        round_num = round_info.get("round", 0)
        delay = search_delay_hours or round_info.get("search_delay_hours", DEFAULT_SEARCH_DELAY_HOURS)
        
        for session_type, wanted in get_desired_sessions().items():
            if not wanted:
                continue
            
            end_time = get_session_end_time(round_info, session_type)
            
            if not end_time:
                continue
            
            search_after = end_time + timedelta(hours=delay)
            
            if now >= search_after:
                ready_sessions.append({
                    "round": round_num,
                    "round_info": round_info,
                    "session_type": session_type,
                    "ended_at": end_time,
                    "search_after": search_after,
                })
    
    return ready_sessions


def get_upcoming_sessions(calendar: dict, hours_ahead: int = 48) -> List[dict]:
    """Find sessions that will happen in the next X hours."""
    upcoming = []
    now = datetime.now()
    cutoff = now + timedelta(hours=hours_ahead)
    
    for round_info in calendar.get("rounds", []):
        round_num = round_info.get("round", 0)
        venue = round_info.get("venue", "Unknown")
        
        for session_type, wanted in get_desired_sessions().items():
            if not wanted:
                continue
            
            end_time = get_session_end_time(round_info, session_type)
            
            if not end_time:
                continue
            
            duration_map = {
                SessionType.PRACTICE_1: 60,
                SessionType.PRACTICE_2: 60,
                SessionType.PRACTICE_3: 60,
                SessionType.SPRINT_QUALIFYING: 45,
                SessionType.SPRINT: 30,
                SessionType.QUALIFYING: 60,
                SessionType.RACE: 120,
            }
            duration = duration_map.get(session_type, 60)
            start_time = end_time - timedelta(minutes=duration)
            
            if now < start_time <= cutoff:
                upcoming.append({
                    "round": round_num,
                    "venue": venue,
                    "session_type": session_type,
                    "starts_at": start_time,
                    "ends_at": end_time,
                })
    
    upcoming.sort(key=lambda x: x["starts_at"])
    return upcoming

#===============================================================================
# LIBRARY SCANNING
#===============================================================================

def get_session_from_element(element_num: int) -> Optional[SessionType]:
    """Get SessionType from element number."""
    try:
        return SessionType(element_num)
    except ValueError:
        return None


def scan_library_for_season(season: int) -> Dict[int, Set[SessionType]]:
    """Scan the library to find existing sessions for a season."""
    season_path = os.path.join(F1_LIBRARY, str(season))
    
    if not os.path.exists(season_path):
        logger.warning(f"Season folder not found: {season_path}")
        return {}
    
    existing = {}
    year_short = season % 100
    
    for round_folder in os.listdir(season_path):
        round_path = os.path.join(season_path, round_folder)
        
        if not os.path.isdir(round_path):
            continue
        
        round_match = re.match(r'Round\s*(\d+)', round_folder)
        if not round_match:
            continue
        
        round_num = int(round_match.group(1))
        existing[round_num] = set()
        
        for session_folder in os.listdir(round_path):
            session_path = os.path.join(round_path, session_folder)
            
            if not os.path.isdir(session_path):
                continue
            
            parsed = parse_prefix(session_folder)
            if parsed:
                folder_year, folder_round, session_type = parsed
                
                if folder_year == season and folder_round == round_num:
                    existing[round_num].add(session_type)
                    logger.debug(f"Found: Round {round_num} - {session_type.display_name}")
    
    return existing


def identify_missing_sessions(
    season: int,
    calendar: dict,
    existing: Dict[int, Set[SessionType]],
    round_filter: Optional[int] = None,
    only_past: bool = True
) -> List[dict]:
    """Identify missing desired sessions, excluding impossible ones."""
    missing = []
    today = datetime.now().date()
    
    for round_entry in calendar.get("rounds", []):
        round_num = round_entry.get("round", 0)
        
        if round_filter and round_num != round_filter:
            continue
        
        if only_past:
            round_date_str = round_entry.get("date", "")
            if round_date_str:
                try:
                    round_date = datetime.strptime(round_date_str, "%Y-%m-%d").date()
                    if round_date > today:
                        continue
                except ValueError:
                    pass
        
        existing_for_round = existing.get(round_num, set())
        
        # Get valid sessions for this specific round (excludes FP2/FP3 for sprints)
        valid_sessions = get_valid_sessions_for_round(round_entry, get_desired_sessions())
        
        for session_type in valid_sessions:
            if session_type not in existing_for_round:
                missing.append({
                    "round": round_num,
                    "round_info": round_entry,
                    "session_type": session_type,
                })
    
    return missing

#===============================================================================
# PROWLARR SEARCH
#===============================================================================

def get_indexers() -> List[dict]:
    """Get list of enabled Prowlarr indexers."""
    try:
        url = f"{PROWLARR_URL}/api/v1/indexer"
        headers = {"X-Api-Key": PROWLARR_API_KEY}
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            indexers = response.json()
            return [i for i in indexers if i.get('enable', False)]
        return []
    except Exception as e:
        logger.error(f"Error getting indexers: {e}")
        return []


def parse_newznab_xml(xml_text: str, indexer_name: str = "Unknown") -> List[dict]:
    """Parse Newznab XML response into release dictionaries."""
    releases = []
    
    try:
        if isinstance(xml_text, bytes):
            xml_text = xml_text.decode('utf-8', errors='replace')
        
        root = ET.fromstring(xml_text)
        
        for item in root.findall('.//item'):
            release = {'indexer': indexer_name}
            
            title_elem = item.find('title')
            if title_elem is not None and title_elem.text:
                release['title'] = title_elem.text.strip()
            
            guid_elem = item.find('guid')
            if guid_elem is not None and guid_elem.text:
                release['guid'] = guid_elem.text.strip()
            
            link_elem = item.find('link')
            if link_elem is not None and link_elem.text:
                release['link'] = link_elem.text.strip()
            
            pubdate_elem = item.find('pubDate')
            if pubdate_elem is not None and pubdate_elem.text:
                release['publishDate'] = pubdate_elem.text.strip()
            
            enclosure = item.find('enclosure')
            if enclosure is not None:
                try:
                    release['size'] = int(enclosure.get('length', 0))
                except:
                    release['size'] = 0
                release['downloadUrl'] = enclosure.get('url', '')
            
            for attr in item.findall('.//{http://www.newznab.com/DTD/2010/feeds/attributes/}attr'):
                name = attr.get('name', '')
                value = attr.get('value', '')
                
                if name == 'size' and 'size' not in release:
                    try:
                        release['size'] = int(value)
                    except:
                        pass
            
            if release.get('title'):
                releases.append(release)
    
    except ET.ParseError as e:
        logger.debug(f"XML parse error: {e}")
    except Exception as e:
        logger.debug(f"Error parsing XML: {e}")
    
    return releases


def search_indexer(query: str, indexer_id: int, indexer_name: str) -> List[dict]:
    """Search a specific indexer via Prowlarr."""
    try:
        url = f"{PROWLARR_URL}/{indexer_id}/api"
        params = {
            "t": "search",
            "q": query,
            "apikey": PROWLARR_API_KEY,
            "limit": 100,
        }
        
        response = requests.get(url, params=params, timeout=60)
        
        if response.status_code == 200:
            return parse_newznab_xml(response.text, indexer_name)
        return []
    except Exception as e:
        logger.debug(f"Error searching {indexer_name}: {e}")
        return []


def build_search_queries(round_info: dict, session_type: SessionType, season: int) -> List[str]:
    """
    Build search queries for a session.
    
    Strategy: Search broadly by venue/round, let filter_releases() narrow to specific session.
    """
    queries = []
    
    venue = round_info.get("venue", "")
    aliases = round_info.get("aliases", [])
    round_num = round_info.get("round", 0)
    
    # =========================================================================
    # VENUE-BASED QUERIES FIRST (highest result count)
    # =========================================================================
    queries.append(f"Formula1 {season} {venue}")
    queries.append(f"Formula1 {venue} {season}")
    queries.append(f"F1 {season} {venue}")
    
    # =========================================================================
    # GRAND PRIX QUERIES
    # =========================================================================
    queries.append(f"Formula1 {season} {venue} Grand Prix")
    
    for alias in aliases:
        if "grand prix" in alias.lower():
            queries.append(f"Formula1 {season} {alias}")
            break
    
    # =========================================================================
    # ALIAS-BASED QUERIES (for venues with common alternate names)
    # =========================================================================
    # Skip overly generic terms
    skip_terms = {"usa", "united states", "american", "america", "uk", "gp"}
    
    for alias in aliases[:3]:
        alias_lower = alias.lower()
        if alias_lower in skip_terms:
            continue
        if "grand prix" in alias_lower:
            continue
        if len(alias) > 2:
            queries.append(f"Formula1 {season} {alias}")
    
    # =========================================================================
    # ROUND-BASED QUERIES (fallback - fewer results but reliable)
    # =========================================================================
    queries.append(f"Formula1 {season} Round{round_num:02d}")
    queries.append(f"Formula1 {season} Round{round_num}")
    queries.append(f"Formula1 {season} Round {round_num}")
    
    # =========================================================================
    # COUNTRY + VENUE QUERIES (for USA races, etc.)
    # =========================================================================
    if "usa" in [a.lower() for a in aliases]:
        queries.append(f"Formula1 {season} USA {venue}")
    
    # Remove duplicates while preserving order
    seen = set()
    unique_queries = []
    for q in queries:
        q_lower = q.lower()
        if q_lower not in seen:
            seen.add(q_lower)
            unique_queries.append(q)
    
    return unique_queries


def search_for_session(
    round_info: dict,
    session_type: SessionType,
    season: int,
    indexers: List[dict],
    calendar: dict = None
) -> Tuple[List[dict], List[str]]:
    """
    Search all indexers for a specific session.
    
    Returns:
        Tuple of (filtered_results, queries_used)
    """
    queries = build_search_queries(round_info, session_type, season)
    
    all_results = []
    seen_guids = set()
    
    round_num = round_info.get("round", "?")
    venue = round_info.get('venue', 'Unknown')
    
    logger.info(f"🔍 Searching: Round {round_num} {venue} - {session_type.display_name}")
    
    for indexer in indexers:
        idx_id = indexer.get('id')
        idx_name = indexer.get('name', 'Unknown')
        
        for query in queries[:8]:  # Changed from [:3] to [:5]  and now to 8 to include other aliases and round number "Round 19" queries 
            results = search_indexer(query, idx_id, idx_name)
            
            for release in results:
                guid = release.get('guid', '')
                if guid and guid not in seen_guids:
                    seen_guids.add(guid)
                    all_results.append(release)
    
    logger.info(f"📋 Found {len(all_results)} candidates for Round {round_num} {venue} - {session_type.display_name}")
    
    # Use shared filter_releases from f1_common
    filtered = filter_releases(
        releases=all_results,
        target_session=session_type,
        season=season,
        round_info=round_info,
        required_quality=REQUIRED_QUALITY,
        allowed_groups=ALLOWED_GROUPS,
        calendar=calendar
    )
    
    return filtered, queries[:8]  # Updated return value - have increased this to 8 queries 


def print_search_queries(queries: List[str], verbose: bool = VERBOSE_SEARCH_OUTPUT):
    """Print search queries based on verbosity setting."""
    if verbose:
        print("     Queries:")
        for query in queries:
            print(f"       • \"{query}\"")
    else:
        if queries:
            print(f"     → \"{queries[0]}\"")

#===============================================================================
# SABNZBD INTEGRATION
#===============================================================================

def test_sabnzbd_connection() -> bool:
    """Test connection to SABnzbd."""
    try:
        params = {
            "mode": "version",
            "apikey": SABNZBD_API_KEY,
            "output": "json"
        }
        
        response = requests.get(f"{SABNZBD_URL}/api", params=params, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            version = result.get('version', 'Unknown')
            logger.info(f"SABnzbd connected: v{version}")
            return True
        return False
    except Exception as e:
        logger.error(f"SABnzbd connection failed: {e}")
        return False


def grab_release(release: dict, session_info: dict) -> bool:
    """
    Send a release to SABnzbd with metadata-enriched name.
    
    The NZB name includes the YYRREE prefix so f1_sort.py can
    skip classification and use the prefix directly.
    """
    download_url = release.get('downloadUrl') or release.get('link')
    
    if not download_url:
        logger.error("No download URL available")
        return False
    
    # Build metadata-enriched name with prefix
    season = session_info['season']
    round_num = session_info['round']
    session_type = session_info['session_type']
    venue = session_info['venue']
    
    prefix = build_prefix(season, round_num, session_type)
    
    # Format: "25R23E19 Qatar Race"
    enriched_name = f"{prefix} {venue} {session_type.display_name}"
    
    try:
        params = {
            "mode": "addurl",
            "name": download_url,
            "nzbname": enriched_name,
            "cat": SABNZBD_CATEGORY,
            "apikey": SABNZBD_API_KEY,
            "output": "json"
        }
        
        response = requests.get(f"{SABNZBD_URL}/api", params=params, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            if result.get('status', False):
                logger.info(f"✅ Grabbed: {enriched_name}")
                logger.debug(f"   Original: {release.get('title', 'Unknown')}")
                return True
            else:
                logger.error(f"SABnzbd error: {result.get('error', 'Unknown')}")
                return False
        return False
    except Exception as e:
        logger.error(f"Grab error: {e}")
        return False

#===============================================================================
# DISPLAY HELPERS
#===============================================================================

def format_size(size_bytes: int) -> str:
    """Convert bytes to human readable format."""
    if not size_bytes:
        return "Unknown"
    
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def format_time_until(dt: datetime) -> str:
    """Format time remaining until a datetime."""
    now = datetime.now()
    delta = dt - now
    
    if delta.total_seconds() < 0:
        return "now"
    
    hours = int(delta.total_seconds() // 3600)
    minutes = int((delta.total_seconds() % 3600) // 60)
    
    if hours > 24:
        days = hours // 24
        return f"{days}d {hours % 24}h"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"


def format_time_ago(dt: datetime) -> str:
    """Format time since a datetime."""
    now = datetime.now()
    delta = now - dt
    
    if delta.total_seconds() < 0:
        return "not yet"
    
    hours = int(delta.total_seconds() // 3600)
    minutes = int((delta.total_seconds() % 3600) // 60)
    
    if hours > 24:
        days = hours // 24
        return f"{days}d {hours % 24}h ago"
    elif hours > 0:
        return f"{hours}h {minutes}m ago"
    else:
        return f"{minutes}m ago"


def print_missing_summary(missing: List[dict]):
    """Print a summary of missing sessions."""
    print("\n" + "=" * 70)
    print("MISSING SESSIONS")
    print("=" * 70)
    
    if not missing:
        print("\n  ✅ No missing sessions found!")
        return
    
    by_round = {}
    for item in missing:
        round_num = item['round']
        if round_num not in by_round:
            by_round[round_num] = {
                'venue': item['round_info'].get('venue', 'Unknown'),
                'sessions': set()
            }
        by_round[round_num]['sessions'].add(item['session_type'])
    
    for round_num in sorted(by_round.keys()):
        info = by_round[round_num]
        print(f"\n  Round {round_num} - {info['venue']}:")
        for session in sorted(info['sessions'], key=lambda s: int(s)):
            print(f"    ❌ {session.display_name}")
    
    print(f"\n  Total missing: {len(missing)} sessions")
    print("=" * 70)


def print_search_results(results: List[dict], limit: int = 5):
    """Print search results."""
    if not results:
        print("    No matching results found")
        return
    
    for i, r in enumerate(results[:limit], 1):
        title = r.get('title', 'Unknown')
        size = format_size(r.get('size', 0))
        indexer = r.get('indexer', 'Unknown')
        score = r.get('_score', 0)
        
        print(f"    [{i}] {title[:60]}{'...' if len(title) > 60 else ''}")
        print(f"        {indexer} | {size} | Score: {score}")


def print_upcoming_sessions(upcoming: List[dict]):
    """Print upcoming sessions."""
    print("\n" + "=" * 70)
    print("UPCOMING SESSIONS (Next 48 hours)")
    print("=" * 70)
    
    if not upcoming:
        print("\n  No sessions scheduled in the next 48 hours")
        return
    
    current_round = None
    
    for session in upcoming:
        if session['round'] != current_round:
            current_round = session['round']
            print(f"\n  Round {current_round} - {session['venue']}:")
        
        starts = session['starts_at'].strftime("%a %H:%M")
        until = format_time_until(session['starts_at'])
        
        print(f"    ⏰ {starts} (in {until}) - {session['session_type'].display_name}")
    
    print("=" * 70)


def print_ready_sessions(ready: List[dict]):
    """Print sessions ready for search."""
    print("\n" + "=" * 70)
    print("SESSIONS READY FOR SEARCH")
    print("=" * 70)
    
    if not ready:
        print("\n  No sessions are ready for searching right now")
        return
    
    for session in ready:
        ended = format_time_ago(session['ended_at'])
        venue = session['round_info'].get('venue', 'Unknown')
        
        print(f"\n  Round {session['round']} {venue}:")
        print(f"    📺 {session['session_type'].display_name}")
        print(f"    🕐 Ended: {ended}")
    
    print(f"\n  Total ready: {len(ready)} sessions")
    print("=" * 70)

#===============================================================================
# MAIN COMMANDS
#===============================================================================

def cmd_backfill(args):
    """Backfill missing sessions for a season."""
    season = args.season
    dry_run = args.dry_run
    round_filter = args.round
    force = args.force
    
    print(f"\n{'=' * 60}")
    print(f"  FORMULA 1 GRABBER - BACKFILL MODE")
    print(f"{'=' * 60}")
    print(f"\n  Season:     {season}")
    print(f"  Dry Run:    {dry_run}")
    print(f"  Max Grabs:  {MAX_GRABS_PER_RUN}")
    if round_filter:
        print(f"  Round:      {round_filter} only")
    print(f"  Verbose:    {VERBOSE_SEARCH_OUTPUT}")
    print(f"  Time:       {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    state = GrabberState()
    
    calendar = load_calendar(season)
    if not calendar:
        print(f"\n❌ Could not load calendar for {season}")
        return 1
    
    print(f"\n  Calendar:   {len(calendar.get('rounds', []))} rounds loaded")
    
    print(f"\n{'─' * 40}")
    print("CONNECTION TESTS")
    print(f"{'─' * 40}")
    
    try:
        indexers = get_indexers()
        if indexers:
            print(f"  ✅ Prowlarr: {len(indexers)} indexers")
        else:
            print("  ❌ Prowlarr: No indexers found")
            return 1
    except Exception as e:
        print(f"  ❌ Prowlarr: {e}")
        return 1
    
    if not dry_run:
        if not test_sabnzbd_connection():
            print("  ❌ SABnzbd: Connection failed")
            return 1
    else:
        print("  ⏭️  SABnzbd: Skipped (dry run)")
    
    print(f"\n{'─' * 40}")
    print("SCANNING LIBRARY")
    print(f"{'─' * 40}")
    
    existing = scan_library_for_season(season)
    total_existing = sum(len(sessions) for sessions in existing.values())
    print(f"  Found {total_existing} existing sessions across {len(existing)} rounds")
    
    missing = identify_missing_sessions(season, calendar, existing, round_filter)
    
    # Filter by specific sessions if provided
    sessions_filter = getattr(args, 'sessions', None)
    if sessions_filter:
        session_nums = []
        for s in sessions_filter.split(','):
            s = s.strip().upper()
            if s.startswith('E'):
                s = s[1:]
            try:
                session_nums.append(int(s))
            except ValueError:
                pass
        
        if session_nums:
            missing = [m for m in missing if int(m['session_type']) in session_nums]
            print(f"\n  Filtered to {len(missing)} sessions: {sessions_filter}")
    print_missing_summary(missing)
    
    if not missing:
        state.save()
        return 0
    
    if not force:
        filtered_missing = []
        for item in missing:
            session_key = state.make_session_key(
                season,
                item['round'],
                item['session_type']
            )
            
            if state.was_grabbed(session_key):
                logger.debug(f"Skipping {session_key}: already grabbed")
                continue
            
            if state.was_searched(session_key):
                logger.debug(f"Skipping {session_key}: recently searched")
                continue
            
            filtered_missing.append(item)
        
        skipped = len(missing) - len(filtered_missing)
        if skipped > 0:
            print(f"\n  ⏭️  Skipping {skipped} recently searched sessions")
            print(f"     (use --force to search again)")
        
        missing = filtered_missing
    
    if not missing:
        print("\n  ℹ️  All missing sessions have been recently searched")
        state.save()
        return 0
    
    print(f"\n{'─' * 40}")
    print(f"SEARCHING & GRABBING (Season {season})")
    print(f"{'─' * 40}")
    
    grabs_this_run = 0
    grabbed_titles_this_run = set()
    
    for item in missing:
        if grabs_this_run >= MAX_GRABS_PER_RUN:
            print(f"\n  ⚠️  Reached max grabs limit ({MAX_GRABS_PER_RUN})")
            break
        
        round_num = item['round']
        round_info = item['round_info']
        session_type = item['session_type']
        venue = round_info.get('venue', 'Unknown')
        
        session_key = state.make_session_key(season, round_num, session_type)
        
        print(f"\n  🔍 Round {round_num} {venue}: {session_type.display_name}")
        
        results, queries_used = search_for_session(
            round_info, session_type, season, indexers, calendar
        )
        
        print_search_queries(queries_used)
        
        state.mark_searched(session_key)
        
        if not results:
            print("    ❌ No matching releases found")
            logger.info(f"❌ No candidates: Round {round_num} {venue} - {session_type.display_name}")
            state.mark_failed(session_key, "No results")
            continue
        
        available_results = [
            r for r in results 
            if r.get('title', '').lower() not in grabbed_titles_this_run
        ]
        
        if not available_results:
            print(f"    ⚠️  All {len(results)} results already grabbed this run")
            state.mark_failed(session_key, "All results already grabbed")
            continue
        
        print(f"    Found {len(available_results)} matching releases:")
        print_search_results(available_results, limit=3)
        
        best = available_results[0]
        best_title = best.get('title', 'Unknown')
        
        # Prepare session info for grab_release
        session_info = {
            'season': season,
            'round': round_num,
            'session_type': session_type,
            'venue': venue,
        }
        
        if dry_run:
            prefix = build_prefix(season, round_num, session_type)
            print(f"\n    [DRY-RUN] Would grab as: {prefix} {venue} {session_type.display_name}")
            print(f"              Original: {best_title[:50]}")
            grabbed_titles_this_run.add(best_title.lower())
            grabs_this_run += 1
        else:
            print(f"\n    ⬇️  Grabbing: {best_title[:50]}")
            
            if grab_release(best, session_info):
                state.mark_grabbed(session_key, best_title)
                grabbed_titles_this_run.add(best_title.lower())
                grabs_this_run += 1
            else:
                state.mark_failed(session_key, "Grab failed")
    
    state.save()
    
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Sessions searched: {len(missing)}")
    print(f"  Releases grabbed:  {grabs_this_run}")
    if grabs_this_run < len(missing):
        remaining = len(missing) - grabs_this_run
        print(f"  Remaining:         {remaining}")
    print(f"{'=' * 60}\n")
    
    return 0


def cmd_scheduled(args):
    """Run scheduled check for the current season."""
    season = args.season or CURRENT_SEASON
    dry_run = args.dry_run
    force = args.force
    
    print(f"\n{'=' * 60}")
    print(f"  FORMULA 1 GRABBER - SCHEDULED MODE")
    print(f"{'=' * 60}")
    print(f"\n  Season:       {season}")
    print(f"  Dry Run:      {dry_run}")
    print(f"  Max Grabs:    {MAX_GRABS_PER_RUN}")
    print(f"  Search Delay: {DEFAULT_SEARCH_DELAY_HOURS} hours")
    print(f"  Verbose:      {VERBOSE_SEARCH_OUTPUT}")
    print(f"  Time (GMT):   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    state = GrabberState()
    
    calendar = load_calendar(season)
    if not calendar:
        print(f"\n❌ Could not load calendar for {season}")
        return 1
    
    if not has_session_times(calendar):
        print(f"\n❌ Calendar for {season} doesn't have session times")
        print("   Scheduled mode requires the 2026+ calendar format")
        print("   Use 'backfill' mode for older seasons")
        return 1
    
    print(f"\n  Calendar: {len(calendar.get('rounds', []))} rounds with session times")
    
    upcoming = get_upcoming_sessions(calendar, hours_ahead=48)
    print_upcoming_sessions(upcoming)
    
    ready = get_sessions_ready_for_search(calendar)
    
    existing = scan_library_for_season(season)
    
    filtered_ready = []
    for item in ready:
        round_num = item['round']
        session_type = item['session_type']
        
        if round_num in existing and session_type in existing[round_num]:
            logger.debug(f"Skipping {session_type.display_name}: already in library")
            continue
        
        session_key = state.make_session_key(season, round_num, session_type)
        
        if state.was_grabbed(session_key):
            logger.debug(f"Skipping {session_key}: already grabbed")
            continue
        
        if not force and state.was_searched(session_key, max_age_days=1):
            logger.debug(f"Skipping {session_key}: recently searched")
            continue
        
        filtered_ready.append(item)
    
    print_ready_sessions(filtered_ready)
    
    if not filtered_ready:
        print("\n  ℹ️  No new sessions to search for")
        state.update_scheduled_check()
        state.save()
        return 0
    
    print(f"\n{'─' * 40}")
    print("CONNECTION TESTS")
    print(f"{'─' * 40}")
    
    try:
        indexers = get_indexers()
        if indexers:
            print(f"  ✅ Prowlarr: {len(indexers)} indexers")
        else:
            print("  ❌ Prowlarr: No indexers found")
            return 1
    except Exception as e:
        print(f"  ❌ Prowlarr: {e}")
        return 1
    
    if not dry_run:
        if not test_sabnzbd_connection():
            print("  ❌ SABnzbd: Connection failed")
            return 1
    else:
        print("  ⏭️  SABnzbd: Skipped (dry run)")
    
    print(f"\n{'─' * 40}")
    print(f"SEARCHING & GRABBING (Season {season})")
    print(f"{'─' * 40}")
    
    grabs_this_run = 0
    
    for item in filtered_ready:
        if grabs_this_run >= MAX_GRABS_PER_RUN:
            print(f"\n  ⚠️  Reached max grabs limit ({MAX_GRABS_PER_RUN})")
            break
        
        round_num = item['round']
        round_info = item['round_info']
        session_type = item['session_type']
        venue = round_info.get('venue', 'Unknown')
        
        session_key = state.make_session_key(season, round_num, session_type)
        
        ended_ago = format_time_ago(item['ended_at'])
        print(f"\n  🔍 Round {round_num} {venue}: {session_type.display_name}")
        print(f"      Session ended: {ended_ago}")
        
        results, queries_used = search_for_session(
            round_info, session_type, season, indexers, calendar
        )
        
        print_search_queries(queries_used)
        
        state.mark_searched(session_key)
        
        if not results:
            print("    ❌ No matching releases found yet")
            logger.info(f"❌ No candidates: Round {round_num} {venue} - {session_type.display_name}")
            state.mark_failed(session_key, "No results")
            continue
        
        print(f"    Found {len(results)} matching releases:")
        print_search_results(results, limit=3)
        
        best = results[0]
        
        session_info = {
            'season': season,
            'round': round_num,
            'session_type': session_type,
            'venue': venue,
        }
        
        if dry_run:
            prefix = build_prefix(season, round_num, session_type)
            print(f"\n    [DRY-RUN] Would grab as: {prefix} {venue} {session_type.display_name}")
            grabs_this_run += 1
        else:
            print(f"\n    ⬇️  Grabbing: {best.get('title', 'Unknown')[:50]}")
            
            if grab_release(best, session_info):
                state.mark_grabbed(session_key, best.get('title', 'Unknown'))
                grabs_this_run += 1
            else:
                state.mark_failed(session_key, "Grab failed")
    
    state.update_scheduled_check()
    state.save()
    
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Sessions ready:    {len(filtered_ready)}")
    print(f"  Releases grabbed:  {grabs_this_run}")
    
    if upcoming:
        next_session = upcoming[0]
        next_time = format_time_until(next_session['starts_at'])
        print(f"\n  Next session: {next_session['session_type'].display_name}")
        print(f"                Round {next_session['round']} {next_session['venue']} in {next_time}")
    
    print(f"{'=' * 60}\n")
    
    return 0


def cmd_status(args):
    """Show status and upcoming sessions."""
    season = args.season or CURRENT_SEASON
    
    print(f"\n{'=' * 60}")
    print(f"  FORMULA 1 GRABBER - STATUS")
    print(f"{'=' * 60}")
    
    state = GrabberState()
    
    print(f"\n  Last run:             {state.state.get('last_run', 'Never')}")
    print(f"  Last scheduled check: {state.state.get('last_scheduled_check', 'Never')}")
    print(f"\n  Searched sessions:    {len(state.state.get('searched', {}))}")
    print(f"  Grabbed sessions:     {len(state.state.get('grabbed', {}))}")
    print(f"  Failed sessions:      {len(state.state.get('failed', {}))}")
    
    calendar = load_calendar(season)
    if calendar:
        if has_session_times(calendar):
            upcoming = get_upcoming_sessions(calendar, hours_ahead=72)
            print_upcoming_sessions(upcoming)
            
            ready = get_sessions_ready_for_search(calendar)
            if ready:
                print(f"\n  ⚠️  {len(ready)} sessions ready for search!")
                print(f"     Run: python3 f1_grabber.py scheduled")
        
        season_prefix = f"{season}_"
        grabbed = {k: v for k, v in state.state.get('grabbed', {}).items() 
                   if k.startswith(season_prefix)}
        
        if grabbed:
            print(f"\n  Recently grabbed for {season}:")
            for key, info in sorted(grabbed.items())[-10:]:
                parts = key.split('_')
                ts = info.get('timestamp', '')[:10]
                # Parse session from key
                try:
                    session_num = int(parts[2][1:])  # E19 -> 19
                    session = SessionType(session_num)
                    session_name = session.display_name
                except:
                    session_name = parts[2]
                print(f"    ✅ {parts[1]} {session_name} ({ts})")
    
    print(f"\n{'=' * 60}\n")
    return 0


def cmd_clear(args):
    """Clear grabber state."""
    state = GrabberState()
    
    if args.all:
        state.state = {
            "searched": {},
            "grabbed": {},
            "failed": {},
            "last_run": None,
            "last_scheduled_check": None,
        }
        print("✅ All state cleared")
    elif args.session:
        state.clear_session(args.session)
        print(f"✅ Cleared state for: {args.session}")
    elif args.searched:
        state.state["searched"] = {}
        print("✅ Cleared searched state")
    elif args.failed:
        state.state["failed"] = {}
        print("✅ Cleared failed state")
    
    state.save()
    return 0

#===============================================================================
# MAIN ENTRY POINT
#===============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Formula 1 Grabber - Automated Session Downloader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Backfill missing 2025 sessions
  python3 f1_grabber.py backfill --season 2025 --dry-run
  python3 f1_grabber.py backfill --season 2025
  
  # Run scheduled check for 2026 (current season)
  python3 f1_grabber.py scheduled
  python3 f1_grabber.py scheduled --dry-run
  
  # Check status and upcoming sessions
  python3 f1_grabber.py status
  
  # Clear state to retry searches
  python3 f1_grabber.py clear --searched
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to run')
    
    # Backfill command
    backfill_parser = subparsers.add_parser('backfill', help='Find and grab missing sessions for a past season')
    backfill_parser.add_argument('--season', type=int, required=True, help='Season year (e.g., 2025)')
    backfill_parser.add_argument('--round', type=int, help='Specific round number')
    backfill_parser.add_argument('--dry-run', action='store_true', help='Preview without grabbing')
    backfill_parser.add_argument('--force', action='store_true', help='Ignore recent search history')
    backfill_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    backfill_parser.add_argument('--sessions', type=str, help='Comma-separated session numbers (e.g., E07,E15,E19)')

    # Scheduled command
    scheduled_parser = subparsers.add_parser('scheduled', help='Check for recently aired sessions')
    scheduled_parser.add_argument('--season', type=int, help=f'Season year (default: {CURRENT_SEASON})')
    scheduled_parser.add_argument('--dry-run', action='store_true', help='Preview without grabbing')
    scheduled_parser.add_argument('--force', action='store_true', help='Ignore recent search history')
    scheduled_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    # Status command
    status_parser = subparsers.add_parser('status', help='Show grabber status and upcoming sessions')
    status_parser.add_argument('--season', type=int, help=f'Season year (default: {CURRENT_SEASON})')
    
    # Clear command
    clear_parser = subparsers.add_parser('clear', help='Clear grabber state')
    clear_parser.add_argument('--all', action='store_true', help='Clear all state')
    clear_parser.add_argument('--searched', action='store_true', help='Clear searched state only')
    clear_parser.add_argument('--failed', action='store_true', help='Clear failed state only')
    clear_parser.add_argument('--session', type=str, help='Clear specific session key')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    # Setup centralized logging
    verbose = getattr(args, 'verbose', False)
    setup_logging(debug_mode=verbose)
    logger.info("F1 Grabber initialized")
    
    if args.command == 'backfill':
        return cmd_backfill(args)
    elif args.command == 'scheduled':
        return cmd_scheduled(args)
    elif args.command == 'status':
        return cmd_status(args)
    elif args.command == 'clear':
        return cmd_clear(args)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())