# Database Recovery & Monitoring

Operational procedures for when things go wrong. Read this when a query hangs, connections are exhausted, or system health is critical.

---

## Live Monitoring Commands

```bash
python -m shared.db_monitor status                              # connections + active queries
python -m shared.db_monitor monitor --auto-kill --max-query 45  # background auto-kill
python -m shared.db_monitor kill --max-seconds 30               # manual kill
python -m shared.system_monitor                                 # disk, memory, CPU, docker
```

---

## When a Query Hangs

1. `python -m shared.db_monitor status` — find the PID
2. `python -m shared.db_monitor kill` — cancel/terminate
3. If that fails: `psql "$DB_URL" -c "SELECT pg_terminate_backend(PID);"`
4. If that fails: `docker compose restart postgres`
5. **Fix the query before running it again**

---

## When Connections Are Exhausted

```bash
python -m shared.db_monitor status    # find what's holding connections
pkill -f "build_dataset.py"
pkill -f "run_strategy.py"
pkill -f "verify_integrity.py"
python -m shared.db_monitor status    # verify dropped
```

---

## Nuclear Reset

```bash
pkill -f "build_dataset.py" 2>/dev/null
pkill -f "run_strategy.py" 2>/dev/null
pkill -f "verify_integrity.py" 2>/dev/null
sleep 2
docker compose restart postgres
```

---

## Disk Recovery

```bash
docker builder prune --all -f    # reclaim build cache
docker image prune -f             # reclaim unused images
```

---

## System Health Thresholds

Configured in `shared/system_monitor.py`. Proportional thresholds (percentage of capacity). CRITICAL = stop all agents and resolve before continuing.
