from __future__ import annotations

from copy import deepcopy
from html import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


# CareerGPS adapts four layout patterns from Reactive Resume's open-source
# template system while keeping data/auth/storage inside Django. See
# THIRD_PARTY_NOTICES.md for attribution and license details.
TEMPLATES = {
    "azurill": {
        "label": "Professional",
        "description": "Balanced two-column CV with a strong header.",
        "reactive_resume_template": "azurill",
    },
    "bronzor": {
        "label": "Modern",
        "description": "Clean sidebar layout with clear section hierarchy.",
        "reactive_resume_template": "bronzor",
    },
    "chikorita": {
        "label": "Graduate",
        "description": "Education and projects first for early-career applicants.",
        "reactive_resume_template": "chikorita",
    },
    "ditto": {
        "label": "Minimal",
        "description": "Simple single-column layout designed for readability.",
        "reactive_resume_template": "ditto",
    },
}

DEFAULT_TEMPLATE_KEY = "azurill"
SECTION_KEYS = (
    "summary",
    "work",
    "projects",
    "education",
    "skills",
    "certificates",
    "languages",
    "awards",
    "volunteer",
)


def normalize_template_key(value: str | None) -> str:
    key = str(value or "").strip().casefold()
    return key if key in TEMPLATES else DEFAULT_TEMPLATE_KEY


def normalize_layout_settings(value: dict | None) -> dict:
    raw = value if isinstance(value, dict) else {}
    hidden = raw.get("hidden_sections", [])
    hidden = [str(item) for item in hidden if str(item) in SECTION_KEYS]
    density = str(raw.get("density", "comfortable")).strip().casefold()
    if density not in {"comfortable", "compact"}:
        density = "comfortable"
    return {
        "hidden_sections": sorted(set(hidden)),
        "density": density,
    }


def public_template_options():
    return [
        {"key": key, **meta}
        for key, meta in TEMPLATES.items()
    ]


def _text(value):
    return escape(str(value or "").strip())


def _date_range(item):
    start = str(item.get("startDate") or item.get("date") or "").strip()
    end = str(item.get("endDate") or "").strip()
    if start and end:
        return f"{start} – {end}"
    return start or end


def _styles(template_key, density):
    base = getSampleStyleSheet()
    compact = density == "compact"
    body_size = 8.8 if compact else 9.4
    leading = 11 if compact else 12.2

    palettes = {
        "azurill": (colors.HexColor("#152238"), colors.HexColor("#2f6f73")),
        "bronzor": (colors.HexColor("#20242a"), colors.HexColor("#7d6645")),
        "chikorita": (colors.HexColor("#233b2e"), colors.HexColor("#4f7b62")),
        "ditto": (colors.HexColor("#111111"), colors.HexColor("#555555")),
    }
    ink, accent = palettes[template_key]

    return {
        "ink": ink,
        "accent": accent,
        "name": ParagraphStyle(
            "RRName", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=20 if not compact else 18, leading=22, textColor=ink,
            alignment=TA_LEFT, spaceAfter=2,
        ),
        "headline": ParagraphStyle(
            "RRHeadline", parent=base["Normal"], fontSize=9.5,
            leading=11, textColor=accent, spaceAfter=2,
        ),
        "contact": ParagraphStyle(
            "RRContact", parent=base["Normal"], fontSize=8.3,
            leading=10, textColor=colors.HexColor("#555555"),
        ),
        "heading": ParagraphStyle(
            "RRHeading", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=10.2, leading=12, textColor=ink,
            spaceBefore=7 if compact else 9, spaceAfter=3,
        ),
        "sidebar_heading": ParagraphStyle(
            "RRSidebarHeading", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=9.6, leading=11, textColor=colors.white,
            spaceBefore=6, spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "RRBody", parent=base["BodyText"], fontSize=body_size,
            leading=leading, textColor=colors.HexColor("#252525"), spaceAfter=2,
        ),
        "sidebar_body": ParagraphStyle(
            "RRSidebarBody", parent=base["BodyText"], fontSize=8.4,
            leading=10.5, textColor=colors.white, spaceAfter=2,
        ),
        "entry": ParagraphStyle(
            "RREntry", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=9.4, leading=11, textColor=ink, spaceAfter=1,
        ),
        "meta": ParagraphStyle(
            "RRMeta", parent=base["Normal"], fontSize=8.2,
            leading=9.5, textColor=colors.HexColor("#666666"), alignment=TA_RIGHT,
        ),
        "bullet": ParagraphStyle(
            "RRBullet", parent=base["BodyText"], fontSize=body_size,
            leading=leading, leftIndent=8, firstLineIndent=-5, spaceAfter=1,
        ),
    }


