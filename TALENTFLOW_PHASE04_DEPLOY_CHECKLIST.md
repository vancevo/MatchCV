# TalentFlow Phase04 Deploy Checklist

## What Changed

The backend now tries the trained TalentFlow Hugging Face model first for CV/resume schema extraction.
If that model is unavailable, the existing OpenRouter/rules flow remains as fallback.

The new flow is:

```text
CV text
-> TalentFlow HF model extracts talentflow.resume.v1 schema
-> backend maps schema into screening.candidate_profile
-> backend keeps existing evidence, scoring, calibration, and interview kit generation
```

## Files Changed

- `backend/app/talentflow_model.py`
- `backend/app/llm.py`
- `backend/app/config.py`
- `backend/app/worker.py`
- `backend/requirements.txt`
- `.env.example`
- `backend/.env.example`
- `backend/tests/test_talentflow_model.py`

## Render Environment Variables

Set these in Render:

```env
HF_TOKEN=hf_xxx
TALENTFLOW_MODEL_REPO_ID=vancevo/talentflow-resume-qwen25-3b
TALENTFLOW_MODEL_DIR=artifacts/phase3-export/merged_model
TALENTFLOW_MODEL_ENABLED=true
TALENTFLOW_MAX_INPUT_TOKENS=8192
TALENTFLOW_MAX_NEW_TOKENS=2048
TASK_TIMEOUT_SECONDS=600
```

Keep the existing `OPENROUTER_API_KEY` if you still want AI JD extraction and interview kit generation.

## Deployment Steps

1. Commit backend code changes to GitHub.
2. Add the Render environment variables above.
3. Make sure Render has enough disk and RAM for a 5.8GB model.
4. Deploy backend.
5. Upload one CV and check the application screening payload.
6. Confirm `screening.candidate_profile.extraction_source` is `talentflow_hf`.
7. Confirm `AgentRun.provider` is `talentflow_hf`.
8. If Render cannot load the model, confirm the flow falls back to `rules` or `openrouter`.

## Verification Commands

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_talentflow_model.py \
  backend/tests/test_foundation.py::test_openrouter_failure_uses_rules \
  backend/tests/test_async_screening.py::test_batch_persists_task_before_worker_and_can_resume
```

Expected result:

```text
5 passed
```

## Notes

- The Hugging Face repo is private: `vancevo/talentflow-resume-qwen25-3b`.
- Do not commit `HF_TOKEN`, `artifacts/`, or `*.safetensors`.
- The token that was pasted into chat should be revoked and replaced before production deploy.
