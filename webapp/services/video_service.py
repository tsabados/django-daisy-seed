"""
Django-native video generation service.

All steps pass data as in-memory dicts — no file I/O, no run directories.
All utilities from video_poc are inlined here; LLM calls use _openai_chat from ai_services.
"""

import base64
import json
import logging
import mimetypes
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from django.conf import settings
from django.core.files.base import ContentFile

from media_library.models import Media, MediaGroup
from pydantic import BaseModel as PydanticBaseModel, field_validator, model_validator

from services.ai_services import (
    OpenAIModel,
    _build_media_descriptions,
    _get_brand_context,
    _openai_chat,
    analyze_media_visuals,
)

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

VIDEO_TYPES = ('teaser', 'demo', 'problem_solution', 'social_proof', 'offer')
ASPECT_RATIOS = ('21:9', '16:9', '4:3', '1:1', '3:4', '9:16')
DEFAULT_ASPECT_RATIO = '9:16'
BRIEF_COUNT = 5
CLIP_ID = 1
MIN_CLIP_DURATION = 4
MAX_CLIP_DURATION = 15
VISUAL_ANALYSIS_IMAGE_LIMIT = 3
MUAPI_REFERENCE_IMAGE_LIMIT = 9
MUAPI_SUBMIT_URL = 'https://api.muapi.ai/api/v1/seedance-2-vip-omni-reference'
MUAPI_RESULT_URL = 'https://api.muapi.ai/api/v1/predictions/{request_id}/result'
MUAPI_PROMPT_MAX_CHARS = 4000
MUAPI_PROMPT_REDUCTION_MAX_ATTEMPTS = 4
MUAPI_PROMPT_REDUCTION_TARGET_CHARS = 3400

SEEDANCE_PROMPT_HEADINGS = (
    'REFERENCE MAP:',
    'PRODUCT LOCK:',
    'FRAMING LOCK:',
    'NARRATIVE BEATS FOR ONE CONTINUOUS CLIP:',
    'MOTION DIRECTION:',
    'CREATIVE SPECIFICITY:',
    'TEXT POLICY:',
    'NEGATIVE CONSTRAINTS:',
)

SEEDANCE_SECTION_BUDGETS = {
    'reference_map': 220,
    'product_lock': 520,
    'framing_lock': 320,
    'narrative_beats': 620,
    'motion_direction': 260,
    'creative_specificity': 220,
    'text_policy': 180,
    'negative_constraints': 340,
}

VIDEO_TYPE_RULES = {
    'teaser': (
        'Build intrigue before revealing the full product value. Use restraint, sensory detail, '
        'and a clear reveal moment instead of a full explanation.'
    ),
    'demo': (
        'Show the product doing something specific. The viewer should understand use, handling, '
        'texture, scale, or styling by watching the action.'
    ),
    'problem_solution': (
        'Start from a concrete audience tension, then show how the product changes that moment. '
        'Avoid vague before/after transformations.'
    ),
    'social_proof': (
        'Use truthful proof mechanics only. Do not invent reviews, ratings, press, customer quotes, '
        'sales numbers, percentages, or testimonials. If no real proof is provided, use a non-fabricated '
        'proof proxy such as peer reaction, repeat-use ritual, customer POV, styling confidence, visible '
        'product desirability, or a real social context.'
    ),
    'offer': (
        'Make the reason to act concrete without becoming generic sale footage. Focus on product value, '
        'occasion, desirability, scarcity of attention, or wardrobe/use-case fit.'
    ),
}

MINIMAL_TEXT_POLICY = (
    'Visible text is optional and must be minimal. Allowed: brand name, product name, CTA, or short '
    'non-factual labels. Forbidden: fake reviews, star ratings, percentages, statistics, press quotes, '
    'customer quotes, testimonial snippets, or any claim not present in the source data.'
)

REFERENCE_HUMAN_POLICY = (
    'If people appear, do not reproduce the identity of any person from the reference images. Treat '
    'reference people as context only, not identity references. Invent new talent, face, hair, makeup, '
    'styling, body, and pose unless the source explicitly requires the same person.'
)

BASE_PRODUCT_LOCK_RULES = (
    'Keep the exact same product identity throughout the entire video.',
    'Preserve product color, material, shape, silhouette, texture, scale, label/logo details, trim, fringe, seams, and distinctive construction.',
    'If reference images show different physical views or sides of the product, preserve each detail only on the view where it belongs.',
    'Never merge mutually exclusive view-specific details into one visible product surface.',
    'Do not morph, recolor, resize, replace, duplicate, redesign, simplify, or swap the product.',
    'The product must be fully visible and recognizable in the opening and final hero moments.',
)

BASE_FRAMING_RULES = (
    'Do not crop or cut off the hero product in the opening or final shot.',
    'Do not let hands, hair, bags, sleeves, camera motion, foreground objects, or frame edges obstruct the product during hero moments.',
    'Avoid fast chaotic camera movement; use controlled motion that keeps the product readable.',
    'End with a clean, unobstructed product-forward composition.',
)

BASE_NEGATIVE_RULES = (
    'No product morphing, warping, melting, flickering, disappearing, changing color, changing texture, or changing label details.',
    'No combining mutually exclusive details from different product views onto the same visible surface.',
    'No fake reviews, ratings, percentages, stats, press quotes, customer quotes, or testimonial text.',
    'No distorted text, malformed logos, extra labels, watermarks, subtitles, UI overlays, or unreadable typography.',
    'No cropped final product, no obstructed hero product, no product leaving frame during the main hero beat.',
    'Do not layer or apply the hero product over another function-equivalent item unless explicitly requested.',
    'Do not keep competing function-equivalent items active or visibly in-use during hero application/reveal beats unless explicitly requested.',
    'Keep item count and state continuity logically consistent across beats; avoid contradictory simultaneous states.',
    'Do not reproduce or closely match the identity of any person visible in the reference images unless explicitly required.',
)

