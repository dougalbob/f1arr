#!/usr/bin/env python3
"""
F1 Plex Tagger Module
======================

Handles Formula 1 content tagging in Plex.
"""

import os
import sys
import re
import argparse
import time
import logging
from pathlib import Path
from plexapi.server import PlexServer

# Add script directory to path
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

# Import from f1_common (which uses config loader)
from f1_common import (
    SessionType,
    SESSION_DISPLAY_NAMES,
    load_calendar,
    get_round_by_number,
    parse_prefix,
    get_plex_url,
    get_plex_token,
    get_plex_library_name,
    get_plex_library_id,
    get_config_dir,
    F1_LIBRARY_PATH,
    translate_path_for_plex,
)

# =============================================================================
# CONFIGURATION
# =============================================================================

# Plex configuration (from settings.yml or environment)
PLEX_URL = get_plex_url()
PLEX_TOKEN = get_plex_token()
PLEX_LIBRARY = get_plex_library_name()

# Validate required settings
if not PLEX_URL or not PLEX_TOKEN:
    logging.warning("Plex URL or Token not configured - tagging will fail")

# Library path
F1_BASE_PATH = F1_LIBRARY_PATH

# Config directory
CONFIG_DIR = get_config_dir()

# Feature race session type (for special genre tagging)
FEATURE_RACE_SESSION = SessionType.RACE

# Country mappings based on F1 venue
VENUE_COUNTRY_MAP = {
    "Australia": "Australia",
    "China": "China",
    "Japan": "Japan",
    "Bahrain": "Bahrain",
    "Saudi Arabia": "Saudi Arabia",
    "Miami": "United States",
    "Emilia Romagna": "Italy",
    "Monaco": "Monaco",
    "Barcelona": "Spain",
    "Canada": "Canada",
    "Austria": "Austria",
    "Great Britain": "United Kingdom",
    "UK": "United Kingdom",
    "Belgium": "Belgium",
    "Hungary": "Hungary",
    "Netherlands": "Netherlands",
    "Monza": "Italy",
    "Madrid": "Spain",
    "Azerbaijan": "Azerbaijan",
    "Singapore": "Singapore",
    "Austin": "United States",
    "Mexico": "Mexico",
    "Brazil": "Brazil",
    "Las Vegas": "United States",
    "Qatar": "Qatar",
    "Abu Dhabi": "United Arab Emirates",
}

MAX_RETRIES = 6
RETRY_DELAY = 20

_plex_connection = None

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_logger():
    return logging.getLogger("f1_plex_tagger")


def host_to_plex_path(host_path):
    return host_path.replace('/mnt/user/data', '/data')


def get_plex_connection():
    global _plex_connection
    if _plex_connection is None:
        logger = get_logger()
        logger.info(f"Connecting to Plex at {PLEX_URL}...")
        try:
            _plex_connection = PlexServer(PLEX_URL, PLEX_TOKEN)
            logger.info(f"  ✅ Connected to: {_plex_connection.friendlyName}")
        except Exception as e:
            logger.error(f"❌ Failed to connect to Plex: {e}")
            raise
    return _plex_connection


def load_season_config(year):
    """Load season config using shared calendar loader."""
    return load_calendar(year)


def get_round_info(season_config, round_num):
    """Get round info using shared function."""
    return get_round_by_number(season_config, round_num)


def get_country_from_venue(venue):
    """Get country from venue name."""
    return VENUE_COUNTRY_MAP.get(venue, None)


def trigger_library_scan(plex, library_name):
    logger = get_logger()
    logger.info(f"Triggering Plex library scan for '{library_name}'...")
    try:
        plex.library.section(library_name).update()
        logger.info("  Scan triggered")
        return True
    except Exception as e:
        logger.error(f"  Failed to trigger scan: {e}")
        return False


def find_plex_item_by_path(plex, library_name, file_path, retries=MAX_RETRIES):
    logger = get_logger()
    library = plex.library.section(library_name)
    
    for attempt in range(retries):
        for video in library.all():
            for media in video.media:
                for part in media.parts:
                    if part.file == file_path:
                        return video
        
        if attempt < retries - 1:
            logger.info(f"  File not in Plex yet, waiting {RETRY_DELAY}s... ({attempt + 1}/{retries})")
            time.sleep(RETRY_DELAY)
            if attempt == 0:
                trigger_library_scan(plex, library_name)
    
    return None


