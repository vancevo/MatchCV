import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

import app.llm as llm
import app.main as main
from app.config import get_settings
from app.interview_ops import rule_based_assessment
from app.main import app


client = TestClient(app)

SAMPLE_TRANSCRIPT = (
    "Leader: Chào em, em giới thiệu sơ qua về kinh nghiệm làm việc của mình nhé.\n"
    "Candidate: Dạ em có 3 năm kinh nghiệm làm React, đã từng xây dựng hệ thống thương mại điện tử.\n"
    "HR: Em thấy môi trường làm việc nhóm quan trọng như thế nào?\n"
    "Candidate: Dạ em nghĩ làm việc nhóm rất quan trọng, em hay chủ động trao đổi với đồng nghiệp."
)


def booked_interview() -> tuple[dict, dict]:
    suffix = uuid4().hex[:8]
    application = client.post("/api/applications", json={
        "job_id": "job-backend-01",
        "candidate_name": f"CrossAnalysis {suffix}",
        "candidate_email": f"cross-{suffix}@example.com",
        "resume_text": "3 năm kinh nghiệm React, xây dựng hệ thống thương mại điện tử. Làm việc nhóm tốt.",
    }).json()
    slots = client.get("/api/interviewers/recruiter-1/available-slots").json()
    earliest = datetime.now(timezone.utc) + timedelta(minutes=1440 + 30)
    slot = next((item["start_at"] for item in slots
                 if datetime.fromisoformat(item["start_at"].replace("Z", "+00:00")) > earliest), None)
    assert slot, "expected a free slot more than a day ahead"
    response = client.post(f"/api/applications/{application['id']}/interview", json={
        "slot": slot, "timezone_name": "Asia/Ho_Chi_Minh", "idempotency_key": f"cross-{suffix}",
    })
    assert response.status_code == 201
    return application, response.json()


def test_without_an_api_key_every_model_is_marked_unavailable_but_the_transcript_still_saves():
    # Test env always runs with OPENROUTER_API_KEY="" (see conftest.py) - this is the common case
    # for this free/demo setup, and it must degrade gracefully rather than error out.
    _, interview = booked_interview()
    response = client.post(f"/api/interviews/{interview['id']}/transcript-sessions",
                           json={"transcript_text": SAMPLE_TRANSCRIPT})
    assert response.status_code == 201
    body = response.json()
    assert body["transcript_text"] == SAMPLE_TRANSCRIPT
    # Segmentation also has no API key, so it falls back to one untagged pair - not an error.
    assert len(body["qa_pairs"]) == 1
    configured_models = get_settings().interview_analysis_models
    assert len(body["models"]) == len(configured_models)
    assert {m["status"] for m in body["models"]} == {"UNAVAILABLE"}
    assert {m["model_name"] for m in body["models"]} == set(configured_models)

    operations = client.get(f"/api/interviews/{interview['id']}/operations")
    assert operations.status_code == 200
    sessions = operations.json()["transcript_sessions"]
    assert len(sessions) == 1
    assert sessions[0]["id"] == body["id"]


