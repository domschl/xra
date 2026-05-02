# XRA — rsync Backup Tool

A configurable file backup tool using `rsync`, supporting local and remote sync, conditional tasks, mount/unmount commands, and dry-run previews.

## Features

- **rsync-based sync** — leverages rsync's efficient file transfer with full statistics
- **JSON configuration** — define multiple backup tasks in a single config file
- **Dry-run preview** — see what would be transferred/deleted before confirming
- **Mount/unmount commands** — auto-mount and unmount external drives per task
- **Conditional execution** — skip tasks based on hostname, platform, disk capacity, etc.
- **Task grouping** — run multiple tasks at once by group name
- **Exclude/include filters** — filter by file extensions or path patterns
- **Git-aware** — checks that git repos are up-to-date before syncing
- **Cross-platform** — supports Linux and macOS with automatic mount-point resolution
- **Unicode normalization** — automatic `--iconv` for macOS ↔ Linux sync

## Requirements

- Python 3.13+
- `rsync` installed and in PATH
- `uv` for dependency management (optional)

## Installation

```bash
cd xra
uv sync
```

Run directly:

```bash
uv run python xra.py
```

## Usage

```
usage: xra.py [-h] [--config CONFIG] [--no-confirm] [--verbose] [--list-tasks] [task_name]

positional arguments:
  task_name             Task name, group name, or 'all' (default: all)

options:
  -h, --help            show this help message and exit
  --config CONFIG, -c   Config file path (default: ~/.config/xra/config.json)
  --no-confirm, -x      Skip dry-run confirmation
  --verbose, -v         Print verbose output (file lists, commands)
  --list-tasks, -l      List configured tasks and their details
```

### Examples

```bash
# Run all configured tasks
python xra.py all

# Run a single task by name
python xra.py documents

# Run all tasks in a group
python xra.py backups

# List all configured tasks
python xra.py --list-tasks

# Run without confirmation (automated/scripted use)
python xra.py all --no-confirm

# Verbose output showing file lists and commands
python xra.py all --verbose

# Use a custom config file
python xra.py --config /path/to/custom.json
```

## Configuration

Config files are stored as JSON. The default location is `~/.config/xra/config.json`.

A hostname-specific config at `~/.config/xra/<hostname>.json` is also loaded and merged (hostname config takes precedence).

### Task Structure

Each task in the config is a JSON object:

```json
{
  "task_name": {
    "description": "Human-readable description",
    "src": "/path/to/source",
    "dst": "/path/to/destination",
    "delete": false,
    "mount_cmd": [],
    "umount_cmd": [],
    "excludes": [],
    "extensions": [],
    "options": [],
    "groups": [],
    "conditions": []
  }
}
```

### Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| `description` | string | Human-readable description of the task |
| `src` | string | Source path (local or `host:path` for remote) |
| `dst` | string | Destination path (local or `host:path` for remote) |
| `delete` | bool | Whether to delete files at destination not present at source |
| `mount_cmd` | list | Commands to run before sync (e.g., mount a drive) |
| `umount_cmd` | list | Commands to run after sync (e.g., unmount a drive) |
| `excludes` | list | Path patterns to exclude (passed as `--exclude` to rsync) |
| `extensions` | list | File extensions to include (all others excluded) |
| `options` | list | Extra rsync options (e.g., `["-az", "--bwlimit=1000"]`) |
| `groups` | list | Group names this task belongs to |
| `conditions` | list | Python expressions that must all evaluate to `true` |

### Placeholders

- `{{mount-point}}` — resolved to `/Volumes` on macOS, `/run/media/<user>` on Linux
- `{{local-hostname}}` — current hostname (in conditions)
- `{{local-platform}}` — platform identifier, e.g., `"linux"` or `"darwin"` (in conditions)
- `{{destination-capacity}}` — total bytes of the destination disk (in conditions, local destinations only)

### Example Config

```json
{
  "documents": {
    "description": "Backup documents to external drive",
    "src": "~/Documents",
    "dst": "{{mount-point}}/Backup/Documents",
    "delete": false,
    "mount_cmd": [],
    "umount_cmd": [],
    "excludes": ["*.tmp", "cache/"],
    "extensions": [],
    "options": [],
    "groups": ["backups"],
    "conditions": []
  },
  "photos": {
    "description": "Sync photos to NAS",
    "src": "~/Pictures",
    "dst": "nas:backups/photos",
    "delete": true,
    "mount_cmd": [],
    "umount_cmd": [],
    "excludes": [],
    "extensions": ["jpg", "png", "heic", "raw"],
    "options": ["--bwlimit=2000"],
    "groups": ["backups"],
    "conditions": ["\"{{local-platform}}\" != \"darwin\""]
  }
}
```

## Example Condition Expressions

```python
# Only run on Linux
"\"{{local-platform}}\" == \"linux\""

# Only run on a specific host
"\"{{local-hostname}}\" == \"workstation\""

# Only run if destination has more than 10GB free
"{{destination-capacity}} > 10000000000"

# Run only on macOS
"\"{{local-platform}}\" == \"darwin\""
```

## Output

For each task, xra reports:

- **Files** — total number of files processed
- **Created** — new files added at destination
- **Transferred** — files that were transferred/updated
- **Deleted** — files removed from destination (if `delete` is enabled)

When running multiple tasks, a summary is printed at the end.

## Notes

- **Git repos**: If the source directory is a git repository, xra checks that it is up-to-date with its remote before syncing
- **macOS hostnames**: macOS may change the hostname on reboot; xra will warn if this is detected
- **Remote paths**: Use `hostname:path` syntax for remote (SSH) paths, which rsync supports natively
- **No external dependencies**: Beyond Python 3.13 and rsync, xra has no additional dependencies