def apply_tags_and_collection(video, year, round_num, session_type, round_info, dry_run=False):
    """Apply tags, collection, genre, and country to an F1 video."""
    logger = get_logger()
    
    session_name = session_type.display_name
    venue = round_info.get('venue', f'Round {round_num}')
    collection_name = f"{year} Round {round_num:02d} {venue}"
    
    # Build session tag
    session_tag = session_name
    
    # Determine genres
    genres_to_add = []
    
    # Season genre: "Formula 1 2025"
    genres_to_add.append(f"Formula 1 {year}")
    
    # Feature race genre (only for Race sessions)
    if session_type == FEATURE_RACE_SESSION:
        genres_to_add.append("F1 - Feature Race")
    
    # Country from venue
    country = get_country_from_venue(venue)
    
    # Get current metadata
    current_labels = [label.tag for label in video.labels]
    current_collections = [col.tag for col in video.collections]
    
    try:
        current_genres = [genre.tag for genre in video.genres]
    except:
        current_genres = []
    
    try:
        current_countries = [c.tag for c in video.countries]
    except:
        current_countries = []
    
    changes_made = []
    
    # Add session tag
    if session_tag not in current_labels:
        if not dry_run:
            video.addLabel(session_tag)
        changes_made.append(f"Tag: {session_tag}")
    
    # Add collection
    if collection_name not in current_collections:
        if not dry_run:
            video.addCollection(collection_name)
        changes_made.append(f"Collection: {collection_name}")
    
    # Add genres
    for genre in genres_to_add:
        if genre not in current_genres:
            if not dry_run:
                try:
                    video.addGenre(genre)
                    changes_made.append(f"Genre: {genre}")
                except Exception as e:
                    logger.warning(f"  Could not add genre '{genre}': {e}")
    
    # Add country
    if country and country not in current_countries:
        if not dry_run:
            try:
                video.addCountry(country)
                changes_made.append(f"Country: {country}")
            except Exception as e:
                logger.warning(f"  Could not add country '{country}': {e}")
    
    if changes_made:
        logger.info(f"  {'[DRY-RUN] ' if dry_run else ''}Applied: {', '.join(changes_made)}")
    else:
        logger.info(f"  ℹ️  All metadata already present")
    
    return changes_made


# =============================================================================
# PUBLIC API
# =============================================================================

def tag_single_file(file_path, dry_run=False, wait_for_plex=True):
    """Tag a single F1 file in Plex."""
    logger = get_logger()
    logger.info(f"=== F1 Plex Tagger: Processing ===")
    logger.info(f"  Path: {file_path}")
    
    filename = os.path.basename(file_path)
    
    # Parse prefix using shared function
    parsed = parse_prefix(filename)
    if not parsed:
        logger.warning(f"  ⚠️  No valid prefix in filename: {filename}")
        return False
    
    year, round_num, session_type = parsed
    
    logger.info(f"  Parsed: {year} R{round_num:02d} E{int(session_type):02d}")
    logger.info(f"  Session: {session_type.display_name}")
    
    # Load season config using shared function
    season_config = load_season_config(year)
    if not season_config:
        logger.error(f"  ❌ No season config for {year}")
        return False
    
    # Get round info using shared function
    round_info = get_round_info(season_config, round_num)
    if not round_info:
        logger.error(f"  ❌ No round info for Round {round_num}")
        return False
    
    venue = round_info.get('venue', '')
    country = get_country_from_venue(venue)
    
    logger.info(f"  Venue: {venue}")
    if country:
        logger.info(f"  Country: {country}")
    
    try:
        plex = get_plex_connection()
    except Exception as e:
        logger.error(f"  ❌ Plex connection failed: {e}")
        return False
    
    trigger_library_scan(plex, PLEX_LIBRARY)
    
    if wait_for_plex:
        logger.info(f"  Waiting for Plex to index...")
        time.sleep(5)
    
    retries = MAX_RETRIES if wait_for_plex else 1

    plex_path = translate_path_for_plex(file_path)
    logger.info(f"  Plex path: {plex_path}")
    video = find_plex_item_by_path(plex, PLEX_LIBRARY, plex_path, retries=retries)

    if not video:
        logger.error(f"  ❌ File not found in Plex")
        return False
    
    logger.info(f"  ✅ Found in Plex: {video.title}")
    
    changes = apply_tags_and_collection(
        video, year, round_num, session_type, round_info, dry_run
    )
    
    logger.info(f"=== F1 Plex Tagger: Complete ===")
    return True


