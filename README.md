OrderFlow

Video Demo: https://youtu.be/xvPRFuExpm0

Description

OrderFlow is a multi-tenant B2B order management system designed to streamline how companies manage their product catalogs, clients, and purchase orders within a centralized platform. The application is built using Flask as the backend framework and SQLite as the database, with a strong focus on secure architecture, data isolation, and practical business workflows.

The core idea behind OrderFlow is to simulate a real-world SaaS product where multiple companies (tenants) can operate independently within the same system without any risk of data leakage. Each company maintains its own isolated dataset, including users, products, and orders, enforced through strict company-level scoping in all database queries.

The system supports two primary user roles: Admin and Client. Admin users manage the system at the company level, including creating products, reviewing orders, managing clients, and publishing announcements. Client users interact with the platform by browsing the product catalog, placing orders, and tracking order history.

⸻

Key Features

OrderFlow includes a complete order lifecycle management system. Orders move through a controlled state machine: Pending → Approved → Fulfilled, with the possibility of rejection. Only administrators can modify order states, ensuring operational control and auditability.

Authentication is securely handled using hashed passwords via Werkzeug, and session management is implemented on the server side using Flask-Session to prevent client-side tampering. Additionally, suspended accounts are blocked at login to maintain administrative control over access.

The application also includes a bulletin system, allowing administrators to publish announcements that appear on client dashboards. This simulates real-world communication between businesses and their clients.

Financial accuracy is maintained by storing all monetary values in integer format (paise), eliminating floating-point precision issues. Conversion to rupees occurs only at the presentation layer.

⸻

File Structure and Responsibilities

The main application logic resides in app.py, which contains all route definitions, business logic, and system architecture implementation. It handles authentication, order processing, product management, and admin controls.

The helpers.py file contains custom decorators such as admin_required and client_required, which enforce role-based access control across routes. It also includes utility functions like currency formatting.

The templates/ directory contains all HTML files rendered by Flask. These templates define the user interface for both admin and client dashboards, including pages such as login, register, catalog, order review, dashboard, and admin panels.

The static/ directory (if included) manages CSS and frontend assets used to style the application.

The database file orderflow.db stores all persistent data, including companies, users, products, orders, order items, bulletins, and leads. The schema is designed to support relational integrity and scalable growth.

⸻

Design Decisions and Architecture

One of the most important design decisions in this project was implementing multi-tenancy using a company_id field across all relevant tables. Instead of creating separate databases per company, this approach ensures scalability while maintaining strict data isolation through query-level filtering.

Another key decision was enforcing role-based access control using decorators. This keeps route logic clean while ensuring that only authorized users can access sensitive operations. It also improves maintainability by centralizing access control logic.

Server-side validation was prioritized over client-side trust. For example, product prices are always re-fetched from the database during order submission rather than relying on user input. This prevents manipulation through browser developer tools and ensures data integrity.

The order lifecycle was intentionally designed as a controlled state machine to reflect real-world business processes. Invalid transitions are restricted, and actions such as approval are tracked using an approved_by field for accountability.

⸻

Security Considerations

Security was a major focus throughout development. Passwords are never stored in plaintext and are securely hashed before storage. Sessions are stored on the server filesystem rather than client cookies to prevent tampering.

All sensitive routes are protected using role-based decorators, and every database query involving user data is scoped by company_id to prevent cross-tenant access.

Additionally, cache-control headers are implemented globally to prevent browsers from caching sensitive pages, reducing the risk of unauthorized data exposure.

⸻

Challenges Faced

One of the main challenges was implementing proper multi-tenant isolation without introducing complexity or performance issues. Ensuring that every query correctly enforced company boundaries required careful design and consistent validation.

Another challenge was maintaining clean separation between admin and client functionality while keeping the codebase organized. This was addressed by structuring routes clearly and using decorators effectively.

Handling financial data correctly was also important. Switching from floating-point to integer-based currency storage required adjustments but significantly improved reliability.

⸻

Future Improvements

OrderFlow can be extended in several ways. Migrating from SQLite to PostgreSQL would improve scalability for production use. Adding REST APIs would enable integration with external systems such as mobile apps or third-party services.

Other potential enhancements include email notifications for order updates, advanced analytics dashboards, role expansion (e.g., sales managers), and improved UI/UX with modern frontend frameworks.

⸻

Conclusion

OrderFlow is a practical, real-world inspired system that demonstrates key software engineering principles, including secure authentication, multi-tenant architecture, role-based access control, and robust data validation. The project reflects an emphasis on building not just functional software, but systems that are scalable, secure, and aligned with real business needs.
