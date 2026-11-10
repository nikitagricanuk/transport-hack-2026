"""Report export: Excel (numbers + native charts built on those cells) and Word."""

from __future__ import annotations

import io
from typing import Any

from .analysis import ANALYTICS_ASSUMPTIONS, LOS_SOURCE_URL

SEV_RU = {"critical": "Критично", "warning": "Предупреждение", "info": "Замечание"}
CAT_RU = {
    "connectivity": "Связность", "direction": "Направления", "junction": "Перекрёстки",
    "lanes": "Полосы", "geometry": "Геометрия и Z", "data": "Данные", "pedestrian": "Пешеходы",
}
STATUS_RU = {"added": "добавлен", "modified": "изменён", "deleted": "удалён", "changed": "изменён", "removed": "удалён"}
TOTALS_RU = {
    "veh_km": "Пробег, авт·км/ч", "veh_hours": "Время в пути, авт·ч", "delay_hours": "Задержка, авт·ч",
    "avg_vc": "Средняя загрузка v/c", "congested_km": "Перегружено (v/c ≥ 0,9), км",
    "overloaded": "Рёбер с v/c ≥ 1", "roads": "Рёбер в расчёте",
}
LOAD_COLS = [
    ("id", "ID"), ("name", "Название"), ("highway", "Класс"), ("lanes", "Полос"), ("length_m", "Длина, м"),
    ("capacity", "Пропускная способность, авт./ч"), ("volume", "Нагрузка, авт./ч"), ("vc", "v/c"), ("los", "Уровень"),
    ("t0_s", "t свободн., с"), ("t_s", "t с нагрузкой, с"),
]


