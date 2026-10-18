"""Workspace dashboard: this month's numbers, the revenue chart and what needs doing next."""

from dataclasses import dataclass
from datetime import date

from flask import render_template, url_for
from flask_login import current_user

from ..models import PENDING
from ..money import format_inr, format_inr_compact
from ..permissions import staff_required
from ..services import analytics
from ..timeutil import to_local, utcnow
from . import bp

# Chart drawing area, in SVG user units (the SVG scales to fit its card)
CHART_WIDTH = 720
CHART_HEIGHT = 240
CHART_PADDING = {"top": 12, "right": 8, "bottom": 30, "left": 60}
# Days between date labels on the x axis, counted back from today
X_LABEL_EVERY = 7


def short_date(day):
    """date(2026, 10, 5) -> "5 Oct"."""
    return day.strftime("%d %b").lstrip("0")


def nice_ceiling(value):
    """Round a positive amount up to 1, 2, 2.5 or 5 times a power of ten, for a tidy axis."""
    if value <= 0:
        return 0
    magnitude = 10 ** (len(str(int(value))) - 1)
    for multiplier in (10, 20, 25, 50):
        candidate = magnitude * multiplier // 10
        if candidate >= value:
            return candidate
    return magnitude * 10


@dataclass(frozen=True)
class Bar:
    x: float
    y: float
    width: float
    height: float
    day: date
    revenue_paise: int
    orders: int

    @property
    def label(self):
        if not self.revenue_paise:
            return f"{short_date(self.day)}: no revenue"
        orders = f"{self.orders} order{'s' if self.orders != 1 else ''}"
        return f"{short_date(self.day)}: {format_inr(self.revenue_paise)} from {orders}"


@dataclass(frozen=True)
class Tick:
    position: float
    label: str


@dataclass(frozen=True)
class BarChart:
    width: int
    height: int
    left: float
    right: float
    top: float
    bottom: float
    bars: list
    y_ticks: list
    x_ticks: list
    days: list
    axis_max_paise: int
    total_paise: int
    total_orders: int
    summary: str

    @property
    def is_empty(self):
        return self.total_paise == 0


def build_bar_chart(days, width=CHART_WIDTH, height=CHART_HEIGHT, padding=None):
    """Lay out one bar per day. Zero days get a thin stub on the baseline."""
    pad = padding or CHART_PADDING
    left, right = pad["left"], width - pad["right"]
    top, bottom = pad["top"], height - pad["bottom"]
    plot_height = bottom - top
    slot = (right - left) / max(len(days), 1)
    bar_width = round(slot * 0.62, 2)

    peak = max((day.revenue_paise for day in days), default=0)
    axis_max = nice_ceiling(peak)

    bars = []
    for index, day in enumerate(days):
        # Any revenue gets at least 2 units so it stays visible; empty days are a thin stub
        bar_height = max(plot_height * day.revenue_paise / axis_max, 2) if day.revenue_paise else 1.5
        bars.append(
            Bar(
                x=round(left + index * slot + (slot - bar_width) / 2, 2),
                y=round(bottom - bar_height, 2),
                width=bar_width,
                height=round(bar_height, 2),
                day=day.day,
                revenue_paise=day.revenue_paise,
                orders=day.orders,
            )
        )

    y_ticks = [Tick(bottom, format_inr_compact(0))]
    if axis_max:
        y_ticks.append(Tick(round(top + plot_height / 2, 2), format_inr_compact(axis_max // 2)))
        y_ticks.append(Tick(top, format_inr_compact(axis_max)))

    label_indexes = sorted(range(len(days) - 1, -1, -X_LABEL_EVERY))
    x_ticks = [Tick(round(bars[i].x + bar_width / 2, 2), short_date(days[i].day)) for i in label_indexes]

    total = sum(day.revenue_paise for day in days)
    orders = sum(day.orders for day in days)
    period = f"the last {len(days)} day{'s' if len(days) != 1 else ''}"
    if total:
        best = max(days, key=lambda day: (day.revenue_paise, day.day))
        summary = (
            f"Revenue over {period}: {format_inr(total)} from {orders} "
            f"order{'s' if orders != 1 else ''}. Best day: {short_date(best.day)}, {format_inr(best.revenue_paise)}."
        )
    else:
        summary = f"No revenue in {period}."

    return BarChart(
        width=width,
        height=height,
        left=left,
        right=right,
        top=top,
        bottom=bottom,
        bars=bars,
        y_ticks=y_ticks,
        x_ticks=x_ticks,
        days=days,
        axis_max_paise=axis_max,
        total_paise=total,
        total_orders=orders,
        summary=summary,
    )


def greeting(local_hour):
    if local_hour < 12:
        return "Good morning"
    if local_hour < 17:
        return "Good afternoon"
    return "Good evening"


def revenue_hint(kpis):
    previous = kpis.month.previous_label
    change = kpis.revenue_change
    if change is None:
        if kpis.revenue_paise:
            return f"Nothing to compare with in {previous} yet"
        return "No approved orders placed this month yet"
    if change > 0:
        return f"Up {change:,}% on the same days in {previous}"
    if change < 0:
        return f"Down {-change:,}% on the same days in {previous}"
    return f"Level with the same days in {previous}"


@dataclass(frozen=True)
class ChecklistStep:
    title: str
    text: str
    action_label: str | None
    action_url: str | None


def checklist_steps(progress):
    """The setup steps still to do, in order. Empty once everything is done."""
    steps = []
    if not progress.has_products:
        steps.append(
            ChecklistStep(
                "Add your first product",
                "List what you sell with prices and GST so clients can order it.",
                "Add products",
                url_for("admin.products"),
            )
        )
    if not progress.has_clients:
        steps.append(
            ChecklistStep(
                "Invite your first client",
                "Clients get an email invitation to a private portal with your catalog.",
                "Invite a client",
                url_for("admin.clients"),
            )
        )
    if not progress.has_company_details:
        steps.append(
            ChecklistStep(
                "Add your company details",
                "Your address and GSTIN appear on every proforma invoice.",
                "Open settings",
                url_for("admin.settings"),
            )
        )
    if not progress.has_orders:
        steps.append(
            ChecklistStep(
                "Receive your first order",
                "Orders your clients place show up here for you to review.",
                None,
                None,
            )
        )
    return steps


@bp.route("/")
@staff_required
def dashboard():
    company = current_user.company
    now = utcnow()
    kpis = analytics.kpis(company, now)
    progress = analytics.setup_progress(company)
    local_now = to_local(now, company.timezone)
    queue = analytics.pending_queue(company) if kpis.pending_orders else []
    return render_template(
        "admin/dashboard.html",
        greeting=greeting(local_now.hour),
        today_label=f"{local_now:%A}, {local_now.day} {local_now:%B}",
        kpis=kpis,
        revenue_hint=revenue_hint(kpis),
        chart=build_bar_chart(analytics.revenue_by_day(company, now)),
        queue=queue,
        top_products=analytics.top_products(company, now),
        top_clients=analytics.top_clients(company, now),
        activity=analytics.recent_activity(company),
        progress=progress,
        steps=checklist_steps(progress),
        pending_url=url_for("admin.orders", status=PENDING),
        month_orders_url=url_for(
            "admin.orders",
            **{
                "from": to_local(kpis.month.start, company.timezone).date().isoformat(),
                "to": local_now.date().isoformat(),
            },
        ),
        portal_login_url=url_for("auth.login", _external=True),
        top_list_days=analytics.TOP_LIST_DAYS,
    )
