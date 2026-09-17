# TalentFlow Model Handoff via Hugging Face

Use Hugging Face Hub for the trained model weights and GitHub for code, config, and download scripts.

## Why

- The exported Phase03 model is about 5.8GB.
- GitHub repositories should not store these weights directly.
- Hugging Face model repos are designed for `safetensors`, tokenizer files, model cards, and later deployment.

## Local Export Found

```text
/Users/Vinh/Downloads/output_Phase3_export_model/talentflow_export/v1/merged_model
```

This directory contains the required Hugging Face files:

```text
config.json
generation_config.json
tokenizer.json
tokenizer_config.json
chat_template.jinja
model.safetensors.index.json
model-00001-of-00003.safetensors
model-00002-of-00003.safetensors
model-00003-of-00003.safetensors
```

## Upload

Install the Hugging Face CLI:

```bash
python -m pip install -U "huggingface_hub[cli]"
```

Set a token without writing it into the repo:

```bash
export HF_TOKEN="hf_xxx"
```

Upload the model:

```bash
bash scripts/upload_talentflow_model_to_hf.sh <hf_user_or_org/talentflow-resume-qwen25-3b>
```

The script uploads only the `merged_model` directory as a private Hugging Face model repo.

## Download For Backend Or Deploy

```bash
bash scripts/download_talentflow_model_from_hf.sh <hf_user_or_org/talentflow-resume-qwen25-3b>
```

By default, this downloads into:

```text
artifacts/phase3-export/merged_model
```

## Backend Flow

The backend now tries the trained TalentFlow Hugging Face model first for CV/resume schema extraction.

```text
CV text
  -> TalentFlow HF model extracts talentflow.resume.v1 schema
  -> backend maps schema into Application.screening.candidate_profile
  -> backend keeps existing deterministic evidence, score calibration, and interview kit flow
  -> fallback to existing OpenRouter/rules flow if the TalentFlow model is unavailable
```

This means the trained model replaces candidate profile extraction, not JD extraction or interview-kit generation.

## Render Environment Variables

Set these in Render Environment Variables, not in GitHub:

```env
HF_TOKEN=hf_xxx
TALENTFLOW_MODEL_REPO_ID=vancevo/talentflow-resume-qwen25-3b
TALENTFLOW_MODEL_DIR=artifacts/phase3-export/merged_model
TALENTFLOW_MODEL_ENABLED=true
TALENTFLOW_MAX_INPUT_TOKENS=8192
TALENTFLOW_MAX_NEW_TOKENS=2048
```

Render must have enough disk and memory to download and run the 5.8GB model. If the runtime cannot load the model,
the backend falls back to the previous rules/OpenRouter path instead of failing the screening job.

## GitHub Rule

Commit these files to GitHub:

- backend code
- config
- deployment docs
- `scripts/upload_talentflow_model_to_hf.sh`
- `scripts/download_talentflow_model_from_hf.sh`

Do not commit:

- `*.safetensors`
- `artifacts/`
- copied model directories