def issue_matrix(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for cat, label in CAT_RU.items():
        counts = {s: sum(1 for i in issues if i["category"] == cat and i["severity"] == s) for s in SEV_RU}
        rows.append({"category": label, **counts})
    return rows


# ===================================================================== Excel
def build_xlsx(data: dict[str, Any]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="1F3A5F")

    def table(ws, top: int, left: int, headers: list[str], rows: list[list[Any]]) -> tuple[int, int]:
        for j, h in enumerate(headers):
            c = ws.cell(row=top, column=left + j, value=h)
            c.font, c.fill = head_font, head_fill
            c.alignment = Alignment(wrap_text=True, vertical="center")
        for i, r in enumerate(rows, start=1):
            for j, v in enumerate(r):
                ws.cell(row=top + i, column=left + j, value=v)
        return top, top + len(rows)

    def widths(ws, values: list[int]) -> None:
        for i, w in enumerate(values, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

    def bar(title: str, y_title: str, *, stacked: bool = False, horizontal: bool = False) -> BarChart:
        ch = BarChart()
        ch.type = "bar" if horizontal else "col"
        ch.title, ch.y_axis.title = title, y_title
        ch.height, ch.width = 8, 16
        if stacked:
            ch.grouping, ch.overlap = "stacked", 100
        return ch

    stats, val, load = data["stats"], data["validation"], data["load"]

    # ---- summary
    ws = wb.active
    ws.title = "Сводка"
    ws["A1"] = data["title"]
    ws["A1"].font = Font(bold=True, size=14)
    summary = [
        ("Файл", data["filename"]), ("Сформирован", data["generated"]),
        ("Длина дорог, км", stats["road_km"]), ("Полосо-километры", stats["lane_km"]),
        ("Зданий", stats["buildings"]["count"]), ("Площадь застройки, м²", stats["buildings"]["footprint_m2"]),
        ("Светофоров", stats["signals"]), ("Переходов", stats["crossings"]), ("Остановок", stats["stops"]),
        ("Мостов", stats["bridges"]),
        ("Компонент связности", val["summary"]["components"]),
        ("Критичных проблем", val["summary"]["counts"]["critical"]),
        ("Предупреждений", val["summary"]["counts"]["warning"]),
        ("Замечаний", val["summary"]["counts"]["info"]),
        ("Изменено объектов", len(data["changes"])),
        ("Множитель спроса", load["multiplier"]),
        ("Средняя загрузка v/c", load["summary"]["avg_vc"]),
        ("Перегружено, км", load["summary"]["congested_km"]),
    ]
    table(ws, 3, 1, ["Показатель", "Значение"], [list(r) for r in summary])
    widths(ws, [34, 28])

    # ---- roads by class
    ws = wb.create_sheet("Дороги")
    cls = stats["classes"]
    top, bottom = table(ws, 1, 1, ["Класс", "OSM", "Рёбер", "Длина, км", "Полосо-км"],
                        [[c["label"], c["highway"], c["count"], c["length_km"], c["lane_km"]] for c in cls])
    widths(ws, [20, 16, 10, 12, 12])
    if cls:
        ch = bar("Протяжённость дорог по классам", "км")
        ch.add_data(Reference(ws, min_col=4, max_col=5, min_row=top, max_row=bottom), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=top + 1, max_row=bottom))
        ws.add_chart(ch, "G2")

    # ---- issues
    ws = wb.create_sheet("Проблемы")
    m = issue_matrix(val["issues"])
    top, bottom = table(ws, 1, 1, ["Категория", *SEV_RU.values()],
                        [[r["category"], r["critical"], r["warning"], r["info"]] for r in m])
    widths(ws, [20, 14, 16, 14, 50, 40])
    ch = bar("Проблемы графа по категориям", "шт.", stacked=True)
    ch.add_data(Reference(ws, min_col=2, max_col=4, min_row=top, max_row=bottom), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=top + 1, max_row=bottom))
    ws.add_chart(ch, "G2")
    table(ws, bottom + 3, 1, ["Уровень", "Категория", "Проблема", "Описание", "Объекты"],
          [[SEV_RU[i["severity"]], CAT_RU.get(i["category"], i["category"]), i["title"], i["description"],
            ", ".join(f"{r['kind']}/{r['id']}" for r in i["refs"][:5])] for i in val["issues"]])

    assumptions = wb.create_sheet("Допущения и источники")
    assumptions["A1"] = ANALYTICS_ASSUMPTIONS
    assumptions["A1"].alignment = Alignment(wrap_text=True, vertical="top")
    assumptions.column_dimensions["A"].width = 85
    assumptions.row_dimensions[1].height = 72
    assumptions["A3"] = "ОДМ 218.2.020-2012, таблица 1"
    assumptions["A3"].hyperlink = LOS_SOURCE_URL
    assumptions["A3"].style = "Hyperlink"

    # ---- load
    ws = wb.create_sheet("Нагрузка")
    rows = sorted(load["rows"], key=lambda r: -r["vc"])
    top, bottom = table(ws, 1, 1, [h for _, h in LOAD_COLS] + ["Источник спроса"],
                        [[r[k] for k, _ in LOAD_COLS] + ["Введено пользователем" if r["custom"] else "Нет данных учёта"]
                         for r in rows])
    widths(ws, [12, 28, 14, 7, 10, 12, 12, 8, 9, 11, 13, 25])
    los_top = 1
    table(ws, los_top, 13, ["Уровень обслуживания", "Рёбер"],
          [[k, v] for k, v in load["summary"]["by_los"].items()])
    ch = bar("Рёбра по уровню обслуживания (A — свободно, F — пробка)", "рёбер")
    ch.add_data(Reference(ws, min_col=14, min_row=los_top, max_row=los_top + 6), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=13, min_row=los_top + 1, max_row=los_top + 6))
    ws.add_chart(ch, "P2")
    n = min(15, len(rows))
    if n:
        ch = bar("Самые загруженные рёбра, v/c", "v/c", horizontal=True)
        ch.add_data(Reference(ws, min_col=8, min_row=top, max_row=top + n), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=2, min_row=top + 1, max_row=top + n))
        ch.height = 10
        ws.add_chart(ch, "P20")

    # ---- comparison of two fixed states
    cmp_ = data.get("comparison")
    if cmp_:
        ws = wb.create_sheet("Сравнение")
        ws["A1"] = f"«{cmp_['a']['name']}» → «{cmp_['b']['name']}»"
        ws["A1"].font = Font(bold=True, size=13)
        top, bottom = table(ws, 3, 1, ["Показатель", "До", "После", "Изменение"],
                            [[TOTALS_RU[k], v["before"], v["after"], v["delta"]] for k, v in cmp_["totals"].items()])
        widths(ws, [32, 12, 12, 12, 14, 10, 10, 12, 12, 9, 9])
        los_top = bottom + 3
        table(ws, los_top, 1, ["Уровень", "До", "После"],
              [[k, cmp_["los_before"][k], cmp_["los_after"][k]] for k in "ABCDEF"])
        ch = bar("Уровень обслуживания: до и после", "рёбер")
        ch.add_data(Reference(ws, min_col=2, max_col=3, min_row=los_top, max_row=los_top + 6), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=los_top + 1, max_row=los_top + 6))
        ws.add_chart(ch, "G3")
        ch_top = los_top + 9
        changed = cmp_["changed"]
        table(ws, ch_top, 1, ["Ребро", "Название", "Статус", "Полос до", "Полос после", "Нагрузка до",
                              "Нагрузка после", "v/c до", "v/c после", "LOS до", "LOS после"],
              [[c["id"], c["name"], STATUS_RU[c["status"]], c["lanes_before"], c["lanes_after"],
                c["volume_before"], c["volume_after"], c["vc_before"], c["vc_after"], c["los_before"], c["los_after"]]
               for c in changed])
        n = min(15, len(changed))
        if n:
            ch = bar("Загрузка изменённых рёбер, v/c", "v/c")
            ch.add_data(Reference(ws, min_col=8, max_col=9, min_row=ch_top, max_row=ch_top + n), titles_from_data=True)
            ch.set_categories(Reference(ws, min_col=2, min_row=ch_top + 1, max_row=ch_top + n))
            ws.add_chart(ch, "G20")

    # ---- edits vs loaded file
    ws = wb.create_sheet("Изменения")
    flat = []
    for c in data["changes"]:
        if c["tags"]:
            flat.extend([c["kind"], c["id"], STATUS_RU[c["status"]], c["name"], t["key"], t["before"], t["after"]]
                        for t in c["tags"])
        else:
            flat.append([c["kind"], c["id"], STATUS_RU[c["status"]], c["name"],
                         "геометрия" if c["geometry"] else "", None, None])
    table(ws, 1, 1, ["Тип", "ID", "Статус", "Название", "Тег", "Было", "Стало"], flat)
    widths(ws, [10, 14, 12, 28, 22, 24, 24])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ====================================================================== Word
