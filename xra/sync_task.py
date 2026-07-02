import os
import sys
import subprocess
import argparse
import socket
import json
import shutil
import getpass
from typing import TypedDict, cast, Callable, Any

class SyncTaskResult(TypedDict):
    error: str | None
    stdout: str | None
    stderr: str | None
    returncode: int | None
    files: int | None
    created: int | None
    deleted: int | None
    transferred: int | None
    file_list: list[str] | None
    delete_list: list[str] | None

class SyncTask(TypedDict):
    description: str
    src: str
    dst: str
    delete: bool
    mount_cmd: list[str]|None
    umount_cmd: list[str]|None
    excludes: list[str]
    extensions: list[str]
    options: list[str]
    groups: list[str]
    conditions: list[str]



class TasksSyncer:
    _rsync_warned = False

    def __init__(self):
        pass

    @staticmethod
    def get_local_disk_info(path: str):
        if ':' in path:
            return None
        path = os.path.expanduser(path)
        try:
            usage = shutil.disk_usage(path)
            return usage.total
        except Exception:
            return None

    @staticmethod
    def is_git_repo(path: str) -> bool:
        path = os.path.expanduser(path)
        return os.path.exists(os.path.join(path, ".git"))

    @staticmethod
    def is_git_uptodate(path: str, check_remote:bool = False) -> bool:
        path = os.path.expanduser(path)
        local_changes = subprocess.run(["git", "status", "--porcelain"], cwd=path, capture_output=True, text=True).stdout == ""
        if check_remote:
            _ = subprocess.run(["git", "fetch"], cwd=path, capture_output=True)
            remote_changes = subprocess.run(["git", "status", "--porcelain", "--branch"], cwd=path, capture_output=True, text=True).stdout == ""
            return local_changes and remote_changes
        else:
            return local_changes

    @staticmethod
    def replace_mount_point(pathspec:str) -> str:
        if f'{{mount-point}}' in pathspec:
            if sys.platform == 'darwin':
                pathspec = pathspec.replace('{{mount-point}}', '/Volumes')
            elif sys.platform == 'linux':
                username = getpass.getuser()
                mnt = f'/run/media/{username}'
                pathspec = pathspec.replace('{{mount-point}}', mnt)
            else:
                raise Exception(f"Unsupported platform: {sys.platform}")
        return pathspec

    def rsync_backup(self, src: str, dst: str, excludes: list[str] | None = None, extensions: list[str] | None = None,
                     options: list[str] | None = None, dry_run: bool = True, delete: bool = False, verbose: bool = False,
                     progress_callback: Callable[[dict[str, Any]], None] | None = None) -> SyncTaskResult:
        src = TasksSyncer.replace_mount_point(os.path.expanduser(src))
        dst = TasksSyncer.replace_mount_point(os.path.expanduser(dst))
        if verbose:
            print(f"Backup {src} to {dst}")
        result: SyncTaskResult = {'error': None, 'stdout': None, 'stderr': None, 'returncode': None, 
                               'files': None, 'created': None, 'deleted': None, 'transferred': None, 
                               'file_list': None, 'delete_list': None}
        if ':' not in src:
            if not os.path.exists(src):
                result['error'] = f"Source directory {src} does not exist"
                return result
            if TasksSyncer.is_git_repo(src):
                if TasksSyncer.is_git_uptodate(src, check_remote=True):
                    result['error'] = f"Source directory {src} is not up to date with remote or contains uncommitted changes, please push/pull changes before sync"
                    return result
        if ':' not in dst:
            if not os.path.exists(dst):
                result['error'] = f"Destination directory {dst} does not exist"
                return result
        if options is None or options == []:
            if extensions is not None and extensions != []:
                rsync_cmd = ["rsync", "-avzm", "--stats"]
            else:
                rsync_cmd = ["rsync", "-avz", "--stats"]
        else:
            rsync_cmd = ["rsync"] + options
            if "--stats" not in rsync_cmd:
                rsync_cmd.append("--stats")

        # On macOS, default to Homebrew's rsync binary if available
        if sys.platform == "darwin":
            homebrew_rsync = None
            for path in ["/opt/homebrew/bin/rsync", "/usr/local/bin/rsync"]:
                if os.path.exists(path):
                    homebrew_rsync = path
                    break
            
            if homebrew_rsync:
                if rsync_cmd[0] == "rsync":
                    rsync_cmd[0] = homebrew_rsync
            else:
                if not TasksSyncer._rsync_warned:
                    print("Warning: Homebrew rsync not found. Using system rsync which may be outdated (v2.6.9).", file=sys.stderr)
                    print("Install it with: brew install rsync", file=sys.stderr)
                    TasksSyncer._rsync_warned = True

        # Automatically handle Unicode normalization for macOS/Linux sync
        if sys.platform == "darwin" and (':' in src or ':' in dst): # or src.startswith('/Volumes/') or dst.startswith("/Volumes/")):
            if "--no-iconv" in rsync_cmd:
                rsync_cmd.remove("--no-iconv")
            elif not any(opt.startswith('--iconv') for opt in rsync_cmd):
                rsync_cmd.append("--iconv=utf-8-mac,utf-8")

        if (excludes is not None and excludes != []) or (extensions is not None and extensions != []):
            if options is None:
                options = []
            for option in options:
                if option.startswith("--include") or option.startswith("--exclude"):
                    result['error'] = "Options already contains --include or --exclude, incompatible with 'exclude' or 'extension' settings"
                    return result
            if extensions is not None and extensions != []:
                for ext in extensions:
                    rsync_cmd.append(f"--include=*.{ext}")
                rsync_cmd += ["-f", "hide,! */"]
            else:
                if excludes is not None and excludes != []:
                    for exclude in excludes:
                        rsync_cmd += [f"--exclude={exclude}"]
        if dry_run:
            rsync_cmd.append("--dry-run")
        
        # If progress callback is set, ensure we request progress updates
        if progress_callback is not None:
            if "-P" not in rsync_cmd and "--progress" not in rsync_cmd and "--info=progress2" not in rsync_cmd:
                rsync_cmd.append("--progress")

        if src[-1] != "/":
            if verbose:
                print(f"Modifying source: {src}")
            src += "/"
        if dst[-1] != "/":
            if verbose:
                print(f"Modifying destination: {dst}")
            dst += "/"
        rsync_cmd.append(src)
        rsync_cmd.append(dst)
        if delete:
            rsync_cmd.append("--delete")
        if verbose:
            print(" ".join(rsync_cmd))

        import re
        import threading
        PROGRESS_RE = re.compile(
            r'^\s*([\d,]+)\s+(\d+)%\s+([\d.]+[\w/]+)\s+(\d+:\d+:\d+|\d+:\d+)(?:\s+\((?:xfr#\d+,\s+)?to-chk=(\d+)/(\d+)\))?'
        )

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        file_list: list[str] = []
        delete_list: list[str] = []

        try:
            proc = subprocess.Popen(
                rsync_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
        except Exception as e:
            result['error'] = f"Failed to start rsync process: {e}"
            return result

        def read_stderr(stream: Any) -> None:
            for err_line in stream:
                stderr_lines.append(err_line)
                # Forward to progress callback if set, otherwise print to sys.stderr
                if progress_callback:
                    progress_callback({
                        'type': 'stderr',
                        'line': err_line.rstrip('\r\n')
                    })
                else:
                    print(err_line, end="", file=sys.stderr, flush=True)

        err_thread = threading.Thread(target=read_stderr, args=(proc.stderr,))
        err_thread.start()

        try:
            current_file = None
            while True:
                line = proc.stdout.readline()  # pyright: ignore[reportOptionalMemberAccess]
                if not line:
                    break
                stdout_lines.append(line)
                
                stripped = line.strip()
                if not stripped:
                    continue
                
                # Check for progress line
                match = PROGRESS_RE.match(line)
                if match:
                    if progress_callback:
                        bytes_val = int(match.group(1).replace(",", ""))
                        percent_val = int(match.group(2))
                        speed_val = match.group(3)
                        eta_val = match.group(4)
                        to_chk_curr = int(match.group(5)) if match.group(5) else None
                        to_chk_tot = int(match.group(6)) if match.group(6) else None
                        
                        progress_callback({
                            'type': 'progress',
                            'bytes': bytes_val,
                            'percent': percent_val,
                            'speed': speed_val,
                            'eta': eta_val,
                            'to_chk_current': to_chk_curr,
                            'to_chk_total': to_chk_tot,
                            'current_file': current_file
                        })
                    continue
                
                # Handle status/system messages
                if stripped.startswith("building file list") or stripped.startswith("sending incremental file list"):
                    if progress_callback:
                        progress_callback({
                            'type': 'status',
                            'status': stripped
                        })
                    continue

                if (stripped.startswith("created directory") or
                    stripped.startswith("rsync:") or
                    stripped.startswith("rsync error:")):
                    continue
                
                if (stripped.startswith("sent ") and "received " in stripped) or (stripped.startswith("total size is ") and "speedup is " in stripped):
                    continue

                if ":" in stripped:
                    prefix = stripped.split(":", 1)[0].strip()
                    if prefix in [
                        "Number of files",
                        "Number of created files",
                        "Number of deleted files",
                        "Number of regular files transferred",
                        "Total file size",
                        "Total transferred file size",
                        "Literal data",
                        "Matched data",
                        "File list size",
                        "File list generation time",
                        "File list transfer time",
                        "Total bytes sent",
                        "Total bytes received",
                        "sent",
                        "received",
                        "speedup"
                    ]:
                        continue
                
                if stripped.startswith("deleting "):
                    del_file = stripped[9:].strip()
                    delete_list.append(del_file)
                    if progress_callback:
                        progress_callback({
                            'type': 'delete',
                            'filename': del_file
                        })
                else:
                    # It's a filename being processed/transferred
                    current_file = stripped
                    file_list.append(current_file)
                    if progress_callback:
                        progress_callback({
                            'type': 'file',
                            'filename': current_file
                        })
        finally:
            proc.stdout.close()  # pyright: ignore[reportOptionalMemberAccess]
            proc.wait()
            err_thread.join()

        result['stdout'] = "".join(stdout_lines)
        result['stderr'] = "".join(stderr_lines)
        result['returncode'] = proc.returncode
        result['file_list'] = file_list
        result['delete_list'] = delete_list

        if proc.returncode != 0:
            result['error'] = f"Error: Command '{' '.join(rsync_cmd)}' returned non-zero exit status {proc.returncode}.\nStdout: {result['stdout']}\nStderr: {result['stderr']}"
            return result

        fields = [
            ("Number of files", "files"),
            ("Number of created files", "created"),
            ("Number of deleted files", "deleted"),
            ("Number of regular files transferred", "transferred"),
        ]
        mandatory_fields = ["files", "created", "transferred"]
        optional_fields = ["deleted"] 

        for line in stdout_lines:
            for field in fields:
                toks = line.split(":")
                if len(toks) < 2:
                    continue
                if toks[0].strip() == field[0]:
                    val_str = toks[1].strip().split(" ")[0].strip().replace(",","")
                    try:
                        if '.' in val_str:
                            val: int | float = float(val_str)
                        else:
                            val = int(val_str)
                    except ValueError:
                        result['error'] = f"Invalid value for field {field}: {val_str}"
                        return result
                    if field[1] in mandatory_fields or field[1] in optional_fields:
                        result[field[1]] = val  # pyright: ignore[reportGeneralTypeIssues]
                    break

        for field in mandatory_fields:
            if field not in result or result[field] is None:  # pyright: ignore[reportGeneralTypeIssues]
                result['error'] = f"Mandatory field {field} not found in rsync result: {list(result.keys())}"
        return result

    @staticmethod
    def replace_shorthands(cmd:str) -> str:
        cmd = os.path.expanduser(cmd)
        cmd = TasksSyncer.replace_mount_point(cmd)
        return cmd

    def pre_post_cmds(self, cmds: list[str]|None, verbose:bool=False) -> bool:
        if cmds is None or cmds == []:
            return True
        cmds = [TasksSyncer.replace_shorthands(cmd) for cmd in cmds]
        str_cmd = " ".join(cmds)
        if verbose:
            print(str_cmd)
        try:
            proc_result = subprocess.run(cmds, check=True, capture_output=True, text=True)
            if proc_result.returncode != 0:
                print(f"{str_cmd} [{proc_result.returncode}]: {proc_result.stderr}")
                return False
        except subprocess.CalledProcessError as e:
            print(f"{str_cmd}: Error: {e}\nStdout: {e.stdout}\nStderr: {e.stderr}")  # pyright: ignore[reportAny]
            return False
        return True

    def create_default_config(self, filename: str):
        if not os.path.exists(filename):
            print(f"Config file {filename} not found")
            default_config: dict[str, SyncTask] = {
                "task1": {
                    "description": "",
                    "src": "",
                    "dst": "",
                    "delete": False,
                    "mount_cmd": [],
                    "umount_cmd": [],
                    "excludes": [],
                    "extensions": [],
                    "options": [],
                    "groups": [],
                    "conditions": []
                },
                "task2": {
                    "description": "",
                    "src": "",
                    "dst": "",
                    "delete": False,
                    "mount_cmd": [],
                    "umount_cmd": [],
                    "excludes": [],
                    "extensions": [],
                    "options": [],
                    "groups": [],
                    "conditions": []
                }
            }
            base_dir = os.path.dirname(filename)
            if not os.path.exists(base_dir):
                os.makedirs(base_dir)
            with open(filename, "w") as f:
                json.dump(default_config, f, indent=4)
            print(f"Created default config file {filename}, please edit it and try again")
            sys.exit(1)

    def get_config(self, filename: str) -> dict[str, SyncTask]:
        with open(filename, "r") as f:
            try:
                tasks:dict[str, SyncTask] = cast(dict[str, SyncTask], json.load(f))
            except Exception as e:
                print(f"Error loading config file {filename}: {e}")
                sys.exit(1)
        return tasks

    def eval_conditions(self, task: SyncTask, verbose: bool=False) -> bool:
        for condition in task['conditions']:
            condition = condition.replace("{{local-hostname}}", socket.gethostname()).replace("{{local-platform}}", sys.platform)
            if "{{destination-capacity}}" in condition:
                dst = TasksSyncer.replace_mount_point(os.path.expanduser(task['dst']))
                if ':' in dst:
                    return False
                capacity = TasksSyncer.get_local_disk_info(dst)
                if capacity is None:
                    if verbose:
                        print(f"Failed to get destination capacity for {dst}")
                    return False
                condition = condition.replace("{{destination-capacity}}", str(capacity))
                if verbose:
                    print(f"Capacity condition: {condition}")
            if eval(condition) is False:
                return False
        return True
