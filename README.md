# TalentFlow — AI Recruitment Copilot

TalentFlow là MVP hỗ trợ recruiter tạo vị trí tuyển dụng, tiếp nhận CV, chấm mức độ phù hợp có evidence, duyệt shortlist và đặt lịch phỏng vấn. Hệ thống ưu tiên **decision support**: AI chuẩn bị dữ liệu và đề xuất, recruiter vẫn phê duyệt tiêu chí, shortlist và quyết định với ứng viên.

> Trạng thái hiện tại: Mốc 1–9 đã hoàn thành phần implementation và test offline; các provider integration và production acceptance vẫn chờ tenant thật. Hệ thống có readiness gate, tenant operations metrics, scheduled SLO alerts, enforced canary promotion, tenant/RBAC, connector gateway có consent/provenance, data lifecycle, quota/kill switch và champion/challenger gate. Sourcing agent không được triển khai. Xem [checklist triển khai](./IMPLEMENTATION_CHECKLIST.md), [runbook production acceptance](./PRODUCTION_ACCEPTANCE_RUNBOOK.md), [kế hoạch mở rộng](./AGENTIC_AI_EXPANSION_PLAN.md) và [legal gate cho sourcing](./SOURCING_LEGAL_GATE.md).

## Luồng đang hoạt động

```text
Tạo JD
  -> trích xuất requirements bằng OpenRouter hoặc rules fallback
  -> recruiter duyệt tiêu chí
  -> upload tối đa 20 CV PDF/DOCX/TXT
  -> extract text, nhận diện ứng viên và tạo durable task
  -> Redis/RQ worker đối chiếu kỹ năng + kinh nghiệm + evidence
  -> embedding + calibration; route anomaly/evidence yếu vào approval inbox
  -> tự tạo shortlist proposal khi đủ điều kiện
  -> recruiter review / duyệt proposal (không tự outreach)
  -> gửi link self-scheduling có hạn dùng
  -> candidate chọn slot; outbox tạo Calendar event/meeting và gửi email đúng một lần
  -> RQ scheduler gửi reminder theo policy; candidate có capability link riêng để đổi lịch
  -> thu scorecard có evidence; tổng hợp feedback có nguồn và phát hiện mâu thuẫn
  -> bounce/no-show/timeout/out-of-scope được escalation vào approval inbox
```

## Những gì đã triển khai

