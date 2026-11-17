# Changelog Drafts for Live Review

Add matching release notes here when updating docs/changelog-notepad.md. This
file ships with the backend. On /admin/superadmin/changelog choose Import feature
drafts, review and edit the imported drafts, then choose Publish when ready.
Imports also match titles across all dates or matching body text even when titles
and dates differ. Case, spacing and HTML formatting differences are ignored.
Older matches are linked to their source ID without changing content. If both
title and content are rewritten before the first import, review manually; the
importer cannot infer that unrelated wording describes the same change.
Imports never publish and never overwrite existing source IDs, even after the
superadmin edits a title or date. Keep IDs stable. To revise an existing entry,
edit it in the superadmin page. A deleted entry can be imported again.

Format: ## unique-source-id | YYYY-MM-DD | Title, then Status: Draft and
**New Features**, **Improvements**, or **Fixed Issues** sections with - bullets.
Use user-facing copy only; exclude patient data, secrets and unverified claims.


Entries below retain their original release dates and are ordered newest day first.
Multiple changes on one day remain separate drafts for individual review.

## changelog-feature-drafts | 2026-11-17 | Import release-note drafts
Status: Draft

**New Features**
- Superadmins can import bundled release-note drafts on the Changelog page,
  review and edit them, then publish to What's New when ready.
- Imports skip existing notes by source ID, matching title on any date, or
  matching content even when the title and date differ. Existing edits and
  publication status are preserved.
- The separate source is `apps/backend/apps/central_library/data/changelog-notepad.md`.
- Requires deployment and a backend migration; no live content was changed.

## facility-ai-access | 2026-11-16 | Enforce facility AI access
Status: Draft

**Fixed Issues**
- AI drafting requires an explicitly enabled linked facility as well as the
  server AI setting and configured key. Users without a facility link are denied.
- Web AI availability is scoped to the signed-in session and refreshed on focus,
  mount, and every 15 seconds while active, so facility changes reach open pages.
  Failed availability checks hide AI controls. Server checks enforce denials
  immediately, including for already-open drafting panels.

## trial-cards-scan | 2026-11-14 | Easier-to-scan trial cards
Status: Draft

**Improvements**
- Trial counts and Undo share a compact card header, with less empty space.
- Response choices have quieter borders, clear labels and visible keyboard focus.
- The current-level marker appears only for automatic prompt fading, so a new
  target no longer looks like a response has already been selected.

## 2026-11-13-html-reference-recording-controls | 2026-11-13 | HTML reference recording controls
Status: Draft

**Improvements**
- Prompt level buttons show their configured label alongside the score or
  abbreviation, with softer theme-aware backgrounds and larger touch areas.
- Trial targets show an empty history message and a labeled Undo action using
  the existing trial deletion flow. The discard confirmation remains in place.
- Session titles and header controls wrap on smaller screens.

## 2026-11-13-recording-dialog-reference-layout | 2026-11-13 | Recording dialog reference layout
Status: Draft

**Improvements**
- Trials, correct responses and accuracy have clearly separated summary cells.
- Completed session notes share a compact row with their form links.
- Optional details has a quiet secondary row; submission guidance appears beside
  the footer actions. Recording controls retain their configured clinical labels.

## 2026-11-13-clearer-recording-notes-and-actions | 2026-11-13 | Clearer recording notes and actions
Status: Draft

**Improvements**
- Completed note links wrap side by side instead of filling separate rows.
- Optional details and session actions share a compact bottom toolbar.
- Note template controls wrap on small screens and have an accessible label.

## 2026-11-13-compact-session-recording-footer | 2026-11-13 | Compact session recording footer
Status: Draft

**Improvements**
- Submit Session and Discard session now share a compact bottom action row,
  leaving more room for recording instead of using full-width button rows.

## 2026-11-13-session-notes-stay-visible-while-recording | 2026-11-13 | Session notes stay visible while recording
Status: Draft

**Improvements**
- Session note forms and linked notes now appear directly above Additional
  session details, without needing to expand it, in popup and full-page views.
- Additional session details is now a compact button beside the notes on wider
  screens. It opens supervision and media in a separate dialog and wraps below
  the notes on smaller screens.

## 2026-11-13-clients-and-providers-now-also-auto-sync-on-login | 2026-11-13 | Clients and providers now also auto-sync on login
Status: Draft

**New Features**
- Logging in now also refreshes the facility's client and provider rosters
  from TherapyPMS automatically, not just appointments — but only once per
  facility per day, since rosters change far less often than appointments
  and a full re-pull on every login would be unnecessary TherapyPMS load.

## 2026-11-13-deleted-therapypms-appointments-no-longer-linger-in-progressly | 2026-11-13 | Deleted TherapyPMS appointments no longer linger in Progressly
Status: Draft

**Fixed Issues**
- An appointment deleted or cancelled in TherapyPMS now gets marked
  cancelled in Progressly the next time appointments sync, instead of
  staying stuck as "Scheduled" forever and reappearing on staff schedules.
- Previously, Progressly's sync only added or updated appointments it saw
  from TherapyPMS — it never noticed when one had disappeared, so it kept
  showing as an open, recordable session even after being deleted upstream.

## 2026-11-13-delete-manual-sessions-permission-gated | 2026-11-13 | Delete manual sessions (permission-gated)
Status: Draft

**New Features**
- A manually-entered session can now be deleted even after it's been submitted
  or approved, from the session's "View" screen — not just while still open.
- This requires a new, separate privilege ("Delete Manual Sessions (any
  status)") so it has to be granted on top of the existing Delete Sessions
  privilege; admins have it by default, staff and supervisors don't.
- Deleting a submitted/approved manual session requires typing a reason, and
  is recorded in the audit log with who did it and why.

## 2026-11-13-session-note-old-form-builder-completion-tracking | 2026-11-13 | Session Note: Old Form Builder completion tracking
Status: Draft

**Fixed Issues**
- A session note filled out and submitted through Old Form Builder
  (TherapyPMS) now shows up correctly afterward, with a link to view it —
  previously it could show "Completed" when it wasn't, or disappear entirely
  with no link once submitted.
- The "Add a session note?" prompt after submitting a session no longer asks
  again if the Session Note (Old Form Builder or the new form builder) was
  already filled in.
- "Session Setups" (pick a reusable program selection to start a session) is
  now a button that opens a popup instead of a card that was always open and
  taking up space on the page.

**Improvements**
- Trial-rating buttons on the recording screen (0 / 1 / 2, etc.) now show a
  bigger number with a "Press N" hint, matching the newer design.

## 2026-11-13-probe-available-as-an-organization-default | 2026-11-13 | Probe available as an organization default
Status: Draft

**Fixed Issues**
- Added a tenant migration to restore missing Probe statuses and make existing
  Probe rows organization defaults, visible in Settings across its practices.
- New organizations receive Probe even when its platform template is missing.
- Existing Probe labels, colors, active/staff visibility choices and clinical
  references are preserved. Other practice-owned custom statuses remain private.
- Requires backend migration/deployment; no shared or production database was changed.

## 2026-11-13-responsive-web-layout-improvements | 2026-11-13 | Responsive web layout improvements
Status: Draft

**Fixed Issues**
- Headers and shared card actions wrap in narrower viewports.
- Form controls, client forms, portal navigation and recording grids fit available space.
- Settings menus stay within viewport width; sheets scroll to keep content reachable.
- Existing design and clinical behavior are preserved. Native Windows scaling and
  authenticated workflow acceptance remain pending; see `docs/responsive-layout.md`.

## 2026-11-12-session-note-dropdown-shows-form-builder-specific-templates | 2026-11-12 | Session note dropdown shows form builder-specific templates
Status: Draft

**New Features**
- `/templates/custom` endpoint returns templates based on facility's form_builder_mode:
  - **New form builder**: Custom templates created by superadmin (NoteTemplate records)
  - **Old form builder**: Assigned templates fetched from TherapyPMS API
  - **Both**: Templates from both sources
- Session note dropdown on `/clients/{clientId}/sessions` now shows templates filtered by form builder mode.
- Superadmin can create custom form templates via `/templates/custom` page for new form builder.
- Old form builder templates are automatically fetched from TherapyPMS and shown in the dropdown.

## 2026-11-12-custom-templates-follow-the-selected-form-builder | 2026-11-12 | Custom Templates follow the selected form builder
Status: Draft

**Fixed Issues**
- Custom Templates loads the facility's builder setting before requesting templates.
  Old-only mode no longer requests the disabled New Builder's uploaded templates.
- Both mode provides separate New Form Builder and Old Form Builder tabs.
- Failure to load the policy shows a retry action instead of assuming New Builder.
- Old Builder now lists assigned session-note forms for the signed-in employee
  and opens the returned form links. Empty assignments and connection errors show
  clear messages with a refresh action.
- Old Builder explicitly requests JSON responses and reports the upstream HTTP
  status with safe guidance instead of a generic loading failure.
- Documented token expiry, token reuse, and employee eligibility rejections now
  show their specific reason without exposing other response contents.

## 2026-11-12-superadmin-facility-integration-setup | 2026-11-12 | Superadmin facility integration setup
Status: Draft

**New Features**
- Superadmins can connect TherapyPMS on behalf of a facility from the new
  Facility Integrations screen, without asking facility users to configure it.
- Added the missing Facility Integrations link beneath Practice Email in the
  Super Admin menu.
- Search mapped facilities, see connection status and the last appointment sync,
  and reconnect or disconnect a selected facility.
- Credentials must match the selected facility. Saved passwords are never shown;
  connection changes are recorded in the audit log.
- The connection serves all facility users. Appointment sync uses the existing
  automatic login flow when a facility user next signs in.

## 2026-11-12-workflows-use-configured-target-statuses | 2026-11-12 | Workflows use configured target statuses
Status: Draft

**Fixed Issues**
- Add/Edit Workflow selects existing target statuses instead of typing phase names.
  Phase names and colors follow Settings → Statuses.
- Duplicate phase statuses and unknown status references are blocked before saving.
  Additional acquisition steps must select a configured status.
- Selected status keys are retained when reopening workflows; maintenance reverts
  to the selected first acquisition status. Existing workflows without this setting
  retain their Acquisition fallback.
- Probe, mastery, and maintenance retain their dedicated clinical behavior.

## 2026-11-12-visible-client-favorite-hearts | 2026-11-12 | Visible client favorite hearts
Status: Draft

**Fixed Issues**
- The client selector always shows the favorite heart, including before hovering.
  Favorites have a filled heart; other clients have an outline heart.

## 2026-11-12-facility-appointment-sync-on-every-user-login | 2026-11-12 | Facility appointment sync on every user login
Status: Draft

**Fixed Issues**
- Every user login queues appointments for that user’s connected TherapyPMS facility,
  across admin, supervisor, staff, caregiver, and reporting roles. No prior manual
  pull or employee/client ID is required to schedule the sync.