def _png(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    return buf


def _charts(data: dict[str, Any]) -> dict[str, io.BytesIO]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out: dict[str, io.BytesIO] = {}
    blue, orange, grey = "#2563EB", "#EA580C", "#94A3B8"
    cls = data["stats"]["classes"]
    if cls:
        fig, ax = plt.subplots(figsize=(7, 3.2))
        labels = [c["label"] for c in cls]
        ax.bar(labels, [c["length_km"] for c in cls], color=blue, label="Длина, км")
        ax.bar(labels, [c["lane_km"] - c["length_km"] for c in cls], bottom=[c["length_km"] for c in cls],
               color=grey, label="Доп. полосо-км")
        ax.set_ylabel("км")
        ax.legend(frameon=False)
        ax.tick_params(axis="x", rotation=30)
        ax.spines[["top", "right"]].set_visible(False)
        out["classes"] = _png(fig)
        plt.close(fig)

    m = issue_matrix(data["validation"]["issues"])
    fig, ax = plt.subplots(figsize=(7, 3.2))
    bottom = [0] * len(m)
    for sev, color in (("critical", orange), ("warning", "#F59E0B"), ("info", grey)):
        vals = [r[sev] for r in m]
        ax.bar([r["category"] for r in m], vals, bottom=bottom, color=color, label=SEV_RU[sev])
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax.set_ylabel("шт.")
    ax.legend(frameon=False)
    ax.tick_params(axis="x", rotation=20)
    ax.spines[["top", "right"]].set_visible(False)
    out["issues"] = _png(fig)
    plt.close(fig)

    los = data["load"]["summary"]["by_los"]
    fig, ax = plt.subplots(figsize=(7, 3))
    colors = ["#16A34A", "#65A30D", "#CA8A04", "#EA580C", "#DC2626", "#7F1D1D"]
    ax.bar(list(los), list(los.values()), color=colors)
    ax.set_xlabel("Уровень обслуживания (A — свободно, F — пробка)")
    ax.set_ylabel("рёбер")
    ax.spines[["top", "right"]].set_visible(False)
    out["los"] = _png(fig)
    plt.close(fig)

    cmp_ = data.get("comparison")
    if cmp_ and cmp_["changed"]:
        top = cmp_["changed"][:12]
        names = [(c["name"] or f"#{c['id']}")[:22] for c in top]
        fig, ax = plt.subplots(figsize=(7, 0.45 * len(top) + 1))
        ys = range(len(top))
        ax.barh([y + 0.2 for y in ys], [c["vc_before"] or 0 for c in top], height=0.4, color=grey, label="До")
        ax.barh([y - 0.2 for y in ys], [c["vc_after"] or 0 for c in top], height=0.4, color=blue, label="После")
        ax.set_yticks(list(ys), names)
        ax.invert_yaxis()
        ax.axvline(1.0, color=orange, linestyle="--", linewidth=1)
        ax.set_xlabel("v/c (1,0 — пропускная способность исчерпана)")
        ax.legend(frameon=False)
        ax.spines[["top", "right"]].set_visible(False)
        out["compare"] = _png(fig)
        plt.close(fig)
    return out


def build_docx(data: dict[str, Any]) -> bytes:
    from docx import Document
    from docx.shared import Cm, Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)
    charts = _charts(data)

    def tbl(headers: list[str], rows: list[list[Any]]) -> None:
        t = doc.add_table(rows=1, cols=len(headers))
        t.style = "Light Grid Accent 1"
        for i, h in enumerate(headers):
            t.rows[0].cells[i].text = str(h)
        for r in rows:
            cells = t.add_row().cells
            for i, v in enumerate(r):
                cells[i].text = "" if v is None else str(v)

    stats, val, load = data["stats"], data["validation"], data["load"]
    doc.add_heading(data["title"], 0)
    doc.add_paragraph(f"Файл: {data['filename']} · сформирован {data['generated']}")

    doc.add_heading("1. Сеть", 1)
    b = stats["buildings"]
    doc.add_paragraph(
        f"Дороги: {stats['road_km']} км ({stats['lane_km']} полосо-км). Светофоров — {stats['signals']}, "
        f"переходов — {stats['crossings']}, остановок — {stats['stops']}, мостов — {stats['bridges']}. "
        f"Зданий — {b['count']}, площадь застройки {b['footprint_m2']:,} м².".replace(",", " "))
    tbl(["Класс", "Рёбер", "Длина, км", "Полосо-км"],
        [[c["label"], c["count"], c["length_km"], c["lane_km"]] for c in stats["classes"]])
    if "classes" in charts:
        doc.add_picture(charts["classes"], width=Cm(16))

    doc.add_heading("2. Качество графа", 1)
    s = val["summary"]
    doc.add_paragraph(
        f"Компонент связности: {s['components']}. Критичных проблем: {s['counts']['critical']}, "
        f"предупреждений: {s['counts']['warning']}, замечаний: {s['counts']['info']}. "
        f"Готовность: MatSim — {s['levels']['matsim']}, SUMO — {s['levels']['sumo']}, Webots — {s['levels']['webots']}.")
    doc.add_picture(charts["issues"], width=Cm(16))
    tbl(["Уровень", "Проблема", "Описание"],
        [[SEV_RU[i["severity"]], i["title"], i["description"]] for i in val["issues"][:25]])
    if len(val["issues"]) > 25:
        doc.add_paragraph(f"…и ещё {len(val['issues']) - 25}. Полный список — в Excel-отчёте.")

    doc.add_heading("3. Изменения относительно исходного файла", 1)
    if data["changes"]:
        by = {st: sum(1 for c in data["changes"] if c["status"] == st) for st in ("added", "modified", "deleted")}
        doc.add_paragraph(f"Добавлено: {by['added']}, изменено: {by['modified']}, удалено: {by['deleted']}.")
        rows = []
        for c in data["changes"][:30]:
            what = "; ".join(f"{t['key']}: {t['before'] or '—'} → {t['after'] or '—'}" for t in c["tags"][:4])
            if c["geometry"]:
                what = (what + "; " if what else "") + "геометрия"
            rows.append([f"{c['kind']}/{c['id']}", STATUS_RU[c["status"]], c["name"], what])
        tbl(["Объект", "Статус", "Название", "Что изменилось"], rows)
    else:
        doc.add_paragraph("Правок нет.")

    doc.add_heading("4. Нагрузка", 1)
    doc.add_paragraph(ANALYTICS_ASSUMPTIONS)
    doc.add_paragraph(LOS_SOURCE_URL)
    ls = load["summary"]
    doc.add_paragraph(
        f"Множитель спроса ×{load['multiplier']}. Средняя загрузка v/c {ls['avg_vc']}, перегружено "
        f"{ls['congested_km']} км, рёбер с v/c ≥ 1: {ls['overloaded']}. Время в пути {ls['veh_hours']} авт·ч, "
        f"из них задержка {ls['delay_hours']} авт·ч. Модель: пропускная способность по полосам и классу, "
        f"время по формуле BPR.")
    doc.add_picture(charts["los"], width=Cm(15))
    top = sorted(load["rows"], key=lambda r: -r["vc"])[:15]
    tbl(["Название", "Класс", "Полос", "Нагрузка", "Пропускная", "v/c", "LOS"],
        [[r["name"] or f"#{r['id']}", r["highway"], r["lanes"], r["volume"], r["capacity"], r["vc"], r["los"]]
         for r in top])

    cmp_ = data.get("comparison")
    if cmp_:
        doc.add_heading("5. Сравнение состояний", 1)
        doc.add_paragraph(f"«{cmp_['a']['name']}» ({cmp_['a']['created']}) → «{cmp_['b']['name']}» ({cmp_['b']['created']}).")
        tbl(["Показатель", "До", "После", "Изменение"],
            [[TOTALS_RU[k], v["before"], v["after"], v["delta"]] for k, v in cmp_["totals"].items()])
        if "compare" in charts:
            doc.add_picture(charts["compare"], width=Cm(16))

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ======================================================================= PDF
def _pdf_styles():
    from pathlib import Path

    import matplotlib
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # DejaVu ships with matplotlib and has Cyrillic glyphs
    fonts = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    if "DejaVu" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVu", str(fonts / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(fonts / "DejaVuSans-Bold.ttf")))
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName="DejaVu-Bold", fontSize=18),
        "h1": ParagraphStyle("h1", parent=base["Heading2"], fontName="DejaVu-Bold", fontSize=13, spaceBefore=10),
        "p": ParagraphStyle("p", parent=base["BodyText"], fontName="DejaVu", fontSize=9.5, leading=13),
        "small": ParagraphStyle("s", parent=base["BodyText"], fontName="DejaVu", fontSize=8, leading=10),
    }