- Dashboard Next.js responsive, đọc metrics, jobs, candidates và interviews từ FastAPI.
- Đăng ký/đăng nhập bằng Supabase Auth khi có cấu hình; chế độ local không bắt buộc auth.
- Tạo job và trích xuất kỹ năng bắt buộc, ưu tiên, số năm kinh nghiệm từ JD.
- Recruiter phê duyệt tiêu chí trước khi dùng cho quy trình tuyển dụng.
- Upload một batch từ 1–20 CV, hỗ trợ PDF, DOCX, TXT; mỗi file tối đa 10 MB.
- API trả `202 Accepted`; từng CV có task/run/step bền vững và được worker xử lý độc lập.
- Retry có backoff, timeout, RQ failed registry và nút retry từng CV lỗi.
- Dedupe bằng SHA-256 trong phạm vi recruiter + job; CV trùng liên kết về application đã có.
- Frontend polling trạng thái thật của batch thay cho progress timer mô phỏng.
- Lưu text đã extract, metadata và SHA-256 checksum; production lưu CV gốc trong bucket Supabase Storage private và chỉ mở bằng signed URL ngắn hạn.
- Screening có điểm rule, kinh nghiệm, semantic proxy, kỹ năng ưu tiên và evidence theo yêu cầu.
- Criteria được version hóa; khi duyệt bản mới, toàn bộ hồ sơ được rescreen bằng durable task có parent-run lineage.
- Embedding hashing 96 chiều chạy offline; PostgreSQL lưu bằng pgvector và HNSW cosine index, SQLite test lưu JSON.
- Kho ứng viên có Semantic Search đa ngôn ngữ bằng BGE-M3 chạy local, xếp hạng theo từng CV version và gộp một kết quả cho mỗi candidate.
- Mỗi cặp CV liền kề có bản phân tích khác biệt trung lập: thêm, bỏ, sửa, giữ nguyên và dữ liệu mâu thuẫn; không phán đoán ứng viên tốt lên hay kém đi.
- Score được calibration theo eval baseline; confidence, borderline và score/evidence mismatch được route sang manual review.
- Approval inbox hợp nhất tiêu chí, evidence yếu/anomaly và shortlist proposal.
- Agent run trace model, prompt version, tool, token/cost và fallback; quyết định recruiter được audit riêng.
- OAuth per recruiter cho Google/Microsoft; access/refresh token được mã hóa at rest và tự refresh.
- Free/busy, create/update/cancel event, Gmail/Graph send-mail và webhook ingestion qua provider-neutral adapter.
- Candidate self-scheduling dùng token chỉ lưu hash, single-use, expiry và timezone IANA.
- Mọi calendar/email side effect đi qua transactional outbox có idempotency key, retry/backoff và audit.
- Candidate/interviewer reminder được lên lịch bằng RQ scheduler; reschedule hủy reminder cũ và tạo lịch nhắc mới.
- Link reschedule dùng token hash riêng, có expiry và giới hạn số lần cấu hình theo recruiter.
- Scorecard lưu rubric, rating, evidence và recommendation; feedback summary có version và nguồn scorecard.
- Bounce, no-show, scheduling/feedback timeout, quá giới hạn reschedule và phản hồi ngoài scope đi vào approval inbox.
- Shared tenant dùng role `OWNER`, `ADMIN`, `RECRUITER`, `VIEWER`; viewer chỉ có quyền đọc và mọi query nghiệp vụ giữ tenant boundary.
- Push connector gateway cho nguồn inbox/folder/ATS chỉ nhận `resumes.write`, dùng secret hash, bắt buộc consent timestamp, provenance và replay-safe source reference.
- Candidate data có JSON export, hard-delete có audit hash và retention sweep chỉ áp dụng cho hồ sơ terminal.
- Quota screening/token/cost theo tenant; email và calendar có kill switch được kiểm tra ngay trước dispatch side effect.
- Model/prompt policy chia cohort xác định, trace variant và chỉ activate sau eval regression gate.
- Liveness (`/api/live`) và readiness (`/api/ready`) tách riêng; staging/production fail readiness khi cấu hình bảo mật, database, queue hoặc provider chưa đạt.
- Operations API theo tenant hiển thị task success, P50/P95 latency, outbox failure/backlog, approval quá SLA và mức dùng quota/chi phí.
- SLO evaluator tạo rolling alert chống trùng, hỗ trợ acknowledge và tự resolve/reopen khi telemetry phục hồi hoặc tái diễn.
- Canary release gate so sánh baseline/candidate theo sample size, success rate, P95 latency, cost và outbox failure trước promotion.
- RQ scheduler tự chạy SLO sweep; on-call notification dùng cùng transactional outbox và chỉ gửi một lần cho mỗi alert episode.
- Promotion endpoint và CLI CI/CD chặn fail-closed nếu canary/readiness/critical-alert gate chưa đạt.
- Mail Sandbox theo tenant chỉ cho phép inbox chính hoặc Gmail alias dạng `vinhvp.khmtk36+<number>@gmail.com`; alias được giữ nguyên trong candidate/outbox/provider payload.
- Xếp hạng Top N, duyệt shortlist và xuất report Markdown kèm interview kit.
- Sinh bộ câu hỏi phỏng vấn riêng theo CV/JD bằng AI hoặc rules fallback.
- Human review với các trạng thái xem xét, từ chối, lưu trữ, chờ/đã đặt lịch.
- Slot phỏng vấn demo có kiểm tra trùng giờ và meeting URL giả lập.
- Dữ liệu được tách theo `owner_id`; có audit log cho các hành động chính.
- SQLite cho local, PostgreSQL cho deploy; Alembic migrations, Docker Compose và GitHub Actions.

## Giới hạn hiện tại

Các điểm sau **chưa phải tích hợp production**:

