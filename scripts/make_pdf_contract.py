"""Generate synthetic PDF contract 001 (fictional hotel, made-up numbers)."""
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/inputs/001_sol_de_marina_contract.pdf")
ss = getSampleStyleSheet()
P = lambda t, s="BodyText": Paragraph(t, ss[s])

story = [
    P("HOTEL SOL DE MARINA ****", "Title"),
    P("Carrer de la Platja 14, 07400 Alcúdia, Mallorca, Spain"),
    P("Reservations &amp; contracting: contratos@soldemarina.example"),
    Spacer(1, 12),
    P("ACCOMMODATION AGREEMENT – SUMMER 2027", "Heading2"),
    P("Between Hotel Sol de Marina (\"the Hotel\") and SunnyBreaks Holidays Ltd (\"the Operator\"). "
      "Validity: 01/05/2027 to 31/10/2027. All prices are NET in EURO, per room per night, "
      "including breakfast unless stated otherwise, and including VAT."),
    Spacer(1, 8),
    P("1. Room types", "Heading3"),
    P("• Double Standard – 1 to 2 adults, max 1 child (3 persons total)<br/>"
      "• Double Sea View – 1 to 2 adults, max 1 child (3 persons total)<br/>"
      "• Family Room (two connecting areas) – min 2 / max 4 adults, max 2 children, maximum 4 persons"),
    P("2. Seasons", "Heading3"),
    P("Low: 01/05 – 14/06 &nbsp;&nbsp; Mid: 15/06 – 14/07 &nbsp;&nbsp; High: 15/07 – 31/08 &nbsp;&nbsp; "
      "Shoulder: 01/09 – 31/10"),
    P("3. Rates (EUR, net, per room per night)", "Heading3"),
]
rates = [
    ["Room / Board", "Low", "Mid", "High", "Shoulder"],
    ["Double Standard – BB", "95", "120", "155", "105"],
    ["Double Standard – HB", "125", "150", "185", "135"],
    ["Double Sea View – BB", "110", "135", "170", "120"],
    ["Double Sea View – HB", "140", "165", "200", "150"],
    ["Family Room – BB", "150", "185", "240", "165"],
    ["Family Room – HB", "200", "235", "290", "215"],
]
t = Table(rates, hAlign="LEFT")
t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                       ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))
story += [
    t,
    P("* All Inclusive available on request at a supplement of EUR 70 per room per night over the HB rate "
      "(Family Room: EUR 110 per room per night)."),
    P("Minimum stay: 3 nights. During High season the minimum stay is 5 nights."),
    PageBreak(),
    P("4. Children (sharing with two full-paying adults, 3rd/4th bed)", "Heading3"),
    P("Infants 0 – 1.99 years: free of charge (cot on request).<br/>"
      "Children 2 – 11.99 years: EUR 25 per night.<br/>"
      "Teens 12 – 17.99 years: EUR 45 per night."),
    P("5. Allocation and release", "Heading3"),
    P("The Hotel guarantees the following rooms per night: Double Standard 10, Double Sea View 4, "
      "Family Room 6. Release period: 14 days prior to arrival; unsold rooms return to the Hotel."),
    P("6. Cancellation", "Heading3"),
    P("Cancellations received more than 30 days before arrival: no charge. 15 to 30 days: 50% of the "
      "total stay. 14 days or less, or no-show: 100%."),
    P("7. Early booking offer", "Heading3"),
    P("10% discount for bookings confirmed on or before 31/01/2027, valid for stays 01/05/2027 – 31/10/2027, "
      "all boards. Not combinable with other offers."),
    P("8. Taxes and payment", "Heading3"),
    P("Balearic Sustainable Tourism Tax (EUR 2.20 per person per night) is NOT included and is payable by "
      "guests directly at the Hotel. Payment: by bank transfer within 30 days of guest departure."),
    Spacer(1, 18),
    P("Signed for the Hotel: M. Ferrer, Revenue Manager &nbsp;&nbsp;&nbsp; Date: 18/11/2026"),
]
out.parent.mkdir(parents=True, exist_ok=True)
SimpleDocTemplate(str(out), pagesize=A4).build(story)
print(f"wrote {out}")
