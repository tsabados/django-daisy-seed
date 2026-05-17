import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from brand.models import Brand
from projects.models import Project
from services.video_poc import (
    VIDEO_TYPE_RULES,
    VIDEO_TYPES,
    build_briefs_prompt,
    build_script_prompt,
    build_seedance_prompt,
    VideoPocError,
    generate_briefs,
    generate_script,
    render_script,
    validate_script_payload,
)

from .models import Media, MediaGroup


def _briefs_response():
    return {
        'briefs': [
            {
                'id': i,
                'title': f'Concept {i}',
                'hook': f'Hook {i}',
                'target_viewer': 'Busy shoppers',
                'core_message': 'This product makes the moment easier.',
                'story_angle': f'Specific story angle {i}',
                'proof_mechanism': 'A truthful product-use moment, not a fabricated claim.',
                'viewer_tension': 'The viewer wants polish without overthinking the outfit.',
                'product_role': 'The product visibly changes the outfit moment.',
                'visual_hook': 'A tactile closeup that reveals the product detail.',
                'visual_direction': 'Warm, cinematic, tactile closeups.',
                'cta': 'Shop now',
                'why_it_fits_type': 'It builds curiosity quickly.',
                'avoid_cliches': 'Avoid generic walking shots and vague luxury language.',
            }
            for i in range(1, 6)
        ]
    }


def _script_response():
    return {
        'selected_brief_id': 2,
        'creative_treatment': {
            'story_arc': 'A small daily problem becomes a satisfying brand moment.',
            'visual_style': 'Cinematic handheld lifestyle footage.',
            'color_grade': 'Warm neutrals with soft contrast.',
            'lighting': 'Natural window light and practical highlights.',
            'recurring_elements': ['wood table', 'morning light'],
            'product_continuity_rules': ['Keep the product color and silhouette identical.'],
            'character_notes': 'Characters can appear naturally if useful.',
            'transition_intent': 'Each clip should feel like the next beat in the same story.',
        },
        'continuity_rules': [
            'Preserve warm color grade.',
            'Keep product scale and finish consistent.',
        ],
        'clip': {
            'id': 1,
            'duration': 8,
            'narrative_purpose': 'A compact product story with a hook, use moment, and payoff.',
            'scene': 'Warm kitchen counter in morning light.',
            'beats': [
                {
                    'start_time': 0,
                    'end_time': 2,
                    'duration_seconds': 2,
                    'visual_action': 'Camera finds the product as the morning routine starts.',
                    'camera_motion': 'Slow push from the counter edge toward the product.',
                    'product_focus': 'Hero product is fully visible in warm light.',
                    'transition_to_next': 'Soft focus pull toward the hands entering frame.',
                },
                {
                    'start_time': 2,
                    'end_time': 6,
                    'duration_seconds': 4,
                    'visual_action': 'Hands use the product naturally in context.',
                    'camera_motion': 'Gentle handheld follow-through with controlled movement.',
                    'product_focus': 'Product remains readable during the main action.',
                    'transition_to_next': 'Small lighting shift and camera settle into final hero angle.',
                },
                {
                    'start_time': 6,
                    'end_time': 8,
                    'duration_seconds': 2,
                    'visual_action': 'Finish on a clean hero moment with the product visible.',
                    'camera_motion': 'Slow stabilizing push in.',
                    'product_focus': 'Product is unobstructed in the final composition.',
                    'transition_to_next': 'No transition; hold the final hero frame.',
                },
            ],
            'product_fidelity_rules': [
                'Keep the same product color, material, shape, texture, and label details.',
                'Do not replace the product with a different accessory.',
            ],
            'framing_rules': [
                'Keep the full hero product visible at the beginning and end.',
                'Do not crop the product during the final hero moment.',
            ],
            'motion_rules': [
                'Use slow controlled camera movement.',
                'Keep the product readable throughout the main action.',
            ],
            'negative_rules': [
                'No morphing, warping, recoloring, or changing product details.',
                'No fake reviews, ratings, stats, press quotes, or testimonial text.',
            ],
            'distinctiveness_notes': 'The video focuses on a concrete morning routine rather than generic fashion posing.',
            'camera_motion': 'Slow push in, then gentle handheld follow-through.',
            'product_action': 'Product is picked up and used naturally.',
            'keyframe_prompt': 'Cinematic product keyframe in warm morning light.',
            'seedance_prompt': 'Begin on the hero product, push in slowly, reveal hands using it, and end on a clean hero product moment.',
        },
    }


