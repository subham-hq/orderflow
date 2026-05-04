"""
Project Name: OrderFlow

Author: Subham B.
Course: CS50x Final Project

Description:
OrderFlow is a multi-tenant B2B order management system built using Flask and SQLite.
It allows companies to manage product catalogs, clients, and purchase orders.

The system supports:
- Role-based authentication (Admin & Client)
- Secure password hashing
- Company-isolated data architecture
- Order lifecycle management (Pending → Approved → Fulfilled)
- Product catalog management
- Bulletin/announcement system
- Revenue and dashboard analytics

Security Features:
- Server-side session management
- Password hashing using Werkzeug
- Company-level access isolation
- Order validation to prevent tampering
- Role-based decorators for route protection
"""

"""
AI Assistance Disclosure:

During the development of this project, AI-based tools such as ChatGPT, Gemini & Cursor were
occasionally used as productivity aids. These tools were used for guidance on
debugging, refining code structure, improving documentation, preparing clearer
inline comments, and suggesting UI/CSS refinements.

AI assistance was also used to help organize and polish code comments so that
the architecture, logic, and design decisions are clearly explained throughout
the project.

All core architectural decisions, system design, database schema, business
logic, and the overall implementation of the OrderFlow application were
designed and written by the author.

AI tools were used strictly as helpers for clarification, explanation, and
minor refinement, and not as substitutes for understanding or completing the
course material independently.
"""

import os
from datetime import datetime
import random
from cs50 import SQL
from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_session import Session
from werkzeug.security import check_password_hash, generate_password_hash

from helpers import admin_required, client_required, inr

# ==================================================
# SYSTEM ARCHITECTURE OVERVIEW
# ==================================================
#
# OrderFlow follows a modular, role-based, multi-tenant architecture.
#
# 1. Multi-Tenant Data Isolation
# --------------------------------------------------
# - Each organization is uniquely identified by company_id.
# - All queries involving orders, products, clients, and bulletins
#   are scoped by company_id to ensure strict data isolation.
# - This prevents cross-company data leakage.
#
# 2. Role-Based Access Control (RBAC)
# --------------------------------------------------
# - Users are assigned roles: "admin" or "client".
# - Custom decorators (@admin_required, @client_required)
#   enforce route-level access control.
# - Sensitive operations are restricted to appropriate roles.
#
# 3. Secure Authentication & Credential Storage
# --------------------------------------------------
# - Passwords are hashed using Werkzeug before database storage.
# - Plaintext passwords are never stored.
# - Session-based authentication is used after successful login.
# - Suspended accounts (is_active = 0) are blocked at login.
#
# 4. Server-Side Session Management
# --------------------------------------------------
# - Sessions are stored in the filesystem (not client cookies).
# - Prevents tampering and enhances security.
# - Session is cleared on login and logout to prevent session fixation.
#
# 5. Order Lifecycle Management
# --------------------------------------------------
# Orders follow a controlled state machine:
#
#   pending → approved → fulfilled
#          ↘ rejected
#
# - Only admins can transition order states.
# - State validation ensures invalid transitions are rejected.
# - approved_by field maintains audit traceability.
#
# 6. Financial Data Integrity
# --------------------------------------------------
# - All currency values are stored as integers (paise).
# - Prevents floating-point rounding errors.
# - Conversion to decimal format happens only during rendering.
#
# 7. Server-Side Validation & Tamper Protection
# --------------------------------------------------
# - Product prices are always re-fetched from the database
#   during order submission.
# - Quantity values are revalidated on the server.
# - Prevents client-side manipulation via browser dev tools.
#
# 8. Data Integrity Constraints
# --------------------------------------------------
# - SKU uniqueness enforced at application level.
# - Client deletion is blocked if financial records exist.
# - Orders are always verified to belong to the requesting user.
#
# 9. Defensive Programming Practices
# --------------------------------------------------
# - All critical queries validate row counts.
# - Unauthorized access redirects safely.
# - Flash messaging used for user feedback.
# - Cache-control headers prevent sensitive data caching.
#
# 10. Scalability Considerations
# --------------------------------------------------
# - Architecture supports horizontal company growth.
# - Database schema supports indexing for large datasets.
# - Order numbers are generated in a human-readable format.
#
# ==================================================


# ==================================================
# DATABASE DESIGN OVERVIEW
# ==================================================
#
# Core Tables:
#
# companies
#   - Stores registered organizations (tenants).
#
# users
#   - Stores admin and client accounts.
#   - Linked to companies via company_id.
#
# products
#   - Product catalog belonging to a company.
#
# orders
#   - Master order records placed by clients.
#
# order_items
#   - Individual line items inside an order.
#
# bulletins
#   - Company announcements displayed on client dashboards.
#
# leads
#   - Marketing leads captured from landing page.
#
# Relationships:
#
# companies (1) ──── (many) users
# companies (1) ──── (many) products
# companies (1) ──── (many) orders
#
# orders (1) ──── (many) order_items
#
# users (clients) ──── place ──── orders
#
# ==================================================

# ==================================================
# FLASK APPLICATION INITIALIZATION
# ==================================================
# Create the main Flask application instance.
app = Flask(__name__)

# Custom filter
app.jinja_env.filters["inr"] = inr

# Configure session to use file systems (instead of signed cookies)
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Configure CS50 Library to use SQLite database
db = SQL("sqlite:///orderflow.db")

# --------------------------------------------------
# SECURITY MIDDLEWARE
# Disable HTTP caching to prevent sensitive data exposure
# --------------------------------------------------


@app.after_request
def after_request(response):
    """Ensure responses aren't cached"""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Expires"] = 0
    response.headers["Pragma"] = "no-cache"
    return response

# ==================================================
# PUBLIC & AUTHENTICATION ROUTES
# Handles landing page, lead capture, login, and registration
# ==================================================