STATE_CONTINUITY_POLICY = (
    'State continuity is mandatory for any product category. Keep the hero product as the single active '
    'item for its function in focus context unless explicit multi-item behavior is requested. Do not '
    'layer/apply the hero product over another function-equivalent item. Do not keep competing equivalent '
    'items active during hero application or reveal beats. Keep item count and state consistent across beats.'
)

# ── Prompts ────────────────────────────────────────────────────────────────────

BRIEFS_PROMPT = """
You are a senior social video strategist.

Create exactly 5 distinct short-form video briefs for the requested video type.
The briefs are for a backend video generation experiment, so be concrete, visual, and non-generic.

Rules:
- Return only valid JSON.
- Do not use markdown.
- The root object must be {{"briefs": [...]}}.
- Each brief must have:
  id, title, hook, target_viewer, core_message, story_angle, proof_mechanism,
  viewer_tension, product_role, visual_hook, visual_direction, cta,
  why_it_fits_type, avoid_cliches.
- id values must be the integers 1 through 5.
- Each concept should be feasible as one continuous Seedance clip with 2-4 internal story beats.
- Full narrative is allowed, including people and faces.
- Product/reference images are creative references, not final first frames.
- Product visual analysis is the source of truth for visible product details.
- {reference_human_policy}
- Avoid generic fashion footage where a person merely wears or holds the product.
- Every brief must create a distinct situation, tension, proof mechanism, or product role.
- {minimal_text_policy}

Video type: {video_type}
Type-specific rules: {video_type_rules}
Aspect ratio: {aspect_ratio}

Brand:
{brand_json}

Product/reference group:
{group_json}

Reference images:
{reference_images_json}

Product visual analysis:
{visual_analysis_json}
""".strip()

SCRIPT_PROMPT = """
You are a cinematic AI-video director designing a cohesive single-clip video.

Convert the selected brief into a continuity-locked script for Seedance image-to-video.

Rules:
- Return only valid JSON.
- Do not use markdown.
- The root object must include:
  selected_brief_id, creative_treatment, continuity_rules, clip.
- creative_treatment must include:
  story_arc, visual_style, color_grade, lighting, recurring_elements,
  product_continuity_rules, character_notes, transition_intent.
- continuity_rules must be an array of concrete rules the single clip should preserve.
- clip must be one object, not an array.
- clip must include:
  id, duration, narrative_purpose, scene, beats, camera_motion, product_action,
  product_fidelity_rules, framing_rules, motion_rules, negative_rules,
  distinctiveness_notes, keyframe_prompt, seedance_prompt.
- clip.id must be 1.
- clip.duration must be an integer from 4 to 15 seconds.
- clip.beats must be 2 to 4 ordered beat objects that fit inside clip.duration.
- Each beat object must include:
  start_time, end_time, duration_seconds, visual_action, camera_motion,
  product_focus, transition_to_next.
- Beat timing must use seconds from the start of the clip. Beats must be ordered,
  non-overlapping, and must not exceed clip.duration.
- Transitions must be soft internal moves only: camera moves, focus pulls, reveals,
  object motion, lighting shifts, or pacing changes. Do not use hard cuts,
  scene cuts, edits, stitching, montage language, or post-production transitions.
- product_fidelity_rules, framing_rules, motion_rules, and negative_rules must be arrays of concrete rules.
- distinctiveness_notes must explain what makes this video non-generic for this product and selected video type.
- Full narrative is allowed, including people and faces, but keep it coherent inside one continuous clip.
- Make keyframe prompts visually specific enough for an image model.
- Make Seedance prompts motion-specific, concise, and compatible with one image-to-video generation.
- The Seedance prompt may describe multiple internal shots/beats, but it must not require stitching.
- Preserve product identity more strongly than cinematic variety.
- Treat metadata.visual_analysis as the source of truth for visible product details.
- Choose one coherent physical product view per shot and do not merge mutually exclusive view-specific details onto the same visible surface.
- {reference_human_policy}
- Do not fabricate reviews, ratings, press, stats, customer claims, or testimonial text.
- {state_continuity_policy}
- {minimal_text_policy}

Video type: {video_type}
Type-specific rules: {video_type_rules}
Aspect ratio: {aspect_ratio}

Briefs metadata:
{metadata_json}

Selected brief:
{brief_json}
""".strip()

SEEDANCE_REDUCTION_PROMPT = """
You are reducing a Seedance video-generation prompt to fit a strict character limit.

Requirements:
- Return plain text only.
- Do not use markdown fences.
- Preserve these headings exactly and in this order:
    REFERENCE MAP:
    PRODUCT LOCK:
    FRAMING LOCK:
    NARRATIVE BEATS FOR ONE CONTINUOUS CLIP:
    MOTION DIRECTION:
    CREATIVE SPECIFICITY:
    TEXT POLICY:
    NEGATIVE CONSTRAINTS:
- Keep every section present.
- Compress wording only. Do not remove product lock, framing, motion, text policy, or negative constraints.
- Prefer shortening examples, repetition, and decorative phrasing before removing essential constraints.
- Prioritize compression in this order:
    1) PRODUCT LOCK detail verbosity (keep core lock constraints)
    2) NARRATIVE BEATS wording (keep timing, action, camera, focus, transition but make it telegraphic)
    3) CREATIVE SPECIFICITY prose
    4) FRAMING/MOTION extra qualifiers
- Current attempt: {attempt}/{max_attempts}
- Characters over hard limit: {chars_over}
- Minimum shrink required this attempt: {min_shrink}
- Target length for this pass: <= {target_chars}
- Hard limit for final output: <= {max_chars}
- Output must be materially shorter than input on every attempt.

Current prompt length: {current_length}
Target max length: {max_chars}

Prompt to reduce:
{prompt}
""".strip()

