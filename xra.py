import os
import sys
import subprocess
import argparse
import socket
import json
import shutil
from typing import TypedDict, cast

class RsyncResult(TypedDict):
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

class XraTask(TypedDict):
    description: str
    src: str
    dst: str
    delete: bool
    excludes: list[str]
    options: list[str]
    groups: list[str]
    conditions: list[str]

def get_local_disk_info(path: str):
    if ':' in path:
        return None
    path = os.path.expanduser(path)
    try:
        usage = shutil.disk_usage(path)
        return usage.total
    except Exception:
        return None

def is_git_repo(path: str) -> bool:
    path = os.path.expanduser(path)
    return os.path.exists(os.path.join(path, ".git"))

def is_git_uptodate(path: str, check_remote:bool = False) -> bool:
    path = os.path.expanduser(path)
    local_changes = subprocess.run(["git", "status", "--porcelain"], cwd=path, capture_output=True, text=True).stdout == ""
    if check_remote:
        _ = subprocess.run(["git", "fetch"], cwd=path, capture_output=True)
        remote_changes = subprocess.run(["git", "status", "--porcelain", "--branch"], cwd=path, capture_output=True, text=True).stdout == ""
        return local_changes and remote_changes
    else:
        return local_changes

def rsync_backup(src: str, dst: str, excludes: list[str] | None = None, 
                 options: list[str] | None = None, dry_run:bool = True, delete:bool = False) -> RsyncResult:
    src = os.path.expanduser(src)
    dst = os.path.expanduser(dst)
    print(f"Backup {src} to {dst}")
    result: RsyncResult = {'error': None, 'stdout': None, 'stderr': None, 'returncode': None, 
                           'files': None, 'created': None, 'deleted': None, 'transferred': None, 
                           'file_list': None, 'delete_list': None}
    if ':' not in src:
        if not os.path.exists(src):
            result['error'] = f"Source directory {src} does not exist"
            return result
        if is_git_repo(src):
            if is_git_uptodate(src, check_remote=True):
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
    if sys.platform == "darwin" and (':' in src or ':' in dst):
        if not any(opt.startswith('--iconv') for opt in rsync_cmd):
            rsync_cmd.append("--iconv=utf-8-mac,utf-8")
    if dry_run:
        rsync_cmd.append("--dry-run")
    if src[-1] != "/":
        src += "/"
    if dst[-1] != "/":
        dst += "/"
    rsync_cmd.append(src)
    rsync_cmd.append(dst)
    if delete:
        rsync_cmd.append("--delete")
    if excludes is not None:
        for exclude in excludes:
            rsync_cmd.append("--exclude")
            rsync_cmd.append(exclude)
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
        ("Total file size", "size"),
        ("Total transferred file size", "transferred_size"),
        ("Literal data", "literal"),
        ("Matched data", "matched"),
        ("File list size", "file_list_size"),
        ("File list generation time", "file_list_generation_time"),
        ("File list transfer time", "file_list_transfer_time"),
        ("Total bytes sent", "bytes_sent"),
        ("Total bytes received", "bytes_received"),
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

