# Research Document Workspace
-> This is my final project for Cloud Computing (Subject)

# Khởi động dự án
```
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

# Khởi động MinIO
```
docker run -d -p 9000:9000 -p 9001:9001 --name minio -e "MINIO_ROOT_USER=YOURPASSWORD" -e "MINIO_ROOT_PASSWORD=YOURPASSWORD" quay.io/minio/minio server /data --console-address ":9001"
```
-> Đăng nhập bằng Username & Password là: Của bạn
-> Đăng nhập tại http://localhost:9001/

# Khởi động hệ thống với Docker
```
docker compose up -d --build
```