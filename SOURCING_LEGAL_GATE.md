# Sourcing agent legal gate

TalentFlow hiện **không có runtime sourcing/scraping/outreach agent**. Tài liệu này là product-release gate, không thay thế tư vấn pháp lý. Mỗi nguồn và mỗi quốc gia triển khai phải có một hồ sơ phê duyệt riêng trước khi code sourcing được bật.

## Điều kiện bắt buộc

- Product owner ghi rõ nguồn, use case, quốc gia, loại dữ liệu và thời gian lưu.
- Legal counsel xác nhận Terms of Service/API policy cho phép discovery, ingest và contact theo cách dự kiến; không dùng scraping để né API, robots hoặc access control.
- Data-protection owner ghi lawful basis/consent, privacy notice, quyền phản đối, export/delete, retention và cross-border transfer.
- Security review xác nhận least-privilege scope, secret rotation, tenant isolation, provenance, rate limit, kill switch và incident response.
- Employment/fairness review cấm thuộc tính nhạy cảm, proxy discrimination và quyết định hire/reject tự động; evidence và human approval vẫn bắt buộc.
- Outreach review xác nhận template, sender identity, quiet hours, unsubscribe/suppression, recipient/frequency cap và luật anti-spam áp dụng.
- DPA/subprocessor/data residency và quy trình data-subject request được kiểm thử end-to-end.
- Acceptance test lưu bằng chứng permission, audit, deletion propagation và source-specific revocation.

## Quy tắc phát hành

Chỉ được chuyển một source từ `DISABLED` sang pilot khi tất cả owner ở trên ký duyệt, approval có ngày hết hạn và rollback owner. Bất kỳ thay đổi Terms/API scope, quốc gia, model hoặc mục đích xử lý nào cũng làm approval cũ mất hiệu lực. Không có approval nghĩa là không ingest, không scrape và không outreach.
