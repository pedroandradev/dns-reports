"""Access report exports retain observation and estimated-end semantics."""
import csv
import io
from datetime import datetime, timezone
from html import escape

from flask import Response, send_file
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

from ..reports import excel_safe
from .access import duration_label
from .views import human_time

HEADERS = ["Computador", "IP", "MAC", "Unidade/setor", "Serviço/site", "Evidência",
           "Destinos reportados", "Início reportado pelo ntopng", "Última atividade",
           "Última presença na coleta", "Duração observada (segundos)", "Bytes dos fluxos retidos",
           "Fluxos agrupados", "Estado", "Primeira ausência (fim estimado)", "Verificado em"]


def export_accesses(rows, fmt):
    values = [[row["hostname"], row["ip"], row["mac"], row["location"], row["service"],
               row["evidence"], ", ".join(row["destinations"]), human_time(row["started_at"]),
               human_time(row["last_activity"]), human_time(row["last_observed"]),
               row["duration"], row["bytes"], row["flow_count"], row["state_label"],
               human_time(row["missing_since"]), human_time(row["checked_at"])] for row in rows]
    filename = "dns-reports-acessos-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M") + "." + fmt
    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
        writer.writerow(HEADERS)
        for row in values:
            writer.writerow([excel_safe(item) for item in row])
        return Response("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    output = io.BytesIO()
    if fmt == "xlsx":
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("Acessos observados")
        sheet.append(HEADERS)
        for row in values:
            sheet.append([excel_safe(item) if isinstance(item, str) else item for item in row])
        workbook.save(output)
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        styles = getSampleStyleSheet()
        story = [Paragraph("DNS-REPORTS | Quem está acessando o quê", styles["Title"]),
                 Paragraph("Presença na coleta não confirma uso humano. Duração é a união dos intervalos "
                           "reportados dos fluxos, sem multiplicar conexões paralelas. "
                           "Encerramento é estimado por ausência; não há horário exato de fechamento. "
                           "Bytes acumulados dos fluxos retidos, inclusive trechos anteriores ao filtro.", styles["Normal"])]
        data = [["Computador / IP", "Serviço/site", "Bytes", "Duração", "Estado", "Presença / ausência"]]
        for row in rows:
            ending = "Última presença: " + human_time(row["last_observed"])
            if row["missing_since"]:
                ending += "; ausente desde " + human_time(row["missing_since"])
            data.append([row["hostname"] + " / " + row["ip"], row["service"], row["bytes"],
                         duration_label(row["duration"]), row["state_label"], ending])
        table = Table([[Paragraph(escape(str(cell)), styles["BodyText"]) for cell in row] for row in data],
                      repeatRows=1, colWidths=[155, 125, 75, 80, 120, 180])
        table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#e7edf5")),
                                   ("VALIGN", (0,0), (-1,-1), "TOP"),
                                   ("BOTTOMPADDING", (0,0), (-1,-1), 6)]))
        story.append(table)
        SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=26, rightMargin=26).build(story)
        mime = "application/pdf"
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=filename, mimetype=mime)
