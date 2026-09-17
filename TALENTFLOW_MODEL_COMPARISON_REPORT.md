# TalentFlow model comparison report

Ngày chạy: 2026-09-17
Phạm vi: so sánh `google/gemini-2.5-flash-lite` qua OpenRouter với TalentFlow HF local trên 2 role IT.

Raw benchmark JSON được lưu local tại `artifacts/model_comparison_it_cases.json` và không commit vì thư mục `artifacts/` dùng cho dữ liệu sinh ra khi chạy benchmark.

## Tóm tắt kết quả

| Role | Model | JD extract | CV extract | Coverage | Score | Nhận xét |
|---|---:|---:|---:|---:|---:|---|
| Backend Golang/Web3 | Gemini | 3.63s | 2.92s | 71.4% | 77.5 | Extract JD tốt, nhanh; wording hơi rộng như `SQL` thay vì `SQL optimization`. |
| Backend Golang/Web3 | TalentFlow HF | 12.27s | 116.04s | 100.0% | 100.0 | CV schema tốt hơn; JD raw yếu nên phải dùng rules guardrails. |
| Frontend React/Next.js | Gemini | 3.34s | 2.79s | 68.8% | 82.4 | Extract JD rất đầy đủ; CV summary tốt; evidence coverage thấp hơn vì requirement granular. |
| Frontend React/Next.js | TalentFlow HF | 7.15s | 70.42s | 100.0% | 100.0 | CV schema ổn nhưng thiếu React/Next.js/TypeScript trong skills; score cao do JD guardrails ít granular hơn. |

## Đánh giá

### Gemini / OpenRouter

Ưu điểm:

- Rất nhanh so với HF local: khoảng 3s cho JD và 3s cho CV.
- Hiểu JD tiếng Việt tốt, đặc biệt với role Frontend: tách được React, Next.js, TypeScript, accessibility, testing, Figma, Git, Agile.
- Tạo `summary` CV tốt, dễ đọc, phù hợp UI/recruiter.

Điểm cần lưu ý:

- Có thể normalize wording chưa đúng chuẩn nội bộ, ví dụ `SQL` thay vì `SQL optimization`, `monitoring tools` thay vì `Monitoring`.
- Coverage thấp hơn TalentFlow HF trong benchmark vì Gemini extract requirement chi tiết hơn, dẫn đến rules evidence checker cần alias tốt hơn.

### TalentFlow HF local

Ưu điểm:

- CV schema đúng format nội bộ `talentflow.resume.v1`.
- Extract được các field sâu hơn như `desired_position`, `projects`, `languages`, `years_experience`.
- Phù hợp nếu mục tiêu là chuẩn hóa CV vào schema TalentFlow.

Điểm yếu:

- Rất chậm trên CPU local: 70-116s cho một CV ngắn.
- JD raw chưa tốt: thường đưa nhiều skill vào `preferred_skills`, thiếu `minimum_experience`, thiếu nhiều requirement.
- Với Frontend CV, HF bỏ sót skill quan trọng trong list skills như React, Next.js, TypeScript dù CV có ghi rõ.
- Có dấu hiệu cần validate project/experience vì model có thể suy diễn một số cấu trúc từ CV ngắn.

## Kết luận đề xuất

Flow nên dùng hiện tại:

1. JD extraction: dùng Gemini/OpenRouter làm chính.
2. CV profile nhanh + interview kit: dùng Gemini/OpenRouter.
3. CV schema nội bộ: dùng TalentFlow HF khi cần schema sâu hoặc khi có GPU/server đủ mạnh.
4. Luôn giữ rules guardrails để:
   - chuẩn hóa skill naming,
   - không mất số năm kinh nghiệm,
   - kiểm chứng evidence bằng quote/rules,
   - fallback khi OpenRouter/HF lỗi.

Đề xuất tiếp theo:

- Fine-tune thêm TalentFlow HF với dataset JD extraction riêng nếu muốn thay Gemini ở bước JD.
- Bổ sung alias/evidence matcher cho Frontend/Cloud/Web3 để score phản ánh đúng hơn khi requirement granular.
- Nếu deploy Render không có GPU, không nên bật HF local inference cho realtime CV upload; nên dùng HF qua endpoint GPU hoặc chỉ chạy async/background.