- Automatic pulls include one year of history and the next 90 days.
- Integrations opens the logged-in admin’s facility ahead of a previously saved selection.
- Automatic sync requires the Celery worker; additional history can still be pulled manually.

## 2026-11-11-automatic-target-opening-validation | 2026-11-11 | Automatic target opening validation
Status: Draft

**Improvements**
- Workflow settings reject invalid automatic-opening options.
- Automatic opening runs when a target enters Mastered; later maintenance or
  closure does not open another batch. All waiting targets works directly from
  the saved direction setting.

## 2026-11-11-live-graph-and-analytics-updates | 2026-11-11 | Live graph and analytics updates
Status: Draft

**Improvements**
- Client analytics, unfrozen progress reports, and organization analytics refresh
  automatically when relevant recorded data changes. Graphs continue to use the
  existing permission-checked data sources.
- Client and organization analytics show the live connection state and provide
  pause/resume controls. Connections pause when the page is hidden or locked,
  reconnect automatically, and fall back to periodic refresh when unavailable.
- Report drafts and frozen report data are preserved during live updates.

**Known limitations (Setup)**
- Live delivery requires Redis and streaming support through the server/proxy.
  The backend Docker configuration now uses threaded Gunicorn workers.
- Implementation is awaiting testing; no tests or type-check were run for this
  change, as requested. This entry has not been published in-app.

## 2026-11-11-persisted-target-observation-periods-for-rate-recording | 2026-11-11 | Persisted target observation periods for rate recording
Status: Draft

**Improvements**
- Rate recording on the web saves separate observation periods per target.
  Live rates, session summaries, mastery and charts use recorded observation
  time, including short periods and observed periods with zero occurrences.
- Unfinished timers survive reload on the recording device. Stop and save them
  before submission; completed periods use the existing offline sync queue.
- Overlapping periods do not double-count time. Historical sessions retain their
  session-duration basis unless explicit target periods are saved.

## 2026-11-10-superadmin-turns-ai-on-per-tpms-facility | 2026-11-10 | Superadmin turns AI on per TPMS facility
Status: Draft

**New Features**
- A platform superadmin can now switch AI features on or off for each TPMS facility (admin ID) from **Superadmin → Practice Email**, right next to the existing email notifications switch. The same setting is in the Add/Edit TPMS Facility dialog. It covers Draft with AI for programs, assessments, and note templates.
- AI is **off for every facility until a superadmin turns it on**. The server-wide AI setting and key still have to be in place; the per-facility switch works on top of them. Users who don't log in through a TPMS facility follow the server-wide setting only, the same way email notifications do.
- When AI is off for a user's facility, they don't see the AI buttons and the server refuses AI requests from them.

## 2026-11-07-clear-read-only-and-action-permission-states | 2026-11-07 | Clear read-only and action-permission states
Status: Draft

**Improvements**
- Read-only program and target statuses display as labels instead of disabled editing buttons. Reminder preferences display as text when the user cannot edit them.
- Prompting-template action menus show only permitted actions. Users with no action permissions see neither an empty action menu nor its table column.
- Prompting-template empty states suggest creation only when the user has Create permission. Authorized actions can still be disabled for clinical locks or pending requests, with the existing explanations retained.

## 2026-11-07-privilege-based-pages-and-actions | 2026-11-07 | Privilege-based pages and actions
Status: Draft

**Fixed Issues**
- Client/session reads require a relevant permission as well as existing client/facility/ownership scope. Saved graph view visibility cannot bypass the caller’s client access. Program edit authorization happens before opening its mutation transaction.
- Page access is checked on direct URLs as well as navigation. Dashboard shortcuts hide unavailable destinations, and the web app uses the server's effective permission map rather than hard-coded role fallbacks.
- Client Directory Delete now checks Delete Clients independently of Edit Clients. Client-program actions inside Program Management use client-program privileges rather than organization-library privileges. Open creation/edit dialogs hide after their action privilege is revoked.
- Central Library import/copy and graph annotation editing controls are hidden without the relevant action privilege. Graph reads and annotation mutations check existing client/program access boundaries.
- Viewing Reports no longer grants editing. Report creation/publishing uses Add/Edit Reports; archiving also needs Delete Reports. Report-status view privileges are enforced by the API.
- Creating solo/manual/group sessions checks Create Sessions in addition to the relevant recording privilege. Discard checks Delete Sessions. Continuing an existing recording does not require Create Sessions.
- Shared note editors recheck their action permissions. Media review requires session approval permission, and read-only recording views hide upload actions.

**Improvements**
- RBT Supervision now has separate View and Manage privileges, retaining existing staff/practice restrictions and default access.
- Privileges shows mandatory administrator permissions as locked, matching the server. Effective permissions refresh when returning to the app or reconnecting, without interrupting pending offline work.

## 2026-11-07-session-note-annotations-and-program-modifications | 2026-11-07 | Session-note annotations and program modifications
Status: Draft

**New Features**
- Supervisors can choose Program modification in the live session's supervision panel and record what changed and why. Existing guidance entries remain guidance.
- Note-template fields have two new auto-fill sources: Program modifications and Graph annotations. Graph annotations include notes, phase lines, overlapping phase ranges, and client graph markers relevant to the session dates and programs/targets.
- Imported content is stored as note text. Later annotation edits do not change existing notes automatically. Refill from session explicitly refreshes configured fields on editable notes; existing text is preserved on initial creation.

**Known limitations (Setup)**
- Add a Text area field to a Notes template, choose either new source in its auto-fill selector, and use that template for a note linked to the session.
- Create program modification entries while supervising a live session. Create graph annotations on the client's analytics page.
- Requires sessions migration `0024_sessionsupervisionnote_note_type`; migration has not been applied as part of this change.

## 2026-11-06-protect-recorded-target-definitions | 2026-11-06 | Protect recorded target definitions
Status: Draft

**Improvements**
- Targets used in recorded or saved sessions retain their measurement, timing, prompt scoring, and step structure. Create a new target for a different definition. Saved sessions are included to protect offline data that has not synced yet.
- Names, instructions, target statuses (including Probe → Acquisition), and workflow settings remain editable with existing permissions. Manually locked programs must be unlocked before editing their targets.
- Recorded targets and their prompt templates cannot be permanently deleted. Close the target or archive its program to retain history. Shared prompt templates retain their scoring levels after session use; duplicate a template to change scoring.
- Target editors show protected controls. Single-target, bulk, program-template propagation, and prompt-template API updates enforce the same protections.

## 2026-11-06-overlapping-graph-points | 2026-11-06 | Overlapping graph points
Status: Draft

**Fixed Issues**
- When multiple visible targets have the same graph position and measurement, the marker now shows each series color instead of letting the last dot cover the others. This also applies while hovering and in scatter views.
- Tooltips keep each target's individual value and trial totals. Hiding a target updates the marker; plotted dates, values, and calculation rules are unchanged.

## 2026-11-06-supervisor-session-recording | 2026-11-06 | Supervisor session recording
Status: Draft

**Fixed Issues**
- Supervisors and administrators with Start Session permission are no longer incorrectly locked out when an accessible client's appointment names a different provider.
- Staff appointment ownership now compares local DCM user IDs for stored appointments and TherapyPMS provider IDs for live TherapyPMS appointments. Client assignment, practice scoping, recording ownership, and Start Session permissions remain enforced.

## 2026-11-06-appointment-controls-respect-session-setups | 2026-11-06 | Appointment controls respect session setups
Status: Draft

**Fixed Issues**
- Upcoming appointments show their default session setup instead of prompting staff to assign programs again. A small optional override control allows appointment-specific changes.
- Completed, cancelled, no-show, and already-started appointments no longer show program-assignment actions. The calendar uses the same rules and displays saved program snapshots for existing recordings.

## 2026-11-06-reusable-session-setups | 2026-11-06 | Reusable session setups
Status: Draft

**New Features**
- **Session Setups** on a client's Sessions page: create a named setup such as Daily Session, select programs individually or use **Add all programs**, arrange their order, pin priorities, and add therapist instructions.
- The first setup becomes the client's default automatically. Reuse it for future sessions, choose another setup when needed, or change the default without assigning programs again for every appointment.
- Web and Flutter show the saved therapist message during recording. The mobile session picker identifies the default setup. Setups can be edited or archived without deleting earlier recordings.

**Improvements**
- Each start creates a fresh recording using current eligible targets. A target changed from Probe to Acquisition appears in Acquisition next time, without reassignment. Programs not run in the previous session remain available; earlier trial data is not copied into the new recording.
- Upcoming appointments show the default setup they will use. Appointment-specific program selections take priority and can be changed through an optional override control without changing the saved setup.

**Fixed Issues**
- Unassigned appointments now load eligible client programs when no default is available, matching the confirmation message instead of creating an empty program list.
- Closed, held, and inactive programs stay out of new recordings. Mastered targets without a maintenance schedule are omitted; scheduled maintenance appears when due.
- Completed, cancelled, no-show, and already-started appointments no longer prompt users to assign programs. Existing recording snapshots and their trial data remain unchanged.

Requires the reusable-session-setup migration before use. Existing appointment program lists are preserved and are not automatically converted into reusable setups. This entry remains a draft and has not been published to What's New.

## 2026-11-05-graph-values-follow-target-measurements | 2026-11-05 | Graph values follow target measurements
Status: Draft

**Fixed Issues**
- Analytics defaults to each target's configured measurement, with separate selections when a program contains different measurements and units. Saved graph settings retain the selected measurement.
- Weekly/monthly duration averages use occurrence counts; minimums and maximums use the observed extrema instead of sums. Duration steps within task-analysis, set-of-targets, and shaping targets retain their recorded seconds.
- Rates use occurrences divided by completed session time, rather than the time between occurrences. Repeated session windows are counted once, including sessions spanning dates. Unknown time windows remain blank.
- Child steps use their own configured scoring threshold when available. Missing duration data and unavailable percent-correct denominators stay blank instead of appearing as zero or raw occurrence counts.

**Known limitations (Notes)**
- Rate timer windows are not stored by the recording apps. Historical target-specific observation rates cannot be reconstructed; graphs explicitly identify rates based on session time.

## 2026-11-05-analytics-date-and-data-point-corrections | 2026-11-05 | Analytics date and data point corrections
Status: Draft

**Fixed Issues**
- Trial graphs order sessions by clinical date and session sequence, including backdated records. Weekly buckets no longer shift when viewed in timezones ahead of UTC.
- Analytics date ranges follow the user's display timezone. Daily labels show dates without misleading session numbers, and trial tooltips include correct and total trial counts.
- Graphs use straight paths between recorded measurements, with extra horizontal room for edge data points.