- Screening vẫn giữ feature hashing 96 chiều để calibration; riêng tìm kiếm kho ứng viên dùng neural embedding BGE-M3 1024 chiều.
- Calibration hiện dùng bộ eval nhỏ; cần dữ liệu recruiter đã ẩn danh trước khi chọn threshold production.
- Chưa dùng LangGraph/agent runtime; workflow được điều phối trực tiếp trong FastAPI.
- Profile `local` dùng slot theo working hours và `meet.example`; chọn `INTEGRATION_PROVIDER=google|microsoft` cùng OAuth credentials để gọi provider thật.
- Chưa chạy acceptance test end-to-end với tenant Google/Microsoft thật trong repo; cần hoàn tất trước production rollout.
- Chưa có adapter pull cụ thể cho Gmail/Drive/ATS; upstream phải đẩy payload đã được consent vào connector gateway và cần acceptance test riêng.
- Chưa có sourcing agent; runtime cố ý không scrape hoặc tự động tìm/liên hệ ứng viên cho tới khi hoàn tất legal gate theo từng nguồn.
- Việc hiểu nội dung reply ngoài scope hiện dựa trên event đã được provider/adapter phân loại; chưa có NLP classifier cho email tự do.
- Readiness, metrics, alert lifecycle và canary gate đã có trong ứng dụng; dashboard hạ tầng bên ngoài vẫn phải cấu hình trên môi trường deploy.
- Scheduler/notification/CLI đã có trong code; production vẫn cần cấu hình recipient thật, RQ scheduler HA, fault-injection và đặt CLI trước bước chuyển traffic.

## Kiến trúc hiện tại

| Thành phần | Công nghệ | Vai trò |
|---|---|---|
| Frontend | Next.js 16, React 19, TypeScript | Dashboard recruiter |
| Backend | FastAPI, SQLAlchemy | REST API, persistence và tạo durable task |
| Queue | Redis, RQ | Durable background screening, retry và failed registry |
| Worker | RQ worker | Screening từng CV và ghi agent run/step |
| AI | OpenRouter | Extract requirements/profile/evidence và interview kit |
| Fallback | Python rules | Duy trì luồng offline khi AI lỗi/hết quota |
| CV parser | pypdf, python-docx | Extract text PDF/DOCX/TXT |
| Auth | Supabase Auth | Session và phân vùng dữ liệu theo recruiter |
| Database | SQLite / PostgreSQL + pgvector | Versioned artifacts, embeddings, proposals, approvals và dữ liệu nghiệp vụ |
| Deploy | Docker Compose, Render | Local stack và cloud services |

Evidence từ OpenRouter chỉ được chấp nhận khi là trích dẫn xuất hiện nguyên văn trong CV. Trước khi gửi CV đến provider, backend che tên đã nhận diện, email và số điện thoại phổ biến. Đây chưa phải cơ chế ẩn danh hóa toàn diện; không dùng dữ liệu thật trước khi hoàn tất đánh giá bảo mật và chính sách xử lý dữ liệu.

## Chạy local

Yêu cầu: Python 3.11+ và Node.js 22+.

Repo có `.nvmrc`; chạy `nvm use` ở thư mục gốc để dùng đúng Node.js 22.19.0 trước khi cài hoặc build frontend.

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

Để chạy model TalentFlow 3B trực tiếp trên máy local, cài thêm các dependency nặng bằng
`.venv/bin/pip install -r requirements-local-hf.txt` và giữ `TALENTFLOW_MODEL_BACKEND=local`.
Trên Render, dùng `TALENTFLOW_MODEL_BACKEND=remote`, đặt `HF_TOKEN` và
`TALENTFLOW_INFERENCE_ENDPOINT_URL` thành URL của Hugging Face Inference Endpoint đã deploy.
Backend production chỉ gọi API từ xa, không tải PyTorch hoặc model 3B vào Render.

Semantic Search dùng model BGE-M3 riêng và không gọi Hugging Face khi người dùng tìm kiếm. Cài model một lần trước khi bật:

```bash
cd backend
.venv/bin/python scripts/download_embedding_model.py
# sau đó đặt EMBEDDING_ENABLED=true trong .env
```

