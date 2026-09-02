# TalentFlow — AI Recruitment Copilot

TalentFlow là MVP hỗ trợ recruiter tạo vị trí tuyển dụng, tiếp nhận CV, chấm mức độ phù hợp có evidence, duyệt shortlist và đặt lịch phỏng vấn. Hệ thống ưu tiên **decision support**: AI chuẩn bị dữ liệu và đề xuất, recruiter vẫn phê duyệt tiêu chí, shortlist và quyết định với ứng viên.

> Trạng thái hiện tại: workflow AI-assisted đã chạy end-to-end và mốc củng cố nền tảng đã hoàn thành. Queue xử lý nền, calendar/email thật, semantic vector search và agent orchestration chưa được triển khai. Xem [checklist triển khai](./IMPLEMENTATION_CHECKLIST.md) và [kế hoạch mở rộng Agentic AI](./AGENTIC_AI_EXPANSION_PLAN.md).

## Luồng đang hoạt động

```text
Tạo JD
  -> trích xuất requirements bằng OpenRouter hoặc rules fallback
  -> recruiter duyệt tiêu chí
  -> upload tối đa 20 CV PDF/DOCX/TXT
  -> extract text và nhận diện ứng viên
  -> đối chiếu kỹ năng + kinh nghiệm + evidence
  -> tính điểm và sinh interview kit
  -> recruiter review / duyệt shortlist
  -> chọn slot demo và tạo lịch phỏng vấn
```

## Những gì đã triển khai

- Dashboard Next.js responsive, đọc metrics, jobs, candidates và interviews từ FastAPI.
- Đăng ký/đăng nhập bằng Supabase Auth khi có cấu hình; chế độ local không bắt buộc auth.
- Tạo job và trích xuất kỹ năng bắt buộc, ưu tiên, số năm kinh nghiệm từ JD.
- Recruiter phê duyệt tiêu chí trước khi dùng cho quy trình tuyển dụng.
- Upload một batch từ 1–20 CV, hỗ trợ PDF, DOCX, TXT; mỗi file tối đa 10 MB.
- Chỉ lưu text đã extract, metadata và SHA-256 checksum; không lưu file CV gốc.
- Screening có điểm rule, kinh nghiệm, semantic proxy, kỹ năng ưu tiên và evidence theo yêu cầu.
- Xếp hạng Top N, duyệt shortlist và xuất report Markdown kèm interview kit.
- Sinh bộ câu hỏi phỏng vấn riêng theo CV/JD bằng AI hoặc rules fallback.
- Human review với các trạng thái xem xét, từ chối, lưu trữ, chờ/đã đặt lịch.
- Slot phỏng vấn demo có kiểm tra trùng giờ và meeting URL giả lập.
- Dữ liệu được tách theo `owner_id`; có audit log cho các hành động chính.
- SQLite cho local, PostgreSQL cho deploy; Alembic migrations, Docker Compose và GitHub Actions.

## Giới hạn hiện tại

Các điểm sau **chưa phải tích hợp production**:

- API xử lý từng batch CV ngay trong request; Redis chưa được dùng làm queue.
- Redis/worker chưa được đưa vào stack vì background processing chưa được triển khai.
- PostgreSQL hiện chưa bật pgvector; code chưa tạo embedding hay vector index.
- `semantic_score` hiện là lexical proxy, không phải semantic search bằng embedding.
- Chưa dùng LangGraph/agent runtime; workflow được điều phối trực tiếp trong FastAPI.
- Slot lịch được sinh từ dữ liệu mẫu; chưa đọc Google/Outlook Calendar.
- Meeting URL dùng domain `meet.example`; chưa tạo phòng họp hay gửi email thật.
- Chưa có sourcing, candidate outreach, reminder, reschedule hoặc tổng hợp feedback tự động.

## Kiến trúc hiện tại

| Thành phần | Công nghệ | Vai trò |
|---|---|---|
| Frontend | Next.js 16, React 19, TypeScript | Dashboard recruiter |
| Backend | FastAPI, SQLAlchemy | REST API và workflow đồng bộ |
| AI | OpenRouter | Extract requirements/profile/evidence và interview kit |
| Fallback | Python rules | Duy trì luồng offline khi AI lỗi/hết quota |
| CV parser | pypdf, python-docx | Extract text PDF/DOCX/TXT |
| Auth | Supabase Auth | Session và phân vùng dữ liệu theo recruiter |
| Database | SQLite / PostgreSQL | Jobs, applications, interviews, audit logs |
| Deploy | Docker Compose, Render | Local stack và cloud services |

Evidence từ OpenRouter chỉ được chấp nhận khi là trích dẫn xuất hiện nguyên văn trong CV. Trước khi gửi CV đến provider, backend che tên đã nhận diện, email và số điện thoại phổ biến. Đây chưa phải cơ chế ẩn danh hóa toàn diện; không dùng dữ liệu thật trước khi hoàn tất đánh giá bảo mật và chính sách xử lý dữ liệu.

## Chạy local

Yêu cầu: Python 3.11+ và Node.js 22+.

### 1. Backend

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

