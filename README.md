# Frigate Auto Archive

A small Docker container that archives Frigate **Alerts** and **Detections** as standalone MP4 files on a configurable daily schedule.

The archiver uses Frigate's review API to discover events, Frigate's export API to create standalone video files, and then copies completed exports to a separate archive location. A SQLite database tracks events that have already been archived.

## Features

- Archives both Frigate `alert` and `detection` review events
- Uses Frigate's export API instead of copying Frigate's internal clip/cache directories
- Runs once per day at a configurable time and timezone
- Persistent SQLite state prevents duplicate archives
- Automatically removes archived files older than the configured retention period
- Retries temporary 404 responses while an asynchronous Frigate export becomes available
- Keeps archive storage, application state, and Frigate exports as separate mounts
- Docker/Compose based
- Deployment-specific paths and settings are supplied through environment variables

## How it works

At the configured daily time, the container:

1. Queries Frigate for alert events.
2. Queries Frigate for detection events.
3. Skips events already recorded in the SQLite state database.
4. Requests a Frigate export for each remaining event.
5. Waits for the asynchronous export to become available.
6. Copies the completed MP4 into the archive directory.
7. Records the event in SQLite.
8. Removes archived files older than the retention period.

Archive layout:

```text
/archive/
└── YYYY/
    └── MM/
        └── DD/
            ├── camera-1/
            │   ├── alert_<review-id>.mp4
            │   └── detection_<review-id>.mp4
            └── camera-2/
                └── ...
```

## Configuration

### Application variables

| Variable | Example | Description |
|---|---|---|
| `FRIGATE_URL` | `http://192.168.4.69:5000` | URL of the Frigate API |
| `ARCHIVE_DIR` | `/archive` | Archive directory inside the container |
| `STATE_DB` | `/data/state.db` | SQLite state database |
| `EXPORT_DIR` | `/frigate-exports` | Frigate export directory inside the container |
| `RETENTION_DAYS` | `90` | Number of days to retain archived events |
| `POLL_SECONDS` | `5` | Seconds between export status checks |
| `EXPORT_TIMEOUT` | `300` | Maximum seconds to wait for an individual export |
| `ARCHIVE_TIME` | `03:00` | Daily archive start time in 24-hour `HH:MM` format |
| `ARCHIVE_TZ` | `America/Chicago` | Timezone associated with the configured schedule |

### Deployment path variables

The Compose file also expects these variables:

| Variable | Example | Description |
|---|---|---|
| `ARCHIVE_HOST_PATH` | `/rpool/hdd/frigate-archive` | Host path for long-term archive storage |
| `STATE_HOST_PATH` | `/opt/docker/appdata/frigate-archiver/data` | Host path for persistent SQLite state |
| `EXPORT_HOST_PATH` | `/mnt/frigate-recordings/exports` | Host path containing Frigate exports |

These paths are intentionally not hard-coded into the repository so the same Compose file can be deployed in different environments.

## Example environment

```text
FRIGATE_URL=http://192.168.4.69:5000
ARCHIVE_DIR=/archive
STATE_DB=/data/state.db
EXPORT_DIR=/frigate-exports
RETENTION_DAYS=90
POLL_SECONDS=5
EXPORT_TIMEOUT=300
ARCHIVE_TIME=03:00
ARCHIVE_TZ=America/Chicago

ARCHIVE_HOST_PATH=/rpool/hdd/frigate-archive
STATE_HOST_PATH=/opt/docker/appdata/frigate-archiver/data
EXPORT_HOST_PATH=/mnt/frigate-recordings/exports
```

Adjust the values for your environment.

## Deployment

The project is designed for Docker Compose and Portainer.

### Portainer Git repository deployment

1. Add the repository to Portainer as a Git-based Stack.
2. Set the Compose file path to `docker-compose.yml`.
3. Add the environment variables listed above.
4. Deploy the stack.
5. Use **Pull and redeploy** when updating the repository.

The container stays running between scheduled archive jobs.

## Volumes

The container uses three mounts:

- **Archive:** read/write at `ARCHIVE_DIR`
- **State:** mounted at `/data` for the SQLite database
- **Frigate exports:** mounted read-only at `EXPORT_DIR`

The Frigate export mount is read-only because the archiver only needs to read completed export files.

## Logs

With Docker:

```bash
docker logs --tail 50 frigate-auto-archive
```

A normal startup message looks like:

```text
Frigate: http://192.168.4.69:5000 | Archive: /archive | Retention: 90 days | Daily run: 03:00 (America/Chicago)
```

At the configured time:

```text
Starting daily archive at 03:00
Archiving alert driveway <review-id>
Archived -> /archive/2026/10/05/driveway/alert_<review-id>.mp4
```

## Requirements

- Frigate 0.18 or a compatible version of the Frigate review/export API
- Docker
- Docker Compose
- Network access from the container to the Frigate API
- A writable archive location
- Access to Frigate's export directory

## Design notes

### Alerts and detections

The project archives both Frigate review severities:

- `alert`
- `detection`

It does not copy Frigate's entire `clips` directory. That directory can contain thumbnails, previews, metadata, and other generated files that are not intended to be part of the long-term event archive.

### Duplicate prevention

An event is recorded in SQLite only after its exported MP4 has been successfully copied to the archive. The Frigate review ID is the unique key.

### Asynchronous exports

Frigate exports are asynchronous. The export API can return an export ID before the export status endpoint is ready. Temporary HTTP 404 responses are therefore retried until the export completes or `EXPORT_TIMEOUT` is reached.

### Retention

Retention is based on the event's Frigate start timestamp. Files older than `RETENTION_DAYS` are removed during the daily run.

### Storage separation

The archive is independent of Frigate's active recording storage. This allows long-term event archives to use larger/slower storage while Frigate's active recordings remain on separate storage.

## License

No license is currently specified. Add a license before distributing the project if desired.
## Manual run

The archiver normally runs once per day at the configured `ARCHIVE_TIME`. To trigger a run immediately for testing or on-demand archiving, run the following from the Proxmox host:

```bash
pct exec 124 -- docker exec frigate-auto-archive python /app/archiver.py --run-now
```

The manual run processes both **alerts** and **detections**, respects the existing SQLite state so previously archived reviews are skipped, performs the normal 90-day cleanup, and exits when the run is complete. It does not change the normal daily schedule.
