#!/data/pythonscripts/venv/bin/python3
"""
F1 Discord Notifications
=========================

Sends Discord webhook notifications when F1 content is added to Plex.

Can be used standalone or imported by other scripts.

Usage:
    # Test notification
    python3 f1_discord.py --test
    
    # Send notification for specific session
    python3 f1_discord.py --notify "Race" --venue "Monaco" --round 8 --season 2025
"""

import os
import sys
import json
import logging
import argparse
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Optional, List

# Add script directory to path
SCRIPT_DIR = Path(__file__).parent.resolve()
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR))

from utils import load_env, get_env

# Load environment variables
load_env()

# =============================================================================
# CONFIGURATION
# =============================================================================

# Discord Webhook URL - Set in .env file
DISCORD_WEBHOOK_URL = get_env("DISCORD_F1_WEBHOOK", default="")

# =============================================================================
# NOTIFICATION SETTINGS
# =============================================================================

CONFIG = {
    # Enable/disable notifications
    "enabled": True,
    
    # Which sessions trigger notifications (use display names)
    "notify_sessions": [
        "Sprint Qualifying",
        "Sprint",
        "Qualifying",
        "Race",
    ],
    
    # Notification appearance
    "bot_name": "F1 Notifier",
    "bot_avatar": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/33/F1.svg/1200px-F1.svg.png",
    
    # Embed color (F1 Red)
    "color": 15158332,  # #E70000
    
    # Session emojis
    "emojis": {
        "Race": "🏁",
        "Sprint": "⚡",
        "Qualifying": "⏱️",
        "Sprint Qualifying": "⏱️",
        "Practice": "🔧",
        "default": "🏎️",
    },
    
    # Message templates
    "templates": {
        "race": {
            "title": "{emoji} {session_name} Available!",
            "description": "**Round {round}** - {venue}\n\nThe {session_name} is now available to watch on Plex!",
        },
        "sprint": {
            "title": "{emoji} {session_name} Available!",
            "description": "**Round {round}** - {venue}\n\nThe {session_name} is now available to watch on Plex!",
        },
        "qualifying": {
            "title": "{emoji} {session_name} Available!",
            "description": "**Round {round}** - {venue}\n\n{session_name} is ready to watch on Plex!",
        },
        "default": {
            "title": "{emoji} {session_name} Available!",
            "description": "**Round {round}** - {venue}\n\n{session_name} is now on Plex!",
        },
    },
    
    # Rate limiting
    "rate_limit": {
        "enabled": True,
        "max_per_hour": 10,
    },
}

# =============================================================================
# RATE LIMITING
# =============================================================================

RATE_LIMIT_FILE = SCRIPT_DIR / "config" / "f1_discord_rate_limit.json"


def check_rate_limit() -> bool:
    """Check if we're within rate limits. Returns True if OK to send."""
    if not CONFIG["rate_limit"]["enabled"]:
        return True
    
    max_per_hour = CONFIG["rate_limit"]["max_per_hour"]
    now = datetime.now()
    
    timestamps = []
    if RATE_LIMIT_FILE.exists():
        try:
            with open(RATE_LIMIT_FILE, 'r') as f:
                data = json.load(f)
                timestamps = [datetime.fromisoformat(ts) for ts in data.get("timestamps", [])]
        except Exception:
            timestamps = []
    
    from datetime import timedelta
    one_hour_ago = now - timedelta(hours=1)
    recent = [ts for ts in timestamps if ts > one_hour_ago]
    
    if len(recent) >= max_per_hour:
        logging.warning(f"Rate limit reached: {len(recent)}/{max_per_hour} notifications in last hour")
        return False
    
    return True


def record_notification():
    """Record that a notification was sent."""
    if not CONFIG["rate_limit"]["enabled"]:
        return
    
    now = datetime.now()
    
    timestamps = []
    if RATE_LIMIT_FILE.exists():
        try:
            with open(RATE_LIMIT_FILE, 'r') as f:
                data = json.load(f)
                timestamps = data.get("timestamps", [])
        except Exception:
            timestamps = []
    
    timestamps.append(now.isoformat())
    timestamps = timestamps[-100:]
    
    RATE_LIMIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RATE_LIMIT_FILE, 'w') as f:
        json.dump({"timestamps": timestamps}, f)

# =============================================================================
# DISCORD WEBHOOK
# =============================================================================

def should_notify(session_name: str) -> bool:
    """Check if this session type should trigger a notification."""
    if not CONFIG["enabled"]:
        return False
    
    if not DISCORD_WEBHOOK_URL:
        logging.debug("Discord webhook URL not configured")
        return False
    
    if session_name not in CONFIG["notify_sessions"]:
        logging.debug(f"Session '{session_name}' not in notification list")
        return False
    
    if not check_rate_limit():
        return False
    
    return True


def get_session_template(session_name: str) -> dict:
    """Get the appropriate message template for a session."""
    session_lower = session_name.lower()
    
    if session_lower == "race":
        return CONFIG["templates"]["race"]
    elif "sprint" in session_lower and "qualifying" not in session_lower:
        return CONFIG["templates"]["sprint"]
    elif "qualifying" in session_lower:
        return CONFIG["templates"]["qualifying"]
    else:
        return CONFIG["templates"]["default"]


