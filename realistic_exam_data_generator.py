#!/usr/bin/env python3
"""
Generate realistic synthetic university examination data.

The JSON output remains compatible with the Flask + SQLite exam scheduler:
  students, faculty, rooms, subjects, enrollments

Elective model:
  1. Every cohort takes its own core subjects.
  2. A configurable fraction of each cohort's core subjects can be taken as
     electives by students in other cohorts in the same semester.
  3. Department electives are shared by programmes within their department.
     A configurable fraction is exposed as open electives to other departments.
  4. University open electives are shared across departments and programmes.
  5. Common courses are programme-specific: students in the same programme
     and semester share them across departments, but different programmes
     receive distinct subject codes and enrollments.
  6. Backlogs can come from earlier subjects across departments/programmes.

No third-party Python packages are required.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path


CORE_TOPICS = {
    "CSE": ["Data Structures", "Algorithms", "Database Systems", "Operating Systems",
            "Computer Networks", "Theory of Computation", "Compiler Design",
            "Computer Architecture", "Software Engineering", "Artificial Intelligence"],
    "ECE": ["Circuit Theory", "Digital Electronics", "Signals and Systems",
            "Analog Communication", "Digital Communication", "Microprocessors",
            "Electromagnetic Theory", "VLSI Design", "Embedded Systems", "Control Systems"],
    "EEE": ["Electrical Circuits", "Power Systems", "Electrical Machines",
            "Power Electronics", "Control Systems", "Electrical Measurements",
            "Signals and Systems", "Digital Electronics", "Energy Systems", "Drives"],
    "ME": ["Engineering Mechanics", "Thermodynamics", "Fluid Mechanics",
           "Manufacturing Processes", "Heat Transfer", "Machine Design",
           "Theory of Machines", "CAD/CAM", "Dynamics", "Materials Engineering"],
    "CE": ["Structural Analysis", "Geotechnical Engineering", "Fluid Mechanics",
           "Surveying", "Concrete Technology", "Transportation Engineering",
           "Environmental Engineering", "Hydrology", "Construction Management", "RCC Design"],
    "CHE": ["Chemical Process Principles", "Fluid Flow", "Heat Transfer",
            "Mass Transfer", "Reaction Engineering", "Process Control",
            "Thermodynamics", "Separation Processes", "Plant Design", "Process Equipment"],
    "AI": ["Python for AI", "Machine Learning", "Deep Learning", "Data Mining",
           "Probabilistic Models", "Computer Vision", "Natural Language Processing",
           "Reinforcement Learning", "Optimization", "AI Systems"],
    "DS": ["Probability and Statistics", "Data Analytics", "Data Mining",
           "Machine Learning", "Big Data Systems", "Data Visualization",
           "Statistical Learning", "Database Systems", "Optimization", "Predictive Modeling"],
}

GENERIC_TOPICS = [
    "Research Methodology", "Mathematical Methods", "Advanced Computing",
    "Optimization Techniques", "Numerical Methods", "Applied Statistics",
    "Professional Communication", "Technical Writing", "Computational Methods",
]

DEPARTMENT_ELECTIVES = {
    "CSE": ["Cloud Computing", "Distributed Systems", "Information Security",
            "Mobile Computing", "High Performance Computing", "Blockchain",
            "Information Retrieval", "Advanced Databases", "Edge Computing", "Quantum Computing"],
    "ECE": ["Wireless Networks", "Antenna Engineering", "Optical Communication",
            "IoT Systems", "Radar Systems", "Nanoelectronics", "RF Circuit Design",
            "Satellite Communication", "Wireless Sensor Networks", "Advanced VLSI"],
    "EEE": ["Smart Grids", "Electric Vehicles", "Renewable Energy",
            "Power System Protection", "HVDC Transmission", "Energy Storage",
            "Industrial Automation", "FACTS Devices", "Microgrids", "Power Quality"],
    "ME": ["Robotics", "Additive Manufacturing", "Computational Fluid Dynamics",
           "Automotive Engineering", "Mechatronics", "Finite Element Methods",
           "Advanced Materials", "Refrigeration", "Aerospace Engineering", "Tribology"],
    "CE": ["Earthquake Engineering", "Building Information Modeling", "Pavement Design",
           "Remote Sensing", "Ground Improvement", "Urban Planning",
           "Bridge Engineering", "Sustainable Construction", "Water Resources", "GIS Applications"],
    "CHE": ["Polymer Technology", "Biochemical Engineering", "Petroleum Refining",
            "Membrane Technology", "Process Intensification", "Corrosion Engineering",
            "Industrial Catalysis", "Green Processes", "Pharmaceutical Engineering", "Safety Engineering"],
    "AI": ["Explainable AI", "Generative AI", "Graph Neural Networks",
           "Large Language Models", "Speech Processing", "Medical AI",
           "AI for Robotics", "Multimodal Learning", "AI Ethics", "MLOps"],
    "DS": ["Time Series Analysis", "Causal Inference", "Bayesian Statistics",
           "Recommender Systems", "Text Analytics", "Graph Analytics",
           "Data Engineering", "Survival Analysis", "Experimental Design", "Forecasting"],
}

OPEN_ELECTIVES = [
    "Environmental Sustainability", "Innovation and Entrepreneurship",
    "Cyber Ethics", "Intellectual Property Rights",
    "Economics for Engineers", "Disaster Management",
    "Introduction to Psychology", "Technology and Society",
    "Science Communication", "Financial Literacy",
]

COMMON_COURSES = [
    "Research Methodology", "Professional Communication",
    "Engineering Mathematics", "Technical Writing",
]


def csv_list(value: str) -> list[str]:
    return [x.strip().upper() for x in value.split(",") if x.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate realistic synthetic university examination data."
    )
    p.add_argument("--output", default="realistic_exam_data.json")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--departments", default="CSE,ECE,EEE,ME,CE")
    p.add_argument(
        "--cohort-structure",
        default="BTECH:1=40,3=40,5=40;MTECH:1=40,3=40,5=40",
        help="Format: 'BTECH:1=80,3=75,5=70;MTECH:1=30,2=25'",
    )
    p.add_argument(
        "--student-variation", type=float, default=0.05,
        help="Random +/- percentage around each cohort size; 0 means exact sizes.",
    )

    p.add_argument("--core-subjects", type=int, default=4,
                   help="Default core subjects per cohort; overridden by --cohort-loads.")
    p.add_argument(
        "--cohort-loads", default="",
        help=("Per-cohort overrides. Format: "
              "'CSE/BTECH/1:core=4,core_elective=1,department_elective=2,"
              "cross_department_elective=1,open_elective=1;"
              "ECE/MTECH/1:core=5,department_elective=1'. "
              "Unspecified values use the global options.")
    )
    p.add_argument(
        "--core-elective-share", type=float, default=0.25,
        help="Fraction of each cohort's core subjects offered to other cohorts (0-1).",
    )
    p.add_argument(
        "--core-electives-per-student", type=int, default=1,
        help="Number of other-cohort core subjects each student selects.",
    )

    p.add_argument("--department-elective-pool", type=int, default=5)
    p.add_argument(
        "--department-electives-per-student", type=int, default=2,
        help="Number of own-department electives each student selects.",
    )
    p.add_argument(
        "--cross-department-elective-share", type=float, default=0.50,
        help="Fraction of each department's electives exposed to other departments as open electives.",
    )
    p.add_argument(
        "--cross-department-electives-per-student", type=int, default=1,
        help="Number of other-department electives each student selects as open electives.",
    )

    p.add_argument("--open-elective-pool", type=int, default=4)
    p.add_argument("--open-electives-per-student", type=int, default=1)
    p.add_argument(
        "--common-courses", type=int, default=1,
        help="Number of programme-specific common courses per programme and semester.",
    )

    p.add_argument("--rooms", type=int, default=30)
    p.add_argument("--faculty", type=int, default=36)
    p.add_argument("--min-room-capacity", type=int, default=30)
    p.add_argument("--max-room-capacity", type=int, default=100)
    p.add_argument(
        "--elective-section-size", type=int, default=0,
        help="If >0, own-department electives are selected by groups of this size.",
    )

    p.add_argument("--backlog-rate", type=float, default=0.10)
    p.add_argument("--max-backlogs", type=int, default=2)
    p.add_argument("--cross-programme-backlog-share", type=float, default=0.20)
    return p


def parse_cohort_structure(value: str) -> dict[str, dict[int, int]]:
    """Parse e.g. BTECH:1=80,3=75;MTECH:1=30,2=25."""
    structure: dict[str, dict[int, int]] = {}
    try:
        for programme_part in value.split(";"):
            programme_part = programme_part.strip()
            if not programme_part:
                continue
            programme, semester_part = programme_part.split(":", 1)
            programme = programme.strip().upper()
            if not programme or programme in structure:
                raise ValueError
            semester_counts: dict[int, int] = {}
            for item in semester_part.split(","):
                semester_text, count_text = item.split("=", 1)
                semester, count = int(semester_text.strip()), int(count_text.strip())
                if semester < 1 or count < 1 or semester in semester_counts:
                    raise ValueError
                semester_counts[semester] = count
            if not semester_counts:
                raise ValueError
            structure[programme] = semester_counts
    except (ValueError, TypeError):
        raise ValueError(
            "Invalid --cohort-structure. Expected "
            "'BTECH:1=80,3=75;MTECH:1=30,2=25'."
        )
    if not structure:
        raise ValueError("--cohort-structure must define at least one programme.")
    return structure



def parse_cohort_loads(value: str) -> dict[tuple[str, int], dict[str, int]]:
    """Parse programme/semester load overrides, independent of department."""
    result = {}
    allowed = {
        "core", "core_elective", "department_elective",
        "cross_department_elective", "open_elective",
    }
    if not value.strip():
        return result
    try:
        for item in value.split(";"):
            key, settings = item.strip().split(":", 1)
            programme, semester = [x.strip().upper() for x in key.split("/")]
            cohort_key = (programme, int(semester))
            if not programme or cohort_key[1] < 1 or cohort_key in result:
                raise ValueError
            values = {}
            for setting in settings.split(","):
                name, count = setting.strip().split("=", 1)
                name, count = name.strip().lower(), int(count.strip())
                if name not in allowed or count < 0 or name in values:
                    raise ValueError
                values[name] = count
            result[cohort_key] = values
    except (ValueError, TypeError):
        raise ValueError(
            "Invalid --cohort-loads. Expected "
            "'BTECH/1:core=4,core_elective=1,department_elective=2'."
        )
    return result


def generate(args: argparse.Namespace) -> dict:
    rng = random.Random(args.seed)
    departments = csv_list(args.departments)
    cohort_structure = parse_cohort_structure(args.cohort_structure)
    programmes = list(cohort_structure)
    semesters = sorted({s for m in cohort_structure.values() for s in m})
    cohort_loads = parse_cohort_loads(args.cohort_loads)

    def load_for(dept: str, programme: str, sem: int, key: str) -> int:
        defaults = {
            "core": args.core_subjects,
            "core_elective": args.core_electives_per_student,
            "department_elective": args.department_electives_per_student,
            "cross_department_elective": args.cross_department_electives_per_student,
            "open_elective": args.open_electives_per_student,
        }
        return cohort_loads.get((programme, sem), {}).get(key, defaults[key])

    if not departments:
        raise ValueError("--departments cannot be empty.")
    valid_programme_semesters = {(programme, sem) for programme, sem_counts in cohort_structure.items() for sem in sem_counts}
    unknown_load_keys = set(cohort_loads) - valid_programme_semesters
    if unknown_load_keys:
        invalid = ", ".join(f"{programme}/{sem}" for programme, sem in sorted(unknown_load_keys))
        raise ValueError(f"--cohort-loads contains programme/semester combinations not in --cohort-structure: {invalid}")
    if args.student_variation < 0 or args.student_variation >= 1:
        raise ValueError("--student-variation must be in [0, 1).")
    if args.core_subjects < 1 or args.department_elective_pool < 0:
        raise ValueError("Core subject count must be positive; elective pool cannot be negative.")
    if args.open_elective_pool < 0 or args.common_courses < 0:
        raise ValueError("Open elective and common course counts cannot be negative.")
    if args.core_electives_per_student < 0:
        raise ValueError("--core-electives-per-student cannot be negative.")
    if args.department_electives_per_student > args.department_elective_pool:
        raise ValueError("Own-department electives per student exceeds the department elective pool.")
    if args.open_electives_per_student > args.open_elective_pool:
        raise ValueError("Open electives per student exceeds the open elective pool.")
    for name, value in [
        ("--core-elective-share", args.core_elective_share),
        ("--cross-department-elective-share", args.cross_department_elective_share),
        ("--backlog-rate", args.backlog_rate),
        ("--cross-programme-backlog-share", args.cross_programme_backlog_share),
    ]:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be between 0 and 1.")
    if min(args.rooms, args.faculty) < 1:
        raise ValueError("Rooms and faculty must be positive.")
    if args.min_room_capacity < 1 or args.max_room_capacity < args.min_room_capacity:
        raise ValueError("Invalid room capacity range.")
    if args.max_backlogs < 0 or args.elective_section_size < 0:
        raise ValueError("Backlogs and elective section size cannot be negative.")

    students = []
    subjects_by_code = {}
    enrollment_pairs = set()
    cohort_records = []
    student_counter = 0

    def add_subject(
        code: str, name: str, department: str, semester: int, kind: str,
        programme: str = "ALL", cohort_id: str = "", offered_as: str = "",
    ) -> str:
        if code not in subjects_by_code:
            subjects_by_code[code] = {
                "subject_code": code,
                "name": name,
                "department": department,
                "semester": semester,
                "type": kind,
                "programme": programme,
                "cohort": cohort_id,
                "offered_as": offered_as or kind,
            }
        return code

    def enroll(roll: str, code: str):
        enrollment_pairs.add((roll, code))

    # Cohort populations, with independent percentage variation.
    for dept in departments:
        for programme, semester_counts in cohort_structure.items():
            for sem, base_size in sorted(semester_counts.items()):
                delta = int(base_size * args.student_variation)
                size = max(1, base_size + rng.randint(-delta, delta)) if delta else base_size
                cohort_id = f"{dept}-{programme}-S{sem:02d}"
                rolls = []
                for _ in range(size):
                    student_counter += 1
                    roll = f"{dept}{programme[:2]}{sem:02d}{student_counter:06d}"
                    rolls.append(roll)
                    students.append({
                        "roll_no": roll,
                        "name": f"Student {student_counter}",
                        "department": dept,
                        "programme": programme,
                        "semester": sem,
                        "cohort": cohort_id,
                    })
                cohort_records.append({
                    "cohort_id": cohort_id,
                    "department": dept,
                    "programme": programme,
                    "semester": sem,
                    "students": rolls,
                })

    # Course catalogue keyed by owner cohort / department / programme.
    cohort_core = {}
    cohort_department_electives = {}
    department_electives = {}
    programme_common = {}
    semester_open = {}

    # Core subjects belong to a specific cohort.
    for cohort in cohort_records:
        dept, programme, sem = cohort["department"], cohort["programme"], cohort["semester"]
        cohort_id = cohort["cohort_id"]
        topic_pool = CORE_TOPICS.get(dept, GENERIC_TOPICS)
        codes = []
        for j in range(load_for(dept, programme, sem, "core")):
            topic = topic_pool[(sem + j - 1) % len(topic_pool)]
            code = f"{dept}-{programme}-S{sem:02d}-C{j+1:02d}"
            codes.append(add_subject(
                code, topic, dept, sem, "core", programme, cohort_id, "core"
            ))
        cohort_core[cohort_id] = codes

    # Department electives belong to a department + semester and are available
    # to every programme in that department at that semester.
    for dept in departments:
        for sem in semesters:
            codes = []
            topics = DEPARTMENT_ELECTIVES.get(dept, GENERIC_TOPICS)
            for j in range(args.department_elective_pool):
                topic = topics[(sem + j - 1) % len(topics)]
                code = f"DE-{dept}-S{sem:02d}-E{j+1:02d}"
                codes.append(add_subject(
                    code, topic, dept, sem, "department_elective",
                    "ALL", "", "department_elective"
                ))
            department_electives[(dept, sem)] = codes

    # Programme common courses are intentionally separated by programme.
    # Departments sharing a programme share its common course; BTECH and MTECH
    # receive distinct subject codes, even when course titles coincide.
    for programme in programmes:
        for sem in semesters:
            codes = []
            for j in range(args.common_courses):
                topic = COMMON_COURSES[(sem + j - 1) % len(COMMON_COURSES)]
                code = f"COM-{programme}-S{sem:02d}-C{j+1:02d}"
                codes.append(add_subject(
                    code, topic, "UNIVERSITY", sem, "common",
                    programme, "", "programme_common"
                ))
            programme_common[(programme, sem)] = codes

    # University-wide open elective courses, shared across all programmes.
    for sem in semesters:
        codes = []
        for j in range(args.open_elective_pool):
            topic = OPEN_ELECTIVES[(sem + j - 1) % len(OPEN_ELECTIVES)]
            code = f"OE-S{sem:02d}-{j+1:02d}"
            codes.append(add_subject(
                code, topic, "INTERDISCIPLINARY", sem, "open_elective",
                "ALL", "", "university_open_elective"
            ))
        semester_open[sem] = codes

    # Decide which core courses from each cohort are available to other cohorts.
    core_elective_offerings = {}
    for cohort in cohort_records:
        codes = cohort_core[cohort["cohort_id"]]
        count = int(round(len(codes) * args.core_elective_share))
        if args.core_elective_share > 0 and count == 0 and codes:
            count = 1
        core_elective_offerings[cohort["cohort_id"]] = (
            rng.sample(codes, min(count, len(codes)))
        )

    # Decide which department electives are exposed to other departments.
    cross_department_offerings = {}
    for dept in departments:
        for sem in semesters:
            codes = department_electives[(dept, sem)]
            count = int(round(len(codes) * args.cross_department_elective_share))
            if args.cross_department_elective_share > 0 and count == 0 and codes:
                count = 1
            cross_department_offerings[(dept, sem)] = (
                rng.sample(codes, min(count, len(codes)))
            )

    # Enrollment by cohort.
    for cohort in cohort_records:
        dept = cohort["department"]
        programme = cohort["programme"]
        sem = cohort["semester"]
        cohort_id = cohort["cohort_id"]
        rolls = cohort["students"]

        # Own core: mandatory for every student.
        for code in cohort_core[cohort_id]:
            for roll in rolls:
                enroll(roll, code)

        # Own department electives: students select a subset. Optionally, choices
        # are made by sections so classmates in a section share electives.
        own_dept_pool = department_electives[(dept, sem)]
        own_dept_count = load_for(dept, programme, sem, "department_elective")
        if own_dept_count > len(own_dept_pool):
            raise ValueError(
                f"{cohort_id} requests {own_dept_count} department electives, "
                f"but the pool contains only {len(own_dept_pool)}."
            )
        if args.elective_section_size > 0:
            shuffled = rolls[:]
            rng.shuffle(shuffled)
            sections = [
                shuffled[i:i + args.elective_section_size]
                for i in range(0, len(shuffled), args.elective_section_size)
            ]
            for section in sections:
                chosen = rng.sample(own_dept_pool, own_dept_count)
                for roll in section:
                    for code in chosen:
                        enroll(roll, code)
        else:
            for roll in rolls:
                for code in rng.sample(own_dept_pool, own_dept_count):
                    enroll(roll, code)

        # Core subjects from other cohorts, offered as cross-cohort electives.
        other_core_pool = []
        for other in cohort_records:
            if other["cohort_id"] == cohort_id or other["semester"] != sem:
                continue
            other_core_pool.extend(core_elective_offerings[other["cohort_id"]])
        other_core_pool = list(dict.fromkeys(other_core_pool))
        core_elective_count = load_for(dept, programme, sem, "core_elective")
        if core_elective_count and other_core_pool:
            k = min(core_elective_count, len(other_core_pool))
            for roll in rolls:
                for code in rng.sample(other_core_pool, k):
                    enroll(roll, code)

        # Department electives from other departments become open electives.
        cross_dept_pool = []
        for other_dept in departments:
            if other_dept == dept:
                continue
            cross_dept_pool.extend(cross_department_offerings[(other_dept, sem)])
        cross_dept_pool = list(dict.fromkeys(cross_dept_pool))
        cross_dept_count = load_for(dept, programme, sem, "cross_department_elective")
        if cross_dept_count and cross_dept_pool:
            k = min(cross_dept_count, len(cross_dept_pool))
            for roll in rolls:
                for code in rng.sample(cross_dept_pool, k):
                    enroll(roll, code)

        # University-wide open electives.
        open_pool = semester_open[sem]
        open_count = load_for(dept, programme, sem, "open_elective")
        if open_count > len(open_pool):
            raise ValueError(
                f"{cohort_id} requests {open_count} university open electives, "
                f"but the pool contains only {len(open_pool)}."
            )
        for roll in rolls:
            for code in rng.sample(open_pool, open_count):
                enroll(roll, code)

        # Common courses: common within a programme + semester, not across programmes.
        for code in programme_common[(programme, sem)]:
            for roll in rolls:
                enroll(roll, code)

    # Backlog catalogue: generate earlier core, department elective, programme
    # common and university open subjects. A student can carry a subject from
    # any department/programme, subject to it being from an earlier semester.
    max_semester = max(semesters)
    historical_subjects_by_sem = {}
    for past_sem in range(1, max_semester):
        past_codes = []
        for dept in departments:
            topic_pool = CORE_TOPICS.get(dept, GENERIC_TOPICS)
            elective_topics = DEPARTMENT_ELECTIVES.get(dept, GENERIC_TOPICS)
            for programme in programmes:
                historical_core_count = load_for(dept, programme, past_sem, "core")
                for j in range(historical_core_count):
                    topic = topic_pool[(past_sem + j - 1) % len(topic_pool)]
                    code = f"{dept}-{programme}-S{past_sem:02d}-C{j+1:02d}"
                    past_codes.append(add_subject(
                        code, topic, dept, past_sem, "core", programme,
                        f"{dept}-{programme}-S{past_sem:02d}", "core"
                    ))
                for j in range(args.department_elective_pool):
                    topic = elective_topics[(past_sem + j - 1) % len(elective_topics)]
                    code = f"DE-{dept}-S{past_sem:02d}-E{j+1:02d}"
                    past_codes.append(add_subject(
                        code, topic, dept, past_sem, "department_elective",
                        "ALL", "", "department_elective"
                    ))
            for j in range(args.common_courses):
                for programme in programmes:
                    topic = COMMON_COURSES[(past_sem + j - 1) % len(COMMON_COURSES)]
                    code = f"COM-{programme}-S{past_sem:02d}-C{j+1:02d}"
                    past_codes.append(add_subject(
                        code, topic, "UNIVERSITY", past_sem, "common",
                        programme, "", "programme_common"
                    ))
        for j in range(args.open_elective_pool):
            topic = OPEN_ELECTIVES[(past_sem + j - 1) % len(OPEN_ELECTIVES)]
            code = f"OE-S{past_sem:02d}-{j+1:02d}"
            past_codes.append(add_subject(
                code, topic, "INTERDISCIPLINARY", past_sem, "open_elective",
                "ALL", "", "university_open_elective"
            ))
        historical_subjects_by_sem[past_sem] = list(dict.fromkeys(past_codes))

    # Backlogs are assigned after current enrollment. They can originate in
    # any department/programme; own-programme subjects are preferred according
    # to --cross-programme-backlog-share.
    if args.backlog_rate > 0 and args.max_backlogs > 0:
        for cohort in cohort_records:
            dept, programme, sem = cohort["department"], cohort["programme"], cohort["semester"]
            eligible = []
            own = []
            other = []
            for past_sem, codes in historical_subjects_by_sem.items():
                if past_sem >= sem:
                    continue
                for code in codes:
                    subj = subjects_by_code[code]
                    is_own = (
                        (subj["department"] == dept and subj["programme"] in (programme, "ALL"))
                        or subj["department"] == "INTERDISCIPLINARY"
                    )
                    (own if is_own else other).append(code)
            eligible = list(dict.fromkeys(own + other))
            if not eligible:
                continue
            for roll in cohort["students"]:
                if rng.random() >= args.backlog_rate:
                    continue
                count = rng.randint(1, min(args.max_backlogs, len(eligible)))
                own_pool, other_pool = list(dict.fromkeys(own)), list(dict.fromkeys(other))
                selected = []
                for _ in range(count):
                    choose_other = bool(other_pool) and (
                        not own_pool or rng.random() < args.cross_programme_backlog_share
                    )
                    pool = other_pool if choose_other else own_pool
                    if not pool:
                        pool = other_pool if other_pool else own_pool
                    if not pool:
                        break
                    code = rng.choice(pool)
                    selected.append(code)
                    pool.remove(code)
                for code in selected:
                    enroll(roll, code)

    used_codes = {code for _, code in enrollment_pairs}
    subjects = [s for code, s in subjects_by_code.items() if code in used_codes]
    enrollments = [
        {"roll_no": roll, "subject_code": code}
        for roll, code in sorted(enrollment_pairs)
    ]

    rooms = [
        {"room_no": f"R{i:03d}",
         "capacity": rng.randint(args.min_room_capacity, args.max_room_capacity)}
        for i in range(1, args.rooms + 1)
    ]
    faculty = [
        {"faculty_id": f"F{i:03d}", "name": f"Faculty {i:03d}"}
        for i in range(1, args.faculty + 1)
    ]

    data = {
        "students": students,
        "faculty": faculty,
        "rooms": rooms,
        "subjects": subjects,
        "enrollments": enrollments,
        "metadata": {
            "generator": "realistic_exam_data_generator.py",
            "seed": args.seed,
            "cohort_count": len(cohort_records),
            "departments": departments,
            "programmes": programmes,
            "semesters": semesters,
            "cohort_structure": {
                programme: {str(sem): count for sem, count in sorted(counts.items())}
                for programme, counts in cohort_structure.items()
            },
            "student_variation": args.student_variation,
            "elective_model": {
                "core": "mandatory within owning cohort; core count can be overridden per cohort",
                "cross_cohort_core_electives": "a configurable fraction of each cohort's core subjects is offered to other cohorts in the same semester",
                "department_electives": "shared by programmes within the owning department",
                "cross_department_open_electives": "a configurable fraction of department electives is offered to other departments",
                "university_open_electives": "shared across departments and programmes",
                "common_courses": "shared across departments within the same programme and semester; distinct between programmes",
            },
            "elective_settings": {
                "programme_semester_load_overrides": {
                    f"{programme}/{sem}": settings
                    for (programme, sem), settings in cohort_loads.items()
                },
                "core_elective_share": args.core_elective_share,
                "core_electives_per_student": args.core_electives_per_student,
                "cross_department_elective_share": args.cross_department_elective_share,
                "cross_department_electives_per_student": args.cross_department_electives_per_student,
                "department_electives_per_student": args.department_electives_per_student,
                "open_electives_per_student": args.open_electives_per_student,
                "common_courses_per_programme_semester": args.common_courses,
            },
            "backlog_rate": args.backlog_rate,
            "max_backlogs_per_student": args.max_backlogs,
            "cross_programme_backlog_share": args.cross_programme_backlog_share,
        },
    }
    return data


def print_report(data: dict, output: Path):
    subject_by_code = {s["subject_code"]: s for s in data["subjects"]}
    student_by_roll = {s["roll_no"]: s for s in data["students"]}
    enrollments_per_subject = Counter(e["subject_code"] for e in data["enrollments"])
    enrollments_per_student = Counter(e["roll_no"] for e in data["enrollments"])
    backlog_count = sum(
        student_by_roll[e["roll_no"]]["semester"] > subject_by_code[e["subject_code"]]["semester"]
        for e in data["enrollments"]
    )
    type_counts = Counter(s["type"] for s in data["subjects"])

    print(f"Generated: {output}")
    print(f"Students: {len(data['students']):,}")
    print(f"Subjects: {len(data['subjects']):,}")
    print(f"Enrollments: {len(data['enrollments']):,}")
    print(f"Backlog enrollments: {backlog_count:,}")
    print(f"Rooms: {len(data['rooms']):,}; Faculty: {len(data['faculty']):,}")
    print(f"Cohorts: {data['metadata']['cohort_count']:,}")
    print("Subjects by type:", dict(sorted(type_counts.items())))
    if enrollments_per_subject:
        counts = sorted(enrollments_per_subject.values())
        print(f"Students per subject: min={counts[0]}, median={counts[len(counts)//2]}, max={counts[-1]}")
    if enrollments_per_student:
        counts = sorted(enrollments_per_student.values())
        print(f"Subjects per student: min={counts[0]}, median={counts[len(counts)//2]}, max={counts[-1]}")


def main():
    args = build_parser().parse_args()
    data = generate(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print_report(data, output)


if __name__ == "__main__":
    main()