## 2026-11-05-client-history-refresh | 2026-11-05 | Client history refresh
Status: Draft

**Improvements**
- Client History now follows the Overview and Programs styling, with summary cards, compact view controls, and clearer event filters.
- The timeline uses a narrower layout on phones and shows the date for every event. Session summaries and activity audit trails remain available.

## 2026-11-05-dashboard-simplification | 2026-11-05 | Dashboard simplification
Status: Draft

**Improvements**

- Dashboard brings follow-ups above summary statistics and gives today's schedule more room.
- Session charts expand on demand. A quieter heading and fewer header actions reduce visual clutter.
- Shortcuts stack on narrow screens, and the whole dashboard scrolls together on mobile.

## 2026-11-05-h5-caregiver-two-way-messaging | 2026-11-05 | H5: Caregiver two-way messaging
Status: Draft

**New Features**

- Added Messages to the Family Portal and client pages for a shared conversation between linked caregivers and authorized clinic staff.
- Messages are stored with sender and time, with older history available and retry protection against duplicate sends. Conversations are auditable; messages have no edit or delete action.
- New-message email and push alerts respect notification preferences and practice policies. Alerts contain no message text or client names. Caregivers can manage message alerts and enable browser notifications from the Messages page.

Prepared locally. A tenant migration and configured notification services are required before release. No shared database migration or live delivery performed.

## 2026-11-05-account-appearance-preferences | 2026-11-05 | Account appearance preferences
Status: Draft

**New Features**

- Account → Appearance offers System default, Light and Dark themes, plus Small, Medium and Large font sizes with an immediate preview.
- Medium preserves the current size and Inter font. Preferences apply across the app and persist in the current browser; the theme also stays synced with the sidebar toggle.

## 2026-11-05-single-row-sessions-toolbar | 2026-11-05 | Single-row Sessions toolbar
Status: Draft

**Improvements**

- Combined the Sessions heading, status dropdown, date filter, view switch and actions into one compact toolbar on wide screens, wrapping on smaller screens.
- Status counts remain available in the dropdown.

## 2026-11-05-simpler-sessions-toolbar | 2026-11-05 | Simpler Sessions toolbar
Status: Draft

**Improvements**

- Compact Sessions heading and actions, grouped status filters, and smaller list/calendar controls leave more space for appointments.
- Renamed Quick Start to Start session and added an accessible Clear date range button.

## 2026-11-05-standalone-abc-section | 2026-11-05 | Standalone ABC section
Status: Draft

**Improvements**

- Moved Standalone ABC Incidents into a collapsed More section below the Sessions list, freeing space for appointments. Open More to view or record an incident.

## 2026-11-05-client-sessions-layout | 2026-11-05 | Client Sessions layout
Status: Draft

**Improvements**

- Matched client Sessions to the Overview and Programs design with compact summary cards and a single Session Management panel.
- Unified session actions, filters, list/calendar views and the count footer, with scrolling inside the panel and theme-aware colors.
- Reduced appointment and walk-in row spacing while preserving session permissions, recording controls, notes and approvals.

## 2026-11-05-compact-program-rows | 2026-11-05 | Compact program rows
Status: Draft

**Improvements**

- Program names, codes and last-run dates now appear on one line, with smaller badges and reduced row padding. Long program names show in full on hover.

## 2026-11-05-programs-pagination-and-viewport-layout | 2026-11-05 | Programs pagination and viewport layout
Status: Draft

**Improvements**

- Programs now scroll inside the management panel, keeping the pagination footer visible within the viewport. Category cards scroll horizontally on narrower screens.
- Added page navigation, result ranges and 10/20/50 rows per page. Filters reset to page one, and Select all applies to the current page while preserving selections on other pages.

## 2026-11-05-client-programs-layout | 2026-11-05 | Client Programs layout
Status: Draft

**Improvements**

- Redesigned client Programs with category summary cards and one Program Management panel containing creation actions, search, filters, saved views, archived programs and the table.
- Updated table spacing, tags and always-visible row actions, using theme colors for light and dark mode. Existing permissions and program workflows are preserved.

## 2026-11-05-client-summary-card-readability | 2026-11-05 | Client summary card readability
Status: Draft

**Fixed Issues**

- Client names now stay white on the blue Overview card in both light and dark mode.
- Made the single summary card more compact and increased the contrast of client details.

## 2026-11-05-appointment-reminder-preference-location | 2026-11-05 | Appointment reminder preference location
Status: Draft

**Improvements**

- Removed the appointment email reminder card from client Overview. Personal reminder preferences remain in Account → Notifications under Upcoming appointment reminder, with explanatory text.
- Organization and practice reminder policies continue to apply; saved client preferences are preserved.

## 2026-11-05-sidebar-notification-and-theme-controls | 2026-11-05 | Sidebar notification and theme controls
Status: Draft

**Improvements**

- Moved notifications and the light/dark theme switch into the sidebar account card beside Sign out, including the collapsed sidebar and mobile navigation.
- Removed the empty desktop top bar to give pages more room.

## 2026-11-04-therapypms-appointment-sync-on-login | 2026-11-04 | TherapyPMS appointment sync on login
Status: Draft

**Improvements**

- After the initial manual appointment pull, caregiver and facility-linked provider logins queue a background sync for their facility.
- The sync checks appointments from one year ago through today for new records and uses the facility's last successful sync time as `updated_after` to retrieve edits.
- Active appointment pulls are reused rather than dispatching duplicate login-triggered jobs.

## 2026-11-04-h3-organization-analytics-dashboard | 2026-11-04 | H3: Organization analytics dashboard
Status: Draft

**New Features**

- Added Reporting → Organization Analytics for administrators and supervisors, with date filters and weekly/monthly session charts.
- Added session totals and staff breakdowns, approval rates, current pending session/note review aging, target mastery across clients, and active/non-active client counts and trends.
- Preserved organization, practice and permission boundaries. Ambiguous client identifiers are excluded with a data-quality notice.
- Added client status observations for normal edits and both TPMS sync paths. The baseline starts at migration time; earlier unavailable history is shown as chart gaps.
- Used existing headers, cards, controls, tables and light/dark chart styling, with textual chart counts available.

Prepared locally. All 58 targeted tests (26 reporting tests and 32 email-reminder regression tests), web TypeScript and migration drift checks passed. Validation and metric definitions are documented in `docs/organization-analytics.md`. Tenant migration, disposable PostgreSQL and interactive browser acceptance remain pending. No deployment or publication performed.

## 2026-11-04-h4-appointment-email-reminders | 2026-11-04 | H4: Appointment email reminders
Status: Draft

**New Features**

- Added Organization → Appointment Reminders to configure email timing and choose assigned staff or linked caregivers, using the existing SMTP email service. SMS is outside this release.
- Added client-level email reminder preferences on the client overview, including organization inheritance and opt-out.
- Added delivery tracking, retries and reschedule/cancellation checks while preserving push reminders, recipient preferences and practice email permissions.
- Reminder emails contain appointment time and a secure sign-in link without clinical details. Caregiver delivery requires an active linked portal account and organization enablement.

Prepared locally. All 32 synthetic reminder tests, web TypeScript, the isolated Django system check and migration drift checks passed; details are in `docs/appointment-email-reminders.md`. Tenant migration, disposable PostgreSQL concurrency, real SMTP and browser acceptance remain pending. No real email, deployment or publication performed.

## 2026-11-03-organization-free-trials-and-premium-billing | 2026-11-03 | Organization free trials and premium billing
Status: Draft

**New Features**

- Added Super Admin → Billing for trial duration, extension limits and approvals, organization subscriptions, premium plans and failed payments.
- Split superadmin billing into Organizations, Trial Settings, Extension Requests, Premium Plans and Failed Payments. Added organization-specific trial duration and an audited end-date editor for existing unpaid trials; both use dialogs.
- New organizations start a configurable free trial. Existing organizations retain their access unless a superadmin explicitly starts a trial after confirmation.
- Added Settings → Billing with trial countdown, extension requests/history, secure Stripe-hosted card setup, plan review, checkout, payment history and billing activity.
- Added server-enforced expiry, signed payment webhooks, duplicate/retry protection and paid-term renewal/cancellation handling. Premium access follows confirmed payment.

Prepared locally for backend and web only. Synthetic service/API/HTTP and browser-gateway checks are documented in `docs/subscription-billing.md`. Shared PostgreSQL migrations, browser acceptance and Stripe test-mode acceptance remain pending. No deployment, real payments or changelog publication performed.

## 2026-11-03-m2-client-document-storage | 2026-11-03 | M2: Client document storage
Status: Draft

**New Features**

- Added a Documents tab to each client's web record for intake forms, consent forms, insurance cards, evaluation reports, and other files.
- Added uploads up to 20 MB for PDF, common image formats, DOCX, XLSX, PPTX, OpenDocument, TXT, CSV, and RTF, with file-content and size validation.
- Added client-specific folders, folder renaming, document titles and categories, search, filters, and pagination. Documents can be moved between folders without replacing the original file.
- Added archive and restore actions. Archived files are retained and cannot be downloaded until restored.
- Added private storage and authenticated downloads that recheck permissions, practice scope, and client assignment. Storage paths and public file URLs are not exposed.
- Added separate viewing, upload/organization, and archive/restore permissions. Assigned staff can view by default; administrators and supervisors can manage documents within their client access scope.
- Added upload retry protection and audit records for uploads, downloads, organization changes, archiving, and restoration.

**Improvements**

- Used the existing client navigation, page header, shared controls, dialogs, and loading/empty states.
- Added binary downloads through the existing cookie-protected browser transport, preserving session refresh and upload CSRF protections.

## 2026-11-02-scatterplot-observation-coverage | 2026-11-02 | Scatterplot observation coverage
Status: Draft

**New Features**

- Added Log observed period for scatterplot targets on web and Flutter, recording explicit continuous observation even when no behavior occurred.
- Heat maps now distinguish unobserved blocks (—), fully observed zeros (0), partial observation (·), and recorded occurrences. An asterisk flags counts with incomplete coverage.
- Added coverage details across selected dates and targets. Overlapping periods are merged, and local time/daylight-saving boundaries are respected.

**Fixed Issues**

- Missing records and old occurrence-only data no longer appear as observed zeros. Observation records do not inflate behavior counts or trigger automatic mastery.
- Observed periods retain their timestamps and stable IDs through offline storage and retries; conflicting reused IDs are rejected and retained for recovery.
- Added observed-period identification to raw behavior exports and non-destructive storage migrations.

Prepared locally. Backend, web offline simulation, rendering, Flutter recovery and targeted analysis checks passed. Backend migration and browser/device acceptance remain pending; not ready for publication.

## 2026-11-02-h6-scatterplot-data-collection | 2026-11-02 | H6: Scatterplot data collection
Status: Draft

**New Features**

