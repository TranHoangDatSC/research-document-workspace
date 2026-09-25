# Ngày 3 — Giao diện và vòng đời tài liệu

## Phạm vi

FastAPI phục vụ cả HTML Jinja2, CSS và API trong container `web`.
Giữ các thư mục `api/`, `schemas/`, `services/`, `repositories/` đã refactor.
Thêm `ui/`, `templates/`, `static/`; không tạo frontend service riêng.
Chỉ thêm dependency runtime `jinja2>=3.1.6,<4`.
Dockerfile, Compose, cấu hình kết nối và volume giữ nguyên.

## Chạy sau khi áp dụng source

```powershell
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { throw 'Build/start failed.' }
docker compose exec -T web python -m app.bootstrap
if ($LASTEXITCODE -ne 0) { throw 'Bootstrap failed. Stop here.' }
docker compose up -d --wait --wait-timeout 180
if ($LASTEXITCODE -ne 0) { throw 'Services not healthy.' }
Invoke-RestMethod http://127.0.0.1:8001/health/ready
Start-Process 'http://127.0.0.1:8001/'
```

Bootstrap bổ sung giá trị `deleting` vào CHECK constraint của `documents.status`.
Thay đổi thực hiện trong transaction PostgreSQL, không xóa bảng hoặc dữ liệu.
Chạy bootstrap trước khi thử DELETE. Có thể chạy lại bootstrap.

## Route mới

| Route | Chức năng |
|---|---|
| `GET /` | Trạng thái ba kho, danh sách và form tạo dự án |
| `POST /ui/projects` | Tạo dự án, redirect 303 |
| `GET /ui/projects/{id}` | Upload và danh sách tài liệu, phân trang 20 dòng |
| `POST /ui/projects/{id}/documents` | Upload, redirect đến metadata |
| `GET /ui/documents/{id}` | Metadata tổng hợp |
| `GET /ui/documents/{id}/delete` | Trang xác nhận, chưa xóa dữ liệu |
| `POST /ui/documents/{id}/delete` | Xóa sau khi xác nhận |
| `DELETE /documents/{id}` | API xóa có thể gọi lại |

Download dùng endpoint Ngày 2. Giới hạn 10 MiB, đọc có giới hạn để phát hiện lỗi trước khi trả HTTP 200; chưa đổi sang streaming trong checkpoint này.
Các endpoint JSON Ngày 2 giữ validation và status code (đuôi file không hợp lệ: 415).
Form và API gọi chung service Python, không gọi HTTP vòng về chính ứng dụng.
Jinja tự escape dữ liệu; lỗi form trả HTML, lỗi API vẫn trả JSON.
Không log nội dung file hoặc credential. Log sự kiện dùng ID và loại lỗi.

## Thao tác xóa và giới hạn

1. Ghi và commit trạng thái `deleting` trong PostgreSQL.
2. Xóa object MinIO (object đã vắng là no-op).
3. Xóa metadata MongoDB (metadata đã vắng là no-op).
4. Xóa bản ghi PostgreSQL cuối cùng.

Nếu gián đoạn, trả 503, giữ bản ghi `deleting`, chặn metadata/download bằng 409.
Trang dự án hiển thị “Thử xóa lại”; người dùng chủ động thử lại sau phục hồi.
DELETE UUID không tồn tại trả 200 với `deleted: true` để hỗ trợ retry sau mất phản hồi commit.
GET tài liệu không tồn tại vẫn trả 404. UUID sai định dạng trả 422.
Không cho xóa bản ghi `pending` để tránh đua với upload đang chạy.
Không có transaction chung giữa ba kho. Không tuyên bố xóa nguyên tử.
Yêu cầu đọc đã bắt đầu trước thao tác xóa vẫn có thể hoàn tất hoặc gặp lỗi 503.
Không có worker tự dọn `pending`/`deleting`. Xóa thành công không thể hoàn tác từ UI.

## Kiểm thử tự động trên Docker thật

