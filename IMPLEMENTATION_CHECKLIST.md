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

- [x] Thiết kế `agent_runs`, `agent_steps`, `agent_tasks`, `batch_items` và idempotency key.
- [x] Bổ sung Redis và RQ worker thật trong Docker Compose.
- [x] Đổi batch upload sang `202 Accepted`, persist task rồi enqueue từng CV.
- [x] Retry/backoff, timeout callback, RQ failed registry và trạng thái failure bền vững.
- [x] Dedupe checksum theo owner/job, dùng application ID xác định để giảm race.
- [x] Progress thật bằng polling; hiển thị batch và retry riêng từng CV trên UI.
- [x] Lưu provider/model/prompt version/fallback reason và execution step.
- [x] Bổ sung migration, tenant-scoped API và tests cho resume/dedupe/retry.

Trạng thái: **Hoàn thành**.

## Mốc 3 — Phase 2: Bounded screening/shortlist agent

- [x] Version hóa criteria và rescreen có lineage.
- [x] Embedding + pgvector và score calibration.
- [x] Tự tạo shortlist proposal theo trigger.
- [x] Approval inbox cho criteria, evidence yếu và shortlist.
- [x] Confidence/anomaly routing sang manual review.
- [x] Trace model, prompt, tool, cost và fallback.

Trạng thái: **Hoàn thành**. Agent chỉ tạo proposal; shortlist vẫn cần recruiter phê duyệt và chưa có side effect outreach.

## Mốc 4 — Phase 3: Calendar và email thật

- [x] OAuth và provider-neutral calendar adapter cho Google Workspace/Microsoft 365.
- [x] Free/busy, create/update/cancel event và webhook sync.
- [x] Email adapter, template version và webhook ingestion.
- [x] Candidate self-scheduling page có IANA timezone, token hash/single-use/expiry.
- [x] Transactional outbox và idempotency cho side effects.

Trạng thái: **Hoàn thành phần code và test offline**. Còn acceptance test bằng tenant OAuth thật trước khi tuyên bố production-ready; profile `local` không tạo side effect bên ngoài.

## Mốc 5 — Phase 4: Follow-up operations

- [x] Reminder candidate/interviewer theo deadline, transactional outbox và RQ scheduler.
- [x] Candidate reschedule bằng capability token riêng, giới hạn số lần theo policy.
- [x] Escalation vào approval inbox cho bounce, no-show, timeout, quá giới hạn và nội dung ngoài scope.
- [x] Structured interview scorecard theo rubric, rating, evidence và recommendation.
- [x] Feedback summary có nguồn, version và phát hiện mâu thuẫn giữa interviewer.

Trạng thái: **Hoàn thành**. Agent tự chạy happy path; các ngoại lệ chỉ tạo yêu cầu human review, không tự kết luận hire/reject.

## Mốc 6 — Phase 5: Tích hợp và tối ưu

- [x] Provider-neutral push connector gateway cho inbox/folder/ATS có secret, scope tối thiểu, consent, provenance và idempotency.
- [x] Shared tenant/RBAC, retention sweep và export/delete candidate data có audit.
- [x] Prompt/model champion-challenger có deterministic cohort và regression gate trước activation.
- [x] Budget/rate limit theo tenant; kill switch riêng cho email/calendar side effect.
- [x] Sourcing agent giữ ở trạng thái disabled; legal gate theo từng nguồn được tài liệu hóa trước mọi triển khai.

Trạng thái: **Hoàn thành phần code và test offline**. Adapter upstream cụ thể (mailbox/folder/ATS), tenant identity thật và challenger dùng provider thật cần acceptance test trong môi trường tích hợp. Legal counsel/data-protection owner vẫn phải ký duyệt từng nguồn; không có chức năng scrape/sourcing trong runtime hiện tại.

## Mốc 7 — Phase 6: Production readiness và observability