Mặc định backend dùng `backend/talentflow.db`, tự seed dữ liệu demo và không yêu cầu đăng nhập. API chạy tại [http://localhost:8000](http://localhost:8000), Swagger tại [http://localhost:8000/docs](http://localhost:8000/docs).

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Mở [http://localhost:3000](http://localhost:3000). Nếu không cấu hình Supabase, frontend vào thẳng workspace local và backend dùng tài khoản dev mặc định.

## Cấu hình môi trường

Backend đọc `backend/.env`:

```env
APP_ENV=local
DATABASE_URL=sqlite:///./talentflow.db
OPENROUTER_API_KEY=
OPENROUTER_MODEL=minimax/minimax-m3:free
SUPABASE_URL=
SUPABASE_JWT_SECRET=
AUTH_REQUIRED=false
AUTO_SEED=true
CORS_ORIGINS=http://localhost:3000
```

Frontend đọc `frontend/.env.local`:

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
```

Các biến quan trọng:

| Biến | Bắt buộc | Ý nghĩa |
|---|---:|---|
| `APP_ENV` | Không | `local`, `test`, `staging` hoặc `production` |
| `DATABASE_URL` | Không | Bỏ trống để dùng SQLite local |
| `OPENROUTER_API_KEY` | Không | Bỏ trống để dùng rules fallback |
| `OPENROUTER_MODEL` | Không | Model gọi qua OpenRouter |
| `AUTH_REQUIRED` | Không | `true` để bắt buộc Bearer token |
| `SUPABASE_URL` | Khi bật auth | Endpoint Supabase, đồng thời dùng để đọc JWKS |
| `SUPABASE_JWT_SECRET` | Tuỳ cấu hình | Xác minh JWT HS256; bỏ trống để dùng JWKS |
| `AUTO_SEED` | Không | Seed job/CV demo khi backend khởi động |
| `CORS_ORIGINS` | Khi deploy | Danh sách origin frontend, phân cách bằng dấu phẩy |

Kiểm tra `GET /api/health`: `ai.configured=true` nghĩa là backend đã đọc được OpenRouter key; trường này không khẳng định provider đang sẵn sàng hay còn quota.

## Chạy bằng Docker

```bash
docker compose up --build
```

Stack hiện chỉ khởi động frontend, backend và PostgreSQL. Backend tự chạy Alembic trước khi phục vụ API; Redis, worker và pgvector sẽ được thêm lại khi Phase 1–2 có execution path thật.

## Kiểm thử và build

```bash
cd backend && .venv/bin/pytest -q
cd backend && .venv/bin/python -m evals.run_baseline
cd frontend && npm run build
```

CI chạy backend tests, frontend production build và build cả hai Docker image.

## Seed tài khoản master

`backend/scripts/seed_master.py` tạo hoặc tìm master user trong Supabase Auth, xác nhận email và seed dữ liệu demo theo đúng `user.id`.

```bash
cd backend
SUPABASE_URL=https://... \
SUPABASE_SERVICE_ROLE_KEY=... \
MASTER_EMAIL=admin@example.com \
MASTER_PASSWORD=change-this-password \
DATABASE_URL=postgresql://... \
.venv/bin/python -m scripts.seed_master
```

Không commit `.env`, API key hoặc service-role key vào repository.

## API chính

| Method | Endpoint | Mục đích |
|---|---|---|
| `GET` | `/api/health` | Health, cấu hình AI và auth |
| `GET` | `/api/dashboard` | Metrics, jobs, candidates, interviews |
| `GET` | `/api/jobs` | Danh sách job có filter/pagination |
| `POST` | `/api/jobs` | Tạo JD và extract requirements |
| `POST` | `/api/jobs/{job_id}/approve-criteria` | Duyệt hoặc yêu cầu sửa tiêu chí |
| `GET` | `/api/jobs/{job_id}/shortlist` | Xếp hạng Top N theo final score |
| `POST` | `/api/jobs/{job_id}/approve-shortlist` | Duyệt shortlist |
| `GET` | `/api/jobs/{job_id}/shortlist-report` | Tải report Markdown |
| `DELETE` | `/api/jobs/{job_id}` | Xóa job nếu không còn candidate active |
| `POST` | `/api/applications` | Tạo application từ resume text |
| `GET` | `/api/applications` | Danh sách application có filter/pagination |
| `POST` | `/api/application-batches` | Upload và xử lý batch CV |
| `GET` | `/api/application-batches/{batch_id}` | Đọc kết quả batch |
| `GET` | `/api/applications/{id}/interview-kit` | Lấy hoặc sinh interview kit |
| `POST` | `/api/applications/{id}/review` | Ghi quyết định recruiter |
| `GET` | `/api/interviewers/{id}/available-slots` | Lấy slot demo còn trống |
| `POST` | `/api/applications/{id}/interview` | Đặt lịch và kiểm tra conflict |
| `GET` | `/api/audit-logs` | Đọc lịch sử hành động |

## Cấu trúc repository

```text
frontend/                         Next.js recruiter dashboard
backend/app/                      FastAPI, database, AI và screening rules
backend/tests/                    API/pipeline tests
backend/scripts/                  Seed scripts
.github/workflows/ci.yml          CI pipeline
docker-compose.yml                Local infrastructure
render.yaml                       Render deployment blueprint
AI_Recruitment_Agent_Project_Design.md
AGENTIC_AI_EXPANSION_PLAN.md       Kế hoạch chuyển từ copilot sang agentic workflow
IMPLEMENTATION_CHECKLIST.md        Checklist tiến độ theo từng mốc
```

## Tài liệu

- [Project design ban đầu](./AI_Recruitment_Agent_Project_Design.md)
- [Kế hoạch mở rộng Agentic AI](./AGENTIC_AI_EXPANSION_PLAN.md)
- [Checklist triển khai](./IMPLEMENTATION_CHECKLIST.md)
