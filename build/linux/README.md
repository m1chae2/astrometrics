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
