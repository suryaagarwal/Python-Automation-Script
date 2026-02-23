import requests
import os
import time
import re
import json
import logging
import sqlite3
import hashlib
import datetime
import configparser
from pathlib import Path
from dotenv import load_dotenv
from tqdm import tqdm
from tenacity import retry, stop_after_attempt, wait_exponential

# ==============================
# CONFIGURATION & SETUP
# ==============================

# Load environment variables
load_dotenv()

# Load config file
config = configparser.ConfigParser()
config_file = "config.ini"
if not os.path.exists(config_file):
    # Create default config
    config['Settings'] = {
        'api_key': 'Your Fireflies API Key',
        'base_url': 'https://api.fireflies.ai/graphql',
        'download_folder': r'D:\Fireflies Backup',
        'batch_size': '50',
        'rate_limit_delay': '0.3',
        'enable_notifications': 'false',
        'notification_email': 'your-email@example.com'
    }
    with open(config_file, 'w') as f:
        config.write(f)
    print(f"Created {config_file}. Please configure it and run again.")
    exit(1)

config.read(config_file)

API_KEY = os.getenv("FIREFLIES_API_KEY") or config.get("Settings", "api_key")
BASE_URL = config.get("Settings", "base_url")
DOWNLOAD_FOLDER = config.get("Settings", "download_folder")
BATCH_SIZE = config.getint("Settings", "batch_size")
RATE_LIMIT_DELAY = config.getfloat("Settings", "rate_limit_delay")
ENABLE_NOTIFICATIONS = config.getboolean("Settings", "enable_notifications")
NOTIFICATION_EMAIL = config.get("Settings", "notification_email")

if API_KEY == "Your Fireflies API Key":
    raise ValueError("Please set FIREFLIES_API_KEY environment variable or update config.ini")

# Create directories
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
LOG_FOLDER = os.path.join(DOWNLOAD_FOLDER, "logs")
os.makedirs(LOG_FOLDER, exist_ok=True)

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# ==============================
# LOGGING SETUP
# ==============================\nlog_file = os.path.join(LOG_FOLDER, f"backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.info("=" * 50)
logger.info("Fireflies Backup Script Started")
logger.info("=" * 50)

