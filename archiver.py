import os
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
import requests

FRIGATE_URL = os.getenv("FRIGATE_URL", "http://192.168.4.69:5000").rstrip("/")
ARCHIVE_DIR = Path(os.getenv("ARCHIVE_DIR", "/archive"))
EXPORT_DIR = Path(os.getenv("EXPORT_DIR", "/frigate-exports"))
STATE_DB = Path(os.getenv("STATE_DB", "/data/state.db"))
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "90"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "5"))
EXPORT_TIMEOUT = int(os.getenv("EXPORT_TIMEOUT", "300"))
session = requests.Session()

def db():
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(STATE_DB)
    c.execute("""CREATE TABLE IF NOT EXISTS archived (
        review_id TEXT PRIMARY KEY, camera TEXT NOT NULL, severity TEXT NOT NULL,
        start_time REAL NOT NULL, end_time REAL NOT NULL,
        archive_path TEXT NOT NULL, archived_at REAL NOT NULL)""")
    c.commit()
    return c

def reviews(severity):
    r = session.get(f"{FRIGATE_URL}/api/review", params={"severity": severity}, timeout=30)
    r.raise_for_status()
    return r.json()

def create_export(review):
    r = session.post(
        f"{FRIGATE_URL}/api/export/{review['camera']}/start/{review['start_time']}/end/{review['end_time']}",
        json={"playback":"realtime","source":"recordings","name":f"archive-{review['id']}"},
        timeout=30)
    r.raise_for_status()
    return r.json()["export_id"]

def wait_export(export_id):
    deadline = time.time() + EXPORT_TIMEOUT
    while time.time() < deadline:
        r = session.get(f"{FRIGATE_URL}/api/exports/{export_id}", timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data.get("in_progress", True):
            video_path = data.get("video_path")
            return EXPORT_DIR / Path(video_path).name if video_path else None
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"Export {export_id} timed out")

def archive_review(c, review):
    rid = review["id"]
    if c.execute("SELECT 1 FROM archived WHERE review_id=?", (rid,)).fetchone():
        return
    print(f"Archiving {review['severity']} {review['camera']} {rid}", flush=True)
    source = wait_export(create_export(review))
    if not source or not source.exists():
        print(f"Export missing for {rid}: {source}", flush=True)
        return
    t = datetime.fromtimestamp(review["start_time"], timezone.utc)
    destdir = ARCHIVE_DIR / t.strftime("%Y/%m/%d") / review["camera"]
    destdir.mkdir(parents=True, exist_ok=True)
    dest = destdir / f"{review['severity']}_{rid}.mp4"
    if not dest.exists():
        shutil.copy2(source, dest)
    c.execute("INSERT INTO archived VALUES (?,?,?,?,?,?,?)",
              (rid, review["camera"], review["severity"], review["start_time"],
               review["end_time"], str(dest), time.time()))
    c.commit()
    print(f"Archived -> {dest}", flush=True)

def cleanup(c):
    cutoff = time.time() - RETENTION_DAYS * 86400
    for rid, path in c.execute("SELECT review_id,archive_path FROM archived WHERE start_time < ?", (cutoff,)).fetchall():
        try:
            p = Path(path)
            if p.exists(): p.unlink()
            c.execute("DELETE FROM archived WHERE review_id=?", (rid,))
        except OSError as e:
            print(f"Could not delete {path}: {e}", flush=True)
    c.commit()

def main():
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    c = db()
    print(f"Frigate: {FRIGATE_URL} | Archive: {ARCHIVE_DIR} | Retention: {RETENTION_DAYS} days", flush=True)
    while True:
        try:
            for severity in ("alert", "detection"):
                for review in reviews(severity):
                    try: archive_review(c, review)
                    except Exception as e: print(f"Error processing {review.get('id')}: {e}", flush=True)
            cleanup(c)
        except Exception as e:
            print(f"Main loop error: {e}", flush=True)
        time.sleep(300)

if __name__ == "__main__":
    main()