Nếu model chưa được cài hoặc bị tắt, API vẫn hoạt động bằng keyword fallback và trả rõ `mode=KEYWORD_FALLBACK`.

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
OPENROUTER_MODEL=google/gemini-2.5-flash-lite
SUPABASE_URL=
SUPABASE_JWT_SECRET=
AUTH_REQUIRED=false
AUTO_SEED=true
REDIS_URL=
QUEUE_EAGER=true
TASK_MAX_ATTEMPTS=3
TASK_TIMEOUT_SECONDS=120
CORS_ORIGINS=http://localhost:3000
PUBLIC_APP_URL=http://localhost:3000
INTEGRATION_PROVIDER=local
INTEGRATION_TOKEN_SECRET=replace-with-a-long-random-secret
OAUTH_STATE_SECRET=replace-with-another-long-random-secret
PROVIDER_WEBHOOK_SECRET=replace-with-a-webhook-secret
OPERATIONAL_SWEEP_INTERVAL_MINUTES=15
EMBEDDING_ENABLED=false
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_MODEL_PATH=models/bge-m3
EMBEDDING_MODEL_REVISION=5617a9f61b028005a4858fdac845db406aefb181
EMBEDDING_DIMENSION=1024
EMBEDDING_DEVICE=cpu
# GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI
# MICROSOFT_CLIENT_ID / MICROSOFT_CLIENT_SECRET / MICROSOFT_REDIRECT_URI
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
| `SUPABASE_SERVICE_ROLE_KEY` | Khi dùng Storage | Chỉ đặt ở backend; dùng tạo bucket private và quản lý CV gốc |
| `RESUME_STORAGE_BACKEND` | Không | `local` khi phát triển, `supabase` trên Render |
| `RESUME_STORAGE_BUCKET` | Không | Bucket private chứa CV, mặc định `resumes` |
| `RESUME_SIGNED_URL_TTL_SECONDS` | Không | Thời gian sống signed URL, mặc định 300 giây |
| `AUTO_SEED` | Không | Seed job/CV demo khi backend khởi động |
| `REDIS_URL` | Khi chạy async | Redis URL dùng chung cho API và worker |
| `QUEUE_EAGER` | Không | `true` chạy inline cho development/test; `false` bắt buộc Redis |
| `QUEUE_NAME` | Không | Tên queue, mặc định `talentflow-screening` |
| `TASK_MAX_ATTEMPTS` | Không | Tổng số lần worker thử một task |
| `TASK_TIMEOUT_SECONDS` | Không | Timeout mỗi RQ job |
| `CORS_ORIGINS` | Khi deploy | Danh sách origin frontend, phân cách bằng dấu phẩy |
| `INTEGRATION_PROVIDER` | Không | `local`, `google` hoặc `microsoft`; chọn provider thực thi side effect |
| `INTEGRATION_TOKEN_SECRET` | Provider thật | Khóa mã hóa access/refresh token at rest |
| `OAUTH_STATE_SECRET` | Provider thật | Khóa ký OAuth state chống CSRF/replay |
| `PUBLIC_APP_URL` | Khi gửi lời mời | Origin dùng để tạo link self-scheduling |
| `PROVIDER_WEBHOOK_SECRET` | Khi deploy webhook | Secret kiểm tra Google channel token, Microsoft clientState hoặc header nội bộ |
| `GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI` | Khi dùng Google | OAuth web application credentials |
| `MICROSOFT_CLIENT_ID/SECRET/REDIRECT_URI` | Khi dùng Microsoft | Microsoft identity platform credentials |

Sử dụng `GET /api/live` cho liveness và `GET /api/ready` cho readiness probe. `GET /api/health` giữ summary tương thích; `ai.configured=true` chỉ nghĩa là backend đã đọc được OpenRouter key, không khẳng định provider còn quota.

## Chạy bằng Docker

```bash
docker compose up --build
```

Stack khởi động frontend, backend, PostgreSQL, Redis và RQ worker thật. Backend chạy Alembic trước khi phục vụ API; worker chỉ khởi động sau khi backend/Redis healthy.

Khi chạy backend trực tiếp mà chưa có Redis, giữ `QUEUE_EAGER=true` để xử lý inline. Để thử đúng execution path async ngoài Docker, chạy Redis rồi đặt `REDIS_URL`, `QUEUE_EAGER=false` và mở worker:

```bash
make worker
```