- Added Scatterplot as a target measurement type. Staff can record timestamped behavior occurrences with optional severity in web and Flutter sessions.
- Added a weekday/time-of-day heat map in program Analytics, with target selection, date ranges, 30-minute or hourly buckets, counts, and timezone labels.
- To use it, select Scatterplot when creating or editing a target, record each occurrence during a session, then review the heat map in the client's program Analytics.
- Heat maps count recorded occurrences only. Empty buckets do not establish that behavior was absent or that observation occurred.
- Preserved existing recording/offline protections and historical session snapshots; older events recorded under a different target type are excluded from the scatterplot.

Prepared locally. Regression, synthetic database/rendering, web TypeScript, and targeted Flutter analysis checks passed. PostgreSQL tenant migration, browser/device acceptance, and offline simulation suites remain pending. The heat map is available on web only. A broader backend run also reproduced three existing permanent-delete test fixture failures on the unchanged baseline. Not ready for publication.

## 2026-11-02-rbt-supervision-tracking | 2026-11-02 | RBT supervision tracking
Status: Draft

**New Features**

- Added an RBT Supervision page to record the supervisor, RBT, contact date, hours, notes, individual/group format, and service observations.
- Added manually entered monthly ABA service-hour totals, including work outside DCM. Reports calculate supervised hours divided by service hours and check the 5% threshold, two face-to-face contacts, one individual contact, and one service observation.
- Added monthly CSV reports and correction history. Incorrect contacts can be voided with a reason; original entries remain visible and are excluded from calculations.
- Administrators and supervisors manage records within their practice. Supervisors log their own contacts, and RBTs can view their own records.
- Supervision tracking supports compliance review; it does not independently verify supervisor credentials or certify BACB compliance.

**Improvements**

- Matched the existing application design with shared headers, themed cards, form controls, status badges, and loading/empty states.
- Added separate header actions for Log supervision contact and ABA service hours. Each opens its own dialog, leaving reports and contact history on the main page.

**Fixed Issues**

- Fixed the shared button's Radix Slot handling that caused the supervision export action to crash the page.

Prepared locally. Backend regression tests, disposable persistence checks, and TypeScript checks passed. Database migration, PostgreSQL tenancy validation, and browser acceptance remain pending. Flutter support is not included. Not ready for publication.

## 2026-10-30-clinical-resource-access-checks | 2026-10-30 | Clinical resource access checks
Status: Draft

**Fixed Issues**

- Fixed browser login responses retaining an incorrect response length after credentials are replaced with secure session metadata.
- Prepared additional client and facility access checks for clinical graphs, exports, note review and session media. Exports recheck current access before delivery.
- Note lists and review queues now filter to accessible clients; staff note and session CSVs retain ownership restrictions.
- Export generation rechecks access before writing files. Media review follows feature permissions, and ambiguous client identifiers no longer grant clinical access.
- Local changes only. Multi-organization database and browser acceptance remain unverified; this entry is not ready for publication.

## 2026-10-26-integration-platform-dropdown | 2026-10-26 | Integration platform dropdown
Status: Draft

**Fixed Issues**

- Fixed the platform selection menu being cut off by the connection card on the Integrations page.

## 2026-10-25-https-protection-preparation | 2026-10-25 | HTTPS protection preparation
Status: Draft

**Improvements**

- Added production defaults that require secure connections and protect browser session cookies.
- Added browser protections against content sniffing and unwanted framing of backend pages.
- Documented the hosting configuration and checks required before release. These changes are prepared locally and have not been deployed.

## 2026-10-25-android-session-protection | 2026-10-25 | Android session protection
Status: Draft

**Improvements**

- The mobile app follows the facility's automatic lock period and checks for inactivity when returning from the background.
- Eligible users can enter their PIN to return to the screen they were working on, keeping the pending form open while the app remains running.
- Expired sessions require a full sign-in. Fingerprint or face unlock cannot bypass a required PIN or expired session.
- Temporary connection failures no longer automatically erase the mobile sign-in.
- Prepared and tested locally; physical Android testing is still required before release. Mobile sign-in with two-step verification remains unavailable.

## 2026-10-25-production-audit-monitoring-preparation | 2026-10-25 | Production audit monitoring preparation
Status: Draft

**Improvements**

- Restricted shared application logs to platform superadmins and removed the built-in internal log access credential.
- Internal log access now uses a protected header rather than a URL token.
- Added VPS/systemd templates for persistent logs, protected remote archival and security-alert scans.
- Added retry storage for failed alert delivery and a safe monitoring readiness/probe command.
- Production destinations and host configuration must be approved and installed before monitoring is active.

## 2026-10-25-separate-android-notification-controls | 2026-10-25 | Separate Android notification controls
Status: Draft

**New Features**

- Admin → Privileges → Notifications now has separate Browser Push and Android Push switches for each notification type. Administrators can manage them by role or for an individual user.
- Users can manage their Android notification preferences from Account → Notifications, unless an administrator has locked the setting.

**Improvements**

- Browser and Android push notifications can be enabled independently. Existing push choices are preserved when the update is applied.
- Android delivery requires the device to be registered for push notifications and notification permission to be allowed on the phone.

## 2026-10-24-verify-sensitive-account-changes | 2026-10-24 | Verify sensitive account changes
Status: Draft

**Improvements**

- Added fresh identity verification before creating accounts, generating password setup links, removing another user's MFA, or changing account access and role permissions.
- Administrators with MFA enabled must enter a new code as well as their account password.
- Approvals apply only to the exact requested change and can be used once. Cancelling leaves the change unapplied.
- Strengthened MFA checking so simultaneous requests cannot reuse the same code.

## 2026-10-24-safer-browser-sign-in-storage | 2026-10-24 | Safer browser sign-in storage
Status: Draft

**Improvements**

- Browser sign-in credentials are stored in protected cookies instead of browser local storage.
- Added protection against requests submitted by other websites, including sign-in and PIN unlock.
- SSO uses a short-lived, one-use handoff instead of placing session credentials in the browser URL.
- Android sign-in continues using its existing authentication flow.

## 2026-10-24-account-security-audit-records | 2026-10-24 | Account security audit records
Status: Draft

**Improvements**

- Added audit records for sign-in attempts, MFA verification, PIN setup and changes, unlock attempts, PIN protection changes and sign-outs.
- PIN lockouts are recorded when the attempt limit is reached.
- Records distinguish completed sign-ins from requests awaiting two-step verification. Passwords, PINs and session tokens are excluded.

## 2026-10-24-verify-before-turning-off-pin-protection | 2026-10-24 | Verify before turning off PIN protection
Status: Draft

**Improvements**

- Disabling PIN screen lock or removing a PIN now requires the current PIN or account password.
- Account settings includes a verification form with an option to use the password if the PIN is forgotten.
- Incorrect verification attempts share the existing five-attempt limit and temporary lockout. Failed verification keeps PIN protection unchanged.

## 2026-10-24-stronger-pin-protection | 2026-10-24 | Stronger PIN protection
Status: Draft

**Improvements**

- Five incorrect PIN attempts temporarily block further attempts for five minutes, including attempts made from multiple windows or devices at the same time.
- PIN verification, screen unlock and PIN changes share the same attempt limit.
- Changing an existing PIN requires the current PIN. Forgotten PINs can be reset by verifying the account password; incorrect recovery attempts also count toward the limit.

## 2026-10-24-in-app-help-assistant | 2026-10-24 | In-app help assistant
Status: Draft

**New Features**

- A chat assistant is now available from every page (floating button, bottom right). It answers "how do I..." and "where is..." questions about using Progressly — navigation, features, and workflows — tailored to what the signed-in user's role can see and do.
- It does not give clinical/ABA advice and does not have access to any client's data — it only helps with finding and using the software, and points to a supervisor/admin or the Knowledge Base for anything else.
- Relies on the same AI setup used for note-template drafting; if no AI key is configured, the assistant tells the user it isn't set up yet instead of failing silently.
- When the assistant can't resolve a question itself, it offers a "Send this as a support ticket" button. One click emails the full conversation to the practice's support inbox so a human can follow up — no separate support form needed.

## 2026-10-21-sidebar-follows-module-permissions | 2026-10-21 | Sidebar follows module permissions
Status: Draft

**Improvements**

- Sidebar modules and client tabs follow the user's granted permissions. Missing permission data no longer shows modules based on role alone.
- Settings, Templates, Organization, and Reporting groups appear only when at least one of their items is accessible.
- Dashboard access, widgets, and shortcuts now follow their module permissions. Inaccessible client, progress, review, schedule, and session sections are hidden and their separate data requests are disabled.
- Client entries and the sidebar client switcher open a permitted client tab instead of always opening Overview. Entries are read-only when no detail tabs are accessible. Client detail pages require Clients access, and directory exports require Export permission.
- Activities now hides session and note rows without the matching permissions, disables their data requests, and shows export and audit actions only to users with access.

## 2026-10-21-approved-session-data-is-locked | 2026-10-21 | Approved session data is locked
Status: Draft

**Improvements**

- Once a session is approved, its trial, behavior, and ABC data becomes read-only for staff, supervisors, and administrators.
- Approved sessions no longer show Edit data. Delayed offline uploads cannot change approved session data.

## 2026-10-21-telehealth-links-on-synced-appointments | 2026-10-21 | Telehealth links on synced appointments
Status: Draft

**Improvements**

- Appointment sync now saves Zoom links returned by TherapyPMS, including changes to links on existing appointments.
- Appointments with a meeting link show a Telehealth label and a Join telehealth action. The call opens inside Progressly beside trial-data recording. Continuing an open telehealth recording restores that view. Starting one defaults the service setting to Telehealth.
- Pull appointments again to bring meeting links into previously synced appointments.

## 2026-10-21-separate-therapypms-connections-for-each-facility | 2026-10-21 | Separate TherapyPMS connections for each facility
Status: Draft

**Improvements**

- Enter TherapyPMS admin credentials on Admin → Integrations. The facility is identified automatically from the login response, with no facility selector. Each facility has a separate connection, sync history, and background sync jobs.
- Connecting or disconnecting one facility leaves the other facilities connected. Credentials for a different facility are rejected.
- Existing TherapyPMS connections move to their matching facility automatically.

## 2026-10-21-therapypms-provider-role-sync | 2026-10-21 | TherapyPMS provider role sync
Status: Draft

**Fixed Issues**

- TherapyPMS provider sync now uses `provider_type`: Supervisor maps to Clinical Supervisor; other values map to Staff. Each sync updates both the provider directory and linked Progressly account, including existing providers whose type changed.

## 2026-10-20-session-service-setting-and-service-address | 2026-10-20 | Session Service Setting and Service Address
Status: Draft

**New Features**

