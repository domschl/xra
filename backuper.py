import os
import sys
import subprocess
import argparse
import socket
import json
import shutil


def get_local_disk_info(path: str):
    if ':' in path:
        return None
    path = os.path.expanduser(path)
    try:
        usage = shutil.disk_usage(path)
        return usage.total
    except Exception:
        return None


def rsync_backup(src: str, dst: str, excludes: list[str] = [], options: list[str] = [], dry_run:bool = True, delete:bool = False):
    src = os.path.expanduser(src)
    dst = os.path.expanduser(dst)
    print(f"Backup {src} to {dst}")
    result = {'error': None}
    if ':' not in src and not os.path.exists(src):
        result['error'] = f"Source directory {src} does not exist"
        return result
    if ':' not in dst and not os.path.exists(dst):
        result['error'] = f"Destination directory {dst} does not exist"
        return result
    if options != []:
        rsync_cmd = ["rsync", "-avz", "--stats"] + options
    else:
        rsync_cmd = options
        if "--stats" not in rsync_cmd:
            rsync_cmd.append("--stats")
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
    for exclude in excludes:
        rsync_cmd.append("--exclude")
        rsync_cmd.append(exclude)
    try:
        proc_result = subprocess.run(rsync_cmd, check=True, capture_output=True, text=True)
        result['stdout'] = proc_result.stdout
        result['stderr'] = proc_result.stderr
        result['returncode'] = proc_result.returncode
    except subprocess.CalledProcessError as e:
        result['error'] = f"Error: {e}\nStdout: {e.stdout}\nStderr: {e.stderr}"
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
    mandatory_fields = ["files", "created", "transferred", "size", "transferred_size"]  # "deleted", 
    file_list = []
    delete_list = []
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
                result[field[1]] = val
                break
    for field in mandatory_fields:
        if field not in result:
            result['error'] = f"Mandatory field {field} not found in rsync result: {list(result.keys())}"
    return result

def get_config(filename: str):
    if not os.path.exists(filename):
        print(f"Config file {filename} not found")
        default_config = {
            "task1": {
                "src": "",
                "dst": "",
                "delete": False,
                "excludes": [],
                "options": [],
                "groups": [],
                "conditions": []
            },
            "task2": {
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
        return json.load(f)

def eval_conditions(task):
    for condition in task['conditions']:
        condition = condition.replace("{{local-hostname}}", socket.gethostname())
        if "{{destination-capacity}}" in condition:
            dst = os.expanduser(task['dst'])
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
    parser.add_argument("task_name", type=str, help="Task name or group name or 'all'")
    parser.add_argument("--config", "-c", type=str, default=os.path.expanduser("~/.config/backuper/config.json"), help="Config file path")
    parser.add_argument("--no-confirm", "-x", action="store_true", help="Do not confirm the backup")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print verbose output")
    args = parser.parse_args()

    config = get_config(args.config)
    hostname = socket.gethostname()
    filename = os.path.expanduser("~/.config/backuper/" + hostname + ".json")
    if os.path.exists(filename):
        config.update(get_config(filename))

    if args.task_name not in config and args.task_name != 'all':
        print(f"Task {args.task_name} not found in config {filename}, available tasks: {list(config.keys())} (or 'all')")
        sys.exit(1)
    
    task_names = []
    for config_task in list(config.keys()):
        if args.task_name == 'all':
            task_names.append(config_task)
        elif args.task_name in config[config_task]['groups']:
            task_names.append(config_task)
        elif args.task_name == config_task:
            task_names.append(config_task)

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
            print(f"Task {task_name} is not properly configured in {filename}")
            sys.exit(1)

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
            if args.verbose:
                print("Files to be transferred:")
                for file in result['file_list']:
                    print(f"  {file}")
                print("Files to be deleted:")
                for file in result['delete_list']:
                    print(f"  {file}")
            n_del = result['deleted'] if 'deleted' in result else len(result['delete_list'])
            print(f"Files:        {result['files']}")
            print(f"Created:      {result['created']}")
            print(f"Transferred:  {result['transferred']}")
            print(f"Deleted:      {n_del}")
            if result['created'] + result['transferred'] + n_del == 0:
                print("No changes")
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
        if args.verbose:
            print("Files transferred:")
            for file in result['file_list']:
                print(f"  {file}")
            print("Files deleted:")
            for file in result['delete_list']:
                print(f"  {file}")
        print(f"Files:        {result['files']}")
        print(f"Created:      {result['created']}")
        print(f"Transferred:  {result['transferred']}")
        print(f"Deleted:      {n_del}")
        summary['ok_tasks'] += 1
        summary['files'] += result['files']
        summary['created'] += result['created']
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
