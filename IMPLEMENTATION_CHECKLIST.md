# TalentFlow Implementation Checklist

Checklist này theo dõi việc triển khai [kế hoạch mở rộng Agentic AI](./AGENTIC_AI_EXPANSION_PLAN.md). Chỉ đánh dấu hoàn thành khi code, test và tài liệu đã cùng phản ánh đúng trạng thái.

## Mốc 1 — Phase 0: Nền tảng tin cậy

- [x] Thêm Alembic và baseline migration tương thích database hiện có.
- [x] Chạy migration trước backend trong Docker và Render.
- [x] Gom biến môi trường vào typed settings, kiểm tra boolean/profile/upload limit.
- [x] Chuẩn hóa status bằng enum, error envelope và timestamp booking theo UTC.
- [x] Thêm database constraint chống hai lịch trùng slot của cùng recruiter.
- [x] Thêm filter/pagination cho jobs, applications và audit logs.
- [x] Bỏ Redis, pgvector image và worker placeholder khỏi execution stack hiện tại.
- [x] Thêm eval dataset cùng baseline extraction/evidence/ranking.
- [x] Bổ sung test auth, tenant isolation, upload limit, fallback và booking race.
- [x] CI chạy migration, tests, eval, frontend build và Docker builds.
- [x] Cập nhật README và roadmap theo implementation thực tế.

Trạng thái: **Hoàn thành**.

## Mốc 2 — Phase 1: Async screening

- [ ] Thiết kế `agent_runs`, `agent_steps`, `tasks` và idempotency key.
- [ ] Bổ sung Redis và worker thật.
- [ ] Đổi batch upload sang enqueue + trạng thái bất đồng bộ.
- [ ] Retry/backoff, timeout và dead-letter queue.
- [ ] Dedupe CV theo policy owner/job.
- [ ] Progress thật bằng polling hoặc SSE; retry riêng từng CV.

## Mốc 3 — Phase 2: Bounded screening/shortlist agent

- [ ] Version hóa criteria và rescreen có lineage.
- [ ] Embedding + pgvector và score calibration.
- [ ] Tự tạo shortlist proposal theo trigger.
- [ ] Approval inbox cho criteria, evidence yếu và shortlist.
- [ ] Confidence/anomaly routing sang manual review.
- [ ] Trace model, prompt, tool, cost và fallback.

## Mốc 4 — Phase 3: Calendar và email thật

- [ ] OAuth và provider-neutral calendar adapter.
- [ ] Free/busy, create/update/cancel event và webhook sync.
- [ ] Email adapter, template version và delivery webhook.
- [ ] Candidate self-scheduling page có timezone/token expiry.
- [ ] Transactional outbox và idempotency cho side effects.

## Mốc 5 — Phase 4: Follow-up operations

- [ ] Reminder và reschedule trong policy.
- [ ] Escalation cho bounce, no-show, timeout và nội dung ngoài scope.
- [ ] Structured interview scorecard.
- [ ] Feedback summary có nguồn và phát hiện mâu thuẫn.

## Mốc 6 — Phase 5: Tích hợp và tối ưu

- [ ] Connector inbox/folder/ATS có consent và provenance.
- [ ] Tenant/RBAC, retention, export/delete candidate data.
- [ ] Prompt/model champion-challenger và regression gates.
- [ ] Budget/rate limit theo tenant.
- [ ] Đánh giá pháp lý trước khi triển khai sourcing agent.