@app.route("/", methods=["GET", "POST"])
def index():
    """
    Landing page controller.

    Handles:
    - Public marketing page rendering
    - Lead capture form submissions
    - Optional personalization if user session exists
    """

    # Attempt session-based personalization
    # If a user is already logged in, retrieve their first name
    # to dynamically adapt the landing experience.
    username = None
    if session.get("user_id"):
        user = db.execute(
            "SELECT first_name FROM users WHERE id = ?",
            session["user_id"]
        )
        if len(user) > 0:
            username = user[0]["first_name"]

    # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":

        # Validate required input fields to prevent empty submissions
        # Ensure first name was submitted.
        if not request.form.get("first_name"):
            flash("First name is required.")
            # Redirect user to home page
            return redirect("/")

        # Ensure company name was submitted.
        if not request.form.get("company_name"):
            flash("Company name is required.")
            # Redirect user to home page
            return redirect("/")

        # Ensure email was submitted.
        elif not request.form.get("email"):
            flash("Email is required")
            # Redirect user to home page
            return redirect("/")

        # If validation passes, safely persist the lead
        else:
            first_name = request.form.get("first_name")
            company_name = request.form.get("company_name")
            email = request.form.get("email")

            try:
                # Insert customer data into leads table
                db.execute(
                    "INSERT INTO leads (name, company_name, email) VALUES (?, ?, ?)",
                    first_name, company_name, email
                )
                # Provide user feedback on successful capture
                flash("Your request has been received. Our team will contact you shortly.", "success")

            except:
                # Prevent application crash while avoiding sensitive error leakage
                flash("We couldn't process your request. Please try again.", "danger")

            # Redirect user to home page
            return redirect("/")

    # User reached route via GET (as by clicking a link or via redirect)
    else:
        return render_template("landing.html", username=username)


@app.route("/privacy_policy")
def privacy_policy():
    """Display the Privacy Policy page."""
    return render_template("privacy_policy.html")