- Choose a Service setting when starting a live, manual, or group session: Home, Office, School, Community, Other, or Telehealth.
- The selected setting is saved with the session and its trial data, including trials synced after recording offline, and appears on the recording screen.
- Service setting is separate from program Category; program categories stay unchanged.
- After recording data, Home, Office, School, and Community sessions require a US service address before submission: street, city, state or territory, and ZIP code, with an optional apartment or suite.
- Staff enter and review the address manually. It is saved with the session and displayed on the recording screen.
- Telehealth sessions are shown as Online and do not require an address. Other sessions do not require an address either.
- The address requirement also applies to manual sessions and sessions submitted through batch sync.

## 2026-10-17-appointment-reminders-and-schedule-updates | 2026-10-17 | Appointment reminders and schedule updates
Status: Draft

**New Features**
- Staff receive a reminder before an upcoming scheduled appointment.
- Staff are notified when an appointment is cancelled, rescheduled, or reassigned.
- Users can manage these notifications from their notification preferences.

## 2026-10-17-client-tabs-show-the-correct-client-data | 2026-10-17 | Client tabs show the correct client data
Status: Draft

**Fixed Issues**
- Fixed an ID mismatch that could hide a client's programs, session notes, progress, behavior plans, and history when staff opened the client page. These sections now recognize both the DCM client ID and the external patient ID, while staff access remains limited to their assigned clients.

## 2026-10-17-pin-unlock-keeps-the-current-page-open | 2026-10-17 | PIN unlock keeps the current page open
Status: Draft

**Fixed Issues**
- Entering the correct PIN after the screen locked could send users to the login page because their session tokens expired with the inactivity timer. PIN unlock now renews the session and returns users to the page they were using.

## 2026-10-17-live-supervisor-guidance-during-sessions | 2026-10-17 | Live supervisor guidance during sessions
Status: Draft

**New Features**
- Supervisors and admins can follow an open session as staff record it and send guidance from the session screen.
- Guidance is saved with the session and appears to its assigned staff member. Updates refresh automatically while the session is open.
- Only users with session approval access can send guidance; staff can read guidance on their own sessions.

## 2026-10-17-group-sessions | 2026-10-17 | Group Sessions
Status: Draft

**New Features**

You can now run a data collection session for multiple clients at once — for example, a social skills group — instead of one client at a time. Pick the clients in the group, choose which programs/goals to run for each one, and switch between clients during the session using a tab bar at the top. Each client's data is still scored, tracked, and reported on individually, exactly as before — grouping them together just makes it easier to run and manage the session as one unit. Find it under **Group Sessions** in the sidebar.

## 2026-10-17-saved-signatures-for-session-notes | 2026-10-17 | Saved Signatures for Session Notes
Status: Draft

**New Features**

Users can save a signature in Settings → Account by drawing it or uploading an image. Staff, supervisors, and admins can apply it when signing an approved session note. The signature is recorded on the note and included in its PDF; updating or removing the saved signature does not change notes already signed.

## 2026-10-17-target-progress-graphs-in-reports | 2026-10-17 | Target progress graphs in reports
Status: Draft

**New Features**

Client progress reports now have a **"Target progress graph"** option in each program's Settings. When turned on, a small success-rate graph (daily, over the report's date range) appears under every target in that program's table, so you can see each target's trend at a glance instead of just the summary numbers. Off by default; turn it on per program.

## 2026-10-15-profile-picture | 2026-10-15 | Profile Picture
Status: Draft

**New Features**

You can now add a profile picture in Settings → Account. It also appears next to your name in the sidebar. If you don't set one, your initials are shown instead, same as before.

## 2026-10-15-duration-all-trials-view-shows-goal-progress | 2026-10-15 | Duration "All Trials" View Shows Goal Progress
Status: Draft

**Improvements**

In the duration "All Trials" table, each occurrence is now highlighted green when it meets the target's duration goal and red when it falls short. This replaces the previous highlighting of only the longest and shortest occurrences within a session. Targets without a duration goal remain uncolored.

## 2026-10-15-trial-count-progress-on-target-cards | 2026-10-15 | Trial Count Progress on Target Cards
Status: Draft

**New Features**

While recording a session, target cards now display the number of trials collected against the minimum required (e.g., "8 / 10"). The progress indicator turns green once the minimum is met. This feature appears only for targets with a minimum trial count configured in their mastery workflow and is currently available on web only.

## 2026-10-15-ai-note-template-drafting-uses-client-programs | 2026-10-15 | AI Note Template Drafting Uses Client Programs
Status: Draft

**Improvements**

The "Draft a note template with AI" workflow now asks you to select a client first, followed by one of that client's actual programs. This helps ensure the drafted fields reflect what's really being tracked for the selected client rather than using a generic program from your library.

## 2026-10-15-voice-to-text-for-session-notes | 2026-10-15 | Voice-to-Text for Session Notes
Status: Draft

**New Features**

Added voice dictation for text fields in session notes. An admin must first enable the feature for each role under Settings → Organization → User Privileges → Activities → "Enable Voice-to-Text (AI) for session note text fields".
Once enabled, a microphone icon appears next to text fields in a note. Tap the icon, speak, and your words will be transcribed into the field. Supported browsers: Chrome, Edge, and Safari.

## 2026-10-14-time-zone-setting-in-account-settings | 2026-10-14 | Time Zone setting in Account Settings
Status: Draft

**New Features**
- You can now set your own time zone in Settings → Account. Pick from a searchable list, or leave it unset to keep using your device's time zone.
- Settings → Account also has a new Language field. (Note: this saves your preference for the future — the app itself isn't translated yet, so changing it won't change what you see today.)

**Improvements**
- Session times, appointment times, audit history, signatures, and other timestamps across the web app now display in your chosen time zone instead of always using your browser's local time.

**Fixed Issues**
- Analytics graphs, program/module summaries, and client progress reports no longer group events into the wrong calendar day for users in a time zone other than UTC. Late-evening sessions were occasionally getting counted under the next day.
- PDF session notes (signature timestamps, footer, session times) and CSV/Excel exports now show times in the time zone you chose in Account Settings, instead of always using the server's time.

Note: this covers the web app, analytics, PDFs, and exports. The mobile app still shows times in device time — follow-up planned.

## 2026-10-12-single-sign-on-from-therapypms | 2026-10-12 | Single sign-on from TherapyPMS
Status: Draft

**New Features**
- **Sign in to Progressly directly from TherapyPMS**: Admins, supervisors, and staff who are already logged into TherapyPMS can now land in Progressly already signed in, with no separate password prompt. Requires the account to already exist in Progressly; MFA still applies if the account has it turned on.

## 2026-10-11-13-new-target-data-collection-types-added-to-programs | 2026-10-11 | 13 new target/data collection types added to programs
Status: Draft

**New Features**
- **New target types for programs**: Targets can now be set up as Latency, Whole Interval, Partial Interval, Momentary Time Sampling, Yes/No, Percentage, Rating Scale, ABC, Scatterplot, Narrative/Event Recording, Permanent Product, Procedure Fidelity, or Opportunities — in addition to the existing types. Each has its own correct way of calculating progress (e.g. average response time for Latency, percent of intervals for interval recording, average rating for Rating Scale).
- **Rating scale range**: When a target is set to Rating Scale, you can now configure the scale's minimum and maximum (e.g. 1–5), and staff score within that range during a session.
- **Session recording for most new types**: Staff can now record live session data on web for Opportunities, Latency, the three interval-recording types, Yes/No, Percentage, Rating Scale, Procedure Fidelity, Permanent Product, and ABC/Narrative targets — each with its own simple recording control matching how that data should be collected.

**Improvements**
- **Procedure Fidelity checklist**: Fidelity targets reuse the step-checklist builder (like Task Analysis), with a correct/incorrect/N/A option per step so non-applicable steps don't count against the score.

**Known limitations (not yet done)**
- Scatterplot targets can be created but don't have a recording screen yet.
- None of the 13 new types are recordable from the mobile app yet — web only for now.
- The program overview/analytics dashboard doesn't yet show correct numbers for most of the new types (shows 0% or "insufficient data" instead) — only the calculation engine behind it is ready.

## 2026-10-11-pin-lock-screen-redesign-and-lockout-protection | 2026-10-11 | PIN lock screen redesign and lockout protection
Status: Draft

**New Features**
- **PIN lockout protection**: After 5 incorrect PIN attempts in a row, the lock screen now locks out further guesses for 5 minutes with a visible countdown, instead of allowing unlimited tries.

**Improvements**
- **Redesigned PIN lock screen**: The screen-lock overlay got a cleaner, more modern look — simplified avatar and status display, a flatter on-screen keypad, and tidier spacing, with no change to how unlocking works.

## 2026-10-10-inactivity-auto-logout-and-personal-pin-screen-lock | 2026-10-10 | Inactivity auto-logout and personal PIN screen lock
Status: Draft

**New Features**
- **Inactivity auto-logout**: The web application now monitors user interaction (keyboard input, mouse movement, clicks, scrolling, and touch events). If no activity is detected for the organization's timeout duration (now including 5-minute, 15-minute, and 30-minute options), the user is automatically logged out for security.
- **Personal PIN screen lock**: When allowed by clinic administrators, staff members can set a personal 4- or 6-digit PIN under **Account → Security**. Inactivity triggers a secure PIN lock screen instead of logging out, allowing staff to enter their PIN and immediately resume their session without losing unsaved changes or page state.
- **Organization PIN lock setting**: Clinic administrators can control whether PIN locking is allowed across the practice via a new toggle in **Admin → Authentication** (`/admin/authentication`). If disabled, or if a user has not configured a PIN, inactivity defaults to an immediate secure logout.

**Improvements**
- **PIN lock screen experience**: Provides a fullscreen lock overlay with masked digit indicators, support for both physical keyboard and on-screen numeric keypad input, invalid attempt feedback with shake animations, and a direct "Log out" button. The lock persists across browser refreshes to prevent lock bypass.

## 2026-09-30-client-directory-saved-views-backed-by-server | 2026-09-30 | Client Directory: saved views backed by server
Status: Draft

**Improvements**
- Saved views (My Views) are now stored on the server instead of the browser's localStorage — they survive clearing the browser, work across devices, and respect visibility settings (Me, All users, Specific role) for real.
- In the My Views dropdown, clicking a view name now **applies** it immediately (sets the status filter and columns). A pencil icon next to each view opens the **Edit View** dialog to rename, change visibility, or delete it.

## 2026-09-29-more-program-library-buttons-now-match-actual-permissions | 2026-09-29 | More program/library buttons now match actual permissions
Status: Draft

**Fixed Issues**
- On the Program Library's **Client** tab, create/edit/archive buttons for a client's own programs were checking the wrong permission — staff who are allowed to edit/archive a client's programs could lose access to the button, and vice versa.
- In **Central Library**, "Import to My Library", "Import Entire Folder", and the per-target copy button were showing even for roles without import permission, so clicking them failed. They now only appear for users who can actually import.

