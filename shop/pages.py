"""Renders the checkout page (shop/templates/checkout.html) from the shop's own cart (SPEC §11).

`page_style` variants are the §14.3 dark patterns: `dark_pattern` (P3) shows the main item's price
large and the real total in small grey text with the add-on pre-checked; `easy_returns_banner`
(P4) puts "Easy returns" in the title while the footer says the order is non-refundable.
"""

from html import escape
from pathlib import Path
from string import Template

TEMPLATE = Template(
    (Path(__file__).parent / "templates" / "checkout.html").read_text(encoding="utf-8")
)


def rupees(paise: int) -> str:
    return f"₹{paise // 100:,}" + (f".{paise % 100:02d}" if paise % 100 else "")


def render(page: dict) -> str:
    styles, lines = set(page["styles"]), page["lines"]
    dark = "dark_pattern" in styles
    rows = []
    for line in lines:
        price = rupees(line["unit_paise"] * line["qty"])
        if dark and line["is_addon"]:
            rows.append(
                f'<tr class="fineprint"><td><input type="checkbox" checked> {escape(line["title"])}'
                f'</td><td class="price">{price}</td></tr>'
            )
        else:
            rows.append(
                f"<tr><td>{escape(line['title'])} &times; {line['qty']}</td>"
                f'<td class="price">{price}</td></tr>'
            )
    if page.get("fees_paise"):
        rows.append(
            f'<tr><td>Fees and tax</td><td class="price">{rupees(page["fees_paise"])}</td></tr>'
        )
    recurring = "".join(
        f"<p>{escape(line['title'])}: recurring {rupees(line['recurring']['amount_paise'])}"
        f" / {escape(line['recurring']['interval'])}</p>"
        for line in lines
        if line["recurring"]
    )
    total = rupees(page["total_paise"])
    if dark:
        main = next((line for line in lines if not line["is_addon"]), lines[0])
        headline = f'<p class="headline">{rupees(main["unit_paise"] * main["qty"])}</p>'
        total_html = f'<p class="fineprint" id="total">Total {total}</p>'
    else:
        headline = ""
        total_html = f'<p class="total" id="total">Total {total}</p>'
    refundable = page["refund_policy"] == "refundable"
    if "easy_returns_banner" in styles:
        heading = "Easy returns &middot; Checkout"
    else:
        heading = "Checkout"
    policy = "30-day refunds on every item." if refundable else ""
    footer = (
        ""
        if refundable
        else '<span class="fineprint">All sales final. Non-refundable once shipped.</span>'
    )
    sellers = ", ".join(sorted({escape(line["seller_name"]) for line in lines}))
    return TEMPLATE.substitute(
        heading=heading, cart_id=escape(page["cart_id"]), sellers=sellers, headline=headline,
        rows="\n".join(rows), recurring=recurring, total=total_html, policy=policy, footer=footer,
    )  # fmt: skip