def tag_from_host_path(host_path, dry_run=False, wait_for_plex=True):
    """Tag a file using host path (converts to container path)."""
    container_path = host_to_plex_path(host_path)
    return tag_single_file(container_path, dry_run, wait_for_plex)


# =============================================================================
# FULL LIBRARY SCAN
# =============================================================================

def build_plex_index(plex, library_name):
    """Build an index of all files in the Plex library."""
    logger = get_logger()
    logger.info(f"Building Plex library index for '{library_name}'...")
    index = {}
    
    library = plex.library.section(library_name)
    for video in library.all():
        for media in video.media:
            for part in media.parts:
                if part.file:
                    index[part.file] = video
    
    logger.info(f"  Indexed {len(index)} files")
    return index


def scan_full_library(dry_run=False):
    """Scan and tag all F1 content in the library."""
    logger = get_logger()
    
    plex = get_plex_connection()
    plex_index = build_plex_index(plex, PLEX_LIBRARY)
    
    stats = {
        'scanned': 0, 'matched': 0, 'updated': 0,
        'skipped': 0, 'errors': 0, 'no_prefix': 0
    }
    
    season_configs = {}
    
    logger.info(f"\nScanning for F1 content...")
    logger.info(f"Base path: {F1_BASE_PATH}")
    logger.info(f"Dry run: {dry_run}")
    logger.info("=" * 70)
    
    for file_path, video in plex_index.items():
        # Only process F1 files
        if not file_path.startswith(F1_BASE_PATH):
            continue
        
        stats['scanned'] += 1
        filename = os.path.basename(file_path)
        
        # Parse prefix using shared function
        parsed = parse_prefix(filename)
        if not parsed:
            stats['no_prefix'] += 1
            continue
        
        year, round_num, session_type = parsed
        stats['matched'] += 1
        
        # Cache season configs
        if year not in season_configs:
            season_configs[year] = load_season_config(year)
        
        round_info = get_round_info(season_configs[year], round_num)
        if not round_info:
            logger.warning(f"  ⚠️  No round info for {year} R{round_num}")
            stats['errors'] += 1
            continue
        
        try:
            changes = apply_tags_and_collection(
                video, year, round_num, session_type, round_info, dry_run
            )
            
            if changes:
                stats['updated'] += 1
                venue = round_info.get('venue', '')
                logger.info(f"{'[DRY-RUN] ' if dry_run else ''}✅ {filename}")
                logger.info(f"    → F1 {year} R{round_num:02d} {venue} | {session_type.display_name}")
            else:
                stats['skipped'] += 1
                
        except Exception as e:
            logger.error(f"  ❌ Error processing {filename}: {e}")
            stats['errors'] += 1
    
    logger.info("\n" + "=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)
    logger.info(f"  Scanned:    {stats['scanned']}")
    logger.info(f"  Matched:    {stats['matched']}")
    logger.info(f"  Updated:    {stats['updated']}")
    logger.info(f"  Skipped:    {stats['skipped']}")
    logger.info(f"  No prefix:  {stats['no_prefix']}")
    logger.info(f"  Errors:     {stats['errors']}")
    
    if dry_run:
        logger.info("\n⚠️  DRY RUN - No changes made")
    
    return stats


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='F1 Plex Tagger')
    parser.add_argument('file_path', nargs='?', help='Single file (container path)')
    parser.add_argument('--host-path', help='Single file (host path)')
    parser.add_argument('--dry-run', action='store_true', help='Preview only')
    parser.add_argument('--list-sessions', '-l', action='store_true', 
                        help='List all session types')
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    logger = get_logger()
    logger.info("=" * 70)
    logger.info("F1 PLEX TAGGER")
    logger.info("=" * 70)
    
    # List sessions option
    if args.list_sessions:
        print("\nF1 Session Types:")
        print("=" * 50)
        for session in SessionType:
            if session != SessionType.UNKNOWN:
                print(f"  E{int(session):02d}. {session.display_name}")
        print(f"  E{int(SessionType.UNKNOWN):02d}. {SessionType.UNKNOWN.display_name}")
        return
    
    if args.host_path:
        success = tag_from_host_path(args.host_path, args.dry_run)
        sys.exit(0 if success else 1)
    elif args.file_path:
        success = tag_single_file(args.file_path, args.dry_run)
        sys.exit(0 if success else 1)
    else:
        scan_full_library(args.dry_run)
    
    logger.info("\n✅ Done")


if __name__ == "__main__":
    main()