SEEDANCE_REDUCTION_STRUCTURED_PROMPT = """
You are rewriting a Seedance video-generation prompt into concise sections with hard character budgets.

Return only valid JSON with exactly these keys:
- reference_map
- product_lock
- framing_lock
- narrative_beats
- motion_direction
- creative_specificity
- text_policy
- negative_constraints

Rules:
- Preserve core meaning and constraints.
- Keep all sections present and non-empty.
- Keep timing/action/camera/focus/transition intent in narrative_beats, but concise.
- Do not invent new claims or remove safety constraints.
- Do not include markdown fences.
- Each field must be <= its budget.

Budgets JSON:
{budgets_json}

Hard final full-prompt max: {max_chars}
Current prompt length: {current_length}

Prompt to rewrite:
{prompt}
""".strip()

SEEDANCE_SECTION_COMPRESSION_PROMPT = """
Compress this Seedance section text to fit a strict character budget.

Rules:
- Return plain text only.
- Keep core constraints and intent.
- Do not add new claims.
- Keep it <= {budget} characters.

Section: {section_name}
Current length: {current_length}
Target max: {budget}

Text:
{text}
""".strip()

# ── Exception ──────────────────────────────────────────────────────────────────


class VideoServiceError(Exception):
    """Raised when a video generation step cannot continue safely."""


# ── Internal helpers ────────────────────────────────────────────────────────────


