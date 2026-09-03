from __future__ import annotations


PIPELINE_NODES = [
    "CV Uploaded",
    "Document Parsed",
    "Candidate Extracted",
    "Rule Matching",
    "Semantic Matching",
    "Evidence Generated",
    "Interview Kit Generated",
    "Recruiter Review",
]


def screening_pipeline(completed_through: str | None = None, failed_at: str | None = None) -> list[dict]:
    completed_index = PIPELINE_NODES.index(completed_through) if completed_through in PIPELINE_NODES else -1
    result = []
    for index, node in enumerate(PIPELINE_NODES):
        if node == failed_at:
            status = "failed"
        elif index <= completed_index:
            status = "completed"
        elif index == completed_index + 1:
            status = "waiting"
        else:
            status = "pending"
        result.append({"node": node, "status": status})
    return result
