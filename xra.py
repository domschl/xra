import argparse
import os
import sys
import socket
from typing import cast, TypedDict
from sync_task import TasksSyncer

class SyncTaskSummary(TypedDict):
    ok_tasks: int
    ok_task_names: list[str]
    skipped_tasks: int
    skipped_task_names: list[str]
    error_tasks: int
    error_task_names: list[str]
    files: int
    created: int
    deleted: int
    transferred: int
    

def main():
    parser = argparse.ArgumentParser(description="Backup files using rsync")
    _ = parser.add_argument("task_name", nargs='?', type=str, default='all', help="Task name or group name or 'all'")
    default_config = os.path.expanduser("~/.config/xra/config.json")
    _ = parser.add_argument("--config", "-c", type=str, default=default_config, help="Config file path")
    _ = parser.add_argument("--no-confirm", "-x", action="store_true", help="Do not confirm the backup")
    _ = parser.add_argument("--verbose", "-v", action="store_true", help="Print verbose output")
    _ = parser.add_argument("--list-tasks", "-l", action="store_true", help="List available tasks")
    args = parser.parse_args()

    verbose = cast(bool, args.verbose)
    config_filename = cast(str, args.config)
    if '/' not in config_filename:
        config_filename = os.path.expanduser("~/.config/xra/" + config_filename)
    if config_filename.endswith('.json') is False:
        config_filename += '.json'

    xst = TasksSyncer()
    
    if os.path.exists(config_filename):
        print(f"Reading: {config_filename}")
        config = xst.get_config(config_filename)
    else:
        config = {}
    hostname = socket.gethostname()
    if sys.platform=="darwin" and ("-2" in hostname or "-3" in hostname):
        print(f"Your local hostname became {hostname}, macOS has again changed it. Please fix this and run again!")
        print("Since macOS has lost the ability to change hostnames 'on the fly', you might need to reboot")
        exit(-1)
    filename = os.path.expanduser("~/.config/xra/" + hostname + ".json")
    if os.path.exists(filename):
        def_config = xst.get_config(filename)
        if def_config and def_config != {}:
            config.update(def_config)

    if config == {}:
        _ = xst.create_default_config(filename)
        
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

    summary: SyncTaskSummary = {
        'ok_tasks': 0,
        'ok_task_names': [],
        'skipped_tasks': 0,
        'skipped_task_names': [],
        'error_tasks': 0,
        'error_task_names': [],
        'files': 0,
        'created': 0,
        'deleted': 0,
        'transferred': 0,
    }
    for task_name in task_names:
        task = config[task_name]
        if 'src' not in task or 'dst' not in task or 'delete' not in task or 'excludes' not in task or not task['src'] or not task['dst']:
            print(f"Task {task_name} is not properly configured in configurations, ignored.")
            summary['error_tasks'] += 1
            summary['error_task_names'].append(task_name)
            continue

        if xst.pre_post_cmds(task['mount_cmd'], verbose) is False:
            print(f"Task {task_name}, mount failure")
            summary['error_tasks'] += 1
            summary['error_task_names'].append(task_name)
            continue
        
        dst = TasksSyncer.replace_mount_point(os.path.expanduser(task['dst']))
        if ':' not in dst:
            if not os.path.exists(dst):
                if verbose:
                    print(f"Destination directory {dst} does not exist")
                summary['skipped_tasks'] += 1
                summary['skipped_task_names'].append(task_name)
                _ = xst.pre_post_cmds(task['umount_cmd'], verbose)
                continue
        else:
            dst_host = dst.split(':')[0]
            if dst_host == hostname:
                if verbose:
                    print(f"Destination directory {dst} is on the same host")
                summary['skipped_tasks'] += 1
                summary['skipped_task_names'].append(task_name)
                continue
                
        src = TasksSyncer.replace_mount_point(os.path.expanduser(task['src']))
        if ':' not in src:
            if not os.path.exists(src):
                if verbose:
                    print(f"Source directory {src} does not exist")
                summary['skipped_tasks'] += 1
                summary['skipped_task_names'].append(task_name)
                _ = xst.pre_post_cmds(task['umount_cmd'], verbose)
                continue
        else:
            src_host = src.split(':')[0]
            if src_host == hostname:
                if verbose:
                    print(f"Source directory {src} is on the same host")
                summary['skipped_tasks'] += 1
                summary['skipped_task_names'].append(task_name)
                _ = xst.pre_post_cmds(task['umount_cmd'], verbose)
                continue

        if 'conditions' in task:
            if xst.eval_conditions(task, verbose) is False:
                if verbose:
                    print(f"Conditions for task {task_name} are not met: {task['conditions']}")
                summary['skipped_tasks'] += 1
                summary['skipped_task_names'].append(task_name)
                _ = xst.pre_post_cmds(task['umount_cmd'], verbose)
                continue

        if args.no_confirm is False:     
            if verbose:
                print(f"{src} ➔ {dst}")   
            result = xst.rsync_backup(task['src'], task['dst'], excludes=task['excludes'], extensions=task['extensions'], options=task['options'], dry_run = True, delete = task['delete'], verbose=verbose)
            if result['error']:
                print(result['error'])
                _ = xst.pre_post_cmds(task['umount_cmd'], verbose)
                sys.exit(1)
            print(f"---- Task {task_name} (dry run) ----")
            print(task['description'])
            if verbose:
                if result['file_list']:
                    print("Files to be transferred:")
                    for file in result['file_list']:
                        print(f"  {file}")
                if result['delete_list']:
                    print("---------------------------")
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
                summary['ok_task_names'].append(task_name)
                _ = xst.pre_post_cmds(task['umount_cmd'], verbose)
                continue
            if n_del > 0:
                print(f"Continue? WARNING DELETING {n_del} FILES (y/n): ", end="")
            else:
                print("Continue? (y/n): ", end="")
            ans = input()
            if ans != "y":
                summary['skipped_tasks'] += 1
                summary['skipped_task_names'].append(task_name)
                _ = xst.pre_post_cmds(task['umount_cmd'], verbose)
                continue

        result = xst.rsync_backup(task['src'], task['dst'], excludes=task['excludes'], extensions=task['extensions'], options=task['options'], dry_run = False, delete = task['delete'])
        print(f"---- Task {task_name} ----")
        if result['error']:
            print(f"Error: {result['error']}")
            summary['error_tasks'] += 1
            summary['error_task_names'].append(task_name)
            _ = xst.pre_post_cmds(task['umount_cmd'], verbose)
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
        summary['ok_task_names'].append(task_name)
        if result['files']:
            summary['files'] += result['files']
        if result['created']:
            summary['created'] += result['created']
        if result['transferred']:
            summary['transferred'] += result['transferred']
        summary['deleted'] += n_del
        _ = xst.pre_post_cmds(task['umount_cmd'], verbose)

    if len(task_names) > 1:
        print("==== Summary ====")
        print(f"OK tasks:       {summary['ok_tasks']}: {summary['ok_task_names']}")
        print(f"Skipped tasks:  {summary['skipped_tasks']}: {summary['skipped_task_names']}")
        print(f"Error tasks:    {summary['error_tasks']}: {summary['error_task_names']}")
        print(f"Files:          {summary['files']}")
        print(f"Created:        {summary['created']}")
        print(f"Transferred:    {summary['transferred']}")
        print(f"Deleted:        {summary['deleted']}")

if __name__ == "__main__":
    main()
