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


class SignupForm(FlaskForm):
    first_name = StringField("First name", filters=[_strip], validators=[DataRequired(), Length(max=80)])
    last_name = StringField("Last name", filters=[_strip], validators=[Optional(), Length(max=80)])
    company_name = StringField("Company name", filters=[_strip], validators=[DataRequired(), Length(max=120)])
    email = EmailField("Work email", filters=[_strip], validators=[DataRequired(), Email(), Length(max=254)])
    password = PasswordField("Password", validators=[DataRequired(), StrongPassword()])
    accept_terms = BooleanField("I agree to the Terms of Service and Privacy Policy", validators=[DataRequired(message="Please accept the terms to continue.")])


class LoginForm(FlaskForm):
    email = EmailField("Email", filters=[_strip], validators=[DataRequired(), Email(), Length(max=254)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=128)])
    remember = BooleanField("Keep me signed in on this device")


class ForgotPasswordForm(FlaskForm):
    email = EmailField("Email", filters=[_strip], validators=[DataRequired(), Email(), Length(max=254)])


class SetPasswordForm(FlaskForm):
    password = PasswordField("New password", validators=[DataRequired(), StrongPassword()])
    confirm = PasswordField("Confirm new password", validators=[DataRequired(), EqualTo("password", message="The passwords don't match.")])


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired(), Length(max=128)])
    password = PasswordField("New password", validators=[DataRequired(), StrongPassword()])
    confirm = PasswordField("Confirm new password", validators=[DataRequired(), EqualTo("password", message="The passwords don't match.")])