def get_config(filename: str) -> dict[str, XraTask]:
    if not os.path.exists(filename):
        print(f"Config file {filename} not found")
        default_config: dict[str, XraTask] = {
            "task1": {
                "description": "",
                "src": "",
                "dst": "",
                "delete": False,
                "excludes": [],
                "options": [],
                "groups": [],
                "conditions": []
            },
            "task2": {
                "description": "",
                "src": "",
                "dst": "",
                "delete": False,
                "excludes": [],
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
    with open(filename, "r") as f:
        try:
            tasks:dict[str, XraTask] = cast(dict[str, XraTask], json.load(f))
        except Exception as e:
            print(f"Error loading config file {filename}: {e}")
            sys.exit(1)
    return tasks

def eval_conditions(task: XraTask) -> bool:
    for condition in task['conditions']:
        condition = condition.replace("{{local-hostname}}", socket.gethostname()).replace("{{local-platform}}", sys.platform)
        if "{{destination-capacity}}" in condition:
            dst = os.path.expanduser(task['dst'])
            if ':' in dst:
                return False
            capacity = get_local_disk_info(dst)
            if capacity is None:
                return False
            condition = condition.replace("{{destination-capacity}}", str(capacity))
            print(f"Capacity condition: {condition}")
        if eval(condition) is False:
            return False
    return True

def main():
    parser = argparse.ArgumentParser(description="Backup files using rsync")
    _ = parser.add_argument("task_name", nargs='?', type=str, default='all', help="Task name or group name or 'all'")
    default_config = os.path.expanduser("~/.config/xra/config.json")
    _ = parser.add_argument("--config", "-c", type=str, default=default_config, help="Config file path")
    _ = parser.add_argument("--no-confirm", "-x", action="store_true", help="Do not confirm the backup")
    _ = parser.add_argument("--verbose", "-v", action="store_true", help="Print verbose output")
    _ = parser.add_argument("--list-tasks", "-l", action="store_true", help="List available tasks")
    args = parser.parse_args()

    config_filename = cast(str, args.config)
    if '/' not in config_filename:
        config_filename = os.path.expanduser("~/.config/xra/" + config_filename)
    if config_filename.endswith('.json') is False:
        config_filename += '.json'
        
    config = get_config(config_filename)
    hostname = socket.gethostname()
    if (config_filename == default_config):
        filename = os.path.expanduser("~/.config/xra/" + hostname + ".json")
        if os.path.exists(filename):
            config.update(get_config(filename))

    task_name = cast(str, args.task_name)
    if task_name not in config and task_name != 'all':
        print(f"Task {task_name} not found in config {config_filename}, available tasks: {list(config.keys())} (or 'all')")
        sys.exit(1)
    
    task_names: list[str] = []
    for config_task in list(config.keys()):
        if task_name == 'all':
            task_names.append(config_task)
        elif task_name in config[config_task]['groups']:
            task_names.append(config_task)
        elif task_name == config_task:
            task_names.append(config_task)

    if cast(bool, args.list_tasks):
        print("Configured tasks:")
        for task_name in task_names:
            task = config[task_name]
            print(f"{task_name}: {task['description']}")
            print(f"  {task['src']} ➔  {task['dst']}")
        sys.exit(0)

    summary = {
        'ok_tasks': 0,
        'skipped_tasks': 0,
        'error_tasks': 0,
        'files': 0,
        'created': 0,
        'deleted': 0,
        'transferred': 0,
    }
    for task_name in task_names:
        task = config[task_name]
        if 'src' not in task or 'dst' not in task or 'delete' not in task or 'excludes' not in task or not task['src'] or not task['dst']:
            print(f"Task {task_name} is not properly configured in configurations")
            sys.exit(1)

        dst = os.path.expanduser(task['dst'])
        if ':' not in dst:
            if not os.path.exists(dst):
                summary['skipped_tasks'] += 1
                continue
        else:
            dst_host = dst.split(':')[0]
            if dst_host == hostname:
                summary['skipped_tasks'] += 1
                continue
                
        src = os.path.expanduser(task['src'])
        if ':' not in src:
            if not os.path.exists(src):
                summary['skipped_tasks'] += 1
                continue
        else:
            src_host = src.split(':')[0]
            if src_host == hostname:
                summary['skipped_tasks'] += 1
                continue

        if 'conditions' in task:
            if eval_conditions(task) is False:
                summary['skipped_tasks'] += 1
                continue

        if args.no_confirm is False:        
            result = rsync_backup(task['src'], task['dst'], excludes=task['excludes'], options=task['options'], dry_run = True, delete = task['delete'])
            if result['error']:
                print(result['error'])
                sys.exit(1)
            print(f"---- Task {task_name} (dry run) ----")
            print(task['description'])
            if cast(bool, args.verbose):
                if result['file_list']:
                    print("Files to be transferred:")
                    for file in result['file_list']:
                        print(f"  {file}")
                if result['delete_list']:
                    print("Files to be deleted:")
                    for file in result['delete_list']:
                        print(f"  {file}")
            
            if result['delete_list'] is not None:
                n_del = len(result['delete_list'])
            else:
                n_del = 0
            if result['deleted'] is not None:
                n_del = result['deleted']
            print(f"Files:        {result['files']}")
            print(f"Created:      {result['created']}")
            print(f"Transferred:  {result['transferred']}")
            print(f"Deleted:      {n_del}")
            num_changes = 0
            if result['created']:
                num_changes += result['created']
            if result['transferred']:
                num_changes += result['transferred']
            num_changes += n_del
            if num_changes == 0:
                print("No changes")
                summary['ok_tasks'] += 1
                continue
            if n_del > 0:
                print(f"Continue? WARNING DELETING {n_del} FILES (y/n): ", end="")
            else:
                print("Continue? (y/n): ", end="")
            ans = input()
            if ans != "y":
                summary['skipped_tasks'] += 1
                continue

        result = rsync_backup(task['src'], task['dst'], excludes=task['excludes'], options=task['options'], dry_run = False, delete = task['delete'])
        print(f"---- Task {task_name} ----")
        if result['error']:
            print(f"Error: {result['error']}")
            summary['error_tasks'] += 1
            continue
        if cast(bool, args.verbose):
            if result['file_list']:
                print("Files transferred:")
                for file in result['file_list']:
                    print(f"  {file}")
            if result['delete_list']:
                print("Files deleted:")
                for file in result['delete_list']:
                    print(f"  {file}")
        if result['delete_list'] is not None:
            n_del = len(result['delete_list'])
        else:
            n_del = 0
        if result['deleted'] is not None:
            n_del = result['deleted']
        print(f"Files:        {result['files']}")
        print(f"Created:      {result['created']}")
        print(f"Transferred:  {result['transferred']}")
        print(f"Deleted:      {n_del}")
        summary['ok_tasks'] += 1
        if result['files']:
            summary['files'] += result['files']
        if result['created']:
            summary['created'] += result['created']
        if result['transferred']:
            summary['transferred'] += result['transferred']
        summary['deleted'] += n_del

    if len(task_names) > 1:
        print("==== Summary ====")
        print(f"OK tasks:       {summary['ok_tasks']}")
        print(f"Skipped tasks:  {summary['skipped_tasks']}")
        print(f"Error tasks:    {summary['error_tasks']}")
        print(f"Files:          {summary['files']}")
        print(f"Created:        {summary['created']}")
        print(f"Transferred:    {summary['transferred']}")
        print(f"Deleted:        {summary['deleted']}")

if __name__ == "__main__":
    main()
