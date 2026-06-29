# JACS Property Management System - Backend

A comprehensive Flask-based backend API for the JACS Property Management System, designed to handle property management, tenant relations, staff coordination, and financial operations.

## 🚀 Features

- **User Management**: Multi-role authentication (Property Manager, Staff, Tenant)
- **Property Management**: Properties, units, occupancy tracking
- **Tenant Management**: Tenant profiles, lease management
- **Staff Management**: Employee profiles, task assignments
- **Financial Management**: Billing, payments, financial reporting
- **Maintenance Management**: Request tracking, task assignments
- **Communication**: Announcements, document management
- **Analytics**: Dashboard metrics, financial reports, occupancy reports

## 🛠️ Technology Stack

- **Framework**: Flask 2.3.3
- **Database**: MySQL with SQLAlchemy ORM
- **Authentication**: JWT (Flask-JWT-Extended)
- **Email**: Flask-Mail for notifications
- **File Handling**: File uploads and document management
- **API**: RESTful API with CORS support

## 📋 Prerequisites

- Python 3.8+
- MySQL 8.0+
- pip (Python package manager)

## 🔧 Installation & Setup

### 1. Clone and Navigate
```bash
cd backend
```

### 2. Create Virtual Environment
```bash
python -m venv venv

# On Windows:
venv\Scripts\activate

# On macOS/Linux:
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Database Setup

#### Create MySQL Database
```sql
CREATE DATABASE jacs_property_management;
CREATE USER 'jacs_user'@'localhost' IDENTIFIED BY 'your_secure_password';
GRANT ALL PRIVILEGES ON jacs_property_management.* TO 'jacs_user'@'localhost';
FLUSH PRIVILEGES;
```

#### Configure Environment Variables
Create a `.env` file in the backend directory and update the following:

```env
# Database Configuration
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=jacs_user
MYSQL_PASSWORD=your_secure_password
MYSQL_DATABASE=jacs_property_management

# Flask Configuration
FLASK_ENV=development
FLASK_DEBUG=True
SECRET_KEY=your-super-secret-key-change-this-in-production

# JWT Configuration
JWT_SECRET_KEY=your-jwt-secret-key-change-this-in-production
JWT_ACCESS_TOKEN_EXPIRES=3600

# Mail Configuration (for password reset)
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=your-email@gmail.com
MAIL_PASSWORD=your-app-password

# Upload Configuration
UPLOAD_FOLDER=uploads
MAX_CONTENT_LENGTH=16777216  # 16MB max file size
```

### 5. Initialize Database
```bash
python init_db.py
```

This will create all database tables and insert sample data.

### 6. Run the Application
```bash
python run.py
```

The API will be available at `http://localhost:5000`

## 🔑 Default Login Credentials

After running the database initialization, you can use these credentials:

**Property Manager:**
- Email: `manager@jacs.com`
- Password: `Manager123!`

**Tenant:**
- Email: `tenant@example.com`
- Password: `Tenant123!`

**Staff:**
- Email: `staff@jacs.com`
- Password: `Staff123!`

## 📚 API Documentation

### Authentication Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/login` | User login |
| POST | `/api/auth/register` | User registration |
| POST | `/api/auth/refresh` | Refresh access token |
| POST | `/api/auth/forgot-password` | Request password reset |
| POST | `/api/auth/reset-password` | Reset password with token |
| POST | `/api/auth/change-password` | Change password (authenticated) |
| GET | `/api/auth/me` | Get current user info |
| POST | `/api/auth/logout` | User logout |

### Dashboard/Analytics Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/analytics/dashboard` | Get role-based dashboard data |
| GET | `/api/analytics/financial-summary` | Financial summary (Manager only) |
| GET | `/api/analytics/occupancy-report` | Occupancy report (Manager only) |

### User Management Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/users/` | Get all users (Manager only) |
| GET | `/api/users/profile` | Get current user profile |
| PUT | `/api/users/profile` | Update user profile |

### Health Check

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | API health check |

## 🏗️ Project Structure

```
backend/
├── app/
│   └── __init__.py          # Flask application factory
├── models/                  # Database models
│   ├── user.py             # User model and authentication
│   ├── property.py         # Property and Unit models
│   ├── tenant.py           # Tenant and lease models
│   ├── staff.py            # Staff management models
│   ├── bill.py             # Billing and payment models
│   ├── request.py          # Maintenance request models
│   ├── announcement.py     # Announcement models
│   ├── document.py         # Document management models
│   ├── task.py             # Task management models
│   └── feedback.py         # Feedback models
├── routes/                 # API route handlers
│   ├── auth_routes.py      # Authentication routes
│   ├── user_routes.py      # User management routes
│   ├── analytics_routes.py # Dashboard and analytics routes
│   └── ...                # Other route modules
├── services/               # Business logic services
│   └── email_service.py    # Email notification service
├── config/                 # Configuration files
│   └── config.py          # Application configuration
├── migrations/            # Database migrations
├── uploads/              # File upload directory
├── tests/               # Test files
├── requirements.txt     # Python dependencies
├── .env                # Environment variables
├── run.py              # Application entry point
├── init_db.py          # Database initialization script
└── README.md           # This file
```

## 🔒 Security Features

- **JWT Authentication**: Secure token-based authentication
- **Password Security**: Bcrypt password hashing
- **Role-based Access Control**: Multi-role authorization
- **Input Validation**: Request data validation
- **CORS Configuration**: Cross-origin resource sharing setup
- **SQL Injection Prevention**: SQLAlchemy ORM protection

## 🚀 Development

### Running in Development Mode
```bash
export FLASK_ENV=development
export FLASK_DEBUG=True
python run.py
```

### Database Migrations (Future Enhancement)
```bash
flask db init
flask db migrate -m "Initial migration"
flask db upgrade
```

## 🔧 Configuration

The application uses environment variables for configuration. Key settings include:

- **Database**: MySQL connection settings
- **JWT**: Token secrets and expiration times
- **Email**: SMTP settings for notifications
- **File Uploads**: Upload directory and size limits
- **Security**: Secret keys and security settings

## 📝 API Response Format

All API responses follow a consistent format:

**Success Response:**
```json
{
    "data": { ... },
    "message": "Operation successful"
}
```

**Error Response:**
```json
{
    "error": "Error description",
    "status_code": 400
}
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆘 Support

For support and questions:
- Create an issue in the repository
- Contact the development team
- Check the documentation for common solutions

## 🎯 Future Enhancements

- [ ] Real-time notifications with WebSocket
- [ ] Advanced reporting and analytics
- [ ] Mobile app API endpoints
- [ ] Integration with payment gateways
- [ ] Automated backup and recovery
- [ ] Multi-language support
- [ ] Advanced search and filtering
- [ ] Audit logging and compliance features