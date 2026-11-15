from flask import Flask, render_template, request, jsonify
import sqlite3
import os
import time
import uuid
import threading
from collections import defaultdict

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
DB_PATH = os.environ.get("EXAM_DB", "exam_scheduler.db")
SOLVER_WORKERS = int(os.environ.get("EXAM_SOLVER_WORKERS", str(max(1, min(8, os.cpu_count() or 1)))))

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (roll_no TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS faculty (faculty_id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rooms (room_no TEXT PRIMARY KEY, capacity INTEGER NOT NULL CHECK(capacity > 0));
CREATE TABLE IF NOT EXISTS subjects (subject_code TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS enrollments (
  roll_no TEXT NOT NULL REFERENCES students(roll_no),
  subject_code TEXT NOT NULL REFERENCES subjects(subject_code),
  PRIMARY KEY (roll_no, subject_code)
);
CREATE TABLE IF NOT EXISTS allocations (
  roll_no TEXT NOT NULL, subject_code TEXT NOT NULL, room_no TEXT NOT NULL, slot_id INTEGER NOT NULL,
  PRIMARY KEY (roll_no, subject_code),
  FOREIGN KEY (roll_no) REFERENCES students(roll_no),
  FOREIGN KEY (subject_code) REFERENCES subjects(subject_code),
  FOREIGN KEY (room_no) REFERENCES rooms(room_no)
);
CREATE TABLE IF NOT EXISTS invigilations (
  faculty_id TEXT NOT NULL, room_no TEXT NOT NULL, slot_id INTEGER NOT NULL,
  PRIMARY KEY (room_no, slot_id), UNIQUE (faculty_id, slot_id),
  FOREIGN KEY (faculty_id) REFERENCES faculty(faculty_id),
  FOREIGN KEY (room_no) REFERENCES rooms(room_no)
);
CREATE TABLE IF NOT EXISTS optimization_history (
  job_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  slots INTEGER NOT NULL,
  elapsed REAL NOT NULL,
  PRIMARY KEY (job_id, sequence)
);
CREATE TABLE IF NOT EXISTS optimization_job (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  job_id TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at REAL NOT NULL,
  elapsed REAL NOT NULL DEFAULT 0,
  slots INTEGER,
  best_found_elapsed REAL,
  best_bound REAL,
  allocated_students INTEGER NOT NULL DEFAULT 0,
  active_rooms INTEGER NOT NULL DEFAULT 0,
  optimal INTEGER NOT NULL DEFAULT 0,
  solver_status TEXT,
  message TEXT,
  error TEXT
);
"""

_job_lock = threading.RLock()
_job = None


def get_db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def init_db():
    global _job
    with get_db() as con:
        con.executescript(SCHEMA)
        row = con.execute("SELECT * FROM optimization_job WHERE singleton=1").fetchone()
        if row and row["status"] in ("RUNNING", "STOPPING"):
            elapsed = max(float(row["elapsed"] or 0), time.time() - float(row["started_at"]))
            con.execute(
                """UPDATE optimization_job SET status='INTERRUPTED', elapsed=?,
                   message='The server restarted while optimization was running. The best saved schedule is available.'
                   WHERE singleton=1""",
                (elapsed,),
            )
            row = con.execute("SELECT * FROM optimization_job WHERE singleton=1").fetchone()
    if row:
        restored = dict(row)
        restored["id"] = restored.pop("job_id")
        restored["started_monotonic"] = time.monotonic() - float(restored.get("elapsed") or 0)
        restored["stop_requested"] = False
        restored["solver"] = None
        restored["thread"] = None
        with _job_lock:
            _job = restored


def persist_job_snapshot(job):
    if not job:
        return
    fields = (
        job.get("id"), job.get("status"), job.get("started_at", time.time()),
        float(job.get("elapsed") or 0), job.get("slots"), job.get("best_found_elapsed"),
        job.get("best_bound"), int(job.get("allocated_students") or 0),
        int(job.get("active_rooms") or 0), int(bool(job.get("optimal"))),
        job.get("solver_status"), job.get("message"), job.get("error"),
    )
    with get_db() as con:
        con.execute(
            """INSERT INTO optimization_job
               (singleton, job_id, status, started_at, elapsed, slots, best_found_elapsed,
                best_bound, allocated_students, active_rooms, optimal, solver_status, message, error)
               VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(singleton) DO UPDATE SET
                 job_id=excluded.job_id, status=excluded.status, started_at=excluded.started_at,
                 elapsed=excluded.elapsed, slots=excluded.slots,
                 best_found_elapsed=excluded.best_found_elapsed, best_bound=excluded.best_bound,
                 allocated_students=excluded.allocated_students, active_rooms=excluded.active_rooms,
                 optimal=excluded.optimal, solver_status=excluded.solver_status,
                 message=excluded.message, error=excluded.error""",
            fields,
        )


def read_persisted_job():
    with get_db() as con:
        row = con.execute("SELECT * FROM optimization_job WHERE singleton=1").fetchone()
    if not row:
        return None
    data = dict(row)
    data["id"] = data.pop("job_id")
    data["started_monotonic"] = time.monotonic() - float(data.get("elapsed") or 0)
    data["stop_requested"] = data["status"] == "STOPPING"
    data["solver"] = None
    data["thread"] = None
    return data


def claim_job_record(job):
    """Atomically claim the single scheduler job slot across Flask processes."""
    fields = (
        job.get("id"), job.get("status"), job.get("started_at", time.time()),
        float(job.get("elapsed") or 0), job.get("slots"), job.get("best_found_elapsed"),
        job.get("best_bound"), int(job.get("allocated_students") or 0),
        int(job.get("active_rooms") or 0), int(bool(job.get("optimal"))),
        job.get("solver_status"), job.get("message"), job.get("error"),
    )
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        previous = con.execute(
            "SELECT status FROM optimization_job WHERE singleton=1"
        ).fetchone()
        if previous and previous["status"] in ("RUNNING", "STOPPING"):
            con.rollback()
            return False
        con.execute("DELETE FROM invigilations")
        con.execute("DELETE FROM allocations")
        con.execute("DELETE FROM optimization_history")
        con.execute(
            """INSERT INTO optimization_job
               (singleton, job_id, status, started_at, elapsed, slots, best_found_elapsed,
                best_bound, allocated_students, active_rooms, optimal, solver_status, message, error)
               VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(singleton) DO UPDATE SET
                 job_id=excluded.job_id, status=excluded.status, started_at=excluded.started_at,
                 elapsed=excluded.elapsed, slots=excluded.slots,
                 best_found_elapsed=excluded.best_found_elapsed, best_bound=excluded.best_bound,
                 allocated_students=excluded.allocated_students, active_rooms=excluded.active_rooms,
                 optimal=excluded.optimal, solver_status=excluded.solver_status,
                 message=excluded.message, error=excluded.error""",
            fields,
        )
    return True


# Load persisted optimization state when launched through Flask/Wsgi as well as python app.py.
init_db()


def get_optimization_history(job_id):
    if not job_id:
        return []
    with get_db() as con:
        rows = con.execute(
            "SELECT sequence, slots, elapsed FROM optimization_history WHERE job_id=? ORDER BY sequence",
            (job_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def validate_payload(data):
    required = ("students", "faculty", "rooms", "subjects", "enrollments")
    if not isinstance(data, dict) or any(k not in data for k in required):
        raise ValueError("JSON must contain students, faculty, rooms, subjects and enrollments arrays.")
    if any(not isinstance(data[k], list) for k in required):
        raise ValueError("Each of students, faculty, rooms, subjects and enrollments must be an array.")

    def ids(key, field):
        values = []
        for item in data[key]:
            if not isinstance(item, dict) or field not in item or str(item[field]).strip() == "":
                raise ValueError(f"Every {key} entry must contain {field}.")
            values.append(str(item[field]).strip())
        if len(values) != len(set(values)):
            raise ValueError(f"{field} values must be unique.")
        return set(values)

    student_ids = ids("students", "roll_no")
    faculty_ids = ids("faculty", "faculty_id")
    room_ids = ids("rooms", "room_no")
    subject_ids = ids("subjects", "subject_code")
    if not student_ids or not faculty_ids or not room_ids or not subject_ids:
        raise ValueError("Students, faculty, rooms and subjects cannot be empty.")
    for room in data["rooms"]:
        try:
            cap = int(room["capacity"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Room {room.get('room_no')} has an invalid capacity.")
        if cap <= 0:
            raise ValueError(f"Room {room['room_no']} must have positive capacity.")
    seen = set()
    for e in data["enrollments"]:
        if not isinstance(e, dict) or "roll_no" not in e or "subject_code" not in e:
            raise ValueError("Every enrollment must contain roll_no and subject_code.")
        pair = (str(e["roll_no"]).strip(), str(e["subject_code"]).strip())
        if pair[0] not in student_ids or pair[1] not in subject_ids:
            raise ValueError(f"Enrollment references an unknown student or subject: {pair}.")
        if pair in seen:
            raise ValueError(f"Duplicate enrollment: {pair}.")
        seen.add(pair)
    return data


def import_data(data):
    with get_db() as con:
        con.execute("DELETE FROM invigilations")
        con.execute("DELETE FROM allocations")
        con.execute("DELETE FROM enrollments")
        con.execute("DELETE FROM students")
        con.execute("DELETE FROM faculty")
        con.execute("DELETE FROM rooms")
        con.execute("DELETE FROM subjects")
        con.executemany("INSERT INTO students VALUES (?)", [(str(x["roll_no"]).strip(),) for x in data["students"]])
        con.executemany("INSERT INTO faculty VALUES (?,?)", [(str(x["faculty_id"]).strip(), str(x.get("name") or x["faculty_id"]).strip()) for x in data["faculty"]])
        con.executemany("INSERT INTO rooms VALUES (?,?)", [(str(x["room_no"]).strip(), int(x["capacity"])) for x in data["rooms"]])
        con.executemany("INSERT INTO subjects VALUES (?,?)", [(str(x["subject_code"]).strip(), str(x.get("name") or x["subject_code"]).strip()) for x in data["subjects"]])
        con.executemany("INSERT INTO enrollments VALUES (?,?)", [(str(x["roll_no"]).strip(), str(x["subject_code"]).strip()) for x in data["enrollments"]])


def get_problem_data():
    with get_db() as con:
        faculty = [dict(r) for r in con.execute("SELECT * FROM faculty ORDER BY faculty_id")]
        rooms = [dict(r) for r in con.execute("SELECT * FROM rooms ORDER BY capacity DESC, room_no")]
        subjects = [dict(r) for r in con.execute("SELECT * FROM subjects ORDER BY subject_code")]
        enrollment_map = defaultdict(set)
        for row in con.execute("SELECT roll_no, subject_code FROM enrollments"):
            enrollment_map[row["subject_code"]].add(row["roll_no"])
    active_subjects = [s["subject_code"] for s in subjects if enrollment_map[s["subject_code"]]]
    return faculty, rooms, subjects, enrollment_map, active_subjects


def pack_schedule(subject_slot, enrollment_map, rooms, faculty, staffed_room_count):
    seats_by_slot = defaultdict(list)
    for subject, slot in subject_slot.items():
        for roll in sorted(enrollment_map[subject]):
            seats_by_slot[slot].append((roll, subject))
    allocations, invigilations = [], []
    selected_rooms = rooms[:staffed_room_count]
    for slot, seats in sorted(seats_by_slot.items()):
        seats.sort(key=lambda item: (item[1], item[0]))
        if len(seats) > sum(r["capacity"] for r in selected_rooms):
            raise RuntimeError("Internal allocation check failed: slot exceeds staffed room capacity.")
        offset = 0
        active_rooms = []
        for room in selected_rooms:
            chunk = seats[offset:offset + room["capacity"]]
            offset += len(chunk)
            if not chunk:
                continue
            active_rooms.append(room)
            allocations.extend((roll, subject, room["room_no"], slot) for roll, subject in chunk)
        for room, f in zip(active_rooms, faculty):
            invigilations.append((f["faculty_id"], room["room_no"], slot))
    return allocations, invigilations


def persist_schedule(allocations, invigilations):
    # Each incumbent replaces the previous incumbent in one atomic transaction.
    with get_db() as con:
        con.execute("DELETE FROM invigilations")
        con.execute("DELETE FROM allocations")
        if allocations:
            con.executemany("INSERT INTO allocations VALUES (?,?,?,?)", allocations)
        if invigilations:
            con.executemany("INSERT INTO invigilations VALUES (?,?,?)", invigilations)


def get_schedule():
    with get_db() as con:
        rows = con.execute("""
          SELECT a.slot_id, a.subject_code, s.name AS subject_name, a.room_no,
                 r.capacity, a.roll_no, f.faculty_id, f.name AS faculty_name
          FROM allocations a
          JOIN subjects s ON s.subject_code=a.subject_code
          JOIN rooms r ON r.room_no=a.room_no
          LEFT JOIN invigilations i ON i.room_no=a.room_no AND i.slot_id=a.slot_id
          LEFT JOIN faculty f ON f.faculty_id=i.faculty_id
          ORDER BY a.slot_id, a.room_no, a.subject_code, a.roll_no
        """).fetchall()
    grouped = {}
    for row in rows:
        key = (row["slot_id"], row["room_no"])
        if key not in grouped:
            grouped[key] = {"slot_id": row["slot_id"], "room_no": row["room_no"],
                            "capacity": row["capacity"], "faculty_id": row["faculty_id"],
                            "faculty_name": row["faculty_name"], "subjects": {}, "students": []}
        grouped[key]["subjects"].setdefault(row["subject_code"], {
            "subject_code": row["subject_code"], "subject_name": row["subject_name"], "students": []
        })["students"].append(row["roll_no"])
        grouped[key]["students"].append(row["roll_no"])
    return list(grouped.values())


def build_model(cp_model, active_subjects, enrollment_map, slot_capacity):
    student_subjects = defaultdict(list)
    for subject, rolls in enrollment_map.items():
        for roll in rolls:
            student_subjects[roll].append(subject)
    conflicts = set()
    for subjects_for_student in student_subjects.values():
        for i, a in enumerate(subjects_for_student):
            for b in subjects_for_student[i + 1:]:
                if a != b:
                    conflicts.add(tuple(sorted((a, b))))

    n = len(active_subjects)
    model = cp_model.CpModel()
    x = {(s, t): model.NewBoolVar(f"x_{i}_{t}")
         for i, s in enumerate(active_subjects) for t in range(n)}
    used = [model.NewBoolVar(f"used_{t}") for t in range(n)]
    for s in active_subjects:
        model.Add(sum(x[s, t] for t in range(n)) == 1)
    for s in active_subjects:
        for t in range(n):
            model.Add(x[s, t] <= used[t])
    for a, b in conflicts:
        for t in range(n):
            model.Add(x[a, t] + x[b, t] <= 1)
    subject_size = {s: len(enrollment_map[s]) for s in active_subjects}
    for t in range(n):
        model.Add(sum(subject_size[s] * x[s, t] for s in active_subjects) <= slot_capacity * used[t])
    for t in range(n - 1):
        model.Add(used[t] >= used[t + 1])
    model.Minimize(sum(used))
    return model, x, used


class IncumbentCallback:
    """Callback adapter is created dynamically from CP-SAT's callback base class."""
    pass


def run_optimization(job_id, workers):
    global _job
    from ortools.sat.python import cp_model
    with _job_lock:
        started = _job["started_monotonic"] if _job and _job["id"] == job_id else time.monotonic()
    try:
        faculty, rooms, _subjects, enrollment_map, active_subjects = get_problem_data()
        if not active_subjects:
            persist_schedule([], [])
            with _job_lock:
                if _job and _job["id"] == job_id:
                    _job.update(status="COMPLETED", solver_status="OPTIMAL", optimal=True,
                                slots=0, best_found_elapsed=0.0, best_bound=0.0,
                                message="No subject has enrolled students.", elapsed=time.monotonic()-started)
                    persist_job_snapshot(_job)
            return
        if not faculty or not rooms:
            raise ValueError("At least one room and one faculty member are required.")
        staffed_room_count = min(len(rooms), len(faculty))
        slot_capacity = sum(r["capacity"] for r in rooms[:staffed_room_count])
        subject_size = {s: len(enrollment_map[s]) for s in active_subjects}
        too_large = [(s, n) for s, n in subject_size.items() if n > slot_capacity]
        if too_large:
            s, n = too_large[0]
            raise ValueError(f"Subject {s} has {n} students, but at most {slot_capacity} can be seated in one slot with the available rooms and invigilators.")

        model, x, used = build_model(cp_model, active_subjects, enrollment_map, slot_capacity)
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = max(1, int(workers or SOLVER_WORKERS))
        solver.parameters.log_search_progress = False

        callback_base = cp_model.CpSolverSolutionCallback
        class BestSolutionCallback(callback_base):
            def on_solution_callback(self):
                nonlocal started
                slot_count = int(round(self.ObjectiveValue()))
                with _job_lock:
                    current = _job if _job and _job["id"] == job_id else None
                    previous = current.get("slots") if current else None
                if previous is not None and slot_count >= previous:
                    return
                subject_slot = {}
                for subject in active_subjects:
                    for t in range(len(active_subjects)):
                        if self.Value(x[subject, t]):
                            subject_slot[subject] = t + 1
                            break
                allocations, invigilations = pack_schedule(subject_slot, enrollment_map, rooms, faculty, staffed_room_count)
                persist_schedule(allocations, invigilations)
                elapsed = time.monotonic() - started
                with _job_lock:
                    current = _job if _job and _job["id"] == job_id else None
                    if current is not None:
                        current.update(slots=slot_count, best_found_elapsed=elapsed,
                                       best_bound=self.BestObjectiveBound(), allocated_students=len(allocations),
                                       active_rooms=len(invigilations), solver_status="FEASIBLE",
                                       elapsed=elapsed)
                        persist_job_snapshot(current)
                        with get_db() as con:
                            sequence = con.execute(
                                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM optimization_history WHERE job_id=?",
                                (job_id,),
                            ).fetchone()[0]
                            con.execute(
                                "INSERT INTO optimization_history(job_id, sequence, slots, elapsed) VALUES(?,?,?,?)",
                                (job_id, sequence, slot_count, elapsed),
                            )

        callback = BestSolutionCallback()
        with _job_lock:
            if not _job or _job["id"] != job_id:
                return
            stop_was_requested = bool(_job.get("stop_requested"))
            if stop_was_requested:
                _job.update(status="STOPPED", solver_status="NOT_STARTED", optimal=False,
                            elapsed=time.monotonic()-started,
                            message="Optimization stopped before the search began; no feasible schedule was found.")
                persist_job_snapshot(_job)
                return
            _job["solver"] = solver
        status_code = solver.Solve(model, callback)
        status_name = solver.StatusName(status_code)
        elapsed = time.monotonic() - started
        with _job_lock:
            current = _job if _job and _job["id"] == job_id else None
            stop_requested = bool(current and current.get("stop_requested"))
        if status_code == cp_model.OPTIMAL:
            final_status = "COMPLETED"
            message = "Minimum number of slots proven optimal."
        elif stop_requested:
            final_status = "STOPPED"
            message = "Optimization stopped. The best feasible schedule found so far has been saved."
        elif status_code == cp_model.INFEASIBLE:
            final_status = "FAILED"
            message = "No valid schedule exists with the current rooms, capacities and faculty."
        elif status_code == cp_model.UNKNOWN:
            final_status = "STOPPED" if stop_requested else "FAILED"
            message = "Stopped before a feasible schedule was found; no new schedule is available." if stop_requested else "Search ended without finding a feasible schedule."
        else:
            final_status = "COMPLETED"
            message = "A feasible schedule was found; optimality was not proven."
        with _job_lock:
            if current is not None:
                current.update(status=final_status, solver_status=status_name, optimal=(status_code == cp_model.OPTIMAL),
                               best_bound=solver.BestObjectiveBound(), elapsed=elapsed, message=message,
                               solver=None)
                persist_job_snapshot(current)
    except Exception as exc:
        with _job_lock:
            if _job and _job["id"] == job_id:
                _job.update(status="FAILED", error=str(exc), message=str(exc),
                            elapsed=time.monotonic()-started, solver=None)
                persist_job_snapshot(_job)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data", methods=["POST"])
def api_import():
    global _job
    try:
        with _job_lock:
            persisted = read_persisted_job()
            if ((_job and _job.get("status") in ("RUNNING", "STOPPING")) or
                    (persisted and persisted.get("status") in ("RUNNING", "STOPPING"))):
                return jsonify({"ok": False, "error": "Stop the active optimization before importing new data."}), 409
        data = validate_payload(request.get_json(force=True))
        import_data(data)
        with get_db() as con:
            con.execute("DELETE FROM optimization_job WHERE singleton=1")
        with _job_lock:
            _job = None
        return jsonify({"ok": True, "message": "Initial data imported successfully."})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/schedule/start", methods=["POST"])
def api_start():
    global _job
    payload = request.get_json(silent=True) or {}
    try:
        workers = int(payload.get("workers") or SOLVER_WORKERS)
        if workers < 1 or workers > 64:
            raise ValueError("Workers must be between 1 and 64.")
        with _job_lock:
            if _job and _job.get("status") in ("RUNNING", "STOPPING"):
                return jsonify({"ok": False, "error": "An optimization is already running."}), 409
            job_id = uuid.uuid4().hex
            _job = {"id": job_id, "status": "RUNNING", "started_at": time.time(),
                    "started_monotonic": time.monotonic(), "elapsed": 0.0, "slots": None,
                    "best_found_elapsed": None, "best_bound": None,
                    "allocated_students": 0, "active_rooms": 0, "optimal": False,
                    "solver_status": None, "error": None,
                    "stop_requested": False, "solver": None,
                    "message": "Optimization is running."}
            if not claim_job_record(_job):
                _job = None
                return jsonify({"ok": False, "error": "An optimization is already running."}), 409
            thread = threading.Thread(target=run_optimization, args=(job_id, workers), daemon=True)
            _job["thread"] = thread
            thread.start()
        return jsonify({"ok": True, "job_id": job_id, "status": "RUNNING"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/schedule/status/<job_id>")
def api_status(job_id):
    with _job_lock:
        current = _job if _job and _job.get("id") == job_id else None
        if current:
            data = {k: v for k, v in current.items() if k not in ("solver", "thread", "started_monotonic")}
            data["elapsed"] = (time.monotonic() - current["started_monotonic"]
                               if current["status"] in ("RUNNING", "STOPPING") else current.get("elapsed", 0.0))
        else:
            current = read_persisted_job()
            if not current or current["id"] != job_id:
                return jsonify({"ok": False, "error": "Optimization job not found."}), 404
            data = {k: v for k, v in current.items() if k not in ("solver", "thread", "started_monotonic")}
    data["schedule"] = get_schedule() if data.get("slots") is not None else []
    data["history"] = get_optimization_history(data.get("id"))
    return jsonify({"ok": True, **data})


@app.route("/api/schedule/current")
def api_current_job():
    with _job_lock:
        current = _job
        if current is None:
            current = read_persisted_job()
        if current:
            data = {k: v for k, v in current.items() if k not in ("solver", "thread", "started_monotonic")}
            data["elapsed"] = (time.monotonic() - current["started_monotonic"]
                               if current["status"] in ("RUNNING", "STOPPING") else current.get("elapsed", 0.0))
        else:
            data = {"id": None, "status": "IDLE", "slots": None, "best_found_elapsed": None,
                    "best_bound": None, "elapsed": 0.0, "message": "No optimization has been run yet.",
                    "optimal": False}
    data["schedule"] = get_schedule()
    data["history"] = get_optimization_history(data.get("id"))
    return jsonify({"ok": True, **data})


@app.route("/api/schedule/stop/<job_id>", methods=["POST"])
def api_stop(job_id):
    with _job_lock:
        if not _job or _job["id"] != job_id:
            return jsonify({"ok": False, "error": "Optimization job not found."}), 404
        if _job["status"] not in ("RUNNING", "STOPPING"):
            return jsonify({"ok": True, "status": _job["status"], "message": "Optimization is already finished."})
        _job["stop_requested"] = True
        _job["status"] = "STOPPING"
        solver = _job.get("solver")
        persist_job_snapshot(_job)
        if solver is not None:
            solver.StopSearch()
    return jsonify({"ok": True, "status": "STOPPING", "message": "Stop requested."})


@app.route("/api/schedule/result/<job_id>")
def api_result(job_id):
    with _job_lock:
        if not _job or _job["id"] != job_id:
            return jsonify({"ok": False, "error": "Optimization job not found."}), 404
        status = _job["status"]
    return jsonify({"ok": True, "status": status, "schedule": get_schedule()})


@app.route("/api/schedule")
def api_schedule():
    return jsonify({"schedule": get_schedule()})


@app.route("/api/summary")
def api_summary():
    with get_db() as con:
        return jsonify({"students": con.execute("SELECT COUNT(*) FROM students").fetchone()[0],
                        "faculty": con.execute("SELECT COUNT(*) FROM faculty").fetchone()[0],
                        "rooms": con.execute("SELECT COUNT(*) FROM rooms").fetchone()[0],
                        "subjects": con.execute("SELECT COUNT(*) FROM subjects").fetchone()[0],
                        "enrollments": con.execute("SELECT COUNT(*) FROM enrollments").fetchone()[0]})


if __name__ == "__main__":
    init_db()
    app.run(debug=True, threaded=True)
