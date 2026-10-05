import argparse
import os
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
import requests

FRIGATE_URL = os.environ["FRIGATE_URL"].rstrip("/")
ARCHIVE_DIR = Path(os.environ["ARCHIVE_DIR"])
EXPORT_DIR = Path(os.environ["EXPORT_DIR"])
STATE_DB = Path(os.environ["STATE_DB"])
RETENTION_DAYS = int(os.environ["RETENTION_DAYS"])
POLL_SECONDS = int(os.environ["POLL_SECONDS"])
EXPORT_TIMEOUT = int(os.environ["EXPORT_TIMEOUT"])
ARCHIVE_TIME = os.environ["ARCHIVE_TIME"]
ARCHIVE_TZ = os.environ.get("ARCHIVE_TZ", "America/Chicago")
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
        json={"playback": "realtime", "source": "recordings", "name": f"archive-{review['id']}"},
        timeout=30)
    r.raise_for_status()
    return r.json()["export_id"]

def wait_export(export_id):
    deadline = time.time() + EXPORT_TIMEOUT
    while time.time() < deadline:
        r = session.get(f"{FRIGATE_URL}/api/exports/{export_id}", timeout=30)
        if r.status_code == 404:
            time.sleep(POLL_SECONDS)
            continue
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
    for rid, path in c.execute(
        "SELECT review_id,archive_path FROM archived WHERE start_time < ?", (cutoff,)
    ).fetchall():
        try:
            p = Path(path)
            if p.exists():
                p.unlink()
            c.execute("DELETE FROM archived WHERE review_id=?", (rid,))
        except OSError as e:
            print(f"Could not delete {path}: {e}", flush=True)
    c.commit()

def run_archive(c):
    print("Starting archive run", flush=True)
    for severity in ("alert", "detection"):
        for review in reviews(severity):
            try:
                archive_review(c, review)
            except Exception as e:
                print(f"Error processing {review.get('id')}: {e}", flush=True)
    cleanup(c)

def should_run_today():
    now = datetime.now().astimezone()
    return now.strftime("%H:%M") == ARCHIVE_TIME

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-now", action="store_true", help="Run the archive job immediately and exit")
    args = parser.parse_args()
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    c = db()
    print(
        f"Frigate: {FRIGATE_URL} | Archive: {ARCHIVE_DIR} | "
        f"Retention: {RETENTION_DAYS} days | Daily run: {ARCHIVE_TIME} ({ARCHIVE_TZ})",
        flush=True,
    )
    if args.run_now:
        run_archive(c)
        c.close()
        return

    last_run_date = None

    while True:
        try:
            now = datetime.now().astimezone()
            today = now.date()

            if should_run_today() and last_run_date != today:
                print(f"Starting daily archive at {ARCHIVE_TIME}", flush=True)
                run_archive(c)
                last_run_date = today

            time.sleep(20)
        except Exception as e:
            print(f"Main loop error: {e}", flush=True)
            time.sleep(20)

if __name__ == "__main__":
    main()