- [x] Tách `GET /api/live` khỏi dependency-aware `GET /api/ready`.
- [x] Production readiness gate cho database, Redis queue, auth, seed, HTTPS và integration provider.
- [x] Kiểm tra OAuth credentials, encryption/state/webhook secrets và active connection theo tenant.
- [x] Tenant-scoped operations metrics cho task success, P50/P95 latency, outbox backlog/failure và approval aging.
- [x] Theo dõi usage/limit screening, token và chi phí trong cùng operational view.
- [x] Test local readiness, production blocker và tenant workflow degradation.
- [x] Runbook acceptance/rollback không chứa credential và không dùng dữ liệu ứng viên thật.
- [ ] Chạy acceptance matrix với tenant Google hoặc Microsoft thật và lưu evidence đã phê duyệt.
- [ ] Cấu hình alert/canary trên môi trường deploy thực tế.

Trạng thái: **Hoàn thành implementation và test offline**. Chưa tuyên bố production-ready cho tới khi hai mục acceptance môi trường ở trên hoàn tất.

## Mốc 8 — Phase 7: SLO alerting và canary gate

- [x] SLO policy theo tenant cho success rate, P95 latency, outbox, approval SLA và budget warning.
- [x] Alert evaluator tạo một rolling incident theo signal, chống alert storm bằng dedupe.
- [x] Alert lifecycle `OPEN -> ACKNOWLEDGED -> RESOLVED`, tự resolve/reopen theo telemetry.
- [x] Canary gate kiểm tra sample size, absolute SLO và regression reliability/latency/cost.
- [x] Chặn canary có outbox failure và lưu toàn bộ baseline/candidate/reason theo release version.
- [x] Tenant isolation và audit cho policy, alert action và release evaluation.
- [x] Alembic migration và test alert lifecycle/canary pass-fail/idempotency.
- [x] Có recurring evaluator qua RQ scheduler; cấu hình HA production được theo dõi ở Phase 8.
- [x] Có notification outbox và promotion enforcement/CLI; wiring production được theo dõi ở Phase 8.

Trạng thái: **Hoàn thành implementation và test offline**. Phase 8 đã bổ sung automation layer; cấu hình môi trường vẫn cần acceptance của Phase 6.

## Mốc 9 — Phase 8: Operational automation và enforced promotion

- [x] API startup đăng ký recurring SLO sweep qua RQ scheduler khi dùng Redis.
- [x] Sweep tenant-scoped chỉ chạy trên tenant đã cấu hình SLO policy.
- [x] Alert notification đi qua transactional outbox với retry và idempotency theo episode.
- [x] Notification policy có kill switch và recipient riêng theo tenant.
- [x] Recovery/tái diễn tạo notification episode mới nhưng không tạo alert record trùng.
- [x] Promotion endpoint chặn canary fail, readiness fail và critical alert active.
- [x] Promotion lưu actor/timestamp và idempotent khi replay.
- [x] CLI CI/CD fail-closed gọi promotion endpoint và trả exit code khác 0 khi bị chặn.
- [ ] Cấu hình on-call recipient, RQ scheduler HA và chạy fault-injection trên staging.
- [ ] Chèn CLI promotion gate trước bước chuyển traffic của pipeline production.

Trạng thái: **Hoàn thành implementation và test offline**. Hai mục môi trường vẫn cần evidence trong production acceptance.

## Mốc 10 — Mail Sandbox cho tester

- [x] Whitelist theo tenant cho inbox chính và alias `+number` có giới hạn.
- [x] Giữ nguyên địa chỉ alias trong candidate/outbox/provider payload; không rewrite về inbox chính.
- [x] Chặn `EMAIL_SEND` và `CALENDAR_CREATE` nếu recipient nằm ngoài whitelist.
- [x] API cấu hình, sinh alias mẫu và gửi email test qua transactional outbox.
- [x] Giao diện Mail Sandbox với mặc định `vinhvp.khmtk36@gmail.com`.
- [x] Migration và test allowed/blocked/boundary/preserved recipient.

Trạng thái: **Hoàn thành code và test offline**. Profile local mô phỏng gửi mail; provider thật cần OAuth acceptance.