def _header_flowables(resume, styles, *, centered=False):
    basics = resume.get("basics") or {}
    name_style = deepcopy(styles["name"])
    headline_style = deepcopy(styles["headline"])
    contact_style = deepcopy(styles["contact"])
    if centered:
        name_style.alignment = TA_CENTER
        headline_style.alignment = TA_CENTER
        contact_style.alignment = TA_CENTER

    out = [Paragraph(_text(basics.get("name") or "CV"), name_style)]
    headline = basics.get("label") or basics.get("headline") or ""
    if headline:
        out.append(Paragraph(_text(headline), headline_style))

    loc = (basics.get("location") or {}).get("address", "") if isinstance(basics.get("location"), dict) else ""
    bits = [basics.get("email"), basics.get("phone"), loc]
    bits = [_text(bit) for bit in bits if str(bit or "").strip()]
    for profile in basics.get("profiles") or []:
        url = str(profile.get("url") or "").strip()
        if url:
            bits.append(_text(url))
    if bits:
        out.append(Paragraph(" &nbsp; | &nbsp; ".join(bits), contact_style))
    out.append(Spacer(1, 6))
    return out


def _section_heading(title, styles, accent_line=False):
    heading = Paragraph(title.upper(), styles["heading"])
    if not accent_line:
        return [heading]
    line = Table([[""]], colWidths=[100 * cm], rowHeights=[1])
    line.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), styles["accent"]),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [heading, line, Spacer(1, 2)]


def _entry(title, subtitle, dates, summary, highlights, styles):
    out = []
    title_bits = [bit for bit in [title, subtitle] if str(bit or "").strip()]
    if title_bits:
        row = [[
            Paragraph(_text(" | ".join(title_bits)), styles["entry"]),
            Paragraph(_text(dates), styles["meta"]),
        ]]
        table = Table(row, colWidths=[12.5 * cm, 4.2 * cm])
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ]))
        out.append(table)
    if summary:
        out.append(Paragraph(_text(summary), styles["body"]))
    for bullet in highlights or []:
        if str(bullet or "").strip():
            out.append(Paragraph("• " + _text(bullet), styles["bullet"]))
    return out


def _main_sections(resume, cv_data, styles, hidden, *, education_first=False, timeline=False):
    out = []

    def heading(title):
        out.extend(_section_heading(title, styles, accent_line=timeline))

    summary = str(cv_data.get("summary") or "").strip()
    if summary and "summary" not in hidden:
        heading("Profile")
        out.append(Paragraph(_text(summary), styles["body"]))

    def add_education():
        if "education" in hidden or not resume.get("education"):
            return
        heading("Education")
        for item in resume.get("education") or []:
            qualification = " ".join(
                part for part in [str(item.get("studyType") or "").strip(), str(item.get("area") or "").strip()] if part
            )
            out.extend(_entry(qualification, item.get("institution", ""), _date_range(item), "", [], styles))

    def add_projects():
        projects = cv_data.get("key_projects") or []
        if not projects:
            projects = [
                {
                    "title": item.get("name", ""),
                    "dates": _date_range(item),
                    "description": item.get("description", ""),
                    "bullets": item.get("highlights", []),
                }
                for item in (resume.get("projects") or [])
            ]
        if "projects" in hidden or not projects:
            return
        heading("Projects")
        for item in projects:
            out.extend(_entry(
                item.get("title", ""), item.get("organisation", ""), item.get("dates", ""),
                item.get("description", ""), item.get("bullets", []), styles
            ))

    def add_work():
        work_items = cv_data.get("work_experience") or []
        if not work_items:
            work_items = [
                {
                    "title": item.get("position", ""),
                    "company": item.get("name", ""),
                    "dates": _date_range(item),
                    "bullets": ([item.get("summary")] if item.get("summary") else []) + list(item.get("highlights", [])),
                }
                for item in (resume.get("work") or [])
            ]
        if "work" in hidden or not work_items:
            return
        heading("Experience")
        for item in work_items:
            out.extend(_entry(
                item.get("title", ""), item.get("company", "") or item.get("organisation", ""),
                item.get("dates", ""), "", item.get("bullets", []), styles
            ))

    if education_first:
        add_education(); add_projects(); add_work()
    else:
        add_work(); add_projects(); add_education()

    if "certificates" not in hidden and resume.get("certificates"):
        heading("Certifications")
        for item in resume.get("certificates") or []:
            display = item.get("display", "")
            out.extend(_entry(
                display or item.get("name", ""),
                "" if display else item.get("issuer", ""),
                _date_range(item), "", [], styles,
            ))

    if "awards" not in hidden and resume.get("awards"):
        heading("Awards")
        for item in resume.get("awards") or []:
            out.extend(_entry(item.get("title", ""), item.get("awarder", ""), _date_range(item), item.get("summary", ""), [], styles))

    if "volunteer" not in hidden and resume.get("volunteer"):
        heading("Volunteering")
        for item in resume.get("volunteer") or []:
            out.extend(_entry(item.get("position", ""), item.get("organization", ""), _date_range(item), item.get("summary", ""), [], styles))

    return out


