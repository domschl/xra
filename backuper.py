import os
import sys
import subprocess
import argparse
import socket
import json


def rsync_backup(src: str, dst: str, dry_run:bool = True, delete:bool = False):
    print(f"Backup {src} to {dst}")
    result = {'error': None}
    if not os.path.exists(src):
        result['error'] = f"Source directory {src} does not exist"
        return result
    if not os.path.exists(dst):
        result['error'] = f"Destination directory {dst} does not exist"
        return result
    rsync_cmd = ["rsync", "-avz", "--stats"]
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
    mandatory_fields = ["files", "created", "deleted", "transferred", "size", "transferred_size"]
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
                val = toks[1].strip().split(" ")[0].strip()
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
            result['error'] = f"Mandatory field {field} not found in rsync result: {proc_result.stdout}"
    return result

def get_config(filename: str):
    if not os.path.exists(filename):
        print(f"Config file {filename} not found")
        default_config = {
            "task1": {
                "src": "",
                "dst": "",
                "delete": False
            },
            "task2": {
                "src": "",
                "dst": "",
                "delete": False
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

def main():
    parser = argparse.ArgumentParser(description="Backup files using rsync")
    parser.add_argument("task_name", type=str, help="Task name")
    parser.add_argument("--no-confirm", "-x", action="store_true", help="Do not confirm the backup")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print verbose output")
    args = parser.parse_args()

    hostname = socket.gethostname()
    filename = os.path.expanduser("~/.config/backuper/" + hostname + ".json")
    config = get_config(filename)

    if args.task_name not in config and args.task_name != 'all':
        print(f"Task {args.task_name} not found in config {filename}, available tasks: {list(config.keys())} (or 'all')")
        sys.exit(1)
    
    if args.task_name == 'all':
        task_names = list(config.keys())
    else:
        task_names = [args.task_name]

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
        if 'src' not in task or 'dst' not in task or 'delete' not in task or not task['src'] or not task['dst']:
            print(f"Task {task_name} is not properly configured in {filename}")
            sys.exit(1)

        if args.no_confirm is False:        
            result = rsync_backup(task['src'], task['dst'], dry_run = True, delete = task['delete'])
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
            print(f"Files:        {result['files']}")
            print(f"Created:      {result['created']}")
            print(f"Transferred:  {result['transferred']}")
            print(f"Deleted:      {result['deleted']}")
            if result['created'] + result['transferred'] + result['deleted'] == 0:
                print("No changes")
                summary['ok_tasks'] += 1
                continue
            if result['deleted'] > 0:
                print(f"Continue? WARNING DELETING {result['deleted']} FILES (y/n): ", end="")
            else:
                print("Continue? (y/n): ", end="")
            ans = input()
            if ans != "y":
                summary['skipped_tasks'] += 1
                continue

        result = rsync_backup(task['src'], task['dst'], dry_run = False, delete = task['delete'])
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
        print(f"Deleted:      {result['deleted']}")
        summary['ok_tasks'] += 1
        summary['files'] += result['files']
        summary['created'] += result['created']
        summary['transferred'] += result['transferred']
        summary['deleted'] += result['deleted']

    if len(task_names) > 1:
        print("==== Summary ====")
        print(f"OK tasks:     {summary['ok_tasks']}")
        print(f"Skipped tasks:  {summary['skipped_tasks']}")
        print(f"Error tasks:  {summary['error_tasks']}")
        print(f"Files:        {summary['files']}")
        print(f"Created:      {summary['created']}")
        print(f"Transferred:  {summary['transferred']}")
        print(f"Deleted:      {summary['deleted']}")

if __name__ == "__main__":
    main()
