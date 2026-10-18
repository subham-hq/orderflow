from flask_wtf import FlaskForm
from wtforms import BooleanField, EmailField, PasswordField, StringField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional, ValidationError

from ..services.accounts import MIN_PASSWORD_LENGTH, password_problem


def _strip(value):
    return value.strip() if isinstance(value, str) else value


class StrongPassword:
    """Reject short or common passwords, using the email field if the form has one."""

    def __call__(self, form, field):
        email = form.email.data if hasattr(form, "email") else None
        problem = password_problem(field.data, email)
        if problem:
            raise ValidationError(problem)


PASSWORD_HINT = f"At least {MIN_PASSWORD_LENGTH} characters. A short phrase works well."


def _email(label, what):
    return EmailField(
        label,
        filters=[_strip],
        validators=[
            DataRequired(f"Enter {what}."),
            Length(max=254, message="Keep the email address to 254 characters or fewer."),
            Email("Enter a valid email address, like name@business.in."),
        ],
    )


def _new_password():
    return PasswordField("New password", validators=[DataRequired("Choose a new password."), StrongPassword()])


def _confirm_password():
    return PasswordField(
        "Confirm new password",
        validators=[
            DataRequired("Enter the new password again."),
            EqualTo("password", message="The passwords don't match."),
        ],
    )


class SignupForm(FlaskForm):
    first_name = StringField(
        "First name",
        filters=[_strip],
        validators=[
            DataRequired("Enter your first name."),
            Length(max=80, message="Keep your first name to 80 characters or fewer."),
        ],
    )
    last_name = StringField(
        "Last name",
        filters=[_strip],
        validators=[Optional(), Length(max=80, message="Keep your last name to 80 characters or fewer.")],
    )
    company_name = StringField(
        "Company name",
        filters=[_strip],
        validators=[
            DataRequired("Enter your company name."),
            Length(max=120, message="Keep the company name to 120 characters or fewer."),
        ],
    )
    email = _email("Work email", "your work email address")
    password = PasswordField("Password", validators=[DataRequired("Choose a password."), StrongPassword()])
    accept_terms = BooleanField(
        "I agree to the Terms of Service and Privacy Policy",
        validators=[DataRequired(message="Please accept the terms to continue.")],
    )


class LoginForm(FlaskForm):
    email = _email("Email", "the email address you sign in with")
    password = PasswordField(
        "Password",
        validators=[
            DataRequired("Enter your password."),
            Length(max=128, message="Passwords here are 128 characters or fewer. Check it and try again."),
        ],
    )
    remember = BooleanField("Keep me signed in on this device")


class ForgotPasswordForm(FlaskForm):
    email = _email("Email", "the email address you sign in with")


class SetPasswordForm(FlaskForm):
    password = _new_password()
    confirm = _confirm_password()


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField(
        "Current password",
        validators=[
            DataRequired("Enter your current password."),
            Length(max=128, message="That isn't your current password."),
        ],
    )
    password = _new_password()
    confirm = _confirm_password()
