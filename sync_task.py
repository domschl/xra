import os
import sys
import subprocess
import argparse
import socket
import json
import shutil
import getpass
from typing import TypedDict, cast

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
                     options: list[str] | None = None, dry_run:bool = True, delete:bool = False, verbose:bool=False) -> SyncTaskResult:
        src = TasksSyncer.replace_mount_point(os.path.expanduser(src))
        dst = TasksSyncer.replace_mount_point(os.path.expanduser(dst))
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
            rsync_cmd = ["rsync", "-avz", "--stats"]
        else:
            rsync_cmd = ["rsync"] + options
            if "--stats" not in rsync_cmd:
                rsync_cmd.append("--stats")

        # Automatically handle Unicode normalization for macOS/Linux sync
        if sys.platform == "darwin" and (':' in src or ':' in dst): # or src.startswith('/Volumes/') or dst.startswith("/Volumes/")):
            if not any(opt.startswith('--iconv') for opt in rsync_cmd):
                rsync_cmd.append("--iconv=utf-8-mac,utf-8")
        if dry_run:
            rsync_cmd.append("--dry-run")
        if src[-1] != "/":
            print(f"Modifying source: {src}")
            src += "/"
        if dst[-1] != "/":
            print(f"Modifying destination: {dst}")
            dst += "/"
        rsync_cmd.append(src)
        rsync_cmd.append(dst)
        if delete:
            rsync_cmd.append("--delete")
        if (excludes is not None and excludes != []) or (extensions is not None and extensions != []):
            if options is None:
                options = []
            for option in options:
                if option.startswith("--include") or option.startswith("--exclude"):
                    result['error'] = "Options already contains --include or --exclude, incompatible with 'exclude' or 'extension' settings"
                    return result
            if extensions is not None and extensions != []:
                rsync_cmd.append("--include=\"*/\"")
                for ext in extensions:
                    rsync_cmd.append(f"--include=\"*.{ext}\"")
                rsync_cmd += ["--exclude=\"*\""]
            if excludes is not None and excludes != []:
                for exclude in excludes:
                    rsync_cmd += ["--exclude=\"{exclude}\""]
        if verbose:
            print(" ".join(rsync_cmd))
        try:
            proc_result = subprocess.run(rsync_cmd, check=True, capture_output=True, text=True)
            result['stdout'] = proc_result.stdout
            result['stderr'] = proc_result.stderr
            result['returncode'] = proc_result.returncode
        except subprocess.CalledProcessError as e:
            result['error'] = f"Error: {e}\nStdout: {e.stdout}\nStderr: {e.stderr}"  # pyright: ignore[reportAny]
            return result
        fields = [
            ("Number of files", "files"),
            ("Number of created files", "created"),
            ("Number of deleted files", "deleted"),
            ("Number of regular files transferred", "transferred"),
            # ("Total file size", "size"),
            # ("Total transferred file size", "transferred_size"),
            # ("Literal data", "literal"),
            # ("Matched data", "matched"),
            # ("File list size", "file_list_size"),
            # ("File list generation time", "file_list_generation_time"),
            # ("File list transfer time", "file_list_transfer_time"),
            # ("Total bytes sent", "bytes_sent"),
            # ("Total bytes received", "bytes_received"),
        ]
        mandatory_fields = ["files", "created", "transferred"]
        optional_fields = ["deleted"] 
        file_list:list[str] = []
        delete_list:list[str] = []
        for line in proc_result.stdout.split("\n")[1:]:
            if not line.strip() or ":" in line:
                break
            if line.startswith("deleting"):
                delete_list.append(line.split("deleting")[1].strip())
            else:
                file_list.append(line)
        result['file_list'] = file_list
        result['delete_list'] = delete_list

        for line in proc_result.stdout.split("\n"):
            for field in fields:
                toks = line.split(":")
                if len(toks) < 2:
                    continue
                if toks[0].strip() == field[0]:
                    val = toks[1].strip().split(" ")[0].strip().replace(",","")
                    try:
                        if '.' in val:
                            val = float(val)
                        else:
                            val = int(val)
                    except ValueError:
                        result['error'] = f"Invalid value for field {field}: {val}"
                        return result
                    if field[1] in mandatory_fields or field[1] in optional_fields:
                        result[field[1]] = val
                    break
        for field in mandatory_fields:
            if field not in result:
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