def _visual_analysis_response():
    return {
        'product_identity_summary': 'A tactile hero product with clear visible details.',
        'visible_attributes': ['single hero product'],
        'colors': ['warm neutral'],
        'materials_textures': ['soft texture'],
        'shape_silhouette': 'compact product silhouette',
        'logos_labels_text': [],
        'scale_fit_usage': ['used by hand in a daily routine'],
        'styling_context': ['warm lifestyle setting'],
        'product_fidelity_rules': ['Keep the product shape and color consistent.'],
        'avoid_assumptions': ['Do not invent logos or claims.'],
    }


def _briefs_payload(run_id='test-run', group_id=1):
    return {
        'run_id': run_id,
        'created_at': '2026-04-27T00:00:00+00:00',
        'video_type': 'teaser',
        'aspect_ratio': '9:16',
        'metadata': {
            'project': {'id': 1, 'name': 'Project'},
            'brand': {
                'brand_name': 'Test Brand',
                'brand_summary': 'A useful brand.',
                'brand_language': 'English',
                'brand_style_guide': 'Warm and precise.',
            },
            'group': {'id': group_id, 'title': 'Hero Product', 'description': 'A product.'},
            'reference_images': [],
        },
        'briefs': _briefs_response()['briefs'],
    }


def _script_payload(run_id='render-run', group_id=1):
    return {
        'run_id': run_id,
        'created_at': '2026-04-27T00:00:00+00:00',
        'source_briefs_path': '',
        'video_type': 'teaser',
        'aspect_ratio': '9:16',
        'metadata': _briefs_payload(run_id=run_id, group_id=group_id)['metadata'],
        'selected_brief': _briefs_response()['briefs'][0],
        **_script_response(),
    }


class VideoPocTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            email='video@example.com',
            password='password123',
        )
        self.project = Project.objects.create(owner=self.user, name='Video Project')
        Brand.objects.create(
            user=self.user,
            project=self.project,
            name='Video Brand',
            summary='A brand for useful products.',
            language='English',
            style_guide='Warm, direct, cinematic.',
        )
        self.group = MediaGroup.objects.create(
            user=self.user,
            project=self.project,
            title='Hero Product',
            description='A tactile product with a clear daily use case.',
            type=MediaGroup.GroupType.PRODUCT,
        )
        self.original_product_url = 'https://cdn.example.com/original-product.jpg'
        Media.objects.create(media_group=self.group, external_url=self.original_product_url)

    def test_generate_briefs_writes_five_video_native_briefs(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(MEDIA_ROOT=tmpdir):
            with patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'}):
                with patch('services.video_poc._openai_vision_json', return_value=_visual_analysis_response()):
                    with patch('services.video_poc._openai_json', return_value=_briefs_response()):
                        output_path = generate_briefs(
                            group_id=self.group.id,
                            video_type='teaser',
                            run_id='brief-run',
                        )

            payload = json.loads(Path(output_path).read_text())
            self.assertEqual(payload['video_type'], 'teaser')
            self.assertEqual(payload['metadata']['group']['id'], self.group.id)
            self.assertIn('visual_analysis', payload['metadata'])
            self.assertEqual(len(payload['briefs']), 5)
            self.assertEqual([brief['id'] for brief in payload['briefs']], [1, 2, 3, 4, 5])
            self.assertIn('story_angle', payload['briefs'][0])
            self.assertIn('proof_mechanism', payload['briefs'][0])

    def test_video_type_rules_are_injected_into_brief_and_script_prompts(self):
        context = _briefs_payload(group_id=self.group.id)['metadata']

        for video_type in VIDEO_TYPES:
            briefs_prompt = build_briefs_prompt(video_type, '9:16', context)
            briefs_payload = _briefs_payload(group_id=self.group.id)
            briefs_payload['video_type'] = video_type
            script_prompt = build_script_prompt(briefs_payload, briefs_payload['briefs'][0])

            self.assertIn(VIDEO_TYPE_RULES[video_type], briefs_prompt)
            self.assertIn(VIDEO_TYPE_RULES[video_type], script_prompt)

        social_proof_prompt = build_briefs_prompt('social_proof', '9:16', context)
        self.assertIn('Do not invent reviews', social_proof_prompt)
        self.assertIn('non-fabricated proof proxy', social_proof_prompt)
        self.assertIn('fake reviews', social_proof_prompt)

    def test_generate_script_writes_treatment_and_single_clip_with_beats(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(MEDIA_ROOT=tmpdir):
            run_dir = Path(tmpdir) / 'video_poc' / 'script-run'
            run_dir.mkdir(parents=True)
            briefs_path = run_dir / 'briefs.json'
            briefs_path.write_text(json.dumps(_briefs_payload(group_id=self.group.id)))

            with patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'}):
                with patch('services.video_poc._openai_json', return_value=_script_response()):
                    script_path = generate_script(briefs_path, brief_id=2)

            payload = json.loads(Path(script_path).read_text())
            self.assertIn('creative_treatment', payload)
            self.assertIn('continuity_rules', payload)
            self.assertEqual(payload['clip']['id'], 1)
            self.assertEqual(len(payload['clip']['beats']), 3)
            self.assertEqual(payload['clip']['beats'][0]['start_time'], 0)
            self.assertEqual(payload['clip']['beats'][0]['end_time'], 2)
            self.assertIn('transition_to_next', payload['clip']['beats'][0])
            self.assertIn('product_fidelity_rules', payload['clip'])
            self.assertIn('framing_rules', payload['clip'])
            self.assertIn('motion_rules', payload['clip'])
            self.assertIn('negative_rules', payload['clip'])

    def test_validate_script_rejects_missing_treatment(self):
        payload = _script_response()
        payload.pop('creative_treatment')

        with self.assertRaises(VideoPocError):
            validate_script_payload(payload)

    def test_validate_script_requires_product_framing_motion_and_negative_rules(self):
        for field in (
            'product_fidelity_rules',
            'framing_rules',
            'motion_rules',
            'negative_rules',
        ):
            payload = _script_response()
            payload['clip'].pop(field)

            with self.subTest(field=field):
                with self.assertRaises(VideoPocError):
                    validate_script_payload(payload)

    def test_validate_script_rejects_old_string_beats(self):
        payload = _script_response()
        payload['clip']['beats'] = [
            '0-2s: old string beat.',
            '2-4s: old string beat.',
        ]

        with self.assertRaises(VideoPocError):
            validate_script_payload(payload)

    def test_validate_script_rejects_beats_outside_clip_duration(self):
        payload = _script_response()
        payload['clip']['beats'][-1]['end_time'] = 9
        payload['clip']['beats'][-1]['duration_seconds'] = 3

        with self.assertRaises(VideoPocError):
            validate_script_payload(payload)

    def test_validate_script_rejects_old_multi_clip_schema(self):
        payload = _script_response()
        payload.pop('clip')
        payload['clips'] = [
            {
                'id': 1,
                'duration': 4,
                'narrative_purpose': 'Old schema',
                'scene': 'Scene 1',
                'camera_motion': 'Push in',
                'product_action': 'Use product',
                'keyframe_prompt': 'Frame',
                'seedance_prompt': 'Motion',
            },
            {
                'id': 2,
                'duration': 4,
                'narrative_purpose': 'Old schema',
                'scene': 'Scene 2',
                'camera_motion': 'Push in',
                'product_action': 'Use product',
                'keyframe_prompt': 'Frame',
                'seedance_prompt': 'Motion',
            },
        ]

        with self.assertRaises(VideoPocError):
            validate_script_payload(payload)

    def test_render_dry_run_uses_product_references_without_keyframe_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(MEDIA_ROOT=tmpdir, MEDIA_URL='/media/'):
            run_dir = Path(tmpdir) / 'video_poc' / 'render-run'
            run_dir.mkdir(parents=True)
            script_path = run_dir / 'script.json'
            script_path.write_text(json.dumps(_script_payload(group_id=self.group.id)))

            with patch('services.video_poc._generate_gemini_image') as generate_image:
                manifest_path = render_script(script_path)

            generate_image.assert_not_called()

            payload = json.loads((run_dir / 'muapi_payloads' / 'clip_01.json').read_text())
            self.assertEqual(payload['images_list'], [self.original_product_url])
            self.assertEqual(payload['video_files'], [])
            self.assertEqual(payload['audio_files'], [])
            self.assertIn('@image1', payload['prompt'])
            self.assertNotIn('@image2', payload['prompt'])
            self.assertIn('PRODUCT LOCK:', payload['prompt'])
            self.assertIn('FRAMING LOCK:', payload['prompt'])
            self.assertIn('0s-2s', payload['prompt'])
            self.assertIn('Camera:', payload['prompt'])
            self.assertIn('Product focus:', payload['prompt'])
            self.assertIn('Transition:', payload['prompt'])
            self.assertIn('NEGATIVE CONSTRAINTS:', payload['prompt'])
            self.assertIn('No fake reviews', payload['prompt'])
            self.assertIn('Do not crop or cut off the hero product', payload['prompt'])

            manifest = json.loads(Path(manifest_path).read_text())
            self.assertEqual(manifest['status'], 'dry_run_complete')
            self.assertFalse(manifest['create_keyframe'])
            self.assertEqual(manifest['clips'][0]['keyframe_source'], 'disabled')

    def test_render_dry_run_can_create_keyframe_when_requested(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(MEDIA_ROOT=tmpdir, MEDIA_URL='/media/'):
            run_dir = Path(tmpdir) / 'video_poc' / 'keyframe-run'
            run_dir.mkdir(parents=True)
            script_path = run_dir / 'script.json'
            script_path.write_text(json.dumps(_script_payload(group_id=self.group.id)))

            with patch.dict('os.environ', {'NGROK_URL': 'https://stryng-test.ngrok.app'}):
                with patch(
                    'services.video_poc._generate_gemini_image',
                    return_value=(b'fake-image', 'image/png'),
                ) as generate_image:
                    manifest_path = render_script(script_path, create_keyframe=True)

            self.assertEqual(generate_image.call_count, 1)
            refs = generate_image.call_args_list[0].kwargs['reference_images']
            self.assertEqual(len(refs), 1)
            self.assertIsInstance(refs[0], Media)

            payload = json.loads((run_dir / 'muapi_payloads' / 'clip_01.json').read_text())
            self.assertTrue(payload['images_list'][0].startswith('https://stryng-test.ngrok.app/media/'))
            self.assertEqual(payload['images_list'][1], self.original_product_url)

            manifest = json.loads(Path(manifest_path).read_text())
            self.assertTrue(manifest['create_keyframe'])
            self.assertEqual(manifest['clips'][0]['keyframe_source'], 'generated')

    def test_seedance_prompt_discourages_fake_claims_and_product_drift(self):
        payload = _script_payload(group_id=self.group.id)
        prompt = build_seedance_prompt(payload, payload['clip'], product_reference_count=1)

        self.assertIn('PRODUCT LOCK:', prompt)
        self.assertIn('Keep the exact same product', prompt)
        self.assertIn('Do not morph', prompt)
        self.assertIn('No fake reviews, ratings, percentages, stats, press quotes', prompt)
        self.assertIn('TEXT POLICY:', prompt)

    def test_render_submit_muapi_reuses_existing_reviewed_keyframes_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(MEDIA_ROOT=tmpdir, MEDIA_URL='/media/'):
            run_dir = Path(tmpdir) / 'video_poc' / 'reuse-run'
            keyframe_dir = run_dir / 'keyframes'
            keyframe_dir.mkdir(parents=True)
            (keyframe_dir / 'clip_01.jpg').write_bytes(b'reviewed-1')
            script_path = run_dir / 'script.json'
            script_path.write_text(json.dumps(_script_payload(run_id='reuse-run', group_id=self.group.id)))

            def fake_download(_url, output_path):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b'clip')
                return output_path

            with patch.dict(
                'os.environ',
                {
                    'NGROK_URL': 'https://new-ngrok-url.ngrok.app',
                    'MUAPIAPP_API_KEY': 'muapi-key',
                },
            ):
                with patch('services.video_poc._generate_gemini_image') as generate_image:
                    with patch('services.video_poc.verify_public_url'):
                        with patch('services.video_poc.submit_muapi_clip', return_value=('request-1', {'request_id': 'request-1'})):
                            with patch('services.video_poc.poll_muapi_clip', return_value=('https://cdn.example.com/clip.mp4', {'status': 'completed'})):
                                with patch('services.video_poc.download_file', side_effect=fake_download):
                                    manifest_path = render_script(script_path, submit_muapi=True, create_keyframe=True)

            generate_image.assert_not_called()
            payload = json.loads((run_dir / 'muapi_payloads' / 'clip_01.json').read_text())
            self.assertEqual(
                payload['images_list'],
                [
                    'https://new-ngrok-url.ngrok.app/media/video_poc/reuse-run/keyframes/clip_01.jpg',
                    self.original_product_url,
                ],
            )
            manifest = json.loads(Path(manifest_path).read_text())
            self.assertEqual(manifest['clips'][0]['keyframe_source'], 'reused')
            self.assertEqual((run_dir / 'final.mp4').read_bytes(), b'clip')

    def test_render_submit_muapi_downloads_single_clip_as_final_video(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(MEDIA_ROOT=tmpdir, MEDIA_URL='/media/'):
            run_dir = Path(tmpdir) / 'video_poc' / 'submit-run'
            run_dir.mkdir(parents=True)
            script_path = run_dir / 'script.json'
            script_path.write_text(json.dumps(_script_payload(run_id='submit-run', group_id=self.group.id)))

            def fake_download(_url, output_path):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b'clip')
                return output_path

            with patch.dict(
                'os.environ',
                {
                    'NGROK_URL': 'https://stryng-test.ngrok.app',
                    'MUAPIAPP_API_KEY': 'muapi-key',
                },
            ):
                with patch('services.video_poc._generate_gemini_image') as generate_image:
                    with patch('services.video_poc.verify_public_url') as verify_url:
                        with patch('services.video_poc.submit_muapi_clip', return_value=('request-1', {'request_id': 'request-1'})):
                            with patch('services.video_poc.poll_muapi_clip', return_value=('https://cdn.example.com/clip.mp4', {'status': 'completed'})):
                                with patch('services.video_poc.download_file', side_effect=fake_download):
                                    manifest_path = render_script(script_path, submit_muapi=True)

            generate_image.assert_not_called()
            verify_url.assert_not_called()
            manifest = json.loads(Path(manifest_path).read_text())
            self.assertEqual(manifest['status'], 'completed')
            self.assertTrue(manifest['final_video_url'].endswith('/media/video_poc/submit-run/final.mp4'))
            self.assertEqual((run_dir / 'final.mp4').read_bytes(), b'clip')

    def test_render_can_force_regenerate_existing_keyframes(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(MEDIA_ROOT=tmpdir, MEDIA_URL='/media/'):
            run_dir = Path(tmpdir) / 'video_poc' / 'regen-run'
            keyframe_dir = run_dir / 'keyframes'
            keyframe_dir.mkdir(parents=True)
            old_keyframe = keyframe_dir / 'clip_01.jpg'
            old_keyframe.write_bytes(b'old-reviewed-keyframe')
            script_path = run_dir / 'script.json'
            script_path.write_text(json.dumps(_script_payload(run_id='regen-run', group_id=self.group.id)))

            with patch.dict('os.environ', {'NGROK_URL': 'https://stryng-test.ngrok.app'}):
                with patch(
                    'services.video_poc._generate_gemini_image',
                    return_value=(b'new-keyframe', 'image/png'),
                ) as generate_image:
                    manifest_path = render_script(
                        script_path,
                        regenerate_keyframes=True,
                        create_keyframe=True,
                    )

            generate_image.assert_called_once()
            self.assertFalse(old_keyframe.exists())
            self.assertEqual((keyframe_dir / 'clip_01.png').read_bytes(), b'new-keyframe')
            manifest = json.loads(Path(manifest_path).read_text())
            self.assertTrue(manifest['regenerate_keyframes'])
            self.assertEqual(manifest['clips'][0]['keyframe_source'], 'generated')
