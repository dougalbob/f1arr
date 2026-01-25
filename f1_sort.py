#!/usr/bin/env python3
"""
F1 Sort - Standalone File Organization and Artwork
===================================================

Sorts F1 downloads into the Plex library structure and applies artwork.

Environment Variables:
    F1_LIBRARY_PATH - Path to F1 media library
"""

import os
import sys
import shutil
import logging
import re
import time
import urllib.request
import argparse
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from utils import load_env, get_env
load_env()

try:
    from f1_discord import notify_session_added, should_notify
    DISCORD_AVAILABLE = True
except ImportError:
    DISCORD_AVAILABLE = False

from f1_common import (
    SessionType,
    SESSION_DISPLAY_NAMES,
    classify_session,
    load_calendar,
    get_round_by_number,
    identify_round,
    match_venue,
    build_prefix,
    parse_prefix,
    F1_LIBRARY_PATH,
    get_config_dir,
)

# =============================================================================
# CONFIGURATION
# =============================================================================

PLEX_URL = get_env("PLEX_URL", "http://192.168.1.241:32400")
PLEX_TOKEN = get_env("PLEX_TOKEN", required=False)
LIBRARY_ID = get_env("F1_PLEX_LIBRARY_ID", "2")

_plex_url_parsed = PLEX_URL.replace("http://", "").replace("https://", "")
if ":" in _plex_url_parsed:
    PLEX_IP, PLEX_PORT = _plex_url_parsed.split(":", 1)
else:
    PLEX_IP = _plex_url_parsed
    PLEX_PORT = "32400"

F1_BASE = F1_LIBRARY_PATH
F1_FANART_DIR = SCRIPT_DIR / "assets" / "f1_fanart"
F1_POSTERS_DIR = SCRIPT_DIR / "assets" / "f1_posters"
CONFIG_DIR = get_config_dir()

PLEX_TAGGER_AVAILABLE = False
tag_single_file = None

def init_plex_tagger():
    global PLEX_TAGGER_AVAILABLE, tag_single_file
    try:
        from f1_plex_tagger import tag_single_file as tagger_func
        tag_single_file = tagger_func
        PLEX_TAGGER_AVAILABLE = True
        logging.info("✅ Plex tagger module loaded")
    except ImportError as e:
        logging.warning(f"⚠️ Plex tagger not available: {e}")
        PLEX_TAGGER_AVAILABLE = False

# =============================================================================
# LOGGING
# =============================================================================

