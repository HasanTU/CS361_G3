# Project Context: e-Mailbox System (Department Document Routing & Tracking)

---

## 1. Executive Summary & Problem Statement

* **Current Situation:** สาขาวิชามีเอกสารไหลเข้าจากหลายช่องทาง (หนังสือราชการภายนอก, เอกสารจากคณะ, บันทึกข้อความภายใน) กระบวนการส่งต่อในปัจจุบันยังเป็นแบบ Manual หรือผ่านแชตกลุ่ม ส่งผลให้เกิดปัญหา:
  * เอกสารตกหล่น สูญหาย หรือส่งต่อล่าช้า
  * ไม่สามารถระบุได้ว่าตอนนี้เอกสารดำเนินการอยู่ที่ใคร
  * ขาดระบบ Audit Trail สำหรับตรวจสอบย้อนหลังว่าเอกสารถึงขั้นตอนใด
* **Project Objective:** ออกแบบและพัฒนาระบบเว็บแอปพลิเคชัน **e-Mailbox** สำหรับรับ ลงทะเบียน จัดเก็บ กระจายเอกสาร และติดตามสถานะการดำเนินงานของเอกสารอิเล็กทรอนิกส์ในระดับสาขาวิชาอย่างเป็นระบบและตรวจสอบย้อนหลังได้

---

## 2. Project Scope & Phasing

### Version 3: Core Distribution (Current Target - MVP)

**สนใจเรื่องการทำ Authantication**

* **Admin Role (ผู้ลงทะเบียน/ธุรการ):**
  * บันทึกข้อมูลและลงทะเบียนเอกสารเข้า
  * อัปโหลดไฟล์เอกสาร (PDF, Image) ขึ้นระบบ
* **User Role (อาจารย์ / เจ้าหน้าที่ผู้รับ):**
  * เปิดอ่าน ดูรายละเอียดได้

---

## 3. User Roles & Permission Matrix 

| Feature / Capability | Admin (ธุรการ/แอดมิน) | User (อาจารย์/เจ้าหน้าที่) |
| :--- | :---: | :---: |
| Authentication (Login/Logout) | ✅ | ✅ |
| Upload Document | ✅ | ❌ |
| Assign Recipients (กำหนดผู้รับ) | ✅ | ❌ |
| View All Documents (ภาพรวมทั้งสาขา) | ✅ | ❌ |
| View Personal Mailbox (เฉพาะเอกสารตัวเอง) | ✅ | ✅ |
| View / Download Assigned File | ✅ | ✅ *(เฉพาะที่ได้รับมอบหมาย)* |


---

## 4. Technical Stack Specifications

* **Backend:** Python 3.11+ (Flask)
* **Frontend:** HTML5, CSS3, JavaScript
* **Database:** NoSQL (MongoDB) ใช้ `pymongo` 
* **File Storage:** Local Volume (จัดเก็บไฟล์ไว้ในโฟลเดอร์ `/uploads` ภายใน Container และ Mount ไปยัง Host)
* **Authentication:** Session-based หรือ JWT Authentication พร้อม Role-Based Access Control (RBAC: `ADMIN`, `USER`)
* **Deployment & Environment:** Docker & Docker Compose

##  5. Database schema 

Table DOCUMENT_TYPES {
  _id ObjectId [pk]
  name string [note: 'ชื่อประเภทเอกสาร']
  Note: 'ประเภทเอกสารที่ถูกส่ง'
}

Table DOCUMENTS {
  _id ObjectId [pk]
  document_type_id ObjectId [ref: > DOCUMENT_TYPES._id]
  document_date       # YYYY-MM-DD
  sender
  receiver
  title string
  file_path string
  file_name string
  file_type string
  file_size int
  uploaded_at datetime
  uploaded_by string
}

## 6. Cloud Architecture 
- Lamda: Backend
- Storege S3: Frontend
- Mongo Atlas: Database NoSQL 

## 7. Folder Structure

The system is organized into two main sections: `backend` and `frontend`. Docker and shared project configuration remain in the root folder.

```text
G3-projetc/
├── Dockerfile                 # Builds the application container
├── docker-compose.yml         # Runs the backend and MongoDB services
├── requirements.txt           # Python dependencies
├── README.md                  # Project documentation
├── project-context.md         # Project requirements and architecture
├── .gitignore                 # Files excluded from Git
├── .env                       # Environment-specific configuration
├── uploads/                   # Uploaded document files
│
├── backend/                   # Flask backend section
│   ├── app.py                 # Flask application entry point
│   ├── config.py              # Application configuration
│   ├── routes/                # API and page routes
│   ├── models/                # MongoDB data operations
│   └── services/              # Application business logic
│
└── frontend/                 # Frontend section
    ├── html/                  # HTML page files
    ├── css/                   # CSS stylesheet files
    └── js/                    # JavaScript files
```

## 8. API Design
  POST /api/v1/documents -เจ้าหน้าที่บันทึกข้อมูลเอกสาร (Metadata) และอัปโหลดไฟล์
  GET  /api/v1/documents -อาจารย์ดึงรายการเอกสารทั้งหมดมาแสดงในตาราง
  GET  /api/v1/documents/<id> -ดึงข้อมูลรายละเอียดของเอกสารเพื่อแสดงผลในหน้าต่าง Preview
  GET  /api/v1/documents/<id>/file -Returns the actual uploaded file so the frontend can view or download it.
  DELETE /api/v1/documents/<id> for delete
  PATCH /api/v1/documents/<id> for edit document

