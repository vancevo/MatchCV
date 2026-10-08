# Lời nhắc: tự kiểm chứng danh mục chung (catalog)

Tác nhân kiểm chứng độc lập đã chạy một vòng và tìm ra các lỗi đã được sửa. Vòng kiểm chứng thứ hai
**chưa chạy**, phần này để bạn tự kiểm. Con số "trước khi sửa" lấy từ lần kiểm chứng độc lập.

## 1. Chạy các bộ test (phải xanh)
```bash
cd backend && .venv/bin/pytest -q
cd cv-warehouse/backend && .venv/bin/python -m pytest -q
python3 shared/catalog/sync.py --check          # ba bản sao catalog phải giống nhau
```

## 2. Hai hệ thống phải dùng cùng một danh sách
Bật cả hai backend (cổng 8000 và 8100), rồi so sánh mã băm (hash) ở hai API:
```bash
curl -s localhost:8000/api/catalog | head -c 400
curl -s localhost:8100/api/v1/catalog | head -c 400
```
Hai bên phải cùng `catalog_version` và cùng sha256.

## 3. Đo lại những chỉ số từng sai
| Chỉ số | Trước khi sửa (đo độc lập) | Cách đo |
|---|---|---|
| Tìm kiếm TalentFlow, precision@10 | 86,7% | `python3 shared/catalog/evals/run_search_eval.py` |
| Tìm kiếm TalentFlow khi tắt tăng điểm ngành | 77,0% | như trên, chế độ không boost |
| Tìm kiếm kho CV khi tắt tự nhận ngành | 77,4% | như trên |
| Số năm kinh nghiệm trích từ JD | 25/32 | `python3 shared/catalog/evals/run_jd_eval.py` |
| Ngành của JD, hai hệ thống có khớp nhau | có chỗ lệch | chạy cùng một JD ở cả hai nơi |

## 4. Thử tay các ca từng lỗi
- Kho CV (cổng 3100): tìm "backend engineer with Kubernetes" kèm kỹ năng bắt buộc Kubernetes. Trước đây ra 0 kết quả, giờ phải có kết quả.
- JD có dòng "Kinh nghiệm: 3-5 năm" phải ra 3 năm.
- JD có đoạn giới thiệu công ty nhắc "Google Cloud", "MongoDB" thì hai kỹ năng đó **không** nằm trong yêu cầu bắt buộc.
- "Có thể học thêm: AWS" thì AWS không phải kỹ năng bắt buộc.
- Một CV ghi "Google Data Analytics coursework" không được có chứng chỉ "Google Data Analytics Certificate".
- Chức danh "Solution Architect" không được gán cấp bậc Lead.
- Sửa tay ngành của một CV trong kho, sau đó gọi `POST /api/v1/cvs/reextract`: giá trị bạn sửa tay phải còn nguyên.
- Danh mục ngành nay có **11** nhóm (thêm FRONTEND ở cuối). Kho CV (cổng 3100) phải hiện 11 tab, tab Frontend có 50 CV, mục "chưa phân loại" bằng 0. Thử tìm "Frontend developer React TypeScript": kết quả đầu phải là CV Frontend, còn CV có chức danh "Full-stack" vẫn thuộc Fullstack.
- Gửi một chuỗi rác dài 60.000 ký tự vào tìm kiếm: phải trả lời trong dưới 1 giây (trước đây treo 36 giây).

## 5. Việc cần bạn quyết định
- 50 hồ sơ TalentFlow (Fullstack_351 đến 400) có hai phiên bản CV trùng checksum. Mã không xoá dữ liệu thật,
  chỉ có script báo cáo `backend/scripts/report_duplicate_versions.py`. Xem danh sách rồi quyết định có xoá không.
- Chưa thử trên PostgreSQL: migration `0026`, phần nạp dữ liệu danh mục và các lệnh ALTER của kho CV mới chỉ chạy trên SQLite.
  Cần thử trước khi triển khai thật.
- Bộ JD mẫu do chính tác nhân xây dựng viết. Nên tự viết thêm vài JD thật của công ty bạn để thử.