def _sidebar_sections(resume, styles, hidden, cv_data=None):
    out = []
    cv_data = cv_data or {}
    skill_names = []
    for group in cv_data.get("technical_skills") or []:
        skill_names.extend(str(item).strip() for item in (group.get("items") or []) if str(item).strip())
    if not skill_names:
        skill_names = [str(item.get("name") or "").strip() for item in (resume.get("skills") or [])]
        skill_names = [name for name in skill_names if name]
    if "skills" not in hidden and skill_names:
        out.append(Paragraph("SKILLS", styles["sidebar_heading"]))
        for name in skill_names:
            out.append(Paragraph("• " + _text(name), styles["sidebar_body"]))
    if "languages" not in hidden and resume.get("languages"):
        out.append(Paragraph("LANGUAGES", styles["sidebar_heading"]))
        for item in resume.get("languages") or []:
            text = item.get("language", "")
            if item.get("fluency"):
                text = f"{text} — {item.get('fluency')}"
            out.append(Paragraph(_text(text), styles["sidebar_body"]))
    return out


def _two_column_table(
    left_flowables,
    right_flowables,
    *,
    col_widths,
    sidebar_background,
):
    """
    Build a two-column CV layout that can paginate safely.

    Important:
    Do NOT put the entire left and right columns into one
    Table row.

    ReportLab can split a Table between rows, but it cannot
    split one giant row whose cells contain the entire CV.

    Each flowable therefore gets its own table row. This
    preserves the two-column appearance while allowing long
    CVs to continue onto additional pages.
    """

    left = list(
        left_flowables or []
    )

    right = list(
        right_flowables or []
    )

    row_count = max(
        len(left),
        len(right),
        1,
    )

    rows = []

    for index in range(
        row_count
    ):
        left_item = (
            left[index]
            if index < len(left)
            else ""
        )

        right_item = (
            right[index]
            if index < len(right)
            else ""
        )

        rows.append([
            left_item,
            right_item,
        ])

    table = Table(
        rows,
        colWidths=col_widths,
        hAlign="LEFT",

        # Explicitly allow ReportLab to split
        # this table between rows/pages.
        splitByRow=1,
    )

    last_row = (
        row_count - 1
    )

    table.setStyle(
        TableStyle([
            # Sidebar background continues for the
            # full height of the two-column layout.
            (
                "BACKGROUND",
                (0, 0),
                (0, last_row),
                sidebar_background,
            ),

            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "TOP",
            ),

            # -------------------------------------
            # LEFT / SIDEBAR COLUMN
            # -------------------------------------

            (
                "LEFTPADDING",
                (0, 0),
                (0, last_row),
                10,
            ),

            (
                "RIGHTPADDING",
                (0, 0),
                (0, last_row),
                10,
            ),

            (
                "TOPPADDING",
                (0, 0),
                (0, last_row),
                0,
            ),

            (
                "BOTTOMPADDING",
                (0, 0),
                (0, last_row),
                0,
            ),

            # Give only the outer top and bottom
            # of the sidebar some breathing room.
            (
                "TOPPADDING",
                (0, 0),
                (0, 0),
                10,
            ),

            (
                "BOTTOMPADDING",
                (0, last_row),
                (0, last_row),
                10,
            ),

            # -------------------------------------
            # MAIN COLUMN
            # -------------------------------------

            (
                "LEFTPADDING",
                (1, 0),
                (1, last_row),
                12,
            ),

            (
                "RIGHTPADDING",
                (1, 0),
                (1, last_row),
                0,
            ),

            (
                "TOPPADDING",
                (1, 0),
                (1, last_row),
                0,
            ),

            (
                "BOTTOMPADDING",
                (1, 0),
                (1, last_row),
                0,
            ),
        ])
    )

    return table


