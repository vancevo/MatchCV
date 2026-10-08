"""Read-only report of CV versions that repeat the same file for the same candidate.

    cd backend && .venv/bin/python -m scripts.report_duplicate_versions [--ids]

`create_resume_version` no longer creates these; rows written before that are listed here so a person can decide
what to do with them. Nothing is changed or deleted.
"""
from __future__ import annotations

import sys

from sqlalchemy import func, select

from app.database import session_scope
from app.models import CandidateProfile, CandidateResumeVersion


def main() -> int:
    with session_scope() as db:
        groups = db.execute(
            select(CandidateResumeVersion.owner_id, CandidateResumeVersion.candidate_profile_id,
                   CandidateResumeVersion.checksum, func.count())
            .group_by(CandidateResumeVersion.owner_id, CandidateResumeVersion.candidate_profile_id,
                      CandidateResumeVersion.checksum)
            .having(func.count() > 1)
        ).all()
        extra = 0
        for owner_id, profile_id, checksum, count in groups:
            extra += count - 1
            if "--ids" not in sys.argv:
                continue
            profile = db.get(CandidateProfile, profile_id)
            versions = db.scalars(select(CandidateResumeVersion).where(
                CandidateResumeVersion.candidate_profile_id == profile_id,
                CandidateResumeVersion.checksum == checksum,
            ).order_by(CandidateResumeVersion.version_number)).all()
            listing = ", ".join(f"v{item.version_number}={item.id}" for item in versions)
            print(f"{owner_id} {profile.full_name if profile else profile_id} {checksum[:12]}: {listing}")
        print(f"{len(groups)} profiles repeat a file; {extra} surplus versions (read-only, nothing deleted)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
