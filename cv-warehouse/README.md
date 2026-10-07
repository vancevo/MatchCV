# Kho CV IT

Sub-project lưu trữ toàn bộ CV IT và cung cấp Hybrid Semantic Search cho TalentFlow.

## Chạy local

```bash
cd cv-warehouse/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/uvicorn app.main:app --reload --port 8100
```

```bash
cd cv-warehouse/frontend
npm install
cp .env.local.example .env.local
npm run dev
```

- UI: http://localhost:3100
- API docs: http://localhost:8100/docs

Local mặc định không bắt buộc auth. Production phải bật `AUTH_REQUIRED=true`, cấu hình Supabase và thay API key bootstrap.
JWT có thể mang `app_metadata.tenant_id` và `app_metadata.role` (`OWNER`, `ADMIN`, `VIEWER`); nếu không có, kho dùng `sub` làm tenant và mặc định vai trò `ADMIN`. `VIEWER` chỉ được đọc/tìm/tải, không được upload, sửa, archive hoặc tạo API key.

## Semantic Search

Kho CV chia nội dung thành chunk, tạo embedding BGE-M3, kết hợp semantic score với lexical score và có thể rerank bằng `bge-reranker-v2-m3`.

```env
EMBEDDING_ENABLED=true
EMBEDDING_MODEL_PATH=models/bge-m3
RERANKER_ENABLED=true
RERANKER_MODEL_PATH=models/bge-reranker-v2-m3
```

Nếu model chưa sẵn sàng, API trả `mode=KEYWORD_FALLBACK`; contract API không thay đổi.

## TalentFlow API key

TalentFlow gửi các header:

```http
X-API-Key: <CV_WAREHOUSE_API_KEY>
X-Tenant-ID: <tenant id>
```

Các scope hỗ trợ: `cvs.search`, `cvs.read`, `cvs.download`.
Bootstrap key chỉ dùng cho local/single-tenant và luôn bị khóa vào `DEFAULT_TENANT_ID`; production nên tạo API key scoped trong Kho CV cho đúng tenant.
