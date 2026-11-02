# Linux scripts

Helper scripts for building, running and checking Astrometrics on Linux.

## run_backend.sh

Starts, stops and checks the Python backend. Run it with `help` to see every command.

### dump

```bash
build/linux/run_backend.sh dump
```

Use this when the backend stops answering. The backend writes the Python stack of every thread to its log file, and the script prints those new lines. The stacks show what each thread is waiting on. It needs no `sudo`.

If the backend is not running, the script says so and exits with an error.

### Memory limit

`start`, `launch` and `restart` run the backend inside a systemd user scope with a memory limit. Siril and other child processes start in the same scope, so the limit covers them. If the backend grows past the hard limit, the kernel's out-of-memory killer stops a process in that scope instead of letting the whole desktop run out of memory. This happened on 2026-09-25, when the backend grew to 15 to 22 GB and the desktop session was killed.

| Limit | Default | What happens above it |
| --- | --- | --- |
| `MemoryHigh` | 80% of RAM | The kernel slows the backend down and reclaims memory first. Nothing is killed. |
| `MemoryMax` | 90% of RAM | The kernel kills a process in the scope. |

The largest real use on record is 26.7 GB plus 2.1 GB of swap, seen in the restack of 2026-10-11 on this 32 GB machine. That is 82% of the RAM. The limit sits above that peak and leaves about 3 GB for the desktop. The 15 to 22 GB growth was a bug, since fixed, and is not a size to plan for.

To change a limit, set an environment variable before you start the backend. Values are systemd sizes such as `24G`. The value `off` turns the limit off.

```bash
ASTROMETRICS_BACKEND_MEMORY_MAX=24G build/linux/run_backend.sh restart
ASTROMETRICS_BACKEND_MEMORY_MAX=off build/linux/run_backend.sh restart
```

Without a systemd user session, the backend starts with no limit and the script prints one line saying so. `foreground` has no limit.

To see whether the kernel killed the backend, run `build/linux/run_backend.sh status`. After a kill it prints "The kernel's out-of-memory killer stopped a process of the backend" with the scope name. The details are in the user journal: `journalctl --user -u <scope name>`. The scope name is kept in `.run_pids/backend.scope`. A kill of a child such as Siril, not the backend itself, shows up as a failed stack and the same journal line.

The helper functions are in `backend_memory_limit.sh`.