Blueprint Render hiện giữ `QUEUE_EAGER=true` để không tự tạo resource có chi phí. Khi deploy async trên Render, cần chủ động tạo Key Value + Background Worker, cấp cùng `DATABASE_URL`, `REDIS_URL`, OpenRouter config và chuyển backend sang `QUEUE_EAGER=false`.
Vì vậy blueprint miễn phí này là môi trường demo và sẽ không vượt qua production readiness gate của Phase 6 cho tới khi Redis/worker và provider thật được cấu hình.

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
| `GET` | `/api/live` | Liveness tối thiểu của API process |
| `GET` | `/api/ready` | Readiness của database, queue và production config |
| `GET` | `/api/operations/readiness` | Readiness chi tiết theo tenant, gồm active provider connection |
| `GET` | `/api/operations/metrics` | Workflow health, latency, outbox, approval SLA và quota theo tenant |
| `GET/PUT` | `/api/operations/slo-policy` | Đọc/cấu hình ngưỡng SLO và canary theo tenant |
| `POST` | `/api/operations/evaluate` | Đánh giá telemetry, dedupe hoặc auto-resolve operational alerts |
| `POST` | `/api/operations/sweep` | Chạy tenant sweep và dispatch notification outbox ngay |
| `GET` | `/api/operations/alerts` | Danh sách alert theo tenant và trạng thái |
| `POST` | `/api/operations/alerts/{id}/action` | Acknowledge hoặc resolve alert |
| `POST/GET` | `/api/operations/release-gates` | Đánh giá và đọc lịch sử canary promotion gate |
| `POST` | `/api/operations/release-gates/{id}/promote` | Enforce readiness/canary/critical-alert gate và ghi nhận promotion |
| `GET/PUT` | `/api/mail-sandbox` | Xem hoặc cấu hình inbox chính và whitelist `+number` |
| `POST` | `/api/mail-sandbox/test` | Gửi email test tới alias được chọn và trả recipient đã lưu |
| `POST` | `/api/candidate-profiles/search` | Semantic Search trực tiếp trên toàn bộ CV version trong kho ứng viên |
| `GET` | `/api/candidate-profiles/search/status` | Trạng thái model local và số bản ghi index theo trạng thái |
| `POST` | `/api/candidate-profiles/search/reindex` | Tạo lại semantic index cho tenant (`OWNER`/`ADMIN`) |
| `GET` | `/api/candidate-profiles/{id}/resume-comparisons` | Danh sách phân tích thay đổi trung lập giữa các CV version |

CI/CD có thể gọi gate fail-closed trước khi chuyển traffic:

```bash
cd backend
TALENTFLOW_API_URL=https://api.example.com \
TALENTFLOW_ACCESS_TOKEN=... \
TALENTFLOW_TENANT_ID=... \
TALENTFLOW_RELEASE_VERSION=v1.0.0 \
.venv/bin/python -m scripts.promote_release
```

## Mail Sandbox cho tester

Mở mục **Mail Sandbox** trên sidebar, giữ email chính `vinhvp.khmtk36@gmail.com`, chọn giới hạn alias rồi bật whitelist. Ví dụ alias số `7` tạo recipient `vinhvp.khmtk36+7@gmail.com`. Gmail chuyển thư về inbox chính nhưng TalentFlow vẫn lưu nguyên alias trong candidate, outbox và email `To` header.

