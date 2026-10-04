# Tests

Run from repo root with the project venv:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

## If you change…

| Area | Tests |
|------|--------|
| `visual_mode` / talking-head limits | `test_narration_visual_mode.py`, `test_phase1_long_conversation_requirements.py`, `test_long_conversation_merge.py` |
| phase1-dialogue merge | `test_phase1_dialogue_merge.py` |
| TTS stage directions / pauses | `test_tts_stage_directions.py` |
| Multi-voice TTS routing | `test_tts_speaker_voice.py` |
| Prompt pack load / fallback | `test_prompt_packs.py` |
| Automation gates / run-daily inference | `test_automation_gates.py`, `test_run_daily_long_conversation_infer.py` |
| Talking-head merge / silence in `video_prompt` | `test_merge_talking_head_silence.py` |
| FAL retries / SadTalker / scene anchor | `test_fal_retry.py`, `test_fal_avatar_preprocess.py`, `test_fal_scene_anchor_i2i_composite.py`, `test_fal_content_policy_rewrite.py` |
| YouTube upload metadata / SRT captions | `test_youtube_upload_metadata.py`, `test_youtube_upload_status_api.py` |
| Phase 1 title uniqueness rewrite | `test_phase1_title_hook.py` |

Narration and video integration is mostly covered by unit tests on `pipeline/`; use `--dry-run` on `narration-to-video.py` before paid vendor runs (see `docs/AGENTS-pipeline.md`).
