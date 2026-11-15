# Realistic University Exam Data Generator

Generates synthetic university data compatible with the existing Flask + SQLite exam scheduler. No third-party Python packages are needed.

## Elective structure

- **Core subjects:** Mandatory for the students in their owning cohort. A configurable fraction can also be selected by students in other cohorts in the same semester.
- **Department electives:** Shared by programmes within the same department and semester.
- **Cross-department electives:** A configurable fraction of a department's electives is made available to students from other departments as open electives.
- **University open electives:** Shared across all departments and programmes.
- **Common courses:** Programme-specific. For example, BTECH common courses are shared across departments offering BTECH, but are separate subject records from MTECH common courses.
- **Backlogs:** Can be from any earlier department/programme subject.

## Example

```bash
python realistic_exam_data_generator.py \
  --departments CSE,ECE,EEE,ME,CE \
  --cohort-structure 'BTECH:1=80,3=75,5=70;MTECH:1=30,2=25' \
  --student-variation 0.05 \
  --core-subjects 4 \
  --core-elective-share 0.25 \
  --core-electives-per-student 1 \
  --department-elective-pool 5 \
  --department-electives-per-student 2 \
  --cross-department-elective-share 0.50 \
  --cross-department-electives-per-student 1 \
  --open-elective-pool 4 \
  --open-electives-per-student 1 \
  --common-courses 1 \
  --backlog-rate 0.10 \
  --max-backlogs 2 \
  --cross-programme-backlog-share 0.20 \
  --rooms 60 --faculty 70
```

## Important options

| Argument | Meaning |
|---|---|
| `--cohort-structure` | Programme-specific semesters and base cohort sizes |
| `--student-variation` | Percentage variation around each configured cohort size; `0` means exact |
| `--core-elective-share` | Fraction of a cohort's core subjects offered to other cohorts |
| `--core-electives-per-student` | Number of other-cohort core subjects each student chooses |
| `--cross-department-elective-share` | Fraction of each department's electives exposed to other departments |
| `--cross-department-electives-per-student` | Number of other-department electives each student chooses |
| `--department-electives-per-student` | Number of own-department electives each student chooses |
| `--open-electives-per-student` | Number of university-wide open electives each student chooses |
| `--common-courses` | Number of common subjects per programme and semester |

Use `--help` to see every option.

## Reproducibility and import

The same `--seed` and arguments produce the same dataset. Upload the generated JSON in the existing scheduler. Extra metadata on students and subjects is descriptive; the scheduler's core arrays and identifiers remain present.

## Per-cohort course-load overrides

Use `--cohort-loads` to override the number of core subjects and each selected elective type for a programme/semester combination, applied identically to every department in that programme and semester. The key format is `PROGRAMME/SEMESTER`. Separate cohorts with `;`; separate settings with `,`.

```bash
python realistic_exam_data_generator.py \
  --cohort-structure 'BTECH:1=80,3=75;MTECH:1=30,2=25' \
  --cohort-loads 'BTECH/1:core=5,core_elective=1,department_elective=2,cross_department_elective=1,open_elective=1;MTECH/1:core=6,department_elective=1,open_elective=2'
```

Supported load keys:

| Key | Meaning |
|---|---|
| `core` | Mandatory core subjects for that cohort |
| `core_elective` | Other cohorts' core subjects selected per student |
| `department_elective` | Own-department electives selected per student |
| `cross_department_elective` | Other departments' department electives selected per student |
| `open_elective` | University-wide open electives selected per student |

Each override applies to all departments sharing that programme and semester. Any omitted key falls back to its existing global argument (for example, `--core-subjects` or `--open-electives-per-student`). Programme/semester combinations not listed also use all global defaults. Pool sizes and offering shares remain controlled by the global pool/share options.