Khi sandbox bật, email hoặc calendar attendee nằm ngoài email chính và dải `+1` đến `+max_alias` sẽ bị chuyển sang `BLOCKED` trước external side effect. Với `INTEGRATION_PROVIDER=local`, nút gửi test chỉ mô phỏng; muốn nhận email thật cần kết nối Google/Microsoft và OAuth có quyền gửi mail.
| `GET` | `/api/dashboard` | Metrics, jobs, candidates, interviews |
| `GET` | `/api/jobs` | Danh sách job có filter/pagination |
| `POST` | `/api/jobs` | Tạo JD và extract requirements |
| `POST` | `/api/jobs/{job_id}/approve-criteria` | Duyệt hoặc yêu cầu sửa tiêu chí |
| `PUT` | `/api/jobs/{job_id}/criteria` | Tạo criteria version mới |
| `GET` | `/api/jobs/{job_id}/criteria-versions` | Xem version và lineage tiêu chí |
| `PUT` | `/api/jobs/{job_id}/shortlist-trigger` | Cấu hình ngưỡng tự tạo proposal |
| `GET` | `/api/jobs/{job_id}/shortlist` | Xếp hạng Top N theo final score |
| `POST/GET` | `/api/jobs/{job_id}/shortlist-proposals` | Trigger hoặc xem shortlist proposal |
| `POST` | `/api/jobs/{job_id}/approve-shortlist` | Duyệt shortlist |
| `GET` | `/api/approvals` | Approval inbox theo trạng thái/type |
| `POST` | `/api/approvals/{id}/resolve` | Approve/reject criteria, evidence, shortlist hoặc escalation |
| `GET` | `/api/jobs/{job_id}/shortlist-report` | Tải report Markdown |
| `DELETE` | `/api/jobs/{job_id}` | Xóa job nếu không còn candidate active |
| `POST` | `/api/applications` | Tạo application từ resume text |
| `GET` | `/api/applications` | Danh sách application có filter/pagination |
| `POST` | `/api/application-batches` | Upload, dedupe và enqueue batch (`202`) |
| `GET` | `/api/application-batches/{batch_id}` | Đọc kết quả batch |
| `POST` | `/api/application-batches/{batch_id}/items/{item_id}/retry` | Retry một CV screening lỗi |
| `GET` | `/api/agent-runs/{run_id}` | Trạng thái và execution steps của agent run |
| `GET` | `/api/applications/{id}/interview-kit` | Lấy hoặc sinh interview kit |
| `POST` | `/api/applications/{id}/review` | Ghi quyết định recruiter |
| `GET` | `/api/integrations` | Trạng thái kết nối calendar/email theo recruiter |
| `POST` | `/api/integrations/{provider}/authorize` | Bắt đầu OAuth Google/Microsoft |
| `DELETE` | `/api/integrations/{provider}` | Revoke connection và xóa token đã lưu |
| `GET/POST` | `/api/email-templates` | Đọc hoặc tạo version template mới |
| `GET` | `/api/interviewers/{id}/available-slots` | Free/busy provider + conflict nội bộ |
| `POST` | `/api/applications/{id}/scheduling-invitations` | Gửi link self-scheduling có expiry |
| `GET/POST` | `/api/public/scheduling/{token}` | Candidate chọn lịch bằng token single-use hoặc đổi lịch bằng capability token riêng |
| `POST` | `/api/applications/{id}/interview` | Recruiter đặt lịch idempotent |
| `PUT/DELETE` | `/api/interviews/{id}` | Reschedule/cancel qua calendar adapter |
| `GET/PUT` | `/api/interview-policy` | Đọc/cấu hình reminder, reschedule và feedback deadline |
| `GET` | `/api/interviews/{id}/operations` | Reminder, scorecard và feedback summary của một lịch |
| `POST` | `/api/interviews/{id}/scorecards` | Nộp structured scorecard và tạo summary có nguồn |
| `POST` | `/api/interviews/{id}/no-show` | Ghi no-show và tạo escalation |
| `POST` | `/api/interview-operations/run-due` | Chạy sweep idempotent cho due reminder/timeout |
| `GET` | `/api/outbox` | Theo dõi side effect và retry state |
| `POST` | `/api/webhooks/{provider}` | Ingest provider webhook có dedupe |
| `GET/POST` | `/api/tenants` | Liệt kê hoặc tạo shared tenant |
| `POST` | `/api/tenants/{id}/members` | Cấp role tenant cho user |
| `GET/PUT` | `/api/tenant-policy` | Retention, quota và email/calendar kill switch |
| `GET/POST` | `/api/source-connectors` | Quản lý connector ingress có least-privilege token |
| `POST` | `/api/source-connectors/{id}/ingest` | Ingest CV có consent/provenance và replay protection |
| `GET` | `/api/applications/{id}/data-export` | Export candidate data dạng JSON cùng provenance |
| `DELETE` | `/api/applications/{id}/data` | Xóa dữ liệu ứng viên và giữ audit hash không chứa PII |
| `POST` | `/api/data-lifecycle/run-retention` | Xóa hồ sơ terminal quá retention window |
| `GET/PUT` | `/api/model-policy` | Cấu hình champion/challenger |
| `POST` | `/api/model-policy/evaluate` | Chạy eval gate trước activation |
| `POST` | `/api/model-policy/activate` | Kích hoạt policy đã pass gate |
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