def _pdf_table(rows: list[list[Any]], st, widths: list[float] | None = None):
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    data = [[Paragraph("" if v is None else str(v), st["small"]) for v in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3A5F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F1F5F9")]),
    ]))
    return t


def _pdf_image(buf: io.BytesIO, width_cm: float):
    from reportlab.lib.units import cm
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image

    w, h = ImageReader(buf).getSize()
    buf.seek(0)
    return Image(buf, width=width_cm * cm, height=width_cm * cm * h / w)


def _pdf_build(story: list) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate

    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm,
                      topMargin=1.4 * cm, bottomMargin=1.4 * cm, title="Отчёт УДС").build(story)
    return out.getvalue()


def build_pdf(data: dict[str, Any]) -> bytes:
    from reportlab.platypus import Paragraph

    st = _pdf_styles()
    charts = _charts(data)
    stats, val, load = data["stats"], data["validation"], data["load"]
    s, ls = val["summary"], load["summary"]
    story: list = [Paragraph(data["title"], st["title"]),
                   Paragraph(f"Файл: {data['filename']} · сформирован {data['generated']}", st["p"])]

    story += [Paragraph("1. Сеть", st["h1"]),
              Paragraph(f"Дороги: {stats['road_km']} км ({stats['lane_km']} полосо-км). Светофоров — {stats['signals']}, "
                        f"переходов — {stats['crossings']}, остановок — {stats['stops']}, мостов — {stats['bridges']}. "
                        f"Зданий — {stats['buildings']['count']}.", st["p"]),
              _pdf_table([["Класс", "Рёбер", "Длина, км", "Полосо-км"]]
                         + [[c["label"], c["count"], c["length_km"], c["lane_km"]] for c in stats["classes"]], st)]
    if "classes" in charts:
        story.append(_pdf_image(charts["classes"], 16))

    story += [Paragraph("2. Качество графа", st["h1"]),
              Paragraph(f"Компонент связности: {s['components']}. Критичных: {s['counts']['critical']}, "
                        f"предупреждений: {s['counts']['warning']}, замечаний: {s['counts']['info']}.", st["p"]),
              _pdf_image(charts["issues"], 15),
              _pdf_table([["Уровень", "Проблема", "Описание"]]
                         + [[SEV_RU[i["severity"]], i["title"], i["description"]] for i in val["issues"][:30]],
                         st, widths=[70, 130, 300])]

    story += [Paragraph("3. Нагрузка", st["h1"]),
              Paragraph(ANALYTICS_ASSUMPTIONS, st["p"]),
              Paragraph(f'<a href="{LOS_SOURCE_URL}">Текст ОДМ 218.2.020-2012</a>', st["p"]),
              Paragraph(f"Множитель спроса ×{load['multiplier']}. Средняя v/c {ls['avg_vc']}, перегружено "
                        f"{ls['congested_km']} км, рёбер с v/c ≥ 1: {ls['overloaded']}, задержка {ls['delay_hours']} авт·ч.",
                        st["p"]),
              _pdf_image(charts["los"], 14)]
    top = sorted(load["rows"], key=lambda r: -r["vc"])[:15]
    story.append(_pdf_table([["Название", "Класс", "Полос", "Нагрузка", "Пропускная", "v/c", "LOS"]]
                            + [[r["name"] or f"#{r['id']}", r["highway"], r["lanes"], r["volume"], r["capacity"],
                                r["vc"], r["los"]] for r in top], st))

    cmp_ = data.get("comparison")
    if cmp_:
        story += [Paragraph("4. Сравнение состояний", st["h1"]),
                  Paragraph(f"«{cmp_['a']['name']}» → «{cmp_['b']['name']}»", st["p"]),
                  _pdf_table([["Показатель", "До", "После", "Изменение"]]
                             + [[TOTALS_RU[k], v["before"], v["after"], v["delta"]] for k, v in cmp_["totals"].items()], st)]
        if "compare" in charts:
            story.append(_pdf_image(charts["compare"], 16))
    od = data.get("od")
    if od:
        story += [Paragraph("5. Матрица корреспонденций, поездок/ч", st["h1"]),
                  _pdf_table([["Из / В", *[z["name"] for z in od["zones"]]]]
                             + [[z["name"], *row] for z, row in zip(od["zones"], od["trips"])], st)]
    return _pdf_build(story)