## 2026-09-29-assign-programs-button-now-matches-actual-permission | 2026-09-29 | "Assign Programs" button now matches actual permission
Status: Draft

**Fixed Issues**
- On a client's Sessions page, "Assign Programs" / "Edit Programs" / "Link Appointment" were showing for staff who don't actually have that permission, so clicking them failed. The buttons now check the correct permission and are hidden for anyone without it, matching what the backend already enforced.

## 2026-09-29-start-recording-now-follows-client-provider-assignments | 2026-09-29 | Start Recording now follows client-provider assignments
Status: Draft

**Fixed Issues**
- On a client's Sessions page, "Start Recording" is now locked to the provider(s) actually assigned to that client in Manage Users / Client Directory, instead of whoever TherapyPMS listed as the appointment's provider.

## 2026-09-29-staff-client-lists-now-match-their-assignments | 2026-09-29 | Staff client lists now match their assignments
Status: Draft

**Fixed Issues**
- Staff members (including those linked through TherapyPMS) now only see clients they've actually been assigned to in Manage Users / Client Directory — in the central client switcher, Central Library, Clients list, and elsewhere. Previously, TPMS-linked staff could see every client in the practice regardless of their assignments.

## 2026-09-29-single-sign-on-sso-launch-from-external-systems | 2026-09-29 | Single Sign-On (SSO) launch from external systems
Status: Draft

**New Features**
- Another system can now sign a user straight into Progressly with no Progressly login page. Set a shared secret in Progressly's server configuration (`SSO_SHARED_SECRET`), hand the same secret to the other system out of band, and have it redirect to `/sso/login?token=...` with a short-lived signed token naming the user's email. Matches by email against an existing Progressly account — it never creates one. Intentionally skips Progressly's own MFA, since the other system is trusted to have already verified the person. Configured entirely through server environment variables, not the app itself.
- The other system can also have its users land on a specific page inside Progressly right after signing in: include a `redirect_url` inside the signed token (a path like `/clients/42`, or a full Progressly URL), and `GET /sso/login?token=...` responds with a redirect there (tokens attached safely, never logged or sent anywhere else). The destination has to come from inside the signed token, not a plain link parameter, so no one else can hijack where a valid sign-in link sends someone — and it can only ever point inside Progressly's own site, never out to an external one.

## 2026-09-29-therapypms-pulls-now-run-in-the-background | 2026-09-29 | TherapyPMS pulls now run in the background
Status: Draft

**Improvements**
- Pulling clients, providers or appointments from TherapyPMS (Integrations page) used to run as one request and could time out — a multi-year appointment backfill (e.g. 2022 to today) would fail outright. Pulls now run in the background in monthly chunks, and the page shows live progress (e.g. "Pulling appointments… (12/58)") plus a "Last synced" line with when it finished and how many records changed, so a large historical backfill can actually complete.

**Fixed Issues**
- A years-long appointment pull could fail partway through if TherapyPMS returned even one appointment with bad data (a negative duration) — that single row stopped the whole month's batch from saving. Bad duration values are now ignored instead of blocking the sync.

## 2026-09-29-recording-screen-now-notices-when-someone-else-closes-your-session | 2026-09-29 | Recording screen now notices when someone else closes your session
Status: Draft

**Fixed Issues**
- If a supervisor submitted a session from another device while you were still recording it, your screen used to keep showing "Start Recording" with no explanation, and new trials silently failed to save. The recording screen now checks session status live and tells you right away if the session was closed elsewhere, so you know to stop and don't lose data thinking it's being recorded.
- The client's session list (appointment rows) could keep showing "Scheduled" with "Start Recording" / "Edit Programs" for a session someone else had already submitted and approved on another device — it only updated after a manual page reload. It now refreshes automatically when you switch back to that browser tab, so the button correctly flips to "View".
- A staff member's session list for a client only ever showed sessions they personally recorded — so if a colleague had already recorded and submitted today's appointment, their screen still offered "Start Recording," risking a duplicate session for the same appointment. The list now shows every session recorded for that client, by anyone. Opening the full trial/behavior detail of a colleague's session is still limited to the recording staff member, supervisors and admins — other staff see its status (e.g. "Approved") instead of a working link.

## 2026-09-28-program-edits-now-show-an-error-message-when-they-fail | 2026-09-28 | Program edits now show an error message when they fail
Status: Draft

**Fixed Issues**
- Editing a program's status/phase, tags, treatment area, instructions, or other fields silently did nothing when the save was rejected (e.g. an invalid value) — no error appeared, so it looked like the click didn't register. Now shows an error message explaining what went wrong.

## 2026-09-28-program-library-copy-programs-straight-to-clients | 2026-09-28 | Program Library: copy programs straight to clients
Status: Draft

