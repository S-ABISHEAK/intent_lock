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
    """The checkout page. `page["variant"]` (eval only, Layer V, D-059) lets a test merchant show
    something other than its quote; with no variant the page is the honest one."""
    styles, lines = set(page["styles"]), page["lines"]
    v = page.get("variant") or {}
    dark = "dark_pattern" in styles
    titles, qtys = v.get("title_shown", {}), v.get("qty_shown", {})
    rows = []
    for line in lines:
        price = rupees(line["unit_paise"] * line["qty"])
        title = escape(titles.get(line["sku"], line["title"]))
        if dark and line["is_addon"]:
            rows.append(
                f'<tr class="fineprint"><td><input type="checkbox" checked> {title}'
                f'</td><td class="price">{price}</td></tr>'
            )
        elif v.get("preselect_addons") and line["is_addon"]:
            rows.append(
                f'<tr><td><input type="checkbox" checked> {title}</td>'
                f'<td class="price">{price}</td></tr>'
            )
        else:
            rows.append(
                f"<tr><td>{title} &times; {qtys.get(line['sku'], line['qty'])}</td>"
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
    if v.get("hide_recurring"):
        recurring = ""
    if v.get("recurring_shown"):
        recurring += f"<p>{escape(v['recurring_shown'])}</p>"
    total = rupees(v.get("total_shown_paise", page["total_paise"]))
    if v.get("discount_paise") is not None:
        headline = f'<p class="headline">You pay {rupees(v["discount_paise"])}</p>'
        total_html = f'<p class="fineprint" id="total">Total {total}</p>'
    elif dark:
        main = next((line for line in lines if not line["is_addon"]), lines[0])
        headline = f'<p class="headline">{rupees(main["unit_paise"] * main["qty"])}</p>'
        total_html = f'<p class="fineprint" id="total">Total {total}</p>'
    else:
        headline = ""
        total_html = f'<p class="total" id="total">Total {total}</p>'
    if v.get("reader_note"):
        headline = f'<p class="note">{escape(v["reader_note"])}</p>' + headline
    refundable = page["refund_policy"] == "refundable" and not v.get("footer_nonrefundable")
    if "easy_returns_banner" in styles or v.get("easy_returns_heading"):
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
    d = page.get("delivery")
    line_shown = v.get("delivery_shown") or (d["line"] if d else "")
    if d:
        delivery = f'<p class="delivery">Deliver to: {escape(d["label"])}, {escape(line_shown)}</p>'
    else:
        delivery = ""
    return TEMPLATE.substitute(
        heading=heading, cart_id=escape(page["cart_id"]), sellers=sellers, headline=headline,
        delivery=delivery,
        rows="\n".join(rows), recurring=recurring, total=total_html, policy=policy, footer=footer,
    )  # fmt: skip