# =========================================================== object report
# Illustrative hourly factors relative to the scenario peak (1.0).
HOURLY_PROFILE = [0.10, 0.06, 0.04, 0.04, 0.08, 0.25, 0.60, 0.95, 1.00, 0.80, 0.70, 0.72,
                  0.75, 0.74, 0.76, 0.82, 0.92, 1.00, 0.95, 0.75, 0.55, 0.40, 0.28, 0.17]


def build_object_xlsx(obj: dict[str, Any]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.chart import LineChart, Reference
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Объект"
    ws["A1"] = obj["title"]
    ws["A1"].font = Font(bold=True, size=14)
    r = 3
    for k, v in obj["facts"]:
        ws.cell(row=r, column=1, value=k).font = Font(bold=True)
        ws.cell(row=r, column=2, value=v)
        r += 1
    ws.column_dimensions["A"].width = 32
    ws.column_dimensions["B"].width = 40
    if obj.get("hourly"):
        ws.cell(row=r, column=1, value="Почасовой профиль задан для сценария")
        top = r + 2
        ws.cell(row=top, column=1, value="Час").font = Font(bold=True)
        ws.cell(row=top, column=2, value="Нагрузка, авт./ч").font = Font(bold=True)
        for h, v in enumerate(obj["hourly"]):
            ws.cell(row=top + 1 + h, column=1, value=f"{h:02d}:00")
            ws.cell(row=top + 1 + h, column=2, value=v)
        ch = LineChart()
        ch.title, ch.y_axis.title = "Суточный профиль нагрузки (допущение)", "авт./ч"
        ch.height, ch.width = 8, 18
        ch.add_data(Reference(ws, min_col=2, min_row=top, max_row=top + 24), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=top + 1, max_row=top + 24))
        ws.add_chart(ch, "D3")
    for name, headers, rows in obj.get("tables", []):
        t = wb.create_sheet(name[:30])
        for j, h in enumerate(headers, start=1):
            t.cell(row=1, column=j, value=h).font = Font(bold=True)
        for i, row in enumerate(rows, start=2):
            for j, v in enumerate(row, start=1):
                t.cell(row=i, column=j, value=v)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_object_pdf(obj: dict[str, Any]) -> bytes:
    from reportlab.platypus import Paragraph

    st = _pdf_styles()
    story: list = [Paragraph(obj["title"], st["title"]),
                   _pdf_table([["Параметр", "Значение"]] + [list(f) for f in obj["facts"]], st)]
    if obj.get("hourly"):
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(7, 2.8))
        ax.plot(range(24), obj["hourly"], color="#2563EB", marker="o", markersize=3)
        if obj.get("capacity"):
            ax.axhline(obj["capacity"], color="#EA580C", linestyle="--", linewidth=1, label="пропускная способность")
            ax.legend(frameon=False)
        ax.set_xticks(range(0, 24, 2))
        ax.set_xlabel("час")
        ax.set_ylabel("авт./ч")
        ax.spines[["top", "right"]].set_visible(False)
        story += [Paragraph("Суточный профиль нагрузки (допущение)", st["h1"]), _pdf_image(_png(fig), 16)]
        plt.close(fig)
    for name, headers, rows in obj.get("tables", []):
        story += [Paragraph(name, st["h1"]), _pdf_table([headers] + rows, st)]
    return _pdf_build(story)
