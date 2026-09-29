"""GET /showcase — read-only витрина рекламодателя: объявления, бюджеты, размещения, клики."""

from __future__ import annotations

from decimal import Decimal
from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ctxads.bot.texts import PRICING_LABELS, rub
from ctxads.context import AppContext
from ctxads.db.repo import ads as ads_repo
from ctxads.db.repo import placements as placements_repo
from ctxads.matching.categories import label

router = APIRouter()

PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ctxads — витрина</title><style>
:root{{--bg:#fff;--fg:#1b1b1f;--muted:#6b6b76;--line:#e6e6ea;--accent:#2d6cdf}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141417;--fg:#ececf1;--muted:#9a9aa6;
--line:#2a2a31;--accent:#7aa5ff}}}}
body{{margin:0;font:15px/1.45 system-ui,-apple-system,sans-serif;background:var(--bg);
color:var(--fg)}} main{{max-width:1100px;margin:0 auto;padding:24px 16px}}
h1{{font-size:22px;margin:0 0 4px}} p.sub{{color:var(--muted);margin:0 0 20px}}
.wrap{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;min-width:760px}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{font-weight:600;color:var(--muted);font-size:13px}} td.n{{text-align:right;
font-variant-numeric:tabular-nums}} .tag{{font-size:12px;color:var(--muted)}}
.st-paused{{color:#c0392b}} a{{color:var(--accent)}}</style></head><body><main>
<h1>Витрина рекламодателя</h1>
<p class="sub">Объявления в каталоге и их результаты. Только чтение.</p>
<div class="wrap"><table><thead><tr><th>Объявление</th><th>Категория</th><th>Модель</th>
<th class="n">Бюджет, остаток</th><th class="n">Размещений</th><th class="n">Просмотров</th>
<th class="n">Кликов</th><th class="n">Потрачено</th></tr></thead><tbody>{rows}</tbody>
</table></div></main></body></html>"""


@router.get("/showcase", response_class=HTMLResponse)
async def showcase(request: Request) -> HTMLResponse:
    ctx: AppContext = request.app.state.ctx
    async with ctx.sessions() as s:
        items = await ads_repo.list_all(s)
        stats = await placements_repo.ad_stats(s)
    rows = []
    for ad, adv in items:
        st = stats.get(ad.id, {"placements": 0, "views": 0, "clicks": 0, "spent": Decimal(0)})
        rows.append(
            "<tr>"
            f"<td><a href='{escape(ad.url)}'>{escape(ad.title)}</a>"
            f"<div class='tag'>{escape(adv.name)} · erid {escape(ad.erid)}</div></td>"
            f"<td>{escape(label(ad.category))}</td>"
            f"<td>{PRICING_LABELS.get(ad.pricing_model, ad.pricing_model)} {rub(ad.price)}"
            f"<div class='tag st-{escape(ad.status)}'>{escape(ad.status)}</div></td>"
            f"<td class='n'>{rub(ad.budget_left)} / {rub(ad.budget_total)}</td>"
            f"<td class='n'>{st['placements']}</td><td class='n'>{st['views']}</td>"
            f"<td class='n'>{st['clicks']}</td><td class='n'>{rub(st['spent'])}</td>"  # type: ignore[arg-type]
            "</tr>"
        )
    return HTMLResponse(PAGE.format(rows="".join(rows)))
