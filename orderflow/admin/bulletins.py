"""Bulletins: announcements shown on client dashboards.

A bulletin is Live while it's published and its end time hasn't passed,
a Draft while it isn't published, and Expired once a published bulletin's
end time has passed. End times are typed in the company's time zone and
stored in UTC.
"""

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from .. import timeutil
from ..extensions import db
from ..models import Bulletin
from ..permissions import company_select, get_company_object_or_404, safe_next_url, staff_required
from . import bp
from .forms import BulletinForm, page_number, paginate

LIVE = "live"
DRAFT = "draft"
EXPIRED = "expired"


def bulletin_state(bulletin, now):
    if not bulletin.is_published:
        return DRAFT
    if bulletin.is_live(now):
        return LIVE
    return EXPIRED


def has_ended(bulletin, now):
    """True once the end time has passed, published or not."""
    return bulletin.expires_at is not None and timeutil.as_utc(bulletin.expires_at) <= now


def _timezone():
    return current_user.company.timezone


def _form_values(bulletin):
    return {
        "title": bulletin.title,
        "body": bulletin.body,
        "publish": bool(bulletin.is_published),
        "expires_at": timeutil.local_input_value(bulletin.expires_at, _timezone()),
    }


def _visibility(bulletin):
    """What clients see now, for the flash after saving."""
    if not bulletin.is_published:
        return "It's a draft, so clients won't see it until you publish it."
    if bulletin.expires_at:
        when = timeutil.format_datetime(bulletin.expires_at, _timezone())
        return f"Clients see it on their dashboard until {when}."
    return "Clients see it on their dashboard until you take it down."


def _render_form(form, bulletin=None):
    state = bulletin_state(bulletin, timeutil.utcnow()) if bulletin is not None else None
    return render_template("admin/bulletin_form.html", form=form, bulletin=bulletin, state=state, timezone=_timezone())


@bp.route("/bulletins")
@staff_required
def bulletins():
    stmt = company_select(Bulletin).order_by(Bulletin.created_at.desc(), Bulletin.id.desc())
    page = paginate(stmt, page_number(request.args.get("page")))
    now = timeutil.utcnow()
    return render_template(
        "admin/bulletins.html",
        page=page,
        rows=[(bulletin, bulletin_state(bulletin, now), has_ended(bulletin, now)) for bulletin in page.items],
        timezone=_timezone(),
    )


@bp.route("/bulletins/new", methods=["GET", "POST"])
@staff_required
def bulletin_new():
    form = BulletinForm(timezone=_timezone())
    if form.validate_on_submit():
        bulletin = Bulletin(
            company_id=current_user.company_id,
            author_id=current_user.id,
            title=form.title.data,
            body=form.body.data,
            is_published=bool(form.publish.data),
            expires_at=form.expires_at.parsed,
        )
        db.session.add(bulletin)
        db.session.commit()
        verb = "Published" if bulletin.is_published else "Saved"
        flash(f"{verb} “{bulletin.title}”. {_visibility(bulletin)}", "success")
        return redirect(url_for("admin.bulletins"))
    return _render_form(form)


@bp.route("/bulletins/<int:bulletin_id>/edit", methods=["GET", "POST"])
@staff_required
def bulletin_edit(bulletin_id):
    bulletin = get_company_object_or_404(Bulletin, bulletin_id)
    form = BulletinForm(
        data=_form_values(bulletin) if request.method == "GET" else None,
        timezone=_timezone(),
        current_expiry=bulletin.expires_at,
    )
    if form.validate_on_submit():
        changes = {
            "title": form.title.data,
            "body": form.body.data,
            "is_published": bool(form.publish.data),
            "expires_at": form.expires_at.parsed,
        }
        changed = False
        for name, value in changes.items():
            current = getattr(bulletin, name)
            if name == "expires_at":
                current = timeutil.as_utc(current)
            if current != value:
                setattr(bulletin, name, value)
                changed = True
        if not changed:
            flash(f"No changes to save for “{bulletin.title}”.", "info")
            return redirect(url_for("admin.bulletins"))
        db.session.commit()
        flash(f"Saved “{bulletin.title}”. {_visibility(bulletin)}", "success")
        return redirect(url_for("admin.bulletins"))
    return _render_form(form, bulletin)


@bp.route("/bulletins/<int:bulletin_id>/status", methods=["POST"])
@staff_required
def bulletin_status(bulletin_id):
    bulletin = get_company_object_or_404(Bulletin, bulletin_id)
    wanted = request.form.get("published")
    if wanted not in ("0", "1"):
        abort(400)
    publish = wanted == "1"
    back = safe_next_url(url_for("admin.bulletins"))
    now = timeutil.utcnow()

    if publish and has_ended(bulletin, now):
        flash(
            f"“{bulletin.title}” ended on {timeutil.format_datetime(bulletin.expires_at, _timezone())}. "
            "Edit it to choose a new end time (or none), then publish it.",
            "warning",
        )
    elif bool(bulletin.is_published) == publish:
        state = "published" if publish else "a draft"
        flash(f"“{bulletin.title}” is already {state}.", "info")
    else:
        bulletin.is_published = publish
        db.session.commit()
        if publish:
            flash(f"Published “{bulletin.title}”. {_visibility(bulletin)}", "success")
        else:
            flash(f"Unpublished “{bulletin.title}”. It's a draft now, and clients no longer see it.", "success")
    return redirect(back)


@bp.route("/bulletins/<int:bulletin_id>/delete", methods=["POST"])
@staff_required
def bulletin_delete(bulletin_id):
    bulletin = get_company_object_or_404(Bulletin, bulletin_id)
    title = bulletin.title
    db.session.delete(bulletin)
    db.session.commit()
    flash(f"Deleted “{title}”.", "success")
    return redirect(url_for("admin.bulletins"))