# ==============================
# DATABASE SETUP
# ==============================\ndef create_database():
    """Create SQLite database for tracking backups"""
    db_file = os.path.join(DOWNLOAD_FOLDER, "backup.db")
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meetings (
            meeting_id TEXT PRIMARY KEY,
            title TEXT,
            date TEXT,
            downloaded_at TEXT,
            local_folder TEXT,
            status TEXT
        )
    """)
    
cursor.execute("""
        CREATE TABLE IF NOT EXISTS backup_sessions (
            session_id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT,
            end_time TEXT,
            total_downloaded INTEGER,
            total_skipped INTEGER,
            total_failed INTEGER
        )
    """)
    
    conn.commit()
    return conn

# ==============================
# HELPER FUNCTIONS
# ==============================\ndef clean_filename(name):
    """Remove invalid characters from filename"""
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = name.encode('ascii', 'ignore').decode()
    return name.strip()

def calculate_file_hash(filepath):
    """Calculate MD5 hash of a file"""
    try:
        md5_hash = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                md5_hash.update(chunk)
        return md5_hash.hexdigest()
    except Exception as e:
        logger.error(f"Error calculating hash for {filepath}: {str(e)}")
        return None

def validate_file(filepath):
    """Validate downloaded file"""
    if not os.path.exists(filepath):
        return False
    if os.path.getsize(filepath) == 0:
        return False
    return True

def load_checkpoint():
    """Load checkpoint to resume interrupted backup"""
    checkpoint_file = os.path.join(DOWNLOAD_FOLDER, "checkpoint.json")
    if os.path.exists(checkpoint_file):
        try:
            with open(checkpoint_file, "r") as f:
                checkpoint = json.load(f)
            logger.info(f"Checkpoint found. Resuming from skip={checkpoint['skip']}")
            return checkpoint
        except Exception as e:
            logger.error(f"Error loading checkpoint: {str(e)}")
    return {"skip": 0}

def save_checkpoint(skip, total_downloaded, total_skipped):
    """Save checkpoint for resuming"""
    checkpoint_file = os.path.join(DOWNLOAD_FOLDER, "checkpoint.json")
    checkpoint = {
        "skip": skip,
        "total_downloaded": total_downloaded,
        "total_skipped": total_skipped,
        "timestamp": datetime.datetime.now().isoformat()
    }
    try:
        with open(checkpoint_file, "w") as f:
            json.dump(checkpoint, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving checkpoint: {str(e)}")

def save_metadata(meeting_folder, meeting_data):
    """Save meeting metadata as JSON"""
    metadata = {
        "meeting_id": meeting_data["id"],
        "title": meeting_data["title"],
        "date": meeting_data["dateString"],
        "downloaded_at": datetime.datetime.now().isoformat(),
        "audio_url": meeting_data["audio_url"],
        "has_summary": bool(meeting_data["summary"]),
        "has_transcript": bool(meeting_data["sentences"])
    }
    try:
        with open(os.path.join(meeting_folder, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
        logger.info(f"Metadata saved for meeting: {meeting_data['title']}")
    except Exception as e:
        logger.error(f"Error saving metadata: {str(e)}")

def log_to_database(conn, meeting_id, title, date, local_folder, status):
    """Log meeting to database"""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO meetings 
            (meeting_id, title, date, downloaded_at, local_folder, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (meeting_id, title, date, datetime.datetime.now().isoformat(), local_folder, status))
        conn.commit()
    except Exception as e:
        logger.error(f"Error logging to database: {str(e)}")

def send_notification(subject, message):
    """Send email notification (requires SMTP configuration)"""
    if not ENABLE_NOTIFICATIONS:
        return
    
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        # Configure SMTP (Gmail example)
        smtp_server = "smtp.gmail.com"
        smtp_port = 587
        sender_email = os.getenv("EMAIL_ADDRESS")
        sender_password = os.getenv("EMAIL_PASSWORD")
        
        if not sender_email or not sender_password:
            logger.warning("Email credentials not found in environment variables")
            return
        
        msg = MIMEMultipart()
        msg["From"] = sender_email
        msg["To"] = NOTIFICATION_EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(message, "plain"))
        
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
        
        logger.info(f"Notification sent to {NOTIFICATION_EMAIL}")
    except Exception as e:
        logger.error(f"Error sending notification: {str(e)}")

# ==============================
# API CALLS WITH RETRY LOGIC
# ==============================\n@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_transcripts(limit=50, skip=0):
    """Fetch transcripts with retry logic"""
    query = """
    query($limit: Int, $skip: Int) {
      transcripts(limit: $limit, skip: $skip) {
        id
        title
        dateString
        audio_url
        summary {
          overview
        }
        sentences {
          text
        }
      }
    }
    """

    variables = {
        "limit": limit,
        "skip": skip
    }

    try:
        response = requests.post(
            BASE_URL,
            json={"query": query, "variables": variables},
            headers=headers,
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"API request failed: {str(e)}")
        raise

# ==============================
# MAIN PROCESS
# ==============================\ndef main():
    """Main backup process"""
    start_time = datetime.datetime.now()
    logger.info(f"Backup started at {start_time}")
    
    # Initialize database
    conn = create_database()
    
    # Load checkpoint
    checkpoint = load_checkpoint()
    skip = checkpoint.get("skip", 0)
    total_downloaded = checkpoint.get("total_downloaded", 0)
    total_skipped = checkpoint.get("total_skipped", 0)
    total_failed = 0
    
    batch_count = 0
    
    try:
        while True:
            batch_count += 1
            logger.info(f"Fetching batch {batch_count} (skip={skip})")
            
            data = fetch_transcripts(BATCH_SIZE, skip)

            if "errors" in data:
                logger.error(f"API ERROR: {data}")
                break

            transcripts = data["data"]["transcripts"]

            if not transcripts:
                logger.info("No more meetings to fetch")
                break

            # Use tqdm for progress bar
            for meeting in tqdm(transcripts, desc=f"Processing batch {batch_count}"):
                try:
                    meeting_id = meeting["id"]
                    title = meeting["title"] or "Untitled"
                    date = meeting["dateString"] or "NoDate"
                    audio_url = meeting["audio_url"]
                    summary = meeting["summary"]["overview"] if meeting["summary"] else "";

                    safe_title = clean_filename(title)
                    folder_name = f"{date[:10]} - {safe_title}"
                    meeting_folder = os.path.join(DOWNLOAD_FOLDER, folder_name)

                    # Check if already exists
                    if os.path.exists(meeting_folder):
                        logger.info(f"Skipped (Already Exists): {folder_name}")
                        log_to_database(conn, meeting_id, title, date, meeting_folder, "skipped")
                        total_skipped += 1
                        continue

                    os.makedirs(meeting_folder, exist_ok=True)

                    # Build transcript
                    transcript = ""
                    if meeting["sentences"]:
                        transcript = " ".join([s["text"] for s in meeting["sentences"]])

                    # Save transcript
                    transcript_file = os.path.join(meeting_folder, "transcript.txt")
                    with open(transcript_file, "w", encoding="utf-8") as f:
                        f.write(transcript)
                    
                    if validate_file(transcript_file):
                        logger.debug(f"Transcript saved and validated: {folder_name}")

                    # Save summary
                    summary_file = os.path.join(meeting_folder, "summary.txt")
                    with open(summary_file, "w", encoding="utf-8") as f:
                        f.write(summary)
                    
                    if validate_file(summary_file):
                        logger.debug(f"Summary saved and validated: {folder_name}")

                    # Download audio
                    audio_file = os.path.join(meeting_folder, "audio.mp3")
                    if audio_url:
                        try:
                            audio_response = requests.get(audio_url, timeout=60)
                            audio_response.raise_for_status()
                            with open(audio_file, "wb") as f:
                                f.write(audio_response.content)
                             
                            # Validate audio file
                            if validate_file(audio_file):
                                file_hash = calculate_file_hash(audio_file)
                                logger.info(f"Audio downloaded and validated: {folder_name} (Hash: {file_hash})")
                            else:
                                logger.warning(f"Audio file validation failed: {folder_name}")
                                total_failed += 1
                                continue
                        except Exception as e:
                            logger.error(f"Audio download failed for {folder_name}: {str(e)}")
                            total_failed += 1
                            continue

                    # Save metadata
                    save_metadata(meeting_folder, meeting)
                    
                    # Log to database
                    log_to_database(conn, meeting_id, title, date, meeting_folder, "downloaded")
                    
                    total_downloaded += 1
                    logger.info(f"Downloaded: {folder_name}")

                    time.sleep(RATE_LIMIT_DELAY)

                except Exception as e:
                    logger.error(f"Error processing meeting {meeting.get('title', 'Unknown')}: {str(e)}")
                    total_failed += 1
                    continue

            skip += BATCH_SIZE
            save_checkpoint(skip, total_downloaded, total_skipped)

    except Exception as e:
        logger.error(f"Critical error in backup process: {str(e)}")
    finally:
        # Save session to database
        end_time = datetime.datetime.now()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO backup_sessions 
                (start_time, end_time, total_downloaded, total_skipped, total_failed)
                VALUES (?, ?, ?, ?, ?)
            """, (start_time.isoformat(), end_time.isoformat(), total_downloaded, total_skipped, total_failed))
            conn.commit()
        except Exception as e:
            logger.error(f"Error saving session to database: {str(e)}")
        finally:
            conn.close()
            
        # Final summary
        logger.info("\n" + "=" * 50)
        logger.info("Backup Completed Successfully")
        logger.info(f"New Downloads: {total_downloaded}")
        logger.info(f"Skipped (Already Existing): {total_skipped}")
        logger.info(f"Failed: {total_failed}")
        logger.info(f"Duration: {end_time - start_time}")
        logger.info("=" * 50 + "\n")
        
        # Send notification
        notification_message = f"""
Fireflies Backup Summary:
- New Downloads: {total_downloaded}
- Skipped (Already Existing): {total_skipped}
- Failed: {total_failed}
- Duration: {end_time - start_time}
- Backup Location: {DOWNLOAD_FOLDER}
        """
        send_notification("Fireflies Backup Completed", notification_message)

if __name__ == "__main__":
    main()