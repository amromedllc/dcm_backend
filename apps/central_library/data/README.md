# Progressly Central Library catalog

`progressly_master_catalog.json` contains all Programs and Targets cells from
`Progressly_Master_Program_Library_Organized.xlsx`, plus its filename and SHA-256.
The workbook's guide sheets are not executable import instructions.

From the project root, export a revised workbook using a Python environment
with `openpyxl`:

```bash
python3 tools/export_central_library_workbook.py /path/to/workbook.xlsx apps/backend/apps/central_library/data/progressly_master_catalog.json
```

From `apps/backend`, validate without changing the database:

```bash
venv/bin/python manage.py import_central_catalog apps/central_library/data/progressly_master_catalog.json
```

To replace the public catalog, supply `--replace --backup-file /absolute/new/path.json`.
The backup parent directory must exist. The command locks the three catalog
tables, saves a Django JSON fixture of folders/programs/targets, then replaces
them in one transaction. Backup failure or any write failure leaves the old
catalog intact. Backup files are never overwritten. Uploaded image files are
not deleted. Existing organization/client programs and their collected data
are not updated; their old source IDs are not remapped to the new catalog.
The catalog contents are replaced, but matching workbook source IDs are updated
in place. Migration 0015 backfills source IDs from existing import metadata,
preserving program/target database IDs and facility source links. Records absent
from the new workbook are removed; unrelated shared settings are retained.

Mapping:

- Clinical domain becomes both the folder and treatment area.
- `behavior_monitoring` becomes `behavior_reduction`; other source program
  types become `skill_acquisition`, retaining their original type in tags
  and instructions.
- Library status is active so drafts are discoverable. Program phase is
  on hold, with a Draft template tag, because the workbook supplies no
  individualized teaching or mastery criteria.
- Opportunities/trial-by-trial use discrete-trial percent correct; frequency
  uses discrete-trial frequency; duration uses total observed duration.
- Source prompt levels become target starter prompting schemes. No automatic
  fading or mastery workflows are invented. Source response options and
  settings are retained in instructions rather than treated as DCM features.
- Task analysis/fidelity have no step lists in this workbook. These and the
  unsupported latency, rating scale, and momentary time sampling modes become
  instruction-only targets, explicitly labeled Requires configuration.
  This affects 38 programs / 114 targets. Configure their recording settings
  in the organization copy before clinical use.
- Every nonempty source field is retained as labeled plain text in the
  corresponding program/target instructions, including stable workbook IDs.
  The JSON also preserves blank fields. Duplicate program titles are distinct
  entries and linked by their workbook IDs.

The workbook importer links treatment areas, tags, and inline prompt levels to
reusable `CentralProgramSetting` records. It does not create facility-owned
settings during catalog replacement. Superadmins manage the six supported
setting kinds through Central Settings and attach them to programs/targets.

A facility import copies linked settings and workflow status dependencies into
that facility, remaps the program/target foreign keys and custom-field values,
and records provenance in `CentralSettingCopy`. Further imports reuse the
facility copy without overwriting local edits. Different TPMS facilities in
one organization receive separate copies. Existing same-name settings with
different configuration get a distinct central-import name; existing status
keys are reused within the facility. Defaults for unrelated facility programs
are never replaced. Custom fields are enabled only for the imported templates.

Program imports, single-target imports, and folder imports are atomic. A target
can override program prompting, workflow, and starting status. Step definitions
can reference central prompting/workflow `setting_ids`; facility IDs are not
accepted in central step definitions. The source workbook contains no automatic
workflows or fading criteria, so none are invented during backfill.

API additions (authenticated under `/api/v1`):
- `GET/POST /superadmin/central-settings` and `PUT /superadmin/central-settings/{id}`.
- `GET /central-settings` for users with Central Library view permission.
- Central program/target create and update accept `setting_ids` and typed
  `configuration`; responses expose these fields.
- Central setting payload: `kind`, `name`, `configuration`, `dependency_ids`,
  `is_active`. Workflows link central target-status dependencies. Setting kind
  and target-status keys cannot be changed after creation.

Apply migrations `0013` and `0014` to the shared schema before using this feature.
Migration `0014` links existing catalog content without changing its target count.

On this local database, migration `0012_centralprogram_prompting_levels` was
already applied but its model field/migration file were missing from the
checkout. They are restored so inserts and backups match the database.


## Corrected workbook (v2)

`progressly_corrected_catalog.json` is the validated export of
`outputs/Progressly_Central_Library_Validated.xlsx` in the project root. The
original corrected workbook had ten duplicate caregiver-training tag links;
the validated copy removes those duplicates and records each change in Review
Notes. It contains 163 programs, 489 targets, 31 workbook settings, 592 unique
program-setting links, no target overrides, and 788 review-note rows.

The exporter detects the three additional settings sheets and emits
`progressly-workbook-v2`. All three must be supplied together:

- Central Settings: stable `setting_id`, `kind`, `name`, valid object-valued
  `configuration_json`, pipe-separated `dependency_setting_ids`, and
  `review_status` (`ready_for_import` or `needs_clinician_review`).
- Program Setting Links: `program_id`, `setting_id`.
- Target Setting Links: `target_id`, `setting_id` (an empty sheet is valid).

Programs and Targets may carry typed `configuration_json`. Review Notes are
preserved in the export as source data. Settings are validated with the same
schemas as the central-settings API. Unknown, duplicate, wrong-kind, unreviewed,
or incompatible references fail before any database mutation. Custom-field
configuration keys reference workbook setting IDs, which are mapped to central
IDs at import and to facility IDs when a facility imports the program.

Program prompting is inherited unless a target has an explicit override;
legacy prompt labels do not create a second template that shadows that link.
Legacy Yes/No outcomes still receive a starter outcome template when needed.
Unsupported recording modes remain instruction-only drafts. No automatic
workflows or fading thresholds are invented.

Example dry run from `apps/backend`:

```bash
venv/bin/python manage.py import_central_catalog apps/central_library/data/progressly_corrected_catalog.json
```

Apply with `--replace --backup-file /absolute/new/backup.json` after validation.
The backup includes central setting definitions, dependencies, catalog links,
and facility-copy provenance. Existing facility-owned settings are unchanged.
