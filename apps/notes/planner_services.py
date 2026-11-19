"""Stage 5: progress-note autofill, addenda and homework. Never claims an
intervention happened or progress occurred — only surfaces the clinician's
own selected objective and a synthetic prompt for them to describe it."""
import hashlib
import json

from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.integrations.practice_planner.snapshots import retain_selection
from .models import ClientHomeworkAssignment, NoteAddendum, NoteContentApplication


def note_client(note):
    from apps.clients.id_aliases import client_identifier_aliases
    from apps.clients.models import Client
    if note.external_client_id is None:
        return None
    return Client.objects.filter(pk__in=client_identifier_aliases(note.external_client_id)).first()


def plan_objectives(plan):
    """Nested problem → goal → objective tree for the clinician to pick from."""
    result = []
    for problem in plan.problems.select_related('source').order_by('position', 'id'):
        goals = []
        for goal in problem.goals.select_related('source').order_by('position', 'id'):
            objectives = [{'id': o.id, 'text': o.clinician_text or o.source.text}
                          for o in goal.objectives.select_related('source').order_by('position', 'id')]
            goals.append({'id': goal.id, 'text': goal.clinician_text or goal.source.text, 'objectives': objectives})
        result.append({'id': problem.id, 'text': problem.clinician_text or problem.source.text,
                        'source_component_id': problem.source.component_id, 'source_edition': problem.source.edition,
                        'source_language': problem.source.language, 'goals': goals})
    return result


def _resolve_objectives(plan, objective_ids):
    from apps.clients.models import TreatmentPlanObjective
    if not objective_ids:
        raise HttpError(400, 'Select at least one objective')
    objectives = list(TreatmentPlanObjective.objects.filter(pk__in=objective_ids, goal__problem__plan=plan)
                       .select_related('goal__problem__source', 'goal__source', 'source'))
    if len(objectives) != len(set(objective_ids)):
        raise HttpError(400, 'An objective is not available on the active plan')
    return objectives


def preview_content(plan, objective_ids, service):
    """Read-only: build the prompt text a clinician would see, without persisting anything."""
    objectives = _resolve_objectives(plan, objective_ids)
    blocks = []
    template_snapshots = []
    for objective in objectives:
        goal = objective.goal
        problem = goal.problem
        prompts = service.progress_notes(problem.source.component_id, edition=problem.source.edition, language=problem.source.language)
        prompt_texts = []
        for content in prompts:
            snapshot = retain_selection(content)
            template_snapshots.append(snapshot.id)
            prompt_texts.append(content.text)
        blocks.append({
            'objective_id': objective.id,
            'problem_text': problem.clinician_text or problem.source.text,
            'goal_text': goal.clinician_text or goal.source.text,
            'objective_text': objective.clinician_text or objective.source.text,
            'prompts': prompt_texts,
        })
    rendered = '\n\n'.join(
        f"Objective addressed: {block['objective_text']}" + (f"\nPrompt: {block['prompts'][0]}" if block['prompts'] else '')
        for block in blocks
    )
    return {'blocks': blocks, 'rendered_content': rendered, 'template_snapshot_ids': sorted(set(template_snapshots))}


@transaction.atomic
def apply_content(note, plan, objective_ids, field_key, mode, service, actor):
    if not note.is_editable:
        raise HttpError(409, f'Note is {note.status} and cannot be modified')
    preview = preview_content(plan, objective_ids, service)
    body = dict(note.body or {})
    existing = (body.get(field_key) or '').strip()
    applied = mode == 'replace' or not existing
    if applied:
        body[field_key] = preview['rendered_content']
        note.body = body
        note.save(update_fields=['body', 'updated_at'])
    NoteContentApplication.objects.create(
        note=note, plan=plan, objective_ids=sorted(set(objective_ids)),
        template_snapshot_ids=preview['template_snapshot_ids'], field_key=field_key,
        rendered_content=preview['rendered_content'], applied=applied, created_by=actor,
    )
    return {'applied': applied, 'field_key': field_key, 'rendered_content': preview['rendered_content'], 'body': note.body}


def list_applications(note):
    return [{field: getattr(row, field) for field in (
        'id', 'plan_id', 'objective_ids', 'template_snapshot_ids', 'field_key', 'rendered_content', 'applied', 'created_at',
    )} for row in note.planner_applications.order_by('created_at', 'id')]


def add_addendum(note, actor, text):
    if not note.signatures.exists():
        raise HttpError(409, 'Only a signed note can receive an addendum')
    text = text.strip()
    if not text:
        raise HttpError(400, 'Addendum text is required')
    addendum = NoteAddendum.objects.create(note=note, author=actor, author_name=actor.full_name, author_role=actor.role, text=text)
    return {field: getattr(addendum, field) for field in ('id', 'author_name', 'author_role', 'text', 'created_at')}


def list_addenda(note):
    return [{field: getattr(row, field) for field in ('id', 'author_name', 'author_role', 'text', 'created_at')}
            for row in note.addenda.order_by('created_at', 'id')]


def note_content_hash(note):
    """Fingerprint note.body plus any applied planner provenance at signing time."""
    applied_ids = list(note.planner_applications.filter(applied=True).order_by('id').values_list('id', flat=True))
    payload = {'body': note.body, 'template_id': note.template_id, 'applied_planner_records': applied_ids}
    body = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


@transaction.atomic
def assign_homework(client, service, *, problem_component_id, homework_component_id, edition, language, actor, note=None):
    homework_items = service.homework(problem_component_id, edition=edition, language=language)
    match = next((c for c in homework_items if c.component_id == homework_component_id), None)
    if match is None:
        raise HttpError(400, 'Selected homework content is not available for this problem')
    snapshot = retain_selection(match)
    assignment = ClientHomeworkAssignment.objects.create(client=client, note=note, source=snapshot, assigned_by=actor)
    return _homework_output(assignment)


def complete_homework(assignment, actor):
    if assignment.status == ClientHomeworkAssignment.Status.COMPLETED:
        raise HttpError(409, 'This homework assignment is already completed')
    assignment.status = ClientHomeworkAssignment.Status.COMPLETED
    assignment.completed_at = timezone.now()
    assignment.save(update_fields=['status', 'completed_at'])
    return _homework_output(assignment)


def _homework_output(assignment):
    return {'id': assignment.id, 'client_id': assignment.client_id, 'note_id': assignment.note_id,
            'status': assignment.status, 'assigned_at': assignment.assigned_at, 'completed_at': assignment.completed_at,
            'source': {'component_id': assignment.source.component_id, 'text': assignment.source.text,
                       'attribution': assignment.source.attribution, 'edition': assignment.source.edition}}


def list_homework(client):
    return [_homework_output(row) for row in client.homework_assignments.select_related('source').all()]
