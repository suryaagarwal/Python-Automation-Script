import requests
import os
import time
import re

# ==============================
# CONFIGURATION
# ==============================

API_KEY = "Your Fireflies API Key"
BASE_URL = "https://api.fireflies.ai/graphql"

DOWNLOAD_FOLDER = r"D:\Fireflies Backup"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# ==============================
# HELPER FUNCTION
# ==============================

def clean_filename(name):
    name = re.sub(r'[<>:"/\\|?*]', '', name)  # remove invalid Windows characters
    name = name.encode('ascii', 'ignore').decode()  # remove special unicode
    return name.strip()

def fetch_transcripts(limit=50, skip=0):
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

    response = requests.post(
        BASE_URL,
        json={"query": query, "variables": variables},
        headers=headers
    )

    return response.json()

# ==============================
# MAIN PROCESS
# ==============================

limit = 50
skip = 0
total_downloaded = 0
total_skipped = 0

while True:
    data = fetch_transcripts(limit, skip)

    if "errors" in data:
        print("API ERROR:")
        print(data)
        break

    transcripts = data["data"]["transcripts"]

    if not transcripts:
        break  # No more meetings

    for meeting in transcripts:

        meeting_id = meeting["id"]
        title = meeting["title"] or "Untitled"
        date = meeting["dateString"] or "NoDate"
        audio_url = meeting["audio_url"]
        summary = meeting["summary"]["overview"] if meeting["summary"] else ""

        safe_title = clean_filename(title)
        folder_name = f"{date[:10]} - {safe_title}"
        meeting_folder = os.path.join(DOWNLOAD_FOLDER, folder_name)

        # =========================
        # AVOID DUPLICATES
        # =========================
        if os.path.exists(meeting_folder):
            print(f"Skipped (Already Exists): {folder_name}")
            total_skipped += 1
            continue

        os.makedirs(meeting_folder, exist_ok=True)

        # Build transcript
        transcript = ""
        if meeting["sentences"]:
            transcript = " ".join([s["text"] for s in meeting["sentences"]])

        # Save transcript
        with open(os.path.join(meeting_folder, "transcript.txt"), "w", encoding="utf-8") as f:
            f.write(transcript)

        # Save summary
        with open(os.path.join(meeting_folder, "summary.txt"), "w", encoding="utf-8") as f:
            f.write(summary)

        # Download audio
        if audio_url:
            try:
                audio_response = requests.get(audio_url)
                with open(os.path.join(meeting_folder, "audio.mp3"), "wb") as f:
                    f.write(audio_response.content)
            except:
                print(f"Audio download failed: {folder_name}")

        total_downloaded += 1
        print(f"Downloaded: {folder_name}")

        time.sleep(0.3)  # avoid rate limiting

    skip += limit

print("\n==============================")
print("Backup Completed Successfully")
print(f"New Downloads: {total_downloaded}")
print(f"Skipped (Already Existing): {total_skipped}")
print("==============================")