def _render_azurill(
    resume,
    cv_data,
    styles,
    hidden,
):
    story = _header_flowables(
        resume,
        styles,
    )

    sidebar = _sidebar_sections(
        resume,
        styles,
        hidden,
        cv_data,
    )

    main = _main_sections(
        resume,
        cv_data,
        styles,
        hidden,
    )

    if not sidebar:
        story.extend(
            main
        )
        return story

    table = _two_column_table(
        sidebar,
        main,
        col_widths=[
            4.8 * cm,
            12.2 * cm,
        ],
        sidebar_background=(
            styles["ink"]
        ),
    )

    story.append(
        table
    )

    return story


def _render_bronzor(
    resume,
    cv_data,
    styles,
    hidden,
):
    story = []

    light_styles = deepcopy(
        styles
    )

    light_styles[
        "sidebar_heading"
    ] = deepcopy(
        styles[
            "sidebar_heading"
        ]
    )

    light_styles[
        "sidebar_heading"
    ].textColor = (
        styles["ink"]
    )

    light_styles[
        "sidebar_body"
    ] = deepcopy(
        styles[
            "sidebar_body"
        ]
    )

    light_styles[
        "sidebar_body"
    ].textColor = (
        colors.HexColor(
            "#3f3a32"
        )
    )

    header = _header_flowables(
        resume,
        styles,
        centered=False,
    )

    sidebar = _sidebar_sections(
        resume,
        light_styles,
        hidden,
        cv_data,
    )

    main = _main_sections(
        resume,
        cv_data,
        styles,
        hidden,
        timeline=True,
    )

    left = (
        header
        + sidebar
    )

    table = _two_column_table(
        left,
        main,
        col_widths=[
            5.6 * cm,
            11.4 * cm,
        ],
        sidebar_background=(
            colors.HexColor(
                "#f2efe8"
            )
        ),
    )

    story.append(
        table
    )

    return story


def _render_chikorita(resume, cv_data, styles, hidden):
    story = _header_flowables(resume, styles, centered=True)
    story.extend(_main_sections(resume, cv_data, styles, hidden, education_first=True, timeline=True))
    if "skills" not in hidden and resume.get("skills"):
        story.extend(_section_heading("Skills", styles, accent_line=True))
        skills = "  •  ".join(_text(item.get("name")) for item in resume.get("skills") or [] if item.get("name"))
        if skills:
            story.append(Paragraph(skills, styles["body"]))
    if "languages" not in hidden and resume.get("languages"):
        story.extend(_section_heading("Languages", styles, accent_line=True))
        languages = "  •  ".join(_text(item.get("language")) for item in resume.get("languages") or [] if item.get("language"))
        story.append(Paragraph(languages, styles["body"]))
    return story


def _render_ditto(resume, cv_data, styles, hidden):
    story = _header_flowables(resume, styles, centered=False)
    story.extend(_main_sections(resume, cv_data, styles, hidden, timeline=False))
    if "skills" not in hidden and resume.get("skills"):
        story.extend(_section_heading("Skills", styles, accent_line=False))
        names = [str(item.get("name") or "").strip() for item in resume.get("skills") or []]
        names = [name for name in names if name]
        if names:
            story.append(Paragraph(_text(" | ".join(names)), styles["body"]))
    return story


def render_resume_pdf(json_resume, cv_data, filepath, *, template_key=None, layout_settings=None):
    template_key = normalize_template_key(template_key)
    layout = normalize_layout_settings(layout_settings)
    hidden = set(layout["hidden_sections"])
    styles = _styles(template_key, layout["density"])

    resume = deepcopy(json_resume or {})
    cv_data = deepcopy(cv_data or {})

    builders = {
        "azurill": _render_azurill,
        "bronzor": _render_bronzor,
        "chikorita": _render_chikorita,
        "ditto": _render_ditto,
    }
    story = builders[template_key](resume, cv_data, styles, hidden)

    doc = SimpleDocTemplate(
        filepath,
        pagesize=A4,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.35 * cm,
        bottomMargin=1.35 * cm,
        title=(resume.get("basics") or {}).get("name", "CV"),
        author="CareerGPS",
    )
    doc.build(story)
    return template_key, layout
