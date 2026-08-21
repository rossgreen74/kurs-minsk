# -*- coding: utf-8 -*-
"""Снимок наличных курсов по обменным пунктам Минска -> data.json.

Источник — таблица myfin.by/currency/minsk. Кроме курсов в ней лежат координаты точки
(data-fillial-coords), банк (data-bank-sef-alias), ссылка на отделение и время, когда
точка последний раз меняла курс.

Разбор идёт по разметке, а не по позициям колонок: ячейки берутся целыми блоками
<td class="currencies-courses__currency-cell">, валюта — из data-currency, сторона —
из класса rate_buy / rate_sell. Позиционный разбор ломается на целых значениях («3»
вместо «3.00») и на устаревших котировках, которые myfin метит классом depricated:
такие строки молча теряются, а вместе с ними — лучшие точки города.

Мы покупаем валюту, значит платим по ПРОДАЖЕ (rate_sell).
"""
import re, json, html, sys, urllib.request, datetime as dt, statistics as st

URL = "https://myfin.by/currency/minsk"
NBRB = "https://api.nbrb.by/exrates/rates/431"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
      "Accept-Language": "ru,en;q=0.9"}
CUR = ("USD", "EUR", "RUB")


def get(url, timeout=90):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)


def parse(raw):
    rows = [r for r in re.findall(r"<tr[^>]*>.*?</tr>", raw, flags=re.S) if "data-fillial-coords" in r]
    pts = []
    for r in rows:
        co = re.search(r"data-fillial-coords='\[\"([0-9.]+),([0-9.]+)\"\]'", r)
        if not co:
            continue
        rates, stale = {}, False
        for td in re.findall(r'<td class="currencies-courses__currency-cell.*?</td>', r, flags=re.S):
            v = re.search(r"<span[^>]*>([^<]*)</span>", td)
            c = re.search(r'data-currency="([A-Z]+)"', td)
            sd = re.search(r"currencies-courses__calc (rate_buy|rate_sell)", td)
            if not (v and c and sd):
                continue
            try:
                val = float(v.group(1).strip().replace(",", "."))
            except ValueError:
                continue
            # рубль РФ котируется за 100 единиц — приводим к курсу за единицу,
            # иначе калькулятор ошибётся ровно в сто раз
            m = re.search(r'data-multiplier="(\d+)"', td)
            if m and m.group(1) != "1":
                val /= float(m.group(1))
            rates[f"{c.group(1)}_{'sell' if sd.group(1) == 'rate_sell' else 'buy'}"] = round(val, 6)
            stale = stale or "depricated" in td
        if "USD_sell" not in rates or "USD_buy" not in rates:
            continue
        bank = re.search(r'data-bank-sef-alias="([^"]*)"', r)
        name = re.search(r'currencies-courses__branch-name"[^>]*>([^<]*)<', r)
        href = re.search(r'href="([^"]*)"', r)
        upd = re.search(r"ic-update-time[^>]*></i>\s*([0-9]{1,2}:[0-9]{2})", r)
        pts.append({"bank": bank.group(1) if bank else "",
                    "addr": re.sub(r"\s+", " ", html.unescape(name.group(1))).strip() if name else "",
                    "url": ("https://myfin.by" + href.group(1)) if href else "",
                    "upd": upd.group(1) if upd else "",
                    "stale": stale,
                    "lat": round(float(co.group(1)), 6), "lon": round(float(co.group(2)), 6),
                    "r": {k: v for k, v in rates.items() if k.split("_")[0] in CUR}})
    return pts, len(rows)


def validate(pts, rows, nbrb):
    """Данные публикуются только если похожи на курс. Лучше показать снимок получасовой
    давности, чем мусор: перепутанная колонка или сбой разбора дороже задержки."""
    errs = []
    if len(pts) < 400:
        errs.append(f"точек всего {len(pts)} из {rows} строк — разбор сломался")
    fresh = [p for p in pts if not p["stale"]]
    if len(fresh) < 300:
        errs.append(f"свежих котировок всего {len(fresh)}")
    if nbrb:
        far = [p for p in fresh if abs(p["r"]["USD_sell"] / nbrb - 1) > 0.05]
        if len(far) > len(fresh) * 0.05:
            errs.append(f"{len(far)} точек расходятся с курсом НБРБ больше чем на 5 %")
    out = [p for p in pts if not (53.7 < p["lat"] < 54.2 and 27.2 < p["lon"] < 28.2)]
    if len(out) > 5:
        errs.append(f"{len(out)} точек с координатами вне Минской области")
    return errs


def main():
    t0 = dt.datetime.now(dt.timezone.utc)
    resp = get(URL)
    raw = resp.read().decode("utf-8", "ignore")
    print(f"myfin: HTTP {resp.status}, {len(raw)//1024} КБ, {(dt.datetime.now(dt.timezone.utc)-t0).total_seconds():.1f} с")
    try:
        nbrb = json.load(get(NBRB, 30))["Cur_OfficialRate"]
    except Exception as e:
        nbrb = None
        print(f"НБРБ недоступен: {e}")
    pts, rows = parse(raw)
    fresh = sorted((p for p in pts if not p["stale"]), key=lambda p: p["r"]["USD_sell"])
    print(f"строк с координатами {rows}, разобрано {len(pts)}, свежих {len(fresh)}, "
          f"устаревших {len(pts)-len(fresh)}")
    errs = validate(pts, rows, nbrb)
    if fresh:
        s = [p["r"]["USD_sell"] for p in fresh]
        print(f"USD продажа: лучший {s[0]:.4f} | медиана {st.median(s):.4f} | "
              f"худший {s[-1]:.4f}" + (f" | НБРБ {nbrb:.4f}" if nbrb else ""))
        for p in fresh[:5]:
            print(f"  {p['r']['USD_sell']:.4f}  {p['bank'][:14]:<14} {p['addr'][:44]}")
    if errs:
        print("ПРОВЕРКА НЕ ПРОЙДЕНА:")
        for e in errs:
            print("  -", e)
        return 1
    json.dump({"snapped": t0.isoformat(timespec="seconds"), "nbrb": nbrb,
               "source": URL, "points": pts},
              open("data.json", "w"), ensure_ascii=False, separators=(",", ":"))
    print(f"data.json записан: {len(pts)} точек")
    return 0


if __name__ == "__main__":
    sys.exit(main())
