"""The client's account: their contact details and the supplier's."""

from flask import flash, redirect, render_template, url_for
from flask_login import current_user

from ..extensions import db
from ..permissions import client_required
from . import bp
from .forms import AccountForm

EDITABLE_FIELDS = ("first_name", "last_name", "phone")


@bp.route("/account", methods=["GET", "POST"])
@client_required
def account():
    user = current_user._get_current_object()
    form = AccountForm(obj=user)
    if form.validate_on_submit():
        submitted = {name: getattr(form, name).data or None for name in EDITABLE_FIELDS}
        changed = {name: value for name, value in submitted.items() if getattr(user, name) != value}
        if changed:
            for name, value in changed.items():
                setattr(user, name, value)
            db.session.commit()
            flash("Your details are saved.", "success")
        else:
            flash("Nothing to save: your details are the same as before.", "info")
        return redirect(url_for("portal.account"))
    return render_template("portal/account.html", form=form, user=user, supplier=user.company)