def get_session_emoji(session_name: str) -> str:
    """Get emoji for session type."""
    return CONFIG["emojis"].get(session_name, CONFIG["emojis"]["default"])


def send_discord_notification(
    session_name: str,
    venue: str,
    round_num: int,
    season: int,
    extra_fields: List[dict] = None,
    dry_run: bool = False
) -> bool:
    """Send a Discord webhook notification."""
    if not DISCORD_WEBHOOK_URL:
        logging.warning("Discord webhook URL not configured")
        return False
    
    template = get_session_template(session_name)
    emoji = get_session_emoji(session_name)
    
    title = template["title"].format(
        emoji=emoji,
        session_name=session_name,
        venue=venue,
        round=round_num,
        season=season
    )
    
    description = template["description"].format(
        emoji=emoji,
        session_name=session_name,
        venue=venue,
        round=round_num,
        season=season
    )
    
    embed = {
        "title": title,
        "description": description,
        "color": CONFIG["color"],
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "footer": {
            "text": f"Formula 1 {season} • Round {round_num}"
        },
        "fields": [
            {"name": "📍 Venue", "value": venue, "inline": True},
            {"name": "🔢 Round", "value": str(round_num), "inline": True},
            {"name": "📅 Season", "value": str(season), "inline": True},
        ],
    }
    
    if extra_fields:
        embed["fields"].extend(extra_fields)
    
    payload = {
        "username": CONFIG["bot_name"],
        "avatar_url": CONFIG["bot_avatar"],
        "embeds": [embed]
    }
    
    if dry_run:
        logging.info(f"[DRY-RUN] Would send Discord notification:")
        logging.info(f"  Title: {title}")
        logging.info(f"  Description: {description[:100]}...")
        return True
    
    try:
        data = json.dumps(payload).encode('utf-8')
        
        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=data,
            method='POST'
        )
        req.add_header('Content-Type', 'application/json')
        req.add_header('User-Agent', 'F1-Notifier/1.0')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status in [200, 204]:
                logging.info(f"✅ Discord notification sent: {session_name}")
                record_notification()
                return True
            else:
                logging.error(f"Discord webhook returned status {response.status}")
                return False
                
    except urllib.error.HTTPError as e:
        logging.error(f"Discord webhook HTTP error: {e.code} - {e.reason}")
        return False
    except urllib.error.URLError as e:
        logging.error(f"Discord webhook URL error: {e.reason}")
        return False
    except Exception as e:
        logging.error(f"Discord webhook error: {e}")
        return False


def notify_session_added(
    session_name: str,
    venue: str,
    round_num: int,
    season: int,
    dry_run: bool = False
) -> bool:
    """Main entry point for notifications. Checks if notification should be sent."""
    if not should_notify(session_name):
        return False
    
    return send_discord_notification(
        session_name=session_name,
        venue=venue,
        round_num=round_num,
        season=season,
        dry_run=dry_run
    )

# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="F1 Discord Notifications",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Test notification
    python3 f1_discord.py --test
    
    # Send notification
    python3 f1_discord.py --notify "Race" --venue "Monaco" --round 8 --season 2025
    
    # List configured sessions
    python3 f1_discord.py --list-sessions
        """
    )
    
    parser.add_argument('--test', action='store_true', help='Send a test notification')
    parser.add_argument('--notify', type=str, metavar='SESSION', help='Session name to notify')
    parser.add_argument('--venue', type=str, help='Venue name')
    parser.add_argument('--round', type=int, help='Round number')
    parser.add_argument('--season', type=int, default=2025, help='Season year')
    parser.add_argument('--list-sessions', action='store_true', help='List sessions configured for notifications')
    parser.add_argument('--dry-run', action='store_true', help='Preview without sending')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    args = parser.parse_args()
    
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    
    if not DISCORD_WEBHOOK_URL and not args.list_sessions:
        print("❌ Discord webhook URL not configured!")
        print("")
        print("Add to your .env file:")
        print("  DISCORD_F1_WEBHOOK=https://discord.com/api/webhooks/YOUR_WEBHOOK_URL")
        return 1
    
    if args.list_sessions:
        print("\n📋 Sessions configured for notifications:\n")
        for session in CONFIG["notify_sessions"]:
            emoji = get_session_emoji(session)
            print(f"  {emoji} {session}")
        print("")
        return 0
    
    if args.test:
        print("\n🧪 Sending test notification...\n")
        
        success = send_discord_notification(
            session_name="Race",
            venue="Test Circuit",
            round_num=99,
            season=2025,
            extra_fields=[{
                "name": "🧪 Test",
                "value": "This is a test notification",
                "inline": False
            }],
            dry_run=args.dry_run
        )
        
        if success:
            print("✅ Test notification sent successfully!")
        else:
            print("❌ Failed to send test notification")
        
        return 0 if success else 1
    
    if args.notify:
        if not args.venue:
            print("❌ --venue required with --notify")
            return 1
        if not args.round:
            print("❌ --round required with --notify")
            return 1
        
        print(f"\n📤 Sending notification for: {args.notify}\n")
        
        success = send_discord_notification(
            session_name=args.notify,
            venue=args.venue,
            round_num=args.round,
            season=args.season,
            dry_run=args.dry_run
        )
        
        return 0 if success else 1
    
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())