# TalentFlow Production Acceptance Runbook

Runbook này là cổng nghiệm thu Phase 6. Không đánh dấu production-ready chỉ dựa trên test offline.

## 1. Điều kiện trước khi chạy

- `APP_ENV=staging|production`, PostgreSQL và Redis riêng cho môi trường.
- `AUTH_REQUIRED=true`, `AUTO_SEED=false`, `QUEUE_EAGER=false`.
- `PUBLIC_APP_URL` dùng HTTPS; CORS chỉ chứa origin đã phê duyệt.
- Chọn `INTEGRATION_PROVIDER=google|microsoft` và cấp OAuth/webhook secrets thật qua secret manager.
- Dùng tenant thử nghiệm và hộp thư/lịch thử nghiệm, không dùng dữ liệu ứng viên thật.

`GET /api/ready` phải trả `200` trước khi nhận traffic. `GET /api/live` chỉ xác nhận process còn sống và không được dùng làm readiness probe.

## 2. Acceptance matrix bắt buộc

| Kịch bản | Kết quả bắt buộc |
|---|---|
| OAuth connect/revoke | Kết nối active sau callback; revoke làm mọi thao tác mới bị chặn |
| Free/busy | Slot bận không được đề xuất; timezone và DST được giữ chính xác |
| Double booking | Hai request đồng thời chỉ tạo một lịch hợp lệ |
| Email idempotency | Replay cùng idempotency key chỉ gửi đúng một email |
| Provider retry | 429/5xx tạo retry có backoff; hết số lần thử chuyển `FAILED` và xuất hiện trong metrics |
| Webhook replay | Cùng external event chỉ được xử lý một lần; chữ ký/secret sai bị từ chối |
| Reschedule/cancel | Event cũ và reminder cũ được hủy; event mới không trùng lịch |
| Queue restart | Restart worker không làm mất durable task hoặc tạo side effect trùng |
| Tenant isolation | User tenant A không đọc/ghi readiness detail hay metrics của tenant B |
| Kill switch | Tắt email/calendar chặn dispatch ngay trước side effect |

Lưu evidence cho mỗi dòng: timestamp UTC, tenant thử nghiệm, request/correlation ID, provider event ID đã che bớt và kết quả quan sát. Không lưu access/refresh token vào evidence.

## 3. Quan sát sau triển khai

- Gọi `GET /api/operations/readiness` trong đúng tenant: không có check `fail`.
- Gọi `GET /api/operations/metrics?hours=24`: kiểm tra task failure, outbox `FAILED/BLOCKED`, due backlog, approval quá 24 giờ và mức sử dụng quota.
- Cấu hình cảnh báo khi có outbox `FAILED`, due backlog tăng liên tục, screening success rate giảm hoặc chi phí gần giới hạn tenant.
- Cấu hình scheduler gọi `POST /api/operations/evaluate`; xác minh alert lặp tăng `occurrences`, recovery chuyển `RESOLVED` và tái diễn mở lại cùng signal.
- Giữ canary tenant trước, sau đó mới tăng rollout. Challenger chỉ được activate sau regression gate hiện có.
- Gửi baseline/canary telemetry vào `POST /api/operations/release-gates`; deployment chỉ được promote khi trạng thái là `PROMOTION_ALLOWED`.
- Bật notification recipient trên SLO policy, gây một lỗi sandbox và xác minh chỉ một email được gửi cho episode; recovery rồi tái diễn phải tạo đúng một email mới.
- Chạy `python -m scripts.promote_release` trong pipeline: canary fail hoặc critical alert phải trả exit code `1`; gate sạch phải ghi `PROMOTED` trước bước chuyển traffic.

## 4. Rollback

1. Bật kill switch email/calendar của tenant.
2. Dừng nhận ingestion mới và scale worker về 0 nếu lỗi tiếp tục tạo side effect.
3. Giữ nguyên database/outbox để điều tra; không xóa event lỗi.
4. Rollback application image về bản đã nghiệm thu gần nhất.
5. Chỉ replay task/outbox sau khi xác minh idempotency key và nguyên nhân gốc.

Phase 6 chỉ được ghi **hoàn thành production acceptance** khi toàn bộ matrix đã chạy với tenant provider thật và evidence được người vận hành phê duyệt.