def _ensure_env(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise VideoServiceError(f'{name} is required for this video generation step.')
    return value


def validate_video_type(video_type):
    if video_type not in VIDEO_TYPES:
        raise VideoServiceError(
            f'Unsupported video type "{video_type}". Expected one of: {", ".join(VIDEO_TYPES)}.'
        )
    return video_type


def validate_aspect_ratio(aspect_ratio):
    if aspect_ratio not in ASPECT_RATIOS:
        raise VideoServiceError(
            f'Unsupported aspect ratio "{aspect_ratio}". Expected one of: {", ".join(ASPECT_RATIOS)}.'
        )
    return aspect_ratio


def _parse_json_response(raw, error_prefix='AI returned invalid JSON'):
    text = str(raw or '').strip()
    if not text:
        raise VideoServiceError(f'{error_prefix}: empty response body.')
    if text.startswith('```'):
        lines = text.splitlines()
        if lines and lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith('```'):
            lines = lines[:-1]
        text = '\n'.join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError as exc:
                raise VideoServiceError(f'{error_prefix}: {exc}') from exc
        raise VideoServiceError(f'{error_prefix}: no JSON object found in response.')


def _call_json(prompt, model=OpenAIModel.QUICK):
    """Call OpenAI and parse the response as JSON."""
    raw = _openai_chat(
        messages=[{'role': 'user', 'content': prompt}],
        model=model,
        text={"verbosity": "low"}
    )
    if not str(raw or '').strip():
        raw = _openai_chat(
            messages=[{'role': 'user', 'content': (
                f'{prompt}\n\n'
                'Return only one valid JSON object. Do not use markdown fences or commentary.'
            )}],
            model=model,
            text={"verbosity": "low"}
        )
    return _parse_json_response(raw)


def _strip_code_fences(text):
    text = str(text or '').strip()
    if not text.startswith('```'):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith('```'):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith('```'):
        lines = lines[:-1]
    return '\n'.join(lines).strip()


def _stringify_value(value):
    if isinstance(value, list):
        return '; '.join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return str(value).strip()


def _normalize_text_field(value, field_name):
    text = _stringify_value(value)
    if not text:
        raise VideoServiceError(f'Missing required field "{field_name}".')
    return text


def _normalize_string_list(value, field_name, min_items=1, max_items=None):
    if not isinstance(value, list):
        raise VideoServiceError(f'{field_name} must be an array of strings.')
    normalized = [_normalize_text_field(item, field_name) for item in value]
    if len(normalized) < min_items:
        raise VideoServiceError(f'{field_name} must include at least {min_items} item(s).')
    if max_items is not None and len(normalized) > max_items:
        raise VideoServiceError(f'{field_name} must include at most {max_items} item(s).')
    return normalized


def _normalize_seconds(value, field_name):
    if isinstance(value, bool):
        raise VideoServiceError(f'{field_name} must be a number of seconds.')
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        text = value.strip().lower()
        for suffix in ('seconds', 'second', 's'):
            if text.endswith(suffix):
                text = text[:-len(suffix)].strip()
                break
        try:
            seconds = float(text)
        except ValueError as exc:
            raise VideoServiceError(f'{field_name} must be a number of seconds.') from exc
    else:
        raise VideoServiceError(f'{field_name} must be a number of seconds.')
    if seconds < 0:
        raise VideoServiceError(f'{field_name} must be zero or greater.')
    return int(seconds) if float(seconds).is_integer() else round(seconds, 2)


def _normalize_beat_payload(beats, clip_duration):
    if not isinstance(beats, list):
        raise VideoServiceError(
            'clip.beats must be an array of structured beat objects. Regenerate old string-beat scripts.'
        )
    if not (2 <= len(beats) <= 4):
        raise VideoServiceError('clip.beats must include 2 to 4 beat objects.')
    normalized = []
    previous_end = None
    text_fields = ('visual_action', 'camera_motion', 'product_focus', 'transition_to_next')
    for index, beat in enumerate(beats, 1):
        if not isinstance(beat, dict):
            raise VideoServiceError(
                'clip.beats must be an array of structured beat objects. Regenerate old string-beat scripts.'
            )
        start_time = _normalize_seconds(beat.get('start_time'), f'clip.beats[{index}].start_time')
        end_time = _normalize_seconds(beat.get('end_time'), f'clip.beats[{index}].end_time')
        duration_seconds = _normalize_seconds(beat.get('duration_seconds'), f'clip.beats[{index}].duration_seconds')
        if end_time <= start_time:
            raise VideoServiceError(f'clip.beats[{index}] end_time must be after start_time.')
        if end_time > clip_duration:
            raise VideoServiceError(f'clip.beats[{index}] end_time exceeds clip.duration.')
        if previous_end is not None and start_time < previous_end:
            raise VideoServiceError('clip.beats must be ordered and non-overlapping.')
        if abs((end_time - start_time) - duration_seconds) > 0.25:
            raise VideoServiceError(f'clip.beats[{index}] duration_seconds must match end_time - start_time.')
        item = {'start_time': start_time, 'end_time': end_time, 'duration_seconds': duration_seconds}
        for field in text_fields:
            item[field] = _normalize_text_field(beat.get(field, ''), f'clip.beats[{index}].{field}')
        normalized.append(item)
        previous_end = end_time
    return normalized


# ── Visual analysis ────────────────────────────────────────────────────────────

VISUAL_ANALYSIS_PROMPT = """
You are a product visual analyst for an AI video generation pipeline.

Analyze the attached product/reference images and return structured visual facts only.
Do not invent product details that are not visible.
If an image is ambiguous, say what is ambiguous in avoid_assumptions.

Rules:
- Return only valid JSON.
- Do not use markdown.
- The root object must include:
  product_identity_summary, visible_attributes, colors, materials_textures,
  shape_silhouette, logos_labels_text, view_specific_details,
  mutually_exclusive_details, view_separation_rules, scale_fit_usage,
  styling_context, product_fidelity_rules, avoid_assumptions.
- visible_attributes, colors, materials_textures, logos_labels_text,
  view_specific_details, mutually_exclusive_details, view_separation_rules,
  scale_fit_usage, styling_context, product_fidelity_rules, and avoid_assumptions
  must be arrays of strings.
- product_identity_summary and shape_silhouette must be strings.
- Focus on facts useful for keeping the product consistent in generated keyframes and video.
- Include any visible labels/logos/text only if they are readable.
- If reference images show different physical sides, angles, surfaces, package faces,
  components, variants, or states of the same product, describe those as view_specific_details.
- Use mutually_exclusive_details for details that cannot physically appear together on
  the same visible surface, face, side, component, or state.
- Create view_separation_rules that explicitly forbid merging details across incompatible
  views. Examples: front/back garment artwork, front/back packaging labels, shoe inner/outer
  side details, bag exterior/interior details, bottle/box details.
- Avoid brand claims, quality claims, customer claims, ratings, stats, reviews, or press language.

Product/reference group metadata:
{group_json}

Reference image metadata:
{reference_images_json}
""".strip()


def _image_data_url(image):
    if image.file and image.file.name:
        path = Path(image.file.path)
        mime_type = mimetypes.guess_type(path.name)[0] or 'image/jpeg'
        image_data = path.read_bytes()
    elif image.external_url:
        response = requests.get(image.external_url, timeout=30)
        response.raise_for_status()
        mime_type = response.headers.get('content-type', '').split(';')[0] or 'image/jpeg'
        image_data = response.content
    else:
        return ''
    encoded = base64.b64encode(image_data).decode('ascii')
    return f'data:{mime_type};base64,{encoded}'


def _normalize_optional_string_list(value):
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def image_reference_summary(image):
    group = image.media_group
    source = image.external_url or (image.file.name if image.file else '')
    return {'id': image.id, 'group_title': group.title, 'group_description': group.description, 'source': source}


def build_group_context(group):
    brand = getattr(group.project, 'brand', None)
    brand_context = _get_brand_context(brand) if brand else {
        'brand_name': group.project.name,
        'brand_summary': '',
        'brand_style_guide': '',
    }
    images = list(group.media_items.filter(media_type='image').order_by('id'))
    return {
        'project': {'id': group.project_id, 'name': group.project.name},
        'brand': brand_context,
        'group': {'id': group.id, 'title': group.title, 'description': group.description, 'type': group.type},
        'reference_images': [image_reference_summary(image) for image in images],
    }


def _video_type_rules(video_type):
    return VIDEO_TYPE_RULES[validate_video_type(video_type)]


def build_briefs_prompt(video_type, aspect_ratio, context):
    validate_video_type(video_type)
    validate_aspect_ratio(aspect_ratio)
    return BRIEFS_PROMPT.format(
        video_type=video_type,
        video_type_rules=_video_type_rules(video_type),
        aspect_ratio=aspect_ratio,
        minimal_text_policy=MINIMAL_TEXT_POLICY,
        reference_human_policy=REFERENCE_HUMAN_POLICY,
        brand_json=json.dumps(context['brand'], indent=2),
        group_json=json.dumps(context['group'], indent=2),
        reference_images_json=json.dumps(context['reference_images'], indent=2),
        visual_analysis_json=json.dumps(context.get('visual_analysis', {}), indent=2),
    )


def build_script_prompt(briefs_payload, brief):
    video_type = validate_video_type(briefs_payload['video_type'])
    aspect_ratio = validate_aspect_ratio(briefs_payload.get('aspect_ratio', DEFAULT_ASPECT_RATIO))
    return SCRIPT_PROMPT.format(
        video_type=video_type,
        video_type_rules=_video_type_rules(video_type),
        aspect_ratio=aspect_ratio,
        state_continuity_policy=STATE_CONTINUITY_POLICY,
        minimal_text_policy=MINIMAL_TEXT_POLICY,
        reference_human_policy=REFERENCE_HUMAN_POLICY,
        metadata_json=json.dumps(briefs_payload.get('metadata', {}), indent=2),
        brief_json=json.dumps(brief, indent=2),
    )


def validate_script_payload(script_payload):
    if not isinstance(script_payload, dict):
        raise VideoServiceError('Script must be a JSON object.')
    treatment = script_payload.get('creative_treatment')
    if not isinstance(treatment, dict):
        raise VideoServiceError('Script must include creative_treatment.')
    for key in ('story_arc', 'visual_style', 'color_grade', 'lighting', 'recurring_elements',
                'product_continuity_rules', 'character_notes', 'transition_intent'):
        if key not in treatment:
            raise VideoServiceError(f'creative_treatment is missing "{key}".')
    continuity_rules = script_payload.get('continuity_rules')
    if not isinstance(continuity_rules, list) or not continuity_rules:
        raise VideoServiceError('Script must include at least one continuity rule.')
    clip = script_payload.get('clip')
    if not isinstance(clip, dict):
        raise VideoServiceError('Script must include one "clip" object. Regenerate old multi-clip scripts.')
    clip_id = int(clip.get('id', CLIP_ID))
    if clip_id != CLIP_ID:
        raise VideoServiceError('Single-clip script must use clip.id = 1.')
    duration = int(clip.get('duration', 0))
    if not (MIN_CLIP_DURATION <= duration <= MAX_CLIP_DURATION):
        raise VideoServiceError(f'Clip duration must be {MIN_CLIP_DURATION}-{MAX_CLIP_DURATION} seconds.')
    normalized_beats = _normalize_beat_payload(clip.get('beats'), duration)
    normalized = {
        'id': clip_id,
        'duration': duration,
        'beats': normalized_beats,
        'product_fidelity_rules': _normalize_string_list(clip.get('product_fidelity_rules'), 'clip.product_fidelity_rules'),
        'framing_rules': _normalize_string_list(clip.get('framing_rules'), 'clip.framing_rules'),
        'motion_rules': _normalize_string_list(clip.get('motion_rules'), 'clip.motion_rules'),
        'negative_rules': _normalize_string_list(clip.get('negative_rules'), 'clip.negative_rules'),
        'distinctiveness_notes': _normalize_text_field(clip.get('distinctiveness_notes', ''), 'clip.distinctiveness_notes'),
    }
    for key in ('narrative_purpose', 'scene', 'camera_motion', 'product_action', 'keyframe_prompt', 'seedance_prompt'):
        normalized[key] = _normalize_text_field(clip.get(key, ''), f'clip.{key}')
    script_payload['clip'] = normalized
    return script_payload


def _product_identity(metadata):
    group = metadata.get('group') or {}
    title = group.get('title') or 'the product'
    description = group.get('description') or ''
    return f'{title}: {description}'.strip(': ')


def _bullets(items):
    return '\n'.join(f'- {item}' for item in items)


def _format_seconds(value):
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f'{value}s'


def _timed_beat_plan(beats):
    lines = []
    for beat in beats:
        lines.append(
            f"{_format_seconds(beat['start_time'])}-{_format_seconds(beat['end_time'])} "
            f"({beat['duration_seconds']}s): {beat['visual_action']} "
            f"Camera: {beat['camera_motion']} "
            f"Product focus: {beat['product_focus']} "
            f"Transition: {beat['transition_to_next']}"
        )
    return '\n'.join(f'{i}. {line}' for i, line in enumerate(lines, 1))


def _shorten(text, limit=500):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else f'{text[:limit - 3].rstrip()}...'


def _enforce_prompt_limit(prompt, max_chars=MUAPI_PROMPT_MAX_CHARS):
    if len(prompt) <= max_chars:
        return prompt
    raise VideoServiceError(
        f'Muapi prompt length is {len(prompt)} characters (max {max_chars}). '
        'Final prompt reduction could not compress it enough.'
    )


def _validate_seedance_prompt_sections(prompt):
    missing = [h for h in SEEDANCE_PROMPT_HEADINGS if h not in prompt]
    if missing:
        raise VideoServiceError(f'Reduced Seedance prompt is missing required sections: {", ".join(missing)}')


def _build_seedance_prompt_from_sections(sections):
    return f"""
REFERENCE MAP:
{sections['reference_map']}

PRODUCT LOCK:
{sections['product_lock']}

FRAMING LOCK:
{sections['framing_lock']}

NARRATIVE BEATS FOR ONE CONTINUOUS CLIP:
{sections['narrative_beats']}

MOTION DIRECTION:
{sections['motion_direction']}

CREATIVE SPECIFICITY:
{sections['creative_specificity']}

TEXT POLICY:
{sections['text_policy']}

NEGATIVE CONSTRAINTS:
{sections['negative_constraints']}
""".strip()


def _reduce_seedance_prompt_structured(prompt, max_chars=MUAPI_PROMPT_MAX_CHARS):
    structured_prompt = SEEDANCE_REDUCTION_STRUCTURED_PROMPT.format(
        budgets_json=json.dumps(SEEDANCE_SECTION_BUDGETS, indent=2),
        max_chars=max_chars,
        current_length=len(prompt),
        prompt=prompt,
    )
    sections = _call_json(structured_prompt, model=OpenAIModel.QUICK)
    if not isinstance(sections, dict):
        raise VideoServiceError('Structured Seedance reduction did not return a JSON object.')

    def _compress_section(section_name, text, budget):
        current = text
        for _ in range(1, 4):
            if len(current) <= budget:
                return current
            compression_prompt = SEEDANCE_SECTION_COMPRESSION_PROMPT.format(
                section_name=section_name, current_length=len(current), budget=budget, text=current,
            )
            compressed = _openai_chat(
                messages=[{'role': 'user', 'content': compression_prompt}],
                model=OpenAIModel.QUICK,
                max_output_tokens=900,
            )
            compressed = _strip_code_fences(compressed)
            if compressed:
                current = compressed.strip()
        return current

    normalized = {}
    for key, budget in SEEDANCE_SECTION_BUDGETS.items():
        text = str(sections.get(key, '') or '').strip()
        if not text:
            raise VideoServiceError(f'Structured Seedance reduction missing section: {key}.')
        normalized[key] = _compress_section(key, text, budget)

    rebuilt = _build_seedance_prompt_from_sections(normalized)
    _validate_seedance_prompt_sections(rebuilt)
    return rebuilt


def _reduce_seedance_prompt(prompt, max_chars=MUAPI_PROMPT_MAX_CHARS):
    # Proactive budget enforcement in build_seedance_prompt means this path is
    # rarely reached. When it is, go directly to the structured JSON rewrite
    # (more reliable than free-form text reduction) then do up to 4 clean-up
    # passes if the rebuilt prompt is still marginally over budget.
    structured = _reduce_seedance_prompt_structured(prompt, max_chars=max_chars)
    for i in range(1, 5):
        if len(structured) <= max_chars:
            break
        final_candidate = _openai_chat(
            messages=[{'role': 'user', 'content': SEEDANCE_REDUCTION_PROMPT.format(
                attempt=i,
                max_attempts=4,
                current_length=len(structured),
                chars_over=max(0, len(structured) - max_chars),
                min_shrink=max(150, max(0, len(structured) - max_chars)),
                target_chars=min(max_chars - 100, MUAPI_PROMPT_REDUCTION_TARGET_CHARS),
                max_chars=max_chars,
                prompt=structured,
            )}],
            model=OpenAIModel.QUICK,
            max_output_tokens=1800,
        )
        final_candidate = _strip_code_fences(final_candidate)
        if final_candidate:
            _validate_seedance_prompt_sections(final_candidate)
            structured = final_candidate
    return _enforce_prompt_limit(structured, max_chars=max_chars)


def _seedance_reference_rules(product_reference_count=0, create_keyframe=False):
    if product_reference_count < 1:
        raise VideoServiceError('Muapi render requires at least one product reference image.')
    rules = []
    if create_keyframe:
        rules.append('Use @image1 as the generated keyframe, opening composition, lighting/world anchor, and continuity anchor.')
        first_product_index = 2
    else:
        rules.append('Use all @image references as product identity references.')
        first_product_index = 1
    if product_reference_count:
        end_label = f'@image{first_product_index + product_reference_count - 1}'
        product_range = (
            f'@image{first_product_index}' if product_reference_count == 1
            else f'@image{first_product_index} through {end_label}'
        )
        rules.extend([
            f'Use {product_range} as product identity references only.',
            f'Match the product color, material, shape, silhouette, scale, texture, label/logo details, trim, seams, and distinctive construction from {product_range}.',
            f'Do not cut away into {product_range} as catalog photos; use them to preserve product fidelity inside the generated scene.',
        ])
    return rules


def build_seedance_prompt(script_payload, clip, product_reference_count=0, create_keyframe=False):
    metadata = script_payload.get('metadata', {})
    visual_analysis = metadata.get('visual_analysis') or {}
    group = metadata.get('group') or {}
    product_name = group.get('title') or 'the hero product'

    view_specific_details = (visual_analysis.get('view_specific_details') or [])[:2]
    mutually_exclusive_details = (visual_analysis.get('mutually_exclusive_details') or [])[:2]
    view_separation_rules = (visual_analysis.get('view_separation_rules') or [])[:2]
    clip_fidelity_rules = (clip.get('product_fidelity_rules') or [])[:3]

    product_facts = [
        visual_analysis.get('product_identity_summary') or _product_identity(metadata),
        *view_specific_details,
        *mutually_exclusive_details,
        *view_separation_rules,
        *clip_fidelity_rules,
    ]
    framing_rules = [BASE_FRAMING_RULES[0], BASE_FRAMING_RULES[-1], *clip['framing_rules'][:4]]
    motion_rules = [clip['camera_motion'], clip['product_action'], *clip['motion_rules'][:3]]
    negative_rules = [
        'Do not morph, warp, melt, flicker, recolor, resize, replace, or change product details.',
        BASE_NEGATIVE_RULES[1],
        'No fake reviews, ratings, percentages, stats, press quotes, customer quotes, or testimonial text.',
        BASE_NEGATIVE_RULES[5],
        BASE_NEGATIVE_RULES[6],
        BASE_NEGATIVE_RULES[7],
        BASE_NEGATIVE_RULES[8],
        *clip['negative_rules'][:3],
    ]
    creative_specificity = [
        _shorten(clip['distinctiveness_notes'], limit=220),
        _shorten(clip['seedance_prompt'], limit=700),
    ]
    reference_rules = _seedance_reference_rules(product_reference_count, create_keyframe=create_keyframe)

    # Proactively cap each section to its budget before assembly so the prompt
    # is always within MUAPI_PROMPT_MAX_CHARS in the common case, avoiding LLM
    # reduction calls entirely.
    sec_reference_map = _shorten(
        _bullets(reference_rules),
        SEEDANCE_SECTION_BUDGETS['reference_map'],
    )
    sec_product_lock = _shorten(
        f'- Hero product: {product_name}\n'
        f'- Keep the exact same product identity throughout the video.\n'
        f'- Preserve only these product facts and do not invent extra markings: {"; ".join(product_facts)}\n'
        f'- Keep one coherent physical product view per shot; do not merge incompatible sides, surfaces, components, variants, or states.',
        SEEDANCE_SECTION_BUDGETS['product_lock'],
    )
    sec_framing_lock = _shorten(
        _bullets(framing_rules),
        SEEDANCE_SECTION_BUDGETS['framing_lock'],
    )
    sec_narrative_beats = _shorten(
        _timed_beat_plan(clip['beats']),
        SEEDANCE_SECTION_BUDGETS['narrative_beats'],
    )
    sec_motion_direction = _shorten(
        _bullets(motion_rules),
        SEEDANCE_SECTION_BUDGETS['motion_direction'],
    )
    sec_creative_specificity = _shorten(
        _bullets(creative_specificity),
        SEEDANCE_SECTION_BUDGETS['creative_specificity'],
    )
    sec_text_policy = _shorten(
        f'- {MINIMAL_TEXT_POLICY}',
        SEEDANCE_SECTION_BUDGETS['text_policy'],
    )
    sec_negative_constraints = _shorten(
        _bullets(negative_rules),
        SEEDANCE_SECTION_BUDGETS['negative_constraints'],
    )

    prompt = f"""
REFERENCE MAP:
{sec_reference_map}

PRODUCT LOCK:
{sec_product_lock}

FRAMING LOCK:
{sec_framing_lock}

NARRATIVE BEATS FOR ONE CONTINUOUS CLIP:
{sec_narrative_beats}

MOTION DIRECTION:
{sec_motion_direction}

CREATIVE SPECIFICITY:
{sec_creative_specificity}

TEXT POLICY:
{sec_text_policy}

NEGATIVE CONSTRAINTS:
{sec_negative_constraints}
""".strip()
    _validate_seedance_prompt_sections(prompt)
    if len(prompt) <= MUAPI_PROMPT_MAX_CHARS:
        return prompt
    return _reduce_seedance_prompt(prompt, max_chars=MUAPI_PROMPT_MAX_CHARS)


def product_reference_urls(product_images):
    urls = []
    for image in product_images[:MUAPI_REFERENCE_IMAGE_LIMIT - 1]:
        url = _image_public_url(image)
        if url:
            urls.append(url)
    return urls


def _image_public_url(image):
    if image.external_url:
        return image.external_url
    if image.file and image.file.name:
        return _public_media_url(image.file.path)
    return ''


def _public_base_url():
    for candidate in [
        os.environ.get('SITE_URL', '').strip(),
        os.environ.get('NGROK_URL', '').strip(),
        getattr(settings, 'SITE_URL', '').strip(),
    ]:
        if candidate:
            return candidate.rstrip('/')
    raise VideoServiceError('SITE_URL or NGROK_URL is required to build public reference URLs.')


def _public_media_url(path):
    media_root = Path(settings.MEDIA_ROOT).resolve()
    target = Path(path).resolve()
    try:
        relative = target.relative_to(media_root)
    except ValueError as exc:
        raise VideoServiceError(f'{target} is not inside MEDIA_ROOT.') from exc
    base_url = _public_base_url()
    host = urlparse(base_url).hostname or ''
    if host in {'localhost', '127.0.0.1', '0.0.0.0'} or host.endswith('.local'):
        raise VideoServiceError('SITE_URL/NGROK_URL must be public; localhost cannot be fetched by Muapi.')
    quoted = '/'.join(quote(part) for part in relative.parts)
    return f'{base_url}{settings.MEDIA_URL}{quoted}'


def build_muapi_payload(script_payload, clip, product_reference_urls, keyframe_url=None, create_keyframe=False):
    ref_urls = list(product_reference_urls or [])
    max_refs = MUAPI_REFERENCE_IMAGE_LIMIT - 1 if create_keyframe else MUAPI_REFERENCE_IMAGE_LIMIT
    ref_urls = ref_urls[:max_refs]
    if not ref_urls:
        raise VideoServiceError('Muapi payload requires at least one product reference image.')
    images_list = list(ref_urls)
    if create_keyframe:
        if not keyframe_url:
            raise VideoServiceError('create_keyframe=True requires a keyframe URL.')
        images_list = [keyframe_url, *ref_urls]
    return {
        'prompt': build_seedance_prompt(script_payload, clip, product_reference_count=len(ref_urls), create_keyframe=create_keyframe),
        'images_list': images_list,
        'video_files': [],
        'audio_files': [],
        'aspect_ratio': script_payload.get('aspect_ratio') or DEFAULT_ASPECT_RATIO,
        'duration': clip['duration'],
    }


def _muapi_headers():
    return {'Content-Type': 'application/json', 'x-api-key': _ensure_env('MUAPIAPP_API_KEY')}


def submit_muapi_clip(payload):
    response = requests.post(MUAPI_SUBMIT_URL, headers=_muapi_headers(), json=payload, timeout=60)
    if response.status_code >= 400:
        try:
            error_body = response.json()
        except ValueError:
            error_body = response.text[:2000]
        raise VideoServiceError(f'Muapi submit failed ({response.status_code} {response.reason}): {error_body}')
    data = response.json()
    request_id = data.get('request_id') or data.get('id') or data.get('output', {}).get('id')
    if not request_id:
        raise VideoServiceError(f'Muapi did not return a request_id: {data}')
    return request_id, data


def poll_muapi_clip(request_id, poll_interval=5, max_wait_seconds=900):
    deadline = time.monotonic() + max_wait_seconds
    last_data = {}
    while time.monotonic() < deadline:
        response = requests.get(
            MUAPI_RESULT_URL.format(request_id=request_id),
            headers=_muapi_headers(),
            timeout=60,
        )
        response.raise_for_status()
        last_data = response.json()
        output = last_data.get('output') if isinstance(last_data, dict) else {}
        if not isinstance(output, dict):
            output = {}
        status = str(output.get('status') or last_data.get('status') or '').lower()
        if status in {'completed', 'succeeded', 'success'}:
            outputs = output.get('outputs') or last_data.get('outputs') or []
            output_url = (outputs[0] if isinstance(outputs, list) and outputs else
                         (outputs if isinstance(outputs, str) else
                          output.get('video_url') or output.get('url') or last_data.get('video_url') or ''))
            if not output_url:
                raise VideoServiceError(f'Muapi completed without an output URL: {last_data}')
            return output_url, last_data
        if status in {'failed', 'error', 'canceled', 'cancelled'}:
            error = output.get('error') or last_data.get('error') or 'Muapi generation failed.'
            raise VideoServiceError(str(error))
        time.sleep(poll_interval)
    raise VideoServiceError(f'Muapi timed out waiting for request {request_id}: {last_data}')


def download_file(url, output_path):
    response = requests.get(url, timeout=120, stream=True)
    response.raise_for_status()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open('wb') as handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                handle.write(chunk)
    return output_path


class _BriefItem(PydanticBaseModel):
    id: int
    title: str
    hook: str
    target_viewer: str
    core_message: str
    story_angle: str
    proof_mechanism: str
    viewer_tension: str
    product_role: str
    visual_hook: str
    visual_direction: str
    cta: str
    why_it_fits_type: str
    avoid_cliches: str

    @field_validator(
        'title', 'hook', 'target_viewer', 'core_message', 'story_angle',
        'proof_mechanism', 'viewer_tension', 'product_role', 'visual_hook',
        'visual_direction', 'cta', 'why_it_fits_type', 'avoid_cliches',
        mode='after',
    )
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError('field must not be empty')
        return v


class _BriefsResponse(PydanticBaseModel):
    briefs: list[_BriefItem]

    @model_validator(mode='after')
    def _validate_briefs(self) -> '_BriefsResponse':
        if len(self.briefs) != BRIEF_COUNT:
            raise ValueError(f'Expected exactly {BRIEF_COUNT} briefs, got {len(self.briefs)}.')
        ids = [b.id for b in self.briefs]
        if sorted(ids) != list(range(1, BRIEF_COUNT + 1)):
            raise ValueError(f'Brief ids must be 1 through {BRIEF_COUNT}, got {ids}.')
        return self


def generate_video_briefs(seed_media_ids, brand, video_type, aspect_ratio=DEFAULT_ASPECT_RATIO):
    """Return a list of 5 normalized video brief dicts for the given seed media and brand.

    Does no file I/O — calls OpenAI and returns the brief list directly.
    """
    validate_video_type(video_type)
    validate_aspect_ratio(aspect_ratio)

    seed_media = list(
        Media.objects.filter(id__in=seed_media_ids)
        .select_related('media_group')
        .order_by('id')
    ) if seed_media_ids else []

    if not seed_media:
        raise VideoServiceError('Seed media is required to generate video briefs.')

    brand_context = _get_brand_context(brand)
    media_descriptions = _build_media_descriptions(seed_media)

    images = [m for m in seed_media if m.media_type == 'image'][:VISUAL_ANALYSIS_IMAGE_LIMIT]
    if not images:
        raise VideoServiceError('Visual analysis requires at least one image in the seed media.')

    visual_analysis = analyze_media_visuals(images)
    if not visual_analysis:
        raise VideoServiceError('Visual analysis returned empty results.')

    context = {
        'brand': brand_context,
        'media_descriptions': media_descriptions,
        'reference_images': [image_reference_summary(img) for img in images],
        'visual_analysis': visual_analysis,
        'group': {},
    }

    prompt = build_briefs_prompt(video_type, aspect_ratio, context)
    result = _openai_chat(
        messages=[{'role': 'user', 'content': prompt}],
        text_format=_BriefsResponse,
    )
    validate_video_type(video_type)
    return [b.model_dump() for b in result.briefs]


def generate_video_script(seed_media_ids, brand, brief, video_type, aspect_ratio=DEFAULT_ASPECT_RATIO):
    """Generate a validated script dict from a brief dict.

    Builds an in-memory briefs_payload, calls OpenAI, and returns the validated script dict.
    """
    validate_video_type(video_type)
    validate_aspect_ratio(aspect_ratio)

    seed_media = list(
        Media.objects.filter(id__in=seed_media_ids)
        .select_related('media_group')
        .order_by('id')
    ) if seed_media_ids else []

    if not seed_media:
        raise VideoServiceError('Seed media is required to generate a video script.')

    brand_context = _get_brand_context(brand)
    media_descriptions = _build_media_descriptions(seed_media)

    images = [m for m in seed_media if m.media_type == 'image'][:VISUAL_ANALYSIS_IMAGE_LIMIT]
    context = {
        'brand': brand_context,
        'media_descriptions': media_descriptions,
        'reference_images': [image_reference_summary(img) for img in images],
        'seed_media_ids': list(seed_media_ids) if seed_media_ids else [],
        'group': {},
    }
    if images:
        context['visual_analysis'] = analyze_media_visuals(images)

    briefs_payload = {
        'video_type': video_type,
        'aspect_ratio': aspect_ratio,
        'metadata': context,
        'briefs': [brief],
    }

    prompt = build_script_prompt(briefs_payload, brief)
    response = _call_json(prompt)
    return validate_script_payload(response)


def render_video_to_media(script_dict, user, project, seed_media_ids=None):
    """Submit a validated script dict to Muapi, download the resulting mp4,
    and return a saved Media object attached to the project's generated MediaGroup.

    Requires MUAPIAPP_API_KEY env var to be set.
    """
    product_images = (
        list(Media.objects.filter(id__in=seed_media_ids, media_type='image').order_by('id'))
        if seed_media_ids else []
    )

    if not product_images:
        raise VideoServiceError('No product images found for the given group; cannot build Muapi payload.')

    ref_urls = product_reference_urls(product_images)
    if not ref_urls:
        raise VideoServiceError('No public product reference URLs available for Muapi submission.')

    clip = script_dict['clip']
    payload = build_muapi_payload(
        script_dict,
        clip,
        product_reference_urls=ref_urls,
        keyframe_url=None,
        create_keyframe=False,
    )

    request_id, _ = submit_muapi_clip(payload)
    output_url, _ = poll_muapi_clip(request_id)

    media_group = product_images[0].media_group

    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        download_file(output_url, tmp_path)
        video_bytes = tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)

    media_obj = Media(
        media_group=media_group,
        source_type=Media.SourceType.GENERATED,
        media_type=Media.MediaType.VIDEO,
        external_url='',
    )
    media_obj.file.save('ai_generated_video.mp4', ContentFile(video_bytes), save=True)
    return media_obj