Script có dừng MongoDB tạm thời và recreate các container. Chạy khi không có thao tác upload khác.
Chỉ xóa tài liệu test do chính script tạo; giữ volume và tài liệu của bạn.
Phase `before` tạo dữ liệu mới và cập nhật state; phase `after` đọc lại cùng ID, không seed lại.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\integration\run_day3.ps1
if ($LASTEXITCODE -ne 0) { throw 'Day 3 checks failed. Stop here.' }
```

`ExecutionPolicy Bypass` chỉ áp dụng cho tiến trình PowerShell này, không đổi chính sách toàn máy.
Runner thực hiện:
- Ngày 2 `before` (regression tạo/upload/download).
- Ngày 3 `before` (HTML, form, hash, xóa và đối chiếu cả ba kho).
- Dừng MongoDB; Ngày 3 `failure` xác nhận xóa dở dang trả 503 và lưu intent.
- Khởi động lại MongoDB trong `finally`; `recovery` thử lại xóa.
- `docker compose down` rồi `up -d --build --wait`, tuyệt đối không thêm `-v`.
- Ngày 2 `after`, Ngày 3 `after` kiểm tra cùng ID và dữ liệu đã xóa không trở lại.
- Kiểm tra Git có theo dõi `.env`, `.venv`, `__pycache__`, `.pyc` hay không.

Kết quả được ghi từ kiểm thử thật tại `artifacts/day-03/` và sao chép báo cáo vào `docs/evidence/day-03/` khi runner hoàn tất.
Không tạo sẵn bằng chứng `PASS`.

Nếu bài thử thất bại, dừng tại lỗi đầu tiên. Không chạy lại toàn bộ chỉ để tìm PASS.
Nếu đã ngắt PowerShell khi MongoDB đang dừng, phục hồi trước:

```powershell
docker compose start mongo
docker compose up -d --wait --wait-timeout 180
```

Cung cấp đúng output liên quan: `docker compose logs --tail 100 web` nếu lỗi app, hoặc log service báo lỗi. Không gửi `.env`.

## Kiểm tra thủ công

- [ ] Mở http://127.0.0.1:8001/; trạng thái ba kho đều up.
- [ ] Tạo dự án bằng form; nhìn thấy trang dự án vừa tạo.
- [ ] Upload file riêng cùng tags, tác giả và JSON metadata.
- [ ] Trang chi tiết hiển thị đúng thông tin; tên file được escape bình thường.
- [ ] Download bằng trình duyệt; dùng `Get-FileHash` so sánh file gốc và file tải về.
- [ ] Chỉ xóa tài liệu thử; xem trang xác nhận trước khi xóa.
- [ ] Kiểm tra giao diện ở cửa sổ hẹp; form/bảng vẫn sử dụng được.
- [ ] Chụp màn hình trang dự án và metadata cho báo cáo.

Hash (thay hai đường dẫn bằng file thật đã chọn):

```powershell
Get-FileHash -Algorithm SHA256 'C:\duong-dan\file-goc.txt'
Get-FileHash -Algorithm SHA256 'C:\duong-dan\file-tai-ve.txt'
```

## Unit tests tùy chọn cho người phát triển

Không cần cài thêm dependency trên host để chạy integration tests.
Unit tests dùng fake storage cần app dependencies và `httpx`:

```powershell
python -m pip install -r requirements.txt httpx
python -m unittest discover -s tests/unit -v
```

## Git và chốt ngày

```powershell
git status --short
git diff --check
git ls-files | Select-String -Pattern '(^|/)\.env$|(^|/)\.venv/|(^|/)__pycache__/|\.pyc$'
```

Lệnh cuối phải không có output. Không dùng `git add .` vô điều kiện.
Sau khi đọc diff và đạt kiểm thử:

```powershell
git add -- app requirements.txt tests/integration/day3_test.py tests/integration/run_day3.ps1 tests/unit/test_day3.py docs/day-03.md docs/evidence/day-03
git diff --cached --check
git diff --cached --stat
git commit -m "feat: add web UI and retryable document deletion"
```

Chốt Ngày 3 khi runner PASS và checklist trình duyệt hoàn tất. Báo cáo rõ chưa có authentication/HTTPS, chỉ demo localhost; chưa có distributed transaction hay AI.

Nguồn tham khảo: https://fastapi.tiangolo.com/advanced/templates/