@app.route("/terms_of_service")
def terms_of_service():
    """Display the Terms of Service page."""
    return render_template("terms_of_service.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    """
    Authentication controller.

    Responsibilities:
    - Validate user credentials
    - Establish secure session
    - Enforce account status checks
    - Redirect based on role (Admin / Client)
    """

    # Clear any existing session data.
    session.clear()

    # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":

        # Ensure required credentials are provided.
        # Ensure email was submitted.
        if not request.form.get("email"):
            flash("Email is required.", "danger")
            # Redirect user to login page
            return render_template("login.html")

        # Ensure password was submitted.
        elif not request.form.get("password"):
            flash("Password is required.", "danger")
            # Redirect user to login page
            return render_template("login.html")

        # Fetch user record by email.
        # Email is treated as the unique authentication identifier.
        rows = db.execute(
            "SELECT * FROM users WHERE email = ?",
            request.form.get("email")
        )

        # Ensure exactly one account exists and verify password hash.
        if len(rows) != 1 or not check_password_hash(
            rows[0]["hash"], request.form.get("password")
        ):
            flash("Invalid credentials. Please try again.", "danger")
            # Redirect user to login page
            return render_template("login.html")

        # Block suspended users from accessing the system.
        if rows[0]["is_active"] == 0:
            flash("This account has been suspended. Please contact the administrator.", "danger")
            return render_template("login.html")

        # Store identity data in session.
        # Used for authorization and role-based access control.
        session["user_id"] = rows[0]["id"]
        session["role"] = rows[0]["role"]

        # Route users to their appropriate control panel.
        if session["role"] == "admin":
            return redirect("/admin")
        else:
            return redirect("/dashboard")

    # User reached route via GET (as by clicking a link or via redirect)
    else:
        return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    """
    Registration controller.

    Responsibilities:
    - Create a new organization (company)
    - Provision the initial admin account
    - Enforce uniqueness constraints
    - Bootstrap authenticated session after successful registration
    """

    # Clear any existing session data.
    session.clear()

    # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":

        # Ensure required credentials are provided.
        # Ensure first name was submitted.
        if not request.form.get("first_name"):
            flash("First name is required.", "danger")
            # Redirect user to register page
            return render_template("register.html")

        # Ensure company name was submitted.
        elif not request.form.get("company_name"):
            flash("Company name is required.", "danger")
            # Redirect user to register page
            return render_template("register.html")

        # Ensure email was submitted.
        elif not request.form.get("email"):
            flash("Email is required.", "danger")
            # Redirect user to register page
            return redirect("/register")

        # Ensure password was submitted.
        elif not request.form.get("password"):
            flash("Password is required.", "danger")
            # Redirect user to register page
            return render_template("register.html")

        # Ensure confirmation was submitted.
        elif not request.form.get("confirmation"):
            flash("Please confirm your password.", "danger")
            # Redirect user to register page
            return render_template("register.html")

        # Ensure password and confirmation are same.
        elif request.form.get("password") != request.form.get("confirmation"):
            flash("Password and confirmation must match.", "danger")
            # Redirect user to register page
            return render_template("register.html")

        else:
            # Ensure email is globally unique across users
            # Prevent duplicate identities and tenant collisions.
            rows = db.execute(
                "SELECT id FROM users WHERE email = ?",
                request.form.get("email")
            )
            if len(rows) != 0:
                flash("This email is already registered. Please log in instead.", "warning")
                # Redirect user to login page
                return render_template("login.html")

            # Ensure company name is unique to avoid tenant duplication
            company_rows = db.execute(
                "SELECT id FROM companies WHERE name = ?",
                request.form.get("company_name")
            )

            if len(company_rows) != 0:
                flash(
                    "A company with this name is already registered."
                    "If this is your company, please contact your administrator.",
                    "warning"
                )
                # Redirect user to registration page
                return render_template("register.html")

            # Create the organization first, then bind the admin user to it.
            first_name = request.form.get("first_name")
            last_name = request.form.get("last_name")
            company_name = request.form.get("company_name")
            email = request.form.get("email")

            # Securely hash password before storage
            hashed_password = generate_password_hash(request.form.get("password"))

            # Initial role assignment (system bootstrap admin)
            role = "admin"

            try:
                # Step 1: Create new tenant record
                new_company_id = db.execute(
                    "INSERT INTO companies (name) VALUES (?)",
                    company_name
                )
                # Step 2: Create admin user associated with tenant
                db.execute(
                    "INSERT INTO users (first_name, last_name, company_id, email, hash, role) VALUES (?, ?, ?, ?, ?, ?)",
                    first_name, last_name, new_company_id, email, hashed_password, role
                )

            except:
                # Defensive handling to prevent partial system state exposure
                flash("We couldn't create your account at the moment. Please try again.", "danger")
                return render_template("register.html")

            # Automatically authenticate newly created admin.
            rows = db.execute(
                "SELECT * FROM users WHERE email = ?",
                email
            )

            # Store identity data in session.
            # Used for authorization and role-based access control.
            session["user_id"] = rows[0]["id"]
            session["role"] = rows[0]["role"]

            # Redirect user to admin dashboard
            flash("Your account has been created successfully.", "success")
            return redirect("/admin")

    # User reached route via GET (as by clicking a link or via redirect)
    else:
        return render_template("register.html")


# ==================================================
# CLIENT PANEL
# All routes accessible only to authenticated client users
# ==================================================

@app.route("/dashboard")
@client_required
def dashboard():
    """
    Client dashboard controller.

    Responsibilities:
    - Display personalized account overview
    - Show recent activity and order statistics
    - Surface active company announcements
    """

    # Retrieve the logged-in user's basic details.
    user_data = db.execute(
        "SELECT first_name, last_name, client_company_name, company_id FROM users WHERE id = ?",
        session["user_id"]
    )

    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    username = user_data[0]["first_name"]
    first_name = user_data[0]["first_name"]
    company_name = user_data[0]["client_company_name"]
    company_id = user_data[0]["company_id"]
    user_id = session["user_id"]

    # Retrieve the most recent active and unexpired
    # announcement for this company.
    bulletin_query = db.execute(
        "SELECT title, content FROM bulletins WHERE company_id = ? AND is_active = 1 AND (expires_at IS NULL OR datetime(expires_at) >= datetime('now', 'localtime')) ORDER BY created_at DESC LIMIT 1",
        company_id
    )

    # If a valid bulletin exists, store it.
    # Otherwise, set it to None.
    bulletin = bulletin_query[0] if len(bulletin_query) > 0 else None

    # Sum approved and fulfilled orders only.
    approved_orders = db.execute(
        "SELECT SUM(total_amount) AS total FROM orders WHERE status IN (?, ?) AND company_id = ? AND user_id = ?",
        "approved", "fulfilled", company_id, user_id
    )

    # Handle case where user has no completed orders
    if approved_orders[0]["total"] is None:
        lifetime_spend = 0.0
    else:
        # Convert stored paise (integer) to rupees for display
        lifetime_spend = approved_orders[0]["total"] / 100.0

    # Count currently pending orders
    pending_orders = db.execute(
        "SELECT COUNT(*) AS total FROM orders WHERE status = 'pending' AND company_id = ? AND user_id = ?",
        company_id, user_id
    )
    pending_count = pending_orders[0]["total"]

    # Count total lifetime orders
    lifetime_orders = db.execute(
        "SELECT COUNT(*) AS total FROM orders WHERE company_id = ? AND user_id = ?",
        company_id, user_id
    )
    total_orders = lifetime_orders[0]["total"]

    # Retrieve orders sorted by most recent first.
    recent_orders = db.execute(
        "SELECT order_number, created_at, total_amount, status FROM orders WHERE company_id = ? AND user_id = ? ORDER BY created_at DESC",
        company_id, user_id
    )

    # Convert stored paise values into rupees for template rendering
    for order in recent_orders:
        order["formatted_total_amount"] = (order["total_amount"] / 100)

    return render_template("dashboard.html", username=username, first_name=first_name, company_name=company_name, bulletin=bulletin, lifetime_spend=lifetime_spend, pending_count=pending_count, total_orders=total_orders, recent_orders=recent_orders)


@app.route("/catalog", methods=["GET", "POST"])
@client_required
def catalog():
    """
    Product catalog controller.

    Responsibilities:
    - Display active products available to the logged-in client
    - Ensure data is scoped to the client’s company
    """

    # Retrieve the logged-in user's company information.
    user_data = db.execute(
        "SELECT first_name, last_name, client_company_name, company_id FROM users WHERE id = ?",
        session["user_id"]
    )

    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    username = user_data[0]["first_name"]
    company_id = user_data[0]["company_id"]

    # Fetch only active products belonging to this company.
    products = db.execute(
        "SELECT id, sku, name, description, base_price, category FROM products WHERE is_active = 1 AND company_id = ?",
        company_id
    )

    # Convert stored paise (integer) into rupees for display
    # Currency formatting is handled before passing to template.
    for product in products:
        product["base_price_formatted"] = (product["base_price"] / 100)

    return render_template("catalog.html", username=username, products=products)


@app.route("/order", methods=["POST"])
@client_required
def review_order():
    """
    Order review controller.

    Responsibilities:
    - Process selected catalog items
    - Validate quantities
    - Calculate totals before final confirmation
    """

    # Retrieve the logged-in user's company information.
    user_data = db.execute(
        "SELECT first_name, last_name, client_company_name, company_id FROM users WHERE id = ?",
        session["user_id"]
    )

    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    username = user_data[0]["first_name"]
    company_id = user_data[0]["company_id"]

    selected_items = []
    grand_total = 0

    # Read submitted quantities from the catalog form.
    for key, value in request.form.items():

        # Only process positive numeric quantities.
        if key.startswith("qty_") and value.isdigit() and int(value) > 0:
            product_id = int(key.split("_")[1])
            quantity = int(value)

            # Fetch product securely, scoped to company
            # Prevents accessing another company's catalog.
            product = db.execute("SELECT id, name, sku, base_price FROM products WHERE id = ? AND company_id = ? AND is_active = 1",
                                 product_id, company_id
                                 )

            if len(product) == 1:
                item = product[0]
                item["quantity"] = quantity

                # Calculate line total (stored in paise)
                line_total = item["base_price"] * quantity

                # Convert values to rupees for display only
                item["base_price_formatted"] = item["base_price"] / 100.0
                item["line_total_formatted"] = line_total / 100.0

                selected_items.append(item)
                grand_total += line_total

    # Prevent empty cart submission
    if not selected_items:
        flash("Please select at least one item to place an order.", "warning")
        return redirect("/catalog")

    # Convert final total to rupees for template rendering
    grand_total_formatted = grand_total / 100.0

    return render_template("client_order_review.html", username=username, items=selected_items, grand_total=grand_total_formatted)


@app.route("/order/submit", methods=["POST"])
@client_required
def submit_order():
    """
    Order submission controller.

    Responsibilities:
    - Re-validate cart data server-side
    - Create order record
    - Store order line items
    """

    user_id = session["user_id"]

    # Retrieve company context for data isolation
    user_data = db.execute("SELECT company_id FROM users WHERE id = ?", user_id)
    company_id = user_data[0]["company_id"]

    notes = request.form.get("notes", "")
    selected_items = []
    grand_total_paise = 0

    # Re-check quantities and prices to prevent client-side tampering.
    for key, value in request.form.items():
        if key.startswith("qty_") and value.isdigit() and int(value) > 0:
            product_id = int(key.split("_")[1])
            quantity = int(value)

            # Always re-fetch price from database never trust values coming from the client.
            product = db.execute(
                "SELECT base_price FROM products WHERE id = ? AND company_id = ? AND is_active = 1",
                product_id, company_id
            )

            if len(product) == 1:
                price = product[0]["base_price"]

                selected_items.append({
                    "product_id": product_id,
                    "quantity": quantity,
                    "unit_price": price
                })
                grand_total_paise += (price * quantity)

    # If cart is empty or invalid, cancel submission
    if not selected_items:
        flash("Order validation failed. Please try again.", "danger")
        return redirect("/catalog")

    # Generate a human-readable order reference number.
    date_str = datetime.now().strftime('%Y%m%d')
    while True:
        random_suffix = random.randint(1000, 9999)
        order_number = f"ORD-{date_str}-{random_suffix}"

        existing = db.execute(
            "SELECT id FROM orders WHERE order_number = ?", order_number
        )

        if not existing:
            break

    # Format: ORD-YYYYMMDD-RANDOM
    # Example: ORD-20260304-4821
    # This allows easy tracking by administrators and clients.

    # Insert master order record (default status: pending)
    db.execute("INSERT INTO orders (company_id, user_id, order_number, total_amount, status, notes) VALUES (?, ?, ?, ?, 'pending', ?)",
               company_id, user_id, order_number, grand_total_paise, notes
               )

    # Retrieve newly created order ID
    new_order = db.execute("SELECT id FROM orders WHERE order_number = ?", order_number)
    order_id = new_order[0]["id"]

    # Insert the individual line items
    for item in selected_items:
        db.execute(
            "INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (?, ?, ?, ?)",
            order_id, item["product_id"], item["quantity"], item["unit_price"]
        )

    # Redirect securely to the success page!
    return redirect(f"/order/success/{order_id}")


@app.route("/order/success/<int:order_id>")
@client_required
def order_success(order_id):
    """
    Order confirmation controller.

    Responsibilities:
    - Display final receipt after successful order submission
    - Ensure the order belongs to the logged-in client
    """

    user_id = session["user_id"]

    # Retrieve the logged-in user's basic details.
    user_data = db.execute(
        "SELECT first_name, last_name, client_company_name, company_id FROM users WHERE id = ?",
        user_id
    )

    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    username = user_data[0]["first_name"]

    # Ensure the requested order belongs to this user.
    # Prevents access to other users' receipts.
    order = db.execute(
        "SELECT order_number, total_amount, created_at FROM orders WHERE id = ? AND user_id = ?",
        order_id, user_id
    )

    # If no matching record is found, deny access
    if len(order) != 1:
        flash("Order not found.", "danger")
        return redirect("/dashboard")

    # Convert stored paise (integer) to rupees for display
    order_data = order[0]
    order_data["total_formatted"] = order_data["total_amount"] / 100.0

    return render_template("client_order_success.html", order=order_data, username=username)


@app.route("/orders", methods=["GET", "POST"])
@client_required
def orders():
    """
    Order history controller.

    Responsibilities:
    - Display all past orders for the logged-in client
    - Show order status and summary information
    """

    # Retrieve the logged-in user's basic details.
    user_data = db.execute(
        "SELECT first_name, last_name, client_company_name, company_id FROM users WHERE id = ?",
        session["user_id"]
    )

    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    username = user_data[0]["first_name"]
    company_id = user_data[0]["company_id"]
    user_id = session["user_id"]

    # Fetch only orders belonging to this user and company.
    # Results are sorted by most recent first.
    orders = db.execute(
        "SELECT id, order_number, created_at, total_amount, status FROM orders WHERE company_id = ? AND user_id = ? ORDER BY created_at DESC",
        company_id, user_id
    )

    # Convert stored paise values into rupees for display
    for order in orders:
        order["formatted_total_amount"] = (order["total_amount"] / 100)

    return render_template("orders.html", username=username, orders=orders)


@app.route("/order/<int:order_id>")
@client_required
def order_details(order_id):
    """
    Order details controller.

    Responsibilities:
    - Display full invoice details for a specific order
    - Ensure the order belongs to the logged-in client
    """

    # Retrieve the logged-in user's basic details.
    user_data = db.execute(
        "SELECT first_name, last_name, client_company_name, company_id FROM users WHERE id = ?",
        session["user_id"]
    )

    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    username = user_data[0]["first_name"]
    user_id = session["user_id"]

    # Ensure the requested order belongs to this user.
    order_query = db.execute(
        "SELECT id, order_number, created_at, status, notes, total_amount FROM orders WHERE id = ? AND user_id = ?",
        order_id, user_id
    )

    # If order does not exist or is not owned by user, deny access
    if len(order_query) != 1:
        flash("Order not found or access denied.", "danger")
        return redirect("/orders")

    order = order_query[0]

    # Convert total from paise to rupees for display
    order["total_formatted"] = order["total_amount"] / 100.0

    # Fetch all products included in this order.
    items = db.execute(
        "SELECT oi.quantity, oi.unit_price, p.name, p.sku FROM order_items oi JOIN products p ON oi.product_id = p.id WHERE oi.order_id = ?",
        order_id
    )

    # Format pricing values for template rendering
    for item in items:
        item["unit_price_formatted"] = item["unit_price"] / 100.0
        item["line_total_formatted"] = (item["unit_price"] * item["quantity"]) / 100.0

    return render_template("order_details.html", username=username, order=order, items=items)


@app.route("/settings", methods=["GET", "POST"])
@client_required
def settings():
    """
    Account settings controller.

    Responsibilities:
    - Display client profile information
    - Allow viewing of basic account details
    """

    # Retrieve current user's profile information.
    user_data = db.execute(
        "SELECT first_name, last_name, email, client_company_name AS company_name FROM users WHERE id = ?",
        session["user_id"]
    )
    username = user_data[0]["first_name"]
    first_name = user_data[0]["first_name"]
    last_name = user_data[0]["last_name"]
    email = user_data[0]["email"]
    company_name = user_data[0]["company_name"]

    return render_template("settings.html", username=username, first_name=first_name, last_name=last_name, email=email, company_name=company_name)


@app.route("/change_password", methods=["GET", "POST"])
@client_required
def change_password():
    """
    Password update controller.

    Responsibilities:
    - Verify current password
    - Enforce basic password rules
    - Securely update stored credentials
    """

    user_id = session["user_id"]

    # Fetch current user credential data
    user_data = db.execute(
        "SELECT first_name, hash FROM users WHERE id = ?",
        user_id
    )
    # If session is invalid, force re-login
    if not user_data:
        return redirect("/logout")

    username = user_data[0]["first_name"]
    current_hash = user_data[0]["hash"]

    # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":

        old_password = request.form.get("old_password")
        new_password = request.form.get("new_password")
        confirmation = request.form.get("confirmation")

        # Perform basic input validation
        if not all([old_password, new_password, confirmation]):
            flash("All fields are required.", "warning")
            return redirect("/change_password")

        # Verify existing password before allowing update
        if not check_password_hash(current_hash, old_password):
            flash("Current password incorrect.", "danger")
            return redirect("/change_password")

        # Confirm new password constraints
        if new_password != confirmation:
            flash("Passwords do not match.", "danger")
            return redirect("/change_password")

        if len(new_password) < 8:
            flash("New password must be at least 8 characters long.", "warning")
            return redirect("/change_password")

        if old_password == new_password:
            flash("New password cannot be the same as your old one.", "info")
            return redirect("/change_password")

        # Store only hashed password in database.
        new_hash = generate_password_hash(new_password)

        db.execute(
            "UPDATE users SET hash = ? WHERE id = ?",
            new_hash, user_id
        )

        flash("Password updated successfully.", "success")
        return redirect("/settings")

    return render_template("change_password.html", username=username)

# ==================================================
# ADMIN CONTROL PANEL
# Administrative routes for managing orders, clients, products, and system settings
# ==================================================


@app.route("/admin")
@admin_required
def admin_dashboard():
    """
    Admin dashboard controller.

    Responsibilities:
    - Display system overview for administrators
    - Show key business metrics and operational data
    """

    # Retrieve administrator identity and company scope.
    user_data = db.execute(
        "SELECT first_name, company_id FROM users WHERE id = ?",
        session["user_id"]
    )

    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    username = user_data[0]["first_name"]
    company_id = user_data[0]["company_id"]

    # Count orders that are currently awaiting review.
    orders = db.execute(
        "SELECT COUNT(*) AS total FROM orders WHERE status = 'pending' AND company_id = ?",
        company_id
    )
    pending_count = orders[0]["total"]

    # Calculate total value of approved orders.
    revenue = db.execute(
        "SELECT SUM(total_amount) AS total FROM orders WHERE status = 'approved' AND company_id = ?",
        company_id
    )
    total_approved_revenue = revenue[0]["total"]

    # Handle case where no revenue exists yet
    if not total_approved_revenue:
        total_revenue = 0
    else:
        # Convert stored paise to rupees for display
        total_revenue = total_approved_revenue / 100

    # Count active clients belonging to this company.
    clients = db.execute(
        "SELECT COUNT(*) AS total FROM users WHERE is_active = 1 AND role = 'client' AND company_id = ?",
        company_id
    )
    active_clients = clients[0]["total"]

    # Count active products in the catalog.
    products = db.execute(
        "SELECT COUNT(*) AS total FROM products WHERE is_active = 1 AND company_id = ?",
        company_id
    )
    active_products = products[0]["total"]

    # Retrieve detailed information for orders awaiting action.
    pending_orders = db.execute(
        "SELECT orders.id, orders.order_number, users.client_company_name AS client_name, orders.total_amount FROM orders JOIN users ON orders.user_id = users.id WHERE orders.status = 'pending' AND orders.company_id = ? ORDER BY orders.created_at DESC",
        company_id
    )

    # Convert stored paise values to rupees for display
    for order in pending_orders:
        order["formatted_total_amount"] = order["total_amount"] / 100

    # Retrieve currently active announcements for clients.
    active_bulletins = db.execute(
        "SELECT * FROM bulletins WHERE is_active = 1 AND company_id = ?",
        company_id
    )

    return render_template("admin.html", username=username, pending_count=pending_count, total_revenue=total_revenue, active_clients=active_clients, active_products=active_products, pending_orders=pending_orders, active_bulletins=active_bulletins)


@app.route("/admin/orders", methods=["GET", "POST"])
@admin_required
def admin_orders():
    """
    Admin order management controller.

    Responsibilities:
    - Display all client orders for the company
    - Provide overview of pending orders requiring review
    """

    # Retrieve administrator identity and company scope.
    user_data = db.execute(
        "SELECT first_name, company_id FROM users WHERE id = ?",
        session["user_id"]
    )
    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    username = user_data[0]["first_name"]
    company_id = user_data[0]["company_id"]

    # Count orders currently awaiting admin action.
    pending_orders = db.execute(
        "SELECT COUNT(*) AS total FROM orders WHERE status = 'pending' AND company_id = ?",
        company_id
    )
    pending_count = pending_orders[0]["total"]

    # Fetch all orders belonging to this company.
    # Join with users table to include client details.
    orders = db.execute(
        "SELECT orders.id AS id, orders.order_number AS order_number, users.client_company_name AS company_name, users.first_name AS user_first_name, orders.created_at AS created_at, orders.total_amount AS total_amount, orders.status AS status FROM orders JOIN users ON orders.user_id = users.id WHERE orders.company_id = ?",
        company_id
    )

    # Convert stored paise values into rupees for display
    for order in orders:
        order["formatted_total_amount"] = (order["total_amount"] / 100)

    return render_template("admin_orders.html", username=username, pending_count=pending_count, orders=orders)


@app.route("/admin/orders/<int:order_id>")
@admin_required
def admin_order_details(order_id):
    """
    Admin order detail controller.

    Responsibilities:
    - Display full order receipt for administrators
    - Show client information and purchased items
    """

    # Retrieve admin identity and company scope.
    user_data = db.execute(
        "SELECT first_name, company_id FROM users WHERE id = ?",
        session["user_id"]
    )

    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    username = user_data[0]["first_name"]
    company_id = user_data[0]["company_id"]

    # Fetch the order along with client and approval details.
    # Ensures the order belongs to the same company.
    target_order = db.execute(
        "SELECT orders.id AS id, orders.order_number AS order_number, orders.status AS status, orders.total_amount AS total_amount, orders.notes AS notes, orders.created_at AS created_at, u.first_name AS client_first, u.last_name AS client_last, u.email AS client_email, u.client_company_name AS client_company_name, a.first_name AS approver_first, a.last_name AS approver_last FROM orders JOIN users u ON orders.user_id = u.id LEFT JOIN users a ON orders.approved_by = a.id WHERE orders.id = ? AND orders.company_id = ?",
        order_id, company_id
    )

    # If order is not found or outside company scope, deny access
    if len(target_order) != 1:
        flash("Order not found or unauthorized access.", "danger")
        return redirect("/admin/orders")

    # Prepare order data for template rendering
    order = target_order[0]

    # Convert stored paise value to rupees
    order["total_formatted"] = (order["total_amount"] / 100)

    # Retrieve all products included in this order.
    items = db.execute(
        "SELECT order_items.quantity AS quantity, order_items.unit_price AS unit_price, products.sku AS sku, products.name AS name, products.category AS category FROM order_items JOIN products ON order_items.product_id = products.id WHERE order_items.order_id = ?",
        order_id
    )

    # Format pricing values for display
    for item in items:
        item["unit_price_formatted"] = (item["unit_price"] / 100)
        item["line_total_formatted"] = ((item["unit_price"] * item["quantity"]) / 100)

    return render_template("admin_order_details.html", username=username, order=order, items=items)


@app.route("/admin/orders/update", methods=["POST"])
@admin_required
def update_order_status():
    """
    Order status update controller.

    Responsibilities:
    - Allow administrators to review and update order status
    - Enforce valid order state transitions
    """

    # Retrieve admin identity and company scope.
    user_data = db.execute(
        "SELECT first_name, company_id FROM users WHERE id = ?",
        session["user_id"]
    )

    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    company_id = user_data[0]["company_id"]
    admin_id = session["user_id"]

    # Read submitted order update data from form.
    order_id = request.form.get("order_id")
    new_status = request.form.get("new_status")

    # Basic validation to ensure request contains required data
    if not order_id or not new_status:
        flash("Invalid request data.", "danger")
        return redirect("/admin/orders")

    # Ensure requested status is allowed by the system.
    allowed_statuses = ["pending", "approved", "rejected", "fulfilled"]

    if new_status not in allowed_statuses:
        flash("Invalid status code.", "danger")
        return redirect("/admin/orders")

    # Ensure the order belongs to this company.
    target_order = db.execute(
        "SELECT order_number, status FROM orders WHERE id = ? AND company_id = ?",
        order_id, company_id
    )

    if len(target_order) != 1:
        flash("Order not found or unauthorized access.", "danger")
        return redirect("/admin/orders")

    order_number = target_order[0]["order_number"]

    # Update order status based on admin decision.
    if new_status == "approved" or new_status == "rejected":

        # Record which admin reviewed the order
        db.execute(
            "UPDATE orders SET status = ?, approved_by = ? WHERE id = ? AND company_id = ?",
            new_status, admin_id, order_id, company_id
        )

        if new_status == "approved":
            flash(f"Order {order_number} has been approved for fulfillment.", "success")
        else:
            flash(f"Order {order_number} was rejected.", "warning")

    elif new_status == "fulfilled":
        db.execute(
            "UPDATE orders SET status = ? WHERE id = ? AND company_id = ?",
            new_status, order_id, company_id
        )

        flash(f"Order {order_number} has been marked as completely fulfilled.", "success")

    # Redirect admin back to order management view
    return redirect("/admin/orders")


@app.route("/admin/products", methods=["GET", "POST"])
@admin_required
def admin_products():
    """
    Product catalog management controller.

    Responsibilities:
    - Allow administrators to create new products
    - Display existing catalog items
    """

    # Retrieve admin identity and company scope.
    user_data = db.execute(
        "SELECT first_name, company_id FROM users WHERE id = ?",
        session["user_id"]
    )

    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    username = user_data[0]["first_name"]
    company_id = user_data[0]["company_id"]

    # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":

        # Ensure required fields are provided.
        # Ensure product name was provided.
        if not request.form.get("name"):
            flash("Please enter the product name.", "warning")
            return redirect("/admin/products")

        # Ensure sku was provided.
        elif not request.form.get("sku"):
            flash("Please enter the SKU (unique code).", "warning")
            return redirect("/admin/products")

        # Ensure base price was provided.
        elif not request.form.get("base_price"):
            flash("Please enter the base price.", "warning")
            return redirect("/admin/products")

        else:
            # Collect form data
            name = request.form.get("name")
            sku = request.form.get("sku").upper()
            category = request.form.get("category")
            description = request.form.get("description")
            is_active = 1 if request.form.get("is_active") == "1" else 0

            # Convert price to paise for safe storage.
            try:
                base_price = int(float(request.form.get("base_price")) * 100)
                tax_percentage = float(request.form.get("tax_percentage") or 0)
            except ValueError:
                flash("Invalid price or tax format. Please enter numbers only.", "danger")
                return redirect("/admin/products")

            # Ensure SKU remains unique
            existing_product = db.execute(
                "SELECT id FROM products WHERE sku = ? AND company_id = ?",
                sku, company_id
            )

            if len(existing_product) != 0:
                flash("This SKU is already registered.", "warning")
                return redirect("/admin/products")

            # Insert product into catalog
            db.execute("INSERT INTO products (name, sku, category, base_price, tax_percentage, description, is_active, company_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       name, sku, category, base_price, tax_percentage, description, is_active, company_id
                       )

            # Success message
            flash("Product created successfully.", "success")
            return redirect("/admin/products")

    # User reached route via GET (as by clicking a link or via redirect)
    else:
        # Fetch the current product details to pre-fill the HTML form
        products = db.execute(
            "SELECT id, sku, name, description, category, base_price, tax_percentage, is_active FROM products WHERE company_id = ?",
            company_id
        )

        # Convert stored paise values into rupees
        for product in products:
            product["base_price_formatted"] = (product["base_price"] / 100)

        return render_template("admin_products.html", username=username, products=products)


@app.route("/admin/products/edit/<int:product_id>", methods=["GET", "POST"])
@admin_required
def edit_product(product_id):
    """
    Product editing controller.

    Responsibilities:
    - Update existing product information
    - Ensure product belongs to the administrator's company
    """

    # Retrieve admin identity and company scope.
    user_data = db.execute(
        "SELECT first_name, company_id FROM users WHERE id = ?",
        session["user_id"]
    )

    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    username = user_data[0]["first_name"]
    company_id = user_data[0]["company_id"]

    # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":

        # Ensure required fields exist
        if not request.form.get("name") or not request.form.get("sku") or not request.form.get("base_price"):
            flash("Name, SKU, and Base Price are required fields.", "warning")
            return redirect(f"/admin/products/edit/{product_id}")

        # Capture form values
        name = request.form.get("name")
        sku = request.form.get("sku").upper()
        category = request.form.get("category")
        description = request.form.get("description")
        is_active = 1 if request.form.get("is_active") == "1" else 0

        # Convert price to paise
        try:
            base_price_cents = int(float(request.form.get("base_price")) * 100)
            tax_percentage = float(request.form.get("tax_percentage") or 0)

        except ValueError:
            flash("Invalid price or tax format. Please enter numbers only.", "danger")
            return redirect(f"/admin/products/edit/{product_id}")

        # Ensure SKU remains unique inside company catalog
        existing_sku = db.execute(
            "SELECT id FROM products WHERE company_id = ? AND sku = ? AND id != ?",
            company_id, sku, product_id
        )

        if len(existing_sku) != 0:
            flash("This SKU is already being used by another product in your catalog.", "warning")
            return redirect(f"/admin/products/edit/{product_id}")

        # Update product record
        db.execute(
            "UPDATE products SET name = ?, sku = ?, category = ?, base_price = ?, tax_percentage = ?, description = ?, is_active = ? WHERE id = ? AND company_id = ?",
            name, sku, category, base_price_cents, tax_percentage, description, is_active, product_id, company_id
        )

        flash(f"Product {sku} has been successfully updated.", "success")
        return redirect("/admin/products")

    # User reached route via GET (as by clicking a link or via redirect)
    else:
        # Fetch existing product details
        product = db.execute(
            "SELECT * FROM products WHERE id = ? AND company_id = ?",
            product_id, company_id
        )

        if len(product) != 1:
            flash("Product not found or access denied.", "danger")
            return redirect("/admin/products")

        # Convert price back to rupees for editing form
        product_data = product[0]
        product_data["base_price_formatted"] = "{:.2f}".format(product_data["base_price"] / 100)

        return render_template("admin_product_edit.html", product=product_data, username=username)


@app.route("/admin/products/toggle", methods=["POST"])
@admin_required
def toggle_products():
    """
    Product visibility controller.

    Responsibilities:
    - Enable or disable products in the client catalog
    """

    # Retrieve admin identity and company scope.
    user_data = db.execute(
        "SELECT company_id FROM users WHERE id = ?",
        session["user_id"]
    )

    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    company_id = user_data[0]["company_id"]

    product_id = request.form.get("product_id")

    if not product_id:
        flash("Invalid product request.", "warning")
        return redirect("/admin/products")

    # Verify product belongs to this company
    product = db.execute(
        "SELECT is_active FROM products WHERE id = ? AND company_id = ?",
        product_id, company_id
    )

    if not product:
        flash("Product not found or unauthorized.", "danger")
        return redirect("/admin/products")

    # Toggle product visibility
    new_status = 0 if product[0]["is_active"] == 1 else 1

    # Update product status
    db.execute(
        "UPDATE products SET is_active = ? WHERE id = ? AND company_id = ?",
        new_status, product_id, company_id
    )

    flash("Product visibility updated successfully.", "success")

    return redirect("/admin/products")


@app.route("/admin/clients", methods=["GET", "POST"])
@admin_required
def admin_clients():
    """
    Client management controller.

    Responsibilities:
    - Create new client accounts
    - Display list of registered clients
    """

    # Retrieve admin identity and company scope.
    user_data = db.execute(
        "SELECT first_name, company_id FROM users WHERE id = ?",
        session["user_id"]
    )

    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    username = user_data[0]["first_name"]
    company_id = user_data[0]["company_id"]

    # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":

        # Ensure required client information is provided.
        # Ensure company name was provided.
        if not request.form.get("company_name"):
            flash("Please enter the company name.", "warning")
            return redirect("/admin/clients")

        # Ensure represtative first name was provided.
        elif not request.form.get("first_name"):
            flash("Please enter the first name.", "warning")
            return redirect("/admin/clients")

        # Ensure email was provided.
        elif not request.form.get("email"):
            flash("Please enter the email address.", "warning")
            return redirect("/admin/clients")

        # Ensure password was provided.
        elif not request.form.get("password"):
            flash("Please enter a temporary password.", "warning")
            return redirect("/admin/clients")

        else:

            # Collect client information
            first_name = request.form.get("first_name")
            last_name = request.form.get("last_name")
            client_company_name = request.form.get("company_name")
            email = request.form.get("email")
            password = request.form.get("password")

            # Ensure email is unique
            existing_user = db.execute(
                "SELECT id FROM users WHERE email = ?",
                email
            )

            if len(existing_user) != 0:
                flash("This email is already registered.", "warning")
                return redirect("/admin/clients")

            # Securely hash password before storage
            hashed_password = generate_password_hash(password)

            # Create client account
            db.execute(
                "INSERT INTO users(company_id, first_name, last_name, email, hash, role, client_company_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                company_id, first_name, last_name, email, hashed_password, "client", client_company_name
            )

            flash("Client added successfully.", "success")

            return redirect("/admin/clients")

    # User reached route via GET (as by clicking a link or via redirect)
    else:

        # Fetch client details
        clients = db.execute(
            "SELECT users.id AS id, users.client_company_name AS company_name, users.first_name AS first_name, users.last_name AS last_name, users.email AS email, users.is_active AS is_active, users.created_at AS created_at FROM users WHERE users.role = 'client' AND users.company_id = ?",
            company_id
        )

        return render_template("admin_clients.html", username=username, clients=clients)


@app.route("/admin/clients/toggle", methods=["POST"])
@admin_required
def toggle_clients():
    """
    Client account status controller.

    Responsibilities:
    - Suspend or restore client access
    """

    # Retrieve admin identity and company scope.
    user_data = db.execute(
        "SELECT company_id FROM users WHERE id = ?",
        session["user_id"]
    )

    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    company_id = user_data[0]["company_id"]

    user_id = request.form.get("user_id")

    if not user_id:
        flash("Invalid request.", "warning")
        return redirect("/admin/clients")

    # Verify the client belongs to this company
    user = db.execute(
        "SELECT is_active FROM users WHERE id = ? AND company_id = ?",
        user_id, company_id
    )

    if len(user) != 1:
        flash("User not found or unauthorized.", "danger")
        return redirect("/admin/clients")

    # Toggle account status
    new_status = 0 if user[0]["is_active"] == 1 else 1

    # Update account status
    db.execute(
        "UPDATE users SET is_active = ? WHERE id = ? AND company_id = ?",
        new_status, user_id, company_id
    )

    flash("Account status updated successfully.", "success")

    return redirect("/admin/clients")


@app.route("/admin/clients/delete", methods=["POST"])
@admin_required
def delete_client():
    """
    Client deletion controller.

    Responsibilities:
    - Remove client accounts that have no order history
    - Protect financial records from accidental deletion
    """

    # Retrieve admin identity and company scope.
    user_data = db.execute(
        "SELECT company_id FROM users WHERE id = ?",
        session["user_id"]
    )

    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    company_id = user_data[0]["company_id"]

    # Retrieve target client id
    target_client_id = request.form.get("client_id")

    if not target_client_id:
        flash("Invalid request.", "danger")
        return redirect("/admin/clients")

    # Prevent deletion if the client has existing orders.
    existing_orders = db.execute(
        "SELECT id FROM orders WHERE company_id = ? AND user_id = ?",
        company_id, target_client_id
    )

    if len(existing_orders) > 0:
        flash("Cannot delete this client because they have existing order records. Please use the 'Suspend' button instead to preserve financial history.", "warning")
        return redirect("/admin/clients")

    # Remove client account
    db.execute(
        "DELETE FROM users WHERE id = ? AND company_id = ?",
        target_client_id, company_id
    )

    flash("Client account has been permanently deleted.", "success")

    return redirect("/admin/clients")


@app.route("/admin/bulletins", methods=["GET", "POST"])
@admin_required
def admin_bulletins():
    """
    Bulletin management controller.

    Responsibilities:
    - Create company announcements for client dashboards
    - Display existing bulletins for management
    """

    # Retrieve admin identity and company scope.
    user_data = db.execute(
        "SELECT first_name, company_id FROM users WHERE id = ?",
        session["user_id"]
    )

    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    username = user_data[0]["first_name"]
    company_id = user_data[0]["company_id"]

    # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":

        # Ensure required bulletin information is provided.
        # Ensure title was provided.
        if not request.form.get("title"):
            flash("Please enter the bulletin title.", "warning")
            return redirect("/admin/bulletins")

        # Ensure content was provided.
        elif not request.form.get("content"):
            flash("Please enter the bulletin content.", "warning")
            return redirect("/admin/bulletins")

        else:
            # Collect bulletin data
            title = request.form.get("title")
            content = request.form.get("content")

            # Convert empty input to NULL for the database.
            expires_at = request.form.get("expires_at")
            if expires_at == "":
                expires_at = None

            # Determine whether bulletin is immediately visible
            is_active = 1 if request.form.get("is_active") else 0

            # Store announcement in the database.
            db.execute(
                "INSERT INTO bulletins (company_id, created_by, title, content, is_active, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                company_id, session["user_id"], title, content, is_active, expires_at
            )

            # Provide feedback based on publication status
            if is_active:
                flash("Bulletin published successfully.", "success")
            else:
                flash("Bulletin saved as inactive draft.", "success")

            return redirect("/admin/bulletins")

    # User reached route via GET (as by clicking a link or via redirect)
    else:

        bulletins = db.execute(
            "SELECT id, title, content, created_at, expires_at, is_active FROM bulletins WHERE company_id = ?",
            company_id
        )

        return render_template("admin_bulletins.html", username=username, bulletins=bulletins)


@app.route("/admin/bulletins/toggle", methods=["POST"])
@admin_required
def toggle_bulletin():
    """
    Bulletin visibility controller.

    Responsibilities:
    - Publish or hide existing bulletins
    - Ensure the bulletin belongs to the admin's company
    """

    # Retrieve admin identity and company scope.
    user_data = db.execute(
        "SELECT company_id FROM users WHERE id = ?",
        session["user_id"]
    )

    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    company_id = user_data[0]["company_id"]

    # Get bulletin identifier from submitted form.
    bulletin_id = request.form.get("bulletin_id")

    if not bulletin_id:
        flash("Invalid request.", "danger")
        return redirect("/admin/bulletins")

    # Ensure the bulletin exists and belongs to this company.
    target_bulletin = db.execute(
        "SELECT title, is_active FROM bulletins WHERE id = ? AND company_id = ?",
        bulletin_id, company_id
    )

    if len(target_bulletin) != 1:
        flash("Bulletin not found or unauthorized access.", "danger")
        return redirect("/admin/bulletins")

    # Switch bulletin visibility between active and inactive.
    current_status = target_bulletin[0]["is_active"]
    bulletin_title = target_bulletin[0]["title"]

    new_status = 0 if current_status == 1 else 1

    # Update bulletin visibility in database
    db.execute(
        "UPDATE bulletins SET is_active = ? WHERE id = ? AND company_id = ?",
        new_status, bulletin_id, company_id
    )

    # Provide feedback to administrator
    if new_status == 1:
        flash(f"Broadcast '{bulletin_title}' is now live on client dashboards.", "success")
    else:
        flash(f"Broadcast '{bulletin_title}' has been safely recalled.", "secondary")

    return redirect("/admin/bulletins")


@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    """
    Admin profile controller.

    Responsibilities:
    - Display administrator account information
    - Show company details linked to the admin account
    """

    # Retrieve administrator profile and company details.
    user_data = db.execute(
        "SELECT first_name, last_name, email, companies.name AS company_name FROM users JOIN companies ON companies.id = users.company_id WHERE users.id = ?",
        session["user_id"]
    )

    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    username = user_data[0]["first_name"]
    first_name = user_data[0]["first_name"]
    last_name = user_data[0]["last_name"]
    email = user_data[0]["email"]
    company_name = user_data[0]["company_name"]

    return render_template("admin_settings.html", username=username, first_name=first_name, last_name=last_name, email=email, company_name=company_name)


@app.route("/admin/change_password", methods=["GET", "POST"])
@admin_required
def admin_change_password():
    """
    Admin password update controller.

    Responsibilities:
    - Allow administrator to securely update account password
    - Verify existing credentials before applying changes
    """

    user_id = session["user_id"]

    # Retrieve admin identity and company scope.
    user_data = db.execute(
        "SELECT first_name, company_id FROM users WHERE id = ?",
        user_id
    )

    # Defensive check: ensure session maps to a valid user
    if len(user_data) != 1:
        flash("Session error. Please log in again.", "danger")
        return redirect("/logout")

    username = user_data[0]["first_name"]

    # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":

        # Collect password fields from submitted form.
        old_password = request.form.get("old_password")
        new_password = request.form.get("new_password")
        confirmation = request.form.get("confirmation")

        # Ensure all fields are provided
        if not old_password or not new_password or not confirmation:
            flash("All fields are required to update security credentials.", "warning")
            return redirect("/admin/change_password")

        # Verify new password confirmation
        if new_password != confirmation:
            flash("New password do not match. Please try again.", "danger")
            return redirect("/admin/change_password")

        # Ensure current password matches stored credentials.
        user_data = db.execute(
            "SELECT hash FROM users WHERE id = ?",
            user_id
        )

        # Failsafe session check
        if len(user_data) != 1:
            flash("Session error. Please log in again.", "danger")
            return redirect("/logout")

        current_hash = user_data[0]["hash"]

        # Validate existing password
        if not check_password_hash(current_hash, old_password):
            flash("Authentication failed. Incorrect current master password.", "danger")
            return redirect("/admin/change_password")

        # Store new password as a secure hash.
        new_hash = generate_password_hash(new_password)

        # Update the database securely
        db.execute(
            "UPDATE users SET hash = ? WHERE id = ?",
            new_hash, user_id
        )

        flash("Master security credentials have been successfully updated.", "success")

        return redirect("/admin/settings")

    # User reached route via GET (as by clicking a link or via redirect)
    else:

        return render_template("admin_change_password.html", username=username)


@app.route("/logout", methods=["GET"])
def logout():
    """
    Session termination controller.

    Responsibilities:
    - Clear user session
    - Redirect user to login page
    """

    # Remove all stored session data.
    session.clear()

    # Redirect user to authentication page
    return redirect("/login")

# ==================================================
# END OF APPLICATION ROUTES
# OrderFlow – CS50 Final Project
# ==================================================