def test_complete_degrades_gracefully_when_provider_returns_null_content(monkeypatch):
    # Reproduces a real crash: a provider can answer 200 with choices[0].message.content: null
    # (a refusal, a tool-call-only reply, an internal hiccup still wrapped as success) - this used
    # to raise an unhandled AttributeError ('NoneType' has no attribute 'strip') all the way up to
    # a 500, instead of degrading to "unavailable" like every other malformed-response case.
    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return httpx.Response(200, json={"choices": [{"message": {"content": None}}]},
                                  request=httpx.Request("POST", "https://example.test/"))

    monkeypatch.setattr(llm.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    get_settings.cache_clear()
    try:
        result = asyncio.run(llm._complete("system", "user"))
    finally:
        get_settings.cache_clear()
    assert result is None


def test_cross_analysis_drops_hallucinated_quotes_and_unknown_ids(monkeypatch):
    async def fake_complete(system, user, model_override=None, endpoint_url=None, endpoint_api_key=None):
        return {
            "overall_assessment": {"score": 82, "recommendation": "YES", "reasoning": "Trả lời cụ thể, có bằng chứng."},
            "qa_analyses": [{
            "qa_index": 0,
            "answer_quality": {"relevance": "high", "completeness": "high", "specificity": "high", "has_example": True},
            "linked_claims": [
                {"claim_id": "claim-1", "relationship": "VERIFIED", "evidence_quote": "3 năm kinh nghiệm React", "confidence": 0.9},
                {"claim_id": "claim-1", "relationship": "VERIFIED", "evidence_quote": "câu bịa không có trong answer", "confidence": 0.9},
                {"claim_id": "unknown-claim-id", "relationship": "VERIFIED", "evidence_quote": "3 năm kinh nghiệm React", "confidence": 0.9},
            ],
            "linked_requirements": [
                {"requirement": "React", "evidence_strength": "STRONG", "evidence_quote": "3 năm kinh nghiệm React"},
                {"requirement": "React", "evidence_strength": "STRONG", "evidence_quote": "câu bịa khác"},
                {"requirement": "Không tồn tại", "evidence_strength": "STRONG", "evidence_quote": "3 năm kinh nghiệm React"},
            ],
        }]}

    monkeypatch.setattr(llm, "_complete", fake_complete)
    qa_pairs = [{"speaker_role": "Candidate", "question": "Em có kinh nghiệm gì?",
                "answer": "Dạ em có 3 năm kinh nghiệm React."}]
    claims = [{"id": "claim-1", "text": "3 năm kinh nghiệm React"}]
    requirements = {"required_skills": ["React"], "preferred_skills": []}
    result = asyncio.run(llm.cross_analyze_interview_ai("fake-model", "Backend Engineer", requirements, claims, qa_pairs))

    assert result is not None
    assert result["overall"] == {"score": 82, "recommendation": "YES", "reasoning": "Trả lời cụ thể, có bằng chứng."}

    linked_claims = result["qa_analyses"][0]["linked_claims"]
    # Only the real, exactly-quoted claim survives - the duplicate hallucinated quote and the
    # unknown claim id are both dropped.
    assert len(linked_claims) == 1
    assert linked_claims[0]["claim_id"] == "claim-1"
    assert linked_claims[0]["evidence_quote"] == "3 năm kinh nghiệm React"

    linked_requirements = result["qa_analyses"][0]["linked_requirements"]
    assert len(linked_requirements) == 1
    assert linked_requirements[0]["requirement"] == "React"
    assert linked_requirements[0]["evidence_quote"] == "3 năm kinh nghiệm React"


def test_colab_prefixed_model_routes_to_the_configured_endpoint_not_openrouter(monkeypatch):
    monkeypatch.setenv("COLAB_LLM_ENDPOINT_URL", "https://example.loca.lt/v1/chat/completions")
    get_settings.cache_clear()
    captured = {}

    async def fake_complete(system, user, model_override=None, endpoint_url=None, endpoint_api_key=None):
        captured["model_override"] = model_override
        captured["endpoint_url"] = endpoint_url
        return {"qa_analyses": []}

    try:
        monkeypatch.setattr(llm, "_complete", fake_complete)
        result = asyncio.run(llm.cross_analyze_interview_ai(
            "colab:llama3.1", "Backend Engineer", {"required_skills": [], "preferred_skills": []}, [], [],
        ))
    finally:
        get_settings.cache_clear()

    assert result == {"overall": {"score": 0, "recommendation": "MIXED", "reasoning": ""}, "qa_analyses": []}
    assert captured["endpoint_url"] == "https://example.loca.lt/v1/chat/completions"
    assert captured["model_override"] == "llama3.1"


def test_colab_prefixed_model_is_unavailable_when_no_endpoint_is_configured():
    result = asyncio.run(llm.cross_analyze_interview_ai(
        "colab:llama3.1", "Backend Engineer", {"required_skills": [], "preferred_skills": []}, [], [],
    ))
    assert result is None


def test_one_model_failing_does_not_block_the_other_three(monkeypatch):
    monkeypatch.setenv("COLAB_LLM_ENDPOINT_URL", "https://example.loca.lt/v1/chat/completions")
    monkeypatch.setenv("INTERVIEW_ANALYSIS_MODELS", "colab:model-a,colab:model-b,colab:model-c")
    get_settings.cache_clear()
    configured_models = get_settings().interview_analysis_models
    failing_model = configured_models[0]

    async def fake_complete(system, user, model_override=None, endpoint_url=None, endpoint_api_key=None):
        if model_override is None:  # the single-model segmentation call (always goes to OpenRouter)
            return {"qa_pairs": [{"speaker_role": "Candidate", "question": "Q", "answer": "A"}]}
        if model_override == failing_model.split(":", 1)[1]:
            raise RuntimeError("simulated provider outage")
        return {"qa_analyses": []}

    try:
        monkeypatch.setattr(llm, "_complete", fake_complete)
        _, interview = booked_interview()
        response = client.post(f"/api/interviews/{interview['id']}/transcript-sessions",
                               json={"transcript_text": SAMPLE_TRANSCRIPT})
    finally:
        get_settings.cache_clear()
    assert response.status_code == 201
    statuses = {m["model_name"]: m["status"] for m in response.json()["models"]}
    assert statuses[failing_model] == "ERROR"
    assert all(status == "OK" for name, status in statuses.items() if name != failing_model)


def test_transcribe_recording_only_transcribes_no_analysis_session_created(monkeypatch):
    # Step 1 of the ghi-âm flow is transcription only - the frontend shows this text immediately,
    # then separately calls POST .../transcript-sessions (tested elsewhere) to run the real
    # analysis. This endpoint must never create a session/run rows by itself.
    captured_path = {}

    async def fake_transcribe(file_path):
        captured_path["path"] = file_path
        assert os.path.exists(file_path)  # the temp file must still exist while transcribing
        return SAMPLE_TRANSCRIPT

    monkeypatch.setattr(main, "transcribe_audio_ai", fake_transcribe)
    _, interview = booked_interview()
    response = client.post(
        f"/api/interviews/{interview['id']}/transcribe-recording",
        files={"audio": ("recording.webm", b"fake-audio-bytes", "audio/webm")},
    )
    assert response.status_code == 200
    assert response.json() == {"transcript_text": SAMPLE_TRANSCRIPT}
    # The temp audio file is deleted right after transcription - never kept around.
    assert not os.path.exists(captured_path["path"])
    operations = client.get(f"/api/interviews/{interview['id']}/operations")
    assert operations.json()["transcript_sessions"] == []


def test_transcribe_recording_returns_503_when_transcription_is_unavailable(monkeypatch):
    async def fake_transcribe(file_path):
        return None

    monkeypatch.setattr(main, "transcribe_audio_ai", fake_transcribe)
    _, interview = booked_interview()
    response = client.post(
        f"/api/interviews/{interview['id']}/transcribe-recording",
        files={"audio": ("recording.webm", b"fake-audio-bytes", "audio/webm")},
    )
    assert response.status_code == 503
    operations = client.get(f"/api/interviews/{interview['id']}/operations")
    assert operations.json()["transcript_sessions"] == []


def _aggregation(verified=0, unverified=0, covered=0, not_covered=0, inconsistent=0) -> dict:
    return {
        "claims_verified": [f"claim-{i}" for i in range(verified)],
        "claims_unverified": [f"unverified-{i}" for i in range(unverified)],
        "potential_inconsistencies": [f"inconsistency-{i}" for i in range(inconsistent)],
        "new_info_beyond_cv": [],
        "requirements_covered": [f"req-{i}" for i in range(covered)],
        "requirements_not_covered": [f"req-missing-{i}" for i in range(not_covered)],
    }


def test_rule_based_assessment_perfect_evidence_scores_100_strong_yes():
    result = rule_based_assessment(_aggregation(verified=3, covered=2))
    assert result["score"] == 100
    assert result["recommendation"] == "STRONG_YES"
    assert result["requirements_ratio"] == 1.0
    assert result["claims_ratio"] == 1.0
    assert result["inconsistency_penalty"] == 0


def test_rule_based_assessment_zero_evidence_scores_0_strong_no():
    result = rule_based_assessment(_aggregation(unverified=3, not_covered=2))
    assert result["score"] == 0
    assert result["recommendation"] == "STRONG_NO"
    assert result["requirements_ratio"] == 0.0
    assert result["claims_ratio"] == 0.0


def test_rule_based_assessment_no_claims_or_requirements_at_all_scores_0():
    # Nothing to verify (e.g. the JD had no parsed requirements and the CV had no claims) - base
    # score has nothing to compute from, must not divide by zero.
    result = rule_based_assessment(_aggregation())
    assert result["score"] == 0
    assert result["requirements_ratio"] is None
    assert result["claims_ratio"] is None


def test_rule_based_assessment_uses_only_the_ratio_that_exists():
    # Only claims present (no JD requirements parsed) - base score comes entirely from the claims
    # ratio, not diluted by a missing requirements term.
    claims_only = rule_based_assessment(_aggregation(verified=1, unverified=1))
    assert claims_only["requirements_ratio"] is None
    assert claims_only["claims_ratio"] == 0.5
    assert claims_only["score"] == 50

    requirements_only = rule_based_assessment(_aggregation(covered=1, not_covered=1))
    assert requirements_only["claims_ratio"] is None
    assert requirements_only["requirements_ratio"] == 0.5
    assert requirements_only["score"] == 50


def test_rule_based_assessment_penalizes_inconsistencies_flat_per_count():
    perfect = rule_based_assessment(_aggregation(verified=4, covered=4))
    one_inconsistency = rule_based_assessment(_aggregation(verified=4, covered=4, inconsistent=1))
    two_inconsistencies = rule_based_assessment(_aggregation(verified=4, covered=4, inconsistent=2))
    assert perfect["score"] == 100
    assert one_inconsistency["score"] == 85
    assert two_inconsistencies["score"] == 70


def test_rule_based_assessment_inconsistency_penalty_caps_at_40():
    result = rule_based_assessment(_aggregation(verified=4, covered=4, inconsistent=10))
    assert result["inconsistency_penalty"] == 40
    assert result["score"] == 60  # 100 - 40, never goes negative or below the cap


def test_transcribe_recording_returns_503_when_transcript_too_short(monkeypatch):
    async def fake_transcribe(file_path):
        return "quá ngắn"

    monkeypatch.setattr(main, "transcribe_audio_ai", fake_transcribe)
    _, interview = booked_interview()
    response = client.post(
        f"/api/interviews/{interview['id']}/transcribe-recording",
        files={"audio": ("recording.webm", b"fake-audio-bytes", "audio/webm")},
    )
    assert response.status_code == 503
    operations = client.get(f"/api/interviews/{interview['id']}/operations")
    assert operations.json()["transcript_sessions"] == []