**New Features**
- Every program in the Program Library (Program Management → Organization tab) now has a "…" menu with: **Copy to client(s)…** (pick one or more clients and the program is copied straight into each one's Programs list — no need to open each client individually), **Preview**, **Duplicate…**, **Save as template**, **Lock program** / **Unlock program** (locks a library program against edits or deletion), and **Copy support info to clipboard**.

**Improvements**
- **Edit**, **Move to folder**, and **Delete** for library programs moved from separate hover icons into that same "…" menu.
- **Preview** now opens a compact, read-only view (program name, Instructions/Targets tabs, target status pills) instead of the full editor — click the expand icon to switch to full editing.

## 2026-09-28-update-available-notice | 2026-09-28 | Update available notice
Status: Draft

**New Features**
- When a new version of the app is deployed, users now see a banner at the bottom of the screen letting them know an update is ready, with buttons to update now (reloads with the latest version) or dismiss for later.

## 2026-09-28-client-directory-toolbar-filters-export-and-saved-views | 2026-09-28 | Client Directory: toolbar filters, export, and saved views
Status: Draft

**New Features**
- **Status filter** dropdown at the top of the Client Directory — filter by Active, Inactive, Discharged, or On Hold without touching the search box.
- **Export** button (top right) — export the current filtered client list as **CSV** or **Excel**. Only the columns currently visible in the table are included.
- **My Views** button — save the current filter (status + columns) as a named view for quick reuse. Click **Save new view**, give it a name, and set visibility: **Me** (only you), **All users** (everyone in the organisation), or **Specific user role** (pick a role). Saved views appear in the My Views dropdown and apply their filters and columns in one click. Views are stored per browser.


**New Features**
- Each client row in the **Client Directory** now has a three-dot menu with four actions: **Edit Client**, **Inactivate Client**, **Delete Client**, and **Manage Users**.
- **Delete Client** now permanently removes the client record (hard delete) after a confirmation. Previously it only set the status to inactive.
- **Manage Users** dialog now shows a checkbox list of all staff and supervisors in the organisation. Checked = assigned to that client. Click any checkbox to instantly assign or unassign — no separate Add button needed.

**Improvements**
- The pencil icon and user icon on each row remain as direct shortcuts for Edit Client and Manage Users.
- The Manage Users dialog is wider and each provider row shows **Name**, **Email**, and **Role badge** in three equal columns with column headers, making it easy to scan at a glance.
- Assigned rows are highlighted with a subtle blue tint so it's immediately clear who is already on the client.

## 2026-09-27-client-list-cleanup-real-clients-a-new-directory-screen-and-a-session | 2026-09-27 | Client list cleanup: real clients, a new directory screen, and a sessions fix
Status: Draft

**New Features**
- Added a new **Clients** page in the sidebar — a simple, searchable list of every client synced into Progressly (name, date of birth, status). Separate from **My Clients**, which stays the full working view (Overview, Programs, Sessions, Notes, etc.).
- The **Clients** page table columns are now customizable — click the gear icon next to the column headers, then check which columns to show (External ID, Preferred Name, Date of Birth, Status, Intake Date, Discharge Date, Created, Last Updated). Your choice is remembered next time you visit.

**Improvements**
- Renamed the sidebar's **Client** tab to **My Clients** for clarity, now that there are two client-related screens.
- The central client-selector dropdown (top of the sidebar) and both client list screens now always show real clients pulled in from TherapyPMS — never provider/staff records, which could show up there before.

**Fixed Issues**
- A client's **Sessions** tab could come back empty even when TherapyPMS had real scheduled sessions for them — fixed a mismatched lookup that was checking the wrong id.
- Staff members opening **My Clients** were sometimes silently redirected to a single, unrelated client's page instead of seeing the full list — removed.
- Cleaned up leftover provider/test data that had been mixed into the client list.

## 2026-09-27-favorite-your-clients-for-easy-access | 2026-09-27 | Favorite your clients for easy access
Status: Draft

**New Features**
- Users with access to more than 5 clients can now favorite the ones they work with most often — click the client selector dropdown, hover a client, and click the heart next to their name.
- Favorited clients move to the top of the dropdown, so the ones you use most are always one click away.
- Favorites are per-user — favoriting a client only affects your own dropdown, not your teammates'.

## 2026-09-27-providers-sign-in-with-a-progressly-password-not-therapypms | 2026-09-27 | Providers sign in with a Progressly password, not TherapyPMS
Status: Draft

**New Features**
- Admins can share a **password-set link** with a synced provider from the Staff page — pick an expiration date, generate the link, and copy it to send them.
- The Staff page shows each provider's login status: Not invited, Invited (with expiry), Invite expired, or Active.
- Providers open their link, set a password, and sign in — this also completes any two-step verification their organization requires.
- The Staff page's **Type** column is now editable — admins can change a staff member's type between staff and supervisor directly from the list. Administrator accounts aren't changed here (see below).
- New **Users** tool under Superadmin: the platform owner can create an Administrator, supervisor, or staff login for any organization directly, scoped to that organization's specific TherapyPMS practice.

**Improvements**
- Provider sign-in no longer depends on TherapyPMS being reachable — Progressly checks their own password instead of calling TherapyPMS on every sign-in.
- Only providers already synced in via Pull Providers can be invited to sign in this way.

**Fixed Issues**
- Sign-in was never recording a provider's last-login time; it now does, which is what powers the new "Active" status.
- Fixed a long-standing bug (since July) where users could get randomly signed out during normal use — a broken setting meant sessions couldn't refresh themselves properly. This affects the whole app, not just providers.
- Fixed the client list failing to load for providers signing in with their own password.

**Known limitations (Known issue (not yet resolved))**
- The password-set link (and some notification email links) show "localhost" instead of the real Progressly address in the live environment. This is a server configuration setting, not a code issue — needs to be corrected on the live server.

## 2026-09-26-therapypms-pull-sync-clients-providers-appointments | 2026-09-26 | TherapyPMS Pull sync (clients, providers, appointments)
Status: Draft

**New Features**
- On the Integrations page, **Pull Providers**, **Pull Clients**, and **Pull Appointments** now sync live data from TherapyPMS into Progressly for the connected practice.
- Pull Appointments asks for a from/to date range (required by TherapyPMS) before syncing sessions.
- Connecting stores the TherapyPMS practice binding so each organization only syncs its own mapped facility — data from another facility or organization is skipped, not overwritten.

**Improvements**
- Disconnect clears stored TherapyPMS credentials and the practice binding, not just the connected flag.

## 2026-09-26-organization-integrations-page | 2026-09-26 | Organization Integrations page
Status: Draft

**New Features**
- Added an **Integrations** page under Organization — connect Progressly to your Practice Management platform.
- Pick **TherapyPMS** from the platform dropdown, enter your TherapyPMS email and password, and click Connect to verify the connection.
- Once connected, the page shows "Your organization is currently linked to TherapyPMS" with a **Disconnect** button and **Pull Providers / Pull Clients / Pull Appointments** buttons.
- Wrong or missing credentials show an error toast; a success toast and status line confirm a working connection.

## 2026-09-26-layout-settings-for-note-and-form-templates | 2026-09-26 | Layout Settings for Note and Form Templates
Status: Draft

**New Features**
- Added **Layout Settings** to the Note Templates and Form Templates pages — turn on a custom Page Header and/or Page Footer with its own rich-text editor (bold, lists, alignment, dynamic fields like client name or session date).
- Notes and Forms each have their own independent header/footer — turning one on for Notes doesn't affect Forms.
- The configured header/footer is now included automatically every time a note or form is exported as a PDF.

## 2026-09-26-templates-moved-into-their-own-pages | 2026-09-26 | Templates moved into their own pages
Status: Draft

**Improvements**
- Templates now has its own sidebar dropdown, like Settings — expand it to jump straight to **Note Templates**, **Form Templates**, or **Custom Templates**, each on its own page and URL instead of tabs on one page.
- "Session Note Templates (Uploaded)" is now called **Custom Templates** for clarity.
- Old `/templates` links still work — they redirect to Note Templates.

## 2026-09-26-central-library-redesign | 2026-09-26 | Central Library redesign
Status: Draft

**Improvements**
- The Central Library now matches the Program Library's layout: folders moved into a sidebar list on the left, with search and category filters (Skill Acquisition, Behavior Reduction, etc.) above the program grid.
- Programs still show as image cards with an Import button — only the surrounding layout changed, so browsing and importing work exactly as before.
- Added a "Showing X of Y programs" line above the grid.

## 2026-09-26-email-option-for-two-step-verification | 2026-09-26 | Email option for two-step verification
Status: Draft

**New Features**
- Two-step verification can now use **Email** instead of an authenticator app — a 6-digit code is emailed automatically each time you sign in, no extra click needed.
- A "Resend code" link is available if the email doesn't arrive, with a short wait between resends to prevent spam.

**Improvements**
- The Account → Security tab has a cleaner look: clearer on/off status, a "choose your method" screen when turning on two-step verification, and matching style with the rest of the app.
- Turning off two-step verification with the email method now also requires a fresh code, same as the authenticator app.

## 2026-09-26-program-library-redesign | 2026-09-26 | Program Library redesign
Status: Draft

**Improvements**
- The Program Library (Program Management → Organization tab) has a cleaner layout: search and the Archived filter now sit together above the category filters, and folders moved into a sidebar list on the left instead of cards.
- Template programs now show a "Template" label right next to their name.
- A program with no treatment area shows a quick "+ Assign area" link instead of just "Not assigned" — click it to fill it in on the spot.
- Added a summary line under the program table showing how many programs are shown, how many are templates, and how many are missing a treatment area.
- The Client tab's program table got the same "+ Assign area" quick-fix link for consistency.

## 2026-09-24-menu-permissions-enforced-everywhere-for-programs-and-notes | 2026-09-24 | Menu permissions enforced everywhere for programs and notes
Status: Draft

**Improvements**
- Program actions now follow the Privileges menu on the server as well as on screen. Creating, editing and deleting a client's programs, targets, modules, materials and images each need the matching **Client Programs** permission. Library programs need the matching **Program Library** permission. Before, the server only checked whether the person was a supervisor or admin.
- Viewing programs, targets, history, the Program Library, notes, signatures, note assignments and note templates now also needs the related menu permission, so someone without access can no longer read them directly.
- Submitting a note needs the **Submit note** permission, and starting a DocuSeal note needs **Create note**.
- The program details window hides everything that changes the program (rename, phase, add/import/edit/delete targets, bulk edit, modules) from people who can only view. The **Organization** switch on the Programs page only shows for people who can open the Program Library.

## 2026-09-24-delete-archived-programs | 2026-09-24 | Delete archived programs
Status: Draft

**New Features**
- On a client's **Programs** page, the **Archived** tab now has a **Delete** button on each program to remove it permanently (after a confirmation).
- A program can only be permanently deleted after it has been archived, and not if any of its targets has recorded session data. Those programs stay archived so no clinical records are lost.

## 2026-09-24-fixed-archiving-a-program-did-nothing | 2026-09-24 | Fixed: archiving a program did nothing
Status: Draft

**Fixed Issues**
- On a client's **Programs** page, clicking the trash icon and confirming didn't remove the program from the list. It now archives the program properly and it disappears from the Active list.
- The **Archived** tab was always empty. It now lists the client's archived programs.

## 2026-09-24-targets-required-when-creating-a-program | 2026-09-24 | Targets required when creating a program
Status: Draft

**Improvements**
- A new program (client or library) now needs at least one target with a label. Instructions Only programs are the exception, since they can't have targets.
- **New Program** points out what's missing: the Targets step is marked **Required**, steps with missing information turn red, and the Preview lists what needs fixing with a **Fix** button. Pressing **Create program** with something missing takes you straight to the first thing to complete.
- The program and its targets are now saved together. If a target is rejected, nothing is saved, instead of a program being created with only some of its targets.

## 2026-09-24-new-program-preview-step | 2026-09-24 | New Program: Preview step
Status: Draft

**Improvements**
- **New Program** now has three steps: **Treatment → Targets → Preview**. The separate Instructions tab is gone.
- **Preview** shows everything you entered (program details and each target) so you can check it before saving. Each section has an **Edit** link that takes you back to change it.
- Instructions (for everyone, or staff-only) are now written on the Preview step, just above the **Create program** button.

## 2026-09-24-stricter-program-validation | 2026-09-24 | Stricter program validation
Status: Draft

**Improvements**
- Program and target names are now limited to 200 characters, and text fields, tags and treatment areas have sensible limits, so oversized input gets a clear message instead of a server error.
- Choosing a prompt level or workflow template that no longer exists is now rejected with a clear message.
- In **New Program**, the name field stops at 200 characters (with a counter near the limit). Saving without a name takes you back to the name field with a message. Targets without a label or with a duplicate label show a warning icon.

## 2026-09-23-guided-new-program-dialog | 2026-09-23 | Guided New Program dialog
Status: Draft

**Improvements**
- **New Program** (and New Library Program) now walks you through numbered steps: **1 Treatment → 2 Instructions → 3 Targets**. Finished steps show a green check, and the Targets step shows how many targets you've added.
- Added **Back** and **Next** buttons. Press Enter after typing the name to move on.
- The main button now says **Create program** and is available on every step, with a hint that you can create it now and finish the rest later. The footer shows the current step and target count.

## 2026-09-23-friendlier-client-programs-page | 2026-09-23 | Friendlier client Programs page
Status: Draft

**Improvements**
- The client **Programs** page has a summary at the top (number of programs and targets) with clickable status chips (Active, Baseline, Hold…) that filter the list.
- Added an **All** tab, plain counts on the category tabs, and an **Active / Archived** switch.
- Click anywhere on a program row to open it. Edit and Archive buttons are always visible instead of appearing only on hover, and each row shows its category and a friendly "Last run 3 days ago".
- The create button now says **New Program**. When a search or filter finds nothing, the page says so and offers **Clear filters**.
- Removed the row checkboxes, which had no bulk actions to go with them.

## 2026-09-23-professional-note-pdfs | 2026-09-23 | Professional note PDFs
Status: Draft

**Improvements**
- **Download PDF** on a note now produces a proper clinical document instead of a plain list: practice name header, a details panel (client name, date of birth, note date, provider, session time, status), and the note laid out the way it was written, with the values you entered and the dynamic details (client, session, programs, appointment) filled in.
- Tables, headings and lists from the template carry over. Signatures show who signed, their role and when, and every page has a confidentiality footer with the page number.

## 2026-09-23-easier-note-forms | 2026-09-23 | Easier note forms
Status: Draft

**Improvements**
- When you add a note, long-text fields and fields on their own line now use the full width of the form instead of a small box, and the text boxes can be resized.
- Required fields are labelled **Required**, the inputs have clearer hover and focus states, and the date and signature options sit in a tidy bar at the top with a short how-to line above the form.

## 2026-09-23-ai-note-templates-from-a-program | 2026-09-23 | AI note templates from a program
Status: Draft

**New Features**
- On **Templates → Notes**, the new **Draft with AI** button builds a note template from a Program Library program. Pick the program (add an optional instruction), and the template form opens pre-filled with suggested fields to review and edit before saving. Nothing is saved until you save it, and it only appears when AI is turned on.

## 2026-09-23-redesigned-sign-in-page | 2026-09-23 | Redesigned sign-in page
Status: Draft

**Improvements**
- The sign-in page has a fresh look that matches the rest of the app: a navy brand panel with a preview of progress tracking, and a clean sign-in card.
- Email and password fields now have icons, a clearer show/hide password button, and gentler error messages. The Sign in button is full-width and shows progress while signing in.
- On phones the page stacks cleanly with the form first.

## 2026-09-23-new-library-program-matches-new-program | 2026-09-23 | New Library Program matches New Program
Status: Draft

**Improvements**
- **New Library Program** (Program Library) now uses the same dialog as a client's **New Program**: Auto fill from a Program Prototype, the category cards, and the Treatment, Instructions and Targets tabs. You can add targets, set the prompt level template and mastery workflow, and write instructions and baseline notes right when you create it.
- Draft with AI works in the same place, and its suggested targets are now added to the library program too.

## 2026-09-23-ai-on-off-switch | 2026-09-23 | AI on/off switch
Status: Draft

**New Features**
- A single setting, **AI_ENABLED**, in the server's settings file turns every AI feature on or off. When it is **false** (the default), the **Draft with AI** buttons disappear from the Program Library and the New Program dialogs, and the server refuses AI requests. When it is **true** and an AI key is set, they show as before.
- Changing it needs a server restart; the web app picks it up on the next page load.

## 2026-09-23-draft-programs-with-ai | 2026-09-23 | Draft programs with AI
Status: Draft

**New Features**
- **Draft with AI** button at the top right of the **New Program** dialog on a client's Programs page, and of **New Library Program** in the Program Library, next to the close button. Click it, type what you want (for example "teach a young learner to request preferred items using picture cards"), and the form fills in: program name, category, treatment area, tags, objective, teaching instructions and, on a client's New Program dialog, a starting list of targets with suggested types.
- Nothing is saved until you review the form and click Create. In the Program Library dialog, objective and instructions are saved with the program, and targets are added afterwards.
- The AI can only pick categories, target types, treatment areas and tags that already exist in DCM (treatment areas and tags come from your own Settings lists), so it can't invent options.
- Descriptions are sent to the AI service, so don't type client names or details. It is a drafting aid, not clinical advice, and it is told not to copy published curricula.

## 2026-09-22-treatment-plan-signatures | 2026-09-22 | Treatment plan signatures
Status: Draft

**New Features**
- Finalized treatment plans can now be **signed** by a supervisor or administrator. Click **Sign plan**, type your full name, confirm you've reviewed the plan, and sign. The signature shows the name, role and date and time.
- A signed plan is **locked**: its content can't be edited, it can't be moved back to draft and it can't be deleted. To change it, archive it and create a new plan.
- Plans show a **Signed** badge, and the signatures panel prints with the plan.
- Only finalized plans can be signed, each person can sign once, and staff-level users can't sign.

## 2026-09-22-draft-assessments-with-ai | 2026-09-22 | Draft assessments with AI
Status: Draft

**New Features**
- **Draft with AI** button in the Program Library. Describe the assessment you want (for example "early communication and social skills, ages 2 to 5, about 6 areas") and the AI drafts areas and skills. You review and edit everything, including names, skills, and adding or removing areas, before **Save to library**. Nothing is saved until you do.
- The AI is told to write original, generic skills and not to copy any published assessment. It only receives your description, never client information, so don't type client details into it.
- Needs an AI key set up by an administrator; without one, the button explains that AI drafting isn't set up. It works with any OpenAI-compatible service, and NVIDIA's free developer API is the default for development.

**Fixed Issues**
- AI drafting failed with a generic "The AI service returned an error" because the default model had been retired by the provider. The default model was updated, and the message now says when the configured model isn't available so it's clear what to change.

## 2026-09-22-assessment-programs | 2026-09-22 | Assessment programs
Status: Draft

**New Features**
- **Add starter assessment** button in the Program Library. It adds a blank, editable "Skills Assessment (Starter)" program with five common areas (Language, Social, Play, Daily Living, Motor), three placeholder skills in each, and a 0–2 rating scale (Not present, Emerging, Present). Replace the placeholder skills with your own, then copy the program to clients like any library program. Clicking it again never makes a duplicate.
- **Assessment summary** on the program detail page for Assessment programs: a card per area showing the average latest score, how many skills are scored, and the change since the previous time the assessment was done. Click an area to see each skill's score and change.
- New **Assessment** program category, next to Skill Acquisition, Behavior Reduction and the others. Use it for skills assessments: areas become modules, skills become targets, and clinicians score them in sessions, with results in the usual graphs and reports.
- Assessments work with **Rating Scale** prompt level templates (Settings → Prompt Level Templates) for scoring skills on a scale, and can be added to the Central Library, copied between clients, and used as Program Prototypes like any other program.
- Nothing from any licensed assessment is included; practices enter their own skills.

## 2026-09-22-trend-by-phase-birthday-icon-and-long-session-warnings | 2026-09-22 | Trend by phase, birthday icon and long-session warnings
Status: Draft

**Improvements**
- **Separate trendlines per phase.** On session graphs, when phase lines are shown, the trendline now restarts at each phase line (for example Baseline, Active, Maintenance) instead of one line running across all of them. With no phase lines, it looks the same as before.
- **Birthday icon on appointments.** A small cake icon appears on an appointment that falls on the client's birthday, on the client's Sessions page (list and calendar views) and on the Appointments report.
- **Long-session warnings.** On the Activities page, a session longer than 12 hours shows a warning icon next to its duration. Submitting a live session that has been open for more than 12 hours now asks you to confirm first, and its timer shows a warning mark.

## 2026-09-22-duration-all-trials-view | 2026-09-22 | Duration "All Trials" view
Status: Draft

**New Features**
- New **All Trials (each occurrence)** option in the Y-axis list for duration data on program graphs (Behavior data source). It shows a table of every timed occurrence, grouped under its session date and name, with one column per occurrence (#1, #2, #3…) and one row per target, in h:mm:ss.
- In each session, the longest occurrence for a target is tinted green and the shortest pink, so outliers stand out. Hiding a target in the graph legend hides its row too.

**Fixed Issues**
- The **Target snapshot** on a client's Analytics page showed 0 trials, 0 sessions and no last-data date for duration targets, even after data was recorded, because it only counted scored trials. It now counts timed occurrences and the sessions they were in.
- The client **Report** page's target table (Sessions, Trials, Last Session) also showed zeros for duration targets. It now counts their timed occurrences too, the same as the Target snapshot.
- The program graph showed "No trial data" for programs made only of duration, rate or frequency targets, because it always opened on scored-trial data. It now opens on the behavior data for those programs, and on Total Observed Duration when they're all duration targets. You can still switch the data source and Y-axis yourself.
- The client **Analytics** page could open on a program that wasn't in the program buttons at the top (for example one with no targets), which looked empty. It now opens on the first program shown in the buttons.

## 2026-09-21-edit-data-in-completed-sessions | 2026-09-21 | Edit data in completed sessions
Status: Draft

**New Features**
- New **Edit Data** button on a completed (submitted, approved or rejected) session's recording screen, for correcting recorded data: add or remove trials, behavior events and ABC events. You enter a reason once, and every change is recorded in the audit log with your name, the change and the reason.
- New privilege **Edit Session Data**, separate from Start Session and Enter Manual Sessions. Admins and supervisors have it by default; staff don't.
- If someone who can't approve sessions edits an approved session, it goes back to submitted for review. Edits by admins and supervisors keep it approved.
- Automatic target advancement and prompt fading are not re-run after an edit. Instead, when you click **Done editing**, a **Please review these goals** list shows any goals that changed stage or prompt level because of that session, so you can correct them by hand if the fixed data no longer supports the change.

## 2026-09-21-manual-session-entry | 2026-09-21 | Manual session entry
Status: Draft

**New Features**
- **Add manual session** button on a client's Sessions page, for entering a session that wasn't recorded live (for example data taken on paper). Pick the date and start and end time, then enter the data on the recording screen as usual.
- New privilege **Enter Manual Sessions**, separate from **Start Session**. A role can be allowed to back-enter data without being able to record live, or the other way round. Admins, supervisors and staff have it by default.
- Manual sessions are tagged **Manual** in the sessions list and show "Manual entry" with their date and time on the recording screen. All their data is stamped inside the time window you entered, so graphs and reports show it on the right day.
- Safeguards: a manual session can't end in the future, can't be longer than 24 hours, and can't start more than a year ago.

## 2026-09-21-session-recording-privilege-enforced | 2026-09-21 | Session recording privilege enforced
Status: Draft

**Fixed Issues**
- The **Start sessions** privilege is now enforced by the system, not just hidden in the screens. Roles without it can no longer start a session, record or delete data, submit, or upload photos and video during a session, even through the API. Admins always keep it, and roles that have it (staff and supervisors by default) see no change.

## 2026-09-21-graphs-baselines-last-run-date-and-report-saving | 2026-09-21 | Graphs, baselines, last run date and report saving
Status: Draft

**New Features**
- **Minimum trials filter** on program graphs: hide data points that have fewer than 2, 3, 5 or 10 trials so tiny sessions don't distort the trend. The choice is saved with saved insights.
- **Calculate baseline from data:** in Edit Program, next to Baseline, click **Calculate from data** to add each target's % correct from its first 3 sessions. It adds to your existing notes.

**Improvements**
- **Last run date** now shows under each program name and on each target card ("Not run yet" until data is collected).

**Fixed Issues**
- **Progress reports are now saved on the server.** Before, drafts, published and locked reports lived only in one browser, so clearing the browser lost them and other computers couldn't see them. Existing browser drafts move up automatically the first time they're opened, and a "Saving… / Saved" note shows in the toolbar.

## 2026-09-21-multi-factor-authentication-mfa | 2026-09-21 | Multi-Factor Authentication (MFA)
Status: Draft

**New Features**
- Two-step verification with an authenticator app (Google Authenticator, Microsoft Authenticator). After entering the password, users enter a 6-digit code.
- Admins can turn on **Require** MFA for any user from the Users page. Required users set up their authenticator the next time they sign in.
- The Users page shows an **MFA** column: Active, Not set up, or Off.
- Users can turn MFA on or off themselves under **Account → Security**. If an administrator requires MFA, it can't be turned off.
- If someone loses their phone, an admin can use **Remove MFA** (asks for the admin's password) and the user sets it up again.
- Web only for now. Users with MFA on can't sign in on the mobile app yet.

## 2026-09-21-session-prototypes-removed | 2026-09-21 | Session Prototypes removed
Status: Draft

**Improvements**
- Starting a session now goes straight to recording, with no "Select Session Prototype" step. Session name and therapist message still come from the assigned lesson, as before.
- Removed the Session Prototypes tab from Settings and its privileges. Previously saved session prototypes were deleted.

## 2026-09-21-program-prototypes | 2026-09-21 | Program Prototypes
Status: Draft

**New Features**
- **Program Prototypes** tab in Settings: save a preset with category, treatment area, prompt level template, workflow, tags, objective, baseline notes and instructions.
- **Auto fill** dropdown at the top of both **New Program** and **Edit Program**. Choose a Program Prototype and all its settings fill in automatically. The program name is not changed, and everything can still be edited.
- New privileges for Program Prototypes (View, Create, Edit, Delete). Admins have them by default.

## 2026-09-21-what-s-new-page | 2026-09-21 | What's New page
Status: Draft

**New Features**
- **What's New** page in the Help menu showing release notes in a dated timeline.
- Superadmins can write, edit, publish and hide release notes from **Changelog** in the superadmin menu.

## 2026-09-21-knowledge-base-editing-improvements | 2026-09-21 | Knowledge Base editing improvements
Status: Draft

**New Features**
- Images and videos can be added directly inside Knowledge Base article text.

**Improvements**
- Article overview and topic summary now use a rich text editor: bold, italic, underline, headings, lists and links.
- The article **Path** field is now a dropdown of real app pages, with a custom option.
- Word (.docx) imports now keep bold, headings, lists and links.

**Fixed Issues**
- Fixed the heading dropdown (Normal / H1 / H2 / H3) in the editor toolbar, which wasn't opening.
- Fixed Word imports showing stray symbols (like asterisks) instead of bold text and headings.
