🚀 OrderFlow

Multi-tenant B2B Order Management System

🎥 Video Demo: https://youtu.be/xvPRFuExpm0

⸻

🧠 Overview

OrderFlow is a multi-tenant B2B order management system designed to help companies manage product catalogs, clients, and purchase orders within a secure, centralized platform.

Built using Flask and SQLite, the system simulates a real-world SaaS product where multiple companies operate independently with strict data isolation.

Each company maintains its own dataset (users, products, orders), enforced via company-level scoping across all queries.

⸻

✨ Key Features

* 🔐 Role-based authentication (Admin & Client)
* 🏢 Multi-tenant architecture with strict data isolation
* 📦 Product catalog management
* 🧾 Order lifecycle: Pending → Approved → Fulfilled / Rejected
* 📊 Client dashboard with analytics and order tracking
* 📢 Bulletin system for announcements
* 💰 Financial accuracy using integer-based currency (paise)
* 🛡️ Server-side validation to prevent tampering

⸻

🧩 Tech Stack

<p align="left">
  <img src="https://skillicons.dev/icons?i=python,flask,sqlite,html,css" />
</p>

* Backend: Flask (Python)
* Database: SQLite
* Frontend: HTML, CSS (Jinja Templates)
* Security: Werkzeug (Password Hashing), Flask-Session

⸻

🏗️ Architecture Highlights

* Multi-Tenancy:
    All data is scoped using company_id to ensure complete isolation.
* RBAC (Role-Based Access Control):
    Custom decorators (admin_required, client_required) enforce permissions.
* Order State Machine:
    Controlled transitions:
  pending → approved → fulfilled
       ↘ rejected
  * Server-Side Validation:
    Prices and quantities are always validated on the server to prevent manipulation.

⸻

🔐 Security Features

* Password hashing using Werkzeug
* Server-side session management (Flask-Session)
* Company-level data isolation
* Protected routes using decorators
* Cache-control headers to prevent sensitive data caching

⸻

📁 Project Structure

orderflow/
│
├── app.py              # Main application logic
├── helpers.py          # Utility functions & decorators
├── requirements.txt    # Dependencies
├── README.md
│
├── templates/          # HTML templates (UI)
├── static/             # CSS, images, assets
⸻

⚙️ How to Run Locally

1. Clone the repository
```bash
git clone https://github.com/yourusername/orderflow.git
cd orderflow
```

2. Create virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate   # Mac/Linux
```

3. Install dependencies
```bash
pip install -r requirements.txt
```

4. Run the application
```bash
flask run
```

5. Open in browser
```bash
http://127.0.0.1:5000
```
