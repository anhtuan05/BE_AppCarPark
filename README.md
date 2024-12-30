# BE_AppCarPark

**BE_AppCarPark** là backend của hệ thống quản lý bãi đỗ xe có tích hợp nhận diện khuôn mặt

---

## 🚀 **Công nghệ**

- **Python**: Ngôn ngữ lập trình chính để phát triển backend.
- **Django**: Framework chính để xây dựng ứng dụng.
- **Django REST Framework (DRF)**: Được sử dụng để phát triển API RESTful.
- **OAuth2**: Tích hợp để xác thực người dùng.
- **MySQL**: Hệ quản trị cơ sở dữ liệu.
- **PythonAnywhere**: Nền tảng triển khai ứng dụng.

---

## 📋 **Yêu cầu cài đặt**

Trước khi bắt đầu, hãy đảm bảo rằng bạn đã cài đặt:

- **Python 3.8+**
- **Pip** (Python package manager)
- **MySQL** (CSDL server)
- **Virtualenv** (khuyến khích sử dụng môi trường ảo)

---

## ⚙️ **Cách cài đặt và chạy dự án**

1. **Clone repository**

   ```bash
   git clone https://github.com/anhtuan05/BE_AppCarPark/
   cd BE_AppCarPark
   
2. **Tạo môi trường ảo và cài đặt các phụ thuộc**
    - python -m venv venv
    - venv\Scripts\activate           # Trên Windows
    - pip install -r requirements.txt
   
3. **Cấu hình cơ sở dữ liệu**
   - Tạo một cơ sở dữ liệu MySQL mới, ví dụ: carpark_db.
   - Cập nhật thông tin cấu hình trong file settings.py:
     ```bash
     DATABASES = {
      'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'carpark_db',
        'USER': '<your_mysql_user>',
        'PASSWORD': '<your_mysql_password>',
        'HOST': 'localhost',
      }
    }
   
   - Tạo bảng trong cơ sở dữ liệu:
       - python manage.py makemigrations
       - python manage.py migrate
   - Tạo siêu người dùng (superuser):
       - python manage.py createsuperuser
   - Chạy server
       - python manage.py runserver

4. **🌐 Triển khai lên PythonAnywhere**
    - Đăng ký tài khoản tại PythonAnywhere.
    - Tạo một ứng dụng Django mới.
    - Upload mã nguồn dự án lên PythonAnywhere.
    - Cấu hình cơ sở dữ liệu MySQL trên PythonAnywhere.
    - Cấu hình các biến môi trường và kết nối tĩnh (static/media).

5. **📂 Cấu trúc dự án**
   ```bash
   - GreenCarPark/carpark/
     - ├── carpark/
     - │   ├── settings.py         # Cấu hình dự án
     - │   ├── urls.py             # Định tuyến toàn dự án
     - ├── greencarpark/           # Ứng dụng chính
     - │   ├── models.py           # Định nghĩa model
     - │   ├── views.py            # Định nghĩa logic API
     - │   ├── serializers.py      # Serializers cho DRF
     - │   ├── admin.py            # adminsite
     - │   ├── momo_payment.py     # Tích hợp thanh toán bằng MoMo
     - │   ├── urls.py             # Định tuyến ứng dụng
     - ├── requirements.txt        # Các thư viện cần thiết
     - ├── manage.py               # Lệnh quản lý Django

6. **📚 Tài liệu API**
   - URL: https://anhtuan05.pythonanywhere.com/swagger/
   
7. **🎨 Frontend**
   - Github Frontend URL: https://github.com/anhtuan05/AppCarPark
   - Frontend được phát triển bằng ReactJS, tương tác với API qua các endpoint được cung cấp.
    
8. **📞 Liên hệ**
   - Nguyễn Anh Tuấn
   - Email: nguyenanhtuan050302@gmail.com
   - Github: https://github.com/anhtuan05

