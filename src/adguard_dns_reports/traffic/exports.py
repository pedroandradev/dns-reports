"""Traffic measurement exports, separate from DNS query exports."""
import csv
import io
from html import escape
from datetime import datetime, timezone

from flask import Response, send_file
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

from ..reports import excel_safe
from .views import human_time

HEADERS = ["Início da hora UTC (exibido no fuso local)", "Fim", "IP", "MAC", "Computador", "Local", "Aplicação",
           "Escopo", "Bytes enviados", "Bytes recebidos", "Bytes totais", "Qualidade"]


def export_usage(rows, format_name, scope):
    values = [[human_time(row["sample_start"]), human_time(row["sample_end"]), row["ip"],
               row["mac"], row["hostname"], row["location"], row["application"],
               row["scope"], row["sent"], row["received"], row["total"], row["quality"]]
              for row in rows]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    filename = f"dns-reports-trafego-{scope}-{stamp}.{format_name}"
    if format_name == "csv":
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
        writer.writerow(HEADERS)
        for row in values:
            writer.writerow([excel_safe(value) for value in row])
        return Response("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    output = io.BytesIO()
    if format_name == "xlsx":
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("Tráfego observado")
        sheet.append(HEADERS)
        for row in values:
            sheet.append([excel_safe(value) if isinstance(value, str) else value for value in row])
        workbook.save(output)
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        styles = getSampleStyleSheet()
        story = [Paragraph("DNS-REPORTS | Tráfego observado", styles["Title"]),
                 Paragraph("Internet: diferenças de fluxos ativos observados, com cobertura parcial. "
                           "LAN: inclui tráfego interno. Agregação por hora UTC, atribuída à hora da leitura. "
                           "Valores em bytes; não representa faturamento do provedor.", styles["Normal"])]
        data = [["Fim", "Computador / IP", "Local", "Aplicação", "Bytes totais"]]
        for row in rows:
            data.append([human_time(row["sample_end"]), row["hostname"] + " / " + row["ip"],
                         row["location"], row["application"] or "Total LAN", str(row["total"])])
        cells = [[Paragraph(escape(str(value)), styles["BodyText"]) for value in row] for row in data]
        table = Table(cells, repeatRows=1, colWidths=[110, 235, 120, 150, 100])
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e7edf5")),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        story.append(table)
        SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=26, rightMargin=26).build(story)
        mime = "application/pdf"
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=filename, mimetype=mime)
