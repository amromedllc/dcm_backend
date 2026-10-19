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
This is a full replacement, not an upsert. Repeating it creates new database IDs.

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

The importer does not create organization-owned TreatmentArea, ProgramTag,
WorkflowTemplate, or PromptingTemplate rows. The Central Library's existing
copy-to-organization flow creates target prompting templates when copied.

On this local database, migration `0012_centralprogram_prompting_levels` was
already applied but its model field/migration file were missing from the
checkout. They are restored so inserts and backups match the database.
