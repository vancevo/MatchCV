# TalentFlow — AI Recruitment Agent

MVP tuyển dụng có pipeline sàng lọc CV giải thích được, human-in-the-loop và đặt lịch phỏng vấn. Project được triển khai từ tài liệu [`AI_Recruitment_Agent_Project_Design.md`](./AI_Recruitment_Agent_Project_Design.md).

## Tính năng hiện có

- Dashboard recruiter responsive, lấy metrics/ranking/chart trực tiếp từ API.
- Tạo việc làm và upload CV PDF/DOCX/TXT ngay trên giao diện.
- Lưu file CV gốc trong `backend/uploads` và cho recruiter mở/tải lại từ hồ sơ ứng viên.
- Pipeline `extract → validate → rule match → semantic match → evidence → score`.
- Điểm số có trọng số và evidence theo từng yêu cầu.
- Chi tiết ứng viên, trạng thái pipeline và recruiter review: xem xét, từ chối, mời phỏng vấn, lưu trữ.
- Chia tab ứng viên theo trạng thái để recruiter dễ theo dõi.
- Xoá việc làm khi chưa có ứng viên hoặc mọi ứng viên liên quan đã bị từ chối/lưu trữ.
- Scheduling agent demo với kiểm tra slot conflict.
- REST API FastAPI, OpenAPI tại `/docs`, audit log và validation upload.
- Unit tests, Docker Compose và GitHub Actions.

Khi có `OPENROUTER_API_KEY`, backend dùng `minimax/minimax-m3:free` để extract requirement từ JD và candidate profile/evidence từ CV. Evidence do AI trả về chỉ được chấp nhận khi là trích dẫn tồn tại nguyên văn trong CV. Nếu thiếu key, OpenRouter lỗi hoặc hết quota, hệ thống tự động dùng matching deterministic/offline để luồng tuyển dụng vẫn hoạt động.

Tạo `backend/.env` từ file mẫu và dùng một API key mới (không commit file `.env`):

```env
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=minimax/minimax-m3:free
```

Kiểm tra `GET /api/health`: `ai.configured=true` nghĩa là backend đã đọc được key. CV được xóa email, số điện thoại và tên ứng viên đã nhập trước khi gửi đến free endpoint; không nên dùng CV thật nếu chưa xác nhận chính sách dữ liệu của provider.

## Chạy nhanh để phát triển

Yêu cầu: Node.js 20+, Python 3.11+.

```bash
cd frontend
npm install
npm run dev
```

Mở [http://localhost:3000](http://localhost:3000). Dashboard có sẵn dữ liệu demo và tương tác xem evidence/đặt lịch.

Chạy API ở terminal khác:

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
```

API: [http://localhost:8000](http://localhost:8000) · Swagger: [http://localhost:8000/docs](http://localhost:8000/docs)

## Chạy toàn bộ bằng Docker

```bash
docker compose up --build
```

Compose khởi động frontend, backend, worker demo, PostgreSQL + pgvector và Redis.

## Kiểm thử và build

```bash
cd backend && .venv/bin/pytest -q
cd frontend && npm run build
```

## API chính

| Method | Endpoint | Mục đích |
|---|---|---|
| `GET` | `/api/dashboard` | Metrics, jobs và ranking |
| `POST` | `/api/jobs` | Tạo JD và extract requirements |
| `DELETE` | `/api/jobs/{job_id}` | Xoá job nếu không còn candidate active |
| `POST` | `/api/applications` | Tạo ứng viên và chạy screening |
| `POST` | `/api/applications/upload` | Upload CV, trích xuất nội dung và chạy screening |
| `GET` | `/api/applications/{id}/resume` | Mở hoặc tải file CV gốc đã lưu |
| `POST` | `/api/applications/{id}/review` | Human review |
| `GET` | `/api/interviewers/{id}/available-slots` | Lấy lịch trống |
| `POST` | `/api/applications/{id}/interview` | Đặt lịch có conflict check |
| `GET` | `/api/audit-logs` | Theo dõi quyết định pipeline |

## Cấu trúc

```text
frontend/       Next.js dashboard
backend/        FastAPI + screening pipeline + tests
.github/        CI workflow
docker-compose.yml
```
