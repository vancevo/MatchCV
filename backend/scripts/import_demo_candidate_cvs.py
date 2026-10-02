"""Create and import 20 synthetic FE/BE CVs into the local candidate pool.

The records use fictional identities under the reserved `.test` domain. The
import is idempotent by email + content checksum and performs the same CV
version indexing used by the application.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select

from app.auth import DEV_USER_ID
from app.candidate_profiles import create_resume_version, resolve_candidate_profile
from app.database import session_scope
from app.models import CandidateResumeVersion
from app.resume import store_resume_file


@dataclass(frozen=True)
class DemoCV:
    code: str
    name: str
    role: str
    years: int
    skills: tuple[str, ...]
    domain: str
    project: str
    responsibilities: tuple[str, ...]
    impact: str


CVS = (
    DemoCV("fe01", "An Nhiên", "Junior React Frontend Developer", 1, ("HTML5", "CSS3", "JavaScript", "React", "Git"), "E-commerce", "Trang danh mục và giỏ hàng React", ("Xây component tái sử dụng", "Sửa lỗi responsive", "Viết unit test cơ bản"), "Giảm 20% lỗi giao diện trên mobile"),
    DemoCV("fe02", "Bảo Minh", "Junior Angular Developer", 1, ("HTML5", "SCSS", "TypeScript", "Angular", "RxJS"), "Education", "Cổng học trực tuyến Angular", ("Xây form reactive", "Tích hợp REST API", "Bảo trì design system"), "Hoàn thành 15 màn hình đúng kế hoạch"),
    DemoCV("fe03", "Chi Mai", "React Frontend Developer", 2, ("React", "TypeScript", "Redux Toolkit", "Jest", "REST API"), "Retail", "Dashboard quản lý chuỗi cửa hàng", ("Phát triển dashboard", "Quản lý state Redux", "Viết test component"), "Cải thiện thời gian tải trang 28%"),
    DemoCV("fe04", "Duy Khang", "Angular Frontend Developer", 2, ("Angular", "TypeScript", "RxJS", "Angular Material", "Cypress"), "Logistics", "Ứng dụng theo dõi vận đơn", ("Thiết kế module Angular", "Xây data table", "Kiểm thử end-to-end"), "Rút ngắn 30% thời gian thao tác điều phối"),
    DemoCV("fe05", "Gia Hân", "Vue Frontend Developer", 2, ("Vue.js", "JavaScript", "Pinia", "Tailwind CSS", "Vite"), "Hospitality", "Cổng đặt phòng khách sạn", ("Xây booking flow", "Tối ưu responsive", "Tích hợp payment UI"), "Tăng 18% tỷ lệ hoàn tất đặt phòng"),
    DemoCV("fe06", "Hoàng Long", "Senior React Developer", 3, ("React", "Next.js", "TypeScript", "React Query", "Playwright"), "Fintech", "Cổng quản lý thanh toán doanh nghiệp", ("Thiết kế frontend architecture", "SSR với Next.js", "Review code"), "Tăng Lighthouse performance từ 62 lên 91"),
    DemoCV("fe07", "Khánh Linh", "Angular Developer", 3, ("Angular", "TypeScript", "NgRx", "RxJS", "Jasmine"), "ERP", "Phân hệ kho cho hệ thống ERP", ("Thiết kế state NgRx", "Xây lazy-loaded modules", "Mentor junior"), "Giảm 35% thời gian tải phân hệ kho"),
    DemoCV("fe08", "Lâm Phúc", "React Native Developer", 3, ("React", "React Native", "TypeScript", "Expo", "Firebase"), "Consumer Mobile", "Ứng dụng khách hàng thân thiết", ("Phát triển iOS và Android", "Tích hợp push notification", "Theo dõi crash"), "Đạt 99.5% crash-free sessions"),
    DemoCV("fe09", "Minh Thư", "Lead React Frontend Engineer", 4, ("React", "Next.js", "TypeScript", "GraphQL", "Storybook", "Playwright"), "SaaS", "Nền tảng quản trị đa tenant", ("Dẫn dắt nhóm frontend", "Xây design system", "Thiết lập CI kiểm thử"), "Giảm 40% thời gian phát triển tính năng UI"),
    DemoCV("fe10", "Ngọc Quân", "Lead Angular Engineer", 5, ("Angular", "TypeScript", "NgRx", "RxJS", "Micro Frontends", "Cypress"), "Banking", "Internet banking dạng micro frontend", ("Thiết kế micro frontend", "Quản lý release", "Review bảo mật phía client"), "Triển khai độc lập 6 frontend modules"),
    DemoCV("be01", "Phương Nam", "Junior Python Backend Developer", 1, ("Python", "FastAPI", "PostgreSQL", "REST API", "Docker"), "HR Tech", "API quản lý hồ sơ nhân sự", ("Xây REST endpoints", "Viết SQL query", "Tạo Docker image"), "Đạt 80% unit-test coverage"),
    DemoCV("be02", "Quỳnh Anh", "Junior Node.js Backend Developer", 1, ("Node.js", "NestJS", "TypeScript", "MySQL", "REST API"), "E-commerce", "Dịch vụ đơn hàng", ("Phát triển CRUD APIs", "Validation DTO", "Viết integration test"), "Xử lý ổn định 50 nghìn đơn thử nghiệm"),
    DemoCV("be03", "Sơn Tùng", "Java Backend Developer", 2, ("Java", "Spring Boot", "PostgreSQL", "JPA", "JUnit"), "Insurance", "Dịch vụ quản lý hợp đồng bảo hiểm", ("Xây Spring services", "Thiết kế database", "Viết unit test"), "Giảm 25% lỗi xử lý hợp đồng"),
    DemoCV("be04", "Thanh Hà", "Golang Backend Developer", 2, ("Go", "Gin", "PostgreSQL", "Redis", "Docker"), "Logistics", "Dịch vụ định tuyến giao hàng", ("Xây APIs bằng Gin", "Cache dữ liệu Redis", "Theo dõi service metrics"), "Giảm response time trung bình xuống 120 ms"),
    DemoCV("be05", "Tuấn Kiệt", "Python Backend Engineer", 3, ("Python", "Django", "Django REST Framework", "PostgreSQL", "Celery", "Redis"), "Marketplace", "Nền tảng quản lý nhà bán hàng", ("Thiết kế REST API", "Xử lý background jobs", "Tối ưu truy vấn ORM"), "Giảm 45% thời gian xử lý báo cáo"),
    DemoCV("be06", "Uyên Phương", "Node.js Backend Engineer", 3, ("Node.js", "NestJS", "TypeScript", "PostgreSQL", "RabbitMQ", "Docker"), "Travel", "Hệ thống giữ chỗ theo sự kiện", ("Thiết kế event-driven services", "Xử lý idempotency", "Viết contract test"), "Giảm 60% lỗi đặt chỗ trùng"),
    DemoCV("be07", "Văn Đức", "Senior Java Backend Engineer", 4, ("Java", "Spring Boot", "Kafka", "PostgreSQL", "Kubernetes", "OpenTelemetry"), "Banking", "Nền tảng xử lý giao dịch", ("Thiết kế microservices", "Xây Kafka consumers", "Thiết lập distributed tracing"), "Xử lý 1.500 giao dịch mỗi giây"),
    DemoCV("be08", "Xuân Bách", "Senior Golang Engineer", 4, ("Go", "gRPC", "Kafka", "PostgreSQL", "Kubernetes", "Prometheus"), "Ad Tech", "Hệ thống phân phối quảng cáo thời gian thực", ("Xây gRPC services", "Tối ưu concurrency", "Thiết lập observability"), "Duy trì p95 latency dưới 90 ms"),
    DemoCV("be09", "Yến Nhi", "Senior Python Backend Engineer", 5, ("Python", "FastAPI", "PostgreSQL", "Redis", "AWS", "Terraform", "Docker"), "Fintech", "Nền tảng chấm điểm rủi ro tín dụng", ("Thiết kế API architecture", "Triển khai AWS", "Dẫn dắt review kỹ thuật"), "Giảm 50% thời gian phát hành backend"),
    DemoCV("be10", "Đăng Khoa", "Lead .NET Backend Engineer", 5, ("C#", ".NET", "ASP.NET Core", "SQL Server", "Azure", "Docker", "Kubernetes"), "Enterprise", "Hệ thống tích hợp nghiệp vụ doanh nghiệp", ("Thiết kế clean architecture", "Tích hợp Azure services", "Mentor backend team"), "Tăng availability dịch vụ lên 99.95%"),
)


def _resume_text(item: DemoCV, email: str) -> str:
    return "\n".join((
        f"{item.name} — {item.role}",
        f"Email: {email}",
        f"Kinh nghiệm: {item.years} năm phát triển phần mềm chuyên nghiệp.",
        f"Kỹ năng: {', '.join(item.skills)}.",
        f"Lĩnh vực: {item.domain}.",
        f"Dự án tiêu biểu: {item.project}.",
        "Trách nhiệm:",
        *(f"- {value}." for value in item.responsibilities),
        f"Kết quả: {item.impact}.",
        "Học vấn: Cử nhân Công nghệ Thông tin.",
        "Ngôn ngữ: Tiếng Việt, đọc hiểu tài liệu kỹ thuật tiếng Anh.",
    ))


def main() -> None:
    imported: list[dict] = []
    skipped: list[dict] = []
    with session_scope() as db:
        for item in CVS:
            email = f"synthetic.{item.code}@example.test"
            text = _resume_text(item, email)
            content = text.encode("utf-8")
            digest = hashlib.sha256(content).hexdigest()
            profile = resolve_candidate_profile(
                db, owner_id=DEV_USER_ID, name=item.name, email=email, phone=None,
            )
            existing = db.scalar(select(CandidateResumeVersion).where(
                CandidateResumeVersion.candidate_profile_id == profile.id,
                CandidateResumeVersion.checksum == digest,
            ))
            if existing:
                skipped.append({"code": item.code, "profile_id": profile.id, "version_id": existing.id})
                continue
            extraction = {
                "candidate": {"name": item.name, "email": email, "phone": ""},
                "profile": {
                    "roles": [item.role],
                    "skills": list(item.skills),
                    "experience_years": item.years,
                    "industries": [item.domain],
                    "projects": [item.project],
                    "responsibilities": list(item.responsibilities),
                    "impact": [item.impact],
                    "education": ["Cử nhân Công nghệ Thông tin"],
                    "summary": f"{item.role} có {item.years} năm kinh nghiệm trong lĩnh vực {item.domain}.",
                    "extraction_source": "SYNTHETIC_DEMO",
                },
                "evidence": [],
                "screening_source": "SYNTHETIC_DEMO",
            }
            storage_key = f"synthetic-{item.code}"
            version = create_resume_version(
                db,
                profile=profile,
                storage_key=storage_key,
                original_filename=f"{item.code}_{item.role.replace(' ', '_')}.txt",
                file_size=len(content),
                checksum=digest,
                extracted_text=text,
                extraction=extraction,
            )
            store_resume_file(DEV_USER_ID, storage_key, version.version_filename, content)
            imported.append({
                "code": item.code,
                "profile_id": profile.id,
                "version_id": version.id,
                "filename": version.version_filename,
                "role": item.role,
                "years": item.years,
            })
    print(json.dumps({
        "requested": len(CVS), "imported": len(imported), "skipped": len(skipped),
        "items": imported, "already_present": skipped,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