def setup_logging(dry_run: bool = False):
    log_dir = os.path.join(F1_BASE, "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = os.path.join(log_dir, f"f1_sort_{timestamp}.log")
    
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    logging.info(f"Log file: {log_file}")
    if dry_run:
        logging.info("DRY RUN MODE - no changes will be made")
    
    return log_file

TAG_PATTERN = re.compile(
    r"(?:1080p|720p|2160p|WEB[-]?DL|WEB|webrip|bluray|h264|x264|x265|H265|hevc|"
    r"hdr|dv|dd5[._]1|aac|dts|atmos|remux|nf|amzn|Sports|verum|Repack|Proper|PlayTV|RBB|"
    r"Multi[-]?MWR|English[-]?scmcgill1969|English[-]?MWR|TNT|Sky|SKYF1UHD|F1UHD|F1TV|"
    r"AHDTV|AHDTVDARKSPORT|DARKSPORT|HDTV|JFF|F1LIVE|RiP|Round|Formula1\.)",
    re.IGNORECASE
)

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def extract_year_from_text(text: str) -> Optional[int]:
    match = re.search(r"\b(20\d{2})\b", text)
    return int(match.group(1)) if match else None

def normalize_name(name: str) -> str:
    clean = name.lstrip(".")
    clean = clean.replace(".", " ")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean

def flatten_if_single_subdir(path: str):
    subdirs = [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]
    files = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
    if len(subdirs) == 1 and not files:
        subdir_path = os.path.join(path, subdirs[0])
        for name in os.listdir(subdir_path):
            src = os.path.join(subdir_path, name)
            dst = os.path.join(path, name)
            shutil.move(src, dst)
        os.rmdir(subdir_path)
        logging.info(f"Flattened nested subfolder")

def rename_files_to_match_folder(folder_path: str):
    folder_name = os.path.basename(folder_path)
    special_files = {'poster.jpg', 'fanart.jpg', 'thumb.jpg'}
    
    files = []
    for f in os.listdir(folder_path):
        file_path = os.path.join(folder_path, f)
        if os.path.isfile(file_path) and f.lower() not in special_files:
            files.append(f)
    
    if not files:
        return
    
    files.sort()
    
    temp_mappings = []
    for idx, fname in enumerate(files):
        old_path = os.path.join(folder_path, fname)
        _, ext = os.path.splitext(fname)
        temp_name = f"_temp_rename_{idx}{ext}"
        temp_path = os.path.join(folder_path, temp_name)
        
        if old_path != temp_path:
            os.rename(old_path, temp_path)
            temp_mappings.append((temp_path, ext, idx))
    
    for temp_path, ext, idx in temp_mappings:
        if len(temp_mappings) == 1:
            new_name = f"{folder_name}{ext}"
        else:
            new_name = f"{folder_name} - Part {idx + 1}{ext}"
        
        new_path = os.path.join(folder_path, new_name)
        
        if not os.path.exists(new_path):
            os.rename(temp_path, new_path)
            logging.info(f"Renamed: {new_name}")
        else:
            fallback_name = f"{folder_name}_{idx}{ext}"
            os.rename(temp_path, os.path.join(folder_path, fallback_name))

def delete_unwanted(base_dir: str, name: str):
    for root, dirs, files in os.walk(base_dir, topdown=False):
        for f in files:
            if f.lower().startswith(name.lower()):
                os.remove(os.path.join(root, f))
        for d in dirs:
            if d.lower().startswith(name.lower()):
                shutil.rmtree(os.path.join(root, d))

def trigger_plex_refresh():
    if not PLEX_TOKEN:
        logging.warning("No PLEX_TOKEN set, skipping refresh")
        return
    
    logging.info("Triggering Plex refresh...")
    url = f"http://{PLEX_IP}:{PLEX_PORT}/library/sections/{LIBRARY_ID}/refresh?X-Plex-Token={PLEX_TOKEN}"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            if response.status == 200:
                logging.info("✅ Plex refresh triggered")
            else:
                logging.warning(f"Plex refresh returned: {response.status}")
    except Exception as e:
        logging.error(f"Plex refresh error: {e}")

# =============================================================================
# OVERLAY FUNCTIONS
# =============================================================================

def overlay_text_on_image(
    image_path: str,
    session_text: str,
    round_info: str = None,
    venue: str = None,
    scale_factor: float = 1.2
) -> Optional[Image.Image]:
    img = None
    overlay = None
    
    try:
        img = Image.open(image_path)
        img.load()
        
        if img.mode in ('RGBA', 'P', 'LA'):
            img = img.convert('RGB')
        
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        is_portrait = img.height > img.width
        
        if is_portrait:
            header_font_size = int(img.width * 0.07 * scale_factor)
            session_font_size = int(img.width * 0.06 * scale_factor)
            wrap_width = 12
        else:
            header_font_size = int(img.width * 0.035 * scale_factor)
            session_font_size = int(img.width * 0.05 * scale_factor)
            wrap_width = 20
        
        header_font_size = max(header_font_size, 24)
        session_font_size = max(session_font_size, 20)
        
        font_paths = [
            str(SCRIPT_DIR / "assets" / "DejaVuSans-Bold.ttf"),
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        ]
        
        header_font = None
        session_font = None
        
        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    header_font = ImageFont.truetype(font_path, header_font_size)
                    session_font = ImageFont.truetype(font_path, session_font_size)
                    break
                except OSError:
                    continue
        
        if header_font is None:
            logging.error("No TrueType font found!")
            header_font = ImageFont.load_default()
            session_font = ImageFont.load_default()
        
        draw = ImageDraw.Draw(img)
        
        header_lines = []
        if round_info:
            header_lines.append(round_info)
        if venue:
            header_lines.append(venue)
        
        session_lines = textwrap.wrap(session_text, width=wrap_width)
        
        header_line_height = header_font.getbbox("A")[3]
        session_line_height = session_font.getbbox("A")[3]
        
        header_spacing = int(header_line_height * 0.3)
        session_spacing = int(session_line_height * 0.4)
        section_gap = int(session_line_height * 1.5)
        
        header_total = (header_line_height + header_spacing) * len(header_lines) if header_lines else 0
        session_total = (session_line_height + session_spacing) * len(session_lines)
        total_height = header_total + (section_gap if header_lines else 0) + session_total
        
        y_start = int(img.height * 0.5) - (total_height // 2)
        
        max_line_width = 0
        for line in header_lines:
            bbox = draw.textbbox((0, 0), line, font=header_font)
            max_line_width = max(max_line_width, bbox[2] - bbox[0])
        for line in session_lines:
            bbox = draw.textbbox((0, 0), line, font=session_font)
            max_line_width = max(max_line_width, bbox[2] - bbox[0])
        
        padding_x = int(img.width * 0.03 * scale_factor)
        padding_y = int(session_line_height * 0.4 * scale_factor)
        
        bg_x1 = max(0, (img.width - max_line_width) // 2 - padding_x)
        bg_y1 = max(0, y_start - padding_y)
        bg_x2 = min(img.width, (img.width + max_line_width) // 2 + padding_x)
        bg_y2 = min(img.height, y_start + total_height + padding_y)
        
        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2], fill=(0, 0, 0, 179))
        
        img = Image.alpha_composite(img, overlay)
        overlay.close()
        
        draw = ImageDraw.Draw(img)
        stroke_width = max(1, int(2 * scale_factor))
        
        current_y = y_start
        
        for line in header_lines:
            bbox = draw.textbbox((0, 0), line, font=header_font)
            x = (img.width - (bbox[2] - bbox[0])) // 2
            draw.text((x, current_y), line, font=header_font, fill="white",
                      stroke_width=stroke_width, stroke_fill="black")
            current_y += header_line_height + header_spacing
        
        if header_lines:
            current_y += section_gap - header_spacing
        
        for line in session_lines:
            bbox = draw.textbbox((0, 0), line, font=session_font)
            x = (img.width - (bbox[2] - bbox[0])) // 2
            draw.text((x, current_y), line, font=session_font, fill="white",
                      stroke_width=stroke_width, stroke_fill="black")
            current_y += session_line_height + session_spacing
        
        result = img.convert('RGB')
        img.close()
        return result
    
    except Exception as e:
        logging.error(f"Overlay error: {e}")
        if img:
            img.close()
        return None

# =============================================================================
# ASSET PROCESSING
# =============================================================================

def copy_and_resize_images(final_path: str, round_info: dict, stats: dict):
    fanart_file = round_info.get("fanart")
    poster_file = round_info.get("poster")
    
    if fanart_file:
        src = F1_FANART_DIR / fanart_file
        if src.exists():
            dest = os.path.join(final_path, "fanart.jpg")
            shutil.copy2(src, dest)
            
            img = Image.open(dest)
            if img.mode in ('RGBA', 'P', 'LA'):
                img = img.convert('RGB')
            if img.size != (1920, 1080):
                img = img.resize((1920, 1080), Image.LANCZOS)
            img.save(dest, "JPEG", quality=95)
            img.close()
            stats["fanart_resized"] = stats.get("fanart_resized", 0) + 1
    
    if poster_file:
        src = F1_POSTERS_DIR / poster_file
        if src.exists():
            dest = os.path.join(final_path, "poster.jpg")
            shutil.copy2(src, dest)
            
            img = Image.open(dest)
            if img.mode in ('RGBA', 'P', 'LA'):
                img = img.convert('RGB')
            if img.size != (1000, 1500):
                img = img.resize((1000, 1500), Image.LANCZOS)
            img.save(dest, "JPEG", quality=95)
            img.close()
            stats["posters_resized"] = stats.get("posters_resized", 0) + 1

def apply_text_overlays(final_path: str, round_info: dict, session_name: str):
    round_num = round_info.get("round", 0)
    venue = round_info.get("venue", "")
    round_text = f"Round {round_num}" if round_num else None
    
    fanart_path = os.path.join(final_path, "fanart.jpg")
    if os.path.isfile(fanart_path):
        result = overlay_text_on_image(fanart_path, session_name, round_text, venue)
        if result:
            result.save(fanart_path, "JPEG", quality=95)
            result.close()
            logging.info(f"✅ Fanart overlay applied")
    
    poster_path = os.path.join(final_path, "poster.jpg")
    if os.path.isfile(poster_path):
        result = overlay_text_on_image(poster_path, session_name, round_text, venue)
        if result:
            result.save(poster_path, "JPEG", quality=95)
            result.close()
            logging.info(f"✅ Poster overlay applied")

def generate_thumb(final_path: str):
    fanart = os.path.join(final_path, "fanart.jpg")
    thumb = os.path.join(final_path, "thumb.jpg")
    if os.path.exists(fanart):
        shutil.copy2(fanart, thumb)
        logging.info("✅ Thumb created")

def apply_assets(final_path: str, round_info: dict, session_name: str, dry_run: bool = False):
    if dry_run:
        logging.info("[DRY RUN] Would apply artwork")
        return
    
    stats = {}
    copy_and_resize_images(final_path, round_info, stats)
    apply_text_overlays(final_path, round_info, session_name)
    generate_thumb(final_path)

def apply_plex_tags(final_path: str, dry_run: bool = False):
    if not PLEX_TAGGER_AVAILABLE:
        return
    
    media_file = None
    for f in os.listdir(final_path):
        if f.lower().endswith(('.mkv', '.mp4', '.avi')):
            media_file = os.path.join(final_path, f)
            break
    
    if media_file:
        try:
            tag_single_file(media_file, dry_run=dry_run, wait_for_plex=True)
        except Exception as e:
            logging.error(f"Plex tagging error: {e}")

# =============================================================================
# MAIN PROCESSING
# =============================================================================

def process_folder(source_path: str, dry_run: bool = False) -> bool:
    folder_name = os.path.basename(source_path)
    
    logging.info(f"Processing: {folder_name}")
    
    if not dry_run:
        delete_unwanted(source_path, "sample")
        delete_unwanted(source_path, "screens")
    
    parsed = parse_prefix(folder_name)
    
    if parsed:
        year, round_num, session_type = parsed
        logging.info(f"📦 Prefix: {build_prefix(year, round_num, session_type)}")
    else:
        session_type = classify_session(folder_name)
        year = extract_year_from_text(folder_name)
        
        if not year:
            logging.error("Could not determine year")
            return False
        
        if session_type == SessionType.UNKNOWN:
            logging.error("Could not classify session")
            return False
        
        logging.info(f"🔍 Classified: {session_type.display_name}")
    
    calendar = load_calendar(year)
    if not calendar:
        logging.error(f"No calendar for {year}")
        return False
    
    if parsed:
        round_info = get_round_by_number(calendar, round_num)
    else:
        round_info = identify_round(folder_name, calendar)
        if round_info:
            round_num = round_info.get("round", 0)
    
    if not round_info:
        logging.error("Could not identify round")
        return False
    
    venue = round_info.get("venue", "Unknown")
    logging.info(f"   Round {round_num}: {venue}")
    
    prefix = build_prefix(year, round_num, session_type)
    session_name = session_type.display_name
    dest_folder = f"{prefix} {venue} {session_name}"
    
    round_folder = round_info.get("folder", f"Round {round_num} {venue}")
    dest_parent = os.path.join(F1_BASE, str(year), round_folder)
    final_path = os.path.join(dest_parent, dest_folder)
    
    logging.info(f"   Destination: {final_path}")
    
    if dry_run:
        logging.info("[DRY RUN] Would move and process")
        return True
    
    os.makedirs(dest_parent, exist_ok=True)
    
    if source_path != final_path:
        if os.path.exists(final_path):
            logging.warning(f"Destination exists, merging...")
            for item in os.listdir(source_path):
                src = os.path.join(source_path, item)
                dst = os.path.join(final_path, item)
                if not os.path.exists(dst):
                    shutil.move(src, dst)
            shutil.rmtree(source_path)
        else:
            shutil.move(source_path, final_path)
    
    flatten_if_single_subdir(final_path)
    rename_files_to_match_folder(final_path)
    apply_assets(final_path, round_info, session_name)
    apply_plex_tags(final_path)
    
    if DISCORD_AVAILABLE and should_notify(session_name):
        try:
            notify_session_added(
                session_name=session_name,
                venue=venue,
                round_num=round_num,
                season=year,
                dry_run=dry_run
            )
        except Exception as e:
            logging.warning(f"Discord notification failed: {e}")
    
    time.sleep(2)
    trigger_plex_refresh()
    
    logging.info("✅ Processing complete")
    return True

def main():
    parser = argparse.ArgumentParser(description="F1 Sort - Standalone Sorter")
    parser.add_argument("--source", "-s", required=True, help="Source folder to process")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Preview without changes")
    args = parser.parse_args()
    
    setup_logging(args.dry_run)
    init_plex_tagger()
    
    source = args.source.rstrip("/")
    
    if not os.path.isdir(source):
        logging.error(f"Source not found: {source}")
        return 1
    
    success = process_folder(source, dry_run=args.dry_run)
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
