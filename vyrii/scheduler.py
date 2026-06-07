"""Task scheduler — APScheduler + JSON persistence at ~/.vyrii/scheduler.json."""
from __future__ import annotations

import json
import subprocess
import threading
import uuid
import pathlib
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

_SCHED_FILE = pathlib.Path.home() / ".vyrii" / "scheduler.json"
_LOG_DIR    = pathlib.Path.home() / ".vyrii" / "scheduler_logs"
_scheduler: BackgroundScheduler | None = None


def _ensure_dirs():
    _SCHED_FILE.parent.mkdir(exist_ok=True)
    _LOG_DIR.mkdir(exist_ok=True)


def load_tasks() -> list:
    _ensure_dirs()
    if _SCHED_FILE.exists():
        try:
            return json.loads(_SCHED_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def save_tasks(tasks: list):
    _ensure_dirs()
    _SCHED_FILE.write_text(json.dumps(tasks, indent=2, ensure_ascii=False), encoding="utf-8")


def _run_task(task: dict):
    """Execute task command, write output to log file, update last_run."""
    _ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = _LOG_DIR / f"task_{task['id']}_{ts}.log"
    try:
        result = subprocess.run(
            task["command"], shell=True, capture_output=True, text=True, timeout=3600
        )
        output = result.stdout
        if result.stderr:
            output += "\n[STDERR]\n" + result.stderr
        status = "ok" if result.returncode == 0 else f"error({result.returncode})"
    except subprocess.TimeoutExpired:
        output = "[TIMEOUT] Task exceeded 3600s"
        status = "timeout"
    except Exception as e:
        output = f"[EXCEPTION] {e}"
        status = "exception"

    log_path.write_text(
        f"Task:    {task['name']}\n"
        f"Command: {task['command']}\n"
        f"Started: {ts}\n"
        f"Status:  {status}\n"
        f"{'─' * 60}\n{output}",
        encoding="utf-8",
    )

    tasks = load_tasks()
    for t in tasks:
        if t["id"] == task["id"]:
            t["last_run"] = ts
            t["last_status"] = status
            t["log_file"] = str(log_path)
    save_tasks(tasks)


def _make_trigger(task: dict):
    stype = task.get("schedule_type", "daily")
    h = task.get("hour", 9)
    m = task.get("minute", 0)
    if stype == "daily":
        return CronTrigger(hour=h, minute=m)
    elif stype == "weekly":
        return CronTrigger(day_of_week=task.get("day_of_week", "mon"), hour=h, minute=m)
    elif stype == "monthly":
        return CronTrigger(day=task.get("interval_value", 1), hour=h, minute=m)
    elif stype == "interval_minutes":
        return IntervalTrigger(minutes=int(task.get("interval_value", 60)))
    elif stype == "interval_hours":
        return IntervalTrigger(hours=int(task.get("interval_value", 1)))
    return CronTrigger(hour=h, minute=m)


def start_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        return
    _scheduler = BackgroundScheduler(daemon=True)
    for task in load_tasks():
        if task.get("enabled", True):
            try:
                _scheduler.add_job(
                    _run_task, _make_trigger(task), args=[task],
                    id=task["id"], replace_existing=True, misfire_grace_time=600,
                )
            except Exception:
                pass
    _scheduler.start()


def add_task(name: str, command: str, schedule_type: str,
             hour: int, minute: int, day_of_week: str,
             interval_value: int) -> dict:
    task = {
        "id":            str(uuid.uuid4()),
        "name":          name,
        "command":       command,
        "schedule_type": schedule_type,
        "hour":          int(hour),
        "minute":        int(minute),
        "day_of_week":   day_of_week,
        "interval_value": interval_value,
        "enabled":       True,
        "created":       datetime.now().isoformat(),
        "last_run":      None,
        "last_status":   None,
        "log_file":      None,
    }
    tasks = load_tasks()
    tasks.append(task)
    save_tasks(tasks)
    if _scheduler and _scheduler.running:
        try:
            _scheduler.add_job(
                _run_task, _make_trigger(task), args=[task],
                id=task["id"], replace_existing=True, misfire_grace_time=600,
            )
        except Exception:
            pass
    return task


def remove_task(task_id: str):
    save_tasks([t for t in load_tasks() if t["id"] != task_id])
    if _scheduler and _scheduler.running:
        try:
            _scheduler.remove_job(task_id)
        except Exception:
            pass


def toggle_task(task_id: str) -> bool:
    tasks = load_tasks()
    for t in tasks:
        if t["id"] == task_id:
            t["enabled"] = not t.get("enabled", True)
            enabled = t["enabled"]
            save_tasks(tasks)
            if _scheduler and _scheduler.running:
                try:
                    if enabled:
                        _scheduler.add_job(
                            _run_task, _make_trigger(t), args=[t],
                            id=task_id, replace_existing=True,
                        )
                    else:
                        _scheduler.remove_job(task_id)
                except Exception:
                    pass
            return enabled
    return False


def run_now(task_id: str):
    for t in load_tasks():
        if t["id"] == task_id:
            threading.Thread(target=_run_task, args=[t], daemon=True).start()
            return


def get_task_logs(task_id: str) -> list[pathlib.Path]:
    if not _LOG_DIR.exists():
        return []
    return sorted(
        [f for f in _LOG_DIR.iterdir() if f.name.startswith(f"task_{task_id}_")],
        reverse=True,
    )


def tasks_as_table(tasks: list) -> str:
    if not tasks:
        return "_No scheduled tasks yet. Use the form below to add one._"
    rows = [
        "| # | ID (prefix) | Name | Schedule | Last run | Status | On |",
        "|---|-------------|------|----------|----------|--------|----|",
    ]
    for i, t in enumerate(tasks, 1):
        stype = t.get("schedule_type", "daily")
        h, m = t.get("hour", 9), t.get("minute", 0)
        if stype == "daily":
            sched = f"Daily {h:02d}:{m:02d}"
        elif stype == "weekly":
            sched = f"Weekly {t.get('day_of_week','mon')} {h:02d}:{m:02d}"
        elif stype == "monthly":
            sched = f"Monthly day {t.get('interval_value',1)} {h:02d}:{m:02d}"
        else:
            sched = f"Every {t.get('interval_value','?')} {stype.split('_')[1]}"
        last   = t.get("last_run") or "never"
        status = t.get("last_status") or "—"
        on     = "✅" if t.get("enabled", True) else "⏸️"
        tid    = t["id"][:8]
        rows.append(f"| {i} | `{tid}` | {t['name']} | {sched} | {last} | {status} | {on} |")
    return "\n".join(rows)
