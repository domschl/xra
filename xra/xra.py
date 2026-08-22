import argparse
import os
import sys
import socket
from typing import cast, TypedDict, Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.prompt import Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn, TransferSpeedColumn

from xra.sync_task import TasksSyncer, SyncTask

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
    

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup files using rsync")
    _ = parser.add_argument("task_name", nargs='?', type=str, default='all', help="Task name or group name or 'all'")
    _ = parser.add_argument("--config", "-c", type=str, default=None, help="Config file path")
    _ = parser.add_argument("--no-confirm", "-x", action="store_true", help="Do not confirm the backup")
    _ = parser.add_argument("--verbose", "-v", action="store_true", help="Print verbose output")
    _ = parser.add_argument("--list-tasks", "-l", action="store_true", help="List available tasks")
    args = parser.parse_args()

    verbose = cast(bool, args.verbose)
    
    hostname = socket.gethostname()
    if sys.platform=="darwin" and ("-2" in hostname or "-3" in hostname):
        console.print(f"[bold red]Your local hostname became {hostname}, macOS has again changed it. Please fix this and run again![/bold red]")
        console.print("[red]Since macOS has lost the ability to change hostnames 'on the fly', you might need to reboot[/red]")
        exit(-1)

    xst = TasksSyncer()
    
    config: dict[str, SyncTask] = {}
    
    if args.config is not None:
        config_filename = cast(str, args.config)
        if '/' not in config_filename:
            config_filename = os.path.expanduser("~/.config/xra/" + config_filename)
        if config_filename.endswith('.json') is False:
            config_filename += '.json'
            
        if os.path.exists(config_filename):
            console.print(f"[dim]Reading configuration: {config_filename}[/dim]")
            config = xst.get_config(config_filename)
    else:
        config_filename = os.path.expanduser("~/.config/xra/config.json")
        if os.path.exists(config_filename):
            console.print(f"[dim]Reading configuration: {config_filename}[/dim]")
            config = xst.get_config(config_filename)
            
        filename = os.path.expanduser("~/.config/xra/" + hostname + ".json")
        if os.path.exists(filename):
            def_config = xst.get_config(filename)
            if def_config and def_config != {}:
                config.update(def_config)

        if config == {}:
            _ = xst.create_default_config(filename)
        
    # Collect all valid group names
    all_groups: set[str] = set()
    for t in config.values():
        if 'groups' in t and isinstance(t['groups'], list):
            for g in t['groups']:
                all_groups.add(g)

    task_name = cast(str, args.task_name)
    if task_name not in config and task_name != 'all' and task_name not in all_groups:
        console.print(f"[bold red]Error: Task or Group '{task_name}' not found in configuration.[/bold red]")
        console.print(f"Available tasks: {list(config.keys())}")
        if all_groups:
            console.print(f"Available groups: {list(all_groups)}")
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
        table = Table(title="Configured Backup Tasks", title_style="bold cyan", border_style="dim cyan")
        table.add_column("Task Name", style="bold green", no_wrap=True)
        table.add_column("Description", style="white")
        table.add_column("Source ➔ Destination", style="magenta")
        for name in task_names:
            task = config[name]
            table.add_row(
                name,
                task.get('description', ''),
                f"[yellow]{task['src']}[/yellow] ➔ [cyan]{task['dst']}[/cyan]"
            )
        console.print(table)
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
        
        console.print(f"\n[bold cyan]▶ Processing Task: {task_name}[/bold cyan]")
        if task.get('description'):
            console.print(f"[dim]{task['description']}[/dim]")
        
        if 'src' not in task or 'dst' not in task or 'delete' not in task or 'excludes' not in task or not task['src'] or not task['dst']:
            console.print(f"[bold red]❌ Task {task_name} is not properly configured. Skipping.[/bold red]")
            summary['error_tasks'] += 1
            summary['error_task_names'].append(task_name)
            continue

        with console.status(f"[bold yellow]Mounting drive for {task_name}...[/bold yellow]", spinner="dots"):
            mount_ok = xst.pre_post_cmds(task['mount_cmd'], verbose)
        if mount_ok is False:
            console.print(f"[bold red]❌ Task {task_name}: Mount failure[/bold red]")
            summary['error_tasks'] += 1
            summary['error_task_names'].append(task_name)
            continue
        
        dst = TasksSyncer.replace_mount_point(os.path.expanduser(task['dst']))
        if ':' not in dst:
            if not os.path.exists(dst):
                if verbose:
                    console.print(f"[dim]Destination directory {dst} does not exist[/dim]")
                console.print(f"[bold yellow]⚠ Skipped: Destination directory {dst} does not exist[/bold yellow]")
                summary['skipped_tasks'] += 1
                summary['skipped_task_names'].append(task_name)
                _ = xst.pre_post_cmds(task['umount_cmd'], verbose)
                continue
        else:
            dst_host = dst.split(':')[0]
            if dst_host == hostname:
                if verbose:
                    console.print(f"[dim]Destination directory {dst} is on the same host[/dim]")
                console.print(f"[bold yellow]⚠ Skipped: Destination {dst} is on the same host[/bold yellow]")
                summary['skipped_tasks'] += 1
                summary['skipped_task_names'].append(task_name)
                continue
                
        src = TasksSyncer.replace_mount_point(os.path.expanduser(task['src']))
        if ':' not in src:
            if not os.path.exists(src):
                if verbose:
                    console.print(f"[dim]Source directory {src} does not exist[/dim]")
                console.print(f"[bold yellow]⚠ Skipped: Source directory {src} does not exist[/bold yellow]")
                summary['skipped_tasks'] += 1
                summary['skipped_task_names'].append(task_name)
                _ = xst.pre_post_cmds(task['umount_cmd'], verbose)
                continue
        else:
            src_host = src.split(':')[0]
            if src_host == hostname:
                if verbose:
                    console.print(f"[dim]Source directory {src} is on the same host[/dim]")
                console.print(f"[bold yellow]⚠ Skipped: Source {src} is on the same host[/bold yellow]")
                summary['skipped_tasks'] += 1
                summary['skipped_task_names'].append(task_name)
                _ = xst.pre_post_cmds(task['umount_cmd'], verbose)
                continue

        if 'conditions' in task:
            with console.status(f"[bold yellow]Evaluating conditions for {task_name}...[/bold yellow]", spinner="dots"):
                cond_met = xst.eval_conditions(task, verbose)
            if cond_met is False:
                if verbose:
                    console.print(f"[dim]Conditions for task {task_name} are not met: {task['conditions']}[/dim]")
                console.print(f"[bold yellow]⚠ Skipped: Conditions not met ({task['conditions']})[/bold yellow]")
                summary['skipped_tasks'] += 1
                summary['skipped_task_names'].append(task_name)
                _ = xst.pre_post_cmds(task['umount_cmd'], verbose)
                continue

        # Check if remote connection requires interactive authentication
        use_spinner = True
        remote_host = None
        if ':' in task['src'] and '/' not in task['src'].split(':')[0]:
            remote_host = task['src'].split(':')[0]
        elif ':' in task['dst'] and '/' not in task['dst'].split(':')[0]:
            remote_host = task['dst'].split(':')[0]
            
        if remote_host:
            try:
                import subprocess
                res = subprocess.run(
                    ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=2", remote_host, "true"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3
                )
                if res.returncode != 0:
                    use_spinner = False
            except Exception:
                use_spinner = False

        dry_run_result = None
        if args.no_confirm is False:     
            if verbose:
                console.print(f"[dim]Dry run: {src} ➔ {dst}[/dim]")
            
            if use_spinner:
                with console.status("[bold yellow]Performing dry-run check...[/bold yellow]", spinner="dots") as status:
                    dry_run_state = {
                        'completed': 0,
                        'total': None,
                        'changes': 0,
                        'current_file': ''
                    }
                    
                    def dry_run_progress(event: dict[str, Any]) -> None:
                        if event['type'] == 'progress':
                            curr_file = event['current_file'] or ""
                            if curr_file:
                                dry_run_state['current_file'] = curr_file
                            
                            if event['to_chk_total'] is not None and event['to_chk_current'] is not None:
                                tot = event['to_chk_total']
                                chk = tot - event['to_chk_current']
                                if tot > 0 and chk >= 0:
                                    dry_run_state['total'] = max(dry_run_state['total'] or 0, tot)
                                    dry_run_state['completed'] = chk
                        elif event['type'] in ('file', 'delete'):
                            dry_run_state['changes'] += 1
                            dry_run_state['current_file'] = event['filename']
                        elif event['type'] == 'status':
                            dry_run_state['current_file'] = event['status']
                        elif event['type'] == 'stderr':
                            console.print(f"[red]{event['line']}[/red]")
                            return

                        # Format the status message
                        msg = "[bold yellow]Performing dry-run check...[/bold yellow]"
                        details = []
                        
                        if dry_run_state['total'] is not None:
                            pct = int(dry_run_state['completed'] / dry_run_state['total'] * 100) if dry_run_state['total'] > 0 else 100
                            details.append(f"{dry_run_state['completed']}/{dry_run_state['total']} checked ({pct}%)")
                        
                        if dry_run_state['changes'] > 0:
                            details.append(f"{dry_run_state['changes']} changes found")
                        
                        if details:
                            msg += f" ({', '.join(details)})"
                        
                        if dry_run_state['current_file']:
                            display_file = dry_run_state['current_file']
                            if len(display_file) > 40:
                                display_file = display_file[:18] + "..." + display_file[-19:]
                            msg += f" [dim][green]{display_file}[/green][/dim]"
                        
                        status.update(msg)

                    dry_run_result = xst.rsync_backup(
                        task['src'], task['dst'],
                        excludes=task['excludes'],
                        extensions=task['extensions'],
                        options=task['options'],
                        dry_run=True,
                        delete=task['delete'],
                        verbose=verbose,
                        progress_callback=dry_run_progress
                    )
            else:
                console.print("[bold yellow]Performing dry-run check (interactive auth may be required)...[/bold yellow]")
                
                def dry_run_static_progress(event: dict[str, Any]) -> None:
                    if event['type'] in ('file', 'delete'):
                        prefix = "[red]Delete:[/red]" if event['type'] == 'delete' else "[green]Change:[/green]"
                        console.print(f"  {prefix} {event['filename']}")
                    elif event['type'] == 'stderr':
                        console.print(f"[red]{event['line']}[/red]")

                dry_run_result = xst.rsync_backup(
                    task['src'], task['dst'],
                    excludes=task['excludes'],
                    extensions=task['extensions'],
                    options=task['options'],
                    dry_run=True,
                    delete=task['delete'],
                    verbose=verbose,
                    progress_callback=dry_run_static_progress
                )
            
            if dry_run_result['error']:
                console.print(Panel(f"[bold red]Dry-run error for task {task_name}:[/bold red]\n{dry_run_result['error']}", border_style="red"))
                _ = xst.pre_post_cmds(task['umount_cmd'], verbose)
                sys.exit(1)
                
            n_del = dry_run_result['deleted'] if dry_run_result['deleted'] is not None else (len(dry_run_result['delete_list']) if dry_run_result['delete_list'] else 0)
            num_changes = (dry_run_result['created'] or 0) + (dry_run_result['transferred'] or 0) + n_del
            
            # Display Dry-Run details
            dry_table = Table(title=f"Dry Run Details: {task_name}", title_style="bold green", show_header=True, border_style="dim green")
            dry_table.add_column("Statistic", style="bold cyan")
            dry_table.add_column("Value", style="magenta")
            dry_table.add_row("Total Files", f"{dry_run_result['files']}")
            dry_table.add_row("To Create", f"{dry_run_result['created']}")
            dry_table.add_row("To Transfer", f"{dry_run_result['transferred']}")
            dry_table.add_row("To Delete", f"{n_del}", style="red" if n_del > 0 else "")
            console.print(dry_table)
            
            if verbose:
                if dry_run_result['file_list']:
                    console.print("[yellow]Files to be transferred:[/yellow]")
                    for file in dry_run_result['file_list']:
                        console.print(f"  {file}")
                if dry_run_result['delete_list']:
                    console.print("---------------------------")
                    console.print("[red]Files to be deleted:[/red]")
                    for file in dry_run_result['delete_list']:
                        console.print(f"  {file}")
            
            if num_changes == 0:
                console.print("[bold green]✔ No changes detected. Task is up to date.[/bold green]")
                summary['ok_tasks'] += 1
                summary['ok_task_names'].append(task_name)
                _ = xst.pre_post_cmds(task['umount_cmd'], verbose)
                continue
                
            if n_del > 0:
                prompt_msg = f"[bold red]WARNING: THIS WILL DELETE {n_del} FILES![/bold red] Continue?"
            else:
                prompt_msg = "Continue?"
            
            ans = Confirm.ask(prompt_msg, default=True)
            if not ans:
                console.print(f"[bold yellow]⚠ Skipped task [green]{task_name}[/green] (cancelled by user)[/bold yellow]")
                summary['skipped_tasks'] += 1
                summary['skipped_task_names'].append(task_name)
                _ = xst.pre_post_cmds(task['umount_cmd'], verbose)
                continue

        # Execute the actual sync with progress updates
        progress = Progress(
            SpinnerColumn(spinner_name="dots"),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            TextColumn("[dim]{task.fields[current_file]}", justify="left")
        )
        
        task_id = progress.add_task(f"Syncing {task_name:<15}", total=None, current_file="")
        
        # Track overall progress state to prevent jumping bar length
        state: dict[str, Any] = {
            'completed': 0,
            'total': None,
            'has_to_chk': False,
            'processed_files': set()
        }
        
        # Pre-populate total count if dry run was executed and yielded files
        if args.no_confirm is False and dry_run_result and not dry_run_result.get('error'):
            if dry_run_result.get('files'):
                state['total'] = dry_run_result['files']
        
        def progress_callback(event: dict[str, Any]) -> None:
            if event['type'] == 'progress':
                curr_file = event['current_file'] or ""
                
                # Update task-wide completion if rsync to_chk fields are populated
                if event['to_chk_total'] is not None and event['to_chk_current'] is not None:
                    tot = event['to_chk_total']
                    chk = tot - event['to_chk_current']
                    if tot > 0 and chk >= 0:
                        state['has_to_chk'] = True
                        state['total'] = max(state['total'] or 0, tot)
                        state['completed'] = chk
                
                # Format fixed-width filename (exactly 30 chars)
                display_name = curr_file
                if len(display_name) > 30:
                    display_name = display_name[:13] + "..." + display_name[-14:]
                display_name = f"{display_name:<30}"
                
                # Format fixed-width percentage (exactly 6 chars)
                pct = event['percent']
                if pct is not None:
                    pct_str = f"({pct}%)"
                else:
                    pct_str = ""
                pct_str = f"{pct_str:>6}"
                
                # Format fixed-width completion (exactly 12 chars if total is known, e.g. " (120/120)  ")
                if state['total'] is not None:
                    count_str = f"({state['completed']}/{state['total']})"
                else:
                    count_str = ""
                count_str = f"{count_str:>12}"
                
                status_msg = f"[dim][green]{display_name}[/green] [cyan]{pct_str}[/cyan] [yellow]{count_str}[/yellow][/dim]"
                
                progress.update(
                    task_id,
                    completed=state['completed'],
                    total=state['total'],
                    current_file=status_msg
                )
            elif event['type'] == 'stderr':
                console.print(f"[red]{event['line']}[/red]")
            elif event['type'] == 'status':
                display_status = event['status']
                if len(display_status) > 30:
                    display_status = display_status[:13] + "..." + display_status[-14:]
                display_status = f"{display_status:<30}"
                
                if state['total'] is not None:
                    count_str = f"({state['completed']}/{state['total']})"
                else:
                    count_str = ""
                count_str = f"{count_str:>12}"
                
                status_msg = f"[dim][yellow]{display_status}[/yellow]        [yellow]{count_str}[/yellow][/dim]"
                progress.update(task_id, completed=state['completed'], total=state['total'], current_file=status_msg)
            elif event['type'] == 'file':
                curr_file = event['filename']
                if curr_file not in state['processed_files']:
                    state['processed_files'].add(curr_file)
                    # If we don't have to_chk info but have a total, increment step-by-step
                    if not state['has_to_chk'] and state['total'] is not None:
                        state['completed'] = min(state['completed'] + 1, state['total'])
                
                display_name = curr_file
                if len(display_name) > 30:
                    display_name = display_name[:13] + "..." + display_name[-14:]
                display_name = f"{display_name:<30}"
                
                if state['total'] is not None:
                    count_str = f"({state['completed']}/{state['total']})"
                else:
                    count_str = ""
                count_str = f"{count_str:>12}"
                
                # Align to 30 + 1 space + 6 space + 1 space + 12 = 50 visible chars
                status_msg = f"[dim][green]{display_name}[/green]        [yellow]{count_str}[/yellow][/dim]"
                
                progress.update(task_id, completed=state['completed'], total=state['total'], current_file=status_msg)
            elif event['type'] == 'delete':
                curr_file = event['filename']
                if curr_file not in state['processed_files']:
                    state['processed_files'].add(curr_file)
                    if not state['has_to_chk'] and state['total'] is not None:
                        state['completed'] = min(state['completed'] + 1, state['total'])
                
                display_name = curr_file
                if len(display_name) > 25:
                    display_name = display_name[:10] + "..." + display_name[-12:]
                display_name = f"Del: {display_name}"
                display_name = f"{display_name:<30}"
                
                if state['total'] is not None:
                    count_str = f"({state['completed']}/{state['total']})"
                else:
                    count_str = ""
                count_str = f"{count_str:>12}"
                
                status_msg = f"[dim][red]{display_name}[/red]        [yellow]{count_str}[/yellow][/dim]"
                
                progress.update(task_id, completed=state['completed'], total=state['total'], current_file=status_msg)

        if use_spinner:
            with progress:
                result = xst.rsync_backup(task['src'], task['dst'], excludes=task['excludes'], extensions=task['extensions'], options=task['options'], dry_run = False, delete = task['delete'], verbose=verbose, progress_callback=progress_callback)
                # Ensure the progress bar completes cleanly
                final_total = state['total'] if state['total'] is not None else 100
                progress.update(task_id, completed=final_total, total=final_total, current_file="Finished")
        else:
            console.print("[bold yellow]Syncing (interactive auth may be required)...[/bold yellow]")
            def sync_static_progress(event: dict[str, Any]) -> None:
                if event['type'] in ('file', 'delete'):
                    prefix = "[red]Delete:[/red]" if event['type'] == 'delete' else "[green]Syncing:[/green]"
                    console.print(f"  {prefix} {event['filename']}")
                elif event['type'] == 'stderr':
                    console.print(f"[red]{event['line']}[/red]")

            result = xst.rsync_backup(task['src'], task['dst'], excludes=task['excludes'], extensions=task['extensions'], options=task['options'], dry_run = False, delete = task['delete'], verbose=verbose, progress_callback=sync_static_progress)

        if result['error']:
            console.print(Panel(f"[bold red]Sync error for task {task_name}:[/bold red]\n{result['error']}", border_style="red"))
            summary['error_tasks'] += 1
            summary['error_task_names'].append(task_name)
            _ = xst.pre_post_cmds(task['umount_cmd'], verbose)
            continue
            
        if verbose:
            if result['file_list']:
                console.print("[yellow]Files transferred:[/yellow]")
                for file in result['file_list']:
                    console.print(f"  {file}")
            if result['delete_list']:
                console.print("[red]Files deleted:[/red]")
                for file in result['delete_list']:
                    console.print(f"  {file}")
                    
        n_del = result['deleted'] if result['deleted'] is not None else (len(result['delete_list']) if result['delete_list'] else 0)
        
        comp_table = Table(show_header=False, border_style="dim cyan")
        comp_table.add_row("Files Processed", f"{result['files']}")
        comp_table.add_row("Created", f"[green]{result['created']}[/green]")
        comp_table.add_row("Transferred", f"[yellow]{result['transferred']}[/yellow]")
        comp_table.add_row("Deleted", f"[red]{n_del}[/red]" if n_del > 0 else "0")
        console.print(f"[bold cyan]✔ Task {task_name} Completed Successfully[/bold cyan]")
        console.print(comp_table)
        
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
        summary_table = Table(title="Sync Run Summary", title_style="bold bright_magenta", border_style="bright_magenta")
        summary_table.add_column("Metric", style="bold cyan")
        summary_table.add_column("Result", style="white")
        
        ok_str = f"[bold green]{summary['ok_tasks']}[/bold green]"
        if summary['ok_task_names']:
            ok_str += f" ({', '.join(summary['ok_task_names'])})"
            
        skipped_str = f"[bold yellow]{summary['skipped_tasks']}[/bold yellow]"
        if summary['skipped_task_names']:
            skipped_str += f" ({', '.join(summary['skipped_task_names'])})"
            
        error_str = f"[bold red]{summary['error_tasks']}[/bold red]"
        if summary['error_task_names']:
            error_str += f" ({', '.join(summary['error_task_names'])})"

        summary_table.add_row("Successful Tasks", ok_str)
        summary_table.add_row("Skipped Tasks", skipped_str)
        summary_table.add_row("Failed Tasks", error_str)
        summary_table.add_row("Total Files Checked", f"{summary['files']}")
        summary_table.add_row("Total Created", f"[green]{summary['created']}[/green]")
        summary_table.add_row("Total Transferred", f"[yellow]{summary['transferred']}[/yellow]")
        summary_table.add_row("Total Deleted", f"[red]{summary['deleted']}[/red]" if summary['deleted'] > 0 else "0")
        
        console.print(summary_table)

if __name__ == "__main__":
    main()
